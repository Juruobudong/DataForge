---
name: "devora-tasks"
description: "Generate executable, dependency-aware tasks from the active spec and plan."
compatibility: "Requires a Devora project with a .devora directory"
metadata:
  author: "devora"
  source: "templates/commands/tasks.md"
---

# Tasks

## User Input

```text
$ARGUMENTS
```

## Procedure

1. Run `python .devora/scripts/python/setup_tasks.py --json`.
2. Read the active `intake.md`, `spec.md`, `plan.md`, `test-cases.md`, and all design artifacts listed by the script.
3. Read `.devora/memory/project-context.md` and `.devora/memory/constitution.md` when present.
4. Write `tasks.md` using `.devora/templates/tasks-template.md`.
5. Every task must be directly executable by a coding agent and include:
   - a stable task ID;
   - the owning project or repository when more than one project is involved;
   - an exact file path or a clearly bounded discovery target;
   - dependencies when ordering matters;
   - a concrete completion and validation condition;
   - `Implementation: NOT_STARTED | DONE | N/A`;
   - `Validation: PENDING | PASSED | FAILED | BLOCKED`.
6. Organize work by project and delivery slice without losing the end-to-end user story. Make shared contract changes explicit and place them before dependent consumers.
7. A task checkbox represents full completion only. It may become `[X]` only when implementation is `DONE` or `N/A`, validation is `PASSED`, and every declared dependency is complete.
8. Mark parallel tasks only when they touch independent files and have no unmet dependency.
9. Include documentation updates and final review prerequisites when the plan identifies them.
10. Update `summary.md` with task totals, critical path, manual checks, and delivery risks.
11. Do not invent projects, layers, or setup work that the plan and real workspace do not require. Preserve the locked artifact language and do not implement business code.

Present the human summary first. Report task count, project distribution, critical dependency chain, validation environments, and readiness for `$devora-analyze`. Do not recommend skipping analysis in production flow.
