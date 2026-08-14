"""add governed Knowledge Type Profile and Collection lifecycle.

Revision ID: 20260814_collection_lifecycle
Revises: 20260814_flow_workbench
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260814_collection_lifecycle"
down_revision = "20260814_flow_workbench"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    profile_columns = {item["name"] for item in inspector.get_columns("knowledge_index_profiles")}
    if "origin" not in profile_columns:
        op.add_column("knowledge_index_profiles", sa.Column("origin", sa.String(32), nullable=False, server_default="manual"))
        op.create_index("ix_knowledge_index_profiles_origin", "knowledge_index_profiles", ["origin"])
    if "owner_knowledge_type_id" not in profile_columns:
        op.add_column("knowledge_index_profiles", sa.Column("owner_knowledge_type_id", sa.String(64), nullable=True))
        op.create_index("ix_knowledge_index_profiles_owner_knowledge_type_id", "knowledge_index_profiles", ["owner_knowledge_type_id"])
        if bind.dialect.name != "sqlite":
            op.create_foreign_key(
                "fk_index_profile_owner_type", "knowledge_index_profiles", "knowledge_types",
                ["owner_knowledge_type_id"], ["id"],
            )
    bind.execute(sa.text(
        "UPDATE knowledge_index_profiles SET origin='builtin' "
        "WHERE code IN ('text','qa-question','qa-full','graph','graph-triple','graph-semantic')"
    ))

    if "managed_collection_deletion_jobs" not in set(inspector.get_table_names()):
        op.create_table(
            "managed_collection_deletion_jobs",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("managed_collection_id", sa.String(64), sa.ForeignKey("managed_collections.id"), nullable=False),
            sa.Column("preflight_json", sa.JSON(), nullable=False),
            sa.Column("status", sa.String(32), nullable=False, server_default="queued"),
            sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("lease_owner", sa.String(255), nullable=True),
            sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_managed_collection_deletion_jobs_managed_collection_id", "managed_collection_deletion_jobs", ["managed_collection_id"])
        op.create_index("ix_managed_collection_deletion_jobs_status", "managed_collection_deletion_jobs", ["status"])


def downgrade() -> None:
    raise RuntimeError("DataForge V7 migrations are forward-only")
