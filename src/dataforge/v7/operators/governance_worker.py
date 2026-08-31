"""Trusted worker-only argument conversion. No eval, exec, imports from user input."""
from collections import defaultdict
import hashlib
import importlib.util
import json
import operator
import math
import re
from pathlib import Path

_semantic_spec = importlib.util.spec_from_file_location("semantic_contract", Path(__file__).with_name("semantic_contract.py"))
_semantic_contract = importlib.util.module_from_spec(_semantic_spec)
_semantic_spec.loader.exec_module(_semantic_contract)


def lock_network():
    import socket
    def denied(*args, **kwargs):
        raise RuntimeError("OPERATOR_NETWORK_DISABLED: 算子子进程禁止联网，模型调用仅允许宿主Serving代理")
    socket.socket.connect = denied
    socket.socket.connect_ex = denied
    socket.create_connection = denied


def predicates(rules):
    if not isinstance(rules, list) or not 1 <= len(rules) <= 32:
        raise ValueError("FILTER_RULE_INVALID: rules must be a nonempty bounded list")
    comparisons = {"eq": operator.eq, "ne": operator.ne, "gt": operator.gt, "ge": operator.ge, "lt": operator.lt, "le": operator.le}
    result = []
    for index, rule in enumerate(rules):
        operation, value, field = rule.get("operator"), rule.get("value"), f"rule_{index}"
        if operation in comparisons:
            compare = comparisons[operation]
            result.append(lambda frame, key=field, expected=value, fn=compare: frame[key].map(lambda current: bool(fn(current, expected))))
        elif operation == "contains":
            result.append(lambda frame, key=field, expected=value: frame[key].map(lambda current: expected in current))
        elif operation == "in":
            result.append(lambda frame, key=field, expected=value: frame[key].isin(expected))
        elif operation in {"is_empty", "not_empty"}:
            result.append(lambda frame, key=field, empty=operation == "is_empty": frame[key].map(lambda current: (current is None or isinstance(current, float) and math.isnan(current) or current == "" or current == []) == empty))
        else:
            raise ValueError("FILTER_RULE_INVALID: operator is not allowlisted")
    return result


def prepare_init(request, init):
    adapter = request.get("adapter_version")
    if adapter == "governance-conditions-v1":
        init["filter_rules"] = predicates(init.pop("rules", None))
    if adapter == "governance-blocklist-v1":
        init.pop("blocklist", None)
    if adapter in {"governance-privacy_filter-v1", "governance-anonymize-v1"}:
        from pathlib import Path
        root = Path(request["resource_bundle"]["root"])
        init.update(lang="en", device="cpu", model_cache_dir=str(root / "hf" / "hub"))
        import tldextract
        tldextract.extract = tldextract.TLDExtract(suffix_list_urls=(), cache_dir=None)
    if str(adapter).startswith("governance-"):
        # Some upstream constructors try to download even when tokenization is
        # disabled. All corpora must be provisioned by the maintainer beforehand.
        import nltk
        def deny_download(*args, **kwargs):
            raise ValueError("OPERATOR_RESOURCE_MISSING: NLTK资源必须预先安装，运行时禁止下载")
        nltk.download = deny_download
    return init


def configure_operator(request, instance):
    if request.get("adapter_version") == "governance-blocklist-v1":
        words = request.get("init", {}).get("blocklist")
        if words:
            if not isinstance(words, list) or any(not isinstance(word, str) or not word.strip() for word in words):
                raise ValueError("FILTER_RULE_INVALID: invalid blocklist")
            instance.blocklist = {word.strip().lower() for word in words}


def execute_special(request, approved_class, serving):
    """Run adapted semantics after the upstream class ownership check.

    Returning None delegates to the ordinary upstream constructor/run path.
    """
    action = request.get("action")
    records = request.get("records") or []
    special_contracts = {
        "governance_generic_evaluate": ("governance-evaluate_generic-v1", "PromptedEvaluator"),
        "governance_punctuation_refine": ("governance-punctuation-v1", "RemoveRepetitionsPunctuationRefiner"),
        "governance_semantic_deduplicate": ("governance-semantic-v1", "SemDeduplicateFilter"),
    }
    if action in special_contracts:
        expected_adapter, expected_class = special_contracts[action]
        if request.get("adapter_version") != expected_adapter or approved_class.__name__ != expected_class:
            raise ValueError("OPERATOR_IMPLEMENTATION_MISMATCH: 特殊动作不符合冻结Adapter与上游身份")
    if action == "governance_generic_evaluate":
        if serving is None:
            raise ValueError("GENERIC_EVALUATION_INVALID: LLM Serving不可用")
        system_prompt = (request.get("init") or {}).get("system_prompt")
        if not isinstance(system_prompt, str) or not system_prompt.strip():
            raise ValueError("GENERIC_EVALUATION_INVALID: 评估标准Prompt缺失")
        user_inputs = [row.get("text") for row in records]
        if any(not isinstance(text, str) or not text.strip() for text in user_inputs):
            raise ValueError("GENERIC_EVALUATION_INVALID: 评估正文不能为空")
        contract = {
            "type": "object", "additionalProperties": False, "required": ["score", "reason"],
            "properties": {"score": {"type": "integer", "minimum": 1, "maximum": 5},
                           "reason": {"type": "string", "minLength": 1, "maxLength": 2000}},
        }
        system_prompt = (system_prompt.rstrip() +
            "\n\n系统输出契约：只返回一个JSON对象，且只能包含score和reason。"
            "score必须是1到5的整数，reason必须是非空说明；不要使用Markdown代码块。")
        replies = serving.generate_from_input(user_inputs, system_prompt=system_prompt, json_schema=contract)
        if not isinstance(replies, list) or len(replies) != len(records):
            raise ValueError("GENERIC_EVALUATION_INVALID: 模型响应数量不匹配")
        outputs = []
        for row, reply in zip(records, replies):
            try:
                value = json.loads(reply)
            except (TypeError, ValueError):
                raise ValueError("GENERIC_EVALUATION_INVALID: 模型必须返回JSON评分和理由") from None
            if (not isinstance(value, dict) or set(value) != {"score", "reason"}
                    or type(value.get("score")) is not int or not 1 <= value["score"] <= 5
                    or not isinstance(value.get("reason"), str) or not value["reason"].strip()
                    or len(value["reason"]) > 2000):
                raise ValueError("GENERIC_EVALUATION_INVALID: 评分必须为1–5整数且理由非空")
            outputs.append({"_df_row": row["_df_row"], "score": value["score"], "reason": value["reason"].strip()})
        return outputs
    if action == "governance_punctuation_refine":
        pattern = re.compile(r"([,，、;；:：!?！？。])\1+")
        return [{**row, "text": pattern.sub(r"\1", row.get("text", ""))} for row in records]
    if action != "governance_semantic_deduplicate":
        return None
    return semantic_annotations(request, records)


def semantic_annotations(request, records):
    bundle = request.get("resource_bundle") or {}
    model_name = bundle.get("semantic_model")
    revision = bundle.get("semantic_revision")
    root = bundle.get("root")
    if not all(isinstance(value, str) and value for value in (model_name, revision, root)):
        raise ValueError("OPERATOR_RESOURCE_INVALID: 语义模型资源描述不完整")
    threshold = (request.get("init") or {}).get("threshold")
    if type(threshold) not in (int, float) or not 0 <= threshold <= 1:
        raise ValueError("PARAMETER_RANGE_INVALID: 语义相似度阈值非法")
    grouped = defaultdict(list)
    for row in records:
        if (type(row.get("_df_row")) is not int or not isinstance(row.get("_df_group"), str)
                or not isinstance(row.get("_df_identity"), str) or not isinstance(row.get("text"), str)):
            raise ValueError("OPERATOR_INPUT_INVALID: 语义标记输入非法")
        grouped[row["_df_group"]].append(row)
    if any(len(group) > 5000 for group in grouped.values()):
        raise ValueError("SEMANTIC_DEDUP_GROUP_TOO_LARGE: 每个比较组最多5000条记录")
    _semantic_contract.validate_model_bundle(bundle)

    from pathlib import Path
    import torch
    from torch.nn.functional import normalize
    from transformers import AutoModel, AutoTokenizer
    cache = str(Path(root) / "hf" / "hub")
    tokenizer = AutoTokenizer.from_pretrained(model_name, revision=revision, cache_dir=cache, local_files_only=True)
    model = AutoModel.from_pretrained(model_name, revision=revision, cache_dir=cache, local_files_only=True).cpu().eval()
    result = []
    for group_key, group in grouped.items():
        embeddings = encode_semantic_records(group, tokenizer, model)
        if not torch.isfinite(embeddings).all() or (torch.linalg.vector_norm(embeddings, dim=1) == 0).any():
            raise ValueError("SEMANTIC_OUTPUT_INVALID: 模型输出包含非有限或零向量")
        representatives = []
        for index, row in enumerate(group):
            duplicate_of = None
            similarity = 1.0
            representative = index
            if representatives:
                scores = embeddings[index] @ embeddings[representatives].T
                best_offset = int(torch.argmax(scores).item())
                best = max(-1.0, min(1.0, float(scores[best_offset].item())))
                if best >= threshold:
                    representative = representatives[best_offset]
                    duplicate_of = group[representative]["_df_identity"]
                    similarity = best
                else:
                    representatives.append(index)
            else:
                representatives.append(index)
            representative_id = group[representative]["_df_identity"]
            cluster = hashlib.sha256(json.dumps([group_key, representative_id, revision], separators=(",", ":")).encode()).hexdigest()
            result.append({"_df_row": row["_df_row"], "duplicate_cluster_id": cluster,
                           "duplicate_of": duplicate_of, "similarity_score": similarity,
                           "model_revision": revision})
    return result


def encode_semantic_records(records, tokenizer, model):
    """Pool all bounded token windows; never silently discard a document tail."""
    import torch
    from torch.nn.functional import normalize
    vectors = []
    for row in records:
        windows = tokenizer(row["text"], padding=True, truncation=True, max_length=128,
                            return_overflowing_tokens=True, return_tensors="pt")
        windows.pop("overflow_to_sample_mapping", None)
        total, weight = None, 0
        for offset in range(0, windows["input_ids"].shape[0], 32):
            encoded = {key: value[offset:offset + 32] for key, value in windows.items()}
            with torch.no_grad():
                hidden = model(**encoded).last_hidden_state
            mask = encoded["attention_mask"].unsqueeze(-1).to(hidden.dtype)
            subtotal = (hidden * mask).sum(dim=(0, 1))
            total = subtotal if total is None else total + subtotal
            weight += int(mask.sum().item())
        vectors.append(normalize((total / max(1, weight)).unsqueeze(0), dim=1).cpu())
    return torch.cat(vectors, dim=0)
