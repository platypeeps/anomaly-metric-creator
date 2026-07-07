# Archive completed Trellis tasks

## Review context

- **Source:** deep-dive review, 2026-07-06 (backlog-hygiene check).
- **Confidence:** CONFIRMED.
- **Severity:** LOW — housekeeping; the active task tree over-states
  remaining work.
- **Category:** Trellis hygiene.

## Goal

Bring `.trellis/tasks/` back to the intended shape: active work in the
top-level directory, completed work under `archive/<month>/`.

## Problem (verified 2026-07-06)

15 `completed` tasks sit un-archived in the active tasks directory
(the 14 found by the review — atomic-artifact-writes,
audit-server-ops-rendering, ci-typecheck-and-coverage, decomp-artifacts,
decomp-combine-impl, decomp-gauges-csv-layout, decomp-timeutil-otlp, the
five mcp-* tasks, server-remote-bind-hardening,
ci-cadence-churn-refinement — plus 06-29-debug-ui-shell-extraction, closed
as completed-by-prior-work during the review) while only five tasks live
in `archive/`. The inconsistency is visible in the decomposition epic:
steps 6-7 were archived, steps 2-5 were not. `task.py archive <task-dir>`
is the existing flow.

## Requirements

- Run `python3 .trellis/scripts/task.py archive <dir>` for every
  `status=completed` task in the active directory.
- Preserve epic linkage: `07-02-legacy-monolith-decomposition` is
  `in_progress` and stays active; verify its `children` references remain
  resolvable after its completed children move to `archive/` (the two
  already-archived children set the precedent).
- Spot-check that `docs/repomix-map.md` regeneration
  (`scripts/update_repomix`) is run afterward if the map lists moved
  paths.

## Acceptance Criteria

- [ ] No `status=completed` task remains in the active
      `.trellis/tasks/` top level.
- [ ] `task.py list` / `list-archive` both render correctly afterward.
- [ ] The decomposition epic still resolves its children; Trellis
      placeholder lint passes on the moved artifacts.

## Notes

- Purely mechanical; good batching candidate with the next housekeeping
  run. Kept as its own task (rather than done inline during the review)
  because moving 15 directories is noisy in a review PR.
