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
ANALYSIS_FILE="$FEATURE_DIR/analysis.md"
TEST_CASES_FILE="$FEATURE_DIR/test-cases.md"
SUMMARY_FILE="$FEATURE_DIR/summary.md"
REVIEW_FILE="$FEATURE_DIR/review.md"

for required in "$SPEC_FILE" "$PLAN_FILE" "$TASKS_FILE" "$ANALYSIS_FILE" "$TEST_CASES_FILE"; do
    [[ -f "$required" ]] || { printf 'Error: missing required artifact: %s\n' "$required" >&2; exit 1; }
done

[[ -f "$REVIEW_FILE" ]] || cp "$ROOT/.devora/templates/review-template.md" "$REVIEW_FILE"

if [[ "$OUTPUT_JSON" == true ]]; then
    printf '{"FEATURE_DIR":%s,"SPEC_FILE":%s,"PLAN_FILE":%s,"TASKS_FILE":%s,"ANALYSIS_FILE":%s,"TEST_CASES_FILE":%s,"SUMMARY_FILE":%s,"REVIEW_FILE":%s}\n' \
        "$(json_string "$FEATURE_DIR")" \
        "$(json_string "$SPEC_FILE")" \
        "$(json_string "$PLAN_FILE")" \
        "$(json_string "$TASKS_FILE")" \
        "$(json_string "$ANALYSIS_FILE")" \
        "$(json_string "$TEST_CASES_FILE")" \
        "$(json_string "$SUMMARY_FILE")" \
        "$(json_string "$REVIEW_FILE")"
else
    printf 'Review file: %s\n' "$REVIEW_FILE"
fi
