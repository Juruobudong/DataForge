#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"

OUTPUT_JSON=false
[[ "${1:-}" == "--json" ]] && OUTPUT_JSON=true

ROOT="$(find_devora_root)"
FEATURE_DIR="$(feature_dir_from_state "$ROOT")"
SPEC_FILE="$FEATURE_DIR/spec.md"
PLAN_FILE="$FEATURE_DIR/plan.md"
TASKS_FILE="$FEATURE_DIR/tasks.md"
TEST_CASES_FILE="$FEATURE_DIR/test-cases.md"
SUMMARY_FILE="$FEATURE_DIR/summary.md"

[[ -f "$SPEC_FILE" ]] || { printf 'Error: missing feature specification: %s\n' "$SPEC_FILE" >&2; exit 1; }
[[ -f "$PLAN_FILE" ]] || { printf 'Error: missing implementation plan: %s\n' "$PLAN_FILE" >&2; exit 1; }
[[ -f "$TEST_CASES_FILE" ]] || { printf 'Error: missing test cases: %s\n' "$TEST_CASES_FILE" >&2; exit 1; }
[[ -f "$TASKS_FILE" ]] || cp "$ROOT/.devora/templates/tasks-template.md" "$TASKS_FILE"

AVAILABLE_DOCS="$(available_design_docs "$FEATURE_DIR")"
if [[ "$OUTPUT_JSON" == true ]]; then
    printf '{"FEATURE_DIR":%s,"SPEC_FILE":%s,"PLAN_FILE":%s,"TASKS_FILE":%s,"TEST_CASES_FILE":%s,"SUMMARY_FILE":%s,"AVAILABLE_DOCS":%s}\n' \
        "$(json_string "$FEATURE_DIR")" \
        "$(json_string "$SPEC_FILE")" \
        "$(json_string "$PLAN_FILE")" \
        "$(json_string "$TASKS_FILE")" \
        "$(json_string "$TEST_CASES_FILE")" \
        "$(json_string "$SUMMARY_FILE")" \
        "$(json_string "$AVAILABLE_DOCS")"
else
    printf 'Tasks file: %s\n' "$TASKS_FILE"
fi
