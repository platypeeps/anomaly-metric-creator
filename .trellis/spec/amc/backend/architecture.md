# Architecture

## Package Shape

This repository is a single Python package that generates deterministic
observability artifacts and can serve those artifacts through an incident
simulator facade. The canonical generation implementation remains
`src/anomaly_metric_creator/legacy.py`; `anomaly-metric-creator.py`,
`src/anomaly_metric_creator/cli.py`, and small package facade modules are
wiring/import-stability surfaces, not behavior forks. Sources: `CLAUDE.md`;
`anomaly-metric-creator.py`; `src/anomaly_metric_creator/legacy.py`;
`src/anomaly_metric_creator/cli.py`; `src/anomaly_metric_creator/combine.py`;
`src/anomaly_metric_creator/models.py`; `src/anomaly_metric_creator/otel.py`;
`src/anomaly_metric_creator/scenarios.py`; `src/anomaly_metric_creator/schema.py`;
`src/anomaly_metric_creator/otel_stream.py`;
`src/anomaly_metric_creator/schema_impl.py`;
`src/anomaly_metric_creator/validate_impl.py`;
`pyproject.toml`.

Installed console scripts `amc` and `anomaly-metric-creator` dispatch through
`anomaly_metric_creator.cli:main`; source-checkout examples may use
`python3 anomaly-metric-creator.py`, but both paths must drive the same
behavior. Sources: `README.md`; `pyproject.toml`;
`src/anomaly_metric_creator/cli.py`; `anomaly-metric-creator.py`;
`tests/test_package_entrypoint.py`; `tests/test_cli_surface.py`.

## Generation Pipeline

Generation runs through `main()` and `generate_component()` with a
`RunContext` carrying per-run state and `np.random.RandomState(seed)`. Do not
reintroduce module-level mutable scenario state or module-level RNG flows.
Sources: `CLAUDE.md`; `src/anomaly_metric_creator/legacy.py`;
`tests/test_determinism.py`; `tests/test_correctness.py`;
`tests/test_scenarios.py`.

`generate_component()` is the vectorized hot path: build timestamp arrays,
draw natural metric values, apply anomaly overrides, recompute derived metrics,
round/cast, apply drops, and write per-component CSV rows. Keep row-scale work
vectorized and avoid per-row parsing, repeated file IO, or broad exception
probes that consume RNG. Sources: `CLAUDE.md`;
`src/anomaly_metric_creator/legacy.py`; `tests/test_correctness.py`;
`tests/test_gauges_file.py`; `tests/test_schema_file.py`.

Output directory cleanup, end-of-run summaries, validator-required files, and
writer paths must derive from the same registries instead of hand-written
parallel maps. Sources: `CLAUDE.md`; `src/anomaly_metric_creator/legacy.py`;
`README.md`; `docs/application-flow.md`; `tests/test_emit_selection_hygiene.py`;
`tests/test_validate_output.py`.

## Module Boundaries

Keep generation and registries in `legacy.py` unless a focused extraction
preserves the same public surface through facades. Focused modules extracted
so far through decomposition epic `07-02-legacy-monolith-decomposition`:
`redaction.py` (sensitive HTTP-header masking), `timeutil.py`
(CSV-timestamp parsing / unix-nano conversion), `otlp.py` (the `_build_otlp_*`
JSON/protobuf payload builders), `csv_layout.py` (shared per-component CSV
scan/iteration primitives + `_INSTANCE_DIMENSION_COLUMNS`), `gauges_impl.py`
(`write_gauges_csv`), `artifacts.py` (atomic-publication helpers),
`combine_impl.py` (wide + long-form combine writers), `schema_impl.py`
(schema.json writers), `validate_impl.py` (schema read-back / output
validation orchestration and `Violation`), `validate_cells.py` (cell,
derivation, and long-form dimension validation), `validate_topology.py`
(aggregate topology coupling validation), `validate_topology_instances.py`
(per-instance topology coupling validation), and `otel_stream.py` (OTEL HTTP
streaming). All are re-imported by
`legacy.py`, and the package facades (`combine.py`, `models.py`, `otel.py`,
`scenarios.py`, `schema.py`) preserve historic object identity. When an
extracted module must read a registry that still lives in `legacy.py`, configure
live callbacks from `legacy.py` and pass the current registry view into leaf
helpers rather than importing `legacy.py` from the extracted module or copying a
registry snapshot; this preserves monkeypatch-sensitive tests while keeping
dependency direction one-way. Output validation is the exception for persisted
topology shape: `validate_topology.py` must iterate and filter anomaly windows
against the `schema.json` topology snapshot because that is the graph used by
the artifacts being validated, while current load-metric name mapping may still
come from the live registry. Sources: `CLAUDE.md`;
`src/anomaly_metric_creator/legacy.py`; `src/anomaly_metric_creator/combine.py`;
`src/anomaly_metric_creator/otel.py`; `src/anomaly_metric_creator/schema.py`;
`src/anomaly_metric_creator/otel_stream.py`;
`src/anomaly_metric_creator/schema_impl.py`;
`src/anomaly_metric_creator/validate_impl.py`;
`src/anomaly_metric_creator/validate_cells.py`;
`src/anomaly_metric_creator/validate_topology.py`;
`src/anomaly_metric_creator/validate_topology_instances.py`;
`tests/test_package_facades.py`; `tests/test_validate_output.py`.

Keep `server.py` as the stdlib HTTP facade for `amc serve`. Lower-level server
behavior belongs in focused modules: `server_ops.py` for simulation state,
commands, Kubernetes objects, and Helm Secret encoding; `server_traces.py` for
command traces, JSONL/SQLite persistence, search, import/export, and
unsupported summaries; `server_mutations.py` for overlay state;
`server_debug_ui.py` for inline HTML/CSS/JS; `server_mcp.py` for the MCP
(Model Context Protocol) facade served at `POST /mcp` (stateless JSON-RPC
plus the read-only tool registry and the eval-mode ground-truth wall);
`server_commands.py`, `server_kubernetes.py`, and `server_helm.py` for focused
facades. `server.py` only routes the MCP request body; protocol behavior and
the import-time-validated `MCP_TOOLS` registry live in `server_mcp.py`.
Sources:
`CLAUDE.md`; `src/anomaly_metric_creator/server.py`;
`src/anomaly_metric_creator/server_ops.py`;
`src/anomaly_metric_creator/server_traces.py`;
`src/anomaly_metric_creator/server_mutations.py`;
`src/anomaly_metric_creator/server_debug_ui.py`;
`src/anomaly_metric_creator/server_mcp.py`;
`src/anomaly_metric_creator/server_commands.py`;
`src/anomaly_metric_creator/server_kubernetes.py`;
`src/anomaly_metric_creator/server_helm.py`; `tests/test_server.py`;
`tests/test_server_mcp.py`; `tests/test_server_eval_mode.py`.

Offline trace-bundle analysis belongs in `trace_bundle.py` and should import
search/unsupported helpers from `server_traces.py`, not from the HTTP facade, so
online and offline filtering remain aligned. Sources: `CLAUDE.md`;
`README.md`; `src/anomaly_metric_creator/trace_bundle.py`;
`src/anomaly_metric_creator/server_traces.py`; `tests/test_trace_bundle.py`;
`tests/test_server.py`.

## Server State Model

Serve mode is a runtime facade over generated artifacts and scenario profiles;
it must not copy generation behavior. Build `SimulationState` from parsed
generation args, generated artifacts, `SCENARIOS`, ops profiles, and the
simulated clock. Sources: `CLAUDE.md`; `README.md`;
`src/anomaly_metric_creator/server.py`; `src/anomaly_metric_creator/server_ops.py`;
`tests/test_server.py`.

Mutable simulator state is an overlay on top of baseline scenario profiles.
Scale, restart, delete, generic resource, event, and Helm release mutations
must layer through `SimulationMutations`; do not write command/UI-only state
back into frozen `Scenario` entries or generated CSV rows. Sources: `CLAUDE.md`;
`README.md`;
`src/anomaly_metric_creator/server_mutations.py`;
`src/anomaly_metric_creator/server_ops.py`; `tests/test_server.py`.

When adding a Kubernetes resource family, update aliases, snapshot kinds,
resource snapshots, renderers, API resource lists, object/table helpers,
mutation handling where relevant, trace classification, and focused server
coverage in one pass. Sources: `CLAUDE.md`;
`src/anomaly_metric_creator/server_ops.py`;
`src/anomaly_metric_creator/server_mutations.py`;
`src/anomaly_metric_creator/server.py`; `tests/test_server.py`.

## Topology and Instances

The topology graph is a directed service-call graph over a subset of
`COMPONENTS`; standalone components are driven by natural draws plus scenario
overrides, not by hidden topology edges. Sources: `docs/topology.md`;
`README.md`; `CLAUDE.md`; `src/anomaly_metric_creator/legacy.py`;
`tests/test_topology_registry.py`; `tests/test_topology_fanout.py`.

The single anonymous `Instance()` path preserves legacy wide CSV output; named
instances or `--instances-per-component N>1` switch per-component CSVs and
long-form artifacts to dimension-aware shapes. Sources: `README.md`;
`docs/application-flow.md`; `docs/topology.md`;
`src/anomaly_metric_creator/legacy.py`; `tests/test_instance_config.py`;
`tests/test_topology_multi_instance.py`; `tests/test_gauges_file.py`.

## Anti-Patterns

Do not copy behavior into shims or facades, hand-roll maps that duplicate
canonical registries, mutate frozen scenario data for UI state, add a second
Kubernetes state model, or let offline tooling drift from online trace search.
Sources: `CLAUDE.md`; `src/anomaly_metric_creator/legacy.py`;
`src/anomaly_metric_creator/server.py`;
`src/anomaly_metric_creator/server_traces.py`;
`src/anomaly_metric_creator/trace_bundle.py`; `tests/test_package_facades.py`;
`tests/test_server.py`; `tests/test_trace_bundle.py`.
