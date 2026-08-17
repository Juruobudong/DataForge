"""Graph quality gate for formal publication.

Runs on assembled graph candidates before the Knowledge Sink.  Semantic graphs
hard-fail when any literal leaked in as an entity, when entity/relation types are
illegal, or when evidence is missing.
"""
from __future__ import annotations

from typing import Any

from .graph_literal import detect_literal
from .graph_schema import GraphExtractionConfig


def _entity_codes(item: dict[str, Any], graph_mode: str) -> list[str]:
    if graph_mode == "semantic":
        source, target = (item.get(key) or {} for key in ("source_entity", "target_entity"))
        return [str(source.get("type") or ""), str(target.get("type") or "")]
    # triple: subject is always an entity; object only when object_kind == entity.
    codes = [str(item.get("subject_type") or "")]
    object_kind = (item.get("data") or {}).get("object_kind")
    if object_kind != "literal":
        codes.append(str(item.get("object_type") or ""))
    return codes


def _entity_names(item: dict[str, Any], graph_mode: str) -> list[str]:
    if graph_mode == "semantic":
        source, target = (item.get(key) or {} for key in ("source_entity", "target_entity"))
        return [str(source.get("name") or "").strip(), str(target.get("name") or "").strip()]
    names = [str(item.get("subject") or "").strip()]
    if (item.get("data") or {}).get("object_kind") != "literal":
        names.append(str(item.get("object") or "").strip())
    return names


def evaluate_graph_quality(items: list[dict[str, Any]], config: GraphExtractionConfig, graph_mode: str) -> dict[str, Any]:
    """Compute quality counters and the semantic hard-fail verdict.

    ``items`` is a list of assembled graph ``data_json`` dictionaries.
    """
    valid_entity_codes = config.entity_codes()
    valid_relation_codes = {item.code for item in config.relation_types}
    has_entity_schema = bool(config.entity_types)
    has_relation_schema = bool(config.relation_types)

    invalid_entity_type = 0
    invalid_relation_type = 0
    literal_as_entity = 0
    unclassified_entity = 0
    missing_evidence = 0
    seen_names: dict[str, int] = {}
    edge_endpoints: set[str] = set()

    for item in items:
        if not isinstance(item, dict):
            continue

        if graph_mode == "semantic":
            relation = item.get("relation") or {}
            rel_type = str(relation.get("type") or "")
            relation_def = config.relation_by_code(rel_type) if rel_type else None
            if not rel_type:
                unclassified_entity += 1
            elif relation_def is None:
                if has_relation_schema:
                    invalid_relation_type += 1
                # else: unconstrained schema accepts free-form relation types.
            else:
                source = item.get("source_entity") or {}
                target = item.get("target_entity") or {}
                if relation_def.source_types and source.get("type") not in relation_def.source_types:
                    invalid_relation_type += 1
                if relation_def.target_types and target.get("type") not in relation_def.target_types:
                    invalid_relation_type += 1
            evidence = item.get("evidence") or []
            if not evidence:
                missing_evidence += 1
        else:
            predicate_code = str(item.get("predicate_code") or "")
            object_kind = (item.get("data") or {}).get("object_kind")
            if object_kind == "literal":
                # A literal fact is legitimate for triple; not an entity node.
                pass
            if has_relation_schema and predicate_code and predicate_code not in valid_relation_codes:
                invalid_relation_type += 1

        for code in _entity_codes(item, graph_mode):
            if not code:
                unclassified_entity += 1
            elif has_entity_schema and code not in valid_entity_codes:
                invalid_entity_type += 1

        for name in _entity_names(item, graph_mode):
            if name:
                seen_names[name] = seen_names.get(name, 0) + 1
            if graph_mode == "semantic" and detect_literal(name) is not None:
                literal_as_entity += 1

        # Entity endpoints participating in at least one relation.
        for name in _entity_names(item, graph_mode):
            if name:
                edge_endpoints.add(name)

    duplicate_entity = sum(1 for count in seen_names.values() if count > 1)
    isolated_entity = sum(1 for name, count in seen_names.items() if name not in edge_endpoints)

    hard_fail = bool(
        literal_as_entity > 0
        or invalid_entity_type > 0
        or invalid_relation_type > 0
        or missing_evidence > 0
    ) if graph_mode == "semantic" else False

    warnings: list[str] = []
    if duplicate_entity:
        warnings.append(f"重复实体候选 {duplicate_entity} 项")
    if isolated_entity:
        warnings.append(f"孤立实体 {isolated_entity} 项")
    if unclassified_entity and not invalid_entity_type:
        warnings.append(f"未分类实体 {unclassified_entity} 项")

    return {
        "invalid_entity_type_count": invalid_entity_type,
        "invalid_relation_type_count": invalid_relation_type,
        "literal_as_entity_count": literal_as_entity,
        "unclassified_entity_count": unclassified_entity,
        "missing_evidence_count": missing_evidence,
        "duplicate_entity_count": duplicate_entity,
        "isolated_entity_count": isolated_entity,
        "hard_fail": hard_fail,
        "warnings": warnings,
    }
