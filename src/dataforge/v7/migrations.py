"""Alembic bootstrap and runtime guard for the isolated V7 database."""
from __future__ import annotations

import os
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine

CURRENT_SCHEMA_REVISION = "20260825_source_chunk_sets"


def _config(database_url: str) -> Config:
    # Docker installs the package non-editably, while the Alembic scripts stay
    # in the copied application source at DATAFORGE_ROOT (/app).  Local source
    # execution uses the current repository directory.
    root = Path(os.getenv("DATAFORGE_ROOT") or Path.cwd()).resolve()
    if not (root / "alembic.ini").is_file():
        root = Path(__file__).resolve().parents[3]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "src" / "dataforge" / "v7" / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def upgrade(database_url: str) -> dict[str, str]:
    """Initialize or upgrade the V7 schema through Alembic only."""
    command.upgrade(_config(database_url), "head")
    return {"current_revision": assert_schema_current(database_url)}


def assert_schema_current(database_url: str) -> str:
    engine = create_engine(database_url, future=True)
    try:
        with engine.connect() as connection:
            revision = MigrationContext.configure(connection).get_current_revision()
    except Exception as exc:
        raise RuntimeError("V7 schema 未初始化；请先运行 dataforge-migrate --upgrade-platform") from exc
    finally:
        engine.dispose()
    if revision != CURRENT_SCHEMA_REVISION:
        raise RuntimeError(
            f"V7 schema revision 必须为 {CURRENT_SCHEMA_REVISION}，当前为 {revision or '未初始化'}；"
            "请先运行 dataforge-migrate --upgrade-platform"
        )
    return revision
