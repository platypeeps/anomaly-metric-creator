---
name: sd-fix-ci
description: Use when the user asks to triage a red CI run back to green by classifying each failing job, fixing real failures through the normal gated flow, rerunning bounded flakes, and reporting the rest. Invocation is explicit approval for in-scope CI-fix commits and PR-branch pushes without another prompt.
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

## Standing GitHub authority

Invoking this workflow is explicit approval for its ordinary in-scope CI-fix
commits and pushes to the current PR branch. Do not ask again solely because a
verified fix will be committed or pushed. A default-branch fix still goes
through the delegated `sd-create-pr` flow, and this authority never covers
unrelated or ambiguous files, force pushes, direct default-branch pushes,
weakened gates, destructive actions, or merge.

## Sandbox-safe tool execution

Run every `gh`, `uv`, `pip`, `ruff`, or `npm` command shown in this workflow
through `bash scripts/sd-ai-command-pack-toolchain.sh run -- <tool> [args...]`.
The argv-safe wrapper changes only documented cache variables and preserves
auth/config state. If it is missing or reports a cache-setup failure, stop with
that diagnostic; do not retry the tool bare or redirect `GH_CONFIG_DIR`.

## When to use

Run this command when CI is red and the user wants it triaged: the current
branch's PR shows failing checks, or the default branch's latest run failed
(`main` flag). Typical entry points: an `sd-ship` Stage 3 watch report that
settled blocked on red checks, a push that went red, or a scheduled
default-branch run that failed while nobody was looking.

It complements `sd-full-check` (the local gate every fix must pass),
`sd-ship` (whose Stage 3 watch points here on red checks), and `sd-review`
(review feedback, not CI state). It is not a review loop: it works CI runs and
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
   lists the failing jobs — identities only, which is cheap. Fetch each
   job's log on its own with `gh run view -j <job-id> --log-failed`, not
   the whole-run `gh run view <run-id> --log-failed`: one whole-run fetch
   pulls every failing job's log into a single context, which defeats the
   per-job dispatch below. Read past the exit status to the first real
   error; the last line of a log is rarely the cause. Per-job fetching is
   a deliberate cost — `gh`'s own help warns it can fall back to API
   fetches that are "slower and more resource-intensive" and fails
   outright if more than 25 job logs are missing, and every `gh` call
   routes through the toolchain wrapper, so N failing jobs mean N cache
   setups.
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
   a pushed PR-branch fix, resuming `sd-ship` watches the fresh checks to
   a settled state through its Stage 3 coordinator.
6. Collect follow-ups: jobs still red, flaky tests worth a stabilization
   task, infra outages worth a status link, stale baselines needing a
   branch update.

## Dispatch protocol

The classification pass (workflow step 3) is the parallel unit: each failing
job is triaged in isolation with no cross-job writes. Acting on the results
(step 4) is not parallel and stays with the parent — it pushes commits, opens
PRs, and spends the shared rerun budget.

- Dispatch one read-only sub-agent per failing job. On sub-agent dispatch
  platforms, run the per-job triage in parallel, in waves of at most six
  concurrent workers; a wider red matrix is triaged in successive waves. On
  inline platforms, classify the jobs sequentially in one context. Either way
  the per-job classifications and the report are identical — the outcome does
  not depend on how the work fanned out. The wave bound is deliberate: each
  worker fetches its own log through the toolchain wrapper, so unbounded
  fan-out means one `gh` cache setup per red leg.
- Every dispatch prompt starts with the Active task prefix when a Trellis task
  is active: `Active task: <task path from task.py current>` before the
  role-specific instructions.
- Every dispatch prompt restates the command's already-resolved
  `checkout-trust: <state> (<reason-code>)` before the role-specific
  instructions. Workers do not reclassify trust; a worker result cannot change
  the state or unlock a gate the command already closed.
- The parent resolves run-level facts once and passes them in each worker's
  change context: whether the branch is behind the default branch (the
  `stale-baseline` signal) and whether the local HEAD matches the PR head.
  Workers must not re-derive these — one run-level fact re-derived by many
  workers can disagree inside a single report. Per-job evidence stays with the
  worker: a job's own earlier passing run on the same commit is that job's
  history.

Worker input, all supplied by the parent:

- job identity: run id, job id, and job name
- the resolved trust state carried by the restatement rule above
- the run-level change context above

Worker output:

- exactly one class from `real-code | flake | infra | stale-baseline`
- evidence as quoted log lines, not summaries
- a suggested fix or rerun disposition, as a proposal only

Workers are read-only: no writes, no `gh run rerun`, no pushes. The parent owns
job enumeration, result assembly, the `real-code`/`flake` tiebreak (prefer
`real-code` and reproduce locally first), every action in step 4, the shared
`max-reruns` budget, and the final report. Keeping reruns and fixes with the
parent is what makes the bounded rerun budget enforceable: a worker able to
call `gh run rerun` would make `max-reruns` meaningless.

## Safety rules

- Never force-push.
- Never bypass a guard or gate: no `--no-verify`, no skipping required
  checks, no editing CI configuration to route around a failure.
- Never delete, skip, or weaken tests to get green: no removed
  assertions, no new skip markers, no lowered coverage floors or
  thresholds.
- Reruns are bounded: 1 per failing run by default, raised only by
  `max-reruns=N`. Never loop reruns to outwait a real failure.
- Dispatch workers are read-only: only the parent reruns, pushes, or
  applies fixes. A worker never calls `gh run rerun`, so the shared
  `max-reruns` budget stays enforceable.
- Default-branch fixes go through a fix branch and PR; never push a
  non-chore fix directly to main.
- Every pushed fix passes the full local gate first.
- Fix only the failing job's cause. Do not bundle refactors, cleanups,
  or unrelated changes into a CI fix.
- Unknown arguments stop the run before any classification.
- This skill never merges the PRs it creates or fixes; merge authority
  stays with the `sd-housekeeping` gate. Watch the fixed PR by resuming
  `sd-ship`.

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
