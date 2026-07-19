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

import contextlib
import csv
import datetime
import hashlib
import heapq
import json
import inspect
import math
import sys
import dataclasses
from dataclasses import dataclass, field
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
# Per-metric and instance models.
# ------------------------------------------------------------------
# MetricSpec / Instance moved to models_impl.py (decomposition step 9A).
# Re-imported here so tests, package facades, and the historic
# ``legacy.<name>`` surface stay unchanged.
from .models_impl import (
    Instance as Instance,
    MetricSpec as MetricSpec,
    _configure_models_runtime as _configure_models_runtime,
    _load_instance_config as _models_load_instance_config,
    _validate_instance_list as _validate_instance_list,
)



# _INSTANCE_DIMENSION_COLUMNS (the canonical long-form dimension column
# order) moved to csv_layout.py (decomposition step 3); re-imported here
# so the Instance model, generation, schema, combine, gauges, OTEL, and
# server_mcp (via state.legacy) consumers keep the historic binding.
from .csv_layout import (
    _INSTANCE_DIMENSION_COLUMNS as _INSTANCE_DIMENSION_COLUMNS,
    _INSTANCE_DIMENSION_FIELDS as _INSTANCE_DIMENSION_FIELDS,
    _is_anonymous_instance_list as _is_anonymous_instance_list,
)


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
# Atomic artifact publication
# ------------------------------------------------------------------
# Atomic artifact publication moved verbatim to artifacts.py
# (decomposition step 4, landed with step 3 because gauges_impl.py
# depends on it). Re-imported here so every writer below plus tests keep
# the historic ``legacy.<name>`` surface; new code should import from
# anomaly_metric_creator.artifacts directly.
from .artifacts import (
    _ATOMIC_TMP_SUFFIX as _ATOMIC_TMP_SUFFIX,
    _atomic_artifact_open as _atomic_artifact_open,
    _atomic_write_text as _atomic_write_text,
)


# ------------------------------------------------------------------
# Core generator
# ------------------------------------------------------------------
def generate_component(component_name, specs: list[MetricSpec], anomaly_specs,
                       *, base_dir, total_seconds, drop_rate,
                       ctx: "RunContext",
                       interval=1.0,
                       ts_array=None, ts_strings=None, emit_metrics=True,
                       dst_inject_day=0, start_time: datetime.datetime = START,
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
        ts_array, ts_strings = _build_timestamp_arrays(
            total_seconds, interval, start_time=start_time
        )
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
        ts_py = start_time + datetime.timedelta(seconds=float(row_idx * interval))
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

        with _atomic_artifact_open(file_path) as f:
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
                    start_time=start_time,
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
                        start_time=start_time,
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
                          *, dim_prefix: str, dst_inject_day: int,
                          start_time: datetime.datetime = START) -> np.ndarray:
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
        rows = _splice_dst_artifact(rows, kept_ts, dst_inject_day, start_time)
    return rows


def _splice_dst_artifact(rows: np.ndarray, kept_ts: np.ndarray,
                         dst_day: int,
                         start_time: datetime.datetime = START) -> np.ndarray:
    """Duplicate the 02:00–02:59 hour on ``dst_day`` (1-based) inside ``rows``.

    ``rows`` is the formatted ``ts,v0,...,vk`` string array; ``kept_ts`` is the
    matching ``YYYY-MM-DD HH:MM:SS`` timestamps used to locate the window. The
    returned array has 3,600 / interval extra rows for the targeted day. The
    duplicate hour reuses the same timestamp prefix, so the resulting CSV has
    non-monotonic timestamps — the realistic fall-DST quirk.
    """
    day_date = (start_time + datetime.timedelta(days=dst_day - 1)).strftime("%Y-%m-%d")
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


def _build_timestamp_arrays(
    total_seconds: int,
    interval: float = 1.0,
    *,
    start_time: datetime.datetime = START,
):
    """Pre-compute the shared per-run timestamp arrays (numpy + formatted str).

    Built once per run and reused across every component — they're identical
    by construction, so re-computing them per component is pure waste.
    Row ``i`` is at ``start_time + i * interval`` seconds; row count is
    ``floor(total_seconds / interval)``. Strings are rendered at second
    precision when ``interval >= 1.0`` and at millisecond precision otherwise
    so adjacent sub-second rows never share a timestamp string.
    """
    n_rows = int(total_seconds // interval)
    step_us = int(round(interval * 1_000_000))
    ts_array = (
        np.datetime64(start_time) + np.arange(n_rows) * np.timedelta64(step_us, "us")
    )
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
# ------------------------------------------------------------------
# Component and instance catalogs.
# ------------------------------------------------------------------
# COMPONENTS / INSTANCES and their catalog metadata validators moved to
# catalog.py (decomposition step 9A). legacy.py keeps the public binding and
# configures live callbacks so monkeypatches against legacy.COMPONENTS or
# legacy.INSTANCES remain visible to moved validation/config-reader helpers.
from .catalog import (
    COMPONENTS as COMPONENTS,
    DEFAULT_METRICS_PER_COMPONENT as DEFAULT_METRICS_PER_COMPONENT,
    INSTANCES as INSTANCES,
    MAX_INSTANCES_PER_COMPONENT as MAX_INSTANCES_PER_COMPONENT,
    MAX_METRICS_PER_COMPONENT as MAX_METRICS_PER_COMPONENT,
    _configure_catalog_runtime as _configure_catalog_runtime,
    _validate_instances_registry as _catalog_validate_instances_registry,
    _validate_metric_spec_schema_metadata as _catalog_validate_metric_spec_schema_metadata,
)


def _catalog_runtime_components():
    return COMPONENTS


def _catalog_runtime_instances():
    return INSTANCES


def _catalog_runtime_default_metrics_per_component():
    return DEFAULT_METRICS_PER_COMPONENT


def _catalog_runtime_max_instances_per_component():
    return MAX_INSTANCES_PER_COMPONENT


_configure_models_runtime(
    get_components=_catalog_runtime_components,
    get_max_instances_per_component=_catalog_runtime_max_instances_per_component,
    runtime_key=__name__,
)
_configure_catalog_runtime(
    get_components=_catalog_runtime_components,
    get_instances=_catalog_runtime_instances,
    get_default_metrics_per_component=_catalog_runtime_default_metrics_per_component,
    runtime_key=__name__,
)


def _validate_metric_spec_schema_metadata() -> None:
    return _catalog_validate_metric_spec_schema_metadata(runtime_key=__name__)


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
            # sum_w > 0 in the shipped graph (constant weights are positive),
            # but a monkeypatched/programmatic TOPOLOGY whose active constant
            # weights sum to 0 would divide by zero here. Zero total weight
            # means no constant coupling, so leave constant_contrib at zeros
            # (07-02-verify-topology-divzero).
            if sum_w > 0:
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
    upstream_arr = np.asarray(upstream_load, dtype=np.float64)
    # Generated captures are finite by construction (Kahn ordering feeds this
    # only post-round load columns), so this never fires on real output; it
    # fails loud for direct/programmatic callers rather than letting a
    # NaN/inf propagate silently through the logistic into a metric cell
    # (07-02-verify-topology-divzero). np.maximum/np.minimum do not filter
    # NaN, so the utilization clamp below cannot catch it.
    if not np.all(np.isfinite(upstream_arr)):
        raise ValueError(
            "_apply_saturation: upstream_load must be finite; "
            "got NaN/inf values"
        )
    utilization = np.maximum(upstream_arr, 0.0) / float(sat.midpoint)
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
                # Guard sum_w == 0 as in the aggregate path above
                # (07-02-verify-topology-divzero): zero total constant weight
                # means no coupling contribution, not a divide-by-zero.
                if sum_w > 0:
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


# Instance registry validation moved to catalog.py; _validate_instance_list and
# _load_instance_config live in models_impl.py. The wrappers keep legacy's
# patch-visible runtime view and preserve the old import-time call position.
def _validate_instances_registry() -> None:
    return _catalog_validate_instances_registry(runtime_key=__name__)


_validate_instances_registry()


def _load_instance_config(path: "Path") -> dict[str, list["Instance"]]:
    return _models_load_instance_config(path, runtime_key=__name__)




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


# CLI parsing and subcommand helpers moved to cli_args.py (decomposition
# step 8). Re-imported here so tests, server state, and the historic
# ``legacy.<name>`` surface stay unchanged; new code should import from
# anomaly_metric_creator.cli_args directly.
from .cli_args import (
    _ADVANCED_DESTS as _ADVANCED_DESTS,
    _SUBCOMMANDS as _SUBCOMMANDS,
    _configure_cli_runtime as _configure_cli_runtime,
    _flag_in_argv as _flag_in_argv,
    _main_combine_subcommand as _cli_main_combine_subcommand,
    _main_serve_subcommand as _cli_main_serve_subcommand,
    _main_trace_bundle_subcommand as _cli_main_trace_bundle_subcommand,
    _main_validate_subcommand as _cli_main_validate_subcommand,
    _parse_components_value as _cli_parse_components_value,
    _parse_start_time_arg as _parse_start_time_arg,
    _reconcile_cli_surface as _reconcile_cli_surface,
    parse_args as _cli_parse_args,
)
_CLI_RUNTIME_KEY = __name__
_configure_cli_runtime(
    get_components=lambda: COMPONENTS,
    get_scenarios=lambda: SCENARIOS,
    get_default_metrics_per_component=lambda: DEFAULT_METRICS_PER_COMPONENT,
    get_legacy_module=lambda: sys.modules[__name__],
    constants={
        "DEFAULT_DROP_RATE": DEFAULT_DROP_RATE,
        "DEFAULT_DURATION_DAYS": DEFAULT_DURATION_DAYS,
        "DEFAULT_INTERVAL_SECONDS": DEFAULT_INTERVAL_SECONDS,
        "DEFAULT_OUTPUT_DIR": DEFAULT_OUTPUT_DIR,
        "DEFAULT_OTEL_STREAM_AUTH_SCHEME": DEFAULT_OTEL_STREAM_AUTH_SCHEME,
        "DEFAULT_ROW_COUNT": DEFAULT_ROW_COUNT,
        "DEFAULT_SEED": DEFAULT_SEED,
        "DEFAULT_SIGNAL_LEVEL": DEFAULT_SIGNAL_LEVEL,
        "MAX_INSTANCES_PER_COMPONENT": MAX_INSTANCES_PER_COMPONENT,
        "MAX_METRICS_PER_COMPONENT": MAX_METRICS_PER_COMPONENT,
        "PREFLIGHT_CELL_CAP": PREFLIGHT_CELL_CAP,
        "SECONDS_PER_DAY": SECONDS_PER_DAY,
        "SIGNAL_LEVELS": SIGNAL_LEVELS,
        "START": START,
    },
    runtime_key=_CLI_RUNTIME_KEY,
)


def _parse_components_value(error, raw: str) -> set[str]:
    return _cli_parse_components_value(error, raw, runtime_key=_CLI_RUNTIME_KEY)


def parse_args(argv=None):
    return _cli_parse_args(argv, runtime_key=_CLI_RUNTIME_KEY)


def _main_combine_subcommand(argv):
    return _cli_main_combine_subcommand(argv, runtime_key=_CLI_RUNTIME_KEY)


def _main_validate_subcommand(argv):
    return _cli_main_validate_subcommand(argv, runtime_key=_CLI_RUNTIME_KEY)


def _main_serve_subcommand(argv):
    return _cli_main_serve_subcommand(argv, runtime_key=_CLI_RUNTIME_KEY)


def _main_trace_bundle_subcommand(argv):
    return _cli_main_trace_bundle_subcommand(argv, runtime_key=_CLI_RUNTIME_KEY)


# ------------------------------------------------------------------
# Combine step: join per-component CSVs into a single unified CSV.
# One row per timestamp; columns prefixed with the component name.
# ------------------------------------------------------------------
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


# The combine writers, autodiscovery, monotonic pre-scan, and the
# _NON_COMPONENT_FILES / _COMBINE_OUTPUT_FILENAME constants moved verbatim
# to combine_impl.py (decomposition step 5). Re-imported here so the
# combine subcommand, main()'s combined-artifact pass, the pre-clean /
# summary uses of _COMBINE_OUTPUT_FILENAME, and tests keep the historic
# ``legacy.<name>`` surface. (_EMIT_ARTIFACT_FILES stays — it is a core
# emit registry, not combine-specific.) New code should import from
# anomaly_metric_creator.combine_impl directly.
from .combine_impl import (
    _COMBINE_OUTPUT_FILENAME as _COMBINE_OUTPUT_FILENAME,
    _NON_COMPONENT_FILES as _NON_COMPONENT_FILES,
    _wide_component_rows_are_monotonic as _wide_component_rows_are_monotonic,  # noqa: F401
    _write_combined_long_form as _write_combined_long_form,  # noqa: F401
    _write_combined_wide_materialized as _write_combined_wide_materialized,  # noqa: F401
    combine_logs as combine_logs,
    combine_logs_unified as combine_logs_unified,
    discover_components as discover_components,
)


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
            stack.enter_context(_atomic_artifact_open(log_path))
            if emit_logs
            else None
        )
        trace_f = (
            stack.enter_context(_atomic_artifact_open(trace_path))
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


# Timestamp parsing / unix-nano helpers moved verbatim to timeutil.py
# (decomposition step 2). Re-imported here so the historic
# ``legacy.<name>`` surface (tests, state.legacy lookups, the merge
# writers below) is unchanged; new code should import from
# anomaly_metric_creator.timeutil directly.
from .timeutil import (
    _UNIX_EPOCH_UTC as _UNIX_EPOCH_UTC,  # noqa: F401
    _dt_to_unix_nanos as _dt_to_unix_nanos,
    _parse_csv_timestamp as _parse_csv_timestamp,
    _to_unix_nanos as _to_unix_nanos,
)

# _anomaly_event_id and the eight _build_otlp_* payload builders moved
# verbatim to otlp.py (decomposition step 2). Re-imported here so
# write_reporting_artifacts, the OTEL streamers, and tests keep the historic
# ``legacy.<name>`` binding; new code should import from
# anomaly_metric_creator.otlp directly.
from .otlp import (
    _anomaly_event_id as _anomaly_event_id,
    _build_otlp_gauge_payload as _build_otlp_gauge_payload,
    _build_otlp_gauge_protobuf as _build_otlp_gauge_protobuf,
    _build_otlp_log_payload as _build_otlp_log_payload,
    _build_otlp_log_protobuf as _build_otlp_log_protobuf,
    _build_otlp_metric_payload as _build_otlp_metric_payload,
    _build_otlp_metric_protobuf as _build_otlp_metric_protobuf,
    _build_otlp_trace_payload as _build_otlp_trace_payload,
    _build_otlp_trace_protobuf as _build_otlp_trace_protobuf,
)

# Header redaction moved verbatim to redaction.py (decomposition step 1),
# including the canonical lowercased allowlist prose. Re-imported here
# so the historic ``legacy.<name>`` surface — the shim, facades, tests, and
# ``state.legacy`` attribute lookups — is unchanged. New code should import
# from ``anomaly_metric_creator.redaction`` directly.
from .redaction import (
    _SAFE_RESPONSE_HEADER_NAMES as _SAFE_RESPONSE_HEADER_NAMES,  # noqa: F401
    _SCHEMED_SENSITIVE_HEADERS as _SCHEMED_SENSITIVE_HEADERS,  # noqa: F401
    _SENSITIVE_HEADER_NAMES as _SENSITIVE_HEADER_NAMES,  # noqa: F401
    _mask_sensitive_value as _mask_sensitive_value,  # noqa: F401
    _masked_headers as _masked_headers,
    _redact_sensitive_headers as _redact_sensitive_headers,
)

# OTEL transport streamers and activity-log helpers moved verbatim to
# otel_stream.py (decomposition step 7). Re-imported here so main(), tests,
# state.legacy lookups, and the historic ``legacy.<name>`` surface are
# unchanged; new code should import from anomaly_metric_creator.otel_stream
# directly.
from .otel_stream import (
    _http_error_activity_fields as _http_error_activity_fields,
    _verbose_body_repr as _verbose_body_repr,
    _write_activity as _write_activity,
    stream_otel_gauges as stream_otel_gauges,
    stream_otel_signals as stream_otel_signals,
)

# The shared per-component CSV primitives moved verbatim to csv_layout.py
# (decomposition step 3). Re-imported here so the combine long-form writer,
# the OTEL gauge streamer, tests, and state.legacy lookups keep the
# historic ``legacy.<name>`` surface; new code should import from
# anomaly_metric_creator.csv_layout directly.
from .csv_layout import (
    _LONG_FORM_FD_MARGIN as _LONG_FORM_FD_MARGIN,  # noqa: F401
    _classify_component_csv_header as _classify_component_csv_header,
    _ensure_long_form_fd_capacity as _ensure_long_form_fd_capacity,
    _iter_component_instance_rows as _iter_component_instance_rows,
    _iter_component_rows as _iter_component_rows,
    _scan_component_csv_headers as _scan_component_csv_headers,
    _scan_instance_block_layout as _scan_instance_block_layout,
)
# write_gauges_csv moved verbatim to gauges_impl.py (decomposition step 3);
# re-imported here so main()'s gauge pass and tests keep the historic
# ``legacy.write_gauges_csv`` binding.
from .gauges_impl import write_gauges_csv as write_gauges_csv
# Schema writer and output validator helpers moved to schema_impl.py and
# validate_impl.py (decomposition step 6). Re-imported here so the historic
# ``legacy.<name>`` surface, package facades, tests, and state.legacy lookups
# keep working unchanged.
from .schema_impl import (
    SCHEMA_DOCUMENT_VERSION as SCHEMA_DOCUMENT_VERSION,
    _configure_schema_runtime as _configure_schema_runtime,
    _metric_spec_to_schema_entry as _metric_spec_to_schema_entry,
    _saturation_params_to_schema_entry as _saturation_params_to_schema_entry,
    _edge_to_schema_entry as _edge_to_schema_entry,
    _component_dimensions_schema_entry as _component_dimensions_schema_entry,
    _serialize_topology as _serialize_topology,
    write_schema_json as write_schema_json,
)
_configure_schema_runtime(get_topology=lambda: TOPOLOGY)

from .validate_cells import (
    _VALIDATE_DERIVATION_TOLERANCE as _VALIDATE_DERIVATION_TOLERANCE,
    _VALIDATE_INT_TOLERANCE as _VALIDATE_INT_TOLERANCE,
    _recompute_cacheservice as _recompute_cacheservice,
    _RECOMPUTERS as _RECOMPUTERS,
    _schema_has_any_dimensions as _schema_has_any_dimensions,
)
from .validate_impl import (
    _configure_validate_runtime as _configure_validate_runtime,
    Violation as Violation,
    _json_path as _json_path,
    _schema_shape_error as _schema_shape_error,
    _require_schema_mapping as _require_schema_mapping,
    _require_schema_list as _require_schema_list,
    _require_schema_string as _require_schema_string,
    _require_schema_number as _require_schema_number,
    _validate_string_list_schema_shape as _validate_string_list_schema_shape,
    _validate_schema_document_shape as _validate_schema_document_shape,
    _load_schema_document as _load_schema_document,
    _validate_required_files_present as _validate_required_files_present,
    _validate_no_unknown_files as _validate_no_unknown_files,
    _validate_anomalies_sorted as _validate_anomalies_sorted,
    _validate_component_row_count as _validate_component_row_count,
    _validate_component_timestamp_coverage as _validate_component_timestamp_coverage,
    _validate_component_cells as _validate_component_cells,
    _validate_component_derivations as _validate_component_derivations,
    _filter_windows_for_pair as _filter_windows_for_pair,
    _validate_topology_coupling as _validate_topology_coupling,
    _validate_topology_coupling_per_instance as _validate_topology_coupling_per_instance,
    _resolve_edge_correlation_threshold as _resolve_edge_correlation_threshold,
    _validate_long_form_dimensions as _validate_long_form_dimensions,
    validate_output as validate_output,
)
from .validate_topology import (
    _TOPOLOGY_DEFAULT_CORRELATION_THRESHOLD as _TOPOLOGY_DEFAULT_CORRELATION_THRESHOLD,
    _TOPOLOGY_CORRELATION_EXCLUSION_PAD_SECONDS as _TOPOLOGY_CORRELATION_EXCLUSION_PAD_SECONDS,
    _TOPOLOGY_MIN_ALIGNED_ROWS as _TOPOLOGY_MIN_ALIGNED_ROWS,
    _read_component_metric_column as _read_component_metric_column,
    _read_anomaly_exclusion_windows as _read_anomaly_exclusion_windows,
    _compute_anomaly_keep_mask as _compute_anomaly_keep_mask,
)
from .validate_topology_instances import (
    _read_component_metric_column_per_instance as _read_component_metric_column_per_instance,
)
_configure_validate_runtime(
    get_topology=lambda: TOPOLOGY,
    get_topology_load_metrics=lambda: _TOPOLOGY_LOAD_METRICS,
)

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


def _known_artifact_filenames():
    """Every artifact filename this script can write into --output-dir.

    Derived from the same registries the pre-clean and end-of-run summary
    consume (`COMPONENTS`, `_EMIT_ARTIFACT_FILES`, `_COMBINE_OUTPUT_FILENAME`)
    so the temp-sibling sweep cannot drift from the real write slots.
    """
    filenames = [f"{component}.csv" for component in COMPONENTS]
    for files in _EMIT_ARTIFACT_FILES.values():
        filenames.extend(files)
    filenames.append(_COMBINE_OUTPUT_FILENAME)
    return filenames


def _pre_clean_output_dir(output_dir, emit_selection, selected_components, combine):
    """Remove stale artifacts from a prior run that this run will not regenerate.

    Called right after --output-dir is created. Idempotent on missing files.
    Files unknown to this script (e.g. user notes, the synthetic-extra-component
    CSV the test fixture relies on for combine autodiscovery) are left alone.
    Not called by the ``combine`` subcommand; that path reads existing
    per-component CSVs as inputs.

    Files this run *will* regenerate are intentionally left in place: every
    generated-artifact writer publishes through ``_atomic_artifact_open``
    (temp sibling + ``os.replace``), so the previous run's content stays
    fully readable until the instant the new content replaces it. Deleting
    here would reopen the mid-delete visibility gap the atomic writers close.
    Stale ``*.tmp`` siblings from a crashed prior run are swept for every
    registry-known artifact slot regardless of the emit selection — a temp
    is never a valid artifact.
    """
    for filename in _known_artifact_filenames():
        (output_dir / (filename + _ATOMIC_TMP_SUFFIX)).unlink(missing_ok=True)
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


def main(argv=None):
    # Subcommand dispatch: 'generate' (the default when the first token is
    # not a subcommand, preserving every historic invocation), 'combine',
    # 'validate', 'serve', and 'trace-bundle'. Handled before argparse so
    # the flat generate parser never sees the subcommand token.
    argv = list(sys.argv[1:]) if argv is None else list(argv)
    if argv and argv[0] in _SUBCOMMANDS:
        sub, rest = argv[0], argv[1:]
        if sub == "combine":
            return _main_combine_subcommand(rest)
        if sub == "validate":
            return _main_validate_subcommand(rest)
        if sub == "serve":
            return _main_serve_subcommand(rest)
        if sub == "trace-bundle":
            return _main_trace_bundle_subcommand(rest)
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

    ts_array, ts_strings = _build_timestamp_arrays(
        total_seconds,
        args.interval_seconds,
        start_time=args.start_time,
    )
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
                           start_time=args.start_time,
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
        with _atomic_artifact_open(args.output_dir / "anomalies.csv") as f:
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
            "start": args.start_time.isoformat(),
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
        gauge_auth = auth_headers.get("metrics")
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
            # max_retries uses the shared _OTEL_DEFAULT_MAX_RETRIES default.
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
