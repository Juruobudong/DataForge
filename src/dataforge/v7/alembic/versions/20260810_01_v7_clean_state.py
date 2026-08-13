"""create the V7 clean-state platform schema.

Revision ID: 20260810_v7
Revises: None
"""
from __future__ import annotations

from alembic import op

from dataforge.v7.models import Base


revision = "20260810_v7"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The V7 database is empty at bootstrap.  The migration owns every V7 table
    # and never inspects or transforms pre-existing application tables.
    Base.metadata.create_all(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    raise RuntimeError("V7 schema downgrade is intentionally disabled; use a new empty database")
