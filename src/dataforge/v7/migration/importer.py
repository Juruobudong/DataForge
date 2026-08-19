"""Checkpointed local import of a previously verified migration package."""
from __future__ import annotations

import json
import os
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import DateTime, delete, select

from ..instance import InstanceContext
from ..models import (
    DocumentLibrary, DocumentLibraryMember, EmbeddingProfile, KnowledgeIndexProfile,
    KnowledgeIndexProfileRevision, KnowledgeItem, KnowledgeItemSource, KnowledgeLibrary,
    Deployment, MilvusTarget, Project, ProjectDeployment, ProjectDeploymentTask, ProjectOrgRoute,
    ProjectOrgRouteLibrary, ProjectTask, Source, SourceChunk, SourceVersion, StorageContract,
    StorageContractRevision,
)
from ..provisioning import ManagedCollectionProvisioner
from ..routing import AtomicRoutingPublisher
from ..store import V7Store, new_id
from ..vector import V7Milvus
from .package import extract_verified_entry, inspect_package


METADATA_MODELS = {
    "document_libraries": DocumentLibrary, "sources": Source, "source_versions": SourceVersion,
    "source_chunks": SourceChunk, "document_library_members": DocumentLibraryMember,
    "knowledge_libraries": KnowledgeLibrary, "knowledge_items": KnowledgeItem,
    "knowledge_item_sources": KnowledgeItemSource,
}
CONTRACT_MODELS = {
    "embedding_profiles": EmbeddingProfile, "storage_contracts": StorageContract,
    "storage_contract_revisions": StorageContractRevision, "index_profiles": KnowledgeIndexProfile,
    "index_profile_revisions": KnowledgeIndexProfileRevision,
}


def _read_json(archive: zipfile.ZipFile, name: str) -> Any:
    return json.loads(archive.read(name))


def _read_jsonl(archive: zipfile.ZipFile, name: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in archive.read(name).splitlines() if line.strip()]


def _model_values(model, payload: dict[str, Any]) -> dict[str, Any]:
    result = {}
    for column in model.__table__.columns:
        if column.name not in payload: continue
        value = payload[column.name]
        if value is not None and isinstance(column.type, DateTime) and isinstance(value, str):
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        result[column.name] = value
    return result


def _upsert(session, model, payload: dict[str, Any], *, immutable: tuple[str, ...] = ()):
    values = _model_values(model, payload)
    current = session.get(model, values["id"])
    if current:
        for key in immutable:
            if getattr(current, key) != values.get(key): raise ValueError(f"{model.__tablename__} {values['id']} 定义不兼容")
        return current
    current = model(**values); session.add(current); return current


class MigrationImporter:
    STAGES = ("verified", "contracts_ready", "metadata_imported", "objects_imported",
              "collections_ready", "vectors_imported", "verified_vectors", "completed")

    def __init__(self, store: V7Store, objects, milvus: V7Milvus, *, migration_dir: Path,
                 trusted_public_keys: str, instance: InstanceContext, routing_dir: Path):
        self.store, self.objects, self.milvus = store, objects, milvus
        self.migration_dir, self.trusted_public_keys = migration_dir, trusted_public_keys
        self.instance, self.routing_dir = instance, routing_dir

    def run(self, job_id: str) -> dict[str, Any]:
        job = self.store.get_migration_job(job_id)
        if self.instance.mode != "local" or job["direction"] != "import": raise ValueError("只有 local 实例可以导入迁移包")
        path = Path(job["package_path"] or "")
        checkpoint = dict(job["checkpoint"] or {})
        try:
            inspected = inspect_package(path, self.trusted_public_keys)
            manifest = inspected["manifest"]
            if job["package_kind"] != manifest["package_kind"]: raise ValueError("任务与包的 package_kind 不一致")
            if manifest["package_kind"] == "deployment_seed" and self.instance.bound_deployment_id:
                raise ValueError("本地实例已经初始化，禁止第二次导入 deployment_seed")
            if manifest["package_kind"] == "knowledge_update" and not self.instance.bound_deployment_id:
                raise ValueError("未初始化的 local 实例不能导入 knowledge_update")
            checkpoint["verified"] = True
            self.store.update_migration_job(job_id, stage="verified", signature_status="verified", checkpoint=checkpoint)
            with zipfile.ZipFile(path, "r", allowZip64=True) as archive:
                control = _read_json(archive, "control/deployment.json")
                contracts = _read_json(archive, "contracts/contracts.json")
                metadata = {name: _read_jsonl(archive, f"metadata/{name}.jsonl") for name in METADATA_MODELS}
                decisions = {item["knowledge_library_id"]: item.get("resolution") for item in job["items"]}
                if checkpoint.get("id_maps"):
                    selected, id_maps = set(checkpoint.get("selected_library_ids", [])), checkpoint["id_maps"]
                else:
                    selected, id_maps = self._resolve_conflicts(metadata, decisions, manifest["package_kind"])
                    checkpoint["selected_library_ids"], checkpoint["id_maps"] = sorted(selected), id_maps
                    self.store.update_migration_job(job_id, stage="verified", checkpoint=checkpoint)
                if not checkpoint.get("contracts_ready"):
                    self._contracts(contracts)
                    checkpoint["contracts_ready"] = True
                    self.store.update_migration_job(job_id, stage="contracts_ready", checkpoint=checkpoint)
                self._preflight_collections(manifest, selected)
                if not checkpoint.get("metadata_imported"):
                    self._metadata(control, metadata, manifest, selected, id_maps, decisions)
                    checkpoint["metadata_imported"] = True
                    self.store.update_migration_job(job_id, stage="metadata_imported", checkpoint=checkpoint)
                if not checkpoint.get("objects_imported"):
                    self._objects(archive, metadata, selected, id_maps, manifest["source_instance_id"])
                    checkpoint["objects_imported"] = True
                    self.store.update_migration_job(job_id, stage="objects_imported", checkpoint=checkpoint)
                checkpoint["collections_ready"] = True
                self.store.update_migration_job(job_id, stage="collections_ready", checkpoint=checkpoint)
                if not checkpoint.get("verified_vectors"):
                    self._vectors(path, manifest, selected, id_maps, job_id)
            checkpoint.update({"vectors_imported": True, "verified_vectors": True})
            self._finish_libraries(selected, id_maps)
            if manifest["package_kind"] == "deployment_seed": self._finish_seed(control, manifest)
            checkpoint["completed"] = True
            return self.store.update_migration_job(job_id, status="completed", stage="completed", checkpoint=checkpoint)
        except Exception as exc:
            self.store.update_migration_job(job_id, status="failed", stage=job.get("stage") or "failed", checkpoint=checkpoint, error=str(exc))
            raise

    def _contracts(self, contracts: dict[str, list[dict]]) -> None:
        order = ("embedding_profiles", "storage_contracts", "storage_contract_revisions", "index_profiles", "index_profile_revisions")
        immutable = {"embedding_profiles": ("model", "dimension", "metric_type"),
            "storage_contract_revisions": ("storage_spec_hash",),
            "index_profile_revisions": ("collection_name", "storage_contract_revision_id")}
        with self.store.sessions.begin() as session:
            for name in order:
                for payload in contracts.get(name, []): _upsert(session, CONTRACT_MODELS[name], payload, immutable=immutable.get(name, ()))

    def _resolve_conflicts(self, metadata: dict[str, list[dict]], decisions: dict[str, str | None], package_kind: str):
        selected, id_maps = set(), {"libraries": {}, "items": {}, "document_libraries": {},
                                    "sources": {}, "versions": {}, "chunks": {}, "members": {}}
        with self.store.sessions() as session:
            for payload in metadata["knowledge_libraries"]:
                library_id = payload["id"]; current = session.get(KnowledgeLibrary, library_id)
                if package_kind == "deployment_seed" and current: raise ValueError("Seed 目标不是空知识域")
                if current and current.origin_type != "central_import": raise ValueError("中央资产 ID 与本地资产冲突")
                resolution = decisions.get(library_id)
                if current and current.origin_state == "forked":
                    if resolution not in {"keep_local", "replace_with_central", "import_as_new"}:
                        raise ValueError(f"知识库 {library_id} 已 forked，必须明确冲突处理")
                    if resolution == "keep_local": continue
                    if resolution == "import_as_new":
                        id_maps["libraries"][library_id] = new_id("klib")
                selected.add(library_id)
        cloned_libraries = set(id_maps["libraries"])
        cloned_item_ids = {item["id"] for item in metadata["knowledge_items"]
                           if item["knowledge_library_id"] in cloned_libraries}
        cloned_version_ids = {link["source_version_id"] for link in metadata["knowledge_item_sources"]
                              if link["knowledge_item_id"] in cloned_item_ids}
        cloned_source_ids = {version["source_id"] for version in metadata["source_versions"]
                             if version["id"] in cloned_version_ids}
        cloned_document_ids = {source["document_library_id"] for source in metadata["sources"]
                               if source["id"] in cloned_source_ids}
        for item_id in cloned_item_ids: id_maps["items"][item_id] = new_id("ki")
        for value in cloned_document_ids: id_maps["document_libraries"][value] = new_id("doclib")
        for value in cloned_source_ids: id_maps["sources"][value] = new_id("src")
        for value in cloned_version_ids: id_maps["versions"][value] = new_id("srcv")
        for chunk in metadata["source_chunks"]:
            if chunk["source_version_id"] in cloned_version_ids: id_maps["chunks"][chunk["id"]] = new_id("chunk")
        for member in metadata["document_library_members"]:
            if member["source_id"] in cloned_source_ids: id_maps["members"][member["id"]] = new_id("dlm")
        return selected, id_maps

    def _preflight_collections(self, manifest: dict[str, Any], selected: set[str]) -> None:
        provisioner = ManagedCollectionProvisioner(self.store, self.milvus)
        for _, partitions in manifest["collections"].items():
            for part in partitions:
                if part["knowledge_library_id"] in selected:
                    provisioner.ensure_collection_for_profile(part["index_profile_revision_id"])

    def _metadata(self, control, metadata, manifest, selected, id_maps, decisions) -> None:
        source_instance_id = manifest["source_instance_id"]
        selected_item_ids = {item["id"] for item in metadata["knowledge_items"]
                             if item["knowledge_library_id"] in selected}
        selected_version_ids = {link["source_version_id"] for link in metadata["knowledge_item_sources"]
                                if link["knowledge_item_id"] in selected_item_ids}
        selected_source_ids = {version["source_id"] for version in metadata["source_versions"]
                               if version["id"] in selected_version_ids}
        selected_document_ids = {source["document_library_id"] for source in metadata["sources"]
                                 if source["id"] in selected_source_ids}
        replaced_library_ids = {library_id for library_id in selected
                                if decisions.get(library_id) == "replace_with_central"}
        replaced_item_ids = {item["id"] for item in metadata["knowledge_items"]
                             if item["knowledge_library_id"] in replaced_library_ids}
        replaced_version_ids = {link["source_version_id"] for link in metadata["knowledge_item_sources"]
                                if link["knowledge_item_id"] in replaced_item_ids}
        replaced_source_ids = {version["source_id"] for version in metadata["source_versions"]
                               if version["id"] in replaced_version_ids}
        replaced_document_ids = {source["document_library_id"] for source in metadata["sources"]
                                 if source["id"] in replaced_source_ids}
        with self.store.sessions.begin() as session:
            if manifest["package_kind"] == "deployment_seed":
                _upsert(session, Project, control["project"])
                deployment_payload = dict(control["deployment"])
                deployment_payload["release_stage"] = "test"
                deployment = _upsert(session, Deployment, deployment_payload)
                local_uri = str(getattr(self.milvus, "uri", "") or os.getenv("DATAFORGE_MILVUS_URI", "")).strip()
                if not local_uri:
                    existing_target = session.scalar(select(MilvusTarget).order_by(MilvusTarget.created_at))
                    local_uri = str(existing_target.milvus_url if existing_target else "").strip()
                if not local_uri:
                    raise ValueError("local 实例没有 Milvus Target URI")
                self.store._put_stage_target(session, deployment, "test", local_uri)
                project_deployment = _upsert(session, ProjectDeployment, control["project_deployment"])
                for payload in control["tasks"]: _upsert(session, ProjectTask, payload)
                for payload in control["deployment_tasks"]: _upsert(session, ProjectDeploymentTask, payload)
                for payload in control["org_routes"]: _upsert(session, ProjectOrgRoute, payload)
                for payload in control["route_libraries"]:
                    if payload["knowledge_library_id"] in selected: _upsert(session, ProjectOrgRouteLibrary, payload)
            for payload in metadata["document_libraries"]:
                if payload["id"] not in selected_document_ids: continue
                payload = dict(payload)
                payload.update(origin_type="central_import", origin_instance_id=source_instance_id,
                               origin_asset_id=payload["id"], origin_state="synced")
                current_document = session.get(DocumentLibrary, payload["id"])
                if current_document and current_document.origin_type != "central_import":
                    raise ValueError(f"中央文档库 ID 与本地资产冲突：{payload['id']}")
                if current_document and (current_document.origin_state != "forked" or
                                         payload["id"] in replaced_document_ids):
                    for key, value in _model_values(DocumentLibrary, payload).items():
                        if key not in {"id", "created_at"}: setattr(current_document, key, value)
                elif not current_document:
                    session.add(DocumentLibrary(**_model_values(DocumentLibrary, payload)))
                if payload["id"] in id_maps["document_libraries"]:
                    cloned = dict(payload); cloned["id"] = id_maps["document_libraries"][payload["id"]]
                    cloned["code"] = f'{payload["code"]}-fork-{cloned["id"][-8:]}'
                    cloned["name"] = f'{payload["name"]}（中心新版副本）'
                    cloned.update(origin_type="local", origin_instance_id=None, origin_asset_id=None, origin_state=None)
                    _upsert(session, DocumentLibrary, cloned)
            for name in ("sources", "source_versions", "source_chunks", "document_library_members"):
                for payload in metadata[name]:
                    payload = dict(payload)
                    if name == "sources" and payload["id"] not in selected_source_ids: continue
                    if name in {"source_versions", "source_chunks"} and payload["source_version_id" if name == "source_chunks" else "id"] not in selected_version_ids: continue
                    if name == "document_library_members" and payload["source_id"] not in selected_source_ids: continue
                    if name == "source_chunks":
                        payload["origin_flow_run_id"], payload["flow_run_id"] = payload.get("flow_run_id"), None
                    if name == "source_versions": payload["object_key"] = f"migration/{source_instance_id}/{payload['id']}"
                    current = session.get(METADATA_MODELS[name], payload["id"])
                    if name == "sources" and current:
                        document = session.get(DocumentLibrary, payload["document_library_id"])
                        if document and (document.origin_state != "forked" or payload["id"] in replaced_source_ids):
                            for key, value in _model_values(Source, payload).items():
                                if key not in {"id", "created_at"}: setattr(current, key, value)
                    elif name == "source_versions":
                        _upsert(session, SourceVersion, payload, immutable=("source_id", "sha256", "mime_type", "size_bytes"))
                    else:
                        _upsert(session, METADATA_MODELS[name], payload)
                    mapping_name = {"sources": "sources", "source_versions": "versions",
                                    "source_chunks": "chunks", "document_library_members": "members"}[name]
                    if payload["id"] in id_maps[mapping_name]:
                        cloned = dict(payload); cloned["id"] = id_maps[mapping_name][payload["id"]]
                        if name == "sources": cloned["document_library_id"] = id_maps["document_libraries"][payload["document_library_id"]]
                        elif name == "source_versions":
                            cloned["source_id"] = id_maps["sources"][payload["source_id"]]
                            cloned["object_key"] = f"migration/{source_instance_id}/{cloned['id']}"
                        elif name == "source_chunks": cloned["source_version_id"] = id_maps["versions"][payload["source_version_id"]]
                        else:
                            cloned["document_library_id"] = id_maps["document_libraries"][payload["document_library_id"]]
                            cloned["source_id"] = id_maps["sources"][payload["source_id"]]
                        _upsert(session, METADATA_MODELS[name], cloned)
            item_library = {item["id"]: item["knowledge_library_id"] for item in metadata["knowledge_items"]}
            for payload in metadata["knowledge_libraries"]:
                old_id = payload["id"]
                if old_id not in selected: continue
                new_id_value = id_maps["libraries"].get(old_id, old_id)
                payload["id"], payload["partition_name"] = new_id_value, f"kl_{new_id_value}"
                if new_id_value != old_id:
                    payload["code"] = f'{payload["code"]}-fork-{new_id_value[-8:]}'
                    payload["name"] = f'{payload["name"]}（中心新版副本）'
                payload.update(origin_type="local" if new_id_value != old_id else "central_import",
                               origin_instance_id=None if new_id_value != old_id else source_instance_id,
                               origin_asset_id=None if new_id_value != old_id else old_id,
                               origin_state=None if new_id_value != old_id else "synced", status="migrating", migration_status="migrating")
                current = session.get(KnowledgeLibrary, new_id_value)
                if current:
                    for key, value in _model_values(KnowledgeLibrary, payload).items():
                        if key not in {"id", "created_at"}: setattr(current, key, value)
                    old_items = list(session.scalars(select(KnowledgeItem.id).where(KnowledgeItem.knowledge_library_id == new_id_value)))
                    if old_items: session.execute(delete(KnowledgeItemSource).where(KnowledgeItemSource.knowledge_item_id.in_(old_items)))
                    session.execute(delete(KnowledgeItem).where(KnowledgeItem.knowledge_library_id == new_id_value))
                else: session.add(KnowledgeLibrary(**_model_values(KnowledgeLibrary, payload)))
            session.flush()
            for payload in metadata["knowledge_items"]:
                old_library = payload["knowledge_library_id"]
                if old_library not in selected: continue
                old_item = payload["id"]
                if old_library in id_maps["libraries"]:
                    payload["id"] = id_maps["items"].setdefault(old_item, new_id("ki"))
                    payload["knowledge_library_id"] = id_maps["libraries"][old_library]
                session.add(KnowledgeItem(**_model_values(KnowledgeItem, payload)))
            session.flush()
            for payload in metadata["knowledge_item_sources"]:
                old_item = payload["knowledge_item_id"]
                library_id = item_library.get(old_item)
                if library_id not in selected: continue
                if old_item in id_maps["items"]:
                    payload["id"], payload["knowledge_item_id"] = new_id("kis"), id_maps["items"][old_item]
                    payload["source_version_id"] = id_maps["versions"].get(payload["source_version_id"], payload["source_version_id"])
                session.add(KnowledgeItemSource(**_model_values(KnowledgeItemSource, payload)))

    def _objects(self, archive, metadata, selected, id_maps, source_instance_id):
        version_by_source = {version["source_id"]: version for version in metadata["source_versions"]}
        for version in version_by_source.values():
            entry = f"objects/{version['id']}"
            with archive.open(entry) as source:
                stored = self.objects.put_stream(f"migration/{source_instance_id}/{version['id']}", source,
                    int(version["size_bytes"]), version["mime_type"])
                if stored.sha256 != version["sha256"]:
                    raise ValueError(f"恢复对象 SHA-256 不匹配：{version['id']}")
            if version["id"] in id_maps["versions"]:
                with archive.open(entry) as source:
                    stored = self.objects.put_stream(f"migration/{source_instance_id}/{id_maps['versions'][version['id']]}",
                        source, int(version["size_bytes"]), version["mime_type"])
                    if stored.sha256 != version["sha256"]: raise ValueError("副本对象 SHA-256 不匹配")

    def _vectors(self, package_path: Path, manifest: dict[str, Any], selected: set[str], id_maps, job_id: str) -> None:
        work = self.migration_dir / job_id / "import-vectors"; work.mkdir(parents=True, exist_ok=True)
        for collection, partitions in manifest["collections"].items():
            for part in partitions:
                library_id = part["knowledge_library_id"]
                if library_id not in selected: continue
                local = work / f"{collection}-{part['partition_name']}.parquet"
                extract_verified_entry(package_path, f"vectors/{collection}/{part['partition_name']}.parquet", local)
                target_partition = part["partition_name"]
                compare_central_digest = True
                if library_id in id_maps["libraries"]:
                    target_partition = f"kl_{id_maps['libraries'][library_id]}"
                    local = self._rewrite_vector_ids(local, library_id, id_maps)
                    compare_central_digest = False
                self.milvus.reset_partition(collection, target_partition)
                result = self.milvus.import_partition(collection, target_partition, local, batch_size=1000)
                verified = self.milvus.verify_partition(collection, target_partition)
                if result != verified or compare_central_digest and verified["digest"] != part["content_revision"]:
                    raise ValueError(f"Partition 完整性校验失败：{collection}/{target_partition}")
                self.milvus.load_partition(collection, target_partition)
                self.store.update_migration_item(job_id, library_id, collection, target_count=verified["count"],
                    target_digest=verified["digest"], status="verified")

    @staticmethod
    def _rewrite_vector_ids(path: Path, library_id: str, id_maps: dict[str, dict[str, str]]) -> Path:
        import hashlib
        import pyarrow as pa
        import pyarrow.parquet as pq
        table = pq.read_table(path); metadata = table.schema.metadata or {}
        primary = metadata.get(b"dataforge_primary_field", b"id").decode("utf-8")
        rows = table.to_pylist(); new_library_id = id_maps["libraries"][library_id]
        for row in rows:
            old_primary = str(row.get(primary, ""))
            row[primary] = hashlib.sha256(f"{new_library_id}|{old_primary}".encode()).hexdigest()
            for field in ("knowledge_library_id", "library_id"):
                if row.get(field) == library_id: row[field] = new_library_id
            for field in ("knowledge_item_id", "item_id"):
                if row.get(field) in id_maps["items"]: row[field] = id_maps["items"][row[field]]
        rewritten = path.with_name(path.stem + "-fork.parquet")
        output = pa.Table.from_pylist(rows).replace_schema_metadata(metadata)
        pq.write_table(output, rewritten, compression="zstd")
        return rewritten

    def _finish_libraries(self, selected, id_maps):
        with self.store.sessions.begin() as session:
            for old_id in selected:
                library = session.get(KnowledgeLibrary, id_maps["libraries"].get(old_id, old_id))
                library.status, library.migration_status = "active", "ready"

    def _finish_seed(self, control, manifest):
        project_deployment_id = control["project_deployment"]["id"]
        deployment_id = control["deployment"]["id"]
        baseline = control.get("route_baseline") or {}
        generated = self.store.routing_snapshot(project_deployment_id)
        # Central and local Target identifiers may differ; the authorization/task payload must not.
        comparable_generated = json.loads(json.dumps(generated))
        comparable_baseline = json.loads(json.dumps(baseline))
        for payload in (comparable_generated, comparable_baseline):
            payload.pop("milvus_target", None); payload.pop("deployment", None)
            payload.pop("release_stage", None)
        if comparable_generated.get("tasks") != comparable_baseline.get("tasks"):
            raise ValueError("Seed 导入后授权生成的 Routing 与 baseline 不一致")
        version = self.store.create_route_version(project_deployment_id, generated, status="draft", origin="central_seed")
        project_code = control["project"]["code"]; deployment_code = control["deployment"]["code"]
        checksum, object_key = AtomicRoutingPublisher(self.routing_dir).publish(
            project_code, deployment_code, version.version_no, version.snapshot_json,
            release_stage=version.release_stage)
        self.store.mark_route_published(version.id, checksum, object_key)
        self.instance = self.instance.bind_seed(self.store, deployment_id, manifest["source_instance_id"])
