"""Versioned, read-only developer samples and side-effect-free previews."""
from __future__ import annotations

import hashlib
import json
from importlib.resources import files
from typing import Any

from .catalog import normalize_chunker_params


SAMPLE_METADATA: tuple[dict[str, Any], ...] = (
    {
        "code": "preprocessing-document-v1",
        "name": "DataForge 示例文档",
        "version": "1",
        "purpose": "preprocessing",
        "readonly": True,
        "resource": "preprocessing/sample-document-v1.md",
        "content_type": "text/markdown",
    },
    {
        "code": "reviewed-medical-v1",
        "name": "DataForge 示例审核数据（历史 5 块）",
        "version": "1",
        "purpose": "knowledge_flow",
        "readonly": True,
        "listed": False,
        "resource": "knowledge-flow/reviewed-chunks-v1.json",
        "content_type": "application/json",
    },
    {
        "code": "reviewed-medical-v2",
        "name": "DataForge 医疗知识生产示例 · 审核结果",
        "version": "2",
        "purpose": "knowledge_flow",
        "readonly": True,
        "resource": "knowledge-flow/reviewed-chunks-v2.json",
        "content_type": "application/json",
    },
)


class SampleDataService:
    def __init__(self) -> None:
        self._metadata = {item["code"]: dict(item) for item in SAMPLE_METADATA}

    @staticmethod
    def _read(relative: str) -> bytes:
        return files("dataforge.v7").joinpath("resources", "samples", *relative.split("/")).read_bytes()

    def list(self, purpose: str = "") -> list[dict[str, Any]]:
        values = []
        for item in self._metadata.values():
            if not item.get("listed", True) or purpose and item["purpose"] != purpose:
                continue
            digest = hashlib.sha256(self._read(item["resource"])).hexdigest()
            values.append({key: value for key, value in item.items()
                           if key not in {"resource", "listed"}} | {"digest": digest})
        return values

    def get(self, code: str) -> dict[str, Any]:
        item = self._metadata.get(code)
        if not item:
            raise ValueError("内置示例不存在")
        raw = self._read(item["resource"])
        payload: Any = json.loads(raw.decode("utf-8")) if item["content_type"] == "application/json" else raw.decode("utf-8")
        result = {key: value for key, value in item.items() if key not in {"resource", "listed"}}
        result["digest"] = hashlib.sha256(raw).hexdigest()
        if item["purpose"] == "preprocessing":
            result.update({"filename": "dataforge-sample.md", "content": payload})
        else:
            result.update(payload)
        return result

    def reviewed_chunks(self, code: str = "reviewed-medical-v2") -> dict[str, Any]:
        value = self.get(code)
        if value["purpose"] != "knowledge_flow":
            raise ValueError("示例用途不是知识流程")
        chunks = []
        for index, raw in enumerate(value.get("chunks") or []):
            content = str(raw.get("content") or "")
            if raw.get("status") != "approved" or not content:
                raise ValueError("内置审核示例必须只包含 approved 文档块")
            chunks.append({
                "source_id": f"sample-source:{code}",
                "source_version_id": f"sample-version:{code}:{value['version']}",
                "source_chunk_id": str(raw.get("chunk_key") or f"sample-{index + 1:03d}"),
                "filename": value.get("source_filename") or value["name"],
                "content": content,
                "text": content,
                "chunk_index": index,
                "status": "approved",
                "anchor": dict(raw.get("anchor") or {}),
            })
        digest = hashlib.sha256(json.dumps(chunks, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        return {**value, "chunks": chunks, "input_digest": digest}


def preview_preprocessing_document(document: dict[str, Any], configuration: dict[str, Any]) -> dict[str, Any]:
    """Run the existing deterministic clean/chunk primitives without persistence."""
    from .runner import _clean_document_text, split_document_text

    raw_text = str(document.get("text") or document.get("content") or "")
    if not raw_text:
        raise ValueError("预览文档没有可处理内容")
    params = dict(configuration or {})
    if "overlap_ratio" in params and "overlap_percent" not in params:
        params["overlap_percent"] = round(float(params.pop("overlap_ratio")) * 100)
    if "preserve_heading_context" in params and "include_heading" not in params:
        params["include_heading"] = bool(params.pop("preserve_heading_context"))
    normalized = normalize_chunker_params(params)
    cleaned = _clean_document_text(raw_text, "text-cleaner")
    cleaned = _clean_document_text(cleaned, "whitespace-cleaner")
    cleaned = _clean_document_text(cleaned, "text-normalizer")
    chunks = []
    for index, raw in enumerate(split_document_text(cleaned, normalized)):
        content = str(raw.get("content") or "")
        chunks.append({
            "chunk_key": f"preview-{index + 1:03d}", "content": content,
            "char_count": len(content), "anchor": dict(raw.get("anchor") or {}),
        })
    lengths = [item["char_count"] for item in chunks]
    return {
        "source": {"type": document.get("type", "builtin_sample"), "name": document.get("name") or document.get("filename") or "预览文档",
                   "filename": document.get("filename")},
        "original_content": raw_text,
        "cleaned_content": cleaned,
        "configuration": normalized,
        "chunks": chunks,
        "statistics": {
            "chunk_count": len(chunks),
            "avg_chars": round(sum(lengths) / len(lengths), 1) if lengths else 0,
            "min_chars": min(lengths) if lengths else 0,
            "max_chars": max(lengths) if lengths else 0,
        },
        "persisted": False,
    }
