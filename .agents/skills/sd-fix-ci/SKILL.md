---
name: sd-fix-ci
description: Use when the user asks to triage a red CI run back to green by classifying each failing job, fixing real failures through the normal gated flow, rerunning bounded flakes, and reporting the rest.
---

# SD Fix CI

Run this project-local skill for `sd-fix-ci` and `/sd:fix-ci` style work.
It turns a red CI signal into a classified triage and, where safe, a fix:
enumerate the failing jobs, classify each one, reproduce and fix real code
failures through the normal gated flow, rerun flakes within a bound, and
report infrastructure and stale-baseline failures with evidence.

This command fixes causes, not symptoms. It never weakens a check to get
green, and it never merges anything — merge authority stays with the
`sd-housekeeping` gate.

## When to use

Run this command when CI is red and the user wants it triaged: the current
branch's PR shows failing checks, or the default branch's latest run failed
(`main` flag). Typical entry points: an `sd-watch-pr` blocker report, a
push that went red, or a scheduled default-branch run that failed while
nobody was looking.

It complements `sd-full-check` (the local gate every fix must pass),
`sd-watch-pr` (which points here on red checks), and `sd-review-pr` (review
feedback, not CI state). It is not a review loop: it works CI runs and
checks, never review threads. For failures that only occur locally, run
`sd-full-check` instead.

## Arguments

Arguments arrive as free text with the invocation: `key=value` pairs and
bare flags. Unknown argument names are an error, not a silent skip — stop
and report them before touching any run. There are no environment
variables; tuning is arguments-only.

- `main` — target the default branch's latest failing run instead of the
  current branch's PR checks.
- `max-reruns=N` — flake rerun budget per failing run. Default `1`.

## Workflow

1. Select the target:
   - default: the current branch's open PR and its failing checks
     (`gh pr checks`, then the runs behind the failing checks)
   - `main`: the default branch's latest failing run
     (`gh run list --branch <default>`)
   - nothing failing: report the already-green target and stop.
   When targeting the PR, note whether the local HEAD matches the PR
   head; on a mismatch the red run may not reflect the local tree, and
   the report must say which commit was triaged.
2. Enumerate the failures. For each failing run, `gh run view <run-id>`
   lists the jobs and `gh run view <run-id> --log-failed` fetches the
   failing jobs' logs. Read past the exit status to the first real error;
   the last line of a log is rarely the cause.
3. Classify every failing job before acting on any of them, so the report
   reflects the whole run. Each job gets exactly one class:
   - `real-code` — a deterministic failure caused by the tree under test:
     build, lint, type, test-assertion, or packaging errors tied to the
     diff or the existing code.
   - `flake` — a non-deterministic failure: timeout, race, or network
     blip, with evidence such as the same job passing on the same commit
     earlier, or the matching local target passing cleanly on the same
     code.
   - `infra` — the platform failed before the code reached a verdict:
     runner loss, quota exhaustion, service outage, or download failures
     outside the repo's control.
   - `stale-baseline` — the check compares against a baseline that moved:
     the branch is behind the default branch, or pinned/golden data no
     longer matches upstream. The remedy is refreshing the baseline
     through the normal flow, never weakening the check.
   When the evidence is ambiguous between `real-code` and `flake`, prefer
   `real-code` and reproduce locally first; a rerun is not a diagnostic.
   Flake evidence must be concrete — the log lines showing the timeout or
   race, or the prior run where the same job passed on the same commit.
4. Act on each class:
   - `real-code` on a PR branch: map the CI job to its local equivalent.
     Read the workflow file under `.github/workflows/` for the job's
     `run:` steps, and prefer the repo's documented make target over
     retyping raw commands; if no local equivalent exists, say so in the
     report instead of guessing. Reproduce locally, fix the cause, run
     the full local gate (the `sd-full-check` flow or the repo's
     documented equivalent), and push to the PR branch through the
     normal flow.
   - `real-code` on the default branch: never push a non-chore fix
     directly to main. Create a fix branch, fix and gate it there, and
     open a PR through the normal flow (`sd-create-pr`); the fix then
     rides the usual review-and-merge path.
   - `flake`: rerun with `gh run rerun <run-id> --failed`, bounded at 1
     rerun by default; `max-reruns=N` raises the budget. Record the flake
     evidence in the report. If the job fails again with the budget
     spent, reclassify on the new evidence and report instead of
     rerunning further.
   - `infra`: report only, with the evidence line. Do not rerun
     repeatedly to outwait an outage.
5. Confirm outcomes. Poll the affected runs and checks with `gh` until
   they conclude, and record the resulting states for the report. After
   a pushed PR-branch fix, `sd-watch-pr` is the tool for watching the
   fresh checks to a settled state.
6. Collect follow-ups: jobs still red, flaky tests worth a stabilization
   task, infra outages worth a status link, stale baselines needing a
   branch update.

## Safety rules

- Never force-push.
- Never bypass a guard or gate: no `--no-verify`, no skipping required
  checks, no editing CI configuration to route around a failure.
- Never delete, skip, or weaken tests to get green: no removed
  assertions, no new skip markers, no lowered coverage floors or
  thresholds.
- Reruns are bounded: 1 per failing run by default, raised only by
  `max-reruns=N`. Never loop reruns to outwait a real failure.
- Default-branch fixes go through a fix branch and PR; never push a
  non-chore fix directly to main.
- Every pushed fix passes the full local gate first.
- Fix only the failing job's cause. Do not bundle refactors, cleanups,
  or unrelated changes into a CI fix.
- Unknown arguments stop the run before any classification.
- This skill never merges the PRs it creates or fixes; merge authority
  stays with the `sd-housekeeping` gate. Watch the fixed PR with
  `sd-watch-pr`.

## Final report

The final response is mandatory-shaped: every item below appears in every
run, and an empty item states its emptiness explicitly. Keep it scannable —
bullets, one point per line, no paragraph blobs.

- Target: the PR number and branch, or `main` plus the failing run id.
- Per-job classification, one bullet per failing job in the shape
  `<job> · <real-code|flake|infra|stale-baseline> · <evidence one-liner>`,
  or an explicit `no failing jobs found`.
- Actions taken: fixes with their commits, branch, and PR; reruns with
  count against budget; or an explicit `none`.
- Resulting run states: each affected run or check after actions, or
  `unchanged — no actions taken`.
- Follow-ups: parked items and recommendations, or an explicit `none`.
