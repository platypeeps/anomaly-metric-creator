---
title: Improve kubectl events compatibility
status: planning
created: 2026-06-29
---
# Improve kubectl events compatibility

## Goal

Add kubectl events support or richer event sorting/filtering when installed client behavior or incident workflows need it.

## Requirements

- Add `kubectl events` support or richer event sorting/filtering only if installed client behavior or incident workflows need it.
- Use existing simulator events, mutation events, and resource snapshots as the source of truth.
- Keep event ordering, filtering, and formatting deterministic for tests.
- Record supported/partial `CommandTrace` entries and unsupported traces for nearby unmodeled event requests.

## Acceptance Criteria

- [ ] The chosen event command/API behavior returns Kubernetes-shaped, scenario-appropriate event output.
- [ ] Sorting/filtering behavior is deterministic and documented through focused tests.
- [ ] Unsupported event options or resources are visible through trace coverage.
- [ ] `tests/test_server.py` covers supported and nearby unsupported cases.

## Notes

- Source: migrated server-mode compatibility backlog entry.
- Keep compatibility backed by existing event/resource state; do not introduce a parallel event store.
- **Current state (verified 2026-07-06):** the base command already works —
  top-level `kubectl events` is rewritten to the `get events` path at
  [server_ops.py:1540](src/anomaly_metric_creator/server_ops.py:1540), and
  classic `kubectl get events` is served via `_SNAPSHOT_KINDS`
  ([server_ops.py:1208](src/anomaly_metric_creator/server_ops.py:1208)).
  Remaining scope is only the *richer sorting/filtering* half of the goal
  (e.g. `--sort-by`, `--for`, `--types`) — scope any implementation to
  those, and only if a concrete incident workflow needs them.
