# 可选的、已校验的模型 wheel

在有网络的本机（先 `conda activate sun`）执行：

```text
python scripts/prepare-operator-model-wheel.py
```

脚本将固定的 `en_core_web_sm-3.7.1-py3-none-any.whl` 下载到本目录并校验 SHA-256。将本目录中的 wheel 随源码同步到构建服务器，可让 Docker 的 spaCy 安装不再访问 GitHub。wheel 不入 Git，但不得从 Docker build context 排除；仅 git pull 不会传输该文件。

已存在且哈希正确的文件直接复用；错误文件拒绝使用且不覆盖。没有预置 wheel 时构建会尝试官方地址，使用独立缓存、超时和重试；这不能保证受限网络下首次下载成功。不接受任意模型版本或关闭证书/哈希校验。
