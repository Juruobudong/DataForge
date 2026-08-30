# DataForge 当前架构概览

> 当前状态：已实现架构，更新于 2026-08-29。
> 本目录只描述当前系统事实；完成度与外部环境验收状态以 [`V7-CAPABILITY-MATRIX.md`](../../V7-CAPABILITY-MATRIX.md) 为准，设计原因以 [`docs/adr/`](../adr/ADR-001-single-current-knowledge.md) 为准。

## 系统定位

DataForge V7 是面向跨领域资料的通用文档处理、受控知识生产与发布平台。它从 V7 新上传的 PDF、CSV、XLSX、Markdown、DOC、DOCX、TXT 等资料生成文本、问答、图谱及已发布扩展类型知识；逻辑知识库保存单一当前态，来源版本、执行快照、知识变更和不可变资产版本承担溯源、发布与回滚。

通用主链不依赖具体行业词表、文档内容或部署主体。医疗实体预设、医疗场景的机构 Deployment、qa_agent 接入及医疗样例是可选领域配置与集成，用来复用同一套 Source、Preparation、Review、Flow、Knowledge Sink、AssetVersion 和 Routing 契约，不定义平台范围。新增能力默认先验证跨领域文档语义，再按需增加领域预设。

平台不是通用脚本编排器。生产流程只能使用已登记的 Operator、强类型端口、已发布修订和不可变 `FlowExecutionSnapshot`；任意 Python、Shell、循环与运行时改图不属于 V7 能力。

## 运行时组件

```text
PC 浏览器
  → Vue frontend
  → dataforge-api
      ├─ MySQL：治理状态、当前知识、版本、任务、授权和审计
      ├─ MinIO：Source、Artifact、迁移包及其他对象
      ├─ dataforge-worker：租约调度、迁移、删除和向量任务
      │    └─ dataforge-runner：冻结 Flow 的确定性执行
      │         ├─ mineru-api：PDF Pipeline GPU OCR
      │         └─ Model Serving / Embedding
      ├─ Milvus：受管或 external Collection 中的版本化 Partition
      └─ RoutingSnapshot volume：按 Project、Deployment、阶段原子发布
```

- API 与 Worker 负责治理、排队和外部存储协调；Runner 执行受控知识流程。
- Standard 保存固定阶段配置并只读展示真实算子；Advanced 保存完整强类型 DAG。两者经编译器生成冻结快照。
- Runner 按精确版本构建 Native/DataFlow/Custom Registry。首批 QA、去重、修订调用独立 Python 3.12 CPU 环境中的 `open-dataflow==1.0.10`；适配层只转换契约、参数、Serving 和血缘。图谱、原文映射、治理及 Sink 保持原生。
- 维护人员安装可信插件，管理员通过 Manifest、真实样例、契约和血缘验证后发布。快照固定包/依赖/环境摘要，运行前检查漂移；插件只取得序列化上下文和 Serving 代理，不取得数据库或模型凭据。子进程故障隔离不代替代码审核。
- 知识处理和 Vector Sync 通过知识库工作租约互斥；不同知识库可并行，多输出任务原子取得全部目标库租约。
- PDF 固定经 MinerU 3.4.4 `pipeline + auto + ch` 解析；一次性强制 OCR 只允许管理员对 PDF 派生调试 Run 使用。
- 运行调试已经实现为 V7 自有 Execution Sandbox：管理员可从 Draft/Published Revision选择版本化内置审核 Sample，或同库多个当前审核 Snapshot；输入统一冻结后由既有 Runner 执行。内置 Sample 使用虚拟空库 Diff，真实输入使用运行时 KnowledgeLibrary Diff，二者均不创建 KnowledgeJob、不写正式知识；Runtime DAG 只读。它不是 DataFlow WebUI 或通用 Studio。
- 流程开发区旧“向量索引”导航仍已移除并重定向到 DataFlow 调试台；业务工作区另提供“向量存储”实时库存，聚合 Collection/Partition 与逻辑资产关系并开放受控 verify/load/release，不恢复开发区索引编辑能力。

## 数据与存储边界

- MySQL 与 MinIO 使用 `dataforge` 名称；测试阶段数据库只接受空环境执行当前单一 V7 baseline，不升级旧 V7 head。
- V7 常规流程不读取、迁移或自动清理 DataForge V2 数据、旧 MinIO 对象、legacy/external Milvus Collection。
- Source 是逻辑资料；SourceVersion 以 `(source_id, sha256)` 唯一并引用全局内容寻址 Blob。历史版本经确认可重新启用，但每次 activation 都由 Dispatch/Job/Sink Gate 独立复验；正式 SourceChunk、Evidence、Artifact 与 Flow Run 保留执行溯源。
- KnowledgeLibrary 是逻辑当前态；正式向量资产是不可变 `KnowledgeAssetVersion`，物理 Partition 为 `kl_<knowledge_library_id>__v<asset_version_no>`。
- 发布的 RoutingSnapshot 只引用通过校验的 Ready AssetVersion，不对运行中的 Partition 执行 reset 或 upsert。
- 删除知识库只清理其 V7 Partition；整库删除是独立治理流程，仅适用于 ownership 与引用门禁全部通过的 DataForge 受管 Collection。

## 部署边界

- central 实例是多 Deployment 控制面，可管理中央环境和多家机构环境。
- 顶层 Deployment 表达机构或中心运行环境；ProjectDeployment 只表达 Project 与该环境的关联。同一 Deployment 可承载多个 Project，但任务、授权、RouteVersion、Snapshot 与回滚互相隔离。
- local 实例通过一次 `deployment_seed` 绑定唯一 Deployment，之后独立自治；中央 Knowledge Update 不覆盖本地授权、Routing、机构代码或本地资产。
- 中心在线 Routing 发布与离线 `.dfm` 迁移是两条不同路径。中心不会通过离线迁移连接机构 local Milvus，local 私有资产也不会回传中心。

## 文档职责

| 文档 | 回答的问题 |
| --- | --- |
| 根 [`README.md`](../../README.md) | 如何安装、运行和找到入口 |
| `docs/architecture/` | 当前系统是什么、如何工作 |
| [`V7-CAPABILITY-MATRIX.md`](../../V7-CAPABILITY-MATRIX.md) | 哪些能力已完成、哪些仍需外部验收 |
| [`docs/adr/`](../adr/ADR-001-single-current-knowledge.md) | 为什么采用当前设计 |
| [`docs/archive/`](../archive/old-plan.md) | 历史方案，仅供追溯 |
| [`specs/`](../../specs/) | 单项功能的规格、实施计划、任务和验收 |

## 来源与关联

- 当前事实：`src/dataforge/v7/`、`frontend/src/`、`compose.yaml`、Alembic revision 与自动化测试。
- 详细知识层：[`wiki/pages/system-architecture.md`](../../wiki/pages/system-architecture.md)、[`wiki/pages/core-workflows.md`](../../wiki/pages/core-workflows.md)、[`wiki/pages/deployment-and-migration.md`](../../wiki/pages/deployment-and-migration.md)。
- 专题架构：[知识生命周期](knowledge-lifecycle.md)、[资产版本](asset-version.md)、[Routing](routing.md)、[Deployment 与离线迁移](deployment-migration.md)。
