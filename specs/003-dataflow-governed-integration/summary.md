# Feature Summary: 文档知识生产链路

**Stage**: IMPLEMENTATION
**Artifact Readiness**: READY
**Human Gate**: APPROVED
**Updated**: 2026-08-05

## Direction

用户已明确用 MySQL、MinIO、持久化 Worker 和独立 DataFlow Runner 替换当前 SQLite/Gateway/Overlay 与医疗模板。旧数据归档、不迁移；保留单管理员和固定工作区导航。

## Execution preflight

- VERIFIED: Conda `sun`、Python 3.12、uv、Node/npm。
- MISSING: Docker CLI，因此 Compose/MinIO/MySQL 端到端验证暂不可执行。
- AVAILABLE_BUT_UNVERIFIED: 当前环境有 SQLAlchemy，但 Alembic、PyMySQL、MinIO 与 open-dataflow 尚未验证到项目虚拟环境。

## Progress

- Implementation: 6 DONE / 6 total
- Validation: 1 PASSED, 3 PARTIAL, 2 BLOCKED
- Validation evidence: `python -m pytest -q tests/test_platform.py tests/test_platform_api.py` passed 5 tests; `compileall` passed for the new platform, API, Runner and Worker.
- Blocked: Docker CLI is absent; npm does not create `node_modules`; uv cannot write one `word2number` distribution cache file after dependency resolution.
- Resume: in a Docker-enabled environment run `uv sync --extra web`, `cd frontend && npm install && npm run build`, then `docker compose up --build` and the complete test suite.
