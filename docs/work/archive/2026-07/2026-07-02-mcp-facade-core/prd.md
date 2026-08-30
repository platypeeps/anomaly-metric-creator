---
title: "MCP facade core: JSON-RPC handshake, tools/list, read-only telemetry tools"
status: done
created: 2026-07-02
branch: feat/mcp-facade-core
---
# MCP facade core: JSON-RPC handshake, tools/list, read-only telemetry tools

## Context

- **Parent:** `07-02-mcp-server-facade` — read its "Shared design decisions"
  first; they are binding here (stdlib-first transport, `server_mcp.py`
  module boundary, single-source-of-truth views, ground-truth wall,
  single-run session model).
- **Reference implementation:** `mezmo/ai-experiments/mock-mcp-service`
  (`src/handler.rs` for the tool surface shape, `src/main.rs` for the
  stateless streamable-HTTP setup). Mirror the *design*, not the schemas.

## Goal

`amc serve` exposes a working MCP endpoint at `POST /mcp`: a stock MCP
client can `initialize`, `tools/list`, and `tools/call` a first wave of
read-only tools that answer from the run's generated dataset and simulated
clock.

## Requirements

### Transport / protocol

- New module `src/anomaly_metric_creator/server_mcp.py` owning: JSON-RPC 2.0
  parse/serialize, MCP method dispatch (`initialize`,
  `notifications/initialized`, `tools/list`, `tools/call`, `ping`), and the
  tool registry. `server.py` adds only the `POST /mcp` route dispatch.
- Stateless mode: every POST is self-contained JSON in / JSON out. `GET
  /mcp` (legacy SSE transport probe) returns 405 with a JSON-RPC error body,
  matching the mock's behavior.
- Protocol-version negotiation per the MCP spec: echo the client's requested
  version when supported, otherwise respond with the server's latest and let
  the client decide (the mock's `initialize` mirrors this; do the same).
- Malformed JSON, unknown methods, unknown tool names, and invalid tool
  arguments each return the correct JSON-RPC error code — never a raw
  traceback or an HTTP 500 with a stringified exception (see the
  `07-02-audit-server-ops-rendering` finding on `{"error": str(exc)}`).
- Request bodies honor the existing `--max-request-body-bytes` cap; the
  over-limit response is a JSON-RPC error, not the app-endpoint 413 shape.

### Tool registry

- Tools are declared with name, description, and a JSON-Schema `inputSchema`
  in one registry structure; `tools/list` derives from it (no hand-written
  parallel list) and returns tools sorted by name for a stable order.
- Registry is validated at import time in the house style: unique names,
  schema is a dict, every tool has a callable handler — fail loudly, not at
  first call.

### First tool wave (all read-only)

- `get_current_time` — returns now / one_hour_ago / one_day_ago in RFC 3339
  from the **simulated** clock (honors `/v1/time/pause·resume·seek`), plus
  epoch milliseconds.
- `list_components` — active components for the run with their emitted
  metric names, units, and semantic types, read from the run's
  `schema.json` (or the equivalent in-memory `SimulationState` view — pick
  one source and document it).
- `get_topology` — the directed coupling graph restricted to active
  components, same shape as `schema.json`'s `topology` section.
- `get_metric_histogram(component, metric, from_ms, to_ms, granularity_ms?)`
  — bucketed aggregation (count, mean, min, max per bucket) over the
  per-component CSV, auto-selecting granularity from a ladder toward a
  target bucket count when the caller passes none (mirror the mock's
  `GRANULARITY_LADDER_MS` / `TARGET_MAX_BUCKETS` approach), with a hard
  bucket ceiling for caller-supplied granularities. Out-of-range windows
  return empty buckets, not errors.
- Time-range arguments are validated (finite, `from < to`); reject with a
  clear JSON-RPC invalid-params error otherwise.

### Ground-truth wall (applies from day one)

- None of these tools reads `anomalies.csv` or the `SCENARIOS` registry.
  `list_components` and `get_topology` expose structure, not which scenario
  is active.

## Acceptance Criteria

- [x] `tests/test_server_mcp.py` drives `initialize` → `tools/list` →
      `tools/call` for every v1 tool through the live HTTP server
      (subprocess or in-process, following `tests/test_server.py` patterns).
- [x] `tools/list` output is byte-stable across two server starts on the
      same dataset (sorted, no dict-iteration-order dependence).
- [x] `get_metric_histogram` bucket sums for a full-day window equal the CSV
      row count for that component/metric (drop-rate rows excluded by
      construction), asserted in a test with an explicit `--seed`.
- [x] `get_current_time` reflects a `/v1/time/seek` — asserted by test.
- [x] `GET /mcp` returns 405; malformed JSON returns JSON-RPC parse-error;
      unknown tool returns method-not-found-shaped tool error — all tested.
- [x] No new required runtime dependency (stdlib-only, or SDK behind an
      optional extra with a clear ImportError message).
- [x] Existing test suite passes unchanged.

## Notes

- Keep per-call CSV work bounded: the histogram should stream the CSV once
  per call, not load it whole; hoist parsing out of per-row loops per the
  CLAUDE.md hot-path rules.
- Bearer-auth interaction (`/mcp` must require `Authorization` when
  `--auth-token` is set) lands here mechanically via the existing gate, but
  its policy/docs live in `07-02-mcp-eval-mode-hardening`.
