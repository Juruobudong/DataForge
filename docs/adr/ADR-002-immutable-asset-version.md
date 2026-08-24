# ADR-002：发布使用不可变 AssetVersion

- 状态：Accepted
- 决策日期：2026-08-20
- 适用范围：向量构建、发布、离线迁移、回滚与 GC

## 背景

如果 Vector Sync 直接清空或覆盖运行中的知识库 Partition，构建失败、Embedding 变化或离线导入中断都可能让当前消费者看到半成品。逻辑知识库还会持续增量变化，无法单独作为可复现的发布标识。

## 决策

每次正式 Vector Sync 或离线导入都创建新的 `KnowledgeAssetVersion`，并写入独立的 `kl_<knowledge_library_id>__v<asset_version_no>` 候选 Partition。只有记录数、摘要、load 与冒烟检索验证通过后，版本才成为 Ready。

RouteVersion 固化到明确的 Ready AssetVersion；运行中的 Ready Partition 不 reset、不原地 upsert。旧版本按引用保护和保留策略由显式 GC 回收。

## 结果

- 构建失败只影响候选版本，当前 Routing 保持可用。
- RouteVersion、离线包和回滚可以复现同一物理资产，而不是重新解析“当前知识”。
- 同一 AssetVersion 可被多个项目共享，包内也可按 ID 去重。
- 每次同步需要额外存储；因此 GC 仅删除无 RouteVersion、Candidate、Release 或迁移引用、至少 30 天且不属于最近两个 Ready 版本的资产。
- Collection 生命周期与 AssetVersion 生命周期分离；删除一个资产 Partition 不代表可以删除整个 Collection。

## 未采用的方案

- 固定 `kl_<library_id>` Partition 并原地 reset/upsert：发布期间存在部分可见与回滚困难。
- 仅记录逻辑版本号、激活时重新构建：无法保证离线交付和回滚使用相同字节级资产。

## 实现与关联

- 实现：`src/dataforge/v7/models.py`、`store.py`、`vector.py`、`migration/`。
- 当前架构：[不可变知识资产版本](../architecture/asset-version.md)。
- 来源：[`wiki/sources/institution-multi-project-release-2026-08-20.md`](../../wiki/sources/institution-multi-project-release-2026-08-20.md)、[`wiki/pages/domain-model.md`](../../wiki/pages/domain-model.md)。
