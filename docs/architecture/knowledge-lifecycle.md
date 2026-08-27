# 知识生命周期

> 当前状态：已实现架构，更新于 2026-08-27。

## Source Preparation 与 ChunkSet

Source Preparation 将 Parser、Cleaner 与 Chunker 参数冻结在 `FlowExecutionSnapshot`，并先产生 `candidate` SourceChunkSet。SourceVersion 可以在旧 `active` Set 和批准 Snapshot 继续对外有效时准备、审核新 Candidate。只有全部 Chunk 通过才能创建绑定该 Set 的 SourceReviewSnapshot；Snapshot 创建、Candidate Promote、旧 Active Supersede、指针更新和 KnowledgeDispatch 在同一事务完成。Retry 复用失败任务 Snapshot，Rechunk 使用显式或最新已发布 Preparation Snapshot。

Parser 同时建立 `SourceAnchorV2`：PDF 从 MinerU 内容块保留页码和 `0~1` bbox，DOCX 按原始顺序保留标题、段落与表格行 Block。Cleaner/Chunker 必须同步传播位置数组；人工编辑不改变来源，Merge 合并位置，无法安全映射的 Split 显式降级为父级来源。旧页级 Anchor 继续兼容，但只有重新分块才会获得 v2 精确位置。

## 主链路

```text
文档库
  → Source / 不可变 SourceVersion
  → 已发布知识流程模板
  → 不可变 FlowExecutionSnapshot
  → Worker / Runner / Knowledge Sink
  → KnowledgeLibrary 单一当前态 + KnowledgeChange 历史
  → Vector Sync / Ready KnowledgeAssetVersion
  → RouteVersion / RoutingSnapshot
```

## 文档与处理

1. 文件或文件夹上传到文档库。Source 表示逻辑文件，文件替换产生新的 SourceVersion；`relative_path` 是目录权威，MinIO object key 不是业务目录。
2. 文档库绑定一个或多个已发布模板。每个“文档库 × 模板 × 输出类型”固定对应一个自动结果知识库；首次处理全量文件，之后只处理新增或新版本，模板新修订则重跑该绑定的当前文件。
3. 任务固定来源版本、结果知识库、模板修订与展开后的 `FlowExecutionSnapshot`。Runner 只执行快照中的受控 DAG。
4. PDF 使用 MinerU Pipeline GPU OCR 并形成多页 bbox SourceBlock；DOCX 原生解析形成标题、段落和表格行 Block；DOC、CSV/XLSX、Markdown/TXT 继续使用各自原生路径。解析结果形成 Document IR、SourceChunk、SourceAnchor 和 Artifact 血缘。
5. `Knowledge Sink` 是正式知识唯一写入口。它对来源、Schema、Canonical、质量、身份与 Diff 做门禁；多 Sink 各自事务隔离，成功分支不会被其他失败分支回滚。

## 文本映射与显式生成

Standard 文本和 Multi 的 text 分支使用 `text-knowledge-mapper`，将审核 Chunk 原样映射为 `candidate:text`，无 LLM、Embedding 或二次 Chunk。正文和 Evidence 逐字符保留，候选继承原 SourceAnchor 和审核血缘，并进入原有 Quality/Binding/Diff/Sink。每个非空 Chunk 一对一输出，空块记录成功零候选，重试和替换范围继续以分块处理状态为准。

Standard 转 Advanced 只展开相同 Mapper DAG。显式使用 `prompt-generator` 或 `structured-knowledge-generator` v6 时，文本才按该节点冻结的 Prompt 与 Serving 生成；结构化输出须含有效 canonical 内容，最多一次 Schema 修复，来源和 Evidence 仍由服务器绑定。执行器精确解析版本，已知 v4/v5 保持原文本复制行为，未知版本拒绝回退；不回写历史快照或批量改写 Advanced 定义。

来源：[文本默认映射与 Advanced 显式生成批准基线](../../wiki/sources/text-knowledge-mapping-2026-08-27.md)；前端仅将有 generation 的 Managed 模板显示为四阶段。

## 单一当前态

KnowledgeLibrary 保存业务查询使用的单一当前知识集合，不要求使用者在多个草稿或候选版本之间选择。

- `knowledge_items` 以 `(knowledge_library_id, source_knowledge_id)` 唯一标识当前知识。
- 同一身份的新结果更新当前项，并在 `knowledge_changes` 记录 `ADD`、`UPDATE`、`INACTIVE` 或 `UNCHANGED`。
- `knowledge_item_sources` 保留 SourceVersion、SourceChunk、结构化锚点和 Evidence；多来源关系在仍有有效 Evidence 时继续有效。
- Q&A、图谱及扩展类型按“类型 × 来源版本 × SourceChunk”记录生成状态。失败分块保留上一版正式知识，成功空结果只撤销自己的旧知识范围。
- 任务形成正式知识后不允许为清理任务历史而删除；任务、Run、变更与来源链继续承担审计。

## 调试与流程演化

运行调试从 Draft 或 Published Revision 冻结源 authoring definition 与 Debug Execution Snapshot，再选择版本化内置审核 Sample，或同一文档库中多个当前 SourceReviewSnapshot 创建 `FlowRun(debug_full)`。Input Resolver 将二者统一冻结为 resolved approved chunks。Debug Runner 使用版本固定 Operator 执行真实 DAG；内置 Sample 以虚拟空库计算全 ADD Diff，真实输入按运行时 KnowledgeLibrary 计算完整 Diff，但二者都不修改 KnowledgeLibrary，也不创建 KnowledgeJob、KnowledgeChange 或 Vector Sync。

- `node_only` 只执行目标节点；`from_node` 执行目标及可达下游，并复用同一 Debug 系列父 Artifact。
- 临时参数按祖先到子 Run 合并；只有 schema-valid 且可映射回源节点的配置能进入流程定义。
- “应用到当前草稿”要求来源仍是未变化的当前自定义 Advanced Draft；“保存为自定义流程”和 Standard 转 Advanced 都从冻结/来源定义 materialize 为新的 Advanced Draft，不修改 Standard 来源。
- Runtime 输入、KnowledgeLibrary 绑定、Artifact、日志、指标和 Preview 都不进入流程定义；旧业务 Run 派生/提交开关仅保留兼容。

## 向量化、发布与删除

- Vector Sync 从当前知识创建新的不可变 AssetVersion；只有 count、digest、load 与冒烟验证通过后才标记 Ready。
- Routing 只引用 Ready AssetVersion，查询始终限定到 Snapshot 指定的版本化 Partition。
- 删除文件先做影响预检并异步清理对象与失效向量；运行任务会阻断删除。
- 删除知识库时，Draft/已发布路由引用和运行中的 Sink 任务会阻断；门禁通过后只删除该库的 `kl_*` Partition，不删除 Collection。

## 来源与关联

- 实现：`src/dataforge/v7/runner.py`、`store.py`、`worker.py`、`vector.py`、`routing.py`。
- 详细事实：[`wiki/pages/core-workflows.md`](../../wiki/pages/core-workflows.md)、[`wiki/pages/domain-model.md`](../../wiki/pages/domain-model.md)。
- 决策：[ADR-001 单一当前知识](../adr/ADR-001-single-current-knowledge.md)、[ADR-002 不可变资产版本](../adr/ADR-002-immutable-asset-version.md)、[ADR-006 ChunkSet 提升](../adr/ADR-006-source-chunk-set-promotion.md)、[ADR-007 SourceAnchor 血缘](../adr/ADR-007-source-anchor-provenance.md)、[ADR-008 Debug Sandbox](../adr/ADR-008-debug-execution-sandbox.md)。
