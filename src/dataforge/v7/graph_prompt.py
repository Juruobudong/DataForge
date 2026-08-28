"""Schema-driven extraction prompts for graph operators.

The default prompt is assembled from the template graph schema. Version 7 adds
per-node business guidance while retaining system-owned inputs and JSON format.
Legacy versions retain their custom-body contract and Schema Validator.
"""
from __future__ import annotations

from typing import Any

from .graph_schema import GraphExtractionConfig, normalize_graph_config, prompt_blocks

GRAPH_GUIDANCE_VERSIONS = {"entity-extractor": 7, "relation-extractor": 7}


def graph_config_for_node(config: GraphExtractionConfig, params: dict[str, Any], *, relation: bool = False,
                          governed_prompt: bool = False) -> GraphExtractionConfig:
    """Shared effective Schema for execution and stateless preview."""
    raw = config.to_dict()
    selected_entities = {str(item) for item in (params.get("entity_types") or []) if str(item)}
    if selected_entities and params.get("entity_type_scope") != "all":
        raw["entity_types"] = [item for item in raw["entity_types"] if item["code"] in selected_entities]
        if not relation:
            raw["relation_types"] = []
    selected_relations = {str(item) for item in (params.get("relation_types") or []) if str(item)}
    if selected_relations:
        raw["relation_types"] = [item for item in raw["relation_types"] if item["code"] in selected_relations]
    if relation and params.get("unknown_relation_policy"):
        raw["unknown_relation_policy"] = params["unknown_relation_policy"]
    if not relation and params.get("unknown_entity_policy"):
        raw["unknown_entity_policy"] = params["unknown_entity_policy"]
    if governed_prompt:
        raw["prompt"] = {"mode": "generated", "body": None}
    elif not relation and params.get("prompt_mode"):
        raw.setdefault("prompt", {})["mode"] = params["prompt_mode"]
    constraints = {str(item.get("relation_type")): item for item in (params.get("relation_constraints") or [])
                   if isinstance(item, dict) and item.get("relation_type")}
    for item in raw.get("relation_types") or []:
        constraint = constraints.get(str(item.get("code")))
        if constraint:
            item["source_types"] = list(constraint.get("source_types") or [])
            item["target_types"] = list(constraint.get("target_types") or [])
    return normalize_graph_config(raw)


def _instruction_block(instructions: str) -> str:
    if not isinstance(instructions, str):
        raise ValueError("抽取要求必须是字符串")
    if not instructions.strip():
        return ""
    return ("节点业务抽取要求（在上述类型、原文语言及系统输出格式约束内执行；无匹配内容时返回空数组）：\n"
            + instructions.strip() + "\n\n")

_ENTITY_SYSTEM = (
    "你是严谨的医学/业务知识实体抽取器。实体名称、描述和别名必须使用当前来源分块的原文语言，"
    "不得翻译；中文原文用中文，英文原文用英文。只返回符合要求的 JSON 对象，不要输出 Markdown 或解释。"
)
_RELATION_SYSTEM = (
    "你是严谨的医学/业务知识关系抽取器。关系 label、描述、关键词以及 source/target 必须使用当前来源分块的原文语言，"
    "不得翻译；中文原文用中文，英文原文用英文。只返回符合要求的 JSON 对象，不要输出 Markdown 或解释。"
)

_ENTITY_JSON_SCHEMA = {
    "type": "object",
    "required": ["entities"],
    "properties": {
        "entities": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["name", "type"],
                "properties": {
                    "name": {"type": "string"},
                    "type": {"type": "string"},
                    "description": {"type": "string"},
                    "aliases": {"type": "array", "items": {"type": "string"}},
                    "object_kind": {"type": "string", "enum": ["entity", "literal"]},
                    "confidence": {"type": "number"},
                },
            },
        }
    },
}

_RELATION_JSON_SCHEMA = {
    "type": "object",
    "required": ["relations"],
    "properties": {
        "relations": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["source", "type", "label", "target"],
                "properties": {
                    "source": {"type": "string"},
                    "type": {"type": "string"},
                    "label": {"type": "string"},
                    "target": {"type": "string"},
                    "description": {"type": "string"},
                    "keywords": {"type": "array", "items": {"type": "string"}},
                    "weight": {"type": "number"},
                },
            },
        }
    },
}


def _literal_rules(enabled: tuple[str, ...]) -> str:
    names = {
        "number": "纯数字", "range": "数值范围", "percentage": "百分比", "duration": "时长",
        "temperature": "温度", "dosage": "剂量", "date": "日期", "boolean": "布尔值", "string": "文本值",
    }
    return "、".join(names.get(item, item) for item in enabled) or "（无）"


def render_entity_prompt(config: GraphExtractionConfig, source_chunk: str, *, instructions: str = "") -> str:
    """Generated prompt requesting typed entities from one source chunk."""
    blocks = prompt_blocks(config)
    forbidden = (
        "禁止将以下内容作为实体：纯数字、数值范围、百分比、剂量、温度、时长、日期、页码、编号。"
    )
    if config.entity_types:
        unknown_note = {
            "reject": "只允许输出以上实体类型；任何不属于这些类型的实体都必须忽略，不得降级为“其他”。",
            "other": "不属于以上实体类型的实体归入“其他”，type 使用 other。",
            "suggest": "可以提出新的实体类型建议，type 使用英文 code。",
        }[config.unknown_entity_policy]
        type_block = f"仅允许抽取以下实体类型：\n\n{blocks['entity_types']}\n\n"
        type_note = f"{unknown_note}\n\n"
    else:
        # Empty schema means "unconstrained types": extract free-form entities
        # in the source language instead of rejecting everything.
        type_block = ""
        type_note = (
            "未定义实体类型，请自由抽取当前分块中的全部实体。为每个实体给出："
            "name（保留原文名称）、type（使用与原文相同语言的简洁、稳定类型名称，例如中文原文使用“疾病”“药物”，"
            "英文原文使用 disease、drug）、description（使用原文语言的简短描述）、"
            "aliases（仅保留原文出现或同语言的别名）、confidence。\n\n"
        )
    return (
        f"{type_block}"
        f"{forbidden}\n\n"
        "可识别为字面值的内容（数字、范围、百分比、剂量、温度、时长、日期等）请在 object_kind 标记为 literal，"
        "不要当作实体输出。\n\n"
        f"{type_note}"
        "除 Graph Schema 要求的 type 技术 code 外，所有实体文本字段必须保持当前来源分块的原文语言，不得翻译；"
        "中文原文用中文，英文原文用英文。\n\n"
        f"{_instruction_block(instructions)}"
        "当前来源分块：\n"
        f"{source_chunk}\n\n"
        "返回 JSON 对象，其 entities 数组的每一项都符合以下 JSON Schema：\n"
        f"{_schema_text(_ENTITY_JSON_SCHEMA)}"
    )


def render_relation_prompt(config: GraphExtractionConfig, entities: list[str], source_chunk: str, *, instructions: str = "") -> str:
    """Generated prompt requesting relations between already-extracted entities."""
    blocks = prompt_blocks(config)
    entity_list = "、".join(entities) if entities else "（无）"
    if config.relation_types:
        unknown_note = {
            "reject": "只允许输出以上关系类型；任何不属于这些类型的关系都必须忽略。",
            "other": "不属于以上关系类型的关系归入“其他”，type 使用 other。",
            "suggest": "可以提出新的关系类型建议，type 使用英文 code。",
        }[config.unknown_relation_policy]
        type_block = (
            f"仅允许以下关系类型：\n\n{blocks['relation_types']}\n\n"
            f"关系端点约束（source 类型 → target 类型）：\n{blocks['relation_constraints']}\n\n"
            f"{unknown_note}\n\n"
        )
    else:
        # Empty schema means "unconstrained types": extract free-form relations
        # in the source language instead of rejecting everything.
        type_block = (
            "未定义关系类型，请自由抽取已抽取实体之间的关系。"
            "type 和 label 使用与原文相同语言的简洁、稳定关系词，并为每条关系给出使用原文语言的简短 description。\n\n"
        )
    return (
        f"{type_block}"
        f"已抽取实体（只能从其中选择 source 与 target）：{entity_list}\n\n"
        "source 与 target 必须严格使用上方实体的原文名称。已定义 Graph Schema 时，type 使用 Schema 中的技术 code；"
        "无论是否定义 Schema，label 都必须是原文语言的简洁关系词；未定义关系类型时，type 与 label 使用相同的原文关系词。"
        "关系 description 和 keywords 同样保持原文语言，不得翻译；"
        "中文原文用中文，英文原文用英文。"
        "禁止把数字、范围、剂量等字面值作为关系的 target。\n\n"
        f"{_instruction_block(instructions)}"
        "当前来源分块：\n"
        f"{source_chunk}\n\n"
        "返回 JSON 对象，其 relations 数组的每一项都符合以下 JSON Schema：\n"
        f"{_schema_text(_RELATION_JSON_SCHEMA)}"
    )


def render_custom_prompt(config: GraphExtractionConfig, source_chunk: str, variables: dict[str, Any] | None = None) -> str:
    """Expand a custom prompt body using ``{{variable}}`` placeholders."""
    body = config.prompt_body or ""
    blocks = prompt_blocks(config)
    values = {
        "entity_types": blocks["entity_types"],
        "relation_types": blocks["relation_types"],
        "relation_constraints": blocks["relation_constraints"],
        "literal_rules": _literal_rules(config.literal_policy.enabled_datatypes),
        "source_chunk": source_chunk,
    }
    values.update(variables or {})
    rendered = body
    for key, value in values.items():
        rendered = rendered.replace("{{" + key + "}}", str(value))
    return rendered


def _schema_text(schema: dict[str, Any]) -> str:
    import json
    return json.dumps(schema, ensure_ascii=False)


def entity_prompt_for(config: GraphExtractionConfig, source_chunk: str) -> tuple[str, str]:
    """Return ``(system, user)`` prompt pair for entity extraction."""
    body = render_custom_prompt(config, source_chunk) if config.prompt_mode == "custom" else render_entity_prompt(config, source_chunk)
    return _ENTITY_SYSTEM, body


def relation_prompt_for(config: GraphExtractionConfig, entities: list[str], source_chunk: str) -> tuple[str, str]:
    """Return ``(system, user)`` prompt pair for relation extraction."""
    if config.prompt_mode == "custom":
        body = render_custom_prompt(config, source_chunk, {"entities": "、".join(entities)})
    else:
        body = render_relation_prompt(config, entities, source_chunk)
    return _RELATION_SYSTEM, body


def graph_node_prompt(config: GraphExtractionConfig, params: dict[str, Any], operator_code: str,
                      operator_version: int | None, source_chunk: str,
                      entities: list[str] | None = None) -> tuple[str, str]:
    """Render the actual request; only the new version accepts business guidance."""
    governed = GRAPH_GUIDANCE_VERSIONS.get(operator_code) == operator_version
    if operator_code == "entity-extractor":
        if governed:
            return _ENTITY_SYSTEM, render_entity_prompt(config, source_chunk, instructions=params.get("extraction_instructions", ""))
        return entity_prompt_for(config, source_chunk)
    if operator_code == "relation-extractor":
        if governed:
            return _RELATION_SYSTEM, render_relation_prompt(config, entities or [], source_chunk, instructions=params.get("extraction_instructions", ""))
        return relation_prompt_for(config, entities or [], source_chunk)
    raise ValueError("仅实体抽取器、关系抽取器支持图谱提示词预览")
