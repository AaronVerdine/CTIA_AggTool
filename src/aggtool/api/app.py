"""REST API (FastAPI). Runs under uvicorn locally and under Mangum in Lambda."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from aggtool import __version__
from aggtool.errors import AggToolError
from aggtool.models import ARTIFACTS, CreateJobRequest, CreateJobResponse, DownloadResponse, JobView
from aggtool.profile import builtin_profiles
from aggtool.service import InvalidStateError, JobService, NotFoundError, build_service
from aggtool.settings import Settings, get_settings
from aggtool.storage import LocalBlobStore

LOCAL_USER = "local-user"


def current_user(request: Request) -> str:
    """Identity comes only from the API Gateway JWT authorizer in AWS."""
    settings: Settings = request.app.state.settings
    if settings.mode == "local":
        return LOCAL_USER
    event: dict[str, Any] = request.scope.get("aws.event", {})
    claims = event.get("requestContext", {}).get("authorizer", {}).get("jwt", {}).get("claims", {})
    sub = claims.get("sub")
    if not sub:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return str(sub)


User = Annotated[str, Depends(current_user)]


def create_app(settings: Settings | None = None, service: JobService | None = None) -> FastAPI:
    settings = settings or get_settings()
    service = service or build_service(settings)
    app = FastAPI(
        title="AggTool API",
        version=__version__,
        description="Aggregate and reconcile carrier short code reports.",
        docs_url="/docs" if settings.mode == "local" else None,
        redoc_url=None,
    )
    app.state.settings = settings
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_methods=["GET", "POST"],
            allow_headers=["Authorization", "Content-Type"],
        )

    @app.exception_handler(NotFoundError)
    async def _not_found(_: Request, __: NotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": "Not found"})

    @app.exception_handler(InvalidStateError)
    async def _conflict(_: Request, exc: InvalidStateError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(AggToolError)
    async def _bad_input(_: Request, exc: AggToolError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.get("/health", tags=["meta"])
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.get("/profiles", tags=["meta"])
    def profiles(_: User) -> list[dict[str, Any]]:
        return [
            {
                "name": p.name,
                "version": p.version,
                "description": p.description,
                "primary": p.primary.label,
                "lookup": p.lookup.label,
                "output": [c.header for c in p.output],
            }
            for p in builtin_profiles().values()
        ]

    @app.post("/jobs", status_code=201, response_model=CreateJobResponse, tags=["jobs"])
    def create_job(body: CreateJobRequest, user: User) -> CreateJobResponse:
        return service.create_job(user, body)

    @app.get("/jobs", response_model=list[JobView], tags=["jobs"])
    def list_jobs(user: User) -> list[JobView]:
        return service.list_jobs(user)

    @app.get("/jobs/{job_id}", response_model=JobView, tags=["jobs"])
    def get_job(job_id: str, user: User) -> JobView:
        return JobView.of(service.get_job(user, job_id))

    @app.post("/jobs/{job_id}/submit", status_code=202, response_model=JobView, tags=["jobs"])
    def submit_job(job_id: str, user: User) -> JobView:
        return service.submit_job(user, job_id)

    @app.get("/jobs/{job_id}/download", response_model=DownloadResponse, tags=["jobs"])
    def download(job_id: str, user: User, artifact: str = "xlsx") -> DownloadResponse:
        if artifact not in ARTIFACTS:
            raise HTTPException(422, f"artifact must be one of: {', '.join(ARTIFACTS)}")
        return service.download(user, job_id, artifact)

    if settings.mode == "local" and isinstance(service.blobs, LocalBlobStore):
        _add_local_routes(app, service, settings)
    return app


def _add_local_routes(app: FastAPI, service: JobService, settings: Settings) -> None:
    """Stand-ins for S3 presigned URLs, and the web UI, for local development."""
    blobs = service.blobs
    assert isinstance(blobs, LocalBlobStore)

    @app.post("/_local/upload", status_code=204, include_in_schema=False)
    async def local_upload(
        key: Annotated[str, Form()], file: Annotated[UploadFile, File()]
    ) -> None:
        if not key.startswith("uploads/"):
            raise HTTPException(403, "invalid key")
        data = await file.read()
        if not 0 < len(data) <= settings.max_upload_bytes:
            raise HTTPException(400, "file is empty or too large")
        blobs.put(key, data, file.content_type or "application/octet-stream")

    @app.get("/_local/files/{key:path}", include_in_schema=False)
    def local_file(key: str) -> FileResponse:
        if not key.startswith("results/"):
            raise HTTPException(404)
        path = blobs.path_for(key)
        if not path.exists():
            raise HTTPException(404)
        return FileResponse(path, filename=path.name)

    web_dir = settings.web_dir or Path(__file__).resolve().parents[3] / "web"
    if web_dir.is_dir():
        app.mount("/", StaticFiles(directory=web_dir, html=True), name="web")
