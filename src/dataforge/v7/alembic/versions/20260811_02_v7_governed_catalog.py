"""add the V7 governed operator catalog and execution snapshot model.

Revision ID: 20260811_v7_governed_catalog
Revises: 20260811_v7_features
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from dataforge.v7.models import Base


revision = "20260811_v7_governed_catalog"
down_revision = "20260811_v7_features"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    def add_if_missing(table: str, column: sa.Column) -> None:
        columns = {item["name"] for item in inspector.get_columns(table)}
        if column.name not in columns:
            op.add_column(table, column)

    add_if_missing("knowledge_types", sa.Column("kind", sa.String(length=32), nullable=False, server_default="builtin"))
    add_if_missing("knowledge_types", sa.Column("current_revision_id", sa.String(length=64), nullable=True))
    add_if_missing("knowledge_flow_template_revisions", sa.Column("execution_snapshot_id", sa.String(length=64), nullable=True))
    add_if_missing("knowledge_libraries", sa.Column("knowledge_type_revision_id", sa.String(length=64), nullable=True))
    add_if_missing("knowledge_items", sa.Column("knowledge_type_revision_id", sa.String(length=64), nullable=True))
    job_columns = {item["name"] for item in inspector.get_columns("knowledge_jobs")}
    if "sink_library_ids" not in job_columns:
        if bind.dialect.name == "mysql":
            # MySQL rejects the plain JSON DEFAULT emitted by SQLAlchemy.  Add
            # the column as nullable, populate existing jobs, then make the
            # persisted schema match the non-null model contract.
            op.add_column("knowledge_jobs", sa.Column("sink_library_ids", sa.JSON(), nullable=True))
            op.execute(sa.text("UPDATE knowledge_jobs SET sink_library_ids = JSON_OBJECT() WHERE sink_library_ids IS NULL"))
            op.alter_column(
                "knowledge_jobs",
                "sink_library_ids",
                existing_type=sa.JSON(),
                existing_nullable=True,
                nullable=False,
            )
        else:
            add_if_missing("knowledge_jobs", sa.Column("sink_library_ids", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))
    add_if_missing("knowledge_jobs", sa.Column("execution_snapshot_id", sa.String(length=64), nullable=True))

    # The remaining tables are new.  create_all is deliberately idempotent so
    # it also works against deployments upgraded from either prior V7 revision.
    Base.metadata.create_all(bind=bind, checkfirst=True)


def downgrade() -> None:
    raise RuntimeError("V7 schema downgrade is intentionally disabled; use a new empty database")
