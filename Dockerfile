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
# 精选算子依赖层：仅依赖 Python/uv 基础层和审核后的两个锁文件。
# 独立于 app-common，业务源码、Adapter 和注册脚本变更不会重装依赖。
# -----------------------------------------------------------------------------
FROM python-base AS operator-deps

COPY runtime/dataflow/upstream.lock runtime/dataflow/requirements.lock ./runtime/dataflow/

RUN --mount=type=cache,target=/root/.cache/pip \
    --mount=type=cache,target=/root/.cache/uv,sharing=locked \
    /usr/local/bin/python -c 'import sys; sys.version_info[:2] == (3, 12) or sys.exit("Operator runtime requires Python 3.12")' \
    && /usr/local/bin/python -m pip download --no-deps --only-binary=:all: --require-hashes \
        -r runtime/dataflow/upstream.lock -d /tmp/operator-wheels \
    && uv venv --python /usr/local/bin/python /opt/dataforge-operators/dataflow-1.0.10 \
    && uv pip sync --python /opt/dataforge-operators/dataflow-1.0.10/bin/python \
        --require-hashes --only-binary=:all: runtime/dataflow/requirements.lock \
    && uv pip install --no-deps --python /opt/dataforge-operators/dataflow-1.0.10/bin/python \
        /tmp/operator-wheels/open_dataflow-1.0.10-py3-none-any.whl

# 新精选环境使用独立审核锁，旧依赖层与旧环境保持不变。
FROM operator-deps AS operator-expanded-deps

COPY runtime/dataflow/requirements-curated-v2.lock ./runtime/dataflow/
RUN --mount=type=cache,target=/root/.cache/uv,sharing=locked \
    uv venv --python /usr/local/bin/python /opt/dataforge-operators/dataflow-1.0.10-curated-v2 \
    && uv pip sync --python /opt/dataforge-operators/dataflow-1.0.10-curated-v2/bin/python \
        --require-hashes --only-binary=:all: runtime/dataflow/requirements-curated-v2.lock \
    && uv pip install --no-deps --python /opt/dataforge-operators/dataflow-1.0.10-curated-v2/bin/python \
        /tmp/operator-wheels/open_dataflow-1.0.10-py3-none-any.whl

# 英文PII依赖和固定模型资源独立缓存，不改变已有两个环境。
FROM operator-expanded-deps AS operator-governance-deps

COPY runtime/dataflow/requirements-pii-v2.lock ./runtime/dataflow/
COPY scripts/prepare-operator-model-wheel.py ./scripts/
COPY runtime/dataflow/vendor/ /tmp/operator-model-wheels/
RUN --mount=type=cache,target=/root/.cache/uv,sharing=locked \
    --mount=type=cache,target=/root/.cache/dataforge-models,sharing=locked \
    /usr/local/bin/python scripts/prepare-operator-model-wheel.py \
        --wheel-dir /tmp/operator-model-wheels --cache-dir /root/.cache/dataforge-models \
        --dependency-lock runtime/dataflow/requirements-pii-v2.lock --output-lock /tmp/operator-pii-install.lock \
    && uv venv --python /usr/local/bin/python /opt/dataforge-operators/dataflow-1.0.10-pii-v2 \
    && uv pip sync --python /opt/dataforge-operators/dataflow-1.0.10-pii-v2/bin/python \
        --torch-backend cpu \
        --require-hashes --only-binary=:all: /tmp/operator-pii-install.lock \
    && uv pip install --no-deps --python /opt/dataforge-operators/dataflow-1.0.10-pii-v2/bin/python \
        /tmp/operator-wheels/open_dataflow-1.0.10-py3-none-any.whl

FROM operator-governance-deps AS operator-governance-resources
COPY scripts/prepare-operator-resources.py ./scripts/
COPY scripts/operator-resource-bundle.py ./scripts/
COPY src/dataforge/v7/operators/resource_bundle.py ./src/dataforge/v7/operators/
COPY runtime/dataflow/resources-pii-v1.lock.json ./runtime/dataflow/
COPY runtime/dataflow/vendor-resources/ /tmp/operator-resource-bundle/
RUN --network=none /opt/dataforge-operators/dataflow-1.0.10-pii-v2/bin/python scripts/prepare-operator-resources.py \
    --download-only \
    --offline-bundle /tmp/operator-resource-bundle/pii-en-v1.zip \
    --resource-lock runtime/dataflow/resources-pii-v1.lock.json \
    --resources /opt/dataforge-operators/resources-pii-v1 \
    --manifest /opt/dataforge-operators/operator-runtime.json \
    --dependency-lock runtime/dataflow/requirements-pii-v2.lock \
    --wheel /tmp/operator-wheels/open_dataflow-1.0.10-py3-none-any.whl

FROM operator-governance-resources AS operator-semantic-deps
COPY runtime/dataflow/requirements-semantic-v1.lock ./runtime/dataflow/
RUN --mount=type=cache,target=/root/.cache/uv,sharing=locked \
    uv venv --python /usr/local/bin/python /opt/dataforge-operators/dataflow-1.0.10-semantic-v1 \
    && uv pip sync --python /opt/dataforge-operators/dataflow-1.0.10-semantic-v1/bin/python \
        --torch-backend cpu --require-hashes --only-binary=:all: runtime/dataflow/requirements-semantic-v1.lock \
    && uv pip install --no-deps --python /opt/dataforge-operators/dataflow-1.0.10-semantic-v1/bin/python \
        /tmp/operator-wheels/open_dataflow-1.0.10-py3-none-any.whl

FROM operator-semantic-deps AS operator-semantic-resources
COPY scripts/prepare-semantic-resources.py ./scripts/
COPY runtime/dataflow/semantic-model-v1.lock.json ./runtime/dataflow/
# vendor-resources was copied by the preceding resource stage. Missing reviewed
# semantic files fail closed here; model downloads never occur in this layer.
RUN --network=none /opt/dataforge-operators/dataflow-1.0.10-semantic-v1/bin/python scripts/prepare-semantic-resources.py \
    --download-only --offline-model-directory /tmp/operator-resource-bundle/semantic-multilingual-v1 \
    --resources /opt/dataforge-operators/resources-semantic-v1 \
    --manifest /opt/dataforge-operators/operator-runtime.json \
    --dependency-lock runtime/dataflow/requirements-semantic-v1.lock \
    --wheel /tmp/operator-wheels/open_dataflow-1.0.10-py3-none-any.whl

# KBC document-chunker使用独立轻量环境，不借用缺少chonkie/transformers的旧环境，
# 也不借用包含完整语义模型与Torch的semantic-v1环境。
FROM operator-semantic-resources AS operator-chunker-deps
COPY runtime/dataflow/requirements-chunker-v1.lock ./runtime/dataflow/
RUN --mount=type=cache,target=/root/.cache/uv,sharing=locked \
    uv venv --python /usr/local/bin/python /opt/dataforge-operators/dataflow-1.0.10-chunker-v1 \
    && uv pip sync --python /opt/dataforge-operators/dataflow-1.0.10-chunker-v1/bin/python \
        --require-hashes --only-binary=:all: runtime/dataflow/requirements-chunker-v1.lock \
    && uv pip install --no-deps --python /opt/dataforge-operators/dataflow-1.0.10-chunker-v1/bin/python \
        /tmp/operator-wheels/open_dataflow-1.0.10-py3-none-any.whl

FROM operator-chunker-deps AS operator-chunker-resources
COPY scripts/prepare-chunker-resources.py ./scripts/
COPY runtime/dataflow/chunker-tokenizer-v1.lock.json ./runtime/dataflow/
RUN --network=none /opt/dataforge-operators/dataflow-1.0.10-chunker-v1/bin/python scripts/prepare-chunker-resources.py \
    --download-only --offline-tokenizer-directory /tmp/operator-resource-bundle/qwen3-tokenizer-v1 \
    --resources /opt/dataforge-operators/resources-chunker-v1 \
    --manifest /opt/dataforge-operators/operator-runtime.json \
    --dependency-lock runtime/dataflow/requirements-chunker-v1.lock \
    --wheel /tmp/operator-wheels/open_dataflow-1.0.10-py3-none-any.whl

# 旧环境注册只读取已安装环境与审核wheel，禁止网络和再次安装。
FROM operator-chunker-resources AS operator-runtime

COPY scripts/register-operator-runtime.py ./scripts/

RUN --network=none \
    /opt/dataforge-operators/dataflow-1.0.10/bin/python scripts/register-operator-runtime.py \
        --output /opt/dataforge-operators/operator-runtime.json \
        --dependency-lock runtime/dataflow/requirements.lock \
        --package open-dataflow 1.0.10 /tmp/operator-wheels/open_dataflow-1.0.10-py3-none-any.whl \
    && /opt/dataforge-operators/dataflow-1.0.10-curated-v2/bin/python scripts/register-operator-runtime.py \
        --output /opt/dataforge-operators/operator-runtime.json \
        --dependency-lock runtime/dataflow/requirements-curated-v2.lock \
        --package open-dataflow 1.0.10 /tmp/operator-wheels/open_dataflow-1.0.10-py3-none-any.whl \
    && /opt/dataforge-operators/dataflow-1.0.10-pii-v2/bin/python scripts/prepare-operator-resources.py \
        --register-only --resources /opt/dataforge-operators/resources-pii-v1 \
        --manifest /opt/dataforge-operators/operator-runtime.json \
        --dependency-lock runtime/dataflow/requirements-pii-v2.lock \
        --wheel /tmp/operator-wheels/open_dataflow-1.0.10-py3-none-any.whl \
    && /opt/dataforge-operators/dataflow-1.0.10-semantic-v1/bin/python scripts/prepare-semantic-resources.py \
        --register-only --resources /opt/dataforge-operators/resources-semantic-v1 \
        --manifest /opt/dataforge-operators/operator-runtime.json \
        --dependency-lock runtime/dataflow/requirements-semantic-v1.lock \
        --wheel /tmp/operator-wheels/open_dataflow-1.0.10-py3-none-any.whl \
    && /opt/dataforge-operators/dataflow-1.0.10-chunker-v1/bin/python scripts/prepare-chunker-resources.py \
        --register-only --resources /opt/dataforge-operators/resources-chunker-v1 \
        --manifest /opt/dataforge-operators/operator-runtime.json \
        --dependency-lock runtime/dataflow/requirements-chunker-v1.lock \
        --wheel /tmp/operator-wheels/open_dataflow-1.0.10-py3-none-any.whl

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
    && mkdir -p /var/lib/dataforge/routing /var/lib/dataforge/migrations \
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
# Runner 额外安装 antiword 和独立 Python 3.12 CPU 算子环境。
# 仅安装精选算子依赖，不部署 DataFlow WebUI、MCP、Agent 或 GPU 推理。
# -----------------------------------------------------------------------------
FROM app-common AS runner

USER root

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update \
    && apt-get install -y --no-install-recommends antiword

COPY --from=operator-runtime /opt/dataforge-operators /opt/dataforge-operators

ENV DATAFORGE_OPERATOR_RUNTIME_MANIFEST=/opt/dataforge-operators/operator-runtime.json

USER dataforge
EXPOSE 8010
CMD ["dataforge-runner"]
