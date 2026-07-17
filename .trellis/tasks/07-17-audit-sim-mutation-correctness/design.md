# Simulator clock + command-mutation correctness — Design (SD Work Designs, 2026-07-17)

## Overview

Six reproduced defects, one PR. The two P2s: `SimulationClock.resume()`
without a paused-guard rewinds simulated time (server_ops.py:861-865);
command-mode `kubectl delete/scale/patch` write the overlay without
checking the resource exists — ghosts "succeed" and pollute state while
the API path correctly 404s (server_ops.py:3320-3400). Four P3 guards
ride along (otel_status race, regen split-brain, zero-byte CSV, negative
limit).

## Proposal

- **A-012:** `resume()` begins `if not self._paused: return` — resume on
  a running clock is a no-op. Test: pause→resume→resume leaves `now()`
  monotonic; resume-without-pause is identity.
- **A-013 (the substantive one):** in `_render_delete` / `_render_scale`
  / `_render_patch`, resolve the target against the overlay-aware
  `resource_snapshot()` **before** any `SimulationMutations` write —
  the same order the API path already enforces (and the same
  refused-mutation-must-not-leave-partial-state rule CLAUDE.md records
  for the HTTP path). On miss: kubectl-shaped stderr
  (`Error from server (NotFound): <kind> "<name>" not found`), exit 1,
  overlay untouched, trace still recorded as a supported command with
  nonzero exit. Nameless `kubectl scale` becomes a usage error (exit 1,
  kubectl-shaped) instead of silently defaulting to apigateway.
  Parity test: ghost delete via `/v1/commands` AND via the REST facade —
  both nonzero/404, snapshot identical before/after.
- **A-014:** pre-seed the known `otel_status` keys at state build and
  copy under a lock in `summary()` — `/v1/state` can never observe a
  mid-resize dict.
- **A-015:** failed regen pass reloads anomaly rows from disk (atomic
  writes guarantee complete files — disk is truth for published
  artifacts); the generation-status error field still records the
  failure. Keeps `/v1/anomalies` consistent with what MCP tools read.
- **A-016:** `next(reader, None)` in `_iter_component_rows`
  (csv_layout.py:82); `None` header → skip file with a stderr warning
  (matches sibling guards).
- **A-017:** clamp `limit` to `max(0, limit)` in `CommandTraceStore.list`
  for both backends; `limit=0` returns empty (assert both backends agree).

## Boundaries And Non-Goals

- No new mutation kinds, no API-path changes (it is already correct), no
  clock API additions.
- The scale-default removal is a deliberate behavior change for a
  nameless invocation — CHANGELOG entry, and the fuzz corpus gets the
  shape.

## Affected Files

`src/anomaly_metric_creator/server_ops.py` (clock, three renderers,
otel_status), `src/anomaly_metric_creator/server.py` (regen reload arm),
`src/anomaly_metric_creator/csv_layout.py`,
`src/anomaly_metric_creator/server_traces.py`, tests
(`test_server.py`, `test_server_ops_fuzz.py`, gauges/combine suite for
the csv_layout guard), CHANGELOG, `.trellis/audit/ledger.md` flips.

## Risks And Edge Cases

- A-013 must distinguish resource *kinds* whose renderers legitimately
  accept absent names (list-style flows) — the check applies to named
  single-target mutations only; enumerate the three renderers' arg
  shapes in the PR.
- A-016's warning path must not change gauge/combine golden hashes — a
  zero-byte CSV never occurs in generated runs (guard is for debris);
  full suite proves it.
- A-015 reload happens on the generation worker thread — reuse the same
  lock the successful-pass swap uses.

## Validation

- Acceptance tests per PRD (resume no-op; ghost-mutation parity;
  per-guard regressions). Fuzz corpus extended with ghost-name and
  nameless-scale shapes.
- `pytest tests/test_server.py tests/test_server_ops_fuzz.py
  tests/test_gauges_file.py -n 0` + full suite (hash safety for
  csv_layout).
