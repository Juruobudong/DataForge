"""Deployment-scoped, deterministic migration planning."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy import func, select

from ..models import (
    KnowledgeIndexProfile,
    KnowledgeIndexProfileRevision,
    KnowledgeItem,
    KnowledgeLibrary,
    Project,
    Deployment,
    DeploymentTarget,
    MilvusTarget,
    ProjectDeployment,
    ProjectDeploymentTask,
    ProjectOrgRoute,
    ProjectOrgRouteLibrary,
    ProjectRouteVersion,
    ProjectTask,
)
from ..store import V7Store
from .dependency import resolve_dependencies


class MigrationPlanner:
    def __init__(self, store: V7Store):
        self.store = store

    def plan(self, project_deployment_id: str, knowledge_library_ids: list[str] | None = None,
             *, include_full_document_library: bool = False, package_kind: str = "deployment_seed") -> dict[str, Any]:
        if package_kind not in {"deployment_seed", "knowledge_update"}:
            raise ValueError("package_kind 无效")
        with self.store.sessions() as session:
            project_deployment = session.get(ProjectDeployment, project_deployment_id)
            if not project_deployment: raise ValueError("ProjectDeployment 不存在")
            deployment = session.get(Deployment, project_deployment.deployment_id)
            project = session.get(Project, project_deployment.project_id)
            if not deployment or not project: raise ValueError("Deployment 或 Project 不存在")
            if deployment.scope != "institution":
                raise ValueError("迁移包目标 Deployment 必须是手动配置的目标机构，不能使用 DataForge 中心环境")
            deployment_tasks = list(session.scalars(select(ProjectDeploymentTask).where(
                ProjectDeploymentTask.project_deployment_id == project_deployment.id,
                ProjectDeploymentTask.enabled.is_(True),
            )))
            task_by_id = {task.id: task for task in deployment_tasks}
            project_tasks = {task.id: task for task in session.scalars(select(ProjectTask).where(
                ProjectTask.id.in_([item.project_task_id for item in deployment_tasks])
            ))}
            routes = list(session.scalars(select(ProjectOrgRoute).where(
                ProjectOrgRoute.project_deployment_task_id.in_(task_by_id), ProjectOrgRoute.enabled.is_(True)
            ))) if task_by_id else []
            route_by_id = {route.id: route for route in routes}
            links = list(session.scalars(select(ProjectOrgRouteLibrary).where(
                ProjectOrgRouteLibrary.project_org_route_id.in_(route_by_id), ProjectOrgRouteLibrary.enabled.is_(True)
            ))) if route_by_id else []
            authorized_ids = {link.knowledge_library_id for link in links}
            selected_ids = set(knowledge_library_ids or authorized_ids)
            if not selected_ids:
                raise ValueError("迁移范围至少包含一个知识库")
            libraries = list(session.scalars(select(KnowledgeLibrary).where(KnowledgeLibrary.id.in_(selected_ids))))
            if {library.id for library in libraries} != selected_ids:
                raise ValueError("迁移范围包含不存在的知识库")

            by_type: dict[str, list[ProjectDeploymentTask]] = defaultdict(list)
            for dt in deployment_tasks:
                task = project_tasks.get(dt.project_task_id)
                if task and dt.index_profile_id: by_type[task.knowledge_type].append(dt)
            collections: dict[str, list[dict[str, Any]]] = defaultdict(list)
            mapping: dict[str, dict[str, Any]] = {}
            for library in sorted(libraries, key=lambda item: item.id):
                candidates = [dt for dt in by_type.get(library.knowledge_type, []) if dt.index_profile_id]
                compatible_profile_ids = {item.id for item in self.store._index_profile_snapshots_for_library(session, library)}
                candidates = [dt for dt in candidates if dt.index_profile_id in compatible_profile_ids]
                profile_ids = sorted({dt.index_profile_id for dt in candidates})
                if len(profile_ids) != 1:
                    raise ValueError(f"知识库 {library.name} 无法唯一映射到启用的 DeploymentTask/Profile")
                profile = session.get(KnowledgeIndexProfile, profile_ids[0])
                revision = session.get(KnowledgeIndexProfileRevision, profile.current_revision_id) if profile else None
                if not profile or not revision or revision.status != "published":
                    raise ValueError(f"知识库 {library.name} 的 Profile Revision 未发布")
                item = {
                    "knowledge_library_id": library.id, "knowledge_library_name": library.name,
                    "index_profile_id": profile.id, "index_profile_revision_id": revision.id,
                    "storage_contract_revision_id": revision.storage_contract_revision_id,
                    "collection_name": revision.collection_name, "partition_name": library.partition_name,
                    "item_count": int(session.scalar(select(func.count()).select_from(KnowledgeItem).where(
                        KnowledgeItem.knowledge_library_id == library.id
                    )) or 0),
                }
                collections[revision.collection_name].append(item)
                mapping[library.id] = item
            dependencies = resolve_dependencies(session, sorted(selected_ids), include_full_document_library=include_full_document_library)
            latest_route = session.scalar(select(ProjectRouteVersion).where(
                ProjectRouteVersion.project_deployment_id == project_deployment.id,
                ProjectRouteVersion.release_stage == deployment.release_stage,
                ProjectRouteVersion.status == "published",
            ).order_by(ProjectRouteVersion.version_no.desc()))
            if package_kind == "deployment_seed" and not latest_route:
                raise ValueError("deployment_seed 要求已发布的 Routing baseline")
            target = session.scalar(select(MilvusTarget).join(
                DeploymentTarget, DeploymentTarget.milvus_target_id == MilvusTarget.id,
            ).where(
                DeploymentTarget.deployment_id == deployment.id,
                DeploymentTarget.release_stage == deployment.release_stage,
                DeploymentTarget.target_kind == "milvus",
            ))
            if not target: raise ValueError("Deployment 当前阶段没有 Milvus Target")
            return {
                "project": {"id": project.id, "code": project.code, "name": project.name},
                "deployment": {"id": deployment.id, "code": deployment.code, "name": deployment.name,
                               "institution_name": deployment.institution_name,
                               "institution_code": deployment.institution_code,
                               "scope": deployment.scope,
                               "release_stage": deployment.release_stage,
                               "milvus_target_id": target.id},
                "project_deployment": {"id": project_deployment.id,
                                       "project_id": project_deployment.project_id,
                                       "deployment_id": project_deployment.deployment_id,
                                       "status": project_deployment.status},
                "package_kind": package_kind,
                "knowledge_library_ids": sorted(selected_ids),
                "authorized_knowledge_library_ids": sorted(authorized_ids),
                "libraries": [mapping[key] for key in sorted(mapping)],
                "collections": {name: sorted(items, key=lambda item: item["partition_name"])
                                for name, items in sorted(collections.items())},
                "dependencies": {
                    "document_library_ids": list(dependencies.document_library_ids),
                    "source_ids": list(dependencies.source_ids),
                    "source_version_ids": list(dependencies.source_version_ids),
                    "source_chunk_ids": list(dependencies.source_chunk_ids),
                },
                "counts": {
                    "knowledge_libraries": len(selected_ids),
                    "document_libraries": len(dependencies.document_library_ids),
                    "sources": len(dependencies.source_ids),
                    "collections": len(collections),
                    "partitions": sum(len(items) for items in collections.values()),
                },
                "include_full_document_library": include_full_document_library,
                "base_route_version": latest_route.version_no if latest_route else None,
                "base_route_snapshot": latest_route.snapshot_json if latest_route else None,
            }
