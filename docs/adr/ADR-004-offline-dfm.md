# ADR-004：离线知识迁移使用签名 `.dfm`

- 状态：Accepted
- 决策日期：2026-08-17
- 更新日期：2026-08-20
- 适用范围：central 到机构 local 的 Seed、Release 与 Knowledge Update

## 背景

医院 local 环境可能与 central 网络隔离，central 也不应持有或操作医院 local Milvus 凭据。离线交付仍需证明包完整、来源可信、依赖闭合，并允许在 Milvus 尚未配置时先恢复元数据与对象。

## 决策

使用 ZIP64 `.dfm` 容器交付。业务 entry 由 SHA-256 清单覆盖，包由受信 `key_id` 对应的 Ed25519 密钥签名；路径经过规范化和目录穿越门禁。应用层不加密包，保密由离线介质和交付流程负责。

schema v2 提供三种语义：首次 `deployment_seed`、后续多项目 `institution_release`、不携带可应用路由的 `knowledge_update`。包携带实际运行闭包和完整当前资产；local 通过持久检查点与可恢复 `waiting_*` 状态完成导入，再以 ImportedRouteCandidate 显式激活。

## 结果

- central 不连接医院 local Milvus，local 私有资产也没有回传路径。
- 验签、feature、版本和 Contract 门禁可以在写入向量前失败。
- Milvus 缺失、未验证或容量不足是可恢复等待状态，不会把已导入元数据误标为失败。
- Knowledge Update 不改变 local 授权或 Routing；采用新资产必须重新下发 Institution Release。
- `.dfm` 本身不提供机密性，介质管理是部署流程的必要组成部分。
- 当前包携带完整资产，体积高于真正增量包；分卷、断点上传和机构公钥加密明确延期。

## 未采用的方案

- central 在线直连医院存储：扩大网络与凭据边界，不适用于隔离环境。
- 只复制向量、不携带模板与执行依赖：目标端无法验证知识资产语义和可重现性。
- Knowledge Update 自动改写 local Routing：会覆盖本地自治授权，并让知识传输隐式变成生产发布。

## 实现与关联

- 实现：`src/dataforge/v7/migration/`、`instance.py`、`store.py`、`worker.py`。
- 当前架构：[Deployment Fork 与离线迁移](../architecture/deployment-migration.md)。
- 来源：[`wiki/sources/deployment-fork-offline-migration-2026-08-17.md`](../../wiki/sources/deployment-fork-offline-migration-2026-08-17.md)、[`wiki/sources/institution-multi-project-release-2026-08-20.md`](../../wiki/sources/institution-multi-project-release-2026-08-20.md)。
