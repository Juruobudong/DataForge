"""Build an exact registry from frozen execution nodes, never the latest code."""
from .builtin import BuiltinOperatorExecutor
from .dataflow import DataFlowOperatorExecutor
from .registry import OperatorExecutorRegistry
from ..operator_runtime_contract import validate_runtime_requirements


def build_runtime_registry(runner_callable, definition):
    registry = OperatorExecutorRegistry()
    seen = {}
    for node in definition.get("nodes", []):
        if node.get("kind") != "operator":
            continue
        code, version = node["ref"], node.get("operator_version")
        if version is None:
            raise ValueError(f"算子 {code} 缺少冻结 OperatorVersion")
        frozen = node.get("operator_spec")
        if frozen and (frozen.get("code") != code or frozen.get("version") != version):
            raise ValueError(f"算子 {code} v{version} 冻结标识不一致")
        if (code, version) in seen:
            if seen[(code, version)] != frozen:
                raise ValueError(f"算子 {code} v{version} 存在冲突的冻结实现")
            continue
        seen[(code, version)] = frozen
        if not frozen:
            raise ValueError(f"算子 {code} v{version} 缺少冻结实现，禁止解析当前 Catalog")
        spec = {**frozen, "code": code, "version": version}
        runtime = validate_runtime_requirements(spec.get("runtime_requirements"))
        driver = runtime["driver"]
        if driver == "builtin":
            registry.register(BuiltinOperatorExecutor(code, version, runner_callable, spec["parameter_schema"]))
        elif driver == "dataflow":
            registry.register(DataFlowOperatorExecutor(spec))
        elif driver == "custom":
            from .custom import CustomOperatorExecutor
            registry.register(CustomOperatorExecutor(spec))
    return registry
