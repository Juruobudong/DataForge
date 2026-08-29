"""First-class operator source and catalog grouping."""
from alembic import op
import sqlalchemy as sa


revision = "20260829_operator_identity"
down_revision = "20260829_source_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("operator_definitions")}
    indexes = {index["name"] for index in inspector.get_indexes("operator_definitions")}
    with op.batch_alter_table("operator_definitions") as batch:
        if "source" not in columns:
            batch.add_column(sa.Column("source", sa.String(length=32), nullable=False))
        if "catalog_group" not in columns:
            batch.add_column(sa.Column("catalog_group", sa.String(length=32), nullable=False))
        if "ix_operator_definitions_source" not in indexes:
            batch.create_index("ix_operator_definitions_source", ["source"], unique=False)
        if "ix_operator_definitions_catalog_group" not in indexes:
            batch.create_index("ix_operator_definitions_catalog_group", ["catalog_group"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("operator_definitions") as batch:
        batch.drop_index("ix_operator_definitions_catalog_group")
        batch.drop_index("ix_operator_definitions_source")
        batch.drop_column("catalog_group")
        batch.drop_column("source")
