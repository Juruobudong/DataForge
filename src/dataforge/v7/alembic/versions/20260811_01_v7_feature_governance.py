"""add V7 governance, provenance, and template-revision tables.

Revision ID: 20260811_v7_features
Revises: 20260810_v7
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260811_v7_features"
down_revision = "20260810_v7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    def add_if_missing(table: str, column: sa.Column) -> None:
        if column.name not in {item["name"] for item in inspector.get_columns(table)}:
            op.add_column(table, column)
    add_if_missing("knowledge_flow_templates", sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()))
    add_if_missing("knowledge_item_sources", sa.Column("anchor_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))
    add_if_missing("knowledge_item_sources", sa.Column("evidence_text", sa.Text(), nullable=False, server_default=""))
    add_if_missing("knowledge_item_sources", sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.false()))
    add_if_missing("knowledge_changes", sa.Column("before_snapshot_json", sa.JSON(), nullable=True))
    add_if_missing("knowledge_changes", sa.Column("after_snapshot_json", sa.JSON(), nullable=True))
    if "knowledge_flow_template_revisions" not in inspector.get_table_names():
        op.create_table(
        "knowledge_flow_template_revisions",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("knowledge_flow_template_id", sa.String(length=64), sa.ForeignKey("knowledge_flow_templates.id"), nullable=False, index=True),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("definition_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="draft", index=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("knowledge_flow_template_id", "revision_no", name="uq_flow_template_revision"),
        )
    add_if_missing("knowledge_jobs", sa.Column("knowledge_flow_template_revision_id", sa.String(length=64), sa.ForeignKey("knowledge_flow_template_revisions.id"), nullable=True))
    if "knowledge_library_deletion_jobs" not in inspector.get_table_names():
        op.create_table(
        "knowledge_library_deletion_jobs",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("knowledge_library_id", sa.String(length=64), sa.ForeignKey("knowledge_libraries.id"), nullable=False, index=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="queued", index=True),
        sa.Column("lease_owner", sa.String(length=255), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )


def downgrade() -> None:
    raise RuntimeError("V7 schema downgrade is intentionally disabled; use a new empty database")
