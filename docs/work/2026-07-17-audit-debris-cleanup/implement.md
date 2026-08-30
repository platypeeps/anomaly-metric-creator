# Audit debris cleanup — Implementation Plan

## Execution Order

1. Branch from `main`. A-036 + A-038 (pure deletions, grep-confirmed).
2. A-003: delete server.py copy, add the alias-block line, grep both
   modules' consumers.
3. A-004: import-time field-order assertion beside `Instance` + the
   step-9 carry-through comment; quick failure check by locally adding
   a fake field (revert).
4. A-035: reference sweep (both spellings) → delete shim + references.
5. Flip A-003/A-004/A-035/A-036/A-038 → `fixed` (same PR).
6. Draft PR → checklist → ready → merge.

## Validation Plan

```bash
rg -n "temp_output_dir|RESOURCE_KINDS|classify_ci_changes" --hidden -g '!.git' .
rg -n "DEFAULT_MAX_BODY_BYTES" src/    # one definition + one alias
.venv/bin/python -c "import anomaly_metric_creator.server"
.venv/bin/pytest tests/test_server.py tests/test_instances_per_component.py -n 0
.venv/bin/pytest && .venv/bin/pre-commit run --all-files
```

## Documentation And Spec Updates

- None beyond the ledger flips (no documented surface names these
  symbols; verify with the grep sweep).

## Review Notes

- Straight debris PR; the A-004 assertion is the only added line of
  logic — point reviewers at the local mutation check.

## Follow-Ups

- The assertion migrates with `Instance` in decomp step 9 (already
  noted in that task's design).
