"""Job lifecycle: create, upload, submit, process, download."""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from aggtool.engine import aggregate_files
from aggtool.errors import AggToolError
from aggtool.models import (
    ARTIFACTS,
    ROLES,
    CreateJobRequest,
    CreateJobResponse,
    DownloadResponse,
    InputFile,
    Job,
    JobStatus,
    JobView,
    utcnow,
)
from aggtool.profile import get_profile
from aggtool.settings import Settings
from aggtool.storage import (
    BlobStore,
    ConflictError,
    DynamoJobStore,
    JobQueue,
    JobStore,
    LocalBlobStore,
    LocalJobQueue,
    LocalJobStore,
    S3BlobStore,
    SqsJobQueue,
)

log = logging.getLogger(__name__)


class NotFoundError(Exception):
    pass


class InvalidStateError(Exception):
    pass


@dataclass
class JobService:
    settings: Settings
    jobs: JobStore
    blobs: BlobStore
    queue: JobQueue

    # ---------------------------------------------------------------- API side

    def create_job(self, owner: str, req: CreateJobRequest) -> CreateJobResponse:
        get_profile(req.profile)  # raises ProfileError for unknown names
        job_id = uuid.uuid4().hex
        inputs = {}
        for role, filename in (("primary", req.primary_filename), ("lookup", req.lookup_filename)):
            ext = Path(filename).suffix.lower()
            inputs[role] = InputFile(
                filename=Path(filename).name, key=f"uploads/{job_id}/{role}{ext}"
            )
        job = Job(
            job_id=job_id,
            owner=owner,
            profile=req.profile,
            inputs=inputs,
            expires_at=int(time.time()) + self.settings.job_retention_days * 86400,
        )
        self.jobs.put(job)
        uploads = {
            role: self.blobs.upload_target(
                f.key, self.settings.max_upload_bytes, self.settings.upload_url_ttl_seconds
            )
            for role, f in inputs.items()
        }
        log.info("job created", extra={"job_id": job_id, "profile": req.profile})
        return CreateJobResponse(job=JobView.of(job), uploads=uploads)

    def get_job(self, owner: str, job_id: str) -> Job:
        job = self.jobs.get(job_id)
        # Same error for "missing" and "someone else's": do not leak existence.
        if job is None or job.owner != owner:
            raise NotFoundError(job_id)
        return job

    def list_jobs(self, owner: str) -> list[JobView]:
        return [JobView.of(j) for j in self.jobs.list_for_owner(owner)]

    def submit_job(self, owner: str, job_id: str) -> JobView:
        job = self.get_job(owner, job_id)
        if job.status != JobStatus.PENDING_UPLOAD:
            raise InvalidStateError(f"Job is already {job.status.value}.")
        missing = [r for r in ROLES if self.blobs.size(job.inputs[r].key) is None]
        if missing:
            raise InvalidStateError(f"Upload the {' and '.join(missing)} file before submitting.")
        job.status = JobStatus.QUEUED
        job.updated_at = utcnow()
        try:
            self.jobs.put(job, expect=[JobStatus.PENDING_UPLOAD])
        except ConflictError as exc:
            raise InvalidStateError("Job was already submitted.") from exc
        self.queue.send(job_id)
        return JobView.of(job)

    def download(self, owner: str, job_id: str, artifact: str) -> DownloadResponse:
        if artifact not in ARTIFACTS:
            raise NotFoundError(artifact)
        job = self.get_job(owner, job_id)
        if job.status != JobStatus.SUCCEEDED:
            raise InvalidStateError("Results are available once the job has succeeded.")
        stem = Path(job.inputs["primary"].filename).stem
        name, content_type = ARTIFACTS[artifact]
        ttl = self.settings.download_url_ttl_seconds
        url = self.blobs.download_url(job.result_key(artifact), f"{stem}-{name}", content_type, ttl)
        return DownloadResponse(url=url, expires_in=ttl)

    # ------------------------------------------------------------- worker side

    def process_job(self, job_id: str) -> None:
        """Run one job. Safe to call more than once (SQS is at-least-once)."""
        job = self.jobs.get(job_id)
        if job is None:
            log.warning("job not found", extra={"job_id": job_id})
            return
        if job.status in (JobStatus.SUCCEEDED, JobStatus.FAILED):
            log.info("job already finished", extra={"job_id": job_id})
            return
        job.status = JobStatus.RUNNING
        job.updated_at = utcnow()
        self.jobs.put(job, expect=[JobStatus.QUEUED, JobStatus.RUNNING])

        started = time.perf_counter()
        try:
            result = aggregate_files(
                self.blobs.get(job.inputs["primary"].key),
                job.inputs["primary"].filename,
                self.blobs.get(job.inputs["lookup"].key),
                job.inputs["lookup"].filename,
                get_profile(job.profile),
            )
        except AggToolError as exc:
            # The user's input is at fault: record it and do not retry.
            job.status = JobStatus.FAILED
            job.error = str(exc)
            job.updated_at = utcnow()
            self.jobs.put(job)
            log.info("job failed validation", extra={"job_id": job_id, "error": str(exc)})
            return

        self.blobs.put(job.result_key("xlsx"), result.to_xlsx(), ARTIFACTS["xlsx"][1])
        self.blobs.put(job.result_key("csv"), result.to_csv(), ARTIFACTS["csv"][1])
        self.blobs.put(
            job.result_key("report"),
            json.dumps(result.report(), indent=2, default=str).encode(),
            ARTIFACTS["report"][1],
        )
        job.status = JobStatus.SUCCEEDED
        job.summary = result.summary
        job.updated_at = utcnow()
        self.jobs.put(job)
        log.info(
            "job succeeded",
            extra={
                "job_id": job_id,
                "duration_ms": round((time.perf_counter() - started) * 1000),
                "output_rows": result.summary["output_rows"],
            },
        )

    def mark_failed(self, job_id: str, message: str) -> None:
        """Record a terminal failure (called when retries are exhausted)."""
        job = self.jobs.get(job_id)
        if job and job.status not in (JobStatus.SUCCEEDED, JobStatus.FAILED):
            job.status = JobStatus.FAILED
            job.error = message
            job.updated_at = utcnow()
            self.jobs.put(job)


def build_service(settings: Settings, base_url: str = "") -> JobService:
    if settings.mode == "aws":
        return JobService(
            settings=settings,
            jobs=DynamoJobStore(settings.jobs_table),
            blobs=S3BlobStore(settings.data_bucket, settings.kms_key_id),
            queue=SqsJobQueue(settings.queue_url),
        )
    queue = LocalJobQueue()
    service = JobService(
        settings=settings,
        jobs=LocalJobStore(settings.local_data_dir),
        blobs=LocalBlobStore(settings.local_data_dir, base_url),
        queue=queue,
    )

    def run_local(job_id: str) -> None:
        try:
            service.process_job(job_id)
        except Exception:
            log.exception("job crashed", extra={"job_id": job_id})
            service.mark_failed(job_id, "Internal error while processing the job.")

    queue.handler = run_local
    return service
