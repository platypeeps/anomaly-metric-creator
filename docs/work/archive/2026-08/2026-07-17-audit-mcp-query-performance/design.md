# MCP tool scans + trace-store hot paths — Design (SD Work Designs, 2026-07-17)

## Overview

Four measured hot paths: MCP analysis tools full-parse every CSV row per
call (0.137s/component regardless of window; ~2s per correlated
timeline), `unsupported_summary()` deserializes the full non-supported
history on the debug UI's 1.5s poll, `record()` rebuilds persistence
resources per trace, and `resource_snapshot()` recomputes
component-invariant values once per pod. All fixes are
behavior-preserving; the existing MCP/server tests are the oracle.

## Proposal

- **A-039 (MCP window scans, server_mcp.py:252-262/392-400/496-504):**
  timestamps are fixed-format `YYYY-MM-DD HH:MM:SS`, so lexicographic
  string order == chronological order. Precompute the window-boundary
  strings once per call; compare `row_timestamp_str` against them
  *before* any `strptime` (parse only in-window rows); hoist the metric
  column index above the row loop. **Early `break` past `to` only on the
  dimensionless layout** — dim-aware long-form CSVs are per-instance
  blocks (each internally sorted, not globally), so breaking there skips
  later instances' in-window rows; gate the break on the layout the
  header scan already reports. Correctness note for the PR: skipped
  rows were previously parsed-then-discarded, so output is identical by
  construction.
- **A-040 (unsupported_summary):** SQLite backend gets a SQL
  `GROUP BY fingerprint` aggregation (count/first/last + bounded example
  rows) instead of fetchall+from_dict; the `summary()` caller that only
  needs a count gets `COUNT(*)`/`COUNT(DISTINCT fingerprint)`. Memory
  backend keeps its list comprehension (already O(ring), bounded).
  Chose aggregation over a version-keyed cache: no invalidation
  machinery, and the DB does the work where the data lives.
- **A-041 (record hot path):** long-lived SQLite connection owned by the
  store, guarded by the store's existing lock (the server is
  multi-threaded — either `check_same_thread=False` under that lock, or
  a per-store writer thread; pick the lock approach, it matches current
  serialization). Persistent JSONL append handle; move the JSONL write
  outside the main trace-ring lock (serialize JSONL with its own small
  lock). Run the retention query every N inserts (N=64) instead of every
  insert.
- **A-042 (snapshot hoist):** compute per-component invariants
  (`_component_events`, `_exposed_component_scenarios`, per-component
  profile lookups) once above the replica loop in `resource_snapshot()`
  (server_ops.py:2022-2056).

## Boundaries And Non-Goals

- No output-shape changes anywhere (MCP tool responses, /v1/state JSON,
  trace records byte-identical modulo timing fields).
- No new caching layers with invalidation state; no schema changes to the
  SQLite store (the GROUP BY runs on existing columns/indexes — add an
  index on `fingerprint` only if EXPLAIN shows a table scan, and then as
  a versioned migration per the store's schema-version machinery).

## Affected Files

`src/anomaly_metric_creator/server_mcp.py`,
`src/anomaly_metric_creator/server_traces.py`,
`src/anomaly_metric_creator/server_ops.py`, timing evidence script
(scratch), tests (existing suites + a layout-gated break test),
`.trellis/audit/ledger.md` flips (A-039/040/041/042).

## Risks And Edge Cases

- The dim-aware no-break gate is THE correctness trap — add a dedicated
  test: N=3 run, window covering late rows, assert per-instance results
  identical pre/post (construct via `--instances-per-component 3` tiny
  run, not the GB fixture).
- SQLite thread discipline: the long-lived connection must never be used
  outside the store lock; assert with a debug-only owner check if cheap.
- Retention every-N: a crash loses at most N-1 rows of retention
  enforcement, not data — state this in the docstring.
- JSONL handle: reopen on rotation/deletion (stat check per write is
  still cheaper than reopen; keep it simple — document that external
  rotation requires restart, matching current behavior).

## Validation

- Before/after timing table in the PR (the audit's measurement method:
  narrow-window `get_metric_histogram` + correlated timeline on a 1-day
  default run; target: ms-scale for narrow windows).
- `pytest tests/test_server_mcp.py tests/test_server.py
  tests/test_trace_bundle.py -n 0` + full suite.
- /v1/state flatness: benchmark summary() at 0 / 5k synthetic traces
  (script in PR description).
