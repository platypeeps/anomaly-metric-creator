# Architecture

## Package Shape

This repository is a single Python package that generates deterministic
observability artifacts and can serve those artifacts through an incident
simulator facade. `src/anomaly_metric_creator/legacy.py` is the historic public
binding and live-runtime wiring surface; `run_pipeline.py` owns one-run
orchestration and focused modules own their named behavior.
`anomaly-metric-creator.py`, `src/anomaly_metric_creator/cli.py`, and small
package facade modules are wiring/import-stability surfaces, not behavior
forks. Sources: `CLAUDE.md`;
`anomaly-metric-creator.py`; `src/anomaly_metric_creator/legacy.py`;
`src/anomaly_metric_creator/cli.py`; `src/anomaly_metric_creator/combine.py`;
`src/anomaly_metric_creator/models.py`;
`src/anomaly_metric_creator/models_impl.py`;
`src/anomaly_metric_creator/catalog.py`; `src/anomaly_metric_creator/otel.py`;
`src/anomaly_metric_creator/scenarios.py`; `src/anomaly_metric_creator/schema.py`;
`src/anomaly_metric_creator/cli_args.py`;
`src/anomaly_metric_creator/cli_subcommands.py`;
`src/anomaly_metric_creator/version.py`;
`src/anomaly_metric_creator/run_pipeline.py`;
`src/anomaly_metric_creator/run_defaults.py`;
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

Generation enters through `legacy.main()`, delegates to `run_pipeline.main()`,
and reaches `generation.generate_component()` with a `models_impl.RunContext`
carrying per-run state and `np.random.RandomState(seed)`. Do not reintroduce
module-level mutable scenario state or module-level RNG flows.
Sources: `CLAUDE.md`; `src/anomaly_metric_creator/legacy.py`;
`src/anomaly_metric_creator/run_pipeline.py`;
`src/anomaly_metric_creator/models_impl.py`;
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

Keep the `main()` wrapper and compatibility bindings in `legacy.py`;
`run_pipeline.py` owns run-level orchestration/artifact lifecycle,
`models_impl.py` owns `RunContext`, and focused generation, topology, scenario,
and artifact owners live in dedicated modules. `legacy.py` re-exports the
historic public surface and configures live runtime views.
Focused modules extracted so far through decomposition epic
`07-02-legacy-monolith-decomposition`:
`redaction.py` (sensitive HTTP-header masking), `timeutil.py`
(CSV-timestamp parsing / unix-nano conversion), `runtime_defaults.py` (shared
timestamp/day defaults), `otlp.py` (the `_build_otlp_*` JSON/protobuf payload
builders), `csv_layout.py` (shared per-component CSV
scan/iteration primitives + `_INSTANCE_DIMENSION_COLUMNS`), `gauges_impl.py`
(`write_gauges_csv`), `artifacts.py` (atomic-publication helpers),
`combine_impl.py` (wide + long-form combine writers), `schema_impl.py`
(schema.json writers), `validate_impl.py` (schema read-back / output
validation orchestration and `Violation`), `validate_cells.py` (cell,
derivation, and long-form dimension validation), `validate_topology.py`
(aggregate topology coupling validation), `validate_topology_instances.py`
(per-instance topology coupling validation), `otel_stream.py` (OTEL HTTP
streaming), `cli_args.py` (parser construction, CLI reconciliation, and
generate-flag validation), `cli_subcommands.py` (dedicated `combine`,
`validate`, `serve`, and `trace-bundle` subcommand dispatch helpers),
`version.py` (installed-distribution version discovery with caller-owned
source-tree fallbacks),
`models_impl.py` (`MetricSpec`, `Instance`, `RunContext`,
`_validate_instance_list`, and `_load_instance_config`), `run_defaults.py`
(generation-command defaults and anomaly-count salt), `run_pipeline.py`
(one-run orchestration, reporting artifacts, emitted-file registry, and output
hygiene), `catalog.py` (`COMPONENTS`, `INSTANCES`,
`DEFAULT_METRICS_PER_COMPONENT`, metric caps, catalog seasonality helpers, and
catalog/instance metadata validator implementations), `scenario_builders.py`
(`Scenario`, `register_cascade`, and deterministic scenario-spec builders),
`scenario_catalog.py` (the single ordered declarative `SCENARIOS` registry),
`scenario_validation.py` (scenario/spec validators with explicit inputs),
`scenarios_impl.py` (selection, signal/count filtering, and composition with a
live registry callback), `anomaly_dispatch.py`
(shape vocabulary and generator-arity dispatch), `generation.py`
(`generate_component` and live generation callbacks),
`generation_derivations.py` (derived-metric recomputation registry),
`generation_helpers.py` (`_natural_column` and instance-filter helpers),
`generation_emit.py` (CSV row formatting, DST splice, and timestamp-array
helpers), `topology_models.py` (`Edge` and `SaturationParams`),
`topology_registry.py` (topology metric registries and tuning constants),
`topology_impl.py` (`TOPOLOGY`, callback runtime, topology generation order, and
topology validators), `topology_compose.py` (aggregate coupling and saturation
composition), `topology_instances.py` (per-instance topology composition), and
`topology_support.py` (shared saturation/equality helpers). All are
re-imported by `legacy.py`, and the package facades (`combine.py`, `models.py`, `otel.py`,
`scenarios.py`, `schema.py`) preserve historic object identity. `models.py`
imports `MetricSpec`, `Instance`, and `RunContext` from `models_impl.py`; `Edge` and
`SaturationParams` are imported by `legacy.py` from `topology_impl.py`, while
`legacy.py` re-exports the canonical `RunContext`. When an
extracted module must read a registry that still lives in `legacy.py`, configure
named, weak-referenceable live callbacks from `legacy.py` and pass the current
registry view into leaf helpers rather than importing `legacy.py` from the
extracted module or copying a registry snapshot; this preserves
monkeypatch-sensitive tests without retaining isolated legacy module copies, and
keeps dependency direction one-way. The moved model/catalog readers use those
callbacks for patched `legacy.COMPONENTS` and `legacy.INSTANCES`; `legacy.py`
still invokes catalog metadata validation at the historical import-time call
site, while `catalog.py` remains the source of truth for the shipped component
and instance registries. The moved scenario readers use the same pattern for
patched `legacy.SCENARIOS`; the sole import-time scenario-validation call stays
at its historical `legacy.py` site, and `scenario_catalog.py` is intentionally
one ordered data-only registry even though it exceeds the normal 800-line
behavior-module limit. The moved generation and topology helpers use the same
callback pattern for `legacy.DERIVATIONS`, `legacy._format_fixed3`,
`legacy.TOPOLOGY`, `legacy._TOPOLOGY_LOAD_METRICS`, and
`legacy._TOPOLOGY_SATURATION_TARGETS`, with direct module callers falling back
to the canonical registries in their extracted homes. Output validation is the
exception for persisted topology shape: `validate_topology.py` must iterate and
filter anomaly windows against the `schema.json` topology snapshot because that
is the graph used by the artifacts being validated, while current load-metric
name mapping may still come from the live registry. Sources: `CLAUDE.md`;
`src/anomaly_metric_creator/legacy.py`; `src/anomaly_metric_creator/combine.py`;
`src/anomaly_metric_creator/run_pipeline.py`;
`src/anomaly_metric_creator/run_defaults.py`;
`src/anomaly_metric_creator/otel.py`; `src/anomaly_metric_creator/schema.py`;
`src/anomaly_metric_creator/otel_stream.py`;
`src/anomaly_metric_creator/cli_args.py`;
`src/anomaly_metric_creator/cli_subcommands.py`;
`src/anomaly_metric_creator/models_impl.py`;
`src/anomaly_metric_creator/catalog.py`;
`src/anomaly_metric_creator/scenario_builders.py`;
`src/anomaly_metric_creator/scenario_catalog.py`;
`src/anomaly_metric_creator/scenario_validation.py`;
`src/anomaly_metric_creator/scenarios_impl.py`;
`src/anomaly_metric_creator/anomaly_dispatch.py`;
`src/anomaly_metric_creator/generation.py`;
`src/anomaly_metric_creator/generation_derivations.py`;
`src/anomaly_metric_creator/generation_helpers.py`;
`src/anomaly_metric_creator/generation_emit.py`;
`src/anomaly_metric_creator/topology_models.py`;
`src/anomaly_metric_creator/topology_registry.py`;
`src/anomaly_metric_creator/topology_impl.py`;
`src/anomaly_metric_creator/topology_compose.py`;
`src/anomaly_metric_creator/topology_instances.py`;
`src/anomaly_metric_creator/topology_support.py`;
`src/anomaly_metric_creator/schema_impl.py`;
`src/anomaly_metric_creator/validate_impl.py`;
`src/anomaly_metric_creator/validate_cells.py`;
`src/anomaly_metric_creator/validate_topology.py`;
`src/anomaly_metric_creator/validate_topology_instances.py`;
`tests/test_package_facades.py`; `tests/test_validate_output.py`.

Keep `server.py` as the stdlib HTTP facade for `amc serve`. Lower-level server
behavior belongs in focused modules: `server_ops.py` for simulation state,
command rendering, the `_k8s_objects_for_resource` / `_k8s_table` dispatchers,
`resource_snapshot()`, and Helm Secret encoding;
`server_ops_support.py` for the pure lower leaf shared downward by the ops and
k8s surfaces (`DEFAULT_RELEASE` / `DEFAULT_CHART` and the snapshot-row /
timestamp / string-coercion / list-resource-version accessors);
`server_k8s_objects.py` for the per-kind Kubernetes object builders plus the
metadata / owner / label / container-state / pod-timestamp / pod-ip helpers
(also the home of `_k8s_metadata` / `_k8s_timestamp` that the
`server_helm_impl` leaf depends on); `server_k8s_tables.py` for the
`meta.k8s.io/v1` Table surface (`_k8s_table`, `_k8s_column`,
`_k8s_table_schema`, and the per-kind cell builders); the two k8s leaves import
their shared accessors from `server_ops_support` and reference `SimulationState`
only under an `if TYPE_CHECKING` guard, so the runtime dependency stays one-way;
`server_ops_profiles.py` for the pure-data ops scenario-profile registry
(`OPS_SCENARIO_PROFILES`, its `OpsComponentImpact` / `OpsScenarioProfile`
dataclasses, `_impact` / `_profile` builders, and `validate_ops_profiles`);
`server_ops_parse.py` for the stdlib-only client-command parse cluster
(`ParsedCommand`, the flag/alias tables, `parse_command` and its
`_parse_kubectl` / `_parse_helm` family sub-parsers, and the
`command_fingerprint` / `guess_intent` / `_redact_*` fingerprint/redaction
helpers), each re-imported by `server_ops.py` at the original block position
(one-way import, no reverse dependency); `server_command_render.py` for the
`CommandResult` return dataclass and the general render/command primitives
`_table` / `_is_dry_run` / `_unsupported` / `_exposed_active_scenarios` shared by
the command renderers and the `server_helm_impl` leaf (a pure sibling
leaf importing only `ParsedCommand` from `server_ops_parse` and re-exporting the
byte-identical `_format_dt` from `server_mutations`, with `SimulationState` under
a `TYPE_CHECKING` guard); `server_helm_impl.py` for the top helm leaf (the 20
helm renderers, release/notes model, and double-base64 gzip Secret encoders,
importing one-way from `server_command_render`, `server_k8s_objects`,
`server_mutations`, `server_ops_parse`, and `server_ops_support`, and imported
only by `server_ops`); `server_traces.py` for
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
`src/anomaly_metric_creator/server_ops_support.py`;
`src/anomaly_metric_creator/server_k8s_objects.py`;
`src/anomaly_metric_creator/server_k8s_tables.py`;
`src/anomaly_metric_creator/server_ops_profiles.py`;
`src/anomaly_metric_creator/server_ops_parse.py`;
`src/anomaly_metric_creator/server_command_render.py`;
`src/anomaly_metric_creator/server_helm_impl.py`;
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
