from __future__ import annotations

import json
import copy
import shutil
import sys
import threading
import types
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .application import DataForge
from .config import Settings
from .database import new_id
from .errors import ValidationError
from .ingestion import materialize_source_records


@dataclass
class StudioStatus:
    available: bool = False
    frontend_available: bool = False
    backend_available: bool = False
    message: str = "DataFlow 加工中心尚未初始化"
    operator_count: int = 0
    pipeline_count: int = 0


class DataFlowStudio:
    """Mount the upstream DataFlow WebUI while keeping its state inside DataForge."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.root = settings.project_root / "third_party" / "dataflow_webui"
        self.backend_root = self.root / "backend"
        self.frontend_dist = self.root / "frontend" / "dist"
        self.state_root = settings.state_dir / "dataflow-studio"
        self.container: Any | None = None
        self._task_registry_lock = threading.Lock()
        self.status = StudioStatus(frontend_available=self.frontend_dist.is_dir())

    def mount(self, app: FastAPI) -> StudioStatus:
        if not self.backend_root.is_dir():
            self.status.message = "未找到 DataFlow 工作台后端源码"
            return self.status
        if not self.settings.dataflow_path or not (self.settings.dataflow_path / "dataflow").is_dir():
            self.status.message = "未找到 DataFlow 核心项目"
            self._mount_frontend(app)
            return self.status

        try:
            self._prepare_imports_and_state()
            from app.api.v1.router import api_router
            from app.core.container import container

            container.init()
            self.container = container
            app.include_router(api_router, prefix="/api/v1")
            self.status.backend_available = True
            self.status.operator_count = len(container.operator_registry.get_op_list(lang="zh"))
            self.status.pipeline_count = len(container.pipeline_registry.list_pipelines())
            self.status.message = "DataFlow 原工作台已接入"
        except Exception as exc:
            self.status.message = f"DataFlow 工作台初始化失败：{exc}"

        self._mount_frontend(app)
        self.status.available = self.status.frontend_available and self.status.backend_available
        return self.status

    def _prepare_imports_and_state(self) -> None:
        for path in (self.settings.dataflow_path, self.backend_root):
            value = str(path)
            if value not in sys.path:
                sys.path.insert(0, value)

        # DataFlow's serving package eagerly imports every local GPU/audio/cloud
        # backend.  The WebUI only requires the HTTP API serving class at startup,
        # so expose that lightweight module without forcing unrelated runtimes.
        serving_path = self.settings.dataflow_path / "dataflow" / "serving"
        if "dataflow.serving" not in sys.modules:
            serving_package = types.ModuleType("dataflow.serving")
            serving_package.__path__ = [str(serving_path)]
            serving_package.__package__ = "dataflow.serving"
            sys.modules["dataflow.serving"] = serving_package
        from dataflow.serving.api_llm_serving_request import APILLMServing_request

        sys.modules["dataflow.serving"].APILLMServing_request = APILLMServing_request

        from app.core.config import settings as upstream

        data_dir = self.state_root / "data"
        core_dir = data_dir / "dataflow_core"
        resources_dir = self.state_root / "resources"
        for directory in (data_dir, core_dir, resources_dir, self.state_root / "cache"):
            directory.mkdir(parents=True, exist_ok=True)

        source_pipelines = self.settings.dataflow_path / "dataflow" / "statics" / "pipelines" / "api_pipelines"
        target_pipelines = core_dir / "api_pipelines"
        if source_pipelines.is_dir():
            shutil.copytree(source_pipelines, target_pipelines, dirs_exist_ok=True)
        target_pipelines.mkdir(parents=True, exist_ok=True)
        (core_dir / "example_data").mkdir(parents=True, exist_ok=True)

        upstream.BASE_DIR = str(self.state_root)
        upstream.DATA_REGISTRY = str(data_dir / "data_registry.yaml")
        upstream.TASK_REGISTRY = str(data_dir / "task_registry.json")
        upstream.PIPELINE_REGISTRY = str(data_dir / "pipeline_registry.json")
        upstream.SERVING_REGISTRY = str(data_dir / "serving_registry.yaml")
        upstream.TEXT2SQL_DATABASE_REGISTRY = str(data_dir / "text2sql_database_registry.yaml")
        upstream.TEXT2SQL_DATABASE_MANAGER_REGISTRY = str(data_dir / "text2sql_database_manager_registry.yaml")
        upstream.DATAFLOW_CORE_DIR = str(core_dir)
        upstream.OPS_JSON_PATH = str(data_dir / "ops.json")
        upstream.PREFERENCES_PATH = str(data_dir / "user_preferences.json")
        upstream.SQLITE_DB_DIR = str(data_dir / "text2sql_dbs")
        upstream.CACHE_DIR = str(self.state_root / "cache")
        upstream.RESOURCE_DIR = str(resources_dir)

    def _mount_frontend(self, app: FastAPI) -> None:
        if self.frontend_dist.is_dir():
            app.mount("/studio", StaticFiles(directory=self.frontend_dist, html=True), name="dataflow-studio")
            self.status.frontend_available = True

    def describe(self) -> dict[str, Any]:
        return asdict(self.status)

    def list_pipelines(self) -> list[dict[str, Any]]:
        if not self.container or not self.status.backend_available:
            return []
        result = []
        for pipeline in self.container.pipeline_registry.list_pipelines():
            config = pipeline.get("config") or {}
            result.append(
                {
                    "id": pipeline.get("id"),
                    "name": pipeline.get("name") or "未命名流程",
                    "operator_count": len(config.get("operators") or []),
                    "updated_at": pipeline.get("updated_at"),
                    # A saved workflow may intentionally defer its input dataset:
                    # DataForge injects the selected source document at run time.
                    "is_draft": not config.get("operators"),
                    "tags": pipeline.get("tags") or [],
                }
            )
        return result

    def list_tasks(self, pipeline_id: str | None = None) -> list[dict[str, Any]]:
        if not self.container or not self.status.backend_available:
            return []
        tasks = self.container.task_registry.list_executions()
        if pipeline_id:
            tasks = [item for item in tasks if item.get("pipeline_id") == pipeline_id]
        tasks.sort(key=lambda item: item.get("completed_at") or item.get("started_at") or "", reverse=True)
        return [
            {
                "task_id": item.get("task_id"),
                "pipeline_id": item.get("pipeline_id"),
                "status": item.get("status"),
                "started_at": item.get("started_at"),
                "completed_at": item.get("completed_at"),
            }
            for item in tasks
        ]

    def task_output_file(self, task_id: str) -> Path:
        if not self.container:
            raise ValidationError("DataFlow 调试台未就绪")
        task = self.container.task_registry.get(task_id)
        if not task:
            raise ValidationError(f"DataFlow 任务不存在：{task_id}")
        results = (task.get("output") or {}).get("execution_results") or []
        if not results:
            raise ValidationError("该任务没有可验证的处理结果")
        persisted = (task.get("output") or {}).get("final_output_file")
        if persisted and Path(persisted).is_file():
            return Path(persisted)
        completed = [
            item for item in self.container.task_registry.list_executions()
            if item.get("status") == "completed"
        ]
        completed.sort(key=lambda item: item.get("completed_at") or "", reverse=True)
        if completed and completed[0].get("task_id") != task_id:
            raise ValidationError("该历史任务没有独立保存输出，请重新运行一次样例后再发布")
        step = int(results[-1].get("index", len(results) - 1))
        filename = f"dataflow_cache_step_{step}.jsonl"
        candidates = [
            self.state_root / "cache" / "task_outputs" / f"{task_id}.jsonl",
            self.state_root / "cache" / filename,
        ]
        for candidate in candidates:
            if candidate.is_file():
                return candidate
        raise ValidationError(f"找不到任务 {task_id} 的最终输出文件")

    def run_pipeline_for_source(
        self, dataforge: DataForge, source_version_id: str, pipeline_id: str
    ) -> dict[str, Any]:
        if not self.container or not self.status.backend_available:
            raise ValidationError(self.status.message)
        imported = self.send_source(dataforge, source_version_id)
        pipeline = self.container.pipeline_registry.get_pipeline(pipeline_id)
        if not pipeline:
            raise ValidationError(f"DataFlow 流程不存在：{pipeline_id}")
        config = copy.deepcopy(pipeline.get("config") or {})
        dataset_id = imported["dataset"]["id"]
        existing_input = config.get("input_dataset")
        config["input_dataset"] = (
            {**existing_input, "id": dataset_id}
            if isinstance(existing_input, dict)
            else dataset_id
        )
        from app.services.dataflow_engine import dataflow_engine

        with self._task_registry_lock:
            task_id, _, _ = self.container.task_registry.start_execution(config=config)
        result = dataflow_engine.run(config, task_id, execution_path=None)
        with self._task_registry_lock:
            registry = self.container.task_registry._read()
            registry["tasks"][task_id].update(result)
            self.container.task_registry._write(registry)
        if result.get("status") != "completed":
            detail = (result.get("output") or {}).get("error") or "DataFlow 流程执行失败"
            raise ValidationError(str(detail))
        return {
            "task_id": task_id,
            "output_file": self.task_output_file(task_id),
            "source_version": dataforge.store.get_source_version(source_version_id),
            "input_file": Path(imported["dataset"]["root"]),
        }

    def send_source(self, dataforge: DataForge, source_version_id: str) -> dict[str, Any]:
        if not self.container or not self.status.backend_available:
            raise ValidationError(self.status.message)

        version = dataforge.store.get_source_version(source_version_id)
        source = dataforge.store.get_source(version["source_id"])
        source_blob = dataforge.blobs.resolve(version["blob_uri"])
        import_dir = self.state_root / "imports"
        target = import_dir / f"{source_version_id}.jsonl"
        record_count = materialize_source_records(source_blob, version, target)
        dataset = self.container.dataset_registry.add_or_update(
            {
                "name": f"{source['name']}（版本 {version['version_no']}）",
                "root": str(target),
                "pipeline": "DataForge 文件入口",
                "meta": {
                    "dataforge_source_id": source["id"],
                    "dataforge_source_version_id": source_version_id,
                    "original_filename": version["original_filename"],
                },
            }
        )
        return {"dataset": dataset, "record_count": record_count, "studio_url": "/studio/#/m/"}

    def publish_task(self, dataforge: DataForge, task_id: str) -> dict[str, Any]:
        """Publish a completed upstream task result as a versioned DataForge asset."""
        if not self.container or not self.status.backend_available:
            raise ValidationError(self.status.message)
        task = self.container.task_registry.get(task_id)
        if not task:
            raise ValidationError(f"DataFlow 任务不存在：{task_id}")
        if task.get("status") != "completed":
            raise ValidationError("只有已经完成的 DataFlow 任务才能发布为数据资产")

        for existing in dataforge.store.list_runs():
            if existing.get("stats", {}).get("dataflow_task_id") == task_id and existing.get("asset_version_id"):
                return {
                    "run": existing,
                    "asset_version": dataforge.store.get_asset_version(existing["asset_version_id"]),
                    "created": False,
                }

        pipeline_config = task.get("pipeline_config") or {}
        dataset_ref = pipeline_config.get("input_dataset") or {}
        dataset_id = dataset_ref.get("id") if isinstance(dataset_ref, dict) else dataset_ref
        dataset = self.container.dataset_registry.get(dataset_id) if dataset_id else None
        source_version_id = (dataset or {}).get("meta", {}).get("dataforge_source_version_id")
        if not source_version_id:
            raise ValidationError("该任务的输入不是从 DataForge 文件管理送入的，无法建立完整来源追溯")
        source_version = dataforge.store.get_source_version(source_version_id)
        source = dataforge.store.get_source(source_version["source_id"])

        execution_results = (task.get("output") or {}).get("execution_results") or []
        if not execution_results:
            raise ValidationError("该任务没有可发布的处理结果")
        step_number = int(execution_results[-1].get("index", len(execution_results) - 1)) + 1
        output_file = self.state_root / "cache" / f"{task_id}_output" / f"dataflow_cache_step_step{step_number}.jsonl"
        if not output_file.is_file():
            raise ValidationError(f"找不到 DataFlow 任务结果文件：{output_file.name}")

        record_count = 0
        schema: dict[str, str] = {}
        with output_file.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                record_count += 1
                if not schema:
                    first = json.loads(line)
                    if isinstance(first, dict):
                        schema = {key: type(value).__name__ for key, value in first.items()}
        if not record_count:
            raise ValidationError("DataFlow 任务结果为空，不能发布为数据资产")

        upstream_pipeline_id = task.get("pipeline_id") or "custom"
        upstream_pipeline = self.container.pipeline_registry.get_pipeline(upstream_pipeline_id) or {}
        pipeline_id = f"dataflow-studio:{upstream_pipeline_id}"
        dataforge.store.register_pipeline(
            pipeline_id,
            upstream_pipeline.get("name") or "DataFlow 可视化流程",
            1,
            "dataflow-studio",
            {"upstream_pipeline": upstream_pipeline, "output_asset_type": "dataflow_dataset"},
        )
        run_id = new_id("run")
        run = dataforge.store.create_run(
            pipeline_id,
            source_version_id,
            "dataflow-studio",
            output_file.parent,
            run_id=run_id,
        )
        stats = {
            "dataflow_task_id": task_id,
            "input_records": (dataset or {}).get("num_samples", 0),
            "output_chunks": record_count,
            "pipeline_version": 1,
        }
        dataforge.store.add_run_event(run_id, "created", "DataFlow 任务成果开始发布", {"task_id": task_id})
        dataforge.store.transition_run(run_id, "preparing", stats=stats)
        dataforge.store.transition_run(run_id, "running", stats=stats)
        dataforge.store.add_run_event(run_id, "processing_completed", "DataFlow 可视化流程已完成", {"records": record_count})
        dataforge.store.transition_run(run_id, "publishing", stats=stats)
        blob_uri, sha256, size_bytes = dataforge.blobs.put_file(output_file)
        asset, asset_version = dataforge.store.publish_asset(
            logical_key=f"{source['id']}:{pipeline_id}:dataflow_dataset",
            name=f"{source['name']} / DataFlow 处理成果",
            asset_type="dataflow_dataset",
            run_id=run_id,
            source_version_id=source_version_id,
            blob_uri=blob_uri,
            sha256=sha256,
            size_bytes=size_bytes,
            record_count=record_count,
            schema=schema,
        )
        dataforge.store.add_run_event(run_id, "asset_published", "DataFlow 成果已生成数据资产", {"asset_version_id": asset_version["id"]})
        run = dataforge.store.transition_run(run_id, "completed", stats=stats, asset_version_id=asset_version["id"])
        dataforge.store.add_run_event(run_id, "completed", "发布完成")
        return {"run": run, "asset": asset, "asset_version": asset_version, "created": True}


def mount_dataflow_studio(app: FastAPI, settings: Settings) -> DataFlowStudio:
    studio = DataFlowStudio(settings)
    studio.mount(app)
    return studio
