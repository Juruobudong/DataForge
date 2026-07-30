#!/usr/bin/env python3
"""Create the final review artifact after required delivery artifacts exist."""

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
    paths = {
        "SPEC_FILE": feature_dir / "spec.md",
        "PLAN_FILE": feature_dir / "plan.md",
        "TASKS_FILE": feature_dir / "tasks.md",
        "ANALYSIS_FILE": feature_dir / "analysis.md",
        "TEST_CASES_FILE": feature_dir / "test-cases.md",
        "SUMMARY_FILE": feature_dir / "summary.md",
        "REVIEW_FILE": feature_dir / "review.md",
    }
    for key in (
        "SPEC_FILE",
        "PLAN_FILE",
        "TASKS_FILE",
        "ANALYSIS_FILE",
        "TEST_CASES_FILE",
    ):
        require_file(paths[key], "required artifact")
    copy_template_if_missing(root, "review-template.md", paths["REVIEW_FILE"])
    return {
        "FEATURE_DIR": str(feature_dir),
        **{key: str(path) for key, path in paths.items()},
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
            print(f"Review file: {result['REVIEW_FILE']}")
        return 0
    except (DevoraScriptError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
