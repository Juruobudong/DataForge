"""Build an exact registry from frozen execution nodes, never the latest code."""
from .builtin import BuiltinOperatorExecutor
from .dataflow import DataFlowOperatorExecutor
from .registry import OperatorExecutorRegistry
from ..catalog import LEGACY_CATALOG_SEEDS, CATALOG_SEEDS


def build_runtime_registry(runner_callable, catalog, definition):
    registry = OperatorExecutorRegistry()
    native = {(item["code"], item["version"]): item for item in (*LEGACY_CATALOG_SEEDS, *CATALOG_SEEDS)
              if item["runtime_requirements"].get("executor") in {"dataforge-native", "dataforge-adapter"}}
    for code in ("prompt-generator", "structured-knowledge-generator", "entity-extractor"):
        for version in (4, 5):
            native.setdefault((code, version), {})
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
            if (code, version) not in native:
                raise ValueError(f"算子 {code} v{version} 缺少冻结实现，禁止解析最新版本")
            registry.register(BuiltinOperatorExecutor(code, version, runner_callable)); continue
        spec = {**frozen, "code": code, "version": version}
        provider = (spec.get("runtime_requirements") or {}).get("provider", "dataforge")
        if provider == "dataflow":
            registry.register(DataFlowOperatorExecutor(spec))
        elif provider == "custom":
            from .custom import CustomOperatorExecutor
            registry.register(CustomOperatorExecutor(spec))
        elif (code, version) in native:
            registry.register(BuiltinOperatorExecutor(code, version, runner_callable))
        else:
            raise ValueError(f"原生算子 {code} v{version} 未注册")
    return registry
