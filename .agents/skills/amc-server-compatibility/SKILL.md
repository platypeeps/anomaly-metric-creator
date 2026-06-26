---
name: amc-server-compatibility
description: "Guide AMC server-mode Kubernetes, Helm, command-trace, mutation-overlay, and debug-UI compatibility work. Use when implementing or reviewing `amc serve`, fake Kubernetes API paths, `kubectl`/Helm command rendering, `resource_snapshot()` or `SimulationMutations` behavior, command trace/search/export behavior, or server-mode roadmap items in `docs/server-roadmap.md`."
---

# AMC Server Compatibility

Use this skill to keep server-mode compatibility changes small, tested, and
aligned with the simulator architecture. The main rule: add compatibility only
when it can be backed by `resource_snapshot()` or `SimulationMutations`; do not
create a second Kubernetes state model.

## Start Here

1. Run the Trellis pre-dev context flow before editing:

   ```bash
   python3 ./.trellis/scripts/get_context.py --mode packages
   ```

2. Read the relevant specs for the slice:
   - `.trellis/spec/amc/backend/api-cli-server.md` for CLI, command API, Kubernetes API, Helm, trace bundles.
   - `.trellis/spec/amc/backend/operations-security-logging.md` for command traces, persistence, redaction, request logging, auth, CORS, rate limits, debug UI.
   - `.trellis/spec/amc/backend/architecture.md` for module boundaries.
   - `.trellis/spec/amc/backend/testing-quality.md` for test and CI expectations.
   - `.trellis/spec/guides/code-reuse-thinking-guide.md` and `.trellis/spec/guides/cross-layer-thinking-guide.md` when adding parser branches, payload fields, helper maps, resource families, or trace formats.

3. Read `references/server-compatibility-map.md` when the task touches server
   command rendering, fake Kubernetes API behavior, Helm compatibility, debug
   UI data, or mutation state.

## Implementation Workflow

1. Classify the surface:
   - Command mode: `server_ops.py` parser/renderer via `run_command()`.
   - Kubernetes API facade: `server_kubernetes.py` facade plus `server_ops.py` API object helpers.
   - Helm facade: `server_helm.py` facade plus `server_ops.py` Helm release/revision helpers.
   - Trace/search/debug: `server_traces.py`, `trace_bundle.py`, `server.py`, `server_debug_ui.py`.
   - Mutable state: `server_mutations.py` plus `resource_snapshot()` projection.

2. Search before adding:

   ```bash
   rg -n "verb|resource|matched_rule_id|resource_snapshot|SimulationMutations|CommandTrace" src/anomaly_metric_creator tests/test_server.py
   ```

3. Extend the existing owner instead of creating parallel maps. For new
   resource behavior, update aliases, snapshot rows, table/object renderers,
   API discovery, mutation overlay handling, trace classification, README/docs,
   and focused tests in the same change.

4. Preserve trace visibility. Every supported, partial, and unsupported command
   or API path should produce a useful `support_status`, `matched_rule_id`, and
   fingerprint so `/debug`, `/v1/debug/search`, and trace-bundle tooling stay
   useful.

5. Prefer narrow compatibility. If the real client supports a broad feature,
   implement the smallest simulator-backed subset and mark nearby unmodeled
   flags or paths as partial/unsupported rather than pretending complete support.

## Validation

Use the narrowest meaningful checks first:

```bash
.venv/bin/pytest tests/test_server.py -q
git diff --check
```

For trace-bundle or CLI surfaces, add the matching focused tests:

```bash
.venv/bin/pytest tests/test_trace_bundle.py tests/test_cli.py -q
```

Run the full suite before publishing broad server compatibility work:

```bash
.venv/bin/pytest
```

If real `kubectl` or Helm behavior is the point of the change and the local
environment has current clients, consider:

```bash
AMC_RUN_REAL_CLIENT_SMOKE=1 .venv/bin/pytest tests/test_server.py -q
```

Report skipped real-client smoke tests explicitly.
