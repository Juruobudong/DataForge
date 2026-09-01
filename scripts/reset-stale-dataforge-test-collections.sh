#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

COMPOSE_FILE="${COMPOSE_FILE:-compose.yaml}"
ENV_FILE="${ENV_FILE:-.env.docker}"

# compose.yaml 含内网地址、不入库；全新 clone 回退到脱敏模板。
if [[ ! -f "$COMPOSE_FILE" && -f compose.example.yaml ]]; then
  COMPOSE_FILE="compose.example.yaml"
fi

if [[ "$#" -ne 0 ]]; then
  echo "该固定测试清理脚本不接受参数；直接运行脚本即执行 dry-run、删除和 Provision。" >&2
  exit 2
fi
if [[ ! -f "$COMPOSE_FILE" ]]; then
  echo "未找到 $COMPOSE_FILE" >&2
  exit 1
fi
if [[ ! -f "$ENV_FILE" ]]; then
  echo "未找到 $ENV_FILE" >&2
  exit 1
fi
bash scripts/ensure-dataforge-milvus-link.sh --env-file "$ENV_FILE"
docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" config >/dev/null

CLEANUP_PY="$(mktemp)"
DRY_RUN_FILE="$(mktemp)"
trap 'rm -f "$CLEANUP_PY" "$DRY_RUN_FILE"' EXIT

# 该 Python 载荷自包含并以只读 bind mount 进入现有 dataforge-provision
# 镜像，避免依赖镜像是否已注册新 console entry。
cat >"$CLEANUP_PY" <<'PYTHON'
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re

from pymilvus import MilvusClient


TARGET_URI = os.environ.get("DATAFORGE_TEST_TARGET_URI", "http://milvus-central-test:19531")
CONNECTION_URI = "http://dataforge-milvus:19530"
MARKER = re.compile(r"^dataforge-managed:([^:]+):([^:]+):([0-9a-f]{64})$")
ALLOWLIST = {
    "dataforge_graph_semantic_knowledge": {
        "id": "collection_graph-semantic",
        "hash": "d43811091bd7f40291f62a809c6b6b4201f43d3cc85247a77ff798dc038c5335",
        "fields": {"id", "vector", "knowledge_library_id", "source_knowledge_id", "content", "data",
                   "source_entity_name", "source_entity_type", "source_entity_description",
                   "target_entity_name", "target_entity_type", "target_entity_description",
                   "relation_description", "relation_keywords", "relation_weight", "evidence"},
    },
    "dataforge_graph_triple_knowledge": {
        "id": "collection_graph-triple",
        "hash": "69e960a08af6599fecb15c2662e4af09693ada9531d3e772c2c1f3739dce4e49",
        "fields": {"id", "vector", "knowledge_library_id", "source_knowledge_id", "content", "data",
                   "subject", "predicate", "object", "subject_type", "object_type"},
    },
    "dataforge_text_knowledge": {
        "id": "collection_text",
        "hash": "704f47be8ad97e45c8265edadbb6e8f43b2d667f400e33cc8044c6ec7b524286",
        "fields": {"id", "vector", "knowledge_library_id", "source_knowledge_id", "content", "data"},
    },
    "dataforge_qa_full": {
        "id": "collection_qa-full",
        "hash": "14becacaf06b5e335ad0541bff238bbac6385461d7179fcb7ab9990f7a9d0382",
        "fields": {"id", "vector", "knowledge_library_id", "source_knowledge_id", "content", "data",
                   "question", "answer"},
    },
    "dataforge_qa_question": {
        "id": "collection_qa-question",
        "hash": "84c3337b849ed84d3cfe561cf9a2e3f898dd2d48a24b1f0316686d8ecc09eb35",
        "fields": {"id", "vector", "knowledge_library_id", "source_knowledge_id", "content", "data",
                   "question"},
    },
}


def refuse(message: str) -> None:
    raise SystemExit(f"REFUSED: {message}")


def confirmation_value() -> str:
    material = [TARGET_URI]
    material.extend(f"{name}:{item['id']}:{item['hash']}" for name, item in ALLOWLIST.items())
    digest = hashlib.sha256("\n".join(material).encode()).hexdigest()[:16].upper()
    return f"DROP-DATAFORGE-OWNED-{digest}"


def inspect_one(client: MilvusClient, name: str) -> dict:
    expected = ALLOWLIST[name]
    if not client.has_collection(collection_name=name):
        return {"collection_name": name, "exists": False}
    description = dict(client.describe_collection(collection_name=name))
    marker_text = str(description.get("description") or "")
    marker = MARKER.fullmatch(marker_text)
    if not marker:
        refuse(f"{name}: ownership marker 格式不匹配")
    managed_id, _old_token, spec_hash = marker.groups()
    if managed_id != expected["id"] or spec_hash != expected["hash"]:
        refuse(f"{name}: 受管 ID 或 Contract hash 不匹配")
    fields = {str(item.get("name")): dict(item) for item in description.get("fields", [])}
    if set(fields) != expected["fields"]:
        refuse(f"{name}: schema 字段集合不匹配")
    vector_params = dict(fields["vector"].get("params") or fields["vector"].get("type_params") or {})
    if int(vector_params.get("dim", 0)) != 768:
        refuse(f"{name}: vector dimension 不是 768")
    indexes = [dict(client.describe_index(collection_name=name, index_name=index_name))
               for index_name in client.list_indexes(collection_name=name)]
    vector_indexes = [item for item in indexes
                      if item.get("field_name") == "vector" or item.get("index_name") == "vector"]
    vector_index = vector_indexes[0] if vector_indexes else {}
    metric = str(vector_index.get("metric_type") or
                 (vector_index.get("params") or {}).get("metric_type") or "").upper()
    if metric != "COSINE":
        refuse(f"{name}: 缺少 COSINE vector index")
    partitions = list(client.list_partitions(collection_name=name))
    unexpected = [value for value in partitions if value != "_default" and not value.startswith("kl_")]
    if unexpected:
        refuse(f"{name}: 存在非 DataForge Partition {unexpected}")
    stats = dict(client.get_collection_stats(collection_name=name))
    return {
        "collection_name": name, "exists": True,
        "managed_collection_id": managed_id, "storage_spec_hash": spec_hash,
        "description": marker_text, "fields": sorted(fields), "indexes": indexes,
        "partitions": partitions, "row_count": int(stats.get("row_count") or 0),
    }


def inspect_all(client: MilvusClient) -> list[dict]:
    return [inspect_one(client, name) for name in ALLOWLIST]


parser = argparse.ArgumentParser()
parser.add_argument("--execute", action="store_true")
parser.add_argument("--confirm", default="")
args = parser.parse_args()

options = {"uri": CONNECTION_URI}
token = os.getenv("DATAFORGE_MILVUS_TOKEN", "").strip()
if token:
    options["token"] = token
client = MilvusClient(**options)
inventory = inspect_all(client)
expected_confirmation = confirmation_value()
result = {
    "mode": "execute" if args.execute else "dry-run",
    "target_uri": TARGET_URI,
    "allowlist": list(ALLOWLIST),
    "confirmation_value": expected_confirmation,
    "total_rows": sum(item.get("row_count", 0) for item in inventory),
    "collections": inventory,
}
if not args.execute:
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0)
if args.confirm != expected_confirmation:
    refuse("确认值不匹配")

inspect_all(client)
deleted = []
for collection_name in ALLOWLIST:
    current = inspect_one(client, collection_name)
    if not current["exists"]:
        continue
    client.drop_collection(collection_name=collection_name)
    deleted.append(collection_name)
remaining = [name for name in ALLOWLIST if client.has_collection(collection_name=name)]
if remaining:
    refuse(f"删除后仍存在 Collection: {remaining}")
result["deleted"] = deleted
result["verified_absent"] = list(ALLOWLIST)
print(json.dumps(result, ensure_ascii=False, indent=2))
PYTHON

# mktemp 默认 0600；provision 容器以 UID 10001 运行，需要宿主机载荷
# 对容器用户可读。bind mount 仍为 :ro，脚本退出时由 trap 删除。
chmod 0444 "$CLEANUP_PY"

run_cleanup() {
  docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" run --rm --no-deps -T \
    -v "$CLEANUP_PY:/tmp/dataforge-stale-cleanup.py:ro" \
    dataforge-provision python /tmp/dataforge-stale-cleanup.py "$@"
}

echo "===== DataForge-owned Collection dry-run ====="
run_cleanup | tee "$DRY_RUN_FILE"

confirmation_value="$(sed -n 's/^[[:space:]]*"confirmation_value":[[:space:]]*"\([^"]*\)".*/\1/p' "$DRY_RUN_FILE" | head -n 1)"
if [[ ! "$confirmation_value" =~ ^DROP-DATAFORGE-OWNED-[0-9A-F]{16}$ ]]; then
  echo "无法从 dry-run 输出解析 confirmation_value，拒绝继续。" >&2
  exit 1
fi

echo
echo "dry-run 门禁已通过；现在自动永久删除固定列出的五个 DataForge-owned Collection。"

echo "===== Execute fixed allowlist cleanup ====="
run_cleanup --execute --confirm "$confirmation_value"

echo "===== Re-provision managed Collections ====="
docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" up -d \
  --force-recreate dataforge-provision
docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" logs dataforge-provision
