#!/usr/bin/env bash

set -euo pipefail

find_devora_root() {
    local current
    current="$(pwd)"
    while [[ "$current" != "/" ]]; do
        if [[ -d "$current/.devora" ]]; then
            printf '%s\n' "$current"
            return 0
        fi
        current="$(dirname "$current")"
    done
    printf 'Error: no .devora directory found from %s upward\n' "$(pwd)" >&2
    return 1
}

json_string() {
    python3 -c 'import json, sys; print(json.dumps(sys.argv[1]))' "$1"
}

feature_dir_from_state() {
    local root="$1"
    local state="$root/.devora/feature.json"
    if [[ ! -f "$state" ]]; then
        printf 'Error: no active feature. Run the specify command first.\n' >&2
        return 1
    fi
    python3 - "$root" "$state" <<'PY'
import json
import pathlib
import sys

root = pathlib.Path(sys.argv[1]).resolve()
state = pathlib.Path(sys.argv[2])
data = json.loads(state.read_text(encoding="utf-8"))
relative = pathlib.Path(data["feature_directory"])
if relative.is_absolute() or ".." in relative.parts:
    raise SystemExit("Error: unsafe feature_directory in .devora/feature.json")
resolved = (root / relative).resolve()
try:
    resolved.relative_to(root)
except ValueError:
    raise SystemExit("Error: feature_directory escapes the workspace root")
print(resolved)
PY
}

available_design_docs() {
    local feature_dir="$1"
    local joined=""

    append_doc() {
        local name="$1"
        [[ -n "$joined" ]] && joined+=","
        joined+="$name"
    }

    [[ -f "$feature_dir/research.md" ]] && append_doc "research.md"
    [[ -f "$feature_dir/data-model.md" ]] && append_doc "data-model.md"
    [[ -f "$feature_dir/quickstart.md" ]] && append_doc "quickstart.md"
    [[ -d "$feature_dir/contracts" ]] && append_doc "contracts/"
    printf '%s\n' "$joined"
}
