from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from .application import DEFAULT_PIPELINE_ID, DataForge
from .errors import DataForgeError


def _json_default(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"Cannot serialize {type(value).__name__}")


def emit(value: Any, *, stream=sys.stdout) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=_json_default), file=stream)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dataforge", description="Medical AI data foundation CLI")
    parser.add_argument("--root", help="DataForge project root (defaults to current directory)")
    parser.add_argument("--dataflow-path", help="Path to the OpenDCAI DataFlow repository")
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("init", help="Initialize metadata, storage, and default pipelines")
    commands.add_parser("health", help="Show workspace and DataFlow integration health")

    source = commands.add_parser("source", help="Manage source files")
    source_commands = source.add_subparsers(dest="source_command", required=True)
    source_add = source_commands.add_parser("add", help="Ingest a source file or add a new version")
    source_add.add_argument("file")
    source_add.add_argument("--name")
    source_add.add_argument("--kind", default="file")
    source_add.add_argument("--source-id")
    source_commands.add_parser("list", help="List sources")
    source_versions = source_commands.add_parser("versions", help="List immutable source versions")
    source_versions.add_argument("source_id")

    pipeline = commands.add_parser("pipeline", help="Manage pipeline definitions")
    pipeline_commands = pipeline.add_subparsers(dest="pipeline_command", required=True)
    pipeline_commands.add_parser("list", help="List pipelines")
    pipeline_show = pipeline_commands.add_parser("show", help="Show one pipeline")
    pipeline_show.add_argument("pipeline_id")
    pipeline_register = pipeline_commands.add_parser("register", help="Register a JSON definition")
    pipeline_register.add_argument("definition_file")

    run = commands.add_parser("run", help="Execute or inspect a processing run")
    run_commands = run.add_subparsers(dest="run_command", required=True)
    run_start = run_commands.add_parser("start", help="Run a pipeline for a source version")
    run_start.add_argument("source_version_id")
    run_start.add_argument("--pipeline", default=DEFAULT_PIPELINE_ID)
    run_start.add_argument("--engine", choices=("dataflow", "native"))
    run_status = run_commands.add_parser("status", help="Show run status")
    run_status.add_argument("run_id")
    run_events = run_commands.add_parser("events", help="Show ordered run events")
    run_events.add_argument("run_id")
    run_commands.add_parser("list", help="List processing runs")

    asset = commands.add_parser("asset", help="Inspect published assets")
    asset_commands = asset.add_subparsers(dest="asset_command", required=True)
    asset_commands.add_parser("list", help="List assets and latest versions")
    asset_versions = asset_commands.add_parser("versions", help="List versions of one asset")
    asset_versions.add_argument("asset_id")
    asset_lineage = asset_commands.add_parser("lineage", help="Trace an asset version to its source")
    asset_lineage.add_argument("asset_version_id")
    asset_export = asset_commands.add_parser("export", help="Export a published asset version")
    asset_export.add_argument("asset_version_id")
    asset_export.add_argument("destination")
    asset_export.add_argument("--overwrite", action="store_true")

    flow = commands.add_parser("flow", help="Ingest, process, and publish in one command")
    flow.add_argument("file")
    flow.add_argument("--name")
    flow.add_argument("--kind", default="file")
    flow.add_argument("--source-id")
    flow.add_argument("--pipeline", default=DEFAULT_PIPELINE_ID)
    flow.add_argument("--engine", choices=("dataflow", "native"), default="dataflow")
    return parser


def dispatch(args: argparse.Namespace) -> Any:
    app = DataForge.open(args.root, args.dataflow_path)
    if args.command == "init":
        return app.health()
    if args.command == "health":
        return app.health()
    if args.command == "source":
        if args.source_command == "add":
            return app.sources.ingest(
                args.file,
                source_id=args.source_id,
                name=args.name,
                kind=args.kind,
            )
        if args.source_command == "list":
            return app.store.list_sources()
        if args.source_command == "versions":
            return app.store.list_source_versions(args.source_id)
    if args.command == "pipeline":
        if args.pipeline_command == "list":
            return app.store.list_pipelines()
        if args.pipeline_command == "show":
            return app.store.get_pipeline(args.pipeline_id)
        if args.pipeline_command == "register":
            return app.register_pipeline(args.definition_file)
    if args.command == "run":
        if args.run_command == "start":
            return app.run(
                args.source_version_id,
                pipeline_id=args.pipeline,
                engine_override=args.engine,
            )
        if args.run_command == "status":
            return app.store.get_run(args.run_id)
        if args.run_command == "events":
            return app.store.list_run_events(args.run_id)
        if args.run_command == "list":
            return app.store.list_runs()
    if args.command == "asset":
        if args.asset_command == "list":
            return app.store.list_assets()
        if args.asset_command == "versions":
            return app.store.list_asset_versions(args.asset_id)
        if args.asset_command == "lineage":
            return app.lineage(args.asset_version_id)
        if args.asset_command == "export":
            return app.export_asset(
                args.asset_version_id,
                args.destination,
                overwrite=args.overwrite,
            )
    if args.command == "flow":
        return app.flow(
            args.file,
            name=args.name,
            kind=args.kind,
            source_id=args.source_id,
            pipeline_id=args.pipeline,
            engine_override=args.engine,
        )
    raise AssertionError(f"Unhandled command: {args.command}")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        emit(dispatch(args))
        return 0
    except (DataForgeError, OSError, ValueError) as exc:
        emit({"ok": False, "error": type(exc).__name__, "message": str(exc)}, stream=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
