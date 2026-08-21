# DataForge 当前实施计划

> 更新日期：2026-08-18
> 当前主线：Deployment Fork、医院 qa-agent Routing 与离线知识迁移

当前批准边界是“中心多 Deployment 控制面 + 医院 qa-agent 测试/生产 Routing + local 单 Deployment Fork 自治 + 签名离线知识资产迁移”。仓库实现以 `specs/009-deployment-offline-migration/` 为规格、技术计划、任务和测试用例来源，详细事实见 [`wiki/pages/deployment-and-migration.md`](wiki/pages/deployment-and-migration.md)。

当前交付状态：数据模型、兼容 Alembic、InstanceContext、医院/阶段 DeploymentTask 授权、Snapshot v3/Runtime API、生产 Partition delivery/备份、qa-agent dense 客户端、qa-agent/ kg 中央项目幂等种子、五个内置 Collection 跨空卷确定性 ownership token、`.34` 旧随机 marker 的自包含受控 Bash 清理入口、`.dfm`/Ed25519、Planner、Parquet Partition、Worker 检查点导入导出、冲突处理、前端和本地自动测试已实现；真实 MySQL/MinIO、双 Milvus、Embedding、签名密钥和离线介质验收等待部署服务器执行。

固定不变量：

- Routing 只能由 `DeploymentTask + org_code → knowledge_library_id[]` 生成。
- local 数据库只允许一个绑定 Deployment；其他 Deployment 返回 404。
- Seed 只允许一次；Update 不修改 local 授权、Routing、org_code 或 local 资产。
- `.dfm` 离线迁移不连接医院 local Milvus；qa-agent 在线发布只连接固定测试/生产 Milvus，并只同步授权所需 `kl_*` Partition。
- 顶层 Deployment 表达一家医院或中心运行环境，ProjectDeployment 仅表达 Project 关联；同一医院环境可同时承载 qa-agent 与 kg-for-consultation，但任务、授权、Snapshot、版本和回滚独立。医院代码全局唯一，`org_code` 仅是当前 ProjectDeploymentTask 的路由键；test 默认 `34.34:19531`，医院 production 由管理员填写，`34.36:19531` 仅是 `dataforge-central` 的 production Target，生产切换/发布/回滚分别确认。
- `.dfm` 使用逐 entry SHA-256 和 Ed25519，不在应用层加密。

---

# 历史计划：DataForge 全流程项目计划

> 版本：v0.5
> 更新日期：2026-08-03
> 当前重点：固定医疗模板下的源文档到可追溯知识库生产

## 1. 当前产品边界

DataForge 面向医疗资料的知识生产，不再提供通用数据流编排产品。顶部工作区名称固定为“业务工作区”和“流程开发区”；所有正式生产均由平台维护的内置模板发起。

```text
源文档 / 不可变版本
  → 选择固定模板
  → 独立知识任务
  → 文本 / 问答 / 图谱知识库
  → 记录级溯源
```

DataFlow 引擎、Studio、通用流程草稿、版本发布和自定义编排已下线。历史 DataFlow 配置、任务和无引用派生产物在启动清理中永久移除；`source`、`source_version` 及仍被原生或 LLM 结果引用的资产保留。

## 2. 固定模板

平台只提供下列 7 个非空组合；不提供通用自定义编排。

| 模板 | 生成结果 |
| --- | --- |
| 医疗模板（首次默认） | 文本知识库 + 问答知识库 + 知识图谱 |
| 文本知识库模板 | 文本知识库 |
| 问答知识库模板 | 问答知识库 |
| 知识图谱模板 | 知识图谱 |
| 文本知识库 + 问答知识库模板 | 文本知识库 + 问答知识库 |
| 文本知识库 + 知识图谱模板 | 文本知识库 + 知识图谱 |
| 问答知识库 + 知识图谱模板 | 问答知识库 + 知识图谱 |

一次提交会为模板中每个类型创建独立 `knowledge_job` 和独立 `knowledge_base`。任务可以并发运行，成功、失败、取消和重试均按类型独立处理。

模板 ID 固定不变。首次启动的全局默认项为“医疗模板”，用户可在模板目录将任一白名单模板设为默认项；该设置持久化，并由任务向导自动预选。

## 3. 知识类型与内置流程

| 类型 | 输出结构 | 执行边界 |
| --- | --- | --- |
| 文本知识库 | `content`、`chunk_index` | 原生标准化、分块和去重，不依赖 LLM。 |
| 问答知识库 | `question`、`answer` | 使用共享 LLM，严格校验、去重并保存来源片段。 |
| 知识图谱 | `subject`、`predicate`、`object` | 使用共享 LLM，严格校验、实体消歧和 SQLite 图谱投影。 |
| 多轮对话库 | `messages` | 仅保留类型目录；当前没有内置模板，不能创建任务。 |

图谱模板安装后立即可选。模型调用、输出校验或图谱生成失败只会失败该图谱任务，不影响同批的文本或问答结果。

## 4. 业务向导与接口

文档管理提供“源文档 / 文档库”二级视图：用户选择部分或全部源文档创建固定版本文档库，后续处理任务的步骤为：选择文档库、选择模板、确认并开始。默认预选服务端返回的全局默认项。流程开发区固定按“知识类型、标准流程、模板、DataFlow 调试台”排列：前三页只读展示内置目录，调试台标记为待开发。

主要接口：

- `GET /api/medical-templates`
- `PUT /api/medical-templates/{id}/default`
- `GET/POST /api/document-libraries`
- `GET/PUT/DELETE /api/document-libraries/{id}`
- `GET /api/standard-pipelines`
- `POST /api/knowledge-jobs/batch`
- 已有的任务详情、取消、重试、知识库、图谱和记录溯源接口。

批量创建请求必须携带 `medical_template_id` 和 `document_library_id`。服务端从文档库展开固定来源版本并决定输出类型和内置流程，拒绝未知模板或文档库；不再接受任意知识类型、流程版本、DataFlow 配置或临时来源列表。

## 5. 数据与清理约束

- `source` 与 `source_version` 是原始事实，清理时永不因 DataFlow 下线而删除。
- `knowledge_base` 只对应一种知识类型；`knowledge_record` 必须关联源版本。
- 历史 DataFlow 任务、流程草稿/版本、调试状态、仅由其引用的运行和资产会在一个数据库事务中删除。
- Blob 仅在没有任意 `source_version` 或 `asset_version` 引用时才回收。
- 原生和 LLM 流程结果、共享 Blob 以及其溯源链必须保留。

## 6. 后续阶段

二期在不改变上述知识生产事实记录的前提下，增加向量索引、图索引、知识集合和统一检索。三期再讨论更多来源（Excel、数据库、API、HIS/EMR）、OCR、医疗标准化和权限治理。

## 7. 当前验收标准

- 首次默认的医疗模板和其余六种模板均能创建正确的独立任务集合；任一模板可持久化设为唯一默认项。
- 文本流程使用原生引擎；问答和图谱仅在各自任务内依赖 LLM。
- 未知模板被拒绝；`/studio` 与 DataFlow API 不可访问。
- 图谱失败不回滚文本或问答结果。
- 历史 DataFlow 派生产物被清理，源文档、原生/LLM 结果和共享 Blob 被保留。
- 用户界面顶部固定为“业务工作区 / 流程开发区”；流程开发区依次展示知识类型、标准流程、模板和待开发的 DataFlow 调试台，不出现 Studio、技术编排或自定义类型入口；多轮对话库仅展示且不能创建任务。
