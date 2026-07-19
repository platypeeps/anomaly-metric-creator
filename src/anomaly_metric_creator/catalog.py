"""Component and instance catalogs for anomaly-metric-creator."""

from __future__ import annotations

import math
from typing import Callable

try:
    import numpy as np
except ModuleNotFoundError as exc:
    if exc.name not in {None, "numpy"}:
        raise
    raise SystemExit(
        "Missing required dependency: numpy\n"
        "Install this project into the Python you are using, for example:\n"
        "  python3 -m pip install -e .\n"
        "or create the documented dev environment:\n"
        "  python3 -m venv .venv\n"
        "  .venv/bin/pip install -e '.[dev]'\n"
    ) from None

from .models_impl import (
    Instance,
    MetricSpec,
    _validate_instance_list,
)

# Local copy keeps the moved ``_daily_sine`` helper independent from
# ``legacy.py`` while preserving its historic formula exactly.
SECONDS_PER_DAY = 86_400

# Vocabulary for ``MetricSpec.semantic_type``. Drives both the ``schema.json``
# emitter and the ``validate`` subcommand's checks (e.g. ``counter`` / ``rate``
# columns must be non-negative). Values map onto the OTLP semantic instrument
# kinds the generator uses elsewhere (``stream_otel_signals`` Sum data points
# for counters, ``stream_otel_gauges`` Gauge data points for gauges).
_VALID_SEMANTIC_TYPES = frozenset({"counter", "gauge", "ratio", "rate"})

# Vocabulary for ``MetricSpec.dtype``. ``int`` here means "values are expected
# to be whole numbers"; ``generate_component`` rounds int-typed columns via
# ``np.rint`` before derivations run. The validator surfaces any remaining
# fractional values as schema violations.
_VALID_DTYPES = frozenset({"float", "int"})

# Shared seasonality / shaping helpers used by COMPONENTS specs
# ------------------------------------------------------------------
def _llm_business_hours(ts, _elapsed):
    """Daily business-hours load multiplier for LLM analytics.

    Works on a single ``datetime.datetime`` (used by tests' natural_band helper)
    and on a ``datetime64`` numpy array (used by the vectorized generator).
    """
    if isinstance(ts, np.ndarray):
        hours = ((ts - ts.astype("datetime64[D]")) // np.timedelta64(1, "h")).astype(np.int64)
        return np.select(
            [(hours >= 8) & (hours < 18), (hours >= 18) & (hours < 22)],
            [1.4, 1.1],
            default=0.6,
        )
    h = ts.hour
    if 8 <= h < 18:
        return 1.4
    if 18 <= h < 22:
        return 1.1
    return 0.6


def _daily_sine(amplitude: float) -> Callable:
    """Additive 24h sine shaped by the elapsed second so the curve has real
    daily seasonality. ``elapsed`` may be a scalar int or a numpy array."""
    def fn(_ts, elapsed):
        return amplitude * np.sin(2 * np.pi * elapsed / SECONDS_PER_DAY)
    return fn


# ------------------------------------------------------------------
# Per-component metric schemas. Add a metric by editing exactly one list.
# ------------------------------------------------------------------
# Each component lists up to ``MAX_METRICS_PER_COMPONENT`` MetricSpecs in
# descending importance. The first ``DEFAULT_METRICS_PER_COMPONENT[component]``
# entries are emitted by default; the remainder are supplemental and emitted
# only when ``--metrics-per-component N`` selects past the default tail.
# Order matters: existing default metrics keep their historic positions to
# preserve byte-for-byte CSV output at default arguments.
COMPONENTS: dict[str, list[MetricSpec]] = {
    "authservice": [
        MetricSpec("active_sessions", 200, additive=_daily_sine(20),
                   unit="sessions", semantic_type="gauge",
                   min_value=0, dtype="int"),
        MetricSpec("login_attempts", 250, 15,
                   unit="attempts/interval", semantic_type="counter",
                   min_value=0, dtype="int"),
        MetricSpec("login_success_rate", 97.0, 0.5,
                   unit="pct", semantic_type="ratio",
                   min_value=0, max_value=100),
        MetricSpec("avg_auth_latency_ms", 110, 5,
                   unit="ms", semantic_type="gauge", min_value=0),
        MetricSpec("cpu_util_pct", 20, 3,
                   unit="pct", semantic_type="ratio",
                   min_value=0, max_value=100),
        MetricSpec("error_rate", 0.2, 0.05, clip_min=0,
                   unit="ratio", semantic_type="ratio",
                   min_value=0, max_value=1),
        # Supplemental metrics
        MetricSpec("avg_session_duration_s", 900, 30, clip_min=0,
                   unit="s", semantic_type="gauge", min_value=0),
        MetricSpec("password_reset_per_min", 3, 1, clip_min=0,
                   unit="events/min", semantic_type="rate",
                   min_value=0, dtype="int"),
        MetricSpec("admin_actions_per_min", 8, 2, clip_min=0,
                   unit="events/min", semantic_type="rate",
                   min_value=0, dtype="int"),
        MetricSpec("memory_util_pct", 45, 4,
                   unit="pct", semantic_type="ratio",
                   min_value=0, max_value=100),
    ],
    "cacheservice": [
        MetricSpec("cache_hits", 5000, 200, clip_min=0,
                   unit="hits/interval", semantic_type="counter",
                   min_value=0, dtype="int"),
        MetricSpec("cache_misses", 200, 20, clip_min=0,
                   unit="misses/interval", semantic_type="counter",
                   min_value=0, dtype="int"),
        MetricSpec("hit_ratio", 95.0, 0.3,
                   unit="pct", semantic_type="ratio",
                   min_value=0, max_value=100,
                   derivation="100 * cache_hits / (cache_hits + cache_misses)"),
        MetricSpec("avg_cache_latency_ms", 15, 1,
                   unit="ms", semantic_type="gauge", min_value=0),
        MetricSpec("memory_util_pct", 70, 5,
                   unit="pct", semantic_type="ratio",
                   min_value=0, max_value=100),
        MetricSpec("error_rate", 0.05, 0.02, clip_min=0,
                   unit="ratio", semantic_type="ratio",
                   min_value=0, max_value=1),
        # Supplemental metrics
        MetricSpec("evictions_per_sec", 8, 3, clip_min=0,
                   unit="events/s", semantic_type="rate", min_value=0),
        MetricSpec("expired_keys_per_sec", 12, 4, clip_min=0,
                   unit="events/s", semantic_type="rate", min_value=0),
        MetricSpec("cpu_util_pct", 15, 3, clip_min=0,
                   unit="pct", semantic_type="ratio",
                   min_value=0, max_value=100),
        MetricSpec("connected_clients", 400, 30, clip_min=0,
                   unit="clients", semantic_type="gauge",
                   min_value=0, dtype="int"),
    ],
    "apigateway": [
        MetricSpec("requests_per_sec", 800, 50,
                   unit="requests/s", semantic_type="rate", min_value=0),
        MetricSpec("avg_response_time_ms", 180, 10,
                   unit="ms", semantic_type="gauge", min_value=0),
        MetricSpec("backend_latency_ms", 90, 8,
                   unit="ms", semantic_type="gauge", min_value=0),
        MetricSpec("active_connections", 1200, 60,
                   unit="connections", semantic_type="gauge",
                   min_value=0, dtype="int"),
        MetricSpec("cpu_util_pct", 22, 4,
                   unit="pct", semantic_type="ratio",
                   min_value=0, max_value=100),
        MetricSpec("error_rate", 0.15, 0.04, clip_min=0,
                   unit="ratio", semantic_type="ratio",
                   min_value=0, max_value=1),
        # Supplemental metrics
        MetricSpec("rate_limited_per_sec", 4, 2, clip_min=0,
                   unit="events/s", semantic_type="rate", min_value=0),
        MetricSpec("tls_handshakes_per_sec", 140, 15, clip_min=0,
                   unit="events/s", semantic_type="rate", min_value=0),
        MetricSpec("memory_util_pct", 55, 4,
                   unit="pct", semantic_type="ratio",
                   min_value=0, max_value=100),
        MetricSpec("upstream_unhealthy_count", 0.2, 0.4, clip_min=0,
                   unit="hosts", semantic_type="gauge",
                   min_value=0, dtype="int"),
    ],
    "database": [
        MetricSpec("connections", 3000, 400,
                   unit="connections", semantic_type="gauge",
                   min_value=0, dtype="int"),
        MetricSpec("read_latency_ms", 10, 2, clip_min=0,
                   unit="ms", semantic_type="gauge", min_value=0),
        MetricSpec("write_latency_ms", 12, 3, clip_min=0,
                   unit="ms", semantic_type="gauge", min_value=0),
        MetricSpec("queries_per_sec", 25000, 2000,
                   unit="queries/s", semantic_type="rate", min_value=0),
        MetricSpec("cpu_util_pct", 18, 3,
                   unit="pct", semantic_type="ratio",
                   min_value=0, max_value=100),
        MetricSpec("error_rate", 0.1, 0.05, clip_min=0,
                   unit="ratio", semantic_type="ratio",
                   min_value=0, max_value=1),
        # disk_used_pct trends slightly upward across the day under natural
        # conditions; the disk-exhaustion ramp anomaly drives it to 100%.
        # ``std=0`` keeps this column out of the shared RNG stream so adding
        # it doesn't shift draws on later components.
        MetricSpec("disk_used_pct", 8.0,
                   additive=lambda _ts, elapsed: 2e-5 * elapsed,
                   clip_min=0,
                   unit="pct", semantic_type="ratio",
                   min_value=0, max_value=100),
        # Supplemental metrics
        MetricSpec("replication_lag_s", 0.4, 0.1, clip_min=0,
                   unit="s", semantic_type="gauge", min_value=0),
        MetricSpec("buffer_cache_hit_ratio", 98.0, 0.3,
                   unit="pct", semantic_type="ratio",
                   min_value=0, max_value=100),
        MetricSpec("deadlocks_per_min", 0.05, 0.05, clip_min=0,
                   unit="events/min", semantic_type="rate", min_value=0),
    ],
    "mqservice": [
        MetricSpec("pending_messages", 45000, 3000,
                   unit="messages", semantic_type="gauge",
                   min_value=0, dtype="int"),
        MetricSpec("processed_messages", 43000, 2500,
                   unit="messages/interval", semantic_type="counter",
                   min_value=0, dtype="int"),
        MetricSpec("avg_latency_ms", 70, 5,
                   unit="ms", semantic_type="gauge", min_value=0),
        MetricSpec("dead_letter_queue", 5, 1, clip_min=0,
                   unit="messages", semantic_type="gauge",
                   min_value=0, dtype="int"),
        MetricSpec("mem_util_pct", 55, 4,
                   unit="pct", semantic_type="ratio",
                   min_value=0, max_value=100),
        MetricSpec("error_rate", 0.08, 0.02, clip_min=0,
                   unit="ratio", semantic_type="ratio",
                   min_value=0, max_value=1),
        # Supplemental metrics
        MetricSpec("publish_rate_per_sec", 4500, 200, clip_min=0,
                   unit="events/s", semantic_type="rate", min_value=0),
        MetricSpec("consumer_lag", 300, 80, clip_min=0,
                   unit="messages", semantic_type="gauge",
                   min_value=0, dtype="int"),
        MetricSpec("unacked_messages", 120, 25, clip_min=0,
                   unit="messages", semantic_type="gauge",
                   min_value=0, dtype="int"),
        MetricSpec("broker_disk_used_pct", 42.0, 2.0,
                   unit="pct", semantic_type="ratio",
                   min_value=0, max_value=100),
    ],
    "llm_analytics": [
        MetricSpec("input_tokens_per_sec", 25000, 2000, multiplier=_llm_business_hours,
                   unit="tokens/s", semantic_type="rate", min_value=0),
        MetricSpec("output_tokens_per_sec", 8000, 800, multiplier=_llm_business_hours,
                   unit="tokens/s", semantic_type="rate", min_value=0),
        MetricSpec("avg_context_window_size", 4500, 500,
                   unit="tokens", semantic_type="gauge", min_value=0),
        MetricSpec("llm_requests_per_sec", 45, 5, multiplier=_llm_business_hours,
                   unit="requests/s", semantic_type="rate", min_value=0),
        MetricSpec("avg_llm_latency_ms", 850, 80,
                   unit="ms", semantic_type="gauge", min_value=0),
        MetricSpec("token_limit_hits_per_min", 2, 0.5,
                   multiplier=_llm_business_hours, clip_min=0,
                   unit="events/min", semantic_type="rate", min_value=0),
        MetricSpec("context_overflow_rate", 0.3, 0.1, clip_min=0,
                   unit="ratio", semantic_type="ratio",
                   min_value=0, max_value=1),
        MetricSpec("llm_api_error_rate", 0.05, 0.02, clip_min=0,
                   unit="ratio", semantic_type="ratio",
                   min_value=0, max_value=1),
        # Supplemental metrics
        MetricSpec("p95_llm_latency_ms", 1400, 80,
                   unit="ms", semantic_type="gauge", min_value=0),
        MetricSpec("prompt_cache_hit_ratio", 55.0, 2.0, clip_min=0,
                   unit="pct", semantic_type="ratio",
                   min_value=0, max_value=100),
    ],
    "loadbalancer": [
        MetricSpec("requests_per_sec", 900, 60,
                   unit="requests/s", semantic_type="rate", min_value=0),
        MetricSpec("healthcheck_failures", 0, 0.1, clip_min=0,
                   unit="events/interval", semantic_type="counter",
                   min_value=0, dtype="int"),
        MetricSpec("active_tls_handshakes", 120, 10,
                   unit="handshakes", semantic_type="gauge",
                   min_value=0, dtype="int"),
        MetricSpec("tls_handshake_errors", 0.5, 0.2, clip_min=0,
                   unit="errors/interval", semantic_type="counter",
                   min_value=0, dtype="int"),
        MetricSpec("backend_5xx_per_sec", 1.5, 0.5, clip_min=0,
                   unit="events/s", semantic_type="rate", min_value=0),
        MetricSpec("connection_resets", 5, 2, clip_min=0,
                   unit="events/interval", semantic_type="counter",
                   min_value=0, dtype="int"),
        MetricSpec("cpu_util_pct", 18, 3,
                   unit="pct", semantic_type="ratio",
                   min_value=0, max_value=100),
        # Supplemental metrics
        MetricSpec("healthy_backends", 12, 0.3,
                   unit="hosts", semantic_type="gauge",
                   min_value=0, dtype="int"),
        MetricSpec("avg_request_duration_ms", 210, 12,
                   unit="ms", semantic_type="gauge", min_value=0),
        MetricSpec("dropped_connections", 0.2, 0.3, clip_min=0,
                   unit="events/interval", semantic_type="counter",
                   min_value=0, dtype="int"),
    ],
    "objectstore": [
        MetricSpec("get_latency_ms", 45, 5,
                   unit="ms", semantic_type="gauge", min_value=0),
        MetricSpec("put_latency_ms", 60, 8,
                   unit="ms", semantic_type="gauge", min_value=0),
        MetricSpec("5xx_rate", 0.1, 0.05, clip_min=0,
                   unit="ratio", semantic_type="ratio",
                   min_value=0, max_value=1),
        MetricSpec("bandwidth_mbps", 180, 20,
                   unit="Mbps", semantic_type="gauge", min_value=0),
        MetricSpec("requests_per_sec", 1200, 80,
                   unit="requests/s", semantic_type="rate", min_value=0),
        # Supplemental metrics
        MetricSpec("p99_get_latency_ms", 140, 10,
                   unit="ms", semantic_type="gauge", min_value=0),
        MetricSpec("avg_object_size_kb", 320, 15, clip_min=0,
                   unit="kB", semantic_type="gauge", min_value=0),
        MetricSpec("error_rate", 0.05, 0.02, clip_min=0,
                   unit="ratio", semantic_type="ratio",
                   min_value=0, max_value=1),
        MetricSpec("throttled_requests_per_sec", 0.3, 0.2, clip_min=0,
                   unit="events/s", semantic_type="rate", min_value=0),
        MetricSpec("multipart_upload_rate", 2.0, 0.5, clip_min=0,
                   unit="events/s", semantic_type="rate", min_value=0),
    ],
    "vectorstore": [
        MetricSpec("ann_query_latency_ms", 25, 4,
                   unit="ms", semantic_type="gauge", min_value=0),
        MetricSpec("embeddings_per_sec", 80, 10, multiplier=_llm_business_hours,
                   unit="embeddings/s", semantic_type="rate", min_value=0),
        MetricSpec("recall_at_10", 0.91, 0.01,
                   unit="ratio", semantic_type="ratio",
                   min_value=0, max_value=1),
        MetricSpec("cache_hit_ratio", 88, 2,
                   unit="pct", semantic_type="ratio",
                   min_value=0, max_value=100),
        MetricSpec("error_rate", 0.1, 0.05, clip_min=0,
                   unit="ratio", semantic_type="ratio",
                   min_value=0, max_value=1),
        # Supplemental metrics. ``std=0`` skips the RNG draw for near-constant
        # metrics so adding them doesn't perturb downstream column noise.
        MetricSpec("index_size_gb", 42.0, 0.0, clip_min=0,
                   unit="GB", semantic_type="gauge", min_value=0),
        MetricSpec("queries_per_sec", 140, 12, multiplier=_llm_business_hours, clip_min=0,
                   unit="queries/s", semantic_type="rate", min_value=0),
        MetricSpec("avg_vector_dim", 1536.0, 0.0,
                   unit="dimensions", semantic_type="gauge",
                   min_value=0, dtype="int"),
        MetricSpec("shard_skew_pct", 3.0, 0.8, clip_min=0,
                   unit="pct", semantic_type="ratio",
                   min_value=0, max_value=100),
        MetricSpec("compaction_lag_s", 2.5, 0.5, clip_min=0,
                   unit="s", semantic_type="gauge", min_value=0),
    ],
    "scheduler": [
        MetricSpec("jobs_running", 20, 3, clip_min=0,
                   unit="jobs", semantic_type="gauge",
                   min_value=0, dtype="int"),
        MetricSpec("jobs_queued", 50, 8, clip_min=0,
                   unit="jobs", semantic_type="gauge",
                   min_value=0, dtype="int"),
        MetricSpec("jobs_failed_per_min", 0.5, 0.15, clip_min=0,
                   unit="events/min", semantic_type="rate", min_value=0),
        MetricSpec("avg_job_duration_s", 120, 12, clip_min=0,
                   unit="s", semantic_type="gauge", min_value=0),
        MetricSpec("missed_schedules", 0.02, 0.05, clip_min=0,
                   unit="events/interval", semantic_type="counter",
                   min_value=0, dtype="int"),
        # Supplemental metrics
        MetricSpec("retries_per_min", 4, 1, clip_min=0,
                   unit="events/min", semantic_type="rate", min_value=0),
        MetricSpec("workers_available", 24, 2, clip_min=0,
                   unit="workers", semantic_type="gauge",
                   min_value=0, dtype="int"),
        MetricSpec("job_throughput_per_min", 140, 10, clip_min=0,
                   unit="jobs/min", semantic_type="rate", min_value=0),
        MetricSpec("queue_age_seconds_p95", 85, 10, clip_min=0,
                   unit="s", semantic_type="gauge", min_value=0),
        MetricSpec("cpu_util_pct", 18, 3,
                   unit="pct", semantic_type="ratio",
                   min_value=0, max_value=100),
    ],
    "paymentservice": [
        MetricSpec("txn_per_sec", 80, 6,
                   multiplier=_llm_business_hours, clip_min=0,
                   unit="transactions/s", semantic_type="rate", min_value=0),
        MetricSpec("provider_5xx_rate", 0.01, 0.005, clip_min=0,
                   unit="ratio", semantic_type="ratio",
                   min_value=0, max_value=1),
        MetricSpec("webhook_delivery_lag_s", 2.0, 0.4, clip_min=0,
                   unit="s", semantic_type="gauge", min_value=0),
        MetricSpec("auth_decline_rate", 0.04, 0.01, clip_min=0,
                   unit="ratio", semantic_type="ratio",
                   min_value=0, max_value=1),
        MetricSpec("avg_txn_latency_ms", 180, 12,
                   unit="ms", semantic_type="gauge", min_value=0),
        # Supplemental metrics
        MetricSpec("chargebacks_per_min", 0.3, 0.1, clip_min=0,
                   unit="events/min", semantic_type="rate", min_value=0),
        MetricSpec("settlement_lag_s", 180, 12, clip_min=0,
                   unit="s", semantic_type="gauge", min_value=0),
        MetricSpec("fraud_score_avg", 0.05, 0.01, clip_min=0,
                   unit="ratio", semantic_type="ratio",
                   min_value=0, max_value=1),
        MetricSpec("retry_rate", 0.02, 0.01, clip_min=0,
                   unit="ratio", semantic_type="ratio",
                   min_value=0, max_value=1),
        MetricSpec("error_rate", 0.08, 0.02, clip_min=0,
                   unit="ratio", semantic_type="ratio",
                   min_value=0, max_value=1),
    ],
    "identityprovider": [
        MetricSpec("token_issuance_per_sec", 150, 12, clip_min=0,
                   unit="tokens/s", semantic_type="rate", min_value=0),
        MetricSpec("jwks_fetch_latency_ms", 25, 3, clip_min=0,
                   unit="ms", semantic_type="gauge", min_value=0),
        MetricSpec("mfa_challenges_per_min", 20, 4,
                   multiplier=_llm_business_hours, clip_min=0,
                   unit="events/min", semantic_type="rate", min_value=0),
        MetricSpec("failed_oidc_flows", 2, 0.6, clip_min=0,
                   unit="events/interval", semantic_type="counter",
                   min_value=0, dtype="int"),
        MetricSpec("key_rotation_events", 0.0, 0.0, clip_min=0,
                   unit="events/interval", semantic_type="counter",
                   min_value=0, dtype="int"),
        # Supplemental metrics
        MetricSpec("avg_token_size_bytes", 1200, 40, clip_min=0,
                   unit="bytes", semantic_type="gauge", min_value=0),
        MetricSpec("revoked_tokens_per_min", 1.5, 0.5, clip_min=0,
                   unit="events/min", semantic_type="rate", min_value=0),
        MetricSpec("session_introspection_rate", 22, 3, clip_min=0,
                   unit="events/s", semantic_type="rate", min_value=0),
        MetricSpec("password_reset_rate", 0.5, 0.2, clip_min=0,
                   unit="events/s", semantic_type="rate", min_value=0),
        MetricSpec("error_rate", 0.04, 0.02, clip_min=0,
                   unit="ratio", semantic_type="ratio",
                   min_value=0, max_value=1),
    ],
    # Self-referential: when this degrades, every other component's telemetry
    # becomes suspect — anomalies fire on the pipeline itself.
    "observabilitypipeline": [
        MetricSpec("metrics_ingested_per_sec", 50000, 2500, clip_min=0,
                   unit="metrics/s", semantic_type="rate", min_value=0),
        MetricSpec("dropped_metrics_per_sec", 5, 1.5, clip_min=0,
                   unit="metrics/s", semantic_type="rate", min_value=0),
        MetricSpec("ingest_lag_s", 1.0, 0.2, clip_min=0,
                   unit="s", semantic_type="gauge", min_value=0),
        MetricSpec("pipeline_error_rate", 0.001, 0.0005, clip_min=0,
                   unit="ratio", semantic_type="ratio",
                   min_value=0, max_value=1),
        # Supplemental metrics
        MetricSpec("cardinality_count", 120000, 4000, clip_min=0,
                   unit="series", semantic_type="gauge",
                   min_value=0, dtype="int"),
        MetricSpec("retention_hours", 72.0, 0.0, clip_min=0,
                   unit="h", semantic_type="gauge", min_value=0),
        MetricSpec("compactions_per_min", 1.5, 0.5, clip_min=0,
                   unit="events/min", semantic_type="rate", min_value=0),
        MetricSpec("shard_count", 12.0, 0.0, clip_min=0,
                   unit="shards", semantic_type="gauge",
                   min_value=0, dtype="int"),
        MetricSpec("flush_latency_ms", 22, 3, clip_min=0,
                   unit="ms", semantic_type="gauge", min_value=0),
        MetricSpec("cpu_util_pct", 12, 2,
                   unit="pct", semantic_type="ratio",
                   min_value=0, max_value=100),
    ],
    "gpu_inference": [
        MetricSpec("batch_size", 10.8, 7.0, clip_min=1,
                   unit="requests", semantic_type="gauge",
                   min_value=1, dtype="int"),
        MetricSpec("model_size_b", 30.0, 14.0, clip_min=7,
                   unit="B parameters", semantic_type="gauge",
                   min_value=0, dtype="int"),
        MetricSpec("gpu_memory_pressure", 0.625, 0.07, clip_min=0,
                   unit="ratio", semantic_type="ratio",
                   min_value=0, max_value=1),
        MetricSpec("kv_cache_usage", 0.835, 0.03, clip_min=0,
                   unit="ratio", semantic_type="ratio",
                   min_value=0, max_value=1),
        MetricSpec("memory_fragmentation", 0.50, 0.08, clip_min=0,
                   unit="ratio", semantic_type="ratio",
                   min_value=0, max_value=1),
        MetricSpec("gpu_utilization", 0.75, 0.04, clip_min=0,
                   unit="ratio", semantic_type="ratio",
                   min_value=0, max_value=1),
        MetricSpec("throughput_tps", 25.4, 10.0, clip_min=0,
                   unit="tokens/s", semantic_type="rate", min_value=0),
        MetricSpec("latency_p50_ms", 109.0, 28.0, clip_min=0,
                   unit="ms", semantic_type="gauge", min_value=0),
        MetricSpec("latency_p99_ms", 383.0, 110.0, clip_min=0,
                   unit="ms", semantic_type="gauge", min_value=0),
        MetricSpec("failure", 0.0, 0.0, clip_min=0,
                   unit="bool", semantic_type="gauge",
                   min_value=0, max_value=1, dtype="int"),
    ],
}

# Maximum metrics any component can expose. Caps both the catalog above and
# the --metrics-per-component CLI flag.
MAX_METRICS_PER_COMPONENT = 10

# Maximum instances any component can fan out to via --instances-per-component.
# Combined with PREFLIGHT_CELL_CAP this prevents accidental memory explosions
# (20 instances * 10 metrics * 86400 rows ~ 17M cells per component).
MAX_INSTANCES_PER_COMPONENT = 20

# Default emitted metrics per component when ``--metrics-per-component`` is
# not provided. Matches the historic catalog so default CSVs remain
# byte-for-byte stable. Keys MUST match COMPONENTS exactly — adding a new
# component requires a new entry here. Drift is rejected at import time by
# the assertion below.
DEFAULT_METRICS_PER_COMPONENT: dict[str, int] = {
    "authservice": 6,
    "cacheservice": 6,
    "apigateway": 6,
    "database": 7,
    "mqservice": 6,
    "llm_analytics": 8,
    "loadbalancer": 7,
    "objectstore": 5,
    "vectorstore": 5,
    "scheduler": 5,
    "paymentservice": 5,
    "identityprovider": 5,
    "observabilitypipeline": 4,
    "gpu_inference": 10,
}

_components_keys = set(COMPONENTS.keys())
_defaults_keys = set(DEFAULT_METRICS_PER_COMPONENT.keys())
if _components_keys != _defaults_keys:
    missing = _components_keys - _defaults_keys
    extra = _defaults_keys - _components_keys
    raise ValueError(
        "DEFAULT_METRICS_PER_COMPONENT and COMPONENTS keys must match. "
        f"Missing from DEFAULT_METRICS_PER_COMPONENT: {sorted(missing)}. "
        f"Extra in DEFAULT_METRICS_PER_COMPONENT: {sorted(extra)}."
    )
_overflowed = {
    name: len(specs)
    for name, specs in COMPONENTS.items()
    if len(specs) > MAX_METRICS_PER_COMPONENT
}
if _overflowed:
    raise ValueError(
        f"COMPONENTS entries exceed MAX_METRICS_PER_COMPONENT={MAX_METRICS_PER_COMPONENT}: "
        f"{_overflowed}. An accidental extra MetricSpec would be unreachable "
        f"via --metrics-per-component; trim the catalog or raise the cap."
    )
for _name, _default in DEFAULT_METRICS_PER_COMPONENT.items():
    if not 1 <= _default <= len(COMPONENTS[_name]):
        raise ValueError(
            f"DEFAULT_METRICS_PER_COMPONENT[{_name!r}] = {_default} is outside "
            f"[1, {len(COMPONENTS[_name])}]"
        )
del _components_keys, _defaults_keys, _overflowed, _name, _default


# Per-component instance topology registry (Phase 1). Default = one
# anonymous ``Instance()`` per component, which keeps the emitted CSVs
# byte-identical to today: ``Instance()`` carries no dimension labels, so
# Phase 2's CSV writer treats the run as "no dimension columns" and falls
# back to today's ``timestamp, m0, m1, ...`` header. Keys MUST match
# ``COMPONENTS`` exactly — drift is rejected at import time by
# ``_validate_instances_registry``.
INSTANCES: dict[str, list["Instance"]] = {
    name: [Instance()] for name in COMPONENTS
}


_DEFAULT_RUNTIME_KEY = "__default__"
_catalog_runtimes = {}


def _configure_catalog_runtime(
    *,
    get_components: Callable[[], dict[str, list[MetricSpec]]],
    get_instances: Callable[[], dict[str, list[Instance]]],
    get_default_metrics_per_component: Callable[[], dict[str, int]],
    runtime_key: str = _DEFAULT_RUNTIME_KEY,
) -> None:
    """Wire live registry access from ``legacy.py`` without importing it."""
    _catalog_runtimes[runtime_key] = {
        "get_components": get_components,
        "get_instances": get_instances,
        "get_default_metrics_per_component": get_default_metrics_per_component,
    }


def _catalog_runtime(runtime_key: str) -> dict | None:
    return _catalog_runtimes.get(runtime_key)


def _runtime_components(runtime_key: str) -> dict[str, list[MetricSpec]]:
    runtime = _catalog_runtime(runtime_key)
    return COMPONENTS if runtime is None else runtime["get_components"]()


def _runtime_instances(runtime_key: str) -> dict[str, list[Instance]]:
    runtime = _catalog_runtime(runtime_key)
    return INSTANCES if runtime is None else runtime["get_instances"]()


def _runtime_default_metrics_per_component(runtime_key: str) -> dict[str, int]:
    runtime = _catalog_runtime(runtime_key)
    if runtime is None:
        return DEFAULT_METRICS_PER_COMPONENT
    return runtime["get_default_metrics_per_component"]()


def _validate_metric_spec_schema_metadata(
    *, runtime_key: str = _DEFAULT_RUNTIME_KEY
) -> None:
    """Import-time invariants for the schema metadata fields on ``MetricSpec``.

    Rejects nonsense vocabulary (unknown ``semantic_type`` / ``dtype``) and
    obvious shape errors (``min_value`` > ``max_value``, non-finite bounds)
    before ``main()`` runs, so ``write_schema_json`` and the validator can
    rely on the declared metadata being consistent. Backfill is incremental:
    a spec with all schema fields left at their defaults is still valid
    (semantic_type is None, dtype defaults to ``float``, bounds default to
    None). Once a field is populated, it must be sensible.
    """
    for component, specs in _runtime_components(runtime_key).items():
        for spec in specs:
            ctx = f"COMPONENTS[{component!r}].{spec.name!r}"
            if spec.semantic_type is not None and spec.semantic_type not in _VALID_SEMANTIC_TYPES:
                raise ValueError(
                    f"{ctx}.semantic_type={spec.semantic_type!r} must be one of "
                    f"{sorted(_VALID_SEMANTIC_TYPES)} or None"
                )
            if spec.dtype not in _VALID_DTYPES:
                raise ValueError(
                    f"{ctx}.dtype={spec.dtype!r} must be one of {sorted(_VALID_DTYPES)}"
                )
            for bound_name, bound in (("min_value", spec.min_value),
                                       ("max_value", spec.max_value)):
                if bound is None:
                    continue
                if isinstance(bound, bool) or not isinstance(bound, (int, float)):
                    raise ValueError(
                        f"{ctx}.{bound_name}={bound!r} must be a finite int or float"
                    )
                if not math.isfinite(bound):
                    raise ValueError(
                        f"{ctx}.{bound_name}={bound!r} must be finite"
                    )
            if (spec.min_value is not None and spec.max_value is not None
                    and spec.min_value > spec.max_value):
                raise ValueError(
                    f"{ctx}.min_value={spec.min_value} > max_value={spec.max_value}"
                )
            if spec.unit is not None and not isinstance(spec.unit, str):
                raise ValueError(
                    f"{ctx}.unit={spec.unit!r} must be a string or None"
                )
            if spec.derivation is not None and not isinstance(spec.derivation, str):
                raise ValueError(
                    f"{ctx}.derivation={spec.derivation!r} must be a string or None"
                )



_validate_metric_spec_schema_metadata()


def _validate_instances_registry(
    *, runtime_key: str = _DEFAULT_RUNTIME_KEY
) -> None:
    """Import-time invariants for ``INSTANCES`` (Phase 1).

    Rejects five classes of drift:

    1. Key drift between ``INSTANCES`` and ``COMPONENTS``: ``main()``
       seeds ``ctx.instances`` via ``{name: list(INSTANCES[name]) for
       name in COMPONENTS}``, so a missing key would raise ``KeyError``
       mid-run on the first generated component. The symmetric case
       (extra ``INSTANCES`` key not in ``COMPONENTS``) would silently
       be ignored. Failing fast at import time surfaces both.
    2. Empty per-component lists: ``generate_component()`` needs at
       least one ``Instance`` to broadcast values into, even the
       anonymous default.
    3. Non-``Instance`` entries in a per-component list (delegated to
       ``_validate_instance_list``).
    4. Non-string (and non-``None``) ``Instance.id`` values (delegated to
       ``_validate_instance_list``).
    5. Duplicate non-None ``id`` within one component's instance list, or
       multiple anonymous ``id=None`` entries (delegated to
       ``_validate_instance_list``).
    """
    components = _runtime_components(runtime_key)
    instances = _runtime_instances(runtime_key)
    known = set(components.keys())
    declared = set(instances.keys())
    if declared != known:
        missing = sorted(known - declared)
        extra = sorted(declared - known)
        raise ValueError(
            "INSTANCES and COMPONENTS keys must match. "
            f"Missing from INSTANCES: {missing}. "
            f"Extra in INSTANCES: {extra}."
        )
    for component, instance_list in instances.items():
        if not instance_list:
            raise ValueError(
                f"INSTANCES[{component!r}] is empty; needs at least one "
                f"Instance (Instance() preserves the dimensionless default)."
            )
        _validate_instance_list(
            instance_list, where=f"INSTANCES[{component!r}]"
        )
