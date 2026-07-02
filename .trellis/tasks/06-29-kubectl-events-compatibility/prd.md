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
