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

- [ ] `kubectl get --watch` emits scenario-appropriate watch-style output for at least one meaningful resource family.
- [ ] Real-client API watch requests receive Kubernetes-shaped streaming or bounded response behavior for the supported resource path.
- [ ] Unsupported watch resources or options produce clear partial/unsupported traces without crashing the simulator.
- [ ] Focused coverage is added in `tests/test_server.py` for command-mode and real-client behavior where applicable.
- [ ] Existing server-mode smoke and regression tests continue to pass.

## Notes

- Source: migrated server-mode compatibility backlog entry.
- Read `.trellis/spec/amc/backend/api-cli-server.md` and `.trellis/spec/amc/backend/operations-security-logging.md` before implementation.
