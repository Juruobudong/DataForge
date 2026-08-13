"""Fixed V7 knowledge processing runner.

The runner has no candidate-confirm step: each successful job atomically writes
the current state and change history of the explicitly selected V7 library.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import tempfile
from importlib import metadata
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from ..config import Settings
from .models import KnowledgeJob, Source, SourceVersion
from .storage import LocalObjectStore, MinioObjectStore
from .store import V7Store


class RunRequest(BaseModel):
    job_id: str


DATAFLOW_VERSION = "1.0.10"


def dataflow_runtime_status() -> dict[str, str | bool]:
    """Report the Runner-only pinned runtime without exposing its classes."""
    try:
        installed = metadata.version("open-dataflow")
    except metadata.PackageNotFoundError:
        installed = None
    return {"package": "open-dataflow", "required": DATAFLOW_VERSION, "installed": installed or "not-installed", "compatible": installed == DATAFLOW_VERSION}


def _objects(settings: Settings):
    if settings.minio_endpoint and settings.minio_access_key and settings.minio_secret_key:
        return MinioObjectStore(settings.minio_endpoint, settings.minio_access_key, settings.minio_secret_key, settings.minio_bucket)
    return LocalObjectStore(settings.state_dir / "v7-objects")


def _extract_text(filename: str, payload: bytes) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix in {".txt", ".md"}: return payload.decode("utf-8-sig", errors="replace")
    if suffix == ".csv":
        rows = csv.reader(io.StringIO(payload.decode("utf-8-sig", errors="replace")))
        return "\n".join(" | ".join(cell.strip() for cell in row if cell.strip()) for row in rows)
    if suffix == ".xlsx":
        from openpyxl import load_workbook
        workbook = load_workbook(io.BytesIO(payload), read_only=True, data_only=True)
        lines: list[str] = []
        for sheet in workbook.worksheets:
            lines.append(f"[Sheet: {sheet.title}]")
            for row in sheet.iter_rows(values_only=True):
                values = [str(value).strip() for value in row if value is not None and str(value).strip()]
                if values:
                    lines.append(" | ".join(values))
        return "\n".join(lines)
    if suffix == ".pdf":
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(payload))
        if reader.is_encrypted: reader.decrypt("")
        value = "\n".join(page.extract_text() or "" for page in reader.pages)
        if not value.strip(): raise ValueError("PDF 未提取到文本；扫描件 OCR 不在本期范围")
        return value
    if suffix == ".docx":
        from docx import Document
        return "\n".join(paragraph.text for paragraph in Document(io.BytesIO(payload)).paragraphs if paragraph.text.strip())
    if suffix == ".doc":
        with tempfile.NamedTemporaryFile(suffix=".doc", delete=False) as handle:
            handle.write(payload); name = handle.name
        try:
            import subprocess
            return subprocess.run(["antiword", name], capture_output=True, check=True, text=True, encoding="utf-8", errors="replace").stdout
        finally:
            Path(name).unlink(missing_ok=True)
    raise ValueError("不支持的文档类型")


def _chunks(text: str, size: int = 800) -> list[str]:
    normalized = re.sub(r"\s+", " ", text).strip()
    return [normalized[index:index + size] for index in range(0, len(normalized), size) if normalized[index:index + size]]


SAMPLE_DOCUMENTS = {
    "guideline-md": ("临床指南.md", "# 高血压指南\n高血压患者应规范随访，并依据风险分层选择治疗方案。"),
    "faq-csv": ("常见问题.csv", "问题,答案\n高血压是否需要随访,需要根据风险分层定期随访。"),
    "case-txt": ("病例摘要.txt", "患者出现高血压并伴随头痛，需要进一步评估。"),
}


def preview_template_definition(definition: dict, sample_id: str) -> dict:
    """Run the fixed parsing/chunking path in memory for developer preview."""
    if sample_id not in SAMPLE_DOCUMENTS:
        raise ValueError("不支持的内置样例")
    filename, text = SAMPLE_DOCUMENTS[sample_id]
    parameters = dict((definition or {}).get("parameters") or {})
    chunks = _chunks(text, int(parameters.get("chunk_size", 800)))
    return {"sample_id": sample_id, "filename": filename, "stages": ["validate", "parse", "normalize", "structure_recovery", "semantic_chunks", "generate"],
            "parsed_text": text, "chunks": [{"index": index, "content": chunk} for index, chunk in enumerate(chunks)],
            "outputs": {"text": len(chunks), "qa": 1 if chunks else 0, "graph": 1 if chunks else 0}, "persisted": False}


def _source_key(source_id: str, output_type: str, anchor: str) -> str:
    return hashlib.sha256(f"{source_id}|{output_type}|{anchor}".encode("utf-8")).hexdigest()


GLOBAL_LLM_MODEL = "qwen3_32b"


def _global_llm_config_path() -> Path:
    configured = os.getenv("DATAFORGE_LLM_CONFIG_PATH")
    if configured:
        return Path(configured).resolve()
    root = Path(os.getenv("DATAFORGE_ROOT") or Path(__file__).resolve().parents[3])
    return root / "llm_local.yaml"


def _initialize_global_llm():
    """Load the Runner's authoritative global_llm configuration once."""
    try:
        from global_llm import chat, get_app_config, init_app
    except ImportError as exc:
        raise RuntimeError("Runner 未安装权威 global_llm 包") from exc
    try:
        get_app_config()
    except RuntimeError:
        config_path = _global_llm_config_path()
        if not config_path.is_file():
            raise RuntimeError(f"Runner 缺少 global_llm 配置文件：{config_path}")
        init_app(config_path)
    return chat


def _llm_json(prompt: str) -> dict[str, Any]:
    """Call the shared global_llm package with DataForge's logical Qwen model."""
    chat = _initialize_global_llm()
    content = chat(
        [
            {"role": "system", "content": "你是严谨的知识抽取器。只返回符合请求的 JSON 对象，不要输出 Markdown 或解释。"},
            {"role": "user", "content": prompt},
        ],
        GLOBAL_LLM_MODEL,
        os.getenv("DATAFORGE_LLM_ORG_CODE") or None,
        response_format={"type": "json_object"},
    )
    if not isinstance(content, str) or not content.strip():
        raise ValueError("LLM 没有返回 JSON 内容")
    try:
        return json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError(f"LLM 返回的内容不是 JSON：{exc.msg}") from exc


def _json_errors(data: dict[str, Any], schema: dict[str, Any]) -> list[str]:
    try:
        from jsonschema import Draft202012Validator
        return [error.message for error in Draft202012Validator(schema).iter_errors(data)]
    except ImportError:
        return [f"缺少必填字段 {item}" for item in schema.get("required", []) if not data.get(item)]


def _chunk_prompt(output_type: str, contract: dict[str, Any], content: str) -> str:
    schema = contract["schema"]
    instructions = {
        "qa": "从当前来源分块生成全部有事实依据且可独立回答的问答对。每项必须包含 question 和 answer。",
        "graph": "从当前来源分块抽取全部明确陈述的关系三元组。每项必须包含 subject、predicate、object。",
        "graph:triple": "从当前来源分块抽取明确关系。每项包含 subject、predicate、object。",
        "graph:semantic": "抽取语义实体关系。每项包含 source_entity、target_entity、relation 和可核验 evidence。",
    }
    type_instruction = instructions.get(output_type, "")
    template = contract.get("prompt") or "根据当前来源分块生成全部有效的结构化知识项。"
    return (
        f"{type_instruction}\n{template}\n\n"
        "仅使用下方当前来源分块；没有有效结果时返回 {\"items\": []}。"
        "必须返回 JSON 对象，且 items 是数组。items 中每一项都必须符合此 JSON Schema：\n"
        f"{json.dumps(schema, ensure_ascii=False)}\n\n"
        f"当前来源分块：\n{content}"
    )


def _item_errors(items: Any, schema: dict[str, Any]) -> list[str]:
    if not isinstance(items, list):
        return ["顶层 JSON 必须包含 items 数组"]
    errors: list[str] = []
    for index, data in enumerate(items):
        if not isinstance(data, dict):
            errors.append(f"items[{index}]：必须是 JSON 对象")
            continue
        errors.extend(f"items[{index}]：{message}" for message in _json_errors(data, schema))
    return errors


def _structured_candidates(source: Source, version: SourceVersion, output_type: str, chunk: dict[str, Any], contract: dict[str, Any]) -> list[dict]:
    prompt = _chunk_prompt(output_type, contract, str(chunk["content"]))
    response = _llm_json(prompt)
    items = response.get("items") if isinstance(response, dict) else None
    errors = _item_errors(items, contract["schema"])
    if errors:
        repair = prompt + "\n\n上次输出校验失败，请只返回修复后的 JSON。错误：" + "；".join(errors)
        response = _llm_json(repair)
        items = response.get("items") if isinstance(response, dict) else None
        errors = _item_errors(items, contract["schema"])
    if errors:
        raise ValueError("LLM 一次修复后仍未通过 Schema 校验：" + "；".join(errors))
    anchor = {"file": source.original_filename, "chunk_index": int(chunk["chunk_index"]), "source_chunk_id": str(chunk["source_chunk_id"])}
    result: list[dict] = []
    for data in items:
        if output_type == "graph:semantic":
            data = dict(data)
            data["evidence"] = [{"source_version_id": version.id, "source_chunk_id": str(chunk["source_chunk_id"])}]
        def nested(path: str) -> Any:
            current: Any = data
            for part in path.split("."):
                current = current.get(part) if isinstance(current, dict) else None
            return current
        canonical = " ".join(str(nested(path) or "").strip() for path in contract.get("canonical_fields") or [contract["canonical_field"]]).strip()
        if not canonical:
            raise ValueError(f"LLM 输出缺少 canonical 字段 {contract['canonical_field']}")
        identity = "|".join(str(nested(field) or "") for field in contract.get("identity_fields", [])) or canonical
        result.append({
            "source_knowledge_id": _source_key(source.id, output_type, f"{chunk['chunk_index']}|{identity}"),
            "canonical_content": canonical,
            "data_json": data,
            "source_version_ids": [version.id],
            "source_chunk_id": chunk["source_chunk_id"],
            "source_anchor": f"{source.original_filename}#chunk-{chunk['chunk_index']}",
            "anchor_json": anchor,
            "evidence_text": chunk["content"],
            "is_primary": True,
        })
    return result


def _candidates(source: Source, version: SourceVersion, output_type: str, chunk: dict[str, Any], *, contract: dict[str, Any] | None = None) -> list[dict]:
    anchor = {"file": source.original_filename, "chunk_index": int(chunk["chunk_index"]), "source_chunk_id": str(chunk["source_chunk_id"])}
    if output_type == "text":
        return [{"source_knowledge_id": _source_key(source.id, "text", str(chunk["chunk_index"])), "canonical_content": chunk["content"], "data_json": {"filename": source.original_filename, "chunk_index": chunk["chunk_index"]}, "source_version_ids": [version.id], "source_chunk_id": chunk["source_chunk_id"], "source_anchor": f"{source.original_filename}#chunk-{chunk['chunk_index']}", "anchor_json": anchor, "evidence_text": chunk["content"], "is_primary": True}]
    if not contract:
        raise ValueError(f"不支持的知识类型或缺少已发布契约：{output_type}")
    return _structured_candidates(source, version, output_type, chunk, contract)


def _incoming(definition: dict[str, Any]) -> dict[str, list[str]]:
    values: dict[str, list[str]] = {node["id"]: [] for node in definition.get("nodes", [])}
    for edge in definition.get("edges", []):
        values.setdefault(edge["target"], []).append(edge["source"])
    return values


def select_parser_adapter(filename: str, profile: str, environ: dict[str, str] | None = None) -> str:
    """Resolve deployment-only MinerU variants behind one logical parser node."""
    variables = environ if environ is not None else os.environ
    suffix = Path(filename).suffix.lower()
    if suffix in {".doc", ".docx"}:
        return "dataforge-word-parser"
    if suffix in {".csv", ".xlsx"}:
        return "dataforge-structured-table-parser"
    if suffix in {".md", ".txt"}:
        return "dataforge-text-parser"
    if suffix in {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".gif"}:
        selected = profile
        if selected == "auto":
            selected = "api" if variables.get("MINERU_API_KEY") else "flash" if variables.get("DATAFORGE_MINERU_FLASH_ENABLED") == "1" else "local"
        return f"mineru-{selected}-adapter"
    raise ValueError("Document Parser 不支持的文档类型")


def select_runtime_mode(record_count: int, environ: dict[str, str] | None = None) -> str:
    """Choose normal vs. DataFlow Batch adapter without changing the Flow DSL."""
    variables = environ if environ is not None else os.environ
    try:
        threshold = max(2, int(variables.get("DATAFORGE_BATCH_THRESHOLD", "32")))
    except ValueError as exc:
        raise ValueError("DATAFORGE_BATCH_THRESHOLD 必须是整数") from exc
    return "batch" if record_count >= threshold else "single"


def _documents_for_versions(objects, versions: list[SourceVersion], sources: dict[str, Source]) -> list[dict[str, Any]]:
    documents: list[dict[str, Any]] = []
    profile = os.getenv("DATAFORGE_PARSER_PROFILE", "auto")
    if profile not in {"auto", "api", "local", "flash"}:
        raise ValueError("DATAFORGE_PARSER_PROFILE 仅支持 auto、api、local 或 flash")
    for version in versions:
        source = sources[version.source_id]
        text = _extract_text(source.original_filename, objects.get_bytes(version.object_key))
        documents.append({"source_id": source.id, "source_version_id": version.id, "filename": source.original_filename,
                          "text": text, "parser_strategy": "auto", "runtime_profile": profile,
                          "parser_adapter": select_parser_adapter(source.original_filename, profile),
                          "anchor": {"file": source.original_filename, "page": None, "section": None}})
    return documents


def _run_operator(ref: str, params: dict[str, Any], values: list[dict[str, Any]], *, root_documents: list[dict[str, Any]], sources: dict[str, Source], versions: dict[str, SourceVersion], type_contracts: dict[str, dict[str, Any]], job_id: str | None = None, store: V7Store | None = None, retry_scope: set[tuple[str, str, str]] | None = None, generation: dict[str, dict[str, list[dict[str, Any]]]] | None = None) -> list[dict[str, Any]]:
    """Execute only DataForge adapters; DataFlow class names never enter a Flow."""
    if ref == "document-parser":
        return root_documents
    if ref in {"document-ir-normalizer", "null-filter", "language-filter", "text-cleaner", "whitespace-cleaner", "text-normalizer", "pii-compliance"}:
        result = []
        mode = select_runtime_mode(len(values))
        for value in values:
            text = str(value.get("text", ""))
            if ref == "null-filter" and not text.strip():
                continue
            value = dict(value)
            if ref in {"text-cleaner", "whitespace-cleaner", "text-normalizer"}:
                value["text"] = re.sub(r"\s+", " ", text).strip()
            if ref == "text-cleaner":
                value["runtime_mode"] = mode
            result.append(value)
        return result
    if ref == "semantic-chunker":
        size = int(params.get("chunk_size", 800))
        if not 100 <= size <= 4000:
            raise ValueError("Semantic Chunker 的 chunk_size 必须是 100–4000")
        result = []; mode = select_runtime_mode(len(values))
        for document in values:
            for index, chunk in enumerate(_chunks(str(document.get("text", "")), size)):
                result.append({"source_id": document["source_id"], "source_version_id": document["source_version_id"],
                               "filename": document["filename"], "content": chunk, "chunk_index": index,
                               "runtime_mode": mode, "anchor": {**document.get("anchor", {}), "chunk_index": index}})
        return result
    if ref == "source-chunk-builder":
        # This is the formal provenance boundary.  Any further internal LLM
        # context-window splitting remains an execution artifact only.
        return [{**value, "source_chunk_id": hashlib.sha256(f"{value['source_version_id']}:{value['chunk_index']}".encode("utf-8")).hexdigest()} for value in values]
    if ref in {"prompt-generator", "qa-generator", "graph-extractor", "structured-knowledge-generator", "multihop-qa",
               "triple-builder", "evidence-binder"}:
        kind = str(params.get("knowledge_type", ""))
        if ref == "qa-generator": kind = "qa"
        if ref == "graph-extractor": kind = "graph"
        mode = str(params.get("graph_mode") or "")
        output_key = f"graph:{mode}" if kind == "graph" and mode else kind
        if not kind:
            raise ValueError("知识生成节点缺少 knowledge_type")
        contract = type_contracts.get(output_key) or type_contracts.get(kind)
        if not contract and kind != "text":
            raise ValueError(f"知识类型 {kind} 缺少已发布契约")
        outcome = (generation if generation is not None else {}).setdefault(output_key, {"successful": [], "failed": [], "targeted": []})
        result: list[dict[str, Any]] = []
        for chunk in values:
            scope_key = (output_key, str(chunk["source_version_id"]), str(chunk["source_chunk_id"]))
            if retry_scope is not None and scope_key not in retry_scope:
                continue
            outcome["targeted"].append(chunk)
            try:
                candidates = _candidates(sources[chunk["source_id"]], versions[chunk["source_version_id"]], output_key, chunk, contract=contract)
                outcome["successful"].append(chunk)
                result.extend(candidates)
                if store and job_id:
                    store.record_chunk_generation(job_id, output_key, chunk, status="completed", candidate_count=len(candidates))
            except Exception as exc:
                outcome["failed"].append({**chunk, "error": str(exc)})
                if store and job_id:
                    store.record_chunk_generation(job_id, output_key, chunk, status="failed", error=str(exc))
        return result
    if ref in {"entity-extractor", "relation-extractor", "entity-normalizer", "semantic-relation-builder"}:
        return [dict(value) for value in values]
    if ref == "artifact-merge":
        return [dict(value) for value in values]
    if ref == "quality-evaluator":
        return [{**value, "quality_score": float(value.get("quality_score", 1.0)), "quality_status": "pass"} for value in values]
    if ref == "quality-filter":
        result = []
        for value in values:
            score = float(value.get("quality_score", 1.0))
            if score < 0.6:
                continue
            result.append({**value, "quality_status": "pass" if score >= 0.8 else "review"})
        return result
    if ref in {"source-binding", "schema-validator", "knowledge-diff", "deduplicate", "prompted-refiner"}:
        # Candidate-only operators deliberately never mutate published items.
        if ref == "deduplicate":
            unique: dict[str, dict[str, Any]] = {}
            for value in values:
                unique.setdefault(str(value.get("source_knowledge_id")), value)
            return list(unique.values())
        return [dict(value) for value in values]
    raise ValueError(f"Runner 没有批准的算子 Adapter：{ref}")


def execute_job(store: V7Store, objects, job_id: str) -> dict:
    with store.sessions() as session:
        job = session.get(KnowledgeJob, job_id)
        if not job:
            raise ValueError("知识任务不存在")
        if job.status not in {"running", "queued"}:
            return {"id": job.id, "status": job.status, "idempotent": True}
        versions_list = session.scalars(select(SourceVersion).where(SourceVersion.id.in_(job.source_version_ids))).all()
        versions = {version.id: version for version in versions_list}
        sources = {source.id: source for source in session.scalars(select(Source).where(Source.id.in_([version.source_id for version in versions_list])))}
        sink_libraries = dict(job.sink_library_ids or job.output_library_ids)
    retry_scope = store.retry_chunk_scope(job_id)
    flow_run = store.start_flow_run(job_id)
    outputs: dict[str, list[dict[str, Any]]] = {}
    artifact_ids: dict[str, list[str]] = {}
    changes: dict[str, dict[str, int]] = {}
    sink_errors: dict[str, str] = {}
    node_errors: dict[str, str] = {}
    generation: dict[str, dict[str, list[dict[str, Any]]]] = {}
    current_source_chunks: list[dict[str, Any]] = []
    try:
        definition = store.template_definition_for_job(job_id)
        type_contracts = store.type_contracts_for_job(job_id)
        incoming = _incoming(definition)
        root_documents = _documents_for_versions(objects, versions_list, sources)
        sink_nodes: list[dict[str, Any]] = []
        for node in definition.get("nodes", []):
            if store.is_job_cancelled(job_id):
                store.finish_flow_run(flow_run["id"], "cancelled")
                return {"id": job_id, "status": "cancelled", "flow_run_id": flow_run["id"]}
            node_id = node["id"]
            source_nodes = incoming.get(node_id, [])
            input_values = [value for source_id in source_nodes for value in outputs.get(source_id, [])]
            input_ids = [artifact_id for source_id in source_nodes for artifact_id in artifact_ids.get(source_id, [])]
            if node.get("kind") == "knowledge_sink":
                sink_nodes.append(node)
                continue
            ref = str(node.get("ref"))
            failed_upstream = [source_id for source_id in source_nodes if source_id in node_errors]
            if failed_upstream:
                message = "上游节点失败，已跳过：" + "、".join(failed_upstream)
                node_errors[node_id] = message; outputs[node_id] = []
                artifact_ids[node_id] = store.record_flow_node(flow_run["id"], node_id, input_ids, [], error=message)
                continue
            try:
                values = _run_operator(ref, dict(node.get("params") or {}), input_values,
                                        root_documents=root_documents, sources=sources, versions=versions,
                                        type_contracts=type_contracts, job_id=job_id, store=store,
                                        retry_scope=retry_scope, generation=generation)
            except Exception as exc:
                node_errors[node_id] = str(exc); outputs[node_id] = []
                artifact_ids[node_id] = store.record_flow_node(flow_run["id"], node_id, input_ids, [], error=str(exc))
                continue
            if ref == "document-parser":
                store.record_document_irs(flow_run["id"], values)
            elif ref == "source-chunk-builder":
                store.record_source_chunks(flow_run["id"], values)
                current_source_chunks = [dict(value) for value in values]
            outputs[node_id] = values
            artifact_type = "execution"
            if values:
                values = [{**value, "_artifact_type": artifact_type} for value in values]
                outputs[node_id] = values
            artifact_ids[node_id] = store.record_flow_node(flow_run["id"], node_id, input_ids, values)
        # All generation gates finish before any Knowledge Sink writes.  A
        # failed chunk stays out of a Sink's replacement range, so successful
        # neighbouring chunks can publish without erasing it.
        for node in sink_nodes:
            node_id = node["id"]
            source_nodes = incoming.get(node_id, [])
            input_values = [value for source_id in source_nodes for value in outputs.get(source_id, [])]
            input_ids = [artifact_id for source_id in source_nodes for artifact_id in artifact_ids.get(source_id, [])]
            knowledge_type = node["knowledge_type"]
            output_key = str(node.get("output_key") or (f"graph:{node.get('graph_mode')}" if knowledge_type == "graph" and node.get("graph_mode") else knowledge_type))
            try:
                failed_upstream = [source_id for source_id in source_nodes if source_id in node_errors]
                if failed_upstream:
                    raise ValueError("上游节点失败，Sink 已跳过：" + "、".join(failed_upstream))
                outcomes = generation.get(output_key, {"successful": [], "failed": [], "targeted": []})
                successful = outcomes["successful"]
                if successful:
                    changes[output_key] = store.apply_knowledge_output(
                        job_id, output_key, input_values, successful_chunks=successful,
                    )
                else:
                    changes[output_key] = {"ADD": 0, "UPDATE": 0, "INACTIVE": 0, "UNCHANGED": 0}
                outputs[node_id] = [{"_artifact_type": f"knowledge_item:{output_key}", "knowledge_type": knowledge_type, "graph_mode": node.get("graph_mode"), "change": changes[output_key]}]
                artifact_ids[node_id] = store.record_flow_node(flow_run["id"], node_id, input_ids, outputs[node_id])
            except Exception as exc:
                sink_errors[output_key] = str(exc)
                outputs[node_id] = []
                artifact_ids[node_id] = store.record_flow_node(flow_run["id"], node_id, input_ids, [], error=str(exc))
        missing_generation = set(sink_libraries) - set(generation)
        for knowledge_type in missing_generation:
            sink_errors[knowledge_type] = "流程没有执行该知识类型的生成节点"
        successful_chunks = [entry for outcome in generation.values() for entry in outcome["successful"]]
        if not successful_chunks:
            detail = "; ".join(f"{kind}: {error}" for kind, error in sink_errors.items())
            if not detail:
                detail = "所有知识生成分块均失败"
            store.finish_flow_run(flow_run["id"], detail)
            store.mark_job_failed(job_id, detail)
            return {"id": job_id, "status": "failed", "flow_run_id": flow_run["id"], "changes": changes, "sink_errors": sink_errors}
        warnings = [{"knowledge_type": kind, "source_version_id": item["source_version_id"], "source_chunk_id": item["source_chunk_id"], "chunk_index": item["chunk_index"], "error": item["error"]} for kind, outcome in generation.items() for item in outcome["failed"]]
        warnings.extend({"node_id": node_id, "error": message} for node_id, message in node_errors.items())
        warnings.extend({"output_key": key, "error": message} for key, message in sink_errors.items())
        # Only after every sink has applied its successful chunk range may an
        # older missing chunk be withdrawn.  A failure in any target type keeps
        # the whole source-version history, including on a failed-only retry.
        if not warnings:
            cleanup_source_versions = store.completed_source_versions_for_cleanup(
                job_id, set(sink_libraries), current_source_chunks,
            )
            if cleanup_source_versions:
                for knowledge_type in sink_libraries:
                    store.apply_knowledge_output(
                        job_id, knowledge_type, [], successful_chunks=current_source_chunks,
                        replace_absent_source_versions=cleanup_source_versions,
                        cleanup_only=True,
                    )
        store.finish_flow_run(flow_run["id"], "部分分块生成失败" if warnings else None,
                              status="completed_with_warnings" if warnings else "completed")
        store.complete_job(job_id, warnings=warnings)
        vector_jobs = {library_id: store.create_vector_sync_jobs(library_id) for library_id in sink_libraries.values()}
        return {"id": job_id, "status": "completed_with_warnings" if warnings else "completed", "flow_run_id": flow_run["id"], "changes": changes, "warnings": warnings, "vector_sync_jobs": vector_jobs}
    except Exception as exc:
        store.mark_source_versions_failed([item.id for item in versions_list], str(exc))
        store.finish_flow_run(flow_run["id"], str(exc))
        store.mark_job_failed(job_id, str(exc))
        raise


def create_app(settings: Settings | None = None, *, check_schema: bool = True) -> FastAPI:
    resolved = settings or Settings.load(); resolved.ensure_directories()
    runtime = dataflow_runtime_status()
    if not runtime["compatible"]:
        raise RuntimeError(f"Runner 必须安装 open-dataflow=={DATAFLOW_VERSION}，当前为 {runtime['installed']}")
    _initialize_global_llm()
    store = V7Store(resolved.platform_database_url)
    if check_schema: store.assert_schema_current()
    objects = _objects(resolved); app = FastAPI(title="DataForge V7 Runner", version="7.0.0")

    def verify(authorization: str | None) -> None:
        expected = f"Bearer {resolved.runner_service_token}" if resolved.runner_service_token else None
        if expected and authorization != expected: raise HTTPException(status_code=403, detail="Runner 服务凭据无效")

    @app.post("/internal/jobs", status_code=202)
    def run(payload: RunRequest, authorization: str | None = Header(None)):
        verify(authorization)
        try: return execute_job(store, objects, payload.job_id)
        except ValueError as exc: raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/internal/runtime")
    def runtime_status(authorization: str | None = Header(None)):
        verify(authorization)
        return runtime
    return app


app = FastAPI(title="DataForge V7 Runner bootstrap")


def main() -> None:
    import uvicorn
    uvicorn.run(create_app(check_schema=True), host="0.0.0.0", port=int(os.getenv("DATAFORGE_RUNNER_PORT", "8010")))
