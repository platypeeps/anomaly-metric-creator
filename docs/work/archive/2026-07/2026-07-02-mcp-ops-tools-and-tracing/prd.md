---
title: MCP ops tools and command-trace integration
status: done
created: 2026-07-02
branch: feat/mcp-ops-tools
---
# MCP ops tools and command-trace integration

## Context

- **Parent:** `07-02-mcp-server-facade` (shared design decisions binding).
- **Depends on:** `07-02-mcp-facade-core` (tool registry / dispatch).
- **Why this matters:** this is AMC's differentiator over mock-mcp-service.
  The mock answers its pipeline/trace tools from static fixtures purely so
  the tool list looks complete; AMC has a live simulated Kubernetes/Helm
  surface (`server_ops.py`, `resource_snapshot()`, `SimulationMutations`)
  whose state is consistent with the generated metrics. Exposing it over
  MCP lets an agent correlate telemetry with cluster state — something the
  mock structurally cannot offer.

## Goal

An MCP agent can inspect the simulated cluster (workloads, events, pod
logs, Helm releases) through first-class tools, and every MCP call —
supported or not — is visible in the existing command-trace debug loop.

## Requirements

### Ops tools (wrappers, not a second model)

- Tools dispatch through the existing `render_command()` /
  `parse_command()` path in `server_ops.py` (or the focused
  `server_commands.py` entrypoints) — never a parallel renderer. The
  overlay (`SimulationMutations`) and scenario profiles
  (`OPS_SCENARIO_PROFILES`) therefore apply automatically.
- v1 tool set:
  - `kubectl_get(kind, name?, namespace?, selector?)` — structured rows
    (the data behind the table view), not pre-rendered text.
  - `describe_resource(kind, name, namespace?)` — the describe rendering.
  - `get_pod_logs(pod, namespace?, container?, tail_lines?)`.
  - `get_events(namespace?, involved_object?)`.
  - `helm_status(release)` / `helm_history(release)`.
- Argument validation mirrors what the command parser would reject; an
  unknown kind/name returns the same not-found semantics `kubectl` would
  see from the fake API, wrapped as a tool-level error result (not a
  JSON-RPC protocol error).
- Scenario-profile descriptions surfaced through these tools are the
  *operator-visible* strings (pod events, rollout messages). They must not
  append eval ground truth (root-cause explanations, scenario slugs) beyond
  what the kubectl/Helm surface already shows a human operator — the
  ground-truth wall for ops output is "no more than kubectl shows".

### Command-trace integration (all MCP tools, not just ops)

- Every `tools/call` — including the core and analysis tools from the
  sibling tasks — records a `CommandTrace` with a new command family
  `mcp`, carrying the tool name, redacted arguments, support status, and
  outcome, through the same ring buffer / JSONL / SQLite persistence and
  `/v1/debug/search` filters as `kubernetes-api` traces.
- Unknown tool names and schema-invalid calls are traced as unsupported
  with a normalized fingerprint, so agent misfires accumulate in
  `/v1/debug/unsupported` and the debug UI as a backlog — the same loop
  real kubectl misfires use today.
- Trace redaction applies to MCP arguments (bearer tokens, token-like
  values) via the existing redaction helpers before anything reaches
  memory, JSONL, SQLite, or the debug UI.
- The debug UI's command family filter includes `mcp` without further UI
  work (verify the family list is data-driven; if it is hand-listed,
  update it in lockstep).

## Acceptance Criteria

- [x] Each v1 ops tool covered in `tests/test_server_mcp.py`, asserting
      output consistency with the equivalent `POST /v1/commands` kubectl
      invocation on the same server (single-source-of-truth check).
- [x] A mutation applied via the Kubernetes API (e.g. scale) is visible in
      the next `kubectl_get` MCP response (overlay flows through).
- [x] An MCP `tools/call` produces a `CommandTrace` with family `mcp`
      retrievable via `/v1/debug/commands` and `/v1/debug/search`
      (`command_family=mcp`), tested end to end.
- [x] An unknown MCP tool call appears in `/v1/debug/unsupported` grouped
      by fingerprint, tested.
- [x] A tool argument containing a bearer-token-shaped string is redacted
      in the stored trace, tested.
- [x] `amc trace-bundle` summary/search over an export containing `mcp`
      traces works unchanged (the offline path shares
      `server_traces.trace_matches_search()`); one focused test.

## Notes

- Trace recording belongs in the `server_mcp.py` dispatch layer so no
  individual tool can forget it.
- Keep tool outputs structured JSON where possible; agents parse fields
  better than pre-rendered tables, and the fake API already has object
  representations behind the table renderer.
