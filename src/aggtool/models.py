"""Job records and API schemas."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator

from aggtool.tables import extension_of

ROLES = ("primary", "lookup")
ARTIFACTS = {
    "xlsx": ("combined.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    "csv": ("combined.csv", "text/csv"),
    "report": ("report.json", "application/json"),
}


def utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class JobStatus(StrEnum):
    PENDING_UPLOAD = "PENDING_UPLOAD"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class InputFile(BaseModel):
    filename: str
    key: str


class Job(BaseModel):
    job_id: str
    owner: str
    profile: str
    status: JobStatus = JobStatus.PENDING_UPLOAD
    created_at: str = Field(default_factory=utcnow)
    updated_at: str = Field(default_factory=utcnow)
    inputs: dict[str, InputFile]
    summary: dict[str, Any] | None = None
    error: str | None = None
    expires_at: int = 0  # epoch seconds; DynamoDB TTL attribute

    def result_key(self, artifact: str) -> str:
        return f"results/{self.job_id}/{ARTIFACTS[artifact][0]}"


class CreateJobRequest(BaseModel):
    profile: str = "tmo-csca"
    primary_filename: str = Field(min_length=1, max_length=255)
    lookup_filename: str = Field(min_length=1, max_length=255)

    @field_validator("primary_filename", "lookup_filename")
    @classmethod
    def _supported(cls, v: str) -> str:
        extension_of(v)
        return v


class UploadTarget(BaseModel):
    url: str
    fields: dict[str, str]


class CreateJobResponse(BaseModel):
    job: JobView
    uploads: dict[str, UploadTarget]


class JobView(BaseModel):
    """What clients see: no storage keys or owner ids."""

    job_id: str
    profile: str
    status: JobStatus
    created_at: str
    updated_at: str
    filenames: dict[str, str]
    summary: dict[str, Any] | None = None
    error: str | None = None

    @classmethod
    def of(cls, job: Job) -> JobView:
        return cls(
            job_id=job.job_id,
            profile=job.profile,
            status=job.status,
            created_at=job.created_at,
            updated_at=job.updated_at,
            filenames={role: f.filename for role, f in job.inputs.items()},
            summary=job.summary,
            error=job.error,
        )


class DownloadResponse(BaseModel):
    url: str
    expires_in: int


CreateJobResponse.model_rebuild()
