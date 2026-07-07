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

- [ ] Zero mutable third-party action tags across the four workflows.
- [ ] All merge-blocking jobs carry `timeout-minutes`.
- [ ] The PR-body scope step provably enforces (test or lint anchor) or
      is removed everywhere it is claimed.
- [ ] One documented `full-ci` lifetime, contract-lint-pinned.
- [ ] An `.opencode/package.json` bump routes to the dependency lane (or
      the accepted risk is recorded in the classifier), covered by
      `tests/test_ci_change_classifier.py`.
- [ ] The `src/` CI lint decision is implemented or recorded; if
      implemented, the quick and full lanes run it.
- [ ] `check_ci_review_contract.py` and its tests updated for any anchors
      this task adds; existing anchors untouched.

## Notes

- Dependabot auto-merge and the `!cancelled()` aggregate guard were
  verified correct in the same review — do not touch them here.
