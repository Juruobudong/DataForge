# Tasks: 图谱 WebUI 与实体同义消除

**Input**: `spec.md`、`plan.md` 和 `test-cases.md`

## Phase 1: 图谱画布

- [x] T001 [DataForge/frontend] [US1] 新增 `frontend/src/components/KnowledgeGraphViewer.vue`，渲染 SVG 节点、有向关系、筛选、缩放/重置、图例、选择态和可访问关系列表 — Depends on: None — Implementation: DONE — Validation: PASSED — Validate: 主前端 Vite 构建成功且组件显示当前图谱数据。

## Phase 2: 知识库详情集成

- [x] T002 [DataForge/frontend] [US1/US2/US3] 修改 `frontend/src/App.vue` 和 `frontend/src/styles.css`，仅对图谱型知识库优先展示组件并复用既有邻域、证据、溯源状态与 API — Depends on: T001 — Implementation: DONE — Validation: PASSED — Validate: 图谱画布先于记录区，普通知识库保持记录优先，选择和筛选不会保留陈旧数据。

## Phase 3: Wiki 同步

- [x] T003 [DataForge/wiki] 登记 LightRAG v1.5.5 `GraphViewer` 交互参考，并更新前端工作区说明与维护日志 — Depends on: T002 — Implementation: DONE — Validation: PASSED — Validate: Wiki 链接、实现陈述和来源边界一致。

## Phase 4: 验证

- [x] T004 [DataForge/workspace] 执行图谱 API 回归、主前端构建和浏览器验收 — Depends on: T001, T002, T003 — Implementation: N/A — Validation: PASSED — Validate: TC-001 至 TC-006 的自动和浏览器证据均通过；窄屏由组件断点规则与构建产物静态核对覆盖。

## Phase 5: 实体同义消除与图谱投影

- [x] T005 [DataForge/backend] [US4] 为任务 API、`knowledge_jobs` 和图谱投影增加图谱专属的 `entity_resolution_enabled`、别名投影及节点度量 — Depends on: T002 — Implementation: DONE — Validation: PASSED — Validate: 单任务/批量默认兼容、别名/度量合同和迁移由 `tests/test_knowledge_flow.py`、`tests/test_web.py` 覆盖。
- [x] T006 [DataForge/backend] [US4] 在图谱抽取完成后实施受约束的模型同义归并及可发布的降级处理 — Depends on: T005 — Implementation: DONE — Validation: PASSED — Validate: 同义节点合并、原始记录/边证据保留、失败 fallback 由自动化回归覆盖。

## Phase 6: 画布强化、Wiki 与验证

- [x] T007 [DataForge/frontend/wiki] [US1/US3/US4] 强化向导配置、左侧固定列表、80 条度量优先同心画布与领域/API/工作流说明 — Depends on: T005, T006 — Implementation: DONE — Validation: PASSED — Validate: 前端构建通过，Wiki 页面和维护日志已同步。
- [x] T008 [DataForge/workspace] 执行完整实体消歧与图谱 UI 回归验收 — Depends on: T005, T006, T007 — Implementation: N/A — Validation: PASSED — Validate: `46 passed, 1 skipped`；浏览器已核对向导默认/取消、非图谱兼容、节点邻域、边证据溯源、缩放重置和 390px 单列布局。

## Phase 7: 圆内实体标签

- [x] T009 [DataForge/frontend/wiki] 将实体名改为圆内两行标签，并扩大节点/同心布局以保留关系和交互可读性 — Depends on: T008 — Implementation: DONE — Validation: PASSED — Validate: Vite 构建通过；浏览器核对 92 个节点都有 1–2 行圆内标签、无节点重叠、80 条边端点在圆外，并复验节点、边、缩放和窄屏交互。

## Phase 8: 失败任务重试与图谱 JSON 可靠性

- [x] T010 [DataForge/backend/frontend/wiki] 为失败且未发布的知识任务提供原子重试和执行次数，提升图谱 JSON 输出可靠性并持久化脱敏失败诊断 — Depends on: T008 — Implementation: DONE — Validation: PASSED — Validate: `tests/test_knowledge_flow.py` 与 `tests/test_web.py` 覆盖重试互斥、成功发布、终态拒绝、JSON 包装/截断/修复、诊断字段和 API 202；主前端构建通过。

## Phase 9: 任务历史批量删除

- [x] T011 [DataForge/backend/frontend/wiki] 为失败或已停止且未发布的处理任务增加全有或全无的批量删除接口、目录勾选交互、回归测试与 Wiki 同步 — Depends on: T010 — Implementation: DONE — Validation: PASSED — Validate: `tests/test_web.py` 覆盖可删除状态、重复 ID、已发布任务拒绝和原子性；主前端 Vite 构建通过。

## Status Summary

| Metric | Count |
|--------|-------|
| Implementation done | 9 |
| Validation passed | 11 |
| Fully completed | 11 |
| Blocked | 0 |

## Dependency Summary

`T001 → T002 → T003 → T004`；`T005 → T006 → T007 → T008 → T009 → T010 → T011`。实体消歧的存储和 API 扩展先于画布度量展示，圆内标签在画布强化完成后更新；重试、模型诊断和任务历史删除复用既有任务状态机与知识库删除边界，Wiki 随实际行为同步。

## Parallel Opportunities

无。消歧投影必须在度量画布展示前完成；最终文档和验收依赖两者。
