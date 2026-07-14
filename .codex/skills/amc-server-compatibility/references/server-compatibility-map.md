# AMC Server Compatibility Map

Use this reference after `amc-server-compatibility` triggers and the task touches
server-mode command/API compatibility. Keep changes backed by current source,
tests, README, Trellis specs, and active Trellis task PRDs.

## Source Owners

| Surface | Primary Owner | Tests |
| --- | --- | --- |
| Command parsing and rendering | `src/anomaly_metric_creator/server_ops.py` | `tests/test_server.py` |
| Fake Kubernetes HTTP facade | `src/anomaly_metric_creator/server_kubernetes.py`, `src/anomaly_metric_creator/server_ops.py` | `tests/test_server.py` |
| Helm facade and release Secrets | `src/anomaly_metric_creator/server_helm.py`, `src/anomaly_metric_creator/server_ops.py` | `tests/test_server.py` |
| Mutable overlay | `src/anomaly_metric_creator/server_mutations.py`, `resource_snapshot()` in `server_ops.py` | `tests/test_server.py` |
| Command traces/search/export | `src/anomaly_metric_creator/server_traces.py`, `src/anomaly_metric_creator/trace_bundle.py` | `tests/test_server.py`, `tests/test_trace_bundle.py` |
| Debug UI shell | `src/anomaly_metric_creator/server_debug_ui.py`, endpoints in `server.py` | `tests/test_server.py` |
| CLI docs/help | `src/anomaly_metric_creator/cli.py`, `src/anomaly_metric_creator/legacy.py`, `README.md` | `tests/test_cli.py`, `tests/test_cli_surface.py` |

## Compatibility Invariants

- Back Kubernetes-looking state with `resource_snapshot()` plus
  `SimulationMutations`; never add a second in-memory Kubernetes model.
- Keep scenario profiles as the source of incident-shaped baseline health,
  events, logs, rollout notes, and Helm notes.
- Keep mutations layered on top of baseline state and visible through command
  mode, fake Kubernetes API responses, `/v1/state`, and debug UI summaries.
- Keep unsupported or partially supported paths traceable instead of silent.
- Keep trace import/export/search semantics shared between live server endpoints
  and offline `amc trace-bundle` tooling.
- Keep Helm Secret payloads simulator JSON encoded in Helm-shaped Secret
  objects; do not treat them as native Helm protobuf releases.
- Keep debug UI data sourced from the same endpoints and stores as the API,
  not from browser-only state.

## Adding A Kubectl Command

1. Update `_parse_kubectl()` only when the existing generic parse shape cannot
   represent the command.
2. Add rendering in `_render_kubectl()` and keep stdout/stderr close enough for
   common operator workflows.
3. Use existing helpers such as `_normalize_kind()`, `_split_resource_token()`,
   `_filter_snapshot_rows()`, `_resource_prefix()`, and `_unsupported()`.
4. Return an explicit `CommandResult` with stable `matched_rule_id`.
5. Add tests for the supported path, an unsupported nearby path, and any partial
   flag behavior.
6. Update README supported-command prose and the relevant Trellis task PRD when
   compatibility status changes.

## Adding A Kubernetes API Resource Or Path

1. Start from discovery: `_k8s_api_resource_list()` and the group/core response
   routing must advertise only paths the simulator can answer.
2. Project from `resource_snapshot()` rows into Kubernetes objects and Table
   rows. Prefer extending existing object/table helpers.
3. If the path mutates state, route through `SimulationMutations` and verify
   the change appears in later command and API snapshots.
4. Keep unsupported methods/subresources rejected unless explicitly modeled.
5. Record real-client calls through `record_kubernetes_api_call()` with useful
   fingerprints and redacted query data.

## Adding Helm Compatibility

1. Keep release history and values changes in the mutation overlay.
2. Ensure `helm list/status/history/get` and Helm-shaped Secret API views agree.
3. For new flags such as `--wait`, `--timeout`, `--atomic`, `--reuse-values`, or
   `--reset-values`, model only behavior that affects simulator state or output;
   otherwise return partial support with a clear warning.
4. Add tests for command output, release revision state, and API-visible Secret
   payloads when relevant.

## Debug UI And Trace Changes

1. Add data to backend endpoints first; make the debug UI consume that data.
2. Keep command trace export/import and offline trace-bundle behavior aligned
   when adding trace fields or filters.
3. Redact secrets before traces, logs, SQLite rows, JSONL, exports, or the debug
   UI can see them.
4. Test endpoint payloads and search/filter behavior; avoid relying only on
   visual inspection of inline HTML/JS.

## Common Checks

```bash
rg -n "matched_rule_id|support_status|fingerprint" src/anomaly_metric_creator tests/test_server.py
.venv/bin/pytest tests/test_server.py -q
git diff --check
```

Use full `.venv/bin/pytest` before publishing broad compatibility work.
