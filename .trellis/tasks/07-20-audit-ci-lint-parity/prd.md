# Mirror CI lints and local gates

## Goal

Implement audit items A-048, A-052, A-060, A-061, and A-062 as one CI/local parity PR.

## Requirements

- Move the canonical 19-module mypy gate list out of workflow YAML into a
  repository-owned checker shared by CI and local preflight (A-048).
- Mirror the role-name, AMC-module-load, and agent-hook-exception guards in CI
  (A-060), and expand the role-name live-tree roots to `src/`, `scripts/`,
  `.agents/`, and `.trellis/` (A-052).
- Check pull-request branch names against `github.head_ref` in the changes job
  (A-061), and wire the role-name guard to scan commit-message files at the
  `commit-msg` pre-commit stage (A-062).
- Resolve the live Ruff 0.15.22 dependency update in lockstep across the dev
  dependency, pre-commit hook, and lockfile so the existing parity guard stays
  green and Dependabot PR #259 can be superseded cleanly.
- Document local mypy and commit-msg setup where users actually follow the
  development cycle; update the covered audit ledger entries in the same PR.

## Acceptance Criteria

- [x] CI and local preflight consume one mypy module list, and a test fails if
      the workflow grows a second inline list.
- [x] The three fast guards run in the lightweight CI guards step and remain
      within its practical runtime budget.
- [x] Role-name scanning covers every new root without false positives in
      audit/task artifacts.
- [x] A nonconforming PR branch fails the changes job, and the role-name
      commit-msg hook is installed/documented without weakening ordinary
      commit behavior.
- [x] Ruff 0.15.22 is pinned consistently in `pyproject.toml`,
      `.pre-commit-config.yaml`, and `uv.lock`; the lockstep lint passes.
- [x] Focused lint tests, pre-commit, and the required local review gate pass.
- [x] A-048, A-052, A-060, A-061, and A-062 are `fixed` in the ledger.

## Notes

- Child 2 of `07-17-audit-ci-cadence-closures`; execute after the workflow
  correctness child merges.
- A-050 requires changing a pack-vouched installed file. Keep it open for an
  upstream command-pack change rather than introducing consumer provenance
  drift in this task.
