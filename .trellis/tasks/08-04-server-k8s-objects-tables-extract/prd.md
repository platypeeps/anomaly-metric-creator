# Extract server_k8s_objects.py + server_k8s_tables.py (step 4)

## Parent

Step 4 of epic `07-06-server-ops-decomposition`, **resequenced ahead of
step 3 (helm)** by maintainer decision 2026-08-04: helm's closure audit
(`08-04-server-helm-impl-extract`, parked) proved helm depends on k8s
object primitives (`_k8s_metadata`, `_k8s_timestamp`) and render
primitives that the epic scheduled *after* helm. Extracting the k8s
object/table leaves first moves those primitives down and unblocks the
eventual helm leaf. Follows step 1 (PR #321) and step 2 (PR #323), merged.

## Goal

Extract the Kubernetes REST object builders and the `meta.k8s.io/v1`
Table renderers out of the ~6,589-line
`src/anomaly_metric_creator/server_ops.py` into two new leaf modules —
`server_k8s_objects.py` (REST object dicts) and `server_k8s_tables.py`
(Table rendering) — cut along the object-vs-table seam, in **one PR**.
Zero HTTP/command/MCP/Kubernetes-API behavior change. `server_ops.py`
re-imports every moved name at its original conceptual position so the
compatibility surface (`server.py`'s alias block, the
`server_kubernetes.py` facade, `server_mcp.py`, the real-client REST
paths) needs no edits.

## Scope (moved cluster)

Pinned by the closure audit in `design.md`. Two-file split because the
combined size (~950) exceeds the epic's 800-line per-module cap:

- `server_k8s_objects.py` — REST object builders (`_k8s_metadata`,
  `_k8s_timestamp`, per-kind object dict builders, owner/label helpers),
  plus the object-side snapshot-kind constants the builders own.
- `server_k8s_tables.py` — `meta.k8s.io/v1` Table response rendering for
  `kubectl get` (may import `server_k8s_objects` one-way).

`resource_snapshot()`, `SimulationState`, and the REST API facade
(`_k8s_api_resource_list`, `_k8s_objects_for_resource`, discovery/OpenAPI
— step 5) stay in `server_ops.py`.

## Non-goals

- No REST API facade / discovery / kubeconfig moves (step 5).
- No render-dispatch (`_render_get`/`_render_describe`) moves (step 6).
- No behavior change, no output-byte change, no new dependency.
- No edits to the facades, `server.py`'s alias block, or `server_mcp.py`.

## Acceptance Criteria

- [ ] **Clean one-way leaves.** Neither new module imports `server_ops`;
      they import only stdlib, already-extracted lower leaves, and (for
      tables) `server_k8s_objects`. If the audit finds a moved builder
      calls a staying `server_ops` helper at runtime, that helper either
      moves too or the seam is redrawn — no reverse import ships. (This is
      the gate helm failed.)
- [ ] `server_ops.py` re-imports every moved name at its original
      position; no caller (facades, `server.py` alias block,
      `server_mcp.py`, REST paths) is edited.
- [ ] Each new module is < 800 lines (epic per-module cap).
- [ ] Kubernetes object + Table render-oracle output is byte-identical
      before and after the move (fixed corpus: `kubectl get`
      pods/deployments/configmaps/secrets/events/services with and without
      Table `Accept`, plus `-o yaml/json`).
- [ ] Server-family tests green:
      `tests/test_server.py tests/test_server_ops_fuzz.py
      tests/test_server_mcp.py tests/test_server_eval_mode.py
      tests/test_server_watch.py`, then the full suite.
- [ ] Splice-hazard grep of the deleted ranges finds no orphaned `from .`
      re-import stub swept into the cut.
- [ ] CLAUDE.md server-module map and
      `.trellis/spec/amc/backend/architecture.md` updated to list both new
      modules; the epic step tracker records step 4 done, the measured
      `server_ops.py` size, and that `_k8s_metadata`/`_k8s_timestamp` now
      live in `server_k8s_objects.py` (helm's k8s deps resolved).
