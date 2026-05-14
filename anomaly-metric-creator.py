#!/usr/bin/env python3
"""
Generate IoT-style metric logs for a SaaS stack with built-in anomalies.

Defaults to one day at 1-second resolution. Use ``--duration-days N`` to span
more days; the multi-day LLM viral/cascade catalog only becomes reachable at
``--duration-days >= 7``. Anomaly specs whose ``time_offset`` falls outside the
configured window are skipped with a warning on stderr.
"""

import argparse
import base64
import csv
import datetime
import hashlib
import json
import os
import shlex
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from hashlib import sha1
from pathlib import Path
from typing import Callable

import numpy as np

# ------------------------------------------------------------------
# Configuration
# ------------------------------------------------------------------
START = datetime.datetime(2026, 3, 10, 0, 0, 0)
SECONDS_PER_DAY = 86_400
DEFAULT_DURATION_DAYS = 1
DEFAULT_SEED = 42
DEFAULT_OUTPUT_DIR = Path("iot_logs")
DEFAULT_DROP_RATE = 0.0005
DEFAULT_INTERVAL_SECONDS = 1.0
DEFAULT_OTEL_STREAM_AUTH_SCHEME = "Bearer"
DEFAULT_SIGNAL_LEVEL = "medium"

# Inclusion hierarchy for --signal-level: each level keeps its own severity tier
# plus everything weaker. A spec with no explicit ``severity`` defaults to
# ``medium`` so today's catalog continues to fire under the default level.
SIGNAL_LEVELS: dict[str, set[str]] = {
    "low": {"low"},
    "medium": {"low", "medium"},
    "high": {"low", "medium", "high"},
}
DEFAULT_SEVERITY = "medium"

# Stable named sub-seed for the --anomaly-count sampling RNG. Derived from
# sha256(b"anomaly_count_cap") and fixed at import time so the cap RNG stream
# is decoupled from any other np.random use that shares the same seed.
_ANOMALY_COUNT_CAP_SALT = int.from_bytes(
    hashlib.sha256(b"anomaly_count_cap").digest()[:4], "big"
)

# ------------------------------------------------------------------
# Anomaly registry and cascade tracking (reset on each main() call)
# ------------------------------------------------------------------
anomalies = []
cascading_anomalies = {}  # {component_name: [anomaly_specs]}

# ------------------------------------------------------------------
# Per-metric schema. One MetricSpec per CSV column per component.
# ------------------------------------------------------------------
@dataclass(frozen=True)
class MetricSpec:
    """Config for one synthetic metric column.

    Natural value is ``(base + N(0, std)) * multiplier(ts, sec) + additive(ts, sec)``,
    optionally clipped at ``clip_min``. ``std=0`` skips the RNG draw entirely so
    deterministic series do not perturb the shared numpy random stream.
    """
    name: str
    base: float
    std: float = 0.0
    multiplier: Callable[[datetime.datetime, int], float] | None = None
    additive: Callable[[datetime.datetime, int], float] | None = None
    clip_min: float | None = None


def _natural_column(spec: MetricSpec, ts_array: np.ndarray, elapsed: np.ndarray) -> np.ndarray:
    """Vectorized natural-value column. Multiplier/additive must accept arrays."""
    col = np.full(elapsed.shape, spec.base, dtype=np.float64)
    if spec.std > 0:
        col += np.random.normal(0.0, spec.std, elapsed.shape[0])
    if spec.multiplier is not None:
        col *= spec.multiplier(ts_array, elapsed)
    if spec.additive is not None:
        col += spec.additive(ts_array, elapsed)
    if spec.clip_min is not None:
        np.maximum(col, spec.clip_min, out=col)
    return col


# ------------------------------------------------------------------
# Core generator
# ------------------------------------------------------------------
def generate_component(component_name, specs: list[MetricSpec], anomaly_specs,
                       *, base_dir, total_seconds, drop_rate, interval=1.0,
                       ts_array=None, ts_strings=None, emit_metrics=True,
                       dst_inject_day=0):
    """
    specs: list of MetricSpec (one per CSV column, in column order)
    anomaly_specs: list of {'time_offset': int, 'metric': str, 'description': str, 'generator': fn}

    Vectorized: natural-value math is one numpy op per metric; anomaly overrides
    are masked writes on the column arrays; packet loss is a single boolean mask
    decided up front so a dropped row emits neither a CSV row nor a manifest
    entry. ``ts_array``/``ts_strings`` are optional so callers can share them
    across components (main() does this). The drop mask is drawn per call so
    each component keeps its independent drop pattern.

    ``interval`` controls sampling density (seconds between rows). Timeline
    coverage stays ``total_seconds`` seconds; row count is
    ``floor(total_seconds / interval)``. Each anomaly's ``time_offset`` is
    mapped to the nearest row via ``round(time_offset / interval)``; specs
    that fall outside ``[0, n_rows)`` are skipped with the existing
    stderr warning.
    """
    file_path = base_dir / f"{component_name}.csv"
    fieldnames = [s.name for s in specs]
    n_rows = int(total_seconds // interval)

    # Merge primary anomalies with cascading anomalies
    all_anomalies = list(anomaly_specs)
    if component_name in cascading_anomalies:
        all_anomalies.extend(cascading_anomalies[component_name])

    # Expand every anomaly spec into concrete row overrides. Out-of-range is
    # anything whose full span lies outside ``[0, n_rows)``.
    expanded_overrides: list[tuple[int, dict, float, int]] = []
    out_of_range: list[dict] = []
    for s in all_anomalies:
        if s["time_offset"] < 0:
            out_of_range.append(s)
            continue
        start_idx = int(round(s["time_offset"] / interval))
        duration_seconds = float(s.get("duration_seconds", 0) or 0)
        duration_rows = max(1, int(np.ceil(duration_seconds / interval)))
        span_has_row = False
        for span_idx in range(duration_rows):
            row_idx = start_idx + span_idx
            if 0 <= row_idx < n_rows:
                span_has_row = True
                t_within = span_idx * interval
                expanded_overrides.append((row_idx, s, t_within, span_idx))
        if not span_has_row:
            out_of_range.append(s)
            continue
    if out_of_range:
        max_offset = max(s["time_offset"] for s in out_of_range)
        needed_days = max_offset // SECONDS_PER_DAY + 1
        print(
            f"WARNING: {component_name}: skipping {len(out_of_range)} anomaly spec(s) "
            f"with time_offset outside [0, {total_seconds}). "
            f"Run with --duration-days {needed_days} to include them.",
            file=sys.stderr,
        )

    # Fail loudly on identical (metric, time_offset) specs — the previous
    # ``metric_overrides = {spec["metric"]: spec["generator"] for spec in specs}``
    # silently kept only the last one.
    seen_specs: dict[tuple[str, int], dict] = {}
    duplicates: list[tuple[str, str, int]] = []
    for s in all_anomalies:
        key = (s["metric"], s["time_offset"])
        if key in seen_specs:
            duplicates.append((component_name, s["metric"], s["time_offset"]))
        else:
            seen_specs[key] = s
    if duplicates:
        raise ValueError(
            f"Overlapping anomaly specs (component, metric, time_offset): {duplicates}"
        )

    # Sort all overrides by row index and then by metric. When multiple specs
    # target the same row+metric (e.g. a single-row cascade firing inside a
    # shaped span), they apply in order. Stable sort + all_anomalies order
    # (primary then cascades) ensures the cascade wins.
    sorted_overrides = sorted(
        expanded_overrides,
        key=lambda item: (item[0], item[1]["metric"]),
    )

    if ts_array is None or ts_strings is None:
        ts_array, ts_strings = _build_timestamp_arrays(total_seconds, interval)
    drop_mask = np.random.random(n_rows) < drop_rate

    # Elapsed seconds (not row index) so daily/hourly seasonality generators
    # produce the same wall-clock shape at any sampling interval.
    elapsed = np.arange(n_rows, dtype=np.float64) * interval

    # Natural values: one column array per metric, computed in a single numpy op.
    n_cols = len(specs)
    values = np.empty((n_rows, n_cols), dtype=np.float64)
    for col, spec in enumerate(specs):
        values[:, col] = _natural_column(spec, ts_array, elapsed)

    # Apply anomaly overrides. Skip overrides at dropped rows so manifest and
    # CSV stay coherent: a dropped row has no CSV entry, so it must have no
    # manifest entry either. Sort for a deterministic order of scale/jitter
    # draws within a run.
    name_to_col = {s.name: i for i, s in enumerate(specs)}
    for row_idx, aspec, t_within, span_idx in sorted_overrides:
        if drop_mask[row_idx]:
            continue
        col = name_to_col[aspec["metric"]]
        ts_py = START + datetime.timedelta(seconds=float(row_idx * interval))
        values[row_idx, col] = _resolve_anomaly_value(
            aspec, ts_py, col, t_within, span_idx
        )
        if span_idx == 0:
            anomalies.append({
                "timestamp": str(ts_strings[row_idx]),
                "component": component_name,
                "metric": aspec["metric"],
                "description": aspec["description"],
            })

    np.round(values, 3, out=values)

    keep_mask = ~drop_mask
    kept_ts = ts_strings[keep_mask]
    kept_vals = values[keep_mask]

    # Format values to fixed 3 decimals. ``np.char.mod("%.3f", ...)`` is correct
    # but spends ~80% of the run inside ``_vec_string``. Scaling to int + numpy
    # string ops produces the same output ~2x faster.
    str_vals = _format_fixed3(kept_vals)

    # Assemble each row as ``ts,v0,v1,...,vk`` via vectorized numpy string adds,
    # then join with newlines. Doing the column concat in numpy (C) and only the
    # final newline-join in Python keeps the per-row Python work to one op.
    rows = np.char.add(kept_ts, ",")
    rows = np.char.add(rows, str_vals[:, 0])
    for col in range(1, n_cols):
        rows = np.char.add(rows, ",")
        rows = np.char.add(rows, str_vals[:, col])

    # Fall-DST artifact. Duplicate the 02:00–02:59 wall-clock hour on the
    # configured day so downstream consumers must handle non-monotonic
    # timestamps (a real-world quirk that breaks naive timeseries pipelines).
    if dst_inject_day > 0:
        rows = _splice_dst_artifact(rows, kept_ts, dst_inject_day)

    if emit_metrics:
        with open(file_path, "w", newline="") as f:
            f.write("timestamp," + ",".join(fieldnames) + "\n")
            f.write("\n".join(rows.tolist()))
            f.write("\n")


def _splice_dst_artifact(rows: np.ndarray, kept_ts: np.ndarray,
                         dst_day: int) -> np.ndarray:
    """Duplicate the 02:00–02:59 hour on ``dst_day`` (1-based) inside ``rows``.

    ``rows`` is the formatted ``ts,v0,...,vk`` string array; ``kept_ts`` is the
    matching ``YYYY-MM-DD HH:MM:SS`` timestamps used to locate the window. The
    returned array has 3,600 / interval extra rows for the targeted day. The
    duplicate hour reuses the same timestamp prefix, so the resulting CSV has
    non-monotonic timestamps — the realistic fall-DST quirk.
    """
    day_date = (START + datetime.timedelta(days=dst_day - 1)).strftime("%Y-%m-%d")
    dst_start = f"{day_date} 02:00:00"
    dst_end = f"{day_date} 03:00:00"
    mask = (kept_ts >= dst_start) & (kept_ts < dst_end)
    indices = np.flatnonzero(mask)
    if indices.size == 0:
        return rows
    first = int(indices[0])
    last = int(indices[-1])
    return np.concatenate([rows[:last + 1], rows[first:last + 1], rows[last + 1:]])



def _resolve_anomaly_value(spec: dict, ts: datetime.datetime, col: int,
                           t_within: float, span_idx: int) -> float:
    """Resolve one anomaly value at one row, honoring shape/duration fields."""
    duration_seconds = float(spec.get("duration_seconds", 0) or 0)
    shape = spec.get("shape", "step")
    shape_params = spec.get("shape_params", {}) or {}

    if duration_seconds <= 0 and shape == "step":
        return float(spec["generator"](ts, col))

    if shape in ("step", "sustained"):
        return float(_call_generator_within_span(spec["generator"], ts, col, t_within, span_idx))

    start = shape_params.get("start")
    if start is None:
        start = _call_generator_within_span(spec["generator"], ts, col, 0.0, 0)
    start = float(start)

    if shape == "ramp_linear":
        end = float(shape_params.get("end", start))
        frac = _span_fraction(t_within, duration_seconds)
        return start + (end - start) * frac

    if shape == "ramp_exp":
        end = float(shape_params.get("end", start))
        exponent = float(shape_params.get("exponent", 3.0))
        frac = _span_fraction(t_within, duration_seconds) ** exponent
        return start + (end - start) * frac

    if shape == "sawtooth":
        period = float(shape_params.get("period_s", max(duration_seconds, 1.0)))
        amplitude = float(shape_params.get("amplitude", 0.0))
        midline = float(shape_params.get("midline", start))
        phase = float(shape_params.get("phase_s", 0.0))
        cycle = ((t_within + phase) / max(period, 1e-9)) % 1.0
        return midline - amplitude + (2.0 * amplitude * cycle)

    if shape == "sine":
        period = float(shape_params.get("period_s", max(duration_seconds, 1.0)))
        amplitude = float(shape_params.get("amplitude", 0.0))
        midline = float(shape_params.get("midline", start))
        phase = float(shape_params.get("phase_s", 0.0))
        angle = 2.0 * np.pi * ((t_within + phase) / max(period, 1e-9))
        return midline + amplitude * np.sin(angle)

    raise ValueError(f"Unsupported anomaly shape: {shape}")


def _call_generator_within_span(generator: Callable, ts: datetime.datetime, col: int,
                                t_within: float, span_idx: int):
    """Backwards-compatible generator call with optional span args."""
    try:
        return generator(ts, col, t_within, span_idx)
    except TypeError:
        try:
            return generator(ts, col, t_within)
        except TypeError:
            return generator(ts, col)


def _span_fraction(t_within: float, duration_seconds: float) -> float:
    if duration_seconds <= 0:
        return 1.0
    return min(max(t_within / duration_seconds, 0.0), 1.0)


def _format_fixed3(arr: np.ndarray) -> np.ndarray:
    """Format a float array as ``%.3f``-equivalent strings ~2x faster than
    ``np.char.mod``. Scales to int64, then assembles ``sign + int + '.' + frac``
    via vectorized numpy string ops.
    """
    scaled = np.round(arr * 1000).astype(np.int64)
    sign = np.where(scaled < 0, "-", "")
    absolute = np.abs(scaled)
    int_part = (absolute // 1000).astype("<U16")
    frac_part = np.char.zfill((absolute % 1000).astype("<U3"), 3)
    out = np.char.add(sign, int_part)
    out = np.char.add(out, ".")
    return np.char.add(out, frac_part)


def _build_timestamp_arrays(total_seconds: int, interval: float = 1.0):
    """Pre-compute the shared per-run timestamp arrays (numpy + formatted str).

    Built once per run and reused across all six components — they're identical
    by construction, so re-computing them per component is pure waste.
    Row ``i`` is at ``START + i * interval`` seconds; row count is
    ``floor(total_seconds / interval)``. Strings are rendered at second
    precision regardless of interval (matches the existing CSV format).
    """
    n_rows = int(total_seconds // interval)
    step_us = int(round(interval * 1_000_000))
    ts_array = np.datetime64(START) + np.arange(n_rows) * np.timedelta64(step_us, "us")
    ts_strings = np.char.replace(np.datetime_as_string(ts_array, unit="s"), "T", " ")
    return ts_array, ts_strings

# ------------------------------------------------------------------
# Cascade helper function
# ------------------------------------------------------------------
def register_cascade(target_component, time_offset, metric, description, generator,
                     severity=DEFAULT_SEVERITY):
    """
    Register a cascading anomaly that will affect another component.

    ``severity`` controls --signal-level eligibility. Defaults to ``medium`` so
    today's cascades fire at the default level; pass ``"high"`` for cascades
    that only belong to the high-pressure catalog.
    """
    cascading_anomalies.setdefault(target_component, []).append({
        "time_offset": time_offset,
        "metric": metric,
        "description": description,
        "generator": generator,
        "severity": severity,
    })

# ------------------------------------------------------------------
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
        MetricSpec("active_sessions", 200, additive=_daily_sine(20)),
        MetricSpec("login_attempts", 250, 15),
        MetricSpec("login_success_rate", 97.0, 0.5),
        MetricSpec("avg_auth_latency_ms", 110, 5),
        MetricSpec("cpu_util_pct", 20, 3),
        MetricSpec("error_rate", 0.2, 0.05),
        # Supplemental metrics
        MetricSpec("avg_session_duration_s", 900, 30, clip_min=0),
        MetricSpec("password_reset_per_min", 3, 1, clip_min=0),
        MetricSpec("admin_actions_per_min", 8, 2, clip_min=0),
        MetricSpec("memory_util_pct", 45, 4),
    ],
    "cacheservice": [
        MetricSpec("cache_hits", 5000, 200),
        MetricSpec("cache_misses", 200, 20),
        MetricSpec("hit_ratio", 95.0, 0.3),
        MetricSpec("avg_cache_latency_ms", 15, 1),
        MetricSpec("memory_util_pct", 70, 5),
        MetricSpec("error_rate", 0.05, 0.02),
        # Supplemental metrics
        MetricSpec("evictions_per_sec", 8, 3, clip_min=0),
        MetricSpec("expired_keys_per_sec", 12, 4, clip_min=0),
        MetricSpec("cpu_util_pct", 15, 3),
        MetricSpec("connected_clients", 400, 30, clip_min=0),
    ],
    "apigateway": [
        MetricSpec("requests_per_sec", 800, 50),
        MetricSpec("avg_response_time_ms", 180, 10),
        MetricSpec("backend_latency_ms", 90, 8),
        MetricSpec("active_connections", 1200, 60),
        MetricSpec("cpu_util_pct", 22, 4),
        MetricSpec("error_rate", 0.15, 0.04),
        # Supplemental metrics
        MetricSpec("rate_limited_per_sec", 4, 2, clip_min=0),
        MetricSpec("tls_handshakes_per_sec", 140, 15, clip_min=0),
        MetricSpec("memory_util_pct", 55, 4),
        MetricSpec("upstream_unhealthy_count", 0.2, 0.4, clip_min=0),
    ],
    "database": [
        MetricSpec("connections", 3000, 400),
        MetricSpec("read_latency_ms", 10, 2),
        MetricSpec("write_latency_ms", 12, 3),
        MetricSpec("queries_per_sec", 25000, 2000),
        MetricSpec("cpu_util_pct", 18, 3),
        MetricSpec("error_rate", 0.1, 0.05),
        # disk_used_pct trends slightly upward across the day under natural
        # conditions; the disk-exhaustion ramp anomaly drives it to 100%.
        # ``std=0`` keeps this column out of the shared RNG stream so adding
        # it doesn't shift draws on later components.
        MetricSpec("disk_used_pct", 8.0,
                   additive=lambda _ts, elapsed: 2e-5 * elapsed,
                   clip_min=0),
        # Supplemental metrics
        MetricSpec("replication_lag_s", 0.4, 0.1, clip_min=0),
        MetricSpec("buffer_cache_hit_ratio", 98.0, 0.3),
        MetricSpec("deadlocks_per_min", 0.05, 0.05, clip_min=0),
    ],
    "mqservice": [
        MetricSpec("pending_messages", 45000, 3000),
        MetricSpec("processed_messages", 43000, 2500),
        MetricSpec("avg_latency_ms", 70, 5),
        MetricSpec("dead_letter_queue", 5, 1),
        MetricSpec("mem_util_pct", 55, 4),
        MetricSpec("error_rate", 0.08, 0.02),
        # Supplemental metrics
        MetricSpec("publish_rate_per_sec", 4500, 200, clip_min=0),
        MetricSpec("consumer_lag", 300, 80, clip_min=0),
        MetricSpec("unacked_messages", 120, 25, clip_min=0),
        MetricSpec("broker_disk_used_pct", 42.0, 2.0),
    ],
    "llm_analytics": [
        MetricSpec("input_tokens_per_sec", 25000, 2000, multiplier=_llm_business_hours),
        MetricSpec("output_tokens_per_sec", 8000, 800, multiplier=_llm_business_hours),
        MetricSpec("avg_context_window_size", 4500, 500),
        MetricSpec("llm_requests_per_sec", 45, 5, multiplier=_llm_business_hours),
        MetricSpec("avg_llm_latency_ms", 850, 80),
        MetricSpec("token_limit_hits_per_min", 2, 0.5,
                   multiplier=_llm_business_hours, clip_min=0),
        MetricSpec("context_overflow_rate", 0.3, 0.1, clip_min=0),
        MetricSpec("llm_api_error_rate", 0.05, 0.02, clip_min=0),
        # Supplemental metrics
        MetricSpec("p95_llm_latency_ms", 1400, 80),
        MetricSpec("prompt_cache_hit_ratio", 0.55, 0.05, clip_min=0),
    ],
    "loadbalancer": [
        MetricSpec("requests_per_sec", 900, 60),
        MetricSpec("healthcheck_failures", 0, 0.1, clip_min=0),
        MetricSpec("active_tls_handshakes", 120, 10),
        MetricSpec("tls_handshake_errors", 0.5, 0.2, clip_min=0),
        MetricSpec("backend_5xx_per_sec", 1.5, 0.5, clip_min=0),
        MetricSpec("connection_resets", 5, 2, clip_min=0),
        MetricSpec("cpu_util_pct", 18, 3),
        # Supplemental metrics
        MetricSpec("healthy_backends", 12, 0.3),
        MetricSpec("avg_request_duration_ms", 210, 12),
        MetricSpec("dropped_connections", 0.2, 0.3, clip_min=0),
    ],
    "objectstore": [
        MetricSpec("get_latency_ms", 45, 5),
        MetricSpec("put_latency_ms", 60, 8),
        MetricSpec("5xx_rate", 0.1, 0.05, clip_min=0),
        MetricSpec("bandwidth_mbps", 180, 20),
        MetricSpec("requests_per_sec", 1200, 80),
        # Supplemental metrics
        MetricSpec("p99_get_latency_ms", 140, 10),
        MetricSpec("avg_object_size_kb", 320, 15, clip_min=0),
        MetricSpec("error_rate", 0.05, 0.02, clip_min=0),
        MetricSpec("throttled_requests_per_sec", 0.3, 0.2, clip_min=0),
        MetricSpec("multipart_upload_rate", 2.0, 0.5, clip_min=0),
    ],
    "vectorstore": [
        MetricSpec("ann_query_latency_ms", 25, 4),
        MetricSpec("embeddings_per_sec", 80, 10, multiplier=_llm_business_hours),
        MetricSpec("recall_at_10", 0.91, 0.01),
        MetricSpec("cache_hit_ratio", 88, 2),
        MetricSpec("error_rate", 0.1, 0.05, clip_min=0),
        # Supplemental metrics. ``std=0`` skips the RNG draw for near-constant
        # metrics so adding them doesn't perturb downstream column noise.
        MetricSpec("index_size_gb", 42.0, 0.0, clip_min=0),
        MetricSpec("queries_per_sec", 140, 12, multiplier=_llm_business_hours, clip_min=0),
        MetricSpec("avg_vector_dim", 1536.0, 0.0),
        MetricSpec("shard_skew_pct", 3.0, 0.8, clip_min=0),
        MetricSpec("compaction_lag_s", 2.5, 0.5, clip_min=0),
    ],
    "scheduler": [
        MetricSpec("jobs_running", 20, 3, clip_min=0),
        MetricSpec("jobs_queued", 50, 8, clip_min=0),
        MetricSpec("jobs_failed_per_min", 0.5, 0.15, clip_min=0),
        MetricSpec("avg_job_duration_s", 120, 12, clip_min=0),
        MetricSpec("missed_schedules", 0.02, 0.05, clip_min=0),
        # Supplemental metrics
        MetricSpec("retries_per_min", 4, 1, clip_min=0),
        MetricSpec("workers_available", 24, 2, clip_min=0),
        MetricSpec("job_throughput_per_min", 140, 10, clip_min=0),
        MetricSpec("queue_age_seconds_p95", 85, 10, clip_min=0),
        MetricSpec("cpu_util_pct", 18, 3),
    ],
    "paymentservice": [
        MetricSpec("txn_per_sec", 80, 6,
                   multiplier=_llm_business_hours, clip_min=0),
        MetricSpec("provider_5xx_rate", 0.01, 0.005, clip_min=0),
        MetricSpec("webhook_delivery_lag_s", 2.0, 0.4, clip_min=0),
        MetricSpec("auth_decline_rate", 0.04, 0.01, clip_min=0),
        MetricSpec("avg_txn_latency_ms", 180, 12),
        # Supplemental metrics
        MetricSpec("chargebacks_per_min", 0.3, 0.1, clip_min=0),
        MetricSpec("settlement_lag_s", 180, 12, clip_min=0),
        MetricSpec("fraud_score_avg", 0.05, 0.01, clip_min=0),
        MetricSpec("retry_rate", 0.02, 0.01, clip_min=0),
        MetricSpec("error_rate", 0.08, 0.02, clip_min=0),
    ],
    "identityprovider": [
        MetricSpec("token_issuance_per_sec", 150, 12, clip_min=0),
        MetricSpec("jwks_fetch_latency_ms", 25, 3, clip_min=0),
        MetricSpec("mfa_challenges_per_min", 20, 4,
                   multiplier=_llm_business_hours, clip_min=0),
        MetricSpec("failed_oidc_flows", 2, 0.6, clip_min=0),
        MetricSpec("key_rotation_events", 0.0, 0.0, clip_min=0),
        # Supplemental metrics
        MetricSpec("avg_token_size_bytes", 1200, 40, clip_min=0),
        MetricSpec("revoked_tokens_per_min", 1.5, 0.5, clip_min=0),
        MetricSpec("session_introspection_rate", 22, 3, clip_min=0),
        MetricSpec("password_reset_rate", 0.5, 0.2, clip_min=0),
        MetricSpec("error_rate", 0.04, 0.02, clip_min=0),
    ],
    # Self-referential: when this degrades, every other component's telemetry
    # becomes suspect — anomalies fire on the pipeline itself.
    "observabilitypipeline": [
        MetricSpec("metrics_ingested_per_sec", 50000, 2500, clip_min=0),
        MetricSpec("dropped_metrics_per_sec", 5, 1.5, clip_min=0),
        MetricSpec("ingest_lag_s", 1.0, 0.2, clip_min=0),
        MetricSpec("pipeline_error_rate", 0.001, 0.0005, clip_min=0),
        # Supplemental metrics
        MetricSpec("cardinality_count", 120000, 4000, clip_min=0),
        MetricSpec("retention_hours", 72.0, 0.0, clip_min=0),
        MetricSpec("compactions_per_min", 1.5, 0.5, clip_min=0),
        MetricSpec("shard_count", 12.0, 0.0, clip_min=0),
        MetricSpec("flush_latency_ms", 22, 3, clip_min=0),
        MetricSpec("cpu_util_pct", 12, 2),
    ],
}

# Maximum metrics any component can expose. Caps both the catalog above and
# the --metrics-per-component CLI flag.
MAX_METRICS_PER_COMPONENT = 10

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

# ------------------------------------------------------------------
# Anomaly specifications
# ------------------------------------------------------------------
anoms_auth = [
    {
        "time_offset": 2*3600 + 15*60,            # 02:15:00
        "metric": "error_rate",
        "description": "Spike in failed logins – possible brute force",
        "generator": lambda ts,idx: 0.42   # 42 % error
    },
    {
        "time_offset": 2*3600 + 15*60,
        "metric": "login_attempts",
        "description": "Login attempts surge 5×",
        "generator": lambda ts,idx: 1250
    },
    {
        "time_offset": 9*3600,                    # 09:00:00
        "metric": "login_attempts",
        "description": "Benign baseline shift: Monday morning login burst — 1,400 attempts/s",
        "generator": lambda ts,idx: 1400,
        "severity": "low",
    }
]

anoms_cache = [
    {
        "time_offset": 6*3600,          # 04:30:00
        "metric": "hit_ratio",
        "description": "Cache hit ratio drops to 5 %",
        "generator": lambda ts,idx: 5.0
    },
    {
        "time_offset": 17*3600,                   # 17:00:00
        "metric": "memory_util_pct",
        "description": "Memory pressure — 97% nearing eviction",
        "generator": lambda ts,idx: 97.0
    },
    # Slow memory leak — linear ramp 70% → 96% over 4h, then snap-back to
    # natural baseline (no explicit reset spec; the span ends and the natural
    # column resumes at row 12:00:00).
    {
        "time_offset": 8*3600,                    # 08:00:00
        "duration_seconds": 4*3600,               # 4h ramp
        "shape": "ramp_linear",
        "shape_params": {"start": 70.0, "end": 96.0},
        "metric": "memory_util_pct",
        "description": "Slow memory leak — utilization ramps 70% → 96% over 4h",
        "generator": lambda ts,idx: 70.0,         # match start_value for test_correctness
    },
]

anoms_api = [
    {
        "time_offset": 6*3600 + 30*60,  # 06:30:00
        "metric": "cpu_util_pct",
        "description": "CPU saturates at 100 %",
        "generator": lambda ts,idx: 100.0
    },
    {
        "time_offset": 9*3600,                    # 09:00:00
        "metric": "requests_per_sec",
        "description": "Monday-morning thundering herd — 2,200 RPS spike",
        "generator": lambda ts,idx: 2200,
        "severity": "low",
    },
    {
        "time_offset": 21*3600 + 45*60,           # 21:45:00
        "metric": "error_rate",
        "description": "5xx burst from bad config push — 12 %",
        "generator": lambda ts,idx: 0.12
    },
    # GC sawtooth — avg_response_time_ms oscillates 180 ↔ 380 every 90s for
    # 30 min, mimicking stop-the-world pauses on a leaky JVM-style workload.
    {
        "time_offset": 9*3600 + 30*60,            # 09:30:00
        "duration_seconds": 30*60,                # 30 min
        "shape": "sawtooth",
        "shape_params": {"period_s": 90, "amplitude": 100, "midline": 280},
        "metric": "avg_response_time_ms",
        "description": "GC sawtooth — response time oscillates 180↔380 ms every 90s for 30 min",
        "generator": lambda ts,idx: 180.0,        # midline - amplitude
    },
    # Deploy regression — step shift +30% (180 → 234 ms) at 10:00, sustained
    # to end of day. The existing 14:31:30 MQ-cascade single-row override
    # still fires inside the span (sort order applies the step first, then
    # the cascade overwrites that one row).
    {
        "time_offset": 10*3600,                   # 10:00:00
        "duration_seconds": 14*3600,              # 10:00 → 24:00
        "shape": "step",
        "metric": "avg_response_time_ms",
        "description": "Deploy regression — avg_response_time_ms step +30% to 234 ms (sustained)",
        "generator": lambda ts,idx: 234.0,
    },
    # Retry storm — requests_per_sec sustained 2× baseline for 8 min, with a
    # co-spec on error_rate climbing in parallel as retries amplify transient
    # failures.
    {
        "time_offset": 19*3600,                   # 19:00:00
        "duration_seconds": 8*60,                 # 8 min
        "shape": "sustained",
        "metric": "requests_per_sec",
        "description": "Retry storm — requests_per_sec sustained 2× baseline for 8 min",
        "generator": lambda ts,idx: 1600,
    },
    {
        "time_offset": 19*3600,                   # 19:00:00 (same span)
        "duration_seconds": 8*60,
        "shape": "ramp_linear",
        "shape_params": {"start": 0.05, "end": 0.30},
        "metric": "error_rate",
        "description": "Retry storm — error_rate climbs 5% → 30% as retries amplify failures",
        "generator": lambda ts,idx: 0.05,
    },
]

anoms_db = [
    {
        "time_offset": 11*3600,           # 11:00:00
        "metric": "read_latency_ms",
        "description": "Read latency skyrockets to 360 ms",
        "generator": lambda ts,idx: 360.0
    },
    {
        "time_offset": 11*3600,
        "metric": "error_rate",
        "description": "Backend errors rise 23 %",
        "generator": lambda ts,idx: 0.23
    },
    {
        "time_offset": 4*3600,                    # 04:00:00
        "metric": "connections",
        "description": "Backup-window connection pile-up — 6,800 connections",
        "generator": lambda ts,idx: 6800
    },
    {
        "time_offset": 4*3600,                    # 04:00:00
        "metric": "write_latency_ms",
        "description": "Backup I/O contention — writes 45 ms",
        "generator": lambda ts,idx: 45.0
    },
    {
        "time_offset": 23*3600,                   # 23:00:00
        "metric": "queries_per_sec",
        "description": "Nightly batch kickoff — 55k QPS",
        "generator": lambda ts,idx: 55000
    },
    # Disk exhaustion — monotonic 24h climb on the disk_used_pct column.
    # Starts at the natural baseline (~8) and ramps to 100% by EOD.
    {
        "time_offset": 0,
        "duration_seconds": SECONDS_PER_DAY,
        "shape": "ramp_linear",
        "shape_params": {"start": 8.0, "end": 100.0},
        "metric": "disk_used_pct",
        "description": "Disk exhaustion — disk_used_pct ramps 8% → 100% over 24h",
        "generator": lambda ts,idx: 8.0,
    },
    # Connection pool leak — connections ramp 3,000 → 9,500 over 6h. Slot
    # 16:00–22:00 keeps the span clear of the existing 14:32 MQ-cascade
    # single-row override on database.connections.
    {
        "time_offset": 16*3600,                   # 16:00:00
        "duration_seconds": 6*3600,
        "shape": "ramp_linear",
        "shape_params": {"start": 3000.0, "end": 9500.0},
        "metric": "connections",
        "description": "Connection pool leak — connections ramp 3,000 → 9,500 over 6h",
        "generator": lambda ts,idx: 3000.0,
    },
    # Brown-out — error_rate ramps 0.1% → 8% over 10 min, then back down over
    # 10 min. Two ramp specs implement the triangle profile and the snap-back
    # is implicit (span ends, natural baseline resumes). No cascade.
    {
        "time_offset": 18*3600,                   # 18:00:00 — climb phase
        "duration_seconds": 10*60,
        "shape": "ramp_linear",
        "shape_params": {"start": 0.001, "end": 0.08},
        "metric": "error_rate",
        "description": "Brown-out — error_rate ramps 0.1% → 8% over 10 min",
        "generator": lambda ts,idx: 0.08,
    },
    {
        "time_offset": 18*3600 + 10*60,           # 18:10:00 — recovery phase
        "duration_seconds": 10*60,
        "shape": "ramp_linear",
        "shape_params": {"start": 0.08, "end": 0.001},
        "metric": "error_rate",
        "description": "Brown-out — error_rate recovers 8% → 0.1% over 10 min",
        "generator": lambda ts,idx: 0.08,
    },
]

anoms_mq = [
    {
        "time_offset": 14*3600 + 30*60,   # 14:30:00
        "metric": "pending_messages",
        "description": "Pending messages jam to 1 M",
        "generator": lambda ts,idx: 1_000_000
    },
    {
        "time_offset": 14*3600 + 30*60,
        "metric": "error_rate",
        "description": "Error rate jumps to 10 %",
        "generator": lambda ts,idx: 0.10
    },
    {
        "time_offset": 12*3600 + 30*60,           # 12:30:00
        "metric": "dead_letter_queue",
        "description": "DLQ blow-up — 1,200 messages parked",
        "generator": lambda ts,idx: 1200
    }
]

anoms_lb = [
    {
        "time_offset": 3*3600,                    # 03:00:00
        "metric": "tls_handshake_errors",
        "description": "TLS handshake errors surge to 80/s (cert near-expiry warning)",
        "generator": lambda ts,idx: 80.0,
    },
    {
        "time_offset": 8*3600 + 15*60,            # 08:15:00
        "metric": "healthcheck_failures",
        "description": "Healthcheck failures jump to 12 (backend pool flapping)",
        "generator": lambda ts,idx: 12.0,
    },
    {
        "time_offset": 13*3600,                   # 13:00:00
        "metric": "connection_resets",
        "description": "Connection resets spike to 450 (SYN flood-style burst)",
        "generator": lambda ts,idx: 450.0,
    },
    {
        "time_offset": 20*3600 + 30*60,           # 20:30:00
        "metric": "backend_5xx_per_sec",
        "description": "Backend 5xx jump to 75/s (region failover cascades 5xx upstream)",
        "generator": lambda ts,idx: 75.0,
    },
]

anoms_obj = [
    {
        "time_offset": 7*3600,                    # 07:00:00
        "metric": "5xx_rate",
        "description": "Object store 5xx rate spikes to 14 % (upstream provider 5xx wave)",
        "generator": lambda ts,idx: 0.14,
    },
    {
        "time_offset": 12*3600,                   # 12:00:00
        "metric": "bandwidth_mbps",
        "description": "Bandwidth saturates at 950 Mbps (batch export)",
        "generator": lambda ts,idx: 950.0,
    },
    {
        "time_offset": 18*3600 + 30*60,           # 18:30:00
        "metric": "get_latency_ms",
        "description": "GET latency tail at 380 ms (read-after-write)",
        "generator": lambda ts,idx: 380.0,
    },
    # Multi-day: ties to LLM weekend batch on Day 6 02:00 (requires --duration-days >= 7)
    {
        "time_offset": 5*SECONDS_PER_DAY + 2*3600,
        "metric": "bandwidth_mbps",
        "description": "Weekend batch export saturates object store at 1400 Mbps",
        "generator": lambda ts,idx: 1400.0,
    },
]

anoms_vec = [
    {
        "time_offset": 10*3600 + 30*60,           # 10:30:00
        "metric": "ann_query_latency_ms",
        "description": "ANN query latency stalls at 280 ms (index rebuild)",
        "generator": lambda ts,idx: 280.0,
    },
    {
        "time_offset": 15*3600,                   # 15:00:00
        "metric": "recall_at_10",
        "description": "Recall@10 degrades to 0.62 after model swap",
        "generator": lambda ts,idx: 0.62,
    },
    # Multi-day: ties to enterprise onboarding Day 3 14:00 (requires --duration-days >= 3)
    {
        "time_offset": 2*SECONDS_PER_DAY + 14*3600,
        "metric": "embeddings_per_sec",
        "description": "Enterprise onboarding drives embeddings to 350/s",
        "generator": lambda ts,idx: 350.0,
    },
]

anoms_scheduler = [
    {
        "time_offset": 8*3600,                    # 08:00:00
        "metric": "avg_job_duration_s",
        "description": "Job overrun — duration 4× baseline blocks next window",
        "generator": lambda ts,idx: 480.0,
    },
    {
        "time_offset": 8*3600 + 5*60,             # 08:05:00
        "metric": "missed_schedules",
        "description": "Missed schedule chain — 12 windows skipped after overrun",
        "generator": lambda ts,idx: 12.0,
    },
    {
        "time_offset": 10*3600,                   # 10:00:00
        "metric": "jobs_queued",
        "description": "Job queue overflow — 2,500 jobs backlog",
        "generator": lambda ts,idx: 2500.0,
    },
]

anoms_payment = [
    {
        "time_offset": 12*3600,                   # 12:00:00
        "metric": "provider_5xx_rate",
        "description": "Stripe-style provider 5xx surge — 18% error rate",
        "generator": lambda ts,idx: 0.18,
    },
    {
        "time_offset": 13*3600 + 30*60,           # 13:30:00
        "metric": "webhook_delivery_lag_s",
        "description": "Webhook delivery 5 min behind — provider backlog",
        "generator": lambda ts,idx: 300.0,
    },
    {
        "time_offset": 15*3600,                   # 15:00:00
        "metric": "auth_decline_rate",
        "description": "Decline-rate jump to 35% — fraud rule misfire",
        "generator": lambda ts,idx: 0.35,
    },
]

anoms_idp = [
    {
        "time_offset": 4*3600,                    # 04:00:00
        "metric": "jwks_fetch_latency_ms",
        "description": "JWKS cache miss storm — fetch latency 1500 ms at key rotation",
        "generator": lambda ts,idx: 1500.0,
    },
    {
        "time_offset": 4*3600,                    # 04:00:00
        "metric": "key_rotation_events",
        "description": "Concurrent key rotation events triggered cache miss storm",
        "generator": lambda ts,idx: 50.0,
    },
    {
        "time_offset": 16*3600 + 30*60,           # 16:30:00
        "metric": "mfa_challenges_per_min",
        "description": "MFA SMS provider degradation — challenges drop to 0",
        "generator": lambda ts,idx: 0.0,
    },
    {
        "time_offset": 19*3600,                   # 19:00:00
        "metric": "failed_oidc_flows",
        "description": "SAML parse error spike — 120 failed flows from upstream IdP",
        "generator": lambda ts,idx: 120.0,
    },
]

anoms_obs = [
    {
        "time_offset": 9*3600,                    # 09:00:00
        "metric": "ingest_lag_s",
        "description": "Ingestion lag grows to 240s — pipeline can't keep up",
        "generator": lambda ts,idx: 240.0,
    },
    {
        "time_offset": 13*3600,                   # 13:00:00
        "metric": "dropped_metrics_per_sec",
        "description": "High-cardinality push drops 8,500 metrics/s",
        "generator": lambda ts,idx: 8500.0,
    },
    {
        "time_offset": 13*3600,                   # 13:00:00
        "metric": "metrics_ingested_per_sec",
        "description": "Ingest rate collapses to 12,000/s during cardinality storm",
        "generator": lambda ts,idx: 12000.0,
    },
    {
        "time_offset": 20*3600,                   # 20:00:00
        "metric": "pipeline_error_rate",
        "description": "Pipeline error rate 8% — downstream dashboards go stale",
        "generator": lambda ts,idx: 0.08,
    },
]

# ------------------------------------------------------------------
# High-pressure cross-component scenarios. Only fire at
# --signal-level high. Each scenario has a triggering anomaly (or
# coordinated pair) plus its own cascade fan-out registered in
# register_high_pressure_cascades(). They are deliberately placed
# off-peak from the medium catalog to keep both layers legible.
# ------------------------------------------------------------------
anoms_high_lb = [
    # Regional failover storm — load balancer sees a sustained 5xx surge for
    # 5 minutes as traffic spills to a degraded region.
    {
        "time_offset": 5*3600,                    # 05:00:00
        "duration_seconds": 5*60,
        "shape": "ramp_linear",
        "shape_params": {"start": 1.5, "end": 220.0},
        "metric": "backend_5xx_per_sec",
        "description": "Regional failover storm — backend 5xx ramps to 220/s over 5 min",
        "generator": lambda ts,idx: 1.5,
        "severity": "high",
    },
]

anoms_high_cache = [
    # Coordinated cache+DB meltdown — cache memory saturates while DB read
    # latency simultaneously climbs (10-minute high-pressure window).
    {
        "time_offset": 11*3600 + 30*60,           # 11:30:00
        "duration_seconds": 10*60,
        "shape": "ramp_linear",
        "shape_params": {"start": 80.0, "end": 99.5},
        "metric": "memory_util_pct",
        "description": "Cache+DB meltdown — cache memory saturates 80% → 99.5% over 10 min",
        "generator": lambda ts,idx: 80.0,
        "severity": "high",
    },
]

anoms_high_db = [
    {
        "time_offset": 11*3600 + 30*60,           # 11:30:00 — paired with cache spec
        "duration_seconds": 10*60,
        "shape": "ramp_linear",
        "shape_params": {"start": 12.0, "end": 800.0},
        "metric": "read_latency_ms",
        "description": "Cache+DB meltdown — DB read latency climbs to 800 ms over 10 min",
        "generator": lambda ts,idx: 12.0,
        "severity": "high",
    },
]

anoms_high_llm = [
    # LLM provider sustained outage — error rate climbs to 60% over 15 min.
    {
        "time_offset": 20*3600,                   # 20:00:00
        "duration_seconds": 15*60,
        "shape": "ramp_linear",
        "shape_params": {"start": 0.05, "end": 0.60},
        "metric": "llm_api_error_rate",
        "description": "LLM provider sustained outage — error rate ramps 5% → 60% over 15 min",
        "generator": lambda ts,idx: 0.05,
        "severity": "high",
    },
    {
        "time_offset": 20*3600,                   # paired latency surge
        "duration_seconds": 15*60,
        "shape": "ramp_linear",
        "shape_params": {"start": 900.0, "end": 8000.0},
        "metric": "avg_llm_latency_ms",
        "description": "LLM provider sustained outage — latency climbs to 8,000 ms over 15 min",
        "generator": lambda ts,idx: 900.0,
        "severity": "high",
    },
]

anoms_high_api = [
    # Gateway DDoS-style saturation — sustained 5x traffic burst for 10 min,
    # CPU pinned at 99%. Drives cascades into auth, DB, and MQ.
    {
        "time_offset": 16*3600,                   # 16:00:00
        "duration_seconds": 10*60,
        "shape": "sustained",
        "metric": "requests_per_sec",
        "description": "Gateway DDoS saturation — requests sustained at 5,000/s for 10 min",
        "generator": lambda ts,idx: 5000,
        "severity": "high",
    },
    {
        "time_offset": 16*3600,
        "duration_seconds": 10*60,
        "shape": "sustained",
        "metric": "cpu_util_pct",
        "description": "Gateway DDoS saturation — CPU pinned at 99% for 10 min",
        "generator": lambda ts,idx: 99.0,
        "severity": "high",
    },
]

anoms_high_obj = [
    # Storage layer pressure — PUT latency climbs and 5xx surge.
    {
        "time_offset": 22*3600,                   # 22:00:00
        "duration_seconds": 10*60,
        "shape": "ramp_linear",
        "shape_params": {"start": 60.0, "end": 700.0},
        "metric": "put_latency_ms",
        "description": "Storage layer pressure — PUT latency climbs 60 → 700 ms over 10 min",
        "generator": lambda ts,idx: 60.0,
        "severity": "high",
    },
    {
        "time_offset": 22*3600,
        "duration_seconds": 10*60,                # matches paired put_latency_ms ramp
        "shape": "sustained",
        "metric": "5xx_rate",
        "description": "Storage layer pressure — object store 5xx surge to 25% for 10 min",
        "generator": lambda ts,idx: 0.25,
        "severity": "high",
    },
]


# Multi-day LLM catalog. Unreachable at --duration-days 1; needs >= 7.
anoms_llm = [
    # Day 1 - Initial viral surge
    {
        "time_offset": 1*SECONDS_PER_DAY + 10*3600 + 15*60,  # Day 2, 10:15:00
        "metric": "llm_requests_per_sec",
        "description": "Viral surge: Customer demo goes viral, 8× request spike",
        "generator": lambda ts,idx: 360
    },
    {
        "time_offset": 1*SECONDS_PER_DAY + 10*3600 + 15*60,
        "metric": "input_tokens_per_sec",
        "description": "Token surge from viral traffic",
        "generator": lambda ts,idx: 185000
    },
    {
        "time_offset": 1*SECONDS_PER_DAY + 10*3600 + 15*60,
        "metric": "output_tokens_per_sec",
        "description": "Output token surge from viral traffic",
        "generator": lambda ts,idx: 62000
    },
    # Day 2 - Enterprise customer onboarding
    {
        "time_offset": 2*SECONDS_PER_DAY + 14*3600,  # Day 3, 14:00:00
        "metric": "llm_requests_per_sec",
        "description": "Enterprise onboarding: Major customer launches AI features",
        "generator": lambda ts,idx: 285
    },
    {
        "time_offset": 2*SECONDS_PER_DAY + 14*3600,
        "metric": "avg_context_window_size",
        "description": "Enterprise using large context windows for analytics",
        "generator": lambda ts,idx: 12500
    },
    {
        "time_offset": 2*SECONDS_PER_DAY + 14*3600,
        "metric": "token_limit_hits_per_min",
        "description": "Token limits hit frequently during enterprise rollout",
        "generator": lambda ts,idx: 45
    },
    # Day 4 - API rate limit issues
    {
        "time_offset": 4*SECONDS_PER_DAY + 9*3600 + 30*60,  # Day 5, 09:30:00
        "metric": "llm_api_error_rate",
        "description": "LLM provider rate limits hit, 18% error rate",
        "generator": lambda ts,idx: 0.18
    },
    {
        "time_offset": 4*SECONDS_PER_DAY + 9*3600 + 30*60,
        "metric": "avg_llm_latency_ms",
        "description": "LLM latency spikes due to rate limiting",
        "generator": lambda ts,idx: 4200
    },
    # Day 5 - Weekend batch processing surge
    {
        "time_offset": 5*SECONDS_PER_DAY + 2*3600,  # Day 6, 02:00:00 (weekend batch job)
        "metric": "input_tokens_per_sec",
        "description": "Weekend batch analytics job processing historical data",
        "generator": lambda ts,idx: 320000
    },
    {
        "time_offset": 5*SECONDS_PER_DAY + 2*3600,
        "metric": "context_overflow_rate",
        "description": "Context overflow from large batch documents",
        "generator": lambda ts,idx: 8.5
    },
    # Day 6 - Second viral event
    {
        "time_offset": 6*SECONDS_PER_DAY + 16*3600 + 45*60,  # Day 7, 16:45:00
        "metric": "llm_requests_per_sec",
        "description": "Social media mention drives 10× traffic spike",
        "generator": lambda ts,idx: 450
    },
    {
        "time_offset": 6*SECONDS_PER_DAY + 16*3600 + 45*60,
        "metric": "input_tokens_per_sec",
        "description": "Massive token usage from social traffic",
        "generator": lambda ts,idx: 420000
    },
    {
        "time_offset": 6*SECONDS_PER_DAY + 16*3600 + 45*60,
        "metric": "output_tokens_per_sec",
        "description": "Output tokens surge from viral event",
        "generator": lambda ts,idx: 135000
    }
]

# ------------------------------------------------------------------
# Cascading-failure registry. Same-day cascades fire under any duration;
# multi-day cascades (LLM-driven) only reach during runs of >= 7 days.
# ------------------------------------------------------------------
def register_default_cascades():
    # Auth service brute force → API Gateway sees more errors
    register_cascade("apigateway",
                     2*3600 + 15*60 + 15,
                     "error_rate",
                     "Cascading: Auth failures cause API gateway errors",
                     lambda ts, idx: 0.28)

    # Brute-force forces session invalidation
    register_cascade("authservice",
                     2*3600 + 15*60 + 30,
                     "active_sessions",
                     "Cascading: Sessions invalidated after brute-force detection",
                     lambda ts, idx: 35)

    # Cache failure → preceding miss surge before DB load lands
    register_cascade("cacheservice",
                     6*3600 + 20,
                     "cache_misses",
                     "Cascading: Cache miss surge before DB cascade lands",
                     lambda ts, idx: 2400 + np.random.normal(0, 150))

    # Cache failure → Database sees increased load
    register_cascade("database",
                     6*3600 + 30,
                     "queries_per_sec",
                     "Cascading: Cache misses increase database queries",
                     lambda ts, idx: 38000 + np.random.normal(0, 3000))

    register_cascade("database",
                     6*3600 + 45,
                     "read_latency_ms",
                     "Cascading: Database read latency increases from cache misses",
                     lambda ts, idx: 45 + np.random.normal(0, 5))

    # API Gateway CPU saturation → cascades to multiple services
    register_cascade("authservice",
                     6*3600 + 30*60 + 12,
                     "error_rate",
                     "Cascading: API gateway overload causes auth errors",
                     lambda ts, idx: 0.35)

    register_cascade("cacheservice",
                     6*3600 + 30*60 + 18,
                     "error_rate",
                     "Cascading: API gateway overload causes cache errors",
                     lambda ts, idx: 0.15)

    # Database failure → cascades to API and Auth
    register_cascade("apigateway",
                     11*3600,
                     "backend_latency_ms",
                     "Cascading: Database latency affects API backend",
                     lambda ts, idx: 850 + np.random.normal(0, 50))

    register_cascade("apigateway",
                     11*3600 + 5,
                     "error_rate",
                     "Cascading: Database errors propagate to API",
                     lambda ts, idx: 0.19)

    register_cascade("authservice",
                     11*3600 + 10,
                     "avg_auth_latency_ms",
                     "Cascading: Database issues slow auth queries",
                     lambda ts, idx: 420 + np.random.normal(0, 30))

    # DB stall → MQ backpressure
    register_cascade("mqservice",
                     11*3600 + 20,
                     "pending_messages",
                     "Cascading: DB stall causes MQ backpressure",
                     lambda ts, idx: 250000 + np.random.normal(0, 5000))

    # MQ service jam → cascades to API and Database
    register_cascade("apigateway",
                     14*3600 + 30*60 + 60,
                     "avg_response_time_ms",
                     "Cascading: MQ backlog delays API responses",
                     lambda ts, idx: 650 + np.random.normal(0, 40))

    register_cascade("database",
                     14*3600 + 30*60 + 90,
                     "connections",
                     "Cascading: MQ issues cause connection buildup",
                     lambda ts, idx: 8500 + np.random.normal(0, 500))

    register_cascade("database",
                     14*3600 + 30*60 + 95,
                     "write_latency_ms",
                     "Cascading: MQ backpressure increases write latency",
                     lambda ts, idx: 85 + np.random.normal(0, 10))

    register_cascade("authservice",
                     14*3600 + 32*60 + 30,
                     "avg_auth_latency_ms",
                     "Cascading: MQ jam delays session writes",
                     lambda ts, idx: 280 + np.random.normal(0, 15))

    # LLM viral surge → cascades to database and cache (multi-day)
    register_cascade("database",
                     1*SECONDS_PER_DAY + 10*3600 + 15*60 + 30,
                     "queries_per_sec",
                     "Cascading: LLM surge increases database queries for context retrieval",
                     lambda ts, idx: 48000 + np.random.normal(0, 4000))

    register_cascade("database",
                     1*SECONDS_PER_DAY + 10*3600 + 15*60 + 45,
                     "connections",
                     "Cascading: LLM service creates more database connections",
                     lambda ts, idx: 7200 + np.random.normal(0, 400))

    register_cascade("cacheservice",
                     1*SECONDS_PER_DAY + 10*3600 + 15*60 + 20,
                     "cache_misses",
                     "Cascading: LLM context cache misses spike",
                     lambda ts, idx: 1800 + np.random.normal(0, 150))

    register_cascade("apigateway",
                     1*SECONDS_PER_DAY + 10*3600 + 15*60 + 10,
                     "requests_per_sec",
                     "Cascading: LLM viral traffic increases API gateway load",
                     lambda ts, idx: 2400 + np.random.normal(0, 200))

    # Enterprise onboarding → database pressure (multi-day)
    register_cascade("database",
                     2*SECONDS_PER_DAY + 14*3600 + 60,
                     "read_latency_ms",
                     "Cascading: Large LLM context windows cause slow DB reads",
                     lambda ts, idx: 85 + np.random.normal(0, 8))

    register_cascade("cacheservice",
                     2*SECONDS_PER_DAY + 14*3600 + 35,
                     "memory_util_pct",
                     "Cascading: LLM context caching increases memory pressure",
                     lambda ts, idx: 92 + np.random.normal(0, 3))

    # LLM rate limit issues → API gateway sees errors (multi-day)
    register_cascade("apigateway",
                     4*SECONDS_PER_DAY + 9*3600 + 30*60 + 8,
                     "error_rate",
                     "Cascading: LLM API errors propagate to gateway",
                     lambda ts, idx: 0.22)

    # Weekend batch job → multiple service impact (multi-day)
    register_cascade("database",
                     5*SECONDS_PER_DAY + 2*3600 + 15,
                     "queries_per_sec",
                     "Cascading: Batch LLM processing hammers database",
                     lambda ts, idx: 65000 + np.random.normal(0, 5000))

    register_cascade("database",
                     5*SECONDS_PER_DAY + 2*3600 + 120,
                     "cpu_util_pct",
                     "Cascading: Database CPU saturates from batch analytics",
                     lambda ts, idx: 94 + np.random.normal(0, 2))

    register_cascade("cacheservice",
                     5*SECONDS_PER_DAY + 2*3600 + 45,
                     "hit_ratio",
                     "Cascading: Batch job overwhelms cache with cold data",
                     lambda ts, idx: 22.0 + np.random.normal(0, 3))

    # Second viral event → system-wide impact (multi-day)
    register_cascade("apigateway",
                     6*SECONDS_PER_DAY + 16*3600 + 45*60 + 5,
                     "active_connections",
                     "Cascading: Viral LLM traffic maxes out connections",
                     lambda ts, idx: 4800 + np.random.normal(0, 200))

    register_cascade("apigateway",
                     6*SECONDS_PER_DAY + 16*3600 + 45*60 + 15,
                     "cpu_util_pct",
                     "Cascading: API gateway CPU spikes from LLM traffic",
                     lambda ts, idx: 87 + np.random.normal(0, 4))

    register_cascade("database",
                     6*SECONDS_PER_DAY + 16*3600 + 45*60 + 25,
                     "connections",
                     "Cascading: Database connection pool exhausted by LLM load",
                     lambda ts, idx: 9800 + np.random.normal(0, 500))

    register_cascade("cacheservice",
                     6*SECONDS_PER_DAY + 16*3600 + 45*60 + 18,
                     "error_rate",
                     "Cascading: Cache service errors under LLM traffic",
                     lambda ts, idx: 0.31)

    # Load balancer cascades
    register_cascade("apigateway",
                     8*3600 + 15*60 + 5,
                     "active_connections",
                     "Cascading: LB withdraws traffic from a flapping backend pool",
                     lambda ts, idx: 200 + np.random.normal(0, 25))

    register_cascade("apigateway",
                     20*3600 + 30*60 + 10,
                     "error_rate",
                     "Cascading: LB region failover propagates 5xx to gateway",
                     lambda ts, idx: 0.09)

    # Object store cascades
    register_cascade("apigateway",
                     7*3600 + 20,
                     "error_rate",
                     "Cascading: object store 5xx wave breaks dependent endpoints",
                     lambda ts, idx: 0.06)

    # Vector store cascades — feed into llm_analytics latency/errors
    register_cascade("llm_analytics",
                     10*3600 + 30*60 + 15,
                     "avg_llm_latency_ms",
                     "Cascading: slow ANN retrieval drags LLM latency to 1,900 ms",
                     lambda ts, idx: 1900 + np.random.normal(0, 80))

    register_cascade("llm_analytics",
                     15*3600 + 30,
                     "llm_api_error_rate",
                     "Cascading: low-recall results trigger LLM fallback retries (8 % errors)",
                     lambda ts, idx: 0.08)

    # Scheduler queue overflow → database connection pressure (jobs pull on DB)
    register_cascade("database",
                     10*3600 + 30,
                     "connections",
                     "Cascading: Scheduler queue overflow drives DB connection buildup",
                     lambda ts, idx: 7800 + np.random.normal(0, 400))

    # Paymentservice 5xx surge → apigateway error rate (payment proxied via API)
    register_cascade("apigateway",
                     12*3600 + 12,
                     "error_rate",
                     "Cascading: Payment provider 5xx propagates to gateway",
                     lambda ts, idx: 0.15)

    # Identityprovider JWKS storm → authservice login success rate dips
    register_cascade("authservice",
                     4*3600 + 25,
                     "login_success_rate",
                     "Cascading: JWKS fetch storm degrades auth verification — success ~45%",
                     lambda ts, idx: 45 + np.random.normal(0, 2))

    # Observabilitypipeline ingest lag → mqservice pending message backup
    register_cascade("mqservice",
                     9*3600 + 20,
                     "pending_messages",
                     "Cascading: Telemetry pipeline lag backs up downstream queue",
                     lambda ts, idx: 220000 + np.random.normal(0, 15000))


def register_high_pressure_cascades():
    """High-severity cascade fan-out for the cross-component scenarios.

    Only registered when --signal-level high. Each cascade fires shortly after
    its triggering scenario and is tagged ``severity="high"`` so it survives
    severity filtering only at the highest level.
    """
    # Regional failover storm (loadbalancer 05:00) → API gateway 5xx, DB
    # connection pressure, auth verification failures, MQ backpressure.
    register_cascade("apigateway",
                     5*3600 + 30,
                     "error_rate",
                     "Cascading: Regional failover floods gateway with 5xx (30%)",
                     lambda ts, idx: 0.30,
                     severity="high")
    register_cascade("database",
                     5*3600 + 45,
                     "connections",
                     "Cascading: Regional failover pile-up — DB connections climb to ~9,000",
                     lambda ts, idx: 9000 + np.random.normal(0, 250),
                     severity="high")
    register_cascade("authservice",
                     5*3600 + 60,
                     "error_rate",
                     "Cascading: Regional failover propagates auth errors (~25%)",
                     lambda ts, idx: 0.25,
                     severity="high")
    register_cascade("mqservice",
                     5*3600 + 90,
                     "pending_messages",
                     "Cascading: Regional failover backs up queue — ~500,000 pending",
                     lambda ts, idx: 500000 + np.random.normal(0, 12000),
                     severity="high")

    # Cache+DB meltdown (11:30) → LLM latency doubles, gateway backend latency.
    register_cascade("llm_analytics",
                     11*3600 + 30*60 + 30,
                     "avg_llm_latency_ms",
                     "Cascading: Cache+DB meltdown doubles LLM latency to ~1,700 ms",
                     lambda ts, idx: 1700 + np.random.normal(0, 90),
                     severity="high")
    register_cascade("apigateway",
                     11*3600 + 30*60 + 45,
                     "backend_latency_ms",
                     "Cascading: Cache+DB meltdown drags gateway backend latency to ~950 ms",
                     lambda ts, idx: 950 + np.random.normal(0, 60),
                     severity="high")

    # LLM provider outage (20:00) → API gateway error rate, cache miss surge.
    register_cascade("apigateway",
                     20*3600 + 15,
                     "error_rate",
                     "Cascading: LLM outage propagates to gateway (~25%)",
                     lambda ts, idx: 0.25,
                     severity="high")
    register_cascade("cacheservice",
                     20*3600 + 30,
                     "cache_misses",
                     "Cascading: LLM outage drives context cache miss surge (~3,000)",
                     lambda ts, idx: 3000 + np.random.normal(0, 200),
                     severity="high")

    # Gateway DDoS saturation (16:00) → auth latency, DB CPU, MQ backpressure.
    register_cascade("authservice",
                     16*3600 + 60,
                     "avg_auth_latency_ms",
                     "Cascading: Gateway saturation slows auth path to ~600 ms",
                     lambda ts, idx: 600 + np.random.normal(0, 25),
                     severity="high")
    register_cascade("database",
                     16*3600 + 90,
                     "cpu_util_pct",
                     "Cascading: Gateway saturation drives DB CPU to ~92%",
                     lambda ts, idx: 92 + np.random.normal(0, 2),
                     severity="high")
    register_cascade("mqservice",
                     16*3600 + 120,
                     "pending_messages",
                     "Cascading: Gateway saturation queues messages — ~800,000 pending",
                     lambda ts, idx: 800000 + np.random.normal(0, 15000),
                     severity="high")

    # Storage layer pressure (22:00) → DB write latency, gateway 5xx.
    register_cascade("database",
                     22*3600 + 30,
                     "write_latency_ms",
                     "Cascading: Storage pressure drags DB write latency to ~90 ms",
                     lambda ts, idx: 90 + np.random.normal(0, 6),
                     severity="high")
    register_cascade("apigateway",
                     22*3600 + 45,
                     "error_rate",
                     "Cascading: Storage 5xx surge propagates to gateway (~15%)",
                     lambda ts, idx: 0.15,
                     severity="high")

def _resolve_effective_specs(metrics_per_component: int | None) -> dict[str, list[MetricSpec]]:
    """Return ``{component: specs[:limit]}`` for the active --metrics-per-component.

    When ``metrics_per_component`` is None, each component is trimmed to its
    historic ``DEFAULT_METRICS_PER_COMPONENT`` count so default CSV output
    stays byte-for-byte identical. When provided, every component is trimmed
    to the same N (capped to its catalog size).
    """
    resolved: dict[str, list[MetricSpec]] = {}
    for name, specs in COMPONENTS.items():
        if metrics_per_component is None:
            limit = DEFAULT_METRICS_PER_COMPONENT[name]
        else:
            limit = min(metrics_per_component, len(specs))
        resolved[name] = specs[:limit]
    return resolved


def _filter_anomalies_for_emitted_metrics(component_anomalies: dict,
                                           cascade_registry: dict,
                                           effective_specs: dict) -> None:
    """Drop anomaly specs whose metric was trimmed by ``--metrics-per-component``.

    Two distinct cases are handled differently:

    - Metric is in the full ``COMPONENTS[component]`` catalog but not in the
      trimmed ``effective_specs[component]`` prefix → silently dropped. This
      is the intended behavior of the cap.
    - Metric (or component) is not in the full catalog at all → raise
      ``ValueError``. This is a typo in an ``anoms_*`` list or a
      ``register_cascade`` call and would otherwise be silently swallowed.

    Filtering happens in-place before the severity / count gates so the
    anomaly-count cap pool reflects what can actually emit.
    """
    full_catalog = {name: {s.name for s in specs}
                    for name, specs in COMPONENTS.items()}
    emitted = {name: {s.name for s in specs}
               for name, specs in effective_specs.items()}

    def _validate_and_filter(specs: list[dict], component: str) -> list[dict]:
        unknown: list[tuple[str, str, str]] = []
        catalog = full_catalog.get(component, set())
        emitted_for_component = emitted.get(component, set())
        kept: list[dict] = []
        for spec in specs:
            metric = spec["metric"]
            if metric not in catalog:
                unknown.append((component, metric, spec.get("description", "")))
                continue
            if metric in emitted_for_component:
                kept.append(spec)
            # else: known metric trimmed by the cap — silent drop is intentional
        if unknown:
            raise ValueError(
                "Anomaly spec(s) reference metrics or components missing "
                f"from COMPONENTS (component, metric, description): {unknown}"
            )
        return kept

    for name in list(component_anomalies.keys()):
        component_anomalies[name] = _validate_and_filter(
            component_anomalies[name], name
        )
    for name in list(cascade_registry.keys()):
        cascade_registry[name] = _validate_and_filter(
            cascade_registry[name], name
        )


def _apply_signal_level_and_count(component_anomalies: dict, cascade_registry: dict,
                                  *, signal_level: str, selected_components: set,
                                  anomaly_count: int | None, seed: int,
                                  total_seconds: int, interval_seconds: float) -> None:
    """In-place filter the primary and cascade anomaly registries.

    Order is: severity (per ``signal_level``) → component allowlist
    (``selected_components``) → optional global cap (``anomaly_count``). Specs
    that fail any gate are dropped from the underlying lists/dicts so the
    generator never sees them.

    Out-of-range specs (``time_offset`` rounding to a row outside
    ``[0, n_rows)``) are excluded from the ``--anomaly-count`` sampling pool
    but always remain in the registries, so the generator's existing stderr
    soft-skip warning fires for them in every configuration. The cap still
    matches manifest output exactly because out-of-range specs cannot
    produce manifest rows.

    Sampling for ``anomaly_count`` uses a dedicated
    ``np.random.SeedSequence`` derived from ``seed`` with a fixed
    ``spawn_key`` tag, so it doesn't perturb the column-noise RNG stream that
    ``generate_component`` shares via ``np.random``. The sampled positions are
    iterated in sorted order so the manifest row order is fully deterministic
    for a given ``(seed, anomaly_count, eligible-pool)`` triple — independent
    of CPython's set iteration order.
    """
    allowed_severities = SIGNAL_LEVELS[signal_level]
    n_rows = int(total_seconds // interval_seconds)

    def _keep(spec: dict, component: str) -> bool:
        if component not in selected_components:
            return False
        return spec.get("severity", DEFAULT_SEVERITY) in allowed_severities

    def _in_range(spec: dict) -> bool:
        offset = spec.get("time_offset", 0)
        if offset < 0:
            return False
        return int(round(offset / interval_seconds)) < n_rows

    for component, specs in component_anomalies.items():
        component_anomalies[component] = [s for s in specs if _keep(s, component)]
    for component in list(cascade_registry.keys()):
        cascade_registry[component] = [s for s in cascade_registry[component]
                                       if _keep(s, component)]

    if anomaly_count is None:
        return

    # Build a positional view of the in-range pool only; out-of-range specs
    # cannot produce manifest rows but stay in the registries so the
    # generator's existing soft-skip warning still fires for them.
    positional: list[tuple[str, str, dict]] = []
    for component, specs in component_anomalies.items():
        for spec in specs:
            if _in_range(spec):
                positional.append((component, "primary", spec))
    for component, specs in cascade_registry.items():
        for spec in specs:
            if _in_range(spec):
                positional.append((component, "cascade", spec))

    if anomaly_count >= len(positional):
        return

    seq = np.random.SeedSequence(seed, spawn_key=(_ANOMALY_COUNT_CAP_SALT,))
    rng = np.random.default_rng(seq)
    # Iterate positions in sorted order so manifest row sequence is
    # independent of Python set hash-iteration order.
    keep_positions = sorted(
        rng.choice(len(positional), size=anomaly_count, replace=False).tolist()
    )

    kept_primary: dict[str, list[dict]] = {}
    kept_cascade: dict[str, list[dict]] = {}
    for pos in keep_positions:
        component, source, spec = positional[pos]
        target = kept_primary if source == "primary" else kept_cascade
        target.setdefault(component, []).append(spec)

    # Re-append out-of-range specs so the generator continues to surface its
    # stderr soft-skip warning for them; they cannot contribute manifest
    # rows, so the cap still matches output exactly.
    for component in list(component_anomalies.keys()):
        out_of_range = [s for s in component_anomalies[component] if not _in_range(s)]
        component_anomalies[component] = kept_primary.get(component, []) + out_of_range
    for component in list(cascade_registry.keys()):
        out_of_range = [s for s in cascade_registry[component] if not _in_range(s)]
        cascade_registry[component] = kept_cascade.get(component, []) + out_of_range


# ------------------------------------------------------------------
# CLI + entry point
# ------------------------------------------------------------------
def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="Generate synthetic IoT metric logs with anomalies.",
    )
    p.add_argument("--duration-days", type=int, default=DEFAULT_DURATION_DAYS,
                   help=f"Number of days of metrics to generate (default: {DEFAULT_DURATION_DAYS}). "
                        "The multi-day LLM/cascade catalog requires >= 7.")
    p.add_argument("--seed", type=int, default=DEFAULT_SEED,
                   help=f"RNG seed for deterministic output (default: {DEFAULT_SEED}).")
    p.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR,
                   help=f"Directory to write CSV files into (default: {DEFAULT_OUTPUT_DIR}).")
    p.add_argument("--drop-rate", type=float, default=DEFAULT_DROP_RATE,
                   help=f"Probability per row of writing a blank line to simulate packet loss "
                        f"(default: {DEFAULT_DROP_RATE}).")
    p.add_argument("--interval-seconds", type=float, default=DEFAULT_INTERVAL_SECONDS,
                   help=f"Seconds between consecutive emitted rows "
                        f"(default: {DEFAULT_INTERVAL_SECONDS}). Controls sampling "
                        f"density; timeline coverage stays --duration-days * 86400 "
                        f"seconds. Row count per component is floor(total_seconds / interval).")
    p.add_argument("--combine", action="store_true",
                   help="After generating logs, also write a unified combined CSV "
                        "(combined_metrics_unified.csv) into --output-dir.")
    p.add_argument("--combine-only", action="store_true",
                   help="Skip generation; only run the combine step against an existing "
                        "--output-dir. Useful for re-running the join without regenerating.")
    p.add_argument(
        "--emit-selection",
        type=str,
        default="metrics,logs,traces",
        help="Comma-separated artifact selection: metrics, logs, traces "
             "(default: metrics,logs,traces).",
    )
    p.add_argument(
        "--components",
        type=str,
        default="all",
        help="Comma-separated list of component names to emit (CSV files, "
             "anomalies.csv, reporting artifacts, and OTel streaming). Use "
             "'all' (default) for every component. Allowed names: "
             f"{', '.join(sorted(COMPONENTS.keys()))}.",
    )
    p.add_argument(
        "--signal-level",
        type=str,
        default=DEFAULT_SIGNAL_LEVEL,
        help="Anomaly intensity level: low, medium (default), or high. "
             "low keeps only benign baseline shifts; medium keeps the standard "
             "catalog (today's behavior); high additionally activates the "
             "high-pressure cross-component scenarios.",
    )
    p.add_argument(
        "--anomaly-count",
        type=int,
        default=None,
        help="Optional cap on the total number of anomalies (including "
             "cascades) injected across the whole dataset. Sampling is "
             "deterministic for a given --seed. Defaults to unlimited.",
    )
    p.add_argument(
        "--metrics-per-component",
        type=int,
        default=None,
        help=f"Optional cap on emitted metrics per component "
             f"(1..{MAX_METRICS_PER_COMPONENT}). When unset, every component "
             f"emits its historic default set. When set to N, each component "
             f"emits the first N metrics from its priority-ordered catalog "
             f"(highest-value first). Anomalies targeting metrics outside "
             f"the trimmed set are filtered out.",
    )
    otel_toggle = p.add_mutually_exclusive_group()
    otel_toggle.add_argument(
        "--otel-enabled",
        dest="otel_enabled",
        action="store_true",
        help="Enable streaming anomaly events to the configured OTLP/HTTP endpoints. "
             "Default: off. When off, configured endpoints are ignored at runtime.",
    )
    otel_toggle.add_argument(
        "--otel-disabled",
        dest="otel_enabled",
        action="store_false",
        help="Explicitly disable OTEL streaming (the default). Overrides --otel-enabled.",
    )
    p.set_defaults(otel_enabled=False)
    p.add_argument(
        "--otel-logs-endpoint",
        type=str,
        default=os.environ.get("MEZMO_OTEL_LOGS_ENDPOINT"),
        help="Optional OTLP/HTTP logs endpoint (for example http://localhost:4318/v1/logs). "
             "Streamed only when --otel-enabled is also passed. "
             "Env override: MEZMO_OTEL_LOGS_ENDPOINT.",
    )
    p.add_argument(
        "--otel-logs-auth-token",
        type=str,
        default=os.environ.get("MEZMO_OTEL_LOGS_AUTH_TOKEN"),
        help="Optional OTEL auth token for the logs endpoint. "
             "Env override: MEZMO_OTEL_LOGS_AUTH_TOKEN.",
    )
    p.add_argument(
        "--otel-metrics-endpoint",
        type=str,
        default=os.environ.get("MEZMO_OTEL_METRICS_ENDPOINT"),
        help="Optional OTLP/HTTP metrics endpoint (for example http://localhost:4318/v1/metrics). "
             "When set, anomaly events are replayed as metrics to this endpoint. "
             "Env override: MEZMO_OTEL_METRICS_ENDPOINT.",
    )
    p.add_argument(
        "--otel-metrics-auth-token",
        type=str,
        default=os.environ.get("MEZMO_OTEL_METRICS_AUTH_TOKEN"),
        help="Optional OTEL auth token for the metrics endpoint. "
             "Env override: MEZMO_OTEL_METRICS_AUTH_TOKEN.",
    )
    p.add_argument(
        "--otel-traces-endpoint",
        type=str,
        default=os.environ.get("MEZMO_OTEL_TRACES_ENDPOINT"),
        help="Optional OTLP/HTTP traces endpoint (for example http://localhost:4318/v1/traces). "
             "When set, anomaly events are replayed as traces to this endpoint. "
             "Env override: MEZMO_OTEL_TRACES_ENDPOINT.",
    )
    p.add_argument(
        "--otel-traces-auth-token",
        type=str,
        default=os.environ.get("MEZMO_OTEL_TRACES_AUTH_TOKEN"),
        help="Optional OTEL auth token for the traces endpoint. "
             "Env override: MEZMO_OTEL_TRACES_AUTH_TOKEN.",
    )
    p.add_argument(
        "--otel-stream-speedup",
        type=float,
        default=3600.0,
        help="Timeline replay speed multiplier for OTEL streaming (default: 3600). "
             "1.0 = real-time, 3600 = one hour of anomaly spacing per second.",
    )
    p.add_argument(
        "--otel-stream-timeout-seconds",
        type=float,
        default=5.0,
        help="HTTP timeout per OTEL streamed event in seconds (default: 5).",
    )
    p.add_argument(
        "--otel-stream-max-events",
        type=int,
        default=None,
        help="Optional cap on streamed anomaly event count (default: all).",
    )
    p.add_argument(
        "--otel-stream-auth-scheme",
        type=str,
        default=os.environ.get("MEZMO_OTEL_STREAM_AUTH_SCHEME", DEFAULT_OTEL_STREAM_AUTH_SCHEME),
        help="Auth scheme prefix for OTEL auth token (default: Bearer). "
             "Env override: MEZMO_OTEL_STREAM_AUTH_SCHEME.",
    )
    p.add_argument(
        "--otel-stream-protocol",
        type=str,
        default="protobuf",
        help="OTLP payload mode for stream endpoint: json or protobuf (default: protobuf).",
    )
    p.add_argument(
        "--otel-activity-log",
        type=Path,
        default=Path("otel-activity.log"),
        help="Path to the OTEL streaming activity log file. Records one line per "
             "send attempt, retry, and failure when --otel-enabled is set. The file "
             "is only created when streaming actually runs. "
             "Default: ./otel-activity.log in the current directory.",
    )
    p.add_argument(
        "--otel-verbose",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Include raw OTLP payload bodies, request headers, response status, "
             "and exception types in the activity log for each SEND/OK/RETRY/FAIL "
             "record. Authorization header values are masked. Default: off.",
    )
    p.add_argument("--inject-dst-artifact-day", type=int, default=0,
                   help="Inject a fall-DST artifact (duplicated 02:00–02:59 wall-clock hour) "
                        "on the given 1-based day of the run. 0 (default) disables. Generator "
                        "quirk, not an anomaly spec — does not appear in anomalies.csv. The "
                        "affected CSVs end up with 3,600/interval extra rows for that day.")
    args = p.parse_args(argv)

    if args.duration_days < 1:
        p.error("--duration-days must be >= 1")
    if not 0.0 <= args.drop_rate <= 1.0:
        p.error("--drop-rate must be between 0 and 1")
    if args.interval_seconds <= 0:
        p.error("--interval-seconds must be > 0")
    if args.combine and args.combine_only:
        p.error("--combine and --combine-only are mutually exclusive")
    if args.inject_dst_artifact_day < 0:
        p.error("--inject-dst-artifact-day must be >= 0 (0 disables)")
    if args.inject_dst_artifact_day > args.duration_days:
        p.error(f"--inject-dst-artifact-day {args.inject_dst_artifact_day} "
                f"is outside the configured --duration-days {args.duration_days}")
    selected = {item.strip().lower() for item in args.emit_selection.split(",") if item.strip()}
    allowed = {"metrics", "logs", "traces"}
    invalid = sorted(selected - allowed)
    if invalid:
        p.error("--emit-selection contains invalid value(s): "
                f"{', '.join(invalid)}. Allowed: metrics,logs,traces")
    if not selected:
        p.error("--emit-selection must contain at least one of metrics,logs,traces")
    if args.combine and "metrics" not in selected:
        p.error("--combine requires --emit-selection to include metrics")
    if args.otel_enabled and not any([
        args.otel_logs_endpoint, args.otel_metrics_endpoint, args.otel_traces_endpoint
    ]):
        p.error("--otel-enabled requires at least one of --otel-logs-endpoint, "
                "--otel-metrics-endpoint, or --otel-traces-endpoint to be set "
                "(via flag or env var).")
    if any([args.otel_logs_endpoint, args.otel_metrics_endpoint, args.otel_traces_endpoint]):
        endpoints = [
            ("logs", args.otel_logs_endpoint, args.otel_logs_auth_token),
            ("metrics", args.otel_metrics_endpoint, args.otel_metrics_auth_token),
            ("traces", args.otel_traces_endpoint, args.otel_traces_auth_token),
        ]
        for signal, endpoint, token in endpoints:
            if endpoint:
                if not endpoint.startswith(("http://", "https://")):
                    p.error(f"--otel-{signal}-endpoint must start with http:// or https://")
                if token and not token.strip():
                    p.error(f"--otel-{signal}-auth-token must be non-empty when provided")

        if args.otel_stream_speedup <= 0:
            p.error("--otel-stream-speedup must be > 0")
        if args.otel_stream_timeout_seconds <= 0:
            p.error("--otel-stream-timeout-seconds must be > 0")
        if args.otel_stream_max_events is not None and args.otel_stream_max_events < 1:
            p.error("--otel-stream-max-events must be >= 1")
        if args.otel_stream_auth_scheme.strip() == "":
            p.error("--otel-stream-auth-scheme must be non-empty")
        if args.otel_stream_protocol not in {"json", "protobuf"}:
            p.error("--otel-stream-protocol must be one of: json, protobuf")
    args.emit_selection = selected

    raw_components = [item.strip().lower() for item in args.components.split(",") if item.strip()]
    if not raw_components:
        p.error("--components must contain at least one component name (or 'all')")
    if "all" in raw_components:
        selected_components = set(COMPONENTS.keys())
    else:
        selected_components = set(raw_components)
        invalid_components = sorted(selected_components - set(COMPONENTS.keys()))
        if invalid_components:
            p.error("--components contains invalid value(s): "
                    f"{', '.join(invalid_components)}. "
                    f"Allowed: {', '.join(sorted(COMPONENTS.keys()))} or 'all'")
    args.components = selected_components

    signal_level = (args.signal_level or "").strip().lower()
    if signal_level not in SIGNAL_LEVELS:
        p.error("--signal-level must be one of: "
                f"{', '.join(sorted(SIGNAL_LEVELS.keys()))}")
    args.signal_level = signal_level

    if args.anomaly_count is not None and args.anomaly_count < 1:
        p.error("--anomaly-count must be >= 1 (omit the flag for unlimited)")

    if args.metrics_per_component is not None and (
        args.metrics_per_component < 1
        or args.metrics_per_component > MAX_METRICS_PER_COMPONENT
    ):
        p.error(
            f"--metrics-per-component must be in [1, {MAX_METRICS_PER_COMPONENT}] "
            f"(omit the flag to use each component's historic default count)"
        )

    return args


# ------------------------------------------------------------------
# Combine step: join per-component CSVs into a single unified CSV.
# One row per timestamp; columns prefixed with the component name.
# ------------------------------------------------------------------
_NON_COMPONENT_FILES = {"anomalies.csv"}


def discover_components(input_dir):
    """Return the sorted list of component names found in ``input_dir``.

    A component is any ``*.csv`` in ``input_dir`` that isn't the anomalies
    manifest or one of this script's own combine outputs.
    """
    components = []
    for path in sorted(Path(input_dir).glob("*.csv")):
        name = path.name
        if name in _NON_COMPONENT_FILES:
            continue
        if name.startswith("combined_metrics_"):
            continue
        components.append(path.stem)
    return components


def combine_logs_unified(components, input_dir, output_file=None):
    """Join the per-component CSVs in ``input_dir`` into a single unified CSV.

    ``output_file`` defaults to ``input_dir/combined_metrics_unified.csv``.
    Returns ``(total_rows, size_mb)``.
    """
    input_dir = Path(input_dir)
    if output_file is None:
        output_file = input_dir / "combined_metrics_unified.csv"
    output_file = Path(output_file)

    print("\nCreating UNIFIED format combined file...")
    print(f"Components discovered: {', '.join(components)}")

    data_by_timestamp = {}
    component_metrics = {}

    for component in components:
        input_path = input_dir / f"{component}.csv"
        print(f"Loading {component}.csv...")

        with open(input_path, "r") as infile:
            reader = csv.DictReader(infile)
            metric_names = [f for f in reader.fieldnames if f != "timestamp"]
            component_metrics[component] = metric_names

            for row in reader:
                timestamp = row["timestamp"]
                bucket = data_by_timestamp.setdefault(timestamp, {})
                bucket[component] = {metric: row[metric] for metric in metric_names}

    fieldnames = ["timestamp"]
    for component in components:
        for metric in component_metrics[component]:
            fieldnames.append(f"{component}_{metric}")

    print(f"Total columns: {len(fieldnames)} (1 timestamp + {len(fieldnames) - 1} metrics)")

    with open(output_file, "w", newline="") as outfile:
        writer = csv.DictWriter(outfile, fieldnames=fieldnames)
        writer.writeheader()

        for timestamp in sorted(data_by_timestamp.keys()):
            row = {"timestamp": timestamp}
            for component in components:
                component_row = data_by_timestamp[timestamp].get(component, {})
                for metric in component_metrics[component]:
                    row[f"{component}_{metric}"] = component_row.get(metric, "")
            writer.writerow(row)

    total_rows = len(data_by_timestamp)
    size_mb = os.path.getsize(output_file) / (1024 * 1024)
    print(f"\nUnified format file created: {output_file}")
    print(f"Total rows: {total_rows:,}")
    print(f"File size: {size_mb:.2f} MB")
    return total_rows, size_mb


def combine_logs(input_dir):
    """Discover components in ``input_dir`` and write the unified combined CSV."""
    input_dir = Path(input_dir)
    components = discover_components(input_dir)
    if not components:
        raise SystemExit(f"No component CSVs found in {input_dir}/")
    return combine_logs_unified(components, input_dir)


def _anomaly_event_id(entry: dict) -> str:
    """Deterministic event id used to correlate metrics, logs, and traces."""
    required = ("timestamp", "component", "metric", "description")
    missing = [k for k in required if not entry.get(k)]
    if missing:
        raise ValueError(f"anomaly entry missing required field(s): {', '.join(missing)}")
    payload = "|".join(str(entry[k]) for k in required)
    return "evt_" + sha1(payload.encode("utf-8")).hexdigest()[:16]


def write_reporting_artifacts(output_dir: Path, anomaly_rows: list[dict]) -> None:
    """Emit correlated log and trace artifacts aligned to anomaly metric records."""
    output_dir = Path(output_dir)
    log_path = output_dir / "metric_report.log"
    trace_path = output_dir / "metric_traces.jsonl"

    with open(log_path, "w", newline="") as log_f, open(trace_path, "w", newline="") as trace_f:
        for entry in anomaly_rows:
            event_id = _anomaly_event_id(entry)
            component = entry["component"]
            metric = entry["metric"]
            timestamp = entry["timestamp"]
            description = entry["description"]

            log_f.write(
                f"{timestamp} INFO metric_report event_id={event_id} "
                f"component={component} metric={metric} msg=\"{description}\"\n"
            )

            trace_f.write(json.dumps({
                "timestamp": timestamp,
                "trace_id": f"trace_{event_id[4:]}",
                "span_id": f"span_{event_id[4:12]}",
                "event_id": event_id,
                "signal_type": "metric_anomaly",
                "component": component,
                "metric": metric,
                "description": description,
            }) + "\n")


def _to_unix_nanos(timestamp: str) -> int:
    """Convert ``YYYY-MM-DD HH:MM:SS`` timestamp strings to unix-nanoseconds."""
    dt = datetime.datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S")
    return int(dt.replace(tzinfo=datetime.timezone.utc).timestamp() * 1_000_000_000)


def _build_otlp_trace_payload(entry: dict) -> dict:
    """Build one OTLP/HTTP JSON ``resourceSpans`` payload from one anomaly event."""
    event_id = _anomaly_event_id(entry)
    component = entry["component"]
    metric = entry["metric"]
    timestamp = entry["timestamp"]
    description = entry["description"]
    attributes = [
        {"key": "event.id", "value": {"stringValue": event_id}},
        {"key": "signal.type", "value": {"stringValue": "metric_anomaly"}},
        {"key": "metric.name", "value": {"stringValue": metric}},
        {"key": "component", "value": {"stringValue": component}},
    ]
    
    ts_nano = _to_unix_nanos(timestamp)
    return {
        "resourceSpans": [{
            "resource": {
                "attributes": [
                    {"key": "service.name", "value": {"stringValue": component}},
                    {"key": "service.namespace", "value": {"stringValue": "anomaly-metric-creator"}},
                ]
            },
            "scopeSpans": [{
                "scope": {
                    "name": "anomaly-metric-creator",
                    "version": "1.0.0",
                },
                "spans": [{
                    "traceId": event_id[4:] * 2,
                    "spanId": event_id[4:20],
                    "name": f"anomaly:{metric}",
                    "kind": 1,  # SPAN_KIND_INTERNAL
                    "startTimeUnixNano": str(ts_nano),
                    "endTimeUnixNano": str(ts_nano + 1000000),  # 1ms duration
                    "attributes": attributes,
                    "status": {"code": 1},  # STATUS_CODE_OK
                }]
            }]
        }]
    }


def _build_otlp_trace_protobuf(entry: dict) -> bytes:
    """Build one OTLP protobuf ExportTraceServiceRequest payload."""
    try:
        from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
        from opentelemetry.proto.common.v1.common_pb2 import AnyValue, KeyValue
    except ImportError as exc:
        raise SystemExit(
            "OTLP protobuf mode requires opentelemetry-proto + protobuf. "
            "Install with: pip install opentelemetry-proto protobuf"
        ) from exc

    event_id = _anomaly_event_id(entry)
    component = entry["component"]
    metric = entry["metric"]
    timestamp = entry["timestamp"]
    description = entry["description"]
    attributes = [
        KeyValue(key="event.id", value=AnyValue(string_value=event_id)),
        KeyValue(key="signal.type", value=AnyValue(string_value="metric_anomaly")),
        KeyValue(key="metric.name", value=AnyValue(string_value=metric)),
        KeyValue(key="component", value=AnyValue(string_value=component)),
    ]

    req = ExportTraceServiceRequest()
    rspan = req.resource_spans.add()
    rspan.resource.attributes.extend([
        KeyValue(key="service.name", value=AnyValue(string_value=component)),
        KeyValue(key="service.namespace", value=AnyValue(string_value="anomaly-metric-creator")),
    ])
    sspan = rspan.scope_spans.add()
    sspan.scope.name = "anomaly-metric-creator"
    sspan.scope.version = "1.0.0"

    ts_nano = _to_unix_nanos(timestamp)
    span = sspan.spans.add()
    span.trace_id = bytes.fromhex(event_id[4:] * 2)
    span.span_id = bytes.fromhex(event_id[4:20])
    span.name = f"anomaly:{metric}"
    span.kind = 1
    span.start_time_unix_nano = ts_nano
    span.end_time_unix_nano = ts_nano + 1000000
    span.attributes.extend(attributes)
    span.status.code = 1
    return req.SerializeToString()


def _build_otlp_metric_payload(entry: dict) -> dict:
    """Build one OTLP/HTTP JSON ``resourceMetrics`` payload from one anomaly event."""
    event_id = _anomaly_event_id(entry)
    component = entry["component"]
    metric = entry["metric"]
    timestamp = entry["timestamp"]
    # For metrics, we'll emit a counter increment for the anomaly
    attributes = [
        {"key": "event.id", "value": {"stringValue": event_id}},
        {"key": "signal.type", "value": {"stringValue": "metric_anomaly"}},
        {"key": "metric.name", "value": {"stringValue": metric}},
        {"key": "component", "value": {"stringValue": component}},
    ]
    ts_nano = _to_unix_nanos(timestamp)
    return {
        "resourceMetrics": [{
            "resource": {
                "attributes": [
                    {"key": "service.name", "value": {"stringValue": component}},
                    {"key": "service.namespace", "value": {"stringValue": "anomaly-metric-creator"}},
                ]
            },
            "scopeMetrics": [{
                "scope": {
                    "name": "anomaly-metric-creator",
                    "version": "1.0.0",
                },
                "metrics": [{
                    "name": "anomaly.count",
                    "description": "Counter of anomaly events",
                    "sum": {
                        "dataPoints": [{
                            "startTimeUnixNano": str(ts_nano),
                            "timeUnixNano": str(ts_nano),
                            "asInt": "1",
                            "attributes": attributes,
                        }],
                        "aggregationTemporality": 1, # DELTA
                        "isMonotonic": True,
                    }
                }]
            }]
        }]
    }


def _build_otlp_metric_protobuf(entry: dict) -> bytes:
    """Build one OTLP protobuf ExportMetricsServiceRequest payload."""
    try:
        from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import ExportMetricsServiceRequest
        from opentelemetry.proto.common.v1.common_pb2 import AnyValue, KeyValue
    except ImportError as exc:
        raise SystemExit(
            "OTLP protobuf mode requires opentelemetry-proto + protobuf. "
            "Install with: pip install opentelemetry-proto protobuf"
        ) from exc

    event_id = _anomaly_event_id(entry)
    component = entry["component"]
    metric = entry["metric"]
    timestamp = entry["timestamp"]
    attributes = [
        KeyValue(key="event.id", value=AnyValue(string_value=event_id)),
        KeyValue(key="signal.type", value=AnyValue(string_value="metric_anomaly")),
        KeyValue(key="metric.name", value=AnyValue(string_value=metric)),
        KeyValue(key="component", value=AnyValue(string_value=component)),
    ]

    req = ExportMetricsServiceRequest()
    rmetric = req.resource_metrics.add()
    rmetric.resource.attributes.extend([
        KeyValue(key="service.name", value=AnyValue(string_value=component)),
        KeyValue(key="service.namespace", value=AnyValue(string_value="anomaly-metric-creator")),
    ])
    smetric = rmetric.scope_metrics.add()
    smetric.scope.name = "anomaly-metric-creator"
    smetric.scope.version = "1.0.0"

    ts_nano = _to_unix_nanos(timestamp)
    m = smetric.metrics.add()
    m.name = "anomaly.count"
    m.description = "Counter of anomaly events"
    m.sum.aggregation_temporality = 1
    m.sum.is_monotonic = True
    dp = m.sum.data_points.add()
    dp.start_time_unix_nano = ts_nano
    dp.time_unix_nano = ts_nano
    dp.as_int = 1
    dp.attributes.extend(attributes)
    return req.SerializeToString()


def _build_otlp_log_payload(entry: dict) -> dict:
    """Build one OTLP/HTTP JSON ``resourceLogs`` payload from one anomaly event."""
    event_id = _anomaly_event_id(entry)
    component = entry["component"]
    metric = entry["metric"]
    timestamp = entry["timestamp"]
    description = entry["description"]
    return {
        "resourceLogs": [{
            "resource": {
                "attributes": [
                    {"key": "service.name", "value": {"stringValue": component}},
                    {"key": "service.namespace", "value": {"stringValue": "anomaly-metric-creator"}},
                ]
            },
            "scopeLogs": [{
                "scope": {
                    "name": "anomaly-metric-creator",
                    "version": "1.0.0",
                },
                "logRecords": [{
                    "timeUnixNano": str(_to_unix_nanos(timestamp)),
                    "severityText": "INFO",
                    "body": {"stringValue": description},
                    "attributes": [
                        {"key": "event.id", "value": {"stringValue": event_id}},
                        {"key": "signal.type", "value": {"stringValue": "metric_anomaly"}},
                        {"key": "metric.name", "value": {"stringValue": metric}},
                        {"key": "component", "value": {"stringValue": component}},
                    ],
                    "traceId": event_id[4:] * 2,
                    "spanId": event_id[4:20],
                }]
            }]
        }]
    }


def _build_otlp_log_protobuf(entry: dict) -> bytes:
    """Build one OTLP protobuf ExportLogsServiceRequest payload."""
    try:
        from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import ExportLogsServiceRequest
        from opentelemetry.proto.common.v1.common_pb2 import AnyValue, KeyValue
    except ImportError as exc:
        raise SystemExit(
            "OTLP protobuf mode requires opentelemetry-proto + protobuf. "
            "Install with: pip install opentelemetry-proto protobuf"
        ) from exc

    event_id = _anomaly_event_id(entry)
    component = entry["component"]
    metric = entry["metric"]
    timestamp = entry["timestamp"]
    description = entry["description"]

    req = ExportLogsServiceRequest()
    rlog = req.resource_logs.add()
    rlog.resource.attributes.extend([
        KeyValue(key="service.name", value=AnyValue(string_value=component)),
        KeyValue(key="service.namespace", value=AnyValue(string_value="anomaly-metric-creator")),
    ])
    slog = rlog.scope_logs.add()
    slog.scope.name = "anomaly-metric-creator"
    slog.scope.version = "1.0.0"

    record = slog.log_records.add()
    record.time_unix_nano = _to_unix_nanos(timestamp)
    record.severity_text = "INFO"
    record.body.CopyFrom(AnyValue(string_value=description))
    record.attributes.extend([
        KeyValue(key="event.id", value=AnyValue(string_value=event_id)),
        KeyValue(key="signal.type", value=AnyValue(string_value="metric_anomaly")),
        KeyValue(key="metric.name", value=AnyValue(string_value=metric)),
        KeyValue(key="component", value=AnyValue(string_value=component)),
    ])
    record.trace_id = bytes.fromhex(event_id[4:] * 2)
    record.span_id = bytes.fromhex(event_id[4:20])
    return req.SerializeToString()


def _write_activity(log_file, event: str, **fields) -> None:
    """Append one activity record. Format: ``ISO_TS EVENT k=v k=v``.

    Values are shell-quoted so embedded whitespace (e.g. ``event_ts`` which uses
    ``YYYY-MM-DD HH:MM:SS``) keeps each ``k=v`` token round-trippable via
    ``shlex.split``.
    """
    if log_file is None:
        return
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    parts = [now, event]
    for k, v in fields.items():
        parts.append(f"{k}={shlex.quote(str(v))}")
    log_file.write(" ".join(parts) + "\n")
    log_file.flush()


def _verbose_body_repr(body: bytes, content_type: str) -> str:
    """Render an OTLP request body for inclusion in the verbose activity log.

    JSON bodies are decoded back to text; protobuf bodies are base64-encoded so
    the log line stays printable and shlex-parseable.
    """
    if "json" in content_type:
        try:
            return body.decode("utf-8")
        except UnicodeDecodeError:
            return base64.b64encode(body).decode("ascii")
    return base64.b64encode(body).decode("ascii")


def _masked_headers(headers: dict[str, str]) -> dict[str, str]:
    """Return a copy of ``headers`` with auth values masked.

    The Authorization header is preserved with its scheme prefix (e.g. ``Bearer``)
    but the token portion is replaced with ``***`` so verbose logs never leak
    bearer/api-key material.
    """
    masked = {}
    for key, value in headers.items():
        if key.lower() == "authorization":
            parts = value.split(" ", 1)
            if len(parts) == 2:
                masked[key] = f"{parts[0]} ***"
            else:
                masked[key] = "***"
        else:
            masked[key] = value
    return masked


def stream_otel_signals(
    endpoints: dict[str, str], # {"logs": url, "metrics": url, "traces": url}
    anomaly_rows: list[dict],
    *,
    speedup: float,
    timeout_seconds: float,
    max_events: int | None = None,
    max_retries: int = 3,
    auth_headers: dict[str, dict[str, str]] | None = None, # {"logs": {"Authorization": ...}, ...}
    protocol: str = "json",
    activity_log_path: Path | None = None,
    verbose: bool = False,
) -> int:
    """Replay anomalies to multiple OTLP/HTTP endpoints with timeline-aware pacing.

    Failures are logged to stderr and do not stop generation. When
    ``activity_log_path`` is set, also records one line per send attempt,
    retry, and failure to that file. When ``verbose`` is true, those records
    additionally include the raw request body, request headers (auth tokens
    masked), the HTTP response status on success, and the exception type on
    failure.
    """
    sorted_rows = sorted(anomaly_rows, key=lambda row: row["timestamp"])
    if max_events is not None:
        sorted_rows = sorted_rows[:max_events]
    if not sorted_rows:
        return 0

    log_file = None
    if activity_log_path is not None:
        activity_log_path.parent.mkdir(parents=True, exist_ok=True)
        log_file = open(activity_log_path, "w", encoding="utf-8")

    active_signals = ",".join(s for s, u in endpoints.items() if u) or "(none)"
    _write_activity(
        log_file,
        "START",
        signals=active_signals,
        events=len(sorted_rows),
        protocol=protocol,
        speedup=speedup,
    )

    prev_dt = None
    sent = 0
    try:
        for row in sorted_rows:
            cur_dt = datetime.datetime.strptime(row["timestamp"], "%Y-%m-%d %H:%M:%S")
            if prev_dt is not None:
                wait_seconds = max(0.0, (cur_dt - prev_dt).total_seconds() / speedup)
                if wait_seconds > 0:
                    time.sleep(wait_seconds)
            prev_dt = cur_dt

            # Prepare requests for each signal
            for signal, endpoint in endpoints.items():
                if not endpoint:
                    continue

                if signal == "logs":
                    if protocol == "protobuf":
                        body = _build_otlp_log_protobuf(row)
                        content_type = "application/x-protobuf"
                    else:
                        body = json.dumps(_build_otlp_log_payload(row)).encode("utf-8")
                        content_type = "application/json"
                elif signal == "metrics":
                    if protocol == "protobuf":
                        body = _build_otlp_metric_protobuf(row)
                        content_type = "application/x-protobuf"
                    else:
                        body = json.dumps(_build_otlp_metric_payload(row)).encode("utf-8")
                        content_type = "application/json"
                elif signal == "traces":
                    if protocol == "protobuf":
                        body = _build_otlp_trace_protobuf(row)
                        content_type = "application/x-protobuf"
                    else:
                        body = json.dumps(_build_otlp_trace_payload(row)).encode("utf-8")
                        content_type = "application/json"
                else:
                    continue

                headers = {"Content-Type": content_type}
                if auth_headers and signal in auth_headers:
                    headers.update(auth_headers[signal])

                req = urllib.request.Request(endpoint, data=body, method="POST", headers=headers)
                verbose_send_fields: dict = {}
                if verbose:
                    verbose_send_fields["body"] = _verbose_body_repr(body, content_type)
                    for hk, hv in _masked_headers(headers).items():
                        verbose_send_fields[hk.lower().replace("-", "_")] = hv
                attempts = 0
                while True:
                    _write_activity(
                        log_file,
                        "SEND",
                        signal=signal,
                        endpoint=endpoint,
                        event_ts=row["timestamp"],
                        component=row["component"],
                        metric=row["metric"],
                        attempt=f"{attempts + 1}/{max_retries + 1}",
                        **verbose_send_fields,
                    )
                    try:
                        with urllib.request.urlopen(req, timeout=timeout_seconds) as response:
                            response_status = response.status
                            if response.status >= 400:
                                raise urllib.error.HTTPError(
                                    endpoint,
                                    response.status,
                                    response.reason,
                                    response.headers,
                                    None,
                                )
                        ok_fields: dict = {}
                        if verbose:
                            ok_fields["status"] = response_status
                        _write_activity(
                            log_file,
                            "OK",
                            signal=signal,
                            event_ts=row["timestamp"],
                            component=row["component"],
                            metric=row["metric"],
                            **ok_fields,
                        )
                        sent += 1
                        break
                    except (urllib.error.URLError, urllib.error.HTTPError) as exc:
                        attempts += 1
                        err_fields: dict = {}
                        if verbose:
                            err_fields["error_type"] = type(exc).__name__
                            if isinstance(exc, urllib.error.HTTPError):
                                err_fields["status"] = exc.code
                        if attempts > max_retries:
                            print(
                                f"WARNING: OTEL {signal} stream failed for {row['timestamp']} "
                                f"({row['component']}.{row['metric']}): {exc}",
                                file=sys.stderr,
                            )
                            _write_activity(
                                log_file,
                                "FAIL",
                                signal=signal,
                                event_ts=row["timestamp"],
                                component=row["component"],
                                metric=row["metric"],
                                error=repr(str(exc)),
                                **err_fields,
                            )
                            break
                        backoff = min(2 ** (attempts - 1), 8)
                        print(
                            f"WARNING: OTEL {signal} stream retry {attempts}/{max_retries} for "
                            f"{row['timestamp']} ({row['component']}.{row['metric']}): {exc}",
                            file=sys.stderr,
                        )
                        _write_activity(
                            log_file,
                            "RETRY",
                            signal=signal,
                            event_ts=row["timestamp"],
                            component=row["component"],
                            metric=row["metric"],
                            attempt=f"{attempts}/{max_retries}",
                            error=repr(str(exc)),
                            **err_fields,
                        )
                        time.sleep(backoff)
    finally:
        _write_activity(log_file, "END", sent=sent)
        if log_file is not None:
            log_file.close()
    return sent


def main(argv=None):
    args = parse_args(argv)

    if args.combine_only:
        if not args.output_dir.is_dir():
            raise SystemExit(f"--combine-only requires an existing --output-dir; "
                             f"{args.output_dir} does not exist")
        combine_logs(args.output_dir)
        return

    total_seconds = SECONDS_PER_DAY * args.duration_days
    args.output_dir.mkdir(exist_ok=True, parents=True)
    np.random.seed(args.seed)

    # Reset module-level registries so repeated calls (e.g., from tests) don't accumulate.
    anomalies.clear()
    cascading_anomalies.clear()
    register_default_cascades()
    if args.signal_level == "high":
        register_high_pressure_cascades()

    component_anomalies = {
        "authservice": list(anoms_auth),
        "cacheservice": list(anoms_cache),
        "apigateway": list(anoms_api),
        "database": list(anoms_db),
        "mqservice": list(anoms_mq),
        "llm_analytics": list(anoms_llm),
        "loadbalancer": list(anoms_lb),
        "objectstore": list(anoms_obj),
        "vectorstore": list(anoms_vec),
        "scheduler": list(anoms_scheduler),
        "paymentservice": list(anoms_payment),
        "identityprovider": list(anoms_idp),
        "observabilitypipeline": list(anoms_obs),
    }
    if args.signal_level == "high":
        component_anomalies["loadbalancer"].extend(anoms_high_lb)
        component_anomalies["cacheservice"].extend(anoms_high_cache)
        component_anomalies["database"].extend(anoms_high_db)
        component_anomalies["llm_analytics"].extend(anoms_high_llm)
        component_anomalies["apigateway"].extend(anoms_high_api)
        component_anomalies["objectstore"].extend(anoms_high_obj)

    effective_specs = _resolve_effective_specs(args.metrics_per_component)
    _filter_anomalies_for_emitted_metrics(
        component_anomalies, cascading_anomalies, effective_specs
    )

    _apply_signal_level_and_count(
        component_anomalies,
        cascading_anomalies,
        signal_level=args.signal_level,
        selected_components=args.components,
        anomaly_count=args.anomaly_count,
        seed=args.seed,
        total_seconds=total_seconds,
        interval_seconds=args.interval_seconds,
    )

    ts_array, ts_strings = _build_timestamp_arrays(total_seconds, args.interval_seconds)
    n_rows = int(total_seconds // args.interval_seconds)

    for name, specs in effective_specs.items():
        if name not in args.components:
            continue
        generate_component(name, specs, component_anomalies[name],
                           base_dir=args.output_dir,
                           total_seconds=total_seconds,
                           drop_rate=args.drop_rate,
                           interval=args.interval_seconds,
                           ts_array=ts_array,
                           ts_strings=ts_strings,
                           emit_metrics="metrics" in args.emit_selection,
                           dst_inject_day=args.inject_dst_artifact_day)

    filtered_anomalies = [a for a in anomalies if a["component"] in args.components]

    if "metrics" in args.emit_selection:
        with open(args.output_dir / "anomalies.csv", "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["timestamp", "component", "metric", "description"])
            writer.writeheader()
            for a in filtered_anomalies:
                writer.writerow(a)
    else:
        (args.output_dir / "anomalies.csv").unlink(missing_ok=True)

    if {"logs", "traces"} & args.emit_selection:
        write_reporting_artifacts(args.output_dir, filtered_anomalies)
        if "logs" not in args.emit_selection:
            (args.output_dir / "metric_report.log").unlink(missing_ok=True)
        if "traces" not in args.emit_selection:
            (args.output_dir / "metric_traces.jsonl").unlink(missing_ok=True)

    streamed_events = 0
    endpoints = {
        "logs": args.otel_logs_endpoint,
        "metrics": args.otel_metrics_endpoint,
        "traces": args.otel_traces_endpoint,
    }
    otel_active = args.otel_enabled and any(endpoints.values())
    if otel_active:
        auth_headers = {}
        for signal in ["logs", "metrics", "traces"]:
            token = getattr(args, f"otel_{signal}_auth_token")
            if token:
                auth_headers[signal] = {"Authorization": f"{args.otel_stream_auth_scheme} {token}"}

        streamed_events = stream_otel_signals(
            endpoints,
            filtered_anomalies,
            speedup=args.otel_stream_speedup,
            timeout_seconds=args.otel_stream_timeout_seconds,
            max_events=args.otel_stream_max_events,
            auth_headers=auth_headers,
            protocol=args.otel_stream_protocol,
            activity_log_path=args.otel_activity_log,
            verbose=args.otel_verbose,
        )

    print(f"Done - {len(args.components)} log files + anomalies.csv + reporting artifacts written to {args.output_dir}")
    print(f"   Duration: {args.duration_days} day(s) ({total_seconds:,} seconds)")
    print(f"   Interval: {args.interval_seconds}s ({n_rows:,} rows per component)")
    print(f"   Anomalies recorded: {len(filtered_anomalies)}")
    if otel_active:
        active = [f"{s} -> {u}" for s, u in endpoints.items() if u]
        print(f"   OTEL signals streamed: {streamed_events} to {', '.join(active)}")
    elif any(endpoints.values()):
        print("   OTEL streaming disabled (pass --otel-enabled to send to configured endpoints)")

    if args.combine:
        combine_logs(args.output_dir)


if __name__ == "__main__":
    main()
