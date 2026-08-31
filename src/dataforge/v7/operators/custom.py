"""Reviewed plugin execution with explicit mappings and server-owned lineage."""
from copy import deepcopy
from .base import OperatorResult
from .dataflow import serving_call
from .runtime import OperatorRuntime
from .diagnostics import capture_operator_diagnostics
from ..operator_runtime_contract import validate_runtime_requirements

PROTECTED = ("source_id", "source_version_id", "source_version_ids", "source_chunk_id", "source_chunk_revision_id",
             "source_review_snapshot_id", "source_anchor", "anchor", "anchor_json", "evidence_text", "source_knowledge_id")


def validate_records(records, artifact_type):
    if not isinstance(records, list) or any(not isinstance(row, dict) for row in records):
        raise ValueError("输出必须为记录对象数组")
    for row in records:
        if artifact_type == "source_chunk_set" and not isinstance(row.get("content"), str):
            raise ValueError("source_chunk_set 缺少正文")
        if artifact_type == "entity_candidate_set" and not isinstance(row.get("entities"), list):
            raise ValueError("entity_candidate_set 缺少 entities 数组")
        if artifact_type == "entity_candidate_set" and any(not isinstance(entity, dict) or not isinstance(entity.get("name"), str) or not entity["name"].strip() for entity in row["entities"]):
            raise ValueError("entity_candidate_set 包含非法实体")
        if artifact_type == "relation_candidate_set" and not isinstance(row.get("relations"), list):
            raise ValueError("relation_candidate_set 缺少 relations 数组")
        if artifact_type == "relation_candidate_set" and any(not isinstance(relation, dict) or any(not isinstance(relation.get(key), str) or not relation[key].strip() for key in ("source", "target", "type")) for relation in row["relations"]):
            raise ValueError("relation_candidate_set 包含非法关系")
        if artifact_type.startswith("candidate:") and (not isinstance(row.get("canonical_content"), str) or not row.get("source_knowledge_id")):
            raise ValueError("候选知识缺少正文或身份")
        if artifact_type == "candidate:qa" and any(not isinstance((row.get("data_json") or {}).get(key), str) for key in ("question", "answer")):
            raise ValueError("问答候选缺少问题或答案")
        if artifact_type == "candidate:graph:triple" and any(not isinstance((row.get("data_json") or {}).get(key), str) for key in ("subject", "predicate", "object")):
            raise ValueError("三元组候选缺少主语、谓语或宾语")
        if artifact_type in {"candidate:graph:semantic", "semantic_relation_set"} and any(not isinstance((row.get("data_json") or {}).get(key), dict) for key in ("source_entity", "target_entity", "relation")):
            raise ValueError("语义关系候选缺少实体或关系契约")


class CustomOperatorExecutor:
    def __init__(self, definition, runtime=None):
        self.code, self.version = definition["code"], definition["version"]
        self.definition = definition
        self.spec = deepcopy(definition["runtime_requirements"])
        runtime_identity = validate_runtime_requirements(self.spec)
        if runtime_identity["driver"] != "custom":
            raise ValueError("CustomOperatorExecutor 只接受 custom Runtime Driver")
        if self.spec.get("adapter_version") != "custom-records-v1":
            raise ValueError("未批准的自定义字段适配版本")
        self.runtime = runtime or OperatorRuntime()

    @capture_operator_diagnostics
    def execute(self, *, inputs, params, context):
        from ..operator_parameters import validate_parameters
        params = validate_parameters(self.definition["parameter_schema"], params, node_id=context.node_id, runtime=True)
        if not self.spec.get("approved") and not context.runtime.get("validation"):
            raise ValueError("自定义算子尚未获批")
        if not inputs:
            return OperatorResult()
        validate_records(inputs, self.definition["input_ports"]["input"]["artifact_type"])
        records = []
        for index, row in enumerate(inputs):
            if not row.get("source_chunk_id") or not (row.get("source_version_id") or row.get("source_version_ids")):
                raise ValueError("SOURCE_LINEAGE_MISSING: 自定义算子输入缺少审核来源")
            if not row.get("source_chunk_revision_id") or not row.get("source_review_snapshot_id") or not isinstance(row.get("anchor_json", row.get("anchor")), dict):
                raise ValueError("SOURCE_LINEAGE_MISSING: 自定义算子必须保留审核修订和完整 Anchor")
            record = deepcopy(row) if self.spec["executor"] == "custom-native" else {
                target: deepcopy(row[source]) for source, target in self.spec.get("input_mapping", {}).items()}
            record["_df_row"] = index
            records.append(record)
        init = {key: params[key] for key in self.spec.get("init_parameters", []) if key in params}
        ctx = {"flow_run_id": context.flow_run_id, "node_id": context.node_id, "source_version_id": context.source_version_id,
               "requested_by": context.requested_by, "runtime": {"source_reviews": [
                   {key: deepcopy(row[key]) for key in PROTECTED if key in row} for row in inputs]
                   if "source-review-read" in self.spec.get("capabilities", []) else []}}
        rows = self.runtime.call(self.spec, records=records, init=init, run_arguments=self.spec.get("run_arguments"),
            context=ctx, params=params, cancelled=context.runtime.get("cancelled"),
            diagnostics=context.runtime["_operator_diagnostics"],
            serving=serving_call(params, context.runtime) if self.spec.get("uses_llm") else None)["outputs"]
        result = []
        for row in rows:
            index = row.get("_df_row")
            if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(inputs):
                raise ValueError("SOURCE_LINEAGE_MISMATCH: 输出丢失或伪造来源关联")
            original = inputs[index]
            output = ({key: value for key, value in row.items() if key != "_df_row"} if self.spec["executor"] == "custom-native" else
                      {target: row[source] for source, target in self.spec.get("output_mapping", {}).items()})
            for field in PROTECTED:
                if field in output and output[field] != original.get(field):
                    raise ValueError(f"SOURCE_LINEAGE_MISMATCH: 插件修改受保护字段 {field}")
            result.append({**deepcopy(original), **deepcopy(output)})
        validate_records(result, self.definition["output_ports"]["output"]["artifact_type"])
        return OperatorResult(outputs=result)
