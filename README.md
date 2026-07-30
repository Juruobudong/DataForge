# DataForge 数据加工与知识资产平台

![DataForge：从源文档到可追溯知识资产](docs/assets/dataforge-hero.png)

DataForge 以 [OpenDCAI DataFlow](https://github.com/OpenDCAI/DataFlow) 为数据处理引擎，面向不熟悉数据工程的业务人员提供简洁的文档加工流程，并为技术人员保留标准流程配置和调试能力。

> [!IMPORTANT]
> DataForge 目前处于持续开发阶段，现有版本主要用于验证核心流程和界面方案，并非生产就绪版本。部分页面和接口仍在调整，向量索引、知识集合、统一检索、权限管理等模块尚待开发。

## 项目目标

平台希望建立一条完整且可追溯的数据链路：

```text
PDF / CSV / Markdown / DOCX / TXT
  → 源文档与不可变版本
  → 可配置的知识类型
  → 已发布的 DataFlow 标准流程
  → 并行加工与逐条格式校验
  → 关系数据库中的知识资产
  → 向量或图索引
  → 知识集合与应用访问
```

业务用户只需要完成“上传文档、选择知识类型、启动处理、查看结果”等操作。知识类型结构、DataFlow 流程、模型服务、索引规则和数据库连接等复杂配置统一放在流程开发区。

## 当前开发状态

| 模块 | 当前状态 | 说明 |
|---|---|---|
| 源文档中心 | 基础能力已实现 | 支持 PDF、CSV、Markdown、DOCX、TXT 的上传、解析、版本管理和重复内容识别 |
| 动态知识类型 | 基础能力已实现 | 支持配置知识类型及字段结构，仍需继续完善版本治理和页面体验 |
| 标准流程 | 开发中 | 已具备流程登记、类型绑定、验证和默认流程基础能力，正在完善 DataFlow 调试与发布闭环 |
| 知识生产 | 原型已实现 | 支持多文档任务、结构校验和知识库写入，任务进度、失败恢复和大数据量处理仍需增强 |
| 知识资产 | 原型已实现 | 可以查看知识库和标准记录，列表、筛选、批量操作和大规模数据浏览仍需优化 |
| 记录级溯源 | 原型已实现 | 已建立知识记录到源版本的关联，PDF 页码、字符范围和分块对照仍需持续完善 |
| DataFlow 调试台 | 集成开发中 | 暂时纳入原 DataFlow 工作台，后续收敛为算子编排、样本调试、验证和发布所需功能 |
| 向量与图索引 | 待开发 | 将提供向量模型、向量库、图数据库、索引方案、异步批处理、重试和版本切换 |
| 知识集合与应用交付 | 待开发 | 将支持同类型、索引兼容知识库的版本化集合与稳定调用标识 |
| 统一检索服务 | 待开发 | 将提供文本、FAQ、图谱及混合检索和引用上下文组装 |
| 登录与权限 | 暂不开发 | 当前优先完成数据生产主流程，后续再设计用户、角色和租户能力 |

完整阶段规划参见 [plan.md](plan.md)。

## 当前可以体验的流程

1. 在“源文档”上传 PDF、CSV、Markdown、DOCX 或 TXT。
2. 在“知识生产”选择一个或多个文档版本和目标知识类型。
3. 系统匹配兼容且已验证的默认标准流程。
4. 启动任务，等待文档加工、逐条校验和关系数据库入库。
5. 在“知识资产”中查看知识库和知识记录。
6. 从知识记录进入溯源视图，查看处理后的内容与源文档位置。

“知识资产已入库”只表示标准记录已经写入关系数据库，并不表示已经向量化或可以被应用检索。向量库和图数据库索引属于下一阶段建设内容。

技术人员可以进入“流程开发区 / DataFlow 调试台”配置知识类型、编排流程、运行样本、检查中间结果并发布标准流程。当前调试台仍是过渡实现，部分 DataFlow 原生功能和界面会继续调整。

## 已具备的基础能力

- 文件内容校验和不可变版本管理
- PDF、CSV、Markdown、DOCX、TXT 解析
- 可配置知识类型及输出字段
- 标准流程与知识类型兼容校验
- 多文档知识生产任务
- 知识库和知识记录持久化
- 记录到源文档版本的关联与溯源
- DataForge 文件版本向 DataFlow 数据集桥接
- DataFlow 任务结果发布为 DataForge 数据资产
- 快速处理、预览、下载和基础接口

## 项目结构

```text
DataForge/
├── src/dataforge/                 # Python 后端、任务、存储与 DataFlow 桥接
├── frontend/                      # 面向业务用户的 Vue 中文界面
├── third_party/dataflow_webui/    # DataFlow 调试台前后端
├── tests/                         # 核心流程和接口测试
├── examples/                      # 示例文档与流程
├── docs/assets/                   # README 等文档资源
└── plan.md                        # 产品流程与阶段规划
```

运行数据默认保存在 `.dataforge/`，该目录不会提交到 Git：

```text
.dataforge/
├── metadata.sqlite3
├── blobs/
├── runs/
└── dataflow-studio/
    ├── data/
    ├── imports/
    └── cache/
```

## 安装与运行

环境要求：

- Python 3.11 或 3.12
- [uv](https://docs.astral.sh/uv/)
- Node.js 与 npm
- 本地 DataFlow 源码

如果 DataFlow 与 DataForge 位于同一个父目录，系统会自动发现；其他目录请设置 `DATAFORGE_DATAFLOW_PATH`。

```bash
git clone https://github.com/cheney369/DataForge.git
cd DataForge

export DATAFORGE_DATAFLOW_PATH=/path/to/DataFlow

uv sync --extra dataflow --extra web --extra studio

cd frontend
npm install
npm run build

cd ../third_party/dataflow_webui/frontend
npm install
npm run build

cd ../../..
uv run --extra dataflow --extra web --extra studio dataforge-web
```

浏览器打开 [http://127.0.0.1:8000](http://127.0.0.1:8000)，接口文档位于 [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)。

## 验证

```bash
uv run --with pytest --extra dataflow --extra web --extra studio pytest -q
```

前端构建：

```bash
cd frontend
npm run build

cd ../third_party/dataflow_webui/frontend
npm run build
```

## 已知边界

- Word 当前只支持 DOCX，旧版 `.doc` 尚未提供格式转换。
- 扫描版 PDF 尚未接入 OCR，只读取已有文字层。
- 大型文档产生数万条知识记录时，任务进度、分页和失败恢复能力仍需加强。
- 向量化、FAQ 索引、三元组图索引和多轮对话索引尚未实现。
- 当前版本未提供统一检索、知识集合、应用接入和生产级权限体系。
- 部分 DataFlow 算子依赖本地模型、GPU、音频、OCR 或第三方服务，需自行安装对应可选依赖。

## 最新更新

### 2026-07-30

- 重写项目说明，明确业务工作区与流程开发区的职责边界。
- 增加模块开发状态，区分现有原型、开发中能力和待开发模块。
- 补充关系库入库、向量或图索引、知识集合与应用访问的阶段规划。
- 增加 DataForge 数据加工全流程视觉图。

完整历史参见 [更新记录](docs/releases/release-notes.md)。
