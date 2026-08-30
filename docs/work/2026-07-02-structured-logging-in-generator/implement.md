# Structured logging in the generator — Implementation Plan

## Execution Order

1. Branch from `main` (confirm decomp step 9 is not concurrently open on
   the resolution cluster; coordinate if so).
2. Grep the suite for every asserted warning literal from the 11 sites;
   list them in the PR as the byte-stability contract.
3. Add the package logger + `_ensure_cli_log_handler()` (idempotent,
   marker-checked) called at `main()` entry.
4. Convert the 7 legacy.py sites, run the stderr-asserting tests; then
   the 4 otel_stream.py sites, run the OTEL tests.
5. Serve capture: regen-worker forwarding handler with guaranteed
   detach; single-emission-after-two-passes test + structured-record
   assertion.
6. CLAUDE.md: one paragraph (logger name, byte-stable prefix rationale,
   serve capture hook).
7. Draft PR → checklist → ready → merge.

## Validation Plan

```bash
.venv/bin/pytest tests/test_scenarios.py tests/test_cli.py -n 0   # stderr contracts
.venv/bin/pytest tests/test_otel_gauges.py -n 0
.venv/bin/pytest tests/test_server.py -n 0 -k "continuous"
.venv/bin/pytest && .venv/bin/pre-commit run --all-files
```

## Documentation And Spec Updates

- CLAUDE.md; `.trellis/spec/amc/backend/` logging conventions stub if
  the spec set has a home for it (grep the index).

## Review Notes

- The `WARNING: ` literal-in-message trade is the one reviewers will
  poke — the byte-stability rationale and the zero-test-edit diff are
  the answer.
- Handler lifecycle (idempotent attach, guaranteed detach) is the
  xdist-safety story; point reviewers at the single-emission test.

## Follow-Ups

- Verbosity flags / log-level surface only if serve operators ask;
  out of scope now.
