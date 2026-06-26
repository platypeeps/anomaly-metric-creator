# anomaly-metric-creator

[![CI](https://github.com/platypeeps/anomaly-metric-creator/actions/workflows/ci.yml/badge.svg)](https://github.com/platypeeps/anomaly-metric-creator/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
![License: Proprietary](https://img.shields.io/badge/license-proprietary-red.svg)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![uv](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json)](https://github.com/astral-sh/uv)
[![pre-commit](https://img.shields.io/badge/pre--commit-enabled-brightgreen?logo=pre-commit&logoColor=white)](https://github.com/pre-commit/pre-commit)

`anomaly-metric-creator.py` generates synthetic IoT-style metric logs for a SaaS stack
with built-in anomalies. By default (`--emit metrics,logs,traces`) it
writes one CSV per component plus an `anomalies.csv` manifest that catalogues each
injected anomaly with at least one surviving row after the `--drop-rate`
packet-loss mask; shaped spans anchor at their first kept row. Runs that omit
`metrics` (e.g. `--emit logs,traces`) skip the per-component CSVs and delete
`anomalies.csv` from `--output-dir`. See [Output files](#output-files) for the
exact `--emit` and packet-loss gating. Output is deterministic for a given
`--seed`.

By default the script spans **50,000 one-minute slots** over about 34.72 days for
fourteen components: `authservice`, `cacheservice`, `apigateway`, `database`,
`mqservice`, `llm_analytics`, `loadbalancer`, `objectstore`, `vectorstore`,
`scheduler`, `paymentservice`, `identityprovider`, `observabilitypipeline`,
`gpu_inference`. The default packet-loss rate is `0`, so the default run emits
the full 50,000-row shape; pass a non-zero `--drop-rate` to simulate missing
samples. Duration, sampling interval, drop rate, and output directory are all
CLI-configurable.

## Significant changes

Recent significant additions to the generator:

- **GPU inference serving layer** — adds `gpu_inference.csv` with
  serving-layer fields matching the reference observability telemetry shape:
  `batch_size`, `model_size_b`, GPU/KV memory pressure, fragmentation,
  utilization, throughput, p50/p99 latency, and `failure`. The
  `gpu_inference_fragmentation` scenario models a reference-like sparse
  failure field: 1,204 labeled failure rows in the default 50,000-row
  shape, mostly singletons, with a detector-visible degradation core that
  concentrates sparse failures while fragmentation, memory pressure, KV cache
  occupancy, utilization, throughput, and tail latency cross bad thresholds
  together.
- **Flag-day default flip + integer-cast bundle** (phase 6) —
  `--topology-mode realistic` became the default, and at the phase-9
  flag day the `--topology-mode independent` contrast alias was removed
  entirely (the flag no longer parses; realistic is the only mode).
  Every `MetricSpec` column declared `dtype="int"` is cast via `np.rint`
  in `generate_component()` before derivations run, clearing all
  fractional-integer validator violations (the `validate` subcommand) on
  the 1-day compatibility output. All locked SHA-256 hashes in `tests/`
  target realistic output; the pure-natural statistical baseline used by
  the test suite is generated directly via `generate_component` (see
  `tests/conftest.py`).
- **Topology graph v1** (realistic topology coupling, always on) —
  declares a directed service-call graph (`TOPOLOGY`)
  and wires it into generation. Phase 2 couples downstream RPS baselines from
  upstream load columns; phase 3 extends coupling to all front-half fan-out
  edges; phase 4 adds logistic-shaped latency multiplier and error-rate
  offset when an upstream saturates; phase 5 closes the graph by coupling
  `apigateway → llm_analytics` (token-throttle reads as load-driven
  saturation). See [docs/topology.md](docs/topology.md) and the
  [Topology graph (v1)](#topology-graph-v1) section.
- **Schema document + output validator** (`--emit schema` /
  the `validate` subcommand) — `schema.json` captures run-level
  parameters and per-metric metadata; `validate DIR` checks required files,
  row counts, timestamps, cell ranges and dtypes, and derived-metric consistency.
- **Gauges file** (`--emit gauges`) — long-form
  `gauges.csv` with one `(timestamp, component, metric, value)` row per data
  point, chronologically merged across components.
- **Output directory hygiene** — `_pre_clean_output_dir` removes
  stale artifacts from prior runs (dropped components, deselected emit types)
  before generation starts.
- **Scenario registry refactor + RNG instance** — all anomaly
  scenarios live in the `SCENARIOS` dict; per-run state moves into `RunContext`
  with an explicit `np.random.RandomState` so seed behavior is deterministic
  regardless of import order.

## Install

Requires Python 3.11+.

```bash
# Runtime install (uses the dependencies declared in pyproject.toml):
python3 -m pip install -e .

# Optional: enable YAML --instance-config files (JSON works without it):
python3 -m pip install -e '.[yaml]'

# Editable install with dev extras (pytest, xdist, ruff, pre-commit, etc.):
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
```

Editable installs expose both `amc` and `anomaly-metric-creator` console
scripts. The examples below use `amc`; from a source checkout without installing,
substitute `python3 anomaly-metric-creator.py` for `amc` in any command — both
drive the same CLI.

The package metadata is marked private (`Private :: Do Not Upload`) to match
the proprietary license badge; install it from this repository or an internal
package index, not the public Python package index.

## Usage

```bash
# Default: 50,000 rows per component at a 60s cadence
amc

# Full week (10,080 rows per component at the default 60s cadence). Each multi-day scenario activates at
# its own `days_required` (e.g. llm_viral_surge_day2 at 2 days, cache_leak_restart
# at 2 days, jwks_rotation_chaos at 3 days, llm_second_viral at 7 days).
# The default 50,000-row window also includes the longer GPU inference pattern.
amc --duration-days 7

# Finer sampling: one row every 5 seconds (17,280 rows per component for 1 day).
amc --duration-days 1 --interval-seconds 5

# Generate logs and produce the unified joined CSV in one shot:
amc --emit metrics,logs,traces,combined

# Skip generation; only build the unified CSV from an existing output dir:
amc combine iot_logs

# Validate an existing output dir against its schema.json:
amc validate iot_logs

# Inspect an exported command trace bundle without starting the server:
amc trace-bundle summary command-traces.json
amc trace-bundle search command-traces.json --status unsupported
amc trace-bundle export-csv command-traces.json --output command-traces.csv

# Run as an incident simulator server with a debug UI and Kubernetes/Helm
# command API. Unrecognized serve options are parsed as normal generate flags:
amc serve \
  --port 8088 \
  --duration-days 2 \
  --scenarios db_disk_exhaustion \
  --otel-send none

# Emit only a subset of artifact types:
amc --emit metrics,logs
amc --emit traces

# Emit only a subset of components (CSVs, anomalies.csv, reporting artifacts,
# and OTEL streaming are all filtered to just these components):
amc --components authservice,database

# Pick the signal intensity level: low (only benign baseline shifts),
# medium (default — today's full catalog), or high (additionally activates
# the high-pressure cross-component scenarios):
amc --signal-level high

# Run only the coordinated cache+DB meltdown scenario at high signal level:
amc --signal-level high --scenarios cache_db_meltdown

# Run all scenarios except LLM-related ones and the Monday baseline:
amc --exclude-scenarios llm_viral_surge_day2,llm_enterprise_onboarding,llm_rate_limit_fallout,llm_weekend_batch,llm_second_viral,llm_provider_outage,monday_baseline

# Run the full default catalog at 7 days but drop the JWKS rotation scenario
# (handy when you want every other multi-day scenario without re-typing the
# full slug list):
amc --duration-days 7 --exclude-scenarios jwks_rotation_chaos

# Selector composition order: --scenarios (allowlist) → --exclude-scenarios
# (denylist, wins on overlap) → --signal-level (severity gate) →
# --duration-days (duration gate) → --components (component allowlist).
# Slugs dropped by the severity or duration gate emit a stderr WARNING line;
# slugs disjoint from --components are dropped silently.

# Cap the total anomaly count across the whole dataset (deterministic for a
# given --seed). Useful for keeping noisy test datasets small or sweeping
# across anomaly density:
amc --anomaly-count 25

# Cap the metric columns emitted per component (1..10). Each component emits
# the first N of its priority-ordered catalog. Omit the flag to keep the
# historic default per-component count:
amc --metrics-per-component 3
amc --metrics-per-component 10

# Stream anomaly events as OTLP signals while generating locally:
# OTEL streaming is OFF by default; pass --otel-send to opt in.
# --otel-endpoint derives the per-signal URLs (BASE/v1/logs, /v1/metrics,
# /v1/traces) for the selected signals.
amc \
  --otel-send logs,metrics,traces \
  --otel-endpoint http://localhost:4318 \
  --otel-stream-speedup 3600

# Stream with signal-specific env controls (still requires --otel-send):
MEZMO_OTEL_LOGS_ENDPOINT=http://localhost:4318/v1/logs \
MEZMO_OTEL_LOGS_AUTH_TOKEN=secret \
amc --otel-send logs

# Additionally stream per-row metric values as OTLP Gauge data points
# (alongside the anomaly-counter/log/trace signal stream):
amc \
  --otel-send logs,metrics,traces,gauges \
  --otel-endpoint http://localhost:4318 \
  --otel-gauge-batch-seconds 60 \
  --otel-gauge-metric-prefix amc.

# Stream only per-row Gauge data points, skipping anomaly-counter/log/trace
# OTEL signals:
amc \
  --otel-send gauges \
  --otel-endpoint http://localhost:4318 \
  --otel-stream-protocol json
```

### CLI flags

The CLI is organized around five subcommands plus grouped flags. `generate`
is the default — a bare invocation with no subcommand token runs the
generation pipeline exactly as before:

- `generate` — the default generation pipeline (implied when no subcommand
  token is given).
- `combine DIR [--components ...]` — skip generation; rebuild
  `combined_metrics_unified.csv` from the per-component CSVs already in `DIR`.
  Respects `--components` when set; otherwise autodiscovers component CSVs in
  `DIR`.
- `validate DIR [--warn]` — standalone validator: load `DIR/schema.json` and
  check the artifacts in `DIR` against it (file presence, row counts,
  timestamp coverage, declared `min_value`/`max_value`/`dtype` bounds,
  `counter`/`rate` non-negativity, derived-column consistency, and
  `anomalies.csv` sort order). Reports every violation found and exits `1`
  if there are any, unless `--warn` is passed, which reports them on stderr
  and exits `0`.
  See [Output validation (the `validate` subcommand)](#output-validation-the-validate-subcommand).
- `serve [server flags] [generate flags...]` — generate (unless
  `--no-generate` is passed), start a stdlib HTTP server, stream OTEL in a
  background thread when `--otel-send` is enabled, and answer simulated
  `kubectl` / `helm` commands from the active scenario state. Open
  `/debug` for the live command trace, unsupported-command explorer,
  synthetic resource view, and scenario profile state.
- `trace-bundle {summary,search,unsupported,export-csv} BUNDLE` — inspect a
  JSON bundle from `GET /v1/debug/commands/export` offline. `summary` prints
  support-status, command-family, scenario, and unsupported-fingerprint counts;
  `search` applies the same `--q`, `--status`, `--family`, `--scenario`,
  `--limit`, and `--offset` filters as `/v1/debug/search`; `unsupported`
  groups partial/unsupported traces by fingerprint; and `export-csv` writes a
  flattened trace table for spreadsheets or workshop notes. The reporting
  commands accept `--format json` for automation.

Help is two-tier: `-h` shows the common surface in the five groups below;
`--help-all` additionally lists the advanced knobs
(see [Advanced flags](#advanced-flags)).

#### Common

| Flag | Default | Notes |
| ---- | ------- | ----- |
| `--duration-days`   | `34.72222222222222` | Days to generate. The default combines with `--interval-seconds 60` to produce exactly 50,000 rows per component, matching the reference observability telemetry CSV shape. Each multi-day scenario has its own `days_required` (the day index of its earliest in-range offset, e.g. `llm_viral_surge_day2` at 2 and `jwks_rotation_chaos` at 3); see the [scenario catalog](#scenario-catalog) for per-scenario values. |
| `--start-time`      | `2026-03-10T00:00:00` | UTC whole-second timestamp for the first generated row. Accepts ISO 8601 values such as `2026-06-24T12:34:56Z` or `2026-06-24 12:34:56`; timezone-aware inputs are normalized to UTC before writing timezone-less CSV timestamps and `schema.json` metadata. Sub-second start times are rejected because not every artifact can represent them exactly. |
| `--seed`            | `42`        | RNG seed for deterministic output.                                 |
| `--output-dir`      | `iot_logs`  | Directory CSVs are written into (created if missing).              |

#### Anomaly selection

| Flag | Default | Notes |
| ---- | ------- | ----- |
| `--scenarios`       | `all`       | Comma-separated allowlist of named scenario slugs (case-insensitive). Use `all` (default) to include every scenario in the `SCENARIOS` registry that passes the severity and duration gates. The `all` sentinel is mutually exclusive with explicit slugs (`all,foo` is rejected). Scenarios outside the active `--signal-level` severity hierarchy or whose `days_required` exceeds `--duration-days` are dropped with a stderr `WARNING: scenario <slug> requires …` message; scenarios whose `components_touched` is disjoint from `--components` are dropped silently. See the [scenario catalog](#scenario-catalog) for all known slugs and the composition order. |
| `--exclude-scenarios` | _empty_   | Comma-separated denylist of scenario slugs to subtract from the resolved set (applied after `--scenarios`, before the severity/duration/components gates). Case-insensitive. Useful for `--exclude-scenarios jwks_rotation_chaos` to get every scenario except one; on overlap with `--scenarios`, exclusion wins. |
| `--signal-level`    | `medium`    | Anomaly intensity level: `low`, `medium` (default), or `high`. Inclusion hierarchy: `low` only fires specs explicitly tagged `severity="low"` (today: a handful of benign Monday-morning baseline shifts) and intentionally has **no cascade fan-out** because benign baseline shifts do not realistically propagate as failures; `medium` adds the standard catalog plus its cascade fan-out (the default behavior); `high` additionally activates the high-pressure cross-component scenarios (regional failover storm, coordinated cache+DB meltdown, LLM provider outage, gateway DDoS saturation, storage layer pressure) and their cascades. |

#### Dataset shape

| Flag | Default | Notes |
| ---- | ------- | ----- |
| `--drop-rate`       | `0.0`       | Per-row probability of dropping the row entirely from the per-component CSV (no row is emitted for that timestamp). Default `0` preserves the full reference-shaped 50,000-row output; use a non-zero value to simulate packet loss. |
| `--interval-seconds`| `60.0`      | Seconds between consecutive rows. Sampling-density knob — timeline coverage stays `duration_days * 86400`s and row count is `floor(total_seconds / interval)`. Must be `>= 0.001` (millisecond precision floor). Anomalies map to the nearest row via `round(time_offset / interval)`. Values ≥ 1.0 emit second-precision timestamps (`YYYY-MM-DD HH:MM:SS`); values < 1.0 emit millisecond-precision timestamps (`YYYY-MM-DD HH:MM:SS.SSS`) so adjacent sub-second rows remain unique. Combinations of this flag with `--duration-days`, `--metrics-per-component`, and `--components` are validated against a preflight cell-count cap (200M cells total); see `--allow-huge-output`. |
| `--components`      | `all`       | Comma-separated component allowlist. Filters CSV emission, `anomalies.csv`, reporting artifacts, and OTEL streaming to only the named components. Use `all` (default) for every component. Allowed names: `apigateway`, `authservice`, `cacheservice`, `database`, `gpu_inference`, `identityprovider`, `llm_analytics`, `loadbalancer`, `mqservice`, `objectstore`, `observabilitypipeline`, `paymentservice`, `scheduler`, `vectorstore`. |
| `--metrics-per-component` | _historic default per component_ | Optional cap on the metric columns emitted per component (must be in `[1, 10]`). Omit the flag to keep today's per-component count (4–10 metrics depending on component). When provided, every component emits the first `N` entries from its priority-ordered metric catalog (highest-value metrics first). Anomalies whose target metric is trimmed by the cap are filtered out before generation. |
| `--instances-per-component` | `1` | Fan each component out to N identical instances (must be in `[1, 20]`). `N=1` (the default) emits today's byte-identical output with no dimension columns. `N>1` prepends `id,host,pod,az,region,tenant` columns to every per-component CSV header and writes N row blocks per component — one block per instance, in the stable `i0`/`pod-0`, `i1`/`pod-1`, … order. `host`/`az`/`region`/`tenant` are empty in v1 (use `--instance-config` to fill them in via a declarative file). All instances share the same RNG-drawn natural values and the same anomaly overrides in v1 unless a scenario spec narrows the targets via `instance_filter` (Phase 4). `anomalies.csv` records one row per `(timestamp, component, metric)` regardless of N. The long-form file writers (`combined_metrics_unified.csv`, `gauges.csv`) became dimension-aware in Phase 5; the OTEL streaming path (`--otel-send`) became dimension-aware in Phase 6; and the schema/validator (`--emit schema`, the `validate` subcommand) became dimension-aware in Phase 8 (per-component `dimensions` blocks in `schema.json` plus long-form header checks). The only remaining gate is the intentional `--inject-dst-artifact-day > 0` boundary: the DST splice produces non-monotonic timestamps the multi-instance row builder is not prepared to resolve. Mutually exclusive with `--instance-config`. Multiplies the preflight cell-count cap inputs linearly with N. |
| `--instance-config` | _off_ | Path to a YAML (`.yaml`/`.yml`) or JSON (`.json`) file declaring a per-component instance topology for repeatable non-uniform fan-outs (Phase 3). The top-level key `components` maps component names to lists of `Instance` field dicts (`id, host, pod, az, region, tenant`). Components not listed in the file fall back to the module-level `INSTANCES` registry (single anonymous `Instance()` per component) so the default per-component CSV shape is preserved. `parse_args` checks the flag-shape invariants (path resolves to a regular file via `Path.is_file()`, suffix in `{.yaml, .yml, .json}`, mutex with `--instances-per-component`); schema validation runs in `main()` via `_load_instance_config`, which raises a `ValueError` (caught and re-raised as `sys.exit`) on malformed YAML/JSON, unknown component, unknown `Instance` field, non-mapping top-level value, non-mapping `components` value, per-component value not a list, empty per-component list, non-dict instance entry, duplicate `id`, and per-component count exceeding `MAX_INSTANCES_PER_COMPONENT=20`. YAML support requires PyYAML — install with `pip install 'anomaly-metric-creator[yaml]'` or `pip install pyyaml`; JSON works without it. Triggers the same multi-instance code path as `--instances-per-component > 1`, so it inherits the same downstream-flag state: the long-form file writers (Phase 5), OTEL streaming (Phase 6), and schema/validator (Phase 8) are all dimension-aware, so dimensioned `--instance-config` runs work with `--emit combined`/`gauges`/`schema`, the `combine` and `validate` subcommands, and `--otel-send` without further configuration; only the intentional `--inject-dst-artifact-day > 0` boundary remains rejected. Preflight cell-count cap uses `MAX_INSTANCES_PER_COMPONENT=20` as a conservative upper bound since the per-component instance count is not known until `main()` parses the config file. |

#### Artifacts

| Flag | Default | Notes |
| ---- | ------- | ----- |
| `--emit` | `metrics,logs,traces` | Comma-separated artifact selection. Valid tokens are `metrics`, `logs`, `traces`, `gauges`, `schema`, `combined`; combinations are allowed subject to the dependency rules below. `metrics` writes the per-component CSVs and `anomalies.csv`, `logs` writes `metric_report.log`, `traces` writes `metric_traces.jsonl`, `gauges` (opt-in) writes the long-form [`gauges.csv`](#gauge-metric-file-gaugescsv), `schema` (opt-in) writes a declarative [`schema.json`](#output-schema-document-schemajson), and `combined` (opt-in) joins the selected per-component CSVs generated by this run into `combined_metrics_unified.csv`. The `gauges` and `combined` tokens require `metrics`; `schema` has no other requirements. |

#### OTEL streaming

| Flag | Default | Notes |
| ---- | ------- | ----- |
| `--otel-send` | _none_ | Comma-separated OTLP signals to stream: any subset of `logs`, `metrics`, `traces`, `gauges`, or `all`, or `none` (explicit off, overriding env defaults). Streaming is off by default, and the selection is authoritative — unselected signals do not stream even when env-var endpoints are configured. `logs` replays anomaly events as `resourceLogs`, `metrics` replays them as `anomaly.count` Sum data points, `traces` replays them as span events, and `gauges` streams every per-row metric value from the per-component CSVs as OTLP Gauge data points to the metrics endpoint (see [Gauge metric streaming](#gauge-metric-streaming---otel-send-gauges)). `--otel-send gauges` alone streams only the Gauge data points, skipping the anomaly log/metric/trace stream — useful for receivers that only accept OTLP Gauge payloads. The `gauges` signal requires `metrics` in `--emit`. Selecting a signal without a configured endpoint is a usage error. |
| `--otel-endpoint` | _unset_ | Base OTLP/HTTP URL for every signal selected by `--otel-send`. Per-signal URLs are derived from it (`BASE/v1/logs`, `BASE/v1/metrics`, `BASE/v1/traces`; the gauge stream shares the metrics URL). The derivation wins over the `MEZMO_OTEL_LOGS_ENDPOINT` / `MEZMO_OTEL_METRICS_ENDPOINT` / `MEZMO_OTEL_TRACES_ENDPOINT` env vars (an explicitly typed base is never silently hijacked by a stale shell export); the env vars supply the per-signal defaults when no base is given. |
| `--otel-auth-token` | _unset_ | Auth token applied to every signal selected by `--otel-send`. Same precedence as `--otel-endpoint`: this token beats the `MEZMO_OTEL_LOGS_AUTH_TOKEN` / `MEZMO_OTEL_METRICS_AUTH_TOKEN` / `MEZMO_OTEL_TRACES_AUTH_TOKEN` env vars (which supply the defaults when this flag is not given). |
| `--otel-stream-speedup` | `3600.0` | Replay speed multiplier for OTEL streaming. `1.0` is real-time, `3600.0` replays one hour of anomaly spacing per second. |
| `--otel-stream-protocol` | `MEZMO_OTEL_STREAM_PROTOCOL` or `protobuf` | OTLP payload mode: `json` (`application/json`) or `protobuf` (`application/x-protobuf`). |

#### Server mode (`serve`)

`serve` accepts its own HTTP/debug flags and forwards every unrecognized flag
through the normal generation parser, so the scenario, component, instance,
artifact, and OTEL knobs above all work unchanged:

```bash
amc serve \
  --host 127.0.0.1 \
  --port 8088 \
  --namespace saas-prod \
  --duration-days 3 \
  --scenarios cache_leak_restart \
  --components apigateway,cacheservice,database,mqservice
```

Longer serve invocations can move stable defaults into JSON or YAML:

```json
{
  "server": {
    "host": "127.0.0.1",
    "port": 8088,
    "namespace": "saas-prod",
    "auth_token": "replace-me",
    "structured_log_file": "server-requests.jsonl"
  },
  "generate": {
    "duration_days": 3,
    "scenarios": "cache_leak_restart",
    "components": "apigateway,cacheservice,database,mqservice",
    "otel_send": "none"
  }
}
```

```bash
amc serve --config serve-config.json --port 8090
```

Config keys use the long flag names with underscores instead of hyphens. Values
from `server` are parsed as serve-mode flags; values from `generate` are
forwarded through the normal generation parser. Explicit CLI flags come after
config defaults, so `--port 8090` in the example overrides the file. JSON works
without optional dependencies; YAML requires PyYAML, matching `--instance-config`.

Server flags:

| Flag | Default | Notes |
| ---- | ------- | ----- |
| `--host` | `127.0.0.1` | HTTP bind host. |
| `--port` | `8088` | HTTP bind port; use `0` for an ephemeral test port. |
| `--namespace` | `saas-prod` | Namespace rendered by simulated Kubernetes/Helm responses. |
| `--debug-ring-size` | `500` | In-memory command trace ring size. |
| `--persist-command-log` | _off_ | Optional JSONL file for command traces. |
| `--persist-command-db` | _off_ | Optional SQLite file for durable command traces and search. |
| `--persist-command-retention` | `0` | Maximum SQLite command traces to retain; `0` keeps all persisted traces. |
| `--config` | _off_ | Optional JSON/YAML file containing `server` and `generate` defaults for `amc serve`. Explicit CLI flags override config values. |
| `--auth-token` | _off_ | Optional bearer token required for HTTP API, debug data, command, and Kubernetes API requests. Embedded into `GET /v1/kubeconfig` when enabled. |
| `--max-request-body-bytes` | `1048576` | Maximum accepted HTTP request body size. Oversized app requests return `413`; oversized Kubernetes API requests return a Kubernetes `Status`. |
| `--allow-remote-without-auth` | _off_ | Explicit lab-only override that permits non-loopback `--host` values without `--auth-token`. |
| `--cors-allow-origin` | _off_ | Optional exact `Access-Control-Allow-Origin` value for browser clients, or `*` for any origin. Preflight requests are answered without bearer auth. |
| `--rate-limit-per-minute` | `0` | Optional per-client command and Kubernetes API request limit; `0` disables rate limiting. Limited app requests return JSON `429`; limited Kubernetes API requests return a Kubernetes `Status` with `reason: TooManyRequests`. |
| `--structured-log` / `--no-structured-log` | _off_ | Emit one JSON request record per HTTP request plus error records for request-handling exceptions. Defaults to stderr unless `--structured-log-file` is set. |
| `--structured-log-file` | _off_ | Optional JSONL path for structured request/error logs. Setting a path enables structured logging. |
| `--no-generate` | _off_ | Use existing artifacts in `--output-dir` instead of generating before serving. |
| `--continuous-generate` | _off_ | Keep regenerating artifacts while the server runs. Each pass refreshes `/v1/state`, Kubernetes/Helm snapshots, anomaly rows, and log-stream inputs. When OTEL streaming is enabled, the continuous generator serializes regeneration and OTEL replay so each fresh batch is streamed in order. |
| `--continuous-generate-interval-seconds` | `60.0` | Seconds to wait between continuous generation passes. |

By default the server binds loopback. Binding a non-loopback host such as
`0.0.0.0` requires `--auth-token` unless
`--allow-remote-without-auth` is passed explicitly. Health probes
(`/healthz` and `/readyz`) and the static debug console shell (`/debug` and
`/`) remain unauthenticated; every JSON/debug data endpoint and the Kubernetes
facade require `Authorization: Bearer TOKEN` when a token is configured. The
debug console prompts for that bearer token and stores it in browser
`localStorage`; `/debug?token=TOKEN` can also bootstrap the browser session.
Command/API traces redact bearer tokens, token-like query values, passwords,
secrets, and client-key shaped values before they are stored in memory, JSONL,
or SQLite.
Structured request logs follow the same query redaction and never record bearer
token values: they include `timestamp`, `event`, `method`, `path`, redacted
`query`, `status`, `client`, `user_agent`, `authorization` (`present` or
`absent`), `duration_ms`, and `response_bytes`; error rows also include
`error_type` and `message`.

For remote lab access, put TLS and host allowlisting in a reverse proxy and
keep the simulator bound to loopback:

```nginx
server {
  listen 443 ssl;
  server_name amc.example.internal;

  ssl_certificate /etc/letsencrypt/live/amc/fullchain.pem;
  ssl_certificate_key /etc/letsencrypt/live/amc/privkey.pem;

  location / {
    proxy_pass http://127.0.0.1:8088;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto https;
  }
}
```

Run the backend with an auth token, an explicit CORS origin if the browser UI is
loaded from a different host, and a command/API limit appropriate for the
workshop:

```bash
amc serve --auth-token "$AMC_TOKEN" \
  --cors-allow-origin https://amc.example.internal \
  --rate-limit-per-minute 120
```

Primary endpoints:

| Endpoint | Purpose |
| -------- | ------- |
| `GET /debug` | Browser debug console shell. Data requests still use bearer auth when configured. |
| `GET /v1/kubeconfig` | Kubeconfig that points stock `kubectl` and `helm` clients at this simulator. |
| `POST /v1/commands` | Execute a simulated command. Body accepts `{"command": "kubectl get pods -n saas-prod"}` or `{"argv": [...]}`. |
| `GET /version`, `/api`, `/apis/...` | Kubernetes-compatible discovery, resource, log, metrics, and Helm release Secret APIs for real clients. |
| `GET /v1/state` | Current synthetic clock, active scenarios, OTEL/generation status, mutable overlay summary, active anomaly spans, and trace counts. |
| `GET /v1/scenarios` | Scenario catalog with primary/cascade signal descriptions and Kubernetes/Helm ops-profile details. |
| `GET /v1/debug/commands` | Recent command traces. |
| `GET /v1/debug/commands/export` | Export command traces as portable JSON for offline debugging or import into another SQLite trace store. |
| `POST /v1/debug/commands/import` | Replace the current command trace history from a portable JSON export. |
| `GET /v1/debug/search` | Search command traces by `q`, `status`, `family`, `scenario`, `limit`, and `offset`; uses SQLite plus FTS5 when available and falls back to LIKE search otherwise. |
| `GET /v1/debug/unsupported` | Unsupported / partial command fingerprints grouped by count, examples, and guessed intent. |
| `GET /v1/debug/resources` | Synthetic pods, deployments, services, HPA, PVC, nodes, events, ingress, and Helm release state. |
| `GET /v1/logs/stream` | SSE replay of `metric_report.log` lines for the generated run; connected clients receive refreshed log batches after continuous generation passes. |
| `POST /v1/time/pause`, `/resume`, `/seek` | Simulation clock controls (`seek` expects `{"timestamp": "YYYY-MM-DD HH:MM:SS"}`). |
| `POST /v1/mutations/reset` | Clear mutable simulator overlays and return to the baseline scenario state without restarting the server. |

Supported command families in server mode:

- `kubectl version|api-versions|api-resources|cluster-info`
- `kubectl config current-context|view`
- `kubectl auth can-i`
- `kubectl get all|namespaces|pods|configmaps|secrets|deployments|replicasets|daemonsets|services|endpoints|endpointslices|events|hpa|jobs|cronjobs|serviceaccounts|nodes|pvc|statefulsets|ingress`
- `kubectl describe` for the same synthetic resources where a description is useful
- `kubectl logs POD` and selector-based logs with `--follow`, `--prefix`,
  `--previous`, `-c/--container`, `--tail`, and `--since-time`; `-f` is
  supported for pod-targeted logs
- `kubectl top pods|nodes`
- `kubectl rollout status|history|restart deployment/NAME`
- `kubectl scale`, `delete`, `apply`, and `create` with scenario-aware mutable simulator state
- `kubectl wait`, `exec`, and `port-forward` with simulated diagnostic responses
- `helm version|env|list|status|history|test|template`
- `helm get values|manifest|notes|all|hooks`
- `helm install|upgrade|rollback|uninstall` with mutable release-history and values state

Every anomaly scenario in the generator has a Kubernetes/Helm ops profile.
Those profiles drive pod/deployment health, events, logs, rollout notes,
`helm status`, `helm history`, `helm get notes`, and the Helm release Secret
payloads exposed through the Helm-shaped Secret API.

Real `kubectl` and Helm 4 client compatibility is also available through the
Kubernetes API facade:

```bash
curl -s http://127.0.0.1:8088/v1/kubeconfig > /tmp/amc.kubeconfig

KUBECONFIG=/tmp/amc.kubeconfig kubectl get pods -n saas-prod
KUBECONFIG=/tmp/amc.kubeconfig kubectl get all -n saas-prod
KUBECONFIG=/tmp/amc.kubeconfig kubectl api-resources
KUBECONFIG=/tmp/amc.kubeconfig kubectl logs cacheservice-0 -n saas-prod
KUBECONFIG=/tmp/amc.kubeconfig kubectl logs -l app.kubernetes.io/name=cacheservice --prefix -n saas-prod
KUBECONFIG=/tmp/amc.kubeconfig kubectl top pods -n saas-prod
KUBECONFIG=/tmp/amc.kubeconfig kubectl auth can-i get pods -n saas-prod

KUBECONFIG=/tmp/amc.kubeconfig helm list -n saas-prod
KUBECONFIG=/tmp/amc.kubeconfig helm status simulated-saas -n saas-prod
KUBECONFIG=/tmp/amc.kubeconfig helm history simulated-saas -n saas-prod
```

With `--auth-token`, fetch the kubeconfig with the same bearer token; the
generated user entry includes that token so subsequent `kubectl` and `helm`
requests authenticate automatically:

```bash
curl -H 'Authorization: Bearer dev-token' \
  -s http://127.0.0.1:8088/v1/kubeconfig > /tmp/amc.kubeconfig
```

The compatibility facade implements enough Kubernetes discovery for normal
client negotiation, server-side Table responses for familiar `kubectl get`
output, core resources (`pods`, `services`, `endpoints`, `events`, `pvc`,
`configmaps`, `secrets`, `serviceaccounts`, `nodes`), `apps/v1` workloads
(`deployments`, `replicasets`, `daemonsets`, `statefulsets`), `batch/v1`
jobs/cronjobs, `discovery.k8s.io/v1` endpoint slices, `autoscaling/v2` HPA,
`networking.k8s.io/v1` ingress, `metrics.k8s.io/v1beta1` pod/node metrics,
and `authorization.k8s.io/v1` self-subject access reviews. Helm compatibility
uses Helm-shaped Secret storage objects (`helm.sh/release.v1` Secrets named
`sh.helm.release.v1.simulated-saas.vN`) with a double-base64 gzip JSON release
payload, so Helm 4 list/status/history/get commands and the simulator debug
tools can decode scenario-appropriate release state through the same fake API
server. The payload is intentionally simulator JSON rather than Helm 3's native
protobuf release object, which keeps the generated state inspectable while
preserving the real-client API paths the simulator needs to observe.
Every real-client API call is recorded as command family `kubernetes-api`, so
unsupported client paths appear in the debug search/backlog just like custom
command API calls. Common mutating Kubernetes operations are applied to an
in-memory overlay: `PATCH`/`PUT` deployments and the `deployments/scale`
subresource update replica/ready counts, `DELETE` pods removes the named pod
from the snapshot, `DELETE` deployments hides that workload, and generic create,
patch, update, and delete calls for ConfigMaps, Secrets, Services,
ServiceAccounts, Jobs, CronJobs, HPA, PVCs, Ingresses, StatefulSets, and
DaemonSets appear in subsequent command/API snapshots. Unsupported mutation paths
still return Kubernetes `Status` responses and are traced for backlog analysis.

The mutable overlay is intentionally separate from the scenario catalog and
base generated artifacts. Scenario profiles still define the baseline
incident-shaped Kubernetes and Helm state; operator commands and real-client
API calls layer changes on top, and `/v1/state` exposes the current mutation
version, deleted pods/resources, created resources, release values, release
overlay, and continuous generation counters. The debug UI Reset button and
`POST /v1/mutations/reset` clear this overlay. With `--continuous-generate`, the
server reruns the generator at the configured interval using incremented seeds,
reloads `anomalies.csv`, refreshes the log and metric artifacts on disk, replays
OTEL from each refreshed batch when `--otel-send` is active, and emits refreshed
`metric_report.log` batches to already-connected `/v1/logs/stream` clients.

Unsupported requests are intentionally captured rather than discarded. The
debug UI groups them by normalized fingerprint, keeps raw examples and parsed
flags, reports generation/OTEL/mutation status, lists workload/release overlays,
and includes a scenario catalog with detailed primary, cascade, event, log, and
Kubernetes-impact descriptions. It also includes global filters, command and
unsupported-backlog exports, compact runtime charts, a combined timeline,
baseline-vs-overlay resource diffs, copyable pytest snippets for unsupported
fingerprints, and a resource drawer that fetches the same Kubernetes object
payload real clients see where a fake API path is available. Add
`--persist-command-db PATH` when you want that trace history to survive restarts
and power the debug console search over raw commands, outputs, fingerprints,
statuses, families, and active scenarios.
Use `--persist-command-retention N` to bound the durable SQLite history, and
use the command export/import endpoints to move trace histories between runs
for offline debugging.
For offline analysis outside a running server, save the export payload and use
one of:

- `amc trace-bundle summary BUNDLE.json`
- `amc trace-bundle search BUNDLE.json --status unsupported`
- `amc trace-bundle unsupported BUNDLE.json`
- `amc trace-bundle export-csv BUNDLE.json --output traces.csv`

#### Advanced flags

`--help-all` lists everything `-h` hides. The advanced knobs are
`--anomaly-count`, `--allow-huge-output`,
`--inject-dst-artifact-day`, and the OTEL transport tuning
flags `--otel-gauge-batch-seconds`, `--otel-gauge-metric-prefix`,
`--otel-stream-timeout-seconds`, `--otel-stream-max-events`,
`--otel-stream-auth-scheme`, `--otel-activity-log`, and `--otel-verbose`.

The 16 deprecated alias flags from the CLI consolidation
(`--emit-selection`, `--combine`, `--combine-only`, `--validate-output`,
`--validate-warn`, the five OTEL toggles, and the six per-signal
endpoint/token flags) were removed at the post-phase-9 CLI flag day and
no longer parse. Their canonical replacements are the surface documented
above: `--emit` (with the `combined` token), the `combine` and `validate`
subcommands, `--otel-send`, `--otel-endpoint`, and `--otel-auth-token`;
per-signal endpoint/token overrides remain available via the
`MEZMO_OTEL_*` env vars.

### Gauge metric streaming (`--otel-send gauges`)

The default OTEL streaming path posts one `anomaly.count` Sum data point per
injected anomaly. Add `gauges` to `--otel-send` to additionally stream
**every per-row metric value** from the per-component CSVs to the metrics
endpoint as OTLP `Gauge` data points. The two streams run
sequentially: anomaly counters first, then
gauges, both against the same endpoint (and the same auth token / activity
log). Pass `--otel-send gauges` alone when the receiver should get only the
Gauge payloads; that mode skips the anomaly log/metric/trace stream entirely.

Payload shape:

- One `resourceMetrics` entry per component, with `resource.attributes`
  carrying `service.name=<component>` and `service.namespace=anomaly-metric-creator`.
- Inside each, one `scopeMetrics` entry (`scope.name=anomaly-metric-creator`,
  `scope.version=1.0.0`) holding one `metrics[]` entry per
  `(component, metric)` pair, with `name=<prefix><metric>` and a
  `gauge.dataPoints[]` array carrying one data point per CSV row in the batch
  (each tagged `metric.name`, `component`, `signal.type=metric_value`).

Batching, dropped rows, and pacing:

- `--otel-gauge-batch-seconds` (default `60`) is how many seconds of
  **timeline coverage** are coalesced into one OTLP request. At
  `--interval-seconds 1` this is 60 rows per metric per component; at
  `--interval-seconds 0.1` it's 600 rows per metric per component.
- Dropped CSV rows (`--drop-rate`) are **suppressed from the gauge stream** —
  the streamer reads what was written to disk, so gauges mirror the realistic
  packet-loss view.
- `--otel-stream-speedup` paces consecutive flushes by their **batch anchor
  spacing**: between two batches the streamer sleeps
  `(batch_start_dt - prev_batch_start_dt) / speedup` seconds, which equals
  `batch_seconds / speedup` in steady state. For example, with
  `--otel-gauge-batch-seconds 60` and `--otel-stream-speedup 3600` the
  streamer waits ~16.7 ms between flushes.
- `--otel-stream-max-events` caps the total number of OTLP **requests** the
  gauge stream sends (mirroring its meaning for the counter stream); it
  does **not** cap individual data points.
- **`--inject-dst-artifact-day` is intentionally incompatible with gauge streaming.**
  The DST artifact duplicates the 02:00–02:59 wall-clock hour inside each
  per-component CSV, producing non-monotonic timestamps that break the
  gauge streamer's chronological merge. The parser rejects the combination
  with a clear error — pass `--inject-dst-artifact-day 0` (the default) or
  drop `gauges` from `--otel-send`. Supporting this would require a
  non-monotonic timestamp model for gauge batching, not just a parser change.
- **The combine step (`--emit …,combined`) preserves DST-duplicated rows.**
  `--inject-dst-artifact-day` duplicates the 02:00–02:59 wall-clock hour
  inside each per-component CSV. The unified combined CSV preserves both
  copies — every row in the per-component CSVs appears in the unified
  output, so the unified row count equals the per-component row count.
  The DST hour therefore appears with each timestamp duplicated in the
  unified CSV, mirroring the source files. Consumers that key on
  timestamp must handle the duplicates explicitly.
- **Normal generated combines skip the redundant wide pre-scan.**
  For freshly generated, non-DST wide component CSVs, `--emit ...,combined`
  trusts the generator's chronological write order and skips the defensive
  monotonic pre-scan. `combine DIR` still scans hand-staged inputs before
  streaming, so external inputs keep the safer behavior. Use
  `python tools/benchmark_combine.py` to compare the pre-scan and trusted
  paths on synthetic wide inputs.

Volume note: at the default `--interval-seconds 60` with the default 14
components x their default metric counts, a default run emits about 4.25M data
points with the default `--drop-rate 0`. The gauge stream at
`--otel-gauge-batch-seconds 60` produces one OTLP request per simulated minute.
Tune
`--otel-gauge-batch-seconds` up if your collector enforces a small request
body limit, or down if it limits batch element counts. The activity log file
(`--otel-activity-log`) is written in append mode for the gauge pass, so
both the counter and the gauge records share one file with
`signal=metrics_gauge` tagging the gauge records.

### Gauge metric file (`gauges.csv`)

The OTEL gauge stream (`--otel-send gauges`) requires an OTLP collector to
consume the per-row metric values. The file peer is `gauges.csv`: opt in by
adding `gauges` to `--emit` (requires `metrics`) and a long-form
CSV is written alongside the per-component CSVs.

```
amc --emit metrics,gauges
```

Schema (header row 1; columns locked):

```
timestamp,component,metric,value
```

- `timestamp` — identical formatting to the per-component CSV `timestamp`
  column (second precision at `--interval-seconds >= 1.0`, millisecond
  precision below).
- `component` — `COMPONENTS` key.
- `metric` — raw `MetricSpec.name` (no namespace prefix; see below).
- `value` — written through from the per-component CSV cell verbatim, so the
  file bytes never depend on Python's `str(float)` repr.

Rows are emitted in a chronologically merged timeline across all selected
components (the same ordering `stream_otel_gauges` produces over its OTLP
data points). Equal timestamps tie-break on `sorted(args.components)` order,
then per-component CSV column order (`MetricSpec` order). Dropped CSV rows
(`--drop-rate`) are absent from the file, matching the OTEL gauge stream's
behavior.

Filter passthrough:

- `--components` — restricts the rows to the named components.
- `--metrics-per-component` — drops the trimmed metric rows from the long
  form (the metric is absent from the per-component CSV column set, so it
  cannot appear in `gauges.csv`).
- `--drop-rate` — dropped rows are absent (skipped at the per-component CSV
  read).

Naming: `gauges.csv` uses the raw `MetricSpec.name`. The OTEL counterpart's
`--otel-gauge-metric-prefix` is an OTLP collector namespace convention and
does **not** apply to the file. Consumers that need a prefix join on the
`metric` column and prepend their own namespace.

Volume: long-form row count equals wide-form cell count
(rows-per-component × selected metrics × components). The existing preflight
cell-count cap (`PREFLIGHT_CELL_CAP`) bounds the wide form, so it transitively
bounds `gauges.csv` size. At default knobs the 50,000-row run produces roughly
4.25M data rows.

The `combine` subcommand does **not** regenerate `gauges.csv`; it's a derived
artifact of a fresh generation run only. A pre-existing `gauges.csv` is left
untouched on the `combine` subcommand path (mirrors `anomalies.csv`).

Consumer one-liners:

```sh
# Filter to one metric across all components, then plot via your tool of choice.
awk -F, 'NR==1 || $3=="cpu_util_pct"' gauges.csv > cpu_util_pct.csv

# Pandas long-form read:
# df = pd.read_csv("gauges.csv", parse_dates=["timestamp"])
# df_pivot = df.pivot_table(index="timestamp", columns=["component","metric"], values="value")
```

### Output schema document (`schema.json`)

Opt in by adding `schema` to `--emit` and a declarative
`schema.json` is written alongside the rest of the artifacts. The
document is the single source of truth the `validate` subcommand consumes to
check the run after the fact.

```
amc --emit metrics,schema
```

Top-level shape (`schema_version=2`, bumped in phase 7):

- `schema_version` — integer schema-document version (bumped on any
  breaking shape change; the validator rejects unknown versions).
- `metadata` — run-level parameters: `seed`, `start` (ISO 8601),
  `duration_days`, `interval_seconds`, `total_seconds`,
  `rows_per_component`, `drop_rate`, `signal_level`,
  `metrics_per_component`, `anomaly_count`, `scenarios` (sorted active
  set), `exclude_scenarios`, `components`, `inject_dst_artifact_day`,
  `emit_selection` (sorted), `combine`, `topology_mode` (always
  `realistic` since the phase-9 flag day; the field is retained so the
  validator keeps honoring documents from the historic `independent`
  mode).
- `files` — sorted list of artifact filenames the run wrote, derived
  from the same registry that drives `_pre_clean_output_dir` (per-
  component CSVs, `anomalies.csv`, `metric_report.log`,
  `metric_traces.jsonl`, `gauges.csv`, `combined_metrics_unified.csv`,
  `schema.json`).
- `components` — keyed by component name. Each entry has
  `csv_filename` plus a `metrics` array in MetricSpec column order.
  Each metric entry carries `name`, `unit`, `semantic_type` (one of
  `counter`, `gauge`, `ratio`, `rate`), `dtype` (`float` or `int`),
  `min_value`, `max_value`, and `derivation` (formula string when the
  column is computed from siblings, else `null`). Phase 8
  adds an optional `dimensions` block per component when the run is
  dim-aware (`--instances-per-component N>1` or a non-default
  `--instance-config`): `{"axes": ["pod"], "cardinality": 3}`. `axes`
  is the sorted subset of `host`, `pod`, `az`, `region`, `tenant`
  populated on at least one instance (the `id` field is excluded
  because it identifies an instance rather than naming an axis to
  slice on), and `cardinality` is the per-component instance count.
  The block is omitted entirely on the default single-anonymous-
  `Instance()` path so the v1 schema bytes (and the locked SHA-256
  hashes at 1d and 7d) stay byte-identical to today.
- `topology` (phase 7) — `{source: [{target, weight,
  saturation, correlation_threshold}, ...]}` snapshot of the directed
  coupling graph, restricted to the active component set.
  Constant-weight edges serialize their numeric weight verbatim;
  callable-weight edges serialize the literal string `"callable"`.
  `saturation` is either `null` or `{midpoint, steepness,
  latency_gain, error_gain}`. `correlation_threshold` is either a
  float or `null` (defaults to 0.85). Consumed by
  the `validate` subcommand's topology coupling check.

Output is byte-deterministic (`sort_keys=True`, fixed indent, UTF-8
with trailing newline) and locked SHA-256 hashes at 1d and 7d live in
`tests/test_schema_file.py`.

The `combine` subcommand does **not** regenerate `schema.json` (mirrors the
`gauges.csv` invariant); rerun a normal generation to refresh it.

### Output validation (the `validate` subcommand)

Pair the schema document with the standalone validator to assert a
run's artifacts are consistent with its declared shape:

```sh
# Hard-fail mode: reports every violation and exits 1 if there are any.
amc validate iot_logs

# Soft mode: violations go to stderr, exit code stays 0.
amc validate iot_logs --warn
```

The validator loads `DIR/schema.json` and runs:

- Every declared file is present on disk.
- No undeclared files in the directory (the registry intent that
  `_pre_clean_output_dir` enforces during generation).
- `anomalies.csv` rows are non-decreasing by timestamp.
- Per-component data row counts ≤ `rows_per_component` (plus the DST
  splice when applicable); the under-emission band is 8 σ around the
  expected drop count so a normal run doesn't false-positive. When
  the per-component schema declares `dimensions` (phase 8),
  both the upper bound and the under-emission band are multiplied by
  `cardinality` so the multi-instance long-form CSV (N copies of
  each row, one per instance) sits inside the band.
- Every row's timestamp falls in `[START, START + total_seconds)`.
- CSV header matches the schema's MetricSpec column order. When the
  per-component schema declares `dimensions`, the expected header is
  the long-form `timestamp, id, host, pod, az, region, tenant,
  <metrics…>` shape so a missing or reordered dim column surfaces as
  a header drift violation instead of cascading cell errors.
- Each cell parses as float, falls in `[min_value, max_value]` when
  declared, is whole-integer (modulo 3-decimal CSV precision) when
  `dtype="int"`, and is non-negative when `semantic_type` is `counter`
  or `rate`. (The dim cells themselves — string id/host/pod/az/region/
  tenant values — are skipped by the numeric checks.)
- Derived columns (today: `cacheservice.hit_ratio`) recompute from
  their source columns within `0.01` of the stored value. Under
  dim-aware schemas the recomputer's column-index lookup is offset by
  the 6-column dim prefix so the formula still reads the right cell.
- When any per-component schema declares `dimensions`, `gauges.csv`
  and `combined_metrics_unified.csv` (when emitted) must carry the
  10-column long-form header `timestamp, component, id, host, pod,
  az, region, tenant, metric, value` (phase 8). Mirrors the
  Phase 5 writer's any-of dispatch predicate.
- Topology coupling (phase 7): for every constant-weight edge
  declared in `topology`, the source's canonical load metric and the
  target's canonical load metric must correlate at Pearson ≥ 0.85
  (per-edge override via `Edge.correlation_threshold`). Skipped under
  `topology_mode == "independent"` and on callable-weight edges where
  the per-row weight signal — not the upstream load — drives the
  target. Anomaly spans (from `anomalies.csv`'s `span_start` /
  `span_end`, padded by 30s) are excluded from the row pool so
  scenario overrides don't dominate the realized correlation.

Default output is violation-free at every duration: the phase 6 flag
day cleared the fractional-integer set, and the phase 9 scenario
re-tune cleared the last known violation (the LLM context-overflow
scenario now saturates `llm_analytics.context_overflow_rate` toward
0.97, inside its declared `max_value=1`, instead of the historic 8.5).
A non-empty report from `validate` on unmodified default output is a
bug; `--warn` remains available for CI flows that prefer a report over
a failing exit code.

### Output files

Written to `--output-dir` (default `iot_logs/`):

- `authservice.csv`
- `cacheservice.csv`
- `apigateway.csv`
- `database.csv`
- `mqservice.csv`
- `llm_analytics.csv`
- `loadbalancer.csv`
- `objectstore.csv`
- `vectorstore.csv`
- `scheduler.csv`
- `paymentservice.csv`
- `identityprovider.csv`
- `observabilitypipeline.csv`
- `gpu_inference.csv`
- `anomalies.csv` — written alongside the per-component CSVs whenever
  `--emit` includes `metrics` (the default); explicitly deleted
  from `--output-dir` on runs that omit `metrics` (e.g.
  `--emit logs,traces`). Manifest of injected anomalies with at least one
  surviving row after the packet-loss mask. Rows are
  sorted by `(span_start, component, metric)` so the manifest reads
  chronologically. Columns:  
  `timestamp, component, metric, description, scenario_id, severity,
  is_cascade, event_id, parent_event_id, span_start, span_end, shape`.
  - `timestamp` — the anomaly entry's wall-clock timestamp; identical to
    `span_start` and, for shaped spans, anchored at the first kept row.
  - `description` — human-readable label (preserved as the historic 4th
    column for backward compatibility).
  - `scenario_id` — the `SCENARIOS` slug that produced this spec.
  - `severity` — `low` / `medium` / `high`, copied from the scenario.
  - `is_cascade` — lowercase `true` / `false` (cascade specs are the
    secondary blast-radius writes; primaries are `false`).
  - `event_id` — deterministic `evt_<sha1>` id stable across runs and
    shared with `metric_report.log` and `metric_traces.jsonl` (see
    `_anomaly_event_id`); one identity per anomaly across all artifacts.
  - `parent_event_id` — for cascade rows, the `event_id` of the same
    scenario's first surviving primary in the manifest's pre-sort order;
    empty for primaries and for orphan cascades (no surviving primary).
  - `span_start`, `span_end` — equal to `timestamp` for single-row specs;
    for any spec with `duration_seconds > 0` (including catalog entries
    with `shape: "step"` such as `db_disk_exhaustion`, `jwks_rotation_chaos`,
    `cache_leak_restart`, and `api_cpu_saturation`, plus all `ramp_linear`,
    `ramp_exp`, `sustained`, `sawtooth`, `sine` shapes), the actual
    first/last non-dropped in-range row timestamps covered by the span.
    They therefore always name timestamps that appear in the component CSV
    even under `--drop-rate`.
  - `shape` — the spec's shape (`step` is the default for single-row and
    cascade specs; a `step` spec may still have `duration_seconds > 0`,
    in which case `span_end > span_start`).
  - The packet-loss mask (`--drop-rate`) is applied per row, not per anomaly.
    A dropped row is omitted entirely from the per-component CSV (no row is
    emitted for that timestamp), and contributes no influence to neighboring
    rows. For single-row anomalies, a dropped target row therefore produces
    no per-component CSV row and no manifest entry. For shaped or
    `duration_seconds` spans, only the dropped rows within the span lose
    their override — any surviving rows in the span still receive the
    anomalous value in the per-component CSV.
  - A manifest entry is written when at least one row in the target row/span
    survives. For shaped or `duration_seconds` spans, a dropped nominal first
    row does not suppress the whole entry when later rows survive; the entry
    anchors at the first kept timestamp. If every row in the span is dropped,
    no manifest entry is produced. Single-row anomalies still produce no entry
    when their target row is dropped.
- `metric_report.log` — line-oriented report log aligned 1:1 with anomaly manifest rows via deterministic `event_id`.
- `metric_traces.jsonl` — JSONL traces aligned 1:1 with anomaly manifest rows (`event_id`, `trace_id`, `span_id`, timestamp/component/metric context).
- `gauges.csv` — long-form CSV with one row per `(timestamp, component, metric, value)` data point, written only when `--emit` includes `gauges` (which itself requires `metrics`). See [Gauge metric file](#gauge-metric-file-gaugescsv).
- `schema.json` — declarative per-metric and run-level schema, written only when `--emit` includes `schema`. Consumed by the `validate` subcommand. See [Output schema document](#output-schema-document-schemajson).
- `combined_metrics_unified.csv` — only when `--emit` includes `combined` or via the `combine` subcommand.

If you omit `--emit`, the default remains the full backward-compatible
set: metrics, logs, and traces.

Re-running into an existing `--output-dir` pre-cleans stale artifacts for any
emit type or component this run will not regenerate (e.g. a metrics-only re-run
deletes `metric_report.log` / `metric_traces.jsonl` from a prior `logs,traces`
run, a `logs,traces` re-run deletes per-component CSVs and `anomalies.csv`,
a re-run without `gauges` in `--emit` deletes a prior `gauges.csv`,
and a narrower `--components` re-run deletes the dropped CSVs). Files unknown
to this script — user notes or extra CSVs the standalone `combine` subcommand
would otherwise autodiscover — are left alone. The `combine` subcommand is exempt because it
reads the existing per-component CSVs as inputs. `./otel-activity.log` lives
outside `--output-dir` and is untouched by design.

### Per-component metric catalog

Each component declares up to **10** metrics in descending importance. The default
emitted set per component (when `--metrics-per-component` is unset) matches the
historic catalog; the supplemental tail is shown in *italics* and is only emitted
when `--metrics-per-component` is set high enough to reach it.

| Component                | Default # | Metrics (ordered by importance — supplemental tail in italics) |
| ------------------------ | --------- | ---- |
| `authservice`            | 6         | `active_sessions`, `login_attempts`, `login_success_rate`, `avg_auth_latency_ms`, `cpu_util_pct`, `error_rate`, *`avg_session_duration_s`*, *`password_reset_per_min`*, *`admin_actions_per_min`*, *`memory_util_pct`* |
| `cacheservice`           | 6         | `cache_hits`, `cache_misses`, `hit_ratio`, `avg_cache_latency_ms`, `memory_util_pct`, `error_rate`, *`evictions_per_sec`*, *`expired_keys_per_sec`*, *`cpu_util_pct`*, *`connected_clients`* |
| `apigateway`             | 6         | `requests_per_sec`, `avg_response_time_ms`, `backend_latency_ms`, `active_connections`, `cpu_util_pct`, `error_rate`, *`rate_limited_per_sec`*, *`tls_handshakes_per_sec`*, *`memory_util_pct`*, *`upstream_unhealthy_count`* |
| `database`               | 7         | `connections`, `read_latency_ms`, `write_latency_ms`, `queries_per_sec`, `cpu_util_pct`, `error_rate`, `disk_used_pct`, *`replication_lag_s`*, *`buffer_cache_hit_ratio`*, *`deadlocks_per_min`* |
| `mqservice`              | 6         | `pending_messages`, `processed_messages`, `avg_latency_ms`, `dead_letter_queue`, `mem_util_pct`, `error_rate`, *`publish_rate_per_sec`*, *`consumer_lag`*, *`unacked_messages`*, *`broker_disk_used_pct`* |
| `llm_analytics`          | 8         | `input_tokens_per_sec`, `output_tokens_per_sec`, `avg_context_window_size`, `llm_requests_per_sec`, `avg_llm_latency_ms`, `token_limit_hits_per_min`, `context_overflow_rate`, `llm_api_error_rate`, *`p95_llm_latency_ms`*, *`prompt_cache_hit_ratio`* |
| `loadbalancer`           | 7         | `requests_per_sec`, `healthcheck_failures`, `active_tls_handshakes`, `tls_handshake_errors`, `backend_5xx_per_sec`, `connection_resets`, `cpu_util_pct`, *`healthy_backends`*, *`avg_request_duration_ms`*, *`dropped_connections`* |
| `objectstore`            | 5         | `get_latency_ms`, `put_latency_ms`, `5xx_rate`, `bandwidth_mbps`, `requests_per_sec`, *`p99_get_latency_ms`*, *`avg_object_size_kb`*, *`error_rate`*, *`throttled_requests_per_sec`*, *`multipart_upload_rate`* |
| `vectorstore`            | 5         | `ann_query_latency_ms`, `embeddings_per_sec`, `recall_at_10`, `cache_hit_ratio`, `error_rate`, *`index_size_gb`*, *`queries_per_sec`*, *`avg_vector_dim`*, *`shard_skew_pct`*, *`compaction_lag_s`* |
| `scheduler`              | 5         | `jobs_running`, `jobs_queued`, `jobs_failed_per_min`, `avg_job_duration_s`, `missed_schedules`, *`retries_per_min`*, *`workers_available`*, *`job_throughput_per_min`*, *`queue_age_seconds_p95`*, *`cpu_util_pct`* |
| `paymentservice`         | 5         | `txn_per_sec`, `provider_5xx_rate`, `webhook_delivery_lag_s`, `auth_decline_rate`, `avg_txn_latency_ms`, *`chargebacks_per_min`*, *`settlement_lag_s`*, *`fraud_score_avg`*, *`retry_rate`*, *`error_rate`* |
| `identityprovider`       | 5         | `token_issuance_per_sec`, `jwks_fetch_latency_ms`, `mfa_challenges_per_min`, `failed_oidc_flows`, `key_rotation_events`, *`avg_token_size_bytes`*, *`revoked_tokens_per_min`*, *`session_introspection_rate`*, *`password_reset_rate`*, *`error_rate`* |
| `observabilitypipeline`  | 4         | `metrics_ingested_per_sec`, `dropped_metrics_per_sec`, `ingest_lag_s`, `pipeline_error_rate`, *`cardinality_count`*, *`retention_hours`*, *`compactions_per_min`*, *`shard_count`*, *`flush_latency_ms`*, *`cpu_util_pct`* |
| `gpu_inference`          | 10        | `batch_size`, `model_size_b`, `gpu_memory_pressure`, `kv_cache_usage`, `memory_fragmentation`, `gpu_utilization`, `throughput_tps`, `latency_p50_ms`, `latency_p99_ms`, `failure` |

Anomaly specs only target metrics within the historic default set, so dropping the
metric cap below a component's default count will filter out anomalies whose target
metric is no longer emitted — they simply will not appear in `anomalies.csv` or any
reporting artifact for that run.

### Topology graph (v1)

The `TOPOLOGY` constant declares the directed service-call graph alongside
`COMPONENTS`. It threads upstream load through downstream baselines on
every run (realistic topology has been the default since the phase 6
flag day and the only mode since phase 9 removed the `independent`
contrast alias).

- `loadbalancer → apigateway` — constant weight `1.0`. Couples
  `apigateway.requests_per_sec` to `loadbalancer.requests_per_sec`.
  Phase 4 saturation: midpoint `860`, steepness `6`, latency gain
  `0.4`, error gain `0.010` — drives apigateway
  `avg_response_time_ms`, `backend_latency_ms`, and `error_rate`.
- `apigateway → authservice` (`0.3`) — couples
  `authservice.login_attempts` to `apigateway.requests_per_sec`.
  Phase 4 saturation: midpoint `760`, steepness `6`, latency gain
  `0.5`, error gain `0.012` — drives authservice
  `avg_auth_latency_ms` and `error_rate`.
- `apigateway → cacheservice` (`0.4`) — couples both
  `cacheservice.cache_hits` and `cacheservice.cache_misses` to
  `apigateway.requests_per_sec`. Phase 4 saturation: midpoint `760`,
  steepness `6`, latency gain `0.3`, error gain `0.008` — drives
  cacheservice `avg_cache_latency_ms` and `error_rate`.
- `apigateway → database` (`0.3`) — couples
  `database.queries_per_sec` to `apigateway.requests_per_sec`. The
  three weights on the auth/cache/database routing trio sum to `1.0`
  (these are request-share fractions). The `apigateway → llm_analytics`
  edge below is **not** part of that routing trio — its constant
  weight is independent because the per-downstream renormalization
  in `_compose_topology_coupled_specs` normalizes the incoming edges
  to each downstream, not the outgoing edges from each upstream.
  Phase 4 saturation: midpoint `760`, steepness `6`, latency gain
  `0.6`, error gain `0.015` — drives database `read_latency_ms`,
  `write_latency_ms`, and `error_rate`.
- `apigateway → llm_analytics` (`1.0`, phase 5) — couples
  `llm_analytics.input_tokens_per_sec` to
  `apigateway.requests_per_sec` (the renormalization makes any
  positive single-edge weight equivalent), and adds saturation
  feedback to the LLM latency / error columns. Saturation: midpoint
  `760`, steepness `6`, latency gain `0.55`, error gain `0.015` —
  drives `avg_llm_latency_ms`, `p95_llm_latency_ms`, and
  `llm_api_error_rate`. The token-budget metering authority is
  apigateway (no synthetic `token_limiter` virtual node).
- `cacheservice → database` — callable weight: per-row cache-miss
  ratio (`cache_misses / (cache_hits + cache_misses)`) multiplied by
  the database's natural `queries_per_sec` baseline. Contributes
  additional DB load when the cache miss rate rises; additive on top
  of the constant-weight apigateway contribution. No saturation
  declared in v1.

See [docs/topology.md](docs/topology.md) for a rendered mermaid diagram
of the edge set above.

## Application flow

End-to-end execution of `main(argv=None)` covers three top-level modes:
the `combine DIR` subcommand (rebuild the unified CSV from existing
per-component CSVs), the `validate DIR` subcommand (load `DIR/schema.json`
and run every validator against the artifacts on disk), and the default
`generate` pipeline. See [docs/application-flow.md](docs/application-flow.md) for a
rendered mermaid diagram of the full pipeline and the `--emit` /
validator gating notes.

## Failure modes / anomaly catalog

Anomalies are time-offset injections that overwrite a metric column at a matched
row or span. Optional fields support span realism:

- `duration_seconds` — span length (0/omitted keeps single-row behavior)
- `shape` — `step` (default), `ramp_linear`, `ramp_exp`, `sustained`, `sawtooth`, `sine`
- `shape_params` — shape-specific parameters (for example `start/end`, `period_s`, `amplitude`, `midline`)

Surviving injected rows are emitted to the relevant per-component CSV, and
the anomaly is catalogued in `anomalies.csv` when at least one row in the
target row/span survives. The packet-loss mask (`--drop-rate`) is applied per
row, not per anomaly: each row in a shaped or `duration_seconds` span is masked
independently. Dropped rows are omitted entirely from the per-component CSV (no
row is emitted for that timestamp) and exert no influence on neighbors, while
surviving rows in the same span still receive the anomalous value. For shaped
or `duration_seconds` spans, the `anomalies.csv` entry anchors at the first kept
timestamp; if every row in the span is dropped, no manifest entry is produced.
Single-row anomalies still produce no entry when their target row is dropped.

Specs whose `time_offset` falls outside `[0, total_seconds)` — or whose nearest row index
falls outside `[0, n_rows)` at a coarse `--interval-seconds` — are soft-skipped with a
`WARNING:` line on stderr that names the `--duration-days` required to include them.

Every scenario below has a **slug** that can be passed to `--scenarios` or
`--exclude-scenarios`. Use `--scenarios all` (default) to include every reachable
scenario, or name specific slugs to narrow or exclude them.

### Scenario catalog

Every scenario in `SCENARIOS` is listed below (one table row per catalog
entry). The **Signal** column shows the minimum
`--signal-level` required (`low`/`medium`/`high`). The **Days** column shows the
minimum `--duration-days` required. The **Duration** column summarizes the span
lengths of the scenario's **primary** specs (`duration_seconds` in `SCENARIOS`).
It is a primary-span summary, not the full wall-clock footprint of the scenario:
scenarios can also include `instant` primaries and cascades at different
timestamps, which are not reflected here. Notation:

- A single value (e.g. `8 min`, `4h`) for a single-span incident, regardless of shape (`step`, `sustained`, `ramp_linear`, `ramp_exp`, `sawtooth`, `sine`).
- A multi-phase summary (e.g. `51h leak + 12h eviction cascade + 5 min restart/cold-start`) for staged incidents — a chronologically ordered list of notable primary spans separated by ` + `. The `+` is *not* an additive total, and spans may overlap (e.g. the 12h eviction cascade above occurs inside the 51h leak window).
- `instant` when every primary spec has `duration_seconds` omitted or `0` — each such spec expands to a single sample at its target timestamp (one row in the CSV before the `--drop-rate` mask is applied; the row may still be dropped at high drop rates).

Cascades are secondary specs within the same scenario that propagate the blast
radius to additional components.

| Slug | Signal | Days | Time / Day | Duration | Components touched | Description |
| ---- | ------ | ---- | ---------- | -------- | ------------------ | ----------- |
| `auth_brute_force` | medium | 1 | 02:15 | 15 min | `authservice`, `apigateway` | Login brute-force window — error rate 42%, login surge 1,250/s; cascades to gateway 5xx and session invalidation. |
| `cache_collapse` | medium | 1 | 06:00 | 20 min miss collapse + 4h leak + 30 min memory plateau | `cacheservice`, `database` | Cache hit-ratio collapse to 5% + slow memory leak 70%→96% + later memory pressure plateau; cascades to DB query spike and read latency. |
| `api_cpu_saturation` | medium | 1 | 06:30 | 10 min CPU saturation + 30 min sawtooth + 14h sustained step + 8 min retry storm + 5 min config error | `apigateway`, `authservice`, `cacheservice` | Gateway CPU saturation (100%) + 25% config-push error burst + retry storm — cascades to auth errors (~35%) and cache errors (~15%). |
| `db_stall` | medium | 1 | 00:00 | 24h disk ramp + 30 min backup pile-up + 20 min DB stall + 6h connection-leak ramp + 20 min brown-out + 20 min nightly batch | `database`, `apigateway`, `authservice`, `mqservice` | DB disk exhaustion ramp, backup-window connection pile-up, read-latency stall, backend errors 35%, brown-out (~8% errors), nightly batch; cascades to backend latency, gateway 5xx (~30%), auth latency, MQ backpressure. |
| `mq_jam` | medium | 1 | 12:30 | 15 min DLQ blow-up + 20 min queue jam | `mqservice`, `apigateway`, `authservice`, `database` | Message queue DLQ blow-up + 1M pending + error rate 25%; cascades to slow API response, DB connection buildup, slow writes, auth session write delay. |
| `lb_flapping` | medium | 1 | 03:00 | 5 min TLS burst + 10 min health-check flap + 5 min resets + 8 min backend 5xx | `loadbalancer`, `apigateway` | TLS cert near-expiry errors 80/s + LB health-check failures; cascades to gateway 5xx (~30%) and reduced active connections. |
| `object_store_5xx` | medium | 1 | 07:00 | 12 min 5xx wave + 30 min bandwidth saturation + 15 min latency tail | `objectstore`, `apigateway` | Object store 5xx surge (18%) + bandwidth saturation (950 Mbps); cascades to gateway 5xx (~6%). |
| `vectorstore_pressure` | medium | 1 | 10:30 | 30 min index rebuild + 1h recall degradation | `vectorstore`, `llm_analytics` | Vector store index rebuild stall (280 ms), recall degrades to 0.62; cascades to LLM latency elevation and fallback retry errors (15%). |
| `scheduler_overflow` | medium | 1 | 08:00 | 20 min job overrun + 30 min missed schedules + 45 min queue overflow | `scheduler`, `database` | Job overrun 4×, 12 missed schedules, 2,500-job queue overflow; cascades to DB connection buildup. |
| `payment_5xx` | medium | 1 | 12:00 | 12 min provider 5xx + 30 min webhook lag + 45 min fraud-rule misfire | `paymentservice`, `apigateway` | Stripe-style provider 5xx (18%), webhook lag 5 min, fraud-rule decline-rate spike (35%); cascades to gateway 5xx (~28%). |
| `idp_jwks_storm` | medium | 1 | 04:00 | 20 min JWKS storm + 30 min MFA outage + 15 min OIDC parse errors | `identityprovider`, `authservice` | JWKS cache-miss storm — fetch latency 1,500 ms, MFA provider degradation; cascades to degraded login success rate. |
| `observability_lag` | medium | 1 | 09:00 | 30 min ingest lag + 20 min cardinality storm + 20 min pipeline errors | `observabilitypipeline`, `mqservice` | Ingest lag grows to 240s, high-cardinality push drops 8,500 metrics/s; cascades to downstream MQ queue backup. |
| `monday_baseline` | low | 1 | 09:00 | 1h baseline shift | `authservice`, `apigateway` | Benign Monday-morning login burst (1,400/s) + RPS spike (2,200/s). No cascades — low severity baseline shift only. |
| `llm_viral_surge_day2` | medium | 2 | Day 2 10:15 | 12 min viral surge | `llm_analytics`, `apigateway`, `cacheservice`, `database` | Viral LLM surge — 8× request spike to 360/s, token surge 185k/s; cascades to gateway RPS, cache misses, DB query spike and connections. |
| `llm_enterprise_onboarding` | medium | 3 | Day 3 14:00 | 6h correlated capacity ramp | `llm_analytics`, `vectorstore`, `cacheservice`, `database` | Enterprise onboarding — requests, context windows, token-limit hits, embeddings, and LLM latency rise together; cascades to DB latency and cache memory. |
| `llm_rate_limit_fallout` | medium | 5 | Day 5 09:30 | 90 min correlated rate-limit fallout | `llm_analytics`, `apigateway` | Upstream rate-limiting — LLM error rate, average latency, and gateway errors rise together; gateway cascade anchor spikes to ~28%. |
| `llm_weekend_batch` | medium | 6 | Day 6 02:00 | 4h correlated batch saturation | `llm_analytics`, `objectstore`, `cacheservice`, `database` | Weekend batch analytics — input tokens, context overflow, LLM latency, object-store bandwidth, and DB query/CPU pressure rise together; cascades to cache misses. |
| `llm_second_viral` | medium | 7 | Day 7 16:45 | 15 min viral surge | `llm_analytics`, `apigateway`, `cacheservice`, `database` | Second viral event — 10× spike to 450/s, 420k tokens/s; cascades to gateway active connections, CPU, DB connections, cache errors. |
| `gpu_inference_fragmentation` | medium | 1 | full default window | sparse labels + coherent incident core | `gpu_inference`, `llm_analytics` | GPU serving telemetry follows the reference CSV's sparse label scale: 1,204 failure rows in the default 50,000-row shape, mostly singleton runs, and a dense detector-visible core. The core concentrates part of the sparse failure budget into a ramp/plateau/recovery window while fragmentation, memory pressure, utilization dips, low throughput, p99 latency, and KV cache saturation cross bad thresholds together for sustained rolling-window signal. |
| `regional_failover_storm` | **high** | 1 | 05:00 | 5 min | `loadbalancer`, `apigateway`, `authservice`, `database`, `mqservice` | Regional failover — backend 5xx ramps to 220/s over 5 min; cascades to gateway 5xx (~30%), DB connections (~9,000), auth errors (~40%), MQ pending ~500k. |
| `dns_provider_outage` | **high** | 1 | 11:00 | 6 min | `loadbalancer`, `apigateway`, `identityprovider`, `paymentservice` | External DNS provider outage — TLS handshake errors 45/s, backend 5xx 80/s, health check failures 8/s, sustained for 6 min; cascades to OIDC callback failures (~150), payment provider 5xx (~32%), gateway error rate (~28%). Sharp step-up at T0 and step-down at T1. |
| `cache_db_meltdown` | **high** | 1 | 11:30 | 10 min | `cacheservice`, `database`, `llm_analytics`, `apigateway` | Coordinated cache memory saturation (80%→99.5%) + DB read latency (800 ms); cascades to doubled LLM latency and elevated gateway backend latency. |
| `deploy_bad_canary_rollback` | **high** | 1 | 15:00 | 8 min | `apigateway`, `authservice`, `cacheservice`, `database` | Bad canary deploy plateau — gateway error rate 18%, backend latency 480 ms, retry-driven RPS 1,100, sustained 8 min until rollback; cascades to login success (~92%), cold-cache miss spike (~1,200), DB connection pile-up (~5,800). Sharp step-up at T0 and step-down at T1. |
| `llm_provider_outage` | **high** | 1 | 20:00 | 15 min | `llm_analytics`, `apigateway`, `cacheservice` | LLM provider sustained outage — error rate 5%→60%, latency 8,000 ms; cascades to gateway 5xx (~35%) and context cache miss surge (~3,000). |
| `network_partition_az_split` | **high** | 1 | 18:20 | 4 min | `database`, `mqservice`, `apigateway`, `authservice` | Intra-region AZ network partition — DB replication lag 18 s, DB error rate 30%, MQ consumer lag 12,000, unacked messages 4,500, sustained 4 min until heal; cascades to gateway backend latency (~380 ms), auth replica read failures (~40%). Sharp step-up at T0 and step-down at T1. (Shifted from 18:00 to clear `db_stall`'s 18:00–18:20 brown-out on `database.error_rate`.) |
| `gateway_ddos` | **high** | 1 | 16:00 | 10 min | `apigateway`, `authservice`, `database`, `mqservice` | Gateway DDoS-style saturation — 5,000 RPS + CPU 99% for 10 min; cascades to auth latency (~600 ms), DB CPU (~92%), MQ pending (~800k). |
| `storage_layer_pressure` | **high** | 1 | 22:00 | 10 min | `objectstore`, `database`, `apigateway` | Storage layer pressure — PUT latency 60→700 ms + object-store 5xx 25%; cascades to DB write latency (~90 ms) and gateway error rate (~30%). |
| `cache_leak_restart` | medium | 2 | Day 2–4 | 51h leak + 12h correlated eviction/DB pressure + 5 min restart/cold-start | `cacheservice`, `database`, `apigateway`, `mqservice` | Cache memory-leak death march 50%→95% over 51h with cache misses, DB query pressure, and DB read latency rising together → forced restart → cold-start cache miss / DB query stampede + brief gateway and MQ pressure. (Full sequence needs `--duration-days 4`; shorter multi-day runs emit the in-range portion with stderr WARNINGs for the tail.) |
| `jwks_rotation_chaos` | medium | 3 | Day 3–5 | 6h TLS flapping + 8h JWKS latency + 6h login degradation + 2h cert-expiry window | `loadbalancer`, `identityprovider`, `authservice`, `apigateway`, `paymentservice`, `cacheservice` | Cert/JWKS rotation chaos — TLS flapping, JWKS latency, login degradation, hard cert expiry spike to 200/s + 800 OIDC failures; cascades across gateway, auth, payments, and cache. (Full sequence needs `--duration-days 5`.) |
| `db_disk_exhaustion` | medium | 2 | Day 2–6 | 96h disk ramp + 12h correlated I/O saturation + 20 min emergency log-truncation | `database`, `scheduler`, `observabilitypipeline`, `mqservice`, `apigateway` | DB disk creeps 65%→92% over 96h; write latency, connections, CPU, observability lag, and MQ backlog rise together before emergency log truncation. (Full sequence needs `--duration-days 6`.) |
| `auth_pod_failure` | high | 1 | Day 1 | 5 min auth pod-0 partial failure — `instance_filter=["i0"]` | `authservice`, `apigateway` | Single authservice pod (id=`i0`) suffers an error_rate spike to 85% and login_success_rate collapse to 30%; cascades to a gateway backend latency spike on that pod. Requires `--instances-per-component >= 2` or an `--instance-config` with named instances to observe the per-pod isolation. |
| `cache_az_isolation` | high | 1 | Day 1 | 10 min Cache AZ `us-east-1a` isolation — callable `instance_filter` | `cacheservice` | Instances in AZ `us-east-1a` suffer a cache_hits collapse (~500) and cache_misses spike (~3000), driving the derived hit_ratio down sharply. Requires `--instance-config` with `az` fields set; instances without `az` see no effect (zero-match WARNING). |

## Tests

Dev dependencies (`pytest`, `pytest-xdist`, `numpy`, `ruff`, `pre-commit`)
ship under the `dev` extra.

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest        # runs across 4 workers by default (see below)
```

Tests live in `tests/` and write only into `tmp_path` (never `iot_logs/`). The suite
runs full 1-day and 7-day generations end-to-end via `main()`. Composition matrix
coverage for `--scenarios` / `--exclude-scenarios` lives in
`tests/test_scenarios.py` (selector intersection, WARNING content, `--anomaly-count`
interaction); the canonical slug catalog is the [scenario catalog](#scenario-catalog)
table in this file.

### Parallel execution

`pyproject.toml` sets `addopts = "-ra --dist loadfile -n 4"` and declares
`required_plugins = ["pytest-xdist"]`, so the default invocation runs across
4 worker processes, distributes tests by file, and fails fast with a clear
message if `pytest-xdist` is missing. This drops the broader validation sweep
from ~15–22 minutes serial to ~5 minutes parallel. Override on the command
line if your host needs a different worker count:

```bash
.venv/bin/pytest -n 0   # in-process; required for `pdb` / true serial
                        # also the right choice on low-RAM (< 8 GB) CI runners
.venv/bin/pytest -n 8   # bigger boxes (~16 GB RAM headroom recommended)
```

`-n 1` is not a true serial run — xdist still spawns one worker subprocess,
which breaks interactive debuggers like `pdb`. Use `-n 0` instead when you
need in-process execution.

Session-scoped fixtures in `tests/conftest.py` are lazily instantiated **per
worker** the first time a worker touches a test that requests them. Peak
fixture RAM therefore scales with how many distinct workers hit each fixture
— `--dist loadfile` keeps each file's tests on a single worker, which
collapses fan-out to at most one instantiation per file. The
`n3_one_day_dataset_dir` fixture alone is ~1.3 GB; 4 workers caps peak
fixture memory near ~5 GB even under worst-case fan-out.

### Test-hygiene lint

Several focused checks run on every `git commit` via `.pre-commit-config.yaml`:

- **`ruff` F401 (unused imports).** Enforces the rule called out in
  [.trellis/spec/amc/backend/testing-quality.md](.trellis/spec/amc/backend/testing-quality.md)
  ("Pytest, Ruff, and Pre-Commit") as a
  mechanical check rather than a human-reviewer task. The configuration
  lives in `pyproject.toml` (`[tool.ruff.lint] select = ["F401"]`); the
  hook scopes it to `tests/`.
- **`amc-no-direct-spec-load` (`tools/check_amc_module_load.py`).** AST
  walk over each test file that flags any
  `spec_from_file_location(...)` call expression — the duplicate-load
  pattern that shipped in PR #63 and PR #64 (a new test re-imports
  `anomaly-metric-creator.py` instead of consuming the session-scoped
  `amc` fixture from `tests/conftest.py:_load_amc`, doubling the
  registry-build cost). `conftest.py` is exempted wholesale; an
  individual call line opts out with a trailing `# amc-load: allow`
  comment for the rare case that genuinely needs a fresh module
  instance (e.g. monkey-patching `_apply_scenarios` in
  `tests/test_correctness.py`).
- **Python syntax (`tools/check_python_syntax.py`).** Parses `src/`, `tests/`,
  `tools/`, and Python hook adapters with `ast.parse` so syntax errors fail
  before review without creating `__pycache__` entries.
- **`ruff` F841 (unused local variables).** Scopes unused-local enforcement to
  runtime code, helper tools, and Python hook adapters.
- **Agent hook exceptions (`tools/check_agent_hook_exceptions.py`).** Forbids
  `except BaseException` / bare `except` in Python hook adapters and requires a
  reason comment on intentionally empty `except Exception: pass` handlers.
- **Trellis placeholders (`tools/check_trellis_placeholders.py`).** Blocks
  unfinished journal/task template text such as `(Add details)` from committed
  Trellis workspace artifacts.
- **Trace payload anti-patterns (`tools/check_trace_payload_antipatterns.py`).**
  Keeps command-trace import/export boundaries on strict validators instead of
  direct casts or silent malformed-entry filtering.

Install and run locally (the `dev` extra installs both `ruff` and
`pre-commit`):

```bash
.venv/bin/pip install -e '.[dev]'           # installs ruff + pre-commit
.venv/bin/pre-commit install                # one-time per clone
.venv/bin/pre-commit install --hook-type pre-push  # one-time per clone
.venv/bin/pre-commit run --all-files        # ad-hoc full sweep
.venv/bin/ruff check tests/                 # direct ruff F401 check
```

The `tests/`-scoped hooks run automatically on `git commit` for any
staged Python file under `tests/` (the `files: ^tests/.*\.py$`
pattern matches subdirectories too). Adding or moving an unused import to
`tests/` makes the commit fail with an `F401` diagnostic; `ruff check --fix
tests/` removes it. Adding a
`spec_from_file_location(...)` call in a new test file fails the
commit with a pointer to the canonical loader; switch to the `amc`
fixture or annotate the line with `# amc-load: allow`.

The `branch-name` hook runs at the `pre-push` stage (a separate
git hook from `pre-commit`), which is why
`pre-commit install --hook-type pre-push` is a one-time per-clone
step. It rejects
any branch name matching `(?i)(^|\b)ver-\d+` — see
[.trellis/spec/amc/backend/testing-quality.md](.trellis/spec/amc/backend/testing-quality.md)
and [CLAUDE.md](CLAUDE.md) for the policy, anchors, and full invocation modes
of `tools/check_branch_name.py`. The
pre-commit hook checks the current local branch only; for full
coverage of refspec pushes (`git push origin clean:ver-123`) and
detached-HEAD pushes (`git push origin HEAD:ver-123`), see the
hand-rolled `.git/hooks/pre-push` snippet in CLAUDE.md.
