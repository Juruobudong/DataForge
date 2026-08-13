#!/usr/bin/env bash
set -Eeuo pipefail

TORCH_BACKEND="${TORCH_BACKEND:-cpu}"
export UV_TORCH_BACKEND="$TORCH_BACKEND"

echo "[DataForge] 使用 PyTorch backend: $UV_TORCH_BACKEND"
echo "[DataForge] 重新生成 uv.lock..."
uv lock

echo "[DataForge] 校验锁文件..."
uv lock --check

echo "[DataForge] 完成。请将 pyproject.toml 与 uv.lock 一并提交。"
