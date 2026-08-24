# ADR-005：SourceChunk 是知识生产的强制人工审核 Gate

- 状态：Accepted
- 日期：2026-08-24

## 背景

上传文件的解析、清洗和分块结果属于不可信中间产物。若模板在上传后立即执行，错误分块会直接进入 Text、QA、Triple Graph、Semantic Graph，并进一步形成向量资产和 Routing 可见数据。仅在前端隐藏按钮不能阻止服务端或内部 Worker 绕过。

## 决策

上传自动链路终止于 `SourceChunk pending_review`。只有当前 SourceVersion 的全部活动 Chunk 为 `approved`，服务端才创建不可变 `SourceReviewSnapshot` 并自动调度该文档库已绑定的知识模板。

Knowledge Flow 必须以 Reviewed SourceChunk Input 为根；Parser、Clean、Chunk 属于独立 Source Preparation 流程。Knowledge Job 冻结 Review Snapshot，Knowledge Sink 的 Evidence 必须指向该快照中的 Chunk Revision。AssetVersion 冻结审核摘要；Vector Sync、Ready 与 Routing 缺少合规摘要时全部 fail closed。

已批准 Chunk 修改前必须重开审核。当前不设置知识生成后的第二个人工审核点。

## 结果

- 正向：人工审核成为后端可证明、可审计、不可绕过的生产边界；知识和 Milvus 数据可追溯到批准时的不可变 Chunk Revision。
- 代价：上传不再立即产生知识；需要独立 Preparation/Dispatch 状态、审核工作区和更多持久化血缘。
- 兼容：内置模板直接升级；旧自定义模板标记 `needs_review_upgrade` 并拒绝执行，不能自动放行历史未审核数据。

## 关联

- [知识生命周期](../architecture/knowledge-lifecycle.md)
- [SourceChunk 人工审核 Gate 批准基线](../../wiki/sources/source-chunk-review-gate-2026-08-24.md)
- `specs/015-source-chunk-review-gate/`

