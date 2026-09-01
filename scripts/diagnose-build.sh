#!/usr/bin/env bash
set -Eeuo pipefail

COMPOSE_FILE="${COMPOSE_FILE:-compose.yaml}"

# compose.yaml 含内网地址、不入库；全新 clone 回退到脱敏模板。
if [[ ! -f "$COMPOSE_FILE" && -f compose.example.yaml ]]; then
  COMPOSE_FILE="compose.example.yaml"
fi

echo "===== Docker ====="
docker version || true

echo "===== Compose ====="
docker compose version || true

echo "===== Buildx ====="
docker buildx version || true
docker buildx ls || true

echo "===== Disk ====="
df -h || true
docker system df || true

echo "===== Compose config ====="
docker compose -f "$COMPOSE_FILE" config --services || true

echo "===== Build cache ====="
docker buildx du || true
