# Feature Specification: 文档知识生产链路

**Feature Directory**: `specs/003-dataflow-governed-integration`
**Status**: Approved for implementation
**Artifact Language**: zh-CN

## Product intent

DataForge 从医疗模板和 SQLite/Gateway 集成切换为通用文档知识生产平台。用户上传 PDF、CSV、Markdown、Word 或 TXT 后，系统以一个固定的流程版本生成文本知识、问答知识和知识图谱，并提供进度、结果与来源查看。

## Requirements

- **FR-001**: 顶部工作区固定为“业务工作区 / 流程开发区”；开发区菜单固定为“知识类型 / 标准流程 / 模板 / DataFlow 调试台”。
- **FR-002**: 文档库、目录、文档、标签、替换和永久删除使用 MySQL 元数据与 MinIO 对象；上传支持批量、六种格式与 200 MB 限制。
- **FR-003**: 平台预置五条只读流程，流程版本声明支持的 text、qa、graph 产出；仅可编辑复制出的模板。
- **FR-004**: 已发布、校验和样例均通过的知识生产流程可设为唯一默认流程；新建和重新生成任务固定绑定当时的流程版本。
- **FR-005**: 独立 `dataflow-runner` 执行解析、清洗、切片和知识分支；未请求分支为 `skipped`，各分支可独立重试。
- **FR-006**: Worker 使用 MySQL 租约、心跳和幂等运行 ID 恢复任务；API 提供可续读 SSE 事件。
- **FR-007**: 全站保留单管理员会话与审计；Runner 只接受私网服务凭据。
- **FR-008**: 不提供 OCR、Excel/数据库输入、多轮对话、Harness、Skills、MCP、Agent 或上游 WebUI iframe。

## Success criteria

1. 默认综合流程能从任一支持格式的文档生成三类知识，并保留流程版本和文档来源。
2. 切换默认流程后，新的上传选项、卡片排序和新任务立即反映变化，历史任务不变。
3. `frontend`、API、Worker、Runner、MySQL 和 MinIO 在 Compose 私有网络中可启动；前端构建与后端自动测试通过。

## Migration decision

旧 SQLite/Blob 在切换窗口做带校验清单的只读归档，不迁移到新系统；新 MySQL 和 MinIO 从空状态与内置流程种子数据启动。
