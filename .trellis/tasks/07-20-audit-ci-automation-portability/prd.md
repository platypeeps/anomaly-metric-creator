# Add CI automation and Windows collection coverage

## Goal

Implement audit items A-063 and A-065, subject to maintainer confirmation before recurring automation is merged.

## Requirements

- Add a weekly command-pack synchronization workflow that opens a pull request
  only when the generated/install state changes and reuses the repository's
  existing auto-merge/full-gate contract (A-063).
- Add an advisory `windows-latest` job that installs the development
  environment and runs `pytest --collect-only` without becoming a required
  branch-protection context (A-065).
- Fix collection-time portability defects exposed by the advisory job within
  the same scope when they are small and directly caused by the lane.
- Obtain an explicit maintainer decision before merging the recurring
  pack-sync automation. If approval is unavailable, park this child without
  weakening or silently omitting that half.
- Update user/agent CI documentation and the covered audit ledger entries in
  the same PR.

## Acceptance Criteria

- [ ] A no-change scheduled run creates no branch or pull request; a genuine
      refresh produces one reviewable PR through the existing gate.
- [ ] Windows collection succeeds with the declared Python floor and locked
      development dependencies.
- [ ] Windows failures are advisory and cannot make the aggregate required
      context red.
- [x] Recurring automation has explicit maintainer approval before merge
      (approved by the maintainer on 2026-07-20).
- [ ] A-063 and A-065 are marked `fixed` only when their behavior is actually
      shipped.

## Notes

- Child 3 of `07-17-audit-ci-cadence-closures`; execute after both earlier
  children. This child contains a user-input gate and may be parked cleanly.
