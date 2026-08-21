from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from .application import DataForge
from .errors import ValidationError
from .processing.native import split_text


KNOWLEDGE_TYPES = [
    {
        "id": "text_chunk",
        "name": "文本知识库",
        "description": "把长文档整理成可搜索、可引用的文本片段。",
        "schema": {
            "type": "object",
            "required": ["content", "chunk_index"],
            "properties": {"content": "string", "chunk_index": "integer"},
        },
    },
    {
        "id": "faq",
        "name": "问答知识库",
        "description": "从文档中整理常见问题和对应答案。",
        "schema": {
            "type": "object",
            "required": ["question", "answer"],
            "properties": {"question": "string", "answer": "string"},
        },
    },
    {
        "id": "knowledge_triple",
        "name": "知识图谱",
        "description": "提取实体以及实体之间的关系。",
        "schema": {
            "type": "object",
            "required": ["subject", "predicate", "object"],
            "properties": {"subject": "string", "predicate": "string", "object": "string"},
        },
    },
    {
        "id": "multi_turn_dialogue",
        "name": "多轮对话库",
        "description": "把内容整理成有上下文的连续对话。",
        "schema": {
            "type": "object",
            "required": ["messages"],
            "properties": {"messages": "array"},
        },
    },
]


STANDARD_PIPELINES = [
    {
        "id": "std-text-chunk-v1",
        "name": "文档文本分块流程",
        "knowledge_type_id": "text_chunk",
        "pipeline_ref": "medical-document-v1",
        "engine": "dataflow",
        "version": 1,
        "description": "文本标准化、分块和去重；输出已经通过文本块格式验证。",
        "validation_status": "validated",
    },
    {
        "id": "std-faq-text2qa-v1",
        "name": "文档转标准问答流程",
        "knowledge_type_id": "faq",
        "pipeline_ref": "Text2Qa Pipeline",
        "engine": "dataflow-studio",
        "version": 1,
        "description": "来自 DataFlow 的 Text2Qa 流程；完成算力配置和样本验证后启用。",
        "validation_status": "configured",
    },
    {
        "id": "std-dialogue-synthesis-v1",
        "name": "多轮对话生成流程",
        "knowledge_type_id": "multi_turn_dialogue",
        "pipeline_ref": "Text Conversation Synthesis Pipeline",
        "engine": "dataflow-studio",
        "version": 1,
        "description": "来自 DataFlow 的多轮对话流程；完成样本格式验证后启用。",
        "validation_status": "configured",
    },
]


class KnowledgeService:
    def __init__(self, dataforge: DataForge):
        self.dataforge = dataforge
        self.studio: Any | None = None

    def seed(self) -> None:
        store = self.dataforge.store
        schemas: dict[str, dict[str, Any]] = {}
        for item in KNOWLEDGE_TYPES:
            store.register_knowledge_type(item["id"], item["name"], item["description"], item["schema"])
            schemas[item["id"]] = item["schema"]
        for item in STANDARD_PIPELINES:
            store.register_standard_pipeline(
                item["id"],
                item["name"],
                item["knowledge_type_id"],
                item["pipeline_ref"],
                item["engine"],
                item["version"],
                item["description"],
                schemas[item["knowledge_type_id"]],
                item["validation_status"],
                item["id"] == "std-text-chunk-v1",
            )

    def create_job(
        self,
        *,
        name: str,
        knowledge_type_id: str,
        standard_pipeline_id: str | None,
        source_version_ids: list[str],
    ) -> dict[str, Any]:
        if not name.strip():
            raise ValidationError("请填写知识库名称")
        if not source_version_ids:
            raise ValidationError("请至少选择一个源文件版本")
        self.dataforge.store.get_knowledge_type(knowledge_type_id)
        pipeline = (
            self.dataforge.store.get_standard_pipeline(standard_pipeline_id)
            if standard_pipeline_id
            else self.dataforge.store.get_default_standard_pipeline(knowledge_type_id)
        )
        if pipeline["knowledge_type_id"] != knowledge_type_id:
            raise ValidationError("所选标准流程与知识库类型不兼容")
        if pipeline["validation_status"] != "validated" or not pipeline["active"]:
            raise ValidationError("该标准流程尚未通过输出格式验证，不能用于正式加工")
        for version_id in dict.fromkeys(source_version_ids):
            self.dataforge.store.get_source_version(version_id)
        return self.dataforge.store.create_knowledge_job(
            name.strip(), knowledge_type_id, pipeline["id"], list(dict.fromkeys(source_version_ids))
        )

    def execute_job(self, job_id: str) -> dict[str, Any]:
        store = self.dataforge.store
        job = store.get_knowledge_job(job_id)
        pipeline = store.get_standard_pipeline(job["standard_pipeline_id"])
        source_ids = job["source_version_ids"]
        store.update_knowledge_job(job_id, status="running", progress=5)
        try:
            results = []
            with ThreadPoolExecutor(max_workers=min(4, len(source_ids))) as executor:
                if pipeline["pipeline_ref"] == "medical-document-v1":
                    engine = "dataflow" if self.dataforge.settings.dataflow_path else "native"
                    futures = {
                        executor.submit(
                            self.dataforge.run,
                            version_id,
                            pipeline_id=pipeline["pipeline_ref"],
                            engine_override=engine,
                        ): version_id
                        for version_id in source_ids
                    }
                elif pipeline["pipeline_ref"].startswith("studio:") and self.studio:
                    upstream_id = pipeline["pipeline_ref"].removeprefix("studio:")
                    futures = {
                        executor.submit(
                            self.studio.run_pipeline_for_source,
                            self.dataforge,
                            version_id,
                            upstream_id,
                        ): version_id
                        for version_id in source_ids
                    }
                else:
                    raise ValidationError("该标准流程缺少可执行的 DataFlow 流程版本")
                for completed, future in enumerate(as_completed(futures), start=1):
                    results.append(future.result())
                    progress = 5 + int(completed / len(futures) * 70)
                    store.update_knowledge_job(job_id, status="running", progress=progress)

            records: list[dict[str, Any]] = []
            validation_errors: list[dict[str, Any]] = []
            input_cache: dict[str, dict[int, str]] = {}
            for result in results:
                if isinstance(result, dict):
                    output = Path(result["output_file"])
                    source_version = result["source_version"]
                    run_id = None
                    asset_version_id = None
                    input_file = Path(result["input_file"])
                    dataflow_task_id = result["task_id"]
                else:
                    output = self.dataforge.blobs.resolve(result.asset_version["blob_uri"])
                    source_version = result.source_version
                    run_id = result.run["id"]
                    asset_version_id = result.asset_version["id"]
                    input_file = Path(result.run["work_dir"]) / "input" / "source_records.jsonl"
                    dataflow_task_id = None
                raw_records = input_cache.setdefault(str(input_file), _read_source_records(input_file))
                with output.open(encoding="utf-8") as handle:
                    for line_number, line in enumerate(handle, start=1):
                        if not line.strip():
                            continue
                        data = json.loads(line)
                        errors = validate_record(data, pipeline["output_schema"])
                        if errors:
                            validation_errors.append(
                                {
                                    "source_version_id": source_version["id"],
                                    "line": line_number,
                                    "errors": errors,
                                }
                            )
                            continue
                        records.append(
                            {
                                "source_version_id": source_version["id"],
                                "run_id": run_id,
                                "asset_version_id": asset_version_id,
                                "source_locator": {
                                    "source_record_index": data.get("source_record_index"),
                                    "chunk_index": data.get("chunk_index"),
                                    "document_id": data.get("document_id"),
                                    "dataflow_task_id": dataflow_task_id,
                                    "source_excerpt": _source_excerpt(raw_records, data),
                                },
                                "data": data,
                            }
                        )

            validation = {
                "passed": not validation_errors,
                "checked_records": len(records) + len(validation_errors),
                "valid_records": len(records),
                "invalid_records": len(validation_errors),
                "errors": validation_errors[:20],
            }
            store.update_knowledge_job(job_id, status="running", progress=85, validation=validation)
            if validation_errors:
                raise ValidationError(f"输出格式验证失败，共 {len(validation_errors)} 条数据不符合知识库格式")
            if not records:
                raise ValidationError("处理结果为空，未生成知识资产")

            knowledge_base = store.create_knowledge_base(
                job["name"], job["knowledge_type_id"], job["standard_pipeline_id"], job_id, records
            )
            return store.update_knowledge_job(
                job_id,
                status="completed",
                progress=100,
                validation=validation,
                knowledge_base_id=knowledge_base["id"],
            )
        except Exception as exc:
            return store.update_knowledge_job(job_id, status="failed", error=str(exc))

    def get_record_lineage(self, record_id: str) -> dict[str, Any]:
        lineage = self.dataforge.store.get_knowledge_record_lineage(record_id)
        locator = lineage.get("source_locator") or {}
        if locator.get("source_excerpt"):
            return lineage
        run_id = lineage.get("run_id")
        if not run_id:
            return lineage
        run = self.dataforge.store.get_run(run_id)
        input_file = Path(run["work_dir"]) / "input" / "source_records.jsonl"
        locator["source_excerpt"] = _source_excerpt(_read_source_records(input_file), lineage.get("data") or {})
        lineage["source_locator"] = locator
        return lineage


def _read_source_records(path: Path) -> dict[int, str]:
    if not path.is_file():
        return {}
    result: dict[int, str] = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            item = json.loads(line)
            result[int(item.get("source_record_index", len(result)))] = str(item.get("raw_content") or "")
    return result


def _source_excerpt(source_records: dict[int, str], record: dict[str, Any]) -> str:
    source_index = int(record.get("source_record_index") or 0)
    raw = source_records.get(source_index, "")
    if not raw:
        return ""
    chunk_index = int(record.get("chunk_index") or 0)
    target = str(record.get("content") or "")
    chunk_size = max(600, len(target)) if target else 600
    chunks = split_text(raw, chunk_size, min(80, max(0, chunk_size - 1)))
    if chunk_index < len(chunks):
        return chunks[chunk_index]
    return raw[: max(800, len(target))]


def validate_record(record: Any, schema: dict[str, Any]) -> list[str]:
    if not isinstance(record, dict):
        return ["数据必须是对象"]
    errors: list[str] = []
    properties = schema.get("properties", {})
    for field in schema.get("required", []):
        if field not in record or record[field] is None:
            errors.append(f"缺少字段：{field}")
            continue
        expected = properties.get(field)
        value = record[field]
        matches = {
            "string": isinstance(value, str),
            "integer": isinstance(value, int) and not isinstance(value, bool),
            "array": isinstance(value, list),
            "object": isinstance(value, dict),
        }.get(expected, True)
        if not matches:
            errors.append(f"字段 {field} 应为 {expected}")
    return errors
