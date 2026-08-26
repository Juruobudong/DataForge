"""add stable artifact ordering index for flow run details.

Revision ID: 20260826_artifact_run_order
Revises: 20260825_flow_authoring_modes
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260826_artifact_run_order"
down_revision = "20260825_flow_authoring_modes"
branch_labels = None
depends_on = None

INDEX_NAME = "ix_artifacts_flow_run_created_at_id"
INDEX_COLUMNS = ("flow_run_id", "created_at", "id")


def _indexes() -> list[dict]:
    if "artifacts" not in sa.inspect(op.get_bind()).get_table_names():
        return []
    return sa.inspect(op.get_bind()).get_indexes("artifacts")


def upgrade() -> None:
    if "artifacts" not in sa.inspect(op.get_bind()).get_table_names():
        return
    indexes = _indexes()
    if not any(
        item.get("name") == INDEX_NAME or tuple(item.get("column_names") or ()) == INDEX_COLUMNS
        for item in indexes
    ):
        op.create_index(INDEX_NAME, "artifacts", list(INDEX_COLUMNS), unique=False)


def downgrade() -> None:
    if "artifacts" not in sa.inspect(op.get_bind()).get_table_names():
        return
    if any(item.get("name") == INDEX_NAME for item in _indexes()):
        op.drop_index(INDEX_NAME, table_name="artifacts")
