# ADR-001：知识库采用单一当前态

- 状态：Accepted
- 决策日期：2026-08-10
- 适用范围：V7 逻辑知识、来源溯源与业务查询

## 背景

DataForge 的主要使用者需要看到“这座知识库现在有什么”，同时平台必须解释每条知识来自哪个文件版本、哪次运行以及发生过什么变化。若把每次处理结果都暴露成并列业务版本，查询、项目授权和增量处理都必须重复选择版本，容易让“当前可用知识”与“历史审计”混为一体。

## 决策

KnowledgeLibrary 保存单一当前态；每条当前知识以 `(knowledge_library_id, source_knowledge_id)` 唯一标识。新处理结果直接产生 `ADD`、`UPDATE`、`INACTIVE` 或 `UNCHANGED`，并通过 KnowledgeChange、SourceVersion、SourceChunk、Evidence、Flow Run 和 Artifact 保留历史与溯源。

逻辑当前态与发布资产版本分开：业务知识可以继续增量更新，但 Routing 只引用独立构建并验证通过的不可变 AssetVersion。

## 结果

- 业务查询和知识库页面始终读取一个明确当前态，不要求用户选择草稿或候选版本。
- 文件替换、模板修订和失败分块可以按来源范围更新；失败不会自动抹掉上一版正式知识。
- 历史任务、变更和 Evidence 必须保留，不能用删除任务记录替代业务回滚。
- 对外发布需要额外的 AssetVersion 构建步骤，避免把正在变化的当前态直接暴露给消费者。

## 未采用的方案

- 将每次任务结果都作为可查询知识库版本：会把生产过程版本选择推给普通使用者，并增加授权歧义。
- 只保留最新值、不保存结构化历史：无法满足来源追踪、Diff、失败恢复与审计要求。

## 实现与关联

- 实现：`src/dataforge/v7/models.py`、`store.py`、`runner.py`。
- 当前架构：[知识生命周期](../architecture/knowledge-lifecycle.md)。
- 来源：[`wiki/sources/dataforge-v7-final-architecture-2026-08-10.md`](../../wiki/sources/dataforge-v7-final-architecture-2026-08-10.md)、[`wiki/pages/domain-model.md`](../../wiki/pages/domain-model.md)。
