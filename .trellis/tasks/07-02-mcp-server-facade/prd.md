# Expose an MCP tool surface from `amc serve` for AI-agent evals

## Context

- **Source:** review of `mezmo/ai-experiments/mock-mcp-service` (2026-07-02) —
  a Rust mock of the real Mezmo MCP server that serves ~20 MCP tools over
  streamable HTTP, with every response derived from a declarative failure
  scenario (world + overlay YAML) so an eval harness can score an RCA agent
  against planted ground truth.
- **Observation:** AMC already has everything the mock had to build except
  the MCP endpoint itself — a scenario catalog with cascades and topology
  coupling (`SCENARIOS`, `TOPOLOGY`), machine-readable ground truth
  (`anomalies.csv` + scenario descriptions), an anchored simulated clock
  (`/v1/time/pause·resume·seek`), a *live* simulated Kubernetes/Helm API
  (where the mock only ships static fixtures for its non-log tools), and
  fail-loud import-time validation.
- **Type:** feature umbrella. Work is split across four subtasks; this task
  carries the shared design decisions and the definition of done.

## Goal

An RCA agent can be pointed at `amc serve`'s MCP endpoint, investigate an
incident via metric histograms, correlated timelines, logs, and
`kubectl`/Helm state — all mutually consistent because they derive from one
generated dataset — and be scored by an eval harness that holds
`anomalies.csv`. The mock-mcp-service eval loop, over a richer simulation.

## Shared design decisions (binding on all subtasks)

1. **Transport:** stateless streamable-HTTP MCP — JSON-RPC 2.0 over
   `POST /mcp` on the existing stdlib HTTP server (`initialize`,
   `tools/list`, `tools/call`; plain JSON responses, no server-initiated SSE
   stream; legacy SSE GETs get 405, matching the mock). Stdlib-only
   implementation preferred; if the `mcp` Python SDK is used it must be an
   optional extra following the PyYAML `[yaml]` pattern.
2. **Module layout:** a new `server_mcp.py` owns the JSON-RPC layer and tool
   registry; `server.py` stays the HTTP/serve facade and only dispatches,
   the same way it does for `server_ops.py` / `server_traces.py`.
3. **Single source of truth:** every tool answers from artifacts the run
   already produces (per-component CSVs, `schema.json`, `metric_report.log`,
   `resource_snapshot()`, the simulated clock). No tool computes state a
   second way — the mock's "one model, many views" rule.
4. **Ground-truth wall:** the MCP tool list never exposes `anomalies.csv`,
   `/v1/anomalies`, or `/v1/scenarios` content. Those are the eval rubric
   and stay on the harness side (see `07-02-mcp-eval-mode-hardening`).
5. **Traceability:** every `tools/call` is recorded as a `CommandTrace`
   under a new `mcp` command family (see `07-02-mcp-ops-tools-and-tracing`),
   so unsupported calls land in the existing debug backlog.
6. **Session model (v1):** one `amc serve` run = one generated dataset = one
   pinned timeline. The mock's `X-Mock-Scenario`/`X-Mock-Session` per-call
   selection is explicitly out of scope for v1 (AMC generation is real array
   work, not closed-form rate integration); revisit as a follow-up only if
   eval throughput demands it.

## Subtasks (sequenced)

1. `07-02-mcp-facade-core` — handshake, `tools/list`, first read-only tools.
2. `07-02-mcp-analysis-tools` — group-by, fields, timeline, log dedup.
3. `07-02-mcp-ops-tools-and-tracing` — kubectl/Helm tools + trace family.
4. `07-02-mcp-eval-mode-hardening` — ground-truth wall, auth, docs.

## Acceptance Criteria (umbrella-level definition of done)

- [ ] `amc serve` answers an MCP `initialize` → `tools/list` → `tools/call`
      round-trip from a stock MCP client over streamable HTTP.
- [ ] All tool responses are consistent with the generated CSV artifacts and
      the simulated clock for the same run (spot-checked by test).
- [ ] No MCP tool or response leaks scenario slugs, anomaly manifest rows,
      or ground-truth descriptions.
- [ ] The existing HTTP surface (`/v1/*`, kubeconfig, debug UI) is unchanged;
      all existing tests pass.
- [ ] README gains an "evaluating agents against AMC" section describing the
      agent-on-`/mcp`, harness-holds-`anomalies.csv` loop.

## Non-goals

- Impersonating the real Mezmo MCP service's exact tool names/schemas. The
  mock must be indistinguishable from the real service; AMC's surface is its
  own simulation and should be named honestly. (If Mezmo-schema parity is
  ever wanted, that is a separate task with the forked-types maintenance
  cost the mock's README already warns about.)
- Exporting AMC scenarios to mock-mcp-service world/scenario YAML (loses
  metric fidelity; the mock is log-first).
- Pointing the Rust mock at AMC CSVs as a backend (cross-repo,
  cross-language coupling).
- Multi-scenario-per-server session selection (v1 explicitly single-run).
