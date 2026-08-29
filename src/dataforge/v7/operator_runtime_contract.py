"""Frozen operator runtime identity shared by Catalog, Compiler and Runner."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any


DRIVER_EXECUTORS = {
    "builtin": frozenset({"dataforge-native"}),
    "dataflow": frozenset({"dataflow-storage", "dataflow-llm"}),
    "custom": frozenset({"dataflow-storage", "dataflow-llm", "custom-native"}),
}
EXECUTORS = frozenset().union(*DRIVER_EXECUTORS.values())


def validate_runtime_requirements(requirements: Mapping[str, Any] | None) -> Mapping[str, Any]:
    """Validate dispatch identity without consulting source, catalog group or code."""
    if not isinstance(requirements, Mapping):
        raise ValueError("算子 runtime_requirements 必须是对象")
    driver = requirements.get("driver")
    executor = requirements.get("executor")
    if not isinstance(driver, str) or not driver:
        raise ValueError("算子 runtime_requirements 缺少 driver")
    if driver not in DRIVER_EXECUTORS:
        raise ValueError(f"不支持的算子 Runtime Driver：{driver}")
    if not isinstance(executor, str) or not executor:
        raise ValueError("算子 runtime_requirements 缺少 executor")
    if executor not in EXECUTORS:
        raise ValueError(f"不支持的算子 Runtime Executor：{executor}")
    if executor not in DRIVER_EXECUTORS[driver]:
        raise ValueError(f"Runtime Driver {driver} 不能使用 Executor {executor}")
    return requirements


def requires_external_runtime(requirements: Mapping[str, Any] | None) -> bool:
    """Return whether the selected inner executor needs the packaged worker runtime."""
    value = validate_runtime_requirements(requirements)
    return value["executor"] != "dataforge-native"
