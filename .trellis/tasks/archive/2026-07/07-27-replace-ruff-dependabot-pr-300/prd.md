# Replace Ruff Dependabot PR 300 with lockstep pins

## Goal

Replace red Dependabot PR #300 with a reviewable human-authored dependency PR
that upgrades Ruff and `ruff-pre-commit` to 0.16.0 together, preserves the
repository's explicit lint scopes, and can pass the normal exact-head merge
gates.

## Background

- PR #300 changes only `.pre-commit-config.yaml` from `v0.15.22` to `0.16.0`.
- CI run `30264742181` fails deterministically in `test light (py3.14)` because
  `tools/check_ruff_lockstep.py` finds `pyproject.toml` still pinned to
  `ruff==0.15.22`; the aggregate `test` and `CI Result` jobs fail downstream.
- The lockstep contract is declared at `pyproject.toml:50-53`,
  `.pre-commit-config.yaml:5-10`, and
  `.trellis/spec/amc/backend/testing-quality.md` under the Ruff pin guidance.
- `uv.lock:51` and `uv.lock:481-483` currently resolve Ruff 0.15.22.
- Ruff 0.16.0 expands its default rule set and changes formatter behavior, but
  this repository's lint gates use explicit F401/F841 selections and do not
  invoke Ruff formatting. The upgrade still requires full validation rather
  than assuming compatibility from the version label.

## Requirements

- Create a human branch from clean, synchronized `main`; do not edit or push to
  the Dependabot branch.
- Set the Ruff dev-extra pin in `pyproject.toml` and the
  `astral-sh/ruff-pre-commit` revision in `.pre-commit-config.yaml` to 0.16.0 in
  the same change.
- Regenerate `uv.lock` with the repository's uv toolchain so Ruff 0.16.0 and its
  platform artifacts are recorded consistently.
- Keep the change dependency-only. Do not weaken lint selections, CI checks,
  lockstep validation, or coverage thresholds; do not perform unrelated lint
  cleanup or dependency upgrades.
- Publish and review the replacement through the normal SD create/review,
  exact-head CI, and guarded housekeeping flow.
- Close PR #300 as superseded only after the replacement PR is confirmed
  merged. Preserve the Dependabot branch unless separately authorized to
  delete it.

## Acceptance Criteria

- [ ] `pyproject.toml`, `.pre-commit-config.yaml`, and `uv.lock` all resolve
      Ruff 0.16.0 with no unrelated dependency movement.
- [ ] `tools/check_ruff_lockstep.py` reports the live pins in lockstep.
- [ ] `tests/test_ruff_lockstep_lint.py` passes.
- [ ] Ruff F401 over `tests/` and Ruff F841 over the configured
      runtime/tools/hooks paths pass under Ruff 0.16.0.
- [ ] The repository's full deterministic local gate passes, with any optional
      provider-only result reported separately rather than treated as evidence.
- [ ] The replacement PR has green required CI/CodeQL, exact-head review, and
      zero unresolved review threads before merge.
- [ ] Guarded housekeeping confirms the merge, returns the checkout to clean
      synchronized `main`, and refreshes approved generated knowledge-base
      output if required.
- [ ] PR #300 is closed with a link to the merged replacement.

## Out of Scope

- Changing Dependabot's repository-wide update strategy.
- Adopting Ruff's newly expanded default rule set beyond the repository's
  existing explicit F401/F841 gates.
- Running Ruff formatting or reformatting Markdown/Python files.
- Deleting superseded local or remote branches.
