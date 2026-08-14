# 任务：V7 受控知识算子目录

**输入**：[spec.md](./spec.md)、[plan.md](./plan.md)、[test-cases.md](./test-cases.md)

## 阶段 1：基础契约

- [X] T001 [DataForge] [US1] 在 `src/dataforge/v7/models.py`、新领域模块和 Alembic 中建立版本化治理模型、任务 Snapshot/Sink 绑定与 Index binding。— Depends on: None — Implementation: DONE — Validation: PASSED — Validate: `pytest -q tests/test_v7_platform.py`
- [X] T002 [DataForge] [US1] 在 `src/dataforge/v7/store.py` 和领域服务中实现 Catalog、Prompt、Quality、知识类型、子图、Flow 修订、种子和受控 V7 重建。— Depends on: T001 — Implementation: DONE — Validation: PASSED — Validate: Catalog/重建测试。
- [X] T003 [DataForge] [US1] 在 `src/dataforge/v7/domain/flow/` 实现 Artifact DSL、校验、子图展开和 ExecutionSnapshot 编译器。— Depends on: T001,T002 — Implementation: DONE — Validation: PASSED — Validate: DAG 拒绝/快照测试。

## 阶段 2：运行与正式知识

- [X] T004 [DataForge] [US2] 在 `src/dataforge/v7/integrations/dataflow/` 和 `runner.py` 实现 P0 Adapter、DocumentIR 路由、SourceChunk、Quality Gate、Sink 与 Flow Run 血缘。— Depends on: T003 — Implementation: DONE — Validation: PASSED — Validate: text/qa/graph Runner 测试。
- [X] T005 [DataForge] [US2] 在 Runner Docker/依赖配置中隔离 `open-dataflow==1.0.10` 和 Schema 依赖，保持 API/Worker 轻量。— Depends on: T004 — Implementation: DONE — Validation: PASSED — Validate: 锁文件与 Docker 静态检查。
- [X] T006 [DataForge] [US2] 收敛 text/qa/graph 三类内置类型，实现扩展类型的结构化生成器、一次 Schema 修复和动态 Index Profile。— Depends on: T004,T005 — Implementation: DONE — Validation: PASSED — Validate: 三类种子、扩展发布与 Runner Gate 测试。

## 阶段 3：API 与前端

- [X] T007 [DataForge] [US1] 扩展 `src/dataforge/v7/web.py`、`frontend/src/api/platform.js` 与任务请求的治理、Snapshot、Run API。— Depends on: T002,T003,T006 — Implementation: DONE — Validation: PASSED — Validate: FastAPI HTTP 契约测试。
- [X] T008 [DataForge] [US3] 更新 `frontend/src/views/developer/`，在固定四页导航内实现类型、子图/Catalog、Vue Flow Canvas 和 Run 诊断。— Depends on: T007 — Implementation: DONE — Validation: PASSED — Validate: `npm run build`。

## 阶段 4：文档与验收

- [X] T009 [DataForge] 更新 `wiki/`、`V7-CAPABILITY-MATRIX.md`、发布验收和来源快照。— Depends on: T001-T008 — Implementation: DONE — Validation: PASSED — Validate: Wiki 链接与陈述核对。
- [X] T010 [DataForge] [US1] 扩展 `tests/test_v7_platform.py` 并运行后端回归、前端构建与安全文本检查。— Depends on: T001-T009 — Implementation: DONE — Validation: PASSED（本地 29 passed、Vite build）；Compose 验收另列为外部部署门禁。— Validate: Conda `sun` 下全部自动验证。
- [X] T011 [DataForge] [FR-009] 在文档库当前页增加全选和选中文件处理，并以 `/process-selected` 对每个有效模板安全创建待更新版本任务。— Depends on: T007,T009,T010 — Implementation: DONE — Validation: PASSED（`pytest -q tests/test_v7_platform.py tests/test_v7_governed_catalog.py` 43 passed；`npm run build` 通过）。— Validate: Conda `sun` 下后端回归与前端生产构建。
- [X] T012 [DataForge] 在上传预检中逐项显示拖入文件及其预检状态。— Depends on: T011 — Implementation: DONE — Validation: PASSED（`npm run build` 通过）。— Validate: `npm run build`。
- [X] T013 [DataForge] [FR-010] 配置 DataForge API/Worker 的专用 Milvus 网络、Milvus 临时接入/防火墙恢复脚本、systemd 定时单元和部署文档。— Depends on: T012 — Implementation: DONE — Validation: PASSED（`test_milvus_egress_deployment_contract` 静态契约）；Docker Compose/Bash 主机执行另列 T014。— Validate: Compose 静态渲染、Bash 语法和部署脚本静态测试。
- [ ] T014 [Deployment] [FR-010] 在 Docker 主机验证 Milvus/Embedding 最小出站、向量同步、Partition 操作和 Milvus 重建恢复。— Depends on: T013 — Implementation: PENDING — Validation: PENDING — Validate: TC-009、TC-012 手动验收。
- [X] T015 [DataForge] [FR-011] 增加 Graph triple/semantic 模式、两个模式修订与专属 Profile，同时保留旧 Graph 冻结兼容。— Depends on: T013 — Implementation: DONE — Validation: PASSED — Validate: TC-013。
- [X] T016 [DataForge] [FR-012] 增加五个默认 Storage Contract、Managed Collection、规格哈希、归属 token 和幂等 Provisioner，并支持扩展 Profile 同规格复用/异规格新建。— Depends on: T015 — Implementation: DONE — Validation: PASSED（Fake/静态）— Validate: TC-014。
- [X] T017 [DataForge] [FR-011] 打通模式化模板输出、双图谱生成/重试/Sink、物化向量字段与统一图谱查询。— Depends on: T015,T016 — Implementation: DONE — Validation: PASSED — Validate: TC-013。
- [X] T018 [DataForge] [FR-013] 升级 Flow DSL v3 和模板 Vue Flow 编辑器，支持强类型分支/合流、撤销重做、校验与发布。— Depends on: T017 — Implementation: DONE — Validation: PASSED — Validate: TC-015、前端构建。
- [X] T019 [DataForge] 更新 API、Compose Provisioner、调试台、知识类型页、Wiki 与验收文档。— Depends on: T015-T018 — Implementation: DONE — Validation: PASSED — Validate: 静态契约与构建。
- [ ] T020 [Deployment] [FR-011/012] 在真实 Milvus 供应五个默认受管 Collection，验证两个 Graph Collection 的 insert/load/search/release、调试台不访问旧 Collection，并确认外部遗留 `dataforge_graph_knowledge` 无变化。— Depends on: T016,T019,T026 — Implementation: PENDING — Validation: PENDING — Validate: TC-016。
- [X] T021 [DataForge] [FR-013] 将知识流程模板升级为 PC 固定三栏专业 DAG 编辑器，提供自定义节点/边、Typed Handle、算子拖入、Inspector、MiniMap、自动布局、事务历史和结构化本地校验定位。— Depends on: T018 — Implementation: DONE — Validation: PASSED（Node 逻辑测试 8 passed、Vite build、1440×900/1920×1080 浏览器验收，Console 无错误）。— Validate: TC-017。
- [X] T022 [DataForge] [FR-014] 实现 MinerU 3.4.4 Pipeline GPU 镜像、内部/回环网络、PDF Adapter、页级 SourceChunk、Middle JSON Artifact、生命周期、失败保真与分层超时。— Depends on: T004,T009 — Implementation: DONE — Validation: PASSED（Conda `sun` 相关 V7 回归 60 passed；Compose/Dockerfile 静态契约通过）。— Validate: TC-018。
- [ ] T023 [Deployment] [FR-014] 在 NVIDIA Docker 主机完成 CUDA/模型、双路径 health、局域网拒绝、文本/扫描/混合 PDF 及 MinerU 停止恢复验收。— Depends on: T022 — Implementation: PENDING — Validation: PENDING — Validate: TC-019。
- [X] T024 [DataForge] [FR-015] 增加算子中文说明、版本化输入输出示例、兼容迁移、逐节点受控内存预览与 Inspector 展示。— Depends on: T021 — Implementation: DONE — Validation: PASSED（V7 回归 63 passed、前端逻辑测试 8 passed、Vite build）。— Validate: TC-020。
- [ ] T025 [DataForge] [FR-016] 固化 Document Parser 路由边界、MinerU `pipeline + auto`、空参数发布契约与 Inspector 只读展示，并明确排除扫描件检测和 Image Parser。— Depends on: T022,T024 — Implementation: DONE — Validation: BLOCKED（Owner: 并行 `007-flow-development-workbench` 迁移；`20260814_02_flow_workbench.py` 在空 SQLite 库重复创建 `flow_node_artifact_bindings`，使 Catalog/PDF Runner 回归在进入 Parser 逻辑前失败。Parser 纯契约 12 passed，前端最新工作区 12 passed 且 Vite build 通过。Next: 修复该迁移的幂等创表后重跑 TC-021）。— Validate: TC-021。
- [X] T026 [DataForge] [FR-011] 保留旧 `graph` Index Profile、旧 Graph 类型修订和冻结选择分支；容量报告按 code 跳过 legacy Collection并返回未监控原因，其他 external Profile 仍正常探测。— Depends on: T016 — Implementation: DONE — Validation: PASSED（定向回归 5 passed；当前-schema 临时空库集成检查通过；前端逻辑测试 12 passed且 Vite build 通过；扩展 pytest 被并行未提交迁移的重复建表阻断，见 summary）。— Validate: TC-013 及相关 V7 回归。

## 状态摘要

| 指标 | 数量 |
|------|------|
| 实现完成或 N/A | 23 |
| 验证通过 | 22 |
| 完全完成 | 22 |
| 验证阻断 | 1（T025，并行 Workbench 迁移） |
| 外部部署待验收 | 3（T014、T020、T023） |

## 关键依赖

T001 → T002/T003 → T004 → T005/T006 → T007 → T008 → T009 → T010 → T011 → T012 → T013 → T014；T004/T009 → T022 → T023。
