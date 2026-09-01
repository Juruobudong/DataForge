"""Builtin executor registry wiring.

For this migration the business logic still lives in the Runner's operator
table (``_run_operator``).  ``build_builtin_registry`` wraps that table behind
the ``OperatorExecutor`` contract so the Runner resolves executors by frozen
``(code, version)`` instead of dispatching on the ``ref`` string directly.  The
runner callable is injected to keep this module free of any runner import
(no import cycle).
"""
from __future__ import annotations

from typing import Any, Callable

from .base import OperatorExecutionContext, OperatorExecutor, OperatorResult
from .registry import OperatorExecutorRegistry
from .outcomes import capture_generation_metrics
from .graph_chunks import GraphChunkStage, uses_triple_chunks
from .diagnostics import OperatorExecutionError
from ..operator_runtime_contract import validate_runtime_requirements

RunnerCallable = Callable[[str, dict[str, Any], list[dict[str, Any]], dict[str, Any]], list[dict[str, Any]]]


class BuiltinOperatorExecutor:
    """Delegates to the legacy runner operator table for a single (code, version)."""

    def __init__(self, code: str, version: int, runner_callable: RunnerCallable, parameter_schema=None):
        self.code = code
        self.version = version
        self._runner = runner_callable
        self.parameter_schema = parameter_schema

    @capture_generation_metrics
    def execute(self, *, inputs: list[dict[str, Any]], params: dict[str, Any],
                context: OperatorExecutionContext) -> OperatorResult:
        from ..operator_parameters import validate_parameters
        schema = self.parameter_schema
        if schema is None:
            from ..catalog import catalog_by_code
            item = catalog_by_code().get(self.code)
            if not item or item["version"] != self.version:
                raise ValueError(f"缺少冻结参数契约：{self.code} v{self.version}")
            schema = item["parameter_schema"]
        params = validate_parameters(schema, params, node_id=context.node_id, runtime=True)
        if self.code == "entity-relation-extractor" and self.version == 1:
            from .joint_graph import JointGraphExecutor
            return JointGraphExecutor().execute(inputs=inputs, params=params, context=context)
        if self.code == "qa-extractor" and self.version == 1:
            from .qa import NativeQAExecutor
            return NativeQAExecutor().execute(inputs=inputs, params=params, context=context)
        runtime = {**dict(context.runtime or {}), "operator_version": self.version}
        if uses_triple_chunks(self.code, self.version, params):
            stage = GraphChunkStage(
                context.node_id, params, context.runtime.setdefault("generation", {}),
                isolate_llm_timeout=self.code == "relation-extractor",
            )
            runtime["graph_chunk_stage"] = stage
            try:
                outputs = self._runner(self.code, dict(params or {}), list(inputs), runtime)
            except Exception as exc:
                stage.diagnostics.append("stderr", stage.diagnostics.error(exc) + "\n")
                error = OperatorExecutionError(exc, stage.diagnostics)
                error.operator_metrics = stage.metrics
                raise error from None
            return OperatorResult(outputs=outputs, logs=stage.diagnostics.snapshot(), metrics=stage.metrics)
        from .derived_text import NATIVE_DERIVED_VERSIONS, prepare_generation, restore_evidence
        if NATIVE_DERIVED_VERSIONS.get(self.code) == self.version and not (
                self.code == "text-knowledge-mapper" and all("source_chunk" not in value for value in inputs)):
            kind = "text" if self.code == "text-knowledge-mapper" else params.get("knowledge_type")
            values, originals = prepare_generation(inputs, kind, context)
            runtime["generation"] = context.runtime.setdefault("generation", {})
            runtime["operator_version"] = self.version - 1
            outputs = self._runner(self.code, dict(params or {}), values, runtime) if values else []
            if originals:
                outputs = restore_evidence(outputs, originals, kind, context)
            return OperatorResult(outputs=outputs)
        outputs = self._runner(self.code, dict(params or {}), list(inputs), runtime)
        return OperatorResult(outputs=outputs)


def build_builtin_registry(runner_callable: RunnerCallable, catalog: dict[str, dict[str, Any]]) -> OperatorExecutorRegistry:
    registry = OperatorExecutorRegistry()
    entries = {(code, item.get("version", 1)): item for code, item in catalog.items()}
    for (code, _), item in entries.items():
        runtime = validate_runtime_requirements(item.get("runtime_requirements"))
        if runtime["driver"] != "builtin":
            continue
        version = int(item.get("version", 1))
        registry.register(BuiltinOperatorExecutor(code, version, runner_callable, item["parameter_schema"]))
    return registry
