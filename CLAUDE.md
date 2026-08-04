# CLAUDE.md

Expanded historical/source guide for the anomaly metric creator. Canonical
development conventions now live in `.trellis/spec/amc/backend/index.md`; update
the focused Trellis spec first when a durable rule changes. The canonical
runtime entrypoint remains `src/anomaly_metric_creator/legacy.py`, but it is a
compatibility and live-runtime wiring facade: `run_pipeline.py` owns the
`main()` orchestration body and run-level artifact lifecycle, while
`models_impl.py` owns `RunContext`. Canonical scenario
builders/data/validation/runtime behavior
lives in `scenario_builders.py`, `scenario_catalog.py`,
`scenario_validation.py`, and `scenarios_impl.py`.
The top-level `anomaly-metric-creator.py` is a thin compatibility shim that
re-exports it and runs `main()`, and the installed `amc` /
`anomaly-metric-creator` console scripts dispatch through
`anomaly_metric_creator.cli`. Edit the focused implementation module for
behavior changes (`generation.py`, `topology_impl.py`, `topology_compose.py`,
and other extracted modules own their named surfaces) — the shim and `cli.py`
are wiring only, so
`python anomaly-metric-creator.py …`, a `pip install .` console script, and the test
suite all drive the same code. User-facing usage, install, CLI reference, output files,
and the anomaly catalog live in [README.md](README.md). Read it first if you need to run
the script or understand the failure modes it injects.
Small package facade modules (`combine.py`, `models.py`, `otel.py`,
`scenarios.py`, `schema.py`) are import-stability points for focused surfaces,
not parallel behavior copies. They re-export from extracted implementations
where a focused surface exists (`combine.py`→`combine_impl.py`,
`otel.py`→`otel_stream.py`, `scenarios.py`→`scenario_builders.py`/
`scenario_catalog.py`, `schema.py`→`schema_impl.py`/`validate_impl.py`,
and `models.py`→`models_impl.py` for `MetricSpec`/`Instance`/`RunContext` while
`Edge`, `RunContext`, and `SaturationParams` stay exposed through `legacy.py`
for identity compatibility; `Edge` and `SaturationParams` originate in
`topology_impl.py`). The schema
facade imports the focused schema/validator implementations after loading
`legacy.py` for live registry wiring, so object identity with the historic
`legacy.<name>` surface remains stable.
The `07-02-legacy-monolith-decomposition` split now leaves `legacy.py` as the
sub-800-line public facade; boundaries and sequencing are recorded in the epic
and its child designs. Extraction
pattern: code moves **verbatim** to a new module, `legacy.py` re-imports every
moved name at the same conceptual location so the historic `legacy.<name>`
surface (shim, facades, tests, `state.legacy` lookups) is unchanged, and new
modules never import `legacy` (one-way dependency). Extracted so far:
`redaction.py` (sensitive HTTP-header masking for OTEL transport
diagnostics), `timeutil.py` (CSV-timestamp parsing + unix-nano conversion,
shared by the merge writers, OTLP builders, and `server_mcp`),
`runtime_defaults.py` (`START` and `SECONDS_PER_DAY` shared by legacy and
extracted generation helpers), and `otlp.py` (the eight `_build_otlp_*`
JSON/protobuf payload builders plus
`_anomaly_event_id`; protobuf imports stay lazy per-function),
`csv_layout.py` (the shared per-component CSV header-scan / row-iteration
primitives — `_scan_component_csv_headers`, `_iter_component_rows`,
`_iter_component_instance_rows`, `_scan_instance_block_layout`,
`_ensure_long_form_fd_capacity`, `_classify_component_csv_header` — the
`_INSTANCE_DIMENSION_COLUMNS` column-order constant, and
`write_long_form_merge`, the single long-form `heapq.merge` writer shared by
both file writers (`write_gauges_csv` and `_write_combined_long_form`)
after 07-06-long-form-merge-writer-dedupe; consumed by the
gauge writer, the combine long-form writer, the OTEL streamer, and
`server_mcp`), `gauges_impl.py` (`write_gauges_csv`), `artifacts.py`
(the `_atomic_artifact_open` / `_atomic_write_text` / `_ATOMIC_TMP_SUFFIX`
publication helpers — landed with step 3 because `gauges_impl` depends on
them), and `combine_impl.py` (the wide + long-form combine writers,
`discover_components`, `_wide_component_rows_are_monotonic`, and the
`_NON_COMPONENT_FILES` / `_COMBINE_OUTPUT_FILENAME` constants),
`schema_impl.py` (`SCHEMA_DOCUMENT_VERSION`, schema document serializers,
topology snapshot serialization, and `write_schema_json`),
`validate_impl.py` (schema read-back shape validation, file-set / row-count /
timestamp orchestration, `Violation`, and `validate_output`),
`validate_cells.py` (cell/range checks, derivation recomputers, and long-form
dimension checks), `validate_topology.py` (aggregate topology coupling),
`validate_topology_instances.py` (per-instance topology coupling),
`otel_stream.py` (`stream_otel_signals`,
`stream_otel_gauges`, `_write_activity`, `_verbose_body_repr`, and
`_http_error_activity_fields`), `cli_args.py` (`parse_args`,
`_parse_start_time_arg`, `_reconcile_cli_surface`, `_ADVANCED_DESTS`, and
generate-flag validation), and `cli_subcommands.py`
(`_SUBCOMMANDS`, `_main_combine_subcommand`, `_main_validate_subcommand`,
`_main_serve_subcommand`, and `_main_trace_bundle_subcommand`),
`version.py` (`package_version`, the shared installed-distribution version
lookup with caller-owned source-tree fallbacks),
`models_impl.py` (`MetricSpec`, `Instance`, `RunContext`,
`_validate_instance_list`, and `_load_instance_config`), `run_defaults.py`
(generation-command defaults and the anomaly-count salt), `run_pipeline.py`
(`main()` orchestration, reporting artifacts, emitted-file collection, and
output hygiene), and `catalog.py` (`COMPONENTS`, `INSTANCES`,
`DEFAULT_METRICS_PER_COMPONENT`, metric caps, catalog seasonality helpers, and
catalog/instance metadata validator implementations), `scenario_builders.py`
(`Scenario`, `register_cascade`, and deterministic scenario-spec builders),
`scenario_catalog.py` (the single ordered declarative `SCENARIOS` registry),
`scenario_validation.py` (spec/registry validation against explicit inputs),
`scenarios_impl.py` (selection, signal/count filtering, and runtime composition
through a live registry callback), `anomaly_dispatch.py`
(`_VALID_ANOMALY_SHAPES`, anomaly value resolution, generator metadata caching,
and step/span dispatch), `generation.py` (`generate_component` and live
generation callbacks), `generation_derivations.py` (`DERIVATIONS`,
`DERIVED_METRICS`, and `_derive_cacheservice`), `generation_helpers.py`
(`_natural_column` and instance-filter helpers), `generation_emit.py` (CSV row
formatting, DST splice, and timestamp-array helpers), `topology_models.py`
(`Edge` and `SaturationParams`), `topology_registry.py` (topology metric
registries and tuning constants), `topology_impl.py` (`TOPOLOGY`, callback
runtime, generation order, and topology validators), `topology_compose.py`
(aggregate coupling/saturation composition), `topology_instances.py`
(per-instance topology composition), and `topology_support.py` (shared
saturation/equality helpers).
`schema_impl.py`, `cli_args.py`, and the validator orchestrator access live
registries through callbacks configured by `legacy.py`; `cli_args.py` refreshes
live `COMPONENTS`, `SCENARIOS`, and `DEFAULT_METRICS_PER_COMPONENT` before
parsing so monkeypatched registry tests see the current state. The moved model
and catalog helpers use the same callback pattern for `legacy.COMPONENTS` and
`legacy.INSTANCES`; these callbacks must be named and weak-referenceable so
isolated `legacy.py` test loads can be garbage-collected after use. `legacy.py`
keeps the catalog metadata validator call at its historical import-time
position so the implementation can move without changing validation order.
Scenario validation/runtime uses the same named weak callback pattern for
`legacy.SCENARIOS`; `legacy.py` keeps exactly one scenario-validator call at
the historical import-time position. `scenario_catalog.py` is the deliberate
module-size exception: its 2k-line body is one ordered declarative registry and
must not acquire validation or runtime orchestration. The executable scenario
modules remain below 800 lines, and `scenarios.py` imports the canonical model,
registry, and helper directly while preserving identity with `legacy`.
Generation and topology leaf modules use the same named callback pattern for
`legacy.DERIVATIONS`, `legacy._format_fixed3`, `legacy.TOPOLOGY`,
`legacy._TOPOLOGY_LOAD_METRICS`, and
`legacy._TOPOLOGY_SATURATION_TARGETS` so legacy monkeypatches still exercise
the current registry state without introducing a reverse import. Direct callers
can patch the canonical extracted-module homes when they bypass `legacy.py`.
`run_pipeline.py` uses one named weak callback returning the current legacy
namespace, keyed by the facade module's `__name__`; `main()` resolves its
collaborators at call time so fresh package-qualified legacy copies and
monkeypatches remain isolated without a reverse import.
`otel.py` imports
its public streamers from `otel_stream.py`; `legacy.py` re-imports the same
objects so facade/legacy identity remains stable.
**Monkeypatch note:** `_wide_component_rows_are_monotonic` is called only
by `combine_logs_unified` in `combine_impl`, so a test stubbing the
pre-scan patches `anomaly_metric_creator.combine_impl.<name>`, not the
`legacy` re-import (the intra-module call resolves in `combine_impl`'s
namespace) — the design.md move-with-callers rule in practice.
**Splice hazard learned in step 5:** a line-range cut can overlap a
*prior* extraction's re-import stub (step 5's combine range swept up the
step-2 `from .otlp import` block); after any extraction, grep the moved
range for `^from \.` re-imports and confirm every leaf re-import
(`redaction`, `timeutil`, `otlp`, `csv_layout`, `artifacts`, `combine_impl`,
`schema_impl`, `validate_impl`, `otel_stream`, `cli_args`, `cli_subcommands`,
`models_impl`, `catalog`, `runtime_defaults`, `run_defaults`, `run_pipeline`,
`anomaly_dispatch`, `generation`,
`generation_derivations`, `generation_helpers`, `generation_emit`,
`topology_models`, `topology_registry`, `topology_impl`, `topology_compose`,
`topology_instances`, `topology_support`, `scenario_builders`,
`scenario_catalog`, `scenario_validation`, `scenarios_impl`)
still resolves.
`tests/conftest.py::_load_amc` and the fresh-copy loaders in
`tests/test_correctness.py` / `tests/test_determinism.py` load `legacy` with
package context (real submodule import or a dotted spec name) so these
re-import seams resolve; a package-less `spec_from_file_location` copy would
fail on them.

## Architecture

### Core generation pattern

The script uses a single generator function `generate_component()` that:

1. Takes a component name, a list of `MetricSpec` rows, anomaly specs, and per-run
   config (`base_dir`, `total_seconds`, `drop_rate`, `interval`, pre-built timestamp
   arrays).
2. Builds `floor(total_seconds / interval)` rows. At `interval=1.0` this is one row
   per second; the CLI default is 50,000 rows at `interval=60.0`, matching the
   reference observability telemetry CSV shape.
3. Injects anomalies at their nearest row (`round(time_offset / interval)`). Specs
   whose row index falls outside `[0, n_rows)` are warned on stderr and skipped.
4. Randomly drops rows at `drop_rate` to simulate packet loss (the row is
   omitted from the CSV entirely; no blank line is emitted). A dropped row
   emits neither a CSV row nor a manifest entry; a shaped span whose
   leading row(s) are dropped records its manifest entry at the span's
   first kept row (a span dropped in its entirety records none).
5. Writes timestamp + metric columns to `{component}.csv`.

`generate_component()` is fully vectorized: one numpy op per metric column, anomalies
applied as masked writes, CSV assembled via `np.char.add`. The test suite drives full
1-day and 7-day runs end-to-end through `main()` — keep that path numpy-vectorized when
making changes.

### Entry point

`main(argv=None)` is the entry point and is only invoked under
`if __name__ == "__main__"`. Importing the module does not trigger generation, which
keeps tests and ad-hoc reuse of `generate_component()` cheap.

### CLI surface (canonical flags, subcommands)

The CLI was consolidated around the common use cases; the 16
deprecated alias flags from that consolidation were removed at the
post-phase-9 CLI flag day and no longer parse. Canonical surface:

- **Subcommands** (dispatched in `main()` before argparse on
  `argv[0]`): `generate` (the default when no subcommand token is
  given — every historic bare invocation is unchanged), `combine DIR
  [--components ...]`, `validate DIR [--warn]`,
  `serve [server flags] [generate flags...]`, and
  `trace-bundle {summary,search,unsupported,export-csv} BUNDLE`.
  The subcommands carry dedicated parsers
  (`_main_combine_subcommand` / `_main_validate_subcommand` /
  `_main_serve_subcommand` / `_main_trace_bundle_subcommand`).
  `combine`, `validate`, and `trace-bundle` never route through
  `parse_args`; `serve` has a small server-flag parser and forwards
  unrecognized flags to `parse_args` so the normal generation surface
  (`--scenarios`, `--components`, `--otel-send`, etc.) stays
  authoritative. `trace-bundle` is offline-only tooling over JSON from
  `GET /v1/debug/commands/export`: it summarizes, searches, groups
  unsupported traces, and exports flattened CSV without starting the HTTP
  server.
- **`--emit ARTIFACTS`**: tokens
  `metrics, logs, traces, gauges, schema, combined` (default
  `metrics,logs,traces`). `combined` sets the internal `args.combine`
  and requires `metrics`.
- **`--otel-send SIGNALS`**: a subset of
  `logs, metrics, traces, gauges`, or `all`, or `none` (explicit off,
  overriding env-var endpoint defaults). `--otel-send gauges` alone is
  the gauges-only mode. The selection is authoritative —
  unselected signals get their endpoint forced to `None` even when an
  env var exports one. The `MEZMO_OTEL_EMIT_GAUGES` env default was
  removed with the toggles — the authoritative selection meant it
  could never take effect once `--otel-send` became the only enable
  path.
- **`--otel-endpoint BASE` / `--otel-auth-token TOKEN`**: per-signal
  URLs derive as `BASE/v1/<signal>` for
  the selected signals (gauges posts to the metrics endpoint) and the
  token fans out to every selected signal. Precedence: the derivation
  beats a `MEZMO_OTEL_*` env var (an
  explicitly typed base must not be silently hijacked by a stale
  shell export); the env var supplies the per-signal default
  when no base is given. The per-signal flags are gone — the env vars
  are the only per-signal override mechanism.
- **Two-tier help**: `-h` renders five argument groups (common /
  anomaly selection / dataset shape / artifacts / OTEL streaming);
  `--help-all` un-hides the advanced knobs (`--anomaly-count`,
  `--allow-huge-output`, `--inject-dst-artifact-day`,
  the OTEL transport tuning flags). Hiding is a post-construction
  pass over `p._actions` keyed by `_ADVANCED_DESTS`.

Parser construction and CLI reconciliation live in `cli_args.py`; the
dedicated subcommand parsers live in `cli_subcommands.py`; `legacy.py`
re-imports those names so `legacy.<name>` and server `state.legacy` lookups
stay compatible. Reconciliation lives in `_reconcile_cli_surface(p, args)`,
called immediately after `p.parse_args` and *before* every validation gate:
the canonical flags translate onto the historic argument-namespace
names (`emit_selection`, `combine`, `otel_enabled`, the per-signal
endpoints, ...) so all downstream gates and `main()` consume one
namespace. Those internal dests are seeded by `p.set_defaults`
(per-signal endpoints/tokens read the `MEZMO_OTEL_*` env vars there)
now that no flag writes them directly. When adding a new flag, place
it in the right group, add it to `_ADVANCED_DESTS` if it is not a
common-use-case flag, and extend `tests/test_cli_surface.py`.

### Server mode and ops command simulation

`src/anomaly_metric_creator/server.py` owns the stdlib HTTP server behind
`amc serve`. Keep it out of `legacy.py` except for the dispatch hook:
server mode is a runtime facade over the generator, not a second copy of
generation behavior. Supporting server-mode modules now hold the lower-level
surfaces that are safe to split without changing public imports:
`server_traces.py` owns `CommandTrace` persistence/search, `server_mutations.py`
owns the mutable overlay dataclasses and helpers, and `server_debug_ui.py` owns
the inline debug shell. `server_ops.py` owns the ops simulation implementation:
simulator state, command rendering, resource
snapshots, Kubernetes/Helm dispatch and mutation, and Helm release Secret
encoding. Its per-kind Kubernetes **object** and **Table** builders moved down
into two focused leaves (see `server_k8s_objects.py` / `server_k8s_tables.py`
below); `server_ops.py` re-imports them and still owns the
`_k8s_objects_for_resource` / `_k8s_table` dispatchers, `resource_snapshot()`,
and `_k8s_endpointslice` (the one builder that reads `resource_snapshot` via its
own default, so it cannot move without a reverse import).
`server_ops_profiles.py` is the pure-data leaf holding the ops scenario-profile
registry (`OPS_SCENARIO_PROFILES`), its `OpsComponentImpact` /
`OpsScenarioProfile` dataclasses, the `_impact` / `_profile` builders, and the
`validate_ops_profiles` fail-fast validator.
`server_ops_parse.py` is the stdlib-only leaf holding the client-command
**parse cluster**: the `ParsedCommand` return dataclass, the flag/alias data
tables (`_VALUE_FLAGS`, `_REPEATABLE_VALUE_FLAGS`, `_BOOL_FLAGS`,
`_SENSITIVE_FLAG_TOKENS`, `_MODELED_FLAGS`, `_KIND_ALIASES`,
`_EXPLAIN_RESOURCE_TARGETS`, `_EXPLAIN_GROUP_ALIASES`), `parse_command` with
its `_split_flags` / `_store_flag_value` / `_flag_values` / `_first_flag_value`
tokenizer helpers, the `_parse_kubectl` / `_parse_helm` family sub-parsers and
their `_split_resource_token` / `_normalize_kind` / `_split_explain_target` /
`_normalize_explain_resource` helpers, and the `command_fingerprint` /
`guess_intent` / `_redact_command_for_trace` / `_redact_argv` /
`_redact_parsed_flags` / `_is_sensitive_flag_name` fingerprint/redaction
helpers. It imports only stdlib plus `DEFAULT_NAMESPACE` from
`server_mutations`; the staying `render_command` renderers, `_is_dry_run`,
`_preview`, and the `_SENSITIVE_QUERY_KEYS` / snapshot-kind constants keep
their `server_ops` homes and read the re-imported parse names.
`server_ops_support.py` is the pure lower leaf (stdlib +
`server_mutations.DEFAULT_NAMESPACE` only) holding the shared ops-support
surface both `server_ops` and the two k8s leaves consume downward: the
`DEFAULT_RELEASE` / `DEFAULT_CHART` identity constants and the
`_snapshot_row_namespace`, `_snapshot_row_labels`, `_parse_user_timestamp`,
`_parse_optional_timestamp`, `_string_dict`, and `_k8s_list_resource_version`
accessors. `server_k8s_objects.py` holds the per-kind Kubernetes **object**
builders (30 `_k8s_*` object functions) plus the metadata / owner-reference /
label / container-state / pod-timestamp / pod-ip helpers they share; it imports
`DEFAULT_RELEASE` + the five snapshot/timestamp accessors from
`server_ops_support`. `server_k8s_objects.py` also becomes the home of
`_k8s_metadata` / `_k8s_timestamp`, which `server_helm.py`'s future extraction
depends on. `server_k8s_tables.py` holds the `meta.k8s.io/v1` **Table**
surface: `_k8s_table`, `_k8s_column`, `_k8s_table_schema`, and the 24 per-kind
cell builders (plus the `_k8s_default_cells` fallback); it imports
`_k8s_list_resource_version` from `server_ops_support`. Both k8s leaves reference
`SimulationState` only in annotations, guarded by a runtime-inert
`if TYPE_CHECKING: from .server_ops import SimulationState` so mypy resolves the
name without a reverse runtime import. The DAG is
`server_mutations → server_ops_support → server_k8s_objects → server_k8s_tables`,
with `server_ops` re-importing every moved name (the allowed direction).
`server_k8s_objects.py`, `server_ops_support.py`, and `server_k8s_tables.py`
are all in the mypy clean-module gate; `server_k8s_tables.py`'s single
verbatim-moved `var-annotate` gap (`_k8s_node_cells`'s `ready` from the
`next(..., {})` fallback) was closed with an explicit `dict[str, Any]`
annotation when it joined the gate.
For each leaf, `server_ops.py` re-imports every
name at the original block position (one-way import — the leaf never imports
`server_ops`), so `server.py`'s alias block, the three facades, and
`server_mcp.py` keep resolving unchanged.
`server_commands.py`, `server_kubernetes.py`, and `server_helm.py` are focused
facades over those ops surfaces for compatibility boundaries. `server.py`
intentionally re-exports their public names for compatibility with existing
tests and ad-hoc imports.
Offline trace-bundle analysis lives in `trace_bundle.py` and imports
`server_traces.py` directly rather than the HTTP server facade.
`server_mcp.py` owns the MCP (Model Context Protocol) facade served at
`POST /mcp`: a stateless streamable-HTTP JSON-RPC layer (`initialize`,
`tools/list`, `tools/call`, `ping`; notifications get 202, `GET /mcp` gets
a 405 JSON-RPC refusal) plus the read-only tool registry
(`get_current_time`, `list_components`, `get_topology`,
`get_metric_histogram`, `list_metric_fields`, `group_metrics_by_field`,
`get_correlated_timeline`, `get_logs`, `deduplicate_logs`, plus the ops
wrappers `kubectl_get`, `describe_resource`, `get_pod_logs`, `get_events`,
`helm_status`, `helm_history`, which dispatch through
`parse_command`/`render_command` and the overlay-aware
`resource_snapshot()` — never a second resource model). Every `tools/call`
is recorded as a `CommandTrace` under command family `mcp` (redacted via
`_redact_parsed_flags`, fingerprinted via `command_fingerprint`), so
unknown-tool and schema-invalid calls accumulate in
`/v1/debug/unsupported` exactly like kubectl misfires; the debug UI's
family filter lists `mcp`. Tools answer
only from what the run already produced — the simulated clock,
`_resolve_effective_specs`, `_serialize_topology`, the per-component CSVs
(via the same `_scan_component_csv_headers` dispatch the gauge/combine
writers use, so dim-aware runs work identically), and
`metric_report.log` — and are subject to the
**ground-truth wall**: no MCP tool may read `anomalies.csv` or the
`SCENARIOS` registry, because the MCP surface is what an AI agent under
evaluation sees while the anomaly manifest is the eval harness's scoring
rubric. `server.py` only routes the request body (`_send_mcp_post`);
protocol behavior, error codes, and the import-time-validated
`MCP_TOOLS` registry live in `server_mcp.py`. When adding a tool, extend
`MCP_TOOLS` (name, description, JSON-Schema `inputSchema`, handler),
keep it inside the ground-truth wall, add core coverage in
`tests/test_server_mcp.py`, and add its schema-valid minimal arguments to
the registry-coupled eval/non-eval sweep in `tests/test_server_eval_mode.py`;
the structural guard there also scans module-local helper calls for rubric
access.

**Eval mode (`--mcp-eval-mode`).** `amc serve` is an evaluation target for
AI incident-response agents; the agent's scoring rubric is the run's
`anomalies.csv` plus scenario descriptions, held by the harness. Eval mode
hides every rubric-bearing surface so an agent reaching `/mcp` cannot read
the key. `SimulationState.eval_mode` is the single source of truth (set by
`build_state(..., eval_mode=)` from the `--mcp-eval-mode` serve flag) read
by both the HTTP dispatch and the MCP log tools. The classification lives
in **one registry** in `server.py`: `_RUBRIC_ENDPOINT_EXACT` +
`_RUBRIC_ENDPOINT_PREFIXES` (judged by `_rubric_endpoint`) list the hidden
surfaces (`/v1/anomalies`, `/v1/scenarios`, `/v1/state`, `/v1/logs/stream`,
the whole `/v1/debug` prefix, and the `/` + `/debug` console shell), and
`_INVESTIGATION_ENDPOINT_EXACT` lists the routes that stay open. In eval
mode a rubric endpoint returns `404` (chosen over `403` for
fingerprint-resistance) before auth and before the debug-shell branch.
`tests/test_server_eval_mode.py::test_every_dispatched_route_is_classified`
scans the dispatch source for path literals and fails if any route is
unclassified — a new endpoint must be placed in the rubric or investigation
registry, never left to default open. **Load-bearing audit finding:**
`metric_report.log` is a *verbatim rendering of the anomaly manifest*
(identical descriptions and `event_id`s to `anomalies.csv`, including
`Cascading:` labels), so it is rubric-bearing — the MCP
`get_logs`/`deduplicate_logs` tools refuse in eval mode
(`_EVAL_MODE_LOG_NOTE`) and `/v1/logs/stream` is hidden. A future log
artifact carrying only observable symptoms (not the manifest) could be
reclassified, but the current report log must stay behind the wall. `/mcp`
also gets the `mcp` rate-limit bucket and JSON-RPC-shaped 401/413/429
refusals (`rate_limited_response` / `body_too_large_response` /
`sse_not_supported_response` in `server_mcp`).

**Endpoint hiding is necessary but not sufficient — the investigation-open
surfaces must also not name the rubric (task
`07-06-eval-mode-ground-truth-wall-completeness`).** The active scenario
*slugs* are as load-bearing as the manifest: an agent that reads
`db_stall,cache_collapse` off any open surface has the answer key. The
extended wall rule is **no active-scenario identifier on any surface an
eval agent can reach — only observable symptoms**. Concretely, in eval mode
`state.active_scenarios` is withheld from every investigation-open render
path via the `_exposed_active_scenarios` / `_exposed_component_scenarios`
helpers in `server_ops.py` (which return empty in eval mode, collapsing to
a legitimate zero-scenario run rather than a marker, so the redaction is
itself fingerprint-resistant): the `simulated-saas-config` ConfigMap
`SCENARIOS` key, per-pod `scenario_ids` in `resource_snapshot()` (so the
MCP `kubectl_get` tool, the `_k8s_configmap` REST object, and command-mode
`kubectl get` all inherit it from the single snapshot), `kubectl exec … env`
output, `helm get values`, and the Helm release `config.scenarios` payload.
**The `/v1/commands` response echoes the `CommandTrace` (`{"trace": …}`),
whose `active_scenarios` field would otherwise leak the full list on every
command regardless of what was run** — `run_command` scrubs that field from
the echo in eval mode while the *stored* trace keeps the real slugs (the
walled `/v1/debug/*` + `/v1/debug/commands/export` surfaces are the
harness's scoring data). Behavioral signals are deliberately **not** gated
— unhealthy pods, profile-driven events, and the `ScenarioInfluenced`
health status stay visible (`_component_scenarios` is the behavior helper;
only the emit sites are wrapped), because the agent must still see the
symptoms. The rubric-`404`-before-auth ordering holds for **every** method
(`do_GET`, `do_POST`, and `_handle_mutating_method` for PUT/PATCH/DELETE):
an unauthenticated request to a rubric endpoint returns `404`, never `401`,
so eval mode cannot be fingerprinted by probing which paths challenge for a
bearer token. `tests/test_server_eval_mode.py` pins the multi-surface leak
sweep (with a non-eval positive control so it cannot pass vacuously) and the
per-method ordering.

Lifecycle:

1. `_main_serve_subcommand()` imports `server.serve_main()` lazily and
   passes the already-loaded legacy module.
2. `serve_main()` parses server-only flags first (`--host`, `--port`,
   `--namespace`, `--debug-ring-size`, `--persist-command-log`,
   `--persist-command-db`, `--persist-command-retention`, `--config`,
   `--auth-token`, `--max-request-body-bytes`, `--allow-remote-without-auth`,
   `--cors-allow-origin`, `--rate-limit-per-minute`,
   `--max-concurrent-requests`, `--max-sse-connections`,
   `--socket-timeout-seconds`, `--structured-log`,
   `--structured-log-file`,
   `--no-generate`, `--continuous-generate`,
   `--continuous-generate-interval-seconds`), then parses all remaining
   flags with `parse_args`. `--config` may point at JSON or YAML containing
   `server` and `generate` maps; config values are converted to long flags
   before parsing, and explicit CLI flags are appended afterward so they win.
3. Unless `--no-generate` is set, it runs the normal generator once with
   `--otel-send none` appended so one-shot generation does not block on
   OTEL before the HTTP listener starts.
4. It builds a `SimulationState` from the parsed args, generated
   `anomalies.csv`, `SCENARIOS`, and the simulated clock.
5. If `--continuous-generate` is enabled, a daemon thread reruns the normal
   generator with incremented seeds, reloads `anomalies.csv`, refreshes the
   generated artifacts on disk, and updates the generation status exposed by
   `/v1/state`. When OTEL streaming is enabled, this same loop serializes
   regeneration and OTEL replay so the streamer never reads files while the
   generator is rewriting them.
6. If continuous generation is not enabled and the original args selected OTEL
   streaming, the server starts a daemon thread that calls the existing
   `stream_otel_signals()` / `stream_otel_gauges()` helpers once for the
   startup artifacts.

The command simulator never shells out. `POST /v1/commands` accepts either
`{"command": "kubectl get pods -n saas-prod"}` or `{"argv": [...]}`;
`parse_command()` uses `shlex` plus a small flag parser, and
`render_command()` returns deterministic stdout/stderr/exit-code triples. Keep
that behavior in `server_ops.py` and expose command-specific entrypoints
through `server_commands.py`; `server.py` should stay the HTTP/serve facade.
Every call is recorded as a `CommandTrace` in a thread-safe ring buffer, with
optional JSONL persistence via `--persist-command-log` and optional SQLite
persistence via `--persist-command-db`. The SQLite store reloads recent traces
on restart, keeps durable counts, records a schema version in
`command_trace_meta`, optionally caps retained rows via
`--persist-command-retention`, and backs filtered search by raw command,
stdout/stderr, fingerprint, matched rule, support status, command family, and
active scenario. SQLite search uses FTS5 when the runtime SQLite build supports
it and falls back to the LIKE search otherwise. `GET /v1/debug/commands/export`
and `POST /v1/debug/commands/import` move trace histories as portable JSON for
offline debugging. `amc trace-bundle` consumes those exports for offline
summary/search/unsupported-grouping/CSV workflows and should keep using the
same `server_traces.trace_matches_search()` and
`server_traces.unsupported_summary_from_traces()` helpers so online and offline
filters stay aligned.

The same server also exposes a real-client Kubernetes API facade so stock
`kubectl` and Helm 4 can point at `/v1/kubeconfig`. Keep this facade in
`server.py` and backed by `resource_snapshot()` rather than creating a second
resource model. The compatibility surface includes Kubernetes discovery
(`/version`, `/api`, `/apis`), core resources, `apps/v1`, `autoscaling/v2`,
`batch/v1`, `discovery.k8s.io/v1`, `networking.k8s.io/v1`,
`metrics.k8s.io/v1beta1`, and `authorization.k8s.io/v1` self-subject access
reviews. `kubectl get` uses server-side `meta.k8s.io/v1` Table responses when
the client asks for them, including category support for `kubectl get all`.
Helm compatibility is provided through Helm-shaped `helm.sh/release.v1` Secret
objects with double-base64 gzip JSON release payloads, which are smoke-tested
in full CI with kubectl v1.36.2 and Helm v4.2.0 against the facade's advertised
Kubernetes v1.36.2. These payloads are not native Helm 3 protobuf releases;
do not describe them that way unless the storage encoder is changed to emit
Helm's protobuf release object.
Every real-client request should be recorded as command family `kubernetes-api`
so unsupported client paths remain visible in `/v1/debug/search`.

**Bounded watch streams.** A real-client `GET …?watch=true` (or `watch=1`) on
a modeled *list* path dispatches to `_send_k8s_watch` in `server.py` **before**
the one-shot `kubernetes_api_response` branch in `do_GET`. Watchable families
are `_WATCHABLE_LIST_RESOURCES` in `server_ops.py` — v1 asserts
`("", "v1", "pods")` and `("apps", "v1", "deployments")` only — but the stream
loop is generic over `_k8s_objects_for_resource`, so opting another modeled
list path in is a one-line addition to that set. `k8s_watch_objects` runs the
same `_k8s_objects_for_resource` → `_filter_k8s_objects_by_namespace` →
`_filter_k8s_objects` chain the list path uses, so a watch observes exactly the
overlay-aware set the equivalent list returns (no second state model). The wire
shape is newline-delimited JSON watch events (`{"type":
"ADDED"|"MODIFIED"|"DELETED", "object": …}`) under `content-type:
application/json` with **no** content-length: an `ADDED` replay of the current
object set, then a poll every `_WATCH_POLL_SECONDS` (module global, default 2.0,
monkeypatched to a small value in tests) that diffs the fresh snapshot by object
identity (`k8s_watch_object_key`: `uid`, else namespace/name) and emits change
events. The stream is bounded — it closes at `min(timeoutSeconds,
_WATCH_MAX_SECONDS)` (default 300) or on `state.shutdown_event` — and consumes
one SSE slot for its lifetime through its own `try_acquire_sse`/`release_sse`
accounting (mirroring `_with_sse_slot`), refusing over-ceiling streams with a
Kubernetes `Status` 503 **before** any stream headers (not the app JSON 503)
and always releasing the slot in `finally`. A client disconnect ends the stream
without a traceback (`_write_event_stream` swallows BrokenPipe). Exactly one
`kubernetes-api` `CommandTrace` is recorded per watch via
`k8s_watch_trace_response` — supported with the emitted event count on a clean
close, partial on a 503 refusal. There is no `resourceVersion=` resume
(kubectl re-lists on reconnect — acceptable simulator behavior); single-object
watch paths, non-`true`/`1` watch values, and unmodeled resources all fall
through to the existing one-shot get/list/404 handling. Because
`POST /v1/commands` cannot hold a stream open, command-mode `kubectl get <kind>
--watch`/`-w` renders the one-shot table exactly as `get`, appends one stderr
note (`_WATCH_COMMAND_NOTE`) pointing at real kubectl, exits 0, and classifies
the trace **partial** under rule `kubectl.get.<kind>.watch`
(`_render_get_watch`) so the ignored flag becomes a debug-backlog signal.
Coverage: `tests/test_server_watch.py` (six design cases) plus watch shapes in
`tests/test_server_ops_fuzz.py`.

Security/ops boundary (the full trust model, remote-bind posture,
credential handling, and known limits live in [SECURITY.md](SECURITY.md) at
the repo root): loopback binds may run unauthenticated for local
workshops, but non-loopback `--host` values require `--auth-token` unless the
operator explicitly passes `--allow-remote-without-auth`. When token auth is
enabled, every endpoint except `/healthz`, `/readyz`, and the static debug
console shell (`/` and `/debug`) requires `Authorization: Bearer TOKEN`, and
`/v1/kubeconfig` embeds that token for real `kubectl`/Helm clients. The debug
console must attach that bearer token to its JSON/API fetches, either from the
browser prompt/localStorage flow or a `/debug?token=TOKEN` bootstrap. Request
bodies are capped by
`--max-request-body-bytes`; app endpoints return `413` JSON and Kubernetes API
endpoints return a Kubernetes `Status`. `--cors-allow-origin` is the only CORS
enablement path; preflight requests are answered without bearer auth, and
normal responses include access-control headers only when the request origin
matches that configured value (or the value is `*`). `--rate-limit-per-minute`
caps command and Kubernetes API requests per client, returning JSON `429` for
app calls and a Kubernetes `Status` with `reason: TooManyRequests` for API
calls; its `_RateLimiter` sweeps idle per-client buckets each window
(`_sweep_locked`) so the limiter's own table stays bounded on a public bind
(the DoS-hardening feature must not itself be an unbounded allocation).
**Remote-bind resource bounds** (task `07-02-server-remote-bind-hardening`):
because the server is a `ThreadingHTTPServer` spawning one worker per
connection, a reachable instance is hardened by three defaults-on bounds,
each disablable with `0`. `_BoundedThreadingHTTPServer` caps concurrent
worker threads (`--max-concurrent-requests`, default 64) via a
`BoundedSemaphore` acquired in `process_request` *before* the worker thread
starts — an over-cap connection gets a raw `503` (`_SATURATED_503`) and is
closed, never spawning a thread. A separate SSE semaphore
(`--max-sse-connections`, default 16) gates the two long-lived streams
(`/v1/debug/events`, `/v1/logs/stream`) through `_with_sse_slot`, which
refuses over-ceiling streams with a JSON `503` before any event-stream
headers and always releases the slot on exit. The handler's `timeout` class
attribute (`--socket-timeout-seconds`, default 30) applies a socket timeout
via `StreamRequestHandler.setup()` so a slow-loris client cannot pin a
worker. Both `serve_main` and `start_test_server` construct the bounded
server, so tests exercise the bounds; the generous defaults do not affect
single-client workshop use. This hardens the surface behind the auth gate
but does not make an unauthenticated remote bind a supported posture (see
[SECURITY.md](SECURITY.md) for the trust-boundary write-up: remote bind is
discouraged and only tolerated for isolated lab networks, never supported). Command/API traces must redact bearer tokens, token-like query params,
passwords, secrets, and client-key shaped values before they reach memory,
JSONL, SQLite, or the debug UI. Structured request logging is opt-in via
`--structured-log` or `--structured-log-file`; it emits JSONL request summaries
and request-handling exception records, redacts query secrets with
`_redact_query()`, and records bearer auth only as present/absent. Server
shutdown sets `SimulationState`'s
shutdown event, joins the continuous-generation worker, and lets long-lived
SSE clients receive a terminal `shutdown` event promptly. Supported mutating
Kubernetes HTTP methods update the in-memory `SimulationMutations` overlay and
are traced as supported `kubernetes-api` calls; unsupported mutation paths
still return Kubernetes `Status` responses and are captured in the debug
backlog. Mutations are checked against the overlay-aware
`resource_snapshot()` *before* any overlay write: a PATCH/PUT/DELETE naming
a resource the snapshot does not contain returns a 404 `Status` and leaves
the overlay untouched (a refused mutation must never leave partial state —
the deployment-scale path once wrote `set_workload` before its own 404
check). The generic 500 boundary in `server.py` returns
`{"error": "internal server error"}`; exception detail goes only to the
structured error log, never the response body.
`tests/test_server_ops_fuzz.py` is the seeded malformed-input corpus
(commands, argv shapes, API paths, mutation bodies) that pins the
graceful-degradation contract.

The command API should stay aligned with that same snapshot-backed surface:
when adding a new Kubernetes resource family, update `_KIND_ALIASES`,
`_SNAPSHOT_KINDS`, `resource_snapshot()`, `_render_get()`,
`_render_describe()` when useful, `_k8s_api_resource_list()`,
`_k8s_objects_for_resource()`, and table/object helpers in one pass. Keep
mutating command/API support layered through `SimulationMutations`; do not
write command-only state back into scenario definitions or generated CSV rows.
Any new mutation must update the command/API trace classification, the snapshot
renderers affected by the overlay, and focused coverage in `tests/test_server.py`.

Scenario-specific Kubernetes/Helm behavior lives in
`OPS_SCENARIO_PROFILES`, keyed by `Scenario.id`. `validate_ops_profiles()`
checks exact coverage of the `SCENARIOS` registry, verifies that every profile
and per-component impact references known components, and fails when an
affected component has no impact. These profiles feed `kubectl get/describe`,
pod logs, rollout output, `helm status`, `helm history`, `helm get notes`, and
the Helm release Secret payloads. When adding a scenario, update this profile
registry in the same change and add focused coverage in `tests/test_server.py`;
do not mutate the frozen `Scenario` dataclass for command/UI-only state unless
the generator itself needs the field.

Mutable simulator state is an overlay on top of those profiles. Workload
mutations cover scale/restart/delete effects, deleted pods are filtered from
snapshots, generic created/deleted resources are merged into snapshot lists,
extra events are appended to event views, and Helm release mutations replace the
revision list and values used by `helm list/status/history/get values` and the
Helm Secret API. `/v1/state` exposes the overlay summary and generation counters
so the debug UI can show whether the simulator is drifting from its baseline.
`POST /v1/mutations/reset` clears only this overlay; it must not regenerate files
or alter the frozen scenario catalog.

The debug UI is served from `GET /debug` as inline HTML/CSS/JS to avoid a
frontend build chain. Its static shell is intentionally accessible when bearer
auth is enabled, but it must send `Authorization` on data requests. It polls
`/v1/state`, `/v1/debug/commands`, `/v1/debug/search`,
`/v1/debug/unsupported`, `/v1/debug/resources`, and `/v1/scenarios`.
Unsupported or partial commands are grouped by normalized fingerprint so real
operator/tool calls outside the currently supported subset become a backlog for
future command renderers. The shell also derives client-side analysis views from
those payloads: exports for command traces and unsupported groups, a combined
timeline, baseline-vs-overlay resource diffs, global filters, compact runtime
charts, copyable pytest snippets for unsupported fingerprints, and a resource
drawer that fetches the same fake Kubernetes object path real clients use when
one is available. The scenario catalog in the debug UI is backed by the
`SCENARIOS` registry plus `OPS_SCENARIO_PROFILES` and is intentionally cached
after first load, while runtime state stays live; keep primary/cascade spec
descriptions and ops-profile summaries useful for humans rather than treating
them as opaque IDs.

`GET /v1/logs/stream` is an SSE stream, not a one-time download: it replays the
current `metric_report.log` immediately and then emits a generation event plus
the refreshed log file when continuous generation writes a new batch. Keep that
path bounded like `/v1/debug/events` so abandoned browser tabs cannot hold worker
threads indefinitely.

### Output directory hygiene

`main()` calls `_pre_clean_output_dir()` immediately after `args.output_dir.mkdir(...)`
and before any generation runs. The helper consumes the `_EMIT_ARTIFACT_FILES`
registry (plus the `_COMBINE_OUTPUT_FILENAME` slot) and deletes any file from
a prior run into the same directory that this run will not regenerate:
per-component CSVs for components no longer in `--components` or when
`metrics` is dropped from `--emit`, `anomalies.csv` /
`metric_report.log` / `metric_traces.jsonl` / `gauges.csv` for emit types
not selected, and `combined_metrics_unified.csv` when `combined` is not
selected.
Idempotent on missing files; files unknown to this script (user notes, the
synthetic-extra-component CSV used by the standalone combine autodiscovery
fixture) are left alone.

**Atomic publication contract.** Every generated artifact (per-component
CSVs, `anomalies.csv`, `metric_report.log`, `metric_traces.jsonl`,
`gauges.csv`, `combined_metrics_unified.csv`, `schema.json`) is written
through `_atomic_artifact_open` (or `_atomic_write_text` for
`schema.json`): the writer stages a sibling `<name>.tmp` in `--output-dir`,
flushes + fsyncs, then `os.replace`s onto the final path. A concurrent
reader — notably the `amc serve` HTTP threads while `--continuous-generate`
reruns the generator — only ever observes the complete previous or complete
new file, never a truncation or a mid-delete gap. This is a write-mechanism
contract only: output bytes are unchanged, and all locked SHA-256 golden
hashes still apply. Files this run will regenerate are therefore *not*
deleted by `_pre_clean_output_dir` (true deletion is reserved for files the
run will genuinely not emit); stale `*.tmp` siblings from a crashed run are
swept for every registry-known slot via `_known_artifact_filenames()`. When
adding a new artifact writer, route it through `_atomic_artifact_open`
(now in `artifacts.py`, re-imported by `legacy.py`) — never
`open(final_path, "w")` — and make sure its filename reaches the
registries `_known_artifact_filenames()` reads. Coverage lives in
`tests/test_atomic_writes.py`. `./otel-activity.log` is exempt: it lives
outside `--output-dir` and appends within a run, which an
atomic-replace-on-close writer cannot express.

The end-of-run `Done - …` summary line is built from the same `args.emit_selection`
+ `args.combine` inputs, so it names exactly the artifacts written this run.

Do **not** call `_pre_clean_output_dir()` from the `combine` subcommand —
that path reads existing per-component CSVs as inputs and pre-cleaning them
would remove the combine inputs. The subcommand's dedicated parser
(`_main_combine_subcommand`) never reaches the generation pipeline, which
keeps it out of the cleanup path structurally. `./otel-activity.log` lives outside
`--output-dir` and must stay outside the registry. It is a per-run log,
not append-only across runs: `stream_otel_signals` opens it with mode
`"w"` (truncating the previous run's records), the gauge pass of the
same run appends to it, and gauges-only streaming (`--otel-send
gauges`) starts it fresh.
The file is also listed in the repo `.gitignore` so a stray run from inside a
clone never commits OTLP transport diagnostics. PR #83 widened the HTTP-error
diagnostics inside `_http_error_activity_fields` to dump every response
header into the `response_headers` field; an intermediary that echoes a
credential on a 4xx/5xx — under a standard name (`Set-Cookie` /
`Authorization` / `X-Api-Key`) or a **novel** one (`X-Amz-Security-Token`,
`X-Vault-Token`, `X-Subject-Token`, `Authentication-Info`) — would have
leaked credential material into that on-disk log. The two redaction shims in
`redaction.py` (re-imported by `legacy.py`) run *before* the JSON dump and
take **deliberately different postures** for their two trust origins
(task `07-02-redaction-allowlist-hardening`):

- **Response side (`_redact_sensitive_headers`, untrusted upstream) is
  mask-unless-known-safe.** Every response-header value is masked *except*
  the short allowlist `_SAFE_RESPONSE_HEADER_NAMES` (`content-type`,
  `content-length`, `content-encoding`, `content-language`, `cache-control`,
  `date`, `server`, `vary`, `age`, `retry-after`, `cf-ray`, `x-request-id`).
  A never-before-seen header defaults to masked, so a credential an upstream
  echoes under any nonstandard name cannot reach disk. The `x-*` namespace is
  the riskiest, so only `x-request-id` is allowlisted from it.
- **Request side (`_masked_headers`, headers this process builds) stays
  allowlist-of-sensitive.** It masks only `_SENSITIVE_HEADER_NAMES`
  (`Authorization`, `Cookie`, `Set-Cookie`, `Proxy-Authorization`,
  `X-Api-Key`) because we control the outbound set and only ever attach
  `Authorization`; operational headers like `Content-Type` stay legible.
  Today the request path sends only `Content-Type` + `Authorization`, so this
  is observably identical to the response posture — the asymmetry is
  future-proofing plus correct threat modeling, not drift.

Both paths share `_mask_sensitive_value`: `Authorization` /
`Proxy-Authorization` are in `_SCHEMED_SENSITIVE_HEADERS`, so the scheme
prefix (`Bearer` / `Basic`) is kept and only the credential is replaced with
`***`; every other masked header has its full value replaced. The raw
`request_body` diagnostic on
RETRY/FAIL records is gated behind `--otel-verbose` (threaded as the
`verbose` kwarg into `_http_error_activity_fields`); non-verbose error
records carry only the always-on `response_headers` / `cf_ray`
diagnostics, so a failing endpoint cannot re-serialize a full gauge
batch into the log on every retry. Allowlist + round-trip coverage
lives in `tests/test_redact_sensitive_headers.py`, and
`test_otel_http_error_activity_log_includes_response_headers` in
`tests/test_cli.py` plus
`test_stream_otel_gauges_http_error_activity_log_includes_response_headers` in
`tests/test_otel_gauges.py` exercise the redaction through the live HTTP error
path.

### Combine step

`combine_logs(input_dir, components=None)` joins the per-component CSVs in
`input_dir` into `combined_metrics_unified.csv`. When `components` is provided,
it acts as the allowlist for which CSVs to combine (missing per-component
CSVs raise `SystemExit`); when omitted, every `*.csv` in `input_dir` is
autodiscovered (excluding the anomalies manifest and the long-form
`gauges.csv` via `_NON_COMPONENT_FILES`, and prior combine outputs via
a separate `combined_metrics_` filename-prefix check inside
`discover_components` — the constant does not cover all three).
`main()` always passes the selected known component list to the generated
`combined` artifact, including the default `--components all`, so stale or
foreign CSVs left in `--output-dir` cannot be folded into artifacts for the
current run. For default `--components all`, that explicit allowlist is sorted
the same way `discover_components` sorts a clean generated directory, preserving
byte-parity with a later standalone combine when no extra CSVs are staged;
narrowed component selections keep the `COMPONENTS` declaration order. The
`combine DIR` subcommand is the autodiscovery path: its default `--components
all` maps to `components=None`, preserving the synthetic-extra-component
fixture and other hand-staged combine inputs. The output ordering contract
differs across the two dispatched layouts: the wide layout uses the caller-supplied
`components` order verbatim for the column sequence; the long layout
ignores it for layout purposes and sorts components alphabetically for
the equal-timestamp tie-break (the row's `component` cell carries the
identity, so column order is not the ordering surface). See the
**Layout (phase 5)** subsection below for the dispatch detail.
For freshly generated, non-DST wide CSVs, `main()` passes
`assume_monotonic_wide_components=set(combine_components)` so the combine
writer does not spend a second full pass proving monotonicity for files it just
emitted. This is only a trusted allowlist for generated combines; external
`combine DIR` invocations still run `_wide_component_rows_are_monotonic` before
using the streaming `heapq.merge` path. The measurement harness is
`tools/benchmark_combine.py`.

**Layout (phase 5).** `combine_logs_unified(components, input_dir, …)`
inspects every per-component CSV's header via `_scan_component_csv_headers`
and dispatches one of two layouts:

- **Wide layout (default, dimensionless input).** Every per-component CSV
  has the classic `timestamp, m0, m1, …` shape — the `N=1`
  anonymous-instance case. The combine writer emits
  `timestamp, component_a_m0, component_a_m1, component_b_m0, …`
  byte-identically to the pre-existing output. Locked `test_combine.py`
  row/column assertions still apply.
- **Long layout (phase 5, dimensioned input).** Any per-component
  CSV carries the multi-instance `id, host, pod, az, region, tenant`
  prefix (the `--instances-per-component N > 1` shape from Phase 2). The
  combine writer dispatches into `_write_combined_long_form` and emits
  `timestamp, component, id, host, pod, az, region, tenant, metric,
  value`. `_write_combined_long_form` keeps the missing-input
  `SystemExit` guard, then delegates the merge to the shared
  `csv_layout.write_long_form_merge` — the same writer
  `write_gauges_csv` uses (07-06-long-form-merge-writer-dedupe), so the two
  file writers cannot drift. Rows are merged chronologically with
  `heapq.merge` across
  per-(component, instance) iterators sourced from
  `_iter_component_instance_rows`; the per-instance ``(dim_tuple,
  start_offset)`` pairs per file come from
  `_scan_instance_block_layout` — a one-pass dim-only scan that records
  the byte offset of each block's first row so the iterator can
  ``seek()`` straight there instead of re-reading every preceding
  block. Tie-break order on equal timestamps is `(component,
  instance_id, metric)`, matching the long-form `gauges.csv` ordering
  contract. Empty / dropped cells are skipped
  (long form encodes "this measurement was emitted" via row presence —
  unlike the wide layout, which carries an empty string in the
  corresponding column). The N=3 tests co-locate both writer fixtures in
  `tests/test_gauges_file.py`, retain an independent absolute SHA-256 guard
  for each entry point, and compare their runtime digests once. Gauges-side
  structural assertions therefore cover the byte-identical combine output
  without repeating three full scans of the ~22M-row file.

Both layouts share the same `_COMBINE_OUTPUT_FILENAME`
(`combined_metrics_unified.csv`); the filename does not change with the
layout. The N=1 default keeps the pre-clean and summary slot unchanged, while
autodiscovery stays scoped to standalone `combine DIR`.

### Output schema document (`schema.json`)

`schema_impl.write_schema_json(output_path, *, components, effective_specs,
metadata, emitted_files, instances_by_component=None)` writes a declarative
`schema.json` alongside the rest of the artifacts. It is opt-in via
`schema` in `--emit` (parallel to `metrics`, `logs`, `traces`,
`gauges`) and is the single source of truth the `validate` subcommand
consumes. `legacy.py` re-imports the writer and `validate_impl.validate_output`
so the historic `legacy.<name>` surface and `schema.py` facade stay stable.

The document carries five slices of information:

- `schema_version` — integer (`SCHEMA_DOCUMENT_VERSION`, currently `2`
  after the phase 7 bump that added the `topology` section).
  `_load_schema_document` rejects unknown versions outright, so v1
  documents fail-fast under a v2 reader and vice versa. Phase 8
  keeps the version at 2 because the new per-component `dimensions`
  block is purely additive — omitted entirely in the default
  single-anonymous-`Instance()` path so the v1 schema bytes (and the
  locked SHA-256 hashes) stay byte-identical to the pre-existing
  baseline.
- `metadata` — run-level parameters (`seed`, `start`, `duration_days`,
  `interval_seconds`, `total_seconds`, `rows_per_component`,
  `drop_rate`, `signal_level`, `scenarios`, `exclude_scenarios`,
  `components`, `inject_dst_artifact_day`, `metrics_per_component`,
  `anomaly_count`, `emit_selection`, `combine`, `topology_mode`).
  `topology_mode` lets the validator short-circuit the coupling
  check under `independent`. The writer only ever emits
  `"realistic"` since the phase-9 flag day removed the independent
  alias; the reader still honors `"independent"` so documents
  produced under the historic mode keep validating.
- `components` — per-component metric metadata in MetricSpec column
  order (each entry carries `name`, `unit`, `semantic_type`, `dtype`,
  `min_value`, `max_value`, `derivation`). Phase 8 adds an
  optional `dimensions` field per component when the per-component
  CSV is dim-aware (`--instances-per-component N>1` fan-out or a
  non-default `--instance-config`):
  `{"axes": ["pod"], "cardinality": 3}`. `axes` is the sorted subset
  of `_INSTANCE_DIMENSION_FIELDS` (i.e.
  `_INSTANCE_DIMENSION_COLUMNS` minus the leading `id` slot — `id`
  identifies an instance, it is not a dimension to slice on) whose
  value is non-`None` on at least one instance in the list;
  `cardinality` is `len(instances)`. The block is omitted when
  `_is_anonymous_instance_list(instances)` returns `True`, matching
  the long-form-CSV writer's dispatch predicate so the schema view
  and the on-disk layout cannot drift.
  `_component_dimensions_schema_entry` is the single helper that
  decides both the block presence and its contents; pass the live
  `RunContext.instances` map via the `instances_by_component`
  kwarg.
- `files` — sorted list of artifact filenames the run wrote, built via
  `_collect_emitted_filenames` (the same registry that drives
  `_pre_clean_output_dir` and the end-of-run summary, so the three views
  cannot drift).
- `topology` (phase 7) — the directed coupling graph snapshot,
  built from the live `TOPOLOGY` constant via `_serialize_topology` and
  restricted to the active component set. Shape:
  `{source: [{target, weight, saturation, correlation_threshold}, ...]}`
  with each source's edge list sorted by `target` for byte-deterministic
  output. Constant-weight edges serialize their numeric weight verbatim;
  callable-weight edges serialize the literal string `"callable"`
  (full reproducibility is a code concern — the schema only declares
  the coupling exists). `saturation` is either `null` or a
  `{midpoint, steepness, latency_gain, error_gain}` dict.
  `correlation_threshold` is a float or `null`. Sources whose
  source or every target was filtered out of `--components` are omitted
  so the validator does not try to correlate columns the run did not
  write. The validator's `_validate_topology_coupling` reads this
  section under `topology_mode == "realistic"`.

The output is byte-deterministic (`sort_keys=True`, fixed indent, UTF-8
with trailing newline). Locked SHA-256 golden hashes live in
`tests/test_schema_file.py`. The 1-day default and 1-day N=3 locks retain their
1s `metrics,schema` inputs; the 7-day default lock uses 60s with
`metrics,schema`, and the 7-day N=3 lock uses a standalone 60s `schema` run.
Those coarse locks assert duration, row cardinality, and (for N=3) dimension
cardinality before hashing, so they cover the schema semantics without
generating unread full-resolution CSVs. The `combine` subcommand does not
regenerate `schema.json` (it never enters the generation pipeline), matching
the `gauges.csv` invariant.

### Multi-instance fan-out (`--instances-per-component`)

`COMPONENTS` declares one MetricSpec list per logical component, and
`INSTANCES` is the parallel module-level registry of `Instance`
objects that name each emitting *replica* (id, host, pod, az, region,
tenant). Phase 1 landed the `Instance` dataclass, the
`INSTANCES = {name: [Instance()] for name in COMPONENTS}` default,
`_validate_instances_registry` / `_validate_instance_list` import-time
checks, and the `RunContext.instances` thread that passes the per-run
per-component list into `generate_component(..., instances=...)`.
Phase 2 wired the CLI:

- `--instances-per-component N` (default `1`, range `[1,
  MAX_INSTANCES_PER_COMPONENT=20]`) — when `N > 1`, `main()` replaces
  `ctx.instances` with `{name: [Instance(id=f"i{k}", pod=f"pod-{k}")
  for k in range(N)] for name in COMPONENTS}` (host / az / region /
  tenant remain `None` in v1; Phase 3 will plug them in via
  `--instance-config PATH`). `N == 1` keeps the module-level
  anonymous-`Instance()` map and emits today's byte-identical
  output.
- `PREFLIGHT_CELL_CAP` now multiplies by `args.instances_per_component`
  so the same `--allow-huge-output` gate that catches metric-cell
  blowups catches instance-cell blowups too. The error message lists
  `--instances-per-component` alongside `--interval-seconds`,
  `--duration-days`, `--components`, and `--metrics-per-component`
  as the levers that can lower the estimate.

The long-form emission path inside `generate_component()` keys off
the *content* of the per-run instance list (not the CLI flag): a
single anonymous `Instance()` keeps the historic `timestamp,m0,…`
header and body; any other shape (`len(instances) > 1`, or a single
instance with any non-`None` dimension field) switches to the
long-form `timestamp,id,host,pod,az,region,tenant,<metrics…>`
header and writes one full row block per instance (all rows for
`instances[0]`, then all rows for `instances[1]`, …) — column
order is fixed and tested in `tests/test_instances_per_component.py`.
All instances share the same RNG-drawn natural values, and unfiltered
anomaly overrides apply to every instance; Phase 4's `instance_filter`
(see the anomaly injection schema) lets a spec target individual
instances, forking a per-instance value buffer for the matched pods.

Every phase of the multi-instance plan has shipped:
`--instance-config PATH` (Phase 3), per-anomaly `instance_filter`
(Phase 4), dimension-aware `gauges.csv` /
`combined_metrics_unified.csv` writers (Phase 5), OTLP data point
attributes (Phase 6), and the schema.json `dimensions` block +
dim-aware output validation (Phase 8 — see the schema-document and
validator sections of this file). After Phase 6, `stream_otel_gauges`
and `stream_otel_signals` lift every non-empty
`_INSTANCE_DIMENSION_COLUMNS` cell off each row and surface it as a
string attribute on every OTLP data point (metric datapoint
attributes, not OTEL resource attributes), so the OTEL signal
stream, the gauge stream, and the gauge-only streaming mode
(all selected via `--otel-send`) are no longer gated against N>1. After
Phase 8, `--emit ...,schema` and the `validate` subcommand work
under `--instances-per-component > 1` too — no parse-time
multi-instance gate remains except the intentional DST one
(`--inject-dst-artifact-day > 0`). `generate_component()`
mirrors the DST guard inside the helper as well — passing a
non-anonymous instance list together with `dst_inject_day > 0`
raises `ValueError` even when the call bypasses `parse_args`. The
single-instance default (`N == 1`) keeps every flag combination
historically permitted, so existing one-instance workflows do not
need to change.

Locked SHA-256 N=3 golden hashes at 1d and 7d live in
`tests/test_instances_per_component.py` (`N3_ONE_DAY_HASHES` /
`N3_SEVEN_DAY_HASHES`); `anomalies.csv` matches the default-run hash
because v1 records one event per `(timestamp, component, metric)`
regardless of `N` — a contract Phase 4 preserved: a spec with an
`instance_filter` still records one manifest entry no matter how many
instances matched (and none on zero-match).

### Per-instance topology (phase 8)

Under realistic topology coupling with `--instances-per-component
N > 1` (or any non-default `--instance-config`), the topology
two-pass generation runs against each downstream instance's
*matching* upstream view rather than the shared aggregate column.
The routing dispatch lives in `_matched_cardinality` and reads
the upstream / downstream cardinalities for each edge:

- **1:1 routing (matched cardinalities).** When
  `len(upstream_instances) == len(downstream_instances) > 0`,
  downstream instance `K` consumes upstream instance `K`'s
  captured load column exclusively for that edge. This is the
  "matching instance set" branch from the issue scope; it
  delivers the per-pod isolation `tests/test_topology_multi_instance.py`
  pins (a slow upstream pod produces saturation feedback only on
  the corresponding downstream pod's rows, sibling pods stay on
  the natural baseline).
- **Uniform fan-out (mismatched cardinalities).** When upstream
  and downstream pod counts differ, downstream instance `K` sees
  the mean of all upstream pods' captured load — the issue's
  "edge weight divided by downstream cardinality" formula averaged
  across `N_up` upstream pods. Every downstream pod sees the same
  averaged view under this branch, so per-pod variation only
  emerges from local saturation noise rather than from upstream
  asymmetry. This is the fallback for mixed-N runs (e.g. an
  `--instance-config` that maps a different `N` to different
  components).

Per-instance composition is gated on the *content* of
`ctx.instances`, not directly on the `--instances-per-component`
flag:

- When the per-component instance list is the single anonymous
  `Instance()` (`_is_anonymous_instance_list(instances)` is True),
  `main()` keeps the pre-existing lambda-baked path:
  `_compose_topology_coupled_specs` + `_compose_topology_saturation_specs`
  modify the per-run `MetricSpec` list (today's path). This
  branch fires for the default `--instances-per-component 1` and
  is byte-identical to the phase-6 baseline by construction.
- When the per-component instance list carries any named instance
  (`len > 1`, or any non-`None` dimension field on a single
  instance), `main()` dispatches into
  `_compute_topology_arrays_per_instance` which:
    - Builds the per-instance upstream view via
      `_per_instance_upstream_view` (1:1 for matched cardinalities,
      uniform-fan-out averaging otherwise).
    - Reuses the existing `_apply_saturation` math per instance
      so the logistic curve / `SaturationParams` ranges stay
      identical to today's shared path.
    - Draws the `_TOPOLOGY_COUPLE_NOISE_STD` coupling noise *once
      per coupled metric*, lazily on the first active contribution
      (so a downstream whose upstream column was trimmed by
      `--metrics-per-component` or whose every callable `signal`
      returned `None` consumes zero RNG draws here — matching the
      legacy `_compose_topology_coupled_specs` short-circuit), and
      caches it across instances so symmetric upstream produces
      byte-identical coupling arrays across pods (and therefore
      byte-identical CSV output to the lambda-baked path).
    - Returns `(coupling_by_instance, saturation_by_instance)`.
      Divergence detection is intentionally not returned:
      `generate_component` re-derives the divergent-instance set
      directly from the passed arrays via `_arrays_equal_dict` /
      `_sat_tuples_equal_dict` so correctness cannot depend on a
      stale caller-supplied hint (a programmatic caller that
      passed divergent arrays alongside a `False` hint would
      otherwise silently force instance-0 reuse across every
      pod). When every instance matches instance 0,
      `generate_component` runs the natural-column draw *once*
      per metric and reuses the result across all instances
      (preserves `N3_ONE_DAY_HASHES` / `N3_SEVEN_DAY_HASHES`
      locked in `tests/test_instances_per_component.py`); when at
      least one instance diverges, per-instance natural draws run
      with shared `noise=` kwargs for the divergent pods only so
      the symmetric pods stay on the shared buffer and the
      per-instance buffers cover only the truly-divergent pods.

The math hook is the refactor of `_natural_column` which
now accepts four optional keyword-only kwargs:

Three topology-state kwargs the lambda-baked path used to fold into
`MetricSpec.multiplier` / `MetricSpec.additive`:

- `latency_factor` — per-row array multiplied between the natural
  multiplier and additive, matching where
  `_compose_topology_saturation_specs` baked the saturation
  multiplier.
- `error_offset` — per-row array added after the natural
  additive and before `clip_min`, matching where the saturation
  offset was baked into `MetricSpec.additive`.
- `baseline_override` — per-row array that REPLACES the natural
  baseline draw entirely; matches what
  `_compose_topology_coupled_specs` produced by replacing
  `base=0, std=0, multiplier=None, additive=lambda: coupled` on
  the coupled metric's spec.

Plus one RNG-control kwarg used to share noise across instances
under the divergent per-instance topology path:

- `noise` — per-row pre-drawn `rng.normal(0, spec.std, n_rows)`
  array. When provided, `_natural_column` uses it verbatim instead
  of drawing fresh noise. The divergent per-instance branch in
  `generate_component` draws noise once per coupled metric and
  shares it across instances so the only divergence between pod
  buffers flows through `baseline_override` / `latency_factor` /
  `error_offset`, not through extra RNG consumption.

`generate_component` consumes these kwargs through two new
parameters threaded by `main()`:

- `coupling_arrays_per_instance: list[dict[str, np.ndarray]] | None`
  — per-instance baseline overrides for coupled load metrics. The
  list is indexed by instance position in `ctx.instances[component]`.
- `saturation_arrays_per_instance: list[dict[str, tuple[lf | None, eo | None]]] | None`
  — per-instance saturation contributions. Each tuple stores
  `(latency_factor, error_offset)` for the metric, with `None`
  on whichever side does not apply (latency-only metrics carry
  `(latency_factor, None)`; error-only metrics carry
  `(None, error_offset)`; an overlap target carries both).

Per-instance upstream capture flows through a new
`topology_capture_by_instance: dict[str, list[dict[str, np.ndarray]]] | None`
arg on `generate_component`. Each instance gets its own
`(metric_name -> column)` capture; under symmetric upstream the
columns all reference identical data (different ``.copy()`` results
on the same source row), and ``generate_component`` collapses to the
shared fast path when the calculated per-instance arrays are
identical — the divergence check is run inside ``generate_component``
against the per-instance arrays it receives directly. When an
`instance_filter` forks a per-instance buffer for pod 0 (e.g.
``instance_filter=["i0"]``), the aggregate ``topology_capture`` mean
reads ``per_instance_values.get(0, values)[:, col_idx]`` as the
initial accumulator so pod 0's forked buffer is included in the
average; using ``values[:, col_idx]`` directly would silently
exclude pod 0 because that is the shared baseline, not the forked
buffer.

Cascade-vs-topology overlap is unchanged from phase 4: per-instance
anomaly overrides (`instance_filter`) are applied per pod after
the natural-column draw, so a cascade write at row `i` for
instance `K` still wins at exactly that cell regardless of the
saturation-driven baseline computed for that pod.

**Validator (phase 7).** The validator's
existing `_validate_topology_coupling` runs an aggregate-mean
Pearson check per edge (the timestamp axis is collapsed across
instances by `_read_component_metric_column`). The per-instance
extension adds a companion `_validate_topology_coupling_per_instance`
invocation
inside the per-edge loop that fires only when both source and
target schemas declare a `dimensions` block with matched
cardinalities. The check verifies
`Pearson(source.iK, target.iK) >= threshold` for each matched
pod pair (by CSV block / insertion order, matching the
generator's index-based 1:1 routing) so a regression that
mis-routes one pod's load to a sibling surfaces as a dedicated
violation. Skipped silently for dimensionless schemas,
mismatched cardinalities (uniform fan-out doesn't promise
per-pod isolation), single-instance runs, or fewer than
`_TOPOLOGY_MIN_ALIGNED_ROWS` aligned rows per pod pair.

### MetricSpec schema metadata

`MetricSpec` carries six optional declarative fields that flow into
`schema.json` and the `validate` subcommand: `unit`, `semantic_type`,
`min_value`, `max_value`, `dtype` (default `"float"`), `derivation`.
Five of the six are metadata-only and do not affect generation —
they exist only so the validator can range-check, dtype-check, and
recompute derived columns. `dtype` is the exception: under the
default `--topology-mode realistic` (phase 6 flag day) every
column declared `dtype="int"` is rounded via `np.rint` in
`generate_component()` before derivations run and before the
`topology_capture` snapshot, so the recorded value is whole-integer
on disk (`main()` always passes `apply_dtype_int_cast=True` since
the phase-9 flag day removed the independent alias; the
`generate_component` kwarg survives for programmatic callers that
need the pre-cast fractional contrast).
`_validate_metric_spec_schema_metadata` enforces the vocabulary at
import time (`semantic_type ∈ {counter, gauge, ratio, rate}`,
`dtype ∈ {float, int}`, finite numeric bounds,
`min_value <= max_value`).

Within `generate_component()` the cast runs after the anomaly-override
pass and *before* the derivation pass, so derived columns
(`cacheservice.hit_ratio`) consume rounded integer source cells and
match what the CSV writes. It also runs *before* the
`topology_capture` snapshot, so downstream coupling signals see the
same integer values the CSV records (cache miss ratios derived from
`cache_hits` / `cache_misses` are therefore computed from the
int-cast values, not the pre-cast floats; the qualitative behavior
is unchanged because the ratio is bounded in [0, 1] in either case).

After the phase 9 scenario re-tune there are no known validator
violations on default output: the LLM context-overflow scenario
(`llm_weekend_batch`) now saturates `context_overflow_rate` toward
0.97 — inside its declared `max_value=1` — instead of the historic
8.5, while staying 3.2–6.7 sigma above the 0.3 natural baseline so
the context-window saturation pattern remains unmistakable.
`tests/test_validate_output.py` pins the empty violation sets for
both default runs.

### Output validator (the `validate` subcommand)

`validate PATH` runs the `validate_impl.py` validator orchestrator in a
standalone mode (peer of the `combine` subcommand) that loads
`PATH/schema.json` and runs every check the validator knows about against the
artifacts in `PATH`. `validate_output` returns `list[Violation]`; each
violation has `component`, `metric`, `kind`, and `message` fields, and
`str(violation)` reproduces the historic prose byte-for-byte so CLI output and
existing substring consumers stay compatible.

- `_validate_required_files_present` — every declared file is on disk.
- `_validate_no_unknown_files` — every file on disk is declared (mirrors
  `_pre_clean_output_dir`'s registry intent; dot-prefixed sidecars such as
  `.DS_Store` are tolerated, non-dot artifact-like files such as `*.tmp` still
  hard-fail, and `schema.json` is always allowed even if undeclared so the
  validator can bootstrap).
- `_validate_anomalies_sorted` — `anomalies.csv` rows are non-decreasing
  by `timestamp`.
- `_validate_component_row_count` — data rows ≤ `rows_per_component`
  plus the DST splice extras when applicable; under-emission is checked
  against an 8-sigma band around the expected drop count. Phase 8: when the per-component schema declares `dimensions`, both
  the upper bound and the under-emission band are multiplied by
  `cardinality` so the Phase 2 long-form CSV (N copies of each row,
  one per instance) sits inside the band.
- `_validate_component_timestamp_coverage` — every row's timestamp is in
  `[START, START + total_seconds)`.
- `_validate_component_cells` — header column order matches the schema's
  MetricSpec list; each cell parses as float, is finite (NaN/±inf cells
  are reported as `non_finite` violations — without the guard a NaN
  cell passes every range check silently because every comparison
  against NaN is False, and a NaN/inf cell in a `dtype="int"` column
  crashes `round()` instead of reporting), falls in
  `[min_value, max_value]` when declared, is whole-integer (modulo
  3-decimal CSV precision) when `dtype="int"`, and is ≥ 0 when
  `semantic_type` is `counter` or `rate`. Each unique
  `(metric, kind)` violation reports once per CSV so the output stays
  bounded. Phase 8: when the per-component schema declares
  `dimensions`, the expected header is
  `("timestamp", *_INSTANCE_DIMENSION_COLUMNS, *metric_names)` to
  match the Phase 2 long-form per-component CSV; metric cells start
  at index `1 + len(_INSTANCE_DIMENSION_COLUMNS)` in that branch, and
  the dim cells themselves (string-valued id/host/pod/az/region/tenant)
  are skipped by the numeric range checks.
- `_validate_component_derivations` — for every metric whose schema entry
  declares a `derivation`, recompute the value from its source columns
  and assert agreement within `_VALIDATE_DERIVATION_TOLERANCE` (0.01).
  A non-finite value on either side (a NaN derived cell, or a NaN
  source flowing through the recomputer) is itself a violation — NaN
  would otherwise poison the tolerance comparison and validate clean.
  Dispatched by `(component, metric)` via the `_RECOMPUTERS` table —
  add a `DERIVATIONS` entry (generator) and a `_RECOMPUTERS` entry
  (validator) in lockstep. Phase 8: the `name_to_col`
  recomputer-lookup index is offset by
  `1 + len(_INSTANCE_DIMENSION_COLUMNS)` when the schema declares
  `dimensions`, so the recomputer reads the right cell from the
  long-form row instead of a dim string.
- `_validate_long_form_dimensions` (phase 8) — when *any*
  per-component schema declares `dimensions`, verify both
  `gauges.csv` and `combined_metrics_unified.csv` (when declared in
  `schema.files`) carry the 10-column long-form header
  `timestamp, component, id, host, pod, az, region, tenant, metric,
  value`. Mirrors the writer's any-of dispatch predicate; when no
  component has dimensions the check is a no-op so today's
  dimensionless validator behavior is unchanged.
- `_validate_topology_coupling` (phase 7) — for every edge in
  the schema's `topology` section with a numeric weight, compute the
  Pearson correlation between the source's canonical load metric and
  the target's canonical load metric (from `_TOPOLOGY_LOAD_METRICS`)
  and flag the edge when it falls below the per-edge threshold
  (`Edge.correlation_threshold`, defaulting to
  `_TOPOLOGY_DEFAULT_CORRELATION_THRESHOLD = 0.85`). Skipped silently
  under `metadata.topology_mode == "independent"` (which decouples by
  construction), when the schema has no `topology` block (older v1
  docs), on callable-weight edges (the per-row weight signal is the
  dominant contributor, not the upstream load), and when the aligned
  row count falls below `_TOPOLOGY_MIN_ALIGNED_ROWS` (narrow `--components` or
  coarse `--interval-seconds`). Anomaly windows from `anomalies.csv` are
  excluded via `_read_anomaly_exclusion_windows` and
  `_filter_windows_for_pair`: each `[span_start, span_end]` is padded
  by `_TOPOLOGY_CORRELATION_EXCLUSION_PAD_SECONDS = 30` and applied
  only to windows whose `(component, metric)` matches the source's
  canonical, the target's canonical, *or* any other upstream
  contributor's captured load columns (so an anomaly on
  `cacheservice.cache_misses` excludes the corresponding rows from
  the `apigateway -> database` correlation, since the cacheservice
  contribution is composed into `database.queries_per_sec` via the
  callable edge). A zero-variance source or target column is treated
  as a coupling regression (Pearson is undefined; the validator emits
  a violation naming the side). Phase 8 makes
  `_read_component_metric_column` collapse per-instance duplicates to
  one `(timestamp, mean)` per unique timestamp so the dim-aware
  long-form CSV (multiple rows per timestamp, one per instance, with
  instances written as sequential per-instance blocks rather than
  chronologically interleaved) keeps the timestamp axis monotonic for
  the downstream `_compute_anomaly_keep_mask` forward-sweep. Under
  the default fan-out (instances share the baseline) the mean equals
  any single instance's value, so the N=1 path is byte-identical.

CLI semantics: default mode hard-fails (`exit 1` on any violation);
`--warn` downgrades to a stderr report and
`exit 0`. Structurally exclusive with the `combine` subcommand (one
subcommand token dispatches per invocation).

### Gauge metric file (`gauges.csv`)

`write_gauges_csv(component_csv_paths, output_path)` is the file peer of the
OTEL gauge stream (`stream_otel_gauges`). Both walk the same per-component
CSVs and merge them chronologically with `heapq.merge` on the parsed
timestamp.

Layout is decided by header inspection via
`_scan_component_csv_headers`:

- **4-column shape (default, dimensionless input).** Every per-component
  CSV is the classic `timestamp, m0, m1, …` shape — the `N=1`
  anonymous-instance case. The writer emits one row per
  `(timestamp, component, metric, value)` tuple. Equal-timestamp ties
  tie-break on sorted component name (the writer sorts
  `component_csv_paths` internally so the tiebreaker holds regardless of
  how the caller built the dict), then per-component CSV column order
  (`MetricSpec` order). Byte-identical to the pre-existing output, so
  existing locked SHA-256 golden hashes at 1d and 7d still apply.
- **10-column shape (phase 5, dimensioned input).** Any per-
  component CSV carries the multi-instance `id, host, pod, az, region,
  tenant` prefix (the `--instances-per-component N > 1` shape from
  Phase 2). The writer emits
  `timestamp, component, id, host, pod, az, region, tenant, metric,
  value`. The whole long-form merge — source-building, FD preflight,
  `(component, instance_dims)` sort/tie-break, header, and empty-cell skip —
  lives in the shared `csv_layout.write_long_form_merge`
  (07-06-long-form-merge-writer-dedupe); `write_gauges_csv` is a thin wrapper
  that passes its sorted component list + layout in. Per-(component, instance)
  iterators come from
  `_iter_component_instance_rows`; the per-instance ``(dim_tuple,
  start_offset)`` pairs per file come from
  `_scan_instance_block_layout` — a one-pass dim-only scan that
  detects the contiguous per-instance blocks `generate_component`
  writes and records each block's byte-offset start so the iterator
  can ``seek()`` instead of re-scanning. Tie-break order on equal timestamps is
  `(component, instance_id, metric)` — sources are sorted by
  `(component, instance_id)` before `heapq.merge`, and within each row
  the inner metric loop walks columns in `MetricSpec` order.

Dropped CSV rows are absent from the file in both shapes (long form
encodes "this measurement was emitted" via row presence), the same way
`stream_otel_gauges` never sees them.

**File-descriptor pre-flight (long-form path only).** The long-form
merge holds one open file handle per `(component, instance)` source
for the lifetime of the merge — `heapq.merge` primes every iterator,
so at max fan-out (14 components × 20 instances = 280 sources) a run
can exceed the default macOS soft limit (256). Before the merge,
`_ensure_long_form_fd_capacity(len(sources))` reads `RLIMIT_NOFILE`,
raises the soft limit to fit (capped by the hard limit), and otherwise
exits with a message naming the needed count and the user-facing
levers (`--instances-per-component`, `--components`, `ulimit -n`).
On Windows the helper no-ops — `open()` surfaces the real error at
write time. The wide-form / 4-column paths never trip the guard
because they stream a single handle per component.

Parity with `stream_otel_gauges` has one intentional asymmetry: the file
writer passes raw cell strings through verbatim (so the byte hash never
depends on Python's `str(float)` repr), whereas `stream_otel_gauges`
`float(raw)`-coerces and silently skips unparseable cells. In practice
`generate_component` only writes finite floats, so both paths emit the
same data points — the difference only matters for hand-edited CSVs.

Both gauge paths are intentionally mutually exclusive with
`--inject-dst-artifact-day > 0` (the DST splice produces non-monotonic CSV
timestamps that break `heapq.merge`); the parser rejects the combination for both
`--otel-send` selections including `gauges` and
`--emit ...,gauges` up front. `--otel-send gauges` (alone) is a CLI mode
that implies the OTEL gauge stream but skips `stream_otel_signals()`,
so receivers that only accept Gauge payloads do not see the anomaly
counter/log/trace stream first. Supporting DST here would require a new
non-monotonic timestamp batching model; do not remove the parser gate as a
local "compatibility" fix.

`gauges.csv` is opt-in via `gauges` in `--emit` (which the
parser enforces alongside `metrics`); the `combine` subcommand /
the `combine` subcommand does not
regenerate it. The end-of-run `Done -` summary additionally prints
`Gauge rows written: N to gauges.csv` so a CI run records how many
data points landed in the file. Locked SHA-256 golden hashes at 1d and
7d for the 4-column shape and at 1d for the 10-column N=3 shape live
in `tests/test_gauges_file.py`.

### OTEL dimension attributes (Phase 6)

`_INSTANCE_DIMENSION_COLUMNS = ("id", "host", "pod", "az", "region", "tenant")`
(defined alongside the `Instance` dataclass) is the single source of
truth for the Phase 2 multi-instance CSV column block —
`generate_component` writes the prefix in this exact order and the
Phase 2 validator's `_INSTANCE_DIMENSION_FIELDS = _INSTANCE_DIMENSION_COLUMNS[1:]`
derives the 5-field view (without `id`) used by
`_validate_instance_list` for None-or-str + CSV-safety checks. Three
consumers re-use the column constant:

- `_iter_component_rows(component, csv_path)` yields a 4-tuple
  `(timestamp_str, component, [(metric_name, value), ...], dimensions)`
  where `dimensions` is a `dict[str, str]` carrying the non-empty
  cells from the row's dimension prefix. The detector treats the
  block as present only when `header[0] == "timestamp"` and columns
  `[1 : 1 + len(_INSTANCE_DIMENSION_COLUMNS)]` of the header equal the
  tuple verbatim — a partial / reordered header is treated as "no dimensions" and the offending columns
  flow into the metric path, where `float(raw)` will naturally skip
  them. Dimensionless CSVs (the default) yield an empty dict so
  downstream consumers can treat the field as always present.
- `stream_otel_gauges` threads the dimensions dict through into each
  gauge batch entry as `entry["dimensions"]`. Empty cells are already
  dropped at the reader so the batch entry never carries empty
  values.
- `_build_otlp_gauge_payload` and `_build_otlp_gauge_protobuf` emit
  each non-empty `(key, value)` from `entry.get("dimensions") or {}`
  as a string attribute alongside the base three (`metric.name`,
  `component`, `signal.type`). `None` and empty-string values are
  skipped defensively at the builder layer too (single source of
  truth: the reader drops them once, the builder drops them again so
  hand-built test batches behave the same way).
- `_build_otlp_metric_payload` and `_build_otlp_metric_protobuf` (the
  anomaly-counter path consumed by `stream_otel_signals`) accept the
  same `entry.get("dimensions") or {}` shape and emit non-empty
  values as string attributes alongside the base four (`event.id`,
  `signal.type`, `metric.name`, `component`). In v1 no anomaly row
  carries dimensions — `anomalies.csv` is single-instance through
  Phase 4 — so the extension is structurally inert today but keeps
  the JSON and protobuf payload shapes aligned with the gauge path.
  Log and trace builders are out of scope for v1 and remain on the
  base attribute set.

`tests/test_otel_gauges.py` pins the JSON and protobuf builder shapes
(per-row attribute emission + empty-cell omission), the
`_iter_component_rows` 4-tuple contract for both dimensioned and
dimensionless CSVs, and the end-to-end `--instances-per-component 3`
→ `pod=pod-N` invariant on the live OTLP stream.

### Metric specs (value generation)

Each component's columns are declared in `COMPONENTS` as a list of `MetricSpec(name,
base, jitter, multiplier=…, additive=…, clip_min=…)`. The baseline column is built by
`_natural_column()`:

```
value = (base + jitter * randn(n)) * multiplier(ts, elapsed) + additive(ts, elapsed)
```

`multiplier` and `additive` must accept numpy arrays so the whole column generates in
one pass. Use `_daily_sine(amplitude)` for natural 24h variation and
`_llm_business_hours` for the LLM business-hours envelope.

### Derived metrics

Some columns are physically derived from siblings and must stay consistent with
them under every anomaly override. `generate_component()` enforces this after the
natural-value pass and the anomaly override loop, before rounding/formatting:

- `cacheservice.hit_ratio = 100 * cache_hits / (cache_hits + cache_misses)`
  (zero-denominator → 0). Anomalies that want to influence the cache hit ratio
  must therefore drive `cache_hits` and/or `cache_misses`, not `hit_ratio`
  directly; otherwise the override is silently overwritten by the derivation.

When adding a new derived-metric rule, keep it inside `generate_component()` so
the recomputation runs once per column, after every override has settled.

### Anomaly injection schema

Anomaly specs are dicts with:

- `time_offset` — seconds from `START` (e.g., `2*3600 + 15*60` = 02:15:00, or
  `N*SECONDS_PER_DAY + …` for multi-day).
- `metric` — name of the metric field to overwrite at the matched row/span.
- `description` — human-readable description; flows into `anomalies.csv`.
- `generator` — `lambda ts, idx: value` returning the anomalous value.
- `duration_seconds` (optional) — span length; omitted/0 keeps single-row behavior.
- `shape` (optional) — one of `step` (default), `ramp_linear`, `ramp_exp`,
  `sustained`, `sawtooth`, `sine`.
- `shape_params` (optional) — shape-specific params (`start/end`, `period_s`,
  `amplitude`, `midline`, etc.).
- `instance_filter` (optional, Phase 4) — restricts which instances
  the override applies to. Accepted forms:
  - omitted / `None` → applies to every active instance (default; preserves
    Phase 2 byte-identical output when no filter is set).
  - iterable of `str` ids (list, tuple, frozenset) → applies only to
    instances whose `Instance.id` is in the set.
  - callable `(Instance) -> bool` → per-instance predicate.
  Scalars (int/float/bool), bare strings (would iterate characters), dicts,
  and iterables containing non-string elements are rejected at import time by
  `_validate_scenario_spec`. Zero-match at runtime emits a `WARNING` on stderr
  and skips the spec (no manifest entry). Non-zero-match adds one manifest
  entry regardless of how many instances matched.

Multiple anomalies can fire at the same timestamp across different metrics. The
anomaly registry is collected into the manifest file.

Production code does not call `register_cascade()`: `_apply_scenarios()` reads
each scenario's `cascade_specs` and appends them directly into the per-run
`RunContext.cascading_anomalies` dict. The `register_cascade(target_component,
time_offset, metric, description, generator, *, cascade_registry=…)` helper
exists for tests that need to build a cascade registry without composing a full
`Scenario`; callers must pass `cascade_registry=` explicitly (the module-level
registry was removed). Cascades simulate blast radius (auth →
gateway, cache → DB, DB → API/auth, MQ → API/DB, LLM → DB/cache/API). Cascades
are single-row step writes only — express ramps/sustained spans as primary
specs in `primary_specs`, not in `cascade_specs`.

### Topology graph

`TOPOLOGY: dict[str, list[Edge]]` declares the directed service-call graph
alongside `COMPONENTS`. Phase 1 landed the constant and its
import-time validator; phase 2 added the
topology-coupling consumer (see "Generation order" below)
that re-shapes downstream RPS baselines from upstream RPS columns. The
consumer was opt-in through phase 5, flipped to the default in
Phase 6, and became the only mode at the phase-9 flag day (the
`--topology-mode independent` contrast alias was removed).
Phase 3 extended coupling to every front-half fan-out edge.
Phase 4 reads `Edge.saturation` and adds a logistic-shaped
latency multiplier and error offset onto each downstream's
latency-family and error-family `MetricSpec` (see "Saturation
feedback" below). Phase 5 closes the v1 graph by promoting
the `apigateway → llm_analytics` placeholder to a real coupling +
saturation edge so the LLM token-throttle reads as load-driven
saturation; see "LLM token-throttle" below for the decision to keep
apigateway as the metering authority instead of introducing a
synthetic `token_limiter` virtual node.

Two dataclasses model the edges:

- `Edge(target, weight=1.0, saturation=None, signal=None,
  correlation_threshold=None)` — frozen.
  `target` is a `COMPONENTS` key. `weight` is either a constant
  `float` (fan-out share, where the outgoing weights of a routing
  source sum to 1, or any non-negative scalar for amplification edges)
  or a callable `(np.ndarray) -> np.ndarray` that derives the per-row
  weight from a per-row scalar signal (e.g. cache-miss ratio driving
  the cache→database fan-out). `signal` is the per-edge
  `(dict[str, np.ndarray]) -> np.ndarray | None` callable that produces
  that scalar signal from the upstream's captured load columns;
  required iff `weight` is callable, must be `None` for constant
  `weight`. Returning `None` from `signal` means "skip this edge" so a
  `--metrics-per-component` trim of a required input column degrades
  gracefully. `correlation_threshold` is a validator-only override
  (phase 7) for the minimum Pearson correlation
  `_validate_topology_coupling` requires between the source's
  canonical load metric and the target's canonical load metric; `None`
  (the default) falls back to
  `_TOPOLOGY_DEFAULT_CORRELATION_THRESHOLD = 0.85`. The field does
  not affect generation and is ignored on callable-weight edges
  (which are skipped by the coupling check). `_validate_topology()`
  smoke-tests every callable weight with a 3-element `np.ndarray` and
  probes every `signal` with a per-key captured-column dict built
  from `_TOPOLOGY_LOAD_METRICS` so a zero-arg / scalar-only lambda or
  a mis-shaped signal fails at import time rather than corrupting
  phase 2's vectorized column writes. The same validator rejects
  `correlation_threshold` values that aren't finite, that fall
  outside the half-open interval `(-1, 1]`, or are `bool`.
- `SaturationParams(midpoint, steepness, latency_gain=0.0, error_gain=0.0)`
  — frozen. Parameters of a logistic response curve consumed by
  `_apply_saturation()`. Zero gains declare the saturation
  point structurally without contributing to the target's metrics;
  after phase 5 the v1 graph no longer has any zero-gain
  saturating edges, so the "structurally inert" branch only triggers
  for synthetic test edges.

The v1 graph (phase 1 declarations + phase 4/5 saturation tuning):

- `loadbalancer → apigateway` (constant weight `1.0`, saturation
  `midpoint=860, steepness=6, latency_gain=0.4, error_gain=0.010`).
- `apigateway → authservice` (`0.3`, saturation `midpoint=760,
  steepness=6, latency_gain=0.5, error_gain=0.012`).
- `apigateway → cacheservice` (`0.4`, saturation `midpoint=760,
  steepness=6, latency_gain=0.3, error_gain=0.008`).
- `apigateway → database` (`0.3`, saturation `midpoint=760,
  steepness=6, latency_gain=0.6, error_gain=0.015`).
- `apigateway → llm_analytics` (constant weight `1.0`, saturation
  `midpoint=760, steepness=6, latency_gain=0.55, error_gain=0.015`).
  Phase 5: under realistic mode, couples
  `llm_analytics.input_tokens_per_sec` to apigateway RPS, and lifts
  `avg_llm_latency_ms` / `p95_llm_latency_ms` / `llm_api_error_rate`
  as apigateway saturates the token budget.
- `cacheservice → database` — callable weight (cache-miss ratio); no
  saturation in v1.

**Generation order.** Since the phase-9 flag day removed the
`--topology-mode independent` contrast alias, `main()` has exactly
one generation order:

- realistic (the only mode) — topological order via
  `_topology_generation_order(args.components)`. Kahn's algorithm
  walks reverse-adjacency of `TOPOLOGY` restricted to
  `args.components`; ties break on `COMPONENTS` insertion order so
  the result is deterministic. As each component finishes,
  `generate_component()` stashes its post-natural / post-anomaly /
  post-derivation load-metric columns (pre-round; full float
  precision for `dtype="float"` columns, post-`np.rint` whole
  integers for `dtype="int"` columns after the phase 6
  integer-cast bundle so the captured signal matches what the CSV
  emits) into a shared `upstream_arrays: dict[str, dict[str,
  np.ndarray]]` keyed by `(component_name, metric_name)`. The set
  of captured metrics per component is declared in
  `_TOPOLOGY_LOAD_METRICS` as a `(canonical, supplementary)` tuple:
  `canonical` is the load metric a constant-weight edge from the
  component reads; `supplementary` lists additional captured columns
  the component's outgoing edges' `Edge.signal` callables consume
  (e.g. cacheservice exposes `("cache_hits", ("cache_misses",))`).
  Before generating a downstream component,
  `_compose_topology_coupled_specs` rewrites each of the downstream's
  load metrics (canonical + supplementary) via the incoming edges:
  - **Constant-weight edges** — `contribution = (upstream /
    upstream_base) * downstream_base * w_norm` where the upstream
    column is the source component's *canonical* load metric and
    `w_norm = w / Σw` normalizes so the combined constant term
    equals `downstream_base` at natural upstream load. At least one
    constant-weight edge must have a non-zero captured upstream for
    this path to fire.
  - **Callable-weight edges** — each callable-weight `Edge` carries
    its own `signal: Callable[[dict[str, np.ndarray]], np.ndarray
    | None]` that derives a per-row scalar from the upstream's
    captured columns (e.g. the `cacheservice → database` edge uses
    the module-level `_cache_miss_ratio_signal` to compute
    `cache_misses / (cache_hits + cache_misses)`). The composer
    calls `edge.signal(upstream_cols)`; a `None` return means
    "skip this edge" (e.g. `--metrics-per-component` trimmed a
    required column). The composer then calls `edge.weight(signal)`
    to produce an additive contribution in the downstream's
    *canonical*-metric units; the contribution is computed once per
    component (the signal/weight evaluation is metric-invariant) and
    applied only to the canonical load metric — a supplementary
    coupled metric has a different base, so it never receives the
    callable array (a supplementary metric whose only incoming
    contribution would be callable stays on its natural baseline).
    `_validate_topology` enforces the pairing: callable weight
    requires a `signal`, constant weight forbids one, and the
    validator probes the `signal` with a captured-column dict
    built from `_TOPOLOGY_LOAD_METRICS[source]` so a mis-shaped
    signal fails at import time.
  The final coupled column is `constant_contrib + callable_contrib +
  rng.normal(0, _TOPOLOGY_COUPLE_NOISE_STD, n_rows)` (with
  `callable_contrib` present on the canonical metric only). The original
  MetricSpec's declarative metadata (unit, semantic_type, min/max,
  dtype, derivation, clip_min) survives via `dataclasses.replace`;
  only `base`, `std`, `multiplier`, and `additive` change.
The deprecated `--topology-mode independent` no-topology contrast
alias was removed at the phase-9 flag day (the flag no longer
parses). The pure-natural baseline that tests previously obtained
via the alias now comes from `tests/conftest.py`'s
`_generate_natural_baseline` (`natural_one_day_run` /
`natural_full_metrics_one_day_run` fixtures): `generate_component`
invoked directly with the raw `COMPONENTS` specs over one shared
RNG stream in `COMPONENTS` insertion order — no coupling, no
saturation, no anomalies. All locked SHA-256 hashes in `tests/`
target realistic output.

Anomaly overrides apply on top of the coupled baseline: the
two-pass pipeline (natural → anomaly overrides → derivations →
capture → round → drop → format) is unchanged inside
`generate_component()`, so a scenario primary on
`apigateway.requests_per_sec` still rewrites the cell at its row
index after coupling has set the baseline.

**Cascade-vs-topology overlap.** Several `SCENARIOS` already encode
pairwise blast-radius via `cascade_specs` (auth → gateway, cache → DB,
DB → API/auth, MQ → API/DB, LLM → DB/cache/API). The topology graph is
an orthogonal structural view: it describes *normal* request flow, not
anomaly propagation, so the two are intentionally allowed to overlap.
Cascades remain the path for "metric X drops at exactly row Y"
behaviors; topology is the path for "load on source raises the
downstream baseline" (phase 2/3) and "load on source elevates
downstream latency + error rate" (phase 4 saturation). Phase 3 expanded coupling to all front-half fan-out edges, so
`authservice.login_attempts`, `cacheservice.cache_hits/cache_misses`,
and `database.queries_per_sec` are all coupled under realistic mode.
Phase 4 extends realistic mode to latency and error
columns: cascade overrides (`error_rate`, latency, `cpu_util_pct`)
now share the same column space as the saturation offset, but the
cascade override path *replaces* the cell at the targeted row (post
saturation, since the override is applied after the natural-column
build), so the cascade value still wins at exactly that row.

**Phase 9 catalog re-tune.** The saturation lift from phase 4/5 raised the column-wide std of `apigateway.error_rate` (from ~0.018 to ~0.040) and `authservice.error_rate` (from ~0.018 to ~0.050), pushing eleven hand-tuned cascade and primary generator values close to or below the new noise floor. Those eleven specs (8 from the initial audit + 3 surfaced by the regression test) were re-tuned to clear the floor by >3σ under realistic mode: `api_cpu_saturation` (primary 0.25), `db_stall` (primary 0.35, cascade 0.30), `lb_flapping` (cascade 0.30), `mq_jam` (primary 0.25), `vectorstore_pressure` (cascade 0.15), `payment_5xx` (cascade 0.28), `regional_failover_storm` (cascade 0.40), `llm_provider_outage` (cascade 0.35), `storage_layer_pressure` (cascade 0.30), and `network_partition_az_split` (cascade 0.40). `tests/test_scenario_deviation.py` is the regression guard: it walks every `SCENARIOS` entry under realistic mode, compares the active CSV against an `--exclude-scenarios <slug>` baseline run that fires zero anomalies, and asserts every recorded `anomalies.csv` row deviates by >1σ. A future saturation re-tune or new edge that quietly lifts a
column's std past a generator's headroom will fail this test on the
specific row that no-ops.

No `SCENARIOS` cascades were structurally removed —
they are kept in place per the decision even where the
saturation feedback would now produce a similar downstream effect.
The cascade override is a single-row step write applied *after*
saturation, so it still pins the targeted cell to a specific value
regardless of upstream load; saturation only lifts the surrounding
band. Cascades that target `error_rate` on `apigateway` or
`authservice` are the most overlap-prone (the saturation curve also
elevates `error_rate` on those components under load) but remain
distinguishable: the cascade override produces a sharp step at the
recorded row, while saturation produces a smooth load-shaped band
underneath it.

### Saturation feedback (realistic topology, phase 4)

Each saturating edge (`Edge.saturation is not None` and at least one
non-zero gain) contributes a logistic-shaped response to its downstream
component, computed by `_apply_saturation(upstream_load, sat)`:

```
utilization        = max(upstream_load, 0) / sat.midpoint
                     clipped to [0, _SATURATION_MAX_UTILIZATION]
logistic           = 1 / (1 + exp(-sat.steepness * (utilization - 1)))
latency_multiplier = 1 + sat.latency_gain * logistic
error_offset       = sat.error_gain * logistic
```

`upstream_load` is the *upstream* component's primary load column
captured in `upstream_arrays` (per `_TOPOLOGY_LOAD_METRICS`); the
downstream's own load is still being assembled at saturation time, so
the curve cannot read it directly. The utilization clamp keeps
`np.exp` numerically stable for arbitrary load magnitudes (logistic
already exceeds 0.99 at utilization = 2 with steepness = 5, so a 5x
cap has no practical effect on the shape).

`_TOPOLOGY_SATURATION_TARGETS[downstream]` declares which of the
downstream's metrics receive the saturation effect:

- `apigateway` → latency `avg_response_time_ms`, `backend_latency_ms`;
  error `error_rate`.
- `authservice` → latency `avg_auth_latency_ms`; error `error_rate`.
- `cacheservice` → latency `avg_cache_latency_ms`; error `error_rate`.
- `database` → latency `read_latency_ms`, `write_latency_ms`; error
  `error_rate`.
- `llm_analytics` → latency `avg_llm_latency_ms`, `p95_llm_latency_ms`;
  error `llm_api_error_rate` (the LLM-specific error column the
  catalog exposes, not the generic `error_rate`). Phase 5.

`_compose_topology_saturation_specs(component, specs, upstream_arrays,
n_rows)` runs immediately after `_compose_topology_coupled_specs` in
the realistic-mode generation loop. It sums incoming saturating
contributions — multiplicatively for the latency factor (each edge
layers an additional load-dependent slowdown) and additively for the
error offset (each edge contributes its own failure surface) — then
composes the resulting per-row arrays on top of the metric's existing
`multiplier` / `additive` via lambda closures. The natural seasonal
patterns (e.g. `_daily_sine`, `_llm_business_hours`) therefore stay
visible underneath the saturation curve. Only `multiplier` and
`additive` change; `std`, `clip_min`, and the declarative schema
metadata pass through unchanged.

**Tuning rationale (per-edge).** Midpoints are set to ~80% of each
upstream's natural peak load (`base + ~3σ`):

- `loadbalancer → apigateway`: loadbalancer base = 900 rps, peak
  ≈ 1080, midpoint = 860 → utilization ~1.05 at natural load
  (logistic ~0.6 with steepness = 6).
- `apigateway → {authservice, cacheservice, database}`: apigateway
  base = 800 rps, peak ≈ 950, midpoint = 760 → same shape.

`latency_gain` scales with each downstream's sensitivity: `database`
gets the largest (`0.6`, heavy I/O), `authservice` next (`0.5`,
per-request crypto work), `apigateway` (`0.4`, request routing),
`cacheservice` smallest (`0.3`, in-memory ops). `error_gain` follows
the same ordering, kept inside `[0.005, 0.02]` so the saturation
offset alone cannot push `error_rate` above 1.0 (worst case
`base + 4σ + error_gain` stays well below the declared
`max_value=1`).

**Bounds and cap tests.** `latency_multiplier ∈ [1, 1 + latency_gain]`
(always positive given non-negative gains; latency never flips sign);
`error_offset ∈ [0, error_gain]` (bounded by the per-edge gain so the
saturation contribution alone cannot exceed the gain). End-to-end
tests in `tests/test_topology_saturation.py` assert both invariants on
the realized CSV columns.

The no-topology contrast baseline for the saturation tests is the
direct-natural fixture in `tests/conftest.py` (the independent alias
and its `LEGACY_INDEPENDENT_ONE_DAY_HASHES` pins were removed at the
phase-9 flag day, along with the explicit-flag byte-identity tests —
the flag no longer parses, so realistic output is pinned solely by the
locked default-run hashes).

### LLM token-throttle (realistic topology, phase 5)

Phase 5 closes the v1 topology graph by promoting the
phase-1 `apigateway → llm_analytics` placeholder into a real
coupling + saturation edge. The edge sits inside the same
phase-3 / phase-4 machinery as the front-half fan-out — no new
generator branch, no new validator, no new file format.

**Decision: no synthetic `token_limiter` virtual node.** The issue
left the upstream choice open (apigateway vs. a synthetic
`token_limiter` node that does not appear in `COMPONENTS`).
Apigateway is the natural metering authority for LLM-bound traffic
in the v1 graph: every LLM call enters the system through it, so its
RPS is a faithful proxy for the token budget being consumed. A
virtual node would require `_validate_topology` to accept upstream
keys outside `COMPONENTS`, would not produce any observable column
of its own, and would not improve the saturation shape (apigateway
RPS already drives every front-half edge). The synthetic-node path
is documented here only to record the decision; revisit it if a
future LLM scenario needs token-counting behavior independent of
apigateway throughput.

**Coupling.** `_TOPOLOGY_LOAD_METRICS["llm_analytics"] =
("input_tokens_per_sec", ())` makes `input_tokens_per_sec` the
canonical load metric for the LLM (no supplementary captures; the
canonical-shape `(canonical_metric, supplementary_tuple)` rule is
preserved). The
edge weight is positive (`1.0`); the per-downstream renormalization
in `_compose_topology_coupled_specs` collapses single-incoming
edges to `w_norm = 1.0`, so any positive weight is structurally
equivalent. Token throughput is the right unit here (not request
rate): the token budget governs tokens/second, not requests/second,
and the larger downstream baseline (25 000 tokens/s vs. 45
requests/s for the LLM RPS) keeps the upstream-driven signal well
above the absolute coupling noise floor
(`_TOPOLOGY_COUPLE_NOISE_STD = 5.0`) and clears the issue's
`>= 0.85 Pearson` correlation gate against
`apigateway.requests_per_sec` on the 1-day default seed.

**Saturation.** The edge's `SaturationParams` sit in the same
phase-4 issue ranges as the other front-half edges (`midpoint=760`
in apigateway RPS units, `steepness=6`, `latency_gain=0.55` between
authservice 0.5 and database 0.6, `error_gain=0.015` inside
`[0.005, 0.02]`). `_TOPOLOGY_SATURATION_TARGETS["llm_analytics"]`
covers both the default-emitted `avg_llm_latency_ms` and the
supplemental `p95_llm_latency_ms`; the additive error offset goes
onto `llm_api_error_rate` (the LLM-specific error column the
catalog exposes — not the generic `error_rate`, which
`llm_analytics` does not declare).

**Tests.** `tests/test_topology_llm.py` pins:

- structural invariants on the `apigateway → llm_analytics` edge
  (active positive weight, non-zero gains, ranges);
- registry entries in `_TOPOLOGY_LOAD_METRICS` and
  `_TOPOLOGY_SATURATION_TARGETS`;
- `>= 0.85 Pearson` correlation between
  `apigateway.requests_per_sec` and
  `llm_analytics.input_tokens_per_sec` in realistic mode;
- realistic-mode mean lifts for `avg_llm_latency_ms`,
  `p95_llm_latency_ms` (under `--metrics-per-component 10`), and
  `llm_api_error_rate` against the direct-natural baseline fixtures
  (`natural_one_day_run` / `natural_full_metrics_one_day_run` in
  `tests/conftest.py`);
- caps (latency non-negative, error rate `<= 1.0`); and
- LLM scenarios still fire under realistic mode (no anomaly cell
  overrides are masked by the coupling).

`_validate_topology()` rejects, at import time: unknown source keys,
non-`list` edge containers, non-`Edge` entries, edge targets outside
`COMPONENTS`, callable weights that fail to accept an `ndarray` or
return something other than an `ndarray`, constant weights that
are not finite, non-negative `int`/`float` scalars (`bool` is
rejected explicitly because it is an `int` subclass), callable
weights paired with `signal=None` (or a non-callable `signal`),
constant weights paired with a non-`None` `signal`, `signal`
callables that raise on the captured-column probe, `signal`
callables that return something other than `np.ndarray` or `None`,
and any cycle in the directed `TOPOLOGY` graph (including
self-loops).

Each non-`None` `Edge.saturation` is also validated at import time via
the shared `_validate_saturation_params(sat, *, context=…)` helper:
`midpoint` and `steepness` must be finite positive non-`bool`
`int`/`float`; `latency_gain` and `error_gain` must be finite
non-negative non-`bool` `int`/`float`. `_apply_saturation()` re-runs
the same check at call time so direct callers (tests, future
consumers) cannot smuggle in `NaN`/`inf`/`bool`/negative values.

The companion metric registries are validated at import time by
`_validate_topology_metric_registries()` (defined and invoked right
after `_TOPOLOGY_SATURATION_TARGETS`, which it reads): every
`_TOPOLOGY_LOAD_METRICS` / `_TOPOLOGY_SATURATION_TARGETS` key must be
a `COMPONENTS` key and every named metric must exist in that
component's *full* catalog; every `TOPOLOGY` source with a
constant-weight or saturating outgoing edge must have a
`_TOPOLOGY_LOAD_METRICS` entry; every constant-weight edge's target
must have a `_TOPOLOGY_LOAD_METRICS` entry; and every saturating
edge's target must have a `_TOPOLOGY_SATURATION_TARGETS` entry. The
runtime consumers keep their soft fallbacks (which exist to tolerate
`--metrics-per-component` trims and `--components` subsets), but a
registry typo now fails at import instead of silently generating
decoupled output that only the opt-in `validate` Pearson
check would catch.

Mirror these invariants in `tests/test_topology_registry.py` when
adding new edges or constraints.

### Multi-instance fan-out

`Instance` is a frozen dataclass holding six optional dimension
fields (`id`, `host`, `pod`, `az`, `region`, `tenant`). The active
per-run map lives on `RunContext.instances: dict[str, list[Instance]]`
and is consumed by `generate_component()`: when the list is a single
anonymous `Instance()` (all fields `None`), CSV output is byte-
identical to the pre-Phase-1 baseline (no dimension columns); when
the list has named instances or `len > 1`, every per-component CSV
gains a `(id, host, pod, az, region, tenant)` prefix block and the
row count multiplies by the per-component instance count.

Three flag paths populate `ctx.instances` in `main()` (mutually
exclusive at parse time):

- `--instance-config` absent and `--instances-per-component 1`
  (default) → `{name: list(INSTANCES[name]) for name in COMPONENTS}`,
  where the module-level `INSTANCES[name]` defaults to
  `[Instance()]`. Today's byte-identical output path.
- `--instances-per-component N` (N in `[1, MAX_INSTANCES_PER_COMPONENT]`,
  `MAX_INSTANCES_PER_COMPONENT = 20`) → every component fans out to
  the same `[Instance(id=f"i{k}", pod=f"pod-{k}") for k in range(N)]`.
- `--instance-config PATH` (Phase 3) → per-component
  fan-out is loaded from a YAML (`.yaml`/`.yml`) or JSON (`.json`)
  file via `_load_instance_config(path)`. The file's top-level
  `components` map keys components to lists of `Instance`-field
  dicts; components *not* listed fall back to `list(INSTANCES[name])`
  (anonymous default), so a partial config keeps untouched
  components on the byte-identical path.

`_load_instance_config(path)` is called from `main()` (after
`parse_args` returns) and raises `ValueError` (caught immediately
and re-raised via `sys.exit`) for every schema violation: top-level
value not a mapping, missing `components` key, `components` value
not a mapping, unknown component name (must be in `COMPONENTS`),
per-component value not a list, empty per-component list,
per-component count exceeding `MAX_INSTANCES_PER_COMPONENT`,
non-dict entry, unknown `Instance` field (the comparison handles
non-string YAML keys via `sorted(..., key=repr)` so the error
message stays a `ValueError` rather than a sorting `TypeError`),
and duplicate `Instance.id` within a component (the last check is
delegated to `_validate_instance_list`). YAML parse errors,
JSON `JSONDecodeError`, and OS I/O errors are also caught inside
the loader and re-raised as `ValueError` with the file path
prefix so users see an actionable error rather than a raw
traceback. PyYAML in particular emits multi-line messages with
embedded line/column markers (e.g. `"in \"<unicode string>\",
line 3, column 10"`); the wrapped `ValueError` preserves that
text verbatim because the line/column information is the most
useful debugging signal — the prefix tells the user *which*
file failed, the body tells them *where in the file*. `parse_args` runs
*before* the loader and is responsible only for the flag-shape
checks: the `--instance-config` and `--instances-per-component`
mutually-exclusive `argparse` group, the file existence check,
and the suffix-must-be-in-`{.yaml, .yml, .json}` check. Schema
validation (everything else in the list above) happens later, in
the loader.

PyYAML is an *optional* runtime dependency: the YAML branch imports
it lazily inside `_load_instance_config` and raises a clear
"install with `pip install pyyaml`" error on `ImportError`. JSON
configs work with the stdlib. The `[yaml]` extra in `pyproject.toml`
declares the dependency for users who want YAML support; the `dev`
extra always pulls it in so the test suite can exercise both
formats.

Both multi-instance paths (`--instances-per-component > 1` and
`--instance-config`) are mutually exclusive with
`--inject-dst-artifact-day > 0`: `parse_args` rejects the combination
with a clear message naming the active flag, and
`generate_component()` carries a matching defense-in-depth
`ValueError` for direct callers that bypass the CLI. After
the long-form CSV writer routes through the shared
`_format_csv_row_block` helper, which applies `_splice_dst_artifact`
regardless of the writer branch — the parse-time guard now stands
on design grounds (the multi-instance long-form CSV produces
per-instance row blocks; running `_splice_dst_artifact` per block
would surface non-monotonic timestamps inside each block, which
`heapq.merge` in `gauges.csv` / `combined_metrics_unified.csv`
cannot resolve), not on the earlier correctness gap where the
long-form path silently dropped the duplicated hour entirely.

When adding fields to `Instance`, add the new field name to
`_INSTANCE_DIMENSION_COLUMNS`. Both the `_load_instance_config`
validator (`_valid_instance_fields = frozenset(_INSTANCE_DIMENSION_COLUMNS)`)
and the `Instance(**{f: entry.get(f) for f in _INSTANCE_DIMENSION_COLUMNS})`
constructor pick the new field up automatically, so config-key
acceptance and constructor population stay in lockstep without a
second edit. The remaining lockstep edit sites are: (1) the
README CLI table row example (cosmetic, lists supported keys for
users), and (2) `_validate_instance_list` if the new field needs
uniqueness or shape checks beyond what the dataclass enforces.

### Scenario registry

`SCENARIOS: dict[str, Scenario]` holds every anomaly scenario in the catalog. There
are no legacy `anoms_*` module-level lists; all specs live in `Scenario` entries.
`scenario_catalog.py` owns the ordered registry, `scenario_builders.py` owns
the frozen model and spec builders, `scenario_validation.py` owns validation,
and `scenarios_impl.py` owns selection/composition. `legacy.py` re-exports the
canonical objects and delegates through a named live getter so tests that patch
`legacy.SCENARIOS` remain visible without any extracted module importing
`legacy`. The catalog is intentionally one data-only module even though it
exceeds 800 lines; splitting it would obscure the ordering contract below.
`_apply_scenarios()` in `main()` is the single point that populates
`component_anomalies` and `cascading_anomalies`. Each `Scenario` bundles:

- `id` — slug, must match the dict key.
- `name` — human-readable label.
- `severity ∈ {low, medium, high}` — controls which `--signal-level` activates it.
- `days_required` (positive int) — minimum `--duration-days` at which any of
  the scenario's specs becomes in range. Must equal the day index (1-based) of
  the earliest `time_offset` across all primary and cascade specs;
  `_validate_scenarios_registry` enforces this equality at import time
  (`test_scenarios_days_required_valid` mirrors the same invariant).
- `category` — free-form label for documentation/filtering.
- `components_touched` — must equal exactly the set of components referenced
  by `primary_specs` + `cascade_specs`; `_validate_scenarios_registry`
  enforces this at import time
  (`test_scenarios_components_touched_matches_specs` mirrors the same invariant).
- `primary_specs` — list of `(component, spec_dict)` pairs, same dict shape as the
  anomaly injection schema above.
- `cascade_specs` — list of `(target_component, cascade_dict)` pairs; each
  `cascade_dict` has `time_offset`, `metric`, `description`, and `generator`
  (no `shape`/`shape_params` — cascades are single-row steps).
  Both primary and cascade dicts may additionally carry an optional
  `instance_filter` (Phase 4) — see the
  [anomaly injection schema](#anomaly-injection-schema) for the accepted
  forms and runtime semantics.

Every primary and cascade spec is schema-checked at import time by
`_validate_scenario_spec()` (called from `_validate_scenarios_registry`).
The check has one deliberate write side effect: an iterable
`instance_filter` is normalized in place to a `frozenset` (element
validation must iterate the filter, which would exhaust a one-shot
iterable before runtime, so the materialized form is stored back).
Checks performed:
required keys present, `metric` in the full `COMPONENTS[component]` catalog,
`generator` callable, `time_offset` a finite non-negative non-bool
`int`/`float`, `description` a non-empty string, `shape` a string in
`_VALID_ANOMALY_SHAPES`, `duration_seconds` a finite non-negative non-bool
numeric, `shape_params` a dict, `instance_filter` (when present) either
`None`, an iterable of `str` ids, or a callable. Cascade specs reject
`shape`/`duration_seconds`/`shape_params` outright.

Generator dispatch rule: the runtime calls each generator with one of
two canonical positional shapes per path, chosen by the generator's
**required** positional count (defaults extend capacity but do not change
the call shape):

- **Step path** (cascades + primary step specs without positive
  `duration_seconds`; note: a spec with `duration_seconds == 0` is still
  the step path):
  - `required_positional == 3` → call as `(ts, col, rng)`
  - `required_positional <= 2` → call as `(ts, col)`; any default
    positional params keep their declared defaults
  - `*args` with `fixed_positional_count <= 2` → call as
    `(ts, col, rng)` (`*args` absorbs position 3)
  - `*args` with `fixed_positional_count == 3` and
    `required_positional == 3` (i.e. `(ts, col, rng, *args)`) → call as
    `(ts, col, rng)` (positions 1–3 fill required, `*args` empty)
- **Span path** (primary specs with `shape != "step"` or
  positive `duration_seconds`):
  - `required_positional == 5` → call as
    `(ts, col, t_within, span_idx, rng)`
  - `required_positional <= 2` → call as `(ts, col)`; any default
    positional params keep their declared defaults
  - `*args` with `fixed_positional_count <= 2` → call as
    `(ts, col, t_within, span_idx, rng)` (`*args` absorbs positions 3–5)
  - `*args` with `fixed_positional_count == 5` and
    `required_positional == 5` → call as
    `(ts, col, t_within, span_idx, rng)`

`*args` is rejected when its fixed-positional prefix would cause a
silent misbind. Two distinct misbind cases the validator and
dispatchers both reject:

- **Default-overwrite case** — `required_positional <= 2` with
  `fixed_positional_count > 2`. Example: `(ts, col, scale=1.0, *args)`
  on either path. The target-arity call would overwrite the author's
  declared default at position 3 (step) or positions 3–min(fixed,5)
  (span) before the rest flows into `*args`.
- **Required-misbind case** (span path only) — `required_positional`
  in `{3, 4}` with `*args`. Example: `(ts, col, rng, *args)` on a span
  spec. The 5-arg call would bind `t_within` into the required `rng`
  slot. (Step path with `required_positional == 3` is the canonical
  shape, so this case only applies to span.)

Move any extra parameters after `*args` (kwarg-only with defaults)
instead.

Intermediate 3- and 4-arg span calls and 3-arg span calls for non-`*args`
generators are never attempted: those shapes were the silent-misbind
vector (a primary spec like `(ts, col, rng)` on a span path would have
had `t_within` bound to its `rng` parameter). The validator's
generator-arity rule rejects any generator whose required positional
count is incompatible with the path's two canonical shapes; see
`_validate_scenario_spec` for the full rule and the corresponding tests
in `tests/test_scenarios.py`.

`_resolve_scenarios()` applies the resolution pipeline:
allowlist (`--scenarios`) → exclusion (`--exclude-scenarios`) → severity filter
(`--signal-level`) → duration filter (`--duration-days`) → component filter
(`--components`). Scenarios dropped by severity or duration emit a stderr WARNING;
scenarios excluded silently by the component filter produce no output.

**RNG**: The RNG is an `np.random.RandomState(seed)` instance created in `main()` and
carried as `RunContext.rng`, passed explicitly through `generate_component()`,
`_natural_column()`, and the anomaly override path. Draw order is identical to the
former global `np.random.seed()` + module-level functions (MT19937 + Box-Muller), so
no locked SHA-256 hashes changed. The module-level `anomalies` list and
`cascading_anomalies` dict have been removed; all per-run state lives in `RunContext`.

**RNG ordering invariant (with tiebreaker caveat)**: `generate_component()` calls
Python's stable `sorted()` on override specs with key `(row_idx, metric_name)`. For
specs that round to **distinct** `(row_idx, metric)` pairs, the declaration order
of `primary_specs` / `cascade_specs` does not affect the RNG draw sequence or CSV
content. However, when two specs collide on the same `(row_idx, metric)` — e.g.
two cascades that round to the same row at a coarse `--interval-seconds`, or a
cascade landing inside a shaped primary span — the stable sort preserves their
input order and the **last** writer wins for that cell. Reordering colliding
specs can therefore change RNG draws and CSV content; preserve declaration order
within a scenario unless you have verified no collisions exist.

**`--anomaly-count` ordering**: `_apply_signal_level_and_count()` flattens the
per-component dict in `COMPONENTS` order, walks each component's spec list in the
order produced by `_apply_scenarios()`, then appends cascades in their target
component's registry order. Two ordering axes therefore matter for stable
`--anomaly-count` sampling: (1) the order of `COMPONENTS` (the dict iteration
order at the top of the file), and (2) the order in which scenarios append into
each component's list — which is the SCENARIOS dict insertion order. Preserve
both unless you intentionally want to shift the cap selection for the same seed.

## Modifying the script

### Adding a new scenario

1. Choose a unique slug (lowercase, underscores). Pick `severity` and `days_required`
   to match when the scenario should fire:
   - `severity="medium"` (default) → fires under `--signal-level medium` and `high`
   - `severity="high"` → fires only under `--signal-level high`
   - `days_required=N` → minimum `--duration-days` at which any of this scenario's
     specs becomes in range. Set this to the day index (1-based) of the earliest
     `time_offset` across all primary and cascade specs. `_validate_scenarios_registry`
     rejects any other value at import time (and
     `test_scenarios_days_required_valid` mirrors the invariant).

2. Add a `Scenario(...)` entry to `SCENARIOS` at the appropriate position (grouped by
   severity/category; new entries go after existing ones in the same group to avoid
   shifting the `--anomaly-count` sampling pool).

3. Populate `primary_specs` and `cascade_specs`:
   - Each primary spec is `(component, {time_offset, metric, description, generator,
     optionally duration_seconds/shape/shape_params})`.
   - Each cascade spec is `(target_component, {time_offset, metric, description,
     generator})` — no shape fields.
   - All referenced components must be keys of `COMPONENTS`; import-time validation
     (`_validate_scenarios_registry`) enforces this.

4. Set `components_touched` to the tuple of `COMPONENTS` keys (component names, not
   the scenario slug) referenced by any primary or cascade spec in this scenario.
   `_validate_scenarios_registry` rejects any drift (missing or extra entries)
   at import time, so the tuple acts as the authoritative `--components` filter
   index (`test_scenarios_components_touched_matches_specs` mirrors the
   invariant).

5. Run the test suite. The parametrized tests in `test_scenarios.py` and the
   coverage checks in `test_correctness.py` will catch missing/wrong specs
   automatically. No conftest changes are needed for a new scenario.

6. Update `README.md`'s scenario catalog table with the new slug, severity,
   `days_required`, and a one-line description.

### Adding new metrics

Append a `MetricSpec` to the relevant list in `COMPONENTS`. Each component's list
is ordered by descending importance and is split by `DEFAULT_METRICS_PER_COMPONENT[name]`
into two zones:

- Indices `[0, DEFAULT_METRICS_PER_COMPONENT[name])` — the historic default schema.
  Inserting or reordering here changes the default CSV columns and breaks the
  byte-for-byte default-output guarantee. Do this only when you are intentionally
  changing the default schema, and bump `DEFAULT_METRICS_PER_COMPONENT[name]` in the
  same change if you are adding (not replacing) an entry.
- Indices `[DEFAULT_METRICS_PER_COMPONENT[name], MAX_METRICS_PER_COMPONENT)` — the
  supplemental zone surfaced only via `--metrics-per-component` (half-open: the last
  valid index is `MAX_METRICS_PER_COMPONENT - 1`, so each component holds at most
  `MAX_METRICS_PER_COMPONENT` entries). New metrics should be appended here by default
  so existing default output stays byte-identical; they are only emitted when callers
  pass `--metrics-per-component` high enough to reach them.

Up to `MAX_METRICS_PER_COMPONENT` (10) entries are allowed per component, and every
catalog in `COMPONENTS` is already at that cap. Adding a new metric therefore
requires one of:

- Replace or remove an existing supplemental metric (zone 2) — preserves the
  default schema and stays within the cap.
- Intentionally raise `MAX_METRICS_PER_COMPONENT` — must be matched by an update
  in `tests/conftest.py` (`COMPONENT_FIELDS` per-component total) and re-run the
  test suite; the import-time validator rejects any list longer than the cap.

Once the slot exists, the column flows through `_natural_column()` and
`generate_component()` automatically.

### Adding new components

A new component needs two lockstep entries in `src/anomaly_metric_creator/catalog.py`
and two in `tests/conftest.py`:

In `src/anomaly_metric_creator/catalog.py`:

1. `COMPONENTS[name]` — ordered `MetricSpec` list (up to `MAX_METRICS_PER_COMPONENT`).
2. `DEFAULT_METRICS_PER_COMPONENT[name]` — how many metrics the new component
   emits by default.

In `tests/conftest.py`:

3. `COMPONENT_FIELDS[name]` — total metric count (int). Drives
   `tests/test_registry.py` (component coverage, metric count) and several
   `tests/test_correctness.py` checks.
4. `DEFAULT_METRIC_COUNT[name]` — historic per-component default count. Drives
   `test_metrics_per_component_default_matches_legacy_columns` in
   `tests/test_cli.py` and the default-emitted-subset checks in
   `tests/test_correctness.py`.

To add anomalies for the new component, add `Scenario` entries to `SCENARIOS` that
reference it in `primary_specs` or `cascade_specs`, and list it in
`components_touched`. No imperative registration functions need to be touched.

Validation is split across import time and the test suite:

- **Import time** rejects:
  - Key drift between `COMPONENTS` and `DEFAULT_METRICS_PER_COMPONENT`.
  - Any catalog longer than `MAX_METRICS_PER_COMPONENT`.
  - Any default count outside `[1, len(catalog)]`.
  - Any scenario referencing a non-existent component.
  - Any `days_required` that does not equal the day index (1-based) of
    the earliest spec offset.
  - Any `components_touched` tuple that does not equal the set of
    components actually referenced by the scenario's primary and
    cascade specs.
  - Any non-string severity, or severity outside `{low, medium, high}`,
    on a scenario, primary spec, or cascade spec.
  - **Per-spec schema drift** (via `_validate_scenario_spec`): non-dict
    specs; missing required keys (`time_offset`, `metric`, `description`,
    `generator`); non-string or unknown metric (rejected against the
    full `COMPONENTS[component]` catalog, not the trimmed default); non-
    callable generator; non-finite, non-numeric, negative, or boolean
    `time_offset`; non-string or empty `description`; non-string or
    unknown `shape`; non-numeric, non-finite, negative, or boolean
    `duration_seconds`; non-dict `shape_params`; cascade specs
    declaring `shape`/`duration_seconds`/`shape_params`.
  - **Generator arity drift** (also via `_validate_scenario_spec`):
    generators with required keyword-only parameters; generators whose
    `required_positional` / `max_positional` shape doesn't match the
    canonical 2-arg or path-target form (3 for step, 5 for span) per
    the dispatch rule above.

  All of these raise a clear `ValueError` naming the scenario slug and
  the offending field before `main()` runs.
- **Test suite only.** Drift between `COMPONENTS` and `COMPONENT_FIELDS` /
  `DEFAULT_METRIC_COUNT` is caught only by the test suite. Run it after adding or
  modifying a component — don't rely on import-time validation alone.

### Anomaly metric validation

`_filter_anomalies_for_emitted_metrics()` runs before generation and treats two
cases differently:

- Metric is in the full `COMPONENTS[component]` catalog but trimmed by
  `--metrics-per-component` → silently dropped (intended behavior of the cap).
- Metric (or component) is not in the full catalog → `ValueError`. This catches
  typos in scenario specs that would otherwise silently disappear from all outputs.

### Changing time range

Pass `--start-time ISO8601` to shift when the synthetic day begins (it
overrides the module `START` default without a code edit; see
`_parse_start_time_arg` and `README.md`). To generate more than one day, pass
`--duration-days N` rather than editing the `SECONDS_PER_DAY` constant — it is
fixed at 86,400 by design. Editing the `START` datetime constant still changes
the built-in default for callers that omit `--start-time`, but the flag is the
supported per-run lever.

### Adjusting anomaly timing

Time offsets are in seconds from `START`. Use expressions like `2*3600 + 15*60` for
readability (2 hours 15 minutes). For multi-day specs use `N*SECONDS_PER_DAY + …`. Any
spec whose `time_offset` is `>= SECONDS_PER_DAY * duration_days` is skipped at run time
with a stderr warning naming the duration required to include it — keep the spec,
increase `--duration-days`, rather than silently truncating.

## Pre-PR checklist (required before marking a PR ready for review)

This checklist maps to 15 recurring review gates, including **CI / workflow / dependency hygiene** from the full sweep of ~750 Copilot comments through PR #122 and **Changelog / version impact** from the 0.4.0 release-hygiene audit. Work through each bold heading before marking the PR ready for review (i.e. before removing draft status). Either confirm each heading or write "N/A — _reason_". The bullets under each heading are guidance for what to verify, not additional checklist entries to copy verbatim. `.trellis/spec/amc/backend/testing-quality.md` and `.trellis/spec/amc/backend/documentation-review.md` are the canonical task-loadable sources for the checklist; `.github/PULL_REQUEST_TEMPLATE.md` prefills the same 15 headings as Markdown `- [ ]` lines on every new PR and must mirror — not redefine — those headings. When a heading is renamed, added, or removed in Trellis, update the template, Copilot instructions, mechanical contract guard, and this source guide in the same diff so the surfaces stay in lockstep.

When a recurring issue is *mechanical* (a greppable shape), prefer turning it into a `tools/check_*.py` lint over adding a prose bullet here: the `ruff-lockstep` / `role-name-leaks` / `branch-name` lints reliably stop their patterns, whereas prose rules in this file have not (the test-resource-cost rules recurred across several PRs after being documented). The sweep's top finding was that **doc/comment-vs-code drift is the single most-flagged pattern (~30% of all review comments)** — so the Doc / docstring sync heading below is the highest-leverage one to actually run, not skim.

**Scope & description**
- PR description names every behavior change in the diff — RNG model, registries, module-level state, default-output bytes, public-helper signatures, CLI/env semantics, doc surface. If the diff is broader than the description, either split the PR or update the description.
- If the diff touches RNG, `RunContext`, registries, or any module-level state, the description calls it out explicitly and the test plan covers determinism.

**Validators and schema checks**
- For every field a new validator inspects, enumerate non-canonical inputs: `None`, `NaN`, `±inf`, negative, `bool` (a subtype of `int`), empty string, unhashable, wrong container type.
- Type-check *before* a membership test or a numeric op, so the validator's own `ValueError` fires instead of a raw exception from deeper in: `x in VALID_SET` raises `TypeError` when `x` is an unhashable list/dict — gate with `isinstance(x, str)` first; `math.isfinite(x)` raises `OverflowError` on an arbitrarily large `int` at import time — guard or skip the float path for non-float numerics.
- `schema.json` (and any `--instance-config` or other hand-editable input read back at runtime) is **untrusted**: every field the *reader* consumes needs the same type + finiteness guards as the writer-side check, not just the writer. A `NaN`/`±inf` that a JSON loader happily parses silently defeats range and zero-variance checks downstream (`np.std` returns `NaN`; every comparison against it is `False`).
- Every *branch* of a discriminator is validated: callable **and** constant `Edge.weight`; cascade **and** primary specs; step **and** span paths; `*args` **and** fixed-arity callables.
- Dispatch tables (`_RECOMPUTERS`, `DERIVATIONS`, etc.) raise on unknown keys; never return `None` or fall through silently. If a caller genuinely needs to tolerate misses, the *caller* opts in via `try/except KeyError` — the table itself stays strict. Concrete antipatterns to grep for before review:
  - `table.get(key)` on a dispatch table — returns `None` on miss instead of raising. Use `table[key]` so a typo or registry drift fails loudly. The fix replaced `_RECOMPUTERS.get(component)` with `_RECOMPUTERS[component]` for exactly this reason.
  - A dispatcher *function* (e.g. `_recompute_cacheservice`) that returns a sentinel — `None`, an empty string, or a "soft violation" message — for an unrecognized metric or component instead of raising `KeyError`. The caller cannot distinguish "metric is fine" from "I have no recomputer for this metric"; both look like success. Replace the soft-violation return with `raise KeyError(...)`.
  - A dispatcher branch that silently falls through to a `return` at the bottom of the function when no `if`/`elif` matched. Add an explicit `raise KeyError(...)` instead.

**Doc / docstring sync** — the single most-flagged pattern in the whole review history; grep the changed *behavior*, not just the symbol name.
- Every changed function with a docstring has its docstring updated in this diff.
- Grep every changed symbol name against CLAUDE.md and README.md and update prose that describes it.
- If a public helper was removed or repurposed, CLAUDE.md prose is updated in the same diff.
- When you change a default, a precedence rule, a count, an edge list, or a dispatch order, grep for the *old value/word* across the docstring, in-file section headers, CLI `--help`/help strings, `README.md`, `docs/*.md`, **and** CLAUDE.md. A behavior change fans out across all of them, not only the file you edited (e.g. flipping `--topology-mode` to `realistic` left `docs/topology.md` stale; moving subcommand dispatch before `parse_args` left the `docs/application-flow.md` mermaid wrong).
- Magnitude/percentage values baked into description strings (a scenario's `(35% errors)`, a docstring's `350 rows`) must match the generator they describe.
- Count words drift silently as a list grows — "four slices", "three modes", "8 specs". Re-count after adding or removing an item.
- A new `tools/check_*.py` (or any file) whose docstring was copy-pasted from a sibling must have its mode/call counts and examples re-verified line-by-line (PR #92 inherited "three modes / three calls" from `check_branch_name`'s docstring while having two modes and four `gh` calls).
- After any bulk find/replace or scrub of internal references, re-read every touched docstring for orphaned grammar — Copilot files each fragment as its own comment, so one scrub burns a whole review cycle (PR #80).

**Single source of truth**
- No hand-rolled emit→filename, metric→component, or component→derivation maps alongside a canonical registry. Every consumer reads from `_EMIT_ARTIFACT_FILES`, `COMPONENTS`, `DERIVATIONS`, etc.
- `_COMBINE_OUTPUT_FILENAME` is used by the actual combine writer, not only the cleanup/summary path.
- The `Instance` dimension fields have multiple drift sites — the validator's `_valid_instance_fields` set and the `Instance(**{...})` constructor kwargs in `_load_instance_config` — both must derive from `_INSTANCE_DIMENSION_COLUMNS`, never a hand-listed copy (#64). Same for any "canonical first entry" *positional* convention (a `break`-after-first over `_TOPOLOGY_LOAD_METRICS`): make the convention explicit, not implicit in iteration order (#47).

**Completeness**
- PR title implies a class of fix (e.g. "add `clip_min` to non-negative metrics") → grep for all instances and confirm coverage.
- When a change adds a *second* code path for the same data — wide vs long-form CSV, anonymous vs named-instance, 4-col vs 10-col gauges, the topology lambda-baked vs per-instance path — list every transform, guard, default, and splice the original path applies and confirm each is re-applied on the new path. Recurring misses: `_splice_dst_artifact` dropped on the long-form writer (#63); a `header[0] == "timestamp"` check missing from the dim-detection predicate (#67); an eagerly-evaluated `config_map.get(name, list(INSTANCES[name]))` default that crashed the unconfigured branch (#64).

**Mode / flag combinations**
- List every other CLI flag, env var, and `--emit` token that interacts with the new flag. Gate invalid combinations in `parse_args` with a clear message, or add a test.
- New `parse_args` checks must not spuriously reject the `combine`/`validate` subcommands or non-default `--emit` invocations.

**Test path determinism**
- Every new code path has a test whose input deterministically exercises that path (no reliance on "the default seed happens to do X").
- Each new CLI flag is covered in isolation, not only in the most-permissive bundle.
- If `expected` is derived from a registry (e.g. `{m for m in COMPONENTS[c] if pred(m)}`, `{e.target for e in TOPOLOGY[s]}`, or a comprehension over `SCENARIOS`), assert `len(expected) > 0` (or the moral equivalent — `assert expected`, `assert expected_count > 0`) *before* the membership/equality check. An empty `expected` makes the downstream check trivially pass in several shapes:
  - `assert expected.issubset(actual)` / `assert expected <= actual` — `∅ ⊆ actual` is always true.
  - `for m in expected: assert <property>(m)` — zero iterations, asserts nothing.
  - `assert actual == expected` — passes whenever `actual` also happens to be empty; the test claims "actual matches registry" but really claims "both are empty".
  - `assert expected & actual == expected` and `assert actual.issuperset(expected)` — collapse to `∅ == ∅` / `actual ⊇ ∅`, both always true.
  - Three of four vacuous-test bugs on PR #50 had this exact shape: a registry filter (`if metric.dtype == "int"`, `if "ratio" in name`, etc.) excluded every candidate under the default catalog, so `expected` was empty, so the assertion ran on nothing. The non-empty guard catches the filter regression at test time instead of letting the test silently rot.
  - When the test legitimately needs `expected` to be empty for some inputs (rare), assert that *condition* explicitly and gate the membership check behind it, so a future registry change that makes `expected` accidentally empty under *different* inputs still trips the guard.
- Pair every "negative" assertion (the dropped scenario's output is *absent*) with a positive one (a *retained* scenario's output survives) — otherwise an over-filter regression that drops everything passes. For a dropped scenario, also assert its *cascade* specs are absent, not just its primary descriptions (cascade leakage went undetected on #13/#16). A file-existence assertion must additionally read ≥1 data row, not just `assert path.exists()`.
- String matching that must be exact uses anchored regex or full-token equality, never bare `in`/substring: version-pin parsing (a `ruff==0.15.17` regex must end-anchor or a `; python_version<…` marker suffix slips through, #117), flag-presence tests (`assert "--emit" in out` false-positives once `--emit-selection` exists, #101/#104), and trailing-marker escape hatches (`# allow` matched mid-line fires inside string literals, #89).
- Avoid tautological boolean assertions: `assert A or B` where `B` is unconditionally true (e.g. `or ("pod" in violations[0])`) always passes (#68). A "negative" test must also assert the run reached the intended code path (e.g. assert exit `0`, and that the fixture actually contains the thing being skipped) so it can't pass for the wrong reason.

**Performance in hot paths**
- No per-row re-parsing of strings or re-computation of constants that could be hoisted above the loop. A timestamp re-`strptime`d once per data point (per row × metric) is a real hotspot at gauge-stream scale (#30).
- No broad `try/except` in a per-row loop where the body has side effects such as RNG draws. Resolve a generator's arity (the `try/except TypeError` arg-count probe) once per spec, not once per row — repeating it per row both wastes work and can duplicate RNG draws (#37).
- Per-`(component, instance)` loops multiply cost by N: hoist per-component file scans/parses above the instance loop, and don't re-open the same CSV from the start for each instance block (#67).

**Action order in user-facing output**
- The end-of-run `Done - … written to …` summary line only names artifacts the run actually wrote, and is printed only after every writer it names has completed successfully.

**Test hygiene**
- New test files have no unused imports or unused helpers. The
  `.pre-commit-config.yaml` ruff hook enforces this on `tests/` using the rule
  selection in `pyproject.toml` (`[tool.ruff.lint] select = ["F401"]`); run
  `.venv/bin/pre-commit run --all-files` or `.venv/bin/ruff check tests/`
  locally if the commit hook is not installed.
- New test files reuse the session-scoped `amc` fixture from
  `tests/conftest.py` and do not re-import the implementation module
  (`src/anomaly_metric_creator/legacy.py`) via
  `importlib.util.spec_from_file_location(...)`. The
  `amc-no-direct-spec-load` pre-commit hook
  (`tools/check_amc_module_load.py`) catches this structurally — PR #63
  and PR #64 each shipped a module-scoped `amc` fixture that re-built
  the registry, and Copilot was the only thing flagging it. When a
  test genuinely needs a fresh module instance (e.g.
  `test_correctness.py` monkey-patches `_apply_scenarios`, or
  `test_scenarios.py` loads `_VALID_ANOMALY_SHAPES` at parametrize
  collection time), route through `conftest._load_amc()` (memoized) or
  annotate the `spec_from_file_location` call line with
  `# amc-load: allow`.
- An in-process `main()` call must not leave mutated module/session-scoped
  state (a filtered `cascading_anomalies` registry, `MEZMO_OTEL_*` env vars)
  visible to later tests — the default `pytest-xdist` parallel mode turns
  leaked global state into an order-dependent flake. Use the `RunContext`
  path or a subprocess, or clean up; an autouse env-isolation fixture must
  out-scope (session, not function) the session fixtures it protects, or it
  runs *after* they already called `parse_args()` (#17).

**Test resource cost**
- The AST-backed `tools/check_test_resource_cost.py` guard runs in pre-commit and the always-run CI repository-guard job. It rejects executable `read_bytes()`, `readlines()`, and `read_text().splitlines()` calls under `tests/`; use a trailing `# resource-lint: allow` only for a reviewed, deliberately small control/log/schema artifact. The command accepts files or directories and exits 0 clean, 1 for aggregated violations, and 2 for missing, unreadable, or syntactically invalid inputs.
- Fixtures generating full 1-day, 7-day, or `--instances-per-component N > 1` (N=3 and larger) datasets must reuse the session-scoped fixtures already declared in `tests/conftest.py` rather than redefine module-scoped duplicates. A `module`-scoped fixture that runs `main()` end-to-end will re-execute the generator once per test file and multiply suite wall-time and peak RSS by the number of duplicating files (PR #67 had three separate 264 MiB N=3 one-day dataset fixtures; PR #63 module-scoped fixtures duplicated session-scoped runs from conftest).
- Reading multi-hundred-MB CSVs into memory via `Path.read_bytes()` is forbidden in tests. Use chunked streaming for hashing (`with path.open("rb") as f: while chunk := f.read(1 << 20): hasher.update(chunk)`) so peak RSS stays bounded regardless of file size. The suite's shared helper is `conftest.sha256_path` — use it instead of declaring a per-file copy. PR #67's `_sha256` helper read a generated CSV fully into RAM in one shot — replace it with a streaming loop.
- A test that needs only a row count must not call `f.readlines()` or `path.read_text().splitlines()` on the full file. Use `sum(1 for _ in f)` (or `with path.open() as f: next(f); count = sum(1 for _ in f)` to skip the header) so the file streams line-by-line. PR #64 read a full N=2 1-day CSV with `readlines()` just to count rows.

**Cross-platform test guards**
- Any test that uses POSIX-only stdlib APIs must guard the use so pytest collection still succeeds on Windows. Two distinct cases need different guards:
  - **POSIX-only modules** — whole-module imports that fail on Windows: `resource`, `pwd`, `grp`, `fcntl`, `termios`, `tty`. Guard with `pytest.importorskip("resource")` inside the test function body before any use, or with a module-top `if sys.platform == "win32": pytest.skip("POSIX only", allow_module_level=True)` *before* the `import resource` line. An unconditional top-of-module `import resource` fails pytest collection on Windows even when the production code under test no-ops on Windows — PR #67's `test_ensure_long_form_fd_capacity_*` had this shape and would have broken any future Windows CI lane.
  - **POSIX-only names on cross-platform modules** — e.g. `select.epoll`, `signal.SIGSTOP`, `signal.SIGKILL`, `os.fork`. `import select` and `import signal` both succeed on Windows, so `pytest.importorskip` is the wrong guard — the missing symbol is the attribute, not the module. Two acceptable forms: (1) `from select import epoll` at module top, gated by `if sys.platform == "win32": pytest.skip("POSIX only", allow_module_level=True)` *before* the `from` line; or (2) `pytest.skipif(not hasattr(select, "epoll"), reason="POSIX only")` on the individual test that uses the attribute.

**Default-behavior changes**
- If a default parameter value or fallback path changes (e.g. unseeded `RandomState`, required arg replacing optional), the PR description names it and tests cover both old and new caller shapes.
- Production-code determinism regressions are as load-bearing as test ones: a `set` iterated to build output-ordered rows (use `sorted()`), an *unseeded* `RandomState` fallback when `rng` is omitted, an `id()`-based spec identity, or float `datetime.timestamp()*1e9` (use integer `timedelta` arithmetic) all break the documented seed-determinism guarantee (#9/#19/#37).

**CI / workflow / dependency hygiene** — the repo recently gained GitHub Actions, Dependabot, and Socket; none of the headings above cover workflow YAML or packaging, and this was the single largest *uncovered* cluster in the sweep (~40 comments).
- Pin third-party GitHub Actions and in-workflow `pip install`s to exact versions — SHA-pin actions where practical, and pin security tooling (`socketsecurity`) so a scan is reproducible and not itself a supply-chain vector. Use `python -m pip`, not bare `pip`, after `actions/setup-python` so the install targets the selected interpreter.
- A job's `permissions:` block grants exactly the scopes its steps need, no more: a step that comments/pushes needs `contents` / `pull-requests: write`; the Actions cache does **not** need an `actions` scope (it authenticates with the runtime token, so `contents: read` is sufficient — a recurring Copilot false positive, see below). Gate secret-bearing triggers on actor/permission, and remember a `pull_request` from a fork gets no secrets (the job silently skips).
- Two-place version pins (`ruff==` in `pyproject.toml` ↔ `rev:` in `.pre-commit-config.yaml`) must be lint-enforced in lockstep (`check_ruff_lockstep.py`); a Dependabot bump must not silently raise a *declared* `>=` floor in `pyproject.toml` (use `versioning-strategy: lockfile-only`); and the Dependabot `package-ecosystem` must match a lockfile that actually exists (`uv` needs a committed `uv.lock`).
- Docs that tell users to run a tool ensure it is in the `dev` extra; `addopts` plugin flags (`-n`) require a matching `required_plugins = [...]`; workflow shell snippets must not assume runner-image tools (`jq`) without installing them and must handle the real JSON payload shape; inline workflow comments must be factually correct about Actions semantics.
- A new `tools/check_*.py` lint honors the `0`/`1`/`2` exit-code contract in its own docstring: wrap `json.loads`, file reads, and `gh` subprocess calls so a decode/IO failure exits `2` (structural) — not a traceback, and not `1` (which means "violation"); check `path.exists()` *before* skip-rules; parse `gh api --paginate` page-by-page (it can emit multiple concatenated JSON documents); use anchored/full-token matching for markers and pins.
- Keep `.github/instructions/anomaly-metric-creator.instructions.md` and `.github/PULL_REQUEST_TEMPLATE.md` in lockstep with the registry contracts and checklist headings they mirror — a stale reviewer-instructions file makes Copilot flag correct code as buggy (#44).

**Changelog / version impact**
- User-visible behavior, compatibility changes, supported Python floors, and
  release-process changes update `CHANGELOG.md` in the same PR or explicitly
  state why no changelog entry is warranted.
- A release PR keeps `pyproject.toml`, the editable project entry in `uv.lock`,
  the promoted changelog heading, tag name, GitHub Release, and
  `amc --version` output aligned.

### Reviewer-before-ready gate

PRs open as **draft** and walk the pre-PR checklist above before draft
status is removed. The pre-PR checklist is the structural backstop —
caught-in-draft issues are fixed before Copilot's first review, not
after.

### Known Copilot false-positives (verify, don't reflexively fix)

The maintainer accepted ~98% of Copilot's flags across 122 PRs, so the
default is to treat a flag as actionable. The few *recurring* exceptions —
worth recognizing so they don't cost a cycle of re-litigation:

- **Cumulative-diff re-flagging.** Copilot reviews the PR's *cumulative*
  diff, so it re-flags an issue you already fixed in a later commit of the
  same PR. Verify against current `HEAD` before "fixing" it again (#80).
- **Triplicated drift.** The same stale sentence flagged from three nearby
  hunks is one defect, not three — fix once (#14/#20/#27).
- **`contents: read` "breaks" the `setup-uv` / Actions cache.** False. The
  cache authenticates with the runtime token (`ACTIONS_RUNTIME_TOKEN`),
  independent of the `GITHUB_TOKEN` `permissions:` block, so caching works
  fine under `contents: read` (#117).
- **"Secrets can be referenced in a step-level `if:`."** False — they
  cannot, at job *or* step level (GitHub docs / actions/runner#520). Mapping
  the secret to `env` and gating on a derived step output is required, not a
  workaround to remove (#118).
- **"Skip the preflight cell-cap when `--emit` excludes `metrics`."** False
  — `generate_component()` still allocates the full array and runs the
  pipeline regardless of emit selection; only the final write is gated, so
  the OOM the cap prevents still happens. Only the `combine`/`validate`
  subcommands (which `return` before generation) are safe skips (#35).

Verification rule: for any version-sensitive claim about a tool's semantics
(Actions, uv, Dependabot, pytest), confirm against current docs before
accepting — Copilot's confident-but-wrong claims cluster there.

### External-comment role-name lint

The `role-name-leaks` pre-commit hook
(`tools/check_role_name_leaks.py`) catches internal role-name
references (canonical list in `_FORBIDDEN_LABELS`) in the
text-bearing files the staged-diff scan passes to pre-commit —
Python source, Markdown docs, YAML configs, PR templates, and helper
scripts that build comment bodies. PR #86 shipped two approval
comments that leaked the role name in the handoff sentence and
VER-701 added the lint as the structural fix.

Scope note: at the default pre-commit stage the hook only sees files
in the staged diff. The separate `role-name-commit-message` hook scans
`git commit` message files after one-time installation with
`pre-commit install --hook-type commit-msg`; it does not scan text typed
directly into the GitHub web UI. For comment bodies authored
outside the staged tree (e.g. `gh pr comment --body-file`
payloads), use the stdin pre-flight pattern below — that is the
structural coverage path for "text destined for an external thread
that never touches a repo file".

The script also accepts stdin so an ad-hoc comment body can be
pre-flighted before being piped through `gh`:

```bash
.venv/bin/python3 tools/check_role_name_leaks.py - < /tmp/body.md \
    && gh pr comment <N> --body-file /tmp/body.md
```

Use this for every `gh pr comment`, `gh issue comment`, `gh pr create
--body-file`, and `gh pr review --body-file` invocation; the `&&`
chain keeps `gh` from posting when the body is dirty. The canonical
one-shot path is `tools/pr_comment.sh` (see the **Comment pre-flight
wrapper** subsection below), which runs this role-name gate *and* the
approval-duplicate gate before `gh pr comment`; the raw chain above is
what the wrapper does for its role-name step.

The literal trailing marker `# role-name-lint: allow` on a line skips
that line wholesale (the script checks `line.rstrip().endswith(...)`
— a mid-line occurrence inside a string literal does NOT exempt the
line). Use sparingly: the canonical-labels tuple inside the lint
script and the acceptance tests in
`tests/test_role_name_leaks_lint.py` (which bake the literal labels
into fixtures) are the only intended consumers today.

Exit codes: `0` clean, `1` at least one label match (the "Internal
role names must not appear…" footer fires only on this branch),
`2` argument or I/O error (e.g. an unreadable path that is not a
binary-skip). Callers chaining the script in `&&` therefore see a
genuine label leak distinct from a structural script failure.

### Approval-duplicate lint

The `tools/check_approval_duplicate.py` script gates `APPROVED`-shaped
PR comments on `(author, commit OID)`. PR #86 accumulated five
`APPROVED`-shaped comments from the same author against the same
head commit, including one `APPROVED (Correction to previous comment:
…)` self-edit that should have been an in-place edit of the prior
comment; VER-704 closes that pattern structurally.

Two refusal arms (each fires independently of the other):

- **Duplicate** — a same-author comment whose body starts with the
  literal upper-case token `APPROVED` and whose `created_at` is at or
  after the PR's current head commit's committer timestamp counts as
  an approval for that commit. The next same-author approval-shape
  write is rejected; the diagnostic names the *most recent* prior
  comment id (the natural edit target) and the count of any other
  same-author priors that also matched, so the caller can switch
  the write to an in-place edit. When a new commit is pushed whose
  committer date is *after* the prior approvals, those priors fall
  before the head and a fresh approval is allowed — the typical case
  for fast-forward pushes. A cherry-pick / rebase / amend that lands
  a head commit with an *older* committer date will *not* clear the
  window; rewriting history doesn't silently re-open the
  duplicate-approval path. The gate uses the commit's
  ``committer.date`` field, which the user normally cannot backdate
  except by these rewrite paths. Timestamps on both sides are parsed
  via `datetime.fromisoformat` (with `Z` → `+00:00` substitution)
  rather than lex-compared, so millisecond precision and
  `+00:00`-offset priors gate identically to the canonical `…Z` form
  GitHub emits today.
- **Self-correction prefix** — a body whose first non-blank line
  carries `Correction to previous comment` (case-insensitive,
  whitespace-flexible) or starts with `Correction:` /
  `Correction -` / `Correction —` (any of the three separators the
  script's `_CORRECTION_PREFIX` accepts) is announcing a correction
  and must be an edit, not a new comment. This arm fires regardless of whether the body is
  approval-shape, so a non-`APPROVED`-prefixed correction body still
  trips the gate.

The gate is invoked the same way as the role-name lint — chained
into the existing `gh pr comment --body-file …` pre-flight slot:

```bash
.venv/bin/python3 tools/check_approval_duplicate.py --pr <N> < /tmp/body.md \
    && gh pr comment <N> --body-file /tmp/body.md
```

The canonical one-shot that wires both gates is `tools/pr_comment.sh`
(see the **Comment pre-flight wrapper** subsection below); the raw
chain above is what the wrapper does for its approval-gate step.

Under `--pr <N>`, the script calls `gh api` to read the head SHA, the
head commit's committer timestamp, the prior issue-comments thread
(`--paginate`; the page-concatenated `[...][...]` output gh emits for
multi-page array endpoints is parsed page-by-page and flattened, so
threads past 100 comments — the exact PR #86 shape — gate correctly
instead of exiting 2 on `Extra data`), and (when `--author` is
omitted) the current user's login. The
`<owner>/<repo>` slug is also fetched in this mode and threaded into
the diagnostic so the suggested `gh api … -X PATCH` command is
copy-paste-ready. For offline tests and CI hooks, fixture mode
accepts every input as flags / paths: `--head-commit-oid`,
`--head-commit-date`, `--author`, `--prior-comments-json`; the
diagnostic falls back to `<owner>/<repo>` placeholders. The two
modes are mutually exclusive — mixing them exits 2. The script also
refuses up front (exit 2) when stdin is a TTY: the body must be
piped in, never typed interactively.

Scope: the gate inspects issue comments
(`/repos/<owner>/<repo>/issues/<n>/comments`), which is where PR
#86's spam landed. Native PR `reviews` (the `Approve / Request
changes / Comment` flow) are a separate endpoint and out of scope for
v1.

Exit codes: `0` clean (chain `gh pr comment …`), `1` duplicate or
self-correction refusal (at most one summary diagnostic per arm —
the duplicate arm collapses N priors into one line naming the most
recent), `2` argument error, missing required flag, malformed JSON,
TTY stdin, or `gh` failure. The exit-code split mirrors the
role-name lint so an `&&` chain stops the `gh` write on a refusal
without silencing structural script failures.

### Comment pre-flight wrapper

`tools/pr_comment.sh` is the canonical enforcement path for the two
comment-body gates above. Until it landed, both the role-name lint and
the approval-duplicate lint were documented only as manual `&&` chains
that nothing invoked (audit item A-034: the approval-duplicate gate — a
~690-line script plus its separate ~1,000-line test suite — had no
enforcement path) — every sibling lint in the repo (`role-name-leaks`,
`role-name-commit-message`, `branch-name`, `ruff-lockstep`) is wired,
this one was the anomaly. The wrapper closes that gap by chaining
role-name → approval-duplicate → `gh pr comment` in one command:

```bash
tools/pr_comment.sh --pr <N> --body-file /tmp/body.md          # post
tools/pr_comment.sh --pr <N> --body-file /tmp/body.md --dry-run # gates only
```

It reads the body from a file (never a TTY) and **redirects that file
into each gate independently** — the gates each consume the full body
from stdin, so a single Unix pipe would feed the second gate the first
gate's diagnostics rather than the comment. Any `gh pr comment` args
after a `--` separator are forwarded verbatim. Exit codes pass the
underlying gate contract through unchanged: `0` clean (comment posted
unless `--dry-run`), `1` a gate refused the body (role-name leak or
duplicate/self-correction approval), `2` argument/IO error or a gate's
structural failure. It is POSIX-sh operator tooling for local comment
posting, not a CI step, so it stays out of the workflow-pip / CI-mirror
lint scopes; it needs `gh` authenticated exactly like the raw chains it
replaces. It posts through `gh pr comment` only (the approval gate is
PR-scoped via `--pr N`), so prefer it over the raw `&&` chains for every
`gh pr comment` body. For `gh issue comment`, `gh pr create --body-file`,
and `gh pr review --body-file` bodies — which the PR-comment wrapper does
not post — keep using the raw role-name chain shown earlier (the
approval-duplicate gate does not apply outside PR comments). The raw
chains remain documented above as what the wrapper runs for each step.

### Branch-name lint

The `branch-name` pre-commit hook
(`tools/check_branch_name.py`) rejects any branch name matching
`(?i)(^|\b)ver-\d+`, so feature branches cannot republish the
internal ticket literal through a PR head ref. PRs #47–#77 and #86
all shipped head refs shaped like `sdelmas/ver-<N>-…`, which is the
gap this lint closes. The hook runs at the `pre-push` stage so it
fires once per push (a check on every commit would be noisy on
clean branches) and uses
`pass_filenames: false` + `always_run: true` so it does not depend
on the diff. Install with
`pre-commit install --hook-type pre-push` after the standard
`pre-commit install` invocation — the default install only
registers the `pre-commit`-stage hooks.

Pattern anchors and the digit requirement:

- **Case-insensitive.** `ver-655`, `VER-655`, and `Ver-655` are all
  rejected uniformly. The leak is the literal ticket id; the case
  of the prefix is irrelevant.
- **Start-of-string OR word boundary.** `ver-655-foo` (whole-string
  prefix) and `sdelmas/ver-655-foo` (boundary after `/`) both
  match; `fever-pitch` and `discover-foo` stay legal because
  neither has a word boundary before `ver`.
- **Digit required after the dash.** Generic `ver-` prefixes
  (`verify-something`, `ver-test-branch`) stay legal — the lint
  specifically catches the ticket form `ver-<N>`, not arbitrary
  `ver`-prefixed words.

Three invocation modes are supported (full details live in the
script's module docstring):

```bash
# Literal branch names (used by the test suite and ad-hoc checks).
.venv/bin/python3 tools/check_branch_name.py feature/clean ver-655
# Current branch via `git symbolic-ref --short HEAD` (the
# pre-commit hook mode; detached HEAD is treated as "nothing to
# check" so a no-branch state cannot wedge `git push`).
.venv/bin/python3 tools/check_branch_name.py --current
# Raw git pre-push stdin protocol — for a hand-rolled
# .git/hooks/pre-push that bypasses pre-commit.
.venv/bin/python3 tools/check_branch_name.py -
```

Exit codes: `0` clean (also: detached HEAD, empty stdin,
all-deletion stdin, tag-only push), `1` at least one branch leaks
the ticket literal, `2` argument or I/O error. There is no
per-branch escape hatch — unlike the role-name lint, a branch
name has no legitimate reason to embed a ticket literal; the
structural fix is to rename the branch.

Scope note: the local hook only fires on `git push`. Branches created
locally that never push (throwaway worktrees, exploratory work)
are not checked, by design — the leak is specifically about what
reaches GitHub. The CI `changes` job checks `github.head_ref`, so every pull
request also receives a server-side branch-name check.

Local-feedback gap (refspec push bypass): the pre-commit hook runs
`check_branch_name.py --current`, which reads the *current local*
branch name. A refspec push of the form
`git push origin clean:ver-123` or a detached-HEAD push
`git push origin HEAD:ver-123` publishes a leaking *remote* ref
name while the local branch is clean — `--current` cannot see the
remote side, so the hook will not flag it locally. CI checks the published PR
head ref and closes the merge-path bypass. The
`-` stdin mode does close this gap: it parses git's pre-push
protocol (`<local-ref> <local-sha> <remote-ref> <remote-sha>`) and
lints *both* ref names per line, de-duped when they are equal.
pre-commit's framework consumes git's pre-push stdin internally
and does not pipe it through to individual hooks, so the stdin
mode cannot be invoked from `.pre-commit-config.yaml` directly.
Developers who want full coverage can drop a one-line
hand-rolled hook in `.git/hooks/pre-push`:

```sh
#!/bin/sh
exec python3 tools/check_branch_name.py - "$@"
```

(make it executable with `chmod +x .git/hooks/pre-push`). The
hand-rolled hook runs in addition to pre-commit's pre-push stage,
so the two layers compose: pre-commit catches the common-case
plain `git push` and the hand-rolled hook catches the refspec
edge cases.

### Ruff version lockstep lint

`ruff` is pinned in two places that must agree: the exact `ruff==X.Y.Z`
entry in `pyproject.toml`'s `dev` extra (drives the local `.venv` ruff
and any `ruff check`) and `rev: vX.Y.Z` on the `astral-sh/ruff-pre-commit`
hook in `.pre-commit-config.yaml` (drives the pre-commit ruff). The
contract is stated in the inline comments on both pins ("Pinned exactly:
must match `rev` … Bump both lines together").

Dependabot's `increase` strategy used to keep the two in step by bumping
the `ruff==` pin in the same window the `pre-commit` ecosystem bumped the
`rev`. Under `versioning-strategy: lockfile-only` (chosen so Dependabot
stops creeping the `>=` floors) it no longer touches the exact `ruff==`
pin — bumping an `==` constraint needs a manifest change, which
`lockfile-only` skips — while the `pre-commit` ecosystem keeps advancing
the `rev`. The two can therefore drift, and with Dependabot auto-merge
enabled a lone `rev` bump could merge while `pyproject.toml` stays stale.

`tools/check_ruff_lockstep.py` closes that gap. It reads the `ruff==` pin
(`tomllib`) and the ruff-pre-commit `rev` (a targeted, dependency-free
line scan), normalizes a leading `v`, and exits `0` in-step / `1` on
drift / `2` on a structural error (missing pin, missing ruff-pre-commit
block, missing `rev`). It runs as a step in the CI `test` job before the
suite, so drift fails the required check until both pins are bumped
together. Acceptance tests live in `tests/test_ruff_lockstep_lint.py`;
the script is stdlib-only so it behaves identically in CI, a pre-commit
hook, or standalone.

### Continuous integration and Dependabot auto-merge

Merges are gated on GitHub Actions (the local pre-commit hooks do **not**
run in CI):

- `.github/workflows/ci.yml` — a path-classified cadence keeps the stable
  aggregate branch-protection context `test` while selecting the cheapest
  safe lane: lightweight readiness, quick test, or the full Python 3.14 path:
  concurrent `test heavy (py3.14)` and `test light (py3.14)` jobs followed by
  `coverage (py3.14)`. The light job also runs the console-script, ruff, and
  mypy gates once; the heavy job proceeds directly from setup to its pytest
  partition.
  The tested version policy is latest-stable-CPython-only (decided
  2026-07-06): `requires-python` in `pyproject.toml` equals the single CI
  matrix version; bump both together when a new stable CPython lands.
  The full matrix runs for opened/reopened/ready PRs, `full-ci`-labeled
  updates, auto-merge-armed PRs (the `auto_merge_enabled` event and every
  later push or label event on an armed PR — auto-merge never lands on
  quick-lane evidence), workflow/dependency diffs, manual dispatch, and every
  push to `main`; manual dispatch forces the application classifier even for a
  docs-only tip. The lightweight guards use `uv`-managed Python 3.14 without
  syncing the full project environment, and command-pack metadata plus
  `.trellis/audit/**` remain in that lightweight lane. The classifier also
  keeps an explicit `is_repo_tooling_path` allowlist for repo-only automation,
  but only when selecting the lightweight lane skips no behavioral test;
  tested scripts and all `tools/` paths stay application-required.
  Merge-burst `main`
  pushes run in per-commit concurrency groups so
  they cannot cancel each other's backstop runs, while PR refs keep
  cancel-in-progress. The aggregate `test` job is guarded with
  `if: ${{ !cancelled() }}` (never `always()`): when arming auto-merge
  triggers a fresh full run that cancels the in-progress lane, the aggregate
  is cancelled *with* the run. Its `test` context reports `cancelled`, and the
  required `CI Result` aggregate cannot pass, so auto-merge waits for the
  superseding run's real verdict rather than observing a spurious `FAILURE`.
  `always()` would run the aggregate during cancellation and evaluate
  `test "cancelled" = "success"` → a transient red on every auto-merge-armed
  PR; `check_ci_review_contract.py` pins the `!cancelled()` guard so a revert
  is caught. A superseded `main`-push commit's backstop run is *not*
  cancelled (per-commit groups), so a merge burst spends N standard-runner
  suites — an accepted cost of the "every merge commit gets a completed
  verdict" guarantee, not a bug (cancelling them would reopen the gap the
  per-commit groups close). All events run on the standard `ubuntu-latest`
  runner: the org's `ubuntu-latest-m` larger runner stopped being
  served on 2026-07-04 (main-push jobs sat queued for hours with
  `runner_id=0`, so the post-merge backstop never ran), and larger runners
  bill per-minute besides. Public-repository standard runners provide 4 vCPU,
  16 GB RAM, and 14 GB SSD, and their minutes are free; wall clock rather than
  billed minutes is therefore the optimization target. A prior full `-n 2`
  run still OOM-died after 32 min while holding the heavy N=3 / 7-day fixtures
  across xdist workers, so every run **splits** the suite by the `heavy`
  marker instead of running everything in one worker pool. Separate jobs start
  `pytest -n 2 --dist loadfile -m heavy` for the GB-scale 7-day / N=3 fixture
  tests and `pytest -n 2 --dist loadfile -m "not heavy"` for the light
  remainder under real xdist at the same time (the pyproject addopts default
  `-n 4 --dist loadfile` still applies to local runs on larger machines).
  This keeps the parallel worker-distribution / global-state ordering path
  — the one CLAUDE.md warns turns leaked global state into order-dependent
  flakes — exercised at the PR gate, so an xdist-only regression fails the
  gate rather than passing serially and only flaking post-merge. The
  `heavy` marker is auto-applied in `tests/conftest.py`
  (`pytest_collection_modifyitems` over `_HEAVY_SESSION_FIXTURES`), so the
  partition tracks the fixture set with no per-test annotation to drift. A
  broken marker still makes the heavy job collect zero tests and fail with
  pytest exit 5; the jobs are concurrent, so this is a correctness guard, not
  an ordering guarantee. Hosted trial `29798826800` adopted two heavy workers
  after measuring 5,333,032 KiB (5.09 GiB) peak system used memory and
  80,632,056 KiB (76.9 GiB) post-run free disk, clearing the pre-committed
  12 GiB / 2 GiB limits; the 48-test step completed in 500.62s versus the
  717s post-parallelization baseline. The light subset excludes exactly the
  GB-scale fixtures whose concurrent generation caused the original OOM. A
  four-worker CI trial completed the light step in 352s versus the 364s two-worker
  baseline, only 12s below baseline and short of the pre-committed 100s
  adoption threshold, so CI retains `-n 2`; the faster local four-worker result
  did not transfer to the hosted runner. Each pytest job has a 30-minute timeout,
  and the coverage-combine job has a 10-minute timeout, capping hangs while
  keeping the critical path below the former sequential 45-minute job.
  The full-suite lane also runs mypy and coverage, each in a **report-only +
  gated** pair (`07-06-coverage-threshold-and-mypy-gating`). **mypy:** a
  report-only baseline step (`continue-on-error: true`, whole `[tool.mypy]`
  `files` set — legacy.py + the server layer are the known-messy ~137-error
  baseline) plus a **gating** step that runs `mypy --follow-imports=silent`
  over the currently-clean modules and fails on any error there. The command
  and gated list are owned by `tools/check_mypy_gate.py`, which both CI and the
  local review preflight invoke. The list contains modules that already type-check clean (the extracted leaf
  modules, the facades, `cli.py`, and the clean `server_*` modules);
  `--follow-imports=silent` checks their imports for inference but reports
  only errors originating in the listed files, so importing still-dirty
  `legacy.py` does not leak its errors into the gate. Grow the list as
  decomposition extracts clean modules; never drop one to silence a
  regression. `mypy==2.1.0` is pinned exactly in the `dev` extra so the
  baseline count is comparable across runs (single pin site, no lockstep
  script). **coverage:** each pytest job runs
  `--cov=src/anomaly_metric_creator` with no inline report, renames its hidden
  `.coverage` file to a visible lane-specific artifact, and uploads it. The
  coverage job downloads both files, runs `coverage combine`, generates
  `coverage.xml`, and then gates with `coverage report --fail-under=85` — a
  no-regression ratchet ~3 points
  below the measured 88% (xdist/partition jitter headroom), ratcheted UP
  toward the measured number as `07-02-legacy-monolith-decomposition` lands,
  never lowered to pass a red build. XML generation precedes the threshold
  step, and `coverage.xml` is uploaded as a workflow artifact
  (`actions/upload-artifact`, `if: ${{ !cancelled() }}`), so it publishes even
  when the gate trips. `[tool.coverage.run] relative_files = true` makes raw
  data portable across job checkouts. `COVERAGE_CORE=sysmon` (the
  sys.monitoring backend, py3.12+) keeps tracing overhead inside the job
  timeout. `--cov` flags stay CI-only — `addopts` / `required_plugins`
  intentionally do not reference pytest-cov, so local `pytest` runs pay no
  tracing cost.
- `.github/workflows/dependabot-auto-merge.yml` — enables GitHub
  auto-merge (squash) on Dependabot **patch + minor** PRs via
  `dependabot/fetch-metadata`; majors stay manual. The merge waits on the
  required `CI Result`, so a bump that breaks the application gate, the
  lockstep guard, or Socket never lands. Needs repo `allow_auto_merge` plus
  branch protection requiring that aggregate check.
- `.github/workflows/sd-ai-command-pack-sync.yml` — runs weekly and on manual
  dispatch, installs the current `platypeeps/sd-ai-command-pack` `main` into
  the repository, refreshes the generated metadata-only map, and uses one
  fixed automation branch. `peter-evans/create-pull-request` no-ops on an
  empty diff; a real diff opens or updates one PR and arms normal squash
  auto-merge. It never pushes to `main` or bypasses branch protection. Both
  writes use the scoped `SD_AI_COMMAND_PACK_PR_TOKEN` Actions secret so the
  repo-wide Actions create/approve setting remains disabled; the token needs
  contents, pull-request, and workflow write access.
- `.github/workflows/ci.yml` also runs `Windows collection (advisory)` on pull
  requests: locked Python 3.14 dev dependencies plus
  `pytest --collect-only -q` on `windows-latest`. `continue-on-error: true`
  and exclusion from `test`/`CI Result` dependencies keep the lane visible but
  non-blocking; it is not a second full test matrix.
- `.github/workflows/codeql.yml` — explicit CodeQL analysis for both
  `python` and `actions`, advisory on PRs (not a required branch-protection
  context; `CI Result` is the required check). Analysis runs on
  opened/reopened/ready PRs and `full-ci`-labeled updates, skips on plain
  synchronize events, and always runs on push to `main`. A skipped analysis
  produces no code-scanning summary check, so `CodeQL` must not be made a
  required context while this gating is in place. The `full-ci` label's
  lifetime is intentionally asymmetric: the application and Socket jobs in
  ci.yml honor it **one-shot** (only at the `labeled` event; a later plain
  synchronize drops the cost-gated full matrix/scan back to the quick lane
  unless auto-merge is armed or dependency/workflow files changed), while
  codeql.yml honors it
  **persistently** — its synchronize arm re-checks the label set
  (`contains(github.event.pull_request.labels.*.name, 'full-ci')`) on every
  push, so security analysis runs for the life of a flagged PR. Do not unify
  the two by making CodeQL one-shot (that cuts security coverage). Both
  semantics are pinned in `tools/check_ci_review_contract.py` (a positive
  anchor on codeql's persistent re-check; a `_require_not_contains` guard
  keeping that form out of ci.yml) so neither drifts silently.
- `.github/workflows/ci.yml` includes a Socket supply-chain job (`socketcli`)
  that flags risky dependency
  *changes* (install scripts, new capabilities, typosquats, compromised
  releases) that Dependabot's CVE scanning misses. It no-ops to success
  until the `SOCKET_SECURITY_API_KEY` repo secret is set, so it never
  blocks before Socket is configured.

The `github-actions` Dependabot ecosystem keeps these workflows' action
pins current.

### Workflow pip lint

`tools/check_workflow_pip.py` forbids bare or unpinned `pip install` in
`.github/workflows/*.yml`: after `actions/setup-python` runs, a bare `pip`
can resolve to a different interpreter than the one just selected, so the
install lands in the wrong environment. The robust form is
`python -m pip install PACKAGE==VERSION` (`uv pip install PACKAGE==VERSION`
is also accepted), with direct third-party installs exactly pinned so
workflow tooling is reproducible. PR #118 shipped a bare `pip install` in the
former standalone Socket workflow; this lint — the one cleanly-mechanical
pattern surfaced by the
all-PR review sweep (the higher-recurrence patterns like doc-drift and the
heavy-read test-resource rule are size/context-dependent and live as
checklist prose, not lints) — catches it structurally instead of relying on
Copilot to flag it on each new workflow.

Wired as the `workflow-pip` pre-commit hook
(`files: ^\.github/workflows/.*\.ya?ml$`, `pass_filenames: true`) and usable
standalone: `tools/check_workflow_pip.py .github/workflows/ci.yml`.
Detection accepts `python -m pip`, combined short module flags such as
`python -Im pip`, `uv pip`, and `pipx`; a line mixing a good and a bare
invocation is still flagged (the scan finds the bare occurrence past the
excluded one). Non-bare installs reject `--upgrade` / `-U` and package specs
without `==`. Exempt a line with a trailing `# pip-lint: allow`
(trailing-only, like the role-name lint marker — `path.exists()` is checked
before the read so a bad path is exit `2`, not a violation). Exit codes:
`0` clean / `1` at least one bare or unpinned `pip install` / `2` argument or
I/O error. Acceptance tests live in `tests/test_workflow_pip_lint.py`; the
script is stdlib-only.

## Tests

Tests live in `tests/` and write only into `tmp_path` (never `iot_logs/`). The suite
runs full 1-day and 7-day generations end-to-end via `main()` and exercises the
vectorized `generate_component()` path. Run with `.venv/bin/pytest` after installing
the `dev` extra (see [README.md](README.md#tests)).

### Parallel execution (`pytest-xdist`)

`pyproject.toml` pins `addopts = "-ra --dist loadfile -n 4"` and declares
`required_plugins = ["pytest-xdist"]`, so every `.venv/bin/pytest`
invocation runs across 4 worker processes by default, distributes tests by
file (whole files stay on one worker), and fails fast with a clear message
if `pytest-xdist` is missing from the active environment. This is the normal
and measured-fastest local full-suite path: the 2026-07-20 checkout completed
in 253.36s.

Session-scoped fixtures in `tests/conftest.py` (`one_day_run_a`,
`one_day_run_b`, `seven_day_run`, `n3_one_day_dataset_dir`, …) are
lazily instantiated **per worker** the first time a worker touches a test
that requests them; peak fixture RAM therefore scales with the number of
distinct workers that hit each fixture, not with the worker count alone.
`--dist loadfile` is the right grouping for this conftest because it keeps
every test in a file on the same worker: under xdist's default `--dist
load` (per-test distribution) a single file's tests can scatter across
every worker and force each of them to instantiate the file's shared
fixtures. With `--dist loadfile`, a session fixture shared by multiple files
can still be built on `min(consuming files, workers)` processes, but each file
stays on one worker. Four workers are the practical saturation point for this
file-granular suite; wider local pools have not produced a material speedup.

Override on the command line for debugging or lower-memory hosts:

```
.venv/bin/pytest -n 0   # in-process; required for `pdb` / true serial
.venv/bin/pytest -n 2   # lower-memory parallel fallback
```

`-n 1` is not a true serial run — xdist still spawns one worker subprocess,
which breaks interactive debuggers like `pdb`. Use `-n 0` instead when you
need in-process execution.

CI's heavy/light split isolates memory on the hosted runner; it is not a local
speed optimization. A serial heavy partition avoids session-fixture fan-out,
but on the same 2026-07-20 checkout it took 345.01s by itself, already slower
than the entire default run. Use that split only when memory pressure requires
it, not as the normal developer command.

The `heavy` marker partitions the suite for the 16 GB PR runner (see
"Continuous integration" above). It is **auto-applied** — never hand-write
`@pytest.mark.heavy` — by `pytest_collection_modifyitems` in
`tests/conftest.py`, which marks any collected test whose fixture closure
(`item.fixturenames`) intersects `_HEAVY_SESSION_FIXTURES` or
`_HEAVY_MODULE_FIXTURES`. It also marks indirect
`request.getfixturevalue` consumers when a parametrized string names a
registered heavy fixture, because those targets do not enter the fixture
closure. Every registered name must resolve to one fixture definition; do not
shadow a registered heavy fixture name in another test module. Add a new
GB-scale fixture to the appropriate frozenset — do not list test files in the
workflow. `pytest -m heavy` and `pytest -m "not heavy"` both use xdist
`-n 2 --dist loadfile` in separate jobs and partition the full suite; the
decision is unit-tested in
`tests/test_heavy_marker.py` via the pure classification helpers plus live
collection and fixture-definition guards.

Tests must remain order-independent and file-isolated for the default
parallel mode to stay sound. Two existing properties of the suite make this
safe: every test writes only into a fresh `tmp_path` (no shared output
directory between tests), and every `main()` invocation receives an
explicit `--seed` so RNG draw order is independent of pytest's collection
order. Do not introduce cross-file shared mutable state (module-level
caches, file system fixtures outside `tmp_path`, environment variables
set without `monkeypatch`) — `pytest-xdist` will distribute those tests
to different workers and you will get a non-reproducible failure.

The canonical scenario catalog — slugs, severities, `days_required`, and
`components_touched` — lives in the [README scenario catalog](README.md#scenario-catalog)
table. Tests should be derived from `amc.SCENARIOS` (and parametrized off it where
practical) rather than hard-coding slug lists, so new scenarios are automatically
covered without test edits.

### Scenario selector test layout

The `--scenarios` / `--exclude-scenarios` selector matrix is covered across three
test files:

- `tests/test_args.py` — `parse_args`-only coverage: defaults, case-insensitivity,
  whitespace tolerance, single-slug / multi-slug parsing, unknown-slug rejection.
- `tests/test_scenarios.py` — in-process composition matrix:
  - `test_compose_scenarios_x_signal_level_*` — severity gate drops the slug and
    emits exactly one stderr WARNING per dropped slug.
  - `test_compose_scenarios_x_duration_days_*` — duration gate drops the slug and
    emits exactly one stderr WARNING per dropped slug.
  - `test_compose_scenarios_x_components_*` — `components_touched` ∩ `--components`
    determines survival; disjoint drops are silent (no WARNING).
  - `test_compose_scenarios_x_exclude_scenarios_*` — exclusion wins over allowlist
    on overlap and is silent.
  - `test_validation_scenarios_*` / `test_validation_exclude_scenarios_*` —
    unknown slugs and `all`+explicit-slug mixes exit non-zero with a clear error
    message naming the offending slug and the catalog.
  - `test_warning_*` — exactly one WARNING line per dropped slug, matching the
    `WARNING: scenario <slug> requires …; skipped.` convention.
  - `test_resolve_scenarios_warning_order_is_deterministic` — WARNING lines
    appear in sorted-slug order across runs, regardless of dict iteration.
  - `test_anomaly_count_with_scenarios_*` — `--anomaly-count` restricts the
    sampling pool to the active scenarios and stays byte-deterministic for a
    given `--seed`.
  - `test_default_*_csvs_byte_identical` + `test_high_seven_day_capped_*` —
    locked SHA-256 hashes for default and `--signal-level high
    --anomaly-count 100` runs; protects against silent spec-order drift.
- `tests/test_cli.py` — subprocess-level smoke for `--scenarios` and
  `--exclude-scenarios`: help text presence, end-to-end run success on a
  single slug, non-zero exit for unknown slugs and `all`+explicit mixes.
- `tests/test_correctness.py` —
  `test_scenarios_all_matches_no_flag_byte_for_byte` is the default-equivalence
  regression: explicit `--scenarios all` must produce identical per-component
  CSV and `anomalies.csv` bytes as omitting the flag, at 1 and 7 days.

Selector composition order (locked by the plan):
`--scenarios` → `--exclude-scenarios` → `--signal-level` → `--duration-days`
→ `--components`. Severity and duration drops are loud (WARNING); the
component filter drop is silent because the user already restricted the
allowlist on purpose.
