# 知识生命周期

> 当前状态：仓库已实现，更新于 2026-08-24；真实 `.34` 服务链路待部署验收。

## Source Preparation 与 ChunkSet

Source Preparation 将 Parser、Cleaner 与 Chunker 参数冻结在 `FlowExecutionSnapshot`，并先产生 `candidate` SourceChunkSet。SourceVersion 可以在旧 `active` Set 和批准 Snapshot 继续对外有效时准备、审核新 Candidate。只有全部 Chunk 通过才能创建绑定该 Set 的 SourceReviewSnapshot；Snapshot 创建、Candidate Promote、旧 Active Supersede、指针更新和 KnowledgeDispatch 在同一事务完成。Retry 复用失败任务 Snapshot，Rechunk 使用显式或最新已发布 Preparation Snapshot。

## 主链路

```text
文档库
  → Source / 不可变 SourceVersion
  → Source Preparation（Parse / Clean / Chunk）
  → SourceChunk 人工审核 Gate
  → 不可变 SourceReviewSnapshot / SourceChunkRevision
  → 已绑定的已发布知识流程模板
  → 不可变 FlowExecutionSnapshot
  → LLM / Operator → Knowledge Sink
  → KnowledgeLibrary 单一当前态 + KnowledgeChange 历史
  → Embedding → KnowledgeAssetVersion → Milvus
  → Ready → RouteVersion / RoutingSnapshot
```

## 文档与处理

1. 文件或文件夹上传到文档库。Source 表示逻辑文件，文件替换产生新的 SourceVersion；`relative_path` 是目录权威，MinIO object key 不是业务目录。
2. 上传或替换自动创建 SourcePreparationJob。PDF 使用 MinerU Pipeline GPU OCR，DOC/DOCX、CSV/XLSX、Markdown/TXT 使用原生解析；Preparation 只形成 DocumentIR、SourceChunk Revision 和 Artifact 血缘，然后停在待审核。
3. 审核员可查看原文并修改、拆分、连续合并、删除、通过或拒绝 Chunk。只有全部活动 Chunk 通过，SourceVersion 才成为 `approved` 并冻结不可变 SourceReviewSnapshot；拒绝或空 Chunk 集合都不能运行模板。
4. 文档库可提前绑定一个或多个已发布模板。绑定表示“审核完成后运行”；批准 Snapshot 后幂等 Dispatch 自动为各绑定创建 Knowledge Job。模板新修订会使已批准当前 Snapshot 重新进入待处理集合。
5. Knowledge Job 同时固定来源版本、Review Snapshot、结果知识库、模板修订与 `FlowExecutionSnapshot`。Knowledge Flow 只能从 Reviewed SourceChunk Input 开始，不得再次 Parse 或 Chunk。
6. `Knowledge Sink` 是正式知识唯一写入口，并复验候选 Evidence 属于任务冻结的 Chunk Revision/Snapshot。AssetVersion 保存审核摘要；Vector Sync、Ready 和 Routing 缺少合规审核血缘时拒绝继续。

## 单一当前态

KnowledgeLibrary 保存业务查询使用的单一当前知识集合，不要求使用者在多个草稿或候选版本之间选择。

- `knowledge_items` 以 `(knowledge_library_id, source_knowledge_id)` 唯一标识当前知识。
- 同一身份的新结果更新当前项，并在 `knowledge_changes` 记录 `ADD`、`UPDATE`、`INACTIVE` 或 `UNCHANGED`。
- `knowledge_item_sources` 保留 SourceVersion、SourceChunk、结构化锚点和 Evidence；多来源关系在仍有有效 Evidence 时继续有效。
- Q&A、图谱及扩展类型按“类型 × 来源版本 × SourceChunk”记录生成状态。失败分块保留上一版正式知识，成功空结果只撤销自己的旧知识范围。
- 任务形成正式知识后不允许为清理任务历史而删除；任务、Run、变更与来源链继续承担审计。

## 调试与提交

DataFlow 调试台读取既有 Flow Run 的 Runtime DAG、事件和 Artifact。派生 Run 必须复用父 Run 的同一快照：

- `node_only` 只执行目标节点；`from_node` 执行目标节点及其可达下游。
- 临时参数只影响本次派生 Run，不修改已发布模板或父 Run。
- 派生结果到达 Sink 后先进入 `awaiting_commit` 并生成 Diff；管理员确认时重新校验当前态哈希与预览 checksum。
- 派生执行与 Sink 提交由两个独立开关控制，默认关闭。

## 向量化、发布与删除

- Vector Sync 只能从带已批准 Evidence 的当前知识创建不可变 AssetVersion；AssetVersion 先冻结审核摘要，再执行 Embedding 与 Milvus 写入，只有 count、digest、load 与冒烟验证通过后才标记 Ready。
- Routing 只引用 Ready AssetVersion，查询始终限定到 Snapshot 指定的版本化 Partition。
- 删除文件先做影响预检并异步清理对象与失效向量；运行任务会阻断删除。
- 删除知识库时，Draft/已发布路由引用和运行中的 Sink 任务会阻断；门禁通过后只删除该库的 `kl_*` Partition，不删除 Collection。

## 来源与关联

- 实现：`src/dataforge/v7/runner.py`、`store.py`、`worker.py`、`vector.py`、`routing.py`。
- 详细事实：[`wiki/pages/core-workflows.md`](../../wiki/pages/core-workflows.md)、[`wiki/pages/domain-model.md`](../../wiki/pages/domain-model.md)。
- 决策：[ADR-001 单一当前知识](../adr/ADR-001-single-current-knowledge.md)、[ADR-002 不可变资产版本](../adr/ADR-002-immutable-asset-version.md)、[ADR-005 SourceChunk 人工审核 Gate](../adr/ADR-005-source-chunk-review-gate.md)。
