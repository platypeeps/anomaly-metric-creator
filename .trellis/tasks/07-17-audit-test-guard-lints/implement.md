# Missing test-guard lints and sync checks — Implementation Plan

## Execution Order

1. Branch from `main`. A-058: write the AST-backed lint (recursive file/directory
   inputs, aggregated 0/1/2 contract, trailing allow marker) + acceptance tests;
   run it against `tests/`, rewrite the two audit-identified unsafe reads, and
   explicitly exempt intentional small-artifact reads.
2. Wire A-058 to pre-commit and the always-run CI changes job. Extend the CI
   contract guard and mutation suite so either anchor cannot drift.
3. A-059: scenario-table sync test (bidirectional + non-empty guards) covering
   slug, signal/severity, days, and component sets;
   mutation-check by hiding one row locally.
4. A-023: heavy-registry resolution test via the fixture manager;
   mutation-check with a misspelled name.
5. A-024: focused JS extraction + `node --check` test with node-absent skipif.
6. A-025: pacing rewrite to monkeypatched sleep capture; keep real
   real-transport assertion.
7. CLAUDE.md and the testing-quality spec gain the enforced guard contract.
8. Flip A-058/A-059/A-023/A-024/A-025 → `fixed` (same PR).
9. Refresh generated repository/KB knowledge, draft PR, run the full checklist
   (test hygiene + resource cost + CI/workflow), then ready
   → merge.

## Validation Plan

```bash
.venv/bin/python tools/check_test_resource_cost.py tests/   # 0 after triage
.venv/bin/pytest tests/test_test_resource_cost_lint.py -n 0
.venv/bin/pytest tests/test_readme_scenario_catalog_sync.py \
  tests/test_debug_ui_javascript.py tests/test_heavy_marker.py \
  tests/test_otel_gauges.py -n 0
.venv/bin/pytest tests/test_ci_review_contract.py -n 0
.venv/bin/pytest && .venv/bin/pre-commit run --all-files
```

Mutation checks per design.md (lint 1/0/2 arms; hidden README row;
misspelled fixture name).

## Documentation And Spec Updates

- CLAUDE.md lint inventory + the test-resource-cost prose section gets a
  "now lint-enforced" note (keep the guidance, point at the tool).

## Review Notes

- The two triaged sites are the proof the lint earns its keep — show
  before/after in the PR description.

## Follow-Ups

- If the JS syntax check catches something real, a debug-UI execution
  harness (jsdom/node smoke) becomes worth its own task.
