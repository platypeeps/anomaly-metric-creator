---
title: Support bounded Kubernetes watch streams
status: done
created: 2026-06-29
branch: feat/server-watch-semantics
---
# Support bounded Kubernetes watch streams

## Goal

Implement kubectl get --watch and Kubernetes API watch semantics as a bounded simulated stream backed by resource_snapshot() or SimulationMutations.

## Requirements

- Support `kubectl get --watch` in command mode for simulator-backed resources where a bounded watch stream can be produced from `resource_snapshot()` and the mutation overlay.
- Support Kubernetes API watch behavior for real clients when the requested resource path is already modeled by the fake API facade.
- Keep the stream bounded and deterministic enough for tests and workshop use; do not introduce an unbounded long-running state model.
- Record a supported or partial `CommandTrace` for command-mode usage, and record unsupported-path traces for nearby unmodeled watch requests.
- Preserve the rule that compatibility surfaces must be backed by `resource_snapshot()` or `SimulationMutations`, not a second Kubernetes state model.

## Acceptance Criteria

- [x] `kubectl get --watch` emits scenario-appropriate watch-style output for at least one meaningful resource family. (Command mode renders the one-shot pods/deployments table plus a real-kubectl note and is classified `partial` (rule `kubectl.get.<kind>.watch`); real clients stream `ADDED`/`MODIFIED`/`DELETED` for pods and `apps/v1` deployments.)
- [x] Real-client API watch requests receive Kubernetes-shaped streaming or bounded response behavior for the supported resource path. (`GET …?watch=true` streams newline-delimited watch events bounded by `min(timeoutSeconds, _WATCH_MAX_SECONDS)` and the shutdown event, one SSE slot.)
- [x] Unsupported watch resources or options produce clear partial/unsupported traces without crashing the simulator. (Unmodeled kinds → 404 unsupported trace; SSE-ceiling refusal → partial Status 503; malformed inputs covered by the fuzz corpus.)
- [x] Focused coverage is added for command-mode and real-client behavior in the dedicated `tests/test_server_watch.py` (the location design.md sanctioned over `tests/test_server.py`) plus watch cases in `tests/test_server_ops_fuzz.py`.
- [x] Existing server-mode smoke and regression tests continue to pass. (Full suite: 1746 passed, 2 skipped.)

## Notes

- Source: migrated server-mode compatibility backlog entry.
- Read `.trellis/spec/amc/backend/api-cli-server.md` and `.trellis/spec/amc/backend/operations-security-logging.md` before implementation.
