"""Graph indexes shared by strict compilation and incremental candidate checks."""
from collections import defaultdict


class FlowEdgeValidationContext:
    def __init__(self, definition, *, catalog, subflows):
        from .flow import FlowEdgeValidationError, _edge
        version = definition.get("schema_version", 2)
        if type(version) is not int or version not in {2, 3}:
            raise FlowEdgeValidationError("FLOW_DSL_VERSION_UNSUPPORTED", "仅支持 Flow DSL schema_version=2 或 3")
        nodes = definition.get("nodes", [])
        if any(not isinstance(n, dict) or not isinstance(n.get("id"), str) or not n["id"] for n in nodes):
            raise FlowEdgeValidationError("NODE_ID_INVALID", "Flow 节点 id 必须存在且唯一")
        self.by_id = {n["id"]: n for n in nodes}
        if len(self.by_id) != len(nodes):
            raise FlowEdgeValidationError("NODE_ID_INVALID", "Flow 节点 id 必须存在且唯一")
        self.catalog, self.subflows = catalog, subflows
        self.edges = [_edge(value) for value in definition.get("edges", [])]
        self.outgoing, self.incoming = defaultdict(list), defaultdict(list)
        for edge in self.edges:
            self.outgoing[edge["source"]].append(edge["target"])
            self.incoming[edge["target"]].append(edge)
        self.port_cache, self.context_cache, self.input_cache = {}, {}, {}
        self._diagnostics = None

    def ports(self, node, direction):
        from .flow import _node_ports
        key = (node["id"], direction)
        if key not in self.port_cache:
            self.port_cache[key] = _node_ports(node, direction=direction, catalog=self.catalog, subflows=self.subflows)
        return self.port_cache[key]

    def contexts(self, node_id):
        from .flow import _sink_context
        if node_id not in self.context_cache:
            result, seen, pending = set(), set(), [node_id]
            while pending:
                current = pending.pop()
                if current in seen:
                    continue
                seen.add(current)
                context = _sink_context(self.by_id.get(current, {}))
                if context:
                    result.add(context)
                pending.extend(self.outgoing[current])
            self.context_cache[node_id] = result
        return self.context_cache[node_id]

    def input_type(self, node_id, trail=frozenset()):
        from .flow import resolve_port_contract
        if node_id in trail:
            return None
        if node_id in self.input_cache:
            return self.input_cache[node_id]
        incoming = self.incoming[node_id]
        if len(incoming) != 1 or incoming[0]["source"] not in self.by_id:
            return None
        edge = incoming[0]
        source = self.by_id[edge["source"]]
        port = self.ports(source, "output").get(edge["source_port"], {})
        parent = self.input_type(source["id"], trail | {node_id}) if port.get("output_by_input") else None
        result = resolve_port_contract({"contexts": self.contexts(source["id"]), "input_type": parent}, source, port).resolved_type
        self.input_cache[node_id] = result
        return result

    def validate(self, index):
        from .flow import (_edge_error, _edge_details, node_role, _would_create_cycle,
                           resolve_port_contract, validate_edge_compatibility)
        edge = self.edges[index]
        source, target = self.by_id.get(edge["source"]), self.by_id.get(edge["target"])
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
        source_port = self.ports(source, "output").get(edge["source_port"])
        target_port = self.ports(target, "input").get(edge["target_port"])
        if (not source_port and edge["source_port"] in self.ports(source, "input")
                or not target_port and edge["target_port"] in self.ports(target, "output")):
            raise _edge_error("EDGE_DIRECTION_INVALID", "Edge 必须从 output port 指向 input port", edge)
        if not source_port:
            raise _edge_error("SOURCE_NODE_NO_OUTPUT", f"来源节点不存在输出端口 {edge['source_port']}", edge)
        if not target_port:
            raise _edge_error("TARGET_NODE_NO_INPUT", f"目标节点不存在输入端口 {edge['target_port']}", edge)
        if target_port.get("binding", "edge") != "edge":
            raise _edge_error("INPUT_NODE_CANNOT_HAVE_INCOMING", "目标输入端口由系统或运行时绑定，不能连接 Edge", edge)
        others = self.edges[:index] + self.edges[index + 1:]
        if edge in others:
            raise _edge_error("EDGE_DUPLICATED", "相同端口之间已经存在连线", edge)
        if target_port.get("cardinality", "one") != "many" and any(
                v["target"] == edge["target"] and v["target_port"] == edge["target_port"] for v in others):
            raise _edge_error("INPUT_PORT_ALREADY_CONNECTED", f"输入端口 {edge['target_port']} 已经有上游节点", edge)
        if _would_create_cycle(others, edge):
            raise _edge_error("EDGE_WOULD_CREATE_CYCLE", "该连接会形成循环依赖，Flow 必须是有向无环图", edge)
        source_contract = resolve_port_contract({"contexts": self.contexts(source["id"]),
            "input_type": self.input_type(source["id"])}, source, source_port)
        target_contract = resolve_port_contract({"contexts": self.contexts(target["id"])}, target, target_port)
        validate_edge_compatibility(source_contract, target_contract, details=_edge_details(edge))

    def issue(self, index):
        from .flow import FlowValidationError, FlowEdgeValidationError, _edge_details
        try:
            self.validate(index)
        except FlowEdgeValidationError as exc:
            return exc.payload()
        except (FlowValidationError, ValueError, TypeError) as exc:
            return {"code": "OPERATOR_CONTRACT_MISMATCH", "message": str(exc), "details": _edge_details(self.edges[index])}
        return None

    @property
    def diagnostics(self):
        if self._diagnostics is None:
            self._diagnostics = {i: issue for i in range(len(self.edges)) if (issue := self.issue(i))}
        return self._diagnostics
