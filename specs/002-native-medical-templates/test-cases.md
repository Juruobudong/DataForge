# Test Cases: 下线 DataFlow，改用内置医疗标准模板

**Spec**: [spec.md](./spec.md)
**Plan**: [plan.md](./plan.md)
**Status**: VALIDATED (第三方目录删除等待工作区改动确认)

## Coverage Matrix

| Test ID | Requirement/Source | Level | Scenario | Expected Result | Automation | Status |
|---------|--------------------|-------|----------|-----------------|------------|--------|
| TC-001 | FR-001 / SRC-001 | unit/API | 查询模板目录 | 恰有 7 项，首次默认医疗模板包含三种类型 | AUTO | PASS |
| TC-002 | FR-002 / SRC-001 | API | 默认、单项和两项模板创建 | 返回的独立任务类型集合与模板完全一致 | AUTO | PASS |
| TC-003 | FR-002 / FR-003 | API | 未知模板和图谱失败 | 未知模板被拒绝；其他类型不会回滚 | AUTO | PASS |
| TC-004 | FR-004 / SRC-002 | integration | 文本知识任务 | 无 DataFlow 路径或依赖时 native 文本流程可完成 | AUTO | PASS |
| TC-005 | FR-005 / SRC-001 | API/build | 已删除入口 | DataFlow/Studio 路由为 404，主前端无 Studio 调用或导航 | AUTO | PASS |
| TC-006 | FR-006 / SRC-001 | integration | 历史清理 | 删除 DataFlow 关联数据与无引用 Blob，保留来源、native/LLM 记录及共享 Blob | AUTO | PASS |
| TC-007 | FR-001 至 FR-005 | browser/build | 模板向导 | 默认模板、6 个替代项、独立任务反馈和只读工作区可用 | MANUAL/AUTO | PASS (静态检查与构建) |
| TC-008 | FR-009 / SRC-003 | API | 默认模板切换 | 白名单模板成为唯一默认项，重启后仍保持；未知 ID 被拒绝 | AUTO | PASS |
| TC-009 | FR-007 / FR-008 | API/build | 调试台目录与多轮类型 | 仅保留目录入口；多轮对话库展示但没有模板或可执行流程 | AUTO | PASS |
| TC-010 | FR-007 / FR-010 / SRC-004 | build/static | 固定流程开发区布局 | 顶部为“业务工作区 / 流程开发区”，侧栏严格依次为知识类型、标准流程、模板、DataFlow 调试台；标准流程只读，调试台为待开发占位 | AUTO | PASS |
| TC-011 | FR-011 至 FR-013 / SRC-005 | API/build | 可复用文档库 | 创建、编辑和删除固定版本文档库；任务仅接收文档库；来源删除返回关联文档库 | AUTO | PASS |
| TC-012 | FR-014 / SRC-006 | unit/integration/build | 图谱结构化输出容错 | 非法实体/属性与依赖关系被保守剔除；没有有效三元组不发布；缓存复用告警；任务详情构建成功 | AUTO | PASS |

## Functional and Acceptance Cases

- 上传或选择来源，默认模板创建三项任务；选择任一模板只创建其声明类型。
- 成功类型进入对应知识库；失败类型保留任务详情和重试入口。

## Failure and Recovery Cases

- LLM 调用/格式校验失败时，批次内其他类型继续完成。
- 图谱 JSON 修复后仍有类型或字段违规时，仅保留严格合规事实；若没有事实，任务失败、可重试且无知识库。
- 重复运行清理不得删除来源或残留记录引用的 Blob。

## Security and Permission Cases

- 模板 ID 必须由后端白名单验证；不得通过 API 提交任意类型集合以绕过固定模板边界。

## Boundary, Concurrency, and Compatibility Cases

- DataFlow 清理记录已完成后可安全重复执行；不存在待删除记录时仍成功。
- 已移除 DataFlow 接口不保留兼容行为；保留的任务、知识库和溯源接口不回归。
- 文档库更新只影响后续任务；历史任务始终保留创建时来源版本。文档库成员仍存在时来源删除必须原子拒绝。

## Cross-Project and End-to-End Cases

- 前端仅使用模板目录和模板创建合同；“DataFlow 调试台”仅保留为只读名称，不包含 Studio iframe、DataFlow 请求或编排入口。

## Manual Product/Design Checks

- 当前默认模板需明确显示三类输出；组合模板名称准确；每张模板卡片显示设为默认或当前默认状态。多轮对话库需明确显示不能创建任务，目录不得暗示可自定义流程。文档管理需能从勾选源文档创建文档库，处理向导需仅显示文档库。顶部工作区和流程开发区四页名称、顺序需与 `AGENTS.md` 一致。

## Environment and Test Data

- `sun` Conda 环境、pytest、主前端依赖；模型成功路径可用假提取器测试，浏览器验收不要求真实 LLM 调用。

## Exit Criteria

- 后端回归和前端构建通过；TC-001 至 TC-006 自动通过；TC-007 完成静态/浏览器确认；Wiki 与发布文档同步。
