---
name: "devora-clarify"
description: "Resolve high-impact ambiguities in the active feature specification."
compatibility: "Requires a Devora project with a .devora directory"
metadata:
  author: "devora"
  source: "templates/commands/clarify.md"
---

# Clarify

## User Input

```text
$ARGUMENTS
```

## Procedure

1. Run `python .devora/scripts/python/check_prerequisites.py --json` and read the returned `SPEC_FILE`.
2. Read `intake.md`, `summary.md`, `.devora/memory/constitution.md`, and `.devora/memory/project-context.md` when they exist. Preserve the feature's locked artifact language.
3. Inspect real project files when an answer is discoverable from the workspace. Do not ask the user to supply discoverable technical facts.
4. Identify only ambiguities whose answers materially affect scope, behavior, security, data, compatibility, or cross-project responsibility.
5. Ask at most five focused questions in one round, recommending a default where evidence supports one.
6. After the user answers, update the relevant sections of `intake.md` and `spec.md`; do not create a separate transcript file.
7. Update `summary.md` with the approved scope, user decisions, remaining assumptions, and requirement-gate status.
8. Re-check that sources, project facts, inferences, assumptions, and user decisions remain distinguishable.

Report the resolved questions in plain language and ask for one explicit requirement approval when no material ambiguity remains. After approval, set the summary human gate to `APPROVED` and report readiness for `$devora-plan`.
