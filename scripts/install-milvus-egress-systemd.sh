#!/usr/bin/env bash
set -Eeuo pipefail

DATAFORGE_HOME="$(pwd)"
ENV_FILE=""

usage() {
  echo "Usage: sudo $0 [--dataforge-home PATH] [--env-file PATH]" >&2
}

while (($#)); do
  case "$1" in
    --dataforge-home) DATAFORGE_HOME="$2"; shift 2 ;;
    --env-file) ENV_FILE="$2"; shift 2 ;;
    *) usage; exit 2 ;;
  esac
done

[[ ${EUID} -eq 0 ]] || { echo "必须以 root 安装 systemd 单元。" >&2; exit 1; }
command -v systemctl >/dev/null || { echo "未找到 systemctl。" >&2; exit 1; }
DATAFORGE_HOME="$(realpath "$DATAFORGE_HOME")"
ENV_FILE="${ENV_FILE:-$DATAFORGE_HOME/.env.docker}"
ENV_FILE="$(realpath "$ENV_FILE")"
[[ -f "$ENV_FILE" ]] || { echo "未找到部署环境文件：$ENV_FILE" >&2; exit 1; }
[[ -f "$DATAFORGE_HOME/scripts/ensure-milvus-egress.sh" ]] || {
  echo "未找到 ensure-milvus-egress.sh：$DATAFORGE_HOME/scripts" >&2
  exit 1
}

install -d -m 0750 /etc/dataforge
umask 077
printf 'DATAFORGE_HOME=%s\nDATAFORGE_ENV_FILE=%s\n' "$DATAFORGE_HOME" "$ENV_FILE" > /etc/dataforge/milvus-egress.env
install -m 0644 "$DATAFORGE_HOME/deploy/systemd/dataforge-milvus-egress.service" /etc/systemd/system/dataforge-milvus-egress.service
install -m 0644 "$DATAFORGE_HOME/deploy/systemd/dataforge-milvus-egress.timer" /etc/systemd/system/dataforge-milvus-egress.timer
systemctl daemon-reload
systemctl enable --now dataforge-milvus-egress.timer
systemctl start dataforge-milvus-egress.service
systemctl status --no-pager dataforge-milvus-egress.service
systemctl status --no-pager dataforge-milvus-egress.timer
