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
import heapq
import json
import math
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

# Derived-metric registry. Each entry maps a component to (derivation_fn,
# tuple_of_derived_metric_names). generate_component() looks the component
# up in this dict after the natural-value pass and the anomaly override
# loop; if a function is registered, it recomputes the derived column(s)
# from their sibling columns so the emitted CSV stays self-consistent.
# Anomalies that want to influence a derived column must therefore target
# its source column(s), not the derived column itself.
#
# DERIVATIONS is the single source of truth: ``DERIVED_METRICS`` is
# computed from it below, so the test-side exemption set and the
# derivation pass can never drift apart. A new derived column requires
# registering both the function and the column name here in lockstep.
def _derive_cacheservice(values: "np.ndarray", name_to_col: dict[str, int]) -> None:
    """Recompute ``hit_ratio`` from ``cache_hits`` / ``cache_misses``.

    Clamps the source columns to ``>= 0`` in place first so the emitted CSV
    values agree with the derived ratio. Anomaly generators bypass
    ``MetricSpec.clip_min``, so without the in-place clamp a future
    generator that drove the counters negative would yield emitted source
    values < 0 alongside a derivation computed from clamped intermediates —
    breaking the very consistency invariant this pass exists to enforce.
    """
    hits_col = name_to_col.get("cache_hits")
    misses_col = name_to_col.get("cache_misses")
    ratio_col = name_to_col.get("hit_ratio")
    if hits_col is None or misses_col is None or ratio_col is None:
        return
    np.maximum(values[:, hits_col], 0.0, out=values[:, hits_col])
    np.maximum(values[:, misses_col], 0.0, out=values[:, misses_col])
    hits = values[:, hits_col]
    misses = values[:, misses_col]
    denom = hits + misses
    with np.errstate(divide="ignore", invalid="ignore"):
        values[:, ratio_col] = np.where(
            denom > 0, 100.0 * hits / denom, 0.0
        )


DERIVATIONS: dict[
    str,
    tuple[Callable[["np.ndarray", dict[str, int]], None], tuple[str, ...]],
] = {
    "cacheservice": (_derive_cacheservice, ("hit_ratio",)),
}

DERIVED_METRICS: set[tuple[str, str]] = {
    (component, metric)
    for component, (_, metrics) in DERIVATIONS.items()
    for metric in metrics
}

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


# ------------------------------------------------------------------
# Named scenario registry (VER-102 / VER-104 — full migration complete).
#
# Each Scenario bundles a slug-named set of primary anomaly specs and
# cascade specs that can be selected together via --scenarios. Every
# anomaly and cascade in the codebase lives in the ``SCENARIOS`` dict
# below; there is no legacy ``anoms_*`` path. ``main()`` builds
# ``component_anomalies`` and ``cascading_anomalies`` exclusively via
# ``_apply_scenarios()``.
#
# Each primary_spec is paired with the component name where it lands;
# each cascade_spec is paired with the target component. The inner dict
# carries the same fields the generator path consumes (time_offset,
# metric, description, generator, optional duration_seconds / shape /
# shape_params / severity), so ``generate_component()`` sees a uniform
# spec shape regardless of whether it arrived as a primary or a cascade.
# ------------------------------------------------------------------
@dataclass(frozen=True)
class Scenario:
    """A named bundle of primary + cascade specs selectable via --scenarios.

    ``days_required`` is the minimum ``--duration-days`` value at which the
    scenario can fully manifest; below that, ``_resolve_scenarios`` emits a
    stderr WARNING naming the scenario and drops it. ``severity`` follows the
    same vocabulary as individual anomaly specs (``low`` / ``medium`` /
    ``high``); scenarios outside the active ``--signal-level`` hierarchy are
    similarly warn-and-skip. ``components_touched`` is the union of components
    where any primary or cascade lands, used by ``--components`` to short-
    circuit scenarios that produce no output for the active component set.
    """
    id: str
    name: str
    severity: str
    days_required: int
    category: str
    components_touched: tuple[str, ...]
    # (component, spec_dict) pairs. spec_dict has the same shape as an entry
    # in the legacy anoms_* lists (time_offset, metric, description, generator,
    # optional duration_seconds/shape/shape_params/severity).
    primary_specs: tuple[tuple[str, dict], ...]
    # (target_component, cascade_dict) pairs. cascade_dict has the same shape
    # as values stored in cascading_anomalies (time_offset, metric, description,
    # generator, severity).
    cascade_specs: tuple[tuple[str, dict], ...]


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

    # Derived metrics: rebuild self-consistent relationships after natural and
    # anomaly values have settled. The registered function recomputes the
    # derived column(s) from their sibling columns; without this pass,
    # anomalies that drove only a source column (or that overrode a derived
    # column in isolation) would leave the columns internally inconsistent —
    # exactly the consistency anomaly real telemetry would flag.
    derivation = DERIVATIONS.get(component_name)
    if derivation is not None:
        derive_fn, _ = derivation
        derive_fn(values, name_to_col)

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
    precision when ``interval >= 1.0`` and at millisecond precision otherwise
    so adjacent sub-second rows never share a timestamp string.
    """
    n_rows = int(total_seconds // interval)
    step_us = int(round(interval * 1_000_000))
    ts_array = np.datetime64(START) + np.arange(n_rows) * np.timedelta64(step_us, "us")
    if interval < 1.0:
        ts_strings = np.char.replace(
            np.datetime_as_string(ts_array, unit="ms"), "T", " "
        )
    else:
        ts_strings = np.char.replace(
            np.datetime_as_string(ts_array, unit="s"), "T", " "
        )
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
        MetricSpec("error_rate", 0.2, 0.05, clip_min=0),
        # Supplemental metrics
        MetricSpec("avg_session_duration_s", 900, 30, clip_min=0),
        MetricSpec("password_reset_per_min", 3, 1, clip_min=0),
        MetricSpec("admin_actions_per_min", 8, 2, clip_min=0),
        MetricSpec("memory_util_pct", 45, 4),
    ],
    "cacheservice": [
        MetricSpec("cache_hits", 5000, 200, clip_min=0),
        MetricSpec("cache_misses", 200, 20, clip_min=0),
        MetricSpec("hit_ratio", 95.0, 0.3),
        MetricSpec("avg_cache_latency_ms", 15, 1),
        MetricSpec("memory_util_pct", 70, 5),
        MetricSpec("error_rate", 0.05, 0.02, clip_min=0),
        # Supplemental metrics
        MetricSpec("evictions_per_sec", 8, 3, clip_min=0),
        MetricSpec("expired_keys_per_sec", 12, 4, clip_min=0),
        MetricSpec("cpu_util_pct", 15, 3, clip_min=0),
        MetricSpec("connected_clients", 400, 30, clip_min=0),
    ],
    "apigateway": [
        MetricSpec("requests_per_sec", 800, 50),
        MetricSpec("avg_response_time_ms", 180, 10),
        MetricSpec("backend_latency_ms", 90, 8),
        MetricSpec("active_connections", 1200, 60),
        MetricSpec("cpu_util_pct", 22, 4),
        MetricSpec("error_rate", 0.15, 0.04, clip_min=0),
        # Supplemental metrics
        MetricSpec("rate_limited_per_sec", 4, 2, clip_min=0),
        MetricSpec("tls_handshakes_per_sec", 140, 15, clip_min=0),
        MetricSpec("memory_util_pct", 55, 4),
        MetricSpec("upstream_unhealthy_count", 0.2, 0.4, clip_min=0),
    ],
    "database": [
        MetricSpec("connections", 3000, 400),
        MetricSpec("read_latency_ms", 10, 2, clip_min=0),
        MetricSpec("write_latency_ms", 12, 3, clip_min=0),
        MetricSpec("queries_per_sec", 25000, 2000),
        MetricSpec("cpu_util_pct", 18, 3),
        MetricSpec("error_rate", 0.1, 0.05, clip_min=0),
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
        MetricSpec("dead_letter_queue", 5, 1, clip_min=0),
        MetricSpec("mem_util_pct", 55, 4),
        MetricSpec("error_rate", 0.08, 0.02, clip_min=0),
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
        MetricSpec("prompt_cache_hit_ratio", 55.0, 2.0, clip_min=0),
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
# Anomaly specifications — migrated to SCENARIOS registry (VER-104).
# All anomaly and cascade specs now live in the SCENARIOS dict below.
# ------------------------------------------------------------------
# (legacy anoms_* lists and COMPONENT_PRIMARY_ANOMALIES removed)


# ------------------------------------------------------------------
# Named scenario registry (VER-102 / VER-104 — full migration complete).
#
# Every primary spec and cascade lives here. main() builds component_anomalies
# and cascading_anomalies entirely from this registry via _apply_scenarios().
# Walk order is dict-insertion order (Python 3.7+). Within each scenario,
# primary_specs and cascade_specs are appended in declaration order.
# Byte-for-byte default output is preserved because generate_component()
# applies Python's stable sort with key (row_idx, metric) to expanded
# overrides, so generator call order — and the global RNG draw sequence —
# is determined by (time_offset, metric_name) when those keys are unique.
# When two specs collide on the same (row_idx, metric) (e.g. a cascade
# landing inside a shaped span, or coarse --interval-seconds rounding
# multiple offsets to the same row), the stable sort preserves their
# declaration order and the last writer wins — so spec list order within
# a scenario is part of the contract for collision cases.
# ------------------------------------------------------------------
SCENARIOS: dict[str, Scenario] = {
    # ------------------------------------------------------------------
    # Same-day medium-severity scenario clusters (days_required=1)
    # ------------------------------------------------------------------
    "auth_brute_force": Scenario(
        id="auth_brute_force",
        name="Authentication brute-force attack",
        severity="medium",
        days_required=1,
        category="same_day",
        components_touched=("authservice", "apigateway"),
        primary_specs=(
            ("authservice", {
                "time_offset": 2*3600 + 15*60,
                "metric": "error_rate",
                "description": "Spike in failed logins – possible brute force",
                "generator": lambda ts, idx: 0.42,
            }),
            ("authservice", {
                "time_offset": 2*3600 + 15*60,
                "metric": "login_attempts",
                "description": "Login attempts surge 5×",
                "generator": lambda ts, idx: 1250,
            }),
        ),
        cascade_specs=(
            ("apigateway", {
                "time_offset": 2*3600 + 15*60 + 15,
                "metric": "error_rate",
                "description": "Cascading: Auth failures cause API gateway errors",
                "generator": lambda ts, idx: 0.28,
                "severity": DEFAULT_SEVERITY,
            }),
            ("authservice", {
                "time_offset": 2*3600 + 15*60 + 30,
                "metric": "active_sessions",
                "description": "Cascading: Sessions invalidated after brute-force detection",
                "generator": lambda ts, idx: 35,
                "severity": DEFAULT_SEVERITY,
            }),
        ),
    ),
    "cache_collapse": Scenario(
        id="cache_collapse",
        name="Cache collapse — hit ratio collapse + memory pressure",
        severity="medium",
        days_required=1,
        category="same_day",
        components_touched=("cacheservice", "database"),
        primary_specs=(
            ("cacheservice", {
                "time_offset": 6*3600,
                "metric": "cache_misses",
                "description": "Cache miss spike to 95,000 — derives hit ratio ~5%",
                "generator": lambda ts, idx: 95000.0,
            }),
            ("cacheservice", {
                "time_offset": 17*3600,
                "metric": "memory_util_pct",
                "description": "Memory pressure — 97% nearing eviction",
                "generator": lambda ts, idx: 97.0,
            }),
            ("cacheservice", {
                "time_offset": 8*3600,
                "duration_seconds": 4*3600,
                "shape": "ramp_linear",
                "shape_params": {"start": 70.0, "end": 96.0},
                "metric": "memory_util_pct",
                "description": "Slow memory leak — utilization ramps 70% → 96% over 4h",
                "generator": lambda ts, idx: 70.0,
            }),
        ),
        cascade_specs=(
            ("cacheservice", {
                "time_offset": 6*3600 + 20,
                "metric": "cache_misses",
                "description": "Cascading: Cache miss surge before DB cascade lands",
                "generator": lambda ts, idx: 2400 + np.random.normal(0, 150),
                "severity": DEFAULT_SEVERITY,
            }),
            ("database", {
                "time_offset": 6*3600 + 30,
                "metric": "queries_per_sec",
                "description": "Cascading: Cache misses increase database queries",
                "generator": lambda ts, idx: 38000 + np.random.normal(0, 3000),
                "severity": DEFAULT_SEVERITY,
            }),
            ("database", {
                "time_offset": 6*3600 + 45,
                "metric": "read_latency_ms",
                "description": "Cascading: Database read latency increases from cache misses",
                "generator": lambda ts, idx: 45 + np.random.normal(0, 5),
                "severity": DEFAULT_SEVERITY,
            }),
        ),
    ),
    "api_cpu_saturation": Scenario(
        id="api_cpu_saturation",
        name="API gateway CPU saturation + retry storm",
        severity="medium",
        days_required=1,
        category="same_day",
        components_touched=("apigateway", "authservice", "cacheservice"),
        primary_specs=(
            ("apigateway", {
                "time_offset": 6*3600 + 30*60,
                "metric": "cpu_util_pct",
                "description": "CPU saturates at 100 %",
                "generator": lambda ts, idx: 100.0,
            }),
            ("apigateway", {
                "time_offset": 21*3600 + 45*60,
                "metric": "error_rate",
                "description": "5xx burst from bad config push — 12 %",
                "generator": lambda ts, idx: 0.12,
            }),
            ("apigateway", {
                "time_offset": 9*3600 + 30*60,
                "duration_seconds": 30*60,
                "shape": "sawtooth",
                "shape_params": {"period_s": 90, "amplitude": 100, "midline": 280},
                "metric": "avg_response_time_ms",
                "description": "GC sawtooth — response time oscillates 180↔380 ms every 90s for 30 min",
                "generator": lambda ts, idx: 180.0,
            }),
            ("apigateway", {
                "time_offset": 10*3600,
                "duration_seconds": 14*3600,
                "shape": "step",
                "metric": "avg_response_time_ms",
                "description": "Deploy regression — avg_response_time_ms step +30% to 234 ms (sustained)",
                "generator": lambda ts, idx: 234.0,
            }),
            ("apigateway", {
                "time_offset": 19*3600,
                "duration_seconds": 8*60,
                "shape": "sustained",
                "metric": "requests_per_sec",
                "description": "Retry storm — requests_per_sec sustained 2× baseline for 8 min",
                "generator": lambda ts, idx: 1600,
            }),
            ("apigateway", {
                "time_offset": 19*3600,
                "duration_seconds": 8*60,
                "shape": "ramp_linear",
                "shape_params": {"start": 0.05, "end": 0.30},
                "metric": "error_rate",
                "description": "Retry storm — error_rate climbs 5% → 30% as retries amplify failures",
                "generator": lambda ts, idx: 0.05,
            }),
        ),
        cascade_specs=(
            ("authservice", {
                "time_offset": 6*3600 + 30*60 + 12,
                "metric": "error_rate",
                "description": "Cascading: API gateway overload causes auth errors",
                "generator": lambda ts, idx: 0.35,
                "severity": DEFAULT_SEVERITY,
            }),
            ("cacheservice", {
                "time_offset": 6*3600 + 30*60 + 18,
                "metric": "error_rate",
                "description": "Cascading: API gateway overload causes cache errors",
                "generator": lambda ts, idx: 0.15,
                "severity": DEFAULT_SEVERITY,
            }),
        ),
    ),
    "db_stall": Scenario(
        id="db_stall",
        name="Database stall — read latency + backup window + disk exhaustion",
        severity="medium",
        days_required=1,
        category="same_day",
        components_touched=("database", "apigateway", "authservice", "mqservice"),
        primary_specs=(
            ("database", {
                "time_offset": 11*3600,
                "metric": "read_latency_ms",
                "description": "Read latency skyrockets to 360 ms",
                "generator": lambda ts, idx: 360.0,
            }),
            ("database", {
                "time_offset": 11*3600,
                "metric": "error_rate",
                "description": "Backend errors rise 23 %",
                "generator": lambda ts, idx: 0.23,
            }),
            ("database", {
                "time_offset": 4*3600,
                "metric": "connections",
                "description": "Backup-window connection pile-up — 6,800 connections",
                "generator": lambda ts, idx: 6800,
            }),
            ("database", {
                "time_offset": 4*3600,
                "metric": "write_latency_ms",
                "description": "Backup I/O contention — writes 45 ms",
                "generator": lambda ts, idx: 45.0,
            }),
            ("database", {
                "time_offset": 23*3600,
                "metric": "queries_per_sec",
                "description": "Nightly batch kickoff — 55k QPS",
                "generator": lambda ts, idx: 55000,
            }),
            ("database", {
                "time_offset": 0,
                "duration_seconds": SECONDS_PER_DAY,
                "shape": "ramp_linear",
                "shape_params": {"start": 8.0, "end": 100.0},
                "metric": "disk_used_pct",
                "description": "Disk exhaustion — disk_used_pct ramps 8% → 100% over 24h",
                "generator": lambda ts, idx: 8.0,
            }),
            ("database", {
                "time_offset": 16*3600,
                "duration_seconds": 6*3600,
                "shape": "ramp_linear",
                "shape_params": {"start": 3000.0, "end": 9500.0},
                "metric": "connections",
                "description": "Connection pool leak — connections ramp 3,000 → 9,500 over 6h",
                "generator": lambda ts, idx: 3000.0,
            }),
            ("database", {
                "time_offset": 18*3600,
                "duration_seconds": 10*60,
                "shape": "ramp_linear",
                "shape_params": {"start": 0.001, "end": 0.08},
                "metric": "error_rate",
                "description": "Brown-out — error_rate ramps 0.1% → 8% over 10 min",
                "generator": lambda ts, idx: 0.08,
            }),
            ("database", {
                "time_offset": 18*3600 + 10*60,
                "duration_seconds": 10*60,
                "shape": "ramp_linear",
                "shape_params": {"start": 0.08, "end": 0.001},
                "metric": "error_rate",
                "description": "Brown-out — error_rate recovers 8% → 0.1% over 10 min",
                "generator": lambda ts, idx: 0.08,
            }),
        ),
        cascade_specs=(
            ("apigateway", {
                "time_offset": 11*3600,
                "metric": "backend_latency_ms",
                "description": "Cascading: Database latency affects API backend",
                "generator": lambda ts, idx: 850 + np.random.normal(0, 50),
                "severity": DEFAULT_SEVERITY,
            }),
            ("apigateway", {
                "time_offset": 11*3600 + 5,
                "metric": "error_rate",
                "description": "Cascading: Database errors propagate to API",
                "generator": lambda ts, idx: 0.19,
                "severity": DEFAULT_SEVERITY,
            }),
            ("authservice", {
                "time_offset": 11*3600 + 10,
                "metric": "avg_auth_latency_ms",
                "description": "Cascading: Database issues slow auth queries",
                "generator": lambda ts, idx: 420 + np.random.normal(0, 30),
                "severity": DEFAULT_SEVERITY,
            }),
            ("mqservice", {
                "time_offset": 11*3600 + 20,
                "metric": "pending_messages",
                "description": "Cascading: DB stall causes MQ backpressure",
                "generator": lambda ts, idx: 250000 + np.random.normal(0, 5000),
                "severity": DEFAULT_SEVERITY,
            }),
        ),
    ),
    "mq_jam": Scenario(
        id="mq_jam",
        name="Message queue jam — pending message backlog",
        severity="medium",
        days_required=1,
        category="same_day",
        components_touched=("mqservice", "apigateway", "database", "authservice"),
        primary_specs=(
            ("mqservice", {
                "time_offset": 14*3600 + 30*60,
                "metric": "pending_messages",
                "description": "Pending messages jam to 1 M",
                "generator": lambda ts, idx: 1_000_000,
            }),
            ("mqservice", {
                "time_offset": 14*3600 + 30*60,
                "metric": "error_rate",
                "description": "Error rate jumps to 10 %",
                "generator": lambda ts, idx: 0.10,
            }),
            ("mqservice", {
                "time_offset": 12*3600 + 30*60,
                "metric": "dead_letter_queue",
                "description": "DLQ blow-up — 1,200 messages parked",
                "generator": lambda ts, idx: 1200,
            }),
        ),
        cascade_specs=(
            ("apigateway", {
                "time_offset": 14*3600 + 30*60 + 60,
                "metric": "avg_response_time_ms",
                "description": "Cascading: MQ backlog delays API responses",
                "generator": lambda ts, idx: 650 + np.random.normal(0, 40),
                "severity": DEFAULT_SEVERITY,
            }),
            ("database", {
                "time_offset": 14*3600 + 30*60 + 90,
                "metric": "connections",
                "description": "Cascading: MQ issues cause connection buildup",
                "generator": lambda ts, idx: 8500 + np.random.normal(0, 500),
                "severity": DEFAULT_SEVERITY,
            }),
            ("database", {
                "time_offset": 14*3600 + 30*60 + 95,
                "metric": "write_latency_ms",
                "description": "Cascading: MQ backpressure increases write latency",
                "generator": lambda ts, idx: 85 + np.random.normal(0, 10),
                "severity": DEFAULT_SEVERITY,
            }),
            ("authservice", {
                "time_offset": 14*3600 + 32*60 + 30,
                "metric": "avg_auth_latency_ms",
                "description": "Cascading: MQ jam delays session writes",
                "generator": lambda ts, idx: 280 + np.random.normal(0, 15),
                "severity": DEFAULT_SEVERITY,
            }),
        ),
    ),
    "lb_flapping": Scenario(
        id="lb_flapping",
        name="Load balancer flapping — TLS errors + health check failures",
        severity="medium",
        days_required=1,
        category="same_day",
        components_touched=("loadbalancer", "apigateway"),
        primary_specs=(
            ("loadbalancer", {
                "time_offset": 3*3600,
                "metric": "tls_handshake_errors",
                "description": "TLS handshake errors surge to 80/s (cert near-expiry warning)",
                "generator": lambda ts, idx: 80.0,
            }),
            ("loadbalancer", {
                "time_offset": 8*3600 + 15*60,
                "metric": "healthcheck_failures",
                "description": "Healthcheck failures jump to 12 (backend pool flapping)",
                "generator": lambda ts, idx: 12.0,
            }),
            ("loadbalancer", {
                "time_offset": 13*3600,
                "metric": "connection_resets",
                "description": "Connection resets spike to 450 (SYN flood-style burst)",
                "generator": lambda ts, idx: 450.0,
            }),
            ("loadbalancer", {
                "time_offset": 20*3600 + 30*60,
                "metric": "backend_5xx_per_sec",
                "description": "Backend 5xx jump to 75/s (region failover cascades 5xx upstream)",
                "generator": lambda ts, idx: 75.0,
            }),
        ),
        cascade_specs=(
            ("apigateway", {
                "time_offset": 8*3600 + 15*60 + 5,
                "metric": "active_connections",
                "description": "Cascading: LB withdraws traffic from a flapping backend pool",
                "generator": lambda ts, idx: 200 + np.random.normal(0, 25),
                "severity": DEFAULT_SEVERITY,
            }),
            ("apigateway", {
                "time_offset": 20*3600 + 30*60 + 10,
                "metric": "error_rate",
                "description": "Cascading: LB region failover propagates 5xx to gateway",
                "generator": lambda ts, idx: 0.09,
                "severity": DEFAULT_SEVERITY,
            }),
        ),
    ),
    "object_store_5xx": Scenario(
        id="object_store_5xx",
        name="Object store 5xx surge — bandwidth saturation",
        severity="medium",
        days_required=1,
        category="same_day",
        components_touched=("objectstore", "apigateway"),
        primary_specs=(
            ("objectstore", {
                "time_offset": 7*3600,
                "metric": "5xx_rate",
                "description": "Object store 5xx rate spikes to 14 % (upstream provider 5xx wave)",
                "generator": lambda ts, idx: 0.14,
            }),
            ("objectstore", {
                "time_offset": 12*3600,
                "metric": "bandwidth_mbps",
                "description": "Bandwidth saturates at 950 Mbps (batch export)",
                "generator": lambda ts, idx: 950.0,
            }),
            ("objectstore", {
                "time_offset": 18*3600 + 30*60,
                "metric": "get_latency_ms",
                "description": "GET latency tail at 380 ms (read-after-write)",
                "generator": lambda ts, idx: 380.0,
            }),
        ),
        cascade_specs=(
            ("apigateway", {
                "time_offset": 7*3600 + 20,
                "metric": "error_rate",
                "description": "Cascading: object store 5xx wave breaks dependent endpoints",
                "generator": lambda ts, idx: 0.06,
                "severity": DEFAULT_SEVERITY,
            }),
        ),
    ),
    "vectorstore_pressure": Scenario(
        id="vectorstore_pressure",
        name="Vector store index rebuild + recall degradation",
        severity="medium",
        days_required=1,
        category="same_day",
        components_touched=("vectorstore", "llm_analytics"),
        primary_specs=(
            ("vectorstore", {
                "time_offset": 10*3600 + 30*60,
                "metric": "ann_query_latency_ms",
                "description": "ANN query latency stalls at 280 ms (index rebuild)",
                "generator": lambda ts, idx: 280.0,
            }),
            ("vectorstore", {
                "time_offset": 15*3600,
                "metric": "recall_at_10",
                "description": "Recall@10 degrades to 0.62 after model swap",
                "generator": lambda ts, idx: 0.62,
            }),
        ),
        cascade_specs=(
            ("llm_analytics", {
                "time_offset": 10*3600 + 30*60 + 15,
                "metric": "avg_llm_latency_ms",
                "description": "Cascading: slow ANN retrieval drags LLM latency to 1,900 ms",
                "generator": lambda ts, idx: 1900 + np.random.normal(0, 80),
                "severity": DEFAULT_SEVERITY,
            }),
            ("llm_analytics", {
                "time_offset": 15*3600 + 30,
                "metric": "llm_api_error_rate",
                "description": "Cascading: low-recall results trigger LLM fallback retries (8 % errors)",
                "generator": lambda ts, idx: 0.08,
                "severity": DEFAULT_SEVERITY,
            }),
        ),
    ),
    "scheduler_overflow": Scenario(
        id="scheduler_overflow",
        name="Scheduler job overrun + queue overflow",
        severity="medium",
        days_required=1,
        category="same_day",
        components_touched=("scheduler", "database"),
        primary_specs=(
            ("scheduler", {
                "time_offset": 8*3600,
                "metric": "avg_job_duration_s",
                "description": "Job overrun — duration 4× baseline blocks next window",
                "generator": lambda ts, idx: 480.0,
            }),
            ("scheduler", {
                "time_offset": 8*3600 + 5*60,
                "metric": "missed_schedules",
                "description": "Missed schedule chain — 12 windows skipped after overrun",
                "generator": lambda ts, idx: 12.0,
            }),
            ("scheduler", {
                "time_offset": 10*3600,
                "metric": "jobs_queued",
                "description": "Job queue overflow — 2,500 jobs backlog",
                "generator": lambda ts, idx: 2500.0,
            }),
        ),
        cascade_specs=(
            ("database", {
                "time_offset": 10*3600 + 30,
                "metric": "connections",
                "description": "Cascading: Scheduler queue overflow drives DB connection buildup",
                "generator": lambda ts, idx: 7800 + np.random.normal(0, 400),
                "severity": DEFAULT_SEVERITY,
            }),
        ),
    ),
    "payment_5xx": Scenario(
        id="payment_5xx",
        name="Payment provider 5xx surge + fraud rule misfire",
        severity="medium",
        days_required=1,
        category="same_day",
        components_touched=("paymentservice", "apigateway"),
        primary_specs=(
            ("paymentservice", {
                "time_offset": 12*3600,
                "metric": "provider_5xx_rate",
                "description": "Stripe-style provider 5xx surge — 18% error rate",
                "generator": lambda ts, idx: 0.18,
            }),
            ("paymentservice", {
                "time_offset": 13*3600 + 30*60,
                "metric": "webhook_delivery_lag_s",
                "description": "Webhook delivery 5 min behind — provider backlog",
                "generator": lambda ts, idx: 300.0,
            }),
            ("paymentservice", {
                "time_offset": 15*3600,
                "metric": "auth_decline_rate",
                "description": "Decline-rate jump to 35% — fraud rule misfire",
                "generator": lambda ts, idx: 0.35,
            }),
        ),
        cascade_specs=(
            ("apigateway", {
                "time_offset": 12*3600 + 12,
                "metric": "error_rate",
                "description": "Cascading: Payment provider 5xx propagates to gateway",
                "generator": lambda ts, idx: 0.15,
                "severity": DEFAULT_SEVERITY,
            }),
        ),
    ),
    "idp_jwks_storm": Scenario(
        id="idp_jwks_storm",
        name="Identity provider JWKS cache miss storm",
        severity="medium",
        days_required=1,
        category="same_day",
        components_touched=("identityprovider", "authservice"),
        primary_specs=(
            ("identityprovider", {
                "time_offset": 4*3600,
                "metric": "jwks_fetch_latency_ms",
                "description": "JWKS cache miss storm — fetch latency 1500 ms at key rotation",
                "generator": lambda ts, idx: 1500.0,
            }),
            ("identityprovider", {
                "time_offset": 4*3600,
                "metric": "key_rotation_events",
                "description": "Concurrent key rotation events triggered cache miss storm",
                "generator": lambda ts, idx: 50.0,
            }),
            ("identityprovider", {
                "time_offset": 16*3600 + 30*60,
                "metric": "mfa_challenges_per_min",
                "description": "MFA SMS provider degradation — challenges drop to 0",
                "generator": lambda ts, idx: 0.0,
            }),
            ("identityprovider", {
                "time_offset": 19*3600,
                "metric": "failed_oidc_flows",
                "description": "SAML parse error spike — 120 failed flows from upstream IdP",
                "generator": lambda ts, idx: 120.0,
            }),
        ),
        cascade_specs=(
            ("authservice", {
                "time_offset": 4*3600 + 25,
                "metric": "login_success_rate",
                "description": "Cascading: JWKS fetch storm degrades auth verification — success ~45%",
                "generator": lambda ts, idx: 45 + np.random.normal(0, 2),
                "severity": DEFAULT_SEVERITY,
            }),
        ),
    ),
    "observability_lag": Scenario(
        id="observability_lag",
        name="Observability pipeline ingest lag + cardinality storm",
        severity="medium",
        days_required=1,
        category="same_day",
        components_touched=("observabilitypipeline", "mqservice"),
        primary_specs=(
            ("observabilitypipeline", {
                "time_offset": 9*3600,
                "metric": "ingest_lag_s",
                "description": "Ingestion lag grows to 240s — pipeline can't keep up",
                "generator": lambda ts, idx: 240.0,
            }),
            ("observabilitypipeline", {
                "time_offset": 13*3600,
                "metric": "dropped_metrics_per_sec",
                "description": "High-cardinality push drops 8,500 metrics/s",
                "generator": lambda ts, idx: 8500.0,
            }),
            ("observabilitypipeline", {
                "time_offset": 13*3600,
                "metric": "metrics_ingested_per_sec",
                "description": "Ingest rate collapses to 12,000/s during cardinality storm",
                "generator": lambda ts, idx: 12000.0,
            }),
            ("observabilitypipeline", {
                "time_offset": 20*3600,
                "metric": "pipeline_error_rate",
                "description": "Pipeline error rate 8% — downstream dashboards go stale",
                "generator": lambda ts, idx: 0.08,
            }),
        ),
        cascade_specs=(
            ("mqservice", {
                "time_offset": 9*3600 + 20,
                "metric": "pending_messages",
                "description": "Cascading: Telemetry pipeline lag backs up downstream queue",
                "generator": lambda ts, idx: 220000 + np.random.normal(0, 15000),
                "severity": DEFAULT_SEVERITY,
            }),
        ),
    ),
    # ------------------------------------------------------------------
    # Low-severity baseline (days_required=1, severity=low)
    # ------------------------------------------------------------------
    "monday_baseline": Scenario(
        id="monday_baseline",
        name="Monday-morning login burst + RPS spike",
        severity="low",
        days_required=1,
        category="same_day",
        components_touched=("authservice", "apigateway"),
        primary_specs=(
            ("authservice", {
                "time_offset": 9*3600,
                "metric": "login_attempts",
                "description": "Benign baseline shift: Monday morning login burst — 1,400 attempts/s",
                "generator": lambda ts, idx: 1400,
                "severity": "low",
            }),
            ("apigateway", {
                "time_offset": 9*3600,
                "metric": "requests_per_sec",
                "description": "Monday-morning thundering herd — 2,200 RPS spike",
                "generator": lambda ts, idx: 2200,
                "severity": "low",
            }),
        ),
        cascade_specs=(),
    ),
    # ------------------------------------------------------------------
    # Multi-day LLM catalog (severity=medium; per-scenario days_required:
    # 2 for llm_viral_surge_day2, 3 for llm_enterprise_onboarding,
    # 5 for llm_rate_limit_fallout, 6 for llm_weekend_batch,
    # 7 for llm_second_viral — each set to the day index of the
    # scenario's earliest in-range offset).
    # ------------------------------------------------------------------
    "llm_viral_surge_day2": Scenario(
        id="llm_viral_surge_day2",
        name="LLM viral surge — customer demo goes viral on Day 2",
        severity="medium",
        days_required=2,
        category="multi_day_llm",
        components_touched=("llm_analytics", "database", "cacheservice", "apigateway"),
        primary_specs=(
            ("llm_analytics", {
                "time_offset": 1*SECONDS_PER_DAY + 10*3600 + 15*60,
                "metric": "llm_requests_per_sec",
                "description": "Viral surge: Customer demo goes viral, 8× request spike",
                "generator": lambda ts, idx: 360,
            }),
            ("llm_analytics", {
                "time_offset": 1*SECONDS_PER_DAY + 10*3600 + 15*60,
                "metric": "input_tokens_per_sec",
                "description": "Token surge from viral traffic",
                "generator": lambda ts, idx: 185000,
            }),
            ("llm_analytics", {
                "time_offset": 1*SECONDS_PER_DAY + 10*3600 + 15*60,
                "metric": "output_tokens_per_sec",
                "description": "Output token surge from viral traffic",
                "generator": lambda ts, idx: 62000,
            }),
        ),
        cascade_specs=(
            ("database", {
                "time_offset": 1*SECONDS_PER_DAY + 10*3600 + 15*60 + 30,
                "metric": "queries_per_sec",
                "description": "Cascading: LLM surge increases database queries for context retrieval",
                "generator": lambda ts, idx: 48000 + np.random.normal(0, 4000),
                "severity": DEFAULT_SEVERITY,
            }),
            ("database", {
                "time_offset": 1*SECONDS_PER_DAY + 10*3600 + 15*60 + 45,
                "metric": "connections",
                "description": "Cascading: LLM service creates more database connections",
                "generator": lambda ts, idx: 7200 + np.random.normal(0, 400),
                "severity": DEFAULT_SEVERITY,
            }),
            ("cacheservice", {
                "time_offset": 1*SECONDS_PER_DAY + 10*3600 + 15*60 + 20,
                "metric": "cache_misses",
                "description": "Cascading: LLM context cache misses spike",
                "generator": lambda ts, idx: 1800 + np.random.normal(0, 150),
                "severity": DEFAULT_SEVERITY,
            }),
            ("apigateway", {
                "time_offset": 1*SECONDS_PER_DAY + 10*3600 + 15*60 + 10,
                "metric": "requests_per_sec",
                "description": "Cascading: LLM viral traffic increases API gateway load",
                "generator": lambda ts, idx: 2400 + np.random.normal(0, 200),
                "severity": DEFAULT_SEVERITY,
            }),
        ),
    ),
    "llm_enterprise_onboarding": Scenario(
        id="llm_enterprise_onboarding",
        name="LLM enterprise customer onboarding — large context windows on Day 3",
        severity="medium",
        days_required=3,
        category="multi_day_llm",
        components_touched=("llm_analytics", "vectorstore", "database", "cacheservice"),
        primary_specs=(
            ("llm_analytics", {
                "time_offset": 2*SECONDS_PER_DAY + 14*3600,
                "metric": "llm_requests_per_sec",
                "description": "Enterprise onboarding: Major customer launches AI features",
                "generator": lambda ts, idx: 285,
            }),
            ("llm_analytics", {
                "time_offset": 2*SECONDS_PER_DAY + 14*3600,
                "metric": "avg_context_window_size",
                "description": "Enterprise using large context windows for analytics",
                "generator": lambda ts, idx: 12500,
            }),
            ("llm_analytics", {
                "time_offset": 2*SECONDS_PER_DAY + 14*3600,
                "metric": "token_limit_hits_per_min",
                "description": "Token limits hit frequently during enterprise rollout",
                "generator": lambda ts, idx: 45,
            }),
            ("vectorstore", {
                "time_offset": 2*SECONDS_PER_DAY + 14*3600,
                "metric": "embeddings_per_sec",
                "description": "Enterprise onboarding drives embeddings to 350/s",
                "generator": lambda ts, idx: 350.0,
            }),
        ),
        cascade_specs=(
            ("database", {
                "time_offset": 2*SECONDS_PER_DAY + 14*3600 + 60,
                "metric": "read_latency_ms",
                "description": "Cascading: Large LLM context windows cause slow DB reads",
                "generator": lambda ts, idx: 85 + np.random.normal(0, 8),
                "severity": DEFAULT_SEVERITY,
            }),
            ("cacheservice", {
                "time_offset": 2*SECONDS_PER_DAY + 14*3600 + 35,
                "metric": "memory_util_pct",
                "description": "Cascading: LLM context caching increases memory pressure",
                "generator": lambda ts, idx: 92 + np.random.normal(0, 3),
                "severity": DEFAULT_SEVERITY,
            }),
        ),
    ),
    "llm_rate_limit_fallout": Scenario(
        id="llm_rate_limit_fallout",
        name="LLM provider rate limit fallout on Day 5",
        severity="medium",
        days_required=5,
        category="multi_day_llm",
        components_touched=("llm_analytics", "apigateway"),
        primary_specs=(
            ("llm_analytics", {
                "time_offset": 4*SECONDS_PER_DAY + 9*3600 + 30*60,
                "metric": "llm_api_error_rate",
                "description": "LLM provider rate limits hit, 18% error rate",
                "generator": lambda ts, idx: 0.18,
            }),
            ("llm_analytics", {
                "time_offset": 4*SECONDS_PER_DAY + 9*3600 + 30*60,
                "metric": "avg_llm_latency_ms",
                "description": "LLM latency spikes due to rate limiting",
                "generator": lambda ts, idx: 4200,
            }),
        ),
        cascade_specs=(
            ("apigateway", {
                "time_offset": 4*SECONDS_PER_DAY + 9*3600 + 30*60 + 8,
                "metric": "error_rate",
                "description": "Cascading: LLM API errors propagate to gateway",
                "generator": lambda ts, idx: 0.22,
                "severity": DEFAULT_SEVERITY,
            }),
        ),
    ),
    "llm_weekend_batch": Scenario(
        id="llm_weekend_batch",
        name="LLM weekend batch analytics job on Day 6",
        severity="medium",
        days_required=6,
        category="multi_day_llm",
        components_touched=("llm_analytics", "objectstore", "database", "cacheservice"),
        primary_specs=(
            ("llm_analytics", {
                "time_offset": 5*SECONDS_PER_DAY + 2*3600,
                "metric": "input_tokens_per_sec",
                "description": "Weekend batch analytics job processing historical data",
                "generator": lambda ts, idx: 320000,
            }),
            ("llm_analytics", {
                "time_offset": 5*SECONDS_PER_DAY + 2*3600,
                "metric": "context_overflow_rate",
                "description": "Context overflow from large batch documents",
                "generator": lambda ts, idx: 8.5,
            }),
            ("objectstore", {
                "time_offset": 5*SECONDS_PER_DAY + 2*3600,
                "metric": "bandwidth_mbps",
                "description": "Weekend batch export saturates object store at 1400 Mbps",
                "generator": lambda ts, idx: 1400.0,
            }),
        ),
        cascade_specs=(
            ("database", {
                "time_offset": 5*SECONDS_PER_DAY + 2*3600 + 15,
                "metric": "queries_per_sec",
                "description": "Cascading: Batch LLM processing hammers database",
                "generator": lambda ts, idx: 65000 + np.random.normal(0, 5000),
                "severity": DEFAULT_SEVERITY,
            }),
            ("database", {
                "time_offset": 5*SECONDS_PER_DAY + 2*3600 + 120,
                "metric": "cpu_util_pct",
                "description": "Cascading: Database CPU saturates from batch analytics",
                "generator": lambda ts, idx: 94 + np.random.normal(0, 2),
                "severity": DEFAULT_SEVERITY,
            }),
            ("cacheservice", {
                "time_offset": 5*SECONDS_PER_DAY + 2*3600 + 45,
                "metric": "cache_misses",
                "description": "Cascading: Batch job overwhelms cache — misses ~17,700 (hit ratio ~22%)",
                "generator": lambda ts, idx: 17727.0 + np.random.normal(0, 800),
                "severity": DEFAULT_SEVERITY,
            }),
        ),
    ),
    "llm_second_viral": Scenario(
        id="llm_second_viral",
        name="LLM second viral event — social media mention on Day 7",
        severity="medium",
        days_required=7,
        category="multi_day_llm",
        components_touched=("llm_analytics", "apigateway", "database", "cacheservice"),
        primary_specs=(
            ("llm_analytics", {
                "time_offset": 6*SECONDS_PER_DAY + 16*3600 + 45*60,
                "metric": "llm_requests_per_sec",
                "description": "Social media mention drives 10× traffic spike",
                "generator": lambda ts, idx: 450,
            }),
            ("llm_analytics", {
                "time_offset": 6*SECONDS_PER_DAY + 16*3600 + 45*60,
                "metric": "input_tokens_per_sec",
                "description": "Massive token usage from social traffic",
                "generator": lambda ts, idx: 420000,
            }),
            ("llm_analytics", {
                "time_offset": 6*SECONDS_PER_DAY + 16*3600 + 45*60,
                "metric": "output_tokens_per_sec",
                "description": "Output tokens surge from viral event",
                "generator": lambda ts, idx: 135000,
            }),
        ),
        cascade_specs=(
            ("apigateway", {
                "time_offset": 6*SECONDS_PER_DAY + 16*3600 + 45*60 + 5,
                "metric": "active_connections",
                "description": "Cascading: Viral LLM traffic maxes out connections",
                "generator": lambda ts, idx: 4800 + np.random.normal(0, 200),
                "severity": DEFAULT_SEVERITY,
            }),
            ("apigateway", {
                "time_offset": 6*SECONDS_PER_DAY + 16*3600 + 45*60 + 15,
                "metric": "cpu_util_pct",
                "description": "Cascading: API gateway CPU spikes from LLM traffic",
                "generator": lambda ts, idx: 87 + np.random.normal(0, 4),
                "severity": DEFAULT_SEVERITY,
            }),
            ("database", {
                "time_offset": 6*SECONDS_PER_DAY + 16*3600 + 45*60 + 25,
                "metric": "connections",
                "description": "Cascading: Database connection pool exhausted by LLM load",
                "generator": lambda ts, idx: 9800 + np.random.normal(0, 500),
                "severity": DEFAULT_SEVERITY,
            }),
            ("cacheservice", {
                "time_offset": 6*SECONDS_PER_DAY + 16*3600 + 45*60 + 18,
                "metric": "error_rate",
                "description": "Cascading: Cache service errors under LLM traffic",
                "generator": lambda ts, idx: 0.31,
                "severity": DEFAULT_SEVERITY,
            }),
        ),
    ),
    # ------------------------------------------------------------------
    # High-pressure cross-component scenarios (days_required=1, severity=high)
    # ------------------------------------------------------------------
    "regional_failover_storm": Scenario(
        id="regional_failover_storm",
        name="Regional failover storm — load balancer 5xx surge",
        severity="high",
        days_required=1,
        category="high_pressure",
        components_touched=("loadbalancer", "apigateway", "database", "authservice", "mqservice"),
        primary_specs=(
            ("loadbalancer", {
                "time_offset": 5*3600,
                "duration_seconds": 5*60,
                "shape": "ramp_linear",
                "shape_params": {"start": 1.5, "end": 220.0},
                "metric": "backend_5xx_per_sec",
                "description": "Regional failover storm — backend 5xx ramps to 220/s over 5 min",
                "generator": lambda ts, idx: 1.5,
                "severity": "high",
            }),
        ),
        cascade_specs=(
            ("apigateway", {
                "time_offset": 5*3600 + 30,
                "metric": "error_rate",
                "description": "Cascading: Regional failover floods gateway with 5xx (30%)",
                "generator": lambda ts, idx: 0.30,
                "severity": "high",
            }),
            ("database", {
                "time_offset": 5*3600 + 45,
                "metric": "connections",
                "description": "Cascading: Regional failover pile-up — DB connections climb to ~9,000",
                "generator": lambda ts, idx: 9000 + np.random.normal(0, 250),
                "severity": "high",
            }),
            ("authservice", {
                "time_offset": 5*3600 + 60,
                "metric": "error_rate",
                "description": "Cascading: Regional failover propagates auth errors (~25%)",
                "generator": lambda ts, idx: 0.25,
                "severity": "high",
            }),
            ("mqservice", {
                "time_offset": 5*3600 + 90,
                "metric": "pending_messages",
                "description": "Cascading: Regional failover backs up queue — ~500,000 pending",
                "generator": lambda ts, idx: 500000 + np.random.normal(0, 12000),
                "severity": "high",
            }),
        ),
    ),
    "cache_db_meltdown": Scenario(
        id="cache_db_meltdown",
        name="Coordinated cache + DB meltdown",
        severity="high",
        days_required=1,
        category="high_pressure",
        components_touched=("cacheservice", "database", "llm_analytics", "apigateway"),
        primary_specs=(
            ("cacheservice", {
                "time_offset": 11*3600 + 30*60,
                "duration_seconds": 10*60,
                "shape": "ramp_linear",
                "shape_params": {"start": 80.0, "end": 99.5},
                "metric": "memory_util_pct",
                "description": "Cache+DB meltdown — cache memory saturates 80% → 99.5% over 10 min",
                "generator": lambda ts, idx: 80.0,
                "severity": "high",
            }),
            ("database", {
                "time_offset": 11*3600 + 30*60,
                "duration_seconds": 10*60,
                "shape": "ramp_linear",
                "shape_params": {"start": 12.0, "end": 800.0},
                "metric": "read_latency_ms",
                "description": "Cache+DB meltdown — DB read latency climbs to 800 ms over 10 min",
                "generator": lambda ts, idx: 12.0,
                "severity": "high",
            }),
        ),
        cascade_specs=(
            ("llm_analytics", {
                "time_offset": 11*3600 + 30*60 + 30,
                "metric": "avg_llm_latency_ms",
                "description": "Cascading: Cache+DB meltdown doubles LLM latency to ~1,700 ms",
                "generator": lambda ts, idx: 1700 + np.random.normal(0, 90),
                "severity": "high",
            }),
            ("apigateway", {
                "time_offset": 11*3600 + 30*60 + 45,
                "metric": "backend_latency_ms",
                "description": "Cascading: Cache+DB meltdown drags gateway backend latency to ~950 ms",
                "generator": lambda ts, idx: 950 + np.random.normal(0, 60),
                "severity": "high",
            }),
        ),
    ),
    "llm_provider_outage": Scenario(
        id="llm_provider_outage",
        name="LLM provider sustained outage",
        severity="high",
        days_required=1,
        category="high_pressure",
        components_touched=("llm_analytics", "apigateway", "cacheservice"),
        primary_specs=(
            ("llm_analytics", {
                "time_offset": 20*3600,
                "duration_seconds": 15*60,
                "shape": "ramp_linear",
                "shape_params": {"start": 0.05, "end": 0.60},
                "metric": "llm_api_error_rate",
                "description": "LLM provider sustained outage — error rate ramps 5% → 60% over 15 min",
                "generator": lambda ts, idx: 0.05,
                "severity": "high",
            }),
            ("llm_analytics", {
                "time_offset": 20*3600,
                "duration_seconds": 15*60,
                "shape": "ramp_linear",
                "shape_params": {"start": 900.0, "end": 8000.0},
                "metric": "avg_llm_latency_ms",
                "description": "LLM provider sustained outage — latency climbs to 8,000 ms over 15 min",
                "generator": lambda ts, idx: 900.0,
                "severity": "high",
            }),
        ),
        cascade_specs=(
            ("apigateway", {
                "time_offset": 20*3600 + 15,
                "metric": "error_rate",
                "description": "Cascading: LLM outage propagates to gateway (~25%)",
                "generator": lambda ts, idx: 0.25,
                "severity": "high",
            }),
            ("cacheservice", {
                "time_offset": 20*3600 + 30,
                "metric": "cache_misses",
                "description": "Cascading: LLM outage drives context cache miss surge (~3,000)",
                "generator": lambda ts, idx: 3000 + np.random.normal(0, 200),
                "severity": "high",
            }),
        ),
    ),
    "gateway_ddos": Scenario(
        id="gateway_ddos",
        name="Gateway DDoS-style saturation",
        severity="high",
        days_required=1,
        category="high_pressure",
        components_touched=("apigateway", "authservice", "database", "mqservice"),
        primary_specs=(
            ("apigateway", {
                "time_offset": 16*3600,
                "duration_seconds": 10*60,
                "shape": "sustained",
                "metric": "requests_per_sec",
                "description": "Gateway DDoS saturation — requests sustained at 5,000/s for 10 min",
                "generator": lambda ts, idx: 5000,
                "severity": "high",
            }),
            ("apigateway", {
                "time_offset": 16*3600,
                "duration_seconds": 10*60,
                "shape": "sustained",
                "metric": "cpu_util_pct",
                "description": "Gateway DDoS saturation — CPU pinned at 99% for 10 min",
                "generator": lambda ts, idx: 99.0,
                "severity": "high",
            }),
        ),
        cascade_specs=(
            ("authservice", {
                "time_offset": 16*3600 + 60,
                "metric": "avg_auth_latency_ms",
                "description": "Cascading: Gateway saturation slows auth path to ~600 ms",
                "generator": lambda ts, idx: 600 + np.random.normal(0, 25),
                "severity": "high",
            }),
            ("database", {
                "time_offset": 16*3600 + 90,
                "metric": "cpu_util_pct",
                "description": "Cascading: Gateway saturation drives DB CPU to ~92%",
                "generator": lambda ts, idx: 92 + np.random.normal(0, 2),
                "severity": "high",
            }),
            ("mqservice", {
                "time_offset": 16*3600 + 120,
                "metric": "pending_messages",
                "description": "Cascading: Gateway saturation queues messages — ~800,000 pending",
                "generator": lambda ts, idx: 800000 + np.random.normal(0, 15000),
                "severity": "high",
            }),
        ),
    ),
    "storage_layer_pressure": Scenario(
        id="storage_layer_pressure",
        name="Storage layer pressure — PUT latency + 5xx surge",
        severity="high",
        days_required=1,
        category="high_pressure",
        components_touched=("objectstore", "database", "apigateway"),
        primary_specs=(
            ("objectstore", {
                "time_offset": 22*3600,
                "duration_seconds": 10*60,
                "shape": "ramp_linear",
                "shape_params": {"start": 60.0, "end": 700.0},
                "metric": "put_latency_ms",
                "description": "Storage layer pressure — PUT latency climbs 60 → 700 ms over 10 min",
                "generator": lambda ts, idx: 60.0,
                "severity": "high",
            }),
            ("objectstore", {
                "time_offset": 22*3600,
                "duration_seconds": 10*60,
                "shape": "sustained",
                "metric": "5xx_rate",
                "description": "Storage layer pressure — object store 5xx surge to 25% for 10 min",
                "generator": lambda ts, idx: 0.25,
                "severity": "high",
            }),
        ),
        cascade_specs=(
            ("database", {
                "time_offset": 22*3600 + 30,
                "metric": "write_latency_ms",
                "description": "Cascading: Storage pressure drags DB write latency to ~90 ms",
                "generator": lambda ts, idx: 90 + np.random.normal(0, 6),
                "severity": "high",
            }),
            ("apigateway", {
                "time_offset": 22*3600 + 45,
                "metric": "error_rate",
                "description": "Cascading: Storage 5xx surge propagates to gateway (~15%)",
                "generator": lambda ts, idx: 0.15,
                "severity": "high",
            }),
        ),
    ),
    "deploy_bad_canary_rollback": Scenario(
        id="deploy_bad_canary_rollback",
        name="Bad canary deploy + rollback — gateway error/latency plateau",
        severity="high",
        days_required=1,
        category="high_pressure",
        components_touched=("apigateway", "authservice", "cacheservice", "database"),
        primary_specs=(
            ("apigateway", {
                "time_offset": 15*3600,
                "duration_seconds": 480,
                "shape": "sustained",
                "metric": "error_rate",
                "description": "Bad canary deploy — gateway error rate plateau at 18% for 8 min until rollback",
                "generator": lambda ts, idx: 0.18,
                "severity": "high",
            }),
            ("apigateway", {
                "time_offset": 15*3600,
                "duration_seconds": 480,
                "shape": "sustained",
                "metric": "backend_latency_ms",
                "description": "Bad canary deploy — gateway backend latency plateau at 480 ms for 8 min until rollback",
                "generator": lambda ts, idx: 480.0,
                "severity": "high",
            }),
            ("apigateway", {
                "time_offset": 15*3600,
                "duration_seconds": 480,
                "shape": "sustained",
                "metric": "requests_per_sec",
                "description": "Bad canary deploy — retry-driven RPS plateau at 1,100 for 8 min until rollback",
                "generator": lambda ts, idx: 1100.0,
                "severity": "high",
            }),
        ),
        cascade_specs=(
            ("authservice", {
                "time_offset": 15*3600 + 30,
                "metric": "login_success_rate",
                "description": "Cascading: bad canary drops login success rate to 92%",
                "generator": lambda ts, idx: 92.0,
                "severity": "high",
            }),
            ("cacheservice", {
                "time_offset": 15*3600 + 60,
                "metric": "cache_misses",
                "description": "Cascading: rollback restart causes cold-cache miss spike (~1,200)",
                "generator": lambda ts, idx: 1200 + np.random.normal(0, 50),
                "severity": "high",
            }),
            ("database", {
                "time_offset": 15*3600 + 90,
                "metric": "connections",
                "description": "Cascading: retry pile-up drives DB connections to ~5,800",
                "generator": lambda ts, idx: 5800 + np.random.normal(0, 150),
                "severity": "high",
            }),
        ),
    ),
    "dns_provider_outage": Scenario(
        id="dns_provider_outage",
        name="External DNS provider outage — TLS/handshake plateau",
        severity="high",
        days_required=1,
        category="high_pressure",
        components_touched=("loadbalancer", "apigateway", "identityprovider", "paymentservice"),
        primary_specs=(
            ("loadbalancer", {
                "time_offset": 11*3600,
                "duration_seconds": 360,
                "shape": "sustained",
                "metric": "tls_handshake_errors",
                "description": "DNS provider outage — TLS handshake errors plateau at 45/s for 6 min",
                "generator": lambda ts, idx: 45.0,
                "severity": "high",
            }),
            ("loadbalancer", {
                "time_offset": 11*3600,
                "duration_seconds": 360,
                "shape": "sustained",
                "metric": "backend_5xx_per_sec",
                "description": "DNS provider outage — backend 5xx plateau at 80/s for 6 min",
                "generator": lambda ts, idx: 80.0,
                "severity": "high",
            }),
            ("loadbalancer", {
                "time_offset": 11*3600,
                "duration_seconds": 360,
                "shape": "sustained",
                "metric": "healthcheck_failures",
                "description": "DNS provider outage — health check failures plateau at 8/s for 6 min",
                "generator": lambda ts, idx: 8.0,
                "severity": "high",
            }),
        ),
        cascade_specs=(
            ("identityprovider", {
                "time_offset": 11*3600 + 30,
                "metric": "failed_oidc_flows",
                "description": "Cascading: federated OIDC callback DNS lookups fail (~150)",
                "generator": lambda ts, idx: 150 + np.random.normal(0, 8),
                "severity": "high",
            }),
            ("paymentservice", {
                "time_offset": 11*3600 + 60,
                "metric": "provider_5xx_rate",
                "description": "Cascading: payment provider lookups fail — 5xx rate 32%",
                "generator": lambda ts, idx: 0.32,
                "severity": "high",
            }),
            ("apigateway", {
                "time_offset": 11*3600 + 45,
                "metric": "error_rate",
                "description": "Cascading: DNS outage propagates to gateway (~28%)",
                "generator": lambda ts, idx: 0.28,
                "severity": "high",
            }),
        ),
    ),
    "network_partition_az_split": Scenario(
        id="network_partition_az_split",
        name="Intra-region AZ network partition — replication + cross-AZ RPC stall",
        severity="high",
        days_required=1,
        category="high_pressure",
        components_touched=("database", "mqservice", "apigateway", "authservice"),
        # T0=18:20: shifted from the originally-designed 18:00 to clear
        # db_stall's pre-existing 18:00-18:20 brown-out ramp on
        # database.error_rate. All other design parameters (sharp start,
        # 4-min plateau, sharp end, magnitudes, components, metrics) are
        # preserved.
        primary_specs=(
            ("database", {
                "time_offset": 18*3600 + 20*60,
                "duration_seconds": 240,
                "shape": "sustained",
                "metric": "replication_lag_s",
                "description": "AZ partition — replication lag plateau at 18 s for 4 min until heal",
                "generator": lambda ts, idx: 18.0,
                "severity": "high",
            }),
            ("database", {
                "time_offset": 18*3600 + 20*60,
                "duration_seconds": 240,
                "shape": "sustained",
                "metric": "error_rate",
                "description": "AZ partition — DB error rate plateau at 30% for 4 min until heal",
                "generator": lambda ts, idx: 0.30,
                "severity": "high",
            }),
            ("mqservice", {
                "time_offset": 18*3600 + 20*60,
                "duration_seconds": 240,
                "shape": "sustained",
                "metric": "consumer_lag",
                "description": "AZ partition — MQ consumer lag plateau at 12,000 for 4 min until heal",
                "generator": lambda ts, idx: 12000.0,
                "severity": "high",
            }),
            ("mqservice", {
                "time_offset": 18*3600 + 20*60,
                "duration_seconds": 240,
                "shape": "sustained",
                "metric": "unacked_messages",
                "description": "AZ partition — unacked messages plateau at 4,500 for 4 min until heal",
                "generator": lambda ts, idx: 4500.0,
                "severity": "high",
            }),
        ),
        cascade_specs=(
            ("apigateway", {
                "time_offset": 18*3600 + 20*60 + 30,
                "metric": "backend_latency_ms",
                "description": "Cascading: cross-AZ RPC drags gateway backend latency to ~380 ms",
                "generator": lambda ts, idx: 380 + np.random.normal(0, 20),
                "severity": "high",
            }),
            ("authservice", {
                "time_offset": 18*3600 + 20*60 + 60,
                "metric": "error_rate",
                "description": "Cascading: AZ partition fails auth replica reads (~22%)",
                "generator": lambda ts, idx: 0.22,
                "severity": "high",
            }),
        ),
    ),
    "cache_leak_restart": Scenario(
        id="cache_leak_restart",
        name="Cache memory-leak death march → forced restart",
        severity="medium",
        days_required=2,
        category="multi_day_cascade",
        components_touched=(
            "cacheservice", "database", "apigateway", "mqservice",
        ),
        # Order matches the historic anoms_cache tail order so that, on
        # tail-append in main(), component_anomalies["cacheservice"] reads
        # identically to today's flat list.
        primary_specs=(
            ("cacheservice", {
                "time_offset": 1*SECONDS_PER_DAY,                 # Day 2 00:00
                "duration_seconds": 51*3600,
                "shape": "ramp_linear",
                "shape_params": {"start": 50.0, "end": 95.0},
                "metric": "memory_util_pct",
                "description": "Cache memory leak — slow growth 50%→95% over 51h",
                "generator": lambda ts, idx: 50.0,
            }),
            ("cacheservice", {
                "time_offset": 2*SECONDS_PER_DAY + 12*3600,       # Day 3 12:00
                "duration_seconds": 12*3600,
                "shape": "ramp_linear",
                "shape_params": {"start": 682.0, "end": 3333.0},
                "metric": "cache_misses",
                "description": "Cache eviction cascade — misses ramp 682→3,333 (hit ratio 88%→60%) over 12h",
                "generator": lambda ts, idx: 682.0,
            }),
            ("cacheservice", {
                "time_offset": 3*SECONDS_PER_DAY + 3*3600,        # Day 4 03:00 — forced restart
                "duration_seconds": 300,
                "shape": "step",
                "metric": "memory_util_pct",
                "description": "Cache forced restart — memory reset to 55%",
                "generator": lambda ts, idx: 55.0,
            }),
            ("cacheservice", {
                "time_offset": 3*SECONDS_PER_DAY + 3*3600,
                "duration_seconds": 300,
                "shape": "step",
                "metric": "cache_misses",
                "description": "Cache cold start after restart — misses ~95,000 (hit ratio ~5%)",
                "generator": lambda ts, idx: 95000.0,
            }),
            ("cacheservice", {
                "time_offset": 3*SECONDS_PER_DAY + 3*3600,
                "duration_seconds": 300,
                "shape": "step",
                "metric": "error_rate",
                "description": "Cache warm-up errors during restart",
                "generator": lambda ts, idx: 0.12,
            }),
        ),
        # Order mirrors today's register_default_cascades() body for the
        # Scenario A block, so cascading_anomalies[target] order is identical.
        cascade_specs=(
            ("database", {
                "time_offset": 1*SECONDS_PER_DAY + 12*3600,
                "metric": "queries_per_sec",
                "description": "Cascading: Rising cache miss volume — DB queries climb to ~32k",
                "generator": lambda ts, idx: 32000 + np.random.normal(0, 1500),
                "severity": DEFAULT_SEVERITY,
            }),
            ("database", {
                "time_offset": 2*SECONDS_PER_DAY + 12*3600 + 30*60,
                "metric": "queries_per_sec",
                "description": "Cascading: Cache hit-ratio decline — DB queries climb to ~42k",
                "generator": lambda ts, idx: 42000 + np.random.normal(0, 2000),
                "severity": DEFAULT_SEVERITY,
            }),
            ("database", {
                "time_offset": 2*SECONDS_PER_DAY + 12*3600 + 30*60,
                "metric": "read_latency_ms",
                "description": "Cascading: Cache hit-ratio decline pushes DB read latency to ~55 ms",
                "generator": lambda ts, idx: 55 + np.random.normal(0, 4),
                "severity": DEFAULT_SEVERITY,
            }),
            # Cold-start stampede: cascade specs are single-row step writes
            # (register_cascade has no shape support), so this collapses to a
            # single ~60k step at restart + 5 min.
            ("database", {
                "time_offset": 3*SECONDS_PER_DAY + 3*3600 + 300,
                "metric": "queries_per_sec",
                "description": "Cascading: Cache cold-start stampede — DB queries ~60k",
                "generator": lambda ts, idx: 60000 + np.random.normal(0, 2500),
                "severity": DEFAULT_SEVERITY,
            }),
            ("apigateway", {
                "time_offset": 3*SECONDS_PER_DAY + 3*3600 + 300,
                "metric": "error_rate",
                "description": "Cascading: Cache restart causes brief gateway errors (~8%)",
                "generator": lambda ts, idx: 0.08,
                "severity": DEFAULT_SEVERITY,
            }),
            ("mqservice", {
                "time_offset": 3*SECONDS_PER_DAY + 3*3600 + 300,
                "metric": "pending_messages",
                "description": "Cascading: Cache restart backs up MQ — ~180,000 pending",
                "generator": lambda ts, idx: 180000 + np.random.normal(0, 6000),
                "severity": DEFAULT_SEVERITY,
            }),
        ),
    ),
    "jwks_rotation_chaos": Scenario(
        id="jwks_rotation_chaos",
        name="Certificate / JWKS rotation chaos",
        severity="medium",
        days_required=3,
        category="multi_day_cascade",
        components_touched=(
            "loadbalancer", "identityprovider", "authservice",
            "apigateway", "paymentservice", "cacheservice",
        ),
        # Order matches anoms_lb / anoms_idp / anoms_auth tail order in the
        # pre-migration file (lb pair, idp triple, auth single) so the
        # component_anomalies tail order for each is identical post-walk.
        primary_specs=(
            ("loadbalancer", {
                "time_offset": 2*SECONDS_PER_DAY + 9*3600,        # Day 3 09:00
                "duration_seconds": 6*3600,
                "shape": "ramp_linear",
                "shape_params": {"start": 2.0, "end": 25.0},
                "metric": "tls_handshake_errors",
                "description": "TLS cert validation flapping at POPs — errors ramp 2→25/s",
                "generator": lambda ts, idx: 2.0,
            }),
            ("loadbalancer", {
                "time_offset": 4*SECONDS_PER_DAY + 2*3600,        # Day 5 02:00 — hard expiry
                "duration_seconds": 2*3600,
                "shape": "step",
                "metric": "tls_handshake_errors",
                "description": "Hard cert expiration — TLS errors spike to 200/s",
                "generator": lambda ts, idx: 200.0,
            }),
            ("identityprovider", {
                "time_offset": 3*SECONDS_PER_DAY + 9*3600,        # Day 4 09:00
                "duration_seconds": 8*3600,
                "shape": "sustained",
                "metric": "jwks_fetch_latency_ms",
                "description": "JWKS fetch latency sustained at 800 ms — pre-rotation slowdown",
                "generator": lambda ts, idx: 800.0,
            }),
            ("identityprovider", {
                "time_offset": 4*SECONDS_PER_DAY + 2*3600,        # Day 5 02:00 — hard expiry
                "duration_seconds": 2*3600,
                "shape": "step",
                "metric": "failed_oidc_flows",
                "description": "Cert expiry — OIDC flow failures spike to 800",
                "generator": lambda ts, idx: 800.0,
            }),
            ("identityprovider", {
                "time_offset": 4*SECONDS_PER_DAY + 2*3600,
                "duration_seconds": 2*3600,
                "shape": "step",
                "metric": "key_rotation_events",
                "description": "Emergency key rotation — 50 events during expiry window",
                "generator": lambda ts, idx: 50.0,
            }),
            ("authservice", {
                "time_offset": 3*SECONDS_PER_DAY + 18*3600,       # Day 4 18:00
                "duration_seconds": 6*3600,
                "shape": "ramp_linear",
                "shape_params": {"start": 98.0, "end": 85.0},
                "metric": "login_success_rate",
                "description": "Login success rate decline 98%→85% as cert chain degrades",
                "generator": lambda ts, idx: 98.0,
            }),
        ),
        cascade_specs=(
            ("apigateway", {
                "time_offset": 2*SECONDS_PER_DAY + 9*3600 + 30*60,
                "metric": "error_rate",
                "description": "Cascading: Sporadic TLS failures propagate to gateway (~5%)",
                "generator": lambda ts, idx: 0.05,
                "severity": DEFAULT_SEVERITY,
            }),
            ("authservice", {
                "time_offset": 3*SECONDS_PER_DAY + 9*3600 + 30*60,
                "metric": "avg_auth_latency_ms",
                "description": "Cascading: Slow JWKS fetch raises auth latency to ~350 ms",
                "generator": lambda ts, idx: 350 + np.random.normal(0, 15),
                "severity": DEFAULT_SEVERITY,
            }),
            ("paymentservice", {
                "time_offset": 3*SECONDS_PER_DAY + 18*3600 + 30*60,
                "metric": "provider_5xx_rate",
                "description": "Cascading: Broken auth chain — payment 5xx ~8%",
                "generator": lambda ts, idx: 0.08,
                "severity": DEFAULT_SEVERITY,
            }),
            ("apigateway", {
                "time_offset": 4*SECONDS_PER_DAY + 2*3600 + 300,
                "metric": "error_rate",
                "description": "Cascading: Mass TLS failure floods gateway (~28%)",
                "generator": lambda ts, idx: 0.28,
                "severity": DEFAULT_SEVERITY,
            }),
            ("paymentservice", {
                "time_offset": 4*SECONDS_PER_DAY + 2*3600 + 600,
                "metric": "auth_decline_rate",
                "description": "Cascading: Unverifiable tokens drive declines to ~45%",
                "generator": lambda ts, idx: 0.45,
                "severity": DEFAULT_SEVERITY,
            }),
            # Constant (not noisy) to preserve the seeded global RNG state for
            # downstream components.
            ("cacheservice", {
                "time_offset": 4*SECONDS_PER_DAY + 2*3600 + 900,
                "metric": "cache_misses",
                "description": "Cascading: Mass session re-auth — cache misses ~3,500",
                "generator": lambda ts, idx: 3500,
                "severity": DEFAULT_SEVERITY,
            }),
        ),
    ),
    "db_disk_exhaustion": Scenario(
        id="db_disk_exhaustion",
        name="Database disk + write-latency exhaustion",
        severity="medium",
        days_required=2,
        category="multi_day_cascade",
        components_touched=(
            "database", "scheduler", "observabilitypipeline",
            "mqservice", "apigateway",
        ),
        primary_specs=(
            ("database", {
                "time_offset": 1*SECONDS_PER_DAY,                 # Day 2 00:00
                "duration_seconds": 96*3600,
                "shape": "ramp_linear",
                "shape_params": {"start": 65.0, "end": 92.0},
                "metric": "disk_used_pct",
                "description": "Database disk slow exhaustion 65%→92% over 96h",
                "generator": lambda ts, idx: 65.0,
            }),
            ("database", {
                "time_offset": 4*SECONDS_PER_DAY + 6*3600,        # Day 5 06:00
                "duration_seconds": 12*3600,
                "shape": "ramp_linear",
                "shape_params": {"start": 12.0, "end": 90.0},
                "metric": "write_latency_ms",
                "description": "Database write latency drift 12→90 ms as I/O saturates",
                "generator": lambda ts, idx: 12.0,
            }),
            ("database", {
                "time_offset": 5*SECONDS_PER_DAY + 3*3600,        # Day 6 03:00 — log truncation
                "duration_seconds": 20*60,
                "shape": "step",
                "metric": "error_rate",
                "description": "Emergency log truncation — write errors spike to 12%",
                "generator": lambda ts, idx: 0.12,
            }),
            ("database", {
                "time_offset": 5*SECONDS_PER_DAY + 3*3600,
                "duration_seconds": 20*60,
                "shape": "step",
                "metric": "disk_used_pct",
                "description": "Database log truncation — disk drops to 78%",
                "generator": lambda ts, idx: 78.0,
            }),
            ("database", {
                "time_offset": 5*SECONDS_PER_DAY + 3*3600,
                "duration_seconds": 20*60,
                "shape": "step",
                "metric": "write_latency_ms",
                "description": "Database write latency partial relief — 30 ms post-truncation",
                "generator": lambda ts, idx: 30.0,
            }),
        ),
        cascade_specs=(
            ("scheduler", {
                "time_offset": 1*SECONDS_PER_DAY + 30*60,
                "metric": "jobs_failed_per_min",
                "description": "Cascading: Slow disk fails background-job writes (~8/min)",
                "generator": lambda ts, idx: 8,
                "severity": DEFAULT_SEVERITY,
            }),
            ("observabilitypipeline", {
                "time_offset": 4*SECONDS_PER_DAY + 6*3600 + 30*60,
                "metric": "ingest_lag_s",
                "description": "Cascading: DB write latency drift lags observability ingest to ~180s",
                "generator": lambda ts, idx: 180,
                "severity": DEFAULT_SEVERITY,
            }),
            ("mqservice", {
                "time_offset": 4*SECONDS_PER_DAY + 12*3600,
                "metric": "pending_messages",
                "description": "Cascading: Consumers blocked on DB writes — ~320k pending",
                "generator": lambda ts, idx: 320000 + np.random.normal(0, 10000),
                "severity": DEFAULT_SEVERITY,
            }),
            # Constant (not noisy) to preserve the seeded global RNG state for
            # downstream components.
            ("apigateway", {
                "time_offset": 5*SECONDS_PER_DAY + 3*3600 + 300,
                "metric": "backend_latency_ms",
                "description": "Cascading: DB truncation event raises backend latency to ~720 ms",
                "generator": lambda ts, idx: 720,
                "severity": DEFAULT_SEVERITY,
            }),
            ("apigateway", {
                "time_offset": 5*SECONDS_PER_DAY + 3*3600 + 300,
                "metric": "error_rate",
                "description": "Cascading: DB error spike propagates to gateway (~15%)",
                "generator": lambda ts, idx: 0.15,
                "severity": DEFAULT_SEVERITY,
            }),
        ),
    ),
}


def _validate_scenarios_registry() -> None:
    """Import-time invariants for ``SCENARIOS``.

    Mirrors the registry tests in ``tests/test_scenarios.py`` so any drift
    while editing the registry is caught at module load. Kept as a function
    so loop locals don't leak into the module namespace.
    """
    known_components = set(COMPONENTS.keys())
    for slug, scenario in SCENARIOS.items():
        if scenario.id != slug:
            raise ValueError(
                f"SCENARIOS[{slug!r}].id is {scenario.id!r}; id must equal "
                f"the registry key"
            )
        if scenario.severity not in {"low", "medium", "high"}:
            raise ValueError(
                f"SCENARIOS[{slug!r}].severity {scenario.severity!r} must be "
                "one of low / medium / high"
            )
        if not isinstance(scenario.days_required, int) or scenario.days_required < 1:
            raise ValueError(
                f"SCENARIOS[{slug!r}].days_required {scenario.days_required!r} "
                "must be a positive int (the minimum --duration-days at which "
                "any of the scenario's specs become in range)"
            )
        unknown_touched = set(scenario.components_touched) - known_components
        if unknown_touched:
            raise ValueError(
                f"SCENARIOS[{slug!r}].components_touched contains unknown "
                f"component(s): {sorted(unknown_touched)}"
            )
        # days_required must equal the day index (1-based) of the earliest
        # time_offset across primary and cascade specs. Setting it too high
        # silently drops in-range specs at the requested --duration-days;
        # too low activates the scenario before any spec is in range.
        offsets = [p["time_offset"] for _, p in scenario.primary_specs]
        offsets += [c["time_offset"] for _, c in scenario.cascade_specs]
        if offsets:
            min_day_required = min(offsets) // SECONDS_PER_DAY + 1
            if scenario.days_required != min_day_required:
                raise ValueError(
                    f"SCENARIOS[{slug!r}].days_required={scenario.days_required} "
                    f"must equal the day index of its earliest spec offset "
                    f"({min_day_required}). Too high silently drops in-range "
                    f"specs at the requested --duration-days; too low activates "
                    f"the scenario before any spec is in range."
                )
        # components_touched must equal exactly the set of components
        # referenced by primary_specs + cascade_specs. Under-claiming
        # silently drops the scenario under a narrow --components allowlist;
        # over-claiming dilutes the filter so the scenario fires for
        # allowlists that contain none of its actual components.
        referenced_components = {c for c, _ in scenario.primary_specs}
        referenced_components.update(c for c, _ in scenario.cascade_specs)
        declared_components = set(scenario.components_touched)
        if referenced_components != declared_components:
            missing = sorted(referenced_components - declared_components)
            extras = sorted(declared_components - referenced_components)
            raise ValueError(
                f"SCENARIOS[{slug!r}].components_touched="
                f"{sorted(declared_components)} must equal components "
                f"referenced by specs={sorted(referenced_components)}; "
                f"missing={missing} extras={extras}. Under-claiming silently "
                f"drops the scenario under a narrow --components allowlist; "
                f"over-claiming dilutes the filter."
            )
        valid_severities = {"low", "medium", "high"}
        for component, spec in scenario.primary_specs:
            if component not in known_components:
                raise ValueError(
                    f"SCENARIOS[{slug!r}].primary_specs references unknown "
                    f"component {component!r}"
                )
            if "severity" in spec and spec["severity"] not in valid_severities:
                raise ValueError(
                    f"SCENARIOS[{slug!r}].primary_specs entry for component "
                    f"{component!r} has severity {spec['severity']!r}; "
                    f"must be one of {sorted(valid_severities)}. "
                    f"_apply_signal_level_and_count reads spec.get('severity', "
                    f"DEFAULT_SEVERITY), so an unknown value would be silently "
                    f"filtered out at every --signal-level."
                )
        for target, cascade in scenario.cascade_specs:
            if target not in known_components:
                raise ValueError(
                    f"SCENARIOS[{slug!r}].cascade_specs targets unknown "
                    f"component {target!r}"
                )
            if "severity" in cascade and cascade["severity"] not in valid_severities:
                raise ValueError(
                    f"SCENARIOS[{slug!r}].cascade_specs entry targeting "
                    f"{target!r} has severity {cascade['severity']!r}; "
                    f"must be one of {sorted(valid_severities)}. "
                    f"_apply_signal_level_and_count reads spec.get('severity', "
                    f"DEFAULT_SEVERITY), so an unknown value would be silently "
                    f"filtered out at every --signal-level."
                )


_validate_scenarios_registry()


def _validate_derivations_registry() -> None:
    """Import-time invariants for ``DERIVATIONS``.

    Catches drift between the derivation registry and ``COMPONENTS``: a
    misnamed component or column would silently no-op (the dict lookup
    misses) or silently mis-target (the name lookup in the derivation
    misses), and the test-side ``DERIVED_METRICS`` exemption would skip a
    column that no longer exists. Failing fast at import time forces
    these to stay in lockstep.
    """
    known_components = set(COMPONENTS.keys())
    for component, (_, metrics) in DERIVATIONS.items():
        if component not in known_components:
            raise ValueError(
                f"DERIVATIONS references unknown component {component!r}; "
                f"expected one of {sorted(known_components)}"
            )
        known_metrics = {spec.name for spec in COMPONENTS[component]}
        unknown_metrics = sorted(set(metrics) - known_metrics)
        if unknown_metrics:
            raise ValueError(
                f"DERIVATIONS[{component!r}] declares derived metrics "
                f"{unknown_metrics} that are not in COMPONENTS[{component!r}]; "
                f"register the MetricSpec first or correct the name."
            )


_validate_derivations_registry()


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


def _resolve_scenarios(args) -> set[str]:
    """Return the effective set of scenario slugs for this run.

    Resolution order: allowlist (``args.scenarios``) → exclusion
    (``args.exclude_scenarios``) → severity filter (``args.signal_level``) →
    duration filter (``args.duration_days``) → component filter
    (``args.components``).

    Scenarios dropped by the severity or duration filter emit a stderr
    ``WARNING: scenario <slug> requires …`` so a misconfigured run is
    diagnosable without re-running with -v. Scenarios with no
    ``components_touched`` intersection with ``args.components`` are dropped
    silently — every spec they would have produced is already filtered out
    downstream, and the warning would be noise for users who restricted
    components on purpose.

    parse_args has already validated that every slug in ``args.scenarios``
    and ``args.exclude_scenarios`` exists in ``SCENARIOS``, so this function
    never raises for unknown-slug.

    Iterates the candidate slugs in sorted order so the stderr ``WARNING``
    lines emitted on severity/duration drops appear in a deterministic
    order across runs — set iteration order would otherwise vary and make
    diagnostics harder to diff.
    """
    allowed_severities = SIGNAL_LEVELS[args.signal_level]
    resolved = set(args.scenarios) - set(args.exclude_scenarios)

    survivors: set[str] = set()
    for slug in sorted(resolved):
        scenario = SCENARIOS[slug]
        if scenario.severity not in allowed_severities:
            print(
                f"WARNING: scenario {slug} requires --signal-level "
                f"{scenario.severity} (current: {args.signal_level}); skipped.",
                file=sys.stderr,
            )
            continue
        if scenario.days_required > args.duration_days:
            print(
                f"WARNING: scenario {slug} requires --duration-days "
                f">= {scenario.days_required} (current: {args.duration_days}); "
                "skipped.",
                file=sys.stderr,
            )
            continue
        touched = set(scenario.components_touched)
        if not (touched & args.components):
            # Silent drop — no scenario output is possible under the
            # active --components allowlist anyway.
            continue
        survivors.add(slug)
    return survivors


def _apply_scenarios(component_anomalies: dict, cascade_registry: dict,
                     active_scenarios: set[str]) -> None:
    """Append the active scenarios' primaries and cascades onto the runtime
    registries.

    Walks ``SCENARIOS`` in declaration order. Byte-for-byte output is
    preserved when each ``(row_idx, metric)`` is unique, because
    ``generate_component`` applies Python's stable ``sorted()`` with key
    ``(row_idx, metric)`` to its expanded overrides — under that condition
    the global RNG draw sequence is anchored to ``(time_offset, metric_name)``
    and the spec list order here does not matter. When two specs collide on
    the same ``(row_idx, metric)`` (e.g. a cascade landing inside a shaped
    primary span, or coarse ``--interval-seconds`` rounding two offsets to
    the same row), the stable sort preserves declaration order and the last
    writer wins for that cell — so the per-scenario spec list order is part
    of the contract for collisions.
    """
    for slug, scenario in SCENARIOS.items():
        if slug not in active_scenarios:
            continue
        for component, spec in scenario.primary_specs:
            component_anomalies.setdefault(component, []).append(spec)
        for target, cascade in scenario.cascade_specs:
            cascade_registry.setdefault(target, []).append(cascade)


# ------------------------------------------------------------------
# CLI + entry point
# ------------------------------------------------------------------
def _env_bool(name: str, default: bool = False) -> bool:
    """Parse a boolean env var with three-valued contract:

    - missing, empty, or whitespace-only → returns ``default``
    - truthy (``1``/``true``/``yes``/``on``, case-insensitive) → returns ``True``
    - any other non-empty value (``0``/``false``/``no``/``off``/garbage) → returns ``False``

    The empty/missing path honors ``default`` so ``MEZMO_FOO=""`` with
    ``default=True`` does not silently flip to ``False``; explicit falsy
    values always win over ``default`` so opt-out env vars behave as
    expected without surprise."""
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="Generate synthetic IoT metric logs with anomalies.",
    )
    p.add_argument("--duration-days", type=int, default=DEFAULT_DURATION_DAYS,
                   help=f"Number of days of metrics to generate (default: {DEFAULT_DURATION_DAYS}). "
                        "Each scenario's ``days_required`` is the minimum value at which "
                        "any of its specs become in range; the full multi-day catalog "
                        "manifests at 7+.")
    p.add_argument("--seed", type=int, default=DEFAULT_SEED,
                   help=f"RNG seed for deterministic output (default: {DEFAULT_SEED}).")
    p.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR,
                   help=f"Directory to write CSV files into (default: {DEFAULT_OUTPUT_DIR}).")
    p.add_argument("--drop-rate", type=float, default=DEFAULT_DROP_RATE,
                   help=f"Per-row probability of dropping the row entirely from the per-component CSV "
                        f"(no row is emitted for that timestamp). Simulated packet loss "
                        f"(default: {DEFAULT_DROP_RATE}).")
    p.add_argument("--interval-seconds", type=float, default=DEFAULT_INTERVAL_SECONDS,
                   help=f"Seconds between consecutive emitted rows "
                        f"(default: {DEFAULT_INTERVAL_SECONDS}). Controls sampling "
                        f"density; timeline coverage stays --duration-days * 86400 "
                        f"seconds. Row count per component is floor(total_seconds / interval). "
                        f"Must be >= 0.001 (millisecond precision floor). "
                        f"Values >= 1.0 emit second-precision timestamps "
                        f"(YYYY-MM-DD HH:MM:SS); values < 1.0 emit millisecond-precision "
                        f"timestamps (YYYY-MM-DD HH:MM:SS.SSS) to keep adjacent rows unique.")
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
        "--scenarios",
        type=str,
        default="all",
        help="Comma-separated list of named scenario slugs to include. Use "
             "'all' (default) to include every scenario in the registry. "
             "Case-insensitive. Known slugs: "
             f"{', '.join(sorted(SCENARIOS.keys()))}.",
    )
    p.add_argument(
        "--exclude-scenarios",
        type=str,
        default="",
        help="Comma-separated list of named scenario slugs to exclude from "
             "the resolved set (applied after --scenarios). Case-insensitive. "
             "Defaults to empty (no exclusion).",
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
    gauge_toggle = p.add_mutually_exclusive_group()
    gauge_toggle.add_argument(
        "--otel-emit-gauges",
        dest="otel_emit_gauges",
        action="store_true",
        help="Emit a second OTLP stream of per-row metric values as Gauge data points "
             "to --otel-metrics-endpoint, in addition to the existing anomaly-counter "
             "stream. Off by default. Requires --otel-enabled, --otel-metrics-endpoint, "
             "and 'metrics' in --emit-selection. "
             "Env override: MEZMO_OTEL_EMIT_GAUGES (truthy = 1/true/yes/on).",
    )
    gauge_toggle.add_argument(
        "--otel-no-emit-gauges",
        dest="otel_emit_gauges",
        action="store_false",
        help="Explicitly disable the gauge stream (the default). Overrides "
             "--otel-emit-gauges and the MEZMO_OTEL_EMIT_GAUGES env var.",
    )
    p.set_defaults(otel_emit_gauges=_env_bool("MEZMO_OTEL_EMIT_GAUGES", False))
    p.add_argument(
        "--otel-gauge-batch-seconds",
        type=int,
        default=60,
        help="Number of consecutive timestamp ticks (in seconds of timeline coverage, "
             "not wall-clock) coalesced into one OTLP request when --otel-emit-gauges "
             "is on. Default: 60. Larger batches mean fewer HTTP requests but bigger "
             "bodies; tune for your OTLP collector body limit.",
    )
    p.add_argument(
        "--otel-gauge-metric-prefix",
        type=str,
        default="",
        help="Optional namespace prefix prepended to the OTLP metric name for each "
             "gauge data point (e.g. 'amc.' produces 'amc.cpu_util_pct'). Default: "
             "empty (use the raw MetricSpec.name).",
    )
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
             "Without --otel-emit-gauges this endpoint receives only the "
             "anomaly-counter stream (one Sum data point per anomaly event); "
             "with --otel-emit-gauges it additionally receives a Gauge stream of "
             "per-row metric values. "
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
        help="Optional cap on streamed HTTP attempt count (default: all). For the "
             "anomaly-counter stream this caps the number of anomaly events sent. "
             "For the gauge stream (``--otel-emit-gauges``) it caps the number of "
             "OTLP request *attempts* (not data points and not successes) — a broken "
             "endpoint that 500s every request still trips the cap at N. Both streams "
             "honor the same flag independently in one run.",
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
        default=os.environ.get("MEZMO_OTEL_STREAM_PROTOCOL", "protobuf"),
        help="OTLP payload mode for stream endpoint: json or protobuf (default: protobuf). "
             "Env override: MEZMO_OTEL_STREAM_PROTOCOL.",
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
    # NaN and infinity slip past plain <= 0 / < 0.001 comparisons:
    # NaN compares false to everything, and inf is greater than every finite
    # bound. NaN later crashes when row counts are cast to int; inf silently
    # generates zero rows. Reject both up-front.
    if not math.isfinite(args.interval_seconds):
        p.error("--interval-seconds must be a finite number")
    if args.interval_seconds <= 0:
        p.error("--interval-seconds must be > 0")
    # Sub-second intervals emit millisecond-precision timestamps. Anything
    # finer than 1ms would collide on the rendered string and silently drop
    # rows in the combine step (the original VER-111 failure mode).
    if args.interval_seconds < 0.001:
        p.error("--interval-seconds must be >= 0.001 (ms-precision floor)")
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
    if args.otel_emit_gauges:
        if not args.otel_enabled:
            p.error("--otel-emit-gauges requires --otel-enabled")
        if not args.otel_metrics_endpoint:
            p.error("--otel-emit-gauges requires --otel-metrics-endpoint to be set "
                    "(via flag or MEZMO_OTEL_METRICS_ENDPOINT)")
        if "metrics" not in selected:
            p.error("--otel-emit-gauges requires --emit-selection to include 'metrics'")
        # The gauge streamer feeds per-component CSVs into ``heapq.merge``,
        # which requires each input iterator to be sorted by the timestamp
        # key. ``--inject-dst-artifact-day`` deliberately duplicates the
        # 02:00–02:59 wall-clock hour inside each CSV (see
        # ``_splice_dst_artifact``), producing non-monotonic timestamps that
        # silently break batching and OTLP payloads. Reject the combination
        # at parse time — real OTLP consumers wouldn't tolerate the artifact
        # either, so there's no realistic user for it.
        if args.inject_dst_artifact_day > 0:
            p.error("--otel-emit-gauges is incompatible with --inject-dst-artifact-day "
                    "(the DST artifact produces non-monotonic CSV timestamps that break "
                    "the gauge streamer's heapq.merge); pass --inject-dst-artifact-day 0 "
                    "or drop --otel-emit-gauges")
    if args.otel_gauge_batch_seconds <= 0:
        p.error("--otel-gauge-batch-seconds must be > 0")
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

    raw_scenarios = [item.strip().lower() for item in args.scenarios.split(",") if item.strip()]
    if not raw_scenarios:
        p.error("--scenarios must contain at least one scenario slug (or 'all')")
    if "all" in raw_scenarios and len(set(raw_scenarios)) > 1:
        # 'all' is a sentinel meaning "every scenario in the registry"; mixing
        # it with explicit slugs is ambiguous (does the user want only those
        # slugs, or every scenario plus those slugs?). Reject so the intent
        # has to be made explicit.
        p.error("--scenarios: 'all' is mutually exclusive with explicit slugs; "
                "pass either 'all' or a comma-separated list of slugs, not both")
    invalid_scenarios = sorted(set(raw_scenarios) - set(SCENARIOS.keys()) - {"all"})
    if invalid_scenarios:
        p.error("--scenarios contains invalid value(s): "
                f"{', '.join(invalid_scenarios)}. "
                f"Allowed: {', '.join(sorted(SCENARIOS.keys()))} or 'all'")
    if "all" in raw_scenarios:
        selected_scenarios = set(SCENARIOS.keys())
    else:
        selected_scenarios = set(raw_scenarios)
    args.scenarios = selected_scenarios

    raw_exclude = [item.strip().lower() for item in args.exclude_scenarios.split(",") if item.strip()]
    excluded_scenarios = set(raw_exclude)
    invalid_excluded = sorted(excluded_scenarios - set(SCENARIOS.keys()))
    if invalid_excluded:
        p.error("--exclude-scenarios contains invalid value(s): "
                f"{', '.join(invalid_excluded)}. "
                f"Allowed: {', '.join(sorted(SCENARIOS.keys()))}")
    args.exclude_scenarios = excluded_scenarios

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


def combine_logs(input_dir, components=None):
    """Write the unified combined CSV from per-component CSVs in ``input_dir``.

    When ``components`` is ``None``, the combine step autodiscovers every
    ``*.csv`` in ``input_dir`` (excluding the anomalies manifest and prior
    combine outputs). When ``components`` is provided, it is used verbatim —
    the caller controls the order and is responsible for restricting to the
    user-selected allowlist. Any named component whose ``{name}.csv`` is
    missing from ``input_dir`` raises ``SystemExit``.
    """
    input_dir = Path(input_dir)
    if components is None:
        components = discover_components(input_dir)
        if not components:
            raise SystemExit(f"No component CSVs found in {input_dir}/")
    else:
        components = list(components)
        missing = [f"{name}.csv" for name in components
                   if not (input_dir / f"{name}.csv").exists()]
        if missing:
            raise SystemExit(
                f"missing component CSVs in {input_dir}: "
                f"{', '.join(missing)}"
            )
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


def _parse_csv_timestamp(timestamp: str) -> datetime.datetime:
    """Parse a ``YYYY-MM-DD HH:MM:SS[.SSS]`` CSV timestamp into a naive datetime.

    The integer-second and millisecond-precision forms emitted by
    ``_build_timestamp_arrays`` are both accepted. Centralizing the format
    dispatch here keeps every consumer (OTLP payload conversion, OTEL stream
    pacing, future readers) in lockstep on the supported formats.
    """
    fmt = "%Y-%m-%d %H:%M:%S.%f" if "." in timestamp else "%Y-%m-%d %H:%M:%S"
    return datetime.datetime.strptime(timestamp, fmt)


_UNIX_EPOCH_UTC = datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc)


def _to_unix_nanos(timestamp: str) -> int:
    """Convert ``YYYY-MM-DD HH:MM:SS[.SSS]`` timestamp strings to unix-nanoseconds.

    Uses integer arithmetic on ``timedelta`` fields rather than
    ``datetime.timestamp() * 1e9`` so millisecond-precision inputs do not
    accrue floating-point rounding error on the way to a nanosecond integer.
    """
    dt = _parse_csv_timestamp(timestamp).replace(tzinfo=datetime.timezone.utc)
    delta = dt - _UNIX_EPOCH_UTC
    return (delta.days * 86_400 + delta.seconds) * 1_000_000_000 + delta.microseconds * 1_000


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


def _build_otlp_gauge_payload(batch: list[dict], *, metric_prefix: str = "") -> dict:
    """Build one OTLP/HTTP JSON ``resourceMetrics`` payload for a batch of per-row gauge values.

    Each ``batch`` entry is ``{"timestamp": str, "component": str, "metric": str, "value": float}``.
    Entries are grouped first by ``component`` (one ``resourceMetrics`` entry per
    component) and then by ``metric`` (one ``metrics[]`` entry per metric within
    the component's scope), with one Gauge data point per row.
    """
    grouped: dict[str, dict[str, list[dict]]] = {}
    for entry in batch:
        comp = entry["component"]
        metric = entry["metric"]
        grouped.setdefault(comp, {}).setdefault(metric, []).append(entry)

    resource_metrics = []
    for component, metrics_map in grouped.items():
        metrics_list = []
        for metric_name, entries in metrics_map.items():
            data_points = []
            for entry in entries:
                ts_nano = _to_unix_nanos(entry["timestamp"])
                data_points.append({
                    "timeUnixNano": str(ts_nano),
                    "asDouble": float(entry["value"]),
                    "attributes": [
                        {"key": "metric.name", "value": {"stringValue": metric_name}},
                        {"key": "component", "value": {"stringValue": component}},
                        {"key": "signal.type", "value": {"stringValue": "metric_value"}},
                    ],
                })
            metrics_list.append({
                "name": f"{metric_prefix}{metric_name}",
                "gauge": {"dataPoints": data_points},
            })
        resource_metrics.append({
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
                "metrics": metrics_list,
            }],
        })
    return {"resourceMetrics": resource_metrics}


def _build_otlp_gauge_protobuf(batch: list[dict], *, metric_prefix: str = "") -> bytes:
    """Build one OTLP protobuf ExportMetricsServiceRequest carrying gauge data points.

    Same grouping as ``_build_otlp_gauge_payload``: one ``resource_metrics`` per
    component, one ``metrics`` entry per (component, metric) pair, with one Gauge
    data point per batch row for that metric.
    """
    try:
        from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import ExportMetricsServiceRequest
        from opentelemetry.proto.common.v1.common_pb2 import AnyValue, KeyValue
    except ImportError as exc:
        raise SystemExit(
            "OTLP protobuf mode requires opentelemetry-proto + protobuf. "
            "Install with: pip install opentelemetry-proto protobuf"
        ) from exc

    grouped: dict[str, dict[str, list[dict]]] = {}
    for entry in batch:
        comp = entry["component"]
        metric = entry["metric"]
        grouped.setdefault(comp, {}).setdefault(metric, []).append(entry)

    req = ExportMetricsServiceRequest()
    for component, metrics_map in grouped.items():
        rmetric = req.resource_metrics.add()
        rmetric.resource.attributes.extend([
            KeyValue(key="service.name", value=AnyValue(string_value=component)),
            KeyValue(key="service.namespace", value=AnyValue(string_value="anomaly-metric-creator")),
        ])
        smetric = rmetric.scope_metrics.add()
        smetric.scope.name = "anomaly-metric-creator"
        smetric.scope.version = "1.0.0"
        for metric_name, entries in metrics_map.items():
            m = smetric.metrics.add()
            m.name = f"{metric_prefix}{metric_name}"
            for entry in entries:
                ts_nano = _to_unix_nanos(entry["timestamp"])
                dp = m.gauge.data_points.add()
                dp.time_unix_nano = ts_nano
                dp.as_double = float(entry["value"])
                dp.attributes.extend([
                    KeyValue(key="metric.name", value=AnyValue(string_value=metric_name)),
                    KeyValue(key="component", value=AnyValue(string_value=component)),
                    KeyValue(key="signal.type", value=AnyValue(string_value="metric_value")),
                ])
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
            cur_dt = _parse_csv_timestamp(row["timestamp"])
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


def _iter_component_rows(component: str, csv_path: Path):
    """Yield ``(timestamp_str, component, [(metric_name, value)...])`` for each
    data row in ``csv_path``.

    ``generate_component`` omits dropped rows from the CSV entirely (via the
    ``keep_mask``) rather than writing them as blank lines, so under normal
    operation the file has only the header plus one row per surviving
    timestamp — dropped timestamps are naturally absent from the gauge
    stream. The streamer still defensively skips any zero-column row to
    tolerate hand-edited inputs.
    """
    with open(csv_path, "r", encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
        metric_cols = header[1:]
        for row in reader:
            if not row:
                continue
            ts = row[0]
            values = []
            for name, raw in zip(metric_cols, row[1:]):
                if raw == "":
                    continue
                try:
                    values.append((name, float(raw)))
                except ValueError:
                    continue
            yield ts, component, values


def stream_otel_gauges(
    component_csv_paths: dict[str, Path],
    *,
    endpoint: str,
    batch_seconds: int,
    metric_prefix: str,
    speedup: float,
    timeout_seconds: float,
    max_events: int | None,
    max_retries: int,
    auth_headers: dict[str, str] | None,
    protocol: str,
    activity_log_path: Path | None,
    verbose: bool,
) -> int:
    """Stream per-row metric values from per-component CSVs to an OTLP/HTTP
    metrics endpoint as Gauge data points.

    Walks all component CSVs in a unified chronological timeline via
    ``heapq.merge`` keyed on the parsed timestamp, accumulating rows into
    batches that cover ``batch_seconds`` seconds of timeline coverage. Each
    flush is one OTLP request grouped by component (resource) and metric
    (scopeMetrics.metrics). Dropped CSV rows are naturally absent from the
    gauge stream because ``generate_component`` omits them from each per-
    component CSV entirely (see ``keep_mask``), so the streamer only ever
    sees surviving timestamps.

    ``max_events`` caps the total number of OTLP requests sent (not data
    points), mirroring ``--otel-stream-max-events`` semantics for the
    counter stream.
    """
    if not component_csv_paths:
        return 0

    log_file = None
    if activity_log_path is not None:
        activity_log_path.parent.mkdir(parents=True, exist_ok=True)
        # Append so a prior stream_otel_signals run's records are preserved.
        log_file = open(activity_log_path, "a", encoding="utf-8")

    _write_activity(
        log_file,
        "START",
        signal="metrics_gauge",
        components=",".join(sorted(component_csv_paths.keys())),
        batch_seconds=batch_seconds,
        protocol=protocol,
        speedup=speedup,
    )

    def _keyed_iter(component: str, csv_path: Path):
        for ts, comp, values in _iter_component_rows(component, csv_path):
            yield (_parse_csv_timestamp(ts), ts, comp, values)

    iters = [_keyed_iter(c, p) for c, p in component_csv_paths.items() if p.exists()]

    batch: list[dict] = []
    batch_start_dt: datetime.datetime | None = None
    requests_sent = 0
    requests_attempted = 0
    data_points_sent = 0
    # Pacing key is the previous batch's *start* time so the wall-clock gap
    # between flushes matches the timeline gap between two batch anchors —
    # which is ``batch_seconds`` in steady state. Using the previous batch's
    # *end* time would collapse the gap to roughly ``interval_seconds`` (the
    # spacing between two adjacent CSV rows), producing a 60× pacing error
    # at the default 60s batch.
    prev_batch_start_dt: datetime.datetime | None = None
    aborted = False

    def _flush() -> bool:
        nonlocal batch, batch_start_dt, requests_sent, requests_attempted
        nonlocal data_points_sent, prev_batch_start_dt
        if not batch:
            return True
        # ``max_events`` mirrors the counter stream's semantics: it caps
        # *attempts*, not successes. The counter stream pre-truncates its
        # event list at ``stream_otel_signals`` entry, so the same flag
        # already means "at most N HTTP attempts" there. If we gated on
        # ``requests_sent`` instead, a broken endpoint would let the gauge
        # stream attempt unbounded flushes since none ever succeeds.
        if max_events is not None and requests_attempted >= max_events:
            return False

        if prev_batch_start_dt is not None and batch_start_dt is not None:
            wait_seconds = max(0.0, (batch_start_dt - prev_batch_start_dt).total_seconds() / speedup)
            if wait_seconds > 0:
                time.sleep(wait_seconds)
        requests_attempted += 1

        if protocol == "protobuf":
            body = _build_otlp_gauge_protobuf(batch, metric_prefix=metric_prefix)
            content_type = "application/x-protobuf"
        else:
            body = json.dumps(
                _build_otlp_gauge_payload(batch, metric_prefix=metric_prefix)
            ).encode("utf-8")
            content_type = "application/json"

        headers = {"Content-Type": content_type}
        if auth_headers:
            headers.update(auth_headers)

        req = urllib.request.Request(endpoint, data=body, method="POST", headers=headers)
        batch_start_ts = batch[0]["timestamp"]
        batch_end_ts = batch[-1]["timestamp"]
        data_points = len(batch)
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
                signal="metrics_gauge",
                endpoint=endpoint,
                batch_start_ts=batch_start_ts,
                batch_end_ts=batch_end_ts,
                data_points=data_points,
                attempt=f"{attempts + 1}/{max_retries + 1}",
                **verbose_send_fields,
            )
            try:
                with urllib.request.urlopen(req, timeout=timeout_seconds) as response:
                    response_status = response.status
                    if response.status >= 400:
                        raise urllib.error.HTTPError(
                            endpoint, response.status, response.reason,
                            response.headers, None,
                        )
                ok_fields: dict = {}
                if verbose:
                    ok_fields["status"] = response_status
                _write_activity(
                    log_file,
                    "OK",
                    signal="metrics_gauge",
                    batch_start_ts=batch_start_ts,
                    batch_end_ts=batch_end_ts,
                    data_points=data_points,
                    **ok_fields,
                )
                requests_sent += 1
                data_points_sent += data_points
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
                        f"WARNING: OTEL metrics_gauge stream failed for batch "
                        f"{batch_start_ts}..{batch_end_ts}: {exc}",
                        file=sys.stderr,
                    )
                    _write_activity(
                        log_file,
                        "FAIL",
                        signal="metrics_gauge",
                        batch_start_ts=batch_start_ts,
                        batch_end_ts=batch_end_ts,
                        data_points=data_points,
                        error=repr(str(exc)),
                        **err_fields,
                    )
                    break
                backoff = min(2 ** (attempts - 1), 8)
                print(
                    f"WARNING: OTEL metrics_gauge stream retry {attempts}/{max_retries} "
                    f"for batch {batch_start_ts}..{batch_end_ts}: {exc}",
                    file=sys.stderr,
                )
                _write_activity(
                    log_file,
                    "RETRY",
                    signal="metrics_gauge",
                    batch_start_ts=batch_start_ts,
                    batch_end_ts=batch_end_ts,
                    data_points=data_points,
                    attempt=f"{attempts}/{max_retries}",
                    error=repr(str(exc)),
                    **err_fields,
                )
                time.sleep(backoff)

        prev_batch_start_dt = batch_start_dt
        batch = []
        batch_start_dt = None
        if max_events is not None and requests_attempted >= max_events:
            return False
        return True

    try:
        for dt, ts, comp, values in heapq.merge(*iters, key=lambda item: item[0]):
            if not values:
                continue
            if batch_start_dt is None:
                batch_start_dt = dt
            # Flush when the new row would push the batch beyond batch_seconds
            # of timeline coverage. Use closed-open semantics: a batch_seconds=60
            # batch starting at t=0 covers rows with dt in [0, 60).
            if (dt - batch_start_dt).total_seconds() >= batch_seconds:
                if not _flush():
                    aborted = True
                    break
                batch_start_dt = dt
            for metric_name, value in values:
                batch.append({
                    "timestamp": ts,
                    "component": comp,
                    "metric": metric_name,
                    "value": value,
                })
        if not aborted:
            _flush()
    finally:
        _write_activity(
            log_file,
            "END",
            signal="metrics_gauge",
            requests_sent=requests_sent,
            data_points_sent=data_points_sent,
        )
        if log_file is not None:
            log_file.close()
    return requests_sent


def main(argv=None):
    args = parse_args(argv)

    # Restrict the combine step to user-selected components when --components
    # narrows the selection; pass None for the default ("all") so autodiscovery
    # still picks up any extra CSVs in --output-dir. List order follows the
    # COMPONENTS declaration so unified CSV columns stay deterministic.
    if args.components == set(COMPONENTS.keys()):
        combine_components = None
    else:
        combine_components = [name for name in COMPONENTS if name in args.components]

    if args.combine_only:
        if not args.output_dir.is_dir():
            raise SystemExit(f"--combine-only requires an existing --output-dir; "
                             f"{args.output_dir} does not exist")
        combine_logs(args.output_dir, components=combine_components)
        return

    total_seconds = SECONDS_PER_DAY * args.duration_days
    args.output_dir.mkdir(exist_ok=True, parents=True)
    np.random.seed(args.seed)

    # Reset module-level registries so repeated calls (e.g., from tests) don't accumulate.
    anomalies.clear()
    cascading_anomalies.clear()

    # Build component_anomalies and cascading_anomalies entirely from the
    # SCENARIOS registry. _resolve_scenarios() applies the --scenarios /
    # --exclude-scenarios / --signal-level / --duration-days / --components
    # gates; _apply_scenarios() walks the resolved set in declaration order
    # and tail-appends each scenario's primaries and cascades.
    component_anomalies = {name: [] for name in COMPONENTS}
    active_scenarios = _resolve_scenarios(args)
    _apply_scenarios(component_anomalies, cascading_anomalies, active_scenarios)

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

    gauge_requests_sent = 0
    if otel_active and args.otel_emit_gauges:
        # Gauge stream runs after the anomaly-counter stream and writes to the
        # same activity log file in append mode so both passes share one log.
        gauge_auth = auth_headers.get("metrics") if otel_active else None
        component_csv_paths = {
            c: args.output_dir / f"{c}.csv" for c in sorted(args.components)
        }
        gauge_requests_sent = stream_otel_gauges(
            component_csv_paths,
            endpoint=args.otel_metrics_endpoint,
            batch_seconds=args.otel_gauge_batch_seconds,
            metric_prefix=args.otel_gauge_metric_prefix,
            speedup=args.otel_stream_speedup,
            timeout_seconds=args.otel_stream_timeout_seconds,
            max_events=args.otel_stream_max_events,
            max_retries=3,
            auth_headers=gauge_auth,
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
        if args.otel_emit_gauges:
            print(f"   OTEL gauge requests streamed: {gauge_requests_sent} to "
                  f"metrics -> {args.otel_metrics_endpoint}")
    elif any(endpoints.values()):
        print("   OTEL streaming disabled (pass --otel-enabled to send to configured endpoints)")

    if args.combine:
        combine_logs(args.output_dir, components=combine_components)


if __name__ == "__main__":
    main()
