# Implementation Plan: 文档知识生产链路

## Design

- `src/dataforge/platform/` 承担 SQLAlchemy MySQL 模型、迁移、对象存储、流程校验、业务 API 和认证。
- `src/dataforge/runner.py` 是独立 FastAPI 进程，运行固定 DataFlow 适配器并写入运行、节点、事件、产物和知识数据。
- `src/dataforge/worker.py` 以数据库租约投递、轮询、取消和恢复运行，绝不在 API 进程内执行流程。
- 前端改为 Vue Router、Pinia 和 Vue Flow，路由化呈现业务工作区与固定开发区入口。
- Compose 只部署 frontend、api、worker、runner、mysql、minio；旧 overlay/webui 服务与 Gateway 代码删除。

## Validation

- MySQL/MinIO 集成测试使用 Compose；无 Docker 时记录为环境阻塞。
- 单元/API 测试覆盖默认流程、上传、版本绑定、任务恢复、SSE、替换删除和权限。
- `npm run build` 验证前端。
