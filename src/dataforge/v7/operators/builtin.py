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

RunnerCallable = Callable[[str, dict[str, Any], list[dict[str, Any]], dict[str, Any]], list[dict[str, Any]]]


class BuiltinOperatorExecutor:
    """Delegates to the legacy runner operator table for a single (code, version)."""

    def __init__(self, code: str, version: int, runner_callable: RunnerCallable):
        self.code = code
        self.version = version
        self._runner = runner_callable

    @capture_generation_metrics
    def execute(self, *, inputs: list[dict[str, Any]], params: dict[str, Any],
                context: OperatorExecutionContext) -> OperatorResult:
        runtime = {**dict(context.runtime or {}), "operator_version": self.version}
        outputs = self._runner(self.code, dict(params or {}), list(inputs), runtime)
        return OperatorResult(outputs=outputs)


def build_builtin_registry(runner_callable: RunnerCallable, catalog: dict[str, dict[str, Any]]) -> OperatorExecutorRegistry:
    from ..catalog import LEGACY_CATALOG_SEEDS
    registry = OperatorExecutorRegistry()
    entries = {(item["code"], item["version"]): item for item in LEGACY_CATALOG_SEEDS if item["code"] in catalog}
    entries.update({(code, item.get("version", 1)): item for code, item in catalog.items()})
    for (code, _), item in entries.items():
        if (item.get("runtime_requirements") or {}).get("provider", "dataforge") != "dataforge":
            continue
        version = int(item.get("version", 1))
        registry.register(BuiltinOperatorExecutor(code, version, runner_callable))
        if code in {"prompt-generator", "structured-knowledge-generator", "entity-extractor"}:
            for legacy_version in (4, 5):
                if legacy_version < version and (code, legacy_version) not in entries:
                    registry.register(BuiltinOperatorExecutor(code, legacy_version, runner_callable))
    return registry
