"""Versioned reusable DAG assets and their current consumers."""
from __future__ import annotations

from copy import deepcopy
from uuid import uuid4

from sqlalchemy import select

from .catalog import SUBFLOW_DISPLAY_NAMES_ZH, catalog_by_code
from .operator_catalog import load_catalog, resolve_operator
from .flow import (FlowCompiler, FlowValidationError, _edge, _node_ports, node_role,
                   resolve_port_contract, resolve_subflow, subflow_dependencies,
                   validate_flow_edges, _contains_knowledge_library_binding)
from .models import (FlowSubgraph, FlowSubgraphRevision, FlowExecutionSnapshot,
                     KnowledgeFlowTemplate, KnowledgeFlowTemplateRevision, utc_now)


def published_subflows(session):
    """Exact revision keys plus deterministic aliases for legacy drafts only."""
    result = {}
    rows = session.execute(select(FlowSubgraph, FlowSubgraphRevision).join(
        FlowSubgraphRevision, FlowSubgraphRevision.flow_subgraph_id == FlowSubgraph.id
    ).where(FlowSubgraph.status == "active", FlowSubgraphRevision.status == "published")
        .order_by(FlowSubgraphRevision.revision_no)).all()
    for asset, revision in rows:
        value = {**deepcopy(revision.definition_json), "_subgraph_code": asset.code,
                 "_subgraph_revision": revision.revision_no, "_subgraph_revision_id": revision.id,
                 "_input_contract": revision.input_contract, "_output_contract": revision.output_contract}
        result[revision.id] = value
        result[asset.code] = value
    return result


def pin_subflows(definition, registry):
    value = deepcopy(definition)
    for node in value.get("nodes", []):
        if node.get("kind") != "subflow":
            continue
        child = resolve_subflow(node, registry)
        node["subflow_revision_id"] = child["_subgraph_revision_id"]
        dependencies = subflow_dependencies(child, registry)
        # An immutable published asset must never resolve its children to moving aliases.
        def require_pins(graph):
            for nested in graph.get("nodes", []):
                if nested.get("kind") == "subflow":
                    if not nested.get("subflow_revision_id"):
                        raise FlowValidationError("旧子流程包含版本未锁定的嵌套引用，请复制为草稿并发布新 Revision")
                    require_pins(resolve_subflow(nested, registry))
        if dependencies:
            require_pins(child)
    return value


def fragment_boundaries(definition):
    nodes = definition.get("nodes") or []
    ids = [str(node.get("id") or "") for node in nodes]
    if not ids or any(not value or "::" in value for value in ids) or len(set(ids)) != len(ids):
        raise FlowValidationError("子流程节点 id 必须存在、唯一且不包含 ::")
    incoming, outgoing = {value: [] for value in ids}, {value: [] for value in ids}
    for edge in map(_edge, definition.get("edges", [])):
        if edge["source"] not in outgoing or edge["target"] not in incoming:
            raise FlowValidationError("子流程连线引用了不存在的节点")
        outgoing[edge["source"]].append(edge["target"])
        incoming[edge["target"]].append(edge["source"])
    entries = [key for key in ids if not incoming[key]]
    exits = [key for key in ids if not outgoing[key]]
    if len(entries) != 1 or len(exits) != 1:
        raise FlowValidationError("选区必须连通、单入口、单出口")
    visited, queue = set(), [entries[0]]
    while queue:
        current = queue.pop()
        if current in visited:
            continue
        visited.add(current); queue.extend(outgoing[current])
    if visited != set(ids):
        raise FlowValidationError("选区必须连通")
    return entries[0], exits[0]


class SubflowService:
    def __init__(self, store):
        self.store = store

    def validate(self, session, definition, input_contract=None, output_contract=None, *, preparation=False, previous_definition=None):
        registry, catalog = published_subflows(session), load_catalog(session)
        definition = pin_subflows(definition, registry)
        entry, exit_node = fragment_boundaries(definition)
        if definition.get("entry_node") != entry or definition.get("exit_node") != exit_node:
            raise FlowValidationError("入口/出口必须与 DAG 的唯一入口/出口一致，请重新选择并校验")
        if _contains_knowledge_library_binding(definition):
            raise FlowValidationError("子流程不得绑定 KnowledgeLibrary")
        # A fragment has no Sink; preserve the verified source's type context for new
        # operators as well as existing ones, without persisting a synthetic node.
        contexts = {(node.get("params", {}).get("knowledge_type"), node.get("params", {}).get("graph_mode"))
                    for node in (previous_definition or {}).get("nodes", []) if node.get("params", {}).get("knowledge_type")}
        normalization = deepcopy(definition)
        context_node_id = "__subflow_contract__"
        while any(node["id"] == context_node_id for node in normalization["nodes"]):
            context_node_id += "_"
        if len(contexts) == 1:
            kind, mode = next(iter(contexts))
            normalization["nodes"].append({"id": context_node_id, "kind": "knowledge_sink", "knowledge_type": kind, "graph_mode": mode})
            normalization.setdefault("edges", []).append({"source": exit_node, "target": context_node_id})
        normalized = self.store._normalize_flow_parameters(session, normalization, previous_definition=previous_definition)
        definition["nodes"] = [node for node in normalized["nodes"] if node["id"] != context_node_id]
        validate_flow_edges(definition, catalog=catalog, subflows=registry)
        expanded, edges = FlowCompiler(catalog=catalog, subflows=registry)._expand(definition)
        validate_flow_edges({"schema_version": 3, "nodes": expanded, "edges": edges}, catalog=catalog, subflows=registry)
        for node in expanded:
            item = resolve_operator(catalog, node)
            if node.get("kind") != "operator" or node_role(node) != "operator" or node.get("ref") == "document-input":
                raise FlowValidationError("子流程不能包含 Flow Input 或 Knowledge Sink")
            if not item or item.get("exposure") in {"disabled", "internal"} or not item.get("enabled", True):
                raise FlowValidationError("子流程包含未登记或不可用算子")
            if item.get("exposure") == "controlled" and not item.get("approved"):
                raise FlowValidationError("子流程包含尚未批准的算子")
            if not preparation and item.get("surfaces") and "advanced-canvas" not in item["surfaces"]:
                raise FlowValidationError("该算子不能用于知识子流程")
            if node.get("ref") == "document-chunker":
                raise FlowValidationError("可复用子流程不得封装文档切分与输入审核 Gate")
        incoming = {(edge["target"], edge["target_port"]) for edge in edges}
        expanded_entry = next(node["id"] for node in expanded if not any(edge["target"] == node["id"] for edge in edges))
        for node in expanded:
            for port, spec in _node_ports(node, direction="input", catalog=catalog, subflows=registry).items():
                if spec.get("required", True) and spec.get("binding", "edge") == "edge" and node["id"] != expanded_entry and (node["id"], port) not in incoming:
                    raise FlowValidationError(f"子流程内部节点 {node['id']} 缺少输入 {port}")
        by_id = {node["id"]: node for node in definition["nodes"]}
        contracts = []
        for direction, boundary, supplied in (("input", entry, input_contract), ("output", exit_node, output_contract)):
            ports = _node_ports(by_id[boundary], direction=direction, catalog=catalog, subflows=registry)
            derived = {name: {**spec, "artifact_type": resolve_port_contract({}, by_id[boundary], spec).resolved_type}
                       for name, spec in ports.items()}
            if supplied and supplied != derived:
                raise FlowValidationError("输入输出契约与当前边界不一致，请清空契约后重新校验生成")
            contracts.append(derived)
        return definition, *contracts

    def create(self, code, name, description, definition, output_types, selected_node_ids):
        code, name = code.strip(), name.strip()
        if not code or not name:
            raise ValueError("子流程编码和名称不能为空")
        with self.store.sessions.begin() as session:
            if session.scalar(select(FlowSubgraph).where(FlowSubgraph.code == code)):
                raise ValueError("子流程编码已存在")
            normalized = self.store._compile_template_definition(session, definition, output_types)["definition"]
            selected = set(selected_node_ids)
            by_id = {node["id"]: node for node in normalized["nodes"]}
            if not selected or not selected <= by_id.keys():
                raise ValueError("请选择存在的节点")
            edges = list(map(_edge, normalized.get("edges", [])))
            fragment = {"schema_version": 3, "nodes": [node for node in normalized["nodes"] if node["id"] in selected],
                        "edges": [edge for edge in edges if edge["source"] in selected and edge["target"] in selected],
                        "ui": {"positions": {key: pos for key, pos in normalized.get("ui", {}).get("positions", {}).items() if key in selected}}}
            entry, exit_node = fragment_boundaries(fragment)
            for edge in edges:
                if edge["source"] not in selected and edge["target"] in selected and edge["target"] != entry:
                    raise ValueError("外部连线只能进入选区入口")
                if edge["source"] in selected and edge["target"] not in selected and edge["source"] != exit_node:
                    raise ValueError("外部连线只能离开选区出口")
            fragment.update(entry_node=entry, exit_node=exit_node)
            fragment, inputs, outputs = self.validate(session, fragment, previous_definition=fragment)
            asset = FlowSubgraph(id=f"subflow_{uuid4().hex}", code=code, name=name, status="draft")
            session.add(asset); session.flush()
            revision = FlowSubgraphRevision(id=f"subflowrev_{uuid4().hex}", flow_subgraph_id=asset.id,
                revision_no=1, definition_json=fragment, input_contract=inputs, output_contract=outputs,
                description=description.strip(), status="draft")
            session.add(revision); session.flush()
            self.store.audit(session, "subgraph.created", "flow_subgraph", asset.id, {"revision": 1})
            return self.payload(asset, revision, published_subflows(session), load_catalog(session))

    @staticmethod
    def payload(asset, revision, registry, catalog):
        definition = deepcopy(revision.definition_json or {})
        inputs, outputs = revision.input_contract or {}, revision.output_contract or {}
        try:
            by_id = {node["id"]: node for node in definition.get("nodes", [])}
            if not inputs:
                inputs = _node_ports(by_id[definition["entry_node"]], direction="input", catalog=catalog, subflows=registry)
            if not outputs:
                outputs = _node_ports(by_id[definition["exit_node"]], direction="output", catalog=catalog, subflows=registry)
        except (ValueError, KeyError):
            pass  # Legacy invalid definitions remain inspectable, never silently repaired.
        return {"id": asset.id, "code": asset.code, "name": asset.name, "status": asset.status,
                "display_name_zh": SUBFLOW_DISPLAY_NAMES_ZH.get(asset.code),
                "revision_id": revision.id, "latest_revision_id": revision.id, "revision": revision.revision_no,
                "revision_status": revision.status, "description": revision.description, "definition": definition,
                "input_contract": inputs, "output_contract": outputs,
                "node_count": len(definition.get("nodes", [])), "edge_count": len(definition.get("edges", [])),
                "usage": "knowledge"}

    def inventory(self):
        with self.store.sessions() as session:
            assets = list(session.scalars(select(FlowSubgraph).order_by(FlowSubgraph.code)))
            revisions = list(session.scalars(select(FlowSubgraphRevision).order_by(FlowSubgraphRevision.revision_no.desc())))
            registry = published_subflows(session)
            catalog = load_catalog(session)
            references = self.reference_index(session)
            values = []
            for asset in assets:
                versions = [rev for rev in revisions if rev.flow_subgraph_id == asset.id]
                if not versions:
                    continue
                published = next((rev for rev in versions if rev.status == "published"), None)
                draft = next((rev for rev in versions if rev.status == "draft"), None)
                current = published or draft or versions[0]
                payload = self.payload(asset, current, registry, catalog)
                payload["draft_revision"] = draft.revision_no if draft else None
                payload["reference_count"] = len({row["template_id"] for row in references.get(current.id, [])})
                # Batch metadata allows old and mixed revisions to render without per-node fetches.
                payload["revisions"] = [self.payload(asset, rev, registry, catalog) for rev in versions]
                values.append(payload)
            return values

    def revisions(self, subflow_id):
        with self.store.sessions() as session:
            asset = session.get(FlowSubgraph, subflow_id)
            if not asset:
                raise ValueError("子流程不存在")
            registry = published_subflows(session)
            catalog = load_catalog(session)
            return [self.payload(asset, rev, registry, catalog) for rev in session.scalars(select(FlowSubgraphRevision).where(
                FlowSubgraphRevision.flow_subgraph_id == subflow_id).order_by(FlowSubgraphRevision.revision_no.desc()))]

    def detail(self, subflow_id, revision_no):
        return next((value for value in self.revisions(subflow_id) if value["revision"] == revision_no), None) or self.missing()

    @staticmethod
    def missing():
        raise ValueError("子流程修订不存在")

    def copy(self, subflow_id, revision_no):
        with self.store.sessions.begin() as session:
            asset = session.get(FlowSubgraph, subflow_id, with_for_update=True)
            revisions = list(session.scalars(select(FlowSubgraphRevision).where(FlowSubgraphRevision.flow_subgraph_id == subflow_id)))
            source = next((rev for rev in revisions if rev.revision_no == revision_no), None)
            if not asset or not source:
                self.missing()
            revision = FlowSubgraphRevision(id=f"subflowrev_{uuid4().hex}", flow_subgraph_id=asset.id,
                revision_no=max(rev.revision_no for rev in revisions) + 1,
                definition_json=deepcopy(source.definition_json), description=source.description,
                input_contract=deepcopy(source.input_contract), output_contract=deepcopy(source.output_contract), status="draft")
            session.add(revision); session.flush()
            self.store.audit(session, "subgraph.draft_created", "flow_subgraph", asset.id, {"revision": revision.revision_no})
            return self.payload(asset, revision, published_subflows(session), load_catalog(session))

    def save(self, subflow_id, revision_no, definition, description, input_contract, output_contract, *, publish=False, check=False):
        with self.store.sessions.begin() as session:
            asset = session.get(FlowSubgraph, subflow_id, with_for_update=True)
            revision = session.scalar(select(FlowSubgraphRevision).where(
                FlowSubgraphRevision.flow_subgraph_id == subflow_id, FlowSubgraphRevision.revision_no == revision_no))
            if not asset or not revision:
                self.missing()
            if revision.status != "draft" and not check:
                raise ValueError("只能修改或发布子流程草稿")
            if definition is None:
                definition, description = revision.definition_json, revision.description
                input_contract, output_contract = revision.input_contract, revision.output_contract
            normalized, inputs, outputs = self.validate(session, definition, input_contract, output_contract,
                                                       previous_definition=revision.definition_json)
            if check:
                return {"valid": True, "definition": normalized, "input_contract": inputs, "output_contract": outputs}
            revision.definition_json = {**normalized, "_subgraph_code": asset.code, "_subgraph_revision": revision_no}
            revision.description, revision.input_contract, revision.output_contract = description.strip(), inputs, outputs
            if publish:
                revision.status, revision.published_at, asset.status = "published", utc_now(), "active"
            self.store.audit(session, "subgraph.published" if publish else "subgraph.saved", "flow_subgraph", asset.id, {"revision": revision_no})
            session.flush()
            return {**self.payload(asset, revision, published_subflows(session), load_catalog(session)), "status": revision.status}

    def reference_index(self, session):
        assets = {asset.id: asset for asset in session.scalars(select(FlowSubgraph))}
        revisions = list(session.scalars(select(FlowSubgraphRevision)))
        exact = {rev.id: rev for rev in revisions}
        code_version = {(assets[rev.flow_subgraph_id].code, rev.revision_no): rev.id for rev in revisions}
        flows = list(session.scalars(select(KnowledgeFlowTemplate).where(KnowledgeFlowTemplate.status != "archived")))
        flow_revs = list(session.scalars(select(KnowledgeFlowTemplateRevision).order_by(KnowledgeFlowTemplateRevision.revision_no.desc())))
        snapshots = {snap.id: snap for snap in session.scalars(select(FlowExecutionSnapshot).where(
            FlowExecutionSnapshot.id.in_([rev.execution_snapshot_id for rev in flow_revs if rev.execution_snapshot_id])))}
        index = {}
        for flow in flows:
            versions = [rev for rev in flow_revs if rev.knowledge_flow_template_id == flow.id]
            if not versions:
                continue
            current = [versions[0]] if versions[0].status == "draft" else []
            published = next((rev for rev in versions if rev.status == "published"), None)
            if published:
                current.append(published)
            for version in current:
                base = {"template_id": flow.id, "template_name": flow.name,
                        "template_revision": version.revision_no, "revision_status": version.status,
                        "authoring_mode": version.authoring_mode, "purpose": flow.purpose,
                        "is_builtin": bool(flow.managed_template_code)}
                deps = []
                if version.status == "published":
                    snapshot = snapshots.get(version.execution_snapshot_id)
                    if snapshot:
                        deps = [dep for dep in snapshot.dependency_json.get("dependencies", []) if dep.get("kind") == "subflow_revision"]
                        if not deps:
                            for node in snapshot.compiled_definition_json.get("nodes", []):
                                origin = node.get("source_subgraph") or {}
                                rid = origin.get("revision_id") or code_version.get((origin.get("code"), origin.get("revision")))
                                if rid:
                                    deps.append({"id": rid, "code": origin.get("code"), "instance_path": (node.get("origin_path") or str(node["id"]).split("::"))[:-1]})
                    # Unproven authoring references are reported separately, never assigned latest.
                    proven = {tuple(dep.get("instance_path") or []) for dep in deps}
                    for node in version.definition_json.get("nodes", []):
                        if node.get("kind") == "subflow" and (str(node["id"]),) not in proven:
                            deps.append({"id": None, "code": node.get("ref"), "instance_path": [node["id"]]})
                else:
                    def walk(graph, prefix=(), stack=()):
                        for node in graph.get("nodes", []):
                            if node.get("kind") != "subflow":
                                continue
                            rid, path = node.get("subflow_revision_id"), (*prefix, node["id"])
                            rev = exact.get(rid)
                            valid = rev and assets[rev.flow_subgraph_id].code == node.get("ref") and rev.status == "published"
                            deps.append({"id": rid if valid else None, "code": node.get("ref"), "instance_path": list(path)})
                            if valid and rid not in stack:
                                walk(rev.definition_json, path, (*stack, rid))
                    walk(version.definition_json)
                seen = set()
                for dep in deps:
                    key = dep.get("id") or f"unlocked:{dep.get('code')}"
                    path = tuple(dep.get("instance_path") or [])
                    if (key, path) in seen:
                        continue
                    seen.add((key, path))
                    index.setdefault(key, []).append({**base, "node_path": list(path), "indirect": len(path) > 1,
                                                     "version_locked": bool(dep.get("id"))})
        return index

    def references(self, subflow_id, revision_no):
        detail = self.detail(subflow_id, revision_no)
        with self.store.sessions() as session:
            index = self.reference_index(session)
            rows = index.get(detail["revision_id"], [])
            return {"revision_id": detail["revision_id"], "reference_count": len({row["template_id"] for row in rows}),
                    "references": rows, "unlocked_references": index.get(f"unlocked:{detail['code']}", [])}
