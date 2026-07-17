# Interactive failure-mode launcher — Implementation Plan

## Execution Order

1. Land after `07-17-audit-serve-main-wiring-tests` (reuses its stub-server
   + capsys pattern). Branch from `main`.
2. Add a small `_print_inspection_banner(host, port, namespace, security,
   eval_mode, active_scenarios)` helper in `server.py`; call it from
   `serve_main` after the existing three URL prints. Implement the
   loopback-vs-remote token rendering rule and the eval-mode
   scenario-line suppression from design.md.
3. Check existing `tests/test_server.py` stdout assertions for collisions
   with the new lines; adjust only if a test greps "the whole banner".
4. Tests: banner-content assertions (kubeconfig fetch, namespaced kubectl
   example, reset hint, auth-header iff token, no slugs under eval mode);
   serve-path unknown-slug exit test.
5. README "Launch a failure-mode environment" section; spec touch-up in
   `.trellis/spec/amc/backend/api-cli-server.md` if startup output is
   documented there.
6. Draft PR → checklist → ready → merge.

## Validation Plan

```bash
.venv/bin/pytest tests/test_serve_main_wiring.py tests/test_server.py -n 0
.venv/bin/pytest && .venv/bin/pre-commit run --all-files
```

Manual smoke: launch `db_stall`, run each printed command verbatim
(browser /debug, kubectl get pods/events, helm list, reset curl).

## Documentation And Spec Updates

- README section (the deliverable); spec file only if it already covers
  serve startup output.

## Review Notes

- The token-rendering rule (loopback prints token, remote prints
  placeholder) is the security-sensitive line — call it out in the PR
  description and cover both branches in tests.

## Follow-Ups

- If workshop use later wants a scenario picker UI, that is a debug-UI
  task, not a CLI one.
