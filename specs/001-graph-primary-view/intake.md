# Requirement Intake: 图谱主视图

**Artifact Language**: zh-CN
**Prepared**: 2026-08-01
**Status**: READY

## Source Register

| ID | Source | Type | Version/Date | Access | Purpose |
|----|--------|------|--------------|--------|---------|
| SRC-001 | 用户需求：“参考 LightRAG v1.5.5，图谱类知识库优先展示自带图谱可视化 WebUI” | conversation | 2026-08-01 | READ | 确定图谱知识库应以可视化而非记录列表为首要展示内容。 |
| SRC-002 | `D:/AppData/Temp/codex-clipboard-b3b4ba54-1e92-43af-979a-a1454a6354fa.png` | image/design | 2026-08-01 | READ | 当前详情页将知识记录置于图谱关系浏览器之前，作为布局问题的直观证据。 |
| SRC-003 | https://github.com/HKUDS/LightRAG/tree/v1.5.5/lightrag_webui/src/features/GraphViewer.tsx | external code reference | v1.5.5, accessed 2026-08-01 | READ | 参考图谱画布、节点搜索、选中属性及缩放/聚焦等首要探索交互；不复用其 React/Sigma 代码或服务。 |
| SRC-004 | 用户需求：“实体（同义）消除与图谱画布强化”及 `D:/AppData/Temp/codex-clipboard-a1356088-44c7-4788-8094-c634a7192c09.png` | conversation/image/design | 2026-08-01 | READ | 确认新图谱任务默认语义消歧、保留可审计别名，及左侧列表、全宽画布、连接度视觉层级和 80 条默认子图。 |
| SRC-005 | 用户需求：“实体名写在圆圈节点里面” | conversation | 2026-08-01 | READ | 确认实体标签必须位于圆形节点内部；采用最多两行、超长省略和完整名称可访问的规则。 |

## Product Intent

- **Problem**: 图谱型知识库详情先显示分页记录，用户需滚动后才能看到关系；现有关系浏览器是线性列表，不能直观呈现实体间的连接。
- **Users/Stakeholders**: 需要探索医疗文档实体、关系和证据来源的业务用户。
- **Desired Outcome**: 打开图谱型知识库时，详情首屏即提供可操作的图谱画布；用户可筛选、选择实体或关系，并沿现有证据溯源回到记录。非图谱知识库保持现有记录优先体验。
- **Extended Outcome**: 新建图谱任务可默认消除明确同义实体而不改写原始知识或证据；图谱画布将优先突出高连接实体，并在更宽的详情区域中保持可读。

## Extracted Facts and Constraints

- 仅具有 `baseDetail.graph` 的知识库拥有图谱投影；当前前端已调用图谱筛选、邻域和边证据 API。— Source: SRC-002
- 当前 `GET /api/knowledge-bases/{id}/graph` 返回受 `query`、`predicate` 和安全上限约束的节点、边与关系类型，可作为画布数据源；不需要增加数据存储或修改现有合同。— Source: SRC-002
- LightRAG v1.5.5 将 `GraphViewer` 作为独立 WebUI 功能，采用图谱画布并配备搜索、聚焦、图例、缩放和属性面板等探索控制。— Source: SRC-003
- 用户只要求参考 LightRAG 和优先展示，并未要求运行、嵌入或依赖 LightRAG 服务。— Source: SRC-001
- 新需求明确仅对新建且启用开关的图谱任务做模型自动消歧；不提供人工别名页、不迁移历史图谱，失败需降级发布。— Source: SRC-004

## Conflicts and Gaps

- “自带图谱可视化 WebUI”可理解为嵌入完整 LightRAG 前端，也可理解为在 DataForge 中提供同类原生可视化。直接嵌入将引入独立服务、认证与数据契约，超过当前请求的明确范围。

## Agent Inferences

- **Inference**: 采用 DataForge 原生 SVG 图谱视图来参考 LightRAG 的探索体验，而非接入其 React/Sigma 前端或后端。依据是 SRC-001 使用“参考”而非“集成”，且现有 API 已提供所需数据与溯源能力。若用户期望完整 LightRAG 部署，需另立跨服务集成需求。
- **Inference**: 初版画布仅渲染当前 API 返回的筛选结果，并为密集图维持现有上限；不在浏览器端加载全量图谱或持久化用户拖拽位置。

## Decisions Required

- 无。上述原生实现解释不改变 API、数据或权限边界；若后续要求嵌入完整 LightRAG WebUI，则需新的产品和部署决策。

## Traceability Notes

SRC-001 驱动 FR-001 至 FR-005 和 SC-001 至 SC-004；SRC-002 约束现有行为兼容性和验收场景；SRC-003 仅提供交互模式参考，不引入其实现或依赖。
