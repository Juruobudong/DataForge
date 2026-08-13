# DataForge Docker 优化包

本目录是按 DataForge 2026-08-05 最新主分支结构生成的替换文件。

## 最短应用步骤

```bash
cd /data/xxw/DataForge

# 1. 备份
cp Dockerfile Dockerfile.bak
cp compose.yaml compose.yaml.bak
cp pyproject.toml pyproject.toml.bak
cp .dockerignore .dockerignore.bak

# 2. 将本优化包内同路径文件覆盖到仓库

# 3. 重新生成 CPU 版锁文件
chmod +x scripts/*.sh
TORCH_BACKEND=cpu ./scripts/regenerate-lock.sh

# 4. 准备环境变量
cp .env.docker.example .env
# 编辑 .env，修改所有密码和 token

# 5. 构建并启动
./scripts/build-and-up.sh
```

完整说明见：

```text
docs/Docker构建优化说明.md
```

## 核心改动

1. Docker 使用 `uv.lock`，不再执行 `pip install ".[web]"`。
2. `open-dataflow` 移到 `runner` extra。
3. API 与 Worker 共用轻量 `dataforge-app` 镜像。
4. 只有 Runner 构建重依赖。
5. 默认使用 CPU PyTorch，避免 CUDA 依赖下载。
6. 前端改为 `npm ci` 并启用 npm 缓存。
7. uv、npm、apt 均启用 BuildKit cache mount。
