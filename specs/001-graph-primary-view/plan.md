# Implementation Plan: 图谱 WebUI 与实体同义消除

**Date**: 2026-08-01 | **Spec**: [spec.md](./spec.md) | **Test Cases**: [test-cases.md](./test-cases.md)

## Summary

图谱型知识库在详情摘要后优先显示 DataForge 原生 SVG 画布；画布从当前至多 200 条筛选关系中选择连接度优先的 80 条，使用同心布局把高连接节点聚向中心并放大。新建图谱任务在业务向导默认开启实体（同义）消除：受约束的模型只可把已提取实体归并到输入已有名称，图谱投影记录别名，原始三元组和证据保持不变。模型失败时保留既有规范化图谱并记录降级。普通知识库继续保持记录优先。

## Grounding

### Existing Components to Reuse

- `frontend/src/App.vue`: 已持有知识库详情、图谱筛选、邻域、证据与溯源状态和 API 调用。
- `frontend/src/api.js`: 已封装所需的四个只读图谱/溯源端点。
- `frontend/src/styles.css`: 已承载主前端知识库详情的布局和响应式样式。

### Verified Constraints

- 图谱投影可由原始记录派生而不改写记录；新增别名投影不会要求历史图谱迁移或重建。
- API 可以进行向后兼容的可选请求字段和响应字段扩展；旧调用维持 `entity_resolution_enabled=false`。
- `frontend/` 未配置图形库或 JavaScript 单测框架；使用 Vue/SVG，避免增加依赖。
- `sun` Conda 环境的 Python 3.12 可用；当前 PATH 无 Node/npm，构建使用 Codex 桌面提供的 Node 运行时。

## Change Design

### frontend

**Current structure**: `frontend/src/App.vue` 是主页面；`styles.css` 提供全局组件样式；现有图谱为详情底部的线性关系列表。

**Planned changes**:

- 新增 `frontend/src/components/KnowledgeGraphViewer.vue`：以确定性 SVG 布局绘制当前筛选结果的节点和有向边，提供关键词/关系筛选表单、缩放/重置、图例、画布计数及无结果状态。组件向父页面发出筛选、节点选择和边选择事件，并保留可聚焦的关系列表作为辅助交互路径。
- 调整 `frontend/src/App.vue`：图谱投影存在时，将查看组件放在详情摘要之后、记录区域之前；选择节点继续调用邻域端点，选择边继续调用证据端点，切换知识库或筛选时清空陈旧选择。记录搜索、分页和溯源不改变；无图谱投影时不渲染组件。
- 扩展 `frontend/src/styles.css`：为画布、工具栏、选中态、信息区和窄屏布局增加最小样式，不改变其他页面。
- 在处理任务向导中仅当图谱类型被选择时显示默认勾选的消歧复选框，并在确认和任务详情展示配置/结果。
- 实体标签改为圆内居中两行文本；按当前节点半径确定每行容量，长英文优先按词边界换行，超长追加省略号。节点基础半径、同心环间距和 SVG 视区同步扩大，边端点继续以半径计算。

### backend

- `src/dataforge/web.py` 和 `src/dataforge/knowledge.py` 接收、持久化和执行图谱专属的消歧开关；批量创建只将其施用于图谱工作流。
- `src/dataforge/graph.py` 提供有严格输入/输出约束的模型实体归并器；`src/dataforge/knowledge.py` 在抽取完成、投影创建前调用它，并把失败写入任务验证信息。
- `src/dataforge/database.py` 为 `knowledge_jobs` 增加开关，为 `graph_node_aliases` 增加只读投影，按别名映射归并节点/边，并在图谱/邻域节点响应添加 `degree`、`aliases`。

### Cross-Project Contracts and Sequence

创建任务端点接受可选 `entity_resolution_enabled`；图谱和邻域节点返回附加 `degree`、`aliases`。前端继续使用既有图谱、邻域、边证据和记录溯源端点，证据/溯源合同不变。

### Data and Migration

新增 `knowledge_jobs.entity_resolution_enabled`（默认 `0`）和 `graph_node_aliases`；SQLite 初始化对已有 `knowledge_jobs` 补列，但不对历史图谱创建别名投影。SVG 坐标、缩放和选择态仍仅保留在当前浏览器内存。

## Risks and Mitigations

| Risk | Evidence | Mitigation |
|------|----------|------------|
| 密集图标签和边重叠 | 当前 API 至多返回 200 条关系 | 默认仅渲染连接度优先的 80 条；同心布局、缩放、筛选、图例和可访问关系列表辅助探索。 |
| 模型错误地合并关联概念 | 语义消歧为模型调用 | 提示限制为输入内的明确同义实体；解析严格验证，任何无效结果回退且不阻断发布。 |
| 父页面已有未提交改动 | `git status --short` | 仅以增量方式修改图谱区和新增组件，不回退或格式化无关代码。 |
| PATH 缺少 Node/npm | 执行预检 | 使用 Codex 桌面 Node 路径构建，不下载依赖。 |

## Delivery Validation

在 `sun` 环境执行 `tests/test_knowledge_flow.py tests/test_web.py`；运行主前端构建；通过浏览器核对消歧向导、图谱和非图谱详情、节点邻域、边证据溯源、缩放重置及窄屏布局。

## Documentation and Knowledge Impact

- 更新领域模型、后端 API、核心工作流、前端工作区、测试说明和 `wiki/log.md`；已有 LightRAG 参考登记继续明确为仅交互参考。
