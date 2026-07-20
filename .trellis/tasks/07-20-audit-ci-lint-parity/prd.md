# Mirror CI lints and local gates

## Goal

Implement audit items A-048, A-050, A-052, A-060, A-061, and A-062 as one CI/local parity PR.

## Requirements

- Move the canonical 19-module mypy gate list out of workflow YAML into a
  repository-owned checker shared by CI and local preflight (A-048).
- Mirror the role-name, AMC-module-load, and agent-hook-exception guards in CI
  (A-060), and expand the role-name live-tree roots to `src/`, `scripts/`,
  `.agents/`, and `.trellis/` (A-052).
- Check pull-request branch names against `github.head_ref` in the changes job
  (A-061) and wire the same guard at the `commit-msg` pre-commit stage (A-062).
- Make the full-check preflight fall back to `python3` when Node is unavailable,
  preserving the command pack's portability posture (A-050).
- Document local mypy and commit-msg setup where users actually follow the
  development cycle; update the covered audit ledger entries in the same PR.

## Acceptance Criteria

- [ ] CI and local preflight consume one mypy module list, and a test fails if
      the workflow grows a second inline list.
- [ ] The three fast guards run in the lightweight CI guards step and remain
      within its practical runtime budget.
- [ ] Role-name scanning covers every new root without false positives in
      audit/task artifacts.
- [ ] A nonconforming PR branch fails the changes job and the commit-msg hook is
      installed/documented without weakening ordinary commit behavior.
- [ ] Full-check works without Node by selecting the tested Python fallback.
- [ ] Focused lint tests, pre-commit, and the required local review gate pass.
- [ ] A-048, A-050, A-052, A-060, A-061, and A-062 are `fixed` in the ledger.

## Notes

- Child 2 of `07-17-audit-ci-cadence-closures`; execute after the workflow
  correctness child merges.
