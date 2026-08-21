"""Explicit cleanup for stale built-in DataForge Collections on test Milvus.

The allowlist is intentionally immutable at runtime.  This command exists only
for the empty-Compose rebuild case where MySQL loses the old provisioning token
while the external test Milvus retains Collections created by DataForge.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from dataclasses import dataclass
from typing import Any

from .store import STORAGE_CONTRACT_SEEDS


TEST_TARGET_URI = os.environ.get("DATAFORGE_TEST_TARGET_URI") or "http://milvus-central-test:19531"
TEST_CONNECTION_URIS = {TEST_TARGET_URI, "http://dataforge-milvus:19530"}
MARKER = re.compile(r"^dataforge-managed:([^:]+):([^:]+):([0-9a-f]{64})$")


@dataclass(frozen=True)
class AllowedCollection:
    contract_code: str
    managed_id: str
    spec_hash: str


ALLOWLIST: dict[str, AllowedCollection] = {
    "dataforge_graph_semantic_knowledge": AllowedCollection(
        "graph-semantic", "collection_graph-semantic",
        "d43811091bd7f40291f62a809c6b6b4201f43d3cc85247a77ff798dc038c5335",
    ),
    "dataforge_graph_triple_knowledge": AllowedCollection(
        "graph-triple", "collection_graph-triple",
        "69e960a08af6599fecb15c2662e4af09693ada9531d3e772c2c1f3739dce4e49",
    ),
    "dataforge_text_knowledge": AllowedCollection(
        "text", "collection_text",
        "704f47be8ad97e45c8265edadbb6e8f43b2d667f400e33cc8044c6ec7b524286",
    ),
    "dataforge_qa_full": AllowedCollection(
        "qa-full", "collection_qa-full",
        "14becacaf06b5e335ad0541bff238bbac6385461d7179fcb7ab9990f7a9d0382",
    ),
    "dataforge_qa_question": AllowedCollection(
        "qa-question", "collection_qa-question",
        "84c3337b849ed84d3cfe561cf9a2e3f898dd2d48a24b1f0316686d8ecc09eb35",
    ),
}


class CleanupRefused(RuntimeError):
    """Raised before deletion when any safety proof is incomplete."""


def _normalise_uri(value: str) -> str:
    return str(value or "").strip().rstrip("/")


def confirmation_value(target_uri: str = TEST_TARGET_URI) -> str:
    material = [target_uri]
    material.extend(
        f"{name}:{item.managed_id}:{item.spec_hash}"
        for name, item in ALLOWLIST.items()
    )
    digest = hashlib.sha256("\n".join(material).encode()).hexdigest()[:16].upper()
    return f"DROP-DATAFORGE-OWNED-{digest}"


def _index_details(client: Any, collection_name: str) -> list[dict[str, Any]]:
    names = list(client.list_indexes(collection_name=collection_name))
    return [dict(client.describe_index(collection_name=collection_name, index_name=name))
            for name in names]


def _field_params(field: dict[str, Any]) -> dict[str, Any]:
    return dict(field.get("params") or field.get("type_params") or {})


def inspect_one(client: Any, collection_name: str) -> dict[str, Any]:
    allowed = ALLOWLIST[collection_name]
    if not client.has_collection(collection_name=collection_name):
        return {"collection_name": collection_name, "exists": False}

    description = dict(client.describe_collection(collection_name=collection_name))
    marker_text = str(description.get("description") or "")
    marker = MARKER.fullmatch(marker_text)
    if not marker:
        raise CleanupRefused(f"{collection_name}: ownership marker 格式不匹配")
    managed_id, _old_token, spec_hash = marker.groups()
    if managed_id != allowed.managed_id or spec_hash != allowed.spec_hash:
        raise CleanupRefused(f"{collection_name}: 受管 ID 或 Contract hash 不匹配")

    seed = STORAGE_CONTRACT_SEEDS[allowed.contract_code]
    if seed["collection"] != collection_name:
        raise CleanupRefused(f"{collection_name}: 当前内置 Contract 名称不匹配")
    actual_fields = {str(field.get("name")): dict(field)
                     for field in description.get("fields", [])}
    expected_fields = {str(field["name"]): field for field in seed["schema"]["fields"]}
    if set(actual_fields) != set(expected_fields):
        raise CleanupRefused(f"{collection_name}: schema 字段集合不匹配")
    vector_params = _field_params(actual_fields["vector"])
    if int(vector_params.get("dim", 0)) != 768:
        raise CleanupRefused(f"{collection_name}: vector dimension 不是 768")

    indexes = _index_details(client, collection_name)
    vector_indexes = [item for item in indexes
                      if item.get("field_name") == "vector" or item.get("index_name") == "vector"]
    if not vector_indexes:
        raise CleanupRefused(f"{collection_name}: 缺少 vector index")
    metric = str(vector_indexes[0].get("metric_type") or
                 (vector_indexes[0].get("params") or {}).get("metric_type") or "").upper()
    if metric != "COSINE":
        raise CleanupRefused(f"{collection_name}: vector index metric 不是 COSINE")

    partitions = list(client.list_partitions(collection_name=collection_name))
    unexpected = [name for name in partitions if name != "_default" and not name.startswith("kl_")]
    if unexpected:
        raise CleanupRefused(f"{collection_name}: 存在非 DataForge Partition {unexpected}")
    stats = dict(client.get_collection_stats(collection_name=collection_name))
    return {
        "collection_name": collection_name,
        "exists": True,
        "managed_collection_id": managed_id,
        "storage_spec_hash": spec_hash,
        "description": marker_text,
        "fields": sorted(actual_fields),
        "indexes": indexes,
        "partitions": partitions,
        "row_count": int(stats.get("row_count") or 0),
    }


def inspect_all(client: Any) -> list[dict[str, Any]]:
    # Validate every target before returning any executable plan.
    return [inspect_one(client, name) for name in ALLOWLIST]


def cleanup(client: Any, *, target_uri: str, execute: bool, confirm: str = "") -> dict[str, Any]:
    target = _normalise_uri(target_uri)
    if target != TEST_TARGET_URI:
        raise CleanupRefused(f"只允许测试目标 {TEST_TARGET_URI}")
    expected_confirm = confirmation_value(target)
    inventory = inspect_all(client)
    result: dict[str, Any] = {
        "mode": "execute" if execute else "dry-run",
        "target_uri": target,
        "allowlist": list(ALLOWLIST),
        "confirmation_value": expected_confirm,
        "total_rows": sum(item.get("row_count", 0) for item in inventory),
        "collections": inventory,
    }
    if not execute:
        return result
    if confirm != expected_confirm:
        raise CleanupRefused("确认值不匹配；请先执行 dry-run 并复制 confirmation_value")

    # One final all-target pass prevents partial deletion from a known preflight
    # mismatch.  Each target is checked again immediately before its drop.
    inspect_all(client)
    deleted: list[str] = []
    for name in ALLOWLIST:
        current = inspect_one(client, name)
        if not current["exists"]:
            continue
        client.drop_collection(collection_name=name)
        deleted.append(name)
    remaining = [name for name in ALLOWLIST
                 if client.has_collection(collection_name=name)]
    if remaining:
        raise CleanupRefused(f"删除后仍存在 Collection: {remaining}")
    result["deleted"] = deleted
    result["verified_absent"] = list(ALLOWLIST)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="清理 .34 空卷重建遗留的五个 DataForge-owned 内置 Collection",
    )
    parser.add_argument("--target-uri", required=True,
                        help=f"必须显式填写 {TEST_TARGET_URI}")
    parser.add_argument("--connection-uri", default=os.getenv("DATAFORGE_MILVUS_URI", ""),
                        help="Milvus 实际连接地址；容器内可使用 http://dataforge-milvus:19530")
    parser.add_argument("--execute", action="store_true", help="执行删除；默认只读 dry-run")
    parser.add_argument("--confirm", default="", help="dry-run 输出的 confirmation_value")
    args = parser.parse_args()

    target_uri = _normalise_uri(args.target_uri)
    connection_uri = _normalise_uri(args.connection_uri)
    if target_uri != TEST_TARGET_URI:
        raise SystemExit(f"只允许测试目标 {TEST_TARGET_URI}")
    if connection_uri not in TEST_CONNECTION_URIS:
        raise SystemExit(f"连接地址只允许 {sorted(TEST_CONNECTION_URIS)}")

    from pymilvus import MilvusClient
    options: dict[str, Any] = {"uri": connection_uri}
    token = os.getenv("DATAFORGE_MILVUS_TOKEN", "").strip()
    if token:
        options["token"] = token
    try:
        result = cleanup(
            MilvusClient(**options), target_uri=target_uri,
            execute=args.execute, confirm=args.confirm,
        )
    except CleanupRefused as exc:
        raise SystemExit(f"REFUSED: {exc}") from exc
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
