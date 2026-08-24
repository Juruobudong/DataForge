# ADR-003：Routing 使用不可变 Snapshot 原子发布

- 状态：Accepted
- 决策日期：2026-08-17
- 更新日期：2026-08-20
- 适用范围：ProjectDeployment 授权、阶段隔离、在线发布与 local 激活

## 背景

消费者必须把 `org_code` 稳定映射到一组可以检索的知识资产。若消费者直接扫描 Milvus、读取正在变化的授权表，或把同一家医院的多个 Project 共用一份路由状态，发布过程会产生跨项目、跨阶段或半更新读取。

## 决策

授权事实固定为 `Project → ProjectDeployment → DeploymentTask → org_code → knowledge_library_id[]`。冻结时系统把每个知识库解析到明确 Ready AssetVersion，生成不可变 RouteVersion 与 Snapshot v3。

Snapshot 按 Project、Deployment 和 `test|production` 隔离。发布时先写历史版本，再原子替换当前 `routing.json`；消费者只读取 Snapshot 指定的 Profile、Collection 和 `kl_*__vN` Partition。历史版本与 last-known-good 保留，回滚通过生成新的已发布版本完成。

## 结果

- 消费端不扫描 Collection、不猜测 Partition，也不直接解释中心授权表。
- 同一医院 Deployment 可承载多个 Project，同时保持任务、授权、版本与回滚隔离。
- 阶段切换不会隐式发布或借用另一阶段 Snapshot；缺失目标阶段版本时 fail closed。
- 单项目发布和 local 激活可以在失败时保留旧 Snapshot；批量 local 激活明确为非原子顺序操作。
- 每次冻结前必须验证 Profile、Contract、Ready AssetVersion 和目标 Partition，发布流程比直接改配置更严格。

## 未采用的方案

- 以 `org_code` 直接作为 Milvus Partition：会把业务路由键与物理版本耦合，无法安全共享资产或回滚。
- 让消费者动态读取授权表并拼装路由：无法提供原子切换、不可变审计和 last-known-good。
- 在共享 Deployment 下复用一个跨 Project Snapshot：会破坏 Project 级任务合同与授权隔离。

## 实现与关联

- 实现：`src/dataforge/v7/routing.py`、`routing_delivery.py`、`store.py`、`web.py`。
- 当前架构：[Routing 架构](../architecture/routing.md)。
- 来源：[`wiki/sources/hospital-deployment-routing-2026-08-17.md`](../../wiki/sources/hospital-deployment-routing-2026-08-17.md)、[`wiki/sources/institution-multi-project-release-2026-08-20.md`](../../wiki/sources/institution-multi-project-release-2026-08-20.md)。
