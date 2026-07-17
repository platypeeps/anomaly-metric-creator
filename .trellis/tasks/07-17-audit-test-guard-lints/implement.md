# Missing test-guard lints and sync checks — Implementation Plan

## Execution Order

1. Branch from `main`. A-058: write the lint (0/1/2 contract, allow
   marker, anchored patterns) + acceptance tests; run against `tests/`
   and triage the two known sites (chunked-stream rewrite preferred over
   `allow`); wire pre-commit.
2. A-059: scenario-table sync test (bidirectional + non-empty guards);
   mutation-check by hiding one row locally.
3. A-023: heavy-registry resolution test via the fixture manager;
   mutation-check with a misspelled name.
4. A-024: JS extraction + `node --check` test with node-absent skipif.
5. A-025: pacing rewrite to monkeypatched sleep capture; keep one
   real-transport assertion.
6. CLAUDE.md lints section gains the new lint (name, marker, exit
   codes); note in `07-17-audit-ci-cadence-closures`' PRD if its guards
   step should pick this lint up (whichever lands second wires it).
7. Flip A-058/A-059/A-023/A-024/A-025 → `fixed` (same PR).
8. Draft PR → checklist (test-hygiene + resource-cost headings) → ready
   → merge.

## Validation Plan

```bash
.venv/bin/python tools/check_test_resource_cost.py tests/   # 0 after triage
.venv/bin/pytest tests/test_test_resource_cost_lint.py -n 0
.venv/bin/pytest tests/test_heavy_marker.py tests/test_otel_gauges.py -n 0
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
