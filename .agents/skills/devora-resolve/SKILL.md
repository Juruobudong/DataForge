---
name: "devora-resolve"
description: "Resolve an analysis report by automatically repairing workflow artifacts and asking users only for true product decisions."
compatibility: "Requires a Devora project with a .devora directory"
metadata:
  author: "devora"
  source: "templates/commands/resolve.md"
---

# Resolve

## User Input

```text
$ARGUMENTS
```

Use non-empty input as resolution guidance or as answers to previously asked decision questions. This command may update feature workflow artifacts, but it must not modify business source code.

## Procedure

1. Run `python .devora/scripts/python/check_prerequisites.py --json --require-plan --require-tasks --require-analysis --require-test-cases`.
2. Read `analysis.md`, `intake.md`, `spec.md`, `plan.md`, `test-cases.md`, `tasks.md`, `summary.md`, all design artifacts, the constitution, and project context.
3. Verify each open finding against the current artifacts and project evidence. Mark already-correct or stale findings `RESOLVED` with a short explanation.
4. Resolve every `AUTO_FIX` finding without asking the user:
   - update the authoritative artifact that owns the problem;
   - keep intake traceability, requirements, plan, test cases, contracts, and tasks mutually consistent;
   - add or correct validation tasks when coverage is missing;
   - preserve user-authored intent and avoid expanding feature scope;
   - record changed files and the resolution under the finding in `analysis.md`.
5. For `USER_DECISION` findings:
   - combine independent questions into one batch, with at most five questions;
   - ask dependent questions only after their prerequisite answer is known;
   - give plain-language context, verified project evidence, 2-3 concrete options, implications, and one recommended option;
   - allow `Use the recommendation` and custom answers;
   - never ask the user which document or technical path to edit;
   - wait for the user's answers before applying those decisions.
6. After the user answers, update every affected authoritative artifact. Product behavior belongs in `spec.md`; technical consequences in `plan.md` or research; test intent in `test-cases.md`; API behavior in contracts; executable work in `tasks.md`. Record the decision and affected files in `analysis.md`.
7. For `EXTERNAL_BLOCKED`, state the exact credential, permission, infrastructure, or external action required. Do not pretend it was resolved.
8. An `ACCEPTED_RISK` requires explicit user acceptance. Record the reason, impact, mitigation, and follow-up location; never infer acceptance from silence.
9. Re-run the full consistency checks from `$devora-analyze` after changes and refresh `analysis.md`:
   - preserve stable finding IDs and resolution history;
   - add new findings only when the resolution uncovered a genuinely new issue;
   - set the result to `READY` only when no blocking finding remains.
10. Refresh `summary.md` in the locked feature language with resolved findings, user decisions, environment status, and one next action.

## Completion Report

Report:

- automatically resolved findings and files changed;
- user decisions applied;
- unresolved external blockers or accepted risks;
- the refreshed analysis result;
- confirmation that no business source code was modified.

Present the human summary first. If artifact readiness is `READY`, recommend `$devora-implement`. Otherwise, state the next required user or external action without asking the user to edit documents manually. Do not recommend a redundant analyze run because resolution already includes re-analysis.
