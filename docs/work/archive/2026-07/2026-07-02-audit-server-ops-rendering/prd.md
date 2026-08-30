---
title: Audit server_ops k8s/Helm rendering under malformed client input
status: done
created: 2026-07-02
branch: test/server-ops-fuzz-audit
---
# Audit server_ops k8s/Helm rendering under malformed client input

## Audit context

- **Source:** first-time staff-engineer audit, 2026-07-02.
- **Confidence:** SUSPICION (largest unaudited surface; verification task).
- **Severity:** UNKNOWN until verified — likely robustness (unhandled 500s), not
  RCE (confirmed: no shell-out / eval / pickle anywhere in `src/`).
- **Category:** robustness / input handling.

## Goal

Establish that `server_ops.py` — the ~7.6k-line fake Kubernetes/Helm API and
command renderer — degrades gracefully (structured error, correct status code,
intact trace state) under adversarial or malformed `kubectl`/`helm`/HTTP-API
input, rather than throwing unhandled exceptions or corrupting simulator state.

## Problem (why it needs checking)

`server_ops.py` is by far the largest module the first-pass audit did **not**
read in depth. What *was* confirmed: the command simulator never shells out
(`grep` for `subprocess`/`os.system`/`shell=True`/`eval`/`exec`/`pickle` is
empty across `src/`), and the top-level `do_GET`/`do_POST` handlers wrap
everything in a `try/except Exception → 500` boundary
([server.py:464](src/anomaly_metric_creator/server.py:464),
[server.py:533](src/anomaly_metric_creator/server.py:533)).

What is **unverified**: whether the command parser (`parse_command`/`shlex` +
the small flag parser) and the many `_render_*` / `_k8s_*` / `_helm_*` functions
handle malformed input without (a) throwing exceptions that leak internals into
the 500 body (`{"error": str(exc)}` — could expose paths/state), (b) corrupting
the `CommandTrace` record or the `SimulationMutations` overlay, or (c) producing
non-Kubernetes-shaped error bodies where a real client expects a `Status`
object.

## Requirements

- Inventory the entry points: `run_command`/`parse_command`/`render_command`,
  `kubernetes_api_response` / `kubernetes_api_post_response` /
  `kubernetes_api_mutating_response`, and the resource/table/object renderers.
- Exercise them with malformed input: unbalanced quotes in `command`; huge/empty
  argv; unknown verbs/resources/namespaces; path-injection-shaped resource names
  (`../`, very long, unicode, null bytes); malformed JSON bodies that slip past
  the body-cap check; label/field selectors with bad syntax; Helm release names
  that don't exist. Prefer a property/fuzz test over hand-picked cases.
- Confirm for each: no unhandled exception escapes as a 500 that leaks internal
  detail; API paths return a Kubernetes `Status` (not a bare JSON error) where a
  real client expects one; every call still records a well-formed `CommandTrace`
  classified `kubernetes-api`/command-family correctly; the mutation overlay is
  never left partially mutated.
- Confirm the 500 `{"error": str(exc)}` body
  ([server.py:466](src/anomaly_metric_creator/server.py:466)) does not leak
  sensitive strings (paths, tokens) for any reachable exception — redact or
  genericize if it can.
- Record findings; spin confirmed bugs into their own tasks or fix inline if
  small.

## Acceptance criteria

- [x] A fuzz/property test drives the command + k8s-API parsers with malformed
      input and asserts: process stays up, status codes are correct
      (400/404/413/422 not 500 for *expected* bad input), API errors are
      Kubernetes `Status`-shaped, and traces are recorded and correctly
      classified.
- [x] The 500 error body is confirmed free of sensitive leakage (or fixed).
- [x] The mutation overlay is verified consistent after a malformed mutating
      request (no partial state).
- [x] Any confirmed defect is filed as a follow-up task or fixed with a
      regression test.

## Notes

- This is scoped as an **audit** task, not a fix task — its primary deliverable
  is either "clean, with new fuzz coverage" or a list of filed sub-tasks.
- Keeps the reassuring headline honest: the audit could confirm *no shell-out*,
  but could not confirm *graceful degradation* across 7.6k lines of renderers.
