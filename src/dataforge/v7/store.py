"""Transactional V7 persistence service.

All methods operate on V7 models only.  There is intentionally no legacy import or
fallback path here: a V7 deployment starts with freshly uploaded material.
"""
from __future__ import annotations

from copy import copy, deepcopy
import hashlib
import json
import logging
import os
import random
import re
import secrets
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from typing import Any, Iterable


from sqlalchemy import create_engine, delete, func, or_, select, tuple_, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session, load_only, sessionmaker
import yaml

from .catalog import (
    CATALOG_SEEDS, OPERATOR_CATEGORIES, SUBFLOW_DISPLAY_NAMES_ZH, catalog_by_code,
    normalize_chunker_params, preparation_flow_definition, subflow_seeds,
)
from .flow import FlowCompiler, FlowValidationError
from .operator_catalog import load_catalog, seed_catalog, resolve_operator, technical_projection
from .operator_runtime_contract import requires_external_runtime, validate_runtime_requirements
from .subflows import SubflowService, published_subflows, pin_subflows
from .flow_authoring import (
    FLOW_AUTHORING_COMPILER, MANAGED_FLOW_CATALOG, ManagedTemplateError,
    assert_normalized_output_types_match_managed_template, normalise_output_key,
)
from .operators.diagnostics import OperatorDiagnostics
from .faq import FAQ_COLLECTION_NAME, FAQ_PROFILE_CODE, FAQ_TYPE_CODE
from .graph_literal import detect_literal
from .graph_schema import GraphExtractionConfig, normalize_graph_config, schema_hash
from .entity_types import clean_removed_entity_references
from .llm_serving import get_llm_serving_registry, resolve_llm_serving_config_path
from .sample_data import SampleDataService
from .servings import DatabaseLLMServingRegistry, ServingManager
from .migrations import assert_schema_current
from .models import (
    AdminSession,
    AuditEvent,
    ComponentCheckResult,
    ComponentCheckRun,
    ComponentHeartbeat,
    DocumentLibrary,
    DocumentLibraryMember,
    DocumentDeletionJob,
    DocumentLibraryProcessingBaseline,
    DocumentLibraryProcessingRecord,
    DocumentLibraryTemplateBinding,
    DocumentLibraryTemplateOutput,
    DocumentIR,
    DebugRunInputSnapshot,
    DebugRunFlowMaterialization,
    DebugRunReviewInput,
    EmbeddingProfile,
    EmbeddingServing,
    RerankerServing,
    KnowledgeAssetItem,
    KnowledgeChange,
    KnowledgeDispatch,
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
    ManagedCollectionDeletionJob,
    ModelServing,
    KnowledgeItem,
    KnowledgeItemSource,
    KnowledgeChunkGeneration,
    KnowledgeJob,
    KnowledgeJobReviewInput,
    KnowledgeLibrary,
    KnowledgeLibraryWorkLease,
    KnowledgeAssetVersion,
    KnowledgeAssetGcJob,
    InstitutionReleaseSnapshot,
    InstitutionReleaseDraft,
    InstitutionReleaseDraftProject,
    ImportedRouteCandidate,
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
    FlowNodeArtifactBinding,
    FlowRunEvent,
    FlowRunSinkPreview,
    Project,
    DataForgeInstance,
    Deployment,
    DeploymentTarget,
    MilvusTarget,
    MilvusTargetRevision,
    ProjectDeployment,
    ProjectDeploymentTask,
    ProjectOrgRoute,
    ProjectOrgRouteLibrary,
    ProjectRouteVersion,
    ProjectRouteVersionAsset,
    ProjectTask,
    KnowledgeMigrationJob,
    KnowledgeMigrationItem,
    Source,
    SourceChunk,
    SourceChunkSet,
    SourceChunkRevision,
    SourcePreparationJob,
    SourceReviewSnapshot,
    SourceReviewSnapshotChunk,
    SourceVersion,
    VectorRecordState,
    VectorDeletionJob,
    VectorSyncJob,
    utc_now,
)
from .source_anchor import (
    api_source_anchor,
    edited_anchor,
    merge_source_anchors,
    sequential_part_ranges,
    split_source_anchor,
)
from .asset_items import freeze_asset_items, vector_items


V7_TYPE_META = {
    "text": ("文", "文本知识"),
    "qa": ("问", "问答知识"),
    "graph": ("图", "图谱知识"),
}
FIXED_KNOWLEDGE_ASSET_TYPES = (
    {"key": "text", "knowledge_type": "text", "graph_mode": None, "label": "文本知识", "icon": "文"},
    {"key": "qa", "knowledge_type": "qa", "graph_mode": None, "label": "问答知识", "icon": "问"},
    {"key": "graph:triple", "knowledge_type": "graph", "graph_mode": "triple", "label": "三元组图谱", "icon": "△"},
    {"key": "graph:semantic", "knowledge_type": "graph", "graph_mode": "semantic", "label": "语义图谱", "icon": "⬡"},
)
KNOWLEDGE_REVIEW_TYPES = frozenset({"text", "qa"})
V7_TEMPLATE_SEEDS = tuple(
    (code, MANAGED_FLOW_CATALOG.get(code).name + "流程", list(MANAGED_FLOW_CATALOG.get(code).output_types))
    for code in MANAGED_FLOW_CATALOG.codes
)
V7_TEMPLATE_LEGACY_NAMES = {
    "standard-text": "标准文本知识流程",
    "standard-qa": "标准问答知识流程",
    "standard-graph-triple": "标准三元组图谱流程",
    "standard-graph-semantic": "标准语义图谱流程",
    "standard-multi": "标准多产出知识流程",
}
V7_BUILTIN_TEMPLATE_CODES = frozenset(code for code, _, _ in V7_TEMPLATE_SEEDS)
WORK_LEASE_DURATION = timedelta(minutes=5)
ARTIFACT_DEADLOCK_RETRY_WINDOWS = ((0.05, 0.10), (0.10, 0.20), (0.20, 0.40))
GRAPH_NEIGHBOR_NOTICE_THRESHOLD = 100
GRAPH_NEIGHBOR_CONFIRM_THRESHOLD = 500

QA_AGENT_TEST_MILVUS_URL = os.environ.get("DATAFORGE_QA_AGENT_TEST_MILVUS_URL") or "http://milvus-central-test:19531"
QA_AGENT_PRODUCTION_MILVUS_URL = os.environ.get("DATAFORGE_QA_AGENT_PRODUCTION_MILVUS_URL") or "http://milvus-central-production:19531"
CENTRAL_TEST_MILVUS_URI = os.environ.get("DATAFORGE_CENTRAL_TEST_MILVUS_URI") or "http://milvus-central-test:19531"
CENTRAL_PRODUCTION_MILVUS_URI = os.environ.get("DATAFORGE_CENTRAL_PRODUCTION_MILVUS_URI") or "http://milvus-central-production:19531"
CENTRAL_DEPLOYMENT_CODE = "dataforge-central"
CENTRAL_DEPLOYMENT_ID = "deployment_dataforge_central"
CENTRAL_STAGE_TARGETS = {
    "test": ("milvus_dataforge_central_test", "DataForge 中心测试 Milvus", CENTRAL_TEST_MILVUS_URI),
    "production": ("milvus_dataforge_central_production", "DataForge 中心生产 Milvus", CENTRAL_PRODUCTION_MILVUS_URI),
}
LOGGER = logging.getLogger(__name__)


class ReviewGateError(ValueError):
    """Structured state conflict used by every server-side review gate."""

    def __init__(self, code: str, message: str, *, source_version_id: str | None = None,
                 counts: dict[str, int] | None = None, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.source_version_id = source_version_id
        self.counts = counts or {}
        self.details = details or {}

    def payload(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "source_version_id": self.source_version_id,
            "counts": self.counts,
            **self.details,
        }


from .operator_parameters import FlowParameterError, validate_parameters


def _database_error_identity(exc: OperationalError) -> tuple[int | None, str | None]:
    original = getattr(exc, "orig", None)
    arguments = getattr(original, "args", ())
    raw_code = getattr(original, "errno", None)
    if raw_code is None and arguments:
        raw_code = arguments[0]
    try:
        code = int(raw_code)
    except (TypeError, ValueError):
        code = None
    raw_sqlstate = getattr(original, "sqlstate", None) or getattr(original, "sql_state", None)
    return code, str(raw_sqlstate) if raw_sqlstate is not None else None


def _is_retryable_mysql_deadlock(exc: OperationalError, dialect_name: str) -> bool:
    if dialect_name != "mysql":
        return False
    code, sqlstate = _database_error_identity(exc)
    return code == 1213 or sqlstate == "40001"


def is_qa_agent_project(project: Project | None) -> bool:
    if not project:
        return False
    values = {
        str(project.code or "").strip().lower().replace("_", "-").replace(" ", "-"),
        str(project.name or "").strip().lower().replace("_", "-").replace(" ", "-"),
    }
    return any(value == "qa-agent" or value.startswith("qa-agent-") for value in values)


def qa_agent_profile_contract(task: ProjectTask | None, profile: KnowledgeIndexProfile | None) -> bool:
    if not task or not profile:
        return False
    expected = {
        "knowledge_qa": ("qa-question", "dataforge_qa_question", "qa"),
        "faq": (FAQ_PROFILE_CODE, FAQ_COLLECTION_NAME, FAQ_TYPE_CODE),
    }.get(str(task.code or "").strip())
    return bool(expected and (profile.code, profile.collection_name, profile.knowledge_type) == expected)


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(16)}"


def generated_business_code(prefix: str) -> str:
    """Codes owned by the platform are never accepted from business clients."""
    return f"{prefix}-{utc_now():%Y%m%d}-{uuid.uuid4()}"


def institution_deployment_code(institution_code: str) -> str:
    """Build a stable technical Deployment code from an institution code."""
    value = re.sub(r"[^a-z0-9]+", "-", str(institution_code or "").strip().lower()).strip("-")
    if not value:
        raise ValueError("机构代码无法生成 Deployment Code")
    return f"inst-{value}"


DEFAULT_INDEX_FIELD_MAPPING = {
    "id": "id",
    "vector": "vector",
    "knowledge_library_id": "knowledge_library_id",
    "source_knowledge_id": "source_knowledge_id",
    "content": "content",
    "data": "data",
}

GRAPH_TRIPLE_INDEX_FIELD_MAPPING = {
    **DEFAULT_INDEX_FIELD_MAPPING,
    "subject": "subject",
    "predicate": "predicate",
    "object": "object",
    "subject_type": "subject_type",
    "object_type": "object_type",
}

KG_PROJECT_CODE = "kg-for-consultation"
KG_DEPLOYMENT_CODE = CENTRAL_DEPLOYMENT_CODE


def content_hash(content: str, data: dict[str, Any]) -> str:
    canonical = json.dumps({"content": content, "data": data}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def template_signature(output_types: Iterable[str]) -> str:
    return ",".join(sorted(dict.fromkeys(output_types)))


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


def builtin_provisioning_token(managed_collection_id: str, collection_name: str,
                               spec_hash: str) -> str:
    """Return a stable ownership token for an immutable built-in Contract.

    Empty Compose rebuilds intentionally discard MySQL but retain the external
    test Milvus.  Built-in IDs, names and spec hashes are deterministic, so the
    marker token must be deterministic as well or every rebuild would reject
    the Collection it created during the previous run.
    """
    material = f"dataforge-builtin:{managed_collection_id}:{collection_name}:{spec_hash}"
    return hashlib.sha256(material.encode()).hexdigest()[:48]


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
    def __init__(self, database_url: str, *, enforce_serving_health: bool = False,
                 config_encryption_key: str | None = None):
        self.database_url = database_url
        connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
        self.engine = create_engine(database_url, future=True, connect_args=connect_args)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False, future=True)
        self.enforce_serving_health = enforce_serving_health
        self.llm_serving_registry = DatabaseLLMServingRegistry(
            ServingManager(self.sessions, config_encryption_key or os.getenv("DATAFORGE_CONFIG_ENCRYPTION_KEY"))
        )

    def assert_schema_current(self) -> str:
        return assert_schema_current(self.database_url)

    def seed(self) -> None:
        """Idempotently install current V7 defaults after the schema gate succeeds."""
        with self.sessions.begin() as session:
            if not session.scalar(select(DataForgeInstance)):
                instance_mode = os.getenv("DATAFORGE_INSTANCE_MODE", "central").strip().lower()
                if instance_mode not in {"central", "local"}:
                    raise ValueError("DATAFORGE_INSTANCE_MODE 只允许 central 或 local")
                instance_code = os.getenv("DATAFORGE_INSTANCE_CODE", "dataforge-central").strip()
                if not instance_code:
                    raise ValueError("DATAFORGE_INSTANCE_CODE 不能为空")
                session.add(DataForgeInstance(
                    id="instance_default", instance_code=instance_code,
                    instance_mode=instance_mode, bound_deployment_id=None,
                    source_instance_id=None,
                ))
            self._seed_model_servings(session)
            for code, (icon, name) in V7_TYPE_META.items():
                if not session.scalar(select(KnowledgeType).where(KnowledgeType.code == code)):
                    session.add(KnowledgeType(id=f"type_{code}", code=code, name=name, icon=icon, kind="builtin", status="active"))
            profile = session.scalar(select(EmbeddingProfile).where(EmbeddingProfile.code == "bce_base_768_v1"))
            if not profile:
                try:
                    configured_dimension = int(os.getenv("EMBEDDING_DIM", "768"))
                except ValueError as exc:
                    raise ValueError("EMBEDDING_DIM 必须是正整数") from exc
                if configured_dimension <= 0:
                    raise ValueError("EMBEDDING_DIM 必须是正整数")
                profile = EmbeddingProfile(
                    id="embedding_bce_base_768_v1",
                    code="bce_base_768_v1",
                    model=os.getenv("EMBEDDING_MODEL", "bce-embedding-base").strip() or "bce-embedding-base",
                    dimension=configured_dimension,
                    metric_type="COSINE",
                    endpoint_ref="EMBEDDING_API_BASE",
                )
                session.add(profile)
            new_flow_revisions = []
            for code, name, output_types in V7_TEMPLATE_SEEDS:
                template = session.scalar(select(KnowledgeFlowTemplate).where(KnowledgeFlowTemplate.code == code))
                stage_config = MANAGED_FLOW_CATALOG.default_stage_config(code)
                if not template:
                    template = KnowledgeFlowTemplate(
                        id=f"flow_{code}", code=code, name=name, output_types=output_types,
                        definition_json=stage_config, authoring_mode="standard", managed_template_code=code,
                        status="active", is_default=code != "standard-multi", purpose="knowledge",
                    )
                    session.add(template)
                    session.flush()
                else:
                    if template.name == V7_TEMPLATE_LEGACY_NAMES[code]:
                        template.name = name
                    template.purpose, template.needs_review_upgrade = "knowledge", False
                revision = session.scalar(select(KnowledgeFlowTemplateRevision).where(
                    KnowledgeFlowTemplateRevision.knowledge_flow_template_id == template.id,
                ).order_by(KnowledgeFlowTemplateRevision.revision_no.desc()))
                if not revision:
                    revision = KnowledgeFlowTemplateRevision(
                        id=new_id("flowrev"), knowledge_flow_template_id=template.id, revision_no=1,
                        definition_json=stage_config, authoring_mode="standard", managed_template_code=code,
                        status="draft", purpose="knowledge",
                    )
                    session.add(revision)
                    new_flow_revisions.append((revision, output_types))
                # The latest Revision is the authoring-state source of truth.  Seed must
                # not rewrite an existing draft/published revision or leave the template
                # row advertising a different mode from the definition returned by APIs.
                template.definition_json = revision.definition_json
                template.authoring_mode = revision.authoring_mode or "advanced"
                template.managed_template_code = (
                    revision.managed_template_code if template.authoring_mode == "standard" else None
                )
            # ``graph`` has long normalized to ``graph:triple``.  Move active
            # document bindings to the canonical template before archiving the
            # redundant row, while keeping historical revisions and jobs.
            compatibility_template = session.scalar(select(KnowledgeFlowTemplate).where(
                KnowledgeFlowTemplate.code == "standard-graph",
            ))
            if compatibility_template:
                triple_template = session.scalar(select(KnowledgeFlowTemplate).where(
                    KnowledgeFlowTemplate.code == "standard-graph-triple",
                ))
                for binding in session.scalars(select(DocumentLibraryTemplateBinding).where(
                    DocumentLibraryTemplateBinding.knowledge_flow_template_id == compatibility_template.id,
                    DocumentLibraryTemplateBinding.status == "active",
                )):
                    replacement = session.scalar(select(DocumentLibraryTemplateBinding).where(
                        DocumentLibraryTemplateBinding.document_library_id == binding.document_library_id,
                        DocumentLibraryTemplateBinding.knowledge_flow_template_id == triple_template.id,
                    ))
                    if replacement:
                        replacement.status, binding.status = "active", "removed"
                    else:
                        binding.knowledge_flow_template_id = triple_template.id
                compatibility_template.status, compatibility_template.is_default = "archived", False
            profile_id = profile.id
            embedding_serving_code = "bce_base_768"
            index_seeds = (
                ("text", "text", "dataforge_text_knowledge"),
                ("qa-question", "qa", "dataforge_qa_question"),
                ("qa-full", "qa", "dataforge_qa_full"),
                ("graph", "graph", "dataforge_graph_knowledge"),
                ("graph-triple", "graph", "dataforge_graph_triple_knowledge"),
                ("graph-semantic", "graph", "dataforge_graph_semantic_knowledge"),
            )
            for code, kind, collection in index_seeds:
                field_mapping = GRAPH_TRIPLE_INDEX_FIELD_MAPPING if code == "graph-triple" else DEFAULT_INDEX_FIELD_MAPPING
                index_profile = session.scalar(select(KnowledgeIndexProfile).where(KnowledgeIndexProfile.code == code))
                if not index_profile:
                    index_profile = KnowledgeIndexProfile(
                        id=f"index_{code}", code=code, knowledge_type=kind, collection_name=collection,
                        embedding_profile_id=profile_id,
                        embedding_serving_id=embedding_serving_code,
                        embedding_input="question" if code == "qa-question" else "question_answer" if code == "qa-full" else "canonical_content",
                        fields_json=dict(field_mapping), origin="builtin", status="active",
                    )
                    session.add(index_profile)
                    session.flush()
                    revision = KnowledgeIndexProfileRevision(
                        id=f"indexrev_{code}_1", knowledge_index_profile_id=index_profile.id, revision_no=1,
                        collection_name=collection, embedding_profile_id=profile_id,
                        embedding_serving_id=embedding_serving_code,
                        embedding_input="question" if code == "qa-question" else "question_answer" if code == "qa-full" else "canonical_content",
                        fields_json=dict(field_mapping), status="published", published_at=utc_now(),
                    )
                    session.add(revision)
                    index_profile.current_revision_id = revision.id
                elif not index_profile.embedding_serving_id:
                    index_profile.embedding_serving_id = embedding_serving_code
                    index_profile.embedding_input = "question" if code == "qa-question" else "question_answer" if code == "qa-full" else "canonical_content"
                    for existing_revision in session.scalars(select(KnowledgeIndexProfileRevision).where(
                        KnowledgeIndexProfileRevision.knowledge_index_profile_id == index_profile.id,
                    )):
                        if not existing_revision.embedding_serving_id:
                            existing_revision.embedding_serving_id = embedding_serving_code
                            existing_revision.embedding_input = index_profile.embedding_input
                elif code == "graph-triple" and dict(index_profile.fields_json or {}) != field_mapping:
                    current = session.get(KnowledgeIndexProfileRevision, index_profile.current_revision_id) \
                        if index_profile.current_revision_id else None
                    next_revision = int(session.scalar(select(func.max(KnowledgeIndexProfileRevision.revision_no)).where(
                        KnowledgeIndexProfileRevision.knowledge_index_profile_id == index_profile.id,
                    )) or 0) + 1
                    revision = KnowledgeIndexProfileRevision(
                        id=new_id("indexrev"), knowledge_index_profile_id=index_profile.id,
                        revision_no=next_revision, collection_name=collection,
                        embedding_profile_id=(current.embedding_profile_id if current else profile_id),
                        fields_json=dict(field_mapping),
                        storage_contract_revision_id=(current.storage_contract_revision_id if current else None),
                        collection_policy=(current.collection_policy if current else "managed"),
                        status="published", published_at=utc_now(),
                    )
                    session.add(revision); session.flush()
                    index_profile.fields_json = dict(field_mapping)
                    index_profile.current_revision_id = revision.id
            session.flush()
            self._seed_storage_contracts(session, profile)
            self._seed_governance(session)
            self._seed_qa_agent(session)
            self._seed_kg_for_consultation(session)
            self._backfill_embedding_servings(session)
            session.flush()
            # Compile against the database defaults written by this transaction,
            # including environment overrides. An engine-bound registry cannot
            # see them before commit; the legacy YAML may contain different values.
            seed_serving_manager = copy(self.llm_serving_registry.manager)
            seed_serving_manager.sessions = sessionmaker(
                bind=session.connection(), expire_on_commit=False, join_transaction_mode="rollback_only",
            )
            seed_serving_registry = DatabaseLLMServingRegistry(seed_serving_manager)
            for revision, output_types in new_flow_revisions:
                self._create_execution_snapshot(session, revision, output_types,
                    llm_registry=seed_serving_registry)
                revision.status, revision.published_at = "published", utc_now()
            session.flush()
            for template in session.scalars(select(KnowledgeFlowTemplate)):
                if template.purpose == "knowledge":
                    if template.authoring_mode == "standard":
                        has_reviewed_root = True
                    else:
                        has_reviewed_root = any(
                            node.get("ref") == "reviewed-source-chunk-input"
                            for node in (template.definition_json or {}).get("nodes", [])
                        )
                    template.needs_review_upgrade = not has_reviewed_root
                revision = session.scalar(select(KnowledgeFlowTemplateRevision).where(
                    KnowledgeFlowTemplateRevision.knowledge_flow_template_id == template.id,
                    KnowledgeFlowTemplateRevision.status == "published",
                ).order_by(KnowledgeFlowTemplateRevision.revision_no.desc()))
                if revision and not revision.execution_snapshot_id and not template.needs_review_upgrade:
                    self._published_execution_snapshot(session, revision)

    def _seed_model_servings(self, session: Session) -> None:
        qwen = session.scalar(select(ModelServing).where(ModelServing.serving_code == "qwen3_32b"))
        if not qwen:
            path = resolve_llm_serving_config_path()
            raw: dict[str, Any] = {}
            if path.is_file():
                try:
                    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
                    raw = dict((loaded.get("servings") or {}).get("qwen3_32b") or {})
                except (OSError, yaml.YAMLError):
                    raw = {}
            base_url = str(os.getenv("DATAFORGE_DEFAULT_LLM_BASE_URL") or raw.get("base_url") or "").strip().rstrip("/") or None
            model_name = str(os.getenv("DATAFORGE_DEFAULT_LLM_MODEL") or raw.get("model_name") or "Qwen3-32B").strip()
            try:
                max_tokens = int(os.getenv("DATAFORGE_DEFAULT_LLM_MAX_TOKENS") or raw.get("max_tokens") or 16384)
            except ValueError as exc:
                raise ValueError("DATAFORGE_DEFAULT_LLM_MAX_TOKENS 必须是正整数") from exc
            if max_tokens <= 0:
                raise ValueError("DATAFORGE_DEFAULT_LLM_MAX_TOKENS 必须是正整数")
            has_default = bool(session.scalar(select(ModelServing.id).where(ModelServing.is_default.is_(True))))
            qwen = ModelServing(
                id="modelserving_qwen3_32b", serving_code="qwen3_32b", name="Qwen3-32B",
                serving_type="openai-compatible-chat", model_name=model_name,
                base_url=base_url, timeout_seconds=120, max_retries=2,
                max_tokens=max_tokens, disable_thinking=True,
                is_enabled=True, is_default=not has_default,
                last_check_status="not_checked" if base_url else "pending_configuration",
            )
            api_key = os.getenv(str(raw.get("api_key_env") or "LOCAL_LLM_API_KEY"), "").strip()
            if api_key not in {"", "EMPTY", "fake"}:
                qwen.credential_ciphertext = self.llm_serving_registry.manager.cipher.encrypt(
                    api_key, f"dataforge:model-serving:{qwen.id}:v1",
                )
                qwen.credential_key_version = self.llm_serving_registry.manager.cipher.key_version
                qwen.credential_configured = True
            session.add(qwen)
        qwen8b = session.scalar(select(ModelServing).where(ModelServing.serving_code == "qwen3_8b_awq"))
        if not qwen8b:
            path = resolve_llm_serving_config_path()
            raw8b: dict[str, Any] = {}
            if path.is_file():
                try:
                    loaded8b = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
                    raw8b = dict((loaded8b.get("servings") or {}).get("qwen3_8b_awq") or {})
                except (OSError, yaml.YAMLError):
                    raw8b = {}
            base_url8b = str(raw8b.get("base_url") or "").strip().rstrip("/") or None
            model_name8b = str(raw8b.get("model_name") or "Qwen3-8B-AWQ").strip()
            try:
                max_tokens8b = int(raw8b.get("max_tokens") or 16384)
            except ValueError as exc:
                raise ValueError("qwen3_8b_awq 的 max_tokens 必须是正整数") from exc
            if max_tokens8b <= 0:
                raise ValueError("qwen3_8b_awq 的 max_tokens 必须是正整数")
            qwen8b = ModelServing(
                id="modelserving_qwen3_8b_awq", serving_code="qwen3_8b_awq", name="Qwen3-8B-AWQ",
                serving_type="openai-compatible-chat", model_name=model_name8b,
                base_url=base_url8b, timeout_seconds=120, max_retries=2,
                max_tokens=max_tokens8b, disable_thinking=True,
                is_enabled=True, is_default=False,
                last_check_status="not_checked" if base_url8b else "pending_configuration",
            )
            api_key8b = os.getenv(str(raw8b.get("api_key_env") or "LOCAL_LLM_API_KEY"), "").strip()
            if api_key8b not in {"", "EMPTY", "fake"}:
                qwen8b.credential_ciphertext = self.llm_serving_registry.manager.cipher.encrypt(
                    api_key8b, f"dataforge:model-serving:{qwen8b.id}:v1",
                )
                qwen8b.credential_key_version = self.llm_serving_registry.manager.cipher.key_version
                qwen8b.credential_configured = True
            session.add(qwen8b)
        bce = session.scalar(select(EmbeddingServing).where(EmbeddingServing.serving_code == "bce_base_768"))
        if not bce:
            base_url = os.getenv("EMBEDDING_API_BASE", "").strip().rstrip("/") or None
            model_name = os.getenv("EMBEDDING_MODEL", "bce-embedding-base").strip() or "bce-embedding-base"
            try:
                dimension = int(os.getenv("EMBEDDING_DIM", "768"))
                batch_size = int(os.getenv("EMBEDDING_BATCH_SIZE", "32"))
            except ValueError as exc:
                raise ValueError("EMBEDDING_DIM 与 EMBEDDING_BATCH_SIZE 必须是正整数") from exc
            if dimension <= 0 or batch_size <= 0:
                raise ValueError("EMBEDDING_DIM 与 EMBEDDING_BATCH_SIZE 必须是正整数")
            has_default = bool(session.scalar(select(EmbeddingServing.id).where(EmbeddingServing.is_default.is_(True))))
            bce = EmbeddingServing(
                id="embeddingserving_bce_base_768", serving_code="bce_base_768", name="BCE Base 768",
                provider_type="openai-compatible-embedding", model_name=model_name,
                base_url=base_url, dimension=dimension, batch_size=batch_size, timeout_seconds=120, max_retries=2,
                is_enabled=True, is_default=not has_default,
                last_check_status="not_checked" if base_url else "pending_configuration",
            )
            api_key = os.getenv("EMBEDDING_API_KEY", "").strip()
            if api_key not in {"", "EMPTY", "fake"}:
                bce.credential_ciphertext = self.llm_serving_registry.manager.cipher.encrypt(
                    api_key, f"dataforge:embedding-serving:{bce.id}:v1",
                )
                bce.credential_key_version = self.llm_serving_registry.manager.cipher.key_version
                bce.credential_configured = True
            session.add(bce)

        if not session.scalar(select(RerankerServing.id).where(RerankerServing.serving_code == "bge_reranker_large")):
            manager = self.llm_serving_registry.manager
            base_url = manager._validate_base_url(os.getenv("DATAFORGE_DEFAULT_RERANKER_BASE_URL", ""))
            reranker = RerankerServing(
                id="rerankerserving_bge_large", serving_code="bge_reranker_large", name="BGE Reranker Large",
                provider_type="cohere-compatible-rerank",
                model_name=os.getenv("DATAFORGE_DEFAULT_RERANKER_MODEL", "bge-reranker-large").strip() or "bge-reranker-large",
                base_url=base_url, timeout_seconds=120, max_retries=2, max_batch_size=32, max_concurrency=4,
                is_enabled=True,
                is_default=not bool(session.scalar(select(RerankerServing.id).where(RerankerServing.is_default.is_(True)))),
                last_check_status="not_checked" if base_url else "pending_configuration",
            )
            api_key = os.getenv("DATAFORGE_DEFAULT_RERANKER_API_KEY", "").strip()
            if api_key not in {"", "EMPTY", "fake"}:
                reranker.credential_ciphertext = manager.cipher.encrypt(api_key, manager._aad("reranker", reranker.id))
                reranker.credential_key_version = manager.cipher.key_version
                reranker.credential_configured = True
            session.add(reranker)

    @staticmethod
    def _backfill_embedding_servings(session: Session) -> None:
        """Bind pre-feature profiles without overwriting an existing Serving choice."""
        bce = session.scalar(select(EmbeddingServing).where(EmbeddingServing.serving_code == "bce_base_768"))
        for embedding in session.scalars(select(EmbeddingProfile)):
            if bce and embedding.model == bce.model_name and embedding.dimension == bce.dimension:
                serving_code = "bce_base_768"
            else:
                normalized = re.sub(r"[^a-z0-9_-]+", "_", embedding.code.lower()).strip("_-")
                if not normalized or not normalized[0].isalpha():
                    normalized = f"embedding_{normalized}"
                serving_code = normalized[:64]
                existing = session.scalar(select(EmbeddingServing).where(
                    EmbeddingServing.serving_code == serving_code,
                ))
                if not existing:
                    session.add(EmbeddingServing(
                        id=f"embeddingserving_{hashlib.sha256(embedding.id.encode()).hexdigest()[:24]}",
                        serving_code=serving_code, name=embedding.code,
                        provider_type="openai-compatible-embedding", model_name=embedding.model,
                        base_url=None, dimension=embedding.dimension, batch_size=32,
                        timeout_seconds=120, max_retries=2, is_enabled=False, is_default=False,
                        last_check_status="pending_configuration",
                    ))
            for profile in session.scalars(select(KnowledgeIndexProfile).where(
                KnowledgeIndexProfile.embedding_profile_id == embedding.id,
                KnowledgeIndexProfile.embedding_serving_id.is_(None),
            )):
                profile.embedding_serving_id = serving_code
            for revision in session.scalars(select(KnowledgeIndexProfileRevision).where(
                KnowledgeIndexProfileRevision.embedding_profile_id == embedding.id,
                KnowledgeIndexProfileRevision.embedding_serving_id.is_(None),
            )):
                revision.embedding_serving_id = serving_code

    def _ensure_central_deployment(self, session: Session) -> Deployment:
        deployment = session.scalar(select(Deployment).where(Deployment.code == CENTRAL_DEPLOYMENT_CODE))
        if not deployment:
            deployment = Deployment(
                id=CENTRAL_DEPLOYMENT_ID, code=CENTRAL_DEPLOYMENT_CODE,
                name="DataForge 中心", scope="central", status="active",
            )
            session.add(deployment); session.flush()
        elif deployment.name != "DataForge 中心":
            deployment.name = "DataForge 中心"
        for stage, (target_id, target_name, target_url) in CENTRAL_STAGE_TARGETS.items():
            target = session.get(MilvusTarget, target_id)
            if not target:
                target = MilvusTarget(
                    id=target_id, name=target_name,
                )
                session.add(target); session.flush()
            if not target.current_revision_id and not target.candidate_revision_id:
                registered_url = target_url
                revision = MilvusTargetRevision(
                    id=f"mtrev_dataforge_central_{stage}", milvus_target_id=target.id,
                    revision_no=1, milvus_url=registered_url,
                    connection_fingerprint=hashlib.sha256(
                        json.dumps({"uri": registered_url,
                                    "credential_fingerprint": hashlib.sha256(b"").hexdigest()},
                                   sort_keys=True, separators=(",", ":")).encode()
                    ).hexdigest(),
                    verification_status="pending_verification",
                )
                session.add(revision); session.flush()
                target.candidate_revision_id = revision.id
        session.flush()
        return deployment

    def _ensure_project_binding(self, session: Session, project: Project, deployment: Deployment) -> ProjectDeployment:
        binding = session.scalar(select(ProjectDeployment).where(
            ProjectDeployment.project_id == project.id,
            ProjectDeployment.deployment_id == deployment.id,
        ))
        if not binding:
            binding = ProjectDeployment(
                id=new_id("pdeploy"), project_id=project.id, deployment_id=deployment.id, status="active",
            )
            session.add(binding); session.flush()
        return binding

    def _seed_qa_agent(self, session: Session) -> None:
        """Install the qa-agent project and its central question-search task."""
        deployment = self._ensure_central_deployment(session)

        project = session.scalar(select(Project).where(Project.code == "qa-agent"))
        if not project:
            project = Project(
                id="project_qa_agent", code="qa-agent", name="qa_agent", status="active",
            )
            session.add(project); session.flush()

        task = session.scalar(select(ProjectTask).where(
            ProjectTask.project_id == project.id,
            ProjectTask.code == "knowledge_qa",
        ))
        if not task:
            task = ProjectTask(
                id="task_qa_agent_knowledge_qa", project_id=project.id,
                code="knowledge_qa", name="知识问答", knowledge_type="qa",
                description="qa-agent DataForge 知识问答任务", status="active",
            )
            session.add(task); session.flush()

        project_deployment = self._ensure_project_binding(session, project, deployment)
        existing = session.scalar(select(ProjectDeploymentTask).where(
            ProjectDeploymentTask.project_deployment_id == project_deployment.id,
            ProjectDeploymentTask.project_task_id == task.id,
        ))
        if not existing:
            profile = session.scalar(select(KnowledgeIndexProfile).where(
                KnowledgeIndexProfile.code == "qa-question",
            ))
            if not profile:
                raise ValueError("qa-agent 种子要求 qa-question Index Profile")
            session.add(ProjectDeploymentTask(
                id="dtask_qa_agent_knowledge_qa",
                project_deployment_id=project_deployment.id,
                project_task_id=task.id, index_profile_id=profile.id,
                qa_embedding_mode="question", top_k=10, enabled=True,
            ))

    def _seed_kg_for_consultation(self, session: Session) -> None:
        """Install the test-only project binding on the shared central Deployment."""
        deployment = self._ensure_central_deployment(session)

        project = session.scalar(select(Project).where(Project.code == KG_PROJECT_CODE))
        if not project:
            project = Project(id="project_kg_for_consultation", code=KG_PROJECT_CODE,
                              name="kg_for_consultation", status="active")
            session.add(project); session.flush()

        task_specs = (
            ("task_kg_clinical_guideline_graph", "clinical_guideline_graph", "临床指南图谱检索", "graph", "index_graph-triple", 20),
            ("task_kg_department_text_infectious", "department_text.infectious_disease", "传染科文本检索", "text", "index_text", 10),
        )
        tasks: list[tuple[ProjectTask, str, int]] = []
        for task_id, code, name, knowledge_type, profile_id, top_k in task_specs:
            task = session.scalar(select(ProjectTask).where(ProjectTask.project_id == project.id,
                                                            ProjectTask.code == code))
            if not task:
                task = ProjectTask(id=task_id, project_id=project.id, code=code, name=name,
                                   knowledge_type=knowledge_type,
                                   description="kg_for_consultation 第一阶段测试任务", status="active")
                session.add(task); session.flush()
            tasks.append((task, profile_id, top_k))

        project_deployment = self._ensure_project_binding(session, project, deployment)

        for task, profile_id, top_k in tasks:
            existing = session.scalar(select(ProjectDeploymentTask).where(
                ProjectDeploymentTask.project_deployment_id == project_deployment.id,
                ProjectDeploymentTask.project_task_id == task.id,
            ))
            if not existing:
                session.add(ProjectDeploymentTask(
                    id=f"dtask_{task.id.removeprefix('task_')}",
                    project_deployment_id=project_deployment.id, project_task_id=task.id,
                    index_profile_id=profile_id, top_k=top_k, enabled=True,
                ))

    def _seed_governance(self, session: Session) -> None:
        """Seed published governance assets.  They are normal revisions, not constants."""
        quality = session.scalar(select(QualityProfile).where(QualityProfile.code == "default-knowledge-quality"))
        if not quality:
            quality = QualityProfile(id="quality_default", code="default-knowledge-quality", name="默认知识质量", status="active")
            session.add(quality); session.flush()
        quality_revision = session.scalar(select(QualityProfileRevision).where(QualityProfileRevision.quality_profile_id == quality.id, QualityProfileRevision.revision_no == 1))
        if not quality_revision:
            quality_revision = QualityProfileRevision(id="qualityrev_default", quality_profile_id=quality.id, revision_no=1, rules_json={"pass_score": 0.8, "review_score": 0.6}, knowledge_types=["*"], status="published", published_at=utc_now())
            session.add(quality_revision)
        prompt = session.scalar(select(PromptTemplate).where(PromptTemplate.code == "knowledge-generator-default"))
        if not prompt:
            prompt = PromptTemplate(id="prompt_default", code="knowledge-generator-default", name="默认知识生成提示", status="active")
            session.add(prompt); session.flush()
        if not session.scalar(select(PromptTemplateRevision).where(PromptTemplateRevision.prompt_template_id == prompt.id, PromptTemplateRevision.revision_no == 1)):
            session.add(PromptTemplateRevision(id="promptrev_default", prompt_template_id=prompt.id, revision_no=1, body="根据输入内容生成结构化知识。", input_schema={"type": "object"}, output_schema={"type": "object"}, knowledge_types=["*"], status="published", published_at=utc_now()))
        if not session.get(PromptTemplate, "prompt_refiner"):
            session.add(PromptTemplate(id="prompt_refiner", code="knowledge-refiner-default", name="候选知识修订", status="active"))
            session.flush()
            session.add(PromptTemplateRevision(id="promptrev_refiner", prompt_template_id="prompt_refiner", revision_no=1,
                body="修订以下候选知识的表述，保留原有事实，不添加新事实。输入为 JSON，只返回相同字段的 JSON 对象，不输出来源、Evidence 或其他字段。",
                input_schema={"type": "object"}, output_schema={"type": "object"}, knowledge_types=["text", "qa"], status="published", published_at=utc_now()))
        if not session.get(PromptTemplate, "prompt_candidate_filter"):
            session.add(PromptTemplate(id="prompt_candidate_filter", code="candidate-filter-default", name="候选知识评分过滤", status="active"))
            session.flush()
            session.add(PromptTemplateRevision(id="promptrev_candidate_filter", prompt_template_id="prompt_candidate_filter", revision_no=1,
                body="评价以下候选知识的清晰度、完整性与实用性：1分不可用，2分较差，3分一般，4分良好，5分优秀。仅返回一个1至5的整数，不输出解释。候选正文：\n",
                input_schema={"type": "string"}, output_schema={"type": "integer", "minimum": 1, "maximum": 5},
                knowledge_types=["text", "qa"], status="published", published_at=utc_now()))
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
                            "source_entity": {"type": "object", "required": ["name", "type", "description"],
                                              "properties": {"name": {"type": "string"}, "type": {"type": "string"}, "description": {"type": "string"}, "type_label": {"type": "string"}, "aliases": {"type": "array"}}},
                            "target_entity": {"type": "object", "required": ["name", "type", "description"],
                                              "properties": {"name": {"type": "string"}, "type": {"type": "string"}, "description": {"type": "string"}, "type_label": {"type": "string"}, "aliases": {"type": "array"}}},
                            "relation": {"type": "object", "required": ["type", "description"],
                                         "properties": {"type": {"type": "string"}, "type_label": {"type": "string"}, "description": {"type": "string"}, "keywords": {"type": "array"}, "weight": {"type": "number"}}},
                            "evidence": {"type": "array"},
                        }},
                        ["source_entity.name", "relation.description", "target_entity.name"],
                        ["source_entity.name", "relation.type", "target_entity.name"],
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
        seed_catalog(session)
        for item in subflow_seeds():
            subflow = session.scalar(select(FlowSubgraph).where(FlowSubgraph.code == item["code"]))
            if not subflow:
                subflow = FlowSubgraph(id=f"subflow_{item['code'].replace('-', '_')}", code=item["code"], name=item["name"], status="active")
                session.add(subflow); session.flush()
            if not session.scalar(select(FlowSubgraphRevision).where(FlowSubgraphRevision.flow_subgraph_id == subflow.id, FlowSubgraphRevision.revision_no == 1)):
                definition_json = {**item["definition"], "_subgraph_code": item["code"], "_subgraph_revision": 1}
                session.add(FlowSubgraphRevision(id=new_id("subflowrev"), flow_subgraph_id=subflow.id, revision_no=1, definition_json=definition_json,
                                                description=item.get("description", ""), status="published", published_at=utc_now()))
        preparation = session.scalar(select(KnowledgeFlowTemplate).where(
            KnowledgeFlowTemplate.code == "source-preparation",
        ))
        if not preparation:
            preparation = KnowledgeFlowTemplate(
                id="flow_source_preparation", code="source-preparation", name="Source Preparation",
                output_types=[], definition_json=preparation_flow_definition(), status="active",
                is_default=False, purpose="source_preparation",
            )
            session.add(preparation); session.flush()
        preparation.definition_json = preparation_flow_definition()
        preparation.purpose, preparation.needs_review_upgrade = "source_preparation", False
        preparation_revision = session.scalar(select(KnowledgeFlowTemplateRevision).where(
            KnowledgeFlowTemplateRevision.knowledge_flow_template_id == preparation.id,
            KnowledgeFlowTemplateRevision.status == "published",
        ).order_by(KnowledgeFlowTemplateRevision.revision_no.desc()))
        if not preparation_revision:
            preparation_revision = KnowledgeFlowTemplateRevision(
                id="flowrev_source_preparation_1", knowledge_flow_template_id=preparation.id,
                revision_no=1, definition_json=preparation.definition_json, purpose="source_preparation",
                status="draft",
            )
            session.add(preparation_revision); session.flush()
            self._create_execution_snapshot(session, preparation_revision, [])
            preparation_revision.status, preparation_revision.published_at = "published", utc_now()
        else:
            self._published_execution_snapshot(session, preparation_revision)

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
            managed_id = f"collection_{code}"
            stable_token = builtin_provisioning_token(managed_id, collection_name, spec_hash)
            managed = session.scalar(select(ManagedCollection).where(ManagedCollection.collection_name == collection_name))
            if not managed:
                managed = ManagedCollection(
                    id=managed_id, storage_contract_revision_id=revision.id,
                    collection_name=collection_name, provisioning_token=stable_token,
                    desired_spec_hash=spec_hash, status="planned",
                )
                session.add(managed)
            elif managed.id == managed_id and managed.desired_spec_hash == spec_hash:
                # Repair pre-deterministic empty-volume seeds in place.  The
                # matching Milvus marker is reconciled separately and remains
                # fail-closed until one explicit cleanup/re-provision cycle.
                managed.provisioning_token = stable_token
            profile = session.scalar(select(KnowledgeIndexProfile).where(KnowledgeIndexProfile.code == code))
            if profile:
                profile.origin = "builtin"
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
                bindings = [] if not revision else list(session.scalars(select(KnowledgeTypeIndexBinding).where(
                    KnowledgeTypeIndexBinding.knowledge_type_revision_id == revision.id,
                )))
                profile_values = []
                for binding in bindings:
                    profile = session.get(KnowledgeIndexProfile, binding.index_profile_id)
                    if not profile:
                        continue
                    profile_revision = session.get(KnowledgeIndexProfileRevision, binding.index_profile_revision_id)
                    profile_values.append({
                        "id": profile.id, "code": profile.code, "origin": profile.origin,
                        "collection_name": profile_revision.collection_name if profile_revision else profile.collection_name,
                        "collection_policy": profile_revision.collection_policy if profile_revision else "external",
                        "profile_revision_id": binding.index_profile_revision_id,
                        "field_path": binding.field_path, "role": binding.role,
                    })
                values.append({"id": item.id, "code": item.code, "name": item.name, "icon": item.icon, "kind": item.kind,
                               "status": item.status, "current_revision": None if not revision else {
                                   "id": revision.id, "revision": revision.revision_no, "schema": revision.schema_json,
                                   "canonical_field": revision.canonical_field, "identity_fields": revision.identity_fields,
                                   "source_policy": revision.source_policy, "quality_profile_revision_id": revision.quality_profile_revision_id,
                                }, "index_profiles": profile_values})
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
                              index_profile_ids: list[str], *, managed_collection_name: str = "",
                              reuse_managed_collection_id: str | None = None) -> dict[str, Any]:
        code, name = code.strip(), name.strip()
        if not code or not name:
            raise ValueError("知识类型编码和名称不能为空")
        if code in V7_TYPE_META:
            raise ValueError("内置知识类型不可通过扩展接口创建")
        self._validate_type_contract(schema, canonical_field, identity_fields, source_policy)
        with self.sessions.begin() as session:
            if session.scalar(select(KnowledgeType).where(KnowledgeType.code == code)):
                raise ValueError("知识类型编码已存在")
            self._validate_quality_revision(session, quality_profile_revision_id)
            item = KnowledgeType(id=new_id("type"), code=code, name=name, icon=(icon or "知")[:8], kind="extension", status="draft")
            session.add(item); session.flush()
            auto_profile, auto_revision, managed = self._create_extension_auto_profile(
                session, item, managed_collection_name, reuse_managed_collection_id,
            )
            manual_profiles = self._validate_additional_profiles(session, code, index_profile_ids, managed.collection_name)
            profile_ids = [auto_profile.id, *[profile.id for profile in manual_profiles]]
            revision = self._add_type_revision(
                session, item, schema, canonical_field, identity_fields, source_policy,
                quality_profile_revision_id, profile_ids,
                profile_revision_ids={auto_profile.id: auto_revision.id},
            )
            self.audit(session, "knowledge_type.created", "knowledge_type", item.id, {"revision": revision.revision_no})
            return {"id": item.id, "revision_id": revision.id, "revision": revision.revision_no, "status": "draft",
                    "managed_profile_id": auto_profile.id, "managed_collection_id": managed.id,
                    "managed_collection_name": managed.collection_name}

    def revise_knowledge_type(self, type_id: str, schema: dict[str, Any], canonical_field: str, identity_fields: list[str],
                              source_policy: str, quality_profile_revision_id: str, index_profile_ids: list[str],
                              *, managed_collection_name: str = "",
                              reuse_managed_collection_id: str | None = None) -> dict[str, Any]:
        with self.sessions.begin() as session:
            item = session.get(KnowledgeType, type_id)
            if not item:
                raise ValueError("知识类型不存在")
            self._validate_type_contract(schema, canonical_field, identity_fields, source_policy, builtin_code=item.code if item.kind == "builtin" else None)
            self._validate_quality_revision(session, quality_profile_revision_id)
            auto_profile = session.scalar(select(KnowledgeIndexProfile).where(
                KnowledgeIndexProfile.owner_knowledge_type_id == item.id,
                KnowledgeIndexProfile.origin == "extension_auto",
            ))
            profile_revision_ids: dict[str, str] = {}
            if item.kind == "extension":
                if not auto_profile:
                    auto_profile, auto_revision, managed = self._create_extension_auto_profile(
                        session, item, managed_collection_name, reuse_managed_collection_id,
                    )
                elif managed_collection_name or reuse_managed_collection_id:
                    embedding = session.get(EmbeddingProfile, auto_profile.embedding_profile_id)
                    assert embedding
                    storage_revision, managed = self._managed_storage_contract(
                        session, auto_profile.code, managed_collection_name, embedding,
                        {"fields": [dict(field) for field in _COMMON_STORAGE_FIELDS]},
                        DEFAULT_INDEX_FIELD_MAPPING, {"index_type": "AUTOINDEX"},
                        reuse_managed_collection_id=reuse_managed_collection_id,
                    )
                    latest = session.scalar(select(func.max(KnowledgeIndexProfileRevision.revision_no)).where(
                        KnowledgeIndexProfileRevision.knowledge_index_profile_id == auto_profile.id,
                    )) or 0
                    auto_revision = KnowledgeIndexProfileRevision(
                        id=new_id("indexrev"), knowledge_index_profile_id=auto_profile.id, revision_no=latest + 1,
                        collection_name=managed.collection_name, embedding_profile_id=embedding.id,
                        fields_json=dict(DEFAULT_INDEX_FIELD_MAPPING), storage_contract_revision_id=storage_revision.id,
                        collection_policy="managed",
                    )
                    session.add(auto_revision); session.flush()
                else:
                    auto_revision = session.scalar(select(KnowledgeIndexProfileRevision).where(
                        KnowledgeIndexProfileRevision.knowledge_index_profile_id == auto_profile.id,
                    ).order_by(KnowledgeIndexProfileRevision.revision_no.desc()))
                    assert auto_revision
                    managed = session.scalar(select(ManagedCollection).where(
                        ManagedCollection.collection_name == auto_revision.collection_name,
                    ))
                    assert managed
                profile_revision_ids[auto_profile.id] = auto_revision.id
                manual_profiles = self._validate_additional_profiles(session, item.code, index_profile_ids, managed.collection_name)
                profile_ids = [auto_profile.id, *[profile.id for profile in manual_profiles]]
            else:
                self._validate_type_revision_dependencies(session, quality_profile_revision_id, index_profile_ids)
                profile_ids = index_profile_ids
            revision = self._add_type_revision(
                session, item, schema, canonical_field, identity_fields, source_policy,
                quality_profile_revision_id, profile_ids, profile_revision_ids=profile_revision_ids,
            )
            self.audit(session, "knowledge_type.revised", "knowledge_type", item.id, {"revision": revision.revision_no})
            return {"id": item.id, "revision_id": revision.id, "revision": revision.revision_no, "status": "draft"}

    def _create_extension_auto_profile(self, session: Session, item: KnowledgeType, collection_name: str,
                                       reuse_managed_collection_id: str | None) -> tuple[KnowledgeIndexProfile, KnowledgeIndexProfileRevision, ManagedCollection]:
        embedding = session.scalar(select(EmbeddingProfile).where(EmbeddingProfile.code == "bce_base_768_v1"))
        if not embedding:
            raise ValueError("默认 Embedding Profile 不存在")
        profile_code = f"{item.code}-default"
        normalized_type_code = re.sub(r"[^A-Za-z0-9_]", "_", item.code.replace("-", "_"))
        resolved_collection_name = collection_name or f"dataforge_{normalized_type_code}_knowledge"
        storage_revision, managed = self._managed_storage_contract(
            session, profile_code, resolved_collection_name, embedding,
            {"fields": [dict(field) for field in _COMMON_STORAGE_FIELDS]},
            DEFAULT_INDEX_FIELD_MAPPING, {"index_type": "AUTOINDEX"},
            reuse_managed_collection_id=reuse_managed_collection_id,
        )
        profile = KnowledgeIndexProfile(
            id=new_id("index"), code=profile_code, knowledge_type=item.code,
            collection_name=managed.collection_name, embedding_profile_id=embedding.id,
            fields_json=dict(DEFAULT_INDEX_FIELD_MAPPING), origin="extension_auto",
            owner_knowledge_type_id=item.id, status="draft",
        )
        session.add(profile); session.flush()
        revision = KnowledgeIndexProfileRevision(
            id=new_id("indexrev"), knowledge_index_profile_id=profile.id, revision_no=1,
            collection_name=managed.collection_name, embedding_profile_id=embedding.id,
            fields_json=dict(DEFAULT_INDEX_FIELD_MAPPING), storage_contract_revision_id=storage_revision.id,
            collection_policy="managed",
        )
        session.add(revision); session.flush()
        return profile, revision, managed

    @staticmethod
    def _validate_quality_revision(session: Session, quality_profile_revision_id: str) -> None:
        quality = session.get(QualityProfileRevision, quality_profile_revision_id)
        if not quality or quality.status != "published":
            raise ValueError("必须绑定已发布的 Quality Profile 修订")

    @staticmethod
    def _validate_additional_profiles(session: Session, type_code: str, profile_ids: list[str],
                                      auto_collection_name: str) -> list[KnowledgeIndexProfile]:
        if not profile_ids:
            return []
        profiles = list(session.scalars(select(KnowledgeIndexProfile).where(
            KnowledgeIndexProfile.id.in_(list(dict.fromkeys(profile_ids))),
            KnowledgeIndexProfile.status == "active",
        )))
        if len(profiles) != len(set(profile_ids)):
            raise ValueError("附加 Manual Profile 不存在或尚未发布")
        if any(profile.origin != "manual" or profile.knowledge_type != type_code or not profile.current_revision_id for profile in profiles):
            raise ValueError("附加 Profile 必须是同一 Knowledge Type 的已发布 Manual Profile")
        collection_names = [auto_collection_name, *[profile.collection_name for profile in profiles]]
        if len(collection_names) != len(set(collection_names)):
            raise ValueError("同一 Type Revision 不能绑定两个指向同一 Collection 的 Profile")
        return profiles

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
            self._validate_quality_revision(session, revision.quality_profile_revision_id or "")
            collections: list[str] = []
            for binding in bindings:
                profile = session.get(KnowledgeIndexProfile, binding.index_profile_id)
                profile_revision = session.get(KnowledgeIndexProfileRevision, binding.index_profile_revision_id) if binding.index_profile_revision_id else None
                if not profile or not profile_revision or profile.knowledge_type != item.code:
                    raise ValueError("Type Revision 绑定的 Profile 或修订无效")
                if profile.origin == "extension_auto":
                    if profile.owner_knowledge_type_id != item.id or profile_revision.collection_policy != "managed":
                        raise ValueError("扩展 Type 的自动 Profile 所有权无效")
                    managed = session.scalar(select(ManagedCollection).where(
                        ManagedCollection.collection_name == profile_revision.collection_name,
                        ManagedCollection.storage_contract_revision_id == profile_revision.storage_contract_revision_id,
                    ))
                    if not managed or managed.status != "ready" or managed.observed_spec_hash != managed.desired_spec_hash:
                        raise ValueError("扩展 Type 的受管 Collection 尚未完成 Provision")
                elif profile.status != "active" or profile_revision.status != "published":
                    raise ValueError("附加 Profile 尚未发布")
                collections.append(profile_revision.collection_name)
            if len(collections) != len(set(collections)):
                raise ValueError("同一 Type Revision 不能绑定两个指向同一 Collection 的 Profile")
            return {"id": item.id, "revision_id": revision.id, "valid": True}

    def publish_knowledge_type(self, type_id: str) -> dict[str, Any]:
        self.validate_knowledge_type(type_id)
        with self.sessions.begin() as session:
            item = session.get(KnowledgeType, type_id)
            revision = session.scalar(select(KnowledgeTypeRevision).where(KnowledgeTypeRevision.knowledge_type_id == type_id).order_by(KnowledgeTypeRevision.revision_no.desc()))
            assert item and revision
            for binding in session.scalars(select(KnowledgeTypeIndexBinding).where(
                KnowledgeTypeIndexBinding.knowledge_type_revision_id == revision.id,
            )):
                profile = session.get(KnowledgeIndexProfile, binding.index_profile_id)
                profile_revision = session.get(KnowledgeIndexProfileRevision, binding.index_profile_revision_id) if binding.index_profile_revision_id else None
                if profile and profile_revision and profile.origin == "extension_auto":
                    profile_revision.status, profile_revision.published_at = "published", utc_now()
                    profile.collection_name = profile_revision.collection_name
                    profile.embedding_profile_id = profile_revision.embedding_profile_id
                    profile.fields_json = profile_revision.fields_json
                    profile.current_revision_id, profile.status = profile_revision.id, "active"
            revision.status, revision.published_at, item.current_revision_id, item.status = "published", utc_now(), revision.id, "active"
            self.audit(session, "knowledge_type.published", "knowledge_type", item.id, {"revision": revision.revision_no})
            return {"id": item.id, "revision_id": revision.id, "revision": revision.revision_no, "status": "published"}

    def knowledge_type_publication_requirements(self, type_id: str) -> list[dict[str, Any]]:
        """Return live external validations and managed reconciliations needed before publish."""
        with self.sessions() as session:
            item = session.get(KnowledgeType, type_id)
            if not item:
                raise ValueError("知识类型不存在")
            revision = session.scalar(select(KnowledgeTypeRevision).where(
                KnowledgeTypeRevision.knowledge_type_id == type_id,
            ).order_by(KnowledgeTypeRevision.revision_no.desc()))
            if not revision:
                raise ValueError("知识类型没有修订")
            requirements = []
            for binding in session.scalars(select(KnowledgeTypeIndexBinding).where(
                KnowledgeTypeIndexBinding.knowledge_type_revision_id == revision.id,
            )):
                profile = session.get(KnowledgeIndexProfile, binding.index_profile_id)
                profile_revision = session.get(KnowledgeIndexProfileRevision, binding.index_profile_revision_id) if binding.index_profile_revision_id else None
                if not profile or not profile_revision:
                    raise ValueError("Type Revision 绑定的 Profile 修订不存在")
                embedding = session.get(EmbeddingProfile, profile_revision.embedding_profile_id)
                if not embedding:
                    raise ValueError("Embedding Profile 不存在")
                requirement = {
                    "profile_id": profile.id, "profile_revision_id": profile_revision.id,
                    "collection_policy": profile_revision.collection_policy,
                    "collection_name": profile_revision.collection_name,
                    "fields": profile_revision.fields_json, "dimension": embedding.dimension,
                }
                if profile_revision.collection_policy == "managed":
                    managed = session.scalar(select(ManagedCollection).where(
                        ManagedCollection.collection_name == profile_revision.collection_name,
                        ManagedCollection.storage_contract_revision_id == profile_revision.storage_contract_revision_id,
                    ))
                    if not managed or managed.status == "deleted":
                        raise ValueError("受管 Collection 登记不存在或已删除")
                    requirement["managed_collection_id"] = managed.id
                requirements.append(requirement)
            return requirements

    def index_profile_publication_requirement(self, profile_id: str) -> dict[str, Any]:
        with self.sessions() as session:
            profile = session.get(KnowledgeIndexProfile, profile_id)
            if not profile:
                raise ValueError("Index Profile 不存在")
            revision = session.scalar(select(KnowledgeIndexProfileRevision).where(
                KnowledgeIndexProfileRevision.knowledge_index_profile_id == profile_id,
            ).order_by(KnowledgeIndexProfileRevision.revision_no.desc()))
            if not revision:
                raise ValueError("Index Profile 没有可发布修订")
            embedding = session.get(EmbeddingProfile, revision.embedding_profile_id)
            if not embedding:
                raise ValueError("Embedding Profile 不存在")
            if revision.embedding_serving_id:
                serving = session.scalar(select(EmbeddingServing).where(
                    EmbeddingServing.serving_code == revision.embedding_serving_id,
                ))
                if not serving or not serving.is_enabled:
                    raise ValueError("Embedding Serving 不存在或已停用")
                if not serving.base_url or serving.last_check_status != "healthy":
                    raise ValueError("Embedding Serving 尚未配置并通过连接测试")
                if serving.dimension != embedding.dimension:
                    raise ValueError("Embedding Serving 与 Index Profile 维度不一致")
                if serving.last_observed_dimension != serving.dimension:
                    raise ValueError("Embedding Serving 实际维度与配置维度不一致")
            contract = session.get(StorageContractRevision, revision.storage_contract_revision_id) \
                if revision.storage_contract_revision_id else None
            if contract and contract.dimension != embedding.dimension:
                raise ValueError("Index Profile 与 Storage Contract 维度不一致")
            result = {"profile_id": profile.id, "collection_policy": revision.collection_policy,
                      "collection_name": revision.collection_name, "fields": revision.fields_json,
                      "dimension": embedding.dimension}
            if revision.collection_policy == "managed":
                managed = session.scalar(select(ManagedCollection).where(
                    ManagedCollection.collection_name == revision.collection_name,
                    ManagedCollection.storage_contract_revision_id == revision.storage_contract_revision_id,
                ))
                if not managed or managed.status == "deleted":
                    raise ValueError("受管 Collection 登记不存在或已删除")
                result["managed_collection_id"] = managed.id
            return result

    def _add_type_revision(self, session: Session, item: KnowledgeType, schema: dict[str, Any], canonical_field: str,
                           identity_fields: list[str], source_policy: str, quality_profile_revision_id: str,
                           index_profile_ids: list[str], *, profile_revision_ids: dict[str, str] | None = None) -> KnowledgeTypeRevision:
        latest = session.scalar(select(func.max(KnowledgeTypeRevision.revision_no)).where(KnowledgeTypeRevision.knowledge_type_id == item.id)) or 0
        revision = KnowledgeTypeRevision(id=new_id("typerev"), knowledge_type_id=item.id, revision_no=latest + 1,
                                         schema_json=schema, canonical_field=canonical_field.strip(), identity_fields=list(identity_fields),
                                         source_policy=source_policy, quality_profile_revision_id=quality_profile_revision_id)
        session.add(revision); session.flush()
        for profile_id in dict.fromkeys(index_profile_ids):
            profile = session.get(KnowledgeIndexProfile, profile_id)
            assert profile
            bound_revision_id = (profile_revision_ids or {}).get(profile.id) or profile.current_revision_id
            if not bound_revision_id:
                latest_revision = session.scalar(select(KnowledgeIndexProfileRevision).where(
                    KnowledgeIndexProfileRevision.knowledge_index_profile_id == profile.id,
                ).order_by(KnowledgeIndexProfileRevision.revision_no.desc()))
                bound_revision_id = latest_revision.id if latest_revision else None
            if not bound_revision_id:
                raise ValueError("Index Profile 没有可绑定的修订")
            session.add(KnowledgeTypeIndexBinding(id=new_id("typeindex"), knowledge_type_revision_id=revision.id,
                                                  index_profile_id=profile.id, index_profile_revision_id=bound_revision_id,
                                                  field_path=canonical_field.strip(),
                                                  role="primary" if profile.origin == "extension_auto" else "secondary"))
        return revision

    @staticmethod
    def _validate_type_revision_dependencies(session: Session, quality_profile_revision_id: str, index_profile_ids: list[str]) -> None:
        V7Store._validate_quality_revision(session, quality_profile_revision_id)
        if not index_profile_ids:
            raise ValueError("必须绑定至少一个已发布 Index Profile")
        profiles = list(session.scalars(select(KnowledgeIndexProfile).where(KnowledgeIndexProfile.id.in_(index_profile_ids), KnowledgeIndexProfile.status == "active")))
        if len(profiles) != len(set(index_profile_ids)) or any(not profile.current_revision_id for profile in profiles):
            raise ValueError("Index Profile 不存在或尚未发布")

    def create_index_profile(self, code: str, knowledge_type: str, collection_name: str, embedding_code: str,
                             embedding_model: str, dimension: int, metric_type: str, endpoint_ref: str | None,
                             fields: dict[str, Any], *, collection_policy: str = "external",
                             storage_schema: dict[str, Any] | None = None,
                             index_spec: dict[str, Any] | None = None, collection_mode: str | None = None,
                              reuse_managed_collection_id: str | None = None, origin: str = "manual",
                              owner_knowledge_type_id: str | None = None,
                              embedding_serving_id: str | None = None,
                              embedding_input: str = "canonical_content") -> dict[str, Any]:
        if collection_mode is not None:
            if collection_mode not in {"create", "attach"}:
                raise ValueError("Collection 模式必须为 create 或 attach")
            collection_policy = "managed" if collection_mode == "create" else "external"
        code, collection_name, embedding_code = code.strip(), collection_name.strip(), embedding_code.strip()
        if embedding_input not in {"canonical_content", "question", "question_answer"}:
            raise ValueError("Embedding Input 无效")
        if not code or not (embedding_code or embedding_serving_id) or collection_policy not in {"external", "managed"}:
            raise ValueError("Index Profile、Embedding 编码和 Collection 策略必须有效")
        if origin not in {"builtin", "extension_auto", "manual"}:
            raise ValueError("Index Profile 来源无效")
        if collection_policy == "external" and not collection_name:
            raise ValueError("外部 Index Profile 必须指定 Collection")
        if collection_policy == "external" and reuse_managed_collection_id:
            raise ValueError("external Profile 不能复用受管 Collection 登记")
        self._validate_index_mapping(fields)
        if dimension <= 0 and not embedding_serving_id:
            raise ValueError("Embedding 维度必须为正整数")
        with self.sessions.begin() as session:
            if session.scalar(select(KnowledgeIndexProfile).where(KnowledgeIndexProfile.code == code)):
                raise ValueError("Index Profile 编码已存在")
            if embedding_serving_id:
                serving = session.scalar(select(EmbeddingServing).where(
                    EmbeddingServing.serving_code == embedding_serving_id,
                    EmbeddingServing.is_enabled.is_(True),
                ))
                if not serving:
                    raise ValueError("Embedding Serving 不存在或已停用")
                embedding_code = "bce_base_768_v1" if serving.serving_code == "bce_base_768" else f"serving_{serving.serving_code}_{serving.dimension}"
                embedding_model, dimension, endpoint_ref = serving.model_name, serving.dimension, None
            embedding = session.scalar(select(EmbeddingProfile).where(EmbeddingProfile.code == embedding_code))
            if not embedding:
                embedding = EmbeddingProfile(id=new_id("embedding"), code=embedding_code, model=embedding_model.strip() or embedding_code,
                                             dimension=dimension, metric_type=metric_type.strip() or "COSINE", endpoint_ref=endpoint_ref)
                session.add(embedding); session.flush()
            elif (embedding.model, embedding.dimension, embedding.metric_type) != (embedding_model.strip() or embedding_code, dimension, metric_type):
                raise ValueError("同一 Embedding 编码的模型、维度和度量类型必须保持稳定")
            storage_revision = None
            managed = None
            if collection_policy == "managed":
                storage_revision, managed = self._managed_storage_contract(
                    session, code, collection_name, embedding, storage_schema, fields, index_spec,
                    reuse_managed_collection_id=reuse_managed_collection_id,
                )
                collection_name = managed.collection_name
            item = KnowledgeIndexProfile(id=new_id("index"), code=code, knowledge_type=knowledge_type.strip(), collection_name=collection_name,
                                         embedding_profile_id=embedding.id, embedding_serving_id=embedding_serving_id,
                                         embedding_input=embedding_input, fields_json=dict(fields), origin=origin,
                                         owner_knowledge_type_id=owner_knowledge_type_id, status="draft")
            session.add(item); session.flush()
            revision = KnowledgeIndexProfileRevision(id=new_id("indexrev"), knowledge_index_profile_id=item.id, revision_no=1,
                                                    collection_name=collection_name, embedding_profile_id=embedding.id,
                                                    embedding_serving_id=embedding_serving_id, embedding_input=embedding_input,
                                                    fields_json=dict(fields),
                                                    storage_contract_revision_id=storage_revision.id if storage_revision else None,
                                                    collection_policy=collection_policy)
            session.add(revision); self.audit(session, "index_profile.created", "index_profile", item.id)
            return {"id": item.id, "revision_id": revision.id, "revision": 1, "status": "draft",
                    "origin": item.origin, "collection_policy": collection_policy,
                    "collection_name": collection_name, "managed_collection_id": managed.id if managed else None}

    def _managed_storage_contract(self, session: Session, code: str, collection_name: str,
                                  embedding: EmbeddingProfile, schema: dict[str, Any] | None,
                                  fields: dict[str, Any], index_spec: dict[str, Any] | None,
                                  *, reuse_managed_collection_id: str | None = None) -> tuple[StorageContractRevision, ManagedCollection]:
        if not isinstance(schema, dict) or not isinstance(schema.get("fields"), list):
            raise ValueError("受管 Index Profile 必须提供完整 storage_schema.fields")
        physical_names = {str(item.get("name")) for item in schema["fields"] if isinstance(item, dict)}
        if not set(str(value) for value in fields.values()).issubset(physical_names):
            raise ValueError("Storage Contract 缺少 Index Profile 映射的物理字段")
        index_json = index_spec or {"index_type": "AUTOINDEX"}
        spec_hash = storage_spec_hash(schema, embedding, index_json)
        revision = session.scalar(select(StorageContractRevision).where(StorageContractRevision.storage_spec_hash == spec_hash))
        if not revision:
            contract = session.scalar(select(StorageContract).where(StorageContract.code == code))
            if not contract:
                contract = StorageContract(id=new_id("storage"), code=code, name=f"{code} 存储结构")
                session.add(contract); session.flush()
            latest = session.scalar(select(func.max(StorageContractRevision.revision_no)).where(
                StorageContractRevision.storage_contract_id == contract.id,
            )) or 0
            revision = StorageContractRevision(
                id=new_id("storagerev"), storage_contract_id=contract.id, revision_no=latest + 1,
                schema_json=schema, embedding_profile_id=embedding.id, vector_type="FLOAT_VECTOR",
                dimension=embedding.dimension, metric_type=embedding.metric_type, index_json=index_json,
                storage_spec_hash=spec_hash, status="published", published_at=utc_now(),
            )
            session.add(revision); session.flush(); contract.current_revision_id = revision.id
        if reuse_managed_collection_id:
            managed = session.get(ManagedCollection, reuse_managed_collection_id)
            if not managed or managed.status != "ready":
                raise ValueError("只能显式复用 ready 的受管 Collection")
            if managed.desired_spec_hash != spec_hash or managed.storage_contract_revision_id != revision.id:
                raise ValueError("所选受管 Collection 与 Storage Contract 不兼容")
            if collection_name and collection_name != managed.collection_name:
                raise ValueError("复用受管 Collection 时不能指定不同名称")
            return revision, managed
        resolved_name = collection_name or f"dataforge_{code.replace('-', '_')}_knowledge"
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,254}", resolved_name):
            raise ValueError("Collection 名必须以字母或下划线开头，且只包含字母、数字和下划线")
        collision = session.scalar(select(ManagedCollection).where(ManagedCollection.collection_name == resolved_name))
        if collision:
            raise ValueError("Collection 名已登记；如需复用请显式选择该受管 Collection")
        stable_first_party = code == "qa-agent-faq-default" and resolved_name == "dataforge_qa_agent_faq"
        managed_id = "collection_qa-agent-faq" if stable_first_party else new_id("collection")
        token = builtin_provisioning_token(managed_id, resolved_name, spec_hash) if stable_first_party else secrets.token_hex(24)
        managed = ManagedCollection(
            id=managed_id, storage_contract_revision_id=revision.id,
            collection_name=resolved_name, provisioning_token=token,
            desired_spec_hash=spec_hash, status="planned",
        )
        session.add(managed); session.flush()
        return revision, managed

    def revise_index_profile(self, profile_id: str, collection_name: str, embedding_code: str, embedding_model: str,
                             dimension: int, metric_type: str, endpoint_ref: str | None, fields: dict[str, Any],
                             *, collection_policy: str = "external", storage_schema: dict[str, Any] | None = None,
                              index_spec: dict[str, Any] | None = None,
                              reuse_managed_collection_id: str | None = None,
                              embedding_serving_id: str | None = None,
                              embedding_input: str = "canonical_content") -> dict[str, Any]:
        self._validate_index_mapping(fields)
        if collection_policy not in {"external", "managed"}:
            raise ValueError("Collection 策略必须为 external 或 managed")
        with self.sessions.begin() as session:
            item = session.get(KnowledgeIndexProfile, profile_id)
            if not item:
                raise ValueError("Index Profile 不存在")
            if embedding_input not in {"canonical_content", "question", "question_answer"}:
                raise ValueError("Embedding Input 无效")
            if embedding_serving_id:
                serving = session.scalar(select(EmbeddingServing).where(
                    EmbeddingServing.serving_code == embedding_serving_id,
                    EmbeddingServing.is_enabled.is_(True),
                ))
                if not serving:
                    raise ValueError("Embedding Serving 不存在或已停用")
                embedding_code = "bce_base_768_v1" if serving.serving_code == "bce_base_768" else f"serving_{serving.serving_code}_{serving.dimension}"
                embedding_model, dimension, endpoint_ref = serving.model_name, serving.dimension, None
            embedding = session.scalar(select(EmbeddingProfile).where(EmbeddingProfile.code == embedding_code.strip()))
            if not embedding:
                embedding = EmbeddingProfile(id=new_id("embedding"), code=embedding_code.strip(), model=embedding_model.strip() or embedding_code.strip(), dimension=dimension, metric_type=metric_type, endpoint_ref=endpoint_ref)
                session.add(embedding); session.flush()
            elif (embedding.model, embedding.dimension, embedding.metric_type) != (embedding_model.strip() or embedding_code.strip(), dimension, metric_type):
                raise ValueError("同一 Embedding 编码的模型、维度和度量类型必须保持稳定")
            storage_revision = None
            managed = None
            if collection_policy == "managed":
                storage_revision, managed = self._managed_storage_contract(
                    session, item.code, collection_name.strip(), embedding, storage_schema, fields, index_spec,
                    reuse_managed_collection_id=reuse_managed_collection_id,
                )
                collection_name = managed.collection_name
            elif not collection_name.strip():
                raise ValueError("外部 Index Profile 必须指定 Collection")
            latest = session.scalar(select(func.max(KnowledgeIndexProfileRevision.revision_no)).where(KnowledgeIndexProfileRevision.knowledge_index_profile_id == item.id)) or 0
            revision = KnowledgeIndexProfileRevision(id=new_id("indexrev"), knowledge_index_profile_id=item.id, revision_no=latest + 1,
                                                    collection_name=collection_name.strip(), embedding_profile_id=embedding.id,
                                                    embedding_serving_id=embedding_serving_id, embedding_input=embedding_input,
                                                    fields_json=dict(fields),
                                                    storage_contract_revision_id=storage_revision.id if storage_revision else None,
                                                    collection_policy=collection_policy)
            session.add(revision); self.audit(session, "index_profile.revised", "index_profile", item.id, {"revision": revision.revision_no})
            return {"id": item.id, "revision_id": revision.id, "revision": revision.revision_no, "status": "draft",
                    "collection_name": collection_name.strip(), "managed_collection_id": managed.id if managed else None}

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
            if revision.embedding_serving_id:
                serving = session.scalar(select(EmbeddingServing).where(
                    EmbeddingServing.serving_code == revision.embedding_serving_id,
                ))
                if not serving or not serving.is_enabled or not serving.base_url:
                    raise ValueError("Embedding Serving 不存在、已停用或待配置")
                if serving.last_check_status != "healthy" or serving.last_observed_dimension != serving.dimension:
                    raise ValueError("Embedding Serving 尚未通过实际维度连接测试")
                if serving.dimension != embedding.dimension:
                    raise ValueError("Embedding Serving 与 Index Profile 维度不一致")
            contract = session.get(StorageContractRevision, revision.storage_contract_revision_id) \
                if revision.storage_contract_revision_id else None
            if contract and contract.dimension != embedding.dimension:
                raise ValueError("Index Profile 与 Storage Contract 维度不一致")
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
            item.embedding_serving_id, item.embedding_input = revision.embedding_serving_id, revision.embedding_input
            item.current_revision_id, item.status = revision.id, "active"
            self.audit(session, "index_profile.published", "index_profile", item.id, {"revision": revision.revision_no})
            return {"id": item.id, "revision_id": revision.id, "revision": revision.revision_no, "status": "published"}

    def list_operator_catalog(self, *, include_internal: bool = False, query: str = "", category: str = "",
                              knowledge_type: str = "", exposure: str = "", status: str = "", surface: str = "",
                              source: str = "", catalog_group: str = "", include_versions: bool = True,
                              include_runtime: bool = True, _catalog=None) -> list[dict[str, Any]]:
        with self.sessions() as session:
            catalog = _catalog if _catalog is not None else load_catalog(session)
            values = []
            for value in sorted(catalog.values(), key=lambda item: (item["category"], item["code"])):
                value = dict(value)
                if not include_internal and value["exposure"] == "internal":
                    continue
                searchable = json.dumps(value, ensure_ascii=False).lower()
                if query and query.lower() not in searchable: continue
                if category and value["category"] != category: continue
                if knowledge_type and "*" not in value["knowledge_types"] and knowledge_type not in value["knowledge_types"]: continue
                if exposure and value["exposure"] != {"canvas": "public"}.get(exposure, exposure): continue
                if status and value["status"] != status: continue
                if surface and surface not in value["surfaces"]: continue
                if source and value["source"] != source: continue
                if catalog_group and value["catalog_group"] != catalog_group: continue
                if include_versions:
                    value["versions"] = [dict(item) for (code, _), item in catalog.versions.items() if code == value["code"]]
                values.append(value)
            entries = [item for value in values for item in [value, *value.get("versions", [])]]
            if include_runtime:
                states = self.operator_runtime_statuses([item["runtime_requirements"] for item in entries])
                for item, state in zip(entries, states):
                    item["dependency_status"] = state
            for item in entries:
                item["runtime_summary"] = {key: item["runtime_requirements"][key] for key in (
                    "driver", "executor", "uses_llm", "resources", "resource_profile", "model", "model_revision",
                    "data_behavior", "limitations",
                ) if key in item["runtime_requirements"]}
                item.pop("runtime_requirements", None)
            return values

    def operator_runtime_statuses(self, requirements):
        from .operator_status import API_STATUS_CACHE
        from .operators.runtime import OperatorRuntime
        runner_url = os.getenv("DATAFORGE_RUNNER_URL", "").rstrip("/")
        if not runner_url:
            return OperatorRuntime().status_batch(requirements)
        token = os.getenv("DATAFORGE_RUNNER_SERVICE_TOKEN", "")
        namespace = (runner_url, hashlib.sha256(token.encode()).hexdigest())
        def fetch(specs):
            import httpx
            response = httpx.post(runner_url + "/internal/operators/status-batch",
                json={"requirements": specs}, headers={"Authorization": "Bearer " + token}, timeout=2)
            response.raise_for_status()
            return response.json()["statuses"]
        return API_STATUS_CACHE.get_many(namespace, requirements, fetch)

    def operator_runtime_status(self, requirements, *, check=False):
        from .operators.runtime import OperatorRuntime
        validate_runtime_requirements(requirements)
        if not requires_external_runtime(requirements):
            return {"status": "ready"}
        runner_url = os.getenv("DATAFORGE_RUNNER_URL")
        if runner_url:
            import httpx
            try:
                response = httpx.post(runner_url.rstrip("/") + "/internal/operators/check",
                    json={"requirements": requirements, "check": check},
                    headers={"Authorization": "Bearer " + os.getenv("DATAFORGE_RUNNER_SERVICE_TOKEN", "")}, timeout=30)
                response.raise_for_status()
                return response.json()
            except Exception:
                return {"status": "unknown", "reason": "Runner 依赖状态不可达"}
        runtime = OperatorRuntime()
        status = runtime.status(requirements)
        if check and status["status"] == "ready":
            try:
                runtime.call(requirements, records=[], action="check", timeout=30)
            except Exception as exc:
                return {"status": "incompatible", "reason": str(exc)}
        return status

    def require_operator_runtime(self, requirements):
        status = self.operator_runtime_status(requirements, check=True)
        if status["status"] != "ready":
            raise ValueError(status.get("reason", "算子依赖尚未就绪"))
        return status

    def operator_candidates(self, definition, output_types, source_node_id=None, source_port="output", *,
                            node_id=None, direction="downstream", include_incompatible=False):
        from .edge_validation import FlowEdgeValidationContext
        from .flow import (_node_ports, _reachable_sink_contexts, _edge, resolve_port_contract,
                           validate_edge_compatibility, FlowEdgeValidationError, input_type_for_node,
                           evaluate_edge_candidate)
        if direction not in {"upstream", "downstream"}:
            raise ValueError("算子发现方向必须为 upstream 或 downstream")
        families = {normalise_output_key(value).split(":")[0] for value in output_types}
        with self.sessions() as session:
            catalog, subflows = load_catalog(session), self._published_subflows(session)
        edge_context = FlowEdgeValidationContext(definition, catalog=catalog, subflows=subflows)
        values = self.list_operator_catalog(surface="advanced-canvas", include_versions=False, include_runtime=False, _catalog=catalog)
        values = [item for item in values if item["enabled"] and item["approved"] and item["status"] == "published"]
        anchor_id = node_id or source_node_id
        source = next((node for node in definition.get("nodes", []) if node["id"] == anchor_id), None)
        if anchor_id and source is None:
            raise ValueError("推荐来源节点不存在")
        if include_incompatible and not source:
            raise ValueError("上下文算子发现必须指定 node_id")
        states = self.operator_runtime_statuses([catalog[item["code"]]["runtime_requirements"] for item in values])
        for item, state in zip(values, states):
            item["dependency_status"] = state
        contexts = {(normalise_output_key(value).split(":")[0], normalise_output_key(value).split(":")[1] if ":" in normalise_output_key(value) else None) for value in output_types}
        if source:
            by_id = {node["id"]: node for node in definition.get("nodes", [])}
            outgoing = {}
            for edge in map(_edge, definition.get("edges", [])):
                outgoing.setdefault(edge["source"], []).append(edge["target"])
            contexts = _reachable_sink_contexts(anchor_id, by_id, outgoing) or contexts
        result = []
        for item in values:
            if not item["enabled"] or not item["approved"] or item["status"] != "published": continue
            if not include_incompatible and item["dependency_status"]["status"] != "ready": continue
            if not include_incompatible and families and "*" not in item["knowledge_types"] and not families.intersection(item["knowledge_types"]): continue
            if not include_incompatible and contexts and item.get("graph_modes") and not {mode for kind, mode in contexts if kind == "graph"}.intersection(item["graph_modes"]): continue
            if include_incompatible:
                candidate_id = "__operator_candidate__"
                while any(node.get("id") == candidate_id for node in definition.get("nodes", [])):
                    candidate_id += "_"
                candidate_node = {"id": candidate_id, "kind": "operator", "node_role": "operator",
                                  "ref": item["code"], "operator_version": item["version"], "params": {}}
                if direction == "downstream":
                    source_ports = _node_ports(source, direction="output", catalog=catalog, subflows=subflows)
                    if source_node_id and not node_id:
                        source_ports = {source_port: source_ports[source_port]} if source_port in source_ports else {}
                    target_ports = item.get("input_ports") or {}
                else:
                    source_ports = item.get("output_ports") or {}
                    target_ports = _node_ports(source, direction="input", catalog=catalog, subflows=subflows)
                attempts = []
                for source_port_id in source_ports:
                    for target_port_id, target_spec in target_ports.items():
                        if str(target_spec.get("binding") or "edge") != "edge":
                            continue
                        edge = ({"source": source["id"], "source_port": source_port_id,
                                 "target": candidate_id, "target_port": target_port_id}
                                if direction == "downstream" else
                                {"source": candidate_id, "source_port": source_port_id,
                                 "target": source["id"], "target_port": target_port_id})
                        attempts.append(evaluate_edge_candidate(
                            definition, edge=edge, catalog=catalog, subflows=subflows,
                            candidate_node=candidate_node,
                            context=edge_context,
                        ))
                compatible = next((attempt for attempt in attempts if attempt["compatible"]), None)
                if compatible is None:
                    compatible = attempts[0] if attempts else {
                        "compatible": False,
                        "reason_code": ("SOURCE_NODE_NO_OUTPUT"
                                        if direction == "downstream" and not source_ports
                                        else "TARGET_NODE_NO_INPUT"),
                        "reason": "当前方向不存在可连接的 Edge 端口",
                        "details": {},
                    }
                compatible.setdefault("graph_warnings", list(edge_context.diagnostics.values()))
                result.append({
                    **item,
                    "compatibility": {**compatible, "direction": direction},
                    "runtime_status": item["dependency_status"],
                    "matching_ports": ([compatible["target_port"]]
                                       if compatible["compatible"] and direction == "downstream"
                                       else [compatible["source_port"]]
                                       if compatible["compatible"] else []),
                })
                continue
            if source:
                source_spec = _node_ports(source, direction="output", catalog=catalog, subflows=subflows).get(source_port)
                target_spec = item["input_ports"].get("input")
                if not source_spec or not target_spec or target_spec.get("binding", "edge") != "edge": continue
                candidate = {"params": dict(source.get("params") or {})}
                try:
                    validate_edge_compatibility(resolve_port_contract({"contexts": contexts, "input_type": input_type_for_node(source_node_id, definition, catalog, subflows)}, source, source_spec),
                        resolve_port_contract({"contexts": contexts}, candidate, target_spec), details={})
                except FlowEdgeValidationError:
                    continue
            result.append({**item, "matching_ports": ["input"] if source else []})
        return result

    def operator_catalog_facets(self) -> dict[str, Any]:
        values = self.list_operator_catalog(include_internal=True, include_runtime=False, include_versions=False)
        return {
            "total": len(values),
            "categories": [{"name": name, "count": sum(item["category"] == name for item in values)} for name in OPERATOR_CATEGORIES],
            "knowledge_types": sorted({kind for item in values for kind in item.get("knowledge_types", [])}),
            "sources": sorted({item["source"] for item in values}),
            "catalog_groups": sorted({item["catalog_group"] for item in values}),
            "exposures": [{"value": value, "label": label, "count": sum(item["exposure"] == value for item in values)} for value, label in (
                ("canvas", "可直接使用"), ("controlled", "受控使用"), ("internal", "系统内部"), ("disabled", "已禁用"))],
            "statuses": sorted({item["status"] for item in values}),
        }

    def operator_catalog_detail(self, code: str) -> dict[str, Any]:
        values = self.list_operator_catalog(include_internal=True)
        value = next((item for item in values if item["code"] == code), None)
        if not value: raise ValueError("算子不存在或没有已发布版本")
        with self.sessions() as session:
            templates = []
            for item in session.scalars(select(KnowledgeFlowTemplate).where(KnowledgeFlowTemplate.status == "active")).all():
                definition = item.definition_json or {}
                if any(node.get("ref") == code for node in definition.get("nodes", [])):
                    templates.append({"id": item.id, "code": item.code, "name": item.name})
        return {**value, "templates": templates}

    @staticmethod
    def _operator_publication_errors(definition: OperatorDefinition, version: OperatorVersion) -> list[str]:
        required = {
            "中文名": definition.display_name_zh, "摘要": definition.summary, "详细说明": definition.description,
            "适用场景": definition.scenarios, "适用知识类型": definition.knowledge_types,
            "输入契约": version.input_ports, "输出契约": version.output_ports,
            "参数说明": version.parameter_docs, "输入样例": version.input_example, "输出样例": version.output_example,
        }
        return [label for label, value in required.items() if value in (None, "", [], {})]

    def publish_operator_version(self, code: str, version_no: int) -> dict[str, Any]:
        with self.sessions() as lookup:
            version = lookup.scalar(select(OperatorVersion).join(OperatorDefinition).where(OperatorDefinition.code == code, OperatorVersion.version_no == version_no))
            definition = lookup.scalar(select(OperatorDefinition).where(OperatorDefinition.id == version.operator_definition_id)) if version else None
            custom = definition and definition.source == "custom"
        if custom:
            from .operator_plugins import OperatorPluginService
            return OperatorPluginService(self).publish(code, version_no)
        with self.sessions.begin() as session:
            definition = session.scalar(select(OperatorDefinition).where(OperatorDefinition.code == code))
            version = session.scalar(select(OperatorVersion).where(OperatorVersion.operator_definition_id == definition.id,
                                                                    OperatorVersion.version_no == version_no)) if definition else None
            if not definition or not version: raise ValueError("算子版本不存在")
            missing = self._operator_publication_errors(definition, version)
            if missing: raise ValueError(f"算子发布元数据不完整：{', '.join(missing)}")
            version.status, version.published_at = "published", utc_now()
            return {"code": code, "version": version_no, "status": "published"}

    def list_prompt_templates(self, *, status: str = "", knowledge_type: str = "") -> list[dict[str, Any]]:
        with self.sessions() as session:
            values = []
            for item in session.scalars(select(PromptTemplate).order_by(PromptTemplate.code)):
                revisions = session.scalars(select(PromptTemplateRevision).where(PromptTemplateRevision.prompt_template_id == item.id).order_by(PromptTemplateRevision.revision_no.desc())).all()
                visible = [rev for rev in revisions if (not status or rev.status == status) and
                           (not knowledge_type or "*" in (rev.knowledge_types or ["*"]) or knowledge_type in (rev.knowledge_types or []))]
                if (status or knowledge_type) and not visible:
                    continue
                values.append({"id": item.id, "code": item.code, "name": item.name, "status": item.status,
                               "revisions": [{"id": rev.id, "revision": rev.revision_no, "status": rev.status,
                                               "knowledge_types": list(rev.knowledge_types or ["*"]),
                                               "input_schema": rev.input_schema, "output_schema": rev.output_schema,
                                               "published_at": rev.published_at.isoformat() if rev.published_at else None} for rev in visible]})
            return values

    def list_quality_profiles(self, *, status: str = "", knowledge_type: str = "") -> list[dict[str, Any]]:
        with self.sessions() as session:
            values = []
            for item in session.scalars(select(QualityProfile).order_by(QualityProfile.code)):
                revisions = session.scalars(select(QualityProfileRevision).where(QualityProfileRevision.quality_profile_id == item.id).order_by(QualityProfileRevision.revision_no.desc())).all()
                visible = [rev for rev in revisions if (not status or rev.status == status) and
                           (not knowledge_type or "*" in (rev.knowledge_types or ["*"]) or knowledge_type in (rev.knowledge_types or []))]
                if (status or knowledge_type) and not visible:
                    continue
                values.append({"id": item.id, "code": item.code, "name": item.name, "status": item.status,
                               "revisions": [{"id": rev.id, "revision": rev.revision_no, "status": rev.status,
                                               "knowledge_types": list(rev.knowledge_types or ["*"]),
                                               "rules": rev.rules_json, "published_at": rev.published_at.isoformat() if rev.published_at else None} for rev in visible]})
            return values

    def create_prompt_template(self, code: str, name: str, body: str, input_schema: dict[str, Any], output_schema: dict[str, Any], knowledge_types: list[str] | None = None) -> dict[str, Any]:
        if not code.strip() or not name.strip() or not body.strip():
            raise ValueError("Prompt 编码、名称和模板内容不能为空")
        with self.sessions.begin() as session:
            if session.scalar(select(PromptTemplate).where(PromptTemplate.code == code.strip())):
                raise ValueError("Prompt 编码已存在")
            prompt = PromptTemplate(id=new_id("prompt"), code=code.strip(), name=name.strip())
            session.add(prompt); session.flush()
            scopes = sorted({str(value).strip() for value in (knowledge_types or ["*"]) if str(value).strip()})
            if not scopes: raise ValueError("Prompt Template 必须至少适用于一种知识类型")
            revision = PromptTemplateRevision(id=new_id("promptrev"), prompt_template_id=prompt.id, revision_no=1,
                                              body=body, input_schema=input_schema, output_schema=output_schema,
                                              knowledge_types=scopes)
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

    def create_quality_profile(self, code: str, name: str, rules: dict[str, Any], knowledge_types: list[str] | None = None) -> dict[str, Any]:
        if not code.strip() or not name.strip() or not isinstance(rules, dict):
            raise ValueError("Quality Profile 编码、名称和规则不能为空")
        with self.sessions.begin() as session:
            if session.scalar(select(QualityProfile).where(QualityProfile.code == code.strip())):
                raise ValueError("Quality Profile 编码已存在")
            profile = QualityProfile(id=new_id("quality"), code=code.strip(), name=name.strip())
            session.add(profile); session.flush()
            scopes = sorted({str(value).strip() for value in (knowledge_types or ["*"]) if str(value).strip()})
            if not scopes: raise ValueError("Quality Profile 必须至少适用于一种知识类型")
            revision = QualityProfileRevision(id=new_id("qualityrev"), quality_profile_id=profile.id, revision_no=1,
                                              rules_json=rules, knowledge_types=scopes)
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

    def list_subflows(self):
        return SubflowService(self).inventory()

    def subflow_revision_detail(self, subflow_id, revision_no):
        return SubflowService(self).detail(subflow_id, revision_no)

    def subflow_revisions(self, subflow_id):
        return SubflowService(self).revisions(subflow_id)

    def subflow_references(self, subflow_id, revision_no):
        return SubflowService(self).references(subflow_id, revision_no)

    def create_subflow(self, **kwargs):
        return SubflowService(self).create(**kwargs)

    def copy_subflow_draft(self, subflow_id, revision_no):
        return SubflowService(self).copy(subflow_id, revision_no)

    def update_subflow_draft(self, subflow_id, revision_no, definition, description, input_contract, output_contract):
        return SubflowService(self).save(subflow_id, revision_no, definition, description, input_contract, output_contract)

    def validate_subflow_draft(self, subflow_id, revision_no):
        return SubflowService(self).save(subflow_id, revision_no, None, '', None, None, check=True)

    def publish_subflow_draft(self, subflow_id, revision_no):
        return SubflowService(self).save(subflow_id, revision_no, None, '', None, None, publish=True)

    def execution_snapshot_detail(self, snapshot_id: str) -> dict[str, Any]:
        with self.sessions() as session:
            value = session.get(FlowExecutionSnapshot, snapshot_id)
            if not value:
                raise ValueError("执行快照不存在")
            return {"id": value.id, "knowledge_flow_template_revision_id": value.knowledge_flow_template_revision_id,
                    **technical_projection(value.compiled_definition_json, load_catalog(session)),
                    "compiled_definition": value.compiled_definition_json, "dependencies": value.dependency_json,
                    "checksum": value.checksum, "status": value.status, "created_at": value.created_at.isoformat()}

    def list_flow_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.sessions() as session:
            values = []
            for item in session.scalars(select(FlowRun).order_by(FlowRun.created_at.desc()).limit(limit)):
                debug_input = session.get(DebugRunInputSnapshot, item.debug_input_snapshot_id) if item.debug_input_snapshot_id else None
                template = session.get(KnowledgeFlowTemplate, debug_input.knowledge_flow_template_id) if debug_input else None
                values.append({
                    "id": item.id, "knowledge_job_id": item.knowledge_job_id,
                    "source_preparation_job_id": item.source_preparation_job_id,
                    "debug_input_snapshot_id": item.debug_input_snapshot_id,
                    "execution_snapshot_id": item.execution_snapshot_id,
                    "parent_flow_run_id": item.parent_flow_run_id, "run_mode": item.run_mode,
                    "start_node_id": item.start_node_id, "status": item.status, "error": item.error,
                    "template_id": template.id if template else None, "template_name": template.name if template else None,
                    "template_revision_id": debug_input.knowledge_flow_template_revision_id if debug_input else None,
                    "created_at": item.created_at.isoformat(),
                    "completed_at": item.completed_at.isoformat() if item.completed_at else None,
                })
            return values

    def flow_run_is_debug(self, flow_run_id: str) -> bool:
        with self.sessions() as session:
            run = session.get(FlowRun, flow_run_id)
            return bool(run and run.debug_input_snapshot_id)

    def audit(self, session: Session, action: str, resource_type: str, resource_id: str, payload: dict[str, Any] | None = None) -> None:
        session.add(AuditEvent(
            id=new_id("audit"), actor="admin", action=action, resource_type=resource_type,
            resource_id=resource_id, payload_json=payload or {},
        ))

    @staticmethod
    def _library_payload(item: DocumentLibrary) -> dict[str, Any]:
        return {"id": item.id, "code": item.code, "name": item.name, "description": item.description,
                "status": item.status, "origin_type": item.origin_type, "origin_state": item.origin_state,
                "updated_at": item.updated_at.isoformat()}

    @staticmethod
    def _source_payload(source: Source, version: SourceVersion | None = None) -> dict[str, Any]:
        return {
            "id": source.id, "document_library_id": source.document_library_id, "name": source.name,
            "original_filename": version.original_filename if version else "", "relative_path": source.relative_path,
            "directory_path": source.directory_path, "source_kind": source.source_kind,
            "status": source.status, "current_version_id": source.current_version_id,
            "metadata": source.metadata_json, "updated_at": source.updated_at.isoformat(),
            "version": None if not version else {"id": version.id, "version_no": version.version_no,
                "blob_uri": version.blob_uri, "sha256": version.sha256, "size_bytes": version.size_bytes,
                "media_type": version.media_type, "original_filename": version.original_filename,
                "activation_no": version.activation_no, "status": version.status,
                "extraction_status": version.extraction_status, "error": version.extraction_error,
                "preparation_status": version.preparation_status, "review_status": version.review_status,
                "current_review_snapshot_id": version.current_review_snapshot_id,
                "active_chunk_set_id": version.active_chunk_set_id,
                "candidate_chunk_set_id": version.candidate_chunk_set_id},
        }

    def list_document_libraries(self, keyword: str = "", status: str | None = None) -> list[dict[str, Any]]:
        with self.sessions() as session:
            query = select(DocumentLibrary).order_by(DocumentLibrary.updated_at.desc())
            if keyword:
                query = query.where((DocumentLibrary.name.contains(keyword)) | (DocumentLibrary.code.contains(keyword)))
            if status:
                query = query.where(DocumentLibrary.status == status)
            return [self._library_payload(item) for item in session.scalars(query)]

    def create_document_library(self, name: str, description: str = "", *, code: str | None = None) -> dict[str, Any]:
        name = name.strip()
        if not name:
            raise ValueError("文档库名称不能为空")
        with self.sessions.begin() as session:
            resolved_code = str(code or "").strip()
            if resolved_code:
                if session.scalar(select(DocumentLibrary.id).where(DocumentLibrary.code == resolved_code)):
                    raise ValueError("文档库编码已存在")
            else:
                date_part = utc_now().strftime("%Y%m%d")
                for _ in range(32):
                    resolved_code = f"DL-{date_part}-{secrets.token_hex(3).upper()}"
                    if not session.scalar(select(DocumentLibrary.id).where(DocumentLibrary.code == resolved_code)):
                        break
                else:
                    raise ValueError("文档库编码生成失败，请稍后重试")
            item = DocumentLibrary(id=new_id("dl"), code=resolved_code, name=name, description=description.strip())
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
        self, *, library_id: str, name: str, filename: str, blob_uri: str, sha256: str,
        size_bytes: int, media_type: str, metadata: dict[str, Any] | None = None, relative_path: str | None = None,
    ) -> dict[str, Any]:
        if not name.strip() or size_bytes <= 0:
            raise ValueError("文件名称不能为空且文件必须非空")
        if blob_uri != f"blob://{sha256}" or not re.fullmatch(r"[0-9a-f]{64}", sha256):
            raise ValueError("Blob URI 与 SHA-256 内容身份不一致")
        with self.sessions.begin() as session:
            if not session.get(DocumentLibrary, library_id):
                raise ValueError("文档库不存在")
            relative_path, directory_path = normalise_relative_path(relative_path or filename)
            source = Source(id=new_id("src"), document_library_id=library_id, name=name.strip(),
                            relative_path=relative_path, relative_path_hash=relative_path_hash(relative_path),
                            directory_path=directory_path, directory_path_hash=relative_path_hash(directory_path), metadata_json=metadata or {})
            # SQLAlchemy has no ORM relationship that expresses the child-row
            # dependencies below.  Persist the parent explicitly so MySQL never
            # flushes a library member before its Source exists.
            session.add(source)
            session.flush()
            version = SourceVersion(
                id=new_id("srcv"), source_id=source.id, version_no=1, blob_uri=blob_uri,
                sha256=sha256, size_bytes=size_bytes, media_type=media_type, original_filename=filename,
            )
            source.current_version_id = version.id
            session.add_all([version, DocumentLibraryMember(id=new_id("dlm"), document_library_id=library_id, source_id=source.id)])
            session.flush(); self._enqueue_source_preparation(session, version)
            self.audit(session, "source.uploaded", "source", source.id, {"source_version_id": version.id})
            session.flush()
            return {**self._source_payload(source, version), "version_action": "created"}

    def replace_source(
        self, *, source_id: str, filename: str, blob_uri: str, sha256: str, size_bytes: int, media_type: str,
    ) -> dict[str, Any]:
        if blob_uri != f"blob://{sha256}" or not re.fullmatch(r"[0-9a-f]{64}", sha256):
            raise ValueError("Blob URI 与 SHA-256 内容身份不一致")
        with self.sessions.begin() as session:
            source = session.scalar(select(Source).where(Source.id == source_id).with_for_update())
            if not source or source.status == "deleted":
                raise ValueError("待替换文件不存在或已删除")
            current = session.get(SourceVersion, source.current_version_id)
            if not current:
                raise ValueError("待替换文件缺少当前版本")
            if Path(filename).suffix.lower() != Path(current.original_filename).suffix.lower():
                raise ValueError("替换文件必须与当前版本保持相同格式")
            existing = session.scalar(select(SourceVersion).where(
                SourceVersion.source_id == source.id, SourceVersion.sha256 == sha256,
            ))
            if existing:
                payload = self._source_payload(source, current)
                if existing.id == current.id:
                    return {**payload, "version_action": "unchanged"}
                return {
                    **payload, "version_action": "confirmation_required",
                    "code": "SOURCE_VERSION_REACTIVATION_REQUIRED",
                    "expected_current_version_id": current.id,
                    "reactivation_version": self._source_payload(source, existing)["version"],
                }
            next_path = "/".join(filter(None, (source.directory_path, filename)))
            conflict = session.scalar(select(Source.id).where(
                Source.document_library_id == source.document_library_id,
                Source.relative_path_hash == relative_path_hash(next_path),
                Source.relative_path == next_path,
                Source.id != source.id,
                Source.status.not_in(("deleted", "deleting")),
            ))
            if conflict:
                raise ValueError("同目录下已存在同名资料")
            if current:
                current.status = "superseded"
            version_no = (session.scalar(select(func.max(SourceVersion.version_no)).where(SourceVersion.source_id == source.id)) or 0) + 1
            version = SourceVersion(
                id=new_id("srcv"), source_id=source.id, version_no=version_no, blob_uri=blob_uri,
                sha256=sha256, size_bytes=size_bytes, media_type=media_type, original_filename=filename,
            )
            source.current_version_id, source.status = version.id, "uploaded"
            source.relative_path, source.relative_path_hash = next_path, relative_path_hash(next_path)
            session.add(version)
            session.flush(); self._enqueue_source_preparation(session, version)
            document_library = session.get(DocumentLibrary, source.document_library_id)
            if document_library and document_library.origin_type == "central_import":
                document_library.origin_state = "forked"
            dependent_libraries = session.scalars(select(KnowledgeLibrary).join(
                KnowledgeItem, KnowledgeItem.knowledge_library_id == KnowledgeLibrary.id
            ).join(KnowledgeItemSource, KnowledgeItemSource.knowledge_item_id == KnowledgeItem.id).join(
                SourceVersion, SourceVersion.id == KnowledgeItemSource.source_version_id
            ).where(SourceVersion.source_id == source.id, KnowledgeLibrary.origin_type == "central_import")).all()
            for library in dependent_libraries: library.origin_state = "forked"
            # The new source version is processed asynchronously.  Existing
            # formal evidence is retained until its result replaces it; this
            # prevents a brief no-knowledge window for multi-source graph data.
            self.audit(session, "source.replaced", "source", source.id, {"source_version_id": version.id})
            session.flush()
            return {**self._source_payload(source, version), "version_action": "created"}

    def reactivate_source_version(
        self, *, source_id: str, version_id: str, expected_current_version_id: str,
    ) -> dict[str, Any]:
        with self.sessions.begin() as session:
            source = session.scalar(select(Source).where(Source.id == source_id).with_for_update())
            target = session.get(SourceVersion, version_id)
            if not source or source.status in {"deleted", "deleting"} or not target or target.source_id != source_id:
                raise ValueError("待重新启用的文件版本不存在")
            if source.current_version_id != expected_current_version_id:
                raise ValueError("SOURCE_VERSION_REACTIVATION_STALE")
            current = session.get(SourceVersion, source.current_version_id)
            if not current:
                raise ValueError("当前文件版本不存在")
            if target.id == current.id:
                return {**self._source_payload(source, current), "version_action": "unchanged"}
            if Path(target.original_filename).suffix.lower() != Path(current.original_filename).suffix.lower():
                raise ValueError("历史版本格式与当前版本不一致")
            next_path = "/".join(filter(None, (source.directory_path, target.original_filename)))
            conflict = session.scalar(select(Source.id).where(
                Source.document_library_id == source.document_library_id,
                Source.relative_path_hash == relative_path_hash(next_path),
                Source.relative_path == next_path,
                Source.id != source.id,
                Source.status.not_in(("deleted", "deleting")),
            ))
            if conflict:
                raise ValueError("同目录下已存在历史版本文件名对应的资料")
            current.status = "superseded"
            target.status, target.activation_no = "active", int(target.activation_no or 0) + 1
            source.current_version_id, source.status = target.id, "uploaded"
            source.relative_path, source.relative_path_hash = next_path, relative_path_hash(next_path)
            if target.review_status == "approved" and target.current_review_snapshot_id:
                self._queue_review_dispatch(session, target.current_review_snapshot_id, target.activation_no)
            self.audit(session, "source.version_reactivated", "source", source.id, {
                "previous_version_id": current.id, "source_version_id": target.id,
                "activation_no": target.activation_no,
            })
            session.flush()
            return {
                **self._source_payload(source, target), "version_action": "reactivated",
                "notice": f"已重新启用 v{target.version_no}，未创建新版本",
            }

    @staticmethod
    def _create_candidate_chunk_set(session: Session, version: SourceVersion,
                                    job: SourcePreparationJob) -> SourceChunkSet:
        item = SourceChunkSet(
            id=new_id("chunkset"), source_version_id=version.id,
            source_preparation_job_id=job.id, execution_snapshot_id=job.execution_snapshot_id,
            preparation_revision=job.preparation_revision, status="candidate",
            chunk_count=0, metrics_json={},
        )
        session.add(item); session.flush()
        version.candidate_chunk_set_id = item.id
        return item

    def _enqueue_source_preparation(self, session: Session, version: SourceVersion) -> SourcePreparationJob:
        existing = session.scalar(select(SourcePreparationJob).where(
            SourcePreparationJob.source_version_id == version.id,
            SourcePreparationJob.preparation_revision == 1,
        ))
        if existing:
            return existing
        template = session.scalar(select(KnowledgeFlowTemplate).where(
            KnowledgeFlowTemplate.code == "source-preparation",
            KnowledgeFlowTemplate.purpose == "source_preparation",
            KnowledgeFlowTemplate.status == "active",
        ))
        revision = session.scalar(select(KnowledgeFlowTemplateRevision).where(
            KnowledgeFlowTemplateRevision.knowledge_flow_template_id == template.id,
            KnowledgeFlowTemplateRevision.status == "published",
        ).order_by(KnowledgeFlowTemplateRevision.revision_no.desc())) if template else None
        if not revision:
            raise ValueError("Source Preparation 系统流程未完成初始化")
        self._published_execution_snapshot(session, revision)
        job = SourcePreparationJob(
            id=new_id("prep"), source_version_id=version.id, preparation_revision=1,
            execution_snapshot_id=revision.execution_snapshot_id, status="queued",
        )
        version.preparation_status, version.review_status = "queued", "pending"
        session.add(job); session.flush()
        chunk_set = self._create_candidate_chunk_set(session, version, job)
        self.audit(session, "source_preparation.queued", "source_preparation_job", job.id,
                   {"source_version_id": version.id, "chunk_set_id": chunk_set.id})
        return job

    def retry_source_preparation(self, source_version_id: str) -> dict[str, Any]:
        with self.sessions.begin() as session:
            version = self._lock_source_review_version(session, source_version_id)
            if not version: raise ValueError("文件版本不存在")
            job = session.scalar(select(SourcePreparationJob).where(
                SourcePreparationJob.source_version_id == version.id,
            ).order_by(SourcePreparationJob.preparation_revision.desc()))
            if job and job.status in {"queued", "running"}:
                return {"id": job.id, "status": job.status, "idempotent": True}
            if not job or job.status != "failed":
                raise ValueError("只有失败的 Source Preparation 可以 Retry；成功结果请使用 Rechunk")
            revision = (job.preparation_revision if job else 0) + 1
            retry = SourcePreparationJob(
                id=new_id("prep"), source_version_id=version.id, preparation_revision=revision,
                execution_snapshot_id=job.execution_snapshot_id, status="queued",
            )
            version.preparation_status, version.review_status = "queued", "pending"
            version.extraction_status, version.extraction_error = "pending", None
            session.add(retry); session.flush()
            chunk_set = self._create_candidate_chunk_set(session, version, retry)
            return {"id": retry.id, "status": retry.status, "chunk_set_id": chunk_set.id,
                    "execution_snapshot_id": retry.execution_snapshot_id}

    def rechunk_source_version(self, source_version_id: str, execution_snapshot_id: str | None = None) -> dict[str, Any]:
        with self.sessions.begin() as session:
            version = self._lock_source_review_version(session, source_version_id)
            if not version:
                raise ValueError("文件版本不存在")
            current_candidate = session.get(SourceChunkSet, version.candidate_chunk_set_id) if version.candidate_chunk_set_id else None
            if current_candidate:
                current_job = session.get(SourcePreparationJob, current_candidate.source_preparation_job_id) if current_candidate.source_preparation_job_id else None
                if current_candidate.status == "candidate" and current_job and current_job.status in {"queued", "running"}:
                    raise ReviewGateError("CANDIDATE_IN_PROGRESS", "候选分块仍在生成，请等待完成后再重新分块",
                                          source_version_id=version.id)
                if current_candidate.status == "candidate":
                    current_candidate.status = "superseded"
            template = session.scalar(select(KnowledgeFlowTemplate).where(
                KnowledgeFlowTemplate.code == "source-preparation", KnowledgeFlowTemplate.purpose == "source_preparation"
            ))
            revisions = select(KnowledgeFlowTemplateRevision).where(
                KnowledgeFlowTemplateRevision.knowledge_flow_template_id == template.id,
                KnowledgeFlowTemplateRevision.status == "published",
                KnowledgeFlowTemplateRevision.purpose == "source_preparation",
            ) if template else None
            flow_revision = None
            if revisions is not None:
                if execution_snapshot_id:
                    flow_revision = session.scalar(revisions.where(
                        KnowledgeFlowTemplateRevision.execution_snapshot_id == execution_snapshot_id
                    ))
                else:
                    flow_revision = session.scalar(revisions.order_by(KnowledgeFlowTemplateRevision.revision_no.desc()))
            if not flow_revision or not flow_revision.execution_snapshot_id:
                raise ValueError("Source Preparation ExecutionSnapshot 不存在或未发布")
            preparation_revision = int(session.scalar(select(func.max(SourcePreparationJob.preparation_revision)).where(
                SourcePreparationJob.source_version_id == version.id
            )) or 0) + 1
            job = SourcePreparationJob(
                id=new_id("prep"), source_version_id=version.id, preparation_revision=preparation_revision,
                execution_snapshot_id=flow_revision.execution_snapshot_id, status="queued",
            )
            session.add(job); session.flush()
            chunk_set = self._create_candidate_chunk_set(session, version, job)
            version.preparation_status, version.review_status = "queued", "pending"
            version.extraction_status, version.extraction_error = "pending", None
            self.audit(session, "source_preparation.rechunk_queued", "source_preparation_job", job.id,
                       {"chunk_set_id": chunk_set.id, "execution_snapshot_id": job.execution_snapshot_id})
            return {"id": job.id, "status": job.status, "chunk_set_id": chunk_set.id,
                    "execution_snapshot_id": job.execution_snapshot_id}

    def claim_source_preparation_job(self, owner: str) -> SourcePreparationJob | None:
        with self.sessions.begin() as session:
            now = utc_now()
            job = session.scalar(select(SourcePreparationJob).where(
                or_(
                    SourcePreparationJob.status == "queued",
                    (SourcePreparationJob.status == "running") & (SourcePreparationJob.lease_expires_at < now),
                )
            ).order_by(SourcePreparationJob.created_at).with_for_update(skip_locked=True))
            if not job: return None
            job.status, job.attempt_count = "running", job.attempt_count + 1
            job.lease_owner, job.lease_expires_at = owner, now + WORK_LEASE_DURATION
            version = session.get(SourceVersion, job.source_version_id)
            if version: version.preparation_status = "running"
            return job

    def renew_source_preparation_lease(self, job_id: str, owner: str) -> bool:
        with self.sessions.begin() as session:
            job = session.get(SourcePreparationJob, job_id)
            if not job or job.status != "running" or job.lease_owner != owner: return False
            job.lease_expires_at = utc_now() + WORK_LEASE_DURATION; return True

    def source_preparation_context(self, job_id: str) -> dict[str, Any]:
        with self.sessions() as session:
            job = session.get(SourcePreparationJob, job_id)
            if not job: raise ValueError("Source Preparation 任务不存在")
            version = session.get(SourceVersion, job.source_version_id)
            source = session.get(Source, version.source_id) if version else None
            snapshot = session.get(FlowExecutionSnapshot, job.execution_snapshot_id) if job.execution_snapshot_id else None
            chunk_set = session.scalar(select(SourceChunkSet).where(SourceChunkSet.source_preparation_job_id == job.id))
            if not version or not source or not snapshot or not chunk_set: raise ValueError("Source Preparation 任务上下文不完整")
            return {"job": job, "version": version, "source": source, "chunk_set": chunk_set,
                    "definition": snapshot.compiled_definition_json}

    def start_source_preparation_flow_run(self, job_id: str) -> dict[str, Any]:
        with self.sessions.begin() as session:
            job = session.get(SourcePreparationJob, job_id)
            if not job or job.status != "running": raise ValueError("Source Preparation 任务未处于运行状态")
            run = FlowRun(
                id=new_id("flowrun"), knowledge_job_id=None, source_preparation_job_id=job.id,
                execution_snapshot_id=job.execution_snapshot_id, run_mode="full", requested_by="system",
            )
            session.add(run); session.flush()
            chunk_set = session.scalar(select(SourceChunkSet).where(SourceChunkSet.source_preparation_job_id == job.id))
            if chunk_set: chunk_set.flow_run_id = run.id
            return {"id": run.id, "status": run.status}

    def finish_source_preparation(self, job_id: str, error: str | None = None) -> dict[str, Any]:
        with self.sessions.begin() as session:
            job = session.get(SourcePreparationJob, job_id)
            if not job: raise ValueError("Source Preparation 任务不存在")
            version = self._lock_source_review_version(session, job.source_version_id)
            chunk_set = session.scalar(select(SourceChunkSet).where(SourceChunkSet.source_preparation_job_id == job.id))
            if error:
                job.status, job.error = "failed", error
                if chunk_set: chunk_set.status = "failed"
                if version:
                    version.preparation_status, version.review_status = "failed", "pending"
                    version.extraction_status, version.extraction_error = "failed", error
            else:
                chunks = self._chunk_set_chunks(session, chunk_set.id) if chunk_set else []
                if not chunks:
                    raise ValueError("Source Preparation 未生成可审核文档块")
                job.status, job.error = "completed", None
                if version:
                    version.preparation_status, version.review_status = "completed", "pending"
                    version.extraction_status, version.extraction_error = "completed", None
                if chunk_set:
                    lengths = sorted(len(item.content) for item in chunks)
                    percentile = lambda fraction: lengths[min(len(lengths) - 1, round((len(lengths) - 1) * fraction))]
                    chunk_set.chunk_count = len(chunks)
                    chunk_set.content_digest = hashlib.sha256(json.dumps(
                        [{"id": item.source_chunk_id, "hash": item.content_hash} for item in chunks],
                        ensure_ascii=False, separators=(",", ":"),
                    ).encode("utf-8")).hexdigest()
                    chunk_set.metrics_json = {
                        "chunk_count": len(chunks), "avg_chars": round(sum(lengths) / len(lengths), 2),
                        "p50_chars": percentile(0.5), "p95_chars": percentile(0.95),
                        "min_chars": lengths[0], "max_chars": lengths[-1],
                        "tiny_chunk_count": sum(1 for value in lengths if value < 100),
                        "hard_cut_count": sum(1 for item in chunks if (item.anchor_json or {}).get("hard_cut")),
                        "page_crossing_count": sum(1 for item in chunks if (item.anchor_json or {}).get("page_start") != (item.anchor_json or {}).get("page_end") and (item.anchor_json or {}).get("page_end") is not None),
                    }
            job.lease_owner, job.lease_expires_at = None, None
            self.audit(session, f"source_preparation.{job.status}", "source_preparation_job", job.id,
                       {"error": error} if error else {})
            return {"id": job.id, "source_version_id": job.source_version_id, "status": job.status, "error": job.error}

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
            query = select(Source).outerjoin(
                SourceVersion, SourceVersion.id == Source.current_version_id,
            ).order_by(Source.updated_at.desc())
            if library_id:
                query = query.where(Source.document_library_id == library_id)
            if keyword:
                query = query.where((Source.name.contains(keyword)) | (SourceVersion.original_filename.contains(keyword)))
            if status:
                query = query.where(Source.status == status)
            items = []
            for source in session.scalars(query):
                version = session.get(SourceVersion, source.current_version_id) if source.current_version_id else None
                payload = self._source_payload(source, version)
                target = self._review_target_chunk_set(session, version) if version else None
                if payload["version"] is not None:
                    payload["version"]["chunk_count"] = target.chunk_count if target else 0
                items.append(payload)
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
            query = select(Source).outerjoin(
                SourceVersion, SourceVersion.id == Source.current_version_id,
            ).where(Source.document_library_id == library_id).order_by(Source.updated_at.desc())
            if path is not None:
                normalized, _ = normalise_relative_path(f"{path}/placeholder") if path else ("", "")
                directory = normalized.rsplit("/", 1)[0] if normalized else ""
                query = query.where(
                    Source.directory_path_hash == relative_path_hash(directory),
                    Source.directory_path == directory,
                )
            if keyword:
                query = query.where((Source.name.contains(keyword)) | (SourceVersion.original_filename.contains(keyword)) | (Source.relative_path.contains(keyword)))
            if status:
                query = query.where(Source.status == status)
            if file_type:
                query = query.where(SourceVersion.original_filename.ilike(f"%.{file_type.lstrip('.').lower()}"))
            total = int(session.scalar(select(func.count()).select_from(query.subquery())) or 0)
            rows = session.scalars(query.offset((page - 1) * page_size).limit(page_size)).all()
            items = []
            for row in rows:
                version = session.get(SourceVersion, row.current_version_id) if row.current_version_id else None
                payload = self._source_payload(row, version)
                target = self._review_target_chunk_set(session, version) if version else None
                if payload["version"] is not None:
                    payload["version"]["chunk_count"] = target.chunk_count if target else 0
                items.append(payload)
            return {"items": items,
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
            return [{"id": item.id, "version_no": item.version_no, "sha256": item.sha256,
                     "blob_uri": item.blob_uri, "media_type": item.media_type,
                     "original_filename": item.original_filename, "activation_no": item.activation_no,
                     "status": item.status, "size_bytes": item.size_bytes, "extraction_status": item.extraction_status,
                     "preparation_status": item.preparation_status, "review_status": item.review_status,
                     "current_review_snapshot_id": item.current_review_snapshot_id,
                     "error": item.extraction_error, "created_at": item.created_at.isoformat()} for item in values]

    def bind_document_library_template(self, document_library_id: str, template_id: str) -> dict[str, Any]:
        """Attach a published template and lazily create its stable result libraries."""
        with self.sessions.begin() as session:
            document_library = session.get(DocumentLibrary, document_library_id)
            if not document_library or document_library.status != "active":
                raise ValueError("文档库不存在或不可用")
            binding = self._bind_document_library_template(session, document_library, template_id)
            session.flush()
            return self._document_binding_payload(session, binding)

    def bind_document_library_templates(self, document_library_id: str, template_ids: list[str]) -> list[dict[str, Any]]:
        """Atomically attach several published templates to one document library."""
        identifiers = list(dict.fromkeys(template_id for template_id in template_ids if template_id))
        if not identifiers:
            raise ValueError("至少选择一个知识模板")
        with self.sessions.begin() as session:
            document_library = session.get(DocumentLibrary, document_library_id)
            if not document_library or document_library.status != "active":
                raise ValueError("文档库不存在或不可用")
            bindings = [
                self._bind_document_library_template(session, document_library, template_id)
                for template_id in identifiers
            ]
            session.flush()
            return [self._document_binding_payload(session, binding) for binding in bindings]

    def _bind_document_library_template(self, session: Session, document_library: DocumentLibrary,
                                        template_id: str) -> DocumentLibraryTemplateBinding:
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
        for snapshot_id in session.scalars(select(SourceVersion.current_review_snapshot_id).join(
            Source, Source.id == SourceVersion.source_id,
        ).where(
            Source.document_library_id == document_library.id,
            Source.current_version_id == SourceVersion.id,
            Source.status == "uploaded",
            SourceVersion.review_status == "approved",
            SourceVersion.current_review_snapshot_id.is_not(None),
        )):
            self._queue_review_dispatch(session, snapshot_id)
        self.audit(session, "document_library.template_bound", "document_library_template_binding", binding.id,
                   {"template_id": template.id})
        return binding

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
                                         template: KnowledgeFlowTemplate, binding: DocumentLibraryTemplateBinding,
                                         *, recreate_deleted: bool = False) -> None:
        """Create missing result libraries and optionally replace completed deletions.

        A deleted automatic result library remains attached to its binding for
        auditability.  Listing the binding must not resurrect it; only an
        explicit new document-processing request may create its replacement.
        """
        existing = {item.output_key: item for item in session.scalars(select(DocumentLibraryTemplateOutput).where(
            DocumentLibraryTemplateOutput.document_library_template_binding_id == binding.id,
        ))}
        if recreate_deleted:
            deleting = [item for item in existing.values() if (library := session.get(
                KnowledgeLibrary, item.knowledge_library_id,
            )) and library.status == "deleting"]
            if deleting:
                raise ValueError("自动结果知识库正在清理，请先等待清理完成或重试删除任务")
        _, flow_revision = self._published_template_revision(session, template.id)
        for raw_output in self._revision_output_types(session, flow_revision):
            output_key = normalise_output_key(raw_output)
            knowledge_type, graph_mode = output_contract(output_key)
            output = existing.get(output_key)
            if output:
                library = session.get(KnowledgeLibrary, output.knowledge_library_id)
                if library and library.status != "deleted":
                    continue
                if not recreate_deleted:
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
            if output:
                output.knowledge_library_id = library.id
            else:
                session.add(DocumentLibraryTemplateOutput(id=new_id("docout"), document_library_template_binding_id=binding.id,
                                                          knowledge_type=knowledge_type, output_key=output_key,
                                                          graph_mode=graph_mode, knowledge_library_id=library.id))

    def _binding_pending_versions(self, session: Session, binding: DocumentLibraryTemplateBinding,
                                  revision: KnowledgeFlowTemplateRevision) -> list[str]:
        active_pairs = list(session.execute(select(
            Source.current_version_id, SourceVersion.current_review_snapshot_id, SourceVersion.activation_no,
        ).join(
            SourceVersion, SourceVersion.id == Source.current_version_id,
        ).where(
            Source.document_library_id == binding.document_library_id, Source.status == "uploaded",
            Source.current_version_id.is_not(None),
            SourceVersion.review_status == "approved",
            SourceVersion.current_review_snapshot_id.is_not(None),
        )).all())
        processed_pairs = set(session.execute(select(
            DocumentLibraryProcessingRecord.source_version_id,
            DocumentLibraryProcessingRecord.source_review_snapshot_id,
            DocumentLibraryProcessingRecord.activation_no,
        ).where(
            DocumentLibraryProcessingRecord.document_library_template_binding_id == binding.id,
            DocumentLibraryProcessingRecord.knowledge_flow_template_revision_id == revision.id,
        )).all())
        baseline_versions = set(session.scalars(select(DocumentLibraryProcessingBaseline.source_version_id).where(
            DocumentLibraryProcessingBaseline.document_library_template_binding_id == binding.id,
            DocumentLibraryProcessingBaseline.knowledge_flow_template_revision_id == revision.id,
            DocumentLibraryProcessingBaseline.last_success_status == "completed",
        )))
        in_flight_pairs: set[tuple[str, str, int]] = set()
        for job in session.scalars(select(KnowledgeJob).where(
            KnowledgeJob.document_library_template_binding_id == binding.id,
            KnowledgeJob.knowledge_flow_template_revision_id == revision.id,
            KnowledgeJob.status.in_(("queued", "running")),
        )):
            for item in session.scalars(select(KnowledgeJobReviewInput).where(
                KnowledgeJobReviewInput.knowledge_job_id == job.id,
            )):
                in_flight_pairs.add((item.source_version_id, item.source_review_snapshot_id, item.activation_no))
        # A published template revision supersedes prior successful records, so
        # all current versions become pending once; queued/running jobs still
        # suppress duplicate dispatch before that run succeeds.
        candidates = active_pairs if binding.last_successful_revision_id != revision.id else [
            item for item in active_pairs
            if item not in processed_pairs and item[0] not in baseline_versions
        ]
        return [version_id for version_id, snapshot_id, activation_no in candidates
                if (version_id, snapshot_id, activation_no) not in in_flight_pairs]

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
                "outputs": [{"knowledge_type": item.knowledge_type, "output_key": item.output_key, "state": library.status,
                             **({"knowledge_library": self._knowledge_library_payload(library)} if library.status != "deleted" else {})}
                            for item, library in outputs],
                "pending_file_count": len(self._binding_pending_versions(session, binding, revision)),
                "latest_job": self.job_payload(latest_job) if latest_job else None}

    def list_document_library_template_bindings(self, document_library_id: str) -> list[dict[str, Any]]:
        with self.sessions.begin() as session:
            if not session.get(DocumentLibrary, document_library_id):
                raise ValueError("文档库不存在")
            return [self._document_binding_payload(session, item) for item in session.scalars(select(DocumentLibraryTemplateBinding).where(
                DocumentLibraryTemplateBinding.document_library_id == document_library_id,
                DocumentLibraryTemplateBinding.status == "active",
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
                gate_sources = [source for source in selected_sources if source.status == "uploaded" and source.current_version_id]
            else:
                gate_sources = list(session.scalars(select(Source).where(
                    Source.document_library_id == document_library_id,
                    Source.status == "uploaded",
                    Source.current_version_id.is_not(None),
                )))
            blocked_versions = [
                version for source in gate_sources
                if (version := session.get(SourceVersion, source.current_version_id)) is not None
                and (version.review_status != "approved" or not version.current_review_snapshot_id)
            ]
            if blocked_versions:
                counts = {"total": 0, "pending_review": 0, "approved": 0, "rejected": 0}
                for version in blocked_versions:
                    for key, value in self._review_counts(self._active_source_chunks(session, version.id)).items():
                        counts[key] += value
                if any(version.preparation_status != "completed" for version in blocked_versions):
                    message = "当前文档仍在解析或分块，完成后请先进行人工审核。"
                elif counts["rejected"]:
                    message = f"当前文档存在 {counts['rejected']} 个已拒绝文档块，请修正或删除后再运行知识流程。"
                else:
                    message = f"当前文档存在 {counts['pending_review']} 个待审核文档块，请完成审核后再运行知识流程。"
                raise ReviewGateError("REVIEW_REQUIRED", message,
                                      source_version_id=blocked_versions[0].id, counts=counts)
            jobs: list[tuple[list[str], dict[str, str], str, str]] = []
            for binding in session.scalars(select(DocumentLibraryTemplateBinding).where(
                DocumentLibraryTemplateBinding.document_library_id == document_library_id,
                DocumentLibraryTemplateBinding.status == "active",
            )):
                template, revision = self._published_template_revision(session, binding.knowledge_flow_template_id)
                self._ensure_document_binding_outputs(session, document_library, template, binding, recreate_deleted=True)
                versions = self._binding_pending_versions(session, binding, revision)
                if selected_versions is not None:
                    versions = [version_id for version_id in versions if version_id in selected_versions]
                if not versions:
                    continue
                outputs = {item.output_key: item.knowledge_library_id for item in session.scalars(select(DocumentLibraryTemplateOutput).where(
                    DocumentLibraryTemplateOutput.document_library_template_binding_id == binding.id,
                    DocumentLibraryTemplateOutput.output_key.in_(self._revision_output_types(session, revision)),
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
            ir = session.scalar(select(DocumentIR).where(DocumentIR.source_version_id == version.id).order_by(DocumentIR.created_at.desc())) if version else None
            active_set = session.get(SourceChunkSet, version.active_chunk_set_id) if version and version.active_chunk_set_id else None
            candidate_set = session.get(SourceChunkSet, version.candidate_chunk_set_id) if version and version.candidate_chunk_set_id else None
            review_target = self._review_target_chunk_set(session, version) if version else None
            chunks = self._chunk_set_chunks(session, review_target.id) if review_target else []
            preparation = session.scalar(select(SourcePreparationJob).where(
                SourcePreparationJob.source_version_id == version.id
            ).order_by(SourcePreparationJob.preparation_revision.desc())) if version else None
            preparation_run = session.scalar(select(FlowRun).where(
                FlowRun.source_preparation_job_id == preparation.id
            ).order_by(FlowRun.created_at.desc())) if preparation else None
            node_runs = list(session.scalars(select(FlowNodeRun).where(
                FlowNodeRun.flow_run_id == preparation_run.id
            ).order_by(FlowNodeRun.started_at))) if preparation_run else []
            current_node = next((item.node_id for item in node_runs if item.status == "running"), None)
            snapshot = session.get(FlowExecutionSnapshot, review_target.execution_snapshot_id) if review_target and review_target.execution_snapshot_id else None
            chunker_node = next((item for item in (snapshot.compiled_definition_json or {}).get("nodes", [])
                                 if item.get("ref") == "semantic-chunker"), None) if snapshot else None
            chunker_revision = session.get(KnowledgeFlowTemplateRevision, snapshot.knowledge_flow_template_revision_id) if snapshot else None
            parser_artifacts = session.scalars(select(Artifact).where(
                Artifact.source_version_id == version.id,
                Artifact.type_code.like("parser.%"),
            ).order_by(Artifact.created_at.desc())).all() if version else []
            evidence_rows = session.execute(select(KnowledgeItemSource, KnowledgeItem, KnowledgeLibrary).join(KnowledgeItem, KnowledgeItem.id == KnowledgeItemSource.knowledge_item_id).join(KnowledgeLibrary, KnowledgeLibrary.id == KnowledgeItem.knowledge_library_id).where(KnowledgeItemSource.source_version_id == version.id)).all() if version else []
            return {"source": self._source_payload(source, version), "versions": [self._source_payload(source, item)["version"] for item in versions],
                    "preparation": None if not preparation else {
                        "job_id": preparation.id, "status": preparation.status,
                        "preparation_revision": preparation.preparation_revision,
                        "execution_snapshot_id": preparation.execution_snapshot_id,
                        "current_node": current_node,
                        "completed_nodes": sum(1 for item in node_runs if item.status == "completed"),
                        "total_nodes": len((snapshot.compiled_definition_json or {}).get("nodes", [])) if snapshot else 0,
                        "error": preparation.error,
                    },
                    "chunk_sets": {
                        "active": self._chunk_set_payload(active_set) if active_set else None,
                        "candidate": self._chunk_set_payload(candidate_set) if candidate_set else None,
                        "review_target_id": review_target.id if review_target else None,
                    },
                    "chunker": None if not chunker_node else {
                        "code": "semantic-chunker", "display_name": "结构化分块器",
                        "revision": chunker_revision.revision_no if chunker_revision else None,
                        "execution_snapshot_id": snapshot.id, "params": normalize_chunker_params(chunker_node.get("params")),
                    },
                    "jobs": [self.job_payload(job) for job in jobs], "flow_runs": [{"id": run.id, "status": run.status, "error": run.error, "created_at": run.created_at.isoformat(), "completed_at": run.completed_at.isoformat() if run.completed_at else None} for run in runs],
                    "document_ir": None if not ir else {
                        "id": ir.id, "text": ir.text, "parser_adapter": ir.parser_adapter,
                        "parser_profile": ir.parser_profile, "anchor": ir.anchor_json,
                        "source_type": (ir.anchor_json or {}).get("source_type"),
                        "blocks": list((ir.anchor_json or {}).get("blocks") or []),
                        "status": ir.status, "error": ir.error,
                    },
                    "parser_artifacts": [{"id": item.id, "type": item.type_code, "flow_run_id": item.flow_run_id,
                                          "uri": item.uri, "checksum": item.checksum, "metadata": item.data_json,
                                          "created_at": item.created_at.isoformat()} for item in parser_artifacts],
                    "review": self._source_review_payload(session, version) if version else None,
                    "source_chunks": [self._source_chunk_payload(session, item) for item in chunks],
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
            for version_id in sorted({str(chunk["source_version_id"]) for chunk in chunks}):
                self._lock_source_review_version(session, version_id)
            touched_versions: set[str] = set()
            for chunk in chunks:
                version_id, index = str(chunk["source_version_id"]), int(chunk["chunk_index"])
                chunk_set_id = str(chunk.get("chunk_set_id") or "")
                if not chunk_set_id:
                    version = session.get(SourceVersion, version_id)
                    chunk_set_id = str(version.candidate_chunk_set_id if version else "")
                chunk_set = session.get(SourceChunkSet, chunk_set_id)
                if not chunk_set or chunk_set.source_version_id != version_id:
                    raise ValueError("SourceChunk 的 ChunkSet 与 SourceVersion 不匹配")
                if session.scalar(select(SourceChunk.id).where(SourceChunk.chunk_set_id == chunk_set_id, SourceChunk.chunk_index == index)):
                    continue
                content = str(chunk.get("content", "")).strip()
                if not content:
                    continue
                digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
                row = SourceChunk(
                    id=new_id("sch"), source_version_id=version_id, chunk_set_id=chunk_set_id, flow_run_id=flow_run_id,
                    source_chunk_id=str(chunk.get("source_chunk_id", "")), chunk_index=index, content=content,
                    anchor_json=dict(chunk.get("anchor") or {}), content_hash=digest,
                    lifecycle_status="active", review_status="pending_review",
                )
                session.add(row); session.flush()
                revision = SourceChunkRevision(
                    id=new_id("schrev"), source_chunk_id=row.id, revision_no=1, content=content,
                    content_hash=digest, anchor_json=dict(row.anchor_json), operation="prepared",
                    parent_chunk_ids=[], actor="system",
                )
                session.add(revision); session.flush(); row.current_revision_id = revision.id
                touched_versions.add(version_id)
            for version_id in touched_versions:
                version = session.get(SourceVersion, version_id)
                if version:
                    version.preparation_status, version.review_status = "completed", "pending"

    @staticmethod
    def _current_chunk_revision(session: Session, chunk: SourceChunk) -> SourceChunkRevision | None:
        if not chunk.current_revision_id:
            return None
        lock = bool(session.info.get("source_review_write"))
        return session.get(SourceChunkRevision, chunk.current_revision_id, with_for_update=lock, populate_existing=lock)

    @staticmethod
    def _source_chunk_payload(session: Session, chunk: SourceChunk) -> dict[str, Any]:
        revision = V7Store._current_chunk_revision(session, chunk)
        return {
            "id": chunk.id, "chunk_set_id": chunk.chunk_set_id,
            "source_chunk_id": chunk.source_chunk_id, "chunk_index": chunk.chunk_index,
            "content": chunk.content, "content_hash": chunk.content_hash,
            "anchor": api_source_anchor(chunk.anchor_json),
            "lifecycle_status": chunk.lifecycle_status, "review_status": chunk.review_status,
            "revision_id": revision.id if revision else None,
            "revision_no": revision.revision_no if revision else 0,
            "reviewed_by": chunk.reviewed_by,
            "reviewed_at": chunk.reviewed_at.isoformat() if chunk.reviewed_at else None,
        }

    @staticmethod
    def _lock_source_review_version(session: Session, source_version_id: str) -> SourceVersion | None:
        """Serialize review mutations with replacement and candidate promotion.

        Always lock Source before SourceVersion. SQLite has no row locks, so its
        local writer transaction is acquired before reading mutable review state.
        """
        if not session.info.get("source_review_write"):
            connection = session.connection()
            if connection.dialect.name == "sqlite" and not connection.connection.driver_connection.in_transaction:
                connection.exec_driver_sql("BEGIN IMMEDIATE")
            session.info["source_review_write"] = True
        source_id = session.scalar(select(SourceVersion.source_id).where(SourceVersion.id == source_version_id))
        if not source_id:
            return None
        session.scalar(select(Source).where(Source.id == source_id).with_for_update().execution_options(populate_existing=True))
        return session.scalar(select(SourceVersion).where(SourceVersion.id == source_version_id)
                              .with_for_update().execution_options(populate_existing=True))

    @classmethod
    def _locked_source_chunk(cls, session: Session, chunk_id: str) -> SourceChunk | None:
        version_id = session.scalar(select(SourceChunk.source_version_id).where(SourceChunk.id == chunk_id))
        if not version_id:
            return None
        cls._lock_source_review_version(session, version_id)
        return session.get(SourceChunk, chunk_id, with_for_update=True, populate_existing=True)

    @staticmethod
    def _chunk_set_chunks(session: Session, chunk_set_id: str) -> list[SourceChunk]:
        query = select(SourceChunk).where(
            SourceChunk.chunk_set_id == chunk_set_id,
            SourceChunk.lifecycle_status == "active",
        ).order_by(SourceChunk.chunk_index, SourceChunk.id)
        if session.info.get("source_review_write"):
            query = query.with_for_update().execution_options(populate_existing=True)
        return list(session.scalars(query))

    @staticmethod
    def _review_target_chunk_set(session: Session, version: SourceVersion,
                                 chunk_set_id: str | None = None) -> SourceChunkSet | None:
        lock = bool(session.info.get("source_review_write"))
        if chunk_set_id:
            item = session.get(SourceChunkSet, chunk_set_id, with_for_update=lock, populate_existing=lock)
            if not item or item.source_version_id != version.id:
                raise ValueError("ChunkSet 不属于当前文件版本")
            return item
        candidate = session.get(SourceChunkSet, version.candidate_chunk_set_id, with_for_update=lock, populate_existing=lock) if version.candidate_chunk_set_id else None
        if candidate and candidate.status == "candidate":
            return candidate
        return session.get(SourceChunkSet, version.active_chunk_set_id, with_for_update=lock, populate_existing=lock) if version.active_chunk_set_id else None

    @classmethod
    def _active_source_chunks(cls, session: Session, source_version_id: str) -> list[SourceChunk]:
        version = session.get(SourceVersion, source_version_id)
        target = cls._review_target_chunk_set(session, version) if version else None
        return cls._chunk_set_chunks(session, target.id) if target else []

    @staticmethod
    def _review_counts(chunks: list[SourceChunk]) -> dict[str, int]:
        values = {"total": len(chunks), "pending_review": 0, "approved": 0, "rejected": 0}
        for chunk in chunks:
            if chunk.review_status in values:
                values[chunk.review_status] += 1
        return values

    @staticmethod
    def _aggregate_review_status(counts: dict[str, int]) -> str:
        if not counts["total"] or counts["rejected"]:
            return "rejected"
        if counts["approved"] == counts["total"]:
            return "approved"
        if counts["approved"]:
            return "in_review"
        return "pending"

    def _recompute_source_review(self, session: Session, version: SourceVersion) -> dict[str, int]:
        target = self._review_target_chunk_set(session, version)
        counts = self._review_counts(self._chunk_set_chunks(session, target.id) if target else [])
        version.review_status = self._aggregate_review_status(counts)
        if target and target.id == version.active_chunk_set_id and version.review_status != "approved":
            version.current_review_snapshot_id = None
        return counts

    def _source_review_payload(self, session: Session, version: SourceVersion,
                               chunk_set_id: str | None = None) -> dict[str, Any]:
        target = self._review_target_chunk_set(session, version, chunk_set_id)
        chunks = self._chunk_set_chunks(session, target.id) if target else []
        review_status = self._aggregate_review_status(self._review_counts(chunks))
        return {
            "source_version_id": version.id,
            "preparation_status": version.preparation_status,
            "review_status": review_status,
            "current_review_snapshot_id": version.current_review_snapshot_id,
            "chunk_set": self._chunk_set_payload(target) if target else None,
            "counts": self._review_counts(chunks),
            "chunks": [self._source_chunk_payload(session, item) for item in chunks],
        }

    def source_review_detail(self, source_version_id: str, chunk_set_id: str | None = None) -> dict[str, Any]:
        with self.sessions() as session:
            version = session.get(SourceVersion, source_version_id)
            if not version:
                raise ValueError("文件版本不存在")
            return self._source_review_payload(session, version, chunk_set_id)

    @staticmethod
    def _chunk_set_payload(item: SourceChunkSet) -> dict[str, Any]:
        return {
            "id": item.id, "source_version_id": item.source_version_id,
            "source_preparation_job_id": item.source_preparation_job_id,
            "flow_run_id": item.flow_run_id, "execution_snapshot_id": item.execution_snapshot_id,
            "preparation_revision": item.preparation_revision, "status": item.status,
            "content_digest": item.content_digest, "chunk_count": item.chunk_count,
            "metrics": dict(item.metrics_json or {}),
            "activated_at": item.activated_at.isoformat() if item.activated_at else None,
        }

    @staticmethod
    def _require_chunk_revision(session: Session, chunk: SourceChunk, expected_revision_no: int) -> SourceChunkRevision:
        version = session.get(SourceVersion, chunk.source_version_id)
        target = V7Store._review_target_chunk_set(session, version) if version else None
        if not target or target.id != chunk.chunk_set_id:
            raise ReviewGateError("CHUNK_SET_NOT_REVIEW_TARGET", "该文档块不属于当前审核目标",
                                  source_version_id=chunk.source_version_id)
        revision = V7Store._current_chunk_revision(session, chunk)
        if not revision:
            raise ValueError("文档块没有当前修订")
        if revision.revision_no != expected_revision_no:
            raise ReviewGateError("STALE_CHUNK_REVISION", "文档块已被其他操作更新，请刷新后重试",
                                  source_version_id=chunk.source_version_id)
        return revision

    @staticmethod
    def _append_chunk_revision(session: Session, chunk: SourceChunk, *, content: str, anchor: dict[str, Any],
                               operation: str, parent_chunk_ids: list[str], actor: str) -> SourceChunkRevision:
        current = V7Store._current_chunk_revision(session, chunk)
        revision_no = (current.revision_no if current else 0) + 1
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        revision = SourceChunkRevision(
            id=new_id("schrev"), source_chunk_id=chunk.id, revision_no=revision_no,
            content=content, content_hash=digest, anchor_json=anchor, operation=operation,
            parent_chunk_ids=parent_chunk_ids, actor=actor,
        )
        session.add(revision); session.flush()
        chunk.current_revision_id = revision.id
        chunk.content, chunk.content_hash, chunk.anchor_json = content, digest, anchor
        chunk.review_status, chunk.reviewed_by, chunk.reviewed_at = "pending_review", None, None
        return revision

    def update_source_chunk(self, chunk_id: str, content: str, expected_revision_no: int, *, actor: str = "admin") -> dict[str, Any]:
        normalized = str(content).strip()
        if not normalized:
            raise ValueError("文档块内容不能为空；如需移除请使用删除")
        with self.sessions.begin() as session:
            chunk = self._locked_source_chunk(session, chunk_id)
            if not chunk or chunk.lifecycle_status != "active":
                raise ValueError("文档块不存在或已删除")
            self._require_chunk_revision(session, chunk, expected_revision_no)
            if chunk.review_status == "approved":
                raise ReviewGateError("REOPEN_REQUIRED", "已批准文档块必须先重开审核再修改",
                                      source_version_id=chunk.source_version_id)
            self._append_chunk_revision(session, chunk, content=normalized, anchor=edited_anchor(chunk.anchor_json),
                                        operation="edited", parent_chunk_ids=[chunk.id], actor=actor)
            version = session.get(SourceVersion, chunk.source_version_id); assert version
            counts = self._recompute_source_review(session, version)
            self.audit(session, "source_chunk.edited", "source_chunk", chunk.id, {"actor": actor})
            return {**self._source_chunk_payload(session, chunk), "document_review_status": version.review_status, "counts": counts}

    def split_source_chunk(self, chunk_id: str, parts: list[str], expected_revision_no: int, *, actor: str = "admin") -> dict[str, Any]:
        normalized = [str(value).strip() for value in parts]
        if len(normalized) < 2 or any(not value for value in normalized):
            raise ValueError("拆分必须提供至少两个非空文档块")
        with self.sessions.begin() as session:
            chunk = self._locked_source_chunk(session, chunk_id)
            if not chunk or chunk.lifecycle_status != "active": raise ValueError("文档块不存在或已删除")
            self._require_chunk_revision(session, chunk, expected_revision_no)
            if chunk.review_status == "approved":
                raise ReviewGateError("REOPEN_REQUIRED", "已批准文档块必须先重开审核再拆分", source_version_id=chunk.source_version_id)
            child_ranges = sequential_part_ranges(chunk.content, normalized)
            siblings = self._chunk_set_chunks(session, chunk.chunk_set_id)
            position = siblings.index(chunk); chunk.lifecycle_status = "deleted"
            created: list[SourceChunk] = []
            for child_index, part in enumerate(normalized):
                child_anchor = split_source_anchor(chunk.anchor_json, child_ranges[child_index] if child_ranges else None)
                logical = SourceChunk(
                    id=new_id("sch"), source_version_id=chunk.source_version_id, chunk_set_id=chunk.chunk_set_id,
                    flow_run_id=chunk.flow_run_id,
                    origin_flow_run_id=chunk.origin_flow_run_id, source_chunk_id=new_id("sourcechunk"),
                    chunk_index=position + len(created), content=part,
                    anchor_json=child_anchor,
                    content_hash=hashlib.sha256(part.encode("utf-8")).hexdigest(),
                    lifecycle_status="active", review_status="pending_review",
                )
                session.add(logical); session.flush()
                revision = SourceChunkRevision(
                    id=new_id("schrev"), source_chunk_id=logical.id, revision_no=1, content=part,
                    content_hash=logical.content_hash, anchor_json=logical.anchor_json, operation="split",
                    parent_chunk_ids=[chunk.id], actor=actor,
                )
                session.add(revision); session.flush(); logical.current_revision_id = revision.id
                created.append(logical)
            active = [*siblings[:position], *created, *siblings[position + 1:]]
            for index, item in enumerate(active): item.chunk_index = index
            version = session.get(SourceVersion, chunk.source_version_id); assert version
            counts = self._recompute_source_review(session, version)
            self.audit(session, "source_chunk.split", "source_chunk", chunk.id, {"children": [item.id for item in created]})
            return {"chunks": [self._source_chunk_payload(session, item) for item in created],
                    "document_review_status": version.review_status, "counts": counts}

    def merge_source_chunks(self, chunk_ids: list[str], expected_revisions: dict[str, int], *, actor: str = "admin") -> dict[str, Any]:
        identifiers = list(dict.fromkeys(str(value) for value in chunk_ids if value))
        if len(identifiers) < 2: raise ValueError("合并必须选择至少两个文档块")
        with self.sessions.begin() as session:
            versions = set(session.scalars(select(SourceChunk.source_version_id).where(SourceChunk.id.in_(identifiers))))
            if len(versions) > 1:
                raise ValueError("只能合并同一文件版本的文档块")
            for version_id in versions:
                self._lock_source_review_version(session, version_id)
            rows = list(session.scalars(select(SourceChunk).where(SourceChunk.id.in_(identifiers))
                                        .with_for_update().execution_options(populate_existing=True)))
            if len(rows) != len(identifiers) or any(item.lifecycle_status != "active" for item in rows):
                raise ValueError("所选文档块不存在或已删除")
            rows.sort(key=lambda item: item.chunk_index)
            if len({item.source_version_id for item in rows}) != 1:
                raise ValueError("只能合并同一文件版本的文档块")
            if len({item.chunk_set_id for item in rows}) != 1:
                raise ValueError("只能合并同一 ChunkSet 的文档块")
            if [item.chunk_index for item in rows] != list(range(rows[0].chunk_index, rows[-1].chunk_index + 1)):
                raise ValueError("只能合并连续文档块")
            for item in rows:
                self._require_chunk_revision(session, item, int(expected_revisions.get(item.id, -1)))
                if item.review_status == "approved":
                    raise ReviewGateError("REOPEN_REQUIRED", "已批准文档块必须先重开审核再合并", source_version_id=item.source_version_id)
            content = "\n\n".join(item.content for item in rows)
            merged = SourceChunk(
                id=new_id("sch"), source_version_id=rows[0].source_version_id, chunk_set_id=rows[0].chunk_set_id,
                flow_run_id=rows[0].flow_run_id,
                source_chunk_id=new_id("sourcechunk"), chunk_index=rows[0].chunk_index, content=content,
                anchor_json=merge_source_anchors([item.anchor_json for item in rows], [item.content for item in rows]),
                content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(), lifecycle_status="active",
                review_status="pending_review",
            )
            session.add(merged); session.flush()
            revision = SourceChunkRevision(
                id=new_id("schrev"), source_chunk_id=merged.id, revision_no=1, content=content,
                content_hash=merged.content_hash, anchor_json=merged.anchor_json, operation="merged",
                parent_chunk_ids=[item.id for item in rows], actor=actor,
            )
            session.add(revision); session.flush(); merged.current_revision_id = revision.id
            for item in rows: item.lifecycle_status = "deleted"
            active = self._chunk_set_chunks(session, merged.chunk_set_id)
            for index, item in enumerate(active): item.chunk_index = index
            version = session.get(SourceVersion, merged.source_version_id); assert version
            counts = self._recompute_source_review(session, version)
            self.audit(session, "source_chunk.merged", "source_chunk", merged.id, {"parents": identifiers})
            return {"chunk": self._source_chunk_payload(session, merged), "document_review_status": version.review_status, "counts": counts}

    def delete_source_chunk(self, chunk_id: str, expected_revision_no: int, *, actor: str = "admin") -> dict[str, Any]:
        with self.sessions.begin() as session:
            chunk = self._locked_source_chunk(session, chunk_id)
            if not chunk or chunk.lifecycle_status != "active": raise ValueError("文档块不存在或已删除")
            self._require_chunk_revision(session, chunk, expected_revision_no)
            if chunk.review_status == "approved":
                raise ReviewGateError("REOPEN_REQUIRED", "已批准文档块必须先重开审核再删除", source_version_id=chunk.source_version_id)
            chunk.lifecycle_status = "deleted"
            active = self._chunk_set_chunks(session, chunk.chunk_set_id)
            for index, item in enumerate(active): item.chunk_index = index
            version = session.get(SourceVersion, chunk.source_version_id); assert version
            counts = self._recompute_source_review(session, version)
            self.audit(session, "source_chunk.deleted", "source_chunk", chunk.id, {"actor": actor})
            return {"id": chunk.id, "lifecycle_status": chunk.lifecycle_status,
                    "document_review_status": version.review_status, "counts": counts}

    def review_source_chunk(self, chunk_id: str, status: str, expected_revision_no: int, *, actor: str = "admin") -> dict[str, Any]:
        if status not in {"approved", "rejected"}: raise ValueError("审核状态必须是 approved 或 rejected")
        with self.sessions.begin() as session:
            chunk = self._locked_source_chunk(session, chunk_id)
            if not chunk or chunk.lifecycle_status != "active": raise ValueError("文档块不存在或已删除")
            self._require_chunk_revision(session, chunk, expected_revision_no)
            chunk.review_status, chunk.reviewed_by, chunk.reviewed_at = status, actor, utc_now()
            version = session.get(SourceVersion, chunk.source_version_id); assert version
            counts = self._recompute_source_review(session, version)
            snapshot = self._approve_source_version(session, version, actor) if version.review_status == "approved" else None
            self.audit(session, f"source_chunk.{status}", "source_chunk", chunk.id, {"actor": actor})
            return {**self._source_chunk_payload(session, chunk), "document_review_status": version.review_status,
                    "counts": counts, "review_snapshot_id": snapshot.id if snapshot else None}

    def reopen_source_chunk(self, chunk_id: str, *, actor: str = "admin") -> dict[str, Any]:
        with self.sessions.begin() as session:
            chunk = self._locked_source_chunk(session, chunk_id)
            if not chunk or chunk.lifecycle_status != "active": raise ValueError("文档块不存在或已删除")
            version = session.get(SourceVersion, chunk.source_version_id); assert version
            target = self._review_target_chunk_set(session, version)
            if not target or target.id != chunk.chunk_set_id:
                raise ReviewGateError("CHUNK_SET_NOT_REVIEW_TARGET", "该文档块不属于当前审核目标",
                                      source_version_id=chunk.source_version_id)
            if chunk.review_status != "approved": raise ValueError("只有已批准文档块可以重开审核")
            active_jobs = [job for job in session.scalars(select(KnowledgeJob).where(
                KnowledgeJob.status.in_(("queued", "running")),
            )) if chunk.source_version_id in (job.source_version_ids or [])]
            if active_jobs:
                raise ReviewGateError("ACTIVE_KNOWLEDGE_JOB", "当前文件仍有活动知识任务，请先停止任务再重开审核",
                                      source_version_id=chunk.source_version_id)
            chunk.review_status, chunk.reviewed_by, chunk.reviewed_at = "pending_review", None, None
            counts = self._recompute_source_review(session, version)
            self.audit(session, "source_chunk.review_reopened", "source_chunk", chunk.id, {"actor": actor})
            return {**self._source_chunk_payload(session, chunk), "document_review_status": version.review_status, "counts": counts}

    def _approve_source_version(self, session: Session, version: SourceVersion, actor: str) -> SourceReviewSnapshot:
        chunk_set = self._review_target_chunk_set(session, version)
        if not chunk_set:
            raise ReviewGateError("REVIEW_REQUIRED", "当前文档没有可审核的 ChunkSet", source_version_id=version.id)
        chunks = self._chunk_set_chunks(session, chunk_set.id); counts = self._review_counts(chunks)
        if not chunks or counts["approved"] != counts["total"]:
            message = (f"当前文档存在 {counts['pending_review']} 个待审核文档块，请完成审核后再运行知识流程。"
                       if counts["pending_review"] else
                       f"当前文档存在 {counts['rejected']} 个已拒绝文档块，请修正或删除后再运行知识流程。")
            raise ReviewGateError("REVIEW_REQUIRED", message, source_version_id=version.id, counts=counts)
        revisions = [self._current_chunk_revision(session, item) for item in chunks]
        if any(item is None for item in revisions): raise ValueError("文档块缺少当前修订")
        material = json.dumps([{"id": item.id, "hash": item.content_hash} for item in revisions], ensure_ascii=False, separators=(",", ":"))
        digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
        snapshot = session.scalar(select(SourceReviewSnapshot).where(
            SourceReviewSnapshot.chunk_set_id == chunk_set.id,
            SourceReviewSnapshot.content_digest == digest,
        ).with_for_update())
        if not snapshot:
            review_no = int(session.scalar(select(func.max(SourceReviewSnapshot.review_no)).where(
                SourceReviewSnapshot.source_version_id == version.id,
            )) or 0) + 1
            snapshot = SourceReviewSnapshot(
                id=new_id("review"), source_version_id=version.id, chunk_set_id=chunk_set.id, review_no=review_no,
                content_digest=digest, reviewed_by=actor, approved_at=utc_now(), status="approved",
            )
            session.add(snapshot); session.flush()
            for ordinal, (chunk, revision) in enumerate(zip(chunks, revisions, strict=True)):
                session.add(SourceReviewSnapshotChunk(
                    id=new_id("reviewchunk"), source_review_snapshot_id=snapshot.id,
                    source_chunk_id=chunk.id, source_chunk_revision_id=revision.id,
                    ordinal=ordinal, content_hash=revision.content_hash,
                ))
        if chunk_set.id == version.candidate_chunk_set_id:
            previous = session.get(SourceChunkSet, version.active_chunk_set_id) if version.active_chunk_set_id else None
            if previous and previous.id != chunk_set.id:
                previous.status = "superseded"
            chunk_set.status, chunk_set.activated_at = "active", utc_now()
            version.active_chunk_set_id, version.candidate_chunk_set_id = chunk_set.id, None
        version.review_status, version.current_review_snapshot_id = "approved", snapshot.id
        self._queue_review_dispatch(session, snapshot.id)
        self.audit(session, "source_version.review_approved", "source_version", version.id,
                   {"snapshot_id": snapshot.id, "review_digest": digest, "actor": actor})
        return snapshot

    def approve_source_version(self, source_version_id: str, *, actor: str = "admin", approve_pending: bool = True) -> dict[str, Any]:
        with self.sessions.begin() as session:
            version = self._lock_source_review_version(session, source_version_id)
            if not version: raise ValueError("文件版本不存在")
            snapshot = self._approve_pending_source_review(session, version, actor, approve_pending=approve_pending)
            return {**self._source_review_payload(session, version),
                    "review_snapshot_id": snapshot.id, "review_digest": snapshot.content_digest}

    def _approve_pending_source_review(self, session: Session, version: SourceVersion, actor: str,
                                       *, approve_pending: bool = True) -> SourceReviewSnapshot:
        chunks = self._active_source_chunks(session, version.id)
        counts = self._review_counts(chunks)
        if counts["rejected"]:
            raise ReviewGateError("REVIEW_REQUIRED",
                                  f"当前文档存在 {counts['rejected']} 个已拒绝文档块，请修正或删除后再运行知识流程。",
                                  source_version_id=version.id, counts=counts)
        if approve_pending:
            for chunk in chunks:
                if chunk.review_status == "pending_review":
                    chunk.review_status, chunk.reviewed_by, chunk.reviewed_at = "approved", actor, utc_now()
        self._recompute_source_review(session, version)
        return self._approve_source_version(session, version, actor)

    def approve_document_sources_batch(self, library_id: str, items: list[dict[str, Any]],
                                       *, actor: str = "admin") -> dict[str, Any]:
        if not 1 <= len(items) <= 50 or len({item["source_id"] for item in items}) != len(items):
            raise ValueError("请选择 1 至 50 个不重复的文件")
        self.get_document_library(library_id)
        results = []
        for item in items:
            result = {"source_id": item["source_id"], "source_version_id": item["source_version_id"]}
            try:
                if not item["source_version_id"] or item["activation_no"] is None:
                    raise ReviewGateError("SOURCE_VERSION_UNAVAILABLE", "文件缺少当前版本，请刷新后核对")
                with self.sessions.begin() as session:
                    version = self._lock_source_review_version(session, item["source_version_id"])
                    if not version or version.source_id != item["source_id"]:
                        raise ReviewGateError("SOURCE_NOT_IN_LIBRARY", "文件或版本不存在于当前文档库")
                    source = session.get(Source, item["source_id"], with_for_update=True)
                    if not source or source.document_library_id != library_id:
                        raise ReviewGateError("SOURCE_NOT_IN_LIBRARY", "文件或版本不存在于当前文档库")
                    result["filename"] = version.original_filename
                    if source.status in {"deleted", "deleting"}:
                        raise ReviewGateError("SOURCE_UNAVAILABLE", "文件已删除或正在删除")
                    if source.current_version_id != version.id or version.activation_no != item["activation_no"]:
                        raise ReviewGateError("SOURCE_VERSION_CHANGED", "当前文件版本已变化，请刷新后重试")
                    if version.preparation_status != "completed":
                        raise ReviewGateError("PREPARATION_NOT_READY", "解析与分块尚未完成或已失败，请完成后重新审核")
                    target = self._review_target_chunk_set(session, version)
                    if not target or target.id != item["chunk_set_id"] or target.status not in {"candidate", "active"}:
                        raise ReviewGateError("REVIEW_TARGET_CHANGED", "当前审核目标已变化或不存在，请刷新后重试")
                    chunks = self._chunk_set_chunks(session, target.id)
                    counts = self._review_counts(chunks)
                    if not counts["total"]:
                        raise ReviewGateError("EMPTY_CHUNK_SET", "文件没有可审核的文档块")
                    if counts["rejected"]:
                        raise ReviewGateError("REJECTED_CHUNKS", f"存在 {counts['rejected']} 个已拒绝文档块，请进入审核页处理")
                    snapshot = session.get(SourceReviewSnapshot, version.current_review_snapshot_id, with_for_update=True) if version.current_review_snapshot_id else None
                    revisions = [self._current_chunk_revision(session, chunk) for chunk in chunks]
                    if any(revision is None for revision in revisions):
                        raise ValueError("文档块缺少当前修订")
                    digest = hashlib.sha256(json.dumps(
                        [{"id": revision.id, "hash": revision.content_hash} for revision in revisions],
                        ensure_ascii=False, separators=(",", ":"),
                    ).encode("utf-8")).hexdigest()
                    if (counts["approved"] == counts["total"] and version.review_status == "approved"
                            and snapshot and snapshot.status == "approved" and snapshot.chunk_set_id == target.id
                            and snapshot.content_digest == digest and target.id == version.active_chunk_set_id):
                        result.update(status="already_approved", code="ALREADY_APPROVED", message="已审核通过，未重复调度",
                                      review_snapshot_id=snapshot.id)
                    else:
                        job = session.get(SourcePreparationJob, target.source_preparation_job_id, with_for_update=True) if target.source_preparation_job_id else None
                        if not job or job.status != "completed":
                            raise ReviewGateError("PREPARATION_NOT_READY", "解析与分块任务尚未成功完成，请完成后重新审核")
                        snapshot = self._approve_pending_source_review(session, version, actor)
                        result.update(status="approved", code="APPROVED", message="审核通过；已按现有绑定调度知识模板",
                                      review_snapshot_id=snapshot.id)
                    self.audit(session, "source_version.batch_review_approved", "source_version", version.id,
                               {"actor": actor, "result": result["status"], "snapshot_id": snapshot.id})
            except ReviewGateError as exc:
                result.update(status="skipped", code=exc.code, message=str(exc))
            except Exception:
                # This file's transaction has rolled back; retain other files' results.
                LOGGER.exception("Batch source review failed", extra={"source_id": item["source_id"]})
                result.pop("review_snapshot_id", None)
                result.update(status="failed", code="REVIEW_FAILED", message="审核提交失败，请刷新后重试")
            results.append(result)
        return {"results": results, "counts": {status: sum(item["status"] == status for item in results)
                for status in ("approved", "already_approved", "skipped", "failed")}}

    def batch_review_source_chunks(self, source_version_id: str, chunk_ids: list[str], action: str,
                                   expected_revisions: dict[str, int], *, actor: str = "admin") -> dict[str, Any]:
        if action not in {"approve", "reject"}:
            raise ValueError("批量审核 action 必须是 approve 或 reject")
        identifiers = list(dict.fromkeys(str(value) for value in chunk_ids if value))
        if not identifiers:
            raise ValueError("批量审核至少选择一个文档块")
        with self.sessions.begin() as session:
            version = self._lock_source_review_version(session, source_version_id)
            if not version:
                raise ValueError("文件版本不存在")
            target = self._review_target_chunk_set(session, version)
            rows = list(session.scalars(select(SourceChunk).where(SourceChunk.id.in_(identifiers)).with_for_update()))
            if len(rows) != len(identifiers) or not target or any(
                item.chunk_set_id != target.id or item.lifecycle_status != "active" for item in rows
            ):
                raise ValueError("批量审核包含不属于当前审核目标的文档块")
            for item in rows:
                self._require_chunk_revision(session, item, int(expected_revisions.get(item.id, -1)))
            status = "approved" if action == "approve" else "rejected"
            reviewed_at = utc_now()
            for item in rows:
                item.review_status, item.reviewed_by, item.reviewed_at = status, actor, reviewed_at
            counts = self._recompute_source_review(session, version)
            snapshot = self._approve_source_version(session, version, actor) if version.review_status == "approved" else None
            self.audit(session, f"source_chunk.batch_{action}", "source_version", version.id,
                       {"chunk_ids": identifiers, "actor": actor})
            return {**self._source_review_payload(session, version),
                    "review_snapshot_id": snapshot.id if snapshot else None, "counts": counts}

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
            job = DocumentDeletionJob(
                id=new_id("deldoc"), target_kind="sources" if source_ids else "libraries",
                source_ids=check["source_ids"], document_library_ids=check["document_library_ids"],
                blob_uris=sorted({item.blob_uri for item in versions}), object_keys=parser_keys, status="queued",
            )
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
        self._published_execution_snapshot(session, revision)
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

    def unreferenced_blobs_for_deletion(self, job_id: str) -> list[str]:
        """Return only blobs whose every SourceVersion reference belongs to this deletion job."""
        with self.sessions() as session:
            job = session.get(DocumentDeletionJob, job_id)
            if not job:
                raise ValueError("文档删除任务不存在")
            candidates = set(job.blob_uris or [])
            if not candidates:
                return []
            referenced = set(session.scalars(select(SourceVersion.blob_uri).where(
                SourceVersion.blob_uri.in_(candidates),
                SourceVersion.source_id.not_in(set(job.source_ids or []) or {""}),
            )))
            return sorted(candidates - referenced)

    def delete_unreferenced_blobs(self, job_id: str, delete_blob) -> list[str]:
        """Recheck and physically remove orphan blobs while holding the indexed reference range."""
        with self.sessions.begin() as session:
            job = session.scalar(select(DocumentDeletionJob).where(
                DocumentDeletionJob.id == job_id,
            ).with_for_update())
            if not job:
                raise ValueError("文档删除任务不存在")
            candidates = set(job.blob_uris or [])
            if not candidates:
                return []
            referenced = set(session.scalars(select(SourceVersion.blob_uri).where(
                SourceVersion.blob_uri.in_(candidates),
                SourceVersion.source_id.not_in(set(job.source_ids or []) or {""}),
            ).with_for_update()))
            deleted = sorted(candidates - referenced)
            for blob_uri in deleted:
                delete_blob(blob_uri)
            return deleted

    def finish_document_deletion(self, job_id: str, error: str | None = None) -> dict[str, Any]:
        with self.sessions.begin() as session:
            job = session.get(DocumentDeletionJob, job_id)
            if not job: raise ValueError("文档删除任务不存在")
            if error:
                job.status, job.error, job.lease_owner = "failed", error, None
                return {"id": job.id, "status": job.status, "error": error}
            version_ids = list(session.scalars(select(SourceVersion.id).where(SourceVersion.source_id.in_(job.source_ids))))
            preparation_ids = list(session.scalars(select(SourcePreparationJob.id).where(
                SourcePreparationJob.source_version_id.in_(version_ids),
            ))) if version_ids else []
            preparation_run_ids = list(session.scalars(select(FlowRun.id).where(
                FlowRun.source_preparation_job_id.in_(preparation_ids),
            ))) if preparation_ids else []
            if preparation_run_ids:
                node_ids = list(session.scalars(select(FlowNodeRun.id).where(FlowNodeRun.flow_run_id.in_(preparation_run_ids))))
                run_artifact_ids = list(session.scalars(select(Artifact.id).where(Artifact.flow_run_id.in_(preparation_run_ids))))
                if node_ids:
                    session.execute(delete(FlowNodeArtifactBinding).where(FlowNodeArtifactBinding.flow_node_run_id.in_(node_ids)))
                if run_artifact_ids:
                    session.execute(delete(ArtifactLineage).where(
                        (ArtifactLineage.parent_artifact_id.in_(run_artifact_ids)) |
                        (ArtifactLineage.child_artifact_id.in_(run_artifact_ids))
                    ))
                    session.execute(delete(Artifact).where(Artifact.id.in_(run_artifact_ids)))
                session.execute(delete(FlowRunEvent).where(FlowRunEvent.flow_run_id.in_(preparation_run_ids)))
                session.execute(delete(FlowNodeRun).where(FlowNodeRun.flow_run_id.in_(preparation_run_ids)))
            review_snapshot_ids = list(session.scalars(select(SourceReviewSnapshot.id).where(
                SourceReviewSnapshot.source_version_id.in_(version_ids),
            ))) if version_ids else []
            if version_ids:
                session.execute(update(SourceVersion).where(SourceVersion.id.in_(version_ids)).values(
                    active_chunk_set_id=None, candidate_chunk_set_id=None,
                ))
            if version_ids:
                session.execute(delete(KnowledgeItemSource).where(KnowledgeItemSource.source_version_id.in_(version_ids)))
                session.execute(delete(DocumentLibraryProcessingRecord).where(
                    DocumentLibraryProcessingRecord.source_version_id.in_(version_ids)
                ))
            if review_snapshot_ids:
                session.execute(delete(KnowledgeDispatch).where(
                    KnowledgeDispatch.source_review_snapshot_id.in_(review_snapshot_ids)
                ))
                session.execute(delete(SourceReviewSnapshotChunk).where(
                    SourceReviewSnapshotChunk.source_review_snapshot_id.in_(review_snapshot_ids)
                ))
            session.execute(delete(KnowledgeJobReviewInput).where(
                KnowledgeJobReviewInput.source_version_id.in_(version_ids)
            ))
            chunk_ids = list(session.scalars(select(SourceChunk.id).where(
                SourceChunk.source_version_id.in_(version_ids),
            ))) if version_ids else []
            if chunk_ids:
                session.execute(delete(SourceChunkRevision).where(SourceChunkRevision.source_chunk_id.in_(chunk_ids)))
            if review_snapshot_ids:
                session.execute(delete(SourceReviewSnapshot).where(SourceReviewSnapshot.id.in_(review_snapshot_ids)))
            artifact_ids = list(session.scalars(select(Artifact.id).where(Artifact.source_version_id.in_(version_ids)))) if version_ids else []
            if artifact_ids:
                session.execute(delete(ArtifactLineage).where(
                    (ArtifactLineage.parent_artifact_id.in_(artifact_ids)) | (ArtifactLineage.child_artifact_id.in_(artifact_ids))
                ))
                session.execute(delete(Artifact).where(Artifact.id.in_(artifact_ids)))
            for model, column in (
                (SourceChunk, SourceChunk.source_version_id),
                (DocumentIR, DocumentIR.source_version_id),
                (KnowledgeChunkGeneration, KnowledgeChunkGeneration.source_version_id),
                (KnowledgeItemSource, KnowledgeItemSource.source_version_id),
            ):
                session.execute(delete(model).where(column.in_(version_ids)))
            if version_ids:
                session.execute(delete(SourceChunkSet).where(SourceChunkSet.source_version_id.in_(version_ids)))
            # SourceChunk and DocumentIR both retain preparation FlowRun lineage,
            # so their rows must be removed before the runs and preparation jobs.
            if preparation_run_ids:
                session.execute(delete(FlowRun).where(FlowRun.id.in_(preparation_run_ids)))
            if preparation_ids:
                session.execute(delete(SourcePreparationJob).where(SourcePreparationJob.id.in_(preparation_ids)))
            session.execute(delete(DocumentLibraryMember).where(DocumentLibraryMember.source_id.in_(job.source_ids)))
            session.execute(delete(SourceVersion).where(SourceVersion.source_id.in_(job.source_ids)))
            session.execute(delete(Source).where(Source.id.in_(job.source_ids)))
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
    def _knowledge_library_payload(item: KnowledgeLibrary, ready: bool | None = None,
                                   knowledge_item_count: int | None = None) -> dict[str, Any]:
        result = {"id": item.id, "code": item.code, "name": item.name, "knowledge_type": item.knowledge_type,
                  "graph_mode": item.graph_mode, "display_type": ({"triple": "三元组图谱", "semantic": "语义图谱"}.get(item.graph_mode) if item.knowledge_type == "graph" else None),
                  "knowledge_type_revision_id": item.knowledge_type_revision_id, "description": item.description, "embedding_profile_id": item.embedding_profile_id, "index_profile_id": item.index_profile_id, "partition_name": item.partition_name, "status": item.status, "updated_at": item.updated_at.isoformat(),
                  "graph_schema_hash": getattr(item, "graph_schema_hash", None), "source_template_revision_id": getattr(item, "source_template_revision_id", None),
                  "graph_schema_snapshot": getattr(item, "graph_schema_snapshot_json", None),
                  "origin_type": item.origin_type, "origin_state": item.origin_state,
                  "migration_status": item.migration_status}
        if ready is not None:
            result["vector_ready"] = ready
        if knowledge_item_count is not None:
            result["knowledge_item_count"] = int(knowledge_item_count)
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
        return bool(self._knowledge_publish_preflight(session, library)["current_ready"])

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
                    embedding_serving_id=revision.embedding_serving_id,
                    embedding_input=revision.embedding_input,
                    fields_json=revision.fields_json, revision_id=revision.id,
                    storage_contract_revision_id=revision.storage_contract_revision_id,
                    collection_policy=revision.collection_policy))
            return snapshots
        return [SimpleNamespace(id=item.id, code=item.code, knowledge_type=item.knowledge_type,
            collection_name=item.collection_name, embedding_profile_id=item.embedding_profile_id,
            embedding_serving_id=item.embedding_serving_id, embedding_input=item.embedding_input,
            fields_json=item.fields_json, revision_id=item.current_revision_id,
            storage_contract_revision_id=None, collection_policy="external")
            for item in session.scalars(select(KnowledgeIndexProfile).where(
                KnowledgeIndexProfile.knowledge_type == library.knowledge_type,
                KnowledgeIndexProfile.status == "active",
            ))]

    def list_knowledge_libraries(self, knowledge_type: str | None = None) -> list[dict[str, Any]]:
        with self.sessions() as session:
            query = select(KnowledgeLibrary).where(
                KnowledgeLibrary.status.in_(("active", "deleting")),
            ).order_by(KnowledgeLibrary.updated_at.desc())
            if knowledge_type:
                query = query.where(KnowledgeLibrary.knowledge_type == knowledge_type)
            libraries = list(session.scalars(query))
            if not libraries:
                return []
            library_ids = [item.id for item in libraries]
            item_counts = dict(session.execute(select(
                KnowledgeItem.knowledge_library_id, func.count(KnowledgeItem.id),
            ).where(
                KnowledgeItem.knowledge_library_id.in_(library_ids),
                KnowledgeItem.status == "active",
            ).group_by(KnowledgeItem.knowledge_library_id)).all())
            source_document_libraries: dict[str, dict[str, dict[str, str]]] = {}
            for library_id, document_library_id, document_library_name in session.execute(select(
                DocumentLibraryTemplateOutput.knowledge_library_id,
                DocumentLibrary.id,
                DocumentLibrary.name,
            ).join(
                DocumentLibraryTemplateBinding,
                DocumentLibraryTemplateBinding.id == DocumentLibraryTemplateOutput.document_library_template_binding_id,
            ).join(
                DocumentLibrary,
                DocumentLibrary.id == DocumentLibraryTemplateBinding.document_library_id,
            ).where(DocumentLibraryTemplateOutput.knowledge_library_id.in_(library_ids))):
                source_document_libraries.setdefault(library_id, {})[document_library_id] = {
                    "id": document_library_id,
                    "name": document_library_name,
                }
            payloads = []
            for item in libraries:
                review = self._knowledge_publish_preflight(session, item)
                payload = self._knowledge_library_payload(
                    item, bool(review["current_ready"]), item_counts.get(item.id, 0),
                )
                payload["review_required"] = review["review_required"]
                payload["review_counts"] = review["counts"]
                payload["vector_state"] = review["vector_state"]
                payload["vector_stale"] = review["vector_stale"]
                payload["has_ready_asset"] = review["has_ready_asset"]
                payload["source_document_libraries"] = list(source_document_libraries.get(item.id, {}).values())
                payload["collection_names"] = list(dict.fromkeys(
                    profile.collection_name for profile in self._index_profile_snapshots_for_library(session, item)
                    if profile.collection_name
                ))
                payloads.append(payload)
            return payloads

    def dashboard_overview(self, instance_mode: str) -> dict[str, Any]:
        """Return one authoritative set of dashboard counters for central or local UI."""
        if instance_mode not in {"central", "local"}:
            raise ValueError("实例模式无效")
        with self.sessions() as session:
            document_library_count = int(session.scalar(select(func.count()).select_from(DocumentLibrary).where(
                DocumentLibrary.status != "deleted",
            )) or 0)
            file_count = int(session.scalar(select(func.count()).select_from(Source).where(
                Source.status.not_in(("deleting", "deleted")),
            )) or 0)
            active_template_binding_count = int(session.scalar(select(func.count()).select_from(
                DocumentLibraryTemplateBinding,
            ).where(DocumentLibraryTemplateBinding.status == "active")) or 0)
            active_job_count = int(session.scalar(select(func.count()).select_from(KnowledgeJob).where(
                KnowledgeJob.status.in_(("queued", "running")),
            )) or 0)
            job_alert_count = int(session.scalar(select(func.count()).select_from(KnowledgeJob).where(
                KnowledgeJob.status.in_(("failed", "completed_with_warnings")),
            )) or 0)

            libraries = list(session.scalars(select(KnowledgeLibrary).where(
                KnowledgeLibrary.status.in_(("active", "deleting")),
            )))
            library_ids = [item.id for item in libraries]
            item_counts = dict(session.execute(select(
                KnowledgeItem.knowledge_library_id, func.count(KnowledgeItem.id),
            ).where(
                KnowledgeItem.knowledge_library_id.in_(library_ids),
                KnowledgeItem.status == "active",
            ).group_by(KnowledgeItem.knowledge_library_id)).all()) if library_ids else {}
            active_libraries = [item for item in libraries if item.status == "active"]
            vector_ready_count = sum(1 for item in active_libraries if self._library_ready(session, item))
            active_knowledge_count = sum(int(item_counts.get(item.id, 0)) for item in active_libraries)
            knowledge_assets = []
            for definition in FIXED_KNOWLEDGE_ASSET_TYPES:
                matched = [item for item in libraries if item.knowledge_type == definition["knowledge_type"] and (
                    definition["graph_mode"] is None or item.graph_mode == definition["graph_mode"]
                )]
                knowledge_assets.append({
                    **definition,
                    "library_count": len(matched),
                    "knowledge_item_count": sum(int(item_counts.get(item.id, 0)) for item in matched),
                })

            if instance_mode == "central":
                releases = list(session.scalars(select(InstitutionReleaseSnapshot)))
                release_ids_with_jobs = set(session.scalars(select(
                    KnowledgeMigrationJob.release_snapshot_id,
                ).where(
                    KnowledgeMigrationJob.direction == "export",
                    KnowledgeMigrationJob.release_snapshot_id.is_not(None),
                )))
                pending_package_count = sum(
                    1 for item in releases if item.status == "frozen" and item.id not in release_ids_with_jobs
                )
                publication_statuses = ("frozen", "building", "ready", "failed")
                publication_counts = {
                    status: sum(1 for item in releases if item.status == status)
                    for status in publication_statuses
                }
                package_summary = {
                    "mode": "export",
                    "label": "待导出发布包",
                    "pending_count": pending_package_count,
                    "alert_count": publication_counts["failed"],
                }
            else:
                imports = list(session.scalars(select(KnowledgeMigrationJob).where(
                    KnowledgeMigrationJob.direction == "import",
                )))
                publication_statuses = ("queued", "running", "waiting", "conflict", "completed", "failed")
                publication_counts = {
                    status: sum(1 for item in imports if item.status == status)
                    for status in publication_statuses
                }
                package_summary = {
                    "mode": "import",
                    "label": "待处理导入任务",
                    "pending_count": sum(
                        publication_counts[status] for status in ("queued", "running", "waiting", "conflict")
                    ),
                    "alert_count": publication_counts["failed"],
                }

            return {
                "instance_mode": instance_mode,
                "runtime": {
                    "documents": {"library_count": document_library_count, "file_count": file_count},
                    "tasks": {"active_count": active_job_count, "alert_count": job_alert_count},
                    "vector": {"ready_count": vector_ready_count, "library_count": len(active_libraries)},
                    "packages": package_summary,
                },
                "knowledge_assets": knowledge_assets,
                "production": {
                    "document_library_count": document_library_count,
                    "active_template_binding_count": active_template_binding_count,
                    "active_job_count": active_job_count,
                    "knowledge_item_count": active_knowledge_count,
                    "vector_ready_count": vector_ready_count,
                    "vector_library_count": len(active_libraries),
                },
                "publication": {"mode": package_summary["mode"], "status_counts": publication_counts},
            }

    def get_knowledge_library(self, library_id: str) -> KnowledgeLibrary:
        with self.sessions() as session:
            item = session.get(KnowledgeLibrary, library_id)
            if not item:
                raise ValueError("知识库不存在")
            return item

    @staticmethod
    def _active_jobs_for_library(session: Session, library_id: str) -> list[KnowledgeJob]:
        jobs = session.scalars(select(KnowledgeJob).where(
            KnowledgeJob.status.in_(("queued", "running")),
        ).order_by(KnowledgeJob.created_at)).all()
        return [job for job in jobs if library_id in set(
            (job.sink_library_ids or job.output_library_ids or {}).values()
        )]

    def knowledge_library_delete_check(self, library_id: str) -> dict[str, Any]:
        with self.sessions() as session:
            library = session.get(KnowledgeLibrary, library_id)
            if not library or library.status != "active":
                raise ValueError("知识库不存在")
            rows = session.execute(
                select(Project, ProjectTask, ProjectOrgRoute).join(ProjectTask, ProjectTask.project_id == Project.id)
                .join(ProjectDeploymentTask, ProjectDeploymentTask.project_task_id == ProjectTask.id)
                .join(ProjectOrgRoute, ProjectOrgRoute.project_deployment_task_id == ProjectDeploymentTask.id)
                .join(ProjectOrgRouteLibrary, ProjectOrgRouteLibrary.project_org_route_id == ProjectOrgRoute.id)
                .where(ProjectOrgRouteLibrary.knowledge_library_id == library_id)
                .order_by(Project.code, ProjectTask.code, ProjectOrgRoute.org_code)
            ).all()
            references = [{"project_id": project.id, "project_code": project.code, "project_name": project.name,
                           "task_code": task.code, "task_name": task.name, "org_code": route.org_code,
                           "route_status": route.status} for project, task, route in rows]
            binding_rows = session.execute(
                select(DocumentLibrary, KnowledgeFlowTemplate, DocumentLibraryTemplateBinding,
                       DocumentLibraryTemplateOutput)
                .join(DocumentLibraryTemplateBinding, DocumentLibraryTemplateBinding.document_library_id == DocumentLibrary.id)
                .join(DocumentLibraryTemplateOutput, DocumentLibraryTemplateOutput.document_library_template_binding_id == DocumentLibraryTemplateBinding.id)
                .join(KnowledgeFlowTemplate, KnowledgeFlowTemplate.id == DocumentLibraryTemplateBinding.knowledge_flow_template_id)
                .where(DocumentLibraryTemplateOutput.knowledge_library_id == library_id, DocumentLibraryTemplateBinding.status == "active")
            ).all()
            binding_references = [{"document_library_id": doc.id, "document_library_name": doc.name,
                                    "template_id": template.id, "template_code": template.code,
                                    "template_name": template.name, "binding_id": binding.id,
                                    "output_key": output.output_key, "knowledge_type": output.knowledge_type,
                                    "graph_mode": output.graph_mode}
                                   for doc, template, binding, output in binding_rows]
            active_jobs = self._active_jobs_for_library(session, library_id)
            active_job_references = [{"job_id": job.id, "status": job.status,
                                      "document_library_template_binding_id": job.document_library_template_binding_id,
                                      "created_at": job.created_at.isoformat()}
                                     for job in active_jobs]
            return {"knowledge_library_id": library.id, "status": library.status,
                    "deletable": library.status == "active" and not references and not active_job_references,
                    "references": references, "active_job_references": active_job_references,
                    "template_binding_references": binding_references}

    def knowledge_library_deletion_preflight(self, library_ids: list[str]) -> dict[str, Any]:
        if not library_ids:
            raise ValueError("未选择知识库")
        results = []
        for lid in dict.fromkeys(library_ids):
            try:
                results.append(self.knowledge_library_delete_check(lid))
            except ValueError as exc:
                results.append({"knowledge_library_id": lid, "deletable": False, "error": str(exc),
                                "references": [], "active_job_references": [], "template_binding_references": []})
        return {"deletable": all(r.get("deletable") for r in results),
                "library_count": len(results), "results": results}

    def request_knowledge_library_deletion(self, library_id: str) -> dict[str, Any]:
        with self.sessions.begin() as session:
            library = session.scalar(select(KnowledgeLibrary).where(KnowledgeLibrary.id == library_id).with_for_update())
            if not library:
                raise ValueError("知识库不存在")
            references = session.scalar(select(func.count()).select_from(ProjectOrgRouteLibrary).where(
                ProjectOrgRouteLibrary.knowledge_library_id == library_id,
            )) or 0
            active_jobs = self._active_jobs_for_library(session, library_id)
            if references:
                raise ValueError("知识库仍被项目路由引用，不能删除")
            if active_jobs:
                raise ValueError("知识库仍有排队或运行中的处理任务，不能删除")
            if library.status == "deleted":
                raise ValueError("知识库已经删除")
            queued = session.scalar(select(KnowledgeLibraryDeletionJob).where(
                KnowledgeLibraryDeletionJob.knowledge_library_id == library.id,
                KnowledgeLibraryDeletionJob.status.in_(("queued", "running", "failed")),
            ).order_by(KnowledgeLibraryDeletionJob.created_at.desc()))
            if queued:
                return {"id": queued.id, "knowledge_library_id": library.id, "status": queued.status, "idempotent": True}
            bindings = session.scalars(select(DocumentLibraryTemplateBinding).join(
                DocumentLibraryTemplateOutput,
                DocumentLibraryTemplateOutput.document_library_template_binding_id == DocumentLibraryTemplateBinding.id,
            ).where(
                DocumentLibraryTemplateOutput.knowledge_library_id == library_id,
                DocumentLibraryTemplateBinding.status == "active",
            ).with_for_update()).all()
            for binding in bindings:
                # Keep the output association for audit/recreation, but force
                # the next explicit processing request to rebuild all current
                # source versions for this template revision.
                binding.last_successful_revision_id = None
            library.status = "deleting"
            job = KnowledgeLibraryDeletionJob(id=new_id("kldel"), knowledge_library_id=library.id)
            session.add(job); self.audit(session, "knowledge_library.deletion_queued", "knowledge_library", library.id, {
                "job_id": job.id, "retained_template_binding_ids": [binding.id for binding in bindings],
            })
            return {"id": job.id, "knowledge_library_id": library.id, "status": job.status}

    def request_knowledge_library_deletions(self, library_ids: list[str]) -> dict[str, Any]:
        if not library_ids:
            raise ValueError("未选择知识库")
        targets = list(dict.fromkeys(library_ids))
        with self.sessions.begin() as session:
            libraries = session.scalars(select(KnowledgeLibrary).where(
                KnowledgeLibrary.id.in_(targets)).with_for_update()).all()
            by_id = {lib.id: lib for lib in libraries}
            blocked = []
            for lid in targets:
                lib = by_id.get(lid)
                if not lib:
                    blocked.append((lid, "知识库不存在")); continue
                if lib.status == "deleted":
                    blocked.append((lib.name or lid, "知识库已经删除")); continue
                refs = session.scalar(select(func.count()).select_from(ProjectOrgRouteLibrary).where(
                    ProjectOrgRouteLibrary.knowledge_library_id == lid)) or 0
                if refs:
                    blocked.append((lib.name or lid, "仍被项目路由引用")); continue
                if self._active_jobs_for_library(session, lid):
                    blocked.append((lib.name or lid, "仍有排队或运行中的处理任务")); continue
            if blocked:
                names = "、".join(name for name, _ in blocked)
                raise ValueError(f"以下知识库暂不能删除（{names}），请取消勾选后再试")
            results = []
            for lid in targets:
                lib = by_id[lid]
                queued = session.scalar(select(KnowledgeLibraryDeletionJob).where(
                    KnowledgeLibraryDeletionJob.knowledge_library_id == lid,
                    KnowledgeLibraryDeletionJob.status.in_(("queued", "running", "failed")),
                ).order_by(KnowledgeLibraryDeletionJob.created_at.desc()))
                if queued:
                    results.append({"id": queued.id, "knowledge_library_id": lid,
                                    "status": queued.status, "idempotent": True}); continue
                bindings = session.scalars(select(DocumentLibraryTemplateBinding).join(
                    DocumentLibraryTemplateOutput,
                    DocumentLibraryTemplateOutput.document_library_template_binding_id == DocumentLibraryTemplateBinding.id,
                ).where(
                    DocumentLibraryTemplateOutput.knowledge_library_id == lid,
                    DocumentLibraryTemplateBinding.status == "active",
                ).with_for_update()).all()
                for binding in bindings:
                    binding.last_successful_revision_id = None
                lib.status = "deleting"
                job = KnowledgeLibraryDeletionJob(id=new_id("kldel"), knowledge_library_id=lid)
                session.add(job)
                self.audit(session, "knowledge_library.deletion_queued", "knowledge_library", lid, {
                    "job_id": job.id, "retained_template_binding_ids": [b.id for b in bindings],
                })
                results.append({"id": job.id, "knowledge_library_id": lid, "status": job.status})
        return {"deleted_count": sum(1 for r in results if not r.get("idempotent")), "results": results}

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
            session.execute(delete(KnowledgeAssetItem).where(KnowledgeAssetItem.asset_version_id.in_(
                select(KnowledgeAssetVersion.id).where(KnowledgeAssetVersion.knowledge_library_id == library.id))))
            for item in session.scalars(select(KnowledgeItem).where(KnowledgeItem.knowledge_library_id == library.id, KnowledgeItem.status == "active")):
                item.status = "inactive"
            for item in session.scalars(select(VectorRecordState).join(KnowledgeItem).where(KnowledgeItem.knowledge_library_id == library.id)):
                item.status, item.error = "deleted", None
            job.status, job.error, job.lease_owner, job.lease_expires_at = "deleted", None, None, None
            self.audit(session, "knowledge_library.deleted", "knowledge_library", library.id, {"job_id": job.id})
            return {"id": job.id, "status": job.status, "knowledge_library_id": library.id}

    @staticmethod
    def _normalise_template_definition(definition: dict[str, Any], output_types: list[str]) -> dict[str, Any]:
        """Advanced definitions are explicit DSL; never rebuild a template here."""
        value = dict(definition or {})
        if "steps" in value:
            raise ValueError("高级编排必须提交完整 Flow DSL，不接受 steps 或自动套用内置模板")
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
        return published_subflows(session)

    @staticmethod
    def _schema_defaults(schema: dict[str, Any], values: dict[str, Any]) -> dict[str, Any]:
        result = dict(values)
        for key, spec in dict(schema.get("properties") or {}).items():
            if key not in result and "default" in spec:
                result[key] = json.loads(json.dumps(spec["default"], ensure_ascii=False))
        return result

    @staticmethod
    def _asset_matches_knowledge_type(scopes: list[str] | None, knowledge_type: str) -> bool:
        values = set(scopes or ["*"])
        return "*" in values or knowledge_type in values

    def _normalize_flow_parameters(self, session: Session, definition: dict[str, Any],
                                   *, previous_definition: dict[str, Any] | None = None,
                                   llm_registry=None) -> dict[str, Any]:
        """Normalize one authoring DAG while keeping parameter_schema as the edit allowlist."""
        value = json.loads(json.dumps(definition or {}, ensure_ascii=False))
        nodes = [item for item in value.get("nodes", []) if isinstance(item, dict)]
        by_id = {str(item.get("id")): item for item in nodes}
        outgoing: dict[str, list[str]] = {node_id: [] for node_id in by_id}
        for raw in value.get("edges", []):
            source = str(raw[0] if isinstance(raw, list) else raw.get("source", ""))
            target = str(raw[1] if isinstance(raw, list) else raw.get("target", ""))
            if source in outgoing and target in by_id:
                outgoing[source].append(target)
        sinks = {node_id: item for node_id, item in by_id.items() if item.get("kind") == "knowledge_sink"}

        reach_cache: dict[str, set[str]] = {}
        def reachable_sinks(node_id: str, trail: frozenset[str] = frozenset()) -> set[str]:
            if node_id in reach_cache:
                return reach_cache[node_id]
            if node_id in trail:
                return set()
            result = {node_id} if node_id in sinks else set()
            for target in outgoing.get(node_id, []):
                result.update(reachable_sinks(target, trail | {node_id}))
            reach_cache[node_id] = result
            return result

        catalog = load_catalog(session)
        for node_id, node in by_id.items():
            if node.get("kind") != "operator":
                continue
            operator = resolve_operator(catalog, node)
            if not operator:
                continue
            node["operator_version"] = operator["version"]
            schema = dict(operator.get("parameter_schema") or {"type": "object"})
            incoming = node.get("params")
            if incoming is not None and not isinstance(incoming, dict):
                raise FlowParameterError("PARAMETER_SCHEMA_INVALID", "算子参数必须是对象", node_id=node_id)
            incoming = dict(incoming or {})
            contexts = []
            for sink_id in sorted(reachable_sinks(node_id)):
                sink = sinks[sink_id]
                kind = str(sink.get("knowledge_type") or "")
                mode = str(sink.get("graph_mode") or "") or None
                contexts.append((sink_id, kind, mode))
            expected_system: dict[str, Any] = {}
            if len(contexts) == 1:
                _, kind, mode = contexts[0]
                if kind:
                    expected_system["knowledge_type"] = kind
                    expected_system["graph_mode"] = mode

            if node.get("ref") in {"entity-extractor", "entity-relation-extractor"} and "entity_type_scope" not in incoming:
                incoming["entity_type_scope"] = "subset" if incoming.get("entity_types") else "all"
            final = validate_parameters(schema, incoming, node_id=node_id, system=expected_system)
            if node.get("ref") == "PromptedFilter" and final["min_score"] > final["max_score"]:
                raise FlowParameterError("PARAMETER_SCHEMA_INVALID", "最低保留分不能高于最高保留分", node_id=node_id, field="min_score")

            serving_id = final.get("llm_serving")
            if serving_id:
                try:
                    serving = (llm_registry or self.llm_serving_registry).require(str(serving_id))
                except ValueError as exc:
                    code = "SERVING_DISABLED" if "停用" in str(exc) else "SERVING_NOT_FOUND"
                    raise FlowParameterError(code, str(exc), node_id=node_id, field="llm_serving") from exc
                if serving.type != "openai-compatible-chat":
                    raise FlowParameterError("SERVING_CAPABILITY_MISMATCH", "算子需要 LLM Chat Serving",
                                             node_id=node_id, field="llm_serving")

            prompt_id = final.get("prompt_template_revision_id")
            if prompt_id:
                revision = session.get(PromptTemplateRevision, str(prompt_id))
                if not revision or revision.status != "published":
                    raise FlowParameterError("PROMPT_REVISION_NOT_PUBLISHED", "Prompt Generator 引用的 Prompt Revision 不存在或未发布",
                                             node_id=node_id, field="prompt_template_revision_id")
                knowledge_type = str(final.get("knowledge_type") or "")
                if knowledge_type and not self._asset_matches_knowledge_type(revision.knowledge_types, knowledge_type):
                    raise FlowParameterError("PROMPT_KNOWLEDGE_TYPE_MISMATCH", "Prompt Revision 与 Flow 知识类型不匹配",
                                             node_id=node_id, field="prompt_template_revision_id")

            node["params"] = final
        return value

    def _compile_template_definition(self, session: Session, definition: dict[str, Any], output_types: list[str] | None,
                                     *, purpose: str = "knowledge", require_serving_health: bool = False, llm_registry=None,
                                     authoring_mode: str = "advanced",
                                     previous_definition: dict[str, Any] | None = None) -> dict[str, Any]:
        if authoring_mode == "standard":
            code = (definition or {}).get("template_code")
            output_types = assert_normalized_output_types_match_managed_template(code, output_types)
            normalized = FLOW_AUTHORING_COMPILER.materialize(definition, output_types)
        else:
            if purpose == "knowledge" and not output_types:
                raise FlowParameterError("OUTPUT_TYPES_REQUIRED", "高级编排必须指定非空输出知识类型", field="output_types")
            normalized = self._normalise_template_definition(definition, output_types)
        previous_materialized = None
        if previous_definition:
            previous_materialized = (FLOW_AUTHORING_COMPILER.materialize(previous_definition)
                                     if authoring_mode == "standard" else previous_definition)
        clean_removed_entity_references(normalized, previous_materialized)
        registry = llm_registry or self.llm_serving_registry
        normalized = self._normalize_flow_parameters(session, normalized,
                                                     previous_definition=previous_materialized,
                                                     llm_registry=registry)
        normalized["purpose"] = purpose
        normalized = pin_subflows(normalized, self._published_subflows(session))
        if "graph" in {output_contract(value)[0] for value in output_types}:
            graph_schema = normalize_graph_config(normalized.get("graph_config"))
            normalized["graph_config"] = graph_schema.to_dict()
            for node in normalized.get("nodes", []):
                params = node.get("params") or {}
                if node.get("ref") in {"entity-extractor", "entity-relation-extractor"} and params.get("entity_type_scope") == "subset":
                    unknown = set(params.get("entity_types") or []) - graph_schema.entity_codes()
                    if unknown:
                        raise FlowParameterError("PARAMETER_SCHEMA_INVALID",
                            "实体类型子集引用了未定义的类型：" + "、".join(sorted(unknown)),
                            node_id=str(node["id"]), field="entity_types")
        try:
            compiled = FlowCompiler(
                catalog=load_catalog(session), subflows=self._published_subflows(session),
                type_revisions=self._published_type_revisions(session),
                llm_serving_registry=registry,
            ).compile(normalized)
        except FlowValidationError as exc:
            if callable(getattr(exc, "payload", None)):
                raise
            raise ValueError(str(exc)) from exc
        declared_sinks = set(compiled["compiled_definition"]["sink_types"].values())
        if purpose == "knowledge" and declared_sinks != {normalise_output_key(value) for value in output_types}:
            raise ValueError("Flow Knowledge Sink 必须与模板输出知识类型完全一致")
        if hasattr(registry, "fingerprint"):
            for dependency in compiled["dependencies"]:
                if dependency.get("kind") == "llm_serving":
                    if isinstance(registry, DatabaseLLMServingRegistry):
                        dependency["fingerprint"] = registry.fingerprint(dependency.get("id"), include_credentials=False)
                        dependency["fingerprint_version"] = 2
                    else:
                        dependency["fingerprint"] = registry.fingerprint(dependency.get("id"))
        for node in compiled["compiled_definition"]["nodes"]:
            if node.get("kind") != "operator":
                continue
            params = node.get("params") or {}
            ref = node.get("ref")
            requirements = (node.get("operator_spec") or {}).get("runtime_requirements") or {}
            validate_runtime_requirements(requirements)
            if requirements.get("uses_llm"):
                from .operators.dataflow import serving_snapshot
                params["_resolved_serving"] = serving_snapshot(registry, params["llm_serving"])
            if require_serving_health and requires_external_runtime(requirements):
                state = self.require_operator_runtime(requirements)
                requirements["environment_digest"] = state["runtime_digest"]
            elif requires_external_runtime(requirements):
                state = self.operator_runtime_status(requirements)
                if state["status"] == "ready":
                    requirements["environment_digest"] = state["runtime_digest"]
            if params.get("prompt_template_revision_id"):
                prompt_id = params.get("prompt_template_revision_id")
                prompt = session.get(PromptTemplateRevision, prompt_id) if prompt_id else None
                if not prompt or prompt.status != "published":
                    raise ValueError("Prompt Generator 只能引用已发布 Prompt Template Revision")
                params["_resolved_prompt_template"] = {
                    "id": prompt.id, "body": prompt.body,
                    "input_schema": prompt.input_schema, "output_schema": prompt.output_schema,
                }
                compiled["dependencies"].append({"kind": "prompt_template_revision", "id": prompt.id})
        frozen_operators = {(node["ref"], node["operator_version"]): node["operator_spec"] for node in compiled["compiled_definition"]["nodes"] if node.get("kind") == "operator"}
        for dependency in compiled["dependencies"]:
            if dependency.get("kind") == "operator":
                dependency["runtime_requirements"] = deepcopy(frozen_operators[(dependency["code"], dependency["version"])]["runtime_requirements"])
        if require_serving_health and self.enforce_serving_health and hasattr(registry, "require_healthy"):
            for dependency in compiled["dependencies"]:
                if dependency.get("kind") == "llm_serving":
                    registry.require_healthy(dependency.get("id"))
        compiled["checksum"] = hashlib.sha256(json.dumps(
            compiled["compiled_definition"], ensure_ascii=False, sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")).hexdigest()
        return {"definition": normalized, **compiled}

    @staticmethod
    def _snapshot_checksum(kind: str, revision_id: str, checksum: str) -> str:
        if kind not in {"debug", "published"}:
            raise ValueError("未知 Snapshot 类型")
        return hashlib.sha256(f"v2:{kind}:{revision_id}:{checksum}".encode("utf-8")).hexdigest()

    @staticmethod
    def _published_execution_snapshot(session: Session, revision: KnowledgeFlowTemplateRevision) -> FlowExecutionSnapshot:
        snapshot = session.get(FlowExecutionSnapshot, revision.execution_snapshot_id) if revision.execution_snapshot_id else None
        if (revision.status != "published" or not snapshot or snapshot.status != "published"
                or snapshot.knowledge_flow_template_revision_id != revision.id):
            raise ValueError("正式任务必须绑定正确归属的 Published Revision / Published Execution Snapshot")
        return snapshot

    def _create_execution_snapshot(self, session: Session, revision: KnowledgeFlowTemplateRevision, output_types: list[str],
                                   *, require_serving_health: bool = False, llm_registry=None,
                                   authoring_mode: str | None = None, compiled: dict[str, Any] | None = None,
                                   snapshot_kind: str = "published", bind_revision: bool = True) -> FlowExecutionSnapshot:
        if bind_revision:
            if snapshot_kind != "published":
                raise ValueError("Debug Snapshot 禁止绑定 Revision")
            if revision.status == "published":
                return self._published_execution_snapshot(session, revision)
            if revision.status != "draft" or revision.execution_snapshot_id:
                raise ValueError("REVISION_SNAPSHOT_ALREADY_BOUND")
        authoring_mode = revision.authoring_mode or authoring_mode or "advanced"
        definition = revision.definition_json
        if compiled is None and authoring_mode == "standard":
            template = session.get(KnowledgeFlowTemplate, revision.knowledge_flow_template_id)
            definition = self._standard_revision_definition(template, revision)
        compiled = compiled if compiled is not None else self._compile_template_definition(
            session, definition, output_types, purpose=revision.purpose,
            require_serving_health=require_serving_health,
            llm_registry=llm_registry,
            authoring_mode=authoring_mode,
        )
        snapshot_checksum = self._snapshot_checksum(snapshot_kind, revision.id, compiled["checksum"])
        snapshot = session.scalar(select(FlowExecutionSnapshot).where(FlowExecutionSnapshot.checksum == snapshot_checksum))
        if snapshot and (snapshot.status != snapshot_kind or snapshot.knowledge_flow_template_revision_id != revision.id):
            raise ValueError("SNAPSHOT_IDENTITY_MISMATCH")
        if not snapshot:
            snapshot = FlowExecutionSnapshot(
                id=new_id("flowsnap"), knowledge_flow_template_revision_id=revision.id,
                compiled_definition_json=deepcopy(compiled["compiled_definition"]),
                dependency_json={"dependencies": deepcopy(compiled["dependencies"]), "source_checksum": compiled["checksum"]},
                checksum=snapshot_checksum, status=snapshot_kind,
            )
            session.add(snapshot); session.flush()
        if bind_revision:
            revision.execution_snapshot_id = snapshot.id
        return snapshot

    def _published_template_revision(self, session: Session, template_id: str) -> tuple[KnowledgeFlowTemplate, KnowledgeFlowTemplateRevision]:
        template = session.get(KnowledgeFlowTemplate, template_id)
        if not template or template.status != "active":
            raise ValueError("知识流程模板不存在或不可用")
        if template.needs_review_upgrade:
            raise ReviewGateError(
                "TEMPLATE_REVIEW_UPGRADE_REQUIRED",
                "自定义模板必须升级为 Reviewed SourceChunk Input 后重新发布",
            )
        revision = session.scalar(
            select(KnowledgeFlowTemplateRevision).where(
                KnowledgeFlowTemplateRevision.knowledge_flow_template_id == template.id,
                KnowledgeFlowTemplateRevision.status == "published",
            ).order_by(KnowledgeFlowTemplateRevision.revision_no.desc())
        )
        if not revision:
            raise ValueError("知识流程模板没有已发布修订")
        self._published_execution_snapshot(session, revision)
        return template, revision

    @staticmethod
    def _revision_output_types(session: Session, revision: KnowledgeFlowTemplateRevision) -> list[str]:
        snapshot = V7Store._published_execution_snapshot(session, revision)
        return sorted(set(snapshot.compiled_definition_json["sink_types"].values()))

    def list_flow_templates(self) -> list[dict[str, Any]]:
        with self.sessions() as session:
            values = []
            for item in session.scalars(select(KnowledgeFlowTemplate).where(
                KnowledgeFlowTemplate.status != "archived",
                KnowledgeFlowTemplate.purpose == "knowledge",
            ).order_by(KnowledgeFlowTemplate.code)):
                revision = session.scalar(select(KnowledgeFlowTemplateRevision).where(
                    KnowledgeFlowTemplateRevision.knowledge_flow_template_id == item.id,
                ).order_by(KnowledgeFlowTemplateRevision.revision_no.desc()))
                published_revision = revision if revision and revision.status == "published" else session.scalar(
                    select(KnowledgeFlowTemplateRevision).where(
                        KnowledgeFlowTemplateRevision.knowledge_flow_template_id == item.id,
                        KnowledgeFlowTemplateRevision.status == "published",
                    ).order_by(KnowledgeFlowTemplateRevision.revision_no.desc())
                )
                current_mode = (revision.authoring_mode if revision else item.authoring_mode) or "advanced"
                current_managed_code = (
                    (revision.managed_template_code if revision else item.managed_template_code)
                    if current_mode == "standard" else None
                )
                values.append({"id": item.id, "code": item.code, "name": item.name,
                               "description": item.description,
                               "is_builtin": item.code in V7_BUILTIN_TEMPLATE_CODES, "output_types": item.output_types,
                               "authoring_mode": current_mode,
                               "managed_template_code": current_managed_code,
                               "definition": revision.definition_json if revision else item.definition_json,
                               "source_definition_checksum": self._definition_checksum(revision.definition_json if revision else item.definition_json),
                               "status": item.status, "is_default": item.is_default,
                               "purpose": item.purpose, "needs_review_upgrade": item.needs_review_upgrade,
                               "revision": revision.revision_no if revision else None,
                               "revision_id": revision.id if revision else None,
                               "revision_status": revision.status if revision else None,
                               "published_revision_id": published_revision.id if published_revision else None,
                               "published_revision": published_revision.revision_no if published_revision else None,
                               "execution_snapshot_id": revision.execution_snapshot_id if revision else None,
                               "derived_from_template_id": item.derived_from_template_id,
                               "derived_from_revision_id": item.derived_from_revision_id})
            return values

    def source_preparation_chunker(self) -> dict[str, Any]:
        with self.sessions() as session:
            template = session.scalar(select(KnowledgeFlowTemplate).where(
                KnowledgeFlowTemplate.code == "source-preparation",
                KnowledgeFlowTemplate.purpose == "source_preparation",
            ))
            revision = session.scalar(select(KnowledgeFlowTemplateRevision).where(
                KnowledgeFlowTemplateRevision.knowledge_flow_template_id == template.id,
                KnowledgeFlowTemplateRevision.status == "published",
            ).order_by(KnowledgeFlowTemplateRevision.revision_no.desc())) if template else None
            snapshot = session.get(FlowExecutionSnapshot, revision.execution_snapshot_id) if revision and revision.execution_snapshot_id else None
            node = next((item for item in (snapshot.compiled_definition_json or {}).get("nodes", [])
                         if item.get("ref") == "semantic-chunker"), None) if snapshot else None
            if not template or not revision or not snapshot or not node:
                raise ValueError("Source Preparation Chunker 尚未发布")
            return {"revision": revision.revision_no, "execution_snapshot_id": snapshot.id,
                    "operator_code": "semantic-chunker", "params": normalize_chunker_params(node.get("params"))}

    def source_preparation_preview_document(self, source_version_id: str) -> dict[str, Any]:
        """Return an already parsed document for a read-only rechunk preview."""
        with self.sessions() as session:
            version = session.get(SourceVersion, source_version_id)
            source = session.get(Source, version.source_id) if version else None
            document = session.scalar(select(DocumentIR).where(
                DocumentIR.source_version_id == source_version_id,
                DocumentIR.status == "completed",
            ).order_by(DocumentIR.created_at.desc())) if version else None
            if not version or not source:
                raise ValueError("业务文档版本不存在")
            if not document or not document.text:
                raise ValueError("业务文档尚无可用于预览的 DocumentIR")
            return {
                "type": "source_version", "name": source.name, "filename": version.original_filename,
                "text": document.text, "source_version_id": version.id,
                "anchor": dict(document.anchor_json or {}),
            }

    def create_source_preparation_chunker_revision(self, base_revision: int,
                                                   params: dict[str, Any]) -> dict[str, Any]:
        normalized = normalize_chunker_params(params)
        with self.sessions.begin() as session:
            template = session.scalar(select(KnowledgeFlowTemplate).where(
                KnowledgeFlowTemplate.code == "source-preparation",
                KnowledgeFlowTemplate.purpose == "source_preparation",
            ).with_for_update())
            latest = session.scalar(select(KnowledgeFlowTemplateRevision).where(
                KnowledgeFlowTemplateRevision.knowledge_flow_template_id == template.id,
                KnowledgeFlowTemplateRevision.status == "published",
            ).order_by(KnowledgeFlowTemplateRevision.revision_no.desc())) if template else None
            if not template or not latest:
                raise ValueError("Source Preparation 系统流程未完成初始化")
            if latest.revision_no != base_revision:
                raise ReviewGateError("STALE_PREPARATION_REVISION", "Source Preparation 已有新 Revision，请刷新后重试")
            definition = json.loads(json.dumps(latest.definition_json or preparation_flow_definition(), ensure_ascii=False))
            node = next((item for item in definition.get("nodes", [])
                         if item.get("ref") == "knowledge-chunk" or item.get("ref") == "semantic-chunker"), None)
            if not node:
                raise ValueError("Source Preparation Definition 缺少 semantic-chunker")
            node["params"] = normalized
            revision = KnowledgeFlowTemplateRevision(
                id=new_id("flowrev"), knowledge_flow_template_id=template.id,
                revision_no=latest.revision_no + 1, definition_json=definition,
                purpose="source_preparation", status="draft",
            )
            session.add(revision); session.flush()
            snapshot = self._create_execution_snapshot(session, revision, [])
            revision.status, revision.published_at = "published", utc_now()
            template.definition_json, template.status = definition, "active"
            self.audit(session, "source_preparation.chunker_revision_published", "knowledge_flow_template", template.id,
                       {"revision": revision.revision_no, "execution_snapshot_id": snapshot.id})
            return {"revision": revision.revision_no, "execution_snapshot_id": snapshot.id,
                    "operator_code": "semantic-chunker", "params": normalized}

    @staticmethod
    def _standard_revision_definition(template, revision):
        code = revision.managed_template_code or template.managed_template_code
        if template.managed_template_code and code != template.managed_template_code:
            raise ManagedTemplateError("MANAGED_TEMPLATE_CODE_MISMATCH", "模板与修订的标准模板标识不一致", "managed_template_code")
        assert_normalized_output_types_match_managed_template(code, template.output_types)
        if template.authoring_mode == "standard":
            MANAGED_FLOW_CATALOG.normalize_config(code, template.definition_json)
        return MANAGED_FLOW_CATALOG.normalize_config(code, revision.definition_json)

    def create_flow_template(self, code: str, name: str, output_types: list[str] | None, definition: dict[str, Any],
                             *, authoring_mode: str = "advanced", managed_template_code: str | None = None,
                             description: str = "", derived_from_template_id: str | None = None,
                             derived_from_revision_id: str | None = None) -> dict[str, Any]:
        code, name = code.strip(), name.strip()
        if not code or not name:
            raise ValueError("模板编码、名称和输出知识类型不合法")
        if authoring_mode not in {"standard", "advanced"}:
            raise ValueError("authoring_mode 必须是 standard 或 advanced")
        if authoring_mode == "standard":
            output_types = assert_normalized_output_types_match_managed_template(managed_template_code, output_types)
        elif not output_types:
            raise FlowParameterError("OUTPUT_TYPES_REQUIRED", "高级编排必须指定非空输出知识类型", field="output_types")
        else:
            output_types = sorted(set(output_types))
        with self.sessions.begin() as session:
            if derived_from_template_id or derived_from_revision_id:
                source = session.get(KnowledgeFlowTemplate, derived_from_template_id) if derived_from_template_id else None
                source_revision = session.get(KnowledgeFlowTemplateRevision, derived_from_revision_id) if derived_from_revision_id else None
                if (not source or not source_revision
                        or source_revision.knowledge_flow_template_id != source.id
                        or source.purpose != "knowledge"):
                    raise ValueError("转换来源模板或修订不匹配")
                if not description:
                    description = f"由“{source.name}”r{source_revision.revision_no} 转换生成"
            active_types = self._published_type_revisions(session)
            if {output_contract(value)[0] for value in output_types} - set(active_types):
                raise ValueError("模板引用了未发布知识类型")
            if authoring_mode == "standard":
                if not managed_template_code:
                    raise ValueError("标准配置必须指定 managed_template_code")
                saved = MANAGED_FLOW_CATALOG.normalize_config(managed_template_code, definition)
                defaults = MANAGED_FLOW_CATALOG.default_stage_config(managed_template_code)
                if defaults["stages"]:
                    generation = saved["stages"].setdefault("generation", {})
                    if generation is None:
                        generation = saved["stages"]["generation"] = {}
                    config = generation.get("config") or {}
                    config.setdefault("entity_types", defaults["stages"]["generation"]["config"]["entity_types"])
                    generation["config"] = config
                self._compile_template_definition(session, saved, output_types, purpose="knowledge", authoring_mode="standard")
            else:
                managed_template_code = None
                saved = self._compile_template_definition(session, definition, sorted(set(output_types)), purpose="knowledge", authoring_mode="advanced")["definition"]
            if session.scalar(select(KnowledgeFlowTemplate).where(KnowledgeFlowTemplate.code == code)):
                raise ValueError("模板编码已存在")
            if session.scalar(select(KnowledgeFlowTemplate).where(
                KnowledgeFlowTemplate.name == name, KnowledgeFlowTemplate.status != "archived",
            )):
                raise ValueError("模板名称已存在")
            template = KnowledgeFlowTemplate(id=new_id("flow"), code=code, name=name, description=description.strip(),
                                             output_types=output_types,
                                             definition_json=saved, authoring_mode=authoring_mode, managed_template_code=managed_template_code,
                                             derived_from_template_id=derived_from_template_id,
                                             derived_from_revision_id=derived_from_revision_id,
                                             status="draft", purpose="knowledge")
            session.add(template); session.flush()
            revision = KnowledgeFlowTemplateRevision(id=new_id("flowrev"), knowledge_flow_template_id=template.id, revision_no=1,
                                                     definition_json=saved, authoring_mode=authoring_mode, managed_template_code=managed_template_code,
                                                     status="draft", purpose="knowledge")
            session.add(revision); self.audit(session, "flow_template.created", "knowledge_flow_template", template.id)
            return {"id": template.id, "revision_id": revision.id, "revision_status": revision.status,
                    "revision": revision.revision_no, "status": template.status,
                    "definition": saved, "output_types": output_types,
                    "source_definition_checksum": self._definition_checksum(saved)}

    def update_flow_template(self, template_id: str, name: str, output_types: list[str] | None, definition: dict[str, Any],
                             *, authoring_mode: str | None = None, managed_template_code: str | None = None,
                             expected_definition_checksum: str | None = None) -> dict[str, Any]:
        if not name.strip():
            raise ValueError("模板名称或输出知识类型不合法")
        with self.sessions.begin() as session:
            if self.engine.dialect.name == "sqlite":
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            template = session.get(KnowledgeFlowTemplate, template_id, with_for_update=True)
            if not template or template.status == "archived":
                raise ValueError("模板不存在或已归档")
            if session.scalar(select(KnowledgeFlowTemplate).where(
                KnowledgeFlowTemplate.name == name.strip(),
                KnowledgeFlowTemplate.id != template_id,
                KnowledgeFlowTemplate.status != "archived",
            )):
                raise ValueError("模板名称已存在")
            latest = session.scalar(select(KnowledgeFlowTemplateRevision).where(
                KnowledgeFlowTemplateRevision.knowledge_flow_template_id == template.id,
            ).order_by(KnowledgeFlowTemplateRevision.revision_no.desc()))
            if expected_definition_checksum is not None and expected_definition_checksum != self._definition_checksum(
                latest.definition_json if latest else template.definition_json
            ):
                raise FlowParameterError("STALE_FLOW_DRAFT", "草稿已被其他编辑更新，请重新打开流程后重试", field="definition")
            mode = authoring_mode or template.authoring_mode or "advanced"
            if mode not in {"standard", "advanced"}:
                raise ValueError("authoring_mode 必须是 standard 或 advanced")
            if mode != (latest.authoring_mode if latest else template.authoring_mode):
                raise FlowParameterError("FLOW_AUTHORING_MODE_LOCKED",
                    "流程编辑模式不可原地切换；请将标准流程复制为独立高级流程", field="authoring_mode")
            if mode == "standard":
                managed_template_code = managed_template_code or template.managed_template_code
                output_types = assert_normalized_output_types_match_managed_template(managed_template_code, output_types)
            elif not output_types:
                raise FlowParameterError("OUTPUT_TYPES_REQUIRED", "高级编排必须指定非空输出知识类型", field="output_types")
            else:
                output_types = sorted(set(output_types))
            if {output_contract(value)[0] for value in output_types} - set(self._published_type_revisions(session)):
                raise ValueError("模板引用了未发布知识类型")
            if mode == "standard":
                managed_template_code = managed_template_code or template.managed_template_code
                if not managed_template_code:
                    raise ValueError("标准配置必须指定 managed_template_code")
                saved = MANAGED_FLOW_CATALOG.normalize_config(managed_template_code, definition)
                self._compile_template_definition(session, saved, output_types, purpose="knowledge",
                                                  authoring_mode="standard",
                                                  previous_definition=latest.definition_json if latest and latest.authoring_mode == "standard" else None)
            else:
                managed_template_code = None
                saved = self._compile_template_definition(session, definition, sorted(set(output_types)), purpose="knowledge",
                                                          authoring_mode="advanced",
                                                          previous_definition=latest.definition_json if latest else None)["definition"]
            template.name, template.output_types, template.definition_json = name.strip(), output_types, saved
            template.authoring_mode, template.managed_template_code = mode, managed_template_code
            if latest and latest.status == "draft":
                latest.definition_json = saved
                latest.authoring_mode, latest.managed_template_code = mode, managed_template_code
            else:
                latest = KnowledgeFlowTemplateRevision(id=new_id("flowrev"), knowledge_flow_template_id=template.id,
                    revision_no=(latest.revision_no if latest else 0) + 1, definition_json=saved,
                    authoring_mode=mode, managed_template_code=managed_template_code, status="draft", purpose="knowledge")
                session.add(latest)
            self.audit(session, "flow_template.updated", "knowledge_flow_template", template.id, {"revision": latest.revision_no})
            return {"id": template.id, "revision_id": latest.id, "revision_status": latest.status,
                    "revision": latest.revision_no, "status": latest.status,
                    "definition": saved, "output_types": output_types,
                    "source_definition_checksum": self._definition_checksum(saved)}

    def publish_flow_template(self, template_id: str, *, revision_id: str,
                              expected_definition_checksum: str) -> dict[str, Any]:
        with self.sessions.begin() as session:
            if self.engine.dialect.name == "sqlite":
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            template = session.get(KnowledgeFlowTemplate, template_id, with_for_update=True)
            if not template or template.status == "archived":
                raise ValueError("模板不存在或已归档")
            latest = session.get(KnowledgeFlowTemplateRevision, revision_id, with_for_update=True)
            if not latest or latest.knowledge_flow_template_id != template.id:
                raise ReviewGateError("FLOW_REVISION_CHANGED", "待发布 Revision 已变化，请刷新后重新发布")
            checksum = self._definition_checksum(latest.definition_json)
            if checksum != expected_definition_checksum:
                raise ReviewGateError("STALE_FLOW_DRAFT", "草稿已发生变化，请确认最新内容后重新发布")
            def result(snapshot, *, idempotent):
                return {"id": template.id, "revision_id": latest.id, "revision": latest.revision_no,
                        "revision_status": "published", "status": "published", "source_definition_checksum": checksum,
                        "execution_snapshot_id": snapshot.id, "idempotent": idempotent}
            if latest.status == "published":
                try:
                    snapshot = self._published_execution_snapshot(session, latest)
                except ValueError as exc:
                    raise ReviewGateError("FLOW_REVISION_ALREADY_PUBLISHED", "已发布版本绑定不合法，禁止重编译或重绑定") from exc
                return result(snapshot, idempotent=True)
            current = session.scalar(select(KnowledgeFlowTemplateRevision).where(
                KnowledgeFlowTemplateRevision.knowledge_flow_template_id == template.id,
            ).order_by(KnowledgeFlowTemplateRevision.revision_no.desc()).with_for_update())
            if latest.status != "draft" or not current or latest.id != current.id:
                raise ReviewGateError("FLOW_REVISION_CHANGED", "只能发布当前最新草稿")
            mode = latest.authoring_mode or template.authoring_mode or "advanced"
            definition = self._standard_revision_definition(template, latest) if mode == "standard" else latest.definition_json
            if mode == "standard":
                template.output_types = assert_normalized_output_types_match_managed_template(definition["template_code"], template.output_types)
            compiled = self._compile_template_definition(
                session, definition, template.output_types, purpose=template.purpose,
                require_serving_health=True, authoring_mode=mode,
            )
            snapshot = self._create_execution_snapshot(session, latest, template.output_types,
                compiled=compiled, snapshot_kind="published", bind_revision=True)
            latest.status, latest.published_at, template.status = "published", utc_now(), "active"
            latest.purpose, template.needs_review_upgrade = template.purpose, False
            latest.authoring_mode = mode
            latest.managed_template_code = template.managed_template_code if mode == "standard" else None
            binding_library_ids = list(session.scalars(select(DocumentLibraryTemplateBinding.document_library_id).where(
                DocumentLibraryTemplateBinding.knowledge_flow_template_id == template.id,
                DocumentLibraryTemplateBinding.status == "active",
            )))
            if binding_library_ids:
                for review_snapshot_id in session.scalars(select(SourceVersion.current_review_snapshot_id).join(
                    Source, Source.id == SourceVersion.source_id,
                ).where(
                    Source.document_library_id.in_(binding_library_ids),
                    Source.current_version_id == SourceVersion.id,
                    Source.status == "uploaded",
                    SourceVersion.review_status == "approved",
                    SourceVersion.current_review_snapshot_id.is_not(None),
                )):
                    self._queue_review_dispatch(session, review_snapshot_id)
            self.audit(session, "flow_template.published", "knowledge_flow_template", template.id, {"revision": latest.revision_no})
            return result(snapshot, idempotent=False)

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
            mode = revision.authoring_mode or template.authoring_mode or "advanced"
            definition = self._standard_revision_definition(template, revision) if mode == "standard" else revision.definition_json
            compiled = self._compile_template_definition(session, definition, template.output_types, authoring_mode=mode)
            result = {"valid": True, "template_id": template.id, "revision": revision.revision_no,
                      "authoring_mode": mode,
                      "managed_template_code": revision.managed_template_code or template.managed_template_code,
                      "definition": compiled["definition"], "compiled_definition": compiled["compiled_definition"],
                      "checksum": compiled["checksum"]}
            stages: list[dict[str, Any]] = []
            managed_code = revision.managed_template_code or template.managed_template_code
            if mode == "standard" and managed_code:
                stages = [{"code": stage.code, "name": stage.name, "locked": stage.locked}
                          for stage in MANAGED_FLOW_CATALOG.get(managed_code).stages]
            result["stages"] = stages
            result["issues"] = []
            return result

    @staticmethod
    def _definition_checksum(definition: dict[str, Any]) -> str:
        encoded = json.dumps(definition or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _debug_sink_requirements(compiled_definition: dict[str, Any]) -> dict[str, dict[str, Any]]:
        values: dict[str, dict[str, Any]] = {}
        for node in compiled_definition.get("nodes", []):
            if node.get("kind") != "knowledge_sink":
                continue
            knowledge_type = str(node.get("knowledge_type") or "")
            graph_mode = str(node.get("graph_mode") or "") or None
            output_key = normalise_output_key(str(node.get("output_key") or (
                f"graph:{graph_mode}" if knowledge_type == "graph" and graph_mode else knowledge_type
            )))
            values[output_key] = {
                "output_key": output_key, "knowledge_type": knowledge_type,
                "graph_mode": graph_mode, "node_id": str(node["id"]),
            }
        return values

    def _debug_revision(self, session: Session, template_id: str, *, revision_kind: str | None = None,
                        revision_id: str | None = None, lock: bool = False) -> tuple[KnowledgeFlowTemplate, KnowledgeFlowTemplateRevision]:
        template = session.get(KnowledgeFlowTemplate, template_id, with_for_update=lock)
        if not template or template.status == "archived" or template.purpose != "knowledge":
            raise ValueError("知识流程不存在或不可调试")
        query = select(KnowledgeFlowTemplateRevision).where(
            KnowledgeFlowTemplateRevision.knowledge_flow_template_id == template.id,
        )
        if lock:
            query = query.with_for_update()
        if revision_id:
            revision = session.scalar(query.where(KnowledgeFlowTemplateRevision.id == revision_id))
        else:
            if revision_kind not in {"draft", "published"}:
                raise ValueError("revision_kind 必须是 draft 或 published")
            revision = session.scalar(query.where(
                KnowledgeFlowTemplateRevision.status == revision_kind,
            ).order_by(KnowledgeFlowTemplateRevision.revision_no.desc()))
        if not revision or revision.status not in {"draft", "published"}:
            raise ValueError("所选流程 Revision 不存在或不可调试")
        return template, revision

    def _debug_compile_bundle(self, session: Session, template: KnowledgeFlowTemplate,
                              revision: KnowledgeFlowTemplateRevision, *, require_serving_health: bool) -> dict[str, Any]:
        authoring_mode = revision.authoring_mode or template.authoring_mode or "advanced"
        definition = revision.definition_json if revision.status == "published" else (
            self._standard_revision_definition(template, revision) if authoring_mode == "standard" else revision.definition_json)
        source_definition = deepcopy(definition or {})
        if revision.status == "published":
            snapshot = self._published_execution_snapshot(session, revision)
            self._validate_snapshot_servings(snapshot)
            compiled = {"compiled_definition": deepcopy(snapshot.compiled_definition_json),
                        "dependencies": deepcopy(snapshot.dependency_json.get("dependencies", [])),
                        "checksum": snapshot.dependency_json.get("source_checksum") or self._definition_checksum(snapshot.compiled_definition_json)}
            if require_serving_health:
                for node in compiled["compiled_definition"].get("nodes", []):
                    if node.get("kind") != "operator":
                        continue
                    requirements = (node.get("operator_spec") or {}).get("runtime_requirements") or {}
                    validate_runtime_requirements(requirements)
                    if requires_external_runtime(requirements):
                        self.require_operator_runtime(requirements)
                if self.enforce_serving_health:
                    for dependency in compiled["dependencies"]:
                        if dependency.get("kind") == "llm_serving":
                            self.llm_serving_registry.require_healthy(dependency["id"])
            materialized = compiled["compiled_definition"]
        else:
            compiled = self._compile_template_definition(
                session, source_definition, list(template.output_types), purpose="knowledge",
                require_serving_health=require_serving_health, authoring_mode=authoring_mode,
            )
            materialized = compiled["definition"]
        source_nodes = {str(item["id"]): item for item in materialized.get("nodes", []) if item.get("kind") == "operator"}
        reusable_map: dict[str, dict[str, Any]] = {}
        managed = MANAGED_FLOW_CATALOG.get(revision.managed_template_code or template.managed_template_code) \
            if authoring_mode == "standard" else None
        stages = {stage.code: stage for stage in managed.stages} if managed else {}
        for node in compiled["compiled_definition"].get("nodes", []):
            if node.get("kind") != "operator":
                continue
            node_id = str(node["id"])
            origin_path = list(node.get("origin_path") or node_id.split("::"))
            if len(origin_path) != 1 or node_id not in source_nodes:
                continue
            mapping: dict[str, Any] = {"advanced_source_node_id": node_id}
            if authoring_mode == "standard":
                stage_code = str(node.get("stage_code") or "")
                stage = stages.get(stage_code)
                allowed = sorted(((stage.config_schema or {}).get("properties") or {}).keys()) if stage else []
                mapping.update({"standard_stage_code": stage_code, "standard_allowed_keys": allowed})
            reusable_map[node_id] = mapping
        return {
            "template": template, "revision": revision, "authoring_mode": authoring_mode,
            "source_definition": source_definition,
            "source_definition_checksum": self._definition_checksum(source_definition),
            "compiled": compiled, "reusable_map": reusable_map,
            "sink_requirements": self._debug_sink_requirements(compiled["compiled_definition"]),
        }


    def _debug_review_selection(self, session: Session, snapshot_ids: list[str], *, require_current: bool) -> list[dict[str, Any]]:
        ids = list(dict.fromkeys(str(value) for value in snapshot_ids if value))
        if not ids:
            raise ValueError("至少选择一份当前审核快照")
        values: list[dict[str, Any]] = []
        document_library_ids: set[str] = set()
        for snapshot_id in ids:
            snapshot = session.get(SourceReviewSnapshot, snapshot_id)
            version = session.get(SourceVersion, snapshot.source_version_id) if snapshot else None
            source = session.get(Source, version.source_id) if version else None
            library = session.get(DocumentLibrary, source.document_library_id) if source else None
            if not snapshot or not version or not source or not library:
                raise ValueError("审核快照上下文不完整")
            if snapshot.status != "approved" or version.review_status != "approved":
                raise ValueError("只能使用已批准的审核快照")
            if require_current and (
                version.current_review_snapshot_id != snapshot.id or source.current_version_id != version.id
                or version.status != "active" or source.status != "uploaded"
            ):
                raise RuntimeError("所选审核快照已不是文档当前有效版本，请刷新后重试")
            chunk_count = session.scalar(select(func.count()).select_from(SourceReviewSnapshotChunk).where(
                SourceReviewSnapshotChunk.source_review_snapshot_id == snapshot.id,
            )) or 0
            if not chunk_count:
                raise ValueError("审核快照不包含文档块")
            document_library_ids.add(library.id)
            values.append({
                "snapshot": snapshot, "version": version, "source": source, "library": library,
                "chunk_count": int(chunk_count),
            })
        if len(document_library_ids) != 1:
            raise ValueError("一次 Debug Run 只能选择同一文档库中的审核文档")
        return values

    def _validate_debug_sinks(self, session: Session, requirements: dict[str, dict[str, Any]],
                              bindings: dict[str, str]) -> dict[str, KnowledgeLibrary]:
        normalized = {normalise_output_key(str(key)): str(value) for key, value in dict(bindings or {}).items() if value}
        if set(normalized) != set(requirements):
            raise ValueError("必须为流程的每个 output_key 且仅为这些 output_key 绑定预览知识库")
        values: dict[str, KnowledgeLibrary] = {}
        for output_key, requirement in requirements.items():
            library = session.get(KnowledgeLibrary, normalized[output_key])
            if (not library or library.status != "active" or library.knowledge_type != requirement["knowledge_type"]
                    or (requirement["graph_mode"] and library.graph_mode != requirement["graph_mode"])):
                raise ValueError(f"{output_key} 必须绑定同类型的有效知识库")
            values[output_key] = library
        return values

    def debug_run_options(self, template_id: str, revision_kind: str) -> dict[str, Any]:
        with self.sessions() as session:
            template, revision = self._debug_revision(session, template_id, revision_kind=revision_kind)
            bundle = self._debug_compile_bundle(session, template, revision, require_serving_health=False)
            review_options = []
            snapshots = session.scalars(select(SourceReviewSnapshot).where(
                SourceReviewSnapshot.status == "approved",
            ).order_by(SourceReviewSnapshot.approved_at.desc())).all()
            for snapshot in snapshots:
                try:
                    row = self._debug_review_selection(session, [snapshot.id], require_current=True)[0]
                except (ValueError, RuntimeError):
                    continue
                review_options.append({
                    "source_review_snapshot_id": snapshot.id, "source_version_id": row["version"].id,
                    "source_id": row["source"].id, "source_name": row["source"].name,
                    "filename": row["version"].original_filename, "review_no": snapshot.review_no,
                    "review_digest": snapshot.content_digest, "chunk_count": row["chunk_count"],
                    "approved_at": snapshot.approved_at.isoformat(),
                    "document_library_id": row["library"].id, "document_library_name": row["library"].name,
                })
            sink_options: dict[str, list[dict[str, Any]]] = {}
            for output_key, requirement in bundle["sink_requirements"].items():
                query = select(KnowledgeLibrary).where(
                    KnowledgeLibrary.status == "active", KnowledgeLibrary.knowledge_type == requirement["knowledge_type"],
                ).order_by(KnowledgeLibrary.name)
                candidates = list(session.scalars(query))
                sink_options[output_key] = [
                    {"id": item.id, "name": item.name, "knowledge_type": item.knowledge_type, "graph_mode": item.graph_mode}
                    for item in candidates if not requirement["graph_mode"] or item.graph_mode == requirement["graph_mode"]
                ]
            return {
                "template": {"id": template.id, "name": template.name, "is_builtin": template.code in V7_BUILTIN_TEMPLATE_CODES},
                "revision": {"id": revision.id, "revision": revision.revision_no, "status": revision.status,
                             "authoring_mode": bundle["authoring_mode"]},
                "compiled_checksum": bundle["compiled"]["checksum"],
                "source_definition_checksum": bundle["source_definition_checksum"],
                "review_inputs": review_options,
                "sink_requirements": list(bundle["sink_requirements"].values()), "sink_options": sink_options,
                "builtin_samples": SampleDataService().list("knowledge_flow"),
                "default_input": {"input_source": "builtin_sample", "sample_code": "reviewed-medical-v2"},
            }

    def _debug_preflight_with_session(self, session: Session, *, template_id: str, revision_id: str,
                                      expected_compiled_checksum: str, source_review_snapshot_ids: list[str],
                                      sink_library_bindings: dict[str, str], require_serving_health: bool,
                                      input_source: str = "source_review_snapshot",
                                      sample_code: str | None = None, lock: bool = False) -> dict[str, Any]:
        template, revision = self._debug_revision(session, template_id, revision_id=revision_id, lock=lock)
        bundle = self._debug_compile_bundle(session, template, revision, require_serving_health=require_serving_health)
        if bundle["compiled"]["checksum"] != expected_compiled_checksum:
            raise RuntimeError("流程定义已变化，请重新执行调试预检")
        if input_source == "builtin_sample":
            sample = SampleDataService().reviewed_chunks(sample_code or "reviewed-medical-v2")
            if sink_library_bindings:
                raise ValueError("内置示例使用虚拟空库 Diff，不接受 KnowledgeLibrary 绑定")
            reviews: list[dict[str, Any]] = []
            libraries: dict[str, KnowledgeLibrary] = {}
            targets = {key: {"baseline_kind": "empty", "knowledge_library_id": None}
                       for key in bundle["sink_requirements"]}
            return {**bundle, "reviews": reviews, "libraries": libraries, "targets": targets,
                    "sample": sample, "resolved_chunks": list(sample["chunks"]),
                    "input_descriptor": {"input_source": "builtin_sample", "sample_code": sample["code"],
                                         "sample_version": sample["version"]},
                    "input_digest": sample["input_digest"]}
        if input_source != "source_review_snapshot":
            raise ValueError("input_source 必须是 builtin_sample 或 source_review_snapshot")
        reviews = self._debug_review_selection(session, source_review_snapshot_ids, require_current=True)
        libraries = self._validate_debug_sinks(session, bundle["sink_requirements"], sink_library_bindings)
        targets = {key: {"baseline_kind": "knowledge_library", "knowledge_library_id": library.id}
                   for key, library in libraries.items()}
        return {**bundle, "reviews": reviews, "libraries": libraries, "targets": targets,
                "sample": None, "resolved_chunks": [],
                "input_descriptor": {"input_source": "source_review_snapshot",
                                     "source_review_snapshot_ids": [item["snapshot"].id for item in reviews]},
                "input_digest": hashlib.sha256("|".join(item["snapshot"].content_digest for item in reviews).encode("utf-8")).hexdigest()}

    def debug_run_preflight(self, *, template_id: str, revision_id: str, expected_compiled_checksum: str,
                            source_review_snapshot_ids: list[str], sink_library_bindings: dict[str, str],
                            input_source: str = "source_review_snapshot", sample_code: str | None = None) -> dict[str, Any]:
        with self.sessions() as session:
            value = self._debug_preflight_with_session(
                session, template_id=template_id, revision_id=revision_id,
                expected_compiled_checksum=expected_compiled_checksum,
                source_review_snapshot_ids=source_review_snapshot_ids,
                sink_library_bindings=sink_library_bindings, require_serving_health=False,
                input_source=input_source, sample_code=sample_code,
            )
            input_count, issues = self._semantic_input_preflight(value)
            valid = not any(issue["severity"] == "error" for issue in issues)
            if valid:
                healthy = self._debug_compile_bundle(session, value["template"], value["revision"], require_serving_health=True)
                if healthy["compiled"]["checksum"] != expected_compiled_checksum:
                    raise RuntimeError("流程定义或运行环境已变化，请重新执行调试预检")
            return {
                "valid": valid, "template_id": value["template"].id, "revision_id": value["revision"].id,
                "revision": value["revision"].revision_no, "compiled_checksum": value["compiled"]["checksum"],
                "source_definition_checksum": value["source_definition_checksum"],
                "input_count": input_count,
                "input_source": input_source, "output_keys": sorted(value["targets"]),
                "sink_policy": "preview_only", "issues": issues,
            }

    @staticmethod
    def _semantic_input_preflight(value):
        from .operators.semantic_contract import input_preflight
        counts = [([chunk["source_version_id"]], 1) for chunk in value["resolved_chunks"]] if value["sample"] else [
            ([review["version"].id], review["chunk_count"]) for review in value["reviews"]]
        return sum(count for _, count in counts), input_preflight(value["compiled"]["compiled_definition"], counts)

    def create_debug_run(self, *, template_id: str, revision_id: str, expected_compiled_checksum: str,
                         source_review_snapshot_ids: list[str], sink_library_bindings: dict[str, str],
                         idempotency_key: str, input_source: str = "source_review_snapshot",
                         sample_code: str | None = None) -> dict[str, Any]:
        if not idempotency_key.strip():
            raise ValueError("idempotency_key 不能为空")
        with self.sessions.begin() as session:
            if self.engine.dialect.name == "sqlite":
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            existing_input = session.scalar(select(DebugRunInputSnapshot).where(
                DebugRunInputSnapshot.idempotency_key == idempotency_key,
            ))
            if existing_input:
                existing_run = session.scalar(select(FlowRun).where(
                    FlowRun.debug_input_snapshot_id == existing_input.id,
                    FlowRun.parent_flow_run_id.is_(None), FlowRun.run_mode == "debug_full",
                ))
                if existing_run:
                    return {"id": existing_run.id, "debug_input_snapshot_id": existing_input.id,
                            "execution_snapshot_id": existing_run.execution_snapshot_id,
                            "run_mode": existing_run.run_mode, "status": existing_run.status, "idempotent": True}
            value = self._debug_preflight_with_session(
                session, template_id=template_id, revision_id=revision_id,
                expected_compiled_checksum=expected_compiled_checksum,
                source_review_snapshot_ids=source_review_snapshot_ids,
                sink_library_bindings=sink_library_bindings, require_serving_health=False,
                input_source=input_source, sample_code=sample_code, lock=True,
            )
            _, issues = self._semantic_input_preflight(value)
            blocking = next((issue for issue in issues if issue["severity"] == "error"), None)
            if blocking:
                raise FlowParameterError(blocking["code"], blocking["message"], node_id=blocking["node_id"], field="scope")
            healthy = self._debug_compile_bundle(session, value["template"], value["revision"], require_serving_health=True)
            if healthy["compiled"]["checksum"] != expected_compiled_checksum:
                raise RuntimeError("流程定义或运行环境已变化，请重新执行调试预检")
            revision = value["revision"]
            execution = self._create_execution_snapshot(session, revision, list(value["sink_requirements"]),
                compiled=value["compiled"], snapshot_kind="debug", bind_revision=False)
            debug_input = DebugRunInputSnapshot(
                id=new_id("debuginput"), knowledge_flow_template_id=value["template"].id,
                knowledge_flow_template_revision_id=revision.id, execution_snapshot_id=execution.id,
                authoring_mode=value["authoring_mode"], source_definition_json=value["source_definition"],
                source_definition_checksum=value["source_definition_checksum"],
                output_types_json=sorted(value["sink_requirements"]), reusable_node_map_json=value["reusable_map"],
                sink_library_bindings_json={key: library.id for key, library in value["libraries"].items()},
                input_source=input_source, input_descriptor_json={**value["input_descriptor"],
                    "revision_kind": revision.status, "revision_no": revision.revision_no},
                resolved_chunks_json=list(value["resolved_chunks"]), input_digest=value["input_digest"],
                sink_preview_targets_json=value["targets"],
                requested_by="admin", idempotency_key=idempotency_key,
            )
            session.add(debug_input); session.flush()
            for ordinal, review in enumerate(value["reviews"]):
                session.add(DebugRunReviewInput(
                    id=new_id("debugreview"), debug_input_snapshot_id=debug_input.id,
                    source_version_id=review["version"].id,
                    source_review_snapshot_id=review["snapshot"].id,
                    activation_no=review["version"].activation_no,
                    review_digest=review["snapshot"].content_digest, ordinal=ordinal,
                ))
            session.flush()
            if input_source == "source_review_snapshot":
                resolved_chunks = self._reviewed_chunks_for_debug_session(session, debug_input.id)
                debug_input.resolved_chunks_json = resolved_chunks
                debug_input.input_digest = hashlib.sha256(json.dumps(
                    resolved_chunks, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                ).encode("utf-8")).hexdigest()
            run = FlowRun(
                id=new_id("flowrun"), knowledge_job_id=None, source_preparation_job_id=None,
                debug_input_snapshot_id=debug_input.id, execution_snapshot_id=execution.id,
                run_mode="debug_full", parameter_overrides={}, sink_policy="preview",
                requested_by="admin", idempotency_key=idempotency_key, status="queued",
            )
            session.add(run); session.flush()
            self._append_run_event(session, run.id, "run.queued", "完整调试 Run 已进入队列",
                                   payload={"template_id": value["template"].id, "revision_id": revision.id,
                                             "input_count": len(debug_input.resolved_chunks_json),
                                             "input_source": input_source, "sink_policy": "preview_only"})
            self.audit(session, "debug_run.created", "flow_run", run.id,
                       {"template_id": value["template"].id, "revision_id": revision.id})
            return {"id": run.id, "debug_input_snapshot_id": debug_input.id,
                    "execution_snapshot_id": execution.id, "run_mode": run.run_mode, "status": run.status}

    def _debug_reusable_definition(self, debug_input: DebugRunInputSnapshot, overrides: dict[str, Any],
                                   *, target_mode: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        if target_mode not in {"standard", "advanced"}:
            raise ValueError("目标流程模式不合法")
        source = json.loads(json.dumps(debug_input.source_definition_json or {}, ensure_ascii=False))
        if target_mode == "advanced" and debug_input.authoring_mode == "standard":
            definition = FLOW_AUTHORING_COMPILER.materialize(source, list(debug_input.output_types_json))
        else:
            definition = source
        mappings = dict(debug_input.reusable_node_map_json or {})
        blocked: list[dict[str, Any]] = []
        for runtime_node_id, raw_params in dict(overrides or {}).items():
            params = {key: value for key, value in dict(raw_params or {}).items() if key != "force_ocr"}
            if not params:
                continue
            mapping = dict(mappings.get(runtime_node_id) or {})
            if target_mode == "standard":
                stage_code = mapping.get("standard_stage_code")
                allowed = set(mapping.get("standard_allowed_keys") or [])
                if not stage_code or set(params) - allowed:
                    blocked.append({"node_id": runtime_node_id, "parameters": sorted(params),
                                    "reason": "参数不能映射回标准配置阶段"})
                    continue
                stages = definition.setdefault("stages", {})
                stage = stages.setdefault(stage_code, {})
                stage["config"] = {**dict(stage.get("config") or {}), **params}
                continue
            source_node_id = mapping.get("advanced_source_node_id")
            node = next((item for item in definition.get("nodes", []) if str(item.get("id")) == source_node_id), None)
            if not node or node.get("kind") != "operator":
                blocked.append({"node_id": runtime_node_id, "parameters": sorted(params),
                                "reason": "展开子图内部节点不能写回父流程"})
                continue
            node["params"] = {**dict(node.get("params") or {}), **params}
        runtime_only_force_ocr = [
            {"node_id": node_id, "parameters": ["force_ocr"], "reason": "force_ocr 仅属于本次运行"}
            for node_id, params in dict(overrides or {}).items() if "force_ocr" in dict(params or {})
        ]
        return definition, [*blocked, *runtime_only_force_ocr]

    def debug_run_materialization(self, flow_run_id: str) -> dict[str, Any]:
        with self.sessions() as session:
            run = session.get(FlowRun, flow_run_id)
            debug_input = session.get(DebugRunInputSnapshot, run.debug_input_snapshot_id) if run and run.debug_input_snapshot_id else None
            if not run or not debug_input:
                raise ValueError("调试 Run 不存在")
            template = session.get(KnowledgeFlowTemplate, debug_input.knowledge_flow_template_id)
            revision = session.get(KnowledgeFlowTemplateRevision, debug_input.knowledge_flow_template_revision_id)
            latest = session.scalar(select(KnowledgeFlowTemplateRevision).where(
                KnowledgeFlowTemplateRevision.knowledge_flow_template_id == template.id,
            ).order_by(KnowledgeFlowTemplateRevision.revision_no.desc())) if template else None
            _, blocked_advanced = self._debug_reusable_definition(
                debug_input, dict(run.parameter_overrides or {}), target_mode="advanced",
            )
            _, blocked_current = self._debug_reusable_definition(
                debug_input, dict(run.parameter_overrides or {}), target_mode=debug_input.authoring_mode,
            )
            completed = run.status == "completed"
            can_apply = bool(
                completed and template and revision and latest and latest.id == revision.id
                and revision.status == "draft" and template.code not in V7_BUILTIN_TEMPLATE_CODES
                and self._definition_checksum(revision.definition_json) == debug_input.source_definition_checksum
                and not blocked_current
            )
            return {
                "run_id": run.id, "status": run.status,
                "source": {"template_id": template.id if template else None, "template_name": template.name if template else None,
                           "revision_id": revision.id if revision else None, "revision": revision.revision_no if revision else None,
                           "revision_status": revision.status if revision else None,
                           "authoring_mode": debug_input.authoring_mode,
                           "definition_checksum": debug_input.source_definition_checksum},
                "effective_parameter_overrides": dict(run.parameter_overrides or {}),
                "runtime_only_overrides": blocked_advanced,
                "can_apply_to_current_draft": can_apply,
                "apply_blockers": [] if can_apply else blocked_current or [
                    "仅成功完成且来源仍是未变化的当前自定义草稿时可应用"
                ],
                "can_save_as_flow": completed and not blocked_advanced,
                "save_blockers": blocked_advanced if blocked_advanced else ([] if completed else ["仅成功完成的调试 Run 可另存流程"]),
                "saved_content": ["Operator DAG", "节点连线", "可复用参数", "Prompt/模型/质量配置引用", "输出契约"],
                "excluded_content": ["审核输入", "KnowledgeLibrary 绑定", "Artifact", "日志与指标", "Sink Preview", "运行状态"],
            }

    def _apply_debug_run_to_draft_once(self, flow_run_id: str, *, expected_revision_id: str,
                                       expected_definition_checksum: str, idempotency_key: str) -> dict[str, Any]:
        with self.sessions.begin() as session:
            existing = session.scalar(select(DebugRunFlowMaterialization).where(
                DebugRunFlowMaterialization.idempotency_key == idempotency_key,
            ))
            if existing:
                if existing.flow_run_id != flow_run_id or existing.action != "apply_to_draft":
                    raise ValueError("流程物化幂等键已用于其他操作")
                return {**dict(existing.result_json), "idempotent": True}
            run = session.get(FlowRun, flow_run_id)
            debug_input = session.get(DebugRunInputSnapshot, run.debug_input_snapshot_id) if run and run.debug_input_snapshot_id else None
            if not run or not debug_input or run.status != "completed":
                raise ValueError("只有成功完成的调试 Run 可以应用到草稿")
            template = session.get(KnowledgeFlowTemplate, debug_input.knowledge_flow_template_id)
            revision = session.get(KnowledgeFlowTemplateRevision, debug_input.knowledge_flow_template_revision_id)
            latest = session.scalar(select(KnowledgeFlowTemplateRevision).where(
                KnowledgeFlowTemplateRevision.knowledge_flow_template_id == debug_input.knowledge_flow_template_id,
            ).order_by(KnowledgeFlowTemplateRevision.revision_no.desc()).with_for_update())
            if (not template or not revision or not latest or template.code in V7_BUILTIN_TEMPLATE_CODES
                    or latest.id != revision.id or revision.id != expected_revision_id or revision.status != "draft"):
                raise RuntimeError("来源已不是当前可编辑的自定义草稿")
            current_checksum = self._definition_checksum(revision.definition_json)
            if current_checksum != expected_definition_checksum or current_checksum != debug_input.source_definition_checksum:
                raise RuntimeError("当前草稿已变化，请重新调试后再应用")
            definition, blocked = self._debug_reusable_definition(
                debug_input, dict(run.parameter_overrides or {}), target_mode=debug_input.authoring_mode,
            )
            if blocked:
                raise RuntimeError("存在不能写回源草稿的运行参数：" + "；".join(item["reason"] for item in blocked))
            compiled = self._compile_template_definition(
                session, definition, list(debug_input.output_types_json), purpose="knowledge",
                authoring_mode=debug_input.authoring_mode,
            )
            revision.definition_json = definition
            template.definition_json = definition
            self.audit(session, "debug_run.applied_to_draft", "knowledge_flow_template", template.id,
                       {"revision_id": revision.id, "flow_run_id": run.id, "compiled_checksum": compiled["checksum"]})
            result = {"id": template.id, "revision_id": revision.id, "revision": revision.revision_no,
                      "status": "draft", "definition_checksum": self._definition_checksum(definition),
                      "open_url": f"/developer/flow-templates?template_id={template.id}&edit=1"}
            session.add(DebugRunFlowMaterialization(
                id=new_id("debugmaterial"), flow_run_id=run.id, action="apply_to_draft",
                idempotency_key=idempotency_key, target_template_id=template.id,
                target_revision_id=revision.id, result_json=result,
            ))
            return result

    def _save_debug_run_as_flow_once(self, flow_run_id: str, *, name: str, description: str = "",
                                     idempotency_key: str) -> dict[str, Any]:
        if not name.strip():
            raise ValueError("流程名称不能为空")
        with self.sessions.begin() as session:
            existing = session.scalar(select(DebugRunFlowMaterialization).where(
                DebugRunFlowMaterialization.idempotency_key == idempotency_key,
            ))
            if existing:
                if existing.flow_run_id != flow_run_id or existing.action != "save_as_flow":
                    raise ValueError("流程物化幂等键已用于其他操作")
                return {**dict(existing.result_json), "idempotent": True}
            run = session.get(FlowRun, flow_run_id)
            debug_input = session.get(DebugRunInputSnapshot, run.debug_input_snapshot_id) if run and run.debug_input_snapshot_id else None
            if not run or not debug_input or run.status != "completed":
                raise ValueError("只有成功完成的调试 Run 可以另存流程")
            source_template_id = debug_input.knowledge_flow_template_id
            source_revision_id = debug_input.knowledge_flow_template_revision_id
            output_types = list(debug_input.output_types_json)
            definition, blocked = self._debug_reusable_definition(
                debug_input, dict(run.parameter_overrides or {}), target_mode="advanced",
            )
            if blocked:
                raise RuntimeError("存在不能保存为流程的运行参数：" + "；".join(item["reason"] for item in blocked))
            compiled = self._compile_template_definition(
                session, definition, output_types, purpose="knowledge", authoring_mode="advanced",
            )
            if session.scalar(select(KnowledgeFlowTemplate.id).where(
                KnowledgeFlowTemplate.name == name.strip(), KnowledgeFlowTemplate.status != "archived",
            )):
                raise ValueError("模板名称已存在")
            date_part = utc_now().strftime("%Y%m%d")
            code = ""
            for _ in range(32):
                candidate = f"custom-{date_part}-{secrets.token_hex(3)}"
                if not session.scalar(select(KnowledgeFlowTemplate.id).where(KnowledgeFlowTemplate.code == candidate)):
                    code = candidate; break
            if not code:
                raise ValueError("自定义流程编码生成失败，请重试")
            template = KnowledgeFlowTemplate(
                id=new_id("flow"), code=code, name=name.strip(), description=description.strip(),
                output_types=sorted(set(output_types)), definition_json=compiled["definition"],
                authoring_mode="advanced", managed_template_code=None,
                derived_from_template_id=source_template_id, derived_from_revision_id=source_revision_id,
                status="draft", purpose="knowledge",
            )
            session.add(template); session.flush()
            revision = KnowledgeFlowTemplateRevision(
                id=new_id("flowrev"), knowledge_flow_template_id=template.id, revision_no=1,
                definition_json=compiled["definition"], authoring_mode="advanced",
                managed_template_code=None, status="draft", purpose="knowledge",
            )
            session.add(revision); session.flush()
            result = {"id": template.id, "revision": 1, "revision_id": revision.id,
                      "status": "draft", "name": template.name, "code": code,
                      "open_url": f"/developer/flow-templates?template_id={template.id}&edit=1"}
            session.add(DebugRunFlowMaterialization(
                id=new_id("debugmaterial"), flow_run_id=run.id, action="save_as_flow",
                idempotency_key=idempotency_key, target_template_id=template.id,
                target_revision_id=revision.id, result_json=result,
            ))
            self.audit(session, "debug_run.saved_as_flow", "knowledge_flow_template", template.id,
                       {"flow_run_id": flow_run_id, "source_template_id": source_template_id,
                        "source_revision_id": source_revision_id})
            return result

    def _debug_materialization_retry_result(self, flow_run_id: str, action: str,
                                            idempotency_key: str) -> dict[str, Any] | None:
        with self.sessions() as session:
            existing = session.scalar(select(DebugRunFlowMaterialization).where(
                DebugRunFlowMaterialization.idempotency_key == idempotency_key,
            ))
            if not existing:
                return None
            if existing.flow_run_id != flow_run_id or existing.action != action:
                raise ValueError("流程物化幂等键已用于其他操作")
            return {**dict(existing.result_json), "idempotent": True}

    def apply_debug_run_to_draft(self, flow_run_id: str, *, expected_revision_id: str,
                                 expected_definition_checksum: str, idempotency_key: str) -> dict[str, Any]:
        try:
            return self._apply_debug_run_to_draft_once(
                flow_run_id, expected_revision_id=expected_revision_id,
                expected_definition_checksum=expected_definition_checksum,
                idempotency_key=idempotency_key,
            )
        except IntegrityError:
            result = self._debug_materialization_retry_result(flow_run_id, "apply_to_draft", idempotency_key)
            if result is not None:
                return result
            raise

    def save_debug_run_as_flow(self, flow_run_id: str, *, name: str, description: str = "",
                               idempotency_key: str) -> dict[str, Any]:
        try:
            return self._save_debug_run_as_flow_once(
                flow_run_id, name=name, description=description, idempotency_key=idempotency_key,
            )
        except IntegrityError:
            result = self._debug_materialization_retry_result(flow_run_id, "save_as_flow", idempotency_key)
            if result is not None:
                return result
            raise

    def list_managed_flow_templates(self) -> list[dict[str, Any]]:
        with self.sessions() as session:
            return MANAGED_FLOW_CATALOG.list_definitions(load_catalog(session))

    def preview_graph_prompt(self, definition: dict[str, Any], node_id: str) -> dict[str, Any]:
        """Read Catalog only; never compile/freeze, create assets or contact a model."""
        from jsonschema import Draft202012Validator
        from .flow import _edge, _reachable_sink_contexts
        from .graph_prompt import GRAPH_GUIDANCE_VERSIONS, graph_config_for_node, graph_node_prompt, uses_graph_guidance
        from .graph_schema import normalize_graph_config

        nodes = definition.get("nodes")
        if not isinstance(nodes, list):
            raise ValueError("definition.nodes 必须是数组")
        matches = [node for node in nodes if isinstance(node, dict) and node.get("id") == node_id]
        if len(matches) != 1:
            raise ValueError("提示词预览需要唯一且存在的节点")
        node = matches[0]
        code = node.get("ref")
        if node.get("kind") != "operator" or code not in {*GRAPH_GUIDANCE_VERSIONS, "entity-relation-extractor"}:
            raise ValueError("仅实体、关系或实体关系联合抽取器支持图谱提示词预览")
        with self.sessions() as session:
            operator = resolve_operator(load_catalog(session), node)
        if not operator:
            raise ValueError("抽取算子未注册")
        schema = operator["parameter_schema"]
        incoming = node.get("params") or {}
        if not isinstance(incoming, dict):
            raise ValueError("节点参数必须是对象")
        if code in {"entity-extractor", "entity-relation-extractor"} and "entity_type_scope" not in incoming:
            incoming = {**incoming, "entity_type_scope": "subset" if incoming.get("entity_types") else "all"}
        params = self._schema_defaults(schema, {key: value for key, value in incoming.items()
                                                if key in schema.get("properties", {})})
        errors = list(Draft202012Validator(schema).iter_errors(params))
        if errors:
            raise FlowParameterError("PARAMETER_SCHEMA_INVALID", errors[0].message, node_id=node_id,
                                     field=str(next(iter(errors[0].path), "")))
        # graph_mode is system-owned and absent from the business schema above.
        # Resolve the selected branch exactly as the compiler does, not all sinks.
        outgoing: dict[str, list[str]] = {}
        for raw_edge in definition.get("edges") or []:
            edge = _edge(raw_edge)
            outgoing.setdefault(edge["source"], []).append(edge["target"])
        contexts = _reachable_sink_contexts(node_id, {item["id"]: item for item in nodes}, outgoing)
        if len(contexts) == 1:
            knowledge_type, graph_mode = next(iter(contexts))
            if knowledge_type == "graph":
                params["graph_mode"] = graph_mode
        version = operator["version"]
        config = normalize_graph_config(definition.get("graph_config"))
        if code in {"entity-extractor", "entity-relation-extractor"} and params.get("entity_type_scope") == "subset":
            unknown = set(params.get("entity_types") or []) - config.entity_codes()
            if unknown:
                raise FlowParameterError("PARAMETER_SCHEMA_INVALID", "实体类型子集引用了未定义的类型：" + "、".join(sorted(unknown)),
                                         node_id=node_id, field="entity_types")
        empty_subset = code in {"entity-extractor", "entity-relation-extractor"} and params.get("entity_type_scope") == "subset" and not params.get("entity_types")
        if code == "entity-relation-extractor":
            from .joint_graph import joint_graph_config, joint_graph_prompt
            config = joint_graph_config(config, params)
            system, user = ("", "") if empty_subset else joint_graph_prompt(config, params, "{{source_chunk}}")
        else:
            config = graph_config_for_node(config, params, relation=code == "relation-extractor",
                                           governed_prompt=uses_graph_guidance(code, version))
            system, user = ("", "") if empty_subset else graph_node_prompt(
                config, params, code, version, "{{source_chunk}}", ["{{entities}}"],
            )
        return {"node_id": node_id, "operator_version": version, "system": system, "user": user,
                "will_call_model": not empty_subset,
                "notice": "未选择实体类型，此节点不调用模型。" if empty_subset else (
                    "运行时每块一次联合抽取，失败最多修复一次完整结果；此预览不调用模型。" if code == "entity-relation-extractor"
                    else "运行时替换原文和上游实体占位；此预览不调用模型。"),
                "placeholders": {"source_chunk": "运行时当前来源分块原文",
                                 **({"entities": "运行时上游抽取实体"} if code == "relation-extractor" else {})}}

    def resolve_standard_flow(self, managed_template_code, output_types, definition):
        output_types = assert_normalized_output_types_match_managed_template(managed_template_code, output_types)
        definition = MANAGED_FLOW_CATALOG.normalize_config(managed_template_code, definition)
        flow = FLOW_AUTHORING_COMPILER.materialize(definition, output_types)
        with self.sessions() as session:
            catalog = load_catalog(session)
            for node in flow["nodes"]:
                if node.get("kind") == "operator":
                    item = resolve_operator(catalog, node)
                    if item:
                        node["params"] = self._schema_defaults(item["parameter_schema"], node.get("params") or {})
                        if item["uses_llm"] and not node["params"].get("llm_serving"):
                            node["params"]["llm_serving"] = self.llm_serving_registry.require(None).id
            return {"managed_template_code": managed_template_code, "output_types": output_types, **technical_projection(flow, catalog)}

    def materialize_managed_flow(self, managed_code: str) -> dict[str, Any]:
        flow_definition = MANAGED_FLOW_CATALOG.get(managed_code)
        flow_dsl = FLOW_AUTHORING_COMPILER.materialize(
            MANAGED_FLOW_CATALOG.default_stage_config(managed_code), list(flow_definition.output_types),
        )
        return {
            "managed_template_code": managed_code,
            "name": flow_definition.name,
            "output_types": list(flow_definition.output_types),
            "definition": flow_dsl,
        }

    def preview_flow_compilation(self, authoring_mode: str, managed_template_code: str | None,
                                 output_types: list[str] | None, definition: dict[str, Any]) -> dict[str, Any]:
        if authoring_mode not in {"standard", "advanced"}:
            raise ValueError("authoring_mode 必须是 standard 或 advanced")
        with self.sessions() as session:
            if authoring_mode == "standard":
                output_types = assert_normalized_output_types_match_managed_template(managed_template_code, output_types)
                definition = MANAGED_FLOW_CATALOG.normalize_config(managed_template_code, definition)
            compiled = self._compile_template_definition(
                session, definition, output_types, purpose="knowledge", authoring_mode=authoring_mode,
            )
            stages: list[dict[str, Any]] = []
            if authoring_mode == "standard" and managed_template_code:
                stages = [{"code": stage.code, "name": stage.name, "locked": stage.locked}
                          for stage in MANAGED_FLOW_CATALOG.get(managed_template_code).stages]
            return {"valid": True, "authoring_mode": authoring_mode,
                    "output_types": output_types,
                    "managed_template_code": managed_template_code,
                    "checksum": compiled["checksum"],
                    "materialized_definition": compiled["definition"] if authoring_mode == "standard" else None,
                    "compiled_definition": compiled["compiled_definition"],
                    "stages": stages,
                    "node_count": len(compiled["compiled_definition"].get("nodes", [])),
                    "edge_count": len(compiled["compiled_definition"].get("edges", [])),
                    "issues": []}

    def detach_flow_template_to_advanced(self, template_id: str, *, preview: bool = False) -> dict[str, Any]:
        with self.sessions.begin() as session:
            template = session.get(KnowledgeFlowTemplate, template_id)
            if not template or template.status == "archived":
                raise ValueError("模板不存在或已归档")
            latest = session.scalar(select(KnowledgeFlowTemplateRevision).where(
                KnowledgeFlowTemplateRevision.knowledge_flow_template_id == template.id,
            ).order_by(KnowledgeFlowTemplateRevision.revision_no.desc()))
            if not latest:
                raise ValueError("模板没有修订")
            if (latest.authoring_mode or template.authoring_mode or "advanced") != "standard":
                raise ValueError("只有标准配置可以转为高级编排")
            managed_code = latest.managed_template_code or template.managed_template_code
            if not managed_code:
                raise ValueError("标准配置缺少 managed_template_code")
            definition = self._standard_revision_definition(template, latest)
            output_types = assert_normalized_output_types_match_managed_template(managed_code, template.output_types)
            flow_dsl = FLOW_AUTHORING_COMPILER.materialize(definition, output_types)
            self._compile_template_definition(session, flow_dsl, template.output_types, purpose="knowledge", authoring_mode="advanced")
            suffix = uuid.uuid4().hex[:8]
            name = f"{template.name} 高级编排"
            if session.scalar(select(KnowledgeFlowTemplate.id).where(
                KnowledgeFlowTemplate.name == name, KnowledgeFlowTemplate.status != "archived",
            )):
                name = f"{name} {suffix}"
            if preview:
                return {"code": f"custom-advanced-{suffix}", "name": name,
                        "authoring_mode": "advanced", "output_types": output_types,
                        "definition": flow_dsl, "source_template_id": template.id,
                        "source_revision_id": latest.id}
            advanced = KnowledgeFlowTemplate(
                id=new_id("flow"), code=f"custom-advanced-{suffix}", name=name,
                description=f"由“{template.name}”r{latest.revision_no} 转换生成",
                output_types=output_types, definition_json=flow_dsl,
                authoring_mode="advanced", managed_template_code=None,
                derived_from_template_id=template.id, derived_from_revision_id=latest.id,
                status="draft", purpose="knowledge",
            )
            session.add(advanced); session.flush()
            revision = KnowledgeFlowTemplateRevision(
                id=new_id("flowrev"), knowledge_flow_template_id=advanced.id,
                revision_no=1, definition_json=flow_dsl,
                authoring_mode="advanced", managed_template_code=None,
                status="draft", purpose="knowledge",
            )
            session.add(revision)
            self.audit(session, "flow_template.converted_to_advanced", "knowledge_flow_template", advanced.id,
                       {"source_template_id": template.id, "source_revision_id": latest.id})
            return {"id": advanced.id, "name": advanced.name, "revision_id": revision.id,
                    "revision": revision.revision_no, "status": "draft", "authoring_mode": "advanced",
                    "definition": flow_dsl, "source_template_id": template.id,
                    "source_revision_id": latest.id,
                    "open_url": f"/developer/flow-templates?template_id={advanced.id}&edit=1"}

    def create_knowledge_job(self, source_version_ids: list[str], output_library_ids: dict[str, str], template_id: str,
                             document_library_template_binding_id: str | None = None) -> dict[str, Any]:
        if not source_version_ids or not output_library_ids:
            raise ValueError("至少选择一个来源版本和一个目标知识库")
        normalized_outputs = {normalise_output_key(key): value for key, value in output_library_ids.items()}
        with self.sessions.begin() as session:
            template, revision = self._published_template_revision(session, template_id)
            if set(normalized_outputs) - set(self._revision_output_types(session, revision)):
                raise ValueError("目标知识类型不在所选流程模板输出范围内")
            source_versions = session.scalars(select(SourceVersion).where(SourceVersion.id.in_(source_version_ids), SourceVersion.status == "active")).all()
            if len(source_versions) != len(set(source_version_ids)):
                raise ValueError("来源版本不存在或不是当前有效版本")
            review_inputs: list[tuple[SourceVersion, SourceReviewSnapshot]] = []
            for source_version in source_versions:
                counts = self._review_counts(self._active_source_chunks(session, source_version.id))
                review = session.get(SourceReviewSnapshot, source_version.current_review_snapshot_id) \
                    if source_version.current_review_snapshot_id else None
                if source_version.review_status != "approved" or not review or review.status != "approved":
                    message = (f"当前文档存在 {counts['pending_review']} 个待审核文档块，请完成审核后再运行知识流程。"
                               if counts["pending_review"] else
                               f"当前文档存在 {counts['rejected']} 个已拒绝文档块，请修正或删除后再运行知识流程。")
                    raise ReviewGateError("REVIEW_REQUIRED", message,
                                          source_version_id=source_version.id, counts=counts)
                review_inputs.append((source_version, review))
            if document_library_template_binding_id and len(review_inputs) == 1:
                existing_job = session.scalar(select(KnowledgeJob).join(
                    KnowledgeJobReviewInput,
                    KnowledgeJobReviewInput.knowledge_job_id == KnowledgeJob.id,
                ).where(
                    KnowledgeJob.document_library_template_binding_id == document_library_template_binding_id,
                    KnowledgeJob.knowledge_flow_template_revision_id == revision.id,
                    KnowledgeJobReviewInput.source_review_snapshot_id == review_inputs[0][1].id,
                    KnowledgeJobReviewInput.activation_no == review_inputs[0][0].activation_no,
                    KnowledgeJob.status.in_(("queued", "running", "completed", "completed_with_warnings")),
                ).order_by(KnowledgeJob.created_at.desc()))
                if existing_job:
                    return {**self.job_payload(existing_job), "idempotent": True}
            locked_libraries = {library.id: library for library in session.scalars(
                select(KnowledgeLibrary).where(
                    KnowledgeLibrary.id.in_(sorted(set(normalized_outputs.values()))),
                ).order_by(KnowledgeLibrary.id).with_for_update()
            ).all()}
            for output_type, library_id in normalized_outputs.items():
                knowledge_type, graph_mode = output_contract(output_type)
                library = locked_libraries.get(library_id)
                if (not library or library.status != "active" or library.knowledge_type != knowledge_type
                        or (graph_mode and library.graph_mode != graph_mode)):
                    raise ValueError(f"{output_type} 必须显式绑定同类型的有效知识库")
            snapshot = session.get(FlowExecutionSnapshot, revision.execution_snapshot_id) if revision.execution_snapshot_id else None
            if not snapshot:
                raise ValueError("流程已发布修订缺少不可变执行快照")
            job = KnowledgeJob(id=new_id("kj"), knowledge_flow_template_id=template.id, knowledge_flow_template_revision_id=revision.id,
                                source_version_ids=list(dict.fromkeys(source_version_ids)), output_library_ids=dict(normalized_outputs),
                                sink_library_ids=dict(normalized_outputs), execution_snapshot_id=snapshot.id,
                                document_library_template_binding_id=document_library_template_binding_id)
            session.add(job); session.flush()
            for source_version, review in review_inputs:
                session.add(KnowledgeJobReviewInput(
                    id=new_id("jobreview"), knowledge_job_id=job.id, source_version_id=source_version.id,
                    source_review_snapshot_id=review.id, review_digest=review.content_digest,
                    activation_no=source_version.activation_no,
                ))
            self.audit(session, "knowledge_job.created", "knowledge_job", job.id, {
                "outputs": normalized_outputs,
                "review_snapshot_ids": [review.id for _, review in review_inputs],
            })
            return self.job_payload(job)

    @staticmethod
    def _queue_review_dispatch(session: Session, snapshot_id: str, activation_no: int | None = None) -> KnowledgeDispatch:
        if activation_no is None:
            snapshot = session.get(SourceReviewSnapshot, snapshot_id)
            version = session.get(SourceVersion, snapshot.source_version_id) if snapshot else None
            activation_no = int(version.activation_no if version else 1)
        dispatch = session.scalar(select(KnowledgeDispatch).where(
            KnowledgeDispatch.source_review_snapshot_id == snapshot_id,
            KnowledgeDispatch.activation_no == activation_no,
        ).with_for_update())
        if not dispatch:
            dispatch = KnowledgeDispatch(
                id=new_id("dispatch"), source_review_snapshot_id=snapshot_id, activation_no=activation_no,
            )
            session.add(dispatch)
        elif dispatch.status not in {"queued", "running"}:
            dispatch.status, dispatch.error = "queued", None
        return dispatch

    def claim_knowledge_dispatch(self, owner: str) -> KnowledgeDispatch | None:
        with self.sessions.begin() as session:
            now = utc_now()
            dispatch = session.scalar(select(KnowledgeDispatch).where(or_(
                KnowledgeDispatch.status == "queued",
                (KnowledgeDispatch.status == "running") & (KnowledgeDispatch.lease_expires_at < now),
            )).order_by(KnowledgeDispatch.created_at).with_for_update(skip_locked=True))
            if not dispatch: return None
            dispatch.status, dispatch.attempt_count = "running", dispatch.attempt_count + 1
            dispatch.lease_owner, dispatch.lease_expires_at = owner, now + WORK_LEASE_DURATION
            return dispatch

    def process_knowledge_dispatch(self, dispatch_id: str) -> dict[str, Any]:
        with self.sessions.begin() as session:
            dispatch = session.get(KnowledgeDispatch, dispatch_id)
            snapshot = session.get(SourceReviewSnapshot, dispatch.source_review_snapshot_id) if dispatch else None
            version = session.get(SourceVersion, snapshot.source_version_id) if snapshot else None
            source = session.get(Source, version.source_id) if version else None
            if not dispatch or dispatch.status != "running" or not snapshot or not version or not source:
                raise ValueError("知识自动调度上下文不完整")
            if (source.current_version_id != version.id or version.current_review_snapshot_id != snapshot.id
                    or version.review_status != "approved" or version.activation_no != dispatch.activation_no):
                dispatch.status, dispatch.error = "failed", "人工审核快照已失效"
                dispatch.lease_owner, dispatch.lease_expires_at = None, None
                return {"id": dispatch.id, "status": dispatch.status, "error": dispatch.error}
            document_library = session.get(DocumentLibrary, source.document_library_id)
            bindings = list(session.scalars(select(DocumentLibraryTemplateBinding).where(
                DocumentLibraryTemplateBinding.document_library_id == source.document_library_id,
                DocumentLibraryTemplateBinding.status == "active",
            )))
            targets: list[tuple[str, dict[str, str], str]] = []
            warnings: list[str] = []
            for binding in bindings:
                try:
                    template, revision = self._published_template_revision(session, binding.knowledge_flow_template_id)
                    self._ensure_document_binding_outputs(session, document_library, template, binding, recreate_deleted=True)
                    outputs = {item.output_key: item.knowledge_library_id for item in session.scalars(select(
                        DocumentLibraryTemplateOutput,
                    ).where(DocumentLibraryTemplateOutput.document_library_template_binding_id == binding.id,
                            DocumentLibraryTemplateOutput.output_key.in_(self._revision_output_types(session, revision))))}
                    targets.append((template.id, outputs, binding.id))
                except (ValueError, ReviewGateError) as exc:
                    warnings.append(str(exc))
        jobs = []
        for template_id, outputs, binding_id in targets:
            try:
                jobs.append(self.create_knowledge_job([version.id], outputs, template_id, binding_id))
            except (ValueError, ReviewGateError) as exc:
                warnings.append(str(exc))
        with self.sessions.begin() as session:
            dispatch = session.get(KnowledgeDispatch, dispatch_id); assert dispatch
            dispatch.status = "completed_with_warnings" if warnings else "completed"
            dispatch.error = "; ".join(warnings) if warnings else None
            dispatch.lease_owner, dispatch.lease_expires_at = None, None
            self.audit(session, "knowledge_dispatch.completed", "knowledge_dispatch", dispatch.id,
                       {"job_ids": [item["id"] for item in jobs], "warnings": warnings})
            return {"id": dispatch.id, "status": dispatch.status, "jobs": jobs, "warnings": warnings}

    @staticmethod
    def _assert_job_review_gate(session: Session, job: KnowledgeJob) -> list[KnowledgeJobReviewInput]:
        inputs = list(session.scalars(select(KnowledgeJobReviewInput).where(
            KnowledgeJobReviewInput.knowledge_job_id == job.id,
        )))
        if len(inputs) != len(set(job.source_version_ids or [])):
            raise ReviewGateError("REVIEW_SNAPSHOT_MISSING", "知识任务缺少完整人工审核快照")
        for item in inputs:
            version = session.get(SourceVersion, item.source_version_id)
            snapshot = session.get(SourceReviewSnapshot, item.source_review_snapshot_id)
            source = session.get(Source, version.source_id) if version else None
            if (not version or not source or source.current_version_id != version.id
                    or item.activation_no != version.activation_no or not snapshot or snapshot.status != "approved"
                    or version.current_review_snapshot_id != snapshot.id
                    or snapshot.content_digest != item.review_digest):
                raise ReviewGateError("REVIEW_SNAPSHOT_STALE", "知识任务引用的人工审核快照已失效",
                                      source_version_id=item.source_version_id)
        return inputs

    def reviewed_chunks_for_job(self, job_id: str) -> list[dict[str, Any]]:
        with self.sessions() as session:
            job = session.get(KnowledgeJob, job_id)
            if not job: raise ValueError("知识任务不存在")
            inputs = self._assert_job_review_gate(session, job)
            values: list[dict[str, Any]] = []
            for item in inputs:
                version = session.get(SourceVersion, item.source_version_id)
                snapshot = session.get(SourceReviewSnapshot, item.source_review_snapshot_id)
                source = session.get(Source, version.source_id)
                rows = session.execute(select(
                    SourceReviewSnapshotChunk, SourceChunk, SourceChunkRevision,
                ).join(SourceChunk, SourceChunk.id == SourceReviewSnapshotChunk.source_chunk_id).join(
                    SourceChunkRevision, SourceChunkRevision.id == SourceReviewSnapshotChunk.source_chunk_revision_id,
                ).where(
                    SourceReviewSnapshotChunk.source_review_snapshot_id == snapshot.id,
                ).order_by(SourceReviewSnapshotChunk.ordinal)).all()
                if not rows: raise ReviewGateError("REVIEW_SNAPSHOT_EMPTY", "人工审核快照不包含文档块")
                for mapping, chunk, revision in rows:
                    if revision.content_hash != mapping.content_hash:
                        raise ReviewGateError("REVIEW_SNAPSHOT_STALE", "人工审核快照内容摘要不一致",
                                              source_version_id=version.id)
                    anchor = dict(revision.anchor_json or {})
                    values.append({
                        "source_id": source.id, "source_version_id": version.id,
                        "filename": version.original_filename, "source_chunk_id": chunk.source_chunk_id,
                        "source_chunk_revision_id": revision.id, "source_review_snapshot_id": snapshot.id,
                        "chunk_index": mapping.ordinal, "content": revision.content, "anchor": anchor,
                        **({"faq": dict(anchor.get("faq") or {})} if anchor.get("faq") else {}),
                    })
            return values

    @staticmethod
    def job_payload(job: KnowledgeJob) -> dict[str, Any]:
        return {"id": job.id, "knowledge_flow_template_id": job.knowledge_flow_template_id, "knowledge_flow_template_revision_id": job.knowledge_flow_template_revision_id, "execution_snapshot_id": job.execution_snapshot_id, "document_library_template_binding_id": job.document_library_template_binding_id, "source_version_ids": job.source_version_ids, "sink_library_ids": job.sink_library_ids or job.output_library_ids, "output_library_ids": job.output_library_ids, "status": job.status, "stage": job.stage, "attempt_count": job.attempt_count, "error": job.error, "warning_count": 0, "failed_chunk_count": 0, "progress": V7Store._job_progress(job, 0, 0), "created_at": job.created_at.isoformat()}

    @staticmethod
    def _job_progress(job: KnowledgeJob, total_nodes: int, completed_nodes: int) -> dict[str, int]:
        total = max(int(total_nodes or 0), 0)
        completed = min(max(int(completed_nodes or 0), 0), total) if total else 0
        if job.status == "queued":
            completed, percent = 0, 0
        elif job.status in {"completed", "completed_with_warnings"}:
            completed, percent = total, 100
        elif total:
            percent = completed * 100 // total
        else:
            percent = 0
        return {"completed_nodes": completed, "total_nodes": total, "percent": percent}

    def _job_progress_with_session(self, session: Session, job: KnowledgeJob) -> dict[str, int]:
        snapshot = session.get(FlowExecutionSnapshot, job.execution_snapshot_id) if job.execution_snapshot_id else None
        total = len((snapshot.compiled_definition_json or {}).get("nodes", [])) if snapshot else 0
        if job.status in {"queued", "completed", "completed_with_warnings"}:
            return self._job_progress(job, total, total if job.status != "queued" else 0)
        run = session.scalar(select(FlowRun).where(
            FlowRun.knowledge_job_id == job.id,
            FlowRun.parent_flow_run_id.is_(None),
            FlowRun.run_mode == "full",
        ).order_by(FlowRun.created_at.desc(), FlowRun.id.desc()))
        completed = 0 if not run else session.scalar(select(func.count(func.distinct(FlowNodeRun.node_id))).where(
            FlowNodeRun.flow_run_id == run.id,
        )) or 0
        return self._job_progress(job, total, completed)

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
        payload["progress"] = self._job_progress_with_session(session, job)
        return payload

    def template_definition_for_job(self, job_id: str) -> dict[str, Any]:
        with self.sessions() as session:
            job = session.get(KnowledgeJob, job_id)
            if not job:
                raise ValueError("知识任务不存在")
            revision = session.get(KnowledgeFlowTemplateRevision, job.knowledge_flow_template_revision_id)
            if not revision or revision.knowledge_flow_template_id != job.knowledge_flow_template_id:
                raise ValueError("知识任务 Revision 归属不匹配")
            snapshot = self._published_execution_snapshot(session, revision)
            if snapshot.id != job.execution_snapshot_id:
                raise ValueError("知识任务必须使用已绑定的 Published Snapshot")
            self._validate_snapshot_servings(snapshot)
            return snapshot.compiled_definition_json

    def _validate_snapshot_servings(self, snapshot: FlowExecutionSnapshot) -> None:
        for dependency in dict(snapshot.dependency_json or {}).get("dependencies", []):
            if dependency.get("kind") != "llm_serving" or not dependency.get("fingerprint"):
                continue
            try:
                registry = self.llm_serving_registry
                current = (registry.fingerprint(str(dependency.get("id") or ""), include_credentials=dependency.get("fingerprint_version", 1) == 1)
                           if isinstance(registry, DatabaseLLMServingRegistry) else registry.fingerprint(str(dependency.get("id") or "")))
            except ValueError as exc:
                raise FlowParameterError("SERVING_CONFIG_DRIFT", str(exc), field="llm_serving") from exc
            if current != dependency["fingerprint"]:
                raise FlowParameterError(
                    "SERVING_CONFIG_DRIFT",
                    f"Serving {dependency.get('id')} 配置已变化，不能执行冻结快照",
                    field="llm_serving",
                )

    def _type_contracts_for_bindings(self, session: Session, bindings: dict[str, str | None],
                                     definition: dict[str, Any], template_revision_id: str | None) -> dict[str, dict[str, Any]]:
        values: dict[str, dict[str, Any]] = {}
        for output_type, library_id in bindings.items():
                output_type = normalise_output_key(output_type)
                library = session.get(KnowledgeLibrary, library_id) if library_id else None
                knowledge_type, graph_mode = output_contract(output_type)
                revision = session.get(KnowledgeTypeRevision, library.knowledge_type_revision_id) if library and library.knowledge_type_revision_id else session.scalar(
                    select(KnowledgeTypeRevision).join(
                        KnowledgeType, KnowledgeType.current_revision_id == KnowledgeTypeRevision.id,
                    ).where(KnowledgeType.code == knowledge_type, KnowledgeType.status == "active",
                            KnowledgeTypeRevision.status == "published")
                )
                if not revision:
                    continue
                mode_revision = None
                effective_type = library.knowledge_type if library else knowledge_type
                effective_mode = library.graph_mode if library else graph_mode
                if effective_type == "graph" and effective_mode:
                    mode_revision = session.scalar(select(KnowledgeTypeModeRevision).where(
                        KnowledgeTypeModeRevision.knowledge_type_revision_id == revision.id,
                        KnowledgeTypeModeRevision.mode == effective_mode,
                        KnowledgeTypeModeRevision.status == "published",
                    ).order_by(KnowledgeTypeModeRevision.revision_no.desc()))
                prompt_body = ""
                params = dict((definition or {}).get("parameters") or {})
                prompt_revision_id = params.get("prompt_template_revision_id")
                prompt_node = next((node for node in (definition or {}).get("nodes", [])
                    if dict(node.get("params") or {}).get("knowledge_type") == effective_type and
                    (not effective_mode or dict(node.get("params") or {}).get("graph_mode") == effective_mode) and
                    dict(node.get("params") or {}).get("prompt_template_revision_id")), None)
                if prompt_node:
                    node_params = dict(prompt_node.get("params") or {})
                    prompt_revision_id = node_params.get("prompt_template_revision_id")
                    prompt_body = str((node_params.get("_resolved_prompt_template") or {}).get("body") or "")
                prompt = session.get(PromptTemplateRevision, prompt_revision_id) if prompt_revision_id else None
                if not prompt_body and prompt and prompt.status == "published":
                    prompt_body = prompt.body
                graph_config: dict[str, Any] | None = None
                graph_schema_hash: str | None = None
                if effective_type == "graph":
                    try:
                        config = normalize_graph_config((definition or {}).get("graph_config"))
                        graph_config = config.to_dict()
                        graph_schema_hash = schema_hash(config)
                    except ValueError:
                        graph_config = normalize_graph_config(None).to_dict()
                values[output_type] = {
                    "schema": mode_revision.schema_json if mode_revision else revision.schema_json,
                    "canonical_field": revision.canonical_field,
                    "canonical_fields": mode_revision.canonical_fields if mode_revision else [revision.canonical_field],
                    "identity_fields": mode_revision.identity_fields if mode_revision else revision.identity_fields,
                    "source_policy": mode_revision.source_policy if mode_revision else revision.source_policy,
                    "knowledge_type": effective_type, "graph_mode": effective_mode,
                    "prompt": prompt_body, "library_id": library.id if library else None,
                    "graph_config": graph_config, "graph_schema_hash": graph_schema_hash,
                    "template_revision_id": template_revision_id,
                }
        return values

    def type_contracts_for_job(self, job_id: str) -> dict[str, dict[str, Any]]:
        with self.sessions() as session:
            job = session.get(KnowledgeJob, job_id)
            if not job:
                raise ValueError("知识任务不存在")
            snapshot = session.get(FlowExecutionSnapshot, job.execution_snapshot_id) if job.execution_snapshot_id else None
            if not snapshot:
                raise ValueError("知识任务缺少执行快照")
            self._validate_snapshot_servings(snapshot)
            definition = snapshot.compiled_definition_json
            return self._type_contracts_for_bindings(
                session, dict(job.sink_library_ids or job.output_library_ids),
                dict(definition or {}), job.knowledge_flow_template_revision_id,
            )

    def list_knowledge_jobs(self) -> list[dict[str, Any]]:
        with self.sessions() as session:
            jobs = list(session.scalars(select(KnowledgeJob).order_by(KnowledgeJob.created_at.desc())))
            if not jobs:
                return []
            job_ids = [job.id for job in jobs]
            template_ids = {job.knowledge_flow_template_id for job in jobs}
            templates = {item.id: item for item in session.scalars(select(KnowledgeFlowTemplate).where(
                KnowledgeFlowTemplate.id.in_(template_ids),
            ))}
            sink_ids = list(dict.fromkeys(
                str(library_id)
                for job in jobs
                for library_id in (job.sink_library_ids or job.output_library_ids or {}).values()
                if library_id
            ))
            sink_libraries = {item.id: item for item in session.scalars(select(KnowledgeLibrary).where(
                KnowledgeLibrary.id.in_(sink_ids),
            ))} if sink_ids else {}
            snapshot_ids = {job.execution_snapshot_id for job in jobs if job.execution_snapshot_id}
            snapshots = {item.id: item for item in session.scalars(select(FlowExecutionSnapshot).where(
                FlowExecutionSnapshot.id.in_(snapshot_ids),
            ))} if snapshot_ids else {}
            failed_counts = dict(session.execute(select(
                KnowledgeChunkGeneration.knowledge_job_id, func.count(),
            ).where(
                KnowledgeChunkGeneration.knowledge_job_id.in_(job_ids),
                KnowledgeChunkGeneration.status == "failed",
            ).group_by(KnowledgeChunkGeneration.knowledge_job_id)).all())
            latest_runs: dict[str, FlowRun] = {}
            for run in session.scalars(select(FlowRun).where(
                FlowRun.knowledge_job_id.in_(job_ids),
                FlowRun.parent_flow_run_id.is_(None),
                FlowRun.run_mode == "full",
            ).order_by(FlowRun.created_at.desc(), FlowRun.id.desc())):
                latest_runs.setdefault(run.knowledge_job_id, run)
            run_ids = [run.id for run in latest_runs.values()]
            node_counts = dict(session.execute(select(
                FlowNodeRun.flow_run_id, func.count(func.distinct(FlowNodeRun.node_id)),
            ).where(FlowNodeRun.flow_run_id.in_(run_ids)).group_by(FlowNodeRun.flow_run_id)).all()) if run_ids else {}
            payloads = []
            for job in jobs:
                payload = self.job_payload(job)
                template = templates.get(job.knowledge_flow_template_id)
                payload["template"] = (
                    {"id": template.id, "code": template.code, "name": template.name}
                    if template else None
                )
                ordered_sink_ids = list(dict.fromkeys(
                    str(library_id)
                    for library_id in (job.sink_library_ids or job.output_library_ids or {}).values()
                    if library_id
                ))
                payload["sink_libraries"] = [
                    {
                        "id": library.id,
                        "name": library.name,
                        "knowledge_type": library.knowledge_type,
                        "graph_mode": library.graph_mode,
                    }
                    for library_id in ordered_sink_ids
                    if (library := sink_libraries.get(library_id))
                ]
                failed = int(failed_counts.get(job.id, 0))
                payload["failed_chunk_count"] = failed
                payload["warning_count"] = failed if job.status == "completed_with_warnings" else 0
                snapshot = snapshots.get(job.execution_snapshot_id)
                total = len((snapshot.compiled_definition_json or {}).get("nodes", [])) if snapshot else 0
                run = latest_runs.get(job.id)
                payload["progress"] = self._job_progress(job, total, node_counts.get(run.id, 0) if run else 0)
                payloads.append(payload)
            return payloads

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

    @staticmethod
    def _job_library_ids(job: KnowledgeJob) -> list[str]:
        return sorted(set((job.sink_library_ids or job.output_library_ids or {}).values()))

    @staticmethod
    def _lease_is_active(lease: KnowledgeLibraryWorkLease, now) -> bool:
        expires = lease.lease_expires_at
        if expires.tzinfo is None and now.tzinfo is not None:
            expires = expires.replace(tzinfo=now.tzinfo)
        return expires >= now

    def _acquire_library_work_leases(self, session: Session, library_ids: list[str], *,
                                     work_kind: str, work_id: str, owner: str, now) -> bool:
        if not library_ids:
            return False
        try:
            with session.begin_nested():
                existing = {
                    item.knowledge_library_id: item
                    for item in session.scalars(select(KnowledgeLibraryWorkLease).where(
                        KnowledgeLibraryWorkLease.knowledge_library_id.in_(library_ids),
                    ).order_by(KnowledgeLibraryWorkLease.knowledge_library_id).with_for_update()).all()
                }
                for library_id in library_ids:
                    lease = existing.get(library_id)
                    if lease and self._lease_is_active(lease, now):
                        return False
                expires_at = now + WORK_LEASE_DURATION
                for library_id in library_ids:
                    lease = existing.get(library_id)
                    if lease:
                        lease.work_kind, lease.work_id = work_kind, work_id
                        lease.lease_owner, lease.lease_expires_at = owner, expires_at
                    else:
                        session.add(KnowledgeLibraryWorkLease(
                            knowledge_library_id=library_id, work_kind=work_kind, work_id=work_id,
                            lease_owner=owner, lease_expires_at=expires_at,
                        ))
                session.flush()
            return True
        except IntegrityError:
            return False

    def claim_job(self, owner: str) -> KnowledgeJob | None:
        now = utc_now()
        with self.sessions.begin() as session:
            candidate_ids = list(session.scalars(select(KnowledgeJob.id).where(
                (KnowledgeJob.status == "queued") |
                ((KnowledgeJob.status == "running") & (KnowledgeJob.lease_expires_at < now))
            ).order_by(KnowledgeJob.created_at)))
            for job_id in candidate_ids:
                job = session.scalar(select(KnowledgeJob).where(
                    KnowledgeJob.id == job_id,
                    (KnowledgeJob.status == "queued") |
                    ((KnowledgeJob.status == "running") & (KnowledgeJob.lease_expires_at < now)),
                ).with_for_update(skip_locked=True))
                if not job:
                    continue
                try:
                    self._assert_job_review_gate(session, job)
                except ReviewGateError as exc:
                    job.status, job.stage, job.error = "failed", "review_gate", str(exc)
                    self.audit(session, "knowledge_job.review_gate_failed", "knowledge_job", job.id, exc.payload())
                    continue
                library_ids = self._job_library_ids(job)
                if not self._acquire_library_work_leases(
                    session, library_ids, work_kind="knowledge", work_id=job.id, owner=owner, now=now,
                ):
                    continue
                job.status, job.stage = "running", "processing"
                job.lease_owner, job.lease_expires_at = owner, now + WORK_LEASE_DURATION
                job.attempt_count += 1
                self.audit(session, "knowledge_job.claimed", "knowledge_job", job.id, {
                    "owner": owner, "attempt": job.attempt_count, "knowledge_library_ids": library_ids,
                })
                return job
            return None

    def renew_work_lease(self, work_kind: str, work_id: str, owner: str) -> bool:
        now = utc_now()
        expires_at = now + WORK_LEASE_DURATION
        with self.sessions.begin() as session:
            if work_kind == "knowledge":
                job = session.get(KnowledgeJob, work_id, with_for_update=True)
                library_ids = self._job_library_ids(job) if job else []
            elif work_kind == "vector_sync":
                job = session.get(VectorSyncJob, work_id, with_for_update=True)
                library_ids = [job.knowledge_library_id] if job else []
            else:
                raise ValueError("未知 Worker 租约类型")
            if not job or job.status != "running" or job.lease_owner != owner:
                return False
            leases = list(session.scalars(select(KnowledgeLibraryWorkLease).where(
                KnowledgeLibraryWorkLease.knowledge_library_id.in_(library_ids),
                KnowledgeLibraryWorkLease.work_kind == work_kind,
                KnowledgeLibraryWorkLease.work_id == work_id,
                KnowledgeLibraryWorkLease.lease_owner == owner,
            ).with_for_update()))
            if len(leases) != len(library_ids):
                return False
            job.lease_expires_at = expires_at
            for lease in leases:
                lease.lease_expires_at = expires_at
            return True

    def assert_work_lease(self, work_kind: str, work_id: str, owner: str) -> None:
        now = utc_now()
        with self.sessions() as session:
            if work_kind == "knowledge":
                job = session.get(KnowledgeJob, work_id)
                library_ids = self._job_library_ids(job) if job else []
            elif work_kind == "vector_sync":
                job = session.get(VectorSyncJob, work_id)
                library_ids = [job.knowledge_library_id] if job else []
            else:
                raise ValueError("未知 Worker 租约类型")
            if not job or job.status != "running" or job.lease_owner != owner:
                raise ValueError("任务执行租约已失效")
            expires = job.lease_expires_at
            if not expires:
                raise ValueError("任务执行租约已失效")
            if expires.tzinfo is None and now.tzinfo is not None:
                expires = expires.replace(tzinfo=now.tzinfo)
            if expires < now:
                raise ValueError("任务执行租约已过期")
            leases = list(session.scalars(select(KnowledgeLibraryWorkLease).where(
                KnowledgeLibraryWorkLease.knowledge_library_id.in_(library_ids),
                KnowledgeLibraryWorkLease.work_kind == work_kind,
                KnowledgeLibraryWorkLease.work_id == work_id,
                KnowledgeLibraryWorkLease.lease_owner == owner,
                KnowledgeLibraryWorkLease.lease_expires_at >= now,
            )))
            if len(leases) != len(library_ids):
                raise ValueError("目标知识库执行租约已失效")

    def release_work_lease(self, work_kind: str, work_id: str, owner: str | None = None) -> None:
        with self.sessions.begin() as session:
            query = delete(KnowledgeLibraryWorkLease).where(
                KnowledgeLibraryWorkLease.work_kind == work_kind,
                KnowledgeLibraryWorkLease.work_id == work_id,
            )
            if owner is not None:
                query = query.where(KnowledgeLibraryWorkLease.lease_owner == owner)
            session.execute(query)

    def has_pending_exclusive_work(self) -> bool:
        now = utc_now()
        with self.sessions() as session:
            checks = (
                select(KnowledgeMigrationJob.id).where(
                    (KnowledgeMigrationJob.status == "queued") |
                    ((KnowledgeMigrationJob.status == "running") & (KnowledgeMigrationJob.lease_expires_at < now))
                ),
                select(FlowRun.id).where(FlowRun.status == "queued", FlowRun.parent_flow_run_id.is_not(None)),
                select(KnowledgeAssetGcJob.id).where(
                    KnowledgeAssetGcJob.status == "queued", KnowledgeAssetGcJob.execute_requested.is_(True),
                ),
                select(VectorDeletionJob.id).where(
                    (VectorDeletionJob.status == "queued") |
                    ((VectorDeletionJob.status == "running") & (VectorDeletionJob.lease_expires_at < now))
                ),
                select(KnowledgeLibraryDeletionJob.id).where(
                    (KnowledgeLibraryDeletionJob.status == "queued") |
                    ((KnowledgeLibraryDeletionJob.status == "running") & (KnowledgeLibraryDeletionJob.lease_expires_at < now))
                ),
                select(DocumentDeletionJob.id).where(
                    (DocumentDeletionJob.status == "queued") |
                    ((DocumentDeletionJob.status == "running") & (DocumentDeletionJob.lease_expires_at < now))
                ),
                select(ManagedCollectionDeletionJob.id).where(
                    (ManagedCollectionDeletionJob.status == "queued") |
                    ((ManagedCollectionDeletionJob.status == "running") & (ManagedCollectionDeletionJob.lease_expires_at < now))
                ),
            )
            return any(session.scalar(query.limit(1)) is not None for query in checks)

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
                blocked = []
                for job in values:
                    if not job.document_library_template_binding_id:
                        continue
                    library_ids = set((job.sink_library_ids or job.output_library_ids or {}).values())
                    if any(library is None or library.status != "active" for library in (
                        session.get(KnowledgeLibrary, library_id) for library_id in library_ids
                    )):
                        blocked.append(job.id)
                if blocked:
                    raise ValueError("自动结果知识库正在清理或已清理，请返回文档库重新发起处理")
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

    def _queue_empty_failed_auto_output_cleanup(self, session: Session, job: KnowledgeJob) -> list[str]:
        """Queue deletion only for never-populated automatic result libraries."""
        if not job.document_library_template_binding_id:
            return []
        library_ids = set((job.sink_library_ids or job.output_library_ids or {}).values())
        if not library_ids:
            return []
        outputs = session.scalars(select(DocumentLibraryTemplateOutput).where(
            DocumentLibraryTemplateOutput.document_library_template_binding_id == job.document_library_template_binding_id,
            DocumentLibraryTemplateOutput.knowledge_library_id.in_(library_ids),
        )).all()
        blocked: list[str] = []
        for output in outputs:
            library = session.get(KnowledgeLibrary, output.knowledge_library_id)
            if not library or library.status != "active":
                continue
            has_history = bool(session.scalar(select(func.count()).select_from(KnowledgeItem).where(
                KnowledgeItem.knowledge_library_id == library.id,
            )))
            if has_history:
                self.audit(session, "knowledge_library.auto_cleanup_skipped", "knowledge_library", library.id, {
                    "knowledge_job_id": job.id, "reason": "existing_knowledge_history",
                })
                continue
            has_route = bool(session.scalar(select(func.count()).select_from(ProjectOrgRouteLibrary).where(
                ProjectOrgRouteLibrary.knowledge_library_id == library.id,
            )))
            if has_route:
                blocked.append(library.name)
                self.audit(session, "knowledge_library.auto_cleanup_blocked", "knowledge_library", library.id, {
                    "knowledge_job_id": job.id, "reason": "project_route_reference",
                })
                continue
            existing = session.scalar(select(KnowledgeLibraryDeletionJob).where(
                KnowledgeLibraryDeletionJob.knowledge_library_id == library.id,
                KnowledgeLibraryDeletionJob.status.in_(("queued", "running", "failed")),
            ).order_by(KnowledgeLibraryDeletionJob.created_at.desc()))
            if existing:
                continue
            library.status = "deleting"
            deletion_job = KnowledgeLibraryDeletionJob(id=new_id("kldel"), knowledge_library_id=library.id)
            session.add(deletion_job)
            self.audit(session, "knowledge_library.auto_cleanup_queued", "knowledge_library", library.id, {
                "knowledge_job_id": job.id, "deletion_job_id": deletion_job.id,
            })
        return blocked

    def mark_job_failed(self, job_id: str, error: str) -> None:
        with self.sessions.begin() as session:
            job = session.get(KnowledgeJob, job_id)
            if not job:
                raise ValueError("知识任务不存在")
            blocked = self._queue_empty_failed_auto_output_cleanup(session, job)
            if blocked:
                error = f"{error}；自动结果知识库仍被项目路由引用，请先解除路由后再清理：{'、'.join(blocked)}"
            job.status, job.stage, job.error, job.lease_owner, job.lease_expires_at = "failed", "failed", error, None, None
            session.execute(delete(KnowledgeLibraryWorkLease).where(
                KnowledgeLibraryWorkLease.work_kind == "knowledge",
                KnowledgeLibraryWorkLease.work_id == job.id,
            ))
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
            session.execute(delete(KnowledgeLibraryWorkLease).where(
                KnowledgeLibraryWorkLease.work_kind == "knowledge",
                KnowledgeLibraryWorkLease.work_id == job.id,
            ))
            if not has_warnings and job.document_library_template_binding_id and job.knowledge_flow_template_revision_id:
                binding = session.get(DocumentLibraryTemplateBinding, job.document_library_template_binding_id)
                if binding:
                    for version_id in job.source_version_ids:
                        review_input = session.scalar(select(KnowledgeJobReviewInput).where(
                            KnowledgeJobReviewInput.knowledge_job_id == job.id,
                            KnowledgeJobReviewInput.source_version_id == version_id,
                        ))
                        if not session.scalar(select(DocumentLibraryProcessingRecord.id).where(
                            DocumentLibraryProcessingRecord.document_library_template_binding_id == binding.id,
                            DocumentLibraryProcessingRecord.source_version_id == version_id,
                            DocumentLibraryProcessingRecord.knowledge_flow_template_revision_id == job.knowledge_flow_template_revision_id,
                            DocumentLibraryProcessingRecord.source_review_snapshot_id == (
                                review_input.source_review_snapshot_id if review_input else None
                            ),
                            DocumentLibraryProcessingRecord.activation_no == (
                                review_input.activation_no if review_input else 1
                            ),
                        )):
                            session.add(DocumentLibraryProcessingRecord(
                                id=new_id("docproc"), document_library_template_binding_id=binding.id,
                                source_version_id=version_id, knowledge_flow_template_revision_id=job.knowledge_flow_template_revision_id,
                                source_review_snapshot_id=review_input.source_review_snapshot_id if review_input else None,
                                activation_no=review_input.activation_no if review_input else 1,
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
            session.flush(); self._append_run_event(session, run.id, "run.started", "Flow Run 已开始")
            self.audit(session, "flow_run.started", "flow_run", run.id, {"knowledge_job_id": job.id})
            return {"id": run.id, "execution_snapshot_id": run.execution_snapshot_id, "status": run.status}

    def record_flow_node(self, flow_run_id: str, node_id: str, input_artifact_ids: list[str], outputs: list[dict[str, Any]], *, error: str | None = None,
                         operator_code: str | None = None, operator_version: int | None = None, resolved_parameters: dict[str, Any] | None = None,
                         status: str | None = None, logs: list[dict[str, Any]] | None = None, metrics: dict[str, Any] | None = None) -> list[str]:
        """Persist execution-only artifacts and their lineage; never use them as formal provenance."""
        diagnostics = OperatorDiagnostics()
        diagnostics.add_secrets(resolved_parameters)
        diagnostics.extend(logs)
        logs = diagnostics.snapshot()
        error = diagnostics.error(error) if error else None
        for attempt in range(len(ARTIFACT_DEADLOCK_RETRY_WINDOWS) + 1):
            try:
                return self._record_flow_node_transaction(
                    flow_run_id, node_id, input_artifact_ids, outputs, error=error,
                    operator_code=operator_code, operator_version=operator_version,
                    resolved_parameters=resolved_parameters, status=status, logs=logs, metrics=metrics,
                )
            except OperationalError as exc:
                if attempt >= len(ARTIFACT_DEADLOCK_RETRY_WINDOWS) or not _is_retryable_mysql_deadlock(
                    exc, self.engine.dialect.name,
                ):
                    raise
                code, sqlstate = _database_error_identity(exc)
                retry_number = attempt + 1
                delay = random.uniform(*ARTIFACT_DEADLOCK_RETRY_WINDOWS[attempt])
                LOGGER.warning(
                    "Artifact 节点事务发生可重试死锁，准备重试：flow_run_id=%s node_id=%s "
                    "retry=%s/%s mysql_errno=%s sqlstate=%s",
                    flow_run_id, node_id, retry_number, len(ARTIFACT_DEADLOCK_RETRY_WINDOWS), code, sqlstate,
                )
                time.sleep(delay)
        raise AssertionError("Artifact 死锁重试循环异常退出")

    def _record_flow_node_transaction(self, flow_run_id: str, node_id: str, input_artifact_ids: list[str], outputs: list[dict[str, Any]], *, error: str | None = None,
                                      operator_code: str | None = None, operator_version: int | None = None, resolved_parameters: dict[str, Any] | None = None,
                                      status: str | None = None, logs: list[dict[str, Any]] | None = None, metrics: dict[str, Any] | None = None) -> list[str]:
        with self.sessions.begin() as session:
            input_ids = list(input_artifact_ids)
            input_artifacts = {
                item.id: item
                for item in session.scalars(select(Artifact).where(Artifact.id.in_(input_ids))).all()
            } if input_ids else {}
            started = utc_now()
            node_run = FlowNodeRun(id=new_id("noderun"), flow_run_id=flow_run_id, node_id=node_id, operator_code=operator_code,
                                   operator_version=operator_version, resolved_parameters=resolved_parameters or {}, logs_json=logs or [], metrics_json=metrics or {},
                                   error_json={"message": error} if error else {}, started_at=started, finished_at=started, duration_ms=0,
                                   status=status or ("failed" if error else "completed"), input_artifact_ids=input_ids, error=error)
            artifacts: list[Artifact] = []
            bindings = [
                FlowNodeArtifactBinding(
                    id=new_id("binding"), flow_node_run_id=node_run.id, artifact_id=artifact_id,
                    direction="input", port_name="input", ordinal=ordinal,
                    reused=bool(input_artifacts.get(artifact_id) and input_artifacts[artifact_id].flow_run_id != flow_run_id),
                )
                for ordinal, artifact_id in enumerate(input_ids)
            ]
            lineages: list[ArtifactLineage] = []
            output_ids: list[str] = []
            for ordinal, value in enumerate(outputs):
                data = dict(value) if isinstance(value, dict) else {"value": value}
                parser_artifacts = list(data.pop("_parser_artifacts", []))
                type_code = str(data.pop("_artifact_type", "execution"))
                encoded = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
                artifact = Artifact(id=new_id("artifact"), flow_run_id=flow_run_id, flow_node_run_id=node_run.id,
                                    type_code=type_code, data_json=data, checksum=hashlib.sha256(encoded).hexdigest(),
                                    summary_json={"keys": sorted(data)[:20], "bytes": len(encoded)}, record_count=1, replayable=True)
                artifacts.append(artifact); output_ids.append(artifact.id)
                bindings.append(FlowNodeArtifactBinding(id=new_id("binding"), flow_node_run_id=node_run.id, artifact_id=artifact.id,
                                                        direction="output", port_name="output", ordinal=ordinal))
                for parent_id in input_ids:
                    lineages.append(ArtifactLineage(id=new_id("lineage"), parent_artifact_id=parent_id, child_artifact_id=artifact.id))
                for parser_artifact in parser_artifacts:
                    parser_data = dict(parser_artifact.get("data") or {})
                    persisted = Artifact(
                        id=new_id("artifact"), flow_run_id=flow_run_id, flow_node_run_id=node_run.id,
                        source_version_id=str(parser_artifact["source_version_id"]),
                        type_code=str(parser_artifact["type_code"]), uri=str(parser_artifact["uri"]),
                        checksum=str(parser_artifact["checksum"]), data_json=parser_data,
                    )
                    artifacts.append(persisted); output_ids.append(persisted.id)
                    lineages.append(ArtifactLineage(id=new_id("lineage"), parent_artifact_id=artifact.id, child_artifact_id=persisted.id))
            node_run.output_artifact_ids = output_ids
            session.add(node_run)
            session.flush()
            session.add_all(sorted(artifacts, key=lambda item: item.id))
            session.flush()
            session.add_all(sorted(bindings, key=lambda item: item.id))
            session.add_all(sorted(lineages, key=lambda item: item.id))
            with session.no_autoflush:
                self._append_run_event(
                    session, flow_run_id, f"node.{node_run.status}", f"节点 {node_id} {node_run.status}", node_id=node_id,
                    payload={"input_count": len(input_ids), "output_count": len(output_ids)},
                )
            session.flush()
            for log in logs or []:
                self._append_run_event(
                    session, flow_run_id, "node.operator_log", log["message"], node_id=node_id,
                    payload={"stream": log["stream"], "truncated": log["truncated"], "node_run_id": node_run.id},
                )
                # _append_run_event allocates the next sequence from this transaction.
                session.flush()
            return output_ids

    def finish_flow_run(self, flow_run_id: str, error: str | None = None, *, status: str | None = None) -> None:
        with self.sessions.begin() as session:
            run = session.get(FlowRun, flow_run_id)
            if not run:
                raise ValueError("Flow Run 不存在")
            final_status = status or ("failed" if error else "completed")
            run.status, run.error, run.completed_at = final_status, error, utc_now()
            self._append_run_event(session, run.id, f"run.{final_status}", error or f"Flow Run {final_status}", level="error" if error else "info")
            self.audit(session, "flow_run.finished", "flow_run", run.id, {"status": final_status, "error": error} if error else {"status": final_status})

    def flow_run_detail(self, flow_run_id: str) -> dict[str, Any]:
        with self.sessions() as session:
            run = session.get(FlowRun, flow_run_id)
            if not run:
                raise ValueError("Flow Run 不存在")
            artifact_summary_columns = (
                Artifact.id, Artifact.flow_node_run_id, Artifact.type_code, Artifact.summary_json,
                Artifact.record_count, Artifact.replayable, Artifact.uri,
            )
            nodes = session.scalars(select(FlowNodeRun).where(FlowNodeRun.flow_run_id == run.id).order_by(FlowNodeRun.created_at)).all()
            artifacts = session.scalars(
                select(Artifact)
                .options(load_only(*artifact_summary_columns))
                .where(Artifact.flow_run_id == run.id)
                .order_by(Artifact.created_at, Artifact.id)
            ).all()
            # Poll summaries without loading the (potentially large) candidates or chunks.
            json_length = func.json_array_length if self.engine.dialect.name == "sqlite" else func.json_length
            previews = session.execute(select(
                FlowRunSinkPreview.id, FlowRunSinkPreview.output_key, FlowRunSinkPreview.status,
                FlowRunSinkPreview.baseline_kind, FlowRunSinkPreview.knowledge_library_id,
                FlowRunSinkPreview.diff_json, FlowRunSinkPreview.quality_json, FlowRunSinkPreview.preview_checksum,
                json_length(FlowRunSinkPreview.candidates_json).label("candidate_count"),
            ).where(FlowRunSinkPreview.flow_run_id == run.id).order_by(FlowRunSinkPreview.created_at)).all()
            snapshot = session.get(FlowExecutionSnapshot, run.execution_snapshot_id)
            definition = snapshot.compiled_definition_json if snapshot else {"nodes": [], "edges": []}
            latest = {node.node_id: node for node in nodes}
            artifact_by_id = {item.id: item for item in artifacts}
            selected_ids = {str(node["id"]) for node in definition.get("nodes", [])}
            if run.parent_flow_run_id and run.start_node_id:
                selected_ids = {run.start_node_id}
                if run.run_mode == "from_node":
                    outgoing: dict[str, list[str]] = {}
                    for edge in definition.get("edges", []):
                        source = str(edge[0] if isinstance(edge, list) else edge.get("source")); target = str(edge[1] if isinstance(edge, list) else edge.get("target"))
                        outgoing.setdefault(source, []).append(target)
                    queue = [run.start_node_id]
                    while queue:
                        current = queue.pop(0)
                        for target in outgoing.get(current, []):
                            if target not in selected_ids: selected_ids.add(target); queue.append(target)
            reused_ids = set()
            parent_latest: dict[str, FlowNodeRun] = {}
            if run.parent_flow_run_id:
                for edge in definition.get("edges", []):
                    source = str(edge[0] if isinstance(edge, list) else edge.get("source")); target = str(edge[1] if isinstance(edge, list) else edge.get("target"))
                    if target in selected_ids and source not in selected_ids: reused_ids.add(source)
                parent_latest = {node.node_id: node for node in session.scalars(select(FlowNodeRun).where(FlowNodeRun.flow_run_id == run.parent_flow_run_id)).all()}
                reused_artifact_ids = [artifact_id for node_id in reused_ids for artifact_id in (parent_latest.get(node_id).output_artifact_ids if parent_latest.get(node_id) else [])]
                if reused_artifact_ids:
                    artifact_by_id.update({item.id: item for item in session.scalars(
                        select(Artifact)
                        .options(load_only(*artifact_summary_columns))
                        .where(Artifact.id.in_(reused_artifact_ids))
                    ).all()})
            runtime_nodes = []
            for definition_node in definition.get("nodes", []):
                node = latest.get(str(definition_node["id"]))
                runtime_nodes.append({"id": definition_node["id"], "kind": definition_node.get("kind"), "ref": definition_node.get("ref"),
                                      "node_role": definition_node.get("node_role"),
                                      "knowledge_type": definition_node.get("knowledge_type"),
                                      "graph_mode": definition_node.get("graph_mode"), "output_key": definition_node.get("output_key"),
                                      "params": definition_node.get("params") or {}, "origin_path": definition_node.get("origin_path") or str(definition_node["id"]).split("::"),
                                      "source_subgraph": definition_node.get("source_subgraph"),
                                      "stage_id": definition_node.get("stage_id"), "stage_code": definition_node.get("stage_code"),
                                      "stage_label": definition_node.get("stage_label"), "operator_version": definition_node.get("operator_version"),
                                      "operator_spec": definition_node.get("operator_spec"),
                                      "status": node.status if node else "reused" if str(definition_node["id"]) in reused_ids else "skipped",
                                      "node_run_id": node.id if node else None})
            runtime_edges = []
            for raw in definition.get("edges", []):
                source = str(raw[0] if isinstance(raw, list) else raw.get("source")); target = str(raw[1] if isinstance(raw, list) else raw.get("target"))
                source_run = latest.get(source) or (parent_latest.get(source) if source in reused_ids else None); edge_artifacts = [artifact_by_id[value] for value in (source_run.output_artifact_ids if source_run else []) if value in artifact_by_id]
                edge = {"source": source, "target": target,
                        "source_port": "output" if isinstance(raw, list) else raw.get("source_port", "output"),
                        "target_port": "input" if isinstance(raw, list) else raw.get("target_port", "input"),
                        "status": latest.get(source).status if latest.get(source) else "reused" if source in reused_ids else "skipped", "artifact_ids": [item.id for item in edge_artifacts],
                        "artifact_type": ", ".join(sorted({item.type_code for item in edge_artifacts})),
                        "record_count": sum(item.record_count or 0 for item in edge_artifacts)}
                runtime_edges.append(edge)
            debug_input = session.get(DebugRunInputSnapshot, run.debug_input_snapshot_id) if run.debug_input_snapshot_id else None
            template = session.get(KnowledgeFlowTemplate, debug_input.knowledge_flow_template_id) if debug_input else None
            # Provenance comes from the run's frozen records, not the mutable draft
            # or revision status (the same draft may since have been published).
            descriptor = (debug_input.input_descriptor_json or {}) if debug_input else {}
            started_at = min((node.started_at for node in nodes if node.started_at), default=None)
            # SQLite/MySQL may return naive UTC datetimes; identify the timezone
            # explicitly in the newly exposed run timestamps.
            def timestamp(value):
                return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).isoformat() if value else None
            input_context = None if debug_input is None else {
                "input_source": debug_input.input_source,
                "sample_code": descriptor.get("sample_code") if debug_input.input_source == "builtin_sample" else None,
                "sample_version": descriptor.get("sample_version") if debug_input.input_source == "builtin_sample" else None,
            }
            return {"id": run.id, "knowledge_job_id": run.knowledge_job_id,
                    "source_preparation_job_id": run.source_preparation_job_id,
                    "debug_input_snapshot_id": run.debug_input_snapshot_id,
                    "execution_snapshot_id": run.execution_snapshot_id,
                    "execution_checksum": snapshot.checksum if snapshot else None,
                    "compiled_checksum": (snapshot.dependency_json or {}).get("source_checksum") if snapshot else None,
                    "source_definition_checksum": debug_input.source_definition_checksum if debug_input else None,
                    "revision_kind": descriptor.get("revision_kind"),
                    "source_revision": descriptor.get("revision_no"),
                    "node_count": len(definition.get("nodes", [])), "edge_count": len(definition.get("edges", [])),
                    "created_at": timestamp(run.created_at), "started_at": timestamp(started_at),
                    "parent_flow_run_id": run.parent_flow_run_id, "run_mode": run.run_mode, "start_node_id": run.start_node_id,
                    "parameter_overrides": run.parameter_overrides, "sink_policy": run.sink_policy,
                    "input_context": input_context,
                     "template_id": template.id if template else None,
                     "template_name": template.name if template else None,
                     "template_revision_id": debug_input.knowledge_flow_template_revision_id if debug_input else None,
                     "authoring_mode": debug_input.authoring_mode if debug_input else None,
                     "status": run.status, "error": run.error, "runtime_dag": {"nodes": runtime_nodes, "edges": runtime_edges}, "nodes": [
                        {"id": node.id, "node_id": node.node_id, "status": node.status, "input_artifact_ids": node.input_artifact_ids,
                         "output_artifact_ids": node.output_artifact_ids, "operator_code": node.operator_code, "operator_version": node.operator_version,
                         "resolved_parameters": node.resolved_parameters, "logs": node.logs_json, "metrics": node.metrics_json,
                         "started_at": node.started_at.isoformat() if node.started_at else None, "finished_at": node.finished_at.isoformat() if node.finished_at else None,
                         "duration_ms": node.duration_ms, "error": node.error, "error_detail": node.error_json} for node in nodes],
                     "artifacts": [{"id": item.id, "node_run_id": item.flow_node_run_id, "type": item.type_code,
                                    "summary": item.summary_json, "record_count": item.record_count, "replayable": item.replayable,
                                    "uri": item.uri} for item in artifacts],
                     "sink_previews": [{"id": item.id, "output_key": item.output_key, "status": item.status,
                                          "candidate_count": item.candidate_count or 0,
                                          "baseline_kind": item.baseline_kind,
                                          "knowledge_library_id": item.knowledge_library_id,
                                          "diff": item.diff_json, "quality": item.quality_json,
                                         "preview_checksum": item.preview_checksum} for item in previews]}

    def _append_run_event(self, session: Session, flow_run_id: str, event_type: str, message: str, *, node_id: str | None = None,
                          level: str = "info", payload: dict[str, Any] | None = None) -> FlowRunEvent:
        sequence = session.scalar(select(func.max(FlowRunEvent.sequence_no)).where(FlowRunEvent.flow_run_id == flow_run_id)) or 0
        event = FlowRunEvent(id=new_id("event"), flow_run_id=flow_run_id, sequence_no=sequence + 1, event_type=event_type,
                             node_id=node_id, level=level, message=message, payload_json=payload or {})
        session.add(event); return event

    def flow_run_events(self, flow_run_id: str, after: int = 0, limit: int = 200) -> dict[str, Any]:
        with self.sessions() as session:
            if not session.get(FlowRun, flow_run_id): raise ValueError("Flow Run 不存在")
            rows = session.scalars(select(FlowRunEvent).where(FlowRunEvent.flow_run_id == flow_run_id,
                                                              FlowRunEvent.sequence_no > max(after, 0))
                                   .order_by(FlowRunEvent.sequence_no).limit(min(max(limit, 1), 500))).all()
            return {"items": [{"cursor": item.sequence_no, "level": item.level, "type": item.event_type, "node_id": item.node_id,
                               "message": item.message, "payload": item.payload_json, "created_at": item.created_at.isoformat()} for item in rows],
                    "next_cursor": rows[-1].sequence_no if rows else after}

    def sink_preview_candidates(self, flow_run_id: str, preview_id: str, offset: int = 0, limit: int = 50) -> dict[str, Any]:
        with self.sessions() as session:
            preview = session.scalar(select(FlowRunSinkPreview).where(
                FlowRunSinkPreview.id == preview_id, FlowRunSinkPreview.flow_run_id == flow_run_id,
            ))
            if preview is None:
                raise ValueError("该 Run 的最终结果不存在")
            values = preview.candidates_json or []
            start, size = max(offset, 0), min(max(limit, 1), 200)
            return {"items": values[start:start + size], "offset": start, "limit": size,
                    "total": len(values), "has_more": start + size < len(values)}

    def artifact_detail(self, artifact_id: str) -> dict[str, Any]:
        with self.sessions() as session:
            item = session.get(Artifact, artifact_id)
            if not item: raise ValueError("Artifact 不存在")
            parents = list(session.scalars(select(ArtifactLineage.parent_artifact_id).where(ArtifactLineage.child_artifact_id == artifact_id)))
            children = list(session.scalars(select(ArtifactLineage.child_artifact_id).where(ArtifactLineage.parent_artifact_id == artifact_id)))
            return {"id": item.id, "flow_run_id": item.flow_run_id, "node_run_id": item.flow_node_run_id, "type": item.type_code,
                    "summary": item.summary_json, "record_count": item.record_count, "content_format": item.content_format,
                    "replayable": item.replayable, "checksum": item.checksum, "uri": item.uri,
                    "lineage": {"parents": parents, "children": children}}

    def artifact_content(self, artifact_id: str, offset: int = 0, limit: int = 100) -> dict[str, Any]:
        with self.sessions() as session:
            item = session.get(Artifact, artifact_id)
            if not item: raise ValueError("Artifact 不存在")
            raw = item.data_json
            values = raw if isinstance(raw, list) else [raw]
            start, size = max(offset, 0), min(max(limit, 1), 200)
            return {"items": values[start:start + size], "offset": start, "limit": size, "total": len(values),
                    "has_more": start + size < len(values)}

    @staticmethod
    def _incoming_nodes(definition: dict[str, Any]) -> dict[str, list[str]]:
        values = {str(node["id"]): [] for node in definition.get("nodes", [])}
        for edge in definition.get("edges", []): values.setdefault(str(edge["target"]), []).append(str(edge["source"]))
        return values

    @staticmethod
    def _artifact_can_replay(item: Artifact | None) -> bool:
        if not item or not item.replayable or not item.checksum:
            return False
        # URI-backed parser artifacts are verified by their storage checksum when read.
        if item.uri:
            return True
        encoded = json.dumps(item.data_json, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest() == item.checksum

    @staticmethod
    def _validate_parameter_override(schema: dict[str, Any], value: dict[str, Any]) -> str | None:
        try:
            normalized = validate_parameters(schema, value)
            if normalized.get("llm_serving"):
                get_llm_serving_registry().require(str(normalized["llm_serving"]))
        except ValueError as exc:
            return str(exc)
        return None

    def create_derived_run(self, parent_run_id: str, mode: str, node_id: str, parameter_overrides: dict[str, Any], idempotency_key: str | None = None) -> dict[str, Any]:
        if mode not in {"node_only", "from_node"}: raise ValueError("派生 Run mode 仅支持 node_only 或 from_node")
        with self.sessions.begin() as session:
            parent = session.get(FlowRun, parent_run_id)
            if not parent or parent.status == "running": raise ValueError("父 Flow Run 不存在或仍在运行")
            if idempotency_key:
                existing = session.scalar(select(FlowRun).where(FlowRun.parent_flow_run_id == parent_run_id,
                                                                 FlowRun.idempotency_key == idempotency_key))
                if existing:
                    return {"id": existing.id, "parent_flow_run_id": parent.id, "execution_snapshot_id": existing.execution_snapshot_id,
                            "run_mode": existing.run_mode, "start_node_id": existing.start_node_id, "status": existing.status, "idempotent": True}
            snapshot = session.get(FlowExecutionSnapshot, parent.execution_snapshot_id); assert snapshot
            definition = snapshot.compiled_definition_json or {}; by_id = {str(node["id"]): node for node in definition.get("nodes", [])}
            node = by_id.get(node_id)
            if not node or node.get("kind") != "operator": raise ValueError("只能对展开后的真实算子节点创建派生 Run")
            override_delta = dict(parameter_overrides or {})
            unknown = set(override_delta) - {node_id}
            if unknown: raise ValueError("参数覆盖只能包含所选节点")
            effective_overrides = json.loads(json.dumps(parent.parameter_overrides or {}, ensure_ascii=False)) \
                if parent.debug_input_snapshot_id else {}
            effective_overrides[node_id] = {
                **dict(effective_overrides.get(node_id) or {}), **dict(override_delta.get(node_id) or {}),
            }
            resolved = {**dict(node.get("params") or {}), **dict(effective_overrides.get(node_id) or {})}
            definition_row = session.scalar(select(OperatorDefinition).where(OperatorDefinition.code == node.get("ref")))
            version_row = session.scalar(select(OperatorVersion).where(OperatorVersion.operator_definition_id == definition_row.id,
                                                                        OperatorVersion.version_no == int(node["operator_version"]))) if definition_row else None
            if not definition_row or not definition_row.enabled or not version_row or version_row.status != "published": raise ValueError("算子版本已不可执行")
            override_schema = node["operator_spec"]["parameter_schema"]
            editable = set(dict(override_schema.get("properties") or {}))
            submitted_keys = set(dict(override_delta.get(node_id) or {})) - {"force_ocr"}
            unknown_parameters = submitted_keys - editable
            if unknown_parameters:
                raise ValueError(f"参数覆盖不符合 Operator Version Schema：不允许参数 {sorted(unknown_parameters)[0]}")
            validate_parameters(override_schema, {key: value for key, value in resolved.items() if key != "force_ocr"},
                                node_id=node_id, runtime=True)
            if resolved.get("force_ocr") and node.get("ref") != "document-parser": raise ValueError("force_ocr 仅适用于 Document Parser")
            if resolved.get("force_ocr"):
                job = session.get(KnowledgeJob, parent.knowledge_job_id); assert job
                sources = session.scalars(select(SourceVersion.original_filename).where(
                    SourceVersion.id.in_(job.source_version_ids),
                )).all()
                if not sources or any(not name.lower().endswith(".pdf") for name in sources): raise ValueError("强制 OCR 仅适用于全部输入均为 PDF 的 Run")
            incoming = self._incoming_nodes(definition)
            parent_nodes = {item.node_id: item for item in session.scalars(select(FlowNodeRun).where(FlowNodeRun.flow_run_id == parent.id)).all()}
            selected_record = parent_nodes.get(node_id)
            selected_inputs = [session.get(Artifact, value) for value in (selected_record.input_artifact_ids if selected_record else [])]
            if selected_record and any(not self._artifact_can_replay(item) for item in selected_inputs):
                raise ValueError(f"节点 {node_id} 缺少完整可重放 Artifact")
            selected_ids = {node_id}
            if mode == "from_node":
                outgoing: dict[str, list[str]] = {}
                for edge in definition.get("edges", []):
                    source = str(edge[0] if isinstance(edge, list) else edge.get("source")); target = str(edge[1] if isinstance(edge, list) else edge.get("target"))
                    outgoing.setdefault(source, []).append(target)
                queue = [node_id]
                while queue:
                    current = queue.pop(0)
                    for target in outgoing.get(current, []):
                        if target not in selected_ids: selected_ids.add(target); queue.append(target)
            boundary_nodes = {predecessor for selected_id in selected_ids for predecessor in incoming.get(selected_id, []) if predecessor not in selected_ids}
            for predecessor in boundary_nodes:
                record = parent_nodes.get(predecessor)
                artifacts = [session.get(Artifact, value) for value in (record.output_artifact_ids if record else [])]
                if not artifacts or any(not self._artifact_can_replay(item) for item in artifacts):
                    raise ValueError(f"节点 {predecessor} 缺少完整可重放 Artifact")
            run = FlowRun(id=new_id("flowrun"), knowledge_job_id=parent.knowledge_job_id,
                          source_preparation_job_id=parent.source_preparation_job_id,
                          debug_input_snapshot_id=parent.debug_input_snapshot_id,
                          execution_snapshot_id=parent.execution_snapshot_id,
                          parent_flow_run_id=parent.id, run_mode=mode, start_node_id=node_id,
                          parameter_overrides=effective_overrides if parent.debug_input_snapshot_id else override_delta,
                          sink_policy="preview", requested_by="admin", idempotency_key=idempotency_key, status="queued")
            session.add(run); session.flush(); self._append_run_event(session, run.id, "run.queued", "派生 Run 已进入队列", payload={"parent": parent.id, "mode": mode, "node": node_id})
            self.audit(session, "flow_run.derived_created", "flow_run", run.id, {"parent": parent.id, "mode": mode, "node": node_id})
            return {"id": run.id, "parent_flow_run_id": parent.id, "execution_snapshot_id": run.execution_snapshot_id, "run_mode": mode, "start_node_id": node_id, "status": "queued"}

    def claim_derived_run(self, owner: str) -> FlowRun | None:
        with self.sessions.begin() as session:
            run = session.scalar(select(FlowRun).where(
                FlowRun.status == "queued", FlowRun.parent_flow_run_id.is_not(None),
                FlowRun.debug_input_snapshot_id.is_(None),
            )
                                 .order_by(FlowRun.created_at).with_for_update(skip_locked=True).limit(1))
            if not run: return None
            run.status = "running"; self._append_run_event(session, run.id, "run.started", f"派生 Run 由 {owner} 开始执行")
            return run

    def claim_debug_run(self, owner: str) -> FlowRun | None:
        with self.sessions.begin() as session:
            run = session.scalar(select(FlowRun).where(
                FlowRun.status == "queued", FlowRun.debug_input_snapshot_id.is_not(None),
                FlowRun.run_mode.in_(("debug_full", "node_only", "from_node")),
            ).order_by(FlowRun.created_at).with_for_update(skip_locked=True).limit(1))
            if not run:
                return None
            run.status = "running"
            self._append_run_event(session, run.id, "run.started", f"调试 Run 由 {owner} 开始执行")
            return run

    def persist_derived_parameters(self, flow_run_id: str, node_id: str, parameters: dict[str, Any]) -> dict[str, Any]:
        with self.sessions() as session:
            run = session.get(FlowRun, flow_run_id)
            if not run or not run.parent_flow_run_id: raise ValueError("只能保存派生 Run 的参数覆盖")
            snapshot = session.get(FlowExecutionSnapshot, run.execution_snapshot_id); job = session.get(KnowledgeJob, run.knowledge_job_id)
            if not snapshot or not job: raise ValueError("派生 Run 缺少不可变快照或任务")
            node = next((item for item in (snapshot.compiled_definition_json or {}).get("nodes", []) if str(item.get("id")) == node_id), None)
            if not node or node.get("kind") != "operator": raise ValueError("只能保存真实算子节点参数")
            if len(node.get("origin_path") or [node_id]) > 1 or "::" in node_id:
                raise ValueError("子图内部节点不能直接写回父模板；请先复制所属子图为草稿")
            definition = session.scalar(select(OperatorDefinition).where(OperatorDefinition.code == node.get("ref")))
            version = session.scalar(select(OperatorVersion).where(OperatorVersion.operator_definition_id == definition.id,
                                                                    OperatorVersion.version_no == int(node["operator_version"]))) if definition else None
            if not version: raise ValueError("算子版本不存在")
            validation_error = self._validate_parameter_override(node["operator_spec"]["parameter_schema"], parameters)
            if validation_error: raise ValueError(f"参数不符合 Operator Version Schema：{validation_error}")
            template = session.get(KnowledgeFlowTemplate, job.knowledge_flow_template_id)
            if not template: raise ValueError("父模板不存在")
            template_id, template_name, output_types = template.id, template.name, list(template.output_types)
            template_definition = json.loads(json.dumps(template.definition_json or {}))
        target = next((item for item in template_definition.get("nodes", []) if str(item.get("id")) == node_id), None)
        if not target: raise ValueError("顶层节点已不在当前模板中")
        target["params"] = dict(parameters)
        result = self.update_flow_template(template_id, template_name, output_types, template_definition)
        return {**result, "node_id": node_id, "message": "参数覆盖已保存为模板草稿"}

    def derived_run_context(self, flow_run_id: str) -> dict[str, Any]:
        with self.sessions() as session:
            run = session.get(FlowRun, flow_run_id)
            if not run or not run.parent_flow_run_id: raise ValueError("派生 Flow Run 不存在")
            parent = session.get(FlowRun, run.parent_flow_run_id); snapshot = session.get(FlowExecutionSnapshot, run.execution_snapshot_id)
            job = session.get(KnowledgeJob, run.knowledge_job_id)
            assert parent and snapshot and job
            self._validate_snapshot_servings(snapshot)
            node_runs = {item.node_id: item for item in session.scalars(select(FlowNodeRun).where(FlowNodeRun.flow_run_id == parent.id)).all()}
            parent_outputs: dict[str, dict[str, Any]] = {}
            for node_id, node_run in node_runs.items():
                artifacts = [session.get(Artifact, value) for value in node_run.output_artifact_ids]
                parent_outputs[node_id] = {"ids": [item.id for item in artifacts if item], "values": [dict(item.data_json) for item in artifacts if item]}
            versions_list = session.scalars(select(SourceVersion).where(SourceVersion.id.in_(job.source_version_ids))).all()
            sources = {source.id: source for source in session.scalars(select(Source).where(Source.id.in_([item.source_id for item in versions_list])))}
            return {"id": run.id, "parent_id": parent.id, "mode": run.run_mode, "start_node_id": run.start_node_id,
                    "parameter_overrides": run.parameter_overrides, "definition": snapshot.compiled_definition_json,
                    "job_id": job.id, "sink_libraries": dict(job.sink_library_ids or job.output_library_ids),
                    "versions": versions_list, "sources": sources, "parent_outputs": parent_outputs}

    def _reviewed_chunks_for_debug_session(self, session: Session, debug_input_id: str) -> list[dict[str, Any]]:
        inputs = list(session.scalars(select(DebugRunReviewInput).where(
            DebugRunReviewInput.debug_input_snapshot_id == debug_input_id,
        ).order_by(DebugRunReviewInput.ordinal)))
        if not inputs:
            raise ValueError("调试输入缺少人工审核快照")
        values: list[dict[str, Any]] = []
        for item in inputs:
            version = session.get(SourceVersion, item.source_version_id)
            snapshot = session.get(SourceReviewSnapshot, item.source_review_snapshot_id)
            source = session.get(Source, version.source_id) if version else None
            if (not version or not snapshot or not source or snapshot.status != "approved"
                    or snapshot.content_digest != item.review_digest):
                raise ValueError("调试输入引用的人工审核快照已失效")
            rows = session.execute(select(
                SourceReviewSnapshotChunk, SourceChunk, SourceChunkRevision,
            ).join(SourceChunk, SourceChunk.id == SourceReviewSnapshotChunk.source_chunk_id).join(
                SourceChunkRevision, SourceChunkRevision.id == SourceReviewSnapshotChunk.source_chunk_revision_id,
            ).where(
                SourceReviewSnapshotChunk.source_review_snapshot_id == snapshot.id,
            ).order_by(SourceReviewSnapshotChunk.ordinal)).all()
            if not rows:
                raise ValueError("调试审核快照不包含文档块")
            for mapping, chunk, revision in rows:
                if revision.content_hash != mapping.content_hash:
                    raise ValueError("调试审核快照内容摘要不一致")
                anchor = dict(revision.anchor_json or {})
                values.append({
                    "source_id": source.id, "source_version_id": version.id,
                    "filename": version.original_filename, "source_chunk_id": chunk.source_chunk_id,
                    "source_chunk_revision_id": revision.id, "source_review_snapshot_id": snapshot.id,
                    "chunk_index": mapping.ordinal, "content": revision.content, "anchor": anchor,
                    **({"faq": dict(anchor.get("faq") or {})} if anchor.get("faq") else {}),
                })
        return values

    def debug_run_context(self, flow_run_id: str) -> dict[str, Any]:
        with self.sessions() as session:
            run = session.get(FlowRun, flow_run_id)
            if not run or not run.debug_input_snapshot_id or run.run_mode not in {"debug_full", "node_only", "from_node"}:
                raise ValueError("调试 Flow Run 不存在")
            debug_input = session.get(DebugRunInputSnapshot, run.debug_input_snapshot_id)
            snapshot = session.get(FlowExecutionSnapshot, run.execution_snapshot_id)
            if not debug_input or not snapshot:
                raise ValueError("调试 Run 缺少不可变输入或执行快照")
            review_inputs = list(session.scalars(select(DebugRunReviewInput).where(
                DebugRunReviewInput.debug_input_snapshot_id == debug_input.id,
            ).order_by(DebugRunReviewInput.ordinal)))
            if debug_input.input_source == "builtin_sample":
                descriptor = dict(debug_input.input_descriptor_json or {})
                source_id = f"sample-source:{descriptor.get('sample_code', 'reviewed-medical-v2')}"
                version_id = f"sample-version:{descriptor.get('sample_code', 'reviewed-medical-v2')}:{descriptor.get('sample_version', '2')}"
                sample_chunks = list(debug_input.resolved_chunks_json or [])
                filename = next((chunk["filename"] for chunk in sample_chunks if chunk.get("filename")), "builtin-reviewed-sample")
                versions_list = [SimpleNamespace(id=version_id, source_id=source_id, original_filename=filename)]
                sources = {source_id: SimpleNamespace(
                    id=source_id, name="DataForge 示例审核数据", original_filename="builtin-reviewed-sample",
                )}
            else:
                versions_list = list(session.scalars(select(SourceVersion).where(
                    SourceVersion.id.in_([item.source_version_id for item in review_inputs]),
                )))
                sources = {item.id: item for item in session.scalars(select(Source).where(
                    Source.id.in_([version.source_id for version in versions_list]),
                ))}
            root_documents = list(debug_input.resolved_chunks_json or [])
            if not root_documents:
                root_documents = self._reviewed_chunks_for_debug_session(session, debug_input.id)
            parent_outputs: dict[str, dict[str, Any]] = {}
            ancestor = session.get(FlowRun, run.parent_flow_run_id) if run.parent_flow_run_id else None
            visited: set[str] = set()
            while ancestor and ancestor.id not in visited:
                visited.add(ancestor.id)
                for node_run in session.scalars(select(FlowNodeRun).where(
                    FlowNodeRun.flow_run_id == ancestor.id,
                ).order_by(FlowNodeRun.created_at.desc())):
                    if node_run.node_id in parent_outputs:
                        continue
                    artifacts = [session.get(Artifact, artifact_id) for artifact_id in node_run.output_artifact_ids]
                    if artifacts and all(self._artifact_can_replay(item) for item in artifacts):
                        parent_outputs[node_run.node_id] = {
                            "ids": [item.id for item in artifacts if item],
                            "values": [dict(item.data_json) for item in artifacts if item],
                        }
                ancestor = session.get(FlowRun, ancestor.parent_flow_run_id) if ancestor.parent_flow_run_id else None
            targets = dict(debug_input.sink_preview_targets_json or {}) or {
                key: {"baseline_kind": "knowledge_library", "knowledge_library_id": value}
                for key, value in dict(debug_input.sink_library_bindings_json or {}).items()
            }
            contract_bindings = {key: target.get("knowledge_library_id") for key, target in targets.items()}
            type_contracts = self._type_contracts_for_bindings(
                session, contract_bindings,
                dict(snapshot.compiled_definition_json), debug_input.knowledge_flow_template_revision_id,
            )
            self._validate_snapshot_servings(snapshot)
            return {
                "id": run.id, "mode": run.run_mode, "start_node_id": run.start_node_id,
                "parameter_overrides": dict(run.parameter_overrides or {}),
                "definition": snapshot.compiled_definition_json,
                "sink_libraries": dict(debug_input.sink_library_bindings_json),
                "sink_preview_targets": targets,
                "input_source": debug_input.input_source,
                "versions": versions_list, "sources": sources, "root_documents": root_documents,
                "parent_outputs": parent_outputs, "type_contracts": type_contracts,
            }

    def is_flow_run_cancelled(self, flow_run_id: str) -> bool:
        with self.sessions() as session:
            run = session.get(FlowRun, flow_run_id)
            return bool(run and run.cancellation_requested_at)

    def cancel_flow_run(self, flow_run_id: str) -> dict[str, Any]:
        with self.sessions.begin() as session:
            run = session.get(FlowRun, flow_run_id)
            if not run or not run.parent_flow_run_id: raise ValueError("派生 Flow Run 不存在")
            if run.status not in {"queued", "running"}: return {"id": run.id, "status": run.status, "idempotent": True}
            run.cancellation_requested_at = utc_now()
            if run.status == "queued": run.status, run.completed_at = "cancelled", utc_now()
            else: run.status = "cancelling"
            self._append_run_event(session, run.id, "run.cancel_requested", "已请求协作式停止")
            return {"id": run.id, "status": run.status}

    def _library_state_hash(self, session: Session, library_id: str) -> str:
        rows = session.execute(select(KnowledgeItem.source_knowledge_id, KnowledgeItem.content_hash, KnowledgeItem.status)
                               .where(KnowledgeItem.knowledge_library_id == library_id).order_by(KnowledgeItem.source_knowledge_id)).all()
        return hashlib.sha256(json.dumps([list(row) for row in rows], ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()

    def _preview_sink_diff(self, session: Session, library_id: str, candidates: list[dict[str, Any]],
                           successful_chunks: list[dict[str, Any]]) -> dict[str, int]:
        library = session.get(KnowledgeLibrary, library_id)
        if not library:
            raise ValueError("预览目标知识库不存在")
        revision = session.get(KnowledgeTypeRevision, library.knowledge_type_revision_id) if library.knowledge_type_revision_id else None
        source_policy = revision.source_policy if revision else "single"
        current = {item.source_knowledge_id: item for item in session.scalars(select(KnowledgeItem).where(
            KnowledgeItem.knowledge_library_id == library_id,
        ))}
        candidate_by_key: dict[str, dict[str, Any]] = {}
        for candidate in candidates:
            key = str(candidate.get("source_knowledge_id") or "")
            if key:
                candidate_by_key[key] = candidate
        counts = {"ADD": 0, "UPDATE": 0, "INACTIVE": 0, "UNCHANGED": 0}
        for key, candidate in candidate_by_key.items():
            item = current.get(key)
            digest = content_hash(str(candidate.get("canonical_content") or ""), dict(candidate.get("data_json") or {}))
            if not item:
                counts["ADD"] += 1
            elif item.status != "active" or item.content_hash != digest:
                counts["UPDATE"] += 1
            else:
                counts["UNCHANGED"] += 1
        version_ids = {str(item.get("source_version_id") or "") for item in successful_chunks}
        versions = {item.id: item for item in session.scalars(select(SourceVersion).where(
            SourceVersion.id.in_(version_ids),
        ))} if version_ids else {}
        processed_sources = {
            (versions[version_id].source_id, int(item.get("chunk_index", -1))): version_id
            for item in successful_chunks
            if (version_id := str(item.get("source_version_id") or "")) in versions
        }
        if processed_sources:
            links = session.execute(select(KnowledgeItemSource, KnowledgeItem, SourceVersion).join(
                KnowledgeItem, KnowledgeItem.id == KnowledgeItemSource.knowledge_item_id,
            ).join(SourceVersion, SourceVersion.id == KnowledgeItemSource.source_version_id).where(
                KnowledgeItem.knowledge_library_id == library_id,
            )).all()
            by_item: dict[str, list[tuple[KnowledgeItemSource, SourceVersion]]] = {}
            items: dict[str, KnowledgeItem] = {}
            for link, item, version in links:
                by_item.setdefault(item.id, []).append((link, version)); items[item.id] = item
            inactive: set[str] = set()
            for item_id, item_links in by_item.items():
                item = items[item_id]
                if item.status != "active" or item.source_knowledge_id in candidate_by_key:
                    continue
                stale = [
                    (link, version) for link, version in item_links
                    if (replacement := processed_sources.get((version.source_id, int((link.anchor_json or {}).get("chunk_index", -1)))))
                    and replacement != link.source_version_id
                ]
                if not stale:
                    continue
                remaining = len(item_links) - len(stale)
                if source_policy == "single" or remaining == 0:
                    inactive.add(item_id)
            counts["INACTIVE"] = len(inactive)
        return counts

    def stage_sink_preview(self, flow_run_id: str, output_key: str, library_id: str | None, candidates: list[dict[str, Any]],
                           successful_chunks: list[dict[str, Any]], quality: dict[str, Any] | None = None,
                           baseline_kind: str = "knowledge_library") -> dict[str, Any]:
        with self.sessions.begin() as session:
            if baseline_kind == "empty":
                if library_id:
                    raise ValueError("虚拟空库 Diff 不允许绑定 KnowledgeLibrary")
                base_hash = hashlib.sha256(b"[]").hexdigest()
                keys = {str(item.get("source_knowledge_id") or "") for item in candidates}
                diff = {"ADD": len({key for key in keys if key}), "UPDATE": 0, "INACTIVE": 0, "UNCHANGED": 0}
            elif baseline_kind == "knowledge_library" and library_id:
                base_hash = self._library_state_hash(session, library_id)
                diff = self._preview_sink_diff(session, library_id, candidates, successful_chunks)
            else:
                raise ValueError("Sink Preview 基线不合法")
            checksum = hashlib.sha256(json.dumps({"base": base_hash, "candidates": candidates, "chunks": successful_chunks}, ensure_ascii=False,
                                                 sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
            run = session.get(FlowRun, flow_run_id)
            preview = FlowRunSinkPreview(id=new_id("preview"), flow_run_id=flow_run_id, output_key=output_key,
                                         knowledge_library_id=library_id, baseline_kind=baseline_kind,
                                         candidates_json=candidates, successful_chunks_json=successful_chunks, diff_json=diff,
                                         quality_json=quality or {"candidate_count": len(candidates), "status": "pass"},
                                         base_state_hash=base_hash, preview_checksum=checksum,
                                         status="preview_only" if run and run.debug_input_snapshot_id else "pending")
            session.add(preview); self._append_run_event(session, flow_run_id, "sink.preview_ready", f"{output_key} Diff 已暂存", payload={"diff": diff, "checksum": checksum})
            return {"output_key": output_key, "baseline_kind": baseline_kind,
                    "knowledge_library_id": library_id, "diff": diff, "preview_checksum": checksum}

    def commit_derived_run(self, flow_run_id: str, preview_checksum: str, idempotency_key: str) -> dict[str, Any]:
        with self.sessions.begin() as session:
            run = session.get(FlowRun, flow_run_id)
            if run and run.debug_input_snapshot_id:
                raise ValueError("Debug Sink Preview 永远不可提交")
            if not run or run.status not in {"awaiting_commit", "completed"}: raise ValueError("Flow Run 没有可提交的 Sink 预览")
            previews = session.scalars(select(FlowRunSinkPreview).where(FlowRunSinkPreview.flow_run_id == flow_run_id)).all()
            preview = next((item for item in previews if item.preview_checksum == preview_checksum), None)
            if not preview: raise ValueError("预览 checksum 不匹配")
            if preview.status == "committed":
                if preview.idempotency_key == idempotency_key: return {"id": run.id, "status": run.status, "idempotent": True}
                raise ValueError("该 Sink 预览已经提交")
            if not preview.knowledge_library_id or preview.baseline_kind != "knowledge_library":
                raise ValueError("虚拟空库 Preview 不可提交")
            if self._library_state_hash(session, preview.knowledge_library_id) != preview.base_state_hash:
                raise RuntimeError("知识库当前态已变化，请重新生成 Sink 预览")
            payload = {"id": preview.id, "output_key": preview.output_key, "knowledge_library_id": preview.knowledge_library_id,
                       "candidates": list(preview.candidates_json), "chunks": list(preview.successful_chunks_json)}
        job_id = self.flow_run_detail(flow_run_id)["knowledge_job_id"]
        self.apply_knowledge_output(job_id, payload["output_key"], payload["candidates"], successful_chunks=payload["chunks"])
        vector_jobs = {payload["knowledge_library_id"]: self.create_vector_sync_jobs(payload["knowledge_library_id"])}
        with self.sessions.begin() as session:
            run = session.get(FlowRun, flow_run_id); assert run
            preview = session.get(FlowRunSinkPreview, payload["id"]); assert preview
            preview.status, preview.idempotency_key, preview.committed_at = "committed", idempotency_key, utc_now()
            remaining = session.scalar(select(func.count()).select_from(FlowRunSinkPreview).where(FlowRunSinkPreview.flow_run_id == flow_run_id,
                                                                                                 FlowRunSinkPreview.status != "committed")) or 0
            run.status = "awaiting_commit" if remaining else "completed"
            if not remaining: run.committed_at, run.completed_at = utc_now(), utc_now()
            self._append_run_event(session, run.id, "sink.committed", f"{payload['output_key']} 预览已提交并排队向量同步")
            return {"id": run.id, "status": run.status, "output_key": payload["output_key"], "vector_sync_jobs": vector_jobs}

    def v7_rebuild_manifest(self) -> dict[str, Any]:
        """Return the exact V7-owned physical resources eligible for a rebuild.

        The manifest is DB-derived: no collection, object prefix, or legacy
        resource is discovered from infrastructure and then deleted by guess.
        """
        with self.sessions() as session:
            blob_uris = list(session.scalars(select(SourceVersion.blob_uri)))
            keys: list[str] = []
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
            return {"blob_uris": sorted(set(blob_uris)), "object_keys": sorted(set(keys)),
                    "partition_names": sorted(set(partitions)), "collections": collections,
                    "partition_bindings": sorted(bindings, key=lambda item: (item["collection_name"], item["partition_name"]))}

    def rebuild_v7_database_state(self) -> dict[str, int]:
        """Delete V7 table rows only; schema and external resources are retained."""
        # Order is intentional for MySQL foreign keys.  Never issue DDL here.
        tables = (
            DebugRunFlowMaterialization, FlowRunSinkPreview, FlowRunEvent, FlowNodeArtifactBinding, ArtifactLineage, Artifact, FlowNodeRun,
            KnowledgeAssetItem, KnowledgeItemSource, KnowledgeJobReviewInput, KnowledgeDispatch, SourceReviewSnapshotChunk,
            SourceChunkRevision, FlowRun, DebugRunReviewInput, DebugRunInputSnapshot,
            SourceReviewSnapshot, SourceChunk, SourceChunkSet, DocumentIR, SourcePreparationJob,
            FlowExecutionSnapshot, KnowledgeChunkGeneration,
            VectorDeletionJob, VectorRecordState, VectorSyncJob, KnowledgeChange, KnowledgeItem,
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
            session.execute(update(SourceVersion).values(active_chunk_set_id=None, candidate_chunk_set_id=None))
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
            # jsonschema is a base dependency since parameter normalization moved
            # into the compile/seed path; this fallback only guards minimal images
            # that omit it.  Keep a conservative required-field gate.
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

    @staticmethod
    def _lock_knowledge_library(session: Session, library_id: str) -> KnowledgeLibrary | None:
        """Serialize Sink, review, edit and publish mutations per library."""
        if not session.info.get("knowledge_review_write"):
            connection = session.connection()
            driver = connection.connection.driver_connection
            if connection.dialect.name == "sqlite" and not driver.in_transaction:
                connection.exec_driver_sql("BEGIN IMMEDIATE")
            session.info["knowledge_review_write"] = True
        return session.scalar(
            select(KnowledgeLibrary).where(KnowledgeLibrary.id == library_id)
            .with_for_update().execution_options(populate_existing=True)
        )

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
            library = self._lock_knowledge_library(session, library_id) if library_id else None
            if not library or library.knowledge_type != knowledge_type or graph_mode and library.graph_mode != graph_mode:
                raise ValueError("任务没有为该产出绑定有效知识库")
            if library.origin_type == "central_import":
                library.origin_state = "forked"
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
            if knowledge_type == "graph" and library.graph_schema_snapshot_json is None:
                template_rev = session.get(KnowledgeFlowTemplateRevision, job.knowledge_flow_template_revision_id) if job.knowledge_flow_template_revision_id else None
                template = session.get(KnowledgeFlowTemplate, job.knowledge_flow_template_id)
                definition = template_rev.definition_json if template_rev else (template.definition_json if template else {})
                execution = session.get(FlowExecutionSnapshot, job.execution_snapshot_id) if job.execution_snapshot_id else None
                if execution:
                    definition = execution.compiled_definition_json
                elif (definition or {}).get("template_code"):
                    definition = FLOW_AUTHORING_COMPILER.materialize(definition, template.output_types)
                config = normalize_graph_config((definition or {}).get("graph_config"))
                library.graph_schema_snapshot_json = config.to_dict()
                library.graph_schema_hash = schema_hash(config)
                library.source_template_revision_id = job.knowledge_flow_template_revision_id
            source_versions = {v.id: v for v in session.scalars(select(SourceVersion).where(SourceVersion.id.in_(job.source_version_ids)))}
            if not source_versions:
                raise ValueError("任务来源版本不存在")
            review_inputs = self._assert_job_review_gate(session, job)
            review_lineage: dict[tuple[str, str], tuple[str, str]] = {}
            for review_input in review_inputs:
                for mapping, chunk in session.execute(select(
                    SourceReviewSnapshotChunk, SourceChunk,
                ).join(SourceChunk, SourceChunk.id == SourceReviewSnapshotChunk.source_chunk_id).where(
                    SourceReviewSnapshotChunk.source_review_snapshot_id == review_input.source_review_snapshot_id,
                )):
                    review_lineage[(review_input.source_version_id, chunk.source_chunk_id)] = (
                        mapping.source_chunk_revision_id, review_input.source_review_snapshot_id,
                    )
            def resolved_candidate_chunk_id(candidate: dict[str, Any], version_id: str) -> str:
                explicit = str(candidate.get("source_chunk_id") or "")
                if explicit:
                    return explicit
                matches = [chunk_id for source_key, chunk_id in review_lineage if source_key == version_id]
                if len(matches) == 1:
                    return matches[0]
                raise ReviewGateError("REVIEW_EVIDENCE_MISSING", "候选知识必须明确绑定已审核 SourceChunk",
                                      source_version_id=version_id)
            current = {item.source_knowledge_id: item for item in session.scalars(
                select(KnowledgeItem).where(KnowledgeItem.knowledge_library_id == library.id)
                .order_by(KnowledgeItem.id).with_for_update().execution_options(populate_existing=True)
            )}
            if successful_chunks is None:
                # Compatibility for existing direct-store callers: their
                # candidates may deliberately carry multiple evidence sources.
                successful = [
                    {
                        "source_version_id": version_id,
                        "source_chunk_id": resolved_candidate_chunk_id(candidate, str(version_id)),
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
                version_ids = list(candidate.get("source_version_ids") or job.source_version_ids)
                version_chunk_ids = {
                    str(version_id): resolved_candidate_chunk_id(candidate, str(version_id))
                    for version_id in version_ids
                }
                source_chunk_id = version_chunk_ids[str(version_ids[0])]
                if successful_chunks is not None and len(version_ids) != 1:
                    raise ValueError("候选项必须绑定一个来源版本")
                for version_id in version_ids:
                    scope_key = (str(version_id), version_chunk_ids[str(version_id)])
                    if scope_key not in chunk_scope:
                        raise ValueError("候选项不属于成功分块范围")
                    incoming_keys_by_chunk[scope_key].add(key)
                content = str(candidate["canonical_content"])
                data = dict(candidate.get("data_json") or {})
                digest = content_hash(content, data)
                item = current.get(key)
                before_snapshot = None
                if not item:
                    item = KnowledgeItem(
                        id=new_id("ki"), knowledge_library_id=library.id,
                        knowledge_type_revision_id=revision.id, source_knowledge_id=key,
                        canonical_content=content, data_json=data, content_hash=digest,
                        status="active", review_status="pending", review_revision=1,
                    )
                    session.add(item)
                    # A logical graph relation may be emitted by several source
                    # chunks in the same Sink batch.  Reuse the pending item so
                    # later candidates add Evidence instead of scheduling a
                    # second row with the same library/identity unique key.
                    current[key] = item
                    change = "ADD"; before = None
                elif item.content_hash != digest or item.status != "active":
                    before_snapshot = {
                        "content": item.canonical_content, "data": item.data_json, "status": item.status,
                        "review_status": item.review_status, "review_revision": item.review_revision,
                    }
                    content_changed = item.content_hash != digest
                    before, item.canonical_content, item.data_json, item.content_hash, item.status = item.content_hash, content, data, digest, "active"; item.knowledge_type_revision_id = revision.id; change = "UPDATE"
                    if knowledge_type in KNOWLEDGE_REVIEW_TYPES and content_changed:
                        item.review_status = "pending"
                        item.review_revision += 1
                        item.reviewed_at = item.reviewed_by = item.review_note = None
                else:
                    before, change = item.content_hash, "UNCHANGED"
                for version_id in version_ids:
                    if version_id not in source_versions:
                        raise ValueError("候选项包含任务外来源版本")
                    version_chunk_id = version_chunk_ids[str(version_id)]
                    lineage = review_lineage.get((version_id, version_chunk_id))
                    if not lineage:
                        raise ReviewGateError("REVIEW_EVIDENCE_MISSING", "候选知识不属于任务冻结的人工审核快照",
                                              source_version_id=version_id)
                    exists = session.scalar(select(KnowledgeItemSource).where(
                        KnowledgeItemSource.knowledge_item_id == item.id,
                        KnowledgeItemSource.source_version_id == version_id,
                        KnowledgeItemSource.source_chunk_id == version_chunk_id,
                    ))
                    if not exists:
                        session.add(KnowledgeItemSource(id=new_id("kis"), knowledge_item_id=item.id, source_version_id=version_id,
                            source_chunk_id=version_chunk_id, source_chunk_revision_id=lineage[0],
                            source_review_snapshot_id=lineage[1],
                            source_anchor=str(candidate.get("source_anchor", anchor.get("label", ""))), anchor_json=anchor,
                            evidence_text=str(candidate.get("evidence_text", content)), is_primary=bool(candidate.get("is_primary", False))))
                    else:
                        # Same logical knowledge and content may be regenerated from
                        # a newer approved Chunk revision. Preserve the knowledge
                        # review decision while refreshing its immutable provenance.
                        exists.source_chunk_revision_id = lineage[0]
                        exists.source_review_snapshot_id = lineage[1]
                        if "source_anchor" in candidate or candidate.get("anchor_json") is not None:
                            exists.source_anchor = str(candidate.get("source_anchor", anchor.get("label", "")))
                        if candidate.get("anchor_json") is not None:
                            exists.anchor_json = anchor
                        if "evidence_text" in candidate:
                            exists.evidence_text = str(candidate["evidence_text"])
                        if "is_primary" in candidate:
                            exists.is_primary = bool(candidate["is_primary"])
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
                session.add(KnowledgeChange(id=new_id("kc"), knowledge_job_id=job.id, knowledge_library_id=library.id, knowledge_item_id=item.id, change_type=change, before_hash=before, after_hash=digest, details_json={"source_knowledge_id": key}, before_snapshot_json=before_snapshot, after_snapshot_json={"content": content, "data": data, "status": "active", "review_status": item.review_status, "review_revision": item.review_revision}))
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

    @staticmethod
    def _review_conflict(item: KnowledgeItem, expected_revision: int) -> ReviewGateError:
        return ReviewGateError(
            "KNOWLEDGE_REVIEW_STALE", "知识内容或审核状态已变化，请刷新后重试",
            details={
                "knowledge_item_id": item.id,
                "expected_review_revision": expected_revision,
                "actual_review_revision": item.review_revision,
            },
        )

    @staticmethod
    def _knowledge_item_payload(item: KnowledgeItem, *, source_count: int = 0) -> dict[str, Any]:
        reviewed_at = ((item.reviewed_at if item.reviewed_at and item.reviewed_at.tzinfo else
                        item.reviewed_at.replace(tzinfo=timezone.utc)).isoformat()
                       if item.reviewed_at else None)
        return {
            "id": item.id, "knowledge_library_id": item.knowledge_library_id,
            "source_knowledge_id": item.source_knowledge_id,
            "canonical_content": item.canonical_content, "data": item.data_json,
            "content_hash": item.content_hash, "status": item.status,
            "review_status": item.review_status, "review_revision": item.review_revision,
            "reviewed_at": reviewed_at,
            "reviewed_by": item.reviewed_by, "review_note": item.review_note,
            "source_count": int(source_count), "updated_at": item.updated_at.isoformat(),
        }

    @staticmethod
    def _assert_knowledge_reviewable(library: KnowledgeLibrary, item: KnowledgeItem) -> None:
        if library.status != "active" or item.status != "active":
            raise ReviewGateError("KNOWLEDGE_ITEM_INACTIVE", "只有活动知识可以编辑或审核")
        if library.knowledge_type not in KNOWLEDGE_REVIEW_TYPES:
            raise ReviewGateError("KNOWLEDGE_REVIEW_NOT_APPLICABLE", "该知识类型不启用逐条人工审核")

    def update_knowledge_item(self, item_id: str, *, data: dict[str, Any] | None,
                              canonical_content: str | None, expected_review_revision: int,
                              actor: str = "admin") -> dict[str, Any]:
        with self.sessions.begin() as session:
            library_id = session.scalar(select(KnowledgeItem.knowledge_library_id).where(KnowledgeItem.id == item_id))
            library = self._lock_knowledge_library(session, library_id) if library_id else None
            item = session.get(KnowledgeItem, item_id, with_for_update=True, populate_existing=True) if library else None
            if not library or not item:
                raise ValueError("知识项不存在")
            self._assert_knowledge_reviewable(library, item)
            if item.review_revision != expected_review_revision:
                raise self._review_conflict(item, expected_review_revision)
            before = self._knowledge_item_payload(item)
            next_data = deepcopy(item.data_json)
            if library.knowledge_type == "qa":
                if canonical_content is not None or not isinstance(data, dict):
                    raise ValueError("问答知识编辑必须提交 data.question 和 data.answer")
                question, answer = str(data.get("question") or "").strip(), str(data.get("answer") or "").strip()
                if not question or not answer:
                    raise ValueError("问题和答案不能为空")
                next_data.update(question=question, answer=answer)
                next_content = f"{question} {answer}"
            else:
                if data is not None or canonical_content is None:
                    raise ValueError("文本知识编辑必须提交 canonical_content")
                next_content = str(canonical_content)
                if not next_content.strip():
                    raise ValueError("文本知识内容不能为空")
            revision = session.get(KnowledgeTypeRevision, item.knowledge_type_revision_id) \
                if item.knowledge_type_revision_id else None
            if revision:
                try:
                    from jsonschema import Draft202012Validator
                    errors = sorted(Draft202012Validator(revision.schema_json or {"type": "object"})
                                    .iter_errors(next_data), key=lambda value: list(value.path))
                    if errors:
                        raise ValueError("知识内容不符合当前 Schema：" + errors[0].message)
                except ImportError:
                    pass
            digest = content_hash(next_content, next_data)
            if digest == item.content_hash:
                return self._knowledge_item_payload(item, source_count=int(session.scalar(
                    select(func.count()).select_from(KnowledgeItemSource).where(
                        KnowledgeItemSource.knowledge_item_id == item.id)) or 0))
            old_hash = item.content_hash
            item.canonical_content, item.data_json, item.content_hash = next_content, next_data, digest
            item.review_status, item.review_revision = "pending", item.review_revision + 1
            item.reviewed_at = item.reviewed_by = item.review_note = None
            after = self._knowledge_item_payload(item)
            session.add(KnowledgeChange(
                id=new_id("kc"), knowledge_job_id=None, knowledge_library_id=library.id,
                knowledge_item_id=item.id, change_type="UPDATE", before_hash=old_hash, after_hash=digest,
                details_json={"source_knowledge_id": item.source_knowledge_id, "origin": "manual", "actor": actor},
                before_snapshot_json={"content": before["canonical_content"], "data": before["data"],
                                      "status": before["status"], "review_status": before["review_status"],
                                      "review_revision": before["review_revision"]},
                after_snapshot_json={"content": after["canonical_content"], "data": after["data"],
                                     "status": after["status"], "review_status": after["review_status"],
                                     "review_revision": after["review_revision"]},
            ))
            self.audit(session, "knowledge_item.updated", "knowledge_item", item.id,
                       {"actor": actor, "review_revision": item.review_revision})
            source_count = int(session.scalar(select(func.count()).select_from(KnowledgeItemSource).where(
                KnowledgeItemSource.knowledge_item_id == item.id)) or 0)
            return self._knowledge_item_payload(item, source_count=source_count)

    def review_knowledge_item(self, item_id: str, *, status: str, expected_review_revision: int,
                              review_note: str | None = None, actor: str = "admin") -> dict[str, Any]:
        if status not in {"approved", "rejected"}:
            raise ValueError("知识审核状态必须为 approved 或 rejected")
        with self.sessions.begin() as session:
            library_id = session.scalar(select(KnowledgeItem.knowledge_library_id).where(KnowledgeItem.id == item_id))
            library = self._lock_knowledge_library(session, library_id) if library_id else None
            item = session.get(KnowledgeItem, item_id, with_for_update=True, populate_existing=True) if library else None
            if not library or not item:
                raise ValueError("知识项不存在")
            self._assert_knowledge_reviewable(library, item)
            if item.review_revision != expected_review_revision:
                raise self._review_conflict(item, expected_review_revision)
            item.review_status, item.review_revision = status, item.review_revision + 1
            item.reviewed_at, item.reviewed_by = utc_now(), actor
            item.review_note = str(review_note).strip() if review_note and str(review_note).strip() else None
            self.audit(session, "knowledge_item.reviewed", "knowledge_item", item.id, {
                "status": status, "review_revision": item.review_revision, "actor": actor,
            })
            source_count = int(session.scalar(select(func.count()).select_from(KnowledgeItemSource).where(
                KnowledgeItemSource.knowledge_item_id == item.id)) or 0)
            return self._knowledge_item_payload(item, source_count=source_count)

    def batch_review_knowledge_items(self, library_id: str, *, item_ids: list[str], action: str,
                                     expected_revisions: dict[str, int], review_note: str | None = None,
                                     actor: str = "admin") -> dict[str, Any]:
        status = {"approve": "approved", "reject": "rejected"}.get(action)
        ids = list(dict.fromkeys(str(value) for value in item_ids if str(value)))
        if not status or not ids:
            raise ValueError("批量审核参数无效")
        with self.sessions.begin() as session:
            library = self._lock_knowledge_library(session, library_id)
            if not library:
                raise ValueError("知识库不存在")
            items = list(session.scalars(select(KnowledgeItem).where(
                KnowledgeItem.id.in_(ids), KnowledgeItem.knowledge_library_id == library.id,
            ).order_by(KnowledgeItem.id).with_for_update().execution_options(populate_existing=True)))
            if len(items) != len(ids):
                raise ValueError("批量审核包含不存在或不属于当前知识库的知识项")
            for item in items:
                self._assert_knowledge_reviewable(library, item)
                expected = int(expected_revisions.get(item.id, -1))
                if item.review_revision != expected:
                    raise self._review_conflict(item, expected)
            reviewed_at = utc_now()
            note = str(review_note).strip() if review_note and str(review_note).strip() else None
            for item in items:
                item.review_status, item.review_revision = status, item.review_revision + 1
                item.reviewed_at, item.reviewed_by, item.review_note = reviewed_at, actor, note
            self.audit(session, "knowledge_library.batch_reviewed", "knowledge_library", library.id, {
                "status": status, "item_ids": ids, "actor": actor,
            })
            return {"knowledge_library_id": library.id, "status": status, "updated": len(items),
                    "items": [self._knowledge_item_payload(item) for item in items]}

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
                payload = self._knowledge_item_payload(item, source_count=len(sources))
                payload["source_version_ids"] = [source.source_version_id for source in sources]
                result.append(payload)
            return result

    def list_qa_pairs(self, library_id: str, *, keyword: str = "", status: str = "active",
                      review_status: str = "all", page: int = 1, page_size: int = 50) -> dict[str, Any]:
        page, page_size = max(1, page), min(max(1, page_size), 200)
        if status not in {"active", "inactive", "all"}:
            raise ValueError("问答知识状态筛选无效")
        if review_status not in {"pending", "approved", "rejected", "all"}:
            raise ValueError("问答知识审核状态筛选无效")
        with self.sessions() as session:
            library = session.get(KnowledgeLibrary, library_id)
            if not library:
                raise ValueError("知识库不存在")
            if library.knowledge_type != "qa":
                raise ValueError("知识库类型不匹配")

            source_counts = (
                select(
                    KnowledgeItemSource.knowledge_item_id.label("knowledge_item_id"),
                    func.count(KnowledgeItemSource.id).label("source_count"),
                )
                .group_by(KnowledgeItemSource.knowledge_item_id)
                .subquery()
            )
            query = (
                select(KnowledgeItem, func.coalesce(source_counts.c.source_count, 0))
                .outerjoin(source_counts, source_counts.c.knowledge_item_id == KnowledgeItem.id)
                .where(KnowledgeItem.knowledge_library_id == library_id)
            )
            normalized_keyword = keyword.strip()
            if normalized_keyword:
                question = KnowledgeItem.data_json["question"].as_string()
                answer = KnowledgeItem.data_json["answer"].as_string()
                query = query.where(or_(question.contains(normalized_keyword), answer.contains(normalized_keyword)))
            if status != "all":
                query = query.where(KnowledgeItem.status == status)
            if review_status != "all":
                query = query.where(KnowledgeItem.review_status == review_status)

            total = int(session.scalar(select(func.count()).select_from(query.subquery())) or 0)
            rows = session.execute(
                query.order_by(KnowledgeItem.updated_at.desc(), KnowledgeItem.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            ).all()
            return {
                "items": [self._knowledge_item_payload(item, source_count=int(source_count or 0))
                          for item, source_count in rows],
                "page": page,
                "page_size": page_size,
                "total": total,
            }

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
                      "source_chunk_id": link.source_chunk_id,
                      "source_chunk_revision_id": link.source_chunk_revision_id,
                      "source_review_snapshot_id": link.source_review_snapshot_id,
                      "source": {"id": source.id, "name": source.name, "original_filename": version.original_filename},
                      "source_version": {"id": version.id, "version_no": version.version_no, "sha256": version.sha256}}
                    for link, version, source in rows]

    @staticmethod
    def _graph_identity(library_id: str, *parts: str) -> str:
        return hashlib.sha256("|".join((library_id, *parts)).encode("utf-8")).hexdigest()[:32]

    @staticmethod
    def _add_graph_node(nodes: dict[str, dict[str, Any]], node_id: str, name: str, type_code: Any, type_label: Any, description: Any, aliases: Any) -> None:
        node = nodes.get(node_id)
        label = type_label or type_code or "未分类"
        if node is None:
            nodes[node_id] = {"id": node_id, "name": name, "type_code": type_code, "type": label,
                              "type_label": type_label, "description": description, "aliases": list(aliases or [])}
            return
        if not node.get("description") and description:
            node["description"] = description
        if not node.get("type_label") and type_label:
            node["type"] = label; node["type_label"] = type_label
        node["aliases"] = sorted(set(node.get("aliases") or []) | set(aliases or []))

    def _graph_projection(self, session: Session, library_id: str) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
        library = session.get(KnowledgeLibrary, library_id)
        if not library or library.knowledge_type != "graph":
            raise ValueError("图谱知识库不存在或类型不匹配")
        nodes: dict[str, dict[str, Any]] = {}
        edges: dict[str, dict[str, Any]] = {}
        facts: dict[str, dict[str, Any]] = {}
        for item in session.scalars(select(KnowledgeItem).where(KnowledgeItem.knowledge_library_id == library_id, KnowledgeItem.status == "active")):
            data = item.data_json or {}
            if library.graph_mode == "semantic":
                source, target, relation = (data.get(key) or {} for key in ("source_entity", "target_entity", "relation"))
                source_name, target_name = str(source.get("name", "")).strip(), str(target.get("name", "")).strip()
                if not source_name or not target_name:
                    continue
                source_id = str(source.get("entity_id") or self._graph_identity(library_id, "entity", str(source.get("type") or ""), source_name.casefold()))
                target_id = str(target.get("entity_id") or self._graph_identity(library_id, "entity", str(target.get("type") or ""), target_name.casefold()))
                relation_id = str(relation.get("relation_id") or self._graph_identity(library_id, "relation", source_id, str(relation.get("type") or ""), target_id))
                self._add_graph_node(nodes, source_id, source_name, source.get("type"), source.get("type_label"), source.get("description"), source.get("aliases"))
                self._add_graph_node(nodes, target_id, target_name, target.get("type"), target.get("type_label"), target.get("description"), target.get("aliases"))
                edge = edges.setdefault(relation_id, {
                    "id": relation_id, "source": source_id, "target": target_id,
                    "predicate": relation.get("type_label") or relation.get("type") or relation.get("description") or "",
                    "relation_type": relation.get("type"), "relation_type_label": relation.get("type_label"),
                    "description": relation.get("description"), "keywords": relation.get("keywords") or [], "weight": relation.get("weight"),
                    "graph_mode": "semantic", "knowledge_item_ids": []})
                edge["knowledge_item_ids"].append(item.id)
            else:
                subject, predicate, obj = (str(data.get(key, "")).strip() for key in ("subject", "predicate", "object"))
                if not subject or not predicate or not obj:
                    continue
                subject_id = self._graph_identity(library_id, "entity", str(data.get("subject_type") or ""), subject.casefold())
                self._add_graph_node(nodes, subject_id, subject, data.get("subject_type"), data.get("subject_type_label"), None, None)
                literal = (data.get("data") or {}).get("object_kind") == "literal"
                if literal:
                    fact_id = self._graph_identity(library_id, "fact", subject.casefold(), predicate.casefold(), str((data.get("data") or {}).get("literal_normalized_value") or obj))
                    fact = facts.setdefault(fact_id, {
                        "id": fact_id, "subject_entity_id": subject_id, "subject": subject,
                        "predicate": predicate, "predicate_code": data.get("predicate_code"),
                        "object": obj, "object_kind": "literal",
                        "literal_datatype": (data.get("data") or {}).get("literal_datatype"),
                        "literal_unit": (data.get("data") or {}).get("literal_unit"),
                        "literal_raw_value": (data.get("data") or {}).get("literal_raw_value") or obj,
                        "literal_normalized_value": (data.get("data") or {}).get("literal_normalized_value"),
                        "knowledge_item_ids": []})
                    fact["knowledge_item_ids"].append(item.id)
                else:
                    obj_id = self._graph_identity(library_id, "entity", str(data.get("object_type") or ""), obj.casefold())
                    self._add_graph_node(nodes, obj_id, obj, data.get("object_type"), data.get("object_type_label"), None, None)
                    relation_id = self._graph_identity(library_id, "relation", subject.casefold(), predicate.casefold(), obj.casefold())
                    edge = edges.setdefault(relation_id, {
                        "id": relation_id, "source": subject_id, "target": obj_id,
                        "predicate": predicate, "relation_type": data.get("predicate_code"), "relation_type_label": None,
                        "description": None, "keywords": [], "weight": None,
                        "graph_mode": "triple", "knowledge_item_ids": []})
                    edge["knowledge_item_ids"].append(item.id)
        return nodes, edges, facts

    def graph_entity_search(self, library_id: str, query: str, limit: int = 20) -> list[dict[str, Any]]:
        with self.sessions() as session:
            nodes, _, _ = self._graph_projection(session, library_id)
            needle = query.casefold().strip()
            def matches(node: dict[str, Any]) -> bool:
                if not needle:
                    return True
                haystack = [node["name"], node.get("description") or "", *((node.get("aliases") or []))]
                return any(needle in str(part).casefold() for part in haystack)
            values = [node for node in nodes.values() if matches(node)]
            return sorted(values, key=lambda item: item["name"])[:max(1, min(limit, 100))]

    def graph_overview(self, library_id: str, *, node_limit: int = 80, edge_limit: int = 160) -> dict[str, Any]:
        """Return a bounded, stable overview biased toward connected entities."""
        with self.sessions() as session:
            library = session.get(KnowledgeLibrary, library_id)
            nodes, edges, facts = self._graph_projection(session, library_id)
            degrees = {node_id: 0 for node_id in nodes}
            for edge in edges.values():
                degrees[edge["source"]] = degrees.get(edge["source"], 0) + 1
                degrees[edge["target"]] = degrees.get(edge["target"], 0) + 1
            selected_node_ids = {
                item["id"] for item in sorted(
                    nodes.values(),
                    key=lambda item: (-degrees.get(item["id"], 0), item["name"].casefold(), item["id"]),
                )[:max(1, min(node_limit, 80))]
            }
            selected_edges = [
                edge for edge in edges.values()
                if edge["source"] in selected_node_ids and edge["target"] in selected_node_ids
            ]
            selected_edges.sort(key=lambda edge: (
                -(degrees.get(edge["source"], 0) + degrees.get(edge["target"], 0)),
                edge["predicate"].casefold(), edge["id"],
            ))
            return {
                "graph_mode": library.graph_mode or "triple",
                "stats": {
                    "entity_count": len(nodes),
                    "relation_count": len(edges),
                    "literal_fact_count": len(facts),
                    "entity_type_count": len({node.get("type_code") for node in nodes.values()}),
                },
                "nodes": [nodes[node_id] for node_id in sorted(
                    selected_node_ids,
                    key=lambda node_id: (-degrees.get(node_id, 0), nodes[node_id]["name"].casefold(), node_id),
                )],
                "edges": selected_edges[:max(1, min(edge_limit, 160))],
                "facts": list(facts.values()),
            }

    def graph_type_facets(self, library_id: str) -> dict[str, Any]:
        """Actual entity/relation type facets extracted into the graph (full set)."""
        with self.sessions() as session:
            nodes, edges, _ = self._graph_projection(session, library_id)
            entity_facets: dict[str, dict[str, Any]] = {}
            for node in nodes.values():
                key = str(node.get("type_code") or node.get("type") or "未分类")
                facet = entity_facets.setdefault(key, {
                    "code": key, "label": node.get("type_label") or node.get("type") or key, "count": 0,
                })
                facet["count"] += 1
            relation_facets: dict[str, dict[str, Any]] = {}
            for edge in edges.values():
                key = str(edge.get("relation_type") or edge.get("predicate") or "未分类")
                facet = relation_facets.setdefault(key, {
                    "code": key,
                    "label": edge.get("relation_type_label") or edge.get("predicate") or key,
                    "count": 0,
                })
                facet["count"] += 1
            sort_key = lambda item: (str(item["label"]).casefold(), item["code"])
            return {
                "entity_types": sorted(entity_facets.values(), key=sort_key),
                "relation_types": sorted(relation_facets.values(), key=sort_key),
            }

    @staticmethod
    def _graph_neighbor_selection(
        nodes: dict[str, dict[str, Any]],
        edges: dict[str, dict[str, Any]],
        entity_id: str,
        depth: int,
        *,
        entity_types: set[str] | None = None,
        relation_types: set[str] | None = None,
    ) -> tuple[set[str], set[str]]:
        if depth not in {1, 2}:
            raise ValueError("图谱邻居深度只支持 1 或 2")
        if entity_id not in nodes:
            raise ValueError("图谱实体不存在")
        entity_types = {str(value) for value in (entity_types or set()) if str(value)}
        relation_types = {str(value) for value in (relation_types or set()) if str(value)}

        def node_allowed(node_id: str) -> bool:
            if node_id == entity_id or not entity_types:
                return True
            node = nodes[node_id]
            return str(node.get("type_code") or node.get("type") or "") in entity_types

        def edge_allowed(edge: dict[str, Any]) -> bool:
            if relation_types and str(edge.get("relation_type") or edge.get("predicate") or "") not in relation_types:
                return False
            return node_allowed(edge["source"]) and node_allowed(edge["target"])

        selected, frontier = {entity_id}, {entity_id}
        selected_edges: set[str] = set()
        for _ in range(depth):
            next_frontier: set[str] = set()
            for edge_id, edge in edges.items():
                if not edge_allowed(edge):
                    continue
                if edge["source"] in frontier or edge["target"] in frontier:
                    selected_edges.add(edge_id)
                    next_frontier.update((edge["source"], edge["target"]))
            frontier = next_frontier - selected
            selected.update(next_frontier)
        return selected, selected_edges

    @staticmethod
    def _graph_neighbor_facets(
        nodes: dict[str, dict[str, Any]],
        edges: dict[str, dict[str, Any]],
        selected: set[str],
        selected_edges: set[str],
        center_id: str,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        entity_facets: dict[str, dict[str, Any]] = {}
        for node_id in selected - {center_id}:
            node = nodes[node_id]
            key = str(node.get("type_code") or node.get("type") or "未分类")
            facet = entity_facets.setdefault(key, {
                "key": key, "label": node.get("type_label") or node.get("type") or key, "count": 0,
            })
            facet["count"] += 1
        relation_facets: dict[str, dict[str, Any]] = {}
        for edge_id in selected_edges:
            edge = edges[edge_id]
            key = str(edge.get("relation_type") or edge.get("predicate") or "未分类")
            facet = relation_facets.setdefault(key, {
                "key": key, "label": edge.get("relation_type_label") or edge.get("predicate") or key, "count": 0,
            })
            facet["count"] += 1
        sort_key = lambda item: (str(item["label"]).casefold(), item["key"])
        return sorted(entity_facets.values(), key=sort_key), sorted(relation_facets.values(), key=sort_key)

    def graph_neighbor_preview(
        self,
        library_id: str,
        entity_id: str,
        depth: int = 1,
        *,
        entity_types: set[str] | None = None,
        relation_types: set[str] | None = None,
    ) -> dict[str, Any]:
        with self.sessions() as session:
            nodes, edges, _ = self._graph_projection(session, library_id)
            selected, selected_edges = self._graph_neighbor_selection(
                nodes, edges, entity_id, depth,
                entity_types=entity_types, relation_types=relation_types,
            )
            entity_facets, relation_facets = self._graph_neighbor_facets(
                nodes, edges, selected, selected_edges, entity_id,
            )
            neighbor_count = max(0, len(selected) - 1)
            return {
                "center_id": entity_id,
                "depth": depth,
                "node_count": len(selected),
                "neighbor_count": neighbor_count,
                "edge_count": len(selected_edges),
                "notice_required": neighbor_count > GRAPH_NEIGHBOR_NOTICE_THRESHOLD,
                "confirmation_required": neighbor_count > GRAPH_NEIGHBOR_CONFIRM_THRESHOLD,
                "entity_type_facets": entity_facets,
                "relation_type_facets": relation_facets,
            }

    def graph_neighbors(
        self,
        library_id: str,
        entity_id: str,
        depth: int = 1,
        *,
        entity_types: set[str] | None = None,
        relation_types: set[str] | None = None,
        confirm_large: bool = False,
    ) -> dict[str, Any]:
        with self.sessions() as session:
            nodes, edges, facts = self._graph_projection(session, library_id)
            selected, selected_edges = self._graph_neighbor_selection(
                nodes, edges, entity_id, depth,
                entity_types=entity_types, relation_types=relation_types,
            )
            neighbor_count = max(0, len(selected) - 1)
            if neighbor_count > GRAPH_NEIGHBOR_CONFIRM_THRESHOLD and not confirm_large:
                raise ValueError(f"预计展开 {neighbor_count} 个邻居，请筛选或确认后重试")
            local_facts = [fact for fact in facts.values() if fact["subject_entity_id"] in selected]
            node_sort_key = lambda node_id: (nodes[node_id]["name"].casefold(), node_id)
            edge_sort_key = lambda edge_id: (edges[edge_id]["predicate"].casefold(), edge_id)
            return {
                "center_id": entity_id,
                "depth": depth,
                "node_count": len(selected),
                "neighbor_count": neighbor_count,
                "edge_count": len(selected_edges),
                "nodes": [nodes[node_id] for node_id in sorted(selected, key=node_sort_key)],
                "edges": [edges[edge_id] for edge_id in sorted(selected_edges, key=edge_sort_key)],
                "facts": local_facts,
            }

    def graph_entity_detail(self, library_id: str, entity_id: str) -> dict[str, Any]:
        with self.sessions() as session:
            nodes, edges, facts = self._graph_projection(session, library_id)
            if entity_id not in nodes:
                raise ValueError("图谱实体不存在")
            related = [edge for edge in edges.values() if entity_id in (edge["source"], edge["target"])]
            entity_facts = [fact for fact in facts.values() if fact["subject_entity_id"] == entity_id]
            evidence_count = sum(len(edge["knowledge_item_ids"]) for edge in related)
            return {**nodes[entity_id], "relation_count": len(related), "relation_ids": [edge["id"] for edge in related],
                    "facts": entity_facts, "evidence_count": evidence_count}

    def graph_relation_evidence(self, library_id: str, relation_id: str) -> dict[str, Any]:
        with self.sessions() as session:
            _, edges, _ = self._graph_projection(session, library_id)
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

    @staticmethod
    def _vector_job_payload(item: VectorSyncJob) -> dict[str, Any]:
        return {
            "id": item.id, "knowledge_library_id": item.knowledge_library_id,
            "index_profile_id": item.index_profile_id, "asset_version_id": item.asset_version_id,
            "status": item.status, "total_count": item.total_count,
            "synced_count": item.synced_count, "attempt_count": item.attempt_count,
            "error": item.error, "publish_idempotency_key": item.publish_idempotency_key,
        }

    def _knowledge_publish_preflight(self, session: Session, library: KnowledgeLibrary, *,
                                     target_available: bool = True,
                                     target_error: str | None = None) -> dict[str, Any]:
        review_required = library.knowledge_type in KNOWLEDGE_REVIEW_TYPES
        item_query = select(KnowledgeItem).where(
            KnowledgeItem.knowledge_library_id == library.id, KnowledgeItem.status == "active",
        ).order_by(KnowledgeItem.id)
        if session.info.get("knowledge_review_write"):
            item_query = item_query.with_for_update().execution_options(populate_existing=True)
        active_items = list(session.scalars(item_query))
        counts = {
            "total": len(active_items),
            "pending": sum(item.review_status == "pending" for item in active_items),
            "approved": sum(item.review_status == "approved" for item in active_items),
            "rejected": sum(item.review_status == "rejected" for item in active_items),
        }
        selected = [item for item in active_items if item.review_status == "approved"] \
            if review_required else active_items
        issues: list[dict[str, str]] = []
        def issue(code: str, message: str) -> None:
            issues.append({"code": code, "message": message})
        if library.status != "active":
            issue("KNOWLEDGE_LIBRARY_INACTIVE", "知识库不是活动状态")
        if not active_items:
            issue("KNOWLEDGE_ITEMS_EMPTY", "知识库没有活动知识")
        if review_required and counts["pending"]:
            issue("KNOWLEDGE_REVIEW_PENDING", f"当前还有 {counts['pending']} 条知识未完成审核")
        if review_required and not selected:
            issue("KNOWLEDGE_APPROVED_EMPTY", "没有审核通过、可进入正式版本的知识")
        profiles = self._index_profile_snapshots_for_library(session, library)
        if not profiles:
            issue("KNOWLEDGE_INDEX_PROFILE_MISSING", "知识库没有可用的已发布 Index Profile")
        for profile in profiles:
            embedding = session.get(EmbeddingProfile, profile.embedding_profile_id)
            if not embedding:
                issue("KNOWLEDGE_EMBEDDING_PROFILE_MISSING", f"Index Profile {profile.code} 缺少 Embedding Profile")
            if profile.embedding_serving_id and not session.scalar(select(EmbeddingServing).where(
                    EmbeddingServing.serving_code == profile.embedding_serving_id)):
                issue("KNOWLEDGE_EMBEDDING_SERVING_MISSING", f"Index Profile {profile.code} 缺少 Embedding Serving")
            if profile.storage_contract_revision_id and not session.get(
                    StorageContractRevision, profile.storage_contract_revision_id):
                issue("KNOWLEDGE_STORAGE_CONTRACT_MISSING", f"Index Profile {profile.code} 缺少 Storage Contract Revision")
        if not target_available:
            issue("AUTHORING_MILVUS_TARGET_UNAVAILABLE", target_error or "没有可用的 verified Authoring Milvus Target")
        material: list[dict[str, Any]] = []
        for item in selected:
            evidence = list(session.scalars(select(KnowledgeItemSource).where(
                KnowledgeItemSource.knowledge_item_id == item.id,
            ).order_by(KnowledgeItemSource.id)))
            if not evidence:
                issue("KNOWLEDGE_EVIDENCE_MISSING", f"知识项 {item.id} 缺少 Evidence")
                continue
            evidence_material = []
            for link in evidence:
                snapshot = session.get(SourceReviewSnapshot, link.source_review_snapshot_id) \
                    if link.source_review_snapshot_id else None
                revision = session.get(SourceChunkRevision, link.source_chunk_revision_id) \
                    if link.source_chunk_revision_id else None
                if not snapshot or snapshot.status != "approved" or not revision:
                    issue("KNOWLEDGE_EVIDENCE_NOT_APPROVED", f"知识项 {item.id} 包含未审核 Evidence")
                    continue
                evidence_material.append({
                    "source_version_id": link.source_version_id,
                    "source_chunk_id": link.source_chunk_id,
                    "source_chunk_revision_id": revision.id,
                    "source_review_snapshot_id": snapshot.id,
                    "anchor": link.anchor_json,
                    "evidence_hash": hashlib.sha256(link.evidence_text.encode("utf-8")).hexdigest(),
                })
            material.append({
                "item_id": item.id, "source_knowledge_id": item.source_knowledge_id,
                "content_hash": item.content_hash, "review_status": item.review_status,
                "review_revision": item.review_revision,
                "evidence": evidence_material,
            })
        digest = hashlib.sha256(json.dumps(
            material, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")).hexdigest()
        active_jobs = list(session.scalars(select(VectorSyncJob).where(
            VectorSyncJob.knowledge_library_id == library.id,
            VectorSyncJob.status.in_(("queued", "running")),
        ).order_by(VectorSyncJob.created_at, VectorSyncJob.id)))
        if active_jobs:
            issue("VECTOR_PUBLISH_IN_PROGRESS", "当前知识库已有向量版本正在构建")
        latest_ready = list(session.scalars(select(KnowledgeAssetVersion).where(
            KnowledgeAssetVersion.knowledge_library_id == library.id,
            KnowledgeAssetVersion.status == "ready",
            KnowledgeAssetVersion.review_gate_status == "approved",
        ).order_by(KnowledgeAssetVersion.version_no.desc())))
        matching_profile_ids = {asset.index_profile_id for asset in latest_ready
                                if asset.review_snapshot_digest == digest}
        current_ready = bool(profiles) and len(matching_profile_ids) == len(profiles)
        failed_current = bool(session.scalar(select(func.count()).select_from(VectorSyncJob).join(
            KnowledgeAssetVersion, KnowledgeAssetVersion.id == VectorSyncJob.asset_version_id,
        ).where(
            VectorSyncJob.knowledge_library_id == library.id,
            VectorSyncJob.status == "failed",
            KnowledgeAssetVersion.review_snapshot_digest == digest,
        )))
        latest_job = session.scalar(select(VectorSyncJob).where(
            VectorSyncJob.knowledge_library_id == library.id,
        ).order_by(VectorSyncJob.created_at.desc(), VectorSyncJob.id.desc()).limit(1))
        if active_jobs:
            vector_state = "building"
        elif current_ready:
            vector_state = "ready"
        elif failed_current or (latest_job and latest_job.status == "failed" and not latest_ready):
            vector_state = "failed"
        elif latest_ready:
            vector_state = "stale"
        else:
            vector_state = "not_published"
        return {
            "knowledge_library_id": library.id, "review_required": review_required,
            "scope": "all_approved" if review_required else "all_active",
            "counts": counts, "selected_count": len(selected), "selected_items": selected,
            "profiles": profiles, "snapshot_digest": digest,
            "issues": issues, "can_publish": not issues,
            "vector_state": vector_state, "vector_stale": bool(latest_ready) and not current_ready,
            "has_ready_asset": bool(latest_ready), "current_ready": current_ready,
            "active_jobs": active_jobs, "latest_ready": latest_ready,
        }

    def knowledge_review_summary(self, library_id: str, *, target_available: bool = True,
                                 target_error: str | None = None) -> dict[str, Any]:
        with self.sessions() as session:
            library = session.get(KnowledgeLibrary, library_id)
            if not library:
                raise ValueError("知识库不存在")
            value = self._knowledge_publish_preflight(
                session, library, target_available=target_available, target_error=target_error,
            )
            return {
                key: value[key] for key in (
                    "knowledge_library_id", "review_required", "scope", "counts", "selected_count",
                    "snapshot_digest", "issues", "can_publish", "vector_state", "vector_stale",
                    "has_ready_asset", "current_ready",
                )
            } | {
                "active_jobs": [self._vector_job_payload(item) for item in value["active_jobs"]],
                "latest_ready_versions": [{
                    "id": item.id, "version_no": item.version_no,
                    "index_profile_id": item.index_profile_id, "status": item.status,
                    "partition_name": item.partition_name, "item_count": item.item_count,
                    "ready_at": item.ready_at.isoformat() if item.ready_at else None,
                } for item in value["latest_ready"]],
            }

    def publish_knowledge_vectors(self, library_id: str, *, scope: str,
                                  expected_snapshot_digest: str, idempotency_key: str,
                                  target_available: bool = True,
                                  target_error: str | None = None) -> list[dict[str, Any]]:
        if not idempotency_key or len(idempotency_key) > 64:
            raise ValueError("向量发布幂等键无效")
        with self.sessions.begin() as session:
            library = self._lock_knowledge_library(session, library_id)
            if not library:
                raise ValueError("知识库不存在")
            existing = list(session.scalars(select(VectorSyncJob).where(
                VectorSyncJob.knowledge_library_id == library.id,
                VectorSyncJob.publish_idempotency_key == idempotency_key,
            ).order_by(VectorSyncJob.index_profile_id)))
            if existing:
                assets = [session.get(KnowledgeAssetVersion, item.asset_version_id) for item in existing]
                if any(not asset or asset.review_snapshot_digest != expected_snapshot_digest for asset in assets):
                    raise ReviewGateError("VECTOR_PUBLISH_IDEMPOTENCY_CONFLICT", "幂等键已用于另一份知识快照")
                return [self._vector_job_payload(item) for item in existing]
            value = self._knowledge_publish_preflight(
                session, library, target_available=target_available, target_error=target_error,
            )
            if scope != value["scope"]:
                raise ReviewGateError("VECTOR_PUBLISH_SCOPE_INVALID", f"该知识库只允许 {value['scope']} 发布")
            if value["snapshot_digest"] != expected_snapshot_digest:
                raise ReviewGateError("VECTOR_PUBLISH_STALE", "知识内容、审核状态或 Evidence 已变化，请重新确认", details={
                    "expected_snapshot_digest": expected_snapshot_digest,
                    "actual_snapshot_digest": value["snapshot_digest"],
                })
            if value["issues"]:
                raise ReviewGateError("VECTOR_PUBLISH_BLOCKED", value["issues"][0]["message"],
                                      counts=value["counts"], details={"issues": value["issues"]})
            next_version = int(session.scalar(select(func.max(KnowledgeAssetVersion.version_no)).where(
                KnowledgeAssetVersion.knowledge_library_id == library.id,
            )) or 0)
            jobs: list[VectorSyncJob] = []
            assets: list[KnowledgeAssetVersion] = []
            for profile in value["profiles"]:
                embedding = session.get(EmbeddingProfile, profile.embedding_profile_id)
                serving = session.scalar(select(EmbeddingServing).where(
                    EmbeddingServing.serving_code == profile.embedding_serving_id,
                )) if profile.embedding_serving_id else None
                next_version += 1
                asset = KnowledgeAssetVersion(
                    id=new_id("kav"), knowledge_library_id=library.id, version_no=next_version,
                    index_profile_id=profile.id, index_profile_revision_id=profile.revision_id,
                    storage_contract_revision_id=profile.storage_contract_revision_id,
                    embedding_serving_id=profile.embedding_serving_id,
                    embedding_model=serving.model_name if serving else embedding.model,
                    embedding_dimension=serving.dimension if serving else embedding.dimension,
                    collection_name=profile.collection_name,
                    partition_name=f"{library.partition_name}__v{next_version}", status="building",
                    review_snapshot_digest=value["snapshot_digest"], review_gate_status="approved",
                )
                job = VectorSyncJob(
                    id=new_id("vsj"), knowledge_library_id=library.id,
                    index_profile_id=profile.id, total_count=value["selected_count"],
                    asset_version_id=asset.id, publish_idempotency_key=idempotency_key,
                )
                session.add(asset); session.add(job); assets.append(asset); jobs.append(job)
                freeze_asset_items(session, asset, value["selected_items"])
            self.audit(session, "vector_publish.queued", "knowledge_library", library.id, {
                "scope": scope, "snapshot_digest": value["snapshot_digest"],
                "jobs": [item.id for item in jobs], "asset_versions": [item.id for item in assets],
            })
            return [self._vector_job_payload(item) for item in jobs]

    def create_vector_sync_jobs(self, library_id: str) -> list[dict[str, Any]]:
        """Compatibility entry for explicit internal/manual callers; never called by Knowledge Runner."""
        summary = self.knowledge_review_summary(library_id)
        return self.publish_knowledge_vectors(
            library_id, scope=summary["scope"], expected_snapshot_digest=summary["snapshot_digest"],
            idempotency_key=uuid.uuid4().hex,
        )

    def vector_sync_context(self, job_id: str) -> dict[str, Any]:
        with self.sessions() as session:
            job = session.get(VectorSyncJob, job_id)
            if not job:
                raise ValueError("向量同步任务不存在")
            library = session.get(KnowledgeLibrary, job.knowledge_library_id)
            if not library:
                raise ValueError("向量同步任务引用的知识库不存在")
            asset = session.get(KnowledgeAssetVersion, job.asset_version_id) if job.asset_version_id else None
            if not asset or asset.review_gate_status != "approved" or not asset.review_snapshot_digest:
                raise ReviewGateError("VECTOR_REVIEW_GATE", "候选 AssetVersion 未通过人工审核 Gate，禁止写入 Milvus")
            profile_row = session.get(KnowledgeIndexProfile, job.index_profile_id)
            revision = session.get(KnowledgeIndexProfileRevision, asset.index_profile_revision_id) \
                if asset else (session.get(KnowledgeIndexProfileRevision, profile_row.current_revision_id)
                               if profile_row and profile_row.current_revision_id else None)
            profile = SimpleNamespace(
                id=profile_row.id, code=profile_row.code, knowledge_type=profile_row.knowledge_type,
                collection_name=revision.collection_name, embedding_profile_id=revision.embedding_profile_id,
                embedding_serving_id=revision.embedding_serving_id,
                embedding_input=revision.embedding_input, fields_json=revision.fields_json,
                revision_id=revision.id, storage_contract_revision_id=revision.storage_contract_revision_id,
                collection_policy=revision.collection_policy,
            ) if profile_row and revision else None
            if not profile:
                raise ValueError("向量同步任务引用的已发布 Index Profile 不存在")
            embedding = session.get(EmbeddingProfile, profile.embedding_profile_id)
            storage_contract = session.get(StorageContractRevision, profile.storage_contract_revision_id) if profile.storage_contract_revision_id else None
            items = vector_items(session, asset.id)
            if len(items) != job.total_count:
                raise ValueError("AssetVersion 冻结条目数量与向量任务不一致")
            live_ids = set(session.scalars(select(KnowledgeItem.id).where(
                KnowledgeItem.id.in_([item.id for item in items]), KnowledgeItem.status == "active",
            )))
            if library.status != "active" or len(live_ids) != len(items):
                raise ValueError("候选资产的知识输入已撤回或删除，禁止重新写入向量")
            if job.asset_version_id and not asset:
                raise ValueError("向量同步任务引用的 AssetVersion 不存在")
            return {"job": job, "library": library, "profile": profile, "embedding": embedding,
                    "storage_contract": storage_contract, "items": items, "asset_version": asset}

    def mark_asset_version_verifying(self, job_id: str) -> dict[str, Any]:
        with self.sessions.begin() as session:
            job = session.get(VectorSyncJob, job_id)
            asset = session.get(KnowledgeAssetVersion, job.asset_version_id) if job and job.asset_version_id else None
            if not job or not asset:
                raise ValueError("向量同步任务没有候选 AssetVersion")
            if asset.status == "ready":
                return {"id": asset.id, "status": asset.status, "partition_name": asset.partition_name}
            asset.status, asset.error = "verifying", None
            return {"id": asset.id, "status": asset.status, "partition_name": asset.partition_name}

    def freeze_asset_authoring_connection(self, job_id: str, revision_id: str | None,
                                           fingerprint: str) -> dict[str, Any]:
        with self.sessions.begin() as session:
            job = session.get(VectorSyncJob, job_id)
            asset = session.get(KnowledgeAssetVersion, job.asset_version_id) if job and job.asset_version_id else None
            if not job or not asset:
                raise ValueError("向量同步任务没有候选 AssetVersion")
            if asset.authoring_connection_fingerprint and asset.authoring_connection_fingerprint != fingerprint:
                raise ValueError("AssetVersion 已冻结到另一知识生产 Milvus 连接")
            asset.authoring_target_revision_id = revision_id
            asset.authoring_connection_fingerprint = fingerprint
            return {"asset_version_id": asset.id, "revision_id": revision_id, "fingerprint": fingerprint}

    def finish_vector_sync(self, job_id: str, vector_rows: Iterable[dict[str, Any]], error: str | None = None,
                           *, asset_count: int | None = None, asset_digest: str | None = None) -> dict[str, Any]:
        with self.sessions.begin() as session:
            job = session.get(VectorSyncJob, job_id)
            if not job:
                raise ValueError("向量同步任务不存在")
            asset = session.get(KnowledgeAssetVersion, job.asset_version_id) if job.asset_version_id else None
            if error:
                job.status, job.error = "failed", error
                job.lease_owner, job.lease_expires_at = None, None
                session.execute(delete(KnowledgeLibraryWorkLease).where(
                    KnowledgeLibraryWorkLease.work_kind == "vector_sync",
                    KnowledgeLibraryWorkLease.work_id == job.id,
                ))
                if asset and asset.status != "ready":
                    asset.status, asset.error = "failed", error
                return {"id": job.id, "status": job.status, "asset_version_id": job.asset_version_id, "error": error}
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
            job.lease_owner, job.lease_expires_at = None, None
            session.execute(delete(KnowledgeLibraryWorkLease).where(
                KnowledgeLibraryWorkLease.work_kind == "vector_sync",
                KnowledgeLibraryWorkLease.work_id == job.id,
            ))
            if asset:
                asset.status, asset.error = "ready", None
                asset.item_count = int(asset_count if asset_count is not None else count)
                asset.content_digest = asset_digest
                asset.ready_at, asset.unreferenced_at = utc_now(), utc_now()
                asset.last_verification_status = "consistent"
                asset.last_verified_at = utc_now()
                asset.last_observed_count = asset.item_count
                asset.last_observed_digest = asset.content_digest
                asset.last_verification_error = None
            self.audit(session, "vector_sync.ready", "vector_sync_job", job.id, {
                "synced_count": count, "asset_version_id": job.asset_version_id,
                "asset_digest": asset_digest,
            })
            return {"id": job.id, "status": job.status, "synced_count": count,
                    "asset_version_id": job.asset_version_id,
                    "partition_name": asset.partition_name if asset else None}

    def list_vector_sync_jobs(self, library_id: str | None = None) -> list[dict[str, Any]]:
        with self.sessions() as session:
            query = select(VectorSyncJob).order_by(VectorSyncJob.created_at.desc())
            if library_id:
                query = query.where(VectorSyncJob.knowledge_library_id == library_id)
            return [{"id": item.id, "knowledge_library_id": item.knowledge_library_id,
                      "index_profile_id": item.index_profile_id, "asset_version_id": item.asset_version_id,
                      "status": item.status, "total_count": item.total_count,
                      "synced_count": item.synced_count, "attempt_count": item.attempt_count, "error": item.error,
                      "publish_idempotency_key": item.publish_idempotency_key,
                      "created_at": item.created_at.isoformat()} for item in session.scalars(query)]

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
            versions = [{
                "id": item.id, "version_no": item.version_no, "index_profile_id": item.index_profile_id,
                "index_profile_revision_id": item.index_profile_revision_id,
                "embedding_serving_id": item.embedding_serving_id,
                "embedding_model": item.embedding_model,
                "embedding_dimension": item.embedding_dimension,
                "collection_name": item.collection_name, "partition_name": item.partition_name,
                "status": item.status, "item_count": item.item_count,
                "content_digest": item.content_digest,
                "ready_at": item.ready_at.isoformat() if item.ready_at else None,
            } for item in session.scalars(select(KnowledgeAssetVersion).where(
                KnowledgeAssetVersion.knowledge_library_id == library_id,
            ).order_by(KnowledgeAssetVersion.version_no.desc()))]
            review = self._knowledge_publish_preflight(session, library)
            return {"knowledge_library_id": library_id, "ready": bool(review["current_ready"]),
                    "has_ready_asset": review["has_ready_asset"], "vector_state": review["vector_state"],
                    "vector_stale": review["vector_stale"], "snapshot_digest": review["snapshot_digest"],
                    "jobs": jobs, "record_states": states, "asset_versions": versions}

    @staticmethod
    def _asset_ids_in_json(value: Any) -> set[str]:
        found: set[str] = set()
        if isinstance(value, dict):
            for key, child in value.items():
                if key in {"asset_version_id", "knowledge_asset_version_id"} and isinstance(child, str):
                    found.add(child)
                elif key in {"asset_version_ids", "knowledge_asset_version_ids"} and isinstance(child, list):
                    found.update(str(item) for item in child if item)
                else:
                    found.update(V7Store._asset_ids_in_json(child))
        elif isinstance(value, list):
            for child in value:
                found.update(V7Store._asset_ids_in_json(child))
        return found

    def vector_inventory_metadata(self) -> dict[str, Any]:
        """Return the DataForge side of the live Milvus inventory join."""
        gc_plan = self.knowledge_asset_gc_plan()
        eligible_ids = {str(item["asset_version_id"]) for item in gc_plan["eligible"]}
        with self.sessions() as session:
            libraries = {item.id: item for item in session.scalars(select(KnowledgeLibrary))}
            profiles = {item.id: item for item in session.scalars(select(KnowledgeIndexProfile))}
            profile_revisions = {
                item.id: item for item in session.scalars(select(KnowledgeIndexProfileRevision))
            }
            contracts = {
                item.id: item for item in session.scalars(select(StorageContractRevision))
            }
            contract_names = {
                item.id: item for item in session.scalars(select(StorageContract))
            }
            managed_rows = list(session.scalars(select(ManagedCollection)))

            project_rows = {item.id: item for item in session.scalars(select(Project))}
            deployment_rows = {item.id: item for item in session.scalars(select(Deployment))}
            project_deployments = {
                item.id: item for item in session.scalars(select(ProjectDeployment))
            }
            route_versions = {
                item.id: item for item in session.scalars(select(ProjectRouteVersion))
            }
            route_refs: dict[str, list[dict[str, Any]]] = {}
            for link in session.scalars(select(ProjectRouteVersionAsset)):
                version = route_versions.get(link.project_route_version_id)
                if not version:
                    continue
                project = project_rows.get(version.project_id)
                project_deployment = project_deployments.get(version.project_deployment_id)
                deployment = deployment_rows.get(project_deployment.deployment_id) if project_deployment else None
                matches = []
                for route in (version.snapshot_json or {}).get("routes", []):
                    for library_payload in route.get("libraries", []):
                        if str(library_payload.get("asset_version_id") or "") == link.knowledge_asset_version_id:
                            matches.append({
                                "task_code": route.get("task_code"),
                                "org_code": route.get("org_code"),
                            })
                if not matches:
                    matches = [{"task_code": None, "org_code": None}]
                for match in matches:
                    route_refs.setdefault(link.knowledge_asset_version_id, []).append({
                        "route_version_id": version.id,
                        "route_version_no": version.version_no,
                        "route_version_status": version.status,
                        "release_stage": version.release_stage,
                        "project_id": project.id if project else version.project_id,
                        "project_code": project.code if project else None,
                        "project_name": project.name if project else None,
                        "project_deployment_id": version.project_deployment_id,
                        "deployment_id": deployment.id if deployment else None,
                        "deployment_code": deployment.code if deployment else None,
                        "deployment_name": deployment.name if deployment else None,
                        **match,
                    })

            release_refs: dict[str, list[dict[str, Any]]] = {}
            for release in session.scalars(select(InstitutionReleaseSnapshot)):
                for asset_id in self._asset_ids_in_json(release.snapshot_json):
                    release_refs.setdefault(asset_id, []).append({
                        "release_id": release.id, "status": release.status,
                        "package_kind": release.package_kind,
                        "target_deployment_id": release.target_deployment_id,
                    })
            candidate_refs: dict[str, list[dict[str, Any]]] = {}
            for candidate in session.scalars(select(ImportedRouteCandidate)):
                asset_ids = self._asset_ids_in_json(candidate.snapshot_json)
                asset_ids.update(self._asset_ids_in_json(candidate.readiness_json))
                for asset_id in asset_ids:
                    candidate_refs.setdefault(asset_id, []).append({
                        "candidate_id": candidate.id, "status": candidate.status,
                        "migration_job_id": candidate.migration_job_id,
                        "project_deployment_id": candidate.project_deployment_id,
                    })
            migration_refs: dict[tuple[str, str], list[dict[str, Any]]] = {}
            migration_rows = session.execute(select(KnowledgeMigrationItem, KnowledgeMigrationJob).join(
                KnowledgeMigrationJob, KnowledgeMigrationJob.id == KnowledgeMigrationItem.migration_job_id,
            )).all()
            for item, job in migration_rows:
                migration_refs.setdefault((item.collection_name, item.partition_name), []).append({
                    "migration_job_id": job.id, "direction": job.direction,
                    "status": job.status, "stage": job.stage,
                })

            assets = []
            current_latest: dict[tuple[str, str], int] = {}
            asset_rows = list(session.scalars(select(KnowledgeAssetVersion).order_by(
                KnowledgeAssetVersion.knowledge_library_id,
                KnowledgeAssetVersion.version_no.desc(),
            )))
            for asset in asset_rows:
                profile = profiles.get(asset.index_profile_id)
                if (asset.status == "ready" and asset.review_gate_status == "approved"
                        and asset.review_snapshot_digest and profile
                        and profile.current_revision_id == asset.index_profile_revision_id):
                    key = (asset.knowledge_library_id, asset.index_profile_id)
                    current_latest[key] = max(current_latest.get(key, 0), int(asset.version_no))
            for asset in asset_rows:
                library = libraries.get(asset.knowledge_library_id)
                profile = profiles.get(asset.index_profile_id)
                revision = profile_revisions.get(asset.index_profile_revision_id)
                knowledge_type = None
                if library:
                    knowledge_type = f"graph:{library.graph_mode}" if library.knowledge_type == "graph" and library.graph_mode else library.knowledge_type
                routes = route_refs.get(asset.id, [])
                assets.append({
                    "asset_version_id": asset.id,
                    "knowledge_library_id": asset.knowledge_library_id,
                    "knowledge_library_name": library.name if library else None,
                    "knowledge_library_status": library.status if library else None,
                    "knowledge_type": knowledge_type,
                    "asset_version_no": asset.version_no,
                    "asset_status": asset.status,
                    "asset_error": asset.error,
                    "index_profile_id": asset.index_profile_id,
                    "index_profile_code": profile.code if profile else None,
                    "index_profile_revision_id": asset.index_profile_revision_id,
                    "current_profile_revision": bool(profile and profile.current_revision_id == asset.index_profile_revision_id),
                    "latest_current_ready": bool(
                        profile and profile.current_revision_id == asset.index_profile_revision_id
                        and asset.status == "ready" and asset.review_gate_status == "approved"
                        and bool(asset.review_snapshot_digest)
                        and current_latest.get((asset.knowledge_library_id, asset.index_profile_id)) == asset.version_no
                    ),
                    "collection_policy": revision.collection_policy if revision else None,
                    "storage_contract_revision_id": asset.storage_contract_revision_id,
                    "embedding_serving_id": asset.embedding_serving_id,
                    "embedding_model": asset.embedding_model,
                    "embedding_dimension": asset.embedding_dimension,
                    "collection_name": asset.collection_name,
                    "partition_name": asset.partition_name,
                    "expected_count": int(asset.item_count),
                    "expected_digest": asset.content_digest,
                    "verification": {
                        "status": asset.last_verification_status,
                        "verified_at": asset.last_verified_at.isoformat() if asset.last_verified_at else None,
                        "observed_count": asset.last_observed_count,
                        "observed_digest": asset.last_observed_digest,
                        "error": asset.last_verification_error,
                    },
                    "routing_refs": routes,
                    "routing_ref_count": len(routes),
                    "release_refs": release_refs.get(asset.id, []),
                    "candidate_refs": candidate_refs.get(asset.id, []),
                    "migration_refs": migration_refs.get((asset.collection_name, asset.partition_name), []),
                    "gc_eligible": asset.id in eligible_ids,
                })

            managed = {}
            for item in managed_rows:
                contract = contracts.get(item.storage_contract_revision_id)
                contract_name = contract_names.get(contract.storage_contract_id) if contract else None
                managed[item.collection_name] = {
                    "managed_collection_id": item.id,
                    "collection_name": item.collection_name,
                    "registration_status": item.status,
                    "registration_error": item.error_summary,
                    "desired_spec_hash": item.desired_spec_hash,
                    "observed_spec_hash": item.observed_spec_hash,
                    "expected_description": (
                        f"dataforge-managed:{item.id}:{item.provisioning_token}:{item.desired_spec_hash}"
                    ),
                    "storage_contract": None if not contract else {
                        "id": contract.id,
                        "code": contract_name.code if contract_name else None,
                        "name": contract_name.name if contract_name else None,
                        "revision": contract.revision_no,
                        "schema": contract.schema_json,
                        "index": contract.index_json,
                        "dimension": contract.dimension,
                        "metric_type": contract.metric_type,
                        "storage_spec_hash": contract.storage_spec_hash,
                    },
                }
            return {
                "managed_collections": managed,
                "assets": assets,
                "gc": gc_plan,
            }

    def vector_partition_metadata(self, collection_name: str, partition_name: str) -> dict[str, Any] | None:
        metadata = self.vector_inventory_metadata()
        return next((item for item in metadata["assets"] if
                     item["collection_name"] == collection_name and item["partition_name"] == partition_name), None)

    def asset_version_references(self, asset_version_id: str) -> dict[str, Any]:
        metadata = self.vector_inventory_metadata()
        asset = next((item for item in metadata["assets"] if item["asset_version_id"] == asset_version_id), None)
        if not asset:
            raise ValueError("AssetVersion 不存在")
        return {
            "routing": asset["routing_refs"], "releases": asset["release_refs"],
            "candidates": asset["candidate_refs"], "migrations": asset["migration_refs"],
            "gc_eligible": asset["gc_eligible"],
        }

    def record_vector_partition_verification(self, asset_version_id: str, *, status: str,
                                             observed_count: int | None = None,
                                             observed_digest: str | None = None,
                                             error: str | None = None) -> dict[str, Any]:
        if status not in {"consistent", "inconsistent", "error"}:
            raise ValueError("Partition 校验状态无效")
        with self.sessions.begin() as session:
            asset = session.get(KnowledgeAssetVersion, asset_version_id, with_for_update=True)
            if not asset:
                raise ValueError("AssetVersion 不存在")
            asset.last_verification_status = status
            asset.last_verified_at = utc_now()
            asset.last_observed_count = observed_count
            asset.last_observed_digest = observed_digest
            asset.last_verification_error = error
            self.audit(session, "vector_partition.verified", "knowledge_asset_version", asset.id, {
                "status": status, "observed_count": observed_count,
                "observed_digest": observed_digest, "error": error,
            })
            return {
                "asset_version_id": asset.id, "status": status,
                "verified_at": asset.last_verified_at.isoformat(),
                "observed_count": observed_count, "observed_digest": observed_digest,
                "error": error,
            }

    def knowledge_asset_gc_plan(self, *, retention_days: int = 30, minimum_versions: int = 2) -> dict[str, Any]:
        if retention_days < 1 or minimum_versions < 1:
            raise ValueError("AssetVersion GC 保留参数无效")
        cutoff = utc_now() - timedelta(days=retention_days)
        with self.sessions() as session:
            referenced = set(session.scalars(select(ProjectRouteVersionAsset.knowledge_asset_version_id)))
            for candidate in session.scalars(select(ImportedRouteCandidate).where(
                ImportedRouteCandidate.status.in_(("waiting_assets", "ready", "activating", "activated")),
            )):
                referenced.update(self._asset_ids_in_json(candidate.snapshot_json))
                referenced.update(self._asset_ids_in_json(candidate.readiness_json))
            for release in session.scalars(select(InstitutionReleaseSnapshot).where(
                InstitutionReleaseSnapshot.status.in_(("frozen", "building", "ready")),
            )):
                referenced.update(self._asset_ids_in_json(release.snapshot_json))
            migration_partitions = {(collection, partition) for collection, partition in session.execute(
                select(KnowledgeMigrationItem.collection_name, KnowledgeMigrationItem.partition_name)
                .join(KnowledgeMigrationJob, KnowledgeMigrationJob.id == KnowledgeMigrationItem.migration_job_id)
                .where(KnowledgeMigrationJob.status.in_((
                    "planning", "queued", "running", "ready", "inspected", "conflict", "waiting",
                )))
            ).all()}
            if migration_partitions:
                referenced.update(session.scalars(select(KnowledgeAssetVersion.id).where(
                    tuple_(KnowledgeAssetVersion.collection_name, KnowledgeAssetVersion.partition_name)
                    .in_(migration_partitions)
                )))

            eligible: list[dict[str, Any]] = []
            protected: list[dict[str, Any]] = []
            library_ids = list(session.scalars(select(KnowledgeAssetVersion.knowledge_library_id).distinct()))
            for library_id in library_ids:
                values = list(session.scalars(select(KnowledgeAssetVersion).where(
                    KnowledgeAssetVersion.knowledge_library_id == library_id,
                    KnowledgeAssetVersion.status == "ready",
                ).order_by(KnowledgeAssetVersion.version_no.desc())))
                newest = {item.id for item in values[:minimum_versions]}
                for item in values:
                    reason = None
                    unreferenced_at = item.unreferenced_at
                    if unreferenced_at and unreferenced_at.tzinfo is None:
                        unreferenced_at = unreferenced_at.replace(tzinfo=utc_now().tzinfo)
                    if item.id in referenced:
                        reason = "referenced"
                    elif item.id in newest:
                        reason = "minimum_versions"
                    elif not unreferenced_at or unreferenced_at > cutoff:
                        reason = "retention_window"
                    payload = {
                        "asset_version_id": item.id, "knowledge_library_id": item.knowledge_library_id,
                        "version_no": item.version_no, "collection_name": item.collection_name,
                        "partition_name": item.partition_name,
                        "authoring_target_revision_id": item.authoring_target_revision_id,
                        "authoring_connection_fingerprint": item.authoring_connection_fingerprint,
                    }
                    if reason:
                        protected.append({**payload, "reason": reason})
                    else:
                        eligible.append(payload)
            confirmation = hashlib.sha256(json.dumps(eligible, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            return {"retention_days": retention_days, "minimum_versions": minimum_versions,
                    "eligible": eligible, "protected": protected, "confirmation": confirmation}

    def create_knowledge_asset_gc_job(self, *, execute: bool = False, confirmation: str | None = None) -> dict[str, Any]:
        plan = self.knowledge_asset_gc_plan()
        if execute and confirmation != plan["confirmation"]:
            raise ValueError("AssetVersion GC 确认值与当前清单不一致")
        with self.sessions.begin() as session:
            job = KnowledgeAssetGcJob(
                id=new_id("kagc"), status="queued" if execute else "planned",
                execute_requested=execute, plan_json=plan,
            )
            session.add(job)
            self.audit(session, "knowledge_asset_gc.planned", "knowledge_asset_gc_job", job.id, {
                "execute": execute, "eligible": len(plan["eligible"]),
            })
            return {"id": job.id, "status": job.status, "execute_requested": execute, "plan": plan}

    def claim_knowledge_asset_gc_job(self) -> KnowledgeAssetGcJob | None:
        with self.sessions.begin() as session:
            job = session.scalar(select(KnowledgeAssetGcJob).where(
                KnowledgeAssetGcJob.status == "queued", KnowledgeAssetGcJob.execute_requested.is_(True),
            ).order_by(KnowledgeAssetGcJob.created_at).with_for_update(skip_locked=True))
            if not job:
                return None
            current = self.knowledge_asset_gc_plan()
            if current["confirmation"] != (job.plan_json or {}).get("confirmation"):
                job.status, job.error = "failed", "GC 清单在执行前发生变化"
                return None
            job.status = "running"
            for item in (job.plan_json or {}).get("eligible", []):
                asset = session.get(KnowledgeAssetVersion, item["asset_version_id"], with_for_update=True)
                if not asset or asset.status != "ready":
                    job.status, job.error = "failed", "GC 候选 AssetVersion 状态在执行前发生变化"
                    return None
                asset.status, asset.error = "delete_pending", None
            return job

    def knowledge_asset_gc_context(self, job_id: str) -> dict[str, Any]:
        with self.sessions() as session:
            job = session.get(KnowledgeAssetGcJob, job_id)
            if not job or job.status != "running" or not job.execute_requested:
                raise ValueError("AssetVersion GC 任务当前不可执行")
            return {"id": job.id, "plan": dict(job.plan_json or {})}

    def finish_knowledge_asset_gc_job(self, job_id: str, deleted_ids: list[str], error: str | None = None) -> dict[str, Any]:
        with self.sessions.begin() as session:
            job = session.get(KnowledgeAssetGcJob, job_id, with_for_update=True)
            if not job:
                raise ValueError("AssetVersion GC 任务不存在")
            if error:
                job.status, job.error = "failed", error
                deleted = set(deleted_ids)
                for item in (job.plan_json or {}).get("eligible", []):
                    asset = session.get(KnowledgeAssetVersion, item["asset_version_id"])
                    if not asset:
                        continue
                    if asset.id in deleted:
                        asset.status = "deleted"
                        session.execute(delete(KnowledgeAssetItem).where(KnowledgeAssetItem.asset_version_id == asset.id))
                    elif asset.status == "delete_pending":
                        asset.status, asset.error = "ready", error
                return {"id": job.id, "status": job.status, "error": error}
            for asset_id in deleted_ids:
                asset = session.get(KnowledgeAssetVersion, asset_id)
                if asset:
                    asset.status = "deleted"
                    session.execute(delete(KnowledgeAssetItem).where(KnowledgeAssetItem.asset_version_id == asset.id))
            job.status, job.error = "completed", None
            self.audit(session, "knowledge_asset_gc.completed", "knowledge_asset_gc_job", job.id,
                       {"deleted_asset_version_ids": deleted_ids})
            return {"id": job.id, "status": job.status, "deleted_asset_version_ids": deleted_ids}

    def claim_vector_sync_job(self, owner: str) -> VectorSyncJob | None:
        now = utc_now()
        with self.sessions.begin() as session:
            candidate_ids = list(session.scalars(select(VectorSyncJob.id).where(
                (VectorSyncJob.status == "queued") |
                ((VectorSyncJob.status == "running") & (VectorSyncJob.lease_expires_at < now))
            ).order_by(VectorSyncJob.created_at)))
            for job_id in candidate_ids:
                job = session.scalar(select(VectorSyncJob).where(
                    VectorSyncJob.id == job_id,
                    (VectorSyncJob.status == "queued") |
                    ((VectorSyncJob.status == "running") & (VectorSyncJob.lease_expires_at < now)),
                ).with_for_update(skip_locked=True))
                if not job:
                    continue
                if not self._acquire_library_work_leases(
                    session, [job.knowledge_library_id], work_kind="vector_sync",
                    work_id=job.id, owner=owner, now=now,
                ):
                    continue
                job.status, job.lease_owner = "running", owner
                job.lease_expires_at = now + WORK_LEASE_DURATION
                job.attempt_count += 1
                self.audit(session, "vector_sync.claimed", "vector_sync_job", job.id, {
                    "owner": owner, "attempt": job.attempt_count,
                    "knowledge_library_id": job.knowledge_library_id,
                })
                return job
            return None

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
                managed = session.scalar(select(ManagedCollection).where(ManagedCollection.collection_name == item.collection_name))
                serving = session.scalar(select(EmbeddingServing).where(
                    EmbeddingServing.serving_code == item.embedding_serving_id,
                )) if item.embedding_serving_id else None
                embedding = session.get(EmbeddingProfile, item.embedding_profile_id)
                values.append({"id": item.id, "code": item.code, "knowledge_type": item.knowledge_type,
                    "collection_name": item.collection_name, "embedding_profile_id": item.embedding_profile_id,
                    "embedding_serving_id": item.embedding_serving_id, "embedding_input": item.embedding_input,
                    "embedding_serving": None if not serving else {
                        "id": serving.id, "serving_code": serving.serving_code, "name": serving.name,
                        "model_name": serving.model_name, "dimension": serving.dimension,
                        "is_enabled": serving.is_enabled, "last_check_status": serving.last_check_status,
                    },
                    "dimension": embedding.dimension if embedding else None,
                    "fields": item.fields_json, "origin": item.origin,
                    "owner_knowledge_type_id": item.owner_knowledge_type_id,
                    "status": item.status, "current_revision_id": item.current_revision_id,
                    "managed_collection_id": managed.id if managed else None,
                    "revisions": [{"id": revision.id, "revision": revision.revision_no, "status": revision.status,
                                   "collection_name": revision.collection_name, "fields": revision.fields_json,
                                   "embedding_profile_id": revision.embedding_profile_id,
                                   "embedding_serving_id": revision.embedding_serving_id,
                                   "embedding_input": revision.embedding_input,
                                   "storage_contract_revision_id": revision.storage_contract_revision_id,
                                   "collection_policy": revision.collection_policy} for revision in revisions]})
            return values

    def list_managed_collections(self) -> list[dict[str, Any]]:
        with self.sessions() as session:
            rows = session.execute(select(ManagedCollection, StorageContractRevision, StorageContract).join(
                StorageContractRevision, StorageContractRevision.id == ManagedCollection.storage_contract_revision_id,
            ).join(StorageContract, StorageContract.id == StorageContractRevision.storage_contract_id).order_by(ManagedCollection.collection_name)).all()
            values = []
            for item, revision, contract in rows:
                references = self._managed_collection_reference_snapshot(session, item)
                values.append({
                    "id": item.id, "collection_name": item.collection_name, "status": item.status,
                    "error": item.error_summary, "ownership": "dataforge",
                    "ownership_verified": item.observed_spec_hash == item.desired_spec_hash and item.status in {"ready", "deleting"},
                    "desired_spec_hash": item.desired_spec_hash, "observed_spec_hash": item.observed_spec_hash,
                    "partition_names": [library["partition_name"] for library in references["knowledge_libraries"]],
                    "storage_contract": {"code": contract.code, "name": contract.name,
                                         "revision": revision.revision_no, "dimension": revision.dimension,
                                         "metric_type": revision.metric_type},
                    "references": references,
                })
            return values

    @staticmethod
    def _managed_collection_reference_snapshot(session: Session, item: ManagedCollection) -> dict[str, Any]:
        revision_ids = list(session.scalars(select(KnowledgeIndexProfileRevision.id).where(
            KnowledgeIndexProfileRevision.collection_name == item.collection_name,
        )))
        profile_ids = list(session.scalars(select(KnowledgeIndexProfile.id).where(
            KnowledgeIndexProfile.current_revision_id.in_(revision_ids),
            KnowledgeIndexProfile.status != "archived",
        ))) if revision_ids else []
        draft_profiles = list(session.scalars(select(KnowledgeIndexProfile.id).where(
            KnowledgeIndexProfile.current_revision_id.is_(None),
            KnowledgeIndexProfile.status != "archived",
        )))
        if revision_ids and draft_profiles:
            draft_bound = list(session.scalars(select(KnowledgeIndexProfileRevision.knowledge_index_profile_id).where(
                KnowledgeIndexProfileRevision.id.in_(revision_ids),
                KnowledgeIndexProfileRevision.knowledge_index_profile_id.in_(draft_profiles),
            )))
            profile_ids = list(dict.fromkeys([*profile_ids, *draft_bound]))
        profiles = list(session.scalars(select(KnowledgeIndexProfile).where(
            KnowledgeIndexProfile.id.in_(profile_ids),
        ))) if profile_ids else []
        binding_rows = list(session.scalars(select(KnowledgeTypeIndexBinding).where(
            KnowledgeTypeIndexBinding.index_profile_revision_id.in_(revision_ids),
        ))) if revision_ids else []
        binding_revision_ids = list({binding.knowledge_type_revision_id for binding in binding_rows})
        current_types = list(session.scalars(select(KnowledgeType).where(
            KnowledgeType.current_revision_id.in_(binding_revision_ids), KnowledgeType.status == "active",
        ))) if binding_revision_ids else []
        libraries = list(session.scalars(select(KnowledgeLibrary).where(
            KnowledgeLibrary.knowledge_type_revision_id.in_(binding_revision_ids),
            KnowledgeLibrary.status.in_(("active", "deleting")),
        ))) if binding_revision_ids else []
        library_ids = [library.id for library in libraries]
        template_rows = session.execute(select(DocumentLibraryTemplateOutput, DocumentLibraryTemplateBinding).join(
            DocumentLibraryTemplateBinding,
            DocumentLibraryTemplateBinding.id == DocumentLibraryTemplateOutput.document_library_template_binding_id,
        ).where(
            DocumentLibraryTemplateOutput.knowledge_library_id.in_(library_ids),
            DocumentLibraryTemplateBinding.status == "active",
        )).all() if library_ids else []
        route_rows = session.execute(select(ProjectOrgRouteLibrary, ProjectOrgRoute).join(
            ProjectOrgRoute, ProjectOrgRoute.id == ProjectOrgRouteLibrary.project_org_route_id,
        ).where(
            ProjectOrgRouteLibrary.knowledge_library_id.in_(library_ids),
            ProjectOrgRoute.status == "published",
        )).all() if library_ids else []
        sync_jobs = list(session.scalars(select(VectorSyncJob).where(
            VectorSyncJob.index_profile_id.in_(profile_ids), VectorSyncJob.status.in_(("queued", "running")),
        ))) if profile_ids else []
        vector_deletion_jobs = list(session.scalars(select(VectorDeletionJob).where(
            VectorDeletionJob.index_profile_id.in_(profile_ids), VectorDeletionJob.status.in_(("queued", "running")),
        ))) if profile_ids else []
        return {
            "profile_ids": profile_ids,
            "profiles": [{"id": value.id, "code": value.code, "origin": value.origin,
                          "status": value.status} for value in profiles],
            "type_ids": [value.id for value in current_types],
            "type_revisions": [{"type_id": value.id, "code": value.code, "name": value.name,
                                "revision_id": value.current_revision_id} for value in current_types],
            "knowledge_libraries": [{"id": value.id, "name": value.name, "partition_name": value.partition_name} for value in libraries],
            "template_bindings": [{"binding_id": binding.id, "template_id": binding.knowledge_flow_template_id,
                                   "knowledge_library_id": output.knowledge_library_id}
                                  for output, binding in template_rows],
            "template_binding_count": len(template_rows),
            "routes": [{"route_id": route.id, "project_deployment_task_id": route.project_deployment_task_id,
                        "org_code": route.org_code, "knowledge_library_id": link.knowledge_library_id}
                       for link, route in route_rows],
            "route_count": len(route_rows),
            "vector_jobs": [{"id": job.id, "kind": "sync", "status": job.status,
                             "index_profile_id": job.index_profile_id} for job in sync_jobs] +
                           [{"id": job.id, "kind": "delete", "status": job.status,
                             "index_profile_id": job.index_profile_id} for job in vector_deletion_jobs],
            "running_vector_job_count": len(sync_jobs) + len(vector_deletion_jobs),
        }

    def managed_collection_delete_check(self, collection_id: str, observed: dict[str, Any] | None = None) -> dict[str, Any]:
        with self.sessions() as session:
            item = session.get(ManagedCollection, collection_id)
            if not item:
                raise ValueError("受管 Collection 不存在")
            references = self._managed_collection_reference_snapshot(session, item)
            blockers: list[dict[str, Any]] = []
            for key, message in (
                ("profile_ids", "仍有当前或草稿 Profile 引用"),
                ("type_ids", "仍有当前 Knowledge Type Revision 引用"),
                ("knowledge_libraries", "仍有活动知识库及其 kl_ Partition 引用"),
            ):
                if references[key]:
                    blockers.append({"code": key, "message": message, "count": len(references[key])})
            for key, message in (
                ("template_binding_count", "仍有文档模板结果绑定"),
                ("route_count", "仍有已发布路由引用"),
                ("running_vector_job_count", "仍有运行中的向量任务"),
            ):
                if references[key]:
                    blockers.append({"code": key, "message": message, "count": references[key]})
            observed = dict(observed or {})
            if observed:
                if observed.get("error"):
                    blockers.append({"code": "milvus_unavailable", "message": str(observed["error"])})
                elif observed.get("exists"):
                    if not observed.get("ownership_valid"):
                        blockers.append({"code": "ownership_mismatch", "message": "Collection ownership marker 或规格哈希不匹配"})
                    external_partitions = [name for name in observed.get("partitions", []) if name not in {"_default"} and not str(name).startswith("kl_")]
                    if external_partitions:
                        blockers.append({"code": "external_partitions", "message": "存在非 DataForge Partition", "partitions": external_partitions})
            elif item.status != "deleted":
                blockers.append({"code": "milvus_unverified", "message": "尚未连接 Milvus 验证 Collection 所有权"})
            return {
                "id": item.id, "collection_name": item.collection_name, "status": item.status,
                "desired_spec_hash": item.desired_spec_hash, "references": references,
                "observed": observed, "blockers": blockers, "deletable": not blockers,
                "warning": "删除受管 Collection 将永久删除其中全部向量数据，此操作不可恢复。",
            }

    def create_managed_collection_deletion(self, collection_id: str, preflight: dict[str, Any]) -> dict[str, Any]:
        if preflight.get("id") != collection_id or preflight.get("blockers") or not preflight.get("deletable"):
            raise ValueError("受管 Collection 删除预检未通过")
        with self.sessions.begin() as session:
            item = session.get(ManagedCollection, collection_id)
            if not item or item.status not in {"ready", "delete_failed"}:
                raise ValueError("仅 ready 或删除失败的受管 Collection 可申请删除")
            existing = session.scalar(select(ManagedCollectionDeletionJob).where(
                ManagedCollectionDeletionJob.managed_collection_id == collection_id,
                ManagedCollectionDeletionJob.status.in_(("queued", "running")),
            ))
            if existing:
                return {"id": existing.id, "managed_collection_id": collection_id, "status": existing.status}
            job = ManagedCollectionDeletionJob(
                id=new_id("mcdj"), managed_collection_id=collection_id,
                preflight_json=preflight,
            )
            session.add(job); item.status = "deleting"
            self.audit(session, "managed_collection.deletion_requested", "managed_collection", collection_id, {"job_id": job.id})
            return {"id": job.id, "managed_collection_id": collection_id, "status": job.status}

    def list_managed_collection_deletion_jobs(self, collection_id: str | None = None) -> list[dict[str, Any]]:
        with self.sessions() as session:
            query = select(ManagedCollectionDeletionJob).order_by(ManagedCollectionDeletionJob.created_at.desc())
            if collection_id:
                query = query.where(ManagedCollectionDeletionJob.managed_collection_id == collection_id)
            return [{"id": job.id, "managed_collection_id": job.managed_collection_id,
                     "status": job.status, "attempt_count": job.attempt_count,
                     "error": job.error, "created_at": job.created_at.isoformat()} for job in session.scalars(query)]

    def retry_managed_collection_deletion(self, job_id: str) -> dict[str, Any]:
        with self.sessions.begin() as session:
            job = session.get(ManagedCollectionDeletionJob, job_id)
            if not job or job.status != "failed":
                raise ValueError("仅可重试失败的受管 Collection 删除任务")
            item = session.get(ManagedCollection, job.managed_collection_id)
            if not item or item.status == "deleted":
                raise ValueError("受管 Collection 已删除或登记不存在")
            job.status, job.error, job.lease_owner, job.lease_expires_at = "queued", None, None, None
            item.status, item.error_summary = "deleting", None
            return {"id": job.id, "status": job.status}

    def claim_managed_collection_deletion_job(self, owner: str) -> ManagedCollectionDeletionJob | None:
        with self.sessions.begin() as session:
            job = session.scalar(select(ManagedCollectionDeletionJob).where(
                (ManagedCollectionDeletionJob.status == "queued") |
                ((ManagedCollectionDeletionJob.status == "running") & (ManagedCollectionDeletionJob.lease_expires_at < utc_now())),
            ).order_by(ManagedCollectionDeletionJob.created_at).with_for_update(skip_locked=True).limit(1))
            if not job:
                return None
            job.status, job.attempt_count = "running", job.attempt_count + 1
            job.lease_owner, job.lease_expires_at = owner, utc_now() + timedelta(minutes=5)
            return job

    def managed_collection_deletion_context(self, job_id: str) -> dict[str, Any]:
        with self.sessions() as session:
            job = session.get(ManagedCollectionDeletionJob, job_id)
            if not job:
                raise ValueError("受管 Collection 删除任务不存在")
            item = session.get(ManagedCollection, job.managed_collection_id)
            revision = session.get(StorageContractRevision, item.storage_contract_revision_id) if item else None
            if not item or not revision:
                raise ValueError("受管 Collection 或 Storage Contract 不存在")
            return {"job": job, "collection": item, "storage_contract_revision": revision}

    def finish_managed_collection_deletion(self, job_id: str, error: str | None = None) -> dict[str, Any]:
        with self.sessions.begin() as session:
            job = session.get(ManagedCollectionDeletionJob, job_id)
            if not job:
                raise ValueError("受管 Collection 删除任务不存在")
            item = session.get(ManagedCollection, job.managed_collection_id)
            if not item:
                raise ValueError("受管 Collection 不存在")
            if error:
                job.status, job.error, job.lease_owner, job.lease_expires_at = "failed", error, None, None
                item.status, item.error_summary = "delete_failed", error
                return {"id": job.id, "status": job.status, "error": error}
            job.status, job.error, job.lease_owner, job.lease_expires_at = "completed", None, None, None
            item.status, item.error_summary, item.observed_spec_hash = "deleted", None, None
            self.audit(session, "managed_collection.deleted", "managed_collection", item.id, {"job_id": job.id})
            return {"id": job.id, "status": job.status, "managed_collection_id": item.id}

    def archive_index_profile(self, profile_id: str) -> dict[str, Any]:
        with self.sessions.begin() as session:
            profile = session.get(KnowledgeIndexProfile, profile_id)
            if not profile:
                raise ValueError("Index Profile 不存在")
            if profile.origin == "builtin":
                raise ValueError("内置 Index Profile 不可归档")
            current_bindings = session.scalar(select(func.count()).select_from(KnowledgeTypeIndexBinding).join(
                KnowledgeType, KnowledgeType.current_revision_id == KnowledgeTypeIndexBinding.knowledge_type_revision_id,
            ).where(KnowledgeTypeIndexBinding.index_profile_id == profile_id, KnowledgeType.status == "active")) or 0
            binding_revision_ids = list(session.scalars(select(KnowledgeTypeIndexBinding.knowledge_type_revision_id).where(
                KnowledgeTypeIndexBinding.index_profile_id == profile_id,
            )))
            library_count = session.scalar(select(func.count()).select_from(KnowledgeLibrary).where(
                KnowledgeLibrary.knowledge_type_revision_id.in_(binding_revision_ids),
                KnowledgeLibrary.status.in_(("active", "deleting")),
            )) if binding_revision_ids else 0
            if current_bindings or library_count:
                raise ValueError("Profile 仍被当前 Knowledge Type 或知识库引用，不能归档")
            profile.status = "archived"
            self.audit(session, "index_profile.archived", "index_profile", profile.id)
            return {"id": profile.id, "status": profile.status}

    def create_project(self, name: str, legacy_name: str | None = None) -> dict[str, Any]:
        code, name = (name, legacy_name) if legacy_name is not None else (generated_business_code("PRJ"), name)
        if not str(name or "").strip():
            raise ValueError("项目名称不能为空")
        with self.sessions.begin() as session:
            if session.scalar(select(Project).where(Project.code == code)):
                raise ValueError("项目编码已存在")
            project = Project(id=new_id("project"), code=code.strip(), name=name.strip())
            session.add(project); session.flush()
            self._ensure_project_binding(session, project, self._ensure_central_deployment(session))
            self.audit(session, "project.created", "project", project.id)
            return {"id": project.id, "code": project.code, "name": project.name, "status": project.status}

    def create_project_task(self, project_id: str, code: str, name: str,
                            knowledge_type: str | None = None, description: str = "") -> dict[str, Any]:
        with self.sessions.begin() as session:
            if not session.get(Project, project_id):
                raise ValueError("项目不存在")
            if knowledge_type and not session.scalar(select(KnowledgeType).where(
                    KnowledgeType.code == knowledge_type, KnowledgeType.status == "active")):
                raise ValueError("Knowledge Type 不存在或未启用")
            task = ProjectTask(id=new_id("task"), project_id=project_id, code=code.strip(), name=name.strip(),
                               knowledge_type=knowledge_type, description=description.strip())
            session.add(task); self.audit(session, "project_task.created", "project_task", task.id)
            return self._task_payload(task)

    @staticmethod
    def _task_payload(task: ProjectTask) -> dict[str, Any]:
        return {"id": task.id, "project_id": task.project_id, "code": task.code, "name": task.name,
                "knowledge_type": task.knowledge_type, "description": task.description, "status": task.status}

    @staticmethod
    def _target_revision_payload(revision: MilvusTargetRevision | None) -> dict[str, Any] | None:
        if not revision:
            return None
        return {
            "id": revision.id,
            "milvus_target_id": revision.milvus_target_id,
            "revision_no": revision.revision_no,
            "milvus_url": revision.milvus_url,
            "connection_fingerprint": revision.connection_fingerprint,
            "verification_status": revision.verification_status,
            "verified_at": revision.verified_at.isoformat() if revision.verified_at else None,
            "verification_error": revision.verification_error,
            "token_configured": bool(revision.token_ciphertext),
            "health_status": revision.health_status or "unknown",
            "health_checked_at": revision.health_checked_at.isoformat()
                if revision.health_checked_at else None,
            "health_latency_ms": revision.health_latency_ms,
            "health_error": revision.health_error,
        }

    @staticmethod
    def _target_payload(target: MilvusTarget, current: MilvusTargetRevision | None = None,
                        candidate: MilvusTargetRevision | None = None) -> dict[str, Any]:
        return {
            "id": target.id,
            "name": target.name,
            "current_revision_id": target.current_revision_id,
            "candidate_revision_id": target.candidate_revision_id,
            "current_revision": V7Store._target_revision_payload(current),
            "candidate_revision": V7Store._target_revision_payload(candidate),
            "created_at": target.created_at.isoformat(),
            "updated_at": target.updated_at.isoformat(),
        }

    @staticmethod
    def _target_revisions(session: Session, target: MilvusTarget) -> tuple[MilvusTargetRevision | None, MilvusTargetRevision | None]:
        current = session.get(MilvusTargetRevision, target.current_revision_id) if target.current_revision_id else None
        candidate = session.get(MilvusTargetRevision, target.candidate_revision_id) if target.candidate_revision_id else None
        return current, candidate

    @staticmethod
    def _shared_deployment_payload(
            deployment: Deployment,
            targets: dict[str, tuple[MilvusTarget, MilvusTargetRevision]] | None = None) -> dict[str, Any]:
        result = {
            "id": deployment.id, "code": deployment.code, "name": deployment.name,
            "scope": deployment.scope, "institution_name": deployment.institution_name,
            "institution_code": deployment.institution_code,
            "institution_code_locked": bool(deployment.institution_code_locked_at),
            "institution_code_locked_at": deployment.institution_code_locked_at.isoformat()
                if deployment.institution_code_locked_at else None,
            "status": deployment.status,
            "created_at": deployment.created_at.isoformat(), "updated_at": deployment.updated_at.isoformat(),
        }
        result["stage_targets"] = {
            stage: {
                "milvus_target_id": target.id,
                "milvus_target_revision_id": revision.id,
                "id": target.id,
                "name": target.name,
                "revision": V7Store._target_revision_payload(revision),
            }
            for stage, (target, revision) in sorted((targets or {}).items())
        }
        return result

    @staticmethod
    def _deployment_payload(binding: ProjectDeployment, deployment: Deployment,
                            targets: dict[str, tuple[MilvusTarget, MilvusTargetRevision]] | None = None) -> dict[str, Any]:
        shared = V7Store._shared_deployment_payload(deployment, targets)
        return {
            **shared,
            "id": binding.id,
            "project_deployment_id": binding.id,
            "project_id": binding.project_id,
            "deployment_id": deployment.id,
            "binding_status": binding.status,
            "deployment": shared,
        }

    @staticmethod
    def _deployment_targets(
            session: Session, deployment_id: str,
    ) -> dict[str, tuple[MilvusTarget, MilvusTargetRevision]]:
        rows = session.execute(select(DeploymentTarget, MilvusTarget, MilvusTargetRevision).join(
            MilvusTarget, MilvusTarget.id == DeploymentTarget.milvus_target_id,
        ).join(
            MilvusTargetRevision,
            MilvusTargetRevision.id == DeploymentTarget.milvus_target_revision_id,
        ).where(
            DeploymentTarget.deployment_id == deployment_id,
            DeploymentTarget.target_kind == "milvus",
        )).all()
        return {link.release_stage: (target, revision) for link, target, revision in rows}

    def list_milvus_targets(self) -> list[dict[str, Any]]:
        with self.sessions() as session:
            result = []
            for item in session.scalars(select(MilvusTarget).order_by(MilvusTarget.name)):
                result.append(self._target_payload(item, *self._target_revisions(session, item)))
            return result

    def get_milvus_target(self, target_id: str) -> dict[str, Any]:
        with self.sessions() as session:
            value = session.get(MilvusTarget, target_id)
            if not value:
                raise ValueError("Milvus Target 不存在")
            return self._target_payload(value, *self._target_revisions(session, value))

    def milvus_target_revision(self, revision_id: str) -> dict[str, Any]:
        with self.sessions() as session:
            revision = session.get(MilvusTargetRevision, revision_id)
            if not revision:
                raise ValueError("Milvus Target Revision 不存在")
            return {
                "id": revision.id, "milvus_target_id": revision.milvus_target_id,
                "revision_no": revision.revision_no, "milvus_url": revision.milvus_url,
                "token_ciphertext": revision.token_ciphertext,
                "token_key_version": revision.token_key_version,
                "connection_fingerprint": revision.connection_fingerprint,
                "verification_status": revision.verification_status,
                "health_status": revision.health_status,
                "health_checked_at": revision.health_checked_at.isoformat()
                    if revision.health_checked_at else None,
                "health_latency_ms": revision.health_latency_ms,
                "health_error": revision.health_error,
            }

    def candidate_milvus_target_revision(self, target_id: str) -> dict[str, Any]:
        with self.sessions() as session:
            target = session.get(MilvusTarget, target_id)
            if not target or not target.candidate_revision_id:
                raise ValueError("Milvus Target 候选配置不存在")
            return self.milvus_target_revision(target.candidate_revision_id)

    def bind_authoring_milvus_target(self, instance_id: str, target_id: str) -> dict[str, Any]:
        with self.sessions.begin() as session:
            instance = session.get(DataForgeInstance, instance_id)
            target = session.get(MilvusTarget, target_id)
            if not instance or not target:
                raise ValueError("DataForge 实例或 Milvus Target 不存在")
            current = session.get(MilvusTargetRevision, target.current_revision_id) if target.current_revision_id else None
            if not current or current.verification_status != "verified":
                raise ValueError("只有连接验证通过的 Milvus Target 才能设为默认知识写入目标")
            instance.authoring_milvus_target_id = target.id
            self.audit(session, "instance.authoring_milvus_target_updated", "dataforge_instance", instance.id, {
                "milvus_target_id": target.id, "revision_id": current.id,
            })
            return self._target_payload(target, current, None)

    def bind_authoring_milvus_target_if_unset(self, instance_id: str, target_id: str) -> dict[str, Any] | None:
        """Bind the verified default once without replacing an administrator choice."""
        with self.sessions.begin() as session:
            instance = session.get(DataForgeInstance, instance_id, with_for_update=True)
            if not instance:
                raise ValueError("DataForge 实例不存在")
            if instance.authoring_milvus_target_id:
                return None
            target = session.get(MilvusTarget, target_id)
            current = session.get(MilvusTargetRevision, target.current_revision_id) \
                if target and target.current_revision_id else None
            if not target or not current or current.verification_status != "verified":
                return None
            instance.authoring_milvus_target_id = target.id
            self.audit(session, "instance.authoring_milvus_target_defaulted", "dataforge_instance", instance.id, {
                "milvus_target_id": target.id, "revision_id": current.id,
            })
            return self._target_payload(target, current, None)

    def authoring_milvus_target(self, instance_id: str) -> dict[str, Any]:
        with self.sessions() as session:
            instance = session.get(DataForgeInstance, instance_id)
            if not instance or not instance.authoring_milvus_target_id:
                raise ValueError("当前实例尚未配置默认知识写入目标")
            target = session.get(MilvusTarget, instance.authoring_milvus_target_id)
            current = session.get(MilvusTargetRevision, target.current_revision_id) if target and target.current_revision_id else None
            if not target or not current or current.verification_status != "verified":
                raise ValueError("默认知识写入目标未通过连接验证")
            return self._target_payload(target, current, None)

    def create_milvus_target(self, name: str, milvus_url: str, *, token_ciphertext: str | None = None,
                             token_key_version: str | None = None,
                             connection_fingerprint: str | None = None) -> dict[str, Any]:
        if not name.strip():
            raise ValueError("Milvus Target 名称不能为空")
        if not milvus_url.strip():
            raise ValueError("Milvus Target URI 不能为空")
        with self.sessions.begin() as session:
            value = MilvusTarget(id=new_id("mt"), name=name.strip())
            session.add(value); session.flush()
            revision = MilvusTargetRevision(
                id=new_id("mtrev"), milvus_target_id=value.id, revision_no=1,
                milvus_url=milvus_url.strip(), token_ciphertext=token_ciphertext,
                token_key_version=token_key_version,
                connection_fingerprint=connection_fingerprint or hashlib.sha256(milvus_url.strip().encode()).hexdigest(),
                verification_status="pending_verification",
            )
            session.add(revision); session.flush()
            value.candidate_revision_id = revision.id
            self.audit(session, "milvus_target.created", "milvus_target", value.id)
            session.flush(); return self._target_payload(value, None, revision)

    def patch_milvus_target(self, target_id: str, *, name: str | None = None,
                            milvus_url: str | None = None,
                            token_ciphertext: str | None = None,
                            token_key_version: str | None = None,
                            connection_fingerprint: str | None = None,
                            connection_changed: bool = False) -> dict[str, Any]:
        with self.sessions.begin() as session:
            value = session.get(MilvusTarget, target_id)
            if not value:
                raise ValueError("Milvus Target 不存在")
            if name is not None:
                if not name.strip(): raise ValueError("Milvus Target 名称不能为空")
                value.name = name.strip()
            if connection_changed:
                current, _old_candidate = self._target_revisions(session, value)
                normalized_uri = str(milvus_url if milvus_url is not None else (current.milvus_url if current else "")).strip()
                if not normalized_uri:
                    raise ValueError("Milvus Target URI 不能为空")
                latest = session.scalar(select(func.max(MilvusTargetRevision.revision_no)).where(
                    MilvusTargetRevision.milvus_target_id == value.id,
                )) or 0
                revision = MilvusTargetRevision(
                    id=new_id("mtrev"), milvus_target_id=value.id, revision_no=latest + 1,
                    milvus_url=normalized_uri, token_ciphertext=token_ciphertext,
                    token_key_version=token_key_version,
                    connection_fingerprint=connection_fingerprint or hashlib.sha256(normalized_uri.encode()).hexdigest(),
                    verification_status="pending_verification",
                )
                session.add(revision); session.flush()
                value.candidate_revision_id = revision.id
            self.audit(session, "milvus_target.updated", "milvus_target", value.id, {
                "name_changed": name is not None, "connection_changed": connection_changed,
            })
            session.flush(); return self._target_payload(value, *self._target_revisions(session, value))

    def finish_milvus_target_verification(self, target_id: str, *, expected_revision_id: str,
                                          expected_fingerprint: str,
                                          passed: bool, error: str | None,
                                          latency_ms: int | None = None) -> dict[str, Any]:
        with self.sessions.begin() as session:
            value = session.get(MilvusTarget, target_id, with_for_update=True)
            if not value:
                raise ValueError("Milvus Target 不存在")
            if value.candidate_revision_id != expected_revision_id:
                raise ValueError("Milvus Target 验证结果已过期")
            revision = session.get(MilvusTargetRevision, expected_revision_id, with_for_update=True)
            if not revision or revision.connection_fingerprint != expected_fingerprint:
                raise ValueError("Milvus Target 验证结果已过期")
            checked_at = utc_now()
            revision.verification_status = "verified" if passed else "verification_failed"
            revision.verified_at = checked_at
            revision.verification_error = error
            revision.health_status = "healthy" if passed else "unavailable"
            revision.health_checked_at = checked_at
            revision.health_latency_ms = latency_ms
            revision.health_error = error
            if passed:
                value.current_revision_id = revision.id
                value.candidate_revision_id = None
            self.audit(session, "milvus_target.verified", "milvus_target", value.id, {
                "revision_id": revision.id, "passed": passed,
            })
            session.flush()
            return self._target_payload(value, *self._target_revisions(session, value))

    def finish_milvus_target_health_check(self, target_id: str, *, expected_revision_id: str,
                                          expected_fingerprint: str, healthy: bool,
                                          latency_ms: int | None, error: str | None) -> dict[str, Any]:
        with self.sessions.begin() as session:
            value = session.get(MilvusTarget, target_id, with_for_update=True)
            if not value:
                raise ValueError("Milvus Target 不存在")
            if value.current_revision_id != expected_revision_id:
                raise ValueError("Milvus Target 健康检查结果已过期")
            revision = session.get(MilvusTargetRevision, expected_revision_id, with_for_update=True)
            if (not revision or revision.connection_fingerprint != expected_fingerprint
                    or revision.verification_status != "verified"):
                raise ValueError("Milvus Target 健康检查结果已过期")
            revision.health_status = "healthy" if healthy else "unavailable"
            revision.health_checked_at = utc_now()
            revision.health_latency_ms = latency_ms
            revision.health_error = error
            self.audit(session, "milvus_target.health_checked", "milvus_target", value.id, {
                "revision_id": revision.id, "healthy": healthy,
                "latency_ms": latency_ms,
            })
            session.flush()
            return self._target_payload(value, *self._target_revisions(session, value))

    def list_deployments(self, project_id: str, *, allowed_deployment_id: str | None = None) -> list[dict[str, Any]]:
        with self.sessions() as session:
            query = select(ProjectDeployment, Deployment).join(
                Deployment, Deployment.id == ProjectDeployment.deployment_id,
            ).where(ProjectDeployment.project_id == project_id).order_by(Deployment.created_at)
            if allowed_deployment_id:
                query = query.where(ProjectDeployment.deployment_id == allowed_deployment_id)
            return [self._deployment_payload(binding, deployment, self._deployment_targets(session, deployment.id))
                    for binding, deployment in session.execute(query)]

    def list_shared_deployments(self, *, allowed_deployment_id: str | None = None) -> list[dict[str, Any]]:
        with self.sessions() as session:
            query = select(Deployment).order_by(Deployment.created_at)
            if allowed_deployment_id:
                query = query.where(Deployment.id == allowed_deployment_id)
            return [self._shared_deployment_payload(item, self._deployment_targets(session, item.id))
                    for item in session.scalars(query)]

    @staticmethod
    def _stage_target(
            session: Session, deployment_id: str, release_stage: str,
    ) -> tuple[MilvusTarget, MilvusTargetRevision]:
        if release_stage not in {"test", "production"}:
            raise ValueError("release_stage 只允许 test 或 production")
        row = session.execute(select(MilvusTarget, MilvusTargetRevision).join(
            DeploymentTarget, DeploymentTarget.milvus_target_id == MilvusTarget.id,
        ).join(
            MilvusTargetRevision,
            MilvusTargetRevision.id == DeploymentTarget.milvus_target_revision_id,
        ).where(
            DeploymentTarget.deployment_id == deployment_id,
            DeploymentTarget.release_stage == release_stage,
            DeploymentTarget.target_kind == "milvus",
        )).first()
        if not row:
            raise ValueError(f"Deployment 尚未配置 {release_stage} Milvus Target")
        target, revision = row
        if revision.verification_status != "verified":
            raise ValueError(f"Deployment 的 {release_stage} Milvus Target 未通过连接验证")
        return target, revision

    def deployment_stage_target(self, boundary_id: str, release_stage: str) -> dict[str, Any]:
        """Resolve one explicit stage target from a ProjectDeployment or Deployment id."""
        with self.sessions() as session:
            project_deployment = session.get(ProjectDeployment, boundary_id)
            deployment_id = project_deployment.deployment_id if project_deployment else boundary_id
            deployment = session.get(Deployment, deployment_id)
            if not deployment:
                raise ValueError("Deployment 不存在")
            target, revision = self._stage_target(session, deployment.id, release_stage)
            return {
                "deployment_id": deployment.id,
                "release_stage": release_stage,
                "target_kind": "milvus",
                "milvus_target": {
                    "id": target.id,
                    "name": target.name,
                    "revision": self._target_revision_payload(revision),
                },
            }

    @staticmethod
    def _bind_stage_target(session: Session, deployment: Deployment, release_stage: str,
                           target: MilvusTarget,
                           revision: MilvusTargetRevision) -> tuple[MilvusTarget, MilvusTargetRevision]:
        if release_stage not in {"test", "production"}:
            raise ValueError("release_stage 只允许 test 或 production")
        if revision.milvus_target_id != target.id or revision.verification_status != "verified":
            raise ValueError("只有连接验证通过的 Milvus Target Revision 才能绑定")
        link = session.scalar(select(DeploymentTarget).where(
            DeploymentTarget.deployment_id == deployment.id,
            DeploymentTarget.release_stage == release_stage,
            DeploymentTarget.target_kind == "milvus",
        ))
        if link:
            link.milvus_target_id = target.id
            link.milvus_target_revision_id = revision.id
        else:
            session.add(DeploymentTarget(
                id=new_id("dtarget"), deployment_id=deployment.id, release_stage=release_stage,
                target_kind="milvus", milvus_target_id=target.id,
                milvus_target_revision_id=revision.id,
            ))
        session.flush()
        return target, revision

    def create_shared_deployment(self, *, institution_name: str,
                                 institution_code: str) -> dict[str, Any]:
        normalized_institution = str(institution_name or "").strip() or None
        normalized_institution_code = str(institution_code or "").strip() or None
        if not normalized_institution or not normalized_institution_code:
            raise ValueError("机构 Deployment 必须填写机构名称和机构代码")
        normalized_code = institution_deployment_code(normalized_institution_code)
        normalized_name = normalized_institution
        with self.sessions.begin() as session:
            if normalized_institution_code and session.scalar(select(Deployment).where(
                    Deployment.institution_code == normalized_institution_code)):
                raise ValueError("该机构代码已有 Deployment")
            if session.scalar(select(Deployment).where(Deployment.code == normalized_code)):
                raise ValueError("Deployment 编码已存在")
            deployment = Deployment(
                id=new_id("deployment"), code=normalized_code, name=normalized_name, scope="institution",
                institution_name=normalized_institution, institution_code=normalized_institution_code,
                status="active",
            )
            session.add(deployment); session.flush()
            self.audit(session, "deployment.created", "deployment", deployment.id)
            session.flush()
            return self._shared_deployment_payload(deployment, self._deployment_targets(session, deployment.id))

    def put_deployment_target(self, deployment_id: str, release_stage: str, milvus_target_id: str,
                              milvus_target_revision_id: str, *,
                              confirm_production: bool = False,
                              expected_target_uri: str | None = None) -> dict[str, Any]:
        with self.sessions.begin() as session:
            deployment = session.get(Deployment, deployment_id)
            if not deployment:
                raise ValueError("Deployment 不存在")
            if deployment.scope != "central":
                raise ValueError("机构 Milvus 由私有化实例自行配置，中心不保存机构 Target")
            target = session.get(MilvusTarget, milvus_target_id, with_for_update=True)
            if not target:
                raise ValueError("Milvus Target 不存在")
            revision = session.get(MilvusTargetRevision, milvus_target_revision_id, with_for_update=True)
            if not revision or revision.milvus_target_id != target.id:
                raise ValueError("Milvus Target Revision 与 Target 不匹配")
            if target.current_revision_id != revision.id:
                raise ValueError("Milvus Target Revision 已过期，请刷新后重新绑定")
            if revision.verification_status != "verified":
                raise ValueError("只有连接验证通过的 Milvus Target Revision 才能绑定")
            normalized_uri = revision.milvus_url
            if release_stage == "production" and (
                    confirm_production is not True or expected_target_uri != normalized_uri):
                raise ValueError("配置生产 Target 必须确认完整的中心生产 URI")
            self._bind_stage_target(session, deployment, release_stage, target, revision)
            self.audit(session, "deployment.target_updated", "deployment", deployment.id,
                       {"release_stage": release_stage, "milvus_target_id": target.id,
                        "milvus_target_revision_id": revision.id, "milvus_url": normalized_uri})
            return {"deployment_id": deployment.id, "release_stage": release_stage,
                    "target_kind": "milvus", "milvus_target": {
                        "id": target.id, "name": target.name,
                        "revision": self._target_revision_payload(revision),
                    }}

    def bind_project_deployment(self, deployment_id: str, project_id: str) -> dict[str, Any]:
        with self.sessions.begin() as session:
            deployment, project = session.get(Deployment, deployment_id), session.get(Project, project_id)
            if not deployment or not project:
                raise ValueError("Deployment 或 Project 不存在")
            if session.scalar(select(ProjectDeployment).where(
                    ProjectDeployment.project_id == project_id,
                    ProjectDeployment.deployment_id == deployment_id)):
                raise ValueError("Project 已绑定该 Deployment")
            binding = ProjectDeployment(
                id=new_id("pdeploy"), project_id=project.id, deployment_id=deployment.id, status="active",
            )
            session.add(binding); session.flush()
            self.audit(session, "project_deployment.created", "project_deployment", binding.id)
            return self._deployment_payload(binding, deployment, self._deployment_targets(session, deployment.id))

    def list_deployment_projects(self, deployment_id: str) -> list[dict[str, Any]]:
        with self.sessions() as session:
            if not session.get(Deployment, deployment_id):
                raise ValueError("Deployment 不存在")
            rows = session.execute(select(ProjectDeployment, Project).join(
                Project, Project.id == ProjectDeployment.project_id,
            ).where(ProjectDeployment.deployment_id == deployment_id).order_by(Project.name)).all()
            return [{"project_deployment_id": binding.id, "deployment_id": deployment_id,
                     "status": binding.status, "project": {
                         "id": project.id, "code": project.code, "name": project.name,
                     }} for binding, project in rows]

    def patch_shared_deployment(self, deployment_id: str, **changes: Any) -> dict[str, Any]:
        with self.sessions.begin() as session:
            value = session.get(Deployment, deployment_id)
            if not value:
                raise ValueError("Deployment 不存在")
            if "institution_name" in changes and changes["institution_name"] is not None:
                normalized = str(changes["institution_name"]).strip()
                if value.scope == "institution" and not normalized:
                    raise ValueError("机构名称不能为空")
                value.institution_name = normalized or None
                if value.scope == "institution":
                    value.name = normalized
            if "institution_code" in changes and changes["institution_code"] is not None:
                normalized_code = str(changes["institution_code"]).strip()
                if value.scope == "institution" and not normalized_code:
                    raise ValueError("机构代码不能为空")
                if value.institution_code_locked_at and normalized_code != value.institution_code:
                    raise ValueError("机构代码已在首次发布后锁定；请使用专用机构码迁移流程")
                duplicate = session.scalar(select(Deployment).where(
                    Deployment.institution_code == normalized_code,
                    Deployment.id != value.id,
                )) if normalized_code else None
                if duplicate:
                    raise ValueError("该机构代码已有 Deployment")
                value.institution_code = normalized_code or None
            if "status" in changes and changes["status"] is not None:
                if changes["status"] not in {"active", "disabled"}:
                    raise ValueError("Deployment 状态无效")
                value.status = changes["status"]
            if value.scope == "institution" and (not value.institution_name or not value.institution_code):
                raise ValueError("机构 Deployment 必须填写机构名称和机构代码")
            session.flush()
            return self._shared_deployment_payload(value, self._deployment_targets(session, value.id))

    def create_deployment_task(self, deployment_id: str, project_task_id: str, index_profile_id: str,
                               *, qa_embedding_mode: str | None = None, top_k: int = 10,
                               enabled: bool = True, final_top_k: int | None = None,
                               reranker_serving_code: str | None = None) -> dict[str, Any]:
        with self.sessions.begin() as session:
            deployment = session.get(ProjectDeployment, deployment_id)
            task = session.get(ProjectTask, project_task_id)
            profile = session.get(KnowledgeIndexProfile, index_profile_id)
            if not deployment or not task or task.project_id != deployment.project_id:
                raise ValueError("Deployment 或 ProjectTask 不存在")
            if not profile or profile.status != "active": raise ValueError("Index Profile 不存在或未发布")
            if task.knowledge_type and profile.knowledge_type != task.knowledge_type:
                raise ValueError("ProjectTask 与 Index Profile 的 Knowledge Type 不匹配")
            if profile.knowledge_type == "qa":
                expected = {"qa-question": "question", "qa-full": "full"}.get(profile.code)
                if expected and qa_embedding_mode not in {None, expected}:
                    raise ValueError("qa_embedding_mode 与 QA Index Profile 不匹配")
                qa_embedding_mode = qa_embedding_mode or expected
            elif qa_embedding_mode is not None:
                raise ValueError("非 QA 任务不能配置 qa_embedding_mode")
            final_top_k = min(5, top_k) if final_top_k is None else final_top_k
            self._validate_retrieval_settings(session, top_k, final_top_k, reranker_serving_code)
            existing = session.scalar(select(ProjectDeploymentTask).where(
                ProjectDeploymentTask.project_deployment_id == deployment_id,
                ProjectDeploymentTask.project_task_id == project_task_id,
            ))
            if existing: raise ValueError("DeploymentTask 已存在")
            value = ProjectDeploymentTask(id=new_id("dtask"), project_deployment_id=deployment_id,
                                          project_task_id=project_task_id, index_profile_id=index_profile_id,
                                          qa_embedding_mode=qa_embedding_mode, top_k=top_k, enabled=enabled,
                                          final_top_k=final_top_k, reranker_serving_code=reranker_serving_code)
            session.add(value); session.flush(); return self._deployment_task_payload(session, value)

    def _deployment_task_payload(self, session: Session, value: ProjectDeploymentTask) -> dict[str, Any]:
        task = session.get(ProjectTask, value.project_task_id)
        profile = session.get(KnowledgeIndexProfile, value.index_profile_id) if value.index_profile_id else None
        return {"id": value.id, "project_deployment_id": value.project_deployment_id,
                "project_task_id": value.project_task_id, "task": self._task_payload(task) if task else None,
                "index_profile_id": value.index_profile_id,
                "index_profile": {"id": profile.id, "code": profile.code, "knowledge_type": profile.knowledge_type} if profile else None,
                "qa_embedding_mode": value.qa_embedding_mode, "top_k": value.top_k, "enabled": value.enabled,
                "final_top_k": value.final_top_k, "reranker_serving_code": value.reranker_serving_code}

    @staticmethod
    def _validate_retrieval_settings(session, top_k, final_top_k, reranker_serving_code):
        if type(top_k) is not int or type(final_top_k) is not int or not 1 <= final_top_k <= top_k <= 200:
            raise ValueError("检索数量必须满足 1 ≤ final_top_k ≤ top_k ≤ 200")
        if reranker_serving_code is not None:
            value = session.scalar(select(RerankerServing).where(RerankerServing.serving_code == reranker_serving_code))
            if not value or not value.is_enabled:
                raise ValueError("Reranker Serving 不存在或已停用")

    def patch_deployment_task(self, deployment_id: str, task_id: str, changes: dict) -> dict:
        with self.sessions.begin() as session:
            value = session.get(ProjectDeploymentTask, task_id, with_for_update=True)
            if not value or value.project_deployment_id != deployment_id:
                raise LookupError("DeploymentTask 不存在")
            settings = {key: changes.get(key, getattr(value, key))
                        for key in ("top_k", "final_top_k", "reranker_serving_code")}
            self._validate_retrieval_settings(session, **settings)
            for key, item in settings.items():
                setattr(value, key, item)
            if "enabled" in changes:
                value.enabled = changes["enabled"]
            self.audit(session, "routing.task_updated", "project_deployment_task", value.id, settings)
            session.flush()
            return self._deployment_task_payload(session, value)

    def list_deployment_tasks(self, deployment_id: str) -> list[dict[str, Any]]:
        with self.sessions() as session:
            if not session.get(ProjectDeployment, deployment_id): raise ValueError("Deployment 不存在")
            return [self._deployment_task_payload(session, value) for value in session.scalars(select(ProjectDeploymentTask).where(
                ProjectDeploymentTask.project_deployment_id == deployment_id).order_by(ProjectDeploymentTask.created_at))]

    def list_projects(self, *, allowed_deployment_id: str | None = None) -> list[dict[str, Any]]:
        with self.sessions() as session:
            query = select(Project).order_by(Project.created_at.desc())
            if allowed_deployment_id:
                project_ids = select(ProjectDeployment.project_id).where(
                    ProjectDeployment.deployment_id == allowed_deployment_id,
                    ProjectDeployment.status == "active",
                )
                query = query.where(Project.id.in_(project_ids))
            result = []
            for project in session.scalars(query):
                tasks = session.scalars(select(ProjectTask).where(ProjectTask.project_id == project.id)).all()
                deployments = self.list_deployments(project.id, allowed_deployment_id=allowed_deployment_id)
                result.append({"id": project.id, "code": project.code, "name": project.name, "status": project.status,
                               "tasks": [self._task_payload(task) for task in tasks], "deployments": deployments})
            return result

    def _ensure_legacy_deployment_task(self, session: Session, task: ProjectTask,
                                       libraries: list[KnowledgeLibrary]) -> ProjectDeploymentTask:
        value = session.scalar(select(ProjectDeploymentTask).where(ProjectDeploymentTask.project_task_id == task.id))
        if value: return value
        deployment = session.scalar(select(ProjectDeployment).where(ProjectDeployment.project_id == task.project_id).order_by(ProjectDeployment.created_at))
        if not deployment:
            project = session.get(Project, task.project_id)
            deployment = self._ensure_project_binding(session, project, self._ensure_central_deployment(session))
        profile_sets = [{item.id for item in self._index_profile_snapshots_for_library(session, library)} for library in libraries]
        common = set.intersection(*profile_sets) if profile_sets else set()
        preferred = {library.index_profile_id for library in libraries if library.index_profile_id}
        preferred.intersection_update(common)
        if len(preferred) == 1:
            profile_id = next(iter(preferred))
        elif len(common) == 1:
            profile_id = next(iter(common))
        else:
            raise ValueError("旧 ProjectTask 无法唯一推导 Index Profile，请先配置 DeploymentTask")
        profile = session.get(KnowledgeIndexProfile, profile_id)
        if not task.knowledge_type: task.knowledge_type = libraries[0].knowledge_type
        value = ProjectDeploymentTask(id=new_id("dtask"), project_deployment_id=deployment.id,
            project_task_id=task.id, index_profile_id=profile_id,
            qa_embedding_mode={"qa-question": "question", "qa-full": "full"}.get(profile.code if profile else ""), enabled=True)
        session.add(value); session.flush(); return value

    def put_route(self, task_id: str, org_code: str, library_ids: list[str]) -> dict[str, Any]:
        """Compatibility service entry; public HTTP clients must use DeploymentTask API."""
        with self.sessions.begin() as session:
            task = session.get(ProjectTask, task_id)
            if not task: raise ValueError("项目任务不存在")
            libraries = list(session.scalars(select(KnowledgeLibrary).where(KnowledgeLibrary.id.in_(library_ids))))
            deployment_task = self._ensure_legacy_deployment_task(session, task, libraries)
            deployment_task_id = deployment_task.id
        return self.put_deployment_route(deployment_task_id, org_code, "", library_ids)

    def put_deployment_route(self, deployment_task_id: str, org_code: str, org_name: str,
                             library_ids: list[str]) -> dict[str, Any]:
        if not org_code.strip(): raise ValueError("org_code 不能为空；general 也必须显式配置")
        if not library_ids: raise ValueError("授权至少要选择一个知识库")
        with self.sessions.begin() as session:
            deployment_task = session.get(ProjectDeploymentTask, deployment_task_id)
            if not deployment_task or not deployment_task.enabled: raise ValueError("DeploymentTask 不存在或未启用")
            deployment = session.get(ProjectDeployment, deployment_task.project_deployment_id)
            project = session.get(Project, deployment.project_id) if deployment else None
            project_task = session.get(ProjectTask, deployment_task.project_task_id)
            profile = session.get(KnowledgeIndexProfile, deployment_task.index_profile_id) if deployment_task.index_profile_id else None
            if is_qa_agent_project(project) and not qa_agent_profile_contract(project_task, profile):
                raise ValueError("qa-agent org route 的 Task 与 Index Profile 合同不匹配")
            libraries = list(session.scalars(select(KnowledgeLibrary).where(
                KnowledgeLibrary.id.in_(library_ids), KnowledgeLibrary.status == "active",
                KnowledgeLibrary.migration_status == "ready")))
            if len(libraries) != len(set(library_ids)): raise ValueError("授权包含不存在、迁移中或不可用的知识库")
            libraries_by_id = {library.id: library for library in libraries}
            ordered_libraries = [libraries_by_id[library_id] for library_id in library_ids]
            for library in ordered_libraries:
                if project_task and project_task.knowledge_type and library.knowledge_type != project_task.knowledge_type:
                    raise ValueError("KnowledgeLibrary 与 ProjectTask 的 Knowledge Type 不匹配")
                compatible = {item.id for item in self._index_profile_snapshots_for_library(session, library)}
                if not profile or profile.id not in compatible:
                    raise ValueError("KnowledgeLibrary 与 DeploymentTask 的 Index Profile 不匹配")
            route = session.scalar(select(ProjectOrgRoute).where(
                ProjectOrgRoute.project_deployment_task_id == deployment_task_id,
                ProjectOrgRoute.org_code == org_code.strip()))
            if not route:
                route = ProjectOrgRoute(id=new_id("route"), project_deployment_task_id=deployment_task_id,
                                        org_code=org_code.strip(), org_name=org_name.strip())
                session.add(route); session.flush()
            else:
                route.org_name, route.enabled = org_name.strip(), True
            for item in session.scalars(select(ProjectOrgRouteLibrary).where(
                    ProjectOrgRouteLibrary.project_org_route_id == route.id)):
                session.delete(item)
            session.flush()
            for priority, library in enumerate(ordered_libraries):
                session.add(ProjectOrgRouteLibrary(id=new_id("rl"), project_org_route_id=route.id,
                    knowledge_library_id=library.id, priority=priority, enabled=True))
            route.status = "draft"; self.audit(session, "routing.draft_updated", "project_org_route", route.id, {"library_ids": library_ids})
            return {"id": route.id, "project_deployment_task_id": route.project_deployment_task_id,
                    "org_code": route.org_code, "org_name": route.org_name,
                    "knowledge_library_ids": library_ids, "enabled": route.enabled, "status": route.status}

    def list_authorizations(self, deployment_id: str) -> list[dict[str, Any]]:
        with self.sessions() as session:
            rows = session.execute(select(ProjectOrgRoute, ProjectDeploymentTask, ProjectTask).join(
                ProjectDeploymentTask, ProjectDeploymentTask.id == ProjectOrgRoute.project_deployment_task_id,
            ).join(ProjectTask, ProjectTask.id == ProjectDeploymentTask.project_task_id).where(
                ProjectDeploymentTask.project_deployment_id == deployment_id,
            ).order_by(ProjectTask.code, ProjectOrgRoute.org_code)).all()
            result = []
            for route, deployment_task, task in rows:
                links = list(session.scalars(select(ProjectOrgRouteLibrary).where(
                    ProjectOrgRouteLibrary.project_org_route_id == route.id,
                    ProjectOrgRouteLibrary.enabled.is_(True)).order_by(ProjectOrgRouteLibrary.priority)))
                result.append({"id": route.id, "project_deployment_task_id": deployment_task.id,
                    "task_code": task.code, "org_code": route.org_code, "org_name": route.org_name,
                    "enabled": route.enabled, "knowledge_library_ids": [item.knowledge_library_id for item in links]})
            return result

    def _resolve_deployment(self, session: Session, boundary_id: str) -> ProjectDeployment:
        deployment = session.get(ProjectDeployment, boundary_id)
        if deployment: return deployment
        if not session.get(Project, boundary_id): raise ValueError("Deployment 不存在")
        values = list(session.scalars(select(ProjectDeployment).where(ProjectDeployment.project_id == boundary_id).order_by(ProjectDeployment.created_at)))
        if len(values) != 1: raise ValueError("Project 存在多个 Deployment，必须显式指定 deployment_id")
        return values[0]

    @staticmethod
    def _ready_asset_for_route(session: Session, library_id: str, profile_id: str,
                               profile_revision_id: str) -> KnowledgeAssetVersion | None:
        return session.scalar(select(KnowledgeAssetVersion).where(
            KnowledgeAssetVersion.knowledge_library_id == library_id,
            KnowledgeAssetVersion.index_profile_id == profile_id,
            KnowledgeAssetVersion.index_profile_revision_id == profile_revision_id,
            KnowledgeAssetVersion.status == "ready",
            KnowledgeAssetVersion.review_gate_status == "approved",
            KnowledgeAssetVersion.review_snapshot_digest.is_not(None),
        ).order_by(KnowledgeAssetVersion.version_no.desc()))

    def routing_snapshot(self, boundary_id: str, release_stage: str) -> dict[str, Any]:
        with self.sessions() as session:
            project_deployment = self._resolve_deployment(session, boundary_id)
            deployment = session.get(Deployment, project_deployment.deployment_id)
            project = session.get(Project, project_deployment.project_id)
            if not project or not deployment:
                raise ValueError("ProjectDeployment 的 Project 或 Deployment 不存在")
            if release_stage not in {"test", "production"}:
                raise ValueError("release_stage 只允许 test 或 production")
            target: MilvusTarget | None = None
            target_revision: MilvusTargetRevision | None = None
            try:
                target, target_revision = self._stage_target(session, deployment.id, release_stage)
            except ValueError:
                if deployment.scope != "institution":
                    raise
            if is_qa_agent_project(project):
                if deployment.scope == "institution" and (
                        not deployment.institution_name or not deployment.institution_code):
                    raise ValueError("qa-agent Deployment 尚未绑定机构名称和机构代码")
            if project.code == KG_PROJECT_CODE and release_stage != "test":
                raise ValueError("kg_for_consultation 当前没有 production RoutingSnapshot")
            task_rows = session.execute(select(ProjectDeploymentTask, ProjectTask).join(
                ProjectTask, ProjectTask.id == ProjectDeploymentTask.project_task_id).where(
                ProjectDeploymentTask.project_deployment_id == project_deployment.id,
                ProjectDeploymentTask.enabled.is_(True), ProjectTask.status == "active").order_by(ProjectTask.code)).all()
            tasks, flat_routes = [], []
            for deployment_task, task in task_rows:
                profile = session.get(KnowledgeIndexProfile, deployment_task.index_profile_id) if deployment_task.index_profile_id else None
                revision = session.get(KnowledgeIndexProfileRevision, profile.current_revision_id) if profile and profile.current_revision_id else None
                contract = session.get(StorageContractRevision, revision.storage_contract_revision_id) \
                    if revision and revision.storage_contract_revision_id else None
                embedding = session.get(EmbeddingProfile, revision.embedding_profile_id) if revision else None
                profile_payload = None if not profile or not revision else {
                    "index_profile_id": profile.id, "index_profile_code": profile.code,
                    "index_profile_revision_id": revision.id, "collection_name": revision.collection_name,
                    "fields": revision.fields_json, "collection_policy": revision.collection_policy,
                    "storage_contract_revision_id": revision.storage_contract_revision_id,
                    "embedding": None if not embedding else {"profile_id": embedding.id, "model": embedding.model,
                        "dimension": embedding.dimension, "metric_type": embedding.metric_type},
                    "storage": None if not contract else {"dimension": contract.dimension,
                        "metric_type": contract.metric_type, "index": contract.index_json,
                        "storage_spec_hash": contract.storage_spec_hash},
                }
                org_routes = []
                reranker = session.scalar(select(RerankerServing).where(
                    RerankerServing.serving_code == deployment_task.reranker_serving_code,
                )) if deployment_task.reranker_serving_code else None
                retrieval = {"final_top_k": deployment_task.final_top_k,
                             "reranker_serving_code": deployment_task.reranker_serving_code,
                             "reranker": None if not reranker else {
                                 "serving_code": reranker.serving_code, "provider_type": reranker.provider_type,
                                 "model_name": reranker.model_name}}
                for route in session.scalars(select(ProjectOrgRoute).where(
                        ProjectOrgRoute.project_deployment_task_id == deployment_task.id,
                        ProjectOrgRoute.enabled.is_(True)).order_by(ProjectOrgRoute.org_code)):
                    if is_qa_agent_project(project) and not qa_agent_profile_contract(task, profile):
                        raise ValueError("qa-agent RoutingSnapshot 的 Task/Profile/Collection 合同不匹配")
                    links = list(session.scalars(select(ProjectOrgRouteLibrary).where(
                        ProjectOrgRouteLibrary.project_org_route_id == route.id,
                        ProjectOrgRouteLibrary.enabled.is_(True)).order_by(ProjectOrgRouteLibrary.priority)))
                    libraries = []
                    for link in links:
                        library = session.get(KnowledgeLibrary, link.knowledge_library_id)
                        if not library: continue
                        asset = self._ready_asset_for_route(
                            session, library.id, profile.id, revision.id,
                        ) if profile and revision else None
                        physical_partition = asset.partition_name if asset else library.partition_name
                        library_payload = {"knowledge_library_id": library.id, "knowledge_type": library.knowledge_type,
                            "priority": link.priority,
                            "asset_version_id": asset.id if asset else None,
                            "asset_version_no": asset.version_no if asset else None,
                            "authoring_target_revision_id": asset.authoring_target_revision_id if asset else None,
                            "authoring_connection_fingerprint": asset.authoring_connection_fingerprint if asset else None,
                            "partition_name": physical_partition, "indexes": [{**profile_payload,
                                "asset_version_id": asset.id if asset else None,
                                "asset_version_no": asset.version_no if asset else None,
                                "partition_name": physical_partition}] if profile_payload else []}
                        libraries.append(library_payload)
                    org_payload = {"org_code": route.org_code, "org_name": route.org_name,
                                   "knowledge_library_ids": [item["knowledge_library_id"] for item in libraries],
                                   "libraries": libraries}
                    org_routes.append(org_payload)
                    flat_routes.append({"task_code": task.code, "org_code": route.org_code,
                                        "top_k": deployment_task.top_k, "libraries": libraries, **retrieval})
                tasks.append({"deployment_task_id": deployment_task.id, "task_id": task.id, "task_code": task.code,
                              "task_name": task.name, "knowledge_type": task.knowledge_type,
                              "qa_embedding_mode": deployment_task.qa_embedding_mode, "top_k": deployment_task.top_k,
                              "index_profile": profile_payload, "org_routes": org_routes, **retrieval})
            return {"schema": "dataforge.routing-snapshot.v7", "schema_version": 3,
                    "release_stage": release_stage,
                    "project": {"id": project.id, "code": project.code, "name": project.name},
                    "deployment": {"id": deployment.id, "code": deployment.code, "name": deployment.name,
                                    "institution_name": deployment.institution_name,
                                    "institution_code": deployment.institution_code,
                                    "scope": deployment.scope,
                                    "release_stage": release_stage},
                    "project_deployment": {"id": project_deployment.id,
                                           "project_id": project_deployment.project_id,
                                           "deployment_id": project_deployment.deployment_id,
                                           "status": project_deployment.status},
                    "milvus_target": None if not target else {
                        "id": target.id, "name": target.name, "milvus_url": target_revision.milvus_url,
                        "revision_id": target_revision.id,
                        "revision_no": target_revision.revision_no,
                        "connection_fingerprint": target_revision.connection_fingerprint,
                        "token_configured": bool(target_revision.token_ciphertext),
                    },
                    "tasks": tasks, "routes": flat_routes}

    def validate_routing(self, boundary_id: str, release_stage: str, milvus=None, *,
                         target_validation_mode: str | None = None,
                         target_reason: str | None = None) -> dict[str, Any]:
        """Validate routing configuration and, when allowed, its live Milvus target."""
        from .vector import CollectionValidationError

        snapshot = self.routing_snapshot(boundary_id, release_stage)
        checks: list[dict[str, Any]] = []
        configuration_issues: list[str] = []
        physical_targets: list[dict[str, Any]] = []

        def add_check(code: str, passed: bool, *, subject: dict[str, Any] | None = None,
                      expected: Any = True, observed: Any = True, message: str) -> None:
            checks.append({
                "code": code, "status": "passed" if passed else "blocked",
                "subject": subject or {}, "expected": expected,
                "observed": observed, "message": message,
            })

        with self.sessions() as session:
            if not snapshot["routes"]:
                configuration_issues.append("Deployment 没有授权路由")
            for task in snapshot["tasks"]:
                deployment_task = session.get(ProjectDeploymentTask, task["deployment_task_id"])
                try:
                    self._validate_retrieval_settings(session, task["top_k"], task["final_top_k"], task.get("reranker_serving_code"))
                    add_check("RETRIEVAL.CONFIGURATION", True, subject={"task_code": task["task_code"]},
                              message="检索数量及 Reranker 引用有效")
                except ValueError as exc:
                    configuration_issues.append(str(exc))
                    add_check("RETRIEVAL.CONFIGURATION", False, subject={"task_code": task["task_code"]},
                              message=str(exc))
                profile = session.get(
                    KnowledgeIndexProfile, deployment_task.index_profile_id,
                ) if deployment_task and deployment_task.index_profile_id else None
                revision = session.get(
                    KnowledgeIndexProfileRevision, profile.current_revision_id,
                ) if profile and profile.current_revision_id else None
                profile_published = bool(
                    profile and profile.status in {"active", "published"} and revision
                    and revision.status == "published"
                )
                profile_subject = {
                    "task_code": task["task_code"],
                    "index_profile_id": profile.id if profile else None,
                }
                add_check(
                    "INDEX_PROFILE.PUBLISHED" if profile_published else "INDEX_PROFILE.NOT_PUBLISHED",
                    profile_published, subject=profile_subject,
                    expected="published",
                    observed={
                        "profile_status": profile.status if profile else "missing",
                        "revision_status": revision.status if revision else "missing",
                    },
                    message=(f"任务 {task['task_code']} 的 Index Profile 已发布" if profile_published
                             else f"任务 {task['task_code']} 没有已发布 Index Profile"),
                )
                if not profile_published:
                    configuration_issues.append(f"任务 {task['task_code']} 没有已发布 Index Profile")
                if not task["org_routes"]:
                    configuration_issues.append(f"任务 {task['task_code']} 没有授权路由")
                for route in task["org_routes"]:
                    if not route["libraries"]:
                        configuration_issues.append(
                            f"任务 {task['task_code']} / {route['org_code']} 没有知识库"
                        )
                    for info in route["libraries"]:
                        library_id = str(info["knowledge_library_id"])
                        library = session.get(KnowledgeLibrary, library_id)
                        indexes = info.get("indexes") or []
                        index = indexes[0] if indexes else (task.get("index_profile") or {})
                        subject = {
                            "task_code": task["task_code"], "org_code": route["org_code"],
                            "knowledge_library_id": library_id,
                            "asset_version_id": info.get("asset_version_id"),
                            "collection_name": index.get("collection_name"),
                            "partition_name": info.get("partition_name"),
                        }
                        library_ready = bool(
                            library and library.migration_status == "ready"
                            and self._library_ready(session, library)
                        )
                        add_check(
                            "KNOWLEDGE_LIBRARY.READY" if library_ready else "KNOWLEDGE_LIBRARY.NOT_READY",
                            library_ready, subject=subject, expected="ready",
                            observed={
                                "exists": bool(library),
                                "migration_status": library.migration_status if library else "missing",
                                "vector_ready": self._library_ready(session, library) if library else False,
                            },
                            message=(f"知识库 {library_id} 已 Ready" if library_ready
                                     else f"知识库 {library_id} 向量未就绪"),
                        )
                        if not library_ready:
                            configuration_issues.append(f"知识库 {library_id} 向量未就绪")

                        asset = session.get(
                            KnowledgeAssetVersion, info.get("asset_version_id"),
                        ) if info.get("asset_version_id") else None
                        latest_asset = asset
                        if not latest_asset and profile and revision:
                            latest_asset = session.scalar(select(KnowledgeAssetVersion).where(
                                KnowledgeAssetVersion.knowledge_library_id == library_id,
                                KnowledgeAssetVersion.index_profile_id == profile.id,
                                KnowledgeAssetVersion.index_profile_revision_id == revision.id,
                            ).order_by(KnowledgeAssetVersion.version_no.desc()))
                        asset_ready = bool(asset and asset.status == "ready" and asset.review_gate_status == "approved"
                                           and asset.review_snapshot_digest)
                        add_check(
                            "ASSET_VERSION.READY" if asset_ready else "ASSET_VERSION.NOT_READY",
                            asset_ready, subject=subject, expected="ready",
                            observed={
                                "asset_version_id": latest_asset.id if latest_asset else None,
                                "status": latest_asset.status if latest_asset else "missing",
                            },
                            message=(f"知识库 {library_id} 的 AssetVersion 已 Ready" if asset_ready
                                     else f"知识库 {library_id} 没有 Ready AssetVersion"),
                        )
                        if not asset_ready:
                            configuration_issues.append(f"知识库 {library_id} 没有 Ready AssetVersion")

                        fields = index.get("fields") if isinstance(index.get("fields"), dict) else {}
                        embedding = index.get("embedding") if isinstance(index.get("embedding"), dict) else {}
                        collection = str(index.get("collection_name") or "").strip()
                        dimension = embedding.get("dimension")
                        contract_id = index.get("storage_contract_revision_id")
                        contract_defined = bool(
                            collection and fields and fields.get("vector")
                            and contract_id and isinstance(dimension, int) and dimension > 0
                        )
                        add_check(
                            "COLLECTION.CONTRACT_DEFINED" if contract_defined
                            else "COLLECTION.CONTRACT_NOT_DEFINED",
                            contract_defined, subject=subject,
                            expected={
                                "collection_name": "non-empty", "fields": "mapped",
                                "embedding_dimension": "positive integer",
                                "storage_contract_revision_id": "non-empty",
                            },
                            observed={
                                "collection_name": collection or None,
                                "fields": fields, "embedding_dimension": dimension,
                                "storage_contract_revision_id": contract_id,
                            },
                            message=(f"Collection Contract 已定义：{collection}" if contract_defined
                                     else f"知识库 {library_id} 的 Collection Contract 不完整"),
                        )
                        if not contract_defined:
                            configuration_issues.append(
                                f"知识库 {library_id} 的 Collection Contract 不完整"
                            )

                        partition = str(info.get("partition_name") or "").strip()
                        partition_defined = bool(partition and partition.startswith("kl_"))
                        add_check(
                            "PARTITION.EXPECTED_DEFINED" if partition_defined
                            else "PARTITION.EXPECTED_NOT_DEFINED",
                            partition_defined, subject=subject,
                            expected="kl_*", observed=partition or None,
                            message=(f"预期 Partition 已确定：{partition}" if partition_defined
                                     else f"知识库 {library_id} 的预期 Partition 未确定"),
                        )
                        if not partition_defined:
                            configuration_issues.append(f"知识库 {library_id} 的预期 Partition 未确定")
                        if asset_ready and contract_defined and partition_defined:
                            physical_targets.append({
                                "subject": subject, "collection_name": collection,
                                "partition_name": partition, "fields": fields,
                                "dimension": int(dimension),
                            })

        configuration_ok = not configuration_issues
        checks.insert(0, {
            "code": "ROUTING.CONFIG_COMPLETE" if configuration_ok else "ROUTING.CONFIG_INCOMPLETE",
            "status": "passed" if configuration_ok else "blocked",
            "subject": {"project_deployment_id": snapshot["project_deployment"]["id"]},
            "expected": "complete", "observed": configuration_issues or "complete",
            "message": "Routing 配置完整" if configuration_ok else "Routing 配置不完整",
        })

        mode = target_validation_mode or ("live" if milvus else "deferred_to_local")
        target_validation = {
            "mode": mode, "attempted": False, "reachable": None,
            "reason": target_reason,
        }

        def safe_milvus_error(exc: Exception) -> str:
            message = str(exc)
            if milvus:
                for secret in (getattr(milvus, "token", None), getattr(milvus, "uri", None)):
                    if secret:
                        message = message.replace(str(secret), "[redacted]")
            return message

        reachable = False
        collection_names: set[str] = set()
        if mode == "live":
            if not milvus:
                reason = target_reason or "当前 Deployment 未配置有效的目标 Milvus URI"
                target_validation.update({"reachable": False, "reason": reason})
                add_check(
                    "MILVUS.UNREACHABLE", False, expected="reachable",
                    observed="target_not_configured", message=reason,
                )
            else:
                target_validation["attempted"] = True
                try:
                    collection_names = set(milvus.list_collections())
                    reachable = True
                    target_validation["reachable"] = True
                    add_check(
                        "MILVUS.REACHABLE", True, expected="reachable",
                        observed="reachable", message="Milvus Target 可连接",
                    )
                except Exception as exc:
                    reason = safe_milvus_error(exc)
                    target_validation.update({"reachable": False, "reason": reason})
                    add_check(
                        "MILVUS.UNREACHABLE", False, expected="reachable",
                        observed=reason, message="Milvus Target 连接失败",
                    )
        else:
            target_validation["reason"] = target_reason or (
                "中心不连接机构现场 Milvus，实体检查延后到机构本地 Prepare/Activation Preflight"
            )

        if reachable:
            collection_targets: dict[tuple[str, str, int], dict[str, Any]] = {}
            partition_targets: dict[tuple[str, str], dict[str, Any]] = {}
            for target in physical_targets:
                fields_key = json.dumps(target["fields"], ensure_ascii=False, sort_keys=True)
                collection_targets.setdefault(
                    (target["collection_name"], fields_key, target["dimension"]), target,
                )
                partition_targets.setdefault(
                    (target["collection_name"], target["partition_name"]), target,
                )
            missing_collections: set[str] = set()
            for target in collection_targets.values():
                collection = target["collection_name"]
                subject = target["subject"]
                if collection not in collection_names:
                    missing_collections.add(collection)
                    add_check(
                        "COLLECTION.NOT_FOUND", False, subject=subject,
                        expected={"exists": True}, observed={"exists": False},
                        message=f"Milvus Collection {collection} 不存在",
                    )
                    continue
                add_check(
                    "COLLECTION.FOUND", True, subject=subject,
                    expected={"exists": True}, observed={"exists": True},
                    message=f"Milvus Collection {collection} 已创建",
                )
                try:
                    report = milvus.validate_collection(
                        collection, target["fields"], target["dimension"],
                    )
                    add_check(
                        "COLLECTION.FIELD_MATCHED", True, subject=subject,
                        expected={"required_fields": sorted(str(value) for value in target["fields"].values())},
                        observed={"fields": report["fields"]},
                        message="Collection 字段与 Index Profile 映射匹配",
                    )
                    add_check(
                        "COLLECTION.DIMENSION_MATCHED", True, subject=subject,
                        expected=target["dimension"], observed=report["dimension"],
                        message=f"Collection 向量维度匹配：{target['dimension']}",
                    )
                except CollectionValidationError as exc:
                    if exc.code == "COLLECTION.NOT_FOUND":
                        missing_collections.add(collection)
                    elif exc.code == "COLLECTION.DIMENSION_MISMATCH":
                        required_fields = sorted(str(value) for value in target["fields"].values())
                        add_check(
                            "COLLECTION.FIELD_MATCHED", True, subject=subject,
                            expected={"required_fields": required_fields},
                            observed={"fields": required_fields},
                            message="Collection 字段与 Index Profile 映射匹配",
                        )
                    add_check(
                        exc.code, False, subject=subject,
                        expected=exc.expected, observed=exc.observed, message=str(exc),
                    )
                except Exception as exc:
                    reason = safe_milvus_error(exc)
                    target_validation.update({"reachable": False, "reason": reason})
                    add_check(
                        "MILVUS.UNREACHABLE", False, subject=subject,
                        expected="reachable", observed=reason,
                        message="Milvus Target 校验过程中连接失败",
                    )
            for target in partition_targets.values():
                collection, partition = target["collection_name"], target["partition_name"]
                if collection in missing_collections:
                    continue
                try:
                    exists = bool(milvus.partition_exists(collection, partition))
                    add_check(
                        "PARTITION.FOUND" if exists else "PARTITION.NOT_FOUND",
                        exists, subject=target["subject"],
                        expected={"exists": True}, observed={"exists": exists},
                        message=(f"Milvus Partition {partition} 已创建" if exists
                                 else f"Milvus Partition {partition} 不存在"),
                    )
                except Exception as exc:
                    reason = safe_milvus_error(exc)
                    target_validation.update({"reachable": False, "reason": reason})
                    add_check(
                        "MILVUS.UNREACHABLE", False, subject=target["subject"],
                        expected="reachable", observed=reason,
                        message="Milvus Target 校验过程中连接失败",
                    )

        blocked = sum(item["status"] == "blocked" for item in checks)
        problems = [item["message"] for item in checks if item["status"] == "blocked"]
        return {
            "valid": blocked == 0, "blocked": blocked,
            "target_validation": target_validation,
            "checks": checks, "problems": problems, "snapshot": snapshot,
        }

    def routing_diff(self, boundary_id: str, release_stage: str) -> dict[str, Any]:
        current = self.routing_snapshot(boundary_id, release_stage)
        deployment_id = current["project_deployment"]["id"]
        stage = release_stage
        with self.sessions() as session:
            previous = session.scalar(select(ProjectRouteVersion).where(
                ProjectRouteVersion.project_deployment_id == deployment_id,
                ProjectRouteVersion.release_stage == stage,
                ProjectRouteVersion.status == "published").order_by(ProjectRouteVersion.version_no.desc()))
        def route_map(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
            return {f"{route['task_code']}:{route['org_code']}": route for route in snapshot.get("routes", [])}
        before, after = route_map(previous.snapshot_json if previous else {}), route_map(current)
        return {"release_stage": stage, "from_version": previous.version_no if previous else None,
                "added": [after[key] for key in sorted(after.keys() - before.keys())],
                "removed": [before[key] for key in sorted(before.keys() - after.keys())],
                "changed": [{"before": before[key], "after": after[key]} for key in sorted(after.keys() & before.keys()) if before[key] != after[key]]}

    def create_route_version(self, boundary_id: str, snapshot: dict[str, Any], *, status: str = "draft",
                             checksum: str | None = None, object_key: str | None = None,
                             origin: str = "central") -> ProjectRouteVersion:
        with self.sessions.begin() as session:
            project_deployment = self._resolve_deployment(session, boundary_id)
            deployment = session.get(Deployment, project_deployment.deployment_id)
            if not deployment:
                raise ValueError("Deployment 不存在")
            release_stage = str(snapshot.get("release_stage") or "")
            if release_stage not in {"test", "production"}:
                raise ValueError("RoutingSnapshot release_stage 无效")
            max_version = session.scalar(select(func.max(ProjectRouteVersion.version_no)).where(
                ProjectRouteVersion.project_deployment_id == project_deployment.id,
                ProjectRouteVersion.release_stage == release_stage)) or 0
            value = ProjectRouteVersion(id=new_id("routev"), project_id=project_deployment.project_id,
                project_deployment_id=project_deployment.id, release_stage=release_stage,
                version_no=max_version + 1, origin=origin,
                status=status, snapshot_json=snapshot, checksum=checksum, object_key=object_key,
                published_at=utc_now() if status == "published" else None)
            session.add(value); session.flush()
            asset_links: dict[str, dict[str, Any]] = {}
            for route in snapshot.get("routes", []):
                for library in route.get("libraries", []):
                    asset_id = library.get("asset_version_id")
                    if asset_id:
                        asset_links[str(library["knowledge_library_id"])] = {
                            **library, "priority": int(library.get("priority", 0)),
                        }
            for library_id, payload in asset_links.items():
                asset = session.get(KnowledgeAssetVersion, payload["asset_version_id"])
                if not asset or asset.status != "ready":
                    raise ValueError(f"RouteVersion 引用的 AssetVersion 不可用：{payload['asset_version_id']}")
                session.add(ProjectRouteVersionAsset(
                    id=new_id("rva"), project_route_version_id=value.id,
                    knowledge_library_id=library_id, knowledge_asset_version_id=asset.id,
                    collection_name=asset.collection_name, partition_name=asset.partition_name,
                    priority=int(payload.get("priority", 0)),
                ))
                asset.unreferenced_at = None
            self.audit(session, "routing.version_created", "project_route_version", value.id,
                                           {"version_no": value.version_no, "status": status,
                                             "project_deployment_id": project_deployment.id,
                                             "deployment_id": deployment.id,
                                             "release_stage": release_stage})
            return value

    def freeze_route_version(self, boundary_id: str, release_stage: str) -> dict[str, Any]:
        with self.sessions() as session:
            project_deployment = self._resolve_deployment(session, boundary_id)
            deployment = session.get(Deployment, project_deployment.deployment_id)
            if not deployment or deployment.scope != "institution":
                raise ValueError("只有 institution Deployment 可以冻结离线路由版本")
        check = self.validate_routing(
            boundary_id, release_stage,
            target_validation_mode="deferred_to_local",
            target_reason=("中心不连接机构现场 Milvus，实体检查延后到机构本地 "
                           "Prepare/Activation Preflight"),
        )
        if not check["valid"]:
            raise ValueError("路由校验失败：" + "；".join(check["problems"]))
        encoded = json.dumps(check["snapshot"], ensure_ascii=False, sort_keys=True,
                             separators=(",", ":")).encode("utf-8")
        checksum = hashlib.sha256(encoded).hexdigest()
        version = self.create_route_version(
            boundary_id, check["snapshot"], status="frozen", checksum=checksum,
            origin="central_offline",
        )
        with self.sessions.begin() as session:
            project_deployment = session.get(ProjectDeployment, version.project_deployment_id)
            deployment = session.get(Deployment, project_deployment.deployment_id) if project_deployment else None
            if deployment and not deployment.institution_code_locked_at:
                deployment.institution_code_locked_at = utc_now()
            for route in session.scalars(select(ProjectOrgRoute).join(
                ProjectDeploymentTask,
                ProjectDeploymentTask.id == ProjectOrgRoute.project_deployment_task_id,
            ).where(ProjectDeploymentTask.project_deployment_id == version.project_deployment_id)):
                route.status = "frozen"
        return {"id": version.id, "project_deployment_id": version.project_deployment_id,
                "release_stage": version.release_stage, "version_no": version.version_no,
                "status": version.status, "checksum": checksum}

    def create_imported_route_candidates(self, migration_job_id: str,
                                         projects: list[dict[str, Any]]) -> list[dict[str, Any]]:
        with self.sessions.begin() as session:
            job = session.get(KnowledgeMigrationJob, migration_job_id)
            if not job or job.direction != "import":
                raise ValueError("导入任务不存在")
            values = []
            for payload in projects:
                deployment_id = str(payload.get("project_deployment_id") or
                                    (payload.get("project_deployment") or {}).get("id") or "")
                if not deployment_id or not session.get(ProjectDeployment, deployment_id):
                    raise ValueError("候选路由引用的 ProjectDeployment 不存在")
                existing = session.scalar(select(ImportedRouteCandidate).where(
                    ImportedRouteCandidate.migration_job_id == migration_job_id,
                    ImportedRouteCandidate.project_deployment_id == deployment_id,
                ))
                if existing:
                    values.append(existing); continue
                snapshot = dict(payload.get("snapshot") or payload.get("route_snapshot") or {})
                asset_ids = sorted(self._asset_ids_in_json(snapshot))
                assets = list(session.scalars(select(KnowledgeAssetVersion).where(
                    KnowledgeAssetVersion.id.in_(asset_ids), KnowledgeAssetVersion.status == "ready",
                ))) if asset_ids else []
                ready = len(assets) == len(asset_ids)
                value = ImportedRouteCandidate(
                    id=new_id("irc"), migration_job_id=migration_job_id,
                    project_deployment_id=deployment_id,
                    source_route_version=int(payload.get("route_version") or payload.get("version_no") or 0),
                    source_route_checksum=payload.get("route_checksum") or payload.get("checksum"),
                    status="ready" if ready else "waiting_assets", snapshot_json=snapshot,
                    readiness_json={"asset_version_ids": asset_ids,
                                    "ready_asset_version_ids": sorted(item.id for item in assets)},
                )
                session.add(value); values.append(value)
            return [self._route_candidate_payload(item) for item in values]

    @staticmethod
    def _route_candidate_payload(item: ImportedRouteCandidate) -> dict[str, Any]:
        return {"id": item.id, "migration_job_id": item.migration_job_id,
                "project_deployment_id": item.project_deployment_id,
                "source_route_version": item.source_route_version,
                "source_route_checksum": item.source_route_checksum, "status": item.status,
                "snapshot": item.snapshot_json, "readiness": item.readiness_json,
                "activated_route_version_id": item.activated_route_version_id,
                "error": item.error}

    def list_imported_route_candidates(self, migration_job_id: str | None = None) -> list[dict[str, Any]]:
        with self.sessions() as session:
            query = select(ImportedRouteCandidate).order_by(ImportedRouteCandidate.created_at)
            if migration_job_id:
                query = query.where(ImportedRouteCandidate.migration_job_id == migration_job_id)
            return [self._route_candidate_payload(item) for item in session.scalars(query)]

    def start_route_candidate_activation(self, candidate_id: str) -> dict[str, Any]:
        with self.sessions.begin() as session:
            candidate = session.get(ImportedRouteCandidate, candidate_id, with_for_update=True)
            if not candidate:
                raise ValueError("候选路由不存在")
            if candidate.status == "activated":
                version = session.get(ProjectRouteVersion, candidate.activated_route_version_id)
                return {"candidate_id": candidate.id, "route_version_id": version.id,
                        "project_deployment_id": candidate.project_deployment_id,
                        "version_no": version.version_no, "snapshot": version.snapshot_json,
                        "idempotent": True}
            if candidate.status not in {"ready", "failed"}:
                raise ValueError("候选路由当前不可激活")
            asset_ids = self._asset_ids_in_json(candidate.snapshot_json)
            ready_ids = set(session.scalars(select(KnowledgeAssetVersion.id).where(
                KnowledgeAssetVersion.id.in_(asset_ids), KnowledgeAssetVersion.status == "ready",
            ))) if asset_ids else set()
            if ready_ids != asset_ids:
                raise ValueError("候选路由引用的 AssetVersion 尚未全部 Ready")
            candidate.status, candidate.error = "activating", None
            snapshot = dict(candidate.snapshot_json)
        version = self.create_route_version(
            candidate.project_deployment_id, snapshot, status="draft", origin="institution_release",
        )
        return {"candidate_id": candidate_id, "route_version_id": version.id,
                "project_deployment_id": candidate.project_deployment_id,
                "version_no": version.version_no, "snapshot": snapshot, "idempotent": False}

    def finish_route_candidate_activation(self, candidate_id: str, route_version_id: str,
                                          checksum: str | None, object_key: str | None,
                                          error: str | None = None) -> dict[str, Any]:
        with self.sessions.begin() as session:
            candidate = session.get(ImportedRouteCandidate, candidate_id, with_for_update=True)
            version = session.get(ProjectRouteVersion, route_version_id, with_for_update=True)
            if not candidate or not version:
                raise ValueError("候选路由或 RouteVersion 不存在")
            if error:
                candidate.status, candidate.error = "failed", error
                return self._route_candidate_payload(candidate)
            version.status, version.checksum, version.object_key = "published", checksum, object_key
            version.published_at = utc_now()
            candidate.status, candidate.error = "activated", None
            candidate.activated_route_version_id = version.id
            for route in session.scalars(select(ProjectOrgRoute).join(
                ProjectDeploymentTask,
                ProjectDeploymentTask.id == ProjectOrgRoute.project_deployment_task_id,
            ).where(ProjectDeploymentTask.project_deployment_id == candidate.project_deployment_id)):
                route.status = "published"
            self.audit(session, "routing.candidate_activated", "imported_route_candidate", candidate.id,
                       {"route_version_id": version.id, "checksum": checksum})
            return self._route_candidate_payload(candidate)

    def create_institution_release_draft(self, target_deployment_id: str, package_kind: str,
                                         *, target_institution_code: str,
                                         release_stage: str,
                                         route_version_ids: list[str] | None = None,
                                         knowledge_library_ids: list[str] | None = None,
                                         extra_asset_version_ids: list[str] | None = None,
                                         base_release_id: str | None = None,
                                         include_full_document_library: bool = False) -> dict[str, Any]:
        if package_kind not in {"deployment_seed", "institution_release", "knowledge_update"}:
            raise ValueError("机构发布包类型无效")
        normalized_target_code = str(target_institution_code or "").strip()
        if not normalized_target_code:
            raise ValueError("机构发布目标 institution_code 不能为空")
        with self.sessions.begin() as session:
            deployment = session.get(Deployment, target_deployment_id)
            if not deployment or deployment.scope != "institution":
                raise ValueError("机构发布目标必须是 institution Deployment")
            if deployment.institution_code != normalized_target_code:
                raise ValueError("机构发布目标 institution_code 与 Deployment 不匹配")
            route_ids = list(dict.fromkeys(route_version_ids or []))
            library_ids = list(dict.fromkeys(knowledge_library_ids or []))
            extra_asset_ids = list(dict.fromkeys(extra_asset_version_ids or []))
            if package_kind == "knowledge_update":
                if route_ids or extra_asset_ids or not library_ids:
                    raise ValueError("Knowledge Update 只允许选择知识库")
            elif not route_ids:
                raise ValueError("Seed/Institution Release 至少选择一个 frozen RouteVersion")
            routes = list(session.scalars(select(ProjectRouteVersion).where(
                ProjectRouteVersion.id.in_(route_ids), ProjectRouteVersion.status == "frozen",
            ))) if route_ids else []
            if len(routes) != len(route_ids):
                raise ValueError("机构发布包含不存在或未 frozen 的 RouteVersion")
            route_stages = {item.release_stage for item in routes}
            if len(route_stages) > 1:
                raise ValueError("机构发布不能混合测试环境和生产环境的项目版本")
            resolved_stage = release_stage
            if resolved_stage not in {"test", "production"}:
                raise ValueError("release_stage 只允许 test 或 production")
            if route_stages and route_stages != {resolved_stage}:
                raise ValueError("机构发布环境与所选 frozen RouteVersion 不一致")
            project_deployments = {item.id: item for item in session.scalars(select(ProjectDeployment).where(
                ProjectDeployment.id.in_([item.project_deployment_id for item in routes]),
            ))} if routes else {}
            if any(project_deployments[item.project_deployment_id].deployment_id != deployment.id for item in routes):
                raise ValueError("多项目 RouteVersion 必须属于同一目标机构")
            if len({item.project_deployment_id for item in routes}) != len(routes):
                raise ValueError("同一 ProjectDeployment 只能选择一个 RouteVersion")
            draft = InstitutionReleaseDraft(
                id=new_id("reldraft"), target_deployment_id=deployment.id,
                package_kind=package_kind, base_release_id=base_release_id,
                selection_json={"target_institution_code": normalized_target_code,
                                "release_stage": resolved_stage,
                                "route_version_ids": route_ids, "knowledge_library_ids": library_ids,
                                "extra_asset_version_ids": extra_asset_ids,
                                "include_full_document_library": bool(include_full_document_library)},
            )
            session.add(draft); session.flush()
            for route in routes:
                session.add(InstitutionReleaseDraftProject(
                    id=new_id("reldp"), institution_release_draft_id=draft.id,
                    project_deployment_id=route.project_deployment_id,
                    project_route_version_id=route.id,
                ))
            self.audit(session, "institution_release.draft_created", "institution_release_draft", draft.id,
                       {"package_kind": package_kind, "target_deployment_id": deployment.id,
                         "route_version_ids": route_ids, "knowledge_library_ids": library_ids,
                         "extra_asset_version_ids": extra_asset_ids})
            return self._institution_release_draft_payload(session, draft)

    @staticmethod
    def _institution_release_draft_payload(session: Session, draft: InstitutionReleaseDraft) -> dict[str, Any]:
        projects = [{
            "project_deployment_id": item.project_deployment_id,
            "project_route_version_id": item.project_route_version_id,
        } for item in session.scalars(select(InstitutionReleaseDraftProject).where(
            InstitutionReleaseDraftProject.institution_release_draft_id == draft.id,
        ).order_by(InstitutionReleaseDraftProject.created_at))]
        return {"id": draft.id, "target_deployment_id": draft.target_deployment_id,
                "target_institution_code": (draft.selection_json or {}).get("target_institution_code"),
                "package_kind": draft.package_kind, "status": draft.status,
                "revision_no": draft.revision_no, "base_release_id": draft.base_release_id,
                "release_stage": (draft.selection_json or {}).get("release_stage"),
                "selection": draft.selection_json or {}, "milvus_override": draft.milvus_override_json or {},
                "milvus_override_reason": draft.milvus_override_reason, "projects": projects,
                "created_at": draft.created_at.isoformat(), "updated_at": draft.updated_at.isoformat()}

    def get_institution_release_draft(self, draft_id: str) -> dict[str, Any]:
        with self.sessions() as session:
            draft = session.get(InstitutionReleaseDraft, draft_id)
            if not draft:
                raise ValueError("机构发布草稿不存在")
            return self._institution_release_draft_payload(session, draft)

    def update_institution_release_draft(self, draft_id: str, *, release_stage: str | None = None,
                                         route_version_ids: list[str] | None = None,
                                         knowledge_library_ids: list[str] | None = None,
                                         extra_asset_version_ids: list[str] | None = None,
                                         base_release_id: str | None = None,
                                         include_full_document_library: bool | None = None,
                                         milvus_override: dict[str, Any] | None = None,
                                         milvus_override_reason: str | None = None) -> dict[str, Any]:
        with self.sessions.begin() as session:
            draft = session.get(InstitutionReleaseDraft, draft_id, with_for_update=True)
            if not draft or draft.status not in {"draft", "planned", "failed"}:
                raise ValueError("机构发布草稿当前不可编辑")
            current = dict(draft.selection_json or {})
            deployment = session.get(Deployment, draft.target_deployment_id)
            if (not deployment or deployment.scope != "institution"
                    or not current.get("target_institution_code")
                    or current["target_institution_code"] != deployment.institution_code):
                raise ValueError("机构发布草稿的 institution_code 与目标 Deployment 不匹配")
            route_ids = list(dict.fromkeys(route_version_ids if route_version_ids is not None
                                           else current.get("route_version_ids") or []))
            library_ids = list(dict.fromkeys(knowledge_library_ids if knowledge_library_ids is not None
                                             else current.get("knowledge_library_ids") or []))
            extra_asset_ids = list(dict.fromkeys(extra_asset_version_ids if extra_asset_version_ids is not None
                                                 else current.get("extra_asset_version_ids") or []))
            if draft.package_kind == "knowledge_update":
                if route_ids or extra_asset_ids or not library_ids:
                    raise ValueError("Knowledge Update 只允许选择知识库")
            elif not route_ids:
                raise ValueError("Seed/Institution Release 至少选择一个 frozen RouteVersion")
            routes = list(session.scalars(select(ProjectRouteVersion).where(
                ProjectRouteVersion.id.in_(route_ids), ProjectRouteVersion.status == "frozen",
            ))) if route_ids else []
            if len(routes) != len(route_ids):
                raise ValueError("机构发布包含不存在或未 frozen 的 RouteVersion")
            route_stages = {item.release_stage for item in routes}
            if len(route_stages) > 1:
                raise ValueError("机构发布不能混合测试环境和生产环境的项目版本")
            resolved_stage = (release_stage or current.get("release_stage")
                              or (next(iter(route_stages)) if route_stages else None))
            if resolved_stage not in {"test", "production"}:
                raise ValueError("release_stage 只允许 test 或 production")
            if route_stages and route_stages != {resolved_stage}:
                raise ValueError("机构发布环境与所选 frozen RouteVersion 不一致")
            bindings = {item.id: item for item in session.scalars(select(ProjectDeployment).where(
                ProjectDeployment.id.in_([item.project_deployment_id for item in routes]),
            ))} if routes else {}
            if any(bindings[item.project_deployment_id].deployment_id != draft.target_deployment_id for item in routes):
                raise ValueError("多项目 RouteVersion 必须属于同一目标机构")
            if len({item.project_deployment_id for item in routes}) != len(routes):
                raise ValueError("同一 ProjectDeployment 只能选择一个 RouteVersion")
            if milvus_override:
                if not str(milvus_override_reason or "").strip():
                    raise ValueError("临时 Milvus 覆盖必须填写原因")
                forbidden = {"password", "token", "secret", "api_key"} & {
                    str(key).lower() for key in milvus_override
                }
                if forbidden:
                    raise ValueError("Release Snapshot 禁止保存 Milvus 凭据")
            session.execute(delete(InstitutionReleaseDraftProject).where(
                InstitutionReleaseDraftProject.institution_release_draft_id == draft.id,
            ))
            for route in routes:
                session.add(InstitutionReleaseDraftProject(
                    id=new_id("reldp"), institution_release_draft_id=draft.id,
                    project_deployment_id=route.project_deployment_id,
                    project_route_version_id=route.id,
                ))
            draft.selection_json = {
                "target_institution_code": current["target_institution_code"],
                "release_stage": resolved_stage,
                "route_version_ids": route_ids, "knowledge_library_ids": library_ids,
                "extra_asset_version_ids": extra_asset_ids,
                "include_full_document_library": bool(
                    current.get("include_full_document_library", False)
                    if include_full_document_library is None else include_full_document_library
                ),
            }
            draft.base_release_id = base_release_id
            draft.milvus_override_json = dict(milvus_override or {})
            draft.milvus_override_reason = str(milvus_override_reason or "").strip() or None
            draft.revision_no += 1
            draft.status = "draft"
            session.flush()
            return self._institution_release_draft_payload(session, draft)

    def freeze_institution_release_snapshot(self, draft_id: str, snapshot: dict[str, Any]) -> dict[str, Any]:
        if int((snapshot.get("preflight") or {}).get("blocked") or 0) > 0:
            raise ValueError("机构 Release 存在阻断项，禁止冻结")
        encoded = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        digest = hashlib.sha256(encoded).hexdigest()
        with self.sessions.begin() as session:
            draft = session.get(InstitutionReleaseDraft, draft_id, with_for_update=True)
            if not draft or draft.status not in {"draft", "planned", "failed"}:
                raise ValueError("机构发布草稿当前不可冻结")
            deployment = session.get(Deployment, draft.target_deployment_id)
            target_code = (draft.selection_json or {}).get("target_institution_code")
            snapshot_deployment = snapshot.get("deployment") or {}
            if (not deployment or deployment.scope != "institution" or not target_code
                    or deployment.institution_code != target_code
                    or snapshot_deployment.get("id") != deployment.id
                    or snapshot_deployment.get("institution_code") != target_code):
                raise ValueError("机构发布 Snapshot 的 institution_code 与目标 Deployment 不匹配")
            existing = session.scalar(select(InstitutionReleaseSnapshot).where(
                InstitutionReleaseSnapshot.manifest_digest == digest,
            ))
            if existing:
                draft.status = "frozen"
                return self._institution_release_snapshot_payload(existing)
            value = InstitutionReleaseSnapshot(
                id=new_id("release"), institution_release_draft_id=draft.id,
                target_deployment_id=draft.target_deployment_id, package_kind=draft.package_kind,
                base_release_id=draft.base_release_id, manifest_digest=digest,
                snapshot_json=snapshot, diff_json=dict(snapshot.get("diff_summary") or {}),
                tombstones_json=list(snapshot.get("tombstones") or []), status="frozen",
            )
            session.add(value); draft.status = "frozen"; session.flush()
            self.audit(session, "institution_release.frozen", "institution_release_snapshot", value.id,
                       {"draft_id": draft.id, "manifest_digest": digest})
            return self._institution_release_snapshot_payload(value)

    @staticmethod
    def _institution_release_snapshot_payload(value: InstitutionReleaseSnapshot) -> dict[str, Any]:
        return {"id": value.id, "draft_id": value.institution_release_draft_id,
                "target_deployment_id": value.target_deployment_id, "package_kind": value.package_kind,
                "base_release_id": value.base_release_id, "manifest_digest": value.manifest_digest,
                "snapshot": value.snapshot_json, "diff": value.diff_json,
                "tombstones": value.tombstones_json, "status": value.status,
                "created_at": value.created_at.isoformat()}

    def get_institution_release_snapshot(self, release_id: str) -> dict[str, Any]:
        with self.sessions() as session:
            value = session.get(InstitutionReleaseSnapshot, release_id)
            if not value:
                raise ValueError("机构发布快照不存在")
            return self._institution_release_snapshot_payload(value)

    def list_institution_release_snapshots(self, target_deployment_id: str | None = None) -> list[dict[str, Any]]:
        with self.sessions() as session:
            query = select(InstitutionReleaseSnapshot)
            if target_deployment_id:
                query = query.where(InstitutionReleaseSnapshot.target_deployment_id == target_deployment_id)
            return [self._institution_release_snapshot_payload(item) for item in session.scalars(
                query.order_by(InstitutionReleaseSnapshot.created_at.desc())
            )]

    def update_institution_release_status(self, release_id: str, status: str) -> dict[str, Any]:
        if status not in {"frozen", "building", "ready", "failed"}:
            raise ValueError("机构 Release 状态无效")
        with self.sessions.begin() as session:
            value = session.get(InstitutionReleaseSnapshot, release_id, with_for_update=True)
            if not value:
                raise ValueError("机构 Release 不存在")
            value.status = status
            return self._institution_release_snapshot_payload(value)

    def mark_route_published(self, version_id: str, checksum: str, object_key: str) -> dict[str, Any]:
        with self.sessions.begin() as session:
            version = session.get(ProjectRouteVersion, version_id)
            if not version: raise ValueError("路由版本不存在")
            version.status, version.checksum, version.object_key, version.published_at = "published", checksum, object_key, utc_now()
            for route in session.scalars(select(ProjectOrgRoute).join(ProjectDeploymentTask,
                    ProjectDeploymentTask.id == ProjectOrgRoute.project_deployment_task_id).where(
                    ProjectDeploymentTask.project_deployment_id == version.project_deployment_id)):
                route.status = "published"
            self.audit(session, "routing.published", "project_route_version", version.id, {"checksum": checksum})
            return {"id": version.id, "project_id": version.project_id,
                    "project_deployment_id": version.project_deployment_id, "origin": version.origin,
                    "release_stage": version.release_stage, "version_no": version.version_no,
                    "status": version.status, "checksum": checksum, "object_key": object_key}

    def published_route_version(self, boundary_id: str, version_no: int | None = None, *,
                                release_stage: str) -> ProjectRouteVersion:
        with self.sessions() as session:
            project_deployment = self._resolve_deployment(session, boundary_id)
            query = select(ProjectRouteVersion).where(ProjectRouteVersion.project_deployment_id == project_deployment.id,
                                                       ProjectRouteVersion.release_stage == release_stage,
                                                       ProjectRouteVersion.status == "published")
            if version_no is not None: query = query.where(ProjectRouteVersion.version_no == version_no)
            value = session.scalar(query.order_by(ProjectRouteVersion.version_no.desc()))
            if not value: raise ValueError("没有可回滚的已发布路由版本")
            return value

    def restore_authorizations(self, deployment_id: str, snapshot: dict[str, Any]) -> None:
        if snapshot.get("project_deployment", {}).get("id") != deployment_id:
            raise ValueError("历史 RoutingSnapshot 不属于当前 Deployment")
        with self.sessions.begin() as session:
            tasks = {value.id: value for value in session.scalars(select(ProjectDeploymentTask).where(
                ProjectDeploymentTask.project_deployment_id == deployment_id))}
            for task_payload in snapshot.get("tasks", []):
                deployment_task = tasks.get(task_payload.get("deployment_task_id"))
                if not deployment_task: raise ValueError("历史 RoutingSnapshot 引用的 DeploymentTask 不存在")
                deployment_task.top_k = task_payload["top_k"]
                deployment_task.final_top_k = task_payload.get("final_top_k", min(5, deployment_task.top_k))
                deployment_task.reranker_serving_code = task_payload.get("reranker_serving_code")
                deployment_task.enabled = True
                for route in session.scalars(select(ProjectOrgRoute).where(
                        ProjectOrgRoute.project_deployment_task_id == deployment_task.id)):
                    session.delete(route)
                session.flush()
                for org in task_payload.get("org_routes", []):
                    route = ProjectOrgRoute(id=new_id("route"), project_deployment_task_id=deployment_task.id,
                        org_code=str(org.get("org_code", "")), org_name=str(org.get("org_name", "")), enabled=True)
                    session.add(route); session.flush()
                    for priority, library_id in enumerate(org.get("knowledge_library_ids", [])):
                        if not session.get(KnowledgeLibrary, library_id): raise ValueError("历史 RoutingSnapshot 引用的知识库不存在")
                        session.add(ProjectOrgRouteLibrary(id=new_id("rl"), project_org_route_id=route.id,
                            knowledge_library_id=library_id, priority=priority, enabled=True))

    def list_route_versions(self, boundary_id: str, release_stage: str) -> list[dict[str, Any]]:
        with self.sessions() as session:
            project_deployment = self._resolve_deployment(session, boundary_id)
            return [{"id": item.id, "project_id": item.project_id, "project_deployment_id": item.project_deployment_id,
                     "origin": item.origin, "release_stage": item.release_stage,
                     "version_no": item.version_no, "status": item.status,
                     "checksum": item.checksum, "object_key": item.object_key,
                     "created_at": item.created_at.isoformat(), "published_at": item.published_at.isoformat() if item.published_at else None}
                    for item in session.scalars(select(ProjectRouteVersion).where(
                        ProjectRouteVersion.project_deployment_id == project_deployment.id,
                        ProjectRouteVersion.release_stage == release_stage).order_by(ProjectRouteVersion.version_no.desc()))]

    def route_version_detail(self, boundary_id: str, version_no: int,
                             release_stage: str) -> dict[str, Any]:
        with self.sessions() as session:
            project_deployment = self._resolve_deployment(session, boundary_id)
            value = session.scalar(select(ProjectRouteVersion).where(
                ProjectRouteVersion.project_deployment_id == project_deployment.id,
                ProjectRouteVersion.release_stage == release_stage,
                ProjectRouteVersion.version_no == version_no))
            if not value: raise ValueError("路由版本不存在")
            assets = [{
                "knowledge_library_id": item.knowledge_library_id,
                "knowledge_asset_version_id": item.knowledge_asset_version_id,
                "collection_name": item.collection_name, "partition_name": item.partition_name,
                "priority": item.priority,
            } for item in session.scalars(select(ProjectRouteVersionAsset).where(
                ProjectRouteVersionAsset.project_route_version_id == value.id,
            ).order_by(ProjectRouteVersionAsset.priority))]
            return {"id": value.id, "project_id": value.project_id, "project_deployment_id": value.project_deployment_id,
                    "origin": value.origin, "release_stage": value.release_stage,
                    "version_no": value.version_no, "status": value.status,
                    "checksum": value.checksum, "object_key": value.object_key, "snapshot": value.snapshot_json,
                    "assets": assets,
                    "created_at": value.created_at.isoformat(), "published_at": value.published_at.isoformat() if value.published_at else None}

    def runtime_routing_snapshot(self, project_code: str, deployment_code: str,
                                 release_stage: str) -> dict[str, Any]:
        if release_stage not in {"test", "production"}:
            raise ValueError("release_stage 只允许 test 或 production")
        with self.sessions() as session:
            project = session.scalar(select(Project).where(Project.code == project_code))
            if not project:
                raise ValueError("Project 不存在")
            row = session.execute(select(ProjectDeployment, Deployment).join(
                Deployment, Deployment.id == ProjectDeployment.deployment_id,
            ).where(
                ProjectDeployment.project_id == project.id,
                Deployment.code == deployment_code,
            )).first()
            if not row:
                raise ValueError("Deployment 不存在")
            project_deployment, deployment = row
            if project.code == KG_PROJECT_CODE and release_stage != "test":
                raise ValueError("kg_for_consultation 当前没有 production RoutingSnapshot")
            version = session.scalar(select(ProjectRouteVersion).where(
                ProjectRouteVersion.project_deployment_id == project_deployment.id,
                ProjectRouteVersion.release_stage == release_stage,
                ProjectRouteVersion.status == "published",
            ).order_by(ProjectRouteVersion.version_no.desc()))
            if not version:
                raise ValueError("该阶段没有已发布 RoutingSnapshot")
            payload = dict(version.snapshot_json or {})
            payload.update({"version": version.version_no, "checksum": version.checksum,
                            "published_at": version.published_at.isoformat() if version.published_at else None})
            return payload

    @staticmethod
    def _migration_job_payload(job: KnowledgeMigrationJob, items: list[KnowledgeMigrationItem] | None = None) -> dict[str, Any]:
        return {
            "id": job.id, "direction": job.direction, "package_kind": job.package_kind,
            "package_id": job.package_id, "project_id": job.project_id,
            "project_deployment_id": job.project_deployment_id,
            "target_deployment_id": job.target_deployment_id,
            "release_snapshot_id": job.release_snapshot_id,
            "status": job.status, "stage": job.stage,
            "checkpoint": job.checkpoint_json or {}, "conflicts": job.conflict_json or {},
            "signature_status": job.signature_status, "package_path": job.package_path,
            "package_sha256": job.package_sha256, "attempt_count": job.attempt_count,
            "error": job.error, "created_at": job.created_at.isoformat(),
            "started_at": job.started_at.isoformat() if job.started_at else None,
            "finished_at": job.finished_at.isoformat() if job.finished_at else None,
            "items": [{"id": item.id, "knowledge_library_id": item.knowledge_library_id,
                       "collection_name": item.collection_name, "partition_name": item.partition_name,
                       "source_count": item.source_count, "target_count": item.target_count,
                       "source_digest": item.source_digest, "target_digest": item.target_digest,
                       "status": item.status, "resolution": item.resolution,
                       "detail": item.detail_json or {}, "error": item.error}
                      for item in (items or [])],
        }

    def create_migration_job(self, *, direction: str, package_kind: str, project_id: str | None = None,
                             project_deployment_id: str | None = None, package_id: str | None = None,
                             target_deployment_id: str | None = None, release_snapshot_id: str | None = None,
                             package_path: str | None = None, package_sha256: str | None = None,
                             status: str = "planning", stage: str = "planning", checkpoint: dict | None = None,
                             signature_status: str | None = None, items: list[dict] | None = None) -> dict[str, Any]:
        if direction not in {"export", "import"} or package_kind not in {
            "deployment_seed", "institution_release", "knowledge_update",
        }:
            raise ValueError("migration job 类型无效")
        with self.sessions.begin() as session:
            if package_id:
                existing = session.scalar(select(KnowledgeMigrationJob).where(KnowledgeMigrationJob.package_id == package_id))
                if existing:
                    existing_items = list(session.scalars(select(KnowledgeMigrationItem).where(
                        KnowledgeMigrationItem.migration_job_id == existing.id)))
                    return self._migration_job_payload(existing, existing_items)
            job = KnowledgeMigrationJob(id=new_id("mig"), direction=direction, package_kind=package_kind,
                package_id=package_id, project_id=project_id, project_deployment_id=project_deployment_id,
                target_deployment_id=target_deployment_id, release_snapshot_id=release_snapshot_id,
                status=status, stage=stage, checkpoint_json=checkpoint or {}, package_path=package_path,
                package_sha256=package_sha256, signature_status=signature_status)
            session.add(job); session.flush()
            values = []
            for item in items or []:
                value = KnowledgeMigrationItem(id=new_id("migi"), migration_job_id=job.id,
                    knowledge_library_id=item["knowledge_library_id"], collection_name=item["collection_name"],
                    partition_name=item["partition_name"], source_count=item.get("source_count", 0),
                    source_digest=item.get("source_digest"), detail_json=item.get("detail", {}))
                session.add(value); values.append(value)
            self.audit(session, "migration.job_created", "knowledge_migration_job", job.id,
                       {"direction": direction, "package_kind": package_kind})
            return self._migration_job_payload(job, values)

    def get_migration_job(self, job_id: str) -> dict[str, Any]:
        with self.sessions() as session:
            job = session.get(KnowledgeMigrationJob, job_id)
            if not job: raise ValueError("迁移任务不存在")
            items = list(session.scalars(select(KnowledgeMigrationItem).where(
                KnowledgeMigrationItem.migration_job_id == job.id).order_by(KnowledgeMigrationItem.created_at)))
            return self._migration_job_payload(job, items)

    def list_migration_jobs(self, *, direction: str | None = None, deployment_id: str | None = None) -> list[dict[str, Any]]:
        with self.sessions() as session:
            query = select(KnowledgeMigrationJob)
            if direction: query = query.where(KnowledgeMigrationJob.direction == direction)
            if deployment_id: query = query.where(KnowledgeMigrationJob.project_deployment_id == deployment_id)
            jobs = list(session.scalars(query.order_by(KnowledgeMigrationJob.created_at.desc())))
            return [self._migration_job_payload(job) for job in jobs]

    def claim_migration_job(self, owner: str) -> KnowledgeMigrationJob | None:
        with self.sessions.begin() as session:
            job = session.scalar(select(KnowledgeMigrationJob).where(
                or_(KnowledgeMigrationJob.status == "queued",
                    (KnowledgeMigrationJob.status == "running") &
                    (KnowledgeMigrationJob.lease_expires_at < utc_now()))
            ).order_by(KnowledgeMigrationJob.created_at).with_for_update(skip_locked=True))
            if not job: return None
            job.status, job.lease_owner = "running", owner
            job.lease_expires_at = utc_now() + timedelta(minutes=30)
            job.attempt_count += 1
            job.started_at = job.started_at or utc_now()
            return job

    def update_migration_job(self, job_id: str, *, status: str | None = None, stage: str | None = None,
                             checkpoint: dict | None = None, conflicts: dict | None = None,
                             signature_status: str | None = None, package_path: str | None = None,
                             package_sha256: str | None = None, package_id: str | None = None,
                             error: str | None = None) -> dict[str, Any]:
        with self.sessions.begin() as session:
            job = session.get(KnowledgeMigrationJob, job_id, with_for_update=True)
            if not job: raise ValueError("迁移任务不存在")
            if status is not None: job.status = status
            if stage is not None: job.stage = stage
            if checkpoint is not None: job.checkpoint_json = checkpoint
            if conflicts is not None: job.conflict_json = conflicts
            if signature_status is not None: job.signature_status = signature_status
            if package_path is not None: job.package_path = package_path
            if package_sha256 is not None: job.package_sha256 = package_sha256
            if package_id is not None: job.package_id = package_id
            job.error = error
            if status in {"completed", "failed", "ready", "conflict", "waiting"}:
                job.finished_at = utc_now() if status in {"completed", "failed"} else None
                job.lease_owner = None; job.lease_expires_at = None
            return self._migration_job_payload(job)

    def update_migration_item(self, job_id: str, library_id: str, collection_name: str, **values) -> None:
        allowed = {"source_count", "target_count", "source_digest", "target_digest", "status", "resolution", "detail_json", "error"}
        with self.sessions.begin() as session:
            item = session.scalar(select(KnowledgeMigrationItem).where(
                KnowledgeMigrationItem.migration_job_id == job_id,
                KnowledgeMigrationItem.knowledge_library_id == library_id,
                KnowledgeMigrationItem.collection_name == collection_name,
            ).with_for_update())
            if not item: raise ValueError("迁移明细不存在")
            for key, value in values.items():
                if key in allowed: setattr(item, key, value)

    def queue_migration_import(self, job_id: str, resolutions: dict[str, str] | None = None) -> dict[str, Any]:
        allowed = {"keep_local", "replace_with_central", "import_as_new"}
        with self.sessions.begin() as session:
            job = session.get(KnowledgeMigrationJob, job_id, with_for_update=True)
            if not job or job.direction != "import": raise ValueError("导入任务不存在")
            if job.status not in {"inspected", "conflict", "failed"}: raise ValueError("导入任务当前不可执行")
            for library_id, resolution in (resolutions or {}).items():
                if resolution not in allowed: raise ValueError("冲突处理方式无效")
                for item in session.scalars(select(KnowledgeMigrationItem).where(
                    KnowledgeMigrationItem.migration_job_id == job.id,
                    KnowledgeMigrationItem.knowledge_library_id == library_id,
                )): item.resolution = resolution
            job.status, job.stage, job.error, job.finished_at = "queued", "verified", None, None
            return self._migration_job_payload(job)

    def retry_migration_job(self, job_id: str) -> dict[str, Any]:
        with self.sessions.begin() as session:
            job = session.get(KnowledgeMigrationJob, job_id, with_for_update=True)
            if not job or job.status != "failed": raise ValueError("只有失败的迁移任务可以重试")
            job.status, job.error, job.finished_at = "queued", None, None
            return self._migration_job_payload(job)

    def resume_migration_job(self, job_id: str, *, selected_import_target: str | None = None) -> dict[str, Any]:
        with self.sessions.begin() as session:
            job = session.get(KnowledgeMigrationJob, job_id, with_for_update=True)
            if not job or job.direction != "import" or job.status != "waiting":
                raise ValueError("只有等待中的导入任务可以继续")
            if selected_import_target:
                if selected_import_target not in {"current_target", "candidate_target"}:
                    raise ValueError("selected_import_target 无效")
                checkpoint = dict(job.checkpoint_json or {})
                checkpoint["selected_import_target"] = selected_import_target
                job.checkpoint_json = checkpoint
            job.status, job.error, job.finished_at = "queued", None, None
            return self._migration_job_payload(job)

    @staticmethod
    def _observation_age(value: datetime | None, now: datetime | None = None) -> float | None:
        if value is None:
            return None
        current = now or utc_now()
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        return max(0.0, (current - value).total_seconds())

    def upsert_component_heartbeat(self, component: str, instance_id: str, *, status: str = "healthy",
                                   version: str | None = None, worker_id: str | None = None,
                                   current_job_id: str | None = None, details: dict | None = None) -> None:
        with self.sessions.begin() as session:
            row = session.get(ComponentHeartbeat, {"component": component, "instance_id": instance_id})
            if row is None:
                row = ComponentHeartbeat(component=component, instance_id=instance_id)
                session.add(row)
            row.status, row.last_seen_at = status, utc_now()
            row.version, row.worker_id, row.current_job_id = version, worker_id, current_job_id
            row.details_json = dict(details or {})

    def list_component_heartbeats(self, component: str | None = None) -> list[dict[str, Any]]:
        with self.sessions() as session:
            statement = select(ComponentHeartbeat)
            if component:
                statement = statement.where(ComponentHeartbeat.component == component)
            rows = list(session.scalars(statement.order_by(ComponentHeartbeat.component, ComponentHeartbeat.instance_id)))
            return [{
                "component": row.component, "instance_id": row.instance_id, "status": row.status,
                "last_seen_at": row.last_seen_at.isoformat(), "age_seconds": self._observation_age(row.last_seen_at),
                "stale": bool((self._observation_age(row.last_seen_at) or 0) > 45),
                "version": row.version, "worker_id": row.worker_id, "current_job_id": row.current_job_id,
                "details": dict(row.details_json or {}),
            } for row in rows]

    def interrupt_component_checks(self) -> int:
        with self.sessions.begin() as session:
            rows = list(session.scalars(select(ComponentCheckRun).where(ComponentCheckRun.status == "running")))
            for row in rows:
                row.status, row.completed_at, row.error_code = "failed", utc_now(), "interrupted"
            return len(rows)

    @staticmethod
    def _component_check_payload(run: ComponentCheckRun, results: list[ComponentCheckResult]) -> dict[str, Any]:
        return {
            "id": run.id, "status": run.status, "selected_components": list(run.selected_components or []),
            "requested_by": run.requested_by, "started_at": run.started_at.isoformat(),
            "completed_at": run.completed_at.isoformat() if run.completed_at else None,
            "error_code": run.error_code,
            "results": [{
                "component": row.component, "status": row.status, "latency_ms": row.latency_ms,
                "summary": row.summary, "error_code": row.error_code, "details": dict(row.details_json or {}),
                "checked_at": row.checked_at.isoformat() if row.checked_at else None,
            } for row in sorted(results, key=lambda item: list(run.selected_components or []).index(item.component))],
        }

    def start_component_check(self, components: list[str], requested_by: str = "admin") -> tuple[dict[str, Any], bool]:
        with self.sessions.begin() as session:
            active = session.scalar(select(ComponentCheckRun).where(ComponentCheckRun.status == "running")
                                    .order_by(ComponentCheckRun.started_at).limit(1).with_for_update())
            if active:
                results = list(session.scalars(select(ComponentCheckResult).where(ComponentCheckResult.check_run_id == active.id)))
                return self._component_check_payload(active, results), False
            run = ComponentCheckRun(id=new_id("check"), status="running", selected_components=list(components),
                                    requested_by=requested_by, started_at=utc_now())
            session.add(run)
            session.flush()
            for component in components:
                session.add(ComponentCheckResult(id=new_id("checkitem"), check_run_id=run.id,
                                                 component=component, status="unknown", summary="等待检查"))
            session.flush()
            results = list(session.scalars(select(ComponentCheckResult).where(ComponentCheckResult.check_run_id == run.id)))
            return self._component_check_payload(run, results), True

    def record_component_check_result(self, run_id: str, component: str, *, status: str, latency_ms: int | None,
                                      summary: str, error_code: str | None = None,
                                      details: dict | None = None) -> None:
        with self.sessions.begin() as session:
            row = session.scalar(select(ComponentCheckResult).where(
                ComponentCheckResult.check_run_id == run_id, ComponentCheckResult.component == component,
            ).with_for_update())
            if not row:
                raise ValueError("组件检查明细不存在")
            row.status, row.latency_ms, row.summary = status, latency_ms, summary[:500]
            row.error_code, row.details_json, row.checked_at = error_code, dict(details or {}), utc_now()

    def finish_component_check(self, run_id: str) -> dict[str, Any]:
        with self.sessions.begin() as session:
            run = session.get(ComponentCheckRun, run_id, with_for_update=True)
            if not run:
                raise ValueError("组件检查不存在")
            results = list(session.scalars(select(ComponentCheckResult).where(ComponentCheckResult.check_run_id == run.id)))
            statuses = {row.status for row in results}
            run.status = "completed" if statuses <= {"healthy", "not_configured"} else "completed_with_warnings"
            run.completed_at = utc_now()
            return self._component_check_payload(run, results)

    def get_component_check(self, run_id: str) -> dict[str, Any]:
        with self.sessions() as session:
            run = session.get(ComponentCheckRun, run_id)
            if not run:
                raise ValueError("组件检查不存在")
            results = list(session.scalars(select(ComponentCheckResult).where(ComponentCheckResult.check_run_id == run.id)))
            return self._component_check_payload(run, results)

    def latest_component_results(self) -> dict[str, dict[str, Any]]:
        with self.sessions() as session:
            rows = list(session.scalars(select(ComponentCheckResult).where(ComponentCheckResult.checked_at.is_not(None))
                                        .order_by(ComponentCheckResult.checked_at.desc())))
            latest: dict[str, dict[str, Any]] = {}
            for row in rows:
                if row.component in latest:
                    continue
                age = self._observation_age(row.checked_at)
                stale = bool(age is not None and age > 900)
                latest[row.component] = {
                    "status": "unknown" if stale else row.status, "last_status": row.status,
                    "stale": stale, "age_seconds": age, "latency_ms": row.latency_ms,
                    "summary": row.summary, "error_code": row.error_code,
                    "checked_at": row.checked_at.isoformat() if row.checked_at else None,
                    "details": dict(row.details_json or {}),
                }
            return latest

    def close(self) -> None:
        self.engine.dispose()
