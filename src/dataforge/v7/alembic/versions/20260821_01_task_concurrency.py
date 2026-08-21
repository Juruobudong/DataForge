"""add concurrent task leases.

Revision ID: 20260821_task_concurrency
Revises: 20260820_inst_release_v2
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260821_task_concurrency"
down_revision = "20260820_inst_release_v2"
branch_labels = None
depends_on = None


def _columns(bind, table: str) -> set[str]:
    return {item["name"] for item in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    existing = set(sa.inspect(bind).get_table_names())
    if "knowledge_library_work_leases" not in existing:
        op.create_table(
            "knowledge_library_work_leases",
            sa.Column("knowledge_library_id", sa.String(length=64), nullable=False),
            sa.Column("work_kind", sa.String(length=32), nullable=False),
            sa.Column("work_id", sa.String(length=64), nullable=False),
            sa.Column("lease_owner", sa.String(length=255), nullable=False),
            sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["knowledge_library_id"], ["knowledge_libraries.id"]),
            sa.PrimaryKeyConstraint("knowledge_library_id"),
        )
        op.create_index("ix_kl_work_lease_kind", "knowledge_library_work_leases", ["work_kind"])
        op.create_index("ix_kl_work_lease_work", "knowledge_library_work_leases", ["work_id"])
        op.create_index("ix_kl_work_lease_expiry", "knowledge_library_work_leases", ["lease_expires_at"])

    columns = _columns(bind, "vector_sync_jobs")
    if "attempt_count" not in columns:
        op.add_column("vector_sync_jobs", sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False))
    if "lease_owner" not in columns:
        op.add_column("vector_sync_jobs", sa.Column("lease_owner", sa.String(length=255), nullable=True))
    if "lease_expires_at" not in columns:
        op.add_column("vector_sync_jobs", sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True))
    indexes = {item["name"] for item in sa.inspect(bind).get_indexes("vector_sync_jobs")}
    if "ix_vector_sync_jobs_status" not in indexes:
        op.create_index("ix_vector_sync_jobs_status", "vector_sync_jobs", ["status"])


def downgrade() -> None:
    bind = op.get_bind()
    existing = set(sa.inspect(bind).get_table_names())
    if "knowledge_library_work_leases" in existing:
        op.drop_table("knowledge_library_work_leases")
    if "vector_sync_jobs" in existing:
        indexes = {item["name"] for item in sa.inspect(bind).get_indexes("vector_sync_jobs")}
        if "ix_vector_sync_jobs_status" in indexes:
            op.drop_index("ix_vector_sync_jobs_status", table_name="vector_sync_jobs")
        columns = _columns(bind, "vector_sync_jobs")
        for name in ("lease_expires_at", "lease_owner", "attempt_count"):
            if name in columns:
                op.drop_column("vector_sync_jobs", name)
