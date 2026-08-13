"""DataForge V7 schema command for empty or existing V7 databases."""
from __future__ import annotations

import argparse
import json
import os

from .config import Settings
from .v7.migrations import assert_schema_current, upgrade
from .v7.storage import LocalObjectStore, MinioObjectStore
from .v7.store import V7Store
from .v7.vector import V7Milvus


def _objects(settings: Settings):
    if settings.minio_endpoint and settings.minio_access_key and settings.minio_secret_key:
        return MinioObjectStore(settings.minio_endpoint, settings.minio_access_key, settings.minio_secret_key, settings.minio_bucket)
    return LocalObjectStore(settings.state_dir / "v7-objects")


def rebuild_v7(settings: Settings) -> dict:
    """Explicit, manifest-driven V7 reset that never drops a Collection."""
    store = V7Store(settings.platform_database_url)
    store.assert_schema_current()
    manifest = store.v7_rebuild_manifest()
    milvus_uri = os.getenv("DATAFORGE_MILVUS_URI")
    if milvus_uri:
        milvus = V7Milvus(milvus_uri, os.getenv("DATAFORGE_MILVUS_TOKEN"))
        for binding in manifest["partition_bindings"]:
            milvus.drop_partition(binding["collection_name"], binding["partition_name"])
    objects = _objects(settings)
    for key in manifest["object_keys"]:
        objects.delete_key(key)
    deleted = store.rebuild_v7_database_state()
    store.seed()
    return {"manifest": manifest, "deleted_rows": deleted, "seeded": True}


def main() -> None:
    parser = argparse.ArgumentParser(description="DataForge V7 Alembic migration")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--upgrade-platform", action="store_true", help="通过 Alembic 初始化或升级已有 V7 schema 并写入 V7 种子")
    group.add_argument("--check-platform-schema", action="store_true", help="仅校验 V7 Alembic revision")
    group.add_argument("--rebuild-v7", action="store_true", help="仅删除数据库登记的 V7 对象、V7 Partition 和 V7 表数据后重建种子")
    parser.add_argument("--confirm", default="", help="执行 --rebuild-v7 必须为 REBUILD-V7")
    args = parser.parse_args()
    settings = Settings.load()
    if not settings.database_url:
        parser.error("V7 migration 需要 DATAFORGE_DATABASE_URL，Compose 目标必须是 MySQL dataforge")
    if args.upgrade_platform:
        output = upgrade(settings.platform_database_url)
        V7Store(settings.platform_database_url).seed()
        output["seeded"] = True
    elif args.rebuild_v7:
        if args.confirm != "REBUILD-V7":
            parser.error("--rebuild-v7 必须同时传入 --confirm=REBUILD-V7")
        output = rebuild_v7(settings)
    else:
        output = {"current_revision": assert_schema_current(settings.platform_database_url)}
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
