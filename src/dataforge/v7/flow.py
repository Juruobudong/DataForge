"""Typed, controlled Flow DSL compiler used by DataForge V7."""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict, deque
from copy import deepcopy
from typing import Any

from .catalog import catalog_by_code
from .llm_serving import LLMServingRegistry, get_llm_serving_registry


class FlowValidationError(ValueError):
    pass


def _edge(value: Any) -> dict[str, str]:
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return {"source": str(value[0]), "source_port": "output", "target": str(value[1]), "target_port": "input"}
    if isinstance(value, dict) and value.get("source") and value.get("target"):
        return {"source": str(value["source"]), "source_port": str(value.get("source_port", "output")),
                "target": str(value["target"]), "target_port": str(value.get("target_port", "input"))}
    raise FlowValidationError("Flow edge 必须包含 source 与 target")


def _type_matches(actual: str, expected: str) -> bool:
    return actual == expected or expected.endswith(":*") and actual.startswith(expected[:-1])


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
                node["origin_path"] = [part for part in f"{prefix}{node_id}".split("::") if part]
                if definition.get("_subgraph_code"):
                    node["source_subgraph"] = {"code": definition["_subgraph_code"], "revision": definition.get("_subgraph_revision")}
                result_nodes.append(node)
                continue
            code = str(node.get("ref", ""))
            if code not in self.subflows:
                raise FlowValidationError(f"子图不存在或未发布：{code}")
            if code in stack:
                raise FlowValidationError("禁止递归子图")
            child = self.subflows[code]
            child_nodes, child_edges = self._expand(child, f"{prefix}{node_id}::", stack + (code,))
            entry, exit_node = child.get("entry_node"), child.get("exit_node")
            if not entry or not exit_node:
                raise FlowValidationError(f"子图 {code} 缺少 entry_node 或 exit_node")
            replacement[node_id] = (f"{prefix}{node_id}::{entry}", f"{prefix}{node_id}::{exit_node}")
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
        schema_version = int(definition.get("schema_version", 2))
        if schema_version not in {2, 3}:
            raise FlowValidationError("仅支持 Flow DSL schema_version=2 或 3")
        purpose = str(definition.get("purpose") or "knowledge")
        if purpose not in {"knowledge", "source_preparation"}:
            raise FlowValidationError("Flow purpose 必须是 knowledge 或 source_preparation")
        nodes, edges = self._expand(definition)
        by_id = {node["id"]: node for node in nodes}
        incoming: dict[str, list[str]] = defaultdict(list)
        outgoing: dict[str, list[str]] = defaultdict(list)
        for edge in edges:
            source_node, target_node = by_id[edge["source"]], by_id[edge["target"]]
            if source_node.get("kind") != "operator":
                raise FlowValidationError(f"节点 {edge['source']} 不能作为边的起点")
            source_item = self.catalog.get(str(source_node.get("ref", ""))) or {}
            source_ports = source_item.get("output_ports") or {"output": {"artifact_type": source_item.get("output")}}
            if edge["source_port"] not in source_ports:
                raise FlowValidationError(f"节点 {edge['source']} 不存在输出端口 {edge['source_port']}")
            if target_node.get("kind") == "knowledge_sink":
                target_ports = {"input": {"artifact_type": "candidate:*", "cardinality": "one"}}
            elif target_node.get("kind") == "operator":
                target_item = self.catalog.get(str(target_node.get("ref", ""))) or {}
                target_ports = target_item.get("input_ports") or {"input": {"artifact_type": target_item.get("input"), "cardinality": "one"}}
            else:
                raise FlowValidationError(f"展开后仍存在不支持的节点类型：{target_node.get('kind')}")
            if edge["target_port"] not in target_ports:
                raise FlowValidationError(f"节点 {edge['target']} 不存在输入端口 {edge['target_port']}")
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
        dependencies: list[dict[str, Any]] = []
        sink_types: dict[str, str] = {}
        for node_id in ordered:
            node = by_id[node_id]; kind = node.get("kind")
            if kind == "knowledge_sink":
                knowledge_type = str(node.get("knowledge_type", ""))
                if knowledge_type not in self.type_revisions:
                    raise FlowValidationError(f"Knowledge Sink 引用的知识类型未发布：{knowledge_type}")
                output_key = str(node.get("output_key") or (f"graph:{node.get('graph_mode')}" if knowledge_type == "graph" and node.get("graph_mode") else knowledge_type))
                required = f"candidate:{output_key}"
                source_types = [outputs[source] for source in incoming[node_id]]
                if len(source_types) != 1 or not (_type_matches(source_types[0], required) or knowledge_type == "graph" and _type_matches(source_types[0], "candidate:graph")):
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
            code = str(node.get("ref", "")); item = self.catalog.get(code)
            if not item or item.get("exposure") in {"disabled", "internal"} or not item.get("enabled", True):
                raise FlowValidationError(f"算子不在 Flow allowlist：{code}")
            if item.get("exposure") == "controlled" and not self.allow_controlled:
                raise FlowValidationError(f"算子尚未获批进入当前 Flow：{code}")
            source_types = [outputs[source] for source in incoming[node_id]]
            port_spec = (item.get("input_ports") or {}).get("input") or {"artifact_type": item["input"], "cardinality": "one"}
            expected = port_spec.get("artifact_type", item["input"])
            cardinality = port_spec.get("cardinality", "one")
            if expected in {"source_file", "approved_source_chunks"}:
                if source_types:
                    raise FlowValidationError(f"{code} 只能作为 Flow 根节点")
            elif not source_types or cardinality == "one" and len(source_types) != 1 or any(not _type_matches(source_type, expected) for source_type in source_types):
                raise FlowValidationError(f"节点 {node_id} 输入 Artifact Type 不兼容，需要 {expected}")
            params = node.get("params")
            if params is None:
                params = {}
            if not isinstance(params, dict):
                raise FlowValidationError(f"节点 {node_id} 参数必须是对象")
            if code == "document-parser" and params:
                raise FlowValidationError(
                    f"节点 {node_id} 的 Document Parser 当前不接受参数；"
                    "PDF 固定使用 MinerU backend=pipeline、parse_method=auto"
                )
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
            output_spec = (item.get("output_ports") or {}).get("output") or {"artifact_type": item["output"]}
            output = str(output_spec.get("artifact_type", item["output"]))
            knowledge_type = str(params.get("knowledge_type", ""))
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
            outputs[node_id] = output
            dependencies.append({"kind": "operator", "code": code, "version": item.get("version", 1), "adapter": item["adapter_code"]})
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
        checksum = hashlib.sha256(json.dumps(compiled, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        return {"compiled_definition": compiled, "dependencies": dependencies, "checksum": checksum}
