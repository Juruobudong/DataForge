# Routing 架构

> 当前状态：已实现架构，更新于 2026-08-29。真实部署接入状态见 [`V7-CAPABILITY-MATRIX.md`](../../V7-CAPABILITY-MATRIX.md)。

## 授权边界

Routing 的唯一配置事实是：

```text
Project
  → ProjectDeployment
      → Deployment + ProjectDeploymentTask
          → org_code
              → knowledge_library_id[]
```

- Deployment 表达机构或中心发布目标，按 `DeploymentTarget.release_stage` 同时拥有 test/production Milvus Target；ProjectDeployment 表达 Project 关联。`Deployment.release_stage` 只保留 legacy 兼容。
- 同一 Deployment 可以承载多个 Project，但任务、授权、RouteVersion、Snapshot、版本号和回滚互不共享。
- `org_code` 属于 ProjectDeploymentTask 授权，只在已绑定的 Project、Deployment 与 Task 内选择知识库；它不能选择环境、阶段或 Milvus Target。
- 每个授权知识库在冻结前必须通过类型、Profile、Vector Ready 和目标 Partition 校验。

## RouteVersion 与 Snapshot

RouteVersion 按 `(project_deployment_id, release_stage)` 独立编号。冻结时系统把授权解析为确定的 Ready AssetVersion，并生成 Snapshot v3：

- Snapshot 固化 Project、Deployment、阶段、任务合同、`org_code` 授权和版本化 `kl_*__vN` Partition。
- 历史版本不可修改；当前版本和 last-known-good 按 ProjectDeployment 与阶段隔离。
- Snapshot 文件写入 `routing-snapshots/<project_code>/<deployment_code>/<release_stage>/`：先写历史文件，再原子替换 `routing.json`。
- 消费端只读取 Snapshot 指定的 Collection/Profile/Partition，不扫描 Collection，也不根据 `org_code` 猜测 Partition。

## 在线发布

- `scope=central` 的中央环境可以在线校验并发布 Routing。
- `scope=institution` 的机构环境在智能中心只冻结单项目 RouteVersion；机构本地 Routing 通过签名 `.dfm` 导入并激活。
- Routing Validate 先检查配置、Profile、知识库、AssetVersion 和预期 Contract。central Deployment 与机构本地执行 live Milvus 校验并分别报告 Collection/字段/dimension/Partition；中心到 institution Deployment 返回 `deferred_to_local`，不连接机构现场 Milvus。
- 每次 Validate/Diff/Freeze/Publish/Rollback/Runtime 请求显式选择环境；切换前端环境 Tab 不写 Deployment、不隐式发布、不复制另一环境版本，也不修改授权。
- 生产发布和生产回滚分别要求所选 production Target 校验与人工确认；没有已发布 production Snapshot 的 Project 在该环境保持不可用。
- Institution Release draft 固化环境并只接受同环境 Frozen RouteVersion；Planner 与 `.dfm` 使用对应阶段 Target，禁止混合 test/production。
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
