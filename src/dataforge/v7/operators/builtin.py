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

RunnerCallable = Callable[[str, dict[str, Any], list[dict[str, Any]], dict[str, Any]], list[dict[str, Any]]]


class BuiltinOperatorExecutor:
    """Delegates to the legacy runner operator table for a single (code, version)."""

    def __init__(self, code: str, version: int, runner_callable: RunnerCallable):
        self.code = code
        self.version = version
        self._runner = runner_callable

    def execute(self, *, inputs: list[dict[str, Any]], params: dict[str, Any],
                context: OperatorExecutionContext) -> OperatorResult:
        outputs = self._runner(self.code, dict(params or {}), list(inputs), dict(context.runtime or {}))
        return OperatorResult(outputs=outputs)


def build_builtin_registry(runner_callable: RunnerCallable, catalog: dict[str, dict[str, Any]]) -> OperatorExecutorRegistry:
    registry = OperatorExecutorRegistry()
    for code, item in catalog.items():
        version = int(item.get("version", 1))
        registry.register(BuiltinOperatorExecutor(code, version, runner_callable))
    return registry
