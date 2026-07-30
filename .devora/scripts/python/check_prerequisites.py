#!/usr/bin/env python3
"""Validate and report the active Devora feature artifacts."""

from __future__ import annotations

import sys
from pathlib import Path

from common import (
    DevoraScriptError,
    available_design_docs,
    feature_dir_from_state,
    find_devora_root,
    print_json,
    require_file,
)

FLAGS = {
    "--require-plan": ("plan.md", "implementation plan"),
    "--require-tasks": ("tasks.md", "task list"),
    "--require-analysis": ("analysis.md", "analysis report"),
    "--require-test-cases": ("test-cases.md", "test cases"),
    "--require-review": ("review.md", "review report"),
}


def check(root: Path, required_flags: set[str]) -> dict[str, str]:
    feature_dir = feature_dir_from_state(root)
    files = {
        "SPEC_FILE": feature_dir / "spec.md",
        "PLAN_FILE": feature_dir / "plan.md",
        "TASKS_FILE": feature_dir / "tasks.md",
        "ANALYSIS_FILE": feature_dir / "analysis.md",
        "INTAKE_FILE": feature_dir / "intake.md",
        "SUMMARY_FILE": feature_dir / "summary.md",
        "TEST_CASES_FILE": feature_dir / "test-cases.md",
        "REVIEW_FILE": feature_dir / "review.md",
    }
    require_file(files["SPEC_FILE"], "feature specification")
    for flag in required_flags:
        filename, label = FLAGS[flag]
        require_file(feature_dir / filename, label)
    return {
        "FEATURE_DIR": str(feature_dir),
        **{key: str(path) for key, path in files.items()},
        "AVAILABLE_DOCS": available_design_docs(feature_dir),
    }


def main(argv: list[str] | None = None) -> int:
    try:
        arguments = argv or sys.argv[1:]
        output_json = "--json" in arguments
        required_flags = set(arguments) - {"--json"}
        unknown = required_flags - FLAGS.keys()
        if unknown:
            raise DevoraScriptError(f"unknown argument {min(unknown)}")
        result = check(find_devora_root(), required_flags)
        if output_json:
            print_json(result)
        else:
            print(f"Feature directory: {result['FEATURE_DIR']}")
        return 0
    except (DevoraScriptError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
