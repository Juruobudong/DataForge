"""Milvus service registry verification state."""
from alembic import op
import sqlalchemy as sa


revision = "20260829_milvus_registry"
down_revision = "20260829_operator_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    target_columns = {column["name"] for column in inspector.get_columns("milvus_targets")}
    target_indexes = {index["name"] for index in inspector.get_indexes("milvus_targets")}
    additions = {
        "verification_status": sa.Column("verification_status", sa.String(length=32), nullable=False, server_default="pending_verification"),
        "verified_at": sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        "verification_error": sa.Column("verification_error", sa.Text(), nullable=True),
        "candidate_milvus_url": sa.Column("candidate_milvus_url", sa.String(length=1024), nullable=True),
        "candidate_verification_status": sa.Column("candidate_verification_status", sa.String(length=32), nullable=True),
        "candidate_verified_at": sa.Column("candidate_verified_at", sa.DateTime(timezone=True), nullable=True),
        "candidate_verification_error": sa.Column("candidate_verification_error", sa.Text(), nullable=True),
    }
    with op.batch_alter_table("milvus_targets") as batch:
        for name, column in additions.items():
            if name not in target_columns:
                batch.add_column(column)
        if "ix_milvus_targets_verification_status" not in target_indexes:
            batch.create_index("ix_milvus_targets_verification_status", ["verification_status"], unique=False)
    local_columns = {column["name"] for column in inspector.get_columns("local_milvus_configurations")}
    if "verification_error" not in local_columns:
        with op.batch_alter_table("local_milvus_configurations") as batch:
            batch.add_column(sa.Column("verification_error", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("local_milvus_configurations") as batch:
        batch.drop_column("verification_error")
    with op.batch_alter_table("milvus_targets") as batch:
        batch.drop_index("ix_milvus_targets_verification_status")
        batch.drop_column("candidate_verification_error")
        batch.drop_column("candidate_verified_at")
        batch.drop_column("candidate_verification_status")
        batch.drop_column("candidate_milvus_url")
        batch.drop_column("verification_error")
        batch.drop_column("verified_at")
        batch.drop_column("verification_status")
