"""Persist live health separately from immutable Milvus verification."""
from alembic import op
import sqlalchemy as sa


revision = "20260830_milvus_health"
down_revision = "20260830_milvus_contract"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("milvus_target_revisions")}
    index_rows = inspector.get_indexes("milvus_target_revisions")
    indexes = {index["name"] for index in index_rows}
    with op.batch_alter_table("milvus_target_revisions") as batch:
        if "health_status" not in columns:
            batch.add_column(sa.Column(
                "health_status", sa.String(length=32), nullable=False, server_default="unknown",
            ))
        if "health_checked_at" not in columns:
            batch.add_column(sa.Column("health_checked_at", sa.DateTime(timezone=True), nullable=True))
        if "health_latency_ms" not in columns:
            batch.add_column(sa.Column("health_latency_ms", sa.Integer(), nullable=True))
        if "health_error" not in columns:
            batch.add_column(sa.Column("health_error", sa.Text(), nullable=True))
        if ("ix_milvus_target_revisions_health" not in indexes
                and not any(list(index.get("column_names") or []) == ["health_status"] for index in index_rows)):
            batch.create_index(
                "ix_milvus_target_revisions_health", ["health_status"], unique=False,
            )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("milvus_target_revisions")}
    indexes = {index["name"] for index in inspector.get_indexes("milvus_target_revisions")}
    with op.batch_alter_table("milvus_target_revisions") as batch:
        if "ix_milvus_target_revisions_health" in indexes:
            batch.drop_index("ix_milvus_target_revisions_health")
        for column in ("health_error", "health_latency_ms", "health_checked_at", "health_status"):
            if column in columns:
                batch.drop_column(column)
