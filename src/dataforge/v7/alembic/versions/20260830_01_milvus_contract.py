"""Immutable Milvus connection revisions and authoring binding."""
from alembic import op
import sqlalchemy as sa


revision = "20260830_milvus_contract"
down_revision = "20260829_milvus_registry"
branch_labels = None
depends_on = None


def _has_index(indexes, columns: list[str]) -> bool:
    return any(list(index.get("column_names") or []) == columns for index in indexes)


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "milvus_target_revisions" not in tables:
        op.create_table(
            "milvus_target_revisions",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column("milvus_target_id", sa.String(length=64), nullable=False),
            sa.Column("revision_no", sa.Integer(), nullable=False),
            sa.Column("milvus_url", sa.String(length=1024), nullable=False),
            sa.Column("token_ciphertext", sa.Text(), nullable=True),
            sa.Column("token_key_version", sa.String(length=32), nullable=True),
            sa.Column("connection_fingerprint", sa.String(length=64), nullable=False),
            sa.Column("verification_status", sa.String(length=32), nullable=False),
            sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("verification_error", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["milvus_target_id"], ["milvus_targets.id"]),
            sa.UniqueConstraint("milvus_target_id", "revision_no", name="uq_milvus_target_revision_no"),
        )
    revision_index_rows = sa.inspect(op.get_bind()).get_indexes("milvus_target_revisions")
    revision_indexes = {index["name"] for index in revision_index_rows}
    for name, columns in (
        ("ix_milvus_target_revisions_target", ["milvus_target_id"]),
        ("ix_milvus_target_revisions_fingerprint", ["connection_fingerprint"]),
        ("ix_milvus_target_revisions_status", ["verification_status"]),
    ):
        if name not in revision_indexes and not _has_index(revision_index_rows, columns):
            op.create_index(name, "milvus_target_revisions", columns)

    target_columns = {column["name"] for column in inspector.get_columns("milvus_targets")}
    target_index_rows = inspector.get_indexes("milvus_targets")
    target_indexes = {index["name"] for index in target_index_rows}
    with op.batch_alter_table("milvus_targets") as batch:
        if "current_revision_id" not in target_columns:
            batch.add_column(sa.Column("current_revision_id", sa.String(length=64), nullable=True))
        if "candidate_revision_id" not in target_columns:
            batch.add_column(sa.Column("candidate_revision_id", sa.String(length=64), nullable=True))
        if "ix_milvus_targets_current_revision_id" not in target_indexes and not _has_index(target_index_rows, ["current_revision_id"]):
            batch.create_index("ix_milvus_targets_current_revision_id", ["current_revision_id"], unique=False)
        if "ix_milvus_targets_candidate_revision_id" not in target_indexes and not _has_index(target_index_rows, ["candidate_revision_id"]):
            batch.create_index("ix_milvus_targets_candidate_revision_id", ["candidate_revision_id"], unique=False)

    # The pre-contract seed linked pending targets. They have no verified
    # revision and must not survive the new system-level gate.
    op.execute(sa.text(
        "DELETE FROM deployment_targets WHERE milvus_target_id IN "
        "(SELECT id FROM milvus_targets WHERE current_revision_id IS NULL)"
    ))

    instance_columns = {column["name"] for column in inspector.get_columns("dataforge_instances")}
    instance_index_rows = inspector.get_indexes("dataforge_instances")
    instance_indexes = {index["name"] for index in instance_index_rows}
    with op.batch_alter_table("dataforge_instances") as batch:
        if "authoring_milvus_target_id" not in instance_columns:
            batch.add_column(sa.Column("authoring_milvus_target_id", sa.String(length=64), nullable=True))
            batch.create_foreign_key("fk_dataforge_instance_authoring_milvus_target", "milvus_targets",
                                     ["authoring_milvus_target_id"], ["id"])
        if ("ix_dataforge_instances_authoring_milvus_target_id" not in instance_indexes
                and not _has_index(instance_index_rows, ["authoring_milvus_target_id"])):
            batch.create_index("ix_dataforge_instances_authoring_milvus_target_id", ["authoring_milvus_target_id"], unique=False)

    asset_columns = {column["name"] for column in inspector.get_columns("knowledge_asset_versions")}
    asset_index_rows = inspector.get_indexes("knowledge_asset_versions")
    asset_indexes = {index["name"] for index in asset_index_rows}
    with op.batch_alter_table("knowledge_asset_versions") as batch:
        if "authoring_target_revision_id" not in asset_columns:
            batch.add_column(sa.Column("authoring_target_revision_id", sa.String(length=64), nullable=True))
            batch.create_foreign_key("fk_asset_authoring_milvus_revision", "milvus_target_revisions",
                                     ["authoring_target_revision_id"], ["id"])
        if "authoring_connection_fingerprint" not in asset_columns:
            batch.add_column(sa.Column("authoring_connection_fingerprint", sa.String(length=64), nullable=True))
        if ("ix_asset_authoring_target_revision_id" not in asset_indexes
                and not _has_index(asset_index_rows, ["authoring_target_revision_id"])):
            batch.create_index("ix_asset_authoring_target_revision_id", ["authoring_target_revision_id"], unique=False)
        if ("ix_asset_authoring_connection_fingerprint" not in asset_indexes
                and not _has_index(asset_index_rows, ["authoring_connection_fingerprint"])):
            batch.create_index("ix_asset_authoring_connection_fingerprint", ["authoring_connection_fingerprint"], unique=False)

    local_columns = {column["name"] for column in inspector.get_columns("local_milvus_configurations")}
    local_index_rows = inspector.get_indexes("local_milvus_configurations")
    local_indexes = {index["name"] for index in local_index_rows}
    with op.batch_alter_table("local_milvus_configurations") as batch:
        if "token_ciphertext" not in local_columns:
            batch.add_column(sa.Column("token_ciphertext", sa.Text(), nullable=True))
        if "token_key_version" not in local_columns:
            batch.add_column(sa.Column("token_key_version", sa.String(length=32), nullable=True))
        if "config_revision" not in local_columns:
            batch.add_column(sa.Column("config_revision", sa.Integer(), nullable=False, server_default="1"))
        if "connection_fingerprint" not in local_columns:
            batch.add_column(sa.Column("connection_fingerprint", sa.String(length=64), nullable=False, server_default=""))
        if ("ix_local_milvus_connection_fingerprint" not in local_indexes
                and not _has_index(local_index_rows, ["connection_fingerprint"])):
            batch.create_index("ix_local_milvus_connection_fingerprint", ["connection_fingerprint"], unique=False)
        for obsolete in ("secret_key_version", "secret_ciphertext", "username", "tls_enabled", "database_name"):
            if obsolete in local_columns:
                batch.drop_column(obsolete)


def downgrade() -> None:
    with op.batch_alter_table("local_milvus_configurations") as batch:
        batch.add_column(sa.Column("database_name", sa.String(length=255), nullable=False, server_default="default"))
        batch.add_column(sa.Column("tls_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.add_column(sa.Column("username", sa.String(length=255), nullable=True))
        batch.add_column(sa.Column("secret_ciphertext", sa.Text(), nullable=True))
        batch.add_column(sa.Column("secret_key_version", sa.String(length=32), nullable=True))
        batch.drop_index("ix_local_milvus_connection_fingerprint")
        batch.drop_column("connection_fingerprint")
        batch.drop_column("config_revision")
        batch.drop_column("token_key_version")
        batch.drop_column("token_ciphertext")
    with op.batch_alter_table("knowledge_asset_versions") as batch:
        batch.drop_index("ix_asset_authoring_connection_fingerprint")
        batch.drop_index("ix_asset_authoring_target_revision_id")
        batch.drop_constraint("fk_asset_authoring_milvus_revision", type_="foreignkey")
        batch.drop_column("authoring_connection_fingerprint")
        batch.drop_column("authoring_target_revision_id")
    with op.batch_alter_table("dataforge_instances") as batch:
        batch.drop_index("ix_dataforge_instances_authoring_milvus_target_id")
        batch.drop_constraint("fk_dataforge_instance_authoring_milvus_target", type_="foreignkey")
        batch.drop_column("authoring_milvus_target_id")
    with op.batch_alter_table("milvus_targets") as batch:
        batch.drop_index("ix_milvus_targets_candidate_revision_id")
        batch.drop_index("ix_milvus_targets_current_revision_id")
        batch.drop_column("candidate_revision_id")
        batch.drop_column("current_revision_id")
    op.drop_index("ix_milvus_target_revisions_status", table_name="milvus_target_revisions")
    op.drop_index("ix_milvus_target_revisions_fingerprint", table_name="milvus_target_revisions")
    op.drop_index("ix_milvus_target_revisions_target", table_name="milvus_target_revisions")
    op.drop_table("milvus_target_revisions")
