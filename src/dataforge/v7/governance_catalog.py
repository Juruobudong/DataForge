"""Explicit reviewed DataFlow 1.0.10 registrations, not registry discovery."""
from copy import deepcopy
from .operators.semantic_contract import MODEL as SEMANTIC_MODEL, PROFILE as SEMANTIC_RESOURCE_PROFILE, REVISION as SEMANTIC_REVISION

TEXT_INPUTS = ["source_chunk_set", "derived_text_set", "candidate:text", "candidate:qa"]
TEXT_OUTPUTS = {"source_chunk_set": "derived_text_set", "derived_text_set": "derived_text_set",
                "candidate:text": "candidate:text", "candidate:qa": "candidate:qa"}
SCORES = ["question_quality", "answer_alignment", "answer_verifiability", "downstream_value"]
GENERIC_SCORE = "evaluation_score"
SEMANTIC_FIELDS = ["semantic_duplicate", "semantic_similarity"]
PII_V1_LOCK_DIGEST = "351bd34b15dc5a9d000b21ab3a108ad750ac863aaa12a54995aab934272240d8"
PII_LOCK_DIGEST = "77d7160571a381827ebcde6c1d02fad0bd782e012798458b57b70972a98a5555"
# Updated together with runtime/dataflow/requirements-semantic-v1.lock.
SEMANTIC_LOCK_DIGEST = "59032efef5ff7783d5023f371a578bffc48e204991bde9e38bd767181fa8c447"
SHORT_SUMMARIES = {
    "MeanWordLengthFilter": "根据平均词长过滤异常文本。",
    "LexicalDiversityFilter": "根据词汇丰富度筛选文本，无法评分时明确标记。",
    "UniqueWordsFilter": "过滤唯一词比例过低的重复文本。",
    "WatermarkFilter": "删除包含指定水印关键词的整条文本。",
    "HtmlEntityFilter": "删除包含HTML实体残留的整条文本。",
    "BlocklistFilter": "按词表命中次数过滤不需要的文本。",
    "PresidioFilter": "检测英文个人信息，并按检测数量保留或过滤记录。",
    "PIIAnonymizeRefiner": "识别英文个人信息，生成匿名化正文副本。",
    "Text2MultiHopQAGenerator": "组合单个文档块内的关联事实，生成多跳问答。",
    "Text2QASampleEvaluator": "为问答提供四维质量评分与反馈。",
    "GeneralFilter": "按全部满足的业务条件筛选文本或问答。",
    "PromptedEvaluator": "按已发布评估标准为文本或问答附加单维评分和理由，不直接过滤。",
    "SemDeduplicateFilter": "在当前输入内标记语义重复关系，不直接删除记录。",
    "SentenceNumberFilter": "按中英文句子数量过滤异常文本，原文和证据保持不变。",
    "SymbolWordRatioFilter": "按Unicode标点和符号占比过滤噪声文本，原文和证据保持不变。",
    "RemoveRepetitionsPunctuationRefiner": "安全折叠正文中的重复标点并保留URL、Markdown与代码结构。",
}


def number(title, default, minimum=0, maximum=None, integer=False):
    value = {"title": title, "type": "integer" if integer else "number", "default": default, "minimum": minimum}
    if maximum is not None:
        value["maximum"] = maximum
    return value


LLM = {"type": "string", "title": "模型服务", "x-dataforge-ui": {"widget": "llm-serving-selector"}}
RULE_SCHEMA = {"type": "array", "title": "保留条件（全部满足）", "minItems": 1, "maxItems": 32,
    "x-dataforge-ui": {"widget": "filter-rules"}, "items": {"type": "object", "additionalProperties": False,
    "required": ["field", "operator"], "properties": {
        "field": {"type": "string", "enum": ["text", "question", "answer", "length", *SCORES, GENERIC_SCORE, *SEMANTIC_FIELDS]},
        "evaluation_node": {"type": "string", "minLength": 1},
        "deduplication_node": {"type": "string", "minLength": 1},
        "operator": {"type": "string", "enum": ["eq", "ne", "gt", "ge", "lt", "le", "contains", "in", "is_empty", "not_empty"]},
        "value": {"type": ["string", "number", "boolean", "null", "array"], "items": {"type": ["string", "number", "boolean"]}}}}}

GOVERNANCE_SPECS = {
    "MeanWordLengthFilter": ("平均词长过滤器", "文本处理", "general_text", "filter", "按空白分词后的平均词长保留文本；不等于中文分词质量检查。", {
        "min_length": number("最小平均词长", 3), "max_length": number("最大平均词长", 10)}),
    "LexicalDiversityFilter": ("词汇丰富度过滤器", "文本处理", "general_text", "lexical", "根据MTLD/HDD筛选词汇丰富度；51–999词外保留并标记未评分，不代表质量通过。", {
        "min_mtld": number("最低MTLD", 50), "max_mtld": number("最高MTLD", 99999),
        "min_hdd": number("最低HDD", .8, 0, 1), "max_hdd": number("最高HDD", 1, 0, 1)}),
    "UniqueWordsFilter": ("唯一词比例过滤器", "文本处理", "general_text", "filter", "按空白分词计算唯一词比例，整条过滤低于阈值的文本；不提供中文分词。", {
        "threshold": number("最小唯一词比例", .1, 0, 1)}),
    "WatermarkFilter": ("水印文本过滤器", "文本处理", "general_text", "filter", "删除包含指定水印关键词的整条文本，不是移除水印后保留正文。", {
        "watermarks": {"type": "array", "items": {"type": "string", "minLength": 1}, "minItems": 1,
                       "default": ["Copyright", "Watermark", "Confidential"], "title": "水印关键词"}}),
    "HtmlEntityFilter": ("HTML 实体过滤器", "文本处理", "general_text", "filter", "删除包含HTML实体残留的整条文本，不是清除实体后保留文本。", {}),
    "BlocklistFilter": ("黑名单内容过滤器", "隐私与安全", "general_text", "blocklist", "按上游分词后的词表命中次数过滤；不做中文子串匹配。自定义词表替代内置词表。", {
        "language": {"type": "string", "enum": ["en", "zh"], "default": "en", "title": "内置词表语言"},
        "threshold": number("最大允许命中次数", 0, integer=True),
        "use_tokenizer": {"type": "boolean", "default": False, "title": "使用NLTK分词"},
        "blocklist": {"type": "array", "items": {"type": "string", "minLength": 1}, "maxItems": 10000, "title": "自定义词表（留空使用内置）", "default": []}}),
    "PresidioFilter": ("隐私信息过滤器", "隐私与安全", "general_text", "privacy_filter", "英文PII检测，默认仅保留未检测到PII的记录；不是中文身份证检测或风险概率。", {
        "min_score": number("最少PII实体数量", 0, integer=True), "max_score": number("最多PII实体数量", 0, integer=True)}),
    "PIIAnonymizeRefiner": ("PII 匿名化器", "文本优化", "general_text", "anonymize", "使用英文NER生成匿名化副本；原始Chunk和Evidence保留，不代表全库脱敏，不支持中文病历保证。", {}),
    "Text2MultiHopQAGenerator": ("多跳问答生成器", "知识生成", "core_text", "multihop", "在单Chunk内组合事实生成多跳问答，不跨文档；上游要求100–200000字符及至少两处句号。", {
        "llm_serving": LLM, "num_q": number("每块最多问答数", 5, 1, 10, True),
        "lang": {"type": "string", "enum": ["zh", "en"], "default": "zh", "title": "生成语言"}}),
    "Text2QASampleEvaluator": ("QA 质量评估器", "质量治理", "core_text", "evaluate", "对QA进行四维1–5分模型评分并保留反馈；可核验性评分不是基于原始证据的事实核验。", {"llm_serving": LLM}),
    "GeneralFilter": ("通用条件过滤器", "质量治理", "core_text", "conditions", "按全部满足的业务条件保留记录；支持正文、QA/通用评分和语义重复标记，不接受Python或表达式。", {"rules": RULE_SCHEMA}),
    "PromptedEvaluator": ("通用质量评估器", "质量治理", "core_text", "evaluate_generic", "使用已发布评估标准对文本或问答输出单维1–5分及理由；DataForge适配为强类型结果，不使用上游有损整数输出。", {
        "llm_serving": LLM,
        "prompt_template_revision_id": {"type": "string", "title": "评估标准 Prompt", "x-dataforge-ui": {"widget": "prompt-template-selector"}},
    }),
    "SemDeduplicateFilter": ("语义重复标记器", "质量治理", "general_text", "semantic", "使用固定多语言CPU模型在当前Artifact内标记语义重复；DataForge适配保留全部记录，不执行上游直接删行行为。", {
        "scope": {"type": "string", "enum": ["source_version", "flow_input"], "default": "source_version", "title": "比较范围"},
        "threshold": number("语义相似度阈值", .95, 0, 1),
    }),
    "SentenceNumberFilter": ("句子数量过滤器", "文本处理", "general_text", "sentence", "按中英文句末和换行统计句子数；DataForge先生成分析副本，再由DataFlow过滤，原文不改写。", {
        "min_sentences": number("最少句子数", 1, 1, 500, True),
        "max_sentences": number("最多句子数", 500, 1, 500, True),
        "semicolon_as_boundary": {"type": "boolean", "default": False, "title": "将中英文分号视为句末"},
    }),
    "SymbolWordRatioFilter": ("符号占比过滤器", "文本处理", "general_text", "symbol_ratio", "按非空白字符中的Unicode标点/符号比例过滤噪声；DataForge分析副本映射到上游规则，原文不改写。", {
        "threshold": number("最大符号占比", .60, 0, 1),
    }),
    "RemoveRepetitionsPunctuationRefiner": ("重复标点清理器", "文本优化", "general_text", "punctuation", "仅折叠prose-safe重复标点；URL、Markdown、代码常用符号保持不变，来源与Evidence不改写。", {}),
}


def text_ports(item):
    item["input"] = item["output"] = "text_record_set"
    item["input_ports"]["input"].update(artifact_type="text_record_set", accepted_types=deepcopy(TEXT_INPUTS))
    item["output_ports"]["output"].update(artifact_type="text_record_set", output_by_input=deepcopy(TEXT_OUTPUTS))


def extend_catalog(entries, package, package_version, package_digest, lock_digest):
    entries = deepcopy(list(entries))
    legacy = []
    base = deepcopy(next(item for item in entries if item["code"] == "ContentNullFilter"))
    for item in entries:
        code = item["code"]
        if code in {"ContentNullFilter", "CharNumberFilter", "SpecialCharacterFilter"}:
            legacy.append(deepcopy(item)); item["version"] += 1
            item["adapter_code"] = item["runtime_requirements"]["adapter_version"] = "governance-filter-v1"
            text_ports(item)
        if code in {"Text2QAGenerator", "text-knowledge-mapper", "prompt-generator", "structured-knowledge-generator"}:
            legacy.append(deepcopy(item)); item["version"] += 1
            item["input_ports"]["input"]["accepted_types"] = ["source_chunk_set", "derived_text_set"]
            item["runtime_requirements"]["derived_text"] = True
            # Omission means the system's current default; Compiler freezes the
            # concrete Serving. Historical schemas remain in the saved version.
            schema = item["parameter_schema"]
            if "llm_serving" in schema.get("properties", {}):
                schema["properties"]["llm_serving"].pop("default", None)
                schema["required"] = [key for key in schema.get("required", []) if key != "llm_serving"]
            if code == "Text2QAGenerator":
                item["adapter_code"] = item["runtime_requirements"]["adapter_version"] = "source-chunk-to-qa-v3"
        if item.get("source") == "dataflow":
            item["subcategory"] = ("知识生成" if code == "Text2QAGenerator" else "文本优化" if code == "PromptedRefiner"
                                   else "去重" if "Deduplicate" in code else "质量治理" if code == "PromptedFilter" else "文本处理")
    for code, (name, category, namespace, adapter, description, properties) in GOVERNANCE_SPECS.items():
        item = deepcopy(base)
        uses_llm = adapter in {"multihop", "evaluate", "evaluate_generic"}
        item.update(code=code, name=code, display_name_zh=name, version=1, subcategory=category,
                    summary=SHORT_SUMMARIES[code], description=description, scenarios=[description], upstream=[code],
                    adapter_code=f"governance-{adapter}-v1", knowledge_types=["qa"] if uses_llm else ["text", "qa"],
                    parameter_schema={"type": "object", "properties": deepcopy(properties), "additionalProperties": False},
                    parameter_docs={key: value.get("description", value["title"]) for key, value in properties.items()})
        if adapter in {"conditions", "evaluate_generic"}:
            required = []
            if adapter == "conditions":
                required.append("rules")
            else:
                required.append("prompt_template_revision_id")
            item["parameter_schema"]["required"] = required
        if adapter == "conditions":
            item["version"] = 2
        item["source"], item["catalog_group"] = "dataflow", "dataflow_featured"
        item["runtime_requirements"] = {"driver": "dataflow",
            "executor": "dataflow-llm" if uses_llm else "dataflow-storage",
            "package": package, "package_version": package_version, "package_digest": package_digest,
            "dependency_lock_digest": lock_digest, "implementation": f"dataflow.operators.{namespace}:{code}",
            "adapter_version": item["adapter_code"], "uses_llm": uses_llm, "resources": "CPU",
            "data_behavior": "生成匿名化副本，原始Evidence保留" if adapter == "anonymize" else "生成问答" if adapter == "multihop" else "附加评分，不改变正文" if adapter in {"evaluate", "evaluate_generic"} else "附加语义重复标记，不删除记录" if adapter == "semantic" else "生成正文副本，原始Evidence保留" if adapter == "punctuation" else "只保留或过滤记录",
            "limitations": description, "preserve_fields": deepcopy(base["runtime_requirements"]["preserve_fields"])}
        if adapter in {"privacy_filter", "anonymize", "blocklist"}:
            item["runtime_requirements"]["resource_profile"] = "pii-en-v1" if adapter != "blocklist" else "nltk-v1"
            item["runtime_requirements"]["dependency_lock_digest"] = PII_V1_LOCK_DIGEST
        if adapter in {"privacy_filter", "anonymize"}:
            item["runtime_requirements"].update(language="en", model="dslim/bert-base-NER", sensitive=True)
        if adapter == "semantic":
            item["runtime_requirements"].update(
                dependency_lock_digest=SEMANTIC_LOCK_DIGEST,
                resource_profile=SEMANTIC_RESOURCE_PROFILE,
                model=SEMANTIC_MODEL,
                model_revision=SEMANTIC_REVISION,
                language="multilingual",
                resources="CPU",
            )
        if adapter in {"evaluate_generic", "semantic", "sentence", "symbol_ratio", "punctuation"}:
            item["runtime_requirements"]["adapted_behavior"] = description
            example = {"source_knowledge_id": "candidate-example", "source_chunk_id": "chunk-example",
                       "source_version_ids": ["version-example"], "canonical_content": "设备维护应记录检查结果。",
                       "data_json": {}, "evidence_text": "设备维护应记录检查结果。", "anchor_json": {"page": 1}}
            item["input_example"], item["output_example"] = {"input": [example]}, {"output": [deepcopy(example)]}
        if adapter == "multihop":
            item["input"] = "source_chunk_set"; item["output"] = "candidate:qa"
            item["input_ports"]["input"].update(artifact_type="source_chunk_set", accepted_types=["source_chunk_set", "derived_text_set"])
            item["output_ports"]["output"]["artifact_type"] = "candidate:qa"
        elif adapter in {"evaluate", "evaluate_generic"}:
            item["input"] = item["output"] = "candidate:qa"
            if adapter == "evaluate_generic":
                item["knowledge_types"] = ["text", "qa"]
                item["input"] = item["output"] = "candidate:*"
                item["input_ports"]["input"].update(artifact_type="candidate:*", accepted_types=["candidate:text", "candidate:qa"])
                item["output_ports"]["output"].update(artifact_type="candidate:*", output_by_input={"candidate:text": "candidate:text", "candidate:qa": "candidate:qa"})
            else:
                item["input_ports"]["input"]["artifact_type"] = "candidate:qa"
                item["output_ports"]["output"]["artifact_type"] = "candidate:qa"
        else:
            text_ports(item)
        if adapter in {"privacy_filter", "anonymize", "blocklist"}:
            # Keep frozen v1 snapshots and installed environments unchanged.
            legacy.append(deepcopy(item))
            item["version"] = 2
            item["runtime_requirements"]["dependency_lock_digest"] = PII_LOCK_DIGEST
        entries.append(item)
    return tuple(entries), tuple(legacy)
