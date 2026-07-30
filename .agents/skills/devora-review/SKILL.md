---
name: "devora-review"
description: "Run an independent pre-PR delivery review and present a focused human approval packet."
compatibility: "Requires a Devora project with a .devora directory"
metadata:
  author: "devora"
  source: "templates/commands/review.md"
---

# Review

## User Input

```text
$ARGUMENTS
```

Use non-empty input as review focus, evidence supplied by the user, or an explicit approval/change request. This is the production PR gate. It reviews and records; it must not silently repair business source code.

## Procedure

1. Run `python .devora/scripts/python/setup_review.py --json` and read every returned artifact.
2. Read the intake sources, specification, plan, test cases, tasks, analysis, summary, constitution, project context, design artifacts, and the complete implementation diff.
3. Stop with `CHANGES_REQUIRED` when artifact analysis is not ready or required task dependencies are inconsistent. Do not treat a checked task as complete unless its implementation, validation, and dependency states satisfy the task rules.
4. Independently review the implementation for:
   - requirement and source traceability;
   - correctness, failure behavior, security, privacy, concurrency, compatibility, and cross-project contracts;
   - scope creep and deviations from approved product intent;
   - test quality and missing negative or end-to-end coverage;
   - project documentation, API, ADR, runbook, changelog, migration, and rollout impact.
5. Re-run or verify the final gates declared in `test-cases.md` and `tasks.md`. Do not rely only on implement-stage claims. Apply the long-running-operation rules from `$devora-implement`: ongoing progress is not a blocker, and alternatives must be equivalent.
6. Write `review.md` using the review template. Give findings stable IDs such as `R001`, exact paths, severity, impact, and required action.
7. Set review status:
   - `CHANGES_REQUIRED` for any open critical/high issue or failed required gate;
   - `BLOCKED` only for a concrete external dependency with an owner and next action;
   - `AWAITING_HUMAN` when AI review and required gates pass but human PR approval has not been given;
   - `APPROVED` only after the user explicitly approves this reviewed change set.
8. Update `summary.md` with delivery readiness, validation evidence, risk hotspots, known gaps, changes since requirement approval, and the human gate status.
9. When status is `AWAITING_HUMAN`, present a compact packet containing delivered behavior, material deviations, test evidence, risk hotspots, known gaps, and one decision: approve for PR or request changes. The user should not need to read all technical artifacts. Ask the user to invoke `$devora-review approve` or `$devora-review changes: <feedback>` so the decision is explicit and the review instructions are loaded again.
10. After explicit approval, record the decision in `review.md` and `summary.md`. Do not create, push, or merge a PR unless the user separately requests that external action.

## Completion Report

Present the human review brief first. If changes are required, recommend `$devora-implement` with exact finding IDs. If blocked, state the external action. If awaiting approval, ask only for the approval decision. If approved, state that the change set is PR-ready.
