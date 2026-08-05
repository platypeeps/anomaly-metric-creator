# Extract server_helm_impl.py (step 3)

> **UNBLOCKED 2026-08-04** — the planning closure audit's blocker (7 non-helm
> primitives + 2 constants defined in `server_ops.py`) is fully resolved.
> Option A (resequence) shipped: `_table`/`_is_dry_run`/`_unsupported`/
> `_exposed_active_scenarios`/`CommandResult` are in `server_command_render.py`
> (PR #331), `_format_dt` in `server_mutations.py`, `_k8s_metadata`/
> `_k8s_timestamp` in `server_k8s_objects.py`, and `DEFAULT_RELEASE`/
> `DEFAULT_CHART` in `server_ops_support.py` (PR #327). Helm is now the top
> leaf and extracts clean one-way — see the refreshed move-set + closure in
> `design.md`.

## Parent

Step 3 of epic `07-06-server-ops-decomposition`. Follows step 1
(`08-04-server-ops-profiles-extract`, PR #321, merged) and step 2
(`08-04-server-ops-parse-extract`, PR #323, merged). The epic's
`design.md` fixes the boundaries and the per-step process; this task
executes one extraction PR against them.

## Goal

Extract the Helm cluster out of the 5,540-line
`src/anomaly_metric_creator/server_ops.py` into a new leaf
`src/anomaly_metric_creator/server_helm_impl.py`, changing **zero**
HTTP/command/MCP/Kubernetes-API behavior. `server_ops.py` re-imports
every moved name at the same conceptual position so the compatibility
surface (`server.py`'s alias block, the `server_helm.py` /
`server_commands.py` facades, `server_mcp.py`, the Helm Secret REST
objects) needs no edits.

## Scope (moved cluster)

Per the epic design's step-3 boundary (helm renderers + Secret
encoding), plus the closure-forced additions the one-way-import rule
requires (a moved function may not call a symbol that stays in
`server_ops`). The exact final set is pinned by the closure audit in
`design.md`.

- Helm command renderers: `_render_helm`, `_render_helm_list`,
  `_render_helm_status`, `_render_helm_history`, `_render_helm_env`,
  `_render_helm_get`, `_render_helm_test`, `_render_helm_install`,
  `_render_helm_upgrade`, `_render_helm_rollback`, and the helm-only
  helpers `_helm_value_overrides`, `_helm_operation_note`.
- Helm release/notes model: `_helm_release`,
  `_helm_release_revisions`, `_helm_notes`,
  `_helm_current_description`.
- Helm Secret encoding: `_helm_secret_objects`, `_helm_secret_object`,
  `_helm_encoded_release_data`, `_helm_release_payload`.

Any staying dispatcher (a general command router that merely *calls*
`_render_helm`) stays in `server_ops.py` and re-imports the moved name.

## Non-goals

- No renderer for non-helm kinds, no snapshot/state, no Kubernetes-API
  object/table code moves (later epic steps 4–6).
- No behavior change, no output-byte change, no new dependency.
- No edits to the facades, `server.py`'s alias block, or
  `server_mcp.py` imports.

## Acceptance Criteria

- [ ] New `server_helm_impl.py` never imports `server_ops` at runtime
      (one-way leaf); it imports only stdlib plus already-extracted lower
      leaves (`server_command_render`, `server_k8s_objects`,
      `server_mutations`, `server_ops_parse`, `server_ops_support`). The sole
      `from .server_ops import SimulationState` sits under `if TYPE_CHECKING:`
      with `from __future__ import annotations` (stringized, never evaluated).
- [ ] `server_ops.py` re-imports every moved name at its original
      conceptual position; no caller (facades, `server.py` alias block,
      `server_mcp.py`, Helm Secret REST objects) is edited.
- [ ] The moved module is < 800 lines (the epic's per-module cap).
- [ ] Helm render-oracle output is byte-identical before and after the
      move over the fixed 33-command corpus (includes `helm status`,
      `helm history`, `helm get values/notes`, `helm list`, install /
      upgrade / rollback).
- [ ] Helm Secret payload bytes (`_helm_encoded_release_data` double-
      base64 gzip) are byte-identical before and after the move.
- [ ] Server-family tests green:
      `tests/test_server.py tests/test_server_ops_fuzz.py
      tests/test_server_mcp.py tests/test_server_eval_mode.py`, then the
      full suite.
- [ ] Splice-hazard grep of the deleted ranges finds no orphaned
      `from .` re-import stub swept into the cut.
- [ ] CLAUDE.md server-module map and
      `.trellis/spec/amc/backend/architecture.md` updated to list
      `server_helm_impl.py`; the epic's step tracker records step 3 done
      and the measured `server_ops.py` size.
