from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any, Protocol

from ..errors import EngineUnavailableError, ValidationError
from ..models import ProcessingResult
from .native import NativeMedicalEngine, asset_schema, read_jsonl


class Engine(Protocol):
    name: str

    def run(self, input_file: Path, work_dir: Path, parameters: dict[str, Any]) -> ProcessingResult: ...


NativeEngine = NativeMedicalEngine


class DataFlowEngine:
    name = "dataflow"

    def __init__(self, dataflow_path: Path | None):
        self.dataflow_path = dataflow_path

    def _bootstrap(self):
        if not self.dataflow_path or not (self.dataflow_path / "dataflow").is_dir():
            raise EngineUnavailableError(
                "DataFlow repository was not found. Set DATAFORGE_DATAFLOW_PATH or use --engine native."
            )
        path_string = str(self.dataflow_path)
        if path_string not in sys.path:
            sys.path.insert(0, path_string)
        try:
            dataflow = importlib.import_module("dataflow")
            pipeline_module = importlib.import_module("dataforge.processing.dataflow_pipeline")
        except ModuleNotFoundError as exc:
            raise EngineUnavailableError(
                f"DataFlow dependency is missing: {exc.name}. Run `uv sync --extra dataflow`."
            ) from exc
        return dataflow, pipeline_module

    def run(self, input_file: Path, work_dir: Path, parameters: dict[str, Any]) -> ProcessingResult:
        dataflow, pipeline_module = self._bootstrap()
        cache_dir = work_dir / "dataflow-cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        # 通用文档处理流程；标识符（medical_document / MedicalDocumentPipeline）沿用历史命名，不限定医疗领域。
        prefix = "medical_document"
        pipeline = pipeline_module.MedicalDocumentPipeline(
            input_file=input_file,
            cache_dir=cache_dir,
            file_prefix=prefix,
            chunk_size=int(parameters.get("chunk_size", 600)),
            chunk_overlap=int(parameters.get("chunk_overlap", 80)),
        )
        pipeline.compile()
        pipeline.forward()
        output = cache_dir / f"{prefix}_step2.jsonl"
        if not output.is_file():
            raise ValidationError(f"DataFlow did not produce the expected output: {output}")
        records = read_jsonl(output)
        input_count = len(read_jsonl(input_file))
        metrics = {
            "input_records": input_count,
            "output_chunks": len(records),
            "deduplicated_chunks": None,
            "average_chunk_chars": round(
                sum(int(item.get("char_count", 0)) for item in records) / len(records), 2
            )
            if records
            else 0,
            "compiled_operators": ["NormalizeMedicalTextOperator", "ChunkMedicalTextOperator"],
        }
        return ProcessingResult(
            output_file=output,
            engine_name=self.name,
            engine_version=str(getattr(dataflow, "__version__", "unknown")),
            record_count=len(records),
            schema=asset_schema(),
            metrics=metrics,
        )


def create_engine(name: str, dataflow_path: Path | None) -> Engine:
    if name == "dataflow":
        return DataFlowEngine(dataflow_path)
    if name == "native":
        return NativeMedicalEngine()
    raise ValidationError(f"Unknown processing engine: {name}")
