# DataForge V7 上线验收清单

更新日期：2026-08-12
适用范围：DataForge V7 的真实部署验收。此清单不授权迁移 V2、删除旧对象/Collection 或修改 `qa_agent`。

## 双图谱与受管 Collection（2026-08-13）

- [ ] `dataforge-provision` 创建/协调五个默认受管 Collection：`dataforge_text_knowledge`、`dataforge_qa_question`、`dataforge_qa_full`、`dataforge_graph_triple_knowledge`、`dataforge_graph_semantic_knowledge`，状态均为 `ready`。
- [ ] 创建相同 `storage_spec_hash` 的受管 Profile 时复用已有 Collection；改变字段、Embedding 修订、维度、Metric 或索引任一项时创建新 Collection；同名外部 Collection 被标记 `incompatible`。
- [ ] 分别完成 Triple/Semantic 的 insert、load、search、release 和 `kl_*` Partition 删除重试。
- [ ] 确认 `dataforge_graph_knowledge` 未改名、未迁移、未删除且新建图谱库不再引用它。
- [ ] 模板画布完成拖入、强类型连线、分支、many 合流、校验、样例运行与发布；环、孤立节点和任意代码节点均被拒绝。

## 进入条件

- [ ] 已提供 MySQL `dataforge`（空库或已处于 V7 revision）。
- [ ] 已提供 MinIO、Milvus、Embedding 的连接地址和部署侧凭据（`EMBEDDING_API_BASE`、`EMBEDDING_API_KEY`、`EMBEDDING_MODEL`、`EMBEDDING_DIM`、`EMBEDDING_BATCH_SIZE`）。
- [ ] API、worker、runner 具有 RoutingSnapshot volume 的正确读写/只读挂载。
- [ ] 三个目标项目、各自的 V7 文档和知识库授权已由业务负责人确认。
- [ ] 已保留旧 MySQL/MinIO/Milvus 的人工处置清单；本次不执行清理。
- [ ] Runner 镜像已验证 `open-dataflow==1.0.10`，且 API/Worker 未安装 Runner-only Runtime；MinerU 镜像已验证 `mineru==3.4.4`、Pipeline 模型齐全且未安装 vLLM；LLM 凭据仅经部署 Secret 注入。
- [ ] 服务器 `/data/zoe-ai-proj/global_llm` 可作为 Compose 的 `global_llm` 附加构建上下文；Runner 已用 `LOCAL_LLM_API_KEY` 成功导入共享包。
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
| 11 | 使用文本 PDF 与纯扫描 PDF 验证独立 MinerU GPU `pipeline + auto`，并复核 DOCX/CSV 原生路由 | PDF 均生成 Markdown、页级 SourceChunk、Middle JSON Artifact 和正式知识；不出现 VLM/vLLM/Flash/Router 或 OCR UI | 待填写 |
| 12 | 在非生产演练库执行 `--rebuild-v7 --confirm=REBUILD-V7` | 删除清单只含已登记 V7 对象和 `kl_` Partition；已发布 Collection、旧对象和旧资源仍存在 | 待填写 |
| 13 | 执行 `docker compose config`、构建 Runner 并运行 `python -c "import global_llm"` | 使用命名附加构建上下文；Runner 成功导入共享包，未从 DataForge 仓库复制同名副本 | 待填写 |
| 14 | 对多分块 Q&A/图谱/扩展类型注入一次 Qwen 失败后重试 | 成功分块保留/更新，失败分块保留历史；任务先为 `completed_with_warnings`，重试仅调用失败组合并清除告警 | 待填写 |
| 15 | 安装并运行 `dataforge-milvus-egress.timer`，再执行 `scripts/verify-milvus-egress.sh` | API/Worker 均经 `dataforge-milvus:19530` 列出 Collection；Worker 可完成 Embedding HTTPS，非白名单 `1.1.1.1:443` 被拒绝 | 待填写 |
| 16 | 重启 API/Worker 后创建新的向量同步任务 | 状态为 `ready`；只创建/写入目标 `kl_*` Partition，完成 load/search/release，未删除 Collection | 待填写 |
| 17 | 重建 `milvus-standalone-new`，立即运行 `systemctl start dataforge-milvus-egress.service` 并重复第 15、16 项 | 固定 `172.26.0.10` 和 `dataforge-milvus` 别名恢复，白名单规则刷新，向量同步仍成功 | 待填写 |
| 18 | 运行 `nvidia-smi` 与 CUDA 12.4 smoke test，构建 MinerU 镜像；分别从 Runner 和宿主机访问 health，再用局域网地址访问 `18000` | GPU 可用；Runner 的 `mineru-api:8000` 与宿主机 `127.0.0.1:18000` 成功，局域网地址被拒绝；GPU 0、并发 1、窗口 16 生效 | 待填写 |
| 19 | 停止 MinerU 后提交 PDF，再恢复服务并人工重试 | 首次仅产生一条保留原始 OCR 错误的失败记录且不写知识；恢复后重试成功 | 待填写 |

## 结论

| 项目 | 负责人 | 日期 | 结论 | 备注 |
| --- | --- | --- | --- | --- |
| 技术验收 | 待指定 | 待填写 | 待验收 |  |
| 业务验收 | 待指定 | 待填写 | 待验收 |  |
| 上线批准 | 待指定 | 待填写 | 待批准 |  |

完成后，将证据写入本文件，并回填 `V7-CAPABILITY-MATRIX.md` 中所有关联能力项的状态、负责人和验收记录。
