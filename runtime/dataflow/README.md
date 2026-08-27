# 精选算子运行环境

API/Worker 不加载 DataFlow。Runner 在独立 **Python 3.12 CPU** 环境中调用 `open-dataflow==1.0.10`，当前支持 QA v5、去重 v4、修订 v4，不扫描上游 Registry。

## 安装

所有本地 Python/uv 命令先激活 sun，但不要在 sun 中安装 DataFlow。维护人员使用 Python 3.12 执行：

```powershell
conda activate sun
python -m pip download --no-deps --only-binary=:all: --require-hashes -r runtime/dataflow/upstream.lock -d .dataforge/operator-downloads
python scripts/install-operator-runtime.py --wheel .dataforge/operator-downloads/open_dataflow-1.0.10-py3-none-any.whl --environment .dataforge/operator-env --manifest .dataforge/operator-runtime.json
```

安装脚本拒绝覆盖已有环境，校验 wheel 摘要并按 `requirements.lock` 安装依赖。Runner 镜像执行同样步骤，环境在 `/opt/dataforge-operators/dataflow-1.0.10`。本次未在本机运行 Docker。

`requirements.in/lock` 只包含精选 CPU 算子的导入依赖集合及其固定传递依赖。上游 wheel 以 `--no-deps` 安装，**未安装它声明的完整 GPU、音频、训练和 Agent 依赖**，不能据此声称支持整个 DataFlow。新增精选算子要同时审核依赖锁、映射、适配版本与真实包契约测试。

运行时只读 `DATAFORGE_OPERATOR_RUNTIME_MANIFEST`，本地默认 `.dataforge/operator-runtime.json`，容器默认 `/opt/dataforge-operators/operator-runtime.json`；不下载或安装包。API 通过已认证 Runner 查询依赖，本地无独立 Runner 时可以查询本地环境。Runner 使用与 API 相同的 `DATAFORGE_CONFIG_ENCRYPTION_KEY` 读取数据库模型配置，不把凭据传给插件。

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
