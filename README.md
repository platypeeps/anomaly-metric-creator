# anomaly-metric-creator

`anomaly-metric-creator.py` generates synthetic IoT-style metric logs for a SaaS stack
with built-in anomalies. It writes one CSV per component plus an `anomalies.csv`
manifest that catalogues every anomaly the run injected. Output is deterministic for a
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

# Full week (604,800 rows per component); required to unlock the multi-day
# LLM/cascade anomaly catalog (~46 specs vs ~19 same-day specs).
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

# Cap the total anomaly count across the whole dataset (deterministic for a
# given --seed). Useful for keeping noisy test datasets small or sweeping
# across anomaly density:
python3 anomaly-metric-creator.py --anomaly-count 25

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
| `--duration-days`   | `1`         | Days to generate. Multi-day LLM/cascade specs require `>= 7`.      |
| `--seed`            | `42`        | RNG seed for deterministic output.                                 |
| `--output-dir`      | `iot_logs`  | Directory CSVs are written into (created if missing).              |
| `--drop-rate`       | `0.0005`    | Per-row probability of emitting a blank line (simulated packet loss). |
| `--interval-seconds`| `1.0`       | Seconds between consecutive rows. Sampling-density knob — timeline coverage stays `duration_days * 86400`s and row count is `floor(total_seconds / interval)`. Must be `> 0`. Anomalies map to the nearest row via `round(time_offset / interval)`. |
| `--emit-selection`  | `metrics,logs,traces` | Comma-separated artifact selection. Valid values are `metrics`, `logs`, `traces`; any combination is allowed. `metrics` writes the per-component CSVs and `anomalies.csv`, `logs` writes `metric_report.log`, and `traces` writes `metric_traces.jsonl`. |
| `--components`      | `all`       | Comma-separated component allowlist. Filters CSV emission, `anomalies.csv`, reporting artifacts, and OTEL streaming to only the named components. Use `all` (default) for every component. Allowed names: `apigateway`, `authservice`, `cacheservice`, `database`, `identityprovider`, `llm_analytics`, `loadbalancer`, `mqservice`, `objectstore`, `observabilitypipeline`, `paymentservice`, `scheduler`, `vectorstore`. |
| `--signal-level`    | `medium`    | Anomaly intensity level: `low`, `medium` (default), or `high`. Inclusion hierarchy: `low` only fires specs explicitly tagged `severity="low"` (today: a handful of benign Monday-morning baseline shifts) and intentionally has **no cascade fan-out** because benign baseline shifts do not realistically propagate as failures; `medium` adds the standard catalog plus its cascade fan-out (the default behavior); `high` additionally activates the high-pressure cross-component scenarios (regional failover storm, coordinated cache+DB meltdown, LLM provider outage, gateway DDoS saturation, storage layer pressure) and their cascades. |
| `--anomaly-count`   | _unlimited_ | Optional cap on the total number of injected anomalies (primary specs + cascades) across the whole dataset. Sampling is deterministic for a given `--seed` and uses its own RNG stream so it doesn't perturb the column noise. Applied after `--signal-level` and `--components` filters. Out-of-range specs (e.g. multi-day cascades on a 1-day run) are excluded from the sampling pool. |
| `--combine`         | _off_       | After generation, also write `combined_metrics_unified.csv` into `--output-dir`. |
| `--combine-only`    | _off_       | Skip generation; only run the combine step against an existing `--output-dir`. Mutually exclusive with `--combine`. |
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
| `--otel-stream-protocol` | `protobuf` | OTLP payload mode: `json` (`application/json`) or `protobuf` (`application/x-protobuf`). |
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
- `anomalies.csv` — manifest of every injected anomaly with recovery-aware columns:  
  `timestamp, component, metric, description, step_note, step_group, next_step`.
  - `step_note`: populated with `recovered-missing-step` when an anomaly could not be placed at the requested row due to simulated packet loss and was recovered to the next available row.
  - `step_group` / `next_step`: optional chain metadata that threads multi-step scenarios.
- `metric_report.log` — line-oriented report log aligned 1:1 with anomaly manifest rows via deterministic `event_id`.
- `metric_traces.jsonl` — JSONL traces aligned 1:1 with anomaly manifest rows (`event_id`, `trace_id`, `span_id`, timestamp/component/metric context).
- `combined_metrics_unified.csv` — only when `--combine` / `--combine-only` is passed.

If you omit `--emit-selection`, the default remains the full backward-compatible
set: metrics, logs, and traces.

## Failure modes / anomaly catalog

Anomalies are time-offset injections that overwrite a metric column at a matched
row or span. Optional fields support span realism:

- `duration_seconds` — span length (0/omitted keeps single-row behavior)
- `shape` — `step` (default), `ramp_linear`, `ramp_exp`, `sustained`, `sawtooth`, `sine`
- `shape_params` — shape-specific parameters (for example `start/end`, `period_s`, `amplitude`, `midline`)

Each injected row is emitted to the relevant per-component CSV and catalogued in
`anomalies.csv`. If a requested anomaly row is dropped by the packet-loss mask, the generator
recovers by emitting that anomaly on the next available timestamp for the same component/metric
and marks that manifest row with `step_note=recovered-missing-step`. This preserves follow-up
chain visibility (`next_step`) even under packet-loss scenarios.

Specs whose `time_offset` falls outside `[0, total_seconds)` — or whose nearest row index
falls outside `[0, n_rows)` at a coarse `--interval-seconds` — are soft-skipped with a
`WARNING:` line on stderr that names the `--duration-days` required to include them.

The same-day catalog always fires. The multi-day LLM catalog only fires at
`--duration-days >= 7`.

### Recovery example

If a target anomaly row is dropped, it is replayed to the next available timestamp for that
metric, with `step_note` marking recovery:

`anomalies.csv` line:
`2026-03-10 00:00:02,recovery_component,m0,"forced drop recovery test",recovered-missing-step,,""`

`metric_report.log` line:
`2026-03-10 00:00:02 INFO metric_report event_id=evt_xxxxx component=recovery_component metric=m0 msg="forced drop recovery test" step_note=recovered-missing-step`

### Same-day specs (any `--duration-days`)

| Time | Component | Metric | Shape | Failure Mode |
| --- | --- | --- | --- | --- |
| 00:00 | `database` | `disk_used_pct` | `ramp_linear` | **Disk exhaustion** — 8% → 100% over 24h. |
| 02:15 | `authservice` | `error_rate` | `step` | Login brute force spike (42%). |
| 02:15 | `authservice` | `login_attempts` | `step` | Login surge (1,250 / s). |
| 03:00 | `loadbalancer` | `tls_handshake_errors` | `step` | Cert near-expiry — TLS errors spike to 80/s. |
| 04:00 | `database` | `connections` | `step` | Backup-window connection pile-up — 6,800 connections. |
| 04:00 | `database` | `write_latency_ms` | `step` | Backup I/O contention — writes 45 ms. |
| 04:00 | `identityprovider` | `jwks_fetch_latency_ms` | `step` | JWKS cache miss storm — fetch latency 1500 ms at key rotation. |
| 04:00 | `identityprovider` | `key_rotation_events` | `step` | Concurrent key rotation events triggered cache miss storm. |
| 06:00 | `cacheservice` | `hit_ratio` | `step` | Cache collapse — hit ratio drops to 5%. |
| 06:30 | `apigateway` | `cpu_util_pct` | `step` | CPU saturation (100%). |
| 07:00 | `objectstore` | `5xx_rate` | `step` | Upstream provider 5xx wave — 14%. |
| 08:00 | `cacheservice` | `memory_util_pct` | `ramp_linear` | **Slow memory leak** — 70% → 96% over 4h. |
| 08:00 | `scheduler` | `avg_job_duration_s` | `step` | Job overrun — duration 4× baseline blocks next window. |
| 08:05 | `scheduler` | `missed_schedules` | `step` | Missed schedule chain — 12 windows skipped after overrun. |
| 08:15 | `loadbalancer` | `healthcheck_failures` | `step` | Backend pool flapping — 12 healthcheck failures. |
| 09:00 | `apigateway` | `requests_per_sec` | `step` | Monday-morning thundering herd — 2,200 RPS spike. |
| 09:00 | `authservice` | `login_attempts` | `step` | Benign baseline shift — Monday-morning login burst at 1,400 attempts/s. |
| 09:00 | `observabilitypipeline` | `ingest_lag_s` | `step` | Ingestion lag grows to 240s — pipeline can't keep up. |
| 09:30 | `apigateway` | `avg_response_time_ms` | `sawtooth` | **GC sawtooth** — oscillations 180↔380 ms every 90s for 30m. |
| 10:00 | `apigateway` | `avg_response_time_ms` | `step` | **Deploy regression** — +30% latency (sustained to EOD). |
| 10:00 | `scheduler` | `jobs_queued` | `step` | Job queue overflow — 2,500 jobs backlog. |
| 10:30 | `vectorstore` | `ann_query_latency_ms` | `step` | Index rebuild stall — 280 ms. |
| 11:00 | `database` | `read_latency_ms` | `step` | Read latency skyrockets to 360 ms. |
| 11:00 | `database` | `error_rate` | `step` | Backend errors rise to 23%. |
| 12:00 | `paymentservice` | `provider_5xx_rate` | `step` | Stripe-style provider 5xx surge — 18% error rate. |
| 12:00 | `objectstore` | `bandwidth_mbps` | `step` | Batch export saturates bandwidth — 950 Mbps. |
| 12:30 | `mqservice` | `dead_letter_queue` | `step` | DLQ blow-up — 1,200 messages parked. |
| 13:00 | `loadbalancer` | `connection_resets` | `step` | SYN flood-style burst — 450 resets. |
| 13:00 | `observabilitypipeline` | `dropped_metrics_per_sec` | `step` | High-cardinality push drops 8,500 metrics/s. |
| 13:00 | `observabilitypipeline` | `metrics_ingested_per_sec` | `step` | Ingest rate collapses to 12,000/s during cardinality storm. |
| 13:30 | `paymentservice` | `webhook_delivery_lag_s` | `step` | Webhook delivery 5 min behind — provider backlog. |
| 14:30 | `mqservice` | `pending_messages` | `step` | Message jam — pending messages climb to 1,000,000. |
| 14:30 | `mqservice` | `error_rate` | `step` | Message processing errors (10%). |
| 15:00 | `paymentservice` | `auth_decline_rate` | `step` | Decline-rate jump to 35% — fraud rule misfire. |
| 15:00 | `vectorstore` | `recall_at_10` | `step` | Recall degrades after model swap — 0.62. |
| 16:00 | `database` | `connections` | `ramp_linear` | **Connection pool leak** — 3,000 → 9,500 over 6h. |
| 16:30 | `identityprovider` | `mfa_challenges_per_min` | `step` | MFA SMS provider degradation — challenges drop to 0. |
| 17:00 | `cacheservice` | `memory_util_pct` | `step` | Memory pressure — 97% nearing eviction. |
| 18:00 | `database` | `error_rate` | `ramp_linear` | **Brown-out** — climbs 0.1% → 8% over 10 min. |
| 18:10 | `database` | `error_rate` | `ramp_linear` | **Brown-out recovery** — recovers 8% → 0.1% over 10 min. |
| 18:30 | `objectstore` | `get_latency_ms` | `step` | Read-after-write tail — 380 ms. |
| 19:00 | `apigateway` | `requests_per_sec` | `sustained` | **Retry storm** — sustained 2× baseline for 8 min. |
| 19:00 | `apigateway` | `error_rate` | `ramp_linear` | **Retry storm** — error rate climbs 5% → 30% alongside surge. |
| 19:00 | `identityprovider` | `failed_oidc_flows` | `step` | SAML parse error spike — 120 failed flows from upstream IdP. |
| 20:00 | `observabilitypipeline` | `pipeline_error_rate` | `step` | Pipeline error rate 8% — downstream dashboards go stale. |
| 20:30 | `loadbalancer` | `backend_5xx_per_sec` | `step` | Region failover propagates 5xx — 75/s. |
| 21:45 | `apigateway` | `error_rate` | `step` | 5xx burst from bad config push — 12%. |
| 23:00 | `database` | `queries_per_sec` | `step` | Nightly batch kickoff — 55k QPS. |

### Same-day cascades (any `--duration-days`)

Cascades fire seconds-to-minutes after the triggering anomaly to mimic blast-radius
propagation:

- 02:15:15 — `apigateway.error_rate` rises to 28% (auth brute force → gateway).
- 02:15:30 — `authservice.active_sessions` drops to ~35 (sessions invalidated post-brute-force).
- 06:00:20 — `cacheservice.cache_misses` surges to ~2,400 (miss surge before DB cascade lands).
- 06:00:30 — `database.queries_per_sec` spikes ~38k (cache collapse → DB load).
- 06:00:45 — `database.read_latency_ms` ~45 ms (cache collapse → DB latency).
- 06:30:12 — `authservice.error_rate` ~35% (gateway saturation → auth errors).
- 06:30:18 — `cacheservice.error_rate` ~15% (gateway saturation → cache errors).
- 11:00:00 — `apigateway.backend_latency_ms` ~850 ms (DB stall → backend latency).
- 11:00:05 — `apigateway.error_rate` ~19% (DB errors → gateway).
- 11:00:10 — `authservice.avg_auth_latency_ms` ~420 ms (DB stall → slow auth).
- 11:00:20 — `mqservice.pending_messages` ~250k (DB stall → MQ backpressure).
- 14:31:30 — `apigateway.avg_response_time_ms` ~650 ms (MQ backlog → slow API).
- 14:32:00 — `database.connections` ~8500 (MQ jam → connection buildup).
- 14:32:05 — `database.write_latency_ms` ~85 ms (MQ backpressure → slow writes).
- 14:32:30 — `authservice.avg_auth_latency_ms` ~280 ms (MQ jam delays session writes).
- 07:00:20 — `apigateway.error_rate` ~6% (object store 5xx wave → dependent endpoints).
- 08:15:05 — `apigateway.active_connections` ~200 (LB withdraws flapping pool).
- 10:30:15 — `llm_analytics.avg_llm_latency_ms` ~1,900 ms (slow ANN retrieval).
- 15:00:30 — `llm_analytics.llm_api_error_rate` ~8% (low-recall fallback retries).
- 20:30:10 — `apigateway.error_rate` ~9% (LB region failover propagates 5xx).
- 04:00:25 — `authservice.login_success_rate` ~45% (IdP JWKS storm → auth verification degraded).
- 09:00:20 — `mqservice.pending_messages` ~220,000 (telemetry pipeline lag → downstream queue backup).
- 10:00:30 — `database.connections` ~7,800 (scheduler queue overflow → DB connection buildup).
- 12:00:12 — `apigateway.error_rate` ~15% (payment provider 5xx → gateway).

### High-pressure cross-component scenarios (`--signal-level high`)

These scenarios are gated by `--signal-level high` and produce coordinated
multi-component pressure to exercise blast-radius detection. Their cascades
fan out into 3–4 additional services per scenario.

| Time | Component | Metric | Shape | Failure Mode |
| --- | --- | --- | --- | --- |
| 05:00 | `loadbalancer` | `backend_5xx_per_sec` | `ramp_linear` | **Regional failover storm** — backend 5xx ramps to 220/s over 5 min; cascades to gateway 5xx (~30%), DB connection pile-up (~9,000), auth errors (~25%), MQ pending ~500k. |
| 11:30 | `cacheservice` | `memory_util_pct` | `ramp_linear` | **Cache+DB meltdown** — cache memory saturates 80% → 99.5% over 10 min, paired with DB read latency climbing to 800 ms; cascades double LLM latency and drag gateway backend latency. |
| 11:30 | `database` | `read_latency_ms` | `ramp_linear` | **Cache+DB meltdown** (paired) — DB read latency climbs to 800 ms over 10 min. |
| 16:00 | `apigateway` | `requests_per_sec` | `sustained` | **Gateway DDoS saturation** — sustained 5,000 RPS for 10 min, paired with CPU pinned at 99%; cascades to auth latency ~600 ms, DB CPU ~92%, MQ pending ~800k. |
| 16:00 | `apigateway` | `cpu_util_pct` | `sustained` | **Gateway DDoS saturation** (paired) — CPU pinned at 99% for 10 min. |
| 20:00 | `llm_analytics` | `llm_api_error_rate` | `ramp_linear` | **LLM provider sustained outage** — error rate ramps 5% → 60% over 15 min, paired with latency climbing to 8,000 ms; cascades to gateway error rate ~25% and cache miss surge ~3,000. |
| 20:00 | `llm_analytics` | `avg_llm_latency_ms` | `ramp_linear` | **LLM provider sustained outage** (paired) — latency climbs to 8,000 ms over 15 min. |
| 22:00 | `objectstore` | `put_latency_ms` | `ramp_linear` | **Storage layer pressure** — PUT latency climbs 60 → 700 ms over 10 min, paired with object-store 5xx surge to 25%; cascades to DB write latency ~90 ms and gateway error rate ~15%. |
| 22:00 | `objectstore` | `5xx_rate` | `sustained` | **Storage layer pressure** (paired) — object-store 5xx surge to 25% for 10 min. |

### Multi-day LLM catalog (`--duration-days >= 7`)

| When | Component | Metric | Failure simulated |
| --- | --- | --- | --- |
| Day 2 10:15 | `llm_analytics` | `llm_requests_per_sec` | Viral surge — 8× request spike to 360/s. |
| Day 2 10:15 | `llm_analytics` | `input_tokens_per_sec` | Token surge to 185k/s from viral traffic. |
| Day 2 10:15 | `llm_analytics` | `output_tokens_per_sec` | Output token surge to 62k/s. |
| Day 3 14:00 | `llm_analytics` | `llm_requests_per_sec` | Enterprise onboarding — sustained 285/s. |
| Day 3 14:00 | `llm_analytics` | `avg_context_window_size` | Context window jumps to 12,500 tokens. |
| Day 3 14:00 | `llm_analytics` | `token_limit_hits_per_min` | 45 hits/min — frequent ceiling strikes. |
| Day 3 14:00 | `vectorstore` | `embeddings_per_sec` | Enterprise onboarding drives embeddings to 350/s. |
| Day 5 09:30 | `llm_analytics` | `llm_api_error_rate` | Upstream rate-limited — 18% errors. |
| Day 5 09:30 | `llm_analytics` | `avg_llm_latency_ms` | Latency spikes to 4200 ms under rate limiting. |
| Day 6 02:00 | `llm_analytics` | `input_tokens_per_sec` | Weekend batch analytics — 320k tokens/s. |
| Day 6 02:00 | `llm_analytics` | `context_overflow_rate` | Context overflow rate at 8.5 (large batch docs). |
| Day 6 02:00 | `objectstore` | `bandwidth_mbps` | Weekend batch saturates object store — 1,400 Mbps. |
| Day 7 16:45 | `llm_analytics` | `llm_requests_per_sec` | Second viral event — 10× spike to 450/s. |
| Day 7 16:45 | `llm_analytics` | `input_tokens_per_sec` | Massive 420k tokens/s under social traffic. |
| Day 7 16:45 | `llm_analytics` | `output_tokens_per_sec` | Output tokens surge to 135k/s. |

### Multi-day cascades (`--duration-days >= 7`)

- Day 2 10:15 — viral LLM surge propagates: `apigateway.requests_per_sec` ~2400,
  `cacheservice.cache_misses` ~1800, `database.queries_per_sec` ~48k,
  `database.connections` ~7200.
- Day 3 14:00 — enterprise onboarding pressure: `database.read_latency_ms` ~85 ms,
  `cacheservice.memory_util_pct` ~92%.
- Day 5 09:30 — LLM rate-limit fallout: `apigateway.error_rate` ~22%.
- Day 6 02:00 — weekend batch: `database.queries_per_sec` ~65k,
  `database.cpu_util_pct` ~94%, `cacheservice.hit_ratio` ~22%.
- Day 7 16:45 — second viral event blast radius: `apigateway.active_connections` ~4800,
  `apigateway.cpu_util_pct` ~87%, `database.connections` ~9800,
  `cacheservice.error_rate` ~31%.

## Tests

Dev dependencies (`pytest`, `numpy`) ship under the `dev` extra.

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest
```

Tests live in `tests/` and write only into `tmp_path` (never `iot_logs/`). The suite
runs full 1-day and 7-day generations end-to-end via `main()`.
