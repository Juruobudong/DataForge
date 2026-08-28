"""Authoritative entity vocabulary and stateless authoring operations."""
from __future__ import annotations

import hashlib
import unicodedata
from copy import deepcopy
from typing import Any


_BASE = (
    ("person", "人物", "具体的人物、角色或个人。"),
    ("organization", "组织", "机构、企业、医院等组织。"),
    ("location", "地点", "地理位置、区域或场所。"),
    ("event", "事件", "发生的活动、行为或事件。"),
    ("concept", "概念", "不属于其他已配置类型的抽象概念。"),
)
_MEDICAL = (
    ("disease", "疾病", "疾病、病症及其诊断名称。"),
    ("symptom", "症状", "患者的症状或体征。"),
    ("drug", "药品", "药物或药品。"),
    ("examination", "检查", "医学检查、检验及检查项目。"),
    ("treatment", "治疗", "治疗方法、手术或干预方案。"),
    ("body_part", "人体部位", "人体器官、组织及解剖部位。"),
    ("department", "科室", "医疗机构中的临床或医技科室。"),
    ("medical_indicator", "医学指标", "可测量的医学指标名称，不包括指标数值。"),
)


def normalize_entity_label(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("实体类型名称必须是字符串")
    label = unicodedata.normalize("NFKC", value).strip()
    if not label:
        raise ValueError("实体类型名称不能为空")
    return label


def custom_type_code(label: str) -> str:
    return "custom_" + hashlib.sha256(normalize_entity_label(label).encode("utf-8")).hexdigest()[:16]


def entity_type_catalog() -> dict[str, Any]:
    def items(rows, source, preset=None):
        return [dict(code=code, label=label, description=description, source=source,
                     **({"preset": preset} if preset else {})) for code, label, description in rows]
    return {"base": items(_BASE, "base"), "presets": [
        {"code": "medical", "label": "医疗", "entity_types": items(_MEDICAL, "preset", "medical")},
    ]}


def normalize_entity_origin(raw: dict[str, Any], code: str, label: str) -> tuple[str, str | None]:
    source = raw.get("source", "custom")
    preset = raw.get("preset")
    if source not in ("base", "preset", "custom"):
        raise ValueError("实体类型 source 必须是 base、preset 或 custom")
    if source == "preset":
        if preset != "medical" or (code, label) not in {(c, n) for c, n, _ in _MEDICAL}:
            raise ValueError("医疗预设实体必须属于 medical 目录")
    elif preset is not None:
        raise ValueError("只有 source=preset 的实体才能指定 preset")
    if source == "base" and (code, label) not in {(c, n) for c, n, _ in _BASE}:
        raise ValueError("基础实体必须属于基础类型目录")
    return source, preset


def normalize_entity_types(raw: Any, *, allow_legacy_strings: bool = False) -> list[dict[str, Any]]:
    # Import here to keep the schema's origin validation independent of authoring.
    from .graph_schema import normalize_graph_config

    if not isinstance(raw, list):
        raise ValueError("entity_types 必须是数组")
    items = deepcopy(raw)
    if allow_legacy_strings:
        catalog = entity_type_catalog()
        known = catalog["base"] + catalog["presets"][0]["entity_types"]
        for index, item in enumerate(items):
            if isinstance(item, str):
                label = normalize_entity_label(item)
                match = next((entry for entry in known if label in (entry["code"], entry["label"])), None)
                # A legacy medical name is not proof of preset ownership.
                items[index] = ({**match, "source": "custom"} if match else {"label": label, "source": "custom"})
                items[index].pop("preset", None)
    return normalize_graph_config({"entity_types": items}).to_dict()["entity_types"]


def resolve_entity_types(raw: Any, action: str, *, label: str | None = None, code: str | None = None,
                         description: str | None = None) -> list[dict[str, Any]]:
    items = normalize_entity_types(raw, allow_legacy_strings=True)
    if action == "normalize":
        return items
    if action == "add_custom":
        items.append({"label": normalize_entity_label(label), "source": "custom"})
    elif action == "add_medical":
        names = {item["label"] for item in items}
        codes = {item["code"] for item in items}
        items.extend(item for item in entity_type_catalog()["presets"][0]["entity_types"]
                     if item["label"] not in names and item["code"] not in codes)
    elif action == "update":
        item = next((item for item in items if item["code"] == code), None)
        if item is None:
            raise ValueError("待编辑实体类型不存在")
        name = normalize_entity_label(label)
        if not isinstance(description, str):
            raise ValueError("实体类型描述必须是字符串")
        description = description.strip()
        if item["label"] != name or item["description"] != description:
            item.update(label=name, description=description, source="custom")
            item.pop("preset", None)
    elif action == "remove_medical":
        items = [item for item in items if not (item["source"] == "preset" and item.get("preset") == "medical")]
    else:
        raise ValueError("未知的实体类型编辑操作")
    return normalize_entity_types(items)


def clean_removed_entity_references(definition: dict[str, Any], previous: dict[str, Any] | None) -> None:
    """Remove only deleted declarations, never silently hide unrelated bad references."""
    old_graph = (previous or {}).get("graph_config") or {}
    new_graph = definition.get("graph_config") or {}
    if not isinstance(old_graph, dict) or not isinstance(new_graph, dict):
        return  # The Graph Schema validator reports malformed payloads.
    old_types, new_types = old_graph.get("entity_types", []), new_graph.get("entity_types", [])
    if not isinstance(old_types, list) or not isinstance(new_types, list):
        return
    old = {item.get("code") for item in old_types if isinstance(item, dict)}
    new = {item.get("code") for item in new_types if isinstance(item, dict)}
    removed = old - new
    if not removed:
        return
    def clean(constraints):
        if not isinstance(constraints, list):
            return
        for item in constraints:
            if not isinstance(item, dict):
                continue
            for key in ("source_types", "target_types"):
                if isinstance(item.get(key), list):
                    item[key] = [code for code in item[key] if code not in removed]
    clean(new_graph.get("relation_types", []))
    for node in definition.get("nodes", []):
        params = node.get("params") or {}
        if node.get("ref") == "entity-extractor" and isinstance(params.get("entity_types"), list):
            if "entity_type_scope" not in params:
                params["entity_type_scope"] = "subset" if params["entity_types"] else "all"
            params["entity_types"] = [code for code in params["entity_types"] if code not in removed]
        if node.get("ref") == "relation-extractor":
            clean(params.get("relation_constraints") or [])
