"""Scenario models and deterministic scenario-spec builders."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import cast

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

from .anomaly_dispatch import _span_fraction
from .runtime_defaults import SECONDS_PER_DAY

DEFAULT_ROW_COUNT = 50_000
DEFAULT_INTERVAL_SECONDS = 60.0
DEFAULT_SEVERITY = "medium"

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
    stress_values = cast("np.ndarray", schedule["stress"])
    incident_values = cast("np.ndarray", schedule["incident_intensity"])
    failure_minutes = cast("frozenset[int]", schedule["failure"])

    def minute_idx(t_within: float) -> int:
        return int(math.floor((t_within + 1e-9) / DEFAULT_INTERVAL_SECONDS))

    def stress_for(minute: int) -> float:
        return float(stress_values[minute])

    def incident_for(minute: int) -> float:
        return float(incident_values[minute])

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

    for minute in sorted(failure_minutes):
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
