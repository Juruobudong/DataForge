"""add immutable asset versions and institution release v2.

Revision ID: 20260820_inst_release_v2
Revises: 20260817_qa_agent_route
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from dataforge.v7.models import Base


revision = "20260820_inst_release_v2"
down_revision = "20260817_qa_agent_route"
branch_labels = None
depends_on = None


def _columns(bind, table: str) -> set[str]:
    return {item["name"] for item in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = set(inspector.get_table_names())
    for name in (
        "knowledge_asset_versions",
        "document_library_processing_baselines",
        "institution_release_drafts",
        "institution_release_draft_projects",
        "institution_release_snapshots",
        "project_route_version_assets",
        "local_milvus_configurations",
        "imported_route_candidates",
        "knowledge_asset_gc_jobs",
    ):
        if name not in existing:
            Base.metadata.tables[name].create(bind, checkfirst=True)

    deployment_columns = _columns(bind, "deployments")
    if "institution_code_locked_at" not in deployment_columns:
        with op.batch_alter_table("deployments") as batch:
            batch.add_column(sa.Column("institution_code_locked_at", sa.DateTime(timezone=True), nullable=True))

    vector_columns = _columns(bind, "vector_sync_jobs")
    if "asset_version_id" not in vector_columns:
        with op.batch_alter_table("vector_sync_jobs") as batch:
            batch.add_column(sa.Column("asset_version_id", sa.String(length=64), nullable=True))
            batch.create_index("ix_vector_sync_jobs_asset_version_id", ["asset_version_id"])
            batch.create_foreign_key(
                "fk_vector_sync_asset_version", "knowledge_asset_versions",
                ["asset_version_id"], ["id"],
            )

    migration_columns = _columns(bind, "knowledge_migration_jobs")
    with op.batch_alter_table("knowledge_migration_jobs") as batch:
        if "target_deployment_id" not in migration_columns:
            batch.add_column(sa.Column("target_deployment_id", sa.String(length=64), nullable=True))
            batch.create_index("ix_migration_target_deployment", ["target_deployment_id"])
            batch.create_foreign_key(
                "fk_migration_target_deployment", "deployments", ["target_deployment_id"], ["id"]
            )
        if "release_snapshot_id" not in migration_columns:
            batch.add_column(sa.Column("release_snapshot_id", sa.String(length=64), nullable=True))
            batch.create_index("ix_migration_release_snapshot", ["release_snapshot_id"])
            batch.create_foreign_key(
                "fk_migration_release_snapshot", "institution_release_snapshots",
                ["release_snapshot_id"], ["id"],
            )


def downgrade() -> None:
    raise RuntimeError("DataForge V7 不支持降级")
