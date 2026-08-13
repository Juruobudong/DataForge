"""persist chunk-scoped knowledge generation outcomes.

Revision ID: 20260812_chunk_generation_result
Revises: 20260812_three_knowledge_types
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from dataforge.v7.models import Base


revision = "20260812_chunk_generation_result"
down_revision = "20260812_three_knowledge_types"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The V7 deployment is initialized from an empty database for test and
    # release deployments.  create_all remains idempotent for an existing V7
    # database and lets SQLAlchemy apply the MySQL-safe unique key definition.
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {item["name"] for item in inspector.get_columns("knowledge_item_sources")}
    if "source_chunk_id" not in columns:
        with op.batch_alter_table("knowledge_item_sources") as batch:
            batch.add_column(sa.Column("source_chunk_id", sa.String(length=128), nullable=False, server_default=""))
            unique_names = {item.get("name") for item in inspector.get_unique_constraints("knowledge_item_sources")}
            if "uq_item_source_version" in unique_names:
                batch.drop_constraint("uq_item_source_version", type_="unique")
            batch.create_unique_constraint(
                "uq_item_source_version_chunk",
                ["knowledge_item_id", "source_version_id", "source_chunk_id"],
            )
    Base.metadata.create_all(bind=bind, checkfirst=True)


def downgrade() -> None:
    raise RuntimeError("V7 schema downgrade is intentionally disabled; use a new empty database")
