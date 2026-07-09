---
name: sd-work-backlog
description: "Use when the user wants to work through the existing Trellis task backlog one task at a time: choose the highest-value implementation-ready task, complete it through PR review and housekeeping, handle follow-ups, then repeat until no actionable tasks remain."
---

# SD Work Backlog

Use this project-local Software Delivery skill for `sd-work-backlog` and
`/sd:work-backlog` style work. It is a bounded orchestration loop over the
existing Trellis backlog. It selects one implementation-ready task, completes
that task through the normal Trellis and SD workflows, processes follow-ups and
learnings, then returns to backlog selection.

This command is not a daemon. It runs only while the current agent session is
active, and it never bypasses the safety gates owned by `sd-create-pr`,
`sd-review-pr`, or `sd-housekeeping`.

## Safety Rules

- Work exactly one backlog task per iteration. Do not start another task until
  the current task is merged and cleaned up, explicitly parked, or stopped with
  a reported blocker.
- Do not create several branches or PRs at once. The loop is sequential.
- Require a clean working tree before selecting a new task. If unrelated dirty
  files exist, stop and report them.
- Resolve `trellis-before-dev`, `sd-create-pr`, and `sd-housekeeping` by name
  using the agent's trusted installed-skill resolver before starting the first
  implementation iteration. Stop if any required skill is missing, unreadable,
  empty, resolves to more than one candidate, fails validation, defines
  contradictory safety rules, or requires unavailable tools.
- Use `sd-create-pr` as the source of truth for spec refresh, staging, commit,
  push, PR creation/reuse, and PR review. Do not duplicate or shortcut that
  workflow.
- Use `sd-housekeeping` as the source of truth for merge and post-merge branch
  cleanup. Do not force a merge, delete a branch, or bypass failed checks.
- Do not create pull requests in the upstream `Trellis` repository without
  explicit approval from the user for that specific upstream PR. If backlog
  work uncovers a `Trellis`-owned change, write a paste-ready handoff and park
  or record the follow-up instead of opening an upstream PR.
- If an iteration produces follow-ups or learnings, address or record them
  before selecting the next backlog task.
- Stop instead of continuing if follow-ups cannot be safely addressed or
  recorded, if housekeeping reports anomalies, if review/CI remains blocked, or
  if the remaining tasks all require user input.

## Step 1: Resolve State And Backlog

Confirm repository state and Trellis context:

```bash
git status -sb
python3 ./.trellis/scripts/get_context.py --mode record
python3 ./.trellis/scripts/task.py list --mine
```

If the working tree is dirty before a new iteration starts, classify the paths.
Continue only when the paths are already part of the active task stream and the
appropriate current-task workflow owns them. Otherwise stop and report the
dirty paths.

Inventory active task directories under `.trellis/tasks/`, excluding
`.trellis/tasks/archive/`. Prefer Trellis CLI output for the task list, then
inspect each candidate's `task.json`, `prd.md`, `design.md`, and
`implement.md` as needed.

## Step 2: Decide Which Tasks Are Actionable

A task is actionable only when all of these are true:

- `task.json.status` is `planning` or `in_progress`.
- `prd.md` exists and is not placeholder-only.
- `prd.md` contains a goal and either concrete requirements or acceptance
  criteria.
- `prd.md` does not contain a current `Parked by sd-work-backlog` note, an
  unresolved blocking open question, or an explicit waiting-for-user marker.
- If the task is complex, `design.md` and `implement.md` exist before
  implementation starts.

Treat these as not actionable for the current loop:

- tasks missing a real PRD;
- complex planning tasks without `design.md` or `implement.md`;
- tasks blocked by product judgment, credentials, external state, or user
  input;
- tasks explicitly parked by this command; and
- tasks whose scope would require opening an upstream `Trellis` PR without the
  user's consent.

## Step 3: Pick The Highest-Value Task

Rank actionable tasks deterministically:

1. `in_progress` before `planning`.
2. Priority order `P0`, `P1`, `P2`, `P3`, then missing/unknown priority.
3. Tasks with complete implementation artifacts before PRD-only tasks.
4. Older `createdAt` date, then lexical task directory name.

Report the selected task and the reason it won. Also keep a brief skipped-task
summary grouped by reason, especially for tasks that need user input.

If there are no actionable tasks, stop and report whether the backlog is empty
or all remaining tasks are blocked, parked, or missing implementation-ready
planning.

## Step 4: Implement One Task

For the selected task:

1. Re-read its `prd.md`, `design.md` if present, and `implement.md` if present.
2. If the task is still in `planning`, run:

   ```bash
   python3 ./.trellis/scripts/task.py start <task-dir>
   ```

3. Load and follow `trellis-before-dev` before editing files.
4. Implement the smallest correct scope for that task.
5. Run the narrow checks for the changed files, then the broader checks
   warranted by the task.

If implementation reveals a blocking user decision, follow Step 5 instead of
guessing.

## Step 5: Wait For User Input Or Park

When a task needs user input:

1. Ask one concise blocking question. Include the decision needed, why it
   matters, your recommended answer, and the tradeoff if the user chooses
   differently.
2. Wait up to 15 minutes when the current platform/session supports timed
   waiting. If the platform does not support timed waiting, state that and use
   the closest available explicit wait mechanism.
3. If the user answers, update the task PRD if the decision changes scope, then
   continue the same iteration.
4. If no answer arrives within the wait window, append a dated note to that
   task's `prd.md`:

   ```markdown
   ## Parked by sd-work-backlog - YYYY-MM-DD

   - Blocking question: ...
   - Why it blocks: ...
   - Needed to resume: ...
   ```

5. Do not invent a custom Trellis status. Leave the task in a normal
   Trellis-compatible state and return to Step 1 to find the next actionable
   task.

## Step 6: Publish, Review, Merge, And Clean Up

When implementation and local checks are ready:

1. Invoke `sd-create-pr` and follow it as the source of truth.
2. Let `sd-create-pr` hand off to `sd-review-pr`; address review comments and
   CI failures through that loop.
3. When the PR is ready or has merged, invoke `sd-housekeeping`.
4. After housekeeping completes, run one extra `sd-housekeeping` pass to verify
   the repository returned to the default branch, the working tree is clean,
   stale refs are pruned, and no current-stream cleanup remains.

If any delegated command reports a blocker or anomaly, stop and report that
blocker. Do not continue to the next backlog task from a dirty, unmerged, or
ambiguous state.

## Step 7: Process Follow-Ups And Learnings

Before selecting the next task, inspect the completed iteration for follow-ups
and learnings:

- Directly address follow-ups that are small, unblocked, and clearly part of
  making the just-completed task truly done.
- Create or update Trellis tasks for follow-ups that are larger, separable,
  blocked, lower priority, or outside the completed task's scope.
- Update specs, docs, or review learnings when the task produced a durable
  convention, gotcha, reviewer pattern, or prevention mechanism.
- Record a short follow-up ledger in the iteration report:
  - addressed immediately;
  - recorded as Trellis tasks;
  - captured as durable knowledge; and
  - deferred because user input or external state is required.

If a follow-up cannot be addressed or recorded safely, stop and report it
instead of continuing the loop.

## Step 8: Repeat Or Stop

Return to Step 1 after the current task is completed, merged, cleaned up, and
follow-ups/learnings are processed.

Stop when:

- no active Trellis tasks remain;
- no remaining task has implementation-ready planning;
- all remaining tasks are parked or require user input;
- a delegated SD command reports an unresolved blocker;
- the working tree is dirty with unrelated or ambiguous files; or
- continuing would exceed the current session's practical limits.

## Final Report

Report:

- tasks completed, with PR numbers/URLs and merge state;
- tasks parked, including the blocking question and where the park note was
  written;
- tasks skipped, grouped by reason;
- follow-ups addressed immediately;
- follow-ups recorded as Trellis tasks;
- learnings captured in specs, docs, or review-learnings files;
- final branch and working-tree state;
- open PRs/issues when relevant; and
- whether another `sd-work-backlog` invocation would have actionable work.
