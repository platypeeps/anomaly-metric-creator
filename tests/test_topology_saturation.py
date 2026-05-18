"""VER-154 phase 4: Saturation feedback (utilization -> latency multiplier + error offset).

Builds on phase 2/3 topology coupling (`tests/test_topology_loadbalancer_gateway.py`,
`tests/test_topology_fanout.py`). The phase-4 layer composes a sigmoid-shaped
saturation curve on top of incoming edges that carry `SaturationParams`:

- `_apply_saturation(upstream_load, sat)` returns `(latency_multiplier, error_offset)`
  numpy arrays. Independent unit tests pin its shape, monotonicity, bounds,
  and numerical stability.
- `_compose_topology_saturation_specs(...)` modifies the downstream's
  latency-family `MetricSpec.multiplier` and error-family `MetricSpec.additive`
  by composing on top of any pre-existing multiplier/additive.
- Default `--topology-mode independent` never invokes the saturation path,
  so per-component CSVs stay byte-identical to the pre-VER-154 baseline.

These tests cover:

* `_apply_saturation` shape, range, monotonicity, and edge cases.
* `_compose_topology_saturation_specs` composition correctness with absent /
  present natural multiplier/additive on the downstream MetricSpec.
* Default-mode byte-identity (no saturation under `--topology-mode independent`).
* Realistic-mode positive correlation between upstream load and downstream
  latency and error rate.
* Cap tests: error_rate column stays <= 1.0 under realistic mode; latency
  multiplier is always positive (no negative latency).
* TOPOLOGY structure: every saturating edge in v1 has `SaturationParams` with
  values inside the planned ranges, and the llm_analytics phase-5 placeholder
  still has zero gains.
"""
from __future__ import annotations

import dataclasses
import hashlib
import math

import numpy as np
import pytest

from conftest import read_component_rows, run_capture


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------
def _sha256_path(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _column_values(out_dir, component, metric):
    rows, header = read_component_rows(out_dir, component)
    idx = header.index(metric)
    ts_sorted = sorted(rows.keys())
    return (
        np.array([float(rows[ts][idx]) for ts in ts_sorted], dtype=np.float64),
        ts_sorted,
    )


def _aligned_columns(out_dir, *pairs):
    series = []
    for component, metric in pairs:
        vals, ts = _column_values(out_dir, component, metric)
        series.append((vals, ts, dict(zip(ts, vals))))
    common = sorted(set.intersection(*(set(ts) for _, ts, _ in series)))
    arrays = [
        np.array([lookup[t] for t in common], dtype=np.float64)
        for _, _, lookup in series
    ]
    return common, arrays


# Anomaly windows that override latency/error cells on coupled metrics. Excluding
# them keeps the Pearson correlation reflective of the saturation curve, not the
# scenario overrides. START is `2026-03-10 00:00:00`.
_EXCLUSION_WINDOWS = [
    # api_cpu_saturation retry storm on apigateway.requests_per_sec
    ("2026-03-10 19:00:00", "2026-03-10 19:08:00"),
    # db_stall nightly batch kickoff on database.queries_per_sec @ 23:00:00
    ("2026-03-10 23:00:00", "2026-03-10 23:00:01"),
    # cache_collapse primary spike on cacheservice.cache_misses @ 06:00:00
    ("2026-03-10 06:00:00", "2026-03-10 06:01:00"),
]


def _exclude_anomaly_rows(ts_list, *arrays):
    keep = [
        i for i, t in enumerate(ts_list)
        if not any(start <= t <= end for start, end in _EXCLUSION_WINDOWS)
    ]
    return tuple(arr[keep] for arr in arrays)


# ------------------------------------------------------------------
# _apply_saturation: unit tests on the logistic curve
# ------------------------------------------------------------------
def test_apply_saturation_neutral_at_zero_gains(amc):
    """Zero gains return latency multiplier = 1.0 (no scaling) and
    error offset = 0.0 (no addition) regardless of load."""
    sat = amc.SaturationParams(midpoint=100.0, steepness=6.0,
                               latency_gain=0.0, error_gain=0.0)
    load = np.array([0.0, 50.0, 100.0, 200.0, 1000.0], dtype=np.float64)
    lat_mult, err_off = amc._apply_saturation(load, sat)
    assert np.allclose(lat_mult, 1.0)
    assert np.allclose(err_off, 0.0)


def test_apply_saturation_at_midpoint_is_half(amc):
    """At load == midpoint, logistic = 0.5, so latency_multiplier =
    1 + 0.5 * latency_gain and error_offset = 0.5 * error_gain."""
    sat = amc.SaturationParams(midpoint=100.0, steepness=6.0,
                               latency_gain=0.4, error_gain=0.02)
    load = np.array([100.0, 100.0, 100.0], dtype=np.float64)
    lat_mult, err_off = amc._apply_saturation(load, sat)
    assert np.allclose(lat_mult, 1.0 + 0.5 * 0.4)
    assert np.allclose(err_off, 0.5 * 0.02)


def test_apply_saturation_monotone_in_load(amc):
    """Latency multiplier and error offset must be monotone non-decreasing
    in load (the logistic is monotone, and gains are non-negative)."""
    sat = amc.SaturationParams(midpoint=100.0, steepness=6.0,
                               latency_gain=0.5, error_gain=0.015)
    load = np.linspace(0.0, 400.0, 200, dtype=np.float64)
    lat_mult, err_off = amc._apply_saturation(load, sat)
    assert np.all(np.diff(lat_mult) >= -1e-12), (
        "latency multiplier must be monotone non-decreasing in load"
    )
    assert np.all(np.diff(err_off) >= -1e-12), (
        "error offset must be monotone non-decreasing in load"
    )


def test_apply_saturation_bounds(amc):
    """Latency multiplier ∈ [1, 1 + latency_gain]; error offset ∈ [0, error_gain].

    These bounds come from logistic ∈ [0, 1] and gains being non-negative
    constants. Together they cap the saturation effect so the per-edge
    contribution is well-bounded regardless of load magnitude.
    """
    sat = amc.SaturationParams(midpoint=100.0, steepness=8.0,
                               latency_gain=0.6, error_gain=0.012)
    # Range that includes well below, at, and well above the midpoint plus
    # a deliberately huge value to exercise the clamp.
    load = np.array([0.0, 1.0, 50.0, 100.0, 200.0, 1e6, 1e9], dtype=np.float64)
    lat_mult, err_off = amc._apply_saturation(load, sat)
    assert np.all(lat_mult >= 1.0)
    assert np.all(lat_mult <= 1.0 + sat.latency_gain + 1e-12)
    assert np.all(err_off >= 0.0)
    assert np.all(err_off <= sat.error_gain + 1e-12)


def test_apply_saturation_latency_multiplier_always_positive(amc):
    """Phase 4 acceptance: 'Latency multiplier never negative'."""
    sat = amc.SaturationParams(midpoint=100.0, steepness=8.0,
                               latency_gain=0.8, error_gain=0.02)
    # Mix in negative values (clamped to 0 by the function) so we exercise
    # the lower clamp path as well.
    load = np.array([-1e6, -1.0, 0.0, 50.0, 100.0, 500.0, 1e6], dtype=np.float64)
    lat_mult, _ = amc._apply_saturation(load, sat)
    assert np.all(lat_mult > 0.0), (
        f"latency multiplier dipped to non-positive: min={lat_mult.min()}"
    )


def test_apply_saturation_error_offset_capped_by_gain(amc):
    """Phase 4 acceptance: 'Saturation never drives error rates above 1.0'.

    The offset itself is capped at error_gain ∈ [0.005, 0.02], so the
    column-level contribution from saturation alone cannot push error_rate
    above 1.0 unless the natural column is already near 1.0 — which the
    integration test below guards against.
    """
    sat = amc.SaturationParams(midpoint=100.0, steepness=8.0,
                               latency_gain=0.6, error_gain=0.02)
    load = np.array([0.0, 100.0, 1e9], dtype=np.float64)
    _, err_off = amc._apply_saturation(load, sat)
    assert np.all(err_off <= 0.02 + 1e-12)


def test_apply_saturation_numerical_stability_huge_load(amc):
    """Very large load values must not produce NaN/Inf via exp overflow.

    The utilization clamp keeps the logistic argument in a finite range so
    `np.exp` stays finite for any input.
    """
    sat = amc.SaturationParams(midpoint=10.0, steepness=8.0,
                               latency_gain=0.5, error_gain=0.01)
    load = np.array([0.0, 1.0, 1e6, 1e12, 1e308], dtype=np.float64)
    lat_mult, err_off = amc._apply_saturation(load, sat)
    assert np.all(np.isfinite(lat_mult))
    assert np.all(np.isfinite(err_off))


def test_apply_saturation_rejects_zero_midpoint(amc):
    """A zero midpoint would divide-by-zero; the function must reject it."""
    sat = amc.SaturationParams(midpoint=0.0, steepness=6.0,
                               latency_gain=0.5, error_gain=0.01)
    with pytest.raises(ValueError, match=r"midpoint"):
        amc._apply_saturation(np.array([1.0, 2.0, 3.0]), sat)


def test_apply_saturation_rejects_negative_midpoint(amc):
    """A negative midpoint is structurally invalid even if division would
    work numerically."""
    sat = amc.SaturationParams(midpoint=-1.0, steepness=6.0,
                               latency_gain=0.5, error_gain=0.01)
    with pytest.raises(ValueError, match=r"midpoint"):
        amc._apply_saturation(np.array([1.0, 2.0, 3.0]), sat)


# ------------------------------------------------------------------
# _compose_topology_saturation_specs: composition tests
# ------------------------------------------------------------------
def test_compose_saturation_specs_no_targets_passthrough(amc):
    """A component absent from `_TOPOLOGY_SATURATION_TARGETS` gets its
    specs returned verbatim."""
    specs = amc.COMPONENTS["loadbalancer"]
    upstream_arrays = {}
    out = amc._compose_topology_saturation_specs(
        "loadbalancer", specs, upstream_arrays, n_rows=10,
    )
    assert out is specs or out == specs


def test_compose_saturation_specs_no_upstream_arrays_passthrough(amc):
    """A target component with no upstream captured arrays gets specs back
    unchanged (graceful degrade when `--components` excludes the upstream)."""
    specs = list(amc.COMPONENTS["apigateway"])
    out = amc._compose_topology_saturation_specs(
        "apigateway", specs, upstream_arrays={}, n_rows=10,
    )
    assert all(a is b for a, b in zip(out, specs)), (
        "specs must be returned untouched when no upstream is captured"
    )


def test_compose_saturation_specs_replaces_latency_multiplier(amc):
    """When an upstream array is present, the latency MetricSpec.multiplier
    must be a callable that returns saturation-shaped values."""
    n_rows = 100
    specs = list(amc.COMPONENTS["apigateway"])
    # Build a synthetic upstream load that varies clearly across the run.
    load = np.linspace(500.0, 1200.0, n_rows, dtype=np.float64)
    upstream_arrays = {"loadbalancer": {"requests_per_sec": load}}
    out = amc._compose_topology_saturation_specs(
        "apigateway", specs, upstream_arrays, n_rows=n_rows,
    )
    by_name = {s.name: s for s in out}
    lat_spec = by_name["avg_response_time_ms"]
    # Call the multiplier with throwaway args; it should return n_rows values.
    ts = np.zeros(n_rows, dtype="datetime64[s]")
    elapsed = np.arange(n_rows, dtype=np.float64)
    mult = lat_spec.multiplier(ts, elapsed)
    assert mult.shape == (n_rows,)
    assert np.all(mult >= 1.0)
    # Monotone increasing along the synthetic load ramp (within numerical noise).
    assert mult[-1] > mult[0], (
        f"latency multiplier did not increase with synthetic load ramp: "
        f"first={mult[0]}, last={mult[-1]}"
    )


def test_compose_saturation_specs_composes_with_existing_multiplier(amc):
    """If the natural MetricSpec already has a multiplier (e.g. a daily
    sine), the saturation factor composes multiplicatively on top of it.
    Use a hand-crafted MetricSpec to avoid depending on real catalog
    multipliers (none of the latency-family columns currently set one)."""
    n_rows = 50
    load = np.full(n_rows, 100.0, dtype=np.float64)  # at midpoint => logistic 0.5
    # Patch in a saturating edge with known params.
    sat = amc.SaturationParams(midpoint=100.0, steepness=6.0,
                               latency_gain=0.4, error_gain=0.01)
    # Hand-roll a target component "synth" with a multiplier that returns 2.0.
    # We test the composition primitive directly by stubbing the registries.
    base_multiplier = lambda _ts, _elapsed, n=n_rows: np.full(n, 2.0)
    base_additive = lambda _ts, _elapsed, n=n_rows: np.full(n, 0.05)
    fake_spec = amc.MetricSpec(
        name="latency_ms", base=100.0, std=0.0,
        multiplier=base_multiplier,
    )
    err_spec = amc.MetricSpec(
        name="error_rate", base=0.1, std=0.0,
        additive=base_additive,
    )

    # Monkeypatch the registries via direct dict access (no monkeypatch fixture
    # needed; we restore inside the test).
    saved_targets = amc._TOPOLOGY_SATURATION_TARGETS.copy()
    saved_topology = dict(amc.TOPOLOGY)
    try:
        amc._TOPOLOGY_SATURATION_TARGETS["synthcomp"] = (
            ("latency_ms",), ("error_rate",),
        )
        amc.TOPOLOGY["synthup"] = [
            amc.Edge(target="synthcomp", weight=1.0, saturation=sat)
        ]
        # Ensure the helper picks the right load metric for "synthup".
        saved_load_metrics = amc._TOPOLOGY_LOAD_METRICS.copy()
        amc._TOPOLOGY_LOAD_METRICS["synthup"] = ("synthload",)
        try:
            upstream_arrays = {"synthup": {"synthload": load}}
            out = amc._compose_topology_saturation_specs(
                "synthcomp", [fake_spec, err_spec], upstream_arrays, n_rows=n_rows,
            )
            by_name = {s.name: s for s in out}
            # Composition: base_multiplier(=2.0) * (1 + 0.4 * 0.5) = 2.4
            ts = np.zeros(n_rows, dtype="datetime64[s]")
            elapsed = np.arange(n_rows, dtype=np.float64)
            assert np.allclose(by_name["latency_ms"].multiplier(ts, elapsed), 2.4)
            # base_additive(=0.05) + 0.01 * 0.5 = 0.055
            assert np.allclose(by_name["error_rate"].additive(ts, elapsed), 0.055)
        finally:
            amc._TOPOLOGY_LOAD_METRICS.clear()
            amc._TOPOLOGY_LOAD_METRICS.update(saved_load_metrics)
    finally:
        amc._TOPOLOGY_SATURATION_TARGETS.clear()
        amc._TOPOLOGY_SATURATION_TARGETS.update(saved_targets)
        amc.TOPOLOGY.clear()
        amc.TOPOLOGY.update(saved_topology)


def test_compose_saturation_specs_zero_gain_edges_skipped(amc):
    """Edges with zero latency_gain AND zero error_gain (the phase-5 LLM
    placeholder shape) must not modify the downstream specs even though
    they are saturating edges by structure."""
    n_rows = 50
    load = np.linspace(0.0, 1000.0, n_rows, dtype=np.float64)
    upstream_arrays = {"apigateway": {"requests_per_sec": load}}
    # llm_analytics receives a saturating edge with zero gains today.
    specs = list(amc.COMPONENTS["llm_analytics"])
    out = amc._compose_topology_saturation_specs(
        "llm_analytics", specs, upstream_arrays, n_rows=n_rows,
    )
    # llm_analytics has no entry in _TOPOLOGY_SATURATION_TARGETS in phase 4,
    # so its specs should be unchanged regardless of the edge.
    assert all(a is b for a, b in zip(out, specs))


# ------------------------------------------------------------------
# TOPOLOGY: SaturationParams declared on the v1 saturating edges
# ------------------------------------------------------------------
def _saturating_edges(amc):
    """Return [(source, edge), ...] for every TOPOLOGY edge with non-None
    SaturationParams."""
    out = []
    for source, edges in amc.TOPOLOGY.items():
        for edge in edges:
            if edge.saturation is not None:
                out.append((source, edge))
    return out


def test_topology_has_saturating_edges_for_phase4(amc):
    """Phase 4 must declare SaturationParams on the four front-half edges
    so saturation feedback actually fires under --topology-mode realistic.
    Also keeps the llm_analytics phase-5 placeholder in the list."""
    saturating = _saturating_edges(amc)
    pairs = {(src, edge.target) for src, edge in saturating}
    assert ("loadbalancer", "apigateway") in pairs
    assert ("apigateway", "authservice") in pairs
    assert ("apigateway", "cacheservice") in pairs
    assert ("apigateway", "database") in pairs


def test_topology_llm_analytics_edge_still_zero_gain_placeholder(amc):
    """Phase 5 reserves the apigateway -> llm_analytics token-throttle
    saturation; phase 4 must leave its gains at zero."""
    for src, edge in _saturating_edges(amc):
        if (src, edge.target) == ("apigateway", "llm_analytics"):
            assert edge.saturation.latency_gain == 0.0
            assert edge.saturation.error_gain == 0.0
            return
    pytest.fail("apigateway -> llm_analytics saturation placeholder edge missing")


def test_topology_saturation_params_in_planned_ranges(amc):
    """All non-placeholder saturating edges declared in phase 4 must use
    gains within the issue's recommended ranges:
      steepness ∈ [5, 8], latency_gain ∈ [0.3, 0.8], error_gain ∈ [0.005, 0.02].
    """
    for src, edge in _saturating_edges(amc):
        sat = edge.saturation
        if sat.latency_gain == 0.0 and sat.error_gain == 0.0:
            continue  # phase-5 placeholder
        assert 5.0 <= sat.steepness <= 8.0, (
            f"{src} -> {edge.target} steepness={sat.steepness} out of "
            f"[5, 8] range"
        )
        assert 0.3 <= sat.latency_gain <= 0.8, (
            f"{src} -> {edge.target} latency_gain={sat.latency_gain} out of "
            f"[0.3, 0.8] range"
        )
        assert 0.005 <= sat.error_gain <= 0.02, (
            f"{src} -> {edge.target} error_gain={sat.error_gain} out of "
            f"[0.005, 0.02] range"
        )
        assert sat.midpoint > 0.0


# ------------------------------------------------------------------
# Default mode byte-identical (no saturation under independent)
# ------------------------------------------------------------------
def test_independent_mode_latency_csvs_byte_identical_to_default(
    amc, one_day_run_a, tmp_path
):
    """Default `--topology-mode independent` must not invoke saturation,
    so latency and error CSVs stay byte-for-byte identical to the
    pre-VER-154 baseline (which is captured by the session-scoped
    `one_day_run_a` fixture)."""
    explicit = run_capture(
        amc, tmp_path / "explicit_independent", days=1,
        extra_args=["--topology-mode", "independent"],
    )
    for filename in (
        "loadbalancer.csv", "apigateway.csv", "authservice.csv",
        "cacheservice.csv", "database.csv", "anomalies.csv",
    ):
        default_hash = _sha256_path(one_day_run_a.out_dir / filename)
        explicit_hash = _sha256_path(explicit.out_dir / filename)
        assert default_hash == explicit_hash, (
            f"{filename} drifted between default run and "
            f"--topology-mode independent run under saturation phase"
        )


# ------------------------------------------------------------------
# Realistic-mode: latency and error rate correlate with upstream load
# ------------------------------------------------------------------
@pytest.fixture(scope="module")
def realistic_one_day_sat(amc, tmp_path_factory):
    out = tmp_path_factory.mktemp("phase4_realistic")
    return run_capture(
        amc, out, days=1, extra_args=["--topology-mode", "realistic"]
    )


@pytest.mark.parametrize(
    "upstream_metric,downstream_metric",
    [
        # apigateway latency tracks loadbalancer RPS (the saturating edge
        # is loadbalancer -> apigateway).
        (("loadbalancer", "requests_per_sec"),
         ("apigateway", "avg_response_time_ms")),
        (("loadbalancer", "requests_per_sec"),
         ("apigateway", "backend_latency_ms")),
        # Front-half fan-out targets get saturation from apigateway RPS.
        (("apigateway", "requests_per_sec"),
         ("authservice", "avg_auth_latency_ms")),
        (("apigateway", "requests_per_sec"),
         ("cacheservice", "avg_cache_latency_ms")),
        (("apigateway", "requests_per_sec"),
         ("database", "read_latency_ms")),
        (("apigateway", "requests_per_sec"),
         ("database", "write_latency_ms")),
    ],
)
def test_realistic_latency_correlates_with_upstream_load(
    realistic_one_day_sat, amc, upstream_metric, downstream_metric
):
    """Each saturating edge must drive a positive correlation between
    upstream load and downstream latency. The correlation is dominated by
    noise (column std around the per-spec sigma), so we only require it
    to clear a modest positive threshold — well above the ~0 we would
    see if saturation was not wired through."""
    common, (upstream, latency) = _aligned_columns(
        realistic_one_day_sat.out_dir, upstream_metric, downstream_metric
    )
    upstream_x, latency_x = _exclude_anomaly_rows(common, upstream, latency)
    corr = float(np.corrcoef(upstream_x, latency_x)[0, 1])
    assert corr > 0.15, (
        f"realistic-mode Pearson({upstream_metric[0]}.{upstream_metric[1]}, "
        f"{downstream_metric[0]}.{downstream_metric[1]})={corr:.4f}; "
        f"expected > 0.15 (saturation should produce a positive "
        f"correlation between upstream load and downstream latency)"
    )


@pytest.mark.parametrize(
    "component,natural_base,error_gain",
    [
        ("apigateway", 0.15, 0.010),
        ("authservice", 0.20, 0.012),
        ("cacheservice", 0.05, 0.008),
        ("database", 0.10, 0.015),
    ],
)
def test_realistic_error_rate_mean_elevated_vs_independent(
    amc, tmp_path, component, natural_base, error_gain
):
    """Per-edge ``error_gain`` is small (≤ 0.02) relative to the natural
    noise sigma on the error_rate column (~0.02-0.05), so a row-level
    correlation is hard to detect statistically. The first-moment shift
    is the cleaner signal: the saturation offset adds a positive bias
    proportional to the logistic, which lifts the column mean under
    realistic mode relative to independent. The expected lift is on
    the order of ``error_gain * 0.5`` (logistic averages roughly 0.5
    around its midpoint), so we accept anything materially above zero.
    """
    indep = run_capture(
        amc, tmp_path / f"phase4_err_indep_{component}", days=1,
        extra_args=["--topology-mode", "independent"],
    )
    real = run_capture(
        amc, tmp_path / f"phase4_err_real_{component}", days=1,
        extra_args=["--topology-mode", "realistic"],
    )
    indep_vals, _ = _column_values(indep.out_dir, component, "error_rate")
    real_vals, _ = _column_values(real.out_dir, component, "error_rate")
    lift = float(np.mean(real_vals) - np.mean(indep_vals))
    # Allow for noise jitter: the lift floor is a tenth of the error_gain
    # which is well below the analytical expectation but well above zero.
    floor = error_gain * 0.1
    assert lift > floor, (
        f"realistic error_rate mean for {component} not elevated above "
        f"independent: realistic={np.mean(real_vals):.5f}, "
        f"independent={np.mean(indep_vals):.5f}, lift={lift:.5f}, "
        f"floor={floor:.5f}; saturation error offset looks inert"
    )


# ------------------------------------------------------------------
# Cap tests: realistic-mode column bounds
# ------------------------------------------------------------------
@pytest.mark.parametrize(
    "component", ["apigateway", "authservice", "cacheservice", "database"],
)
def test_realistic_error_rate_never_above_one(
    realistic_one_day_sat, component
):
    """Phase 4 acceptance: 'Saturation never drives error rates above 1.0'."""
    vals, _ = _column_values(
        realistic_one_day_sat.out_dir, component, "error_rate"
    )
    assert vals.max() <= 1.0, (
        f"{component}.error_rate max={vals.max():.6f} exceeded 1.0 under "
        f"realistic saturation"
    )


@pytest.mark.parametrize(
    "component,metric",
    [
        ("apigateway", "avg_response_time_ms"),
        ("apigateway", "backend_latency_ms"),
        ("authservice", "avg_auth_latency_ms"),
        ("cacheservice", "avg_cache_latency_ms"),
        ("database", "read_latency_ms"),
        ("database", "write_latency_ms"),
    ],
)
def test_realistic_latency_never_negative(
    realistic_one_day_sat, component, metric
):
    """Phase 4 acceptance: 'Latency multiplier never negative'. Because the
    natural latency base is positive and the multiplier is always >= 1,
    the resulting column must stay non-negative."""
    vals, _ = _column_values(realistic_one_day_sat.out_dir, component, metric)
    assert vals.min() >= 0.0, (
        f"{component}.{metric} min={vals.min():.6f} went negative under "
        f"realistic saturation"
    )


# ------------------------------------------------------------------
# Realistic-mode contrast: independent latency means stay near natural base
# ------------------------------------------------------------------
@pytest.mark.parametrize(
    "component,metric",
    [
        ("apigateway", "avg_response_time_ms"),
        ("apigateway", "backend_latency_ms"),
        ("authservice", "avg_auth_latency_ms"),
        ("cacheservice", "avg_cache_latency_ms"),
        ("database", "read_latency_ms"),
        ("database", "write_latency_ms"),
    ],
)
def test_realistic_latency_mean_elevated_vs_independent(
    amc, tmp_path, component, metric
):
    """Under realistic mode the saturation curve must lift the latency
    column's mean above the independent-mode mean. We can't pin the
    independent mean to the natural ``MetricSpec.base`` because several
    scenarios already inject long-duration latency overrides (e.g. the
    apigateway ``Deploy regression`` step from 10:00 onwards). The
    contrast against independent mode is therefore the cleaner signal:
    saturation must produce a measurable positive lift that survives
    those baked-in overrides.
    """
    indep = run_capture(
        amc, tmp_path / f"phase4_lat_indep_{component}_{metric}",
        days=1, extra_args=["--topology-mode", "independent"],
    )
    real = run_capture(
        amc, tmp_path / f"phase4_lat_real_{component}_{metric}",
        days=1, extra_args=["--topology-mode", "realistic"],
    )
    indep_vals, _ = _column_values(indep.out_dir, component, metric)
    real_vals, _ = _column_values(real.out_dir, component, metric)
    indep_mean = float(np.mean(indep_vals))
    real_mean = float(np.mean(real_vals))
    assert real_mean > indep_mean + 1.0, (
        f"realistic-mode {component}.{metric} mean={real_mean:.2f} not "
        f"elevated above independent-mode mean={indep_mean:.2f}; "
        f"saturation looks inert"
    )
