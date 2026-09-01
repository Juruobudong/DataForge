# ADR-006：SourceChunkSet 候选审核与原子提升

**状态**：Superseded by [ADR-011](ADR-011-parsed-document-flow-owned-chunking.md)
**日期**：2026-08-25

> 本文保留历史决策。当前 Set、审核与 Snapshot 归属 Flow Revision，不再归属 Source Preparation 或 SourceVersion。

## 决策

一次 Source Preparation 产生一个 `SourceChunkSet`。新结果先处于 `candidate`，只有该 Set 的全部活动 Chunk 已批准并冻结 `SourceReviewSnapshot` 后，才在同一事务中提升为 `active`；旧 Active 同时转为 `superseded`。失败 Set 保留为 `failed`，不得改变既有 Active、Snapshot 或 Ready 资产。

`SourceReviewSnapshot` 必须显式绑定 `chunk_set_id`，Runner 的 Chunker 参数必须来自 Preparation Job 冻结的 `FlowExecutionSnapshot`。Retry 复用原 Snapshot；Rechunk 使用指定或最新已发布 Snapshot并创建新 Candidate。

## 理由

- 允许审核新分块时继续提供旧正式知识，避免 Rechunk 造成服务空窗。
- 相同内容的不同分块尝试仍有独立身份、审核记录和 Promotion 边界。
- 前端配置只有在不可变 Snapshot 被 Runner 实际消费时才是真实生产配置。

## 后果

- SourceVersion 同时保存 Active/Candidate 指针，审核 API 默认 Candidate，否则 Active。
- Chunk 编辑、拆分、合并、删除和审核只能作用于当前审核目标。
- Routing wire schema 和下游消费者无需变化；部署测试仍需在 `.34` 验证真实链路。
