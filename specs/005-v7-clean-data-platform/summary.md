# Feature Summary：DataForge V7 全新数据重建

**Stage**：IMPLEMENTATION  
**Updated**：2026-08-11

V7 已实现为独立运行时：在原有 `dataforge` 名称中对空库或已有 V7 schema 执行常规 Alembic 升级、文档上传和溯源、当前态知识、四 Collection、路由快照、V7 前端与回归测试。旧数据既不读取也不迁移；旧 Milvus/MinIO 资源不自动删除。

2026-08-11 增量功能已完成：知识库路由引用保护与异步 V7 Partition 删除、结构化 Evidence 和可读 Diff、受控模板修订/发布/样例运行、图谱实体与关系证据投影、路由版本预览，以及知识库/模板/项目/工作台界面补全。新 schema revision 为 `20260811_v7_features`；已有 `20260810_v7` 数据库只做正常升级。

2026-08-11 用户确认已手动删除 V2 Docker 数据卷，因此移除 V2 SQL 表白名单、数据库清理函数与 CLI 开关；Compose 只运行 `--upgrade-platform`。迁移 CLI 帮助、11 项 V7 回归和前端构建均已通过；本机 `uv` 因缓存路径冲突不可用，Python 回归改由已启用 `sun` 的项目 `.venv` 完成。

- Implementation：15 DONE，1 NOT_STARTED。
- Validation：15 PASSED，1 BLOCKED（真实部署集成）。
- 已验证：`tests/test_v7_platform.py` 11 passed；`frontend npm run build` passed。

本轮已获需求批准：实现删除治理、结构化来源与 Diff、受控模板修订、图谱浏览、V7 管理界面补全、原型视觉完整落地，以及能力矩阵、只读 DataFlow 调试台和上线验收清单。T009–T016 均已完成并通过本地验证；外部部署验收 T008 仍独立阻塞。

下一步：在真实 MySQL `dataforge`、MinIO、Milvus、Embedding 环境完成 T008；确认空库和 `20260810_v7 → 20260811_v7_features` 的常规升级、删除任务仅清理目标 V7 Partition，且旧 Collection 仍由人工处置。
