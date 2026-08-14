# 测试用例：V7 受控知识算子目录

**规格**：[spec.md](./spec.md)  
**计划**：[plan.md](./plan.md)  
**状态**：READY

## 覆盖矩阵

| ID | 要求 | 层级 | 场景 | 预期 | 自动化 |
|----|------|------|------|------|--------|
| TC-001 | FR-001 | unit | 资产草稿、修订、发布与任务快照 | 未发布依赖不可运行；快照不随之后编辑漂移 | AUTO |
| TC-002 | FR-002 | unit | P0/P1/禁用 Catalog 解析 | 仅 allowlist 可加入 P0 Flow，MinerU/Batch 被隐藏 | AUTO |
| TC-003 | FR-003 | integration | TXT、DOCX、CSV、PDF 路由到 DocumentIR | 统一结构与 SourceChunk anchor；OCR 缺失明确失败 | AUTO |
| TC-004 | FR-004 | unit | 环、递归、端口错配、未发布 Prompt、非法参数 | 发布/编译拒绝并给出节点级错误 | AUTO |
| TC-005 | FR-005 | integration | 多 Sink、无效 Candidate、质量 review | 失败 Sink 不写入；独立 Sink 可完成 | AUTO |
| TC-006 | FR-006 | e2e | 扩展类型、Prompt、Index Profile 与 LLM 一次修复 Gate | 仅合法 JSON 进入 Sink；Collection/字段映射从已发布 Profile 派生 | AUTO/Fake |
| TC-007 | FR-007 | integration | 重建命令 dry-run/confirm | 仅数据库登记的 V7 对象和 Partition 被处理；无 Collection 删除 | AUTO/Fake |
| TC-008 | FR-008 | HTTP/frontend | Catalog、子图、Canvas、Run 诊断 | 四页导航不变，兼容连线与诊断数据可见 | AUTO |
| TC-009 | FR-001～FR-008 | deployment | MySQL、MinIO、Milvus、Embedding、OCR 实机 | 完成上传、运行、Vector Ready、失败恢复 | MANUAL |
| TC-010 | FR-009 / SRC-004 | HTTP/frontend | 当前页全选、选中文件处理与重复请求 | 当前页选择独立；全部有效模板仅处理所选待更新版本；非本库、已处理、处理中或删除来源不会重复入队 | AUTO |
| TC-011 | 上传预检交互 | frontend | 拖入多文件或文件夹 | 预检逐项显示名称、相对路径、大小和冲突/格式/大小状态 | AUTO/build |
| TC-012 | FR-010 / SRC-005 | deployment | DataForge API/Worker 经专用网络访问本机独立 Milvus | Compose 仅为 API/Worker 增加外部网络；Milvus 通过 `dataforge-milvus:19530` 访问；防火墙仅放行 Milvus RPC 与 Embedding HTTPS；Milvus 重建后可恢复 | AUTO/static + MANUAL |
| TC-013 | FR-011 | integration | Triple/Semantic 模式、旧 Graph Profile 与专属 Profile | 新库冻结模式 Profile；旧 Collection 不改名、不迁移、不用于新库 | AUTO |
| TC-014 | FR-012 | unit/integration | Storage Contract 哈希、归属冲突、幂等供应 | 同规格复用；不兼容拒绝；失败可重试；没有 Collection 删除 | AUTO/Fake + MANUAL |
| TC-015 | FR-013 | unit/frontend | Flow DSL v3 分支、many 合流、孤立/环/非法 Sink 与拖拽画布 | 合法 DAG 发布；非法图拒绝；前端构建通过 | AUTO/build |
| TC-016 | FR-011/012 | deployment | 五个默认受管 Collection 实机供应，并对两个新 Graph Collection 执行 insert/load/search/release | 五个状态 ready；Triple/Semantic 各自可用，旧 Collection 无变化 | MANUAL |
| TC-017 | FR-013 | frontend | PC 固定三栏专业 DAG 画布、Typed Handle、端口连接、方向边、Palette、Inspector、MiniMap、自动布局、历史与校验定位 | 1440/1920 桌面视口可完整编排；旧 DSL/坐标可往返；非法连接与旧修订误操作被阻止 | AUTO/build + MANUAL |
| TC-018 | FR-014 | unit/integration/static | MinerU multipart、响应/错误/超时、文本/扫描 PDF Fake、Artifact 生命周期与 Compose 契约 | 参数/版本严格；Markdown、页级 SourceChunk 和 Artifact 正确；失败不写知识且原始错误不被覆盖；回环端口、GPU 0、并发 1、窗口 16正确且无禁用 Runtime | AUTO |
| TC-019 | FR-014 | deployment | CUDA 12.4、MinerU 3.4.4 Pipeline 模型、Runner/宿主机 health、局域网拒绝、真实文本/扫描 PDF与停止恢复 | 双路径访问成功、局域网拒绝；两类 PDF 完整入库；停止时仅一条失败且恢复后人工重试成功 | MANUAL |
| TC-020 | FR-015 | unit/HTTP/frontend | Catalog 说明/示例、幂等种子、MySQL 迁移、逐节点预览、分支失败、子图/Sink、截断和 Inspector 映射 | 新字段完整；预览无外部调用或持久化；节点数据与状态正确；前端测试和构建通过 | AUTO/build |

## 退出标准

- 所有 AUTO 用例通过；后端回归与前端构建通过。
- 静态审查确认无 `drop_collection`、无旧资源读取、无未受控 Prompt/Shell 节点。
- TC-009 记录为部署验收，不得以本地 Fake 替代真实签字。
- TC-019 必须在 NVIDIA Docker 主机留存证据，本地静态测试不能替代。
