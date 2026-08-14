# 功能摘要：V7 受控知识算子目录

**阶段**：IMPLEMENTATION  
**工件语言**：zh-CN  
**工件就绪度**：READY
**交付就绪度**：DEPLOYMENT_ACCEPTANCE_PENDING
**人工门禁**：APPROVED  
**更新日期**：2026-08-14

## 变更内容

将 V7 收敛为文本、问答、图谱三类内置知识，并实现可发布扩展类型、动态 Index Profile、文档库模板绑定和自动结果知识库处理；后续已加入 DataForge Model Serving Registry 驱动的分块结构化生成、失败分块持久化/局部重试、统一 OpenAI-like Embedding 和当前页选中文件定向处理。Flow 统一保存 `llm_serving` ID，默认 `qwen3_32b`，模型名和连接信息保持为 Runner 内部配置。

## 保持不变

- 管理员指定的既有 Collection、`kl_` Partition、RoutingSnapshot 和既有业务工作区。
- 不读取、迁移或删除 V2、旧 MinIO/Milvus 资源；不修改 `qa_agent`。
- 流程开发区保持知识类型、标准流水线、知识流程模板、DataFlow 调试台四页。

## 已确认决定

| 项目 | 类型 | 所有人 | 状态 | 决定 |
|------|------|--------|------|------|
| 交付范围 | DECISION | USER | CONFIRMED | 三类内置知识、受控扩展、自动结果库与动态 Profile。 |
| 数据处置 | DECISION | USER | CONFIRMED | 直接重建当前 V7 数据，不导出快照。 |
| 执行边界 | DECISION | USER | CONFIRMED | 独立 Runner，固定 `open-dataflow==1.0.10`。 |
| Catalog | DECISION | USER | CONFIRMED | 仅 P0 逻辑节点公开，P1 受控，其余禁用。 |
| 文档库定向处理 | DECISION | USER | CONFIRMED | 全选仅作用当前页，按全部有效绑定处理选中文件中待更新的当前版本，保留整库入口。 |
| 同机 Milvus 网络 | DECISION | USER | CONFIRMED | 保留 `private`，通过外部 `dataforge_milvus_egress` 临时接入独立 Milvus，固定别名/地址并以最小出站白名单保护。 |
| 双图谱与存储 | DECISION | USER | CONFIRMED | 顶层保持 graph；Triple/Semantic 使用专属 Profile/Collection；保留旧 `graph` Profile 与冻结兼容，但容量诊断跳过其外部 Collection。 |
| 流程画布 | DECISION | USER | CONFIRMED | Flow DSL v3 采用白名单、强类型端口、受控分支/合流与 Knowledge Sink 终点，不开放任意代码。 |
| PDF GPU OCR | DECISION | USER | CONFIRMED | 所有 PDF 固定 MinerU 3.4.4 `pipeline + auto`；Runner 内网调用，宿主机内部服务仅回环调用，不接 VLM/vLLM/Flash/Router 或 OCR UI。 |
| 算子说明与预览 | DECISION | USER | CONFIRMED | 算子展示中文功能说明；Inspector 同时显示版本化典型示例与不调用外部服务的逐节点受控内存预览。 |
| Document Parser 边界 | DECISION | USER | CONFIRMED | Canvas 只暴露 Document Parser；PDF 固定 MinerU `pipeline + auto`且不接受参数；不设扫描件检测算子，Image Parser 以后绑定视觉模型单独设计。 |

## 主要风险

- 真实 MinerU GPU/CUDA、LLM/Milvus 环境未提供；本地以 Adapter Fake/静态契约验证，真实集成保持部署验收项。
- V7 重建是破坏性操作；命令需精确清单和确认参数，测试证明不触碰旧资源或 Collection。
- Milvus 由另一份 Compose 管理，容器重建会失去临时网络端点；`dataforge-milvus-egress.timer` 负责恢复。基础网络/白名单验证已通过，但容器重建恢复及新 Collection 实机供应仍待验收。

## 实施与验证

- **实施**：23 项仓库任务完成，包括 T025 Document Parser 边界；Canvas 只保留稳定 Parser，PDF 固定 MinerU 3.4.4 `pipeline + auto`，非空参数发布被拒绝，Inspector 改为只读展示。
- **画布增量**：T021 已完成；知识流程模板现为 PC 固定三栏专业 DAG 编辑器，包含 Typed Handle、自定义方向边、Palette、Inspector、MiniMap、LR 自动布局、事务历史和结构化问题定位，未改后端 DSL 或数据库。
- **验证**：Conda `sun` 下本次相关 V7 回归 60 passed（1 条第三方警告）；MinerU Adapter、文本/扫描 PDF Runner、Artifact 补偿与删除生命周期、Worker 错误保真和 Compose 静态契约通过。全仓为 78 passed、1 skipped、1 个既有 `/studio/` 占位文案断言失败；当前 Windows 工作区不运行 Docker，真实 GPU/CUDA/模型、文本/扫描 PDF、回环访问与服务恢复仍待部署主机执行。
- **本次画布验证**：Node 前端逻辑测试 8 passed，Vite 生产构建通过；1440×900 与 1920×1080 真实浏览器验收通过。先前并行变更出现的 3 个后端失败已修复并纳入本次 60 passed 回归。
- **本次算子预览验证**：V7 回归 63 passed、前端逻辑测试 8 passed、Vite 生产构建通过；预览路径不调用 LLM、MinerU、对象存储或 Store 写入。
- **旧 Graph Profile 兼容**：T026 已按最新决定调整；保留 legacy Profile 与旧库冻结选择，容量报告返回未监控原因且不调用旧 Collection，其他 external Profile仍接受探测。定向回归 5 passed；当前-schema 临时空库的种子/兼容/external/API 集成检查通过；前端逻辑测试 12 passed且 Vite build 通过。
- **本次验证边界**：三文件扩展 pytest 在并行新增的未提交 `20260814_02_flow_workbench.py` 迁移中被重复创建 `flow_node_artifact_bindings` 阻断，46 项均在业务断言前失败；该迁移不属于本修复，未擅自改动。Python 静态编译与 `git diff --check` 通过。
- **本次 Parser 验证**：并行 Workbench 迁移出现前，相关 V7 回归 70 passed；最新工作区中 Parser 纯契约 12 passed、前端逻辑 12 passed 且 Vite build 通过。但新增 `20260814_02_flow_workbench.py` 在空 SQLite 库重复创建 `flow_node_artifact_bindings`，导致 Catalog/PDF Runner 集成测试在进入 Parser 逻辑前失败，T025 验证因此标记 BLOCKED。
- **完成统计**：Implementation DONE/N/A 23，Validation PASSED 22，完全完成 22，Validation BLOCKED 1（T025）；外部验收待完成 3（T014、T020、T023）。
- **评审**：自动化验证完成后待审；真实部署验收完成前不进入最终交付评审。

## 下一步

先修复并行 `007-flow-development-workbench` 的空库重复创表迁移，重跑 `TC-021` 并将 T025 验证改为 PASSED。随后在 NVIDIA Docker 主机按 `docs/releases/v7-acceptance.md` 完成文本、扫描和混合 PDF 实机验收，并验证五个默认受管 Collection、向量链路与 Milvus 重建恢复。
