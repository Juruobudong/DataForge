"""add flow development workbench metadata and runtime diagnostics.

Revision ID: 20260814_flow_workbench
Revises: 20260814_operator_examples
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260814_flow_workbench"
down_revision = "20260814_operator_examples"
branch_labels = None
depends_on = None


def _add(table: str, inspector, *columns: sa.Column) -> None:
    existing = {item["name"] for item in inspector.get_columns(table)}
    for column in columns:
        if column.name not in existing:
            op.add_column(table, column)


def upgrade() -> None:
    bind = op.get_bind(); inspector = sa.inspect(bind)
    _add("operator_definitions", inspector,
         sa.Column("display_name_zh", sa.String(255), nullable=False, server_default=""),
         sa.Column("subcategory", sa.String(64), nullable=False, server_default=""),
         sa.Column("summary", sa.Text(), nullable=False, server_default=""),
         sa.Column("scenarios", sa.JSON(), nullable=False, server_default="[]"),
         sa.Column("knowledge_types", sa.JSON(), nullable=False, server_default="[]"),
         sa.Column("recommended_predecessors", sa.JSON(), nullable=False, server_default="[]"),
         sa.Column("recommended_successors", sa.JSON(), nullable=False, server_default="[]"),
         sa.Column("lifecycle_status", sa.String(32), nullable=False, server_default="published"))
    _add("operator_versions", inspector, sa.Column("parameter_docs", sa.JSON(), nullable=False, server_default="{}"))
    _add("flow_subgraph_revisions", inspector,
         sa.Column("description", sa.Text(), nullable=False, server_default=""),
         sa.Column("input_contract", sa.JSON(), nullable=False, server_default="{}"),
         sa.Column("output_contract", sa.JSON(), nullable=False, server_default="{}"))
    _add("flow_runs", inspector,
         sa.Column("parent_flow_run_id", sa.String(64), nullable=True),
         sa.Column("run_mode", sa.String(32), nullable=False, server_default="full"),
         sa.Column("start_node_id", sa.String(255), nullable=True),
         sa.Column("parameter_overrides", sa.JSON(), nullable=False, server_default="{}"),
         sa.Column("sink_policy", sa.String(32), nullable=False, server_default="commit"),
         sa.Column("requested_by", sa.String(255), nullable=False, server_default="system"),
         sa.Column("idempotency_key", sa.String(120), nullable=True),
         sa.Column("cancellation_requested_at", sa.DateTime(timezone=True), nullable=True),
         sa.Column("committed_at", sa.DateTime(timezone=True), nullable=True))
    _add("flow_node_runs", inspector,
         sa.Column("operator_code", sa.String(120), nullable=True),
         sa.Column("operator_version", sa.Integer(), nullable=True),
         sa.Column("resolved_parameters", sa.JSON(), nullable=False, server_default="{}"),
         sa.Column("logs_json", sa.JSON(), nullable=False, server_default="[]"),
         sa.Column("metrics_json", sa.JSON(), nullable=False, server_default="{}"),
         sa.Column("error_json", sa.JSON(), nullable=False, server_default="{}"),
         sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
         sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
         sa.Column("duration_ms", sa.Integer(), nullable=True))
    _add("artifacts", inspector,
         sa.Column("summary_json", sa.JSON(), nullable=False, server_default="{}"),
         sa.Column("record_count", sa.Integer(), nullable=False, server_default="0"),
         sa.Column("content_format", sa.String(32), nullable=False, server_default="json"),
         sa.Column("replayable", sa.Boolean(), nullable=False, server_default=sa.false()))

    tables = set(inspector.get_table_names())
    if "flow_node_artifact_bindings" not in tables:
        op.create_table("flow_node_artifact_bindings",
        sa.Column("id", sa.String(64), primary_key=True), sa.Column("flow_node_run_id", sa.String(64), sa.ForeignKey("flow_node_runs.id"), nullable=False),
        sa.Column("artifact_id", sa.String(64), sa.ForeignKey("artifacts.id"), nullable=False), sa.Column("direction", sa.String(16), nullable=False),
        sa.Column("port_name", sa.String(120), nullable=False, server_default="input"), sa.Column("ordinal", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reused", sa.Boolean(), nullable=False, server_default=sa.false()), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False), sa.UniqueConstraint("flow_node_run_id", "direction", "port_name", "ordinal", name="uq_node_artifact_port"))
        op.create_index("ix_node_artifact_binding_node", "flow_node_artifact_bindings", ["flow_node_run_id"])
        op.create_index("ix_node_artifact_binding_artifact", "flow_node_artifact_bindings", ["artifact_id"])
    if "flow_run_events" not in tables:
        op.create_table("flow_run_events",
        sa.Column("id", sa.String(64), primary_key=True), sa.Column("flow_run_id", sa.String(64), sa.ForeignKey("flow_runs.id"), nullable=False),
        sa.Column("sequence_no", sa.Integer(), nullable=False), sa.Column("level", sa.String(16), nullable=False), sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("node_id", sa.String(255), nullable=True), sa.Column("message", sa.Text(), nullable=False), sa.Column("payload_json", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.UniqueConstraint("flow_run_id", "sequence_no", name="uq_flow_run_event_sequence"))
        op.create_index("ix_flow_run_events_run", "flow_run_events", ["flow_run_id"])
    if "flow_run_sink_previews" not in tables:
        op.create_table("flow_run_sink_previews",
        sa.Column("id", sa.String(64), primary_key=True), sa.Column("flow_run_id", sa.String(64), sa.ForeignKey("flow_runs.id"), nullable=False),
        sa.Column("output_key", sa.String(64), nullable=False), sa.Column("knowledge_library_id", sa.String(64), sa.ForeignKey("knowledge_libraries.id"), nullable=False),
        sa.Column("candidates_json", sa.JSON(), nullable=False), sa.Column("successful_chunks_json", sa.JSON(), nullable=False), sa.Column("diff_json", sa.JSON(), nullable=False), sa.Column("quality_json", sa.JSON(), nullable=False),
        sa.Column("base_state_hash", sa.String(64), nullable=False), sa.Column("preview_checksum", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False), sa.Column("idempotency_key", sa.String(120), nullable=True),
        sa.Column("committed_at", sa.DateTime(timezone=True), nullable=True), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False), sa.UniqueConstraint("flow_run_id", "output_key", name="uq_flow_run_sink_preview"))
        op.create_index("ix_flow_run_sink_previews_run", "flow_run_sink_previews", ["flow_run_id"])
    if bind.dialect.name != "sqlite" and "parent_flow_run_id" not in {fk.get("constrained_columns", [None])[0] for fk in inspector.get_foreign_keys("flow_runs")}:
        op.create_foreign_key("fk_flow_runs_parent", "flow_runs", "flow_runs", ["parent_flow_run_id"], ["id"])
    if bind.dialect.name != "sqlite" and "uq_derived_run_request" not in {item.get("name") for item in inspector.get_unique_constraints("flow_runs")}:
        op.create_unique_constraint("uq_derived_run_request", "flow_runs", ["parent_flow_run_id", "idempotency_key"])


def downgrade() -> None:
    raise RuntimeError("DataForge V7 migrations are forward-only")
