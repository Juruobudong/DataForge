"""Deployment-scoped, deterministic migration planning."""
from __future__ import annotations

from collections import defaultdict
import json
from typing import Any

from sqlalchemy import func, select

from ..models import (
    DocumentLibraryProcessingRecord,
    DocumentLibraryTemplateBinding,
    DocumentLibraryTemplateOutput,
    KnowledgeIndexProfile,
    KnowledgeIndexProfileRevision,
    KnowledgeItem,
    KnowledgeLibrary,
    KnowledgeAssetVersion,
    Project,
    Deployment,
    ProjectDeployment,
    ProjectDeploymentTask,
    ProjectOrgRoute,
    ProjectOrgRouteLibrary,
    ProjectRouteVersion,
    ProjectRouteVersionAsset,
    InstitutionReleaseDraft,
    InstitutionReleaseDraftProject,
    InstitutionReleaseSnapshot,
    ProjectTask,
    Source, SourceVersion,
    StorageContractRevision,
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
            return {
                "project": {"id": project.id, "code": project.code, "name": project.name},
                "deployment": {"id": deployment.id, "code": deployment.code, "name": deployment.name,
                               "institution_name": deployment.institution_name,
                               "institution_code": deployment.institution_code,
                               "scope": deployment.scope,
                               "release_stage": deployment.release_stage},
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


class InstitutionReleasePlanner:
    """Freeze one institution-wide, multi-project release closure."""

    def __init__(self, store: V7Store):
        self.store = store

    def plan(self, draft_id: str) -> dict[str, Any]:
        with self.store.sessions() as session:
            draft = session.get(InstitutionReleaseDraft, draft_id)
            if not draft or draft.status not in {"draft", "planned", "failed"}:
                raise ValueError("机构发布草稿当前不可规划")
            deployment = session.get(Deployment, draft.target_deployment_id)
            if not deployment or deployment.scope != "institution" or not deployment.institution_code:
                raise ValueError("机构发布目标身份不完整")
            target_code = (draft.selection_json or {}).get("target_institution_code")
            if not target_code or target_code != deployment.institution_code:
                raise ValueError("机构发布草稿的 institution_code 与目标 Deployment 不匹配")
            project_links = list(session.scalars(select(InstitutionReleaseDraftProject).where(
                InstitutionReleaseDraftProject.institution_release_draft_id == draft.id,
            ).order_by(InstitutionReleaseDraftProject.created_at)))
            projects: list[dict[str, Any]] = []
            route_stages: set[str] = set()
            asset_ids: set[str] = set()
            required_by_projects: dict[str, list[dict[str, str]]] = defaultdict(list)
            project_required_refs = 0
            extra_asset_ids: set[str] = set()
            if draft.package_kind in {"deployment_seed", "institution_release"}:
                if not project_links:
                    raise ValueError("Seed/Institution Release 至少选择一个项目版本")
                for link in project_links:
                    binding = session.get(ProjectDeployment, link.project_deployment_id)
                    route = session.get(ProjectRouteVersion, link.project_route_version_id)
                    project = session.get(Project, binding.project_id) if binding else None
                    if not binding or binding.deployment_id != deployment.id or not project:
                        raise ValueError("机构发布项目不属于目标机构")
                    if not route or route.status != "frozen" or route.project_deployment_id != binding.id:
                        raise ValueError("机构发布只能引用当前机构的 frozen RouteVersion")
                    route_stages.add(route.release_stage)
                    route_assets = list(session.scalars(select(ProjectRouteVersionAsset).where(
                        ProjectRouteVersionAsset.project_route_version_id == route.id,
                    )))
                    if not route_assets:
                        raise ValueError(f"项目 {project.name} 的 frozen RouteVersion 没有 AssetVersion")
                    asset_ids.update(item.knowledge_asset_version_id for item in route_assets)
                    project_required_refs += len(route_assets)
                    project_ref = {"project_id": project.id, "project_name": project.name}
                    for item in route_assets:
                        if project_ref not in required_by_projects[item.knowledge_asset_version_id]:
                            required_by_projects[item.knowledge_asset_version_id].append(project_ref)
                    projects.append({
                        "project": {"id": project.id, "code": project.code, "name": project.name},
                        "project_deployment_id": binding.id,
                        "route_version_id": route.id, "route_version": route.version_no,
                        "route_checksum": route.checksum, "route_snapshot": route.snapshot_json,
                    })
                extra_asset_ids = set((draft.selection_json or {}).get("extra_asset_version_ids") or [])
                asset_ids.update(extra_asset_ids)
            else:
                selected = list(dict.fromkeys((draft.selection_json or {}).get("knowledge_library_ids") or []))
                if not selected:
                    raise ValueError("Knowledge Update 至少选择一个知识库")
                for library_id in selected:
                    latest_by_profile: dict[str, KnowledgeAssetVersion] = {}
                    for asset in session.scalars(select(KnowledgeAssetVersion).where(
                        KnowledgeAssetVersion.knowledge_library_id == library_id,
                        KnowledgeAssetVersion.status == "ready",
                    ).order_by(KnowledgeAssetVersion.version_no.desc())):
                        latest_by_profile.setdefault(asset.index_profile_id, asset)
                    if not latest_by_profile:
                        raise ValueError(f"知识库 {library_id} 没有 Ready AssetVersion")
                    asset_ids.update(item.id for item in latest_by_profile.values())

            if len(route_stages) > 1:
                raise ValueError("机构发布不能混合测试环境和生产环境的项目版本")
            selected_stage = (draft.selection_json or {}).get("release_stage")
            release_stage = str(selected_stage or (next(iter(route_stages)) if route_stages
                                                    else deployment.release_stage))
            if release_stage not in {"test", "production"}:
                raise ValueError("release_stage 只允许 test 或 production")
            if route_stages and route_stages != {release_stage}:
                raise ValueError("机构发布环境与所选 frozen RouteVersion 不一致")

            assets = list(session.scalars(select(KnowledgeAssetVersion).where(
                KnowledgeAssetVersion.id.in_(asset_ids),
            ))) if asset_ids else []
            assets_by_id = {item.id: item for item in assets}
            checks: list[dict[str, Any]] = []

            def block(code: str, subject: dict[str, Any], expected: Any, observed: Any,
                      message: str) -> None:
                checks.append({"code": code, "status": "blocked", "subject": subject,
                               "expected": expected, "observed": observed, "message": message})

            missing_ids = sorted(asset_ids - set(assets_by_id))
            for asset_id in missing_ids:
                if asset_id in required_by_projects:
                    block("RELEASE.PROJECT.ASSET_MISSING", {"asset_version_id": asset_id,
                          "required_by_projects": required_by_projects[asset_id]}, "AssetVersion exists",
                          "missing", "项目冻结版本引用的 AssetVersion 不存在")
                else:
                    block("RELEASE.ASSET.NOT_READY", {"asset_version_id": asset_id}, "ready",
                          "missing", "额外选择的 AssetVersion 不存在")
            for asset in assets:
                if asset.status != "ready":
                    block("RELEASE.ASSET.NOT_READY", {"asset_version_id": asset.id}, "ready",
                          asset.status, "AssetVersion 尚未 Ready")
            library_ids = sorted({item.knowledge_library_id for item in assets})
            libraries = {item.id: item for item in session.scalars(select(KnowledgeLibrary).where(
                KnowledgeLibrary.id.in_(library_ids),
            ))}
            for library_id in library_ids:
                library = libraries.get(library_id)
                if not library or library.status != "active":
                    block("RELEASE.LIBRARY.NOT_ACTIVE", {"knowledge_library_id": library_id},
                          "active", library.status if library else "missing", "知识库不存在或不可用")
            # A logical library may be shared by projects only when its selected
            # physical contract is identical.  Multiple profiles remain legal
            # only for an asset-only Knowledge Update.
            if draft.package_kind != "knowledge_update":
                for library_id in library_ids:
                    versions = sorted({item.id for item in assets if item.knowledge_library_id == library_id})
                    if len(versions) > 1:
                        block("RELEASE.LIBRARY.ASSET_VERSION_CONFLICT",
                              {"knowledge_library_id": library_id}, "one AssetVersion", versions,
                              "同一知识库引用了不同 AssetVersion")

                profile_ids = {item.index_profile_revision_id for item in assets}
                contract_ids = {item.storage_contract_revision_id for item in assets
                                if item.storage_contract_revision_id}
                profiles = {item.id: item for item in session.scalars(
                    select(KnowledgeIndexProfileRevision).where(
                        KnowledgeIndexProfileRevision.id.in_(profile_ids)))
                } if profile_ids else {}
                contracts = {item.id: item for item in session.scalars(
                    select(StorageContractRevision).where(StorageContractRevision.id.in_(contract_ids)))
                } if contract_ids else {}
                by_collection: dict[str, list[KnowledgeAssetVersion]] = defaultdict(list)
                by_partition: dict[tuple[str, str], list[KnowledgeAssetVersion]] = defaultdict(list)
                for asset in assets:
                    by_collection[asset.collection_name].append(asset)
                    by_partition[(asset.collection_name, asset.partition_name)].append(asset)
                for collection_name, values in by_collection.items():
                    signatures: dict[str, dict[str, Any]] = {}
                    for asset in values:
                        profile = profiles.get(asset.index_profile_revision_id)
                        contract = contracts.get(asset.storage_contract_revision_id)
                        signature = {
                            "storage_contract_revision_id": asset.storage_contract_revision_id,
                            "storage_spec_hash": contract.storage_spec_hash if contract else None,
                            "schema": contract.schema_json if contract else None,
                            "dimension": contract.dimension if contract else None,
                            "metric_type": contract.metric_type if contract else None,
                            "index": contract.index_json if contract else None,
                            "index_profile_revision_id": asset.index_profile_revision_id,
                            "field_mapping": profile.fields_json if profile else None,
                        }
                        signatures[json.dumps(signature, sort_keys=True, separators=(",", ":"))] = signature
                    if len(signatures) > 1 or any(value.get("storage_spec_hash") is None or
                                                  value.get("field_mapping") is None
                                                  for value in signatures.values()):
                        block("RELEASE.COLLECTION.CONTRACT_CONFLICT",
                              {"collection_name": collection_name}, "one compatible contract",
                              list(signatures.values()), "同一 Collection 引用了不兼容的物理合同")
                for (collection_name, partition_name), values in by_partition.items():
                    identities = {(item.id, item.content_digest) for item in values}
                    if len(identities) > 1:
                        block("RELEASE.PARTITION.CONTENT_CONFLICT",
                              {"collection_name": collection_name, "partition_name": partition_name},
                              "one AssetVersion and digest",
                              [{"asset_version_id": item.id, "content_digest": item.content_digest}
                               for item in values], "同一物理 Partition 对应了不同内容")

            blocked_codes = {item["code"] for item in checks if item["status"] == "blocked"}
            pass_checks = (
                ("RELEASE.PROJECT.ASSET_COMPLETE", "项目资产闭包完整",
                 {"RELEASE.PROJECT.ASSET_MISSING"}),
                ("RELEASE.ASSET.READY", "AssetVersion 全部 Ready", {"RELEASE.ASSET.NOT_READY"}),
                ("RELEASE.LIBRARY.ACTIVE", "KnowledgeLibrary 全部 Active", {"RELEASE.LIBRARY.NOT_ACTIVE"}),
                ("RELEASE.LIBRARY.VERSION_UNIQUE", "逻辑知识库版本唯一",
                 {"RELEASE.LIBRARY.ASSET_VERSION_CONFLICT"}),
                ("RELEASE.COLLECTION.CONTRACT_COMPATIBLE", "Collection Contract 一致",
                 {"RELEASE.COLLECTION.CONTRACT_CONFLICT"}),
                ("RELEASE.PARTITION.CONTENT_UNIQUE", "Partition 内容无冲突",
                 {"RELEASE.PARTITION.CONTENT_CONFLICT"}),
            )
            for code, message, failures in pass_checks:
                if not (blocked_codes & failures):
                    checks.append({"code": code, "status": "passed", "subject": {},
                                   "expected": True, "observed": True, "message": message})
            include_full = (draft.package_kind in {"deployment_seed", "institution_release"} or
                            bool((draft.selection_json or {}).get("include_full_document_library")))
            dependencies = resolve_dependencies(session, library_ids,
                                                 include_full_document_library=include_full)
            closure_outputs = list(session.scalars(select(DocumentLibraryTemplateOutput).where(
                DocumentLibraryTemplateOutput.knowledge_library_id.in_(library_ids),
            ))) if library_ids else []
            closure_binding_ids = {item.document_library_template_binding_id for item in closure_outputs}
            closure_bindings = list(session.scalars(select(DocumentLibraryTemplateBinding).where(
                DocumentLibraryTemplateBinding.id.in_(closure_binding_ids),
                DocumentLibraryTemplateBinding.document_library_id.in_(dependencies.document_library_ids),
            ))) if closure_binding_ids and dependencies.document_library_ids else []
            closure_binding_ids = {item.id for item in closure_bindings}
            closure_outputs = [item for item in closure_outputs
                               if item.document_library_template_binding_id in closure_binding_ids]
            closure_records = list(session.scalars(select(DocumentLibraryProcessingRecord).where(
                DocumentLibraryProcessingRecord.document_library_template_binding_id.in_(closure_binding_ids),
                DocumentLibraryProcessingRecord.source_version_id.in_(dependencies.source_version_ids),
            ))) if closure_binding_ids and dependencies.source_version_ids else []
            closure_revision_ids = {
                item.last_successful_revision_id for item in closure_bindings if item.last_successful_revision_id
            } | {item.knowledge_flow_template_revision_id for item in closure_records}
            inventory_items = list(session.scalars(select(KnowledgeItem).where(
                KnowledgeItem.knowledge_library_id.in_(library_ids), KnowledgeItem.status == "active",
            ))) if library_ids else []
            inventory_sources = list(session.scalars(select(Source).where(
                Source.id.in_(dependencies.source_ids), Source.status != "deleted",
            ))) if dependencies.source_ids else []
            inventory_versions = list(session.scalars(select(SourceVersion).where(
                SourceVersion.id.in_(dependencies.source_version_ids), SourceVersion.status != "deleted",
            ))) if dependencies.source_version_ids else []
            content_inventory = {
                "knowledge_items": [{"id": item.id, "knowledge_library_id": item.knowledge_library_id,
                                     "content_hash": item.content_hash} for item in inventory_items],
                "sources": [{"id": item.id, "document_library_id": item.document_library_id}
                            for item in inventory_sources],
                "source_versions": [{"id": item.id, "source_id": item.source_id, "sha256": item.sha256}
                                    for item in inventory_versions],
            }
            collections: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for asset in sorted(assets, key=lambda item: (item.collection_name, item.partition_name)):
                library = libraries.get(asset.knowledge_library_id)
                collections[asset.collection_name].append({
                    "knowledge_library_id": asset.knowledge_library_id,
                    "knowledge_library_name": library.name if library else asset.knowledge_library_id,
                    "asset_version_id": asset.id, "asset_version_no": asset.version_no,
                    "index_profile_id": asset.index_profile_id,
                    "index_profile_revision_id": asset.index_profile_revision_id,
                    "storage_contract_revision_id": asset.storage_contract_revision_id,
                    "collection_name": asset.collection_name, "partition_name": asset.partition_name,
                    "item_count": asset.item_count, "content_digest": asset.content_digest,
                    "embedding_serving_id": asset.embedding_serving_id,
                    "embedding_model": asset.embedding_model, "embedding_dimension": asset.embedding_dimension,
                    "review_gate_status": asset.review_gate_status,
                    "review_snapshot_digest": asset.review_snapshot_digest,
                    "status": asset.status,
                    "required_by_projects": required_by_projects.get(asset.id, []),
                    "selected_manually": asset.id in extra_asset_ids,
                    "locked": bool(required_by_projects.get(asset.id)),
                })
            base = session.get(InstitutionReleaseSnapshot, draft.base_release_id) if draft.base_release_id else None
            if draft.base_release_id and (not base or base.target_deployment_id != deployment.id):
                raise ValueError("base release 不属于当前目标机构")
            if base and str((base.snapshot_json or {}).get("deployment", {}).get("release_stage") or release_stage) != release_stage:
                raise ValueError("base release 与当前发布环境不一致")
            previous_assets = {
                str(item.get("id")): item for item in ((base.snapshot_json or {}).get("asset_versions") or [])
            } if base else {}
            current_assets = {item.id: item for item in assets}
            added = sorted(set(current_assets) - set(previous_assets))
            removed = sorted(set(previous_assets) - set(current_assets))
            reused = sorted(set(current_assets) & set(previous_assets))
            tombstones = [{"kind": "asset_version", "id": asset_id,
                           "knowledge_library_id": previous_assets[asset_id].get("knowledge_library_id")}
                          for asset_id in removed]
            base_inventory = (base.snapshot_json or {}).get("content_inventory") or {} if base else {}
            content_diff: dict[str, dict[str, int]] = {}
            for kind in ("knowledge_items", "sources", "source_versions"):
                previous = {str(item["id"]): item for item in base_inventory.get(kind) or []}
                current = {str(item["id"]): item for item in content_inventory[kind]}
                comparable = (kind == "knowledge_items" or not base or
                              (include_full and bool((base.snapshot_json or {}).get(
                                  "include_full_document_library"))))
                removed_ids = sorted(set(previous) - set(current))
                if not comparable:
                    removed_ids = []
                added_ids = sorted(set(current) - set(previous))
                updated_ids = sorted(identifier for identifier in set(previous) & set(current)
                                     if previous[identifier] != current[identifier])
                content_diff[kind] = {"added": len(added_ids), "updated": len(updated_ids),
                                      "removed": len(removed_ids),
                                      "reused": len(set(previous) & set(current)) - len(updated_ids)}
                tombstone_kind = {"knowledge_items": "knowledge_item", "sources": "source",
                                  "source_versions": "source_version"}[kind]
                tombstones.extend({"kind": tombstone_kind, **previous[identifier]}
                                  for identifier in removed_ids)
            object_size = session.scalar(select(func.sum(SourceVersion.size_bytes)).where(
                SourceVersion.id.in_(dependencies.source_version_ids)
            )) if dependencies.source_version_ids else 0
            blocked = sum(item["status"] == "blocked" for item in checks)
            passed = sum(item["status"] == "passed" for item in checks)
            return {
                "package_kind": draft.package_kind,
                "deployment": {"id": deployment.id, "code": deployment.code, "name": deployment.name,
                               "institution_name": deployment.institution_name,
                               "institution_code": deployment.institution_code, "scope": deployment.scope,
                               "release_stage": release_stage},
                "projects": projects,
                "knowledge_library_ids": library_ids,
                "asset_versions": [item for values in collections.values() for item in values],
                "libraries": [item for values in collections.values() for item in values],
                "collections": dict(collections),
                "selection_summary": {
                    "project_required_refs": project_required_refs,
                    "manual_refs": len(extra_asset_ids),
                    "raw_refs": project_required_refs + len(extra_asset_ids),
                    "duplicates_removed": max(0, project_required_refs + len(extra_asset_ids) - len(asset_ids)),
                    "resolved_assets": len(asset_ids),
                },
                "preflight": {"passed": passed, "warnings": 0, "blocked": blocked, "checks": checks},
                "dependencies": {
                    "document_library_ids": list(dependencies.document_library_ids),
                    "source_ids": list(dependencies.source_ids),
                    "source_version_ids": list(dependencies.source_version_ids),
                    "source_chunk_ids": list(dependencies.source_chunk_ids),
                },
                "runtime_closure": {
                    "binding_ids": sorted(closure_binding_ids),
                    "output_ids": sorted(item.id for item in closure_outputs),
                    "processing_record_ids": sorted(item.id for item in closure_records),
                    "template_revision_ids": sorted(closure_revision_ids),
                },
                "content_inventory": content_inventory,
                "include_full_document_library": include_full,
                "base_release_id": draft.base_release_id,
                "base_manifest_digest": base.manifest_digest if base else None,
                "diff_summary": {"asset_versions": {"added": len(added), "updated": 0,
                                                        "removed": len(removed), "reused": len(reused)},
                                 **content_diff,
                                 "added_ids": added, "removed_ids": removed, "reused_ids": reused},
                "tombstones": tombstones,
                "counts": {"projects": len(projects), "knowledge_libraries": len(library_ids),
                           "document_libraries": len(dependencies.document_library_ids),
                           "collections": len(collections), "partitions": len(assets),
                           "object_size_bytes": int(object_size or 0),
                           "vector_item_count": sum(int(item.item_count or 0) for item in assets)},
                "milvus_override_reason": draft.milvus_override_reason,
            }

    def asset_options(self, draft_id: str) -> dict[str, Any]:
        plan = self.plan(draft_id)
        selected_rows = {item["asset_version_id"]: dict(item) for item in plan["asset_versions"]}
        with self.store.sessions() as session:
            draft = session.get(InstitutionReleaseDraft, draft_id)
            if not draft:
                raise ValueError("机构发布草稿不存在")
            if draft.package_kind != "knowledge_update":
                latest: dict[tuple[str, str], tuple[KnowledgeAssetVersion, KnowledgeLibrary]] = {}
                rows = session.execute(select(KnowledgeAssetVersion, KnowledgeLibrary).join(
                    KnowledgeLibrary, KnowledgeLibrary.id == KnowledgeAssetVersion.knowledge_library_id,
                ).where(KnowledgeAssetVersion.status == "ready", KnowledgeLibrary.status == "active").order_by(
                    KnowledgeAssetVersion.version_no.desc())).all()
                for asset, library in rows:
                    latest.setdefault((asset.knowledge_library_id, asset.index_profile_id), (asset, library))
                for asset, library in latest.values():
                    selected_rows.setdefault(asset.id, {
                        "asset_version_id": asset.id, "asset_version_no": asset.version_no,
                        "knowledge_library_id": asset.knowledge_library_id,
                        "knowledge_library_name": library.name,
                        "collection_name": asset.collection_name, "partition_name": asset.partition_name,
                        "item_count": asset.item_count, "content_digest": asset.content_digest,
                        "status": asset.status, "required_by_projects": [],
                        "selected_manually": False, "locked": False,
                    })
        collections: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in selected_rows.values():
            row = dict(item)
            row["required"] = bool(row.get("locked"))
            collections[row["collection_name"]].append(row)
        return {"collections": [{"collection_name": name,
                                  "assets": sorted(values, key=lambda item: (
                                      not item.get("locked"), item.get("knowledge_library_name", ""),
                                      item.get("asset_version_no", 0)))}
                                 for name, values in sorted(collections.items())]}
