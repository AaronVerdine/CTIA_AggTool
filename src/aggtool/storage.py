"""Storage and queue adapters: AWS (S3, DynamoDB, SQS) and local (filesystem).

The service layer depends only on the protocols below, so the same code runs
in Lambda and on a laptop.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, Protocol

from aggtool.models import Job, JobStatus, UploadTarget


class ConflictError(Exception):
    """A conditional update lost: the job is not in the expected state."""


class JobStore(Protocol):
    def put(self, job: Job, expect: Iterable[JobStatus] | None = None) -> None: ...
    def get(self, job_id: str) -> Job | None: ...
    def list_for_owner(self, owner: str, limit: int = 50) -> list[Job]: ...


class BlobStore(Protocol):
    def upload_target(self, key: str, max_bytes: int, ttl: int) -> UploadTarget: ...
    def size(self, key: str) -> int | None: ...
    def get(self, key: str) -> bytes: ...
    def put(self, key: str, data: bytes, content_type: str) -> None: ...
    def download_url(self, key: str, filename: str, content_type: str, ttl: int) -> str: ...


class JobQueue(Protocol):
    def send(self, job_id: str) -> None: ...


# --------------------------------------------------------------------------- AWS


class DynamoJobStore:
    """Jobs table: PK ``job_id``; GSI ``owner-index`` on (owner, created_at).

    The full job is stored as a JSON document so nested summaries need no
    Decimal conversion; top-level attributes exist for keys, TTL and conditions.
    """

    OWNER_INDEX = "owner-index"

    def __init__(self, table_name: str, client: Any = None) -> None:
        import boto3

        self._table = table_name
        self._ddb = client or boto3.client("dynamodb")

    def put(self, job: Job, expect: Iterable[JobStatus] | None = None) -> None:
        item = {
            "job_id": {"S": job.job_id},
            "owner": {"S": job.owner},
            "created_at": {"S": job.created_at},
            "status": {"S": job.status.value},
            "expires_at": {"N": str(job.expires_at)},
            "doc": {"S": job.model_dump_json()},
        }
        kwargs: dict[str, Any] = {"TableName": self._table, "Item": item}
        if expect is not None:
            statuses = list(expect)
            names = {f":s{i}": {"S": s.value} for i, s in enumerate(statuses)}
            kwargs["ConditionExpression"] = (
                f"attribute_exists(job_id) AND #st IN ({', '.join(names)})"
            )
            kwargs["ExpressionAttributeNames"] = {"#st": "status"}
            kwargs["ExpressionAttributeValues"] = names
        try:
            self._ddb.put_item(**kwargs)
        except self._ddb.exceptions.ConditionalCheckFailedException as exc:
            raise ConflictError(job.job_id) from exc

    def get(self, job_id: str) -> Job | None:
        resp = self._ddb.get_item(
            TableName=self._table, Key={"job_id": {"S": job_id}}, ConsistentRead=True
        )
        item = resp.get("Item")
        return Job.model_validate_json(item["doc"]["S"]) if item else None

    def list_for_owner(self, owner: str, limit: int = 50) -> list[Job]:
        resp = self._ddb.query(
            TableName=self._table,
            IndexName=self.OWNER_INDEX,
            KeyConditionExpression="#o = :o",
            ExpressionAttributeNames={"#o": "owner"},
            ExpressionAttributeValues={":o": {"S": owner}},
            ScanIndexForward=False,
            Limit=limit,
        )
        return [Job.model_validate_json(i["doc"]["S"]) for i in resp.get("Items", [])]


class S3BlobStore:
    def __init__(self, bucket: str, kms_key_id: str = "", client: Any = None) -> None:
        import boto3
        from botocore.config import Config

        self._bucket = bucket
        self._kms = kms_key_id
        # SigV4 + regional endpoint so presigned URLs work for KMS-encrypted objects.
        self._s3 = client or boto3.client(
            "s3", config=Config(signature_version="s3v4", s3={"addressing_style": "virtual"})
        )

    def upload_target(self, key: str, max_bytes: int, ttl: int) -> UploadTarget:
        fields: dict[str, str] = {}
        conditions: list[Any] = [["content-length-range", 1, max_bytes]]
        if self._kms:
            fields["x-amz-server-side-encryption"] = "aws:kms"
            fields["x-amz-server-side-encryption-aws-kms-key-id"] = self._kms
            conditions += [{k: v} for k, v in fields.items()]
        post = self._s3.generate_presigned_post(
            Bucket=self._bucket, Key=key, Fields=fields, Conditions=conditions, ExpiresIn=ttl
        )
        return UploadTarget(url=post["url"], fields=post["fields"])

    def size(self, key: str) -> int | None:
        try:
            head = self._s3.head_object(Bucket=self._bucket, Key=key)
        except self._s3.exceptions.ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return None
            raise
        return int(head["ContentLength"])

    def get(self, key: str) -> bytes:
        body: bytes = self._s3.get_object(Bucket=self._bucket, Key=key)["Body"].read()
        return body

    def put(self, key: str, data: bytes, content_type: str) -> None:
        extra: dict[str, Any] = {}
        if self._kms:
            extra = {"ServerSideEncryption": "aws:kms", "SSEKMSKeyId": self._kms}
        self._s3.put_object(
            Bucket=self._bucket, Key=key, Body=data, ContentType=content_type, **extra
        )

    def download_url(self, key: str, filename: str, content_type: str, ttl: int) -> str:
        url: str = self._s3.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": self._bucket,
                "Key": key,
                "ResponseContentDisposition": f'attachment; filename="{filename}"',
                "ResponseContentType": content_type,
            },
            ExpiresIn=ttl,
        )
        return url


class SqsJobQueue:
    def __init__(self, queue_url: str, client: Any = None) -> None:
        import boto3

        self._url = queue_url
        self._sqs = client or boto3.client("sqs")

    def send(self, job_id: str) -> None:
        self._sqs.send_message(QueueUrl=self._url, MessageBody=json.dumps({"job_id": job_id}))


# ------------------------------------------------------------------------- local


class LocalJobStore:
    def __init__(self, root: Path) -> None:
        self._dir = root / "jobs"
        self._dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _path(self, job_id: str) -> Path:
        return self._dir / f"{job_id}.json"

    def put(self, job: Job, expect: Iterable[JobStatus] | None = None) -> None:
        with self._lock:
            if expect is not None:
                current = self.get(job.job_id)
                if current is None or current.status not in set(expect):
                    raise ConflictError(job.job_id)
            self._path(job.job_id).write_text(job.model_dump_json(), encoding="utf-8")

    def get(self, job_id: str) -> Job | None:
        path = self._path(job_id)
        return Job.model_validate_json(path.read_text(encoding="utf-8")) if path.exists() else None

    def list_for_owner(self, owner: str, limit: int = 50) -> list[Job]:
        jobs = [
            Job.model_validate_json(p.read_text(encoding="utf-8")) for p in self._dir.glob("*.json")
        ]
        mine = sorted(
            (j for j in jobs if j.owner == owner), key=lambda j: j.created_at, reverse=True
        )
        return mine[:limit]


class LocalBlobStore:
    """Files on disk; the local API serves the upload and download endpoints."""

    def __init__(self, root: Path, base_url: str = "") -> None:
        self._dir = (root / "blobs").resolve()
        self._dir.mkdir(parents=True, exist_ok=True)
        self._base = base_url

    def path_for(self, key: str) -> Path:
        path = (self._dir / key).resolve()
        if not path.is_relative_to(self._dir):
            raise ValueError("invalid key")
        return path

    def upload_target(self, key: str, max_bytes: int, ttl: int) -> UploadTarget:
        return UploadTarget(url=f"{self._base}/_local/upload", fields={"key": key})

    def size(self, key: str) -> int | None:
        path = self.path_for(key)
        return path.stat().st_size if path.exists() else None

    def get(self, key: str) -> bytes:
        return self.path_for(key).read_bytes()

    def put(self, key: str, data: bytes, content_type: str) -> None:
        path = self.path_for(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def download_url(self, key: str, filename: str, content_type: str, ttl: int) -> str:
        return f"{self._base}/_local/files/{key}"


class LocalJobQueue:
    """Runs each job on a background thread, standing in for SQS + worker Lambda."""

    def __init__(self, handler: Callable[[str], None] | None = None, synchronous: bool = False):
        self.handler = handler
        self._sync = synchronous

    def send(self, job_id: str) -> None:
        if self.handler is None:
            raise RuntimeError("LocalJobQueue has no handler")
        if self._sync:
            self.handler(job_id)
        else:
            threading.Thread(target=self.handler, args=(job_id,), daemon=True).start()
