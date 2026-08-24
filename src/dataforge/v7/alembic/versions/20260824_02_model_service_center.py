"""add persistent LLM and embedding model services.

Revision ID: 20260824_model_services
Revises: 20260824_component_observe
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260824_model_services"
down_revision = "20260824_component_observe"
branch_labels = None
depends_on = None


def _serving_columns(kind: str) -> list[sa.Column]:
    type_name = "serving_type" if kind == "model" else "provider_type"
    columns = [
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("serving_code", sa.String(64), nullable=False, unique=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column(type_name, sa.String(64), nullable=False),
        sa.Column("model_name", sa.String(255), nullable=False),
        sa.Column("base_url", sa.String(1024), nullable=True),
        sa.Column("credential_ciphertext", sa.Text(), nullable=True),
        sa.Column("credential_configured", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("credential_key_version", sa.String(32), nullable=True),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False, server_default="120"),
        sa.Column("max_retries", sa.Integer(), nullable=False, server_default="2"),
    ]
    if kind == "model":
        columns.extend([
            sa.Column("max_tokens", sa.Integer(), nullable=False, server_default="16384"),
            sa.Column("disable_thinking", sa.Boolean(), nullable=False, server_default=sa.true()),
        ])
    else:
        columns.extend([
            sa.Column("dimension", sa.Integer(), nullable=False),
            sa.Column("batch_size", sa.Integer(), nullable=False, server_default="32"),
        ])
    columns.extend([
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("last_check_status", sa.String(64), nullable=False, server_default="pending_configuration"),
        sa.Column("last_check_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_check_latency_ms", sa.Integer(), nullable=True),
    ])
    if kind == "embedding":
        columns.append(sa.Column("last_observed_dimension", sa.Integer(), nullable=True))
    columns.extend([
        sa.Column("last_check_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ])
    return columns


def upgrade() -> None:
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    if "model_servings" not in existing:
        op.create_table("model_servings", *_serving_columns("model"))
        op.create_index("ix_model_servings_is_enabled", "model_servings", ["is_enabled"])
        op.create_index("ix_model_servings_is_default", "model_servings", ["is_default"])
    if "embedding_servings" not in existing:
        op.create_table("embedding_servings", *_serving_columns("embedding"))
        op.create_index("ix_embedding_servings_is_enabled", "embedding_servings", ["is_enabled"])
        op.create_index("ix_embedding_servings_is_default", "embedding_servings", ["is_default"])

    inspector = sa.inspect(op.get_bind())
    additions = {
        "knowledge_index_profiles": [
            sa.Column("embedding_serving_id", sa.String(64), nullable=True),
            sa.Column("embedding_input", sa.String(32), nullable=False, server_default="canonical_content"),
        ],
        "knowledge_index_profile_revisions": [
            sa.Column("embedding_serving_id", sa.String(64), nullable=True),
            sa.Column("embedding_input", sa.String(32), nullable=False, server_default="canonical_content"),
        ],
        "knowledge_asset_versions": [
            sa.Column("embedding_serving_id", sa.String(64), nullable=True),
            sa.Column("embedding_model", sa.String(255), nullable=True),
            sa.Column("embedding_dimension", sa.Integer(), nullable=True),
        ],
    }
    for table, columns in additions.items():
        current = {item["name"] for item in inspector.get_columns(table)}
        for column in columns:
            if column.name not in current:
                op.add_column(table, column)
        index_name = f"ix_{table}_embedding_serving_id"
        current_indexes = {item["name"] for item in inspector.get_indexes(table)}
        if index_name not in current_indexes:
            op.create_index(index_name, table, ["embedding_serving_id"])


def downgrade() -> None:
    for table, columns in (
        ("knowledge_asset_versions", ("embedding_dimension", "embedding_model", "embedding_serving_id")),
        ("knowledge_index_profile_revisions", ("embedding_input", "embedding_serving_id")),
        ("knowledge_index_profiles", ("embedding_input", "embedding_serving_id")),
    ):
        op.drop_index(f"ix_{table}_embedding_serving_id", table_name=table)
        for column in columns:
            op.drop_column(table, column)
    op.drop_table("embedding_servings")
    op.drop_table("model_servings")
