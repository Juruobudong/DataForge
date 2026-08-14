# 实施计划：V7 受控知识算子目录

**日期**：2026-08-11 | **规格**：[spec.md](./spec.md)  
**测试用例**：[test-cases.md](./test-cases.md)

## 摘要

在 `src/dataforge/v7/` 内建立版本化治理域与执行适配层。Flow Draft 使用类型化 DAG；发布编译为包含 DataFlow Adapter Version、知识类型/Prompt/质量修订的不可变 Snapshot；Runner 只执行 Snapshot。现有平铺模块以兼容包装保留公共命令入口，业务工作区继续使用知识库、向量和路由能力。

## 依据

### 复用组件

- `src/dataforge/v7/models.py`：既有 SQLAlchemy V7 模型与当前态知识。
- `src/dataforge/v7/store.py`：事务、任务、知识写入、向量和路由服务。
- `src/dataforge/v7/vector.py`：已发布 Collection 与 `kl_` Partition 安全边界。
- `src/dataforge/v7/runner.py`：Runner HTTP 边界和对象存储读取。
- `frontend/src/components/GraphBrowser.vue`：已有 Vue Flow 使用方式。

### 已验证约束

- Python、uv、pytest 均通过 Conda `sun` 运行。
- MySQL/MinIO 仍命名为 `dataforge`；只可删除数据库已登记的 V7 对象和 `kl_` Partition，禁止 `drop_collection`。
- 开发区导航固定四页；Catalog/子图必须置于既有页面内部。

## 技术设计

### 领域、存储与迁移

- 将模型拆入 `src/dataforge/v7/domain/`，但保留 `models.py` 的可导入兼容导出；增加知识类型、算子、Prompt、质量、子图、Flow、Snapshot、Run、Artifact/Lineage 的定义与修订表。
- 新增类型和 Index Profile 修订、文档模板绑定、结果库映射、处理记录和向量删除任务；类型修订冻结已发布 Profile。
- 为任务增加 `execution_snapshot_id` 和 `sink_library_ids`；在 V7 重建后不保留旧任务/模板兼容数据。
- 新增 Alembic head revision 和显式 `--reset-v7-data --confirm=REBUILD-V7`：先从数据库读取精确对象键和 Partition，再按允许范围删除、清空 V7 行、升级与播种；默认及 Compose 启动路径不执行重建。

### Catalog、DAG 与运行时

- 在 `domain/flow` 定义 Artifact Type、节点、边、校验器、子图展开器与确定性拓扑编译器。
- 在 `integrations/dataflow/` 定义 Catalog、Adapter 与 Runtime Profile；P0 逻辑节点映射白名单 DataFlow 实现，MinerU/Batch 仅作为隐藏能力。
- Document Parser 根据类型路由 PDF、DOC/DOCX、MD/TXT、CSV 至 `DocumentIR`；PDF 内部委派给固定 `pipeline + auto` 的 MinerU Parser，解析依赖缺失时显式失败。
- 通过 `KnowledgeSink` 完成 Schema、Source Binding、Quality、Canonical、Diff 和独立 Sink 事务；Candidate 内部二次切分不改变 `SourceChunk`。
- Runner Docker target 通过独立 extra 安装 `open-dataflow==1.0.10` 与 `jsonschema`；本地 Adapter 可在未安装重型依赖时使用安全 Fake，真实 Adapter 仅在 Runner 启用。

### API 与前端

- 扩展 `/api/developer/`：类型修订、Catalog、Prompt、质量、子图、Flow、Snapshot 和 Run；任务请求改为按 Sink 绑定目标知识库。
- 保持四个侧栏路由。知识类型页提供类型生命周期；标准流水线页展示内置子图；模板页实现 Catalog/子图/Canvas 标签；调试台显示快照、节点、质量和血缘。
- 文档库文件列表新增当前页选择与“处理选中文件”；`POST /api/document-libraries/{id}/process-selected` 验证文件归属，并对选中来源的当前版本与每个有效绑定的待处理集合取交集后入队。上传预检逐项显示拖入文件的名称、路径、大小和冲突/格式/大小状态。整库 `/process` 行为保持不变，无需 schema 迁移。
- 对本机独立 Milvus 容器，定义 `dataforge_milvus_egress` 外部可附加网络（`172.26.0.0/24`），以固定 IP `172.26.0.10` 和别名 `dataforge-milvus` 临时接入 `milvus-standalone-new`；API/Worker 保留 `private` 并同时接入该网络。以 systemd 定时脚本恢复连接、刷新 `DOCKER-USER` 最小出站规则，仅允许 Milvus `19530` 和当前 Embedding HTTPS 域名。

## 实施顺序

1. 模型、迁移、Catalog/DSL、种子与重建命令。
2. 编译器、DataFlow Adapter、Runner 和 text/qa/graph 内置子图。
3. 三类种子、扩展类型、自动结果库、API、前端页面和运行诊断。
4. 单元、HTTP、Runner、前端构建、Wiki/能力矩阵/验收文档。

## 风险与控制

| 风险 | 控制 |
|------|------|
| 上游 Operator API 漂移 | Catalog 仅持有 DataForge Adapter Version；Runner pin 1.0.10。 |
| 破坏性 V7 重建 | 精确枚举、确认参数、服务维护窗口、测试无 Collection/旧资源访问。 |
| MinerU/LLM 环境缺失 | 本地 Fake 验证接口；真实运行失败透明并列入部署验收。 |
| 正式知识污染 | Sink 之前拒绝未发布依赖、无效 Schema、来源/质量失败。 |
| 未受管 Milvus 容器重建 | systemd 每 5 分钟检测并恢复专用网络、固定地址、DNS 别名和防火墙规则；实际 Milvus Compose 后续纳管前维持此运维约束。 |

## 验证

```powershell
conda activate sun
uv run --extra web --with pytest pytest -q tests/test_v7_platform.py
cd frontend; npm run build
```

真实 MySQL/MinIO/Milvus/Embedding/OCR 验收依照 `docs/releases/v7-acceptance.md` 执行。

## 文档影响

- 更新 Wiki 的项目概览、架构、领域、流程、API、前端、运行、路线图与日志。
- 更新 `V7-CAPABILITY-MATRIX.md` 和发布验收清单；登记 DataFlow 版本来源快照。

## 2026-08-13 增量设计

- Graph 保持顶层 `graph`，新增 `triple / semantic` 模式修订和两个专属受管 Collection；旧 Graph 兼容随后由 2026-08-14 增量设计移除。
- Index Profile 通过版本化 Storage Contract 与 `storage_spec_hash` 决定 Collection 复用，独立 Provisioner 负责幂等创建与归属校验。
- Flow DSL 升级到 v3 显式端口与基数，前端开放受控拖拽、分支和合流，继续禁止任意代码节点。

## 2026-08-13 PDF GPU OCR 增量设计

- 新增独立 MinerU 3.4.4 GPU 镜像，只安装和下载 Pipeline Runtime/模型；Compose 固定 GPU 0、并发 1、窗口 16，并以回环端口向宿主机内部服务提供访问。
- Runner 新增同步 MinerU Adapter，所有 PDF 固定提交 `pipeline + auto + ch`；Markdown 写入 DocumentIR，Content List 生成页级 SourceChunk，Middle JSON 使用确定性 MinIO 对象键及 `source_version_id` Artifact 登记。
- 文件删除、V7 重建和对象写入补偿均只使用数据库登记键；MinerU PDF 解析失败由 Runner 一次性持久化，Worker 不覆盖原始错误。MinerU 与 Runner 默认超时分别为 1800/1860 秒。
- 本地以 Adapter/Runner/生命周期/Compose 静态测试验收；CUDA、模型、真实文本/扫描 PDF、回环访问和局域网拒绝在部署服务器验收。

## 2026-08-14 算子说明与节点预览增量设计

- Operator Definition 保存简短中文功能说明，Operator Version 冻结端口化输入/输出 JSON 示例；Alembic 对已有 MySQL JSON 列采用可空新增、回填、再收紧非空的兼容升级。
- 样例接口复用编译器的展开 DAG，但使用独立的确定性内存预览处理器；禁止调用生产 `_run_operator`、LLM、MinerU、对象存储和 Store 写入。
- 响应按画布节点聚合端口数据，并保留展开节点与子图内部轨迹；每端口最多 3 条、字符串最多 500 字符。Inspector 仅消费当前节点数据。

## 2026-08-14 旧 Graph Profile 兼容与容量跳过设计

- 空库种子创建五个受管 Profile，并额外保留 external legacy `graph → dataforge_graph_knowledge` Profile；Graph revision 1 绑定 legacy Profile，revision 2 包含 Triple/Semantic 模式并绑定两个专属 Profile。
- 新建 Graph 库按 `graph-{graph_mode}` 解析 Profile；已有库若冻结到 legacy `graph` 则继续使用原 Profile。容量报告按稳定 code 跳过 legacy Profile并返回明确原因，其他 external Profile 不受影响。
- 不新增数据清理迁移；部署测试环境按标准空卷重建。外部遗留 Collection 不由 Provisioner 供应、不迁移也不删除；仅旧库真正同步时按 external 契约要求其存在。

## 2026-08-14 Document Parser 边界增量设计

- Canvas 继续只暴露稳定 `Document Parser`，MinerU Adapter 保持内部；不增加扫描件检测、公开 PDF Parser 或 Image Parser。
- PDF 固定使用 MinerU 3.4.4 `backend=pipeline`、`parse_method=auto`；Flow 发布要求 `document-parser.params` 为空对象，Inspector 只读展示固定契约。
- 原生 DOC/DOCX、MD/TXT、CSV/XLSX 路由、上传白名单、DocumentIR、Artifact 和快照保持不变；本次无数据库迁移，图片留待独立视觉模型 Parser。
