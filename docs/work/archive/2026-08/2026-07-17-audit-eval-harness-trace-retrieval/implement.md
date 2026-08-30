# Eval recipe trace-evidence loss — Implementation Plan

## Execution Order

1. Branch from `main` (ideally after
   `07-17-audit-serve-main-wiring-tests` merges — its stub pattern hosts
   the warning tests; else include a minimal local copy).
2. Add the eval-without-persistence stderr WARNING to `serve_main`
   (fires iff `mcp_eval_mode` and neither `--persist-command-db` nor
   `--persist-command-log`).
3. Tests: four flag combinations (eval×persistence), warning present in
   exactly one.
4. README eval recipe update + rationale sentences + trace-bundle
   pointer; one-sentence cross-link in the eval-mode doc section.
5. Manual end-to-end: run the printed recipe, stop the server, read the
   persisted store with `amc trace-bundle summary`; paste the transcript
   into the PR.
6. Flip A-066 → `fixed` (same PR). Draft PR → checklist → ready → merge.

## Validation Plan

```bash
.venv/bin/pytest tests/test_serve_main_wiring.py -n 0   # or the local host file
.venv/bin/pytest tests/test_server_eval_mode.py -n 0    # wall untouched
.venv/bin/pytest -m "not heavy" -n 2 && .venv/bin/pre-commit run --all-files
```

## Documentation And Spec Updates

- README (the deliverable); CLAUDE.md/SECURITY.md sentence;
  `.trellis/spec/amc/backend/operations-security-logging.md` if it
  restates the eval recipe.

## Review Notes

- Emphasize: no wall change — reviewers should check the eval-mode test
  file is untouched except additions.

## Follow-Ups

- `07-06-eval-mode-symptom-log-artifact` remains the vehicle for richer
  agent-visible logs; do not scope-creep into it.
