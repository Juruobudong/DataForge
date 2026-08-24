"""Checkpointed local import of a previously verified migration package."""
from __future__ import annotations

import json
import hashlib
import os
import shutil
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import DateTime, delete, func, select

from ..instance import InstanceContext
from ..models import (
    DocumentLibrary, DocumentLibraryMember, DocumentLibraryProcessingBaseline,
    DocumentLibraryTemplateBinding, DocumentLibraryTemplateOutput, EmbeddingProfile,
    FlowExecutionSnapshot, FlowSubgraph, FlowSubgraphRevision, KnowledgeFlowTemplate,
    KnowledgeFlowTemplateRevision, KnowledgeIndexProfile, KnowledgeType,
    KnowledgeTypeIndexBinding, KnowledgeTypeModeRevision, KnowledgeTypeRevision,
    KnowledgeAssetVersion, KnowledgeIndexProfileRevision, KnowledgeItem, KnowledgeItemSource, KnowledgeLibrary,
    OperatorDefinition, OperatorVersion, PromptTemplate, PromptTemplateRevision,
    QualityProfile, QualityProfileRevision,
    Deployment, MilvusTarget, Project, ProjectDeployment, ProjectDeploymentTask, ProjectOrgRoute,
    ProjectOrgRouteLibrary, ProjectTask, Source, SourceChunk, SourceChunkRevision,
    SourceReviewSnapshot, SourceReviewSnapshotChunk, SourceVersion, StorageContract,
    StorageContractRevision,
    utc_now,
)
from ..local_config import LocalMilvusConfigurationService
from ..provisioning import ManagedCollectionProvisioner
from ..routing import AtomicRoutingPublisher
from ..store import V7Store, new_id
from ..vector import V7Milvus
from .package import extract_verified_entry, inspect_package


METADATA_MODELS = {
    "document_libraries": DocumentLibrary, "sources": Source, "source_versions": SourceVersion,
    "source_chunks": SourceChunk, "document_library_members": DocumentLibraryMember,
    "source_chunk_revisions": SourceChunkRevision,
    "source_review_snapshots": SourceReviewSnapshot,
    "source_review_snapshot_chunks": SourceReviewSnapshotChunk,
    "knowledge_libraries": KnowledgeLibrary, "knowledge_items": KnowledgeItem,
    "knowledge_item_sources": KnowledgeItemSource,
    "knowledge_types": KnowledgeType, "knowledge_type_revisions": KnowledgeTypeRevision,
    "knowledge_type_mode_revisions": KnowledgeTypeModeRevision,
    "knowledge_type_index_bindings": KnowledgeTypeIndexBinding,
    "operator_definitions": OperatorDefinition, "operator_versions": OperatorVersion,
    "prompt_templates": PromptTemplate, "prompt_template_revisions": PromptTemplateRevision,
    "quality_profiles": QualityProfile, "quality_profile_revisions": QualityProfileRevision,
    "flow_subgraphs": FlowSubgraph, "flow_subgraph_revisions": FlowSubgraphRevision,
    "knowledge_flow_templates": KnowledgeFlowTemplate,
    "knowledge_flow_template_revisions": KnowledgeFlowTemplateRevision,
    "flow_execution_snapshots": FlowExecutionSnapshot,
    "document_library_template_bindings": DocumentLibraryTemplateBinding,
    "document_library_template_outputs": DocumentLibraryTemplateOutput,
    "document_library_processing_baselines": DocumentLibraryProcessingBaseline,
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


def _optional_jsonl(archive: zipfile.ZipFile, name: str) -> list[dict[str, Any]]:
    return _read_jsonl(archive, name) if name in archive.namelist() else []


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
    STAGES = (
        "uploaded", "verified", "contracts_metadata_imported", "documents_objects_imported",
        "waiting_for_milvus_configuration", "waiting_for_milvus_verification",
        "waiting_for_vector_capacity", "ready_for_vector_import", "importing_vectors",
        "verified_vectors", "assets_ready", "route_candidates_ready", "completed",
    )

    def __init__(self, store: V7Store, objects, milvus: V7Milvus | None, *, migration_dir: Path,
                 trusted_public_keys: str, instance: InstanceContext, routing_dir: Path,
                 local_config: LocalMilvusConfigurationService | None = None):
        self.store, self.objects, self.milvus = store, objects, milvus
        self.migration_dir, self.trusted_public_keys = migration_dir, trusted_public_keys
        self.instance, self.routing_dir = instance, routing_dir
        self.local_config = local_config
        self.migration_dir.mkdir(parents=True, exist_ok=True)

    def run(self, job_id: str) -> dict[str, Any]:
        job = self.store.get_migration_job(job_id)
        if self.instance.mode != "local" or job["direction"] != "import": raise ValueError("只有 local 实例可以导入迁移包")
        path = Path(job["package_path"] or "")
        checkpoint = dict(job["checkpoint"] or {})
        try:
            inspected = inspect_package(path, self.trusted_public_keys)
            manifest = inspected["manifest"]
            self._static_preflight(manifest)
            if job["package_kind"] != manifest["package_kind"]: raise ValueError("任务与包的 package_kind 不一致")
            if manifest["package_kind"] == "deployment_seed" and self.instance.bound_deployment_id:
                raise ValueError("本地实例已经初始化，禁止第二次导入 deployment_seed")
            if manifest["package_kind"] == "knowledge_update" and not self.instance.bound_deployment_id:
                raise ValueError("未初始化的 local 实例不能导入 knowledge_update")
            checkpoint["verified"] = True
            self.store.update_migration_job(job_id, stage="verified", signature_status="verified", checkpoint=checkpoint)
            with zipfile.ZipFile(path, "r", allowZip64=True) as archive:
                schema_version = int(manifest.get("manifest_schema_version") or manifest.get("schema_version", 1))
                control = _read_json(archive, "control/release.json" if schema_version == 2
                                     else "control/deployment.json")
                contracts = _read_json(archive, "contracts/contracts.json")
                metadata = {name: _optional_jsonl(archive, f"metadata/{name}.jsonl")
                            for name in METADATA_MODELS}
                self._package_content_preflight(manifest, contracts, metadata)
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
                    self.store.update_migration_job(job_id, stage="contracts_metadata_imported", checkpoint=checkpoint)
                if not checkpoint.get("metadata_imported"):
                    self._metadata(control, metadata, manifest, selected, id_maps, decisions)
                    if schema_version == 2:
                        self._apply_tombstones(manifest, selected)
                    checkpoint["metadata_imported"] = True
                    self.store.update_migration_job(job_id, stage="contracts_metadata_imported", checkpoint=checkpoint)
                disk_capacity = self._disk_capacity(path, metadata)
                checkpoint["disk_capacity"] = disk_capacity
                if not disk_capacity["sufficient"]:
                    return self._wait(job_id, checkpoint, "waiting_for_vector_capacity")
                if not checkpoint.get("objects_imported"):
                    self._objects(archive, metadata, selected, id_maps, manifest["source_instance_id"])
                    checkpoint["objects_imported"] = True
                    self.store.update_migration_job(job_id, stage="documents_objects_imported", checkpoint=checkpoint)
                if schema_version == 2:
                    self._register_package_preset(manifest)
                    self._prepare_asset_versions(manifest, selected, id_maps, job_id)
                    waiting = self._resolve_import_target(job_id, checkpoint)
                    if waiting:
                        return waiting
                elif self.milvus is None:
                    raise ValueError("v1 migration import 缺少 Milvus Target")
                self._prepare_collections(manifest, selected)
                if self._vector_capacity_blocked(manifest, selected):
                    return self._wait(job_id, checkpoint, "waiting_for_vector_capacity")
                checkpoint["ready_for_vector_import"] = True
                self.store.update_migration_job(job_id, stage="ready_for_vector_import", checkpoint=checkpoint)
                if not checkpoint.get("verified_vectors"):
                    self.store.update_migration_job(job_id, stage="importing_vectors", checkpoint=checkpoint)
                    self._vectors(path, manifest, selected, id_maps, job_id)
            checkpoint.update({"vectors_imported": True, "verified_vectors": True})
            self.store.update_migration_job(job_id, stage="verified_vectors", checkpoint=checkpoint)
            if schema_version == 2:
                self._finish_asset_versions(manifest, selected, id_maps, job_id)
                checkpoint["assets_ready"] = True
                if checkpoint.get("active_target_fingerprint"):
                    checkpoint["prepared_target_fingerprint"] = checkpoint["active_target_fingerprint"]
                    checkpoint["prepared_target_uri"] = checkpoint.get("active_target_uri")
                self.store.update_migration_job(job_id, stage="assets_ready", checkpoint=checkpoint)
            self._finish_libraries(selected, id_maps)
            if schema_version == 2 and manifest["package_kind"] != "knowledge_update":
                projects = self._local_candidate_projects(manifest.get("projects") or [], id_maps)
                self.store.create_imported_route_candidates(job_id, projects)
                checkpoint["route_candidates_ready"] = True
                self.store.update_migration_job(job_id, stage="route_candidates_ready", checkpoint=checkpoint)
            if manifest["package_kind"] == "deployment_seed":
                if schema_version == 1:
                    self._finish_seed(control, manifest)
                else:
                    deployment_id = manifest["deployment"]["id"]
                    if not self.instance.bound_deployment_id:
                        self.instance = self.instance.bind_seed(
                            self.store, deployment_id, manifest["source_instance_id"],
                        )
                    with self.store.sessions.begin() as session:
                        deployment = session.get(Deployment, deployment_id, with_for_update=True)
                        if deployment and not deployment.institution_code_locked_at:
                            deployment.institution_code_locked_at = utc_now()
            checkpoint["completed"] = True
            return self.store.update_migration_job(job_id, status="completed", stage="completed", checkpoint=checkpoint)
        except Exception as exc:
            current = self.store.get_migration_job(job_id)
            self.store.update_migration_job(
                job_id, status="failed", stage=current.get("stage") or "failed",
                checkpoint=checkpoint, error=str(exc),
            )
            raise

    @staticmethod
    def _version_tuple(value: str) -> tuple[int, ...]:
        parts = []
        for item in str(value).split("."):
            digits = "".join(character for character in item if character.isdigit())
            parts.append(int(digits or 0))
        return tuple(parts)

    def _static_preflight(self, manifest: dict[str, Any]) -> None:
        schema_version = int(manifest.get("manifest_schema_version") or manifest.get("schema_version", 1))
        if schema_version != 2:
            return
        from ... import __version__ as dataforge_version
        current = self._version_tuple(dataforge_version)
        if current < self._version_tuple(manifest["minimum_dataforge_version"]):
            raise ValueError("本地 DataForge 版本低于 migration package 最低版本")
        if current > self._version_tuple(manifest["maximum_dataforge_version"]):
            raise ValueError("本地 DataForge 版本高于 migration package 最高版本")
        supported = {"immutable_asset_versions", "multi_project_release", "resumable_import"}
        missing = set(manifest.get("required_features") or []) - supported
        if missing:
            raise ValueError("本地缺少 migration package 所需功能：" + ", ".join(sorted(missing)))

    @staticmethod
    def _package_content_preflight(manifest: dict[str, Any], contracts: dict[str, list[dict]],
                                   metadata: dict[str, list[dict[str, Any]]]) -> None:
        schema_version = int(manifest.get("manifest_schema_version") or manifest.get("schema_version", 1))
        if schema_version != 2:
            return
        manifest_library_ids = set(manifest["scope"]["knowledge_library_ids"])
        metadata_library_ids = {item["id"] for item in metadata["knowledge_libraries"]}
        if manifest_library_ids != metadata_library_ids:
            raise ValueError("Manifest 与知识库元数据闭包不一致")
        asset_ids = {item["id"] for item in manifest.get("asset_versions") or []}
        partition_asset_ids = {
            item.get("asset_version_id") or item.get("id")
            for partitions in manifest["collections"].values() for item in partitions
        }
        if asset_ids != partition_asset_ids:
            raise ValueError("Manifest AssetVersion 与 Partition 闭包不一致")
        contract_revision_ids = {item["id"] for item in contracts.get("storage_contract_revisions", [])}
        required_contract_ids = set(manifest.get("storage_contract_versions") or [])
        if not required_contract_ids.issubset(contract_revision_ids):
            raise ValueError("migration package 缺少所需 Storage Contract Revision")
        profile_revision_ids = {item["id"] for item in contracts.get("index_profile_revisions", [])}
        required_profile_ids = {item["index_profile_revision_id"] for item in manifest.get("asset_versions") or []}
        if not required_profile_ids.issubset(profile_revision_ids):
            raise ValueError("migration package 缺少 AssetVersion 的 Index Profile Revision")
        definitions = {item["id"]: item["code"] for item in metadata.get("operator_definitions", [])}
        available_operators = {(definitions.get(item["operator_definition_id"]), int(item["version_no"]))
                               for item in metadata.get("operator_versions", [])}
        required_operators = {(item["code"], int(item["version"]))
                              for item in manifest.get("operator_versions") or []}
        if not required_operators.issubset(available_operators):
            raise ValueError("migration package 缺少所需 OperatorVersion")

    def _register_package_preset(self, manifest: dict[str, Any]) -> None:
        if not self.local_config:
            return
        preset = (manifest.get("deployment") or {}).get("milvus_preset") or {}
        uri = str(preset.get("uri") or "").strip()
        if uri:
            self.local_config.put(self.instance.id, "package_preset", uri=uri, preserve_secret=False)

    def _resolve_import_target(self, job_id: str, checkpoint: dict[str, Any]) -> dict[str, Any] | None:
        if not self.local_config:
            if self.milvus is None:
                return self._wait(job_id, checkpoint, "waiting_for_milvus_configuration")
            return None
        slot = str(checkpoint.get("selected_import_target") or "")
        configurations = {item["slot"]: item for item in self.local_config.list(self.instance.id)}
        if not slot:
            slot = next((name for name in ("candidate_target", "current_target")
                         if name in configurations), "")
        if not slot or slot not in {"candidate_target", "current_target"}:
            return self._wait(job_id, checkpoint, "waiting_for_milvus_configuration")
        checkpoint["selected_import_target"] = slot
        configuration = configurations.get(slot)
        if not configuration:
            return self._wait(job_id, checkpoint, "waiting_for_milvus_configuration")
        if configuration["status"] != "verified":
            return self._wait(job_id, checkpoint, "waiting_for_milvus_verification")
        target = self.local_config.resolve(self.instance.id, slot)
        if not target:
            return self._wait(job_id, checkpoint, "waiting_for_milvus_configuration")
        if target.fingerprint != configuration["verified_fingerprint"]:
            return self._wait(job_id, checkpoint, "waiting_for_milvus_verification")
        checkpoint["active_target_fingerprint"] = target.fingerprint
        checkpoint["active_target_uri"] = target.uri
        self.milvus = V7Milvus(target.uri, target.token)
        if not hasattr(self.milvus, "uri"):
            setattr(self.milvus, "uri", target.uri)
        return None

    def _wait(self, job_id: str, checkpoint: dict[str, Any], stage: str) -> dict[str, Any]:
        checkpoint[stage] = True
        return self.store.update_migration_job(
            job_id, status="waiting", stage=stage, checkpoint=checkpoint, error=None,
        )

    def _vector_capacity_blocked(self, manifest: dict[str, Any], selected: set[str]) -> bool:
        if not self.milvus or not hasattr(self.milvus, "capacity"):
            return False
        for collection, partitions in manifest["collections"].items():
            if not any(item["knowledge_library_id"] in selected for item in partitions):
                continue
            try:
                if self.milvus.capacity(collection).alert:
                    return True
            except Exception:
                # Collection validation/provisioning has already succeeded. A
                # backend without capacity telemetry must not fabricate a red state.
                continue
        return False

    def _disk_capacity(self, package_path: Path, metadata: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
        package_size = int(package_path.stat().st_size)
        object_size = sum(int(item.get("size_bytes") or 0) for item in metadata.get("source_versions", []))
        # Keep room for verified vector extraction plus restored objects. The
        # package itself is already present and is therefore not counted as free.
        required_free = package_size + object_size
        usage = shutil.disk_usage(self.migration_dir)
        return {"package_size_bytes": package_size, "object_size_bytes": object_size,
                "required_free_bytes": required_free, "available_bytes": int(usage.free),
                "sufficient": int(usage.free) >= required_free}

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
                                    "sources": {}, "versions": {}, "chunks": {}, "chunk_revisions": {},
                                    "review_snapshots": {}, "review_snapshot_chunks": {}, "members": {},
                                    "bindings": {}, "outputs": {}, "baselines": {},
                                    "asset_versions": {}, "partitions": {},
                                    "template_revisions": {}, "prompt_revisions": {},
                                    "quality_revisions": {}, "subgraph_revisions": {},
                                    "execution_snapshots": {}, "knowledge_types": {},
                                    "knowledge_type_revisions": {}}
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
        cloned_source_ids.update(source["id"] for source in metadata["sources"]
                                 if source["document_library_id"] in cloned_document_ids)
        cloned_version_ids.update(version["id"] for version in metadata["source_versions"]
                                  if version["source_id"] in cloned_source_ids)
        for item_id in cloned_item_ids: id_maps["items"][item_id] = new_id("ki")
        for value in cloned_document_ids: id_maps["document_libraries"][value] = new_id("doclib")
        for value in cloned_source_ids: id_maps["sources"][value] = new_id("src")
        for value in cloned_version_ids: id_maps["versions"][value] = new_id("srcv")
        for chunk in metadata["source_chunks"]:
            if chunk["source_version_id"] in cloned_version_ids: id_maps["chunks"][chunk["id"]] = new_id("chunk")
        for revision in metadata.get("source_chunk_revisions", []):
            if revision["source_chunk_id"] in id_maps["chunks"]:
                id_maps["chunk_revisions"][revision["id"]] = new_id("schrev")
        for snapshot in metadata.get("source_review_snapshots", []):
            if snapshot["source_version_id"] in cloned_version_ids:
                id_maps["review_snapshots"][snapshot["id"]] = new_id("review")
        for mapping in metadata.get("source_review_snapshot_chunks", []):
            if mapping["source_review_snapshot_id"] in id_maps["review_snapshots"]:
                id_maps["review_snapshot_chunks"][mapping["id"]] = new_id("reviewchunk")
        for member in metadata["document_library_members"]:
            if member["source_id"] in cloned_source_ids: id_maps["members"][member["id"]] = new_id("dlm")
        for binding in metadata["document_library_template_bindings"]:
            if binding["document_library_id"] in cloned_document_ids:
                id_maps["bindings"][binding["id"]] = new_id("docbind")
        for output in metadata["document_library_template_outputs"]:
            if output["document_library_template_binding_id"] in id_maps["bindings"]:
                id_maps["outputs"][output["id"]] = new_id("docout")
        for baseline in metadata["document_library_processing_baselines"]:
            if baseline["document_library_template_binding_id"] in id_maps["bindings"]:
                id_maps["baselines"][baseline["id"]] = new_id("baseline")
        return selected, id_maps

    def _prepare_collections(self, manifest: dict[str, Any], selected: set[str]) -> None:
        if not self.milvus:
            raise ValueError("Milvus Target 尚未配置")
        provisioner = ManagedCollectionProvisioner(self.store, self.milvus)
        for _, partitions in manifest["collections"].items():
            for part in partitions:
                if part["knowledge_library_id"] in selected:
                    provisioner.ensure_collection_for_profile(part["index_profile_revision_id"])

    def _metadata(self, control, metadata, manifest, selected, id_maps, decisions) -> None:
        source_instance_id = manifest["source_instance_id"]
        for key in ("chunk_revisions", "review_snapshots", "review_snapshot_chunks"):
            id_maps.setdefault(key, {})
        selected_item_ids = {item["id"] for item in metadata["knowledge_items"]
                             if item["knowledge_library_id"] in selected}
        selected_version_ids = {link["source_version_id"] for link in metadata["knowledge_item_sources"]
                                if link["knowledge_item_id"] in selected_item_ids}
        selected_source_ids = {version["source_id"] for version in metadata["source_versions"]
                               if version["id"] in selected_version_ids}
        selected_document_ids = {source["document_library_id"] for source in metadata["sources"]
                                 if source["id"] in selected_source_ids}
        # When the package carries the complete associated document library,
        # retain every current/non-deleted source version in that selected
        # container, including files that are not direct evidence of one item.
        selected_source_ids.update(source["id"] for source in metadata["sources"]
                                   if source["document_library_id"] in selected_document_ids)
        selected_version_ids.update(version["id"] for version in metadata["source_versions"]
                                    if version["source_id"] in selected_source_ids)
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
            self._runtime_parents(session, metadata, id_maps)
            schema_version = int(manifest.get("manifest_schema_version") or manifest.get("schema_version", 1))
            if schema_version == 2:
                self._v2_project_boundaries(session, control, manifest)
            if manifest["package_kind"] == "deployment_seed" and schema_version == 1:
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
                            if cloned.get("current_review_snapshot_id"):
                                cloned["current_review_snapshot_id"] = id_maps["review_snapshots"].get(
                                    cloned["current_review_snapshot_id"], cloned["current_review_snapshot_id"],
                                )
                        elif name == "source_chunks":
                            cloned["source_version_id"] = id_maps["versions"][payload["source_version_id"]]
                            if cloned.get("current_revision_id"):
                                cloned["current_revision_id"] = id_maps["chunk_revisions"].get(
                                    cloned["current_revision_id"], cloned["current_revision_id"],
                                )
                        else:
                            cloned["document_library_id"] = id_maps["document_libraries"][payload["document_library_id"]]
                            cloned["source_id"] = id_maps["sources"][payload["source_id"]]
                        _upsert(session, METADATA_MODELS[name], cloned)
            selected_chunk_ids = {item["id"] for item in metadata["source_chunks"]
                                  if item["source_version_id"] in selected_version_ids}
            selected_snapshot_ids = {item["id"] for item in metadata.get("source_review_snapshots", [])
                                     if item["source_version_id"] in selected_version_ids}
            for payload in metadata.get("source_chunk_revisions", []):
                if payload["source_chunk_id"] not in selected_chunk_ids: continue
                payload = dict(payload)
                _upsert(session, SourceChunkRevision, payload)
                if payload["id"] in id_maps["chunk_revisions"]:
                    cloned = dict(payload)
                    cloned["id"] = id_maps["chunk_revisions"][payload["id"]]
                    cloned["source_chunk_id"] = id_maps["chunks"][payload["source_chunk_id"]]
                    cloned["parent_chunk_ids"] = [id_maps["chunks"].get(value, value) for value in cloned.get("parent_chunk_ids") or []]
                    _upsert(session, SourceChunkRevision, cloned)
            for payload in metadata.get("source_review_snapshots", []):
                if payload["id"] not in selected_snapshot_ids: continue
                payload = dict(payload); _upsert(session, SourceReviewSnapshot, payload)
                if payload["id"] in id_maps["review_snapshots"]:
                    cloned = dict(payload)
                    cloned["id"] = id_maps["review_snapshots"][payload["id"]]
                    cloned["source_version_id"] = id_maps["versions"][payload["source_version_id"]]
                    _upsert(session, SourceReviewSnapshot, cloned)
            for payload in metadata.get("source_review_snapshot_chunks", []):
                if payload["source_review_snapshot_id"] not in selected_snapshot_ids: continue
                payload = dict(payload); _upsert(session, SourceReviewSnapshotChunk, payload)
                if payload["id"] in id_maps["review_snapshot_chunks"]:
                    cloned = dict(payload)
                    cloned["id"] = id_maps["review_snapshot_chunks"][payload["id"]]
                    cloned["source_review_snapshot_id"] = id_maps["review_snapshots"][payload["source_review_snapshot_id"]]
                    cloned["source_chunk_id"] = id_maps["chunks"][payload["source_chunk_id"]]
                    cloned["source_chunk_revision_id"] = id_maps["chunk_revisions"][payload["source_chunk_revision_id"]]
                    _upsert(session, SourceReviewSnapshotChunk, cloned)
            self._runtime_bindings_and_baselines(
                session, metadata, selected_document_ids, selected_version_ids, id_maps,
            )
            item_library = {item["id"]: item["knowledge_library_id"] for item in metadata["knowledge_items"]}
            for payload in metadata["knowledge_libraries"]:
                old_id = payload["id"]
                if old_id not in selected: continue
                new_id_value = id_maps["libraries"].get(old_id, old_id)
                payload = dict(payload)
                if payload.get("knowledge_type_revision_id"):
                    payload["knowledge_type_revision_id"] = id_maps["knowledge_type_revisions"].get(
                        payload["knowledge_type_revision_id"], payload["knowledge_type_revision_id"],
                    )
                if payload.get("source_template_revision_id"):
                    payload["source_template_revision_id"] = id_maps["template_revisions"].get(
                        payload["source_template_revision_id"], payload["source_template_revision_id"],
                    )
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
            self._runtime_outputs(session, metadata, selected, id_maps)
            for payload in metadata["knowledge_items"]:
                payload = dict(payload)
                old_library = payload["knowledge_library_id"]
                if old_library not in selected: continue
                if payload.get("knowledge_type_revision_id"):
                    payload["knowledge_type_revision_id"] = id_maps["knowledge_type_revisions"].get(
                        payload["knowledge_type_revision_id"], payload["knowledge_type_revision_id"],
                    )
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
                    if payload.get("source_chunk_revision_id"):
                        payload["source_chunk_revision_id"] = id_maps["chunk_revisions"].get(
                            payload["source_chunk_revision_id"], payload["source_chunk_revision_id"],
                        )
                    if payload.get("source_review_snapshot_id"):
                        payload["source_review_snapshot_id"] = id_maps["review_snapshots"].get(
                            payload["source_review_snapshot_id"], payload["source_review_snapshot_id"],
                        )
                session.add(KnowledgeItemSource(**_model_values(KnowledgeItemSource, payload)))

    @staticmethod
    def _v2_project_boundaries(session, control: dict[str, Any], manifest: dict[str, Any]) -> None:
        deployment_payload = dict(manifest["deployment"])
        deployment_payload.pop("milvus_preset", None)
        deployment_payload.setdefault("scope", "institution")
        deployment_payload.setdefault("release_stage", "test")
        deployment_payload.setdefault("status", "active")
        _upsert(session, Deployment, deployment_payload, immutable=("institution_code", "scope"))
        for entry in control.get("projects") or manifest.get("projects") or []:
            project_payload = dict(entry.get("project") or {})
            project_payload.setdefault("status", "active")
            project = _upsert(session, Project, project_payload, immutable=("code",))
            snapshot = dict(entry.get("route_snapshot") or {})
            project_deployment_payload = dict(snapshot.get("project_deployment") or {})
            project_deployment_payload.update({
                "id": entry["project_deployment_id"], "project_id": project.id,
                "deployment_id": deployment_payload["id"],
            })
            project_deployment_payload.setdefault("status", "active")
            _upsert(session, ProjectDeployment, project_deployment_payload,
                    immutable=("project_id", "deployment_id"))
            for task in snapshot.get("tasks") or []:
                project_task_payload = {
                    "id": task["task_id"], "project_id": project.id,
                    "code": task["task_code"], "name": task.get("task_name") or task["task_code"],
                    "knowledge_type": task.get("knowledge_type"), "description": "", "status": "active",
                }
                _upsert(session, ProjectTask, project_task_payload, immutable=("project_id", "code"))
                profile = task.get("index_profile") or {}
                deployment_task_payload = {
                    "id": task["deployment_task_id"],
                    "project_deployment_id": entry["project_deployment_id"],
                    "project_task_id": task["task_id"],
                    "index_profile_id": profile.get("index_profile_id"),
                    "qa_embedding_mode": task.get("qa_embedding_mode"),
                    "top_k": int(task.get("top_k", 10)), "enabled": True,
                }
                _upsert(session, ProjectDeploymentTask, deployment_task_payload,
                        immutable=("project_deployment_id", "project_task_id", "index_profile_id"))

    @staticmethod
    def _runtime_parents(session, metadata: dict[str, list[dict[str, Any]]],
                         id_maps: dict[str, dict[str, str]]) -> None:
        for name in ("operator_definitions", "prompt_templates", "quality_profiles",
                     "flow_subgraphs", "knowledge_flow_templates"):
            for payload in metadata.get(name, []):
                _upsert(session, METADATA_MODELS[name], payload, immutable=("code",))

        def natural_revision(name: str, parent_key: str, revision_model, map_name: str,
                             immutable: tuple[str, ...]) -> None:
            for raw in metadata.get(name, []):
                payload = dict(raw)
                current = session.scalar(select(revision_model).where(
                    getattr(revision_model, parent_key) == payload[parent_key],
                    revision_model.revision_no == int(payload["revision_no"]),
                ))
                if current:
                    for key in immutable:
                        if getattr(current, key) != payload.get(key):
                            raise ValueError(f"{revision_model.__tablename__} v{payload['revision_no']} 定义不兼容")
                    id_maps[map_name][payload["id"]] = current.id
                else:
                    value = _upsert(session, revision_model, payload)
                    id_maps[map_name][payload["id"]] = value.id

        natural_revision(
            "prompt_template_revisions", "prompt_template_id", PromptTemplateRevision,
            "prompt_revisions", ("body", "input_schema", "output_schema"),
        )
        natural_revision(
            "quality_profile_revisions", "quality_profile_id", QualityProfileRevision,
            "quality_revisions", ("rules_json",),
        )
        for raw in metadata.get("knowledge_types", []):
            current = session.scalar(select(KnowledgeType).where(KnowledgeType.code == raw["code"]))
            if current:
                if current.kind != raw.get("kind"):
                    raise ValueError(f"KnowledgeType {raw['code']} 定义不兼容")
                id_maps["knowledge_types"][raw["id"]] = current.id
            else:
                payload = dict(raw); payload["current_revision_id"] = None
                value = _upsert(session, KnowledgeType, payload)
                id_maps["knowledge_types"][raw["id"]] = value.id
        for raw in metadata.get("knowledge_type_revisions", []):
            payload = dict(raw)
            payload["knowledge_type_id"] = id_maps["knowledge_types"].get(
                payload["knowledge_type_id"], payload["knowledge_type_id"],
            )
            if payload.get("quality_profile_revision_id"):
                payload["quality_profile_revision_id"] = id_maps["quality_revisions"].get(
                    payload["quality_profile_revision_id"], payload["quality_profile_revision_id"],
                )
            current = session.scalar(select(KnowledgeTypeRevision).where(
                KnowledgeTypeRevision.knowledge_type_id == payload["knowledge_type_id"],
                KnowledgeTypeRevision.revision_no == int(payload["revision_no"]),
            ))
            if current:
                for key in ("schema_json", "canonical_field", "identity_fields", "source_policy",
                            "quality_profile_revision_id"):
                    if getattr(current, key) != payload.get(key):
                        raise ValueError(f"KnowledgeTypeRevision v{payload['revision_no']} 定义不兼容")
                revision_id = current.id
            else:
                revision_id = _upsert(session, KnowledgeTypeRevision, payload).id
            id_maps["knowledge_type_revisions"][raw["id"]] = revision_id
        for raw in metadata.get("knowledge_type_mode_revisions", []):
            payload = dict(raw)
            payload["knowledge_type_revision_id"] = id_maps["knowledge_type_revisions"].get(
                payload["knowledge_type_revision_id"], payload["knowledge_type_revision_id"],
            )
            current = session.scalar(select(KnowledgeTypeModeRevision).where(
                KnowledgeTypeModeRevision.knowledge_type_revision_id == payload["knowledge_type_revision_id"],
                KnowledgeTypeModeRevision.mode == payload["mode"],
                KnowledgeTypeModeRevision.revision_no == int(payload["revision_no"]),
            ))
            if current:
                for key in ("schema_json", "canonical_fields", "identity_fields", "source_policy"):
                    if getattr(current, key) != payload.get(key):
                        raise ValueError(f"KnowledgeTypeModeRevision {payload['mode']} 定义不兼容")
            else:
                _upsert(session, KnowledgeTypeModeRevision, payload)
        for raw in metadata.get("knowledge_type_index_bindings", []):
            payload = dict(raw)
            payload["knowledge_type_revision_id"] = id_maps["knowledge_type_revisions"].get(
                payload["knowledge_type_revision_id"], payload["knowledge_type_revision_id"],
            )
            current = session.scalar(select(KnowledgeTypeIndexBinding).where(
                KnowledgeTypeIndexBinding.knowledge_type_revision_id == payload["knowledge_type_revision_id"],
                KnowledgeTypeIndexBinding.index_profile_id == payload["index_profile_id"],
            ))
            if current:
                if (current.index_profile_revision_id != payload.get("index_profile_revision_id") or
                        current.field_path != payload.get("field_path") or current.role != payload.get("role")):
                    raise ValueError("KnowledgeTypeIndexBinding 定义不兼容")
            else:
                _upsert(session, KnowledgeTypeIndexBinding, payload)
        for raw in metadata.get("knowledge_types", []):
            value = session.get(KnowledgeType, id_maps["knowledge_types"].get(raw["id"], raw["id"]))
            if value and raw.get("current_revision_id"):
                value.current_revision_id = id_maps["knowledge_type_revisions"].get(
                    raw["current_revision_id"], raw["current_revision_id"],
                )
        natural_revision(
            "flow_subgraph_revisions", "flow_subgraph_id", FlowSubgraphRevision,
            "subgraph_revisions", ("definition_json", "input_contract", "output_contract"),
        )

        def rewrite_refs(value: Any) -> Any:
            if isinstance(value, dict):
                result = {}
                for key, child in value.items():
                    if key == "prompt_template_revision_id" and isinstance(child, str):
                        result[key] = id_maps["prompt_revisions"].get(child, child)
                    elif key == "quality_profile_revision_id" and isinstance(child, str):
                        result[key] = id_maps["quality_revisions"].get(child, child)
                    else:
                        result[key] = rewrite_refs(child)
                return result
            if isinstance(value, list):
                return [rewrite_refs(child) for child in value]
            return value

        for raw in metadata.get("knowledge_flow_template_revisions", []):
            payload = dict(raw)
            payload["definition_json"] = rewrite_refs(payload.get("definition_json") or {})
            payload["execution_snapshot_id"] = None
            current = session.scalar(select(KnowledgeFlowTemplateRevision).where(
                KnowledgeFlowTemplateRevision.knowledge_flow_template_id == payload["knowledge_flow_template_id"],
                KnowledgeFlowTemplateRevision.revision_no == int(payload["revision_no"]),
            ))
            if current:
                if current.definition_json != payload["definition_json"]:
                    raise ValueError(f"KnowledgeFlowTemplateRevision v{payload['revision_no']} 定义不兼容")
                revision_id = current.id
            else:
                revision_id = _upsert(session, KnowledgeFlowTemplateRevision, payload).id
            id_maps["template_revisions"][raw["id"]] = revision_id

        for payload in metadata.get("operator_versions", []):
            current = session.scalar(select(OperatorVersion).where(
                OperatorVersion.operator_definition_id == payload["operator_definition_id"],
                OperatorVersion.version_no == int(payload["version_no"]),
            ))
            if current:
                for key in ("adapter_code", "input_ports", "output_ports", "parameter_schema",
                            "runtime_requirements"):
                    if getattr(current, key) != payload.get(key):
                        raise ValueError(f"OperatorVersion {current.operator_definition_id}/v{current.version_no} 定义不兼容")
            else:
                _upsert(session, OperatorVersion, payload)
        for payload in metadata.get("flow_execution_snapshots", []):
            payload = dict(payload)
            source_snapshot_id = payload["id"]
            source_revision_id = payload["knowledge_flow_template_revision_id"]
            payload["knowledge_flow_template_revision_id"] = id_maps["template_revisions"].get(
                source_revision_id, source_revision_id,
            )
            payload["compiled_definition_json"] = rewrite_refs(payload.get("compiled_definition_json") or {})
            rewritten = payload["knowledge_flow_template_revision_id"] != source_revision_id
            if rewritten:
                compiled_checksum = hashlib.sha256(json.dumps(
                    payload["compiled_definition_json"], ensure_ascii=False, sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")).hexdigest()
                payload["dependency_json"] = dict(payload.get("dependency_json") or {})
                payload["dependency_json"]["source_checksum"] = compiled_checksum
                payload["checksum"] = hashlib.sha256(
                    f"{payload['knowledge_flow_template_revision_id']}:{compiled_checksum}".encode("utf-8")
                ).hexdigest()
            current = session.scalar(select(FlowExecutionSnapshot).where(
                FlowExecutionSnapshot.checksum == payload["checksum"],
            ))
            if current:
                if (current.compiled_definition_json != payload.get("compiled_definition_json") or
                        current.dependency_json != payload.get("dependency_json")):
                    raise ValueError(f"FlowExecutionSnapshot {payload['checksum']} 定义不兼容")
                snapshot_id = current.id
            else:
                by_id = session.get(FlowExecutionSnapshot, payload["id"])
                if by_id and by_id.checksum != payload["checksum"]:
                    payload["id"] = new_id("flowsnap")
                current = _upsert(session, FlowExecutionSnapshot, payload, immutable=(
                    "knowledge_flow_template_revision_id", "checksum",
                    "compiled_definition_json", "dependency_json",
                ))
                snapshot_id = current.id
            id_maps["execution_snapshots"][source_snapshot_id] = snapshot_id
            revision = session.get(
                KnowledgeFlowTemplateRevision, payload["knowledge_flow_template_revision_id"],
            )
            if revision:
                revision.execution_snapshot_id = snapshot_id

    @staticmethod
    def _runtime_bindings_and_baselines(session, metadata: dict[str, list[dict[str, Any]]],
                                        selected_document_ids: set[str], selected_version_ids: set[str],
                                        id_maps: dict[str, dict[str, str]]) -> None:
        binding_maps = id_maps.get("bindings", {})
        version_maps = id_maps.get("versions", {})
        selected_binding_ids: set[str] = set()
        for raw in metadata.get("document_library_template_bindings", []):
            if raw["document_library_id"] not in selected_document_ids:
                continue
            payload = dict(raw)
            if payload.get("last_successful_revision_id"):
                payload["last_successful_revision_id"] = id_maps["template_revisions"].get(
                    payload["last_successful_revision_id"], payload["last_successful_revision_id"],
                )
            _upsert(session, DocumentLibraryTemplateBinding, payload,
                    immutable=("document_library_id", "knowledge_flow_template_id"))
            selected_binding_ids.add(payload["id"])
            if payload["id"] in binding_maps:
                clone = dict(payload)
                clone["id"] = binding_maps[payload["id"]]
                clone["document_library_id"] = id_maps["document_libraries"][payload["document_library_id"]]
                _upsert(session, DocumentLibraryTemplateBinding, clone,
                        immutable=("document_library_id", "knowledge_flow_template_id"))
        for raw in metadata.get("document_library_processing_baselines", []):
            if raw["document_library_template_binding_id"] not in selected_binding_ids:
                continue
            if raw["source_version_id"] not in selected_version_ids:
                continue
            payload = dict(raw)
            payload["knowledge_flow_template_revision_id"] = id_maps["template_revisions"].get(
                payload["knowledge_flow_template_revision_id"],
                payload["knowledge_flow_template_revision_id"],
            )
            _upsert(session, DocumentLibraryProcessingBaseline, payload, immutable=(
                "document_library_template_binding_id", "source_version_id",
                "knowledge_flow_template_revision_id", "origin_job_id",
            ))
            if payload["document_library_template_binding_id"] in binding_maps:
                clone = dict(payload)
                clone["id"] = id_maps.get("baselines", {}).get(payload["id"], new_id("baseline"))
                clone["document_library_template_binding_id"] = binding_maps[payload["document_library_template_binding_id"]]
                clone["source_version_id"] = version_maps[payload["source_version_id"]]
                _upsert(session, DocumentLibraryProcessingBaseline, clone, immutable=(
                    "document_library_template_binding_id", "source_version_id",
                    "knowledge_flow_template_revision_id", "origin_job_id",
                ))

    @staticmethod
    def _runtime_outputs(session, metadata: dict[str, list[dict[str, Any]]], selected: set[str],
                         id_maps: dict[str, dict[str, str]]) -> None:
        binding_maps = id_maps.get("bindings", {})
        library_maps = id_maps.get("libraries", {})
        for raw in metadata.get("document_library_template_outputs", []):
            if raw["knowledge_library_id"] not in selected:
                continue
            payload = dict(raw)
            _upsert(session, DocumentLibraryTemplateOutput, payload, immutable=(
                "document_library_template_binding_id", "output_key", "knowledge_library_id",
            ))
            if payload["document_library_template_binding_id"] in binding_maps:
                clone = dict(payload)
                clone["id"] = id_maps.get("outputs", {}).get(payload["id"], new_id("docout"))
                clone["document_library_template_binding_id"] = binding_maps[payload["document_library_template_binding_id"]]
                clone["knowledge_library_id"] = library_maps.get(payload["knowledge_library_id"], payload["knowledge_library_id"])
                _upsert(session, DocumentLibraryTemplateOutput, clone, immutable=(
                    "document_library_template_binding_id", "output_key", "knowledge_library_id",
                ))

    def _prepare_asset_versions(self, manifest: dict[str, Any], selected: set[str],
                                id_maps: dict[str, dict[str, str]], job_id: str) -> None:
        assets = {item["id"]: dict(item) for item in manifest.get("asset_versions") or []}
        by_asset_id = {
            part.get("asset_version_id") or part.get("id"): (collection, part)
            for collection, partitions in manifest["collections"].items() for part in partitions
        }
        with self.store.sessions.begin() as session:
            for source_asset_id, asset in assets.items():
                source_library_id = asset["knowledge_library_id"]
                if source_library_id not in selected:
                    continue
                collection, part = by_asset_id.get(source_asset_id, (asset.get("collection_name"), asset))
                local_library_id = id_maps["libraries"].get(source_library_id, source_library_id)
                local_asset_id = source_asset_id
                version_no = int(asset.get("asset_version_no") or asset.get("version_no") or 0)
                partition_name = str(asset.get("partition_name") or part.get("partition_name") or "")
                if local_library_id != source_library_id:
                    local_asset_id = id_maps["asset_versions"].setdefault(source_asset_id, new_id("asset"))
                    latest = session.scalar(select(func.max(KnowledgeAssetVersion.version_no)).where(
                        KnowledgeAssetVersion.knowledge_library_id == local_library_id,
                    )) or 0
                    version_no = int(latest) + 1
                    partition_name = f"kl_{local_library_id}__v{version_no}"
                else:
                    id_maps["asset_versions"].setdefault(source_asset_id, local_asset_id)
                id_maps["partitions"][str(part.get("partition_name") or partition_name)] = partition_name
                current = session.get(KnowledgeAssetVersion, local_asset_id)
                expected = {
                    "knowledge_library_id": local_library_id,
                    "version_no": version_no,
                    "index_profile_id": asset["index_profile_id"],
                    "index_profile_revision_id": asset["index_profile_revision_id"],
                    "storage_contract_revision_id": asset.get("storage_contract_revision_id"),
                    "embedding_serving_id": asset.get("embedding_serving_id"),
                    "embedding_model": asset.get("embedding_model"),
                    "embedding_dimension": asset.get("embedding_dimension"),
                    "collection_name": collection, "partition_name": partition_name,
                }
                if current:
                    if any(getattr(current, key) != value for key, value in expected.items()):
                        raise ValueError(f"AssetVersion {source_asset_id} 与本地已有定义不兼容")
                    continue
                session.add(KnowledgeAssetVersion(
                    id=local_asset_id, **expected, status="building",
                    item_count=int(asset.get("item_count") or 0),
                    content_digest=asset.get("content_digest"),
                    source_release_id=manifest.get("release_id") or manifest.get("base_release_id"),
                    source_migration_job_id=job_id,
                ))

    def _finish_asset_versions(self, manifest: dict[str, Any], selected: set[str],
                               id_maps: dict[str, dict[str, str]], job_id: str) -> None:
        item_results = {(item["knowledge_library_id"], item["collection_name"]): item
                        for item in self.store.get_migration_job(job_id)["items"]}
        with self.store.sessions.begin() as session:
            for asset in manifest.get("asset_versions") or []:
                if asset["knowledge_library_id"] not in selected:
                    continue
                local_id = id_maps["asset_versions"].get(asset["id"], asset["id"])
                value = session.get(KnowledgeAssetVersion, local_id, with_for_update=True)
                if not value:
                    raise ValueError(f"导入 AssetVersion 不存在：{local_id}")
                result = item_results.get((asset["knowledge_library_id"], value.collection_name))
                if not result or result["status"] != "verified":
                    raise ValueError(f"AssetVersion {local_id} 的向量尚未验证")
                value.status, value.error = "ready", None
                value.item_count = int(result["target_count"])
                value.content_digest = result["target_digest"]
                value.ready_at = utc_now()
                value.last_verification_status = "consistent"
                value.last_verified_at = utc_now()
                value.last_observed_count = value.item_count
                value.last_observed_digest = value.content_digest
                value.last_verification_error = None

    def _local_candidate_projects(self, projects: list[dict[str, Any]],
                                  id_maps: dict[str, dict[str, str]]) -> list[dict[str, Any]]:
        def rewrite(value: Any, key: str | None = None) -> Any:
            if isinstance(value, dict):
                return {name: rewrite(child, name) for name, child in value.items()}
            if isinstance(value, list):
                if key == "knowledge_library_ids":
                    return [id_maps["libraries"].get(str(child), child) for child in value]
                return [rewrite(child, key) for child in value]
            if isinstance(value, str):
                if key == "knowledge_library_id":
                    return id_maps["libraries"].get(value, value)
                if key == "asset_version_id":
                    return id_maps["asset_versions"].get(value, value)
                if key == "partition_name":
                    return id_maps["partitions"].get(value, value)
            return value

        values = []
        for project in projects:
            payload = rewrite(json.loads(json.dumps(project)))
            snapshot = payload.get("route_snapshot") or {}
            if self.milvus:
                snapshot["milvus_target"] = {
                    "id": "local-import-target", "name": "机构本地 Milvus",
                    "milvus_url": self.milvus.uri,
                }
            payload["route_snapshot"] = snapshot
            values.append(payload)
        return values

    def _objects(self, archive, metadata, selected, id_maps, source_instance_id):
        selected_item_ids = {item["id"] for item in metadata["knowledge_items"]
                             if item["knowledge_library_id"] in selected}
        selected_version_ids = {item["source_version_id"] for item in metadata["knowledge_item_sources"]
                                if item["knowledge_item_id"] in selected_item_ids}
        source_by_version = {item["id"]: item["source_id"] for item in metadata["source_versions"]}
        document_by_source = {item["id"]: item["document_library_id"] for item in metadata["sources"]}
        selected_document_ids = {
            document_by_source[source_by_version[version_id]] for version_id in selected_version_ids
            if version_id in source_by_version and source_by_version[version_id] in document_by_source
        }
        selected_version_ids.update(
            item["id"] for item in metadata["source_versions"]
            if document_by_source.get(item["source_id"]) in selected_document_ids
        )
        version_by_source = {version["source_id"]: version for version in metadata["source_versions"]
                             if version["id"] in selected_version_ids}
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

    def _apply_tombstones(self, manifest: dict[str, Any], selected: set[str]) -> None:
        source_instance_id = manifest["source_instance_id"]
        with self.store.sessions.begin() as session:
            for tombstone in manifest.get("tombstones") or []:
                kind = tombstone.get("kind")
                if kind == "knowledge_item":
                    if tombstone.get("knowledge_library_id") not in selected:
                        continue
                    item = session.get(KnowledgeItem, tombstone.get("id"))
                    library = session.get(KnowledgeLibrary, item.knowledge_library_id) if item else None
                    if (item and library and library.origin_type == "central_import" and
                            library.origin_instance_id == source_instance_id and
                            library.origin_state != "forked"):
                        item.status = "inactive"
                elif kind in {"source", "source_version"}:
                    version = session.get(SourceVersion, tombstone.get("id")) if kind == "source_version" else None
                    source = session.get(Source, version.source_id) if version else session.get(Source, tombstone.get("id"))
                    document = session.get(DocumentLibrary, source.document_library_id) if source else None
                    if (not source or not document or document.origin_type != "central_import" or
                            document.origin_instance_id != source_instance_id or document.origin_state == "forked"):
                        continue
                    if version:
                        version.status = "superseded"
                    else:
                        source.status = "deleted"

    def _vectors(self, package_path: Path, manifest: dict[str, Any], selected: set[str], id_maps, job_id: str) -> None:
        if not self.milvus:
            raise ValueError("Milvus Target 尚未配置")
        schema_version = int(manifest.get("manifest_schema_version") or manifest.get("schema_version", 1))
        manifest_assets = {str(item.get("id") or item.get("asset_version_id")): item
                           for item in manifest.get("asset_versions") or []}
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
                    target_partition = id_maps.get("partitions", {}).get(
                        part["partition_name"], f"kl_{id_maps['libraries'][library_id]}__v1",
                    )
                    local = self._rewrite_vector_ids(local, library_id, id_maps)
                    compare_central_digest = False
                elif schema_version == 2:
                    target_partition = id_maps.get("partitions", {}).get(part["partition_name"], part["partition_name"])
                asset_id = None
                if schema_version == 2:
                    source_asset_id = part.get("asset_version_id") or part.get("id")
                    source_asset = manifest_assets.get(str(source_asset_id)) or {}
                    asset_id = id_maps.get("asset_versions", {}).get(source_asset_id, source_asset_id)
                    with self.store.sessions() as session:
                        asset = session.get(KnowledgeAssetVersion, asset_id)
                        if asset and asset.status == "ready":
                            verified = self.milvus.verify_partition(collection, target_partition)
                            if compare_central_digest and verified["digest"] != part["content_revision"]:
                                raise ValueError(f"已就绪 AssetVersion 摘要不匹配：{asset_id}")
                            self.store.update_migration_item(
                                job_id, library_id, collection,
                                source_count=int(source_asset.get("item_count") or verified["count"]),
                                source_digest=part.get("content_revision"),
                                target_count=verified["count"], target_digest=verified["digest"],
                                status="verified",
                            )
                            continue
                    self._set_import_asset_status(asset_id, "verifying")
                    if hasattr(self.milvus, "partition_exists") and self.milvus.partition_exists(
                            collection, target_partition):
                        # Only this unpublished candidate is replaceable. Ready
                        # assets were handled above and are never reset/dropped.
                        self.milvus.drop_partition(collection, target_partition)
                    if hasattr(self.milvus, "ensure_partition"):
                        self.milvus.ensure_partition(collection, target_partition)
                else:
                    self.milvus.reset_partition(collection, target_partition)
                try:
                    result = self.milvus.import_partition(collection, target_partition, local, batch_size=1000)
                except Exception as exc:
                    if asset_id:
                        self._set_import_asset_status(asset_id, "building", str(exc))
                    raise
                verified = self.milvus.verify_partition(collection, target_partition)
                if result != verified or compare_central_digest and verified["digest"] != part["content_revision"]:
                    if asset_id:
                        self._set_import_asset_status(asset_id, "building", "向量摘要不匹配")
                    raise ValueError(f"Partition 完整性校验失败：{collection}/{target_partition}")
                self.milvus.load_partition(collection, target_partition)
                self.store.update_migration_item(job_id, library_id, collection, target_count=verified["count"],
                    source_count=int((manifest_assets.get(str(part.get("asset_version_id") or part.get("id"))) or {}).get(
                        "item_count") or result["count"]), source_digest=part.get("content_revision"),
                    target_digest=verified["digest"], status="verified")

    def _set_import_asset_status(self, asset_id: str, status: str, error: str | None = None) -> None:
        with self.store.sessions.begin() as session:
            asset = session.get(KnowledgeAssetVersion, asset_id, with_for_update=True)
            if not asset:
                raise ValueError(f"导入 AssetVersion 不存在：{asset_id}")
            if asset.status == "ready" and status != "ready":
                raise ValueError("禁止修改已 Ready 的 AssetVersion")
            asset.status, asset.error = status, error

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
            if int(manifest.get("schema_version", 1)) == 1:
                # v1 packages predate immutable AssetVersion identities.  They
                # prove the task/authorization/Profile contract while the
                # local importer assigns its own physical Partition identity.
                for task in payload.get("tasks", []):
                    for route in task.get("org_routes", []):
                        for library in route.get("libraries", []):
                            library.pop("asset_version_id", None)
                            library.pop("asset_version_no", None)
                            library.pop("partition_name", None)
                            for index in library.get("indexes", []):
                                index.pop("asset_version_id", None)
                                index.pop("asset_version_no", None)
                                index.pop("partition_name", None)
        if comparable_generated.get("tasks") != comparable_baseline.get("tasks"):
            raise ValueError("Seed 导入后授权生成的 Routing 与 baseline 不一致")
        version = self.store.create_route_version(project_deployment_id, generated, status="draft", origin="central_seed")
        project_code = control["project"]["code"]; deployment_code = control["deployment"]["code"]
        checksum, object_key = AtomicRoutingPublisher(self.routing_dir).publish(
            project_code, deployment_code, version.version_no, version.snapshot_json,
            release_stage=version.release_stage)
        self.store.mark_route_published(version.id, checksum, object_key)
        self.instance = self.instance.bind_seed(self.store, deployment_id, manifest["source_instance_id"])
