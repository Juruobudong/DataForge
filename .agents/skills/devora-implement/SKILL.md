---
name: "devora-implement"
description: "Implement the active feature by following its grounded plan and task list."
compatibility: "Requires a Devora project with a .devora directory"
metadata:
  author: "devora"
  source: "templates/commands/implement.md"
---

# Implement

## User Input

```text
$ARGUMENTS
```

Use non-empty input as a task filter or additional implementation constraint.

## Procedure

1. Run `python .devora/scripts/python/check_prerequisites.py --json --require-plan --require-tasks --require-test-cases`.
2. Read `summary.md`, `tasks.md`, `test-cases.md`, `plan.md`, `spec.md`, `intake.md`, all available design artifacts, the constitution, and project context.
3. If `analysis.md` exists, read it before changing code. Accept `Artifact Readiness` or the legacy `Result` field. Stop when readiness is `NOT_READY` or it contains any `OPEN`, `AWAITING_USER`, or `EXTERNAL_BLOCKED` finding. Recommend `$devora-resolve` instead. A `PARTIAL` or `UNKNOWN` execution environment is not automatically an artifact blocker; preflight it explicitly.
4. Before source changes, perform an execution preflight for required commands, services, credentials, test data, browsers, containers, and network downloads. Record what is `VERIFIED`, `AVAILABLE_BUT_UNVERIFIED`, or `MISSING`; do not confuse correct command syntax with a usable environment.
5. Before changing a task's target, inspect the current real files. Prior artifacts guide the work but do not override newer code.
6. Execute tasks in dependency order. In a multi-project feature, respect contract-producer and consumer sequencing.
7. Keep changes within the task's declared project and scope unless a newly discovered dependency makes that impossible. If the expansion is material, stop and explain the evidence before broadening scope.
8. Update each task's two states independently:
   - set `Implementation: DONE` when the required source or artifact work exists, or `N/A` for validation-only work;
   - set `Validation: PASSED` only after its declared evidence succeeds;
   - set `Validation: BLOCKED` only with an exact owner, cause, and next action;
   - mark `[X]` only when implementation is `DONE` or `N/A`, validation is `PASSED`, and every dependency is complete.
9. If safe independent work can proceed while a dependency's validation is blocked, record that work without marking the dependent task complete. Never report a dependency chain as complete out of order.
10. Treat long-running operations carefully:
    - active download/build/test progress without an error remains `IN_PROGRESS`, not `BLOCKED`;
    - do not stop solely because a first-time image or dependency download is slow;
    - use an alternative validation path only when it is materially equivalent, and state the evidence;
    - mark external blocking only after a concrete failure or when an explicit user/external action is required.
11. Re-check cross-project contracts, test-case coverage, documentation tasks, and end-to-end acceptance scenarios before completion.
12. Refresh `summary.md` with separate implementation, validation, completion, and blocked counts plus one exact resume or review action.

Present the human summary first. Report completed and remaining tasks, files changed by project, validation evidence, deviations, and unresolved risks. If every required task is complete, recommend `$devora-review`; otherwise give the exact command or external action needed before re-running `$devora-implement`.
