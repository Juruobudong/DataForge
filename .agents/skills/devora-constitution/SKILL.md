---
name: "devora-constitution"
description: "Create or update the project's durable development principles and governance rules."
compatibility: "Requires a Devora project with a .devora directory"
metadata:
  author: "devora"
  source: "templates/commands/constitution.md"
---

# Constitution

## User Input

```text
$ARGUMENTS
```

## Procedure

1. Read `.devora/memory/constitution.md` and `.devora/templates/constitution-template.md`.
2. If `.devora/memory/project-context.md` exists, read it so principles match the actual project boundaries.
3. Convert the user's principles into clear, testable rules. Resolve template placeholders and keep rules technology-specific only when the user or project context requires it.
4. Preserve existing ratification history where possible and apply semantic versioning to constitution changes:
   - major for removed or incompatible principles;
   - minor for new principles or materially expanded governance;
   - patch for clarification without changed meaning.
5. Update `.devora/memory/constitution.md` only. Do not change business code.
6. Check the Devora templates for obvious contradictions and report them; do not silently rewrite other templates.

Finish with the new constitution version, key changes, and any follow-up decisions.
