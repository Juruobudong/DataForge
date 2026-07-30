from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .errors import NotFoundError, ValidationError


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _decode_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    result = dict(row)
    for key in (
        "metadata_json",
        "definition_json",
        "stats_json",
        "schema_json",
        "payload_json",
        "output_schema_json",
        "source_version_ids_json",
        "validation_json",
        "source_locator_json",
        "data_json",
    ):
        if key in result:
            raw = result.pop(key)
            result[key.removesuffix("_json")] = json.loads(raw) if raw else {}
    return result


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS sources (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS source_versions (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES sources(id),
    version_no INTEGER NOT NULL,
    blob_uri TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    media_type TEXT NOT NULL,
    original_filename TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(source_id, version_no),
    UNIQUE(source_id, sha256)
);

CREATE TABLE IF NOT EXISTS pipelines (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    version INTEGER NOT NULL,
    engine TEXT NOT NULL,
    definition_json TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    pipeline_id TEXT NOT NULL REFERENCES pipelines(id),
    source_version_id TEXT NOT NULL REFERENCES source_versions(id),
    status TEXT NOT NULL,
    engine TEXT NOT NULL,
    work_dir TEXT NOT NULL,
    stats_json TEXT NOT NULL,
    error TEXT,
    asset_version_id TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS run_events (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(id),
    sequence INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    message TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(run_id, sequence)
);

CREATE TABLE IF NOT EXISTS assets (
    id TEXT PRIMARY KEY,
    logical_key TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    asset_type TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS asset_versions (
    id TEXT PRIMARY KEY,
    asset_id TEXT NOT NULL REFERENCES assets(id),
    version_no INTEGER NOT NULL,
    run_id TEXT NOT NULL UNIQUE REFERENCES runs(id),
    blob_uri TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    record_count INTEGER NOT NULL,
    schema_json TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(asset_id, version_no)
);

CREATE TABLE IF NOT EXISTS lineage (
    id TEXT PRIMARY KEY,
    source_version_id TEXT NOT NULL REFERENCES source_versions(id),
    run_id TEXT NOT NULL REFERENCES runs(id),
    asset_version_id TEXT NOT NULL REFERENCES asset_versions(id),
    relation_type TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(source_version_id, run_id, asset_version_id)
);

CREATE TABLE IF NOT EXISTS publications (
    id TEXT PRIMARY KEY,
    asset_version_id TEXT NOT NULL REFERENCES asset_versions(id),
    channel TEXT NOT NULL,
    status TEXT NOT NULL,
    published_uri TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(asset_version_id, channel)
);

CREATE TABLE IF NOT EXISTS knowledge_types (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    schema_json TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS standard_pipelines (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    knowledge_type_id TEXT NOT NULL REFERENCES knowledge_types(id),
    pipeline_ref TEXT NOT NULL,
    engine TEXT NOT NULL,
    version INTEGER NOT NULL,
    description TEXT NOT NULL,
    output_schema_json TEXT NOT NULL,
    validation_status TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    is_default INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS knowledge_jobs (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    knowledge_type_id TEXT NOT NULL REFERENCES knowledge_types(id),
    standard_pipeline_id TEXT NOT NULL REFERENCES standard_pipelines(id),
    source_version_ids_json TEXT NOT NULL,
    status TEXT NOT NULL,
    progress INTEGER NOT NULL DEFAULT 0,
    validation_json TEXT NOT NULL,
    error TEXT,
    knowledge_base_id TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS knowledge_bases (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    knowledge_type_id TEXT NOT NULL REFERENCES knowledge_types(id),
    standard_pipeline_id TEXT NOT NULL REFERENCES standard_pipelines(id),
    job_id TEXT NOT NULL UNIQUE REFERENCES knowledge_jobs(id),
    record_count INTEGER NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS knowledge_records (
    id TEXT PRIMARY KEY,
    knowledge_base_id TEXT NOT NULL REFERENCES knowledge_bases(id),
    record_index INTEGER NOT NULL,
    source_version_id TEXT NOT NULL REFERENCES source_versions(id),
    run_id TEXT REFERENCES runs(id),
    asset_version_id TEXT REFERENCES asset_versions(id),
    source_locator_json TEXT NOT NULL,
    data_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(knowledge_base_id, record_index)
);

CREATE INDEX IF NOT EXISTS idx_source_versions_source ON source_versions(source_id, version_no);
CREATE INDEX IF NOT EXISTS idx_runs_source_version ON runs(source_version_id, created_at);
CREATE INDEX IF NOT EXISTS idx_run_events_run ON run_events(run_id, sequence);
CREATE INDEX IF NOT EXISTS idx_asset_versions_asset ON asset_versions(asset_id, version_no);
CREATE INDEX IF NOT EXISTS idx_knowledge_jobs_created ON knowledge_jobs(created_at);
CREATE INDEX IF NOT EXISTS idx_knowledge_records_base ON knowledge_records(knowledge_base_id, record_index);
CREATE INDEX IF NOT EXISTS idx_knowledge_records_source ON knowledge_records(source_version_id);
"""


class MetadataStore:
    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript(SCHEMA)
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(standard_pipelines)").fetchall()
            }
            if "is_default" not in columns:
                connection.execute(
                    "ALTER TABLE standard_pipelines ADD COLUMN is_default INTEGER NOT NULL DEFAULT 0"
                )

    def create_source(self, name: str, kind: str, metadata: dict[str, Any]) -> dict[str, Any]:
        source_id = new_id("src")
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO sources VALUES (?, ?, ?, ?, ?)",
                (source_id, name, kind, _json(metadata), utc_now()),
            )
        return self.get_source(source_id)

    def get_source(self, source_id: str) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
        if not row:
            raise NotFoundError(f"Source not found: {source_id}")
        return _decode_row(row) or {}

    def list_sources(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute("SELECT * FROM sources ORDER BY created_at DESC").fetchall()
        return [_decode_row(row) or {} for row in rows]

    def find_source_version_by_hash(self, source_id: str, sha256: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM source_versions WHERE source_id = ? AND sha256 = ?",
                (source_id, sha256),
            ).fetchone()
        return _decode_row(row)

    def create_source_version(
        self,
        source_id: str,
        blob_uri: str,
        sha256: str,
        size_bytes: int,
        media_type: str,
        original_filename: str,
    ) -> dict[str, Any]:
        version_id = new_id("srcv")
        with self.connect() as connection:
            next_version = connection.execute(
                "SELECT COALESCE(MAX(version_no), 0) + 1 FROM source_versions WHERE source_id = ?",
                (source_id,),
            ).fetchone()[0]
            connection.execute(
                """INSERT INTO source_versions
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    version_id,
                    source_id,
                    next_version,
                    blob_uri,
                    sha256,
                    size_bytes,
                    media_type,
                    original_filename,
                    utc_now(),
                ),
            )
        return self.get_source_version(version_id)

    def get_source_version(self, version_id: str) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM source_versions WHERE id = ?", (version_id,)).fetchone()
        if not row:
            raise NotFoundError(f"Source version not found: {version_id}")
        return _decode_row(row) or {}

    def list_source_versions(self, source_id: str) -> list[dict[str, Any]]:
        self.get_source(source_id)
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM source_versions WHERE source_id = ? ORDER BY version_no DESC",
                (source_id,),
            ).fetchall()
        return [_decode_row(row) or {} for row in rows]

    def register_pipeline(
        self,
        pipeline_id: str,
        name: str,
        version: int,
        engine: str,
        definition: dict[str, Any],
    ) -> dict[str, Any]:
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO pipelines (id, name, version, engine, definition_json, active, created_at)
                   VALUES (?, ?, ?, ?, ?, 1, ?)
                   ON CONFLICT(id) DO UPDATE SET
                     name = excluded.name,
                     version = excluded.version,
                     engine = excluded.engine,
                     definition_json = excluded.definition_json,
                     active = 1""",
                (pipeline_id, name, version, engine, _json(definition), utc_now()),
            )
        return self.get_pipeline(pipeline_id)

    def get_pipeline(self, pipeline_id: str) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM pipelines WHERE id = ?", (pipeline_id,)).fetchone()
        if not row:
            raise NotFoundError(f"Pipeline not found: {pipeline_id}")
        return _decode_row(row) or {}

    def list_pipelines(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute("SELECT * FROM pipelines ORDER BY name, version DESC").fetchall()
        return [_decode_row(row) or {} for row in rows]

    def create_run(
        self,
        pipeline_id: str,
        source_version_id: str,
        engine: str,
        work_dir: Path,
        *,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        run_id = run_id or new_id("run")
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO runs
                   (id, pipeline_id, source_version_id, status, engine, work_dir, stats_json, created_at)
                   VALUES (?, ?, ?, 'pending', ?, ?, '{}', ?)""",
                (run_id, pipeline_id, source_version_id, engine, str(work_dir), utc_now()),
            )
        return self.get_run(run_id)

    def get_run(self, run_id: str) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if not row:
            raise NotFoundError(f"Run not found: {run_id}")
        return _decode_row(row) or {}

    def list_runs(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute("SELECT * FROM runs ORDER BY created_at DESC").fetchall()
        return [_decode_row(row) or {} for row in rows]

    def transition_run(
        self,
        run_id: str,
        status: str,
        *,
        stats: dict[str, Any] | None = None,
        error: str | None = None,
        asset_version_id: str | None = None,
    ) -> dict[str, Any]:
        current = self.get_run(run_id)
        allowed = {
            "pending": {"preparing", "failed"},
            "preparing": {"running", "failed"},
            "running": {"publishing", "failed"},
            "publishing": {"completed", "failed"},
            "completed": set(),
            "failed": set(),
        }
        if status not in allowed[current["status"]]:
            raise ValidationError(f"Invalid run transition: {current['status']} -> {status}")
        started_at = utc_now() if status == "running" and not current.get("started_at") else current.get("started_at")
        completed_at = utc_now() if status in {"completed", "failed"} else current.get("completed_at")
        with self.connect() as connection:
            connection.execute(
                """UPDATE runs SET status = ?, stats_json = ?, error = ?, asset_version_id = ?,
                   started_at = ?, completed_at = ? WHERE id = ?""",
                (
                    status,
                    _json(stats if stats is not None else current.get("stats", {})),
                    error,
                    asset_version_id or current.get("asset_version_id"),
                    started_at,
                    completed_at,
                    run_id,
                ),
            )
        return self.get_run(run_id)

    def add_run_event(
        self,
        run_id: str,
        event_type: str,
        message: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        event_id = new_id("evt")
        with self.connect() as connection:
            sequence = connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 FROM run_events WHERE run_id = ?",
                (run_id,),
            ).fetchone()[0]
            connection.execute(
                "INSERT INTO run_events VALUES (?, ?, ?, ?, ?, ?, ?)",
                (event_id, run_id, sequence, event_type, message, _json(payload or {}), utc_now()),
            )
            row = connection.execute("SELECT * FROM run_events WHERE id = ?", (event_id,)).fetchone()
        return _decode_row(row) or {}

    def list_run_events(self, run_id: str) -> list[dict[str, Any]]:
        self.get_run(run_id)
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM run_events WHERE run_id = ? ORDER BY sequence", (run_id,)
            ).fetchall()
        return [_decode_row(row) or {} for row in rows]

    def publish_asset(
        self,
        *,
        logical_key: str,
        name: str,
        asset_type: str,
        run_id: str,
        source_version_id: str,
        blob_uri: str,
        sha256: str,
        size_bytes: int,
        record_count: int,
        schema: dict[str, str],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        with self.connect() as connection:
            asset_row = connection.execute("SELECT * FROM assets WHERE logical_key = ?", (logical_key,)).fetchone()
            if asset_row:
                asset_id = asset_row["id"]
            else:
                asset_id = new_id("asset")
                connection.execute(
                    "INSERT INTO assets VALUES (?, ?, ?, ?, ?)",
                    (asset_id, logical_key, name, asset_type, utc_now()),
                )
            next_version = connection.execute(
                "SELECT COALESCE(MAX(version_no), 0) + 1 FROM asset_versions WHERE asset_id = ?",
                (asset_id,),
            ).fetchone()[0]
            asset_version_id = new_id("assetv")
            connection.execute(
                """INSERT INTO asset_versions
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'published', ?)""",
                (
                    asset_version_id,
                    asset_id,
                    next_version,
                    run_id,
                    blob_uri,
                    sha256,
                    size_bytes,
                    record_count,
                    _json(schema),
                    utc_now(),
                ),
            )
            connection.execute(
                "INSERT INTO lineage VALUES (?, ?, ?, ?, 'derived_from', ?)",
                (new_id("lin"), source_version_id, run_id, asset_version_id, utc_now()),
            )
            connection.execute(
                "INSERT INTO publications VALUES (?, ?, 'internal', 'published', ?, ?)",
                (new_id("pub"), asset_version_id, blob_uri, utc_now()),
            )
        return self.get_asset(asset_id), self.get_asset_version(asset_version_id)

    def get_asset(self, asset_id: str) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM assets WHERE id = ?", (asset_id,)).fetchone()
        if not row:
            raise NotFoundError(f"Asset not found: {asset_id}")
        return _decode_row(row) or {}

    def get_asset_version(self, version_id: str) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM asset_versions WHERE id = ?", (version_id,)).fetchone()
        if not row:
            raise NotFoundError(f"Asset version not found: {version_id}")
        return _decode_row(row) or {}

    def list_asset_versions(self, asset_id: str) -> list[dict[str, Any]]:
        self.get_asset(asset_id)
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM asset_versions WHERE asset_id = ? ORDER BY version_no DESC",
                (asset_id,),
            ).fetchall()
        return [_decode_row(row) or {} for row in rows]

    def list_assets(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT a.*, av.id AS latest_version_id, av.version_no AS latest_version_no,
                          av.record_count, av.status
                   FROM assets a
                   LEFT JOIN asset_versions av ON av.asset_id = a.id
                     AND av.version_no = (SELECT MAX(v.version_no) FROM asset_versions v WHERE v.asset_id = a.id)
                   ORDER BY a.created_at DESC"""
            ).fetchall()
        return [_decode_row(row) or {} for row in rows]

    def get_lineage(self, asset_version_id: str) -> dict[str, Any]:
        self.get_asset_version(asset_version_id)
        with self.connect() as connection:
            row = connection.execute(
                """SELECT l.*, sv.source_id, sv.version_no AS source_version_no,
                          r.pipeline_id, r.engine, r.stats_json,
                          av.asset_id, av.version_no AS asset_version_no
                   FROM lineage l
                   JOIN source_versions sv ON sv.id = l.source_version_id
                   JOIN runs r ON r.id = l.run_id
                   JOIN asset_versions av ON av.id = l.asset_version_id
                   WHERE l.asset_version_id = ?""",
                (asset_version_id,),
            ).fetchone()
        if not row:
            raise NotFoundError(f"Lineage not found for asset version: {asset_version_id}")
        return _decode_row(row) or {}

    def register_knowledge_type(
        self, type_id: str, name: str, description: str, schema: dict[str, Any]
    ) -> dict[str, Any]:
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO knowledge_types VALUES (?, ?, ?, ?, 1, ?)
                   ON CONFLICT(id) DO UPDATE SET
                     name = excluded.name,
                     description = excluded.description,
                     schema_json = excluded.schema_json,
                     active = 1""",
                (type_id, name, description, _json(schema), utc_now()),
            )
        return self.get_knowledge_type(type_id)

    def get_knowledge_type(self, type_id: str) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM knowledge_types WHERE id = ?", (type_id,)).fetchone()
        if not row:
            raise NotFoundError(f"Knowledge type not found: {type_id}")
        return _decode_row(row) or {}

    def list_knowledge_types(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM knowledge_types WHERE active = 1 ORDER BY created_at"
            ).fetchall()
        return [_decode_row(row) or {} for row in rows]

    def register_standard_pipeline(
        self,
        pipeline_id: str,
        name: str,
        knowledge_type_id: str,
        pipeline_ref: str,
        engine: str,
        version: int,
        description: str,
        output_schema: dict[str, Any],
        validation_status: str,
        is_default: bool = False,
    ) -> dict[str, Any]:
        self.get_knowledge_type(knowledge_type_id)
        now = utc_now()
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO standard_pipelines
                   (id, name, knowledge_type_id, pipeline_ref, engine, version, description,
                    output_schema_json, validation_status, active, is_default, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                     name = excluded.name,
                     knowledge_type_id = excluded.knowledge_type_id,
                     pipeline_ref = excluded.pipeline_ref,
                     engine = excluded.engine,
                     version = excluded.version,
                     description = excluded.description,
                     output_schema_json = excluded.output_schema_json,
                     validation_status = excluded.validation_status,
                     active = 1,
                     is_default = CASE
                       WHEN excluded.is_default = 1 THEN 1
                       ELSE standard_pipelines.is_default
                     END,
                     updated_at = excluded.updated_at""",
                (
                    pipeline_id,
                    name,
                    knowledge_type_id,
                    pipeline_ref,
                    engine,
                    version,
                    description,
                    _json(output_schema),
                    validation_status,
                    int(is_default),
                    now,
                    now,
                ),
            )
            if is_default and validation_status == "validated":
                connection.execute(
                    """UPDATE standard_pipelines SET is_default = CASE WHEN id = ? THEN 1 ELSE 0 END
                       WHERE knowledge_type_id = ?""",
                    (pipeline_id, knowledge_type_id),
                )
        return self.get_standard_pipeline(pipeline_id)

    def set_default_standard_pipeline(self, pipeline_id: str) -> dict[str, Any]:
        pipeline = self.get_standard_pipeline(pipeline_id)
        if pipeline["validation_status"] != "validated" or not pipeline["active"]:
            raise ValidationError("只有已验证并启用的标准流程才能设为默认流程")
        with self.connect() as connection:
            connection.execute(
                """UPDATE standard_pipelines SET is_default = CASE WHEN id = ? THEN 1 ELSE 0 END
                   WHERE knowledge_type_id = ?""",
                (pipeline_id, pipeline["knowledge_type_id"]),
            )
        return self.get_standard_pipeline(pipeline_id)

    def get_default_standard_pipeline(self, knowledge_type_id: str) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute(
                """SELECT * FROM standard_pipelines
                   WHERE knowledge_type_id = ? AND active = 1 AND validation_status = 'validated'
                   ORDER BY is_default DESC, updated_at DESC LIMIT 1""",
                (knowledge_type_id,),
            ).fetchone()
        if not row:
            raise ValidationError("当前生成内容尚未开放，请联系流程管理员")
        return _decode_row(row) or {}

    def get_standard_pipeline(self, pipeline_id: str) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM standard_pipelines WHERE id = ?", (pipeline_id,)).fetchone()
        if not row:
            raise NotFoundError(f"Standard pipeline not found: {pipeline_id}")
        return _decode_row(row) or {}

    def list_standard_pipelines(self, knowledge_type_id: str | None = None) -> list[dict[str, Any]]:
        query = """SELECT p.*, k.name AS knowledge_type_name
                   FROM standard_pipelines p
                   JOIN knowledge_types k ON k.id = p.knowledge_type_id
                   WHERE p.active = 1"""
        params: tuple[Any, ...] = ()
        if knowledge_type_id:
            query += " AND p.knowledge_type_id = ?"
            params = (knowledge_type_id,)
        query += " ORDER BY k.created_at, p.name"
        with self.connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [_decode_row(row) or {} for row in rows]

    def create_knowledge_job(
        self,
        name: str,
        knowledge_type_id: str,
        standard_pipeline_id: str,
        source_version_ids: list[str],
    ) -> dict[str, Any]:
        job_id = new_id("kjob")
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO knowledge_jobs
                   (id, name, knowledge_type_id, standard_pipeline_id, source_version_ids_json,
                    status, progress, validation_json, created_at)
                   VALUES (?, ?, ?, ?, ?, 'pending', 0, '{}', ?)""",
                (job_id, name, knowledge_type_id, standard_pipeline_id, _json(source_version_ids), utc_now()),
            )
        return self.get_knowledge_job(job_id)

    def update_knowledge_job(
        self,
        job_id: str,
        *,
        status: str | None = None,
        progress: int | None = None,
        validation: dict[str, Any] | None = None,
        error: str | None = None,
        knowledge_base_id: str | None = None,
    ) -> dict[str, Any]:
        current = self.get_knowledge_job(job_id)
        new_status = status or current["status"]
        started_at = current.get("started_at")
        completed_at = current.get("completed_at")
        if new_status == "running" and not started_at:
            started_at = utc_now()
        if new_status in {"completed", "failed"}:
            completed_at = utc_now()
        with self.connect() as connection:
            connection.execute(
                """UPDATE knowledge_jobs SET status = ?, progress = ?, validation_json = ?,
                   error = ?, knowledge_base_id = ?, started_at = ?, completed_at = ? WHERE id = ?""",
                (
                    new_status,
                    progress if progress is not None else current["progress"],
                    _json(validation if validation is not None else current.get("validation", {})),
                    error,
                    knowledge_base_id or current.get("knowledge_base_id"),
                    started_at,
                    completed_at,
                    job_id,
                ),
            )
        return self.get_knowledge_job(job_id)

    def get_knowledge_job(self, job_id: str) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM knowledge_jobs WHERE id = ?", (job_id,)).fetchone()
        if not row:
            raise NotFoundError(f"Knowledge job not found: {job_id}")
        return _decode_row(row) or {}

    def list_knowledge_jobs(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT j.*, k.name AS knowledge_type_name, p.name AS standard_pipeline_name
                   FROM knowledge_jobs j
                   JOIN knowledge_types k ON k.id = j.knowledge_type_id
                   JOIN standard_pipelines p ON p.id = j.standard_pipeline_id
                   ORDER BY j.created_at DESC"""
            ).fetchall()
        return [_decode_row(row) or {} for row in rows]

    def create_knowledge_base(
        self,
        name: str,
        knowledge_type_id: str,
        standard_pipeline_id: str,
        job_id: str,
        records: list[dict[str, Any]],
    ) -> dict[str, Any]:
        base_id = new_id("kb")
        now = utc_now()
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO knowledge_bases VALUES (?, ?, ?, ?, ?, ?, 'available', ?)",
                (base_id, name, knowledge_type_id, standard_pipeline_id, job_id, len(records), now),
            )
            for index, record in enumerate(records):
                connection.execute(
                    """INSERT INTO knowledge_records
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        new_id("krec"),
                        base_id,
                        index,
                        record["source_version_id"],
                        record.get("run_id"),
                        record.get("asset_version_id"),
                        _json(record.get("source_locator", {})),
                        _json(record["data"]),
                        now,
                    ),
                )
        return self.get_knowledge_base(base_id)

    def get_knowledge_base(self, base_id: str) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute(
                """SELECT b.*, k.name AS knowledge_type_name, p.name AS standard_pipeline_name
                   FROM knowledge_bases b
                   JOIN knowledge_types k ON k.id = b.knowledge_type_id
                   JOIN standard_pipelines p ON p.id = b.standard_pipeline_id
                   WHERE b.id = ?""",
                (base_id,),
            ).fetchone()
        if not row:
            raise NotFoundError(f"Knowledge base not found: {base_id}")
        return _decode_row(row) or {}

    def list_knowledge_bases(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT b.*, k.name AS knowledge_type_name, p.name AS standard_pipeline_name
                   FROM knowledge_bases b
                   JOIN knowledge_types k ON k.id = b.knowledge_type_id
                   JOIN standard_pipelines p ON p.id = b.standard_pipeline_id
                   ORDER BY b.created_at DESC"""
            ).fetchall()
        return [_decode_row(row) or {} for row in rows]

    def list_knowledge_records(
        self, base_id: str, limit: int = 50, offset: int = 0, query: str = ""
    ) -> list[dict[str, Any]]:
        self.get_knowledge_base(base_id)
        where = "r.knowledge_base_id = ?"
        params: list[Any] = [base_id]
        if query.strip():
            where += " AND (r.data_json LIKE ? OR s.name LIKE ? OR sv.original_filename LIKE ?)"
            pattern = f"%{query.strip()}%"
            params.extend([pattern, pattern, pattern])
        params.extend([max(1, min(limit, 200)), max(0, offset)])
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT r.*, s.name AS source_name, sv.original_filename, sv.version_no AS source_version_no
                   FROM knowledge_records r
                   JOIN source_versions sv ON sv.id = r.source_version_id
                   JOIN sources s ON s.id = sv.source_id
                   WHERE """ + where + " ORDER BY r.record_index LIMIT ? OFFSET ?",
                tuple(params),
            ).fetchall()
        return [_decode_row(row) or {} for row in rows]

    def count_knowledge_records(self, base_id: str, query: str = "") -> int:
        self.get_knowledge_base(base_id)
        where = "r.knowledge_base_id = ?"
        params: list[Any] = [base_id]
        if query.strip():
            where += " AND (r.data_json LIKE ? OR s.name LIKE ? OR sv.original_filename LIKE ?)"
            pattern = f"%{query.strip()}%"
            params.extend([pattern, pattern, pattern])
        with self.connect() as connection:
            return int(
                connection.execute(
                    """SELECT COUNT(*) FROM knowledge_records r
                       JOIN source_versions sv ON sv.id = r.source_version_id
                       JOIN sources s ON s.id = sv.source_id
                       WHERE """ + where,
                    tuple(params),
                ).fetchone()[0]
            )

    def get_knowledge_record_lineage(self, record_id: str) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute(
                """SELECT r.*, b.name AS knowledge_base_name, k.name AS knowledge_type_name,
                          p.name AS standard_pipeline_name, s.name AS source_name,
                          sv.original_filename, sv.version_no AS source_version_no
                   FROM knowledge_records r
                   JOIN knowledge_bases b ON b.id = r.knowledge_base_id
                   JOIN knowledge_types k ON k.id = b.knowledge_type_id
                   JOIN standard_pipelines p ON p.id = b.standard_pipeline_id
                   JOIN source_versions sv ON sv.id = r.source_version_id
                   JOIN sources s ON s.id = sv.source_id
                   WHERE r.id = ?""",
                (record_id,),
            ).fetchone()
        if not row:
            raise NotFoundError(f"Knowledge record not found: {record_id}")
        return _decode_row(row) or {}
