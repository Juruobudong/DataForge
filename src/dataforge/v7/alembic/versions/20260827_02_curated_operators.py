"""Curated operator surfaces and validation evidence."""
from alembic import op
import sqlalchemy as sa

revision = "20260827_curated_operators"
down_revision = "20260827_operator_parameters"
branch_labels = None
depends_on = None


def upgrade():
    if "surfaces" not in {column["name"] for column in sa.inspect(op.get_bind()).get_columns("operator_definitions")}:
        with op.batch_alter_table("operator_definitions") as batch:
            batch.add_column(sa.Column("surfaces", sa.JSON(), nullable=False, server_default="[]"))
    from dataforge.v7.models import OperatorValidationRun
    OperatorValidationRun.__table__.create(op.get_bind(), checkfirst=True)


def downgrade():
    op.drop_table("operator_validation_runs")
    with op.batch_alter_table("operator_definitions") as batch:
        batch.drop_column("surfaces")
