# kubectl logs refinements — Implementation Plan

## Execution Order

1. Branch from `main`. Fix the `--since` silent no-op: duration parser
   (subset grammar, simulated-clock-relative) → reuse the `--since-time`
   cutoff path; move the flag into the modeled set; malformed-duration
   and both-flags-conflict errors.
2. Add `--timestamps` (modeled flag + RFC3339 prefixes from the lines'
   simulated times; deterministic index-derived fallback).
3. Tests: since-filtering correctness against known simulated windows,
   conflict case, timestamps on/off byte assertions, fuzz durations.
4. Manual real-kubectl transcript.
5. Draft PR → checklist → ready → merge.

## Validation Plan

```bash
.venv/bin/pytest tests/test_server.py -n 0 -k "logs"
.venv/bin/pytest tests/test_server_ops_fuzz.py -n 0
.venv/bin/pytest && .venv/bin/pre-commit run --all-files
```

## Documentation And Spec Updates

- README serve kubectl notes if supported log flags are listed.

## Review Notes

- Emphasize the silent-no-op → modeled transition for `--since`: the
  trace support status changes from misleadingly-supported to actually
  supported, which is the finding's point.

## Follow-Ups

- Multi-container histories: deferred until a workshop workflow needs
  them (PRD defer rule).
