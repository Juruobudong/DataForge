"""Operator runtime contracts shared by the Runner and every Executor.

Executors are deliberately narrow: they receive typed ``inputs``, frozen
``params`` and a ``context``, and return ``OperatorResult``.  They never read
the Snapshot or the catalog directly — the Runner resolves the exact
``(code, version)`` executor and hands it everything it needs.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class OperatorExecutionContext:
    flow_run_id: str
    node_id: str
    source_version_id: str | None = None
    requested_by: str = "system"
    # Runner-provided business context (sources/versions/type contracts/…).
    # Kept opaque so Executors never couple to the store or runner modules.
    runtime: dict[str, Any] = field(default_factory=dict)


@dataclass
class OperatorResult:
    outputs: list[dict[str, Any]] = field(default_factory=list)
    logs: list[dict[str, Any]] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class OperatorExecutor(Protocol):
    code: str
    version: int

    def execute(self, *, inputs: list[dict[str, Any]], params: dict[str, Any],
                context: OperatorExecutionContext) -> OperatorResult:
        ...
