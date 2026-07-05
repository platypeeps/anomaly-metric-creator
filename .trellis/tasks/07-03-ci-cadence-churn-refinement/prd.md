# Refine CI cadence: cancelled-lane transient FAILURE and redundant queued main runs

## Context

- **Source:** observed 2026-07-03 on PR #186 (the first PR to merge under the
  new CI cadence rules from PR #179, "gate auto-merge-armed PRs on the full
  matrix; stop cancelling main backstop runs").
- **Severity:** LOW — refinement, not a defect. Every merge under the new
  rules has been correct; these are rough edges, not failures.
- **Category:** CI / workflow hygiene.
- **Relationship to #179:** both symptoms are documented trade-offs #179's own
  PR body called out. This task decides whether to smooth them or accept them.

## Problem (two symptoms)

### 1. Transient red `test` check from a cancelled lane

When auto-merge is armed on a PR, #179's logic triggers a full-matrix run on
the current head. That new run's `concurrency: ci-<ref>` (with
`cancel-in-progress: true` on PR refs) cancels the in-progress lane. The
aggregate `test` job runs `if: always()` and **fails when its selected lane
was cancelled**, so the `test` context briefly reports `FAILURE` before the
rerun's `test` reports `SUCCESS`.

- **Observed on #186:** the check history showed two `test` aggregate runs —
  one `FAILURE` (cancelled lane), one `SUCCESS` (rerun) — plus `CANCELLED`
  conclusions on `classify changes` / `lightweight readiness` / `quick test` /
  `test (py3.12)` from the superseded run.
- **Why it merged anyway:** GitHub evaluates the *latest* check run for a
  required context; the rerun's `SUCCESS` was latest, so auto-merge fired.
- **Latent risk:** if the `FAILURE` run is ever the latest for the `test`
  context at the moment auto-merge evaluates (a timing/ordering window), a
  legitimately-green PR could stall on a spurious red. Not yet observed;
  this is the concrete failure mode to close or rule out.
- **Recurs on app-code PRs, not just metadata:** first seen on the
  metadata-only #186, but #188 (a server.py app-code change, auto-merge
  armed) reproduced the identical pattern — two `test` runs (`FAILURE` from
  the cancelled lane, `SUCCESS` from the rerun) — and merged on the green.
  So the churn is systematic across PR types, not a metadata-PR artifact;
  the transient red will appear on every auto-merge-armed PR until fixed.

### 2. Redundant queued main-push runs

#179 gave `push` events per-commit concurrency groups (`ci-<sha>`) so merge
bursts stop cancelling each other's backstop runs. The side effect: a
superseded main commit keeps its queued run instead of being cancelled.

- **Observed:** `4e1c182`'s main-push CI run was still `queued` after
  `49bd1b9` (a later merge) had already merged and passed.
- This is the intended "N parallel full suites during an N-merge burst"
  behavior, but it spends runner minutes on commits whose successor is
  already green on `main`.
- **Correction (2026-07-04, ci.yml `b7dbea2`, external change):** the
  "queued for hours" severity of the original observation was
  **conflated** — it had a *second* cause now removed. The org's
  `ubuntu-latest-m` larger runner (which main-push jobs used) was
  decommissioned; those jobs sat `queued` with `runner_id=0` because no
  runner picked them up, not solely because of per-commit concurrency.
  `ci.yml` now runs all events on the standard `ubuntu-latest` runner, so
  a superseded commit's backstop run **executes** rather than hanging. The
  per-commit `ci-<sha>` concurrency groups from #179 are **unchanged**, so
  the residual symptom is only "N standard-runner suites run per burst"
  (wasted minutes), not indefinite hangs. Re-scope this symptom to that
  milder cost — or accept + document it — when designing the fix.
- **Symptom 1 is unaffected by `b7dbea2`:** that change touched only runner
  selection and the test-step command (verified via
  `git diff 56463d0..b7dbea2 -- .github/workflows/ci.yml`); the
  `concurrency` block, the "Decide full CI cadence" step, and the aggregate
  `test` job's `if: always()` are all untouched, so the transient-`FAILURE`
  symptom remains fully open and is the real work here.

## Requirements / candidate approaches (decide in design)

Pick the minimal change that removes the rough edge without reopening the gap
#179 closed. Candidates:

- **For symptom 1:** make the aggregate `test` job treat a `cancelled`
  selected-lane result as non-failing (e.g. neutral/skip) so a superseded
  lane never flashes red — while still failing on a genuine lane `failure`.
  Verify this cannot mask a real failure (a cancelled lane is not a passed
  lane; ensure the *rerun's* `test` remains the authoritative gate).
- **For symptom 1 (alt):** skip the full-CI auto-merge trigger for
  classifier-tagged lightweight/metadata-only PRs (the `.trellis/`-only and
  docs-only changes), so arming auto-merge on a trivial PR does not spawn a
  cancelling full run at all. Must not weaken the gate for app-code PRs.
- **For symptom 2:** optionally cap or dedupe queued main-push runs for
  superseded commits (e.g. a lightweight "is this SHA still `main`'s tip or
  an ancestor with a newer green run?" guard), or simply accept it and
  document the cost. Lowest-risk option may be "accept + document".

## Acceptance criteria

- [ ] The chosen change is expressed in `.github/workflows/ci.yml` (and, if
      the invariant is lint-worthy, pinned in `tools/check_ci_review_contract.py`
      with mutation coverage in `tests/test_ci_review_contract.py`, matching
      the repo's CI-contract pattern that #179 established).
- [ ] A metadata-only PR with auto-merge armed no longer leaves a transient
      `FAILURE` on the `test` context (or the residual is proven harmless to
      auto-merge by construction).
- [ ] The #179 guarantees are preserved: an auto-merge-armed app-code PR still
      merges only on full-matrix green, and main-push commits still each get a
      completed (non-cancelled) verdict.
- [ ] CLAUDE.md's "Continuous integration" section documents the final
      behavior (it already documents #179's model — extend it, don't fork it).
- [ ] If symptom 2 is accepted rather than fixed, that decision and its
      runner-minute cost are recorded here and in CLAUDE.md.

## Notes

- Verify every version-sensitive claim about Actions semantics against current
  docs before relying on it (the repo's standing rule — several confident-but-
  wrong Actions assumptions have cost cycles before).
- This is a good candidate to validate *on itself*: because it edits
  `ci.yml`, the classifier forces full CI, so the PR self-exercises the new
  behavior.
