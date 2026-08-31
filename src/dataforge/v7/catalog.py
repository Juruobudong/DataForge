"""Stable DataForge knowledge-production catalog.

The catalog deliberately exposes logical DataForge operators instead of the full
DataFlow registry.  Adapter/runtime changes are recorded on OperatorVersion and
never leak into a published Flow definition.
"""
from __future__ import annotations

from typing import Any
from copy import deepcopy

from .llm_serving import DEFAULT_LLM_SERVING_ID

DATAFORGE_CATEGORIES = {
    "content-processing", "knowledge-generation", "quality-processing", "index-processing",
}
DATAFLOW_CATEGORIES = {"text-cleaning", "content-filtering", "deduplication", "text-generation"}
DEFAULT_QA_EXTRACTION_INSTRUCTIONS = (
    "仅基于审核通过的原文生成问答。"
    "问题应清晰、具体且可独立理解，避免依赖上下文的模糊指代。"
    "答案必须能够从原文直接得到或由原文明确归纳，不使用外部知识，不猜测或补充原文未提供的信息。"
    "优先提取定义、事实、条件、步骤、规则、结论和数值等具有明确答案的知识。"
    "答案保持原文语言，简洁但信息完整；文本不足以形成可靠问答时不生成。"
)
QA_EXTRACTION_SCHEMA = {
    "type": "string", "title": "QA 提取要求", "default": DEFAULT_QA_EXTRACTION_INSTRUCTIONS,
    "description": "填写提取主题、对象、问法和答案要求；空白使用通用提取规则。无匹配内容时正常产出零条问答。",
    "x-dataforge-ui": {"widget": "textarea"},
}


DEFAULT_CHUNKER_PARAMS: dict[str, Any] = {
    "chunk_size": 800,
    "overlap_percent": 10,
    "delimiters": ["\n\n", "\n", "。", "！", "？", "；"],
    "min_chunk_size": 100,
    "preserve_page_boundary": True,
    "include_heading": True,
}


def normalize_chunker_params(value: dict[str, Any] | None) -> dict[str, Any]:
    params = {**DEFAULT_CHUNKER_PARAMS, **dict(value or {})}
    integer_fields = ("chunk_size", "overlap_percent", "min_chunk_size")
    if any(not isinstance(params[name], int) or isinstance(params[name], bool) for name in integer_fields):
        raise ValueError("Chunker 的大小、Overlap 与最小块必须是整数")
    if not 100 <= params["chunk_size"] <= 4000:
        raise ValueError("chunk_size 必须是 100–4000")
    if not 0 <= params["overlap_percent"] <= 50:
        raise ValueError("overlap_percent 必须是 0–50")
    if not 1 <= params["min_chunk_size"] <= params["chunk_size"]:
        raise ValueError("min_chunk_size 必须介于 1 与 chunk_size 之间")
    delimiters = params.get("delimiters")
    if not isinstance(delimiters, list) or not delimiters or any(not isinstance(item, str) or not item for item in delimiters):
        raise ValueError("delimiters 必须是非空字符串数组")
    if not isinstance(params.get("preserve_page_boundary"), bool) or not isinstance(params.get("include_heading"), bool):
        raise ValueError("页边界和标题上下文参数必须是布尔值")
    params["delimiters"] = list(dict.fromkeys(delimiters))
    return params


OPERATOR_DESCRIPTIONS: dict[str, str] = {
    "document-parser": "将源文件解析为统一文档结构",
    "document-ir-normalizer": "统一文档结构与基础元数据",
    "null-filter": "过滤缺少有效正文的文档",
    "language-filter": "按允许语言筛选文档内容",
    "text-cleaner": "清理文本噪声与异常内容",
    "whitespace-cleaner": "合并多余空白与换行",
    "text-normalizer": "规范字符、标点与文本格式",
    "semantic-chunker": "按语义边界将文档切分为文本块",
    "source-chunk-builder": "生成可追溯的正式来源文本块",
    "reviewed-source-chunk-input": "读取人工审核快照中冻结的来源文本块",
    "faq-table-row-builder": "将单机构 FAQ 表格逐行规范为来源切片",
    "faq-record-mapper": "将规范 FAQ 行确定性映射为专用知识",
    "text-knowledge-mapper": "将已审核来源切片确定性映射为文本候选知识",
    "HashDeduplicateFilter": "按知识身份精确移除重复候选",
    "MinHashDeduplicateFilter": "在同一来源块内按文本相似度移除重复候选",
    "prompt-generator": "按受控提示生成结构化知识",
    "Text2QAGenerator": "使用 DataFlow 两阶段流程从来源文本块生成问答知识",
    "qa-extractor": "基于业务要求从审核原文直接提取完整问答；DataForge 原生单阶段生成",
    "graph-extractor": "从来源文本块提取图谱知识",
    "entity-extractor": "识别文本中的实体候选",
    "entity-relation-extractor": "一次模型调用联合抽取实体和关系；每块最多一次完整结果修复，失败整块隔离",
    "relation-extractor": "识别实体之间的关系候选",
    "triple-builder": "构造主谓宾三元组知识",
    "entity-normalizer": "合并并规范同义实体",
    "semantic-relation-builder": "构造带语义描述的实体关系",
    "evidence-binder": "为语义关系绑定来源证据",
    "literal-detector": "识别并规范化数字、范围、剂量等字面值",
    "graph-quality-validator": "在正式发布前校验图谱实体、关系与字面值质量",
    "artifact-merge": "合并多个上游候选结果",
    "structured-knowledge-generator": "按知识类型契约生成结构化知识",
    "schema-validator": "校验候选知识是否符合 Schema",
    "mineru-pipeline-gpu-adapter": "通过 MinerU GPU 服务解析 PDF",
    "kbc-cleaner-batch": "批量执行文本清理",
    "kbc-chunker-batch": "批量执行语义切片",
    "PromptedRefiner": "使用受控提示修订候选知识",
    "multihop-qa": "生成需要多步推理的问答知识",
    "pii-compliance": "识别并处理个人敏感信息",
}

OPERATOR_CATEGORIES: tuple[str, ...] = (
    "content-processing", "knowledge-generation", "quality-processing", "index-processing",
    "text-cleaning", "content-filtering", "deduplication", "text-generation",
)

OPERATOR_DISPLAY_NAMES_ZH: dict[str, str] = {
    "document-parser": "文档解析器", "document-ir-normalizer": "文档结构规范器", "null-filter": "空内容过滤器",
    "language-filter": "语言过滤器", "text-cleaner": "文本清洗器", "whitespace-cleaner": "空白清理器",
    "text-normalizer": "文本规范器", "semantic-chunker": "结构化分块器", "source-chunk-builder": "来源切片构建器",
    "reviewed-source-chunk-input": "已审核来源切片",
    "text-knowledge-mapper": "文本知识映射器",
    "qa-extractor": "问答提取器",
    "HashDeduplicateFilter": "哈希去重过滤器", "MinHashDeduplicateFilter": "MinHash 相似去重过滤器",
    "prompt-generator": "提示词生成器", "Text2QAGenerator": "QA 生成器",
    "graph-extractor": "图谱抽取器", "entity-extractor": "实体抽取器", "relation-extractor": "关系抽取器",
    "entity-relation-extractor": "实体关系联合抽取器",
    "triple-builder": "三元组构建器", "entity-normalizer": "实体规范器", "semantic-relation-builder": "语义关系构建器",
    "evidence-binder": "证据绑定器", "literal-detector": "字面值识别器", "graph-quality-validator": "图谱质量校验器", "artifact-merge": "产物合并器", "structured-knowledge-generator": "结构化知识生成器",
    "schema-validator": "结构校验器", "mineru-pipeline-gpu-adapter": "MinerU GPU 解析适配器",
    "kbc-cleaner-batch": "批量文本清洗器", "kbc-chunker-batch": "批量语义切片器", "PromptedRefiner": "提示词修订器",
    "multihop-qa": "多跳问答生成器", "pii-compliance": "敏感信息合规器", "faq-table-row-builder": "FAQ 表格行构建器",
    "faq-record-mapper": "FAQ 知识映射器",
}

SUBFLOW_DISPLAY_NAMES_ZH: dict[str, str] = {
    "document-parse": "文档解析",
    "document-clean": "文档清洗",
    "knowledge-chunk": "知识切分",
}


def _catalog_category(code: str, previous: str) -> tuple[str, str]:
    if code == "entity-relation-extractor": return "知识生成", "图谱"
    if code == "document-parser": return "文档输入", "文档解析"
    if code in {"semantic-chunker", "source-chunk-builder", "kbc-chunker-batch"}: return "知识切分", previous
    if code in {"Text2QAGenerator", "graph-extractor", "entity-extractor", "relation-extractor", "triple-builder", "entity-normalizer", "semantic-relation-builder", "evidence-binder", "structured-knowledge-generator", "multihop-qa", "literal-detector"}: return "知识生成", previous
    if code in {"prompt-generator", "PromptedRefiner"}: return "LLM 处理", previous
    if code in {"HashDeduplicateFilter", "MinHashDeduplicateFilter", "schema-validator", "graph-quality-validator"}: return "质量治理", previous
    if code == "artifact-merge": return "流程控制", previous
    return "内容处理", previous


def _knowledge_types(source: str, target: str) -> list[str]:
    if target == "candidate:text": return ["text"]
    contract = f"{source}|{target}"
    if "qa-agent-faq" in contract: return ["qa-agent-faq"]
    if "qa" in contract: return ["qa"]
    if any(value in contract for value in ("graph", "entity", "relation")): return ["graph"]
    return ["text", "qa", "graph"]


def _artifact_example(artifact_type: str) -> dict[str, Any]:
    if artifact_type == "source_file":
        return {"filename": "临床指南.md", "content_type": "text/markdown"}
    if artifact_type == "document_ir":
        return {"filename": "临床指南.md", "text": "高血压患者应规范随访。", "anchor": {"page": 1}}
    if artifact_type in {"chunk_set", "source_chunk_set"}:
        value = {"content": "高血压患者应规范随访。", "chunk_index": 0, "anchor": {"page": 1}}
        if artifact_type == "source_chunk_set":
            value["source_chunk_id"] = "chunk-example-001"
        return value
    if artifact_type == "entity_candidate_set":
        return {"entities": [{"name": "高血压", "type": "疾病"}], "source_chunk_id": "chunk-example-001"}
    if artifact_type == "relation_candidate_set":
        return {"source": "高血压", "relation": "需要", "target": "规范随访", "source_chunk_id": "chunk-example-001"}
    if artifact_type == "semantic_relation_set":
        return {"source_entity": "高血压", "relation": "患者需要规范随访", "target_entity": "规范随访"}
    if artifact_type.startswith("candidate:qa"):
        return {"question": "高血压患者需要什么？", "answer": "需要规范随访。", "source_chunk_id": "chunk-example-001"}
    if artifact_type.startswith("candidate:graph:semantic"):
        return {"source_entity": {"name": "高血压"}, "relation": {"description": "需要"}, "target_entity": {"name": "规范随访"}, "evidence": ["高血压患者应规范随访。"]}
    if artifact_type.startswith("candidate:graph"):
        return {"subject": "高血压", "predicate": "需要", "object": "规范随访", "source_chunk_id": "chunk-example-001"}
    if artifact_type.startswith("candidate:"):
        return {"canonical_content": "高血压患者应规范随访。", "source_chunk_id": "chunk-example-001"}
    return {"value": "示例数据"}


def _entry(code: str, name: str, category: str, input_type: str, target: str, adapter: str, *, exposure: str = "canvas", risk: str = "standard", upstream: list[str] | None = None, input_cardinality: str = "one", input_binding: str = "edge", node_role: str = "operator", uses_llm: bool = False, extra_params: dict[str, dict[str, Any]] | None = None, version: int | None = None) -> dict[str, Any]:
    primary_category, subcategory = _catalog_category(code, category)
    parameter_schema: dict[str, Any] = {"type": "object", "additionalProperties": False}
    parameter_docs: dict[str, str] = {"_overview": "此版本没有面向画布的业务可配置参数；运行时内部配置不会返回前端。"}
    properties: dict[str, Any] = {}
    required: list[str] = []
    if uses_llm:
        properties["llm_serving"] = {
            "type": "string",
            "title": "模型服务",
            "default": DEFAULT_LLM_SERVING_ID,
            "description": "已配置的 Model Serving ID，不是模型名称或 URL",
            "x-dataforge-ui": {"widget": "llm-serving-selector"},
        }
        required.append("llm_serving")
        parameter_docs["_overview"] = "选择 DataForge 已配置的 Model Serving；连接信息和密钥不会暴露到 Flow。"
        parameter_docs["llm_serving"] = "Model Serving ID。当前默认 qwen3_32b；未来由 Serving 选择器提供候选。"
    if extra_params:
        for key, spec in extra_params.items():
            properties[key] = spec.get("schema", {"type": "string"})
            parameter_docs[key] = spec.get("doc", key)
            if spec.get("required"):
                required.append(key)
    if properties:
        parameter_schema = {"type": "object", "properties": properties, "additionalProperties": False}
        if required:
            parameter_schema["required"] = list(dict.fromkeys(required))
    return {
        "code": code, "name": name, "display_name_zh": OPERATOR_DISPLAY_NAMES_ZH[code],
        "summary": OPERATOR_DESCRIPTIONS[code], "description": OPERATOR_DESCRIPTIONS[code],
        "category": primary_category, "subcategory": subcategory, "input": input_type,
        "source": "dataforge", "catalog_group": "dataforge",
        "output": target, "adapter_code": adapter, "exposure": exposure,
        "risk_level": risk, "upstream": upstream or [], "version": version or (4 if uses_llm else 3),
        "node_role": node_role,
        "scenarios": [f"适用于{OPERATOR_DESCRIPTIONS[code]}的受控知识流程"],
        "knowledge_types": _knowledge_types(input_type, target),
        "recommended_predecessors": [], "recommended_successors": [],
        "lifecycle_status": "deprecated" if exposure == "disabled" else "published",
        "input_ports": {"input": {"artifact_type": input_type, "cardinality": input_cardinality,
                                    "required": True, "binding": input_binding}},
        "output_ports": {"output": {"artifact_type": target, "cardinality": "many",
                                      "required": False, "binding": "edge"}},
        "input_example": {"input": [_artifact_example(input_type)]},
        "output_example": {"output": [_artifact_example(target)]},
        "parameter_schema": parameter_schema,
        "parameter_docs": parameter_docs,
        "runtime_requirements": {"driver": "builtin", "executor": "dataforge-native", "implementation": adapter, "adapter_version": 1, "upstream": upstream or [], "uses_llm": uses_llm},
    }


CATALOG_SEEDS: tuple[dict[str, Any], ...] = (
    _entry("reviewed-source-chunk-input", "Reviewed SourceChunk Input", "文档", "approved_source_chunks", "source_chunk_set", "reviewed_source_chunk_input", input_binding="runtime_input", node_role="flow_input"),
    _entry("document-parser", "Document Parser", "文档", "source_file", "document_ir", "document_parser", input_binding="system_injected", upstream=["DataForgeNativeParser", "MinerUPipelineHTTPAdapter"]),
    _entry("document-ir-normalizer", "Document IR Normalizer", "文档", "document_ir", "document_ir", "document_ir_normalizer"),
    _entry("null-filter", "Null Filter", "清洗", "document_ir", "document_ir", "content_null_filter", upstream=["ContentNullFilter"]),
    _entry("language-filter", "Language Filter", "清洗", "document_ir", "document_ir", "language_filter", upstream=["LanguageFilter"]),
    _entry("text-cleaner", "Text Cleaner", "清洗", "document_ir", "document_ir", "knowledge_text_cleaner", upstream=["KBCTextCleaner"]),
    _entry("whitespace-cleaner", "Whitespace Cleaner", "清洗", "document_ir", "document_ir", "whitespace_cleaner", upstream=["RemoveExtraSpacesRefiner"]),
    _entry("text-normalizer", "Text Normalizer", "清洗", "document_ir", "document_ir", "text_normalizer", upstream=["TextNormalizationRefiner"]),
    _entry("semantic-chunker", "Semantic Chunker", "切片", "document_ir", "chunk_set", "semantic_chunker", upstream=["KBCChunkGenerator"],
           extra_params={
               "chunk_size": {"schema": {"type": "integer", "default": 800, "minimum": 100, "maximum": 4000}, "doc": "目标块大小（字符），不是 Token。"},
               "overlap_percent": {"schema": {"type": "integer", "default": 10, "minimum": 0, "maximum": 50}, "doc": "相邻块按自然边界复用的目标比例。"},
               "delimiters": {"schema": {"type": "array", "items": {"type": "string"}, "minItems": 1, "default": DEFAULT_CHUNKER_PARAMS["delimiters"]}, "doc": "按优先级排列的自然边界。"},
               "min_chunk_size": {"schema": {"type": "integer", "default": 100, "minimum": 1, "maximum": 4000}, "doc": "允许的最小块大小。"},
               "preserve_page_boundary": {"schema": {"type": "boolean", "default": True}, "doc": "开启时禁止 Chunk 和 Overlap 跨页。"},
               "include_heading": {"schema": {"type": "boolean", "default": True}, "doc": "复用 Parser 或 Markdown 明确提供的标题上下文。"},
           }),
    _entry("source-chunk-builder", "Source Chunk Builder", "治理", "chunk_set", "source_chunk_set", "source_chunk_builder"),
    _entry("faq-table-row-builder", "FAQ Table Row Builder", "清洗", "document_ir", "chunk_set", "faq_table_row_builder"),
    _entry("faq-record-mapper", "FAQ Record Mapper", "知识生成", "source_chunk_set", "candidate:qa-agent-faq", "faq_record_mapper"),
    _entry("text-knowledge-mapper", "Text Knowledge Mapper", "内容处理", "source_chunk_set", "candidate:text", "text_knowledge_mapper", uses_llm=False),
    _entry("HashDeduplicateFilter", "HashDeduplicateFilter", "清洗", "candidate:*", "candidate:*", "candidate_deduplicate", upstream=["HashDeduplicateFilter"]),
    _entry("prompt-generator", "Prompt Generator", "知识生成", "source_chunk_set", "candidate:*", "prompt_generator", risk="advanced", upstream=["PromptedGenerator", "ChunkedPromptedGenerator"], uses_llm=True, version=6,
           extra_params={"prompt_template_revision_id": {"schema": {"type": "string", "title": "Prompt 模板", "default": "promptrev_default", "x-dataforge-ui": {"widget": "prompt-template-selector"}}, "doc": "已发布且与当前知识类型匹配的 Prompt Template Revision ID。", "required": True}}),
    _entry("Text2QAGenerator", "Text2QAGenerator", "知识生成", "source_chunk_set", "candidate:qa", "qa_generator", upstream=["Text2QAGenerator"], uses_llm=True),
    _entry("graph-extractor", "Graph Extractor", "知识生成", "source_chunk_set", "candidate:graph", "graph_extractor", uses_llm=True),
    _entry("entity-extractor", "Entity Extractor", "图谱", "source_chunk_set", "entity_candidate_set", "entity_extractor", uses_llm=True, version=6,
           extra_params={
               "entity_types": {"schema": {"type": "array", "title": "实体类型范围", "items": {"type": "string"}, "default": [], "x-dataforge-ui": {"widget": "entity-type-subset"}}, "doc": "引用流程 Graph Schema 中的实体类型；不在节点创建预设包。"},
               "entity_type_scope": {"schema": {"type": "string", "enum": ["all", "subset"], "default": "all", "x-dataforge-ui": {"widget": "hidden"}}, "doc": "all 使用完整流程 Schema；subset 使用 entity_types（空子集不抽取实体）。"},
               "unknown_entity_policy": {"schema": {"type": "string", "title": "未知实体策略", "enum": ["reject", "other", "suggest"], "default": "reject"}, "doc": "未识别实体处理策略。"},
               "generate_description": {"schema": {"type": "boolean", "title": "生成描述", "default": True}, "doc": "是否要求 LLM 为实体生成描述。"},
               "extract_aliases": {"schema": {"type": "boolean", "title": "提取别名", "default": True}, "doc": "是否要求 LLM 抽取实体别名。"},
               "confidence_threshold": {"schema": {"type": "number", "title": "最低置信度", "default": 0.7, "minimum": 0, "maximum": 1}, "doc": "实体置信度阈值。"},
               "prompt_mode": {"schema": {"type": "string", "title": "Prompt 模式", "enum": ["generated", "custom"], "default": "generated"}, "doc": "生成 Prompt 模式。"},
           }),
    _entry("relation-extractor", "Relation Extractor", "图谱", "entity_candidate_set", "relation_candidate_set", "relation_extractor", uses_llm=True, version=5,
           extra_params={
               "relation_types": {"schema": {"type": "array", "title": "关系类型", "items": {"type": "string"}, "default": [], "x-dataforge-ui": {"widget": "tag-select"}}, "doc": "允许抽取的关系类型 code 列表。"},
               "relation_constraints": {"schema": {"type": "array", "title": "关系约束", "default": [], "x-dataforge-ui": {"widget": "relation-constraints"}, "items": {"type": "object", "properties": {"relation_type": {"type": "string"}, "source_types": {"type": "array", "items": {"type": "string"}}, "target_types": {"type": "array", "items": {"type": "string"}}}, "required": ["relation_type", "source_types", "target_types"], "additionalProperties": False}}, "doc": "关系的 source/target 实体类型约束。"},
               "unknown_relation_policy": {"schema": {"type": "string", "title": "未知关系策略", "enum": ["reject", "other", "suggest"], "default": "reject"}, "doc": "未识别关系处理策略。"},
           }),
    _entry("literal-detector", "Literal Detector", "图谱", "entity_candidate_set", "entity_candidate_set", "literal_detector"),
    _entry("triple-builder", "Triple Builder", "图谱", "relation_candidate_set", "candidate:graph:triple", "triple_builder"),
    _entry("entity-normalizer", "Entity Normalizer", "图谱", "entity_candidate_set", "entity_candidate_set", "entity_normalizer"),
    _entry("semantic-relation-builder", "Semantic Relation Builder", "图谱", "relation_candidate_set", "semantic_relation_set", "semantic_relation_builder"),
    _entry("evidence-binder", "Evidence Binder", "图谱", "semantic_relation_set", "candidate:graph:semantic", "evidence_binder"),
    _entry("artifact-merge", "Artifact Merge", "治理", "candidate:*", "candidate:*", "artifact_merge", input_cardinality="many"),
    _entry("structured-knowledge-generator", "Structured Knowledge Generator", "知识生成", "source_chunk_set", "candidate:*", "structured_knowledge_generator", upstream=["ChunkedPromptedGenerator"], uses_llm=True, version=6,
           extra_params={"prompt_template_revision_id": {"schema": {"type": "string", "title": "Prompt 模板", "default": "promptrev_default", "x-dataforge-ui": {"widget": "prompt-template-selector"}}, "doc": "已发布且与当前知识类型匹配的 Prompt Template Revision ID。", "required": True}}),
    _entry("schema-validator", "Schema Validator", "治理", "candidate:*", "candidate:*", "schema_validator"),
    _entry("graph-quality-validator", "Graph Quality Validator", "质量", "candidate:*", "candidate:*", "graph_quality_validator"),
    _entry("mineru-pipeline-gpu-adapter", "MinerU Pipeline GPU Adapter", "Runtime", "source_file", "document_ir", "mineru_pipeline_gpu", exposure="internal", upstream=["MinerUPipelineHTTPAdapter"]),
    _entry("kbc-cleaner-batch", "KBC Cleaner Batch", "Runtime", "document_ir", "document_ir", "kbc_cleaner_batch", exposure="internal", upstream=["KBCTextCleanerBatch"]),
    _entry("kbc-chunker-batch", "KBC Chunker Batch", "Runtime", "document_ir", "chunk_set", "kbc_chunker_batch", exposure="internal", upstream=["KBCChunkGeneratorBatch"]),
    _entry("PromptedRefiner", "PromptedRefiner", "质量", "candidate:*", "candidate:*", "prompted_refiner", exposure="controlled", risk="advanced", upstream=["PromptedRefiner"]),
    _entry("multihop-qa", "Multi-hop QA", "知识生成", "chunk_set", "candidate:qa", "multihop_qa", exposure="controlled", risk="advanced", upstream=["Text2MultiHopQAGenerator"], uses_llm=True),
    _entry("pii-compliance", "PII Compliance", "合规", "document_ir", "document_ir", "pii_compliance", exposure="controlled", risk="compliance", upstream=["PIIAnonymizeRefiner"]),
)


DATAFLOW_PACKAGE = "open-dataflow"
DATAFLOW_VERSION = "1.0.10"
DATAFLOW_DIGEST = "75dd8e03fd96875472c11bd9fdf8af30e66d76a6b2d59b6b426d998db25e8790"
DATAFLOW_LOCK_DIGEST = "d575faf7e20b1bf75725fa15a4d29de17168c957d09f99f5b2cd5053df257a28"
DATAFLOW_CURATED_LOCK_DIGEST = "dcd3a3c0858ee2af3790255b435885fd50f5ef649fc18a6b26a250d84a0890e8"


def operator_surfaces(code: str, input_type: str, exposure: str = "canvas") -> list[str]:
    if exposure == "internal" or input_type in {"source_file", "document_ir", "chunk_set"}:
        return ["system-internal"]
    standard = {"reviewed-source-chunk-input", "text-knowledge-mapper", "Text2QAGenerator", "qa-extractor",
                "entity-extractor", "entity-relation-extractor", "literal-detector", "relation-extractor", "triple-builder",
                "entity-normalizer", "semantic-relation-builder", "evidence-binder",
                "schema-validator", "graph-quality-validator"}
    return (["standard-template"] if code in standard else []) + ["advanced-canvas"]


def _curated_entry(item: dict[str, Any]) -> dict[str, Any]:
    item = deepcopy(item)
    item["surfaces"] = operator_surfaces(item["code"], item["input"], item["exposure"])
    graph_modes = {"triple-builder": ["triple"], "semantic-relation-builder": ["semantic"], "evidence-binder": ["semantic"]}
    if item["code"] in graph_modes:
        item["graph_modes"] = graph_modes[item["code"]]
        item["runtime_requirements"]["graph_modes"] = item["graph_modes"]
    if item["code"] == "graph-quality-validator":
        item["knowledge_types"] = ["graph"]
    if item["code"] in {"reviewed-source-chunk-input", "prompt-generator", "structured-knowledge-generator",
                        "schema-validator", "artifact-merge"}:
        item["knowledge_types"] = ["*"]
    if item["code"] == "schema-validator":
        item["knowledge_types"] = ["graph"]
        item["display_name_zh"] = "图谱结构校验器"
        item["description"] = item["summary"] = "独立校验图谱实体、关系与方向约束，非法结构阻止该分支提交"
    implementations = {
        "Text2QAGenerator": (6, "dataflow.operators.core_text:Text2QAGenerator", "source-chunk-to-qa-v2"),
        "HashDeduplicateFilter": (4, "dataflow.operators.general_text:HashDeduplicateFilter", "candidate-hash-deduplicate-v1"),
        "PromptedRefiner": (4, "dataflow.operators.core_text:PromptedRefiner", "candidate-refiner-v1"),
    }
    if item["code"] not in implementations:
        return item
    version, implementation, adapter = implementations[item["code"]]
    uses_llm = item["code"] != "HashDeduplicateFilter"
    item.update(version=version, source="dataflow", catalog_group="dataflow_featured", adapter_code=adapter)
    item["runtime_requirements"] = {
        "driver": "dataflow",
        "executor": "dataflow-llm" if uses_llm else "dataflow-storage",
        "package": DATAFLOW_PACKAGE, "package_version": DATAFLOW_VERSION, "package_digest": DATAFLOW_DIGEST,
        "dependency_lock_digest": DATAFLOW_LOCK_DIGEST,
        "implementation": implementation, "adapter_version": adapter, "uses_llm": uses_llm,
        "preserve_fields": ["source_version_ids", "source_chunk_id", "source_chunk_revision_id", "source_review_snapshot_id", "anchor_json", "evidence_text"],
    }
    props = item["parameter_schema"].setdefault("properties", {})
    docs = item["parameter_docs"]
    if uses_llm:
        props["llm_serving"] = {"type": "string", "title": "模型服务", "x-dataforge-ui": {"widget": "llm-serving-selector"}}
        docs["llm_serving"] = "已配置的模型服务，发布时冻结配置指纹。"
    if item["code"] == "Text2QAGenerator":
        props["questions_per_chunk"] = {"type": "integer", "title": "每块最多问题数", "default": 1, "minimum": 1, "maximum": 10}
        docs["questions_per_chunk"] = "上游先生成提问方向，再生成问答；每块最多生成的问题数。"
    elif item["code"] == "HashDeduplicateFilter":
        item["runtime_requirements"]["implementations"] = {"identity": implementation}
        item["description"] = item["summary"] = "使用 DataFlow HashDeduplicateFilter 按知识身份精确去重，保留来源与 Evidence"
    else:
        item["knowledge_types"] = ["text", "qa"]
        props["prompt_template_revision_id"] = {"type": "string", "title": "修订 Prompt", "default": "promptrev_refiner", "x-dataforge-ui": {"widget": "prompt-template-selector"}}
        item["parameter_schema"]["required"] = ["prompt_template_revision_id"]
        docs["prompt_template_revision_id"] = "已发布的修订提示词；只修订候选正文，不能改变来源 Evidence。"
    return item


CATALOG_SEEDS = tuple(_curated_entry(item) for item in CATALOG_SEEDS)
for _item in CATALOG_SEEDS:
    if _item["code"] == "Text2QAGenerator":
        _item["parameter_schema"]["properties"]["extraction_instructions"] = deepcopy(QA_EXTRACTION_SCHEMA)
        _item["parameter_docs"]["extraction_instructions"] = QA_EXTRACTION_SCHEMA["description"]

_hash = next(item for item in CATALOG_SEEDS if item["code"] == "HashDeduplicateFilter")
_minhash = deepcopy(_hash)
_minhash.update(code="MinHashDeduplicateFilter", name="MinHashDeduplicateFilter",
                display_name_zh="MinHash 相似去重过滤器", knowledge_types=["text", "qa"])
_minhash["adapter_code"] = _minhash["runtime_requirements"]["adapter_version"] = "candidate-minhash-deduplicate-v1"
_minhash["runtime_requirements"]["implementation"] = "dataflow.operators.general_text:MinHashDeduplicateFilter"
_minhash["runtime_requirements"]["implementations"] = {
    "identity": "dataflow.operators.general_text:HashDeduplicateFilter",
    "minhash": "dataflow.operators.general_text:MinHashDeduplicateFilter",
}
_minhash["parameter_schema"]["properties"]["threshold"] = {"type": "number", "minimum": 0.01, "maximum": 1, "default": 0.9, "title": "相似度阈值"}
_minhash["parameter_docs"]["threshold"] = "仅在同一来源 Chunk 内对文本或问答做 MinHash 相似去重。"
_minhash["description"] = _minhash["summary"] = "使用 DataFlow MinHashDeduplicateFilter 在同一来源 Chunk 内做相似去重；少于 5 字符的正文使用 HashDeduplicateFilter 精确去重保护"
CATALOG_SEEDS += (_minhash,)

DATAFLOW_CURATED_SPECS = {
    "ContentNullFilter": {"name": "空内容过滤器", "category": "内容清洗", "adapter": "candidate-row-filter-v1", "properties": {}},
    "CharNumberFilter": {"name": "字符长度过滤器", "category": "内容清洗", "adapter": "candidate-row-filter-v1",
        "properties": {"threshold": {"type": "integer", "minimum": 1, "default": 100, "title": "最小字符数", "description": "按上游规则去除空格、换行和制表符后计数。"}}},
    "SpecialCharacterFilter": {"name": "特殊字符过滤器", "category": "内容清洗", "adapter": "candidate-row-filter-v1", "properties": {}},
    "NgramHashDeduplicateFilter": {"name": "N-gram 相似去重过滤器", "category": "内容去重", "adapter": "candidate-ngram-deduplicate-v1",
        "properties": {
            "n_gram": {"type": "integer", "minimum": 1, "default": 3, "title": "分段数量", "description": "上游按字符长度等分，尾部不足整段的字符不参与比较；短于分段数时使用 Hash 精确去重。"},
            "hash_func": {"type": "string", "enum": ["md5", "sha256", "xxh3"], "default": "md5", "title": "哈希算法"},
            "diff_size": {"type": "integer", "minimum": 1, "default": 1, "title": "重复片段阈值", "description": "上游实际比较哈希集合交集：共同片段数达到阈值即视为重复。"}}},
    "SimHashDeduplicateFilter": {"name": "SimHash 相似去重过滤器", "category": "内容去重", "adapter": "candidate-simhash-deduplicate-v1",
        "properties": {
            "fingerprint_size": {"type": "integer", "minimum": 8, "maximum": 128, "multipleOf": 8, "default": 64, "title": "指纹长度"},
            "bound": {"type": "number", "minimum": 0, "exclusiveMaximum": 1, "default": 0.1, "title": "相似度容差"}}},
    "PromptedFilter": {"name": "智能内容过滤器", "category": "智能过滤", "adapter": "candidate-prompted-filter-v1", "uses_llm": True,
        "properties": {
            "llm_serving": {"type": "string", "title": "模型服务", "x-dataforge-ui": {"widget": "llm-serving-selector"}},
            "prompt_template_revision_id": {"type": "string", "default": "promptrev_candidate_filter", "title": "评分 Prompt", "x-dataforge-ui": {"widget": "prompt-template-selector"}},
            "min_score": {"type": "integer", "minimum": 1, "maximum": 5, "default": 4, "title": "最低保留分"},
            "max_score": {"type": "integer", "minimum": 1, "maximum": 5, "default": 5, "title": "最高保留分"}}},
}


def _new_curated_entries():
    base = next(item for item in CATALOG_SEEDS if item["code"] == "HashDeduplicateFilter")
    for code, spec in DATAFLOW_CURATED_SPECS.items():
        item = deepcopy(base)
        uses_llm = spec.get("uses_llm", False)
        namespace = "core_text" if uses_llm else "general_text"
        description = ("同来源 Chunk 内对文本/问答候选相似去重，保留原候选与完整 Evidence。" if "deduplicate" in spec["adapter"]
                       else "通过真实 DataFlow 过滤文本/问答候选，只决定保留或删除，不改变正文和 Evidence。")
        item.update(code=code, name=code, display_name_zh=spec["name"], subcategory=spec["category"],
                    summary=description, description=description, version=1, knowledge_types=["text", "qa"],
                    scenarios=[description], upstream=[code], adapter_code=spec["adapter"], surfaces=["advanced-canvas"],
                    parameter_schema={"type": "object", "properties": deepcopy(spec["properties"]), "additionalProperties": False},
                    parameter_docs={key: value.get("description", value["title"]) for key, value in spec["properties"].items()})
        if uses_llm:
            item["parameter_schema"]["required"] = ["prompt_template_revision_id"]
        example = {"source_knowledge_id": "candidate-example", "canonical_content": "高血压患者应规范随访，遵医嘱用药并定期复查。" * 6,
                   "source_version_ids": ["version-example"], "source_chunk_id": "chunk-example",
                   "source_chunk_revision_id": "revision-example", "source_review_snapshot_id": "review-example",
                   "anchor_json": {"page": 1}, "evidence_text": "审核后的来源正文", "data_json": {}}
        item["input_example"], item["output_example"] = {"input": [example]}, {"output": [deepcopy(example)]}
        item["runtime_requirements"] = {
            "driver": "dataflow",
            "executor": "dataflow-llm" if uses_llm else "dataflow-storage",
            "package": DATAFLOW_PACKAGE, "package_version": DATAFLOW_VERSION, "package_digest": DATAFLOW_DIGEST,
            "dependency_lock_digest": DATAFLOW_CURATED_LOCK_DIGEST, "uses_llm": uses_llm,
            "implementation": f"dataflow.operators.{namespace}:{code}", "adapter_version": spec["adapter"],
            "preserve_fields": deepcopy(base["runtime_requirements"]["preserve_fields"]),
        }
        if code == "NgramHashDeduplicateFilter":
            item["runtime_requirements"]["implementations"] = {"short_text": "dataflow.operators.general_text:HashDeduplicateFilter"}
        yield item


CATALOG_SEEDS += tuple(_new_curated_entries())
for _item in CATALOG_SEEDS:
    if _item["code"] in {"Text2QAGenerator", "PromptedRefiner", "HashDeduplicateFilter", "MinHashDeduplicateFilter"}:
        _item["subcategory"] = {"Text2QAGenerator": "内容生成", "PromptedRefiner": "内容处理"}.get(_item["code"], "内容去重")

from .governance_catalog import extend_catalog

CATALOG_SEEDS, _ = extend_catalog(
    CATALOG_SEEDS, DATAFLOW_PACKAGE, DATAFLOW_VERSION, DATAFLOW_DIGEST, DATAFLOW_CURATED_LOCK_DIGEST,
)

# Apply the current execution contracts to the clean-environment catalog.
from .operators.graph_chunks import TRIPLE_CHUNK_VERSIONS

for _item in CATALOG_SEEDS:
    if _item["code"] in TRIPLE_CHUNK_VERSIONS:
        _item["version"] = TRIPLE_CHUNK_VERSIONS[_item["code"]]
        _item["runtime_requirements"] = {**_item["runtime_requirements"], "triple_chunk_isolation": True}
        _item["description"] = _item["summary"] = _item["description"] + "；三元组按分块隔离错误，保留失败块旧知识，其他分块继续。"

from .graph_prompt import GRAPH_GUIDANCE_VERSIONS

for _item in CATALOG_SEEDS:
    if _item["code"] in GRAPH_GUIDANCE_VERSIONS:
        _item["version"] = GRAPH_GUIDANCE_VERSIONS[_item["code"]]
        _item["parameter_schema"]["properties"].pop("prompt_mode", None)
        _item["parameter_docs"].pop("prompt_mode", None)
        _title = "实体抽取要求" if _item["code"] == "entity-extractor" else "关系抽取要求"
        _item["parameter_schema"]["properties"]["extraction_instructions"] = {
            "type": "string", "title": _title, "default": "",
            "description": "业务要求会进入实际模型提示词；空白使用系统默认。类型、原文与 JSON 输出格式由系统维护。",
            "x-dataforge-ui": {"widget": "extraction-instructions"},
        }
        _item["parameter_docs"]["extraction_instructions"] = _item["parameter_schema"]["properties"]["extraction_instructions"]["description"]

# The current relation version includes bounded endpoint repair.
from .graph_prompt import RELATION_REPAIR_VERSION

for _item in CATALOG_SEEDS:
    if _item["code"] == "relation-extractor":
        _item["version"] = RELATION_REPAIR_VERSION
        _item["runtime_requirements"] = {**_item["runtime_requirements"], "triple_endpoint_repair_attempts": 1}
        _item["description"] = _item["summary"] = _item["description"] + "；Triple 未知实体端点最多重抽取一次，仍不合法则隔离整块；Semantic 不变。"

# Joint extraction shares business controls, not the two-call execution path.
_joint_params = {}
for _source in CATALOG_SEEDS:
    if _source["code"] in {"entity-extractor", "relation-extractor"}:
        for _key, _schema in _source["parameter_schema"]["properties"].items():
            if _key == "llm_serving":
                continue
            if _key == "extraction_instructions":
                _key = "entity_extraction_instructions" if _source["code"] == "entity-extractor" else "relation_extraction_instructions"
            _joint_params[_key] = {"schema": deepcopy(_schema), "doc": _source["parameter_docs"].get(_key, _schema.get("description", _key))}
_joint = _curated_entry(_entry("entity-relation-extractor", "Entity Relation Extractor", "图谱",
    "source_chunk_set", "relation_candidate_set", "entity_relation_extractor", uses_llm=True,
    version=1, extra_params=_joint_params))
_joint["input_example"] = {"input": [{"content": "设备A包含控制模块。", "source_chunk_id": "chunk-example-001"}]}
_joint["output_example"] = {"output": [{"source_chunk_id": "chunk-example-001",
    "entities": [{"name": "设备A", "type": "concept"}, {"name": "控制模块", "type": "concept"}],
    "relations": [{"source": "设备A", "target": "控制模块", "type": "包含", "type_label": "包含"}]}]}
_joint["runtime_requirements"].update(joint_extraction=True, protocol_repair_attempts=1)
CATALOG_SEEDS += (_joint,)
for _item in CATALOG_SEEDS:
    if _item["code"] in {"literal-detector", "entity-normalizer"}:
        _item["version"] = 4
        _item["input_ports"]["input"]["accepted_types"] = ["entity_candidate_set", "relation_candidate_set"]
        _item["output_ports"]["output"]["output_by_input"] = {
            "entity_candidate_set": "entity_candidate_set", "relation_candidate_set": "relation_candidate_set"}

for _item in CATALOG_SEEDS:
    if _item["code"] == "Text2QAGenerator":
        _item["version"] = 8
        _item["adapter_code"] = _item["runtime_requirements"]["adapter_version"] = "source-chunk-to-qa-v4"
        _item["parameter_schema"]["properties"].pop("extraction_instructions", None)
        _item["parameter_docs"].pop("extraction_instructions", None)
        _item["surfaces"] = ["advanced-canvas"]
        _item["summary"] = _item["description"] = "上游两阶段生成：先生成提问方向，再生成问答；保留 RL 短答案提示词，不支持业务提取要求。"

_native_qa = _entry("qa-extractor", "QA Extractor", "知识生成", "source_chunk_set", "candidate:qa", "native_qa_extractor",
                    uses_llm=True, version=1, extra_params={
                        "questions_per_chunk": {"schema": {"type": "integer", "title": "每块最多问题数", "minimum": 1, "maximum": 10, "default": 1}},
                        "extraction_instructions": {"schema": deepcopy(QA_EXTRACTION_SCHEMA)},
                    })
_native_qa.update(subcategory="知识生成", surfaces=["standard-template", "advanced-canvas"])
_native_qa["input_ports"]["input"]["accepted_types"] = ["source_chunk_set", "derived_text_set"]
_native_qa["parameter_schema"]["properties"]["llm_serving"].pop("default", None)
_native_qa["parameter_schema"]["required"] = []
CATALOG_SEEDS += (_native_qa,)

DATAFLOW_TEXT_GENERATION = {"Text2QAGenerator", "Text2MultiHopQAGenerator"}
DATAFLOW_DEDUPLICATION = {"HashDeduplicateFilter", "MinHashDeduplicateFilter", "NgramHashDeduplicateFilter", "SimHashDeduplicateFilter", "SemDeduplicateFilter"}
DATAFLOW_TEXT_CLEANING = {"PromptedRefiner", "PIIAnonymizeRefiner", "RemoveRepetitionsPunctuationRefiner"}
DATAFORGE_CONTENT_PROCESSING = {"reviewed-source-chunk-input", "faq-record-mapper", "text-knowledge-mapper", "qa-extractor"}
DATAFORGE_QUALITY_PROCESSING = {"schema-validator", "graph-quality-validator"}

for _item in CATALOG_SEEDS:
    if _item["source"] == "dataflow":
        _item["category"] = ("text-generation" if _item["code"] in DATAFLOW_TEXT_GENERATION else
                             "deduplication" if _item["code"] in DATAFLOW_DEDUPLICATION else
                             "text-cleaning" if _item["code"] in DATAFLOW_TEXT_CLEANING else "content-filtering")
        _item["catalog_group"] = "dataflow_featured"
    else:
        _item["category"] = ("content-processing" if _item["code"] in DATAFORGE_CONTENT_PROCESSING else
                             "quality-processing" if _item["code"] in DATAFORGE_QUALITY_PROCESSING else
                             "knowledge-generation" if "advanced-canvas" in _item.get("surfaces", []) else "content-processing")
        _item["catalog_group"] = "dataforge"

PLATFORM_RESERVED_OPERATOR_CODES = frozenset(
    {item["code"].casefold() for item in CATALOG_SEEDS} | {"knowledge-sink"}
)


def catalog_by_code(entries: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None = None) -> dict[str, dict[str, Any]]:
    entries = CATALOG_SEEDS if entries is None else entries
    return {entry["code"]: dict(entry) for entry in entries}


def subflow_seeds() -> tuple[dict[str, Any], ...]:
    return (
        {"code": "document-parse", "name": "Document Parse", "display_name_zh": SUBFLOW_DISPLAY_NAMES_ZH["document-parse"], "description": "将源文件解析并规范为统一 DocumentIR。", "definition": {"entry_node": "parser", "exit_node": "normalize", "nodes": [{"id": "parser", "kind": "operator", "ref": "document-parser"}, {"id": "normalize", "kind": "operator", "ref": "document-ir-normalizer"}], "edges": [["parser", "normalize"]]}},
        {"code": "document-clean", "name": "Document Clean", "display_name_zh": SUBFLOW_DISPLAY_NAMES_ZH["document-clean"], "description": "过滤、清洗并规范文档正文。", "definition": {"entry_node": "null", "exit_node": "normalize", "nodes": [{"id": "null", "kind": "operator", "ref": "null-filter"}, {"id": "language", "kind": "operator", "ref": "language-filter"}, {"id": "clean", "kind": "operator", "ref": "text-cleaner"}, {"id": "space", "kind": "operator", "ref": "whitespace-cleaner"}, {"id": "normalize", "kind": "operator", "ref": "text-normalizer"}], "edges": [["null", "language"], ["language", "clean"], ["clean", "space"], ["space", "normalize"]]}},
        {"code": "knowledge-chunk", "name": "Knowledge Chunk", "display_name_zh": SUBFLOW_DISPLAY_NAMES_ZH["knowledge-chunk"], "description": "将 DocumentIR 切分为可追溯的 SourceChunk。", "definition": {"entry_node": "chunk", "exit_node": "source-chunks", "nodes": [{"id": "chunk", "kind": "operator", "ref": "semantic-chunker"}, {"id": "source-chunks", "kind": "operator", "ref": "source-chunk-builder"}], "edges": [["chunk", "source-chunks"]]}},
    )


def builtin_flow_definition(output_types: list[str]) -> dict[str, Any]:
    nodes: list[dict[str, Any]] = [
        {"id": "reviewed-input", "kind": "operator", "node_role": "flow_input", "ref": "reviewed-source-chunk-input"},
    ]
    edges: list[list[str]] = []
    generators = {"text": "text-knowledge-mapper", "qa": "qa-extractor", "graph": "graph-extractor"}
    for raw_kind in output_types:
        kind = "graph:triple" if raw_kind == "graph" else raw_kind
        family, _, mode = kind.partition(":")
        generator = f"generate-{kind}"
        validator = f"validate-{kind}"; quality = f"quality-{kind}"; sink = f"sink-{kind}"
        generator_params: dict[str, Any] = {"knowledge_type": family}
        if mode:
            generator_params["graph_mode"] = mode
        generator_ref = generators.get(family, "structured-knowledge-generator")
        graph_prefix: list[dict[str, Any]] = []
        graph_edges: list[list[str]] = []
        if kind == "graph:triple":
            graph_prefix = [
                {"id": f"extract-{kind}", "kind": "operator", "ref": "entity-relation-extractor", "params": {"knowledge_type": "graph", "graph_mode": mode, "llm_serving": DEFAULT_LLM_SERVING_ID}},
                {"id": f"literals-{kind}", "kind": "operator", "ref": "literal-detector", "params": {"knowledge_type": "graph", "graph_mode": mode}},
            ]
            graph_edges = [["reviewed-input", f"extract-{kind}"], [f"extract-{kind}", f"literals-{kind}"], [f"literals-{kind}", generator]]
            generator_ref = "triple-builder"
        elif kind == "graph:semantic":
            graph_prefix = [
                {"id": f"extract-{kind}", "kind": "operator", "ref": "entity-relation-extractor", "params": {"knowledge_type": "graph", "graph_mode": mode, "llm_serving": DEFAULT_LLM_SERVING_ID}},
                {"id": f"literals-{kind}", "kind": "operator", "ref": "literal-detector", "params": {"knowledge_type": "graph", "graph_mode": mode}},
                {"id": f"normalize-{kind}", "kind": "operator", "ref": "entity-normalizer", "params": {"knowledge_type": "graph", "graph_mode": mode}},
                {"id": f"build-{kind}", "kind": "operator", "ref": "semantic-relation-builder", "params": {"knowledge_type": "graph", "graph_mode": mode}},
            ]
            graph_edges = [["reviewed-input", f"extract-{kind}"], [f"extract-{kind}", f"literals-{kind}"], [f"literals-{kind}", f"normalize-{kind}"], [f"normalize-{kind}", f"build-{kind}"], [f"build-{kind}", generator]]
            generator_ref = "evidence-binder"
        if generator_ref in {"prompt-generator", "structured-knowledge-generator"}:
            generator_params["prompt_template_revision_id"] = "promptrev_default"
        if generator_ref in {"prompt-generator", "qa-extractor", "Text2QAGenerator", "graph-extractor", "structured-knowledge-generator", "multihop-qa"}:
            generator_params["llm_serving"] = DEFAULT_LLM_SERVING_ID
        nodes.extend(graph_prefix)
        nodes.extend((
            {"id": generator, "kind": "operator", "ref": generator_ref, "params": generator_params},
            *([{"id": validator, "kind": "operator", "ref": "schema-validator", "params": {"knowledge_type": family, "graph_mode": mode}}] if family == "graph" else []),
            *([{"id": quality, "kind": "operator", "ref": "graph-quality-validator", "params": {"knowledge_type": family, "graph_mode": mode or None}}] if family == "graph" else []),
            {"id": sink, "kind": "knowledge_sink", "node_role": "knowledge_output", "knowledge_type": family, "graph_mode": mode or None, "output_key": kind},
        ))
        edges.extend(graph_edges or [["reviewed-input", generator]])
        edges.extend([[generator, validator], [validator, quality], [quality, sink]]
                     if family == "graph" else [[generator, sink]])
    return {"schema_version": 3, "purpose": "knowledge", "nodes": nodes, "edges": [
        {"source": edge[0], "source_port": "output", "target": edge[1], "target_port": "input"} for edge in edges
    ], "graph_config": {"entity_types": [], "relation_types": []}, "ui": {"positions": {}}}


def preparation_flow_definition() -> dict[str, Any]:
    """Hidden system flow that stops after formal SourceChunk production."""
    return {
        "schema_version": 3,
        "purpose": "source_preparation",
        "nodes": [
            {"id": "parse", "kind": "subflow", "ref": "document-parse"},
            {"id": "clean", "kind": "subflow", "ref": "document-clean"},
            {"id": "chunk", "kind": "subflow", "ref": "knowledge-chunk", "params": dict(DEFAULT_CHUNKER_PARAMS)},
        ],
        "edges": [
            {"source": "parse", "source_port": "output", "target": "clean", "target_port": "input"},
            {"source": "clean", "source_port": "output", "target": "chunk", "target_port": "input"},
        ],
        "ui": {"positions": {}},
    }
