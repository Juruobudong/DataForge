"""add deployment-scoped routing and offline migration state.

Revision ID: 20260817_deploy_migration
Revises: 20260815_graph_schema_snapshot
"""
from __future__ import annotations

import hashlib
import json
import os

import sqlalchemy as sa
from alembic import op

from dataforge.v7.models import Base

revision = "20260817_deploy_migration"
down_revision = "20260815_graph_schema_snapshot"
branch_labels = None
depends_on = None


def _columns(inspector, table: str) -> set[str]:
    return {item["name"] for item in inspector.get_columns(table)}


def _unique_names(inspector, table: str) -> set[str]:
    return {str(item.get("name")) for item in inspector.get_unique_constraints(table) if item.get("name")}


def _stable(prefix: str, value: str) -> str:
    return f"{prefix}_{hashlib.sha256(value.encode('utf-8')).hexdigest()[:24]}"


def _create_new_tables(bind) -> None:
    for name in (
        "milvus_targets",
        "deployments",
        "deployment_targets",
        "project_deployments",
        "dataforge_instances",
        "project_deployment_tasks",
        "knowledge_migration_jobs",
        "knowledge_migration_items",
    ):
        Base.metadata.tables[name].create(bind, checkfirst=True)


def _add_asset_origin_columns(inspector) -> None:
    for table in ("document_libraries", "knowledge_libraries"):
        existing = _columns(inspector, table)
        with op.batch_alter_table(table) as batch:
            if "origin_type" not in existing:
                batch.add_column(sa.Column("origin_type", sa.String(32), nullable=False, server_default="local"))
                batch.create_index(f"ix_{table}_origin_type", ["origin_type"])
            if "origin_instance_id" not in existing:
                batch.add_column(sa.Column("origin_instance_id", sa.String(64), nullable=True))
                batch.create_index(f"ix_{table}_origin_instance_id", ["origin_instance_id"])
            if "origin_asset_id" not in existing:
                batch.add_column(sa.Column("origin_asset_id", sa.String(64), nullable=True))
                batch.create_index(f"ix_{table}_origin_asset_id", ["origin_asset_id"])
            if "origin_state" not in existing:
                batch.add_column(sa.Column("origin_state", sa.String(32), nullable=True))
                batch.create_index(f"ix_{table}_origin_state", ["origin_state"])
            if table == "knowledge_libraries" and "migration_status" not in existing:
                batch.add_column(sa.Column("migration_status", sa.String(32), nullable=False, server_default="ready"))
                batch.create_index("ix_knowledge_libraries_migration_status", ["migration_status"])


def _add_task_and_chunk_columns(inspector) -> None:
    existing = _columns(inspector, "project_tasks")
    with op.batch_alter_table("project_tasks") as batch:
        if "knowledge_type" not in existing:
            batch.add_column(sa.Column("knowledge_type", sa.String(32), nullable=True))
            batch.create_index("ix_project_tasks_knowledge_type", ["knowledge_type"])
        if "description" not in existing:
            # MySQL versions used by the deployment environment reject defaults
            # on TEXT columns. Add it nullable first so existing rows remain valid,
            # then backfill and enforce the model's NOT NULL contract below.
            batch.add_column(sa.Column("description", sa.Text(), nullable=True))
    if "description" not in existing:
        op.execute(sa.text("UPDATE project_tasks SET description='' WHERE description IS NULL"))
        with op.batch_alter_table("project_tasks") as batch:
            batch.alter_column(
                "description",
                existing_type=sa.Text(),
                existing_nullable=True,
                nullable=False,
            )
    existing = _columns(inspector, "source_chunks")
    with op.batch_alter_table("source_chunks") as batch:
        if "origin_flow_run_id" not in existing:
            batch.add_column(sa.Column("origin_flow_run_id", sa.String(64), nullable=True))
            batch.create_index("ix_source_chunks_origin_flow_run_id", ["origin_flow_run_id"])
        flow_run = next((item for item in inspector.get_columns("source_chunks") if item["name"] == "flow_run_id"), None)
        if flow_run and not flow_run.get("nullable"):
            batch.alter_column("flow_run_id", existing_type=sa.String(64), nullable=True)


def _seed_control_plane(bind) -> dict[str, str]:
    target_specs = (
        (
            "milvus_dataforge_central_test",
            "DataForge 中心测试 Milvus",
            os.environ.get("DATAFORGE_CENTRAL_TEST_MILVUS_URI") or "http://milvus-central-test:19531",
        ),
        (
            "milvus_dataforge_central_production",
            "DataForge 中心生产 Milvus",
            os.environ.get("DATAFORGE_CENTRAL_PRODUCTION_MILVUS_URI") or "http://milvus-central-production:19531",
        ),
    )
    for target_id, target_name, target_url in target_specs:
        if not bind.execute(sa.text("SELECT 1 FROM milvus_targets WHERE id=:id"), {"id": target_id}).first():
            bind.execute(sa.text(
                "INSERT INTO milvus_targets (id,name,milvus_url,created_at,updated_at) "
                "VALUES (:id,:name,:url,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
            ), {"id": target_id, "name": target_name, "url": target_url})
    deployment_id = "deployment_dataforge_central"
    if not bind.execute(sa.text("SELECT 1 FROM deployments WHERE id=:id"), {"id": deployment_id}).first():
        bind.execute(sa.text(
            "INSERT INTO deployments (id,code,name,scope,institution_name,institution_code,release_stage,status,created_at,updated_at) "
            "VALUES (:id,'dataforge-central','DataForge 中心环境','central',NULL,NULL,'test','active',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
        ), {"id": deployment_id})
    for stage, target_id in (("test", target_specs[0][0]), ("production", target_specs[1][0])):
        row_id = f"deployment_target_dataforge_central_{stage}"
        if not bind.execute(sa.text("SELECT 1 FROM deployment_targets WHERE id=:id"), {"id": row_id}).first():
            bind.execute(sa.text(
                "INSERT INTO deployment_targets "
                "(id,deployment_id,release_stage,target_kind,milvus_target_id,created_at,updated_at) "
                "VALUES (:id,:deployment,:stage,'milvus',:target,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
            ), {"id": row_id, "deployment": deployment_id, "stage": stage, "target": target_id})
    if not bind.execute(sa.text("SELECT 1 FROM dataforge_instances LIMIT 1")).first():
        mode = os.getenv("DATAFORGE_INSTANCE_MODE", "central").strip().lower()
        if mode not in {"central", "local"}:
            mode = "central"
        bind.execute(sa.text(
            "INSERT INTO dataforge_instances (id,instance_code,instance_mode,bound_deployment_id,source_instance_id,created_at) "
            "VALUES (:id,:code,:mode,NULL,NULL,CURRENT_TIMESTAMP)"
        ), {"id": "instance_central_default", "code": os.getenv("DATAFORGE_INSTANCE_CODE", "central-default"), "mode": mode})
    deployments: dict[str, str] = {}
    for project_id, project_code, project_name in bind.execute(sa.text("SELECT id,code,name FROM projects")):
        binding_id = _stable("pdeploy", f"{project_id}|{deployment_id}")
        deployments[str(project_id)] = binding_id
        if not bind.execute(sa.text("SELECT 1 FROM project_deployments WHERE id=:id"), {"id": binding_id}).first():
            bind.execute(sa.text(
                "INSERT INTO project_deployments (id,project_id,deployment_id,status,created_at,updated_at) "
                "VALUES (:id,:project_id,:deployment,'active',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
            ), {"id": binding_id, "project_id": project_id, "deployment": deployment_id})
    return deployments


def _seed_deployment_tasks(bind, deployments: dict[str, str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for task_id, project_id in bind.execute(sa.text("SELECT id,project_id FROM project_tasks")):
        rows = list(bind.execute(sa.text(
            "SELECT DISTINCT kl.knowledge_type, kl.index_profile_id "
            "FROM project_org_routes r "
            "JOIN project_org_route_libraries rl ON rl.project_org_route_id=r.id "
            "JOIN knowledge_libraries kl ON kl.id=rl.knowledge_library_id "
            "WHERE r.project_task_id=:task_id"
        ), {"task_id": task_id}))
        knowledge_types = {str(row[0]) for row in rows if row[0]}
        profile_ids = {str(row[1]) for row in rows if row[1]}
        enabled = len(knowledge_types) == 1 and len(profile_ids) == 1
        knowledge_type = next(iter(knowledge_types)) if len(knowledge_types) == 1 else None
        profile_id = next(iter(profile_ids)) if enabled else None
        bind.execute(sa.text("UPDATE project_tasks SET knowledge_type=:kind WHERE id=:id"),
                     {"kind": knowledge_type, "id": task_id})
        deployment_id = deployments[str(project_id)]
        deployment_task_id = _stable("dtask", str(task_id))
        result[str(task_id)] = deployment_task_id
        if not bind.execute(sa.text("SELECT 1 FROM project_deployment_tasks WHERE id=:id"), {"id": deployment_task_id}).first():
            bind.execute(sa.text(
                "INSERT INTO project_deployment_tasks "
                "(id,project_deployment_id,project_task_id,index_profile_id,qa_embedding_mode,top_k,enabled,created_at,updated_at) "
                "VALUES (:id,:deployment,:task,:profile,:qa_mode,10,:enabled,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
            ), {"id": deployment_task_id, "deployment": deployment_id, "task": task_id,
                "profile": profile_id, "qa_mode": None, "enabled": bool(enabled)})
    return result


def _upgrade_routes(bind, inspector, task_map: dict[str, str], deployments: dict[str, str]) -> None:
    route_columns = _columns(inspector, "project_org_routes")
    route_uniques = _unique_names(inspector, "project_org_routes")
    with op.batch_alter_table("project_org_routes") as batch:
        if "project_deployment_task_id" not in route_columns:
            batch.add_column(sa.Column("project_deployment_task_id", sa.String(64), nullable=True))
        if "org_name" not in route_columns:
            batch.add_column(sa.Column("org_name", sa.String(255), nullable=False, server_default=""))
        if "enabled" not in route_columns:
            batch.add_column(sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()))
    for old_task_id, deployment_task_id in task_map.items():
        bind.execute(sa.text(
            "UPDATE project_org_routes SET project_deployment_task_id=:deployment_task WHERE project_task_id=:task"
        ), {"deployment_task": deployment_task_id, "task": old_task_id})
    inspector = sa.inspect(bind)
    route_uniques = _unique_names(inspector, "project_org_routes")
    old_route_fks = [item.get("name") for item in inspector.get_foreign_keys("project_org_routes")
                     if item.get("constrained_columns") == ["project_task_id"] and item.get("name")]
    old_route_indexes = [item.get("name") for item in inspector.get_indexes("project_org_routes")
                         if item.get("column_names") == ["project_task_id"] and item.get("name")]
    with op.batch_alter_table("project_org_routes") as batch:
        if "uq_task_org_route" in route_uniques:
            batch.drop_constraint("uq_task_org_route", type_="unique")
        if "uq_deploy_task_org_route" not in route_uniques:
            batch.create_unique_constraint("uq_deploy_task_org_route", ["project_deployment_task_id", "org_code"])
        batch.create_foreign_key("fk_route_deployment_task", "project_deployment_tasks", ["project_deployment_task_id"], ["id"])
        batch.create_index("ix_project_org_routes_deployment_task", ["project_deployment_task_id"])
        batch.create_index("ix_project_org_routes_enabled", ["enabled"])
        batch.alter_column("project_deployment_task_id", existing_type=sa.String(64), nullable=False)
        for name in old_route_fks: batch.drop_constraint(name, type_="foreignkey")
        for name in old_route_indexes: batch.drop_index(name)
        batch.drop_column("project_task_id")

    link_columns = _columns(sa.inspect(bind), "project_org_route_libraries")
    with op.batch_alter_table("project_org_route_libraries") as batch:
        if "priority" not in link_columns:
            batch.add_column(sa.Column("priority", sa.Integer(), nullable=False, server_default="0"))
        if "enabled" not in link_columns:
            batch.add_column(sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()))
            batch.create_index("ix_project_org_route_libraries_enabled", ["enabled"])

    version_columns = _columns(sa.inspect(bind), "project_route_versions")
    with op.batch_alter_table("project_route_versions") as batch:
        if "project_deployment_id" not in version_columns:
            batch.add_column(sa.Column("project_deployment_id", sa.String(64), nullable=True))
        if "origin" not in version_columns:
            batch.add_column(sa.Column("origin", sa.String(32), nullable=False, server_default="central"))
    for project_id, deployment_id in deployments.items():
        bind.execute(sa.text(
            "UPDATE project_route_versions SET project_deployment_id=:deployment WHERE project_id=:project"
        ), {"deployment": deployment_id, "project": project_id})
    for row in bind.execute(sa.text("SELECT id,project_id,snapshot_json FROM project_route_versions")):
        raw = row[2]
        snapshot = json.loads(raw) if isinstance(raw, str) else dict(raw or {})
        deployment_id = deployments.get(str(row[1]))
        if deployment_id and "deployment" not in snapshot:
            snapshot["schema_version"] = 2
            snapshot["deployment"] = {"id": deployment_id}
            snapshot["milvus_target"] = {"id": "milvus_default"}
            bind.execute(sa.text("UPDATE project_route_versions SET snapshot_json=:snapshot WHERE id=:id"),
                         {"snapshot": json.dumps(snapshot, ensure_ascii=False), "id": row[0]})
    inspector = sa.inspect(bind)
    version_uniques = _unique_names(inspector, "project_route_versions")
    with op.batch_alter_table("project_route_versions") as batch:
        if "uq_project_route_version" in version_uniques:
            batch.drop_constraint("uq_project_route_version", type_="unique")
        if "uq_deploy_route_version" not in version_uniques:
            batch.create_unique_constraint("uq_deploy_route_version", ["project_deployment_id", "version_no"])
        batch.create_foreign_key("fk_route_version_deployment", "project_deployments", ["project_deployment_id"], ["id"])
        batch.create_index("ix_project_route_versions_deployment", ["project_deployment_id"])
        batch.create_index("ix_project_route_versions_origin", ["origin"])
        batch.alter_column("project_deployment_id", existing_type=sa.String(64), nullable=False)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    _add_asset_origin_columns(inspector)
    inspector = sa.inspect(bind)
    _add_task_and_chunk_columns(inspector)
    _create_new_tables(bind)
    inspector = sa.inspect(bind)
    deployments = _seed_control_plane(bind)
    # A fresh database was created from current metadata and already has the new route shape.
    if "project_task_id" not in _columns(inspector, "project_org_routes"):
        return
    task_map = _seed_deployment_tasks(bind, deployments)
    _upgrade_routes(bind, inspector, task_map, deployments)


def downgrade() -> None:
    raise RuntimeError("V7 schema downgrade is intentionally disabled; use a new empty database")
