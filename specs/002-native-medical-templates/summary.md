# Feature Summary: 下线 DataFlow，改用内置医疗标准模板

**Stage**: IMPLEMENTATION
**Artifact Language**: zh-CN
**Artifact Readiness**: READY
**Delivery Readiness**: IN_PROGRESS
**Human Gate**: APPROVED
**Updated**: 2026-08-03

## What Is Changing

移除 OpenDCAI DataFlow 与嵌入式 Studio 的运行能力，改为 DataForge 原生的固定模板目录。全量组合显示为“医疗模板”，首次默认时创建文本块、问答和图谱任务，并提供其余六种非空组合。默认项可由用户切换并持久化。

业务用户还可将部分源文档保存为命名文档库。文档库固定成员版本，后续任务仅选择文档库；编辑文档库只影响新任务，来源被文档库引用时不可删除。

图谱模型在自动修复后仍有少量 schema 违规时，会保守过滤不合规项目及其依赖关系，发布其余严格校验的事实；任务详情展示被剔除项目的数量、名称和原因。

## What Is Not Changing

- 不重做向量索引、检索、OCR 或下一期之外的流程能力。
- 不删除原始来源及版本，也不删除 native/LLM 流程的历史结果。
- 不恢复 Studio、iframe、通用编排或 DataFlow 引擎；DataFlow 调试台仅保留为流程开发区中的待开发占位页。

## Decisions and Assumptions

| Item | Type | Owner | Status | Recommendation/Decision |
|------|------|-------|--------|-------------------------|
| DataFlow 范围 | USER DECISION | USER | CONFIRMED | 删除代码、入口、依赖、专用数据与派生产物。 |
| 模板目录 | USER DECISION | USER | CONFIRMED | 预置全部 7 个非空三类组合；全三类为默认。 |
| 组合失败 | USER DECISION | USER | CONFIRMED | 各类型独立完成和重试，不整批回滚。 |
| 图谱状态 | USER DECISION | USER | CONFIRMED | 含图谱模板安装后立即可选。 |
| 来源保留 | USER DECISION | USER | CONFIRMED | 永久删除 DataFlow 历史，但保留所有源文档和版本。 |
| 工作区布局 | USER DECISION | USER | CONFIRMED | 顶部固定“业务工作区 / 流程开发区”；流程开发区依次为知识类型、标准流程、模板、DataFlow 调试台。 |
| 默认模板 | USER DECISION | USER | CONFIRMED | 首次默认医疗模板；任一固定模板可设为全局持久默认项。 |
| 多轮对话库 | USER DECISION | USER | CONFIRMED | 仅保留类型目录，当前没有模板或任务入口。 |
| 文档库 | USER DECISION | USER | CONFIRMED | 文档管理中创建固定版本文档库；任务仅选择文档库，引用来源不可删除。 |
| 图谱输出容错 | USER DECISION | USER | CONFIRMED | 不猜测类型；过滤不合规片段和依赖，显示名称与原因；无有效三元组不发布。 |

## Main Risks

- 清理可能误删共享 Blob；以事务内引用核对和仅删除无引用文件降低风险。
- LLM 运行时不可用；通过独立任务状态、失败信息与重试保留其他产物。
- 工作区已有未提交图谱改动；实现仅触及本功能范围，不重置无关改动。

## Implementation and Validation

- **Implementation**: 已完成固定模板、原生文本、LLM 问答/图谱、模板默认设置、固定版本文档库、接口和前端替换；已移除应用内 DataFlow/Studio 代码、路由、配置和依赖。
- **Validation**: 文档库 API 回归与 Vite 生产构建成功；覆盖版本固定、编辑后续任务、临时来源输入拒绝和来源删除保护。图谱容错覆盖实体/属性过滤、无有效三元组不发布、脱敏诊断与缓存复用；`tests/test_knowledge_flow.py` 30 项、`tests/test_web.py` 24 项和隔离 Vite 构建通过。
- **Review**: `third_party/dataflow_webui/` 的物理删除被其未提交本地改动阻塞，等待用户确认可永久丢弃后完成。

## Changes Since Last Human Approval

- 恢复固定“业务工作区 / 流程开发区”顶部布局；流程开发区严格保留知识类型、标准流程、模板、DataFlow 调试台四页。
- 恢复多轮对话库类型卡片；三项可执行标准流程与七个模板的显示名称按已确认文案调整。
- 模板目录与任务向导改为读取服务端唯一默认项；新增白名单保护的默认项切换接口。
- 文档管理新增源文档/文档库二级视图；处理向导改为仅选择文档库，文档库显式保存成员版本。

## Next Action

可进入 `$devora-review` 审核文档库变更。`third_party/dataflow_webui/` 的物理删除仍等待明确授权，不影响主应用中已下线的运行入口。

## Delivery Counts

| Metric | Count |
| --- | ---: |
| Implementation complete | 15 |
| Validation passed | 15 |
| Fully completed | 15 |
| Blocked | 1 |
