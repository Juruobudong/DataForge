# Feature Specification：V7 全新数据平台

## 要求

1. MySQL `dataforge` 的空库或已有 V7 schema 由 Alembic 创建或升级；运行时仅接受 V7 revision。
2. 支持新文件上传、替换、删除、重试、搜索筛选和 SourceVersion 溯源。
3. 支持显式目标知识库的文/问/图当前态生产、Diff 和多来源。
4. 只管理四个 V7 Collection、知识库 ID Partition、向量 Ready 和容量监控。
5. 支持项目知识授权、Validate、原子 RoutingSnapshot、last-known-good 与回滚。
6. 前端严格显示 V7 双工作区；不提供旧平台页面或 qa_agent 迁移。
7. 知识库删除必须先检查全部 Draft 和已发布路由引用；无引用时异步清理该库的 V7 Partition，保留 MySQL 审计与历史。
8. 流程模板必须是可修订、可发布的受控线性配置，处理任务固定引用已发布修订；不得开放任意 DAG 或代码。
9. 知识项必须能返回结构化来源证据；图谱必须提供实体、邻居和关系证据的浏览接口。
10. 前端必须提供知识库 Diff/向量/来源视图、路由历史与快照预览、模板管理、图谱浏览和工作台统计。

## 禁止事项

不得读取或迁移旧数据，也不提供数据库清理命令。不得自动删除旧 MinIO、旧 Milvus Collection、12 个 FAQ 业务 Partition 或 `_default`。
