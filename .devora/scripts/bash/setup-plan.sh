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
TEST_CASES_FILE="$FEATURE_DIR/test-cases.md"
SUMMARY_FILE="$FEATURE_DIR/summary.md"
INTAKE_FILE="$FEATURE_DIR/intake.md"

[[ -f "$SPEC_FILE" ]] || { printf 'Error: missing feature specification: %s\n' "$SPEC_FILE" >&2; exit 1; }
[[ -f "$INTAKE_FILE" ]] || cp "$ROOT/.devora/templates/intake-template.md" "$INTAKE_FILE"
[[ -f "$SUMMARY_FILE" ]] || cp "$ROOT/.devora/templates/summary-template.md" "$SUMMARY_FILE"
[[ -f "$PLAN_FILE" ]] || cp "$ROOT/.devora/templates/plan-template.md" "$PLAN_FILE"
[[ -f "$TEST_CASES_FILE" ]] || cp "$ROOT/.devora/templates/test-cases-template.md" "$TEST_CASES_FILE"

if [[ "$OUTPUT_JSON" == true ]]; then
    printf '{"FEATURE_DIR":%s,"SPEC_FILE":%s,"INTAKE_FILE":%s,"PLAN_FILE":%s,"TEST_CASES_FILE":%s,"SUMMARY_FILE":%s}\n' \
        "$(json_string "$FEATURE_DIR")" \
        "$(json_string "$SPEC_FILE")" \
        "$(json_string "$INTAKE_FILE")" \
        "$(json_string "$PLAN_FILE")" \
        "$(json_string "$TEST_CASES_FILE")" \
        "$(json_string "$SUMMARY_FILE")"
else
    printf 'Plan file: %s\n' "$PLAN_FILE"
fi
