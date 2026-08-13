# 功能规格：V7 受控知识算子目录

**功能目录**：`specs/006-governed-operator-catalog`  
**创建日期**：2026-08-11  
**状态**：APPROVED  
**工件语言**：zh-CN  
**需求输入**：[intake.md](./intake.md)

## 需求来源

| 来源 | 贡献 | 要求/场景 |
|------|------|-----------|
| SRC-001 | 架构、数据重建、部署和交付边界 | FR-001、FR-004、FR-007、FR-008 |
| SRC-002 | Catalog、Adapter、Prompt、Parser、质量与子图设计 | FR-002、FR-003、FR-005、FR-006 |
| SRC-003 | 现有实现与安全边界 | 全部迁移与回归场景 |

## 已验证的项目上下文

- `src/dataforge/v7/store.py` 固定三类内置 `V7_TYPE_META`、类型发布和受控模板校验。
- `src/dataforge/v7/runner.py` 自行解析文件、固定切片，并仅生成文/问/图候选。
- `src/dataforge/v7/vector.py` 已具备只操作四个 V7 Collection 与 `kl_` Partition 的安全边界。
- `frontend/src/layouts/WorkspaceLayout.vue` 固定开发区四页导航；`frontend` 已安装 Vue Flow。

## 用户场景

### 用户故事 1：受控算子与知识类型配置（P1）

流程开发人员能查看受限 Catalog，创建扩展知识类型、Prompt、质量规则、子图和流程草稿，并且只能发布通过契约校验的修订。

**独立验证**：创建扩展类型与包含已发布 P0 节点的流程，编译并发布成功；禁用算子、未发布 Prompt 和类型不匹配边被拒绝。

### 用户故事 2：正式知识生产（P1）

业务人员从已发布模板创建任务，按 Sink 绑定知识库；Runner 只运行 ExecutionSnapshot，并写入可追溯的正式知识。

**独立验证**：从 TXT/DOCX/CSV/PDF 样例生成 text、qa、graph 及已发布扩展类型，获得 Evidence、Diff 和节点运行记录。

### 用户故事 3：可视化开发与诊断（P2）

流程开发人员能在既有四页导航中编辑受控 Canvas、浏览 Catalog/子图和定位 Flow Run 节点失败。

**独立验证**：前端建立兼容连线、发布后查看冻结快照、运行状态和 Artifact 血缘。

## 范围

### 包含

- 版本化知识类型、Operator Catalog、Prompt、Quality Profile、子图、DAG Flow、执行快照与运行血缘。
- P0 DataFlow Adapter、独立 Runner、DocumentIR 路由、内置 text/qa/graph 流程、扩展类型 LLM 生成与一次修复 Gate。
- 仅 V7 受管资源的显式重建命令、API、Vue Flow Canvas、调试台、回归和 Wiki。

### 不包含

- V2 或旧资源迁移、读取、删除；`qa_agent` 变更；DataFlow WebUI；任意 Python/Shell 节点。
- KCenterGreedy、MultiHop Batch、Reference Remover、训练/代码/Text2SQL/Agentic-RAG 算子；普通 PII 节点与多跳 QA 正式生产。

## 功能要求

- **FR-001**：系统必须将知识类型、算子、Prompt、质量规则、子图和 Flow 定义为可修订、可发布资产；生产任务只能绑定冻结快照。— SRC-001
- **FR-002**：系统必须只对外开放 P0 Catalog 逻辑节点，并以 Adapter 隐藏 DataFlow 类名、MinerU 部署变体与 Batch 变体。— SRC-002
- **FR-003**：系统必须把 PDF、DOC/DOCX、MD/TXT、CSV 路由至统一 `DocumentIR`，并使正式 `SourceChunk` 溯源不受运行时二次切分影响。— SRC-002
- **FR-004**：系统必须验证 DAG、端口 Artifact Type、版本引用、Prompt/Quality 发布态、知识类型 Schema、来源策略和 allowlist；禁止环、递归、未注册实现和任意代码。— SRC-001
- **FR-005**：`KnowledgeSink` 必须是正式知识唯一入口，先执行来源、Schema、canonical、质量与 Diff 校验；失败 Sink 不写入，独立 Sink 可隔离完成。— SRC-001、SRC-002
- **FR-006**：系统必须支持管理员发布扩展知识类型、Prompt 和 Index Profile；未知类型仅在 LLM 一次修复后通过 Schema、来源、质量和 Diff Gate 才能写入。— SRC-001
- **FR-007**：系统必须提供明确确认的 V7 重建命令，仅清理数据库登记的 V7 对象和允许 Partition，绝不删除 Collection 或旧资源。— SRC-001
- **FR-008**：系统必须保持开发区四页导航，在模板页容纳 Catalog、子图和 Canvas，并在调试台展示快照、节点运行、质量与血缘。— SRC-001
- **FR-009**：文档库文件列表必须支持当前页全选及选中文件处理；仅对所选、仍待更新的当前版本按全部有效模板创建任务，保留整库待更新处理入口，并拒绝非本库文件。— SRC-004
- **FR-010**：当 Milvus 由同一 Docker 主机上不受 DataForge 管理的容器提供时，API 与 Worker 必须经专用外部 Docker 网络以 `dataforge-milvus:19530` 直连；仅允许该网络访问 Milvus RPC 和当前 Embedding HTTPS 端点，Milvus 容器重建后必须可由部署自动化恢复接入。— SRC-005
- **FR-011**：顶层知识族保持 `text / qa / graph`，Graph 必须支持 `triple / semantic` 两种冻结模式并使用两个独立受管 Collection；旧 `dataforge_graph_knowledge` 不迁移、不删除且只供旧库使用。— SRC-006
- **FR-012**：Collection 必须按版本化 Storage Contract 的规格哈希复用；供应过程必须可重试、校验归属并且不得自动删除 Collection。— SRC-006
- **FR-013**：模板页必须提供白名单、强类型、无环、可分支合流的受控可拖拽 DAG；禁止任意代码、Shell 与运行时改图。— SRC-006

## 关键实体

- **KnowledgeTypeRevision**：定义正式知识 Schema、canonical/identity 字段、来源策略、质量 Profile 和 Index binding。
- **OperatorVersion**：定义逻辑节点端口、参数 Schema、Adapter、风险等级与固定 Runtime 依赖。
- **PromptTemplateRevision / QualityProfileRevision**：生产节点唯一可引用的已发布 Prompt 和质量规则。
- **FlowSubgraphRevision / FlowTemplateRevision**：设计态 DAG；编译后不直接运行。
- **FlowExecutionSnapshot**：展开子图并冻结所有依赖的执行定义。
- **Artifact / FlowNodeRun**：执行期产物和血缘；不替代正式 Evidence。

## 边界与失败行为

- 未配置 OCR、LLM、Embedding 或 Milvus 时，相关任务明确失败或保持待处理，不伪造成功。
- `review` 质量结果阻断对应 Sink，不创建候选确认批次。
- 一项候选不满足 Sink 契约时，该 Sink 事务回滚；不影响已独立通过的 Sink。
- 重建命令必须先列出精确目标，并要求 `--confirm=REBUILD-V7`。

## 成功标准

- **SC-001**：P0 节点、禁用节点和 Prompt 发布态均被 API/编译器自动验证。
- **SC-002**：三类内置流程和已发布扩展类型均从快照执行并保存可查询的 Evidence、Diff、节点运行和血缘。
- **SC-003**：测试证明外部 Profile 只访问既有 Collection；受管 Profile 仅创建归属明确且规格匹配的 Collection，所有路径都禁止自动删除 Collection。
- **SC-004**：前端构建通过，开发区保持固定四页并完成 Canvas/Catalog/诊断体验。
- **SC-005**：当前页文件选择不会跨筛选、目录或分页保留；选中处理不会重复调度已处理、处理中或已删除版本。

## 依赖

- Runner 镜像需要固定 `open-dataflow==1.0.10`；真实 MinerU、LLM、Milvus 和 Embedding 验收需要部署凭据。
