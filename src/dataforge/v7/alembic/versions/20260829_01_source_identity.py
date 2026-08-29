"""Source content identity, CAS blobs and activation-scoped processing.

Revision ID: 20260829_source_identity
Revises: 20260828_retrieval_debug
"""
from alembic import op
import sqlalchemy as sa


revision = "20260829_source_identity"
down_revision = "20260828_retrieval_debug"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {item["name"] for item in sa.inspect(op.get_bind()).get_columns(table)}


def _uniques(table: str) -> set[str]:
    return {item["name"] for item in sa.inspect(op.get_bind()).get_unique_constraints(table) if item.get("name")}


def _indexes(table: str) -> set[str]:
    return {item["name"] for item in sa.inspect(op.get_bind()).get_indexes(table) if item.get("name")}


def upgrade() -> None:
    source_columns = _columns("sources")
    version_columns = _columns("source_versions")
    with op.batch_alter_table("source_versions") as batch:
        if "blob_uri" not in version_columns:
            batch.add_column(sa.Column("blob_uri", sa.String(80), nullable=False))
        if "media_type" not in version_columns:
            batch.add_column(sa.Column("media_type", sa.String(255), nullable=False))
        if "original_filename" not in version_columns:
            batch.add_column(sa.Column("original_filename", sa.String(512), nullable=False))
        if "activation_no" not in version_columns:
            batch.add_column(sa.Column("activation_no", sa.Integer(), nullable=False, server_default="1"))
        if "object_key" in version_columns:
            batch.drop_column("object_key")
        if "mime_type" in version_columns:
            batch.drop_column("mime_type")
        if "uq_source_version_sha256" not in _uniques("source_versions"):
            batch.create_unique_constraint("uq_source_version_sha256", ["source_id", "sha256"])
    if "ix_source_versions_blob_uri" not in _indexes("source_versions"):
        op.create_index("ix_source_versions_blob_uri", "source_versions", ["blob_uri"])
    if "original_filename" in source_columns:
        with op.batch_alter_table("sources") as batch:
            batch.drop_column("original_filename")

    additions = (
        ("knowledge_dispatches", "activation_no"),
        ("knowledge_job_review_inputs", "activation_no"),
        ("document_library_processing_records", "activation_no"),
        ("debug_run_review_inputs", "activation_no"),
    )
    for table, column in additions:
        if column not in _columns(table):
            op.add_column(table, sa.Column(column, sa.Integer(), nullable=False, server_default="1"))
    if "blob_uris" not in _columns("document_deletion_jobs"):
        op.add_column("document_deletion_jobs", sa.Column("blob_uris", sa.JSON(), nullable=False, server_default="[]"))

    dispatch_uniques = _uniques("knowledge_dispatches")
    processing_uniques = _uniques("document_library_processing_records")
    with op.batch_alter_table("knowledge_dispatches") as batch:
        if "uq_knowledge_dispatch_snapshot" in dispatch_uniques:
            batch.drop_constraint("uq_knowledge_dispatch_snapshot", type_="unique")
        if "uq_knowledge_dispatch_snapshot_activation" not in dispatch_uniques:
            batch.create_unique_constraint(
                "uq_knowledge_dispatch_snapshot_activation", ["source_review_snapshot_id", "activation_no"],
            )
    with op.batch_alter_table("document_library_processing_records") as batch:
        if "uq_doc_processing_review_revision" in processing_uniques:
            batch.drop_constraint("uq_doc_processing_review_revision", type_="unique")
        if "uq_doc_processing_review_activation" not in processing_uniques:
            batch.create_unique_constraint(
                "uq_doc_processing_review_activation",
                ["document_library_template_binding_id", "source_version_id",
                 "knowledge_flow_template_revision_id", "source_review_snapshot_id", "activation_no"],
            )


def downgrade() -> None:
    raise RuntimeError("V7 Source 内容身份迁移不支持降级；请使用新的空数据库")
