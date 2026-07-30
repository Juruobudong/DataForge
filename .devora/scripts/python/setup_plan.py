#!/usr/bin/env python3
"""Create or backfill planning artifacts for the active feature."""

from __future__ import annotations

import sys

from common import (
    DevoraScriptError,
    copy_template_if_missing,
    feature_dir_from_state,
    find_devora_root,
    print_json,
    require_file,
)


def setup() -> dict[str, str]:
    root = find_devora_root()
    feature_dir = feature_dir_from_state(root)
    spec_file = feature_dir / "spec.md"
    require_file(spec_file, "feature specification")

    files = {
        "INTAKE_FILE": (feature_dir / "intake.md", "intake-template.md"),
        "SUMMARY_FILE": (feature_dir / "summary.md", "summary-template.md"),
        "PLAN_FILE": (feature_dir / "plan.md", "plan-template.md"),
        "TEST_CASES_FILE": (
            feature_dir / "test-cases.md",
            "test-cases-template.md",
        ),
    }
    for path, template in files.values():
        copy_template_if_missing(root, template, path)
    return {
        "FEATURE_DIR": str(feature_dir),
        "SPEC_FILE": str(spec_file),
        **{key: str(path) for key, (path, _) in files.items()},
    }


def main(argv: list[str] | None = None) -> int:
    try:
        arguments = argv or sys.argv[1:]
        if any(argument != "--json" for argument in arguments):
            raise DevoraScriptError(f"unknown argument {arguments[0]}")
        result = setup()
        if "--json" in arguments:
            print_json(result)
        else:
            print(f"Plan file: {result['PLAN_FILE']}")
        return 0
    except (DevoraScriptError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
