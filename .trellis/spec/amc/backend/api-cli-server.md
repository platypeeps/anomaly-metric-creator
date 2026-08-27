# API, CLI, and Server

## Entry Points

`amc`, `anomaly-metric-creator`, and `python3 anomaly-metric-creator.py` must
continue to invoke the same CLI behavior. Package metadata owns the installed
script names; the top-level script remains a compatibility shim. Sources:
`README.md`; `pyproject.toml`; `anomaly-metric-creator.py`;
`src/anomaly_metric_creator/cli.py`; `tests/test_package_entrypoint.py`;
`tests/test_cli.py`.

`main(argv=None)` is the generation entry point and must remain import-safe:
importing the module should not generate files. Sources:
`src/anomaly_metric_creator/legacy.py`; `tests/conftest.py`;
`tests/test_package_facades.py`.

## CLI Surface

The supported subcommands are `generate` (implicit default), `combine DIR`,
`validate DIR [--warn]`, `serve [server flags] [generate flags...]`, and
`trace-bundle {summary,search,unsupported,export-csv} BUNDLE`. `combine`,
`validate`, and `trace-bundle` use dedicated parsers and must not route through
the generation parser; `serve` parses server flags first and forwards remaining
generation flags to the normal parser. Sources: `README.md`;
`docs/application-flow.md`; `src/anomaly_metric_creator/legacy.py`;
`src/anomaly_metric_creator/cli_args.py`;
`src/anomaly_metric_creator/cli_subcommands.py`;
`src/anomaly_metric_creator/server.py`; `src/anomaly_metric_creator/trace_bundle.py`;
`tests/test_cli_surface.py`; `tests/test_trace_bundle.py`.

The canonical artifact flag is `--emit` with tokens `metrics`, `logs`,
`traces`, `gauges`, `schema`, and `combined`. `combined` **and** `gauges` each
require `metrics` (the parser rejects the combination otherwise); `schema` has
no artifact dependency. Sources: `README.md`;
`src/anomaly_metric_creator/cli_args.py`;
`src/anomaly_metric_creator/legacy.py`; `tests/test_emit_selection_hygiene.py`;
`tests/test_cli_surface.py`.

The canonical OTEL selection flag is `--otel-send` with `logs`, `metrics`,
`traces`, `gauges`, `all`, or `none`. Streaming is off unless selected; selected
signals are authoritative and unselected endpoints must not leak in from env
defaults. Sources: `README.md`;
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
flags and subcommands. Sources: `README.md`;
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
unknown user files alone. Sources: `README.md`;
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
it lives outside `--output-dir` and appends within a run. Sources:
`src/anomaly_metric_creator/artifacts.py`;
`src/anomaly_metric_creator/legacy.py`; `tests/test_atomic_writes.py`.

`schema.json` is opt-in via `--emit schema`, uses `schema_version`, run
metadata, declared files, component metric metadata, optional dimension blocks,
and topology data, and is the single source consumed by the `validate`
subcommand. `_load_schema_document` rejects unknown `schema_version` values
outright, so a v1 document fails fast under a v2 reader and vice versa. The
writer only ever emits `topology_mode: "realistic"` — the `independent` contrast
alias no longer parses — but **the reader still honors `"independent"`** so
documents produced under the historic mode keep validating, and the validator's
topology-coupling check short-circuits under it. Sources: `README.md`;
`src/anomaly_metric_creator/legacy.py`;
`src/anomaly_metric_creator/schema_impl.py`;
`src/anomaly_metric_creator/validate_impl.py`; `tests/test_schema_file.py`;
`tests/test_validate_output.py`.

`validate DIR` must read back `schema.json` as untrusted input, validate file
presence, row counts, timestamps, cell types/ranges, dimensions, derived
metrics, anomaly ordering, and topology coupling, and return nonzero on hard
violations unless `--warn` is passed. `validate_output` returns structured
`Violation` objects whose string form preserves the historic prose output. A
zero-variance source or target column in the topology-coupling check is itself a
coupling regression (Pearson is undefined), and the violation message must name
**which side** was constant rather than the ambiguous "source or target"
both-sides form.
Unknown-file validation tolerates dot-prefixed sidecars such as `.DS_Store` but
continues to hard-fail undeclared non-dot artifact files, including stale
`*.tmp` debris. Sources: `README.md`;
`src/anomaly_metric_creator/legacy.py`;
`src/anomaly_metric_creator/validate_impl.py`;
`src/anomaly_metric_creator/validate_cells.py`;
`src/anomaly_metric_creator/validate_topology.py`;
`src/anomaly_metric_creator/validate_topology_instances.py`;
`tests/test_validate_output.py`;
`tests/test_schema_file.py`.

`combine DIR` reads existing per-component CSVs and writes
`combined_metrics_unified.csv`; it must not pre-clean inputs or regenerate
`schema.json` or `gauges.csv`. Sources: `README.md`;
`src/anomaly_metric_creator/legacy.py`; `src/anomaly_metric_creator/combine.py`;
`tests/test_combine.py`; `docs/application-flow.md`.

Both long-form file writers — `gauges.csv` and the long-form combine output —
delegate their merge to the single shared `csv_layout.write_long_form_merge`, so
the two cannot drift. `combine_logs_unified` and `write_gauges_csv` each
dispatch on header inspection (`_scan_component_csv_headers`): dimensionless
input keeps the classic wide / 4-column shape byte-identically, while any
per-component CSV carrying the `id, host, pod, az, region, tenant` prefix
switches to the 10-column long form. The two layouts order differently on
purpose — the wide layout uses the caller-supplied `components` order verbatim
for the column sequence, while the long layout sorts components alphabetically
for the equal-timestamp tie-break because the row's own `component` cell carries
identity. Long-form tie-break order is `(component, instance_id, metric)`, and
empty/dropped cells are skipped (row presence encodes "this measurement was
emitted"), unlike the wide layout's empty string in the corresponding column.
Sources: `src/anomaly_metric_creator/csv_layout.py`;
`src/anomaly_metric_creator/combine_impl.py`;
`src/anomaly_metric_creator/gauges_impl.py`; `tests/test_gauges_file.py`;
`tests/test_combine.py`.

Two performance contracts attach to those writers. First, generated combines
pass `assume_monotonic_wide_components=set(combine_components)` so the writer
skips a second full pass proving monotonicity for files `main()` just emitted;
this is a trusted allowlist for freshly generated non-DST wide CSVs only —
external `combine DIR` invocations still run
`_wide_component_rows_are_monotonic` before taking the streaming `heapq.merge`
path. Second, the long-form merge holds one open handle per
`(component, instance)` source for the merge's lifetime (`heapq.merge` primes
every iterator), so at max fan-out — 14 components × 20 instances = 280 sources
— it can exceed the default macOS soft limit of 256.
`_ensure_long_form_fd_capacity(len(sources))` therefore reads `RLIMIT_NOFILE`,
raises the soft limit to fit (capped by the hard limit), and otherwise exits
with a message naming the needed count and the user-facing levers
(`--instances-per-component`, `--components`, `ulimit -n`). It no-ops on
Windows, where `open()` surfaces the real error at write time; the wide-form
paths never trip it because they stream one handle per component. Sources:
`src/anomaly_metric_creator/csv_layout.py`;
`src/anomaly_metric_creator/combine_impl.py`;
`src/anomaly_metric_creator/gauges_impl.py`; `tools/benchmark_combine.py`;
`tests/test_gauges_file.py`; `tests/test_combine.py`.

## Serve Mode

`--config` accepts a JSON/YAML file with `server` and `generate` sections. The
contract is **the config must not be the reason the run fails, and when it is,
the error names the file** -- not "everything in the file is checked". Keys are
always checked, in both sections, and a `generate` key naming a *serve* flag is
refused outright: the combined parse would let the serve parser take it first,
so it would configure the server from the wrong section without ever reaching
generation. Values are checked as far as they affect the run -- a `generate`
section that only fails in isolation is not a failure. That qualifier does not
extend to a value the CLI overrides: argparse converts every occurrence, not
just the winning one, so a bad overridden value still breaks the run and is
still refused.
Config values are emitted as `--flag=value`, one token each: as a separate
token a value starting with `-` is read as an option, so `namespace: "-weird"`
failed as an unrecognized flag. `server` keys check against the
`_SERVE_CONFIG_SERVER_KEYS` allowlist; `server`
values are checked by `_probe_config_server_argv`, which parses the
config-derived server argv through the real serve parser on its own, the
counterpart of the `generate` probe below. It also refuses what the parser
does not *consume*: every token there came from an allowlisted key, so a
leftover means `_SERVE_CONFIG_SERVER_KEYS` has drifted from the parser. Nothing
else can catch that -- the real parse must keep tolerating unconsumed tokens,
since generate flags travel in the same argv. Without it a bad value fell through
to the combined parse and failed as a bare `argument --port: invalid int
value`, naming no file. `generate` keys have no
introspectable allowlist -- `parse_args` builds its parser inline -- so **the
real parser is the allowlist**: the config-derived generate argv is parsed on
its own in a probe that traps `SystemExit` and captures both streams, and
argparse's own diagnostic is embedded in a `ValueError` naming the config path.
Do not hand-maintain a second list of generate keys; it would drift from the
parser on every new flag. The probe is the same parse `serve_main` runs later,
so it must reject nothing that would have survived anyway -- but a valid key
with an invalid *value* does fail at load, which is intended.

Holding both of those at once takes two parses. Several generate gates are
cross-flag (the preflight cell cap multiplies interval, duration, metric count,
components, and instances), so the config section judged *in isolation* can
fail while the real run is fine -- `interval_seconds: 1` against the defaults.
The probe therefore runs after the combined parse and refuses only when the
merged `generate_argv`, the argv the run will actually parse, fails too. Report
the config's own diagnostic, since that is the attributable one; decide on the
merged argv. Two rules follow, and both are load-bearing. A
merged failure counts only if it is the *same* failure the config section
produced on its own -- otherwise the run is breaking on something the config
did not cause, and naming the file sends the operator to the wrong place, so
the real parse reports it later instead. And the exit-`0` check has to run
*before* the combined parse, because the serve parser owns `--help` too and
would print serve usage and exit before the config was ever judged.

Every diagnostic embedded in a config error passes through
`_redact_config_values` first. Values are attached to their flags, so argparse
quotes them back verbatim; a typo'd key is by definition on no sensitive-key
list, so every value is masked and the flag name kept. Validation that is genuinely cross-flag and lives *outside* the
parsers -- `--cors-allow-origin '*'` requiring `--auth-token`, for one -- stays
in `serve_main` on the merged arguments; neither probe can see it, and neither
should try. An exit-`0`
parse is still refused -- `help: true` would print usage and exit instead of
configuring a run -- but it must not be *described* as a rejection by the
parser, because the parser accepted the flag. It gets its own diagnostic.

`null` and `false` are the two shapes that emit no flag at all, so the argv
probe cannot see those keys and a typo would vanish silently. They are not
refused outright -- that would refuse `otel_verbose: false`, a real switch
whose off state is exactly what the operator wrote. `_vouch_no_flag_generate_keys`
asks the same real parser about the bare flag instead: a flag that parses on
its own is a switch, so dropping the key keeps its documented meaning of "use
the default", while a typo or a value-taking flag is refused naming the file.
Keep this a parser question; never answer it from a hand-maintained list of
switch names. Reading "the bare flag parses" as "this key is a switch" holds
only while every value-taking generate option requires its value: `nargs="?"`
and `nargs="*"` would make a value-taking flag parse bare and be vouched
silently. Neither is used. A test pins this by *introspecting the real
parser* -- captured by spying on `_reconcile_cli_surface`, which is handed it
mid-parse -- not by searching the source text, which any respelling or an
option declared elsewhere would defeat. Under `server`, both shapes still mean "use the default", since
those key names are already allowlisted and cannot hide a typo. *Every*
`_load_serve_config` refusal routes through `_config_error` -- suffix, read,
parse, shape, and key arms alike -- so none can drift back to a bare message
the way the unknown-`server`-key arm did while the generate arm was
attributed. `_config_mapping_to_argv` stays a pure conversion -- all validation
lives in the probe layer.

`--config` is an untrusted read-back boundary, and the YAML reader admits
shapes JSON cannot: a non-string key (`1:`, `true:`) reaches
`key.replace("_", "-")` and raises `AttributeError`, escaping the `ValueError`
refusal that names the file, and cannot be sorted alongside string keys for a
diagnostic either. Both sections' keys are checked for `str` on the reader
side, and any key set that gets sorted into a message is `str()`-mapped first.
Sources: `src/anomaly_metric_creator/server.py`; `README.md`;
`tests/test_server.py`. Precedence is unchanged: config flags are
placed ahead of user flags so explicit CLI flags still win. This whole cluster lives in `src/anomaly_metric_creator/server_config.py`, a
leaf that reads nothing from `server.py`; `server.py` re-imports every name, so
the historic `server.<name>` surface is unchanged. Sources:
`src/anomaly_metric_creator/server_config.py`;
`src/anomaly_metric_creator/server.py`; `README.md`; `tests/test_server.py`.

`amc serve` must generate once before listening unless `--no-generate` is set,
must append `--otel-send none` to startup generation so the listener is not
blocked by OTEL, and must serialize continuous regeneration with OTEL replay
when continuous mode and OTEL are both active. Sources:
`README.md`; `src/anomaly_metric_creator/server.py`; `tests/test_server.py`.

After the three startup URL lines, `serve_main` must print a print-only
inspection banner (`_print_inspection_banner`) with a copyable kubeconfig
fetch, namespaced `kubectl get pods`/`get events` and `helm list` examples,
and a `POST /v1/mutations/reset` hint. The banner is security-sensitive in one
place: a real `--auth-token` is echoed into the curl examples only on a
loopback bind; a non-loopback bind must render a `$AMC_TOKEN` placeholder
instead so the printed banner commands do not carry the token into a remote
shell history or log (the operator's launch invocation still holds it). The
`Active scenarios:` line is suppressed entirely under `--mcp-eval-mode`
(active slugs are the eval harness's scoring rubric). The banner changes no
serve security default. This is the interactive failure-mode launcher: an
environment is launched by `amc serve --scenarios <slug>`, not a separate
command. Sources: `README.md`; `src/anomaly_metric_creator/server.py`;
`tests/test_serve_main_wiring.py`.

Serve config files are JSON or YAML objects with top-level `server` and
`generate` maps. Config keys use long flag names with underscores; values are
converted to flags before parsing, and explicit CLI flags come after config
defaults so they win. Sources: `README.md`;
`src/anomaly_metric_creator/server.py`; `tests/test_server.py`.

The command API accepts either a command string or argv list, parses through
the simulator command parser, returns deterministic stdout/stderr/exit-code
triples, and never shells out. Sources: `README.md`;
`src/anomaly_metric_creator/server.py`; `src/anomaly_metric_creator/server_ops.py`;
`src/anomaly_metric_creator/server_commands.py`; `tests/test_server.py`.

Every command or real-client Kubernetes API request should create a
`CommandTrace` so supported, partial, and unsupported operator behavior remains
visible in debug search and backlog views. Sources: `README.md`;
`src/anomaly_metric_creator/server.py`;
`src/anomaly_metric_creator/server_traces.py`; `tests/test_server.py`;
`tests/test_trace_bundle.py`.

Server request handlers run under a `ThreadingHTTPServer`, so every per-request
path must stay flat as the run's data and trace history grow — a poll that is
linear in history size is a defect, not just slow. Three standing hot-path
conventions:

- **Trace store owns one long-lived SQLite connection and one long-lived JSONL
  append handle**, never `sqlite3.connect` per operation or `open(persist,"a")`
  per insert. The connection is opened once with `check_same_thread=False` and
  touched only through `_locked_conn` (acquires `_sqlite_lock`, commits on clean
  exit, rolls back on error); the JSONL handle is written under its own
  `_jsonl_lock` off the ring `_lock` and flushed per write for durability.
  `_locked_conn` is non-reentrant, so a method that re-enters the store (e.g.
  `_replace_sqlite_traces` calling `_load_sqlite_tail`) must release the guard
  first. SQLite retention runs immediately per insert because
  `test_command_trace_sqlite_retention_*` asserts trimmed state right after the
  insert; do not batch it behind an insert counter.
- **`/v1/state` reports the unsupported-command count via
  `unsupported_fingerprint_count()` (`COUNT(DISTINCT fingerprint)` /
  in-memory set), never `len(unsupported_summary())`.** The full
  `unsupported_summary()` is memoized on a store generation
  (`_sqlite_gen` under `_sqlite_lock`, or the ring `_version` under `_lock`) so
  repeated `/v1/debug/unsupported` polls at an unchanged head are O(1); the
  generation and the rows must be read in the *same* locked section so the
  cached `(gen, summary)` pair is internally consistent. The memoized result
  stays byte-identical to `_unsupported_summary_from_traces`, pinned by an
  oracle test in `tests/test_server.py`.
- **MCP window-scan tools string-gate CSV rows before `strptime`.** The
  per-component CSV timestamp column is fixed-width and sorts lexicographically,
  so `_tool_get_metric_histogram` / `_tool_group_metrics_by_field` /
  `_tool_get_correlated_timeline` build `[lo, hi)` boundary strings once per call
  (`_window_boundary_strings`: floor `lo`, ceil `hi` to the whole second so the
  gate is a conservative superset) and skip out-of-window rows before any parse.
  The exact `from_ms <= ms < to_ms` check still decides inclusion, so output is
  identical. The loop may `break` past `hi` only when `_layout_allows_break` is
  true — the dimensionless wide layout with no DST splice; the dim-aware
  per-instance-block layout and DST-injected runs are non-monotonic and must
  parse-gate without breaking. Sources:
  `src/anomaly_metric_creator/server_traces.py`;
  `src/anomaly_metric_creator/server_mcp.py`;
  `src/anomaly_metric_creator/server_ops.py`; `tests/test_server.py`;
  `tests/test_server_mcp.py`.

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
Sources: `README.md`;
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
`/v1/commands` trace echo. Sources: `README.md`;
`src/anomaly_metric_creator/server.py`;
`src/anomaly_metric_creator/server_ops.py`;
`src/anomaly_metric_creator/server_mcp.py`;
`tests/test_server_eval_mode.py`.

## HTTP, Kubernetes, and Helm API

The stdlib server exposes app endpoints (`/v1/state`, `/v1/commands`,
`/v1/debug/...`, `/v1/logs/stream`, time controls, and mutation reset) plus a
Kubernetes-compatible facade for real `kubectl` and Helm clients. Sources:
`README.md`; `src/anomaly_metric_creator/server.py`;
`src/anomaly_metric_creator/server_kubernetes.py`;
`src/anomaly_metric_creator/server_helm.py`; `tests/test_server.py`.

The Kubernetes facade must be backed by `resource_snapshot()` and
`SimulationMutations`, not a second resource model. It includes discovery,
Table responses, core resources, workloads, metrics, authorization reviews,
pod logs, and Helm-shaped release Secret storage for Helm 4 compatibility.
Sources: `README.md`;
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

The two entry points to the one simulated cluster — `/v1/commands` renderers
and the REST facade `kubernetes_api_mutating_response` — must agree on resource
existence. A mutation naming a resource absent from the overlay-aware
`resource_snapshot()` resolves against the snapshot *before* any overlay write
and refuses without mutating: the REST path returns a 404 `Status`, and the
command renderers (`_render_delete` for pods/deployments/generic,
`_render_scale`'s deployment branch, `_render_patch`'s deployment branch)
return a `NotFound` `CommandResult` (`_not_found`, nonzero exit). A nameless
`kubectl scale deployment` is a usage error (`kubectl.scale.usage`), never a
default to `apigateway`. The generic (non-deployment) patch branch keeps upsert
semantics on both paths. A refused mutation must leave the overlay
byte-identical (audit A-013). Sources:
`src/anomaly_metric_creator/server_ops.py`;
`tests/test_sim_mutation_correctness.py`; `tests/test_server_ops_fuzz.py`;
`tests/test_server.py`.

Helm compatibility uses simulator JSON inside double-base64 gzip
`helm.sh/release.v1` Secret payloads; do not document or treat these as native
Helm 3 protobuf release objects unless the encoder changes. Sources:
`README.md`; `src/anomaly_metric_creator/server_ops.py`;
`src/anomaly_metric_creator/server_helm.py`; `tests/test_server.py`.

### Bounded Kubernetes watch streams

Real-client watch (`GET …?watch=true` or `watch=1`) on a modeled *list* path
dispatches to `server._send_k8s_watch` before the one-shot list branch. The
watchable families are the `(group, version, resource)` set
`server_ops._WATCHABLE_LIST_RESOURCES` — v1 asserts only `("", "v1", "pods")`
and `("apps", "v1", "deployments")` — and the stream loop is generic over
`_k8s_objects_for_resource`, so the same overlay-aware object set the list path
returns is what the watch observes (`k8s_watch_objects` runs the identical
snapshot -> namespace-filter -> selector-filter chain). The wire shape is
newline-delimited JSON watch events (`{"type": "ADDED"|"MODIFIED"|"DELETED",
"object": …}`) under `content-type: application/json` with no content-length:
an `ADDED` replay of the current set, then a poll every
`server._WATCH_POLL_SECONDS` (default 2.0, monkeypatchable) diffing by object
identity (`uid`, else namespace/name) to emit change events. The stream is
bounded — it closes at `min(timeoutSeconds, server._WATCH_MAX_SECONDS)`
(default 300) or on the server shutdown event — and consumes one SSE slot for
its lifetime: over the SSE ceiling it refuses with a Kubernetes `Status` 503
(not the app JSON 503) before any stream headers, and it always releases the
slot in `finally`, mirroring `_with_sse_slot`. Exactly one `kubernetes-api`
`CommandTrace` is recorded per watch — supported with the emitted event count
on close, partial on a 503 refusal (`k8s_watch_trace_response`). Single-object
watch paths, non-`true`/`1` watch values, and unmodeled resources fall through
to the existing one-shot get/list/404 handling; there is no
`resourceVersion=` resume (kubectl re-lists on reconnect). The watch dispatch
sits behind the eval-mode wall like the list path (`/api`, `/apis` are
investigation-open).

Command mode cannot hold a stream: `kubectl get <kind> --watch`/`-w` over
`POST /v1/commands` renders the one-shot table exactly as `get`, appends one
stderr note pointing at real kubectl (`_WATCH_COMMAND_NOTE`), exits 0, and is
classified **partial** under rule `kubectl.get.<kind>.watch`
(`_render_get_watch`) so the ignored flag surfaces in the debug backlog. A
watch with no/unknown kind degrades to the normal unsupported path. Sources:
`README.md`; `src/anomaly_metric_creator/server.py`;
`src/anomaly_metric_creator/server_ops.py`; `tests/test_server_watch.py`;
`tests/test_server_ops_fuzz.py`.

## Trace Bundles

`GET /v1/debug/commands/export` and `POST /v1/debug/commands/import` move
portable command-trace JSON histories between live stores. `amc trace-bundle`
must consume the exported shape offline for summary, search, unsupported
grouping, and CSV export without starting the HTTP server. Sources:
`README.md`; `src/anomaly_metric_creator/server.py`;
`src/anomaly_metric_creator/server_traces.py`;
`src/anomaly_metric_creator/trace_bundle.py`; `tests/test_server.py`;
`tests/test_trace_bundle.py`.

Bundle import/read paths validate top-level shape, API/schema version, trace
entries, declared trace counts, and integer fields before coercion; booleans
are not accepted as integers. Sources:
`src/anomaly_metric_creator/trace_bundle.py`;
`src/anomaly_metric_creator/server_traces.py`; `tests/test_trace_bundle.py`;
`tests/test_server.py`.

`write_trace_bundle_csv` neutralizes spreadsheet formula triggers (a leading
`=`, `+`, `-`, `@`, tab, or CR) by apostrophe-prefixing the cell, applied to
**every** cell it writes rather than to a named subset of columns: recorded
traces are attacker-influenced, injection fires from any cell, and a per-column
allowlist rots the moment a column is added (A-018). Keep the guard at the
writer boundary, after any truncation or preview step, so the first byte written
is the guarded one; stored traces stay verbatim.

There are **two** such boundaries, not one. The debug UI builds its
unsupported-backlog CSV client-side, so its `csvCell` never reaches the Python
writer and carries the same guard independently. Two rules hold it:
neutralization runs **before** quoting, so the apostrophe lands inside the
quotes — quoting first emits `'"=a,b"`, whose first character is a quote and
whose formula still evaluates — and the two trigger sets are pinned to each
other by `tools/check_csv_formula_trigger_lockstep.py`, which anchors on the
`csv-formula-triggers:` marker comment above the guard. Adding a trigger to
one site alone is a lint failure, not a silent hole; moving the guard without
its marker is a structural (exit 2) failure rather than a vacuous pass.
Sources: `src/anomaly_metric_creator/trace_bundle.py`;
`src/anomaly_metric_creator/server_debug_ui.py`;
`tools/check_csv_formula_trigger_lockstep.py`; `SECURITY.md`;
`tests/test_trace_bundle.py`; `tests/test_debug_ui_javascript.py`;
`tests/test_csv_formula_trigger_lockstep_lint.py`.

Trace bundles are read by the tool version that wrote them — there is
deliberately no N-1 compatibility adapter, because the schema has never been
bumped (A-070). The PR that first bumps `COMMAND_TRACE_EXPORT_VERSION` owns the
decision of whether to add one, and updates the comment beside the check, the
mismatch error message, and the README trace-bundle section together. Sources:
`src/anomaly_metric_creator/trace_bundle.py`; `README.md`;
`tests/test_trace_bundle.py`.

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
