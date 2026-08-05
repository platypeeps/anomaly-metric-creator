# Decompose server_ops.py — Implementation Plan

Epic: one extraction PR per step, design.md fixes the boundaries. Child
tasks per step may be created later **with explicit user consent** (Trellis
rule); until then this file is the step tracker.

## Execution Order

0. Build the render-oracle scratch script once (fixed sample command list
   → captured stdout/stderr/exit triples to a file); commit it under
   `tools/` only if the maintainer wants it kept — otherwise scratch.
1. `server_ops_profiles.py` (data + validator; import-position preserved).
2. `server_ops_parse.py` (parser + flag tables + fingerprint/redaction).
3. `server_helm_impl.py` (helm renderers + Secret encoding).
4. `server_k8s_objects.py` + `server_k8s_tables.py` (one PR, two leaves).
5. `server_k8s_api.py` (discovery/OpenAPI/kubeconfig + REST helpers).
6. `server_ops_render.py` + `server_ops_render_workloads.py` (dispatch +
   get/describe; logs/rollout/mutation).
7. Close-out: record `server_ops.py` end size, update CLAUDE.md +
   architecture spec final map, follow-ups filed (per-kind descriptor
   collapse; `server.py` seam), archive epic.

Every step: monkeypatch grep → closure audit (AST/grep) → verbatim move →
re-import stub → splice-hazard grep → tests → render-oracle diff →
CLAUDE.md/spec map update → draft PR → checklist → ready → merge.

## Step Status

- [x] Step 1 — `server_ops_profiles.py` (child `08-04-server-ops-profiles-extract`,
  PR #321, merged). `server_ops.py` 7,095 lines after.
- [x] Step 2 — `server_ops_parse.py` (child `08-04-server-ops-parse-extract`).
  Leaf 566 lines (stdlib + `DEFAULT_NAMESPACE` only); `server_ops.py`
  7,095 → 6,589 lines. 26 symbols moved verbatim, zero residual free names,
  render-oracle byte-identical over the 33-command corpus.
- [x] Step 4 — `server_k8s_objects.py` + `server_k8s_tables.py`
  (child `08-04-server-k8s-objects-tables-extract`), **resequenced ahead of
  step 3** with explicit maintainer consent (helm step 3 parked until its k8s
  + render primitives are extracted). The seam audit surfaced a shared-accessor
  entanglement, so this became a **3-leaf** shape (maintainer Option A): a new
  pure lower leaf `server_ops_support.py` (77 lines: `DEFAULT_RELEASE` /
  `DEFAULT_CHART` + the five snapshot/timestamp accessors) feeds both k8s leaves
  downward. `server_k8s_objects.py` 594 lines (30 object builders + shared
  metadata/owner/label/container-state/pod-timestamp/pod-ip helpers;
  `_k8s_metadata` / `_k8s_timestamp` now live here for the future helm step);
  `server_k8s_tables.py` 470 lines (table/column/schema + 24 cell builders).
  `_k8s_endpointslice` stayed in `server_ops` (reads `resource_snapshot` via its
  own default — moving it would reverse-import). `server_ops.py`
  6,589 → 5,590 lines (includes removing a dead `import shlex` orphaned by the
  step-2 parse extraction). DAG:
  `server_mutations → server_ops_support → server_k8s_objects → server_k8s_tables`,
  `server_ops` re-imports all moved names. Behavior-identity proven by a
  frozen-clock render-oracle (byte-identical objects + Tables + kubectl
  get/describe corpus) and the server fuzz/eval suites. Support + objects added
  to the mypy clean gate (27 modules); tables carries one verbatim `var-annotate`
  gap (`_k8s_node_cells`'s `ready = next(...)`), follow-up to annotate + gate.
- [x] Step 3 precursor — `server_command_render.py` (child
  `08-04-server-ops-support-render-primitives`, PR #331). New pure leaf (90
  lines): `CommandResult` + `_table` / `_is_dry_run` / `_unsupported` /
  `_exposed_active_scenarios`, re-exporting `_format_dt` from `server_mutations`
  (duplicate `server_ops` copy deleted — single source). `server_ops`
  re-imports every moved name at the `CommandResult` block position; the leaf's
  only `from .server_ops` is a `TYPE_CHECKING`-guarded `SimulationState`
  annotation (`from __future__ import annotations` → stringized, never
  evaluated), so the one-way runtime rule holds. `server_ops.py`
  5,590 → **5,540** lines. Leaf added to the mypy clean gate (29 modules).
  Behavior-identity proven by a render-oracle byte-identical over a 14-command
  corpus in both normal and `--mcp-eval-mode` states (the eval-mode
  `_exposed_active_scenarios` redaction path exercised). This closes the last
  render-primitive coupling so the later `server_helm_impl` extraction imports
  them one-way without an epic resequence.
- [x] Step 3 — `server_helm_impl.py` (child
  `08-04-server-helm-impl-extract`). New top helm leaf (490 lines): 20 helm
  symbols moved verbatim in 4 contiguous blocks (renderers `_render_helm` +
  `_render_helm_*` family, `_helm_value_overrides` / `_helm_operation_note`,
  the `_helm_release` / `_helm_release_revisions` / `_helm_notes` /
  `_helm_current_description` model, and the `_helm_secret_objects` /
  `_helm_secret_object` / `_helm_encoded_release_data` / `_helm_release_payload`
  double-base64 gzip Secret encoders). One-way imports from five lower leaves
  (`server_command_render`, `server_k8s_objects`, `server_mutations`,
  `server_ops_parse`, `server_ops_support`); the only `from .server_ops` is a
  `TYPE_CHECKING` `SimulationState` annotation. `server_ops` re-imports all 20
  at each block's original position (four callers keep resolving:
  `render_command`, `resource_snapshot` ×2, `_k8s_objects_for_resource`); orphaned
  `import base64` / `import gzip` (helm-only) removed. `server_ops.py`
  5,540 → **5,135** lines. Leaf added to the mypy clean gate (30 modules).
  Behavior-identity proven by a render-oracle byte-identical over a 24-command
  helm corpus in both normal and `--mcp-eval-mode` states plus a direct
  `_helm_secret_objects` payload-bytes dump. The epic's blocker
  (helm called 7 staying `server_ops` primitives) was resolved by the
  step-1/2/4 + render-primitive resequence, so no synthetic module or callback
  seam was needed.
- [ ] Steps 5–7 pending.

## Validation Plan

```bash
.venv/bin/pytest tests/test_server.py tests/test_server_ops_fuzz.py \
  tests/test_server_mcp.py tests/test_server_eval_mode.py -n 0
.venv/bin/pytest && .venv/bin/pre-commit run --all-files
# render oracle:
.venv/bin/python <scratch>/render_oracle.py --check
```

## Documentation And Spec Updates

- CLAUDE.md module map + `.trellis/spec/amc/backend/architecture.md` per
  PR (acceptance criterion).
- CLAUDE.md server section gains the "server_ops re-imports moved names;
  patch the canonical home" note the first time a patched name moves.

## Review Notes

- Each PR description: cluster moved, measured sizes, monkeypatch grep
  results, render-oracle diff status. Behavior-identical claim rests on
  the fuzz corpus + oracle — say so explicitly.

## Follow-Ups

- Per-kind descriptor collapse (behavior-affecting; own design).
- `server.py` (1,791 lines) infrastructure/dispatch/CLI split.
- `__getattr__` delegation for `server.py`'s alias block (only if the
  manual block ever becomes a maintenance pain point).
