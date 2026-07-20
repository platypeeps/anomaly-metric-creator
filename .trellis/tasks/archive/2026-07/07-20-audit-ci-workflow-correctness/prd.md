# Fix CI workflow selection and guard runtime

## Goal

Implement audit items A-047, A-049, A-051, A-053 and the Trellis audit-path classifier addendum as one workflow-correctness PR.

## Requirements

- Close A-047 by making the `labeled` event honor the existing
  `PR_AUTO_MERGE` signal so an armed pull request always selects the full
  application matrix. Pin the behavior in the CI-review contract tests.
- Close A-051 by forcing the application lane for `workflow_dispatch`.
- Close A-053 by installing `uv` and running the lightweight guards under
  Python 3.14 instead of the runner's unpinned system Python.
- Close A-049 by lightweight-classifying `.sd-ai-command-pack/**`, syntax
  checking the command-pack toolchain and shell library in every existing
  shell gate, and including top-level `scripts/*.py` in Python syntax checks.
- Lightweight-classify `.trellis/audit/**` alongside the other Trellis
  documentation/state paths.
- Preserve the existing three-lane CI model, CodeQL label semantics, aggregate
  `CI Result` context, and the rule that auto-merge never lands on quick-lane
  evidence.
- Update the audit ledger entries covered by this child in the same pull
  request. Do not edit the upstream command-pack repository.

## Acceptance Criteria

- [ ] A labeled-event fixture for an auto-merge-armed PR selects the full
      matrix and fails if the `PR_AUTO_MERGE` clause is removed.
- [ ] Manual workflow dispatch forces `app_required=true`.
- [ ] The lightweight guards execute through `uv run --python 3.14`.
- [ ] Diffs limited to `.sd-ai-command-pack/**` or `.trellis/audit/**`
      classify lightweight, while application, dependency, and workflow paths
      retain their existing escalation behavior.
- [ ] Both shell syntax-gate locations include the toolchain and shell library;
      Python syntax coverage includes `scripts/*.py`.
- [ ] Focused classifier/contract tests and the repository's required local
      review gate pass.
- [ ] A-047, A-049, A-051, and A-053 are marked `fixed` in
      `.trellis/audit/ledger.md`; the session-discovered audit-path addendum is
      recorded as completed.

## Notes

- Child 1 of `07-17-audit-ci-cadence-closures`; it must merge before the lint
  parity and automation/portability children.
