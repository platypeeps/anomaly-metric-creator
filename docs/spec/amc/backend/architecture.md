# Architecture

## Package Shape

This repository is a single Python package that generates deterministic
observability artifacts and can serve those artifacts through an incident
simulator facade. `src/anomaly_metric_creator/legacy.py` is the historic public
binding and live-runtime wiring surface; `run_pipeline.py` owns one-run
orchestration and focused modules own their named behavior.
`anomaly-metric-creator.py`, `src/anomaly_metric_creator/cli.py`, and small
package facade modules are wiring/import-stability surfaces, not behavior
forks. Sources:
`anomaly-metric-creator.py`; `src/anomaly_metric_creator/legacy.py`;
`src/anomaly_metric_creator/cli.py`; `src/anomaly_metric_creator/combine.py`;
`src/anomaly_metric_creator/models.py`;
`src/anomaly_metric_creator/models_impl.py`;
`src/anomaly_metric_creator/catalog.py`; `src/anomaly_metric_creator/otel.py`;
`src/anomaly_metric_creator/scenarios.py`; `src/anomaly_metric_creator/schema.py`;
`src/anomaly_metric_creator/cli_args.py`;
`src/anomaly_metric_creator/cli_argv_safety.py`;
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
Sources: `src/anomaly_metric_creator/legacy.py`;
`src/anomaly_metric_creator/run_pipeline.py`;
`src/anomaly_metric_creator/models_impl.py`;
`tests/test_determinism.py`; `tests/test_correctness.py`;
`tests/test_scenarios.py`.

`generate_component()` is the vectorized hot path: build timestamp arrays,
draw natural metric values, apply anomaly overrides, recompute derived metrics,
round/cast, apply drops, and write per-component CSV rows. Keep row-scale work
vectorized and avoid per-row parsing, repeated file IO, or broad exception
probes that consume RNG. Sources:
`src/anomaly_metric_creator/legacy.py`; `tests/test_correctness.py`;
`tests/test_gauges_file.py`; `tests/test_schema_file.py`.

Output directory cleanup, end-of-run summaries, validator-required files, and
writer paths must derive from the same registries instead of hand-written
parallel maps. Sources: `src/anomaly_metric_creator/legacy.py`;
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
generate-flag validation), `cli_argv_safety.py` (the value-safe
unrecognized-arguments error shared by the generate, `combine`, `validate`,
and `trace-bundle` parsers and the serve pass-through refusal; imports nothing from the package), `cli_subcommands.py` (dedicated `combine`,
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
behavior-module limit, and is enrolled as the one permanent exception in
`tools/check_module_size.py`. The moved generation and topology helpers use the same
callback pattern for `legacy.DERIVATIONS`, `legacy._format_fixed3`,
`legacy.TOPOLOGY`, `legacy._TOPOLOGY_LOAD_METRICS`, and
`legacy._TOPOLOGY_SATURATION_TARGETS`, with direct module callers falling back
to the canonical registries in their extracted homes. Output validation is the
exception for persisted topology shape: `validate_topology.py` must iterate and
filter anomaly windows against the `schema.json` topology snapshot because that
is the graph used by the artifacts being validated, while current load-metric
name mapping may still come from the live registry. Sources:
`src/anomaly_metric_creator/legacy.py`; `src/anomaly_metric_creator/combine.py`;
`src/anomaly_metric_creator/run_pipeline.py`;
`src/anomaly_metric_creator/run_defaults.py`;
`src/anomaly_metric_creator/otel.py`; `src/anomaly_metric_creator/schema.py`;
`src/anomaly_metric_creator/otel_stream.py`;
`src/anomaly_metric_creator/cli_args.py`;
`src/anomaly_metric_creator/cli_argv_safety.py`;
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

Keep `server.py` as the stdlib HTTP facade for `amc serve`. It republishes the
historic `anomaly_metric_creator.server` attribute surface through a module
`__getattr__` that forwards to `server_ops`, so **an extraction that publishes
a new ops name needs no edit in `server.py`** — the 227-line
`NAME = _server_ops.NAME` block that every step used to append to is gone. Two
rules hold that seam together, and both are enforced by
`tests/test_server_alias_surface.py` rather than by prose:

- Any `server_ops` name `server.py` reads as a **bare global** must still be
  imported explicitly (40 names today). PEP 562's module `__getattr__` answers
  attribute access on the module object and is never consulted for global-name
  resolution inside the module, so a delegated name read as a global fails with
  a `NameError` on one request path instead of at import.
- `__getattr__` refuses `__dunder__` names. `server_ops` defines `__all__` and
  `server.py` deliberately does not; forwarding it would silently change what
  `from anomaly_metric_creator.server import *` publishes. `__dir__` filters
  the delegated half through the *same* `_is_delegation_excluded` predicate:
  whatever a `__getattr__` guard refuses, the module's `__dir__` must not
  list, or `dir()` advertises attributes that reading raises on. Sharing one
  predicate rather than repeating the condition is what keeps the guard and
  the listing from drifting apart.

`server_ops` re-imports only the leaf names that `server.py`, the k8s/helm
facades, `server_mcp.py` or the tests read through it, and its `__all__`
lists the public command, state and REST entry points (23 names). No module
star-imports `server_ops`. A new caller of a leaf name imports it from its
leaf; add a `server_ops` re-import only for a name that must stay reachable
as `server.<name>`.

Lower-level server
behavior belongs in focused modules: `server_ops.py` for the command entry
points (`run_command`, `render_command`, the `_render_kubectl` dispatcher)
and the compatibility re-export surface, with the runtime state,
`resource_snapshot()`, the kubectl renderers, the snapshot-bound Kubernetes
resource builders and the REST dispatch spine in the `server_ops_*` /
`server_k8s_resources` / `server_k8s_dispatch` leaves below;
`server_ops_support.py` for the pure lower leaf shared downward by the ops and
k8s surfaces (`DEFAULT_RELEASE` / `DEFAULT_CHART`, the snapshot-row /
timestamp / string-coercion / list-resource-version accessors, and `_preview`,
the output-truncation helper moved down in the step-5 k8s-API extraction);
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
helpers), with the names callers read re-imported by `server_ops.py`
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
only by `server_ops`); `server_k8s_api.py` for the pure Kubernetes REST-facade
builder/filter/format leaf (`KubernetesApiResponse` + response builders,
discovery/`_k8s_api_resource_list` data builders, the structural non-snapshot
OpenAPI helpers, label/field/namespace filters, the pure watch helpers, the
non-snapshot mutation-parse helpers, request-body readers, and
`render_kubeconfig`), importing one-way from `server_mutations` /
`server_ops_parse` / `server_ops_support` /
`server_k8s_objects` with `SimulationState` under a `TYPE_CHECKING` guard;
`server_k8s_api_trace.py` for the sibling sink leaf carved off it for the
800-line cap (the `_api_*` fingerprint helpers, `_is_kubernetes_api_path`,
`_rate_limit_bucket`, and query/secret redaction), importing one-way from
`server_k8s_api` plus `server_ops_support._preview`. `server_ops_snapshot.py` owns the overlay-aware
`resource_snapshot()` and its runtime closure (snapshot kind sets, the
namespace and mutation-row appliers, component health/impact/event helpers,
`_event_rows`, `_node_rows`, `_replica_count`, `_pod_name`,
`_stable_cluster_ip`), importing one-way from `server_command_render` /
`server_helm_impl` / `server_k8s_api` / `server_k8s_objects` /
`server_mutations` / `server_ops_profiles` / `server_ops_support` with
`SimulationState` under a `TYPE_CHECKING` guard. `server_ops` re-imports every
name, so a caller left in `server_ops` still resolves `resource_snapshot` in
`server_ops`'s namespace, where `tests/test_server.py` monkeypatches it.
`server_ops_render.py` owns the read-only kubectl renderers (`get` with
`--watch` and `get all`, `describe`, the `logs` family, `top`, `version`,
`api-versions`, `api-resources`, `cluster-info`) and their row filters,
importing `resource_snapshot` one-way from `server_ops_snapshot` plus
`server_command_render` / `server_k8s_api` / `server_mutations` /
`server_ops_support`; a test that stubs the snapshot for these renderers
patches `server_ops_render.resource_snapshot`. The shared `_not_found` helper
lives in `server_command_render`, and `_find_named` / `_component_from_name`
in `server_ops_support`, so later render leaves reach them without importing
each other. `server_ops_render_manifest.py` owns `kubectl patch` (merge and
RFC 6902 JSON patch payloads), `diff`, `apply -f` and `create`, the
manifest-target resolution, and the generic resource-row builders; it imports
`resource_snapshot` from `server_ops_snapshot` and the one shared row filter
`_filter_snapshot_rows` from `server_ops_render` (read leaf below manifest
leaf, never the reverse), plus `server_command_render` / `server_k8s_api` /
`server_k8s_objects` / `server_mutations` / `server_ops_parse` /
`server_ops_payloads` / `server_ops_support`. `server_ops_render_workloads.py`
owns the workload operations (`scale`, `delete`, the six `rollout`
subcommands, `wait`, `exec`, `port-forward`) and their rollout and replica
helpers, including `_normalized_resource_prefix`; it imports
`resource_snapshot` from `server_ops_snapshot` and `_mutation_snapshot_kind`
from `server_ops_render_manifest` (manifest leaf below workloads leaf, never
the reverse), plus `server_command_render` / `server_mutations` /
`server_ops_parse` / `server_ops_support`. `server_k8s_resources.py` owns
the snapshot-bound Kubernetes resource builders: `kubectl explain`
(`_render_explain`, `_explain_schema_for_kind`,
`_EXPLAIN_RESOURCE_DESCRIPTIONS`), the `/openapi/v2` and `/openapi/v3`
document builders, `_minimal_k8s_object`, the `_k8s_objects_for_resource`
dispatcher and `_k8s_endpointslice`. It imports `resource_snapshot` from
`server_ops_snapshot` plus `server_command_render` / `server_helm_impl` /
`server_k8s_api` / `server_k8s_objects` / `server_ops_explain` /
`server_ops_parse` / `server_ops_support`; a test that stubs the snapshot
for these builders patches `server_k8s_resources.resource_snapshot`.
`server_k8s_dispatch.py` owns the REST dispatch spine
(`kubernetes_api_response`, `kubernetes_api_post_response`,
`kubernetes_api_mutating_response`, the core/group/per-resource response
builders, `_k8s_mutated_object`, `k8s_watch_objects`,
`record_kubernetes_api_call`), importing one-way from `server_k8s_resources`,
the `server_ops_render*` leaves, `server_ops_snapshot`, `server_k8s_api`,
`server_k8s_api_trace`, `server_k8s_objects`, `server_k8s_tables`,
`server_mutations`, `server_ops_support` and `server_traces`. `server.py`
calls `kubernetes_api_post_response` / `kubernetes_api_mutating_response` as
bare globals, so a test that stubs them patches `server.<name>`.
`server_ops_state.py` owns the runtime state: `SimulationState` and
`build_state()`, `SimulationClock`, `ContinuousGenerationStatus`,
`RefusalCounters`, `load_anomaly_rows()`, and the operator error sink
(`_record_server_error`, its traceback helpers and
`_record_continuous_generation_failure`). It imports only stdlib plus
`server_mutations` / `server_ops_profiles` / `server_ops_support` /
`server_traces`. The leaves that name `SimulationState` only in annotations
keep their `TYPE_CHECKING` import from `server_ops`, which re-exports it. Also out: `server_ops_explain.py` for the ten pure `kubectl explain` /
OpenAPI schema formatters (`_openapi_schema_from_value`, `_explain_schema_at_path`,
`_format_explain` and its recursive-field/type-label helpers) — the only leaf in
the package with no intra-package import at all — while the state-bound
`_render_explain` / `_explain_schema_for_kind` in `server_k8s_resources`
call into it;
and `server_ops_payloads.py` for declarative request-payload handling (the
RFC 6902 JSON Patch `_apply_json_patch` plus its RFC 6901 JSON Pointer
`_json_pointer_parts` / `_set_json_pointer` / `_remove_json_pointer`
operations, and the `_load_manifest_documents` /
`_normalize_manifest_documents` reader), importing one-way from
`server_command_render` for `CommandResult` only. `server_traces.py` for
command traces, JSONL/SQLite persistence, search, import/export, and
unsupported summaries; `server_mutations.py` for overlay state;
`server_config.py` for `--config` loading, validation, and serve-arg parsing
(the `_load_serve_config` / `_config_mapping_to_argv` / `_probe_config_generate_argv`
/ `_vouch_no_flag_generate_keys` / `_parse_serve_args` cluster), a leaf that
imports nothing from `server.py` and reaches the generate parser only through a
call-time `legacy` import;
`server_debug_ui.py` for inline HTML/CSS/JS; `server_mcp.py` for the MCP
(Model Context Protocol) facade served at `POST /mcp` (stateless JSON-RPC
plus the read-only tool registry and the eval-mode ground-truth wall);
`server_commands.py`, `server_kubernetes.py`, and `server_helm.py` for focused
facades. `server.py` only routes the MCP request body; protocol behavior and
the import-time-validated `MCP_TOOLS` registry live in `server_mcp.py`.
Sources:
`src/anomaly_metric_creator/server.py`;
`src/anomaly_metric_creator/server_ops.py`;
`src/anomaly_metric_creator/server_ops_support.py`;
`src/anomaly_metric_creator/server_ops_snapshot.py`;
`src/anomaly_metric_creator/server_ops_render.py`;
`src/anomaly_metric_creator/server_ops_render_manifest.py`;
`src/anomaly_metric_creator/server_ops_render_workloads.py`;
`src/anomaly_metric_creator/server_k8s_resources.py`;
`src/anomaly_metric_creator/server_k8s_dispatch.py`;
`src/anomaly_metric_creator/server_ops_state.py`;
`src/anomaly_metric_creator/server_k8s_objects.py`;
`src/anomaly_metric_creator/server_k8s_tables.py`;
`src/anomaly_metric_creator/server_ops_profiles.py`;
`src/anomaly_metric_creator/server_ops_parse.py`;
`src/anomaly_metric_creator/server_ops_explain.py`;
`src/anomaly_metric_creator/server_ops_payloads.py`;
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
online and offline filtering remain aligned. Sources:
`README.md`; `src/anomaly_metric_creator/trace_bundle.py`;
`src/anomaly_metric_creator/server_traces.py`; `tests/test_trace_bundle.py`;
`tests/test_server.py`.

## Server State Model

Serve mode is a runtime facade over generated artifacts and scenario profiles;
it must not copy generation behavior. Build `SimulationState` from parsed
generation args, generated artifacts, `SCENARIOS`, ops profiles, and the
simulated clock. Sources: `README.md`;
`src/anomaly_metric_creator/server.py`; `src/anomaly_metric_creator/server_ops.py`;
`tests/test_server.py`.

Mutable simulator state is an overlay on top of baseline scenario profiles.
Scale, restart, delete, generic resource, event, and Helm release mutations
must layer through `SimulationMutations`; do not write command/UI-only state
back into frozen `Scenario` entries or generated CSV rows. Sources:
`README.md`;
`src/anomaly_metric_creator/server_mutations.py`;
`src/anomaly_metric_creator/server_ops.py`; `tests/test_server.py`.

When adding a Kubernetes resource family, update aliases, snapshot kinds,
resource snapshots, renderers, API resource lists, object/table helpers,
mutation handling where relevant, trace classification, and focused server
coverage in one pass. Sources:
`src/anomaly_metric_creator/server_ops.py`;
`src/anomaly_metric_creator/server_mutations.py`;
`src/anomaly_metric_creator/server.py`; `tests/test_server.py`.

## Topology and Instances

The topology graph is a directed service-call graph over a subset of
`COMPONENTS`; standalone components are driven by natural draws plus scenario
overrides, not by hidden topology edges. Sources: `docs/topology.md`;
`README.md`; `src/anomaly_metric_creator/legacy.py`;
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
Sources: `src/anomaly_metric_creator/legacy.py`;
`src/anomaly_metric_creator/server.py`;
`src/anomaly_metric_creator/server_traces.py`;
`src/anomaly_metric_creator/trace_bundle.py`; `tests/test_package_facades.py`;
`tests/test_server.py`; `tests/test_trace_bundle.py`.
