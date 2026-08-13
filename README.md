# DataForge V7 文档知识生产平台

DataForge V7 从新上传的 PDF、CSV、Markdown、DOC、DOCX、TXT 生成稳定的文本（文）、问答（问）和图谱（图）知识库。知识库保存单一当前态；来源版本、任务和变更历史承担溯源与审计。

## 数据边界

- V7 使用名为 `dataforge` 的 MySQL 数据库和 MinIO bucket；部署目标必须是空库或已有 V7 schema。
- 不迁移、不读取、不兼容旧数据、旧 MinIO 对象或旧 FAQ Collection；V7 只写自己的新 Source 对象键。
- DataForge 只管理四个新 Collection：`dataforge_text_knowledge`、`dataforge_qa_question`、`dataforge_qa_full`、`dataforge_graph_knowledge`。Partition 是 V7 `knowledge_library_id`，不是 `org_code`。
- `dataforge-migrate --upgrade-platform` 通过 Alembic 初始化空库或升级已有 V7 schema，并写入 V7 种子。
- 不存在自动删除旧 MinIO、旧 Milvus Collection 或旧 FAQ Partition 的代码。

## 运行架构

`frontend → dataforge-api → MySQL / MinIO`；`dataforge-worker` 通过租约调度，`dataforge-runner` 执行固定的受控知识流程。路由发布通过共享卷内的原子 `RoutingSnapshot` 文件交给未来下游只读消费。

## 开发与部署

```powershell
conda activate sun
uv sync --extra web
uv run --extra web dataforge-migrate --upgrade-platform
uv run --extra web dataforge-web
cd frontend
npm run build
```

Compose 始终使用 `dataforge`，并要求 MySQL 数据库为空或已是 V7 schema。迁移服务通过常规 Alembic 升级并写入种子；旧 Milvus 资源与旧 MinIO 对象保持不变。

## 核心接口

- `POST /api/document-libraries/{id}/sources/upload`：批量上传新文件。
- `POST /api/knowledge-jobs`：把模板输出显式绑定到长期知识库。
- `POST /api/knowledge-libraries/{id}/vector-sync-jobs`：创建 V7 向量同步任务。
- `POST /api/projects/{id}/routing/publish`：校验并原子发布 RoutingSnapshot。

更完整的实现、边界与运维说明见 [Wiki](wiki/index.md)。
