# Evaluate debug UI shell extraction

## Goal

Consider extracting the inline debug UI shell from server.py only when it reduces maintenance risk and remains strongly covered by endpoint behavior tests.

## Requirements

- Evaluate extracting the inline debug UI shell only if doing so reduces maintenance risk.
- Keep any extraction behavior-preserving and strongly covered through endpoint behavior tests.
- Preserve the existing `server.py` facade compatibility expectations, including `DEBUG_HTML` re-export behavior if still required.
- Avoid broad frontend rewrites; migrated backlog notes called for incremental debug UI changes.
- If extraction is not yet justified, document the decision and leave the existing structure intact.

## Acceptance Criteria

- [x] The task starts with a concrete go/no-go decision for extraction based on current code structure and maintenance cost.
- [x] If extraction proceeds, debug UI HTML/CSS/JS behavior remains equivalent through focused endpoint tests.
- [x] If extraction is deferred, the PR records why and does not churn debug UI files unnecessarily.
- [x] `tests/test_server.py` architecture-boundary expectations remain accurate.

## Notes

- Source: migrated server-mode debug UI backlog entry.
- Treat this as a maintenance decision, not an automatic extraction mandate.

## Resolution (2026-07-06 review — already shipped before this task was filed)

The extraction this task asks to evaluate **already exists on `main`** and
predates the task's 2026-06-29 creation: `server_debug_ui.py` was introduced
in commit `3dcd944` (PR #140, merged 2026-06-25) as part of the server-module
split, and later touched by PRs #152 and #175. Verified on 2026-07-06:

- The shell lives wholly in `server_debug_ui.py` (~1,189 lines, a single
  `DEBUG_HTML` constant, zero package-internal imports — a leaf module).
- `server.py` imports it at [server.py:28](src/anomaly_metric_creator/server.py:28)
  (`from .server_debug_ui import DEBUG_HTML`), preserving the module-level
  `server.DEBUG_HTML` re-export, and serves it at
  [server.py:559](src/anomaly_metric_creator/server.py:559).
- No inline HTML remains in `server.py`.
- Coverage: `tests/test_server.py:2624-2647` asserts the facade identity and
  the `DEBUG_HTML` content markers; endpoint behavior tests exercise `/debug`.

Task closed as completed-by-prior-work; the backlog entry it migrated from
was stale at migration time. No PR is attributable to this task itself.
