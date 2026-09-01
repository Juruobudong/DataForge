# ADR-007：SourceAnchorV2 与证据视图分离

**状态**：Superseded by [ADR-011](ADR-011-parsed-document-flow-owned-chunking.md)
**日期**：2026-08-25

> 本文保留历史决策。当前 ParseJob 在 ParsedDocument Anchor Map 建立来源定位，FlowChunkRevision 只冻结所消费的范围；不存在 Source Preparation Anchor 所有权。

## 决策

Source Preparation 在 Parser 边界建立 `SourceAnchorV2`，并通过 Cleaner、Chunker、SourceChunk Revision 与 ReviewSnapshot 保持来源位置。契约继续保存在既有 `anchor_json`，不新增表；PDF 使用 1 起始页码、0 起始页索引和 `0~1` 归一化 bbox 数组，DOCX 使用稳定的标题、段落、表格行 Block ID。一个 Chunk 可以引用多个页面或块。

原始证据和审核文本是两个独立维度：人工编辑 Chunk 不改写 Anchor；连续 Merge 合并、排序并去重父位置；Split 只有在未编辑文本可安全映射时裁剪位置，否则子 Chunk 继承父位置并明确标记 `precision=parent`。旧页级 Anchor 继续只读兼容，无位置时显式标记 `unavailable`。

Workbench 左侧按来源类型选择证据视图：PDF 使用本地 PDF.js Canvas/Text/Highlight 三层连续 Viewer，DOCX 使用结构化标题、段落和表格块视图；本期只实现 `Chunk → 原文` 单向定位，不把 DOCX 转为 PDF，也不承诺 Word 像素级分页。

## 理由

- 坐标属于来源血缘，不应依赖浏览器内置 PDF Viewer 或混入可编辑 Chunk 文本。
- 多位置数组可正确表达多栏、跨页表格和合并 Chunk，避免制造一个覆盖无关内容的大矩形。
- `precision` 让人工 Split 和历史数据诚实降级，不伪造错误精确度。
- PDF 与 DOCX 复用一个版本化顶层契约，同时允许各自使用合适的证据视图。

## 后果

- 旧 Chunk 仍可页级定位；重新分块后获得 v2 精确血缘，不做历史 bbox 回填。
- Source Detail 的 DocumentIR 增加 `source_type/blocks`，Review Chunk 的 `anchor` 明确返回版本和精度。
- Routing、Knowledge Flow、ReviewSnapshot 和数据库 schema 均不变化；真实 MinerU/PDF/DOCX 仍需在 `.34` 验收。
