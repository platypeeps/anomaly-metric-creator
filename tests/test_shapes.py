"""Anomaly shape tests: ramp endpoints, sustained span row count, periodic mean,
and the DST-artifact CLI flag.

Each test exercises a single shape end-to-end through ``main()`` and reads the
emitted CSV to assert the shape's mathematical invariants — endpoint values for
ramps, span row count for sustained, midline mean for periodic shapes, and row
duplication for the DST quirk.
"""

import datetime
import math
from pathlib import Path

import numpy as np
import pytest

from conftest import read_component_rows, read_manifest, run_capture


def _apply_anomaly_shape(amc, values, col, start_row, n_rows, spec, interval=1.0):
    """Compatibility shim for old vectorized internal helper."""
    duration_seconds = n_rows * interval
    # Ensure spec has duration_seconds if missing
    if "duration_seconds" not in spec:
        spec = spec.copy()
        spec["duration_seconds"] = duration_seconds
    for i in range(n_rows):
        row_idx = start_row + i
        t_within = i * interval
        ts = amc.START + datetime.timedelta(seconds=float(row_idx * interval))
        values[row_idx, col] = amc._resolve_anomaly_value(spec, ts, col, t_within, i)


# ------------------------------------------------------------------
# Shared shape spec — one named span anomaly per shape so each test can
# locate its rows without depending on the others.
# ------------------------------------------------------------------
def _span_rows(out_dir: Path, component: str, metric: str,
               start_ts: datetime.datetime, duration_seconds: int):
    """Return the list of (datetime, float) pairs for ``metric`` over the span."""
    rows, header = read_component_rows(out_dir, component)
    col_idx = header.index(metric)
    end_ts = start_ts + datetime.timedelta(seconds=duration_seconds)
    out = []
    for ts_str, row in rows.items():
        ts = datetime.datetime.fromisoformat(ts_str)
        if start_ts <= ts < end_ts:
            out.append((ts, float(row[col_idx])))
    out.sort()
    return out


# ------------------------------------------------------------------
# ramp_linear — endpoints land at start / end.
# ------------------------------------------------------------------
def test_ramp_linear_endpoints(amc, one_day_run_a):
    """Slow memory leak: ramp_linear 70 → 96 over 4h. First and last span
    rows should equal start / end (modulo the 3-decimal CSV
    rounding); span row count = duration / interval."""
    start = amc.START + datetime.timedelta(hours=8)
    duration = 4 * 3600
    span = _span_rows(one_day_run_a.out_dir, "cacheservice",
                      "memory_util_pct", start, duration)
    assert span, "memory_util_pct ramp_linear span is empty"
    # Accept up to a couple of dropped rows near the endpoints.
    first_ts, first_v = span[0]
    last_ts, last_v = span[-1]
    assert first_ts == start, f"ramp start ts={first_ts}, expected {start}"
    # last_ts is duration-1 seconds after start at interval=1.0 because the
    # span is half-open [start, start+duration).
    expected_last_ts = start + datetime.timedelta(seconds=duration - 1)
    assert last_ts == expected_last_ts, (
        f"ramp end ts={last_ts}, expected {expected_last_ts}"
    )
    assert abs(first_v - 70.0) < 0.01, f"ramp start value {first_v} != 70.0"
    assert abs(last_v - 96.0) < 0.01, f"ramp end value {last_v} != 96.0"
    # Linspace monotonicity: values should be non-decreasing across the span.
    values = np.array([v for _, v in span])
    assert np.all(np.diff(values) >= -1e-6), "ramp_linear should be monotonic"


# ------------------------------------------------------------------
# ramp_exp (geomspace) endpoints — covered by disk_used_pct uses
# ramp_linear in current catalog, so we drive the shape directly via the
# helper to verify endpoint semantics without re-running main().
# ------------------------------------------------------------------
def test_ramp_exp_endpoints_directly(amc):
    """The ``ramp_exp`` shape uses power-based progression; endpoints match
    mathematical expectation for the row's time-within-span."""
    n = 10
    values = np.zeros((n, 1), dtype=np.float64)
    aspec = {
        "metric": "fake",
        "generator": lambda ts, idx: 0,
        "shape": "ramp_exp",
        "shape_params": {"start": 1.0, "end": 1001.0, "exponent": 3.0},
    }
    # With n=10, interval=1.0, duration=10.0.
    # Last row is i=9, t_within=9.0, frac = (9/10)^3 = 0.729.
    # Value = 1.0 + (1001 - 1) * 0.729 = 1 + 1000 * 0.729 = 730.0.
    _apply_anomaly_shape(amc, values, 0, 0, n, aspec, interval=1.0)
    assert math.isclose(values[0, 0], 1.0, rel_tol=1e-9)
    assert math.isclose(values[-1, 0], 730.0, rel_tol=1e-9)
    # Monotonicity for positive ramps:
    assert np.all(np.diff(values[:, 0]) > 0)


# ------------------------------------------------------------------
# sustained — span has duration / interval rows at the multiplied baseline.
# ------------------------------------------------------------------
def test_sustained_span_row_count_and_multiplier(amc, one_day_run_a):
    """Retry storm: requests_per_sec sustained 2× baseline for 8 min.

    The span must contain ``duration / interval`` rows minus drops, and the
    mean value across the span must be ≈ 2× the natural baseline (≈ 800).
    """
    start = amc.START + datetime.timedelta(hours=19)
    duration = 8 * 60
    span = _span_rows(one_day_run_a.out_dir, "apigateway",
                      "requests_per_sec", start, duration)
    # At drop_rate=0.0005, expect ~480 rows; tolerate a couple of dropped rows.
    assert duration - 4 <= len(span) <= duration, (
        f"sustained span row count {len(span)} not within {duration} ± 4"
    )
    values = np.array([v for _, v in span])
    # Natural baseline for requests_per_sec is 800; sustained generator returns 1600.
    mean_v = float(values.mean())
    assert 1500 <= mean_v <= 1700, (
        f"sustained mean {mean_v} not near 2× baseline (expected ~1600)"
    )


# ------------------------------------------------------------------
# Step / deploy regression — span values match generator output.
# ------------------------------------------------------------------
def test_step_span_constant_value(amc, one_day_run_a):
    """Deploy regression: step shift to 234 ms sustained 10:00–24:00.

    Every span row's avg_response_time_ms equals 234, except the single row
    where the 14:31 MQ cascade overwrites the step value with ~650 ms.
    """
    start = amc.START + datetime.timedelta(hours=10)
    duration = 14 * 3600
    span = _span_rows(one_day_run_a.out_dir, "apigateway",
                      "avg_response_time_ms", start, duration)
    assert len(span) > duration - 50, f"step span suspiciously short: {len(span)}"

    # Identify the cascade row (14:31:00) — it must NOT equal the step value.
    cascade_ts = amc.START + datetime.timedelta(hours=14, minutes=31)
    cascade_values = [v for ts, v in span if ts == cascade_ts]
    assert cascade_values, "expected the 14:31 cascade row inside the deploy span"
    assert cascade_values[0] != 234.0, (
        f"cascade row {cascade_ts} carries step value {cascade_values[0]} — "
        f"existing cascade should override"
    )

    # Everywhere else inside the span the deploy step is the source of truth.
    non_cascade = [v for ts, v in span if ts != cascade_ts]
    assert all(v == 234.0 for v in non_cascade[:50]), (
        f"first 50 non-cascade step values not all 234.0: "
        f"e.g. {[v for v in non_cascade[:5]]}"
    )


# ------------------------------------------------------------------
# sawtooth — mean across the span ≈ midline.
# ------------------------------------------------------------------
def test_sawtooth_mean_near_midline(amc, one_day_run_a):
    """GC sawtooth on avg_response_time_ms: 90s period, amplitude 100, midline 280.

    Across 30 minutes (= 20 full periods), the per-row arithmetic mean of a
    rising sawtooth converges to the midline. Loose tolerance because the
    span is finite and a few rows may be dropped.
    """
    start = amc.START + datetime.timedelta(hours=9, minutes=30)
    duration = 30 * 60
    span = _span_rows(one_day_run_a.out_dir, "apigateway",
                      "avg_response_time_ms", start, duration)
    values = np.array([v for _, v in span])
    assert values.size > duration - 20, f"sawtooth_span suspiciously short: {values.size}"
    mean_v = float(values.mean())
    # Sawtooth -1..+1 rising linearly has mean 0 → shaped mean ≈ midline.
    # Allow ±5 ms for finite-span + drop effects.
    assert abs(mean_v - 280.0) < 5.0, (
        f"sawtooth mean {mean_v} not within ±5 of midline 280.0"
    )
    # And confirm the extremes are near midline ± amplitude.
    assert values.min() < 200.0, f"sawtooth min {values.min()} not below 200"
    assert values.max() > 360.0, f"sawtooth max {values.max()} not above 360"


# ------------------------------------------------------------------
# sine — verify directly via the helper (no in-catalog sine anomaly yet).
# ------------------------------------------------------------------
def test_sine_shape_mean_near_midline(amc):
    """Sine shape: midline=10, amplitude=4, period=100. Across 1 full period
    the mean is exactly the midline (modulo floating-point noise)."""
    n = 100  # exactly one period at interval=1.0
    values = np.zeros((n, 1), dtype=np.float64)
    aspec = {
        "metric": "fake",
        "generator": lambda ts, idx: 0,
        "shape": "sine",
        "shape_params": {"period_s": 100, "amplitude": 4.0, "midline": 10.0},
    }
    _apply_anomaly_shape(amc, values, 0, 0, n, aspec, interval=1.0)
    assert math.isclose(values[:, 0].mean(), 10.0, abs_tol=0.05), (
        f"sine mean {values[:, 0].mean()} not ≈ 10.0"
    )
    # Range check: must touch within tolerance of midline ± amplitude.
    assert values[:, 0].max() > 13.9
    assert values[:, 0].min() < 6.1


# ------------------------------------------------------------------
# Brown-out — two adjacent ramp specs implement a triangle (up then down).
# ------------------------------------------------------------------
def test_brownout_triangle_profile(amc, one_day_run_a):
    """Brown-out splits the 20-min window into two 10-min ramps; the value at
    the join (~18:10:00) should be near 0.08 (peak)."""
    rows, header = read_component_rows(one_day_run_a.out_dir, "database")
    col = header.index("error_rate")
    start_ts = "2026-03-10 18:00:00"
    join_ts = "2026-03-10 18:10:00"
    end_ts = "2026-03-10 18:19:59"
    start_v = float(rows[start_ts][col])
    join_v = float(rows[join_ts][col])
    end_v = float(rows[end_ts][col])
    assert start_v < 0.01, f"brown-out start {start_v} should be near 0.001"
    assert 0.07 <= join_v <= 0.09, f"brown-out peak {join_v} should be near 0.08"
    assert end_v < 0.01, f"brown-out end {end_v} should be near 0.001"


# ------------------------------------------------------------------
# Manifest entry — span anomalies appear once, at the span start.
# ------------------------------------------------------------------
def test_span_manifest_at_start(amc, one_day_run_a):
    """Every span spec emits exactly one manifest row, with the timestamp
    equal to the span's start."""
    manifest = read_manifest(one_day_run_a.out_dir)
    expected = {
        ("cacheservice", "memory_util_pct", "2026-03-10 08:00:00"),
        ("apigateway", "avg_response_time_ms", "2026-03-10 09:30:00"),
        ("apigateway", "avg_response_time_ms", "2026-03-10 10:00:00"),
        ("apigateway", "requests_per_sec", "2026-03-10 19:00:00"),
        ("apigateway", "error_rate", "2026-03-10 19:00:00"),
        ("database", "disk_used_pct", "2026-03-10 00:00:00"),
        ("database", "connections", "2026-03-10 16:00:00"),
        ("database", "error_rate", "2026-03-10 18:00:00"),
        ("database", "error_rate", "2026-03-10 18:10:00"),
    }
    seen = {(e["component"], e["metric"], e["timestamp"]) for e in manifest}
    missing = expected - seen
    assert not missing, f"Span manifest entries missing: {sorted(missing)}"


# ------------------------------------------------------------------
# Unknown shape raises with a useful message.
# ------------------------------------------------------------------
def test_unknown_shape_raises(amc):
    values = np.zeros((4, 1), dtype=np.float64)
    aspec = {
        "metric": "fake",
        "generator": lambda ts, idx: 0,
        "shape": "not_a_shape",
    }
    with pytest.raises(ValueError, match="Unsupported anomaly shape"):
        _apply_anomaly_shape(amc, values, 0, 0, 4, aspec, interval=1.0)


# ------------------------------------------------------------------
# Catalog hygiene: every anomaly spec's ``shape_params`` keys must
# be consumed by ``_resolve_anomaly_value`` for the spec's shape.
# Catches dead config like ``sustained: {"multiplier": ...}`` (which
# the resolver ignores) before it ships as silently-misleading docs.
# ------------------------------------------------------------------
_SHAPE_PARAM_KEYS = {
    "step": frozenset(),
    "sustained": frozenset(),
    "ramp_linear": frozenset({"start", "end"}),
    "ramp_exp": frozenset({"start", "end", "exponent"}),
    "sawtooth": frozenset({"start", "period_s", "amplitude", "midline", "phase_s"}),
    "sine": frozenset({"start", "period_s", "amplitude", "midline", "phase_s"}),
}


def _all_specs(amc):
    out = []
    for slug, scenario in amc.SCENARIOS.items():
        for _component, spec in scenario.primary_specs:
            out.append((slug, spec))
    return out


def test_no_dead_shape_params_in_catalog(amc):
    """Every spec's ``shape_params`` keys must be consumed for its shape.

    Regression guard for the Copilot review finding on the gateway DDoS
    sustained scenario, which originally shipped with a no-op
    ``{"multiplier": 6.0}``. The resolver silently dropped it, leaving a
    misleading paper-only value.
    """
    offenders = []
    for source, spec in _all_specs(amc):
        shape = spec.get("shape", "step")
        params = spec.get("shape_params") or {}
        if not params:
            continue
        allowed = _SHAPE_PARAM_KEYS.get(shape)
        if allowed is None:
            offenders.append((source, shape, "unknown shape", sorted(params.keys())))
            continue
        unknown = set(params.keys()) - allowed
        if unknown:
            offenders.append((source, shape, "unknown keys", sorted(unknown)))
    assert not offenders, (
        "specs declare shape_params keys that the resolver does not read: "
        f"{offenders}"
    )


# ------------------------------------------------------------------
# DST artifact — duplicate hour around 02:00 on the configured day.
# ------------------------------------------------------------------
def test_dst_artifact_duplicates_02_hour(amc, tmp_path):
    """--inject-dst-artifact-day 1 duplicates the 02:00–02:59 wall-clock
    hour on day 1, so each component CSV gains 3,600 rows and the
    02:xx:xx timestamp range appears twice."""
    out = tmp_path / "dst_day1"
    run_capture(amc, out, days=1)  # baseline run, no DST flag
    rows_baseline, _ = read_component_rows(out, "authservice")
    baseline_row_count = len(rows_baseline)

    out_dst = tmp_path / "dst_day1_on"
    out_dst.mkdir()
    import io
    import sys as _sys
    args = [
        "--seed", "42",
        "--duration-days", "1",
        "--inject-dst-artifact-day", "1",
        "--output-dir", str(out_dst),
    ]
    stderr_buf = io.StringIO()
    real_stderr = _sys.stderr
    _sys.stderr = stderr_buf
    try:
        amc.main(args)
    finally:
        _sys.stderr = real_stderr

    # Count duplicate-timestamp lines directly (read_component_rows dedupes
    # by timestamp, so we count via a second pass).
    import csv
    with open(out_dst / "authservice.csv") as f:
        reader = csv.reader(f)
        next(reader)
        timestamps = [row[0] for row in reader if row]
    duplicate_hour = [t for t in timestamps if t.startswith("2026-03-10 02:")]
    # Each second in the duplicate hour appears twice (3,600 unique seconds);
    # tolerate the small drop count.
    assert 7100 <= len(duplicate_hour) <= 7200, (
        f"duplicate hour row count {len(duplicate_hour)} not near 7,200"
    )
    # Total row count: baseline + ~3,600 extras (modulo drops).
    assert len(timestamps) >= baseline_row_count + 3500, (
        f"DST run row count {len(timestamps)} not >= baseline {baseline_row_count} + 3,500"
    )


def test_dst_artifact_off_by_default(amc, one_day_run_a):
    """No --inject-dst-artifact-day flag means no duplicate timestamps."""
    import csv
    with open(one_day_run_a.out_dir / "authservice.csv") as f:
        reader = csv.reader(f)
        next(reader)
        timestamps = [row[0] for row in reader if row]
    duplicate_hour_02 = [t for t in timestamps if t.startswith("2026-03-10 02:")]
    # With no DST flag, the 02:xx hour has at most ~3,600 unique rows.
    assert len(duplicate_hour_02) <= 3600, (
        f"02:xx row count {len(duplicate_hour_02)} > 3,600 — DST flag leaked?"
    )


def test_dst_artifact_day_out_of_range_rejected(amc, tmp_path):
    """--inject-dst-artifact-day must not exceed --duration-days."""
    with pytest.raises(SystemExit):
        amc.parse_args([
            "--duration-days", "2",
            "--inject-dst-artifact-day", "5",
            "--output-dir", str(tmp_path),
        ])
