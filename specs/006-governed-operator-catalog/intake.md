# 需求输入：V7 受控知识算子目录

**工件语言**：zh-CN  
**准备时间**：2026-08-11  
**状态**：READY

## 来源登记

| ID | 来源 | 类型 | 版本/日期 | 访问 | 作用 |
|----|------|------|-----------|------|------|
| SRC-001 | 用户已批准的「三类内置知识与受控扩展机制」计划 | 对话/产品方案 | 2026-08-12 | READ | 三类种子、编码、动态类型/Profile、自动结果库、删除与验收要求。 |
| SRC-002 | 用户补充的 Operator Catalog 分层与 P0/P1/禁用清单 | 对话/产品方案 | 2026-08-11 | READ | 逻辑节点、DataFlow Adapter、Prompt 治理、Parser 与质量 Gate 边界。 |
| SRC-003 | 现有 V7 实现与 Wiki | 仓库 | 2026-08-11 | READ | 固定类型/线性模板/Runner 的当前实现及安全边界。 |
| SRC-004 | 用户确认的文档库当前页选中文件处理 | 对话/产品方案 | 2026-08-12 | READ | 当前页全选、按全部有效模板定向处理待更新版本、保留整库处理入口。 |
| SRC-005 | 用户确认的本机 Milvus 隔离直连方案与服务器网络盘点 | 对话/部署证据 | 2026-08-12 | READ | 专用外部 Docker 网络、Milvus 容器临时接入、API/Worker 最小出站与 systemd 恢复。 |
| SRC-006 | 用户批准的双图谱专属 Collection 与受控 DAG 方案 | 对话/产品方案 | 2026-08-13 | READ | Graph 模式、Storage Contract、Managed Collection、Flow DSL v3 与编辑画布。 |
| SRC-007 | 用户批准的 PDF GPU OCR 实施计划 | 对话/产品方案 | 2026-08-13 | READ | MinerU 3.4.4 Pipeline GPU、回环宿主机访问、Artifact 生命周期、超时和部署验收。 |
| SRC-008 | 用户批准的算子说明与节点输入输出示例计划 | 对话/产品方案 | 2026-08-14 | READ | 中文功能说明、版本化静态示例、逐节点受控内存预览与 Inspector 展示。 |

## 产品意图

- **问题**：当前 V7 的业务入口需要手工填写业务编码、手工选择目标库，且无法受控扩展类型/Collection。
- **用户**：管理员配置类型、模板和 Index Profile；业务人员从文档库触发自动结果库处理。
- **目标结果**：DataForge 暴露约 15 个稳定知识生产逻辑节点，将 DataFlow 具体算子、批处理与部署差异封装在版本化 Adapter 中；正式知识必须通过类型契约和 Sink。

## 已提取事实与约束

- 初始只保留 `text`、`qa`、`graph`；管理员可绑定任意已有兼容 Collection，平台只管理 `kl_<知识库ID>` 分区。— SRC-001
- 现有 V7 数据直接重建，不导出快照；只清理数据库登记的 V7 对象与 V7 Partition，不删除 Collection 或旧资源。— SRC-001
- P0 Catalog 包含 Parser、清洗、切片、QA/Prompt 生成、质量、去重和治理节点；P1 与禁用清单见 SRC-002。— SRC-002
- Prompt、质量规则、知识类型、算子、子图和流程均需修订发布；生产任务只执行不可变 Snapshot。— SRC-001、SRC-002
- V7 当前实现仅支持固定三类知识和线性模板，开发区侧栏固定为四页。— SRC-003
- `milvus-standalone-new` 是同一 Docker 主机上的未受 DataForge 管理容器，当前在默认 `bridge`，宿主机 `19531` 映射到其容器 `19530`；DataForge 不可修改其 Compose。— SRC-005
- Worker 的 `private` 网络为 `internal: true`，不能经宿主机映射端口访问 Milvus；API 也需要 Milvus 连接以校验和发布 Index Profile。— SRC-005
- 所有 PDF 固定通过独立 MinerU 3.4.4 GPU 服务以 `pipeline + auto` 解析；宿主机内部调用仅绑定回环地址，首版不接 VLM、vLLM、Flash、Router、多 GPU或 OCR UI。— SRC-007

## 冲突与缺口

- 旧 V7 规格明确禁止任意 DAG 和新增知识类型；本特性取代该限制，但继续禁止任意代码、Shell、DataFlow WebUI 与非白名单算子。
- 真实 MinerU GPU/CUDA、LLM、Milvus 和 Embedding 环境不在本地工作区；真实集成验收需部署环境。

## 推断

- DataFlow 上游类名与 Batch API 会持续变化，因此 Adapter Version 必须是已发布流程的唯一稳定依赖。— 依据 SRC-002。
- 上游 Pipeline 的运行顺序应由 DataForge 以 Snapshot 的拓扑序明确提供，而不以未保证的原生子图接口作为产品契约。— 依据 SRC-001。

## 已确认决定

- 用户已经批准三类内置知识、独立 Runner、V7 空库重建、无兼容迁移和固定四页导航。— SRC-001

## 可追溯性

SRC-001 覆盖 FR-001～FR-008；SRC-002 覆盖 FR-002、FR-003、FR-005、FR-006；SRC-003 提供迁移、兼容与安全验证基线；SRC-004 覆盖 FR-009；SRC-005 覆盖 FR-010；SRC-006 覆盖 FR-011～FR-013；SRC-007 覆盖 FR-014；SRC-008 覆盖 FR-015。
