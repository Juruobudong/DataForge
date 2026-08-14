from __future__ import annotations

import logging
import json
import os
import secrets
from datetime import timedelta
from pathlib import Path, PurePath
from typing import Annotated, Literal

from fastapi import FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import SQLAlchemyError

from ..config import Settings
from .auth import SESSION_COOKIE, verify_admin_password
from .models import AdminSession, utc_now
from .runner import preview_template_definition
from .routing import AtomicRoutingPublisher
from .storage import LocalObjectStore, MinioObjectStore
from .store import V7Store
from .vector import V7Milvus, VectorSyncService


MAX_UPLOAD_BYTES = 200 * 1024 * 1024
SUPPORTED_EXTENSIONS = {".pdf", ".csv", ".xlsx", ".md", ".doc", ".docx", ".txt"}
logger = logging.getLogger(__name__)


class DocumentLibraryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    description: str = ""


class SourceImportPreflightRequest(BaseModel):
    entries: list[dict] = Field(min_length=1)


class SelectedDocumentSourcesRequest(BaseModel):
    source_ids: list[str] = Field(min_length=1)


class DocumentDeletionRequest(BaseModel):
    source_ids: list[str] = Field(default_factory=list)
    document_library_ids: list[str] = Field(default_factory=list)


class KnowledgeLibraryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    knowledge_type: str
    graph_mode: Literal["triple", "semantic"] | None = None
    description: str = ""


class KnowledgeJobRequest(BaseModel):
    source_version_ids: list[str] = Field(min_length=1)
    output_library_ids: dict[str, str] = Field(min_length=1)
    knowledge_flow_template_id: str


class KnowledgeJobBatchActionRequest(BaseModel):
    job_ids: list[str] = Field(min_length=1)
    action: Literal["cancel", "retry", "delete"]


class FlowTemplateRequest(BaseModel):
    code: str = ""
    name: str
    output_types: list[str] = Field(min_length=1)
    definition: dict = Field(default_factory=lambda: {"steps": ["validate", "parse", "normalize", "structure_recovery", "semantic_chunks", "generate"], "parameters": {"chunk_size": 800}})


class TemplateSampleRequest(BaseModel):
    sample_id: Literal["guideline-md", "faq-csv", "case-txt"] = "guideline-md"


class PromptTemplateRequest(BaseModel):
    code: str
    name: str
    body: str
    input_schema: dict = Field(default_factory=dict)
    output_schema: dict = Field(default_factory=dict)


class QualityProfileRequest(BaseModel):
    code: str
    name: str
    rules: dict = Field(default_factory=dict)


class ProjectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str


class KnowledgeTypeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    code: str
    name: str
    icon: str = "知"
    type_schema: dict = Field(alias="schema")
    canonical_field: str
    identity_fields: list[str] = Field(min_length=1)
    source_policy: Literal["single", "multiple"]
    quality_profile_revision_id: str
    index_profile_ids: list[str] = Field(min_length=1)


class IndexProfileRequest(BaseModel):
    code: str = ""
    knowledge_type: str
    collection_name: str = ""
    collection_policy: Literal["external", "managed"] = "external"
    storage_schema: dict | None = None
    index_spec: dict = Field(default_factory=lambda: {"index_type": "AUTOINDEX"})
    embedding_code: str
    embedding_model: str = ""
    dimension: int
    metric_type: str = "COSINE"
    endpoint_ref: str | None = None
    fields: dict


class ProjectTaskRequest(BaseModel):
    code: str
    name: str


class RouteRequest(BaseModel):
    org_code: str
    knowledge_library_ids: list[str] = Field(min_length=1)


def _objects(settings: Settings):
    if settings.minio_endpoint and settings.minio_access_key and settings.minio_secret_key:
        return MinioObjectStore(settings.minio_endpoint, settings.minio_access_key, settings.minio_secret_key, settings.minio_bucket)
    return LocalObjectStore(settings.state_dir / "v7-objects")


def _error(exc: ValueError) -> HTTPException:
    return HTTPException(status_code=422, detail=str(exc))


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
    store = V7Store(resolved.platform_database_url)
    if check_schema:
        store.assert_schema_current()
    objects = _objects(resolved)
    app = FastAPI(title="DataForge V7", version="7.0.0")
    app.state.store, app.state.objects = store, objects

    @app.middleware("http")
    async def require_admin(request: Request, call_next):
        open_paths = {"/api/health", "/api/auth/status", "/api/auth/login"}
        if resolved.authentication_enabled and request.url.path.startswith("/api/") and request.url.path not in open_paths:
            token = request.cookies.get(SESSION_COOKIE)
            with store.sessions() as session:
                active = session.get(AdminSession, token) if token else None
                if not active or active.expires_at <= utc_now():
                    return JSONResponse(status_code=401, content={"detail": "需要管理员登录"})
        return await call_next(request)

    @app.get("/api/health")
    def health():
        return {"status": "ok", "platform": "v7", "storage": "minio" if resolved.minio_endpoint else "local-dev", "database": "mysql" if resolved.database_url else "sqlite-dev"}

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

    @app.get("/api/document-libraries")
    def document_libraries(keyword: str = "", status: str | None = None):
        return store.list_document_libraries(keyword, status)

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

    @app.delete("/api/document-libraries/{library_id}/template-bindings/{template_id}")
    def unbind_document_library_template(library_id: str, template_id: str):
        try: return store.unbind_document_library_template(library_id, template_id)
        except ValueError as exc: raise _error(exc) from exc

    @app.post("/api/document-libraries/{library_id}/process", status_code=202)
    def process_document_library(library_id: str):
        try: return {"jobs": store.process_document_library(library_id)}
        except ValueError as exc: raise _error(exc) from exc

    @app.post("/api/document-libraries/{library_id}/process-selected", status_code=202)
    def process_selected_document_sources(library_id: str, payload: SelectedDocumentSourcesRequest):
        try: return {"jobs": store.process_selected_document_sources(library_id, payload.source_ids)}
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

        def discard_stored_object(stored: object | None) -> None:
            if stored is None:
                return
            try:
                objects.delete_key(stored.key)
            except Exception:
                logger.exception("Failed to remove object after source upload persistence failure", extra={"object_key": stored.key})

        for index, upload in enumerate(files):
            filename = Path(upload.filename or "upload.txt").name; suffix = Path(filename).suffix.lower()
            if suffix not in SUPPORTED_EXTENSIONS:
                results.append({"filename": filename, "status": "failed", "error": "仅支持 PDF、CSV、XLSX、Markdown、DOC、DOCX 和 TXT"}); continue
            stored = None
            try:
                data = await _read_upload(upload)
                object_key = f"sources/{library_id}/{secrets.token_hex(16)}/source{suffix}"
                stored = objects.put_bytes(object_key, data, upload.content_type or "application/octet-stream")
                source = store.create_source(library_id=library_id, name=(names[index] if names and index < len(names) else Path(filename).stem), filename=filename, object_key=stored.key, sha256=stored.sha256, size_bytes=stored.size_bytes, mime_type=upload.content_type or "application/octet-stream", relative_path=filename)
                results.append({"filename": filename, "status": "created", "source": source})
            except (ValueError, HTTPException) as exc:
                discard_stored_object(stored)
                results.append({"filename": filename, "status": "failed", "error": str(getattr(exc, "detail", exc))})
            except SQLAlchemyError:
                logger.exception("Source upload database persistence failed", extra={"upload_filename": filename, "document_library_id": library_id})
                discard_stored_object(stored)
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
                    raise ValueError("仅支持 PDF、CSV、XLSX、Markdown、DOC、DOCX 和 TXT")
                current = store.source_by_relative_path(library_id, relative_path)
                if current and current.status not in {"deleted", "deleting"}:
                    if duplicate_policy == "skip":
                        results.append({"filename": filename, "relative_path": relative_path, "status": "skipped", "reason": "同路径文件已存在"}); continue
                    if duplicate_policy == "keep_both":
                        relative_path = store.available_relative_path(library_id, relative_path)
                data = await _read_upload(upload)
                stored = objects.put_bytes(f"sources/{library_id}/{secrets.token_hex(16)}/source{suffix}", data, upload.content_type or "application/octet-stream")
                if current and duplicate_policy == "replace":
                    source = store.replace_source(source_id=current.id, filename=filename, object_key=stored.key, sha256=stored.sha256, size_bytes=stored.size_bytes, mime_type=upload.content_type or "application/octet-stream")
                    results.append({"filename": filename, "relative_path": relative_path, "status": "replaced", "source": source})
                else:
                    source = store.create_source(library_id=library_id, name=Path(filename).stem, filename=filename, object_key=stored.key, sha256=stored.sha256,
                                                 size_bytes=stored.size_bytes, mime_type=upload.content_type or "application/octet-stream", relative_path=relative_path)
                    results.append({"filename": filename, "relative_path": relative_path, "status": "renamed" if relative_path != str(entry.get("relative_path")) else "created", "source": source})
            except (ValueError, HTTPException) as exc:
                if stored:
                    try: objects.delete_key(stored.key)
                    except Exception: logger.exception("Failed to remove rejected import object")
                results.append({"filename": filename, "relative_path": relative_path, "status": "failed", "error": str(getattr(exc, "detail", exc))})
            except SQLAlchemyError:
                if stored:
                    try: objects.delete_key(stored.key)
                    except Exception: logger.exception("Failed to remove failed import object")
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

    @app.post("/api/sources/{source_id}/replace", status_code=201)
    async def replace_source(source_id: str, file: Annotated[UploadFile, File()]):
        try: source = store.source_for_upload(source_id)
        except ValueError as exc: raise HTTPException(status_code=404, detail=str(exc)) from exc
        filename = Path(file.filename or "replacement.txt").name; suffix = Path(filename).suffix.lower()
        if suffix not in SUPPORTED_EXTENSIONS: raise HTTPException(status_code=422, detail="仅支持 PDF、CSV、XLSX、Markdown、DOC、DOCX 和 TXT")
        if filename != PurePath(source.relative_path).name:
            raise HTTPException(status_code=422, detail="替换文件名称必须与原相对路径的文件名一致")
        data = await _read_upload(file); stored = objects.put_bytes(f"sources/{source.document_library_id}/{source_id}/{secrets.token_hex(16)}/replacement{suffix}", data, file.content_type or "application/octet-stream")
        try:
            return store.replace_source(source_id=source_id, filename=filename, object_key=stored.key, sha256=stored.sha256, size_bytes=stored.size_bytes, mime_type=file.content_type or "application/octet-stream")
        except (ValueError, SQLAlchemyError) as exc:
            try: objects.delete_key(stored.key)
            except Exception: logger.exception("Failed to remove object after source replacement failure", extra={"object_key": stored.key})
            if isinstance(exc, ValueError):
                raise _error(exc) from exc
            logger.exception("Source replacement database persistence failed", extra={"source_id": source_id})
            raise HTTPException(status_code=500, detail="文件记录保存失败，请稍后重试") from exc

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

    @app.get("/api/sources/{source_id}/versions/{version_id}/download")
    def download_source(source_id: str, version_id: str):
        try: version = store.source_version_for_download(source_id, version_id)
        except ValueError as exc: raise HTTPException(status_code=404, detail=str(exc)) from exc
        return Response(content=objects.get_bytes(version.object_key), media_type=version.mime_type, headers={"Content-Disposition": f'attachment; filename="{version_id}"'})

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

    @app.post("/api/knowledge-libraries", status_code=201)
    def create_knowledge_library(payload: KnowledgeLibraryRequest):
        try: return store.create_knowledge_library(payload.name, payload.knowledge_type, payload.description, payload.graph_mode)
        except ValueError as exc: raise _error(exc) from exc

    @app.get("/api/knowledge-libraries/{library_id}/delete-check")
    def knowledge_library_delete_check(library_id: str):
        try: return store.knowledge_library_delete_check(library_id)
        except ValueError as exc: raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.delete("/api/knowledge-libraries/{library_id}", status_code=202)
    def delete_knowledge_library(library_id: str):
        try: return store.request_knowledge_library_deletion(library_id)
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
    def qa_pairs(library_id: str):
        try: return store.list_knowledge_items(library_id, "qa")
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

    @app.get("/api/knowledge-libraries/{library_id}/graph/entities/{entity_id}")
    def graph_entity_detail(library_id: str, entity_id: str):
        try: return store.graph_entity_detail(library_id, entity_id)
        except ValueError as exc: raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/knowledge-libraries/{library_id}/graph/entities/{entity_id}/neighbors")
    def graph_neighbors(library_id: str, entity_id: str, depth: int = 1):
        try: return store.graph_neighbors(library_id, entity_id, depth)
        except ValueError as exc: raise _error(exc) from exc

    @app.get("/api/knowledge-libraries/{library_id}/graph/relations/{relation_id}/evidence")
    def graph_relation_evidence(library_id: str, relation_id: str):
        try: return store.graph_relation_evidence(library_id, relation_id)
        except ValueError as exc: raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/knowledge-jobs")
    def knowledge_jobs(): return store.list_knowledge_jobs()

    @app.post("/api/knowledge-jobs", status_code=202)
    def create_knowledge_job(payload: KnowledgeJobRequest):
        try: return store.create_knowledge_job(payload.source_version_ids, payload.output_library_ids, payload.knowledge_flow_template_id)
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

    @app.get("/api/developer/knowledge-types")
    def knowledge_types():
        return store.list_knowledge_type_definitions()

    @app.post("/api/developer/knowledge-types", status_code=201)
    def create_knowledge_type(payload: KnowledgeTypeRequest):
        try: return store.create_knowledge_type(payload.code, payload.name, payload.icon, payload.type_schema, payload.canonical_field,
                                                payload.identity_fields, payload.source_policy, payload.quality_profile_revision_id,
                                                payload.index_profile_ids)
        except ValueError as exc: raise _error(exc) from exc

    @app.post("/api/developer/knowledge-types/{type_id}/revisions", status_code=201)
    def revise_knowledge_type(type_id: str, payload: KnowledgeTypeRequest):
        try: return store.revise_knowledge_type(type_id, payload.type_schema, payload.canonical_field, payload.identity_fields,
                                                payload.source_policy, payload.quality_profile_revision_id, payload.index_profile_ids)
        except ValueError as exc: raise _error(exc) from exc

    @app.post("/api/developer/knowledge-types/{type_id}/validate")
    def validate_knowledge_type(type_id: str):
        try: return store.validate_knowledge_type(type_id)
        except ValueError as exc: raise _error(exc) from exc

    @app.post("/api/developer/knowledge-types/{type_id}/publish")
    def publish_knowledge_type(type_id: str):
        try: return store.publish_knowledge_type(type_id)
        except ValueError as exc: raise _error(exc) from exc

    @app.get("/api/developer/standard-pipelines")
    def standard_pipelines():
        return [{"code": "common", "steps": ["Document Parse", "Document Clean", "Knowledge Chunk", "Production", "Knowledge Publish"]}]

    @app.get("/api/developer/operator-catalog")
    def operator_catalog():
        return store.list_operator_catalog()

    @app.get("/api/developer/prompt-templates")
    def prompt_templates():
        return store.list_prompt_templates()

    @app.post("/api/developer/prompt-templates", status_code=201)
    def create_prompt_template(payload: PromptTemplateRequest):
        try: return store.create_prompt_template(payload.code, payload.name, payload.body, payload.input_schema, payload.output_schema)
        except ValueError as exc: raise _error(exc) from exc

    @app.post("/api/developer/prompt-templates/{prompt_id}/publish")
    def publish_prompt_template(prompt_id: str):
        try: return store.publish_prompt_template(prompt_id)
        except ValueError as exc: raise _error(exc) from exc

    @app.get("/api/developer/quality-profiles")
    def quality_profiles():
        return store.list_quality_profiles()

    @app.post("/api/developer/quality-profiles", status_code=201)
    def create_quality_profile(payload: QualityProfileRequest):
        try: return store.create_quality_profile(payload.code, payload.name, payload.rules)
        except ValueError as exc: raise _error(exc) from exc

    @app.post("/api/developer/quality-profiles/{profile_id}/publish")
    def publish_quality_profile(profile_id: str):
        try: return store.publish_quality_profile(profile_id)
        except ValueError as exc: raise _error(exc) from exc

    @app.get("/api/developer/flow-subgraphs")
    def flow_subgraphs():
        return store.list_subflows()

    @app.get("/api/developer/knowledge-flow-templates")
    def flow_templates():
        return store.list_flow_templates()

    @app.post("/api/developer/knowledge-flow-templates", status_code=201)
    def create_flow_template(payload: FlowTemplateRequest):
        try: return store.create_flow_template(payload.code, payload.name, payload.output_types, payload.definition)
        except ValueError as exc: raise _error(exc) from exc

    @app.put("/api/developer/knowledge-flow-templates/{template_id}")
    def update_flow_template(template_id: str, payload: FlowTemplateRequest):
        try: return store.update_flow_template(template_id, payload.name, payload.output_types, payload.definition)
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
    def publish_flow_template(template_id: str):
        try: return store.publish_flow_template(template_id)
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

    @app.get("/api/developer/flow-runs")
    def flow_runs():
        return store.list_flow_runs()

    @app.get("/api/developer/flow-runs/{flow_run_id}")
    def flow_run(flow_run_id: str):
        try: return store.flow_run_detail(flow_run_id)
        except ValueError as exc: raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/developer/vector-indexes")
    def vector_indexes(): return {"profiles": store.list_index_profiles(), "managed_collections": store.list_managed_collections(), "capacity": VectorSyncService.from_environment(store).capacity_report()}

    @app.get("/api/developer/managed-collections")
    def managed_collections(): return store.list_managed_collections()

    @app.post("/api/developer/managed-collections/{collection_id}/reconcile")
    def reconcile_managed_collection(collection_id: str):
        from .provisioning import ManagedCollectionProvisioner
        service = VectorSyncService.from_environment(store)
        if not service.milvus:
            raise HTTPException(status_code=503, detail="DATAFORGE_MILVUS_URI 未配置")
        try: return ManagedCollectionProvisioner(store, service.milvus).reconcile_one(collection_id)
        except ValueError as exc: raise _error(exc) from exc

    @app.post("/api/developer/index-profiles", status_code=201)
    def create_index_profile(payload: IndexProfileRequest):
        try: return store.create_index_profile(payload.code, payload.knowledge_type, payload.collection_name, payload.embedding_code,
                                               payload.embedding_model, payload.dimension, payload.metric_type, payload.endpoint_ref, payload.fields,
                                               collection_policy=payload.collection_policy, storage_schema=payload.storage_schema,
                                               index_spec=payload.index_spec)
        except ValueError as exc: raise _error(exc) from exc

    @app.post("/api/developer/index-profiles/{profile_id}/revisions", status_code=201)
    def revise_index_profile(profile_id: str, payload: IndexProfileRequest):
        try: return store.revise_index_profile(profile_id, payload.collection_name, payload.embedding_code, payload.embedding_model,
                                               payload.dimension, payload.metric_type, payload.endpoint_ref, payload.fields,
                                               collection_policy=payload.collection_policy, storage_schema=payload.storage_schema,
                                               index_spec=payload.index_spec)
        except ValueError as exc: raise _error(exc) from exc

    def index_validator(collection_name: str, fields: dict, dimension: int) -> None:
        uri = os.getenv("DATAFORGE_MILVUS_URI")
        if not uri:
            raise ValueError("未配置 DATAFORGE_MILVUS_URI，不能校验或发布 Index Profile")
        V7Milvus(uri, os.getenv("DATAFORGE_MILVUS_TOKEN")).validate_collection(collection_name, fields, dimension)

    @app.post("/api/developer/index-profiles/{profile_id}/validate")
    def validate_index_profile(profile_id: str):
        try: return store.validate_index_profile(profile_id, index_validator)
        except ValueError as exc: raise _error(exc) from exc

    @app.post("/api/developer/index-profiles/{profile_id}/publish")
    def publish_index_profile(profile_id: str):
        try: return store.publish_index_profile(profile_id, index_validator)
        except ValueError as exc: raise _error(exc) from exc

    @app.post("/api/knowledge-libraries/{library_id}/vector-sync-jobs", status_code=202)
    def create_vector_sync_jobs(library_id: str):
        try: return store.create_vector_sync_jobs(library_id)
        except ValueError as exc: raise _error(exc) from exc

    @app.get("/api/vector-sync-jobs")
    def vector_sync_jobs(knowledge_library_id: str | None = None): return store.list_vector_sync_jobs(knowledge_library_id)

    @app.post("/api/vector-sync-jobs/{sync_job_id}/run")
    def run_vector_sync(sync_job_id: str): return VectorSyncService.from_environment(store).run(sync_job_id)

    @app.get("/api/projects")
    def projects(): return store.list_projects()

    @app.post("/api/projects", status_code=201)
    def create_project(payload: ProjectRequest):
        try: return store.create_project(payload.name)
        except ValueError as exc: raise _error(exc) from exc

    @app.post("/api/projects/{project_id}/tasks", status_code=201)
    def create_project_task(project_id: str, payload: ProjectTaskRequest):
        try: return store.create_project_task(project_id, payload.code, payload.name)
        except ValueError as exc: raise _error(exc) from exc

    @app.put("/api/project-tasks/{task_id}/org-routes")
    def put_route(task_id: str, payload: RouteRequest):
        try: return store.put_route(task_id, payload.org_code, payload.knowledge_library_ids)
        except ValueError as exc: raise _error(exc) from exc

    @app.post("/api/projects/{project_id}/routing/validate")
    def validate_routing(project_id: str):
        try: return store.validate_routing(project_id)
        except ValueError as exc: raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/projects/{project_id}/routing/diff")
    def routing_diff(project_id: str):
        try: return store.routing_diff(project_id)
        except ValueError as exc: raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/projects/{project_id}/routing/versions")
    def routing_versions(project_id: str): return store.list_route_versions(project_id)

    @app.get("/api/projects/{project_id}/routing/versions/{version_no}")
    def routing_version_detail(project_id: str, version_no: int):
        try: return store.route_version_detail(project_id, version_no)
        except ValueError as exc: raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/projects/{project_id}/routing/publish")
    def publish_routing(project_id: str):
        try:
            check = store.validate_routing(project_id)
            if not check["valid"]: raise ValueError("路由校验失败：" + "；".join(check["problems"]))
            snapshot = check["snapshot"]; version = store.create_route_version(project_id, snapshot)
            checksum, object_key = AtomicRoutingPublisher(resolved.routing_dir).publish(snapshot["project"]["code"], version.version_no, snapshot)
            return store.mark_route_published(version.id, checksum, object_key)
        except ValueError as exc: raise _error(exc) from exc

    @app.post("/api/projects/{project_id}/routing/rollback/{version_no}")
    def rollback_routing(project_id: str, version_no: int):
        try:
            previous = store.published_route_version(project_id, version_no)
            snapshot = previous.snapshot_json; version = store.create_route_version(project_id, snapshot)
            checksum, object_key = AtomicRoutingPublisher(resolved.routing_dir).publish(snapshot["project"]["code"], version.version_no, snapshot)
            return store.mark_route_published(version.id, checksum, object_key)
        except ValueError as exc: raise _error(exc) from exc

    return app


# Importing a module must not create a schema or a local object-store directory.
# The command entry point below constructs the fully configured, verified app.
app = FastAPI(title="DataForge V7 bootstrap")


def main() -> None:
    import uvicorn
    verified_app = create_app(check_schema=True)
    uvicorn.run(verified_app, host=os.getenv("DATAFORGE_WEB_HOST", "0.0.0.0"), port=int(os.getenv("DATAFORGE_WEB_PORT", "8000")))
