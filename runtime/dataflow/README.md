# 精选算子运行环境

API/Worker 不加载 DataFlow。Runner 在独立 **Python 3.12 CPU** 环境中调用 `open-dataflow==1.0.10`，当前 Catalog 登记26个精选算子，不扫描全Registry。新质量信号的适配行为在 Catalog 明确声明；MinHash的同Chunk/短文本保护不变。注册数量不等于所有模型环境均已Ready。

## semantic-v1 独立多语言模型（2026-08-31）

新增 `requirements-semantic-v1.in/lock`，锁摘要 `59032efef5ff7783d5023f371a578bffc48e204991bde9e38bd767181fa8c447`。Windows全新环境已实际安装47个依赖加open-dataflow；不包含PII/Presidio/spaCy，原环境和Manifest条目不覆盖。

固定模型为 `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`，commit `e8f8c211226b894fcb81acc59f3b34ba3efd5f42`。`semantic-model-v1.lock.json` 固定11个文件及大小、Git blob或LFS SHA256；模型权重本身约471MB。运行按128-token窗口覆盖全部正文，以attention mask池化；语义重复只标记，不删除。

当前实际权重尚未下载成功：主站连接超时，镜像文件下载元数据缺失/TLS断流。资源Profile未登记，`SemDeduplicateFilter`返回`OPERATOR_RESOURCE_MISSING`。真实模型验收和Runner资源层构建仍未完成，不能把受控向量测试作为模型效果验收。

先激活 `sun`，新环境安装（已存在环境不要再次运行）：

```powershell
python scripts/install-operator-runtime.py --wheel .dataforge/operator-downloads/open_dataflow-1.0.10-py3-none-any.whl --environment .dataforge/operator-env-semantic-v1 --manifest .dataforge/operator-runtime.json --dependency-lock runtime/dataflow/requirements-semantic-v1.lock --torch-backend cpu
```

在可联网机器准备同一审核提交的11个文件后，将文件按原相对目录放入 `runtime/dataflow/vendor-resources/semantic-multilingual-v1/`。本地离线导入/校验/登记：

```powershell
.dataforge/operator-env-semantic-v1/Scripts/python.exe scripts/prepare-semantic-resources.py --offline-model-directory runtime/dataflow/vendor-resources/semantic-multilingual-v1 --resources .dataforge/operator-resources-semantic-v1 --manifest .dataforge/operator-runtime.json --dependency-lock runtime/dataflow/requirements-semantic-v1.lock --wheel .dataforge/operator-downloads/open_dataflow-1.0.10-py3-none-any.whl
```

也可在联网机器直接运行准备脚本（省略 `--offline-model-directory`，默认官方站点；公开镜像需显式 `--download-endpoint https://hf-mirror.com`）。不论来源均验证固定文件哈希，下载失败不登记资源；不接受任意模型名或浮动修订。需要只准备不登记时加 `--download-only`。离线归档继续复用 `operator-resource-bundle.py`，新语义描述符使用独立模型metadata，不改变PII归档格式。

Runner新增独立semantic依赖层和`--network=none`资源层；构建必须额外同步上述11个模型文件，缺失立即失败，不在构建期回退下载。资源登记和运行验证模型修订、资源树摘要与冻结环境指纹。

## 英文治理资源环境

当前使用 `requirements-pii-v2.in/lock`：保持v1既有依赖版本，重新生成跨平台哈希，补充HuggingFace声明的Linux `hf-xet==1.6.0` 条件依赖。Windows/Linux各安装83项。PresidioFilter、PIIAnonymizeRefiner、BlocklistFilter发布v2；三项v1与原锁/已安装环境保持不变，供历史快照执行，其余轻量算子继续使用curated-v2。

在有网络的本机先下载固定spaCy wheel，再新建环境；不得向已登记环境追加依赖：

```powershell
conda activate sun
$env:UV_LINK_MODE = 'copy'
python scripts/prepare-operator-model-wheel.py
python scripts/install-operator-runtime.py --wheel .dataforge/operator-downloads/open_dataflow-1.0.10-py3-none-any.whl --environment .dataforge/operator-env-governance-v2 --manifest .dataforge/operator-runtime.json --dependency-lock runtime/dataflow/requirements-pii-v2.lock --torch-backend cpu --model-wheel-dir runtime/dataflow/vendor
.dataforge/operator-env-governance-v2/Scripts/python.exe scripts/prepare-operator-resources.py --resources .dataforge/operator-resources-pii-v1 --manifest .dataforge/operator-runtime.json --dependency-lock runtime/dataflow/requirements-pii-v2.lock --wheel .dataforge/operator-downloads/open_dataflow-1.0.10-py3-none-any.whl
```

使用 `--torch-backend cpu` 仅为PyTorch选择CPU包源，其他包仍从配置的PyPI源安装；不要设置全局 `UV_EXTRA_INDEX_URL` 或 `unsafe-best-match`。所有依赖保持SHA-256校验。锁通过uv0.9.9的 `--universal --python-version 3.12 --torch-backend cpu --generate-hashes` 生成，命令见锁文件头；当前验证目标是Windows AMD64与Linux x86_64/Python3.12，不承诺其他架构。

### GitHub模型包超时与构建复用

预下载产物：`runtime/dataflow/vendor/en_core_web_sm-3.7.1-py3-none-any.whl`，SHA-256为 `86cc141f63942d4b2c5fcee06630fd6f904788d2f0ab005cce45aadb8fb73889`。**需将此wheel随源码同步到服务器，Git不跟踪它，仅git pull不够。** `python scripts/prepare-operator-model-wheel.py --offline` 可检查已准备包。

Docker构建优先校验并复用vendor内wheel，无需连接GitHub；未预置时使用独立BuildKit模型缓存及官方地址，默认120秒超时/4次尝试。错误哈希拒绝使用且不覆盖已有文件；临时签名URL不写日志。不通过关闭证书或哈希校验解决网络问题。

安装时只将spaCy下载位置投影为已验证本地文件，保留版本及SHA-256；运行清单登记原始v2审核锁的指纹，不登记临时文件路径。模型包不会复制进最终Runner下载目录。

NER固定 `dslim/bert-base-NER@d1a3e8f13f8c3566299d95fcfc9a8d2382a9affc`，NLTK固定 `550b6625bcef1f2abff2ff770a5a0d272c9c6b2a` 的punkt/punkt_tab，spaCy固定3.7.1。维护人员首次准备原始资源需网络；当前Docker资源层已改为强制离线导入完整包，不再连接HuggingFace/NLTK源。已登记资源目录只读复用，漂移拒绝运行。

### 完整NER/NLTK离线资源包

除spaCy wheel，还必须同步`runtime/dataflow/vendor-resources/pii-en-v1.zip`（443,016,588 bytes，约443 MB）及`runtime/dataflow/resources-pii-v1.lock.json`。ZIP不入Git，须额外同步；审核清单随源码管理。归档SHA-256为`71d1db9d9e8fd0f5839d9f2c0e187c27e20da681e9cb1b104e346e4665fe3267`，包含132个固定资源文件，解压共500,533,691 bytes。导出命令见[资源包说明](vendor-resources/README.md)。

Docker的`operator-governance-resources`阶段使用`RUN --network=none`，校验固定修订、ZIP哈希、文件数量/体积及跨平台内容摘要后导入。缺包、损坏、路径穿越或链接立即拒绝，不回退下载。内容摘要以POSIX相对路径字符串排序，原运行时摘要算法与旧快照不变；导入到容器路径后按原算法生成本机描述符并离线登记。资源包只进入资源层，不使Python依赖层重装。完整镜像首次安装其他依赖仍可能访问PyPI，这里不宣称整个镜像离线构建。

资源脚本支持 `--download-only` 和 `--register-only`；镜像独立资源层准备后，在 `RUN --network=none` 登记。新PII环境为 `dataflow-1.0.10-pii-v2`；旧轻量环境镜像路径不变。历史PII v1快照仍需保留其原运行环境/Runner，不能用v2冒充v1；v1 Linux构建此前未成功。

执行阶段HF/NLTK与Python子进程继续离线；LLM经宿主Serving代理。PII仅英文CPU，不承诺中文病历识别，原始Chunk/Evidence仍保留。

## 安装

### 历史与轻量精选环境

原四个算子版本和 `requirements.lock` 不变。新增 ContentNullFilter、CharNumberFilter、SpecialCharacterFilter、NgramHashDeduplicateFilter、SimHashDeduplicateFilter、PromptedFilter v1 使用 `requirements-curated-v2.lock`，补齐 nltk/simhash 最小CPU依赖。上游仍为同一 `open-dataflow==1.0.10` wheel；不得原地升级旧环境。

完成下文原环境安装后，另建扩充环境并登记到同一 Manifest（自动保留旧记录）：

```powershell
conda activate sun
.dataforge/operator-env/Scripts/python.exe scripts/install-operator-runtime.py --wheel .dataforge/operator-downloads/open_dataflow-1.0.10-py3-none-any.whl --environment .dataforge/operator-env-curated-v2 --manifest .dataforge/operator-runtime.json --dependency-lock runtime/dataflow/requirements-curated-v2.lock
```

Runner镜像在旧 `operator-deps` 之后增加独立 `operator-expanded-deps`，扩充锁变更只影响扩充依赖层。注册阶段离线登记两个环境，Runner同时保留原路径与 `dataflow-1.0.10-curated-v2`。旧版本继续按旧锁匹配；不修改历史Snapshot。

规则过滤仅导入NLTK，不使用语料；子进程的 NLTK_DATA 指向一次性临时目录，不下载任何语料/模型。PromptedFilter只能使用冻结Prompt/Serving；PromptedEvaluator只是它的上游内部依赖，不作为独立卡片开放。范围与参数见[执行契约基线](../../wiki/sources/flow-execution-curated-expansion-2026-08-28.md)。

所有本地 Python/uv 命令先激活 sun，但不要在 sun 中安装 DataFlow。维护人员使用 Python 3.12 执行：

```powershell
conda activate sun
python -m pip download --no-deps --only-binary=:all: --require-hashes -r runtime/dataflow/upstream.lock -d .dataforge/operator-downloads
python scripts/install-operator-runtime.py --wheel .dataforge/operator-downloads/open_dataflow-1.0.10-py3-none-any.whl --environment .dataforge/operator-env --manifest .dataforge/operator-runtime.json
```

安装脚本拒绝覆盖已有环境，先校验 wheel 摘要，再通过 `uv pip sync --require-hashes --only-binary=:all:` 按 `requirements.lock` 安装依赖，最后以 `--no-deps` 安装上游 wheel 并登记 Manifest。缺少匹配的 wheel 或哈希不符会失败，不回退源码构建；安装失败不会登记 Manifest。Runner 镜像采用相同安装顺序，环境在 `/opt/dataforge-operators/dataflow-1.0.10`。

`requirements.in/lock` 只包含精选 CPU 算子的导入依赖集合及其固定传递依赖。上游 wheel 以 `--no-deps` 安装，**未安装它声明的完整 GPU、音频、训练和 Agent 依赖**，不能据此声称支持整个 DataFlow。新增精选算子要同时审核依赖锁、映射、适配版本与真实包契约测试。

运行时只读 `DATAFORGE_OPERATOR_RUNTIME_MANIFEST`，本地默认 `.dataforge/operator-runtime.json`，容器默认 `/opt/dataforge-operators/operator-runtime.json`；不下载或安装包。API 通过已认证 Runner 查询依赖，本地无独立 Runner 时可以查询本地环境。Runner 使用与 API 相同的 `DATAFORGE_CONFIG_ENCRYPTION_KEY` 读取数据库模型配置，不把凭据传给插件。

## Runner 构建与缓存

根 Dockerfile 将算子环境与应用源码分开构建：

```text
python-base
├─ app-common → app / runner
└─ operator-deps → operator-expanded-deps → operator-governance-deps
   → operator-governance-resources → operator-runtime → 复制到 runner
```

- `operator-deps` 只复制 `upstream.lock` 和 `requirements.lock`，复用 pip/uv 缓存。当前依赖锁已有 26 个固定版本及 SHA-256，不需要在构建时重新生成；上游依然单独锁定，不能解析它的完整依赖集合。
- `operator-runtime` 才复制注册脚本，在 `RUN --network=none` 下生成 Manifest。Runner 只接收 `/opt/dataforge-operators`，不接收下载 wheel，并保留相同绝对路径。
- 仅修改应用源码、Adapter、安装脚本、注册脚本或此说明，不会使算子依赖阶段失效；修改锁文件、Python/uv 基础层或相应构建指令会使其重建。修改注册脚本只重做登记和后续镜像组装。
- 缓存减少重复安装，不能保证首次下载速度；保留缓存也不保证所有包都已缓存。`Resolved` 日志不表示版本未锁定，不以日志是否出现作为验收标准。

默认源保留 `https://pypi.org/simple`。部署环境可在 `.env.docker` 设置 `PYPI_INDEX_URL`；例如选择清华镜像时使用 `https://pypi.tuna.tsinghua.edu.cn/simple`。也可在部署主机临时覆盖：

```bash
docker compose --env-file .env.docker build \
  --build-arg PYPI_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
  dataforge-runner
```

pip 和 uv 共用该构建源，版本及哈希校验保持不变；哪个源更快应由部署环境实测。换源会影响构建缓存，不应反复换源或默认使用 `--no-cache`。日常构建不要清理 builder 缓存。

部署验收需分别确认首次构建成功、仅改源码或注册脚本时 `operator-deps` 显示 `CACHED`、更改审核锁后依赖层重新安装且仍执行哈希校验。本机不运行 Docker；同步到 `.34` 后先执行 `docker compose down -v` 清空测试数据，完整部署顺序见 [运行与测试](../../wiki/pages/operations-and-testing.md)。

## 冻结与维护

- Flow 固定 OperatorVersion、Provider、Executor、实现、适配版本、wheel 摘要、依赖锁和实际环境摘要。发布时验证实现可加载；子进程校验 Python/依赖版本、算子实际文件摘要及类的包归属。
- 新版本使用新环境并重新登记，必须保留旧环境才能执行旧快照。清单支持多个环境。不要向已发布环境追加插件；这会改变环境摘要。
- 自定义包由团队开发、维护人员审核安装、管理员注册 Manifest 并验证发布。网页不能安装包或接收 Python 源码。
- 子进程提供故障隔离，不是恶意 Python 安全沙箱。文件/网络访问仍依赖代码审核和部署权限。超时/取消后终止插件并丢弃结果；已发出的模型请求可能仍在模型端结束，但结果不会被提交。

## 自定义示例

`examples/operator_packages/medical_demo` 提供 DataFlow 词典匹配和原生正文变换两种协议。先构建并在新的受控环境中安装审核后的 wheel/依赖，再用该环境的 Python 登记：

```powershell
conda activate sun
.dataforge/custom-env/Scripts/python.exe scripts/register-operator-runtime.py --output .dataforge/operator-runtime.json --dependency-lock runtime/dataflow/requirements.lock --package open-dataflow 1.0.10 .dataforge/operator-downloads/open_dataflow-1.0.10-py3-none-any.whl --package your-package 0.1.0 path/to/your_package-0.1.0-py3-none-any.whl
```

增加依赖时使用新审核锁文件。Manifest 填清单中的 `package_digest`；仅允许已知单 input/output 端口、普通字段映射、业务参数和受限能力。禁止覆盖平台 code、来源身份/Evidence、系统参数或自行写正式知识。样例：[DataFlow Manifest](../../examples/operator_packages/medical_demo/dataflow-manifest.json)、[Native Manifest](../../examples/operator_packages/medical_demo/native-manifest.json)，摘要需替换为实际构建 wheel 的值。

在「算子组件 → 自定义算子」注册 Manifest，运行真实包样例、查看报告、审核发布。LLM 验证仅使用 Manifest 样例响应。依赖、实现、参数、样例输出、契约、血缘、取消和主进程超时全部通过才能发布；修改 Manifest 需注册新版本，不能复用旧报告。

## 验证

```powershell
conda activate sun
uv run --extra web --with pytest pytest -q tests/test_curated_dataflow_operators.py
```

实际包测试需要已安装环境和两种示例插件；缺失时相关测试会跳过，交付必须区分通过与跳过。本次本机使用真实 wheel 和 stub Serving 验证，不代表真实模型效果或 `.34` 容器验收。
