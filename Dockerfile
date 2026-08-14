# syntax=docker/dockerfile:1.7

ARG PYTHON_VERSION=3.12
FROM python:${PYTHON_VERSION}-slim AS python-base

ARG UV_VERSION=0.9.9
ARG PYPI_INDEX_URL=https://pypi.org/simple

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_INDEX_URL=${PYPI_INDEX_URL} \
    UV_DEFAULT_INDEX=${PYPI_INDEX_URL} \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_COMPILE_BYTECODE=1 \
    UV_NO_DEV=1 \
    PATH=/app/.venv/bin:$PATH

WORKDIR /app

# uv 本身很小；从 PyPI 安装比依赖额外访问 GHCR 更适合网络受限服务器。
RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install "uv==${UV_VERSION}"

# -----------------------------------------------------------------------------
# API / Worker 公共层：仅安装基础依赖和 web extra。
# 先复制锁文件，代码变更不会让第三方依赖层失效。
# -----------------------------------------------------------------------------
FROM python-base AS app-common

COPY pyproject.toml uv.lock README.md alembic.ini ./

RUN --mount=type=cache,target=/root/.cache/uv,sharing=locked \
    uv sync --frozen --no-install-project --extra web

COPY src ./src
COPY llm_servings.yaml ./

RUN --mount=type=cache,target=/root/.cache/uv,sharing=locked \
    uv sync --frozen --no-editable --extra web

RUN groupadd --system --gid 10001 dataforge \
    && useradd --system --uid 10001 --gid dataforge --home /app dataforge \
    && mkdir -p /var/lib/dataforge \
    && chown -R dataforge:dataforge /app /var/lib/dataforge

ENV DATAFORGE_ROOT=/app \
    DATAFORGE_STATE_DIR=/var/lib/dataforge \
    DATAFORGE_WEB_HOST=0.0.0.0 \
    DATAFORGE_WEB_PORT=8000


# -----------------------------------------------------------------------------
# API / Worker 镜像目标。
# dataforge-api 和 dataforge-worker 在 Compose 中共用该镜像。
# -----------------------------------------------------------------------------
FROM app-common AS app

USER dataforge
EXPOSE 8000
CMD ["dataforge-web"]


# -----------------------------------------------------------------------------
# V7 Runner 镜像目标。
# Runner 仅补装结构化输出 Schema Validator；算子执行由本仓库的
# DataForge Adapter 完成，不部署 DataFlow WebUI、MCP 或 Agent。
# -----------------------------------------------------------------------------
FROM app-common AS runner

USER root

RUN --mount=type=cache,target=/root/.cache/uv,sharing=locked \
    uv sync --frozen --no-editable --extra web --extra runner

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update \
    && apt-get install -y --no-install-recommends antiword

USER dataforge
EXPOSE 8010
CMD ["dataforge-runner"]
