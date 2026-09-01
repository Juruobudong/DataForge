# Routing 架构

> 当前状态：已实现架构，更新于 2026-08-29。真实部署接入状态见 [`V7-CAPABILITY-MATRIX.md`](../../V7-CAPABILITY-MATRIX.md)。

## 授权边界

Routing 的唯一配置事实是：

```text
Project
  → ProjectReleaseTask
      → org_code
          → knowledge_library_id[]
  → ProjectRouteVersion
      → ProjectPublication(test|production, Target Revision)
```

- 中心 test/production 的 Target 由 `InstanceReleaseTarget` 保存精确 verified Registry Revision；所有项目共用，首次绑定后本期不可改绑。
- Deployment 只表达机构身份和机构 Release 的项目归属，不参与中心在线任务、授权或版本编号。
- `org_code` 属于 ProjectReleaseTask，唯一边界为 `(project_release_task_id, org_code)`，不要求等于机构 `institution_code`。
- 每个授权知识库在冻结前必须通过类型、Profile、Vector Ready 和目标 Partition 校验。

## RouteVersion 与 Snapshot

RouteVersion 按 Project 独立编号。冻结时系统把授权解析为确定的 Ready AssetVersion，生成不含环境、Deployment 和 Target 的项目 Snapshot：

- Snapshot 固化 Project、任务合同、`org_code` 授权和版本化 `kl_*__vN` Partition。
- ProjectPublication 再固化环境、精确 Target Revision、checksum 与原子文件；last-known-good 按 Project 与阶段隔离。
- Snapshot 文件写入 `routing-snapshots/<project_code>/<deployment_code>/<release_stage>/`：先写历史文件，再原子替换 `routing.json`。
- 消费端只读取 Snapshot 指定的 Collection/Profile/Partition，不扫描 Collection，也不根据 `org_code` 猜测 Partition。

## 在线发布

- 中心 Publication 对实例级 Target 执行 live 校验与 Partition Delivery。机构 Release 选择目标机构已绑定项目的 frozen RouteVersion；中心不连接机构 Milvus。
- 每次 Validate/Diff/Freeze/Publish/Rollback/Runtime 请求显式选择环境；切换前端环境 Tab 不写 Deployment、不隐式发布、不复制另一环境版本，也不修改授权。
- 生产发布和生产回滚分别要求所选 production Target 校验与人工确认；没有已发布 production Snapshot 的 Project 在该环境保持不可用。
- Institution Release draft 固化目标机构与环境，但 frozen ProjectRouteVersion 本身无环境；同一 Project 在一个 Release 中只能选择一个版本。
- 生产 Partition delivery 只同步候选 Snapshot 实际引用的授权资产，并在原子发布前完成校验与备份。

## 回滚与失败

- 在线回滚从历史 RouteVersion 恢复授权事实，再生成一个新的已发布版本；历史快照本身不被改写。
- local 单项目激活在同一事务和原子文件替换边界内更新 RouteVersion 与 RoutingSnapshot；失败时旧 Published RouteVersion 和旧 `routing.json` 继续有效。
- local 批量激活明确为非原子顺序操作：一个项目失败不回滚已成功激活的其他项目。
- 消费端保留 last-known-good；没有有效 Snapshot 时 fail closed，不跨 Deployment 或阶段借用版本。

## 来源与关联

- 实现：`src/dataforge/v7/routing.py`、`routing_delivery.py`、`store.py`、`web.py`，以及 qa_agent 的 DataForge Routing 客户端。
- 详细事实：[`wiki/pages/deployment-and-migration.md`](../../wiki/pages/deployment-and-migration.md)、[`wiki/pages/domain-model.md`](../../wiki/pages/domain-model.md)。
- 决策：[ADR-003 RoutingSnapshot](../adr/ADR-003-routing-snapshot.md)。

## 检索调试扩展（2026-08-28）

任务的 final_top_k、reranker_serving_code 及重排模型身份增量冻结到 Snapshot v3；现有消费端字段不变。管理员检索调试复用授权边界，Draft 只生成内存快照，Published/Historical 只读取指定快照；检索执行不写 Routing/Asset/Milvus。正文与 Evidence 使用 knowledge_asset_items，Query Embedding 使用资产冻结的模型/维度；临时覆盖不保存。中心到机构仍仅解析 Routing。详见 [实施基线](../../wiki/sources/reranker-retrieval-debug-2026-08-28.md)。

## Public Retrieval v1（2026-08-29）

RoutingSnapshot 继续作为内部基础设施契约，保留 Milvus Target、Collection、Partition、Embedding 与 Storage Contract。业务边界改用 Published-only Public Retrieval v1：逻辑身份为 `project_code/deployment_code/release_stage/task_code + org_code`，Public DTO 仅输出 route、policy、正文、业务 data、评分、Context 与冻结 Evidence。独立 Retrieval Token 不能读取 Runtime Snapshot；管理员测试端点复用相同执行器和 presenter，但不向浏览器下发 token。当前 qa_agent 与 kg_for_consultation 尚未迁移。详见 [实施基线](../../wiki/sources/public-retrieval-gateway-2026-08-29.md)。

## Milvus Connection Contract（2026-08-30）

中心 Registry 以稳定 Target + 不可变 URI/加密 Token Revision 管理连接。实例单独绑定 verified Authoring Target；Index、Provision、Vector Sync 和 Inventory 使用该连接，AssetVersion 冻结实际来源 revision。RoutingSnapshot v3 增量冻结目标 revision/fingerprint，DataForge Retrieval 与 Delivery 按冻结 revision 解密 Token；秘密不进入 Snapshot、Public DTO 或 `.dfm`。Seed Target 为 pending 且无绑定，验证结果通过 CAS 防止配置在网络请求期间被替换。详见 [ADR-009](../adr/ADR-009-milvus-connection-contract.md) 与 [实施基线](../../wiki/sources/milvus-connection-contract-2026-08-30.md)。
