# Deployment Fork 与离线迁移

> 当前状态：仓库能力已实现；真实 MySQL、MinIO、Milvus、密钥与离线介质验收仍按 Capability Matrix 的 `CONNECT` 项执行。

## central 与 local

- central 是多 Deployment 控制面，维护中央知识、机构 Deployment、项目关联、授权与冻结版本。
- local 通过一次 `deployment_seed` 绑定唯一 Deployment；Seed 完成后成为独立控制面，不能用第二次 Seed 代替重建。
- 中央不连接机构 local Milvus；local 不向中央回传私有知识或本地 Fork。
- Knowledge Update 只带知识资产及必要依赖，不修改 local 授权、`org_code`、Routing、机构身份或本地来源资产。

## `.dfm` v2

`.dfm` 是 ZIP64 容器。新包使用 schema v2，v1 仅保留导入兼容。

| 包类型 | 用途 | Routing 行为 |
| --- | --- | --- |
| `deployment_seed` | 首次初始化机构，可携带多个冻结 Project | 建立唯一 Deployment，并形成待激活 RouteCandidate |
| `institution_release` | 同一机构后续多 Project 发布 | 携带冻结 RouteVersion 并形成待激活 RouteCandidate |
| `knowledge_update` | 更新完整知识资产与必要依赖 | 不携带可应用路由，不改变当前 RouteVersion |

Seed 与 Institution Release 携带实际使用的 Template、FlowExecutionSnapshot、Operator、Prompt、Quality、文档绑定/输出及只读 ProcessingBaseline；不复制中央完整 Job/FlowRun 历史。Frozen Project 资产自动锁定，也可追加额外 Ready AssetVersion；Planner 产生唯一 Inventory 和结构化冲突检查，Freeze 与 Exporter 只消费其中去重后的物理 Partition。

## 完整性与信任

- `checksums.json` 覆盖 manifest 与业务 entry，每个 entry 使用 SHA-256。
- `signature.json` 使用受信 `key_id` 对应的 Ed25519 公钥验证。
- ZIP 路径必须是规范 POSIX 相对路径，重复、绝对路径和目录穿越会被拒绝。
- 应用层不加密 `.dfm`；离线介质与交付流程承担保密。local Milvus Token/密码则使用 `DATAFORGE_CONFIG_ENCRYPTION_KEY` 的 AES-256-GCM 入库并在 API 响应中脱敏。

## 构建、导入与恢复

1. central 从同一机构选择一个或多个 frozen RouteVersion，Planner 解析所需 Ready AssetVersion、文档、对象和 Contract，Worker 构建签名包。
2. local 先验签并执行版本、feature 与 Contract 静态门禁，再导入元数据、模板、知识、文档与对象。
3. Milvus 未配置、未验证或容量不足进入可恢复 `waiting_*` 状态；Worker 保存检查点并释放租约，管理员补充配置后 `/resume`。
4. 向量只写新的 `kl_*__vN` 候选 Partition；验证通过后资产成为 Ready，Seed/Release 再形成 ImportedRouteCandidate。Prepare 完成时 checkpoint 冻结所用 target 的 verified fingerprint。
5. 激活前重新连接同一 target，load/query 每个 Partition 并比较 source/target count/digest，同时复核 AssetVersion 和 Candidate；任一项不通过即 fail-closed。
6. 管理员按项目激活；Activate 只发布 RoutingSnapshot，不执行数据导入。Knowledge Update 必须在中心重新冻结项目并下发新的 Institution Release，才会让 Routing 采用新资产。

## Fork 与冲突

- 中央导入资产标记为 `central_import/synced`；修改正式知识或实际依赖 Source 后整库标记 `forked`。
- 授权、Routing 和 Vector 重同步本身不触发 Fork。
- 更新遇到 Fork 时必须显式选择 `keep_local`、`replace_with_central` 或 `import_as_new`。
- `import_as_new` 重写 Library、Item、Source、Version、Chunk、Vector ID 与新 Partition，且不会自动加入授权。
- Tombstone 只收敛同源中央导入资产，不删除 local 资产。

## 延期边界

多管理员 RBAC/审批、离线激活回执、独立资产或整包回滚、真正增量/分卷/断点上传、机构公钥加密、整包原子激活与机构码迁移不属于当前实现。

## 来源与关联

- 实现：`src/dataforge/v7/migration/`、`instance.py`、`models.py`、`store.py`、`worker.py`、`web.py`。
- 详细事实：[`wiki/pages/deployment-and-migration.md`](../../wiki/pages/deployment-and-migration.md)。
- 决策：[ADR-004 离线 `.dfm`](../adr/ADR-004-offline-dfm.md)、[ADR-003 RoutingSnapshot](../adr/ADR-003-routing-snapshot.md)。
- 来源：[机构发布部署闭环增强批准基线](../../wiki/sources/institution-release-closure-2026-08-22.md)。
