# MCP analysis tools: group-by, metric fields, correlated timeline, log dedup

## Context

- **Parent:** `07-02-mcp-server-facade` (shared design decisions binding).
- **Depends on:** `07-02-mcp-facade-core` (JSON-RPC layer, tool registry,
  histogram plumbing).
- **Reference:** mock-mcp-service `src/views.rs` — every analysis view
  evaluates the same underlying model so tools can never contradict each
  other. AMC gets this property for free by reading the same generated
  artifacts, but the tool designs below must not introduce a second
  computation path.

## Goal

Give an investigating agent the mid-level analysis tools it needs between
"raw histogram" and "kubectl": field discovery, distribution/aggregation
queries, a cross-component correlated timeline, and log deduplication over
`metric_report.log`.

## Requirements

### Field discovery and grouping

- `list_metric_fields` — enumerates queryable fields: always `component`,
  `metric`; plus the instance dimensions (`id`, `host`, `pod`, `az`,
  `region`, `tenant`) exactly when the run's `schema.json` declares a
  `dimensions` block for at least one component (reuse
  `_INSTANCE_DIMENSION_COLUMNS` — no hand-listed copy).
- `group_metrics_by_field(field, from_ms, to_ms, metric?, agg?)` — buckets
  by the field's values; default aggregation is count, with `avg`, `sum`,
  `min`, `max`, `p95`, `p99` over a named metric's values. Sorted by count
  descending, capped at a documented top-N with the truncation stated in
  the response (the CLAUDE.md "no silent caps" rule).
- Dimensioned runs (`--instances-per-component N > 1` /
  `--instance-config`) group over the long-form per-instance rows;
  dimensionless runs treat the wide CSV as a single anonymous instance.
  Both paths tested — this is a two-code-paths-for-the-same-data surface,
  the checklist's known drift risk.

### Correlated timeline

- `get_correlated_timeline(from_ms, to_ms, components?, sensitivity?)` —
  per-component timelines of notable metric excursions (cells beyond a
  z-score threshold against that column's window statistics), plus one
  interleaved cross-component timeline ordered by timestamp, so upstream →
  downstream causality from topology coupling is visible in one response.
- Excursion detection is computed from the CSVs at call time; it must NOT
  read `anomalies.csv` — the tool surfaces what the data shows, never what
  the manifest says was planted. (This is the ground-truth wall applied to
  the analysis layer; a leak here silently invalidates every eval.)
- Response is bounded: per-component and total event budgets, oldest-first
  keep policy, truncation flagged in the response.

### Log tools

- `get_logs(from_ms, to_ms, query?, limit?)` — filtered slice of
  `metric_report.log` with a simple query grammar: bare substring terms plus
  `component:X` / `level:Y` filters (subset of the mock's Mezmo-query
  handling; document exactly what is supported in the tool description).
- `deduplicate_logs(from_ms, to_ms, query?)` — clusters identical log lines
  modulo their variable parts (timestamps, values), returning one
  representative per cluster with a count, sorted by count descending —
  the shape an RCA agent expects from the mock's dedup tool.
- Both read the log artifact as written; when `logs` was not in the run's
  `--emit` selection, the tools return an empty result with an explanatory
  note rather than erroring.

## Acceptance Criteria

- [x] All four tools registered, listed, and covered in
      `tests/test_server_mcp.py` end to end with explicit `--seed` runs.
- [x] `group_metrics_by_field("component", ...)` totals equal per-CSV row
      counts for the same window (consistency-with-artifacts test).
- [x] A correlated-timeline test on a default run shows a planted primary
      anomaly's excursion **without** the response containing the anomaly's
      description string, scenario slug, or any `anomalies.csv` content
      (grep-negative assertion on the serialized response).
- [x] Dimensioned (N=3) and dimensionless runs both tested for
      `list_metric_fields` and `group_metrics_by_field`; expected field sets
      asserted non-empty before membership checks (vacuous-test guard).
- [x] Dedup on the default run collapses repeated report lines into
      clusters with correct counts (deterministic golden assertion).
- [x] Per-call cost stays bounded: CSVs streamed, not slurped; no
      per-row `strptime` in the hot loop.

## Notes

- Percentile aggregations should use one documented method (e.g. nearest
  rank) so responses are deterministic across Python versions.
- Keep the query grammar deliberately small; every accepted token must be
  in the tool description so agents don't guess unsupported syntax into the
  unsupported-call backlog.
