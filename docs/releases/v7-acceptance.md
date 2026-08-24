# DataForge V7 上线验收清单

更新日期：2026-08-14
适用范围：DataForge V7 的真实部署验收。此清单不授权迁移 V2、删除旧对象、external/非受管 Collection 或修改 `qa_agent`；受管 Collection 仅可按本清单完成门禁演练。

## Knowledge Type、Profile 与受管 Collection（2026-08-14）

- [ ] `dataforge-provision` 创建/协调五个默认受管 Collection：`dataforge_text_knowledge`、`dataforge_qa_question`、`dataforge_qa_full`、`dataforge_graph_triple_knowledge`、`dataforge_graph_semantic_knowledge`，状态均为 `ready`。
- [ ] 创建相同 `storage_spec_hash` 的受管 Profile 默认得到独立 Collection；显式选择 ready 且兼容的受管登记才复用。改变字段、Embedding 修订、维度、Metric 或索引任一项时拒绝复用并创建新 Collection；同名外部 Collection 被标记 `incompatible`。
- [ ] 扩展 Type 自动产生 `<type-code>-default` Profile 与 `dataforge_<normalized-type-code>_knowledge`；Manual `create` Provision 新受管 Collection，Manual `attach` 只校验既有 external Collection，且缺失时绝不创建。
- [ ] 共享受管 Collection 的两个知识库分别只读写自己的 `kl_<library-id>` Partition；搜索、路由与知识库删除互不影响。
- [ ] 受管整库删除对当前 Profile/Type、知识库、模板、路由、运行任务、非 DataForge Partition 或 ownership/hash 不匹配逐项阻断；无引用且确认后异步完成，缺失整库幂等完成，失败可重试。external 全程没有整库删除操作。
- [ ] 分别完成 Triple/Semantic 的 insert、load、search、release 和 `kl_*` Partition 删除重试。
- [ ] 确认向量索引 API 保留 legacy `graph` Profile，并将其显示为“旧外部 Profile，不参与容量监控”；容量日志不探测 `dataforge_graph_knowledge`，新建图谱库只使用 Triple/Semantic Profile，且该外部 Collection 未被迁移、供应或删除。
- [ ] 模板画布完成拖入、强类型连线、分支、many 合流、校验、样例运行与发布；环、孤立节点和任意代码节点均被拒绝。

## 进入条件

- [ ] 已提供 MySQL `dataforge`（空库或已处于 V7 revision）。
- [ ] 已提供 MinIO、Milvus、Embedding 的连接地址和部署侧凭据（`EMBEDDING_API_BASE`、`EMBEDDING_API_KEY`、`EMBEDDING_MODEL`、`EMBEDDING_DIM`、`EMBEDDING_BATCH_SIZE`）。
- [ ] API、worker、runner 具有 RoutingSnapshot volume 的正确读写/只读挂载。
- [ ] 三个目标项目、各自的 V7 文档和知识库授权已由业务负责人确认。
- [ ] 已保留旧 MySQL/MinIO/Milvus 的人工处置清单；本次不执行清理。
- [ ] Runner 镜像已验证 `open-dataflow==1.0.10`，且 API/Worker 未安装 Runner-only Runtime；MinerU 镜像已验证 `mineru==3.4.4`、Pipeline 模型齐全且未安装 vLLM；LLM 凭据仅经部署 Secret 注入。
- [ ] Runner 镜像包含有效 `llm_servings.yaml`，默认 Serving ID 为 `qwen3_32b`；`LOCAL_LLM_API_KEY` 仅在实际调用时读取，配置检查不要求加载密钥。
- [ ] 同机独立 Milvus 容器名为 `milvus-standalone-new`，其当前 Compose 不由 DataForge 修改；`DATAFORGE_MILVUS_*` 已按 `wiki/pages/operations-and-testing.md` 配置且 `172.26.0.0/24` 未冲突。

## 验收步骤与证据

| 序号 | 操作 | 通过标准 | 证据链接或记录 |
| --- | --- | --- | --- |
| 1 | 在 `sun` 环境运行 V7 Alembic 升级与 schema check | 仅创建/升级 V7 schema，revision 正确 | 待填写 |
| 2 | 上传、替换并处理一份文档 | SourceVersion、来源锚点和可读 Diff 正确 | 待填写 |
| 3 | 在文档库绑定模板后运行文、问、图知识任务 | 首次创建自动结果库；后续只处理新增/替换版本，QA 按已发布 Profile 写入 | 待填写 |
| 4 | 执行向量同步、Partition load/search/release | 只操作该库的 `kl_` Partition，使用已发布 Collection/字段映射，容量状态可读 | 待填写 |
| 5 | 尝试删除被 Draft/已发布路由引用的库 | API 拒绝并列出引用 | 待填写 |
| 6 | 删除无引用库 | 异步任务仅删除对应 V7 Partition，库转为 `deleted`，Collection 保留 | 待填写 |
| 7 | 注入一次 Milvus、Embedding 和 runner 失败 | 状态/错误可见，重试或租约恢复可完成 | 待填写 |
| 8 | 为三个目标项目创建路由并发布 | 每个项目获得独立、可预览的 RoutingSnapshot | 待填写 |
| 9 | 复核版本 Diff、回滚和浏览器关键流程 | 回滚产生新版本，不改写历史 | 待填写 |
| 10 | 对文、问、图和一个已发布扩展类型运行受控 Flow | 每个任务有不可变执行快照；扩展类型 LLM 仅在 Schema 失败时修复一次，二次失败前不得写入 Knowledge Sink | 待填写 |
| 11 | 使用文本、纯扫描与混合 PDF 验证独立 MinerU GPU `pipeline + auto`，并复核 DOCX/CSV 原生路由 | 三类 PDF 均生成 Markdown、页级 SourceChunk、Middle JSON Artifact 和正式知识；不出现 VLM/vLLM/Flash/Router、扫描件检测算子或 OCR UI | 待填写 |
| 12 | 在非生产演练库执行 `--rebuild-v7 --confirm=REBUILD-V7` | 删除清单只含已登记 V7 对象和 `kl_` Partition；已发布 Collection、旧对象和旧资源仍存在 | 待填写 |
| 13 | 执行 `docker compose config`、构建 Runner，并在容器内加载 Serving Registry、输出默认 Serving ID | Registry 在不读取密钥的情况下通过启动校验并输出 `qwen3_32b`；镜像不依赖 `global_llm` 附加构建上下文 | 待填写 |
| 14 | 对多分块 Q&A/图谱/扩展类型注入一次 Serving 超时后重试，并使用 `srcv_b23e17585e1cdcbf1127751c90f29daf` 三分块做真实 QA | 单次分块尝试只增加一次 `attempt_count`；成功分块保留/更新，失败分块保留历史；任务先为 `completed_with_warnings`，重试仅调用失败组合并清除告警；真实调用使用快照中的同一 Serving ID | 待填写 |
| 15 | 安装并运行 `dataforge-milvus-egress.timer`，再执行 `scripts/verify-milvus-egress.sh` | API/Worker 均经 `dataforge-milvus:19530` 列出 Collection；Worker 可完成 Embedding HTTPS，非白名单 `1.1.1.1:443` 被拒绝 | 待填写 |
| 16 | 重启 API/Worker 后创建新的向量同步任务 | 状态为 `ready`；只创建/写入目标 `kl_*` Partition，完成 load/search/release，未删除 Collection | 待填写 |
| 17 | 重建 `milvus-standalone-new`，立即运行 `systemctl start dataforge-milvus-egress.service` 并重复第 15、16 项 | 固定 `172.26.0.10` 和 `dataforge-milvus` 别名恢复，白名单规则刷新，向量同步仍成功 | 待填写 |
| 18 | 运行 `nvidia-smi` 与 CUDA 12.4 smoke test，构建 MinerU 镜像；分别从 Runner 和宿主机访问 health，再用局域网地址访问 `18000` | GPU 可用；Runner 的 `mineru-api:8000` 与宿主机 `127.0.0.1:18000` 成功，局域网地址被拒绝；GPU 0、并发 1、窗口 16 生效 | 待填写 |
| 19 | 停止 MinerU 后提交 PDF，再恢复服务并人工重试 | 首次仅产生一条保留原始 PDF 解析错误的失败记录且不写知识；恢复后重试成功 | 待填写 |
| 20 | 保持两个派生运行开关关闭，检查算子目录、Mini DAG、子图 revision 和历史 Runtime DAG | 九类动态数量正确；内部配置不泄漏；历史 Run 不变且成功/失败/跳过/复用状态可读 | 待填写 |
| 21 | 只开启 `DATAFORGE_DERIVED_RUNS_ENABLED`，对真实节点分别执行 `node_only` 与 `from_node` | HTTP 仅返回 queued；Worker/Runner 异步执行；前者不跑下游，后者只跑可达下游且 Merge 复用边界 Artifact | 待填写 |
| 22 | 删除、篡改或标记一个父 Run Artifact 不可重放，再提交派生请求 | 请求直接拒绝，不隐式完整重跑；父 Run、快照和模板不变 | 待填写 |
| 23 | 对 PDF Document Parser 执行一次 `force_ocr`，并对非 PDF 重复 | PDF 保持 MinerU Pipeline/中文配置但强制 OCR；非 PDF 被拒绝；覆盖不写入已发布模板 | 待填写 |
| 24 | 让派生 Run 到达一个及多个 Knowledge Sink | 仅生成候选、质量和 Diff，状态为 `awaiting_commit`；正式知识与向量任务均未变化；各 Sink 独立可见 | 待填写 |
| 25 | 开启 `DATAFORGE_DERIVED_RUN_COMMIT_ENABLED`，测试确认、重复幂等键、目标态漂移和多 Sink 单独提交 | checksum 匹配才提交；重复请求幂等；漂移返回 409；每个成功 Sink 独立写知识并排队向量同步，失败 Sink 不回滚其他 Sink | 待填写 |
| 26 | 创建并发布一个扩展 Type，再分别创建 Manual `create` 与 `attach` Profile | 自动/Manual create Collection 带正确 ownership marker 且为 ready；attach 只验证客户既有 Collection | 待填写 |
| 27 | 对相同 Contract 分别测试默认创建和显式复用 | 默认 Collection ID 不同；显式兼容复用成功；异构 Contract 复用被拒绝 | 待填写 |
| 28 | 在共享受管 Collection 的两个知识库写入、搜索、发布路由并删除其中一个库 | 全程使用不同 `kl_*` Partition；另一个库的数据、搜索和路由不受影响 | 待填写 |
| 29 | 对仍有引用、客户 Partition 或被篡改 marker 的受管 Collection 申请删除 | 预检和 Worker 二次预检均拒绝，Milvus 未收到 `drop_collection` | 待填写 |
| 30 | 归档无引用 Profile 后删除 DataForge-owned 演练 Collection，并测试缺失与失败重试 | 明确确认后异步删除并登记 `deleted`；缺失幂等完成；失败原因可见且可重试 | 待填写 |
| 31 | 在工作台分别检查 MySQL/MinIO/disk/Worker/Runner，再全选九项 | 单项、多选、全选均产生一个持久 run；逐项独立完成；未点击前无外部探针调用 | 待填写 |
| 32 | 停止 Worker 与 Runner，等待 45 秒后刷新，再恢复服务 | 对应 heartbeat 变 stale/unavailable；queued 任务显示带证据的可能原因；恢复后新心跳转绿 | 待填写 |
| 33 | 分别注入 MinerU、LLM、Embedding、Milvus 故障并手动检查 | 真实最小探针失败、错误脱敏、其他选中项继续完成；业务任务状态不被检查器改变 | 待填写 |
| 34 | 匿名读取 `/api/health`，管理员读取组件详单，等待检查结果超过 15 分钟 | 匿名无 endpoint/实例/任务/错误原文；管理员可见详单；过期结果保留 last_status 但聚合为 unknown | 待填写 |

## 结论

| 项目 | 负责人 | 日期 | 结论 | 备注 |
| --- | --- | --- | --- | --- |
| 技术验收 | 待指定 | 待填写 | 待验收 |  |
| 业务验收 | 待指定 | 待填写 | 待验收 |  |
| 上线批准 | 待指定 | 待填写 | 待批准 |  |

完成后，将证据写入本文件，并回填 `V7-CAPABILITY-MATRIX.md` 中所有关联能力项的状态、负责人和验收记录。
