from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _read_secret(name: str) -> str | None:
    """Read a Docker-secret file when present, otherwise use the environment."""
    value = os.getenv(name)
    if value:
        return value
    file_name = os.getenv(f"{name}_FILE")
    if not file_name:
        return None
    try:
        return Path(file_name).read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


@dataclass(frozen=True)
class Settings:
    project_root: Path
    state_dir: Path
    admin_password_hash: str | None = None
    session_secret: str | None = None
    database_url: str | None = None
    minio_endpoint: str | None = None
    minio_access_key: str | None = None
    minio_secret_key: str | None = None
    minio_bucket: str = "dataforge"
    runner_url: str | None = None
    runner_service_token: str | None = None

    @classmethod
    def load(
        cls,
        project_root: str | Path | None = None,
    ) -> "Settings":
        root = Path(project_root or os.getenv("DATAFORGE_ROOT") or Path.cwd()).resolve()
        state = Path(os.getenv("DATAFORGE_STATE_DIR") or root / ".dataforge").resolve()
        return cls(
            project_root=root,
            state_dir=state,
            admin_password_hash=_read_secret("DATAFORGE_ADMIN_PASSWORD_HASH"),
            session_secret=_read_secret("DATAFORGE_SESSION_SECRET"),
            database_url=os.getenv("DATAFORGE_DATABASE_URL"),
            minio_endpoint=os.getenv("DATAFORGE_MINIO_ENDPOINT"),
            minio_access_key=_read_secret("DATAFORGE_MINIO_ACCESS_KEY"),
            minio_secret_key=_read_secret("DATAFORGE_MINIO_SECRET_KEY"),
            minio_bucket=os.getenv("DATAFORGE_MINIO_BUCKET", "dataforge"),
            runner_url=os.getenv("DATAFORGE_RUNNER_URL"),
            runner_service_token=_read_secret("DATAFORGE_RUNNER_SERVICE_TOKEN"),
        )

    @property
    def authentication_enabled(self) -> bool:
        return bool(self.admin_password_hash and self.session_secret)

    @property
    def platform_database_url(self) -> str:
        """Production runs V7 in MySQL ``dataforge``; SQLite is local-only."""
        return self.database_url or f"sqlite:///{self.state_dir / 'platform-dev.sqlite3'}"

    @property
    def routing_dir(self) -> Path:
        configured = os.getenv("DATAFORGE_ROUTING_DIR")
        return Path(configured).resolve() if configured else self.state_dir / "routing"

    def ensure_directories(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.routing_dir.mkdir(parents=True, exist_ok=True)
