# kubectl logs refinements — Design (SD Work Designs, 2026-07-17)

## Overview

`--since-time`, `--tail`, and `-c/--container` are already handled
(PRD's verified note). Two pinned gaps: **`--since` (duration form) is
parsed but silently ignored** in `_render_pod_logs` — a silent no-op,
worse than unmodeled — and `--timestamps` is unmodeled (downgrades to
partial). Multi-container histories have no driving workflow → deferred
per the PRD's own defer rule.

## Proposal

- **`--since` (first):** parse the kubectl duration grammar subset
  (`30s`, `5m`, `2h`, and compounds like `1h30m`) relative to the
  simulated clock's now; apply exactly like `--since-time` (same
  filtering path — convert duration → absolute cutoff and reuse the
  existing code). Malformed durations → kubectl-shaped error, nonzero
  exit. Flag moves into the modeled set (no more silent no-op; the trace
  reflects real support).
- **`--timestamps`:** add to `_MODELED_FLAGS`; prepend each emitted line
  with an RFC3339 timestamp derived from the line's simulated log time
  (the log renderer already knows per-line times from the generated
  stream; if a line has no intrinsic time, derive deterministically from
  the entry index within the simulated window).
- `--since` + `--since-time` together: kubectl rejects the combination —
  mirror that (error, nonzero exit) rather than picking one silently.

## Boundaries And Non-Goals

- No multi-container history model, no `--follow` streaming (one-shot
  command API; same posture as the watch task's note), no log-content
  changes.

## Affected Files

`src/anomaly_metric_creator/server_ops.py` (`_render_pod_logs`, flag
tables, duration parser helper), `tests/test_server.py`,
`tests/test_server_ops_fuzz.py` (malformed durations).

## Risks And Edge Cases

- Duration parsing must be pure/deterministic (no wall clock — simulated
  clock only).
- `--timestamps` must not change the *unflagged* output bytes (existing
  assertions stay green); tests pin both shapes.
- The since-cutoff comparison uses the same time representation the
  since-time path uses — do not introduce a second parse of the log
  line times.

## Validation

- `pytest tests/test_server.py -n 0 -k logs` + fuzz; full suite.
- Manual: `kubectl logs <pod> --since 5m --timestamps` via real kubectl
  against a live serve.
