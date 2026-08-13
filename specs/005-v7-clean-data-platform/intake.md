# 需求输入：DataForge V7 全新数据重建

来源为用户确认的 V7 最终架构与前端原型，登记见 [`wiki/sources/dataforge-v7-final-architecture-2026-08-10.md`](../../wiki/sources/dataforge-v7-final-architecture-2026-08-10.md) 与 [同库切换决定](../../wiki/sources/v7-same-name-cutover-2026-08-10.md)。关键决定：不迁移或读取 V2；保留 MySQL/MinIO 的 `dataforge` 名称，在同库删除受限 V2 表后新建 V7 表；旧 FAQ Collection/Partition 只人工处置；本轮不改 qa_agent。

2026-08-11 用户确认 V7 功能完善：补齐知识库删除治理、受控模板修订、图谱浏览、完整来源追踪和已有路由/向量能力的可读界面。删除保护覆盖 Draft 与已发布路由；模板只允许固定线性阶段和受限参数；知识类型、四个 Index Profile、V2 边界与 qa_agent 范围保持不变。

2026-08-11 用户确认已在部署环境手动删除 V2 Docker 数据卷，不需要保留旧数据库。DataForge 必须删除应用内的 V2 SQL 清理链路；Compose 仅支持空库或已有 V7 schema 的常规 Alembic 升级。
