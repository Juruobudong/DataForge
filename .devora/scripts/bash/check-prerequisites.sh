#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"

OUTPUT_JSON=false
REQUIRE_PLAN=false
REQUIRE_TASKS=false
REQUIRE_ANALYSIS=false
REQUIRE_TEST_CASES=false
REQUIRE_REVIEW=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --json) OUTPUT_JSON=true ;;
        --require-plan) REQUIRE_PLAN=true ;;
        --require-tasks) REQUIRE_TASKS=true ;;
        --require-analysis) REQUIRE_ANALYSIS=true ;;
        --require-test-cases) REQUIRE_TEST_CASES=true ;;
        --require-review) REQUIRE_REVIEW=true ;;
        *) printf 'Error: unknown argument %s\n' "$1" >&2; exit 1 ;;
    esac
    shift
done

ROOT="$(find_devora_root)"
FEATURE_DIR="$(feature_dir_from_state "$ROOT")"
SPEC_FILE="$FEATURE_DIR/spec.md"
PLAN_FILE="$FEATURE_DIR/plan.md"
TASKS_FILE="$FEATURE_DIR/tasks.md"
ANALYSIS_FILE="$FEATURE_DIR/analysis.md"
INTAKE_FILE="$FEATURE_DIR/intake.md"
SUMMARY_FILE="$FEATURE_DIR/summary.md"
TEST_CASES_FILE="$FEATURE_DIR/test-cases.md"
REVIEW_FILE="$FEATURE_DIR/review.md"

[[ -f "$SPEC_FILE" ]] || { printf 'Error: missing feature specification: %s\n' "$SPEC_FILE" >&2; exit 1; }
if [[ "$REQUIRE_PLAN" == true && ! -f "$PLAN_FILE" ]]; then
    printf 'Error: missing implementation plan: %s\n' "$PLAN_FILE" >&2
    exit 1
fi
if [[ "$REQUIRE_TASKS" == true && ! -f "$TASKS_FILE" ]]; then
    printf 'Error: missing task list: %s\n' "$TASKS_FILE" >&2
    exit 1
fi
if [[ "$REQUIRE_ANALYSIS" == true && ! -f "$ANALYSIS_FILE" ]]; then
    printf 'Error: missing analysis report: %s\n' "$ANALYSIS_FILE" >&2
    exit 1
fi
if [[ "$REQUIRE_TEST_CASES" == true && ! -f "$TEST_CASES_FILE" ]]; then
    printf 'Error: missing test cases: %s\n' "$TEST_CASES_FILE" >&2
    exit 1
fi
if [[ "$REQUIRE_REVIEW" == true && ! -f "$REVIEW_FILE" ]]; then
    printf 'Error: missing review report: %s\n' "$REVIEW_FILE" >&2
    exit 1
fi

AVAILABLE_DOCS="$(available_design_docs "$FEATURE_DIR")"
if [[ "$OUTPUT_JSON" == true ]]; then
    printf '{"FEATURE_DIR":%s,"SPEC_FILE":%s,"PLAN_FILE":%s,"TASKS_FILE":%s,"ANALYSIS_FILE":%s,"INTAKE_FILE":%s,"SUMMARY_FILE":%s,"TEST_CASES_FILE":%s,"REVIEW_FILE":%s,"AVAILABLE_DOCS":%s}\n' \
        "$(json_string "$FEATURE_DIR")" \
        "$(json_string "$SPEC_FILE")" \
        "$(json_string "$PLAN_FILE")" \
        "$(json_string "$TASKS_FILE")" \
        "$(json_string "$ANALYSIS_FILE")" \
        "$(json_string "$INTAKE_FILE")" \
        "$(json_string "$SUMMARY_FILE")" \
        "$(json_string "$TEST_CASES_FILE")" \
        "$(json_string "$REVIEW_FILE")" \
        "$(json_string "$AVAILABLE_DOCS")"
else
    printf 'Feature directory: %s\n' "$FEATURE_DIR"
fi
