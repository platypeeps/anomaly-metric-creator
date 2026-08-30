---
title: Prune MCP tool scans and trace-store hot paths
status: done
created: 2026-07-17
branch: sdelmas/mcp-query-performance
---
# Prune MCP tool scans and trace-store hot paths

## Audit context

- **Source:** 2026-07-17 full repo audit @ b0df00b — report:
  `.trellis/audit/report-2026-07-17.md`; per-item evidence + fix sketches:
  `.trellis/audit/ledger.md`.
- **Ledger items:** A-039 (P2·M), A-040 (P2·M), A-041 (P3·S), A-042 (P3·S)

## Goal

The agent-facing MCP analysis tools full-parse every CSV row per call (measured
0.137s/component regardless of window; ~2s per correlated-timeline call), and the
debug UI's 1.5s poll pays two O(full-history) SQLite deserializations per tick.

## Scope (ledger items)

- A-039 — precompute lexicographic window-boundary strings and compare row[0] before any strptime; break past `to` on sorted dimensionless layout; hoist metric-column index out of the row loop.
- A-040 — SQL GROUP BY fingerprint aggregation for unsupported_summary (count/min/max + bounded examples) or cache keyed on the store version counter; give summary() a COUNT(DISTINCT ...) instead of the full grouping.
- A-041 — long-lived SQLite connection + persistent JSONL append handle; JSONL write outside the main lock; retention every N inserts.
- A-042 — hoist per-component _component_events/_exposed_component_scenarios above the replica loop in resource_snapshot().

## Acceptance criteria

- [x] Narrow-window get_metric_histogram is ~milliseconds; behavior unchanged (existing MCP tests green).
- [x] /v1/state cost is flat as trace history grows (benchmark or complexity argument in PR).
- [x] Closing PR flips each covered ledger item to `status: fixed` in
      `.trellis/audit/ledger.md` (same-PR, per ledger rules).
