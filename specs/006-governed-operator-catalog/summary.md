# 功能摘要：V7 受控知识算子目录

**阶段**：IMPLEMENTATION  
**工件语言**：zh-CN  
**工件就绪度**：READY
**交付就绪度**：DEPLOYMENT_ACCEPTANCE_PENDING
**人工门禁**：APPROVED  
**更新日期**：2026-08-13

## 变更内容

将 V7 收敛为文本、问答、图谱三类内置知识，并实现可发布扩展类型、动态 Index Profile、文档库模板绑定和自动结果知识库处理；后续已加入 Qwen3-32B 分块结构化生成、失败分块持久化/局部重试、统一 OpenAI-like Embedding 和当前页选中文件定向处理。

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
| 双图谱与存储 | DECISION | USER | CONFIRMED | 顶层保持 graph；Triple/Semantic 使用专属 Collection；Collection 按完整 Storage Contract 哈希归并，旧 Graph 冻结。 |
| 流程画布 | DECISION | USER | CONFIRMED | Flow DSL v3 采用白名单、强类型端口、受控分支/合流与 Knowledge Sink 终点，不开放任意代码。 |

## 主要风险

- 真实 OCR/LLM/Milvus 环境未提供；本地以 Adapter Fake/Stub 验证，真实集成保持部署验收项。
- V7 重建是破坏性操作；命令需精确清单和确认参数，测试证明不触碰旧资源或 Collection。
- Milvus 由另一份 Compose 管理，容器重建会失去临时网络端点；`dataforge-milvus-egress.timer` 负责恢复。基础网络/白名单验证已通过，但容器重建恢复及新 Collection 实机供应仍待验收。

## 实施与验证

- **实施**：T001-T013、T015-T019 已完成；新增双 Graph 模式、五个默认 Storage Contract/Managed Collection、幂等 Provisioner、模式化 Sink、Flow DSL v3 与 Vue Flow 编辑画布。
- **验证**：Conda `sun` 下完整回归 49 passed（1 条第三方警告），前端生产构建通过；部署主机基础 Milvus/Embedding/防火墙验证通过。当前 Windows 工作区无 Docker CLI，实机 Provision、向量写入/search/release 与重建恢复仍待部署主机执行。
- **完成统计**：18 项仓库实现完成；T014、T020 为外部部署验收门禁。
- **评审**：自动化验证完成后待审；真实部署验收完成前不进入最终交付评审。

## 下一步

在 Docker 主机重新构建/启动当前代码，运行 `dataforge-provision --reconcile`，验证五个默认受管 Collection 与新 Triple/Semantic 向量链路，再完成 Milvus 重建恢复验收。
