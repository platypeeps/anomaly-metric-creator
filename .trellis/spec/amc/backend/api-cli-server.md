# API, CLI, and Server

## Entry Points

`amc`, `anomaly-metric-creator`, and `python3 anomaly-metric-creator.py` must
continue to invoke the same CLI behavior. Package metadata owns the installed
script names; the top-level script remains a compatibility shim. Sources:
`README.md`; `pyproject.toml`; `anomaly-metric-creator.py`;
`src/anomaly_metric_creator/cli.py`; `tests/test_package_entrypoint.py`;
`tests/test_cli.py`.

`main(argv=None)` is the generation entry point and must remain import-safe:
importing the module should not generate files. Sources: `CLAUDE.md`;
`src/anomaly_metric_creator/legacy.py`; `tests/conftest.py`;
`tests/test_package_facades.py`.

## CLI Surface

The supported subcommands are `generate` (implicit default), `combine DIR`,
`validate DIR [--warn]`, `serve [server flags] [generate flags...]`, and
`trace-bundle {summary,search,unsupported,export-csv} BUNDLE`. `combine`,
`validate`, and `trace-bundle` use dedicated parsers and must not route through
the generation parser; `serve` parses server flags first and forwards remaining
generation flags to the normal parser. Sources: `CLAUDE.md`; `README.md`;
`docs/application-flow.md`; `src/anomaly_metric_creator/legacy.py`;
`src/anomaly_metric_creator/cli_args.py`;
`src/anomaly_metric_creator/cli_subcommands.py`;
`src/anomaly_metric_creator/server.py`; `src/anomaly_metric_creator/trace_bundle.py`;
`tests/test_cli_surface.py`; `tests/test_trace_bundle.py`.

The canonical artifact flag is `--emit` with tokens `metrics`, `logs`,
`traces`, `gauges`, `schema`, and `combined`. `combined` **and** `gauges` each
require `metrics` (the parser rejects the combination otherwise); `schema` has
no artifact dependency. Sources: `README.md`; `CLAUDE.md`;
`src/anomaly_metric_creator/cli_args.py`;
`src/anomaly_metric_creator/legacy.py`; `tests/test_emit_selection_hygiene.py`;
`tests/test_cli_surface.py`.

The canonical OTEL selection flag is `--otel-send` with `logs`, `metrics`,
`traces`, `gauges`, `all`, or `none`. Streaming is off unless selected; selected
signals are authoritative and unselected endpoints must not leak in from env
defaults. Sources: `README.md`; `CLAUDE.md`;
`src/anomaly_metric_creator/cli_args.py`;
`src/anomaly_metric_creator/legacy.py`; `tests/test_cli.py`;
`tests/test_otel_gauges.py`; `tests/test_cli_surface.py`.

`--otel-stream-max-events` caps OTLP send attempts that are allowed to start,
not anomaly rows. For the anomaly signal stream, count the first send attempt
across all selected signal endpoints (`logs`, `metrics`, `traces`) so
`--otel-send logs,metrics,traces --otel-stream-max-events 1` starts only the
first selected signal request. A send that has started still follows the
configured retry path. Sources: `README.md`;
`src/anomaly_metric_creator/otel_stream.py`; `tests/test_cli.py`;
`tests/test_otel_gauges.py`.

New flags must be placed in the right parser/group, reconciled through the
existing namespace flow, tested in isolation, and checked against interacting
flags and subcommands. Sources: `CLAUDE.md`; `README.md`;
`src/anomaly_metric_creator/cli_args.py`;
`src/anomaly_metric_creator/cli_subcommands.py`;
`src/anomaly_metric_creator/legacy.py`; `tests/test_cli_surface.py`;
`tests/test_args.py`.

### Scenario: Installed version discovery

#### 1. Scope / Trigger

Release and support reports need one installed-distribution version across the
CLI, package facade, and MCP initialize response.

#### 2. Signatures

- `package_version(*, fallback: str = "0+unknown") -> str`
- `amc --version` and `anomaly-metric-creator --version`
- `anomaly_metric_creator.__version__: str`

#### 3. Contracts

Installed metadata for distribution `anomaly-metric-creator` is authoritative.
The CLI and package facade use `0+unknown` only when that metadata is absent;
the MCP facade passes `fallback="unknown"` to preserve its protocol response.

#### 4. Validation & Error Matrix

- Installed distribution -> return its normalized metadata version exactly.
- Missing distribution metadata -> return the caller-owned fallback.
- Other metadata/runtime failure -> propagate it; do not hide broken installs.

#### 5. Good / Base / Bad Cases

- Good: an installed 0.4.0 wheel reports `0.4.0` through both console scripts
  and `anomaly_metric_creator.__version__`.
- Base: an uninstalled source import reports `0+unknown`.
- Bad: separate hard-coded versions in CLI, package, or MCP code drift from
  `pyproject.toml`.

#### 6. Tests Required

Assert both help tiers list `--version`, the CLI matches
`importlib.metadata.version(...)`, the package facade matches the same value,
and missing metadata yields both the default and MCP-specific fallbacks.

#### 7. Wrong vs Correct

```python
# Wrong: a second release-version owner.
__version__ = "0.4.0"

# Correct: installed metadata with a caller-owned source fallback.
__version__ = package_version()
```

Sources: `pyproject.toml`; `uv.lock`;
`src/anomaly_metric_creator/version.py`;
`src/anomaly_metric_creator/__init__.py`;
`src/anomaly_metric_creator/cli_args.py`;
`src/anomaly_metric_creator/server_mcp.py`; `tests/test_cli.py`;
`tests/test_version.py`.

## Output Contracts

Generated artifacts live under `--output-dir`; cleanup must remove stale files
for artifacts/components that this run will not regenerate while leaving
unknown user files alone. Sources: `CLAUDE.md`; `README.md`;
`docs/application-flow.md`; `src/anomaly_metric_creator/legacy.py`;
`tests/test_emit_selection_hygiene.py`; `tests/test_reporting_artifacts.py`.

Every generated artifact is published **atomically**: a writer stages a
sibling `<name>.tmp` in `--output-dir`, flushes + fsyncs, then `os.replace`s
onto the final path, so a concurrent reader (notably the `amc serve` HTTP
threads while `--continuous-generate` reruns the generator) only ever observes
the complete previous or complete new file. New artifact writers must route
through `_atomic_artifact_open` / `_atomic_write_text` (in `artifacts.py`),
never `open(final_path, "w")`, and their filename must reach the registries
`_known_artifact_filenames()` reads so stale `*.tmp` siblings are swept. Files
this run will regenerate are therefore not deleted by pre-clean (true deletion
is reserved for files the run will not emit). `./otel-activity.log` is exempt:
it lives outside `--output-dir` and appends within a run. Sources: `CLAUDE.md`;
`src/anomaly_metric_creator/artifacts.py`;
`src/anomaly_metric_creator/legacy.py`; `tests/test_atomic_writes.py`.

`schema.json` is opt-in via `--emit schema`, uses `schema_version`, run
metadata, declared files, component metric metadata, optional dimension blocks,
and topology data, and is the single source consumed by the `validate`
subcommand. Sources: `README.md`; `CLAUDE.md`;
`src/anomaly_metric_creator/legacy.py`;
`src/anomaly_metric_creator/schema_impl.py`;
`src/anomaly_metric_creator/validate_impl.py`; `tests/test_schema_file.py`;
`tests/test_validate_output.py`.

`validate DIR` must read back `schema.json` as untrusted input, validate file
presence, row counts, timestamps, cell types/ranges, dimensions, derived
metrics, anomaly ordering, and topology coupling, and return nonzero on hard
violations unless `--warn` is passed. `validate_output` returns structured
`Violation` objects whose string form preserves the historic prose output.
Unknown-file validation tolerates dot-prefixed sidecars such as `.DS_Store` but
continues to hard-fail undeclared non-dot artifact files, including stale
`*.tmp` debris. Sources: `README.md`; `CLAUDE.md`;
`src/anomaly_metric_creator/legacy.py`;
`src/anomaly_metric_creator/validate_impl.py`;
`src/anomaly_metric_creator/validate_cells.py`;
`src/anomaly_metric_creator/validate_topology.py`;
`src/anomaly_metric_creator/validate_topology_instances.py`;
`tests/test_validate_output.py`;
`tests/test_schema_file.py`.

`combine DIR` reads existing per-component CSVs and writes
`combined_metrics_unified.csv`; it must not pre-clean inputs or regenerate
`schema.json` or `gauges.csv`. Sources: `README.md`; `CLAUDE.md`;
`src/anomaly_metric_creator/legacy.py`; `src/anomaly_metric_creator/combine.py`;
`tests/test_combine.py`; `docs/application-flow.md`.

## Serve Mode

`amc serve` must generate once before listening unless `--no-generate` is set,
must append `--otel-send none` to startup generation so the listener is not
blocked by OTEL, and must serialize continuous regeneration with OTEL replay
when continuous mode and OTEL are both active. Sources: `CLAUDE.md`;
`README.md`; `src/anomaly_metric_creator/server.py`; `tests/test_server.py`.

Serve config files are JSON or YAML objects with top-level `server` and
`generate` maps. Config keys use long flag names with underscores; values are
converted to flags before parsing, and explicit CLI flags come after config
defaults so they win. Sources: `README.md`; `CLAUDE.md`;
`src/anomaly_metric_creator/server.py`; `tests/test_server.py`.

The command API accepts either a command string or argv list, parses through
the simulator command parser, returns deterministic stdout/stderr/exit-code
triples, and never shells out. Sources: `README.md`; `CLAUDE.md`;
`src/anomaly_metric_creator/server.py`; `src/anomaly_metric_creator/server_ops.py`;
`src/anomaly_metric_creator/server_commands.py`; `tests/test_server.py`.

Every command or real-client Kubernetes API request should create a
`CommandTrace` so supported, partial, and unsupported operator behavior remains
visible in debug search and backlog views. Sources: `CLAUDE.md`; `README.md`;
`src/anomaly_metric_creator/server.py`;
`src/anomaly_metric_creator/server_traces.py`; `tests/test_server.py`;
`tests/test_trace_bundle.py`.

## MCP Facade and Eval Mode

`amc serve` exposes an MCP (Model Context Protocol) endpoint at `POST /mcp`: a
stateless streamable-HTTP JSON-RPC layer (`initialize`, `tools/list`,
`tools/call`, `ping`; notifications get 202, `GET /mcp` gets a 405 JSON-RPC
refusal) plus a read-only tool registry (`MCP_TOOLS`). Protocol behavior, error
codes, and the import-time-validated registry live in `server_mcp.py`;
`server.py` only routes the request body. Every `tools/call` is recorded as a
`CommandTrace` under command family `mcp`, so unknown-tool and schema-invalid
calls accumulate in `/v1/debug/unsupported` like kubectl misfires. Tools answer
only from what the run already produced (the simulated clock, resolved specs,
serialized topology, the per-component CSVs, and `metric_report.log`) and are
subject to the **ground-truth wall**: no MCP tool may read `anomalies.csv` or
the `SCENARIOS` registry, because the MCP surface is what an AI agent under
evaluation sees while the anomaly manifest is the eval harness's scoring
rubric. When adding a tool, extend `MCP_TOOLS`, keep it inside the wall, and add
core behavior coverage in `tests/test_server_mcp.py` plus one schema-valid
minimal-argument entry in the registry-coupled eval/non-eval sweep in
`tests/test_server_eval_mode.py`. That sweep must keep exact key equality with
`MCP_TOOLS`. Its structural guard scans each handler and transitively called
module-local helpers for rubric-bearing state or files; only the eval-gated
`get_logs` and `deduplicate_logs` call graphs may reach `metric_report.log`.
Sources: `CLAUDE.md`; `README.md`;
`src/anomaly_metric_creator/server_mcp.py`;
`src/anomaly_metric_creator/server.py`; `tests/test_server_mcp.py`;
`tests/test_server_eval_mode.py`.

`amc serve --mcp-eval-mode` is a stricter posture: the run's active scenarios
and anomaly manifest are the scoring rubric, so eval mode hides every
rubric-bearing surface. `SimulationState.eval_mode` is the single source of
truth. Route classification lives in one registry in `server.py`
(`_RUBRIC_ENDPOINT_EXACT` + `_RUBRIC_ENDPOINT_PREFIXES`, judged by
`_rubric_endpoint`): the hidden surfaces are `/v1/anomalies`, `/v1/scenarios`,
`/v1/state`, `/v1/logs/stream`, the whole `/v1/debug` prefix, and the `/` +
`/debug` console shell; a rubric endpoint returns `404` (chosen over `403` for
fingerprint-resistance) **before auth for every HTTP method**.
`test_every_dispatched_route_is_classified` fails if any dispatched route is
left unclassified, so a new endpoint must be placed in the rubric or
investigation registry, never left to default open. Beyond endpoint hiding, no
active-scenario identifier may appear on any investigation-open surface — the
`get_logs`/`deduplicate_logs` MCP tools refuse in eval mode (because
`metric_report.log` is a verbatim manifest rendering), and the active scenario
slugs are withheld from the ConfigMap `SCENARIOS` key, pod `scenario_ids`,
`kubectl exec … env`, `helm get values`, the Helm release payload, and the
`/v1/commands` trace echo. Sources: `CLAUDE.md`; `README.md`;
`src/anomaly_metric_creator/server.py`;
`src/anomaly_metric_creator/server_ops.py`;
`src/anomaly_metric_creator/server_mcp.py`;
`tests/test_server_eval_mode.py`.

## HTTP, Kubernetes, and Helm API

The stdlib server exposes app endpoints (`/v1/state`, `/v1/commands`,
`/v1/debug/...`, `/v1/logs/stream`, time controls, and mutation reset) plus a
Kubernetes-compatible facade for real `kubectl` and Helm clients. Sources:
`README.md`; `CLAUDE.md`; `src/anomaly_metric_creator/server.py`;
`src/anomaly_metric_creator/server_kubernetes.py`;
`src/anomaly_metric_creator/server_helm.py`; `tests/test_server.py`.

The Kubernetes facade must be backed by `resource_snapshot()` and
`SimulationMutations`, not a second resource model. It includes discovery,
Table responses, core resources, workloads, metrics, authorization reviews,
pod logs, and Helm-shaped release Secret storage for Helm 4 compatibility.
Sources: `README.md`; `CLAUDE.md`;
`src/anomaly_metric_creator/server_ops.py`;
`src/anomaly_metric_creator/server_kubernetes.py`;
`src/anomaly_metric_creator/server_helm.py`; `tests/test_server.py`.

The facade advertises Kubernetes v1.36.2 from one server-ops constant and the
full CI light lane smoke-tests it with checksum-pinned kubectl v1.36.2 and Helm
v4.2.0 binaries. Treat the client pins, checksums, advertised version, README
tested-version statement, real-client smoke selectors, and deterministic CI
guard as one update contract. Sources: `.github/workflows/ci.yml`;
`src/anomaly_metric_creator/server_ops.py`; `tests/test_server.py`;
`tools/check_ci_review_contract.py`; `tests/test_ci_review_contract.py`;
`README.md`; `docs/DEVELOPMENT_CYCLE.md`.

Kubernetes mutation and event identity must include namespace anywhere a real
cluster would treat namespace as part of object identity. Generated pod names
such as replacement/recreated pods must map back to their owning component when
mutations are rendered, and mutating subresources must be accepted only through
explicit allowlists rather than any non-empty subresource path. Sources:
`src/anomaly_metric_creator/server_mutations.py`;
`src/anomaly_metric_creator/server_ops.py`;
`src/anomaly_metric_creator/server_kubernetes.py`; `tests/test_server.py`.

Helm compatibility uses simulator JSON inside double-base64 gzip
`helm.sh/release.v1` Secret payloads; do not document or treat these as native
Helm 3 protobuf release objects unless the encoder changes. Sources:
`README.md`; `CLAUDE.md`; `src/anomaly_metric_creator/server_ops.py`;
`src/anomaly_metric_creator/server_helm.py`; `tests/test_server.py`.

## Trace Bundles

`GET /v1/debug/commands/export` and `POST /v1/debug/commands/import` move
portable command-trace JSON histories between live stores. `amc trace-bundle`
must consume the exported shape offline for summary, search, unsupported
grouping, and CSV export without starting the HTTP server. Sources:
`README.md`; `CLAUDE.md`; `src/anomaly_metric_creator/server.py`;
`src/anomaly_metric_creator/server_traces.py`;
`src/anomaly_metric_creator/trace_bundle.py`; `tests/test_server.py`;
`tests/test_trace_bundle.py`.

Bundle import/read paths validate top-level shape, API/schema version, trace
entries, declared trace counts, and integer fields before coercion; booleans
are not accepted as integers. Sources: `CLAUDE.md`;
`src/anomaly_metric_creator/trace_bundle.py`;
`src/anomaly_metric_creator/server_traces.py`; `tests/test_trace_bundle.py`;
`tests/test_server.py`.

Trace import/export code should use the shared trace scalar and tuple validators
rather than direct `int(...)` or `tuple(...)` calls on payload data. Invalid
trace entries must raise validation errors with the entry index instead of being
silently filtered out, and offline bundle search should preserve the live debug
API contract: exact filter semantics, recent results ordered by trace id, and
summary timestamp bounds computed from the trace set rather than file order.
Sources: `src/anomaly_metric_creator/server_traces.py`;
`src/anomaly_metric_creator/trace_bundle.py`;
`tools/check_trace_payload_antipatterns.py`; `tests/test_trace_bundle.py`;
`tests/test_trace_payload_antipatterns_lint.py`.
