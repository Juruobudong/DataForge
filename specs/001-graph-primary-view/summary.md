# Feature Summary: 图谱 WebUI 与实体同义消除

**Stage**: DELIVERY
**Artifact Language**: zh-CN
**Artifact Readiness**: READY
**Delivery Readiness**: READY
**Human Gate**: APPROVED
**Updated**: 2026-08-02

## What Is Changing

图谱型知识库在详情摘要后首先显示 DataForge 原生图谱 WebUI。画布在当前 200 条筛选关系中优先绘制连接度更高的 80 条，使用度量加权同心布局和大小可变节点；知识库列表固定在左侧，详情使用其余宽度。新建图谱任务在业务向导默认启用实体（同义）消除，模型只归并输入中明确同义的实体，原始三元组/知识记录和全部证据保持不变；失败时按原规范化规则发布并写入 fallback。普通知识库详情页不变。失败且尚未发布的知识任务现在可从详情重试；图谱 JSON 输出加入更高输出上限、包装兼容解析和不含原文的失败诊断。处理任务目录还可批量永久删除失败或已停止、且尚未生成知识库的任务历史；已发布任务继续随知识库删除，避免遗留记录或图谱投影。

## What Is Not Changing

- 不运行、嵌入或依赖 LightRAG 服务和其 React/Sigma 源码。
- 不修改原始知识记录、来源证据、历史图谱或提供人工别名维护/图谱编辑能力。

## Decisions and Assumptions

| Item | Type | Owner | Status | Recommendation/Decision |
|------|------|-------|--------|-------------------------|
| 在 DataForge 中原生实现而非嵌入 LightRAG | ASSUMPTION | AGENT | CONFIRMED | 用户要求“参考”LightRAG；复用已有 API，以同类探索交互满足首屏优先展示。 |
| 图谱画布规模 | ASSUMPTION | AGENT | CONFIRMED | 仅画出当前 API 返回的受限筛选结果，不在浏览器加载全量图或持久化布局。 |
| 同义消歧范围 | USER DECISION | USER | CONFIRMED | 仅对新建且勾选的图谱任务自动执行；不迁移历史图谱，别名仅作为可审计投影保存。 |
| 消歧失败策略 | USER DECISION | USER | CONFIRMED | 模型调用或输出无效时继续完成图谱投影，并在验证信息记录 fallback。 |

## Main Risks

- 密集图可能造成标签、边和节点重叠；画布将限制标签密度并保留关系列表、筛选及缩放作为可访问降级。
- 单文件 Vue 页面不含图形库；用 SVG 和既有 API 实现，以避免新增打包、运行和安全依赖。

## Implementation and Validation

- **Implementation**: 已新增受约束的实体归并器、任务持久化开关、`graph_node_aliases` 投影和节点 `degree`/`aliases` 扩展；并将 80 条连接度优先的 SVG 画布、向导设置和左列/右侧全宽布局集成到图谱型知识库。
- **Implementation**: 实体名称现为圆内居中两行标签；节点半径、同心布局与 SVG 视区扩大，长名称按词边界或字符截断并保持全名可访问。
- **Implementation**: `knowledge_jobs` 保存实际执行次数；重试接口使用条件更新清除单次运行状态并复用后台执行。图谱提取继续使用 JSON 模式，输出上限为 4096 token，兼容 BOM、思考标签和围栏；原始与修复输出均无效时仅保存长度、完成原因、解析位置、内容哈希和截断信号。
- **Implementation**: 新增 `DELETE /api/knowledge-jobs/batch`，只接受失败或已停止且未生成知识库的任务；服务在 SQLite 写事务中先校验整批再删除。任务目录提供可删除项勾选、全选、确认、加载和错误反馈，已发布任务明确引导至知识库删除路径。
- **Validation**: 在 `sun` 环境下，`uv run --extra dataflow --extra web --extra studio pytest -q` 已通过（63 passed、1 skipped）；批量任务删除的 `tests/test_web.py` 回归共 27 项通过，主前端 Vite 构建通过。浏览器验收覆盖图谱首屏、非图谱兼容、向导默认/取消、圆内标签、节点邻域、边证据至记录溯源、缩放重置和 390px 单列。
- **Review**: 已完成实施自检；HTTP API 仅进行向后兼容扩展，SQLite 仅对新任务投影保存开关/别名。

## Delivery Counts

- Implementation done: 8
- Validation passed: 11
- Fully completed: 11
- Blocked: 0

## Changes Since Last Human Approval

- 首次规格：无。

## Next Action

交付包含任务重试与图谱 JSON 诊断的强化图谱工作流。
