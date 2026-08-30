---
title: Server compatibility and debug UI polish
status: done
created: 2026-06-26
---
# Server compatibility and debug UI polish

## Goal

Improve AMC serve-mode workshop compatibility by making `kubectl apply -f`
consume real multi-document YAML/JSON manifest files instead of only deriving a
single resource from the filename, add deployment rollout lifecycle commands,
then make the resulting simulator state more visible in the debug UI and keep
user-facing docs and Trellis guidance in sync.

## Confirmed Facts

- The prior server-mode handoff listed `kubectl apply -f` for multi-document
  YAML or JSON payloads as a good next compatibility target and said every new
  surface needs parser coverage, trace visibility, nearby unsupported/partial
  coverage, and focused `tests/test_server.py` tests.
- `src/anomaly_metric_creator/server_ops.py` already parses `apply`/`diff` as
  manifest commands, writes created resources through `SimulationMutations`, and
  projects generic rows into `resource_snapshot()`.
- `_render_apply()` currently derives a single `(kind, name)` from the filename,
  so it cannot reflect actual object names, namespaces, labels, data, or
  multiple manifest documents.
- `src/anomaly_metric_creator/server_debug_ui.py` already renders mutation drift,
  created resources, resource diffs, and resource drawers from `/v1/state` and
  `/v1/debug/resources`; debug UI changes should consume those same backend
  payloads rather than inventing browser-only state.
- The same handoff also listed `kubectl rollout pause`, `resume`, and `undo` as
  useful next server-mode compatibility targets.
- Rollout status/history/restart already operate on deployment targets through
  scenario profiles and `SimulationMutations` workload overlays.

## Requirements

- Support command-mode `kubectl apply -f PATH` for readable local `.json`,
  `.yaml`, and `.yml` manifest files.
- Support multiple YAML documents from one file, and JSON files containing
  either one object or a list of objects.
- For supported simulator-backed kinds, preserve manifest `kind`,
  `metadata.name`, `metadata.namespace`, labels, annotations, and known `data` /
  `spec` fields through the existing generic resource row helpers.
- Keep dry-run behavior side-effect free while still reporting the resources
  that would be configured.
- Return a partial result with a clear error when a manifest file cannot be
  read, parsed, has an unsupported top-level shape, has a document without
  kind/name, or contains no supported simulator-backed resources.
- Preserve command trace visibility with a stable `matched_rule_id` for
  manifest-backed apply paths.
- Support `kubectl rollout pause`, `kubectl rollout resume`, and
  `kubectl rollout undo` for deployment targets, including the common
  `deployment NAME` and `deployment/NAME` command forms.
- Preserve command trace visibility with stable `matched_rule_id` values for
  the new rollout commands, and reject non-deployment rollout targets as
  unsupported.
- Improve debug UI visibility for created resources so applied manifest objects
  are easier to inspect from the overlay/resource-diff surfaces.
- Update README and canonical Trellis guidance so completed and remaining
  server-mode items match the implementation.

## Acceptance Criteria

- [x] `kubectl apply -f` configures multiple supported resources from one YAML
      manifest file and those resources appear in later `kubectl get`,
      `/v1/state`, and `/v1/debug/resources` output.
- [x] `kubectl apply --dry-run=client -f` reports the manifest resources without
      mutating `SimulationMutations`.
- [x] Invalid or unsupported manifest payloads return partial/clear errors and
      remain traceable.
- [x] Debug UI resource diff / mutation surfaces show applied resource
      identities and namespaces clearly enough to inspect them from the existing
      drawer path.
- [x] Focused server tests cover supported multi-document YAML apply, JSON list
      apply, dry-run apply, and invalid manifest behavior.
- [x] `kubectl rollout pause`, `resume`, and `undo --to-revision` update the
      workload overlay and appear in later rollout status / event output.
- [x] Non-deployment rollout lifecycle targets remain unsupported instead of
      being treated as generic rollout-compatible resources.
- [x] README and Trellis docs no longer describe this slice as future work.

## Notes

- Keep behavior backed by `resource_snapshot()` and `SimulationMutations`; do
  not add a second Kubernetes state model.
- PyYAML is available in the dev environment, but runtime should still report a
  clear partial error if YAML manifests are requested without PyYAML installed.
- Rollout lifecycle simulation is intentionally deployment-scoped and backed by
  workload mutation overlays plus synthetic command events.
