from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .application import DEFAULT_PIPELINE_ID, DataForge
from .config import Settings
from .database import new_id
from .dataflow_studio import mount_dataflow_studio
from .errors import DataForgeError, NotFoundError, ValidationError
from .knowledge import KnowledgeService, validate_record


class RunRequest(BaseModel):
    source_version_id: str
    pipeline_id: str = DEFAULT_PIPELINE_ID
    engine: str | None = None


class KnowledgeJobRequest(BaseModel):
    name: str
    knowledge_type_id: str
    standard_pipeline_id: str | None = None
    source_version_ids: list[str]


class KnowledgeTypeRequest(BaseModel):
    name: str
    description: str = ""
    schema: dict[str, Any]


class StandardPipelinePublishRequest(BaseModel):
    name: str
    description: str = ""
    knowledge_type_id: str
    dataflow_pipeline_id: str
    sample_task_id: str
    version: int = 1
    make_default: bool = True


def _execute_run_safely(dataforge: DataForge, run_id: str) -> None:
    try:
        dataforge.execute_run(run_id)
    except Exception:
        # The application layer already persists the terminal failure and event.
        return


def _execute_knowledge_job_safely(service: KnowledgeService, job_id: str) -> None:
    try:
        service.execute_job(job_id)
    except Exception:
        return


def _enrich_sources(dataforge: DataForge) -> list[dict[str, Any]]:
    result = []
    for source in dataforge.store.list_sources():
        versions = dataforge.store.list_source_versions(source["id"])
        result.append(
            {
                **source,
                "version_count": len(versions),
                "latest_version": versions[0] if versions else None,
                "versions": versions,
            }
        )
    return result


def _read_asset_preview(dataforge: DataForge, asset_version_id: str, limit: int) -> list[Any]:
    version = dataforge.store.get_asset_version(asset_version_id)
    path = dataforge.blobs.resolve(version["blob_uri"])
    records: list[Any] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            records.append(json.loads(line))
            if len(records) >= limit:
                break
    return records


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or Settings.load()
    dataforge = DataForge(resolved)
    knowledge = KnowledgeService(dataforge)
    app = FastAPI(title="Medical DataForge", version="0.1.0")
    app.state.dataforge = dataforge
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?$",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(DataForgeError)
    async def dataforge_error_handler(_, exc: DataForgeError):
        status = 404 if isinstance(exc, NotFoundError) else 400
        return JSONResponse(
            status_code=status,
            content={"error": type(exc).__name__, "message": str(exc)},
        )

    @app.get("/api/health")
    def health():
        return dataforge.health()

    @app.get("/api/dashboard")
    def dashboard():
        sources = _enrich_sources(dataforge)
        runs = dataforge.store.list_runs()
        assets = dataforge.store.list_assets()
        knowledge_bases = dataforge.store.list_knowledge_bases()
        completed = sum(run["status"] == "completed" for run in runs)
        failed = sum(run["status"] == "failed" for run in runs)
        return {
            "counts": {
                "sources": len(sources),
                "source_versions": sum(item["version_count"] for item in sources),
                "runs": len(runs),
                "assets": len(assets),
            },
            "knowledge_counts": {"knowledge_bases": len(knowledge_bases)},
            "run_summary": {"completed": completed, "failed": failed, "active": len(runs) - completed - failed},
            "recent_runs": runs[:6],
            "recent_assets": assets[:6],
            "health": dataforge.health(),
        }

    @app.get("/api/sources")
    def list_sources():
        return _enrich_sources(dataforge)

    @app.post("/api/sources", status_code=201)
    async def upload_source(
        file: UploadFile = File(...),
        name: str | None = Form(None),
        kind: str = Form("file"),
        source_id: str | None = Form(None),
    ):
        upload_dir = resolved.state_dir / "uploads"
        temporary_dir = upload_dir / uuid.uuid4().hex
        temporary_dir.mkdir(parents=True, exist_ok=False)
        safe_name = Path(file.filename or "upload.txt").name
        temporary = temporary_dir / safe_name
        try:
            with temporary.open("wb") as handle:
                while chunk := await file.read(1024 * 1024):
                    handle.write(chunk)
            result = dataforge.sources.ingest(
                temporary,
                source_id=source_id,
                name=name or Path(safe_name).stem,
                kind=kind,
            )
            return {
                "source": result.source,
                "source_version": result.source_version,
                "created": result.created,
            }
        finally:
            await file.close()
            temporary.unlink(missing_ok=True)
            temporary_dir.rmdir()

    @app.get("/api/sources/{source_id}/versions")
    def list_source_versions(source_id: str):
        return dataforge.store.list_source_versions(source_id)

    studio = mount_dataflow_studio(app, resolved)
    app.state.dataflow_studio = studio
    knowledge.studio = studio

    @app.get("/api/dataflow-studio/status")
    def dataflow_studio_status():
        return studio.describe()

    @app.get("/api/dataflow-pipelines")
    def list_dataflow_pipelines():
        return studio.list_pipelines()

    @app.get("/api/dataflow-tasks")
    def list_dataflow_tasks(pipeline_id: str | None = None):
        return studio.list_tasks(pipeline_id)

    @app.post("/api/source-versions/{source_version_id}/send-to-dataflow")
    def send_source_to_dataflow(source_version_id: str):
        return studio.send_source(dataforge, source_version_id)

    @app.post("/api/dataflow-tasks/{task_id}/publish")
    def publish_dataflow_task(task_id: str):
        return studio.publish_task(dataforge, task_id)

    @app.get("/api/pipelines")
    def list_pipelines():
        return dataforge.store.list_pipelines()

    @app.get("/api/knowledge-types")
    def list_knowledge_types():
        return dataforge.store.list_knowledge_types()

    @app.post("/api/knowledge-types", status_code=201)
    def create_knowledge_type(payload: KnowledgeTypeRequest):
        if not payload.name.strip():
            raise ValidationError("请填写知识类型名称")
        required = payload.schema.get("required")
        properties = payload.schema.get("properties")
        if payload.schema.get("type") != "object" or not isinstance(required, list) or not required:
            raise ValidationError("知识类型必须是对象，并至少包含一个必填字段")
        if not isinstance(properties, dict) or any(field not in properties for field in required):
            raise ValidationError("每个必填字段都必须在字段定义中声明")
        supported = {"string", "integer", "array", "object"}
        invalid = [name for name, kind in properties.items() if kind not in supported]
        if invalid:
            raise ValidationError(f"字段类型暂不支持：{'、'.join(invalid)}")
        return dataforge.store.register_knowledge_type(
            new_id("ktype"),
            payload.name.strip(),
            payload.description.strip(),
            payload.schema,
        )

    @app.get("/api/standard-pipelines")
    def list_standard_pipelines(knowledge_type_id: str | None = None):
        return dataforge.store.list_standard_pipelines(knowledge_type_id)

    @app.post("/api/standard-pipelines/publish", status_code=201)
    def publish_standard_pipeline(payload: StandardPipelinePublishRequest):
        if not payload.name.strip():
            raise ValidationError("请填写标准流程名称")
        if payload.version < 1:
            raise ValidationError("流程版本必须大于 0")
        pipeline = next(
            (item for item in studio.list_pipelines() if item["id"] == payload.dataflow_pipeline_id),
            None,
        )
        if not pipeline:
            raise ValidationError("所选 DataFlow 流程不存在")
        if pipeline["is_draft"]:
            raise ValidationError("空白草稿不能发布，请先配置至少一个算子")
        task = studio.container.task_registry.get(payload.sample_task_id) if studio.container else None
        if not task or task.get("status") != "completed":
            raise ValidationError("请选择该流程一次已经成功完成的样本任务")
        if task.get("pipeline_id") != payload.dataflow_pipeline_id:
            raise ValidationError("样本任务与所选 DataFlow 流程不一致")
        knowledge_type = dataforge.store.get_knowledge_type(payload.knowledge_type_id)
        output_file = studio.task_output_file(payload.sample_task_id)
        checked = 0
        invalid: list[dict[str, Any]] = []
        with output_file.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                checked += 1
                errors = validate_record(json.loads(line), knowledge_type["schema"])
                if errors and len(invalid) < 20:
                    invalid.append({"line": line_number, "errors": errors})
        if not checked:
            raise ValidationError("样本任务输出为空，不能发布")
        if invalid:
            first = "；".join(invalid[0]["errors"])
            raise ValidationError(f"输出格式不符合“{knowledge_type['name']}”：{first}")
        published = dataforge.store.register_standard_pipeline(
            new_id("std"),
            payload.name.strip(),
            payload.knowledge_type_id,
            f"studio:{payload.dataflow_pipeline_id}",
            "dataflow-studio",
            payload.version,
            payload.description.strip() or f"由 DataFlow 流程“{pipeline['name']}”发布。",
            knowledge_type["schema"],
            "validated",
            payload.make_default,
        )
        return {**published, "checked_records": checked, "sample_task_id": payload.sample_task_id}

    @app.post("/api/standard-pipelines/{pipeline_id}/default")
    def set_default_standard_pipeline(pipeline_id: str):
        return dataforge.store.set_default_standard_pipeline(pipeline_id)

    @app.get("/api/knowledge-jobs")
    def list_knowledge_jobs():
        return dataforge.store.list_knowledge_jobs()

    @app.get("/api/knowledge-jobs/{job_id}")
    def get_knowledge_job(job_id: str):
        return dataforge.store.get_knowledge_job(job_id)

    @app.post("/api/knowledge-jobs", status_code=202)
    def start_knowledge_job(payload: KnowledgeJobRequest, background_tasks: BackgroundTasks):
        job = knowledge.create_job(
            name=payload.name,
            knowledge_type_id=payload.knowledge_type_id,
            standard_pipeline_id=payload.standard_pipeline_id,
            source_version_ids=payload.source_version_ids,
        )
        background_tasks.add_task(_execute_knowledge_job_safely, knowledge, job["id"])
        return job

    @app.get("/api/knowledge-bases")
    def list_knowledge_bases():
        return dataforge.store.list_knowledge_bases()

    @app.get("/api/knowledge-bases/{base_id}")
    def get_knowledge_base(base_id: str, page: int = 1, page_size: int = 50, query: str = ""):
        safe_page = max(1, page)
        safe_size = max(10, min(page_size, 100))
        total = dataforge.store.count_knowledge_records(base_id, query)
        return {
            "knowledge_base": dataforge.store.get_knowledge_base(base_id),
            "records": dataforge.store.list_knowledge_records(
                base_id, safe_size, (safe_page - 1) * safe_size, query
            ),
            "pagination": {
                "page": safe_page,
                "page_size": safe_size,
                "total": total,
                "pages": max(1, (total + safe_size - 1) // safe_size),
            },
            "query": query,
        }

    @app.get("/api/knowledge-records/{record_id}/lineage")
    def get_knowledge_record_lineage(record_id: str):
        return knowledge.get_record_lineage(record_id)

    @app.get("/api/runs")
    def list_runs():
        return dataforge.store.list_runs()

    @app.post("/api/runs", status_code=202)
    def start_run(payload: RunRequest, background_tasks: BackgroundTasks):
        if payload.engine not in {None, "dataflow", "native"}:
            raise ValidationError(f"Unknown processing engine: {payload.engine}")
        run = dataforge.create_run(
            payload.source_version_id,
            pipeline_id=payload.pipeline_id,
            engine_override=payload.engine,
        )
        background_tasks.add_task(_execute_run_safely, dataforge, run["id"])
        return run

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str):
        return {
            "run": dataforge.store.get_run(run_id),
            "events": dataforge.store.list_run_events(run_id),
        }

    @app.get("/api/assets")
    def list_assets():
        return dataforge.store.list_assets()

    @app.get("/api/assets/{asset_id}/versions")
    def list_asset_versions(asset_id: str):
        return dataforge.store.list_asset_versions(asset_id)

    @app.get("/api/asset-versions/{asset_version_id}/lineage")
    def get_lineage(asset_version_id: str):
        return dataforge.lineage(asset_version_id)

    @app.get("/api/asset-versions/{asset_version_id}/preview")
    def preview_asset(asset_version_id: str, limit: int = 5):
        return _read_asset_preview(dataforge, asset_version_id, max(1, min(limit, 50)))

    @app.get("/api/asset-versions/{asset_version_id}/download")
    def download_asset(asset_version_id: str):
        version = dataforge.store.get_asset_version(asset_version_id)
        path = dataforge.blobs.resolve(version["blob_uri"])
        return FileResponse(
            path,
            filename=f"{asset_version_id}.jsonl",
            media_type="application/x-ndjson",
        )

    frontend_dist = resolved.project_root / "frontend" / "dist"
    assets_dir = frontend_dist / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="frontend-assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def frontend(full_path: str):
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="API route not found")
        index = frontend_dist / "index.html"
        if index.is_file():
            return FileResponse(index)
        return {
            "message": "DataForge API is running; build the frontend with `npm run build` in frontend/.",
            "docs": "/docs",
        }

    return app


app = create_app()


def main() -> None:
    import uvicorn

    host = os.getenv("DATAFORGE_WEB_HOST", "127.0.0.1")
    port = int(os.getenv("DATAFORGE_WEB_PORT", "8000"))
    uvicorn.run("dataforge.web:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
