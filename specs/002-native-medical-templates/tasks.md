# Tasks: 下线 DataFlow，改用内置医疗标准模板

**Input**: `spec.md`、`plan.md` 和 `test-cases.md`

## Phase 1: 后端模板合同

- [x] T001 [DataForge/backend] [US1] 在 `src/dataforge/knowledge.py`、`models.py` 与 `web.py` 定义 7 个固定医疗模板、查询接口及模板驱动的批量任务创建，并将文本流程固定为 native — Depends on: None — Implementation: DONE — Validation: PASSED — Validate: 模板/任务 API 回归覆盖 7 个映射、默认和未知模板。

## Phase 2: 移除 DataFlow 与清理历史

- [x] T002 [DataForge/backend] [US2] 移除 `config.py`、`application.py`、`cli.py`、`processing/`、`dataflow_studio.py`、`knowledge.py` 与 `web.py` 中的 DataFlow/Studio 路径，并删除 DataFlow 专用源码 — Depends on: T001 — Implementation: DONE — Validation: PASSED — Validate: 无 DataFlow import/路由，native 文本任务通过。
- [x] T003 [DataForge/backend] [US2] 在 `database.py`、`blobs.py` 与应用启动中实现可重复的 DataFlow 历史清理，删除专用业务记录、运行目录、Studio 状态与无引用 Blob，保留来源和非 DataFlow 结果 — Depends on: T002 — Implementation: DONE — Validation: PASSED — Validate: 清理夹具回归验证精确删除范围。
- [ ] T004 [DataForge/workspace] 删除 `third_party/dataflow_webui/`，并更新 `pyproject.toml` 与 `uv.lock` 去除 DataFlow/Studio 依赖 — Depends on: T002 — Implementation: BLOCKED — Validation: PARTIAL — Validate: `uv run --extra web` 无 DataFlow extra 仍可导入服务。阻塞：目录内有 7 处未提交本地改动及一个未跟踪锁文件，等待明确授权后才可永久丢弃。

## Phase 3: 业务界面

- [x] T005 [DataForge/frontend] [US1/US2] 在 `frontend/src/App.vue`、`api.js` 与 `styles.css` 以模板选择替换流程/类型/Studio 编排交互，保留独立任务详情与重试 — Depends on: T001, T002 — Implementation: DONE — Validation: PASSED — Validate: Vite 构建，默认模板和六个替代项可见且没有 Studio 入口。

## Phase 4: 回归与文档

- [x] T006 [DataForge/tests] 为模板目录、批量创建、独立故障、native 文本、清理范围和删除路由更新 `tests/` — Depends on: T001-T004 — Implementation: DONE — Validation: PASSED — Validate: `pytest -q` 通过。
- [x] T007 [DataForge/wiki] 同步 `wiki/`、README、`plan.md` 和发布记录，删除 DataFlow 集成专题并追加维护日志 — Depends on: T002, T004, T005 — Implementation: DONE — Validation: PASSED — Validate: Wiki 链接和实施陈述核对通过。
- [x] T008 [DataForge/workspace] 执行后端回归、主前端构建和静态路由/API 检查 — Depends on: T003-T007 — Implementation: DONE — Validation: PASSED — Validate: TC-001 至 TC-007 已通过；`third_party/dataflow_webui/` 的物理删除等待授权。

## Phase 5: 调试台目录与默认模板调整

- [x] T009 [DataForge/backend/frontend] [US1/US2] 保留“DataFlow 调试台”名称、恢复多轮对话库目录、更新 7 个模板及三项标准流程显示名，并提供持久默认模板切换 — Depends on: T001, T005 — Implementation: DONE — Validation: PASSED — Validate: TC-008、TC-009 和 Vite 构建。
- [x] T010 [DataForge/wiki/tests] [US1/US2] 同步默认模板、只读目录和多轮对话边界的测试与文档 — Depends on: T009 — Implementation: DONE — Validation: PASSED — Validate: 后端回归、Wiki 链接和文档陈述核对。

## Phase 6: 固定流程开发区布局

- [x] T011 [DataForge/frontend] [US2] 恢复“业务工作区 / 流程开发区”顶部布局，并固定流程开发区为知识类型、标准流程、模板、DataFlow 调试台四页；标准流程只读，调试台标为待开发 — Depends on: T009 — Implementation: DONE — Validation: PASSED — Validate: 主前端静态断言与 Vite 构建。
- [x] T012 [DataForge/wiki/tests] [US2] 在根目录 `AGENTS.md`、Wiki、README、发布记录、项目计划与规格记录中固化名称、顺序和产品边界 — Depends on: T011 — Implementation: DONE — Validation: PASSED — Validate: Wiki 链接、`pytest -q` 和静态路由检查。

## Phase 7: 可复用文档库

- [x] T013 [DataForge/backend] [US3] 在 `database.py`、`knowledge.py` 与 `web.py` 实现固定版本文档库、CRUD、任务入口解析与来源删除保护 — Depends on: T001, T003 — Implementation: DONE — Validation: PASSED — Validate: TC-011 API 回归覆盖版本固定、任务快照、输入拒绝和删除保护。
- [x] T014 [DataForge/frontend] [US3] 在 `frontend/src/App.vue`、`api.js` 与 `styles.css` 提供源文档/文档库二级视图、部分/全选建库、成员编辑与仅文档库任务向导 — Depends on: T013 — Implementation: DONE — Validation: PASSED — Validate: Vite 生产构建通过。
- [x] T015 [DataForge/tests/wiki] [US3] 同步规格、Wiki、测试用例和维护日志，并执行全量后端回归 — Depends on: T013, T014 — Implementation: DONE — Validation: PASSED — Validate: Web、医疗模板、知识流、优先来源和端到端回归均通过；Wiki 链接和陈述已核对。

## Phase 8: 图谱输出容错

- [x] T016 [DataForge/backend/frontend/tests/wiki] [US1] 对图谱模型 schema 违规执行保守过滤、缓存告警复用和任务详情提示；JSON/长度失败保持可重试且无部分发布 — Depends on: T001, T005 — Implementation: DONE — Validation: PASSED — Validate: `tests/test_knowledge_flow.py` 30 项、`tests/test_web.py` 24 项及隔离 Vite 生产构建通过。

## Status Summary

| Metric | Count |
|--------|-------|
| Implementation done | 15 |
| Validation passed | 15 |
| Fully completed | 15 |
| Blocked | 1 |

## Dependency Summary

`T001 → T002 → T003/T004 → T005/T006 → T007 → T008`。模板合同先于接口下线和前端消费；清理和依赖删除后再验证完整安装。

## Parallel Opportunities

T003 与 T004 在 T002 后可并行；T005 可与 T006 并行，但最终文档与验证依赖二者完成。
