"""Operator runtime: version-pinned executors resolved by the Runner."""
from .base import OperatorExecutionContext, OperatorExecutor, OperatorResult
from .builtin import BuiltinOperatorExecutor, build_builtin_registry
from .registry import OperatorExecutorRegistry

__all__ = [
    "OperatorExecutionContext",
    "OperatorExecutor",
    "OperatorResult",
    "BuiltinOperatorExecutor",
    "build_builtin_registry",
    "OperatorExecutorRegistry",
]
