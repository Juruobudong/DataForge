"""Stable DataForge knowledge-production catalog.

The catalog deliberately exposes logical DataForge operators instead of the full
DataFlow registry.  Adapter/runtime changes are recorded on OperatorVersion and
never leak into a published Flow definition.
"""
from __future__ import annotations

from typing import Any

from .llm_serving import DEFAULT_LLM_SERVING_ID


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
    "deduplicate": "移除重复的候选知识",
    "prompt-generator": "按受控提示生成结构化知识",
    "qa-generator": "从来源文本块生成问答知识",
    "graph-extractor": "从来源文本块提取图谱知识",
    "entity-extractor": "识别文本中的实体候选",
    "relation-extractor": "识别实体之间的关系候选",
    "triple-builder": "构造主谓宾三元组知识",
    "entity-normalizer": "合并并规范同义实体",
    "semantic-relation-builder": "构造带语义描述的实体关系",
    "evidence-binder": "为语义关系绑定来源证据",
    "literal-detector": "识别并规范化数字、范围、剂量等字面值",
    "graph-quality-validator": "在正式发布前校验图谱实体、关系与字面值质量",
    "artifact-merge": "合并多个上游候选结果",
    "structured-knowledge-generator": "按知识类型契约生成结构化知识",
    "quality-evaluator": "评估候选知识的质量分数",
    "quality-filter": "按质量门槛筛选候选知识",
    "source-binding": "为候选知识绑定来源与锚点",
    "schema-validator": "校验候选知识是否符合 Schema",
    "knowledge-diff": "计算候选知识与当前版本的差异",
    "mineru-pipeline-gpu-adapter": "通过 MinerU GPU 服务解析 PDF",
    "kbc-cleaner-batch": "批量执行文本清理",
    "kbc-chunker-batch": "批量执行语义切片",
    "prompted-refiner": "使用受控提示修订候选知识",
    "multihop-qa": "生成需要多步推理的问答知识",
    "pii-compliance": "识别并处理个人敏感信息",
    "kcenter-greedy": "按覆盖度选择代表性样本",
    "reference-remover": "移除文档中的参考文献部分",
}

OPERATOR_CATEGORIES: tuple[str, ...] = (
    "文档输入", "内容处理", "知识切分", "知识生成", "LLM 处理",
    "质量治理", "向量处理", "知识发布", "流程控制",
)

OPERATOR_DISPLAY_NAMES_ZH: dict[str, str] = {
    "document-parser": "文档解析器", "document-ir-normalizer": "文档结构规范器", "null-filter": "空内容过滤器",
    "language-filter": "语言过滤器", "text-cleaner": "文本清洗器", "whitespace-cleaner": "空白清理器",
    "text-normalizer": "文本规范器", "semantic-chunker": "结构化分块器", "source-chunk-builder": "来源切片构建器",
    "reviewed-source-chunk-input": "已审核来源切片",
    "deduplicate": "候选去重器", "prompt-generator": "提示词生成器", "qa-generator": "问答生成器",
    "graph-extractor": "图谱抽取器", "entity-extractor": "实体抽取器", "relation-extractor": "关系抽取器",
    "triple-builder": "三元组构建器", "entity-normalizer": "实体规范器", "semantic-relation-builder": "语义关系构建器",
    "evidence-binder": "证据绑定器", "literal-detector": "字面值识别器", "graph-quality-validator": "图谱质量校验器", "artifact-merge": "产物合并器", "structured-knowledge-generator": "结构化知识生成器",
    "quality-evaluator": "知识质量评估器", "quality-filter": "知识质量过滤器", "source-binding": "来源绑定器",
    "schema-validator": "结构校验器", "knowledge-diff": "知识差异计算器", "mineru-pipeline-gpu-adapter": "MinerU GPU 解析适配器",
    "kbc-cleaner-batch": "批量文本清洗器", "kbc-chunker-batch": "批量语义切片器", "prompted-refiner": "知识修订器",
    "multihop-qa": "多跳问答生成器", "pii-compliance": "敏感信息合规器", "kcenter-greedy": "代表样本选择器",
    "reference-remover": "参考文献移除器", "faq-table-row-builder": "FAQ 表格行构建器",
    "faq-record-mapper": "FAQ 知识映射器",
}


def _catalog_category(code: str, previous: str) -> tuple[str, str]:
    if code == "document-parser": return "文档输入", "文档解析"
    if code in {"semantic-chunker", "source-chunk-builder", "kbc-chunker-batch"}: return "知识切分", previous
    if code in {"qa-generator", "graph-extractor", "entity-extractor", "relation-extractor", "triple-builder", "entity-normalizer", "semantic-relation-builder", "evidence-binder", "structured-knowledge-generator", "multihop-qa", "literal-detector"}: return "知识生成", previous
    if code in {"prompt-generator", "prompted-refiner"}: return "LLM 处理", previous
    if code in {"deduplicate", "quality-evaluator", "quality-filter", "source-binding", "schema-validator", "knowledge-diff", "graph-quality-validator", "kcenter-greedy"}: return "质量治理", previous
    if code == "artifact-merge": return "流程控制", previous
    return "内容处理", previous


def _knowledge_types(source: str, target: str) -> list[str]:
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


def _entry(code: str, name: str, category: str, source: str, target: str, adapter: str, *, exposure: str = "canvas", risk: str = "standard", upstream: list[str] | None = None, input_cardinality: str = "one", uses_llm: bool = False, extra_params: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    primary_category, subcategory = _catalog_category(code, category)
    parameter_schema: dict[str, Any] = {"type": "object", "additionalProperties": False}
    parameter_docs: dict[str, str] = {"_overview": "此版本没有面向画布的业务可配置参数；运行时内部配置不会返回前端。"}
    properties: dict[str, Any] = {}
    if uses_llm:
        properties["llm_serving"] = {
            "type": "string",
            "default": DEFAULT_LLM_SERVING_ID,
            "description": "已配置的 Model Serving ID，不是模型名称或 URL",
        }
        parameter_docs["_overview"] = "选择 DataForge 已配置的 Model Serving；连接信息和密钥不会暴露到 Flow。"
        parameter_docs["llm_serving"] = "Model Serving ID。当前默认 qwen3_32b；未来由 Serving 选择器提供候选。"
    if extra_params:
        for key, spec in extra_params.items():
            properties[key] = spec.get("schema", {"type": "string"})
            parameter_docs[key] = spec.get("doc", key)
    if properties:
        parameter_schema = {"type": "object", "properties": properties, "additionalProperties": False}
    return {
        "code": code, "name": name, "display_name_zh": OPERATOR_DISPLAY_NAMES_ZH[code],
        "summary": OPERATOR_DESCRIPTIONS[code], "description": OPERATOR_DESCRIPTIONS[code],
        "category": primary_category, "subcategory": subcategory, "input": source,
        "output": target, "adapter_code": adapter, "exposure": exposure,
        "risk_level": risk, "upstream": upstream or [], "version": 4 if uses_llm else 3,
        "scenarios": [f"适用于{OPERATOR_DESCRIPTIONS[code]}的受控知识流程"],
        "knowledge_types": _knowledge_types(source, target),
        "recommended_predecessors": [], "recommended_successors": [],
        "lifecycle_status": "deprecated" if exposure == "disabled" else "published",
        "input_ports": {"input": {"artifact_type": source, "cardinality": input_cardinality}},
        "output_ports": {"output": {"artifact_type": target, "cardinality": "many"}},
        "input_example": {"input": [_artifact_example(source)]},
        "output_example": {"output": [_artifact_example(target)]},
        "parameter_schema": parameter_schema,
        "parameter_docs": parameter_docs,
        "runtime_requirements": {"executor": "dataforge-adapter", "upstream": upstream or [], "uses_llm": uses_llm},
    }


CATALOG_SEEDS: tuple[dict[str, Any], ...] = (
    _entry("reviewed-source-chunk-input", "Reviewed SourceChunk Input", "文档", "approved_source_chunks", "source_chunk_set", "reviewed_source_chunk_input"),
    _entry("document-parser", "Document Parser", "文档", "source_file", "document_ir", "document_parser", upstream=["DataForgeNativeParser", "MinerUPipelineHTTPAdapter"]),
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
    _entry("deduplicate", "Candidate Deduplicate", "清洗", "candidate:*", "candidate:*", "candidate_deduplicate", upstream=["MinHashDeduplicateFilter", "SemDeduplicateFilter"]),
    _entry("prompt-generator", "Prompt Generator", "知识生成", "source_chunk_set", "candidate:*", "prompt_generator", risk="advanced", upstream=["PromptedGenerator", "ChunkedPromptedGenerator"], uses_llm=True),
    _entry("qa-generator", "QA Generator", "知识生成", "source_chunk_set", "candidate:qa", "qa_generator", upstream=["Text2QAGenerator"], uses_llm=True),
    _entry("graph-extractor", "Graph Extractor", "知识生成", "source_chunk_set", "candidate:graph", "graph_extractor", uses_llm=True),
    _entry("entity-extractor", "Entity Extractor", "图谱", "source_chunk_set", "entity_candidate_set", "entity_extractor", uses_llm=True,
           extra_params={
               "entity_types": {"schema": {"type": "array", "items": {"type": "string"}}, "doc": "允许抽取的实体类型 code 列表（由模板 graph_config 注入）。"},
               "unknown_entity_policy": {"schema": {"type": "string", "enum": ["reject", "other", "suggest"], "default": "reject"}, "doc": "未识别实体处理策略：reject=拒绝，other=归为“其他”，suggest=允许模型建议新类型。"},
               "generate_description": {"schema": {"type": "boolean", "default": True}, "doc": "是否要求 LLM 为实体生成描述。"},
               "extract_aliases": {"schema": {"type": "boolean", "default": True}, "doc": "是否要求 LLM 抽取实体别名。"},
               "confidence_threshold": {"schema": {"type": "number", "default": 0.7}, "doc": "实体置信度阈值。"},
               "prompt_mode": {"schema": {"type": "string", "enum": ["generated", "custom"], "default": "generated"}, "doc": "生成 Prompt 模式。"},
           }),
    _entry("relation-extractor", "Relation Extractor", "图谱", "entity_candidate_set", "relation_candidate_set", "relation_extractor", uses_llm=True,
           extra_params={
               "relation_types": {"schema": {"type": "array", "items": {"type": "string"}}, "doc": "允许抽取的关系类型 code 列表（由模板 graph_config 注入）。"},
               "relation_constraints": {"schema": {"type": "array", "items": {"type": "object"}}, "doc": "关系的 source/target 实体类型约束。"},
               "unknown_relation_policy": {"schema": {"type": "string", "enum": ["reject", "other", "suggest"], "default": "reject"}, "doc": "未识别关系处理策略。"},
           }),
    _entry("literal-detector", "Literal Detector", "图谱", "entity_candidate_set", "entity_candidate_set", "literal_detector"),
    _entry("triple-builder", "Triple Builder", "图谱", "relation_candidate_set", "candidate:graph:triple", "triple_builder"),
    _entry("entity-normalizer", "Entity Normalizer", "图谱", "entity_candidate_set", "entity_candidate_set", "entity_normalizer"),
    _entry("semantic-relation-builder", "Semantic Relation Builder", "图谱", "relation_candidate_set", "semantic_relation_set", "semantic_relation_builder"),
    _entry("evidence-binder", "Evidence Binder", "图谱", "semantic_relation_set", "candidate:graph:semantic", "evidence_binder"),
    _entry("artifact-merge", "Artifact Merge", "治理", "candidate:*", "candidate:*", "artifact_merge", input_cardinality="many"),
    _entry("structured-knowledge-generator", "Structured Knowledge Generator", "知识生成", "source_chunk_set", "candidate:*", "structured_knowledge_generator", upstream=["ChunkedPromptedGenerator"], uses_llm=True),
    _entry("quality-evaluator", "Knowledge Evaluator", "质量", "candidate:*", "candidate:*", "quality_evaluator", upstream=["PromptedEvaluator"]),
    _entry("quality-filter", "Knowledge Filter", "质量", "candidate:*", "candidate:*", "quality_filter", upstream=["PromptedFilter"]),
    _entry("source-binding", "Source Binding", "治理", "candidate:*", "candidate:*", "source_binding"),
    _entry("schema-validator", "Schema Validator", "治理", "candidate:*", "candidate:*", "schema_validator"),
    _entry("graph-quality-validator", "Graph Quality Validator", "质量", "candidate:*", "candidate:*", "graph_quality_validator"),
    _entry("knowledge-diff", "Knowledge Diff", "治理", "candidate:*", "candidate:*", "knowledge_diff"),
    _entry("mineru-pipeline-gpu-adapter", "MinerU Pipeline GPU Adapter", "Runtime", "source_file", "document_ir", "mineru_pipeline_gpu", exposure="internal", upstream=["MinerUPipelineHTTPAdapter"]),
    _entry("kbc-cleaner-batch", "KBC Cleaner Batch", "Runtime", "document_ir", "document_ir", "kbc_cleaner_batch", exposure="internal", upstream=["KBCTextCleanerBatch"]),
    _entry("kbc-chunker-batch", "KBC Chunker Batch", "Runtime", "document_ir", "chunk_set", "kbc_chunker_batch", exposure="internal", upstream=["KBCChunkGeneratorBatch"]),
    _entry("prompted-refiner", "Knowledge Refiner", "质量", "candidate:*", "candidate:*", "prompted_refiner", exposure="controlled", risk="advanced", upstream=["PromptedRefiner"]),
    _entry("multihop-qa", "Multi-hop QA", "知识生成", "chunk_set", "candidate:qa", "multihop_qa", exposure="controlled", risk="advanced", upstream=["Text2MultiHopQAGenerator"], uses_llm=True),
    _entry("pii-compliance", "PII Compliance", "合规", "document_ir", "document_ir", "pii_compliance", exposure="controlled", risk="compliance", upstream=["PIIAnonymizeRefiner"]),
    _entry("kcenter-greedy", "KCenter Greedy", "禁用", "candidate:*", "candidate:*", "disabled", exposure="disabled", risk="disabled", upstream=["KCenterGreedyFilter"]),
    _entry("reference-remover", "Reference Remover", "禁用", "document_ir", "document_ir", "disabled", exposure="disabled", risk="disabled", upstream=["ReferenceRemoverRefiner"]),
)


def catalog_by_code(entries: list[dict[str, Any]] | tuple[dict[str, Any], ...] = CATALOG_SEEDS) -> dict[str, dict[str, Any]]:
    return {entry["code"]: dict(entry) for entry in entries}


def subflow_seeds() -> tuple[dict[str, Any], ...]:
    return (
        {"code": "document-parse", "name": "Document Parse", "description": "将源文件解析并规范为统一 DocumentIR。", "definition": {"entry_node": "parser", "exit_node": "normalize", "nodes": [{"id": "parser", "kind": "operator", "ref": "document-parser"}, {"id": "normalize", "kind": "operator", "ref": "document-ir-normalizer"}], "edges": [["parser", "normalize"]]}},
        {"code": "document-clean", "name": "Document Clean", "description": "过滤、清洗并规范文档正文。", "definition": {"entry_node": "null", "exit_node": "normalize", "nodes": [{"id": "null", "kind": "operator", "ref": "null-filter"}, {"id": "language", "kind": "operator", "ref": "language-filter"}, {"id": "clean", "kind": "operator", "ref": "text-cleaner"}, {"id": "space", "kind": "operator", "ref": "whitespace-cleaner"}, {"id": "normalize", "kind": "operator", "ref": "text-normalizer"}], "edges": [["null", "language"], ["language", "clean"], ["clean", "space"], ["space", "normalize"]]}},
        {"code": "knowledge-chunk", "name": "Knowledge Chunk", "description": "将 DocumentIR 切分为可追溯的 SourceChunk。", "definition": {"entry_node": "chunk", "exit_node": "source-chunks", "nodes": [{"id": "chunk", "kind": "operator", "ref": "semantic-chunker"}, {"id": "source-chunks", "kind": "operator", "ref": "source-chunk-builder"}], "edges": [["chunk", "source-chunks"]]}},
    )


def builtin_flow_definition(output_types: list[str]) -> dict[str, Any]:
    nodes: list[dict[str, Any]] = [
        {"id": "reviewed-input", "kind": "operator", "ref": "reviewed-source-chunk-input"},
    ]
    edges: list[list[str]] = []
    generators = {"text": "prompt-generator", "qa": "qa-generator", "graph": "graph-extractor"}
    for raw_kind in output_types:
        kind = "graph:triple" if raw_kind == "graph" else raw_kind
        family, _, mode = kind.partition(":")
        generator = f"generate-{kind}"; evaluator = f"evaluate-{kind}"; quality_filter = f"filter-{kind}"
        binding = f"bind-{kind}"; validator = f"validate-{kind}"; quality = f"quality-{kind}"; diff = f"diff-{kind}"; sink = f"sink-{kind}"
        generator_params: dict[str, Any] = {"knowledge_type": family}
        if mode:
            generator_params["graph_mode"] = mode
        generator_ref = generators.get(family, "structured-knowledge-generator")
        graph_prefix: list[dict[str, Any]] = []
        graph_edges: list[list[str]] = []
        if kind == "graph:triple":
            graph_prefix = [
                {"id": f"entities-{kind}", "kind": "operator", "ref": "entity-extractor", "params": {"knowledge_type": "graph", "graph_mode": mode, "llm_serving": DEFAULT_LLM_SERVING_ID}},
                {"id": f"literals-{kind}", "kind": "operator", "ref": "literal-detector", "params": {"knowledge_type": "graph", "graph_mode": mode}},
                {"id": f"relations-{kind}", "kind": "operator", "ref": "relation-extractor", "params": {"knowledge_type": "graph", "graph_mode": mode, "llm_serving": DEFAULT_LLM_SERVING_ID}},
            ]
            graph_edges = [["reviewed-input", f"entities-{kind}"], [f"entities-{kind}", f"literals-{kind}"], [f"literals-{kind}", f"relations-{kind}"], [f"relations-{kind}", generator]]
            generator_ref = "triple-builder"
        elif kind == "graph:semantic":
            graph_prefix = [
                {"id": f"entities-{kind}", "kind": "operator", "ref": "entity-extractor", "params": {"knowledge_type": "graph", "graph_mode": mode, "llm_serving": DEFAULT_LLM_SERVING_ID}},
                {"id": f"literals-{kind}", "kind": "operator", "ref": "literal-detector", "params": {"knowledge_type": "graph", "graph_mode": mode}},
                {"id": f"normalize-{kind}", "kind": "operator", "ref": "entity-normalizer", "params": {"knowledge_type": "graph", "graph_mode": mode}},
                {"id": f"relations-{kind}", "kind": "operator", "ref": "relation-extractor", "params": {"knowledge_type": "graph", "graph_mode": mode, "llm_serving": DEFAULT_LLM_SERVING_ID}},
                {"id": f"build-{kind}", "kind": "operator", "ref": "semantic-relation-builder", "params": {"knowledge_type": "graph", "graph_mode": mode}},
            ]
            graph_edges = [["reviewed-input", f"entities-{kind}"], [f"entities-{kind}", f"literals-{kind}"], [f"literals-{kind}", f"normalize-{kind}"], [f"normalize-{kind}", f"relations-{kind}"], [f"relations-{kind}", f"build-{kind}"], [f"build-{kind}", generator]]
            generator_ref = "evidence-binder"
        if generator_ref in {"prompt-generator", "structured-knowledge-generator"}:
            generator_params["prompt_template_revision_id"] = "promptrev_default"
        if generator_ref in {"prompt-generator", "qa-generator", "graph-extractor", "structured-knowledge-generator", "multihop-qa"}:
            generator_params["llm_serving"] = DEFAULT_LLM_SERVING_ID
        nodes.extend(graph_prefix)
        nodes.extend((
            {"id": generator, "kind": "operator", "ref": generator_ref, "params": generator_params},
            {"id": evaluator, "kind": "operator", "ref": "quality-evaluator", "params": {"knowledge_type": family, "graph_mode": mode or None, "quality_profile_revision_id": "qualityrev_default"}},
            {"id": quality_filter, "kind": "operator", "ref": "quality-filter", "params": {"knowledge_type": family, "graph_mode": mode or None, "quality_profile_revision_id": "qualityrev_default"}},
            {"id": binding, "kind": "operator", "ref": "source-binding", "params": {"knowledge_type": family, "graph_mode": mode or None}},
            {"id": validator, "kind": "operator", "ref": "schema-validator", "params": {"knowledge_type": family, "graph_mode": mode or None}},
            {"id": quality, "kind": "operator", "ref": "graph-quality-validator", "params": {"knowledge_type": family, "graph_mode": mode or None}},
            {"id": diff, "kind": "operator", "ref": "knowledge-diff", "params": {"knowledge_type": family, "graph_mode": mode or None}},
            {"id": sink, "kind": "knowledge_sink", "knowledge_type": family, "graph_mode": mode or None, "output_key": kind},
        ))
        edges.extend(graph_edges or [["reviewed-input", generator]])
        edges.extend((
            [generator, evaluator], [evaluator, quality_filter],
            [quality_filter, binding], [binding, validator], [validator, quality], [quality, diff], [diff, sink],
        ))
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
