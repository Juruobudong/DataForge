"""Shared helpers for Devora's project-local workflow scripts."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


class DevoraScriptError(RuntimeError):
    """A user-facing workflow script failure."""


def find_devora_root(start: Path | None = None) -> Path:
    current = (start or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".devora").is_dir():
            return candidate
    raise DevoraScriptError(f"no .devora directory found from {current} upward")


def load_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise DevoraScriptError(f"missing {label}: {path}") from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DevoraScriptError(f"invalid {label}: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise DevoraScriptError(f"invalid {label}: expected a JSON object: {path}")
    return value


def feature_dir_from_state(root: Path) -> Path:
    state_path = root / ".devora" / "feature.json"
    if not state_path.is_file():
        raise DevoraScriptError("no active feature. Run the specify command first.")

    state = load_json(state_path, label="active feature state")
    raw_directory = state.get("feature_directory")
    if not isinstance(raw_directory, str) or not raw_directory.strip():
        raise DevoraScriptError(
            "invalid active feature state: feature_directory must be a string"
        )

    relative = Path(raw_directory)
    if relative.is_absolute() or ".." in relative.parts:
        raise DevoraScriptError("unsafe feature_directory in .devora/feature.json")

    resolved = (root / relative).resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise DevoraScriptError("feature_directory escapes the workspace root") from exc
    return resolved


def available_design_docs(feature_dir: Path) -> str:
    docs: list[str] = []
    for name in ("research.md", "data-model.md", "quickstart.md"):
        if (feature_dir / name).is_file():
            docs.append(name)
    if (feature_dir / "contracts").is_dir():
        docs.append("contracts/")
    return ",".join(docs)


def copy_template_if_missing(root: Path, template: str, destination: Path) -> None:
    if destination.exists():
        if not destination.is_file() or destination.is_symlink():
            raise DevoraScriptError(
                f"refusing to replace non-file artifact: {destination}"
            )
        return
    source = root / ".devora" / "templates" / template
    if not source.is_file():
        raise DevoraScriptError(f"missing workflow template: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(source.read_bytes())


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def print_json(value: dict[str, Any]) -> None:
    print(json.dumps(value, ensure_ascii=False, separators=(",", ":")))


def require_file(path: Path, label: str) -> None:
    if not path.is_file():
        raise DevoraScriptError(f"missing {label}: {path}")
