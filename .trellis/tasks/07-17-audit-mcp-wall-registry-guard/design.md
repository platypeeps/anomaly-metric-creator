# Registry-couple the MCP wall leak sweeps — Design (SD Work Designs, 2026-07-17)

## Overview

Verified state: `MCP_TOOLS` (server_mcp.py:793) registers 15 tools; every
handler is `_tool_<name>(state, arguments)` and receives the **full**
`SimulationState` (rubric-bearing fields included). The eval leak sweep
`test_eval_mode_tool_surface_has_no_ground_truth_leak`
(tests/test_server_eval_mode.py:188) hand-enumerates 8 tools + `tools/list`
while its docstring claims "every tool response"; the ops-surface sweep
(:236) covers the profile-text renderers separately. A new tool ships with
zero leak coverage unless a reviewer remembers.

## Proposal

### A-021 — registry-driven sweep

- Add a module-level table in `tests/test_server_eval_mode.py`:
  `_TOOL_MINIMAL_ARGS: dict[str, dict]` mapping **every** tool name to
  schema-valid minimal arguments (`get_metric_histogram` →
  `{"component": ..., "metric": ..., **window}`; `kubectl_get` →
  `{"resource": "pods"}`; `get_pod_logs` → a pod name from the snapshot;
  window-taking tools reuse the existing `from_ms`/`to_ms` day window; etc.).
- Coupling assertion first, so it fails loudly and names the gap:
  `assert set(_TOOL_MINIMAL_ARGS) == {t.name for t in server_mcp.MCP_TOOLS}`.
- Rewrite the sweep body to iterate the table: `tools/call` each tool in
  **eval mode**, serialize the full JSON-RPC response (including error
  shapes — a refusal note is a response too), and run the existing two
  leak assertions (no scenario slug; no `anomalies.csv` description
  substring) over the joined blob. Keep the non-empty guards.
- Add the **non-eval positive control** in the same test module: the same
  table-driven calls with `eval_mode=False` must leak at least one active
  slug somewhere (e.g. `kubectl_get` of the config map) — pins the sweep
  against vacuous passes if serialization changes.
- Fix the docstring to describe the registry-driven mechanism.
- Every `tools/call` must succeed or be an *expected* eval refusal
  (`get_logs`/`deduplicate_logs` → `_EVAL_MODE_LOG_NOTE`); assert no
  unexpected `isError` results so a schema drift in the minimal-args table
  fails as itself, not as a silent empty blob.

### A-001 — structural guard on handler source

Recommended shape: a **source-scan guard test** mirroring the
`test_every_dispatched_route_is_classified` precedent (same file, :~150):
for each `MCP_TOOLS` entry, `inspect.getsource(tool.handler)` and assert it
contains none of a forbidden-access list — `state.anomaly_rows`,
`state.active_scenarios`, `SCENARIOS`, `state.scenarios`,
`"anomalies.csv"`, `metric_report.log`-reading outside the two gated log
tools. Keep the list beside the test with a comment pointing at the
ground-truth-wall section of CLAUDE.md. A narrowed investigation-view
`state` object would be stronger but refactors all 15 handlers and the ops
dispatch — explicitly deferred (non-goal) unless the scan proves too leaky.
The two log tools legitimately read `metric_report.log` behind the
`eval_mode` refusal — encode that as a per-tool allowlist entry
(`{"get_logs", "deduplicate_logs"}: metric_report.log allowed`), never a
blanket exemption.

## Boundaries And Non-Goals

- No new MCP tools, no handler behavior changes, no `SimulationState`
  refactor (the narrowed-view idea is recorded as future work only).
- The ops-surface sweep (:236) stays as-is; this task only fixes the tool
  sweep and adds the structural guard.

## Affected Files

- `tests/test_server_eval_mode.py` (sweep rewrite + minimal-args table +
  positive control + source-scan guard),
- possibly `src/anomaly_metric_creator/server_mcp.py` (docstring/comment
  pointing new-tool authors at the table — no behavior change),
- `.trellis/audit/ledger.md` (flip A-021, A-001 → fixed),
- CLAUDE.md MCP section: one sentence noting the sweep is registry-coupled
  and a new tool must add a `_TOOL_MINIMAL_ARGS` entry.

## Risks And Edge Cases

- Minimal-args values must be deterministic under the fixture seed (pod
  names come from `resource_snapshot()`; derive them from the snapshot at
  test time rather than hardcoding).
- Slug-substring false positives: scenario slugs are distinctive
  (`db_stall`, …) but the assertion runs over JSON that includes tool
  *input* echoes — keep arguments free of slug strings.
- `inspect.getsource` on handlers is stable (plain module functions); if a
  handler is ever wrapped, the guard must unwrap (`__wrapped__`) — note in
  the test.

## Validation

- `pytest tests/test_server_eval_mode.py tests/test_server_mcp.py -n 0`
  then the full suite.
- Mutation check while developing: comment one tool out of the table →
  coupling assertion must name it; add a fake `state.active_scenarios`
  read to a handler → guard must fail.
