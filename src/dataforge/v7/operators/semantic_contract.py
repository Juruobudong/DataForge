"""Reviewed semantic model identity; safe to load in isolated maintenance/worker Python."""

MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
REVISION = "e8f8c211226b894fcb81acc59f3b34ba3efd5f42"
PROFILE = "semantic-multilingual-v1"
MAX_GROUP_RECORDS = 5000


def group_key(scope, versions):
    import json
    if scope not in {"flow_input", "source_version"}:
        raise ValueError("PARAMETER_SCHEMA_INVALID: 语义比较范围非法")
    if not isinstance(versions, list) or not versions or any(not isinstance(v, str) or not v for v in versions):
        raise ValueError("SOURCE_LINEAGE_MISSING: 来源版本列表非法")
    return "flow_input" if scope == "flow_input" else json.dumps(sorted(set(versions)), ensure_ascii=True)


def size_issue(count, *, node_id=None, group=None):
    return {"code": "SEMANTIC_DEDUP_GROUP_TOO_LARGE", "severity": "error", "node_id": node_id,
            "group": group, "count": count, "limit": MAX_GROUP_RECORDS,
            "message": f"语义重复标记节点 {node_id or '当前节点'} 的比较组有 {count} 条记录，最多允许 {MAX_GROUP_RECORDS} 条"}


def require_group_size(count, *, node_id=None, group=None):
    if count > MAX_GROUP_RECORDS:
        issue = size_issue(count, node_id=node_id, group=group)
        error = ValueError(issue["code"] + ": " + issue["message"] + f"（组：{group}）")
        error.issue = issue
        raise error


def input_preflight(definition, source_counts):
    """Only a direct reviewed-input edge has an exact pre-execution count."""
    from collections import defaultdict
    by_id = {node["id"]: node for node in definition.get("nodes", [])}
    incoming = defaultdict(list)
    for edge in definition.get("edges", []):
        source, target = (edge[0], edge[1]) if isinstance(edge, list) else (edge["source"], edge["target"])
        incoming[target].append(source)
    issues = []
    for node in by_id.values():
        if node.get("kind") != "operator" or node.get("ref") != "SemDeduplicateFilter":
            continue
        parents = incoming[node["id"]]
        if len(parents) != 1 or by_id.get(parents[0], {}).get("ref") != "reviewed-source-chunk-input":
            issues.append({"code": "SEMANTIC_DEDUP_SIZE_DEFERRED", "severity": "warning", "node_id": node["id"],
                "limit": MAX_GROUP_RECORDS, "message": "上游执行后的记录数尚未确定，将在语义节点启动前检查每组5000条上限"})
            continue
        counts = defaultdict(int)
        for versions, count in source_counts:
            counts[group_key((node.get("params") or {}).get("scope", "source_version"), versions)] += count
        issues.extend(size_issue(count, node_id=node["id"], group=group)
                      for group, count in counts.items() if count > MAX_GROUP_RECORDS)
    return issues


def validate_model_bundle(bundle):
    if (not isinstance(bundle, dict) or bundle.get("semantic_model") != MODEL
            or bundle.get("semantic_revision") != REVISION):
        raise ValueError("OPERATOR_RESOURCE_DRIFT: 语义模型或修订不符合审核契约")
