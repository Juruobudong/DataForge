# 实施计划：V7 全新数据平台

1. 在 `dataforge.v7` 建立独立 SQLAlchemy 模型和 Alembic revision。
2. 实现 Source/SourceVersion、当前态知识、Vector Sync、RoutingSnapshot 的事务服务。
3. 将 Web、Worker、Runner 命令入口与 Compose 定向到 V7 schema，并对空库或已有 V7 schema 执行常规 Alembic 升级。
4. 替换 Vue 导航与业务页面，覆盖上传、任务、知识库、授权和索引。
5. 增加 V7 回归测试并同步 README/Wiki/来源登记。

6. 在后续 V7 revision 中加入删除任务、结构化来源证据、变更快照和模板修订；Worker 只删除受验证的 V7 Partition。
7. 将图谱知识投影为 MySQL 驱动的实体/关系/Evidence API，并在 Vue 中复用 Vue Flow。
8. 扩展知识库、项目授权、模板与工作台页面；固定知识类型和索引页面继续保持只读受控。
9. 删除 V2 SQL 表白名单和清理命令；Compose 仅执行空库或已有 V7 schema 的常规 Alembic 升级。

真实 MySQL/MinIO/Milvus/Embedding 验收在部署环境进行；Compose 只接受空库或已有 V7 schema，不清理旧 MinIO/Milvus 资源。
