"""Transactional V7 persistence service.

All methods operate on V7 models only.  There is intentionally no legacy import or
fallback path here: a V7 deployment starts with freshly uploaded material.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import uuid
from datetime import timedelta
from pathlib import PurePosixPath
from types import SimpleNamespace
from typing import Any, Iterable


from sqlalchemy import create_engine, delete, func, or_, select, tuple_, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from .catalog import CATALOG_SEEDS, OPERATOR_CATEGORIES, builtin_flow_definition, catalog_by_code, subflow_seeds
from .flow import FlowCompiler, FlowValidationError
from .faq import FAQ_COLLECTION_NAME, FAQ_PROFILE_CODE, FAQ_TYPE_CODE
from .graph_literal import detect_literal
from .graph_schema import GraphExtractionConfig, normalize_graph_config, schema_hash
from .llm_serving import get_llm_serving_registry
from .migrations import assert_schema_current
from .models import (
    AdminSession,
    AuditEvent,
    DocumentLibrary,
    DocumentLibraryMember,
    DocumentDeletionJob,
    DocumentLibraryProcessingBaseline,
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
    ManagedCollectionDeletionJob,
    KnowledgeItem,
    KnowledgeItemSource,
    KnowledgeChunkGeneration,
    KnowledgeJob,
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
FIXED_KNOWLEDGE_ASSET_TYPES = (
    {"key": "text", "knowledge_type": "text", "graph_mode": None, "label": "文本知识", "icon": "文"},
    {"key": "qa", "knowledge_type": "qa", "graph_mode": None, "label": "问答知识", "icon": "问"},
    {"key": "graph:triple", "knowledge_type": "graph", "graph_mode": "triple", "label": "三元组图谱", "icon": "△"},
    {"key": "graph:semantic", "knowledge_type": "graph", "graph_mode": "semantic", "label": "语义图谱", "icon": "⬡"},
)
V7_TEMPLATE_SEEDS = (
    ("standard-text", "文本知识流程", ["text"]),
    ("standard-qa", "问答知识流程", ["qa"]),
    ("standard-graph-triple", "三元组图谱流程", ["graph:triple"]),
    ("standard-graph-semantic", "语义图谱流程", ["graph:semantic"]),
    ("standard-multi", "多产出知识流程", ["text", "qa", "graph"]),
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
GRAPH_NEIGHBOR_NOTICE_THRESHOLD = 100
GRAPH_NEIGHBOR_CONFIRM_THRESHOLD = 500
LINEAR_TEMPLATE_STEPS = ("validate", "parse", "normalize", "structure_recovery", "semantic_chunks", "generate")  # legacy API input only

QA_AGENT_TEST_MILVUS_URL = os.environ.get("DATAFORGE_QA_AGENT_TEST_MILVUS_URL") or "http://milvus-central-test:19531"
QA_AGENT_PRODUCTION_MILVUS_URL = os.environ.get("DATAFORGE_QA_AGENT_PRODUCTION_MILVUS_URL") or "http://milvus-central-production:19531"
CENTRAL_DEPLOYMENT_CODE = "dataforge-central"
CENTRAL_DEPLOYMENT_ID = "deployment_dataforge_central"
CENTRAL_STAGE_TARGETS = {
    "test": ("milvus_dataforge_central_test", "DataForge 中心测试 Milvus", QA_AGENT_TEST_MILVUS_URL),
    "production": ("milvus_dataforge_central_production", "DataForge 中心生产 Milvus", QA_AGENT_PRODUCTION_MILVUS_URL),
}


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
                elif template.name == V7_TEMPLATE_LEGACY_NAMES[code]:
                    template.name = name
                if not session.scalar(select(KnowledgeFlowTemplateRevision).where(KnowledgeFlowTemplateRevision.knowledge_flow_template_id == template.id)):
                    session.add(KnowledgeFlowTemplateRevision(
                        id=new_id("flowrev"), knowledge_flow_template_id=template.id, revision_no=1,
                        definition_json=template.definition_json, status="published", published_at=utc_now(),
                    ))
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
                        fields_json=dict(field_mapping), origin="builtin", status="active",
                    )
                    session.add(index_profile)
                    session.flush()
                    revision = KnowledgeIndexProfileRevision(
                        id=f"indexrev_{code}_1", knowledge_index_profile_id=index_profile.id, revision_no=1,
                        collection_name=collection, embedding_profile_id=profile_id,
                        fields_json=dict(field_mapping), status="published", published_at=utc_now(),
                    )
                    session.add(revision)
                    index_profile.current_revision_id = revision.id
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
            session.flush()
            for template in session.scalars(select(KnowledgeFlowTemplate)):
                revision = session.scalar(select(KnowledgeFlowTemplateRevision).where(
                    KnowledgeFlowTemplateRevision.knowledge_flow_template_id == template.id,
                    KnowledgeFlowTemplateRevision.status == "published",
                ).order_by(KnowledgeFlowTemplateRevision.revision_no.desc()))
                if revision and not revision.execution_snapshot_id:
                    self._create_execution_snapshot(session, revision, template.output_types)

    def _ensure_central_deployment(self, session: Session) -> Deployment:
        deployment = session.scalar(select(Deployment).where(Deployment.code == CENTRAL_DEPLOYMENT_CODE))
        if not deployment:
            deployment = Deployment(
                id=CENTRAL_DEPLOYMENT_ID, code=CENTRAL_DEPLOYMENT_CODE,
                name="DataForge 中心环境", scope="central", release_stage="test", status="active",
            )
            session.add(deployment); session.flush()
        for stage, (target_id, target_name, target_url) in CENTRAL_STAGE_TARGETS.items():
            target = session.get(MilvusTarget, target_id)
            if not target:
                target = MilvusTarget(id=target_id, name=target_name, milvus_url=target_url)
                session.add(target); session.flush()
            else:
                target.name, target.milvus_url = target_name, target_url
            link = session.scalar(select(DeploymentTarget).where(
                DeploymentTarget.deployment_id == deployment.id,
                DeploymentTarget.release_stage == stage,
                DeploymentTarget.target_kind == "milvus",
            ))
            if not link:
                session.add(DeploymentTarget(
                    id=f"deployment_target_dataforge_central_{stage}",
                    deployment_id=deployment.id, release_stage=stage,
                    target_kind="milvus", milvus_target_id=target.id,
                ))
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
        for item in CATALOG_SEEDS:
            definition = session.scalar(select(OperatorDefinition).where(OperatorDefinition.code == item["code"]))
            if not definition:
                definition = OperatorDefinition(id=f"op_{item['code'].replace('-', '_')}", code=item["code"], name=item["name"], description=item["description"], category=item["category"], exposure=item["exposure"], risk_level=item["risk_level"], enabled=item["exposure"] != "disabled")
                session.add(definition); session.flush()
            definition.name, definition.description, definition.category = item["name"], item["description"], item["category"]
            definition.display_name_zh, definition.subcategory = item["display_name_zh"], item["subcategory"]
            definition.summary, definition.scenarios = item["summary"], item["scenarios"]
            definition.knowledge_types = item["knowledge_types"]
            definition.recommended_predecessors, definition.recommended_successors = item["recommended_predecessors"], item["recommended_successors"]
            definition.lifecycle_status = item["lifecycle_status"]
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
            version.parameter_schema, version.parameter_docs, version.runtime_requirements = item["parameter_schema"], item["parameter_docs"], item["runtime_requirements"]
            missing = self._operator_publication_errors(definition, version)
            if missing:
                raise RuntimeError(f"算子 {definition.code} 发布元数据不完整：{', '.join(missing)}")
            definition.latest_version = max(definition.latest_version or 0, version_no)
        for item in subflow_seeds():
            subflow = session.scalar(select(FlowSubgraph).where(FlowSubgraph.code == item["code"]))
            if not subflow:
                subflow = FlowSubgraph(id=f"subflow_{item['code'].replace('-', '_')}", code=item["code"], name=item["name"], status="active")
                session.add(subflow); session.flush()
            if not session.scalar(select(FlowSubgraphRevision).where(FlowSubgraphRevision.flow_subgraph_id == subflow.id, FlowSubgraphRevision.revision_no == 1)):
                definition_json = {**item["definition"], "_subgraph_code": item["code"], "_subgraph_revision": 1}
                session.add(FlowSubgraphRevision(id=new_id("subflowrev"), flow_subgraph_id=subflow.id, revision_no=1, definition_json=definition_json,
                                                description=item.get("description", ""), status="published", published_at=utc_now()))

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
                             owner_knowledge_type_id: str | None = None) -> dict[str, Any]:
        if collection_mode is not None:
            if collection_mode not in {"create", "attach"}:
                raise ValueError("Collection 模式必须为 create 或 attach")
            collection_policy = "managed" if collection_mode == "create" else "external"
        code, collection_name, embedding_code = code.strip(), collection_name.strip(), embedding_code.strip()
        if not code or not embedding_code or collection_policy not in {"external", "managed"}:
            raise ValueError("Index Profile、Embedding 编码和 Collection 策略必须有效")
        if origin not in {"builtin", "extension_auto", "manual"}:
            raise ValueError("Index Profile 来源无效")
        if collection_policy == "external" and not collection_name:
            raise ValueError("外部 Index Profile 必须指定 Collection")
        if collection_policy == "external" and reuse_managed_collection_id:
            raise ValueError("external Profile 不能复用受管 Collection 登记")
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
            managed = None
            if collection_policy == "managed":
                storage_revision, managed = self._managed_storage_contract(
                    session, code, collection_name, embedding, storage_schema, fields, index_spec,
                    reuse_managed_collection_id=reuse_managed_collection_id,
                )
                collection_name = managed.collection_name
            item = KnowledgeIndexProfile(id=new_id("index"), code=code, knowledge_type=knowledge_type.strip(), collection_name=collection_name,
                                         embedding_profile_id=embedding.id, fields_json=dict(fields), origin=origin,
                                         owner_knowledge_type_id=owner_knowledge_type_id, status="draft")
            session.add(item); session.flush()
            revision = KnowledgeIndexProfileRevision(id=new_id("indexrev"), knowledge_index_profile_id=item.id, revision_no=1,
                                                    collection_name=collection_name, embedding_profile_id=embedding.id, fields_json=dict(fields),
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
                             reuse_managed_collection_id: str | None = None) -> dict[str, Any]:
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
                                                    collection_name=collection_name.strip(), embedding_profile_id=embedding.id, fields_json=dict(fields),
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

    def list_operator_catalog(self, *, include_internal: bool = False, query: str = "", category: str = "",
                              knowledge_type: str = "", exposure: str = "", status: str = "") -> list[dict[str, Any]]:
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
                value = {"id": definition.id, "code": definition.code, "name": definition.name, "display_name_zh": definition.display_name_zh,
                               "summary": definition.summary, "description": definition.description, "category": definition.category, "subcategory": definition.subcategory,
                               "exposure": definition.exposure, "risk_level": definition.risk_level, "enabled": definition.enabled,
                               "scenarios": definition.scenarios, "knowledge_types": definition.knowledge_types,
                               "recommended_predecessors": definition.recommended_predecessors, "recommended_successors": definition.recommended_successors,
                               "status": definition.lifecycle_status,
                               "version": version.version_no, "adapter_code": version.adapter_code,
                               "input_ports": version.input_ports, "output_ports": version.output_ports,
                               "input_example": version.input_example, "output_example": version.output_example,
                               "parameter_schema": version.parameter_schema, "parameter_docs": version.parameter_docs}
                searchable = " ".join((definition.code, definition.name, definition.display_name_zh, definition.summary,
                                       definition.description, json.dumps(version.input_ports, ensure_ascii=False),
                                       json.dumps(version.output_ports, ensure_ascii=False))).lower()
                if query and query.lower() not in searchable: continue
                if category and definition.category != category: continue
                if knowledge_type and knowledge_type not in (definition.knowledge_types or []): continue
                if exposure and definition.exposure != exposure: continue
                if status and definition.lifecycle_status != status: continue
                values.append(value)
            return values

    def operator_catalog_facets(self) -> dict[str, Any]:
        values = self.list_operator_catalog(include_internal=True)
        return {
            "total": len(values),
            "categories": [{"name": name, "count": sum(item["category"] == name for item in values)} for name in OPERATOR_CATEGORIES],
            "knowledge_types": sorted({kind for item in values for kind in item.get("knowledge_types", [])}),
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
        with self.sessions.begin() as session:
            definition = session.scalar(select(OperatorDefinition).where(OperatorDefinition.code == code))
            version = session.scalar(select(OperatorVersion).where(OperatorVersion.operator_definition_id == definition.id,
                                                                    OperatorVersion.version_no == version_no)) if definition else None
            if not definition or not version: raise ValueError("算子版本不存在")
            missing = self._operator_publication_errors(definition, version)
            if missing: raise ValueError(f"算子发布元数据不完整：{', '.join(missing)}")
            version.status, version.published_at = "published", utc_now()
            return {"code": code, "version": version_no, "status": "published"}

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
                revision = session.scalar(select(FlowSubgraphRevision).where(FlowSubgraphRevision.flow_subgraph_id == item.id,
                                                                             FlowSubgraphRevision.status == "published")
                                          .order_by(FlowSubgraphRevision.revision_no.desc()))
                definition = revision.definition_json if revision else None
                values.append({"id": item.id, "code": item.code, "name": item.name, "status": item.status,
                               "revision": revision.revision_no if revision else None,
                               "revision_status": revision.status if revision else None,
                               "description": revision.description if revision else "",
                               "input_contract": revision.input_contract if revision else {}, "output_contract": revision.output_contract if revision else {},
                               "node_count": len((definition or {}).get("nodes", [])), "edge_count": len((definition or {}).get("edges", [])),
                               "definition": definition})
            return values

    def subflow_revision_detail(self, subflow_id: str, revision_no: int) -> dict[str, Any]:
        with self.sessions() as session:
            item = session.get(FlowSubgraph, subflow_id)
            revision = session.scalar(select(FlowSubgraphRevision).where(FlowSubgraphRevision.flow_subgraph_id == subflow_id,
                                                                         FlowSubgraphRevision.revision_no == revision_no))
            if not item or not revision: raise ValueError("子图修订不存在")
            definition = revision.definition_json or {}
            return {"id": item.id, "code": item.code, "name": item.name, "status": item.status,
                    "revision_id": revision.id, "revision": revision.revision_no, "revision_status": revision.status,
                    "description": revision.description, "input_contract": revision.input_contract, "output_contract": revision.output_contract,
                    "node_count": len(definition.get("nodes", [])), "edge_count": len(definition.get("edges", [])), "definition": definition}

    @staticmethod
    def _validate_subflow_definition(definition: dict[str, Any]) -> None:
        nodes = definition.get("nodes") or []; edges = definition.get("edges") or []
        ids = [str(node.get("id", "")) for node in nodes]
        if not ids or any(not value for value in ids) or len(ids) != len(set(ids)): raise ValueError("子图节点 id 必须存在且唯一")
        if definition.get("entry_node") not in ids or definition.get("exit_node") not in ids: raise ValueError("子图 entry_node/exit_node 必须引用内部节点")
        incoming = {value: 0 for value in ids}; outgoing = {value: [] for value in ids}
        for raw in edges:
            source = str(raw[0] if isinstance(raw, list) else raw.get("source", "")); target = str(raw[1] if isinstance(raw, list) else raw.get("target", ""))
            if source not in incoming or target not in incoming: raise ValueError("子图连线引用了不存在的节点")
            incoming[target] += 1; outgoing[source].append(target)
        queue = [value for value in ids if incoming[value] == 0]; visited = []
        while queue:
            current = queue.pop(0); visited.append(current)
            for target in outgoing[current]:
                incoming[target] -= 1
                if incoming[target] == 0: queue.append(target)
        if len(visited) != len(ids): raise ValueError("子图必须是有向无环图")

    def copy_subflow_draft(self, subflow_id: str, revision_no: int) -> dict[str, Any]:
        with self.sessions.begin() as session:
            item = session.get(FlowSubgraph, subflow_id)
            source = session.scalar(select(FlowSubgraphRevision).where(FlowSubgraphRevision.flow_subgraph_id == subflow_id,
                                                                       FlowSubgraphRevision.revision_no == revision_no))
            if not item or not source: raise ValueError("子图修订不存在")
            latest = session.scalar(select(func.max(FlowSubgraphRevision.revision_no)).where(FlowSubgraphRevision.flow_subgraph_id == subflow_id)) or 0
            revision = FlowSubgraphRevision(id=new_id("subflowrev"), flow_subgraph_id=subflow_id, revision_no=latest + 1,
                                            definition_json=dict(source.definition_json), description=source.description,
                                            input_contract=dict(source.input_contract), output_contract=dict(source.output_contract), status="draft")
            session.add(revision); self.audit(session, "subgraph.draft_created", "flow_subgraph", subflow_id, {"revision": revision.revision_no})
            return {"id": subflow_id, "revision": revision.revision_no, "status": "draft"}

    def update_subflow_draft(self, subflow_id: str, revision_no: int, definition: dict[str, Any], description: str,
                             input_contract: dict[str, Any], output_contract: dict[str, Any]) -> dict[str, Any]:
        self._validate_subflow_definition(definition)
        with self.sessions.begin() as session:
            revision = session.scalar(select(FlowSubgraphRevision).where(FlowSubgraphRevision.flow_subgraph_id == subflow_id,
                                                                         FlowSubgraphRevision.revision_no == revision_no))
            if not revision or revision.status != "draft": raise ValueError("只能修改子图草稿")
            item = session.get(FlowSubgraph, subflow_id); assert item
            revision.definition_json = {**definition, "_subgraph_code": item.code, "_subgraph_revision": revision_no}
            revision.description, revision.input_contract, revision.output_contract = description.strip(), input_contract, output_contract
            return {"id": subflow_id, "revision": revision_no, "status": "draft"}

    def publish_subflow_draft(self, subflow_id: str, revision_no: int) -> dict[str, Any]:
        with self.sessions.begin() as session:
            revision = session.scalar(select(FlowSubgraphRevision).where(FlowSubgraphRevision.flow_subgraph_id == subflow_id,
                                                                         FlowSubgraphRevision.revision_no == revision_no))
            if not revision or revision.status != "draft": raise ValueError("只能发布子图草稿")
            self._validate_subflow_definition(revision.definition_json)
            revision.status, revision.published_at = "published", utc_now()
            self.audit(session, "subgraph.published", "flow_subgraph", subflow_id, {"revision": revision_no})
            return {"id": subflow_id, "revision": revision_no, "status": "published"}

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
                     "parent_flow_run_id": item.parent_flow_run_id, "run_mode": item.run_mode, "start_node_id": item.start_node_id,
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
        return {"id": item.id, "code": item.code, "name": item.name, "description": item.description,
                "status": item.status, "origin_type": item.origin_type, "origin_state": item.origin_state,
                "updated_at": item.updated_at.isoformat()}

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
        for raw_output in template.output_types:
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
        active_versions = list(session.scalars(select(Source.current_version_id).where(
            Source.document_library_id == binding.document_library_id, Source.status == "uploaded",
            Source.current_version_id.is_not(None),
        )))
        processed = set(session.scalars(select(DocumentLibraryProcessingRecord.source_version_id).where(
            DocumentLibraryProcessingRecord.document_library_template_binding_id == binding.id,
            DocumentLibraryProcessingRecord.knowledge_flow_template_revision_id == revision.id,
        )))
        processed.update(session.scalars(select(DocumentLibraryProcessingBaseline.source_version_id).where(
            DocumentLibraryProcessingBaseline.document_library_template_binding_id == binding.id,
            DocumentLibraryProcessingBaseline.knowledge_flow_template_revision_id == revision.id,
            DocumentLibraryProcessingBaseline.last_success_status == "completed",
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
                payload = self._knowledge_library_payload(
                    item, self._library_ready(session, item), item_counts.get(item.id, 0),
                )
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
        if "graph" in {output_contract(value)[0] for value in output_types}:
            config = normalize_graph_config(normalized.get("graph_config"))
            normalized["graph_config"] = config.to_dict()
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
            for item in session.scalars(select(KnowledgeFlowTemplate).where(
                KnowledgeFlowTemplate.status != "archived",
            ).order_by(KnowledgeFlowTemplate.code)):
                revision = session.scalar(select(KnowledgeFlowTemplateRevision).where(
                    KnowledgeFlowTemplateRevision.knowledge_flow_template_id == item.id,
                ).order_by(KnowledgeFlowTemplateRevision.revision_no.desc()))
                values.append({"id": item.id, "code": item.code, "name": item.name,
                               "is_builtin": item.code in V7_BUILTIN_TEMPLATE_CODES, "output_types": item.output_types,
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
            session.add(job); self.audit(session, "knowledge_job.created", "knowledge_job", job.id, {"outputs": normalized_outputs})
            session.flush()
            return self.job_payload(job)

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
                graph_config: dict[str, Any] | None = None
                graph_schema_hash: str | None = None
                if library.knowledge_type == "graph":
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
                    "knowledge_type": library.knowledge_type, "graph_mode": library.graph_mode,
                    "prompt": prompt_body, "library_id": library.id,
                    "graph_config": graph_config, "graph_schema_hash": graph_schema_hash,
                    "template_revision_id": job.knowledge_flow_template_revision_id,
                }
            return values

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
            session.flush(); self._append_run_event(session, run.id, "run.started", "Flow Run 已开始")
            self.audit(session, "flow_run.started", "flow_run", run.id, {"knowledge_job_id": job.id})
            return {"id": run.id, "execution_snapshot_id": run.execution_snapshot_id, "status": run.status}

    def record_flow_node(self, flow_run_id: str, node_id: str, input_artifact_ids: list[str], outputs: list[dict[str, Any]], *, error: str | None = None,
                         operator_code: str | None = None, operator_version: int | None = None, resolved_parameters: dict[str, Any] | None = None,
                         status: str | None = None, logs: list[dict[str, Any]] | None = None, metrics: dict[str, Any] | None = None) -> list[str]:
        """Persist execution-only artifacts and their lineage; never use them as formal provenance."""
        with self.sessions.begin() as session:
            started = utc_now()
            node_run = FlowNodeRun(id=new_id("noderun"), flow_run_id=flow_run_id, node_id=node_id, operator_code=operator_code,
                                   operator_version=operator_version, resolved_parameters=resolved_parameters or {}, logs_json=logs or [], metrics_json=metrics or {},
                                   error_json={"message": error} if error else {}, started_at=started, finished_at=started, duration_ms=0,
                                   status=status or ("failed" if error else "completed"), input_artifact_ids=list(input_artifact_ids), error=error)
            session.add(node_run); session.flush()
            for ordinal, artifact_id in enumerate(input_artifact_ids):
                session.add(FlowNodeArtifactBinding(id=new_id("binding"), flow_node_run_id=node_run.id, artifact_id=artifact_id,
                                                    direction="input", port_name="input", ordinal=ordinal,
                                                    reused=bool(session.get(Artifact, artifact_id) and session.get(Artifact, artifact_id).flow_run_id != flow_run_id)))
            output_ids: list[str] = []
            for ordinal, value in enumerate(outputs):
                data = dict(value) if isinstance(value, dict) else {"value": value}
                parser_artifacts = list(data.pop("_parser_artifacts", []))
                type_code = str(data.pop("_artifact_type", "execution"))
                encoded = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
                artifact = Artifact(id=new_id("artifact"), flow_run_id=flow_run_id, flow_node_run_id=node_run.id,
                                    type_code=type_code, data_json=data, checksum=hashlib.sha256(encoded).hexdigest(),
                                    summary_json={"keys": sorted(data)[:20], "bytes": len(encoded)}, record_count=1, replayable=True)
                session.add(artifact); session.flush(); output_ids.append(artifact.id)
                session.add(FlowNodeArtifactBinding(id=new_id("binding"), flow_node_run_id=node_run.id, artifact_id=artifact.id,
                                                    direction="output", port_name="output", ordinal=ordinal))
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
            self._append_run_event(session, flow_run_id, f"node.{node_run.status}", f"节点 {node_id} {node_run.status}", node_id=node_id,
                                   payload={"input_count": len(input_artifact_ids), "output_count": len(output_ids)})
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
            nodes = session.scalars(select(FlowNodeRun).where(FlowNodeRun.flow_run_id == run.id).order_by(FlowNodeRun.created_at)).all()
            artifacts = session.scalars(select(Artifact).where(Artifact.flow_run_id == run.id).order_by(Artifact.created_at)).all()
            previews = session.scalars(select(FlowRunSinkPreview).where(FlowRunSinkPreview.flow_run_id == run.id).order_by(FlowRunSinkPreview.created_at)).all()
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
                    artifact_by_id.update({item.id: item for item in session.scalars(select(Artifact).where(Artifact.id.in_(reused_artifact_ids))).all()})
            runtime_nodes = []
            for definition_node in definition.get("nodes", []):
                node = latest.get(str(definition_node["id"]))
                runtime_nodes.append({"id": definition_node["id"], "kind": definition_node.get("kind"), "ref": definition_node.get("ref"),
                                      "params": definition_node.get("params") or {}, "origin_path": definition_node.get("origin_path") or str(definition_node["id"]).split("::"),
                                      "source_subgraph": definition_node.get("source_subgraph"), "status": node.status if node else "reused" if str(definition_node["id"]) in reused_ids else "skipped",
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
            return {"id": run.id, "knowledge_job_id": run.knowledge_job_id, "execution_snapshot_id": run.execution_snapshot_id,
                    "parent_flow_run_id": run.parent_flow_run_id, "run_mode": run.run_mode, "start_node_id": run.start_node_id,
                    "parameter_overrides": run.parameter_overrides, "sink_policy": run.sink_policy,
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
        properties = dict(schema.get("properties") or {})
        unknown = set(value) - set(properties)
        if schema.get("additionalProperties") is False and unknown:
            return f"不允许参数 {sorted(unknown)[0]}"
        for key in schema.get("required") or []:
            if key not in value: return f"缺少必填参数 {key}"
        python_types = {"string": str, "integer": int, "number": (int, float), "boolean": bool, "array": list, "object": dict}
        for key, item in value.items():
            spec = properties.get(key) or {}
            expected = python_types.get(spec.get("type"))
            if expected and not isinstance(item, expected): return f"参数 {key} 类型不符合 {spec['type']}"
            if spec.get("enum") and item not in spec["enum"]: return f"参数 {key} 不在允许值范围内"
            if key == "llm_serving":
                try:
                    get_llm_serving_registry().require(str(item))
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
            overrides = dict(parameter_overrides or {})
            unknown = set(overrides) - {node_id}
            if unknown: raise ValueError("参数覆盖只能包含所选节点")
            resolved = {**dict(node.get("params") or {}), **dict(overrides.get(node_id) or {})}
            definition_row = session.scalar(select(OperatorDefinition).where(OperatorDefinition.code == node.get("ref")))
            version_row = session.scalar(select(OperatorVersion).where(OperatorVersion.operator_definition_id == definition_row.id,
                                                                        OperatorVersion.version_no == int(node.get("version") or definition_row.latest_version or 1))) if definition_row else None
            if not definition_row or not definition_row.enabled or not version_row or version_row.status != "published": raise ValueError("算子版本已不可执行")
            validated_parameters = {key: value for key, value in dict(overrides.get(node_id) or {}).items() if key != "force_ocr"}
            validation_error = self._validate_parameter_override(version_row.parameter_schema or {"type": "object"}, validated_parameters)
            if validation_error: raise ValueError(f"参数覆盖不符合 Operator Version Schema：{validation_error}")
            if resolved.get("force_ocr") and node.get("ref") != "document-parser": raise ValueError("force_ocr 仅适用于 Document Parser")
            if resolved.get("force_ocr"):
                job = session.get(KnowledgeJob, parent.knowledge_job_id); assert job
                sources = session.execute(select(Source.original_filename).join(SourceVersion, SourceVersion.source_id == Source.id)
                                          .where(SourceVersion.id.in_(job.source_version_ids))).scalars().all()
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
            run = FlowRun(id=new_id("flowrun"), knowledge_job_id=parent.knowledge_job_id, execution_snapshot_id=parent.execution_snapshot_id,
                          parent_flow_run_id=parent.id, run_mode=mode, start_node_id=node_id, parameter_overrides=overrides,
                          sink_policy="preview", requested_by="admin", idempotency_key=idempotency_key, status="queued")
            session.add(run); session.flush(); self._append_run_event(session, run.id, "run.queued", "派生 Run 已进入队列", payload={"parent": parent.id, "mode": mode, "node": node_id})
            self.audit(session, "flow_run.derived_created", "flow_run", run.id, {"parent": parent.id, "mode": mode, "node": node_id})
            return {"id": run.id, "parent_flow_run_id": parent.id, "execution_snapshot_id": run.execution_snapshot_id, "run_mode": mode, "start_node_id": node_id, "status": "queued"}

    def claim_derived_run(self, owner: str) -> FlowRun | None:
        with self.sessions.begin() as session:
            run = session.scalar(select(FlowRun).where(FlowRun.status == "queued", FlowRun.parent_flow_run_id.is_not(None))
                                 .order_by(FlowRun.created_at).with_for_update(skip_locked=True).limit(1))
            if not run: return None
            run.status = "running"; self._append_run_event(session, run.id, "run.started", f"派生 Run 由 {owner} 开始执行")
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
                                                                    OperatorVersion.version_no == int(node.get("version") or definition.latest_version or 1))) if definition else None
            if not version: raise ValueError("算子版本不存在")
            validation_error = self._validate_parameter_override(version.parameter_schema or {"type": "object"}, parameters)
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

    def stage_sink_preview(self, flow_run_id: str, output_key: str, library_id: str, candidates: list[dict[str, Any]],
                           successful_chunks: list[dict[str, Any]], quality: dict[str, Any] | None = None) -> dict[str, Any]:
        with self.sessions.begin() as session:
            base_hash = self._library_state_hash(session, library_id)
            current_ids = set(session.scalars(select(KnowledgeItem.source_knowledge_id).where(KnowledgeItem.knowledge_library_id == library_id,
                                                                                               KnowledgeItem.status == "active")))
            candidate_ids = {str(item.get("source_knowledge_id")) for item in candidates}
            diff = {"ADD": len(candidate_ids - current_ids), "UPDATE": len(candidate_ids & current_ids), "INACTIVE": 0, "UNCHANGED": 0}
            checksum = hashlib.sha256(json.dumps({"base": base_hash, "candidates": candidates, "chunks": successful_chunks}, ensure_ascii=False,
                                                 sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
            preview = FlowRunSinkPreview(id=new_id("preview"), flow_run_id=flow_run_id, output_key=output_key, knowledge_library_id=library_id,
                                         candidates_json=candidates, successful_chunks_json=successful_chunks, diff_json=diff,
                                         quality_json=quality or {"candidate_count": len(candidates), "status": "pass"},
                                         base_state_hash=base_hash, preview_checksum=checksum)
            session.add(preview); self._append_run_event(session, flow_run_id, "sink.preview_ready", f"{output_key} Diff 已暂存", payload={"diff": diff, "checksum": checksum})
            return {"output_key": output_key, "diff": diff, "preview_checksum": checksum}

    def commit_derived_run(self, flow_run_id: str, preview_checksum: str, idempotency_key: str) -> dict[str, Any]:
        with self.sessions.begin() as session:
            run = session.get(FlowRun, flow_run_id)
            if not run or run.status not in {"awaiting_commit", "completed"}: raise ValueError("Flow Run 没有可提交的 Sink 预览")
            previews = session.scalars(select(FlowRunSinkPreview).where(FlowRunSinkPreview.flow_run_id == flow_run_id)).all()
            preview = next((item for item in previews if item.preview_checksum == preview_checksum), None)
            if not preview: raise ValueError("预览 checksum 不匹配")
            if preview.status == "committed":
                if preview.idempotency_key == idempotency_key: return {"id": run.id, "status": run.status, "idempotent": True}
                raise ValueError("该 Sink 预览已经提交")
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
            FlowRunSinkPreview, FlowRunEvent, FlowNodeArtifactBinding, ArtifactLineage, Artifact, FlowNodeRun, SourceChunk, DocumentIR, FlowRun, FlowExecutionSnapshot, KnowledgeChunkGeneration,
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
                config = normalize_graph_config((definition or {}).get("graph_config"))
                library.graph_schema_snapshot_json = config.to_dict()
                library.graph_schema_hash = schema_hash(config)
                library.source_template_revision_id = job.knowledge_flow_template_revision_id
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
                    session.add(item)
                    # A logical graph relation may be emitted by several source
                    # chunks in the same Sink batch.  Reuse the pending item so
                    # later candidates add Evidence instead of scheduling a
                    # second row with the same library/identity unique key.
                    current[key] = item
                    change = "ADD"; before = None
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

    def list_qa_pairs(self, library_id: str, *, keyword: str = "", status: str = "active",
                      page: int = 1, page_size: int = 50) -> dict[str, Any]:
        page, page_size = max(1, page), min(max(1, page_size), 200)
        if status not in {"active", "inactive", "all"}:
            raise ValueError("问答知识状态筛选无效")
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

            total = int(session.scalar(select(func.count()).select_from(query.subquery())) or 0)
            rows = session.execute(
                query.order_by(KnowledgeItem.updated_at.desc(), KnowledgeItem.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            ).all()
            return {
                "items": [{
                    "id": item.id,
                    "source_knowledge_id": item.source_knowledge_id,
                    "canonical_content": item.canonical_content,
                    "data": item.data_json,
                    "content_hash": item.content_hash,
                    "status": item.status,
                    "source_count": int(source_count or 0),
                    "updated_at": item.updated_at.isoformat(),
                } for item, source_count in rows],
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
                     "source": {"id": source.id, "name": source.name, "original_filename": source.original_filename},
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

    def create_vector_sync_jobs(self, library_id: str) -> list[dict[str, Any]]:
        with self.sessions.begin() as session:
            library = session.get(KnowledgeLibrary, library_id)
            if not library:
                raise ValueError("知识库不存在")
            profiles = self._index_profile_snapshots_for_library(session, library)
            count = session.scalar(select(func.count()).select_from(KnowledgeItem).where(KnowledgeItem.knowledge_library_id == library.id, KnowledgeItem.status == "active")) or 0
            next_version = int(session.scalar(select(func.max(KnowledgeAssetVersion.version_no)).where(
                KnowledgeAssetVersion.knowledge_library_id == library.id,
            )) or 0)
            jobs: list[VectorSyncJob] = []
            assets: list[KnowledgeAssetVersion] = []
            for profile in profiles:
                next_version += 1
                asset = KnowledgeAssetVersion(
                    id=new_id("kav"), knowledge_library_id=library.id, version_no=next_version,
                    index_profile_id=profile.id, index_profile_revision_id=profile.revision_id,
                    storage_contract_revision_id=profile.storage_contract_revision_id,
                    collection_name=profile.collection_name,
                    partition_name=f"{library.partition_name}__v{next_version}", status="building",
                )
                job = VectorSyncJob(
                    id=new_id("vsj"), knowledge_library_id=library.id,
                    index_profile_id=profile.id, total_count=count, asset_version_id=asset.id,
                )
                session.add(asset); session.add(job); assets.append(asset); jobs.append(job)
            self.audit(session, "vector_sync.queued", "knowledge_library", library.id, {
                "jobs": [item.id for item in jobs], "asset_versions": [item.id for item in assets],
            })
            return [{
                "id": item.id, "knowledge_library_id": item.knowledge_library_id,
                "index_profile_id": item.index_profile_id, "asset_version_id": item.asset_version_id,
                "status": item.status, "total_count": item.total_count, "attempt_count": item.attempt_count,
            } for item in jobs]

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
            asset = session.get(KnowledgeAssetVersion, job.asset_version_id) if job.asset_version_id else None
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
                    asset.status, asset.error = "building", error
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
                "collection_name": item.collection_name, "partition_name": item.partition_name,
                "status": item.status, "item_count": item.item_count,
                "content_digest": item.content_digest,
                "ready_at": item.ready_at.isoformat() if item.ready_at else None,
            } for item in session.scalars(select(KnowledgeAssetVersion).where(
                KnowledgeAssetVersion.knowledge_library_id == library_id,
            ).order_by(KnowledgeAssetVersion.version_no.desc()))]
            return {"knowledge_library_id": library_id, "ready": self._library_ready(session, library),
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
                    elif asset.status == "delete_pending":
                        asset.status, asset.error = "ready", error
                return {"id": job.id, "status": job.status, "error": error}
            for asset_id in deleted_ids:
                asset = session.get(KnowledgeAssetVersion, asset_id)
                if asset:
                    asset.status = "deleted"
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
                values.append({"id": item.id, "code": item.code, "knowledge_type": item.knowledge_type,
                    "collection_name": item.collection_name, "embedding_profile_id": item.embedding_profile_id,
                    "fields": item.fields_json, "origin": item.origin,
                    "owner_knowledge_type_id": item.owner_knowledge_type_id,
                    "status": item.status, "current_revision_id": item.current_revision_id,
                    "managed_collection_id": managed.id if managed else None,
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
    def _target_payload(target: MilvusTarget) -> dict[str, Any]:
        return {"id": target.id, "name": target.name, "milvus_url": target.milvus_url,
                "created_at": target.created_at.isoformat(), "updated_at": target.updated_at.isoformat()}

    @staticmethod
    def _shared_deployment_payload(deployment: Deployment, targets: dict[str, MilvusTarget] | None = None) -> dict[str, Any]:
        result = {
            "id": deployment.id, "code": deployment.code, "name": deployment.name,
            "scope": deployment.scope, "institution_name": deployment.institution_name,
            "institution_code": deployment.institution_code,
            "institution_code_locked": bool(deployment.institution_code_locked_at),
            "institution_code_locked_at": deployment.institution_code_locked_at.isoformat()
                if deployment.institution_code_locked_at else None,
            "release_stage": deployment.release_stage, "status": deployment.status,
            "created_at": deployment.created_at.isoformat(), "updated_at": deployment.updated_at.isoformat(),
        }
        result["stage_targets"] = {
            stage: V7Store._target_payload(target) for stage, target in sorted((targets or {}).items())
        }
        current = (targets or {}).get(deployment.release_stage)
        if current:
            result["milvus_target_id"] = current.id
            result["milvus_target"] = V7Store._target_payload(current)
        return result

    @staticmethod
    def _deployment_payload(binding: ProjectDeployment, deployment: Deployment,
                            targets: dict[str, MilvusTarget] | None = None) -> dict[str, Any]:
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
    def _deployment_targets(session: Session, deployment_id: str) -> dict[str, MilvusTarget]:
        rows = session.execute(select(DeploymentTarget, MilvusTarget).join(
            MilvusTarget, MilvusTarget.id == DeploymentTarget.milvus_target_id,
        ).where(
            DeploymentTarget.deployment_id == deployment_id,
            DeploymentTarget.target_kind == "milvus",
        )).all()
        return {link.release_stage: target for link, target in rows}

    def list_milvus_targets(self) -> list[dict[str, Any]]:
        with self.sessions() as session:
            return [self._target_payload(item) for item in session.scalars(select(MilvusTarget).order_by(MilvusTarget.name))]

    def create_milvus_target(self, name: str, milvus_url: str) -> dict[str, Any]:
        if not name.strip():
            raise ValueError("Milvus Target 名称不能为空")
        with self.sessions.begin() as session:
            value = MilvusTarget(id=new_id("mt"), name=name.strip(), milvus_url=milvus_url.strip())
            session.add(value); self.audit(session, "milvus_target.created", "milvus_target", value.id)
            session.flush(); return self._target_payload(value)

    def patch_milvus_target(self, target_id: str, *, name: str | None = None,
                            milvus_url: str | None = None) -> dict[str, Any]:
        with self.sessions.begin() as session:
            value = session.get(MilvusTarget, target_id)
            if not value:
                raise ValueError("Milvus Target 不存在")
            if name is not None:
                if not name.strip(): raise ValueError("Milvus Target 名称不能为空")
                value.name = name.strip()
            if milvus_url is not None: value.milvus_url = milvus_url.strip()
            session.flush(); return self._target_payload(value)

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
    def _stage_target(session: Session, deployment_id: str, release_stage: str) -> MilvusTarget:
        if release_stage not in {"test", "production"}:
            raise ValueError("release_stage 只允许 test 或 production")
        target = session.scalar(select(MilvusTarget).join(
            DeploymentTarget, DeploymentTarget.milvus_target_id == MilvusTarget.id,
        ).where(
            DeploymentTarget.deployment_id == deployment_id,
            DeploymentTarget.release_stage == release_stage,
            DeploymentTarget.target_kind == "milvus",
        ))
        if not target:
            raise ValueError(f"Deployment 尚未配置 {release_stage} Milvus Target")
        return target

    @staticmethod
    def _put_stage_target(session: Session, deployment: Deployment, release_stage: str,
                          milvus_url: str, *, name: str | None = None) -> MilvusTarget:
        if release_stage not in {"test", "production"}:
            raise ValueError("release_stage 只允许 test 或 production")
        normalized_uri = str(milvus_url or "").strip()
        if not normalized_uri:
            raise ValueError("Milvus Target URI 不能为空")
        link = session.scalar(select(DeploymentTarget).where(
            DeploymentTarget.deployment_id == deployment.id,
            DeploymentTarget.release_stage == release_stage,
            DeploymentTarget.target_kind == "milvus",
        ))
        target = session.get(MilvusTarget, link.milvus_target_id) if link else None
        if not target:
            target = MilvusTarget(
                id=new_id("mt"), name=(name or f"{deployment.name} · {release_stage} Milvus").strip(),
                milvus_url=normalized_uri,
            )
            session.add(target); session.flush()
        else:
            target.name = (name or target.name).strip()
            target.milvus_url = normalized_uri
        if not link:
            session.add(DeploymentTarget(
                id=new_id("dtarget"), deployment_id=deployment.id, release_stage=release_stage,
                target_kind="milvus", milvus_target_id=target.id,
            ))
        session.flush()
        return target

    def create_shared_deployment(self, code: str, name: str, *, scope: str = "institution",
                                 institution_name: str | None = None,
                                 institution_code: str | None = None,
                                 test_milvus_uri: str = QA_AGENT_TEST_MILVUS_URL) -> dict[str, Any]:
        normalized_code = str(code or "").strip()
        normalized_name = str(name or "").strip()
        normalized_institution = str(institution_name or "").strip() or None
        normalized_institution_code = str(institution_code or "").strip() or None
        if not normalized_code or not normalized_name:
            raise ValueError("Deployment 编码和名称不能为空")
        if scope not in {"central", "institution"}:
            raise ValueError("Deployment scope 只允许 central 或 institution")
        if scope == "institution" and (not normalized_institution or not normalized_institution_code):
            raise ValueError("医院 Deployment 必须填写医院机构名称和机构代码")
        with self.sessions.begin() as session:
            if session.scalar(select(Deployment).where(Deployment.code == normalized_code)):
                raise ValueError("Deployment 编码已存在")
            if normalized_institution_code and session.scalar(select(Deployment).where(
                    Deployment.institution_code == normalized_institution_code)):
                raise ValueError("该医院机构代码已有 Deployment")
            deployment = Deployment(
                id=new_id("deployment"), code=normalized_code, name=normalized_name, scope=scope,
                institution_name=normalized_institution, institution_code=normalized_institution_code,
                release_stage="test", status="active",
            )
            session.add(deployment); session.flush()
            self._put_stage_target(session, deployment, "test", test_milvus_uri)
            if normalized_code == CENTRAL_DEPLOYMENT_CODE:
                self._put_stage_target(session, deployment, "production", QA_AGENT_PRODUCTION_MILVUS_URL)
            self.audit(session, "deployment.created", "deployment", deployment.id)
            session.flush()
            return self._shared_deployment_payload(deployment, self._deployment_targets(session, deployment.id))

    def put_deployment_target(self, deployment_id: str, release_stage: str, milvus_uri: str, *,
                              confirm_production: bool = False,
                              expected_target_uri: str | None = None) -> dict[str, Any]:
        with self.sessions.begin() as session:
            deployment = session.get(Deployment, deployment_id)
            if not deployment:
                raise ValueError("Deployment 不存在")
            normalized_uri = str(milvus_uri or "").strip()
            if release_stage == "production" and (
                    confirm_production is not True or expected_target_uri != normalized_uri):
                raise ValueError("配置生产 Target 必须确认完整的医院生产 URI")
            target = self._put_stage_target(session, deployment, release_stage, normalized_uri)
            self.audit(session, "deployment.target_updated", "deployment", deployment.id,
                       {"release_stage": release_stage, "milvus_url": normalized_uri})
            return {"deployment_id": deployment.id, "release_stage": release_stage,
                    "target_kind": "milvus", "milvus_target": self._target_payload(target)}

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

    def create_deployment(self, project_id: str, code: str, name: str, deployment_type: str,
                          milvus_target_id: str, *, institution_name: str | None = None,
                          institution_code: str | None = None,
                          release_stage: str = "test") -> dict[str, Any]:
        if release_stage != "test":
            raise ValueError("新 Deployment 必须先以 test 阶段创建，再由管理员手工切换生产")
        test_uri = QA_AGENT_TEST_MILVUS_URL
        with self.sessions() as session:
            requested_target = session.get(MilvusTarget, milvus_target_id) if milvus_target_id else None
            if requested_target and requested_target.milvus_url:
                test_uri = requested_target.milvus_url
        shared = self.create_shared_deployment(
            code, name, scope="institution" if institution_code or institution_name else "central",
            institution_name=institution_name, institution_code=institution_code,
            test_milvus_uri=test_uri,
        )
        return self.bind_project_deployment(shared["id"], project_id)

    def patch_deployment(self, deployment_id: str, **changes: Any) -> dict[str, Any]:
        with self.sessions() as session:
            binding = session.get(ProjectDeployment, deployment_id)
            if not binding:
                raise ValueError("ProjectDeployment 不存在")
            shared_id = binding.deployment_id
        self.patch_shared_deployment(shared_id, **changes)
        with self.sessions() as session:
            binding = session.get(ProjectDeployment, deployment_id)
            deployment = session.get(Deployment, shared_id)
            return self._deployment_payload(binding, deployment, self._deployment_targets(session, shared_id))

    def patch_shared_deployment(self, deployment_id: str, **changes: Any) -> dict[str, Any]:
        with self.sessions.begin() as session:
            value = session.get(Deployment, deployment_id)
            if not value:
                raise ValueError("Deployment 不存在")
            if "name" in changes and changes["name"] is not None:
                normalized_name = str(changes["name"]).strip()
                if not normalized_name:
                    raise ValueError("Deployment 名称不能为空")
                value.name = normalized_name
            if "institution_name" in changes and changes["institution_name"] is not None:
                normalized = str(changes["institution_name"]).strip()
                if value.scope == "institution" and not normalized:
                    raise ValueError("医院机构名称不能为空")
                value.institution_name = normalized or None
            if "institution_code" in changes and changes["institution_code"] is not None:
                normalized_code = str(changes["institution_code"]).strip()
                if value.scope == "institution" and not normalized_code:
                    raise ValueError("医院机构代码不能为空")
                if value.institution_code_locked_at and normalized_code != value.institution_code:
                    raise ValueError("机构代码已在首次发布后锁定；请使用专用机构码迁移流程")
                duplicate = session.scalar(select(Deployment).where(
                    Deployment.institution_code == normalized_code,
                    Deployment.id != value.id,
                )) if normalized_code else None
                if duplicate:
                    raise ValueError("该医院机构代码已有 Deployment")
                value.institution_code = normalized_code or None
            if "status" in changes and changes["status"] is not None:
                if changes["status"] not in {"active", "disabled"}:
                    raise ValueError("Deployment 状态无效")
                value.status = changes["status"]
            requested_stage = changes.get("release_stage")
            if requested_stage is not None and requested_stage != value.release_stage:
                target = self._stage_target(session, value.id, requested_stage)
                if requested_stage == "production" and (
                        changes.get("confirm_production") is not True
                        or changes.get("expected_target_uri") != target.milvus_url):
                    raise ValueError("切换生产必须确认当前医院配置的完整生产 URI")
                value.release_stage = requested_stage
                self.audit(session, "deployment.release_stage_changed", "deployment", value.id,
                           {"release_stage": requested_stage, "milvus_url": target.milvus_url,
                            "institution_name": value.institution_name,
                            "institution_code": value.institution_code})
            if value.scope == "institution" and (not value.institution_name or not value.institution_code):
                raise ValueError("医院 Deployment 必须填写医院机构名称和机构代码")
            session.flush()
            return self._shared_deployment_payload(value, self._deployment_targets(session, value.id))

    def create_deployment_task(self, deployment_id: str, project_task_id: str, index_profile_id: str,
                               *, qa_embedding_mode: str | None = None, top_k: int = 10,
                               enabled: bool = True) -> dict[str, Any]:
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
            if top_k <= 0: raise ValueError("top_k 必须大于 0")
            existing = session.scalar(select(ProjectDeploymentTask).where(
                ProjectDeploymentTask.project_deployment_id == deployment_id,
                ProjectDeploymentTask.project_task_id == project_task_id,
            ))
            if existing: raise ValueError("DeploymentTask 已存在")
            value = ProjectDeploymentTask(id=new_id("dtask"), project_deployment_id=deployment_id,
                                          project_task_id=project_task_id, index_profile_id=index_profile_id,
                                          qa_embedding_mode=qa_embedding_mode, top_k=top_k, enabled=enabled)
            session.add(value); session.flush(); return self._deployment_task_payload(session, value)

    def _deployment_task_payload(self, session: Session, value: ProjectDeploymentTask) -> dict[str, Any]:
        task = session.get(ProjectTask, value.project_task_id)
        profile = session.get(KnowledgeIndexProfile, value.index_profile_id) if value.index_profile_id else None
        return {"id": value.id, "project_deployment_id": value.project_deployment_id,
                "project_task_id": value.project_task_id, "task": self._task_payload(task) if task else None,
                "index_profile_id": value.index_profile_id,
                "index_profile": {"id": profile.id, "code": profile.code, "knowledge_type": profile.knowledge_type} if profile else None,
                "qa_embedding_mode": value.qa_embedding_mode, "top_k": value.top_k, "enabled": value.enabled}

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
            shared_deployment = session.get(Deployment, deployment.deployment_id) if deployment else None
            project = session.get(Project, deployment.project_id) if deployment else None
            project_task = session.get(ProjectTask, deployment_task.project_task_id)
            profile = session.get(KnowledgeIndexProfile, deployment_task.index_profile_id) if deployment_task.index_profile_id else None
            if is_qa_agent_project(project) and not qa_agent_profile_contract(project_task, profile):
                raise ValueError("qa-agent org route 的 Task 与 Index Profile 合同不匹配")
            if shared_deployment and shared_deployment.scope == "institution":
                if not shared_deployment.institution_code:
                    raise ValueError("机构 Deployment 尚未配置 institution_code")
                if org_code.strip() != shared_deployment.institution_code:
                    raise ValueError("机构 Deployment 的 org_code 必须等于 institution_code")
                org_name = shared_deployment.institution_name or org_name
            libraries = list(session.scalars(select(KnowledgeLibrary).where(
                KnowledgeLibrary.id.in_(library_ids), KnowledgeLibrary.status == "active",
                KnowledgeLibrary.migration_status == "ready")))
            if len(libraries) != len(set(library_ids)): raise ValueError("授权包含不存在、迁移中或不可用的知识库")
            for library in libraries:
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
            for priority, library in enumerate(libraries):
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
        ).order_by(KnowledgeAssetVersion.version_no.desc()))

    def routing_snapshot(self, boundary_id: str) -> dict[str, Any]:
        with self.sessions() as session:
            project_deployment = self._resolve_deployment(session, boundary_id)
            deployment = session.get(Deployment, project_deployment.deployment_id)
            project = session.get(Project, project_deployment.project_id)
            if not project or not deployment:
                raise ValueError("ProjectDeployment 的 Project 或 Deployment 不存在")
            if deployment.release_stage not in {"test", "production"}:
                raise ValueError("Deployment release_stage 无效")
            target = self._stage_target(session, deployment.id, deployment.release_stage)
            if is_qa_agent_project(project):
                if deployment.scope == "institution" and (
                        not deployment.institution_name or not deployment.institution_code):
                    raise ValueError("qa-agent Deployment 尚未绑定医院机构名称和机构代码")
            if project.code == KG_PROJECT_CODE and deployment.release_stage != "test":
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
                                        "top_k": deployment_task.top_k, "libraries": libraries})
                tasks.append({"deployment_task_id": deployment_task.id, "task_id": task.id, "task_code": task.code,
                              "task_name": task.name, "knowledge_type": task.knowledge_type,
                              "qa_embedding_mode": deployment_task.qa_embedding_mode, "top_k": deployment_task.top_k,
                              "index_profile": profile_payload, "org_routes": org_routes})
            return {"schema": "dataforge.routing-snapshot.v7", "schema_version": 3,
                    "release_stage": deployment.release_stage,
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
                    "milvus_target": {"id": target.id, "name": target.name, "milvus_url": target.milvus_url},
                    "tasks": tasks, "routes": flat_routes}

    def validate_routing(self, boundary_id: str, milvus=None) -> dict[str, Any]:
        snapshot = self.routing_snapshot(boundary_id); problems: list[str] = []
        with self.sessions() as session:
            if not snapshot["routes"]: problems.append("Deployment 没有授权路由")
            for task in snapshot["tasks"]:
                if not task["index_profile"]: problems.append(f"任务 {task['task_code']} 没有已发布 Index Profile")
                for route in task["org_routes"]:
                    if not route["libraries"]: problems.append(f"任务 {task['task_code']} / {route['org_code']} 没有知识库")
                    for info in route["libraries"]:
                        library = session.get(KnowledgeLibrary, info["knowledge_library_id"])
                        if not library or library.migration_status != "ready" or not self._library_ready(session, library):
                            problems.append(f"知识库 {info['knowledge_library_id']} 向量未就绪")
                            continue
                        if not info.get("asset_version_id"):
                            problems.append(f"知识库 {library.id} 没有 Ready AssetVersion")
                            continue
                        if milvus:
                            indexes = info.get("indexes") or []
                            collection = indexes[0].get("collection_name") if indexes else None
                            try:
                                if not collection or not milvus.partition_exists(collection, info["partition_name"]):
                                    problems.append(f"知识库 {library.id} 的目标 Partition 不存在")
                            except Exception as exc:
                                problems.append(f"知识库 {library.id} 的目标 Milvus 校验失败：{exc}")
        return {"valid": not problems, "problems": problems, "snapshot": snapshot}

    def routing_diff(self, boundary_id: str, release_stage: str | None = None) -> dict[str, Any]:
        current = self.routing_snapshot(boundary_id)
        deployment_id = current["project_deployment"]["id"]
        stage = release_stage or current["release_stage"]
        if stage != current["release_stage"]:
            raise ValueError("Diff 阶段必须与 Deployment 当前 release_stage 一致")
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
            release_stage = str(snapshot.get("release_stage") or deployment.release_stage)
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

    def freeze_route_version(self, boundary_id: str) -> dict[str, Any]:
        with self.sessions() as session:
            project_deployment = self._resolve_deployment(session, boundary_id)
            deployment = session.get(Deployment, project_deployment.deployment_id)
            if not deployment or deployment.scope != "institution":
                raise ValueError("只有 institution Deployment 可以冻结离线路由版本")
        check = self.validate_routing(boundary_id)
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
                                         *, route_version_ids: list[str] | None = None,
                                         knowledge_library_ids: list[str] | None = None,
                                         base_release_id: str | None = None,
                                         include_full_document_library: bool = False) -> dict[str, Any]:
        if package_kind not in {"deployment_seed", "institution_release", "knowledge_update"}:
            raise ValueError("机构发布包类型无效")
        with self.sessions.begin() as session:
            deployment = session.get(Deployment, target_deployment_id)
            if not deployment or deployment.scope != "institution":
                raise ValueError("机构发布目标必须是 institution Deployment")
            route_ids = list(dict.fromkeys(route_version_ids or []))
            library_ids = list(dict.fromkeys(knowledge_library_ids or []))
            if package_kind == "knowledge_update":
                if route_ids or not library_ids:
                    raise ValueError("Knowledge Update 只允许选择知识库")
            elif not route_ids:
                raise ValueError("Seed/Institution Release 至少选择一个 frozen RouteVersion")
            routes = list(session.scalars(select(ProjectRouteVersion).where(
                ProjectRouteVersion.id.in_(route_ids), ProjectRouteVersion.status == "frozen",
            ))) if route_ids else []
            if len(routes) != len(route_ids):
                raise ValueError("机构发布包含不存在或未 frozen 的 RouteVersion")
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
                selection_json={"route_version_ids": route_ids, "knowledge_library_ids": library_ids,
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
                        "route_version_ids": route_ids, "knowledge_library_ids": library_ids})
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
                "package_kind": draft.package_kind, "status": draft.status,
                "revision_no": draft.revision_no, "base_release_id": draft.base_release_id,
                "selection": draft.selection_json or {}, "milvus_override": draft.milvus_override_json or {},
                "milvus_override_reason": draft.milvus_override_reason, "projects": projects,
                "created_at": draft.created_at.isoformat(), "updated_at": draft.updated_at.isoformat()}

    def get_institution_release_draft(self, draft_id: str) -> dict[str, Any]:
        with self.sessions() as session:
            draft = session.get(InstitutionReleaseDraft, draft_id)
            if not draft:
                raise ValueError("机构发布草稿不存在")
            return self._institution_release_draft_payload(session, draft)

    def update_institution_release_draft(self, draft_id: str, *, route_version_ids: list[str] | None = None,
                                         knowledge_library_ids: list[str] | None = None,
                                         base_release_id: str | None = None,
                                         include_full_document_library: bool | None = None,
                                         milvus_override: dict[str, Any] | None = None,
                                         milvus_override_reason: str | None = None) -> dict[str, Any]:
        with self.sessions.begin() as session:
            draft = session.get(InstitutionReleaseDraft, draft_id, with_for_update=True)
            if not draft or draft.status not in {"draft", "planned", "failed"}:
                raise ValueError("机构发布草稿当前不可编辑")
            current = dict(draft.selection_json or {})
            route_ids = list(dict.fromkeys(route_version_ids if route_version_ids is not None
                                           else current.get("route_version_ids") or []))
            library_ids = list(dict.fromkeys(knowledge_library_ids if knowledge_library_ids is not None
                                             else current.get("knowledge_library_ids") or []))
            if draft.package_kind == "knowledge_update":
                if route_ids or not library_ids:
                    raise ValueError("Knowledge Update 只允许选择知识库")
            elif not route_ids:
                raise ValueError("Seed/Institution Release 至少选择一个 frozen RouteVersion")
            routes = list(session.scalars(select(ProjectRouteVersion).where(
                ProjectRouteVersion.id.in_(route_ids), ProjectRouteVersion.status == "frozen",
            ))) if route_ids else []
            if len(routes) != len(route_ids):
                raise ValueError("机构发布包含不存在或未 frozen 的 RouteVersion")
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
                "route_version_ids": route_ids, "knowledge_library_ids": library_ids,
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
        encoded = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        digest = hashlib.sha256(encoded).hexdigest()
        with self.sessions.begin() as session:
            draft = session.get(InstitutionReleaseDraft, draft_id, with_for_update=True)
            if not draft or draft.status not in {"draft", "planned", "failed"}:
                raise ValueError("机构发布草稿当前不可冻结")
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

    def published_route_version(self, boundary_id: str, version_no: int | None = None,
                                release_stage: str | None = None) -> ProjectRouteVersion:
        with self.sessions() as session:
            project_deployment = self._resolve_deployment(session, boundary_id)
            deployment = session.get(Deployment, project_deployment.deployment_id)
            stage = release_stage or deployment.release_stage
            query = select(ProjectRouteVersion).where(ProjectRouteVersion.project_deployment_id == project_deployment.id,
                                                       ProjectRouteVersion.release_stage == stage,
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

    def list_route_versions(self, boundary_id: str, release_stage: str | None = None) -> list[dict[str, Any]]:
        with self.sessions() as session:
            project_deployment = self._resolve_deployment(session, boundary_id)
            deployment = session.get(Deployment, project_deployment.deployment_id)
            stage = release_stage or deployment.release_stage
            return [{"id": item.id, "project_id": item.project_id, "project_deployment_id": item.project_deployment_id,
                     "origin": item.origin, "release_stage": item.release_stage,
                     "version_no": item.version_no, "status": item.status,
                     "checksum": item.checksum, "object_key": item.object_key,
                     "created_at": item.created_at.isoformat(), "published_at": item.published_at.isoformat() if item.published_at else None}
                    for item in session.scalars(select(ProjectRouteVersion).where(
                        ProjectRouteVersion.project_deployment_id == project_deployment.id,
                        ProjectRouteVersion.release_stage == stage).order_by(ProjectRouteVersion.version_no.desc()))]

    def route_version_detail(self, boundary_id: str, version_no: int,
                             release_stage: str | None = None) -> dict[str, Any]:
        with self.sessions() as session:
            project_deployment = self._resolve_deployment(session, boundary_id)
            deployment = session.get(Deployment, project_deployment.deployment_id)
            stage = release_stage or deployment.release_stage
            value = session.scalar(select(ProjectRouteVersion).where(
                ProjectRouteVersion.project_deployment_id == project_deployment.id,
                ProjectRouteVersion.release_stage == stage,
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
            if deployment.release_stage != release_stage:
                raise ValueError("Deployment 当前阶段与请求阶段不一致")
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

    def close(self) -> None:
        self.engine.dispose()
