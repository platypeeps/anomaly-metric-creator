# Extract server_k8s_api.py — Implementation Plan (epic step 5)

## Execution Order

0. **Render-oracle capture (before)**: scratch script drives a fixed
   k8s-API corpus (see design Validation) through `kubernetes_api_response`
   / `run_command` on a frozen clock, dumps stdout/bytes/exit to
   `/tmp/.../oracle_before.txt`. Keep the script under the scratchpad.
1. **Companion move**: relocate `_preview` from `server_ops.py` into
   `server_ops_support.py` (verbatim). `server_ops_support.py` has **no**
   `__all__` today (ends ~line 80) — do **not** invent one; `_preview`
   becomes a normal module-level def imported directly. Re-import
   `_preview` in `server_ops.py` at its original position (it is in
   `server_ops.__all__` at :5325 — keep that entry, membership unchanged).
   Run server tests — expect green (no behavior change).
2. **Create `server_k8s_api.py`**: module header with
   `from __future__ import annotations`, stdlib imports, one-way leaf
   imports (grep each helper's canonical home first), and the
   `if TYPE_CHECKING: from .server_ops import SimulationState` guard.
3. **Move members in audit groups** (verbatim), one group at a time so a
   mistake is bisectable: response builders + dataclass → discovery/data →
   OpenAPI structural → filters/selectors → watch pure → mutation-parse →
   api trace/fingerprint/redaction → body-read/constants/kubeconfig. After
   each group: delete the bodies from `server_ops.py`, add the
   `from .server_k8s_api import (...)` re-import at the original position,
   grep the cut range for orphaned `^from \.` leaf stubs.
4. **`server_ops.py` cleanup**: confirm `__all__` (:5212–5440) membership
   byte-unchanged (moved names already in it stay via re-import; moved names
   not in it — `_query_int`, `_query_str`, etc. — are NOT added); remove any
   now-orphaned stdlib imports the moved bodies owned (grep the deleted
   names). Confirm no residual free reference to a moved name remains
   defined-but-unused or used-but-undefined.
5. **Structural closure check (the decisive spine-stayed proof)**:
   - `grep -c "resource_snapshot" server_k8s_api.py` MUST be `0` — proves no
     snapshot-bound dispatcher moved (the audit's whole risk). If nonzero, a
     spine function was moved by mistake; revert it to `server_ops`.
   - `grep -nE "^\s*from \.server_ops import|^\s*import .*server_ops" server_k8s_api.py`
     MUST return nothing except the `TYPE_CHECKING`-guarded `SimulationState`
     line — proves the one-way rule.
   - `python -c "import anomaly_metric_creator.server"` succeeds — proves the
     re-import seam + `server.py` alias block resolve.
6. **Leaf-size criterion (PRD AC)**: `wc -l server_k8s_api.py`. If < 800,
   done. If ≥ 800, either (a) carve the self-contained `_api_*`
   trace/fingerprint/redaction sub-cluster into a second small leaf
   `server_k8s_api_trace.py` along that seam, or (b) record an explicit
   size exemption (cohesive single k8s-REST-builder surface) in the PR
   description AND the CLAUDE.md module-map note, mirroring the epic's
   data-registry-exemption convention. Decide at edit time from the measured
   number; do not leave the cap silently violated.
7. **mypy gate**: add `server_k8s_api` (and the trace leaf if created) to
   `tools/check_mypy_gate.py` gated list; run it.
8. **Render-oracle (after)**: rerun the scratch script → `oracle_after.txt`;
   `diff` must be empty.
9. **Docs**: update the CLAUDE.md server module map (add
   `server_k8s_api.py`, note the spine stayed + `_preview` moved to support,
   plus any size exemption from step 6) and
   `.trellis/spec/amc/backend/architecture.md` map if it carries one.
   Record measured `server_ops.py` end size and the leaf size.

## Validation Plan

```bash
# focused, serial (matches epic convention)
.venv/bin/pytest tests/test_server.py tests/test_server_ops_fuzz.py \
  tests/test_server_mcp.py tests/test_server_eval_mode.py \
  tests/test_server_watch.py -n 0
# render oracle
diff /tmp/.../oracle_before.txt /tmp/.../oracle_after.txt   # empty
# gates
.venv/bin/python tools/check_mypy_gate.py
.venv/bin/pre-commit run --all-files
# full suite
.venv/bin/pytest
```

Decisive checks (spine-stayed rests on the STRUCTURAL check, not one test):
- `grep -c resource_snapshot server_k8s_api.py == 0` (step 5) — the primary
  proof that no snapshot-bound dispatcher moved. `test_server.py`'s
  `resource_snapshot` monkeypatch case (installed at :563, asserted at
  :574/:581) covers only the OpenAPI-document route and is a *behavior*
  spot-check, not the completeness proof — the grep is.
- full `tests/test_server.py` + fuzz + eval + watch green — behavior net over
  every dispatcher, including the snapshot-bound ones staying at
  `server_ops.py:4438` / `:4872`.
- render-oracle empty diff — byte-identical k8s-API behavior.
- import of `anomaly_metric_creator.server` succeeds — re-import seam +
  `server.py` alias block resolve.

## Documentation And Spec Updates

- CLAUDE.md "Server mode" section module map: new leaf, DAG placement
  (`server_k8s_api` imports leaves one-way, imported only by `server_ops`
  re-import), `_preview` now in `server_ops_support`.
- `.trellis/spec/amc/backend/architecture.md` map (if present).
- Epic `07-06-server-ops-decomposition/implement.md` step-5 status line →
  `[x]` with measured sizes (do in the finish/close-out, not necessarily
  this PR — but update the step tracker).

## Review Notes

- PR description: cluster moved, measured before/after sizes, `_preview`
  companion move rationale, monkeypatch-grep result, splice-hazard grep
  result, render-oracle empty-diff status. State the behavior-identical
  claim rests on fuzz corpus + oracle explicitly.
- Emphasize what STAYED and why (resource_snapshot pin) so a reviewer does
  not read the partial move as an incomplete step-5.

## Rollback Points

- After step 1 (companion move) tests green — safe checkpoint.
- After each member group + re-import, tests green — bisectable checkpoints.
- Any red at a group boundary: revert that group's cut, re-import stays, tree
  returns to prior green.

## Follow-Ups (out of this PR)

- Move `_openapi_paths` + snapshot-kind constants companion (needs
  `server_ops_support` to own `_snapshot_kind_namespaced` +
  `_SNAPSHOT_KINDS`/`_MUTATION_SNAPSHOT_KINDS`/`_NAMESPACED_SNAPSHOT_KINDS`/
  `_CLUSTER_SCOPED_SNAPSHOT_KINDS`) — enables moving the OpenAPI document
  builders too.
- Epic steps 6 (`server_ops_render.py` + `server_ops_render_workloads.py`)
  and 7 (close-out: end size, final map, archive epic).
