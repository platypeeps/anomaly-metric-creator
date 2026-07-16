---
name: sd-watch-pr
description: Use when the user asks to watch the current branch's open pull request until checks and reviews settle, then hand a green PR to the housekeeping merge gate or report the blockers.
---

# SD Watch PR

Run this project-local skill for `sd-watch-pr` and `/sd:watch-pr` style
work. It watches the current branch's open PR until it settles — checks
finished, reviewer heard from or given a fair grace window — then either
hands a green, comment-clean PR to the `sd-housekeeping` flow or reports
the blockers.

This command watches and hands off; it never merges directly. The
`sd-housekeeping` gate is the only merge authority, and this skill defers
to that gate with its criteria unchanged.

## When to use

Run this command after a PR is open and review is requested, when the user
wants the settle-then-merge tail of a stream handled without hand-rolled
polling: wait out CI, wait for the reviewer, then merge through the gate or
stop with a precise blocker list.

It complements `sd-create-pr` (opens the PR), `sd-review-pr` (works review
feedback), `sd-fix-ci` (fixes failing checks), and `sd-housekeeping` (the
merge gate). It replaces none of them: it does not fix code, reply to
reviews, resolve threads, or merge on its own. It watches only the current
branch's own PR — not arbitrary PRs in this or other repositories.

Preconditions — verify both before the first poll, and stop with a report
if either fails:

- The current branch is a feature branch, not the default branch.
- Exactly one open PR exists for the current branch. Zero open PRs or more
  than one is a stop-and-report condition, not a guess.

## Arguments

Arguments arrive as free text with the invocation: `key=value` pairs and
bare flags. Unknown argument names are an error, not a silent skip — stop
and report them before the first poll. There are no environment variables;
tuning is arguments-only.

- `timeout-minutes=N` — total watch budget in minutes. Default `30`. The
  budget covers the whole loop, including the reviewer grace period.
- `no-merge` — watch and report only. Skip the housekeeping handoff even
  when the PR settles green and comment-clean.

## Workflow

1. Resolve the watch target. Confirm `gh auth status` succeeds, confirm
   the preconditions above, and record the PR number and URL for the
   report.
2. Poll with `gh` roughly every 20 seconds, inside the timeout budget.
   Each poll gathers four signals, without fetching full comment bodies on
   every interval:
   - check state (`gh pr checks`: name, workflow, state, bucket) and the
     set of still-pending checks
   - requested-reviewer state: which requested reviewers have not yet
     reviewed
   - review events: new approved, changes-requested, or commented reviews
     since the previous poll
   - unresolved review threads, thread-aware through `gh` GraphQL
   Keep the latest snapshot of each signal for the final report. If a
   poll shows the PR closed or merged outside this watch, stop the loop
   and report that state instead of continuing to poll.
3. Decide settled. The PR is settled when both hold:
   - zero pending checks, and
   - a requested reviewer has posted a review, or a short grace period (a
     few extra polls after the checks settle) passes with no new reviewer
     activity.
4. On settled, classify the outcome:
   - Green and comment-clean: every executed check succeeded — skipped
     and neutral conclusions do not block; failed, cancelled, or
     timed-out conclusions do — and no review thread is unresolved. Unless
     `no-merge` was passed, run the `sd-housekeeping` flow — read
     `.agents/skills/sd-housekeeping/SKILL.md` and follow it. Its merge
     gate remains the only merge authority; relay its outcome in the
     report.
   - Blocked: anything else. Report failing or blocked checks by name and
     unresolved review threads by file path. Do not auto-fix anything.
     Point at `sd-fix-ci` for failing checks and `sd-review-pr` for
     review-thread work.
5. On timeout expiry, stop the loop and report the last observed state
   with the same blocker detail. Timeout expiry is a report, not an error
   loop — never restart the loop or keep polling past the budget.

## Safety rules

- This skill never merges directly. Merging happens only through the
  `sd-housekeeping` gate, with that skill's merge criteria unchanged.
- Never resolve a review thread this session did not author. Blockers are
  reported, not tidied away.
- Never force-push, and never push commits as part of watching.
- Timeout expiry is a report, not an error loop. One bounded watch per
  invocation; the user decides whether to watch again.
- Unknown arguments stop the run before the first poll.
- The watch loop itself is read-only. The only mutating step is the
  delegated `sd-housekeeping` flow, and `no-merge` suppresses even that.

## Final report

The final response is mandatory-shaped: every item below appears in every
run, and an empty item states its emptiness explicitly. Keep it scannable —
bullets, one point per line, no paragraph blobs.

- PR: number and URL, or the precondition failure that stopped the run.
- Settle state: `settled-green`, `settled-blocked`, or `timed-out`, plus
  elapsed watch time against the budget.
- Checks summary: pass/fail/skip counts and each failing check by name, or
  an explicit `all checks green`.
- Unresolved threads: one bullet per thread with its file path, or an
  explicit `none`.
- Action taken: `handed off to sd-housekeeping` plus that flow's outcome,
  or `none` with the reason (`no-merge`, blockers, timeout, precondition
  failure).
- Next step: the single most useful follow-up — `sd-fix-ci` for red
  checks, `sd-review-pr` for threads, re-run `sd-watch-pr` after fixes
  land, or nothing further.
