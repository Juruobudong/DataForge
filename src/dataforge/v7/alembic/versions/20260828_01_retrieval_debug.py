"""Reranker services, retrieval settings and immutable asset items."""
from alembic import op
import sqlalchemy as sa

revision = "20260828_retrieval_debug"
down_revision = "20260827_curated_operators"
branch_labels = None
depends_on = None


def upgrade():
    # Earlier empty-database bootstrap revisions can already create current models.
    from dataforge.v7.models import RerankerServing, KnowledgeAssetItem
    bind = op.get_bind()
    RerankerServing.__table__.create(bind, checkfirst=True)
    KnowledgeAssetItem.__table__.create(bind, checkfirst=True)
    columns = {column["name"] for column in sa.inspect(bind).get_columns("project_deployment_tasks")}
    if "final_top_k" not in columns:
        op.add_column("project_deployment_tasks", sa.Column("final_top_k", sa.Integer(), nullable=False, server_default="5"))
    if "reranker_serving_code" not in columns:
        op.add_column("project_deployment_tasks", sa.Column("reranker_serving_code", sa.String(64), nullable=True))
        op.create_index("ix_project_deployment_tasks_reranker_serving_code", "project_deployment_tasks", ["reranker_serving_code"])


def downgrade():
    op.drop_index("ix_project_deployment_tasks_reranker_serving_code", table_name="project_deployment_tasks")
    op.drop_column("project_deployment_tasks", "reranker_serving_code")
    op.drop_column("project_deployment_tasks", "final_top_k")
    op.drop_table("knowledge_asset_items")
    op.drop_table("reranker_servings")
