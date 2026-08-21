"""add stage-scoped routing for shared deployments.

Revision ID: 20260817_qa_agent_route
Revises: 20260817_deploy_migration
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260817_qa_agent_route"
down_revision = "20260817_deploy_migration"
branch_labels = None
depends_on = None


def _columns(inspector, table: str) -> set[str]:
    return {item["name"] for item in inspector.get_columns(table)}


def _unique_names(inspector, table: str) -> set[str]:
    return {
        str(item.get("name"))
        for item in inspector.get_unique_constraints(table)
        if item.get("name")
    }


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    route_columns = _columns(sa.inspect(bind), "project_route_versions")
    route_unique = _unique_names(sa.inspect(bind), "project_route_versions")
    with op.batch_alter_table("project_route_versions") as batch:
        if "release_stage" not in route_columns:
            batch.add_column(
                sa.Column(
                    "release_stage",
                    sa.String(length=16),
                    nullable=False,
                    server_default="test",
                )
            )
            batch.create_index(
                "ix_project_route_versions_release_stage", ["release_stage"]
            )
        if "uq_deploy_route_version" in route_unique:
            batch.drop_constraint("uq_deploy_route_version", type_="unique")
        if "uq_deploy_stage_route_version" not in route_unique:
            batch.create_unique_constraint(
                "uq_deploy_stage_route_version",
                ["project_deployment_id", "release_stage", "version_no"],
            )

    bind.execute(
        sa.text(
            "UPDATE project_route_versions SET release_stage='test' WHERE release_stage IS NULL OR release_stage=''"
        )
    )


def downgrade() -> None:
    raise RuntimeError("DataForge V7 不支持降级")
