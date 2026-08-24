"""persist the latest AssetVersion verification result.

Revision ID: 20260823_vector_inventory
Revises: 20260821_task_concurrency
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260823_vector_inventory"
down_revision = "20260821_task_concurrency"
branch_labels = None
depends_on = None


def _columns(bind, table: str) -> set[str]:
    return {item["name"] for item in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    columns = _columns(bind, "knowledge_asset_versions")
    additions = {
        "last_verification_status": sa.Column("last_verification_status", sa.String(length=32), nullable=True),
        "last_verified_at": sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
        "last_observed_count": sa.Column("last_observed_count", sa.BigInteger(), nullable=True),
        "last_observed_digest": sa.Column("last_observed_digest", sa.String(length=64), nullable=True),
        "last_verification_error": sa.Column("last_verification_error", sa.Text(), nullable=True),
    }
    for name, column in additions.items():
        if name not in columns:
            op.add_column("knowledge_asset_versions", column)


def downgrade() -> None:
    bind = op.get_bind()
    columns = _columns(bind, "knowledge_asset_versions")
    for name in (
        "last_verification_error",
        "last_observed_digest",
        "last_observed_count",
        "last_verified_at",
        "last_verification_status",
    ):
        if name in columns:
            op.drop_column("knowledge_asset_versions", name)
