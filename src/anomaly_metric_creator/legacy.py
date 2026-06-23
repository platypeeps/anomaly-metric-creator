#!/usr/bin/env python3
"""
Generate IoT-style metric logs for a SaaS stack with built-in anomalies.

Defaults to 50,000 rows at 1-minute resolution, matching the reference
observability telemetry CSV shape. Use ``--duration-days N`` to span more days;
multi-day scenarios activate based on their own ``days_required`` (see the
README scenario catalog for current values). ``--duration-days 7`` currently
unlocks the original week-long catalog; the default 50,000-row window also
captures the longer GPU inference serving pattern. Anomaly specs whose
``time_offset`` falls outside the configured window are skipped with a warning
on stderr.
"""

import argparse
import base64
import contextlib
import csv
import datetime
import hashlib
import heapq
import json
import inspect
import math
import os
import shlex
import sys
import time
import urllib.error
import urllib.request
import dataclasses
import functools
from dataclasses import dataclass, field
from hashlib import sha1
from pathlib import Path
from typing import Callable

try:
    import numpy as np
except ModuleNotFoundError as exc:
    if exc.name not in {None, "numpy"}:
        raise
    print(
        "Missing required dependency: numpy\n"
        "Install this project into the Python you are using, for example:\n"
        "  python3 -m pip install -e .\n"
        "or create the documented dev environment:\n"
        "  python3 -m venv .venv\n"
        "  .venv/bin/pip install -e '.[dev]'\n",
        file=sys.stderr,
    )
    raise SystemExit(1) from None

# ------------------------------------------------------------------
# Configuration
# ------------------------------------------------------------------
START = datetime.datetime(2026, 3, 10, 0, 0, 0)
SECONDS_PER_DAY = 86_400
DEFAULT_ROW_COUNT = 50_000
DEFAULT_SEED = 42
DEFAULT_OUTPUT_DIR = Path("iot_logs")
DEFAULT_DROP_RATE = 0.0
DEFAULT_INTERVAL_SECONDS = 60.0
DEFAULT_DURATION_DAYS = (
    DEFAULT_ROW_COUNT * DEFAULT_INTERVAL_SECONDS / SECONDS_PER_DAY
)
DEFAULT_OTEL_STREAM_AUTH_SCHEME = "Bearer"
DEFAULT_SIGNAL_LEVEL = "medium"

# Preflight ceiling on total emitted cells per run, where one "cell" is one
# metric value at one timestamp summed across selected components. Trips on
# the metric × row × component product to catch the common foot-gun of
# combining a very small --interval-seconds with the default --duration-days
# and component allowlist, which silently blows up to billions of cells (and
# tens of GB of CSV) before the user notices. Override with
# --allow-huge-output when the size is intentional. 200M cells corresponds
# to roughly 5-15 GB of output and runs in tens of seconds; well above any
# default workload but well under "I rebooted my laptop by accident".
PREFLIGHT_CELL_CAP = 200_000_000

# Inclusion hierarchy for --signal-level: each level keeps its own severity tier
# plus everything weaker. A spec with no explicit ``severity`` defaults to
# ``medium`` so today's catalog continues to fire under the default level.
SIGNAL_LEVELS: dict[str, set[str]] = {
    "low": {"low"},
    "medium": {"low", "medium"},
    "high": {"low", "medium", "high"},
}
DEFAULT_SEVERITY = "medium"

# Anomaly shape vocabulary recognised by ``_resolve_anomaly_value``. Specs that
# declare an unknown ``shape`` are rejected at import time by
# ``_validate_scenario_spec``.
_VALID_ANOMALY_SHAPES = frozenset({
    "step", "sustained", "ramp_linear", "ramp_exp", "sawtooth", "sine",
})

# Stable named sub-seed for the --anomaly-count sampling RNG. Derived from
# sha256(b"anomaly_count_cap") and fixed at import time so the cap RNG stream
# is decoupled from any other np.random use that shares the same seed.
_ANOMALY_COUNT_CAP_SALT = int.from_bytes(
    hashlib.sha256(b"anomaly_count_cap").digest()[:4], "big"
)

# ------------------------------------------------------------------
# Per-run state container
# ------------------------------------------------------------------
@dataclass
class RunContext:
    """Per-run mutable state.

    Fields:
    - ``rng``: ``np.random.RandomState`` instance seeded from ``--seed``.
      Authoritative RNG for the run; threaded explicitly through
      ``generate_component()``, ``_natural_column()``, and the anomaly
      override path.
    - ``anomalies``: list accumulator for manifest rows. Each call to
      ``generate_component()`` appends one entry per anomaly span that
      survives drop-mask filtering.
    - ``cascading_anomalies``: dict keyed by target component name, value
      is the list of cascade spec dicts that fire on that component.
      Populated by ``_apply_scenarios()`` and consumed by
      ``generate_component()`` when it merges primary + cascade overrides.
    - ``instances``: optional per-run override of the module-level
      ``INSTANCES`` registry, keyed by component name. ``main()``
      populates it from ``INSTANCES`` by default (preserving today's
      single-anonymous-instance contract) and Phase 2+ CLI flags will
      replace the per-component list when the user asks for fan-out.
    """
    rng: "np.random.RandomState"
    anomalies: list = field(default_factory=list)
    cascading_anomalies: dict = field(default_factory=dict)
    instances: dict = field(default_factory=dict)

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
# Vocabulary for ``MetricSpec.semantic_type``. Drives both the ``schema.json``
# emitter and the ``validate`` subcommand's checks (e.g. ``counter`` / ``rate``
# columns must be non-negative). Values map onto the OTLP semantic instrument
# kinds the generator uses elsewhere (``stream_otel_signals`` Sum data points
# for counters, ``stream_otel_gauges`` Gauge data points for gauges).
_VALID_SEMANTIC_TYPES = frozenset({"counter", "gauge", "ratio", "rate"})

# Vocabulary for ``MetricSpec.dtype``. ``int`` here means "values are
# expected to be whole numbers"; ``generate_component`` rounds
# int-typed columns via ``np.rint`` before derivations run (the
# default since the phase 6 flag day; the phase-9 flag day removed the
# last CLI opt-out, and programmatic callers can still opt out via
# ``apply_dtype_int_cast=False``), so the CSV
# cell is a whole-integer string. The validator surfaces any remaining
# fractional values as schema violations.
_VALID_DTYPES = frozenset({"float", "int"})


@dataclass(frozen=True)
class MetricSpec:
    """Config for one synthetic metric column.

    Natural value is ``(base + N(0, std)) * multiplier(ts, sec) + additive(ts, sec)``,
    optionally clipped at ``clip_min``. ``std=0`` skips the RNG draw entirely so
    deterministic series do not perturb the shared numpy random stream.

    Schema fields (``unit``, ``semantic_type``, ``min_value``, ``max_value``,
    ``dtype``, ``derivation``) are declarative metadata only — they do not
    affect generation. They flow into ``schema.json`` and the
    ``validate`` subcommand's checks. Defaults preserve existing behavior for
    catalog entries that have not been backfilled yet (the generator still
    emits the same bytes whether or not these fields are populated).
    """
    name: str
    base: float
    std: float = 0.0
    multiplier: Callable[[datetime.datetime, int], float] | None = None
    additive: Callable[[datetime.datetime, int], float] | None = None
    clip_min: float | None = None
    # --- schema metadata ------------------------------------
    unit: str | None = None
    semantic_type: str | None = None
    min_value: float | None = None
    max_value: float | None = None
    dtype: str = "float"
    derivation: str | None = None


# ------------------------------------------------------------------
# Instance dimensions (Phase 1)
# ------------------------------------------------------------------
@dataclass(frozen=True)
class Instance:
    """One emitting instance of a component.

    Phase 1 introduces this dataclass as the foundational dimension model for
    multi-instance output. The CSV writer still emits one anonymous
    ``Instance()`` per component, so default byte output is unchanged; later
    phases plug ``--instances-per-component`` / ``--instance-config`` into the
    same shape and surface the dimensions as CSV columns, anomaly
    ``instance_filter`` selectors, OTEL resource attributes, and
    ``schema.json`` dimension declarations.

    All fields default to ``None`` so today's catalog can build the registry
    in lockstep with ``COMPONENTS`` without naming dimensions yet.
    """
    id: str | None = None
    host: str | None = None
    pod: str | None = None
    az: str | None = None
    region: str | None = None
    tenant: str | None = None


# Canonical column order for the multi-instance long-form dimension
# prefix. ``generate_component()``'s long-form branch writes these
# columns in this order; ``combine_logs`` matches the same shape when
# detecting a multi-instance CSV; ``_is_anonymous_instance_list`` keys
# its predicate off the same field list so all three views stay in
# lockstep with the ``Instance`` dataclass above. The Phase-5 long-form
# writers (``write_gauges_csv`` / ``combine_logs_unified``) read the same
# constant to detect dimensioned per-component CSVs by header inspection
# and to project dimension values into the long-form output rows.
_INSTANCE_DIMENSION_COLUMNS = ("id", "host", "pod", "az", "region", "tenant")


def _is_anonymous_instance_list(instances) -> bool:
    """True iff ``instances`` is the single-anonymous-Instance() default.

    Single source of truth for the "emit today's dimensionless format"
    branch in ``generate_component()`` and the DST-guard helper. Keying
    off ``_INSTANCE_DIMENSION_COLUMNS`` means adding or removing an
    ``Instance`` field touches one constant instead of two predicates.
    """
    if len(instances) != 1:
        return False
    only = instances[0]
    return all(getattr(only, field) is None for field in _INSTANCE_DIMENSION_COLUMNS)


# ------------------------------------------------------------------
# Topology graph dataclasses (phase 1 — structural-only).
# ------------------------------------------------------------------
# The ``TOPOLOGY`` constant below declares directed service-to-service edges
# alongside ``COMPONENTS``. The dataclasses landed first (phase 1)
# so the structural shape stays stable across the two-pass coupling
# generator (phase 2, phase 3) and the saturation
# feedback layer (phase 4, phase 5).
@dataclass(frozen=True)
class SaturationParams:
    """Sigmoid-style saturation parameters attached to a topology edge.

    Read by ``_apply_saturation`` as the parameters of a logistic
    response curve on the source's load metric: latency and error gains
    are added to the target's natural latency / error rate columns once
    load crosses ``midpoint`` at ``steepness``. Zero-gain (the default)
    means the edge declares the saturation point structurally but does
    not contribute to the target's metrics — handy for placeholder
    edges declared at phase 1 that have not been wired up to gains yet.
    """
    midpoint: float
    steepness: float
    latency_gain: float = 0.0
    error_gain: float = 0.0


@dataclass(frozen=True)
class Edge:
    """A directed edge in the service-call ``TOPOLOGY`` graph.

    ``weight`` is either a constant fan-out share (``float`` in ``[0, 1]``
    for routing fractions, or any non-negative scalar for amplification
    edges) or a callable ``(np.ndarray) -> np.ndarray`` that computes the
    per-row weight from a numpy column (e.g. cache-miss rate driving the
    cache→database fan-out). The import-time ``_validate_topology``
    validator enforces both branches: constant weights must be a finite
    non-negative ``int``/``float`` (``bool`` is rejected); callable
    weights must accept a numpy array and return a numpy array.

    ``signal`` is the per-edge derivation that feeds a callable ``weight``.
    It receives a ``dict[str, np.ndarray]`` of the upstream component's
    captured load columns (the canonical metric plus any supplementary
    metrics declared in ``_TOPOLOGY_LOAD_METRICS``) and returns either an
    ``np.ndarray`` of per-row signal values (passed verbatim into
    ``weight(signal)``) or ``None`` to skip the edge entirely (e.g. when
    ``--metrics-per-component`` has trimmed a required input column).
    Required iff ``weight`` is callable; must be ``None`` for constant
    ``weight``. The validator probes the callable with a tiny captured-
    column dict so a mis-shaped signal fails at import time.

    ``saturation`` is optional; when set, the phase-4 saturation feedback
    layer adds a sigmoid-shaped latency/error contribution to the target
    component once the source's load metric crosses the configured
    midpoint.

    ``correlation_threshold`` is the minimum Pearson correlation the phase-7 ``_validate_topology_coupling`` check requires between this
    edge's source canonical load metric and its target canonical load
    metric under realistic topology coupling. ``None`` (the default)
    means "use the registry-level default
    ``_TOPOLOGY_DEFAULT_CORRELATION_THRESHOLD``". The field is read by the
    validator only and does not affect generation. Callable-weight edges
    skip the check regardless (the correlation is dominated by the per-row
    weight signal rather than the upstream load), so the field is ignored
    for them.
    """
    target: str
    weight: float | Callable[[np.ndarray], np.ndarray] = 1.0
    saturation: SaturationParams | None = None
    signal: Callable[[dict[str, np.ndarray]], "np.ndarray | None"] | None = None
    correlation_threshold: float | None = None


# ------------------------------------------------------------------
# Scenario helper builders
# ------------------------------------------------------------------
def _const_generator(value: float):
    """Return a generator callable that emits ``value`` for every row."""
    return lambda _ts, _col, _value=value: _value


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _scenario_stress_fraction(t_within: float, duration_seconds: float,
                              *, phase: float = 0.0) -> float:
    """Smooth 0→1 span stress with small shared oscillation."""
    frac = _span_fraction(t_within, duration_seconds)
    wave = 0.035 * math.sin((2.0 * math.pi * frac * 3.0) + phase)
    ripple = 0.018 * math.sin((2.0 * math.pi * frac * 11.0) + phase * 0.5)
    return _clamp(frac + wave + ripple, 0.0, 1.0)


def _correlated_span_generator(
    start: float,
    end: float,
    duration_seconds: float,
    *,
    noise: float = 0.0,
    lo: float | None = None,
    hi: float | None = None,
    phase: float = 0.0,
    curve: float = 1.0,
):
    """Return a span generator driven by a shared gradual-stress profile."""
    def generator(ts, col, t_within, span_idx, rng):
        stress = _scenario_stress_fraction(
            t_within, duration_seconds, phase=phase
        )
        if curve != 1.0:
            stress = stress ** curve
        value = start + (end - start) * stress
        if noise:
            value += rng.normal(0.0, noise)
        if lo is not None or hi is not None:
            value = _clamp(
                float(value),
                float("-inf") if lo is None else lo,
                float("inf") if hi is None else hi,
            )
        return float(value)
    return generator


def _weighted_choice(rng: "np.random.RandomState", choices: tuple[float, ...],
                     weights: tuple[float, ...]) -> float:
    threshold = float(rng.random())
    cumulative = 0.0
    for choice, weight in zip(choices, weights):
        cumulative += weight
        if threshold <= cumulative:
            return choice
    return choices[-1]


def _ranked_choice_set(values: set[int], count: int,
                       score: "np.ndarray") -> set[int]:
    """Pick the highest-scoring unique minutes from ``values``."""
    if count <= 0:
        return set()
    if count > len(values):
        raise ValueError(
            f"Cannot sample {count} unique GPU inference minutes from "
            f"{len(values)} candidates"
        )
    ranked = sorted(values, key=lambda minute: (float(score[minute]), -minute),
                    reverse=True)
    return {int(v) for v in ranked[:count]}


def _gpu_inference_stress_score(
    rng: "np.random.RandomState",
    active_minutes: int,
    failure_minutes: frozenset[int],
) -> "np.ndarray":
    """Shared serving-stress score used to couple GPU inference metrics."""
    minutes = np.arange(active_minutes, dtype=np.float64)
    daily = 0.5 + 0.5 * np.sin((2 * np.pi * minutes / 1440.0) - 0.9)
    half_day = 0.5 + 0.5 * np.sin((2 * np.pi * minutes / 720.0) + 0.35)
    slow_noise = rng.normal(0.0, 1.0, active_minutes)
    kernel = np.ones(97, dtype=np.float64) / 97.0
    slow_noise = np.convolve(slow_noise, kernel, mode="same")
    incident_intensity = _gpu_inference_incident_intensity(active_minutes)
    raw = (
        0.85 * slow_noise
        + 0.34 * daily
        + 0.16 * half_day
        + 2.85 * incident_intensity
        + rng.normal(0.0, 0.18, active_minutes)
    )
    for minute in failure_minutes:
        raw[minute] += 2.10
        if minute > 0:
            raw[minute - 1] += 0.42
        if minute + 1 < active_minutes:
            raw[minute + 1] += 0.42

    order = np.argsort(raw, kind="mergesort")
    score = np.empty(active_minutes, dtype=np.float64)
    if active_minutes == 1:
        score[order] = 1.0
    else:
        score[order] = np.linspace(0.0, 1.0, active_minutes)
    return score


def _gpu_inference_incident_windows(
    active_minutes: int,
) -> tuple[tuple[int, int], ...]:
    """Deterministic degradation windows for the GPU serving incident field."""
    if active_minutes <= 0:
        return ()
    if active_minutes < 1_000:
        return ((0, active_minutes),)

    length = min(active_minutes, max(12 * 60, int(round(active_minutes * 0.0288))))
    start = min(int(round(active_minutes * 0.50)), active_minutes - length)
    return ((max(0, start), max(0, start) + length),)


def _gpu_inference_incident_intensity(active_minutes: int) -> "np.ndarray":
    """Ramp/plateau/recovery envelope for time-coherent GPU degradation."""
    intensity = np.zeros(active_minutes, dtype=np.float64)
    for start, end in _gpu_inference_incident_windows(active_minutes):
        length = end - start
        if length <= 0:
            continue
        positions = np.linspace(0.0, 1.0, length, endpoint=True)
        envelope = np.ones(length, dtype=np.float64)
        ramp = positions < 0.20
        recovery = positions > 0.82
        envelope[ramp] = positions[ramp] / 0.20
        envelope[recovery] = 1.0 - ((positions[recovery] - 0.82) / 0.18) * 0.35
        envelope = np.clip(envelope, 0.0, 1.0)
        intensity[start:end] = np.maximum(intensity[start:end], envelope)
    return intensity


def _gpu_inference_failure_minutes(active_minutes: int) -> frozenset[int]:
    """Sparse GPU serving labels with coherent incident-window concentration."""
    rng = np.random.RandomState(20260527)
    failure: set[int] = set()
    incident_windows = _gpu_inference_incident_windows(active_minutes)

    def can_add(start: int, length: int) -> bool:
        if start < 0 or start + length > active_minutes:
            return False
        for minute in range(start - 1, start + length + 1):
            if minute in failure:
                return False
        return True

    def add(start: int, length: int) -> bool:
        if not can_add(start, length):
            return False
        failure.update(range(start, start + length))
        return True

    def is_incident_minute(minute: int) -> bool:
        return any(start <= minute < end for start, end in incident_windows)

    def incident_failure_count() -> int:
        return sum(1 for minute in failure if is_incident_minute(minute))

    def shuffled_candidates(candidates: list[int]) -> list[int]:
        candidates = list(candidates)
        rng.shuffle(candidates)
        return candidates

    def center_ranked_candidates(candidates: list[int]) -> list[int]:
        if not incident_windows:
            return shuffled_candidates(candidates)
        center = (incident_windows[0][0] + incident_windows[0][1]) / 2.0
        return sorted(candidates, key=lambda minute: (abs(minute - center), minute))

    target_rows = min(active_minutes, int(round(active_minutes * 0.02408)))

    if len(failure) < target_rows and active_minutes > 5:
        add(5, 1)
    if len(failure) < target_rows and active_minutes > 30:
        add(active_minutes - 18, 1)

    # Match the reference's run geometry: almost all singleton failures, with
    # a small tail of two-minute runs and no longer failure stretches. Most
    # pairs are placed inside incident windows so rolling detectors see a
    # consistent degradation episode rather than only uniformly sprinkled rows.
    two_minute_runs = min(80, max(0, (target_rows - len(failure)) // 2))
    added_two = 0
    incident_pair_target = min(72, two_minute_runs)
    incident_pair_starts = [
        minute
        for start, end in incident_windows
        for minute in range(
            max(start, ((start + end) // 2) - 180),
            min(max(start, end - 1), ((start + end) // 2) + 180),
            3,
        )
    ]
    for start in center_ranked_candidates(incident_pair_starts):
        if added_two >= incident_pair_target:
            break
        if add(start, 2):
            added_two += 1

    background_pair_starts = [
        minute
        for minute in range(0, max(0, active_minutes - 1))
        if not is_incident_minute(minute) and not is_incident_minute(minute + 1)
    ]
    for start in shuffled_candidates(background_pair_starts):
        if added_two >= two_minute_runs:
            break
        if add(start, 2):
            added_two += 1
    if added_two < two_minute_runs:
        raise RuntimeError("Unable to place GPU inference two-minute runs")

    incident_target_rows = min(int(round(target_rows * 0.60)),
                               max(0, sum(end - start for start, end in incident_windows) // 2))
    incident_singletons = [
        minute
        for start, end in incident_windows
        for minute in range(start, end)
    ]
    for minute in center_ranked_candidates(incident_singletons):
        if len(failure) >= target_rows or incident_failure_count() >= incident_target_rows:
            break
        add(minute, 1)

    # Add a small daily background of sparse labels outside the core so the
    # file still contains reference-like operational noise, but do it after
    # the dense core is placed so the primary incident stays detector-visible.
    days = int(math.ceil(active_minutes / (SECONDS_PER_DAY / DEFAULT_INTERVAL_SECONDS)))
    for day in range(days):
        day_start = int(day * SECONDS_PER_DAY / DEFAULT_INTERVAL_SECONDS)
        if day_start >= active_minutes:
            break
        hour = int(rng.randint(0, 24))
        burst_count = int(rng.choice([3, 4, 5], p=[0.45, 0.40, 0.15]))
        minute_choices = list(rng.choice(np.arange(0, 60, 2), size=burst_count, replace=False))
        for minute_in_hour in minute_choices:
            if len(failure) >= target_rows:
                break
            minute = day_start + hour * 60 + int(minute_in_hour)
            if not is_incident_minute(minute):
                add(minute, 1)

    background_singletons = [
        minute
        for minute in range(active_minutes)
        if not is_incident_minute(minute)
    ]
    for minute in shuffled_candidates(background_singletons):
        if len(failure) >= target_rows:
            break
        add(minute, 1)
    if len(failure) < target_rows:
        for minute in shuffled_candidates(list(range(active_minutes))):
            if len(failure) >= target_rows:
                break
            add(minute, 1)
    if len(failure) < target_rows:
        raise RuntimeError("Unable to place GPU inference failure rows")

    return frozenset(failure)


def _gpu_feature_minutes(
    *,
    active_minutes: int,
    target_count: int,
    failure_minutes: frozenset[int],
    failure_count: int,
    score: "np.ndarray",
    preferred_failure_pools: tuple[tuple[set[int], int], ...] = (),
    preferred_non_failure_pools: tuple[tuple[set[int], int], ...] = (),
) -> frozenset[int]:
    """Select feature-high/low minutes with controlled failure overlap."""
    selected: set[int] = set()
    failure_pool = set(failure_minutes)

    if failure_count > target_count:
        raise ValueError(
            f"GPU inference feature failure_count {failure_count} exceeds "
            f"target_count {target_count}"
        )

    for pool, count in preferred_failure_pools:
        needed = min(count, failure_count - len(selected))
        selected |= _ranked_choice_set((failure_pool & pool) - selected,
                                       needed, score)

    remaining_failure = failure_count - len(selected)
    selected |= _ranked_choice_set(failure_pool - selected,
                                   remaining_failure, score)

    non_failure_pool = set(range(active_minutes)) - failure_pool
    selected_non_failure = len(selected - failure_pool)
    target_non_failure = target_count - failure_count

    for pool, count in preferred_non_failure_pools:
        needed = min(count, target_non_failure - selected_non_failure)
        additions = _ranked_choice_set((non_failure_pool & pool) - selected,
                                       needed, score)
        selected |= additions
        selected_non_failure += len(additions)

    selected |= _ranked_choice_set(non_failure_pool - selected,
                                   target_count - len(selected), score)
    return frozenset(selected)


def _gpu_inference_reference_schedule(active_minutes: int) -> dict[str, object]:
    """Build deterministic row sets that mirror the reference CSV signal.

    The target counts come from ``observability_telemetry.csv``: 1,204 failure
    rows, 744 rows with memory fragmentation >= 0.8, 3,737 rows with memory
    pressure >= 0.9, 3,271 rows with utilization <= 0.65, 5,000 rows with
    throughput <= 1, 420 rows with p99 latency >= 900, and 31,907 rows with
    KV cache usage >= 0.95.
    """
    rng = np.random.RandomState(20260528)
    failure = _gpu_inference_failure_minutes(active_minutes)
    stress = _gpu_inference_stress_score(rng, active_minutes, failure)
    incident_intensity = _gpu_inference_incident_intensity(active_minutes)
    core_pool = set(np.flatnonzero(incident_intensity >= 0.82).astype(int).tolist())
    core_score = stress + 1.15 * incident_intensity
    strict_core_count = min(320, len(core_pool))
    strict_core = _ranked_choice_set(core_pool, strict_core_count, core_score)
    strict_failure_count = len(strict_core & failure)
    strict_non_failure_count = len(strict_core - failure)

    def failure_count_for(target_count: int, desired: int) -> int:
        lower = strict_failure_count
        upper = target_count - strict_non_failure_count
        return max(lower, min(desired, upper))

    strict_failure_pool = ((strict_core, strict_failure_count),)
    strict_non_failure_pool = ((strict_core, strict_non_failure_count),)

    frag_score = (1.00 * stress + 1.10 * incident_intensity
                  + rng.normal(0.0, 0.20, active_minutes))
    pressure_score = (0.74 * stress + 1.18 * incident_intensity
                      + rng.normal(0.0, 0.28, active_minutes))
    util_score = (0.84 * stress + 1.12 * incident_intensity
                  + rng.normal(0.0, 0.24, active_minutes))
    p99_score = (0.68 * stress + 1.30 * incident_intensity
                 + rng.normal(0.0, 0.24, active_minutes))
    throughput_score = (0.56 * stress + 1.05 * incident_intensity
                        + rng.normal(0.0, 0.34, active_minutes))
    kv_score = (0.78 * stress + 0.72 * incident_intensity
                + rng.normal(0.0, 0.18, active_minutes))

    high_frag = _gpu_feature_minutes(
        active_minutes=active_minutes, target_count=744,
        failure_minutes=failure, failure_count=failure_count_for(744, 420),
        score=frag_score,
        preferred_failure_pools=strict_failure_pool,
        preferred_non_failure_pools=strict_non_failure_pool,
    )
    high_pressure = _gpu_feature_minutes(
        active_minutes=active_minutes, target_count=3_737,
        failure_minutes=failure, failure_count=failure_count_for(3_737, 520),
        score=pressure_score,
        preferred_failure_pools=strict_failure_pool,
        preferred_non_failure_pools=strict_non_failure_pool,
    )
    low_util = _gpu_feature_minutes(
        active_minutes=active_minutes, target_count=3_271,
        failure_minutes=failure, failure_count=failure_count_for(3_271, 520),
        score=util_score,
        preferred_failure_pools=strict_failure_pool,
        preferred_non_failure_pools=strict_non_failure_pool,
    )
    p99_high = _gpu_feature_minutes(
        active_minutes=active_minutes, target_count=420,
        failure_minutes=failure, failure_count=failure_count_for(420, 260),
        score=p99_score,
        preferred_failure_pools=strict_failure_pool,
        preferred_non_failure_pools=strict_non_failure_pool,
    )
    throughput_low = _gpu_feature_minutes(
        active_minutes=active_minutes, target_count=5_000,
        failure_minutes=failure, failure_count=failure_count_for(5_000, 520),
        score=throughput_score,
        preferred_failure_pools=strict_failure_pool,
        preferred_non_failure_pools=strict_non_failure_pool,
    )
    kv_high = _gpu_feature_minutes(
        active_minutes=active_minutes, target_count=31_907,
        failure_minutes=failure, failure_count=1_020, score=kv_score,
        preferred_failure_pools=strict_failure_pool,
        preferred_non_failure_pools=strict_non_failure_pool,
    )
    return {
        "failure": failure,
        "stress": stress,
        "incident_intensity": incident_intensity,
        "strict_core": strict_core,
        "high_frag": high_frag,
        "high_pressure": high_pressure,
        "low_util": low_util,
        "p99_high": p99_high,
        "throughput_low": throughput_low,
        "kv_high": kv_high,
    }


def _gpu_inference_fragmentation_specs(
) -> tuple[tuple[tuple[str, dict], ...], tuple[tuple[str, dict], ...]]:
    """Scenario specs modeled after the reference observability telemetry CSV.

    The CSV's failure labels are mostly isolated one-row points. The
    strongest useful signal is not a perfect spike but a statistical lift in
    fragmentation/pressure plus weaker utilization, throughput, and latency
    evidence. This scenario therefore generates a dense but sparse-label
    incident field instead of a handful of perfectly separable pulses.
    """
    start = 0
    duration_seconds = DEFAULT_ROW_COUNT * DEFAULT_INTERVAL_SECONDS
    active_minutes = int(duration_seconds // DEFAULT_INTERVAL_SECONDS)
    schedule = _gpu_inference_reference_schedule(active_minutes)

    def minute_idx(t_within: float) -> int:
        return int(math.floor((t_within + 1e-9) / DEFAULT_INTERVAL_SECONDS))

    def stress_for(minute: int) -> float:
        return float(schedule["stress"][minute])

    def incident_for(minute: int) -> float:
        return float(schedule["incident_intensity"][minute])

    def batch_size_gen(ts, col, t_within, span_idx, rng):
        return _weighted_choice(
            rng,
            (1.0, 4.0, 8.0, 16.0, 32.0),
            (0.12, 0.18, 0.30, 0.25, 0.15),
        )

    def model_size_gen(ts, col, t_within, span_idx, rng):
        return _weighted_choice(rng, (7.0, 13.0, 70.0), (0.35, 0.45, 0.20))

    def memory_fragmentation_gen(ts, col, t_within, span_idx, rng):
        minute = minute_idx(t_within)
        stress = stress_for(minute)
        incident = incident_for(minute)
        if minute in schedule["high_frag"]:
            base = 0.80 + 0.12 * incident + 0.06 * stress + rng.normal(0.0, 0.008)
            return _clamp(float(base), 0.80, 0.96)
        base = 0.18 + 0.45 * stress + 0.18 * incident + rng.normal(0.0, 0.035)
        return _clamp(float(base), 0.14, 0.799)

    def gpu_memory_pressure_gen(ts, col, t_within, span_idx, rng):
        minute = minute_idx(t_within)
        stress = stress_for(minute)
        incident = incident_for(minute)
        if minute in schedule["high_pressure"]:
            base = 0.90 + 0.08 * incident + 0.035 * stress + rng.normal(0.0, 0.006)
            return _clamp(float(base), 0.90, 0.99)
        base = 0.36 + 0.38 * stress + 0.18 * incident + rng.normal(0.0, 0.036)
        return _clamp(float(base), 0.30, 0.899)

    def kv_cache_usage_gen(ts, col, t_within, span_idx, rng):
        minute = minute_idx(t_within)
        stress = stress_for(minute)
        incident = incident_for(minute)
        if minute in schedule["kv_high"]:
            base = 0.95 + 0.04 * incident + 0.02 * stress + rng.normal(0.0, 0.003)
            return _clamp(float(base), 0.95, 1.0)
        base = 0.20 + 0.54 * stress + 0.24 * incident + rng.normal(0.0, 0.040)
        return _clamp(float(base), 0.20, 0.949)

    def gpu_utilization_gen(ts, col, t_within, span_idx, rng):
        minute = minute_idx(t_within)
        stress = stress_for(minute)
        incident = incident_for(minute)
        if minute in schedule["low_util"]:
            base = 0.66 - 0.11 * incident - 0.035 * stress + rng.normal(0.0, 0.008)
            return _clamp(float(base), 0.52, 0.65)
        base = 0.90 - 0.16 * stress - 0.13 * incident + rng.normal(0.0, 0.018)
        return _clamp(float(base), 0.651, 0.93)

    def throughput_tps_gen(ts, col, t_within, span_idx, rng):
        minute = minute_idx(t_within)
        stress = stress_for(minute)
        incident = incident_for(minute)
        if minute in schedule["throughput_low"]:
            return _clamp(float(0.96 - 0.42 * incident + rng.normal(0.0, 0.020)), 0.45, 0.999)
        base = float(rng.lognormal(np.log(18.0 - 8.0 * stress - 4.0 * incident), 0.50))
        return _clamp(base, 1.001, 220.0)

    def latency_p50_ms_gen(ts, col, t_within, span_idx, rng):
        minute = minute_idx(t_within)
        stress = stress_for(minute)
        incident = incident_for(minute)
        if minute in schedule["p99_high"]:
            return _clamp(float(130.0 + 130.0 * incident + 50.0 * stress
                                + rng.normal(0.0, 8.0)), 130.0, 320.0)
        base = float(rng.lognormal(np.log(58.0 + 88.0 * stress + 58.0 * incident), 0.22))
        return _clamp(base, 33.0, 245.0)

    def latency_p99_ms_gen(ts, col, t_within, span_idx, rng):
        minute = minute_idx(t_within)
        stress = stress_for(minute)
        incident = incident_for(minute)
        if minute in schedule["p99_high"]:
            return _clamp(float(900.0 + 260.0 * incident + 90.0 * stress
                                + rng.normal(0.0, 12.0)), 900.0, 1240.0)
        base = float(rng.lognormal(np.log(150.0 + 360.0 * stress + 170.0 * incident), 0.22))
        return _clamp(base, 80.0, 899.0)

    specs: list[tuple[str, dict]] = [
        ("gpu_inference", {
            "time_offset": start,
            "duration_seconds": duration_seconds,
            "shape": "sustained",
            "metric": "batch_size",
            "description": "GPU inference serving layer - batch_size follows reference-like discrete serving batches",
            "generator": batch_size_gen,
        }),
        ("gpu_inference", {
            "time_offset": start,
            "duration_seconds": duration_seconds,
            "shape": "sustained",
            "metric": "model_size_b",
            "description": "GPU inference serving layer - model_size_b follows reference-like 7B/13B/70B mix",
            "generator": model_size_gen,
        }),
        ("gpu_inference", {
            "time_offset": start,
            "duration_seconds": duration_seconds,
            "shape": "sustained",
            "metric": "memory_fragmentation",
            "description": "GPU allocator fragmentation incident field - memory_fragmentation has reference-like high-risk lift",
            "generator": memory_fragmentation_gen,
        }),
        ("gpu_inference", {
            "time_offset": start,
            "duration_seconds": duration_seconds,
            "shape": "sustained",
            "metric": "gpu_memory_pressure",
            "description": "GPU memory pressure incident field - pressure high rows provide weak but useful lift",
            "generator": gpu_memory_pressure_gen,
        }),
        ("gpu_inference", {
            "time_offset": start,
            "duration_seconds": duration_seconds,
            "shape": "sustained",
            "metric": "kv_cache_usage",
            "description": "KV cache serving profile - high cache occupancy mirrors reference distribution",
            "generator": kv_cache_usage_gen,
        }),
        ("gpu_inference", {
            "time_offset": start,
            "duration_seconds": duration_seconds,
            "shape": "sustained",
            "metric": "gpu_utilization",
            "description": "GPU utilization incident field - utilization dips are weak failure evidence",
            "generator": gpu_utilization_gen,
        }),
        ("gpu_inference", {
            "time_offset": start,
            "duration_seconds": duration_seconds,
            "shape": "sustained",
            "metric": "throughput_tps",
            "description": "GPU throughput incident field - low-throughput minutes are common but only weakly predictive",
            "generator": throughput_tps_gen,
        }),
        ("gpu_inference", {
            "time_offset": start,
            "duration_seconds": duration_seconds,
            "shape": "sustained",
            "metric": "latency_p50_ms",
            "description": "GPU inference p50 latency field - heavy-tailed latency with incident lift",
            "generator": latency_p50_ms_gen,
        }),
        ("gpu_inference", {
            "time_offset": start,
            "duration_seconds": duration_seconds,
            "shape": "sustained",
            "metric": "latency_p99_ms",
            "description": "GPU inference p99 latency field - sparse tail spikes avoid perfect separability",
            "generator": latency_p99_ms_gen,
        }),
    ]

    for minute in sorted(schedule["failure"]):
        specs.append(("gpu_inference", {
            "time_offset": start + minute * DEFAULT_INTERVAL_SECONDS,
            "metric": "failure",
            "description": "GPU inference sparse reference-like failure label",
            "generator": _const_generator(1.0),
        }))

    cascades: list[tuple[str, dict]] = [
        ("llm_analytics", {
            "time_offset": start + 90,
            "metric": "avg_llm_latency_ms",
            "description": "Cascading: GPU inference allocator recovery drags LLM latency to ~1,900 ms",
            "generator": lambda ts, idx, rng: 1900 + rng.normal(0, 60),
            "severity": DEFAULT_SEVERITY,
        }),
        ("llm_analytics", {
            "time_offset": start + 20 * 3600 + 90,
            "metric": "llm_api_error_rate",
            "description": "Cascading: dense GPU inference failure field surfaces as LLM API errors (~12%)",
            "generator": _const_generator(0.12),
            "severity": DEFAULT_SEVERITY,
        }),
    ]

    return tuple(specs), tuple(cascades)


(
    _GPU_INFERENCE_FRAGMENTATION_PRIMARY_SPECS,
    _GPU_INFERENCE_FRAGMENTATION_CASCADE_SPECS,
) = _gpu_inference_fragmentation_specs()


# ------------------------------------------------------------------
# Named scenario registry (full migration complete).
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


def _natural_column(spec: MetricSpec, ts_array: np.ndarray, elapsed: np.ndarray,
                    rng: "np.random.RandomState",
                    *,
                    noise: np.ndarray | None = None,
                    latency_factor: np.ndarray | None = None,
                    error_offset: np.ndarray | None = None,
                    baseline_override: np.ndarray | None = None) -> np.ndarray:
    """Vectorized natural-value column. Multiplier/additive must accept arrays.

    The optional kwargs decouple two pieces of state that were previously
    baked into ``MetricSpec.multiplier`` / ``MetricSpec.additive`` lambdas
    by ``_compose_topology_*_specs``. Called with ``latency_factor`` and
    ``error_offset`` equal to what the lambdas would have computed, the
    result matches the lambda-baked path byte-for-byte on the locked
    baselines (pinned by the N=3 golden hashes; IEEE-754 multiplication
    and addition are not associative, so the equality is an empirical
    property of the shipped seeds holding through the 3-decimal CSV
    rounding, not a mathematical guarantee), and they unlock the
    per-instance saturation path where each instance's curve depends on
    its own upstream view:

    * ``noise`` — pre-drawn ``rng.normal(0, spec.std, n_rows)`` array.
      When provided, the function uses it instead of drawing fresh
      noise so multiple call sites (e.g. one per instance) can share
      the same noise floor without advancing the RNG more than once.
      Pass ``None`` to keep the historic single-call draw.
    * ``latency_factor`` — per-row multiplicative array applied
      *between* the natural multiplier and the natural additive,
      matching where ``_compose_topology_saturation_specs`` baked the
      saturation latency multiplier into ``MetricSpec.multiplier``.
    * ``error_offset`` — per-row additive array applied *after* the
      natural additive and *before* ``clip_min``, matching where the
      saturation error offset was baked into ``MetricSpec.additive``.
    * ``baseline_override`` — per-row array that REPLACES the natural
      baseline (used by per-instance coupling where the downstream
      load metric is fully baked from upstream views). Composes with
      ``latency_factor`` / ``error_offset`` after the replacement.
      Mirrors what ``_compose_topology_coupled_specs`` produces by
      replacing ``base=0, std=0, multiplier=None,
      additive=lambda: coupled`` on the spec — the override is the
      ``coupled`` array exactly.
    """
    if baseline_override is not None:
        col = np.array(baseline_override, dtype=np.float64, copy=True)
    else:
        col = np.full(elapsed.shape, spec.base, dtype=np.float64)
        if spec.std > 0:
            if noise is None:
                noise = rng.normal(0.0, spec.std, elapsed.shape[0])
            col += noise
        if spec.multiplier is not None:
            col *= spec.multiplier(ts_array, elapsed)
    if latency_factor is not None:
        col *= latency_factor
    if baseline_override is None and spec.additive is not None:
        col += spec.additive(ts_array, elapsed)
    if error_offset is not None:
        col += error_offset
    if spec.clip_min is not None:
        np.maximum(col, spec.clip_min, out=col)
    return col


# Sentinel returned by ``_resolve_instance_filter`` when an ``instance_filter``
# matches zero active instances. Distinct from ``None`` (which means "no
# filter / matches every instance"); the caller emits a single WARNING per
# skipped spec and drops it from the override pipeline.
_INSTANCE_FILTER_NO_MATCH = object()


def _resolve_instance_filter(spec_filter, instances: list["Instance"]):
    """Resolve a spec's ``instance_filter`` against the active instance list.

    Returns ``None`` when every active instance matches (no filter declared
    or filter matches everyone) — the caller takes the shared-values fast
    path and preserves the single-shared-buffer behavior.

    Returns ``_INSTANCE_FILTER_NO_MATCH`` when the filter matches zero
    active instances — the caller emits one WARNING per spec and drops it.

    Returns a ``bool`` ``np.ndarray`` of length ``len(instances)`` for
    partial matches — the caller applies overrides only to selected
    per-instance buffers.

    ``spec_filter`` must already have passed the structural validation in
    ``_validate_scenario_spec`` (``None``, iterable of ``str``, or
    callable). Membership against ``INSTANCES`` is not checked at import
    time because ``--instance-config`` (a later phase) will register
    runtime ids; this function compares against the per-run ``instances``
    list and warns on no-match instead.
    """
    if spec_filter is None:
        return None
    if callable(spec_filter):
        mask = np.array(
            [bool(spec_filter(inst)) for inst in instances], dtype=bool
        )
    else:
        id_set = frozenset(spec_filter)
        mask = np.array(
            [inst.id is not None and inst.id in id_set for inst in instances],
            dtype=bool,
        )
    if not mask.any():
        return _INSTANCE_FILTER_NO_MATCH
    if mask.all():
        return None
    return mask


# ------------------------------------------------------------------
# Core generator
# ------------------------------------------------------------------
def generate_component(component_name, specs: list[MetricSpec], anomaly_specs,
                       *, base_dir, total_seconds, drop_rate, interval=1.0,
                       ts_array=None, ts_strings=None, emit_metrics=True,
                       dst_inject_day=0, ctx: "RunContext",
                       instances: list["Instance"] | None = None,
                       topology_capture: dict[str, dict[str, np.ndarray]] | None = None,
                       topology_capture_by_instance: dict[str, list[dict[str, np.ndarray]]] | None = None,
                       coupling_arrays_per_instance: list[dict[str, np.ndarray]] | None = None,
                       saturation_arrays_per_instance: list[dict[str, tuple[np.ndarray | None, np.ndarray | None]]] | None = None,
                       apply_dtype_int_cast: bool = True):
    """
    specs: list of MetricSpec (one per CSV column, in column order)
    anomaly_specs: list of {'time_offset': int, 'metric': str, 'description': str, 'generator': fn}
    instances: optional list of ``Instance`` carrying the per-component
        dimension topology (Phase 1). ``None`` resolves to a single
        anonymous ``Instance()`` so today's output stays byte-identical;
        dimension columns are emitted when ``len > 1`` or any instance
        has non-None dimension fields (the Phase 2 long-form CSV layout).
    apply_dtype_int_cast: if True (default), round columns with ``dtype="int"``
        to whole numbers via ``np.rint`` before derivations. ``main()``
        always passes True; programmatic callers may pass False to keep
        the pre-cast fractional contrast.

    Vectorized: natural-value math is one numpy op per metric; anomaly overrides
    are masked writes on the column arrays; packet loss is a single boolean mask
    decided up front so a dropped row emits neither a CSV row nor a manifest
    entry. A shaped span whose *leading* row(s) are dropped still records its
    manifest entry, anchored at the span's first kept row (a span dropped in
    its entirety records none). ``ts_array``/``ts_strings`` are optional so
    callers can share them across components (main() does this). The drop mask
    is drawn per call so each component keeps its independent drop pattern.

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

    # ctx is the sole entry point for per-run state. It carries the RNG
    # (ctx.rng), the anomaly manifest accumulator (ctx.anomalies), and the
    # cascade registry (ctx.cascading_anomalies). Callers that need to read
    # the manifest after generation must own the RunContext; constructing
    # one inside this function would discard the appended entries.
    if ctx is None:
        raise TypeError(
            "generate_component() requires an explicit ctx= argument; "
            "pass RunContext(rng=np.random.RandomState(seed))."
        )
    rng = ctx.rng

    # Resolve the active per-component instance topology. Phase 1 only
    # validates the shape and falls back to a single anonymous ``Instance()``
    # so today's dimensionless CSV output is preserved. Phases 2–8 wire the
    # dimension columns, anomaly filtering, and OTEL attributes.
    if instances is None:
        instances = [Instance()]
    if not instances:
        raise ValueError(
            f"generate_component({component_name!r}) requires at least one "
            f"Instance; got an empty list."
        )
    # Per-entry shape checks mirror _validate_instances_registry so a caller
    # bypassing the registry (test fixtures, ad-hoc reuse) gets a clear
    # ValueError naming the call site, not a downstream AttributeError /
    # TypeError once Phases 2–4 start consuming Instance metadata.
    _validate_instance_list(
        instances, where=f"generate_component({component_name!r}) instances"
    )

    # Defense-in-depth: ``parse_args`` rejects ``--inject-dst-artifact-day``
    # paired with multi-instance at the CLI; this guard mirrors the
    # rejection for direct callers (tests, future consumers) that bypass
    # the CLI. The original rationale was correctness — the long-form
    # writer rebuilt rows from pre-splice timestamps and silently dropped
    # the duplicated hour. The long-form path now routes through
    # ``_format_csv_row_block``, which applies the splice per-instance,
    # so the guard now stands on design grounds: the multi-instance
    # long-form CSV emits per-instance row blocks, and per-block splicing
    # surfaces non-monotonic timestamps inside each block that
    # ``heapq.merge`` (``gauges.csv`` / ``combined_metrics_unified.csv``)
    # cannot resolve.
    _is_anonymous = _is_anonymous_instance_list(instances)
    if not _is_anonymous and dst_inject_day > 0:
        raise ValueError(
            f"generate_component({component_name!r}): dst_inject_day > 0 "
            f"is incompatible with a non-anonymous instance list by design — "
            f"per-instance DST splicing would surface non-monotonic "
            f"timestamps inside each row block that downstream long-form "
            f"merges (gauges.csv / combined_metrics_unified.csv) cannot "
            f"resolve. Pass instances=[Instance()] or dst_inject_day=0."
        )

    # Merge primary anomalies with cascading anomalies
    all_anomalies = list(anomaly_specs)
    if component_name in ctx.cascading_anomalies:
        all_anomalies.extend(ctx.cascading_anomalies[component_name])

    # Phase 4: resolve each spec's ``instance_filter`` against the
    # active ``instances`` list before expansion. Specs whose filter matches
    # zero instances are dropped here (one WARNING per skipped spec) so they
    # never produce manifest entries or value writes. Specs with no filter
    # or whose filter matches every instance are mapped to ``None`` so the
    # shared-values fast path stays byte-identical to Phase 2 (locked
    # built-in hashes do not move). ``resolved_filters`` is keyed by
    # ``id(spec_dict)`` so the override loop below can look up the per-spec
    # mask in O(1).
    resolved_filters: dict[int, "np.ndarray | None"] = {}
    filter_skips: list[tuple[str, str]] = []
    kept_anomalies: list[dict] = []
    for s in all_anomalies:
        resolved = _resolve_instance_filter(s.get("instance_filter"), instances)
        if resolved is _INSTANCE_FILTER_NO_MATCH:
            filter_skips.append((s["metric"], s["description"]))
            continue
        resolved_filters[id(s)] = resolved
        kept_anomalies.append(s)
    all_anomalies = kept_anomalies
    if filter_skips:
        # Sorted by (metric, description) so WARNING order is deterministic
        # regardless of dict iteration order; mirrors the convention in
        # ``_resolve_scenarios``.
        for metric, desc in sorted(filter_skips):
            print(
                f"WARNING: {component_name}: skipping anomaly spec "
                f"metric={metric!r} description={desc!r} — instance_filter "
                f"matched zero active instances.",
                file=sys.stderr,
            )

    # Keep in-range anomaly spans compact here. The override loop below merges
    # these ranges lazily so long sustained scenarios do not materialize one
    # Python tuple per affected row before vectorized generation starts.
    override_spans: list[tuple[int, int, dict, int]] = []
    out_of_range: list[dict] = []
    for spec_order, s in enumerate(all_anomalies):
        if s["time_offset"] < 0:
            out_of_range.append(s)
            continue
        start_idx = int(round(s["time_offset"] / interval))
        duration_seconds = float(s.get("duration_seconds", 0) or 0)
        duration_rows = max(1, int(np.ceil(duration_seconds / interval)))
        end_idx_exclusive = min(n_rows, start_idx + duration_rows)
        if start_idx >= n_rows or end_idx_exclusive <= start_idx:
            out_of_range.append(s)
            continue
        override_spans.append((start_idx, end_idx_exclusive, s, spec_order))
    if out_of_range:
        non_negative_offsets = [
            s["time_offset"] for s in out_of_range
            if s["time_offset"] >= 0
        ]
        if non_negative_offsets:
            max_start_idx = max(
                int(round(offset / interval))
                for offset in non_negative_offsets
            )
            needed_seconds = (max_start_idx + 1) * interval
            needed_days = max(
                1.0,
                math.nextafter(needed_seconds / SECONDS_PER_DAY, math.inf),
            )
            include_hint = (
                f"Run with --duration-days {needed_days!r} to include them."
            )
        else:
            include_hint = "Check anomaly specs for negative time_offset values."
        print(
            f"WARNING: {component_name}: skipping {len(out_of_range)} anomaly spec(s) "
            f"with time_offset outside [0, {total_seconds}). "
            f"{include_hint}",
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

    def iter_sorted_overrides():
        """Yield concrete overrides in row/metric/spec order without a row list."""
        heap = [
            (start_idx, aspec["metric"], spec_order, start_idx, end_idx, aspec)
            for start_idx, end_idx, aspec, spec_order in override_spans
        ]
        heapq.heapify(heap)
        while heap:
            row_idx, metric, spec_order, start_idx, end_idx, aspec = heapq.heappop(heap)
            span_idx = row_idx - start_idx
            yield row_idx, aspec, span_idx * interval, span_idx
            next_row_idx = row_idx + 1
            if next_row_idx < end_idx:
                heapq.heappush(
                    heap,
                    (next_row_idx, metric, spec_order, start_idx, end_idx, aspec),
                )

    if ts_array is None or ts_strings is None:
        ts_array, ts_strings = _build_timestamp_arrays(total_seconds, interval)
    drop_mask = rng.random(n_rows) < drop_rate

    # Elapsed seconds (not row index) so daily/hourly seasonality generators
    # produce the same wall-clock shape at any sampling interval.
    elapsed = np.arange(n_rows, dtype=np.float64) * interval

    # Natural values: one column array per metric, computed in a single numpy op.
    n_cols = len(specs)
    values = np.empty((n_rows, n_cols), dtype=np.float64)

    # phase 8: per-instance topology dispatch. When the caller
    # passes per-instance coupling / saturation arrays (under
    # realistic topology coupling with N>1 or a non-default
    # instance config), each instance K consumes its own arrays via
    # ``_natural_column``'s ``baseline_override`` / ``latency_factor``
    # / ``error_offset`` kwargs. Under symmetric upstream (no
    # ``instance_filter`` on an upstream load metric) the arrays are
    # byte-identical across instances → fast-path single draw shared
    # across all instances preserves today's locked N=3 hashes.
    # Under asymmetric upstream the arrays diverge → per-instance
    # natural-column draws (with shared noise via the ``noise=``
    # kwarg so the noise floor matches the symmetric case).
    use_per_instance_topology = (
        coupling_arrays_per_instance is not None
        and saturation_arrays_per_instance is not None
    )
    # Reject the half-passed shape up front so programmatic callers
    # see a clear error rather than a silent fall-back to the legacy
    # shared-arrays path that would emit wrong per-instance values.
    if (
        (coupling_arrays_per_instance is None)
        != (saturation_arrays_per_instance is None)
    ):
        raise ValueError(
            f"generate_component({component_name!r}) requires both "
            "coupling_arrays_per_instance and saturation_arrays_per_instance "
            "or neither; got "
            f"coupling={'present' if coupling_arrays_per_instance is not None else 'None'} "
            f"saturation={'present' if saturation_arrays_per_instance is not None else 'None'}."
        )
    pre_populated_per_instance_eager: dict[int, np.ndarray] = {}
    if use_per_instance_topology:
        n_inst_local = len(instances)
        # Defensive shape check: the per-instance arrays are indexed by
        # instance position below ([0] for the fast path, range() for
        # the divergent loop). A mismatched length would surface as a
        # confusing IndexError mid-loop; raise a clear ValueError up
        # front so programmatic callers see exactly which list is the
        # wrong shape. ``main()`` always passes lists built from
        # ``_compute_topology_arrays_per_instance`` with this length,
        # so this branch only catches third-party or test misuse.
        if (
            len(coupling_arrays_per_instance) != n_inst_local
            or len(saturation_arrays_per_instance) != n_inst_local
        ):
            raise ValueError(
                f"generate_component({component_name!r}) per-instance "
                f"topology arrays must match len(instances)={n_inst_local}; "
                f"got coupling_arrays_per_instance="
                f"{len(coupling_arrays_per_instance)} and "
                f"saturation_arrays_per_instance="
                f"{len(saturation_arrays_per_instance)}."
            )
        # Identify which specific instances diverge from instance 0 so
        # the divergent path only allocates per-instance buffers for
        # the instances that actually need them. At N=20 with a single
        # asymmetric upstream pod, this saves 18 full (n_rows × n_cols)
        # buffers compared to the previous "allocate-N-up-front" shape
        # (~9.7 GB at 7d / 1s / N=20 / 10 metrics).
        #
        # Divergence is always re-derived from the passed arrays so
        # correctness does not depend on any caller-supplied hint.
        divergent_instances: set[int] = set()
        if n_inst_local > 1:
            ref_coupling = coupling_arrays_per_instance[0]
            ref_saturation = saturation_arrays_per_instance[0]
            for inst_idx_k in range(1, n_inst_local):
                if not _arrays_equal_dict(
                    coupling_arrays_per_instance[inst_idx_k], ref_coupling
                ) or not _sat_tuples_equal_dict(
                    saturation_arrays_per_instance[inst_idx_k], ref_saturation
                ):
                    divergent_instances.add(inst_idx_k)

        if not divergent_instances:
            # Shared fast path — one draw per metric, reusable across instances.
            coupling = coupling_arrays_per_instance[0]
            saturation = saturation_arrays_per_instance[0]
            for col, spec in enumerate(specs):
                baseline_override = coupling.get(spec.name)
                lf, eo = saturation.get(spec.name, (None, None))
                values[:, col] = _natural_column(
                    spec, ts_array, elapsed, rng,
                    latency_factor=lf, error_offset=eo,
                    baseline_override=baseline_override,
                )
        else:
            # Divergent — write instance 0 into ``values`` (already
            # allocated) and allocate per-instance buffers only for
            # the instances that diverge. Noise per metric is drawn
            # once and shared so the only divergence flows through
            # topology arrays. Non-divergent instances stay on
            # ``values`` via the missing-key lookup in
            # ``per_instance_values`` below.
            divergent_buffers: dict[int, np.ndarray] = {
                inst_idx_k: np.empty((n_rows, n_cols), dtype=np.float64)
                for inst_idx_k in divergent_instances
            }
            for col, spec in enumerate(specs):
                shared_noise = None
                if spec.std > 0 and (
                    coupling_arrays_per_instance[0].get(spec.name) is None
                ):
                    # Coupled metrics have a baseline_override that replaces the
                    # natural draw entirely — drawing noise would advance the RNG
                    # without producing any output difference. Probe instance 0
                    # only: the per-instance composer assigns a coupling
                    # baseline_override consistently across instances for a
                    # given metric (either all instances get an override or
                    # none do — the existence of an override per metric is
                    # gated on whether any incoming edge contributed, which
                    # is decided once per metric in
                    # ``_compute_topology_arrays_per_instance``), so instance
                    # 0's presence is a faithful proxy for whether *any*
                    # instance will use the natural baseline path.
                    shared_noise = rng.normal(0.0, spec.std, n_rows)
                # Instance 0 always writes into ``values`` (the shared
                # buffer).
                coupling0 = coupling_arrays_per_instance[0]
                saturation0 = saturation_arrays_per_instance[0]
                baseline_override0 = coupling0.get(spec.name)
                lf0, eo0 = saturation0.get(spec.name, (None, None))
                values[:, col] = _natural_column(
                    spec, ts_array, elapsed, rng,
                    noise=shared_noise,
                    latency_factor=lf0, error_offset=eo0,
                    baseline_override=baseline_override0,
                )
                for inst_idx_k in divergent_instances:
                    coupling = coupling_arrays_per_instance[inst_idx_k]
                    saturation = saturation_arrays_per_instance[inst_idx_k]
                    baseline_override = coupling.get(spec.name)
                    lf, eo = saturation.get(spec.name, (None, None))
                    divergent_buffers[inst_idx_k][:, col] = _natural_column(
                        spec, ts_array, elapsed, rng,
                        noise=shared_noise,
                        latency_factor=lf, error_offset=eo,
                        baseline_override=baseline_override,
                    )
            for inst_idx_k, buf in divergent_buffers.items():
                pre_populated_per_instance_eager[inst_idx_k] = buf
    else:
        # Today's path: shared lambda-baked specs, single natural draw per column.
        for col, spec in enumerate(specs):
            values[:, col] = _natural_column(spec, ts_array, elapsed, rng)

    # Apply anomaly overrides. Skip overrides at dropped rows so manifest and
    # CSV stay coherent: a dropped row has no CSV entry, so it must have no
    # manifest entry either. For shaped spans the manifest entry is recorded
    # at the spec's first kept row (see ``manifest_emitted`` below), so a
    # span whose leading rows are dropped still surfaces in the manifest.
    #
    # Phase 4: per-instance value buffers are materialized lazily
    # for any instance touched by a partial ``instance_filter``. An
    # unfiltered override writes to shared ``values`` AND propagates the
    # same write to every already-materialized per-instance buffer (so a
    # later unfiltered spec stays visible to instances whose buffer was
    # forked by an earlier filtered spec). Built-in scenarios omit
    # ``instance_filter``, so this dict stays empty for the default run
    # and the shared-values fast path is preserved — locked Phase 2 hashes
    # do not move. RNG draw order is identical to today's path because
    # ``_resolve_anomaly_value`` is still called exactly once per
    # ``(row_idx, span_idx, aspec)`` triple in row/metric/spec order,
    # regardless of filter resolution.
    name_to_col = {s.name: i for i, s in enumerate(specs)}
    per_instance_values: dict[int, np.ndarray] = dict(pre_populated_per_instance_eager)
    # Manifest bookkeeping: one entry per spec, recorded at the spec's first
    # *kept* row. Historically the entry was gated on ``span_idx == 0``, which
    # silently lost the manifest entry for a shaped span whose first row was
    # dropped — the CSV still carried the anomalous values for the span's
    # surviving rows, but ``anomalies.csv`` (and every consumer of it, e.g.
    # the topology-coupling validator's exclusion windows) never saw the
    # event. Keyed by ``id(aspec)``; spec dicts are alive for the whole loop
    # and the duplicate-spec guard above ensures one entry per spec.
    manifest_emitted: set[int] = set()
    for row_idx, aspec, t_within, span_idx in iter_sorted_overrides():
        if drop_mask[row_idx]:
            continue
        col = name_to_col[aspec["metric"]]
        ts_py = START + datetime.timedelta(seconds=float(row_idx * interval))
        override_value = _resolve_anomaly_value(
            aspec, ts_py, col, t_within, span_idx, rng
        )
        inst_mask = resolved_filters.get(id(aspec))
        if inst_mask is None:
            # No filter, or filter matches every instance — write to the
            # shared ``values`` (Phase 2 fast path) AND propagate the
            # write to every already-forked per-instance buffer. The
            # propagation is for *different* rows than the row that
            # forked the buffer: e.g. a filtered spec at t=60 forks
            # pod-0's buffer; a later unfiltered spec at t=120 must
            # apply to pod-0 too, not stay stuck on its forked baseline
            # from t=60. Same-cell collisions CAN occur here — the
            # duplicate-spec guard above only rejects *identical*
            # ``(metric, time_offset)`` pairs, while two specs with
            # different offsets can round to the same row at a coarse
            # ``--interval-seconds`` (or a cascade can land inside a
            # shaped span). Colliding specs resolve last-writer-wins per
            # buffer in ``(row_idx, metric, spec_order)`` order — the
            # documented contract in CLAUDE.md's RNG-ordering section.
            values[row_idx, col] = override_value
            for buf in per_instance_values.values():
                buf[row_idx, col] = override_value
        else:
            # Partial filter — only matched instances see the override.
            # Unmatched instances continue to read ``values`` (or their
            # own forked buffer if a prior spec already diverged them).
            for inst_idx in np.flatnonzero(inst_mask):
                inst_idx = int(inst_idx)
                buf = per_instance_values.get(inst_idx)
                if buf is None:
                    # Snapshot shared values (including any unfiltered
                    # writes applied so far this loop) before diverging.
                    buf = values.copy()
                    per_instance_values[inst_idx] = buf
                buf[row_idx, col] = override_value
        if id(aspec) not in manifest_emitted:
            manifest_emitted.add(id(aspec))
            # timestamp / span_start equal the spec's first *kept* row —
            # historically always the span's first row, but under
            # ``--drop-rate`` the leading row(s) of a shaped span can be
            # dropped and the entry must anchor at the first row that
            # actually appears in the component CSV. span_end equals
            # timestamp for single-row specs and the formatted
            # end-of-span timestamp for shaped specs with
            # ``duration_seconds``. The end row is the last row index
            # covered by the span — computed from the spec's *nominal*
            # start row ``row_idx - span_idx``, clipped to ``n_rows - 1``
            # so specs whose tail spills past the run window still produce
            # a valid in-range end timestamp — then walked back to the last
            # non-dropped row in the span so span_end always names a
            # timestamp that actually appears in the component CSV.
            # ``row_idx`` itself is non-dropped (checked above), so the
            # slice is guaranteed to contain at least one kept row.
            duration_seconds = float(aspec.get("duration_seconds", 0) or 0)
            duration_rows = max(1, int(np.ceil(duration_seconds / interval)))
            start_idx_nominal = row_idx - span_idx
            end_idx_nominal = min(start_idx_nominal + duration_rows - 1, n_rows - 1)
            span_kept = ~drop_mask[row_idx:end_idx_nominal + 1]
            end_idx = row_idx + int(np.flatnonzero(span_kept)[-1])
            ts_str = str(ts_strings[row_idx])
            ctx.anomalies.append({
                "timestamp": ts_str,
                "component": component_name,
                "metric": aspec["metric"],
                "description": aspec["description"],
                "scenario_id": aspec.get("_scenario_id", ""),
                "severity": aspec.get("_severity", ""),
                "is_cascade": "true" if aspec.get("_is_cascade") else "false",
                "span_start": ts_str,
                "span_end": str(ts_strings[end_idx]),
                "shape": aspec.get("shape", "step"),
            })

    # Phase 6 integer-cast bundle. Every MetricSpec declared with
    # ``dtype="int"`` must render as a whole-integer CSV cell so the
    # validator's ``_validate_component_cells`` ``dtype="int"``
    # check passes. The cast runs *before* derivations so derived columns
    # (e.g. ``cacheservice.hit_ratio``) are recomputed from the rounded
    # integer source cells and stay self-consistent with what the CSV
    # actually writes — otherwise the validator's
    # ``_validate_component_derivations`` recompute step would flag the
    # derived cell as drifting from the recomputed value. ``np.rint``
    # rounds half-to-even into floats, which is consistent with
    # ``_format_fixed3`` printing "1235.000" for an underlying float of
    # ``1235.0``. ``apply_dtype_int_cast=False`` skips the cast for
    # programmatic callers that need the pre-cast fractional contrast;
    # main() always passes ``True`` (the phase-9 flag day removed the
    # ``--topology-mode independent`` alias that used to skip it).
    if apply_dtype_int_cast:
        for col_idx, spec in enumerate(specs):
            if spec.dtype == "int":
                np.rint(values[:, col_idx], out=values[:, col_idx])
                for buf in per_instance_values.values():
                    np.rint(buf[:, col_idx], out=buf[:, col_idx])

    # Derived metrics: rebuild self-consistent relationships after natural and
    # anomaly values have settled (and after the integer-cast bundle above
    # so derivations consume the same values the CSV emits). The registered
    # function recomputes the derived column(s) from their sibling columns;
    # without this pass, anomalies that drove only a source column (or that
    # overrode a derived column in isolation) would leave the columns
    # internally inconsistent — exactly the consistency anomaly real
    # telemetry would flag.
    derivation = DERIVATIONS.get(component_name)
    if derivation is not None:
        derive_fn, _ = derivation
        derive_fn(values, name_to_col)
        # Phase 4: per-instance buffers diverged in source columns, so
        # rebuild their derived columns independently from the shared run.
        for buf in per_instance_values.values():
            derive_fn(buf, name_to_col)

    # Topology phase 2/3: expose post-natural /
    # post-anomaly / post-derivation load-metric columns to downstream
    # components via the ``topology_capture`` dict. Phase 3 extends the
    # capture from a single ``requests_per_sec`` column to all metrics
    # listed in ``_TOPOLOGY_LOAD_METRICS[component_name]`` (the canonical
    # load metric plus any supplementary columns) so per-edge ``signal``
    # callables (e.g. the cacheservice -> database miss-ratio derivation)
    # can read the full upstream state. Capturing pre-round (before the
    # ``np.round(values, 3, ...)`` below) keeps the signal at full
    # 3+-decimal float precision *for ``dtype="float"`` columns*. After
    # the phase 6 integer-cast bundle, ``dtype="int"`` upstream
    # load metrics (notably ``cache_hits`` / ``cache_misses`` driving the
    # cacheservice -> database miss-ratio signal) are captured at their
    # post-cast whole-integer values, which matches what the CSV emits
    # and what the validator's derivation recompute reads — the
    # downstream coupling signal therefore stays self-consistent with
    # the on-disk row. ``None`` short-circuits so direct callers that
    # skip topology capture (e.g. the natural-baseline test fixtures)
    # see zero topology work.
    if topology_capture is not None:
        entry = _TOPOLOGY_LOAD_METRICS.get(component_name)
        if entry is not None:
            canonical_up, supplementary_up = entry
            load_metrics = (canonical_up, *supplementary_up)
            captured: dict[str, np.ndarray] = {}
            # When per-instance buffers diverged from the shared
            # ``values`` (an ``instance_filter`` partial override on a
            # load metric, or per-instance topology produced divergent
            # baselines), an instance-0-only capture biases every
            # downstream consumer of the aggregate ``topology_capture``
            # view. Mirror the documented "uniform fan-out — mean
            # across upstream pods" contract by averaging across all
            # instance buffers (instance 0 is ``values``; the rest
            # live in ``per_instance_values``). When no buffer
            # diverged, the average equals ``values`` exactly and
            # the captured bytes are unchanged.
            inst_count = len(instances)
            may_diverge = bool(per_instance_values) and inst_count > 1
            for lm in load_metrics:
                if lm and lm in name_to_col:
                    col_idx = name_to_col[lm]
                    shared_col = values[:, col_idx]
                    captured_col: np.ndarray | None = None
                    if may_diverge:
                        # ``per_instance_values`` may be populated by a
                        # filter on a *non-load* metric, in which case
                        # the load column is byte-identical across all
                        # instances and the historic ``shared_col.copy()``
                        # capture stays byte-exact. Only switch to the
                        # equal-weight mean when at least one
                        # per-instance buffer actually diverged on
                        # this load column.
                        any_diverged = False
                        for buf in per_instance_values.values():
                            if not np.array_equal(
                                buf[:, col_idx], shared_col
                            ):
                                any_diverged = True
                                break
                        if any_diverged:
                            # Incremental sum-then-divide avoids the
                            # ``(N_instances × n_rows)`` temporary
                            # ``np.stack`` would allocate. Same
                            # equal-weight mean as ``np.mean`` over the
                            # stacked array but at O(n_rows) extra
                            # memory instead of O(N_instances × n_rows).
                            #
                            # Use ``per_instance_values.get(k, values)``
                            # for *every* k including 0 so an
                            # ``instance_filter`` that targets pod 0
                            # (forking only ``per_instance_values[0]``
                            # while other pods stay on the shared
                            # ``values`` baseline) still contributes
                            # pod-0's forked buffer to the aggregate.
                            # ``shared_col`` is ``values[:, col_idx]``
                            # and would silently skip pod 0's diverged
                            # buffer if used as the initial accumulator.
                            buf0 = per_instance_values.get(0, values)
                            captured_col = buf0[:, col_idx].astype(
                                np.float64, copy=True
                            )
                            for inst_idx_k in range(1, inst_count):
                                buf = per_instance_values.get(
                                    inst_idx_k, values
                                )
                                captured_col += buf[:, col_idx]
                            captured_col /= inst_count
                    if captured_col is None:
                        captured_col = shared_col.copy()
                    captured[lm] = captured_col
            if captured:
                topology_capture[component_name] = captured

    # phase 8: per-instance load-metric capture for downstream
    # consumers. Mirrors ``topology_capture`` above but produces one
    # entry per instance. Under symmetric upstream (no
    # ``instance_filter`` on load metrics) every entry references a
    # copy of the same underlying column, so downstream composers
    # collapse back to the shared-arrays fast path and N=3 byte
    # parity holds.
    if topology_capture_by_instance is not None:
        entry = _TOPOLOGY_LOAD_METRICS.get(component_name)
        if entry is not None:
            canonical_up, supplementary_up = entry
            load_metrics = (canonical_up, *supplementary_up)
            per_inst_caps: list[dict[str, np.ndarray]] = []
            for inst_idx_k in range(len(instances)):
                buf = per_instance_values.get(inst_idx_k, values)
                inst_captured: dict[str, np.ndarray] = {}
                for lm in load_metrics:
                    if lm and lm in name_to_col:
                        inst_captured[lm] = buf[:, name_to_col[lm]].copy()
                per_inst_caps.append(inst_captured)
            # Only record when at least one entry has captures; aligns
            # with the shared ``topology_capture`` guard above.
            if any(per_inst_caps):
                topology_capture_by_instance[component_name] = per_inst_caps

    # Multi-instance fan-out (Phase 2/4). When the active instance list
    # is a single anonymous Instance() (all fields None), emit today's
    # byte-identical format: ``timestamp,m0,m1,...``. When the list carries
    # named instances (len > 1, or any non-None dimension field), prepend
    # ``id,host,pod,az,region,tenant`` columns and repeat the row block for
    # each instance sequentially (all rows for instance 0, then instance 1,
    # …). ``_is_anonymous`` was already computed above for the DST
    # defense-in-depth guard via the shared ``_is_anonymous_instance_list``
    # helper.
    #
    # Phase 4 (instance_filter): instances touched by a partial filter use
    # their own ``per_instance_str_vals`` buffer (post-override,
    # post-derive); other instances reuse the shared ``str_vals`` so the
    # all-instances-unfiltered case stays byte-identical to Phase 2.
    #
    # both branches now flow through the shared ``_format_metric_suffix``
    # / ``_format_csv_row_block`` helpers. The dimensionless branch is the
    # degenerate ``dim_prefix=""`` case of the long-form path, so the DST
    # splice — applied inside ``_format_csv_row_block`` — fires regardless
    # of the writer branch. Before the refactor the long-form path
    # rebuilt rows from pre-splice timestamps and silently dropped the
    # duplicate hour (the PR #63 long-form DST drop).

    if emit_metrics:
        # Rounding and fixed-3 string formatting exist only to produce
        # the CSV bytes, so they run inside the emit guard: a run whose
        # ``--emit`` omits ``metrics`` previously paid the
        # full formatting cost (historically ~80% of generation
        # runtime, per the comment below) and threw the result away.
        # Safe to skip when not emitting: the ``topology_capture``
        # snapshots above were taken pre-round by design, and nothing
        # after this block reads ``values`` / the per-instance buffers.
        # No RNG is consumed here, so draw order — and therefore every
        # locked hash — is unchanged for runs that do emit metrics.
        np.round(values, 3, out=values)
        for buf in per_instance_values.values():
            np.round(buf, 3, out=buf)

        keep_mask = ~drop_mask
        kept_ts = ts_strings[keep_mask]
        kept_vals = values[keep_mask]

        # Format values to fixed 3 decimals. ``np.char.mod("%.3f", ...)`` is correct
        # but spends ~80% of the run inside ``_vec_string``. Scaling to int + numpy
        # string ops produces the same output ~2x faster.
        str_vals = _format_fixed3(kept_vals)
        # Phase 4: per-instance string buffers for instances that diverged from
        # the shared baseline via a partial ``instance_filter``. Other instances
        # reuse ``str_vals`` directly.
        per_instance_str_vals: dict[int, np.ndarray] = {
            inst_idx: _format_fixed3(buf[keep_mask])
            for inst_idx, buf in per_instance_values.items()
        }

        with open(file_path, "w", encoding="utf-8", newline="") as f:
            # Precompute the shared metric suffix once per component. Every
            # instance not in ``per_instance_str_vals`` reuses this array,
            # preserving Phase 2's "precompute once, reuse per instance"
            # optimization. The anonymous branch is a single-instance
            # degenerate case so reuse is a no-op there.
            shared_metric_suffix = _format_metric_suffix(str_vals)
            if _is_anonymous:
                # Dimensionless default — byte-identical to pre-Phase-2 output.
                f.write("timestamp," + ",".join(fieldnames) + "\n")
                rows = _format_csv_row_block(
                    kept_ts, shared_metric_suffix,
                    dim_prefix="", dst_inject_day=dst_inject_day,
                )
                f.write("\n".join(rows.tolist()))
                f.write("\n")
            else:
                # Long form: timestamp,id,host,pod,az,region,tenant,<metrics>
                # ``_INSTANCE_DIMENSION_COLUMNS`` is the single source of
                # truth for the column order; ``_iter_component_rows`` lifts
                # the same prefix back into the per-row dimensions dict
                # consumed by the OTEL gauge attributes path (Phase 6).
                dim_header = "timestamp," + ",".join(_INSTANCE_DIMENSION_COLUMNS)
                f.write(dim_header + "," + ",".join(fieldnames) + "\n")
                # Materialize per-instance suffixes for forked buffers only;
                # other instances reuse ``shared_metric_suffix`` so the
                # all-instances-unfiltered case stays byte-identical to
                # Phase 2.
                per_instance_metric_suffixes: dict[int, np.ndarray] = {
                    inst_idx: _format_metric_suffix(buf)
                    for inst_idx, buf in per_instance_str_vals.items()
                }
                for inst_idx, inst in enumerate(instances):
                    # Build the dimension prefix string once per instance.
                    # Reads fields off ``Instance`` in canonical column order
                    # so adding/removing a field touches only
                    # ``_INSTANCE_DIMENSION_COLUMNS``. The leading comma
                    # lets ``_format_csv_row_block`` concatenate as
                    # ``ts + dim_prefix + "," + metric_suffix`` regardless
                    # of branch.
                    dim_vals = ",".join(
                        getattr(inst, field) if getattr(inst, field) is not None else ""
                        for field in _INSTANCE_DIMENSION_COLUMNS
                    )
                    inst_suffix = per_instance_metric_suffixes.get(
                        inst_idx, shared_metric_suffix
                    )
                    inst_rows = _format_csv_row_block(
                        kept_ts, inst_suffix,
                        dim_prefix=f",{dim_vals}",
                        dst_inject_day=dst_inject_day,
                    )
                    f.write("\n".join(inst_rows.tolist()))
                    f.write("\n")


def _format_metric_suffix(str_vals: np.ndarray) -> np.ndarray:
    """Return ``,``-joined metric values as a 1-D string array.

    ``str_vals`` is the post-format ``(n_rows, n_cols)`` array produced by
    ``_format_fixed3``; the returned array carries one ``v0,v1,...,vk``
    string per row, with no leading comma. Callers prepend the
    ``timestamp,<optional_dims>,`` head via ``_format_csv_row_block``.

    Aliasing safety: when ``n_cols >= 2`` the first ``np.char.add`` call
    returns a fresh array, so we can start from a view of column 0 and
    rely on the loop to allocate. When ``n_cols == 1`` the loop never
    runs, so we must explicitly copy column 0 to avoid the caller's
    downstream ``np.char.add`` mutating the source array.
    """
    n_cols = str_vals.shape[1]
    if n_cols == 1:
        return str_vals[:, 0].copy()
    suffix = str_vals[:, 0]
    for col in range(1, n_cols):
        suffix = np.char.add(suffix, ",")
        suffix = np.char.add(suffix, str_vals[:, col])
    return suffix


def _format_csv_row_block(kept_ts: np.ndarray, metric_suffix: np.ndarray,
                          *, dim_prefix: str, dst_inject_day: int) -> np.ndarray:
    """Concatenate ``timestamp + dim_prefix + ',' + metric_suffix`` per row.

    ``dim_prefix`` is the empty string for the dimensionless / single-anonymous-
    instance CSV layout, or one instance's comma-prefixed dimension
    *values* (e.g. ``",i0,,pod-0,,,"`` — leading comma, then the six
    ``_INSTANCE_DIMENSION_COLUMNS`` values with empty cells for unset
    fields) for one long-form instance block. The shared shape lets
    the same DST splice apply regardless of which branch produced the
    block — fixing a prior failure of the long-form writer to call
    ``_splice_dst_artifact`` after rebuilding rows from raw timestamps. The helper itself does not gate which combinations
    are reachable: ``_splice_dst_artifact`` runs unconditionally when
    ``dst_inject_day > 0`` regardless of ``dim_prefix``. ``parse_args``
    and the matching ``generate_component`` defense-in-depth check
    still reject ``--inject-dst-artifact-day > 0`` paired with a
    multi-instance run by design (per-instance non-monotonic timestamps
    break downstream ``heapq.merge`` in ``gauges.csv`` /
    ``combined_metrics_unified.csv``), so today every production caller
    that reaches the long-form branch has ``dst_inject_day == 0``; the
    helper would handle the long-form splice correctly under any
    future caller that relaxes the guard.
    """
    rows = np.char.add(kept_ts, f"{dim_prefix},")
    rows = np.char.add(rows, metric_suffix)
    if dst_inject_day > 0:
        rows = _splice_dst_artifact(rows, kept_ts, dst_inject_day)
    return rows


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
                           t_within: float, span_idx: int,
                           rng: "np.random.RandomState" = None) -> float:
    """Resolve one anomaly value at one row, honoring shape/duration fields."""
    duration_seconds = float(spec.get("duration_seconds", 0) or 0)
    shape = spec.get("shape", "step")
    shape_params = spec.get("shape_params", {}) or {}

    if duration_seconds <= 0 and shape == "step":
        # Dispatch by REQUIRED positional count, not by maximum callability.
        # A generator like (ts, col, scale=1.0) accepts a 3-arg call at the
        # Python language level, but the author marked the 3rd positional
        # as optional with a non-rng name — calling 3-arg would silently
        # bind the RNG object to ``scale``. Required-based dispatch keeps
        # the default and avoids the misbind. Only generators that
        # explicitly opt into the RNG (required=3 or *args) receive it.
        meta = _cached_generator_meta(spec["generator"])
        if not meta["inspectable"]:
            # Conservative fallback: try only the two canonical shapes
            # (3-arg first, then 2-arg). No intermediate calls. Retry
            # only on a call-*binding* TypeError (arity mismatch raised
            # at the call site: the traceback has no frame beyond this
            # one). A TypeError raised *inside* the generator body has a
            # deeper traceback; retrying it with 2 args would mask the
            # real bug and — if the body drew from ``rng`` before
            # raising — double-advance the RNG stream. (A C-extension
            # body raising TypeError without Python frames is
            # indistinguishable from a binding failure and still
            # retries; that is the best the fallback can do.)
            try:
                value = spec["generator"](ts, col, rng)
            except TypeError as exc:
                if exc.__traceback__.tb_next is not None:
                    raise
                value = spec["generator"](ts, col)
            return float(value)
        required = meta["required_positional"]
        fixed = meta["fixed_positional_count"]
        if meta["has_var_positional"]:
            # Mirror the validator's *args misbind check so direct callers
            # (e.g., tests bypassing _validate_scenario_spec) cannot silently
            # bind the RNG to a default-having fixed positional like
            # ``scale`` in ``(ts, col, scale=1.0, *args)``.
            if required <= 2 and fixed > 2:
                # Step path calls 3-arg, so the only position the dispatcher
                # could misbind onto is fixed position 3. Positions 4+ are
                # left at their declared defaults (not bound by the 3-arg
                # call), so name the actual offender — position 3 — rather
                # than the count of fixed params.
                raise TypeError(
                    f"Generator {spec['generator']!r} has *args with "
                    f"fixed_positional_count={fixed} > 2 and required <= 2; "
                    f"the 3-arg step call would overwrite the default-having "
                    f"fixed positional at position 3. Use (ts, col) or "
                    f"(ts, col, rng) instead."
                )
            return float(spec["generator"](ts, col, rng))
        if required == 3:
            return float(spec["generator"](ts, col, rng))
        if required <= 2:
            return float(spec["generator"](ts, col))
        raise TypeError(
            f"Generator {spec['generator']!r} requires {required} positional "
            f"args; step-path specs must use a 2-arg or 3-arg required shape."
        )

    if shape in ("step", "sustained"):
        return float(_call_generator_within_span(spec["generator"], ts, col, t_within, span_idx, rng))

    start = shape_params.get("start")
    if start is None:
        start = _call_generator_within_span(spec["generator"], ts, col, 0.0, 0, rng)
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


class _IdentityKey:
    """Dict key with identity-based equality, used by the generator-meta
    cache. Two distinct callables that compare equal via custom ``__eq__``
    must not share cached metadata; keying by identity avoids that.
    Storing the object inside the key also keeps a strong reference,
    so Python can't recycle ``id(obj)`` for a different generator after
    garbage collection."""
    __slots__ = ("obj",)

    def __init__(self, obj):
        self.obj = obj

    def __hash__(self):
        return id(self.obj)

    def __eq__(self, other):
        return isinstance(other, _IdentityKey) and self.obj is other.obj


_GENERATOR_META_CACHE: "dict[_IdentityKey, dict]" = {}
_GENERATOR_META_CACHE_MAX = 1024


def _generator_meta(gen) -> dict:
    """Return introspection metadata for a generator callable.

    Tracking *required* and *maximum* positional separately matters because
    a generator like ``(ts, col, rng=None, extra=None)`` has 2 required +
    2 optional positional params (4 max), so the runtime can call it with
    2, 3, or 4 positional args. The validator and dispatcher both consult
    this metadata to pick a safe call shape.

    Keys returned:
    - ``required_positional``: count of positional-only or
      positional-or-keyword params with no default. The minimum positional
      arity the callable accepts.
    - ``fixed_positional_count``: count of positional-only or
      positional-or-keyword params total (with or without defaults).
      Preserved even when ``*args`` is present, because a fixed-positional
      prefix BEFORE ``*args`` still receives the first N positional args
      of a call before the rest flow into ``*args``.
    - ``max_positional``: total positional capacity. Equals
      ``fixed_positional_count`` when ``*args`` is absent; ``None`` when
      ``*args`` is present (unbounded).
    - ``has_var_positional``: True iff ``*args`` is in the signature. The
      validator and both dispatchers consult this flag to decide whether
      to call the canonical target-arity shape.
    - ``has_required_kwargs``: True iff any ``KEYWORD_ONLY`` param has no
      default. Such generators cannot be called positionally by our runtime.
    - ``inspectable``: True iff ``inspect.signature()`` succeeded. When
      False, callers must fall back to a try/except call chain.
    """
    try:
        sig = inspect.signature(gen)
    except (TypeError, ValueError):
        return {"required_positional": 0,
                "fixed_positional_count": 0,
                "max_positional": None,
                "has_var_positional": False,
                "has_required_kwargs": False,
                "inspectable": False}
    required = 0
    fixed = 0
    has_var_positional = False
    has_required_kw = False
    for p in sig.parameters.values():
        if p.kind is inspect.Parameter.VAR_POSITIONAL:
            has_var_positional = True
        elif p.kind in (inspect.Parameter.POSITIONAL_ONLY,
                        inspect.Parameter.POSITIONAL_OR_KEYWORD):
            fixed += 1
            if p.default is inspect.Parameter.empty:
                required += 1
        elif p.kind is inspect.Parameter.KEYWORD_ONLY:
            if p.default is inspect.Parameter.empty:
                has_required_kw = True
    return {"required_positional": required,
            "fixed_positional_count": fixed,
            "max_positional": None if has_var_positional else fixed,
            "has_var_positional": has_var_positional,
            "has_required_kwargs": has_required_kw,
            "inspectable": True}


def _cached_generator_meta(gen) -> dict:
    """Cached introspection lookup keyed by callable identity.

    - Identity keying (not object equality) prevents two distinct
      callables that compare equal via custom ``__eq__`` from sharing
      stale metadata.
    - The ``_IdentityKey`` wrapper holds a strong reference, so Python
      can't recycle ``id(gen)`` for a different callable after garbage
      collection.
    - Bounded size with simple insertion-order eviction keeps the cache
      from growing without bound in long-lived processes that create
      many fresh callables (e.g., test sessions building lambdas in
      loops). Dropped wrappers release their callables for gc.
    """
    key = _IdentityKey(gen)
    cached = _GENERATOR_META_CACHE.get(key)
    if cached is not None:
        return cached
    meta = _generator_meta(gen)
    if len(_GENERATOR_META_CACHE) >= _GENERATOR_META_CACHE_MAX:
        for stale in list(_GENERATOR_META_CACHE)[: _GENERATOR_META_CACHE_MAX // 2]:
            del _GENERATOR_META_CACHE[stale]
    _GENERATOR_META_CACHE[key] = meta
    return meta


def _call_generator_within_span(generator: Callable, ts: datetime.datetime, col: int,
                                t_within: float, span_idx: int,
                                rng: "np.random.RandomState" = None):
    """Call a span-path generator with either the 5-arg or 2-arg shape.

    Dispatch by REQUIRED positional count, not by maximum callability. A
    generator like ``(ts, col, scale=1.0, factor=2.0, baseline=0.0)`` is
    callable with 5 args at the Python language level, but the author named
    the 3rd–5th positions for their own values, not for runtime internals.
    Calling 5-arg would silently bind ``t_within``/``span_idx``/``rng`` to
    those parameters. Required-based dispatch instead calls 2-arg, keeps
    the defaults, and avoids the misbind. Only generators that explicitly
    opt into the runtime internals (``required=5`` or ``*args``) receive
    the 5-arg call.

    Uninspectable callables (e.g., C extensions) fall back to a try/except
    chain that tries only the two canonical shapes (5-arg then 2-arg) — no
    intermediate 3- or 4-arg attempts, because those would themselves be
    misbinding vectors. The fallback retries only on a call-*binding*
    TypeError; a TypeError raised inside the generator body propagates
    (see the step-path fallback in ``_resolve_anomaly_value``).
    """
    meta = _cached_generator_meta(generator)
    if not meta["inspectable"]:
        # See the matching step-path fallback in ``_resolve_anomaly_value``
        # for the binding-vs-body TypeError distinction.
        try:
            return generator(ts, col, t_within, span_idx, rng)
        except TypeError as exc:
            if exc.__traceback__.tb_next is not None:
                raise
            return generator(ts, col)
    required = meta["required_positional"]
    fixed = meta["fixed_positional_count"]
    if meta["has_var_positional"]:
        # Mirror the validator's *args misbind checks for direct callers.
        # Two distinct misbind cases:
        #   (a) required <= 2 with default-having fixed positions beyond
        #       (ts, col): the 5-arg call overwrites declared defaults at
        #       positions 3 through min(fixed, 5).
        #   (b) required ∈ {3, 4}: the 5-arg call binds t_within (and
        #       possibly span_idx) into REQUIRED positional slots the
        #       author intended for other values (e.g. (ts, col, rng,
        #       *args) where rng would receive t_within).
        if required <= 2 and fixed > 2:
            misbind_end = min(fixed, 5)
            misbind_range = (
                f"position 3" if misbind_end == 3
                else f"positions 3 through {misbind_end}"
            )
            raise TypeError(
                f"Generator {generator!r} has *args with "
                f"fixed_positional_count={fixed} > 2 and required <= 2; "
                f"the 5-arg span call would overwrite the default-having "
                f"fixed positional at {misbind_range}. Use (ts, col) or "
                f"(ts, col, *args) instead."
            )
        if required > 2 and required != 5:
            raise TypeError(
                f"Generator {generator!r} has *args with "
                f"required_positional={required} (neither 2 nor 5); "
                f"the 5-arg span call would bind t_within/span_idx into "
                f"the required positions the author intended for other "
                f"values. Use (ts, col, t_within, span_idx, rng) for full "
                f"control or (ts, col) for the legacy form."
            )
        return generator(ts, col, t_within, span_idx, rng)
    if required == 5:
        return generator(ts, col, t_within, span_idx, rng)
    if required <= 2:
        return generator(ts, col)
    raise TypeError(
        f"Generator {generator!r} requires {required} positional args; "
        f"span-path specs must use a 2-arg or 5-arg required shape. "
        f"_validate_scenario_spec should have rejected this at import time."
    )


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
                     severity=DEFAULT_SEVERITY, *, cascade_registry: dict | None = None):
    """
    Register a cascading anomaly that will affect another component.

    ``severity`` controls --signal-level eligibility. Defaults to ``medium`` so
    today's cascades fire at the default level; pass ``"high"`` for cascades
    that only belong to the high-pressure catalog.

    Production code routes every cascade through ``_apply_scenarios()`` and
    does not call this helper; it is retained as a small-surface entry point
    for tests that want to inject a cascade without standing up a full
    ``Scenario``. The provenance fields (``_is_cascade``, ``_severity``,
    ``_scenario_id``) are stamped here so a helper-registered cascade still
    emits a manifest row with ``is_cascade=true`` and the correct severity,
    matching what ``_apply_scenarios()`` would have produced.

    ``cascade_registry`` must be provided explicitly (pass
    ``RunContext.cascading_anomalies`` or an empty dict for tests).
    """
    if cascade_registry is None:
        raise TypeError(
            "register_cascade() requires a cascade_registry= keyword argument; "
            "pass the RunContext.cascading_anomalies dict or an empty dict for tests."
        )
    cascade_registry.setdefault(target_component, []).append({
        "time_offset": time_offset,
        "metric": metric,
        "description": description,
        "generator": generator,
        "severity": severity,
        "_is_cascade": True,
        "_severity": severity,
        "_scenario_id": "",
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


def _validate_metric_spec_schema_metadata() -> None:
    """Import-time invariants for the schema metadata fields on ``MetricSpec``.

    Rejects nonsense vocabulary (unknown ``semantic_type`` / ``dtype``) and
    obvious shape errors (``min_value`` > ``max_value``, non-finite bounds)
    before ``main()`` runs, so ``write_schema_json`` and the validator can
    rely on the declared metadata being consistent. Backfill is incremental:
    a spec with all schema fields left at their defaults is still valid
    (semantic_type is None, dtype defaults to ``float``, bounds default to
    None). Once a field is populated, it must be sensible.
    """
    for component, specs in COMPONENTS.items():
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


# ------------------------------------------------------------------
# Topology graph (phase 1 — structural-only).
# ------------------------------------------------------------------
# Directed service-call graph. ``TOPOLOGY[source]`` lists the ``Edge``
# instances downstream of ``source``; both source keys and ``Edge.target``
# values are component names from ``COMPONENTS``. Under the default
# ``--topology-mode realistic`` (phase 6 flag day) the graph
# is consumed by ``_compose_topology_coupled_specs`` (phase 2/3:
# rewrites downstream load-metric baselines from upstream RPS/token
# columns) and ``_compose_topology_saturation_specs`` (phase 4/5:
# lifts downstream latency/error specs via the logistic saturation
# curve). The graph is always read: the phase-9 flag day removed the
# ``--topology-mode independent`` no-topology contrast alias.
#
# v1 graph (per design):
#   loadbalancer -> apigateway                   (constant weight 1.0)
#   apigateway   -> authservice (0.3),           (request fan-out shares;
#                   cacheservice (0.4),           the weights here sum to 1
#                   database (0.3)                so the phase-2 two-pass
#                                                 generation can treat them
#                                                 as routing fractions)
#   cacheservice -> database                     (weight = callable on
#                                                 cache_miss / total rate)
#   apigateway   -> llm_analytics                (phase 5 token-
#                                                 throttle: positive
#                                                 weight couples
#                                                 input_tokens_per_sec to
#                                                 apigateway RPS; non-
#                                                 zero gains lift LLM
#                                                 latency / error as
#                                                 apigateway saturates)
#
# Cascade-vs-topology overlap: several SCENARIOS already encode pairwise
# blast-radius (e.g. auth -> gateway, cache -> DB) via cascade_specs. The
# topology graph is a structural orthogonal view — it describes *normal*
# request flow, not anomaly propagation — so the two are intentionally
# allowed to overlap. The realistic-mode pipeline applies topology
# coupling and saturation to the natural baseline before the per-row
# anomaly override loop runs, so a cascade write at row i still wins at
# exactly that row regardless of the topology-derived baseline.
def _component_metric_base(component: str, metric: str) -> float:
    """Look up the natural ``MetricSpec.base`` for ``component[metric]``.

    Returns ``0.0`` when the metric is not in the component's catalog so
    callers can branch on the falsy value without raising. Coupling uses
    the natural baseline to map upstream load (in upstream units) to the
    downstream metric's scale (e.g. apigateway's ~800 rps to database's
    ~25k qps). Defined above ``TOPOLOGY`` so the cacheservice → database
    callable lambda can reference it at the import-time smoke test in
    ``_validate_topology``.
    """
    for spec in COMPONENTS.get(component, ()):
        if spec.name == metric:
            return float(spec.base)
    return 0.0


# Phase 3: per-component "load metrics" the topology coupling
# operates on. Each entry maps a component to a
# ``(canonical, supplementary)`` tuple where:
#
# * ``canonical`` is the single MetricSpec.name a constant-weight edge
#   from this component reads to produce its contribution. Required;
#   must be a captured MetricSpec on the component.
# * ``supplementary`` is the (possibly empty) tuple of additional
#   MetricSpec.name values captured alongside the canonical metric so
#   ``Edge.signal`` callables on outgoing edges can derive a per-row
#   scalar from multiple columns (e.g. cacheservice exposes both
#   ``cache_hits`` and ``cache_misses`` so the cache→database miss-ratio
#   signal can compute ``misses / (hits + misses)``).
#
# Components with a single load metric have ``supplementary = ()``.
# Constant-weight edges always read ``canonical``; the capture loop
# captures ``(canonical, *supplementary)`` into ``topology_capture``;
# ``_compose_topology_coupled_specs`` rewrites both canonical and
# supplementary metrics on downstream components that have incoming
# edges. Declared above ``TOPOLOGY`` so ``_validate_topology()`` (which
# runs at import time) can build a captured-column probe for callable-
# weight edges' ``signal`` callables.
_TOPOLOGY_LOAD_METRICS: dict[str, tuple[str, tuple[str, ...]]] = {
    "loadbalancer": ("requests_per_sec", ()),
    "apigateway": ("requests_per_sec", ()),
    "authservice": ("login_attempts", ()),
    "cacheservice": ("cache_hits", ("cache_misses",)),
    "database": ("queries_per_sec", ()),
    # phase 5: llm_analytics couples its token throughput to
    # apigateway under realistic mode. ``input_tokens_per_sec`` is the
    # canonical "load" metric here because the token budget governs
    # tokens/second (not requests/second) — pinning the load metric to
    # tokens also gives the coupling enough signal-to-noise to clear
    # the >= 0.85 Pearson correlation gate, given the noise floor at
    # ``_TOPOLOGY_COUPLE_NOISE_STD`` is fixed in absolute units.
    # No downstream consumes llm_analytics in the v1 graph, so there
    # are no supplementary columns.
    "llm_analytics": ("input_tokens_per_sec", ()),
}


def _cache_miss_ratio_signal(
    cols: dict[str, np.ndarray],
) -> "np.ndarray | None":
    """Per-edge ``Edge.signal`` for the ``cacheservice -> database`` edge.

    Receives ``cacheservice``'s captured load columns and returns the
    per-row cache-miss ratio ``cache_misses / (cache_hits + cache_misses)``
    (0.0 where the combined total is non-positive). Returns ``None`` when
    either required column is missing — the composer treats this as
    "skip this edge" so a ``--metrics-per-component`` selection that
    trims a required column degrades gracefully instead of raising.
    """
    hits = cols.get("cache_hits")
    misses = cols.get("cache_misses")
    if hits is None or misses is None:
        return None
    total = hits + misses
    return np.divide(
        misses, total,
        out=np.zeros_like(misses, dtype=np.float64),
        where=total > 0,
    )


TOPOLOGY: dict[str, list[Edge]] = {
    "loadbalancer": [
        # phase 4: saturation feedback. ``midpoint`` is the
        # upstream's load value at which the logistic curve sits at 0.5
        # (~80% of the natural peak of ~1080 rps for loadbalancer). The
        # gains shape latency and error responses as the gateway nears
        # capacity. See ``_apply_saturation`` for the exact formula and
        # ``_TOPOLOGY_SATURATION_TARGETS`` for the affected downstream
        # latency/error columns.
        Edge(
            target="apigateway", weight=1.0,
            saturation=SaturationParams(
                midpoint=860.0, steepness=6.0,
                latency_gain=0.4, error_gain=0.010,
            ),
        ),
    ],
    "apigateway": [
        # phase 4: saturation feedback on the three fan-out
        # downstreams. ``midpoint`` is ~80% of the apigateway natural
        # peak (~950 rps). ``latency_gain`` scales with each downstream's
        # sensitivity to upstream load: database is most sensitive
        # (heavy I/O), authservice next (per-request crypto work),
        # cacheservice least (in-memory ops). ``error_gain`` follows the
        # same ordering, kept inside the issue's [0.005, 0.02] band.
        Edge(
            target="authservice", weight=0.3,
            saturation=SaturationParams(
                midpoint=760.0, steepness=6.0,
                latency_gain=0.5, error_gain=0.012,
            ),
        ),
        Edge(
            target="cacheservice", weight=0.4,
            saturation=SaturationParams(
                midpoint=760.0, steepness=6.0,
                latency_gain=0.3, error_gain=0.008,
            ),
        ),
        Edge(
            target="database", weight=0.3,
            saturation=SaturationParams(
                midpoint=760.0, steepness=6.0,
                latency_gain=0.6, error_gain=0.015,
            ),
        ),
        # phase 5: LLM token-throttle. Apigateway serves as the
        # token-budget metering authority for LLM-bound traffic, so this
        # edge couples ``llm_analytics.input_tokens_per_sec`` to
        # ``apigateway.requests_per_sec`` (the renormalization in
        # ``_compose_topology_coupled_specs`` reproduces the natural
        # LLM baseline at natural apigateway load regardless of the
        # raw weight magnitude — any positive weight makes the edge
        # active). ``midpoint`` is expressed in apigateway RPS units
        # (same scale as the other apigateway -> * edges) so the
        # saturation curve shifts the LLM-side response in lockstep
        # with the rest of the front-half fan-out. ``latency_gain``
        # sits between authservice (0.5) and database (0.6); the LLM
        # is moderately sensitive to upstream throttle because every
        # token call queues behind the budget. ``error_gain`` follows
        # the same band as the other downstream edges.
        Edge(
            target="llm_analytics",
            weight=1.0,
            saturation=SaturationParams(
                midpoint=760.0, steepness=6.0,
                latency_gain=0.55, error_gain=0.015,
            ),
        ),
    ],
    # Cache miss rate drives extra database load on top of apigateway's
    # routing fraction. ``signal`` is the module-level
    # ``_cache_miss_ratio_signal`` which derives the per-row cache-miss
    # ratio (``cache_misses / (cache_hits + cache_misses)``) from
    # cacheservice's captured columns; the callable ``weight`` then
    # maps that ratio onto the additive QPS contribution to the
    # database baseline: ``weight(miss_ratio) = miss_ratio * base_qps``.
    # At the natural baseline (~4% miss rate, ~25k base QPS) this is
    # ~1000 QPS on top of the apigateway-driven contribution.
    # ``base_qps`` is resolved lazily via ``_component_metric_base`` so
    # the lambda always reads the live ``COMPONENTS`` catalog — matching
    # the constant-weight path's behavior under monkeypatched / test-
    # injected baselines.
    "cacheservice": [
        Edge(
            target="database",
            signal=_cache_miss_ratio_signal,
            weight=lambda miss_ratio: (
                np.asarray(miss_ratio, dtype=np.float64)
                * _component_metric_base("database", "queries_per_sec")
            ),
        ),
    ],
}


def _validate_saturation_params(sat: SaturationParams, *, context: str) -> None:
    """Field-level invariants for a ``SaturationParams`` instance.

    Used by ``_validate_topology()`` at import time on every edge that
    carries saturation, and re-checked at call time inside
    ``_apply_saturation()`` so direct callers (tests, future consumers)
    cannot smuggle in bad params. ``context`` is a short string naming
    the source of the params (an edge identifier or the function name)
    so the raised ``ValueError`` points at the offending site.

    Rejected inputs per field:

    - ``midpoint`` — must be a finite positive non-``bool``
      ``int``/``float``. Zero divides; negative or non-finite
      contaminates ``utilization`` with non-finite values; ``bool`` is
      an ``int`` subtype so ``True`` would otherwise slip through.
    - ``steepness`` — must be a finite positive non-``bool``
      ``int``/``float``. Zero collapses the logistic to a constant
      0.5; negative inverts the curve.
    - ``latency_gain`` / ``error_gain`` — must be finite non-negative
      non-``bool`` ``int``/``float``. The saturation curve models
      load-driven *degradation*: a positive gain raises latency and
      error rate as upstream load climbs. Negative gains would invert
      that physics (saturation reducing latency / pushing
      ``error_offset`` below zero) and, when multiplied across two
      saturating edges into the same downstream, could flip
      ``latency_multiplier`` past zero into negative latency.
    """
    def _check(name: str, value, *, positive: bool) -> None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(
                f"{context}: SaturationParams.{name}={value!r} must be a "
                f"finite {'positive' if positive else 'non-negative'} "
                f"int/float; got {type(value).__name__}."
            )
        if not math.isfinite(value):
            raise ValueError(
                f"{context}: SaturationParams.{name}={value!r} must be "
                f"finite."
            )
        if positive and value <= 0:
            raise ValueError(
                f"{context}: SaturationParams.{name}={value!r} must be > 0."
            )
        if not positive and value < 0:
            raise ValueError(
                f"{context}: SaturationParams.{name}={value!r} must be "
                f">= 0."
            )

    _check("midpoint", sat.midpoint, positive=True)
    _check("steepness", sat.steepness, positive=True)
    _check("latency_gain", sat.latency_gain, positive=False)
    _check("error_gain", sat.error_gain, positive=False)


def _validate_topology() -> None:
    """Import-time invariants for ``TOPOLOGY``.

    Catches drift between the topology graph and ``COMPONENTS`` at module
    load so phase 2's two-pass generator can rely on every source and
    target being a real component. Callable weights are smoke-tested with
    a tiny ``np.ndarray`` so a mis-shaped lambda (e.g. zero-arg or scalar-
    only) fails here instead of corrupting the generator's vectorized
    column writes downstream. Each non-``None`` ``Edge.saturation`` has
    its ``SaturationParams`` field invariants enforced via
    ``_validate_saturation_params`` so phase 4's saturation feedback
    cannot silently consume ``NaN``/``inf``/``bool``/negative values.
    """
    known_components = set(COMPONENTS.keys())
    for source, edges in TOPOLOGY.items():
        if source not in known_components:
            raise ValueError(
                f"TOPOLOGY source {source!r} is not in COMPONENTS; "
                f"known components: {sorted(known_components)}"
            )
        if not isinstance(edges, list):
            raise ValueError(
                f"TOPOLOGY[{source!r}] must be a list of Edge, got "
                f"{type(edges).__name__}"
            )
        for edge in edges:
            if not isinstance(edge, Edge):
                raise ValueError(
                    f"TOPOLOGY[{source!r}] contains a non-Edge entry "
                    f"{edge!r} (type {type(edge).__name__}); every entry "
                    f"must be an Edge instance."
                )
            if edge.target not in known_components:
                raise ValueError(
                    f"TOPOLOGY[{source!r}] -> Edge.target={edge.target!r} "
                    f"is not in COMPONENTS; known components: "
                    f"{sorted(known_components)}"
                )
            if edge.saturation is not None:
                if not isinstance(edge.saturation, SaturationParams):
                    raise ValueError(
                        f"TOPOLOGY[{source!r}] -> {edge.target!r} "
                        f"Edge.saturation={edge.saturation!r} must be a "
                        f"SaturationParams instance or None; got "
                        f"{type(edge.saturation).__name__}."
                    )
                _validate_saturation_params(
                    edge.saturation,
                    context=f"TOPOLOGY[{source!r}] -> {edge.target!r}",
                )
            if edge.correlation_threshold is not None:
                # phase 7: validator-only per-edge override of the
                # default Pearson coupling threshold. ``bool`` is an ``int``
                # subtype so reject it explicitly before the numeric check.
                if (isinstance(edge.correlation_threshold, bool)
                        or not isinstance(
                            edge.correlation_threshold, (int, float)
                        )):
                    raise ValueError(
                        f"TOPOLOGY[{source!r}] -> {edge.target!r} "
                        f"correlation_threshold="
                        f"{edge.correlation_threshold!r} must be a finite "
                        f"float in (-1, 1] or None; got "
                        f"{type(edge.correlation_threshold).__name__}."
                    )
                if not math.isfinite(edge.correlation_threshold):
                    raise ValueError(
                        f"TOPOLOGY[{source!r}] -> {edge.target!r} "
                        f"correlation_threshold="
                        f"{edge.correlation_threshold!r} must be finite."
                    )
                if not -1.0 < edge.correlation_threshold <= 1.0:
                    raise ValueError(
                        f"TOPOLOGY[{source!r}] -> {edge.target!r} "
                        f"correlation_threshold="
                        f"{edge.correlation_threshold!r} must be in the "
                        f"half-open interval (-1, 1]."
                    )
            if callable(edge.weight):
                probe = np.array([0.0, 0.5, 1.0], dtype=np.float64)
                try:
                    result = edge.weight(probe)
                except Exception as exc:
                    raise ValueError(
                        f"TOPOLOGY[{source!r}] -> {edge.target!r} callable "
                        f"weight {edge.weight!r} raised "
                        f"{type(exc).__name__}({exc!r}) when called with a "
                        f"numpy array; callable weights must accept an "
                        f"ndarray and return an ndarray."
                    ) from exc
                if not isinstance(result, np.ndarray):
                    raise ValueError(
                        f"TOPOLOGY[{source!r}] -> {edge.target!r} callable "
                        f"weight {edge.weight!r} returned "
                        f"{type(result).__name__}; callable weights must "
                        f"return a numpy array."
                    )
                # Callable weights require a per-edge signal: the composer
                # feeds ``edge.signal(upstream_cols)``'s return value
                # straight into ``edge.weight(signal)``. Without a signal
                # the composer has no per-row input and would silently
                # skip the edge — exactly the soft footgun this refactor
                # is removing.
                if edge.signal is None:
                    raise ValueError(
                        f"TOPOLOGY[{source!r}] -> {edge.target!r} has "
                        f"callable weight but signal=None; callable "
                        f"weights require a per-edge signal callable."
                    )
                if not callable(edge.signal):
                    raise ValueError(
                        f"TOPOLOGY[{source!r}] -> {edge.target!r} signal="
                        f"{edge.signal!r} must be callable; got "
                        f"{type(edge.signal).__name__}."
                    )
                ups_entry = _TOPOLOGY_LOAD_METRICS.get(source)
                if ups_entry is None:
                    probe_cols: dict[str, np.ndarray] = {}
                else:
                    canonical_src, supplementary_src = ups_entry
                    # Distinct array per key: real captured columns are
                    # always per-column buffers, and a future signal that
                    # mutates an input in-place (e.g. via ``out=``) must
                    # not silently alias other "columns" in the probe.
                    probe_template = np.array(
                        [0.0, 0.5, 1.0], dtype=np.float64
                    )
                    probe_cols = {
                        name: probe_template.copy()
                        for name in (canonical_src, *supplementary_src)
                        if name
                    }
                try:
                    sig_result = edge.signal(probe_cols)
                except Exception as exc:
                    raise ValueError(
                        f"TOPOLOGY[{source!r}] -> {edge.target!r} signal "
                        f"{edge.signal!r} raised {exc!r} when called with "
                        f"the upstream's captured-column probe; signal "
                        f"callables must accept a dict[str, np.ndarray] "
                        f"and return np.ndarray or None."
                    ) from exc
                if sig_result is not None and not isinstance(
                    sig_result, np.ndarray
                ):
                    raise ValueError(
                        f"TOPOLOGY[{source!r}] -> {edge.target!r} signal "
                        f"returned {type(sig_result).__name__}; signal "
                        f"callables must return np.ndarray or None."
                    )
            else:
                # Constant weight: must be a finite, non-negative scalar.
                # ``bool`` is a subclass of ``int`` so ``isinstance(True,
                # (int, float))`` is True; reject it explicitly before the
                # numeric check.
                if (isinstance(edge.weight, bool)
                        or not isinstance(edge.weight, (int, float))):
                    raise ValueError(
                        f"TOPOLOGY[{source!r}] -> {edge.target!r} weight="
                        f"{edge.weight!r} must be a finite non-negative "
                        f"int/float or a callable (np.ndarray) -> "
                        f"np.ndarray; got {type(edge.weight).__name__}."
                    )
                if not math.isfinite(edge.weight):
                    raise ValueError(
                        f"TOPOLOGY[{source!r}] -> {edge.target!r} weight="
                        f"{edge.weight!r} must be finite."
                    )
                if edge.weight < 0:
                    raise ValueError(
                        f"TOPOLOGY[{source!r}] -> {edge.target!r} weight="
                        f"{edge.weight!r} must be non-negative."
                    )
                # Constant weight: signal is meaningless because the
                # composer never reads it. Reject up-front so an edge
                # author cannot stash a stale signal on a constant edge
                # and assume it will fire.
                if edge.signal is not None:
                    raise ValueError(
                        f"TOPOLOGY[{source!r}] -> {edge.target!r} has "
                        f"constant weight={edge.weight!r} but signal is "
                        f"set; signal is only valid with a callable "
                        f"weight."
                    )

    # Cycle detection (phase 3): the two-pass realistic-mode
    # generator walks TOPOLOGY in Kahn order and expects a DAG. Reject
    # any cycle (including self-loops) at import time so a cyclic edit
    # fails fast instead of silently falling back to COMPONENTS order.
    incoming: dict[str, set[str]] = {}
    for source, edges in TOPOLOGY.items():
        incoming.setdefault(source, set())
        for edge in edges:
            incoming.setdefault(edge.target, set()).add(source)
    remaining = {node: set(deps) for node, deps in incoming.items()}
    while remaining:
        ready = [n for n, deps in remaining.items() if not deps]
        if not ready:
            cycle_nodes = sorted(remaining.keys())
            raise ValueError(
                f"TOPOLOGY must be acyclic; cycle detected among "
                f"nodes {cycle_nodes}"
            )
        for n in ready:
            del remaining[n]
            for deps in remaining.values():
                deps.discard(n)


_validate_topology()


# Phase 2/3: standard deviation of the additive noise
# injected on top of the coupled upstream signal under realistic
# topology coupling. Kept small (5.0) relative to the typical
# coupling signal std (~15–1600 depending on component) so the Pearson
# correlation between upstream and downstream stays well above every
# gate that reads it — the 0.95 phase-2 acceptance threshold in
# ``tests/test_topology_loadbalancer_gateway.py``, the 0.9 phase-3
# thresholds in ``tests/test_topology_fanout.py``, and the validator's
# ``_TOPOLOGY_DEFAULT_CORRELATION_THRESHOLD = 0.85`` — while the
# column still looks like a noisy signal rather than a perfect copy
# of the upstream.
_TOPOLOGY_COUPLE_NOISE_STD = 5.0


def _topology_generation_order(active_components: set[str]) -> list[str]:
    """Return ``active_components`` in topological generation order.

    Roots (no incoming TOPOLOGY edges from any other active component) come
    first; downstream components come after their upstream(s). Only edges
    where both endpoints are in ``active_components`` are considered, so
    ``--components`` filtering naturally restricts the dependency graph.
    Cycles are not expected in TOPOLOGY (``_validate_topology`` rejects
    them at import time, so this branch is defensive dead code); if one
    ever appeared, the fallback flushes *all* remaining nodes — cycle
    members and their not-yet-ready downstreams alike — in one
    ``COMPONENTS``-insertion-order pass so the walk always makes
    forward progress.

    Ties (multiple roots / multiple ready nodes at the same Kahn step)
    break on ``COMPONENTS`` insertion order so the result is deterministic
    regardless of how the caller iterates ``args.components``.
    """
    incoming: dict[str, set[str]] = {c: set() for c in active_components}
    for source, edges in TOPOLOGY.items():
        if source not in active_components:
            continue
        for edge in edges:
            if edge.target in incoming and edge.target != source:
                incoming[edge.target].add(source)
    component_index = {name: i for i, name in enumerate(COMPONENTS.keys())}
    ordered: list[str] = []
    remaining = {c: set(deps) for c, deps in incoming.items()}
    while remaining:
        ready = sorted(
            (c for c, deps in remaining.items() if not deps),
            key=lambda c: component_index[c],
        )
        if not ready:
            ready = sorted(remaining.keys(), key=lambda c: component_index[c])
        for c in ready:
            ordered.append(c)
            del remaining[c]
            for deps in remaining.values():
                deps.discard(c)
    return ordered


def _compose_topology_coupled_specs(
    component_name: str,
    specs: list[MetricSpec],
    upstream_arrays: dict[str, dict[str, np.ndarray]],
    rng: "np.random.RandomState",
    n_rows: int,
) -> list[MetricSpec]:
    """Return a possibly-modified spec list with the downstream's load
    metric(s) coupled to upstream component(s) via the TOPOLOGY graph.

    Phase 3 extends the coupling to every constant-weight
    edge in the v1 graph plus the ``cacheservice -> database`` callable
    edge:

    * Constant-weight edges scale the upstream's captured load column to
      the downstream metric's natural baseline:
      ``contribution = (upstream / upstream_base) * downstream_base *
      w_norm`` where ``w_norm = w / Σw`` across all active constant edges
      to this downstream. The normalization makes the combined constant
      term equal ``downstream_base`` at natural upstream load *regardless*
      of the raw weights' sum — relative weights set the fan-out shares,
      but the absolute values do not leave any "uncoupled" residue at
      the natural baseline. (Today the v1 graph's three apigateway fan-
      out weights already sum to 1.0; the renormalization keeps the
      formula well-defined if that invariant is ever relaxed.)

      Side-effect under ``--components`` subsetting: the normalization
      is computed over the *active* edges only, not the full declared
      fan-out. If a run drops one of apigateway's three fan-out targets
      (say ``--components apigateway,authservice,database``), the
      surviving fan-out edges renormalize so each carries its full
      ``downstream_base`` at natural upstream load — not the routing-
      fraction-weighted share the raw weights imply. This is intentional
      (subsetting should not leave the surviving downstreams running at
      a fraction of their natural baseline), but it does mean the
      effective per-edge contribution depends on which components are
      active; pin a full ``--components all`` baseline when comparing
      coupling magnitudes across runs.
    * Callable-weight edges call ``edge.weight(signal)`` with a per-row
      scalar signal derived from the upstream's captured columns by
      ``edge.signal(upstream_cols)``. The signal callable is paired
      with the callable weight on the same ``Edge`` (the import-time
      validator enforces the pairing); ``signal`` returning ``None``
      means "skip this edge" (e.g. a ``--metrics-per-component``
      selection trimmed a required input column). The weight's return
      value is added to the downstream baseline directly (in
      downstream-metric units) — e.g. the ``cacheservice -> database``
      callable returns the per-row cache-miss QPS contribution.

    When neither path delivers any signal (no upstream captured, all
    constant weights are zero, callable signal absent) the spec list is
    returned unchanged so the downstream falls back to its natural
    Gaussian baseline.

    The natural per-metric ``MetricSpec`` (multiplier, additive,
    clip_min, declarative schema metadata) is preserved via
    ``dataclasses.replace``; only ``base``, ``std``, ``multiplier``,
    and ``additive`` change so the coupled column writes the baked
    coupled column verbatim.
    """
    coupled_entry = _TOPOLOGY_LOAD_METRICS.get(component_name)
    if coupled_entry is None:
        return specs
    canonical_down, supplementary_down = coupled_entry
    coupled_metric_names = (canonical_down, *supplementary_down)
    name_to_idx = {s.name: i for i, s in enumerate(specs)}
    if not any(m in name_to_idx for m in coupled_metric_names):
        return specs
    incoming: list[tuple[str, Edge]] = []
    for upstream, edges in TOPOLOGY.items():
        if upstream not in upstream_arrays:
            continue
        for edge in edges:
            if edge.target == component_name:
                incoming.append((upstream, edge))
    if not incoming:
        return specs

    # Callable-weight contributions are computed once per component —
    # ``edge.signal`` / ``edge.weight`` are metric-invariant, so
    # re-evaluating them per coupled metric was redundant — and applied
    # only to the *canonical* load metric below: the weight callable
    # returns values in the downstream's canonical-metric units (e.g.
    # ``_cache_miss_ratio_signal``'s weight scales to
    # ``database.queries_per_sec``'s natural base), so adding the same
    # array to a supplementary metric with a different base would inject
    # a wrong-unit contribution. Inert today — no callable-edge target
    # declares supplementary captures — but the first one added would
    # have silently mixed units. Track whether any callable signal was
    # successfully evaluated separately from the numeric contribution —
    # a callable that happens to be exactly zero everywhere (e.g. a
    # cache with a 0% miss rate for the whole run) is still a valid
    # coupling signal, not an absent one, and must not silently fall
    # back to the natural Gaussian baseline.
    callable_active = False
    callable_contrib = np.zeros(n_rows, dtype=np.float64)
    for upstream, edge in incoming:
        if not callable(edge.weight):
            continue
        if edge.signal is None:
            # Defence-in-depth: the validator rejects callable-weight
            # edges without ``signal`` at import-time. A missing
            # ``signal`` here means a future contributor bypassed the
            # validator (e.g. via a monkeypatched TOPOLOGY in a test);
            # skip the edge rather than crashing the generator.
            continue
        ups_cols = upstream_arrays.get(upstream, {})
        signal = edge.signal(ups_cols)
        if signal is None:
            continue
        callable_contrib = callable_contrib + np.asarray(
            edge.weight(signal), dtype=np.float64
        )
        callable_active = True

    new_specs = list(specs)
    for metric_name in coupled_metric_names:
        if metric_name not in name_to_idx:
            continue
        original = specs[name_to_idx[metric_name]]
        downstream_base = float(original.base)
        if downstream_base <= 0:
            continue
        # Canonical-only: see the callable-contribution comment above.
        metric_callable_active = (
            callable_active and metric_name == canonical_down
        )

        # First pass: collect all active constant-weight edges to compute
        # the normalization factor that maps ``sum(weight)`` to 1.0 so the
        # combined contribution equals ``downstream_base`` at natural
        # upstream load.
        active_constant: list[tuple[np.ndarray, float, float]] = []  # (arr, base, w)
        for upstream, edge in incoming:
            if callable(edge.weight):
                continue
            if isinstance(edge.weight, bool) or not isinstance(
                edge.weight, (int, float)
            ):
                continue
            w = float(edge.weight)
            if w == 0.0:
                continue
            ups_cols = upstream_arrays.get(upstream, {})
            ups_entry = _TOPOLOGY_LOAD_METRICS.get(upstream)
            if ups_entry is None:
                continue
            ups_canonical, _ = ups_entry
            if ups_canonical and ups_canonical in ups_cols:
                ups_base = _component_metric_base(upstream, ups_canonical)
                if ups_base > 0:
                    active_constant.append(
                        (ups_cols[ups_canonical], ups_base, w)
                    )

        if not active_constant and not metric_callable_active:
            continue

        # Constant contributions: normalize by sum(w) so the constant term
        # equals ``downstream_base`` at natural upstream load regardless of
        # how many contributing edges exist. Each upstream's array is scaled
        # by ``(upstream / upstream_base) * downstream_base * w_normalized``
        # so variation in the upstream flows through at a proportional scale
        # to the downstream metric's natural magnitude.
        constant_contrib = np.zeros(n_rows, dtype=np.float64)
        if active_constant:
            sum_w = sum(w for _, _, w in active_constant)
            for ups_arr, ups_base, w in active_constant:
                w_norm = w / sum_w  # normalise so contributions sum to 1.0
                constant_contrib = constant_contrib + (
                    ups_arr / ups_base * downstream_base * w_norm
                )

        coupled = (
            constant_contrib
            + (callable_contrib if metric_callable_active else 0.0)
            + rng.normal(0.0, _TOPOLOGY_COUPLE_NOISE_STD, n_rows)
        )
        new_specs[name_to_idx[metric_name]] = dataclasses.replace(
            original,
            base=0.0,
            std=0.0,
            multiplier=None,
            additive=lambda ts, elapsed, baked=coupled: baked,
        )
    return new_specs


# Phase 4: Maximum utilization clamp before the logistic. Keeps
# ``np.exp`` numerically stable for arbitrary load magnitudes; the logistic
# is already > 0.99 at utilization = 2 with the smallest planned steepness
# (5), so a cap at 5x has no practical effect on the shape.
_SATURATION_MAX_UTILIZATION = 5.0


def _apply_saturation(
    upstream_load: np.ndarray, sat: SaturationParams,
) -> tuple[np.ndarray, np.ndarray]:
    """Compute the per-row ``(latency_multiplier, error_offset)`` arrays
    for one saturating TOPOLOGY edge.

    The logistic response curve:

        utilization = upstream_load / sat.midpoint
                       (clamped to ``[0, _SATURATION_MAX_UTILIZATION]`` so
                       ``np.exp`` stays finite for any input)
        logistic    = 1 / (1 + exp(-sat.steepness * (utilization - 1)))
        latency_multiplier = 1 + sat.latency_gain * logistic
        error_offset       = sat.error_gain * logistic

    Bounds: ``latency_multiplier`` ∈ ``[1, 1 + latency_gain]`` (always
    positive given non-negative gains); ``error_offset`` ∈
    ``[0, error_gain]`` (capped by the gain itself).

    ``upstream_load`` is the captured load metric of the saturating edge's
    *source* component (e.g. ``loadbalancer.requests_per_sec`` for the
    ``loadbalancer -> apigateway`` edge). Phase 4 drives the curve from
    upstream load — which Kahn ordering guarantees is already captured
    in ``upstream_arrays`` when the downstream is composed — rather
    than the downstream's own load column, which is still being
    constructed at composition time.
    """
    _validate_saturation_params(sat, context="_apply_saturation")
    utilization = np.maximum(
        np.asarray(upstream_load, dtype=np.float64), 0.0
    ) / float(sat.midpoint)
    np.minimum(utilization, _SATURATION_MAX_UTILIZATION, out=utilization)
    logistic = 1.0 / (1.0 + np.exp(-sat.steepness * (utilization - 1.0)))
    latency_multiplier = 1.0 + sat.latency_gain * logistic
    error_offset = sat.error_gain * logistic
    return latency_multiplier, error_offset


# Per-component map of ``(latency_metrics, error_metrics)`` that
# incoming saturating TOPOLOGY edges modulate. The latency metrics get
# the per-edge ``latency_multiplier`` composed multiplicatively into
# their ``MetricSpec.multiplier``; the error metrics get the per-edge
# ``error_offset`` added to their ``MetricSpec.additive``. Components
# absent from this map are saturation-inert even when they have
# incoming saturating edges, so additional downstream targets can be
# added here without touching the front-half wiring. Phase 4
# wired the four front-half targets (apigateway and its three fan-out
# downstreams); phase 5 added ``llm_analytics`` for the
# token-throttle response.
_TOPOLOGY_SATURATION_TARGETS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "apigateway": (
        ("avg_response_time_ms", "backend_latency_ms"),
        ("error_rate",),
    ),
    "authservice": (
        ("avg_auth_latency_ms",),
        ("error_rate",),
    ),
    "cacheservice": (
        ("avg_cache_latency_ms",),
        ("error_rate",),
    ),
    "database": (
        ("read_latency_ms", "write_latency_ms"),
        ("error_rate",),
    ),
    # phase 5: under apigateway saturation (the LLM token
    # budget), the llm_analytics latency family lifts via the logistic
    # multiplier and the LLM-specific error rate lifts via the additive
    # offset. The catalog exposes ``llm_api_error_rate`` (not the
    # generic ``error_rate``) so the LLM error column is the right
    # additive target.
    "llm_analytics": (
        ("avg_llm_latency_ms", "p95_llm_latency_ms"),
        ("llm_api_error_rate",),
    ),
}


def _validate_topology_metric_registries() -> None:
    """Import-time validation of the topology *metric* registries.

    ``_validate_topology()`` exhaustively validates ``TOPOLOGY`` itself,
    but the two companion registries that name actual metric columns —
    ``_TOPOLOGY_LOAD_METRICS`` and ``_TOPOLOGY_SATURATION_TARGETS`` —
    were previously unchecked, and every runtime consumer degrades
    *silently* on a miss: a typo'd canonical load metric makes
    ``_component_metric_base`` return 0.0 so the coupling edge is
    skipped; an unregistered saturating source falls through
    ``ups_entry is None``; a typo'd saturation target falls through
    ``name_to_idx.get(...)``. Those soft fallbacks exist to tolerate
    legitimate runtime states (``--metrics-per-component`` trims,
    ``--components`` subsets) — but they also swallowed registry typos,
    so a new edge with a misspelled metric would pass import, generate
    fully decoupled output, and surface only at the opt-in
    ``validate`` subcommand's Pearson check. This validator fails the typo
    at import time instead. Checks:

    * every ``_TOPOLOGY_LOAD_METRICS`` key is a ``COMPONENTS`` key, and
      its canonical + supplementary names all exist in that component's
      *full* metric catalog (the un-trimmed list — trimming is a
      runtime state, not a registry property);
    * every ``_TOPOLOGY_SATURATION_TARGETS`` key is a ``COMPONENTS``
      key, and every latency-family / error-family name exists in that
      component's full catalog;
    * every ``TOPOLOGY`` source with at least one constant-weight or
      saturating outgoing edge has a ``_TOPOLOGY_LOAD_METRICS`` entry
      (the constant-weight composer and the saturation driver both
      read the source's canonical column);
    * every constant-weight edge's *target* has a
      ``_TOPOLOGY_LOAD_METRICS`` entry (the composer rewrites the
      target's own load metrics — a missing entry makes the edge
      silently inert);
    * every saturating edge's target has a
      ``_TOPOLOGY_SATURATION_TARGETS`` entry.

    Mirrored by ``tests/test_topology_registry.py``.
    """
    catalog_names = {
        comp: {s.name for s in specs} for comp, specs in COMPONENTS.items()
    }
    for comp, entry in _TOPOLOGY_LOAD_METRICS.items():
        if comp not in COMPONENTS:
            raise ValueError(
                f"_TOPOLOGY_LOAD_METRICS key {comp!r} is not a COMPONENTS key"
            )
        canonical, supplementary = entry
        for metric in (canonical, *supplementary):
            if metric not in catalog_names[comp]:
                raise ValueError(
                    f"_TOPOLOGY_LOAD_METRICS[{comp!r}] names metric "
                    f"{metric!r} which is not in COMPONENTS[{comp!r}]"
                )
    for comp, (latency_metrics, error_metrics) in _TOPOLOGY_SATURATION_TARGETS.items():
        if comp not in COMPONENTS:
            raise ValueError(
                f"_TOPOLOGY_SATURATION_TARGETS key {comp!r} is not a "
                "COMPONENTS key"
            )
        for metric in (*latency_metrics, *error_metrics):
            if metric not in catalog_names[comp]:
                raise ValueError(
                    f"_TOPOLOGY_SATURATION_TARGETS[{comp!r}] names metric "
                    f"{metric!r} which is not in COMPONENTS[{comp!r}]"
                )
    for source, edges in TOPOLOGY.items():
        for edge in edges:
            saturating = edge.saturation is not None and (
                edge.saturation.latency_gain != 0.0
                or edge.saturation.error_gain != 0.0
            )
            if callable(edge.weight) and not saturating:
                # Callable-weight edges read the source's captured
                # columns through their own ``signal``, which
                # ``_validate_topology`` already probes against
                # ``_TOPOLOGY_LOAD_METRICS`` — but only a non-callable
                # weight or a saturating edge *requires* the canonical
                # column below.
                continue
            if source not in _TOPOLOGY_LOAD_METRICS:
                raise ValueError(
                    f"TOPOLOGY source {source!r} has a constant-weight or "
                    f"saturating edge to {edge.target!r} but no "
                    "_TOPOLOGY_LOAD_METRICS entry; the coupling composer "
                    "and saturation driver would silently skip the edge"
                )
            if not callable(edge.weight) and edge.target not in _TOPOLOGY_LOAD_METRICS:
                raise ValueError(
                    f"TOPOLOGY constant-weight edge {source!r} -> "
                    f"{edge.target!r} targets a component with no "
                    "_TOPOLOGY_LOAD_METRICS entry; the composer rewrites "
                    "the target's load metrics, so the edge would be "
                    "silently inert"
                )
            if saturating and edge.target not in _TOPOLOGY_SATURATION_TARGETS:
                raise ValueError(
                    f"TOPOLOGY saturating edge {source!r} -> {edge.target!r} "
                    "targets a component with no _TOPOLOGY_SATURATION_TARGETS "
                    "entry; the saturation contribution would be silently "
                    "dropped"
                )


_validate_topology_metric_registries()


def _compose_topology_saturation_specs(
    component_name: str,
    specs: list[MetricSpec],
    upstream_arrays: dict[str, dict[str, np.ndarray]],
    n_rows: int,
) -> list[MetricSpec]:
    """Apply saturation feedback from every incoming TOPOLOGY edge with
    non-None ``SaturationParams`` to the downstream's latency-family and
    error-family ``MetricSpec`` entries (as declared in
    ``_TOPOLOGY_SATURATION_TARGETS``).

    For each saturating incoming edge the upstream's primary captured
    load metric drives ``_apply_saturation`` once. Multiple incoming
    saturating edges to the same downstream compose multiplicatively for
    the latency factor (each edge layers an additional load-dependent
    slowdown) and additively for the error offset (each edge contributes
    its own failure surface).

    The natural ``MetricSpec.multiplier`` / ``MetricSpec.additive`` (e.g.
    a ``_daily_sine`` envelope) is preserved by closing over the
    saturation array and composing on top of the existing callable — so
    seasonal patterns remain visible underneath the saturation curve.
    Only ``multiplier`` and ``additive`` change; ``std``, ``clip_min``,
    and the declarative schema metadata pass through unchanged.

    Returns ``specs`` unchanged when:

    * the component is not in ``_TOPOLOGY_SATURATION_TARGETS``;
    * no incoming saturating edge has its upstream captured (e.g. a
      ``--components`` subset that removes the upstream);
    * every incoming saturating edge declares zero ``latency_gain`` and
      zero ``error_gain`` (no v1 edges sit in this state after
      phase 5 promoted the LLM placeholder).
    """
    targets = _TOPOLOGY_SATURATION_TARGETS.get(component_name)
    if targets is None:
        return specs
    latency_metrics, error_metrics = targets
    if not latency_metrics and not error_metrics:
        return specs

    name_to_idx = {s.name: i for i, s in enumerate(specs)}
    latency_factor = np.ones(n_rows, dtype=np.float64)
    error_offset = np.zeros(n_rows, dtype=np.float64)
    any_active = False
    for upstream, edges in TOPOLOGY.items():
        ups_cols = upstream_arrays.get(upstream)
        if not ups_cols:
            continue
        for edge in edges:
            if edge.target != component_name or edge.saturation is None:
                continue
            sat = edge.saturation
            if sat.latency_gain == 0.0 and sat.error_gain == 0.0:
                continue  # structurally-declared but inert edge.
            ups_entry = _TOPOLOGY_LOAD_METRICS.get(upstream)
            if ups_entry is None:
                continue
            ups_canonical, _ups_supplementary = ups_entry
            # Canonical-only driver: ``sat.midpoint`` is tuned in the
            # upstream's canonical load-metric units, so a supplementary
            # column (different units — e.g. cacheservice's
            # ``cache_misses``) must never drive the logistic. When the
            # canonical column is absent (``--metrics-per-component``
            # trim) the edge is skipped, matching the constant-weight
            # coupling path's posture.
            driver = ups_cols.get(ups_canonical)
            if driver is None or driver.shape[0] != n_rows:
                continue
            lat_mult, err_off = _apply_saturation(driver, sat)
            latency_factor *= lat_mult
            error_offset += err_off
            any_active = True

    if not any_active:
        return specs

    # Both loops read (and replace into) ``new_specs`` rather than the
    # pristine ``specs`` so a metric that appears in BOTH the latency and
    # error tuples composes both effects. Reading ``specs[idx]`` in the
    # second loop would rebuild the spec from the original and silently
    # discard the multiplier wrap the first loop installed — diverging
    # from the per-instance path, which applies both sides of the
    # ``(latency_factor, error_offset)`` tuple to an overlap target. No
    # v1 registry entry overlaps today; this keeps the two paths aligned
    # for the first one that does.
    new_specs = list(specs)
    for metric_name in latency_metrics:
        idx = name_to_idx.get(metric_name)
        if idx is None:
            continue
        original = new_specs[idx]
        old_mult = original.multiplier
        if old_mult is None:
            new_mult = lambda ts, elapsed, baked=latency_factor: baked
        else:
            new_mult = (
                lambda ts, elapsed, baked=latency_factor, base=old_mult:
                base(ts, elapsed) * baked
            )
        new_specs[idx] = dataclasses.replace(original, multiplier=new_mult)
    for metric_name in error_metrics:
        idx = name_to_idx.get(metric_name)
        if idx is None:
            continue
        original = new_specs[idx]
        old_add = original.additive
        if old_add is None:
            new_add = lambda ts, elapsed, baked=error_offset: baked
        else:
            new_add = (
                lambda ts, elapsed, baked=error_offset, base=old_add:
                base(ts, elapsed) + baked
            )
        new_specs[idx] = dataclasses.replace(original, additive=new_add)

    return new_specs


# ------------------------------------------------------------------
# Per-instance topology (phase 8).
# ------------------------------------------------------------------
# When ``--instances-per-component N > 1`` (or any non-default
# ``--instance-config``), the topology two-pass generation runs against
# each downstream instance's *matching* upstream view rather than the
# shared aggregate. The matching rule depends on the per-edge upstream
# vs. downstream cardinality:
#
# * **1:1 routing (matched cardinalities, ``len(upstream_instances) ==
#   len(downstream_instances)``).** Downstream instance ``K`` sees
#   upstream instance ``K`` exclusively for that edge. This is the
#   "matching instance set" branch from the issue scope; it
#   delivers the per-pod isolation the test verifies (a slow upstream
#   pod only saturates the corresponding downstream pod).
# * **Uniform fan-out (mismatched cardinalities).** Downstream instance
#   ``K`` sees the mean of all upstream instances' load — equivalent to
#   the issue's "edge weight divided by downstream cardinality" formula
#   averaged over ``N_up`` upstream pods. This is the fallback when the
#   1:1 mapping is undefined and matches the existing N=1-vs-N=1
#   aggregate behavior at the limit.
#
# Under symmetric upstream (no ``instance_filter`` on an upstream load
# metric, the default for every shipped scenario), every per-instance
# upstream view equals the shared aggregate view, so the per-instance
# saturation / coupling arrays collapse to the shared arrays and the
# CSV bytes are byte-identical to the pre-existing default-N=3 run. The
# locked ``N3_ONE_DAY_HASHES`` and ``N3_SEVEN_DAY_HASHES`` in
# ``tests/test_instances_per_component.py`` continue to hold without
# re-baselining.

def _matched_cardinality(upstream_inst_count: int, downstream_inst_count: int) -> bool:
    """Return True when 1:1 routing applies between source and target.

    Routes via ``upstream_instances[K] -> downstream_instances[K]``
    when both sides have the same number of instances. Otherwise the
    composer falls back to uniform fan-out (averaging across upstream
    pods) — see the module-level comment above.

    Only ``N == N`` matched lengths are 1:1; the helper treats any
    other shape (including ``N_up == 1`` against ``N_down > 1`` or vice
    versa) as the uniform-fan-out fallback so per-instance views are
    well-defined for any combination.
    """
    return (
        upstream_inst_count == downstream_inst_count
        and upstream_inst_count > 0
    )


def _per_instance_upstream_view(
    upstream_name: str,
    upstream_arrays_by_instance: list[dict[str, np.ndarray]] | None,
    upstream_arrays_shared: dict[str, np.ndarray] | None,
    downstream_inst_count: int,
    downstream_inst_idx: int,
    *,
    uniform_fanout_cache: dict[str, dict[str, np.ndarray]] | None = None,
) -> dict[str, np.ndarray] | None:
    """Return the captured-column dict that downstream instance K should
    consume from ``upstream_name``.

    Dispatches between the matched-cardinality 1:1 branch and the
    uniform fan-out branch, both producing a ``dict[metric_name,
    np.ndarray]`` shaped identically to ``upstream_arrays_shared`` so
    the existing ``_compose_topology_coupled_specs`` /
    ``_compose_topology_saturation_specs`` math can be re-used per
    instance.

    Returns ``None`` when no upstream capture is available — the
    composer skips this edge so a ``--components`` subset that drops
    the upstream degrades gracefully (identical to the N=1 path's
    ``upstream not in upstream_arrays`` guard).

    ``uniform_fanout_cache`` (optional) memoizes the averaged upstream
    dict by ``upstream_name``. Under mismatched cardinality the
    averaged view is identical for every downstream instance, so
    callers that loop across downstream instances pass a shared dict
    to avoid repeating the incremental sum-then-divide averaging work
    (O(N_down * N_up * n_rows) → O(N_up * n_rows)). Pass ``None`` for
    one-shot callers.
    """
    if upstream_arrays_by_instance is None:
        # No per-instance capture available for the upstream — fall
        # back to the shared aggregate view, equivalent to today's
        # N=1 path. This branch fires for N=1 upstream components in
        # mixed-N scenarios (the only mixed-N entry path is
        # ``--instance-config`` with a partial ``components`` map).
        return upstream_arrays_shared
    if not upstream_arrays_by_instance:
        return None
    n_up = len(upstream_arrays_by_instance)
    if _matched_cardinality(n_up, downstream_inst_count):
        return upstream_arrays_by_instance[downstream_inst_idx]
    if uniform_fanout_cache is not None:
        cached = uniform_fanout_cache.get(upstream_name)
        if cached is not None:
            return cached
    # Uniform fan-out: average across upstream pods. Each downstream
    # pod sees the same averaged view, so per-pod variation under this
    # branch only emerges from local saturation noise / coupling math
    # rather than from upstream asymmetry. The upstream instances
    # share the same metric-key set because they came from the same
    # MetricSpec list in ``generate_component``.
    averaged: dict[str, np.ndarray] = {}
    metric_keys = set()
    for entry in upstream_arrays_by_instance:
        metric_keys.update(entry.keys())
    for metric in metric_keys:
        arrays = [
            entry[metric] for entry in upstream_arrays_by_instance
            if metric in entry
        ]
        if not arrays:
            continue
        # Incremental sum-then-divide. Equal-weight mean as ``np.mean``
        # over the stacked array, but at O(n_rows) extra memory instead
        # of O(N_up × n_rows) — the ``np.stack`` allocation can become
        # multi-MB per metric for large ``N_up`` and 7-day runs.
        acc = arrays[0].astype(np.float64, copy=True)
        for arr in arrays[1:]:
            acc += arr
        acc /= len(arrays)
        averaged[metric] = acc
    if uniform_fanout_cache is not None:
        uniform_fanout_cache[upstream_name] = averaged
    return averaged


def _compute_topology_arrays_per_instance(
    component_name: str,
    specs: list[MetricSpec],
    upstream_arrays_shared: dict[str, dict[str, np.ndarray]],
    upstream_arrays_by_instance: dict[str, list[dict[str, np.ndarray]]],
    instances: list["Instance"],
    rng: "np.random.RandomState",
    n_rows: int,
) -> tuple[
    list[dict[str, np.ndarray]],
    list[dict[str, tuple[np.ndarray | None, np.ndarray | None]]],
]:
    """Compute per-instance coupling and saturation arrays for ``component_name``.

    Returns ``(coupling_by_instance, saturation_by_instance)``:

    * ``coupling_by_instance[K][metric_name]`` is the per-row coupled
      baseline array for downstream instance ``K``'s coupled load
      metrics (replaces the natural baseline in ``_natural_column``
      via ``baseline_override``). Absent metrics fall back to the
      natural draw.
    * ``saturation_by_instance[K][metric_name]`` is the
      ``(latency_factor, error_offset)`` tuple applied to instance
      ``K``'s saturation-target metrics (composes with
      ``MetricSpec.multiplier`` / ``MetricSpec.additive`` via the
      ``_natural_column`` kwargs).

    Divergence detection (which instances diverge from instance 0)
    is intentionally not returned. ``generate_component`` re-derives
    it directly from the passed arrays via ``_arrays_equal_dict`` /
    ``_sat_tuples_equal_dict`` so correctness does not depend on a
    caller-supplied hint that could drift from the actual array
    contents.

    Shared ``rng.normal`` noise for callable+constant coupling is
    drawn once and reused across all instances so the
    ``_TOPOLOGY_COUPLE_NOISE_STD`` floor sits at the same magnitude
    today's shared draw produces under symmetric upstream — that
    keeps per-instance arrays under symmetric upstream byte-identical
    to the shared array a single ``_compose_topology_coupled_specs``
    call would produce.
    """
    n_inst = len(instances)
    coupling_by_instance: list[dict[str, np.ndarray]] = [{} for _ in range(n_inst)]
    saturation_by_instance: list[
        dict[str, tuple[np.ndarray | None, np.ndarray | None]]
    ] = [{} for _ in range(n_inst)]

    coupled_entry = _TOPOLOGY_LOAD_METRICS.get(component_name)
    sat_targets = _TOPOLOGY_SATURATION_TARGETS.get(component_name)

    # Determine which downstream metrics need either coupling or
    # saturation arrays.
    coupled_metric_names: tuple[str, ...] = ()
    canonical_down: str | None = None
    if coupled_entry is not None:
        canonical_down = coupled_entry[0]
        coupled_metric_names = (canonical_down, *coupled_entry[1])
    latency_metrics: tuple[str, ...] = ()
    error_metrics: tuple[str, ...] = ()
    if sat_targets is not None:
        latency_metrics, error_metrics = sat_targets

    name_to_idx = {s.name: i for i, s in enumerate(specs)}

    # Collect incoming edges once. Each entry is (upstream_name, Edge).
    # Filter to upstreams that actually have captured load arrays —
    # mirrors ``_compose_topology_coupled_specs``'s
    # ``if upstream not in upstream_arrays: continue`` guard so a
    # ``--components`` subset that drops an upstream (or a
    # ``--metrics-per-component`` trim that removes the canonical load
    # column) degrades gracefully *and* keeps the RNG draw schedule
    # aligned with the legacy path: ``shared_coupling_noise`` below
    # advances ``rng`` only when at least one upstream is actually
    # contributing, exactly as the lambda-baked composer does.
    incoming: list[tuple[str, Edge]] = []
    for upstream, edges in TOPOLOGY.items():
        if (
            upstream not in upstream_arrays_shared
            and upstream not in upstream_arrays_by_instance
        ):
            continue
        for edge in edges:
            if edge.target == component_name:
                incoming.append((upstream, edge))
    if not incoming:
        return coupling_by_instance, saturation_by_instance

    # Shared callable+constant noise per coupled metric — drawn lazily
    # the *first* time a metric produces an active contribution, then
    # cached across instances so symmetric upstream stays byte-identical
    # to today's shared draw. Lazy initialization (instead of an upfront
    # pre-draw over ``coupled_metric_names``) matches
    # ``_compose_topology_coupled_specs``'s RNG schedule: that legacy
    # path draws noise inside the active branch only, so a coupled
    # metric whose contributions all get skipped (e.g.
    # ``--metrics-per-component`` trimmed the canonical upstream
    # column, or every callable ``signal`` returned ``None``) consumes
    # zero RNG draws there. Pre-drawing here would have advanced
    # ``rng`` for those skipped metrics, shifting every subsequent
    # downstream's draws.
    shared_coupling_noise: dict[str, np.ndarray] = {}

    # Compute per-instance arrays.
    # Cache shared across downstream instances: under mismatched
    # cardinality, ``_per_instance_upstream_view`` averages every
    # upstream pod into a single dict that is identical for every
    # downstream pod. Without the cache the same incremental
    # sum-then-divide averaging runs N_down times per upstream
    # (O(N_down * N_up * n_rows)); with the cache it runs once
    # (O(N_up * n_rows)).
    uniform_fanout_cache: dict[str, dict[str, np.ndarray]] = {}
    for inst_idx in range(n_inst):
        # Build the per-instance upstream view dict keyed by upstream name.
        per_instance_upstream_cols: dict[str, dict[str, np.ndarray]] = {}
        for upstream, _edge in incoming:
            per_instance_upstream_cols[upstream] = (
                _per_instance_upstream_view(
                    upstream,
                    upstream_arrays_by_instance.get(upstream),
                    upstream_arrays_shared.get(upstream),
                    n_inst,
                    inst_idx,
                    uniform_fanout_cache=uniform_fanout_cache,
                )
                or {}
            )

        # ------------------------------------------------------------
        # Coupling arrays (one per coupled metric on this component).
        #
        # Callable-weight contributions are computed once per instance
        # (``edge.signal`` / ``edge.weight`` are metric-invariant) and
        # applied only to the canonical load metric — the weight
        # callable returns canonical-metric units, so a supplementary
        # metric with a different base must not receive it. Mirrors the
        # shared-path rule in ``_compose_topology_coupled_specs``.
        # ------------------------------------------------------------
        callable_active = False
        callable_contrib = np.zeros(n_rows, dtype=np.float64)
        for upstream, edge in incoming:
            if not callable(edge.weight):
                continue
            if edge.signal is None:
                continue
            ups_cols = per_instance_upstream_cols.get(upstream, {})
            signal = edge.signal(ups_cols)
            if signal is None:
                continue
            callable_contrib = callable_contrib + np.asarray(
                edge.weight(signal), dtype=np.float64
            )
            callable_active = True

        for metric_name in coupled_metric_names:
            if metric_name not in name_to_idx:
                continue
            original = specs[name_to_idx[metric_name]]
            downstream_base = float(original.base)
            if downstream_base <= 0:
                continue
            metric_callable_active = (
                callable_active and metric_name == canonical_down
            )

            # First: active constant-weight edges for normalization.
            active_constant: list[tuple[np.ndarray, float, float]] = []
            for upstream, edge in incoming:
                if callable(edge.weight):
                    continue
                if isinstance(edge.weight, bool) or not isinstance(
                    edge.weight, (int, float)
                ):
                    continue
                w = float(edge.weight)
                if w == 0.0:
                    continue
                ups_cols = per_instance_upstream_cols.get(upstream, {})
                ups_entry = _TOPOLOGY_LOAD_METRICS.get(upstream)
                if ups_entry is None:
                    continue
                ups_canonical, _ = ups_entry
                if ups_canonical and ups_canonical in ups_cols:
                    ups_base = _component_metric_base(upstream, ups_canonical)
                    if ups_base > 0:
                        active_constant.append(
                            (ups_cols[ups_canonical], ups_base, w)
                        )

            if not active_constant and not metric_callable_active:
                continue

            constant_contrib = np.zeros(n_rows, dtype=np.float64)
            if active_constant:
                sum_w = sum(w for _, _, w in active_constant)
                for ups_arr, ups_base, w in active_constant:
                    w_norm = w / sum_w
                    constant_contrib = constant_contrib + (
                        ups_arr / ups_base * downstream_base * w_norm
                    )

            # Lazy noise draw: only after we know this metric has an
            # active contribution. ``setdefault`` keeps the noise
            # shared across instances — instance 0 (first iteration)
            # draws, later instances reuse the cached array — so
            # symmetric upstream still produces byte-identical
            # coupling arrays across pods.
            noise = shared_coupling_noise.get(metric_name)
            if noise is None:
                noise = rng.normal(
                    0.0, _TOPOLOGY_COUPLE_NOISE_STD, n_rows
                )
                shared_coupling_noise[metric_name] = noise
            coupling_by_instance[inst_idx][metric_name] = (
                constant_contrib
                + (callable_contrib if metric_callable_active else 0.0)
                + noise
            )

        # ------------------------------------------------------------
        # Saturation arrays.
        # ------------------------------------------------------------
        if sat_targets is None:
            continue
        latency_factor = np.ones(n_rows, dtype=np.float64)
        error_offset = np.zeros(n_rows, dtype=np.float64)
        any_active = False
        for upstream, edge in incoming:
            if edge.saturation is None:
                continue
            sat = edge.saturation
            if sat.latency_gain == 0.0 and sat.error_gain == 0.0:
                continue
            ups_cols = per_instance_upstream_cols.get(upstream, {})
            ups_entry = _TOPOLOGY_LOAD_METRICS.get(upstream)
            if ups_entry is None:
                continue
            ups_canonical, _ups_supplementary = ups_entry
            # Canonical-only driver: ``sat.midpoint`` is tuned in the
            # upstream's canonical load-metric units, so a supplementary
            # column (different units — e.g. cacheservice's
            # ``cache_misses``) must never drive the logistic. When the
            # canonical column is absent (``--metrics-per-component``
            # trim) the edge is skipped, matching the constant-weight
            # coupling path's posture.
            driver = ups_cols.get(ups_canonical)
            if driver is None or driver.shape[0] != n_rows:
                continue
            lat_mult, err_off = _apply_saturation(driver, sat)
            latency_factor *= lat_mult
            error_offset += err_off
            any_active = True
        if not any_active:
            continue
        # Latency targets receive ONLY the multiplicative
        # ``latency_factor`` (mirrors today's
        # ``_compose_topology_saturation_specs`` wrapping
        # ``MetricSpec.multiplier``); error targets receive ONLY the
        # additive ``error_offset`` (mirrors wrapping
        # ``MetricSpec.additive``). A metric appearing in both lists
        # — rare; only triggered by future overlapping targets — gets
        # both effects applied.
        for metric_name in latency_metrics:
            saturation_by_instance[inst_idx][metric_name] = (
                latency_factor, None
            )
        for metric_name in error_metrics:
            existing = saturation_by_instance[inst_idx].get(metric_name)
            if existing is not None:
                lf_old, _ = existing
                saturation_by_instance[inst_idx][metric_name] = (
                    lf_old, error_offset
                )
            else:
                saturation_by_instance[inst_idx][metric_name] = (
                    None, error_offset
                )

    return coupling_by_instance, saturation_by_instance


def _arrays_equal_dict(
    a: dict[str, np.ndarray], b: dict[str, np.ndarray],
) -> bool:
    """Byte-comparison of two ``dict[str, np.ndarray]`` entries.

    Used by ``generate_component`` to detect whether the
    per-instance topology arrays returned by
    ``_compute_topology_arrays_per_instance`` diverge from instance
    0. Equality is element-wise via ``np.array_equal`` with its
    default ``equal_nan=False`` — two byte-identical arrays that
    contain NaN therefore compare *unequal* and force the divergent
    per-instance path. That is fail-safe (the divergent path still
    produces correct, identical output with an unchanged RNG schedule
    since coupling noise is pre-drawn and shared; only memory is
    wasted on redundant per-instance buffers), and NaN never reaches
    these arrays from the catalog generators today.
    """
    if a.keys() != b.keys():
        return False
    for key, arr in a.items():
        if not np.array_equal(arr, b[key]):
            return False
    return True


def _sat_tuples_equal_dict(
    a: dict[str, tuple["np.ndarray | None", "np.ndarray | None"]],
    b: dict[str, tuple["np.ndarray | None", "np.ndarray | None"]],
) -> bool:
    """Byte-comparison of two saturation-tuple dicts.

    Mirrors ``_arrays_equal_dict`` but unpacks the
    ``(latency_factor, error_offset)`` pair from each entry. Either
    side of the tuple may be ``None`` — saturation populates only
    one side per metric depending on whether the metric is a
    latency target or an error target.
    """
    if a.keys() != b.keys():
        return False
    for key, (lf_a, eo_a) in a.items():
        lf_b, eo_b = b[key]
        if (lf_a is None) != (lf_b is None):
            return False
        if lf_a is not None and not np.array_equal(lf_a, lf_b):
            return False
        if (eo_a is None) != (eo_b is None):
            return False
        if eo_a is not None and not np.array_equal(eo_a, eo_b):
            return False
    return True


# ------------------------------------------------------------------
# Anomaly specifications — migrated to SCENARIOS registry.
# All anomaly and cascade specs now live in the SCENARIOS dict below.
# ------------------------------------------------------------------
# (legacy anoms_* lists and COMPONENT_PRIMARY_ANOMALIES removed)


# ------------------------------------------------------------------
# Named scenario registry (full migration complete).
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
                "duration_seconds": 15*60,
                "shape": "sustained",
                "metric": "error_rate",
                "description": "Sustained failed-login burst — possible brute force",
                "generator": lambda ts, idx: 0.42,
            }),
            ("authservice", {
                "time_offset": 2*3600 + 15*60,
                "duration_seconds": 15*60,
                "shape": "sustained",
                "metric": "login_attempts",
                "description": "Login attempts surge 5× for 15 min",
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
                "duration_seconds": 20*60,
                "shape": "sustained",
                "metric": "cache_misses",
                "description": "Cache miss collapse — misses hold near 95,000 for 20 min, hit ratio ~5%",
                "generator": lambda ts, idx: 95000.0,
            }),
            ("cacheservice", {
                "time_offset": 17*3600,
                "duration_seconds": 30*60,
                "shape": "sustained",
                "metric": "memory_util_pct",
                "description": "Memory pressure plateau — 97% nearing eviction for 30 min",
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
                "generator": lambda ts, idx, rng: 2400 + rng.normal(0, 150),
                "severity": DEFAULT_SEVERITY,
            }),
            ("database", {
                "time_offset": 6*3600 + 30,
                "metric": "queries_per_sec",
                "description": "Cascading: Cache misses increase database queries",
                "generator": lambda ts, idx, rng: 38000 + rng.normal(0, 3000),
                "severity": DEFAULT_SEVERITY,
            }),
            ("database", {
                "time_offset": 6*3600 + 45,
                "metric": "read_latency_ms",
                "description": "Cascading: Database read latency increases from cache misses",
                "generator": lambda ts, idx, rng: 45 + rng.normal(0, 5),
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
                "duration_seconds": 10*60,
                "shape": "sustained",
                "metric": "cpu_util_pct",
                "description": "CPU saturates at 100% for 10 min",
                "generator": lambda ts, idx: 100.0,
            }),
            ("apigateway", {
                "time_offset": 21*3600 + 45*60,
                "duration_seconds": 5*60,
                "shape": "sustained",
                "metric": "error_rate",
                "description": "5xx burst from bad config push — 25% for 5 min",
                "generator": lambda ts, idx: 0.25,
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
                "duration_seconds": 20*60,
                "shape": "sustained",
                "metric": "read_latency_ms",
                "description": "Read latency stall — 360 ms for 20 min",
                "generator": lambda ts, idx: 360.0,
            }),
            ("database", {
                "time_offset": 11*3600,
                "duration_seconds": 20*60,
                "shape": "sustained",
                "metric": "error_rate",
                "description": "Backend errors rise to 35% for 20 min",
                "generator": lambda ts, idx: 0.35,
            }),
            ("database", {
                "time_offset": 4*3600,
                "duration_seconds": 30*60,
                "shape": "sustained",
                "metric": "connections",
                "description": "Backup-window connection pile-up — 6,800 connections for 30 min",
                "generator": lambda ts, idx: 6800,
            }),
            ("database", {
                "time_offset": 4*3600,
                "duration_seconds": 30*60,
                "shape": "sustained",
                "metric": "write_latency_ms",
                "description": "Backup I/O contention — writes hold near 45 ms for 30 min",
                "generator": lambda ts, idx: 45.0,
            }),
            ("database", {
                "time_offset": 23*3600,
                "duration_seconds": 20*60,
                "shape": "sustained",
                "metric": "queries_per_sec",
                "description": "Nightly batch kickoff — 55k QPS for 20 min",
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
                "generator": lambda ts, idx, rng: 850 + rng.normal(0, 50),
                "severity": DEFAULT_SEVERITY,
            }),
            ("apigateway", {
                "time_offset": 11*3600 + 5,
                "metric": "error_rate",
                "description": "Cascading: Database errors propagate to API (~30%)",
                "generator": lambda ts, idx: 0.30,
                "severity": DEFAULT_SEVERITY,
            }),
            ("authservice", {
                "time_offset": 11*3600 + 10,
                "metric": "avg_auth_latency_ms",
                "description": "Cascading: Database issues slow auth queries",
                "generator": lambda ts, idx, rng: 420 + rng.normal(0, 30),
                "severity": DEFAULT_SEVERITY,
            }),
            ("mqservice", {
                "time_offset": 11*3600 + 20,
                "metric": "pending_messages",
                "description": "Cascading: DB stall causes MQ backpressure",
                "generator": lambda ts, idx, rng: 250000 + rng.normal(0, 5000),
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
                "duration_seconds": 20*60,
                "shape": "sustained",
                "metric": "pending_messages",
                "description": "Pending messages jam to 1M for 20 min",
                "generator": lambda ts, idx: 1_000_000,
            }),
            ("mqservice", {
                "time_offset": 14*3600 + 30*60,
                "duration_seconds": 20*60,
                "shape": "sustained",
                "metric": "error_rate",
                "description": "Error rate holds at 25% during queue jam",
                "generator": lambda ts, idx: 0.25,
            }),
            ("mqservice", {
                "time_offset": 12*3600 + 30*60,
                "duration_seconds": 15*60,
                "shape": "sustained",
                "metric": "dead_letter_queue",
                "description": "DLQ blow-up — 1,200 messages parked for 15 min",
                "generator": lambda ts, idx: 1200,
            }),
        ),
        cascade_specs=(
            ("apigateway", {
                "time_offset": 14*3600 + 30*60 + 60,
                "metric": "avg_response_time_ms",
                "description": "Cascading: MQ backlog delays API responses",
                "generator": lambda ts, idx, rng: 650 + rng.normal(0, 40),
                "severity": DEFAULT_SEVERITY,
            }),
            ("database", {
                "time_offset": 14*3600 + 30*60 + 90,
                "metric": "connections",
                "description": "Cascading: MQ issues cause connection buildup",
                "generator": lambda ts, idx, rng: 8500 + rng.normal(0, 500),
                "severity": DEFAULT_SEVERITY,
            }),
            ("database", {
                "time_offset": 14*3600 + 30*60 + 95,
                "metric": "write_latency_ms",
                "description": "Cascading: MQ backpressure increases write latency",
                "generator": lambda ts, idx, rng: 85 + rng.normal(0, 10),
                "severity": DEFAULT_SEVERITY,
            }),
            ("authservice", {
                "time_offset": 14*3600 + 32*60 + 30,
                "metric": "avg_auth_latency_ms",
                "description": "Cascading: MQ jam delays session writes",
                "generator": lambda ts, idx, rng: 280 + rng.normal(0, 15),
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
                "duration_seconds": 5*60,
                "shape": "sustained",
                "metric": "tls_handshake_errors",
                "description": "TLS handshake errors sustain at 80/s for 5 min (cert near-expiry warning)",
                "generator": lambda ts, idx: 80.0,
            }),
            ("loadbalancer", {
                "time_offset": 8*3600 + 15*60,
                "duration_seconds": 10*60,
                "shape": "sustained",
                "metric": "healthcheck_failures",
                "description": "Healthcheck failures hold at 12 for 10 min (backend pool flapping)",
                "generator": lambda ts, idx: 12.0,
            }),
            ("loadbalancer", {
                "time_offset": 13*3600,
                "duration_seconds": 5*60,
                "shape": "sustained",
                "metric": "connection_resets",
                "description": "Connection resets sustain at 450 for 5 min (SYN flood-style burst)",
                "generator": lambda ts, idx: 450.0,
            }),
            ("loadbalancer", {
                "time_offset": 20*3600 + 30*60,
                "duration_seconds": 8*60,
                "shape": "sustained",
                "metric": "backend_5xx_per_sec",
                "description": "Backend 5xx hold near 75/s for 8 min (region failover cascades 5xx upstream)",
                "generator": lambda ts, idx: 75.0,
            }),
        ),
        cascade_specs=(
            ("apigateway", {
                "time_offset": 8*3600 + 15*60 + 5,
                "metric": "active_connections",
                "description": "Cascading: LB withdraws traffic from a flapping backend pool",
                "generator": lambda ts, idx, rng: 200 + rng.normal(0, 25),
                "severity": DEFAULT_SEVERITY,
            }),
            ("apigateway", {
                "time_offset": 20*3600 + 30*60 + 10,
                "metric": "error_rate",
                "description": "Cascading: LB region failover propagates 5xx to gateway (~30%)",
                "generator": lambda ts, idx: 0.30,
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
                "duration_seconds": 12*60,
                "shape": "sustained",
                "metric": "5xx_rate",
                "description": "Object store 5xx rate holds at 18% for 12 min (upstream provider 5xx wave)",
                "generator": lambda ts, idx: 0.18,
            }),
            ("objectstore", {
                "time_offset": 12*3600,
                "duration_seconds": 30*60,
                "shape": "sustained",
                "metric": "bandwidth_mbps",
                "description": "Bandwidth saturates at 950 Mbps for 30 min (batch export)",
                "generator": lambda ts, idx: 950.0,
            }),
            ("objectstore", {
                "time_offset": 18*3600 + 30*60,
                "duration_seconds": 15*60,
                "shape": "sustained",
                "metric": "get_latency_ms",
                "description": "GET latency tail holds at 380 ms for 15 min (read-after-write)",
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
                "duration_seconds": 30*60,
                "shape": "sustained",
                "metric": "ann_query_latency_ms",
                "description": "ANN query latency stalls at 280 ms for 30 min (index rebuild)",
                "generator": lambda ts, idx: 280.0,
            }),
            ("vectorstore", {
                "time_offset": 15*3600,
                "duration_seconds": 60*60,
                "shape": "sustained",
                "metric": "recall_at_10",
                "description": "Recall@10 degrades to 0.62 for 1h after model swap",
                "generator": lambda ts, idx: 0.62,
            }),
        ),
        cascade_specs=(
            ("llm_analytics", {
                "time_offset": 10*3600 + 30*60 + 15,
                "metric": "avg_llm_latency_ms",
                "description": "Cascading: slow ANN retrieval drags LLM latency to 1,900 ms",
                "generator": lambda ts, idx, rng: 1900 + rng.normal(0, 80),
                "severity": DEFAULT_SEVERITY,
            }),
            ("llm_analytics", {
                "time_offset": 15*3600 + 30,
                "metric": "llm_api_error_rate",
                "description": "Cascading: low-recall results trigger LLM fallback retries (15% errors)",
                "generator": lambda ts, idx: 0.15,
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
                "duration_seconds": 20*60,
                "shape": "sustained",
                "metric": "avg_job_duration_s",
                "description": "Job overrun — duration 4× baseline for 20 min blocks next window",
                "generator": lambda ts, idx: 480.0,
            }),
            ("scheduler", {
                "time_offset": 8*3600 + 5*60,
                "duration_seconds": 30*60,
                "shape": "sustained",
                "metric": "missed_schedules",
                "description": "Missed schedule chain — 12 windows skipped after overrun",
                "generator": lambda ts, idx: 12.0,
            }),
            ("scheduler", {
                "time_offset": 10*3600,
                "duration_seconds": 45*60,
                "shape": "sustained",
                "metric": "jobs_queued",
                "description": "Job queue overflow — 2,500 jobs backlog for 45 min",
                "generator": lambda ts, idx: 2500.0,
            }),
        ),
        cascade_specs=(
            ("database", {
                "time_offset": 10*3600 + 30,
                "metric": "connections",
                "description": "Cascading: Scheduler queue overflow drives DB connection buildup",
                "generator": lambda ts, idx, rng: 7800 + rng.normal(0, 400),
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
                "duration_seconds": 12*60,
                "shape": "sustained",
                "metric": "provider_5xx_rate",
                "description": "Stripe-style provider 5xx surge — 18% error rate for 12 min",
                "generator": lambda ts, idx: 0.18,
            }),
            ("paymentservice", {
                "time_offset": 13*3600 + 30*60,
                "duration_seconds": 30*60,
                "shape": "sustained",
                "metric": "webhook_delivery_lag_s",
                "description": "Webhook delivery 5 min behind for 30 min — provider backlog",
                "generator": lambda ts, idx: 300.0,
            }),
            ("paymentservice", {
                "time_offset": 15*3600,
                "duration_seconds": 45*60,
                "shape": "sustained",
                "metric": "auth_decline_rate",
                "description": "Decline-rate holds at 35% for 45 min — fraud rule misfire",
                "generator": lambda ts, idx: 0.35,
            }),
        ),
        cascade_specs=(
            ("apigateway", {
                "time_offset": 12*3600 + 12,
                "metric": "error_rate",
                "description": "Cascading: Payment provider 5xx propagates to gateway (~28%)",
                "generator": lambda ts, idx: 0.28,
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
                "duration_seconds": 20*60,
                "shape": "sustained",
                "metric": "jwks_fetch_latency_ms",
                "description": "JWKS cache miss storm — fetch latency 1500 ms for 20 min at key rotation",
                "generator": lambda ts, idx: 1500.0,
            }),
            ("identityprovider", {
                "time_offset": 4*3600,
                "duration_seconds": 20*60,
                "shape": "sustained",
                "metric": "key_rotation_events",
                "description": "Concurrent key rotation events sustain the cache miss storm",
                "generator": lambda ts, idx: 50.0,
            }),
            ("identityprovider", {
                "time_offset": 16*3600 + 30*60,
                "duration_seconds": 30*60,
                "shape": "sustained",
                "metric": "mfa_challenges_per_min",
                "description": "MFA SMS provider degradation — challenges drop to 0 for 30 min",
                "generator": lambda ts, idx: 0.0,
            }),
            ("identityprovider", {
                "time_offset": 19*3600,
                "duration_seconds": 15*60,
                "shape": "sustained",
                "metric": "failed_oidc_flows",
                "description": "SAML parse error burst — 120 failed flows for 15 min from upstream IdP",
                "generator": lambda ts, idx: 120.0,
            }),
        ),
        cascade_specs=(
            ("authservice", {
                "time_offset": 4*3600 + 25,
                "metric": "login_success_rate",
                "description": "Cascading: JWKS fetch storm degrades auth verification — success ~45%",
                "generator": lambda ts, idx, rng: 45 + rng.normal(0, 2),
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
                "duration_seconds": 30*60,
                "shape": "sustained",
                "metric": "ingest_lag_s",
                "description": "Ingestion lag holds near 240s for 30 min — pipeline can't keep up",
                "generator": lambda ts, idx: 240.0,
            }),
            ("observabilitypipeline", {
                "time_offset": 13*3600,
                "duration_seconds": 20*60,
                "shape": "sustained",
                "metric": "dropped_metrics_per_sec",
                "description": "High-cardinality push drops 8,500 metrics/s for 20 min",
                "generator": lambda ts, idx: 8500.0,
            }),
            ("observabilitypipeline", {
                "time_offset": 13*3600,
                "duration_seconds": 20*60,
                "shape": "sustained",
                "metric": "metrics_ingested_per_sec",
                "description": "Ingest rate collapses to 12,000/s for 20 min during cardinality storm",
                "generator": lambda ts, idx: 12000.0,
            }),
            ("observabilitypipeline", {
                "time_offset": 20*3600,
                "duration_seconds": 20*60,
                "shape": "sustained",
                "metric": "pipeline_error_rate",
                "description": "Pipeline error rate holds at 8% for 20 min — downstream dashboards go stale",
                "generator": lambda ts, idx: 0.08,
            }),
        ),
        cascade_specs=(
            ("mqservice", {
                "time_offset": 9*3600 + 20,
                "metric": "pending_messages",
                "description": "Cascading: Telemetry pipeline lag backs up downstream queue",
                "generator": lambda ts, idx, rng: 220000 + rng.normal(0, 15000),
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
                "duration_seconds": 60*60,
                "shape": "sustained",
                "metric": "login_attempts",
                "description": "Benign baseline shift: Monday morning login burst — 1,400 attempts/s for 1h",
                "generator": lambda ts, idx: 1400,
                "severity": "low",
            }),
            ("apigateway", {
                "time_offset": 9*3600,
                "duration_seconds": 60*60,
                "shape": "sustained",
                "metric": "requests_per_sec",
                "description": "Monday-morning thundering herd — 2,200 RPS for 1h",
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
                "duration_seconds": 12*60,
                "shape": "sustained",
                "metric": "llm_requests_per_sec",
                "description": "Viral surge: Customer demo goes viral, 8× request spike for 12 min",
                "generator": lambda ts, idx: 360,
            }),
            ("llm_analytics", {
                "time_offset": 1*SECONDS_PER_DAY + 10*3600 + 15*60,
                "duration_seconds": 12*60,
                "shape": "sustained",
                "metric": "input_tokens_per_sec",
                "description": "Token surge from viral traffic for 12 min",
                "generator": lambda ts, idx: 185000,
            }),
            ("llm_analytics", {
                "time_offset": 1*SECONDS_PER_DAY + 10*3600 + 15*60,
                "duration_seconds": 12*60,
                "shape": "sustained",
                "metric": "output_tokens_per_sec",
                "description": "Output token surge from viral traffic for 12 min",
                "generator": lambda ts, idx: 62000,
            }),
        ),
        cascade_specs=(
            ("database", {
                "time_offset": 1*SECONDS_PER_DAY + 10*3600 + 15*60 + 30,
                "metric": "queries_per_sec",
                "description": "Cascading: LLM surge increases database queries for context retrieval",
                "generator": lambda ts, idx, rng: 48000 + rng.normal(0, 4000),
                "severity": DEFAULT_SEVERITY,
            }),
            ("database", {
                "time_offset": 1*SECONDS_PER_DAY + 10*3600 + 15*60 + 45,
                "metric": "connections",
                "description": "Cascading: LLM service creates more database connections",
                "generator": lambda ts, idx, rng: 7200 + rng.normal(0, 400),
                "severity": DEFAULT_SEVERITY,
            }),
            ("cacheservice", {
                "time_offset": 1*SECONDS_PER_DAY + 10*3600 + 15*60 + 20,
                "metric": "cache_misses",
                "description": "Cascading: LLM context cache misses spike",
                "generator": lambda ts, idx, rng: 1800 + rng.normal(0, 150),
                "severity": DEFAULT_SEVERITY,
            }),
            ("apigateway", {
                "time_offset": 1*SECONDS_PER_DAY + 10*3600 + 15*60 + 10,
                "metric": "requests_per_sec",
                "description": "Cascading: LLM viral traffic increases API gateway load",
                "generator": lambda ts, idx, rng: 2400 + rng.normal(0, 200),
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
                "duration_seconds": 6*3600,
                "shape": "sustained",
                "metric": "llm_requests_per_sec",
                "description": "Enterprise onboarding: Major customer launches AI features",
                "generator": _correlated_span_generator(
                    150.0, 285.0, 6*3600, noise=6.0,
                    lo=120.0, hi=320.0, phase=0.75,
                ),
            }),
            ("llm_analytics", {
                "time_offset": 2*SECONDS_PER_DAY + 14*3600,
                "duration_seconds": 6*3600,
                "shape": "sustained",
                "metric": "avg_context_window_size",
                "description": "Enterprise using large context windows for analytics",
                "generator": _correlated_span_generator(
                    6500.0, 12500.0, 6*3600, noise=220.0,
                    lo=5600.0, hi=13200.0, phase=0.75,
                ),
            }),
            ("llm_analytics", {
                "time_offset": 2*SECONDS_PER_DAY + 14*3600,
                "duration_seconds": 6*3600,
                "shape": "sustained",
                "metric": "token_limit_hits_per_min",
                "description": "Token limits hit frequently during enterprise rollout",
                "generator": _correlated_span_generator(
                    10.0, 45.0, 6*3600, noise=1.2,
                    lo=6.0, hi=52.0, phase=0.75,
                ),
            }),
            ("llm_analytics", {
                "time_offset": 2*SECONDS_PER_DAY + 14*3600,
                "duration_seconds": 6*3600,
                "shape": "sustained",
                "metric": "avg_llm_latency_ms",
                "description": "Enterprise onboarding correlated LLM latency climb",
                "generator": _correlated_span_generator(
                    980.0, 2300.0, 6*3600, noise=55.0,
                    lo=850.0, hi=2500.0, phase=0.75,
                ),
            }),
            ("vectorstore", {
                "time_offset": 2*SECONDS_PER_DAY + 14*3600,
                "duration_seconds": 6*3600,
                "shape": "sustained",
                "metric": "embeddings_per_sec",
                "description": "Enterprise onboarding drives embeddings to 350/s",
                "generator": _correlated_span_generator(
                    180.0, 350.0, 6*3600, noise=9.0,
                    lo=140.0, hi=390.0, phase=0.75,
                ),
            }),
        ),
        cascade_specs=(
            ("database", {
                "time_offset": 2*SECONDS_PER_DAY + 14*3600 + 60,
                "metric": "read_latency_ms",
                "description": "Cascading: Large LLM context windows cause slow DB reads",
                "generator": lambda ts, idx, rng: 85 + rng.normal(0, 8),
                "severity": DEFAULT_SEVERITY,
            }),
            ("cacheservice", {
                "time_offset": 2*SECONDS_PER_DAY + 14*3600 + 35,
                "metric": "memory_util_pct",
                "description": "Cascading: LLM context caching increases memory pressure",
                "generator": lambda ts, idx, rng: 92 + rng.normal(0, 3),
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
                "duration_seconds": 90*60,
                "shape": "sustained",
                "metric": "llm_api_error_rate",
                "description": "LLM provider rate limits hit, 18% error rate",
                "generator": _correlated_span_generator(
                    0.04, 0.18, 90*60, noise=0.006,
                    lo=0.02, hi=0.22, phase=1.05,
                ),
            }),
            ("llm_analytics", {
                "time_offset": 4*SECONDS_PER_DAY + 9*3600 + 30*60,
                "duration_seconds": 90*60,
                "shape": "sustained",
                "metric": "avg_llm_latency_ms",
                "description": "LLM latency spikes due to rate limiting",
                "generator": _correlated_span_generator(
                    1200.0, 4200.0, 90*60, noise=95.0,
                    lo=950.0, hi=4600.0, phase=1.05,
                ),
            }),
            ("apigateway", {
                "time_offset": 4*SECONDS_PER_DAY + 9*3600 + 31*60,
                "duration_seconds": 90*60,
                "shape": "sustained",
                "metric": "error_rate",
                "description": "LLM rate limiting correlated gateway error plateau",
                "generator": _correlated_span_generator(
                    0.06, 0.22, 90*60, noise=0.006,
                    lo=0.03, hi=0.26, phase=1.05,
                ),
            }),
        ),
        cascade_specs=(
            ("apigateway", {
                "time_offset": 4*SECONDS_PER_DAY + 9*3600 + 30*60 + 8,
                "metric": "error_rate",
                "description": "Cascading: LLM API errors propagate to gateway",
                "generator": lambda ts, idx: 0.28,
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
                "duration_seconds": 4*3600,
                "shape": "sustained",
                "metric": "input_tokens_per_sec",
                "description": "Weekend batch analytics job processing historical data",
                "generator": _correlated_span_generator(
                    145000.0, 320000.0, 4*3600, noise=7000.0,
                    lo=110000.0, hi=360000.0, phase=1.35,
                ),
            }),
            ("llm_analytics", {
                "time_offset": 5*SECONDS_PER_DAY + 2*3600,
                "duration_seconds": 4*3600,
                "shape": "sustained",
                "metric": "context_overflow_rate",
                "description": "Context overflow from large batch documents",
                # Phase 9 re-tune: the ratio is declared in [0, 1]
                # (max_value=1), so the span saturates toward 0.97
                # instead of the pre-retune 8.5 that violated the bound
                # on every default 7-day --validate-output run. The
                # natural baseline is 0.3 +/- 0.1, so the 0.62 -> 0.97
                # stress ramp stays 3.2-6.7 sigma above it — still
                # unmistakably the context-window saturation pattern,
                # now physically plausible for a ratio.
                "generator": _correlated_span_generator(
                    0.62, 0.97, 4*3600, noise=0.015,
                    lo=0.55, hi=0.995, phase=1.35,
                ),
            }),
            ("llm_analytics", {
                "time_offset": 5*SECONDS_PER_DAY + 2*3600,
                "duration_seconds": 4*3600,
                "shape": "sustained",
                "metric": "avg_llm_latency_ms",
                "description": "Weekend batch correlated LLM latency climb",
                "generator": _correlated_span_generator(
                    1100.0, 3400.0, 4*3600, noise=85.0,
                    lo=850.0, hi=3800.0, phase=1.35,
                ),
            }),
            ("objectstore", {
                "time_offset": 5*SECONDS_PER_DAY + 2*3600,
                "duration_seconds": 4*3600,
                "shape": "sustained",
                "metric": "bandwidth_mbps",
                "description": "Weekend batch export saturates object store at 1400 Mbps",
                "generator": _correlated_span_generator(
                    650.0, 1400.0, 4*3600, noise=35.0,
                    lo=500.0, hi=1550.0, phase=1.35,
                ),
            }),
            ("database", {
                "time_offset": 5*SECONDS_PER_DAY + 2*3600 + 180,
                "duration_seconds": 4*3600,
                "shape": "sustained",
                "metric": "queries_per_sec",
                "description": "Weekend batch correlated database query surge",
                "generator": _correlated_span_generator(
                    36000.0, 65000.0, 4*3600, noise=1800.0,
                    lo=30000.0, hi=72000.0, phase=1.35,
                ),
            }),
            ("database", {
                "time_offset": 5*SECONDS_PER_DAY + 2*3600 + 240,
                "duration_seconds": 4*3600,
                "shape": "sustained",
                "metric": "cpu_util_pct",
                "description": "Weekend batch correlated database CPU saturation",
                "generator": _correlated_span_generator(
                    45.0, 94.0, 4*3600, noise=1.8,
                    lo=35.0, hi=98.0, phase=1.35,
                ),
            }),
        ),
        cascade_specs=(
            ("database", {
                "time_offset": 5*SECONDS_PER_DAY + 2*3600 + 15,
                "metric": "queries_per_sec",
                "description": "Cascading: Batch LLM processing hammers database",
                "generator": lambda ts, idx, rng: 65000 + rng.normal(0, 5000),
                "severity": DEFAULT_SEVERITY,
            }),
            ("database", {
                "time_offset": 5*SECONDS_PER_DAY + 2*3600 + 120,
                "metric": "cpu_util_pct",
                "description": "Cascading: Database CPU saturates from batch analytics",
                "generator": lambda ts, idx, rng: 94 + rng.normal(0, 2),
                "severity": DEFAULT_SEVERITY,
            }),
            ("cacheservice", {
                "time_offset": 5*SECONDS_PER_DAY + 2*3600 + 45,
                "metric": "cache_misses",
                "description": "Cascading: Batch job overwhelms cache — misses ~17,700 (hit ratio ~22%)",
                "generator": lambda ts, idx, rng: 17727.0 + rng.normal(0, 800),
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
                "duration_seconds": 15*60,
                "shape": "sustained",
                "metric": "llm_requests_per_sec",
                "description": "Social media mention drives 10× traffic spike for 15 min",
                "generator": lambda ts, idx: 450,
            }),
            ("llm_analytics", {
                "time_offset": 6*SECONDS_PER_DAY + 16*3600 + 45*60,
                "duration_seconds": 15*60,
                "shape": "sustained",
                "metric": "input_tokens_per_sec",
                "description": "Massive token usage from social traffic for 15 min",
                "generator": lambda ts, idx: 420000,
            }),
            ("llm_analytics", {
                "time_offset": 6*SECONDS_PER_DAY + 16*3600 + 45*60,
                "duration_seconds": 15*60,
                "shape": "sustained",
                "metric": "output_tokens_per_sec",
                "description": "Output tokens surge from viral event for 15 min",
                "generator": lambda ts, idx: 135000,
            }),
        ),
        cascade_specs=(
            ("apigateway", {
                "time_offset": 6*SECONDS_PER_DAY + 16*3600 + 45*60 + 5,
                "metric": "active_connections",
                "description": "Cascading: Viral LLM traffic maxes out connections",
                "generator": lambda ts, idx, rng: 4800 + rng.normal(0, 200),
                "severity": DEFAULT_SEVERITY,
            }),
            ("apigateway", {
                "time_offset": 6*SECONDS_PER_DAY + 16*3600 + 45*60 + 15,
                "metric": "cpu_util_pct",
                "description": "Cascading: API gateway CPU spikes from LLM traffic",
                "generator": lambda ts, idx, rng: 87 + rng.normal(0, 4),
                "severity": DEFAULT_SEVERITY,
            }),
            ("database", {
                "time_offset": 6*SECONDS_PER_DAY + 16*3600 + 45*60 + 25,
                "metric": "connections",
                "description": "Cascading: Database connection pool exhausted by LLM load",
                "generator": lambda ts, idx, rng: 9800 + rng.normal(0, 500),
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
    # Long-window GPU inference serving catalog (default 50k-row shape)
    # ------------------------------------------------------------------
    "gpu_inference_fragmentation": Scenario(
        id="gpu_inference_fragmentation",
        name="GPU inference allocator fragmentation + sparse failures",
        severity="medium",
        days_required=1,
        category="gpu_inference",
        components_touched=("gpu_inference", "llm_analytics"),
        primary_specs=_GPU_INFERENCE_FRAGMENTATION_PRIMARY_SPECS,
        cascade_specs=_GPU_INFERENCE_FRAGMENTATION_CASCADE_SPECS,
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
                "generator": lambda ts, idx, rng: 9000 + rng.normal(0, 250),
                "severity": "high",
            }),
            ("authservice", {
                "time_offset": 5*3600 + 60,
                "metric": "error_rate",
                "description": "Cascading: Regional failover propagates auth errors (~40%)",
                "generator": lambda ts, idx: 0.40,
                "severity": "high",
            }),
            ("mqservice", {
                "time_offset": 5*3600 + 90,
                "metric": "pending_messages",
                "description": "Cascading: Regional failover backs up queue — ~500,000 pending",
                "generator": lambda ts, idx, rng: 500000 + rng.normal(0, 12000),
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
                "generator": lambda ts, idx, rng: 1700 + rng.normal(0, 90),
                "severity": "high",
            }),
            ("apigateway", {
                "time_offset": 11*3600 + 30*60 + 45,
                "metric": "backend_latency_ms",
                "description": "Cascading: Cache+DB meltdown drags gateway backend latency to ~950 ms",
                "generator": lambda ts, idx, rng: 950 + rng.normal(0, 60),
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
                "description": "Cascading: LLM outage propagates to gateway (~35%)",
                "generator": lambda ts, idx: 0.35,
                "severity": "high",
            }),
            ("cacheservice", {
                "time_offset": 20*3600 + 30,
                "metric": "cache_misses",
                "description": "Cascading: LLM outage drives context cache miss surge (~3,000)",
                "generator": lambda ts, idx, rng: 3000 + rng.normal(0, 200),
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
                "generator": lambda ts, idx, rng: 600 + rng.normal(0, 25),
                "severity": "high",
            }),
            ("database", {
                "time_offset": 16*3600 + 90,
                "metric": "cpu_util_pct",
                "description": "Cascading: Gateway saturation drives DB CPU to ~92%",
                "generator": lambda ts, idx, rng: 92 + rng.normal(0, 2),
                "severity": "high",
            }),
            ("mqservice", {
                "time_offset": 16*3600 + 120,
                "metric": "pending_messages",
                "description": "Cascading: Gateway saturation queues messages — ~800,000 pending",
                "generator": lambda ts, idx, rng: 800000 + rng.normal(0, 15000),
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
                "generator": lambda ts, idx, rng: 90 + rng.normal(0, 6),
                "severity": "high",
            }),
            ("apigateway", {
                "time_offset": 22*3600 + 45,
                "metric": "error_rate",
                "description": "Cascading: Storage 5xx surge propagates to gateway (~30%)",
                "generator": lambda ts, idx: 0.30,
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
                "generator": lambda ts, idx, rng: 1200 + rng.normal(0, 50),
                "severity": "high",
            }),
            ("database", {
                "time_offset": 15*3600 + 90,
                "metric": "connections",
                "description": "Cascading: retry pile-up drives DB connections to ~5,800",
                "generator": lambda ts, idx, rng: 5800 + rng.normal(0, 150),
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
                "generator": lambda ts, idx, rng: 150 + rng.normal(0, 8),
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
                "generator": lambda ts, idx, rng: 380 + rng.normal(0, 20),
                "severity": "high",
            }),
            ("authservice", {
                "time_offset": 18*3600 + 20*60 + 60,
                "metric": "error_rate",
                "description": "Cascading: AZ partition fails auth replica reads (~40%)",
                "generator": lambda ts, idx: 0.40,
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
                "shape": "sustained",
                "metric": "memory_util_pct",
                "description": "Cache memory leak — slow growth 50%→95% over 51h",
                "generator": _correlated_span_generator(
                    50.0, 95.0, 51*3600, noise=0.25,
                    lo=48.0, hi=97.0, phase=0.15,
                ),
            }),
            ("cacheservice", {
                "time_offset": 2*SECONDS_PER_DAY + 12*3600,       # Day 3 12:00
                "duration_seconds": 12*3600,
                "shape": "sustained",
                "metric": "cache_misses",
                "description": "Cache eviction cascade — misses ramp 682→3,333 (hit ratio 88%→60%) over 12h",
                "generator": _correlated_span_generator(
                    682.0, 3333.0, 12*3600, noise=55.0,
                    lo=600.0, hi=3600.0, phase=0.15,
                ),
            }),
            ("database", {
                "time_offset": 1*SECONDS_PER_DAY + 12*3600 + 60,
                "duration_seconds": 24*3600,
                "shape": "sustained",
                "metric": "queries_per_sec",
                "description": "Cache leak correlated DB query pressure — sustained climb with miss volume",
                "generator": _correlated_span_generator(
                    32000.0, 43000.0, 24*3600, noise=900.0,
                    lo=28000.0, hi=47000.0, phase=0.15,
                ),
            }),
            ("database", {
                "time_offset": 2*SECONDS_PER_DAY + 12*3600 + 60,
                "duration_seconds": 12*3600,
                "shape": "sustained",
                "metric": "read_latency_ms",
                "description": "Cache leak correlated DB read latency — rises with eviction pressure",
                "generator": _correlated_span_generator(
                    24.0, 58.0, 12*3600, noise=2.0,
                    lo=18.0, hi=66.0, phase=0.15,
                ),
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
                "generator": lambda ts, idx, rng: 32000 + rng.normal(0, 1500),
                "severity": DEFAULT_SEVERITY,
            }),
            ("database", {
                "time_offset": 2*SECONDS_PER_DAY + 12*3600 + 30*60,
                "metric": "queries_per_sec",
                "description": "Cascading: Cache hit-ratio decline — DB queries climb to ~42k",
                "generator": lambda ts, idx, rng: 42000 + rng.normal(0, 2000),
                "severity": DEFAULT_SEVERITY,
            }),
            ("database", {
                "time_offset": 2*SECONDS_PER_DAY + 12*3600 + 30*60,
                "metric": "read_latency_ms",
                "description": "Cascading: Cache hit-ratio decline pushes DB read latency to ~55 ms",
                "generator": lambda ts, idx, rng: 55 + rng.normal(0, 4),
                "severity": DEFAULT_SEVERITY,
            }),
            # Cold-start stampede: cascade specs are single-row step writes
            # (register_cascade has no shape support), so this collapses to a
            # single ~60k step at restart + 5 min.
            ("database", {
                "time_offset": 3*SECONDS_PER_DAY + 3*3600 + 300,
                "metric": "queries_per_sec",
                "description": "Cascading: Cache cold-start stampede — DB queries ~60k",
                "generator": lambda ts, idx, rng: 60000 + rng.normal(0, 2500),
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
                "generator": lambda ts, idx, rng: 180000 + rng.normal(0, 6000),
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
                "generator": lambda ts, idx, rng: 350 + rng.normal(0, 15),
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
                "shape": "sustained",
                "metric": "disk_used_pct",
                "description": "Database disk slow exhaustion 65%→92% over 96h",
                "generator": _correlated_span_generator(
                    65.0, 92.0, 96*3600, noise=0.18,
                    lo=63.0, hi=94.0, phase=0.45,
                ),
            }),
            ("database", {
                "time_offset": 4*SECONDS_PER_DAY + 6*3600,        # Day 5 06:00
                "duration_seconds": 12*3600,
                "shape": "sustained",
                "metric": "write_latency_ms",
                "description": "Database write latency drift 12→90 ms as I/O saturates",
                "generator": _correlated_span_generator(
                    12.0, 90.0, 12*3600, noise=3.0,
                    lo=10.0, hi=102.0, phase=0.45,
                ),
            }),
            ("database", {
                "time_offset": 4*SECONDS_PER_DAY + 6*3600 + 60,
                "duration_seconds": 12*3600,
                "shape": "sustained",
                "metric": "connections",
                "description": "Database disk pressure correlated connection buildup",
                "generator": _correlated_span_generator(
                    4200.0, 7800.0, 12*3600, noise=140.0,
                    lo=3500.0, hi=8500.0, phase=0.45,
                ),
            }),
            ("database", {
                "time_offset": 4*SECONDS_PER_DAY + 6*3600 + 120,
                "duration_seconds": 12*3600,
                "shape": "sustained",
                "metric": "cpu_util_pct",
                "description": "Database disk pressure correlated CPU saturation",
                "generator": _correlated_span_generator(
                    35.0, 86.0, 12*3600, noise=1.5,
                    lo=28.0, hi=92.0, phase=0.45,
                ),
            }),
            ("observabilitypipeline", {
                "time_offset": 4*SECONDS_PER_DAY + 6*3600 + 31*60,
                "duration_seconds": 6*3600,
                "shape": "sustained",
                "metric": "ingest_lag_s",
                "description": "DB disk pressure correlated observability ingest lag",
                "generator": _correlated_span_generator(
                    60.0, 180.0, 6*3600, noise=6.0,
                    lo=40.0, hi=210.0, phase=0.45,
                ),
            }),
            ("mqservice", {
                "time_offset": 4*SECONDS_PER_DAY + 12*3600 + 60,
                "duration_seconds": 12*3600,
                "shape": "sustained",
                "metric": "pending_messages",
                "description": "DB disk pressure correlated MQ backlog growth",
                "generator": _correlated_span_generator(
                    120000.0, 330000.0, 12*3600, noise=9000.0,
                    lo=90000.0, hi=380000.0, phase=0.45,
                ),
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
                "generator": lambda ts, idx, rng: 320000 + rng.normal(0, 10000),
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
    # Phase 7: partial-outage scenarios exercising instance_filter
    "auth_pod_failure": Scenario(
        id="auth_pod_failure",
        name="Auth Pod-0 Partial Failure",
        severity="high",
        days_required=1,
        category="partial_outage",
        components_touched=("authservice", "apigateway"),
        primary_specs=(
            ("authservice", {
                "time_offset": 3*3600 + 30*60,
                "duration_seconds": 5*60,
                "shape": "sustained",
                "metric": "error_rate",
                "description": "Pod-0 partial failure — error_rate holds near 85% on i0 for 5 min",
                "generator": lambda ts, idx: 0.85,
                "instance_filter": ["i0"],
            }),
            ("authservice", {
                "time_offset": 3*3600 + 30*60,
                "duration_seconds": 5*60,
                "shape": "sustained",
                "metric": "login_success_rate",
                "description": "Pod-0 partial failure — login_success_rate holds near 30% on i0 for 5 min",
                "generator": lambda ts, idx: 30.0,
                "instance_filter": ["i0"],
            }),
        ),
        cascade_specs=(
            ("apigateway", {
                "time_offset": 3*3600 + 30*60,
                "metric": "backend_latency_ms",
                "description": "Cascading: auth pod-0 failure raises gateway backend latency to ~800 ms",
                "generator": lambda ts, idx: 800,
                "instance_filter": ["i0"],
            }),
        ),
    ),
    "cache_az_isolation": Scenario(
        id="cache_az_isolation",
        name="Cache AZ us-east-1a Isolation",
        severity="high",
        days_required=1,
        category="partial_outage",
        components_touched=("cacheservice",),
        primary_specs=(
            ("cacheservice", {
                "time_offset": 5*3600,
                "duration_seconds": 10*60,
                "shape": "sustained",
                "metric": "cache_hits",
                "description": "AZ us-east-1a isolated — cache_hits collapse to ~500 on affected instances for 10 min",
                "generator": lambda ts, idx: 500,
                "instance_filter": lambda inst: inst.az == "us-east-1a",
            }),
            ("cacheservice", {
                "time_offset": 5*3600,
                "duration_seconds": 10*60,
                "shape": "sustained",
                "metric": "cache_misses",
                "description": "AZ us-east-1a isolated — cache_misses spike to ~3000 on affected instances for 10 min",
                "generator": lambda ts, idx: 3000,
                "instance_filter": lambda inst: inst.az == "us-east-1a",
            }),
        ),
        cascade_specs=(),
    ),
}


def _validate_scenario_spec(slug: str, component: str, spec: dict,
                            *, is_cascade: bool) -> None:
    """Schema-check one primary or cascade spec dict at import time.

    Raises ``ValueError`` naming the scenario slug, component, and offending
    field on any drift. Cascade specs reject ``shape`` / ``duration_seconds``
    / ``shape_params`` because the cascade injection path is single-row step
    writes only (see CLAUDE.md § Anomaly injection schema).

    One deliberate write side effect: an iterable ``instance_filter`` is
    normalized in place to a ``frozenset`` (``spec["instance_filter"] =
    frozenset(items)``). This is load-bearing, not incidental — element
    validation must iterate the filter, which would *exhaust* a one-shot
    iterable (a generator, ``iter(...)``) before the runtime ever saw it,
    so the materialized form has to be stored back. Callers passing a
    spec dict should expect the field to be rewritten; everything else in
    the dict is read-only to this function.
    """
    kind = "cascade_specs" if is_cascade else "primary_specs"
    location = f"SCENARIOS[{slug!r}].{kind} entry for component {component!r}"

    if not isinstance(spec, dict):
        raise ValueError(
            f"{location} is not a dict (got {type(spec).__name__}); every "
            f"spec must be a dict with keys time_offset/metric/description/generator."
        )

    required = ("time_offset", "metric", "description", "generator")
    missing = [k for k in required if k not in spec]
    if missing:
        raise ValueError(
            f"{location} is missing required key(s) {missing}; every spec "
            f"must define {list(required)}."
        )

    metric = spec["metric"]
    if not isinstance(metric, str):
        raise ValueError(
            f"{location} has non-string metric {metric!r}; expected a "
            f"metric name from COMPONENTS[{component!r}]."
        )
    catalog = COMPONENTS.get(component, ())
    catalog_names = {s.name for s in catalog}
    if metric not in catalog_names:
        raise ValueError(
            f"{location} references metric {metric!r} not present in "
            f"COMPONENTS[{component!r}] (full catalog). Catalog: "
            f"{sorted(catalog_names)}."
        )

    if not callable(spec["generator"]):
        raise ValueError(
            f"{location} metric={metric!r} has non-callable generator "
            f"{spec['generator']!r}; expected a callable."
        )

    time_offset = spec["time_offset"]
    # ``bool`` is a subclass of ``int`` so ``isinstance(True, (int, float))``
    # is True; reject it explicitly so a stray boolean doesn't silently
    # round to row 1.
    if isinstance(time_offset, bool) or not isinstance(time_offset, (int, float)):
        raise ValueError(
            f"{location} metric={metric!r} has time_offset {time_offset!r}; "
            f"expected int or float seconds from START."
        )
    # math.isfinite() converts non-floats to a C double first; an
    # arbitrarily large Python int can raise OverflowError before the
    # finiteness check completes. Integers are finite by definition.
    if isinstance(time_offset, float) and not math.isfinite(time_offset):
        raise ValueError(
            f"{location} metric={metric!r} has non-finite time_offset "
            f"{time_offset!r}; offsets must be finite seconds from START."
        )
    # generate_component() does ``time_offset / interval`` (float divide)
    # at runtime; a Python int that can't be represented as a float would
    # raise OverflowError there. Reject at import time so the failure
    # surfaces with the validator's clear ValueError instead of a deep
    # runtime crash.
    if isinstance(time_offset, int) and not isinstance(time_offset, bool):
        try:
            float(time_offset)
        except OverflowError:
            raise ValueError(
                f"{location} metric={metric!r} has time_offset "
                f"{time_offset!r} that overflows float representation; "
                f"offsets are converted to float at runtime."
            ) from None
    if time_offset < 0:
        raise ValueError(
            f"{location} metric={metric!r} has negative time_offset "
            f"{time_offset!r}; offsets are seconds from START and must be >= 0."
        )

    description = spec["description"]
    if not isinstance(description, str) or not description.strip():
        raise ValueError(
            f"{location} metric={metric!r} has empty or non-string "
            f"description {description!r}; manifest rows require a label."
        )

    if is_cascade:
        forbidden = [k for k in ("shape", "duration_seconds", "shape_params")
                     if k in spec]
        if forbidden:
            raise ValueError(
                f"{location} metric={metric!r} declares {forbidden}; cascade "
                f"specs are single-row step writes and must not carry "
                f"shape/duration fields. Move shaped behavior into "
                f"primary_specs."
            )

    if not is_cascade and "shape" in spec:
        shape = spec["shape"]
        if not isinstance(shape, str):
            raise ValueError(
                f"{location} metric={metric!r} has non-string shape "
                f"{shape!r}; expected one of {sorted(_VALID_ANOMALY_SHAPES)}."
            )
        if shape not in _VALID_ANOMALY_SHAPES:
            raise ValueError(
                f"{location} metric={metric!r} has unsupported shape "
                f"{shape!r}; expected one of {sorted(_VALID_ANOMALY_SHAPES)}."
            )

    if not is_cascade and "duration_seconds" in spec:
        duration = spec["duration_seconds"]
        if isinstance(duration, bool) or not isinstance(duration, (int, float)):
            raise ValueError(
                f"{location} metric={metric!r} has duration_seconds "
                f"{duration!r}; expected int or float."
            )
        # Integers are finite by definition; avoid the C-double conversion
        # that math.isfinite() does for non-floats (can raise OverflowError
        # on arbitrarily large Python ints).
        if isinstance(duration, float) and not math.isfinite(duration):
            raise ValueError(
                f"{location} metric={metric!r} has non-finite duration_seconds "
                f"{duration!r}; expected a finite value."
            )
        # Same float-representability check as time_offset: generate_component
        # / _resolve_anomaly_value cast duration_seconds to float at runtime.
        if isinstance(duration, int) and not isinstance(duration, bool):
            try:
                float(duration)
            except OverflowError:
                raise ValueError(
                    f"{location} metric={metric!r} has duration_seconds "
                    f"{duration!r} that overflows float representation; "
                    f"durations are converted to float at runtime."
                ) from None
        if duration < 0:
            raise ValueError(
                f"{location} metric={metric!r} has negative duration_seconds "
                f"{duration!r}; duration must be >= 0 (0 means single-row step)."
            )

    if not is_cascade and "shape_params" in spec:
        params = spec["shape_params"]
        if not isinstance(params, dict):
            raise ValueError(
                f"{location} metric={metric!r} has shape_params "
                f"{params!r}; expected a dict."
            )

    # ``instance_filter`` (Phase 4) — optional on both primary and
    # cascade specs. Accepted forms:
    #
    #   * omitted / ``None``         -> apply to every active instance
    #                                   (today's Phase 2 behavior; preserves
    #                                   the locked Phase 2 hashes).
    #   * iterable of ``str`` ids    -> apply only to instances whose
    #                                   ``Instance.id`` is in the set.
    #   * callable ``(Instance) -> bool`` -> per-instance predicate.
    #
    # Import-time validation only checks structural shape. ``INSTANCES`` is
    # static but ``--instance-config`` (a later phase) will register runtime
    # ids, so membership against the registry can't be validated at import
    # time. The runtime path (``_resolve_instance_filter`` +
    # ``generate_component``) emits a ``WARNING`` and skips the spec when
    # the filter resolves to zero active instances.
    #
    # ``bool`` is rejected before the iterable check because ``bool`` is a
    # subclass of ``int`` but a scalar — the error message should name it
    # as a scalar, not "iterable of non-string". A bare ``str`` is also
    # rejected: it's iterable in Python but almost always a bug (would
    # iterate characters, producing a per-character filter); callers must
    # pass ``["i0"]`` instead of ``"i0"``.
    if "instance_filter" in spec:
        inst_filter = spec["instance_filter"]
        if inst_filter is None:
            pass
        elif callable(inst_filter):
            pass
        elif isinstance(inst_filter, (bool, int, float)):
            raise ValueError(
                f"{location} metric={metric!r} has instance_filter "
                f"{inst_filter!r}; expected None, an iterable of instance "
                f"ids (str), or a callable (Instance) -> bool."
            )
        elif isinstance(inst_filter, str):
            raise ValueError(
                f"{location} metric={metric!r} has instance_filter "
                f"{inst_filter!r} (a bare string); expected an iterable of "
                f"instance ids like [\"i0\"], not a single string (which "
                f"would iterate characters)."
            )
        elif isinstance(inst_filter, dict):
            raise ValueError(
                f"{location} metric={metric!r} has instance_filter "
                f"{inst_filter!r} (a dict); expected None, an iterable of "
                f"instance ids (str), or a callable (Instance) -> bool."
            )
        else:
            try:
                items = list(inst_filter)
            except TypeError:
                raise ValueError(
                    f"{location} metric={metric!r} has instance_filter "
                    f"{inst_filter!r}; expected None, an iterable of "
                    f"instance ids (str), or a callable (Instance) -> bool."
                ) from None
            for item in items:
                if not isinstance(item, str):
                    raise ValueError(
                        f"{location} metric={metric!r} has instance_filter "
                        f"with non-string entry {item!r} "
                        f"(type {type(item).__name__}); ids must be strings."
                    )
            # Normalize to frozenset so one-shot iterators (generators,
            # iter(...)) are materialized and ``_resolve_instance_filter``
            # can call ``frozenset(spec_filter)`` on a reiterable object.
            # Also gives O(1) membership checks at runtime.
            spec["instance_filter"] = frozenset(items)

    # Generator signature rules. The runtime always calls a generator with
    # a fixed positional shape determined by the path:
    #   - Step path (cascades + primary step specs without duration_seconds):
    #     3-arg ``(ts, col, rng)`` or 2-arg ``(ts, col)``.
    #   - Span path (primary specs with shape != "step" or
    #     duration_seconds > 0): 5-arg ``(ts, col, t_within, span_idx, rng)``
    #     or 2-arg ``(ts, col)``.
    # The validator must accept only signatures that the runtime can call
    # without silently misbinding ``t_within``/``span_idx`` to a parameter
    # the author intended for a different value (most commonly ``rng``).
    has_shape = spec.get("shape", "step") != "step"
    # Avoid the float() conversion on raw spec data — an arbitrarily large
    # int duration_seconds would overflow. Test the raw value's positivity
    # directly; type/finiteness was already validated above.
    raw_duration = spec.get("duration_seconds", 0)
    has_duration = bool(raw_duration) and raw_duration > 0
    meta = _generator_meta(spec["generator"])
    # Required keyword-only params can never be supplied by the runtime
    # (the dispatch path uses positional args only); reject up front.
    if meta["has_required_kwargs"]:
        raise ValueError(
            f"{location} metric={metric!r} has a generator with required "
            f"keyword-only parameters; generators are called positionally "
            f"at runtime, so kwarg-only requirements would fail when the "
            f"spec fires. Provide defaults for keyword-only params, or "
            f"declare them as positional."
        )
    if not meta["inspectable"]:
        # Can't introspect — trust the caller; the dispatcher's try/except
        # fallback will handle it at runtime.
        return
    target = 5 if (has_shape or has_duration) else 3
    target_form = (
        "(ts, col, t_within, span_idx, rng)" if target == 5
        else "(ts, col, rng)"
    )
    path_name = "shape/duration" if (has_shape or has_duration) else "single-row step"
    required = meta["required_positional"]
    fixed = meta["fixed_positional_count"]
    has_var = meta["has_var_positional"]
    # Mirror the dispatcher logic:
    #   - if has_var or required == target: dispatcher calls target-arg
    #   - elif required <= 2: dispatcher calls 2-arg
    #   - else: no valid dispatch
    # Safety rules below ensure that whatever shape the dispatcher picks
    # binds the runtime values to author-intended positions (required
    # params or *args overflow) and never overwrites a default-having
    # fixed positional with t_within/span_idx/rng.
    reject_reason = None
    if required > target:
        reject_reason = (
            f"required_positional={required} > target {target}; no valid "
            f"dispatch can satisfy this many required params."
        )
    elif required != target and required > 2:
        # required ∈ {3, 4} on span path: dispatcher can't call 2-arg
        # (would fail required check) or target-arg (would bind t_within
        # /span_idx to required positional 3/4 — misbind).
        reject_reason = (
            f"required_positional={required} is between 2 and target "
            f"{target}; dispatcher would bind runtime internals to "
            f"required positions that the author intended for other values."
        )
    elif has_var and required <= 2 and fixed > 2:
        # has_var + default-having fixed positions BEYOND (ts, col).
        # Dispatcher picks target-arg, fills the default-having fixed
        # positions with t_within/span_idx/rng before flowing into *args
        # — overwriting the author's declared defaults.
        reject_reason = (
            f"required_positional={required} fixed_positional_count={fixed} "
            f"with *args: the dispatcher's {target}-arg call would bind "
            f"runtime internals to default-having fixed positions 3"
            f"{' through ' + str(min(fixed, target)) if min(fixed, target) > 3 else ''}, "
            f"overwriting the declared defaults. Move the default-having "
            f"positions after ``*args`` (kwarg-only with default) or drop them."
        )
    # Note: ``required == target and fixed > target`` (e.g. (ts, col, rng,
    # extra=None) for step) is intentionally accepted. The dispatcher calls
    # exactly target args, all required positions are bound, and any
    # trailing optional positions keep their declared defaults — no misbind.
    elif not has_var and required <= 2 and fixed < 2:
        # (ts) or () — dispatcher 2-arg call would fail.
        reject_reason = (
            f"fixed_positional_count={fixed} < 2; the 2-arg dispatcher "
            f"call would fail because the generator can't accept 2 args."
        )
    if reject_reason is not None:
        raise ValueError(
            f"{location} metric={metric!r} has a generator with "
            f"{reject_reason} {path_name} specs must use either the 2-arg "
            f"legacy form (ts, col) or the {target}-arg form {target_form}; "
            f"see CLAUDE.md § Scenario registry for the full dispatch rule."
        )


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
        # isinstance check first so an unhashable malformed value
        # (e.g., severity=[]) raises ValueError rather than a raw TypeError
        # from the set membership lookup.
        if (not isinstance(scenario.severity, str)
                or scenario.severity not in {"low", "medium", "high"}):
            raise ValueError(
                f"SCENARIOS[{slug!r}].severity {scenario.severity!r} must be "
                "a string in low / medium / high"
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
        # Validate each spec first so missing/malformed keys produce a clear
        # error before we try to read time_offset for the days_required check.
        valid_severities = {"low", "medium", "high"}
        for component, spec in scenario.primary_specs:
            if component not in known_components:
                raise ValueError(
                    f"SCENARIOS[{slug!r}].primary_specs references unknown "
                    f"component {component!r}"
                )
            _validate_scenario_spec(slug, component, spec, is_cascade=False)
            if "severity" in spec:
                sev = spec["severity"]
                if not isinstance(sev, str) or sev not in valid_severities:
                    raise ValueError(
                        f"SCENARIOS[{slug!r}].primary_specs entry for component "
                        f"{component!r} has severity {sev!r}; "
                        f"must be a string in {sorted(valid_severities)}. "
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
            _validate_scenario_spec(slug, target, cascade, is_cascade=True)
            if "severity" in cascade:
                sev = cascade["severity"]
                if not isinstance(sev, str) or sev not in valid_severities:
                    raise ValueError(
                        f"SCENARIOS[{slug!r}].cascade_specs entry targeting "
                        f"{target!r} has severity {sev!r}; "
                        f"must be a string in {sorted(valid_severities)}. "
                        f"_apply_signal_level_and_count reads spec.get('severity', "
                        f"DEFAULT_SEVERITY), so an unknown value would be silently "
                        f"filtered out at every --signal-level."
                    )
        # days_required must equal the day index (1-based) of the earliest
        # time_offset across primary and cascade specs. Setting it too high
        # silently drops in-range specs at the requested --duration-days;
        # too low activates the scenario before any spec is in range.
        # Spec validation above ensures time_offset is a valid finite numeric.
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
    # Reverse direction: the consistency must hold both ways. A
    # MetricSpec that declares a ``derivation`` string without a
    # matching DERIVATIONS entry would emit a schema.json claiming a
    # derivation the generator never recomputes, and the failure would
    # surface only at ``validate``-subcommand time as a runtime KeyError
    # from the strict ``_RECOMPUTERS[...]`` lookup instead of a clear
    # import-time error here. A DERIVATIONS metric whose MetricSpec
    # does NOT declare a ``derivation`` string is the mirror drift: the
    # generator recomputes the column but the schema never tells the
    # validator to check it.
    for component, specs in COMPONENTS.items():
        declared = {s.name for s in specs if s.derivation is not None}
        registered = set(DERIVATIONS.get(component, (None, ()))[1])
        unregistered = sorted(declared - registered)
        if unregistered:
            raise ValueError(
                f"COMPONENTS[{component!r}] metrics {unregistered} declare "
                "a `derivation` string but have no DERIVATIONS entry; the "
                "generator would never recompute them and the validate subcommand "
                "would fail with a KeyError. Add the DERIVATIONS (and "
                "_RECOMPUTERS) entries in lockstep."
            )
        undeclared = sorted(registered - declared)
        if undeclared:
            raise ValueError(
                f"DERIVATIONS[{component!r}] recomputes metrics "
                f"{undeclared} whose MetricSpec declares no `derivation` "
                "string; schema.json would omit the derivation and "
                "the validate subcommand would silently skip the check. Declare "
                "`derivation=` on the MetricSpec."
            )


_validate_derivations_registry()


# Derived from _INSTANCE_DIMENSION_COLUMNS so the two cannot drift:
# _INSTANCE_DIMENSION_COLUMNS leads with "id" (validated separately as a
# string/None/CSV-safe value); the remaining fields are the dimension
# attributes that _validate_instance_list iterates over.
_INSTANCE_DIMENSION_FIELDS: tuple[str, ...] = _INSTANCE_DIMENSION_COLUMNS[1:]


def _validate_instance_list(instances, *, where: str) -> None:
    """Per-entry invariants shared by ``_validate_instances_registry`` and
    ``generate_component`` (Phase 1, expanded in Phase 2).

    Rejects four classes of drift in ``instances`` (a non-empty iterable
    of ``Instance``):

    1. Non-``Instance`` entries: would raise a bare ``AttributeError`` on
       ``.id`` access at the next caller rather than a clear ``ValueError``.
       Mirrors ``_validate_scenarios_registry``'s isinstance-first pattern.
    2. Non-string (and non-``None``) ``Instance.id`` values: would raise a
       bare ``TypeError`` on set-membership lookup; Phase 4's
       ``instance_filter`` expects string ids.
    3. Duplicate non-None ``id`` values, or more than one anonymous
       (``id=None``) entry. Phase 4's ``instance_filter=["..."]`` looks up
       instances by id, so collisions would silently target multiple rows;
       multiple anonymous entries would be indistinguishable.
    4. Non-string (and non-``None``) dimension fields
       (``host``, ``pod``, ``az``, ``region``, ``tenant``): the Phase 2
       long-form CSV writer joins them with ``","`` directly. A non-string
       would raise a bare ``TypeError`` in the writer, and a value
       containing a comma or newline would silently corrupt the emitted
       CSV. Phase 3 (``--instance-config``) will surface this same
       constraint to file-loaded instance maps.

    ``where`` is the descriptor prefix used in raised error messages
    (e.g. ``"INSTANCES['authservice']"`` from the registry validator or
    ``"generate_component('authservice') instances"`` from the call site).
    Empty-list rejection lives at each call site so it can use a
    site-specific message.
    """
    seen_ids: set[str] = set()
    anon_count = 0
    for inst in instances:
        if not isinstance(inst, Instance):
            raise ValueError(
                f"{where} contains non-Instance entry {inst!r} "
                f"(type {type(inst).__name__}); every entry must be an "
                f"Instance dataclass."
            )
        if inst.id is not None:
            if not isinstance(inst.id, str):
                raise ValueError(
                    f"{where} entry has Instance.id={inst.id!r} "
                    f"(type {type(inst.id).__name__}); id must be None or a "
                    f"string (instance_filter looks up ids by string equality)."
                )
            if "," in inst.id or "\n" in inst.id or "\r" in inst.id:
                raise ValueError(
                    f"{where} entry has Instance.id={inst.id!r} containing "
                    f"a comma or newline; ids must not contain CSV-significant "
                    f"characters (the long-form writer does not quote id cells)."
                )
        for field_name in _INSTANCE_DIMENSION_FIELDS:
            value = getattr(inst, field_name)
            if value is None:
                continue
            if not isinstance(value, str):
                raise ValueError(
                    f"{where} entry has Instance.{field_name}={value!r} "
                    f"(type {type(value).__name__}); dimension fields must "
                    f"be None or a string (the long-form CSV writer joins "
                    f"them with ',' directly)."
                )
            if "," in value or "\n" in value or "\r" in value:
                raise ValueError(
                    f"{where} entry has Instance.{field_name}={value!r} "
                    f"containing a comma or newline; dimension values "
                    f"must not contain CSV-significant characters "
                    f"(the long-form writer does not quote dimension cells)."
                )
        if inst.id is None:
            anon_count += 1
            continue
        if inst.id in seen_ids:
            raise ValueError(
                f"{where} declares duplicate Instance.id={inst.id!r}; "
                f"ids must be unique per component for instance_filter "
                f"lookups (Phase 4)."
            )
        seen_ids.add(inst.id)
    if anon_count > 1:
        raise ValueError(
            f"{where} contains {anon_count} anonymous Instance(id=None) "
            f"entries; at most one anonymous instance is allowed per "
            f"component."
        )


def _validate_instances_registry() -> None:
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
    known = set(COMPONENTS.keys())
    declared = set(INSTANCES.keys())
    if declared != known:
        missing = sorted(known - declared)
        extra = sorted(declared - known)
        raise ValueError(
            "INSTANCES and COMPONENTS keys must match. "
            f"Missing from INSTANCES: {missing}. "
            f"Extra in INSTANCES: {extra}."
        )
    for component, instance_list in INSTANCES.items():
        if not instance_list:
            raise ValueError(
                f"INSTANCES[{component!r}] is empty; needs at least one "
                f"Instance (Instance() preserves the dimensionless default)."
            )
        _validate_instance_list(
            instance_list, where=f"INSTANCES[{component!r}]"
        )


_validate_instances_registry()


def _load_instance_config(path: "Path") -> dict[str, list["Instance"]]:
    """Parse a YAML or JSON --instance-config file into a per-component Instance map.

    File schema::

        components:
          authservice:
            - {id: auth-east, region: us-east-1, pod: auth-1}
            - {id: auth-west, region: us-west-2, pod: auth-2}

    Every listed component must be a key of COMPONENTS. Each instance dict may
    only contain Instance field names (id, host, pod, az, region, tenant).
    Per-component instance counts are capped at MAX_INSTANCES_PER_COMPONENT.
    The id-uniqueness and shape rules from _validate_instance_list apply after
    construction.

    Returns a partial map: only components explicitly listed in the file appear
    as keys. ``main()`` fills the remaining components from the module-level
    ``INSTANCES`` registry (defaulting to ``[Instance()]``).

    Raises ``ValueError`` (caught in ``main()`` and re-raised via ``sys.exit``)
    for every schema violation: unknown components, unknown fields, empty
    component lists, duplicate ids, count exceeding the cap, missing or
    malformed top-level structure, IO errors on the file, and YAML/JSON parse
    errors.
    """
    suffix = path.suffix.lower()
    is_yaml = suffix in {".yaml", ".yml"}
    if is_yaml:
        try:
            import yaml  # PyYAML; optional dependency
        except ImportError:
            raise ValueError(
                f"--instance-config {path}: PyYAML is required to parse YAML files "
                "but is not installed. Install it with 'pip install pyyaml' or "
                "use a .json file instead."
            )
        # PyYAML's YAMLError is the parent of every parse / scanner /
        # composer error it raises.
        parse_exc_types: tuple[type[Exception], ...] = (
            yaml.YAMLError, UnicodeDecodeError,
        )
    else:
        import json
        parse_exc_types = (json.JSONDecodeError, UnicodeDecodeError)
    try:
        with open(path, encoding="utf-8") as f:
            raw = yaml.safe_load(f) if is_yaml else json.load(f)
    except OSError as exc:
        raise ValueError(
            f"--instance-config {path}: failed to read file: {exc}"
        ) from exc
    except parse_exc_types as exc:
        # Narrowed from ``except Exception`` so KeyboardInterrupt /
        # SystemExit (they inherit from BaseException, not Exception, but
        # being explicit avoids accidentally swallowing programming-error
        # exceptions like AttributeError if the parser were ever swapped).
        raise ValueError(
            f"--instance-config {path}: failed to parse "
            f"{'YAML' if is_yaml else 'JSON'}: {exc}"
        ) from exc

    if not isinstance(raw, dict):
        raise ValueError(
            f"--instance-config {path}: top-level value must be a mapping, "
            f"got {type(raw).__name__}"
        )
    # Distinguish "key absent" from "key present but explicitly null" so
    # ``components: null`` in YAML reports the more accurate
    # "must be a mapping" error rather than the misleading "missing key"
    # error.
    if "components" not in raw:
        raise ValueError(
            f"--instance-config {path}: missing required top-level key 'components'"
        )
    components_raw = raw["components"]
    if not isinstance(components_raw, dict):
        raise ValueError(
            f"--instance-config {path}: 'components' must be a mapping, "
            f"got {type(components_raw).__name__}"
        )

    # Derived from the canonical column list so a future Instance field
    # added to ``_INSTANCE_DIMENSION_COLUMNS`` is immediately accepted by
    # the config loader without a second edit.
    _valid_instance_fields = frozenset(_INSTANCE_DIMENSION_COLUMNS)
    result: dict[str, list[Instance]] = {}
    for component, inst_list in components_raw.items():
        if component not in COMPONENTS:
            raise ValueError(
                f"--instance-config {path}: unknown component {component!r}; "
                f"valid components: {sorted(COMPONENTS.keys())}"
            )
        if not isinstance(inst_list, list):
            raise ValueError(
                f"--instance-config {path}: {component!r} value must be a list, "
                f"got {type(inst_list).__name__}"
            )
        if not inst_list:
            raise ValueError(
                f"--instance-config {path}: {component!r} has an empty instance list; "
                "omit the key to fall back to a single anonymous Instance()"
            )
        if len(inst_list) > MAX_INSTANCES_PER_COMPONENT:
            raise ValueError(
                f"--instance-config {path}: {component!r} declares {len(inst_list)} "
                f"instances but MAX_INSTANCES_PER_COMPONENT={MAX_INSTANCES_PER_COMPONENT}"
            )
        instances = []
        for i, entry in enumerate(inst_list):
            if not isinstance(entry, dict):
                raise ValueError(
                    f"--instance-config {path}: {component!r}[{i}] must be a dict, "
                    f"got {type(entry).__name__}"
                )
            # Compare keys against the valid set after coercing to repr so a
            # YAML mapping with non-string keys (e.g. ``{1: 'x'}``) still
            # surfaces as an unknown-field ValueError rather than a TypeError
            # from sorting heterogeneous keys.
            unknown = [k for k in entry if k not in _valid_instance_fields]
            if unknown:
                raise ValueError(
                    f"--instance-config {path}: {component!r}[{i}] contains unknown "
                    f"field(s) {sorted(unknown, key=repr)}; valid fields: "
                    f"{sorted(_valid_instance_fields)}"
                )
            # Build the Instance kwargs from the same canonical tuple
            # used by the validator above, so a future field added to
            # _INSTANCE_DIMENSION_COLUMNS lands in both places at once
            # (validator accepts the key + constructor populates the
            # attribute) and can't be accepted-and-silently-dropped.
            instances.append(Instance(**{
                field: entry.get(field) for field in _INSTANCE_DIMENSION_COLUMNS
            }))
        _validate_instance_list(instances, where=f"--instance-config {path} {component!r}")
        result[component] = instances

    return result


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
        # Shallow-copy each spec dict and stamp scenario provenance with
        # ``_``-prefixed keys. ``generate_component`` carries the dict reference
        # forward into the manifest entry, and the manifest writer uses
        # ``csv.DictWriter(..., extrasaction="ignore")`` so the private keys
        # never leak into the CSV. Shallow-copying keeps the frozen SCENARIOS
        # registry pristine across test runs (the registry is reused process-wide).
        for component, spec in scenario.primary_specs:
            tagged = dict(spec)
            tagged["_scenario_id"] = slug
            tagged["_severity"] = scenario.severity
            tagged["_is_cascade"] = False
            component_anomalies.setdefault(component, []).append(tagged)
        for target, cascade in scenario.cascade_specs:
            tagged = dict(cascade)
            tagged["_scenario_id"] = slug
            tagged["_severity"] = scenario.severity
            tagged["_is_cascade"] = True
            cascade_registry.setdefault(target, []).append(tagged)


# ------------------------------------------------------------------
# CLI + entry point
# ------------------------------------------------------------------


# Flags hidden from the default ``-h`` (shown by ``--help-all``): the
# advanced / research knobs that the common use cases never touch.
# Keyed by argparse ``dest``. (The 16 deprecated alias flags that used
# to live here were removed at the post-phase-9 CLI flag day; the
# historic dests they wrote — ``emit_selection``, ``combine``, the
# OTEL toggle/endpoint sextet — survive as the internal namespace
# populated by ``p.set_defaults`` + ``_reconcile_cli_surface``.)
_ADVANCED_DESTS: frozenset[str] = frozenset({
    # research / power knobs
    "anomaly_count", "allow_huge_output", "inject_dst_artifact_day",
    # OTEL transport tuning (set-once-per-environment; env vars exist
    # for protocol and auth scheme already)
    "otel_gauge_batch_seconds", "otel_gauge_metric_prefix",
    "otel_stream_timeout_seconds", "otel_stream_max_events",
    "otel_stream_auth_scheme", "otel_activity_log", "otel_verbose",
})


def _flag_in_argv(argv: list[str], flag: str) -> bool:
    """True when ``flag`` was explicitly passed (bare or ``=value`` form)."""
    return any(tok == flag or tok.startswith(flag + "=") for tok in argv)


def _parse_components_value(error, raw: str) -> set[str]:
    """Parse and validate a ``--components`` CSV value ('all' or names).

    ``error`` is an argparse ``parser.error``-style callable so both the
    flat parser and the ``combine`` subcommand share one validation path.
    """
    raw_components = [item.strip().lower() for item in raw.split(",") if item.strip()]
    if not raw_components:
        error("--components must contain at least one component name (or 'all')")
    selected_components = set(raw_components)
    invalid_components = sorted(selected_components - set(COMPONENTS.keys()))
    if invalid_components and "all" in invalid_components:
        invalid_components.remove("all")
    if invalid_components:
        error("--components contains invalid value(s): "
              f"{', '.join(invalid_components)}. "
              f"Allowed: {', '.join(sorted(COMPONENTS.keys()))} or 'all'")
    if "all" in selected_components:
        if len(selected_components) > 1:
            error("--components 'all' cannot be combined with explicit "
                  "component names")
        return set(COMPONENTS.keys())
    return selected_components


def _reconcile_cli_surface(p, args):
    """Map the canonical CLI surface onto the internal argument namespace.

    Everything downstream of ``parse_args`` consumes the historic
    names: ``emit_selection`` (written here in its raw comma-separated
    *string* form — the gates that run after this function re-parse it
    and replace it with the final ``set``), the ``combine`` boolean,
    the ``otel_enabled`` /
    ``otel_emit_gauges`` / ``otel_gauges_only`` toggles, and the
    per-signal endpoint/token sextet. Since the post-phase-9 CLI flag
    day removed the deprecated alias flags, the canonical flags
    (``--emit``, ``--otel-send``, ``--otel-endpoint``,
    ``--otel-auth-token``) are the only writers besides the
    ``p.set_defaults`` baselines and the ``MEZMO_OTEL_*`` env vars;
    this function translates them — immediately after parsing and
    before any validation gate — so every gate consumes one namespace.
    """
    # ------------------------------------------------------------------
    # --emit -> emit_selection (+ combine via the 'combined' token).
    # ------------------------------------------------------------------
    if args.emit is not None:
        tokens = {t.strip().lower() for t in args.emit.split(",") if t.strip()}
        allowed = {"metrics", "logs", "traces", "gauges", "schema", "combined"}
        invalid = sorted(tokens - allowed)
        if invalid:
            p.error("--emit contains invalid value(s): "
                    f"{', '.join(invalid)}. "
                    "Allowed: metrics,logs,traces,gauges,schema,combined")
        if not tokens:
            p.error("--emit must contain at least one of "
                    "metrics,logs,traces,gauges,schema,combined")
        if "combined" in tokens:
            args.combine = True
            tokens.discard("combined")
            if not tokens:
                p.error(
                    "--emit 'combined' joins the per-component CSVs, so it "
                    "requires the generated artifacts alongside it — "
                    "include 'metrics' (e.g. --emit metrics,combined)"
                )
        # Early twin of the post-reconcile gate below, so the error
        # points at the exact --emit value the user typed. (gauges.csv
        # derives from the per-component CSVs that only 'metrics'
        # writes.)
        if "gauges" in tokens and "metrics" not in tokens:
            p.error("--emit 'gauges' requires 'metrics' in the selection "
                    "(gauges.csv derives from the per-component CSVs)")
        args.emit_selection = ",".join(sorted(tokens))

    # ------------------------------------------------------------------
    # --otel-send -> otel_enabled / otel_emit_gauges / otel_gauges_only.
    # ------------------------------------------------------------------
    send_tokens = None
    if args.otel_send is not None:
        send_tokens = {
            t.strip().lower() for t in args.otel_send.split(",") if t.strip()
        }
        allowed = {"logs", "metrics", "traces", "gauges", "all", "none"}
        invalid = sorted(send_tokens - allowed)
        if invalid:
            p.error("--otel-send contains invalid value(s): "
                    f"{', '.join(invalid)}. "
                    "Allowed: logs, metrics, traces, gauges, all, none")
        if not send_tokens:
            p.error("--otel-send must contain at least one of "
                    "logs,metrics,traces,gauges (or 'all' / 'none')")
        if "none" in send_tokens:
            if send_tokens != {"none"}:
                p.error("--otel-send 'none' cannot be combined with other "
                        "signals")
            # Explicit off, overriding any env-var endpoint defaults.
            args.otel_enabled = False
            args.otel_emit_gauges = False
            args.otel_gauges_only = False
            # Clear env-provided per-signal endpoints/tokens: 'none' must
            # be truly off — leaving them would route the values into the
            # endpoint-shape validation below, so a malformed shell
            # export could fail a run the user explicitly disabled.
            for _sig in ("logs", "metrics", "traces"):
                setattr(args, f"otel_{_sig}_endpoint", None)
                setattr(args, f"otel_{_sig}_auth_token", None)
            send_tokens = set()
        else:
            if "all" in send_tokens:
                send_tokens = {"logs", "metrics", "traces", "gauges"}
            args.otel_enabled = True
            args.otel_emit_gauges = "gauges" in send_tokens
            args.otel_gauges_only = send_tokens == {"gauges"}
            # The anomaly-signal selection, minus the gauge stream:
            # main() filters stream_otel_signals' endpoint dict by this
            # so that e.g. --otel-send logs,gauges derives the metrics
            # ENDPOINT (the gauge stream posts there) without leaking
            # the anomaly-count metrics SIGNAL.
            args.otel_signal_selection = frozenset(send_tokens - {"gauges"})

    # ------------------------------------------------------------------
    # --otel-endpoint / --otel-auth-token -> the per-signal sextet.
    # ------------------------------------------------------------------
    if args.otel_endpoint is not None or args.otel_auth_token is not None:
        if send_tokens is None:
            flag = ("--otel-endpoint" if args.otel_endpoint is not None
                    else "--otel-auth-token")
            p.error(f"{flag} requires --otel-send")
    base = None
    if args.otel_endpoint is not None:
        if not args.otel_endpoint.startswith(("http://", "https://")):
            p.error("--otel-endpoint must start with http:// or https://")
        base = args.otel_endpoint.rstrip("/")
    wanted = None
    if send_tokens:
        wanted = {s for s in ("logs", "metrics", "traces")
                  if s in send_tokens}
        if "gauges" in send_tokens:
            # The gauge stream posts to the metrics endpoint.
            wanted.add("metrics")
    if wanted is not None:
        for sig in ("logs", "metrics", "traces"):
            if sig in wanted:
                # An explicitly typed --otel-endpoint base beats the
                # MEZMO_OTEL_*_ENDPOINT env-var default (a stale shell
                # export must not silently hijack typed input); the env
                # var supplies the per-signal value when no base is
                # given. Same ladder for the token.
                if base is not None:
                    setattr(args, f"otel_{sig}_endpoint", f"{base}/v1/{sig}")
                if args.otel_auth_token is not None:
                    setattr(args, f"otel_{sig}_auth_token",
                            args.otel_auth_token)
            else:
                # --otel-send is authoritative for signal selection:
                # env-var endpoint AND token defaults for unselected
                # signals are cleared so a configured-but-unselected
                # signal cannot leak into the stream and a dangling
                # credential is not carried in the namespace (matching
                # the stricter clearing the 'none' branch does).
                setattr(args, f"otel_{sig}_endpoint", None)
                setattr(args, f"otel_{sig}_auth_token", None)
    if send_tokens:
        if not any([args.otel_logs_endpoint, args.otel_metrics_endpoint,
                    args.otel_traces_endpoint]):
            p.error("--otel-send requires --otel-endpoint (or a per-signal "
                    "endpoint via MEZMO_OTEL_*_ENDPOINT)")


def parse_args(argv=None):
    raw_argv = list(sys.argv[1:]) if argv is None else list(argv)
    # ``--help-all`` rebuilds the help view with the advanced flags
    # un-hidden, then renders help. Handled before argparse so the
    # brief parser never needs to know the flag exists as an action.
    show_all = _flag_in_argv(raw_argv, "--help-all")
    if show_all:
        raw_argv = ["--help"]
    argv = raw_argv

    p = argparse.ArgumentParser(
        description="Generate synthetic IoT metric logs with anomalies.",
        # Abbreviated flags (--emit-sel, --otel-en, ...) would bypass the
        # canonical/alias mixing checks and the deprecation notices, which
        # scan raw argv for exact spellings. Exact flags only.
        allow_abbrev=False,
        epilog=(
            "Subcommands: 'generate' (the default when no subcommand is "
            "given), 'combine DIR' (join existing per-component CSVs into "
            "combined_metrics_unified.csv), 'validate DIR [--warn]' "
            "(check artifacts against DIR/schema.json), and 'serve' "
            "(start the Kubernetes/Helm simulator server). This help "
            "shows the common surface; run with --help-all to also list "
            "the advanced knobs."
        ),
    )
    g_common = p.add_argument_group("common")
    g_anom = p.add_argument_group("anomaly selection")
    g_shape = p.add_argument_group("dataset shape")
    g_art = p.add_argument_group("artifacts")
    g_otel = p.add_argument_group("OTEL streaming")
    g_adv = p.add_argument_group(
        "advanced",
        "Hidden from -h; shown here via --help-all.",
    )

    g_art.add_argument(
        "--emit",
        type=str,
        default=None,
        metavar="ARTIFACTS",
        help="Comma-separated artifact selection: metrics, logs, traces, "
             "gauges, schema, combined (default: metrics,logs,traces). "
             "'gauges' writes a long-form gauges.csv and requires "
             "'metrics'; 'schema' writes a declarative schema.json "
             "consumed by the validate subcommand; 'combined' "
             "additionally joins the per-component CSVs into "
             "combined_metrics_unified.csv after generation (requires "
             "'metrics').",
    )
    g_otel.add_argument(
        "--otel-send",
        type=str,
        default=None,
        metavar="SIGNALS",
        help="Enable OTLP/HTTP streaming and select what to send: a "
             "comma-separated subset of logs, metrics, traces, gauges — "
             "or 'all', or 'none' (explicitly off, overriding env "
             "defaults). logs/metrics/traces replay anomaly events to the "
             "matching signal endpoint; 'gauges' streams per-row metric "
             "values as Gauge data points to the metrics endpoint "
             "(requires the 'metrics' artifact). '--otel-send gauges' "
             "alone skips the anomaly signal stream entirely.",
    )
    g_otel.add_argument(
        "--otel-endpoint",
        type=str,
        default=None,
        metavar="BASE_URL",
        help="OTLP/HTTP base endpoint (e.g. http://localhost:4318). "
             "Per-signal URLs are derived as BASE/v1/logs, BASE/v1/metrics, "
             "BASE/v1/traces for the signals selected by --otel-send. "
             "This derivation beats the MEZMO_OTEL_*_ENDPOINT env vars "
             "(they supply per-signal defaults when no base is given).",
    )
    g_otel.add_argument(
        "--otel-auth-token",
        type=str,
        default=None,
        metavar="TOKEN",
        help="Auth token applied to every selected signal endpoint "
             "(scheme via --otel-stream-auth-scheme, default Bearer). "
             "This token beats the MEZMO_OTEL_*_AUTH_TOKEN env vars "
             "(they supply per-signal defaults when this flag is not "
             "given).",
    )
    g_common.add_argument("--duration-days", type=float, default=DEFAULT_DURATION_DAYS,
                   help=f"Number of days of metrics to generate "
                        f"(default: {DEFAULT_DURATION_DAYS!r}, "
                        f"which yields {DEFAULT_ROW_COUNT:,} rows at the "
                        f"default {DEFAULT_INTERVAL_SECONDS:g}s interval). "
                        "Each scenario's ``days_required`` is the minimum value at which "
                        "any of its specs become in range; the full multi-day catalog "
                        f"manifests at {max(s.days_required for s in SCENARIOS.values())}+.")
    g_common.add_argument("--seed", type=int, default=DEFAULT_SEED,
                   help=f"RNG seed for deterministic output (default: {DEFAULT_SEED}).")
    g_common.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR,
                   help=f"Directory to write CSV files into (default: {DEFAULT_OUTPUT_DIR}).")
    g_shape.add_argument("--drop-rate", type=float, default=DEFAULT_DROP_RATE,
                   help=f"Per-row probability of dropping the row entirely from the per-component CSV "
                        f"(no row is emitted for that timestamp). Simulated packet loss "
                        f"(default: {DEFAULT_DROP_RATE}).")
    g_shape.add_argument("--interval-seconds", type=float, default=DEFAULT_INTERVAL_SECONDS,
                   help=f"Seconds between consecutive emitted rows "
                        f"(default: {DEFAULT_INTERVAL_SECONDS}). Controls sampling "
                        f"density; timeline coverage stays --duration-days * 86400 "
                        f"seconds. Row count per component is floor(total_seconds / interval). "
                        f"Must be >= 0.001 (millisecond precision floor). "
                        f"Values >= 1.0 emit second-precision timestamps "
                        f"(YYYY-MM-DD HH:MM:SS); values < 1.0 emit millisecond-precision "
                        f"timestamps (YYYY-MM-DD HH:MM:SS.SSS) to keep adjacent rows unique.")
    # Internal namespace baselines for the dests the canonical flags
    # translate onto via _reconcile_cli_surface. The flags that used to
    # write these directly (--combine, --emit-selection) were removed at
    # the post-phase-9 CLI flag day; the dests survive because every
    # downstream gate and main() consumer reads them.
    p.set_defaults(combine=False)
    p.set_defaults(emit_selection="metrics,logs,traces")
    g_shape.add_argument(
        "--components",
        type=str,
        default="all",
        help="Comma-separated list of component names to emit (CSV files, "
             "anomalies.csv, reporting artifacts, and OTel streaming). Use "
             "'all' (default) for every component. Allowed names: "
             f"{', '.join(sorted(COMPONENTS.keys()))}.",
    )
    g_anom.add_argument(
        "--scenarios",
        type=str,
        default="all",
        help="Comma-separated list of named scenario slugs to include. Use "
             "'all' (default) to include every scenario in the registry. "
             "Case-insensitive. Known slugs: "
             f"{', '.join(sorted(SCENARIOS.keys()))}.",
    )
    g_anom.add_argument(
        "--exclude-scenarios",
        type=str,
        default="",
        help="Comma-separated list of named scenario slugs to exclude from "
             "the resolved set (applied after --scenarios). Case-insensitive. "
             "Defaults to empty (no exclusion).",
    )
    g_anom.add_argument(
        "--signal-level",
        type=str,
        default=DEFAULT_SIGNAL_LEVEL,
        help="Anomaly intensity level: low, medium (default), or high. "
             "low keeps only benign baseline shifts; medium keeps the standard "
             "catalog (today's behavior); high additionally activates the "
             "high-pressure cross-component scenarios.",
    )
    g_adv.add_argument(
        "--anomaly-count",
        type=int,
        default=None,
        help="Optional cap on the total number of anomalies (including "
             "cascades) injected across the whole dataset. Sampling is "
             "deterministic for a given --seed. Defaults to unlimited.",
    )
    g_shape.add_argument(
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
    g_adv.add_argument(
        "--allow-huge-output",
        action="store_true",
        default=False,
        help=f"Bypass the preflight cell-count cap "
             f"({PREFLIGHT_CELL_CAP:,} metric cells across all "
             f"components, timestamps, and instances). Without this flag, "
             f"parse_args rejects combinations of --interval-seconds, "
             f"--duration-days, --metrics-per-component, --components, and "
             f"--instances-per-component that would emit more cells than "
             f"the cap. Pass this flag when the size is intentional.",
    )
    # OTEL streaming state: --otel-send is the only writer (the five
    # toggle aliases were removed at the CLI flag day, and the
    # MEZMO_OTEL_EMIT_GAUGES env default went with them — once the
    # selection became the only enable path it was authoritative over
    # the env var, which therefore could never take effect).
    p.set_defaults(otel_enabled=False)
    p.set_defaults(otel_emit_gauges=False)
    p.set_defaults(otel_gauges_only=False)
    g_adv.add_argument(
        "--otel-gauge-batch-seconds",
        type=int,
        default=60,
        help="Number of consecutive timestamp ticks (in seconds of timeline coverage, "
             "not wall-clock) coalesced into one OTLP request when the gauge stream "
             "is selected (--otel-send with 'gauges'). Default: 60. Larger batches mean fewer HTTP requests but bigger "
             "bodies; tune for your OTLP collector body limit.",
    )
    g_adv.add_argument(
        "--otel-gauge-metric-prefix",
        type=str,
        default="",
        help="Optional namespace prefix prepended to the OTLP metric name for each "
             "gauge data point (e.g. 'amc.' produces 'amc.cpu_util_pct'). Default: "
             "empty (use the raw MetricSpec.name).",
    )
    # Per-signal endpoint/token internal namespace. The six per-signal
    # flags were removed at the CLI flag day; the MEZMO_OTEL_* env vars
    # remain the per-signal override mechanism (an explicitly typed
    # --otel-endpoint base beats them for the selected signals — see
    # _reconcile_cli_surface).
    p.set_defaults(
        otel_logs_endpoint=os.environ.get("MEZMO_OTEL_LOGS_ENDPOINT"),
        otel_logs_auth_token=os.environ.get("MEZMO_OTEL_LOGS_AUTH_TOKEN"),
        otel_metrics_endpoint=os.environ.get("MEZMO_OTEL_METRICS_ENDPOINT"),
        otel_metrics_auth_token=os.environ.get("MEZMO_OTEL_METRICS_AUTH_TOKEN"),
        otel_traces_endpoint=os.environ.get("MEZMO_OTEL_TRACES_ENDPOINT"),
        otel_traces_auth_token=os.environ.get("MEZMO_OTEL_TRACES_AUTH_TOKEN"),
    )
    g_otel.add_argument(
        "--otel-stream-speedup",
        type=float,
        default=3600.0,
        help="Timeline replay speed multiplier for OTEL streaming (default: 3600). "
             "1.0 = real-time, 3600 = one hour of anomaly spacing per second.",
    )
    g_adv.add_argument(
        "--otel-stream-timeout-seconds",
        type=float,
        default=5.0,
        help="HTTP timeout per OTEL streamed event in seconds (default: 5).",
    )
    g_adv.add_argument(
        "--otel-stream-max-events",
        type=int,
        default=None,
        help="Optional cap on streamed HTTP attempt count (default: all). For the "
             "anomaly-counter stream this caps the number of anomaly events sent. "
             "For the gauge stream (--otel-send with 'gauges') it caps the number of "
             "OTLP request *attempts* (not data points and not successes) — a broken "
             "endpoint that 500s every request still trips the cap at N. Both streams "
             "honor the same flag independently in one run.",
    )
    g_adv.add_argument(
        "--otel-stream-auth-scheme",
        type=str,
        default=os.environ.get("MEZMO_OTEL_STREAM_AUTH_SCHEME", DEFAULT_OTEL_STREAM_AUTH_SCHEME),
        help="Auth scheme prefix for OTEL auth token (default: Bearer). "
             "Env override: MEZMO_OTEL_STREAM_AUTH_SCHEME.",
    )
    g_otel.add_argument(
        "--otel-stream-protocol",
        type=str,
        default=os.environ.get("MEZMO_OTEL_STREAM_PROTOCOL", "protobuf"),
        help="OTLP payload mode for stream endpoint: json or protobuf (default: protobuf). "
             "Env override: MEZMO_OTEL_STREAM_PROTOCOL.",
    )
    g_adv.add_argument(
        "--otel-activity-log",
        type=Path,
        default=Path("otel-activity.log"),
        help="Path to the OTEL streaming activity log file. Records one line per "
             "send attempt, retry, and failure when OTEL streaming is on. The file "
             "is only created when streaming actually runs. "
             "Default: ./otel-activity.log in the current directory.",
    )
    g_adv.add_argument(
        "--otel-verbose",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Include raw OTLP payload bodies, request headers, response status, "
             "and exception types in the activity log for each SEND/OK/RETRY/FAIL "
             "record. Authorization header values are masked. Default: off.",
    )
    g_adv.add_argument("--inject-dst-artifact-day", type=int, default=0,
                   help="Inject a fall-DST artifact (duplicated 02:00–02:59 wall-clock hour) "
                        "on the given 1-based day of the run. 0 (default) disables. Generator "
                        "quirk, not an anomaly spec — does not appear in anomalies.csv. The "
                        "affected CSVs end up with 3,600/interval extra rows for that day.")
    instance_source = g_shape.add_mutually_exclusive_group()
    instance_source.add_argument(
        "--instances-per-component",
        type=int,
        default=1,
        metavar="N",
        help=f"Fan each component out to N identical instances (default 1). "
             f"N=1 emits today's byte-identical output with no dimension columns. "
             f"N>1 prepends id, host, pod, az, region, tenant columns to every "
             f"per-component CSV and emits N×rows_per_component rows. "
             f"Accepted range: [1, {MAX_INSTANCES_PER_COMPONENT}]. "
             f"Mutually exclusive with --instance-config.",
    )
    instance_source.add_argument(
        "--instance-config",
        type=Path,
        default=None,
        metavar="PATH",
        help="YAML (.yaml/.yml) or JSON (.json) file declaring a per-component "
             "instance topology for repeatable non-uniform fan-outs. "
             "Top-level key 'components' maps component names to lists of "
             "Instance field dicts (id, host, pod, az, region, tenant). "
             "Components not listed in the file fall back to the module-level "
             "INSTANCES registry (today: a single anonymous Instance() per "
             "component). Mutually exclusive with --instances-per-component.",
    )
    # Brief-help mode hides the advanced knobs; --help-all shows
    # everything. Walks the parser's actions once after construction so
    # each flag is defined exactly once above.
    if not show_all:
        for action in p._actions:
            if action.dest in _ADVANCED_DESTS:
                action.help = argparse.SUPPRESS

    args = p.parse_args(argv)

    # Default: no signal filtering (every configured endpoint streams;
    # only reachable for programmatic namespaces — flag-level streaming
    # always routes through --otel-send). _reconcile_cli_surface
    # overrides this with the --otel-send selection when given.
    args.otel_signal_selection = None

    _reconcile_cli_surface(p, args)

    if not math.isfinite(args.duration_days):
        p.error("--duration-days must be a finite number")
    if args.duration_days < 1:
        p.error("--duration-days must be >= 1")
    if not 0.0 <= args.drop_rate <= 1.0:
        p.error("--drop-rate must be between 0 and 1")
    # np.random.RandomState accepts seeds in [0, 2**32). An out-of-range
    # value (e.g. --seed -1) used to crash later in main() with a raw
    # numpy ValueError traceback instead of a clean usage error.
    if not 0 <= args.seed < 2**32:
        p.error("--seed must be in [0, 2**32) (numpy RandomState range)")
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
    # rows in the combine step (the original failure mode).
    if args.interval_seconds < 0.001:
        p.error("--interval-seconds must be >= 0.001 (ms-precision floor)")
    if args.inject_dst_artifact_day < 0:
        p.error("--inject-dst-artifact-day must be >= 0 (0 disables)")
    if args.inject_dst_artifact_day > args.duration_days:
        p.error(f"--inject-dst-artifact-day {args.inject_dst_artifact_day} "
                f"is outside the configured --duration-days {args.duration_days}")
    # Re-parse the reconciled emit_selection string into the final set.
    # The token vocabulary was already gated when --emit was given; this
    # pass re-checks it so a programmatic namespace (or a future default
    # change) cannot smuggle an unknown token past the gates.
    selected = {item.strip().lower() for item in args.emit_selection.split(",") if item.strip()}
    allowed = {"metrics", "logs", "traces", "gauges", "schema"}
    invalid = sorted(selected - allowed)
    if invalid:
        p.error("--emit contains invalid value(s): "
                f"{', '.join(invalid)}. "
                "Allowed: metrics,logs,traces,gauges,schema "
                "(plus 'combined', consumed at --emit parse time)")
    if not selected:
        p.error("--emit must contain at least one of "
                "metrics,logs,traces,gauges,schema")
    if args.combine and "metrics" not in selected:
        p.error("--emit 'combined' requires 'metrics' in the selection "
                "(e.g. --emit metrics,combined)")
    # ``gauges`` is derived from the per-component CSVs written under
    # ``metrics`` (same input as the OTEL gauge stream). Without ``metrics``,
    # the per-component CSVs are not written this run, so we have nothing to
    # derive ``gauges.csv`` from. Reject up-front with a clear message.
    if "gauges" in selected and "metrics" not in selected:
        p.error("--emit 'gauges' requires 'metrics' in the selection")
    if args.otel_gauges_only:
        args.otel_emit_gauges = True
    if args.otel_emit_gauges:
        if args.otel_send is not None:
            gauge_flag = ("--otel-send gauges" if args.otel_gauges_only
                          else "--otel-send with 'gauges'")
        else:
            # No flag or env var writes otel_emit_gauges anymore; only a
            # programmatic namespace can reach this branch.
            gauge_flag = "the gauge stream (otel_emit_gauges)"
        if not args.otel_enabled:
            p.error(f"{gauge_flag} requires --otel-send to enable streaming")
        if not args.otel_metrics_endpoint:
            p.error(f"{gauge_flag} requires a metrics endpoint "
                    "(via --otel-endpoint or MEZMO_OTEL_METRICS_ENDPOINT)")
        if "metrics" not in selected:
            p.error(f"{gauge_flag} requires --emit to include 'metrics'")
    # Both gauge paths (``--otel-send ...,gauges`` and ``--emit ...,gauges``)
    # feed per-component CSVs into ``heapq.merge``, which requires each input
    # iterator to be sorted by the timestamp key.
    # ``--inject-dst-artifact-day`` deliberately duplicates the 02:00–02:59
    # wall-clock hour inside each CSV (see ``_splice_dst_artifact``),
    # producing non-monotonic timestamps that silently break batching, OTLP
    # payloads, and the merged ``gauges.csv`` ordering. Reject the
    # combination at parse time for both paths — real OTLP consumers wouldn't
    # tolerate the artifact either, so there's no realistic user for it.
    if args.inject_dst_artifact_day > 0 and (
        args.otel_emit_gauges or "gauges" in selected
    ):
        flags = []
        if args.otel_gauges_only:
            flags.append("--otel-send gauges")
        elif args.otel_emit_gauges:
            flags.append("--otel-send with 'gauges'")
        if "gauges" in selected:
            flags.append("--emit 'gauges'")
        p.error(
            f"{' / '.join(flags)} is incompatible with --inject-dst-artifact-day "
            "(the DST artifact produces non-monotonic CSV timestamps that break "
            "the heapq.merge over per-component CSVs); pass "
            "--inject-dst-artifact-day 0 or drop the gauge emission flag"
        )
    # Validate ``--instances-per-component`` range *before* any N>1 gating
    # so an out-of-range value (e.g. 0 or 999) surfaces the range error
    # rather than masquerading as an incompatibility error when the user
    # also passed --emit combined or another gated flag.
    if (
        args.instances_per_component < 1
        or args.instances_per_component > MAX_INSTANCES_PER_COMPONENT
    ):
        p.error(
            f"--instances-per-component must be in [1, "
            f"{MAX_INSTANCES_PER_COMPONENT}] (1 = default dimensionless "
            f"output; >1 fans out with pod/az/etc. columns)"
        )
    # Validate ``--instance-config`` file path early (before any multi-instance
    # gating) so a missing file or wrong suffix surfaces a clean error rather
    # than as a generic incompatibility.
    if args.instance_config is not None:
        # ``is_file()`` rejects missing paths *and* directories /
        # broken-symlink-style entries in one shot. ``exists()`` would let
        # a directory through and then ``_load_instance_config`` would
        # surface it as an OSError mid-run.
        if not args.instance_config.is_file():
            if args.instance_config.exists():
                p.error(
                    f"--instance-config path is not a regular file: "
                    f"{args.instance_config}"
                )
            p.error(f"--instance-config path does not exist: {args.instance_config}")
        if args.instance_config.suffix.lower() not in {".yaml", ".yml", ".json"}:
            p.error(
                f"--instance-config must be a .yaml, .yml, or .json file; "
                f"got {args.instance_config.suffix!r}"
            )
    # Phase 3: --instance-config triggers the same multi-instance
    # code path as --instances-per-component > 1 (dimension columns,
    # N×rows per component, partial-aware downstream emitters). Both flags
    # must be gated identically against incompatible downstream flags so
    # the user gets one error message, not two divergent ones.
    _multi_instance = (
        args.instances_per_component > 1 or args.instance_config is not None
    )
    _multi_instance_flag = (
        "--instance-config" if args.instance_config is not None
        else "--instances-per-component > 1"
    )
    if _multi_instance and args.inject_dst_artifact_day > 0:
        p.error(
            f"{_multi_instance_flag} is incompatible with --inject-dst-artifact-day "
            "by design (per-instance DST splicing produces non-monotonic "
            "timestamps inside each long-form row block, which downstream "
            "long-form merges in gauges.csv / combined_metrics_unified.csv "
            "cannot resolve); pass --inject-dst-artifact-day 0 or use the "
            "default single-instance mode"
        )
    # Multi-instance dimension-awareness status by emitter (post-Phase-8):
    #
    # - File-form long-form writers (``gauges.csv`` /
    #   ``combined_metrics_unified.csv``): wired in Phase 5.
    #   Header inspection dispatches to a 10-column layout when the
    #   per-component CSVs carry the ``id, host, pod, az, region,
    #   tenant`` prefix; the historic 4-column / wide layouts stay
    #   byte-identical when the prefix is absent.
    # - OTEL streaming (``--otel-send``):
    #   wired in Phase 6. ``stream_otel_gauges`` and
    #   ``stream_otel_signals`` lift the dimension columns off each
    #   row and surface them as string attributes on every OTLP data
    #   point.
    # - Schema/validator (``--emit ...,schema`` /
    #   the ``validate`` subcommand): wired in Phase 8.
    #   ``schema.json`` declares a per-component ``dimensions`` block
    #   when the run is dim-aware and the validator's
    #   ``_validate_component_cells`` / ``_validate_component_row_count``
    #   / new ``_validate_long_form_dimensions`` honor it end-to-end.
    #
    # No multi-instance gate fires here anymore; the only remaining
    # downstream-flag rejection is the DST guard above.
    if args.otel_gauge_batch_seconds <= 0:
        p.error("--otel-gauge-batch-seconds must be > 0")
    # The OTEL stream scalar checks run unconditionally (matching
    # --otel-gauge-batch-seconds above): a bad value used to be silently
    # accepted whenever no endpoint was configured, so e.g.
    # `--otel-stream-speedup -5` could sit unnoticed in a wrapper script
    # until the day an endpoint was added. Endpoint-shape and token
    # checks stay inside the endpoint conditional — they validate the
    # endpoint values themselves.
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
    if any([args.otel_logs_endpoint, args.otel_metrics_endpoint, args.otel_traces_endpoint]):
        endpoints = [
            ("logs", args.otel_logs_endpoint, args.otel_logs_auth_token),
            ("metrics", args.otel_metrics_endpoint, args.otel_metrics_auth_token),
            ("traces", args.otel_traces_endpoint, args.otel_traces_auth_token),
        ]
        for signal, endpoint, token in endpoints:
            if endpoint:
                if not endpoint.startswith(("http://", "https://")):
                    p.error(f"the {signal} OTLP endpoint must start with "
                            "http:// or https:// (check --otel-endpoint or "
                            f"MEZMO_OTEL_{signal.upper()}_ENDPOINT)")
                if token and not token.strip():
                    p.error(f"the {signal} OTLP auth token must be non-empty "
                            "when provided (check --otel-auth-token or "
                            f"MEZMO_OTEL_{signal.upper()}_AUTH_TOKEN)")
    args.emit_selection = selected

    args.components = _parse_components_value(p.error, args.components)

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

    # Preflight cell-count cap. ``--interval-seconds 0.001`` with default flags
    # would emit 86.4M rows * ~75 default metrics = ~6.5B cells; large
    # combinations of the four knobs below silently chew through tens of GB
    # of memory and runtime before the user notices. The cost the cap
    # protects against is the in-memory ``np.empty((n_rows, n_cols),
    # float64)`` allocation and vectorized math inside ``generate_component``
    # (~52 GB of RAM at 6.5B cells), not just the on-disk CSV size. Disk
    # output is gated by ``emit_metrics`` but the matrix work runs
    # unconditionally for every component in ``args.components`` — so the
    # cap must apply on every code path that reaches ``generate_component``,
    # including ``--emit logs`` / ``--emit traces``
    # runs where no per-component CSV is written. Skipping the cap when
    # ``"metrics" not in args.emit_selection`` would invite OOMs without
    # saving any allocation or compute. (The ``combine`` subcommand never
    # routes through ``parse_args``, so the combine-over-a-huge-dataset
    # path is structurally exempt.)
    #
    # Mirror the generator's row-count derivation byte-for-byte. main()
    # computes ``total_seconds = SECONDS_PER_DAY * args.duration_days``
    # and ``n_rows = int(total_seconds // args.interval_seconds)``; use
    # the same two expressions here so the preflight estimate cannot
    # diverge from the row count actually emitted by generate_component.
    total_seconds = SECONDS_PER_DAY * args.duration_days
    rows_per_component = int(total_seconds // args.interval_seconds)
    if args.metrics_per_component is None:
        total_metrics = sum(
            DEFAULT_METRICS_PER_COMPONENT[c] for c in args.components
        )
    else:
        total_metrics = sum(
            min(args.metrics_per_component, len(COMPONENTS[c]))
            for c in args.components
        )
    # Multiply by n_instances per component (Phase 2/3). For
    # --instances-per-component: uniform N across all components.
    # For --instance-config: the per-component count is not yet parsed
    # here (that happens in main()), so use the max declared count
    # (MAX_INSTANCES_PER_COMPONENT) as a conservative upper bound.
    # Both flags are mutually exclusive so only one branch fires.
    if args.instance_config is not None:
        n_instances_factor = MAX_INSTANCES_PER_COMPONENT  # conservative
    else:
        n_instances_factor = args.instances_per_component
    estimated_cells = rows_per_component * total_metrics * n_instances_factor
    if estimated_cells > PREFLIGHT_CELL_CAP and not args.allow_huge_output:
        instance_clause = (
            f"x --instance-config (≤{MAX_INSTANCES_PER_COMPONENT} instances/component) "
            if args.instance_config is not None
            else f"x --instances-per-component {args.instances_per_component} "
        )
        p.error(
            f"preflight cell-count cap exceeded: "
            f"--interval-seconds {args.interval_seconds} "
            f"x --duration-days {args.duration_days} "
            f"x --components ({len(args.components)} selected) "
            f"x --metrics-per-component "
            f"{args.metrics_per_component if args.metrics_per_component is not None else 'default'} "
            f"{instance_clause}"
            f"would emit ~{estimated_cells:,} metric cells "
            f"(cap: {PREFLIGHT_CELL_CAP:,}). "
            f"Raise --interval-seconds, lower --duration-days, lower "
            f"--metrics-per-component, narrow --components, reduce instances, "
            f"or pass --allow-huge-output to bypass."
        )

    return args


# ------------------------------------------------------------------
# Combine step: join per-component CSVs into a single unified CSV.
# One row per timestamp; columns prefixed with the component name.
# ------------------------------------------------------------------
_NON_COMPONENT_FILES = {"anomalies.csv", "gauges.csv"}

# Filenames written into --output-dir for each --emit item.
# Per-component CSVs are derived from args.components, not listed here.
# Consumed by _pre_clean_output_dir() and by the end-of-run summary line.
_EMIT_ARTIFACT_FILES = {
    "metrics": ("anomalies.csv",),
    "logs": ("metric_report.log",),
    "traces": ("metric_traces.jsonl",),
    "gauges": ("gauges.csv",),
    "schema": ("schema.json",),
}
# Written only when the 'combined' artifact is selected (which itself
# requires "metrics" in --emit). Tracked separately so the pre-clean and summary can
# treat it as its own slot.
_COMBINE_OUTPUT_FILENAME = "combined_metrics_unified.csv"


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


def combine_logs_unified(
    components,
    input_dir,
    output_file=None,
    *,
    assume_monotonic_wide_components=None,
):
    """Join the per-component CSVs in ``input_dir`` into a single unified CSV.

    ``output_file`` defaults to ``input_dir/combined_metrics_unified.csv``.
    Returns ``(total_rows, size_mb)``. ``assume_monotonic_wide_components``
    is an optional allowlist for trusted, freshly-generated wide CSVs whose
    rows are known to be timestamp-monotonic, letting the normal non-DST
    generation path avoid a second full-file scan. Components not in that set
    still take the conservative scan so hand-staged/autodiscovered CSVs keep
    the same safety behavior as direct ``combine`` calls.

    Layout is chosen by header inspection of the per-component CSVs:

    - If every per-component CSV is dimensionless (the first column is
      ``timestamp`` followed directly by the metric columns — the
      default ``N=1`` anonymous-instance shape), the writer emits the
      wide ``timestamp,component_a_m0,component_a_m1,...`` layout
      byte-identically to the pre-existing output (so existing
      ``test_combine.py`` row/column-shape assertions continue to hold).
    - If **any** per-component CSV carries the full ``id, host, pod, az,
      region, tenant`` dimension prefix after ``timestamp``, the writer
      switches to a long layout: ``timestamp,component,id,host,pod,az,
      region,tenant,metric,value``. Rows are emitted in
      ``(timestamp, component, instance_id, metric)`` tie-break order
      via ``heapq.merge`` across per-(component, instance) iterators,
      matching the long-form ``gauges.csv`` ordering contract. Empty /
      dropped cells are skipped — long form encodes "this measurement
      was emitted" explicitly via row presence.

    The dispatch is purely header-based, so any path that produces
    dimensioned per-component CSVs routes here — ``--instances-per-
    component N > 1`` (the Phase-2 fan-out) is the canonical path, but
    ``--instance-config`` can also produce a dimensioned single-instance
    CSV and lands the same long-form output.

    Missing per-component CSVs raise ``SystemExit`` in both branches —
    the wide path checks first via ``_scan_component_csv_headers``'s
    ``layout[c]["exists"]`` flags so direct callers get a consistent
    user-facing error instead of an unhandled ``FileNotFoundError``
    later in the loop.
    """
    input_dir = Path(input_dir)
    if output_file is None:
        output_file = input_dir / _COMBINE_OUTPUT_FILENAME
    output_file = Path(output_file)

    component_csv_paths = {c: input_dir / f"{c}.csv" for c in components}
    any_dimensioned, layout = _scan_component_csv_headers(component_csv_paths)

    # Mirror ``_write_combined_long_form``'s missing-file guard for the
    # wide-form path so direct callers of ``combine_logs_unified`` see a
    # consistent user-facing error regardless of which branch they hit.
    # ``combine_logs`` already raises on missing files when invoked with
    # an explicit ``components`` allowlist, so this check is dead on the
    # main pipeline; it covers a direct caller that bypasses
    # ``combine_logs`` and lands a missing file in the layout.
    missing = [
        f"{name}.csv" for name in components
        if not layout[name]["exists"]
    ]
    if missing:
        raise SystemExit(
            f"missing component CSVs for combine: {', '.join(missing)}"
        )

    print("\nCreating UNIFIED format combined file...")
    print(f"Components discovered: {', '.join(components)}")

    if any_dimensioned:
        total_rows = _write_combined_long_form(
            components, layout, output_file,
        )
        size_mb = os.path.getsize(output_file) / (1024 * 1024)
        print(f"\nUnified format file created: {output_file}")
        print(f"Total rows: {total_rows:,}")
        print(f"File size: {size_mb:.2f} MB")
        return total_rows, size_mb

    component_metrics = {}
    row_streams = []
    nonmonotonic_components = []
    trusted_monotonic_components = (
        set(assume_monotonic_wide_components)
        if assume_monotonic_wide_components is not None
        else None
    )

    for component in components:
        input_path = input_dir / f"{component}.csv"
        print(f"Loading {component}.csv...")

        with open(input_path, "r", encoding="utf-8") as infile:
            reader = csv.DictReader(infile)
            # The any-dimensioned dispatch above already routed the long-
            # form CSVs into ``_write_combined_long_form``, so by the time
            # control reaches this DictReader path every per-component CSV
            # is the classic dimensionless ``timestamp, m0, m1, ...`` shape
            # and ``fieldnames[1:]`` is the metric list verbatim.
            #
            # ``csv.DictReader.fieldnames`` is ``None`` for a fully empty
            # input, ``[]`` for a file whose first line is blank, and may
            # legitimately omit ``timestamp`` if the user staged a CSV
            # with a different schema. ``combine_logs`` rejects a missing
            # file before we get here, but a present-but-malformed header
            # would otherwise crash either the list comprehension below
            # (``None``) or the ``row["timestamp"]`` lookup in the loop
            # (missing key). Validate all three shapes up-front and raise
            # the same flavor of ``SystemExit`` ``combine_logs`` uses for
            # missing files so the operator gets a clean diagnosis
            # instead of a stack trace.
            if not reader.fieldnames:
                raise SystemExit(
                    f"{input_path.name} is empty / has no header row; "
                    f"combine_logs cannot derive its metric columns"
                )
            if "timestamp" not in reader.fieldnames:
                raise SystemExit(
                    f"{input_path.name} header {list(reader.fieldnames)!r} "
                    f"is missing the 'timestamp' column; combine_logs "
                    f"cannot key per-component rows without it"
                )
            metric_names = [f for f in reader.fieldnames if f != "timestamp"]
            component_metrics[component] = metric_names

        def _iter_component_rows(
            component_name: str = component,
            path: Path = input_path,
            metrics: list[str] = metric_names,
        ):
            previous_timestamp = None
            occurrence = 0
            with open(path, "r", encoding="utf-8", newline="") as infile:
                reader = csv.DictReader(infile)
                for row in reader:
                    timestamp = row["timestamp"]
                    # This generator only feeds the monotonic ``heapq.merge``
                    # path, so any duplicate timestamps (the DST fall-back
                    # 02:00-02:59 wall-clock hour) arrive consecutively. Track
                    # only the previous timestamp + a running occurrence index
                    # (non-DST rows always 0) instead of a full per-timestamp
                    # dict, keeping the stream O(1) in memory.
                    if timestamp == previous_timestamp:
                        occurrence += 1
                    else:
                        occurrence = 0
                        previous_timestamp = timestamp
                    yield (
                        timestamp,
                        occurrence,
                        component_name,
                        {metric: row[metric] for metric in metrics},
                    )

        row_streams.append(_iter_component_rows())
        if (
            trusted_monotonic_components is None
            or component not in trusted_monotonic_components
        ) and not _wide_component_rows_are_monotonic(input_path):
            nonmonotonic_components.append(component)

    fieldnames = ["timestamp"]
    for component in components:
        for metric in component_metrics[component]:
            fieldnames.append(f"{component}_{metric}")

    print(f"Total columns: {len(fieldnames)} (1 timestamp + {len(fieldnames) - 1} metrics)")

    if nonmonotonic_components:
        print(
            "Non-monotonic timestamp stream detected for "
            f"{', '.join(nonmonotonic_components)}; using sorted wide merge."
        )
        total_rows = _write_combined_wide_materialized(
            components, input_dir, output_file, component_metrics, fieldnames,
        )
        size_mb = os.path.getsize(output_file) / (1024 * 1024)
        print(f"\nUnified format file created: {output_file}")
        print(f"Total rows: {total_rows:,}")
        print(f"File size: {size_mb:.2f} MB")
        return total_rows, size_mb

    with open(output_file, "w", encoding="utf-8", newline="") as outfile:
        writer = csv.DictWriter(outfile, fieldnames=fieldnames)
        writer.writeheader()

        total_rows = 0
        current_key = None
        current_components = {}

        def _flush_current_bucket() -> None:
            nonlocal total_rows
            if current_key is None:
                return
            timestamp, _occurrence = current_key
            row = {"timestamp": timestamp}
            for component in components:
                component_row = current_components.get(component, {})
                for metric in component_metrics[component]:
                    row[f"{component}_{metric}"] = component_row.get(metric, "")
            writer.writerow(row)
            total_rows += 1

        for timestamp, occurrence, component, row_values in heapq.merge(*row_streams):
            bucket_key = (timestamp, occurrence)
            if current_key is None:
                current_key = bucket_key
            elif bucket_key != current_key:
                _flush_current_bucket()
                current_key = bucket_key
                current_components = {}
            current_components[component] = row_values

        _flush_current_bucket()

    size_mb = os.path.getsize(output_file) / (1024 * 1024)
    print(f"\nUnified format file created: {output_file}")
    print(f"Total rows: {total_rows:,}")
    print(f"File size: {size_mb:.2f} MB")
    return total_rows, size_mb


def _wide_component_rows_are_monotonic(input_path: Path) -> bool:
    """Return false when a wide component CSV's timestamp keys move backward.

    Duplicate timestamps (the DST fall-back wall-clock hour) are only valid
    when consecutive, so the occurrence index is tracked with a running
    counter against the previous timestamp rather than a full per-timestamp
    dict — keeping the scan O(1) in memory. A non-consecutive duplicate
    necessarily implies an earlier strictly-backward timestamp step, which
    this loop already reports as non-monotonic before the occurrence value
    could diverge from the dict-based count.
    """
    previous_key = None
    previous_timestamp = None
    occurrence = 0
    with open(input_path, "r", encoding="utf-8", newline="") as infile:
        reader = csv.DictReader(infile)
        for row in reader:
            timestamp = row["timestamp"]
            if timestamp == previous_timestamp:
                occurrence += 1
            else:
                occurrence = 0
                previous_timestamp = timestamp
            key = (timestamp, occurrence)
            if previous_key is not None and key < previous_key:
                return False
            previous_key = key
    return True


def _write_combined_wide_materialized(
    components: list[str],
    input_dir: Path,
    output_file: Path,
    component_metrics: dict[str, list[str]],
    fieldnames: list[str],
) -> int:
    """Write the wide combined CSV when inputs require an explicit sort.

    Normal dimensionless runs stream through ``heapq.merge`` above. DST
    artifact injection intentionally moves timestamps backward within each
    component file, so those rare runs need the historical materialized sort to
    preserve duplicate wall-clock hours exactly.
    """
    data_by_timestamp = {}
    for component in components:
        input_path = input_dir / f"{component}.csv"
        seen_in_component = {}
        with open(input_path, "r", encoding="utf-8", newline="") as infile:
            reader = csv.DictReader(infile)
            metric_names = component_metrics[component]
            for row in reader:
                timestamp = row["timestamp"]
                occurrence = seen_in_component.get(timestamp, 0)
                seen_in_component[timestamp] = occurrence + 1
                bucket = data_by_timestamp.setdefault((timestamp, occurrence), {})
                bucket[component] = {
                    metric: row[metric] for metric in metric_names
                }

    with open(output_file, "w", encoding="utf-8", newline="") as outfile:
        writer = csv.DictWriter(outfile, fieldnames=fieldnames)
        writer.writeheader()
        for bucket_key in sorted(data_by_timestamp.keys()):
            timestamp, _occurrence = bucket_key
            row = {"timestamp": timestamp}
            for component in components:
                component_row = data_by_timestamp[bucket_key].get(component, {})
                for metric in component_metrics[component]:
                    row[f"{component}_{metric}"] = component_row.get(metric, "")
            writer.writerow(row)
    return len(data_by_timestamp)


def _write_combined_long_form(
    components: list[str], layout: dict[str, dict], output_file: Path,
) -> int:
    """Write the long-form unified CSV when any per-component CSV carries
    the multi-instance dimension prefix.

    Layout mirrors the long-form ``gauges.csv``: ``timestamp, component,
    id, host, pod, az, region, tenant, metric, value``. Per-(component,
    instance) iterators feed ``heapq.merge`` on parsed timestamps;
    sources are pre-sorted by ``(component, instance_dims)`` (alphabetical
    by component, then by id-leading dim tuple) so equal-timestamp
    groups emit component-then-instance-then-metric order — the
    documented Phase 5 ``(timestamp, component, instance_id, metric)``
    tie-break. The caller-supplied ``components`` order is **not**
    preserved (the function sorts components alphabetically internally)
    so the on-disk component order is deterministic regardless of how
    the caller built the list; this matches the long-form
    ``gauges.csv`` writer's tie-break contract.

    Missing per-component CSVs raise ``SystemExit`` rather than being
    silently skipped — the wide-form path's ``combine_logs`` guard
    raises on the same input, and the long form mirrors that contract
    so the two paths agree on what "missing input" means even when the
    caller bypasses ``combine_logs`` to call this writer directly.
    """
    # Mirror the wide-form path's contract: every requested component
    # must have a per-component CSV on disk. Defends against a direct
    # caller that bypasses ``combine_logs``'s missing-file check; the
    # autodiscovery path through ``combine_logs(...)`` already filters
    # to existing files via ``discover_components``, so this loop is a
    # no-op there.
    missing = [
        f"{name}.csv" for name in components
        if not layout[name]["exists"]
    ]
    if missing:
        raise SystemExit(
            f"missing component CSVs for long-form combine: "
            f"{', '.join(missing)}"
        )
    sources = []
    sorted_components = sorted(components)
    for component in sorted_components:
        entry = layout[component]
        metric_cols = entry["metric_cols"]
        has_dims = bool(entry["dim_cols"])
        instance_blocks = _scan_instance_block_layout(
            entry["path"], has_dims=has_dims,
        )
        for instance_dims, start_offset in instance_blocks:
            row_iter = _iter_component_instance_rows(
                entry["path"], start_offset,
                has_dims=has_dims, n_metrics=len(metric_cols),
            )

            def _tagged(_iter=row_iter, _comp=component,
                        _dims=instance_dims, _cols=metric_cols):
                for ts_dt, ts_raw, values in _iter:
                    yield (ts_dt, ts_raw, _comp, _dims, _cols, values)
            # Sort key carries the full ``instance_dims`` tuple (see
            # the ``write_gauges_csv`` long-form path for the same
            # rationale); ``id`` is the leading field, which yields the
            # documented ``(timestamp, component, instance_id, metric)``
            # tie-break order in v1.
            sources.append(((component, instance_dims), _tagged()))

    # Each source holds an open file handle for the lifetime of the
    # merge. Pre-flight the FD soft limit so high-fan-out runs (e.g.,
    # 14 components × 20 instances = 280 handles) either bump the
    # rlimit up to fit or fail with an actionable message before
    # ``heapq.merge`` tries to prime the heap.
    _ensure_long_form_fd_capacity(len(sources))

    sources.sort(key=lambda item: item[0])
    iters = [src for _key, src in sources]

    rows_written = 0
    with open(output_file, "w", encoding="utf-8", newline="") as out_f:
        writer = csv.writer(out_f, lineterminator="\n")
        writer.writerow(
            ("timestamp", "component", *_INSTANCE_DIMENSION_COLUMNS, "metric", "value")
        )
        for _dt, ts, comp, dims, metric_cols, values in heapq.merge(
            *iters, key=lambda item: item[0]
        ):
            for name, raw in zip(metric_cols, values):
                if raw == "":
                    continue
                writer.writerow((ts, comp, *dims, name, raw))
                rows_written += 1
    return rows_written


def combine_logs(input_dir, components=None, *, assume_monotonic_wide_components=None):
    """Write the unified combined CSV from per-component CSVs in ``input_dir``.

    When ``components`` is ``None``, the combine step autodiscovers every
    ``*.csv`` in ``input_dir`` (excluding the anomalies manifest and prior
    combine outputs). When ``components`` is provided, it is used as the
    allowlist for which CSVs to combine. Any named component whose
    ``{name}.csv`` is missing from ``input_dir`` raises ``SystemExit``.

    Output ordering depends on which layout the underlying
    ``combine_logs_unified`` dispatches to:

    - **Wide layout** (default, dimensionless inputs) — the caller-
      supplied ``components`` order is preserved verbatim in the
      ``component_a_m0, component_a_m1, component_b_m0, …`` column
      sequence.
    - **Long layout** (any dimensioned input, phase 5) — the
      caller-supplied ``components`` order is **not** preserved. Rows
      are merged chronologically and tie-break on
      ``(component, instance_id, metric)`` with components sorted
      alphabetically; the caller-supplied order only filters which
      components participate. The on-disk column shape is fixed
      (``timestamp, component, id, host, pod, az, region, tenant,
      metric, value``), so the order argument has no column-layout
      effect in the long form.
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
    return combine_logs_unified(
        components,
        input_dir,
        assume_monotonic_wide_components=assume_monotonic_wide_components,
    )


def _anomaly_event_id(entry: dict) -> str:
    """Deterministic event id used to correlate metrics, logs, and traces."""
    required = ("timestamp", "component", "metric", "description")
    missing = [k for k in required if not entry.get(k)]
    if missing:
        raise ValueError(f"anomaly entry missing required field(s): {', '.join(missing)}")
    payload = "|".join(str(entry[k]) for k in required)
    return "evt_" + sha1(payload.encode("utf-8")).hexdigest()[:16]


def write_reporting_artifacts(
    output_dir: Path,
    anomaly_rows: list[dict],
    *,
    emit_logs: bool = True,
    emit_traces: bool = True,
) -> None:
    """Emit correlated log and trace artifacts aligned to anomaly metric records.

    ``emit_logs`` / ``emit_traces`` gate which file is written; both default to
    True to preserve the historic two-file behavior for direct callers.
    """
    output_dir = Path(output_dir)
    log_path = output_dir / "metric_report.log"
    trace_path = output_dir / "metric_traces.jsonl"

    with contextlib.ExitStack() as stack:
        log_f = (
            stack.enter_context(open(log_path, "w", encoding="utf-8", newline=""))
            if emit_logs
            else None
        )
        trace_f = (
            stack.enter_context(open(trace_path, "w", encoding="utf-8", newline=""))
            if emit_traces
            else None
        )
        for entry in anomaly_rows:
            event_id = _anomaly_event_id(entry)
            component = entry["component"]
            metric = entry["metric"]
            timestamp = entry["timestamp"]
            description = entry["description"]

            if log_f is not None:
                # Escape embedded double quotes so the key=value line
                # stays parseable if a future catalog description carries
                # one (today's descriptions are quote-free, so emitted
                # bytes are unchanged). Mirrors the shlex.quote posture
                # of _write_activity.
                safe_description = description.replace('"', '\\"')
                log_f.write(
                    f"{timestamp} INFO metric_report event_id={event_id} "
                    f"component={component} metric={metric} msg=\"{safe_description}\"\n"
                )

            if trace_f is not None:
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


@functools.lru_cache(maxsize=4096)
def _parse_csv_timestamp(timestamp: str) -> datetime.datetime:
    """Parse a ``YYYY-MM-DD HH:MM:SS[.SSS]`` CSV timestamp into a naive datetime.

    The integer-second and millisecond-precision forms emitted by
    ``_build_timestamp_arrays`` are both accepted. Centralizing the format
    dispatch here keeps every consumer (OTLP payload conversion, OTEL stream
    pacing, future readers) in lockstep on the supported formats.

    Cached: every per-(component, instance) source in the ``heapq.merge``
    writers re-parses the same shared timestamp grid (~42 parses per
    unique string on an N=3 14-component run). Merge access is
    window-local — the same timestamp recurs across sources within a
    merge window, then never again — so a small LRU absorbs the repeats
    without holding a 7-day grid (~600k datetimes) in memory. The
    returned ``datetime`` is immutable, so sharing one instance across
    callers is safe.
    """
    fmt = "%Y-%m-%d %H:%M:%S.%f" if "." in timestamp else "%Y-%m-%d %H:%M:%S"
    return datetime.datetime.strptime(timestamp, fmt)


_UNIX_EPOCH_UTC = datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc)


def _dt_to_unix_nanos(dt: datetime.datetime) -> int:
    """Convert a ``datetime`` (naive UTC or tz-aware) to unix-nanoseconds.

    Uses integer arithmetic on ``timedelta`` fields rather than
    ``datetime.timestamp() * 1e9`` so millisecond-precision inputs do not
    accrue floating-point rounding error on the way to a nanosecond integer.
    Naive inputs are interpreted as UTC, matching the convention used by
    ``_parse_csv_timestamp`` consumers.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    delta = dt - _UNIX_EPOCH_UTC
    return (delta.days * 86_400 + delta.seconds) * 1_000_000_000 + delta.microseconds * 1_000


def _to_unix_nanos(timestamp: str) -> int:
    """Convert ``YYYY-MM-DD HH:MM:SS[.SSS]`` timestamp strings to unix-nanoseconds."""
    return _dt_to_unix_nanos(_parse_csv_timestamp(timestamp))


def _build_otlp_trace_payload(entry: dict) -> dict:
    """Build one OTLP/HTTP JSON ``resourceSpans`` payload from one anomaly event."""
    event_id = _anomaly_event_id(entry)
    component = entry["component"]
    metric = entry["metric"]
    timestamp = entry["timestamp"]
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
    """Build one OTLP/HTTP JSON ``resourceMetrics`` payload from one anomaly event.

    Phase 6: when the anomaly entry carries a ``dimensions`` dict
    (currently empty in v1; populated by Phase 4 ``instance_filter``), each
    non-empty key/value pair is emitted as a string attribute alongside the
    base four (``event.id``, ``signal.type``, ``metric.name``,
    ``component``). Empty-string and ``None`` values are skipped.
    """
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
    for dim_key, dim_value in (entry.get("dimensions") or {}).items():
        if dim_value is None or dim_value == "":
            continue
        attributes.append({
            "key": dim_key,
            "value": {"stringValue": str(dim_value)},
        })
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
    """Build one OTLP protobuf ExportMetricsServiceRequest payload.

    Mirrors the JSON builder's Phase 6 behavior on
    ``entry["dimensions"]``: non-empty values are emitted as string
    attributes; empty / ``None`` cells are dropped.
    """
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
    for dim_key, dim_value in (entry.get("dimensions") or {}).items():
        if dim_value is None or dim_value == "":
            continue
        attributes.append(KeyValue(
            key=dim_key,
            value=AnyValue(string_value=str(dim_value)),
        ))

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

    Each ``batch`` entry is
    ``{"timestamp": str, "time_unix_nano": int, "component": str, "metric": str,
       "value": float, "dimensions": dict[str, str] (optional)}``.
    ``time_unix_nano`` is precomputed once per CSV row in ``stream_otel_gauges``
    so the builder does not re-parse the timestamp string per data point — the
    default config emits ~7,800 data points per batch, and per-data-point
    ``strptime`` was the dominant hotspot at high ``--otel-stream-speedup``.
    Entries are grouped first by ``component`` (one ``resourceMetrics`` entry
    per component) and then by ``metric`` (one ``metrics[]`` entry per metric
    within the component's scope), with one Gauge data point per row.
    ``dimensions`` (Phase 6) — when non-empty, each key/value pair is
    emitted as an additional string attribute alongside ``metric.name``,
    ``component``, and ``signal.type``. Empty-string and ``None`` values are
    skipped so the OTEL stream never carries empty-string attributes.
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
                attributes = [
                    {"key": "metric.name", "value": {"stringValue": metric_name}},
                    {"key": "component", "value": {"stringValue": component}},
                    {"key": "signal.type", "value": {"stringValue": "metric_value"}},
                ]
                for dim_key, dim_value in (entry.get("dimensions") or {}).items():
                    if dim_value is None or dim_value == "":
                        continue
                    attributes.append({
                        "key": dim_key,
                        "value": {"stringValue": str(dim_value)},
                    })
                data_points.append({
                    "timeUnixNano": str(entry["time_unix_nano"]),
                    "asDouble": float(entry["value"]),
                    "attributes": attributes,
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
    data point per batch row for that metric. Mirrors the JSON builder's
    Phase 6 behavior: any non-empty ``dimensions`` key on a batch
    entry is emitted as a string attribute alongside ``metric.name``,
    ``component``, and ``signal.type``.
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
                dp = m.gauge.data_points.add()
                dp.time_unix_nano = entry["time_unix_nano"]
                dp.as_double = float(entry["value"])
                dp.attributes.extend([
                    KeyValue(key="metric.name", value=AnyValue(string_value=metric_name)),
                    KeyValue(key="component", value=AnyValue(string_value=component)),
                    KeyValue(key="signal.type", value=AnyValue(string_value="metric_value")),
                ])
                for dim_key, dim_value in (entry.get("dimensions") or {}).items():
                    if dim_value is None or dim_value == "":
                        continue
                    dp.attributes.append(KeyValue(
                        key=dim_key,
                        value=AnyValue(string_value=str(dim_value)),
                    ))
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


# Canonical lowercased allowlist of HTTP header names whose value must
# never appear in plaintext in the OTEL activity log. The lookup is
# case-insensitive (the matcher lowercases the inbound header name
# before the set lookup), so every wire-format casing —
# ``Authorization``, ``authorization``, ``AUTHORIZATION``,
# ``SeT-cOoKiE`` — collapses to the same entry. Both
# ``_masked_headers`` (request-side, dict input) and
# ``_redact_sensitive_headers`` (response-side, list-of-pairs input)
# read from this set so the two paths cannot drift.
_SENSITIVE_HEADER_NAMES: frozenset[str] = frozenset({
    "authorization",
    "cookie",
    "set-cookie",
    "proxy-authorization",
    "x-api-key",
})

# Subset of ``_SENSITIVE_HEADER_NAMES`` whose values follow
# ``<scheme> <token>``. The scheme prefix is preserved so an operator
# can still distinguish a Bearer challenge from a Basic challenge in
# the log; only the token portion is replaced with ``***``.
_SCHEMED_SENSITIVE_HEADERS: frozenset[str] = frozenset({
    "authorization",
    "proxy-authorization",
})


def _mask_sensitive_value(name: str, value: str) -> str:
    """Replace ``value`` with the redacted form for header ``name``.

    Callers are expected to have already verified ``name.lower()`` is
    in ``_SENSITIVE_HEADER_NAMES``; the schemed-header branch preserves
    the leading scheme token (``Bearer``/``Basic``/etc.) and replaces
    only the credential token.
    """
    if name.lower() in _SCHEMED_SENSITIVE_HEADERS:
        parts = value.split(" ", 1)
        if len(parts) == 2:
            return f"{parts[0]} ***"
        return "***"
    return "***"


def _masked_headers(headers: dict[str, str]) -> dict[str, str]:
    """Return a copy of ``headers`` with sensitive request-side values masked.

    The full sensitive set lives in ``_SENSITIVE_HEADER_NAMES``;
    schemed entries (``Authorization``, ``Proxy-Authorization``) keep
    their scheme prefix and replace the credential with ``***``,
    matching the wire-format an operator expects to see in a verbose
    log. The OTEL request path only ever sets Authorization today,
    but covering the broader allowlist keeps the helper symmetric
    with ``_redact_sensitive_headers`` so a future request-side
    cookie or api-key never leaks.
    """
    masked = {}
    for key, value in headers.items():
        if key.lower() in _SENSITIVE_HEADER_NAMES:
            masked[key] = _mask_sensitive_value(key, value)
        else:
            masked[key] = value
    return masked


def _redact_sensitive_headers(
    header_pairs: list[tuple[str, str]],
) -> list[tuple[str, str]]:
    """Return ``header_pairs`` with sensitive response-side values masked.

    ``_http_error_activity_fields`` calls this on
    ``list(exc.headers.items())`` before serializing the result to
    JSON, so the redaction runs before any string ever reaches the
    activity log. Input casing is preserved on the output header
    name (so a downstream consumer still sees the wire-format name);
    the lookup is case-insensitive via ``name.lower()``. Duplicate
    names (``Set-Cookie`` commonly repeats) are independently
    redacted in order.
    """
    redacted: list[tuple[str, str]] = []
    for name, value in header_pairs:
        if name.lower() in _SENSITIVE_HEADER_NAMES:
            redacted.append((name, _mask_sensitive_value(name, value)))
        else:
            redacted.append((name, value))
    return redacted


def _http_error_activity_fields(
    exc, body: bytes, content_type: str, *, verbose: bool = False
) -> dict[str, str]:
    """Return structured activity-log diagnostics for ``HTTPError`` failures.

    Response header values are passed through
    ``_redact_sensitive_headers`` before they are serialized into the
    ``response_headers`` field, so an upstream proxy that echoes
    ``Set-Cookie`` / ``Authorization`` / ``X-Api-Key`` on a 4xx/5xx
    response never leaks credential material into the on-disk log.

    ``response_headers`` and ``cf_ray`` are always-on diagnostics. The
    raw ``request_body`` is included only under ``verbose=True`` —
    matching the ``--otel-verbose`` contract that raw OTLP payload
    bodies reach the activity log only when explicitly requested
    (a failing gauge endpoint would otherwise re-serialize a full
    multi-thousand-data-point batch into the log on every retry).
    """
    if not isinstance(exc, urllib.error.HTTPError):
        return {}

    fields: dict[str, str] = {}
    if exc.headers is not None:
        header_pairs = list(exc.headers.items())
        if header_pairs:
            fields["response_headers"] = json.dumps(
                _redact_sensitive_headers(header_pairs),
                separators=(",", ":"),
            )
        cf_ray = exc.headers.get("cf-ray") if hasattr(exc.headers, "get") else None
        if cf_ray:
            fields["cf_ray"] = cf_ray

    if verbose and "json" in content_type:
        fields["request_body"] = body.decode("utf-8", errors="replace")
    return fields


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
    prev_dt = None
    sent = 0
    try:
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
                        http_error_fields = _http_error_activity_fields(
                            exc, body, content_type, verbose=verbose
                        )
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
                                **http_error_fields,
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
                            **http_error_fields,
                            **err_fields,
                        )
                        time.sleep(backoff)
    finally:
        _write_activity(log_file, "END", sent=sent)
        if log_file is not None:
            log_file.close()
    return sent


def _iter_component_rows(component: str, csv_path: Path):
    """Yield ``(timestamp_str, component, [(metric_name, value)...], dimensions)``
    for each data row in ``csv_path``.

    ``dimensions`` is a ``dict[str, str]`` carrying any
    ``_INSTANCE_DIMENSION_COLUMNS`` cells lifted off the row when the CSV
    was emitted with Phase 2's multi-instance fan-out (header begins
    ``timestamp,id,host,pod,az,region,tenant,...``). Dimensionless CSVs
    (today's default, ``--instances-per-component 1``) yield an empty dict,
    so downstream consumers can treat the field as always present.
    Empty-string dimension cells are omitted from the dict so the OTEL
    attribute path never emits empty-string attributes (Phase 6).

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
        # Detect the Phase 2 dimension column block. The block is the full
        # ``_INSTANCE_DIMENSION_COLUMNS`` tuple in canonical order starting at
        # column 1, written verbatim by ``generate_component`` — a partial /
        # reordered header is treated as "no dimensions" and those columns
        # flow into the metric path (where ``float(raw)`` will naturally skip
        # them).
        dim_col_count = len(_INSTANCE_DIMENSION_COLUMNS)
        if (header[0:1] == ["timestamp"]
                and len(header) >= 1 + dim_col_count
                and tuple(header[1:1 + dim_col_count])
                == _INSTANCE_DIMENSION_COLUMNS):
            dim_cols = _INSTANCE_DIMENSION_COLUMNS
            metric_start = 1 + dim_col_count
        else:
            dim_cols = ()
            metric_start = 1
        metric_cols = header[metric_start:]
        for row in reader:
            if not row:
                continue
            ts = row[0]
            dimensions: dict[str, str] = {}
            for name, raw in zip(dim_cols, row[1:metric_start]):
                if raw == "":
                    continue
                dimensions[name] = raw
            values = []
            for name, raw in zip(metric_cols, row[metric_start:]):
                if raw == "":
                    continue
                try:
                    values.append((name, float(raw)))
                except ValueError:
                    continue
            yield ts, component, values, dimensions


# Margin reserved for stdin/stdout/stderr + the output file + room
# for the OS's accounting; the long-form merge needs at least
# ``len(sources) + _LONG_FORM_FD_MARGIN`` file descriptors available.
_LONG_FORM_FD_MARGIN = 16


def _ensure_long_form_fd_capacity(n_sources: int) -> None:
    """Raise the soft FD limit to fit ``n_sources`` concurrent file
    handles, or ``SystemExit`` with an actionable message if the OS
    won't let us.

    The long-form ``heapq.merge`` over per-(component, instance)
    iterators primes every source, so all of them hold an open
    ``csv.reader`` handle for the lifetime of the merge. At max
    fan-out (``len(COMPONENTS) * MAX_INSTANCES_PER_COMPONENT`` =
    14 × 20 = 280) we can exceed the default macOS soft limit
    (256), causing ``EMFILE`` deep inside the writer.

    Fix on POSIX (Linux, macOS): try to bump the soft limit (up to
    the hard cap) using ``resource.setrlimit``; if the hard limit is
    still too low or ``setrlimit`` rejects the raise, exit early with
    a clear error naming the needed headroom and the user-facing
    levers (``--instances-per-component``, ``--components``,
    ``ulimit -n``).

    Windows is a no-op: ``resource`` is POSIX-only, and there's no
    portable equivalent of ``RLIMIT_NOFILE`` we can pre-flight. The
    helper returns silently and lets ``open()`` surface the real
    error inside ``heapq.merge`` if the OS-level FD cap is reached.
    In practice the Windows default open-file table is plenty large
    for ``MAX_INSTANCES_PER_COMPONENT * len(COMPONENTS) = 280`` so
    this is unlikely to bite; tests
    (``test_ensure_long_form_fd_capacity_raises_systemexit_when_hard_limit_too_low``)
    skip on Windows via ``pytest.importorskip("resource")``.

    ``n_sources`` is only the file-handle count from this merge; the
    ``_LONG_FORM_FD_MARGIN`` reserves space for stdio + the output
    stream + a bit of OS overhead.
    """
    needed = n_sources + _LONG_FORM_FD_MARGIN
    try:
        import resource  # POSIX-only; absent on Windows.
    except ImportError:
        # No portable rlimit on Windows. If we end up needing more
        # FDs than the platform allows, ``open()`` will surface the
        # real error; we can't pre-flight it from here, so trust the
        # OS to enforce the bound at write time.
        return
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    if soft >= needed:
        return
    target = needed if hard == resource.RLIM_INFINITY else min(needed, hard)
    if target < needed:
        raise SystemExit(
            f"long-form output needs {needed} concurrent file handles "
            f"({n_sources} per-instance sources + {_LONG_FORM_FD_MARGIN} "
            f"reserve) but the process FD hard limit is {hard}. Lower "
            f"--instances-per-component, narrow --components, or raise "
            f"the system FD limit (ulimit -n) before re-running."
        )
    try:
        resource.setrlimit(resource.RLIMIT_NOFILE, (target, hard))
    except (ValueError, OSError) as exc:
        raise SystemExit(
            f"long-form output needs {needed} concurrent file handles "
            f"({n_sources} per-instance sources + {_LONG_FORM_FD_MARGIN} "
            f"reserve) but raising the soft FD limit from {soft} to "
            f"{target} failed: {exc}. Lower --instances-per-component, "
            f"narrow --components, or raise the system FD limit "
            f"(ulimit -n) before re-running."
        ) from exc


def _classify_component_csv_header(
    header: list[str],
) -> tuple[tuple[str, ...], list[str]]:
    """Split a per-component CSV header into ``(dim_cols, metric_cols)``.

    A header is classified as dimensioned only when **all three** hold:
    column 0 is exactly ``"timestamp"``, the header is long enough to
    fit the full dimension prefix (``len(header) >= 1 +
    len(_INSTANCE_DIMENSION_COLUMNS)``), and columns 1..6 after the
    timestamp are exactly ``_INSTANCE_DIMENSION_COLUMNS`` in registry
    order — i.e. the six fields ``id, host, pod, az, region, tenant``.
    Headers that fail any of those checks fall through to the no-dim
    branch, where every post-``timestamp`` column is treated as a
    metric. The explicit ``header[0]`` and length checks defend against
    a malformed / user-staged CSV whose first column happens to be
    ``id`` (or another dim name) being mis-routed into the dimensioned
    branch. Empty / missing headers go the same no-dim route.
    """
    if not header:
        return (), []
    dim_count = len(_INSTANCE_DIMENSION_COLUMNS)
    rest = header[1:]
    if (
        header[0] == "timestamp"
        and len(header) >= 1 + dim_count
        and tuple(rest[:dim_count]) == _INSTANCE_DIMENSION_COLUMNS
    ):
        return _INSTANCE_DIMENSION_COLUMNS, rest[dim_count:]
    return (), rest


def _scan_component_csv_headers(
    component_csv_paths: dict[str, Path],
) -> tuple[bool, dict[str, dict]]:
    """Inspect every per-component CSV header and return a layout summary.

    Returns ``(any_dimensioned, info)`` where ``info[component]`` is
    ``{"path": path, "exists": bool, "dim_cols": tuple, "metric_cols":
    list[str]}``. ``any_dimensioned`` is True when at least one existing
    CSV has the dimension prefix; this drives the long-form-with-dims vs.
    classic 4-column branch in ``write_gauges_csv``.
    """
    any_dimensioned = False
    info: dict[str, dict] = {}
    for component, path in component_csv_paths.items():
        entry: dict = {
            "path": path,
            "exists": path.exists(),
            "dim_cols": (),
            "metric_cols": [],
        }
        if entry["exists"]:
            with open(path, "r", encoding="utf-8", newline="") as fh:
                reader = csv.reader(fh)
                header = next(reader, None)
            if header:
                dim_cols, metric_cols = _classify_component_csv_header(header)
                entry["dim_cols"] = dim_cols
                entry["metric_cols"] = metric_cols
                if dim_cols:
                    any_dimensioned = True
        info[component] = entry
    return any_dimensioned, info


def _scan_instance_block_layout(
    csv_path: Path, *, has_dims: bool,
) -> list[tuple[tuple[str, ...], int]]:
    """Return the ordered list of ``(dim_tuple, start_offset)`` pairs for
    every instance block in ``csv_path``, in the order they first appear.

    ``start_offset`` is the opaque seek cookie returned by
    ``fh.tell()`` BEFORE the block's first row is read — *not* a raw
    byte position. Python's text-mode files return a cookie that
    encodes both the byte position and the decoder state, and it is
    only meaningful when handed back to ``seek()`` on a file opened
    with **matching** ``encoding`` and ``newline`` parameters.
    ``_iter_component_instance_rows`` reopens ``csv_path`` with the
    same ``encoding="utf-8"`` / ``newline=""`` settings as the scan
    here, so the cookie round-trips cleanly. ``seek()``ing straight to
    the block's start cookie gives O(rows_total) total work per
    per-component CSV — each row is read at most twice (once by the
    scan, once by exactly one iterator) — instead of the
    O(rows_total × instances) the previous "scan from top each time"
    iterator did.

    ``has_dims`` short-circuits the scan: dimensionless CSVs always
    have a single conceptual block represented by a tuple of empty
    strings (one per ``_INSTANCE_DIMENSION_COLUMNS`` entry); the
    ``start_offset`` is the cookie right after the header line. For
    dimensioned CSVs the scan reads one line at a time, recording
    ``tell()`` before each line, and detects block boundaries where
    the dim tuple changes (``generate_component`` writes per-instance
    blocks sequentially, so each unique dim tuple appears in exactly
    one contiguous block). The lightweight scan uses ``readline()`` +
    ``line.split(',')`` so the recorded ``tell()`` cookie isn't
    corrupted by ``csv.reader``'s internal buffer — the writer side
    emits unquoted comma-separated values (see the ``np.char.add``
    path in ``generate_component``), so the simple split is
    equivalent to a full CSV parse for the dim columns.
    """
    dim_count = len(_INSTANCE_DIMENSION_COLUMNS)
    if not has_dims:
        empty_dims = tuple("" for _ in _INSTANCE_DIMENSION_COLUMNS)
        with open(csv_path, "r", encoding="utf-8", newline="") as fh:
            fh.readline()  # header
            start_offset = fh.tell()
        return [(empty_dims, start_offset)]
    blocks: list[tuple[tuple[str, ...], int]] = []
    last: tuple[str, ...] | None = None
    with open(csv_path, "r", encoding="utf-8", newline="") as fh:
        fh.readline()  # header
        while True:
            pos = fh.tell()
            line = fh.readline()
            if not line:
                break  # EOF
            if line in ("\n", "\r\n"):
                # Skip blank lines (tolerate hand-edited inputs).
                # ``generate_component`` omits dropped rows from the
                # CSV entirely rather than writing them as blanks
                # (see ``_iter_component_rows``), so blank lines do
                # not occur on a freshly generated file — this guard
                # only matters for staged / hand-edited inputs.
                continue
            # generate_component writes plain comma-separated values
            # without quoting (see the np.char.add path), so a simple
            # split is safe and exactly what csv.reader would parse.
            fields = line.rstrip("\r\n").split(",")
            if len(fields) < 1 + dim_count:
                continue
            dims = tuple(fields[1:1 + dim_count])
            if dims != last:
                blocks.append((dims, pos))
                last = dims
    return blocks


def _iter_component_instance_rows(
    csv_path: Path, start_offset: int, *,
    has_dims: bool, n_metrics: int,
):
    """Yield ``(ts_dt, ts_raw, metric_values)`` for the rows belonging
    to one instance block in ``csv_path``, starting at the seek cookie
    ``start_offset``.

    Opens a fresh file handle so the caller can hold multiple
    per-instance iterators on the same CSV open simultaneously (e.g.
    for ``heapq.merge``). The handle ``seek()``s straight to the
    block's start cookie produced by ``_scan_instance_block_layout`` —
    no re-scanning from the top — and a ``csv.reader`` parses from
    there. The cookie is the opaque value returned by Python's
    text-mode ``tell()``, valid only against a handle opened with the
    matching ``encoding="utf-8"`` / ``newline=""`` settings (both the
    scan and this iterator open the file that way, so the round-trip
    is well-defined). On dimensioned CSVs the iterator records the
    first row's dim tuple as the block's identity and exits as soon as
    a later row's dim tuple differs (``generate_component`` writes
    blocks contiguously, so the dim transition is the end-of-block
    marker). On dimensionless CSVs the iterator yields every data row
    to EOF; ``start_offset`` then points to the first data row.
    """
    dim_count = len(_INSTANCE_DIMENSION_COLUMNS)
    with open(csv_path, "r", encoding="utf-8", newline="") as fh:
        fh.seek(start_offset)
        reader = csv.reader(fh)
        if has_dims:
            min_cols = 1 + dim_count
            block_dims: tuple[str, ...] | None = None
            for row in reader:
                # Skip blank lines AND short/malformed rows so a row
                # with fewer than ``min_cols`` columns cannot set
                # ``block_dims`` to a truncated tuple and prematurely
                # terminate the block on the next well-formed row.
                # ``_scan_instance_block_layout`` applies the same guard
                # so the two helpers cannot disagree on what counts as
                # an in-block row.
                if len(row) < min_cols:
                    continue
                dims = tuple(row[1:1 + dim_count])
                if block_dims is None:
                    block_dims = dims
                elif dims != block_dims:
                    return  # EOF for this block
                ts = row[0]
                ts_dt = _parse_csv_timestamp(ts)
                metric_values = row[
                    1 + dim_count: 1 + dim_count + n_metrics
                ]
                yield (ts_dt, ts, metric_values)
        else:
            for row in reader:
                if not row:
                    continue
                ts = row[0]
                ts_dt = _parse_csv_timestamp(ts)
                metric_values = row[1: 1 + n_metrics]
                yield (ts_dt, ts, metric_values)


def write_gauges_csv(
    component_csv_paths: dict[str, Path],
    output_path: Path,
) -> int:
    """Write a long-form ``gauges.csv`` with one row per
    ``(timestamp, component, metric, value)`` tuple (4-column shape) or
    ``(timestamp, component, id, host, pod, az, region, tenant, metric,
    value)`` tuple (10-column shape, phase 5) from the given
    per-component CSVs.

    Layout is decided purely by header inspection: if **any**
    per-component CSV carries the full ``id, host, pod, az, region,
    tenant`` dimension prefix after ``timestamp``, the writer emits the
    10-column long form. If every CSV is the classic dimensionless
    shape (first column ``timestamp`` followed directly by the metric
    columns), the writer emits today's 4-column form byte-identically,
    so the existing locked golden hashes are preserved.

    The dispatch is header-based, not flag-based: the Phase-2
    ``--instances-per-component N > 1`` fan-out is the canonical path
    that lands a dimensioned CSV, but ``--instance-config`` can also
    produce a dimensioned single-instance CSV and routes to the same
    long-form output.

    Rows are emitted in a chronologically merged timeline via
    ``heapq.merge`` keyed on the parsed timestamp — the same ordering
    ``stream_otel_gauges`` produces over its OTLP data points, so the file
    artifact can be cross-checked against an OTLP collector recording.
    Equal-timestamp ties tie-break on sorted component name, then on
    instance id (sorted), then on the per-component CSV's column order
    (``MetricSpec`` order). The function sorts ``component_csv_paths.keys()``
    internally so the component tiebreaker holds regardless of how the
    caller built the mapping; the instance tiebreaker sorts ids
    *lexicographically*, which matches the generated per-instance block
    order for single-digit fan-outs but diverges from numeric order at
    ``--instances-per-component`` >= 11 (``i0, i1, i10, i11, …, i19,
    i2, …``). ``_write_combined_long_form`` sorts the same way, so the
    two long-form artifacts always agree on the tie-break.

    Values are written through verbatim from the per-component CSV's raw
    cell string — no ``float(raw)`` coercion is attempted, so the on-disk
    bytes never depend on Python's ``str(float)`` repr and any malformed
    cell would propagate as-is (this is intentionally narrower than
    ``stream_otel_gauges``, which ``float(raw)``-coerces and silently skips
    unparseable cells; ``generate_component`` only ever writes finite
    floats, so in practice the two paths emit the same data points).
    Empty / dropped cells are skipped (mirroring the OTEL gauge stream's
    behavior on dropped rows).

    Returns the number of data rows written (header excluded).
    """
    any_dimensioned, layout = _scan_component_csv_headers(component_csv_paths)

    if not component_csv_paths:
        with open(output_path, "w", encoding="utf-8", newline="") as f:
            f.write("timestamp,component,metric,value\n")
        return 0

    # Sort the component iterators by component name so equal-timestamp
    # ties tie-break on sorted-component order regardless of how the caller
    # built ``component_csv_paths``. This is what the locked golden hashes
    # encode (callers in this module already pass ``sorted(args.components)``,
    # so the sort is idempotent in the happy path).
    sorted_components = [
        c for c in sorted(component_csv_paths)
        if layout[c]["exists"]
    ]

    if not any_dimensioned:
        # Classic 4-column path. Preserved byte-identically by keeping the
        # row iterator and writer shape unchanged from pre-existing code so
        # the locked SHA-256 hashes in ``tests/test_gauges_file.py`` still
        # apply to N=1 / dimensionless runs.
        def _row_iter_4col(component: str):
            entry = layout[component]
            metric_cols = entry["metric_cols"]
            n_metrics = len(metric_cols)
            with open(entry["path"], "r", encoding="utf-8", newline="") as fh:
                reader = csv.reader(fh)
                next(reader, None)  # header (already inspected)
                for row in reader:
                    if not row:
                        continue
                    ts = row[0]
                    ts_dt = _parse_csv_timestamp(ts)
                    values = row[1: 1 + n_metrics]
                    yield (ts_dt, ts, component,
                           list(zip(metric_cols, values)))

        iters = [_row_iter_4col(c) for c in sorted_components]
        rows_written = 0
        with open(output_path, "w", encoding="utf-8", newline="") as out_f:
            writer = csv.writer(out_f, lineterminator="\n")
            writer.writerow(("timestamp", "component", "metric", "value"))
            for _dt, ts, comp, name_value_pairs in heapq.merge(
                *iters, key=lambda item: item[0]
            ):
                for name, raw in name_value_pairs:
                    if raw == "":
                        continue
                    writer.writerow((ts, comp, name, raw))
                    rows_written += 1
        return rows_written

    # Long form with dimensions (phase 5). Build one merge iterator
    # per (component, instance) block. Each block is timestamp-monotonic
    # because ``generate_component`` writes dimensioned CSVs as sequential
    # per-instance blocks. We sort sources by (component_name, instance_id)
    # before passing them to ``heapq.merge`` so equal-timestamp output
    # groups by component, then by instance id, and within each row the
    # inner metric loop walks columns in MetricSpec order — matching the
    # ``(timestamp, component, instance_id, metric)`` tie-break order
    # promised in the docstring.

    sources = []
    for component in sorted_components:
        entry = layout[component]
        metric_cols = entry["metric_cols"]
        has_dims = bool(entry["dim_cols"])
        instance_blocks = _scan_instance_block_layout(
            entry["path"], has_dims=has_dims,
        )
        for instance_dims, start_offset in instance_blocks:
            row_iter = _iter_component_instance_rows(
                entry["path"], start_offset,
                has_dims=has_dims, n_metrics=len(metric_cols),
            )

            def _tagged(_iter=row_iter, _comp=component,
                        _dims=instance_dims, _cols=metric_cols):
                for ts_dt, ts_raw, values in _iter:
                    yield (ts_dt, ts_raw, _comp, _dims, _cols, values)
            # Sort key carries the full ``instance_dims`` tuple, not
            # just the leading ``id`` field, so a hypothetical future
            # registry where two instances share an ``id`` but differ
            # in another dim still gets a total order. In v1 the ``id``
            # is unique per component, so the trailing fields are inert.
            sources.append(((component, instance_dims), _tagged()))

    # Each source holds an open file handle for the lifetime of the
    # merge. Pre-flight the FD soft limit so high-fan-out runs (e.g.,
    # 14 components × 20 instances = 280 handles) either bump the
    # rlimit up to fit or fail with an actionable message before
    # ``heapq.merge`` tries to prime the heap.
    _ensure_long_form_fd_capacity(len(sources))

    sources.sort(key=lambda item: item[0])
    iters = [src for _key, src in sources]

    rows_written = 0
    with open(output_path, "w", encoding="utf-8", newline="") as out_f:
        writer = csv.writer(out_f, lineterminator="\n")
        writer.writerow(
            ("timestamp", "component", *_INSTANCE_DIMENSION_COLUMNS, "metric", "value")
        )
        for _dt, ts, comp, dims, metric_cols, values in heapq.merge(
            *iters, key=lambda item: item[0]
        ):
            for name, raw in zip(metric_cols, values):
                if raw == "":
                    continue
                writer.writerow((ts, comp, *dims, name, raw))
                rows_written += 1
    return rows_written


# Schema-document version. Bump on any breaking change to the ``schema.json``
# shape so consumers (including the validator) can fail fast against a stale
# document. The validator rejects unknown versions outright.
#
# Version 2 (phase 7): adds a top-level ``topology`` section
# carrying the directed coupling graph (source -> edge[]) so
# the ``validate`` subcommand can run the realistic-mode Pearson coupling
# check against the snapshot of edges the run was supposed to honor.
SCHEMA_DOCUMENT_VERSION = 2


# Default Pearson correlation gate for ``_validate_topology_coupling``.
# Mirrors the issue acceptance bound (0.85) and the existing LLM
# correlation test in ``tests/test_topology_llm.py``. Per-edge overrides
# live in ``Edge.correlation_threshold``.
_TOPOLOGY_DEFAULT_CORRELATION_THRESHOLD = 0.85


# Padding (seconds) applied around every ``anomalies.csv`` window when
# the validator excludes anomaly-affected rows from the topology
# correlation computation. Mirrors the ``_EXCLUSION_PAD_SECONDS``
# constant in ``tests/test_topology_llm.py`` so single-row cascades that
# round to the nearest sampled row don't leak into the correlation pool.
_TOPOLOGY_CORRELATION_EXCLUSION_PAD_SECONDS = 30


def _metric_spec_to_schema_entry(spec: "MetricSpec") -> dict:
    """Return the schema.json entry for one ``MetricSpec``.

    Schema metadata is emitted verbatim with stable key order. ``None`` values
    are preserved (rather than dropped) so consumers can distinguish "field
    explicitly declared unbounded" from "field absent due to old schema".
    """
    return {
        "name": spec.name,
        "unit": spec.unit,
        "semantic_type": spec.semantic_type,
        "dtype": spec.dtype,
        "min_value": spec.min_value,
        "max_value": spec.max_value,
        "derivation": spec.derivation,
    }


def _saturation_params_to_schema_entry(
    sat: "SaturationParams | None",
) -> dict | None:
    """Return the schema.json entry for one ``SaturationParams``.

    Returns ``None`` when the edge has no saturation. Keys are emitted in
    a stable order so ``sort_keys=True`` produces byte-deterministic
    JSON.
    """
    if sat is None:
        return None
    return {
        "midpoint": sat.midpoint,
        "steepness": sat.steepness,
        "latency_gain": sat.latency_gain,
        "error_gain": sat.error_gain,
    }


def _edge_to_schema_entry(edge: "Edge") -> dict:
    """Return the schema.json entry for one ``Edge``.

    Constant-weight edges serialize their numeric weight verbatim;
    callable-weight edges serialize the literal string ``"callable"``
    (full reproducibility of the per-row weight is a code concern — the
    schema only declares that the coupling exists).
    """
    weight: float | str
    if callable(edge.weight):
        weight = "callable"
    else:
        weight = edge.weight
    return {
        "target": edge.target,
        "weight": weight,
        "saturation": _saturation_params_to_schema_entry(edge.saturation),
        "correlation_threshold": edge.correlation_threshold,
    }


def _component_dimensions_schema_entry(
    instances: list["Instance"] | None,
) -> dict | None:
    """Return the ``schema.json`` ``dimensions`` entry for a component's
    instance list, or ``None`` for the dimensionless default.

    Mirrors the long-form per-component CSV writer's branch predicate
    (``_is_anonymous_instance_list``): any non-anonymous instance list —
    whether ``--instances-per-component N>1`` fan-out or
    ``--instance-config`` with a non-default declaration — produces
    dim-aware CSV output and therefore declares ``dimensions`` in the
    schema. The single-anonymous-``Instance()`` default produces
    dimensionless output and omits the block so the v1 (default)
    ``schema.json`` stays byte-identical to the dimensionless baseline.

    The ``axes`` list is the sorted subset of
    ``_INSTANCE_DIMENSION_FIELDS`` (i.e. ``_INSTANCE_DIMENSION_COLUMNS``
    minus the leading ``id`` slot — ``id`` identifies an instance, it is
    not a dimension to slice on) whose value is non-``None`` on at least
    one instance in the list. ``cardinality`` is ``len(instances)``.
    Both keys are always present together so the validator can read them
    in lockstep. ``axes`` is allowed to be empty with ``cardinality > 1``:
    that is the shape produced by an instance list whose only non-``None``
    field is ``id`` (e.g. ``[Instance(id="i0"), Instance(id="i1")]`` —
    multiple replicas with no slicable dimension yet). The schema still
    declares the long-form CSV layout under that shape because the
    per-component CSV carries the full ``id, host, pod, az, region,
    tenant`` prefix block whenever ``cardinality > 1``, regardless of
    which dim columns are populated.
    """
    if instances is None or _is_anonymous_instance_list(instances):
        return None
    axes = sorted(
        {
            field
            for inst in instances
            for field in _INSTANCE_DIMENSION_FIELDS
            if getattr(inst, field) is not None
        }
    )
    return {"axes": axes, "cardinality": len(instances)}


def _serialize_topology(
    components: list[str],
) -> dict[str, list[dict]]:
    """Return the ``schema.json`` ``topology`` section for the live ``TOPOLOGY``.

    The output is keyed by source component and contains only edges whose
    *source and target both appear in* ``components``; a run that drops a
    component via ``--components`` does not couple to it, so the snapshot
    must reflect the actual coupling graph the validator should check. The
    surviving source keys are restricted to ``TOPOLOGY``'s declared sources
    (sources with no surviving outgoing edges in the filtered graph are
    omitted to keep the section minimal), and each source's edge list is
    sorted by target name for byte-deterministic output (top-level keys
    are already byte-sorted via ``json.dumps(sort_keys=True)``).
    """
    components_set = set(components)
    topology: dict[str, list[dict]] = {}
    for source, edges in TOPOLOGY.items():
        if source not in components_set:
            continue
        kept = [
            _edge_to_schema_entry(edge)
            for edge in edges
            if edge.target in components_set
        ]
        if not kept:
            continue
        kept.sort(key=lambda entry: entry["target"])
        topology[source] = kept
    return topology


def write_schema_json(
    output_path: Path,
    *,
    components: list[str],
    effective_specs: dict[str, list["MetricSpec"]],
    metadata: dict,
    emitted_files: list[str],
    instances_by_component: dict[str, list["Instance"]] | None = None,
) -> None:
    """Write a declarative ``schema.json`` describing the current run's artifacts.

    The document is the single source of truth the ``validate`` subcommand consumes
    to check the run after the fact. It captures five slices of information:

    - ``schema_version`` — integer schema-document version (see
      ``SCHEMA_DOCUMENT_VERSION``).
    - ``metadata`` — run-level parameters (timestamp anchor, duration, drop
      rate, scenario set, seed, ...) needed to reconstruct the timeline and
      row-count expectations from the artifacts on disk.
    - ``components`` — per-component metric metadata in MetricSpec column
      order, so the validator can check ``dtype`` / ``min_value`` /
      ``max_value`` / ``semantic_type`` / ``derivation`` cell-by-cell against
      the per-component CSV. Each per-component payload also carries an
      optional ``dimensions`` block (phase 8) declaring the
      instance topology's axes + cardinality when the per-component CSV
      is dim-aware (``--instances-per-component N>1`` fan-out or a non-
      default ``--instance-config`` entry); the block is omitted in the
      default single-anonymous-``Instance()`` path so the v1 schema bytes
      stay byte-identical to the dimensionless baseline.
    - ``files`` — sorted list of artifact filenames the run was supposed to
      write, so the validator can flag missing or extra files.
    - ``topology`` (phase 7) — the directed coupling graph
      restricted to the active component set: ``{source:
      [{target, weight, saturation, correlation_threshold}, ...]}``.
      Callable weights serialize as the literal string ``"callable"``;
      ``saturation`` is either a
      ``{midpoint, steepness, latency_gain, error_gain}`` dict or
      ``null``; ``correlation_threshold`` is either a float in
      ``(-1, 1]`` (per-edge override) or ``null`` (fall back to
      ``_TOPOLOGY_DEFAULT_CORRELATION_THRESHOLD``). The validator reads
      this to run ``_validate_topology_coupling`` under
      ``metadata.topology_mode == "realistic"``.

    ``instances_by_component`` is the live per-run instance map
    (``RunContext.instances``) restricted to the schema's components.
    A missing entry, or the single-anonymous-``Instance()`` default,
    omits the per-component ``dimensions`` block.

    The output is byte-deterministic: ``json.dumps`` with ``sort_keys=True``,
    fixed indent, ``ensure_ascii=False``, and a trailing newline. The
    per-component ``metrics`` list intentionally preserves MetricSpec column
    order (not sorted) so the validator can zip it against CSV header columns
    in one pass. The ``topology`` section sorts each source's edge list by
    target name for stable output independent of declaration order.
    """
    instances_by_component = instances_by_component or {}
    component_payload = {}
    for component in components:
        specs = effective_specs.get(component, [])
        payload = {
            "csv_filename": f"{component}.csv",
            "metrics": [_metric_spec_to_schema_entry(spec) for spec in specs],
        }
        dimensions = _component_dimensions_schema_entry(
            instances_by_component.get(component)
        )
        if dimensions is not None:
            payload["dimensions"] = dimensions
        component_payload[component] = payload

    document = {
        "schema_version": SCHEMA_DOCUMENT_VERSION,
        "metadata": metadata,
        "files": sorted(emitted_files),
        "components": component_payload,
        "topology": _serialize_topology(components),
    }

    # ``sort_keys=True`` gives byte-stable top-level ordering. Nested lists
    # (metrics, files, scenarios) keep their declared order — they are sorted
    # by the caller where determinism matters (files, scenarios) and left in
    # MetricSpec column order where the order carries meaning (metrics).
    output_path.write_text(
        json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


# ------------------------------------------------------------------
# Output validator (the `validate` subcommand)
# ------------------------------------------------------------------
# Floating-point tolerance for derived-column checks. CSV cells are written
# at 3-decimal precision (see ``np.round(values, 3)`` in
# ``generate_component``), so a derivation recomputed from rounded source
# cells can drift from the stored derived cell by at most a few units of
# the last digit. 0.01 is conservative enough to absorb that drift while
# still catching real bugs (e.g. a 5% miscompute of ``hit_ratio``).
_VALIDATE_DERIVATION_TOLERANCE = 0.01

# Integer-cell tolerance for ``dtype="int"`` checks. CSV cells are 3-decimal
# floats so a value the generator wrote as ``5.000`` round-trips exactly,
# while a fractional source value like ``4.567`` lands well outside this
# band. Using 0.0005 means we reject anything ≥ 0.001 (the smallest
# representable fractional at 3-decimal precision).
_VALIDATE_INT_TOLERANCE = 5e-4


def _json_path(parent: str, child: str | int) -> str:
    """Return a compact dotted path for schema-shape diagnostics."""
    if isinstance(child, int):
        return f"{parent}[{child}]"
    if parent == "$":
        return f"$.{child}"
    return f"{parent}.{child}"


def _schema_shape_error(schema_path: Path, path: str, message: str) -> ValueError:
    return ValueError(f"{schema_path}: schema shape error at {path}: {message}")


def _require_schema_mapping(schema_path: Path, value, path: str) -> dict:
    if not isinstance(value, dict):
        raise _schema_shape_error(schema_path, path, "expected object")
    return value


def _require_schema_list(schema_path: Path, value, path: str) -> list:
    if not isinstance(value, list):
        raise _schema_shape_error(schema_path, path, "expected list")
    return value


def _require_schema_string(schema_path: Path, value, path: str) -> str:
    if not isinstance(value, str):
        raise _schema_shape_error(schema_path, path, "expected string")
    return value


def _require_schema_number(schema_path: Path, value, path: str) -> int | float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise _schema_shape_error(schema_path, path, "expected finite number")
    if not math.isfinite(value):
        raise _schema_shape_error(schema_path, path, "expected finite number")
    return value


def _validate_string_list_schema_shape(
    schema_path: Path, value, path: str
) -> list[str]:
    items = _require_schema_list(schema_path, value, path)
    for index, item in enumerate(items):
        _require_schema_string(schema_path, item, _json_path(path, index))
    return items


def _validate_schema_document_shape(schema_path: Path, document) -> dict:
    """Validate the structural contract consumed by ``validate DIR``.

    The downstream validators trust ``schema.json`` as their declarative input
    and intentionally index hot-path fields directly. Keep malformed or
    hand-edited schema documents from escaping as raw ``KeyError`` /
    ``TypeError`` tracebacks by checking the minimum shape at load time.
    """
    document = _require_schema_mapping(schema_path, document, "$")
    metadata = _require_schema_mapping(
        schema_path, document.get("metadata"), "$.metadata"
    )
    _validate_string_list_schema_shape(
        schema_path, metadata.get("components"), "$.metadata.components"
    )
    _validate_string_list_schema_shape(
        schema_path, metadata.get("emit_selection"), "$.metadata.emit_selection"
    )
    rows_per_component = _require_schema_number(
        schema_path, metadata.get("rows_per_component"),
        "$.metadata.rows_per_component",
    )
    if not isinstance(rows_per_component, int) or rows_per_component <= 0:
        raise _schema_shape_error(
            schema_path, "$.metadata.rows_per_component",
            "expected positive integer",
        )
    interval_seconds = _require_schema_number(
        schema_path, metadata.get("interval_seconds"),
        "$.metadata.interval_seconds",
    )
    if interval_seconds <= 0:
        raise _schema_shape_error(
            schema_path, "$.metadata.interval_seconds",
            "expected positive number",
        )
    total_seconds = _require_schema_number(
        schema_path, metadata.get("total_seconds"), "$.metadata.total_seconds"
    )
    if total_seconds <= 0:
        raise _schema_shape_error(
            schema_path, "$.metadata.total_seconds", "expected positive number"
        )
    _require_schema_string(schema_path, metadata.get("start"), "$.metadata.start")
    drop_rate = _require_schema_number(
        schema_path, metadata.get("drop_rate"), "$.metadata.drop_rate"
    )
    if not 0 <= drop_rate <= 1:
        raise _schema_shape_error(
            schema_path, "$.metadata.drop_rate", "expected value in [0, 1]"
        )
    inject_dst_artifact_day = _require_schema_number(
        schema_path, metadata.get("inject_dst_artifact_day"),
        "$.metadata.inject_dst_artifact_day",
    )
    if not isinstance(inject_dst_artifact_day, int) or inject_dst_artifact_day < 0:
        raise _schema_shape_error(
            schema_path, "$.metadata.inject_dst_artifact_day",
            "expected non-negative integer",
        )

    _validate_string_list_schema_shape(schema_path, document.get("files"), "$.files")
    components_payload = _require_schema_mapping(
        schema_path, document.get("components"), "$.components"
    )

    for component in metadata["components"]:
        payload_path = _json_path("$.components", component)
        if component not in components_payload:
            raise _schema_shape_error(
                schema_path, payload_path,
                "component listed in metadata.components is missing",
            )
        payload = _require_schema_mapping(
            schema_path, components_payload[component], payload_path
        )
        _require_schema_string(
            schema_path, payload.get("csv_filename"),
            _json_path(payload_path, "csv_filename"),
        )
        metrics = _require_schema_list(
            schema_path, payload.get("metrics"), _json_path(payload_path, "metrics")
        )
        for index, metric in enumerate(metrics):
            metric_path = _json_path(_json_path(payload_path, "metrics"), index)
            metric = _require_schema_mapping(schema_path, metric, metric_path)
            _require_schema_string(
                schema_path, metric.get("name"), _json_path(metric_path, "name")
            )
            dtype = _require_schema_string(
                schema_path, metric.get("dtype"), _json_path(metric_path, "dtype")
            )
            if dtype not in {"float", "int"}:
                raise _schema_shape_error(
                    schema_path, _json_path(metric_path, "dtype"),
                    "expected 'float' or 'int'",
                )
            for bound in ("min_value", "max_value"):
                raw_bound = metric.get(bound)
                if raw_bound is not None:
                    _require_schema_number(
                        schema_path, raw_bound, _json_path(metric_path, bound)
                    )
            for string_field in ("unit", "semantic_type", "derivation"):
                raw_value = metric.get(string_field)
                if raw_value is not None:
                    _require_schema_string(
                        schema_path, raw_value, _json_path(metric_path, string_field)
                    )
        dimensions = payload.get("dimensions")
        if dimensions is not None:
            dimensions_path = _json_path(payload_path, "dimensions")
            dimensions = _require_schema_mapping(schema_path, dimensions, dimensions_path)
            cardinality = _require_schema_number(
                schema_path, dimensions.get("cardinality"),
                _json_path(dimensions_path, "cardinality"),
            )
            if not isinstance(cardinality, int) or cardinality <= 0:
                raise _schema_shape_error(
                    schema_path, _json_path(dimensions_path, "cardinality"),
                    "expected positive integer",
                )
            _validate_string_list_schema_shape(
                schema_path, dimensions.get("axes"),
                _json_path(dimensions_path, "axes"),
            )
    return document


def _load_schema_document(schema_path: Path) -> dict:
    """Load and version-check a ``schema.json`` document.

    Raises ``ValueError`` if the file is missing, malformed JSON, or written
    by a schema-document version this build cannot validate, or if required
    structural fields are missing/mistyped. The validator intentionally
    rejects unknown versions outright rather than silently skipping unfamiliar
    fields — a stale schema would produce false-positive or false-negative
    results.
    """
    if not schema_path.exists():
        raise ValueError(
            f"validate requires {schema_path}; "
            "regenerate the run with 'schema' included in --emit "
            "(e.g. --emit metrics,schema)"
        )
    try:
        document = json.loads(schema_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ValueError(f"{schema_path} is not valid JSON: {e}") from e
    document = _require_schema_mapping(schema_path, document, "$")
    version = document.get("schema_version")
    if version != SCHEMA_DOCUMENT_VERSION:
        raise ValueError(
            f"{schema_path} has schema_version={version!r}; "
            f"this build only validates schema_version="
            f"{SCHEMA_DOCUMENT_VERSION}"
        )
    return _validate_schema_document_shape(schema_path, document)


def _validate_required_files_present(output_dir: Path, schema: dict) -> list[str]:
    """Return one violation per declared file missing from ``output_dir``."""
    violations = []
    for filename in schema.get("files", []):
        if not (output_dir / filename).exists():
            violations.append(
                f"missing declared file: {filename!r} (listed in schema.files)"
            )
    return violations


def _validate_no_unknown_files(output_dir: Path, schema: dict) -> list[str]:
    """Return one violation per file in ``output_dir`` not declared in the schema.

    Mirrors ``_pre_clean_output_dir``'s registry intent: an unknown file is
    either stale debris the pre-clean missed, a foreign file in the wrong
    directory, or a new artifact someone forgot to register.
    """
    declared = set(schema.get("files", []))
    # The schema document itself is always allowed even if a stale schema
    # somehow omits its own filename — without this exemption the validator
    # would be unable to bootstrap.
    declared.add("schema.json")
    violations = []
    for path in sorted(output_dir.iterdir()):
        if not path.is_file():
            continue
        if path.name not in declared:
            violations.append(f"unknown file in output dir: {path.name!r}")
    return violations


def _validate_anomalies_sorted(output_dir: Path, schema: dict) -> list[str]:
    """Return one violation if ``anomalies.csv`` is not sorted by timestamp.

    ``main()`` sorts the manifest chronologically by ``(span_start, component,
    metric)`` before writing; the validator only requires the timestamp axis
    to be non-decreasing (the secondary keys break ties within the same
    timestamp and don't matter for downstream consumers)."""
    anomalies_path = output_dir / "anomalies.csv"
    if not anomalies_path.exists():
        return []
    violations = []
    with open(anomalies_path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        prev = None
        for i, row in enumerate(reader, start=1):
            ts = row.get("timestamp")
            if ts is None:
                violations.append(
                    f"anomalies.csv row {i} missing 'timestamp' column"
                )
                continue
            if prev is not None and ts < prev:
                violations.append(
                    f"anomalies.csv row {i}: timestamp {ts!r} precedes "
                    f"previous timestamp {prev!r}"
                )
            prev = ts
    return violations


def _validate_component_row_count(
    output_dir: Path, schema: dict, component: str
) -> list[str]:
    """Each component CSV may have at most ``rows_per_component`` data rows
    (plus the DST splice extras when applicable). Dropped rows are absent
    from the CSV entirely (``generate_component`` filters via ``keep_mask``
    before serialization), so under-emission is expected and not flagged
    unless it exceeds the configured ``drop_rate``'s plausible band.

    Phase 8: when the per-component schema declares
    ``dimensions``, each per-row generation is fanned out across
    ``cardinality`` instances (Phase 2 long-form CSV writer), so both
    bands are multiplied by ``cardinality`` to keep the expected and
    actual counts in the same units.
    """
    csv_filename = schema["components"][component]["csv_filename"]
    csv_path = output_dir / csv_filename
    if not csv_path.exists():
        return []  # covered by _validate_required_files_present

    metadata = schema["metadata"]
    base_rows = metadata["rows_per_component"]
    interval = metadata["interval_seconds"]
    dst_day = metadata.get("inject_dst_artifact_day", 0)
    drop_rate = metadata.get("drop_rate", 0.0) or 0.0
    # The DST splice duplicates the 02:00–02:59 wall-clock hour on one day,
    # adding 3,600 / interval extra rows to that day. Use floor division to
    # mirror the generator's row-count derivation.
    dst_extra = int(3600 // interval) if dst_day and dst_day > 0 else 0
    dimensions = schema["components"][component].get("dimensions")
    cardinality = dimensions["cardinality"] if dimensions else 1
    expected_max = (base_rows + dst_extra) * cardinality

    with open(csv_path, "r", encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        try:
            next(reader)  # skip header
        except StopIteration:
            return [f"{csv_filename}: file has no header row"]
        data = sum(1 for row in reader if row)

    violations = []
    if data > expected_max:
        violations.append(
            f"{csv_filename}: data row count {data} exceeds expected max "
            f"{expected_max} (rows_per_component={base_rows} "
            f"+ DST splice {dst_extra}, cardinality={cardinality})"
        )
    # Under-emission lower bound: with drop_rate p and N rows the surviving
    # count per instance is N*(1-p) with std sqrt(N*p*(1-p)). ``generate_component``
    # draws a single ``drop_mask`` and reuses the same ``keep_mask`` for every
    # instance's row block, so row drops are *perfectly correlated* across
    # instances — the total surviving-row count is cardinality times the
    # per-instance count, and the std scales linearly (not by sqrt) on
    # ``cardinality``. Allow a generous 8-sigma band on top of that so a tiny
    # N doesn't trigger a false positive (e.g. a 144-row 600s smoke run).
    if drop_rate < 1.0:
        if base_rows > 0:
            per_instance_std = math.sqrt(base_rows * drop_rate * (1.0 - drop_rate))
            std = per_instance_std * cardinality
            lower = int(base_rows * cardinality * (1.0 - drop_rate) - 8.0 * std)
            if lower < 0:
                lower = 0
        else:
            lower = 0
        if data < lower:
            violations.append(
                f"{csv_filename}: data row count {data} is below the "
                f"expected lower bound {lower} for drop_rate={drop_rate}, "
                f"rows_per_component={base_rows}, cardinality={cardinality}"
            )
    return violations


def _validate_component_timestamp_coverage(
    output_dir: Path, schema: dict, component: str
) -> list[str]:
    """Every row's timestamp must fall in the expected window
    ``[START, START + total_seconds)``. DST duplicates are within range and
    intentionally not flagged here.
    """
    csv_filename = schema["components"][component]["csv_filename"]
    csv_path = output_dir / csv_filename
    if not csv_path.exists():
        return []

    metadata = schema["metadata"]
    start_dt = datetime.datetime.fromisoformat(metadata["start"])
    end_dt = start_dt + datetime.timedelta(seconds=metadata["total_seconds"])

    violations = []
    with open(csv_path, "r", encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        try:
            next(reader)
        except StopIteration:
            return violations
        for i, row in enumerate(reader, start=2):  # data rows start at line 2
            if not row:
                continue
            try:
                ts = _parse_csv_timestamp(row[0])
            except ValueError as e:
                violations.append(
                    f"{csv_filename} line {i}: bad timestamp {row[0]!r}: {e}"
                )
                continue
            if ts < start_dt:
                violations.append(
                    f"{csv_filename} line {i}: timestamp {row[0]!r} precedes "
                    f"START {metadata['start']!r}"
                )
                return violations  # one is enough; don't flood output
            if ts >= end_dt:
                violations.append(
                    f"{csv_filename} line {i}: timestamp {row[0]!r} is at or "
                    f"after end {(end_dt.isoformat())!r}"
                )
                return violations
    return violations


def _validate_component_cells(
    output_dir: Path, schema: dict, component: str
) -> list[str]:
    """Every cell must respect its MetricSpec's declared schema metadata:

    - column order matches the schema's MetricSpec list,
    - parseable as a float,
    - within ``[min_value, max_value]`` if either is declared,
    - integer-valued (modulo 3-decimal CSV precision) when ``dtype="int"``,
    - non-negative when ``semantic_type`` is ``counter`` or ``rate`` (even
      if ``min_value`` was not declared — these semantic kinds are always
      ≥ 0 by definition).

    Each unique violation is reported once with a line-number example so the
    output stays bounded even when a whole column is wrong.

    Phase 8: when the per-component schema declares
    ``dimensions``, the expected header is
    ``("timestamp", *_INSTANCE_DIMENSION_COLUMNS, *metric_names)`` to
    match the Phase 2 long-form per-component CSV. The dim cells (id,
    host, pod, az, region, tenant) are strings, not metric values, so
    they are skipped by the cell-range checks below; the metric cells
    start at index ``1 + len(_INSTANCE_DIMENSION_COLUMNS)`` in that
    branch.
    """
    csv_filename = schema["components"][component]["csv_filename"]
    csv_path = output_dir / csv_filename
    metrics = schema["components"][component]["metrics"]
    dimensions = schema["components"][component].get("dimensions")
    if not csv_path.exists():
        return []

    violations = []
    with open(csv_path, "r", encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        try:
            header = next(reader)
        except StopIteration:
            return [f"{csv_filename}: file has no header row"]

        if dimensions is not None:
            expected_columns = (
                ["timestamp"]
                + list(_INSTANCE_DIMENSION_COLUMNS)
                + [m["name"] for m in metrics]
            )
            metric_col_start = 1 + len(_INSTANCE_DIMENSION_COLUMNS)
        else:
            expected_columns = ["timestamp"] + [m["name"] for m in metrics]
            metric_col_start = 1
        if header != expected_columns:
            violations.append(
                f"{csv_filename}: header {header} does not match schema "
                f"column order {expected_columns}"
            )
            return violations  # cell checks are meaningless once columns drift

        # Track which violations have already fired per (metric, kind) so we
        # don't flood the output for an entire-column violation.
        seen: set[tuple[str, str]] = set()

        def _record(metric_name: str, kind: str, msg: str) -> None:
            key = (metric_name, kind)
            if key in seen:
                return
            seen.add(key)
            violations.append(msg)

        # Hoist the per-column constants out of the row loop: the dict
        # lookups (name, dtype, bounds, semantic_type) are invariant per
        # column, and re-fetching them per cell costs ~4 lookups x rows x
        # metrics (~85M on a default 7-day validation) — the exact
        # "per-row re-computation of constants" pattern the pre-PR
        # checklist forbids in hot paths.
        column_checks = [
            (
                col_idx,
                m["name"],
                m.get("dtype") == "int",
                m.get("min_value"),
                m.get("max_value"),
                m.get("semantic_type"),
            )
            for col_idx, m in enumerate(metrics, start=metric_col_start)
        ]
        for i, row in enumerate(reader, start=2):
            if not row:
                continue
            for col_idx, name, is_int, lo, hi, semantic in column_checks:
                if col_idx >= len(row):
                    _record(name, "missing_col",
                            f"{csv_filename} line {i}: missing column for "
                            f"metric {name!r}")
                    continue
                raw = row[col_idx]
                try:
                    value = float(raw)
                except ValueError:
                    _record(name, "non_numeric",
                            f"{csv_filename} line {i}: {name}={raw!r} not "
                            "parseable as float")
                    continue
                # ``float()`` happily parses ``"nan"`` / ``"inf"`` /
                # ``"-inf"``. Without this guard a NaN cell silently
                # passes every range check below (every comparison
                # against NaN is False) and a NaN/inf cell in a
                # ``dtype="int"`` column crashes ``round()`` with an
                # uncaught ValueError/OverflowError instead of producing
                # a violation report. Mirrors the non-finite posture of
                # ``_validate_topology_coupling``.
                if not math.isfinite(value):
                    _record(name, "non_finite",
                            f"{csv_filename} line {i}: {name}={raw!r} is "
                            "not finite")
                    continue
                if is_int:
                    if abs(value - round(value)) > _VALIDATE_INT_TOLERANCE:
                        _record(name, "fractional",
                                f"{csv_filename} line {i}: {name}={value} "
                                "is fractional but dtype='int'")
                if lo is not None and value < lo:
                    _record(name, "below_min",
                            f"{csv_filename} line {i}: {name}={value} "
                            f"below min_value={lo}")
                if hi is not None and value > hi:
                    _record(name, "above_max",
                            f"{csv_filename} line {i}: {name}={value} "
                            f"above max_value={hi}")
                if semantic in ("counter", "rate") and value < 0:
                    _record(name, "negative_kind",
                            f"{csv_filename} line {i}: {name}={value} "
                            f"is negative but semantic_type={semantic!r}")
    return violations


def _validate_component_derivations(
    output_dir: Path, schema: dict, component: str
) -> list[str]:
    """For every metric with a ``derivation`` string, recompute the value
    from its source columns and assert agreement within
    ``_VALIDATE_DERIVATION_TOLERANCE``.

    Only the derivations the generator implements are checked (the
    string carries the formula for documentation; the validator dispatches
    by ``(component, metric)``). New derived columns must be added to
    ``DERIVATIONS`` in the generator and to ``_RECOMPUTERS`` here in
    lockstep — drift is caught by the test suite (``DERIVATIONS`` and
    ``_RECOMPUTERS`` keysets must match).
    """
    csv_filename = schema["components"][component]["csv_filename"]
    csv_path = output_dir / csv_filename
    metrics = schema["components"][component]["metrics"]
    if not csv_path.exists():
        return []
    derived_entries = [m for m in metrics if m.get("derivation")]
    if not derived_entries:
        return []

    # dispatch tables raise on unknown keys. ``DERIVATIONS`` and
    # ``_RECOMPUTERS`` are paired single-source registries whose keyset
    # equality is enforced by the test suite; a missing recomputer for a
    # component whose schema declares a derivation is programmer drift,
    # not a runtime data issue, and must surface loudly instead of being
    # downgraded to a violation entry.
    recompute = _RECOMPUTERS[component]

    violations = []
    # phase 8: dimensioned per-component CSVs prepend the six
    # dim columns between ``timestamp`` and the metric block, so the
    # ``name_to_col`` index must offset by that prefix to stay aligned
    # with the recomputer's ``row[col]`` reads.
    dimensions = schema["components"][component].get("dimensions")
    metric_col_start = (
        1 + len(_INSTANCE_DIMENSION_COLUMNS) if dimensions is not None else 1
    )
    name_to_col = {
        m["name"]: i + metric_col_start for i, m in enumerate(metrics)
    }
    with open(csv_path, "r", encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        try:
            next(reader)
        except StopIteration:
            return violations
        seen: set[str] = set()
        for i, row in enumerate(reader, start=2):
            if len(seen) == len(derived_entries):
                # Every derived metric has already recorded its
                # one-per-file violation; the remaining rows cannot add
                # anything (each metric reports at most once per CSV).
                break
            if not row:
                continue
            for entry in derived_entries:
                name = entry["name"]
                if name in seen:
                    continue
                col = name_to_col.get(name)
                if col is None or col >= len(row):
                    continue
                # the per-row except is narrowed to the data-only
                # exceptions (malformed cells, accidental zero-division
                # inside a recomputer). ``KeyError`` is intentionally
                # excluded so a dispatch raise from an unknown metric
                # (drift between ``DERIVATIONS`` and the recomputer body)
                # propagates instead of being silently dropped row by row.
                try:
                    actual = float(row[col])
                    expected = recompute(name, row, name_to_col)
                except (ValueError, ZeroDivisionError):
                    continue
                if expected is None:
                    continue
                # NaN poisons the tolerance gate below: ``abs(nan - x) >
                # tol`` is False, so a corrupted derived column (or a
                # NaN source cell flowing through the recomputer) would
                # validate clean. Treat any non-finite side as a
                # violation instead.
                if not (math.isfinite(actual) and math.isfinite(expected)):
                    seen.add(name)
                    violations.append(
                        f"{csv_filename} line {i}: derived {name}={actual} "
                        f"or recomputed value {expected} is not finite "
                        f"(formula: {entry['derivation']})"
                    )
                    continue
                if abs(actual - expected) > _VALIDATE_DERIVATION_TOLERANCE:
                    seen.add(name)
                    violations.append(
                        f"{csv_filename} line {i}: derived {name}={actual} "
                        f"differs from recomputed {expected} by more than "
                        f"{_VALIDATE_DERIVATION_TOLERANCE} (formula: "
                        f"{entry['derivation']})"
                    )
    return violations


def _recompute_cacheservice(metric: str, row: list[str],
                             name_to_col: dict[str, int]) -> float | None:
    """Recompute ``cacheservice.hit_ratio`` from ``cache_hits`` / ``cache_misses``.

    Returns ``None`` when one of the source columns is absent from the
    schema header (a ``--metrics-per-component`` trim drops the column).
    Unparseable source cells raise ``ValueError`` from ``float()``; the
    caller (``_validate_component_derivations``) catches ``ValueError`` /
    ``ZeroDivisionError`` per row so the cell validator can report those
    separately.

    per-metric dispatch within the recomputer raises ``KeyError``
    on any unknown metric. ``DERIVATIONS['cacheservice']`` declares only
    ``hit_ratio``; a call with any other metric is programmer drift
    between the generator-side ``DERIVATIONS`` table and the validator-
    side recomputer body and must not be silently swallowed.
    """
    if metric != "hit_ratio":
        raise KeyError(
            f"_recompute_cacheservice: unknown metric {metric!r}; "
            f"cacheservice declares only 'hit_ratio' as derived"
        )
    hits_col = name_to_col.get("cache_hits")
    misses_col = name_to_col.get("cache_misses")
    if hits_col is None or misses_col is None:
        return None
    if hits_col >= len(row) or misses_col >= len(row):
        return None
    hits = float(row[hits_col])
    misses = float(row[misses_col])
    denom = hits + misses
    if denom <= 0:
        return 0.0
    return 100.0 * hits / denom


# Per-component derivation dispatch table. Keys must mirror
# ``DERIVATIONS`` in the generator; the validator's
# ``_validate_component_derivations`` looks up by component name and
# delegates by metric name. Adding a new derived column requires
# adding both a ``DERIVATIONS`` entry (for the generator) and a
# ``_RECOMPUTERS`` entry (for the validator).
_RECOMPUTERS: dict[str, Callable[[str, list[str], dict[str, int]],
                                  float | None]] = {
    "cacheservice": _recompute_cacheservice,
}


def _read_component_metric_column(
    csv_path: Path, metric_name: str,
) -> tuple[list[datetime.datetime], np.ndarray] | None:
    """Read a single metric column from a per-component CSV.

    Returns ``(timestamps, values)`` aligned row-by-row, or ``None`` if
    the CSV does not exist, has no header, or does not declare
    ``metric_name``. Used by ``_validate_topology_coupling`` to align
    source / target canonical load metrics on shared timestamps.

    Phase 8: when the CSV is the dim-aware long-form layout
    (multiple rows per timestamp, one per instance), values are
    collapsed to one ``(timestamp, mean)`` per unique timestamp. This
    keeps the timestamp axis monotonic (the existing
    ``_compute_anomaly_keep_mask`` forward-sweep relies on it) and
    matches the validator's "per-timestamp" coupling contract — the
    correlation is between the upstream's per-second load and the
    downstream's per-second load, not between per-instance copies of
    that load. Aggregation is the mean across instances at the
    timestamp; under the default fan-out (all instances share the
    same baseline) the mean equals any single instance's value, so
    the N=1 byte path is unchanged.
    """
    if not csv_path.exists():
        return None
    per_ts_sum: dict[datetime.datetime, float] = {}
    per_ts_count: dict[datetime.datetime, int] = {}
    order: list[datetime.datetime] = []
    with open(csv_path, "r", encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        try:
            header = next(reader)
        except StopIteration:
            return None
        try:
            col_idx = header.index(metric_name)
        except ValueError:
            return None
        for row in reader:
            if not row or col_idx >= len(row):
                continue
            try:
                ts = _parse_csv_timestamp(row[0])
                value = float(row[col_idx])
            except ValueError:
                continue
            if ts not in per_ts_count:
                order.append(ts)
                per_ts_sum[ts] = value
                per_ts_count[ts] = 1
            else:
                per_ts_sum[ts] += value
                per_ts_count[ts] += 1
    # Sort the timestamp axis so non-monotonic per-row layouts don't
    # confuse the downstream forward-sweep mask. Two sources of
    # non-monotonicity: (1) the dim-aware per-component CSV writes
    # contiguous per-instance blocks (i0 chronological, then i1
    # chronological, ...), so ``order`` restarts at ts_0 at each block
    # boundary; (2) the dimensionless ``--inject-dst-artifact-day > 0``
    # path (mutex with the multi-instance path) duplicates the 02:00–
    # 02:59 wall-clock hour, so ``order`` repeats that hour's
    # timestamps mid-CSV. The unconditional sort normalizes both into
    # a monotonic per-timestamp axis; for a plain default
    # dimensionless CSV the input is already monotonic so the sort is
    # a no-op.
    order.sort()
    values = np.array(
        [per_ts_sum[ts] / per_ts_count[ts] for ts in order],
        dtype=np.float64,
    )
    return order, values


def _read_component_metric_column_per_instance(
    csv_path: Path, metric_name: str,
) -> dict[str, tuple[list[datetime.datetime], np.ndarray]] | None:
    """Return per-instance ``(timestamps, values)`` for ``metric_name``
    from a dim-aware long-form per-component CSV.

    Returns ``None`` when the CSV does not exist, has no header, is
    dimensionless (no ``id`` column), or does not declare
    ``metric_name``. The result maps instance id → ``(timestamps,
    values)`` so the per-instance correlation check can align
    each instance's load column across components without re-walking
    the CSV per pair.

    Counterpart to ``_read_component_metric_column`` which aggregates
    via mean across instances; this helper keeps each instance's
    rows distinct so per-instance coupling can be Pearson-checked
    against its matching upstream instance.
    """
    if not csv_path.exists():
        return None
    with open(csv_path, "r", encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        try:
            header = next(reader)
        except StopIteration:
            return None
        try:
            col_idx = header.index(metric_name)
        except ValueError:
            return None
        try:
            id_idx = header.index("id")
        except ValueError:
            # Dimensionless layout — the aggregate reader is the right
            # one. Distinguish from "no header" so the caller can
            # branch on the absence specifically.
            return None
        per_instance: dict[
            str, tuple[list[datetime.datetime], list[float]]
        ] = {}
        for row in reader:
            if not row or col_idx >= len(row) or id_idx >= len(row):
                continue
            inst_id = row[id_idx]
            try:
                ts = _parse_csv_timestamp(row[0])
                value = float(row[col_idx])
            except ValueError:
                continue
            ts_list, val_list = per_instance.setdefault(inst_id, ([], []))
            ts_list.append(ts)
            val_list.append(value)
    # Per-instance CSV blocks are written in increasing timestamp
    # order by ``generate_component`` (one block per instance, rows
    # within each block ordered by elapsed seconds), and the DST
    # splice that produces non-monotonic timestamps is rejected up
    # front for non-anonymous instances — by ``parse_args`` on the
    # CLI path and by ``generate_component``'s own
    # ``dst_inject_day > 0`` + non-anonymous-instance guard for
    # direct programmatic callers. Reading rows in CSV order
    # therefore yields a monotonic ``ts_list`` per instance already —
    # no sort needed. Skipping the O(n log n) work noticeably speeds
    # up the validator on long runs.
    out: dict[str, tuple[list[datetime.datetime], np.ndarray]] = {}
    for inst_id, (ts_list, val_list) in per_instance.items():
        out[inst_id] = (ts_list, np.array(val_list, dtype=np.float64))
    return out


def _read_anomaly_exclusion_windows(
    anomalies_path: Path,
) -> list[tuple[datetime.datetime, datetime.datetime, str, str]]:
    """Return one padded ``(start, end, component, metric)`` tuple per row in
    ``anomalies.csv``.

    Each window spans ``[span_start, span_end]`` from the manifest with
    ``_TOPOLOGY_CORRELATION_EXCLUSION_PAD_SECONDS`` added on either side
    so the validator can excise the entire shaped anomaly span when the
    targeted column is one of the two columns the per-edge correlation
    reads. Cascade rows have ``span_start == span_end`` so they get a
    2*pad point exclusion. Returns an empty list when the manifest is
    missing — a run with ``metrics`` opted out of ``--emit``
    won't have one.

    The ``component`` / ``metric`` fields are carried in the tuple so
    ``_apply_anomaly_exclusion`` can filter windows down to those that
    actually touch the columns being correlated. Excluding *every*
    anomaly's time range globally would erase entire days for unrelated
    long ramps (e.g. ``db_stall``'s 24h ``disk_used_pct`` ramp) and
    leave too few rows to test coupling on the load columns themselves.

    Older anomalies.csv variants (or rows missing the columns) fall
    back to a point exclusion around the ``timestamp`` field; the
    column-fallback chain ensures the validator stays usable across
    manifest revisions even if the columns drift.
    """
    if not anomalies_path.exists():
        return []
    windows: list[tuple[datetime.datetime, datetime.datetime, str, str]] = []
    pad = datetime.timedelta(
        seconds=_TOPOLOGY_CORRELATION_EXCLUSION_PAD_SECONDS
    )
    with open(anomalies_path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            start_raw = row.get("span_start") or row.get("timestamp")
            end_raw = row.get("span_end") or start_raw
            if not start_raw:
                continue
            try:
                start_dt = _parse_csv_timestamp(start_raw)
                end_dt = _parse_csv_timestamp(end_raw)
            except ValueError:
                continue
            component = row.get("component") or ""
            metric = row.get("metric") or ""
            windows.append((start_dt - pad, end_dt + pad, component, metric))
    windows.sort()
    return windows


def _filter_windows_for_pair(
    windows: list[tuple[datetime.datetime, datetime.datetime, str, str]],
    source_component: str, source_metric: str,
    target_component: str, target_metric: str,
) -> list[tuple[datetime.datetime, datetime.datetime]]:
    """Return ``(start, end)`` ranges touching the columns that affect a pair.

    Drops every window whose ``(component, metric)`` does not match one
    of the load columns the topology pipeline composes into the target's
    canonical load metric: the source's canonical metric, the target's
    canonical metric, *or* any other incoming-edge source's captured
    load metric. The third category matters when the target is fed by
    more than one upstream (e.g. ``database`` is composed of an
    apigateway-driven constant edge plus a cacheservice-driven callable
    edge): an anomaly on the *other* upstream's load column shifts the
    target's baseline in a way that decouples it from the source under
    test, so excluding those windows keeps the Pearson check focused on
    the contract this specific edge enforces.

    Used by ``_validate_topology_coupling`` so anomalies on unrelated
    columns (e.g. ``disk_used_pct``) do not shrink the correlation pool
    while anomalies that genuinely break the load coupling do.
    """
    targets: set[tuple[str, str]] = {
        (source_component, source_metric),
        (target_component, target_metric),
    }
    # Walk reverse-adjacency of the live TOPOLOGY restricted to the
    # target: every component with an outgoing edge to the target is an
    # upstream contributor whose captured load columns can shift the
    # target's baseline.
    for upstream, edges in TOPOLOGY.items():
        if upstream == source_component:
            continue
        if not any(edge.target == target_component for edge in edges):
            continue
        ups_entry = _TOPOLOGY_LOAD_METRICS.get(upstream)
        if ups_entry is None:
            continue
        canonical, supplementary = ups_entry
        if canonical:
            targets.add((upstream, canonical))
        for name in supplementary:
            if name:
                targets.add((upstream, name))
    return [
        (start, end)
        for start, end, comp, metric in windows
        if (comp, metric) in targets
    ]


def _compute_anomaly_keep_mask(
    timestamps: list[datetime.datetime],
    windows: list[tuple[datetime.datetime, datetime.datetime]],
) -> np.ndarray:
    """Build a boolean ``keep`` mask flagging rows outside every window.

    ``True`` at position ``i`` means ``timestamps[i]`` falls in *no*
    exclusion window. ``False`` means at least one window covers it.

    Runs in ``O(N + W log W)`` time: windows are sorted by start and
    overlapping windows are merged into disjoint intervals; a single
    forward sweep over ``timestamps`` (which the validator passes in
    chronological row-emission order) advances an index over the
    merged intervals so each timestamp checks at most one interval.
    This replaces the previous ``O(N * W)`` nested-loop check; on a
    7-day run (~604,800 rows) with ~30 exclusion windows per pair,
    the cost drops from ~18M comparisons to ~604,800 across all pairs.
    """
    n = len(timestamps)
    keep = np.ones(n, dtype=bool)
    if not windows or n == 0:
        return keep
    sorted_windows = sorted(windows)
    merged: list[tuple[datetime.datetime, datetime.datetime]] = []
    for w_start, w_end in sorted_windows:
        if merged and w_start <= merged[-1][1]:
            prev_start, prev_end = merged[-1]
            merged[-1] = (prev_start, max(prev_end, w_end))
        else:
            merged.append((w_start, w_end))
    w_idx = 0
    w_count = len(merged)
    for i, ts in enumerate(timestamps):
        while w_idx < w_count and merged[w_idx][1] < ts:
            w_idx += 1
        if w_idx == w_count:
            break
        if merged[w_idx][0] <= ts:
            keep[i] = False
    return keep


def _apply_anomaly_exclusion(
    timestamps: list[datetime.datetime],
    values: np.ndarray,
    windows: list[tuple[datetime.datetime, datetime.datetime]],
) -> np.ndarray:
    """Drop rows whose timestamp falls in any anomaly exclusion window.

    Thin wrapper around ``_compute_anomaly_keep_mask`` retained as the
    single-array entry point; callers that need to filter two arrays
    aligned on the same timestamps (e.g. source/target in
    ``_validate_topology_coupling``) should compute the mask once via
    ``_compute_anomaly_keep_mask`` and index both arrays directly.
    """
    if not windows:
        return values
    keep = _compute_anomaly_keep_mask(timestamps, windows)
    return values[keep]


def _validate_topology_coupling(
    output_dir: Path, schema: dict,
) -> list[str]:
    """Verify every declared coupling edge produces a high Pearson correlation
    between the upstream's canonical load metric and the downstream's
    canonical load metric.

    Skipped silently — returning an empty list — when:

    - ``metadata.topology_mode != "realistic"`` (documents produced under
      the historic ``independent`` mode carry decoupled baselines by
      construction, so there is no coupling to check).
    - The schema document has no ``topology`` section (older schema docs
      written before phase 7; the loader rejects unknown
      ``schema_version`` values outright, but defensive code paths can
      still land here).
    - Either side's CSV is missing or fails to declare its canonical
      ``_TOPOLOGY_LOAD_METRICS`` metric (e.g. ``--metrics-per-component``
      trimmed the column away).

    Each edge whose weight is the literal string ``"callable"`` is also
    skipped — the per-row weight is the dominant signal in that case
    (e.g. cache-miss ratio driving database QPS), not the upstream load
    column the Pearson check inspects. Edges with ``weight == 0`` are
    likewise skipped because ``_validate_topology`` accepts them as a
    saturation-only placeholder that does not contribute to the
    downstream load baseline. The intent of the check is to catch
    silent coupling regressions on the constant-weight edges with
    non-zero load contribution, where upstream→downstream load
    tracking is the contract.

    Each surviving edge contributes one violation message when the
    realized Pearson correlation falls below the per-edge threshold
    (``Edge.correlation_threshold`` on the live ``TOPOLOGY``, falling
    back to ``_TOPOLOGY_DEFAULT_CORRELATION_THRESHOLD``).

    Malformed schema entries — a non-``dict`` ``topology`` block, a
    non-``list`` edge container, a non-``dict`` edge entry, a
    missing/non-string ``target``, a missing ``weight`` field, a
    ``weight`` that is neither numeric nor the literal string
    ``"callable"``, a non-finite numeric ``weight`` (Python's
    ``json`` parses ``NaN``/``Infinity``/``-Infinity`` as float by
    default), or a ``correlation_threshold`` that is non-numeric,
    ``bool``, not finite, or outside the half-open range
    ``(-1, 1]`` — surface as dedicated violation messages rather
    than crashing the validator. A hand-edited or older
    ``schema.json`` therefore degrades to a clear report instead of
    a ``KeyError``, ``AttributeError``, or ``TypeError`` traceback.
    An invalid ``correlation_threshold`` additionally falls back to
    ``_resolve_edge_correlation_threshold(source, target)`` so the
    rest of the coupling check still runs.

    Non-finite cell values (NaN, +/-inf) in either canonical load
    column are also flagged: ``np.std`` and ``np.corrcoef`` both
    return NaN on such input, and ``corr < threshold`` would
    silently evaluate False and bypass the check. Well-formed runs
    only emit finite floats, so this guard only fires on
    hand-edited or otherwise corrupted CSVs.
    """
    if schema["metadata"].get("topology_mode") != "realistic":
        return []
    topology = schema.get("topology")
    if topology is None or topology == {}:
        # Missing or empty block is the older-schema / narrow-run
        # case; degrade silently. Anything else must be a dict —
        # ``topology.keys()`` would crash on a list/string/scalar
        # before any per-edge violation could surface, so surface
        # one dedicated violation up front instead.
        return []
    if not isinstance(topology, dict):
        return [
            f"topology block malformed in schema.json (expected "
            f"dict, got {type(topology).__name__})"
        ]

    anomaly_windows = _read_anomaly_exclusion_windows(
        output_dir / "anomalies.csv"
    )

    # Per-run cache for the per-instance long-form column reads: one
    # parse per (component, metric) for the whole edge loop instead of
    # one per edge (see _validate_topology_coupling_per_instance).
    per_instance_column_cache: dict = {}

    violations: list[str] = []
    for source in sorted(topology.keys()):
        source_entry = _TOPOLOGY_LOAD_METRICS.get(source)
        if source_entry is None:
            # An edge whose source isn't in the load-metrics registry
            # can't be correlation-checked because we don't know which
            # column to read. Skip silently — _validate_topology()
            # already rejects this at import time so reaching here would
            # mean a schema doc that names a source the current build
            # doesn't recognize, which is a separate concern.
            continue
        source_edges = topology.get(source)
        if not isinstance(source_edges, list):
            violations.append(
                f"topology coupling {source}: edge list malformed in "
                f"schema.json (expected list, got "
                f"{type(source_edges).__name__})"
            )
            continue
        source_canonical = source_entry[0]
        source_data = _read_component_metric_column(
            output_dir / f"{source}.csv", source_canonical
        )
        if source_data is None:
            continue
        source_ts, source_vals = source_data

        # Align on the intersection of timestamps (drop-rate noise
        # means a given second can appear in one CSV but not the
        # other). Build a dict lookup for the source side and walk
        # each target side to keep this O(N) rather than O(N^2).
        source_map = {ts: v for ts, v in zip(source_ts, source_vals)}

        for edge_entry in source_edges:
            if not isinstance(edge_entry, dict):
                violations.append(
                    f"topology coupling {source}: edge entry malformed "
                    f"in schema.json (expected dict, got "
                    f"{type(edge_entry).__name__})"
                )
                continue
            target = edge_entry.get("target")
            if not isinstance(target, str) or not target:
                violations.append(
                    f"topology coupling {source}: edge entry missing or "
                    f"invalid 'target' in schema.json (got {target!r})"
                )
                continue
            if "weight" not in edge_entry:
                violations.append(
                    f"topology coupling {source}->{target}: edge entry "
                    f"missing 'weight' in schema.json"
                )
                continue
            weight = edge_entry["weight"]
            if weight == "callable":
                continue
            if not isinstance(weight, (int, float)) or isinstance(
                weight, bool
            ):
                violations.append(
                    f"topology coupling {source}->{target}: edge weight "
                    f"in schema.json must be a number or the literal "
                    f"\"callable\" (got {weight!r})"
                )
                continue
            # Python's ``json`` loader parses ``NaN``/``Infinity``/
            # ``-Infinity`` as float by default (a CPython extension);
            # ``_validate_topology()`` rejects those values on the live
            # ``Edge`` so the validator's schema view must match. A
            # non-finite weight cannot drive a meaningful Pearson check
            # either, so flag it and skip the edge.
            if not math.isfinite(float(weight)):
                violations.append(
                    f"topology coupling {source}->{target}: edge weight "
                    f"in schema.json must be finite "
                    f"(got {weight!r})"
                )
                continue
            if weight == 0.0:
                # ``_validate_topology()`` accepts ``weight == 0`` as a
                # saturation-only placeholder: the edge declares the
                # logistic feedback shape (`Edge.saturation`) without
                # contributing to the downstream's canonical load
                # baseline. ``_compose_topology_coupled_specs`` skips
                # zero-weight constant edges for the same reason, so
                # there is no load-coupling contract to check here.
                continue
            target_entry = _TOPOLOGY_LOAD_METRICS.get(target)
            if target_entry is None:
                continue
            target_canonical = target_entry[0]
            target_data = _read_component_metric_column(
                output_dir / f"{target}.csv", target_canonical
            )
            if target_data is None:
                continue
            target_ts, target_vals = target_data

            common_ts: list[datetime.datetime] = []
            target_aligned: list[float] = []
            source_aligned: list[float] = []
            for ts, v in zip(target_ts, target_vals):
                src_v = source_map.get(ts)
                if src_v is None:
                    continue
                common_ts.append(ts)
                target_aligned.append(v)
                source_aligned.append(src_v)
            if len(common_ts) < 100:
                # Too few aligned rows to compute a meaningful Pearson
                # correlation — happens on intentionally narrow
                # ``--components`` or extreme ``--interval-seconds``
                # selections; not a coupling regression.
                continue

            # Resolve and validate the per-edge correlation threshold
            # exactly once per edge, before any comparison or
            # formatting. A hand-edited schema can carry any JSON value
            # here; treat the same set of invalid shapes the live
            # ``Edge.correlation_threshold`` validator rejects
            # (non-numeric, ``bool``, NaN, +/-inf, outside ``(-1, 1]``)
            # as a dedicated violation and fall back to the live
            # ``TOPOLOGY``'s value (or the module default) so the rest
            # of the check still runs cleanly.
            raw_threshold = edge_entry.get("correlation_threshold")
            if raw_threshold is None:
                threshold = _resolve_edge_correlation_threshold(
                    source, target
                )
            elif (
                isinstance(raw_threshold, bool)
                or not isinstance(raw_threshold, (int, float))
                or not math.isfinite(float(raw_threshold))
                or not (-1.0 < float(raw_threshold) <= 1.0)
            ):
                violations.append(
                    f"topology coupling {source}->{target}: "
                    f"correlation_threshold in schema.json must be a "
                    f"finite number in (-1, 1] or null "
                    f"(got {raw_threshold!r}); falling back to live "
                    f"TOPOLOGY"
                )
                threshold = _resolve_edge_correlation_threshold(
                    source, target
                )
            else:
                threshold = float(raw_threshold)

            source_arr = np.array(source_aligned, dtype=np.float64)
            target_arr = np.array(target_aligned, dtype=np.float64)
            pair_windows = _filter_windows_for_pair(
                anomaly_windows,
                source, source_canonical,
                target, target_canonical,
            )
            # Source and target share ``common_ts`` and ``pair_windows``,
            # so compute the keep mask once and apply it to both arrays.
            # This halves the exclusion cost per edge and keeps the
            # validator's hot path linear in the number of rows.
            if pair_windows:
                keep_mask = _compute_anomaly_keep_mask(
                    common_ts, pair_windows
                )
                source_kept = source_arr[keep_mask]
                target_kept = target_arr[keep_mask]
            else:
                source_kept = source_arr
                target_kept = target_arr
            if len(source_kept) < 100:
                continue
            # Non-finite values (NaN/+/-inf) in either column would
            # poison ``np.std`` and ``np.corrcoef`` (both return NaN),
            # silently flipping ``corr < threshold`` to False and
            # bypassing the coupling check. Treat any non-finite cell
            # as a regression — well-formed runs only ever write
            # finite floats, so this only fires on hand-edited or
            # otherwise corrupted CSVs.
            source_finite = np.isfinite(source_kept)
            target_finite = np.isfinite(target_kept)
            if not (source_finite.all() and target_finite.all()):
                sides: list[str] = []
                if not source_finite.all():
                    sides.append(f"{source}.{source_canonical}")
                if not target_finite.all():
                    sides.append(f"{target}.{target_canonical}")
                violations.append(
                    f"topology coupling {source}->{target}: "
                    f"non-finite values in "
                    f"{' and '.join(sides)} "
                    f"(NaN/+/-inf); Pearson correlation undefined "
                    f"(expected >= {threshold:.4f})"
                )
                continue
            # Pearson is undefined when either side is constant
            # (zero-variance column). Treat that as a coupling
            # regression: a constant downstream load is exactly the
            # mutation the validator is supposed to flag.
            source_std = float(np.std(source_kept))
            target_std = float(np.std(target_kept))
            if source_std == 0.0 or target_std == 0.0:
                # Name the offending side(s) explicitly — both std
                # values are already computed, and "X or Y" forces the
                # operator to inspect two columns when the violation
                # already knows which one is constant.
                sides = []
                if source_std == 0.0:
                    sides.append(f"{source}.{source_canonical}")
                if target_std == 0.0:
                    sides.append(f"{target}.{target_canonical}")
                violations.append(
                    f"topology coupling {source}->{target}: zero-variance "
                    f"column ({' and '.join(sides)}); Pearson correlation "
                    f"undefined (expected >= {threshold:.4f})"
                )
                continue
            corr = float(np.corrcoef(source_kept, target_kept)[0, 1])
            if corr < threshold:
                violations.append(
                    f"topology coupling {source}->{target}: "
                    f"Pearson({source}.{source_canonical}, "
                    f"{target}.{target_canonical})={corr:.4f} "
                    f"below threshold {threshold:.4f}"
                )

            # phase 8: per-instance correlation. When both
            # sides' schemas declare ``dimensions`` with matched
            # cardinalities (1:1 routing applies), additionally
            # verify Pearson(source.iK, target.iK) >= threshold for
            # each matching pair. Runs unconditionally — even if the
            # aggregate-mean check above just recorded a violation,
            # the per-instance breakdown is still useful (it names
            # the exact pod pair that diverged from the 1:1 contract,
            # which the mean-aggregate Pearson alone cannot do).
            violations += _validate_topology_coupling_per_instance(
                output_dir, schema, source, target,
                source_canonical, target_canonical,
                threshold, anomaly_windows,
                column_cache=per_instance_column_cache,
            )
    return violations


def _validate_topology_coupling_per_instance(
    output_dir: Path, schema: dict,
    source: str, target: str,
    source_canonical: str, target_canonical: str,
    threshold: float,
    anomaly_windows: list[tuple[datetime.datetime, datetime.datetime, str, str]],
    column_cache: dict | None = None,
) -> list[str]:
    """Per-instance edge correlation check (phase 8).

    Only fires when both ``source`` and ``target`` schemas declare a
    ``dimensions`` block with matched cardinalities. Under
    matched-cardinality 1:1 routing, downstream instance ``K``'s
    canonical load metric is driven by upstream instance ``K``'s
    canonical load metric exclusively; the Pearson correlation
    between the two should clear the same per-edge threshold the
    aggregate-mean check above uses.

    Returns one violation per failing pod pair so the report names
    the exact instance that diverged from the contract. Skips
    silently when:

    - Either side is dimensionless (no per-instance CSV layout).
    - Cardinalities mismatch (uniform fan-out fallback — per-pod
      isolation is not the contract there).
    - Either side has fewer than 100 aligned rows for the matching
      instance.
    - Either column is zero-variance or non-finite (flagged by the
      aggregate check above; surfacing once is enough).

    Instance pairing matches the generator's index-based 1:1
    routing in ``_per_instance_upstream_view``: position ``K`` in
    the source CSV is paired with position ``K`` in the target CSV.
    This keeps the validator consistent with the generator across
    ``--instance-config`` runs where the two components declare
    instances in different orders, and across
    ``--instances-per-component`` runs whose ids ("i0".."i19")
    don't lexically sort in the same order as their generation
    index.
    """
    components_schema = schema.get("components")
    if not isinstance(components_schema, dict):
        return []

    def _find_dimensions(name: str) -> dict | None:
        entry = components_schema.get(name)
        if isinstance(entry, dict):
            dims = entry.get("dimensions")
            if isinstance(dims, dict):
                return dims
        return None

    source_dims = _find_dimensions(source)
    target_dims = _find_dimensions(target)
    if source_dims is None or target_dims is None:
        return []
    src_card = source_dims.get("cardinality")
    tgt_card = target_dims.get("cardinality")
    if (
        not isinstance(src_card, int)
        or not isinstance(tgt_card, int)
        or src_card != tgt_card
        or src_card <= 1
    ):
        # Mismatched cardinalities or single-instance — uniform
        # fan-out applies, per-pod isolation is not the contract.
        return []

    def _read_cached(component: str, metric: str):
        # One full long-form parse per (CSV, metric) per validation run:
        # a source with several outgoing edges (apigateway has four)
        # used to have its entire per-instance CSV re-parsed once per
        # edge. The cache is owned by ``_validate_topology_coupling``
        # and lives only for the duration of one validation pass.
        if column_cache is None:
            return _read_component_metric_column_per_instance(
                output_dir / f"{component}.csv", metric
            )
        key = (component, metric)
        if key not in column_cache:
            column_cache[key] = _read_component_metric_column_per_instance(
                output_dir / f"{component}.csv", metric
            )
        return column_cache[key]

    source_per_inst = _read_cached(source, source_canonical)
    target_per_inst = _read_cached(target, target_canonical)
    if source_per_inst is None or target_per_inst is None:
        return []

    # Pair instances by CSV-block insertion order, NOT sorted id.
    # ``generate_component`` writes one block per instance in
    # ``instances[k]`` order, and the 1:1 routing in
    # ``_per_instance_upstream_view`` maps downstream instance ``K``
    # to upstream instance ``K`` by *list index*. Sorting by id here
    # would mis-pair when ``--instance-config`` lists the two
    # components' instances in different declared orders, or when
    # ``--instances-per-component`` produces ``i0..i9, i10`` whose
    # lexical sort ("i10" < "i2") drifts from the numeric block
    # order. ``_read_component_metric_column_per_instance`` walks
    # rows top-to-bottom and uses ``setdefault``, so dict iteration
    # order matches the on-disk block order, which matches the
    # generator's instance list order.
    source_ids = list(source_per_inst.keys())
    target_ids = list(target_per_inst.keys())
    if len(source_ids) != len(target_ids):
        return []

    violations: list[str] = []
    # Loop-invariant: the window filter depends only on the edge's
    # (component, metric) pairs, not on the pod pair — hoisted out of
    # the per-pod loop.
    pair_windows = _filter_windows_for_pair(
        anomaly_windows,
        source, source_canonical,
        target, target_canonical,
    )
    for src_id, tgt_id in zip(source_ids, target_ids):
        src_ts, src_vals = source_per_inst[src_id]
        tgt_ts, tgt_vals = target_per_inst[tgt_id]
        common_ts: list[datetime.datetime] = []
        target_aligned: list[float] = []
        source_aligned: list[float] = []

        # Linear-time two-pointer merge join (phase 8). Since
        # both CSV blocks were read into monotonic timestamp lists,
        # we can align them in O(N+M) without materialising a full
        # dict per pod pair.
        i = 0
        j = 0
        n_src = len(src_ts)
        n_tgt = len(tgt_ts)
        while i < n_src and j < n_tgt:
            s_t = src_ts[i]
            t_t = tgt_ts[j]
            if s_t == t_t:
                common_ts.append(s_t)
                source_aligned.append(src_vals[i])
                target_aligned.append(tgt_vals[j])
                i += 1
                j += 1
            elif s_t < t_t:
                i += 1
            else:
                j += 1

        if len(common_ts) < 100:
            continue
        source_arr = np.array(source_aligned, dtype=np.float64)
        target_arr = np.array(target_aligned, dtype=np.float64)
        if pair_windows:
            keep_mask = _compute_anomaly_keep_mask(common_ts, pair_windows)
            source_kept = source_arr[keep_mask]
            target_kept = target_arr[keep_mask]
        else:
            source_kept = source_arr
            target_kept = target_arr
        if len(source_kept) < 100:
            continue
        if not (np.isfinite(source_kept).all() and np.isfinite(target_kept).all()):
            # Aggregate check already flagged non-finite values; skip
            # silently to avoid duplicate noise.
            continue
        if np.std(source_kept) == 0.0 or np.std(target_kept) == 0.0:
            continue
        corr = float(np.corrcoef(source_kept, target_kept)[0, 1])
        if corr < threshold:
            violations.append(
                f"topology coupling {source}.{src_id}->{target}.{tgt_id}: "
                f"Pearson({source_canonical}, {target_canonical})="
                f"{corr:.4f} below threshold {threshold:.4f} "
                f"(per-instance, matched cardinality)"
            )
    return violations


def _resolve_edge_correlation_threshold(source: str, target: str) -> float:
    """Look up the per-edge ``correlation_threshold`` from live ``TOPOLOGY``.

    Falls back to ``_TOPOLOGY_DEFAULT_CORRELATION_THRESHOLD`` when the
    edge is not declared in the live module (e.g. the schema was written
    by a build that declared an edge the current build no longer ships)
    or when ``Edge.correlation_threshold`` is ``None``.
    """
    for edge in TOPOLOGY.get(source, ()):
        if edge.target == target:
            if edge.correlation_threshold is not None:
                return float(edge.correlation_threshold)
            break
    return _TOPOLOGY_DEFAULT_CORRELATION_THRESHOLD


def _schema_has_any_dimensions(schema: dict) -> bool:
    """True iff any component in the schema declares a ``dimensions`` block.

    The long-form file writers (``gauges.csv`` and
    ``combined_metrics_unified.csv``) dispatch to the dim-aware 10-column
    layout when *any* per-component CSV in the run is dimensioned; this
    helper mirrors that any-of predicate so the validator's long-form
    header check stays in lockstep with the writer.
    """
    return any(
        payload.get("dimensions") is not None
        for payload in schema.get("components", {}).values()
    )


def _validate_long_form_dimensions(
    output_dir: Path, schema: dict,
) -> list[str]:
    """Verify ``gauges.csv`` and ``combined_metrics_unified.csv`` headers
    match the layout implied by the schema's per-component ``dimensions``
    blocks.

    Phase 5 made both long-form writers dim-aware: a run with any
    dimensioned per-component CSV dispatches to the 10-column
    ``timestamp, component, id, host, pod, az, region, tenant, metric,
    value`` shape; otherwise the historic 4-column
    ``timestamp, component, metric, value`` (gauges) and wide
    ``timestamp, component_metric, ...`` (combined) layouts stay
    byte-identical. This validator only enforces the long-form 10-column
    header when at least one component declares ``dimensions``. The
    classic 4-column / wide header is *not* checked here (status quo —
    no validator inspects those headers in v1; the layout is pinned only
    by the writers' locked SHA-256 golden hashes in
    ``tests/test_gauges_file.py`` / ``tests/test_combine.py``). The
    existing required-files / unknown-files checks only verify file
    presence, not column layout, so they don't fill that gap.

    Only files actually declared in ``schema.files`` are inspected —
    missing-file flagging is already covered by
    ``_validate_required_files_present``, and an undeclared on-disk file
    is caught by ``_validate_no_unknown_files``.
    """
    if not _schema_has_any_dimensions(schema):
        return []
    declared_files = set(schema.get("files", []))
    violations: list[str] = []
    expected = (
        "timestamp",
        "component",
        *_INSTANCE_DIMENSION_COLUMNS,
        "metric",
        "value",
    )
    for filename in ("gauges.csv", _COMBINE_OUTPUT_FILENAME):
        if filename not in declared_files:
            continue
        path = output_dir / filename
        if not path.exists():
            continue  # covered by _validate_required_files_present
        with open(path, "r", encoding="utf-8", newline="") as f:
            reader = csv.reader(f)
            try:
                header = next(reader)
            except StopIteration:
                violations.append(
                    f"{filename}: file has no header row "
                    "(dim-aware long-form layout expected)"
                )
                continue
        if tuple(header) != expected:
            violations.append(
                f"{filename}: header {header} does not match dim-aware "
                f"long-form layout {list(expected)}"
            )
    return violations


def validate_output(output_dir: Path) -> list[str]:
    """Run every validation against the artifacts in ``output_dir``.

    Returns the list of violation messages (empty when the directory is
    fully consistent with its ``schema.json``). The CLI layer
    (the ``validate`` subcommand) prints the list, decides the exit code based on
    ``--warn``, and is the only caller that touches ``sys.exit``.
    """
    schema_path = output_dir / "schema.json"
    schema = _load_schema_document(schema_path)
    violations: list[str] = []
    violations += _validate_required_files_present(output_dir, schema)
    violations += _validate_no_unknown_files(output_dir, schema)
    if "metrics" in schema["metadata"].get("emit_selection", []):
        violations += _validate_anomalies_sorted(output_dir, schema)
    for component in schema["metadata"]["components"]:
        violations += _validate_component_row_count(output_dir, schema, component)
        violations += _validate_component_timestamp_coverage(
            output_dir, schema, component
        )
        violations += _validate_component_cells(output_dir, schema, component)
        violations += _validate_component_derivations(
            output_dir, schema, component
        )
    violations += _validate_long_form_dimensions(output_dir, schema)
    violations += _validate_topology_coupling(output_dir, schema)
    return violations


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
    append_activity_log: bool = True,
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

    def _keyed_iter(component: str, csv_path: Path):
        for ts, comp, values, dimensions in _iter_component_rows(component, csv_path):
            yield (_parse_csv_timestamp(ts), ts, comp, values, dimensions)

    # Sort internally (matching write_gauges_csv) so the equal-timestamp
    # component tiebreaker holds regardless of how the caller built the
    # mapping. main() already passes a sorted dict, so the live OTLP
    # emission order is unchanged; this closes the asymmetry for direct
    # callers only.
    iters = [
        _keyed_iter(c, p)
        for c, p in sorted(component_csv_paths.items())
        if p.exists()
    ]

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
                http_error_fields = _http_error_activity_fields(
                    exc, body, content_type, verbose=verbose
                )
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
                        **http_error_fields,
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
                    **http_error_fields,
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
        if activity_log_path is not None:
            activity_log_path.parent.mkdir(parents=True, exist_ok=True)
            # Append so a prior stream_otel_signals run's records are preserved.
            # Gauge-only CLI mode passes ``append_activity_log=False`` because
            # there is no signal pass creating a fresh log for this run.
            mode = "a" if append_activity_log else "w"
            log_file = open(activity_log_path, mode, encoding="utf-8")

        _write_activity(
            log_file,
            "START",
            signal="metrics_gauge",
            components=",".join(sorted(component_csv_paths.keys())),
            batch_seconds=batch_seconds,
            protocol=protocol,
            speedup=speedup,
        )
        for dt, ts, comp, values, dimensions in heapq.merge(
            *iters, key=lambda item: item[0]
        ):
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
            # Precompute the nanos once per CSV row, not per data point — the
            # gauge builders read this field directly, skipping the per-point
            # ``strptime`` that previously dominated request-encoding cost.
            ts_nano = _dt_to_unix_nanos(dt)
            for metric_name, value in values:
                batch.append({
                    "timestamp": ts,
                    "time_unix_nano": ts_nano,
                    "component": comp,
                    "metric": metric_name,
                    "value": value,
                    "dimensions": dimensions,
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


def _collect_emitted_filenames(*, emit_selection, components, combine):
    """Return the sorted list of filenames a run with the given options writes.

    Same single source of truth ``_pre_clean_output_dir`` and the end-of-run
    summary already consume: ``_EMIT_ARTIFACT_FILES`` for emit-typed artifacts,
    ``_COMBINE_OUTPUT_FILENAME`` for the combine output, and one
    ``{component}.csv`` per allowlisted component when ``metrics`` is selected.

    Used by ``write_schema_json`` and the ``validate`` subcommand to keep the
    expected-file-set check anchored to one definition.
    """
    files: set[str] = set()
    if "metrics" in emit_selection:
        for component in components:
            files.add(f"{component}.csv")
    for emit_type, artifact_files in _EMIT_ARTIFACT_FILES.items():
        if emit_type in emit_selection:
            files.update(artifact_files)
    if combine:
        files.add(_COMBINE_OUTPUT_FILENAME)
    return sorted(files)


def _pre_clean_output_dir(output_dir, emit_selection, selected_components, combine):
    """Remove stale artifacts from a prior run that this run will not regenerate.

    Called right after --output-dir is created. Idempotent on missing files.
    Files unknown to this script (e.g. user notes, the synthetic-extra-component
    CSV the test fixture relies on for combine autodiscovery) are left alone.
    Not called by the ``combine`` subcommand; that path reads existing
    per-component CSVs as inputs.
    """
    metrics_on = "metrics" in emit_selection
    # Per-component CSVs: drop any that this run will not (re)write — either
    # because metrics was dropped from --emit or because the
    # component is no longer in --components.
    for component in COMPONENTS:
        if metrics_on and component in selected_components:
            continue
        (output_dir / f"{component}.csv").unlink(missing_ok=True)
    # Emit-typed artifacts: drop files for any emit type not selected.
    for emit_type, files in _EMIT_ARTIFACT_FILES.items():
        if emit_type in emit_selection:
            continue
        for filename in files:
            (output_dir / filename).unlink(missing_ok=True)
    # combined_metrics_unified.csv: only the 'combined' artifact writes it. Drop stale
    # output otherwise so it can't masquerade as this run's result.
    if not combine:
        (output_dir / _COMBINE_OUTPUT_FILENAME).unlink(missing_ok=True)


_SUBCOMMANDS = ("generate", "combine", "validate", "serve")


def _main_combine_subcommand(argv):
    """``combine DIR [--components ...]``: skip generation and join the
    existing per-component CSVs in DIR into combined_metrics_unified.csv.
    """
    sp = argparse.ArgumentParser(
        prog="anomaly-metric-creator.py combine",
        description="Join existing per-component CSVs in DIR into "
                    "combined_metrics_unified.csv (no generation).",
    )
    sp.add_argument("directory", type=Path,
                    help="Directory holding the per-component CSVs of a "
                         "prior run (a previous run's --output-dir).")
    sp.add_argument("--components", type=str, default="all",
                    help="Comma-separated allowlist of component CSVs to "
                         "combine; 'all' (default) autodiscovers every "
                         "*.csv in DIR.")
    a = sp.parse_args(argv)
    if not a.directory.is_dir():
        if a.directory.exists():
            sp.error(f"combine requires a directory; "
                     f"{a.directory} exists but is not one")
        sp.error(f"combine requires an existing directory; "
                 f"{a.directory} does not exist")
    selected = _parse_components_value(sp.error, a.components)
    if selected == set(COMPONENTS.keys()):
        combine_components = None
    else:
        combine_components = [name for name in COMPONENTS if name in selected]
    combine_logs(a.directory, components=combine_components)


def _main_validate_subcommand(argv):
    """``validate DIR [--warn]``: check the artifacts in DIR against
    DIR/schema.json and exit 1 on violations (0 with --warn).
    """
    sp = argparse.ArgumentParser(
        prog="anomaly-metric-creator.py validate",
        description="Validate the artifacts in DIR against DIR/schema.json.",
    )
    sp.add_argument("directory", type=Path,
                    help="Directory holding a prior run's artifacts, "
                         "including the schema.json written via "
                         "--emit ...,schema.")
    sp.add_argument("--warn", action="store_true",
                    help="Report violations on stderr but exit 0 (default: "
                         "exit 1 on any violation).")
    a = sp.parse_args(argv)
    if not a.directory.is_dir():
        if a.directory.exists():
            sp.error(f"validate requires a directory; "
                     f"{a.directory} exists but is not one")
        sp.error(f"validate requires an existing directory; "
                 f"{a.directory} does not exist")
    try:
        violations = validate_output(a.directory)
    except ValueError as exc:
        sp.error(str(exc))
    for line in violations:
        print(f"VALIDATION: {line}", file=sys.stderr)
    if not violations:
        print(f"validate: {a.directory} OK (no violations)")
        return
    if a.warn:
        print(f"validate: {len(violations)} violation(s) in "
              f"{a.directory} (--warn: returning 0)", file=sys.stderr)
        return
    raise SystemExit(1)


def _main_serve_subcommand(argv):
    """``serve [server flags] [generate flags...]``: run the simulator as an
    HTTP server with Kubernetes/Helm command responses and debug APIs.
    """
    from .server import serve_main

    return serve_main(argv, legacy_module=sys.modules[__name__])


def main(argv=None):
    # Subcommand dispatch: 'generate' (the default when the first token is
    # not a subcommand, preserving every historic invocation), 'combine',
    # and 'validate'. Handled before argparse so the flat generate parser
    # never sees the subcommand token.
    argv = list(sys.argv[1:]) if argv is None else list(argv)
    if argv and argv[0] in _SUBCOMMANDS:
        sub, rest = argv[0], argv[1:]
        if sub == "combine":
            return _main_combine_subcommand(rest)
        if sub == "validate":
            return _main_validate_subcommand(rest)
        if sub == "serve":
            return _main_serve_subcommand(rest)
        argv = rest  # generate: strip the token, fall through.

    args = parse_args(argv)

    # Generation knows exactly which component CSVs it just wrote. Always pass
    # that explicit allowlist to the combined writer so stale/foreign CSVs left
    # in --output-dir cannot be folded into this run's artifacts. The standalone
    # ``combine DIR`` subcommand keeps autodiscovery when its --components value
    # is the default "all". For generation's own default "all", keep the
    # discover_components-compatible sorted order for byte-parity with a later
    # ``combine DIR`` over a clean generated directory.
    if args.components == set(COMPONENTS.keys()):
        combine_components = sorted(COMPONENTS)
    else:
        combine_components = [name for name in COMPONENTS if name in args.components]

    total_seconds = SECONDS_PER_DAY * args.duration_days
    args.output_dir.mkdir(exist_ok=True, parents=True)
    _pre_clean_output_dir(
        args.output_dir,
        args.emit_selection,
        args.components,
        args.combine,
    )
    ctx = RunContext(rng=np.random.RandomState(args.seed))
    # Seed the per-run instance map. Phase 1 default: one anonymous Instance()
    # per component → byte-identical output. Phase 2: --instances-per-component
    # N > 1 fans every component out to N named instances (id=i0..iN-1,
    # pod=pod-0..pod-N-1); all other dimension fields remain None in v1.
    if args.instance_config is not None:
        # Phase 3: --instance-config populates the per-component map from file;
        # missing components fall back to the module-level INSTANCES registry
        # (default [Instance()] per component).
        try:
            config_map = _load_instance_config(args.instance_config)
        except ValueError as exc:
            sys.exit(str(exc))
        ctx.instances = {
            name: (
                config_map[name]
                if name in config_map
                else list(INSTANCES[name])
            )
            for name in COMPONENTS
        }
    elif args.instances_per_component == 1:
        ctx.instances = {name: list(INSTANCES[name]) for name in COMPONENTS}
    else:
        n = args.instances_per_component
        fan_out = [Instance(id=f"i{k}", pod=f"pod-{k}") for k in range(n)]
        ctx.instances = {name: list(fan_out) for name in COMPONENTS}

    # Build component_anomalies and cascading_anomalies entirely from the
    # SCENARIOS registry. _resolve_scenarios() applies the --scenarios /
    # --exclude-scenarios / --signal-level / --duration-days / --components
    # gates; _apply_scenarios() walks the resolved set in declaration order
    # and tail-appends each scenario's primaries and cascades.
    component_anomalies = {name: [] for name in COMPONENTS}
    active_scenarios = _resolve_scenarios(args)
    _apply_scenarios(component_anomalies, ctx.cascading_anomalies, active_scenarios)

    effective_specs = _resolve_effective_specs(args.metrics_per_component)
    _filter_anomalies_for_emitted_metrics(
        component_anomalies, ctx.cascading_anomalies, effective_specs
    )

    _apply_signal_level_and_count(
        component_anomalies,
        ctx.cascading_anomalies,
        signal_level=args.signal_level,
        selected_components=args.components,
        anomaly_count=args.anomaly_count,
        seed=args.seed,
        total_seconds=total_seconds,
        interval_seconds=args.interval_seconds,
    )

    ts_array, ts_strings = _build_timestamp_arrays(total_seconds, args.interval_seconds)
    n_rows = int(total_seconds // args.interval_seconds)

    # Topology phase 2 / phase 6 flag day: we walk
    # ``args.components`` in topological order (roots first) and stash
    # each generated component's load-metric columns so downstream
    # components can reshape their baseline via
    # ``_compose_topology_coupled_specs`` and layer saturation feedback
    # via ``_compose_topology_saturation_specs``. (The deprecated
    # ``--topology-mode independent`` no-topology contrast alias was
    # removed at the phase-9 flag day; realistic is the only mode.)
    active = set(args.components)
    generation_order = [
        name for name in _topology_generation_order(active)
        if name in effective_specs
    ]
    upstream_arrays: dict[str, dict[str, np.ndarray]] = {}
    # phase 8: parallel per-instance capture. Populated by
    # ``generate_component`` whenever ``--instances-per-component
    # N>1`` (or a non-default ``--instance-config``) makes the
    # component dim-aware. Consumed by
    # ``_compute_topology_arrays_per_instance`` so each downstream
    # instance gets a "matching instance set" view of its upstream
    # (see CLAUDE.md § Per-instance topology).
    upstream_arrays_by_instance: dict[str, list[dict[str, np.ndarray]]] = {}

    for name in generation_order:
        specs = effective_specs[name]
        coupling_per_instance = None
        saturation_per_instance = None
        instances_for_component = ctx.instances[name]
        n_inst_local = len(instances_for_component)
        is_anonymous_local = _is_anonymous_instance_list(instances_for_component)

        # Realistic is the only topology mode (phase-9 flag day
        # removed the independent contrast alias).
        if n_inst_local > 1 or not is_anonymous_local:
            # phase 8 — per-instance dispatch. Skip the
            # spec-modifying composers; compute per-instance
            # arrays directly. ``_compute_topology_arrays_per_instance``
            # shares the ``_TOPOLOGY_COUPLE_NOISE_STD`` draw across
            # instances so symmetric upstream produces byte-identical
            # output to the shared lambda-baked path used by the
            # N=1 anonymous branch below. ``generate_component``
            # re-derives divergence from the returned arrays directly
            # so the helper does not need to return a hint.
            (
                coupling_per_instance,
                saturation_per_instance,
            ) = _compute_topology_arrays_per_instance(
                name, specs, upstream_arrays,
                upstream_arrays_by_instance,
                instances_for_component, ctx.rng, n_rows,
            )
        else:
            # N=1 anonymous — today's shared lambda-baked path.
            # Byte-parity contract: the default
            # ``--instances-per-component 1`` keeps this branch.
            specs = _compose_topology_coupled_specs(
                name, specs, upstream_arrays, ctx.rng, n_rows
            )
            # Phase 4: saturation feedback. Layers logistic-shaped
            # latency multipliers and error offsets on top of the coupled
            # baseline so downstream latency/error metrics respond to
            # upstream load. Composes on top of any existing multiplier /
            # additive (e.g. ``_daily_sine``) so seasonal patterns survive.
            specs = _compose_topology_saturation_specs(
                name, specs, upstream_arrays, n_rows
            )
        generate_component(name, specs, component_anomalies[name],
                           base_dir=args.output_dir,
                           total_seconds=total_seconds,
                           drop_rate=args.drop_rate,
                           interval=args.interval_seconds,
                           ts_array=ts_array,
                           ts_strings=ts_strings,
                           emit_metrics="metrics" in args.emit_selection,
                           dst_inject_day=args.inject_dst_artifact_day,
                           ctx=ctx,
                           instances=ctx.instances[name],
                           topology_capture=upstream_arrays,
                           topology_capture_by_instance=(
                               upstream_arrays_by_instance if (
                                   n_inst_local > 1 or not is_anonymous_local
                               ) else None
                           ),
                           coupling_arrays_per_instance=coupling_per_instance,
                           saturation_arrays_per_instance=saturation_per_instance,
                           apply_dtype_int_cast=True)

    filtered_anomalies = [a for a in ctx.anomalies if a["component"] in args.components]

    # Enrich each manifest entry with ``event_id`` and ``parent_event_id`` before
    # sorting. ``event_id`` is a pure function of the four required fields
    # (timestamp, component, metric, description) — sort order does not affect
    # it. ``parent_event_id`` is computed in original (insertion) order so that
    # for each scenario the first non-cascade entry observed (which reflects
    # the COMPONENTS iteration order × per-component row_idx ordering) becomes
    # the canonical parent for every cascade row of the same scenario.
    scenario_first_primary_event_id: dict[str, str] = {}
    for entry in filtered_anomalies:
        entry["event_id"] = _anomaly_event_id(entry)
        scenario_id = entry.get("scenario_id", "")
        is_cascade = entry.get("is_cascade") == "true"
        if scenario_id and not is_cascade:
            scenario_first_primary_event_id.setdefault(scenario_id, entry["event_id"])
    for entry in filtered_anomalies:
        is_cascade = entry.get("is_cascade") == "true"
        scenario_id = entry.get("scenario_id", "")
        if is_cascade and scenario_id:
            # Orphan cascades (no surviving primary for the scenario, e.g. all
            # primaries dropped by --drop-rate) leave parent_event_id empty.
            entry["parent_event_id"] = scenario_first_primary_event_id.get(scenario_id, "")
        else:
            entry["parent_event_id"] = ""

    # Sort chronologically by ``(span_start, component, metric)`` so the manifest
    # is incident-friendly and the correlated reporting artifacts emit in the
    # same order (test_reporting_artifacts_align_with_manifest pins the index
    # alignment between anomalies.csv, metric_report.log, and metric_traces.jsonl).
    filtered_anomalies.sort(key=lambda a: (a["span_start"], a["component"], a["metric"]))

    manifest_fieldnames = [
        "timestamp", "component", "metric", "description",
        "scenario_id", "severity", "is_cascade",
        "event_id", "parent_event_id",
        "span_start", "span_end", "shape",
    ]

    if "metrics" in args.emit_selection:
        with open(args.output_dir / "anomalies.csv", "w", encoding="utf-8", newline="") as f:
            # ``extrasaction="ignore"`` is a defensive guard so any future
            # ``_``-prefixed private keys on entry dicts cannot leak into the CSV.
            writer = csv.DictWriter(
                f, fieldnames=manifest_fieldnames, extrasaction="ignore",
            )
            writer.writeheader()
            for a in filtered_anomalies:
                writer.writerow(a)

    if {"logs", "traces"} & args.emit_selection:
        write_reporting_artifacts(
            args.output_dir,
            filtered_anomalies,
            emit_logs="logs" in args.emit_selection,
            emit_traces="traces" in args.emit_selection,
        )

    gauge_rows_written = 0
    if "gauges" in args.emit_selection:
        # Long-form file peer of the OTEL gauge stream. Derived from the
        # per-component CSVs just written above (guaranteed present because
        # the parse_args gate requires "metrics" alongside "gauges"). The
        # sorted-components iterator order makes equal-timestamp ties
        # deterministic regardless of dict iteration order.
        gauge_csv_paths = {
            c: args.output_dir / f"{c}.csv" for c in sorted(args.components)
        }
        gauge_rows_written = write_gauges_csv(
            gauge_csv_paths, args.output_dir / "gauges.csv"
        )

    if "schema" in args.emit_selection:
        # Schema doc reflects exactly what this run wrote so the validator can
        # cross-check the directory after the fact. Built from the same emit
        # selection + components + combine flag the pre-clean step and the
        # end-of-run summary already consume, so the three views stay in sync.
        schema_components_in_order = [
            c for c in COMPONENTS if c in args.components
        ]
        emitted_files = _collect_emitted_filenames(
            emit_selection=args.emit_selection,
            components=schema_components_in_order,
            combine=args.combine,
        )
        schema_metadata = {
            "seed": args.seed,
            "start": START.isoformat(),
            "duration_days": args.duration_days,
            "interval_seconds": args.interval_seconds,
            "total_seconds": total_seconds,
            "rows_per_component": n_rows,
            "drop_rate": args.drop_rate,
            "signal_level": args.signal_level,
            "metrics_per_component": args.metrics_per_component,
            "anomaly_count": args.anomaly_count,
            "scenarios": sorted(active_scenarios),
            "exclude_scenarios": sorted(args.exclude_scenarios),
            "components": schema_components_in_order,
            "inject_dst_artifact_day": args.inject_dst_artifact_day,
            "emit_selection": sorted(args.emit_selection),
            "combine": args.combine,
            # phase 7 (constant since the phase-9 flag day removed the
            # independent alias): the field is retained so the validator
            # can keep honoring documents produced under either historic
            # mode; this writer only ever emits "realistic" now. The
            # validator's Pearson coupling check only runs under
            # ``realistic`` because the historic ``independent`` mode
            # produced decoupled baselines by construction.
            "topology_mode": "realistic",
        }
        write_schema_json(
            args.output_dir / "schema.json",
            components=schema_components_in_order,
            effective_specs=effective_specs,
            metadata=schema_metadata,
            emitted_files=emitted_files,
            # phase 8: per-component ``dimensions`` block (axes +
            # cardinality) when the run is dim-aware (``--instances-per-
            # component N>1`` or a non-default ``--instance-config``).
            # Filtered to the active component set so a ``--components``
            # subset doesn't leak instance topology for components the
            # run didn't write.
            instances_by_component={
                c: ctx.instances[c] for c in schema_components_in_order
            },
        )

    streamed_events = 0
    endpoints = {
        "logs": args.otel_logs_endpoint,
        "metrics": args.otel_metrics_endpoint,
        "traces": args.otel_traces_endpoint,
    }
    # --otel-send is authoritative for the anomaly-signal stream too:
    # the gauge stream needs the metrics endpoint to exist, but a
    # selection like 'logs,gauges' must not leak the anomaly-count
    # metrics signal through it. None = legacy toggles, no filtering.
    signal_selection = getattr(args, "otel_signal_selection", None)
    if signal_selection is not None:
        signal_endpoints = {
            sig: (url if sig in signal_selection else None)
            for sig, url in endpoints.items()
        }
    else:
        signal_endpoints = endpoints
    otel_active = args.otel_enabled and any(endpoints.values())
    auth_headers = {}
    if otel_active:
        for signal in ["logs", "metrics", "traces"]:
            token = getattr(args, f"otel_{signal}_auth_token")
            if token:
                auth_headers[signal] = {"Authorization": f"{args.otel_stream_auth_scheme} {token}"}

    if otel_active and not args.otel_gauges_only and any(signal_endpoints.values()):
        streamed_events = stream_otel_signals(
            signal_endpoints,
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
        # Gauge stream normally appends after the anomaly-counter stream so both
        # passes share one log. In gauges-only mode (--otel-send gauges) there is no prior
        # signal pass, so the gauge stream starts a fresh log instead.
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
            append_activity_log=not args.otel_gauges_only,
        )

    if args.combine:
        # Freshly-generated, non-DST component CSVs are emitted in chronological
        # order, so the wide combine writer can skip its defensive monotonic
        # pre-scan for exactly the generated component allowlist. External
        # ``combine DIR`` invocations still take the conservative scan.
        assume_monotonic_wide_components = (
            set(combine_components)
            if args.inject_dst_artifact_day == 0
            else None
        )
        combine_logs(
            args.output_dir,
            components=combine_components,
            assume_monotonic_wide_components=assume_monotonic_wide_components,
        )

    written = []
    if "metrics" in args.emit_selection:
        written.append(f"{len(args.components)} component CSV(s)")
    for emit_type, files in _EMIT_ARTIFACT_FILES.items():
        if emit_type in args.emit_selection:
            written.extend(files)
    if args.combine:
        written.append(_COMBINE_OUTPUT_FILENAME)
    print(f"Done - {', '.join(written)} written to {args.output_dir}")
    print(f"   Duration: {args.duration_days} day(s) ({total_seconds:,} seconds)")
    print(f"   Interval: {args.interval_seconds}s ({n_rows:,} rows per component)")
    print(f"   Anomalies recorded: {len(filtered_anomalies)}")
    if "gauges" in args.emit_selection:
        print(f"   Gauge rows written: {gauge_rows_written:,} to gauges.csv")
    if otel_active:
        active = [f"{s} -> {u}" for s, u in endpoints.items() if u]
        if args.otel_gauges_only:
            print("   OTEL signal stream skipped (--otel-send gauges)")
        else:
            print(f"   OTEL signals streamed: {streamed_events} to {', '.join(active)}")
        if args.otel_emit_gauges:
            print(f"   OTEL gauge requests streamed: {gauge_requests_sent} to "
                  f"metrics -> {args.otel_metrics_endpoint}")
    elif any(endpoints.values()):
        print("   OTEL streaming disabled (pass --otel-send to stream to configured endpoints)")


if __name__ == "__main__":
    main()
