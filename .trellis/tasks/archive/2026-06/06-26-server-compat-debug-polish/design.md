# Server Compatibility And Debug UI Polish Design

## Scope

Implement a focused compatibility slice around command-mode `kubectl apply -f`
for manifest files and deployment rollout lifecycle commands, plus small debug
UI and documentation polish that reflects the resulting overlay state.

## Architecture

- Keep command parsing in `server_ops.py`; no new parser branch is needed
  because `apply` already maps to the manifest command shape.
- Add manifest-loading helpers near `_render_apply()` in `server_ops.py`:
  parse JSON through `json.loads`, parse YAML through optional PyYAML, normalize
  multi-document payloads into a list of Kubernetes-like mapping objects, and
  validate kind/name before applying.
- Reuse `_mutation_snapshot_kind()`, `_generic_resource_row()`, and
  `SimulationMutations.put_resource()` for supported resources. This keeps
  command mode, fake Kubernetes object rendering, `/v1/state`, and `/debug`
  aligned through the existing resource snapshot path.
- Keep dry-run apply side-effect free by rendering the same target list without
  calling mutation methods.
- Return `CommandResult` directly from `_render_apply()` so invalid manifests
  can be classified as partial with stable rule ids.
- Add deployment-scoped rollout lifecycle renderers near the existing rollout
  status/history/restart code. These commands should use
  `SimulationMutations.set_workload()` plus `record_event()` so later
  `resource_snapshot()`, `kubectl get deployments`, `kubectl rollout status`,
  and `kubectl get events` all observe the same state.
- Normalize rollout target parsing for both `deployment/name` and
  `deployment name`, and gate rollout lifecycle handling to deployment kinds so
  service or pod targets remain unsupported.

## Debug UI

- Preserve the inline debug shell architecture in `server_debug_ui.py`.
- Improve resource diff rows for created resources by showing namespace and a
  more descriptive apply/configured note from existing mutation summary data.
- Avoid adding debug-only state; consume `/v1/state` and `/v1/debug/resources`
  only.

## Compatibility

- Supported manifest kinds are limited to kinds already accepted by
  `_mutation_snapshot_kind()`.
- Unsupported documents should not mutate simulator state. If at least one
  document is valid but another is unsupported or malformed, the whole apply is
  reported as partial and no mutation is performed to avoid half-applied
  simulator state.
- Missing files preserve the current filename-derived fallback only when the
  path does not exist; this keeps existing command examples/tests working while
  letting real files drive richer behavior.
- `kubectl rollout pause`, `resume`, and `undo` are intentionally
  deployment-only. `undo --to-revision` is parsed for command realism but does
  not create a full revision-history model beyond the workload overlay and
  event stream.

## Docs

- Move `kubectl apply -f` multi-document coverage from future to recently
  covered in `docs/server-roadmap.md`.
- Move rollout pause/resume/undo from future to recently covered in
  `docs/server-roadmap.md`.
- Update README serve-mode compatibility prose to mention manifest-backed apply
  and the additional rollout verbs.
