"""add operator descriptions and versioned I/O examples.

Revision ID: 20260814_operator_examples
Revises: 20260813_pdf_ocr_artifacts
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260814_operator_examples"
down_revision = "20260813_pdf_ocr_artifacts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    definition_columns = {item["name"] for item in inspector.get_columns("operator_definitions")}
    if "description" not in definition_columns:
        op.add_column("operator_definitions", sa.Column("description", sa.Text(), nullable=True))
        op.execute(sa.text("UPDATE operator_definitions SET description = '' WHERE description IS NULL"))
        op.alter_column("operator_definitions", "description", existing_type=sa.Text(), existing_nullable=True, nullable=False)

    version_columns = {item["name"] for item in inspector.get_columns("operator_versions")}
    for name in ("input_example", "output_example"):
        if name in version_columns:
            continue
        op.add_column("operator_versions", sa.Column(name, sa.JSON(), nullable=True))
        if bind.dialect.name == "mysql":
            op.execute(sa.text(f"UPDATE operator_versions SET {name} = JSON_OBJECT() WHERE {name} IS NULL"))
        else:
            op.execute(sa.text(f"UPDATE operator_versions SET {name} = '{{}}' WHERE {name} IS NULL"))
        op.alter_column("operator_versions", name, existing_type=sa.JSON(), existing_nullable=True, nullable=False)


def downgrade() -> None:
    raise RuntimeError("V7 schema downgrade is intentionally disabled; use a new empty database")
