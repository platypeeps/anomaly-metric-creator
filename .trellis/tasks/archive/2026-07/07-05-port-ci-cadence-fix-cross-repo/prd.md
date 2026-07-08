# Port the CI cadence churn fix (!cancelled guard) to other repos

> **Handoff note.** This task is self-contained for a fresh session with no
> prior context. It captures a cross-repo CI-hygiene port originating from
> `anomaly-metric-creator`. Everything needed to decide *whether* and *how*
> to port is below; the actual porting requires access to the target repos.

## Origin (what was fixed here, and why it may apply elsewhere)

Two CI-cadence fixes landed in `anomaly-metric-creator` and are the subject
of this port:

1. **PR #179 — auto-merge full-gate + per-commit main concurrency.** Closed
   a gap where arming GitHub auto-merge on a PR, then pushing a follow-up
   commit, downgraded the run to a cheap "quick" lane and let auto-merge
   land code on `main` without a full-suite run. #179 made auto-merge-armed
   PRs request full CI (via the `auto_merge_enabled` event and a
   `synchronize` gate on `github.event.pull_request.auto_merge`), and gave
   `push` events **per-commit** concurrency groups
   (`ci-${{ github.event_name == 'push' && github.sha || github.ref }}`) so
   merge-burst `main` pushes stop cancelling each other's backstop runs.

2. **PR #197 — aggregate `test` job `!cancelled()` guard (THE key one-liner).**
   #179's auto-merge full-gate triggers a fresh full run when auto-merge is
   armed, and `concurrency: cancel-in-progress` cancels the in-progress lane.
   The repo's stable aggregate job (`test`, required by branch protection)
   was guarded with `if: ${{ always() }}`, which runs the job **even while
   the run is being cancelled** — it then evaluated
   `test "cancelled" = "success"` and exited 1, flashing a **transient red
   `FAILURE`** on the required `test` context of *every auto-merge-armed PR*.
   The fix: `if: ${{ !cancelled() }}`. Now the aggregate is cancelled *with*
   the run, so its `test` context reports `cancelled` — which does not
   satisfy branch protection (auto-merge correctly waits for the superseding
   run's real verdict) and is not a red failure. A genuine lane failure is
   not a cancellation, so the aggregate still runs and fails on it; this
   cannot mask a real failure, and unlike a "treat cancelled as success"
   hack it cannot let auto-merge fire before the superseding run finishes.
   **Self-validated:** PR #197's own `test` conclusions were
   `[CANCELLED, SUCCESS]` (would have been `[FAILURE, SUCCESS]` pre-fix).

Reference implementation in this repo (read these before porting):
- `.github/workflows/ci.yml` — the aggregate `test` job (guard =
  `if: ${{ !cancelled() }}`) and the `concurrency` / cadence blocks.
- `tools/check_ci_review_contract.py` — the `aggregate cancellation-safe
  guard` anchor (and the #179 anchors: `auto-merge synchronize gate`,
  `per-commit push concurrency`, etc.).
- `tests/test_ci_review_contract.py::test_reverting_aggregate_guard_to_always_fails`
  — mutation test proving a revert to `always()` is caught.
- CLAUDE.md → "Continuous integration and Dependabot auto-merge" section.

## The portable principle (port this, not the file)

> **Any CI job that is (a) required by branch protection and (b) subject to
> `concurrency: cancel-in-progress` must guard with `if: ${{ !cancelled() }}`,
> never `if: ${{ always() }}`.** `always()` makes the required context flash
> `FAILURE` whenever the run is superseded/cancelled; `!cancelled()` makes it
> report `cancelled` instead, which blocks nothing and flashes no red.

Do **not** copy `ci.yml` wholesale — most of it is repo-specific (the path
classifier, the heavy/not-heavy pytest split, AMC paths).

## Which repos are affected (preconditions — all three required)

A target repo needs this fix **iff** it has all of:
1. Uses GitHub **auto-merge** (squash or otherwise) on PRs.
2. Has a **stable aggregate job** (a `needs:`-fan-in job giving branch
   protection one required context) guarded with `if: ${{ always() }}`.
3. Uses `concurrency:` with `cancel-in-progress: true` on PR refs.

Missing any one → not affected. **Strong signal:** a repo that already
adopted #179's pattern (per-commit main concurrency + auto-merge full-gate)
but kept `always()` on its aggregate now has this exact transient — #179 and
#197 are a **pair**.

## What is NOT a propagation path

- **`sd-ai-command-pack` does not manage `ci.yml`** (verified: not in
  `.sd-ai-command-pack/installed-targets.txt`). There is no "fix once in the
  pack, update everywhere" route — ports are manual, per repo.
- **`mock-mcp-service`** (the sibling repo in this workspace) has **no
  `.github/workflows/` at all** — not affected, nothing to port.

## Requirements (for the porting session)

- For each candidate repo the operator names / grants access to:
  1. Read `.github/workflows/*.yml`; find the branch-protection-required
     aggregate job and check its `if:` guard and the PR `concurrency` block.
  2. If preconditions 1–3 hold and the guard is `always()`, change it to
     `if: ${{ !cancelled() }}` with a comment pointing at this rationale.
  3. If the repo also lacks #179's auto-merge full-gate and uses auto-merge,
     evaluate porting that too (bigger change; scope separately).
  4. If the repo has a CI-contract lint, add an equivalent
     `!cancelled()` anchor + mutation test; otherwise note the invariant in
     that repo's CI docs so it is not reverted.
  5. Ship as its own PR in that repo (workflow change → its CI self-tests
     the guard; a `[CANCELLED, SUCCESS]` history confirms the fix).
- Record per-repo outcome (ported / not-applicable / needs-#179-first).

## Acceptance criteria

- [x] Every candidate repo the operator provides is audited against the
      three preconditions, with a recorded verdict (see Results).
- [x] Affected repos get the `!cancelled()` guard, each in its own PR
      (hoa-manager #92, rwbp-website #109, rwbp-coordinator #97,
      loadsmith #63). None of the target repos carry a CI-contract lint, so
      no anchor/mutation test applied there; the invariant is pinned inline
      via the "Do not revert to always()" rationale comment on each
      `ci_result` guard. (amc, the origin, keeps its contract-lint anchor +
      `test_reverting_aggregate_guard_to_always_fails` mutation test.)
- [x] Repos needing #179's auto-merge full-gate first are flagged: none. The
      three rwbp/loadsmith repos have no auto-merge workflow (so the #197
      transient-red is low-frequency there — manual re-push/re-run only, not
      auto-merge arming), and hoa already gates its expensive lane on the
      `full-ci` trigger. Only the `!cancelled()` half of the #197/#179 pair
      was needed anywhere.
- [x] A short per-repo results summary is recorded (see Results).

## Results (2026-07-08)

Candidate set = every platypeeps repo with a `ci.yml` fan-in aggregate under
`concurrency: cancel-in-progress`. The Hugo/static sites
(`www_platypeeps_com`, `copper-hugo-*`, …) have no such workflow, and
`sd-ai-command-pack` uses a non-aggregate `tests.yml` — all n/a. Four repos
qualified, and a per-`ci_result`-block audit found **all four affected** (an
initial coarse grep mis-read the `${{ }}`-wrapped guards and under-reported;
the authoritative audit pulled each aggregate's `if:` directly):

| Repo | `ci_result` guard was | auto-merge | Verdict | Fix PR (merged) |
|---|---|---|---|---|
| hoa-manager | `if: ${{ always() }}` | yes | affected | #92 |
| rwbp-website | `if: ${{ always() }}` | no | affected (low-freq) | #109 |
| rwbp-coordinator | `if: always()` | no | affected (low-freq) | #97 |
| loadsmith | `if: ${{ always() }}` | no | affected (low-freq) | #63 |

Each fix is the one-line `always()` → `!cancelled()` guard on the
branch-protection-required `ci_result` aggregate, with the ported rationale
comment. All four PRs merged 2026-07-08. Task complete.

## Notes / decisions to make

- **Durable home for the rule:** if the operator maintains several repos on
  this cadence, consider encoding the `!cancelled()` rule in shared guidance
  (e.g. the command-pack's CI docs or a shared workflow-lint) so new repo
  setups start correct instead of re-discovering the churn. Decide during
  the port.
- **Verify Actions semantics against current docs** before relying on them
  (repo standing rule): `always()` "returns true even when cancelled";
  `!cancelled()` runs on success/failure but not cancellation. Both are
  documented status-check functions.
- Candidate repos are **not enumerable from this repo** — the operator must
  name them or add them as working directories for the porting session.
