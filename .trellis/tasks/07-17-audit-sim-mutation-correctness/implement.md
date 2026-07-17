# Simulator clock + command-mutation correctness — Implementation Plan

## Execution Order

1. Branch from `main`. A-012 clock guard + tests (smallest; warms up the
   file).
2. A-013: audit the three renderers' target-resolution shapes; add the
   snapshot existence check before overlay writes; kubectl-shaped
   NotFound + exit 1; nameless-scale usage error. Parity tests (command
   path + REST path on the same ghost), overlay-untouched assertions,
   fuzz-corpus shapes.
3. A-014 otel_status pre-seed + locked copy; race test via a
   thread-hammer smoke (poll /v1/state while inserting keys).
4. A-015 regen-failure disk reload under the existing swap lock; test
   via a failing second-pass argv.
5. A-016 csv_layout guard + zero-byte-file test; run gauges/combine
   suites for hash safety.
6. A-017 limit clamp both backends + agreement test.
7. CHANGELOG (nameless-scale behavior change); flip
   A-012/013/014/015/016/017 → `fixed` (same PR).
8. Draft PR → checklist → ready → merge.

## Validation Plan

```bash
.venv/bin/pytest tests/test_server.py tests/test_server_ops_fuzz.py -n 0
.venv/bin/pytest tests/test_gauges_file.py tests/test_combine.py -n 0
.venv/bin/pytest && .venv/bin/pre-commit run --all-files
```

## Documentation And Spec Updates

- CHANGELOG Fixed entries (ghost mutations, clock resume).
- CLAUDE.md only if it documents the command-mutation ordering rule —
  extend the existing refused-mutation paragraph with the command-path
  parity sentence.

## Review Notes

- Lead with the parity table (command vs API path, before/after) — the
  finding was the two entry points disagreeing about one cluster.

## Follow-Ups

- If other renderers gain mutation semantics later, the snapshot-first
  rule from this PR is the template (consider a shared helper then, not
  now).
