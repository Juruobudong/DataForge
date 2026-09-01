#!/usr/bin/env bash
set -Eeuo pipefail

export DOCKER_BUILDKIT=1
export COMPOSE_DOCKER_CLI_BUILD=1

COMPOSE_FILE="${COMPOSE_FILE:-compose.yaml}"
ENV_FILE="${ENV_FILE:-.env.docker}"

# compose.yaml 含内网地址、不入库；全新 clone 回退到脱敏模板。
if [[ ! -f "$COMPOSE_FILE" && -f compose.example.yaml ]]; then
  COMPOSE_FILE="compose.example.yaml"
fi

if [[ ! -f "$COMPOSE_FILE" ]]; then
  echo "未找到 $COMPOSE_FILE" >&2
  exit 1
fi
if [[ ! -f "$ENV_FILE" ]]; then
  echo "未找到 $ENV_FILE" >&2
  exit 1
fi

MILVUS_LINK_SCRIPT="${MILVUS_LINK_SCRIPT:-scripts/ensure-dataforge-milvus-link.sh}"
bash "$MILVUS_LINK_SCRIPT" --env-file "$ENV_FILE"

# Worker 与 API 共用 dataforge-app 镜像，所以只构建三个有 build 配置的服务。
docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" config >/dev/null
docker compose --env-file "$ENV_FILE" --progress plain -f "$COMPOSE_FILE" build --parallel \
  frontend dataforge-api dataforge-runner

docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" up -d --no-build

docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" ps
