# DataForge V7

DataForge V7 是医疗文档知识生产与发布平台。本文件只提供开发、运行和验证入口；系统事实见 [Architecture](docs/architecture/overview.md)，能力完成度见 [Capability Matrix](V7-CAPABILITY-MATRIX.md)。

## 环境要求

- 后端统一使用 Conda 环境 `sun`；所有 Python、`uv`、`pytest` 和 `dataforge-*` 命令都必须先执行 `conda activate sun`。
- 前端使用 Node.js 与 npm。
- 本地没有 Docker；Compose 只在部署服务器使用。本地后端需配置可用的 MySQL、MinIO，以及按测试范围需要的 Milvus、Embedding、Model Serving 和 MinerU。

## 安装后端依赖

```powershell
conda activate sun
uv sync --extra web
```

## 初始化或升级 V7 Schema

目标必须是空数据库或已有 V7 schema；命令会执行 Alembic 升级并写入 V7 种子。

```powershell
conda activate sun
uv run --extra web dataforge-migrate --upgrade-platform
```

## 启动后端进程

根据开发场景分别启动 API、Worker 与 Runner：

```powershell
conda activate sun
uv run --extra web dataforge-web
```

```powershell
conda activate sun
uv run --extra web dataforge-worker
```

```powershell
conda activate sun
uv run --extra web dataforge-runner
```

运行前需要按 [`compose.yaml`](compose.yaml) 配置数据库、对象存储、Runner、Milvus、Routing 与迁移目录。中心环境在 `.env.docker` 通过 `DATAFORGE_DEFAULT_LLM_BASE_URL/MODEL/MAX_TOKENS`、`LOCAL_LLM_API_KEY` 和 `EMBEDDING_API_BASE/API_KEY/MODEL/DIM/BATCH_SIZE` 初始化两个默认 Serving；这些变量只由 migrate 首次读取，数据库记录存在后改由“模型服务”页面维护且 Seed 不覆盖。保存非占位 API Key 或 local Milvus 凭据时，必须设置 32 字节 `DATAFORGE_CONFIG_ENCRYPTION_KEY`（Base64 或 64 位十六进制）。

## 启动前端

```powershell
cd frontend
npm ci
npm run dev
```

生产构建：

```powershell
cd frontend
npm run build
```

## 验证

后端测试按范围选择测试文件，仍须在 `sun` 中执行：

```powershell
conda activate sun
uv run --extra web pytest tests/test_v7_platform.py
```

前端测试与构建：

```powershell
cd frontend
npm test
npm run build
```

真实部署、GPU OCR、Milvus、Embedding、Routing 和离线迁移的验收范围及当前状态见 [`V7-CAPABILITY-MATRIX.md`](V7-CAPABILITY-MATRIX.md) 与 [`docs/releases/v7-acceptance.md`](docs/releases/v7-acceptance.md)。部署服务器的空卷重建顺序及生产保护规则见 [`wiki/pages/operations-and-testing.md`](wiki/pages/operations-and-testing.md) 和 [`AGENTS.md`](AGENTS.md)。

## 文档入口

| 需要了解 | 入口 |
| --- | --- |
| 当前架构 | [`docs/architecture/`](docs/architecture/overview.md) |
| 当前完成度 | [`V7-CAPABILITY-MATRIX.md`](V7-CAPABILITY-MATRIX.md) |
| 设计理由 | [`docs/adr/`](docs/adr/ADR-001-single-current-knowledge.md) |
| 功能规格与任务 | [`specs/`](specs/) |
| 项目 Wiki | [`wiki/index.md`](wiki/index.md) |
| 历史方案 | [`docs/archive/`](docs/archive/old-plan.md) |
