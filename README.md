# DataForge V7

DataForge V7 是通用文档处理、知识生产与发布平台，支持从跨领域资料生成可追溯的文本、问答、图谱及扩展类型知识。医疗实体预设、医疗场景的机构 Deployment 与医疗业务接入是可选领域能力，不构成平台的默认边界。本文件只提供开发、运行和验证入口；系统事实见 [Architecture](docs/architecture/overview.md)，能力完成度见 [Capability Matrix](V7-CAPABILITY-MATRIX.md)。

## 环境要求

- 后端统一使用 Conda 环境 `sun`；所有 Python、`uv`、`pytest` 和 `dataforge-*` 命令都必须先执行 `conda activate sun`。
- 前端使用 Node.js 与 npm。
- 本地没有 Docker；Compose 只在部署服务器使用。本地后端需配置可用的 MySQL、MinIO，以及按测试范围需要的 Milvus、Embedding、Model Serving 和 MinerU。

## 安装后端依赖

```powershell
conda activate sun
uv sync --extra web
```

精选 QA、去重、修订算子还需要 Runner 的独立 Python 3.12 CPU 环境。安装与自定义包审核登记见 [算子运行环境](runtime/dataflow/README.md)。Runner 镜像已包含锁定的 DataFlow 环境；API/Worker 不安装 DataFlow，不在任务执行期间自动下载依赖。

## 初始化或升级 V7 Schema

目标必须是空数据库；命令会执行当前不可变 V7 baseline 并幂等写入当前种子，不支持旧 V7 head 或旧测试数据升级。

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

运行前需要按 [`compose.yaml`](compose.yaml) 中同名环境变量配置数据库、对象存储、Runner、Embedding、Model Serving、Milvus、Routing 与迁移目录。机构 local 保存 Milvus 密码或 Token 时，必须设置 32 字节 `DATAFORGE_CONFIG_ENCRYPTION_KEY`（Base64 或 64 位十六进制）。

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
