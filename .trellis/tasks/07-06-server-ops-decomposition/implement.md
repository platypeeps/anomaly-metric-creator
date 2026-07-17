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
