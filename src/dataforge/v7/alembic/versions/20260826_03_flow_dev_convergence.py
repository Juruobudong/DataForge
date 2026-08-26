"""extend debug snapshots for builtin samples and virtual sink baselines.

Revision ID: 20260826_flow_dev_convergence
Revises: 20260826_debug_execution_sandbox
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260826_flow_dev_convergence"
down_revision = "20260826_debug_execution_sandbox"
branch_labels = None
depends_on = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _columns(table: str) -> set[str]:
    return {item["name"] for item in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if "debug_run_input_snapshots" in _tables():
        columns = _columns("debug_run_input_snapshots")
        with op.batch_alter_table("debug_run_input_snapshots") as batch:
            if "input_source" not in columns:
                batch.add_column(sa.Column("input_source", sa.String(32), nullable=False,
                                           server_default="source_review_snapshot"))
                batch.create_index("ix_debug_input_source", ["input_source"])
            if "input_descriptor_json" not in columns:
                batch.add_column(sa.Column("input_descriptor_json", sa.JSON(), nullable=False, server_default="{}"))
            if "resolved_chunks_json" not in columns:
                batch.add_column(sa.Column("resolved_chunks_json", sa.JSON(), nullable=False, server_default="[]"))
            if "input_digest" not in columns:
                batch.add_column(sa.Column("input_digest", sa.String(64), nullable=False, server_default=""))
                batch.create_index("ix_debug_input_digest", ["input_digest"])
            if "sink_preview_targets_json" not in columns:
                batch.add_column(sa.Column("sink_preview_targets_json", sa.JSON(), nullable=False, server_default="{}"))

    if "flow_run_sink_previews" in _tables():
        columns = _columns("flow_run_sink_previews")
        with op.batch_alter_table("flow_run_sink_previews") as batch:
            if "baseline_kind" not in columns:
                batch.add_column(sa.Column("baseline_kind", sa.String(32), nullable=False,
                                           server_default="knowledge_library"))
                batch.create_index("ix_flow_sink_preview_baseline", ["baseline_kind"])
            batch.alter_column("knowledge_library_id", existing_type=sa.String(64), nullable=True)


def downgrade() -> None:
    if "flow_run_sink_previews" in _tables():
        columns = _columns("flow_run_sink_previews")
        with op.batch_alter_table("flow_run_sink_previews") as batch:
            batch.alter_column("knowledge_library_id", existing_type=sa.String(64), nullable=False)
            if "baseline_kind" in columns:
                batch.drop_index("ix_flow_sink_preview_baseline")
                batch.drop_column("baseline_kind")
    if "debug_run_input_snapshots" in _tables():
        columns = _columns("debug_run_input_snapshots")
        with op.batch_alter_table("debug_run_input_snapshots") as batch:
            for index_name, column in (
                ("ix_debug_input_digest", "input_digest"),
                ("ix_debug_input_source", "input_source"),
            ):
                if column in columns:
                    batch.drop_index(index_name)
            for column in ("sink_preview_targets_json", "input_digest", "resolved_chunks_json",
                           "input_descriptor_json", "input_source"):
                if column in columns:
                    batch.drop_column(column)
