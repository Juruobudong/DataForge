"""add preview-only debug execution sandbox persistence.

Revision ID: 20260826_debug_execution_sandbox
Revises: 20260826_artifact_run_order
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260826_debug_execution_sandbox"
down_revision = "20260826_artifact_run_order"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {item["name"] for item in sa.inspect(op.get_bind()).get_columns(table)}


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _constraints(table: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    values = {item.get("name") for item in inspector.get_check_constraints(table)}
    values.update(item.get("name") for item in inspector.get_foreign_keys(table))
    return {str(value) for value in values if value}


def upgrade() -> None:
    required = {"knowledge_flow_templates", "knowledge_flow_template_revisions", "flow_execution_snapshots", "flow_runs"}
    if not required.issubset(_tables()):
        return
    template_columns = _columns("knowledge_flow_templates")
    with op.batch_alter_table("knowledge_flow_templates") as batch:
        if "description" not in template_columns:
            batch.add_column(sa.Column("description", sa.Text(), nullable=False, server_default=""))
        if "derived_from_template_id" not in template_columns:
            batch.add_column(sa.Column("derived_from_template_id", sa.String(64), nullable=True))
            batch.create_index("ix_knowledge_flow_templates_derived_from_template_id", ["derived_from_template_id"])
        if "derived_from_revision_id" not in template_columns:
            batch.add_column(sa.Column("derived_from_revision_id", sa.String(64), nullable=True))
            batch.create_index("ix_knowledge_flow_templates_derived_from_revision_id", ["derived_from_revision_id"])

    if "debug_run_input_snapshots" not in _tables():
        op.create_table(
            "debug_run_input_snapshots",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("knowledge_flow_template_id", sa.String(64), nullable=False),
        sa.Column("knowledge_flow_template_revision_id", sa.String(64), nullable=False),
        sa.Column("execution_snapshot_id", sa.String(64), nullable=False),
        sa.Column("authoring_mode", sa.String(32), nullable=False),
        sa.Column("source_definition_json", sa.JSON(), nullable=False),
        sa.Column("source_definition_checksum", sa.String(64), nullable=False),
        sa.Column("output_types_json", sa.JSON(), nullable=False),
        sa.Column("reusable_node_map_json", sa.JSON(), nullable=False),
        sa.Column("sink_library_bindings_json", sa.JSON(), nullable=False),
        sa.Column("requested_by", sa.String(255), nullable=False),
        sa.Column("idempotency_key", sa.String(120), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["knowledge_flow_template_id"], ["knowledge_flow_templates.id"]),
        sa.ForeignKeyConstraint(["knowledge_flow_template_revision_id"], ["knowledge_flow_template_revisions.id"]),
        sa.ForeignKeyConstraint(["execution_snapshot_id"], ["flow_execution_snapshots.id"]),
        )
        op.create_index("ix_debug_input_template", "debug_run_input_snapshots", ["knowledge_flow_template_id"])
        op.create_index("ix_debug_input_revision", "debug_run_input_snapshots", ["knowledge_flow_template_revision_id"])
        op.create_index("ix_debug_input_execution", "debug_run_input_snapshots", ["execution_snapshot_id"])
        op.create_index("ix_debug_input_definition_checksum", "debug_run_input_snapshots", ["source_definition_checksum"])

    if "debug_run_review_inputs" not in _tables():
        op.create_table(
            "debug_run_review_inputs",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("debug_input_snapshot_id", sa.String(64), nullable=False),
        sa.Column("source_version_id", sa.String(64), nullable=False),
        sa.Column("source_review_snapshot_id", sa.String(64), nullable=False),
        sa.Column("review_digest", sa.String(64), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["debug_input_snapshot_id"], ["debug_run_input_snapshots.id"]),
        sa.ForeignKeyConstraint(["source_version_id"], ["source_versions.id"]),
        sa.ForeignKeyConstraint(["source_review_snapshot_id"], ["source_review_snapshots.id"]),
        sa.UniqueConstraint("debug_input_snapshot_id", "source_version_id", name="uq_debug_review_source_version"),
        sa.UniqueConstraint("debug_input_snapshot_id", "ordinal", name="uq_debug_review_ordinal"),
        )
        op.create_index("ix_debug_review_input", "debug_run_review_inputs", ["debug_input_snapshot_id"])
        op.create_index("ix_debug_review_version", "debug_run_review_inputs", ["source_version_id"])
        op.create_index("ix_debug_review_snapshot", "debug_run_review_inputs", ["source_review_snapshot_id"])

    flow_columns = _columns("flow_runs")
    constraints = _constraints("flow_runs")
    with op.batch_alter_table("flow_runs") as batch:
        if "ck_flow_run_single_owner" in constraints:
            batch.drop_constraint("ck_flow_run_single_owner", type_="check")
        if "debug_input_snapshot_id" not in flow_columns:
            batch.add_column(sa.Column("debug_input_snapshot_id", sa.String(64), nullable=True))
            batch.create_index("ix_flow_runs_debug_input_snapshot_id", ["debug_input_snapshot_id"])
            batch.create_foreign_key(
                "fk_flow_run_debug_input", "debug_run_input_snapshots",
                ["debug_input_snapshot_id"], ["id"],
            )
        batch.create_check_constraint(
            "ck_flow_run_single_owner",
            "(knowledge_job_id IS NOT NULL AND source_preparation_job_id IS NULL AND debug_input_snapshot_id IS NULL) OR "
            "(knowledge_job_id IS NULL AND source_preparation_job_id IS NOT NULL AND debug_input_snapshot_id IS NULL) OR "
            "(knowledge_job_id IS NULL AND source_preparation_job_id IS NULL AND debug_input_snapshot_id IS NOT NULL)",
        )

    if "debug_run_flow_materializations" not in _tables():
        op.create_table(
            "debug_run_flow_materializations",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("flow_run_id", sa.String(64), nullable=False),
            sa.Column("action", sa.String(32), nullable=False),
            sa.Column("idempotency_key", sa.String(120), nullable=False, unique=True),
            sa.Column("target_template_id", sa.String(64), nullable=False),
            sa.Column("target_revision_id", sa.String(64), nullable=False),
            sa.Column("result_json", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["flow_run_id"], ["flow_runs.id"]),
        )
        op.create_index("ix_debug_materialization_run", "debug_run_flow_materializations", ["flow_run_id"])
        op.create_index("ix_debug_materialization_action", "debug_run_flow_materializations", ["action"])
        op.create_index("ix_debug_materialization_target", "debug_run_flow_materializations", ["target_template_id"])


def downgrade() -> None:
    if not {"knowledge_flow_templates", "flow_runs"}.issubset(_tables()):
        return
    if "debug_run_flow_materializations" in _tables():
        op.drop_table("debug_run_flow_materializations")
    with op.batch_alter_table("flow_runs") as batch:
        batch.drop_constraint("ck_flow_run_single_owner", type_="check")
        batch.drop_constraint("fk_flow_run_debug_input", type_="foreignkey")
        batch.drop_index("ix_flow_runs_debug_input_snapshot_id")
        batch.drop_column("debug_input_snapshot_id")
        batch.create_check_constraint(
            "ck_flow_run_single_owner",
            "(knowledge_job_id IS NOT NULL AND source_preparation_job_id IS NULL) OR "
            "(knowledge_job_id IS NULL AND source_preparation_job_id IS NOT NULL)",
        )
    op.drop_table("debug_run_review_inputs")
    op.drop_table("debug_run_input_snapshots")
    with op.batch_alter_table("knowledge_flow_templates") as batch:
        batch.drop_index("ix_knowledge_flow_templates_derived_from_revision_id")
        batch.drop_index("ix_knowledge_flow_templates_derived_from_template_id")
        batch.drop_column("derived_from_revision_id")
        batch.drop_column("derived_from_template_id")
        batch.drop_column("description")
