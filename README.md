# anomaly-metric-creator

`anomaly-metric-creator.py` generates synthetic IoT-style metric logs for a SaaS stack
with built-in anomalies. By default (`--emit-selection metrics,logs,traces`) it
writes one CSV per component plus an `anomalies.csv` manifest that catalogues each
injected anomaly whose span anchor row survives the `--drop-rate` packet-loss mask;
runs that omit `metrics` (e.g. `--emit-selection logs,traces`) skip the per-component
CSVs and delete `anomalies.csv` from `--output-dir`. See [Output files](#output-files)
for the exact emit-selection and packet-loss gating. Output is deterministic for a
given `--seed`.

By default the script emits **one day** of second-by-second metrics for thirteen
components: `authservice`, `cacheservice`, `apigateway`, `database`, `mqservice`,
`llm_analytics`, `loadbalancer`, `objectstore`, `vectorstore`, `scheduler`,
`paymentservice`, `identityprovider`, `observabilitypipeline`. Duration,
sampling interval, drop rate, and output directory are all CLI-configurable.

## Install

Requires Python 3.11+.

```bash
# Library install (numpy only):
pip install numpy

# Editable install with dev extras (pytest + numpy):
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
```

## Usage

```bash
# Default: 1 day (86,400 rows per component)
python3 anomaly-metric-creator.py

# Full week (604,800 rows per component). Each multi-day scenario activates at
# its own `days_required` (e.g. llm_viral_surge_day2 at 2 days, cache_leak_restart
# at 2 days, jwks_rotation_chaos at 3 days, llm_second_viral at 7 days);
# --duration-days 7 unlocks the complete multi-day catalog.
python3 anomaly-metric-creator.py --duration-days 7

# Coarser sampling: one row every 5 seconds (17,280 rows per component for 1 day).
python3 anomaly-metric-creator.py --interval-seconds 5

# Generate logs and produce the unified joined CSV in one shot:
python3 anomaly-metric-creator.py --combine

# Skip generation; only build the unified CSV from an existing output dir:
python3 anomaly-metric-creator.py --combine-only --output-dir iot_logs

# Emit only a subset of artifact types:
python3 anomaly-metric-creator.py --emit-selection metrics,logs
python3 anomaly-metric-creator.py --emit-selection traces

# Emit only a subset of components (CSVs, anomalies.csv, reporting artifacts,
# and OTEL streaming are all filtered to just these components):
python3 anomaly-metric-creator.py --components authservice,database

# Pick the signal intensity level: low (only benign baseline shifts),
# medium (default — today's full catalog), or high (additionally activates
# the high-pressure cross-component scenarios):
python3 anomaly-metric-creator.py --signal-level high

# Run only the coordinated cache+DB meltdown scenario at high signal level:
python3 anomaly-metric-creator.py --signal-level high --scenarios cache_db_meltdown

# Run all scenarios except LLM-related ones and the Monday baseline:
python3 anomaly-metric-creator.py --exclude-scenarios llm_viral_surge_day2,llm_enterprise_onboarding,llm_rate_limit_fallout,llm_weekend_batch,llm_second_viral,llm_provider_outage,monday_baseline

# Run the full default catalog at 7 days but drop the JWKS rotation scenario
# (handy when you want every other multi-day scenario without re-typing the
# full slug list):
python3 anomaly-metric-creator.py --duration-days 7 --exclude-scenarios jwks_rotation_chaos

# Selector composition order: --scenarios (allowlist) → --exclude-scenarios
# (denylist, wins on overlap) → --signal-level (severity gate) →
# --duration-days (duration gate) → --components (component allowlist).
# Slugs dropped by the severity or duration gate emit a stderr WARNING line;
# slugs disjoint from --components are dropped silently.

# Cap the total anomaly count across the whole dataset (deterministic for a
# given --seed). Useful for keeping noisy test datasets small or sweeping
# across anomaly density:
python3 anomaly-metric-creator.py --anomaly-count 25

# Cap the metric columns emitted per component (1..10). Each component emits
# the first N of its priority-ordered catalog. Omit the flag to keep the
# historic default per-component count:
python3 anomaly-metric-creator.py --metrics-per-component 3
python3 anomaly-metric-creator.py --metrics-per-component 10

# Stream anomaly events as OTLP signals while generating locally:
# OTEL streaming is OFF by default; pass --otel-enabled to opt in.
python3 anomaly-metric-creator.py \
  --otel-enabled \
  --otel-logs-endpoint http://localhost:4318/v1/logs \
  --otel-metrics-endpoint http://localhost:4318/v1/metrics \
  --otel-traces-endpoint http://localhost:4318/v1/traces \
  --otel-stream-speedup 3600

# Stream with signal-specific env controls (still requires --otel-enabled):
MEZMO_OTEL_LOGS_ENDPOINT=http://localhost:4318/v1/logs \
MEZMO_OTEL_LOGS_AUTH_TOKEN=secret \
python3 anomaly-metric-creator.py --otel-enabled
```

### CLI flags

| Flag                | Default     | Notes                                                              |
| ------------------- | ----------- | ------------------------------------------------------------------ |
| `--duration-days`   | `1`         | Days to generate. Each multi-day scenario has its own `days_required` (the day index of its earliest in-range offset, e.g. `llm_viral_surge_day2` at 2, `jwks_rotation_chaos` at 3, `llm_second_viral` at 7); see the [scenario catalog](#scenario-catalog) for per-scenario values. Pass `--duration-days 7` to unlock the full multi-day catalog. |
| `--seed`            | `42`        | RNG seed for deterministic output.                                 |
| `--output-dir`      | `iot_logs`  | Directory CSVs are written into (created if missing).              |
| `--drop-rate`       | `0.0005`    | Per-row probability of dropping the row entirely from the per-component CSV (no row is emitted for that timestamp). Simulated packet loss. |
| `--interval-seconds`| `1.0`       | Seconds between consecutive rows. Sampling-density knob — timeline coverage stays `duration_days * 86400`s and row count is `floor(total_seconds / interval)`. Must be `>= 0.001` (millisecond precision floor). Anomalies map to the nearest row via `round(time_offset / interval)`. Values ≥ 1.0 emit second-precision timestamps (`YYYY-MM-DD HH:MM:SS`); values < 1.0 emit millisecond-precision timestamps (`YYYY-MM-DD HH:MM:SS.SSS`) so adjacent sub-second rows remain unique. |
| `--emit-selection`  | `metrics,logs,traces` | Comma-separated artifact selection. Valid values are `metrics`, `logs`, `traces`; any combination is allowed. `metrics` writes the per-component CSVs and `anomalies.csv`, `logs` writes `metric_report.log`, and `traces` writes `metric_traces.jsonl`. |
| `--components`      | `all`       | Comma-separated component allowlist. Filters CSV emission, `anomalies.csv`, reporting artifacts, and OTEL streaming to only the named components. Use `all` (default) for every component. Allowed names: `apigateway`, `authservice`, `cacheservice`, `database`, `identityprovider`, `llm_analytics`, `loadbalancer`, `mqservice`, `objectstore`, `observabilitypipeline`, `paymentservice`, `scheduler`, `vectorstore`. |
| `--scenarios`       | `all`       | Comma-separated allowlist of named scenario slugs (case-insensitive). Use `all` (default) to include every scenario in the `SCENARIOS` registry that passes the severity and duration gates. The `all` sentinel is mutually exclusive with explicit slugs (`all,foo` is rejected). Scenarios outside the active `--signal-level` severity hierarchy or whose `days_required` exceeds `--duration-days` are dropped with a stderr `WARNING: scenario <slug> requires …` message; scenarios whose `components_touched` is disjoint from `--components` are dropped silently. See the [scenario catalog](#scenario-catalog) for all known slugs and the composition order. |
| `--exclude-scenarios` | _empty_   | Comma-separated denylist of scenario slugs to subtract from the resolved set (applied after `--scenarios`, before the severity/duration/components gates). Case-insensitive. Useful for `--exclude-scenarios jwks_rotation_chaos` to get every scenario except one; on overlap with `--scenarios`, exclusion wins. |
| `--signal-level`    | `medium`    | Anomaly intensity level: `low`, `medium` (default), or `high`. Inclusion hierarchy: `low` only fires specs explicitly tagged `severity="low"` (today: a handful of benign Monday-morning baseline shifts) and intentionally has **no cascade fan-out** because benign baseline shifts do not realistically propagate as failures; `medium` adds the standard catalog plus its cascade fan-out (the default behavior); `high` additionally activates the high-pressure cross-component scenarios (regional failover storm, coordinated cache+DB meltdown, LLM provider outage, gateway DDoS saturation, storage layer pressure) and their cascades. |
| `--anomaly-count`   | _unlimited_ | Optional cap on the total number of injected anomalies (primary specs + cascades) across the whole dataset. Sampling is deterministic for a given `--seed` and uses its own RNG stream so it doesn't perturb the column noise. Applied after `--signal-level` and `--components` filters. Out-of-range specs (e.g. multi-day cascades on a 1-day run) are excluded from the sampling pool. |
| `--metrics-per-component` | _historic default per component_ | Optional cap on the metric columns emitted per component (must be in `[1, 10]`). Omit the flag to keep today's per-component count (4–8 metrics depending on component). When provided, every component emits the first `N` entries from its priority-ordered metric catalog (highest-value metrics first). Anomalies whose target metric is trimmed by the cap are filtered out before generation. |
| `--combine`         | _off_       | After generation, also write `combined_metrics_unified.csv` into `--output-dir`. Respects `--components` when set; otherwise combines every CSV in `--output-dir`. |
| `--combine-only`    | _off_       | Skip generation; only run the combine step against an existing `--output-dir`. Mutually exclusive with `--combine`. Respects `--components` when set; otherwise combines every CSV in `--output-dir`. |
| `--inject-dst-artifact-day` | `0` | 1-based day to inject a fall-DST artifact: the 02:00–02:59 wall-clock hour is duplicated, so the day's CSVs gain ~3,600/interval rows with non-monotonic timestamps. `0` disables. Generator quirk, not an anomaly — does not appear in `anomalies.csv`. |
| `--otel-enabled` / `--otel-disabled` | _off_ | Master switch for OTEL streaming. Default is off — configured endpoints are ignored at runtime unless `--otel-enabled` is passed. `--otel-disabled` forces it off and is mutually exclusive with `--otel-enabled`. Enabling without any configured endpoint is a usage error. |
| `--otel-logs-endpoint` | `MEZMO_OTEL_LOGS_ENDPOINT` | Optional OTLP/HTTP logs endpoint. Anomaly events are replayed as `resourceLogs` when `--otel-enabled`. |
| `--otel-logs-auth-token` | `MEZMO_OTEL_LOGS_AUTH_TOKEN` | Optional auth token for logs endpoint. |
| `--otel-metrics-endpoint` | `MEZMO_OTEL_METRICS_ENDPOINT` | Optional OTLP/HTTP metrics endpoint. Anomaly events are replayed as `anomaly.count` sum metrics when `--otel-enabled`. |
| `--otel-metrics-auth-token` | `MEZMO_OTEL_METRICS_AUTH_TOKEN` | Optional auth token for metrics endpoint. |
| `--otel-traces-endpoint` | `MEZMO_OTEL_TRACES_ENDPOINT` | Optional OTLP/HTTP traces endpoint. Anomaly events are replayed as span events when `--otel-enabled`. |
| `--otel-traces-auth-token` | `MEZMO_OTEL_TRACES_AUTH_TOKEN` | Optional auth token for traces endpoint. |
| `--otel-stream-speedup` | `3600.0` | Replay speed multiplier for OTEL streaming. `1.0` is real-time, `3600.0` replays one hour of anomaly spacing per second. |
| `--otel-stream-timeout-seconds` | `5.0` | HTTP timeout for each OTEL post attempt. |
| `--otel-stream-max-events` | _all_ | Optional cap on streamed anomaly events for smoke-testing a receiver. |
| `--otel-stream-auth-scheme` | `MEZMO_OTEL_STREAM_AUTH_SCHEME` or `Bearer` | Auth scheme prefix used with the OTEL auth tokens. |
| `--otel-stream-protocol` | `MEZMO_OTEL_STREAM_PROTOCOL` or `protobuf` | OTLP payload mode: `json` (`application/json`) or `protobuf` (`application/x-protobuf`). |
| `--otel-activity-log` | `./otel-activity.log` | File that records every OTEL streaming activity (`START`, `SEND`, `OK`, `RETRY`, `FAIL`, `END`) when `--otel-enabled` is set. Only created when streaming actually runs. |
| `--otel-verbose` / `--no-otel-verbose` | _off_ | When enabled, the activity log captures the raw OTLP payload (`body`), the request `content_type` and other request headers (auth values masked as `<scheme> ***`), the HTTP response `status` on success, and the exception `error_type` (plus HTTP `status` for `HTTPError`) on retry/failure. Useful for offline debugging of receiver behavior. |

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
- `anomalies.csv` — written alongside the per-component CSVs whenever
  `--emit-selection` includes `metrics` (the default); explicitly deleted
  from `--output-dir` on runs that omit `metrics` (e.g.
  `--emit-selection logs,traces`). Manifest of injected anomalies whose
  span anchor row (`span_idx == 0`) survives the packet-loss mask, with
  columns:  
  `timestamp, component, metric, description`.
  - The packet-loss mask (`--drop-rate`) is applied per row, not per anomaly.
    A dropped row is omitted entirely from the per-component CSV (no row is
    emitted for that timestamp), and contributes no influence to neighboring
    rows. For single-row anomalies, a dropped target row therefore produces
    no per-component CSV row and no manifest entry. For shaped or
    `duration_seconds` spans, only the dropped rows within the span lose
    their override — any surviving rows in the span still receive the
    anomalous value in the per-component CSV.
  - A manifest entry is written only when the **first** row of the span
    (`span_idx == 0`) is kept. If that anchor row is dropped, no manifest
    entry is produced even when later rows in the same span survive and
    carry the anomaly value. The generator never slides anomalies forward
    to a later timestamp.
- `metric_report.log` — line-oriented report log aligned 1:1 with anomaly manifest rows via deterministic `event_id`.
- `metric_traces.jsonl` — JSONL traces aligned 1:1 with anomaly manifest rows (`event_id`, `trace_id`, `span_id`, timestamp/component/metric context).
- `combined_metrics_unified.csv` — only when `--combine` / `--combine-only` is passed.

If you omit `--emit-selection`, the default remains the full backward-compatible
set: metrics, logs, and traces.

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

Anomaly specs only target metrics within the historic default set, so dropping the
metric cap below a component's default count will filter out anomalies whose target
metric is no longer emitted — they simply will not appear in `anomalies.csv` or any
reporting artifact for that run.

## Failure modes / anomaly catalog

Anomalies are time-offset injections that overwrite a metric column at a matched
row or span. Optional fields support span realism:

- `duration_seconds` — span length (0/omitted keeps single-row behavior)
- `shape` — `step` (default), `ramp_linear`, `ramp_exp`, `sustained`, `sawtooth`, `sine`
- `shape_params` — shape-specific parameters (for example `start/end`, `period_s`, `amplitude`, `midline`)

Surviving injected rows are emitted to the relevant per-component CSV, and
the anomaly is catalogued in `anomalies.csv` when the span's first row
(`span_idx == 0`) is kept. The packet-loss mask (`--drop-rate`) is applied
per row, not per anomaly: each row in a shaped or `duration_seconds` span is
masked independently. Dropped rows are omitted entirely from the per-component
CSV (no row is emitted for that timestamp) and exert no influence on
neighbors, while surviving rows in the same span still receive the anomalous
value. The `anomalies.csv` entry is written only when the first row of the
span (`span_idx == 0`) is kept; if that anchor row is dropped, no manifest
entry is produced even when later rows in the span survive and carry the
anomaly value. The generator never slides anomalies forward to a later
timestamp.

Specs whose `time_offset` falls outside `[0, total_seconds)` — or whose nearest row index
falls outside `[0, n_rows)` at a coarse `--interval-seconds` — are soft-skipped with a
`WARNING:` line on stderr that names the `--duration-days` required to include them.

Every scenario below has a **slug** that can be passed to `--scenarios` or
`--exclude-scenarios`. Use `--scenarios all` (default) to include every reachable
scenario, or name specific slugs to narrow or exclude them.

### Scenario catalog

All 29 scenarios are listed below. The **Signal** column shows the minimum
`--signal-level` required (`low`/`medium`/`high`). The **Days** column shows the
minimum `--duration-days` required. The **Duration** column shows how long each
scenario's anomalous behavior lasts in the dataset, derived from
`duration_seconds` on the primary specs in `SCENARIOS`:

- A single value (e.g. `8 min`, `4h`) for a single-span incident, regardless of shape (`step`, `sustained`, `ramp_linear`, `ramp_exp`, `sawtooth`, `sine`).
- A multi-phase summary (e.g. `51h leak + 12h eviction cascade + 5 min restart/cold-start`) for staged incidents, in `time + phase` segments separated by ` + `.
- `instant` for one-sample step injections (`duration_seconds` omitted or `0`), which write exactly one row in the CSV at the matched timestamp (a single sample; at the default `--interval-seconds 1.0` that's 1 Hz).

Cascades are secondary specs within the same scenario that propagate the blast
radius to additional components.

| Slug | Signal | Days | Time / Day | Duration | Components touched | Description |
| ---- | ------ | ---- | ---------- | -------- | ------------------ | ----------- |
| `auth_brute_force` | medium | 1 | 02:15 | instant | `authservice`, `apigateway` | Login brute-force spike — error rate 42%, login surge 1,250/s; cascades to gateway 5xx and session invalidation. |
| `cache_collapse` | medium | 1 | 06:00 | 4h | `cacheservice`, `database` | Cache hit-ratio collapse to 5% + slow memory leak 70%→96%; cascades to DB query spike and read latency. |
| `api_cpu_saturation` | medium | 1 | 06:30 | 14h sustained step + 30 min sawtooth + 8 min retry storm | `apigateway`, `authservice`, `cacheservice` | Gateway CPU saturation (100%) + retry storm — cascades to auth errors and cache errors. |
| `db_stall` | medium | 1 | 00:00 | 24h disk ramp + 6h connection-leak ramp + 20 min brown-out (10 min down + 10 min recovery) | `database`, `apigateway`, `authservice`, `mqservice` | DB disk exhaustion ramp, backup-window connection pile-up, read-latency skyrocket, brown-out, nightly batch; cascades to backend latency, gateway 5xx, auth latency, MQ backpressure. |
| `mq_jam` | medium | 1 | 12:30 | instant | `mqservice`, `apigateway`, `authservice`, `database` | Message queue DLQ blow-up + 1M pending; cascades to slow API response, DB connection buildup, slow writes, auth session write delay. |
| `lb_flapping` | medium | 1 | 03:00 | instant | `loadbalancer`, `apigateway` | TLS cert near-expiry errors 80/s + LB health-check failures; cascades to reduced active connections. |
| `object_store_5xx` | medium | 1 | 07:00 | instant | `objectstore`, `apigateway` | Object store 5xx surge (14%) + bandwidth saturation (950 Mbps); cascades to gateway 5xx. |
| `vectorstore_pressure` | medium | 1 | 10:30 | instant | `vectorstore`, `llm_analytics` | Vector store index rebuild stall (280 ms), recall degrades to 0.62; cascades to LLM latency elevation and fallback retry errors. |
| `scheduler_overflow` | medium | 1 | 08:00 | instant | `scheduler`, `database` | Job overrun 4×, 12 missed schedules, 2,500-job queue overflow; cascades to DB connection buildup. |
| `payment_5xx` | medium | 1 | 12:00 | instant | `paymentservice`, `apigateway` | Stripe-style provider 5xx (18%), webhook lag 5 min, fraud-rule decline-rate spike (35%); cascades to gateway 5xx. |
| `idp_jwks_storm` | medium | 1 | 04:00 | instant | `identityprovider`, `authservice` | JWKS cache-miss storm — fetch latency 1,500 ms, MFA provider degradation; cascades to degraded login success rate. |
| `observability_lag` | medium | 1 | 09:00 | instant | `observabilitypipeline`, `mqservice` | Ingest lag grows to 240s, high-cardinality push drops 8,500 metrics/s; cascades to downstream MQ queue backup. |
| `monday_baseline` | low | 1 | 09:00 | instant | `authservice`, `apigateway` | Benign Monday-morning login burst (1,400/s) + RPS spike (2,200/s). No cascades — low severity baseline shift only. |
| `llm_viral_surge_day2` | medium | 2 | Day 2 10:15 | instant | `llm_analytics`, `apigateway`, `cacheservice`, `database` | Viral LLM surge — 8× request spike to 360/s, token surge 185k/s; cascades to gateway RPS, cache misses, DB query spike and connections. |
| `llm_enterprise_onboarding` | medium | 3 | Day 3 14:00 | instant | `llm_analytics`, `vectorstore`, `cacheservice`, `database` | Enterprise onboarding — request spike to 285/s, large context windows 12,500 tokens, 45 ceiling hits/min; cascades to embedding surge, DB latency, cache memory. |
| `llm_rate_limit_fallout` | medium | 5 | Day 5 09:30 | instant | `llm_analytics`, `apigateway` | Upstream rate-limiting — 18% error rate, latency spikes to 4,200 ms; cascades to gateway error rate ~22%. |
| `llm_weekend_batch` | medium | 6 | Day 6 02:00 | instant | `llm_analytics`, `objectstore`, `cacheservice`, `database` | Weekend batch analytics — 320k tokens/s, context overflow rate 8.5; cascades to object-store bandwidth saturation, DB query/CPU surge, cache hit-ratio drop. |
| `llm_second_viral` | medium | 7 | Day 7 16:45 | instant | `llm_analytics`, `apigateway`, `cacheservice`, `database` | Second viral event — 10× spike to 450/s, 420k tokens/s; cascades to gateway active connections, CPU, DB connections, cache errors. |
| `regional_failover_storm` | **high** | 1 | 05:00 | 5 min | `loadbalancer`, `apigateway`, `authservice`, `database`, `mqservice` | Regional failover — backend 5xx ramps to 220/s over 5 min; cascades to gateway 5xx (~30%), DB connections (~9,000), auth errors (~25%), MQ pending ~500k. |
| `dns_provider_outage` | **high** | 1 | 11:00 | 6 min | `loadbalancer`, `apigateway`, `identityprovider`, `paymentservice` | External DNS provider outage — TLS handshake errors 45/s, backend 5xx 80/s, health check failures 8/s, sustained for 6 min; cascades to OIDC callback failures (~150), payment provider 5xx (~32%), gateway error rate (~28%). Sharp step-up at T0 and step-down at T1. |
| `cache_db_meltdown` | **high** | 1 | 11:30 | 10 min | `cacheservice`, `database`, `llm_analytics`, `apigateway` | Coordinated cache memory saturation (80%→99.5%) + DB read latency (800 ms); cascades to doubled LLM latency and elevated gateway backend latency. |
| `deploy_bad_canary_rollback` | **high** | 1 | 15:00 | 8 min | `apigateway`, `authservice`, `cacheservice`, `database` | Bad canary deploy plateau — gateway error rate 18%, backend latency 480 ms, retry-driven RPS 1,100, sustained 8 min until rollback; cascades to login success (~92%), cold-cache miss spike (~1,200), DB connection pile-up (~5,800). Sharp step-up at T0 and step-down at T1. |
| `llm_provider_outage` | **high** | 1 | 20:00 | 15 min | `llm_analytics`, `apigateway`, `cacheservice` | LLM provider sustained outage — error rate 5%→60%, latency 8,000 ms; cascades to gateway 5xx (~25%) and context cache miss surge (~3,000). |
| `network_partition_az_split` | **high** | 1 | 18:20 | 4 min | `database`, `mqservice`, `apigateway`, `authservice` | Intra-region AZ network partition — DB replication lag 18 s, DB error rate 30%, MQ consumer lag 12,000, unacked messages 4,500, sustained 4 min until heal; cascades to gateway backend latency (~380 ms), auth replica read failures (~22%). Sharp step-up at T0 and step-down at T1. (Shifted from 18:00 to clear `db_stall`'s 18:00–18:20 brown-out on `database.error_rate`.) |
| `gateway_ddos` | **high** | 1 | 16:00 | 10 min | `apigateway`, `authservice`, `database`, `mqservice` | Gateway DDoS-style saturation — 5,000 RPS + CPU 99% for 10 min; cascades to auth latency (~600 ms), DB CPU (~92%), MQ pending (~800k). |
| `storage_layer_pressure` | **high** | 1 | 22:00 | 10 min | `objectstore`, `database`, `apigateway` | Storage layer pressure — PUT latency 60→700 ms + object-store 5xx 25%; cascades to DB write latency (~90 ms) and gateway error rate (~15%). |
| `cache_leak_restart` | medium | 2 | Day 2–4 | 51h leak + 12h eviction cascade + 5 min restart/cold-start | `cacheservice`, `database`, `apigateway`, `mqservice` | Cache memory-leak death march 50%→95% over 51h → forced restart → cold-start cache miss / DB query stampede + brief gateway and MQ pressure. (Full sequence needs `--duration-days 4`; shorter multi-day runs emit the in-range portion with stderr WARNINGs for the tail.) |
| `jwks_rotation_chaos` | medium | 3 | Day 3–5 | 8h JWKS latency + 6h TLS flapping + 6h login degradation + 2h cert-expiry window | `loadbalancer`, `identityprovider`, `authservice`, `apigateway`, `paymentservice`, `cacheservice` | Cert/JWKS rotation chaos — TLS flapping, JWKS latency, login degradation, hard cert expiry spike to 200/s + 800 OIDC failures; cascades across gateway, auth, payments, and cache. (Full sequence needs `--duration-days 5`.) |
| `db_disk_exhaustion` | medium | 2 | Day 2–6 | 96h disk ramp + 12h I/O saturation drift + 20 min emergency log-truncation | `database`, `scheduler`, `observabilitypipeline`, `mqservice`, `apigateway` | DB disk creeps 65%→92% over 96h, write latency 12→90 ms, emergency log-truncation event; cascades to scheduler failures, observability lag, MQ backlog, elevated gateway backend latency. (Full sequence needs `--duration-days 6`.) |

## Tests

Dev dependencies (`pytest`, `numpy`) ship under the `dev` extra.

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest
```

Tests live in `tests/` and write only into `tmp_path` (never `iot_logs/`). The suite
runs full 1-day and 7-day generations end-to-end via `main()`. Composition matrix
coverage for `--scenarios` / `--exclude-scenarios` lives in
`tests/test_scenarios.py` (selector intersection, WARNING content, `--anomaly-count`
interaction); the canonical slug catalog is the [scenario catalog](#scenario-catalog)
table in this file.
