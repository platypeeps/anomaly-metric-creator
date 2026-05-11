# anomaly-metric-creator

`anomaly-metric-creator.py` generates synthetic IoT-style metric logs for a SaaS stack
with built-in anomalies. It writes one CSV per component plus an `anomalies.csv`
manifest that catalogues every anomaly the run injected. Output is deterministic for a
given `--seed`.

By default the script emits **one day** of second-by-second metrics for nine
components: `authservice`, `cacheservice`, `apigateway`, `database`, `mqservice`,
`llm_analytics`, `loadbalancer`, `objectstore`, `vectorstore`. Duration, sampling
interval, drop rate, and output directory are all CLI-configurable.

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
```

### CLI flags

| Flag                | Default     | Notes                                                              |
| ------------------- | ----------- | ------------------------------------------------------------------ |
| `--duration-days`   | `1`         | Days to generate. Multi-day LLM/cascade specs require `>= 7`.      |
| `--seed`            | `42`        | RNG seed for deterministic output.                                 |
| `--output-dir`      | `iot_logs`  | Directory CSVs are written into (created if missing).              |
| `--drop-rate`       | `0.0005`    | Per-row probability of emitting a blank line (simulated packet loss). |
| `--interval-seconds`| `1.0`       | Seconds between consecutive rows. Sampling-density knob — timeline coverage stays `duration_days * 86400`s and row count is `floor(total_seconds / interval)`. Must be `> 0`. Anomalies map to the nearest row via `round(time_offset / interval)`. |
| `--combine`         | _off_       | After generation, also write `combined_metrics_unified.csv` into `--output-dir`. |
| `--combine-only`    | _off_       | Skip generation; only run the combine step against an existing `--output-dir`. Mutually exclusive with `--combine`. |

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
- `anomalies.csv` — manifest of every injected anomaly (component, metric, timestamp, description, value).
- `combined_metrics_unified.csv` — only when `--combine` / `--combine-only` is passed.

## Failure modes / anomaly catalog

Anomalies are time-offset injections that overwrite a single metric column at the matched
row. Each one is emitted to the relevant per-component CSV and catalogued in
`anomalies.csv`. Specs whose `time_offset` falls outside `[0, total_seconds)` — or whose
nearest row index falls outside `[0, n_rows)` at a coarse `--interval-seconds` — are
soft-skipped with a `WARNING:` line on stderr that names the `--duration-days` required
to include them.

The same-day catalog always fires. The multi-day LLM catalog only fires at
`--duration-days >= 7`.

### Same-day specs (any `--duration-days`)

| Component | Time (HH:MM) | Metric | Failure simulated |
| --- | --- | --- | --- |
| `authservice` | 02:15 | `error_rate` | Brute-force login surge — error rate jumps to 42%. |
| `authservice` | 02:15 | `login_attempts` | Login attempts surge 5× to 1250/s. |
| `database` | 04:00 | `connections` | Backup-window connection pile-up — 6,800 connections. |
| `database` | 04:00 | `write_latency_ms` | Backup I/O contention — writes 45 ms. |
| `cacheservice` | 06:00 | `hit_ratio` | Cache collapse — hit ratio drops to 5%. |
| `apigateway` | 06:30 | `cpu_util_pct` | Gateway CPU saturates at 100%. |
| `apigateway` | 09:00 | `requests_per_sec` | Monday-morning thundering herd — 2,200 RPS spike. |
| `authservice` | 09:00 | `login_attempts` | Benign baseline shift — Monday-morning login burst at 1,400 attempts/s. |
| `database` | 11:00 | `read_latency_ms` | Read latency skyrockets to 360 ms. |
| `database` | 11:00 | `error_rate` | Backend errors rise to 23%. |
| `mqservice` | 12:30 | `dead_letter_queue` | DLQ blow-up — 1,200 messages parked. |
| `mqservice` | 14:30 | `pending_messages` | Queue jam — pending messages climb to 1,000,000. |
| `mqservice` | 14:30 | `error_rate` | MQ error rate jumps to 10%. |
| `cacheservice` | 17:00 | `memory_util_pct` | Memory pressure — 97% nearing eviction. |
| `apigateway` | 21:45 | `error_rate` | 5xx burst from bad config push — 12%. |
| `database` | 23:00 | `queries_per_sec` | Nightly batch kickoff — 55k QPS. |
| `loadbalancer` | 03:00 | `tls_handshake_errors` | Cert near-expiry — TLS errors spike to 80/s. |
| `loadbalancer` | 08:15 | `healthcheck_failures` | Backend pool flapping — 12 healthcheck failures. |
| `loadbalancer` | 13:00 | `connection_resets` | SYN flood-style burst — 450 resets. |
| `loadbalancer` | 20:30 | `backend_5xx_per_sec` | Region failover propagates 5xx — 75/s. |
| `objectstore` | 07:00 | `5xx_rate` | Upstream provider 5xx wave — 14%. |
| `objectstore` | 12:00 | `bandwidth_mbps` | Batch export saturates bandwidth — 950 Mbps. |
| `objectstore` | 18:30 | `get_latency_ms` | Read-after-write tail — 380 ms. |
| `vectorstore` | 10:30 | `ann_query_latency_ms` | Index rebuild stall — 280 ms. |
| `vectorstore` | 15:00 | `recall_at_10` | Recall degrades after model swap — 0.62. |

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
