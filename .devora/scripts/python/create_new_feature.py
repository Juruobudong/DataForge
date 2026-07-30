#!/usr/bin/env python3
"""Create the next sequential Devora feature directory."""

from __future__ import annotations

import re
import sys
from pathlib import Path

from common import (
    DevoraScriptError,
    atomic_write_json,
    copy_template_if_missing,
    find_devora_root,
    load_json,
    print_json,
)

LANGUAGES = {"auto", "zh-CN", "en"}
SHORT_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
FEATURE_RE = re.compile(r"^(\d{3})-")


def parse_args(argv: list[str]) -> tuple[bool, str, str | None]:
    output_json = False
    short_name: str | None = None
    language: str | None = None
    index = 0
    while index < len(argv):
        argument = argv[index]
        if argument == "--json":
            output_json = True
            index += 1
        elif argument in {"--short-name", "--language"}:
            if index + 1 >= len(argv):
                raise DevoraScriptError(f"{argument} requires a value")
            if argument == "--short-name":
                short_name = argv[index + 1]
            else:
                language = argv[index + 1]
            index += 2
        else:
            raise DevoraScriptError(f"unknown argument {argument}")

    if short_name is None or not SHORT_NAME_RE.fullmatch(short_name):
        raise DevoraScriptError("--short-name must be lowercase kebab-case")
    return output_json, short_name, language


def create_feature(
    root: Path, short_name: str, language: str | None = None
) -> dict[str, str]:
    if language is None:
        options_path = root / ".devora" / "init-options.json"
        if options_path.is_file():
            options = load_json(options_path, label="initialization options")
            configured = options.get("artifact_language", "auto")
            language = configured if isinstance(configured, str) else "auto"
        else:
            language = "auto"
    if language not in LANGUAGES:
        raise DevoraScriptError("--language must be auto, zh-CN, or en")

    specs_dir = root / "specs"
    specs_dir.mkdir(parents=True, exist_ok=True)
    highest = 0
    for path in specs_dir.iterdir():
        match = FEATURE_RE.match(path.name)
        if path.is_dir() and match:
            highest = max(highest, int(match.group(1)))

    relative = Path("specs") / f"{highest + 1:03d}-{short_name}"
    feature_dir = root / relative
    if feature_dir.exists():
        raise DevoraScriptError(f"feature directory already exists: {feature_dir}")
    feature_dir.mkdir(parents=False)

    spec_file = feature_dir / "spec.md"
    intake_file = feature_dir / "intake.md"
    summary_file = feature_dir / "summary.md"
    copy_template_if_missing(root, "spec-template.md", spec_file)
    copy_template_if_missing(root, "intake-template.md", intake_file)
    copy_template_if_missing(root, "summary-template.md", summary_file)

    atomic_write_json(
        root / ".devora" / "feature.json",
        {
            "feature_directory": relative.as_posix(),
            "artifact_language": language,
        },
    )
    return {
        "FEATURE_DIR": str(feature_dir),
        "SPEC_FILE": str(spec_file),
        "INTAKE_FILE": str(intake_file),
        "SUMMARY_FILE": str(summary_file),
        "ARTIFACT_LANGUAGE": language,
    }


def main(argv: list[str] | None = None) -> int:
    try:
        output_json, short_name, language = parse_args(argv or sys.argv[1:])
        root = find_devora_root()
        result = create_feature(root, short_name, language)
        if output_json:
            print_json(result)
        else:
            print(
                "Created feature: "
                + Path(result["FEATURE_DIR"]).relative_to(root).as_posix()
            )
        return 0
    except (DevoraScriptError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
