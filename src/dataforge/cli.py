"""Small V7-only administrative command surface."""
from __future__ import annotations

import argparse
import json

from .config import Settings
from .v7.migrations import assert_schema_current
from .v7.store import V7Store


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dataforge", description="DataForge V7")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("health", help="检查 V7 schema 和当前知识库")
    args = parser.parse_args(argv)
    settings = Settings.load()
    if args.command == "health":
        revision = assert_schema_current(settings.platform_database_url)
        store = V7Store(settings.platform_database_url)
        print(json.dumps({"platform": "v7", "revision": revision, "knowledge_libraries": len(store.list_knowledge_libraries())}, ensure_ascii=False))
        return 0
    return 1
