"""Worker-side creation of immutable, signed migration packages."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select

from ..instance import InstanceContext
from ... import __version__ as DATAFORGE_VERSION
from ..models import (
    DataForgeInstance, DocumentLibrary, DocumentLibraryMember,
    DocumentLibraryProcessingBaseline, DocumentLibraryProcessingRecord,
    DocumentLibraryTemplateBinding, DocumentLibraryTemplateOutput, EmbeddingProfile,
    FlowExecutionSnapshot, FlowSubgraph, FlowSubgraphRevision,
    KnowledgeIndexProfile, KnowledgeIndexProfileRevision, KnowledgeItem, KnowledgeItemSource,
    KnowledgeFlowTemplate, KnowledgeFlowTemplateRevision, KnowledgeLibrary, KnowledgeType,
    KnowledgeTypeIndexBinding, KnowledgeTypeModeRevision, KnowledgeTypeRevision,
    OperatorDefinition, OperatorVersion, Project, ProjectDeployment, ProjectDeploymentTask, ProjectOrgRoute,
    ProjectOrgRouteLibrary, ProjectRouteVersion, ProjectTask, Source, SourceChunk, SourceVersion,
    PromptTemplate, PromptTemplateRevision, QualityProfile, QualityProfileRevision,
    StorageContract, StorageContractRevision,
)
from ..store import V7Store, new_id
from ..vector import V7Milvus
from .package import MigrationPackageBuilder
from .planner import InstitutionReleasePlanner, MigrationPlanner


def _value(value: Any) -> Any:
    if isinstance(value, (datetime, date)): return value.isoformat()
    return value


def model_payload(item) -> dict[str, Any]:
    return {column.name: _value(getattr(item, column.name)) for column in item.__table__.columns}


def jsonl(items: list[Any]) -> bytes:
    return b"".join(json.dumps(model_payload(item), ensure_ascii=False, sort_keys=True,
                               separators=(",", ":")).encode("utf-8") + b"\n" for item in items)


def jsonl_payloads(items: list[dict[str, Any]]) -> bytes:
    return b"".join(json.dumps(item, ensure_ascii=False, sort_keys=True,
                               separators=(",", ":")).encode("utf-8") + b"\n" for item in items)


def _filtered_baseline(snapshot: dict[str, Any] | None, selected_ids: set[str]) -> dict[str, Any] | None:
    if not snapshot: return None
    value = json.loads(json.dumps(snapshot))
    flat = []
    for task in value.get("tasks", []):
        kept_routes = []
        for route in task.get("org_routes", []):
            route["libraries"] = [item for item in route.get("libraries", [])
                                  if item.get("knowledge_library_id") in selected_ids]
            route["knowledge_library_ids"] = [item["knowledge_library_id"] for item in route["libraries"]]
            if route["libraries"]:
                kept_routes.append(route)
                flat.append({"task_code": task["task_code"], "org_code": route["org_code"],
                             "libraries": route["libraries"]})
        task["org_routes"] = kept_routes
    value["tasks"] = [task for task in value.get("tasks", []) if task.get("org_routes")]
    value["routes"] = flat
    return value


class MigrationExporter:
    def __init__(self, store: V7Store, objects, milvus: V7Milvus, *, migration_dir: Path,
                 private_key: str, key_id: str, instance: InstanceContext):
        self.store, self.objects, self.milvus = store, objects, milvus
        self.migration_dir, self.private_key, self.key_id, self.instance = migration_dir, private_key, key_id, instance

    def run(self, job_id: str) -> dict[str, Any]:
        job = self.store.get_migration_job(job_id)
        if self.instance.mode != "central" or job["direction"] != "export":
            raise ValueError("只有 central 实例可以导出迁移包")
        options = job["checkpoint"].get("options", {})
        is_v2 = bool(job.get("release_snapshot_id"))
        if is_v2:
            release = self.store.get_institution_release_snapshot(job["release_snapshot_id"])
            plan = dict(release["snapshot"])
            plan["release_id"] = release["id"]
        else:
            plan = MigrationPlanner(self.store).plan(job["project_deployment_id"], options.get("knowledge_library_ids"),
                include_full_document_library=bool(options.get("include_full_document_library")),
                package_kind=job["package_kind"])
        work = self.migration_dir / job_id
        work.mkdir(parents=True, exist_ok=True)
        builder = MigrationPackageBuilder(work / f"{job_id}.dfm", key_id=self.key_id, private_key=self.private_key)
        package_id = job.get("package_id") or new_id("pkg")
        try:
            if is_v2:
                self.store.update_institution_release_status(job["release_snapshot_id"], "building")
            self.store.update_migration_job(job_id, status="running", stage="exporting_metadata",
                                            checkpoint={**job["checkpoint"], "plan": plan})
            scope_revision = self._scope_revision(plan)
            metadata_revisions = self._content_revisions(plan["knowledge_library_ids"], active_only=is_v2)
            if is_v2:
                self._add_metadata_v2(builder, plan, work)
            else:
                self._add_metadata(builder, plan, work)
            vector_revisions = self._add_vectors(builder, plan, work, job_id)
            if metadata_revisions != self._content_revisions(plan["knowledge_library_ids"], active_only=is_v2):
                raise ValueError("正式知识元数据在导出期间发生变化")
            if scope_revision != self._scope_revision(plan):
                raise ValueError("文档或知识依赖在导出期间发生变化")
            if is_v2:
                manifest = self._manifest_v2(plan, package_id, metadata_revisions, vector_revisions)
            else:
                manifest = {
                    "format": "dataforge-migration", "schema_version": 1, "package_kind": job["package_kind"],
                    "package_id": package_id, "source_instance_id": self.instance.id,
                    "project": plan["project"], "deployment": plan["deployment"],
                    "project_deployment": plan["project_deployment"],
                    "scope": {"deployment_count": 1, "knowledge_library_ids": plan["knowledge_library_ids"]},
                    "collections": {name: [{"knowledge_library_id": item["knowledge_library_id"],
                        "partition_name": item["partition_name"], "index_profile_revision_id": item["index_profile_revision_id"],
                        "content_revision": vector_revisions[item.get("asset_version_id") or item["knowledge_library_id"]],
                        "metadata_revision": metadata_revisions[item["knowledge_library_id"]]}
                        for item in items] for name, items in plan["collections"].items()},
                    "base_route_version": plan["base_route_version"],
                }
            result = builder.build(manifest)
            if is_v2:
                self.store.update_institution_release_status(job["release_snapshot_id"], "ready")
            return self.store.update_migration_job(job_id, status="ready", stage="ready", package_path=result["path"],
                package_sha256=result["sha256"], package_id=package_id,
                checkpoint={**job["checkpoint"], "plan": plan, "package_id": package_id})
        except Exception as exc:
            if is_v2:
                self.store.update_institution_release_status(job["release_snapshot_id"], "failed")
            self.store.update_migration_job(job_id, status="failed", stage="failed", error=str(exc))
            raise

    def _manifest_v2(self, plan: dict[str, Any], package_id: str,
                     metadata_revisions: dict[str, str], vector_revisions: dict[str, str]) -> dict[str, Any]:
        assets = []
        collections = {}
        for collection_name, items in plan["collections"].items():
            partitions = []
            for item in items:
                asset_id = item["asset_version_id"]
                value = {**item, "id": asset_id,
                         "content_revision": vector_revisions[asset_id],
                         "metadata_revision": metadata_revisions[item["knowledge_library_id"]]}
                assets.append(value)
                partitions.append({key: value[key] for key in (
                    "knowledge_library_id", "asset_version_id", "asset_version_no",
                    "partition_name", "index_profile_revision_id", "content_revision", "metadata_revision",
                )})
            collections[collection_name] = partitions
        return {
            "format": "dataforge-migration", "schema_version": 2,
            "manifest_schema_version": 2, "package_kind": plan["package_kind"],
            "package_id": package_id, "source_instance_id": self.instance.id,
            "minimum_dataforge_version": DATAFORGE_VERSION,
            "maximum_dataforge_version": DATAFORGE_VERSION,
            "source_instance_version": DATAFORGE_VERSION,
            "required_features": ["immutable_asset_versions", "multi_project_release", "resumable_import"],
            "release_id": plan.get("release_id"),
            "operator_versions": list(plan.get("operator_versions") or []),
            "storage_contract_versions": sorted({str(item.get("storage_contract_revision_id"))
                                                   for item in assets if item.get("storage_contract_revision_id")}),
            "base_release_id": plan.get("base_release_id"),
            "base_manifest_digest": plan.get("base_manifest_digest"),
            "deployment": plan["deployment"], "projects": plan.get("projects") or [],
            "scope": {"deployment_count": 1, "project_count": len(plan.get("projects") or []),
                      "knowledge_library_ids": plan["knowledge_library_ids"]},
            "asset_versions": assets, "collections": collections,
            "diff_summary": plan.get("diff_summary") or {},
            "tombstones": plan.get("tombstones") or [],
        }

    def _content_revisions(self, library_ids: list[str], *, active_only: bool = False) -> dict[str, str]:
        result = {}
        with self.store.sessions() as session:
            for library_id in library_ids:
                digest = hashlib.sha256()
                query = select(KnowledgeItem).where(KnowledgeItem.knowledge_library_id == library_id)
                if active_only:
                    query = query.where(KnowledgeItem.status == "active")
                items = list(session.scalars(query.order_by(KnowledgeItem.id)))
                for item in items:
                    digest.update(json.dumps(model_payload(item), ensure_ascii=False, sort_keys=True,
                                             separators=(",", ":")).encode()); digest.update(b"\n")
                    links = list(session.scalars(select(KnowledgeItemSource).where(
                        KnowledgeItemSource.knowledge_item_id == item.id).order_by(KnowledgeItemSource.id)))
                    for link in links:
                        digest.update(json.dumps(model_payload(link), ensure_ascii=False, sort_keys=True,
                                                 separators=(",", ":")).encode()); digest.update(b"\n")
                result[library_id] = digest.hexdigest()
        return result

    def _scope_revision(self, plan: dict[str, Any]) -> str:
        digest = hashlib.sha256(); deps = plan["dependencies"]
        with self.store.sessions() as session:
            queries = (
                (DocumentLibrary, DocumentLibrary.id, deps["document_library_ids"]),
                (Source, Source.id, deps["source_ids"]),
                (SourceVersion, SourceVersion.id, deps["source_version_ids"]),
                (SourceChunk, SourceChunk.id, deps["source_chunk_ids"]),
                (KnowledgeLibrary, KnowledgeLibrary.id, plan["knowledge_library_ids"]),
            )
            for model, column, identifiers in queries:
                if not identifiers: continue
                for item in session.scalars(select(model).where(column.in_(identifiers)).order_by(column)):
                    digest.update(json.dumps(model_payload(item), ensure_ascii=False, sort_keys=True,
                                             separators=(",", ":")).encode()); digest.update(b"\n")
        return digest.hexdigest()

    def _add_metadata(self, builder: MigrationPackageBuilder, plan: dict[str, Any], work: Path) -> None:
        with self.store.sessions() as session:
            project_deployment = session.get(ProjectDeployment, plan["project_deployment"]["id"])
            project = session.get(Project, project_deployment.project_id)
            dts = list(session.scalars(select(ProjectDeploymentTask).where(
                ProjectDeploymentTask.project_deployment_id == project_deployment.id)))
            tasks = list(session.scalars(select(ProjectTask).where(ProjectTask.id.in_([item.project_task_id for item in dts]))))
            routes = list(session.scalars(select(ProjectOrgRoute).where(ProjectOrgRoute.project_deployment_task_id.in_([item.id for item in dts]))))
            links = list(session.scalars(select(ProjectOrgRouteLibrary).where(ProjectOrgRouteLibrary.project_org_route_id.in_([item.id for item in routes])))) if routes else []
            selected = set(plan["knowledge_library_ids"])
            links = [item for item in links if item.knowledge_library_id in selected]
            kept_route_ids = {item.project_org_route_id for item in links}
            routes = [item for item in routes if item.id in kept_route_ids]
            if plan["package_kind"] == "knowledge_update": routes, links = [], []
            baseline = (_filtered_baseline(plan["base_route_snapshot"], selected)
                        if plan["package_kind"] == "deployment_seed" else None)
            control = {"project": model_payload(project), "deployment": plan["deployment"],
                "project_deployment": model_payload(project_deployment),
                "tasks": [model_payload(item) for item in tasks], "deployment_tasks": [model_payload(item) for item in dts],
                "org_routes": [model_payload(item) for item in routes], "route_libraries": [model_payload(item) for item in links],
                "route_baseline": baseline}
            builder.add_bytes("control/deployment.json", json.dumps(control, ensure_ascii=False, sort_keys=True).encode())

            profile_revision_ids = {item["index_profile_revision_id"] for item in plan["libraries"]}
            profile_revisions = list(session.scalars(select(KnowledgeIndexProfileRevision).where(KnowledgeIndexProfileRevision.id.in_(profile_revision_ids))))
            profiles = list(session.scalars(select(KnowledgeIndexProfile).where(KnowledgeIndexProfile.id.in_({item.knowledge_index_profile_id for item in profile_revisions}))))
            embedding_ids = {item.embedding_profile_id for item in profile_revisions}
            contract_revision_ids = {item.storage_contract_revision_id for item in profile_revisions if item.storage_contract_revision_id}
            contract_revisions = list(session.scalars(select(StorageContractRevision).where(StorageContractRevision.id.in_(contract_revision_ids)))) if contract_revision_ids else []
            contracts = list(session.scalars(select(StorageContract).where(StorageContract.id.in_({item.storage_contract_id for item in contract_revisions})))) if contract_revisions else []
            embeddings = list(session.scalars(select(EmbeddingProfile).where(EmbeddingProfile.id.in_(embedding_ids)))) if embedding_ids else []
            contract_doc = {"index_profiles": [model_payload(item) for item in profiles],
                "index_profile_revisions": [model_payload(item) for item in profile_revisions],
                "embedding_profiles": [model_payload(item) for item in embeddings],
                "storage_contracts": [model_payload(item) for item in contracts],
                "storage_contract_revisions": [model_payload(item) for item in contract_revisions]}
            builder.add_bytes("contracts/contracts.json", json.dumps(contract_doc, ensure_ascii=False, sort_keys=True).encode())

            ids = plan["dependencies"]
            metadata = {
                "document_libraries": list(session.scalars(select(DocumentLibrary).where(DocumentLibrary.id.in_(ids["document_library_ids"])))) if ids["document_library_ids"] else [],
                "sources": list(session.scalars(select(Source).where(Source.id.in_(ids["source_ids"])))) if ids["source_ids"] else [],
                "source_versions": list(session.scalars(select(SourceVersion).where(SourceVersion.id.in_(ids["source_version_ids"])))) if ids["source_version_ids"] else [],
                "source_chunks": list(session.scalars(select(SourceChunk).where(SourceChunk.id.in_(ids["source_chunk_ids"])))) if ids["source_chunk_ids"] else [],
                "document_library_members": list(session.scalars(select(DocumentLibraryMember).where(DocumentLibraryMember.source_id.in_(ids["source_ids"])))) if ids["source_ids"] else [],
                "knowledge_libraries": list(session.scalars(select(KnowledgeLibrary).where(KnowledgeLibrary.id.in_(plan["knowledge_library_ids"])))),
            }
            items = list(session.scalars(select(KnowledgeItem).where(KnowledgeItem.knowledge_library_id.in_(plan["knowledge_library_ids"]))))
            metadata["knowledge_items"] = items
            metadata["knowledge_item_sources"] = list(session.scalars(select(KnowledgeItemSource).where(
                KnowledgeItemSource.knowledge_item_id.in_([item.id for item in items])))) if items else []
            for name, values in metadata.items(): builder.add_bytes(f"metadata/{name}.jsonl", jsonl(values))
            for version in metadata["source_versions"]:
                target = work / "objects" / version.id
                copied = self.objects.copy_to(version.object_key, target)
                if copied.sha256 != version.sha256 or copied.size_bytes != version.size_bytes:
                    raise ValueError(f"对象文件导出期间发生变化：{version.id}")
                builder.add_file(f"objects/{version.id}", target)

    def _add_metadata_v2(self, builder: MigrationPackageBuilder, plan: dict[str, Any], work: Path) -> None:
        builder.add_bytes("control/release.json", json.dumps({
            "deployment": plan["deployment"], "projects": plan.get("projects") or [],
            "package_kind": plan["package_kind"], "base_release_id": plan.get("base_release_id"),
        }, ensure_ascii=False, sort_keys=True).encode())
        with self.store.sessions() as session:
            profile_revision_ids = {item["index_profile_revision_id"] for item in plan["libraries"]}
            profile_revisions = list(session.scalars(select(KnowledgeIndexProfileRevision).where(
                KnowledgeIndexProfileRevision.id.in_(profile_revision_ids))))
            profiles = list(session.scalars(select(KnowledgeIndexProfile).where(
                KnowledgeIndexProfile.id.in_({item.knowledge_index_profile_id for item in profile_revisions}))))
            embedding_ids = {item.embedding_profile_id for item in profile_revisions}
            contract_revision_ids = {item.storage_contract_revision_id for item in profile_revisions
                                     if item.storage_contract_revision_id}
            contract_revisions = list(session.scalars(select(StorageContractRevision).where(
                StorageContractRevision.id.in_(contract_revision_ids)))) if contract_revision_ids else []
            contracts = list(session.scalars(select(StorageContract).where(
                StorageContract.id.in_({item.storage_contract_id for item in contract_revisions})))) \
                if contract_revisions else []
            embeddings = list(session.scalars(select(EmbeddingProfile).where(
                EmbeddingProfile.id.in_(embedding_ids)))) if embedding_ids else []
            builder.add_bytes("contracts/contracts.json", json.dumps({
                "index_profiles": [model_payload(item) for item in profiles],
                "index_profile_revisions": [model_payload(item) for item in profile_revisions],
                "embedding_profiles": [model_payload(item) for item in embeddings],
                "storage_contracts": [model_payload(item) for item in contracts],
                "storage_contract_revisions": [model_payload(item) for item in contract_revisions],
            }, ensure_ascii=False, sort_keys=True).encode())
            ids = plan["dependencies"]
            metadata = {
                "document_libraries": list(session.scalars(select(DocumentLibrary).where(
                    DocumentLibrary.id.in_(ids["document_library_ids"])))) if ids["document_library_ids"] else [],
                "sources": list(session.scalars(select(Source).where(
                    Source.id.in_(ids["source_ids"])))) if ids["source_ids"] else [],
                "source_versions": list(session.scalars(select(SourceVersion).where(
                    SourceVersion.id.in_(ids["source_version_ids"])))) if ids["source_version_ids"] else [],
                "source_chunks": list(session.scalars(select(SourceChunk).where(
                    SourceChunk.id.in_(ids["source_chunk_ids"])))) if ids["source_chunk_ids"] else [],
                "document_library_members": list(session.scalars(select(DocumentLibraryMember).where(
                    DocumentLibraryMember.source_id.in_(ids["source_ids"])))) if ids["source_ids"] else [],
                "knowledge_libraries": list(session.scalars(select(KnowledgeLibrary).where(
                    KnowledgeLibrary.id.in_(plan["knowledge_library_ids"])))),
            }
            items = list(session.scalars(select(KnowledgeItem).where(
                KnowledgeItem.knowledge_library_id.in_(plan["knowledge_library_ids"]),
                KnowledgeItem.status == "active")))
            metadata["knowledge_items"] = items
            metadata["knowledge_item_sources"] = list(session.scalars(select(KnowledgeItemSource).where(
                KnowledgeItemSource.knowledge_item_id.in_([item.id for item in items])))) if items else []
            self._add_runtime_closure(session, metadata, plan)
            for name, values in metadata.items():
                payload = jsonl_payloads(values) if values and isinstance(values[0], dict) else jsonl(values)
                builder.add_bytes(f"metadata/{name}.jsonl", payload)
            for version in metadata["source_versions"]:
                target = work / "objects" / version.id
                copied = self.objects.copy_to(version.object_key, target)
                if copied.sha256 != version.sha256 or copied.size_bytes != version.size_bytes:
                    raise ValueError(f"对象文件导出期间发生变化：{version.id}")
                builder.add_file(f"objects/{version.id}", target)

    @staticmethod
    def _referenced_revision_ids(value: Any, key: str) -> set[str]:
        result: set[str] = set()
        if isinstance(value, dict):
            for name, child in value.items():
                if name == key and isinstance(child, str) and child:
                    result.add(child)
                else:
                    result.update(MigrationExporter._referenced_revision_ids(child, key))
        elif isinstance(value, list):
            for child in value:
                result.update(MigrationExporter._referenced_revision_ids(child, key))
        return result

    def _add_runtime_closure(self, session, metadata: dict[str, list[Any]], plan: dict[str, Any]) -> None:
        """Freeze the exact runnable template closure without copying Job/Run history."""
        document_ids = set(plan["dependencies"]["document_library_ids"])
        source_version_ids = set(plan["dependencies"]["source_version_ids"])
        selected_library_ids = set(plan["knowledge_library_ids"])
        library_rows = metadata.get("knowledge_libraries") or []
        type_revision_ids = {item.knowledge_type_revision_id for item in library_rows
                             if item.knowledge_type_revision_id}
        type_revisions = list(session.scalars(select(KnowledgeTypeRevision).where(
            KnowledgeTypeRevision.id.in_(type_revision_ids),
        ))) if type_revision_ids else []
        if type_revision_ids != {item.id for item in type_revisions}:
            raise ValueError("知识库引用的 KnowledgeTypeRevision 不完整")
        knowledge_types = list(session.scalars(select(KnowledgeType).where(
            KnowledgeType.id.in_({item.knowledge_type_id for item in type_revisions}),
        ))) if type_revisions else []
        type_modes = list(session.scalars(select(KnowledgeTypeModeRevision).where(
            KnowledgeTypeModeRevision.knowledge_type_revision_id.in_(type_revision_ids),
        ))) if type_revision_ids else []
        type_index_bindings = list(session.scalars(select(KnowledgeTypeIndexBinding).where(
            KnowledgeTypeIndexBinding.knowledge_type_revision_id.in_(type_revision_ids),
        ))) if type_revision_ids else []
        frozen = dict(plan.get("runtime_closure") or {})
        if frozen:
            outputs = list(session.scalars(select(DocumentLibraryTemplateOutput).where(
                DocumentLibraryTemplateOutput.id.in_(set(frozen.get("output_ids") or [])),
            ))) if frozen.get("output_ids") else []
            bindings = list(session.scalars(select(DocumentLibraryTemplateBinding).where(
                DocumentLibraryTemplateBinding.id.in_(set(frozen.get("binding_ids") or [])),
            ))) if frozen.get("binding_ids") else []
            records = list(session.scalars(select(DocumentLibraryProcessingRecord).where(
                DocumentLibraryProcessingRecord.id.in_(set(frozen.get("processing_record_ids") or [])),
            ))) if frozen.get("processing_record_ids") else []
            if ({item.id for item in outputs} != set(frozen.get("output_ids") or []) or
                    {item.id for item in bindings} != set(frozen.get("binding_ids") or []) or
                    {item.id for item in records} != set(frozen.get("processing_record_ids") or [])):
                raise ValueError("Release Snapshot 的模板运行闭包已不完整")
        else:
            outputs = list(session.scalars(select(DocumentLibraryTemplateOutput).where(
                DocumentLibraryTemplateOutput.knowledge_library_id.in_(selected_library_ids)
            ))) if selected_library_ids else []
            output_binding_ids = {item.document_library_template_binding_id for item in outputs}
            bindings = list(session.scalars(select(DocumentLibraryTemplateBinding).where(
                DocumentLibraryTemplateBinding.document_library_id.in_(document_ids),
                DocumentLibraryTemplateBinding.id.in_(output_binding_ids),
            ))) if document_ids and output_binding_ids else []
            records = []
        binding_ids = {item.id for item in bindings}
        outputs = [item for item in outputs if item.document_library_template_binding_id in binding_ids]
        if frozen and any(item.knowledge_library_id not in selected_library_ids for item in outputs):
            raise ValueError("Release Snapshot 的模板输出知识库发生变化")
        if not frozen:
            records = list(session.scalars(select(DocumentLibraryProcessingRecord).where(
                DocumentLibraryProcessingRecord.document_library_template_binding_id.in_(binding_ids),
                DocumentLibraryProcessingRecord.source_version_id.in_(source_version_ids),
            ))) if binding_ids and source_version_ids else []
        revision_ids = {item.last_successful_revision_id for item in bindings if item.last_successful_revision_id}
        revision_ids.update(item.knowledge_flow_template_revision_id for item in records)
        if frozen and revision_ids != set(frozen.get("template_revision_ids") or []):
            raise ValueError("Release Snapshot 的 TemplateRevision 闭包发生变化")
        revisions = list(session.scalars(select(KnowledgeFlowTemplateRevision).where(
            KnowledgeFlowTemplateRevision.id.in_(revision_ids)
        ))) if revision_ids else []
        if revision_ids != {item.id for item in revisions}:
            raise ValueError("文档模板绑定引用的 TemplateRevision 不完整")
        templates = list(session.scalars(select(KnowledgeFlowTemplate).where(
            KnowledgeFlowTemplate.id.in_({item.knowledge_flow_template_id for item in revisions})
        ))) if revisions else []
        snapshot_ids = {item.execution_snapshot_id for item in revisions if item.execution_snapshot_id}
        snapshots = list(session.scalars(select(FlowExecutionSnapshot).where(
            FlowExecutionSnapshot.id.in_(snapshot_ids)
        ))) if snapshot_ids else []
        if snapshot_ids != {item.id for item in snapshots}:
            raise ValueError("TemplateRevision 缺少精确 FlowExecutionSnapshot")

        operator_refs: set[tuple[str, int]] = set()
        prompt_revision_ids: set[str] = set()
        quality_revision_ids: set[str] = set()
        quality_revision_ids.update(item.quality_profile_revision_id for item in type_revisions
                                    if item.quality_profile_revision_id)
        subgraph_refs: set[tuple[str, int]] = set()
        for snapshot in snapshots:
            for dependency in (snapshot.dependency_json or {}).get("dependencies", []):
                if dependency.get("kind") == "operator" and dependency.get("code"):
                    operator_refs.add((str(dependency["code"]), int(dependency.get("version", 1))))
            prompt_revision_ids.update(self._referenced_revision_ids(
                snapshot.compiled_definition_json, "prompt_template_revision_id"))
            quality_revision_ids.update(self._referenced_revision_ids(
                snapshot.compiled_definition_json, "quality_profile_revision_id"))
            for node in (snapshot.compiled_definition_json or {}).get("nodes", []):
                source = node.get("source_subgraph") or {}
                if source.get("code") and source.get("revision") is not None:
                    subgraph_refs.add((str(source["code"]), int(source["revision"])))

        operator_definitions = list(session.scalars(select(OperatorDefinition).where(
            OperatorDefinition.code.in_({code for code, _ in operator_refs})
        ))) if operator_refs else []
        definitions_by_code = {item.code: item for item in operator_definitions}
        codes_by_definition_id = {item.id: item.code for item in operator_definitions}
        operator_versions = list(session.scalars(select(OperatorVersion).where(
            OperatorVersion.operator_definition_id.in_({item.id for item in operator_definitions})
        ))) if operator_definitions else []
        operator_versions = [item for item in operator_versions
                             if (codes_by_definition_id.get(item.operator_definition_id, ""), item.version_no)
                             in operator_refs]
        resolved_operator_refs = {(codes_by_definition_id[item.operator_definition_id], item.version_no)
                                  for item in operator_versions}
        if resolved_operator_refs != operator_refs:
            raise ValueError("FlowExecutionSnapshot 引用的 OperatorVersion 不完整")

        prompt_revisions = list(session.scalars(select(PromptTemplateRevision).where(
            PromptTemplateRevision.id.in_(prompt_revision_ids)
        ))) if prompt_revision_ids else []
        quality_revisions = list(session.scalars(select(QualityProfileRevision).where(
            QualityProfileRevision.id.in_(quality_revision_ids)
        ))) if quality_revision_ids else []
        if prompt_revision_ids != {item.id for item in prompt_revisions}:
            raise ValueError("FlowExecutionSnapshot 引用的 PromptTemplateRevision 不完整")
        if quality_revision_ids != {item.id for item in quality_revisions}:
            raise ValueError("FlowExecutionSnapshot 引用的 QualityProfileRevision 不完整")
        prompts = list(session.scalars(select(PromptTemplate).where(
            PromptTemplate.id.in_({item.prompt_template_id for item in prompt_revisions})
        ))) if prompt_revisions else []
        qualities = list(session.scalars(select(QualityProfile).where(
            QualityProfile.id.in_({item.quality_profile_id for item in quality_revisions})
        ))) if quality_revisions else []

        subgraphs = list(session.scalars(select(FlowSubgraph).where(
            FlowSubgraph.code.in_({code for code, _ in subgraph_refs})
        ))) if subgraph_refs else []
        subgraphs_by_code = {item.code: item for item in subgraphs}
        subgraph_revisions = list(session.scalars(select(FlowSubgraphRevision).where(
            FlowSubgraphRevision.flow_subgraph_id.in_({item.id for item in subgraphs})
        ))) if subgraphs else []
        subgraph_revisions = [item for item in subgraph_revisions
                              if any(subgraphs_by_code[code].id == item.flow_subgraph_id and revision == item.revision_no
                                     for code, revision in subgraph_refs)]

        metadata.update({
            "knowledge_types": knowledge_types,
            "knowledge_type_revisions": type_revisions,
            "knowledge_type_mode_revisions": type_modes,
            "knowledge_type_index_bindings": type_index_bindings,
            "operator_definitions": operator_definitions,
            "operator_versions": operator_versions,
            "prompt_templates": prompts,
            "prompt_template_revisions": prompt_revisions,
            "quality_profiles": qualities,
            "quality_profile_revisions": quality_revisions,
            "flow_subgraphs": subgraphs,
            "flow_subgraph_revisions": subgraph_revisions,
            "knowledge_flow_templates": templates,
            "knowledge_flow_template_revisions": revisions,
            "flow_execution_snapshots": snapshots,
            "document_library_template_bindings": bindings,
            "document_library_template_outputs": outputs,
            "document_library_processing_baselines": [{
                "id": f"baseline_{item.id}",
                "document_library_template_binding_id": item.document_library_template_binding_id,
                "source_version_id": item.source_version_id,
                "knowledge_flow_template_revision_id": item.knowledge_flow_template_revision_id,
                "origin_job_id": item.knowledge_job_id,
                "imported_release_id": plan.get("release_id"),
                "last_success_status": "completed",
                "created_at": _value(item.created_at), "updated_at": _value(item.updated_at),
            } for item in records],
        })
        plan["operator_versions"] = [
            {"code": code, "version": version} for code, version in sorted(operator_refs)
        ]

    def _add_vectors(self, builder: MigrationPackageBuilder, plan: dict[str, Any], work: Path, job_id: str) -> dict[str, str]:
        revisions: dict[str, str] = {}
        physical_partitions: set[tuple[str, str]] = set()
        for collection_name, items in plan["collections"].items():
            for item in items:
                physical_key = (collection_name, item["partition_name"])
                if physical_key in physical_partitions:
                    raise ValueError(f"Frozen Release Snapshot 包含重复 Partition：{collection_name}/{item['partition_name']}")
                physical_partitions.add(physical_key)
                output = work / "vectors" / collection_name / f'{item["partition_name"]}.parquet'
                before = self.milvus.verify_partition(collection_name, item["partition_name"])
                result = self.milvus.export_partition(collection_name, item["partition_name"], output, batch_size=1000)
                after = self.milvus.verify_partition(collection_name, item["partition_name"])
                if before != after or result["count"] != before["count"] or result["digest"] != before["digest"]:
                    raise ValueError(f"Partition 导出期间发生变化：{collection_name}/{item['partition_name']}")
                builder.add_file(f"vectors/{collection_name}/{item['partition_name']}.parquet", output)
                self.store.update_migration_item(job_id, item["knowledge_library_id"], collection_name,
                    source_count=result["count"], source_digest=result["digest"], status="exported")
                revisions[item.get("asset_version_id") or item["knowledge_library_id"]] = result["digest"]
        return revisions
