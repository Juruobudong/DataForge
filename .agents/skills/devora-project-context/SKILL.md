---
name: "devora-project-context"
description: "Initialize or refresh a durable, evidence-based snapshot of the current project workspace."
compatibility: "Requires a Devora project with a .devora directory"
metadata:
  author: "devora"
  source: "templates/commands/project-context.md"
---

# Project Context

## User Input

```text
$ARGUMENTS
```

Use any non-empty input as focus or scope guidance. This command is optional and must not modify business code.

## Goal

Help later Devora stages enter an existing project with stable context. You, the coding agent, are responsible for reading and understanding the workspace. Devora does not provide a code index or scanner.

## Procedure

1. Confirm `.devora/` exists at the workspace root. If it does not, stop and ask the user to run `devora init`.
2. Read `.devora/templates/project-context-template.md`.
3. If `.devora/memory/constitution.md` contains real project rules, read it.
4. Inspect the current workspace deeply enough to identify:
   - projects or packages and their responsibilities;
   - project types, languages, frameworks, and entrypoints;
   - important routes, interfaces, schemas, data boundaries, and shared packages;
   - build, test, lint, and run commands;
   - architectural and ownership boundaries that constrain future changes;
   - relationships among projects in a multi-project workspace.
5. Prefer facts verified from real files. Attach repository-relative file paths as evidence for important claims.
6. Mark uncertain statements as `Inference` or `Unknown`; never present guesses as verified facts.
7. Do not perform exhaustive reading when representative entrypoints and configuration files establish the same fact. Follow references only as needed to understand boundaries.
8. Write or refresh `.devora/memory/project-context.md` using the template.
9. If the file already exists, refresh stale verified sections while preserving content under `## Human Notes` unless the user explicitly asks to change it.

## Completion Report

Report:

- projects and boundaries discovered;
- the strongest evidence paths;
- unresolved unknowns;
- the output file path;
- confirmation that no business code was changed.

Recommend `$devora-constitution` if governance rules are still placeholders, otherwise recommend `$devora-specify <feature description>`.
