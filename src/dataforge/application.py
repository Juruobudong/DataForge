from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from .blobs import BlobStore
from .config import Settings
from .database import MetadataStore, new_id
from .errors import ValidationError
from .ingestion import SourceService, materialize_source_records
from .models import FlowResult
from .processing import create_engine


DEFAULT_PIPELINE_ID = "medical-document-v1"
DEFAULT_PIPELINE = {
    "kind": "medical_document",
    "description": "对医疗文档进行标准化、分段、去重并发布为数据资产。",
    "parameters": {"chunk_size": 600, "chunk_overlap": 80},
    "operators": ["NormalizeMedicalTextOperator", "ChunkMedicalTextOperator"],
    "output_asset_type": "medical_chunk_collection",
}


class DataForge:
    """Application service coordinating source, processing, and asset lifecycles."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.settings.ensure_directories()
        self.store = MetadataStore(settings.database_path)
        self.store.initialize()
        self.blobs = BlobStore(settings.blobs_dir)
        self.sources = SourceService(self.store, self.blobs)
        self._seed_defaults()

    @classmethod
    def open(
        cls,
        project_root: str | Path | None = None,
        dataflow_path: str | Path | None = None,
    ) -> "DataForge":
        return cls(Settings.load(project_root, dataflow_path))

    def _seed_defaults(self) -> None:
        self.store.register_pipeline(
            DEFAULT_PIPELINE_ID,
            "医疗文档标准化处理",
            1,
            "dataflow",
            DEFAULT_PIPELINE,
        )
        from .knowledge import KnowledgeService

        KnowledgeService(self).seed()

    def register_pipeline(self, definition_file: str | Path) -> dict[str, Any]:
        path = Path(definition_file).expanduser().resolve()
        try:
            definition = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValidationError(f"Cannot read pipeline definition: {exc}") from exc
        required = {"id", "name", "version", "engine", "definition"}
        missing = sorted(required - definition.keys())
        if missing:
            raise ValidationError(f"Pipeline definition is missing: {', '.join(missing)}")
        return self.store.register_pipeline(
            definition["id"],
            definition["name"],
            int(definition["version"]),
            definition["engine"],
            definition["definition"],
        )

    def run(
        self,
        source_version_id: str,
        *,
        pipeline_id: str = DEFAULT_PIPELINE_ID,
        engine_override: str | None = None,
    ) -> FlowResult:
        run = self.create_run(
            source_version_id,
            pipeline_id=pipeline_id,
            engine_override=engine_override,
        )
        return self.execute_run(run["id"])

    def create_run(
        self,
        source_version_id: str,
        *,
        pipeline_id: str = DEFAULT_PIPELINE_ID,
        engine_override: str | None = None,
    ) -> dict[str, Any]:
        source_version = self.store.get_source_version(source_version_id)
        self.store.get_source(source_version["source_id"])
        pipeline = self.store.get_pipeline(pipeline_id)
        if not pipeline["active"]:
            raise ValidationError(f"Pipeline is inactive: {pipeline_id}")
        engine_name = engine_override or pipeline["engine"]

        run_id = new_id("run")
        work_dir = self.settings.runs_dir / run_id
        work_dir.mkdir(parents=True, exist_ok=False)
        run = self.store.create_run(
            pipeline_id,
            source_version_id,
            engine_name,
            work_dir,
            run_id=run_id,
        )
        self.store.add_run_event(run_id, "created", "处理任务已创建", {"engine": engine_name})
        return run

    def execute_run(self, run_id: str) -> FlowResult:
        pending_run = self.store.get_run(run_id)
        if pending_run["status"] != "pending":
            raise ValidationError(
                f"Run {run_id} cannot be executed from status {pending_run['status']}"
            )
        source_version_id = pending_run["source_version_id"]
        pipeline_id = pending_run["pipeline_id"]
        engine_name = pending_run["engine"]
        work_dir = Path(pending_run["work_dir"])
        source_version = self.store.get_source_version(source_version_id)
        source = self.store.get_source(source_version["source_id"])
        pipeline = self.store.get_pipeline(pipeline_id)

        try:
            self.store.transition_run(run_id, "preparing")
            source_blob = self.blobs.resolve(source_version["blob_uri"])
            input_file = work_dir / "input" / "source_records.jsonl"
            input_records = materialize_source_records(source_blob, source_version, input_file)
            self.store.add_run_event(
                run_id,
                "input_materialized",
                "源文件已转换为待处理数据",
                {"records": input_records, "input_file": str(input_file)},
            )

            self.store.transition_run(run_id, "running")
            self.store.add_run_event(run_id, "processing_started", "数据处理已开始")
            engine = create_engine(engine_name, self.settings.dataflow_path)
            parameters = pipeline["definition"].get("parameters", {})
            processing = engine.run(input_file, work_dir, parameters)
            if processing.record_count == 0:
                raise ValidationError("Processing completed without any publishable asset records")
            stats = {
                **processing.metrics,
                "engine": processing.engine_name,
                "engine_version": processing.engine_version,
                "pipeline_id": pipeline_id,
                "pipeline_version": pipeline["version"],
            }
            self.store.add_run_event(
                run_id,
                "processing_completed",
                "数据处理完成，准备生成资产",
                {"records": processing.record_count, "output_file": str(processing.output_file)},
            )

            self.store.transition_run(run_id, "publishing", stats=stats)
            blob_uri, sha256, size_bytes = self.blobs.put_file(processing.output_file)
            logical_key = f"{source['id']}:{pipeline_id}:{pipeline['definition']['output_asset_type']}"
            asset, asset_version = self.store.publish_asset(
                logical_key=logical_key,
                name=f"{source['name']} / 标准化医疗数据",
                asset_type=pipeline["definition"]["output_asset_type"],
                run_id=run_id,
                source_version_id=source_version_id,
                blob_uri=blob_uri,
                sha256=sha256,
                size_bytes=size_bytes,
                record_count=processing.record_count,
                schema=processing.schema,
            )
            self.store.add_run_event(
                run_id,
                "asset_published",
                "数据资产版本已生成",
                {"asset_id": asset["id"], "asset_version_id": asset_version["id"]},
            )
            run = self.store.transition_run(
                run_id,
                "completed",
                stats=stats,
                asset_version_id=asset_version["id"],
            )
            self.store.add_run_event(run_id, "completed", "处理任务已完成")
            return FlowResult(source, source_version, run, asset, asset_version)
        except Exception as exc:
            current = self.store.get_run(run_id)
            if current["status"] not in {"completed", "failed"}:
                self.store.transition_run(run_id, "failed", error=str(exc))
                self.store.add_run_event(
                    run_id,
                    "failed",
                    "处理任务失败",
                    {"error_type": type(exc).__name__, "error": str(exc)},
                )
            raise

    def flow(
        self,
        file_path: str | Path,
        *,
        name: str | None = None,
        kind: str = "file",
        source_id: str | None = None,
        pipeline_id: str = DEFAULT_PIPELINE_ID,
        engine_override: str | None = None,
    ) -> FlowResult:
        ingestion = self.sources.ingest(
            file_path,
            source_id=source_id,
            name=name,
            kind=kind,
        )
        return self.run(
            ingestion.source_version["id"],
            pipeline_id=pipeline_id,
            engine_override=engine_override,
        )

    def lineage(self, asset_version_id: str) -> dict[str, Any]:
        lineage = self.store.get_lineage(asset_version_id)
        lineage["events"] = self.store.list_run_events(lineage["run_id"])
        return lineage

    def export_asset(
        self,
        asset_version_id: str,
        destination: str | Path,
        *,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        version = self.store.get_asset_version(asset_version_id)
        target = Path(destination).expanduser().resolve()
        if target.exists() and not overwrite:
            raise ValidationError(f"Export destination already exists: {target}")
        target.parent.mkdir(parents=True, exist_ok=True)
        source = self.blobs.resolve(version["blob_uri"])
        shutil.copyfile(source, target)
        return {
            "asset_version_id": asset_version_id,
            "destination": str(target),
            "sha256": version["sha256"],
            "size_bytes": version["size_bytes"],
            "record_count": version["record_count"],
        }

    def health(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "project_root": str(self.settings.project_root),
            "state_dir": str(self.settings.state_dir),
            "database": str(self.settings.database_path),
            "dataflow_path": str(self.settings.dataflow_path) if self.settings.dataflow_path else None,
            "dataflow_present": bool(
                self.settings.dataflow_path and (self.settings.dataflow_path / "dataflow").is_dir()
            ),
        }
