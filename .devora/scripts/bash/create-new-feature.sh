#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"

OUTPUT_JSON=false
SHORT_NAME=""
LANGUAGE=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --json)
            OUTPUT_JSON=true
            shift
            ;;
        --short-name)
            [[ $# -ge 2 ]] || { printf 'Error: --short-name requires a value\n' >&2; exit 1; }
            SHORT_NAME="$2"
            shift 2
            ;;
        --language)
            [[ $# -ge 2 ]] || { printf 'Error: --language requires a value\n' >&2; exit 1; }
            LANGUAGE="$2"
            shift 2
            ;;
        *)
            printf 'Error: unknown argument %s\n' "$1" >&2
            exit 1
            ;;
    esac
done

if [[ ! "$SHORT_NAME" =~ ^[a-z0-9]+(-[a-z0-9]+)*$ ]]; then
    printf 'Error: --short-name must be lowercase kebab-case\n' >&2
    exit 1
fi

ROOT="$(find_devora_root)"
SPECS_DIR="$ROOT/specs"
mkdir -p "$SPECS_DIR"

if [[ -z "$LANGUAGE" ]]; then
    LANGUAGE="$(python3 - "$ROOT/.devora/init-options.json" <<'PY'
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
if not path.is_file():
    print("auto")
else:
    data = json.loads(path.read_text(encoding="utf-8"))
    print(data.get("artifact_language", "auto"))
PY
)"
fi

case "$LANGUAGE" in
    auto|zh-CN|en) ;;
    *) printf 'Error: --language must be auto, zh-CN, or en\n' >&2; exit 1 ;;
esac

highest=0
shopt -s nullglob
for path in "$SPECS_DIR"/[0-9][0-9][0-9]-*; do
    base="$(basename "$path")"
    number="${base%%-*}"
    number=$((10#$number))
    (( number > highest )) && highest=$number
done
shopt -u nullglob

next=$((highest + 1))
prefix="$(printf '%03d' "$next")"
relative="specs/$prefix-$SHORT_NAME"
feature_dir="$ROOT/$relative"

if [[ -e "$feature_dir" ]]; then
    printf 'Error: feature directory already exists: %s\n' "$feature_dir" >&2
    exit 1
fi

mkdir -p "$feature_dir"
cp "$ROOT/.devora/templates/spec-template.md" "$feature_dir/spec.md"
cp "$ROOT/.devora/templates/intake-template.md" "$feature_dir/intake.md"
cp "$ROOT/.devora/templates/summary-template.md" "$feature_dir/summary.md"

python3 - "$ROOT/.devora/feature.json" "$relative" "$LANGUAGE" <<'PY'
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
path.write_text(
    json.dumps(
        {
            "feature_directory": sys.argv[2],
            "artifact_language": sys.argv[3],
        },
        indent=2,
    )
    + "\n",
    encoding="utf-8",
)
PY

if [[ "$OUTPUT_JSON" == true ]]; then
    printf '{"FEATURE_DIR":%s,"SPEC_FILE":%s,"INTAKE_FILE":%s,"SUMMARY_FILE":%s,"ARTIFACT_LANGUAGE":%s}\n' \
        "$(json_string "$feature_dir")" \
        "$(json_string "$feature_dir/spec.md")" \
        "$(json_string "$feature_dir/intake.md")" \
        "$(json_string "$feature_dir/summary.md")" \
        "$(json_string "$LANGUAGE")"
else
    printf 'Created feature: %s\n' "$relative"
fi
