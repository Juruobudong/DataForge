#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

COMPOSE_FILE="${COMPOSE_FILE:-compose.yaml}"
ENV_FILE="${ENV_FILE:-.env.docker}"
EXECUTE=0

# compose.yaml 含内网地址、不入库；全新 clone 回退到脱敏模板。
if [[ ! -f "$COMPOSE_FILE" && -f compose.example.yaml ]]; then
  COMPOSE_FILE="compose.example.yaml"
fi
CONFIRM=""

usage() {
  cat <<'USAGE'
Usage:
  bash scripts/migrate-qa-agent-faq-test.sh [--env-file FILE]
  bash scripts/migrate-qa-agent-faq-test.sh [--env-file FILE] --execute --confirm VALUE

默认只执行固定 .34/faq 的 inventory + dry-run；execute 只导入并验证，
不会发布 Routing、切换 qa_agent、删除旧 Collection 或访问 .36。
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --env-file) ENV_FILE="${2:-}"; shift 2 ;;
    --execute) EXECUTE=1; shift ;;
    --confirm) CONFIRM="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "不支持的参数：$1" >&2; usage >&2; exit 2 ;;
  esac
done

[[ -f "$COMPOSE_FILE" ]] || { echo "未找到 $COMPOSE_FILE" >&2; exit 1; }
[[ -f "$ENV_FILE" ]] || { echo "未找到 $ENV_FILE" >&2; exit 1; }
if [[ "$EXECUTE" -eq 1 && -z "$CONFIRM" ]]; then
  echo "--execute 必须同时提供 --confirm <dry-run确认值>" >&2
  exit 2
fi
if [[ "$EXECUTE" -eq 0 && -n "$CONFIRM" ]]; then
  echo "未指定 --execute 时不能提供 --confirm" >&2
  exit 2
fi

docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" config >/dev/null

REPORT_DIR="$(mktemp -d)"
trap 'rm -rf -- "$REPORT_DIR"' EXIT
CONNECT_URI="${DATAFORGE_QA_AGENT_TEST_MILVUS_URL:-http://milvus-test:19531}"

run_importer() {
  docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" run --rm --no-deps -T \
    dataforge-worker dataforge-qa-faq-legacy-import "$@" \
    --milvus-uri "${DATAFORGE_QA_AGENT_TEST_MILVUS_URL:-http://milvus-test:19531}" \
    --connect-uri "$CONNECT_URI"
}

echo "===== qa_agent FAQ inventory ====="
run_importer inventory | tee "$REPORT_DIR/inventory.json"

echo "===== qa_agent FAQ dry-run ====="
run_importer dry-run | tee "$REPORT_DIR/dry-run.json"

required_confirmation="$(sed -n 's/^[[:space:]]*"confirmation":[[:space:]]*"\([^"]*\)".*/\1/p' "$REPORT_DIR/dry-run.json" | head -n 1)"
if [[ ! "$required_confirmation" =~ ^MIGRATE-QA-FAQ-[0-9A-F]{20}$ ]]; then
  echo "无法从 dry-run 输出解析固定确认值，拒绝继续。" >&2
  exit 1
fi

if [[ "$EXECUTE" -eq 0 ]]; then
  echo
  echo "Dry-run 完成。执行迁移时使用："
  echo "bash scripts/migrate-qa-agent-faq-test.sh --env-file $ENV_FILE --execute --confirm $required_confirmation"
  exit 0
fi

if [[ "$CONFIRM" != "$required_confirmation" ]]; then
  echo "确认值不匹配；当前 dry-run 需要：$required_confirmation" >&2
  exit 1
fi

echo "===== execute prerequisites ====="
bash scripts/ensure-dataforge-milvus-link.sh --env-file "$ENV_FILE"
CONNECT_URI="http://dataforge-milvus:19530"
running_services="$(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" ps --services --status running)"
for service in mysql minio dataforge-api dataforge-runner dataforge-worker; do
  if ! grep -Fxq "$service" <<<"$running_services"; then
    echo "执行迁移前 Compose 服务未运行：$service" >&2
    exit 1
  fi
done

echo "===== prepare qa-agent-faq type/profile/collection/template ====="
run_importer prepare --confirm "$CONFIRM" | tee "$REPORT_DIR/prepare.json"

echo "===== import 12 authoritative FAQ documents ====="
run_importer import --confirm "$CONFIRM" | tee "$REPORT_DIR/import.json"

echo "===== verify source/CSV/MySQL/Milvus ====="
run_importer verify --confirm "$CONFIRM" | tee "$REPORT_DIR/verify.json"

if ! grep -q '"routing_published": false' "$REPORT_DIR/verify.json"; then
  echo "验证报告缺少 routing_published=false，拒绝宣告成功。" >&2
  exit 1
fi
verified_count="$(grep -c '"ready": true' "$REPORT_DIR/verify.json" || true)"
if [[ "$verified_count" -ne 12 ]]; then
  echo "验证通过的机构数量不是 12，实际为 $verified_count。" >&2
  exit 1
fi

echo
echo "FAQ 导入与验证完成：12 个机构，8,281 行。"
echo "脚本未发布 Routing、未切换 qa_agent、未删除旧 faq Collection。"
