"""add flow authoring modes (standard | advanced).

Revision ID: 20260825_flow_authoring_modes
Revises: 20260825_source_chunk_sets
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260825_flow_authoring_modes"
down_revision = "20260825_source_chunk_sets"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {item["name"] for item in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    for table in ("knowledge_flow_templates", "knowledge_flow_template_revisions"):
        columns = _columns(table)
        if "authoring_mode" not in columns:
            op.add_column(table, sa.Column("authoring_mode", sa.String(32), nullable=False, server_default="advanced"))
        if "managed_template_code" not in columns:
            op.add_column(table, sa.Column("managed_template_code", sa.String(64), nullable=True))


def downgrade() -> None:
    for table in ("knowledge_flow_template_revisions", "knowledge_flow_templates"):
        columns = _columns(table)
        if "managed_template_code" in columns:
            op.drop_column(table, "managed_template_code")
        if "authoring_mode" in columns:
            op.drop_column(table, "authoring_mode")
