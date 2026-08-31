"""Governed artifacts and explicit DataForge adaptations of reviewed DataFlow identities."""
from copy import deepcopy
import hashlib
import json
import math
import re
import unicodedata

from .base import OperatorResult
from .derived_text import content_digest, derived_record, is_source, prepare_generation, restore_evidence
from ..governance_catalog import GENERIC_SCORE, SCORES, SEMANTIC_FIELDS, SEMANTIC_MODEL
from .semantic_contract import MAX_GROUP_RECORDS, REVISION as SEMANTIC_REVISION


def business_parameters(executor, params):
    from jsonschema import Draft202012Validator
    properties = executor.parameter_schema.get("properties", {})
    result = {key: deepcopy(params[key] if key in params else spec["default"])
              for key, spec in properties.items() if key in params or "default" in spec}
    def finite(value):
        if isinstance(value, dict):
            for item in value.values():
                finite(item)
        elif isinstance(value, list):
            for item in value:
                finite(item)
        elif isinstance(value, float) and not math.isfinite(value):
            raise ValueError("PARAMETER_RANGE_INVALID: 参数不能为NaN或Infinity")
    finite(result)
    Draft202012Validator(executor.parameter_schema).validate(result)
    for low, high in (("min_length", "max_length"), ("min_mtld", "max_mtld"), ("min_hdd", "max_hdd"), ("min_score", "max_score"), ("min_sentences", "max_sentences")):
        if low in result and result[low] > result[high]:
            raise ValueError("PARAMETER_RANGE_INVALID: 最小值不能大于最大值")
    return result


def checked_rows(rows, allowed, complete=False):
    mapped = {}
    if not isinstance(rows, list):
        raise ValueError("OPERATOR_OUTPUT_INVALID: 输出必须是记录列表")
    for row in rows:
        key = row.get("_df_row") if isinstance(row, dict) else None
        if type(key) is not int or key not in allowed or key in mapped:
            raise ValueError("SOURCE_LINEAGE_MISMATCH: 输出含缺失、重复或未知行身份")
        mapped[key] = row
    if complete and set(mapped) != set(allowed):
        raise ValueError("SOURCE_LINEAGE_MISMATCH: 算子不得增删记录")
    return mapped


def guarded_serving(adapter, callback):
    def call(message):
        replies = callback(message)
        if not isinstance(replies, list) or len(replies) != len(message.get("user_inputs", [])):
            raise ValueError("GOVERNANCE_RESPONSE_INVALID: 模型响应数量不匹配")
        for reply in replies:
            if adapter == "governance-evaluate-v1":
                if not isinstance(reply, str):
                    raise ValueError("QA_SCORE_INVALID: 模型评分必须为文本")
                match = re.fullmatch(r"\s*\*\*Grading\*\*:\s*([1-5])\s*\*\*Feedback\*\*:\s*(\S[\s\S]*)", reply or "")
                if not match:
                    raise ValueError("QA_SCORE_INVALID: 需要1–5整数Grading和非空Feedback")
            else:
                try:
                    value = json.loads(reply)
                except (ValueError, TypeError):
                    raise ValueError("MULTIHOP_OUTPUT_INVALID: 多跳响应必须为JSON问答或数组") from None
                values = value if isinstance(value, list) else [value]
                for qa in values:
                    if (not isinstance(qa, dict) or any(not isinstance(qa.get(key), str) or not qa[key].strip() for key in ("question", "answer"))
                            or not isinstance(qa.get("reasoning_steps"), list) or len(qa["reasoning_steps"]) < 2
                            or any(not isinstance(step, dict) or not isinstance(step.get("step"), str) or not step["step"].strip() for step in qa["reasoning_steps"])
                            or not isinstance(qa.get("supporting_facts"), list) or not qa["supporting_facts"]
                            or any(not isinstance(fact, str) or not fact.strip() for fact in qa["supporting_facts"])):
                        raise ValueError("MULTIHOP_OUTPUT_INVALID: 问答及多跳辅助字段不合法")
        return replies
    return call


def condition_value(rule, value, text):
    field, operation = rule["field"], rule["operator"]
    if field in [*SCORES, GENERIC_SCORE]:
        node = rule.get("evaluation_node")
        evaluation = (value.get("evaluation_results") or {}).get(node)
        if not evaluation or evaluation.get("content_digest") != content_digest(value):
            raise ValueError("EVALUATION_MISSING_OR_STALE: 所需评分缺失或正文已改变")
        if field == GENERIC_SCORE and evaluation.get("operator") != "PromptedEvaluator":
            raise ValueError("EVALUATION_MISSING_OR_STALE: 评分来源不是通用质量评估器")
        score_key = "score" if field == GENERIC_SCORE else field
        current = evaluation.get("scores", {}).get(score_key)
        if type(current) not in (int, float) or not 1 <= current <= 5:
            raise ValueError("QA_SCORE_INVALID: 所需评分非法")
    elif field in SEMANTIC_FIELDS:
        node = rule.get("deduplication_node")
        annotation = (value.get("deduplication_results") or {}).get(node)
        if (not annotation or annotation.get("content_digest") != content_digest(value)
                or annotation.get("operator") != "SemDeduplicateFilter"
                or annotation.get("status") != "evaluated"
                or annotation.get("model_revision") != SEMANTIC_REVISION):
            raise ValueError("DEDUPLICATION_MISSING_OR_STALE: 所需语义标记缺失或正文已改变")
        current = bool(annotation.get("duplicate_of")) if field == "semantic_duplicate" else annotation.get("similarity_score")
        if field == "semantic_similarity" and (type(current) not in (int, float) or not math.isfinite(current) or not -1 <= current <= 1):
            raise ValueError("SEMANTIC_SIMILARITY_INVALID: 语义相似度缺失或非法")
    elif field == "text":
        current = text
    elif field == "length":
        current = len(text)
    else:
        data = value.get("data_json") or {}
        if field not in data:
            raise ValueError("FILTER_FIELD_MISSING: 候选缺少所需字段")
        current = data[field]
    expected = rule.get("value")
    if operation in {"is_empty", "not_empty"}:
        return current
    if "value" not in rule:
        raise ValueError("FILTER_VALUE_MISSING: 条件缺少比较值")
    numeric = lambda item: type(item) in (int, float)
    same = lambda left, right: type(left) is type(right) or numeric(left) and numeric(right)
    if operation in {"gt", "ge", "lt", "le"} and not (numeric(current) and numeric(expected)):
        raise ValueError("FILTER_TYPE_INVALID: 大小比较必须使用数值")
    if operation in {"eq", "ne"} and not same(current, expected):
        raise ValueError("FILTER_TYPE_INVALID: 比较字段与值类型不一致")
    if operation == "contains" and not (isinstance(current, str) and isinstance(expected, str)):
        raise ValueError("FILTER_TYPE_INVALID: 包含条件必须使用文本")
    if operation == "in" and (not isinstance(expected, list) or any(not same(current, entry) for entry in expected)):
        raise ValueError("FILTER_TYPE_INVALID: 集合成员类型不一致")
    return current


def execute_governance(executor, values, params, context, invoke):
    business = business_parameters(executor, params)
    init = {key: value for key, value in business.items() if key != "llm_serving"}
    adapter = executor.adapter
    if adapter == "governance-multihop-v1":
        return multihop(values, init, context, invoke)
    if adapter == "governance-evaluate-v1":
        return evaluate(values, context, invoke)
    if adapter == "governance-evaluate_generic-v1":
        return evaluate_generic(executor, values, business, params, context, invoke)
    if adapter == "governance-semantic-v1":
        return semantic_deduplicate(executor, values, business, params, context, invoke)
    sources = all(is_source(value) for value in values)
    if not sources and any(is_source(value) for value in values):
        raise ValueError("OPERATOR_CONTRACT_MISMATCH: 不能混合来源和候选")
    if not sources and params.get("knowledge_type") not in {"text", "qa"}:
        raise ValueError("OPERATOR_CONTRACT_MISMATCH: 正文治理仅支持Text/QA")
    originals = [derived_record(value) for value in values] if sources else deepcopy(values)
    records, skipped = [], set()
    semicolon_as_boundary = init.pop("semicolon_as_boundary", False) if adapter == "governance-sentence-v1" else False
    for index, value in enumerate(originals):
        if sources:
            if value["disposition"] == "filtered":
                continue
            text = value["effective_text"]
        else:
            if not all(value.get(key) for key in ("source_knowledge_id", "source_chunk_id", "source_version_ids")):
                raise ValueError("SOURCE_LINEAGE_MISSING: 候选来源缺失")
            text = value.get("canonical_content")
        if not isinstance(text, str) and not (executor.code == "ContentNullFilter" and text is None):
            raise ValueError("OPERATOR_INPUT_INVALID: 正文必须是字符串")
        if adapter == "governance-lexical-v1" and not 50 < len(text.split()) < 1000:
            skipped.add(index); continue
        analysis_text = text
        if adapter == "governance-sentence-v1":
            boundaries = r"[.!?。！？]+|\n+"
            if semicolon_as_boundary:
                boundaries = r"[.!?。！？;；]+|\n+"
            pieces = [part.strip() for part in re.split(boundaries, text) if part.strip()]
            analysis_text = ".".join("sentence" for _ in pieces)
        elif adapter == "governance-symbol_ratio-v1":
            analysis_text = " ".join("#" if unicodedata.category(char)[:1] in {"P", "S"} else "w"
                                     for char in text if not char.isspace())
        record = {"_df_row": index, "text": analysis_text}
        if adapter == "governance-conditions-v1":
            for number, rule in enumerate(init["rules"]):
                record[f"rule_{number}"] = condition_value(rule, value, text)
        records.append(record)
    if adapter == "governance-lexical-v1":
        init = {"min_scores": {"mtld": init["min_mtld"], "hdd": init["min_hdd"]},
                "max_scores": {"mtld": init["max_mtld"], "hdd": init["max_hdd"]}}
    run = {} if adapter == "governance-conditions-v1" else {"input_key": "text"}
    refiner = adapter in {"governance-anonymize-v1", "governance-punctuation-v1"}
    if refiner and not sources and params.get("knowledge_type") == "qa":
        records = []
        for index, value in enumerate(originals):
            for offset, field in enumerate(("question", "answer")):
                text = (value.get("data_json") or {}).get(field)
                if not isinstance(text, str) or not text.strip():
                    raise ValueError("QA_OUTPUT_INVALID: 匿名化需要合法问题和答案")
                records.append({"_df_row": index * 2 + offset, "text": text})
    action = "governance_punctuation_refine" if adapter == "governance-punctuation-v1" else "execute"
    rows = checked_rows(invoke(records, init=init, run_arguments=run, action=action) if records else [],
                        {row["_df_row"] for row in records}, complete=refiner)
    result = []
    for index, value in enumerate(originals):
        before_digest = content_digest(value)
        retained = index in rows or index in skipped
        if refiner and not sources and params.get("knowledge_type") == "qa":
            retained = True
            question, answer = rows[index * 2]["text"], rows[index * 2 + 1]["text"]
            if any(not isinstance(text, str) or not text.strip() for text in (question, answer)):
                raise ValueError("REFINER_OUTPUT_INVALID: 处理后的问答不能为空")
            value["data_json"].update(question=question, answer=answer)
            value["canonical_content"] = f"{question} {answer}"
        elif retained and refiner:
            text = rows[index]["text"]
            if not isinstance(text, str):
                raise ValueError("REFINER_OUTPUT_INVALID: 处理后的正文必须为字符串")
            value["effective_text" if sources else "canonical_content"] = text
        if adapter == "governance-punctuation-v1" and not sources:
            value.setdefault("processing_records", []).append({"node_id": context.node_id, "operator": executor.code,
                "version": executor.version, "status": "refined" if content_digest(value) != before_digest else "unchanged"})
        if sources:
            if adapter == "governance-punctuation-v1":
                if value["disposition"] == "keep":
                    value["processing_records"].append({"node_id": context.node_id, "operator": executor.code,
                        "version": executor.version, "status": "refined" if content_digest(value) != before_digest else "unchanged"})
            elif value["disposition"] == "keep":
                value["disposition"] = "keep" if retained else "filtered"
                value["processing_records"].append({"node_id": context.node_id, "operator": executor.code,
                    "version": executor.version, "status": "not_scored" if index in skipped else value["disposition"]})
            result.append(value)
        elif retained:
            if index in skipped:
                value.setdefault("processing_records", []).append({"node_id": context.node_id, "operator": executor.code, "status": "not_scored"})
            result.append(value)
    kept = sum(row["disposition"] == "keep" for row in result) if sources else len(result)
    return OperatorResult(outputs=result, metrics={"input_records": len(values), "output_records": len(result),
        "retained_records": kept, "filtered_records": len(values) - kept, "not_scored_records": len(skipped)})


def evaluate_generic(executor, values, business, params, context, invoke):
    if params.get("knowledge_type") not in {"text", "qa"}:
        raise ValueError("OPERATOR_CONTRACT_MISMATCH: 通用评估仅支持Text/QA Candidate")
    prompt = params.get("_resolved_prompt_template") or {}
    prompt_body = prompt.get("body")
    prompt_revision_id = params.get("prompt_template_revision_id")
    if not prompt_body or not prompt_revision_id or prompt.get("id", prompt_revision_id) != prompt_revision_id:
        raise ValueError("PROMPT_REVISION_NOT_PUBLISHED: 通用评估必须使用已冻结Prompt Revision")
    records = []
    for index, value in enumerate(values):
        if not all(value.get(key) for key in ("source_knowledge_id", "source_chunk_id", "source_version_ids")):
            raise ValueError("SOURCE_LINEAGE_MISSING: 评估候选缺少来源身份")
        text = value.get("canonical_content")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("OPERATOR_INPUT_INVALID: 评估正文必须为非空字符串")
        if params.get("knowledge_type") == "qa":
            qa = value.get("data_json") or {}
            if any(not isinstance(qa.get(key), str) or not qa[key].strip() for key in ("question", "answer")):
                raise ValueError("QA_OUTPUT_INVALID: 通用评估需要非空问题和答案")
            text = json.dumps({key: qa[key] for key in ("question", "answer")}, ensure_ascii=False)
        records.append({"_df_row": index, "text": text})
    rows = checked_rows(invoke(records, init={"system_prompt": prompt_body},
        action="governance_generic_evaluate"), set(range(len(records))), complete=True)
    result = deepcopy(values)
    for index, value in enumerate(result):
        score, reason = rows[index].get("score"), rows[index].get("reason")
        if type(score) is not int or not 1 <= score <= 5 or not isinstance(reason, str) or not reason.strip() or len(reason) > 2000:
            raise ValueError("GENERIC_EVALUATION_INVALID: 评分必须为1–5整数且理由非空")
        value.setdefault("evaluation_results", {})[context.node_id] = {
            "operator": executor.code, "version": executor.version,
            "content_digest": content_digest(value), "criterion_revision_id": str(prompt_revision_id),
            "scores": {"score": score}, "feedback": {"score": reason.strip()},
        }
    return OperatorResult(outputs=result, metrics={"input_records": len(values), "output_records": len(result)})


def semantic_deduplicate(executor, values, business, params, context, invoke):
    sources = all(is_source(value) for value in values)
    if not sources and any(is_source(value) for value in values):
        raise ValueError("OPERATOR_CONTRACT_MISMATCH: 不能混合来源和候选")
    if not sources and params.get("knowledge_type") not in {"text", "qa"}:
        raise ValueError("OPERATOR_CONTRACT_MISMATCH: 语义去重仅支持Text/QA")
    originals = [derived_record(value) for value in values] if sources else deepcopy(values)
    records, skipped, group_counts, identities = [], [], {}, set()
    for index, value in enumerate(originals):
        if sources:
            source = value["source_chunk"]
            text = value["effective_text"]
            versions = [source["source_version_id"]]
            identity = source["source_chunk_id"]
            if value["disposition"] == "filtered":
                skipped.append(index)
                continue
        else:
            if not all(value.get(key) for key in ("source_knowledge_id", "source_chunk_id", "source_version_ids")):
                raise ValueError("SOURCE_LINEAGE_MISSING: 语义标记缺少来源身份")
            text = value.get("canonical_content")
            versions = value["source_version_ids"]
            identity = value["source_knowledge_id"]
        if not isinstance(text, str) or not text.strip():
            raise ValueError("OPERATOR_INPUT_INVALID: 语义去重正文必须为非空字符串")
        if not isinstance(versions, list) or any(not isinstance(version, str) or not version for version in versions):
            raise ValueError("SOURCE_LINEAGE_MISSING: 来源版本列表非法")
        group = "flow_input" if business["scope"] == "flow_input" else json.dumps(sorted(set(versions)), ensure_ascii=True)
        if not isinstance(identity, str) or not identity or identity in identities:
            raise ValueError("SOURCE_LINEAGE_MISMATCH: 语义标记记录身份必须非空且唯一")
        identities.add(identity)
        group_counts[group] = group_counts.get(group, 0) + 1
        if group_counts[group] > MAX_GROUP_RECORDS:
            raise ValueError("SEMANTIC_DEDUP_GROUP_TOO_LARGE: 每个比较组最多5000条记录")
        records.append({"_df_row": index, "_df_group": group, "_df_identity": str(identity), "text": text})
    rows = checked_rows(invoke(records, init={"threshold": business["threshold"]},
        action="governance_semantic_deduplicate") if records else [], {row["_df_row"] for row in records}, complete=True)
    known_representatives = {}
    for record in records:
        row = rows[record["_df_row"]]
        target, score = row.get("duplicate_of"), row.get("similarity_score")
        if (not isinstance(row.get("duplicate_cluster_id"), str) or not row["duplicate_cluster_id"]
                or row.get("model_revision") != SEMANTIC_REVISION
                or type(score) not in (int, float) or not math.isfinite(score) or not -1 <= score <= 1):
            raise ValueError("SEMANTIC_OUTPUT_INVALID: 语义标记输出契约非法")
        if target is not None:
            representative = known_representatives.get((record["_df_group"], target))
            if (representative is None or score < business["threshold"]
                    or representative != row["duplicate_cluster_id"]):
                raise ValueError("SEMANTIC_OUTPUT_INVALID: 重复项未指向同组既有代表项")
        else:
            known_representatives[(record["_df_group"], record["_df_identity"])] = row["duplicate_cluster_id"]
    result = deepcopy(originals)
    for index, value in enumerate(result):
        row = rows.get(index)
        annotation = {
            "operator": executor.code, "version": executor.version, "content_digest": content_digest(value),
            "scope": business["scope"], "model": SEMANTIC_MODEL,
            "model_revision": row.get("model_revision") if row else None,
            "duplicate_cluster_id": row.get("duplicate_cluster_id") if row else None,
            "duplicate_of": row.get("duplicate_of") if row else None,
            "similarity_score": row.get("similarity_score") if row else None,
            "status": "skipped_filtered" if index in skipped else "evaluated",
        }
        value.setdefault("deduplication_results", {})[context.node_id] = annotation
        if sources:
            value["processing_records"].append({"node_id": context.node_id, "operator": executor.code,
                "version": executor.version, "status": annotation["status"]})
    duplicates = sum(bool((value.get("deduplication_results") or {}).get(context.node_id, {}).get("duplicate_of")) for value in result)
    return OperatorResult(outputs=result, metrics={"input_records": len(values), "output_records": len(result),
        "duplicate_records": duplicates, "skipped_records": len(skipped)})


def evaluate(values, context, invoke):
    records = []
    for index, value in enumerate(values):
        data = value.get("data_json") or {}
        if any(not isinstance(data.get(field), str) or not data[field].strip() for field in ("question", "answer")):
            raise ValueError("QA_OUTPUT_INVALID: 评估需要问题和答案")
        if not value.get("source_chunk_id") or not value.get("source_version_ids"):
            raise ValueError("SOURCE_LINEAGE_MISSING: 评估候选来源缺失")
        records.append({"_df_row": index, "question": data["question"], "answer": data["answer"]})
    arguments = {"input_question_key": "question", "input_answer_key": "answer"}
    for metric in SCORES:
        arguments[f"output_{metric}_key"] = metric
        arguments[f"output_{metric}_feedback_key"] = metric + "_feedback"
    rows = checked_rows(invoke(records, run_arguments=arguments), set(range(len(values))), complete=True)
    result = deepcopy(values)
    for index, value in enumerate(result):
        scores, feedback = {}, {}
        for metric in SCORES:
            score, explanation = rows[index].get(metric), rows[index].get(metric + "_feedback")
            if type(score) not in (int, float) or score not in range(1, 6) or not isinstance(explanation, str) or not explanation.strip():
                raise ValueError("QA_SCORE_INVALID: 上游评分或反馈非法")
            scores[metric] = int(score); feedback[metric] = explanation
        value.setdefault("evaluation_results", {})[context.node_id] = {"operator": "Text2QASampleEvaluator", "version": 1,
            "content_digest": content_digest(value), "scores": scores, "feedback": feedback}
    return OperatorResult(outputs=result, metrics={"input_records": len(values), "output_records": len(result)})


def multihop(values, init, context, invoke):
    inputs, originals = prepare_generation(values, "qa", context)
    runtime = context.runtime
    outcome = runtime["generation"]["qa"]
    result = []
    for chunk in inputs:
        outcome["targeted"].append(chunk)
        try:
            rows = checked_rows(invoke([{"_df_row": 0, "text": chunk["content"]}], init=init,
                 run_arguments={"input_key": "text", "output_key": "qa_pairs", "output_meta_key": "qa_metadata"}), {0}, complete=True)
            pairs = rows[0].get("qa_pairs")
            if not isinstance(pairs, list):
                raise ValueError("MULTIHOP_OUTPUT_INVALID: 上游未返回问答列表")
            candidates = []
            for pair in pairs:
                if any(not isinstance(pair.get(field), str) or not pair[field].strip() for field in ("question", "answer")):
                    raise ValueError("MULTIHOP_OUTPUT_INVALID: 问题和答案必须非空")
                data = {field: pair[field].strip() for field in ("question", "answer")}
                identity = f"{chunk['source_id']}|qa|{chunk.get('chunk_index', 0)}|{data['question']}"
                candidates.append({"source_knowledge_id": hashlib.sha256(identity.encode()).hexdigest(),
                    "canonical_content": f"{data['question']} {data['answer']}", "data_json": data,
                    "source_version_ids": [chunk["source_version_id"]], "source_chunk_id": chunk["source_chunk_id"],
                    "source_anchor": f"{chunk.get('filename', '')}#chunk-{chunk.get('chunk_index', 0)}", "is_primary": True})
            outcome["successful"].append(chunk); result.extend(candidates)
            if runtime.get("store") and runtime.get("job_id"):
                runtime["store"].record_chunk_generation(runtime["job_id"], "qa", chunk, status="completed", candidate_count=len(candidates))
        except Exception as exc:
            if "OPERATOR_CANCELLED" in str(exc):
                raise
            error = runtime["_operator_diagnostics"].error(exc)
            outcome["failed"].append({**chunk, "error": error})
            if runtime.get("store") and runtime.get("job_id"):
                runtime["store"].record_chunk_generation(runtime["job_id"], "qa", chunk, status="failed", error=error)
    result = restore_evidence(result, originals, "qa", context)
    if outcome["failed"] and not outcome["successful"]:
        raise ValueError(outcome["failed"][0]["error"])
    return OperatorResult(outputs=result, metrics={"input_records": len(values), "output_records": len(result)})
