# DataFlow 旧直连快照迁移证据

`dataflow-webui-legacy-direct-integration.patch` 是已删除的
`third_party/dataflow_webui` 与其 `UPSTREAM_NOTICE.md` 所声明上游提交
`3835a1018f4d77d3a871e77dbe0b05e763b00d1f` 的只读二进制差异导出。
导出仅包含当时由 Git 跟踪的文件，不包含 `node_modules` 或其它本地依赖。

`dataflow-webui-legacy-pnpm-lock.yaml` 是删除前唯一未跟踪的前端锁文件，已原样保留以支持审计和必要的回溯分析；它不参加 DataForge 的当前构建。当前实现使用 [v1 Overlay](../../deploy/dataflow-overlay/overlay.py) 和在 Docker 构建时固定的上游候选基线。

迁移前用 `dataforge-migrate --backup-dir <目录>` 创建 SQLite/Blob 备份；可用
`dataforge-migrate --restore-from <备份目录> --restore-dir <新的空状态目录>` 验证恢复。恢复命令拒绝覆盖已有数据库或 Blob，生产恢复前应先停止目标部署。
