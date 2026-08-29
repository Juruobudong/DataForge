"""Versioned, curated catalog shared by authoring and runtime projections."""
from __future__ import annotations

from copy import deepcopy
from typing import Any
from uuid import uuid4
from sqlalchemy import select

from .catalog import CATALOG_SEEDS, operator_surfaces
from .models import OperatorDefinition, OperatorVersion, utc_now


class VersionedCatalog(dict):
    def __init__(self, entries):
        super().__init__()
        self.versions = {}
        for item in entries:
            self.versions[(item["code"], item["version"])] = item
            if item["code"] not in self or self[item["code"]]["version"] < item["version"]:
                self[item["code"]] = item

    def resolve(self, node):
        code = str(node.get("ref") or "")
        version = node.get("operator_version")
        if version is None:
            return self.get(code)
        if not isinstance(version, int) or isinstance(version, bool):
            raise ValueError("OperatorVersion 必须是整数")
        item = self.versions.get((code, version))
        if item is None:
            raise ValueError(f"Operator {code} v{version} 未发布，禁止回退到最新版本")
        return item


def resolve_operator(catalog, node):
    if hasattr(catalog, "resolve"):
        return catalog.resolve(node)
    item = catalog.get(str(node.get("ref") or ""))
    if item and node.get("operator_version") not in (None, item.get("version", 1)):
        raise ValueError(f"Operator {node.get('ref')} v{node.get('operator_version')} 未注册")
    return item


def seed_catalog(session):
    # A clean environment seeds only the current published identity/version pairs.
    entries = {(item["code"], item["version"]): item for item in CATALOG_SEEDS}
    for item in entries.values():
        definition = session.scalar(select(OperatorDefinition).where(OperatorDefinition.code == item["code"]))
        if definition is None:
            definition = OperatorDefinition(id=f"op_{item['code'].replace('-', '_')}", code=item["code"],
                                            source=item["source"], catalog_group=item["catalog_group"],
                                            enabled=item["exposure"] != "disabled")
            session.add(definition)
        for key in ("name", "display_name_zh", "description", "category", "subcategory", "summary", "scenarios",
                    "knowledge_types", "recommended_predecessors", "recommended_successors", "lifecycle_status", "risk_level"):
            setattr(definition, key, deepcopy(item[key]))
        definition.exposure = "public" if item["exposure"] == "canvas" else item["exposure"]
        definition.source, definition.catalog_group = item["source"], item["catalog_group"]
        definition.surfaces = item.get("surfaces") or operator_surfaces(item["code"], item["input"], item["exposure"])
        session.flush()
        version = session.scalar(select(OperatorVersion).where(OperatorVersion.operator_definition_id == definition.id,
                                                              OperatorVersion.version_no == item["version"]))
        if version is None:
            runtime = deepcopy(item["runtime_requirements"])
            runtime.update(knowledge_types=item["knowledge_types"], surfaces=definition.surfaces,
                           approved=item["exposure"] not in {"disabled", "internal"})
            version = OperatorVersion(id=f"oprev_{uuid4().hex}", operator_definition_id=definition.id,
                                      version_no=item["version"], status="published", published_at=utc_now(),
                                      runtime_requirements=runtime)
            for key in ("adapter_code", "input_ports", "output_ports", "input_example", "output_example", "parameter_schema", "parameter_docs"):
                setattr(version, key, deepcopy(item[key]))
            session.add(version)
        definition.latest_version = max(definition.latest_version or 0, item["version"])
    session.flush()


def version_payload(definition, version):
    runtime = deepcopy(version.runtime_requirements or {})
    return {
        **{key: deepcopy(getattr(definition, key)) for key in (
            "id", "code", "name", "display_name_zh", "summary", "description", "source", "catalog_group", "category", "subcategory",
            "exposure", "risk_level", "enabled", "scenarios", "recommended_predecessors", "recommended_successors")},
        "knowledge_types": runtime.get("knowledge_types", definition.knowledge_types),
        "graph_modes": runtime.get("graph_modes", []),
        "surfaces": list(definition.surfaces or runtime.get("surfaces") or []),
        "status": definition.lifecycle_status, "version_status": version.status,
        "version": version.version_no, "version_id": version.id,
        "adapter_code": version.adapter_code, "runtime_requirements": runtime,
        "executor": runtime.get("executor", "dataforge-native"),
        "uses_llm": bool(runtime.get("uses_llm")), "approved": runtime.get("approved", definition.exposure != "controlled"),
        "node_role": "flow_input" if definition.code == "reviewed-source-chunk-input" else "operator",
        "input": (version.input_ports.get("input") or {}).get("artifact_type", ""),
        "output": (version.output_ports.get("output") or {}).get("artifact_type", ""),
        **{key: deepcopy(getattr(version, key)) for key in (
            "input_ports", "output_ports", "input_example", "output_example", "parameter_schema", "parameter_docs")},
    }


def load_catalog(session):
    rows = session.execute(select(OperatorDefinition, OperatorVersion).join(
        OperatorVersion, OperatorVersion.operator_definition_id == OperatorDefinition.id
    ).where(OperatorVersion.status == "published")).all()
    return VersionedCatalog(version_payload(definition, version) for definition, version in rows)


def technical_projection(definition, catalog):
    """Project actual edges, never static stage.operator_refs. No runtime calls."""
    nodes, issues = [], []
    for node in definition.get("nodes", []):
        params = node.get("params") or {}
        if node.get("kind") == "knowledge_sink":
            item = {"code": "knowledge-sink", "name": "Knowledge Sink", "display_name_zh": "知识输出",
                    "version": 1, "source": "dataforge", "catalog_group": "dataforge", "category": "quality-processing",
                    "description": "正式知识提交入口", "uses_llm": False,
                    "input_ports": {"input": {"artifact_type": f"candidate:{node.get('output_key')}"}}, "output_ports": {}, "parameter_schema": {}}
        else:
            try:
                item = resolve_operator(catalog, node) or {}
            except ValueError as exc:
                item = {}; issues.append({"node_id": node["id"], "message": str(exc)})
        frozen = node.get("operator_spec") or {}
        runtime = frozen.get("runtime_requirements") or item.get("runtime_requirements") or {}
        schema = frozen.get("parameter_schema") or item.get("parameter_schema") or {}
        nodes.append({
            "node_id": node["id"], "kind": node.get("kind"), "code": node.get("ref", "knowledge-sink"),
            "version": node.get("operator_version", item.get("version")),
            "stage_id": node.get("stage_id"), "stage_label": node.get("stage_label"),
            "name": frozen.get("name", item.get("name", node.get("ref", ""))),
            "display_name_zh": frozen.get("display_name_zh", item.get("display_name_zh", node.get("ref", ""))),
            "description": frozen.get("description", item.get("description", "")),
            "source": frozen.get("source", item.get("source", "dataforge")),
            "catalog_group": frozen.get("catalog_group", item.get("catalog_group", "dataforge")),
            "category": frozen.get("category", item.get("category", "content-processing")),
            "executor": runtime.get("executor", "dataforge-native"),
            "uses_llm": runtime.get("uses_llm", item.get("uses_llm", False)),
            "input_ports": frozen.get("input_ports", item.get("input_ports", {})),
            "output_ports": frozen.get("output_ports", item.get("output_ports", {})),
            "parameters": {key: deepcopy(params.get(key, value.get("default"))) for key, value in schema.get("properties", {}).items()
                           if not any(secret in key.lower() for secret in ("password", "secret", "token", "api_key"))},
            "output_key": node.get("output_key") or params.get("knowledge_type"), "locked": True,
        })
    return {"resolved_operators": nodes, "edges": deepcopy(definition.get("edges", [])), "issues": issues}
