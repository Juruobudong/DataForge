# DataForge V7 能力矩阵

更新日期：2026-08-12
范围：仅 `dataforge.v7`、V7 前后端、V7 Alembic 与运行文档；不读取、不迁移或清理 V2、旧 Milvus/MinIO 资源，也不修改 `qa_agent`。

## 状态定义

| 标记 | 含义 |
| --- | --- |
| `DONE` | 已实现并已纳入本地自动化或前端构建验证。 |
| `CONNECT` | 代码与契约已具备，等待真实部署环境、凭据或业务数据接入。 |
| `BUILD` | 本期仍需开发的仓库内能力。 |
| `FIXED` | 平台受控能力，不允许在管理界面新增或修改。 |
| `DEFER` | 已明确排至下一期，不在 V7 本期实现。 |
| `REMOVE` | 从原型或导航中移除，不作为 V7 能力提供。 |

## 页面与操作

| 范围 | 页面或操作 | 状态 | 说明 |
| --- | --- | --- | --- |
| 业务工作区 | 工作台：文档、任务、知识库、Vector Ready、项目统计 | `DONE` | 使用现有列表接口汇总。 |
| 业务工作区 | 文档管理：目录树、文件夹递归上传、模板绑定、自动处理与安全删除 | `DONE` | `relative_path` 是目录权威；首次绑定创建结果知识库，之后仅新增/新版本处理；批次限 200 MiB/3 并发，只有运行任务阻断物理删除。 |
| 业务工作区 | 处理任务：监控、停止、重试、删除、日志 | `DONE` | 任务只从文档库模板绑定发起；`completed_with_warnings` 显示失败分块数与日志明细，重试仅执行失败组合；删除不会物理删除已形成的正式知识。 |
| 业务工作区 | 知识库详情：内容、Knowledge Diff、向量状态、来源追踪 | `DONE` | 历史仅 hash 的 Diff 会明确标注兼容状态。 |
| 业务工作区 | 知识库安全删除和失败重试 | `DONE` | Draft/已发布路由引用均阻止删除；仅异步清理目标 V7 Partition。 |
| 业务工作区 | 图谱浏览：实体搜索、1/2 跳邻居、关系 Evidence | `DONE` | 基于 MySQL 当前态三元组投影，不引入图数据库。 |
| 业务工作区 | 项目知识授权：验证、Diff、历史、Snapshot 预览、发布、回滚 | `DONE` | RoutingSnapshot 为项目的唯一发布物。 |
| 流程开发区 | 知识类型 | `DONE` | 初始仅 `text / qa / graph`；管理员可创建、修订、校验、发布扩展类型、Schema、来源策略、质量和 Index Profile 绑定。 |
| 流程开发区 | 标准流程 | `DONE` | Document Parse / Clean / Chunk / Production / Publish 由受控子图与节点组成。 |
| 流程开发区 | 模板 / 算子库 / 可复用子图 | `DONE` | Flow DSL v3 支持白名单强类型节点拖拽、分支、受控合流与不可变快照；禁止任意代码、环和运行时改图。 |
| 流程开发区 | DataFlow 调试台 | `DONE` | 只读 V7 诊断：快照、展开 DAG、节点、Artifact 血缘、已发布 Profile、容量和最近任务。 |
| 流程开发区 | 独立“向量索引”导航页 | `REMOVE` | 已收口进 DataFlow 调试台；旧链接重定向，仍只读。 |
| 原型能力 | DataFlow WebUI、Shell、任意 Python、运行时改图 | `REMOVE` | 与受控 Flow 和 Runner 安全边界冲突。 |
| 原型能力 | 任意上游算子、KCenterGreedy、MultiHop Batch、Reference Remover、训练/代码/Text2SQL/Agentic-RAG | `REMOVE` | 不在 V7 Catalog；P1 Refiner/MultiHop/PII 仍需专门批准。 |

## API 与数据契约

| 契约 | 状态 | 说明 |
| --- | --- | --- |
| 知识库 `delete-check`、删除任务、重试 | `DONE` | 删除状态为 `active → deleting → deleted`；失败保持 `deleting`。 |
| 知识库/项目自动编码与请求拒绝 | `DONE` | 知识库由服务端生成 `KL-YYYYMMDD-UUID4`，项目生成 `PRJ-YYYYMMDD-UUID4`；客户端提交 `code` 被拒绝。 |
| 文档库模板绑定与自动结果库 | `DONE` | 一个文档库可绑定多个已发布模板；每个输出类型固定一个结果库，首次全量、后续增量、模板新修订全量。 |
| `knowledge_item_sources`、Evidence、结构化锚点 | `DONE` | 返回文档、SourceVersion、锚点、Evidence 与 primary 标识。 |
| Knowledge Diff 和向量状态 | `DONE` | 新变更有可读 before/after；旧记录兼容 hash。 |
| 受控模板修订 CRUD、默认、校验、发布、样例运行 | `DONE` | 生产任务固定引用已发布修订。 |
| Operator / Prompt / Quality / Subflow / Snapshot / Flow Run 接口 | `DONE` | Catalog 屏蔽内部 DataFlow 类名；任务固定 `execution_snapshot_id`，可读取节点和 Artifact 诊断。 |
| Knowledge Sink Schema/来源/质量 Gate | `DONE` | `review` 与失败候选阻断该 Sink；多 Sink 独立事务写入。 |
| Qwen 分块生成、失败保留与局部重试 | `DONE` | Runner 通过权威 `global_llm` 固定 `qwen3_32b`；每个类型×来源版本×SourceChunk 持久化最新结果，成功空结果撤销、失败保留、全部失败零写入。 |
| OpenAI-like Embedding 与发布 Profile 约束 | `DONE` | 使用 `EMBEDDING_*` 和 `OpenAILikeEmbedding`；发布的模型/维度仍约束运行，环境模型/维度仅初始化新默认 Profile。 |
| 图谱实体、详情、邻居、关系 Evidence | `DONE` | 深度限制为 1/2 跳，重复三元组聚合 Evidence。 |
| 双图谱模式与受管 Collection | `DONE` | 顶层保持 graph；Triple/Semantic 使用两个专属 Storage Contract/Collection，文本与 QA 两路也纳入默认五个受管 Collection；同规格哈希复用，旧 Graph Collection 冻结兼容。真实供应仍属部署验收。 |
| RoutingSnapshot Diff、版本列表、单版本预览、发布、回滚 | `DONE` | 保持已有路由契约并由前端接入。 |
| 已发布 Collection 与 `kl_` Partition 生命周期 | `DONE` | 管理员可指定任意已有兼容 Collection；平台仅管理每库 `kl_` 分区，严禁删除 Collection。 |
| V7 受控重建 | `DONE` | 仅 `dataforge-migrate --rebuild-v7 --confirm=REBUILD-V7`，基于 DB manifest 删除 V7 对象/分区/表数据，绝不删除 Collection 或旧资源。 |

## 尚未完成项（按执行顺序）

这些项均需要仓库以外的部署资源、真实业务数据或上线授权；它们不是可在当前本机伪造完成的开发任务。

| 顺序 | 未完成能力 | 状态 | 完成条件 |
| --- | --- | --- | --- |
| C-01 | 准备真实 V7 运行环境 | `CONNECT` | 提供空或已升级的 MySQL `dataforge`、MinIO bucket、Milvus URI/token、`EMBEDDING_*`、`LOCAL_LLM_API_KEY`、服务器 `/data/zoe-ai-proj/global_llm` 和可写 RoutingSnapshot volume；同机独立 Milvus 还须安装 `dataforge-milvus-egress.timer`。 |
| C-02 | 真实集成验收 | `CONNECT` | 执行 Compose config/Runner build/`import global_llm`、Alembic 升级、上传、分块 Qwen 任务/失败重试、真实向量同步、Partition load/search/release、容量阈值与安全删除；验证 API/Worker 经专用网络的 Milvus/Embedding 最小出站、Milvus 重建恢复，确认没有 Collection 删除。 |
| C-03 | 三个目标项目接入 | `CONNECT` | 在 V7 中创建项目/任务/路由，使用新建 V7 知识库发布并核对各自 Snapshot；不迁移或读取 V2。 |
| C-04 | 真实失败恢复验收 | `CONNECT` | 验证 Milvus/Embedding/runner 不可用、worker 租约恢复、删除任务重试及前端告警。无 Milvus 的本地删除失败/重试已自动化覆盖。 |
| C-05 | 上线签字与验收记录 | `CONNECT` | 依照 `docs/releases/v7-acceptance.md` 留存 C-01～C-04 的证据、负责人和批准记录。 |
| C-06 | `qa_agent` 消费新的 V7 RoutingSnapshot | `DEFER` | 后续独立项目；本期不改 `qa_agent`。 |
| C-07 | 旧 MySQL/MinIO/Milvus 资源处置 | `DEFER` | 仅在人工清单、备份和明确批准后进行，V7 不自动清理。 |

## 执行记录

- 本矩阵创建时已完成本期唯一可本地完成的导航收口：开发区第四页为只读「DataFlow 调试台」，向量状态作为其诊断面板的一部分保留。
- 2026-08-12：本地回归 40 passed，覆盖三类内置种子、自动编码/`code` 拒绝、扩展类型发布、Runner 启动 Qwen 初始化/多项结构化结果/一次修复、分块失败保留/空结果撤销/身份变更替换/版本缩减跨类型全成功清理与失败重试后清理、OpenAI-like Embedding 及 Profile 维度、文档库自动结果库与增量防重、来源删除处理和动态路由；真实 MinerU API/Local/Flash、Qwen、Milvus/Embedding 仍需 C-01～C-04 部署验收。
- C-01 至 C-05 是本期的下一执行队列。每完成一项，应更新本文件状态、`wiki/`、Devora 测试用例和上线证据；不得将模拟或缺失依赖的结果写成真实部署验收。

## 依据

- `specs/005-v7-clean-data-platform/tasks.md`
- `specs/005-v7-clean-data-platform/test-cases.md`
- `wiki/pages/roadmap.md`
- `wiki/pages/operations-and-testing.md`
