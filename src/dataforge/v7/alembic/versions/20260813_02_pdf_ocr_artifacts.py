"""link parser artifacts to source versions.

Revision ID: 20260813_pdf_ocr_artifacts
Revises: 20260813_graph_storage_contracts
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260813_pdf_ocr_artifacts"
down_revision = "20260813_graph_storage_contracts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {item["name"] for item in sa.inspect(bind).get_columns("artifacts")}
    if "source_version_id" not in columns:
        with op.batch_alter_table("artifacts") as batch:
            batch.add_column(sa.Column("source_version_id", sa.String(64), nullable=True))
            batch.create_foreign_key("fk_artifact_source_version", "source_versions", ["source_version_id"], ["id"])
            batch.create_index("ix_artifacts_source_version_id", ["source_version_id"])


def downgrade() -> None:
    raise RuntimeError("V7 schema downgrade is intentionally disabled; use a new empty database")
