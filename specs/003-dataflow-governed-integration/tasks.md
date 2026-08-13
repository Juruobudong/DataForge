# Tasks: 文档知识生产链路

- [ ] T001 [foundation] 替换规格、依赖、配置、MySQL/MinIO 基础设施 — Implementation: DONE — Validation: PARTIAL (SQLite 开发回退通过；MySQL/MinIO 需 Docker)
- [ ] T002 [backend] 实现流程、默认流程、文档、任务、SSE 和审计 API — Depends on: T001 — Implementation: DONE — Validation: PASSED (平台存储与 API 5 项回归)
- [ ] T003 [runner] 实现独立 DataFlow Runner、Worker 租约和恢复 — Depends on: T001 — Implementation: DONE — Validation: PARTIAL (模块编译通过；服务集成需 Docker)
- [ ] T004 [frontend] 路由化双工作区、文档库、上传、流程画布和知识结果 — Depends on: T002 — Implementation: DONE — Validation: BLOCKED (npm 未能创建 node_modules)
- [ ] T005 [deploy] 六服务 Compose、归档命令和运行时镜像 — Depends on: T001,T003,T004 — Implementation: DONE — Validation: BLOCKED (Docker CLI 未安装)
- [ ] T006 [tests/docs] 自动测试、构建、Wiki、README、来源与日志 — Depends on: T001-T005 — Implementation: DONE — Validation: PARTIAL (pytest 5 passed；前端与 Compose 待验证)
