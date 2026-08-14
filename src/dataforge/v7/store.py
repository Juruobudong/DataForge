"""Transactional V7 persistence service.

All methods operate on V7 models only.  There is intentionally no legacy import or
fallback path here: a V7 deployment starts with freshly uploaded material.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import uuid
from datetime import timedelta
from pathlib import PurePosixPath
from types import SimpleNamespace
from typing import Any, Iterable

from sqlalchemy import create_engine, delete, func, select, update
from sqlalchemy.orm import Session, sessionmaker

from .catalog import CATALOG_SEEDS, builtin_flow_definition, catalog_by_code, subflow_seeds
from .flow import FlowCompiler, FlowValidationError
from .migrations import assert_schema_current
from .models import (
    AdminSession,
    AuditEvent,
    DocumentLibrary,
    DocumentLibraryMember,
    DocumentDeletionJob,
    DocumentLibraryProcessingRecord,
    DocumentLibraryTemplateBinding,
    DocumentLibraryTemplateOutput,
    DocumentIR,
    EmbeddingProfile,
    KnowledgeChange,
    KnowledgeFlowTemplate,
    KnowledgeFlowTemplateRevision,
    KnowledgeTypeIndexBinding,
    KnowledgeTypeModeRevision,
    KnowledgeTypeRevision,
    KnowledgeIndexProfile,
    KnowledgeIndexProfileRevision,
    StorageContract,
    StorageContractRevision,
    ManagedCollection,
    KnowledgeItem,
    KnowledgeItemSource,
    KnowledgeChunkGeneration,
    KnowledgeJob,
    KnowledgeLibrary,
    KnowledgeLibraryDeletionJob,
    KnowledgeType,
    OperatorDefinition,
    OperatorVersion,
    PromptTemplate,
    PromptTemplateRevision,
    QualityProfile,
    QualityProfileRevision,
    FlowSubgraph,
    FlowSubgraphRevision,
    FlowExecutionSnapshot,
    FlowRun,
    FlowNodeRun,
    Artifact,
    ArtifactLineage,
    Project,
    ProjectOrgRoute,
    ProjectOrgRouteLibrary,
    ProjectRouteVersion,
    ProjectTask,
    Source,
    SourceChunk,
    SourceVersion,
    VectorRecordState,
    VectorDeletionJob,
    VectorSyncJob,
    utc_now,
)


V7_TYPE_META = {
    "text": ("文", "文本知识"),
    "qa": ("问", "问答知识"),
    "graph": ("图", "图谱知识"),
}
V7_TEMPLATE_SEEDS = (
    ("standard-text", "标准文本知识流程", ["text"]),
    ("standard-qa", "标准问答知识流程", ["qa"]),
    ("standard-graph-triple", "标准三元组图谱流程", ["graph:triple"]),
    ("standard-graph-semantic", "标准语义图谱流程", ["graph:semantic"]),
    ("standard-graph", "标准图谱知识流程（兼容）", ["graph"]),
    ("standard-multi", "标准多产出知识流程", ["text", "qa", "graph"]),
)
LINEAR_TEMPLATE_STEPS = ("validate", "parse", "normalize", "structure_recovery", "semantic_chunks", "generate")  # legacy API input only


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(16)}"


def generated_business_code(prefix: str) -> str:
    """Codes owned by the platform are never accepted from business clients."""
    return f"{prefix}-{utc_now():%Y%m%d}-{uuid.uuid4()}"


DEFAULT_INDEX_FIELD_MAPPING = {
    "id": "id",
    "vector": "vector",
    "knowledge_library_id": "knowledge_library_id",
    "source_knowledge_id": "source_knowledge_id",
    "content": "content",
    "data": "data",
}


def content_hash(content: str, data: dict[str, Any]) -> str:
    canonical = json.dumps({"content": content, "data": data}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def template_signature(output_types: Iterable[str]) -> str:
    return ",".join(sorted(dict.fromkeys(output_types)))


def normalise_output_key(value: str) -> str:
    value = str(value or "").strip()
    return "graph:triple" if value == "graph" else value


def output_contract(value: str) -> tuple[str, str | None]:
    key = normalise_output_key(value)
    if key in {"graph:triple", "graph:semantic"}:
        return "graph", key.split(":", 1)[1]
    return key, None


_COMMON_STORAGE_FIELDS = [
    {"name": "id", "type": "VARCHAR", "max_length": 64, "primary": True},
    {"name": "vector", "type": "FLOAT_VECTOR"},
    {"name": "knowledge_library_id", "type": "VARCHAR", "max_length": 64},
    {"name": "source_knowledge_id", "type": "VARCHAR", "max_length": 128},
    {"name": "content", "type": "VARCHAR", "max_length": 65535},
    {"name": "data", "type": "JSON"},
]


STORAGE_CONTRACT_SEEDS: dict[str, dict[str, Any]] = {
    "text": {
        "name": "文本知识存储结构", "collection": "dataforge_text_knowledge",
        "schema": {"fields": [*_COMMON_STORAGE_FIELDS]},
    },
    "qa-question": {
        "name": "问答问题向量存储结构", "collection": "dataforge_qa_question",
        "schema": {"fields": [*_COMMON_STORAGE_FIELDS,
            {"name": "question", "type": "VARCHAR", "max_length": 16384}]},
    },
    "qa-full": {
        "name": "问答全文向量存储结构", "collection": "dataforge_qa_full",
        "schema": {"fields": [*_COMMON_STORAGE_FIELDS,
            {"name": "question", "type": "VARCHAR", "max_length": 16384},
            {"name": "answer", "type": "VARCHAR", "max_length": 65535}]},
    },
    "graph-triple": {
        "name": "三元组图谱存储结构", "collection": "dataforge_graph_triple_knowledge",
        "schema": {
        "fields": [
            *_COMMON_STORAGE_FIELDS,
            {"name": "subject", "type": "VARCHAR", "max_length": 2048},
            {"name": "predicate", "type": "VARCHAR", "max_length": 1024},
            {"name": "object", "type": "VARCHAR", "max_length": 2048},
            {"name": "subject_type", "type": "VARCHAR", "max_length": 255, "nullable": True},
            {"name": "object_type", "type": "VARCHAR", "max_length": 255, "nullable": True},
        ]},
    },
    "graph-semantic": {
        "name": "语义图谱存储结构", "collection": "dataforge_graph_semantic_knowledge",
        "schema": {
        "fields": [
            *_COMMON_STORAGE_FIELDS,
            {"name": "source_entity_name", "type": "VARCHAR", "max_length": 2048},
            {"name": "source_entity_type", "type": "VARCHAR", "max_length": 255, "nullable": True},
            {"name": "source_entity_description", "type": "VARCHAR", "max_length": 8192, "nullable": True},
            {"name": "target_entity_name", "type": "VARCHAR", "max_length": 2048},
            {"name": "target_entity_type", "type": "VARCHAR", "max_length": 255, "nullable": True},
            {"name": "target_entity_description", "type": "VARCHAR", "max_length": 8192, "nullable": True},
            {"name": "relation_description", "type": "VARCHAR", "max_length": 16384},
            {"name": "relation_keywords", "type": "JSON", "nullable": True},
            {"name": "relation_weight", "type": "DOUBLE", "nullable": True},
            {"name": "evidence", "type": "JSON", "nullable": True},
        ]},
    },
}


def storage_spec_hash(schema: dict[str, Any], embedding: EmbeddingProfile, index_spec: dict[str, Any] | None = None) -> str:
    value = {
        "schema": schema, "embedding_profile_id": embedding.id, "model": embedding.model,
        "dimension": embedding.dimension, "metric_type": embedding.metric_type,
        "vector_type": "FLOAT_VECTOR", "index": index_spec or {"index_type": "AUTOINDEX"},
    }
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def normalise_relative_path(value: str) -> tuple[str, str]:
    """Return a safe, database-authoritative POSIX file path and its parent."""
    raw = str(value or "").replace("\\", "/").strip("/")
    parts = raw.split("/") if raw else []
    if not parts or any(not part or part in {".", ".."} or "\x00" in part for part in parts):
        raise ValueError("relative_path 必须是文档库内的有效相对文件路径")
    if any(":" in part for part in parts) or len(raw) > 1024:
        raise ValueError("relative_path 包含不支持的路径字符或过长")
    path = str(PurePosixPath(*parts))
    return path, str(PurePosixPath(*parts[:-1])) if len(parts) > 1 else ""


def relative_path_hash(relative_path: str) -> str:
    """Fixed-width identity for a Unicode path that is too wide for MySQL keys."""
    return hashlib.sha256(relative_path.encode("utf-8")).hexdigest()


class V7Store:
    def __init__(self, database_url: str):
        self.database_url = database_url
        connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
        self.engine = create_engine(database_url, future=True, connect_args=connect_args)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False, future=True)

    def assert_schema_current(self) -> str:
        return assert_schema_current(self.database_url)

    def seed(self) -> None:
        """Install immutable V7 defaults after Alembic creates an empty schema."""
        with self.sessions.begin() as session:
            for code, (icon, name) in V7_TYPE_META.items():
                if not session.scalar(select(KnowledgeType).where(KnowledgeType.code == code)):
                    session.add(KnowledgeType(id=f"type_{code}", code=code, name=name, icon=icon, kind="builtin", status="active"))
            profile = session.scalar(select(EmbeddingProfile).where(EmbeddingProfile.code == "bce_base_768_v1"))
            if not profile:
                try:
                    embedding_dimension = int(os.getenv("EMBEDDING_DIM", "768"))
                except ValueError as exc:
                    raise ValueError("EMBEDDING_DIM 必须是整数") from exc
                if embedding_dimension <= 0:
                    raise ValueError("EMBEDDING_DIM 必须为正整数")
                profile = EmbeddingProfile(
                    id="embedding_bce_base_768_v1",
                    code="bce_base_768_v1",
                    model=os.getenv("EMBEDDING_MODEL", "bce-embedding-base"),
                    dimension=embedding_dimension,
                    metric_type="COSINE",
                    endpoint_ref="EMBEDDING_API_BASE",
                )
                session.add(profile)
            for code, name, output_types in V7_TEMPLATE_SEEDS:
                template = session.scalar(select(KnowledgeFlowTemplate).where(KnowledgeFlowTemplate.code == code))
                if not template:
                    template = KnowledgeFlowTemplate(
                        id=f"flow_{code}", code=code, name=name, output_types=output_types,
                        definition_json=builtin_flow_definition(output_types),
                        status="active", is_default=code != "standard-multi",
                    )
                    session.add(template)
                    session.flush()
                if not session.scalar(select(KnowledgeFlowTemplateRevision).where(KnowledgeFlowTemplateRevision.knowledge_flow_template_id == template.id)):
                    session.add(KnowledgeFlowTemplateRevision(
                        id=new_id("flowrev"), knowledge_flow_template_id=template.id, revision_no=1,
                        definition_json=template.definition_json, status="published", published_at=utc_now(),
                    ))
            profile_id = profile.id
            index_seeds = (
                ("text", "text", "dataforge_text_knowledge"),
                ("qa-question", "qa", "dataforge_qa_question"),
                ("qa-full", "qa", "dataforge_qa_full"),
                ("graph", "graph", "dataforge_graph_knowledge"),
                ("graph-triple", "graph", "dataforge_graph_triple_knowledge"),
                ("graph-semantic", "graph", "dataforge_graph_semantic_knowledge"),
            )
            for code, kind, collection in index_seeds:
                if not session.scalar(select(KnowledgeIndexProfile).where(KnowledgeIndexProfile.code == code)):
                    index_profile = KnowledgeIndexProfile(
                        id=f"index_{code}", code=code, knowledge_type=kind, collection_name=collection,
                        embedding_profile_id=profile_id,
                        fields_json=dict(DEFAULT_INDEX_FIELD_MAPPING), status="active",
                    )
                    session.add(index_profile)
                    session.flush()
                    revision = KnowledgeIndexProfileRevision(
                        id=f"indexrev_{code}_1", knowledge_index_profile_id=index_profile.id, revision_no=1,
                        collection_name=collection, embedding_profile_id=profile_id,
                        fields_json=dict(DEFAULT_INDEX_FIELD_MAPPING), status="published", published_at=utc_now(),
                    )
                    session.add(revision)
                    index_profile.current_revision_id = revision.id
            session.flush()
            self._seed_storage_contracts(session, profile)
            self._seed_governance(session)
            session.flush()
            for template in session.scalars(select(KnowledgeFlowTemplate)):
                revision = session.scalar(select(KnowledgeFlowTemplateRevision).where(
                    KnowledgeFlowTemplateRevision.knowledge_flow_template_id == template.id,
                    KnowledgeFlowTemplateRevision.status == "published",
                ).order_by(KnowledgeFlowTemplateRevision.revision_no.desc()))
                if revision and not revision.execution_snapshot_id:
                    self._create_execution_snapshot(session, revision, template.output_types)

    def _seed_governance(self, session: Session) -> None:
        """Seed published governance assets.  They are normal revisions, not constants."""
        quality = session.scalar(select(QualityProfile).where(QualityProfile.code == "default-knowledge-quality"))
        if not quality:
            quality = QualityProfile(id="quality_default", code="default-knowledge-quality", name="默认知识质量", status="active")
            session.add(quality); session.flush()
        quality_revision = session.scalar(select(QualityProfileRevision).where(QualityProfileRevision.quality_profile_id == quality.id, QualityProfileRevision.revision_no == 1))
        if not quality_revision:
            quality_revision = QualityProfileRevision(id="qualityrev_default", quality_profile_id=quality.id, revision_no=1, rules_json={"pass_score": 0.8, "review_score": 0.6}, status="published", published_at=utc_now())
            session.add(quality_revision)
        prompt = session.scalar(select(PromptTemplate).where(PromptTemplate.code == "knowledge-generator-default"))
        if not prompt:
            prompt = PromptTemplate(id="prompt_default", code="knowledge-generator-default", name="默认知识生成提示", status="active")
            session.add(prompt); session.flush()
        if not session.scalar(select(PromptTemplateRevision).where(PromptTemplateRevision.prompt_template_id == prompt.id, PromptTemplateRevision.revision_no == 1)):
            session.add(PromptTemplateRevision(id="promptrev_default", prompt_template_id=prompt.id, revision_no=1, body="根据输入内容生成结构化知识。", input_schema={"type": "object"}, output_schema={"type": "object"}, status="published", published_at=utc_now()))
        profiles = {item.code: item for item in session.scalars(select(KnowledgeIndexProfile)).all()}
        type_contracts = {
            "text": ({"type": "object"}, "canonical_content", ["source_anchor"], "single", ["text"]),
            "qa": ({"type": "object", "required": ["question", "answer"]}, "answer", ["question"], "single", ["qa-question", "qa-full"]),
            "graph": ({"type": "object"}, "canonical_content", ["source_anchor"], "multiple", ["graph-triple", "graph-semantic"]),
        }
        for code, (schema, canonical, identity, policy, profile_codes) in type_contracts.items():
            knowledge_type = session.scalar(select(KnowledgeType).where(KnowledgeType.code == code))
            if not knowledge_type:
                continue
            if code == "graph":
                legacy_revision = session.scalar(select(KnowledgeTypeRevision).where(
                    KnowledgeTypeRevision.knowledge_type_id == knowledge_type.id,
                    KnowledgeTypeRevision.revision_no == 1,
                ))
                if not legacy_revision:
                    legacy_revision = KnowledgeTypeRevision(
                        id="typerev_graph_1", knowledge_type_id=knowledge_type.id, revision_no=1,
                        schema_json={"type": "object", "required": ["subject", "predicate", "object"]},
                        canonical_field="predicate", identity_fields=["subject", "predicate", "object"],
                        source_policy="multiple", quality_profile_revision_id="qualityrev_default",
                        status="published", published_at=utc_now(),
                    )
                    session.add(legacy_revision); session.flush()
                legacy_profile = profiles.get("graph")
                if legacy_profile and not session.scalar(select(KnowledgeTypeIndexBinding).where(
                    KnowledgeTypeIndexBinding.knowledge_type_revision_id == legacy_revision.id,
                    KnowledgeTypeIndexBinding.index_profile_id == legacy_profile.id,
                )):
                    session.add(KnowledgeTypeIndexBinding(
                        id=new_id("typeindex"), knowledge_type_revision_id=legacy_revision.id,
                        index_profile_id=legacy_profile.id, index_profile_revision_id=legacy_profile.current_revision_id,
                        field_path="predicate",
                    ))
            target_revision_no = 2 if code == "graph" else 1
            revision = session.scalar(select(KnowledgeTypeRevision).where(
                KnowledgeTypeRevision.knowledge_type_id == knowledge_type.id,
                KnowledgeTypeRevision.revision_no == target_revision_no,
            ))
            if not revision:
                revision = KnowledgeTypeRevision(id=f"typerev_{code}_{target_revision_no}", knowledge_type_id=knowledge_type.id, revision_no=target_revision_no, schema_json=schema, canonical_field=canonical, identity_fields=identity, source_policy=policy, quality_profile_revision_id="qualityrev_default", status="published", published_at=utc_now())
                session.add(revision); session.flush()
            knowledge_type.current_revision_id = revision.id
            for profile_code in profile_codes:
                profile = profiles.get(profile_code)
                if profile and not session.scalar(select(KnowledgeTypeIndexBinding).where(KnowledgeTypeIndexBinding.knowledge_type_revision_id == revision.id, KnowledgeTypeIndexBinding.index_profile_id == profile.id)):
                    session.add(KnowledgeTypeIndexBinding(
                        id=new_id("typeindex"), knowledge_type_revision_id=revision.id,
                        index_profile_id=profile.id, index_profile_revision_id=profile.current_revision_id,
                        field_path=canonical,
                    ))
            if code == "graph":
                mode_contracts = {
                    "triple": (
                        {"type": "object", "required": ["subject", "predicate", "object"], "properties": {
                            "subject": {"type": "string"}, "predicate": {"type": "string"}, "object": {"type": "string"},
                            "subject_type": {"type": "string"}, "object_type": {"type": "string"},
                        }},
                        ["subject", "predicate", "object"], ["subject", "predicate", "object"],
                    ),
                    "semantic": (
                        {"type": "object", "required": ["source_entity", "target_entity", "relation", "evidence"], "properties": {
                            "source_entity": {"type": "object", "required": ["name"]},
                            "target_entity": {"type": "object", "required": ["name"]},
                            "relation": {"type": "object", "required": ["description"]},
                            "evidence": {"type": "array"},
                        }},
                        ["source_entity.name", "relation.description", "target_entity.name"],
                        ["source_entity.name", "relation.description", "target_entity.name"],
                    ),
                }
                for mode, (mode_schema, canonical_fields, identity_fields) in mode_contracts.items():
                    if not session.scalar(select(KnowledgeTypeModeRevision).where(
                        KnowledgeTypeModeRevision.knowledge_type_revision_id == revision.id,
                        KnowledgeTypeModeRevision.mode == mode,
                    )):
                        session.add(KnowledgeTypeModeRevision(
                            id=f"typemode_graph_{mode}_1", knowledge_type_revision_id=revision.id,
                            mode=mode, revision_no=1, schema_json=mode_schema,
                            canonical_fields=canonical_fields, identity_fields=identity_fields,
                            source_policy="multiple", status="published", published_at=utc_now(),
                        ))
        for item in CATALOG_SEEDS:
            definition = session.scalar(select(OperatorDefinition).where(OperatorDefinition.code == item["code"]))
            if not definition:
                definition = OperatorDefinition(id=f"op_{item['code'].replace('-', '_')}", code=item["code"], name=item["name"], description=item["description"], category=item["category"], exposure=item["exposure"], risk_level=item["risk_level"], enabled=item["exposure"] != "disabled")
                session.add(definition); session.flush()
            definition.name, definition.description, definition.category = item["name"], item["description"], item["category"]
            definition.exposure, definition.risk_level = item["exposure"], item["risk_level"]
            definition.enabled = item["exposure"] != "disabled"
            version_no = int(item.get("version", 1))
            version = session.scalar(select(OperatorVersion).where(OperatorVersion.operator_definition_id == definition.id, OperatorVersion.version_no == version_no))
            if not version:
                version = OperatorVersion(id=new_id("oprev"), operator_definition_id=definition.id, version_no=version_no, status="published", published_at=utc_now())
                session.add(version)
            version.adapter_code = item["adapter_code"]
            version.input_ports = item.get("input_ports") or {"input": {"artifact_type": item["input"], "cardinality": "one"}}
            version.output_ports = item.get("output_ports") or {"output": {"artifact_type": item["output"], "cardinality": "many"}}
            version.input_example, version.output_example = item["input_example"], item["output_example"]
            version.parameter_schema, version.runtime_requirements = item["parameter_schema"], item["runtime_requirements"]
            definition.latest_version = max(definition.latest_version or 0, version_no)
        for item in subflow_seeds():
            subflow = session.scalar(select(FlowSubgraph).where(FlowSubgraph.code == item["code"]))
            if not subflow:
                subflow = FlowSubgraph(id=f"subflow_{item['code'].replace('-', '_')}", code=item["code"], name=item["name"], status="active")
                session.add(subflow); session.flush()
            if not session.scalar(select(FlowSubgraphRevision).where(FlowSubgraphRevision.flow_subgraph_id == subflow.id, FlowSubgraphRevision.revision_no == 1)):
                session.add(FlowSubgraphRevision(id=new_id("subflowrev"), flow_subgraph_id=subflow.id, revision_no=1, definition_json=item["definition"], status="published", published_at=utc_now()))

    def _seed_storage_contracts(self, session: Session, embedding: EmbeddingProfile) -> None:
        for code, seed in STORAGE_CONTRACT_SEEDS.items():
            schema = seed["schema"]
            contract = session.scalar(select(StorageContract).where(StorageContract.code == code))
            if not contract:
                contract = StorageContract(id=f"storage_{code}", code=code, name=seed["name"])
                session.add(contract); session.flush()
            spec_hash = storage_spec_hash(schema, embedding)
            revision = session.scalar(select(StorageContractRevision).where(StorageContractRevision.storage_spec_hash == spec_hash))
            if not revision:
                revision = StorageContractRevision(
                    id=f"storagerev_{code}_1", storage_contract_id=contract.id, revision_no=1,
                    schema_json=schema, embedding_profile_id=embedding.id, vector_type="FLOAT_VECTOR",
                    dimension=embedding.dimension, metric_type=embedding.metric_type,
                    index_json={"index_type": "AUTOINDEX"}, storage_spec_hash=spec_hash,
                    status="published", published_at=utc_now(),
                )
                session.add(revision); session.flush()
            contract.current_revision_id = revision.id
            collection_name = seed["collection"]
            managed = session.scalar(select(ManagedCollection).where(ManagedCollection.collection_name == collection_name))
            if not managed:
                managed = ManagedCollection(
                    id=f"collection_{code}", storage_contract_revision_id=revision.id,
                    collection_name=collection_name, provisioning_token=secrets.token_hex(24),
                    desired_spec_hash=spec_hash, status="planned",
                )
                session.add(managed)
            profile = session.scalar(select(KnowledgeIndexProfile).where(KnowledgeIndexProfile.code == code))
            profile_revision = session.get(KnowledgeIndexProfileRevision, profile.current_revision_id) if profile and profile.current_revision_id else None
            if profile_revision:
                profile_revision.storage_contract_revision_id = revision.id
                profile_revision.collection_policy = "managed"
        legacy = session.scalar(select(KnowledgeIndexProfile).where(KnowledgeIndexProfile.code == "graph"))
        legacy_revision = session.get(KnowledgeIndexProfileRevision, legacy.current_revision_id) if legacy and legacy.current_revision_id else None
        if legacy_revision:
            legacy_revision.collection_policy = "external"

    def list_knowledge_type_definitions(self) -> list[dict[str, Any]]:
        with self.sessions() as session:
            values = []
            for item in session.scalars(select(KnowledgeType).order_by(KnowledgeType.code)):
                revision = session.get(KnowledgeTypeRevision, item.current_revision_id) if item.current_revision_id else None
                bindings = [] if not revision else session.execute(
                    select(KnowledgeIndexProfile.code, KnowledgeIndexProfile.collection_name, KnowledgeTypeIndexBinding.field_path)
                    .join(KnowledgeTypeIndexBinding, KnowledgeTypeIndexBinding.index_profile_id == KnowledgeIndexProfile.id)
                    .where(KnowledgeTypeIndexBinding.knowledge_type_revision_id == revision.id)
                ).all()
                values.append({"id": item.id, "code": item.code, "name": item.name, "icon": item.icon, "kind": item.kind,
                               "status": item.status, "current_revision": None if not revision else {
                                   "id": revision.id, "revision": revision.revision_no, "schema": revision.schema_json,
                                   "canonical_field": revision.canonical_field, "identity_fields": revision.identity_fields,
                                   "source_policy": revision.source_policy, "quality_profile_revision_id": revision.quality_profile_revision_id,
                               }, "index_profiles": [{"code": code, "collection_name": collection, "field_path": path} for code, collection, path in bindings]})
            return values

    @staticmethod
    def _validate_type_contract(schema: dict[str, Any], canonical_field: str, identity_fields: list[str], source_policy: str, *, builtin_code: str | None = None) -> None:
        if not isinstance(schema, dict) or schema.get("type") != "object":
            raise ValueError("知识类型 JSON Schema 必须是 object")
        if not canonical_field.strip() or not identity_fields:
            raise ValueError("必须配置 canonical 字段和至少一个 identity 字段")
        if source_policy not in {"single", "multiple"}:
            raise ValueError("来源策略只能是 single 或 multiple")
        fixed = {"text": "single", "qa": "single", "graph": "multiple"}
        if builtin_code in fixed and source_policy != fixed[builtin_code]:
            raise ValueError(f"{builtin_code} 知识类型的来源策略固定为 {fixed[builtin_code]}")

    def create_knowledge_type(self, code: str, name: str, icon: str, schema: dict[str, Any], canonical_field: str,
                              identity_fields: list[str], source_policy: str, quality_profile_revision_id: str,
                              index_profile_ids: list[str]) -> dict[str, Any]:
        code, name = code.strip(), name.strip()
        if not code or not name:
            raise ValueError("知识类型编码和名称不能为空")
        if code in V7_TYPE_META:
            raise ValueError("内置知识类型不可通过扩展接口创建")
        self._validate_type_contract(schema, canonical_field, identity_fields, source_policy)
        with self.sessions.begin() as session:
            if session.scalar(select(KnowledgeType).where(KnowledgeType.code == code)):
                raise ValueError("知识类型编码已存在")
            self._validate_type_revision_dependencies(session, quality_profile_revision_id, index_profile_ids)
            item = KnowledgeType(id=new_id("type"), code=code, name=name, icon=(icon or "知")[:8], kind="extension", status="draft")
            session.add(item); session.flush()
            revision = self._add_type_revision(session, item, schema, canonical_field, identity_fields, source_policy, quality_profile_revision_id, index_profile_ids)
            self.audit(session, "knowledge_type.created", "knowledge_type", item.id, {"revision": revision.revision_no})
            return {"id": item.id, "revision_id": revision.id, "revision": revision.revision_no, "status": "draft"}

    def revise_knowledge_type(self, type_id: str, schema: dict[str, Any], canonical_field: str, identity_fields: list[str],
                              source_policy: str, quality_profile_revision_id: str, index_profile_ids: list[str]) -> dict[str, Any]:
        with self.sessions.begin() as session:
            item = session.get(KnowledgeType, type_id)
            if not item:
                raise ValueError("知识类型不存在")
            self._validate_type_contract(schema, canonical_field, identity_fields, source_policy, builtin_code=item.code if item.kind == "builtin" else None)
            self._validate_type_revision_dependencies(session, quality_profile_revision_id, index_profile_ids)
            revision = self._add_type_revision(session, item, schema, canonical_field, identity_fields, source_policy, quality_profile_revision_id, index_profile_ids)
            self.audit(session, "knowledge_type.revised", "knowledge_type", item.id, {"revision": revision.revision_no})
            return {"id": item.id, "revision_id": revision.id, "revision": revision.revision_no, "status": "draft"}

    def validate_knowledge_type(self, type_id: str) -> dict[str, Any]:
        with self.sessions() as session:
            item = session.get(KnowledgeType, type_id)
            if not item:
                raise ValueError("知识类型不存在")
            revision = session.scalar(select(KnowledgeTypeRevision).where(KnowledgeTypeRevision.knowledge_type_id == item.id).order_by(KnowledgeTypeRevision.revision_no.desc()))
            if not revision:
                raise ValueError("知识类型没有修订")
            self._validate_type_contract(revision.schema_json, revision.canonical_field, revision.identity_fields, revision.source_policy, builtin_code=item.code if item.kind == "builtin" else None)
            bindings = list(session.scalars(select(KnowledgeTypeIndexBinding).where(KnowledgeTypeIndexBinding.knowledge_type_revision_id == revision.id)))
            if not bindings:
                raise ValueError("知识类型至少要绑定一个已发布 Index Profile")
            self._validate_type_revision_dependencies(session, revision.quality_profile_revision_id or "", [item.index_profile_id for item in bindings])
            return {"id": item.id, "revision_id": revision.id, "valid": True}

    def publish_knowledge_type(self, type_id: str) -> dict[str, Any]:
        self.validate_knowledge_type(type_id)
        with self.sessions.begin() as session:
            item = session.get(KnowledgeType, type_id)
            revision = session.scalar(select(KnowledgeTypeRevision).where(KnowledgeTypeRevision.knowledge_type_id == type_id).order_by(KnowledgeTypeRevision.revision_no.desc()))
            assert item and revision
            revision.status, revision.published_at, item.current_revision_id, item.status = "published", utc_now(), revision.id, "active"
            self.audit(session, "knowledge_type.published", "knowledge_type", item.id, {"revision": revision.revision_no})
            return {"id": item.id, "revision_id": revision.id, "revision": revision.revision_no, "status": "published"}

    def _add_type_revision(self, session: Session, item: KnowledgeType, schema: dict[str, Any], canonical_field: str,
                           identity_fields: list[str], source_policy: str, quality_profile_revision_id: str,
                           index_profile_ids: list[str]) -> KnowledgeTypeRevision:
        latest = session.scalar(select(func.max(KnowledgeTypeRevision.revision_no)).where(KnowledgeTypeRevision.knowledge_type_id == item.id)) or 0
        revision = KnowledgeTypeRevision(id=new_id("typerev"), knowledge_type_id=item.id, revision_no=latest + 1,
                                         schema_json=schema, canonical_field=canonical_field.strip(), identity_fields=list(identity_fields),
                                         source_policy=source_policy, quality_profile_revision_id=quality_profile_revision_id)
        session.add(revision); session.flush()
        for profile_id in dict.fromkeys(index_profile_ids):
            profile = session.get(KnowledgeIndexProfile, profile_id)
            assert profile
            session.add(KnowledgeTypeIndexBinding(id=new_id("typeindex"), knowledge_type_revision_id=revision.id,
                                                  index_profile_id=profile.id, index_profile_revision_id=profile.current_revision_id,
                                                  field_path=canonical_field.strip()))
        return revision

    @staticmethod
    def _validate_type_revision_dependencies(session: Session, quality_profile_revision_id: str, index_profile_ids: list[str]) -> None:
        quality = session.get(QualityProfileRevision, quality_profile_revision_id)
        if not quality or quality.status != "published":
            raise ValueError("必须绑定已发布的 Quality Profile 修订")
        if not index_profile_ids:
            raise ValueError("必须绑定至少一个已发布 Index Profile")
        profiles = list(session.scalars(select(KnowledgeIndexProfile).where(KnowledgeIndexProfile.id.in_(index_profile_ids), KnowledgeIndexProfile.status == "active")))
        if len(profiles) != len(set(index_profile_ids)) or any(not profile.current_revision_id for profile in profiles):
            raise ValueError("Index Profile 不存在或尚未发布")

    def create_index_profile(self, code: str, knowledge_type: str, collection_name: str, embedding_code: str,
                             embedding_model: str, dimension: int, metric_type: str, endpoint_ref: str | None,
                             fields: dict[str, Any], *, collection_policy: str = "external",
                             storage_schema: dict[str, Any] | None = None,
                             index_spec: dict[str, Any] | None = None) -> dict[str, Any]:
        code, collection_name, embedding_code = code.strip(), collection_name.strip(), embedding_code.strip()
        if not code or not embedding_code or collection_policy not in {"external", "managed"}:
            raise ValueError("Index Profile、Embedding 编码和 Collection 策略必须有效")
        if collection_policy == "external" and not collection_name:
            raise ValueError("外部 Index Profile 必须指定 Collection")
        self._validate_index_mapping(fields)
        if dimension <= 0:
            raise ValueError("Embedding 维度必须为正整数")
        with self.sessions.begin() as session:
            if session.scalar(select(KnowledgeIndexProfile).where(KnowledgeIndexProfile.code == code)):
                raise ValueError("Index Profile 编码已存在")
            embedding = session.scalar(select(EmbeddingProfile).where(EmbeddingProfile.code == embedding_code))
            if not embedding:
                embedding = EmbeddingProfile(id=new_id("embedding"), code=embedding_code, model=embedding_model.strip() or embedding_code,
                                             dimension=dimension, metric_type=metric_type.strip() or "COSINE", endpoint_ref=endpoint_ref)
                session.add(embedding); session.flush()
            elif (embedding.model, embedding.dimension, embedding.metric_type) != (embedding_model.strip() or embedding_code, dimension, metric_type):
                raise ValueError("同一 Embedding 编码的模型、维度和度量类型必须保持稳定")
            storage_revision = None
            if collection_policy == "managed":
                storage_revision, collection_name = self._managed_storage_contract(
                    session, code, collection_name, embedding, storage_schema, fields, index_spec,
                )
            item = KnowledgeIndexProfile(id=new_id("index"), code=code, knowledge_type=knowledge_type.strip(), collection_name=collection_name,
                                         embedding_profile_id=embedding.id, fields_json=dict(fields), status="draft")
            session.add(item); session.flush()
            revision = KnowledgeIndexProfileRevision(id=new_id("indexrev"), knowledge_index_profile_id=item.id, revision_no=1,
                                                    collection_name=collection_name, embedding_profile_id=embedding.id, fields_json=dict(fields),
                                                    storage_contract_revision_id=storage_revision.id if storage_revision else None,
                                                    collection_policy=collection_policy)
            session.add(revision); self.audit(session, "index_profile.created", "index_profile", item.id)
            return {"id": item.id, "revision_id": revision.id, "revision": 1, "status": "draft"}

    def _managed_storage_contract(self, session: Session, code: str, collection_name: str,
                                  embedding: EmbeddingProfile, schema: dict[str, Any] | None,
                                  fields: dict[str, Any], index_spec: dict[str, Any] | None) -> tuple[StorageContractRevision, str]:
        if not isinstance(schema, dict) or not isinstance(schema.get("fields"), list):
            raise ValueError("受管 Index Profile 必须提供完整 storage_schema.fields")
        physical_names = {str(item.get("name")) for item in schema["fields"] if isinstance(item, dict)}
        if not set(str(value) for value in fields.values()).issubset(physical_names):
            raise ValueError("Storage Contract 缺少 Index Profile 映射的物理字段")
        index_json = index_spec or {"index_type": "AUTOINDEX"}
        spec_hash = storage_spec_hash(schema, embedding, index_json)
        revision = session.scalar(select(StorageContractRevision).where(StorageContractRevision.storage_spec_hash == spec_hash))
        if revision:
            managed = session.scalar(select(ManagedCollection).where(ManagedCollection.storage_contract_revision_id == revision.id))
            if not managed:
                raise ValueError("相同 Storage Contract 已存在但没有受管 Collection")
            return revision, managed.collection_name
        resolved_name = collection_name or f"dataforge_{code.replace('-', '_')}_knowledge"
        collision = session.scalar(select(ManagedCollection).where(ManagedCollection.collection_name == resolved_name))
        if collision:
            raise ValueError("同名受管 Collection 已绑定不兼容的 Storage Contract")
        contract = StorageContract(id=new_id("storage"), code=code, name=f"{code} 存储结构")
        session.add(contract); session.flush()
        revision = StorageContractRevision(
            id=new_id("storagerev"), storage_contract_id=contract.id, revision_no=1,
            schema_json=schema, embedding_profile_id=embedding.id, vector_type="FLOAT_VECTOR",
            dimension=embedding.dimension, metric_type=embedding.metric_type, index_json=index_json,
            storage_spec_hash=spec_hash, status="published", published_at=utc_now(),
        )
        session.add(revision); session.flush(); contract.current_revision_id = revision.id
        session.add(ManagedCollection(
            id=new_id("collection"), storage_contract_revision_id=revision.id,
            collection_name=resolved_name, provisioning_token=secrets.token_hex(24),
            desired_spec_hash=spec_hash, status="planned",
        ))
        return revision, resolved_name

    def revise_index_profile(self, profile_id: str, collection_name: str, embedding_code: str, embedding_model: str,
                             dimension: int, metric_type: str, endpoint_ref: str | None, fields: dict[str, Any],
                             *, collection_policy: str = "external", storage_schema: dict[str, Any] | None = None,
                             index_spec: dict[str, Any] | None = None) -> dict[str, Any]:
        self._validate_index_mapping(fields)
        if collection_policy not in {"external", "managed"}:
            raise ValueError("Collection 策略必须为 external 或 managed")
        with self.sessions.begin() as session:
            item = session.get(KnowledgeIndexProfile, profile_id)
            if not item:
                raise ValueError("Index Profile 不存在")
            embedding = session.scalar(select(EmbeddingProfile).where(EmbeddingProfile.code == embedding_code.strip()))
            if not embedding:
                embedding = EmbeddingProfile(id=new_id("embedding"), code=embedding_code.strip(), model=embedding_model.strip() or embedding_code.strip(), dimension=dimension, metric_type=metric_type, endpoint_ref=endpoint_ref)
                session.add(embedding); session.flush()
            elif (embedding.model, embedding.dimension, embedding.metric_type) != (embedding_model.strip() or embedding_code.strip(), dimension, metric_type):
                raise ValueError("同一 Embedding 编码的模型、维度和度量类型必须保持稳定")
            storage_revision = None
            if collection_policy == "managed":
                storage_revision, collection_name = self._managed_storage_contract(
                    session, item.code, collection_name.strip(), embedding, storage_schema, fields, index_spec,
                )
            elif not collection_name.strip():
                raise ValueError("外部 Index Profile 必须指定 Collection")
            latest = session.scalar(select(func.max(KnowledgeIndexProfileRevision.revision_no)).where(KnowledgeIndexProfileRevision.knowledge_index_profile_id == item.id)) or 0
            revision = KnowledgeIndexProfileRevision(id=new_id("indexrev"), knowledge_index_profile_id=item.id, revision_no=latest + 1,
                                                    collection_name=collection_name.strip(), embedding_profile_id=embedding.id, fields_json=dict(fields),
                                                    storage_contract_revision_id=storage_revision.id if storage_revision else None,
                                                    collection_policy=collection_policy)
            session.add(revision); self.audit(session, "index_profile.revised", "index_profile", item.id, {"revision": revision.revision_no})
            return {"id": item.id, "revision_id": revision.id, "revision": revision.revision_no, "status": "draft"}

    @staticmethod
    def _validate_index_mapping(fields: dict[str, Any]) -> None:
        if not isinstance(fields, dict) or set(DEFAULT_INDEX_FIELD_MAPPING) - set(fields):
            raise ValueError("字段映射必须包含 id、vector、knowledge_library_id、source_knowledge_id、content、data")
        values = [str(fields[key]).strip() for key in DEFAULT_INDEX_FIELD_MAPPING]
        if any(not value for value in values) or len(set(values)) != len(values):
            raise ValueError("字段映射不能为空且每个目标字段必须唯一")

    def validate_index_profile(self, profile_id: str, validator: Any | None = None) -> dict[str, Any]:
        with self.sessions() as session:
            item = session.get(KnowledgeIndexProfile, profile_id)
            if not item:
                raise ValueError("Index Profile 不存在")
            revision = session.scalar(select(KnowledgeIndexProfileRevision).where(KnowledgeIndexProfileRevision.knowledge_index_profile_id == item.id).order_by(KnowledgeIndexProfileRevision.revision_no.desc()))
            if not revision:
                raise ValueError("Index Profile 没有可发布修订")
            self._validate_index_mapping(revision.fields_json)
            embedding = session.get(EmbeddingProfile, revision.embedding_profile_id)
            if not embedding:
                raise ValueError("Embedding Profile 不存在")
            if revision.collection_policy == "managed":
                managed = session.scalar(select(ManagedCollection).where(
                    ManagedCollection.storage_contract_revision_id == revision.storage_contract_revision_id,
                    ManagedCollection.collection_name == revision.collection_name,
                ))
                if not managed or managed.status != "ready" or managed.observed_spec_hash != managed.desired_spec_hash:
                    raise ValueError("受管 Collection 尚未完成 Provision 或规格校验")
            else:
                if validator is None:
                    raise ValueError("未配置可验证的向量 Collection，不能发布 Index Profile")
                validator(revision.collection_name, revision.fields_json, embedding.dimension)
            return {"id": item.id, "revision_id": revision.id, "valid": True}

    def publish_index_profile(self, profile_id: str, validator: Any) -> dict[str, Any]:
        self.validate_index_profile(profile_id, validator)
        with self.sessions.begin() as session:
            item = session.get(KnowledgeIndexProfile, profile_id)
            revision = session.scalar(select(KnowledgeIndexProfileRevision).where(KnowledgeIndexProfileRevision.knowledge_index_profile_id == profile_id).order_by(KnowledgeIndexProfileRevision.revision_no.desc()))
            assert item and revision
            revision.status, revision.published_at = "published", utc_now()
            item.collection_name, item.embedding_profile_id, item.fields_json = revision.collection_name, revision.embedding_profile_id, revision.fields_json
            item.current_revision_id, item.status = revision.id, "active"
            self.audit(session, "index_profile.published", "index_profile", item.id, {"revision": revision.revision_no})
            return {"id": item.id, "revision_id": revision.id, "revision": revision.revision_no, "status": "published"}

    def list_operator_catalog(self, *, include_internal: bool = False) -> list[dict[str, Any]]:
        with self.sessions() as session:
            rows = session.execute(
                select(OperatorDefinition, OperatorVersion).join(OperatorVersion, OperatorVersion.operator_definition_id == OperatorDefinition.id)
                .where(OperatorVersion.status == "published").order_by(OperatorDefinition.category, OperatorDefinition.code, OperatorVersion.version_no.desc())
            ).all()
            values = []
            seen: set[str] = set()
            for definition, version in rows:
                if definition.code in seen or (definition.exposure == "internal" and not include_internal):
                    continue
                seen.add(definition.code)
                values.append({"id": definition.id, "code": definition.code, "name": definition.name, "description": definition.description, "category": definition.category,
                               "exposure": definition.exposure, "risk_level": definition.risk_level, "enabled": definition.enabled,
                               "version": version.version_no, "adapter_code": version.adapter_code,
                               "input_ports": version.input_ports, "output_ports": version.output_ports,
                               "input_example": version.input_example, "output_example": version.output_example,
                               "parameter_schema": version.parameter_schema, "runtime_requirements": version.runtime_requirements})
            return values

    def list_prompt_templates(self) -> list[dict[str, Any]]:
        with self.sessions() as session:
            values = []
            for item in session.scalars(select(PromptTemplate).order_by(PromptTemplate.code)):
                revisions = session.scalars(select(PromptTemplateRevision).where(PromptTemplateRevision.prompt_template_id == item.id).order_by(PromptTemplateRevision.revision_no.desc())).all()
                values.append({"id": item.id, "code": item.code, "name": item.name, "status": item.status,
                               "revisions": [{"id": rev.id, "revision": rev.revision_no, "status": rev.status,
                                              "input_schema": rev.input_schema, "output_schema": rev.output_schema,
                                              "published_at": rev.published_at.isoformat() if rev.published_at else None} for rev in revisions]})
            return values

    def list_quality_profiles(self) -> list[dict[str, Any]]:
        with self.sessions() as session:
            values = []
            for item in session.scalars(select(QualityProfile).order_by(QualityProfile.code)):
                revisions = session.scalars(select(QualityProfileRevision).where(QualityProfileRevision.quality_profile_id == item.id).order_by(QualityProfileRevision.revision_no.desc())).all()
                values.append({"id": item.id, "code": item.code, "name": item.name, "status": item.status,
                               "revisions": [{"id": rev.id, "revision": rev.revision_no, "status": rev.status,
                                              "rules": rev.rules_json, "published_at": rev.published_at.isoformat() if rev.published_at else None} for rev in revisions]})
            return values

    def create_prompt_template(self, code: str, name: str, body: str, input_schema: dict[str, Any], output_schema: dict[str, Any]) -> dict[str, Any]:
        if not code.strip() or not name.strip() or not body.strip():
            raise ValueError("Prompt 编码、名称和模板内容不能为空")
        with self.sessions.begin() as session:
            if session.scalar(select(PromptTemplate).where(PromptTemplate.code == code.strip())):
                raise ValueError("Prompt 编码已存在")
            prompt = PromptTemplate(id=new_id("prompt"), code=code.strip(), name=name.strip())
            session.add(prompt); session.flush()
            revision = PromptTemplateRevision(id=new_id("promptrev"), prompt_template_id=prompt.id, revision_no=1,
                                              body=body, input_schema=input_schema, output_schema=output_schema)
            session.add(revision); self.audit(session, "prompt_template.created", "prompt_template", prompt.id)
            return {"id": prompt.id, "revision_id": revision.id, "revision": 1, "status": "draft"}

    def publish_prompt_template(self, prompt_id: str) -> dict[str, Any]:
        with self.sessions.begin() as session:
            prompt = session.get(PromptTemplate, prompt_id)
            if not prompt:
                raise ValueError("Prompt Template 不存在")
            revision = session.scalar(select(PromptTemplateRevision).where(PromptTemplateRevision.prompt_template_id == prompt.id).order_by(PromptTemplateRevision.revision_no.desc()))
            if not revision:
                raise ValueError("Prompt Template 没有可发布修订")
            revision.status, revision.published_at, prompt.status = "published", utc_now(), "active"
            self.audit(session, "prompt_template.published", "prompt_template", prompt.id, {"revision": revision.revision_no})
            return {"id": prompt.id, "revision_id": revision.id, "revision": revision.revision_no, "status": "published"}

    def create_quality_profile(self, code: str, name: str, rules: dict[str, Any]) -> dict[str, Any]:
        if not code.strip() or not name.strip() or not isinstance(rules, dict):
            raise ValueError("Quality Profile 编码、名称和规则不能为空")
        with self.sessions.begin() as session:
            if session.scalar(select(QualityProfile).where(QualityProfile.code == code.strip())):
                raise ValueError("Quality Profile 编码已存在")
            profile = QualityProfile(id=new_id("quality"), code=code.strip(), name=name.strip())
            session.add(profile); session.flush()
            revision = QualityProfileRevision(id=new_id("qualityrev"), quality_profile_id=profile.id, revision_no=1, rules_json=rules)
            session.add(revision); self.audit(session, "quality_profile.created", "quality_profile", profile.id)
            return {"id": profile.id, "revision_id": revision.id, "revision": 1, "status": "draft"}

    def publish_quality_profile(self, profile_id: str) -> dict[str, Any]:
        with self.sessions.begin() as session:
            profile = session.get(QualityProfile, profile_id)
            if not profile:
                raise ValueError("Quality Profile 不存在")
            revision = session.scalar(select(QualityProfileRevision).where(QualityProfileRevision.quality_profile_id == profile.id).order_by(QualityProfileRevision.revision_no.desc()))
            if not revision:
                raise ValueError("Quality Profile 没有可发布修订")
            rules = revision.rules_json or {}
            for key in ("pass_score", "review_score"):
                if key in rules and not isinstance(rules[key], (int, float)):
                    raise ValueError(f"Quality Profile {key} 必须为数值")
            revision.status, revision.published_at, profile.status = "published", utc_now(), "active"
            self.audit(session, "quality_profile.published", "quality_profile", profile.id, {"revision": revision.revision_no})
            return {"id": profile.id, "revision_id": revision.id, "revision": revision.revision_no, "status": "published"}

    def list_subflows(self) -> list[dict[str, Any]]:
        with self.sessions() as session:
            values = []
            for item in session.scalars(select(FlowSubgraph).order_by(FlowSubgraph.code)):
                revision = session.scalar(select(FlowSubgraphRevision).where(FlowSubgraphRevision.flow_subgraph_id == item.id).order_by(FlowSubgraphRevision.revision_no.desc()))
                values.append({"id": item.id, "code": item.code, "name": item.name, "status": item.status,
                               "revision": revision.revision_no if revision else None,
                               "revision_status": revision.status if revision else None,
                               "definition": revision.definition_json if revision else None})
            return values

    def execution_snapshot_detail(self, snapshot_id: str) -> dict[str, Any]:
        with self.sessions() as session:
            value = session.get(FlowExecutionSnapshot, snapshot_id)
            if not value:
                raise ValueError("执行快照不存在")
            return {"id": value.id, "knowledge_flow_template_revision_id": value.knowledge_flow_template_revision_id,
                    "compiled_definition": value.compiled_definition_json, "dependencies": value.dependency_json,
                    "checksum": value.checksum, "status": value.status, "created_at": value.created_at.isoformat()}

    def list_flow_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.sessions() as session:
            return [{"id": item.id, "knowledge_job_id": item.knowledge_job_id, "execution_snapshot_id": item.execution_snapshot_id,
                     "status": item.status, "error": item.error, "created_at": item.created_at.isoformat(),
                     "completed_at": item.completed_at.isoformat() if item.completed_at else None}
                    for item in session.scalars(select(FlowRun).order_by(FlowRun.created_at.desc()).limit(limit))]

    def audit(self, session: Session, action: str, resource_type: str, resource_id: str, payload: dict[str, Any] | None = None) -> None:
        session.add(AuditEvent(
            id=new_id("audit"), actor="admin", action=action, resource_type=resource_type,
            resource_id=resource_id, payload_json=payload or {},
        ))

    @staticmethod
    def _library_payload(item: DocumentLibrary) -> dict[str, Any]:
        return {"id": item.id, "code": item.code, "name": item.name, "description": item.description, "status": item.status, "updated_at": item.updated_at.isoformat()}

    @staticmethod
    def _source_payload(source: Source, version: SourceVersion | None = None) -> dict[str, Any]:
        return {
            "id": source.id, "document_library_id": source.document_library_id, "name": source.name,
            "original_filename": source.original_filename, "relative_path": source.relative_path,
            "directory_path": source.directory_path, "source_kind": source.source_kind,
            "status": source.status, "current_version_id": source.current_version_id,
            "metadata": source.metadata_json, "updated_at": source.updated_at.isoformat(),
            "version": None if not version else {"id": version.id, "version_no": version.version_no, "sha256": version.sha256, "size_bytes": version.size_bytes, "mime_type": version.mime_type, "status": version.status, "extraction_status": version.extraction_status, "error": version.extraction_error},
        }

    def list_document_libraries(self, keyword: str = "", status: str | None = None) -> list[dict[str, Any]]:
        with self.sessions() as session:
            query = select(DocumentLibrary).order_by(DocumentLibrary.updated_at.desc())
            if keyword:
                query = query.where((DocumentLibrary.name.contains(keyword)) | (DocumentLibrary.code.contains(keyword)))
            if status:
                query = query.where(DocumentLibrary.status == status)
            return [self._library_payload(item) for item in session.scalars(query)]

    def create_document_library(self, name: str, description: str = "") -> dict[str, Any]:
        name = name.strip()
        if not name:
            raise ValueError("文档库名称不能为空")
        with self.sessions.begin() as session:
            date_part = utc_now().strftime("%Y%m%d")
            for _ in range(32):
                code = f"DL-{date_part}-{secrets.token_hex(3).upper()}"
                if not session.scalar(select(DocumentLibrary.id).where(DocumentLibrary.code == code)):
                    break
            else:
                raise ValueError("文档库编码生成失败，请稍后重试")
            item = DocumentLibrary(id=new_id("dl"), code=code, name=name, description=description.strip())
            session.add(item); self.audit(session, "document_library.created", "document_library", item.id)
            session.flush()
            return self._library_payload(item)

    def get_document_library(self, library_id: str) -> DocumentLibrary:
        with self.sessions() as session:
            value = session.get(DocumentLibrary, library_id)
            if not value:
                raise ValueError("文档库不存在")
            return value

    def create_source(
        self, *, library_id: str, name: str, filename: str, object_key: str, sha256: str,
        size_bytes: int, mime_type: str, metadata: dict[str, Any] | None = None, relative_path: str | None = None,
    ) -> dict[str, Any]:
        if not name.strip() or size_bytes <= 0:
            raise ValueError("文件名称不能为空且文件必须非空")
        with self.sessions.begin() as session:
            if not session.get(DocumentLibrary, library_id):
                raise ValueError("文档库不存在")
            relative_path, directory_path = normalise_relative_path(relative_path or filename)
            source = Source(id=new_id("src"), document_library_id=library_id, name=name.strip(), original_filename=filename,
                            relative_path=relative_path, relative_path_hash=relative_path_hash(relative_path),
                            directory_path=directory_path, directory_path_hash=relative_path_hash(directory_path), metadata_json=metadata or {})
            # SQLAlchemy has no ORM relationship that expresses the child-row
            # dependencies below.  Persist the parent explicitly so MySQL never
            # flushes a library member before its Source exists.
            session.add(source)
            session.flush()
            version = SourceVersion(id=new_id("srcv"), source_id=source.id, version_no=1, object_key=object_key, sha256=sha256, size_bytes=size_bytes, mime_type=mime_type)
            source.current_version_id = version.id
            session.add_all([version, DocumentLibraryMember(id=new_id("dlm"), document_library_id=library_id, source_id=source.id)])
            self.audit(session, "source.uploaded", "source", source.id, {"source_version_id": version.id})
            session.flush()
            return self._source_payload(source, version)

    def replace_source(
        self, *, source_id: str, filename: str, object_key: str, sha256: str, size_bytes: int, mime_type: str,
    ) -> dict[str, Any]:
        with self.sessions.begin() as session:
            source = session.get(Source, source_id)
            if not source or source.status == "deleted":
                raise ValueError("待替换文件不存在或已删除")
            current = session.get(SourceVersion, source.current_version_id)
            if current:
                current.status = "superseded"
            version_no = (session.scalar(select(func.max(SourceVersion.version_no)).where(SourceVersion.source_id == source.id)) or 0) + 1
            version = SourceVersion(id=new_id("srcv"), source_id=source.id, version_no=version_no, object_key=object_key, sha256=sha256, size_bytes=size_bytes, mime_type=mime_type)
            source.current_version_id, source.original_filename, source.status = version.id, filename, "uploaded"
            session.add(version)
            # The new source version is processed asynchronously.  Existing
            # formal evidence is retained until its result replaces it; this
            # prevents a brief no-knowledge window for multi-source graph data.
            self.audit(session, "source.replaced", "source", source.id, {"source_version_id": version.id})
            session.flush()
            return self._source_payload(source, version)

    def source_for_upload(self, source_id: str) -> Source:
        with self.sessions() as session:
            source = session.get(Source, source_id)
            if not source:
                raise ValueError("文件不存在")
            return source

    def delete_source(self, source_id: str) -> dict[str, Any]:
        raise ValueError("请先执行删除影响预检并提交确认短语")

    def retry_source(self, source_id: str) -> dict[str, Any]:
        with self.sessions.begin() as session:
            source = session.get(Source, source_id)
            if not source or not source.current_version_id:
                raise ValueError("文件不存在")
            version = session.get(SourceVersion, source.current_version_id)
            version.extraction_status, version.extraction_error, source.status = "pending", None, "uploaded"
            self.audit(session, "source.retry_requested", "source", source_id)
            session.flush()
            return self._source_payload(source, version)

    def list_sources(self, library_id: str | None = None, keyword: str = "", status: str | None = None) -> list[dict[str, Any]]:
        with self.sessions() as session:
            query = select(Source).order_by(Source.updated_at.desc())
            if library_id:
                query = query.where(Source.document_library_id == library_id)
            if keyword:
                query = query.where((Source.name.contains(keyword)) | (Source.original_filename.contains(keyword)))
            if status:
                query = query.where(Source.status == status)
            items = []
            for source in session.scalars(query):
                items.append(self._source_payload(source, session.get(SourceVersion, source.current_version_id) if source.current_version_id else None))
            return items

    def document_tree(self, library_id: str) -> dict[str, Any]:
        with self.sessions() as session:
            if not session.get(DocumentLibrary, library_id):
                raise ValueError("文档库不存在")
            paths = session.scalars(select(Source.directory_path).where(
                Source.document_library_id == library_id, Source.status.not_in(("deleted", "deleting")),
            )).all()
        root: dict[str, Any] = {"name": "全部文件", "path": "", "children": {}, "file_count": 0}
        for directory in paths:
            node = root
            node["file_count"] += 1
            for part in filter(None, directory.split("/")):
                node = node["children"].setdefault(part, {"name": part, "path": (f"{node['path']}/{part}".strip("/")), "children": {}, "file_count": 0})
                node["file_count"] += 1

        def serialise(node: dict[str, Any]) -> dict[str, Any]:
            return {key: value for key, value in node.items() if key != "children"} | {
                "children": [serialise(item) for item in sorted(node["children"].values(), key=lambda item: item["name"].lower())]
            }
        return serialise(root)

    def list_library_sources(self, library_id: str, *, path: str | None = None, keyword: str = "", status: str | None = None,
                             file_type: str | None = None, page: int = 1, page_size: int = 50) -> dict[str, Any]:
        page, page_size = max(1, page), min(max(1, page_size), 200)
        with self.sessions() as session:
            if not session.get(DocumentLibrary, library_id):
                raise ValueError("文档库不存在")
            query = select(Source).where(Source.document_library_id == library_id).order_by(Source.updated_at.desc())
            if path is not None:
                normalized, _ = normalise_relative_path(f"{path}/placeholder") if path else ("", "")
                directory = normalized.rsplit("/", 1)[0] if normalized else ""
                query = query.where(
                    Source.directory_path_hash == relative_path_hash(directory),
                    Source.directory_path == directory,
                )
            if keyword:
                query = query.where((Source.name.contains(keyword)) | (Source.original_filename.contains(keyword)) | (Source.relative_path.contains(keyword)))
            if status:
                query = query.where(Source.status == status)
            if file_type:
                query = query.where(Source.original_filename.ilike(f"%.{file_type.lstrip('.').lower()}"))
            total = int(session.scalar(select(func.count()).select_from(query.subquery())) or 0)
            rows = session.scalars(query.offset((page - 1) * page_size).limit(page_size)).all()
            return {"items": [self._source_payload(row, session.get(SourceVersion, row.current_version_id) if row.current_version_id else None) for row in rows],
                    "page": page, "page_size": page_size, "total": total}

    def source_by_relative_path(self, library_id: str, relative_path: str) -> Source | None:
        normalized, _ = normalise_relative_path(relative_path)
        with self.sessions() as session:
            return session.scalar(select(Source).where(
                Source.document_library_id == library_id,
                Source.relative_path_hash == relative_path_hash(normalized),
                Source.relative_path == normalized,
            ))

    def available_relative_path(self, library_id: str, relative_path: str) -> str:
        normalized, directory = normalise_relative_path(relative_path)
        name = PurePosixPath(normalized).name
        stem, suffix = PurePosixPath(name).stem, PurePosixPath(name).suffix
        with self.sessions() as session:
            existing = set(session.scalars(select(Source.relative_path).where(Source.document_library_id == library_id)).all())
        if normalized not in existing:
            return normalized
        number = 2
        while True:
            candidate = "/".join(filter(None, (directory, f"{stem} ({number}){suffix}")))
            if candidate not in existing:
                return candidate
            number += 1

    def source_versions(self, source_id: str) -> list[dict[str, Any]]:
        with self.sessions() as session:
            if not session.get(Source, source_id):
                raise ValueError("文件不存在")
            values = session.scalars(select(SourceVersion).where(SourceVersion.source_id == source_id).order_by(SourceVersion.version_no.desc())).all()
            return [{"id": item.id, "version_no": item.version_no, "sha256": item.sha256, "object_key": item.object_key, "status": item.status, "size_bytes": item.size_bytes, "extraction_status": item.extraction_status, "error": item.extraction_error, "created_at": item.created_at.isoformat()} for item in values]

    def bind_document_library_template(self, document_library_id: str, template_id: str) -> dict[str, Any]:
        """Attach a published template and lazily create its stable result libraries."""
        with self.sessions.begin() as session:
            document_library = session.get(DocumentLibrary, document_library_id)
            if not document_library or document_library.status != "active":
                raise ValueError("文档库不存在或不可用")
            template, _ = self._published_template_revision(session, template_id)
            binding = session.scalar(select(DocumentLibraryTemplateBinding).where(
                DocumentLibraryTemplateBinding.document_library_id == document_library.id,
                DocumentLibraryTemplateBinding.knowledge_flow_template_id == template.id,
            ))
            if not binding:
                binding = DocumentLibraryTemplateBinding(id=new_id("docbind"), document_library_id=document_library.id,
                                                        knowledge_flow_template_id=template.id)
                session.add(binding); session.flush()
            binding.status = "active"
            self._ensure_document_binding_outputs(session, document_library, template, binding)
            self.audit(session, "document_library.template_bound", "document_library_template_binding", binding.id,
                       {"template_id": template.id})
            session.flush()
            return self._document_binding_payload(session, binding)

    def unbind_document_library_template(self, document_library_id: str, template_id: str) -> dict[str, Any]:
        with self.sessions.begin() as session:
            binding = session.scalar(select(DocumentLibraryTemplateBinding).where(
                DocumentLibraryTemplateBinding.document_library_id == document_library_id,
                DocumentLibraryTemplateBinding.knowledge_flow_template_id == template_id,
            ))
            if not binding or binding.status != "active":
                raise ValueError("文档库没有该活动模板绑定")
            binding.status = "removed"
            self.audit(session, "document_library.template_unbound", "document_library_template_binding", binding.id)
            return {"id": binding.id, "status": binding.status}

    def _ensure_document_binding_outputs(self, session: Session, document_library: DocumentLibrary,
                                         template: KnowledgeFlowTemplate, binding: DocumentLibraryTemplateBinding) -> None:
        existing = {item.output_key: item for item in session.scalars(select(DocumentLibraryTemplateOutput).where(
            DocumentLibraryTemplateOutput.document_library_template_binding_id == binding.id,
        ))}
        for raw_output in template.output_types:
            output_key = normalise_output_key(raw_output)
            knowledge_type, graph_mode = output_contract(output_key)
            if output_key in existing:
                continue
            type_row = session.scalar(select(KnowledgeType).where(KnowledgeType.code == knowledge_type, KnowledgeType.status == "active"))
            revision = session.get(KnowledgeTypeRevision, type_row.current_revision_id) if type_row and type_row.current_revision_id else None
            if not revision or revision.status != "published":
                raise ValueError(f"输出知识类型 {knowledge_type} 不存在已发布契约")
            indexes = session.scalars(select(KnowledgeIndexProfile).join(KnowledgeTypeIndexBinding,
                KnowledgeTypeIndexBinding.index_profile_id == KnowledgeIndexProfile.id).where(
                    KnowledgeTypeIndexBinding.knowledge_type_revision_id == revision.id,
                    KnowledgeIndexProfile.status == "active",
                ).order_by(KnowledgeIndexProfile.code)).all()
            if graph_mode:
                indexes = [item for item in indexes if item.code == f"graph-{graph_mode}"]
            if not indexes:
                raise ValueError(f"输出 {output_key} 没有已发布 Index Profile")
            embedding = session.get(EmbeddingProfile, indexes[0].embedding_profile_id) if indexes else None
            library = KnowledgeLibrary(id=new_id("kl"), code=generated_business_code("KL"),
                name=f"{document_library.name} · {template.name} · {type_row.name}{' · ' + graph_mode if graph_mode else ''}"[:255], knowledge_type=knowledge_type,
                graph_mode=graph_mode,
                knowledge_type_revision_id=revision.id, description=f"文档库自动结果库：{document_library.name} / {template.name}",
                embedding_profile_id=embedding.id if embedding else None, index_profile_id=indexes[0].id if indexes else None,
                partition_name="")
            library.partition_name = f"kl_{library.id}"
            session.add(library); session.flush()
            session.add(DocumentLibraryTemplateOutput(id=new_id("docout"), document_library_template_binding_id=binding.id,
                                                      knowledge_type=knowledge_type, output_key=output_key,
                                                      graph_mode=graph_mode, knowledge_library_id=library.id))

    def _binding_pending_versions(self, session: Session, binding: DocumentLibraryTemplateBinding,
                                  revision: KnowledgeFlowTemplateRevision) -> list[str]:
        active_versions = list(session.scalars(select(Source.current_version_id).where(
            Source.document_library_id == binding.document_library_id, Source.status == "uploaded",
            Source.current_version_id.is_not(None),
        )))
        processed = set(session.scalars(select(DocumentLibraryProcessingRecord.source_version_id).where(
            DocumentLibraryProcessingRecord.document_library_template_binding_id == binding.id,
            DocumentLibraryProcessingRecord.knowledge_flow_template_revision_id == revision.id,
        )))
        in_flight = set()
        for job in session.scalars(select(KnowledgeJob).where(
            KnowledgeJob.document_library_template_binding_id == binding.id,
            KnowledgeJob.knowledge_flow_template_revision_id == revision.id,
            KnowledgeJob.status.in_(("queued", "running")),
        )):
            in_flight.update(job.source_version_ids or [])
        # A published template revision supersedes prior successful records, so
        # all current versions become pending once; queued/running jobs still
        # suppress duplicate dispatch before that run succeeds.
        candidates = active_versions if binding.last_successful_revision_id != revision.id else [item for item in active_versions if item not in processed]
        return [item for item in candidates if item not in in_flight]

    def _document_binding_payload(self, session: Session, binding: DocumentLibraryTemplateBinding) -> dict[str, Any]:
        document_library = session.get(DocumentLibrary, binding.document_library_id)
        template, revision = self._published_template_revision(session, binding.knowledge_flow_template_id)
        self._ensure_document_binding_outputs(session, document_library, template, binding)
        outputs = session.execute(select(DocumentLibraryTemplateOutput, KnowledgeLibrary).join(
            KnowledgeLibrary, KnowledgeLibrary.id == DocumentLibraryTemplateOutput.knowledge_library_id,
        ).where(DocumentLibraryTemplateOutput.document_library_template_binding_id == binding.id)).all()
        latest_job = session.scalar(select(KnowledgeJob).where(
            KnowledgeJob.document_library_template_binding_id == binding.id,
        ).order_by(KnowledgeJob.created_at.desc()))
        return {"id": binding.id, "status": binding.status, "template": {"id": template.id, "code": template.code,
                "name": template.name, "revision": revision.revision_no, "revision_id": revision.id},
                "outputs": [{"knowledge_type": item.knowledge_type, "knowledge_library": self._knowledge_library_payload(library)} for item, library in outputs],
                "pending_file_count": len(self._binding_pending_versions(session, binding, revision)),
                "latest_job": self.job_payload(latest_job) if latest_job else None}

    def list_document_library_template_bindings(self, document_library_id: str) -> list[dict[str, Any]]:
        with self.sessions.begin() as session:
            if not session.get(DocumentLibrary, document_library_id):
                raise ValueError("文档库不存在")
            return [self._document_binding_payload(session, item) for item in session.scalars(select(DocumentLibraryTemplateBinding).where(
                DocumentLibraryTemplateBinding.document_library_id == document_library_id,
            ).order_by(DocumentLibraryTemplateBinding.created_at.desc()))]

    def process_document_library(self, document_library_id: str) -> list[dict[str, Any]]:
        """Queue only current versions that were not successfully handled by the current template revision."""
        return self._process_document_library(document_library_id)

    def process_selected_document_sources(self, document_library_id: str, source_ids: list[str]) -> list[dict[str, Any]]:
        """Queue selected current sources that are pending for each active binding."""
        selected_ids = list(dict.fromkeys(source_id for source_id in source_ids if source_id))
        if not selected_ids:
            raise ValueError("至少选择一个文件")
        return self._process_document_library(document_library_id, selected_ids)

    def _process_document_library(self, document_library_id: str, source_ids: list[str] | None = None) -> list[dict[str, Any]]:
        """Queue pending current versions for every active template binding."""
        with self.sessions.begin() as session:
            document_library = session.get(DocumentLibrary, document_library_id)
            if not document_library or document_library.status != "active":
                raise ValueError("文档库不存在或不可用")
            selected_versions: set[str] | None = None
            if source_ids is not None:
                selected_sources = session.scalars(select(Source).where(Source.id.in_(source_ids))).all()
                if len(selected_sources) != len(source_ids) or any(source.document_library_id != document_library_id for source in selected_sources):
                    raise ValueError("所选文件不存在或不属于当前文档库")
                selected_versions = {
                    source.current_version_id for source in selected_sources
                    if source.status == "uploaded" and source.current_version_id
                }
            jobs: list[tuple[list[str], dict[str, str], str, str]] = []
            for binding in session.scalars(select(DocumentLibraryTemplateBinding).where(
                DocumentLibraryTemplateBinding.document_library_id == document_library_id,
                DocumentLibraryTemplateBinding.status == "active",
            )):
                template, revision = self._published_template_revision(session, binding.knowledge_flow_template_id)
                self._ensure_document_binding_outputs(session, document_library, template, binding)
                versions = self._binding_pending_versions(session, binding, revision)
                if selected_versions is not None:
                    versions = [version_id for version_id in versions if version_id in selected_versions]
                if not versions:
                    continue
                outputs = {item.output_key: item.knowledge_library_id for item in session.scalars(select(DocumentLibraryTemplateOutput).where(
                    DocumentLibraryTemplateOutput.document_library_template_binding_id == binding.id,
                    DocumentLibraryTemplateOutput.output_key.in_([normalise_output_key(value) for value in template.output_types]),
                ))}
                jobs.append((versions, outputs, template.id, binding.id))
        return [self.create_knowledge_job(versions, outputs, template_id, binding_id)
                for versions, outputs, template_id, binding_id in jobs]

    def source_detail(self, source_id: str, version_id: str | None = None, flow_run_id: str | None = None) -> dict[str, Any]:
        with self.sessions() as session:
            source = session.get(Source, source_id)
            if not source:
                raise ValueError("文件不存在")
            versions = session.scalars(select(SourceVersion).where(SourceVersion.source_id == source.id).order_by(SourceVersion.version_no.desc())).all()
            version = next((item for item in versions if item.id == version_id), None) if version_id else session.get(SourceVersion, source.current_version_id)
            if version_id and not version:
                raise ValueError("文件版本不存在")
            jobs = [job for job in session.scalars(select(KnowledgeJob).order_by(KnowledgeJob.created_at.desc())).all() if version and version.id in (job.source_version_ids or [])]
            runs = session.scalars(select(FlowRun).where(FlowRun.knowledge_job_id.in_([job.id for job in jobs] or [""])).order_by(FlowRun.created_at.desc())).all()
            active_run = next((run for run in runs if run.id == flow_run_id), None) if flow_run_id else next((run for run in runs if run.status in {"completed", "completed_with_warnings"}), None)
            ir = session.scalar(select(DocumentIR).where(DocumentIR.source_version_id == version.id, DocumentIR.flow_run_id == active_run.id)) if version and active_run else None
            chunks = session.scalars(select(SourceChunk).where(SourceChunk.source_version_id == version.id, SourceChunk.flow_run_id == active_run.id).order_by(SourceChunk.chunk_index)).all() if version and active_run else []
            parser_artifacts = session.scalars(select(Artifact).where(
                Artifact.source_version_id == version.id,
                Artifact.type_code.like("parser.%"),
            ).order_by(Artifact.created_at.desc())).all() if version else []
            evidence_rows = session.execute(select(KnowledgeItemSource, KnowledgeItem, KnowledgeLibrary).join(KnowledgeItem, KnowledgeItem.id == KnowledgeItemSource.knowledge_item_id).join(KnowledgeLibrary, KnowledgeLibrary.id == KnowledgeItem.knowledge_library_id).where(KnowledgeItemSource.source_version_id == version.id)).all() if version else []
            return {"source": self._source_payload(source, version), "versions": [self._source_payload(source, item)["version"] for item in versions],
                    "jobs": [self.job_payload(job) for job in jobs], "flow_runs": [{"id": run.id, "status": run.status, "error": run.error, "created_at": run.created_at.isoformat(), "completed_at": run.completed_at.isoformat() if run.completed_at else None} for run in runs],
                    "document_ir": None if not ir else {"id": ir.id, "text": ir.text, "parser_adapter": ir.parser_adapter, "parser_profile": ir.parser_profile, "anchor": ir.anchor_json, "status": ir.status, "error": ir.error},
                    "parser_artifacts": [{"id": item.id, "type": item.type_code, "flow_run_id": item.flow_run_id,
                                          "uri": item.uri, "checksum": item.checksum, "metadata": item.data_json,
                                          "created_at": item.created_at.isoformat()} for item in parser_artifacts],
                    "source_chunks": [{"id": item.id, "source_chunk_id": item.source_chunk_id, "chunk_index": item.chunk_index, "content": item.content, "anchor": item.anchor_json} for item in chunks],
                    "knowledge_results": [{"knowledge_item_id": item.id, "knowledge_library_id": library.id, "knowledge_library_name": library.name, "content": item.canonical_content, "status": item.status, "anchor": link.anchor_json, "evidence_text": link.evidence_text} for link, item, library in evidence_rows]}

    def source_version_for_download(self, source_id: str, version_id: str) -> SourceVersion:
        with self.sessions() as session:
            version = session.get(SourceVersion, version_id)
            if not version or version.source_id != source_id or version.status == "deleted":
                raise ValueError("文件版本不存在或已删除")
            return version

    def record_document_irs(self, flow_run_id: str, documents: list[dict[str, Any]]) -> None:
        with self.sessions.begin() as session:
            for document in documents:
                version = session.get(SourceVersion, document["source_version_id"])
                if not version:
                    continue
                row = session.scalar(select(DocumentIR).where(DocumentIR.source_version_id == version.id, DocumentIR.flow_run_id == flow_run_id))
                if not row:
                    row = DocumentIR(id=new_id("dir"), source_version_id=version.id, flow_run_id=flow_run_id,
                                     parser_adapter=str(document.get("parser_adapter", "document-parser")), parser_profile=str(document.get("runtime_profile", "auto")),
                                     text=str(document.get("text", "")), anchor_json=dict(document.get("anchor") or {}), checksum=hashlib.sha256(str(document.get("text", "")).encode("utf-8")).hexdigest())
                    session.add(row)
                version.extraction_status, version.extraction_error = "completed", None

    def record_source_chunks(self, flow_run_id: str, chunks: list[dict[str, Any]]) -> None:
        with self.sessions.begin() as session:
            for chunk in chunks:
                version_id, index = str(chunk["source_version_id"]), int(chunk["chunk_index"])
                if session.scalar(select(SourceChunk.id).where(SourceChunk.source_version_id == version_id, SourceChunk.flow_run_id == flow_run_id, SourceChunk.chunk_index == index)):
                    continue
                content = str(chunk.get("content", ""))
                session.add(SourceChunk(id=new_id("sch"), source_version_id=version_id, flow_run_id=flow_run_id,
                                        source_chunk_id=str(chunk.get("source_chunk_id", "")), chunk_index=index, content=content,
                                        anchor_json=dict(chunk.get("anchor") or {}), content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest()))

    def mark_source_versions_failed(self, version_ids: list[str], error: str) -> None:
        with self.sessions.begin() as session:
            for version in session.scalars(select(SourceVersion).where(SourceVersion.id.in_(version_ids))):
                version.extraction_status, version.extraction_error = "failed", error

    def document_deletion_preflight(self, *, source_ids: list[str] | None = None, document_library_ids: list[str] | None = None) -> dict[str, Any]:
        source_ids, document_library_ids = list(dict.fromkeys(source_ids or [])), list(dict.fromkeys(document_library_ids or []))
        if bool(source_ids) == bool(document_library_ids):
            raise ValueError("必须且只能选择文件或文档库中的一种删除目标")
        with self.sessions() as session:
            sources = session.scalars(select(Source).where(Source.id.in_(source_ids))).all() if source_ids else session.scalars(select(Source).where(Source.document_library_id.in_(document_library_ids))).all()
            if source_ids and len({item.id for item in sources}) != len(source_ids):
                raise ValueError("删除目标不存在")
            if document_library_ids and len(set(session.scalars(select(DocumentLibrary.id).where(DocumentLibrary.id.in_(document_library_ids))).all())) != len(document_library_ids):
                raise ValueError("删除目标不存在")
            version_ids = list(session.scalars(select(SourceVersion.id).where(SourceVersion.source_id.in_([item.id for item in sources]))))
            running = [job.id for job in session.scalars(select(KnowledgeJob).where(KnowledgeJob.status.in_(("queued", "running")))).all() if set(job.source_version_ids or []) & set(version_ids)]
            evidence_rows = session.execute(select(KnowledgeItemSource, KnowledgeItem, KnowledgeLibrary).join(
                KnowledgeItem, KnowledgeItem.id == KnowledgeItemSource.knowledge_item_id,
            ).join(KnowledgeLibrary, KnowledgeLibrary.id == KnowledgeItem.knowledge_library_id).where(
                KnowledgeItemSource.source_version_id.in_(version_ids),
            )).all() if version_ids else []
            blockers = []
            if running: blockers.append({"kind": "running_job", "ids": running})
            source_count, library_count = len(sources), len(document_library_ids)
            return {"deletable": not blockers, "source_count": source_count, "document_library_count": library_count, "blockers": blockers,
                    "source_ids": [item.id for item in sources], "document_library_ids": document_library_ids,
                    "impact": {"knowledge_item_count": len({item.id for _, item, _ in evidence_rows}),
                               "knowledge_library_count": len({library.id for _, _, library in evidence_rows}),
                               "graph_relation_count": sum(1 for _, item, _ in evidence_rows if item.data_json.get("subject") and item.data_json.get("predicate")),
                               "vector_record_count": session.scalar(select(func.count()).select_from(VectorRecordState).join(
                                    KnowledgeItem, KnowledgeItem.id == VectorRecordState.knowledge_item_id,
                               ).join(KnowledgeItemSource, KnowledgeItemSource.knowledge_item_id == KnowledgeItem.id).where(
                                    KnowledgeItemSource.source_version_id.in_(version_ids),
                               )) or 0,
                               "knowledge_libraries": sorted({library.name for _, _, library in evidence_rows})}}

    def request_document_deletion(self, *, source_ids: list[str] | None = None, document_library_ids: list[str] | None = None) -> dict[str, Any]:
        check = self.document_deletion_preflight(source_ids=source_ids, document_library_ids=document_library_ids)
        if not check["deletable"]:
            raise ValueError("删除被运行任务阻断")
        with self.sessions.begin() as session:
            versions = session.scalars(select(SourceVersion).where(SourceVersion.source_id.in_(check["source_ids"]))).all()
            version_ids = [item.id for item in versions]
            parser_keys = [
                str(item.data_json.get("object_key")) for item in session.scalars(select(Artifact).where(
                    Artifact.source_version_id.in_(version_ids), Artifact.type_code.like("parser.%"),
                )) if item.data_json.get("object_key")
            ] if version_ids else []
            self._remove_source_evidence(session, [item.id for item in versions], deletion_job_id=None)
            for source in session.scalars(select(Source).where(Source.id.in_(check["source_ids"]))):
                source.status = "deleting"
            for library in session.scalars(select(DocumentLibrary).where(DocumentLibrary.id.in_(check["document_library_ids"]))):
                library.status = "deleting"
            job = DocumentDeletionJob(id=new_id("deldoc"), target_kind="sources" if source_ids else "libraries", source_ids=check["source_ids"], document_library_ids=check["document_library_ids"], object_keys=[item.object_key for item in versions] + parser_keys, status="queued")
            session.add(job); self.audit(session, "document.deletion_queued", "document_deletion_job", job.id, {"source_count": len(check["source_ids"])})
            return {"id": job.id, "status": job.status, **check}

    def _remove_source_evidence(self, session: Session, source_version_ids: list[str], deletion_job_id: str | None) -> None:
        """Apply published source policy before physical source-object deletion."""
        links = list(session.scalars(select(KnowledgeItemSource).where(KnowledgeItemSource.source_version_id.in_(source_version_ids))))
        if not links:
            return
        template = session.scalar(select(KnowledgeFlowTemplate).where(KnowledgeFlowTemplate.status == "active").order_by(KnowledgeFlowTemplate.code))
        revision = session.scalar(select(KnowledgeFlowTemplateRevision).where(
            KnowledgeFlowTemplateRevision.knowledge_flow_template_id == template.id,
            KnowledgeFlowTemplateRevision.status == "published",
        ).order_by(KnowledgeFlowTemplateRevision.revision_no.desc())) if template else None
        if not template or not revision:
            raise ValueError("没有可用于记录来源删除的已发布流程模板")
        audit_job = KnowledgeJob(id=new_id("kj"), knowledge_flow_template_id=template.id,
                                 knowledge_flow_template_revision_id=revision.id, source_version_ids=list(source_version_ids),
                                 output_library_ids={}, sink_library_ids={}, execution_snapshot_id=revision.execution_snapshot_id,
                                 status="completed", stage="source_deletion")
        session.add(audit_job); session.flush()
        by_item: dict[str, list[KnowledgeItemSource]] = {}
        for link in links:
            by_item.setdefault(link.knowledge_item_id, []).append(link)
        for item_id, item_links in by_item.items():
            item = session.get(KnowledgeItem, item_id)
            if not item:
                continue
            revision = session.get(KnowledgeTypeRevision, item.knowledge_type_revision_id) if item.knowledge_type_revision_id else None
            policy = revision.source_policy if revision else "single"
            all_links = list(session.scalars(select(KnowledgeItemSource).where(KnowledgeItemSource.knowledge_item_id == item.id)))
            remaining = [link for link in all_links if link.source_version_id not in set(source_version_ids)]
            session.execute(delete(KnowledgeItemSource).where(KnowledgeItemSource.id.in_([link.id for link in item_links])))
            should_inactivate = policy == "single" or not remaining
            if should_inactivate and item.status == "active":
                item.status = "inactive"
                session.add(KnowledgeChange(id=new_id("kc"), knowledge_job_id=audit_job.id, knowledge_library_id=item.knowledge_library_id,
                    knowledge_item_id=item.id, change_type="INACTIVE", before_hash=item.content_hash,
                    details_json={"reason": "source_deleted", "source_version_ids": source_version_ids, "document_deletion_job_id": deletion_job_id},
                    before_snapshot_json={"content": item.canonical_content, "data": item.data_json, "status": "active"},
                    after_snapshot_json={"content": item.canonical_content, "data": item.data_json, "status": "inactive"}))
                self._queue_vector_deletions_for_item(session, item)
            else:
                self.audit(session, "knowledge.evidence_removed", "knowledge_item", item.id,
                           {"source_version_ids": source_version_ids, "remaining_evidence": len(remaining)})

    def _queue_vector_deletions_for_item(self, session: Session, item: KnowledgeItem) -> None:
        states = list(session.scalars(select(VectorRecordState).where(VectorRecordState.knowledge_item_id == item.id)))
        for state in states:
            existing = session.scalar(select(VectorDeletionJob).where(
                VectorDeletionJob.knowledge_library_id == item.knowledge_library_id,
                VectorDeletionJob.index_profile_id == state.index_profile_id,
                VectorDeletionJob.status.in_(("queued", "running", "failed")),
            ).order_by(VectorDeletionJob.created_at.desc()))
            if existing:
                if state.vector_id not in (existing.vector_ids or []):
                    existing.vector_ids = [*(existing.vector_ids or []), state.vector_id]
            else:
                session.add(VectorDeletionJob(id=new_id("vdj"), knowledge_library_id=item.knowledge_library_id,
                                              index_profile_id=state.index_profile_id, vector_ids=[state.vector_id]))

    def claim_document_deletion_job(self, owner: str) -> DocumentDeletionJob | None:
        with self.sessions.begin() as session:
            job = session.scalar(select(DocumentDeletionJob).where((DocumentDeletionJob.status == "queued") | ((DocumentDeletionJob.status == "running") & (DocumentDeletionJob.lease_expires_at < utc_now()))).order_by(DocumentDeletionJob.created_at).with_for_update(skip_locked=True).limit(1))
            if not job: return None
            job.status, job.lease_owner, job.lease_expires_at = "running", owner, utc_now() + timedelta(minutes=5)
            return job

    def finish_document_deletion(self, job_id: str, error: str | None = None) -> dict[str, Any]:
        with self.sessions.begin() as session:
            job = session.get(DocumentDeletionJob, job_id)
            if not job: raise ValueError("文档删除任务不存在")
            if error:
                job.status, job.error, job.lease_owner = "failed", error, None
                return {"id": job.id, "status": job.status, "error": error}
            version_ids = list(session.scalars(select(SourceVersion.id).where(SourceVersion.source_id.in_(job.source_ids))))
            artifact_ids = list(session.scalars(select(Artifact.id).where(Artifact.source_version_id.in_(version_ids)))) if version_ids else []
            if artifact_ids:
                session.execute(delete(ArtifactLineage).where(
                    (ArtifactLineage.parent_artifact_id.in_(artifact_ids)) | (ArtifactLineage.child_artifact_id.in_(artifact_ids))
                ))
                session.execute(delete(Artifact).where(Artifact.id.in_(artifact_ids)))
            for model, column in ((SourceChunk, SourceChunk.source_version_id), (DocumentIR, DocumentIR.source_version_id), (DocumentLibraryProcessingRecord, DocumentLibraryProcessingRecord.source_version_id), (KnowledgeChunkGeneration, KnowledgeChunkGeneration.source_version_id), (KnowledgeItemSource, KnowledgeItemSource.source_version_id), (DocumentLibraryMember, DocumentLibraryMember.source_id), (SourceVersion, SourceVersion.source_id), (Source, Source.id)):
                session.execute(delete(model).where(column.in_(job.source_ids if model in (DocumentLibraryMember, SourceVersion, Source) else version_ids)))
            if job.document_library_ids:
                binding_ids = list(session.scalars(select(DocumentLibraryTemplateBinding.id).where(
                    DocumentLibraryTemplateBinding.document_library_id.in_(job.document_library_ids),
                )))
                if binding_ids:
                    # A completed (or otherwise historical) job remains the audit
                    # record for the deleted document library.  Its binding is only
                    # optional provenance, so release the nullable FK before the
                    # binding row is removed.
                    session.execute(update(KnowledgeJob).where(
                        KnowledgeJob.document_library_template_binding_id.in_(binding_ids),
                    ).values(document_library_template_binding_id=None))
                    session.execute(delete(DocumentLibraryTemplateOutput).where(
                        DocumentLibraryTemplateOutput.document_library_template_binding_id.in_(binding_ids),
                    ))
                    session.execute(delete(DocumentLibraryTemplateBinding).where(
                        DocumentLibraryTemplateBinding.id.in_(binding_ids),
                    ))
                session.execute(delete(DocumentLibrary).where(DocumentLibrary.id.in_(job.document_library_ids)))
            job.status, job.error, job.lease_owner, job.lease_expires_at = "completed", None, None, None
            self.audit(session, "document.deletion_completed", "document_deletion_job", job.id)
            return {"id": job.id, "status": job.status}

    def retry_document_deletion(self, job_id: str) -> dict[str, Any]:
        with self.sessions.begin() as session:
            job = session.get(DocumentDeletionJob, job_id)
            if not job or job.status != "failed": raise ValueError("仅可重试失败的文档删除任务")
            job.status, job.error, job.lease_owner, job.lease_expires_at = "queued", None, None, None
            return {"id": job.id, "status": job.status}

    @staticmethod
    def _knowledge_library_payload(item: KnowledgeLibrary, ready: bool | None = None) -> dict[str, Any]:
        result = {"id": item.id, "code": item.code, "name": item.name, "knowledge_type": item.knowledge_type,
                  "graph_mode": item.graph_mode, "display_type": ({"triple": "三元组图谱", "semantic": "语义图谱（LightRAG 模式）"}.get(item.graph_mode) if item.knowledge_type == "graph" else None),
                  "knowledge_type_revision_id": item.knowledge_type_revision_id, "description": item.description, "embedding_profile_id": item.embedding_profile_id, "index_profile_id": item.index_profile_id, "partition_name": item.partition_name, "status": item.status, "updated_at": item.updated_at.isoformat()}
        if ready is not None:
            result["vector_ready"] = ready
        return result

    def create_knowledge_library(self, name: str, knowledge_type: str, description: str = "", graph_mode: str | None = None, *, code: str | None = None) -> dict[str, Any]:
        """Create a library with a platform-owned code.

        The three-positional-argument form is retained only for old internal
        callers during the V7 replacement; HTTP handlers never pass ``code``.
        """
        # Old test and internal callers used (code, name, knowledge_type).
        if description in V7_TYPE_META and code is None:
            code, name, knowledge_type, description = name, knowledge_type, description, ""
        name, knowledge_type = name.strip(), knowledge_type.strip()
        graph_mode = (graph_mode or ("triple" if knowledge_type == "graph" else None))
        if knowledge_type == "graph" and graph_mode not in {"triple", "semantic"}:
            raise ValueError("图谱知识库必须选择 triple 或 semantic 模式")
        if knowledge_type != "graph" and graph_mode is not None:
            raise ValueError("只有图谱知识库可以设置 graph_mode")
        code = (code or generated_business_code("KL")).strip()
        if not name:
            raise ValueError("知识库名称不能为空")
        with self.sessions.begin() as session:
            if session.scalar(select(KnowledgeLibrary).where(KnowledgeLibrary.code == code)):
                raise ValueError("知识库编码已存在")
            type_row = session.scalar(select(KnowledgeType).where(KnowledgeType.code == knowledge_type, KnowledgeType.status == "active"))
            revision = session.get(KnowledgeTypeRevision, type_row.current_revision_id) if type_row and type_row.current_revision_id else None
            if not revision or revision.status != "published":
                raise ValueError("知识类型不存在或没有已发布契约")
            indexes = session.scalars(
                select(KnowledgeIndexProfile).join(KnowledgeTypeIndexBinding, KnowledgeTypeIndexBinding.index_profile_id == KnowledgeIndexProfile.id)
                .where(KnowledgeTypeIndexBinding.knowledge_type_revision_id == revision.id, KnowledgeIndexProfile.status == "active")
                .order_by(KnowledgeIndexProfile.code)
            ).all()
            if knowledge_type == "graph":
                expected_code = f"graph-{graph_mode}"
                indexes = [item for item in indexes if item.code == expected_code]
                if not indexes:
                    raise ValueError(f"图谱模式 {graph_mode} 没有已发布 Index Profile")
            profile = session.get(EmbeddingProfile, indexes[0].embedding_profile_id) if indexes else None
            library = KnowledgeLibrary(
                id=new_id("kl"), code=code, name=name, knowledge_type=knowledge_type, graph_mode=graph_mode,
                knowledge_type_revision_id=revision.id, description=description.strip(),
                embedding_profile_id=profile.id if profile else None, index_profile_id=indexes[0].id if indexes else None,
                # A V7 library id is the partition identity; an org code never is.
                partition_name="",
            )
            library.partition_name = f"kl_{library.id}"
            session.add(library); self.audit(session, "knowledge_library.created", "knowledge_library", library.id)
            session.flush()
            return self._knowledge_library_payload(library, False)

    def _library_ready(self, session: Session, library: KnowledgeLibrary) -> bool:
        if library.status != "active":
            return False
        active = session.scalar(select(func.count()).select_from(KnowledgeItem).where(KnowledgeItem.knowledge_library_id == library.id, KnowledgeItem.status == "active")) or 0
        if active == 0:
            return False
        profile_ids = [item.id for item in self._index_profile_snapshots_for_library(session, library)]
        ready = session.scalar(select(func.count()).select_from(VectorRecordState).join(KnowledgeItem).where(KnowledgeItem.knowledge_library_id == library.id, KnowledgeItem.status == "active", VectorRecordState.index_profile_id.in_(profile_ids), VectorRecordState.status == "ready")) or 0
        return ready >= active * len(profile_ids)

    @staticmethod
    def _profile_ids_for_library(session: Session, library: KnowledgeLibrary) -> list[str]:
        if library.knowledge_type_revision_id:
            values = list(session.scalars(
                select(KnowledgeTypeIndexBinding.index_profile_id).where(
                    KnowledgeTypeIndexBinding.knowledge_type_revision_id == library.knowledge_type_revision_id,
                )
            ))
            if library.knowledge_type == "graph" and library.graph_mode:
                frozen = session.get(KnowledgeIndexProfile, library.index_profile_id) if library.index_profile_id else None
                expected = "graph" if frozen and frozen.code == "graph" else f"graph-{library.graph_mode}"
                return [item.id for item in session.scalars(select(KnowledgeIndexProfile).where(
                    KnowledgeIndexProfile.id.in_(values), KnowledgeIndexProfile.code == expected,
                ))]
            return values
        return list(session.scalars(select(KnowledgeIndexProfile.id).where(KnowledgeIndexProfile.knowledge_type == library.knowledge_type)))

    @staticmethod
    def _index_profile_snapshots_for_library(session: Session, library: KnowledgeLibrary) -> list[SimpleNamespace]:
        """Return the Profile revision frozen by the library's type revision."""
        if library.knowledge_type_revision_id:
            bindings = list(session.scalars(select(KnowledgeTypeIndexBinding).where(
                KnowledgeTypeIndexBinding.knowledge_type_revision_id == library.knowledge_type_revision_id,
            )))
            snapshots: list[SimpleNamespace] = []
            frozen = session.get(KnowledgeIndexProfile, library.index_profile_id) if library.knowledge_type == "graph" and library.index_profile_id else None
            expected_graph_profile = "graph" if frozen and frozen.code == "graph" else f"graph-{library.graph_mode}"
            for binding in bindings:
                profile = session.get(KnowledgeIndexProfile, binding.index_profile_id)
                if library.knowledge_type == "graph" and library.graph_mode and profile and profile.code != expected_graph_profile:
                    continue
                revision = session.get(KnowledgeIndexProfileRevision, binding.index_profile_revision_id) if binding.index_profile_revision_id else None
                revision = revision or (session.get(KnowledgeIndexProfileRevision, profile.current_revision_id) if profile and profile.current_revision_id else None)
                if not profile or profile.status != "active" or not revision or revision.status != "published":
                    continue
                snapshots.append(SimpleNamespace(id=profile.id, code=profile.code, knowledge_type=profile.knowledge_type,
                    collection_name=revision.collection_name, embedding_profile_id=revision.embedding_profile_id,
                    fields_json=revision.fields_json, revision_id=revision.id,
                    storage_contract_revision_id=revision.storage_contract_revision_id,
                    collection_policy=revision.collection_policy))
            return snapshots
        return [SimpleNamespace(id=item.id, code=item.code, knowledge_type=item.knowledge_type,
            collection_name=item.collection_name, embedding_profile_id=item.embedding_profile_id,
            fields_json=item.fields_json, revision_id=item.current_revision_id,
            storage_contract_revision_id=None, collection_policy="external")
            for item in session.scalars(select(KnowledgeIndexProfile).where(
                KnowledgeIndexProfile.knowledge_type == library.knowledge_type,
                KnowledgeIndexProfile.status == "active",
            ))]

    def list_knowledge_libraries(self, knowledge_type: str | None = None) -> list[dict[str, Any]]:
        with self.sessions() as session:
            query = select(KnowledgeLibrary).order_by(KnowledgeLibrary.updated_at.desc())
            if knowledge_type:
                query = query.where(KnowledgeLibrary.knowledge_type == knowledge_type)
            return [self._knowledge_library_payload(item, self._library_ready(session, item)) for item in session.scalars(query)]

    def get_knowledge_library(self, library_id: str) -> KnowledgeLibrary:
        with self.sessions() as session:
            item = session.get(KnowledgeLibrary, library_id)
            if not item:
                raise ValueError("知识库不存在")
            return item

    def knowledge_library_delete_check(self, library_id: str) -> dict[str, Any]:
        with self.sessions() as session:
            library = session.get(KnowledgeLibrary, library_id)
            if not library or library.status != "active":
                raise ValueError("知识库不存在")
            rows = session.execute(
                select(Project, ProjectTask, ProjectOrgRoute).join(ProjectTask, ProjectTask.project_id == Project.id)
                .join(ProjectOrgRoute, ProjectOrgRoute.project_task_id == ProjectTask.id)
                .join(ProjectOrgRouteLibrary, ProjectOrgRouteLibrary.project_org_route_id == ProjectOrgRoute.id)
                .where(ProjectOrgRouteLibrary.knowledge_library_id == library_id)
                .order_by(Project.code, ProjectTask.code, ProjectOrgRoute.org_code)
            ).all()
            references = [{"project_id": project.id, "project_code": project.code, "project_name": project.name,
                           "task_code": task.code, "task_name": task.name, "org_code": route.org_code,
                           "route_status": route.status} for project, task, route in rows]
            binding_rows = session.execute(
                select(DocumentLibrary, KnowledgeFlowTemplate, DocumentLibraryTemplateBinding)
                .join(DocumentLibraryTemplateBinding, DocumentLibraryTemplateBinding.document_library_id == DocumentLibrary.id)
                .join(DocumentLibraryTemplateOutput, DocumentLibraryTemplateOutput.document_library_template_binding_id == DocumentLibraryTemplateBinding.id)
                .join(KnowledgeFlowTemplate, KnowledgeFlowTemplate.id == DocumentLibraryTemplateBinding.knowledge_flow_template_id)
                .where(DocumentLibraryTemplateOutput.knowledge_library_id == library_id, DocumentLibraryTemplateBinding.status == "active")
            ).all()
            binding_references = [{"document_library_id": doc.id, "document_library_name": doc.name,
                                   "template_id": template.id, "template_code": template.code,
                                   "binding_id": binding.id} for doc, template, binding in binding_rows]
            return {"knowledge_library_id": library.id, "status": library.status,
                    "deletable": library.status == "active" and not references and not binding_references,
                    "references": references, "template_binding_references": binding_references}

    def request_knowledge_library_deletion(self, library_id: str) -> dict[str, Any]:
        with self.sessions.begin() as session:
            library = session.scalar(select(KnowledgeLibrary).where(KnowledgeLibrary.id == library_id).with_for_update())
            if not library:
                raise ValueError("知识库不存在")
            references = session.scalar(select(func.count()).select_from(ProjectOrgRouteLibrary).where(
                ProjectOrgRouteLibrary.knowledge_library_id == library_id,
            )) or 0
            binding_references = session.scalar(select(func.count()).select_from(DocumentLibraryTemplateOutput)
                .join(DocumentLibraryTemplateBinding, DocumentLibraryTemplateBinding.id == DocumentLibraryTemplateOutput.document_library_template_binding_id)
                .where(DocumentLibraryTemplateOutput.knowledge_library_id == library_id, DocumentLibraryTemplateBinding.status == "active")) or 0
            if references or binding_references:
                raise ValueError("知识库仍被路由或文档库模板绑定引用，不能删除")
            if library.status == "deleted":
                raise ValueError("知识库已经删除")
            queued = session.scalar(select(KnowledgeLibraryDeletionJob).where(
                KnowledgeLibraryDeletionJob.knowledge_library_id == library.id,
                KnowledgeLibraryDeletionJob.status.in_(("queued", "running", "failed")),
            ).order_by(KnowledgeLibraryDeletionJob.created_at.desc()))
            if queued:
                return {"id": queued.id, "knowledge_library_id": library.id, "status": queued.status, "idempotent": True}
            library.status = "deleting"
            job = KnowledgeLibraryDeletionJob(id=new_id("kldel"), knowledge_library_id=library.id)
            session.add(job); self.audit(session, "knowledge_library.deletion_queued", "knowledge_library", library.id, {"job_id": job.id})
            return {"id": job.id, "knowledge_library_id": library.id, "status": job.status}

    def list_library_deletion_jobs(self, library_id: str) -> list[dict[str, Any]]:
        with self.sessions() as session:
            return [{"id": item.id, "knowledge_library_id": item.knowledge_library_id, "status": item.status,
                     "error": item.error, "created_at": item.created_at.isoformat(), "updated_at": item.updated_at.isoformat()}
                    for item in session.scalars(select(KnowledgeLibraryDeletionJob).where(
                        KnowledgeLibraryDeletionJob.knowledge_library_id == library_id,
                    ).order_by(KnowledgeLibraryDeletionJob.created_at.desc()))]

    def retry_library_deletion(self, job_id: str) -> dict[str, Any]:
        with self.sessions.begin() as session:
            job = session.get(KnowledgeLibraryDeletionJob, job_id)
            if not job or job.status != "failed":
                raise ValueError("仅可重试失败的知识库删除任务")
            job.status, job.error, job.lease_owner, job.lease_expires_at = "queued", None, None, None
            self.audit(session, "knowledge_library.deletion_retried", "knowledge_library", job.knowledge_library_id, {"job_id": job.id})
            return {"id": job.id, "status": job.status}

    def claim_library_deletion_job(self, owner: str) -> KnowledgeLibraryDeletionJob | None:
        with self.sessions.begin() as session:
            job = session.scalar(select(KnowledgeLibraryDeletionJob).where(
                (KnowledgeLibraryDeletionJob.status == "queued") |
                ((KnowledgeLibraryDeletionJob.status == "running") & (KnowledgeLibraryDeletionJob.lease_expires_at < utc_now())),
            ).order_by(KnowledgeLibraryDeletionJob.created_at).with_for_update(skip_locked=True).limit(1))
            if not job:
                return None
            job.status, job.lease_owner, job.lease_expires_at = "running", owner, utc_now() + timedelta(minutes=5)
            return job

    def library_deletion_context(self, job_id: str) -> dict[str, Any]:
        with self.sessions() as session:
            job = session.get(KnowledgeLibraryDeletionJob, job_id)
            if not job:
                raise ValueError("知识库删除任务不存在")
            library = session.get(KnowledgeLibrary, job.knowledge_library_id)
            if not library or library.status != "deleting":
                raise ValueError("知识库不处于删除状态")
            profiles = self._index_profile_snapshots_for_library(session, library)
            return {"job": job, "library": library, "profiles": profiles}

    def finish_library_deletion(self, job_id: str, error: str | None = None) -> dict[str, Any]:
        with self.sessions.begin() as session:
            job = session.get(KnowledgeLibraryDeletionJob, job_id)
            if not job:
                raise ValueError("知识库删除任务不存在")
            library = session.get(KnowledgeLibrary, job.knowledge_library_id)
            if error:
                job.status, job.error, job.lease_owner, job.lease_expires_at = "failed", error, None, None
                self.audit(session, "knowledge_library.deletion_failed", "knowledge_library", job.knowledge_library_id, {"job_id": job.id, "error": error})
                return {"id": job.id, "status": job.status, "error": error}
            library.status = "deleted"
            for item in session.scalars(select(KnowledgeItem).where(KnowledgeItem.knowledge_library_id == library.id, KnowledgeItem.status == "active")):
                item.status = "inactive"
            for item in session.scalars(select(VectorRecordState).join(KnowledgeItem).where(KnowledgeItem.knowledge_library_id == library.id)):
                item.status, item.error = "deleted", None
            job.status, job.error, job.lease_owner, job.lease_expires_at = "deleted", None, None, None
            self.audit(session, "knowledge_library.deleted", "knowledge_library", library.id, {"job_id": job.id})
            return {"id": job.id, "status": job.status, "knowledge_library_id": library.id}

    @staticmethod
    def _normalise_template_definition(definition: dict[str, Any], output_types: list[str]) -> dict[str, Any]:
        """Accept the retired linear payload once, then persist only Flow DSL v2."""
        value = dict(definition or {})
        if "steps" in value:
            steps = list(value.get("steps") or [])
            if steps != list(LINEAR_TEMPLATE_STEPS):
                raise ValueError("旧模板必须使用固定线性阶段：" + " → ".join(LINEAR_TEMPLATE_STEPS))
            chunk_size = dict(value.get("parameters") or {}).get("chunk_size", 800)
            if not isinstance(chunk_size, int) or not 100 <= chunk_size <= 4000:
                raise ValueError("chunk_size 必须是 100–4000 的整数")
            return builtin_flow_definition(output_types)
        if int(value.get("schema_version", 0)) not in {2, 3}:
            raise ValueError("Flow 必须使用 schema_version=2 或 3 的受控 DSL")
        if any(node.get("kind") in {"shell", "python", "loop", "script"} for node in value.get("nodes", []) if isinstance(node, dict)):
            raise ValueError("Flow 不允许 Shell、任意 Python、循环或运行时改图")
        return value

    def _published_type_revisions(self, session: Session) -> dict[str, dict[str, Any]]:
        rows = session.execute(
            select(KnowledgeType.code, KnowledgeTypeRevision.id, KnowledgeTypeRevision.revision_no)
            .join(KnowledgeTypeRevision, KnowledgeTypeRevision.id == KnowledgeType.current_revision_id)
            .where(KnowledgeType.status == "active", KnowledgeTypeRevision.status == "published")
        ).all()
        return {code: {"id": revision_id, "revision": revision_no} for code, revision_id, revision_no in rows}

    def _published_subflows(self, session: Session) -> dict[str, dict[str, Any]]:
        rows = session.execute(
            select(FlowSubgraph.code, FlowSubgraphRevision.definition_json)
            .join(FlowSubgraphRevision, FlowSubgraphRevision.flow_subgraph_id == FlowSubgraph.id)
            .where(FlowSubgraph.status == "active", FlowSubgraphRevision.status == "published")
        ).all()
        return {code: value for code, value in rows}

    def _compile_template_definition(self, session: Session, definition: dict[str, Any], output_types: list[str]) -> dict[str, Any]:
        normalized = self._normalise_template_definition(definition, output_types)
        try:
            compiled = FlowCompiler(
                catalog=catalog_by_code(), subflows=self._published_subflows(session),
                type_revisions=self._published_type_revisions(session),
            ).compile(normalized)
        except FlowValidationError as exc:
            raise ValueError(str(exc)) from exc
        declared_sinks = set(compiled["compiled_definition"]["sink_types"].values())
        if declared_sinks != {normalise_output_key(value) for value in output_types}:
            raise ValueError("Flow Knowledge Sink 必须与模板输出知识类型完全一致")
        for node in compiled["compiled_definition"]["nodes"]:
            if node.get("kind") != "operator":
                continue
            params = node.get("params") or {}
            ref = node.get("ref")
            if ref in {"prompt-generator", "structured-knowledge-generator"}:
                prompt_id = params.get("prompt_template_revision_id")
                if not prompt_id or not session.scalar(select(PromptTemplateRevision.id).where(PromptTemplateRevision.id == prompt_id, PromptTemplateRevision.status == "published")):
                    raise ValueError("Prompt Generator 只能引用已发布 Prompt Template Revision")
            if ref in {"quality-evaluator", "quality-filter", "prompted-refiner"}:
                quality_id = params.get("quality_profile_revision_id")
                if not quality_id or not session.scalar(select(QualityProfileRevision.id).where(QualityProfileRevision.id == quality_id, QualityProfileRevision.status == "published")):
                    raise ValueError("质量节点只能引用已发布 Quality Profile Revision")
        return {"definition": normalized, **compiled}

    @staticmethod
    def _snapshot_checksum(revision_id: str, checksum: str) -> str:
        return hashlib.sha256(f"{revision_id}:{checksum}".encode("utf-8")).hexdigest()

    def _create_execution_snapshot(self, session: Session, revision: KnowledgeFlowTemplateRevision, output_types: list[str]) -> FlowExecutionSnapshot:
        compiled = self._compile_template_definition(session, revision.definition_json, output_types)
        snapshot_checksum = self._snapshot_checksum(revision.id, compiled["checksum"])
        snapshot = session.scalar(select(FlowExecutionSnapshot).where(FlowExecutionSnapshot.checksum == snapshot_checksum))
        if not snapshot:
            snapshot = FlowExecutionSnapshot(
                id=new_id("flowsnap"), knowledge_flow_template_revision_id=revision.id,
                compiled_definition_json=compiled["compiled_definition"],
                dependency_json={"dependencies": compiled["dependencies"], "source_checksum": compiled["checksum"]},
                checksum=snapshot_checksum,
            )
            session.add(snapshot); session.flush()
        revision.execution_snapshot_id = snapshot.id
        return snapshot

    def _published_template_revision(self, session: Session, template_id: str) -> tuple[KnowledgeFlowTemplate, KnowledgeFlowTemplateRevision]:
        template = session.get(KnowledgeFlowTemplate, template_id)
        if not template or template.status != "active":
            raise ValueError("知识流程模板不存在或不可用")
        revision = session.scalar(
            select(KnowledgeFlowTemplateRevision).where(
                KnowledgeFlowTemplateRevision.knowledge_flow_template_id == template.id,
                KnowledgeFlowTemplateRevision.status == "published",
            ).order_by(KnowledgeFlowTemplateRevision.revision_no.desc())
        )
        if not revision:
            raise ValueError("知识流程模板没有已发布修订")
        return template, revision

    def list_flow_templates(self) -> list[dict[str, Any]]:
        with self.sessions() as session:
            values = []
            for item in session.scalars(select(KnowledgeFlowTemplate).order_by(KnowledgeFlowTemplate.code)):
                revision = session.scalar(select(KnowledgeFlowTemplateRevision).where(
                    KnowledgeFlowTemplateRevision.knowledge_flow_template_id == item.id,
                ).order_by(KnowledgeFlowTemplateRevision.revision_no.desc()))
                values.append({"id": item.id, "code": item.code, "name": item.name, "output_types": item.output_types,
                               "definition": revision.definition_json if revision else item.definition_json,
                               "status": item.status, "is_default": item.is_default,
                               "revision": revision.revision_no if revision else None,
                               "revision_status": revision.status if revision else None,
                               "execution_snapshot_id": revision.execution_snapshot_id if revision else None})
            return values

    def create_flow_template(self, code: str, name: str, output_types: list[str], definition: dict[str, Any]) -> dict[str, Any]:
        code, name = code.strip(), name.strip()
        if not code or not name or not output_types:
            raise ValueError("模板编码、名称和输出知识类型不合法")
        with self.sessions.begin() as session:
            active_types = self._published_type_revisions(session)
            if {output_contract(value)[0] for value in output_types} - set(active_types):
                raise ValueError("模板引用了未发布知识类型")
            definition = self._compile_template_definition(session, definition, sorted(set(output_types)))["definition"]
            if session.scalar(select(KnowledgeFlowTemplate).where(KnowledgeFlowTemplate.code == code)):
                raise ValueError("模板编码已存在")
            template = KnowledgeFlowTemplate(id=new_id("flow"), code=code, name=name, output_types=sorted(set(output_types)), definition_json=definition, status="draft")
            session.add(template); session.flush()
            revision = KnowledgeFlowTemplateRevision(id=new_id("flowrev"), knowledge_flow_template_id=template.id, revision_no=1, definition_json=definition, status="draft")
            session.add(revision); self.audit(session, "flow_template.created", "knowledge_flow_template", template.id)
            return {"id": template.id, "revision": revision.revision_no, "status": template.status}

    def update_flow_template(self, template_id: str, name: str, output_types: list[str], definition: dict[str, Any]) -> dict[str, Any]:
        if not name.strip() or not output_types:
            raise ValueError("模板名称或输出知识类型不合法")
        with self.sessions.begin() as session:
            if {output_contract(value)[0] for value in output_types} - set(self._published_type_revisions(session)):
                raise ValueError("模板引用了未发布知识类型")
            definition = self._compile_template_definition(session, definition, sorted(set(output_types)))["definition"]
            template = session.get(KnowledgeFlowTemplate, template_id)
            if not template or template.status == "archived":
                raise ValueError("模板不存在或已归档")
            latest = session.scalar(select(KnowledgeFlowTemplateRevision).where(
                KnowledgeFlowTemplateRevision.knowledge_flow_template_id == template.id,
            ).order_by(KnowledgeFlowTemplateRevision.revision_no.desc()))
            template.name, template.output_types, template.definition_json = name.strip(), sorted(set(output_types)), definition
            if latest and latest.status == "draft":
                latest.definition_json = definition
            else:
                latest = KnowledgeFlowTemplateRevision(id=new_id("flowrev"), knowledge_flow_template_id=template.id,
                    revision_no=(latest.revision_no if latest else 0) + 1, definition_json=definition, status="draft")
                session.add(latest)
            self.audit(session, "flow_template.updated", "knowledge_flow_template", template.id, {"revision": latest.revision_no})
            return {"id": template.id, "revision": latest.revision_no, "status": latest.status}

    def publish_flow_template(self, template_id: str) -> dict[str, Any]:
        with self.sessions.begin() as session:
            template = session.get(KnowledgeFlowTemplate, template_id)
            if not template or template.status == "archived":
                raise ValueError("模板不存在或已归档")
            latest = session.scalar(select(KnowledgeFlowTemplateRevision).where(
                KnowledgeFlowTemplateRevision.knowledge_flow_template_id == template.id,
            ).order_by(KnowledgeFlowTemplateRevision.revision_no.desc()))
            if not latest:
                raise ValueError("模板没有可发布修订")
            latest.definition_json = self._compile_template_definition(session, latest.definition_json, template.output_types)["definition"]
            latest.status, latest.published_at, template.status = "published", utc_now(), "active"
            snapshot = self._create_execution_snapshot(session, latest, template.output_types)
            self.audit(session, "flow_template.published", "knowledge_flow_template", template.id, {"revision": latest.revision_no})
            return {"id": template.id, "revision": latest.revision_no, "status": "published", "execution_snapshot_id": snapshot.id}

    def set_default_flow_template(self, template_id: str) -> dict[str, Any]:
        with self.sessions.begin() as session:
            template, _ = self._published_template_revision(session, template_id)
            signature = template_signature(template.output_types)
            for item in session.scalars(select(KnowledgeFlowTemplate).where(KnowledgeFlowTemplate.status == "active")):
                if template_signature(item.output_types) == signature:
                    item.is_default = item.id == template.id
            self.audit(session, "flow_template.default_set", "knowledge_flow_template", template.id, {"output_signature": signature})
            return {"id": template.id, "is_default": True}

    def archive_flow_template(self, template_id: str) -> dict[str, Any]:
        with self.sessions.begin() as session:
            template = session.get(KnowledgeFlowTemplate, template_id)
            if not template:
                raise ValueError("模板不存在")
            template.status, template.is_default = "archived", False
            self.audit(session, "flow_template.archived", "knowledge_flow_template", template.id)
            return {"id": template.id, "status": template.status}

    def validate_flow_template(self, template_id: str) -> dict[str, Any]:
        with self.sessions() as session:
            template = session.get(KnowledgeFlowTemplate, template_id)
            if not template:
                raise ValueError("模板不存在")
            revision = session.scalar(select(KnowledgeFlowTemplateRevision).where(
                KnowledgeFlowTemplateRevision.knowledge_flow_template_id == template.id,
            ).order_by(KnowledgeFlowTemplateRevision.revision_no.desc()))
            if not revision:
                raise ValueError("模板没有修订")
            compiled = self._compile_template_definition(session, revision.definition_json, template.output_types)
            return {"valid": True, "template_id": template.id, "revision": revision.revision_no,
                    "definition": compiled["definition"], "compiled_definition": compiled["compiled_definition"],
                    "checksum": compiled["checksum"]}

    def create_knowledge_job(self, source_version_ids: list[str], output_library_ids: dict[str, str], template_id: str,
                             document_library_template_binding_id: str | None = None) -> dict[str, Any]:
        if not source_version_ids or not output_library_ids:
            raise ValueError("至少选择一个来源版本和一个目标知识库")
        normalized_outputs = {normalise_output_key(key): value for key, value in output_library_ids.items()}
        with self.sessions.begin() as session:
            template, revision = self._published_template_revision(session, template_id)
            if set(normalized_outputs) - {normalise_output_key(value) for value in template.output_types}:
                raise ValueError("目标知识类型不在所选流程模板输出范围内")
            source_versions = session.scalars(select(SourceVersion).where(SourceVersion.id.in_(source_version_ids), SourceVersion.status == "active")).all()
            if len(source_versions) != len(set(source_version_ids)):
                raise ValueError("来源版本不存在或不是当前有效版本")
            for output_type, library_id in normalized_outputs.items():
                knowledge_type, graph_mode = output_contract(output_type)
                library = session.get(KnowledgeLibrary, library_id)
                if not library or library.status != "active" or library.knowledge_type != knowledge_type or graph_mode and library.graph_mode != graph_mode:
                    raise ValueError(f"{output_type} 必须显式绑定同类型的有效知识库")
            snapshot = session.get(FlowExecutionSnapshot, revision.execution_snapshot_id) if revision.execution_snapshot_id else None
            if not snapshot:
                raise ValueError("流程已发布修订缺少不可变执行快照")
            job = KnowledgeJob(id=new_id("kj"), knowledge_flow_template_id=template.id, knowledge_flow_template_revision_id=revision.id,
                                source_version_ids=list(dict.fromkeys(source_version_ids)), output_library_ids=dict(normalized_outputs),
                                sink_library_ids=dict(normalized_outputs), execution_snapshot_id=snapshot.id,
                                document_library_template_binding_id=document_library_template_binding_id)
            session.add(job); self.audit(session, "knowledge_job.created", "knowledge_job", job.id, {"outputs": normalized_outputs})
            session.flush()
            return self.job_payload(job)

    @staticmethod
    def job_payload(job: KnowledgeJob) -> dict[str, Any]:
        return {"id": job.id, "knowledge_flow_template_id": job.knowledge_flow_template_id, "knowledge_flow_template_revision_id": job.knowledge_flow_template_revision_id, "execution_snapshot_id": job.execution_snapshot_id, "document_library_template_binding_id": job.document_library_template_binding_id, "source_version_ids": job.source_version_ids, "sink_library_ids": job.sink_library_ids or job.output_library_ids, "output_library_ids": job.output_library_ids, "status": job.status, "stage": job.stage, "attempt_count": job.attempt_count, "error": job.error, "warning_count": 0, "failed_chunk_count": 0, "created_at": job.created_at.isoformat()}

    @staticmethod
    def _generation_payload(row: KnowledgeChunkGeneration) -> dict[str, Any]:
        return {
            "id": row.id,
            "knowledge_type": row.knowledge_type,
            "source_version_id": row.source_version_id,
            "source_chunk_id": row.source_chunk_id,
            "chunk_index": row.chunk_index,
            "status": row.status,
            "candidate_count": row.candidate_count,
            "attempt_count": row.attempt_count,
            "error": row.error,
            "updated_at": row.updated_at.isoformat(),
        }

    def _job_payload_with_generation_summary(self, session: Session, job: KnowledgeJob) -> dict[str, Any]:
        payload = self.job_payload(job)
        failed = list(session.scalars(select(KnowledgeChunkGeneration).where(
            KnowledgeChunkGeneration.knowledge_job_id == job.id,
            KnowledgeChunkGeneration.status == "failed",
        )))
        payload["failed_chunk_count"] = len(failed)
        payload["warning_count"] = len(failed) if job.status == "completed_with_warnings" else 0
        return payload

    def template_definition_for_job(self, job_id: str) -> dict[str, Any]:
        with self.sessions() as session:
            job = session.get(KnowledgeJob, job_id)
            if not job:
                raise ValueError("知识任务不存在")
            snapshot = session.get(FlowExecutionSnapshot, job.execution_snapshot_id) if job.execution_snapshot_id else None
            if not snapshot:
                raise ValueError("知识任务缺少执行快照")
            return snapshot.compiled_definition_json

    def type_contracts_for_job(self, job_id: str) -> dict[str, dict[str, Any]]:
        with self.sessions() as session:
            job = session.get(KnowledgeJob, job_id)
            if not job:
                raise ValueError("知识任务不存在")
            values: dict[str, dict[str, Any]] = {}
            for output_type, library_id in (job.sink_library_ids or job.output_library_ids).items():
                library = session.get(KnowledgeLibrary, library_id)
                revision = session.get(KnowledgeTypeRevision, library.knowledge_type_revision_id) if library and library.knowledge_type_revision_id else None
                if not revision:
                    continue
                mode_revision = None
                if library.knowledge_type == "graph" and library.graph_mode:
                    mode_revision = session.scalar(select(KnowledgeTypeModeRevision).where(
                        KnowledgeTypeModeRevision.knowledge_type_revision_id == revision.id,
                        KnowledgeTypeModeRevision.mode == library.graph_mode,
                        KnowledgeTypeModeRevision.status == "published",
                    ).order_by(KnowledgeTypeModeRevision.revision_no.desc()))
                prompt_body = ""
                template = session.get(KnowledgeFlowTemplate, job.knowledge_flow_template_id)
                definition = session.get(KnowledgeFlowTemplateRevision, job.knowledge_flow_template_revision_id).definition_json if job.knowledge_flow_template_revision_id else (template.definition_json if template else {})
                params = dict((definition or {}).get("parameters") or {})
                prompt_revision_id = params.get("prompt_template_revision_id")
                if not prompt_revision_id:
                    prompt_revision_id = next((dict(node.get("params") or {}).get("prompt_template_revision_id")
                        for node in (definition or {}).get("nodes", [])
                        if dict(node.get("params") or {}).get("knowledge_type") == output_type and
                        dict(node.get("params") or {}).get("prompt_template_revision_id")), None)
                prompt = session.get(PromptTemplateRevision, prompt_revision_id) if prompt_revision_id else None
                if prompt and prompt.status == "published":
                    prompt_body = prompt.body
                values[output_type] = {
                    "schema": mode_revision.schema_json if mode_revision else revision.schema_json,
                    "canonical_field": revision.canonical_field,
                    "canonical_fields": mode_revision.canonical_fields if mode_revision else [revision.canonical_field],
                    "identity_fields": mode_revision.identity_fields if mode_revision else revision.identity_fields,
                    "source_policy": mode_revision.source_policy if mode_revision else revision.source_policy,
                    "knowledge_type": library.knowledge_type, "graph_mode": library.graph_mode,
                    "prompt": prompt_body,
                }
            return values

    def list_knowledge_jobs(self) -> list[dict[str, Any]]:
        with self.sessions() as session:
            return [self._job_payload_with_generation_summary(session, item) for item in session.scalars(select(KnowledgeJob).order_by(KnowledgeJob.created_at.desc()))]

    def job_generation_results(self, job_id: str, *, failed_only: bool = False) -> list[dict[str, Any]]:
        with self.sessions() as session:
            if not session.get(KnowledgeJob, job_id):
                raise ValueError("知识任务不存在")
            query = select(KnowledgeChunkGeneration).where(KnowledgeChunkGeneration.knowledge_job_id == job_id)
            if failed_only:
                query = query.where(KnowledgeChunkGeneration.status == "failed")
            query = query.order_by(KnowledgeChunkGeneration.knowledge_type, KnowledgeChunkGeneration.source_version_id, KnowledgeChunkGeneration.chunk_index)
            return [self._generation_payload(item) for item in session.scalars(query)]

    def retry_chunk_scope(self, job_id: str) -> set[tuple[str, str, str]] | None:
        """Return latest failed (type, version, chunk) tuples, if any."""
        with self.sessions() as session:
            if not session.get(KnowledgeJob, job_id):
                return None
            values = {
                (row.knowledge_type, row.source_version_id, row.source_chunk_id)
                for row in session.scalars(select(KnowledgeChunkGeneration).where(
                    KnowledgeChunkGeneration.knowledge_job_id == job_id,
                    KnowledgeChunkGeneration.status == "failed",
                ))
            }
            return values or None

    def record_chunk_generation(self, job_id: str, knowledge_type: str, chunk: dict[str, Any], *, status: str,
                                candidate_count: int = 0, error: str | None = None) -> dict[str, Any]:
        if status not in {"completed", "failed"}:
            raise ValueError("分块生成状态必须为 completed 或 failed")
        with self.sessions.begin() as session:
            row = session.scalar(select(KnowledgeChunkGeneration).where(
                KnowledgeChunkGeneration.knowledge_job_id == job_id,
                KnowledgeChunkGeneration.knowledge_type == knowledge_type,
                KnowledgeChunkGeneration.source_version_id == str(chunk["source_version_id"]),
                KnowledgeChunkGeneration.source_chunk_id == str(chunk["source_chunk_id"]),
            ))
            if row:
                row.status, row.candidate_count, row.error = status, candidate_count, error
                row.attempt_count += 1
            else:
                row = KnowledgeChunkGeneration(
                    id=new_id("kcg"), knowledge_job_id=job_id, knowledge_type=knowledge_type,
                    source_version_id=str(chunk["source_version_id"]), source_chunk_id=str(chunk["source_chunk_id"]),
                    chunk_index=int(chunk["chunk_index"]), status=status, candidate_count=candidate_count, error=error,
                )
                session.add(row)
            self.audit(session, "knowledge_chunk_generation.recorded", "knowledge_job", job_id, {
                "knowledge_type": knowledge_type, "source_version_id": row.source_version_id,
                "source_chunk_id": row.source_chunk_id, "chunk_index": row.chunk_index,
                "status": status, "candidate_count": candidate_count, "error": error,
            })
            session.flush()
            return self._generation_payload(row)

    def completed_source_versions_for_cleanup(self, job_id: str, knowledge_types: set[str],
                                               current_chunks: list[dict[str, Any]]) -> set[str]:
        """Return versions whose every current chunk succeeded for every type.

        This reads persisted results instead of a single Runner pass, which is
        essential when a retry executes only the previously failed chunks.
        """
        required: dict[str, set[str]] = {}
        for chunk in current_chunks:
            required.setdefault(str(chunk["source_version_id"]), set()).add(str(chunk["source_chunk_id"]))
        if not required or not knowledge_types:
            return set(required)
        with self.sessions() as session:
            job = session.get(KnowledgeJob, job_id)
            if not job:
                raise ValueError("知识任务不存在")
            rows = session.scalars(select(KnowledgeChunkGeneration).where(
                KnowledgeChunkGeneration.knowledge_job_id == job_id,
                KnowledgeChunkGeneration.knowledge_type.in_(knowledge_types),
                KnowledgeChunkGeneration.source_version_id.in_(required),
            )).all()
            latest: dict[tuple[str, str, str], KnowledgeChunkGeneration] = {}
            for row in rows:
                key = (row.knowledge_type, row.source_version_id, row.source_chunk_id)
                if key not in latest or row.updated_at > latest[key].updated_at:
                    latest[key] = row
            completed = {key for key, row in latest.items() if row.status == "completed"}
        return {
            version_id for version_id, chunk_ids in required.items()
            if all((knowledge_type, version_id, chunk_id) in completed
                   for knowledge_type in knowledge_types for chunk_id in chunk_ids)
        }

    def claim_job(self, owner: str) -> KnowledgeJob | None:
        with self.sessions.begin() as session:
            job = session.scalar(select(KnowledgeJob).where((KnowledgeJob.status == "queued") | ((KnowledgeJob.status == "running") & (KnowledgeJob.lease_expires_at < utc_now()))).order_by(KnowledgeJob.created_at).with_for_update(skip_locked=True).limit(1))
            if not job:
                return None
            job.status, job.stage, job.lease_owner, job.lease_expires_at = "running", "processing", owner, utc_now() + timedelta(minutes=5)
            job.attempt_count += 1
            self.audit(session, "knowledge_job.claimed", "knowledge_job", job.id, {"owner": owner, "attempt": job.attempt_count})
            return job

    def get_job(self, job_id: str) -> KnowledgeJob:
        with self.sessions() as session:
            job = session.get(KnowledgeJob, job_id)
            if not job:
                raise ValueError("知识任务不存在")
            return job

    def is_job_cancelled(self, job_id: str) -> bool:
        with self.sessions() as session:
            job = session.get(KnowledgeJob, job_id)
            return bool(job and job.status == "cancelled")

    def manage_jobs(self, job_ids: list[str], action: str) -> list[dict[str, Any]]:
        """Stop, retry, or safely remove task records without removing knowledge."""
        if action not in {"cancel", "retry", "delete"}:
            raise ValueError("仅支持 cancel、retry 或 delete 任务操作")
        identifiers = list(dict.fromkeys(item for item in job_ids if item))
        if not identifiers:
            raise ValueError("至少选择一个任务")
        with self.sessions.begin() as session:
            jobs = [session.get(KnowledgeJob, job_id) for job_id in identifiers]
            if any(job is None for job in jobs):
                raise ValueError("任务不存在")
            values = [job for job in jobs if job is not None]
            if action == "cancel":
                invalid = [job.id for job in values if job.status not in {"queued", "running"}]
                if invalid:
                    raise ValueError("仅可停止 queued 或 running 任务")
                for job in values:
                    job.status, job.stage, job.error, job.lease_owner, job.lease_expires_at = "cancelled", "cancelled", "管理员已停止", None, None
                    self.audit(session, "knowledge_job.cancelled", "knowledge_job", job.id)
                return [self.job_payload(job) for job in values]
            if action == "retry":
                invalid = [job.id for job in values if job.status not in {"failed", "cancelled", "completed_with_warnings"}]
                if invalid:
                    raise ValueError("仅可重试 failed、cancelled 或 completed_with_warnings 任务")
                for job in values:
                    job.status, job.stage, job.error, job.lease_owner, job.lease_expires_at = "queued", "queued", None, None, None
                    scope = [self._generation_payload(row) for row in session.scalars(select(KnowledgeChunkGeneration).where(
                        KnowledgeChunkGeneration.knowledge_job_id == job.id,
                        KnowledgeChunkGeneration.status == "failed",
                    ))]
                    self.audit(session, "knowledge_job.retry_queued", "knowledge_job", job.id, {"failed_chunks": scope})
                return [self._job_payload_with_generation_summary(session, job) for job in values]
            invalid = [job.id for job in values if job.status not in {"queued", "failed", "cancelled"}]
            if invalid:
                raise ValueError("只能删除未完成、失败或已停止的任务")
            has_knowledge = [job.id for job in values if session.scalar(select(func.count()).select_from(KnowledgeChange).where(KnowledgeChange.knowledge_job_id == job.id))]
            if has_knowledge:
                raise ValueError("任务已经形成正式知识，不能删除任务记录")
            payloads = [self.job_payload(job) for job in values]
            for job in values:
                self.audit(session, "knowledge_job.deleted", "knowledge_job", job.id)
                session.delete(job)
            return payloads

    def job_logs(self, job_id: str) -> list[dict[str, Any]]:
        with self.sessions() as session:
            if not session.get(KnowledgeJob, job_id):
                raise ValueError("知识任务不存在")
            query = select(AuditEvent).where(
                AuditEvent.resource_type == "knowledge_job", AuditEvent.resource_id == job_id,
            ).order_by(AuditEvent.created_at.desc())
            return [{"id": event.id, "action": event.action, "payload": event.payload_json, "created_at": event.created_at.isoformat()} for event in session.scalars(query)]

    def mark_job_failed(self, job_id: str, error: str) -> None:
        with self.sessions.begin() as session:
            job = session.get(KnowledgeJob, job_id)
            job.status, job.stage, job.error, job.lease_owner = "failed", "failed", error, None
            self.audit(session, "knowledge_job.failed", "knowledge_job", job_id, {"error": error})

    def complete_job(self, job_id: str, *, warnings: list[dict[str, Any]] | None = None) -> None:
        with self.sessions.begin() as session:
            job = session.get(KnowledgeJob, job_id)
            has_warnings = bool(warnings)
            job.status, job.stage, job.error, job.lease_owner, job.lease_expires_at = (
                "completed_with_warnings" if has_warnings else "completed",
                "completed_with_warnings" if has_warnings else "completed",
                None,
                None,
                None,
            )
            if not has_warnings and job.document_library_template_binding_id and job.knowledge_flow_template_revision_id:
                binding = session.get(DocumentLibraryTemplateBinding, job.document_library_template_binding_id)
                if binding:
                    for version_id in job.source_version_ids:
                        if not session.scalar(select(DocumentLibraryProcessingRecord.id).where(
                            DocumentLibraryProcessingRecord.document_library_template_binding_id == binding.id,
                            DocumentLibraryProcessingRecord.source_version_id == version_id,
                            DocumentLibraryProcessingRecord.knowledge_flow_template_revision_id == job.knowledge_flow_template_revision_id,
                        )):
                            session.add(DocumentLibraryProcessingRecord(
                                id=new_id("docproc"), document_library_template_binding_id=binding.id,
                                source_version_id=version_id, knowledge_flow_template_revision_id=job.knowledge_flow_template_revision_id,
                                knowledge_job_id=job.id,
                            ))
                    binding.last_successful_revision_id = job.knowledge_flow_template_revision_id
            self.audit(session, "knowledge_job.completed", "knowledge_job", job_id, {"warnings": warnings or []})

    def start_flow_run(self, job_id: str) -> dict[str, Any]:
        with self.sessions.begin() as session:
            job = session.get(KnowledgeJob, job_id)
            if not job or not job.execution_snapshot_id:
                raise ValueError("知识任务缺少执行快照")
            run = FlowRun(id=new_id("flowrun"), knowledge_job_id=job.id, execution_snapshot_id=job.execution_snapshot_id)
            session.add(run)
            self.audit(session, "flow_run.started", "flow_run", run.id, {"knowledge_job_id": job.id})
            return {"id": run.id, "execution_snapshot_id": run.execution_snapshot_id, "status": run.status}

    def record_flow_node(self, flow_run_id: str, node_id: str, input_artifact_ids: list[str], outputs: list[dict[str, Any]], *, error: str | None = None) -> list[str]:
        """Persist execution-only artifacts and their lineage; never use them as formal provenance."""
        with self.sessions.begin() as session:
            node_run = FlowNodeRun(id=new_id("noderun"), flow_run_id=flow_run_id, node_id=node_id,
                                   status="failed" if error else "completed", input_artifact_ids=list(input_artifact_ids), error=error)
            session.add(node_run); session.flush()
            output_ids: list[str] = []
            for value in outputs:
                data = dict(value) if isinstance(value, dict) else {"value": value}
                parser_artifacts = list(data.pop("_parser_artifacts", []))
                artifact = Artifact(id=new_id("artifact"), flow_run_id=flow_run_id, flow_node_run_id=node_run.id,
                                    type_code=str(data.pop("_artifact_type", "execution")), data_json=data)
                session.add(artifact); session.flush(); output_ids.append(artifact.id)
                for parent_id in input_artifact_ids:
                    session.add(ArtifactLineage(id=new_id("lineage"), parent_artifact_id=parent_id, child_artifact_id=artifact.id))
                for parser_artifact in parser_artifacts:
                    parser_data = dict(parser_artifact.get("data") or {})
                    persisted = Artifact(
                        id=new_id("artifact"), flow_run_id=flow_run_id, flow_node_run_id=node_run.id,
                        source_version_id=str(parser_artifact["source_version_id"]),
                        type_code=str(parser_artifact["type_code"]), uri=str(parser_artifact["uri"]),
                        checksum=str(parser_artifact["checksum"]), data_json=parser_data,
                    )
                    session.add(persisted); session.flush(); output_ids.append(persisted.id)
                    session.add(ArtifactLineage(id=new_id("lineage"), parent_artifact_id=artifact.id, child_artifact_id=persisted.id))
            node_run.output_artifact_ids = output_ids
            return output_ids

    def finish_flow_run(self, flow_run_id: str, error: str | None = None, *, status: str | None = None) -> None:
        with self.sessions.begin() as session:
            run = session.get(FlowRun, flow_run_id)
            if not run:
                raise ValueError("Flow Run 不存在")
            final_status = status or ("failed" if error else "completed")
            run.status, run.error, run.completed_at = final_status, error, utc_now()
            self.audit(session, "flow_run.finished", "flow_run", run.id, {"status": final_status, "error": error} if error else {"status": final_status})

    def flow_run_detail(self, flow_run_id: str) -> dict[str, Any]:
        with self.sessions() as session:
            run = session.get(FlowRun, flow_run_id)
            if not run:
                raise ValueError("Flow Run 不存在")
            nodes = session.scalars(select(FlowNodeRun).where(FlowNodeRun.flow_run_id == run.id).order_by(FlowNodeRun.created_at)).all()
            artifacts = session.scalars(select(Artifact).where(Artifact.flow_run_id == run.id).order_by(Artifact.created_at)).all()
            return {"id": run.id, "knowledge_job_id": run.knowledge_job_id, "execution_snapshot_id": run.execution_snapshot_id,
                    "status": run.status, "error": run.error, "nodes": [
                        {"id": node.id, "node_id": node.node_id, "status": node.status, "input_artifact_ids": node.input_artifact_ids,
                         "output_artifact_ids": node.output_artifact_ids, "error": node.error} for node in nodes],
                    "artifacts": [{"id": item.id, "node_run_id": item.flow_node_run_id, "type": item.type_code,
                                   "data": item.data_json, "uri": item.uri} for item in artifacts]}

    def v7_rebuild_manifest(self) -> dict[str, Any]:
        """Return the exact V7-owned physical resources eligible for a rebuild.

        The manifest is DB-derived: no collection, object prefix, or legacy
        resource is discovered from infrastructure and then deleted by guess.
        """
        with self.sessions() as session:
            keys = list(session.scalars(select(SourceVersion.object_key)))
            keys.extend(
                str(item.data_json.get("object_key")) for item in session.scalars(select(Artifact).where(Artifact.type_code.like("parser.%")))
                if item.data_json.get("object_key")
            )
            partitions = list(session.scalars(select(KnowledgeLibrary.partition_name)))
            # The Collection is administrator-owned.  Rebuild/deletion may only
            # operate on partitions that are referenced from V7 libraries.
            collections = sorted({profile.collection_name for profile in session.scalars(select(KnowledgeIndexProfile))})
            bindings = []
            for library in session.scalars(select(KnowledgeLibrary)):
                for profile in self._index_profile_snapshots_for_library(session, library):
                    bindings.append({"collection_name": profile.collection_name, "partition_name": library.partition_name})
            return {"object_keys": sorted(set(keys)), "partition_names": sorted(set(partitions)), "collections": collections,
                    "partition_bindings": sorted(bindings, key=lambda item: (item["collection_name"], item["partition_name"]))}

    def rebuild_v7_database_state(self) -> dict[str, int]:
        """Delete V7 table rows only; schema and external resources are retained."""
        # Order is intentional for MySQL foreign keys.  Never issue DDL here.
        tables = (
            ArtifactLineage, Artifact, FlowNodeRun, SourceChunk, DocumentIR, FlowRun, FlowExecutionSnapshot, KnowledgeChunkGeneration,
            VectorDeletionJob, VectorRecordState, VectorSyncJob, KnowledgeChange, KnowledgeItemSource, KnowledgeItem,
            ProjectOrgRouteLibrary, ProjectOrgRoute, ProjectRouteVersion, ProjectTask, Project,
            DocumentLibraryProcessingRecord, KnowledgeJob, KnowledgeLibraryDeletionJob, DocumentDeletionJob,
            DocumentLibraryTemplateOutput, KnowledgeLibrary, DocumentLibraryTemplateBinding, DocumentLibraryMember, SourceVersion, Source,
            KnowledgeFlowTemplateRevision, KnowledgeFlowTemplate,
            FlowSubgraphRevision, FlowSubgraph, OperatorVersion, OperatorDefinition,
            PromptTemplateRevision, PromptTemplate, QualityProfileRevision, QualityProfile,
            KnowledgeTypeIndexBinding, KnowledgeTypeRevision, KnowledgeType,
            KnowledgeIndexProfileRevision, KnowledgeIndexProfile, EmbeddingProfile, AdminSession, AuditEvent,
        )
        counts: dict[str, int] = {}
        with self.sessions.begin() as session:
            for model in tables:
                result = session.execute(delete(model))
                counts[model.__tablename__] = int(result.rowcount or 0)
        return counts

    @staticmethod
    def _validate_candidate_contract(candidate: dict[str, Any], revision: KnowledgeTypeRevision,
                                     mode_revision: KnowledgeTypeModeRevision | None = None) -> None:
        data = dict(candidate.get("data_json") or {})
        schema = mode_revision.schema_json if mode_revision else revision.schema_json
        try:
            from jsonschema import Draft202012Validator
            errors = sorted(Draft202012Validator(schema or {"type": "object"}).iter_errors(data), key=lambda item: list(item.path))
            if errors:
                raise ValueError("候选项不符合知识 Schema：" + errors[0].message)
        except ImportError:
            # API/Worker intentionally omit the Runner-only validator.  Keep a
            # conservative required-field gate for direct administrative calls.
            pass
        required = list((schema or {}).get("required") or [])
        missing = [field for field in required if not data.get(field)]
        if missing:
            raise ValueError("候选项不符合知识 Schema，缺少：" + "、".join(missing))
        canonical = candidate.get("canonical_content") or data.get(revision.canonical_field)
        if not str(canonical or "").strip():
            raise ValueError("候选项缺少 canonical 内容")
        source_ids = list(candidate.get("source_version_ids") or [])
        if not source_ids:
            raise ValueError("Knowledge Sink 拒绝缺少来源的候选项")
        if revision.source_policy == "single" and len(set(source_ids)) != 1:
            raise ValueError("该知识类型只允许一个当前来源")
        if candidate.get("quality_status") in {"review", "reject", "failed"}:
            raise ValueError("质量 Gate 未通过，禁止写入 Knowledge Sink")

    def apply_knowledge_output(self, job_id: str, output_key: str, candidates: list[dict[str, Any]],
                               *, successful_chunks: list[dict[str, Any]] | None = None,
                               replace_absent_chunks: bool = False,
                               replace_absent_source_versions: set[str] | None = None,
                               cleanup_only: bool = False) -> dict[str, int]:
        """Apply candidates only for completed formal chunks.

        ``successful_chunks`` is the authoritative replacement range.  A
        completed empty chunk therefore withdraws its previous results, while
        a failed chunk is absent from the range and retains its old knowledge.
        """
        with self.sessions.begin() as session:
            job = session.get(KnowledgeJob, job_id)
            if not job:
                raise ValueError("知识任务不存在")
            output_key = normalise_output_key(output_key)
            knowledge_type, graph_mode = output_contract(output_key)
            library_id = (job.sink_library_ids or job.output_library_ids or {}).get(output_key)
            library = session.get(KnowledgeLibrary, library_id) if library_id else None
            if not library or library.knowledge_type != knowledge_type or graph_mode and library.graph_mode != graph_mode:
                raise ValueError("任务没有为该产出绑定有效知识库")
            revision = session.get(KnowledgeTypeRevision, library.knowledge_type_revision_id) if library.knowledge_type_revision_id else None
            if not revision or revision.status != "published":
                raise ValueError("目标知识库没有已发布知识类型契约")
            mode_revision = None
            if graph_mode:
                mode_revision = session.scalar(select(KnowledgeTypeModeRevision).where(
                    KnowledgeTypeModeRevision.knowledge_type_revision_id == revision.id,
                    KnowledgeTypeModeRevision.mode == graph_mode,
                    KnowledgeTypeModeRevision.status == "published",
                ).order_by(KnowledgeTypeModeRevision.revision_no.desc()))
                if not mode_revision:
                    raise ValueError("目标图谱知识库没有已发布模式契约")
            source_versions = {v.id: v for v in session.scalars(select(SourceVersion).where(SourceVersion.id.in_(job.source_version_ids)))}
            if not source_versions:
                raise ValueError("任务来源版本不存在")
            current = {item.source_knowledge_id: item for item in session.scalars(select(KnowledgeItem).where(KnowledgeItem.knowledge_library_id == library.id))}
            if successful_chunks is None:
                # Compatibility for existing direct-store callers: their
                # candidates may deliberately carry multiple evidence sources.
                successful = [
                    {
                        "source_version_id": version_id,
                        "source_chunk_id": str(candidate.get("source_chunk_id") or ""),
                        "chunk_index": int(dict(candidate.get("anchor_json") or {}).get("chunk_index", 0)),
                    }
                    for candidate in candidates
                    for version_id in (candidate.get("source_version_ids") or job.source_version_ids)
                ]
            else:
                successful = successful_chunks
            chunk_scope = {(str(value["source_version_id"]), str(value["source_chunk_id"])) for value in successful}
            # Older callers did not provide an explicit completed-chunk range.
            # Preserve their whole-output replacement contract for an empty
            # result while runner jobs always pass ``successful_chunks``.
            if successful_chunks is None and not chunk_scope:
                chunk_scope = set(session.execute(select(
                    KnowledgeItemSource.source_version_id,
                    KnowledgeItemSource.source_chunk_id,
                ).join(KnowledgeItem, KnowledgeItem.id == KnowledgeItemSource.knowledge_item_id).where(
                    KnowledgeItem.knowledge_library_id == library.id,
                )).all())
            incoming_keys_by_chunk: dict[tuple[str, str], set[str]] = {value: set() for value in chunk_scope}
            counts = {"ADD": 0, "UPDATE": 0, "INACTIVE": 0, "UNCHANGED": 0}
            for candidate in candidates:
                self._validate_candidate_contract(candidate, revision, mode_revision)
                key = str(candidate["source_knowledge_id"])
                anchor = dict(candidate.get("anchor_json") or {})
                source_chunk_id = str(candidate.get("source_chunk_id") or anchor.get("source_chunk_id") or "")
                version_ids = list(candidate.get("source_version_ids") or job.source_version_ids)
                if successful_chunks is not None and len(version_ids) != 1:
                    raise ValueError("候选项必须绑定一个来源版本")
                for version_id in version_ids:
                    scope_key = (str(version_id), source_chunk_id)
                    if scope_key not in chunk_scope:
                        raise ValueError("候选项不属于成功分块范围")
                    incoming_keys_by_chunk[scope_key].add(key)
                content = str(candidate["canonical_content"])
                data = dict(candidate.get("data_json") or {})
                digest = content_hash(content, data)
                item = current.get(key)
                before_snapshot = None
                if not item:
                    item = KnowledgeItem(id=new_id("ki"), knowledge_library_id=library.id, knowledge_type_revision_id=revision.id, source_knowledge_id=key, canonical_content=content, data_json=data, content_hash=digest, status="active")
                    session.add(item); change = "ADD"; before = None
                elif item.content_hash != digest or item.status != "active":
                    before_snapshot = {"content": item.canonical_content, "data": item.data_json, "status": item.status}
                    before, item.canonical_content, item.data_json, item.content_hash, item.status = item.content_hash, content, data, digest, "active"; item.knowledge_type_revision_id = revision.id; change = "UPDATE"
                else:
                    before, change = item.content_hash, "UNCHANGED"
                for version_id in version_ids:
                    if version_id not in source_versions:
                        raise ValueError("候选项包含任务外来源版本")
                    exists = session.scalar(select(KnowledgeItemSource).where(
                        KnowledgeItemSource.knowledge_item_id == item.id,
                        KnowledgeItemSource.source_version_id == version_id,
                        KnowledgeItemSource.source_chunk_id == source_chunk_id,
                    ))
                    if not exists:
                        session.add(KnowledgeItemSource(id=new_id("kis"), knowledge_item_id=item.id, source_version_id=version_id,
                            source_chunk_id=source_chunk_id, source_anchor=str(candidate.get("source_anchor", anchor.get("label", ""))), anchor_json=anchor,
                            evidence_text=str(candidate.get("evidence_text", content)), is_primary=bool(candidate.get("is_primary", False))))
                if revision.source_policy == "single":
                    chosen = version_ids[0]
                    # The same logical source may have a newer SourceVersion.
                    # A successful current chunk supersedes only the matching
                    # old chunk index; other old chunks remain until every
                    # current chunk succeeds and the absence sweep is allowed.
                    current_source_id = source_versions[chosen].source_id
                    # A single-source contract may not retain evidence from a
                    # different logical source, but it must retain other chunks
                    # from earlier versions of this *same* source.  Removing
                    # every non-current version here would erase failed chunks.
                    unrelated_links = session.execute(select(KnowledgeItemSource, SourceVersion).join(
                        SourceVersion, SourceVersion.id == KnowledgeItemSource.source_version_id,
                    ).where(
                        KnowledgeItemSource.knowledge_item_id == item.id,
                        SourceVersion.source_id != current_source_id,
                    )).all()
                    for unrelated_link, _ in unrelated_links:
                        session.delete(unrelated_link)
                    current_chunk_index = int(anchor.get("chunk_index", -1))
                    stale_links = session.execute(select(KnowledgeItemSource, SourceVersion).join(
                        SourceVersion, SourceVersion.id == KnowledgeItemSource.source_version_id,
                    ).where(
                        KnowledgeItemSource.knowledge_item_id == item.id,
                        SourceVersion.source_id == current_source_id,
                        SourceVersion.id != chosen,
                    )).all()
                    for stale_link, _ in stale_links:
                        if int((stale_link.anchor_json or {}).get("chunk_index", -2)) == current_chunk_index:
                            session.delete(stale_link)
                session.add(KnowledgeChange(id=new_id("kc"), knowledge_job_id=job.id, knowledge_library_id=library.id, knowledge_item_id=item.id, change_type=change, before_hash=before, after_hash=digest, details_json={"source_knowledge_id": key}, before_snapshot_json=before_snapshot, after_snapshot_json={"content": content, "data": data, "status": "active"}))
                counts[change] += 1
            all_links = session.execute(select(KnowledgeItemSource, KnowledgeItem).join(
                KnowledgeItem, KnowledgeItem.id == KnowledgeItemSource.knowledge_item_id,
            ).where(KnowledgeItem.knowledge_library_id == library.id)).all()
            # A successful new SourceVersion chunk replaces the full logical
            # source/chunk range, not only candidates whose identity happened
            # to be unchanged.  This also makes a valid empty result withdraw
            # old evidence from the corresponding older version.
            processed_sources = {
                (source_versions[str(chunk["source_version_id"])].source_id, int(chunk["chunk_index"])):
                str(chunk["source_version_id"])
                for chunk in successful
                if str(chunk["source_version_id"]) in source_versions
            }
            linked_version_ids = {link.source_version_id for link, _ in all_links}
            linked_sources = {row.id: row.source_id for row in session.scalars(select(SourceVersion).where(SourceVersion.id.in_(linked_version_ids)))}
            for link, item in all_links:
                replacement_version = processed_sources.get((
                    linked_sources.get(link.source_version_id),
                    int((link.anchor_json or {}).get("chunk_index", -1)),
                ))
                if not replacement_version or link.source_version_id == replacement_version:
                    continue
                session.delete(link)
                remaining = list(session.scalars(select(KnowledgeItemSource).where(
                    KnowledgeItemSource.knowledge_item_id == item.id,
                    KnowledgeItemSource.id != link.id,
                )))
                if (revision.source_policy == "single" or not remaining) and item.status == "active":
                    item.status = "inactive"
                    session.add(KnowledgeChange(id=new_id("kc"), knowledge_job_id=job.id, knowledge_library_id=library.id,
                        knowledge_item_id=item.id, change_type="INACTIVE", before_hash=item.content_hash,
                        details_json={"source_knowledge_id": item.source_knowledge_id, "source_chunk_id": link.source_chunk_id},
                        before_snapshot_json={"content": item.canonical_content, "data": item.data_json, "status": "active"},
                        after_snapshot_json={"content": item.canonical_content, "data": item.data_json, "status": "inactive"}))
                    counts["INACTIVE"] += 1
                    self._queue_vector_deletions_for_item(session, item)
            all_links = session.execute(select(KnowledgeItemSource, KnowledgeItem).join(
                KnowledgeItem, KnowledgeItem.id == KnowledgeItemSource.knowledge_item_id,
            ).where(KnowledgeItem.knowledge_library_id == library.id)).all()
            if not cleanup_only:
                for link, item in all_links:
                    scope_key = (link.source_version_id, link.source_chunk_id)
                    if scope_key not in chunk_scope or item.source_knowledge_id in incoming_keys_by_chunk[scope_key]:
                        continue
                    # This completed chunk produced no corresponding candidate.
                    # Remove only this evidence; a multi-source item stays current
                    # if another source chunk still supports it.
                    session.delete(link)
                    remaining = list(session.scalars(select(KnowledgeItemSource).where(
                        KnowledgeItemSource.knowledge_item_id == item.id,
                        KnowledgeItemSource.id != link.id,
                    )))
                    if (revision.source_policy == "single" or not remaining) and item.status == "active":
                        item.status = "inactive"
                        session.add(KnowledgeChange(id=new_id("kc"), knowledge_job_id=job.id, knowledge_library_id=library.id,
                            knowledge_item_id=item.id, change_type="INACTIVE", before_hash=item.content_hash,
                            details_json={"source_knowledge_id": item.source_knowledge_id, "source_chunk_id": link.source_chunk_id},
                            before_snapshot_json={"content": item.canonical_content, "data": item.data_json, "status": "active"},
                            after_snapshot_json={"content": item.canonical_content, "data": item.data_json, "status": "inactive"}))
                        counts["INACTIVE"] += 1
                        self._queue_vector_deletions_for_item(session, item)
            if replace_absent_chunks or replace_absent_source_versions is not None:
                replacement_versions = (
                    {str(value) for value in replace_absent_source_versions}
                    if replace_absent_source_versions is not None
                    else set(source_versions)
                )
                active_by_source: dict[str, set[int]] = {}
                current_versions = {row.id: row.source_id for row in session.scalars(select(SourceVersion).where(SourceVersion.id.in_(source_versions)))}
                for chunk in successful:
                    if str(chunk["source_version_id"]) not in replacement_versions:
                        continue
                    source_id = current_versions.get(str(chunk["source_version_id"]))
                    if source_id:
                        active_by_source.setdefault(source_id, set()).add(int(chunk["chunk_index"]))
                linked_version_ids = {link.source_version_id for link, _ in all_links}
                linked_sources = {row.id: row.source_id for row in session.scalars(select(SourceVersion).where(SourceVersion.id.in_(linked_version_ids)))}
                for link, item in all_links:
                    source_id = linked_sources.get(link.source_version_id)
                    chunk_index = int((link.anchor_json or {}).get("chunk_index", -1))
                    if not source_id or source_id not in active_by_source or chunk_index in active_by_source[source_id]:
                        continue
                    session.delete(link)
                    remaining = list(session.scalars(select(KnowledgeItemSource).where(
                        KnowledgeItemSource.knowledge_item_id == item.id,
                        KnowledgeItemSource.id != link.id,
                    )))
                    if (revision.source_policy == "single" or not remaining) and item.status == "active":
                        item.status = "inactive"
                        self._queue_vector_deletions_for_item(session, item)
            self.audit(session, "knowledge.current_state_applied", "knowledge_library", library.id, counts)
            return counts

    def list_knowledge_items(self, library_id: str, kind: str | None = None) -> list[dict[str, Any]]:
        with self.sessions() as session:
            library = session.get(KnowledgeLibrary, library_id)
            if not library:
                raise ValueError("知识库不存在")
            if kind and library.knowledge_type != kind:
                raise ValueError("知识库类型不匹配")
            values = session.scalars(select(KnowledgeItem).where(KnowledgeItem.knowledge_library_id == library_id).order_by(KnowledgeItem.updated_at.desc())).all()
            result = []
            for item in values:
                sources = session.scalars(select(KnowledgeItemSource).where(KnowledgeItemSource.knowledge_item_id == item.id)).all()
                result.append({"id": item.id, "source_knowledge_id": item.source_knowledge_id, "canonical_content": item.canonical_content, "data": item.data_json, "content_hash": item.content_hash, "status": item.status, "source_version_ids": [source.source_version_id for source in sources], "source_count": len(sources), "updated_at": item.updated_at.isoformat()})
            return result

    def list_changes(self, library_id: str | None = None) -> list[dict[str, Any]]:
        with self.sessions() as session:
            query = select(KnowledgeChange).order_by(KnowledgeChange.created_at.desc())
            if library_id:
                query = query.where(KnowledgeChange.knowledge_library_id == library_id)
            return [{"id": item.id, "knowledge_job_id": item.knowledge_job_id, "knowledge_library_id": item.knowledge_library_id, "knowledge_item_id": item.knowledge_item_id, "change_type": item.change_type, "before_hash": item.before_hash, "after_hash": item.after_hash, "details": item.details_json, "before": item.before_snapshot_json, "after": item.after_snapshot_json, "created_at": item.created_at.isoformat()} for item in session.scalars(query)]

    def knowledge_item_sources(self, item_id: str) -> list[dict[str, Any]]:
        with self.sessions() as session:
            item = session.get(KnowledgeItem, item_id)
            if not item:
                raise ValueError("知识项不存在")
            rows = session.execute(
                select(KnowledgeItemSource, SourceVersion, Source).join(SourceVersion, SourceVersion.id == KnowledgeItemSource.source_version_id)
                .join(Source, Source.id == SourceVersion.source_id)
                .where(KnowledgeItemSource.knowledge_item_id == item_id).order_by(KnowledgeItemSource.created_at)
            ).all()
            return [{"id": link.id, "is_primary": link.is_primary, "evidence_text": link.evidence_text,
                     "anchor": link.anchor_json or {"label": link.source_anchor},
                     "source": {"id": source.id, "name": source.name, "original_filename": source.original_filename},
                     "source_version": {"id": version.id, "version_no": version.version_no, "sha256": version.sha256}}
                    for link, version, source in rows]

    @staticmethod
    def _graph_identity(library_id: str, *parts: str) -> str:
        return hashlib.sha256("|".join((library_id, *parts)).encode("utf-8")).hexdigest()[:32]

    def _graph_projection(self, session: Session, library_id: str) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
        library = session.get(KnowledgeLibrary, library_id)
        if not library or library.knowledge_type != "graph":
            raise ValueError("图谱知识库不存在或类型不匹配")
        nodes: dict[str, dict[str, Any]] = {}
        edges: dict[str, dict[str, Any]] = {}
        for item in session.scalars(select(KnowledgeItem).where(KnowledgeItem.knowledge_library_id == library_id, KnowledgeItem.status == "active")):
            data = item.data_json or {}
            if library.graph_mode == "semantic":
                source, target, relation = (data.get(key) or {} for key in ("source_entity", "target_entity", "relation"))
                subject, obj = str(source.get("name", "")).strip(), str(target.get("name", "")).strip()
                predicate = str(relation.get("description", "")).strip()
            else:
                source, target, relation = {}, {}, {}
                subject, predicate, obj = (str(data.get(key, "")).strip() for key in ("subject", "predicate", "object"))
            if not subject or not predicate or not obj:
                continue
            source_id = self._graph_identity(library_id, "entity", subject.casefold())
            target_id = self._graph_identity(library_id, "entity", obj.casefold())
            relation_id = self._graph_identity(library_id, "relation", subject.casefold(), predicate.casefold(), obj.casefold())
            nodes.setdefault(source_id, {"id": source_id, "name": subject, "type": source.get("type") or data.get("subject_type") or "未分类", "description": source.get("description")})
            nodes.setdefault(target_id, {"id": target_id, "name": obj, "type": target.get("type") or data.get("object_type") or "未分类", "description": target.get("description")})
            edge = edges.setdefault(relation_id, {"id": relation_id, "source": source_id, "target": target_id,
                "predicate": predicate, "description": predicate if library.graph_mode == "semantic" else None,
                "keywords": relation.get("keywords") or [], "weight": relation.get("weight"),
                "graph_mode": library.graph_mode or "triple", "knowledge_item_ids": []})
            edge["knowledge_item_ids"].append(item.id)
        return nodes, edges

    def graph_entity_search(self, library_id: str, query: str, limit: int = 20) -> list[dict[str, Any]]:
        with self.sessions() as session:
            nodes, _ = self._graph_projection(session, library_id)
            needle = query.casefold().strip()
            values = [node for node in nodes.values() if not needle or needle in node["name"].casefold()]
            return sorted(values, key=lambda item: item["name"])[:max(1, min(limit, 100))]

    def graph_neighbors(self, library_id: str, entity_id: str, depth: int = 1) -> dict[str, Any]:
        if depth not in {1, 2}:
            raise ValueError("图谱邻居深度只支持 1 或 2")
        with self.sessions() as session:
            nodes, edges = self._graph_projection(session, library_id)
            if entity_id not in nodes:
                raise ValueError("图谱实体不存在")
            selected, frontier = {entity_id}, {entity_id}
            selected_edges: set[str] = set()
            for _ in range(depth):
                next_frontier: set[str] = set()
                for edge_id, edge in edges.items():
                    if edge["source"] in frontier or edge["target"] in frontier:
                        selected_edges.add(edge_id); next_frontier.update((edge["source"], edge["target"]))
                frontier = next_frontier - selected
                selected.update(next_frontier)
                if len(selected) >= 100:
                    break
            return {"center_id": entity_id, "depth": depth, "nodes": [nodes[node_id] for node_id in sorted(selected)[:100]],
                    "edges": [edges[edge_id] for edge_id in sorted(selected_edges)[:200]]}

    def graph_entity_detail(self, library_id: str, entity_id: str) -> dict[str, Any]:
        with self.sessions() as session:
            nodes, edges = self._graph_projection(session, library_id)
            if entity_id not in nodes:
                raise ValueError("图谱实体不存在")
            related = [edge for edge in edges.values() if entity_id in (edge["source"], edge["target"])]
            return {**nodes[entity_id], "relation_count": len(related), "relation_ids": [edge["id"] for edge in related]}

    def graph_relation_evidence(self, library_id: str, relation_id: str) -> dict[str, Any]:
        with self.sessions() as session:
            _, edges = self._graph_projection(session, library_id)
            relation = edges.get(relation_id)
            if not relation:
                raise ValueError("图谱关系不存在")
            evidence = []
            for item_id in relation["knowledge_item_ids"]:
                evidence.extend(self.knowledge_item_sources(item_id))
            return {"relation": relation, "evidence": evidence}

    def index_profiles_for_library(self, library_id: str) -> tuple[KnowledgeLibrary, list[KnowledgeIndexProfile]]:
        with self.sessions() as session:
            library = session.get(KnowledgeLibrary, library_id)
            if not library:
                raise ValueError("知识库不存在")
            profiles = self._index_profile_snapshots_for_library(session, library)
            return library, profiles

    def create_vector_sync_jobs(self, library_id: str) -> list[dict[str, Any]]:
        with self.sessions.begin() as session:
            library = session.get(KnowledgeLibrary, library_id)
            if not library:
                raise ValueError("知识库不存在")
            profiles = self._index_profile_snapshots_for_library(session, library)
            count = session.scalar(select(func.count()).select_from(KnowledgeItem).where(KnowledgeItem.knowledge_library_id == library.id, KnowledgeItem.status == "active")) or 0
            jobs = [VectorSyncJob(id=new_id("vsj"), knowledge_library_id=library.id, index_profile_id=profile.id, total_count=count) for profile in profiles]
            session.add_all(jobs); self.audit(session, "vector_sync.queued", "knowledge_library", library.id, {"jobs": [item.id for item in jobs]})
            return [{"id": item.id, "knowledge_library_id": item.knowledge_library_id, "index_profile_id": item.index_profile_id, "status": item.status, "total_count": item.total_count} for item in jobs]

    def vector_sync_context(self, job_id: str) -> dict[str, Any]:
        with self.sessions() as session:
            job = session.get(VectorSyncJob, job_id)
            if not job:
                raise ValueError("向量同步任务不存在")
            library = session.get(KnowledgeLibrary, job.knowledge_library_id)
            if not library:
                raise ValueError("向量同步任务引用的知识库不存在")
            profile = next((item for item in self._index_profile_snapshots_for_library(session, library) if item.id == job.index_profile_id), None)
            if not profile:
                raise ValueError("向量同步任务引用的已发布 Index Profile 不存在")
            embedding = session.get(EmbeddingProfile, profile.embedding_profile_id)
            storage_contract = session.get(StorageContractRevision, profile.storage_contract_revision_id) if profile.storage_contract_revision_id else None
            items = session.scalars(select(KnowledgeItem).where(KnowledgeItem.knowledge_library_id == library.id, KnowledgeItem.status == "active")).all()
            return {"job": job, "library": library, "profile": profile, "embedding": embedding,
                    "storage_contract": storage_contract, "items": items}

    def finish_vector_sync(self, job_id: str, vector_rows: Iterable[dict[str, Any]], error: str | None = None) -> dict[str, Any]:
        with self.sessions.begin() as session:
            job = session.get(VectorSyncJob, job_id)
            if not job:
                raise ValueError("向量同步任务不存在")
            if error:
                job.status, job.error = "failed", error
                return {"id": job.id, "status": job.status, "error": error}
            count = 0
            for row in vector_rows:
                state = session.scalar(select(VectorRecordState).where(VectorRecordState.knowledge_item_id == row["knowledge_item_id"], VectorRecordState.index_profile_id == job.index_profile_id))
                if not state:
                    state = VectorRecordState(id=new_id("vrs"), knowledge_item_id=row["knowledge_item_id"], index_profile_id=job.index_profile_id, vector_id=row["vector_id"], content_hash=row["content_hash"])
                    session.add(state)
                else:
                    state.vector_id, state.content_hash, state.error = row["vector_id"], row["content_hash"], None
                state.status = "ready"; count += 1
            job.status, job.synced_count, job.error = "ready", count, None
            self.audit(session, "vector_sync.ready", "vector_sync_job", job.id, {"synced_count": count})
            return {"id": job.id, "status": job.status, "synced_count": count}

    def list_vector_sync_jobs(self, library_id: str | None = None) -> list[dict[str, Any]]:
        with self.sessions() as session:
            query = select(VectorSyncJob).order_by(VectorSyncJob.created_at.desc())
            if library_id:
                query = query.where(VectorSyncJob.knowledge_library_id == library_id)
            return [{"id": item.id, "knowledge_library_id": item.knowledge_library_id, "index_profile_id": item.index_profile_id, "status": item.status, "total_count": item.total_count, "synced_count": item.synced_count, "error": item.error, "created_at": item.created_at.isoformat()} for item in session.scalars(query)]

    def vector_status(self, library_id: str) -> dict[str, Any]:
        with self.sessions() as session:
            library = session.get(KnowledgeLibrary, library_id)
            if not library:
                raise ValueError("知识库不存在")
            jobs = self.list_vector_sync_jobs(library_id)
            profile_rows = session.execute(
                select(KnowledgeIndexProfile.code, VectorRecordState.status, func.count(VectorRecordState.id))
                .join(VectorRecordState, VectorRecordState.index_profile_id == KnowledgeIndexProfile.id)
                .join(KnowledgeItem, KnowledgeItem.id == VectorRecordState.knowledge_item_id)
                .where(KnowledgeItem.knowledge_library_id == library_id)
                .group_by(KnowledgeIndexProfile.code, VectorRecordState.status)
            ).all()
            states: dict[str, dict[str, int]] = {}
            for code, status, count in profile_rows:
                states.setdefault(code, {})[status] = int(count)
            return {"knowledge_library_id": library_id, "ready": self._library_ready(session, library), "jobs": jobs, "record_states": states}

    def claim_vector_sync_job(self, owner: str) -> VectorSyncJob | None:
        with self.sessions.begin() as session:
            job = session.scalar(select(VectorSyncJob).where(VectorSyncJob.status == "queued").order_by(VectorSyncJob.created_at).with_for_update(skip_locked=True).limit(1))
            if not job:
                return None
            job.status = "running"
            self.audit(session, "vector_sync.claimed", "vector_sync_job", job.id, {"owner": owner})
            return job

    def claim_vector_deletion_job(self, owner: str) -> VectorDeletionJob | None:
        with self.sessions.begin() as session:
            job = session.scalar(select(VectorDeletionJob).where(
                (VectorDeletionJob.status == "queued") | ((VectorDeletionJob.status == "running") & (VectorDeletionJob.lease_expires_at < utc_now()))
            ).order_by(VectorDeletionJob.created_at).with_for_update(skip_locked=True).limit(1))
            if not job:
                return None
            job.status, job.lease_owner, job.lease_expires_at = "running", owner, utc_now() + timedelta(minutes=5)
            job.attempt_count += 1
            return job

    def vector_deletion_context(self, job_id: str) -> dict[str, Any]:
        with self.sessions() as session:
            job = session.get(VectorDeletionJob, job_id)
            if not job:
                raise ValueError("向量删除任务不存在")
            library = session.get(KnowledgeLibrary, job.knowledge_library_id)
            profile = next((item for item in self._index_profile_snapshots_for_library(session, library) if item.id == job.index_profile_id), None) if library else None
            if not library or not profile:
                raise ValueError("向量删除任务引用的知识库或 Index Profile 不存在")
            return {"job": job, "library": library, "profile": profile}

    def finish_vector_deletion(self, job_id: str, error: str | None = None) -> dict[str, Any]:
        with self.sessions.begin() as session:
            job = session.get(VectorDeletionJob, job_id)
            if not job:
                raise ValueError("向量删除任务不存在")
            if error:
                job.status, job.error, job.lease_owner, job.lease_expires_at = "failed", error, None, None
                return {"id": job.id, "status": job.status, "error": error}
            session.execute(delete(VectorRecordState).where(
                VectorRecordState.index_profile_id == job.index_profile_id,
                VectorRecordState.vector_id.in_(job.vector_ids),
            ))
            job.status, job.error, job.lease_owner, job.lease_expires_at = "completed", None, None, None
            self.audit(session, "vector_deletion.completed", "vector_deletion_job", job.id, {"count": len(job.vector_ids)})
            return {"id": job.id, "status": job.status}

    def list_index_profiles(self) -> list[dict[str, Any]]:
        with self.sessions() as session:
            values = []
            for item in session.scalars(select(KnowledgeIndexProfile).order_by(KnowledgeIndexProfile.code)):
                revisions = session.scalars(select(KnowledgeIndexProfileRevision).where(
                    KnowledgeIndexProfileRevision.knowledge_index_profile_id == item.id,
                ).order_by(KnowledgeIndexProfileRevision.revision_no.desc())).all()
                values.append({"id": item.id, "code": item.code, "knowledge_type": item.knowledge_type,
                    "collection_name": item.collection_name, "embedding_profile_id": item.embedding_profile_id,
                    "fields": item.fields_json, "status": item.status, "current_revision_id": item.current_revision_id,
                    "revisions": [{"id": revision.id, "revision": revision.revision_no, "status": revision.status,
                                   "collection_name": revision.collection_name, "fields": revision.fields_json,
                                   "embedding_profile_id": revision.embedding_profile_id,
                                   "storage_contract_revision_id": revision.storage_contract_revision_id,
                                   "collection_policy": revision.collection_policy} for revision in revisions]})
            return values

    def list_managed_collections(self) -> list[dict[str, Any]]:
        with self.sessions() as session:
            rows = session.execute(select(ManagedCollection, StorageContractRevision, StorageContract).join(
                StorageContractRevision, StorageContractRevision.id == ManagedCollection.storage_contract_revision_id,
            ).join(StorageContract, StorageContract.id == StorageContractRevision.storage_contract_id).order_by(ManagedCollection.collection_name)).all()
            return [{"id": item.id, "collection_name": item.collection_name, "status": item.status,
                     "error": item.error_summary, "desired_spec_hash": item.desired_spec_hash,
                     "observed_spec_hash": item.observed_spec_hash, "storage_contract": {"code": contract.code,
                     "name": contract.name, "revision": revision.revision_no, "dimension": revision.dimension,
                     "metric_type": revision.metric_type}} for item, revision, contract in rows]

    def create_project(self, name: str, legacy_name: str | None = None) -> dict[str, Any]:
        # The optional form avoids breaking old private callers; public API uses
        # one argument and always gets a generated code.
        code, name = (name, legacy_name) if legacy_name is not None else (generated_business_code("PRJ"), name)
        if not str(name or "").strip():
            raise ValueError("项目名称不能为空")
        with self.sessions.begin() as session:
            if session.scalar(select(Project).where(Project.code == code)):
                raise ValueError("项目编码已存在")
            project = Project(id=new_id("project"), code=code.strip(), name=name.strip())
            session.add(project); self.audit(session, "project.created", "project", project.id)
            return {"id": project.id, "code": project.code, "name": project.name, "status": project.status}

    def create_project_task(self, project_id: str, code: str, name: str) -> dict[str, Any]:
        with self.sessions.begin() as session:
            if not session.get(Project, project_id):
                raise ValueError("项目不存在")
            task = ProjectTask(id=new_id("task"), project_id=project_id, code=code.strip(), name=name.strip())
            session.add(task); self.audit(session, "project_task.created", "project_task", task.id)
            return {"id": task.id, "project_id": task.project_id, "code": task.code, "name": task.name, "status": task.status}

    def list_projects(self) -> list[dict[str, Any]]:
        with self.sessions() as session:
            result = []
            for project in session.scalars(select(Project).order_by(Project.created_at.desc())):
                tasks = session.scalars(select(ProjectTask).where(ProjectTask.project_id == project.id)).all()
                result.append({"id": project.id, "code": project.code, "name": project.name, "status": project.status, "tasks": [{"id": task.id, "code": task.code, "name": task.name, "status": task.status} for task in tasks]})
            return result

    def put_route(self, task_id: str, org_code: str, library_ids: list[str]) -> dict[str, Any]:
        if not org_code.strip():
            raise ValueError("org_code 不能为空；general 也必须显式配置")
        if not library_ids:
            raise ValueError("路由至少要选择一个知识库")
        with self.sessions.begin() as session:
            if not session.get(ProjectTask, task_id):
                raise ValueError("项目任务不存在")
            libraries = session.scalars(select(KnowledgeLibrary).where(KnowledgeLibrary.id.in_(library_ids), KnowledgeLibrary.status == "active")).all()
            if len(libraries) != len(set(library_ids)):
                raise ValueError("路由包含不存在或不可用的知识库")
            route = session.scalar(select(ProjectOrgRoute).where(ProjectOrgRoute.project_task_id == task_id, ProjectOrgRoute.org_code == org_code.strip()))
            if not route:
                route = ProjectOrgRoute(id=new_id("route"), project_task_id=task_id, org_code=org_code.strip())
                session.add(route); session.flush()
            existing = session.scalars(select(ProjectOrgRouteLibrary).where(ProjectOrgRouteLibrary.project_org_route_id == route.id)).all()
            for item in existing:
                session.delete(item)
            for library in libraries:
                session.add(ProjectOrgRouteLibrary(id=new_id("rl"), project_org_route_id=route.id, knowledge_library_id=library.id))
            route.status = "draft"; self.audit(session, "routing.draft_updated", "project_org_route", route.id, {"library_ids": library_ids})
            return {"id": route.id, "project_task_id": route.project_task_id, "org_code": route.org_code, "library_ids": library_ids, "status": route.status}

    def routing_snapshot(self, project_id: str) -> dict[str, Any]:
        with self.sessions() as session:
            project = session.get(Project, project_id)
            if not project:
                raise ValueError("项目不存在")
            tasks = session.scalars(select(ProjectTask).where(ProjectTask.project_id == project_id, ProjectTask.status == "active")).all()
            routes = []
            for task in tasks:
                for route in session.scalars(select(ProjectOrgRoute).where(ProjectOrgRoute.project_task_id == task.id)):
                    links = session.scalars(select(ProjectOrgRouteLibrary).where(ProjectOrgRouteLibrary.project_org_route_id == route.id)).all()
                    libraries = [session.get(KnowledgeLibrary, link.knowledge_library_id) for link in links]
                    library_routes = []
                    for library in libraries:
                        if not library:
                            continue
                        profiles = self._index_profile_snapshots_for_library(session, library)
                        library_routes.append({"knowledge_library_id": library.id, "knowledge_type": library.knowledge_type,
                            "partition_name": library.partition_name, "indexes": [{"index_profile_id": profile.id, "index_profile_code": profile.code,
                                "collection_name": profile.collection_name, "fields": profile.fields_json,
                                "partition_name": library.partition_name} for profile in profiles]})
                    routes.append({"task_code": task.code, "org_code": route.org_code, "libraries": library_routes})
            return {"schema": "dataforge.routing-snapshot.v7", "project": {"id": project.id, "code": project.code}, "routes": routes}

    def validate_routing(self, project_id: str) -> dict[str, Any]:
        snapshot = self.routing_snapshot(project_id); problems: list[str] = []
        with self.sessions() as session:
            if not snapshot["routes"]:
                problems.append("项目没有路由")
            for route in snapshot["routes"]:
                if not route["libraries"]:
                    problems.append(f"任务 {route['task_code']} / {route['org_code']} 没有知识库")
                for library_info in route["libraries"]:
                    library = session.get(KnowledgeLibrary, library_info["knowledge_library_id"])
                    if not self._library_ready(session, library):
                        problems.append(f"知识库 {library.code} 向量未就绪")
        return {"valid": not problems, "problems": problems, "snapshot": snapshot}

    def routing_diff(self, project_id: str) -> dict[str, Any]:
        """Compare the in-progress routing draft with the last known good snapshot."""
        current = self.routing_snapshot(project_id)
        with self.sessions() as session:
            previous = session.scalar(select(ProjectRouteVersion).where(ProjectRouteVersion.project_id == project_id, ProjectRouteVersion.status == "published").order_by(ProjectRouteVersion.version_no.desc()))
        def route_map(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
            return {f"{route['task_code']}:{route['org_code']}": route for route in snapshot.get("routes", [])}
        before, after = route_map(previous.snapshot_json if previous else {}), route_map(current)
        return {
            "from_version": previous.version_no if previous else None,
            "added": [after[key] for key in sorted(after.keys() - before.keys())],
            "removed": [before[key] for key in sorted(before.keys() - after.keys())],
            "changed": [{"before": before[key], "after": after[key]} for key in sorted(after.keys() & before.keys()) if before[key] != after[key]],
        }

    def create_route_version(self, project_id: str, snapshot: dict[str, Any], *, status: str = "draft", checksum: str | None = None, object_key: str | None = None) -> ProjectRouteVersion:
        with self.sessions.begin() as session:
            max_version = session.scalar(select(func.max(ProjectRouteVersion.version_no)).where(ProjectRouteVersion.project_id == project_id)) or 0
            value = ProjectRouteVersion(id=new_id("routev"), project_id=project_id, version_no=max_version + 1, status=status, snapshot_json=snapshot, checksum=checksum, object_key=object_key, published_at=utc_now() if status == "published" else None)
            session.add(value); self.audit(session, "routing.version_created", "project_route_version", value.id, {"version_no": value.version_no, "status": status})
            return value

    def mark_route_published(self, version_id: str, checksum: str, object_key: str) -> dict[str, Any]:
        with self.sessions.begin() as session:
            version = session.get(ProjectRouteVersion, version_id)
            if not version:
                raise ValueError("路由版本不存在")
            version.status, version.checksum, version.object_key, version.published_at = "published", checksum, object_key, utc_now()
            for route in session.scalars(select(ProjectOrgRoute).join(ProjectTask).where(ProjectTask.project_id == version.project_id)):
                route.status = "published"
            self.audit(session, "routing.published", "project_route_version", version.id, {"checksum": checksum})
            return {"id": version.id, "project_id": version.project_id, "version_no": version.version_no, "status": version.status, "checksum": checksum, "object_key": object_key}

    def published_route_version(self, project_id: str, version_no: int | None = None) -> ProjectRouteVersion:
        with self.sessions() as session:
            query = select(ProjectRouteVersion).where(ProjectRouteVersion.project_id == project_id, ProjectRouteVersion.status == "published")
            if version_no is not None:
                query = query.where(ProjectRouteVersion.version_no == version_no)
            value = session.scalar(query.order_by(ProjectRouteVersion.version_no.desc()))
            if not value:
                raise ValueError("没有可回滚的已发布路由版本")
            return value

    def list_route_versions(self, project_id: str) -> list[dict[str, Any]]:
        with self.sessions() as session:
            return [{"id": item.id, "project_id": item.project_id, "version_no": item.version_no, "status": item.status, "checksum": item.checksum, "object_key": item.object_key, "created_at": item.created_at.isoformat(), "published_at": item.published_at.isoformat() if item.published_at else None} for item in session.scalars(select(ProjectRouteVersion).where(ProjectRouteVersion.project_id == project_id).order_by(ProjectRouteVersion.version_no.desc()))]

    def route_version_detail(self, project_id: str, version_no: int) -> dict[str, Any]:
        with self.sessions() as session:
            value = session.scalar(select(ProjectRouteVersion).where(
                ProjectRouteVersion.project_id == project_id, ProjectRouteVersion.version_no == version_no,
            ))
            if not value:
                raise ValueError("路由版本不存在")
            return {"id": value.id, "project_id": value.project_id, "version_no": value.version_no, "status": value.status,
                    "checksum": value.checksum, "object_key": value.object_key, "snapshot": value.snapshot_json,
                    "created_at": value.created_at.isoformat(), "published_at": value.published_at.isoformat() if value.published_at else None}

    def close(self) -> None:
        self.engine.dispose()
