# Test Cases: 图谱 WebUI 与实体同义消除

**Spec**: [spec.md](./spec.md)
**Plan**: [plan.md](./plan.md)
**Status**: READY

## Coverage Matrix

| Test ID | Requirement/Source | Level | Scenario | Expected Result | Automation | Status |
|---------|--------------------|-------|----------|-----------------|------------|--------|
| TC-001 | FR-001 / SRC-001 | build | 构建新增 Vue 图谱组件 | Vite 成功解析组件、模板和样式 | AUTO | PASSED — Vite 7.3.6 |
| TC-002 | FR-001、FR-004 / SRC-002 | API contract | 运行既有图谱 Web API 回归 | 筛选、邻域和边证据端点保持现有响应合同 | AUTO | PASSED — 20 passed、1 skipped |
| TC-003 | FR-001 至 FR-005 / SRC-001 | browser | 打开图谱型知识库 | 摘要后先出现画布，记录区位于其后 | MANUAL | PASSED |
| TC-004 | FR-003 / SRC-001 | browser | 筛选、缩放、重置和无结果 | 仅显示当前筛选图，控制不写服务端，空状态可清除 | MANUAL | PASSED |
| TC-005 | FR-004 / SRC-002 | browser | 选择节点、边和证据 | 显示一跳邻域、证据，并可进入既有溯源 | MANUAL | PASSED |
| TC-006 | FR-001、FR-005 / SRC-002 | browser | 打开无图谱知识库和窄屏 | 记录优先无回归，图谱工具栏在窄屏可用 | MANUAL | PASSED — 浏览器核对非图谱记录区及 390px 单列，无水平溢出 |
| TC-007 | FR-006、FR-007 / SRC-004 | unit/API | 图谱任务消歧开关与批量兼容 | 旧请求默认关闭；图谱向导默认传 true；混合批量不影响非图谱任务 | AUTO | PASSED — `tests/test_knowledge_flow.py tests/test_web.py` |
| TC-008 | FR-008、FR-009 / SRC-004 | integration | 同义实体成功归并和模型失败降级 | 节点/边别名归并，原始记录和证据不变；失败仍发布并记录 fallback | AUTO | PASSED — `tests/test_knowledge_flow.py` |
| TC-009 | FR-010 / SRC-004 | API contract | 节点别名与度量扩展 | 图谱/邻域节点携带 `degree`、`aliases`，筛选、邻域、边证据合同保持可用 | AUTO | PASSED — `tests/test_knowledge_flow.py tests/test_web.py` |
| TC-010 | FR-006、FR-010、FR-011 / SRC-004 | browser | 向导与强化画布 | 消歧默认勾选且可取消/隐藏；80 条高连接优先节点更大更居中；节点/边/缩放可用 | MANUAL | PASSED — 浏览器核对 |
| TC-011 | FR-012 / SRC-005 | browser/build | 圆内实体标签 | 短中英文、长中英文在圆内最多两行；超长省略；节点无重叠且边端点在圆外 | MANUAL/AUTO | PASSED — Vite 构建和浏览器 DOM/几何核对 |
| TC-012 | 用户实施计划 / SRC-006 | unit/API/build | 失败任务重试与图谱 JSON 修复 | 仅失败未发布任务返回 202 并只调度一次；执行次数递增；BOM、思考标签、围栏、空/截断输出与一次修复均受控；双失败任务仅保存脱敏诊断；前端可确认、加载、失败展示重试 | AUTO | PASSED — `tests/test_knowledge_flow.py tests/test_web.py`、主前端 Vite 构建 |
| TC-013 | 用户需求 / SRC-007 | API/build | 批量删除任务历史 | 仅失败或已停止且未生成知识库的任务可整批删除；重复 ID 合并，缺失或任一不可删除项均不产生部分删除；任务目录只允许选择可删除项 | AUTO | PASSED — `tests/test_web.py`、完整 pytest、主前端 Vite 构建 |

## Functional and Acceptance Cases

- 图谱型知识库在详情摘要后显示画布和图例；当前结果数量与 API 返回边数一致，且不超过默认 200 条。
- 记录查询、分页和逐条溯源仍可在图谱区域后使用。
- 新建图谱任务的原始 `knowledge_records` 不被同义消歧改写；仅图谱节点、边和 `graph_node_aliases` 是派生投影。

## Failure and Recovery Cases

- 图谱筛选结果为空时，组件清除上次画布数据，显示空状态和清除筛选按钮。
- 图谱、邻域或证据请求失败时，沿用页面 toast，记录浏览保持可用。
- 失败任务重复点击重试时，只有第一个原子状态转换成功；取消、完成或已有知识库的任务保持拒绝，且不会覆盖已发布结果。
- 图谱原始/修复模型输出均无效时，任务返回可操作的网关检查提示；任务诊断不含模型原文、来源片段或凭据。

## Security and Permission Cases

- 任务创建新增可选开关，但不新增权限、人工别名维护或额外源数据展示；图谱浏览继续只读。

## Boundary, Concurrency, and Compatibility Cases

- 切换知识库或重新筛选时清空前一个图谱的邻域和证据；同一节点对的多条边都可单独选择。
- 没有 `graph` 投影的知识库不创建图谱请求或控件。

## Cross-Project and End-to-End Cases

- `tests/test_knowledge_flow.py` 与 `tests/test_web.py` 验证消歧投影、任务开关、图谱/邻域/证据 HTTP 合同，以及重试互斥、图谱 JSON 失败诊断和任务详情扩展字段。

## Manual Product/Design Checks

- 检查节点、箭头、关系图例和选中态可辨识；键盘可到达筛选、画布控制和辅助关系列表。
- 检查 1280px 宽度和窄屏宽度下，工具栏、信息区与记录区不溢出。

## Environment and Test Data

- `conda activate sun`、既有 API 测试依赖、主前端 `node_modules`、Codex 提供的 Node 22.16.0、带图谱投影的本地演示数据和浏览器。

## Exit Criteria

- 主前端构建通过；消歧及图谱 API 回归通过；浏览器验收 TC-003 至 TC-006、TC-010 通过；Wiki 与外部资料登记完成。
