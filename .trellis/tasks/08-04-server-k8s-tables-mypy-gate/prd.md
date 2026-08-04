# Annotate `server_k8s_tables` and add it to the mypy clean gate

## Goal

`server_k8s_tables.py` was extracted verbatim from `server_ops.py` in task
`08-04-server-k8s-objects-tables-extract`. It carries exactly one mypy
`var-annotate` gap inherited unchanged from its `server_ops` origin, so it is
the only one of the three new k8s leaves NOT yet in the mypy clean-module gate
(`tools/check_mypy_gate.py`). Close that gap and add the module to the gate so
all three leaves are type-checked.

## Requirements

- `src/anomaly_metric_creator/server_k8s_tables.py`, `_k8s_node_cells`: the line
  `ready = next((condition for condition in conditions if
  condition.get("type") == "Ready"), {})` needs an explicit `dict[str, Any]`
  annotation (mypy cannot infer the type from the empty-dict fallback).
  Annotate only; do not change behavior.
- `tools/check_mypy_gate.py`: add
  `"src/anomaly_metric_creator/server_k8s_tables.py"` to `CLEAN_MODULES`
  (sorted position, between `server_k8s_objects.py` and `server_kubernetes.py`).
- `tests/test_mypy_gate_lint.py`: bump the expected clean-module count 27 → 28.

## Acceptance Criteria

- [ ] `.venv/bin/python tools/check_mypy_gate.py` exits 0 with
  `server_k8s_tables.py` in the checked set.
- [ ] `tests/test_mypy_gate_lint.py` passes with the updated count.
- [ ] Server-family suite stays green
  (`tests/test_server.py tests/test_server_ops_fuzz.py tests/test_server_mcp.py
  tests/test_server_eval_mode.py tests/test_server_watch.py`).
- [ ] Whole suite + `pre-commit run --all-files` clean (excluding the
  pre-existing workspace journal-index drift unrelated to this change).

## Notes

- Non-goal: no refactor of the table/cell builders beyond the single
  annotation; no change to `server_k8s_objects.py` or `server_ops_support.py`
  (already gated).
