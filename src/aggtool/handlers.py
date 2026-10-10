"""AWS Lambda entry points."""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from aws_lambda_powertools import Logger, Metrics, Tracer
from aws_lambda_powertools.metrics import MetricUnit

from aggtool.service import JobService, build_service
from aggtool.settings import get_settings

logger = Logger(service="aggtool")
tracer = Tracer(service="aggtool")
metrics = Metrics(namespace="AggTool", service="aggtool")

# Matches maxReceiveCount on the queue's redrive policy (infra/terraform/queue.tf).
MAX_RECEIVES = 3


@lru_cache(maxsize=1)
def _service() -> JobService:
    return build_service(get_settings())


@lru_cache(maxsize=1)
def _asgi() -> Any:
    from mangum import Mangum

    from aggtool.api.app import create_app

    return Mangum(create_app(get_settings(), _service()), lifespan="off")


@logger.inject_lambda_context(clear_state=True)
@tracer.capture_lambda_handler
def api_handler(event: dict[str, Any], context: Any) -> Any:
    return _asgi()(event, context)


@logger.inject_lambda_context(clear_state=True)
@tracer.capture_lambda_handler
@metrics.log_metrics
def worker_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """SQS batch handler with partial batch failure reporting."""
    service = _service()
    failures: list[dict[str, str]] = []
    for record in event.get("Records", []):
        job_id = json.loads(record["body"])["job_id"]
        logger.append_keys(job_id=job_id)
        try:
            service.process_job(job_id)
            job = service.jobs.get(job_id)
            if job is not None:
                metrics.add_metric(
                    name=f"Jobs{job.status.value.title()}", unit=MetricUnit.Count, value=1
                )
        except Exception:
            receives = int(record.get("attributes", {}).get("ApproximateReceiveCount", "1"))
            logger.exception("job processing error", extra={"receive_count": receives})
            if receives >= MAX_RECEIVES:
                # Last attempt: record the failure so the user is not left waiting.
                service.mark_failed(job_id, "Internal error while processing the job.")
                metrics.add_metric(name="JobsCrashed", unit=MetricUnit.Count, value=1)
            failures.append({"itemIdentifier": record["messageId"]})
    return {"batchItemFailures": failures}
