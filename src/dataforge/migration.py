"""DataForge V7 schema command for empty or existing V7 databases."""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence

from .config import Settings
from .v7.database_preflight import DatabaseUserDecisionRequired
from .v7.migrations import assert_schema_current, initialize, validate_current_schema
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


DATABASE_USER_DECISION_EXIT_CODE = 20


def _decision_output(error: DatabaseUserDecisionRequired) -> int:
    payload = error.to_dict()
    if not sys.stdin.isatty():
        payload.update({
            "status": "user_decision_required",
            "next_action": (
                "如不保留数据，请在测试环境中自行清空数据库后重新运行；"
                "如需保留数据，请停止初始化并先进行兼容性分析。"
            ),
        })
        print(json.dumps(payload, ensure_ascii=False, indent=2), file=sys.stderr)
        return DATABASE_USER_DECISION_EXIT_CODE

    print("检测到数据库包含已有状态，当前版本不会自动兼容或修改已有数据库。", file=sys.stderr)
    print("是否需要保留现有数据？", file=sys.stderr)
    print("[Y] 保留数据，停止初始化并输出兼容性分析报告", file=sys.stderr)
    print("[N] 不保留数据，我会自行清空数据库", file=sys.stderr)
    try:
        answer = sys.stdin.readline().strip().lower()
    except (EOFError, OSError):
        answer = ""
    if answer in {"y", "yes"}:
        payload.update({
            "status": "compatibility_required",
            "next_action": "数据库保持不变；请基于本报告单独进行兼容性分析。",
        })
    elif answer in {"n", "no"}:
        payload.update({
            "status": "reset_required",
            "next_action": (
                "数据库保持不变；请自行清空数据库后重新运行。测试服务器可执行 "
                "docker compose --env-file .env.docker down -v。"
            ),
        })
    else:
        payload.update({
            "status": "user_decision_required",
            "next_action": "未识别明确的 Y/N 选择，数据库保持不变。",
        })
    print(json.dumps(payload, ensure_ascii=False, indent=2), file=sys.stderr)
    return DATABASE_USER_DECISION_EXIT_CODE


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="DataForge V7 Alembic migration")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--upgrade-platform", action="store_true", help="初始化空数据库或校验当前 V7 schema，并幂等写入当前种子")
    group.add_argument("--check-platform-schema", action="store_true", help="仅校验当前 V7 Alembic revision 与 schema 结构")
    group.add_argument("--rebuild-v7", action="store_true", help="仅删除数据库登记的 V7 对象、V7 Partition 和 V7 表数据后重建种子")
    parser.add_argument("--confirm", default="", help="执行 --rebuild-v7 必须为 REBUILD-V7")
    args = parser.parse_args(argv)
    settings = Settings.load()
    if not settings.database_url:
        parser.error("V7 migration 需要 DATAFORGE_DATABASE_URL，Compose 目标必须是 MySQL dataforge")
    try:
        if args.upgrade_platform:
            output = initialize(settings.platform_database_url)
            V7Store(settings.platform_database_url).seed()
            output["seeded"] = True
        elif args.rebuild_v7:
            if args.confirm != "REBUILD-V7":
                parser.error("--rebuild-v7 必须同时传入 --confirm=REBUILD-V7")
            output = rebuild_v7(settings)
        else:
            revision = assert_schema_current(settings.platform_database_url)
            validate_current_schema(settings.platform_database_url)
            output = {
                "status": "current",
                "database_state": "current",
                "current_revision": revision,
                "seeded": False,
            }
    except DatabaseUserDecisionRequired as error:
        return _decision_output(error)
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
