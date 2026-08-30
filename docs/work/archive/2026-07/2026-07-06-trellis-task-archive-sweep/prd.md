---
title: Archive completed Trellis tasks
status: done
created: 2026-07-06
branch: chore/trellis-archive-sweep
---
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

- [x] No `status=completed` task remains in the active
      `.trellis/tasks/` top level.
- [x] `task.py list` / `list-archive` both render correctly afterward.
- [x] The decomposition epic still resolves its children; Trellis
      placeholder lint passes on the moved artifacts.

## Resolution (2026-07-06)

All 15 completed tasks archived to `archive/2026-07/` via
`task.py archive` (the 14 from the review inventory plus
`06-29-debug-ui-shell-extraction`, closed during the review). Verified:
zero `completed` tasks remain active; `task.py list` shows only
planning/in_progress work; `list-archive 2026-07` renders the moved set;
the decomposition epic resolves all 10 children across the archive
boundary and reports `[6/10 done]`. This task archives itself in the
same PR as the sixteenth move. `docs/repomix-map.md` regeneration is
deliberately deferred to `07-06-docs-refresh-sweep`, which owns the
repomix refresh (the map was already stale from PRs #206/#207's 16 new
task dirs before this sweep).

## Notes

- Purely mechanical; good batching candidate with the next housekeeping
  run. Kept as its own task (rather than done inline during the review)
  because moving 15 directories is noisy in a review PR.
