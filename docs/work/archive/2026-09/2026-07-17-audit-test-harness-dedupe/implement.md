# OTLP/topology/SQLite harness dedupe — Implementation Plan

## Execution Order

**PR 1 (A-031, prod):** extract `_insert_trace_row` (SQL verbatim, FTS
branch param); run store/trace-bundle suites; flip A-031; draft →
checklist → merge.

**PR 2 (tests):**

1. A-037 first (`run_tool` helper) — smallest, touches the most files
   mechanically; record the contract-checker standalone decision in the
   PR.
2. A-033: move the three topology helpers to conftest; derive exclusion
   windows from SCENARIOS; verify superset-of-hand-lists before
   deleting; diff pass/fail sets pre/post.
3. A-032: collapse test_cli.py's 22 capture servers onto the new
   `capture_otlp_server` fixture in batches of ~5, suite green between
   batches; keep true variants as parameters/subclasses.
4. Flip A-032/A-033/A-037; draft → checklist (test-hygiene heading) →
   merge.

## Validation Plan

```bash
.venv/bin/pytest tests/test_server.py tests/test_trace_bundle.py -n 0   # PR 1
.venv/bin/pytest tests/test_cli.py -n 0                                  # PR 2 batches
.venv/bin/pytest tests/test_topology_registry.py tests/test_topology_saturation.py \
  tests/test_topology_llm.py tests/test_topology_multi_instance.py -n 0
.venv/bin/pytest && .venv/bin/pre-commit run --all-files
rg -c '_Handler' tests/test_cli.py; rg '_EXCLUSION_WINDOWS' tests/
```

## Documentation And Spec Updates

- CLAUDE.md test-hygiene bullet: name `capture_otlp_server` and
  `run_tool` as the canonical harnesses (stops re-invention).

## Review Notes

- PR 2's description lists each collapsed site → variant mapping so the
  reviewer can spot a behavior-fold mistake quickly.

## Follow-Ups

- If a third contract checker appears, revisit the shared-lib decision
  recorded in design.md.
