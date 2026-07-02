# Persist server mutation state optionally

## Goal

Add optional persisted mutation state for workshops that need simulator restart continuity, while keeping unsupported subresources rejected unless explicitly modeled.

## Requirements

- Add optional persisted mutation state for workshops that need simulator restart continuity.
- Persistence must be opt-in and must not change the default in-memory mutation overlay behavior.
- Persist only modeled mutation overlay state; do not create a second Kubernetes state model.
- Keep unsupported subresources rejected unless a subresource is explicitly modeled and tested.
- Define clear reset/cleanup behavior so workshop state can be discarded predictably.

## Acceptance Criteria

- [ ] A documented opt-in mechanism persists supported mutation overlay state across simulator restarts.
- [ ] Default server behavior remains in-memory only.
- [ ] Reset behavior clears persisted mutation state when requested.
- [ ] Unsupported subresources remain rejected and covered by tests.
- [ ] Restart-continuity tests cover at least one persisted workload/resource mutation.

## Notes

- Source: migrated server-mode mutable-state backlog entry.
- This should have `design.md` before implementation because it introduces persistence and restart semantics.
