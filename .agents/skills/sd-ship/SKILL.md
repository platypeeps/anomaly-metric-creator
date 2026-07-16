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

## Workflow

1. Validate the arguments and the preconditions above, and record the
   stop-point in effect. Then run the chain in order, one stage at a time.
   Running a stage means reading that stage's skill from
   `.agents/skills/<name>/SKILL.md` and following it as the primary
   instructions: its own preconditions, gates, loops, and reports remain
   authoritative, and sd-ship never re-implements, abridges, or reorders a
   stage's internals.
2. Stage 1 — `sd-create-pr`: run its flow to refresh specs, commit and
   push the intended work, and create or reuse the branch pull request.
   Record the PR number and URL for the report. If `until=pr`, stop the
   chain here.
3. Stage 2 — `sd-review-pr`: run its bounded review loop — deterministic
   local gate, configured remote review, fixes, replies — until the loop
   stops clean or blocked. If `until=review`, stop the chain here.
4. Stage 3 — `sd-watch-pr`: run its watch flow until the pull request
   settles green or blocked, forwarding `timeout-minutes=` verbatim when
   it was passed.
5. Stage 4 — `sd-housekeeping`: its gate performs the merge and the
   post-merge cleanup and reports the final repo state; sd-ship relays
   that outcome.
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
- sd-ship never force-pushes; any push happens inside a stage flow, under
  that stage skill's own rules.
- A stopped chain is a report, not an error loop: never restart the chain
  or re-run a stage that stopped itself, and never continue past a failed
  or blocked stage.
- Unknown arguments stop the run before Stage 1 starts.

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
- Next step: the single most useful follow-up — the next stage command
  after a stop-point, the stopping stage's own recommendation after a
  failure or blocker, or nothing further after a clean merge.
