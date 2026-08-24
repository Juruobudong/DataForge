"""add SourceChunk preparation and mandatory human review gate.

Revision ID: 20260824_chunk_review_gate
Revises: 20260824_model_services
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260824_chunk_review_gate"
down_revision = "20260824_model_services"
branch_labels = None
depends_on = None


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def _add_column(table: str, column: sa.Column) -> None:
    inspector = sa.inspect(op.get_bind())
    if column.name not in {item["name"] for item in inspector.get_columns(table)}:
        op.add_column(table, column)


def _add_index(table: str, name: str, columns: list[str]) -> None:
    inspector = sa.inspect(op.get_bind())
    if name not in {item["name"] for item in inspector.get_indexes(table)}:
        op.create_index(name, table, columns)


def _constraint_names(table: str, kind: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if kind == "unique":
        return {item["name"] for item in inspector.get_unique_constraints(table) if item.get("name")}
    if kind == "foreignkey":
        return {item["name"] for item in inspector.get_foreign_keys(table) if item.get("name")}
    if kind == "check":
        return {item["name"] for item in inspector.get_check_constraints(table) if item.get("name")}
    return set()


def _foreign_key_columns(table: str) -> set[tuple[str, ...]]:
    return {
        tuple(item.get("constrained_columns") or [])
        for item in sa.inspect(op.get_bind()).get_foreign_keys(table)
    }


def upgrade() -> None:
    existing = set(sa.inspect(op.get_bind()).get_table_names())

    for column in (
        sa.Column("preparation_status", sa.String(32), nullable=False, server_default="queued"),
        sa.Column("review_status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("current_review_snapshot_id", sa.String(64), nullable=True),
    ):
        _add_column("source_versions", column)
    _add_index("source_versions", "ix_source_versions_preparation_status", ["preparation_status"])
    _add_index("source_versions", "ix_source_versions_review_status", ["review_status"])
    _add_index("source_versions", "ix_source_versions_current_review_snapshot_id", ["current_review_snapshot_id"])

    for column in (
        sa.Column("current_revision_id", sa.String(64), nullable=True),
        sa.Column("lifecycle_status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("review_status", sa.String(32), nullable=False, server_default="pending_review"),
        sa.Column("reviewed_by", sa.String(255), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
    ):
        _add_column("source_chunks", column)
    _add_index("source_chunks", "ix_source_chunks_current_revision_id", ["current_revision_id"])
    _add_index("source_chunks", "ix_source_chunks_lifecycle_status", ["lifecycle_status"])
    _add_index("source_chunks", "ix_source_chunks_review_status", ["review_status"])
    source_chunk_uniques = _constraint_names("source_chunks", "unique")
    if "uq_source_chunk_version_logical_id" not in source_chunk_uniques:
        with op.batch_alter_table("source_chunks") as batch:
            if "uq_source_chunk_version_run_index" in source_chunk_uniques:
                batch.drop_constraint("uq_source_chunk_version_run_index", type_="unique")
            batch.create_unique_constraint(
                "uq_source_chunk_version_logical_id", ["source_version_id", "source_chunk_id"]
            )

    if "source_chunk_revisions" not in existing:
        op.create_table(
            "source_chunk_revisions",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("source_chunk_id", sa.String(64), sa.ForeignKey("source_chunks.id"), nullable=False),
            sa.Column("revision_no", sa.Integer(), nullable=False),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("content_hash", sa.String(64), nullable=False),
            sa.Column("anchor_json", sa.JSON(), nullable=False),
            sa.Column("operation", sa.String(32), nullable=False, server_default="prepared"),
            sa.Column("parent_chunk_ids", sa.JSON(), nullable=False),
            sa.Column("actor", sa.String(255), nullable=False, server_default="system"),
            *_timestamps(),
            sa.UniqueConstraint("source_chunk_id", "revision_no", name="uq_source_chunk_revision"),
        )
        op.create_index("ix_source_chunk_revisions_source_chunk_id", "source_chunk_revisions", ["source_chunk_id"])
        op.create_index("ix_source_chunk_revisions_content_hash", "source_chunk_revisions", ["content_hash"])
        op.create_index("ix_source_chunk_revisions_operation", "source_chunk_revisions", ["operation"])

    if "source_review_snapshots" not in existing:
        op.create_table(
            "source_review_snapshots",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("source_version_id", sa.String(64), sa.ForeignKey("source_versions.id"), nullable=False),
            sa.Column("review_no", sa.Integer(), nullable=False),
            sa.Column("content_digest", sa.String(64), nullable=False),
            sa.Column("reviewed_by", sa.String(255), nullable=False),
            sa.Column("approved_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("status", sa.String(32), nullable=False, server_default="approved"),
            *_timestamps(),
            sa.UniqueConstraint("source_version_id", "review_no", name="uq_source_review_number"),
            sa.UniqueConstraint("source_version_id", "content_digest", name="uq_source_review_digest"),
        )
        op.create_index("ix_source_review_snapshots_source_version_id", "source_review_snapshots", ["source_version_id"])
        op.create_index("ix_source_review_snapshots_content_digest", "source_review_snapshots", ["content_digest"])
        op.create_index("ix_source_review_snapshots_status", "source_review_snapshots", ["status"])

    if "source_review_snapshot_chunks" not in existing:
        op.create_table(
            "source_review_snapshot_chunks",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("source_review_snapshot_id", sa.String(64), sa.ForeignKey("source_review_snapshots.id"), nullable=False),
            sa.Column("source_chunk_id", sa.String(64), sa.ForeignKey("source_chunks.id"), nullable=False),
            sa.Column("source_chunk_revision_id", sa.String(64), sa.ForeignKey("source_chunk_revisions.id"), nullable=False),
            sa.Column("ordinal", sa.Integer(), nullable=False),
            sa.Column("content_hash", sa.String(64), nullable=False),
            *_timestamps(),
            sa.UniqueConstraint("source_review_snapshot_id", "ordinal", name="uq_source_review_chunk_ordinal"),
            sa.UniqueConstraint("source_review_snapshot_id", "source_chunk_revision_id", name="uq_source_review_chunk_revision"),
        )
        for column in ("source_review_snapshot_id", "source_chunk_id", "source_chunk_revision_id"):
            op.create_index(f"ix_source_review_snapshot_chunks_{column}", "source_review_snapshot_chunks", [column])

    if "source_preparation_jobs" not in existing:
        op.create_table(
            "source_preparation_jobs",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("source_version_id", sa.String(64), sa.ForeignKey("source_versions.id"), nullable=False),
            sa.Column("preparation_revision", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("execution_snapshot_id", sa.String(64), nullable=True),
            sa.Column("status", sa.String(32), nullable=False, server_default="queued"),
            sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("lease_owner", sa.String(255), nullable=True),
            sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("error", sa.Text(), nullable=True),
            *_timestamps(),
            sa.UniqueConstraint("source_version_id", "preparation_revision", name="uq_source_preparation_revision"),
        )
        for column in ("source_version_id", "execution_snapshot_id", "status", "lease_expires_at"):
            op.create_index(f"ix_source_preparation_jobs_{column}", "source_preparation_jobs", [column])

    if "knowledge_dispatches" not in existing:
        op.create_table(
            "knowledge_dispatches",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("source_review_snapshot_id", sa.String(64), sa.ForeignKey("source_review_snapshots.id"), nullable=False),
            sa.Column("status", sa.String(32), nullable=False, server_default="queued"),
            sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("lease_owner", sa.String(255), nullable=True),
            sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("error", sa.Text(), nullable=True),
            *_timestamps(),
            sa.UniqueConstraint("source_review_snapshot_id", name="uq_knowledge_dispatch_snapshot"),
        )
        for column in ("source_review_snapshot_id", "status", "lease_expires_at"):
            op.create_index(f"ix_knowledge_dispatches_{column}", "knowledge_dispatches", [column])

    for table in ("knowledge_flow_templates", "knowledge_flow_template_revisions"):
        _add_column(table, sa.Column("purpose", sa.String(32), nullable=False, server_default="knowledge"))
        _add_index(table, f"ix_{table}_purpose", ["purpose"])
    _add_column("knowledge_flow_templates", sa.Column("needs_review_upgrade", sa.Boolean(), nullable=False, server_default=sa.false()))
    _add_index("knowledge_flow_templates", "ix_knowledge_flow_templates_needs_review_upgrade", ["needs_review_upgrade"])

    _add_column("document_library_processing_records", sa.Column("source_review_snapshot_id", sa.String(64), nullable=True))
    _add_index("document_library_processing_records", "ix_document_library_processing_records_source_review_snapshot_id", ["source_review_snapshot_id"])
    processing_uniques = _constraint_names("document_library_processing_records", "unique")
    processing_fk_columns = _foreign_key_columns("document_library_processing_records")
    if "uq_doc_processing_review_revision" not in processing_uniques or ("source_review_snapshot_id",) not in processing_fk_columns:
        with op.batch_alter_table("document_library_processing_records") as batch:
            if "uq_doc_processing_revision" in processing_uniques:
                batch.drop_constraint("uq_doc_processing_revision", type_="unique")
            if "uq_doc_processing_review_revision" not in processing_uniques:
                batch.create_unique_constraint(
                    "uq_doc_processing_review_revision",
                    ["document_library_template_binding_id", "source_version_id", "knowledge_flow_template_revision_id", "source_review_snapshot_id"],
                )
            if ("source_review_snapshot_id",) not in processing_fk_columns:
                batch.create_foreign_key(
                    "fk_doc_processing_review_snapshot", "source_review_snapshots", ["source_review_snapshot_id"], ["id"]
                )

    for column in (
        sa.Column("source_chunk_revision_id", sa.String(64), nullable=True),
        sa.Column("source_review_snapshot_id", sa.String(64), nullable=True),
    ):
        _add_column("knowledge_item_sources", column)
        _add_index("knowledge_item_sources", f"ix_knowledge_item_sources_{column.name}", [column.name])
    item_source_fk_columns = _foreign_key_columns("knowledge_item_sources")
    if not {("source_chunk_revision_id",), ("source_review_snapshot_id",)}.issubset(item_source_fk_columns):
        with op.batch_alter_table("knowledge_item_sources") as batch:
            if ("source_chunk_revision_id",) not in item_source_fk_columns:
                batch.create_foreign_key("fk_item_source_chunk_revision", "source_chunk_revisions", ["source_chunk_revision_id"], ["id"])
            if ("source_review_snapshot_id",) not in item_source_fk_columns:
                batch.create_foreign_key("fk_item_source_review_snapshot", "source_review_snapshots", ["source_review_snapshot_id"], ["id"])

    if "knowledge_job_review_inputs" not in existing:
        op.create_table(
            "knowledge_job_review_inputs",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("knowledge_job_id", sa.String(64), sa.ForeignKey("knowledge_jobs.id"), nullable=False),
            sa.Column("source_version_id", sa.String(64), sa.ForeignKey("source_versions.id"), nullable=False),
            sa.Column("source_review_snapshot_id", sa.String(64), sa.ForeignKey("source_review_snapshots.id"), nullable=False),
            sa.Column("review_digest", sa.String(64), nullable=False),
            *_timestamps(),
            sa.UniqueConstraint("knowledge_job_id", "source_version_id", name="uq_job_review_source_version"),
        )
        for column in ("knowledge_job_id", "source_version_id", "source_review_snapshot_id", "review_digest"):
            op.create_index(f"ix_knowledge_job_review_inputs_{column}", "knowledge_job_review_inputs", [column])

    _add_column("flow_runs", sa.Column("source_preparation_job_id", sa.String(64), nullable=True))
    _add_index("flow_runs", "ix_flow_runs_source_preparation_job_id", ["source_preparation_job_id"])
    flow_run_fk_columns = _foreign_key_columns("flow_runs")
    flow_run_checks = _constraint_names("flow_runs", "check")
    knowledge_job_nullable = next(
        item.get("nullable", True) for item in sa.inspect(op.get_bind()).get_columns("flow_runs")
        if item["name"] == "knowledge_job_id"
    )
    if not knowledge_job_nullable or ("source_preparation_job_id",) not in flow_run_fk_columns or "ck_flow_run_single_owner" not in flow_run_checks:
        with op.batch_alter_table("flow_runs") as batch:
            if not knowledge_job_nullable:
                batch.alter_column("knowledge_job_id", existing_type=sa.String(64), nullable=True)
            if ("source_preparation_job_id",) not in flow_run_fk_columns:
                batch.create_foreign_key("fk_flow_run_preparation_job", "source_preparation_jobs", ["source_preparation_job_id"], ["id"])
            if "ck_flow_run_single_owner" not in flow_run_checks:
                batch.create_check_constraint(
                    "ck_flow_run_single_owner",
                    "(knowledge_job_id IS NOT NULL AND source_preparation_job_id IS NULL) OR "
                    "(knowledge_job_id IS NULL AND source_preparation_job_id IS NOT NULL)",
                )

    for column in (
        sa.Column("review_snapshot_digest", sa.String(64), nullable=True),
        sa.Column("review_gate_status", sa.String(32), nullable=False, server_default="pending"),
    ):
        _add_column("knowledge_asset_versions", column)
        _add_index("knowledge_asset_versions", f"ix_knowledge_asset_versions_{column.name}", [column.name])


def downgrade() -> None:
    with op.batch_alter_table("flow_runs") as batch:
        batch.drop_constraint("ck_flow_run_single_owner", type_="check")
        batch.drop_constraint("fk_flow_run_preparation_job", type_="foreignkey")
        batch.alter_column("knowledge_job_id", existing_type=sa.String(64), nullable=False)
    op.drop_index("ix_flow_runs_source_preparation_job_id", table_name="flow_runs")
    op.drop_column("flow_runs", "source_preparation_job_id")

    for column in ("review_gate_status", "review_snapshot_digest"):
        op.drop_index(f"ix_knowledge_asset_versions_{column}", table_name="knowledge_asset_versions")
        op.drop_column("knowledge_asset_versions", column)

    op.drop_table("knowledge_job_review_inputs")
    with op.batch_alter_table("knowledge_item_sources") as batch:
        batch.drop_constraint("fk_item_source_review_snapshot", type_="foreignkey")
        batch.drop_constraint("fk_item_source_chunk_revision", type_="foreignkey")
    for column in ("source_review_snapshot_id", "source_chunk_revision_id"):
        op.drop_index(f"ix_knowledge_item_sources_{column}", table_name="knowledge_item_sources")
        op.drop_column("knowledge_item_sources", column)

    with op.batch_alter_table("document_library_processing_records") as batch:
        batch.drop_constraint("fk_doc_processing_review_snapshot", type_="foreignkey")
        batch.drop_constraint("uq_doc_processing_review_revision", type_="unique")
        batch.create_unique_constraint(
            "uq_doc_processing_revision",
            ["document_library_template_binding_id", "source_version_id", "knowledge_flow_template_revision_id"],
        )
    op.drop_index("ix_document_library_processing_records_source_review_snapshot_id", table_name="document_library_processing_records")
    op.drop_column("document_library_processing_records", "source_review_snapshot_id")

    op.drop_index("ix_knowledge_flow_templates_needs_review_upgrade", table_name="knowledge_flow_templates")
    op.drop_column("knowledge_flow_templates", "needs_review_upgrade")
    for table in ("knowledge_flow_template_revisions", "knowledge_flow_templates"):
        op.drop_index(f"ix_{table}_purpose", table_name=table)
        op.drop_column(table, "purpose")

    for table in (
        "knowledge_dispatches", "source_preparation_jobs", "source_review_snapshot_chunks",
        "source_review_snapshots", "source_chunk_revisions",
    ):
        op.drop_table(table)

    source_chunk_uniques = _constraint_names("source_chunks", "unique")
    with op.batch_alter_table("source_chunks") as batch:
        if "uq_source_chunk_version_logical_id" in source_chunk_uniques:
            batch.drop_constraint("uq_source_chunk_version_logical_id", type_="unique")
        batch.create_unique_constraint(
            "uq_source_chunk_version_run_index", ["source_version_id", "flow_run_id", "chunk_index"]
        )

    for column in ("review_status", "lifecycle_status", "current_revision_id"):
        op.drop_index(f"ix_source_chunks_{column}", table_name="source_chunks")
    for column in ("reviewed_at", "reviewed_by", "review_status", "lifecycle_status", "current_revision_id"):
        op.drop_column("source_chunks", column)

    for column in ("current_review_snapshot_id", "review_status", "preparation_status"):
        op.drop_index(f"ix_source_versions_{column}", table_name="source_versions")
        op.drop_column("source_versions", column)
