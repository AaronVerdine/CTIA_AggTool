"""AWS adapters and Lambda handlers against moto's in-memory AWS."""

from __future__ import annotations

import json

import boto3
import pytest
from moto import mock_aws

from aggtool import handlers
from aggtool.models import CreateJobRequest, JobStatus
from aggtool.service import InvalidStateError, NotFoundError, build_service
from aggtool.settings import Settings

REGION = "us-east-1"
BUCKET = "aggtool-data-test"
TABLE = "aggtool-jobs-test"


@pytest.fixture
def aws(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("POWERTOOLS_TRACE_DISABLED", "true")
    monkeypatch.setenv("POWERTOOLS_METRICS_NAMESPACE", "AggToolTest")
    with mock_aws():
        boto3.client("s3").create_bucket(Bucket=BUCKET)
        boto3.client("dynamodb").create_table(
            TableName=TABLE,
            BillingMode="PAY_PER_REQUEST",
            AttributeDefinitions=[
                {"AttributeName": "job_id", "AttributeType": "S"},
                {"AttributeName": "owner", "AttributeType": "S"},
                {"AttributeName": "created_at", "AttributeType": "S"},
            ],
            KeySchema=[{"AttributeName": "job_id", "KeyType": "HASH"}],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "owner-index",
                    "KeySchema": [
                        {"AttributeName": "owner", "KeyType": "HASH"},
                        {"AttributeName": "created_at", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                }
            ],
        )
        queue_url = boto3.client("sqs").create_queue(QueueName="aggtool-jobs")["QueueUrl"]
        settings = Settings(mode="aws", data_bucket=BUCKET, jobs_table=TABLE, queue_url=queue_url)
        monkeypatch.setenv("AGGTOOL_MODE", "aws")
        monkeypatch.setenv("AGGTOOL_DATA_BUCKET", BUCKET)
        monkeypatch.setenv("AGGTOOL_JOBS_TABLE", TABLE)
        monkeypatch.setenv("AGGTOOL_QUEUE_URL", queue_url)
        handlers._service.cache_clear()
        handlers._asgi.cache_clear()
        from aggtool.settings import get_settings

        get_settings.cache_clear()
        yield settings
        get_settings.cache_clear()
        handlers._service.cache_clear()
        handlers._asgi.cache_clear()


class Ctx:
    function_name = "aggtool-test"
    memory_limit_in_mb = 512
    invoked_function_arn = "arn:aws:lambda:us-east-1:123456789012:function:aggtool-test"
    aws_request_id = "req-1"


def _sqs_records(queue_url: str, receive_count: int = 1) -> dict:
    msgs = boto3.client("sqs").receive_message(QueueUrl=queue_url, MaxNumberOfMessages=10)
    return {
        "Records": [
            {
                "messageId": m["MessageId"],
                "body": m["Body"],
                "attributes": {"ApproximateReceiveCount": str(receive_count)},
            }
            for m in msgs.get("Messages", [])
        ]
    }


def test_service_flow_on_aws(aws, primary_bytes, lookup_bytes):
    service = build_service(aws)
    created = service.create_job(
        "user-a", CreateJobRequest(primary_filename="t.csv", lookup_filename="c.csv")
    )
    job_id = created.job.job_id
    post = created.uploads["primary"]
    assert post.fields["key"] == f"uploads/{job_id}/primary.csv"
    assert "policy" in post.fields

    s3 = boto3.client("s3")
    s3.put_object(Bucket=BUCKET, Key=f"uploads/{job_id}/primary.csv", Body=primary_bytes)
    with pytest.raises(InvalidStateError):
        service.submit_job("user-a", job_id)
    s3.put_object(Bucket=BUCKET, Key=f"uploads/{job_id}/lookup.csv", Body=lookup_bytes)
    with pytest.raises(NotFoundError):
        service.submit_job("user-b", job_id)  # other users cannot see the job
    service.submit_job("user-a", job_id)
    with pytest.raises(InvalidStateError):
        service.submit_job("user-a", job_id)

    result = handlers.worker_handler(_sqs_records(aws.queue_url), Ctx())
    assert result == {"batchItemFailures": []}
    job = service.get_job("user-a", job_id)
    assert job.status == JobStatus.SUCCEEDED
    assert job.summary and job.summary["output_rows"] == 6
    csv = s3.get_object(Bucket=BUCKET, Key=f"results/{job_id}/combined.csv")["Body"].read()
    assert b"Northwind Traders" in csv

    url = service.download("user-a", job_id, "xlsx").url
    assert BUCKET in url and "X-Amz-Signature" in url
    assert [j.job_id for j in service.list_jobs("user-a")] == [job_id]
    assert service.list_jobs("user-b") == []

    # Redelivery of a finished job is a no-op.
    service.process_job(job_id)
    service.process_job("does-not-exist")


def test_worker_reports_failures_and_gives_up_after_max_receives(aws, monkeypatch):
    service = handlers._service()
    created = service.create_job(
        "u", CreateJobRequest(primary_filename="t.csv", lookup_filename="c.csv")
    )
    job_id = created.job.job_id
    s3 = boto3.client("s3")
    for role in ("primary", "lookup"):
        s3.put_object(Bucket=BUCKET, Key=f"uploads/{job_id}/{role}.csv", Body=b"x")
    service.submit_job("u", job_id)
    event = _sqs_records(aws.queue_url)

    def boom(*_a, **_k):
        raise RuntimeError("transient")

    monkeypatch.setattr("aggtool.service.aggregate_files", boom)
    first = handlers.worker_handler(event, Ctx())
    assert len(first["batchItemFailures"]) == 1
    assert service.get_job("u", job_id).status == JobStatus.RUNNING

    for rec in event["Records"]:
        rec["attributes"]["ApproximateReceiveCount"] = str(handlers.MAX_RECEIVES)
    handlers.worker_handler(event, Ctx())
    job = service.get_job("u", job_id)
    assert job.status == JobStatus.FAILED and job.error


def _http_event(method: str, path: str, sub: str | None, body: dict | None = None) -> dict:
    ctx: dict = {
        "http": {"method": method, "path": path, "sourceIp": "1.2.3.4", "protocol": "HTTP/1.1"},
        "requestId": "r",
        "stage": "$default",
    }
    if sub:
        ctx["authorizer"] = {"jwt": {"claims": {"sub": sub}, "scopes": None}}
    return {
        "version": "2.0",
        "routeKey": f"{method} {path}",
        "rawPath": path,
        "rawQueryString": "",
        "headers": {"content-type": "application/json", "host": "api.example.com"},
        "requestContext": ctx,
        "body": json.dumps(body) if body else None,
        "isBase64Encoded": False,
    }


def test_api_handler_requires_jwt_identity(aws):
    resp = handlers.api_handler(_http_event("GET", "/jobs", None), Ctx())
    assert resp["statusCode"] == 401

    body = {"primary_filename": "t.xlsx", "lookup_filename": "c.csv"}
    resp = handlers.api_handler(_http_event("POST", "/jobs", "user-a", body), Ctx())
    assert resp["statusCode"] == 201, resp
    job_id = json.loads(resp["body"])["job"]["job_id"]

    mine = handlers.api_handler(_http_event("GET", f"/jobs/{job_id}", "user-a"), Ctx())
    theirs = handlers.api_handler(_http_event("GET", f"/jobs/{job_id}", "user-b"), Ctx())
    assert (mine["statusCode"], theirs["statusCode"]) == (200, 404)
    assert handlers.api_handler(_http_event("GET", "/health", None), Ctx())["statusCode"] == 200
    # Interactive docs are disabled in AWS mode.
    assert handlers.api_handler(_http_event("GET", "/docs", "user-a"), Ctx())["statusCode"] == 404


def test_settings_validation():
    with pytest.raises(ValueError, match="AGGTOOL_DATA_BUCKET"):
        Settings(mode="aws").validate()
    with pytest.raises(ValueError, match="must be"):
        Settings(mode="cloud").validate()
