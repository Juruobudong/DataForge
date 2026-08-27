"""add governed parameter asset applicability.

Revision ID: 20260827_operator_parameters
Revises: 20260826_flow_dev_convergence
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260827_operator_parameters"
down_revision = "20260826_flow_dev_convergence"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {item["name"] for item in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    for table in ("prompt_template_revisions", "quality_profile_revisions"):
        if "knowledge_types" in _columns(table):
            continue
        with op.batch_alter_table(table) as batch:
            batch.add_column(sa.Column("knowledge_types", sa.JSON(), nullable=False, server_default='["*"]'))


def downgrade() -> None:
    for table in ("quality_profile_revisions", "prompt_template_revisions"):
        if "knowledge_types" in _columns(table):
            with op.batch_alter_table(table) as batch:
                batch.drop_column("knowledge_types")
