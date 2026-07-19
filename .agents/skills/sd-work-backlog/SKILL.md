---
name: sd-work-backlog
description: "Use when the user wants an autonomous, resumable loop that plans and completes Trellis backlog tasks sequentially through green merged pull requests."
---

# SD Work Backlog

Use this project-local Software Delivery skill for `sd-work-backlog` and
`/sd:work-backlog` style work. This is the canonical autonomous work-loop
controller. It selects exactly one Trellis task, takes it through any missing
planning, implementation, validation, `sd-ship until=merge`, follow-up
processing, and clean-state verification, then re-inventories and repeats.

The agent executes the workflow. The user-local work-loop ledger, Trellis, Git,
and GitHub are authoritative across compaction, interruption, and resume.
Reports from nested skills return to this controller and are never the overall
loop's final response unless this skill records a valid stop condition.

## Arguments

Parse the invocation before acquiring a lock or mutating repository state.
Unknown option-shaped input is an error.

- Bare text is one implicit preferred focus. `sd-work-backlog CI pipeline` is
  equivalent to `focus="CI pipeline"`; do not split the phrase on spaces or
  commas.
- `focus="<expression>"` is repeatable and creates ordered preference bands.
  Matching tasks run first, then the normally ranked backlog continues.
- `focus-only="<expression>"` is repeatable and strictly filters the backlog.
  Stop with `focused_backlog_exhausted` when no matching actionable task
  remains; never broaden to unrelated work.
- Structured expressions support `priority:`, `package:`, `task:`, `status:`,
  and `scope:`. Unprefixed expressions use conservative task-artifact matching.
- `until=merge` is the default full lifecycle. `until=design` stops after the
  selected task's `design.md` and `implement.md` are implementation-ready.
- Bare text cannot be mixed with explicit focus arguments. `focus=` and
  `focus-only=` are mutually exclusive. Reject empty expressions, unknown
  selectors, unknown lifecycle values, and any malformed mixture before the
  helper starts or resumes a run.

Thin platform adapters must pass the user's invocation text unchanged to this
skill. This skill owns normalization. At iteration boundaries, also honor
operator controls: `stop now`, `stop after current`, `pause`, `skip current`,
`reprioritize <task>`, `focus <value>`, `focus-only <value>`,
`add focus <value>`, `clear focus`, and `report status`.

## Run-Level Authority

Invoking the full-cycle command authorizes ordinary repo-local planning,
implementation, focused validation, feature branches, pull requests, review
fixes, green merges through the existing gate, and clearly scoped follow-up
task recording for this run. Continue without per-iteration confirmation.

That authority never includes:

- an upstream Trellis pull request without explicit approval for that PR;
- force pushes, destructive cleanup, bypassing branch protection, or weakening
  deterministic checks;
- credentials, secrets, security-sensitive policy choices, or irreversible
  external operations; or
- product decisions that cannot be inferred responsibly from repository
  evidence and existing conventions.

Before the first mutation, print this authority boundary, the selector/focus,
the current run ID, and the checkpoint target.

## Prerequisites And Durable State

Resolve `trellis-before-dev`, `sd-ship`, and
`scripts/sd-ai-command-pack-work-loop.py` before starting. Stop if a required
surface is missing, ambiguous, unreadable, contradictory, or unusable.

Start or resume through the helper. Use `--mode designs --selector needs-design`
only when invoked by the trusted `sd-work-designs` entry point; direct backlog
runs use `--mode backlog --selector all`.

```bash
bash scripts/sd-ai-command-pack-toolchain.sh run-python -- \
  scripts/sd-ai-command-pack-work-loop.py start --repo . --mode backlog \
  --selector all --until merge --json
```

Pass normalized focus as repeatable `--focus`, `--focus-only`, or one
`--bare-focus` value. `SD_AI_COMMAND_PACK_STATE_HOME` may override the user
state root only with an absolute path. Otherwise the helper uses the documented
XDG, Windows local-app-data, or home-state fallback. Never create a tracked
loop ledger in the target repository.

The helper's lock is run-specific. A stale lock is not permission to steal a
run: reconcile the ledger with live Trellis, Git, branch, and PR state first,
then use explicit stale-lock recovery only when the prior process is gone and
the repository is safe. Concurrent loops for one repository are forbidden.

At startup, resume, every phase boundary, and every iteration boundary:

1. Read the helper's status JSON.
2. Reload current Trellis task/artifacts and applicable specs.
3. Inspect current branch, HEAD, working tree, upstream, and relevant PR.
4. Reconcile observed evidence with the ledger before repeating a side effect.
5. Heartbeat or transition only after reconciliation succeeds.

If live state is verifiably ahead, record a verified live advance. If the
ledger claims work that live state cannot prove, record red context health and
stop instead of replaying it.

## Candidate Inventory And Focus

Require a clean, unambiguous iteration boundary before selecting new work.
Inventory active task directories below `.trellis/tasks/`, excluding
`archive/`. Prefer Trellis CLI output, then inspect each candidate's
`task.json`, `prd.md`, `design.md`, and `implement.md`.

A task is actionable when:

- status is `planning` or `in_progress`;
- `prd.md` contains a real goal plus concrete requirements or acceptance
  criteria;
- no current park note, waiting marker, or unresolved blocking question makes
  implementation unsafe; and
- missing design artifacts can be responsibly produced from repository
  evidence before implementation.

The trusted `needs-design` selector first excludes tasks whose design and
implementation artifacts are already complete. Focus then ranks only the
remaining selector candidates.

Build a bounded temporary JSON candidate list containing task ID, title,
description, status, priority, created date, artifact readiness, package,
scope, converged PRD text, related files, and explicit metadata. Do not search
unrelated source content to manufacture a focus match. Rank it through:

```bash
bash scripts/sd-ai-command-pack-toolchain.sh run-python -- \
  scripts/sd-ai-command-pack-work-loop.py rank --repo . \
  --candidates-file /absolute/path/to/candidates.json --json
```

The helper returns ordered candidates and grounded match evidence. Existing
deterministic tie-breakers remain: `in_progress`, priority `P0` through `P3`,
artifact readiness, older creation date, then lexical task ID. Report why the
selected task won and summarize ambiguous, non-matching, parked, and blocked
candidates. In preferred mode, a focus miss falls back visibly to normal
ranking. In focus-only mode, it stops without broadening.

## One Iteration

Work exactly one task, branch, and PR at a time. Before each iteration, report
the iteration number, selected task, selection/focus evidence, concise plan,
decisions in effect, counters, and current context health.

### 1. Select And Plan

Transition `inventory -> selected`, recording the task. Follow
[`references/autonomous-loop.md`](references/autonomous-loop.md) to converge
the PRD, design, implementation plan, scope, risks, and validation strategy.
Split an oversized task into ordered Trellis tasks before implementation when
the run-level task-creation authority applies.

If `until=design`, validate the planning artifacts, record a clean checkpoint
and stop. Do not start implementation or create a PR.

### 2. Implement And Validate

Start the selected Trellis task when needed, establish one feature branch, and
load `trellis-before-dev` before editing. Implement the smallest coherent scope.
Run focused checks, then the broader repo gate warranted by the change.

Persist phase transitions through `planning`, `implementing`, and `validating`.
Rehydrate after several complex phases or any compaction, continuation-summary,
or truncated-output signal.

### 3. Ship Through One Lifecycle Owner

Invoke `sd-ship until=merge` exactly once with the trusted internal context:

```text
caller: sd-work-backlog
run-id: <ledger run ID>
iteration: <number>
return-after: merge-result
```

`sd-ship` remains the only owner of create/review/watch/finish/merge/housekeeping
stages. The outer loop must not invoke `sd-create-pr`, `sd-review-pr`,
`sd-watch-pr`, or `sd-housekeeping` separately. Its nested result records the
PR, merge state, finish-work, housekeeping, review rounds, final branch/HEAD,
and anomalies, then returns here.

### 4. Process Follow-Ups

Before selecting another task:

- address small, in-scope follow-ups that are required for the completed task;
- create or update Trellis tasks for separable, larger, blocked, or lower-value
  work;
- capture durable conventions through the existing spec/review-learning owner;
  never rerun the PR-scoped learning pass at this outer level; and
- record each follow-up as addressed, tasked, captured, parked, or blocked.

Record the compact iteration result through the helper, including PR,
review-round and CI-retry counts, decisions, and follow-up pointers. Verify the
repository is back on the synchronized default branch with a clean tree, then
transition `complete -> inventory` and re-inventory live state. A clean nested
housekeeping report is a return value, not a reason to end the parent loop.

## Blockers, Parking, And Operator Input

Classify failures as transient, task-local, user-input, or repository-wide.

- Retry transient provider/network failures with bounded backoff and state
  deltas, not unbounded full-payload polling.
- Park a task-local blocker only before mutation or after the repo has returned
  to a clean default branch with no blocking PR.
- A blocker with uncommitted implementation, an unresolved PR, an unexplained
  dirty path, or contradictory lifecycle evidence is repository-wide for this
  run and stops the loop.
- For unavoidable user input, ask one concise question with a recommended
  answer and tradeoff. Wait up to 15 minutes when supported. If unanswered,
  append a dated park note with the question, why it blocks, and what resumes
  it; persist the parked result and continue only from a clean boundary.

At every boundary, check for newer operator instructions. `skip current` is
allowed only before mutation. `pause` writes a resumable checkpoint and
releases the lock. `stop now` checkpoints at the next safe transition.
`stop after current` completes follow-ups and cleanup before stopping. Focus
updates replace/add/clear the helper's focus and immediately re-inventory.

## Context Health And Checkpoints

Use evidence, not model self-assessment:

- `green`: ledger and live state agree; continue.
- `amber`: compaction, continuation summary, truncated output, or unverifiable
  remembered detail without a contradiction; reload task/spec/Git/PR evidence,
  reconcile, increment context epoch, then continue.
- `red`: state contradiction, duplicate-side-effect attempt, wrong task or
  branch, unexplained dirty files, or an unverified completed phase; persist a
  blocked checkpoint and stop or safely park.

Around a natural clean boundary between approximately eight and twelve
completed iterations, emit a non-blocking stop offer and persist the checkpoint
target. Continue unless the user asks to stop. Never interrupt active work or
weaken review/CI gates because a time, cost, or iteration counter was reached.

If a platform cannot open a fresh context, include the exact resume invocation
in a checkpoint report. The user-local ledger must be sufficient to continue
after context replacement.

## Stop Conditions

Stop only after persisting one evidence-backed reason:

- `backlog_exhausted`;
- `focused_backlog_exhausted`;
- `all_remaining_tasks_blocked`;
- `operator_stop` or `operator_pause`;
- `repository_wide_blocker`;
- `context_health_red`; or
- `until_design_reached`.

Do not emit the overall final response while the helper remains active without
a verified terminal/checkpoint state.

## Final Report

Report the run ID, mode/selector/focus, concrete stop reason, elapsed run
boundary, and final branch/tree state. Include:

1. completed tasks with PR links and merge state;
2. parked, skipped, failed, and blocked tasks with reasons;
3. merged PRs, remote-review rounds, CI retries, and iteration counters;
4. decisions and follow-ups addressed, tasked, captured, or still blocked;
5. context-health events and checkpoint/resume state; and
6. whether another invocation has actionable work.

For `until=design`, end with numbered links to each created or updated
`design.md` and `implement.md`, each with a one-line summary. Every empty report
category must say `none`; never let a nested skill's final-looking report stand
in for this controller's final report.
