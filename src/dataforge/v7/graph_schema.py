"""Per-template graph extraction schema.

The graph schema is the production rule set of a knowledge-flow template.  It
lives in ``definition_json.graph_config`` of a ``KnowledgeFlowTemplateRevision``
and is snapshotted onto a ``KnowledgeLibrary`` when knowledge is produced.

Internal codes are stable English identifiers; ``label``/``description`` are the
Chinese business display text.  No business page may render an English code
directly.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from .graph_literal import LITERAL_DATATYPES

_CODE_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_UNKNOWN_POLICIES = ("reject", "other", "suggest")
_PROMPT_MODES = ("generated", "custom")


class GraphSchemaError(ValueError):
    """A graph extraction configuration is structurally invalid."""


@dataclass(frozen=True)
class EntityTypeDefinition:
    code: str
    label: str
    description: str = ""


@dataclass(frozen=True)
class RelationTypeDefinition:
    code: str
    label: str
    description: str = ""
    source_types: tuple[str, ...] = ()
    target_types: tuple[str, ...] = ()


@dataclass(frozen=True)
class LiteralPolicy:
    enabled_datatypes: tuple[str, ...] = LITERAL_DATATYPES


@dataclass(frozen=True)
class GraphExtractionConfig:
    entity_types: tuple[EntityTypeDefinition, ...] = ()
    relation_types: tuple[RelationTypeDefinition, ...] = ()
    literal_policy: LiteralPolicy = field(default_factory=LiteralPolicy)
    unknown_entity_policy: str = "reject"
    unknown_relation_policy: str = "reject"
    prompt_mode: str = "generated"
    prompt_body: str | None = None

    def entity_by_code(self, code: str) -> EntityTypeDefinition | None:
        return next((item for item in self.entity_types if item.code == code), None)

    def relation_by_code(self, code: str) -> RelationTypeDefinition | None:
        return next((item for item in self.relation_types if item.code == code), None)

    def entity_codes(self) -> frozenset[str]:
        return frozenset(item.code for item in self.entity_types)

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity_types": [{"code": item.code, "label": item.label, "description": item.description}
                             for item in self.entity_types],
            "relation_types": [{"code": item.code, "label": item.label, "description": item.description,
                                "source_types": list(item.source_types), "target_types": list(item.target_types)}
                               for item in self.relation_types],
            "literal_policy": {"enabled_datatypes": list(self.literal_policy.enabled_datatypes)},
            "unknown_entity_policy": self.unknown_entity_policy,
            "unknown_relation_policy": self.unknown_relation_policy,
            "prompt": {"mode": self.prompt_mode, "body": self.prompt_body},
        }


def _require_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise GraphSchemaError(f"{field_name} 不能为空")
    return value.strip()


def _require_code(value: Any, field_name: str) -> str:
    code = _require_text(value, field_name)
    if not _CODE_RE.match(code):
        raise GraphSchemaError(f"{field_name} 必须是英文稳定标识（小写字母/数字/下划线，字母开头）：{code!r}")
    return code


def _optional_text(value: Any, field_name: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise GraphSchemaError(f"{field_name} 必须是字符串")
    return value.strip()


def _optional_text_list(value: Any, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise GraphSchemaError(f"{field_name} 必须是字符串数组")
    return tuple(item.strip() for item in value)


def _normalize_entity_type(raw: Any, seen: set[str]) -> EntityTypeDefinition:
    if not isinstance(raw, dict):
        raise GraphSchemaError("entity_types 每一项必须是对象")
    code = _require_code(raw.get("code"), "实体类型 code")
    if code in seen:
        raise GraphSchemaError(f"实体类型 code 重复：{code}")
    seen.add(code)
    return EntityTypeDefinition(
        code=code,
        label=_require_text(raw.get("label"), f"实体类型 {code} 的 label"),
        description=_optional_text(raw.get("description"), f"实体类型 {code} 的 description"),
    )


def _normalize_relation_type(raw: Any, entity_codes: frozenset[str], seen: set[str]) -> RelationTypeDefinition:
    if not isinstance(raw, dict):
        raise GraphSchemaError("relation_types 每一项必须是对象")
    code = _require_code(raw.get("code"), "关系类型 code")
    if code in seen:
        raise GraphSchemaError(f"关系类型 code 重复：{code}")
    seen.add(code)
    source_types = _optional_text_list(raw.get("source_types"), f"关系类型 {code} 的 source_types")
    target_types = _optional_text_list(raw.get("target_types"), f"关系类型 {code} 的 target_types")
    for role, values in (("source_types", source_types), ("target_types", target_types)):
        unknown = [item for item in values if item not in entity_codes]
        if unknown:
            raise GraphSchemaError(f"关系类型 {code} 的 {role} 引用了未声明的实体类型：{'、'.join(unknown)}")
    return RelationTypeDefinition(code=code, label=_require_text(raw.get("label"), f"关系类型 {code} 的 label"),
                                  description=_optional_text(raw.get("description"), f"关系类型 {code} 的 description"),
                                  source_types=source_types, target_types=target_types)


def normalize_graph_config(raw: Any) -> GraphExtractionConfig:
    """Validate and normalize a ``graph_config`` payload.

    Returns a frozen :class:`GraphExtractionConfig`; raises
    :class:`GraphSchemaError` for any structural problem.
    """
    if raw is None:
        return GraphExtractionConfig()
    if not isinstance(raw, dict):
        raise GraphSchemaError("graph_config 必须是对象")
    entity_seen: set[str] = set()
    entity_types = tuple(_normalize_entity_type(item, entity_seen) for item in (raw.get("entity_types") or []))
    relation_seen: set[str] = set()
    entity_codes = frozenset(item.code for item in entity_types)
    relation_types = tuple(_normalize_relation_type(item, entity_codes, relation_seen) for item in (raw.get("relation_types") or []))

    literal_policy_raw = raw.get("literal_policy") or {}
    enabled = _optional_text_list(literal_policy_raw.get("enabled_datatypes"), "literal_policy.enabled_datatypes")
    if not enabled:
        enabled = LITERAL_DATATYPES
    invalid = [item for item in enabled if item not in LITERAL_DATATYPES]
    if invalid:
        raise GraphSchemaError(f"literal_policy 包含未知 datatype：{'、'.join(invalid)}")

    unknown_entity_policy = str(raw.get("unknown_entity_policy") or "reject").strip()
    unknown_relation_policy = str(raw.get("unknown_relation_policy") or "reject").strip()
    if unknown_entity_policy not in _UNKNOWN_POLICIES:
        raise GraphSchemaError(f"unknown_entity_policy 必须是 {'/'.join(_UNKNOWN_POLICIES)} 之一")
    if unknown_relation_policy not in _UNKNOWN_POLICIES:
        raise GraphSchemaError(f"unknown_relation_policy 必须是 {'/'.join(_UNKNOWN_POLICIES)} 之一")

    prompt_raw = raw.get("prompt") or {}
    prompt_mode = str(prompt_raw.get("mode") or "generated").strip()
    if prompt_mode not in _PROMPT_MODES:
        raise GraphSchemaError(f"prompt.mode 必须是 {'/'.join(_PROMPT_MODES)} 之一")
    prompt_body = prompt_raw.get("body")
    if prompt_mode == "custom" and (not isinstance(prompt_body, str) or not prompt_body.strip()):
        raise GraphSchemaError("prompt.mode=custom 时必须提供非空 prompt.body")

    return GraphExtractionConfig(
        entity_types=entity_types,
        relation_types=relation_types,
        literal_policy=LiteralPolicy(enabled_datatypes=enabled),
        unknown_entity_policy=unknown_entity_policy,
        unknown_relation_policy=unknown_relation_policy,
        prompt_mode=prompt_mode,
        prompt_body=prompt_body.strip() if isinstance(prompt_body, str) else None,
    )


def graph_config_has_schema(config: GraphExtractionConfig) -> bool:
    return bool(config.entity_types or config.relation_types)


def schema_hash(config: GraphExtractionConfig) -> str:
    """Stable digest over the normalised schema (types, relations, literal policy)."""
    payload = {
        "entity_types": [{"code": item.code, "label": item.label, "description": item.description}
                         for item in config.entity_types],
        "relation_types": [{"code": item.code, "label": item.label, "description": item.description,
                            "source_types": list(item.source_types), "target_types": list(item.target_types)}
                           for item in config.relation_types],
        "literal_policy": {"enabled_datatypes": list(config.literal_policy.enabled_datatypes)},
    }
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def prompt_blocks(config: GraphExtractionConfig) -> dict[str, str]:
    """Render the plain-text blocks used by the generated extraction prompt."""
    entity_lines = [f"{item.label}（{item.code}）：{item.description}" for item in config.entity_types]
    relation_lines = [f"{item.label}（{item.code}）：{item.description}" for item in config.relation_types]
    constraint_lines: list[str] = []
    for item in config.relation_types:
        source = "、".join(item.source_types) if item.source_types else "任意"
        target = "、".join(item.target_types) if item.target_types else "任意"
        constraint_lines.append(f"{item.label}（{item.code}）：{source} → {target}")
    literal_lines = [item for item in config.literal_policy.enabled_datatypes]
    return {
        "entity_types": "\n".join(entity_lines) or "（未定义实体类型）",
        "relation_types": "\n".join(relation_lines) or "（未定义关系类型）",
        "relation_constraints": "\n".join(constraint_lines) or "（无显式约束）",
        "literal_rules": "、".join(literal_lines) or "（无）",
    }


def inject_graph_config(definition: dict[str, Any], graph_config: GraphExtractionConfig) -> dict[str, Any]:
    """Return a copy of a template definition carrying a normalised ``graph_config``."""
    value = dict(definition or {})
    value["graph_config"] = graph_config.to_dict()
    return value
