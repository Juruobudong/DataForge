# DataForge V7 文档知识生产平台

DataForge V7 从新上传的 PDF、CSV、Markdown、DOC、DOCX、TXT 生成稳定的文本（文）、问答（问）和图谱（图）知识库。知识库保存单一当前态；来源版本、任务和变更历史承担溯源与审计。

## 数据边界

- V7 使用名为 `dataforge` 的 MySQL 数据库和 MinIO bucket；部署目标必须是空库或已有 V7 schema。
- 常规流程不迁移、不读取、不兼容旧数据或旧 MinIO 对象；唯一受控例外是用户显式执行 `scripts/migrate-qa-agent-faq-test.sh`，它只读 `.34/faq` 并将规范文件写为新的 V7 Source，绝不修改旧 Collection。
- DataForge 管理内置与已发布扩展类型声明的受管 Collection；qa_agent FAQ 使用 `dataforge_qa_agent_faq`。正式知识资产使用不可变的 `kl_<knowledge_library_id>__v<asset_version_no>` Partition，逻辑知识库与 `org_code` 都不是可反复清空的物理 Partition。
- `dataforge-migrate --upgrade-platform` 通过 Alembic 初始化空库或升级已有 V7 schema，并写入 V7 种子。
- 不存在自动删除旧 MinIO、legacy/external Milvus Collection 或旧 FAQ Partition 的代码。
- qa_agent FAQ 手工文件使用 `faq-{org_code}.csv|xlsx` 命名；机构由文件名补入知识数据，文件中的可选机构列必须与文件名一致。

## 运行架构

`frontend → dataforge-api → MySQL / MinIO`；`dataforge-worker` 通过租约调度，`dataforge-runner` 执行固定的受控知识流程。智能中心冻结单项目 RouteVersion，再把同一机构的多个项目与共享 AssetVersion 组成签名 `.dfm`；机构本地可在缺少 Milvus 时先导入元数据/对象，验证候选目标后继续向量导入并逐项目激活原子 `RoutingSnapshot`。

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
- `POST /api/project-deployments/{id}/routing/freeze`：冻结机构项目 RouteVersion，不生成包。
- `/api/institution-deployments/*`：机构多项目 Seed、Institution Release 与不改路由的 Knowledge Update 草稿/差异/冻结/构建。
- `/api/migrations/*`、`/api/imported-route-candidates/*`：验签导入、等待恢复、逐项目或非原子批量激活。

机构本地保存 Milvus 密码或 Token 时必须配置 32 字节 `DATAFORGE_CONFIG_ENCRYPTION_KEY`（Base64 或 64 位十六进制）；凭据用 AES-256-GCM 入库，HTTP 响应不返回原值。

更完整的实现、边界与运维说明见 [Wiki](wiki/index.md)。
