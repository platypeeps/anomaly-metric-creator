# kubectl events compatibility — Design (SD Work Designs, 2026-07-17)

## Overview

The base command already works (PRD's verified 2026-07-06 note:
`kubectl events` rewrites to `get events` at server_ops.py:1540; `get
events` serves via `_SNAPSHOT_KINDS`). Remaining scope is only richer
sorting/filtering — and only what incident workflows actually use.

## Proposal

Three refinements, all over the existing event view (no parallel store):

- **`--for <kind>/<name>`** — filter events to one involved object
  (the core incident move: "events for this pod"). Resolve the target
  against the snapshot; unknown target → empty list + the same
  kubectl-shaped "No events found" stdout, exit 0 (matches real
  kubectl).
- **`--types Warning[,Normal]`** — type filter (real flag on `kubectl
  events`); case-insensitive match on the event type field.
- **Deterministic default sort** — pin ascending `.lastTimestamp` (real
  `kubectl events` default) with a stable tiebreak (name) so tests and
  workshops see reproducible order. `--sort-by` support limited to the
  two JSONPaths real workflows use (`.lastTimestamp`,
  `.metadata.creationTimestamp`); any other JSONPath → **partial** trace
  via the existing `_with_flag_support` downgrade (visible demand, no
  silent misorder).

Both entry paths inherit the behavior (top-level `events` rewrites into
`get events`, so one renderer serves both). Flags land in
`_MODELED_FLAGS`/value-flag tables as required.

## Boundaries And Non-Goals

- No field-selector emulation, no `--watch` here (the watch task owns
  streams), no event-store changes, no API-path `?fieldSelector`
  work (command mode only; the REST list already serves clients).

## Affected Files

`src/anomaly_metric_creator/server_ops.py` (event renderer + flag
tables), `tests/test_server.py`, `tests/test_server_ops_fuzz.py`
(malformed `--for` shapes).

## Risks And Edge Cases

- `--for` parsing accepts `kind/name` with kind aliases — reuse
  `_KIND_ALIASES` rather than a new mapping (single-source rule).
- Sort must be applied after overlay-event merge so mutation-appended
  events order correctly.
- Deterministic order may differ from today's insertion order — check
  existing event-render assertions before pinning the new default
  (test edits are expected and enumerated, not silent).

## Validation

- `pytest tests/test_server.py -n 0 -k event` + fuzz corpus; full suite.
- Manual: `kubectl events --for pod/<name>` and `--types Warning`
  against a live serve via the real-client kubeconfig path.
