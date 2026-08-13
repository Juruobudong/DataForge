# Requirement Intake: 下线 DataFlow，改用内置医疗标准模板

**Artifact Language**: zh-CN
**Prepared**: 2026-08-02
**Status**: READY

## Source Register

| ID | Source | Type | Version/Date | Access | Purpose |
|----|--------|------|--------------|--------|---------|
| SRC-001 | 用户确认的实施计划与连续澄清对话 | conversation | 2026-08-02 | READ | 下线范围、7 个模板、默认/失败/清理策略 |
| SRC-002 | 当前仓库实现与 Wiki | repository/wiki | 2026-08-02 | READ | 现有 DataFlow、知识任务和前端边界 |
| SRC-003 | 用户确认的界面与默认项调整计划 | conversation | 2026-08-03 | READ | 保留 DataFlow 调试台名称、多轮对话目录与可切换默认模板 |
| SRC-004 | 用户确认的流程开发区布局计划 | conversation | 2026-08-03 | READ | 固定顶部工作区名称、四页顺序、只读标准流程与待开发调试台 |
| SRC-005 | 用户确认的可复用文档库设计 | conversation | 2026-08-03 | READ | 固定版本文档库、任务选择入口与来源删除保护 |
| SRC-006 | 用户确认的图谱结构化输出容错计划 | conversation | 2026-08-03 | READ | 保守过滤、诊断展示、缓存兼容与失败重试 |

## Product Intent

- **Problem**: 嵌入式 OpenDCAI DataFlow 与 Studio 过于复杂，且内置核心源码已不在仓库中，影响产品可用性与维护范围。
- **Users/Stakeholders**: 业务工作区用户、维护内置医疗能力的技术人员。
- **Desired Outcome**: 用户只需从固定医疗模板中选择所需的知识产出；系统以原生文本、问答和图谱流程创建可追溯的独立知识库任务。

## Extracted Facts and Constraints

- 标准流程固定为三项单流程、三项两两组合和一项全量组合；全量组合是默认医疗模板。 — Source: SRC-001
- 组合内的每项任务独立完成；模型或格式失败不回滚其他已成功类型。 — Source: SRC-001
- 图谱模板安装后立即可选。 — Source: SRC-001
- 删除 DataFlow 配置、任务和派生产物，保留所有原始来源及版本。 — Source: SRC-001
- 现有文本块流程在 DataFlow 缺失时才使用 native 降级；问答、图谱已有独立 LLM 执行器。 — Source: SRC-002
- 顶部工作区名称固定为“业务工作区”和“流程开发区”；流程开发区固定依次为知识类型、标准流程、模板、DataFlow 调试台。 — Source: SRC-004
- 标准流程为三个只读内置流程；DataFlow 调试台排在最后并标为待开发，不恢复 Studio、iframe、通用编排或执行引擎。 — Source: SRC-004
- 多轮对话库仅保留在类型目录中，当前无内置模板且不能创建任务。 — Source: SRC-003
- 七个模板的 ID 和批量创建合同不变；“医疗模板”为三项全量组合的显示名，模板默认项可全局持久化切换。 — Source: SRC-003
- 用户在文档管理中选择部分源文档（含全选）并命名创建文档库；后续处理任务仅选择已保存的文档库。 — Source: SRC-005
- 文档库固定创建或显式编辑时的具体来源版本；源文档被文档库引用时必须阻止删除并提示关联文档库。 — Source: SRC-005
- 图谱模型已完成 JSON 修复但仍有少量 schema 违规时，保守剔除不合规项目及其依赖，发布其余严格校验的事实；任务详情显示项目名称和原因。 — Source: SRC-006

## Conflicts and Gaps

- 无未决产品决定。旧 Wiki 仍将 DataFlow 描述为当前能力，实施时必须同步更正。

## Agent Inferences

- **Inference**: 使用服务端固定模板 ID 选择类型，优于由客户端任意提交类型列表；这样可阻止下线通用编排后出现未定义组合。依据：SRC-001 固定 7 种组合。
- **Inference**: 清理历史时仅删除由 DataFlow 引擎或 Studio 流程产生的任务和资产；保留源文档以及 native/LLM 历史结果。依据：SRC-001 的保留范围。
- **Inference**: 任务创建时展开文档库成员并继续写入既有 `knowledge_job.source_version_ids` 快照，无需将任务外键绑定到可删除的文档库。依据：SRC-005 的可复用与历史稳定要求。

## Decisions Required

- 无。用户已明确授权永久删除 DataFlow 派生产物。

## Traceability Notes

SRC-001 对应 FR-001 至 FR-006 与全部模板/清理验收；SRC-002 对应实现边界、兼容迁移和回归范围。
