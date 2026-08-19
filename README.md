# DataForge V7 文档知识生产平台

DataForge V7 从新上传的 PDF、CSV、Markdown、DOC、DOCX、TXT 生成稳定的文本（文）、问答（问）和图谱（图）知识库。知识库保存单一当前态；来源版本、任务和变更历史承担溯源与审计。

## 数据边界

- V7 使用名为 `dataforge` 的 MySQL 数据库和 MinIO bucket；部署目标必须是空库或已有 V7 schema。
- 常规流程不迁移、不读取、不兼容旧数据或旧 MinIO 对象；唯一受控例外是用户显式执行 `scripts/migrate-qa-agent-faq-test.sh`，它只读 `.34/faq` 并将规范文件写为新的 V7 Source，绝不修改旧 Collection。
- DataForge 管理内置与已发布扩展类型声明的受管 Collection；qa_agent FAQ 使用 `dataforge_qa_agent_faq`。所有知识库仍使用 `kl_<knowledge_library_id>` Partition，物理 Partition 不是 `org_code`。
- `dataforge-migrate --upgrade-platform` 通过 Alembic 初始化空库或升级已有 V7 schema，并写入 V7 种子。
- 不存在自动删除旧 MinIO、legacy/external Milvus Collection 或旧 FAQ Partition 的代码。
- qa_agent FAQ 手工文件使用 `faq-{org_code}.csv|xlsx` 命名；机构由文件名补入知识数据，文件中的可选机构列必须与文件名一致。

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
