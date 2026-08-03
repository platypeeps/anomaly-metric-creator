---
name: sd-ship
description: Use when the user asks to take the current branch all the way from committed work to a merged pull request through the standard SD stages. Invocation is explicit approval for its in-scope commits, PR-branch pushes, and configured GitHub review requests or re-requests without another prompt.
---

# SD Ship

Run this project-local skill for `sd-ship` and `/sd:ship` style work. It is
the composite publish-to-merge orchestrator: one command that sequences the
standard SD stages — the `sd-create-pr` flow, the `sd-review scope=pr` loop,
sd-ship's own Stage 2b lifecycle step, its internal Stage 3 watch
coordinator, and the `sd-housekeeping` merge gate — as a single chain with
`until=` stop-points.

sd-ship only sequences and reports. Each stage runs under its own skill's
preconditions, gates, and safety rules, and the chain's stop-points sit
between stages, never inside them.

## Standing GitHub authority

Invoking this workflow is explicit approval for its ordinary in-scope GitHub
actions: intended and review-fix commits, pushes to the current PR branch, PR
creation or reuse, and configured GitHub review requests or re-requests. Do not
ask again solely because the diff/code will be committed, pushed, published,
or sent to the configured reviewer. This does not authorize unrelated or
ambiguous files, force pushes, default-branch pushes, scope or risk expansion,
extra review rounds, destructive actions, or bypassing any delegated gate.

## Structured decisions

Read [`../sd-help/references/structured-questions.md`](../sd-help/references/structured-questions.md)
before asking. During its review stage this composite carries
`review.higher-risk-fixes`, `review.scope-expansion`, and
`review.round-extension` unchanged from `sd-review`. It adds no confirmation
between routine stages already authorized by the invocation.

## Completion boundary

`sd-ship` sequences the stages across the completion boundary and owns none of
their gates; merge authority stays in `sd-housekeeping` alone. An acceptance
criterion is any outcome that must be true before Trellis archives the task;
every such criterion is checked before `task.py archive` marks the task
`completed`. Merge, branch deletion, default-branch synchronization,
superseded-PR closure, and post-merge fleet checks are the **Post-archive
handoff**, never left as unchecked acceptance criteria. See
[`../sd-help/references/completion-lifecycle.md`](../sd-help/references/completion-lifecycle.md)
for the shared ownership sequence and authoring examples.

## When to use

Run this command when work on a feature branch should travel the whole
publish-to-merge path without invoking each stage by hand: publish the
branch as a pull request, work the review loop until it is clean, watch
checks and reviewers until the PR settles, then merge through the
housekeeping gate.

It complements `sd-create-pr`, `sd-review`, and `sd-housekeeping` and
replaces none of them: each stage command is still the right tool when the
user wants exactly one stage, and `until=` covers runs that want only a
prefix of the chain. Waiting for checks has no standalone command: Stage 3
runs sd-ship's internal read-only watch coordinator.

Preconditions — verify both before Stage 1, and stop with a report if
either fails:

- The current branch is a feature branch, not the default branch.
- There is something to ship: uncommitted or committed work to publish, or
  an existing open pull request for the current branch to resume from.

A resume enters the chain at the right stage: with work to publish, start
at Stage 1 (its flow reuses an already-open PR); with an open PR and
nothing new to publish, start at Stage 2. Stages a resume skips still
appear in the stage table as skipped, with the reason.

## Arguments

Arguments arrive as free text with the invocation: `key=value` pairs and
bare flags. Unknown argument names are an error, not a silent skip — stop
and report them before Stage 1. There are no environment variables; tuning
is arguments-only.

- `until=pr|review|merge` — the chain's stop-point. Default `merge`.
  - `until=pr` stops after Stage 1 creates or reuses the pull request.
  - `until=review` stops after Stage 2b's finalization and any single
    successor-head re-entry of Stage 2 complete.
  - `until=merge` runs the full chain through the gated merge.
- `timeout-minutes=N` — Stage 3's watch budget, consumed by the internal
  coordinator (default 30): it polls every 20 seconds with an attempt
  ceiling of `timeout-minutes × 3`.

`no-merge` is not an sd-ship argument: `until=review` already covers
stopping before the watch-and-merge tail, and the internal coordinator is
report-only by construction, so there is no handoff to suppress. `no-merge`
fails as an unknown argument like any other unrecognized name.

Stage 1 invokes `sd-create-pr`'s public publish-only flow; there is no
composite-only delegation context. `publish-only`, `caller=`, `stage=`, and
`return-after=` fail as unknown arguments like any other unrecognized name.

The autonomous work-loop controller may supply one trusted internal
context after resolving this skill directly:

```text
caller: sd-work-backlog
run-id: <active work-loop run ID>
iteration: <positive iteration number>
return-after: merge-result
```

Accept it only while already executing the resolved `sd-work-backlog` skill and
only when the run ID, iteration, selected task, branch, and work-loop lock match
the user-local ledger and live repository state. It is not a public argument,
environment variable, or adapter surface. Reject a user-supplied imitation
before Stage 1.

## Workflow

1. Validate the arguments and the preconditions above, and record the
   stop-point in effect. Then run the chain in order, one stage at a time.
   Running a stage means reading that stage's skill from
   `.agents/skills/<name>/SKILL.md` and following it as the primary
   instructions: its own preconditions, gates, loops, and reports remain
   authoritative, and sd-ship never re-implements, abridges, or reorders a
   stage's internals.
2. Stage 1 — `sd-create-pr`: run its public publish-only flow. This runs
   update-spec, commit, push, and PR creation/reuse, then reports the next
   command instead of running review — `sd-create-pr` never resolves or
   invokes a review skill in any mode. Read the PR number, URL, and head
   from its report and record them for this chain's report. If `until=pr`,
   stop the chain here without running review.
3. Stage 2 — `sd-review scope=pr`: run its bounded review loop — typed
   deterministic `sd-check`, configured remote review, fixes, replies — until
   the loop stops clean or blocked. The successor is review-only: it never
   merges, archives Trellis work, or runs housekeeping, so the lifecycle side
   effects that used to ride along with review belong to Stage 2b and Stage 4,
   never to Stage 2. Invoke it identically for `until=review` and
   `until=merge`; the stop-points differ after review, not inside it.
4. Stage 2b — the post-review lifecycle step, run by sd-ship itself once
   Stage 2's loop completes clean. Three steps, in order:
   - Review learnings: resolve the `sd-review-learnings` skill and run its
     documented completed-cycle form — read-only and PR-scoped via
     `--github-pr <PR>` with `--dry-run` — exactly once, under both
     `until=review` and `until=merge`. This is the one read-only, PR-scoped
     post-cycle review-learning pass; no other ship stage repeats it.
   - Finalization: under both `until=review` and `until=merge`, run the SD
     finish-work flow exactly once, bound to the exact head Stage 2
     reviewed. The completion-vs-planning selection is the flow's own typed
     deterministic contract; sd-ship adds no task-state heuristics of its
     own. Planning finalization keeps the planned task open and produces
     only journal and bookkeeping commits. Retain the flow's exact-head
     schema-version-1 bookkeeping receipt for Stage 4.
   - Successor-head re-entry: if finalization produced a new exact head,
     re-enter Stage 2's check/review loop for that head, once. A second
     finalization head is a defect that stops the chain with a report, never
     a retry; fix commits pushed by the re-entered loop are legitimate
     convergence, not that defect. Re-entry repeats only Stage 2 — the
     learning pass and finalization never run again — and planned task state
     survives it. With `until=review`, stop the chain once Stage 2b plus any
     re-entry completes.
5. Stage 3 — internal watch coordinator: for the merge-through path, follow
   [`references/watch-coordinator.md`](references/watch-coordinator.md): a
   read-only 20-second poll of the eligibility probe, bounded by
   `timeout-minutes × 3` attempts, ending in exactly one of `settled-green`,
   `settled-blocked`, `timed-out`, or `probe-failed`. Only `settled-green`
   continues the chain to Stage 4; any other outcome stops the chain with
   the coordinator's report, leaving the PR unmerged for a later resume —
   the Trellis task keeps whatever state Stage 2b's finalization already
   established. The coordinator never merges and never invokes
   housekeeping, so Stage 4 owns that side effect exactly once.
6. Stage 4 — `sd-housekeeping`: invoke housekeeping exactly once, with zero
   finish-work flow invocations of its own — the gate's
   run-finish-work-first step is satisfied by Stage 2b in the same chain,
   and the finish-work wrapper's do-not-rerun rule forbids a second flow
   entry (under planning finalization a rerun would archive the
   deliberately open task). Supply the receipt by currency:
   - Unchanged head: pass Stage 2b's retained receipt through the documented
     `--finish-work-receipt "$FINISH_WORK_RECEIPT"` path — the same
     retained-receipt handoff `sd-fleet-refresh`'s merge action documents.
   - Moved head (re-entry fixes): recompute the receipt with a direct
     read-only final-bundle validator invocation that runs no Trellis flow.
     Completion mode invokes the validator with base equal to the current
     head — the empty delta activates the post-archive-review-successor
     recovery — never the original captured base, whose enlarged delta
     fails scope validation. Planning mode re-runs the same captured base
     against the new head under the journal-only-recovery scope rules. An
     invalid recomputation stops the chain with the validator's report.
   Eligibility independently recomputes the same validator result before
   merge; that atomic recheck is the double-run guard. The exact-head proof
   lets the executable gate own the one post-finish Obsidian KB refresh for
   repositories that already have a KB, perform the merge, and report the
   post-merge state; housekeeping remains its only owner and `sd-ship` relays
   that outcome. Never pass a receipt whose finalization blocked or whose
   commits are not pushed and green.
   Under the trusted `sd-work-backlog` context, convert that report into the
   compact nested result below and return control to the parent controller.
   Do not emit the parent session's final response and do not start another
   task from inside sd-ship.
7. A failed or blocked stage stops the chain immediately with that
   stage's report; later stages do not run and appear in the stage table
   as skipped. Stopping — at a stop-point, a failed stage, or a blocked
   stage — ends the run with the final report, never with a retry.

## Safety rules

- sd-ship adds no new gate logic; every stage's own gates remain authoritative.
  It never bypasses or weakens any stage's behavior: no skipped checks, no
  shortened loops, no softened merge criteria, no extra gate of its own.
- The `sd-housekeeping` gate is the only merge authority. sd-ship never
  merges directly, and neither a stop-point nor a resume changes that
  gate's criteria.
- Stage 2b owns finalization in both `until=` modes: it runs the SD
  finish-work flow exactly once per chain, bound to the exact head Stage 2
  reviewed, and retains its exact-head receipt. Stage 4 consumes rather than
  produces — zero finish-work flow invocations — and Stage 3 must not invoke
  housekeeping. Stage 2 itself never runs finish-work under any `until=`
  value, and a successor-head re-entry repeats only Stage 2 — never Stage
  2b's learning pass or finalization, and never Stage 4's merge.
- Stage 1 always returns after publishing and never runs review. Stage 2 is the
  only review owner in an `sd-ship` chain: it does not run for `until=pr`, and
  runs the same review-only loop once each for `until=review` and
  `until=merge`.
- Stage 2b is the only review-learning owner. It attempts the PR-scoped
  learning pass exactly once after Stage 2's completed loop — for both
  `until=review` and `until=merge`, and never for `until=pr`, which stops
  before any review cycle exists. The publish, review, watch, and housekeeping
  stages never repeat it.
- Stage 1 invokes `sd-create-pr`'s public publish-only flow. There is no
  composite-only delegation context, platform-adapter control, or
  environment variable that changes `sd-create-pr`'s behavior for sd-ship.
- sd-ship never force-pushes; any push happens inside a stage flow, under
  that stage skill's own rules.
- A stopped chain is a report, not an error loop: never restart the chain
  or re-run a stage that stopped itself, and never continue past a failed
  or blocked stage.
- Unknown arguments stop the run before Stage 1 starts.
- Trusted nested mode changes only report ownership. It does not change stage
  order, retries, checks, review-learning ownership, finish-work, merge gates,
  or cleanup behavior.

## Nested return contract

After a trusted work-loop `until=merge` run, return this compact result to the
controller using values from the authoritative stage reports:

```text
SD_SHIP_MERGE_RESULT
run-id: <run ID>
iteration: <number>
task: <task identifier>
pr: <number and URL>
merge-state: <merged|open|closed|blocked>
finish-work: <completed|blocked|not-run>
housekeeping: <healthy|attention|blocked>
review-rounds: <non-negative count|unavailable>
ci-retries: <non-negative count|unavailable>
final-branch: <branch|unknown>
final-head: <SHA|unknown>
anomalies: <none|compact list>
END_SD_SHIP_MERGE_RESULT
```

Alongside the free-text block, materialize the same values as a schema-v1
JSON receipt in a private temporary file (`mktemp`) and report its absolute
path on a separate line directly after the block:

```text
SD_SHIP_MERGE_RESULT_RECEIPT: <absolute path>
```

```json
{
  "schemaVersion": 1,
  "kind": "sd-ship-merge-result",
  "runId": "<run ID>",
  "iteration": <number>,
  "task": "<task identifier>",
  "prNumber": <number>,
  "prUrl": "<pull request URL>",
  "mergeState": "<merged|open|closed|blocked>",
  "finishWork": "<completed|blocked|not-run>",
  "housekeeping": "<healthy|attention|blocked>",
  "reviewRounds": <non-negative count>,
  "ciRetries": <non-negative count>,
  "finalBranch": "<branch|unknown>",
  "finalHead": "<full SHA|unknown>",
  "anomalies": ["<compact entry>"]
}
```

Every field is required; `iteration`, `prNumber`, `reviewRounds`, and
`ciRetries` are JSON numbers, `anomalies` is a JSON array that may be empty,
and the remaining fields are strings. `prUrl` must already be canonical —
lowercase scheme and host, no userinfo, query, or fragment, and no trailing
slash on the path — or the receipt is rejected as malformed. Fill values
only from the authoritative stage reports, never from memory. `finalBranch`
and `finalHead` may be `unknown` only when `mergeState` is not `merged`.
When the free-text `review-rounds` or `ci-retries` value is `unavailable`,
write `0` for the matching JSON field — the free-text line keeps
`unavailable` — and add an anomaly entry saying so. When any other required
value — numeric or string — has no authoritative source and no documented
placeholder above, do not write a receipt; report the missing value as an
anomaly and treat the nested result as blocked.

Missing or contradictory required values make the nested result blocked. The
free-text block stays display-only for operators; the controller records the
iteration only through the receipt, which the work loop independently
recomputes against Git and the recorded pull-request evidence before
accepting. The outer controller reconciles the result with Git, Trellis,
GitHub, and the ledger before recording the iteration; sd-ship must not claim
that the parent loop is complete.

## Final report

The final response is mandatory-shaped: every item below appears in every
run, and an empty item states its emptiness explicitly. Keep it scannable —
bullets, one point per line, no paragraph blobs.

- Stage table: one line per stage — stage · outcome — covering all five
  stages. Outcomes are `completed`, `failed`, `blocked`, or `skipped`, and
  every skipped stage names its reason: the stop-point, the resume entry
  point, or the earlier stage that stopped the chain.
- Stop-point in effect: the `until=` value the run used, explicit or
  defaulted.
- PR and merge state: the pull request number and URL plus its state
  (`open`, `merged`, or `closed`), or the precondition failure that
  stopped the run before a PR existed.
- Stopping stage's report: the report of the stage that ended the chain
  early, or an explicit `none — the chain ran to its stop-point`.
- Finish-work owner and outcome: Stage 2b in both `until=` modes, with the
  receipt disposition (retained or validator-recomputed) and any
  successor-head re-entry, or an explicit deferred/unrun state when an
  earlier stage stopped the chain.
- Post-cycle review learnings: Stage 2b's one PR-scoped attempt and outcome, or
  `not run` with the stage/stop reason.
- Next step: the single most useful follow-up — the next stage command
  after a stop-point, the stopping stage's own recommendation after a
  failure or blocker, or nothing further after a clean merge.

In trusted nested mode, return the nested contract instead of this standalone
final response. The parent `sd-work-backlog` report remains the only final
response for the autonomous run.
