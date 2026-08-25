# DataForge V7 能力矩阵

更新日期：2026-08-24
范围：`dataforge.v7`、V7 前后端/Alembic/运行文档，以及 `qa_agent` 的 DataForge Runtime Routing 客户端；常规流程不读取、迁移或清理旧资源，唯一例外是显式执行的 `.34/faq` 只读导入，旧 Collection 始终不变。

本文件只记录能力完成度与外部验收状态；当前系统结构见 [`docs/architecture/`](docs/architecture/overview.md)，设计原因见 [`docs/adr/`](docs/adr/ADR-001-single-current-knowledge.md)，历史方案见 [`docs/archive/`](docs/archive/old-plan.md)。

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
| 业务工作区 | 文档管理：目录树、递归上传、SourceChunk 人工审核 Gate、模板绑定与安全删除 | `DONE` | 上传/替换自动 Parse/Clean/Chunk 后停在待审核；双栏工作区支持 PDF.js 多页 bbox、DOCX 结构块证据定位，以及修改、拆分、连续合并、删除、通过/拒绝/重开，文档批准后自动调度已绑定模板。真实 `.34` E2E 为 `CONNECT`。 |
| 业务工作区 | 处理任务：监控、停止、重试、删除、日志 | `DONE` | 任务只从文档库模板绑定发起；模板和目标知识库名称优先于技术 ID，`completed_with_warnings` 显示失败分块数与日志明细，重试仅执行失败组合；删除不会物理删除已形成的正式知识。 |
| 业务工作区 | 知识库详情：内容、Knowledge Diff、向量状态、来源追踪 | `DONE` | 总览按类型统计活跃知识；单库详情使用独立路由，历史仅 hash 的 Diff 会明确标注兼容状态。 |
| 业务工作区 | 知识库安全删除和失败重试 | `DONE` | Draft/已发布路由引用均阻止删除；仅异步清理目标 V7 Partition。 |
| 业务工作区 | 向量存储：Collection/Partition 库存、资产映射与受控运维 | `DONE` | 实时聚合 Milvus 与 AssetVersion/Routing/Release/GC；普通刷新只读 stats，显式 verify 持久化最近 count/digest；load/release 仅限严格受管版本 Partition，无任意 drop。`.34` 真实对账仍为 `CONNECT`。 |
| 业务工作区 | 菜单排序、显隐与恢复默认 | `DONE` | Menu Registry 与 `dataforge.workspace-menu.v1` 仅保存 order/hidden；支持拖拽和上下按钮，隐藏不影响路由或权限。 |
| 业务工作区 | 图谱浏览：默认概览、实体搜索、1/2 跳邻居、关系 Evidence | `DONE` | 基于 MySQL 当前态三元组投影，不引入图数据库；默认概览按连接度选择最多 80 节点和 160 边。 |
| 业务工作区 | 项目发布：任务/授权/优先级、Ready AssetVersion、RouteVersion 冻结与在线发布 | `DONE` | Routing Validate 返回结构化配置、AssetVersion、Contract 与 live Milvus Collection/字段/dimension/Partition 检查；中心到 institution Deployment 使用 `deferred_to_local` 且不连接机构现场。institution scope 只 freeze，central scope 保留在线发布。 |
| 业务工作区 | 机构发布部署：多项目 Seed/Release、额外资产、统一 Inventory、Knowledge Update、Prepare/Verify/Activate | `DONE` | `.dfm` v2、项目资产锁定与额外 AssetVersion、结构化冲突门禁、模板闭包、正常 waiting、Prepare target fingerprint、Partition count/digest Activation Preflight 和逐项目/非原子批量激活已本地验证；`.34` 真实服务验收仍为 `CONNECT`。 |
| 业务工作区 | local 初始化、手动组件健康与导入任务详情 | `DONE` | Worker/Runner 15 秒心跳；九类组件支持单项、多选和全选真实检查，结果 15 分钟 stale；向导不动态配置 MySQL/MinIO。真实服务仍归 C-04。 |
| 流程开发区 | 知识类型 | `DONE` | 初始仅 `text / qa / graph`；扩展 Type 自动生成可改名的受管 Profile，Manual Profile 明确区分 `create / attach`，页面展示 ownership、Contract、Partition、引用和删除任务。 |
| 流程开发区 | 模型服务 | `DONE` | LLM/Embedding 独立持久化、Secret 脱敏、真实测试、默认/启停/引用管理；真实 `.34` 调用仍为 `CONNECT`。 |
| 流程开发区 | 标准流程 | `DONE` | Document Parse / Clean / Chunk / Production / Publish 由受控子图与节点组成。 |
| 流程开发区 | 知识流程模板 / 算子库 / 可复用子图 | `DONE` | Schema 驱动 ServingSelector、模型状态节点卡、九类动态目录与共享 FlowCanvas；发布 Snapshot 冻结 Serving code。 |
| 流程开发区 | DataFlow 调试台 | `DONE` | 三栏 Runtime DAG + Console、Node/Artifact Inspector、游标事件、派生节点运行、协作式取消与 Sink Diff 确认；派生执行/提交开关默认关闭。 |
| 流程开发区 | 独立“向量索引”导航页 | `REMOVE` | 已收口进 DataFlow 调试台；旧链接重定向，仍只读。 |
| 原型能力 | DataFlow WebUI、Shell、任意 Python、运行时改图 | `REMOVE` | 与受控 Flow 和 Runner 安全边界冲突。 |
| 原型能力 | 任意上游算子、KCenterGreedy、MultiHop Batch、Reference Remover、训练/代码/Text2SQL/Agentic-RAG | `REMOVE` | 不在 V7 Catalog；P1 Refiner/MultiHop/PII 仍需专门批准。 |

## API 与数据契约

| 契约 | 状态 | 说明 |
| --- | --- | --- |
| 知识库 `delete-check`、删除任务、重试 | `DONE` | 删除状态为 `active → deleting → deleted`；失败保持 `deleting`。 |
| 知识库/项目自动编码与请求拒绝 | `DONE` | 知识库由服务端生成 `KL-YYYYMMDD-UUID4`，项目生成 `PRJ-YYYYMMDD-UUID4`；客户端提交 `code` 被拒绝。 |
| 文档库模板绑定与自动结果库 | `DONE` | 一个文档库可绑定多个已发布模板；每个输出类型固定一个结果库，首次全量、后续增量、模板新修订全量。 |
| Source Preparation / Review Snapshot / Knowledge Dispatch | `DONE` | Preparation 与 Knowledge Flow 分离；只有全部活动 Chunk approved 才冻结不可变 Snapshot 并幂等 Dispatch，未审核的整库、选中文件、直接 Job 均服务端拒绝。 |
| SourceChunkSet / Rechunk / SourceAnchor 精确审核工作台 | `LOCAL` | Candidate/Active/Superseded/Failed 生命周期、Snapshot 参数化结构分块、Retry/Rechunk、批量审核、原子 Promote、SourceAnchorV2、PDF.js 多页 bbox、DOCX 结构块与独立 Workbench 已完成本地实现；真实 `.34` E2E 待验收。 |
| Reviewed Flow 与下游多层 Gate | `DONE` | 知识模板唯一根为 Reviewed SourceChunk Input；Job/Runner/Sink/Evidence/AssetVersion/Vector/Ready/Routing 逐层复验 Snapshot 或 digest，Milvus 写入位于 LLM/Operator、Knowledge Sink 与 Embedding 之后。 |
| `knowledge_item_sources`、Evidence、结构化锚点 | `DONE` | 返回文档、SourceVersion、锚点、Evidence 与 primary 标识。 |
| Knowledge Diff 和向量状态 | `DONE` | 新变更有可读 before/after；旧记录兼容 hash。 |
| 受控模板修订 CRUD、默认、校验、发布、样例运行 | `DONE` | 生产任务固定引用已发布修订。 |
| Operator / Prompt / Quality / Subflow / Snapshot / Flow Run 接口 | `DONE` | Catalog 屏蔽内部 DataFlow 类名；任务固定 `execution_snapshot_id`，可读取节点和 Artifact 诊断。 |
| 派生 Run 与 Sink 暂存提交 | `DONE` | `node_only/from_node` 复用同快照可重放 Artifact；Sink 默认 `awaiting_commit`，以 checksum、当前态冲突检测和幂等键确认提交。 |
| Knowledge Sink Schema/来源/质量 Gate | `DONE` | `review` 与失败候选阻断该 Sink；多 Sink 独立事务写入。 |
| Model Serving 分块生成、失败保留与局部重试 | `DONE` | DB Registry 默认 `qwen3_32b`，新默认策略 120 秒/2 次重试；Flow 发布健康门禁，Snapshot 冻结 code，分块失败隔离不变。 |
| PDF MinerU Pipeline GPU OCR | `DONE` | 所有 PDF 固定调用 MinerU 3.4.4 `pipeline + auto + ch`；Markdown、`0~1` 多位置 SourceAnchor 与 Middle JSON Artifact 已纳入失败保真、精确删除和 V7 重建。本地自动化完成，真实 GPU/坐标验收归 C-01～C-04/C-11。 |
| OpenAI-like Embedding 与发布 Profile 约束 | `DONE` | DB Embedding Registry 默认 BCE 768；Profile/Contract/Milvus 强制维度一致，Vector Sync 按 Profile 动态选 Provider，AssetVersion 冻结血缘。 |
| 图谱实体、详情、邻居、关系 Evidence | `DONE` | 深度限制为 1/2 跳，重复三元组聚合 Evidence。 |
| 双图谱模式与受管 Collection | `DONE` | 顶层保持 graph；Triple/Semantic 使用两个专属 Storage Contract/Collection，文本与 QA 两路也纳入默认五个受管 Collection；同规格默认独立、仅显式选择兼容 ready 登记时复用。旧 `graph` Profile 仅供已有库冻结兼容，不参与受管供应或容量探测；真实供应仍属部署验收。 |
| RoutingSnapshot / AssetVersion / ImportedRouteCandidate | `DONE` | 按 ProjectDeployment/阶段隔离；Snapshot v3 指向 Ready `kl_*__vN`，local 单项目激活原子，批量明确非原子。 |
| Instance / Deployment / local Milvus Target | `DONE` | 服务端实例身份不可由 URL 覆盖；Deployment 可关联多个 Project，机构码可锁定；local current/candidate/preset 凭据 AES-GCM 入库且响应脱敏。 |
| `.dfm` v2 Seed / Institution Release / Knowledge Update | `DONE` | 多 frozen 项目、完整当前资产、差异/Tombstone、模板运行闭包、Ed25519、v1 导入兼容与检查点恢复。 |
| Knowledge Type / Profile 发布契约 | `DONE` | 草稿只登记 planned 资源；发布自动 Provision 扩展/Manual create，实时校验 Manual attach，再冻结 Profile 与 Type Revision；同一 Type Revision 拒绝重复 Collection。 |
| qa_agent FAQ 专用文件生产与固定迁移 | `DONE` | `qa-agent-faq`、自动 Profile、受管 Collection、无 LLM CSV/XLSX 模板、固定 12 Partition CLI/Bash 和 qa_agent `legacy/shadow/primary` 已实现并完成本地定向测试；真实导入归 C-10。 |
| Collection 与版本化 `kl_*__vN` Partition 生命周期 | `DONE` | 候选构建不 reset 运行版本；GC 引用保护、30 天和最近两版门禁，默认 dry-run 且只由显式 Job 执行。受管整库治理保持独立。 |
| `/api/vector-storage/*` 实时库存与校验 | `DONE` | overview/list/detail 聚合实时 Milvus 与 MySQL，pymilvus 元数据先转为普通 JSON；业务列表默认仅托管并可切全量，API 缺省仍全量；verify 才全量 digest 并保存最近结果；load/release 每次复验 ownership/Contract/资产映射；本命名空间无 DELETE。 |
| V7 受控重建 | `DONE` | 仅 `dataforge-migrate --rebuild-v7 --confirm=REBUILD-V7`，基于 DB manifest 删除 V7 对象/分区/表数据，绝不删除 Collection 或旧资源。 |

## 尚未完成项（按执行顺序）

这些项均需要仓库以外的部署资源、真实业务数据或上线授权；它们不是可在当前本机伪造完成的开发任务。

| 顺序 | 未完成能力 | 状态 | 完成条件 |
| --- | --- | --- | --- |
| C-01 | 准备真实 V7 运行环境 | `CONNECT` | 提供空或已升级的 MySQL `dataforge`、MinIO、Milvus、`DATAFORGE_CONFIG_ENCRYPTION_KEY`、可写 RoutingSnapshot volume及 NVIDIA GPU；migrate 可用旧 YAML/Embedding 环境初始化，随后在模型服务页完成真实测试。 |
| C-02 | 真实集成验收 | `CONNECT` | 执行 Compose config/Runner build/Serving Registry 默认项检查、Alembic 升级、MinerU 双路径 health 与局域网拒绝、文本/扫描 PDF、指定三分块 QA、真实向量同步、Partition load/search/release、Manual attach/create、扩展自动 Provision 与受管删除门禁；确认 external/非受管 Collection 不能删除。 |
| C-03 | 三个目标项目接入 | `CONNECT` | 在 V7 中创建项目/任务/路由，使用新建 V7 知识库发布并核对各自 Snapshot；不迁移或读取 V2。 |
| C-04 | 真实失败恢复验收 | `CONNECT` | 手动组件检查、匿名脱敏、Worker/Runner 心跳与前端告警已完成仓库自动化；仍须在 `.34` 验证 Milvus/Embedding/Runner/MinerU/MinIO 不可用、Worker lease 恢复和删除任务重试。 |
| C-05 | 上线签字与验收记录 | `CONNECT` | 依照 `docs/releases/v7-acceptance.md` 留存 C-01～C-04 的证据、负责人和批准记录。 |
| C-06 | `qa_agent` 消费新的 V7 RoutingSnapshot | `DONE` | 绑定机构/阶段、LKG、dense `dataforge_qa_question/kl_*`、回退与 503 已实现并完成定向测试。 |
| C-07 | 旧 MySQL/MinIO/Milvus 资源处置 | `DEFER` | 仅在人工清单、备份和明确批准后进行，V7 不自动清理。 |
| C-08 | 多项目 Seed/Release/Update 真实离线迁移验收 | `CONNECT` | 在 `.34` 空卷环境挂载签名与配置加密密钥，完成双项目 Seed、无 Milvus waiting、candidate 验证/导入、逐项目/非原子激活、Update 路由不变、再发布采用新资产、Tombstone/Fork/失败恢复与 GC dry-run。 |
| C-09 | 共享机构 Deployment 多 Project 真实验收 | `CONNECT` | 验证机构名称/代码、逐机构 production URI、qa-agent 与 kg-for-consultation 共用 deployment code 但 Snapshot/授权独立、相同 org_code 跨 Project/机构隔离、Routing 分项检查、生产备份恢复、Embedding 契约、dense 召回和 503 fail-closed。 |
| C-10 | qa_agent FAQ `.34` 导入与文件替换验收 | `CONNECT` | 运行固定 dry-run/execute，证明 12 个文档库/Source/结果库/`kl_*`、MySQL 与目标 Milvus 8,281 条一致、旧 `faq` 完全不变；替换一个测试机构文件验证 ADD/UPDATE/INACTIVE 和新向量。随后单独配置 FAQ Routing 并按 shadow/primary 切换。 |
| C-11 | SourceChunk 人工审核 Gate 真实链路验收 | `CONNECT` | 用户同步代码并清空测试数据后，在 `.34` 验证文本/扫描 PDF、多栏/跨页 bbox、DOCX 标题/表格块、TXT/CSV 只自动准备；批准前无 LLM/Knowledge/Milvus 调用，批准后按绑定自动生成，最终顺序为 Sink → Embedding → AssetVersion/Milvus → Ready；不访问 `.36`。 |
| C-11 | 向量存储 `.34` 实时库存验收 | `CONNECT` | 对一个精确 DataForge-owned 测试 AssetVersion 核对 overview/list/详情，证明普通刷新无 full scan，执行 verify 与 load/release；GC 只 dry-run，未托管资源保持只读。 |

## 执行记录

- 本矩阵创建时已完成本期唯一可本地完成的导航收口：开发区第四页为只读「DataFlow 调试台」，向量状态作为其诊断面板的一部分保留。
- 2026-08-13：MinerU 3.4.4 Pipeline GPU OCR 仓库实现完成；相关 V7 回归 60 passed，覆盖 Adapter、文本/扫描 PDF Runner、Artifact 补偿与删除生命周期、失败保真和 Compose 静态契约。真实 CUDA/模型、文本/扫描 PDF、回环端口和服务恢复仍需 C-01～C-04 部署验收。
- 2026-08-14：007 流程开发工作台仓库实现完成；工作台定向后端 7 passed、相关后端回归 56 passed、前端逻辑 13 passed、Vite 构建通过。真实派生 OCR/LLM、Sink 提交和向量同步仍须在 C-01～C-04 部署环境按开关分阶段验收。
- 2026-08-14：008 Knowledge Type / Profile / Collection 生命周期仓库实现完成；相关后端回归 87 passed、前端逻辑 15 passed 与 Vite 构建通过。真实 Milvus Provision、ownership marker 和受管整库删除演练仍归 C-01～C-04。
- 2026-08-24：013 手动组件健康检查仓库实现完成；新增 Worker/Runner 心跳、九类手动真实探针、匿名脱敏摘要、管理员检查 run、首页单项/多选/全选和 queued 可能原因。本地定向后端 10 passed、前端 45 tests 与生产构建通过；真实 GPU/Serving/存储故障恢复仍归 C-04。
- 2026-08-14：QA 知识生成改为 DataForge Model Serving Registry 直连；Catalog/Flow/派生覆盖统一使用稳定 Serving ID，默认 `qwen3_32b`，移除 `global_llm` 构建上下文。定向回归 50 passed、四文件相关 V7 回归共 92 passed；真实三分块 QA 仍归 C-02。
- 2026-08-17：009 Deployment Fork 与离线迁移仓库实现完成；新增 Deployment 级授权/路由、local 后端边界、签名 `.dfm`、最小依赖 Planner、Parquet Partition、幂等 Worker、Fork 冲突和前端页面。真实服务与密钥验收归 C-08。
- 2026-08-17：机构 qa-agent Routing 扩展完成；新增机构/阶段 schema、Snapshot v3、双 Milvus production delivery/备份、Runtime Token/ETag、前端生产确认和 qa-agent 绑定机构 dense 检索。真实双环境验收归 C-09。
- 2026-08-18：机构运行环境与项目发布关联拆层；`dataforge-central` 可同时关联 qa-agent 与 kg-for-consultation，阶段 Target 归顶层 Deployment，授权/版本按 ProjectDeployment 隔离，机构 production URI 改为逐机构手工配置。真实共享环境验收仍归 C-09。
- 2026-08-18：修复空库初始化遗漏 qa-agent；seed 现在幂等创建 `qa-agent / knowledge_qa / qa-question` 的中央 Project、ProjectDeployment 与 DeploymentTask，不自动授权或发布，三文件组合回归 49 passed。能力状态不变，空卷真实验证仍归 C-08/C-09。
- 2026-08-18：按新治理增加 `.34` 空卷遗留内置 Collection 手动清理 CLI；固定五项 allowlist、ownership/schema/index/Partition/行数门禁、dry-run/动态确认/显式 execute 和删除后复核已实现，真实 dry-run 识别 476 行但未执行删除。空卷 Provision 复验仍归 C-08/C-09。
- 2026-08-18：部署实测发现镜像未注册清理 CLI；单 Bash 入口改为把自包含安全载荷只读挂载到现有 provision 容器执行，不再依赖镜像重建、源码安装或 console entry。静态与单元验证通过，真实删除/Provision 仍待用户重试。
- 2026-08-18：实时盘点证明清理已创建五个 0 行新 Collection，但随机 seed token 在后续空卷再次失配；内置 token 改为稳定 ID+名称+Contract hash 的确定值，两个独立空库回归一致。首次升级仍需清理旧随机 marker，后续同 Contract 空卷重建不再复发。
- 2026-08-18：010 qa_agent FAQ 仓库实现完成；新增专用类型/受管 Collection、确定性 CSV/XLSX 模板、固定 `.34/faq` 导入 CLI 与单 Bash，以及 qa_agent 三阶段读取。DataForge 7 项、qa_agent 20 项定向测试通过；未执行 `.34` 写入、Routing 发布或模式切换，真实验收归 C-10。
- 2026-08-19：FAQ 手工上传契约改为 `faq-{org_code}.csv|xlsx` 文件名承载机构，CSV 可省略机构列；导出12份修正版 CSV/manifest/checksum 共8,281行，DataForge定向12项和表格运行时12文件导入/渲染通过。部署 API 尚无 FAQ 类型/模板/文档库，预建与上传仍归 C-10。
- 2026-08-24：015 SourceChunk 人工审核 Gate 仓库实现完成；Preparation、不可变 Chunk Revision/Review Snapshot、自动 Dispatch、Reviewed Flow、Sink/Vector/Routing fail-closed 与 PC 双栏审核工作区已落地。平台 53、迁移 13、前端 50 项及相关定向回归通过；真实 `.34` 顺序与服务验收归 C-11。
- C-01 至 C-05 是本期的下一执行队列。每完成一项，应更新本文件状态、`wiki/`、Devora 测试用例和上线证据；不得将模拟或缺失依赖的结果写成真实部署验收。

## 依据

- `docs/architecture/`
- `docs/adr/`
- `specs/005-v7-clean-data-platform/tasks.md`
- `specs/005-v7-clean-data-platform/test-cases.md`
- `specs/008-knowledge-collection-lifecycle/spec.md`
- `wiki/pages/roadmap.md`
- `wiki/pages/operations-and-testing.md`
