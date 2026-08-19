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
from ..models import (
    DataForgeInstance, DocumentLibrary, DocumentLibraryMember, EmbeddingProfile,
    KnowledgeIndexProfile, KnowledgeIndexProfileRevision, KnowledgeItem, KnowledgeItemSource,
    KnowledgeLibrary, Project, ProjectDeployment, ProjectDeploymentTask, ProjectOrgRoute,
    ProjectOrgRouteLibrary, ProjectRouteVersion, ProjectTask, Source, SourceChunk, SourceVersion,
    StorageContract, StorageContractRevision,
)
from ..store import V7Store, new_id
from ..vector import V7Milvus
from .package import MigrationPackageBuilder
from .planner import MigrationPlanner


def _value(value: Any) -> Any:
    if isinstance(value, (datetime, date)): return value.isoformat()
    return value


def model_payload(item) -> dict[str, Any]:
    return {column.name: _value(getattr(item, column.name)) for column in item.__table__.columns}


def jsonl(items: list[Any]) -> bytes:
    return b"".join(json.dumps(model_payload(item), ensure_ascii=False, sort_keys=True,
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
        plan = MigrationPlanner(self.store).plan(job["project_deployment_id"], options.get("knowledge_library_ids"),
            include_full_document_library=bool(options.get("include_full_document_library")),
            package_kind=job["package_kind"])
        work = self.migration_dir / job_id
        work.mkdir(parents=True, exist_ok=True)
        builder = MigrationPackageBuilder(work / f"{job_id}.dfm", key_id=self.key_id, private_key=self.private_key)
        package_id = job.get("package_id") or new_id("pkg")
        try:
            self.store.update_migration_job(job_id, status="running", stage="exporting_metadata",
                                            checkpoint={**job["checkpoint"], "plan": plan})
            scope_revision = self._scope_revision(plan)
            metadata_revisions = self._content_revisions(plan["knowledge_library_ids"])
            self._add_metadata(builder, plan, work)
            vector_revisions = self._add_vectors(builder, plan, work, job_id)
            if metadata_revisions != self._content_revisions(plan["knowledge_library_ids"]):
                raise ValueError("正式知识元数据在导出期间发生变化")
            if scope_revision != self._scope_revision(plan):
                raise ValueError("文档或知识依赖在导出期间发生变化")
            manifest = {
                "format": "dataforge-migration", "schema_version": 1, "package_kind": job["package_kind"],
                "package_id": package_id, "source_instance_id": self.instance.id,
                "project": plan["project"], "deployment": plan["deployment"],
                "project_deployment": plan["project_deployment"],
                "scope": {"deployment_count": 1, "knowledge_library_ids": plan["knowledge_library_ids"]},
                "collections": {name: [{"knowledge_library_id": item["knowledge_library_id"],
                    "partition_name": item["partition_name"], "index_profile_revision_id": item["index_profile_revision_id"],
                    "content_revision": vector_revisions[item["knowledge_library_id"]],
                    "metadata_revision": metadata_revisions[item["knowledge_library_id"]]}
                    for item in items] for name, items in plan["collections"].items()},
                "base_route_version": plan["base_route_version"],
            }
            result = builder.build(manifest)
            return self.store.update_migration_job(job_id, status="ready", stage="ready", package_path=result["path"],
                package_sha256=result["sha256"], package_id=package_id,
                checkpoint={**job["checkpoint"], "plan": plan, "package_id": package_id})
        except Exception as exc:
            self.store.update_migration_job(job_id, status="failed", stage="failed", error=str(exc))
            raise

    def _content_revisions(self, library_ids: list[str]) -> dict[str, str]:
        result = {}
        with self.store.sessions() as session:
            for library_id in library_ids:
                digest = hashlib.sha256()
                items = list(session.scalars(select(KnowledgeItem).where(
                    KnowledgeItem.knowledge_library_id == library_id).order_by(KnowledgeItem.id)))
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

    def _add_vectors(self, builder: MigrationPackageBuilder, plan: dict[str, Any], work: Path, job_id: str) -> dict[str, str]:
        revisions: dict[str, str] = {}
        for collection_name, items in plan["collections"].items():
            for item in items:
                output = work / "vectors" / collection_name / f'{item["partition_name"]}.parquet'
                before = self.milvus.verify_partition(collection_name, item["partition_name"])
                result = self.milvus.export_partition(collection_name, item["partition_name"], output, batch_size=1000)
                after = self.milvus.verify_partition(collection_name, item["partition_name"])
                if before != after or result["count"] != before["count"] or result["digest"] != before["digest"]:
                    raise ValueError(f"Partition 导出期间发生变化：{collection_name}/{item['partition_name']}")
                builder.add_file(f"vectors/{collection_name}/{item['partition_name']}.parquet", output)
                self.store.update_migration_item(job_id, item["knowledge_library_id"], collection_name,
                    source_count=result["count"], source_digest=result["digest"], status="exported")
                revisions[item["knowledge_library_id"]] = result["digest"]
        return revisions
