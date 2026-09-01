from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


ORG_CODE_PRESETS_ENV = "DATAFORGE_ORG_CODE_PRESETS"
DEFAULT_RELEASE_STAGE_ENV = "DATAFORGE_DEFAULT_RELEASE_STAGE"
LOCAL_MILVUS_URI_ENV = "DATAFORGE_LOCAL_MILVUS_URI"


@dataclass(frozen=True)
class OrgCodePreset:
    name: str
    org_code: str


DEFAULT_ORG_CODE_PRESETS = (
    OrgCodePreset(name="厦门第一医院", org_code="KMDSRMYY"),
    OrgCodePreset(name="厦门市中医院", org_code="XMSZ"),
)


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


def _org_code_presets_environment() -> tuple[OrgCodePreset, ...]:
    raw = os.getenv(ORG_CODE_PRESETS_ENV)
    if raw is None or not raw.strip():
        return DEFAULT_ORG_CODE_PRESETS
    try:
        values = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{ORG_CODE_PRESETS_ENV} 必须是合法 JSON 数组") from exc
    if not isinstance(values, list):
        raise ValueError(f"{ORG_CODE_PRESETS_ENV} 必须是 JSON 数组")
    presets: list[OrgCodePreset] = []
    seen_codes: set[str] = set()
    required_fields = {"name", "org_code"}
    for index, value in enumerate(values):
        if not isinstance(value, dict) or set(value) != required_fields:
            raise ValueError(
                f"{ORG_CODE_PRESETS_ENV}[{index}] 只能包含 name 和 org_code"
            )
        name, org_code = value["name"], value["org_code"]
        if not isinstance(name, str) or not isinstance(org_code, str):
            raise ValueError(f"{ORG_CODE_PRESETS_ENV}[{index}] 的 name 和 org_code 必须是字符串")
        normalized_name, normalized_code = name.strip(), org_code.strip()
        if not normalized_name or not normalized_code:
            raise ValueError(f"{ORG_CODE_PRESETS_ENV}[{index}] 的 name 和 org_code 不能为空")
        if len(normalized_name) > 255 or len(normalized_code) > 120:
            raise ValueError(f"{ORG_CODE_PRESETS_ENV}[{index}] 超出名称或编码长度限制")
        if normalized_code in seen_codes:
            raise ValueError(f"{ORG_CODE_PRESETS_ENV} 包含重复 org_code：{normalized_code}")
        seen_codes.add(normalized_code)
        presets.append(OrgCodePreset(name=normalized_name, org_code=normalized_code))
    return tuple(presets)


def _default_release_stage_environment() -> str:
    value = os.getenv(DEFAULT_RELEASE_STAGE_ENV, "test").strip().lower()
    if value not in {"test", "production"}:
        raise ValueError(f"{DEFAULT_RELEASE_STAGE_ENV} 只允许 test 或 production")
    return value


def _local_milvus_uri_environment() -> str:
    value = os.getenv(LOCAL_MILVUS_URI_ENV, "http://dataforge-milvus:19530").strip()
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.path not in {"", "/"}:
        raise ValueError(f"{LOCAL_MILVUS_URI_ENV} 必须是合法的 http(s) Milvus URI")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"{LOCAL_MILVUS_URI_ENV} 端口无效") from exc
    if port is None:
        raise ValueError(f"{LOCAL_MILVUS_URI_ENV} 必须显式包含端口")
    return value.rstrip("/")


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
    graph_llm_timeout_seconds: int = 400
    graph_runner_timeout_buffer_seconds: int = 300
    knowledge_job_concurrency: int = 3
    vector_sync_concurrency: int = 2
    parse_concurrency: int = 2
    derived_runs_enabled: bool = False
    derived_run_commit_enabled: bool = False
    instance_mode: str = "central"
    instance_code: str = "central-default"
    migration_signing_private_key: str | None = None
    migration_trusted_public_keys: str | None = None
    migration_signing_key_id: str = "central-default"
    config_encryption_key: str | None = None
    org_code_presets: tuple[OrgCodePreset, ...] = DEFAULT_ORG_CODE_PRESETS
    default_release_stage: str = "test"
    local_milvus_default_uri: str = "http://dataforge-milvus:19530"

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
            graph_llm_timeout_seconds=_positive_int_environment("DATAFORGE_GRAPH_LLM_TIMEOUT_SECONDS", 400),
            graph_runner_timeout_buffer_seconds=_positive_int_environment(
                "DATAFORGE_GRAPH_RUNNER_TIMEOUT_BUFFER_SECONDS", 300,
            ),
            knowledge_job_concurrency=_positive_int_environment("DATAFORGE_KNOWLEDGE_JOB_CONCURRENCY", 3),
            vector_sync_concurrency=_positive_int_environment("DATAFORGE_VECTOR_SYNC_CONCURRENCY", 2),
            parse_concurrency=_positive_int_environment("DATAFORGE_PARSE_CONCURRENCY", 2),
            derived_runs_enabled=os.getenv("DATAFORGE_DERIVED_RUNS_ENABLED", "0") == "1",
            derived_run_commit_enabled=os.getenv("DATAFORGE_DERIVED_RUN_COMMIT_ENABLED", "0") == "1",
            instance_mode=os.getenv("DATAFORGE_INSTANCE_MODE", "central").strip().lower(),
            instance_code=os.getenv("DATAFORGE_INSTANCE_CODE", "central-default").strip(),
            migration_signing_private_key=_read_secret("DATAFORGE_MIGRATION_SIGNING_PRIVATE_KEY"),
            migration_trusted_public_keys=_read_secret("DATAFORGE_MIGRATION_TRUSTED_PUBLIC_KEYS"),
            migration_signing_key_id=os.getenv("DATAFORGE_MIGRATION_SIGNING_KEY_ID", "central-default").strip(),
            config_encryption_key=_read_secret("DATAFORGE_CONFIG_ENCRYPTION_KEY"),
            org_code_presets=_org_code_presets_environment(),
            default_release_stage=_default_release_stage_environment(),
            local_milvus_default_uri=_local_milvus_uri_environment(),
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
        self._ensure_writable_directory(self.routing_dir, "DATAFORGE_ROUTING_DIR")
        self._ensure_writable_directory(self.migration_dir, "DATAFORGE_MIGRATION_DIR")

    @staticmethod
    def _ensure_writable_directory(directory: Path, environment: str) -> None:
        """Create and remove a unique file so existing mounts are proven writable."""
        try:
            directory.mkdir(parents=True, exist_ok=True)
            descriptor, probe_name = tempfile.mkstemp(
                prefix=".dataforge-write-probe-", dir=directory
            )
            try:
                os.close(descriptor)
            finally:
                Path(probe_name).unlink(missing_ok=True)
        except OSError as exc:
            raise RuntimeError(f"{environment} 目录不可写：{directory}") from exc
