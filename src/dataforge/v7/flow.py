"""Typed, controlled Flow DSL compiler used by DataForge V7."""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict, deque
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from .catalog import catalog_by_code, normalize_chunker_params, DEFAULT_QA_EXTRACTION_INSTRUCTIONS
from .operator_catalog import resolve_operator
from .llm_serving import LLMServingRegistry, get_llm_serving_registry


class FlowValidationError(ValueError):
    pass


def _reject_unknown_operators(definition, catalog):
    for node in definition.get("nodes", []):
        if node.get("kind") == "operator":
            code = str(node.get("ref") or "")
            if not code or code not in catalog:
                raise FlowValidationError(f"算子不存在：{code or '未声明'}")


class FlowEdgeValidationError(FlowValidationError):
    """Structured authoring-edge failure returned by every Flow compile entry."""

    def __init__(self, code: str, message: str, *, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}

    def payload(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "details": self.details}


@dataclass(frozen=True)
class ResolvedPortContract:
    raw_type: str
    kind: str
    knowledge_type: str | None = None
    graph_mode: str | None = None
    resolved_type: str = ""
    accepted_types: tuple[str, ...] = ()


def _edge(value: Any) -> dict[str, str]:
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return {"source": str(value[0]), "source_port": "output", "target": str(value[1]), "target_port": "input"}
    if isinstance(value, dict) and value.get("source") and value.get("target"):
        return {"source": str(value["source"]), "source_port": str(value.get("source_port", "output")),
                "target": str(value["target"]), "target_port": str(value.get("target_port", "input"))}
    raise FlowValidationError("Flow edge 必须包含 source 与 target")


def _type_matches(actual: str, expected: str) -> bool:
    if isinstance(expected, (list, tuple)):
        return any(_type_matches(actual, value) for value in expected)
    return actual == expected or expected.endswith(":*") and actual.startswith(expected[:-1])


def node_role(node: dict[str, Any]) -> str:
    """Return the stable authoring role while accepting legacy Flow DSL nodes."""
    explicit = str(node.get("node_role") or "")
    if explicit in {"flow_input", "operator", "knowledge_output"}:
        return explicit
    if node.get("kind") == "knowledge_sink":
        return "knowledge_output"
    if node.get("kind") == "operator" and node.get("ref") == "reviewed-source-chunk-input":
        return "flow_input"
    return "operator"


def _edge_details(edge: dict[str, str]) -> dict[str, str]:
    return {
        "source_node_id": edge["source"],
        "source_port": edge["source_port"],
        "target_node_id": edge["target"],
        "target_port": edge["target_port"],
    }


def _edge_error(code: str, message: str, edge: dict[str, str]) -> FlowEdgeValidationError:
    return FlowEdgeValidationError(code, message, details=_edge_details(edge))


def _candidate_parts(raw_type: str) -> tuple[str | None, str | None]:
    if not raw_type.startswith("candidate:"):
        return None, None
    parts = raw_type.split(":")
    knowledge_type = parts[1] if len(parts) > 1 and parts[1] != "*" else None
    graph_mode = parts[2] if knowledge_type == "graph" and len(parts) > 2 else None
    return knowledge_type, graph_mode


def _sink_context(node: dict[str, Any]) -> tuple[str, str | None] | None:
    if node.get("kind") != "knowledge_sink":
        return None
    knowledge_type = str(node.get("knowledge_type") or "")
    graph_mode = str(node.get("graph_mode") or "") or None
    output_key = str(node.get("output_key") or "")
    if not knowledge_type and output_key:
        knowledge_type, _, parsed_mode = output_key.partition(":")
        graph_mode = graph_mode or parsed_mode or None
    return (knowledge_type, graph_mode) if knowledge_type else None


def _reachable_sink_contexts(node_id: str, by_id: dict[str, dict[str, Any]],
                             outgoing: dict[str, list[str]], trail: frozenset[str] = frozenset()) -> set[tuple[str, str | None]]:
    if node_id in trail:
        return set()
    context = _sink_context(by_id.get(node_id, {}))
    result = {context} if context else set()
    for target in outgoing.get(node_id, []):
        result.update(_reachable_sink_contexts(target, by_id, outgoing, trail | {node_id}))
    return result


def resolve_port_contract(flow_context: dict[str, Any], node: dict[str, Any],
                          port: dict[str, Any]) -> ResolvedPortContract:
    """Resolve polymorphic candidate ports against normalized Flow context."""
    raw_type = str(port.get("artifact_type") or "")
    alternatives = tuple(port.get("accepted_types") or ())
    mapping = port.get("output_by_input") or {}
    if mapping:
        raw_type = mapping.get(flow_context.get("input_type"), raw_type)
        if raw_type == "text_record_set":
            alternatives = tuple(dict.fromkeys(mapping.values()))
    if alternatives:
        return ResolvedPortContract(raw_type=raw_type, kind=raw_type, resolved_type=raw_type,
                                    accepted_types=alternatives)
    if not raw_type.startswith("candidate:"):
        return ResolvedPortContract(raw_type=raw_type, kind=raw_type,
                                    resolved_type=raw_type)

    raw_knowledge_type, raw_graph_mode = _candidate_parts(raw_type)
    params = dict(node.get("params") or {})
    knowledge_type = raw_knowledge_type or str(params.get("knowledge_type") or "") or None
    graph_mode = raw_graph_mode or (str(params.get("graph_mode") or "") or None if knowledge_type == "graph" else None)
    contexts = set(flow_context.get("contexts") or set())
    if len(contexts) == 1:
        context_knowledge_type, context_graph_mode = next(iter(contexts))
        knowledge_type = knowledge_type or context_knowledge_type
        if knowledge_type == "graph":
            graph_mode = graph_mode or context_graph_mode

    resolved_type = raw_type
    if knowledge_type:
        resolved_type = f"candidate:{knowledge_type}"
        if knowledge_type == "graph" and graph_mode:
            resolved_type += f":{graph_mode}"
    return ResolvedPortContract(raw_type=raw_type, kind="candidate",
                                knowledge_type=knowledge_type, graph_mode=graph_mode,
                                resolved_type=resolved_type)


def validate_edge_compatibility(source_contract: ResolvedPortContract,
                                target_contract: ResolvedPortContract, *,
                                details: dict[str, str]) -> None:
    """Validate two already-resolved port contracts using stable error semantics."""
    if source_contract.accepted_types or target_contract.accepted_types:
        sources = source_contract.accepted_types or (source_contract.resolved_type,)
        targets = target_contract.accepted_types or (target_contract.resolved_type,)
        if any(_type_matches(source, targets) for source in sources):
            return
    if source_contract.kind != target_contract.kind:
        raise FlowEdgeValidationError(
            "PORT_TYPE_MISMATCH",
            f"端口数据类型不兼容：{source_contract.resolved_type} → {target_contract.resolved_type}",
            details=details,
        )
    if source_contract.kind != "candidate":
        if source_contract.resolved_type != target_contract.resolved_type:
            raise FlowEdgeValidationError(
                "PORT_TYPE_MISMATCH",
                f"端口数据类型不兼容：{source_contract.resolved_type} → {target_contract.resolved_type}",
                details=details,
            )
        return
    source_knowledge_type = source_contract.knowledge_type or target_contract.knowledge_type
    target_knowledge_type = target_contract.knowledge_type or source_contract.knowledge_type
    source_graph_mode = source_contract.graph_mode or target_contract.graph_mode
    target_graph_mode = target_contract.graph_mode or source_contract.graph_mode
    if not source_knowledge_type or not target_knowledge_type:
        raise FlowEdgeValidationError(
            "OPERATOR_CONTRACT_MISMATCH",
            "无法从当前 Flow 上下文解析 candidate:* 的实际知识类型",
            details=details,
        )
    if source_knowledge_type != target_knowledge_type:
        raise FlowEdgeValidationError(
            "KNOWLEDGE_TYPE_MISMATCH",
            f"知识类型不兼容：{source_contract.resolved_type} → {target_contract.resolved_type}",
            details=details,
        )
    if source_knowledge_type == "graph":
        if not source_graph_mode or not target_graph_mode:
            raise FlowEdgeValidationError(
                "OPERATOR_CONTRACT_MISMATCH",
                "无法从当前 Flow 上下文解析 graph_mode",
                details=details,
            )
        if source_graph_mode != target_graph_mode:
            raise FlowEdgeValidationError(
                "GRAPH_MODE_MISMATCH",
                f"图谱模式不兼容：{source_contract.resolved_type} → {target_contract.resolved_type}",
                details=details,
            )


def resolve_subflow(node: dict[str, Any], subflows: dict[str, dict[str, Any]]) -> dict[str, Any]:
    key = str(node.get("subflow_revision_id") or node.get("ref") or "")
    child = subflows.get(key)
    if not child or (child.get("_subgraph_code") and child["_subgraph_code"] != node.get("ref")):
        raise FlowValidationError(f"子流程修订不存在、编码不匹配或未发布：{key}")
    return child


def subflow_dependencies(definition: dict[str, Any], subflows: dict[str, dict[str, Any]],
                         prefix: tuple[str, ...] = (), stack: tuple[str, ...] = ()) -> list[dict[str, Any]]:
    result = []
    for node in definition.get("nodes", []):
        if node.get("kind") != "subflow":
            continue
        child = resolve_subflow(node, subflows)
        identity = str(node.get("subflow_revision_id") or node["ref"])
        if identity in stack:
            raise FlowValidationError("禁止递归子流程")
        path = (*prefix, str(node["id"]))
        result.append({"kind": "subflow_revision", "id": child.get("_subgraph_revision_id"),
                       "code": node["ref"], "revision": child.get("_subgraph_revision"), "instance_path": list(path)})
        result.extend(subflow_dependencies(child, subflows, path, (*stack, identity)))
    return result


def subflow_boundary_path(definition: dict[str, Any], boundary: str, subflows: dict[str, dict[str, Any]]) -> str:
    node_id = definition.get(boundary)
    node = next((value for value in definition.get("nodes", []) if value.get("id") == node_id), None)
    if not node:
        raise FlowValidationError(f"子流程缺少合法的 {boundary}")
    if node.get("kind") == "subflow":
        return f"{node_id}::{subflow_boundary_path(resolve_subflow(node, subflows), boundary, subflows)}"
    return str(node_id)


def _node_ports(node: dict[str, Any], *, direction: str,
                catalog: dict[str, dict[str, Any]], subflows: dict[str, dict[str, Any]],
                stack: tuple[str, ...] = ()) -> dict[str, dict[str, Any]]:
    if node.get("kind") == "knowledge_sink":
        if direction == "output":
            return {}
        knowledge_type = str(node.get("knowledge_type") or "")
        graph_mode = str(node.get("graph_mode") or "") or None
        output_key = str(node.get("output_key") or (f"graph:{graph_mode}" if knowledge_type == "graph" and graph_mode else knowledge_type))
        return {"input": {"artifact_type": f"candidate:{output_key}", "cardinality": "one",
                          "required": True, "binding": "edge"}}
    if node.get("kind") == "operator":
        item = resolve_operator(catalog, node) or {}
        key = "output_ports" if direction == "output" else "input_ports"
        fallback_key = "output" if direction == "output" else "input"
        fallback_type = item.get(fallback_key)
        if item.get(key):
            return dict(item[key])
        if fallback_type:
            return {fallback_key: {"artifact_type": fallback_type,
                                   "cardinality": "many" if direction == "output" else "one",
                                   "binding": "edge"}}
        return {}
    if node.get("kind") != "subflow":
        return {}
    code = str(node.get("subflow_revision_id") or node.get("ref") or "")
    if code in stack:
        raise FlowValidationError(f"子图不存在、未发布或递归引用：{code}")
    child = resolve_subflow(node, subflows)
    contract = child.get("_output_contract" if direction == "output" else "_input_contract")
    if contract:
        return deepcopy(contract)
    boundary_id = child.get("exit_node" if direction == "output" else "entry_node")
    boundary = next((item for item in child.get("nodes", []) if item.get("id") == boundary_id), None)
    if not boundary:
        raise FlowValidationError(f"子图 {code} 缺少 {'exit_node' if direction == 'output' else 'entry_node'}")
    return _node_ports(boundary, direction=direction, catalog=catalog, subflows=subflows,
                       stack=stack + (code,))


def _would_create_cycle(edges: list[dict[str, str]], candidate: dict[str, str]) -> bool:
    outgoing: dict[str, list[str]] = defaultdict(list)
    for edge in edges:
        outgoing[edge["source"]].append(edge["target"])
    queue = deque([candidate["target"]])
    seen: set[str] = set()
    while queue:
        current = queue.popleft()
        if current == candidate["source"]:
            return True
        if current in seen:
            continue
        seen.add(current)
        queue.extend(outgoing[current])
    return False


def input_type_for_node(node_id, definition, catalog, subflows, trail=frozenset()):
    """Resolve type-preserving processing ports from actual ancestors, not Sink intent."""
    if node_id in trail:
        return None
    by_id = {node["id"]: node for node in definition.get("nodes", [])}
    incoming = [edge for edge in map(_edge, definition.get("edges", [])) if edge["target"] == node_id]
    if len(incoming) != 1 or incoming[0]["source"] not in by_id:
        return None
    edge = incoming[0]; source = by_id[edge["source"]]
    port = _node_ports(source, direction="output", catalog=catalog, subflows=subflows).get(edge["source_port"], {})
    parent_type = input_type_for_node(source["id"], definition, catalog, subflows, trail | {node_id}) if port.get("output_by_input") else None
    outgoing = defaultdict(list)
    for link in map(_edge, definition.get("edges", [])):
        outgoing[link["source"]].append(link["target"])
    return resolve_port_contract({"input_type": parent_type, "contexts": _reachable_sink_contexts(source["id"], by_id, outgoing)}, source, port).resolved_type


def validate_flow_edges(definition: dict[str, Any], *, catalog: dict[str, dict[str, Any]],
                        subflows: dict[str, dict[str, Any]]) -> list[dict[str, str]]:
    """Validate the authoring graph before subflow expansion and return normalized edges."""
    schema_version = int(definition.get("schema_version", 2))
    if schema_version not in {2, 3}:
        raise FlowEdgeValidationError(
            "FLOW_DSL_VERSION_UNSUPPORTED",
            "仅支持 Flow DSL schema_version=2 或 3",
            details={},
        )
    nodes = [dict(node) for node in definition.get("nodes", []) if isinstance(node, dict)]
    by_id = {str(node.get("id") or ""): node for node in nodes if node.get("id")}
    normalized = [_edge(value) for value in definition.get("edges", [])]
    outgoing: dict[str, list[str]] = defaultdict(list)
    for edge in normalized:
        outgoing[edge["source"]].append(edge["target"])

    accepted: list[dict[str, str]] = []
    seen: set[tuple[str, str, str, str]] = set()
    incoming_counts: dict[tuple[str, str], int] = defaultdict(int)
    for edge in normalized:
        details = _edge_details(edge)
        source = by_id.get(edge["source"])
        target = by_id.get(edge["target"])
        if not source:
            raise _edge_error("SOURCE_NODE_NO_OUTPUT", "连线来源节点不存在", edge)
        if not target:
            raise _edge_error("TARGET_NODE_NO_INPUT", "连线目标节点不存在", edge)
        if source["id"] == target["id"]:
            raise _edge_error("EDGE_SELF_LOOP", "节点不能连接自身", edge)
        if source.get("kind") == "knowledge_sink" or node_role(source) == "knowledge_output":
            raise _edge_error("SINK_NODE_CANNOT_HAVE_OUTGOING", "Knowledge Sink 不允许作为上游节点", edge)
        if node_role(target) == "flow_input":
            raise _edge_error("INPUT_NODE_CANNOT_HAVE_INCOMING", "INPUT 节点不允许存在 Incoming Edge", edge)

        source_ports = _node_ports(source, direction="output", catalog=catalog, subflows=subflows)
        target_ports = _node_ports(target, direction="input", catalog=catalog, subflows=subflows)
        source_port = source_ports.get(edge["source_port"])
        target_port = target_ports.get(edge["target_port"])
        if not source_port and edge["source_port"] in _node_ports(source, direction="input", catalog=catalog, subflows=subflows):
            raise _edge_error("EDGE_DIRECTION_INVALID", "Edge 必须从 output port 指向 input port", edge)
        if not target_port and edge["target_port"] in _node_ports(target, direction="output", catalog=catalog, subflows=subflows):
            raise _edge_error("EDGE_DIRECTION_INVALID", "Edge 必须从 output port 指向 input port", edge)
        if not source_port:
            raise _edge_error("SOURCE_NODE_NO_OUTPUT", f"来源节点不存在输出端口 {edge['source_port']}", edge)
        if not target_port:
            raise _edge_error("TARGET_NODE_NO_INPUT", f"目标节点不存在输入端口 {edge['target_port']}", edge)
        if str(target_port.get("binding") or "edge") != "edge":
            raise _edge_error("INPUT_NODE_CANNOT_HAVE_INCOMING", "目标输入端口由系统或运行时绑定，不能连接 Edge", edge)

        identity = (edge["source"], edge["source_port"], edge["target"], edge["target_port"])
        if identity in seen:
            raise _edge_error("EDGE_DUPLICATED", "相同端口之间已经存在连线", edge)
        target_key = (edge["target"], edge["target_port"])
        if str(target_port.get("cardinality") or "one") != "many" and incoming_counts[target_key] >= 1:
            raise _edge_error("INPUT_PORT_ALREADY_CONNECTED", f"输入端口 {edge['target_port']} 已经有上游节点", edge)
        if _would_create_cycle(accepted, edge):
            raise _edge_error("EDGE_WOULD_CREATE_CYCLE", "该连接会形成循环依赖，Flow 必须是有向无环图", edge)

        source_context = {"contexts": _reachable_sink_contexts(edge["source"], by_id, outgoing),
                          "input_type": input_type_for_node(edge["source"], definition, catalog, subflows)}
        target_context = {"contexts": _reachable_sink_contexts(edge["target"], by_id, outgoing)}
        source_contract = resolve_port_contract(source_context, source, source_port)
        target_contract = resolve_port_contract(target_context, target, target_port)
        validate_edge_compatibility(source_contract, target_contract, details=details)
        seen.add(identity)
        incoming_counts[target_key] += 1
        accepted.append(edge)
    return normalized


def _contains_knowledge_library_binding(value: Any) -> bool:
    if isinstance(value, dict):
        return any(key == "knowledge_library_id" or _contains_knowledge_library_binding(item)
                   for key, item in value.items())
    if isinstance(value, list):
        return any(_contains_knowledge_library_binding(item) for item in value)
    return False


class FlowCompiler:
    def __init__(self, catalog: dict[str, dict[str, Any]] | None = None, subflows: dict[str, dict[str, Any]] | None = None, type_revisions: dict[str, dict[str, Any]] | None = None, *, allow_controlled: bool = False, llm_serving_registry: LLMServingRegistry | None = None):
        self.catalog = catalog or catalog_by_code()
        self.subflows = subflows or {}
        self.type_revisions = type_revisions or {}
        self.allow_controlled = allow_controlled
        self.llm_serving_registry = llm_serving_registry

    def _expand(self, definition: dict[str, Any], prefix: str = "", stack: tuple[str, ...] = ()) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
        nodes = [deepcopy(value) for value in definition.get("nodes", [])]
        edges = [_edge(value) for value in definition.get("edges", [])]
        ids = [str(value.get("id", "")) for value in nodes]
        if not ids or any(not value for value in ids) or len(ids) != len(set(ids)):
            raise FlowValidationError("Flow 节点 id 必须存在且唯一")
        result_nodes: list[dict[str, Any]] = []
        expanded_child_edges: list[dict[str, str]] = []
        replacement: dict[str, tuple[str, str]] = {}
        for node in nodes:
            node_id = str(node["id"])
            if node.get("kind") != "subflow":
                node["id"] = f"{prefix}{node_id}"
                if node.get("ref") == "GeneralFilter":
                    for rule in (node.get("params") or {}).get("rules", []):
                        if isinstance(rule.get("evaluation_node"), str) and rule["evaluation_node"].split("::", 1)[0] in ids:
                            rule["evaluation_node"] = f"{prefix}{rule['evaluation_node']}"
                node["node_role"] = node_role(node)
                node["origin_path"] = [part for part in f"{prefix}{node_id}".split("::") if part]
                if definition.get("_subgraph_code"):
                    node["source_subgraph"] = {"code": definition["_subgraph_code"], "revision": definition.get("_subgraph_revision"), "revision_id": definition.get("_subgraph_revision_id")}
                result_nodes.append(node)
                continue
            code = str(node.get("ref", ""))
            identity = str(node.get("subflow_revision_id") or code)
            if identity in stack:
                raise FlowValidationError("禁止递归子图")
            child = resolve_subflow(node, self.subflows)
            _reject_unknown_operators(child, self.catalog)
            validate_flow_edges(child, catalog=self.catalog, subflows=self.subflows)
            child_nodes, child_edges = self._expand(child, f"{prefix}{node_id}::", stack + (identity,))
            if code == "knowledge-chunk" and node.get("params"):
                for child_node in child_nodes:
                    if child_node.get("ref") == "semantic-chunker":
                        child_node["params"] = {**dict(child_node.get("params") or {}), **dict(node["params"])}
            entry, exit_node = child.get("entry_node"), child.get("exit_node")
            if not entry or not exit_node:
                raise FlowValidationError(f"子图 {code} 缺少 entry_node 或 exit_node")
            replacement[node_id] = (f"{prefix}{node_id}::{subflow_boundary_path(child, 'entry_node', self.subflows)}",
                                    f"{prefix}{node_id}::{subflow_boundary_path(child, 'exit_node', self.subflows)}")
            result_nodes.extend(child_nodes)
            # Recursive expansion has already applied the full namespace to
            # child edges; applying this frame's prefix again would corrupt
            # references such as ``parse::parser``.
            expanded_child_edges.extend(child_edges)
        result_edges: list[dict[str, str]] = []
        known = {node["id"] for node in result_nodes}
        for item in edges:
            source = replacement.get(item["source"], (f"{prefix}{item['source']}", ""))[1] or f"{prefix}{item['source']}"
            target = replacement.get(item["target"], ("", f"{prefix}{item['target']}"))[0] or f"{prefix}{item['target']}"
            if source not in known or target not in known:
                raise FlowValidationError(f"Flow edge 引用了不存在的节点：{item['source']} → {item['target']}")
            result_edges.append({**item, "source": source, "target": target})
        result_edges.extend(expanded_child_edges)
        return result_nodes, result_edges

    def compile(self, definition: dict[str, Any]) -> dict[str, Any]:
        _reject_unknown_operators(definition, self.catalog)
        if _contains_knowledge_library_binding(definition):
            raise FlowValidationError("Flow Definition 不允许绑定 KnowledgeLibrary")
        validate_flow_edges(definition, catalog=self.catalog, subflows=self.subflows)
        schema_version = int(definition.get("schema_version", 2))
        purpose = str(definition.get("purpose") or "knowledge")
        if purpose not in {"knowledge", "source_preparation"}:
            raise FlowValidationError("Flow purpose 必须是 knowledge 或 source_preparation")
        nodes, edges = self._expand(definition)
        # Recheck actual boundary ports, not just a subflow's declared interface.
        validate_flow_edges({**definition, "nodes": nodes, "edges": edges},
                            catalog=self.catalog, subflows=self.subflows)
        by_id = {node["id"]: node for node in nodes}
        incoming: dict[str, list[str]] = defaultdict(list)
        outgoing: dict[str, list[str]] = defaultdict(list)
        for edge in edges:
            source_node, target_node = by_id[edge["source"]], by_id[edge["target"]]
            if source_node.get("kind") != "operator" or node_role(source_node) == "knowledge_output":
                raise FlowValidationError(f"节点 {edge['source']} 不能作为边的起点")
            source_item = resolve_operator(self.catalog, source_node) or {}
            source_ports = source_item.get("output_ports") or {"output": {"artifact_type": source_item.get("output")}}
            if edge["source_port"] not in source_ports:
                raise FlowValidationError(f"节点 {edge['source']} 不存在输出端口 {edge['source_port']}")
            if target_node.get("kind") == "knowledge_sink":
                target_ports = {"input": {"artifact_type": "candidate:*", "cardinality": "one",
                                            "required": True, "binding": "edge"}}
            elif target_node.get("kind") == "operator":
                target_item = resolve_operator(self.catalog, target_node) or {}
                target_ports = target_item.get("input_ports") or {"input": {"artifact_type": target_item.get("input"), "cardinality": "one"}}
            else:
                raise FlowValidationError(f"展开后仍存在不支持的节点类型：{target_node.get('kind')}")
            if edge["target_port"] not in target_ports:
                raise FlowValidationError(f"节点 {edge['target']} 不存在输入端口 {edge['target_port']}")
            if str(target_ports[edge["target_port"]].get("binding") or "edge") != "edge":
                raise FlowValidationError(f"节点 {edge['target']} 的输入端口 {edge['target_port']} 由运行时绑定，不能连接 Edge")
            if node_role(target_node) == "flow_input":
                raise FlowValidationError(f"Flow Input {edge['target']} 不能连接 Incoming Edge")
            incoming[edge["target"]].append(edge["source"]); outgoing[edge["source"]].append(edge["target"])
        isolated = [node_id for node_id in by_id if not incoming[node_id] and not outgoing[node_id]]
        if isolated and len(by_id) > 1:
            raise FlowValidationError("Flow 不允许孤立节点：" + ", ".join(sorted(isolated)))
        non_sink_terminals = [node_id for node_id, node in by_id.items()
                              if not outgoing[node_id] and node.get("kind") != "knowledge_sink"]
        if purpose == "knowledge" and non_sink_terminals:
            raise FlowValidationError("Flow 的所有终点必须是 Knowledge Sink：" + ", ".join(sorted(non_sink_terminals)))
        queue = deque(sorted(node_id for node_id in by_id if not incoming[node_id]))
        ordered: list[str] = []
        mutable_incoming = {node_id: set(values) for node_id, values in incoming.items()}
        while queue:
            current = queue.popleft(); ordered.append(current)
            for target in sorted(outgoing[current]):
                mutable_incoming[target].remove(current)
                if not mutable_incoming[target]: queue.append(target)
        if len(ordered) != len(by_id):
            raise FlowValidationError("Flow 必须是有向无环图")
        outputs: dict[str, str] = {}
        dependencies: list[dict[str, Any]] = subflow_dependencies(definition, self.subflows)
        sink_types: dict[str, str] = {}
        for node_id in ordered:
            node = by_id[node_id]; kind = node.get("kind")
            if kind == "knowledge_sink":
                node["node_role"] = "knowledge_output"
                knowledge_type = str(node.get("knowledge_type", ""))
                if knowledge_type not in self.type_revisions:
                    raise FlowValidationError(f"Knowledge Sink 引用的知识类型未发布：{knowledge_type}")
                output_key = str(node.get("output_key") or (f"graph:{node.get('graph_mode')}" if knowledge_type == "graph" and node.get("graph_mode") else knowledge_type))
                expected_key = (f"graph:{node.get('graph_mode')}" if knowledge_type == "graph" else knowledge_type)
                if output_key != expected_key or knowledge_type == "graph" and node.get("graph_mode") not in {"triple", "semantic"}:
                    raise FlowValidationError(f"Knowledge Sink {node_id} 输出键与知识类型或图谱模式不一致")
                required = f"candidate:{output_key}"
                source_types = [outputs[source] for source in incoming[node_id]]
                if source_types != [required]:
                    raise FlowValidationError(f"Knowledge Sink {node_id} 需要 {required}")
                if outgoing[node_id]:
                    raise FlowValidationError(f"Knowledge Sink {node_id} 必须是终点")
                if output_key in sink_types.values():
                    raise FlowValidationError(f"模板输出 {output_key} 必须且只能对应一个 Knowledge Sink")
                sink_types[node_id] = output_key; outputs[node_id] = f"knowledge_item:{output_key}"
                dependencies.append({"kind": "knowledge_type", "code": knowledge_type, "revision": self.type_revisions[knowledge_type]})
                continue
            if kind != "operator":
                raise FlowValidationError(f"不支持的节点类型：{kind}")
            code = str(node.get("ref", "")); item = resolve_operator(self.catalog, node)
            if not item or item.get("exposure") in {"disabled", "internal"} or not item.get("enabled", True):
                raise FlowValidationError(f"算子不在 Flow allowlist：{code}")
            if purpose == "knowledge" and item.get("surfaces") and "advanced-canvas" not in item["surfaces"] and "standard-template" not in item["surfaces"]:
                raise FlowValidationError(f"算子不允许用于知识流程：{code}")
            if item.get("exposure") == "controlled" and not (item.get("approved") or self.allow_controlled):
                raise FlowValidationError(f"算子尚未获批进入当前 Flow：{code}")
            node["node_role"] = node_role(node)
            source_types = [outputs[source] for source in incoming[node_id]]
            port_spec = (item.get("input_ports") or {}).get("input") or {"artifact_type": item["input"], "cardinality": "one", "required": True}
            expected = port_spec.get("accepted_types") or port_spec.get("artifact_type", item["input"])
            cardinality = port_spec.get("cardinality", "one")
            binding = str(port_spec.get("binding") or (
                "runtime_input" if node_role(node) == "flow_input"
                else "system_injected" if expected == "source_file" else "edge"
            ))
            required = bool(port_spec.get("required", True))
            if binding in {"runtime_input", "system_injected"}:
                if source_types:
                    raise FlowValidationError(f"{code} 只能作为 Flow 根节点")
            elif required and (not source_types or cardinality == "one" and len(source_types) != 1) or source_types and any(not _type_matches(source_type, expected) for source_type in source_types):
                raise FlowValidationError(f"节点 {node_id} 输入 Artifact Type 不兼容，需要 {expected}")
            params = node.get("params")
            if params is None:
                params = {}
            if not isinstance(params, dict):
                raise FlowValidationError(f"节点 {node_id} 参数必须是对象")
            if code == "GeneralFilter":
                from .governance_catalog import RULE_SCHEMA, SCORES
                from jsonschema import Draft202012Validator
                errors = list(Draft202012Validator(RULE_SCHEMA).iter_errors(params.get("rules")))
                if errors:
                    raise FlowValidationError(f"节点 {node_id} 保留条件非法：{errors[0].message}")
                ancestors, pending = set(), list(incoming[node_id])
                while pending:
                    parent = pending.pop()
                    if parent in ancestors:
                        continue
                    ancestors.add(parent); pending.extend(incoming[parent])
                for rule in params["rules"]:
                    if rule["field"] in SCORES and (rule.get("evaluation_node") not in ancestors or by_id[rule["evaluation_node"]].get("ref") != "Text2QASampleEvaluator"):
                        raise FlowValidationError(f"节点 {node_id} 必须选择上游QA评估节点")
            if code == "schema-validator":
                if not source_types or any(value not in {"candidate:graph:triple", "candidate:graph:semantic"} for value in source_types):
                    raise FlowValidationError("schema-validator 仅支持 Graph Candidate")
                params.setdefault("knowledge_type", "graph")
                params.setdefault("graph_mode", source_types[0].split(":")[2])
            if code == "qa-extractor" or (code == "Text2QAGenerator" and item.get("version") in {6, 7}):
                instructions = params.get("extraction_instructions", "")
                if not isinstance(instructions, str):
                    raise FlowValidationError("QA 提取要求必须是字符串")
                params["extraction_instructions"] = instructions.strip() or DEFAULT_QA_EXTRACTION_INSTRUCTIONS
            if code == "document-parser" and params:
                raise FlowValidationError(
                    f"节点 {node_id} 的 Document Parser 当前不接受参数；"
                    "PDF 固定使用 MinerU backend=pipeline、parse_method=auto"
                )
            if code == "semantic-chunker":
                try:
                    params = normalize_chunker_params(params)
                except ValueError as exc:
                    raise FlowValidationError(f"节点 {node_id} 参数非法：{exc}") from exc
            uses_llm = bool((item.get("runtime_requirements") or {}).get("uses_llm"))
            if "llm_serving" in params and not uses_llm:
                raise FlowValidationError(f"节点 {node_id} 不是 LLM 算子，不能配置 llm_serving")
            if uses_llm:
                registry = self.llm_serving_registry or get_llm_serving_registry()
                serving_id = str(params.get("llm_serving") or registry.default_serving).strip()
                try:
                    registry.require(serving_id)
                except ValueError as exc:
                    raise FlowValidationError(f"节点 {node_id} 引用了未配置的 LLM Serving：{serving_id}") from exc
                params["llm_serving"] = serving_id
                dependencies.append({"kind": "llm_serving", "id": serving_id})
            node["params"] = params
            node["operator_version"] = item.get("version", 1)
            node["adapter_code"] = item["adapter_code"]
            node["operator_spec"] = deepcopy({key: item.get(key) for key in (
                "code", "version", "name", "display_name_zh", "description", "source", "catalog_group", "category",
                "adapter_code", "runtime_requirements",
                "input_ports", "output_ports", "parameter_schema")})
            output_spec = (item.get("output_ports") or {}).get("output") or {"artifact_type": item["output"]}
            output = str(output_spec.get("artifact_type", item["output"]))
            if output_spec.get("output_by_input"):
                output = output_spec["output_by_input"].get(source_types[0] if len(source_types) == 1 else "")
                if not output:
                    raise FlowValidationError(f"节点 {node_id} 无法解析正文处理的实际输出类型")
            if "derived_text_set" in source_types and str(params.get("knowledge_type", "text")) not in {"text", "qa"}:
                raise FlowValidationError("派生正文仅支持Text/QA生成")
            node["resolved_input_type"] = source_types[0] if len(source_types) == 1 else None
            knowledge_type = str(params.get("knowledge_type", ""))
            if knowledge_type and item.get("knowledge_types") and knowledge_type not in item["knowledge_types"] and "*" not in item["knowledge_types"]:
                raise FlowValidationError(f"算子 {code} 不支持知识类型 {knowledge_type}")
            if knowledge_type == "graph" and params.get("graph_mode") and item.get("graph_modes") and params["graph_mode"] not in item["graph_modes"]:
                raise FlowValidationError(f"算子 {code} 不支持图谱模式 {params['graph_mode']}")
            if output == "candidate:*":
                if knowledge_type:
                    if knowledge_type not in self.type_revisions:
                        raise FlowValidationError(f"节点 {node_id} 必须指定已发布 knowledge_type")
                    graph_mode = str(params.get("graph_mode") or "")
                    output = f"candidate:{knowledge_type}:{graph_mode}" if knowledge_type == "graph" and graph_mode else f"candidate:{knowledge_type}"
                elif source_types and len(set(source_types)) == 1 and source_types[0].startswith("candidate:"):
                    output = source_types[0]
                else:
                    raise FlowValidationError(f"节点 {node_id} 无法从输入推导候选知识类型")
            if output == "candidate:graph":
                contract = resolve_port_contract(
                    {"contexts": _reachable_sink_contexts(node_id, by_id, outgoing)}, node, output_spec,
                )
                if contract.graph_mode not in {"triple", "semantic"}:
                    raise FlowValidationError(f"节点 {node_id} 无法解析 Graph Candidate 模式")
                params.setdefault("knowledge_type", "graph")
                params.setdefault("graph_mode", contract.graph_mode)
                output = contract.resolved_type
            outputs[node_id] = output
            node["resolved_output_type"] = output
            dependencies.append({"kind": "operator", "code": code, "version": item.get("version", 1), "adapter": item["adapter_code"], "runtime_requirements": deepcopy(item.get("runtime_requirements") or {})})
        root_refs = {str(by_id[node_id].get("ref") or "") for node_id in ordered if not incoming[node_id]}
        node_refs = {str(node.get("ref") or "") for node in by_id.values() if node.get("kind") == "operator"}
        if purpose == "knowledge":
            if not sink_types:
                raise FlowValidationError("Flow 至少需要一个 Knowledge Sink")
            if root_refs != {"reviewed-source-chunk-input"}:
                raise FlowValidationError("知识流程必须且只能从 Reviewed SourceChunk Input 开始")
            if {"document-parser", "source-chunk-builder"} & node_refs:
                raise FlowValidationError("知识流程不得重新解析或构建 SourceChunk")
        else:
            if sink_types:
                raise FlowValidationError("Source Preparation 不允许 Knowledge Sink")
            if root_refs != {"document-parser"}:
                raise FlowValidationError("Source Preparation 必须从 Document Parser 开始")
            if len(non_sink_terminals) != 1 or outputs.get(non_sink_terminals[0]) != "source_chunk_set":
                raise FlowValidationError("Source Preparation 必须且只能以 SourceChunk 结束")
        compiled = {"schema_version": 3, "purpose": purpose, "nodes": [by_id[node_id] for node_id in ordered], "edges": edges, "sink_types": sink_types}
        compiled["subflow_revisions"] = [value for value in dependencies if value["kind"] == "subflow_revision"]
        if definition.get("graph_config") is not None:
            compiled["graph_config"] = deepcopy(definition["graph_config"])
        checksum = hashlib.sha256(json.dumps(compiled, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        return {"compiled_definition": compiled, "dependencies": dependencies, "checksum": checksum}
