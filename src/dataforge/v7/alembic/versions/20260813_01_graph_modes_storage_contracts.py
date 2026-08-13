"""add graph modes, storage contracts and managed collections.

Revision ID: 20260813_graph_storage_contracts
Revises: 20260812_chunk_generation_result
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from dataforge.v7.models import Base


revision = "20260813_graph_storage_contracts"
down_revision = "20260812_chunk_generation_result"
branch_labels = None
depends_on = None


def _columns(inspector, table: str) -> set[str]:
    return {item["name"] for item in inspector.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "graph_mode" not in _columns(inspector, "knowledge_libraries"):
        with op.batch_alter_table("knowledge_libraries") as batch:
            batch.add_column(sa.Column("graph_mode", sa.String(32), nullable=True))
            batch.create_index("ix_knowledge_libraries_graph_mode", ["graph_mode"])
        bind.execute(sa.text("UPDATE knowledge_libraries SET graph_mode='triple' WHERE knowledge_type='graph'"))
    output_columns = _columns(inspector, "document_library_template_outputs")
    if "output_key" not in output_columns:
        with op.batch_alter_table("document_library_template_outputs") as batch:
            batch.add_column(sa.Column("output_key", sa.String(64), nullable=False, server_default=""))
            batch.add_column(sa.Column("graph_mode", sa.String(32), nullable=True))
        bind.execute(sa.text("UPDATE document_library_template_outputs SET output_key=CASE WHEN knowledge_type='graph' THEN 'graph:triple' ELSE knowledge_type END"))
        inspector = sa.inspect(bind)
        unique_names = {item.get("name") for item in inspector.get_unique_constraints("document_library_template_outputs")}
        with op.batch_alter_table("document_library_template_outputs") as batch:
            if "uq_doc_binding_output_type" in unique_names:
                batch.drop_constraint("uq_doc_binding_output_type", type_="unique")
            batch.create_unique_constraint("uq_doc_binding_output_key", ["document_library_template_binding_id", "output_key"])
            batch.create_index("ix_document_library_template_outputs_output_key", ["output_key"])
            batch.create_index("ix_document_library_template_outputs_graph_mode", ["graph_mode"])
    # Create the referenced contract tables before adding the relation to an
    # already-existing profile revision table. Empty deployments may already
    # have the complete current metadata because earlier V7 revisions use
    # create_all(checkfirst=True).
    Base.metadata.create_all(bind=bind, checkfirst=True)
    profile_columns = _columns(sa.inspect(bind), "knowledge_index_profile_revisions")
    if "storage_contract_revision_id" not in profile_columns:
        with op.batch_alter_table("knowledge_index_profile_revisions") as batch:
            batch.add_column(sa.Column("storage_contract_revision_id", sa.String(64), nullable=True))
            batch.add_column(sa.Column("collection_policy", sa.String(32), nullable=False, server_default="external"))
            batch.create_foreign_key("fk_index_profile_storage_contract", "storage_contract_revisions",
                                     ["storage_contract_revision_id"], ["id"])
            batch.create_index("ix_index_profile_storage_contract", ["storage_contract_revision_id"])


def downgrade() -> None:
    raise RuntimeError("V7 schema downgrade is intentionally disabled; use a new empty database")
