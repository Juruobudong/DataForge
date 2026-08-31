"""Host-owned, replayable text copies. Never modify reviewed source records."""
from copy import deepcopy
import hashlib
import json

NATIVE_DERIVED_VERSIONS = {"text-knowledge-mapper": 4, "prompt-generator": 7, "structured-knowledge-generator": 7}


def source_key(source):
    if not all(source.get(key) for key in ("source_id", "source_version_id", "source_chunk_id")):
        raise ValueError("SOURCE_LINEAGE_MISSING: 来源 Chunk 身份缺失")
    return str(source["source_version_id"]), str(source["source_chunk_id"])


def is_source(value):
    return "source_chunk" in value or "source_version_id" in value


def derived_record(value):
    if "source_chunk" in value:
        result = deepcopy(value)
        source_key(result["source_chunk"])
        if not isinstance(result["source_chunk"].get("content"), str):
            raise ValueError("DERIVED_TEXT_INVALID: 原始来源正文必须是字符串")
        if result.get("disposition") not in {"keep", "filtered"} or not isinstance(result.get("effective_text"), str):
            raise ValueError("DERIVED_TEXT_INVALID: 派生正文或处理状态非法")
        if not isinstance(result.get("processing_records"), list):
            raise ValueError("DERIVED_TEXT_INVALID: 处理记录必须是列表")
        return result
    source_key(value)
    if not isinstance(value.get("content"), str):
        raise ValueError("DERIVED_TEXT_INVALID: 来源正文必须是字符串")
    return {"source_chunk": deepcopy(value), "effective_text": value["content"],
            "disposition": "keep", "processing_records": []}


def prepare_generation(values, kind, context):
    """Return transient model inputs and original evidence, recording normal exclusions."""
    if kind not in {"text", "qa"}:
        if any("source_chunk" in value for value in values):
            raise ValueError("DERIVED_TEXT_UNSUPPORTED: 派生正文仅支持Text/QA")
        return deepcopy(values), {}
    runtime = context.runtime
    outcome = runtime.setdefault("generation", {}).setdefault(kind, {"targeted": [], "successful": [], "failed": []})
    inputs, originals = [], {}
    for value in values:
        record = derived_record(value)
        source = record["source_chunk"]
        key = source_key(source)
        if key in originals:
            raise ValueError("SOURCE_LINEAGE_MISMATCH: 重复来源 Chunk")
        originals[key] = source
        scope = (kind, *key)
        if runtime.get("retry_scope") is not None and scope not in runtime["retry_scope"]:
            continue
        if record["disposition"] == "filtered":
            outcome["targeted"].append(deepcopy(source))
            outcome["successful"].append(deepcopy(source))
            if runtime.get("store") and runtime.get("job_id"):
                runtime["store"].record_chunk_generation(runtime["job_id"], kind, source, status="completed", candidate_count=0)
        else:
            inputs.append({**deepcopy(source), "content": record["effective_text"]})
    return inputs, originals


def restore_evidence(outputs, originals, kind, context):
    result = []
    for output in outputs:
        versions = output.get("source_version_ids") or []
        key = (str(versions[0]), str(output.get("source_chunk_id"))) if len(versions) == 1 else None
        if key not in originals:
            raise ValueError("SOURCE_LINEAGE_MISMATCH: 生成结果改变来源")
        source = originals[key]
        value = deepcopy(output)
        value.update(evidence_text=source["content"], source_version_ids=[source["source_version_id"]],
                     source_chunk_id=source["source_chunk_id"], source_chunk_revision_id=source.get("source_chunk_revision_id"),
                     source_review_snapshot_id=source.get("source_review_snapshot_id"),
                     anchor_json=deepcopy(source.get("anchor_json") or source.get("anchor") or {}))
        result.append(value)
    outcome = context.runtime.get("generation", {}).get(kind, {})
    for field in ("targeted", "successful", "failed"):
        for index, value in enumerate(outcome.get(field, [])):
            source = originals.get((str(value.get("source_version_id")), str(value.get("source_chunk_id"))))
            if source:
                outcome[field][index] = {**deepcopy(source), **({"error": value["error"]} if "error" in value else {})}
    return result


def content_digest(value):
    if is_source(value):
        payload = {"effective_text": value.get("effective_text") if "source_chunk" in value else value.get("content")}
    else:
        payload = {"canonical_content": value.get("canonical_content"),
                   "question": (value.get("data_json") or {}).get("question"),
                   "answer": (value.get("data_json") or {}).get("answer")}
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
