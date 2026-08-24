"""add component heartbeat and manual check state.

Revision ID: 20260824_component_observe
Revises: 20260823_vector_inventory
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260824_component_observe"
down_revision = "20260823_vector_inventory"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    if "component_heartbeats" not in existing:
        op.create_table(
        "component_heartbeats",
        sa.Column("component", sa.String(32), primary_key=True),
        sa.Column("instance_id", sa.String(128), primary_key=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.String(64), nullable=True),
        sa.Column("worker_id", sa.String(128), nullable=True),
        sa.Column("current_job_id", sa.String(64), nullable=True),
        sa.Column("details_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_component_heartbeats_status", "component_heartbeats", ["status"])
        op.create_index("ix_component_heartbeats_last_seen_at", "component_heartbeats", ["last_seen_at"])
    if "component_check_runs" not in existing:
        op.create_table(
        "component_check_runs",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("selected_components", sa.JSON(), nullable=False),
        sa.Column("requested_by", sa.String(64), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_component_check_runs_status", "component_check_runs", ["status"])
    if "component_check_results" not in existing:
        op.create_table(
        "component_check_results",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("check_run_id", sa.String(64), sa.ForeignKey("component_check_runs.id"), nullable=False),
        sa.Column("component", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("summary", sa.String(500), nullable=False),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("details_json", sa.JSON(), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("check_run_id", "component", name="uq_component_check_run_item"),
        )
        op.create_index("ix_component_check_results_check_run_id", "component_check_results", ["check_run_id"])
        op.create_index("ix_component_check_results_component", "component_check_results", ["component"])


def downgrade() -> None:
    op.drop_table("component_check_results")
    op.drop_table("component_check_runs")
    op.drop_table("component_heartbeats")
