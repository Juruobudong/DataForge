# Tasks: [FEATURE NAME]

**Input**: `spec.md`, `plan.md`, and feature design artifacts

## Format

```text
- [ ] T001 [P?] [Project] [Story?] Action in exact/path — Depends on: IDs or None — Implementation: NOT_STARTED — Validation: PENDING — Validate: command or observable result
```

- `[P]` means the task is safe to execute in parallel.
- `[Project]` is required when the feature spans multiple projects.
- `[Story]` links the task to a user story when applicable.
- Dependencies must be written explicitly when ordering matters.
- `Implementation` is `NOT_STARTED`, `DONE`, or `N/A` for validation-only work.
- `Validation` is `PENDING`, `PASSED`, `FAILED`, or `BLOCKED` with an owner and exact next action when blocked.
- `[X]` means the task's implementation is `DONE` or `N/A`, validation is `PASSED`, and every dependency is complete.

## Phase 1: Shared Contracts and Prerequisites

- [ ] T001 [Project] [Description with exact path] — Depends on: None — Implementation: NOT_STARTED — Validation: PENDING — Validate: [command/result]

## Phase 2: User Story 1 - [Title]

**Goal**: [Deliverable]
**Independent Test**: [Verification]

- [ ] T002 [Project] [US1] [Description with exact path] — Depends on: T001 — Implementation: NOT_STARTED — Validation: PENDING — Validate: [command/result]

## Phase 3: Cross-Project Integration

- [ ] T003 [Project] [Description] — Depends on: T002 — Implementation: NOT_STARTED — Validation: PENDING — Validate: [contract/e2e result]

## Phase 4: Final Validation

- [ ] T004 [Project/Workspace] Run [validation command or acceptance scenario] — Depends on: T001-T003 — Implementation: N/A — Validation: PENDING — Validate: [final gates]

## Status Summary

| Metric | Count |
|--------|-------|
| Implementation done | 0 |
| Validation passed | 0 |
| Fully completed | 0 |
| Blocked | 0 |

## Dependency Summary

[Critical path and project handoffs]

## Parallel Opportunities

[Only tasks with independent files and satisfied dependencies]
