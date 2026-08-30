---
title: Fix simulator clock and command-mutation correctness gaps
status: done
created: 2026-07-17
branch: sdelmas/sim-mutation-correctness
---
# Fix simulator clock and command-mutation correctness gaps

## Audit context

- **Source:** 2026-07-17 full repo audit @ b0df00b — report:
  `.trellis/audit/report-2026-07-17.md`; per-item evidence + fix sketches:
  `.trellis/audit/ledger.md`.
- **Ledger items:** A-012 (P2·S), A-013 (P2·M), A-014 (P3·S), A-015 (P3·M), A-016 (P3·S), A-017 (P3·S)

## Goal

Live-reproduced simulator-state defects: an unpaired clock resume rewinds simulated
time; command-mode kubectl delete/scale/patch succeed on nonexistent resources and
pollute the overlay (the API path 404s correctly); plus four smaller races/guards.

## Scope (ledger items)

- A-012 — guard resume() with `if self._paused` so resume on a running clock is a no-op.
- A-013 — resolve targets against resource_snapshot() before overlay writes in _render_delete/_render_scale/_render_patch; kubectl-shaped NotFound + nonzero exit on miss; nameless scale = usage error, not apigateway default.
- A-014 — lock + copy (or pre-seed) otel_status so /v1/state can't 500 during OTEL startup.
- A-015 — reload anomaly rows from disk on failed regen pass (disk is truth for published artifacts) or surface the divergence.
- A-016 — next(reader, None) guard in _iter_component_rows for zero-byte CSVs.
- A-017 — clamp negative ?limit= in CommandTraceStore.list (both memory and SQLite backends).

## Acceptance criteria

- [x] POST /v1/time/resume on a running clock leaves now() unchanged.
- [x] kubectl delete of a ghost resource exits nonzero on BOTH entry paths; overlay untouched.
- [x] Regression tests for each P3 guard.
- [x] Closing PR flips each covered ledger item to `status: fixed` in
      `.trellis/audit/ledger.md` (same-PR, per ledger rules).
