"""add controlled knowledge extensions and document-library processing.

Revision ID: 20260812_three_knowledge_types
Revises: 20260811_v7_governed_catalog
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from dataforge.v7.models import Base


revision = "20260812_three_knowledge_types"
down_revision = "20260811_v7_governed_catalog"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    def add_if_missing(table: str, column: sa.Column) -> None:
        if column.name not in {item["name"] for item in inspector.get_columns(table)}:
            op.add_column(table, column)

    add_if_missing("knowledge_jobs", sa.Column("document_library_template_binding_id", sa.String(length=64), nullable=True))
    add_if_missing("knowledge_type_index_bindings", sa.Column("index_profile_revision_id", sa.String(length=64), nullable=True))
    add_if_missing("knowledge_index_profiles", sa.Column("current_revision_id", sa.String(length=64), nullable=True))
    # V7 is rebuilt from an empty database for this release.  These tables
    # establish the new controlled contract; no legacy type data is migrated.
    Base.metadata.create_all(bind=bind, checkfirst=True)


def downgrade() -> None:
    raise RuntimeError("V7 schema downgrade is intentionally disabled; use a new empty database")
