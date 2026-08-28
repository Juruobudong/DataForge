# 完整英文NLP资源包

`pii-en-v1.zip` 包含固定版本英文NER模型、tokenizer与NLTK punkt/punkt_tab。必须将此二进制包随源码同步到构建服务器；不入Git，不能只git pull。

审核清单是上级目录的`resources-pii-v1.lock.json`，包含归档SHA-256、跨平台内容摘要、文件数量/体积与固定NER/NLTK修订。Docker资源层强制`--network=none`，缺包、损坏或修订不匹配立即失败，不回退联网。

本地已有资源的导出命令（先激活sun）：

```text
python scripts/operator-resource-bundle.py --resources .dataforge/operator-resources-pii-v1 --descriptor .dataforge/operator-resources-pii-v1.bundle.json --archive runtime/dataflow/vendor-resources/pii-en-v1.zip --lock runtime/dataflow/resources-pii-v1.lock.json
```

导出只读校验来源资源，不修改已登记环境；拒绝覆盖现有包或审核清单。需要重新打包时使用新文件路径，审核新摘要后再调整构建。spaCy wheel仍在独立的`vendor/`目录，避免完整资源包变更使Python依赖层缓存失效。
