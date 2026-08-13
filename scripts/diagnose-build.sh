#!/usr/bin/env bash
set -Eeuo pipefail

COMPOSE_FILE="${COMPOSE_FILE:-compose.yaml}"

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
