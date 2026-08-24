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


def _positive_int_environment(name: str, default: int) -> int:
    raw = os.getenv(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} 必须是正整数") from exc
    if value <= 0:
        raise ValueError(f"{name} 必须是正整数")
    return value


@dataclass(frozen=True)
class Settings:
    project_root: Path
    state_dir: Path
    dataflow_path: Path | None = None
    admin_password_hash: str | None = None
    session_secret: str | None = None
    database_url: str | None = None
    minio_endpoint: str | None = None
    minio_access_key: str | None = None
    minio_secret_key: str | None = None
    minio_bucket: str = "dataforge"
    runner_url: str | None = None
    runner_service_token: str | None = None
    runner_timeout_seconds: float = 1860.0
    knowledge_job_concurrency: int = 3
    vector_sync_concurrency: int = 2
    source_preparation_concurrency: int = 2
    derived_runs_enabled: bool = False
    derived_run_commit_enabled: bool = False
    instance_mode: str = "central"
    instance_code: str = "central-default"
    migration_signing_private_key: str | None = None
    migration_trusted_public_keys: str | None = None
    migration_signing_key_id: str = "central-default"
    config_encryption_key: str | None = None

    @classmethod
    def load(
        cls,
        project_root: str | Path | None = None,
        dataflow_path: str | Path | None = None,
    ) -> "Settings":
        root = Path(project_root or os.getenv("DATAFORGE_ROOT") or Path.cwd()).resolve()
        state = Path(os.getenv("DATAFORGE_STATE_DIR") or root / ".dataforge").resolve()
        configured_dataflow = dataflow_path or os.getenv("DATAFORGE_DATAFLOW_PATH")
        if configured_dataflow:
            resolved_dataflow: Path | None = Path(configured_dataflow).resolve()
        else:
            conventional = root.parent / "DataFlow"
            resolved_dataflow = conventional.resolve() if conventional.exists() else None
        return cls(
            project_root=root,
            state_dir=state,
            dataflow_path=resolved_dataflow,
            admin_password_hash=_read_secret("DATAFORGE_ADMIN_PASSWORD_HASH"),
            session_secret=_read_secret("DATAFORGE_SESSION_SECRET"),
            database_url=os.getenv("DATAFORGE_DATABASE_URL"),
            minio_endpoint=os.getenv("DATAFORGE_MINIO_ENDPOINT"),
            minio_access_key=_read_secret("DATAFORGE_MINIO_ACCESS_KEY"),
            minio_secret_key=_read_secret("DATAFORGE_MINIO_SECRET_KEY"),
            minio_bucket=os.getenv("DATAFORGE_MINIO_BUCKET", "dataforge"),
            runner_url=os.getenv("DATAFORGE_RUNNER_URL"),
            runner_service_token=_read_secret("DATAFORGE_RUNNER_SERVICE_TOKEN"),
            runner_timeout_seconds=float(os.getenv("DATAFORGE_RUNNER_TIMEOUT_SECONDS", "1860")),
            knowledge_job_concurrency=_positive_int_environment("DATAFORGE_KNOWLEDGE_JOB_CONCURRENCY", 3),
            vector_sync_concurrency=_positive_int_environment("DATAFORGE_VECTOR_SYNC_CONCURRENCY", 2),
            source_preparation_concurrency=_positive_int_environment("DATAFORGE_SOURCE_PREPARATION_CONCURRENCY", 2),
            derived_runs_enabled=os.getenv("DATAFORGE_DERIVED_RUNS_ENABLED", "0") == "1",
            derived_run_commit_enabled=os.getenv("DATAFORGE_DERIVED_RUN_COMMIT_ENABLED", "0") == "1",
            instance_mode=os.getenv("DATAFORGE_INSTANCE_MODE", "central").strip().lower(),
            instance_code=os.getenv("DATAFORGE_INSTANCE_CODE", "central-default").strip(),
            migration_signing_private_key=_read_secret("DATAFORGE_MIGRATION_SIGNING_PRIVATE_KEY"),
            migration_trusted_public_keys=_read_secret("DATAFORGE_MIGRATION_TRUSTED_PUBLIC_KEYS"),
            migration_signing_key_id=os.getenv("DATAFORGE_MIGRATION_SIGNING_KEY_ID", "central-default").strip(),
            config_encryption_key=_read_secret("DATAFORGE_CONFIG_ENCRYPTION_KEY"),
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

    @property
    def migration_dir(self) -> Path:
        configured = os.getenv("DATAFORGE_MIGRATION_DIR")
        return Path(configured).resolve() if configured else self.state_dir / "migrations"

    @property
    def database_path(self) -> Path:
        return self.state_dir / "metadata.sqlite3"

    @property
    def blobs_dir(self) -> Path:
        return self.state_dir / "blobs"

    @property
    def runs_dir(self) -> Path:
        return self.state_dir / "runs"

    def ensure_directories(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.blobs_dir.mkdir(parents=True, exist_ok=True)
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self.routing_dir.mkdir(parents=True, exist_ok=True)
        self.migration_dir.mkdir(parents=True, exist_ok=True)
