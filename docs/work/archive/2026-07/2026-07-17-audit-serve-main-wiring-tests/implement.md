# Test serve_main composition — Implementation Plan

## Execution Order

1. Branch from `main`. Create a new `test_serve_main_wiring.py` under `tests/`
   with minimal in-memory state/server doubles; do not generate artifacts or
   duplicate an HTTP smoke already covered through the real eval-mode handler.
2. Write test 1 (eval-kwarg capture via `_StopWiring` sentinel) with both
   the `--mcp-eval-mode` and no-flag control cases. Run it; mutation-check
   by locally removing the `eval_mode=` kwarg (must fail), then restore.
3. Write test 2 (eight-flag → `ServerSecurityConfig` field-by-field
   mapping + stub server `max_workers`/`max_sse` passthrough and cleanup).
   Mutation-check by swapping two kwargs locally, then restore.
4. Flip A-020 → `fixed` in `.trellis/audit/ledger.md` (same PR).
5. Draft PR → pre-PR checklist (test-hygiene + resource-cost headings
   matter here) → ready → merge.

## Validation Plan

```bash
.venv/bin/pytest tests/test_serve_main_wiring.py -n 0
.venv/bin/pytest tests/test_server.py tests/test_server_eval_mode.py -n 0
.venv/bin/pytest            # full suite
.venv/bin/pre-commit run --all-files
```

## Documentation And Spec Updates

- Ledger flip A-020 in the same PR.
- No CLAUDE.md change needed unless the review decides the wiring-test
  pattern deserves a sentence in the server-mode section (optional).

## Review Notes

- The PR description should name the mutation checks performed — the tests'
  entire value is failing on the silent-wall-drop regression, so prove it.
- Keep all patches inside `monkeypatch` so xdist workers stay isolated;
  no module/global state may leak (order-independence rule).

## Follow-Ups

- None expected; live handler behavior remains owned by the existing eval-mode
  server tests rather than duplicated in this composition-only file.
