from __future__ import annotations

import hashlib
import logging
import json
import os
import secrets
import threading
import uuid
from urllib.parse import quote
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path, PurePath
from typing import Annotated, Literal

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from ..config import Settings
from .auth import SESSION_COOKIE, verify_admin_password
from .models import AdminSession, Deployment, ProjectDeployment, utc_now
from .observability import COMPONENTS, ComponentCheckService, components_snapshot
from .runner import preview_template_definition
from .sample_data import SampleDataService
from .routing import AtomicRoutingPublisher
from .routing_delivery import RoutingDeliveryService
from .storage import LocalObjectStore, MinioObjectStore
from .parsed_document import (
    apply_table_cell_updates,
    persist_content,
    read_anchors,
    read_content,
    reviewed_text_anchors,
)
from .instance import InstanceContext
from .local_config import LocalMilvusConfigurationService
from .milvus_targets import (
    InstanceMilvusConnectionResolver, MilvusConnectionResolver, MilvusTargetService,
    StaleMilvusVerification,
)
from .llm_serving import configure_llm_serving_registry
from .servings import ServingManager
from .retrieval import (
    PublicRetrievalError,
    PublicRetrievalRequest,
    PublicRetrievalService,
    RetrievalDebugRequest,
    RetrievalDebugService,
)
from .entity_types import entity_type_catalog, resolve_entity_types
from .migration.importer import validate_local_package_target
from .migration.package import inspect_package
from .migration.planner import InstitutionReleasePlanner
from .migration.verifier import ActivationPreflightVerifier
from .store import (
    CENTRAL_DEPLOYMENT_CODE,
    CENTRAL_STAGE_TARGETS,
    ReviewGateError,
    V7Store,
    new_id,
)
from .vector import V7Milvus, VectorSyncService
from .vector_inventory import INVENTORY_STATUSES, MilvusInventoryService


MAX_UPLOAD_BYTES = 200 * 1024 * 1024
MILVUS_STARTUP_CHECK_DELAY_SECONDS = 30
MEDIA_TYPES = {
    ".pdf": "application/pdf", ".csv": "text/csv",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".md": "text/markdown", ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain", ".json": "application/json", ".jsonl": "application/x-ndjson",
}
SUPPORTED_EXTENSIONS = set(MEDIA_TYPES)
SUPPORTED_FORMAT_MESSAGE = "仅支持 PDF、CSV、XLSX、Markdown、DOC、DOCX、TXT、JSON 和 JSONL"
logger = logging.getLogger(__name__)


class DocumentLibraryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    description: str = ""


class SourceImportPreflightRequest(BaseModel):
    entries: list[dict] = Field(min_length=1)


class SourceVersionReactivationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_current_version_id: str


class SelectedDocumentSourcesRequest(BaseModel):
    source_ids: list[str] = Field(min_length=1)


class ParsedDocumentCellUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sheet_index: int = Field(ge=0)
    row_index: int = Field(ge=0)
    column_index: int = Field(ge=0)
    value: str | int | float | bool | None = None
    value_type: Literal["empty", "string", "number", "boolean"]


class ParsedDocumentReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_content_digest: str = Field(min_length=64, max_length=64)
    expected_anchor_map_digest: str = Field(min_length=64, max_length=64)
    markdown: str | None = None
    cell_updates: list[ParsedDocumentCellUpdate] = Field(default_factory=list, max_length=10000)


class ParsedDocumentBatchReviewItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: str
    source_version_id: str
    parsed_document_id: str
    expected_content_digest: str = Field(min_length=64, max_length=64)
    expected_anchor_map_digest: str = Field(min_length=64, max_length=64)


class ParsedDocumentBatchReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[ParsedDocumentBatchReviewItem] = Field(min_length=1, max_length=200)


class FlowChunkUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str
    expected_revision_no: int = Field(ge=1)


class FlowChunkSplitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    parts: list[str] = Field(min_length=2)
    expected_revision_no: int = Field(ge=1)


class FlowChunkMergeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chunk_ids: list[str] = Field(min_length=2)
    expected_revisions: dict[str, int]


class FlowChunkReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["approved", "excluded"]
    expected_revision_no: int = Field(ge=1)


class FlowChunkBatchReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chunk_ids: list[str] = Field(min_length=1)
    action: Literal["approved", "excluded"]
    expected_revisions: dict[str, int]


class DocumentTemplateBatchBindingRequest(BaseModel):
    knowledge_flow_template_ids: list[str] = Field(min_length=1)


class DocumentDeletionRequest(BaseModel):
    source_ids: list[str] = Field(default_factory=list)
    document_library_ids: list[str] = Field(default_factory=list)


class KnowledgeLibraryDeletionRequest(BaseModel):
    library_ids: list[str] = Field(min_length=1)


class KnowledgeItemUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data: dict | None = None
    canonical_content: str | None = None
    expected_review_revision: int = Field(ge=1)


class KnowledgeItemReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["approved", "rejected"]
    expected_review_revision: int = Field(ge=1)
    review_note: str | None = Field(default=None, max_length=2000)


class KnowledgeItemBatchReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    item_ids: list[str] = Field(min_length=1, max_length=200)
    action: Literal["approve", "reject"]
    expected_revisions: dict[str, int]
    review_note: str | None = Field(default=None, max_length=2000)


class KnowledgeVectorPublishRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope: Literal["all_approved", "all_active"]
    expected_snapshot_digest: str = Field(min_length=64, max_length=64)
    idempotency_key: str = Field(min_length=1, max_length=64)
    approve_pending: bool = False


class KnowledgeJobRequest(BaseModel):
    input_source: str = "parsed_document"
    source_version_ids: list[str] = Field(min_length=1)
    output_library_ids: dict[str, str] = Field(min_length=1)
    knowledge_flow_template_id: str


class KnowledgeJobBatchActionRequest(BaseModel):
    job_ids: list[str] = Field(min_length=1)
    action: Literal["cancel", "retry", "delete"]


class FlowTemplateRequest(BaseModel):
    code: str = ""
    name: str
    output_types: list[str] | None = None
    authoring_mode: Literal["standard", "advanced"] = "advanced"
    managed_template_code: str | None = None
    definition: dict = Field(default_factory=dict)
    expected_definition_checksum: str | None = None
    derived_from_template_id: str | None = None
    derived_from_revision_id: str | None = None


class FlowPublishRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision_id: str = Field(min_length=1, max_length=64, pattern=r"^\S+$")
    expected_definition_checksum: str = Field(pattern=r"^[a-f0-9]{64}$")


class FlowCompilerPreviewRequest(BaseModel):
    authoring_mode: Literal["standard", "advanced"] = "advanced"
    managed_template_code: str | None = None
    output_types: list[str] | None = None
    definition: dict = Field(default_factory=dict)


class OperatorCandidatesRequest(BaseModel):
    definition: dict = Field(default_factory=dict)
    output_types: list[str] = Field(default_factory=list)
    source_node_id: str | None = None
    source_port: str = "output"
    node_id: str | None = None
    direction: Literal["upstream", "downstream"] = "downstream"
    include_incompatible: bool = False


class OperatorManifestRequest(BaseModel):
    manifest: dict


class TemplateSampleRequest(BaseModel):
    sample_id: Literal["guideline-md", "faq-csv", "case-txt"] = "guideline-md"


class SubflowRevisionRequest(BaseModel):
    description: str = ""
    input_contract: dict = Field(default_factory=dict)
    output_contract: dict = Field(default_factory=dict)
    definition: dict


class SubflowCreateRequest(BaseModel):
    code: str
    name: str
    description: str = ""
    definition: dict
    output_types: list[str] = Field(min_length=1)
    selected_node_ids: list[str] = Field(min_length=1)


class DerivedRunRequest(BaseModel):
    mode: Literal["node_only", "from_node"]
    node_id: str
    parameter_overrides: dict = Field(default_factory=dict)
    idempotency_key: str | None = None


class DebugRunConfigRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    template_id: str
    revision_id: str
    expected_compiled_checksum: str
    input_source: Literal["builtin_sample", "flow_chunk_review_snapshot"] = "flow_chunk_review_snapshot"
    sample_code: str | None = None
    flow_chunk_review_snapshot_ids: list[str] = Field(default_factory=list)
    sink_library_bindings: dict[str, str] = Field(default_factory=dict)
    idempotency_key: str | None = None


class ApplyDebugRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision_id: str
    expected_definition_checksum: str
    idempotency_key: str


class SaveDebugRunAsFlowRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    description: str = ""
    idempotency_key: str


class EntityTypesResolveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entity_types: list[dict | str] = Field(default_factory=list)
    action: Literal["normalize", "add_custom", "add_medical", "remove_medical", "update"] = "normalize"
    label: str | None = None
    code: str | None = None
    description: str | None = None


class GraphPromptPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    definition: dict
    node_id: str


class CommitDerivedRunRequest(BaseModel):
    preview_checksum: str
    idempotency_key: str


class PersistDerivedParametersRequest(BaseModel):
    node_id: str
    parameters: dict = Field(default_factory=dict)


class PromptTemplateRequest(BaseModel):
    code: str
    name: str
    body: str
    input_schema: dict = Field(default_factory=dict)
    output_schema: dict = Field(default_factory=dict)
    knowledge_types: list[str] = Field(default_factory=lambda: ["*"])


class QualityProfileRequest(BaseModel):
    code: str
    name: str
    rules: dict = Field(default_factory=dict)
    knowledge_types: list[str] = Field(default_factory=lambda: ["*"])


class ProjectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str


class KnowledgeTypeSemanticRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    type_schema: dict = Field(alias="schema")
    canonical_field: str
    identity_fields: list[str] = Field(min_length=1)
    source_policy: Literal["single", "multiple"]


class CreateKnowledgeTypeRequest(KnowledgeTypeSemanticRequest):
    code: str
    name: str
    icon: str = "知"


class KnowledgeTypeStorageBindingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    index_profile_ids: list[str] = Field(default_factory=list)
    managed_collection_name: str = ""
    reuse_managed_collection_id: str | None = None


class IndexProfileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = ""
    knowledge_type: str
    collection_name: str = ""
    collection_mode: Literal["create", "attach"] | None = None
    collection_policy: Literal["external", "managed"] | None = None
    reuse_managed_collection_id: str | None = None
    storage_schema: dict | None = None
    index_spec: dict = Field(default_factory=lambda: {"index_type": "AUTOINDEX"})
    embedding_serving_id: str | None = None
    embedding_input: Literal["canonical_content", "question", "question_answer"] = "canonical_content"
    embedding_code: str = ""
    embedding_model: str = ""
    dimension: int = 0
    metric_type: str = "COSINE"
    endpoint_ref: str | None = None
    fields: dict


class ProjectTaskRequest(BaseModel):
    code: str
    name: str
    knowledge_type: str
    description: str = ""


class RouteRequest(BaseModel):
    org_code: str
    org_name: str = ""
    knowledge_library_ids: list[str] = Field(min_length=1)


class MilvusTargetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    milvus_url: str
    token: str | None = None


class MilvusTargetPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = None
    milvus_url: str | None = None
    token: str | None = None
    preserve_token: bool = True


class AuthoringMilvusTargetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    milvus_target_id: str


class DeploymentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    institution_name: str
    institution_code: str
    project_ids: list[str] = Field(default_factory=list)


class DeploymentPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    institution_name: str | None = None
    institution_code: str | None = None
    status: Literal["active", "disabled"] | None = None


class InstanceReleaseTargetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    milvus_target_id: str
    milvus_target_revision_id: str
    confirm_production: bool = False
    expected_target_uri: str | None = None


class DeploymentProjectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: str


class DeploymentProjectsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_ids: list[str] = Field(default_factory=list)


class RoutingActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    release_stage: Literal["test", "production"]
    expected_target_uri: str | None = None
    confirm_production: bool = False
    route_version_id: str | None = None


class PublicRetrievalAdminRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    release_stage: Literal["test", "production"]
    task_code: str = Field(min_length=1, max_length=120)
    org_code: str = Field(min_length=1, max_length=120)
    query: str = Field(min_length=1, max_length=8192)


class DeploymentTaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_task_id: str
    index_profile_id: str
    top_k: int = 10
    final_top_k: int | None = None
    reranker_serving_code: str | None = None
    enabled: bool = True


class DeploymentTaskPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    top_k: int = Field(default=10, ge=1, le=200)
    final_top_k: int = Field(default=5, ge=1, le=200)
    reranker_serving_code: str | None = None
    enabled: bool = True


class MigrationImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_id: str
    conflict_resolutions: dict[str, Literal["keep_local", "replace_with_central", "import_as_new"]] = Field(default_factory=dict)


class RouteCandidateBatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidate_ids: list[str] = Field(min_length=1)


class InstitutionReleaseDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_deployment_id: str
    target_institution_code: str = Field(min_length=1, max_length=120)
    package_kind: Literal["deployment_seed", "institution_release", "knowledge_update"]
    release_stage: Literal["test", "production"]
    route_version_ids: list[str] = Field(default_factory=list)
    knowledge_library_ids: list[str] = Field(default_factory=list)
    extra_asset_version_ids: list[str] = Field(default_factory=list)
    base_release_id: str | None = None
    include_full_document_library: bool = False


class InstitutionReleaseDraftPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    release_stage: Literal["test", "production"] | None = None
    route_version_ids: list[str] | None = None
    knowledge_library_ids: list[str] | None = None
    extra_asset_version_ids: list[str] | None = None
    base_release_id: str | None = None
    include_full_document_library: bool | None = None
    milvus_override: dict | None = None
    milvus_override_reason: str | None = None


class LocalMilvusConfigurationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    uri: str
    token: str | None = None
    preserve_token: bool = True


class MigrationResumeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    selected_import_target: Literal["current_target", "candidate_target"] | None = None


class KnowledgeAssetGcRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    execute: bool = False
    confirmation: str | None = None


class ComponentCheckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    components: list[str] = Field(min_length=1)


class ModelServingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    serving_code: str
    name: str
    serving_type: Literal["openai-compatible-chat"] = "openai-compatible-chat"
    model_name: str
    base_url: str = ""
    api_key: str = ""
    timeout_seconds: int = 120
    max_retries: int = 2
    max_tokens: int = 16384
    context_window_tokens: int = 8192
    tokenizer_name: str = "Qwen/Qwen3-32B"
    disable_thinking: bool = True
    is_enabled: bool = True


class ModelServingPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = None
    serving_type: Literal["openai-compatible-chat"] | None = None
    model_name: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    clear_credential: bool = False
    timeout_seconds: int | None = None
    max_retries: int | None = None
    max_tokens: int | None = None
    context_window_tokens: int | None = None
    tokenizer_name: str | None = None
    disable_thinking: bool | None = None
    is_enabled: bool | None = None


class EmbeddingServingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    serving_code: str
    name: str
    provider_type: Literal["openai-compatible-embedding"] = "openai-compatible-embedding"
    model_name: str
    base_url: str = ""
    api_key: str = ""
    dimension: int
    batch_size: int = 32
    timeout_seconds: int = 120
    max_retries: int = 2
    is_enabled: bool = True


class EmbeddingServingPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = None
    provider_type: Literal["openai-compatible-embedding"] | None = None
    model_name: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    clear_credential: bool = False
    dimension: int | None = None
    batch_size: int | None = None
    timeout_seconds: int | None = None
    max_retries: int | None = None
    is_enabled: bool | None = None


class RerankerServingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    serving_code: str
    name: str
    provider_type: Literal["cohere-compatible-rerank"] = "cohere-compatible-rerank"
    model_name: str
    base_url: str = ""
    api_key: str = ""
    timeout_seconds: int = Field(default=120, ge=1)
    max_retries: int = Field(default=2, ge=0, le=10)
    max_batch_size: int = Field(default=32, ge=1, le=200)
    max_concurrency: int = Field(default=4, ge=1, le=64)
    is_enabled: bool = True


class RerankerServingPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = None
    provider_type: Literal["cohere-compatible-rerank"] | None = None
    model_name: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    clear_credential: bool = False
    timeout_seconds: int | None = Field(default=None, ge=1)
    max_retries: int | None = Field(default=None, ge=0, le=10)
    max_batch_size: int | None = Field(default=None, ge=1, le=200)
    max_concurrency: int | None = Field(default=None, ge=1, le=64)
    is_enabled: bool | None = None


def _objects(settings: Settings):
    if settings.minio_endpoint and settings.minio_access_key and settings.minio_secret_key:
        return MinioObjectStore(settings.minio_endpoint, settings.minio_access_key, settings.minio_secret_key, settings.minio_bucket)
    return LocalObjectStore(settings.state_dir / "v7-objects")


def _review_parsed_document(store: V7Store, objects, parsed_document_id: str,
                            payload: ParsedDocumentReviewRequest) -> dict:
    parent = store.parsed_document(parsed_document_id)
    content, anchors = read_content(objects, parent), read_anchors(objects, parent)
    if parent.kind == "textual":
        if payload.cell_updates:
            raise ValueError("文本 ParsedDocument 不接受单元格修改")
        if payload.markdown is None or not payload.markdown.strip():
            raise ValueError("Markdown 校订内容不能为空")
        reviewed_content = payload.markdown
        reviewed_anchors = reviewed_text_anchors(
            anchors, content_changed=reviewed_content != content,
            parent_parsed_document_id=parent.id,
        )
    elif parent.kind == "tabular":
        if payload.markdown is not None:
            raise ValueError("表格 ParsedDocument 不接受 Markdown 内容")
        reviewed_content, reviewed_anchors = apply_table_cell_updates(
            content, anchors, [item.model_dump() for item in payload.cell_updates],
            parent_parsed_document_id=parent.id,
        )
    else:
        raise ValueError("未知 ParsedDocument 类型")
    reviewed_id = new_id("parsed")
    persisted = persist_content(
        objects, reviewed_id, kind=parent.kind, content=reviewed_content,
        anchors=reviewed_anchors,
    )
    object_keys = list(persisted.pop("object_keys", []))
    try:
        result = store.approve_parsed_document_revision(
            parent.id, reviewed_document_id=reviewed_id,
            expected_content_digest=payload.expected_content_digest,
            expected_anchor_map_digest=payload.expected_anchor_map_digest,
            content_ref=persisted["content_ref"], content_digest=persisted["content_digest"],
            anchor_map_ref=persisted["anchor_map_ref"], anchor_map_digest=persisted["anchor_map_digest"],
        )
    except Exception:
        for key in object_keys:
            try:
                objects.delete_key(key)
            except Exception:
                logger.exception("清理失败的 ParsedDocument 校订对象：%s", key)
        raise
    if result.get("idempotent") and result["parsed_document"]["id"] != reviewed_id:
        for key in object_keys:
            try:
                objects.delete_key(key)
            except Exception:
                logger.exception("清理幂等 ParsedDocument 校订对象：%s", key)
    return result


def _error(exc: ValueError) -> HTTPException:
    payload = getattr(exc, "payload", None)
    return HTTPException(status_code=422, detail=payload() if callable(payload) else str(exc))


def _review_error(exc: ReviewGateError) -> HTTPException:
    return HTTPException(status_code=409, detail=exc.payload())


def _source_error(exc: ValueError) -> HTTPException:
    message = str(exc)
    if "同目录下已存在" in message or message == "SOURCE_VERSION_REACTIVATION_STALE":
        return HTTPException(status_code=409, detail={"code": "SOURCE_PATH_CONFLICT", "message": message})
    return _error(exc)


def _content_disposition(disposition: str, filename: str) -> str:
    safe = quote(Path(filename).name, safe="")
    return f"{disposition}; filename*=UTF-8''{safe}"


async def _read_upload(upload: UploadFile) -> bytes:
    content = bytearray()
    while chunk := await upload.read(1024 * 1024):
        content.extend(chunk)
        if len(content) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="单个文件不能超过 200 MB")
    if not content:
        raise HTTPException(status_code=422, detail="文件不能为空")
    return bytes(content)


def create_app(settings: Settings | None = None, *, check_schema: bool = True) -> FastAPI:
    resolved = settings or Settings.load(); resolved.ensure_directories()
    store = V7Store(resolved.platform_database_url, enforce_serving_health=True,
                    config_encryption_key=resolved.config_encryption_key)
    if check_schema:
        store.assert_schema_current()
    instance = InstanceContext.load(store, resolved)
    objects = _objects(resolved)
    samples = SampleDataService()
    component_checks = ComponentCheckService(store, resolved, objects)
    component_check_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="observability-run")
    serving_check_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="serving-startup-check")
    milvus_startup_check_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="milvus-startup-check")
    local_milvus_config = LocalMilvusConfigurationService(store, resolved.config_encryption_key)
    milvus_targets_service = MilvusTargetService(
        store, resolved.config_encryption_key, lambda uri, token: V7Milvus(uri, token),
    )
    central_milvus_resolver = MilvusConnectionResolver(store, resolved.config_encryption_key)
    serving_manager = ServingManager(store.sessions, resolved.config_encryption_key)
    configure_llm_serving_registry(store.sessions, resolved.config_encryption_key)
    app = FastAPI(title="DataForge V7", version="7.0.0")
    app.state.store, app.state.objects, app.state.instance, app.state.samples = store, objects, instance, samples
    app.state.local_milvus_config = local_milvus_config
    milvus_resolver = InstanceMilvusConnectionResolver(
        central_milvus_resolver, local_milvus_config, lambda: app.state.instance,
    )
    component_checks.milvus_resolver = milvus_resolver
    app.state.milvus_resolver = milvus_resolver
    app.state.serving_manager = serving_manager
    app.state.serving_startup_check_future = None
    app.state.milvus_targets_service = milvus_targets_service
    app.state.milvus_startup_check_stop = threading.Event()
    app.state.milvus_startup_check_future = None
    app.state.routing_publications = set()
    app.state.retrieval_debug = RetrievalDebugService(store, serving_manager, milvus_resolver=milvus_resolver)
    app.state.public_retrieval = PublicRetrievalService(store, app.state.retrieval_debug)
    app.state.routing_publications_lock = threading.RLock()

    def _authoring_connection():
        try:
            return milvus_resolver.authoring(app.state.instance.id)
        except ValueError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    def _vector_sync_service() -> VectorSyncService:
        return VectorSyncService.from_connection(store, _authoring_connection())

    def _milvus_inventory_service() -> MilvusInventoryService:
        return MilvusInventoryService.from_connection(store, _authoring_connection())

    @app.exception_handler(RequestValidationError)
    async def public_retrieval_validation_error(request: Request, exc: RequestValidationError):
        if request.url.path.startswith("/api/runtime/retrieval/v1/"):
            return JSONResponse(status_code=422, content={
                "error": {"code": "invalid_request", "message": "公共检索请求格式无效"},
                "request_id": getattr(request.state, "request_id", ""),
            }, headers={"Cache-Control": "no-store"})
        return await request_validation_exception_handler(request, exc)

    def public_retrieval_error(exc: PublicRetrievalError, request: Request) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={
            "error": {"code": exc.code, "message": exc.message},
            "request_id": getattr(request.state, "request_id", ""),
        }, headers={"Cache-Control": "no-store"})

    def require_retrieval_token(request: Request) -> None:
        configured = os.getenv("DATAFORGE_RETRIEVAL_TOKEN", "").strip()
        if not configured:
            raise PublicRetrievalError(
                "retrieval_token_not_configured", "DataForge Retrieval token 未配置", 503,
            )
        supplied = request.headers.get("Authorization", "")
        if not secrets.compare_digest(supplied, f"Bearer {configured}"):
            raise PublicRetrievalError("retrieval_token_invalid", "Retrieval token 无效", 401)

    def _check_central_milvus_after_delay() -> list[dict]:
        if app.state.milvus_startup_check_stop.wait(MILVUS_STARTUP_CHECK_DELAY_SECONDS):
            return []
        target_ids = tuple(spec[0] for spec in CENTRAL_STAGE_TARGETS.values())
        results = milvus_targets_service.check_startup_targets(target_ids)
        test_target_id = CENTRAL_STAGE_TARGETS["test"][0]
        test_result = next((item for item in results if item.get("target_id") == test_target_id), None)
        if test_result and test_result.get("status") == "healthy":
            try:
                store.bind_authoring_milvus_target_if_unset(app.state.instance.id, test_target_id)
            except Exception as exc:
                logger.warning(
                    "Default authoring Milvus binding failed: target_id=%s error_type=%s",
                    test_target_id, type(exc).__name__,
                )
            try:
                store.bind_default_test_release_target_if_unset(app.state.instance.id)
            except Exception as exc:
                logger.warning(
                    "Default test release Milvus binding failed: target_id=%s error_type=%s",
                    test_target_id, type(exc).__name__,
                )
        return results

    app.state.run_milvus_startup_check_after_delay = _check_central_milvus_after_delay

    @app.on_event("startup")
    def reconcile_component_checks() -> None:
        store.interrupt_component_checks()
        if store.engine.dialect.name != "sqlite":
            app.state.serving_startup_check_future = serving_check_executor.submit(
                serving_manager.check_configured_on_startup,
            )
            if app.state.instance.mode == "central":
                app.state.milvus_startup_check_future = milvus_startup_check_executor.submit(
                    _check_central_milvus_after_delay,
                )

    @app.on_event("shutdown")
    def shutdown_component_checks() -> None:
        app.state.milvus_startup_check_stop.set()
        component_check_executor.shutdown(wait=False, cancel_futures=True)
        serving_check_executor.shutdown(wait=False, cancel_futures=True)
        milvus_startup_check_executor.shutdown(wait=False, cancel_futures=True)

    @app.middleware("http")
    async def require_admin(request: Request, call_next):
        open_paths = {"/api/health", "/api/health/live", "/api/health/ready", "/api/auth/status", "/api/auth/login"}
        runtime_path = request.url.path.startswith((
            "/api/runtime/routing/", "/api/runtime/retrieval/v1/",
        ))
        if resolved.authentication_enabled and request.url.path.startswith("/api/") and request.url.path not in open_paths and not runtime_path:
            token = request.cookies.get(SESSION_COOKIE)
            with store.sessions() as session:
                active = session.get(AdminSession, token) if token else None
                if not active or active.expires_at <= utc_now():
                    return JSONResponse(status_code=401, content={"detail": "需要管理员登录"})
        return await call_next(request)

    @app.middleware("http")
    async def restrict_unbound_local(request: Request, call_next):
        app.state.instance = InstanceContext.load(store, resolved)
        current = app.state.instance
        if current.mode == "local" and not current.bound_deployment_id and request.url.path.startswith("/api/"):
            allowed_prefixes = ("/api/health", "/api/auth/", "/api/instance", "/api/local/",
                                "/api/migrations/import",
                                "/api/migrations/", "/api/runtime/routing/")
            project_creation = request.method == "POST" and request.url.path == "/api/projects"
            if not project_creation and not any(request.url.path.startswith(prefix) for prefix in allowed_prefixes):
                return JSONResponse(status_code=404, content={"detail": "local 实例尚未完成 Seed 初始化"})
        return await call_next(request)

    @app.middleware("http")
    async def attach_request_id(request: Request, call_next):
        supplied = str(request.headers.get("X-Request-ID") or "").strip()
        request_id = supplied[:120] if supplied and all(ch.isalnum() or ch in "-_." for ch in supplied) else str(uuid.uuid4())
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

    @app.get("/api/health")
    def health():
        return {"platform": "v7", **components_snapshot(store, detailed=False)}

    @app.get("/api/health/live")
    def health_live():
        return {"status": "healthy", "platform": "v7"}

    @app.get("/api/health/ready")
    def health_ready():
        try:
            with store.engine.connect() as connection:
                connection.exec_driver_sql("SELECT 1")
            return {"status": "healthy", "platform": "v7"}
        except SQLAlchemyError:
            return JSONResponse(status_code=503, content={"status": "unavailable", "platform": "v7"})

    @app.get("/api/observability/components")
    def observability_components():
        return {"available_components": list(COMPONENTS), **components_snapshot(store, detailed=True)}

    @app.post("/api/observability/check-runs", status_code=202)
    def create_component_check(payload: ComponentCheckRequest):
        try:
            components = component_checks.validate_components(payload.components)
            run, created = store.start_component_check(components)
            if created:
                component_check_executor.submit(component_checks.run, run["id"], components)
            return {**run, "created": created}
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/observability/check-runs/{run_id}")
    def component_check_run(run_id: str):
        try:
            return store.get_component_check(run_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/instance")
    def instance_info():
        payload = app.state.instance.payload()
        payload["org_code_presets"] = [
            {"name": item.name, "org_code": item.org_code}
            for item in resolved.org_code_presets
        ]
        payload["default_release_stage"] = resolved.default_release_stage
        try:
            payload["authoring_milvus_target"] = store.authoring_milvus_target(app.state.instance.id)
        except ValueError:
            payload["authoring_milvus_target"] = None
        payload["deployment_flavor"] = (
            "central_control_plane" if app.state.instance.mode == "central" else "institution_private"
        )
        payload["display_name"] = "智能中心"
        if app.state.instance.mode == "local":
            payload["display_name"] = "机构本地"
            payload["local_milvus_default_uri"] = resolved.local_milvus_default_uri
            if app.state.instance.bound_deployment_id:
                with store.sessions() as session:
                    deployment = session.get(Deployment, app.state.instance.bound_deployment_id)
                    if deployment:
                        payload["display_name"] = deployment.institution_name or deployment.name
                        payload["institution_code"] = deployment.institution_code
        return payload

    @app.get("/api/auth/status")
    def auth_status(request: Request):
        with store.sessions() as session:
            current = session.get(AdminSession, request.cookies.get(SESSION_COOKIE, ""))
            authenticated = bool(current and current.expires_at > utc_now())
        return {"authentication_enabled": resolved.authentication_enabled, "authenticated": authenticated}

    @app.post("/api/auth/login")
    async def login(request: Request, response: Response):
        payload = await request.json(); password = str(payload.get("password", ""))
        if not resolved.authentication_enabled or not resolved.admin_password_hash:
            raise HTTPException(status_code=503, detail="未配置管理员认证")
        if not verify_admin_password(password, resolved.admin_password_hash):
            raise HTTPException(status_code=401, detail="管理员密码错误")
        session_id, expires_at = secrets.token_urlsafe(32), utc_now() + timedelta(hours=8)
        with store.sessions.begin() as session:
            session.add(AdminSession(id=session_id, expires_at=expires_at)); store.audit(session, "session.created", "admin_session", session_id)
        response.set_cookie(SESSION_COOKIE, session_id, httponly=True, secure=os.getenv("DATAFORGE_COOKIE_SECURE", "0") == "1", samesite="strict", max_age=8 * 3600, path="/")
        return {"authenticated": True, "expires_at": expires_at.isoformat()}

    @app.post("/api/auth/logout", status_code=204)
    def logout(request: Request, response: Response):
        with store.sessions.begin() as session:
            current = session.get(AdminSession, request.cookies.get(SESSION_COOKIE, ""))
            if current: session.delete(current)
        response.delete_cookie(SESSION_COOKIE, path="/")

    @app.get("/api/serving-categories")
    def serving_categories():
        return serving_manager.categories()

    @app.get("/api/model-servings")
    def model_servings(): return serving_manager.list("model")

    @app.post("/api/model-servings", status_code=201)
    def create_model_serving(payload: ModelServingRequest):
        try: return serving_manager.create("model", new_id("modelserving"), payload.model_dump())
        except ValueError as exc: raise _error(exc) from exc

    @app.get("/api/model-servings/{serving_id}")
    def model_serving(serving_id: str):
        try: return serving_manager.get("model", serving_id)
        except ValueError as exc: raise _error(exc) from exc

    @app.patch("/api/model-servings/{serving_id}")
    def patch_model_serving(serving_id: str, payload: ModelServingPatch):
        try: return serving_manager.update("model", serving_id, payload.model_dump(exclude_none=True))
        except ValueError as exc: raise _error(exc) from exc

    @app.delete("/api/model-servings/{serving_id}")
    def delete_model_serving(serving_id: str):
        try: return serving_manager.delete("model", serving_id)
        except ValueError as exc: raise _error(exc) from exc

    @app.post("/api/model-servings/{serving_id}/test")
    def test_model_serving(serving_id: str):
        try: return serving_manager.test("model", serving_id)
        except ValueError as exc: raise _error(exc) from exc

    @app.post("/api/model-servings/{serving_id}/set-default")
    def default_model_serving(serving_id: str):
        try: return serving_manager.set_default("model", serving_id)
        except ValueError as exc: raise _error(exc) from exc

    @app.get("/api/model-servings/{serving_id}/references")
    def model_serving_references(serving_id: str):
        try: return serving_manager.references("model", serving_id)
        except ValueError as exc: raise _error(exc) from exc

    @app.get("/api/embedding-servings")
    def embedding_servings(): return serving_manager.list("embedding")

    @app.post("/api/embedding-servings", status_code=201)
    def create_embedding_serving(payload: EmbeddingServingRequest):
        try: return serving_manager.create("embedding", new_id("embeddingserving"), payload.model_dump())
        except ValueError as exc: raise _error(exc) from exc

    @app.get("/api/embedding-servings/{serving_id}")
    def embedding_serving(serving_id: str):
        try: return serving_manager.get("embedding", serving_id)
        except ValueError as exc: raise _error(exc) from exc

    @app.patch("/api/embedding-servings/{serving_id}")
    def patch_embedding_serving(serving_id: str, payload: EmbeddingServingPatch):
        try: return serving_manager.update("embedding", serving_id, payload.model_dump(exclude_none=True))
        except ValueError as exc: raise _error(exc) from exc

    @app.delete("/api/embedding-servings/{serving_id}")
    def delete_embedding_serving(serving_id: str):
        try: return serving_manager.delete("embedding", serving_id)
        except ValueError as exc: raise _error(exc) from exc

    @app.post("/api/embedding-servings/{serving_id}/test")
    def test_embedding_serving(serving_id: str):
        try: return serving_manager.test("embedding", serving_id)
        except ValueError as exc: raise _error(exc) from exc

    @app.post("/api/embedding-servings/{serving_id}/set-default")
    def default_embedding_serving(serving_id: str):
        try: return serving_manager.set_default("embedding", serving_id)
        except ValueError as exc: raise _error(exc) from exc

    @app.get("/api/embedding-servings/{serving_id}/references")
    def embedding_serving_references(serving_id: str):
        try: return serving_manager.references("embedding", serving_id)
        except ValueError as exc: raise _error(exc) from exc

    @app.get("/api/reranker-servings")
    def reranker_servings(): return serving_manager.list("reranker")

    @app.post("/api/reranker-servings", status_code=201)
    def create_reranker_serving(payload: RerankerServingRequest):
        try: return serving_manager.create("reranker", new_id("rerankerserving"), payload.model_dump())
        except ValueError as exc: raise _error(exc) from exc

    @app.get("/api/reranker-servings/{serving_id}")
    def reranker_serving(serving_id: str):
        try: return serving_manager.get("reranker", serving_id)
        except ValueError as exc: raise _error(exc) from exc

    @app.patch("/api/reranker-servings/{serving_id}")
    def patch_reranker_serving(serving_id: str, payload: RerankerServingPatch):
        try: return serving_manager.update("reranker", serving_id, payload.model_dump(exclude_none=True))
        except ValueError as exc: raise _error(exc) from exc

    @app.delete("/api/reranker-servings/{serving_id}")
    def delete_reranker_serving(serving_id: str):
        try: return serving_manager.delete("reranker", serving_id)
        except ValueError as exc: raise _error(exc) from exc

    @app.post("/api/reranker-servings/{serving_id}/test")
    def test_reranker_serving(serving_id: str):
        try: return serving_manager.test("reranker", serving_id)
        except ValueError as exc: raise _error(exc) from exc

    @app.post("/api/reranker-servings/{serving_id}/set-default")
    def default_reranker_serving(serving_id: str):
        try: return serving_manager.set_default("reranker", serving_id)
        except ValueError as exc: raise _error(exc) from exc

    @app.get("/api/reranker-servings/{serving_id}/references")
    def reranker_serving_references(serving_id: str):
        try: return serving_manager.references("reranker", serving_id)
        except ValueError as exc: raise _error(exc) from exc

    @app.get("/api/document-libraries")
    def document_libraries(keyword: str = "", status: str | None = None):
        return store.list_document_libraries(keyword, status)

    @app.get("/api/dashboard/overview")
    def dashboard_overview():
        result = store.dashboard_overview(app.state.instance.mode)
        result["observability"] = components_snapshot(store, detailed=False)
        return result

    @app.post("/api/document-libraries", status_code=201)
    def create_document_library(payload: DocumentLibraryRequest):
        try: return store.create_document_library(payload.name, payload.description)
        except ValueError as exc: raise _error(exc) from exc

    @app.get("/api/document-libraries/{library_id}/sources")
    def sources(library_id: str, path: str | None = None, keyword: str = "", status: str | None = None,
                file_type: str | None = None, page: int = 1, page_size: int = 50):
        try: return store.list_library_sources(library_id, path=path, keyword=keyword, status=status, file_type=file_type, page=page, page_size=page_size)
        except ValueError as exc: raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/document-libraries/{library_id}/tree")
    def document_tree(library_id: str):
        try: return store.document_tree(library_id)
        except ValueError as exc: raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/document-libraries/{library_id}/template-bindings")
    def document_library_template_bindings(library_id: str):
        try: return store.list_document_library_template_bindings(library_id)
        except ValueError as exc: raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/document-libraries/{library_id}/template-bindings", status_code=201)
    def bind_document_library_template(library_id: str, payload: dict):
        try: return store.bind_document_library_template(library_id, str(payload.get("knowledge_flow_template_id", "")))
        except ValueError as exc: raise _error(exc) from exc

    @app.post("/api/document-libraries/{library_id}/template-bindings/batch", status_code=201)
    def bind_document_library_templates(library_id: str, payload: DocumentTemplateBatchBindingRequest):
        try: return {"bindings": store.bind_document_library_templates(library_id, payload.knowledge_flow_template_ids)}
        except ValueError as exc: raise _error(exc) from exc

    @app.delete("/api/document-libraries/{library_id}/template-bindings/{template_id}")
    def unbind_document_library_template(library_id: str, template_id: str):
        try: return store.unbind_document_library_template(library_id, template_id)
        except ValueError as exc: raise _error(exc) from exc

    @app.post("/api/document-libraries/{library_id}/process", status_code=202)
    def process_document_library(library_id: str):
        try: return {"jobs": store.process_document_library(library_id)}
        except ReviewGateError as exc: raise _review_error(exc) from exc
        except ValueError as exc: raise _error(exc) from exc

    @app.post("/api/document-libraries/{library_id}/process-selected", status_code=202)
    def process_selected_document_sources(library_id: str, payload: SelectedDocumentSourcesRequest):
        try: return {"jobs": store.process_selected_document_sources(library_id, payload.source_ids)}
        except ReviewGateError as exc: raise _review_error(exc) from exc
        except ValueError as exc: raise _error(exc) from exc

    @app.post("/api/document-libraries/{library_id}/sources/import-preflight")
    def source_import_preflight(library_id: str, payload: SourceImportPreflightRequest):
        try: store.get_document_library(library_id)
        except ValueError as exc: raise HTTPException(status_code=404, detail=str(exc)) from exc
        seen, duplicates, unsupported, oversized = set(), [], [], []
        for entry in payload.entries:
            relative_path = str(entry.get("relative_path", ""))
            try:
                from .store import normalise_relative_path
                relative_path, _ = normalise_relative_path(relative_path)
            except ValueError as exc:
                unsupported.append({"relative_path": relative_path, "error": str(exc)}); continue
            suffix = Path(relative_path).suffix.lower()
            if suffix not in SUPPORTED_EXTENSIONS:
                unsupported.append({"relative_path": relative_path, "error": "不支持的文件格式"})
            if int(entry.get("size_bytes", 0) or 0) > MAX_UPLOAD_BYTES:
                oversized.append(relative_path)
            if relative_path in seen or store.source_by_relative_path(library_id, relative_path):
                duplicates.append(relative_path)
            seen.add(relative_path)
        return {"file_count": len(payload.entries), "duplicates": sorted(set(duplicates)), "unsupported": unsupported, "oversized": oversized}

    @app.post("/api/document-libraries/{library_id}/sources/upload", status_code=201)
    async def upload_sources(library_id: str, files: Annotated[list[UploadFile], File()], names: Annotated[list[str] | None, Form()] = None):
        try: store.get_document_library(library_id)
        except ValueError as exc: raise HTTPException(status_code=404, detail=str(exc)) from exc
        results = []

        for index, upload in enumerate(files):
            filename = Path(upload.filename or "upload.txt").name; suffix = Path(filename).suffix.lower()
            if suffix not in SUPPORTED_EXTENSIONS:
                results.append({"filename": filename, "status": "failed", "error": SUPPORTED_FORMAT_MESSAGE}); continue
            stored = None
            try:
                data = await _read_upload(upload)
                stored = objects.put_blob(data, MEDIA_TYPES[suffix])
                source = store.create_source(
                    library_id=library_id, name=(names[index] if names and index < len(names) else Path(filename).stem),
                    filename=filename, blob_uri=stored.blob_uri, sha256=stored.sha256,
                    size_bytes=stored.size_bytes, media_type=MEDIA_TYPES[suffix], relative_path=filename,
                )
                results.append({"filename": filename, "status": "created", "source": source})
            except (ValueError, HTTPException) as exc:
                results.append({"filename": filename, "status": "failed", "error": str(getattr(exc, "detail", exc))})
            except SQLAlchemyError:
                logger.exception("Source upload database persistence failed", extra={"upload_filename": filename, "document_library_id": library_id})
                results.append({"filename": filename, "status": "failed", "error": "文件记录保存失败，请稍后重试"})
        return {"document_library_id": library_id, "results": results}

    @app.post("/api/document-libraries/{library_id}/sources/batch", status_code=201)
    async def import_sources(library_id: str, files: Annotated[list[UploadFile], File()], manifest: Annotated[str, Form()],
                             duplicate_policy: Annotated[Literal["skip", "replace", "keep_both"], Form()] = "skip"):
        try: store.get_document_library(library_id)
        except ValueError as exc: raise HTTPException(status_code=404, detail=str(exc)) from exc
        try: entries = json.loads(manifest)
        except json.JSONDecodeError as exc: raise HTTPException(status_code=422, detail="manifest 必须是 JSON 数组") from exc
        if not isinstance(entries, list) or len(entries) != len(files):
            raise HTTPException(status_code=422, detail="manifest 必须与 files 一一对应")
        if sum(max(0, int(entry.get("size_bytes", 0) or 0)) for entry in entries) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="单次批量上传不能超过 200 MB")
        results = []
        for upload, entry in zip(files, entries, strict=True):
            filename = Path(upload.filename or "upload.txt").name
            relative_path = str(entry.get("relative_path", ""))
            stored = None
            try:
                from .store import normalise_relative_path
                relative_path, _ = normalise_relative_path(relative_path)
                if PurePath(relative_path).name != filename:
                    raise ValueError("relative_path 的文件名必须与上传文件一致")
                suffix = Path(filename).suffix.lower()
                if suffix not in SUPPORTED_EXTENSIONS:
                    raise ValueError(SUPPORTED_FORMAT_MESSAGE)
                current = store.source_by_relative_path(library_id, relative_path)
                if current and current.status not in {"deleted", "deleting"}:
                    if duplicate_policy == "skip":
                        results.append({"filename": filename, "relative_path": relative_path, "status": "skipped", "reason": "同路径文件已存在"}); continue
                    if duplicate_policy == "keep_both":
                        relative_path = store.available_relative_path(library_id, relative_path)
                data = await _read_upload(upload)
                stored = objects.put_blob(data, MEDIA_TYPES[suffix])
                if current and duplicate_policy == "replace":
                    source = store.replace_source(
                        source_id=current.id, filename=filename, blob_uri=stored.blob_uri,
                        sha256=stored.sha256, size_bytes=stored.size_bytes, media_type=MEDIA_TYPES[suffix],
                    )
                    action = source["version_action"]
                    results.append({"filename": filename, "relative_path": relative_path,
                                    "status": action, "source": source})
                else:
                    source = store.create_source(
                        library_id=library_id, name=Path(filename).stem, filename=filename,
                        blob_uri=stored.blob_uri, sha256=stored.sha256,
                        size_bytes=stored.size_bytes, media_type=MEDIA_TYPES[suffix], relative_path=relative_path,
                    )
                    results.append({"filename": filename, "relative_path": relative_path, "status": "renamed" if relative_path != str(entry.get("relative_path")) else "created", "source": source})
            except (ValueError, HTTPException) as exc:
                results.append({"filename": filename, "relative_path": relative_path, "status": "failed", "error": str(getattr(exc, "detail", exc))})
            except SQLAlchemyError:
                logger.exception("Batch source import persistence failed", extra={"upload_filename": filename})
                results.append({"filename": filename, "relative_path": relative_path, "status": "failed", "error": "文件记录保存失败，请稍后重试"})
        return {"document_library_id": library_id, "results": results}

    @app.get("/api/sources")
    def search_sources(keyword: str = "", status: str | None = None, document_library_id: str | None = None):
        return store.list_sources(document_library_id, keyword, status)

    @app.get("/api/sources/{source_id}/versions")
    def source_versions(source_id: str):
        try: return store.source_versions(source_id)
        except ValueError as exc: raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/sources/{source_id}/replace")
    async def replace_source(source_id: str, file: Annotated[UploadFile, File()]):
        try: source = store.source_for_upload(source_id)
        except ValueError as exc: raise HTTPException(status_code=404, detail=str(exc)) from exc
        filename = Path(file.filename or "replacement.txt").name; suffix = Path(filename).suffix.lower()
        if suffix not in SUPPORTED_EXTENSIONS: raise HTTPException(status_code=422, detail=SUPPORTED_FORMAT_MESSAGE)
        data = await _read_upload(file); stored = objects.put_blob(data, MEDIA_TYPES[suffix])
        try:
            result = store.replace_source(
                source_id=source_id, filename=filename, blob_uri=stored.blob_uri,
                sha256=stored.sha256, size_bytes=stored.size_bytes, media_type=MEDIA_TYPES[suffix],
            )
            if result["version_action"] == "confirmation_required":
                return JSONResponse(status_code=409, content=result)
            return JSONResponse(status_code=201 if result["version_action"] == "created" else 200, content=result)
        except (ValueError, SQLAlchemyError) as exc:
            if isinstance(exc, ValueError):
                raise _source_error(exc) from exc
            logger.exception("Source replacement database persistence failed", extra={"source_id": source_id})
            raise HTTPException(status_code=500, detail="文件记录保存失败，请稍后重试") from exc

    @app.post("/api/sources/{source_id}/versions/{version_id}/reactivate")
    def reactivate_source_version(source_id: str, version_id: str, payload: SourceVersionReactivationRequest):
        try:
            return store.reactivate_source_version(
                source_id=source_id, version_id=version_id,
                expected_current_version_id=payload.expected_current_version_id,
            )
        except ValueError as exc:
            if str(exc) == "SOURCE_VERSION_REACTIVATION_STALE":
                raise HTTPException(status_code=409, detail={"code": str(exc), "message": "当前版本已变化，请刷新后重试"}) from exc
            raise _source_error(exc) from exc

    @app.delete("/api/sources/{source_id}")
    def delete_source(source_id: str):
        try: return store.delete_source(source_id)
        except ValueError as exc: raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/sources/{source_id}/retry")
    def retry_source(source_id: str):
        try: return store.retry_source(source_id)
        except ValueError as exc: raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/sources/{source_id}/detail")
    def source_detail(source_id: str, version_id: str | None = None, flow_run_id: str | None = None):
        try: return store.source_detail(source_id, version_id, flow_run_id)
        except ValueError as exc: raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/source-versions/{source_version_id}/parse/retry", status_code=202)
    def retry_parse_job(source_version_id: str):
        try: return store.retry_parse_job(source_version_id)
        except ValueError as exc: raise _error(exc) from exc

    @app.get("/api/parsed-documents/{parsed_document_id}")
    def parsed_document_metadata(parsed_document_id: str):
        try: return store._parsed_document_payload(store.parsed_document(parsed_document_id))
        except ValueError as exc: raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/parsed-documents/{parsed_document_id}/review")
    def review_parsed_document(parsed_document_id: str, payload: ParsedDocumentReviewRequest):
        try: return _review_parsed_document(store, objects, parsed_document_id, payload)
        except ReviewGateError as exc: raise _review_error(exc) from exc
        except ValueError as exc: raise _error(exc) from exc

    @app.post("/api/document-libraries/{library_id}/parsed-documents/review-batch")
    def review_parsed_documents_batch(library_id: str, payload: ParsedDocumentBatchReviewRequest):
        result: dict[str, list] = {
            "approved": [], "already_approved": [], "skipped": [], "failed": [], "dispatches": [],
        }
        for item in payload.items:
            identity = None
            try:
                identity = store.parsed_document_review_identity(item.parsed_document_id)
                if (identity["document_library_id"] != library_id
                        or identity["source_id"] != item.source_id
                        or identity["source_version_id"] != item.source_version_id):
                    result["skipped"].append({"parsed_document_id": item.parsed_document_id,
                                              "reason": "所选文件与解析修订不匹配"})
                    continue
                if (identity["current_source_version_id"] != item.source_version_id
                        or identity["current_parsed_document_id"] != item.parsed_document_id):
                    result["skipped"].append({"parsed_document_id": item.parsed_document_id,
                                              "reason": "文件或解析修订已变化"})
                    continue
                if identity["review_status"] == "approved":
                    result["already_approved"].append({"parsed_document_id": item.parsed_document_id})
                    continue
                parent = store.parsed_document(item.parsed_document_id)
                current_content = read_content(objects, parent)
                review = _review_parsed_document(store, objects, item.parsed_document_id,
                    ParsedDocumentReviewRequest(
                        expected_content_digest=item.expected_content_digest,
                        expected_anchor_map_digest=item.expected_anchor_map_digest,
                        markdown=current_content if parent.kind == "textual" else None,
                        cell_updates=[],
                    ))
                result["approved"].append({
                    "source_id": item.source_id,
                    "parsed_document": review["parsed_document"],
                })
                result["dispatches"].extend(review.get("dispatches") or [])
            except ReviewGateError as exc:
                result["skipped"].append({"parsed_document_id": item.parsed_document_id,
                                          "reason": exc.message, "code": exc.code})
            except ValueError as exc:
                result["failed"].append({"parsed_document_id": item.parsed_document_id,
                                         "reason": str(exc)})
        return result

    @app.get("/api/parsed-documents/{parsed_document_id}/content")
    def parsed_document_content(parsed_document_id: str, sheet: int = 0, offset: int = 0,
                                limit: int = Query(200, ge=1, le=1000)):
        try:
            item = store.parsed_document(parsed_document_id); content = read_content(objects, item)
            if item.kind == "textual":
                return {"kind": item.kind, "content_format": item.content_format, "markdown": content}
            sheets = list(content.get("sheets") or [])
            if sheet < 0 or sheet >= len(sheets): raise ValueError("Sheet 不存在")
            selected = dict(sheets[sheet]); rows = list(selected.pop("rows", []))
            return {"kind": item.kind, "content_format": item.content_format,
                    "sheet_count": len(sheets), "sheet": selected, "rows": rows[offset:offset + limit],
                    "offset": offset, "limit": limit, "total": len(rows)}
        except ValueError as exc: raise _error(exc) from exc

    @app.get("/api/parsed-documents/{parsed_document_id}/anchors")
    def parsed_document_anchors(parsed_document_id: str):
        try: return read_anchors(objects, store.parsed_document(parsed_document_id))
        except ValueError as exc: raise _error(exc) from exc

    @app.get("/api/flow-chunk-sets/{flow_chunk_set_id}/review")
    def flow_chunk_set_review(flow_chunk_set_id: str):
        try: return store.flow_chunk_set_review(flow_chunk_set_id)
        except ValueError as exc: raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/flow-chunk-sets/{flow_chunk_set_id}/freeze")
    def freeze_flow_chunk_set(flow_chunk_set_id: str):
        try: return store.freeze_flow_chunk_set(flow_chunk_set_id)
        except ReviewGateError as exc: raise _review_error(exc) from exc
        except ValueError as exc: raise _error(exc) from exc

    @app.patch("/api/flow-chunks/{chunk_id}")
    def update_flow_chunk(chunk_id: str, payload: FlowChunkUpdateRequest):
        try: return store.update_flow_chunk(chunk_id, payload.content, payload.expected_revision_no)
        except ReviewGateError as exc: raise _review_error(exc) from exc
        except ValueError as exc: raise _error(exc) from exc

    @app.post("/api/flow-chunks/{chunk_id}/split")
    def split_flow_chunk(chunk_id: str, payload: FlowChunkSplitRequest):
        try: return store.split_flow_chunk(chunk_id, payload.parts, payload.expected_revision_no)
        except ReviewGateError as exc: raise _review_error(exc) from exc
        except ValueError as exc: raise _error(exc) from exc

    @app.post("/api/flow-chunks/merge")
    def merge_flow_chunks(payload: FlowChunkMergeRequest):
        try: return store.merge_flow_chunks(payload.chunk_ids, payload.expected_revisions)
        except ReviewGateError as exc: raise _review_error(exc) from exc
        except ValueError as exc: raise _error(exc) from exc

    @app.delete("/api/flow-chunks/{chunk_id}")
    def delete_flow_chunk(chunk_id: str, expected_revision_no: int = Query(ge=1)):
        try: return store.delete_flow_chunk(chunk_id, expected_revision_no)
        except ReviewGateError as exc: raise _review_error(exc) from exc
        except ValueError as exc: raise _error(exc) from exc

    @app.post("/api/flow-chunks/{chunk_id}/review")
    def review_flow_chunk(chunk_id: str, payload: FlowChunkReviewRequest):
        try: return store.review_flow_chunk(chunk_id, payload.status, payload.expected_revision_no)
        except ReviewGateError as exc: raise _review_error(exc) from exc
        except ValueError as exc: raise _error(exc) from exc

    @app.post("/api/flow-chunks/{chunk_id}/reopen")
    def reopen_flow_chunk(chunk_id: str):
        try: return store.reopen_flow_chunk(chunk_id)
        except ReviewGateError as exc: raise _review_error(exc) from exc
        except ValueError as exc: raise _error(exc) from exc

    @app.get("/api/sources/{source_id}/versions/{version_id}/download")
    def download_source(source_id: str, version_id: str):
        try: version = store.source_version_for_download(source_id, version_id)
        except ValueError as exc: raise HTTPException(status_code=404, detail=str(exc)) from exc
        return Response(content=objects.get_blob(version.blob_uri), media_type=version.media_type, headers={
            "Content-Disposition": _content_disposition("attachment", version.original_filename),
        })

    @app.get("/api/sources/{source_id}/versions/{version_id}/preview")
    def preview_source(source_id: str, version_id: str):
        try: version = store.source_version_for_download(source_id, version_id)
        except ValueError as exc: raise HTTPException(status_code=404, detail=str(exc)) from exc
        if version.media_type != "application/pdf" or not version.original_filename.lower().endswith(".pdf"):
            raise HTTPException(status_code=415, detail="原文件内联预览仅支持 PDF；其他格式请使用 ParsedDocument")
        return Response(content=objects.get_blob(version.blob_uri), media_type="application/pdf", headers={
            "Content-Disposition": _content_disposition("inline", version.original_filename),
            "X-Content-Type-Options": "nosniff",
        })

    @app.post("/api/document-deletions/preflight")
    def document_deletion_preflight(payload: DocumentDeletionRequest):
        try: return store.document_deletion_preflight(source_ids=payload.source_ids, document_library_ids=payload.document_library_ids)
        except ValueError as exc: raise _error(exc) from exc

    @app.post("/api/document-deletions", status_code=202)
    def request_document_deletion(payload: DocumentDeletionRequest):
        try: return store.request_document_deletion(source_ids=payload.source_ids, document_library_ids=payload.document_library_ids)
        except ValueError as exc: raise _error(exc) from exc

    @app.post("/api/document-deletion-jobs/{job_id}/retry")
    def retry_document_deletion(job_id: str):
        try: return store.retry_document_deletion(job_id)
        except ValueError as exc: raise _error(exc) from exc

    @app.get("/api/knowledge-libraries")
    def knowledge_libraries(knowledge_type: str | None = None): return store.list_knowledge_libraries(knowledge_type)

    @app.get("/api/knowledge-libraries/{library_id}/delete-check")
    def knowledge_library_delete_check(library_id: str):
        try: return store.knowledge_library_delete_check(library_id)
        except ValueError as exc: raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.delete("/api/knowledge-libraries/{library_id}", status_code=202)
    def delete_knowledge_library(library_id: str):
        try: return store.request_knowledge_library_deletion(library_id)
        except ValueError as exc: raise _error(exc) from exc

    @app.post("/api/knowledge-libraries/deletions/preflight")
    def knowledge_library_deletion_preflight(payload: KnowledgeLibraryDeletionRequest):
        try: return store.knowledge_library_deletion_preflight(payload.library_ids)
        except ValueError as exc: raise _error(exc) from exc

    @app.post("/api/knowledge-libraries/deletions", status_code=202)
    def request_knowledge_library_deletions(payload: KnowledgeLibraryDeletionRequest):
        try: return store.request_knowledge_library_deletions(payload.library_ids)
        except ValueError as exc: raise _error(exc) from exc

    @app.get("/api/knowledge-libraries/{library_id}/deletion-jobs")
    def library_deletion_jobs(library_id: str): return store.list_library_deletion_jobs(library_id)

    @app.post("/api/knowledge-library-deletion-jobs/{job_id}/retry")
    def retry_library_deletion(job_id: str):
        try: return store.retry_library_deletion(job_id)
        except ValueError as exc: raise _error(exc) from exc

    @app.get("/api/knowledge-libraries/{library_id}/items")
    def knowledge_items(library_id: str):
        try: return store.list_knowledge_items(library_id)
        except ValueError as exc: raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/knowledge-libraries/{library_id}/qa-pairs")
    def qa_pairs(library_id: str, q: str = "", status: Literal["active", "inactive", "all"] = "active",
                 review_status: Literal["pending", "approved", "rejected", "all"] = "all",
                 page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200)):
        try: return store.list_qa_pairs(library_id, keyword=q, status=status, review_status=review_status,
                                        page=page, page_size=page_size)
        except ValueError as exc: raise _error(exc) from exc

    @app.patch("/api/knowledge/items/{item_id}")
    def update_knowledge_item(item_id: str, payload: KnowledgeItemUpdateRequest):
        try:
            return store.update_knowledge_item(
                item_id, data=payload.data, canonical_content=payload.canonical_content,
                expected_review_revision=payload.expected_review_revision,
            )
        except ReviewGateError as exc: raise _review_error(exc) from exc
        except ValueError as exc: raise _error(exc) from exc

    @app.post("/api/flow-chunks/review-batch")
    def batch_review_flow_chunks(payload: FlowChunkBatchReviewRequest):
        try: return store.batch_review_flow_chunks(payload.chunk_ids, payload.action, payload.expected_revisions)
        except ReviewGateError as exc: raise _review_error(exc) from exc
        except ValueError as exc: raise _error(exc) from exc

    @app.post("/api/knowledge/items/{item_id}/review")
    def review_knowledge_item(item_id: str, payload: KnowledgeItemReviewRequest):
        try:
            return store.review_knowledge_item(
                item_id, status=payload.status, expected_review_revision=payload.expected_review_revision,
                review_note=payload.review_note,
            )
        except ReviewGateError as exc: raise _review_error(exc) from exc
        except ValueError as exc: raise _error(exc) from exc

    @app.post("/api/knowledge-libraries/{library_id}/review/batch")
    def batch_review_knowledge_items(library_id: str, payload: KnowledgeItemBatchReviewRequest):
        try:
            return store.batch_review_knowledge_items(
                library_id, item_ids=payload.item_ids, action=payload.action,
                expected_revisions=payload.expected_revisions, review_note=payload.review_note,
            )
        except ReviewGateError as exc: raise _review_error(exc) from exc
        except ValueError as exc: raise _error(exc) from exc

    @app.get("/api/knowledge-libraries/{library_id}/review-summary")
    def knowledge_review_summary(library_id: str):
        target_available, target_error = True, None
        try:
            _authoring_connection()
        except HTTPException as exc:
            target_available, target_error = False, str(exc.detail)
        try:
            return store.knowledge_review_summary(
                library_id, target_available=target_available, target_error=target_error,
            )
        except ValueError as exc: raise _error(exc) from exc

    @app.post("/api/knowledge-libraries/{library_id}/vector-publish", status_code=202)
    def publish_knowledge_vectors(library_id: str, payload: KnowledgeVectorPublishRequest):
        _authoring_connection()
        try:
            return store.publish_knowledge_vectors(
                library_id, scope=payload.scope,
                expected_snapshot_digest=payload.expected_snapshot_digest,
                idempotency_key=payload.idempotency_key,
                approve_pending=payload.approve_pending,
            )
        except ReviewGateError as exc: raise _review_error(exc) from exc
        except ValueError as exc: raise _error(exc) from exc

    @app.get("/api/knowledge-libraries/{library_id}/graph")
    def graph_items(library_id: str):
        try: return store.list_knowledge_items(library_id, "graph")
        except ValueError as exc: raise _error(exc) from exc

    @app.get("/api/knowledge-libraries/{library_id}/changes")
    def knowledge_changes(library_id: str): return store.list_changes(library_id)

    @app.get("/api/knowledge-libraries/{library_id}/vector-status")
    def knowledge_vector_status(library_id: str):
        try: return store.vector_status(library_id)
        except ValueError as exc: raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/knowledge-items/{item_id}/sources")
    def knowledge_item_sources(item_id: str):
        try: return store.knowledge_item_sources(item_id)
        except ValueError as exc: raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/knowledge-libraries/{library_id}/graph/entities")
    def graph_entity_search(library_id: str, q: str = "", limit: int = 20):
        try: return store.graph_entity_search(library_id, q, limit)
        except ValueError as exc: raise _error(exc) from exc

    @app.get("/api/knowledge-libraries/{library_id}/graph/overview")
    def graph_overview(library_id: str):
        try: return store.graph_overview(library_id)
        except ValueError as exc: raise _error(exc) from exc

    @app.get("/api/knowledge-libraries/{library_id}/graph/type-facets")
    def graph_type_facets(library_id: str):
        try: return store.graph_type_facets(library_id)
        except ValueError as exc: raise _error(exc) from exc

    @app.get("/api/knowledge-libraries/{library_id}/graph/entities/{entity_id}")
    def graph_entity_detail(library_id: str, entity_id: str):
        try: return store.graph_entity_detail(library_id, entity_id)
        except ValueError as exc: raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/knowledge-libraries/{library_id}/graph/entities/{entity_id}/neighbors")
    def graph_neighbors(library_id: str, entity_id: str, depth: int = 1,
                        entity_types: Annotated[list[str] | None, Query()] = None,
                        relation_types: Annotated[list[str] | None, Query()] = None,
                        confirm_large: bool = False):
        try:
            return store.graph_neighbors(
                library_id, entity_id, depth,
                entity_types=set(entity_types or []),
                relation_types=set(relation_types or []),
                confirm_large=confirm_large,
            )
        except ValueError as exc: raise _error(exc) from exc

    @app.get("/api/knowledge-libraries/{library_id}/graph/entities/{entity_id}/neighbors/preview")
    def graph_neighbor_preview(library_id: str, entity_id: str, depth: int = 1,
                               entity_types: Annotated[list[str] | None, Query()] = None,
                               relation_types: Annotated[list[str] | None, Query()] = None):
        try:
            return store.graph_neighbor_preview(
                library_id, entity_id, depth,
                entity_types=set(entity_types or []),
                relation_types=set(relation_types or []),
            )
        except ValueError as exc: raise _error(exc) from exc

    @app.get("/api/knowledge-libraries/{library_id}/graph/relations/{relation_id}/evidence")
    def graph_relation_evidence(library_id: str, relation_id: str):
        try: return store.graph_relation_evidence(library_id, relation_id)
        except ValueError as exc: raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/knowledge-jobs")
    def knowledge_jobs(): return store.list_knowledge_jobs()

    @app.post("/api/knowledge-jobs", status_code=202)
    def create_knowledge_job(payload: KnowledgeJobRequest):
        try:
            if payload.input_source != "flow_chunk_review_snapshot":
                raise ReviewGateError("BUILTIN_SAMPLE_NOT_ALLOWED", "正式 KnowledgeJob 只能使用真实审核快照")
            return store.create_knowledge_job(payload.source_version_ids, payload.output_library_ids, payload.knowledge_flow_template_id)
        except ReviewGateError as exc: raise _review_error(exc) from exc
        except ValueError as exc: raise _error(exc) from exc

    @app.post("/api/knowledge-jobs/batch-actions")
    def manage_knowledge_jobs(payload: KnowledgeJobBatchActionRequest):
        try: return store.manage_jobs(payload.job_ids, payload.action)
        except ValueError as exc: raise _error(exc) from exc

    @app.get("/api/knowledge-jobs/{job_id}/logs")
    def knowledge_job_logs(job_id: str):
        try: return store.job_logs(job_id)
        except ValueError as exc: raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/knowledge-jobs/{job_id}/chunk-generations")
    def knowledge_job_chunk_generations(job_id: str, failed_only: bool = False):
        try: return store.job_generation_results(job_id, failed_only=failed_only)
        except ValueError as exc: raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/knowledge-jobs/{job_id}/input-preparations")
    def knowledge_job_input_preparations(job_id: str):
        try: return store.job_input_preparations(job_id)
        except ValueError as exc: raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/knowledge-jobs/{job_id}/regenerate", status_code=202)
    def regenerate_knowledge_job(job_id: str):
        try: return store.regenerate_knowledge_job(job_id, reprepare=False)
        except ValueError as exc: raise _error(exc) from exc

    @app.post("/api/knowledge-jobs/{job_id}/reprepare-input", status_code=202)
    def reprepare_knowledge_job_input(job_id: str):
        try: return store.regenerate_knowledge_job(job_id, reprepare=True)
        except ValueError as exc: raise _error(exc) from exc

    @app.post("/api/knowledge-jobs/{job_id}/retry-failed", status_code=202)
    def retry_failed_generation_units(job_id: str):
        try: return store.manage_jobs([job_id], "retry")[0]
        except ValueError as exc: raise _error(exc) from exc

    @app.get("/api/developer/knowledge-types")
    def knowledge_types():
        return store.list_knowledge_type_definitions()

    @app.post("/api/developer/knowledge-types", status_code=201)
    def create_knowledge_type(payload: CreateKnowledgeTypeRequest):
        try: return store.create_knowledge_type_semantic(
            payload.code, payload.name, payload.icon, payload.type_schema, payload.canonical_field,
            payload.identity_fields, payload.source_policy,
        )
        except ValueError as exc: raise _error(exc) from exc

    @app.post("/api/developer/knowledge-types/{type_id}/revisions", status_code=201)
    def revise_knowledge_type(type_id: str, payload: KnowledgeTypeSemanticRequest):
        try: return store.revise_knowledge_type_semantic(
            type_id, payload.type_schema, payload.canonical_field, payload.identity_fields, payload.source_policy,
        )
        except ValueError as exc: raise _error(exc) from exc

    @app.post("/api/developer/knowledge-types/{type_id}/storage-bindings", status_code=201)
    def revise_knowledge_type_storage_bindings(type_id: str, payload: KnowledgeTypeStorageBindingsRequest):
        try:
            if payload.managed_collection_name.strip() and payload.reuse_managed_collection_id:
                raise ValueError("新 Collection 名与复用受管 Collection 不能同时设置")
            return store.revise_knowledge_type_storage_bindings(
                type_id, payload.index_profile_ids,
                managed_collection_name=payload.managed_collection_name,
                reuse_managed_collection_id=payload.reuse_managed_collection_id,
            )
        except ValueError as exc: raise _error(exc) from exc

    @app.post("/api/developer/knowledge-types/{type_id}/validate")
    def validate_knowledge_type(type_id: str):
        try: return store.validate_knowledge_type(type_id)
        except ValueError as exc: raise _error(exc) from exc

    @app.post("/api/developer/knowledge-types/{type_id}/publish")
    def publish_knowledge_type(type_id: str):
        try:
            from .provisioning import ManagedCollectionProvisioner
            requirements = store.knowledge_type_publication_requirements(type_id)
            service = _vector_sync_service()
            for requirement in requirements:
                if requirement["collection_policy"] == "managed":
                    if not service.milvus:
                        raise ValueError("未配置 verified Authoring Target，不能 Provision 受管 Collection")
                    result = ManagedCollectionProvisioner(store, service.milvus).reconcile_one(requirement["managed_collection_id"])
                    if result["status"] != "ready":
                        raise ValueError(result.get("error") or "受管 Collection Provision 失败")
                else:
                    index_validator(requirement["collection_name"], requirement["fields"], requirement["dimension"])
            return store.publish_knowledge_type(type_id)
        except ValueError as exc: raise _error(exc) from exc

    @app.get("/api/developer/standard-pipelines")
    def standard_pipelines():
        return [{"code": "common", "steps": [
            "ParseJob / ParsedDocument", "Document Chunker", "FlowChunk 人工审核 Gate", "Knowledge Flow",
            "Knowledge Sink", "Embedding", "Milvus", "Ready / Routing",
        ]}]

    @app.get("/api/developer/samples")
    def developer_samples(purpose: str = ""):
        return samples.list(purpose)

    @app.get("/api/developer/samples/{sample_code}")
    def developer_sample(sample_code: str):
        try: return samples.get(sample_code)
        except ValueError as exc: raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/developer/operator-catalog")
    def operator_catalog(q: str = "", category: str = "", knowledge_type: str = "", exposure: str = "", status: str = "",
                         include_internal: bool = True, surface: str = "", source: str = "", catalog_group: str = ""):
        return store.list_operator_catalog(include_internal=include_internal, query=q, category=category, knowledge_type=knowledge_type,
                                           exposure=exposure, status=status, surface=surface, source=source, catalog_group=catalog_group)

    @app.post("/api/developer/operator-catalog/candidates")
    def operator_candidates(payload: OperatorCandidatesRequest):
        try: return store.operator_candidates(
            payload.definition, payload.output_types, payload.source_node_id, payload.source_port,
            node_id=payload.node_id, direction=payload.direction,
            include_incompatible=payload.include_incompatible,
        )
        except ValueError as exc: raise _error(exc) from exc

    @app.get("/api/developer/operator-catalog/facets")
    def operator_catalog_facets(): return store.operator_catalog_facets()

    @app.get("/api/developer/operator-plugins")
    def operator_plugins():
        from .operator_plugins import OperatorPluginService
        return OperatorPluginService(store).versions()

    @app.post("/api/developer/operator-plugins", status_code=201)
    def register_operator_plugin(payload: OperatorManifestRequest):
        from .operator_plugins import OperatorPluginService
        try: return OperatorPluginService(store).register(payload.manifest)
        except (ValueError, KeyError, TypeError) as exc: raise _error(ValueError(str(exc))) from exc

    @app.post("/api/developer/operator-plugins/{code}/versions/{version}/validate", status_code=202)
    def validate_operator_plugin(code: str, version: int, background_tasks: BackgroundTasks):
        from .operator_plugins import OperatorPluginService
        service = OperatorPluginService(store)
        try: result = service.start_validation(code, version)
        except ValueError as exc: raise _error(exc) from exc
        background_tasks.add_task(service.validate, result["id"])
        return result

    @app.get("/api/developer/operator-validations/{run_id}")
    def operator_validation(run_id: str):
        from .operator_plugins import OperatorPluginService
        try: return OperatorPluginService(store).report(run_id)
        except ValueError as exc: raise _error(exc) from exc

    @app.post("/api/developer/operator-plugins/{code}/versions/{version}/publish")
    def publish_operator_plugin(code: str, version: int):
        from .operator_plugins import OperatorPluginService
        try: return OperatorPluginService(store).publish(code, version)
        except ValueError as exc: raise _error(exc) from exc

    @app.get("/api/developer/operator-catalog/{code}")
    def operator_catalog_detail(code: str):
        try: return store.operator_catalog_detail(code)
        except ValueError as exc: raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/developer/prompt-templates")
    def prompt_templates(status: str = "", knowledge_type: str = ""):
        return store.list_prompt_templates(status=status, knowledge_type=knowledge_type)

    @app.post("/api/developer/prompt-templates", status_code=201)
    def create_prompt_template(payload: PromptTemplateRequest):
        try: return store.create_prompt_template(payload.code, payload.name, payload.body, payload.input_schema, payload.output_schema, payload.knowledge_types)
        except ValueError as exc: raise _error(exc) from exc

    @app.post("/api/developer/prompt-templates/{prompt_id}/publish")
    def publish_prompt_template(prompt_id: str):
        try: return store.publish_prompt_template(prompt_id)
        except ValueError as exc: raise _error(exc) from exc

    @app.get("/api/developer/quality-profiles")
    def quality_profiles(status: str = "", knowledge_type: str = ""):
        return store.list_quality_profiles(status=status, knowledge_type=knowledge_type)

    @app.post("/api/developer/quality-profiles", status_code=201)
    def create_quality_profile(payload: QualityProfileRequest):
        try: return store.create_quality_profile(payload.code, payload.name, payload.rules, payload.knowledge_types)
        except ValueError as exc: raise _error(exc) from exc

    @app.post("/api/developer/quality-profiles/{profile_id}/publish")
    def publish_quality_profile(profile_id: str):
        try: return store.publish_quality_profile(profile_id)
        except ValueError as exc: raise _error(exc) from exc

    @app.get("/api/developer/flow-subgraphs")
    def flow_subgraphs():
        return store.list_subflows()

    @app.post("/api/developer/flow-subgraphs", status_code=201)
    def create_flow_subgraph(payload: SubflowCreateRequest):
        try: return store.create_subflow(**payload.model_dump())
        except ValueError as exc: raise _error(exc) from exc

    @app.get("/api/developer/flow-subgraphs/{subflow_id}/revisions")
    def flow_subgraph_revisions(subflow_id: str):
        try: return store.subflow_revisions(subflow_id)
        except ValueError as exc: raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/developer/flow-subgraphs/{subflow_id}/revisions/{revision}/references")
    def flow_subgraph_references(subflow_id: str, revision: int):
        try: return store.subflow_references(subflow_id, revision)
        except ValueError as exc: raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/developer/flow-subgraphs/{subflow_id}/revisions/{revision}")
    def flow_subgraph_revision(subflow_id: str, revision: int):
        try: return store.subflow_revision_detail(subflow_id, revision)
        except ValueError as exc: raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/developer/flow-subgraphs/{subflow_id}/revisions/{revision}/copy", status_code=201)
    def copy_flow_subgraph_revision(subflow_id: str, revision: int):
        try: return store.copy_subflow_draft(subflow_id, revision)
        except ValueError as exc: raise _error(exc) from exc

    @app.put("/api/developer/flow-subgraphs/{subflow_id}/revisions/{revision}")
    def update_flow_subgraph_revision(subflow_id: str, revision: int, payload: SubflowRevisionRequest):
        try: return store.update_subflow_draft(subflow_id, revision, payload.definition, payload.description, payload.input_contract, payload.output_contract)
        except ValueError as exc: raise _error(exc) from exc

    @app.post("/api/developer/flow-subgraphs/{subflow_id}/revisions/{revision}/validate")
    def validate_flow_subgraph_revision(subflow_id: str, revision: int):
        try:
            return {"id": subflow_id, "revision": revision, **store.validate_subflow_draft(subflow_id, revision)}
        except ValueError as exc: raise _error(exc) from exc

    @app.post("/api/developer/flow-subgraphs/{subflow_id}/revisions/{revision}/publish")
    def publish_flow_subgraph_revision(subflow_id: str, revision: int):
        try: return store.publish_subflow_draft(subflow_id, revision)
        except ValueError as exc: raise _error(exc) from exc

    @app.get("/api/developer/knowledge-flow-templates")
    def flow_templates():
        return store.list_flow_templates()

    @app.get("/api/developer/managed-flow-templates")
    def managed_flow_templates():
        return store.list_managed_flow_templates()

    @app.get("/api/developer/graph-entity-types")
    def graph_entity_types():
        return entity_type_catalog()

    @app.post("/api/developer/graph-entity-types/resolve")
    def resolve_graph_entity_types(body: EntityTypesResolveRequest):
        # A pure editor operation: no template, revision or database is written.
        try:
            return {"entity_types": resolve_entity_types(body.entity_types, body.action, label=body.label,
                                                         code=body.code, description=body.description)}
        except ValueError as exc:
            raise _error(exc) from exc

    @app.post("/api/developer/managed-flow-templates/{code}/materialize")
    def materialize_managed_flow(code: str):
        try: return store.materialize_managed_flow(code)
        except ValueError as exc: raise _error(exc) from exc

    @app.post("/api/developer/flow-compiler/preview")
    def preview_flow_compilation(payload: FlowCompilerPreviewRequest):
        try: return store.preview_flow_compilation(payload.authoring_mode, payload.managed_template_code, payload.output_types, payload.definition)
        except ValueError as exc: raise _error(exc) from exc

    @app.post("/api/developer/knowledge-flow-templates/{template_id}/detach-to-advanced")
    def detach_flow_template_to_advanced(template_id: str, preview: bool = False):
        try: return store.detach_flow_template_to_advanced(template_id, preview=preview)
        except ValueError as exc: raise _error(exc) from exc

    @app.post("/api/developer/knowledge-flow-templates", status_code=201)
    def create_flow_template(payload: FlowTemplateRequest):
        try: return store.create_flow_template(payload.code, payload.name, payload.output_types, payload.definition, authoring_mode=payload.authoring_mode, managed_template_code=payload.managed_template_code,
                                             derived_from_template_id=payload.derived_from_template_id, derived_from_revision_id=payload.derived_from_revision_id)
        except ValueError as exc: raise _error(exc) from exc

    @app.put("/api/developer/knowledge-flow-templates/{template_id}")
    def update_flow_template(template_id: str, payload: FlowTemplateRequest):
        try: return store.update_flow_template(template_id, payload.name, payload.output_types, payload.definition, authoring_mode=payload.authoring_mode, managed_template_code=payload.managed_template_code, expected_definition_checksum=payload.expected_definition_checksum)
        except ValueError as exc: raise _error(exc) from exc

    @app.delete("/api/developer/knowledge-flow-templates/{template_id}")
    def archive_flow_template(template_id: str):
        try: return store.archive_flow_template(template_id)
        except ValueError as exc: raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/developer/knowledge-flow-templates/{template_id}/set-default")
    def set_default_flow_template(template_id: str):
        try: return store.set_default_flow_template(template_id)
        except ValueError as exc: raise _error(exc) from exc

    @app.post("/api/developer/knowledge-flow-templates/{template_id}/validate")
    def validate_flow_template(template_id: str):
        try: return store.validate_flow_template(template_id)
        except ValueError as exc: raise _error(exc) from exc

    @app.post("/api/developer/knowledge-flow-templates/{template_id}/publish")
    def publish_flow_template(template_id: str, payload: FlowPublishRequest):
        try: return store.publish_flow_template(template_id, **payload.model_dump())
        except ReviewGateError as exc: raise _review_error(exc) from exc
        except ValueError as exc: raise _error(exc) from exc

    @app.post("/api/developer/graph-prompts/preview")
    def preview_graph_prompt(body: GraphPromptPreviewRequest):
        try: return store.preview_graph_prompt(body.definition, body.node_id)
        except ValueError as exc: raise _error(exc) from exc

    @app.post("/api/developer/knowledge-flow-templates/{template_id}/sample-run")
    def sample_run_flow_template(template_id: str, payload: TemplateSampleRequest):
        try:
            checked = store.validate_flow_template(template_id)
            return {**preview_template_definition(checked["definition"], payload.sample_id, compiled_definition=checked["compiled_definition"]), "template_id": template_id, "revision": checked["revision"]}
        except ValueError as exc: raise _error(exc) from exc

    @app.get("/api/developer/execution-snapshots/{snapshot_id}")
    def execution_snapshot(snapshot_id: str):
        try: return store.execution_snapshot_detail(snapshot_id)
        except ValueError as exc: raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/developer/debug-runs/options")
    def debug_run_options(template_id: str, revision_kind: Literal["draft", "published"] = "draft"):
        try: return store.debug_run_options(template_id, revision_kind)
        except ValueError as exc: raise _error(exc) from exc

    @app.post("/api/developer/flow-compiler/resolve")
    def resolve_standard_flow(payload: FlowCompilerPreviewRequest):
        if payload.authoring_mode != "standard":
            raise HTTPException(status_code=422, detail="只读物化仅适用于 Standard")
        try: return store.resolve_standard_flow(payload.managed_template_code, payload.output_types, payload.definition)
        except ValueError as exc: raise _error(exc) from exc

    @app.post("/api/developer/debug-runs/preflight")
    def debug_run_preflight(payload: DebugRunConfigRequest):
        try:
            return store.debug_run_preflight(
                template_id=payload.template_id, revision_id=payload.revision_id,
                expected_compiled_checksum=payload.expected_compiled_checksum,
                flow_chunk_review_snapshot_ids=payload.flow_chunk_review_snapshot_ids,
                sink_library_bindings=payload.sink_library_bindings,
                input_source=payload.input_source, sample_code=payload.sample_code,
            )
        except RuntimeError as exc: raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc: raise _error(exc) from exc

    @app.post("/api/developer/debug-runs", status_code=202)
    def create_debug_run(payload: DebugRunConfigRequest):
        try:
            return store.create_debug_run(
                template_id=payload.template_id, revision_id=payload.revision_id,
                expected_compiled_checksum=payload.expected_compiled_checksum,
                flow_chunk_review_snapshot_ids=payload.flow_chunk_review_snapshot_ids,
                sink_library_bindings=payload.sink_library_bindings,
                idempotency_key=payload.idempotency_key or str(uuid.uuid4()),
                input_source=payload.input_source, sample_code=payload.sample_code,
            )
        except RuntimeError as exc: raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc: raise _error(exc) from exc

    @app.get("/api/developer/debug-runs/{flow_run_id}/flow-materialization")
    def debug_run_materialization(flow_run_id: str):
        try: return store.debug_run_materialization(flow_run_id)
        except ValueError as exc: raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/developer/debug-runs/{flow_run_id}/apply-to-draft")
    def apply_debug_run_to_draft(flow_run_id: str, payload: ApplyDebugRunRequest):
        try:
            return store.apply_debug_run_to_draft(
                flow_run_id, expected_revision_id=payload.expected_revision_id,
                expected_definition_checksum=payload.expected_definition_checksum,
                idempotency_key=payload.idempotency_key,
            )
        except RuntimeError as exc: raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc: raise _error(exc) from exc

    @app.post("/api/developer/debug-runs/{flow_run_id}/save-as-flow", status_code=201)
    def save_debug_run_as_flow(flow_run_id: str, payload: SaveDebugRunAsFlowRequest):
        try: return store.save_debug_run_as_flow(
            flow_run_id, name=payload.name, description=payload.description,
            idempotency_key=payload.idempotency_key,
        )
        except RuntimeError as exc: raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc: raise _error(exc) from exc

    @app.get("/api/developer/flow-runs")
    def flow_runs():
        return store.list_flow_runs()

    @app.get("/api/developer/flow-runs/capabilities")
    def flow_run_capabilities():
        return {"derived_runs_enabled": resolved.derived_runs_enabled,
                "derived_run_commit_enabled": resolved.derived_run_commit_enabled,
                "debug_full_enabled": True, "debug_replay_enabled": True,
                "debug_sink_policy": "preview_only",
                "cancellation": "cooperative"}

    @app.get("/api/developer/flow-runs/{flow_run_id}")
    def flow_run(flow_run_id: str):
        try: return store.flow_run_detail(flow_run_id)
        except ValueError as exc: raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/developer/flow-runs/{flow_run_id}/sink-previews/{preview_id}/candidates")
    def sink_preview_candidates(flow_run_id: str, preview_id: str, offset: int = 0, limit: int = 50):
        try: return store.sink_preview_candidates(flow_run_id, preview_id, offset, limit)
        except ValueError as exc: raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/developer/flow-runs/{flow_run_id}/derived-runs", status_code=202)
    def create_derived_flow_run(flow_run_id: str, payload: DerivedRunRequest):
        if not store.flow_run_is_debug(flow_run_id) and not resolved.derived_runs_enabled:
            raise HTTPException(status_code=403, detail="业务历史 Run 派生功能未启用")
        try: return store.create_derived_run(flow_run_id, payload.mode, payload.node_id, payload.parameter_overrides, payload.idempotency_key)
        except ValueError as exc: raise _error(exc) from exc

    @app.post("/api/developer/flow-runs/{flow_run_id}/cancel")
    def cancel_derived_flow_run(flow_run_id: str):
        if not store.flow_run_is_debug(flow_run_id) and not resolved.derived_runs_enabled:
            raise HTTPException(status_code=403, detail="业务历史 Run 派生功能未启用")
        try: return store.cancel_flow_run(flow_run_id)
        except ValueError as exc: raise _error(exc) from exc

    @app.post("/api/developer/flow-runs/{flow_run_id}/persist-parameters")
    def persist_derived_parameters(flow_run_id: str, payload: PersistDerivedParametersRequest):
        if not resolved.derived_runs_enabled: raise HTTPException(status_code=403, detail="派生 Run 功能未启用")
        try: return store.persist_derived_parameters(flow_run_id, payload.node_id, payload.parameters)
        except ValueError as exc: raise _error(exc) from exc

    @app.post("/api/developer/flow-runs/{flow_run_id}/commit")
    def commit_derived_flow_run(flow_run_id: str, payload: CommitDerivedRunRequest):
        if not resolved.derived_run_commit_enabled: raise HTTPException(status_code=403, detail="派生 Run 正式提交功能未启用")
        try: return store.commit_derived_run(flow_run_id, payload.preview_checksum, payload.idempotency_key)
        except RuntimeError as exc: raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc: raise _error(exc) from exc

    @app.get("/api/developer/flow-runs/{flow_run_id}/events")
    def flow_run_events(flow_run_id: str, after: int = 0, limit: int = 200):
        try: return store.flow_run_events(flow_run_id, after, limit)
        except ValueError as exc: raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/developer/artifacts/{artifact_id}")
    def artifact_detail(artifact_id: str):
        try: return store.artifact_detail(artifact_id)
        except ValueError as exc: raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/developer/artifacts/{artifact_id}/content")
    def artifact_content(artifact_id: str, offset: int = 0, limit: int = 100):
        try: return store.artifact_content(artifact_id, offset, limit)
        except ValueError as exc: raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/developer/vector-indexes")
    def vector_indexes():
        profiles = store.list_index_profiles()
        observed, capacity = {}, []
        authoring_milvus = {"status": "ready", "message": "知识生产 Milvus 当前可用"}
        try:
            connection = milvus_resolver.authoring(app.state.instance.id)
        except ValueError:
            connection = None
            authoring_milvus = {
                "status": "not_configured",
                "message": "当前实例尚未配置默认知识写入目标",
            }
        if connection:
            try:
                observed = {
                    item["collection_name"]: item
                    for item in MilvusInventoryService.from_connection(store, connection).collections()
                }
                capacity = VectorSyncService.from_connection(store, connection).capacity_report()
            except Exception:
                observed = {}
                authoring_milvus = {
                    "status": "unavailable",
                    "message": "知识生产 Milvus 当前不可用；环境摘要已降级",
                }
        if not capacity:
            capacity = VectorSyncService(store).capacity_report()
            reason = authoring_milvus["message"]
            capacity = [
                {**item, "reason": reason} if item.get("reason") == "Milvus 未配置" else item
                for item in capacity
            ]
        for profile in profiles:
            collection = observed.get(profile["collection_name"]) or {}
            serving_dimension = (profile.get("embedding_serving") or {}).get("dimension")
            profile_dimension = profile.get("dimension")
            collection_dimension = collection.get("dimension")
            profile["vector_contract"] = {
                "embedding_serving_dimension": serving_dimension,
                "index_profile_dimension": profile_dimension,
                "milvus_collection_dimension": collection_dimension,
                "compatible": serving_dimension == profile_dimension and (
                    collection_dimension is None or collection_dimension == profile_dimension
                ),
            }
        return {"profiles": profiles, "managed_collections": store.list_managed_collections(),
                "capacity": capacity, "authoring_milvus": authoring_milvus}

    @app.get("/api/vector-storage/overview")
    def vector_storage_overview():
        try:
            return _milvus_inventory_service().overview()
        except (ValueError, HTTPException) as exc:
            return {"configured": False, "healthy": False, "error_code": "MILVUS_NOT_CONFIGURED",
                    "error_message": str(getattr(exc, "detail", exc))}

    @app.get("/api/vector-storage/collections")
    def vector_storage_collections(
        q: str = "",
        knowledge_type: str = "",
        status: str = "",
        only_anomaly: bool = False,
        only_unused: bool = False,
        only_managed: bool = False,
    ):
        if status and status not in INVENTORY_STATUSES:
            raise HTTPException(status_code=422, detail="向量库存状态筛选无效")
        service = _milvus_inventory_service()
        if not service.milvus:
            raise HTTPException(status_code=503, detail="verified Authoring Target 未配置")
        try:
            return service.collections(
                q=q, knowledge_type=knowledge_type, status=status,
                only_anomaly=only_anomaly, only_unused=only_unused,
                only_managed=only_managed,
            )
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"Milvus 库存读取失败：{exc}") from exc

    @app.get("/api/vector-storage/collections/{collection_name}")
    def vector_storage_collection(collection_name: str):
        service = _milvus_inventory_service()
        if not service.milvus:
            raise HTTPException(status_code=503, detail="verified Authoring Target 未配置")
        try:
            return service.collection_detail(collection_name)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"Milvus Collection 读取失败：{exc}") from exc

    @app.get("/api/vector-storage/collections/{collection_name}/partitions/{partition_name}")
    def vector_storage_partition(collection_name: str, partition_name: str):
        service = _milvus_inventory_service()
        if not service.milvus:
            raise HTTPException(status_code=503, detail="verified Authoring Target 未配置")
        try:
            return service.partition_detail(collection_name, partition_name)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"Milvus Partition 读取失败：{exc}") from exc

    @app.post("/api/vector-storage/collections/{collection_name}/partitions/{partition_name}/verify")
    def verify_vector_storage_partition(collection_name: str, partition_name: str):
        try:
            return _milvus_inventory_service().verify_partition(collection_name, partition_name)
        except ValueError as exc:
            raise _error(exc) from exc
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"Milvus Partition 校验失败：{exc}") from exc

    @app.post("/api/vector-storage/collections/{collection_name}/partitions/{partition_name}/load")
    def load_vector_storage_partition(collection_name: str, partition_name: str):
        try:
            return _milvus_inventory_service().load_partition(collection_name, partition_name)
        except ValueError as exc:
            raise _error(exc) from exc
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"Milvus Partition 加载失败：{exc}") from exc

    @app.post("/api/vector-storage/collections/{collection_name}/partitions/{partition_name}/release")
    def release_vector_storage_partition(collection_name: str, partition_name: str):
        try:
            return _milvus_inventory_service().release_partition(collection_name, partition_name)
        except ValueError as exc:
            raise _error(exc) from exc
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"Milvus Partition 释放失败：{exc}") from exc

    @app.get("/api/developer/managed-collections")
    def managed_collections(): return store.list_managed_collections()

    @app.post("/api/developer/managed-collections/{collection_id}/reconcile")
    def reconcile_managed_collection(collection_id: str):
        from .provisioning import ManagedCollectionProvisioner
        service = _vector_sync_service()
        if not service.milvus:
            raise HTTPException(status_code=503, detail="verified Authoring Target 未配置")
        try: return ManagedCollectionProvisioner(store, service.milvus).reconcile_one(collection_id)
        except ValueError as exc: raise _error(exc) from exc

    @app.get("/api/developer/managed-collections/{collection_id}/delete-check")
    def managed_collection_delete_check(collection_id: str):
        from .provisioning import ManagedCollectionDeletionService
        try: return ManagedCollectionDeletionService(store, _authoring_connection().client()).preflight(collection_id)
        except ValueError as exc: raise _error(exc) from exc

    @app.delete("/api/developer/managed-collections/{collection_id}", status_code=202)
    def delete_managed_collection(collection_id: str):
        from .provisioning import ManagedCollectionDeletionService
        try: return ManagedCollectionDeletionService(store, _authoring_connection().client()).request_delete(collection_id)
        except ValueError as exc: raise _error(exc) from exc

    @app.get("/api/developer/managed-collections/{collection_id}/deletion-jobs")
    def managed_collection_deletion_jobs(collection_id: str):
        return store.list_managed_collection_deletion_jobs(collection_id)

    @app.post("/api/developer/managed-collection-deletion-jobs/{job_id}/retry")
    def retry_managed_collection_deletion(job_id: str):
        try: return store.retry_managed_collection_deletion(job_id)
        except ValueError as exc: raise _error(exc) from exc

    @app.post("/api/developer/index-profiles", status_code=201)
    def create_index_profile(payload: IndexProfileRequest):
        policy = payload.collection_policy or ("managed" if payload.collection_mode == "create" else "external")
        try:
            if payload.collection_mode and payload.collection_policy \
                    and policy != ("managed" if payload.collection_mode == "create" else "external"):
                raise ValueError("collection_mode 与 collection_policy 冲突")
            return store.create_index_profile(payload.code, payload.knowledge_type, payload.collection_name, payload.embedding_code,
                                              payload.embedding_model, payload.dimension, payload.metric_type, payload.endpoint_ref, payload.fields,
                                              collection_policy=policy, collection_mode=payload.collection_mode,
                                               storage_schema=payload.storage_schema, index_spec=payload.index_spec,
                                               reuse_managed_collection_id=payload.reuse_managed_collection_id,
                                               embedding_serving_id=payload.embedding_serving_id,
                                               embedding_input=payload.embedding_input)
        except ValueError as exc: raise _error(exc) from exc

    @app.post("/api/developer/index-profiles/{profile_id}/revisions", status_code=201)
    def revise_index_profile(profile_id: str, payload: IndexProfileRequest):
        policy = payload.collection_policy or ("managed" if payload.collection_mode == "create" else "external")
        try:
            if payload.collection_mode and payload.collection_policy \
                    and policy != ("managed" if payload.collection_mode == "create" else "external"):
                raise ValueError("collection_mode 与 collection_policy 冲突")
            return store.revise_index_profile(profile_id, payload.collection_name, payload.embedding_code, payload.embedding_model,
                                              payload.dimension, payload.metric_type, payload.endpoint_ref, payload.fields,
                                              collection_policy=policy, storage_schema=payload.storage_schema,
                                               index_spec=payload.index_spec,
                                               reuse_managed_collection_id=payload.reuse_managed_collection_id,
                                               embedding_serving_id=payload.embedding_serving_id,
                                               embedding_input=payload.embedding_input)
        except ValueError as exc: raise _error(exc) from exc

    def index_validator(collection_name: str, fields: dict, dimension: int) -> None:
        connection = _authoring_connection()
        V7Milvus(connection.uri, connection.token).validate_collection(collection_name, fields, dimension)

    @app.post("/api/developer/index-profiles/{profile_id}/validate")
    def validate_index_profile(profile_id: str):
        try: return store.validate_index_profile(profile_id, index_validator)
        except ValueError as exc: raise _error(exc) from exc

    @app.post("/api/developer/index-profiles/{profile_id}/publish")
    def publish_index_profile(profile_id: str):
        try:
            from .provisioning import ManagedCollectionProvisioner
            requirement = store.index_profile_publication_requirement(profile_id)
            if requirement["collection_policy"] == "managed":
                service = _vector_sync_service()
                if not service.milvus:
                    raise ValueError("未配置 verified Authoring Target，不能 Provision 受管 Collection")
                result = ManagedCollectionProvisioner(store, service.milvus).reconcile_one(requirement["managed_collection_id"])
                if result["status"] != "ready":
                    raise ValueError(result.get("error") or "受管 Collection Provision 失败")
            else:
                index_validator(requirement["collection_name"], requirement["fields"], requirement["dimension"])
            return store.publish_index_profile(profile_id, index_validator)
        except ValueError as exc: raise _error(exc) from exc

    @app.post("/api/developer/index-profiles/{profile_id}/archive")
    def archive_index_profile(profile_id: str):
        try: return store.archive_index_profile(profile_id)
        except ValueError as exc: raise _error(exc) from exc

    @app.get("/api/vector-sync-jobs")
    def vector_sync_jobs(knowledge_library_id: str | None = None): return store.list_vector_sync_jobs(knowledge_library_id)

    @app.post("/api/vector-sync-jobs/{sync_job_id}/run")
    def run_vector_sync(sync_job_id: str):
        try: return _vector_sync_service().run(sync_job_id)
        except ValueError as exc: raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.get("/api/projects")
    def projects(): return store.list_projects(allowed_deployment_id=app.state.instance.bound_deployment_id if app.state.instance.mode == "local" else None)

    @app.post("/api/projects", status_code=201)
    def create_project(payload: ProjectRequest):
        if app.state.instance.mode != "central":
            raise HTTPException(status_code=403, detail="Local 实例不能创建 Project")
        try: return store.create_project(payload.name)
        except ValueError as exc: raise _error(exc) from exc

    @app.post("/api/projects/{project_id}/tasks", status_code=201)
    def create_project_task(project_id: str, payload: ProjectTaskRequest):
        try: return store.create_project_task(project_id, payload.code, payload.name, payload.knowledge_type, payload.description)
        except ValueError as exc: raise _error(exc) from exc

    @app.get("/api/milvus-targets")
    def milvus_targets():
        if app.state.instance.mode != "central":
            raise HTTPException(status_code=403, detail="只有智能中心可以管理 Milvus 服务注册表")
        return store.list_milvus_targets()

    @app.post("/api/milvus-targets", status_code=201)
    def create_milvus_target(payload: MilvusTargetRequest):
        if app.state.instance.mode != "central": raise HTTPException(status_code=403, detail="Local 实例不能创建 Milvus Target")
        try: return milvus_targets_service.create(payload.name, payload.milvus_url, payload.token)
        except StaleMilvusVerification as exc: raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc: raise _error(exc) from exc

    @app.patch("/api/milvus-targets/{target_id}")
    def patch_milvus_target(target_id: str, payload: MilvusTargetPatch):
        if app.state.instance.mode != "central":
            raise HTTPException(status_code=403, detail="只有智能中心可以管理 Milvus 服务注册表")
        try: return milvus_targets_service.patch(target_id, **payload.model_dump())
        except StaleMilvusVerification as exc: raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc: raise _error(exc) from exc

    @app.post("/api/milvus-targets/{target_id}/verify")
    def verify_milvus_target(target_id: str):
        if app.state.instance.mode != "central":
            raise HTTPException(status_code=403, detail="只有智能中心可以验证 Milvus 服务")
        try: return milvus_targets_service.verify(target_id)
        except StaleMilvusVerification as exc: raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc: raise _error(exc) from exc

    @app.post("/api/milvus-targets/{target_id}/health-check")
    def check_milvus_target_health(target_id: str):
        if app.state.instance.mode != "central":
            raise HTTPException(status_code=403, detail="只有智能中心可以检查 Milvus 服务健康")
        try: return milvus_targets_service.check_current(target_id)
        except StaleMilvusVerification as exc: raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc: raise _error(exc) from exc

    @app.post("/api/milvus-targets/{target_id}/collections-check")
    def check_milvus_target_collections(target_id: str):
        if app.state.instance.mode != "central":
            raise HTTPException(status_code=403, detail="只有智能中心可以检查 Milvus Collection")
        try: return milvus_targets_service.check_collections(target_id)
        except ValueError as exc: raise _error(exc) from exc

    @app.put("/api/instance/milvus-authoring-target")
    def put_authoring_milvus_target(payload: AuthoringMilvusTargetRequest):
        if app.state.instance.mode != "central":
            raise HTTPException(status_code=403, detail="只有智能中心可以配置默认知识写入目标")
        try: return store.bind_authoring_milvus_target(app.state.instance.id, payload.milvus_target_id)
        except ValueError as exc: raise _error(exc) from exc

    def _require_central_deployment_admin() -> None:
        if app.state.instance.mode != "central":
            raise HTTPException(status_code=403, detail="Local 实例不能管理共享 Deployment")

    @app.get("/api/institution-deployments")
    def institution_deployments():
        return store.list_shared_deployments(
            allowed_deployment_id=app.state.instance.bound_deployment_id
            if app.state.instance.mode == "local" else None
        )

    @app.post("/api/institution-deployments", status_code=201)
    def create_institution_deployment(payload: DeploymentRequest):
        _require_central_deployment_admin()
        try:
            return store.create_shared_deployment(
                institution_name=payload.institution_name,
                institution_code=payload.institution_code,
                project_ids=payload.project_ids,
            )
        except ValueError as exc: raise _error(exc) from exc

    @app.patch("/api/institution-deployments/{deployment_id}")
    def patch_institution_deployment(deployment_id: str, payload: DeploymentPatch):
        _require_central_deployment_admin()
        try:
            return store.patch_shared_deployment(deployment_id, **payload.model_dump())
        except ValueError as exc: raise _error(exc) from exc

    @app.get("/api/instance/release-targets/{release_stage}")
    def get_instance_release_target(release_stage: Literal["test", "production"]):
        if app.state.instance.mode != "central":
            raise HTTPException(status_code=403, detail="只有中心实例可以读取发布 Target")
        try: return store.instance_release_target(app.state.instance.id, release_stage)
        except ValueError as exc: raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.put("/api/instance/release-targets/{release_stage}")
    def put_instance_release_target(release_stage: Literal["test", "production"],
                                    payload: InstanceReleaseTargetRequest):
        _require_central_deployment_admin()
        try:
            return store.put_instance_release_target(
                app.state.instance.id, release_stage, payload.milvus_target_id,
                payload.milvus_target_revision_id,
                confirm_production=payload.confirm_production,
                expected_target_uri=payload.expected_target_uri,
            )
        except ValueError as exc: raise _error(exc) from exc

    @app.get("/api/institution-deployments/{deployment_id}/projects")
    def institution_deployment_projects(deployment_id: str):
        try: return store.list_deployment_projects(deployment_id)
        except ValueError as exc: raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.put("/api/institution-deployments/{deployment_id}/projects")
    def replace_institution_deployment_projects(deployment_id: str, payload: DeploymentProjectsRequest):
        _require_central_deployment_admin()
        try: return store.replace_deployment_projects(deployment_id, payload.project_ids)
        except ValueError as exc: raise _error(exc) from exc

    @app.get("/api/projects/{project_id}/release-tasks")
    def release_tasks(project_id: str):
        try: return store.list_release_tasks(project_id)
        except ValueError as exc: raise _error(exc) from exc

    @app.post("/api/projects/{project_id}/release-tasks", status_code=201)
    def create_release_task(project_id: str, payload: DeploymentTaskRequest):
        try:
            return store.create_release_task(project_id, payload.project_task_id, payload.index_profile_id,
                top_k=payload.top_k, enabled=payload.enabled,
                final_top_k=payload.final_top_k, reranker_serving_code=payload.reranker_serving_code)
        except LookupError as exc: raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc: raise _error(exc) from exc

    @app.patch("/api/projects/{project_id}/release-tasks/{task_id}")
    def patch_release_task(project_id: str, task_id: str, payload: DeploymentTaskPatch):
        try:
            return store.patch_release_task(project_id, task_id, payload.model_dump(exclude_unset=True))
        except LookupError as exc: raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc: raise _error(exc) from exc

    @app.delete("/api/projects/{project_id}/release-tasks/{task_id}", status_code=204)
    def delete_release_task(project_id: str, task_id: str):
        try:
            store.delete_release_task(project_id, task_id)
            return Response(status_code=204)
        except LookupError as exc: raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/projects/{project_id}/retrieval-debug/options")
    def retrieval_debug_options(project_id: str, release_stage: Literal["test", "production"],
                                route_mode: Literal["draft", "published", "historical"] = "draft",
                                version_no: int | None = None):
        try:
            return app.state.retrieval_debug.options(project_id, release_stage, route_mode, version_no)
        except LookupError as exc: raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc: raise _error(exc) from exc

    @app.post("/api/projects/{project_id}/retrieval-debug")
    def retrieval_debug(project_id: str, payload: RetrievalDebugRequest):
        try:
            snapshot, identity = app.state.retrieval_debug.snapshot(
                project_id, payload.release_stage, payload.route_mode, payload.version_no,
            )
            if not snapshot.get("milvus_target"):
                snapshot = _execution_snapshot(
                    project_id, snapshot, payload.release_stage,
                    _stage_connection(project_id, payload.release_stage),
                )
            return app.state.retrieval_debug.run_resolved(
                snapshot, identity, payload, instance_mode=app.state.instance.mode,
            )
        except LookupError as exc: raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc: raise _error(exc) from exc

    @app.post("/api/projects/{project_id}/retrieval-public-test")
    def retrieval_public_test(project_id: str, payload: PublicRetrievalAdminRequest,
                              request: Request):
        try:
            snapshot, _identity = app.state.retrieval_debug.snapshot(
                project_id, payload.release_stage, "published",
            )
            deployment_code = (
                CENTRAL_DEPLOYMENT_CODE if app.state.instance.mode == "central"
                else snapshot["deployment"]["code"]
            )
            public_request = PublicRetrievalRequest(org_code=payload.org_code, query=payload.query)
            content, trace, failure = app.state.public_retrieval.query_with_trace(
                snapshot["project"]["code"], deployment_code,
                payload.release_stage, payload.task_code, public_request,
                request_id=request.state.request_id, instance_mode=app.state.instance.mode,
            )
            public_path = (
                f"/api/runtime/retrieval/v1/{snapshot['project']['code']}/"
                f"{deployment_code}/{payload.release_stage}/"
                f"{payload.task_code}/query"
            )
            response_body = content if content is not None else {
                "error": {"code": failure.code, "message": failure.message},
                "request_id": request.state.request_id,
            }
            envelope = {
                "request": {
                    "method": "POST", "path": public_path,
                    "headers": {
                        "Authorization": "Bearer <DATAFORGE_RETRIEVAL_TOKEN>",
                        "Content-Type": "application/json",
                    },
                    "body": public_request.model_dump(),
                },
                "response": {
                    "status_code": failure.status_code if failure else 200,
                    "request_id": request.state.request_id,
                    "body": response_body,
                },
                "trace": trace,
            }
            return JSONResponse(
                status_code=failure.status_code if failure else 200,
                content=envelope,
                headers={"Cache-Control": "no-store"},
            )
        except LookupError:
            return public_retrieval_error(
                PublicRetrievalError("route_not_found", "公共检索路由不存在", 404), request,
            )
        except ValueError:
            return public_retrieval_error(
                PublicRetrievalError("route_not_found", "公共检索路由不存在", 404), request,
            )
        except PublicRetrievalError as exc:
            return public_retrieval_error(exc, request)

    @app.get("/api/projects/{project_id}/authorizations")
    def project_authorizations(project_id: str):
        try: return store.list_authorizations(project_id)
        except LookupError as exc: raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.put("/api/projects/{project_id}/release-tasks/{release_task_id}/org-routes")
    def put_release_route(project_id: str, release_task_id: str, payload: RouteRequest):
        try:
            tasks = {item["id"] for item in store.list_release_tasks(project_id)}
            if release_task_id not in tasks: raise LookupError("ProjectReleaseTask 不存在")
            return store.put_release_route(release_task_id, payload.org_code, payload.org_name, payload.knowledge_library_ids)
        except LookupError as exc: raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc: raise _error(exc) from exc

    def _project_boundary(project_id: str) -> None:
        if not any(item["id"] == project_id for item in store.list_projects(
                allowed_deployment_id=(app.state.instance.bound_deployment_id
                                       if app.state.instance.mode == "local" else None))):
            raise HTTPException(status_code=404, detail="Project 不存在")

    def _begin_routing_action(project_id: str) -> None:
        with app.state.routing_publications_lock:
            if project_id in app.state.routing_publications:
                raise HTTPException(status_code=409, detail="该 Project 已有 Routing 发布或回滚正在运行")
            app.state.routing_publications.add(project_id)

    def _end_routing_action(project_id: str) -> None:
        with app.state.routing_publications_lock:
            app.state.routing_publications.discard(project_id)

    def _stage_connection(project_id: str, release_stage: Literal["test", "production"]):
        try: return milvus_resolver.stage(project_id, release_stage)
        except ValueError as exc: raise HTTPException(status_code=503, detail=str(exc)) from exc

    def _execution_snapshot(project_id: str, core: dict, release_stage: str, connection) -> dict:
        """Attach transient execution identity to a Project-level route snapshot.

        ProjectRouteVersion intentionally contains no central Deployment or physical
        target.  This helper is the single boundary that resolves those values for
        a debug run or a publication; it never writes them back to the core route.
        """
        snapshot = dict(core)
        snapshot["release_stage"] = release_stage
        if app.state.instance.mode == "local" and app.state.instance.bound_deployment_id:
            with store.sessions() as session:
                deployment = session.get(Deployment, app.state.instance.bound_deployment_id)
                binding = session.scalar(select(ProjectDeployment).where(
                    ProjectDeployment.project_id == project_id,
                    ProjectDeployment.deployment_id == app.state.instance.bound_deployment_id,
                    ProjectDeployment.status == "active",
                ))
            if not deployment or not binding:
                raise ValueError("机构本地 Project 未绑定当前 Deployment")
            snapshot["deployment"] = store._shared_deployment_payload(deployment)
            snapshot["project_deployment"] = {
                "id": binding.id, "project_id": project_id,
                "deployment_id": deployment.id, "status": binding.status,
            }
            revision_id = f"local:{connection.revision_id}"
        else:
            snapshot["deployment"] = {
                "id": "central-runtime", "code": CENTRAL_DEPLOYMENT_CODE,
                "name": "DataForge 中心", "scope": "central",
            }
            snapshot.pop("project_deployment", None)
            revision_id = connection.revision_id
        snapshot["milvus_target"] = {
            "id": connection.target_id, "name": "当前环境 Milvus",
            "milvus_url": connection.uri, "revision_id": revision_id,
            "connection_fingerprint": connection.fingerprint,
            "token_configured": bool(connection.token),
        }
        return snapshot

    def _validate_target_routing(project_id: str, release_stage: Literal["test", "production"]):
        connection = _stage_connection(project_id, release_stage)
        result = store.validate_routing(
            project_id, release_stage, V7Milvus(connection.uri, connection.token),
            target_validation_mode="live",
        )
        if result.get("snapshot"):
            result["snapshot"] = _execution_snapshot(
                project_id, result["snapshot"], release_stage, connection,
            )
        return result

    @app.post("/api/projects/{project_id}/routing/validate")
    def validate_project_routing(project_id: str,
                                    release_stage: Literal["test", "production"]):
        _project_boundary(project_id)
        try: return _validate_target_routing(project_id, release_stage)
        except ValueError as exc: raise _error(exc) from exc

    @app.get("/api/projects/{project_id}/routing/diff")
    def project_routing_diff(project_id: str, release_stage: Literal["test", "production"]):
        _project_boundary(project_id)
        try: return store.routing_diff(project_id, release_stage)
        except ValueError as exc: raise _error(exc) from exc

    @app.get("/api/projects/{project_id}/route-versions")
    def project_route_versions(project_id: str, release_stage: Literal["test", "production"]):
        _project_boundary(project_id); return store.list_route_versions(project_id, release_stage)

    @app.get("/api/projects/{project_id}/route-versions/{version_no}")
    def project_route_version(project_id: str, version_no: int,
                                   release_stage: Literal["test", "production"]):
        _project_boundary(project_id)
        try: return store.route_version_detail(project_id, version_no, release_stage)
        except ValueError as exc: raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/projects/{project_id}/route-versions", status_code=201)
    def freeze_project_routing(project_id: str,
                                  release_stage: Literal["test", "production"]):
        if app.state.instance.mode != "central":
            raise HTTPException(status_code=403, detail="只有智能中心可以冻结机构离线路由")
        _project_boundary(project_id)
        try:
            return store.freeze_route_version(project_id, release_stage)
        except ValueError as exc:
            raise _error(exc) from exc

    @app.post("/api/projects/{project_id}/publications")
    def publish_project_routing(project_id: str, payload: RoutingActionRequest):
        _project_boundary(project_id)
        _begin_routing_action(project_id)
        try:
            stage = payload.release_stage
            connection = _stage_connection(project_id, stage)
            expected_uri = connection.uri
            if stage == "production" and (
                not payload.confirm_production or payload.expected_target_uri != expected_uri
            ):
                raise ValueError("生产发布必须再次确认当前生产环境的完整 Milvus URI")
            if payload.expected_target_uri and payload.expected_target_uri != expected_uri:
                raise ValueError("发布目标与 release_stage 不一致")
            versions = store.list_route_versions(project_id, stage)
            version_id = payload.route_version_id or (versions[0]["id"] if versions else None)
            version = next((item for item in versions if item["id"] == version_id), None)
            if not version:
                raise ValueError("请先冻结项目版本")
            detail = store.route_version_detail(project_id, version["version_no"], stage)
            candidate = _execution_snapshot(project_id, detail["snapshot"], stage, connection)
            if app.state.instance.mode == "central":
                RoutingDeliveryService(
                    store, resolved.routing_dir / "production-backups", resolver=milvus_resolver,
                ).sync(candidate)
            origin = "local" if app.state.instance.mode == "local" else "central"
            checksum, object_key = AtomicRoutingPublisher(resolved.routing_dir).publish(
                candidate["project"]["code"], candidate["deployment"]["code"], version["version_no"], candidate,
                release_stage=stage)
            return store.create_project_publication(
                project_id, version["id"], stage,
                target_kind="local" if app.state.instance.mode == "local" else "registry",
                target_id=str(connection.target_id or "local"),
                target_revision_id=str(connection.revision_id),
                target_connection_fingerprint=connection.fingerprint,
                snapshot=candidate, checksum=checksum, object_key=object_key, origin=origin,
            )
        except ValueError as exc: raise _error(exc) from exc
        finally: _end_routing_action(project_id)

    @app.post("/api/projects/{project_id}/publications/rollback/{version_no}")
    def rollback_project_routing(project_id: str, version_no: int,
                                    payload: RoutingActionRequest):
        _project_boundary(project_id)
        _begin_routing_action(project_id)
        try:
            stage = payload.release_stage
            connection = _stage_connection(project_id, stage)
            expected_uri = connection.uri
            if stage == "production" and (
                not payload.confirm_production or payload.expected_target_uri != expected_uri
            ):
                raise ValueError("生产回滚必须再次确认当前生产环境的完整 Milvus URI")
            if payload.expected_target_uri and payload.expected_target_uri != expected_uri:
                raise ValueError("回滚目标与 release_stage 不一致")
            previous = store.published_route_version(
                project_id, version_no, release_stage=stage,
            )
            store.restore_authorizations(project_id, previous.snapshot_json)
            check = _validate_target_routing(project_id, stage)
            if not check["valid"]: raise ValueError("回滚后的授权校验失败：" + "；".join(check["problems"]))
            snapshot = _execution_snapshot(project_id, previous.snapshot_json, stage, connection)
            if app.state.instance.mode == "central":
                RoutingDeliveryService(
                    store, resolved.routing_dir / "production-backups", resolver=milvus_resolver,
                ).sync(snapshot)
            checksum, object_key = AtomicRoutingPublisher(resolved.routing_dir).publish(
                snapshot["project"]["code"], snapshot["deployment"]["code"], previous.version_no, snapshot,
                release_stage=stage)
            return store.create_project_publication(
                project_id, previous.id, stage,
                target_kind="local" if app.state.instance.mode == "local" else "registry",
                target_id=str(connection.target_id or "local"),
                target_revision_id=str(connection.revision_id),
                target_connection_fingerprint=connection.fingerprint,
                snapshot=snapshot, checksum=checksum, object_key=object_key,
                origin="local" if app.state.instance.mode == "local" else "central",
            )
        except ValueError as exc: raise _error(exc) from exc
        finally: _end_routing_action(project_id)

    @app.get("/api/imported-route-candidates")
    def imported_route_candidates(migration_job_id: str | None = None):
        if app.state.instance.mode != "local":
            raise HTTPException(status_code=403, detail="只有机构本地可以查看导入候选路由")
        values = store.list_imported_route_candidates(migration_job_id)
        visible = []
        for value in values:
            try:
                app.state.instance.require_deployment(store, value["project_deployment_id"])
                visible.append(value)
            except LookupError:
                continue
        return visible

    def _activation_preflight(job_id: str):
        return ActivationPreflightVerifier(
            store, local_milvus_config, app.state.instance,
        ).run(job_id)

    def _candidate(candidate_id: str):
        candidate = next((item for item in store.list_imported_route_candidates()
                          if item["id"] == candidate_id), None)
        if not candidate:
            raise ValueError("候选路由不存在")
        return candidate

    def _activate_route_candidate(candidate_id: str, *, preflight: dict | None = None):
        candidate = _candidate(candidate_id)
        preflight = preflight or _activation_preflight(candidate["migration_job_id"])
        if not preflight.get("ready"):
            raise ValueError("Activation Preflight 存在阻断项，禁止激活")
        started = store.start_route_candidate_activation(candidate_id)
        app.state.instance.require_deployment(store, started["project_deployment_id"])
        if started.get("idempotent"):
            return next(item for item in store.list_imported_route_candidates()
                        if item["id"] == candidate_id)
        snapshot = started["snapshot"]
        try:
            checksum, object_key = AtomicRoutingPublisher(resolved.routing_dir).publish(
                snapshot["project"]["code"], snapshot["deployment"]["code"],
                started["version_no"], snapshot, release_stage=snapshot["release_stage"],
            )
            return store.finish_route_candidate_activation(
                candidate_id, started["route_version_id"], checksum, object_key,
            )
        except Exception as exc:
            store.finish_route_candidate_activation(
                candidate_id, started["route_version_id"], None, None, str(exc),
            )
            raise

    @app.post("/api/imported-route-candidates/{candidate_id}/activate")
    def activate_imported_route_candidate(candidate_id: str):
        if app.state.instance.mode != "local":
            raise HTTPException(status_code=403, detail="只有机构本地可以激活导入候选路由")
        try:
            return _activate_route_candidate(candidate_id)
        except (ValueError, LookupError) as exc:
            raise _error(ValueError(str(exc))) from exc

    @app.post("/api/imported-route-candidates/activate-ready")
    def activate_ready_route_candidates(payload: RouteCandidateBatchRequest):
        if app.state.instance.mode != "local":
            raise HTTPException(status_code=403, detail="只有机构本地可以批量激活候选路由")
        results = []
        preflights: dict[str, dict] = {}
        for candidate_id in payload.candidate_ids:
            try:
                candidate = _candidate(candidate_id)
                if candidate["migration_job_id"] not in preflights:
                    preflights[candidate["migration_job_id"]] = _activation_preflight(
                        candidate["migration_job_id"])
                preflight = preflights[candidate["migration_job_id"]]
                results.append({"candidate_id": candidate_id, "ok": True,
                                "result": _activate_route_candidate(candidate_id, preflight=preflight)})
            except Exception as exc:
                results.append({"candidate_id": candidate_id, "ok": False, "error": str(exc)})
        return {"atomic": False, "results": results}

    @app.post("/api/migrations/{job_id}/activation-preflight")
    def migration_activation_preflight(job_id: str):
        if app.state.instance.mode != "local":
            raise HTTPException(status_code=403, detail="只有机构本地可以执行激活检查")
        try:
            return _activation_preflight(job_id)
        except ValueError as exc:
            raise _error(exc) from exc

    @app.post("/api/migrations/{job_id}/activate-ready")
    def activate_migration_ready_routes(job_id: str):
        if app.state.instance.mode != "local":
            raise HTTPException(status_code=403, detail="只有机构本地可以批量激活候选路由")
        try:
            preflight = _activation_preflight(job_id)
            if not preflight.get("ready"):
                raise ValueError("Activation Preflight 存在阻断项，禁止激活")
            results = []
            for candidate in preflight["candidates"]:
                try:
                    results.append({"candidate_id": candidate["id"], "ok": True,
                                    "result": _activate_route_candidate(candidate["id"], preflight=preflight)})
                except Exception as exc:
                    results.append({"candidate_id": candidate["id"], "ok": False, "error": str(exc)})
            return {"atomic": False, "preflight": preflight, "results": results}
        except ValueError as exc:
            raise _error(exc) from exc

    @app.get("/api/runtime/routing/{project_code}/{deployment_code}/{release_stage}")
    def runtime_routing(project_code: str, deployment_code: str,
                        release_stage: Literal["test", "production"], request: Request):
        configured_token = os.getenv("DATAFORGE_RUNTIME_TOKEN", "").strip()
        if not configured_token:
            raise HTTPException(status_code=503, detail="DataForge runtime token 未配置")
        if not secrets.compare_digest(
                request.headers.get("Authorization", ""), f"Bearer {configured_token}"):
            raise HTTPException(status_code=401, detail="Runtime token 无效")
        try:
            snapshot = store.runtime_routing_snapshot(project_code, deployment_code, release_stage)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        checksum = str(snapshot.get("checksum") or "")
        etag = f'"{checksum}"'
        if checksum and request.headers.get("If-None-Match") == etag:
            return Response(status_code=304, headers={"ETag": etag})
        return JSONResponse(content=snapshot, headers={"ETag": etag, "Cache-Control": "no-store"})

    @app.get("/api/runtime/retrieval/v1/{project_code}/{deployment_code}/{release_stage}/{task_code}/contract")
    def public_retrieval_contract(project_code: str, deployment_code: str,
                                  release_stage: Literal["test", "production"], task_code: str,
                                  request: Request,
                                  org_code: Annotated[str, Query(min_length=1, max_length=120)]):
        try:
            require_retrieval_token(request)
            content = app.state.public_retrieval.contract(
                project_code, deployment_code, release_stage, task_code, org_code,
                request_id=request.state.request_id,
                allowed_deployment_id=(app.state.instance.bound_deployment_id
                                       if app.state.instance.mode == "local" else None),
            )
            return JSONResponse(content=content, headers={"Cache-Control": "no-store"})
        except PublicRetrievalError as exc:
            return public_retrieval_error(exc, request)

    @app.post("/api/runtime/retrieval/v1/{project_code}/{deployment_code}/{release_stage}/{task_code}/query")
    def public_retrieval_query(project_code: str, deployment_code: str,
                               release_stage: Literal["test", "production"], task_code: str,
                               payload: PublicRetrievalRequest, request: Request):
        try:
            require_retrieval_token(request)
            content = app.state.public_retrieval.query(
                project_code, deployment_code, release_stage, task_code, payload,
                request_id=request.state.request_id, instance_mode=app.state.instance.mode,
                allowed_deployment_id=(app.state.instance.bound_deployment_id
                                       if app.state.instance.mode == "local" else None),
            )
            return JSONResponse(content=content, headers={"Cache-Control": "no-store"})
        except PublicRetrievalError as exc:
            logger.warning(
                "Public retrieval failed. request_id=%s project=%s deployment=%s stage=%s task=%s code=%s",
                request.state.request_id, project_code, deployment_code,
                release_stage, task_code, exc.code,
            )
            return public_retrieval_error(exc, request)

    @app.post("/api/institution-deployments/drafts", status_code=201)
    def create_institution_release_draft(payload: InstitutionReleaseDraftRequest):
        if app.state.instance.mode != "central":
            raise HTTPException(status_code=403, detail="只有智能中心可以创建机构发布草稿")
        try:
            return store.create_institution_release_draft(
                payload.target_deployment_id, payload.package_kind,
                target_institution_code=payload.target_institution_code,
                release_stage=payload.release_stage,
                route_version_ids=payload.route_version_ids,
                knowledge_library_ids=payload.knowledge_library_ids,
                extra_asset_version_ids=payload.extra_asset_version_ids,
                base_release_id=payload.base_release_id,
                include_full_document_library=payload.include_full_document_library,
            )
        except ValueError as exc:
            raise _error(exc) from exc

    @app.get("/api/institution-deployments/drafts/{draft_id}")
    def institution_release_draft(draft_id: str):
        if app.state.instance.mode != "central":
            raise HTTPException(status_code=403, detail="只有智能中心可以查看机构发布草稿")
        try:
            return store.get_institution_release_draft(draft_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.put("/api/institution-deployments/drafts/{draft_id}")
    def update_institution_release_draft(draft_id: str, payload: InstitutionReleaseDraftPatch):
        if app.state.instance.mode != "central":
            raise HTTPException(status_code=403, detail="只有智能中心可以编辑机构发布草稿")
        try:
            return store.update_institution_release_draft(draft_id, **payload.model_dump())
        except ValueError as exc:
            raise _error(exc) from exc

    @app.get("/api/institution-deployments/drafts/{draft_id}/plan")
    def plan_institution_release(draft_id: str):
        if app.state.instance.mode != "central":
            raise HTTPException(status_code=403, detail="只有智能中心可以规划机构发布")
        try:
            return InstitutionReleasePlanner(store).plan(draft_id)
        except ValueError as exc:
            raise _error(exc) from exc

    @app.get("/api/institution-deployments/drafts/{draft_id}/asset-options")
    def institution_release_asset_options(draft_id: str):
        if app.state.instance.mode != "central":
            raise HTTPException(status_code=403, detail="只有智能中心可以查看机构发布资产")
        try:
            return InstitutionReleasePlanner(store).asset_options(draft_id)
        except ValueError as exc:
            raise _error(exc) from exc

    @app.post("/api/institution-deployments/drafts/{draft_id}/freeze", status_code=201)
    def freeze_institution_release(draft_id: str):
        if app.state.instance.mode != "central":
            raise HTTPException(status_code=403, detail="只有智能中心可以冻结机构发布")
        try:
            plan = InstitutionReleasePlanner(store).plan(draft_id)
            if int((plan.get("preflight") or {}).get("blocked") or 0) > 0:
                raise ValueError("机构 Release 存在阻断项，禁止冻结")
            return store.freeze_institution_release_snapshot(draft_id, plan)
        except ValueError as exc:
            raise _error(exc) from exc

    @app.get("/api/institution-deployments/releases")
    def institution_releases(target_deployment_id: str | None = None):
        if app.state.instance.mode != "central":
            raise HTTPException(status_code=403, detail="只有智能中心可以查看机构发布")
        return store.list_institution_release_snapshots(target_deployment_id)

    @app.get("/api/institution-deployments/releases/{release_id}")
    def institution_release(release_id: str):
        if app.state.instance.mode != "central":
            raise HTTPException(status_code=403, detail="只有智能中心可以查看机构发布")
        try:
            return store.get_institution_release_snapshot(release_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/institution-deployments/releases/{release_id}/build", status_code=202)
    def build_institution_release(release_id: str):
        if app.state.instance.mode != "central":
            raise HTTPException(status_code=403, detail="只有智能中心可以构建机构发布包")
        if not resolved.migration_signing_private_key:
            raise HTTPException(status_code=503, detail="未配置 migration Ed25519 签名私钥")
        try:
            release = store.get_institution_release_snapshot(release_id)
            existing = next((item for item in store.list_migration_jobs(direction="export")
                             if item.get("release_snapshot_id") == release_id and
                             item["status"] in {"queued", "running", "ready"}), None)
            if existing:
                return existing
            plan = release["snapshot"]
            items = [{"knowledge_library_id": item["knowledge_library_id"],
                      "collection_name": collection, "partition_name": item["partition_name"],
                      "detail": item}
                     for collection, values in plan["collections"].items() for item in values]
            return store.create_migration_job(
                direction="export", package_kind=release["package_kind"],
                target_deployment_id=release["target_deployment_id"],
                release_snapshot_id=release_id, status="queued", stage="planned", items=items,
            )
        except ValueError as exc:
            raise _error(exc) from exc

    @app.get("/api/local/milvus-configurations")
    def local_milvus_configurations():
        if app.state.instance.mode != "local":
            raise HTTPException(status_code=403, detail="只有机构本地可以管理 Milvus 配置")
        return local_milvus_config.list(app.state.instance.id)

    @app.put("/api/local/milvus-configurations/{slot}")
    def put_local_milvus_configuration(slot: str, payload: LocalMilvusConfigurationRequest):
        if app.state.instance.mode != "local":
            raise HTTPException(status_code=403, detail="只有机构本地可以管理 Milvus 配置")
        try:
            local_milvus_config.put(app.state.instance.id, slot, **payload.model_dump())
            try:
                return local_milvus_config.verify(
                    app.state.instance.id, slot,
                    factory=lambda uri, token: V7Milvus(uri, token),
                )
            except StaleMilvusVerification as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            except ValueError:
                return local_milvus_config.get(app.state.instance.id, slot)
        except ValueError as exc:
            raise _error(exc) from exc

    @app.post("/api/local/milvus-configurations/{slot}/verify")
    def verify_local_milvus_configuration(slot: str):
        if app.state.instance.mode != "local":
            raise HTTPException(status_code=403, detail="只有机构本地可以验证 Milvus 配置")
        try:
            return local_milvus_config.verify(app.state.instance.id, slot)
        except StaleMilvusVerification as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise _error(exc) from exc

    @app.post("/api/local/milvus-configurations/candidate_target/promote")
    def promote_local_milvus_candidate():
        if app.state.instance.mode != "local":
            raise HTTPException(status_code=403, detail="只有机构本地可以切换 Milvus 配置")
        try:
            return local_milvus_config.promote_candidate(app.state.instance.id)
        except ValueError as exc:
            raise _error(exc) from exc

    @app.get("/api/knowledge-asset-gc/plan")
    def knowledge_asset_gc_plan():
        return store.knowledge_asset_gc_plan()

    @app.post("/api/knowledge-asset-gc/jobs", status_code=202)
    def create_knowledge_asset_gc_job(payload: KnowledgeAssetGcRequest):
        try:
            return store.create_knowledge_asset_gc_job(
                execute=payload.execute, confirmation=payload.confirmation,
            )
        except ValueError as exc:
            raise _error(exc) from exc

    @app.get("/api/migrations")
    def migrations(direction: Literal["export", "import"] | None = None):
        deployment_id = app.state.instance.bound_deployment_id if app.state.instance.mode == "local" else None
        return store.list_migration_jobs(direction=direction, deployment_id=deployment_id)

    @app.get("/api/migrations/{job_id}")
    def migration_job(job_id: str):
        try:
            job = store.get_migration_job(job_id)
            if app.state.instance.mode == "local" and job["project_deployment_id"]:
                try: app.state.instance.require_deployment(store, job["project_deployment_id"])
                except LookupError as exc: raise HTTPException(status_code=404, detail="迁移任务不存在") from exc
            return job
        except ValueError as exc: raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/migrations/{job_id}/package")
    def migration_package(job_id: str):
        if app.state.instance.mode != "central": raise HTTPException(status_code=403, detail="只有 central 实例可以下载导出包")
        try: job = store.get_migration_job(job_id)
        except ValueError as exc: raise HTTPException(status_code=404, detail=str(exc)) from exc
        if job["status"] != "ready" or not job["package_path"]: raise HTTPException(status_code=409, detail="迁移包尚未就绪")
        path = Path(job["package_path"])
        if not path.is_file(): raise HTTPException(status_code=410, detail="迁移包文件已不存在")
        return FileResponse(path, media_type="application/vnd.dataforge.migration+zip", filename=f'{job["package_id"]}.dfm')

    @app.post("/api/migrations/import/inspect", status_code=201)
    async def migration_import_inspect(file: UploadFile = File(...)):
        if app.state.instance.mode != "local": raise HTTPException(status_code=403, detail="只有 local 实例可以导入")
        if not resolved.migration_trusted_public_keys:
            raise HTTPException(status_code=503, detail="未配置 migration 受信 Ed25519 公钥")
        upload_id = secrets.token_hex(16); target_dir = resolved.migration_dir / f"upload-{upload_id}"
        target_dir.mkdir(parents=True, exist_ok=False); target = target_dir / "package.dfm"
        digest, size = hashlib.sha256(), 0
        try:
            with target.open("wb") as handle:
                while chunk := await file.read(8 * 1024 * 1024):
                    handle.write(chunk); digest.update(chunk); size += len(chunk)
            if not size: raise ValueError("迁移包不能为空")
            inspected = inspect_package(target, resolved.migration_trusted_public_keys)
            manifest = inspected["manifest"]
            validate_local_package_target(store, app.state.instance, manifest)
            manifest_assets = {str(item.get("id") or item.get("asset_version_id")): item
                               for item in manifest.get("asset_versions") or []}
            items = [{"knowledge_library_id": part["knowledge_library_id"], "collection_name": collection,
                      "partition_name": part["partition_name"],
                      "source_count": int((manifest_assets.get(str(part.get("asset_version_id") or part.get("id"))) or {}).get("item_count") or 0),
                      "source_digest": part.get("content_revision"), "detail": part}
                     for collection, partitions in manifest["collections"].items() for part in partitions]
            status = "inspected"
            conflicts = {}
            with store.sessions() as session:
                from .models import KnowledgeLibrary
                for library_id in manifest["scope"]["knowledge_library_ids"]:
                    current = session.get(KnowledgeLibrary, library_id)
                    if current and current.origin_state == "forked": conflicts[library_id] = "forked"
            if conflicts: status = "conflict"
            projects = manifest.get("projects") or []
            project_payload = manifest.get("project") or ((projects[0] or {}).get("project") if projects else {}) or {}
            created = store.create_migration_job(direction="import", package_kind=manifest["package_kind"],
                package_id=manifest["package_id"], project_id=project_payload.get("id"),
                project_deployment_id=(manifest.get("project_deployment") or {}).get("id"), package_path=str(target),
                package_sha256=digest.hexdigest(), status=status, stage="verified",
                checkpoint={"verified": True, "upload_size": size, "package_admission": {
                    "signature": "verified", "checksum": "verified",
                    "manifest_schema_version": manifest.get("manifest_schema_version") or manifest.get("schema_version"),
                    "package_kind": manifest.get("package_kind"), "package_id": manifest.get("package_id"),
                    "release_id": manifest.get("release_id"), "base_release_id": manifest.get("base_release_id"),
                    "deployment": manifest.get("deployment") or {},
                    "project_count": len(manifest.get("projects") or []),
                }}, signature_status="verified", items=items)
            if created.get("package_path") != str(target):
                target.unlink(missing_ok=True)
                target_dir.rmdir()
            return created
        except Exception as exc:
            target.unlink(missing_ok=True)
            if isinstance(exc, HTTPException): raise
            raise _error(ValueError(str(exc))) from exc

    @app.post("/api/migrations/import", status_code=202)
    def migration_import(payload: MigrationImportRequest):
        if app.state.instance.mode != "local": raise HTTPException(status_code=403, detail="只有 local 实例可以导入")
        try: return store.queue_migration_import(payload.job_id, payload.conflict_resolutions)
        except ValueError as exc: raise _error(exc) from exc

    @app.post("/api/migrations/{job_id}/retry", status_code=202)
    def migration_retry(job_id: str):
        try: return store.retry_migration_job(job_id)
        except ValueError as exc: raise _error(exc) from exc

    @app.post("/api/migrations/{job_id}/resume", status_code=202)
    def migration_resume(job_id: str, payload: MigrationResumeRequest):
        if app.state.instance.mode != "local":
            raise HTTPException(status_code=403, detail="只有机构本地可以继续导入任务")
        try:
            return store.resume_migration_job(
                job_id, selected_import_target=payload.selected_import_target,
            )
        except ValueError as exc:
            raise _error(exc) from exc

    return app


# Importing a module must not create a schema or a local object-store directory.
# The command entry point below constructs the fully configured, verified app.
app = FastAPI(title="DataForge V7 bootstrap")


def main() -> None:
    import uvicorn
    verified_app = create_app(check_schema=True)
    uvicorn.run(verified_app, host=os.getenv("DATAFORGE_WEB_HOST", "0.0.0.0"), port=int(os.getenv("DATAFORGE_WEB_PORT", "8000")))
