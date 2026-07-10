---
name: sd-work-designs
description: "Use when the user wants to work through Trellis tasks that still need design.md and/or implement.md planning artifacts before they can be implemented."
---

# SD Work Designs

Use this project-local Software Delivery skill for `sd-work-designs` and
`/sd:work-designs` style work. It is a bounded planning loop over existing
Trellis tasks whose PRDs are real enough to design, but whose `design.md` or
`implement.md` artifacts are missing, placeholder-only, or clearly incomplete.

This command prepares implementation proposals and execution guidance. It does
not start tasks, implement product/code changes, create pull requests, merge
branches, or bypass Trellis planning rules.

## Safety Rules

- Work only on existing Trellis tasks. Do not create new tasks unless the user
  explicitly asks for task creation in the current session.
- Require a clean working tree before the first planning edit. If unrelated
  dirty files exist, stop and report them.
- Do not run `task.py start`, `sd-create-pr`, `sd-review-pr`, or
  `sd-housekeeping`; this command creates planning artifacts only.
- Do not overwrite existing user-authored task artifacts. If `design.md` or
  `implement.md` exists and has useful content, append a dated
  `SD Work Designs Proposal` section or fill only obvious placeholder sections.
- Do not create pull requests in the upstream `Trellis` repository without
  explicit approval from the user for that specific upstream PR. If planning
  identifies an upstream Trellis-owned change, write a paste-ready handoff in
  the local task artifact instead.
- If a task needs a user decision before a responsible proposal can be written,
  ask one blocking question, wait when the platform supports waiting, and park
  the task if no answer arrives.
- Stop instead of continuing if task artifact writes fail, if the working tree
  becomes ambiguous, or if all remaining design candidates require user input.

## Step 1: Resolve State And Backlog

Confirm repository state and Trellis context:

```bash
git status -sb
python3 ./.trellis/scripts/get_context.py --mode record
python3 ./.trellis/scripts/task.py list --mine
```

Inventory active task directories under `.trellis/tasks/`, excluding
`.trellis/tasks/archive/`. Prefer Trellis CLI output for the task list, then
inspect each candidate's `task.json`, `prd.md`, `design.md`, and
`implement.md`.

If the working tree is dirty before planning starts, classify the paths.
Continue only when the paths are already part of the current planning stream
and are safe to update. Otherwise stop and report the dirty paths.

## Step 2: Identify Design Candidates

A task is a design candidate only when all of these are true:

- `task.json.status` is `planning` or `in_progress`.
- `prd.md` exists and is not placeholder-only.
- `prd.md` contains a goal and either concrete requirements or acceptance
  criteria.
- `design.md` or `implement.md` is missing, placeholder-only, or clearly lacks
  enough detail for another session to implement safely.
- The task does not contain a current `Parked by sd-work-designs` note,
  unresolved blocking question, or explicit waiting-for-user marker.
- The missing planning can be produced from repository context, task artifacts,
  and source inspection without inventing product decisions.

Treat these as not actionable for this command:

- tasks missing a real PRD;
- tasks whose PRD explicitly says they are PRD-only or do not need additional
  design;
- tasks blocked by product judgment, credentials, external state, or user
  input;
- tasks explicitly parked by this command; and
- tasks whose design would require opening an upstream `Trellis` PR without
  the user's consent.

## Step 3: Rank Candidates

Rank candidates deterministically:

1. `in_progress` before `planning`.
2. Priority order `P0`, `P1`, `P2`, `P3`, then missing/unknown priority.
3. Tasks missing both `design.md` and `implement.md` before tasks missing only
   one artifact.
4. Tasks with clearer acceptance criteria before ambiguous PRDs.
5. Older `createdAt` date, then lexical task directory name.

Report the selected task and why it won. Also keep a skipped-task summary
grouped by reason.

## Step 4: Create Or Update `design.md`

For the selected task:

1. Re-read the task's `prd.md`, existing `design.md` if present, existing
   `implement.md` if present, and any files or specs referenced by the PRD.
2. Inspect only the smallest relevant source/doc set needed to ground the
   proposal. Prefer `rg`/`rg --files` when available; if ripgrep is missing,
   fall back to repository-native search such as `git grep` and
   `git ls-files`.
3. Create `design.md` when missing. If it exists, preserve the existing text
   and append or update a dated proposal section instead of rewriting the file.
4. Include enough implementation proposal detail for a future coding session:
   - chosen approach and why it fits the repo;
   - boundaries and non-goals;
   - affected files or modules;
   - data/config/command surfaces involved;
   - risks, edge cases, and reviewer-sensitive areas; and
   - validation strategy.
5. Mark unknowns explicitly. Do not fill gaps with guesses that change product
   scope.

Use this structure for a new `design.md` unless the repo/task already has a
clearer local pattern:

```markdown
# <Task Title> Design

## Overview

## Proposal

## Boundaries And Non-Goals

## Affected Files

## Risks And Edge Cases

## Validation
```

## Step 5: Create Or Update `implement.md`

Create or update `implement.md` after the design proposal is grounded:

1. Preserve existing useful content; append or fill placeholders rather than
   overwriting user-authored plans.
2. Convert the design into implementation guidance, not code changes.
3. Include an ordered execution plan, validation commands, docs/spec updates,
   review/PR considerations, and follow-up handling.
4. Make the first implementation step small enough for the next session to
   start confidently.
5. If the task should remain blocked until a decision is made, do not write a
   fake implementation path; park it via Step 6 instead.

Use this structure for a new `implement.md` unless the repo/task already has a
clearer local pattern:

```markdown
# <Task Title> Implementation Plan

## Execution Order

## Validation Plan

## Documentation And Spec Updates

## Review Notes

## Follow-Ups
```

## Step 6: Wait For User Input Or Park

When a task needs user input:

1. Ask one concise blocking question. Include the decision needed, why it
   matters, your recommended answer, and the tradeoff if the user chooses
   differently.
2. Wait up to 15 minutes when the current platform/session supports timed
   waiting. If the platform does not support timed waiting, state that and use
   the closest available explicit wait mechanism.
3. If the user answers, update the PRD/design/implement artifacts if the
   decision changes scope, then continue the same candidate.
4. If no answer arrives within the wait window, append a dated note to that
   task's `prd.md`:

   ```markdown
   ## Parked by sd-work-designs - YYYY-MM-DD

   - Blocking question: ...
   - Why it blocks: ...
   - Needed to resume: ...
   ```

5. Leave the task in its normal Trellis-compatible status and return to Step 1
   to find the next design candidate.

## Step 7: Repeat Or Stop

After each task artifact update:

1. Run `git diff --check` on the changed task artifacts when available.
2. Record the artifact paths created or updated.
3. Return to Step 1 and continue with the next highest-ranked design candidate.

Stop when:

- no active Trellis tasks remain;
- no remaining task needs `design.md` or `implement.md`;
- all remaining design candidates are parked or require user input;
- task artifacts are dirty in a way this command cannot classify; or
- continuing would exceed the current session's practical limits.

## Final Report

Report:

- tasks whose design artifacts were created or updated;
- tasks parked, including the blocking question and where the park note was
  written;
- tasks skipped, grouped by reason;
- final branch and working-tree state;
- open PRs/issues when relevant; and
- whether another `sd-work-designs` invocation would have actionable design
  work.

End with a numbered list of the added or updated design/implementation
documents. Link each local file path and include a one-line summary of the
proposal or implementation guidance added.
