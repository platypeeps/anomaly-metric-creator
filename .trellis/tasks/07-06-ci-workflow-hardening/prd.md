# Harden CI workflows: SHA pins, timeouts, PR-body wiring, full-ci label

## Review context

- **Source:** deep-dive tooling/CI review, 2026-07-06.
- **Confidence:** CONFIRMED (each item verified in the workflow files).
- **Severity:** MEDIUM aggregate — individually small, together they are
  the mechanical CI gaps left after the cadence work.
- **Category:** CI hygiene / supply chain.

## Goal

Close the four verified workflow gaps: mutable action tags, missing job
timeouts on merge-blocking jobs, a lint step that silently no-ops, and a
label with two different lifetimes.

## Problem (verified 2026-07-06)

1. **No action is SHA-pinned** (ci.yml, codeql.yml, socket.yml,
   dependabot-auto-merge.yml all use mutable tags) — CLAUDE.md's own
   checklist says "SHA-pin actions where practical". Highest value:
   `astral-sh/setup-uv` (third-party, cache-enabled, feeds the test job)
   and `dependabot/fetch-metadata` (runs under `pull_request_target`
   holding `contents: write`).
2. **Missing `timeout-minutes`:** the `socket` job (a required
   branch-protection context doing network work) and ci.yml's `changes`
   classifier job (every lane `needs:` it); a hang holds merges for the
   360-minute default. The trivial `test` aggregate job also has none.
3. **PR-body scope check silently no-ops:** the lightweight lane runs
   `scripts/sd-ai-command-pack-pr-body-scope.py` with no arguments; the
   script's contract exits 0 when no body is supplied, and CI never
   passes `github.event.pull_request.body` — yet
   `docs/DEVELOPMENT_CYCLE.md` presents the step as a guard.
4. **`full-ci` label has two lifetimes:** one-shot in ci.yml/socket.yml
   (honored only at the `labeled` event; later plain `synchronize` drops
   to the quick lane) but persistent in codeql.yml (re-analyzes every
   push while labeled). Each behavior is separately documented, but one
   label carrying two semantics is a trap.
5. **`.opencode/package.json` is Dependabot-managed but classified as
   review tooling** *(added 2026-07-07, review-ledger completion)*: the
   `npm` ecosystem in `dependabot.yml` manages it, but
   `scripts/classify-ci-changes.sh` routes `.opencode/*` to the
   review-tooling → lightweight lane and leaves `dependency_changed`
   false, so an npm bump there skips both the full test matrix on
   synchronize and socket's dependency re-scan gate. Mitigations already
   in place (socket scans at `opened`; auto-merge forces the full matrix
   on the armed head) make this small — either classify the path (or
   `**/package.json`) as a dependency change, or record the accepted
   risk in the classifier comments.
6. **`src/` has no CI lint coverage beyond report-only mypy** *(added
   2026-07-07)*: CI runs `ruff check tests/` only (repo-wide rule set is
   F401), and the F841 pre-commit hook scoped to src/tools/hooks never
   runs in CI (local hooks are explicitly not a CI gate in this repo).
   Decide the CI-enforced rule set for `src/` (at minimum mirror the
   pre-commit F401/F841 scopes in the quick + full lanes) and wire it in,
   or record why src-side lint stays local-only.

## Requirements

- SHA-pin all third-party actions (Dependabot's `github-actions`
  ecosystem keeps SHA pins fresh); pin or explicitly waive first-party
  actions in a comment.
- Add `timeout-minutes` to the socket, changes, and `test` aggregate
  jobs.
- Either wire the PR body into the scope step
  (`env: PR_BODY: ${{ github.event.pull_request.body }}` + the script's
  documented env/`--body-file` input) or delete the step and the
  DEVELOPMENT_CYCLE.md claim.
- Unify the `full-ci` label lifetime across the three workflows; document
  the chosen semantics and pin them in
  `tools/check_ci_review_contract.py`.
- Optional (decide in-task): a 3-line CI job running
  `tools/check_branch_name.py` against `github.head_ref`, closing the
  documented no-CI-backstop gap for the branch-name lint.

## Acceptance Criteria

- [x] Zero mutable third-party action tags across the four workflows.
- [x] All merge-blocking jobs carry `timeout-minutes`.
- [ ] The PR-body scope step provably enforces (test or lint anchor) or
      is removed everywhere it is claimed. — **still deferred** (item 7;
      the pack-side bot-skip shipped in sd-ai-command-pack#61, but the
      repo-side ci.yml wiring is intentionally out of the "items 1–6"
      batch and awaits an explicit enforce decision).
- [x] `full-ci` lifetime documented and contract-lint-pinned — **not**
      unified. The one-shot (ci.yml/socket.yml) vs persistent (codeql.yml)
      split is kept deliberately (unifying CodeQL to one-shot would cut
      security coverage); documented in DEVELOPMENT_CYCLE.md + CLAUDE.md and
      pinned in `check_ci_review_contract.py` (codeql persistent-re-check
      positive anchor + ci.yml `_require_not_contains` guard).
- [x] An `.opencode/package.json` bump routes to the dependency lane
      (`is_dependency_path` now matches `package.json`/`package-lock.json`
      at any depth), covered by `tests/test_ci_change_classifier.py`
      (`test_opencode_package_json_forces_dependency_lane`).
- [x] The `src/` CI lint decision is implemented: the quick and full lanes
      run `ruff check --select F841` on `src tools .codex/hooks
      .github/copilot/hooks .gemini/hooks`, mirroring the pre-commit F841
      hook. F401 stays tests-only (src/ facades re-export deliberately).
- [x] The `no merge base` finding is fixed: the classifier + socket
      "Collect changed files" and the lightweight whitespace step drop the
      `--depth=1` shallow re-fetch (checkout is already `fetch-depth: 0`),
      with a two-dot fallback on the `--name-only` collects (safe there;
      omitted on `git diff --check` where `||` would mask real failures).
- [x] `check_ci_review_contract.py` and its tests updated for any anchors
      this task adds; existing anchors untouched. — no new anchors in the
      shipped subset; SHA pins are Dependabot-maintained (an anchor would
      fight the bumps), timeouts are additive.

## Progress (2026-07-07) — safe subset shipped, merge-gate items deferred

**Shipped:** all five actions across the four workflows are SHA-pinned with
`# vX` comments (`actions/checkout` v7, `actions/setup-python` v6,
`astral-sh/setup-uv` v8.3.0, `dependabot/fetch-metadata` v3,
`github/codeql-action` v4 — Dependabot's `github-actions` ecosystem keeps
the pins fresh), and the three merge-blocking jobs that lacked a timeout
now have one (`changes` 10m, `socket` 15m, the `test` aggregate 5m). Both
are unambiguous wins, self-verified by the PR's own full CI run.

**Deferred with analysis — these change the *shared merge gate* for every
contributor, so they warrant a deliberate decision rather than an
autonomous end-of-session change:**

- **PR-body scope enforcement is NOT safe to simply wire in.** The scope
  script fails (exit 1) when a PR body *is* supplied but lacks the scope
  heading matching the changed paths. Wiring `github.event.pull_request.body`
  into the lightweight lane would therefore **break Dependabot auto-merge**:
  a Dependabot PR touches dependency files (→ needs a "dependency" scope
  heading) but its body never carries one, so the scope step would fail and
  block the auto-merge the repo relies on. Options for the focused pass:
  (a) delete the no-op step + the `DEVELOPMENT_CYCLE.md` claim; (b) wire it
  in *with* a `github.actor == 'dependabot[bot]'` (and bot-actor) skip.
  Decide deliberately.
- **`full-ci` label lifetime unification** changes CI cadence (ci.yml/
  socket.yml one-shot at the `labeled` event vs codeql.yml persistent on
  every synchronize). Aligning codeql to one-shot is the consistent choice
  (matches the deliberate cadence design; codeql is advisory so low cost),
  but it is a cadence change worth an explicit nod + a contract anchor +
  mutation test.
- **`.opencode/package.json` classification** and the **`src/` CI-lint
  decision** are lower-risk but touch the classifier / lint surface; fold
  them into the focused pass (the src/ lint decision also overlaps
  `07-06-coverage-threshold-and-mypy-gating`).

### New finding (2026-07-07, discovered on PR #219): fragile `git diff` merge-base

The `changes` and `socket` jobs' **"Collect changed files"** step shallow-
fetches `origin/$BASE_REF` at `--depth=1`, then runs a three-dot
`git diff --name-only "origin/$BASE_REF...HEAD"`. When the PR branch has
fallen **behind** the target branch (e.g. another PR merges during this PR's
CI lane), the merge-base is no longer inside the depth-1 shallow fetch and
the step dies with `fatal: origin/$BASE_REF...HEAD: no merge base` (exit
128), failing the required `test` context. PR #219 hit this after #218
merged mid-lane; a rebase onto the current tip worked around it, but that is
a manual fix every stale PR would need. Fold a real fix into the focused
pass — deepen the fetch (drop `--depth=1` or use `--deepen`) or fall back to
a two-dot `git diff origin/$BASE_REF HEAD` when the three-dot form fails.
The `changes` job's own checkout already uses `fetch-depth: 0`, so the
shallow re-fetch is what re-shallows `origin/$BASE_REF` — the two are in
tension.

## Notes

- Dependabot auto-merge and the `!cancelled()` aggregate guard were
  verified correct in the same review — do not touch them here.
