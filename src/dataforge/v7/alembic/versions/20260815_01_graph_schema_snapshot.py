"""add graph schema snapshot to knowledge libraries.

Revision ID: 20260815_graph_schema_snapshot
Revises: 20260814_collection_lifecycle
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260815_graph_schema_snapshot"
down_revision = "20260814_collection_lifecycle"
branch_labels = None
depends_on = None


def _columns(inspector, table: str) -> set[str]:
    return {item["name"] for item in inspector.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = _columns(inspector, "knowledge_libraries")
    with op.batch_alter_table("knowledge_libraries") as batch:
        if "graph_schema_snapshot_json" not in existing:
            batch.add_column(sa.Column("graph_schema_snapshot_json", sa.JSON(), nullable=True))
        if "graph_schema_hash" not in existing:
            batch.add_column(sa.Column("graph_schema_hash", sa.String(64), nullable=True))
        if "source_template_revision_id" not in existing:
            batch.add_column(sa.Column("source_template_revision_id", sa.String(64), nullable=True))
            batch.create_foreign_key(
                "fk_knowledge_library_source_template_revision",
                "knowledge_flow_template_revisions",
                ["source_template_revision_id"],
                ["id"],
            )
            batch.create_index("ix_knowledge_libraries_source_template_revision", ["source_template_revision_id"])


def downgrade() -> None:
    raise RuntimeError("V7 schema downgrade is intentionally disabled; use a new empty database")
