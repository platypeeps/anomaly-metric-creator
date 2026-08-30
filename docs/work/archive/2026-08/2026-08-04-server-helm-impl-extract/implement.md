# server_helm_impl.py extraction — Implement (epic step 3)

Ordered execution. Verbatim move, one-way import, re-import stub per block,
splice-hazard grep, oracle diff. Move-set + closure pinned in `design.md`.

## Steps

- [ ] **0. Oracle baseline** — `helm_before.json` captured on branch tip
  pre-move (18-command helm corpus + Secret payload dump, normal + eval).
  DONE before task activation.
- [ ] **1. Confirm move-set** — grep the 20 defs + 4 caller sites in
  `server_ops.py` still match `design.md` ranges (Block1 625–675, Block2
  2918–3160, Block3 3465–3496, Block4 5000–5104).
- [ ] **2. Create `server_helm_impl.py`** — module docstring; the
  `design.md` import block; then paste all 20 functions **verbatim** (order:
  Block 3 helpers `_helm_notes`/`_helm_current_description` and Block 4 before
  their callers is unnecessary — Python resolves at call time — so paste in
  source order Block1→2→3→4 for reviewability). Add `__all__` listing the 20
  public-to-server_ops names.
- [ ] **3. Cut + stub in `server_ops.py`** — delete blocks **highest line
  first** (Block4 5000–5104, Block3 3465–3496, Block2 2918–3160, Block1
  625–675) so earlier ranges stay valid. At each deleted block's position
  insert a `from .server_helm_impl import (…)` re-import stub of exactly that
  block's names, at the same conceptual location.
- [ ] **4. Splice-hazard grep** — for each of the 4 cut ranges, confirm no
  prior extraction's `^from \.` re-import stub was swept into the delete;
  re-verify every leaf re-import in `server_ops.py` still resolves
  (`.venv/bin/python -c "import anomaly_metric_creator.server_ops"`).
- [ ] **5. Prune leaf imports** — `.venv/bin/ruff check
  src/anomaly_metric_creator/server_helm_impl.py` → drop any F401-unused name
  from the import block (e.g. `_first_flag_value`/`Any` if unreferenced).
- [ ] **6. Compat + identity** — import `anomaly_metric_creator.server` and
  `.server_mcp` clean; assert `server_ops.<name> is server_helm_impl.<name>`
  for all 20 moved names.
- [ ] **7. One-way runtime grep** — `grep -nE 'from \.server_ops|import
  server_ops' server_helm_impl.py` → only the `TYPE_CHECKING` `SimulationState`
  line.
- [ ] **8. Oracle after** — run `helm_oracle.py` → `helm_after.json`; `cmp
  helm_before.json helm_after.json` byte-identical (covers command output +
  Secret payload bytes).
- [ ] **9. mypy gate** — `.venv/bin/mypy --follow-imports=silent
  server_helm_impl.py`; if clean, add to `tools/check_mypy_gate.py`
  `CLEAN_MODULES` and bump the `tests/test_mypy_gate_lint.py` count. Close any
  verbatim-inherited `var-annotate` gap with an explicit annotation first.
- [ ] **10. Suites** — `-n 0`: `tests/test_server.py
  tests/test_server_ops_fuzz.py tests/test_server_mcp.py
  tests/test_server_eval_mode.py`; then full `.venv/bin/pytest`.
- [ ] **11. Docs** — CLAUDE.md server-module map (new leaf + DAG line);
  `.trellis/spec/amc/backend/architecture.md` leaf inventory + DAG; epic
  `07-06 implement.md` step-3 done + measured `server_ops.py` size.
- [ ] **12. Line count** — record `server_ops.py` end line count in the PR
  body and epic step tracker.

## Rollback

Pure additive+move on one branch. `git checkout -- server_ops.py &&
git rm server_helm_impl.py` reverts. No data/schema/CLI surface touched.

## Validation commands

```bash
.venv/bin/python -c "import anomaly_metric_creator.server, anomaly_metric_creator.server_mcp"
.venv/bin/ruff check src/anomaly_metric_creator/server_helm_impl.py
.venv/bin/python tools/check_mypy_gate.py
.venv/bin/pytest -n 0 tests/test_server.py tests/test_server_ops_fuzz.py tests/test_server_mcp.py tests/test_server_eval_mode.py
.venv/bin/pytest
```
