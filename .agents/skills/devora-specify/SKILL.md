---
name: "devora-specify"
description: "Create a traceable, project-grounded feature specification from product materials and user intent."
compatibility: "Requires a Devora project with a .devora directory"
metadata:
  author: "devora"
  source: "templates/commands/specify.md"
---

# Specify

## User Input

```text
$ARGUMENTS
```

The input is the feature request plus any referenced or attached product materials. Inputs may include documents, images, designs, tickets, URLs, API descriptions, and prior requirements. If no request or source material is available, stop with `No feature description or requirement source provided`.

## Core Rule

Do not write the specification from a short prompt alone when richer materials are available. Inventory the sources, normalize product intent, understand the relevant existing system, and internally analyze likely impact. Devora defines this process; the connected agent uses its own document, image, browser, and repository tools.

## Procedure

1. Confirm `.devora/` exists.
2. Read `.devora/memory/constitution.md` when present.
3. Read `.devora/memory/project-context.md` when present. Treat it as a helpful snapshot, not as a substitute for current code.
4. Inventory every accessible requirement source before drawing conclusions. Do not claim to have read an inaccessible link, file, image, or design. Assign stable IDs such as `SRC-001` and record conflicts, gaps, versions, and access state.
5. Resolve the artifact language:
   - honor an explicit user choice first;
   - otherwise use the configured `artifact_language` from `.devora/init-options.json`;
   - when configured as `auto`, choose `zh-CN` or `en` from the dominant product input and keep that language for the entire feature;
   - preserve code identifiers, API names, exact contract strings, and product terminology in their source form.
6. Derive a concise 2-4 word kebab-case short name from the request and sources.
7. Run:

   ```bash
   python .devora/scripts/python/create_new_feature.py --json --short-name "<short-name>" --language "<zh-CN-or-en>"
   ```

   Use the returned `SPEC_FILE`, `INTAKE_FILE`, `SUMMARY_FILE`, and `FEATURE_DIR` for this invocation.
8. Read the intake, specification, and summary templates. Fill `intake.md` first with source traceability, product intent, conflicts, gaps, inferences, and decisions required.
9. Perform an internal, request-scoped impact analysis before filling the specification:
   - identify likely relevant projects, packages, modules, interfaces, and current behavior;
   - inspect real files needed to verify those relationships;
   - separate verified facts, reasonable inferences, and unresolved questions;
   - attach repository-relative evidence paths to important project claims;
   - detect conflicts between the request and the project's actual type or boundaries.
10. If an unresolved question materially changes scope, security, privacy, data behavior, compatibility, cross-project ownership, or user experience, ask the user before finalizing. Batch independent questions, give plain-language options and a recommendation, and never ask for discoverable technical facts.
11. Fill `spec.md`. Link requirements and acceptance criteria to source IDs where practical. Keep requirements centered on user and system behavior while recording enough existing-system context to prevent drift.
12. Update `summary.md` for the human requirement gate. It must explain scope, exclusions, material assumptions, decisions, risks, and one exact next action without requiring the user to read the detailed artifacts.
13. Validate that:
    - project claims have evidence or are explicitly marked as inferences;
    - scope and out-of-scope behavior are clear;
    - affected projects and cross-project behavior are captured when relevant;
    - requirements are testable;
    - implementation choices that belong in planning are not prematurely fixed;
    - no unresolved placeholder remains.
    - all feature artifacts use the locked artifact language except preserved source terms.

## Completion Report

Present the human summary first. If material decisions or assumptions remain unapproved, recommend `$devora-clarify`. Otherwise explain that invoking `$devora-plan` explicitly approves the presented requirement scope; the user can run clarify instead when changes are needed. Report source coverage and inaccessible materials without dumping machine-oriented detail.
