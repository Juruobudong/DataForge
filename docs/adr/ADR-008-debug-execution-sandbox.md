# ADR-008：Debug Execution Sandbox 与流程定义分离

**状态**：Accepted  
**日期**：2026-08-26

## 决策

运行调试采用一等公民 `DebugRunInputSnapshot → FlowRun(debug_full)`，与 `KnowledgeJob`、`SourcePreparationJob` 三种 owner 互斥。Debug 输入可以是版本化内置 Sample 或同库多个当前审核 Snapshot；二者都会冻结为 resolved chunks、输入 digest、源 authoring definition、编译执行快照和 Sink Preview target。Runner 只生成 NodeRun、Artifact、事件和 Sink Diff，不写正式知识。

内置 Sample 使用虚拟空库基线，所有有效候选只计为预计 `ADD`，不要求或创建 KnowledgeLibrary；真实审核输入继续按运行时 KnowledgeLibrary 计算完整 Diff。正式 KnowledgeJob 明确拒绝内置 Sample。

成功 Debug Run 可以把 schema-valid、可映射的有效参数应用回未变化的当前自定义 Draft，或从冻结源定义创建新的自定义 Advanced Draft。Standard 来源先通过 `FlowAuthoringCompiler.materialize()` 转为源级 DSL；禁止复制展开后的 Runtime DAG。运行输入、KnowledgeLibrary ID、Artifact、日志、指标和 Preview 永不进入流程定义。

## 理由

- 流程定义是可复用开发资产，Debug Run 是一次性执行证据，两者生命周期和审计语义不同。
- Preview-only 保持“流程开发区验证、业务工作区正式生产”的边界，不伪造 KnowledgeJob 或污染 KnowledgeChange。
- 冻结源定义并由后端维护 runtime-to-source 映射，避免把子图展开节点、Artifact edge 或运行 ID 反向保存成 Flow DSL。

## 后果

- Runtime DAG 保持只读；结构、连线和 Prompt 正文继续在流程编辑器修改。
- Standard 转 Advanced 始终创建新的 Custom Advanced Draft，来源 Standard Flow 不变化。
- 新 Sandbox 默认对管理员开放；旧业务 Run 派生和正式提交开关只保留兼容，不控制 Debug full。
- 真实 LLM/Milvus 行为仍需在 `.34` 空卷环境验收，生产 `.36` 不在本决策授权范围内。
