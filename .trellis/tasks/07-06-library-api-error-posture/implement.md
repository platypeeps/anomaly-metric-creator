# Library-API error posture — Implementation Plan

## Execution Order

1. Branch from `main`. Grep for docstring-asserting tests over the
   target modules (expect none).
2. Add the posture paragraphs: two function docstrings
   (`combine_logs_unified`/`combine_logs`), two skip-semantics notes
   (`stream_otel_gauges`, `write_gauges_csv`), two module-docstring
   notes (`otlp.py`, `csv_layout.py`), three facade sentences.
3. Record the posture (rationale, revisit trigger → typed-boundaries) in
   the focused CLI-error spec `.trellis/spec/amc/backend/api-cli-server.md`;
   leave `error-handling.md` as the pointer it is (it forbids new
   conventions).
4. CLAUDE.md facade-section paragraph.
5. Draft PR → checklist (doc-sync heading) → ready → merge.

## Validation Plan

```bash
rg -l "CLI-internal surface" src/anomaly_metric_creator/   # all listed sites
.venv/bin/pytest -m "not heavy" -n 2
.venv/bin/pre-commit run --all-files
```

## Documentation And Spec Updates

This task *is* the documentation; verify the posture landed in
`api-cli-server.md` and that `error-handling.md` still points at the
focused specs (unchanged).

## Review Notes

- Reference the PRD's recorded decision + rationale in the PR body so
  Copilot doesn't flag the "documented SystemExit" as a bug to fix.

## Follow-Ups

- None unless an embedder requirement appears (revisit trigger recorded
  in the spec).
