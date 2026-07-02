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
`src/anomaly_metric_creator/server.py`; `src/anomaly_metric_creator/trace_bundle.py`;
`tests/test_cli_surface.py`; `tests/test_trace_bundle.py`.

The canonical artifact flag is `--emit` with tokens `metrics`, `logs`,
`traces`, `gauges`, `schema`, and `combined`. `combined` requires `metrics`,
and `schema` has no artifact dependency. Sources: `README.md`; `CLAUDE.md`;
`src/anomaly_metric_creator/legacy.py`; `tests/test_emit_selection_hygiene.py`;
`tests/test_cli_surface.py`.

The canonical OTEL selection flag is `--otel-send` with `logs`, `metrics`,
`traces`, `gauges`, `all`, or `none`. Streaming is off unless selected; selected
signals are authoritative and unselected endpoints must not leak in from env
defaults. Sources: `README.md`; `CLAUDE.md`;
`src/anomaly_metric_creator/legacy.py`; `tests/test_cli.py`;
`tests/test_otel_gauges.py`; `tests/test_cli_surface.py`.

New flags must be placed in the right parser/group, reconciled through the
existing namespace flow, tested in isolation, and checked against interacting
flags and subcommands. Sources: `CLAUDE.md`; `README.md`;
`src/anomaly_metric_creator/legacy.py`; `tests/test_cli_surface.py`;
`tests/test_args.py`.

## Output Contracts

Generated artifacts live under `--output-dir`; cleanup must remove stale files
for artifacts/components that this run will not regenerate while leaving
unknown user files alone. Sources: `CLAUDE.md`; `README.md`;
`docs/application-flow.md`; `src/anomaly_metric_creator/legacy.py`;
`tests/test_emit_selection_hygiene.py`; `tests/test_reporting_artifacts.py`.

`schema.json` is opt-in via `--emit schema`, uses `schema_version`, run
metadata, declared files, component metric metadata, optional dimension blocks,
and topology data, and is the single source consumed by the `validate`
subcommand. Sources: `README.md`; `CLAUDE.md`;
`src/anomaly_metric_creator/legacy.py`; `tests/test_schema_file.py`;
`tests/test_validate_output.py`.

`validate DIR` must read back `schema.json` as untrusted input, validate file
presence, row counts, timestamps, cell types/ranges, dimensions, derived
metrics, anomaly ordering, and topology coupling, and return nonzero on hard
violations unless `--warn` is passed. Sources: `README.md`; `CLAUDE.md`;
`src/anomaly_metric_creator/legacy.py`; `tests/test_validate_output.py`;
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
