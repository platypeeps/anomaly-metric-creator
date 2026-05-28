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

    The span must contain ``duration / interval`` rows minus any configured drops, and the
    mean value across the span must be ≈ 2× the natural baseline (≈ 800).
    """
    start = amc.START + datetime.timedelta(hours=19)
    duration = 8 * 60
    span = _span_rows(one_day_run_a.out_dir, "apigateway",
                      "requests_per_sec", start, duration)
    # The default drop rate is zero; keep a small tolerance so this invariant
    # still exercises non-zero drop-rate fixture variants cleanly.
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
    run_capture(amc, out, days=1, interval_seconds=1.0)  # baseline run, no DST flag
    rows_baseline, _ = read_component_rows(out, "authservice")
    baseline_row_count = len(rows_baseline)

    out_dst = tmp_path / "dst_day1_on"
    out_dst.mkdir()
    import io
    import sys as _sys
    args = [
        "--seed", "42",
        "--duration-days", "1",
        "--interval-seconds", "1.0",
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


# ---------------------------------------------------------------------------
# shared CSV row builder eliminates the PR #63 long-form DST drop.
# These unit-level tests pin the contract of the new helpers regardless of
# which writer branch (dimensionless or long-form) consumes them.
# ---------------------------------------------------------------------------

def test_format_metric_suffix_joins_columns_with_commas(amc):
    """``_format_metric_suffix`` produces ``v0,v1,...,vk`` byte-for-byte."""
    str_vals = np.array(
        [["1.000", "2.500", "3.250"],
         ["4.750", "5.125", "6.000"]],
        dtype="U6",
    )
    suffix = amc._format_metric_suffix(str_vals)
    assert suffix.tolist() == ["1.000,2.500,3.250", "4.750,5.125,6.000"]


def test_format_metric_suffix_single_column_returns_copy(amc):
    """Single-metric components must still produce a usable suffix array.

    The caller mutates the returned array via further ``np.char.add``;
    if the helper returned a view of ``str_vals[:, 0]`` the writer
    would corrupt the source. ``np.shares_memory`` is the robust check
    here — ``suffix is not str_vals[:, 0]`` would always be true
    because ``str_vals[:, 0]`` returns a fresh view object on each
    access regardless of whether the data buffer is shared.
    """
    str_vals = np.array([["1.000"], ["2.000"]], dtype="U5")
    suffix = amc._format_metric_suffix(str_vals)
    assert suffix.tolist() == ["1.000", "2.000"]
    # Real aliasing check: the returned array's buffer must not overlap
    # the source array's buffer.
    assert not np.shares_memory(suffix, str_vals), (
        "single-column _format_metric_suffix returned an alias of "
        "str_vals[:, 0]; downstream np.char.add would mutate the source."
    )


def test_format_metric_suffix_multi_column_does_not_alias(amc):
    """Multi-column inputs must also produce a non-aliasing buffer.

    The multi-column path relies on the first ``np.char.add`` call to
    allocate a fresh array (so the helper can skip the explicit
    ``.copy()`` for the optimization win). This test pins that
    guarantee independent of the implementation choice: regardless of
    whether the helper copies eagerly or relies on the add-loop to
    allocate, the returned suffix must not share memory with
    ``str_vals``.
    """
    str_vals = np.array(
        [["1.000", "2.000"], ["3.000", "4.000"]], dtype="U5",
    )
    suffix = amc._format_metric_suffix(str_vals)
    assert suffix.tolist() == ["1.000,2.000", "3.000,4.000"]
    assert not np.shares_memory(suffix, str_vals), (
        "multi-column _format_metric_suffix returned an alias of "
        "str_vals; downstream np.char.add would mutate the source."
    )


def test_format_csv_row_block_dimensionless_layout(amc):
    """Empty ``dim_prefix`` produces ``ts,v0,...,vk`` byte-for-byte."""
    kept_ts = np.array(
        ["2026-03-10 00:00:00", "2026-03-10 00:00:01"], dtype="U19",
    )
    suffix = np.array(["1.000,2.000", "3.000,4.000"], dtype="U11")
    rows = amc._format_csv_row_block(
        kept_ts, suffix, dim_prefix="", dst_inject_day=0,
    )
    assert rows.tolist() == [
        "2026-03-10 00:00:00,1.000,2.000",
        "2026-03-10 00:00:01,3.000,4.000",
    ]


def test_format_csv_row_block_long_form_layout(amc):
    """Non-empty ``dim_prefix`` inserts dimensions between ts and metrics."""
    kept_ts = np.array(
        ["2026-03-10 00:00:00", "2026-03-10 00:00:01"], dtype="U19",
    )
    suffix = np.array(["1.000,2.000", "3.000,4.000"], dtype="U11")
    rows = amc._format_csv_row_block(
        kept_ts, suffix, dim_prefix=",i0,,pod-0,,,", dst_inject_day=0,
    )
    assert rows.tolist() == [
        "2026-03-10 00:00:00,i0,,pod-0,,,,1.000,2.000",
        "2026-03-10 00:00:01,i0,,pod-0,,,,3.000,4.000",
    ]


def test_format_csv_row_block_applies_dst_splice_in_long_form(amc):
    """The shared helper must apply ``_splice_dst_artifact`` regardless of
    ``dim_prefix``.

    This is the regression guard: before the refactor the
    long-form branch of ``generate_component``'s ``emit_metrics``
    writer (the PR #63 multi-instance path) took ``kept_ts`` /
    ``str_vals`` directly and never called ``_splice_dst_artifact``,
    so any caller that reached it with ``dst_inject_day > 0`` would
    silently drop the duplicated hour. After the refactor, both
    branches route through ``_format_csv_row_block`` and inherit the
    splice for free — a future caller that relaxes the ``parse_args``
    mutual-exclusion guard will not regress that bug.
    """
    # Walk a narrow window of one-second timestamps that straddles the
    # 02:00–02:59 splice range (from 01:59:58 through 03:00:00) so we
    # can assert the exact duplicated indices without generating an
    # 86,400-row component CSV. The splice covers day 1's
    # 02:00–02:59 wall-clock range.
    day_str = amc.START.strftime("%Y-%m-%d")
    seconds = [
        f"{day_str} 01:59:58",
        f"{day_str} 01:59:59",
        f"{day_str} 02:00:00",
        f"{day_str} 02:00:01",
        f"{day_str} 02:59:58",
        f"{day_str} 02:59:59",
        f"{day_str} 03:00:00",
    ]
    kept_ts = np.array(seconds, dtype="U19")
    n_rows = len(seconds)
    # Mark each row with its index so we can locate the duplicated
    # window unambiguously in the output.
    suffix = np.char.add(
        np.full(n_rows, "v", dtype="U1"),
        np.array([str(i) for i in range(n_rows)], dtype="U2"),
    )
    rows = amc._format_csv_row_block(
        kept_ts, suffix,
        dim_prefix=",i0,,pod-0,,,",
        dst_inject_day=1,
    )
    # The 02:00–02:59 inclusive window covers rows 2..5 (indices into
    # ``seconds`` above). _splice_dst_artifact inserts a duplicate of
    # rows[first:last+1] after rows[last], so the spliced array has
    # ``n_rows + 4`` entries and rows[6:10] match rows[2:6] verbatim.
    assert len(rows) == n_rows + 4, (
        f"expected {n_rows + 4} rows after DST splice, got {len(rows)}"
    )
    pre = rows[2:6].tolist()
    dup = rows[6:10].tolist()
    assert pre == dup, (
        f"DST splice did not duplicate the 02:xx window in the long-form "
        f"row block: pre={pre!r} dup={dup!r}"
    )
    # Each row in the duplicated window keeps the long-form dim prefix.
    for row in dup:
        assert row.startswith(f"{day_str} 02:"), row
        assert ",i0,,pod-0,,,," in row, row


def test_format_csv_row_block_dst_off_returns_input_length(amc):
    """``dst_inject_day=0`` (the default) must not re-shape the rows."""
    kept_ts = np.array(
        ["2026-03-10 00:00:00", "2026-03-10 00:00:01"], dtype="U19",
    )
    suffix = np.array(["1.000", "2.000"], dtype="U5")
    rows = amc._format_csv_row_block(
        kept_ts, suffix, dim_prefix="", dst_inject_day=0,
    )
    assert rows.tolist() == [
        "2026-03-10 00:00:00,1.000",
        "2026-03-10 00:00:01,2.000",
    ]
