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

- [x] A documented opt-in mechanism persists supported mutation overlay state across simulator restarts. (`--persist-mutations PATH`, README § mutation overlay persistence; `test_mutations_survive_a_restart`)
- [x] Default server behavior remains in-memory only. (`test_persist_mutations_defaults_to_off`, `test_flag_off_writes_nothing_and_behaves_identically` — the latter chdirs into `tmp_path` and spies the atomic writer, so a stray relative-path write fails the guard)
- [x] Reset behavior clears persisted mutation state when requested. (`POST /v1/mutations/reset` and the debug UI Reset button truncate the file with the overlay; `test_reset_truncates_the_file_as_well_as_memory`)
- [x] Unsupported subresources remain rejected and covered by tests. (`_k8s_subresource_mutation_allowed` still admits only `deployments/scale`; `test_mutating_kubernetes_api_updates_simulated_state` asserts 405 `MethodNotAllowed` for `/status` and `/log`. Unchanged by this task — the criterion is that persistence did not widen the surface)
- [x] Restart-continuity tests cover at least one persisted workload/resource mutation. (`_mutate` exercises workload, pod-delete, resource put/delete, and Helm revisions/values; `test_mutations_survive_a_restart` and `test_every_commit_reaches_disk_not_only_the_last` replay them across a reload)

## Notes

- Source: migrated server-mode mutable-state backlog entry.
- This should have `design.md` before implementation because it introduces persistence and restart semantics.
