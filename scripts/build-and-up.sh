#!/usr/bin/env bash
set -Eeuo pipefail

export DOCKER_BUILDKIT=1
export COMPOSE_DOCKER_CLI_BUILD=1

COMPOSE_FILE="${COMPOSE_FILE:-compose.yaml}"

if [[ ! -f "$COMPOSE_FILE" ]]; then
  echo "未找到 $COMPOSE_FILE" >&2
  exit 1
fi

# Worker 与 API 共用 dataforge-app 镜像，所以只构建三个有 build 配置的服务。
docker compose -f "$COMPOSE_FILE" config >/dev/null
docker compose --progress plain -f "$COMPOSE_FILE" build --parallel \
  frontend dataforge-api dataforge-runner

docker compose -f "$COMPOSE_FILE" up -d --no-build

docker compose -f "$COMPOSE_FILE" ps
