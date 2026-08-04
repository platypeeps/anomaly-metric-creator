# Quick simulator environment reset — Implementation Plan

## Execution Order

1. Branch from `main`. Inspect the current reset handler response body and
   `resource_snapshot()` renders for clock-derived fields (design.md risk);
   pick byte-equality vs normalized comparison accordingly.
2. Create a new `test_server_reset.py` on `start_test_server`. Write the
   per-family contract tests (workload, created/deleted resources, events,
   Helm overlay, deleted pods) as mutate → reset → baseline-equal.
3. Add the not-reset assertions (traces survive, clock/generation counters
   unchanged).
4. Add the additive `"scope": "mutation-overlay"` field to the reset
   response if absent; keep existing fields untouched (compat bullet in
   the PRD).
5. README + spec: the explicit does/does-not list and the curl one-liner.
6. Draft PR → checklist → ready → merge.

## Validation Plan

```bash
.venv/bin/pytest tests/test_server_reset.py -n 0
.venv/bin/pytest tests/test_server.py -n 0   # no regressions in existing reset callers
.venv/bin/pytest && .venv/bin/pre-commit run --all-files
```

Manual smoke per design.md (kubectl scale + helm mutation + UI Reset).

## Documentation And Spec Updates

- README serve section; operations spec file if it covers the overlay;
  debug-UI paragraph mentions the button's exact scope.

## Review Notes

- The interesting review question is the byte-equality strategy — state in
  the PR whether renders embed clock-derived fields and how the tests
  handle it.
- Keep the new test file off the heavy-fixture path (fresh tiny run via
  `start_test_server`'s normal flow; no GB fixtures).

## Follow-Ups

- A trace-clearing endpoint, if operators ever ask, is its own task (and
  interacts with eval-harness scoring data — needs the wall review).
