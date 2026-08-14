"""Stable DataForge knowledge-production catalog.

The catalog deliberately exposes logical DataForge operators instead of the full
DataFlow registry.  Adapter/runtime changes are recorded on OperatorVersion and
never leak into a published Flow definition.
"""
from __future__ import annotations

from typing import Any


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


def _entry(code: str, name: str, category: str, source: str, target: str, adapter: str, *, exposure: str = "canvas", risk: str = "standard", upstream: list[str] | None = None, input_cardinality: str = "one") -> dict[str, Any]:
    return {
        "code": code, "name": name, "description": OPERATOR_DESCRIPTIONS[code], "category": category, "input": source,
        "output": target, "adapter_code": adapter, "exposure": exposure,
        "risk_level": risk, "upstream": upstream or [], "version": 3,
        "input_ports": {"input": {"artifact_type": source, "cardinality": input_cardinality}},
        "output_ports": {"output": {"artifact_type": target, "cardinality": "many"}},
        "input_example": {"input": [_artifact_example(source)]},
        "output_example": {"output": [_artifact_example(target)]},
        "parameter_schema": {"type": "object", "additionalProperties": False},
        "runtime_requirements": {"executor": "dataforge-adapter", "upstream": upstream or []},
    }


CATALOG_SEEDS: tuple[dict[str, Any], ...] = (
    _entry("document-parser", "Document Parser", "文档", "source_file", "document_ir", "document_parser", upstream=["DataForgeNativeParser", "MinerUPipelineHTTPAdapter"]),
    _entry("document-ir-normalizer", "Document IR Normalizer", "文档", "document_ir", "document_ir", "document_ir_normalizer"),
    _entry("null-filter", "Null Filter", "清洗", "document_ir", "document_ir", "content_null_filter", upstream=["ContentNullFilter"]),
    _entry("language-filter", "Language Filter", "清洗", "document_ir", "document_ir", "language_filter", upstream=["LanguageFilter"]),
    _entry("text-cleaner", "Text Cleaner", "清洗", "document_ir", "document_ir", "knowledge_text_cleaner", upstream=["KBCTextCleaner"]),
    _entry("whitespace-cleaner", "Whitespace Cleaner", "清洗", "document_ir", "document_ir", "whitespace_cleaner", upstream=["RemoveExtraSpacesRefiner"]),
    _entry("text-normalizer", "Text Normalizer", "清洗", "document_ir", "document_ir", "text_normalizer", upstream=["TextNormalizationRefiner"]),
    _entry("semantic-chunker", "Semantic Chunker", "切片", "document_ir", "chunk_set", "semantic_chunker", upstream=["KBCChunkGenerator"]),
    _entry("source-chunk-builder", "Source Chunk Builder", "治理", "chunk_set", "source_chunk_set", "source_chunk_builder"),
    _entry("deduplicate", "Candidate Deduplicate", "清洗", "candidate:*", "candidate:*", "candidate_deduplicate", upstream=["MinHashDeduplicateFilter", "SemDeduplicateFilter"]),
    _entry("prompt-generator", "Prompt Generator", "知识生成", "source_chunk_set", "candidate:*", "prompt_generator", risk="advanced", upstream=["PromptedGenerator", "ChunkedPromptedGenerator"]),
    _entry("qa-generator", "QA Generator", "知识生成", "source_chunk_set", "candidate:qa", "qa_generator", upstream=["Text2QAGenerator"]),
    _entry("graph-extractor", "Graph Extractor", "知识生成", "source_chunk_set", "candidate:graph", "graph_extractor"),
    _entry("entity-extractor", "Entity Extractor", "图谱", "source_chunk_set", "entity_candidate_set", "entity_extractor"),
    _entry("relation-extractor", "Relation Extractor", "图谱", "entity_candidate_set", "relation_candidate_set", "relation_extractor"),
    _entry("triple-builder", "Triple Builder", "图谱", "relation_candidate_set", "candidate:graph:triple", "triple_builder"),
    _entry("entity-normalizer", "Entity Normalizer", "图谱", "entity_candidate_set", "entity_candidate_set", "entity_normalizer"),
    _entry("semantic-relation-builder", "Semantic Relation Builder", "图谱", "entity_candidate_set", "semantic_relation_set", "semantic_relation_builder"),
    _entry("evidence-binder", "Evidence Binder", "图谱", "semantic_relation_set", "candidate:graph:semantic", "evidence_binder"),
    _entry("artifact-merge", "Artifact Merge", "治理", "candidate:*", "candidate:*", "artifact_merge", input_cardinality="many"),
    _entry("structured-knowledge-generator", "Structured Knowledge Generator", "知识生成", "source_chunk_set", "candidate:*", "structured_knowledge_generator", upstream=["ChunkedPromptedGenerator"]),
    _entry("quality-evaluator", "Knowledge Evaluator", "质量", "candidate:*", "candidate:*", "quality_evaluator", upstream=["PromptedEvaluator"]),
    _entry("quality-filter", "Knowledge Filter", "质量", "candidate:*", "candidate:*", "quality_filter", upstream=["PromptedFilter"]),
    _entry("source-binding", "Source Binding", "治理", "candidate:*", "candidate:*", "source_binding"),
    _entry("schema-validator", "Schema Validator", "治理", "candidate:*", "candidate:*", "schema_validator"),
    _entry("knowledge-diff", "Knowledge Diff", "治理", "candidate:*", "candidate:*", "knowledge_diff"),
    _entry("mineru-pipeline-gpu-adapter", "MinerU Pipeline GPU Adapter", "Runtime", "source_file", "document_ir", "mineru_pipeline_gpu", exposure="internal", upstream=["MinerUPipelineHTTPAdapter"]),
    _entry("kbc-cleaner-batch", "KBC Cleaner Batch", "Runtime", "document_ir", "document_ir", "kbc_cleaner_batch", exposure="internal", upstream=["KBCTextCleanerBatch"]),
    _entry("kbc-chunker-batch", "KBC Chunker Batch", "Runtime", "document_ir", "chunk_set", "kbc_chunker_batch", exposure="internal", upstream=["KBCChunkGeneratorBatch"]),
    _entry("prompted-refiner", "Knowledge Refiner", "质量", "candidate:*", "candidate:*", "prompted_refiner", exposure="controlled", risk="advanced", upstream=["PromptedRefiner"]),
    _entry("multihop-qa", "Multi-hop QA", "知识生成", "chunk_set", "candidate:qa", "multihop_qa", exposure="controlled", risk="advanced", upstream=["Text2MultiHopQAGenerator"]),
    _entry("pii-compliance", "PII Compliance", "合规", "document_ir", "document_ir", "pii_compliance", exposure="controlled", risk="compliance", upstream=["PIIAnonymizeRefiner"]),
    _entry("kcenter-greedy", "KCenter Greedy", "禁用", "candidate:*", "candidate:*", "disabled", exposure="disabled", risk="disabled", upstream=["KCenterGreedyFilter"]),
    _entry("reference-remover", "Reference Remover", "禁用", "document_ir", "document_ir", "disabled", exposure="disabled", risk="disabled", upstream=["ReferenceRemoverRefiner"]),
)


def catalog_by_code(entries: list[dict[str, Any]] | tuple[dict[str, Any], ...] = CATALOG_SEEDS) -> dict[str, dict[str, Any]]:
    return {entry["code"]: dict(entry) for entry in entries}


def subflow_seeds() -> tuple[dict[str, Any], ...]:
    return (
        {"code": "document-parse", "name": "Document Parse", "definition": {"entry_node": "parser", "exit_node": "normalize", "nodes": [{"id": "parser", "kind": "operator", "ref": "document-parser"}, {"id": "normalize", "kind": "operator", "ref": "document-ir-normalizer"}], "edges": [["parser", "normalize"]]}},
        {"code": "document-clean", "name": "Document Clean", "definition": {"entry_node": "null", "exit_node": "normalize", "nodes": [{"id": "null", "kind": "operator", "ref": "null-filter"}, {"id": "language", "kind": "operator", "ref": "language-filter"}, {"id": "clean", "kind": "operator", "ref": "text-cleaner"}, {"id": "space", "kind": "operator", "ref": "whitespace-cleaner"}, {"id": "normalize", "kind": "operator", "ref": "text-normalizer"}], "edges": [["null", "language"], ["language", "clean"], ["clean", "space"], ["space", "normalize"]]}},
        {"code": "knowledge-chunk", "name": "Knowledge Chunk", "definition": {"entry_node": "chunk", "exit_node": "source-chunks", "nodes": [{"id": "chunk", "kind": "operator", "ref": "semantic-chunker"}, {"id": "source-chunks", "kind": "operator", "ref": "source-chunk-builder"}], "edges": [["chunk", "source-chunks"]]}},
    )


def builtin_flow_definition(output_types: list[str]) -> dict[str, Any]:
    nodes: list[dict[str, Any]] = [
        {"id": "parse", "kind": "subflow", "ref": "document-parse"},
        {"id": "clean", "kind": "subflow", "ref": "document-clean"},
        {"id": "chunk", "kind": "subflow", "ref": "knowledge-chunk"},
    ]
    edges: list[list[str]] = [["parse", "clean"], ["clean", "chunk"]]
    generators = {"text": "prompt-generator", "qa": "qa-generator", "graph": "graph-extractor"}
    for raw_kind in output_types:
        kind = "graph:triple" if raw_kind == "graph" else raw_kind
        family, _, mode = kind.partition(":")
        generator = f"generate-{kind}"; evaluator = f"evaluate-{kind}"; quality_filter = f"filter-{kind}"
        binding = f"bind-{kind}"; validator = f"validate-{kind}"; diff = f"diff-{kind}"; sink = f"sink-{kind}"
        generator_params = {"knowledge_type": family}
        if mode:
            generator_params["graph_mode"] = mode
        generator_ref = generators.get(family, "structured-knowledge-generator")
        graph_prefix: list[dict[str, Any]] = []
        graph_edges: list[list[str]] = []
        if kind == "graph:triple":
            graph_prefix = [
                {"id": f"entities-{kind}", "kind": "operator", "ref": "entity-extractor", "params": {}},
                {"id": f"relations-{kind}", "kind": "operator", "ref": "relation-extractor", "params": {}},
            ]
            graph_edges = [["chunk", f"entities-{kind}"], [f"entities-{kind}", f"relations-{kind}"], [f"relations-{kind}", generator]]
            generator_ref = "triple-builder"
        elif kind == "graph:semantic":
            graph_prefix = [
                {"id": f"entities-{kind}", "kind": "operator", "ref": "entity-extractor", "params": {}},
                {"id": f"normalize-{kind}", "kind": "operator", "ref": "entity-normalizer", "params": {}},
                {"id": f"relations-{kind}", "kind": "operator", "ref": "semantic-relation-builder", "params": {}},
            ]
            graph_edges = [["chunk", f"entities-{kind}"], [f"entities-{kind}", f"normalize-{kind}"], [f"normalize-{kind}", f"relations-{kind}"], [f"relations-{kind}", generator]]
            generator_ref = "evidence-binder"
        if generator_ref in {"prompt-generator", "structured-knowledge-generator"}:
            generator_params["prompt_template_revision_id"] = "promptrev_default"
        nodes.extend(graph_prefix)
        nodes.extend((
            {"id": generator, "kind": "operator", "ref": generator_ref, "params": generator_params},
            {"id": evaluator, "kind": "operator", "ref": "quality-evaluator", "params": {"knowledge_type": family, "graph_mode": mode or None, "quality_profile_revision_id": "qualityrev_default"}},
            {"id": quality_filter, "kind": "operator", "ref": "quality-filter", "params": {"knowledge_type": family, "graph_mode": mode or None, "quality_profile_revision_id": "qualityrev_default"}},
            {"id": binding, "kind": "operator", "ref": "source-binding", "params": {"knowledge_type": family, "graph_mode": mode or None}},
            {"id": validator, "kind": "operator", "ref": "schema-validator", "params": {"knowledge_type": family, "graph_mode": mode or None}},
            {"id": diff, "kind": "operator", "ref": "knowledge-diff", "params": {"knowledge_type": family, "graph_mode": mode or None}},
            {"id": sink, "kind": "knowledge_sink", "knowledge_type": family, "graph_mode": mode or None, "output_key": kind},
        ))
        edges.extend(graph_edges or [["chunk", generator]])
        edges.extend((
            [generator, evaluator], [evaluator, quality_filter],
            [quality_filter, binding], [binding, validator], [validator, diff], [diff, sink],
        ))
    return {"schema_version": 3, "nodes": nodes, "edges": [
        {"source": edge[0], "source_port": "output", "target": edge[1], "target_port": "input"} for edge in edges
    ], "ui": {"positions": {}}}
