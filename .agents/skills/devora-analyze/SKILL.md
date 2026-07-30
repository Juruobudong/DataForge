---
name: "devora-analyze"
description: "Analyze the active feature for consistency, persist actionable risks, and route each finding to the right owner."
compatibility: "Requires a Devora project with a .devora directory"
metadata:
  author: "devora"
  source: "templates/commands/analyze.md"
---

# Analyze

## User Input

```text
$ARGUMENTS
```

Use non-empty input as an analysis focus. This command reviews workflow artifacts and project evidence; it must not modify business code or authoritative requirement, plan, contract, test-case, or task content. It may write `analysis.md` and refresh the derived human `summary.md` only.

## Procedure

1. Run `python .devora/scripts/python/check_prerequisites.py --json --require-plan --require-tasks --require-test-cases`.
2. Read the returned `intake.md`, `spec.md`, `plan.md`, `test-cases.md`, `tasks.md`, `summary.md`, all available design artifacts, the constitution, and project context.
3. Read `.devora/templates/analysis-template.md`.
4. Check the real project when artifact claims, paths, commands, test isolation, contracts, or current behavior need verification.
5. Check for:
   - requirements with no task coverage;
   - requirement sources with no traceability and test cases with no requirement mapping;
   - tasks unsupported by a requirement or plan decision;
   - conflicts with verified project context or constitution rules;
   - cross-project contract or ordering gaps;
   - vague tasks, incorrect paths or commands, missing validation, and unsafe parallel markers;
   - acceptance, failure, concurrency, security, and compatibility behavior without tests;
   - stale claims that no longer match current code.
   - required execution environments that are missing, unverified, or incorrectly described.
6. Give every finding:
   - a stable ID such as `A001`;
   - severity: `CRITICAL`, `HIGH`, `MEDIUM`, or `LOW`;
   - owner: `AGENT`, `USER`, or `EXTERNAL`;
   - resolution kind: `AUTO_FIX`, `USER_DECISION`, `EXTERNAL_BLOCKED`, or `ACCEPTED_RISK`;
   - status: `OPEN`, `AWAITING_USER`, `RESOLVED`, `ACCEPTED`, or `EXTERNAL_BLOCKED`;
   - evidence with exact artifact sections or repository-relative paths;
   - impact and a concrete resolution action.
7. Apply these routing rules:
   - Use `AUTO_FIX` for commands, paths, coverage gaps, task dependencies, parallel markers, artifact inconsistency, and technical choices already determined by project evidence. These do not require user judgment.
   - Use `USER_DECISION` only for material product behavior, scope, security/privacy policy, compatibility trade-offs, or business rules that cannot be discovered from the project.
   - Use `EXTERNAL_BLOCKED` only when credentials, permissions, infrastructure, or another external actor is genuinely required to make the workflow artifacts correct. A delivery environment that is merely unavailable during analysis belongs in the separate execution-environment status and does not by itself make artifact readiness `NOT_READY`.
   - Never route a discoverable technical fact or routine engineering repair to the user.
8. Write or replace `ANALYSIS_FILE` from the prerequisite output, using `.devora/templates/analysis-template.md`, and refresh `summary.md` with the finding counts and next action. These are the only permitted file changes.
9. Set two separate readiness results:
   - `READY` only when no blocking finding remains open;
   - `NOT_READY` when any open critical/high artifact finding, awaiting user decision, or artifact-level external blocker remains.
   - set execution environment to `VERIFIED`, `PARTIAL`, or `UNKNOWN`; do not claim environment executability merely because command syntax is correct.
10. Summarize separately:
    - items the Agent can resolve automatically;
    - decisions the user must make;
    - external blockers;
    - accepted risks.

## Completion Report

Do not merely tell the user to "fix the findings." State the exact next action:

- if automatic or user-decision findings exist, recommend `$devora-resolve`;
- if only external blockers exist, name the required external action;
- if artifact readiness is `READY`, recommend `$devora-implement` and state the independent execution-environment status.

Present the human summary first. Report the path to `analysis.md` and confirm that no source code or authoritative workflow artifact was modified. `$devora-resolve` already re-runs analysis, so never tell the user to run analyze again after resolve.
