---
title: Extract server_k8s_api.py from server_ops.py
status: done
created: 2026-08-05
branch: sdelmas/server-k8s-api-extract
---
# Extract server_k8s_api.py from server_ops.py (epic step 5)

## Review context

- **Parent epic:** `07-06-server-ops-decomposition` (decompose the
  7.7k-line `server_ops.py` into focused one-way leaf modules using the
  legacy.py role-swap pattern). This is **step 5** per the epic
  `implement.md` execution order.
- **Prior steps shipped:** `server_ops_profiles.py` (1),
  `server_ops_parse.py` (2), `server_helm_impl.py` (3),
  `server_k8s_objects.py` + `server_k8s_tables.py` + `server_ops_support.py`
  (4), `server_command_render.py` (3-precursor).
- **Confidence:** CONFIRMED. A read-only closure audit (2026-08-05)
  measured the cluster boundary and the reverse-import hazards.

## Goal

Extract the Kubernetes REST-facade **pure builder/filter/format layer** out
of `server_ops.py` (currently 5,440 lines) into a new one-way leaf module
`server_k8s_api.py`, reducing `server_ops.py` by ~950 lines with **zero**
HTTP/command/MCP behavior change and **zero** edits to the compatibility
surface (`server.py` alias block, the three facades, `server_mcp.py`).

## Problem

The k8s REST facade cluster (~1,680 contiguous tail lines, :3532–5210, plus
scattered constants/dataclass at :50–644) is the single largest un-extracted
cluster in `server_ops.py`. The audit split it into two tiers:

- a **dispatch spine** (~650–700 lines) transitively bound to
  `resource_snapshot` (:859, the snapshot-assembly heart), `_render_logs`,
  the explain-schema trio, and generic-mutation helpers — all in
  `server_ops`'s middle layer *above* the leaf tier, and
  `resource_snapshot` is monkeypatch-pinned in `server_ops`'s namespace by
  `tests/test_server.py:563`; and
- a **pure builder/filter/format/discovery layer** (~950 lines) with no
  `resource_snapshot` coupling.

Only the pure layer can move to a one-way leaf. Moving any
`resource_snapshot`-touching function would require the leaf to import
`resource_snapshot` back from `server_ops` (violates the one-way rule) and
break the `snapshot_calls == 1` assertion at `test_server.py:563`.

## Requirements

- New leaf `server_k8s_api.py`: stdlib + existing leaves + a
  `TYPE_CHECKING`-guarded `SimulationState` annotation import only. It must
  **not** import `server_ops` at runtime.
- Verbatim moves; `server_ops.py` re-imports every moved name at its
  original conceptual position (`from .server_k8s_api import (...)`), so
  every moved name remains a `server_ops` attribute. `server_ops.__all__`
  membership is left **exactly unchanged** — a moved name already in
  `__all__` stays (the re-import keeps the attribute); a moved name not in
  `__all__` today (e.g. `_query_int`, `_query_str`) is **not** added.
- **Prerequisite companion move:** `_preview` (:3532–3537) moves down into
  `server_ops_support.py` first — it has a staying caller (`run_command`,
  :687) and cluster callers (`_api_trace_body`, `record_kubernetes_api_call`).
  Both sides then import it one-way from support.
- `_openapi_paths`, the OpenAPI **document** builders, and every other
  `resource_snapshot`-bound function **stay** in `server_ops.py` (recorded
  as deferred sub-scope; moving them needs a second companion move of the
  snapshot-kind constants — out of scope for this PR).
- All server-family tests pass unchanged apart from design-sanctioned
  import-path retargets: `tests/test_server.py`,
  `tests/test_server_ops_fuzz.py`, `tests/test_server_mcp.py`,
  `tests/test_server_eval_mode.py`, `tests/test_server_watch.py`.
- Behavior-identity evidence: a before/after render-oracle byte-diff over a
  fixed k8s-API command/path corpus (server-layer analog of golden hashes),
  plus the fuzz + eval suites.
- `server_k8s_api.py` added to the mypy clean-module gate
  (`tools/check_mypy_gate.py`).
- CLAUDE.md server module map and
  `.trellis/spec/amc/backend/architecture.md` (if it carries the map)
  updated in this PR.

## Acceptance Criteria

- [x] `server_k8s_api.py` created; strictly one-way (no runtime
      `from .server_ops`); < 800 lines OR the epic data/size exemption
      recorded explicitly if it lands larger. (743 lines; the `_api_*`
      trace/redaction sink carved into `server_k8s_api_trace.py` for the cap.)
- [x] `_preview` relocated to `server_ops_support.py`; both callers import
      it one-way; no duplicate copy remains.
- [x] `server_ops.py` re-imports every moved name at its original position;
      `server_ops.__all__` membership is byte-unchanged (no moved name added
      to or removed from it). (Review-fix: 6 verified-dead re-exports with no
      consumer were dropped; none was in `__all__`, so membership is unchanged.)
- [x] `server.py` alias block, `server_kubernetes.py` /
      `server_commands.py` / `server_helm.py` facades, and `server_mcp.py`
      imports are **not** edited and still resolve (identity tests pass).
- [x] `tests/test_server.py:563` `resource_snapshot` monkeypatch still
      bites (all `resource_snapshot`-touching functions stayed in
      `server_ops`).
- [x] Full server test suite + fuzz + eval + watch green; render-oracle
      byte-identical over the k8s-API corpus.
- [x] `server_k8s_api.py` in the mypy clean gate (32 modules); CLAUDE.md +
      spec map updated; `server_ops.py` end size recorded in the PR (4,693).

## Non-Goals

- Moving the `resource_snapshot`-bound dispatch spine (kept; needs the
  snapshot-kind-constants companion move — separable follow-up).
- `_openapi_paths` relocation (kept — needs snapshot-kind constants).
- Steps 6–7 of the epic (render dispatch/workloads split; close-out).
- The four-parallel-per-kind-surface collapse (named epic follow-up).
