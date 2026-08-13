# Feature Specification: 下线 DataFlow，改用内置医疗标准模板

**Feature Directory**: `specs/002-native-medical-templates`
**Created**: 2026-08-02
**Status**: Approved for implementation
**Artifact Language**: zh-CN
**Requirement Intake**: [intake.md](./intake.md)

## Requirement Sources

| Source ID | Contribution | Requirements/Scenarios |
|-----------|--------------|------------------------|
| SRC-001 | 用户确认的产品范围与交互决策 | FR-001 至 FR-006、全部场景 |
| SRC-002 | 当前 DataForge 代码与 Wiki | 现有行为、迁移和回归范围 |
| SRC-003 | 用户确认的界面与默认项调整计划 | FR-007 至 FR-009、目录与默认项场景 |
| SRC-004 | 用户确认的流程开发区布局计划 | FR-007、FR-010、固定页面顺序场景 |
| SRC-005 | 用户确认的可复用文档库设计 | FR-011 至 FR-013、文档库与任务场景 |

## Existing Project Context

### Verified Current Behavior

- `DataFlowEngine`、`dataflow_studio.py` 和前端 Studio 页面共同提供 DataFlow 调试能力；根目录的 `dataflow/` 当前不存在。 — Evidence: `src/dataforge/processing/engine.py`、`src/dataforge/dataflow_studio.py`、`frontend/src/App.vue`
- 文本、问答和图谱知识生产已分别有 native、FAQ LLM 和图谱 LLM 的执行路径；批量接口会为每种类型创建独立任务。 — Evidence: `src/dataforge/knowledge.py`
- `standard_flows`、草稿和版本表只服务于 DataFlow Studio 快照；单类型 `standard_pipelines` 是现有知识任务的稳定引用。 — Evidence: `src/dataforge/database.py`

### Relevant Projects and Modules

| Project/Module | Existing Responsibility | Relevance | Evidence |
|----------------|-------------------------|-----------|----------|
| 后端 | 处理、标准流程、知识任务、API 与迁移 | 移除 DataFlow，提供固定模板与历史清理 | `src/dataforge/` |
| 前端 | 业务任务向导和开发工作区 | 展示 7 个模板、保留只读 DataFlow 调试台名称 | `frontend/src/` |
| 文档与依赖 | 运行说明、产品边界、锁文件 | 去除 DataFlow 依赖与当前能力陈述 | `wiki/`、`README.md`、`pyproject.toml` |

### Inferences and Unknowns

- **Inference**: 固定模板由后端定义和验证，前端仅显示目录并提交模板 ID。
- **Unknown**: 无。

## User Scenarios and Testing

### User Story 1 - 选择内置医疗模板生产知识 (Priority: P1)

业务用户选择文档与一个模板。首次默认的“医疗模板”产出三种知识库，其他六个模板产出其命名的单项或组合类型；每项在独立任务中可追溯、可重试。维护人员可将任何固定模板设为全局默认项。

**Independent Test**: 查询模板目录并以默认模板、单项模板和两项模板分别创建任务，核对任务类型集合和独立完成状态。

**Acceptance Scenarios**:

1. **Given** 三项内置流程可用，**When** 用户选择默认医疗模板，**Then** 系统创建文本块、问答和图谱三项独立任务。
2. **Given** 用户选择“文本知识库 + 知识图谱模板”，**When** 创建任务，**Then** 只创建文本块和图谱任务。
3. **Given** 图谱任务失败而文本任务成功，**When** 批次结束，**Then** 文本知识库可用，图谱任务保留失败与重试信息。

### User Story 2 - 维护简化且不遗留 DataFlow (Priority: P1)

维护人员启动升级后的系统后，在固定的“流程开发区”中依次看到知识类型、标准流程、模板和标为待开发的 DataFlow 调试台；不会看到 DataFlow/Studio 编排入口或 API；旧的 DataFlow 派生产物已删除，但原始文件仍可供新模板使用。

**Independent Test**: 用含 DataFlow 与非 DataFlow 记录的数据库夹具运行清理，核对精确删除范围；检查已移除路由返回 404。

### User Story 3 - 复用固定来源的文档库 (Priority: P1)

业务用户在文档管理中选择部分源文档并命名保存为文档库。后续创建处理任务时只选择文档库；任务使用文档库固定的来源版本，文档更新不会静默改变既有文档库或任务。

**Independent Test**: 创建包含一份来源版本的文档库，上传该来源的新版本，核对旧文档库任务仍处理旧版本；显式更新文档库后，新任务处理新版本；删除被文档库引用的来源被拒绝。

## Scope and Impact

### In Scope

- 下线 DataFlow 引擎、Studio、第三方 WebUI、可编辑流程草稿和所有专用 API。
- 固定 7 个模板：文本、问答、图谱、文本+问答、文本+图谱、问答+图谱、文本+问答+图谱。
- 默认模板、模板驱动批量任务、只读 DataFlow 调试台目录、历史 DataFlow 清理和文档同步。
- 文档库的创建、查看、编辑、删除、固定版本成员和来源删除保护。
- 图谱结构化输出的保守过滤、可操作诊断与缓存告警复用。

### Out of Scope

- 通用算子编排、自定义知识类型或自定义流程发布。
- 向量索引、检索、OCR、数据库来源和下一期之外的新流程能力。

### Expected Project Impact

- **后端**: 模板目录与创建合同替代 DataFlow/标准流快照合同；文本处理固定使用 native。
- **前端**: 任务向导改为模板选择，开发区只读展示内置三类与模板。
- **存储**: 清除 DataFlow 特有历史及无引用 Blob，不删除来源与非 DataFlow 结果。

### Cross-Project Behavior

后端先提供模板目录与模板创建合同并完成旧数据清理，前端随后消费新合同。已删除 DataFlow 路由不保持兼容；任务、知识库和溯源合同保持可用。

## Requirements

### Functional Requirements

- **FR-001**: 系统必须仅公开 7 个固定模板，首次将“文本+问答+图谱”的“医疗模板”标记为默认。 — Sources: SRC-001, SRC-003
- **FR-002**: 系统必须由模板 ID 在服务端确定目标类型和内置流程；未知模板或非内置类型请求必须被拒绝。 — Sources: SRC-001
- **FR-003**: 组合模板必须为每个类型创建独立任务；任一 LLM/校验失败不得回滚其他类型的成功结果。 — Sources: SRC-001
- **FR-004**: 文本知识流程必须不依赖 DataFlow；问答和图谱模板必须安装后可选。 — Sources: SRC-001, SRC-002
- **FR-005**: 系统必须移除 DataFlow/Studio 的运行入口、依赖、代码和 API，并将类型/标准流程配置收敛为只读内置目录。 — Sources: SRC-001
- **FR-006**: 升级清理必须删除 DataFlow 配置、任务和派生产物，保留来源、版本以及 native/LLM 历史结果，并只移除无引用 Blob。 — Sources: SRC-001
- **FR-007**: 流程开发区必须提供只读的知识类型、标准流程和模板页面；DataFlow 调试台必须标为待开发，不恢复 Studio、iframe、通用编排或执行引擎。 — Sources: SRC-003, SRC-004
- **FR-008**: 多轮对话库必须保留为只读类型卡片，明确无内置模板且不能创建任务。 — Sources: SRC-003
- **FR-009**: 任一固定模板必须可经服务端白名单验证设为全局持久默认项；模板目录必须仅标记一个默认项，任务向导必须预选它。 — Sources: SRC-003
- **FR-010**: 顶部工作区名称必须固定为“业务工作区”和“流程开发区”；流程开发区页面顺序必须固定为“知识类型 → 标准流程 → 模板 → DataFlow 调试台”，不得改名、合并或替换。 — Sources: SRC-004
- **FR-011**: 文档管理必须支持选择部分或全部源文档并命名创建、查看、编辑和删除文档库；文档库名称在工作区内唯一且成员不可为空。 — Sources: SRC-005
- **FR-012**: 文档库必须保存每个成员的具体 `source_version`，每个逻辑源文档最多一个成员版本；只有用户显式编辑时才可改变成员或更新到最新版本。 — Sources: SRC-005
- **FR-013**: 批量知识任务必须仅接收 `document_library_id` 并展开成员版本；来源仍被文档库引用时必须拒绝删除并返回关联文档库。 — Sources: SRC-005
- **FR-014**: 图谱模型输出已是可解析 JSON、但含无法可靠归类的实体、关系或属性时，系统必须仅发布其余完整合规的三元组，并在任务验证信息显示被剔除项目的数量、名称和原因；若没有有效三元组则不发布。 — Sources: SRC-006

### Key Entities

- **模板**: 固定 ID、名称、目标知识类型集合与默认标记；一个模板映射到一至三个独立知识任务。
- **默认模板设置**: 数据库持久化的唯一全局模板 ID；首次值为三项全量“医疗模板”。
- **内置标准流程**: 三种不可编辑的单类型执行定义，供模板组合引用。
- **文档库**: 唯一名称与有序的来源版本成员集合；可删除，但不会改变已创建任务的来源版本快照。

## Edge Cases

- 图谱或问答模型不可用时，仅对应任务失败并可重试。
- 模板包含图谱时，实体消歧沿用当前默认启用行为。
- 默认设置包含未知 ID 时，服务端安全回退至首次默认的医疗模板。
- 历史清理失败不得删除来源；数据库删除与 Blob 引用核对必须可重复安全执行。
- 试图创建空文档库、重复名称、未知版本或同一来源的多个版本时必须被拒绝。
- 图谱 JSON 无法解析、疑似长度截断或清洗后没有有效三元组时，任务保持可重试失败，不创建部分知识库。

## Success Criteria

- **SC-001**: 模板目录仅返回 7 项且唯一默认项明确；默认模板请求返回对应的独立任务，其他六个模板的类型集合准确。
- **SC-002**: 安装与主前端不再包含 DataFlow/Studio 入口或依赖，文本任务可在无 DataFlow 环境完成。
- **SC-003**: 清理后 DataFlow 特有数据不可查询，来源与非 DataFlow 知识结果仍可查询。
- **SC-004**: 文档库在来源更新后仍使用固定版本；更新文档库仅影响后续任务，引用中的来源不可删除。

## Assumptions

- `llm_local.yaml` 已正确配置时，问答与图谱可执行；运行时异常以单项任务失败反馈。
- 用户已授权永久删除 DataFlow 派生产物，并明确要求保留源文档。
- 多轮对话库只保留契约目录，未来是否提供模板或任务入口不在本期范围。

## Dependencies

- FastAPI、Vue/Vite、SQLite、现有全局 LLM 网关；不再依赖 OpenDCAI DataFlow。
