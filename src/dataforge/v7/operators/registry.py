"""Version-pinned operator executor registry.

Resolution is exact on ``(code, version)`` — never "latest".  A published
Snapshot freezes each node's ``operator_version``, so re-running an old
Snapshot must keep executing the version it was published with, even after a
newer version appears in the catalog.
"""
from __future__ import annotations

from typing import Any

from .base import OperatorExecutor


class OperatorExecutorRegistry:
    def __init__(self) -> None:
        self._executors: dict[tuple[str, int], OperatorExecutor] = {}

    def register(self, executor: OperatorExecutor) -> None:
        self._executors[(executor.code, executor.version)] = executor

    def resolve(self, code: str, version: int) -> OperatorExecutor:
        executor = self._executors.get((code, version))
        if executor is None:
            raise ValueError(f"Operator {code} v{version} 未注册（禁止回退到最新版本）")
        return executor

    def versions_for(self, code: str) -> tuple[int, ...]:
        return tuple(sorted(version for (registered_code, version) in self._executors if registered_code == code))

    def codes(self) -> tuple[str, ...]:
        return tuple(sorted({code for code, _ in self._executors}))

    def __len__(self) -> int:
        return len(self._executors)
