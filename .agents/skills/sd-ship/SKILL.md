---
name: sd-ship
description: Use when the user asks to take the current branch all the way from committed work to a merged pull request through the standard SD stages.
---

# SD Ship

Run this project-local skill for `sd-ship` and `/sd:ship` style work. It is
the composite publish-to-merge orchestrator: one command that sequences the
standard SD stages — the `sd-create-pr` flow, the `sd-review-pr` loop, the
`sd-watch-pr` flow, and the `sd-housekeeping` merge gate — as a single
chain with `until=` stop-points.

sd-ship only sequences and reports. Each stage runs under its own skill's
preconditions, gates, and safety rules, and the chain's stop-points sit
between stages, never inside them.

## When to use

Run this command when work on a feature branch should travel the whole
publish-to-merge path without invoking each stage by hand: publish the
branch as a pull request, work the review loop until it is clean, watch
checks and reviewers until the PR settles, then merge through the
housekeeping gate.

It complements `sd-create-pr`, `sd-review-pr`, `sd-watch-pr`, and
`sd-housekeeping` and replaces none of them: each stage command is still
the right tool when the user wants exactly one stage, and `until=` covers
runs that want only a prefix of the chain.

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
  - `until=review` stops after Stage 2's review loop completes.
  - `until=merge` runs the full chain through the gated merge.
- `timeout-minutes=N` — pass-through argument, forwarded verbatim to
  `sd-watch-pr` as Stage 3's watch budget. sd-ship neither interprets nor
  re-defaults it.

`no-merge` is not an sd-ship argument: `until=review` already covers
stopping before the watch-and-merge tail, so `no-merge` fails as an
unknown argument like any other unrecognized name.

`sd-create-pr`'s Stage 1 orchestration context, `defer-finish-work`, and Stage
3's `no-merge` are internal delegation modes, not public `sd-ship` arguments.
The composite supplies them only as described below so review and lifecycle
side effects each have one owner. Reject user-supplied `publish-only`,
`caller=`, `stage=`, or `return-after=` controls as unknown arguments.

The autonomous work-loop controller may supply one additional trusted internal
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
2. Stage 1 — `sd-create-pr`: delegate its flow with the exact internal
   orchestration context `caller: sd-ship`, `stage: 1`, `return-after: pr`.
   This runs update-spec, commit, push, and PR creation/reuse, then returns the
   publish result without entering `sd-create-pr`'s standalone review handoff.
   Record the PR number and URL for the report. If `until=pr`, stop the chain
   here without running review.
3. Stage 2 — `sd-review-pr`: run its bounded review loop — deterministic
   local gate, configured remote review, fixes, replies — until the loop
   stops clean or blocked. Its completed loop owns the one read-only,
   PR-scoped post-cycle review-learning pass; no other ship stage repeats it.
   With `until=review`, invoke it without
   `defer-finish-work`, so its normal Step 8 finishes the Trellis work before
   the chain stops. With `until=merge`, invoke it with `defer-finish-work` and
   require the explicit Stage 4 handoff; no task archive or final journal
   commit happens in Stage 2.
4. Stage 3 — `sd-watch-pr`: for the merge-through path, run its watch flow
   with `no-merge` until the pull request settles green or blocked, forwarding
   `timeout-minutes=` verbatim when it was passed. `no-merge` suppresses the
   standalone watch command's automatic housekeeping handoff so Stage 4 owns
   that side effect exactly once. If Stage 3 blocks or times out, stop the
   chain; this leaves the active Trellis task unarchived for a later resume.
5. Stage 4 — `sd-housekeeping`: invoke housekeeping exactly once. Its gate
   runs finish-work, pushes any resulting task/journal commits and waits for
   their checks, owns the one post-finish Obsidian KB refresh for repositories
   that already have a KB, performs the merge, and reports the post-merge
   state; housekeeping remains its only owner and `sd-ship` relays that
   outcome.
   Under the trusted `sd-work-backlog` context, convert that report into the
   compact nested result below and return control to the parent controller.
   Do not emit the parent session's final response and do not start another
   task from inside sd-ship.
6. A failed or blocked stage stops the chain immediately with that
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
- In an `until=merge` chain, finish-work and housekeeping side effects belong
  only to Stage 4. Stage 2 must defer finish-work and Stage 3 must not invoke
  housekeeping. In an `until=review` chain, Stage 2 retains its normal
  finish-work behavior.
- Stage 1 always returns after publishing and never runs review. Stage 2 is the
  only review owner in an `sd-ship` chain: it does not run for `until=pr`, runs
  once normally for `until=review`, and runs once with `defer-finish-work` for
  `until=merge`.
- Stage 2 is also the only review-learning owner. Its `sd-review-pr` invocation
  attempts the PR-scoped learning pass once after the overall review loop; the
  composite, watch, finish-work, and housekeeping stages never repeat it.
- The Stage 1 orchestration context is supplied by this composite directly to
  `sd-create-pr`. Never expose it through a platform adapter, environment
  variable, or user-facing argument.
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
pr: <number and URL>
merge-state: <merged|open|closed|blocked>
finish-work: <completed|blocked|not-run>
housekeeping: <healthy|attention|blocked>
review-rounds: <non-negative count|unavailable>
final-branch: <branch|unknown>
final-head: <SHA|unknown>
anomalies: <none|compact list>
END_SD_SHIP_MERGE_RESULT
```

Missing or contradictory required values make the nested result blocked. The
outer controller reconciles the result with Git, Trellis, GitHub, and the
ledger before recording the iteration; sd-ship must not claim that the parent
loop is complete.

## Final report

The final response is mandatory-shaped: every item below appears in every
run, and an empty item states its emptiness explicitly. Keep it scannable —
bullets, one point per line, no paragraph blobs.

- Stage table: one line per stage — stage · outcome — covering all four
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
- Finish-work owner and outcome: Stage 2 for `until=review`, Stage 4 for
  `until=merge`, or an explicit deferred/unrun state when an earlier stage
  stopped the chain.
- Post-cycle review learnings: Stage 2's one PR-scoped attempt and outcome, or
  `not run` with the stage/stop reason.
- Next step: the single most useful follow-up — the next stage command
  after a stop-point, the stopping stage's own recommendation after a
  failure or blocker, or nothing further after a clean merge.

In trusted nested mode, return the nested contract instead of this standalone
final response. The parent `sd-work-backlog` report remains the only final
response for the autonomous run.
