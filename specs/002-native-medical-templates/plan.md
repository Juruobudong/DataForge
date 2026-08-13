# Implementation Plan: 下线 DataFlow，改用内置医疗标准模板

**Date**: 2026-08-02 | **Spec**: [spec.md](./spec.md)
**Test Cases**: [test-cases.md](./test-cases.md)

## Summary

以 `standard_pipelines` 保留三项可执行的原生单流程；新增固定医疗模板目录将它们组合为 7 种服务端验证的选择。删除 DataFlow 引擎/Studio 路径，业务向导只提交模板 ID。一次性迁移删除所有 DataFlow 派生产物，但不触碰来源和仍被保留结果引用的内容。

## Grounding

### Existing Components to Reuse

- `src/dataforge/knowledge.py`: 三项知识生产执行器、批量任务创建与独立失败边界。
- `src/dataforge/database.py`: SQLite 标准流程、任务、知识库和 Blob 引用关系。
- `frontend/src/App.vue`: 现有任务向导、任务详情和开发工作区。

### Verified Constraints

- 内置文本块流程目前会在 DataFlow 路径存在时选择 DataFlow；必须固定为 native。
- `knowledge_job` 一次只持有一种输出 schema；组合模板必须复用批量创建独立任务。
- `.dataforge/` 是运行数据且被 Git 忽略；升级清理只应作用于其 DataFlow 专属内容。

## Technical Context

| Project | Type | Language/Framework | Testing | Responsibility in Feature |
|---------|------|--------------------|---------|---------------------------|
| DataForge | 后端/前端单仓库 | Python 3.12、FastAPI、SQLite、Vue 3 | `uv run --extra web pytest -q`、`npm run build` | 模板、清理、界面和文档 |

## Constitution Check

- PASS：使用既有原生/LLM 执行器，不引入新服务或未批准的通用编排能力。
- PASS：对用户授权的数据删除采用精确关联、事务与引用检查。

## Change Design by Project

### DataForge 后端

**Current structure**: `dataflow_studio.py`、DataFlow engine 和标准流程快照共同耦合 DataFlow；`KnowledgeService` 已可批量创建各类型任务。

**Planned changes**:

- 在知识服务中定义三项原生标准流程和固定的 7 项医疗模板目录；文本流程使用 native，图谱流程可用并沿用实体消歧默认值。
- 增加模板目录查询和模板驱动的批量任务创建；由服务端将模板映射为类型和标准流程，保留任务/知识库/重试合同。
- 移除 DataFlow Engine、Studio 服务、DataFlow 处理模块、配置、CLI 选项、路由、请求模型和 DataFlow 标准流程快照执行分支。
- 以数据库升级清理 DataFlow 专属标准流程、任务、知识库、run、资产和其无引用 Blob；清理 Studio 状态目录，保留 source/source_version、原生和 LLM 结果。

**Validation**:

- `uv run --extra web pytest -q`

## Cross-Project Contracts and Sequence

后端拥有 `GET /api/medical-templates` 与模板 ID 的批量创建合同；前端先读取目录，再向批量创建接口提交 `medical_template_id`。旧 DataFlow、Studio 与动态标准流接口将被删除并返回 404。

## Data and Migration

定义静态模板 `{id, name, knowledge_type_ids, default}`。保留 `standard_pipelines` 作为三个单流程的记录；废弃动态 DataFlow `standard_flows` 及草稿/版本记录。清理先在事务中删除 DataFlow 关联业务记录，再收集并删除数据库外无引用 Blob；来源表和 Blob 不在候选范围。清理标记使重复启动安全。

## Risks and Mitigations

| Risk | Evidence | Mitigation |
|------|----------|------------|
| 历史资产 Blob 可被保留记录共享 | `BlobStore` 内容寻址 | 删除前检查剩余 `asset_versions` 的引用。 |
| 模型故障导致组合任务部分失败 | 独立 `knowledge_job` 状态机 | 每个类型独立发布、失败和重试。 |
| 已有图谱工作区改动 | `git status` | 不重置或修改无关实现。 |

## Delivery Validation

执行模板映射、批量创建、独立故障、native 文本、清理范围和已移除端点的后端回归；构建主前端并手动确认默认模板、6 个替代项、无 Studio 导航与任务详情。

## Documentation and Knowledge Impact

- 更新项目概览、系统架构、核心工作流、后端 API、前端工作区、运行测试、路线图；删除 DataFlow 集成专题并更新索引和维护日志。
- 更新 README、全流程计划、发布记录和 Python 依赖说明。

## 2026-08-03 已批准调整

- 调试工作区保留名称“DataFlow 调试台”，仅有“知识类型”和“模板”两个只读页面；不恢复 Studio、iframe、通用编排或 DataFlow 引擎。
- 类型目录恢复多轮对话库，但该类型不属于任何模板、没有内置流程，也不能创建任务。
- 三项单类型标准流程显示为“文本知识库标准流程”“问答知识库标准流程”“知识图谱标准流程”。七个既有模板 ID 不变：全量组合显示为“医疗模板”，其余六项按目标知识类型命名。
- 新增数据库全局默认模板设置与白名单 `PUT /api/medical-templates/{id}/default` 合同。首次默认“医疗模板”；模板目录返回唯一默认项，向导读取该项而不写死模板 ID。

## 2026-08-03 已批准布局调整

- 顶部工作区永久固定为“业务工作区”和“流程开发区”。流程开发区不再作为 DataFlow 调试台的别名。
- 流程开发区固定展示知识类型、标准流程、模板、DataFlow 调试台四页，顺序不可变。知识类型与模板保持只读；标准流程只展示三个内置单类型流程，也不提供编辑、发布或验证。
- DataFlow 调试台仅为最后一个待开发占位页；不恢复 DataFlow 引擎、Studio、iframe、执行接口或第三方 WebUI。
- 将这一名称与顺序写入根目录 `AGENTS.md`，并以主前端静态断言、后端回归和 Vite 构建验证。

## 2026-08-03 已批准文档库调整

- 在 SQLite 中新增 `document_libraries` 与有序 `document_library_members`，成员关联具体 `source_version`；名称唯一、成员非空且同一逻辑来源只允许一个版本。
- 新增文档库 CRUD 接口；批量任务合同改为只接收 `document_library_id`，服务端展开成员并复用既有 `knowledge_job.source_version_ids` 快照。
- 文档管理页提供“源文档 / 文档库”二级视图：从源文档勾选（含全选）创建文档库，编辑时可显式添加、移除或更新成员版本；处理向导仅选择文档库。
- 在来源批量删除的事务检查中加入文档库成员关系，返回关联文档库名称；删除文档库不影响既有任务和知识结果。

## 2026-08-03 图谱输出容错调整

- 图谱抽取在一次模型修复后仅对仍可解析的 JSON 运行保守清洗：不猜测实体类型，删除不合规项目及其依赖，只有保留至少一个严格校验的三元组时才发布。
- 图谱缓存从历史三元组列表兼容升级为可选告警结果包；任务 `validation` 增加可选 `graph_model_cleanup`，前端展示过滤数量、最多 20 个名称和原因。
- JSON/长度失败保持原有网关诊断；结构约束失败改为准确的 schema 提示。无需数据库迁移。
