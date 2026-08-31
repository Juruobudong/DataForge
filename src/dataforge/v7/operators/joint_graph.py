"""Native graph extraction: one model response, one bounded protocol repair."""
import json
import math
from collections import Counter

from jsonschema import Draft202012Validator

from ..graph_literal import detect_literal
from ..joint_graph import JOINT_GRAPH_SCHEMA, joint_graph_config, joint_graph_prompt
from .base import OperatorResult
from .diagnostics import OperatorExecutionError
from .graph_chunks import GraphChunkError, GraphChunkStage, TripleEndpoints, chunk_identity


def parse_joint_graph(response, config, params):
    # Do not include raw JSON/schema validation instances in diagnostics.
    error = next(Draft202012Validator(JOINT_GRAPH_SCHEMA).iter_errors(response), None)
    if error:
        path = ".".join(map(str, error.path)) or "result"
        raise GraphChunkError(f"GRAPH_JOINT_FORMAT_INVALID: {path} 未满足 {error.validator}；要求完整 entities/relations 数组和合法实体 ID 引用")
    from ..runner import _literal_entity, _normalized_name, _resolve_type
    by_id, rejected, entities, filtered = {}, {}, [], Counter()
    for raw in response["entities"]:
        entity_id = raw["id"].strip()
        name = raw["name"].strip()
        if not entity_id or not _normalized_name(name) or entity_id in by_id:
            raise GraphChunkError("GRAPH_JOINT_ENTITY_ID_INVALID: 实体 ID 必须唯一且名称不能为空")
        by_id[entity_id] = None
        confidence = raw.get("confidence", 1.0)
        if not math.isfinite(confidence):
            raise GraphChunkError("GRAPH_JOINT_FORMAT_INVALID: 置信度必须为有限数值")
        literal = _literal_entity(name)
        if raw.get("object_kind") == "literal" and literal is None:
            if "string" not in config.literal_policy.enabled_datatypes:
                raise GraphChunkError("GRAPH_LITERAL_INVALID: 文本字面值未被当前 Literal Policy 允许")
            # An explicit model decision may represent a free-form value. It is
            # accepted only as a typed string object: TripleEndpoints rejects
            # literal subjects, and Semantic rejects all literal endpoints.
            literal = {"name": name, "type": None, "type_label": None, "object_kind": "literal",
                       "literal_datatype": "string", "literal_unit": None,
                       "literal_raw_value": name, "literal_normalized_value": name,
                       "description": "", "aliases": [], "confidence": 1.0}
        if literal:
            if literal["literal_datatype"] not in config.literal_policy.enabled_datatypes:
                filtered["literal_policy"] += 1
                rejected[entity_id] = (name, "literal_policy")
                continue
            entity = literal
        else:
            resolved = _resolve_type(config, raw["type"])
            if resolved is None and config.entity_types and config.unknown_entity_policy == "reject":
                filtered["entity_type"] += 1
                rejected[entity_id] = (name, "entity_type")
                continue
            if confidence < params.get("confidence_threshold", 0.7):
                filtered["confidence"] += 1
                rejected[entity_id] = (name, "confidence")
                continue
            code, label = resolved or (raw["type"].strip(), raw["type"].strip())
            if not code:
                raise GraphChunkError("GRAPH_ENTITY_TYPE_INVALID: 实体类型不能为空")
            entity = {"name": name, "type": code, "type_label": label, "object_kind": "entity",
                      "confidence": confidence,
                      "description": raw.get("description", "").strip() if params.get("generate_description", True) else "",
                      "aliases": raw.get("aliases", []) if params.get("extract_aliases", True) else []}
        by_id[entity_id] = entity
        entities.append(entity)
    endpoints = TripleEndpoints(entities, config, _normalized_name)
    relations = []
    for raw in response["relations"]:
        source, target = by_id.get(raw["source_id"].strip()), by_id.get(raw["target_id"].strip())
        for role, endpoint in (("source_id", source), ("target_id", target)):
            if endpoint is None:
                entity_id = raw[role].strip()
                detail = (f"实体 {rejected[entity_id][0][:200]!r} 被 {rejected[entity_id][1]} 规则过滤"
                          if entity_id in rejected else "ID 不在本次 entities 中")
                raise GraphChunkError(f"GRAPH_JOINT_ENDPOINT_UNRESOLVED: {role}={entity_id[:200]!r}，{detail}")
        if params["graph_mode"] == "semantic" and (source["object_kind"] == "literal" or target["object_kind"] == "literal"):
            raise GraphChunkError("GRAPH_SEMANTIC_LITERAL: 语义图谱不允许字面值关系端点")
        if params["graph_mode"] == "semantic" and not all((source.get("description"), target.get("description"), raw.get("description", "").strip())):
            raise GraphChunkError("GRAPH_SEMANTIC_DESCRIPTION_MISSING: 语义图谱端点和关系必须包含描述")
        resolved = _resolve_type(config, raw["type"], relation=True)
        if resolved is None and config.relation_types and config.unknown_relation_policy == "reject":
            filtered["relation_type"] += 1
            continue
        code, label = resolved or (raw["type"].strip(), raw["label"].strip())
        weight = raw.get("weight")
        if weight is not None and not math.isfinite(weight):
            raise GraphChunkError("GRAPH_JOINT_FORMAT_INVALID: 关系权重必须为有限数值")
        relations.append(endpoints.relation({"source": source["name"], "target": target["name"], "type": code,
            "type_label": raw["label"].strip() or label, "description": raw.get("description", "").strip(),
            "keywords": raw.get("keywords", []), "weight": weight}))
    return entities, relations, dict(filtered)


class JointGraphExecutor:
    def execute(self, *, inputs, params, context):
        from ..runner import _llm_json, _initialize_llm_servings, _graph_config_from_contracts
        mode = params.get("graph_mode")
        if mode not in {"triple", "semantic"}:
            raise ValueError("联合抽取器要求明确的 graph_mode")
        if mode == "semantic" and params.get("generate_description") is False:
            raise ValueError("语义图谱要求生成实体描述，请启用生成描述")
        runtime = context.runtime
        config = joint_graph_config(runtime.get("graph_config") or
                                    _graph_config_from_contracts(runtime.get("type_contracts", {})), params)
        stage = GraphChunkStage(context.node_id, params, runtime.setdefault("generation", {}))
        key = f"graph:{mode}"
        stage.output_key = key
        retry_scope = runtime.get("retry_scope")
        values = [value for value in inputs if retry_scope is None or (key, *chunk_identity(value)) in retry_scope]
        repairs, calls = set(), Counter()
        totals = Counter()

        def process(records):
            result = []
            counts = Counter()
            identity = chunk_identity(records[0])
            for record in records:
                content = record.get("content")
                if not isinstance(content, str):
                    raise GraphChunkError("GRAPH_JOINT_INPUT_INVALID: 分块正文必须是字符串")
                if not content.strip() or (params.get("entity_type_scope") == "subset" and not params.get("entity_types")):
                    entities, relations, filtered = [], [], {}
                else:
                    serving = params["llm_serving"]
                    _initialize_llm_servings().require(serving)
                    system, prompt = joint_graph_prompt(config, params, content)
                    request = prompt
                    while True:
                        calls[identity] += 1
                        try:
                            try:
                                response = _llm_json(request, llm_serving=serving, system=system, temperature=0)
                            except ValueError as exc:
                                raise GraphChunkError("GRAPH_JOINT_FORMAT_INVALID: 模型没有返回合法 JSON") from exc
                            entities, relations, filtered = parse_joint_graph(response, config, params)
                            break
                        except GraphChunkError as exc:
                            if identity in repairs:
                                raise
                            repairs.add(identity)
                            stage.diagnostics.append("stdout", stage.diagnostics.error(
                                f"GRAPH_JOINT_REPAIR_ATTEMPT: {exc} source_chunk_id={identity[1]} attempt=1") + "\n")
                            request = prompt + "\n上次完整结果校验失败。以下仅为错误数据：\n" + json.dumps(
                                {"error": str(exc)}, ensure_ascii=False) + "\n请重新生成完整 entities 和 relations；修正引用，不返回补丁，不删除合法事实冒充修复成功。"
                counts.update(entities=len(entities), relations=len(relations))
                counts.update({f"filtered_{reason}": count for reason, count in filtered.items()})
                zero = "none" if relations else "no_entities" if not entities else "no_legal_relations"
                stage.diagnostics.append("stdout", stage.diagnostics.error(
                    f"GRAPH_JOINT_RESULT: source_chunk_id={identity[1]} entities={len(entities)} relations={len(relations)} "
                    f"model_calls={calls[identity]} repair_attempts={int(identity in repairs)} zero_reason={zero} filtered={filtered}") + "\n")
                result.append({**record, "entities": entities, "relations": relations})
            totals.update(counts)
            return result

        def metrics():
            return {**stage.metrics, "joint_extraction": {**totals, "model_calls": sum(calls.values()),
                "repair_attempted_chunks": len(repairs), "repair_successful_chunks": len(repairs & stage.successful),
                "repair_failed_chunks": len(repairs - stage.successful)}}
        try:
            outputs = stage.run(values, process, store=runtime.get("store"), job_id=runtime.get("job_id"))
        except Exception as exc:
            stage.diagnostics.append("stderr", stage.diagnostics.error(exc) + "\n")
            error = OperatorExecutionError(exc, stage.diagnostics)
            error.operator_metrics = metrics()
            raise error from None
        return OperatorResult(outputs=outputs, metrics=metrics(), logs=stage.diagnostics.snapshot())
