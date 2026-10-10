"""Runtime configuration, read from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path


def _env_list(name: str, default: str = "") -> list[str]:
    return [v.strip() for v in os.environ.get(name, default).split(",") if v.strip()]


@dataclass(frozen=True)
class Settings:
    mode: str = "local"  # "local" (filesystem, no auth) or "aws"
    data_bucket: str = ""
    jobs_table: str = ""
    queue_url: str = ""
    kms_key_id: str = ""
    local_data_dir: Path = Path(".aggtool-data")
    max_upload_bytes: int = 25 * 1024 * 1024
    upload_url_ttl_seconds: int = 900
    download_url_ttl_seconds: int = 300
    job_retention_days: int = 30
    cors_origins: list[str] = field(default_factory=list)
    web_dir: Path | None = None

    @classmethod
    def from_env(cls) -> Settings:
        web_dir = os.environ.get("AGGTOOL_WEB_DIR")
        return cls(
            mode=os.environ.get("AGGTOOL_MODE", "local"),
            data_bucket=os.environ.get("AGGTOOL_DATA_BUCKET", ""),
            jobs_table=os.environ.get("AGGTOOL_JOBS_TABLE", ""),
            queue_url=os.environ.get("AGGTOOL_QUEUE_URL", ""),
            kms_key_id=os.environ.get("AGGTOOL_KMS_KEY_ID", ""),
            local_data_dir=Path(os.environ.get("AGGTOOL_LOCAL_DATA_DIR", ".aggtool-data")),
            max_upload_bytes=int(os.environ.get("AGGTOOL_MAX_UPLOAD_BYTES", 25 * 1024 * 1024)),
            job_retention_days=int(os.environ.get("AGGTOOL_JOB_RETENTION_DAYS", 30)),
            cors_origins=_env_list("AGGTOOL_CORS_ORIGINS"),
            web_dir=Path(web_dir) if web_dir else None,
        )

    def validate(self) -> None:
        if self.mode not in ("local", "aws"):
            raise ValueError(f"AGGTOOL_MODE must be 'local' or 'aws', got '{self.mode}'")
        if self.mode == "aws":
            missing = [
                n
                for n, v in (
                    ("AGGTOOL_DATA_BUCKET", self.data_bucket),
                    ("AGGTOOL_JOBS_TABLE", self.jobs_table),
                    ("AGGTOOL_QUEUE_URL", self.queue_url),
                )
                if not v
            ]
            if missing:
                raise ValueError(f"Missing required settings for aws mode: {', '.join(missing)}")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings.from_env()
    settings.validate()
    return settings
