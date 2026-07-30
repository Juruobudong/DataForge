---
name: "devora-plan"
description: "Build a technical implementation plan grounded in the active spec and the real project structure."
compatibility: "Requires a Devora project with a .devora directory"
metadata:
  author: "devora"
  source: "templates/commands/plan.md"
---

# Plan

## User Input

```text
$ARGUMENTS
```

Use non-empty input as additional planning constraints.

## Procedure

1. Run `python .devora/scripts/python/setup_plan.py --json` and read the returned `SPEC_FILE`, `INTAKE_FILE`, `PLAN_FILE`, `TEST_CASES_FILE`, `SUMMARY_FILE`, and `FEATURE_DIR`. When upgrading an older active feature, backfill newly created intake/summary/test-case templates from the existing specification and verified project evidence before planning.
2. Read:
   - the active `spec.md`;
   - `intake.md` and its source IDs;
   - `.devora/memory/constitution.md` when present;
   - `.devora/memory/project-context.md` when present;
   - `.devora/templates/plan-template.md` and `.devora/templates/test-cases-template.md`.
3. Treat the user's explicit invocation of this command as approval of the current requirement summary only when no material decision remains open. Record the requirement gate as approved in `summary.md`. If scope, security, privacy, compatibility, or business behavior is still unresolved, stop and recommend `$devora-clarify` instead.
4. Re-open the real files cited by the spec when needed. Verify current structures, interfaces, conventions, and commands instead of planning against stale summaries.
5. Resolve technical unknowns through targeted project reading and documented research. Record genuine trade-offs in `research.md` when useful.
6. Write `plan.md` with:
   - concrete project/repository boundaries;
   - existing files and components to reuse;
   - proposed changes by project;
   - cross-project contracts and sequencing;
   - migration, compatibility, testing, and rollout considerations;
   - actual source paths and validation commands.
7. Fill `test-cases.md` as a first-class delivery artifact. Trace cases to requirements and sources, covering normal, failure, security, boundary, concurrency, compatibility, cross-project, E2E, and genuinely manual product/design checks. Record required environments and exit criteria.
8. Create only other design artifacts the feature needs, such as `research.md`, `data-model.md`, `contracts/`, or `quickstart.md`. Do not generate empty ceremonial files.
9. Update `summary.md` with the chosen direction, material trade-offs, testing scope, risks, and changes since requirement approval.
10. Run the constitution checks before and after design. Explicitly justify any violation.
11. Keep all artifacts in the locked feature language and do not implement business code.

Present the updated human summary first, then report produced artifacts, test coverage, project boundaries, key decisions, risks, and readiness for `$devora-tasks`.
