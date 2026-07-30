#!/usr/bin/env python3
"""Create the active feature's executable task list."""

from __future__ import annotations

import sys

from common import (
    DevoraScriptError,
    available_design_docs,
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
    plan_file = feature_dir / "plan.md"
    test_cases_file = feature_dir / "test-cases.md"
    tasks_file = feature_dir / "tasks.md"
    summary_file = feature_dir / "summary.md"

    require_file(spec_file, "feature specification")
    require_file(plan_file, "implementation plan")
    require_file(test_cases_file, "test cases")
    copy_template_if_missing(root, "tasks-template.md", tasks_file)
    return {
        "FEATURE_DIR": str(feature_dir),
        "SPEC_FILE": str(spec_file),
        "PLAN_FILE": str(plan_file),
        "TASKS_FILE": str(tasks_file),
        "TEST_CASES_FILE": str(test_cases_file),
        "SUMMARY_FILE": str(summary_file),
        "AVAILABLE_DOCS": available_design_docs(feature_dir),
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
            print(f"Tasks file: {result['TASKS_FILE']}")
        return 0
    except (DevoraScriptError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
