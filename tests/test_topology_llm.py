"""VER-155 phase 5: LLM topology coupling (token-throttle → llm_analytics).

Phase 5 closes the v1 topology graph by promoting the
``apigateway -> llm_analytics`` placeholder edge declared in phase 1
into a real coupling:

* The edge weight makes ``llm_analytics.input_tokens_per_sec`` track
  ``apigateway.requests_per_sec`` under ``--topology-mode realistic``
  via the phase-3 constant-weight machinery in
  ``_compose_topology_coupled_specs``. The natural baseline
  (~25 000 tokens/s) is reproduced at natural apigateway load
  (~800 rps) thanks to the per-downstream renormalization, and
  variation in apigateway flows through proportionally.
* The edge's ``SaturationParams`` plug into the phase-4
  ``_apply_saturation`` / ``_compose_topology_saturation_specs`` path
  so that as ``apigateway.requests_per_sec`` approaches its
  token-budget midpoint, ``avg_llm_latency_ms`` /
  ``p95_llm_latency_ms`` lift via the logistic ``latency_multiplier``
  and ``llm_api_error_rate`` lifts via the additive ``error_offset``.

Decision (documented in CLAUDE.md): no synthetic ``token_limiter``
virtual node — apigateway already serves as the metering authority
for LLM-bound traffic in the v1 graph. The midpoint is expressed in
``apigateway.requests_per_sec`` units (i.e. the same scale as the
other ``apigateway -> *`` saturation edges) so the curve shifts the
LLM-side response in lockstep with the rest of the front-half fan-out.

Acceptance gates exercised here:

* TOPOLOGY structure: the ``apigateway -> llm_analytics`` edge now
  carries a non-zero weight and non-zero gains within the phase-4
  planned ranges.
* Registry wiring: ``llm_analytics`` appears in both
  ``_TOPOLOGY_LOAD_METRICS`` and ``_TOPOLOGY_SATURATION_TARGETS``.
* Realistic-mode load-coupling correlation: Pearson(
  apigateway.requests_per_sec,
  llm_analytics.input_tokens_per_sec) >= 0.85 on the 1-day default
  seed.
* Realistic-mode latency / error lift: latency and error means under
  realistic mode exceed independent-mode means by a measurable margin.
* Caps: latency stays non-negative, error rate stays <= 1.0.
* Default (independent) llm_analytics.csv is byte-identical to the
  pre-VER-155 baseline, so no locked SHA-256 hashes drift.
"""
from __future__ import annotations

import hashlib
from datetime import timedelta

import numpy as np
import pytest

from conftest import _load_amc, read_component_rows, run_capture


# ------------------------------------------------------------------
# Helpers (mirror tests/test_topology_saturation.py)
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


# Anomaly windows that override coupled / saturated llm_analytics cells
# (or the upstream apigateway.requests_per_sec used to drive them) on
# the default 1-day seed. Excluding them keeps correlations / means
# reflective of the topology coupling, not the scenario overrides.
#
# Derived from the live SCENARIOS catalog so future scenario edits
# don't silently drift the test: we walk every same-day,
# non-high-severity scenario and add a window for every primary or
# cascade spec whose target is either ``apigateway.requests_per_sec``
# (the upstream of the LLM coupling) or one of the LLM columns the
# saturation layer rewrites (``input_tokens_per_sec``,
# ``avg_llm_latency_ms``, ``p95_llm_latency_ms``, ``llm_api_error_rate``).
# Each window covers ``[time_offset, time_offset + duration_seconds]``
# padded by ``_EXCLUSION_PAD_SECONDS`` on either side so the single-row
# cascades that round to the nearest 1s row aren't included in the
# correlation pool.
_EXCLUSION_PAD_SECONDS = 30

_LLM_SATURATION_AFFECTED_METRICS = frozenset({
    "input_tokens_per_sec",
    "avg_llm_latency_ms",
    "p95_llm_latency_ms",
    "llm_api_error_rate",
})


def _compute_llm_exclusion_windows():
    amc = _load_amc()
    start = amc.START
    windows: list[tuple[str, str]] = []
    for scen in amc.SCENARIOS.values():
        if scen.days_required > 1 or scen.severity == "high":
            continue
        for component, spec in (*scen.primary_specs, *scen.cascade_specs):
            is_upstream = (
                component == "apigateway"
                and spec["metric"] == "requests_per_sec"
            )
            is_llm_saturation = (
                component == "llm_analytics"
                and spec["metric"] in _LLM_SATURATION_AFFECTED_METRICS
            )
            if not (is_upstream or is_llm_saturation):
                continue
            duration = spec.get("duration_seconds", 0) or 0
            t_start = max(0, spec["time_offset"] - _EXCLUSION_PAD_SECONDS)
            t_end = spec["time_offset"] + duration + _EXCLUSION_PAD_SECONDS
            windows.append((
                (start + timedelta(seconds=t_start)).strftime("%Y-%m-%d %H:%M:%S"),
                (start + timedelta(seconds=t_end)).strftime("%Y-%m-%d %H:%M:%S"),
            ))
    windows.sort()
    return windows


_EXCLUSION_WINDOWS = _compute_llm_exclusion_windows()


def _exclude_anomaly_rows(ts_list, *arrays):
    keep = [
        i for i, t in enumerate(ts_list)
        if not any(start <= t <= end for start, end in _EXCLUSION_WINDOWS)
    ]
    return tuple(arr[keep] for arr in arrays)


# ------------------------------------------------------------------
# Module-scoped 1-day realistic + independent runs (shared across tests)
# ------------------------------------------------------------------
@pytest.fixture(scope="module")
def realistic_one_day_llm(amc, tmp_path_factory):
    out = tmp_path_factory.mktemp("phase5_realistic")
    return run_capture(
        amc, out, days=1, extra_args=["--topology-mode", "realistic"]
    )


@pytest.fixture(scope="module")
def independent_one_day_llm(amc, tmp_path_factory):
    out = tmp_path_factory.mktemp("phase5_independent")
    return run_capture(
        amc, out, days=1, extra_args=["--topology-mode", "independent"]
    )


# ------------------------------------------------------------------
# TOPOLOGY: apigateway -> llm_analytics edge promoted to real coupling
# ------------------------------------------------------------------
def _find_llm_edge(amc):
    """Return the ``Edge`` instance for ``apigateway -> llm_analytics``."""
    for edge in amc.TOPOLOGY.get("apigateway", ()):
        if edge.target == "llm_analytics":
            return edge
    pytest.fail("apigateway -> llm_analytics edge missing from TOPOLOGY")


def test_llm_edge_weight_is_active(amc):
    """Phase 5 promotes the placeholder weight=0.0 to a positive,
    finite, non-bool value so ``_compose_topology_coupled_specs``
    treats the edge as an active constant-weight contribution to
    ``llm_analytics.input_tokens_per_sec`` (the canonical LLM load
    metric registered in ``_TOPOLOGY_LOAD_METRICS``)."""
    edge = _find_llm_edge(amc)
    weight = edge.weight
    assert isinstance(weight, (int, float))
    assert not isinstance(weight, bool)
    assert weight > 0.0
    # A non-tiny weight that doesn't blow the renormalization. We don't
    # pin a specific magnitude because the per-downstream normalization
    # collapses single-edge incomings to w_norm = 1.0 regardless; any
    # finite positive weight here is structurally equivalent.
    assert weight <= 5.0, (
        f"apigateway -> llm_analytics weight={weight} is unexpectedly "
        f"large; the edge is a single-incoming amplifier so the value "
        f"is normalized away, but a runaway literal probably indicates "
        f"the placeholder was edited by accident"
    )


def test_llm_edge_has_saturation_with_non_zero_gains(amc):
    """Phase 5 fills in the SaturationParams placeholder with non-zero
    gains so the token-budget throttle is observable on the downstream
    latency / error columns."""
    edge = _find_llm_edge(amc)
    sat = edge.saturation
    assert sat is not None, (
        "apigateway -> llm_analytics edge must carry SaturationParams"
    )
    assert sat.latency_gain > 0.0, (
        f"phase 5 latency_gain={sat.latency_gain} must be > 0; the "
        f"token-throttle response is what produces the LLM-side latency "
        f"lift under high apigateway load"
    )
    assert sat.error_gain > 0.0, (
        f"phase 5 error_gain={sat.error_gain} must be > 0; the "
        f"token-throttle response is what produces the LLM-side error "
        f"lift under high apigateway load"
    )


def test_llm_edge_saturation_in_planned_ranges(amc):
    """The LLM saturation parameters must fall in the phase-4 issue
    ranges (steepness ∈ [5, 8], latency_gain ∈ [0.3, 0.8], error_gain
    ∈ [0.005, 0.02]) so the curve composes coherently with the other
    apigateway-driven saturations. Midpoint is expressed in apigateway
    requests_per_sec units (same scale as the other apigateway->*
    edges)."""
    edge = _find_llm_edge(amc)
    sat = edge.saturation
    assert 5.0 <= sat.steepness <= 8.0, (
        f"apigateway -> llm_analytics steepness={sat.steepness} out of "
        f"[5, 8] range"
    )
    assert 0.3 <= sat.latency_gain <= 0.8, (
        f"apigateway -> llm_analytics latency_gain={sat.latency_gain} "
        f"out of [0.3, 0.8] range"
    )
    assert 0.005 <= sat.error_gain <= 0.02, (
        f"apigateway -> llm_analytics error_gain={sat.error_gain} out "
        f"of [0.005, 0.02] range"
    )
    # Midpoint must be in apigateway RPS units. The apigateway natural
    # peak is ~950 rps, so a token-budget midpoint sits well inside the
    # [200, 1500] interval. A value <= 1 would imply the legacy
    # 0..1 "utilization" placeholder is still in place.
    assert 200.0 <= sat.midpoint <= 1500.0, (
        f"apigateway -> llm_analytics midpoint={sat.midpoint} not in "
        f"the expected apigateway requests_per_sec scale [200, 1500]; "
        f"if you see a value <= 1 the placeholder utilization-scale "
        f"midpoint was probably left untouched"
    )


def test_llm_analytics_in_load_metrics_registry(amc):
    """``_TOPOLOGY_LOAD_METRICS`` controls which downstream columns the
    phase-3 coupling rewrites. The canonical load metric for
    ``llm_analytics`` is ``input_tokens_per_sec`` — token throughput,
    not request rate, is the quantity the apigateway-side token budget
    actually meters."""
    load_metrics = amc._TOPOLOGY_LOAD_METRICS.get("llm_analytics", ())
    assert "input_tokens_per_sec" in load_metrics
    # First entry is the canonical load metric used by upstream
    # consumers when this component is itself an upstream. Today
    # nothing consumes llm_analytics, but pin the first entry for
    # forward compatibility.
    assert load_metrics[0] == "input_tokens_per_sec"


def test_llm_analytics_in_saturation_targets_registry(amc):
    """``_TOPOLOGY_SATURATION_TARGETS`` declares which downstream
    columns saturation modifies. Phase 5 must register the LLM
    latency and error families so the placeholder edge stops being
    inert and the phase-4 composition actually mutates llm_analytics
    specs under realistic mode."""
    targets = amc._TOPOLOGY_SATURATION_TARGETS.get("llm_analytics")
    assert targets is not None, (
        "llm_analytics must be a saturation target in phase 5"
    )
    latency_metrics, error_metrics = targets
    # Latency family: cover both the average and the p95 the catalog
    # exposes so realistic-mode lifts both columns coherently.
    assert "avg_llm_latency_ms" in latency_metrics
    assert "p95_llm_latency_ms" in latency_metrics
    # Error family: the LLM-specific API error rate is the analogue of
    # other components' ``error_rate``. We don't want to lift the
    # generic ``error_rate`` because llm_analytics doesn't expose one.
    assert "llm_api_error_rate" in error_metrics


# ------------------------------------------------------------------
# Default (independent) mode: byte-identical pre-VER-155 baseline
# ------------------------------------------------------------------
def test_independent_mode_llm_analytics_byte_identical_to_default(
    amc, one_day_run_a, independent_one_day_llm,
):
    """``--topology-mode independent`` (the default) must not touch the
    LLM coupling or saturation paths, so ``llm_analytics.csv`` stays
    byte-for-byte identical to the default 1-day run captured by the
    session-scoped fixture."""
    default_hash = _sha256_path(one_day_run_a.out_dir / "llm_analytics.csv")
    explicit_hash = _sha256_path(
        independent_one_day_llm.out_dir / "llm_analytics.csv"
    )
    assert default_hash == explicit_hash, (
        "llm_analytics.csv drifted between the default 1-day run and "
        "an explicit --topology-mode independent run; phase 5 must "
        "keep the independent baseline byte-identical"
    )


# ------------------------------------------------------------------
# Realistic mode: RPS coupling tracks apigateway
# ------------------------------------------------------------------
def test_realistic_llm_token_throughput_tracks_apigateway(
    realistic_one_day_llm, amc,
):
    """Issue acceptance: ``llm_analytics`` RPS / token-throughput now
    tracks upstream gating in realistic mode at >= 0.85 Pearson
    correlation against ``apigateway.requests_per_sec`` on the 1-day
    default seed.

    ``input_tokens_per_sec`` is the canonical LLM load metric here
    (token budget meters tokens/second). Its baseline (25000) sits
    well above the absolute coupling noise floor
    (``_TOPOLOGY_COUPLE_NOISE_STD = 5.0``), so the upstream-driven
    signal dominates and the correlation lands close to 1.
    """
    common, (api_rps, llm_tokens) = _aligned_columns(
        realistic_one_day_llm.out_dir,
        ("apigateway", "requests_per_sec"),
        ("llm_analytics", "input_tokens_per_sec"),
    )
    api_x, llm_x = _exclude_anomaly_rows(common, api_rps, llm_tokens)
    assert len(api_x) > 60_000, (
        f"too few rows after exclusion to compute correlation: {len(api_x)}"
    )
    corr = float(np.corrcoef(api_x, llm_x)[0, 1])
    assert corr >= 0.85, (
        f"realistic-mode Pearson(apigateway.requests_per_sec, "
        f"llm_analytics.input_tokens_per_sec)={corr:.4f}; "
        f"expected >= 0.85 — the LLM token throughput must track "
        f"upstream gating per the VER-155 acceptance criterion"
    )


def test_independent_mode_llm_token_throughput_uncoupled(
    independent_one_day_llm,
):
    """Sanity check: in independent mode ``input_tokens_per_sec`` is
    driven by its own ``_llm_business_hours`` envelope and Gaussian
    jitter, so the Pearson correlation against apigateway RPS is
    comparatively low (well below the realistic threshold). Mirrors
    the contrast test in test_topology_loadbalancer_gateway.py."""
    common, (api_rps, llm_tokens) = _aligned_columns(
        independent_one_day_llm.out_dir,
        ("apigateway", "requests_per_sec"),
        ("llm_analytics", "input_tokens_per_sec"),
    )
    api_x, llm_x = _exclude_anomaly_rows(common, api_rps, llm_tokens)
    corr = float(np.corrcoef(api_x, llm_x)[0, 1])
    # apigateway requests_per_sec has no daily multiplier (constant
    # base + jitter), so its independent-mode correlation with any
    # business-hours-shaped LLM column is approximately zero. Use a
    # generous ceiling well below the realistic threshold.
    assert corr < 0.5, (
        f"independent-mode Pearson(apigateway.requests_per_sec, "
        f"llm_analytics.input_tokens_per_sec)={corr:.4f} is unexpectedly "
        f"high; the realistic-mode correlation must be the discriminating "
        f"signal between the two modes"
    )


# ------------------------------------------------------------------
# Realistic mode: saturation lifts latency / error vs independent
# ------------------------------------------------------------------
def test_realistic_llm_latency_mean_elevated_vs_independent(
    realistic_one_day_llm, independent_one_day_llm,
):
    """Saturation feedback must lift ``avg_llm_latency_ms`` above the
    independent-mode mean. ``p95_llm_latency_ms`` is a supplemental
    metric (zone 2 of llm_analytics' catalog) so it isn't emitted at
    the default ``--metrics-per-component``; covered separately by
    ``test_realistic_llm_supplemental_latency_lifted``. The expected
    lift on the default-emitted ``avg_llm_latency_ms`` is
    ``base * latency_gain * mean(logistic)``. With apigateway at
    utilization ≈ 1.05 at its natural baseline and oscillating through
    the day, ``mean(logistic)`` lands around 0.4–0.5, so the lift is
    roughly 850 * 0.55 * 0.4–0.5 ≈ 190–230 ms — comfortably above the
    5 ms noise floor used as the test threshold.
    """
    metric = "avg_llm_latency_ms"
    indep_vals, indep_ts = _column_values(
        independent_one_day_llm.out_dir, "llm_analytics", metric
    )
    real_vals, real_ts = _column_values(
        realistic_one_day_llm.out_dir, "llm_analytics", metric
    )
    # Align on the common timestamps so anomaly exclusion is consistent.
    common = sorted(set(indep_ts) & set(real_ts))
    indep_lookup = dict(zip(indep_ts, indep_vals))
    real_lookup = dict(zip(real_ts, real_vals))
    indep_arr = np.array([indep_lookup[t] for t in common], dtype=np.float64)
    real_arr = np.array([real_lookup[t] for t in common], dtype=np.float64)
    (indep_x, real_x) = _exclude_anomaly_rows(common, indep_arr, real_arr)
    indep_mean = float(np.mean(indep_x))
    real_mean = float(np.mean(real_x))
    # apigateway sits at utilization ≈ 1.05 at its natural baseline
    # (base 800 RPS / midpoint 760 RPS) and the daily envelope swings
    # around that, so ``mean(logistic)`` lands around 0.4–0.5 — the
    # analytical lift is ``base * latency_gain * mean(logistic)`` ≈
    # 850 * 0.55 * 0.4–0.5 ≈ 190–230 ms. We accept anything above 5 ms
    # to leave headroom for noise jitter and the same-day anomaly rows
    # that aren't fully scrubbed by the exclusion windows.
    assert real_mean - indep_mean > 5.0, (
        f"realistic-mode llm_analytics.{metric} mean={real_mean:.2f} "
        f"not elevated above independent-mode mean={indep_mean:.2f}; "
        f"saturation feedback looks inert"
    )


def test_realistic_llm_api_error_rate_mean_elevated_vs_independent(
    realistic_one_day_llm, independent_one_day_llm,
):
    """Saturation adds a positive error offset proportional to the
    logistic, so the llm_api_error_rate mean must lift above the
    independent baseline."""
    indep_vals, indep_ts = _column_values(
        independent_one_day_llm.out_dir, "llm_analytics", "llm_api_error_rate"
    )
    real_vals, real_ts = _column_values(
        realistic_one_day_llm.out_dir, "llm_analytics", "llm_api_error_rate"
    )
    common = sorted(set(indep_ts) & set(real_ts))
    indep_lookup = dict(zip(indep_ts, indep_vals))
    real_lookup = dict(zip(real_ts, real_vals))
    indep_arr = np.array([indep_lookup[t] for t in common], dtype=np.float64)
    real_arr = np.array([real_lookup[t] for t in common], dtype=np.float64)
    (indep_x, real_x) = _exclude_anomaly_rows(common, indep_arr, real_arr)
    indep_mean = float(np.mean(indep_x))
    real_mean = float(np.mean(real_x))
    # error_gain = 0.015; with logistic mean around 0.4–0.5 on the
    # default day (see avg_llm_latency_ms test for the derivation) the
    # absolute lift is ~0.006–0.0075. Allow 1/10th of the error_gain
    # (well below the analytical expectation) as the lift floor.
    floor = 0.0015
    assert real_mean - indep_mean > floor, (
        f"realistic-mode llm_analytics.llm_api_error_rate "
        f"mean={real_mean:.5f} not elevated above independent-mode "
        f"mean={indep_mean:.5f}; saturation error offset looks inert"
    )


# ------------------------------------------------------------------
# Realistic mode: caps on saturated columns
# ------------------------------------------------------------------
def test_realistic_llm_latency_never_negative(realistic_one_day_llm):
    """Phase 4 acceptance carries through: latency multiplier is always
    >= 1, so the column must stay non-negative under realistic-mode
    saturation. ``avg_llm_latency_ms`` is the default-emitted target;
    ``p95_llm_latency_ms`` is covered by the supplemental-metrics
    fixture test below.
    """
    vals, _ = _column_values(
        realistic_one_day_llm.out_dir, "llm_analytics", "avg_llm_latency_ms"
    )
    assert vals.min() >= 0.0, (
        f"llm_analytics.avg_llm_latency_ms min={vals.min():.6f} went "
        f"negative under realistic saturation"
    )


# ------------------------------------------------------------------
# Supplemental-zone p95 metric: covered via --metrics-per-component 10
# ------------------------------------------------------------------
@pytest.fixture(scope="module")
def realistic_full_metrics_one_day_llm(amc, tmp_path_factory):
    """Realistic 1-day run with --metrics-per-component 10 so the
    supplemental ``p95_llm_latency_ms`` column is emitted and the
    saturation composition is observable on it."""
    out = tmp_path_factory.mktemp("phase5_realistic_full")
    return run_capture(
        amc, out, days=1,
        extra_args=[
            "--topology-mode", "realistic",
            "--metrics-per-component", "10",
        ],
    )


@pytest.fixture(scope="module")
def independent_full_metrics_one_day_llm(amc, tmp_path_factory):
    out = tmp_path_factory.mktemp("phase5_independent_full")
    return run_capture(
        amc, out, days=1,
        extra_args=[
            "--topology-mode", "independent",
            "--metrics-per-component", "10",
        ],
    )


def test_realistic_llm_supplemental_p95_latency_lifted(
    realistic_full_metrics_one_day_llm,
    independent_full_metrics_one_day_llm,
):
    """``p95_llm_latency_ms`` sits in llm_analytics' supplemental zone
    (index 8) so it only emits under ``--metrics-per-component 10``.
    Saturation feedback must lift its mean above the independent-mode
    baseline."""
    indep_vals, _ = _column_values(
        independent_full_metrics_one_day_llm.out_dir,
        "llm_analytics", "p95_llm_latency_ms",
    )
    real_vals, _ = _column_values(
        realistic_full_metrics_one_day_llm.out_dir,
        "llm_analytics", "p95_llm_latency_ms",
    )
    lift = float(np.mean(real_vals) - np.mean(indep_vals))
    assert lift > 5.0, (
        f"realistic-mode llm_analytics.p95_llm_latency_ms mean lift "
        f"({lift:.2f} ms) below 5 ms floor; saturation looks inert on "
        f"the supplemental p95 metric"
    )


def test_realistic_llm_supplemental_p95_latency_never_negative(
    realistic_full_metrics_one_day_llm,
):
    """Cap check on the supplemental p95 metric."""
    vals, _ = _column_values(
        realistic_full_metrics_one_day_llm.out_dir,
        "llm_analytics", "p95_llm_latency_ms",
    )
    assert vals.min() >= 0.0, (
        f"llm_analytics.p95_llm_latency_ms min={vals.min():.6f} went "
        f"negative under realistic saturation"
    )


def test_realistic_llm_api_error_rate_never_above_one(realistic_one_day_llm):
    """Phase 4 acceptance carries through: saturation never drives
    error rates above 1.0. The natural llm_api_error_rate base is ~0.05
    and ``error_gain`` is at most 0.02, so a saturated column lives
    comfortably inside [0, 0.1] — except where llm_analytics scenarios
    inject anomalies that intentionally push it higher. We therefore
    cap at 1.0 (the schema bound), not at 0.1.
    """
    vals, _ = _column_values(
        realistic_one_day_llm.out_dir, "llm_analytics", "llm_api_error_rate"
    )
    assert vals.max() <= 1.0, (
        f"llm_analytics.llm_api_error_rate max={vals.max():.6f} exceeded "
        f"1.0 under realistic saturation"
    )


# ------------------------------------------------------------------
# Scenario sanity: LLM scenarios still fire in realistic mode
# ------------------------------------------------------------------
def test_llm_scenarios_still_fire_in_realistic_mode(
    realistic_one_day_llm, amc,
):
    """Issue acceptance: 'LLM-specific scenarios in the catalog still
    fire (no shifting needed yet — phase 9 handles catalog re-tuning).'

    Check that the manifest contains every scenario id that touches
    ``llm_analytics`` in the SCENARIOS catalog at default
    --signal-level medium / --duration-days 1, so phase-5 coupling
    didn't inadvertently override the anomaly cells.

    On the default 1-day medium catalog today there are no scenarios
    whose *primary* specs land on ``llm_analytics`` — the only LLM
    activity in that slice is the ``vectorstore_pressure`` cascades
    on ``avg_llm_latency_ms`` / ``llm_api_error_rate``. The check
    therefore considers both primary and cascade specs and asserts
    the expected set is non-empty so it cannot pass vacuously.
    """
    import csv
    expected_scenario_ids: set[str] = set()
    for scen_id, scen in amc.SCENARIOS.items():
        if scen.days_required > 1:
            continue
        if scen.severity == "high":
            continue
        for comp, _spec in (*scen.primary_specs, *scen.cascade_specs):
            if comp == "llm_analytics":
                expected_scenario_ids.add(scen_id)
                break
    assert expected_scenario_ids, (
        "default 1-day medium catalog has no llm_analytics-touching "
        "scenarios; this test would pass vacuously — update the filter "
        "or pick a different acceptance signal"
    )
    with open(realistic_one_day_llm.out_dir / "anomalies.csv") as f:
        reader = csv.DictReader(f)
        seen_ids = {row["scenario_id"] for row in reader if row["scenario_id"]}
    missing = expected_scenario_ids - seen_ids
    assert not missing, (
        f"realistic-mode 1-day run missed LLM scenarios: {sorted(missing)}; "
        f"phase-5 coupling may be overriding scenario primary cells"
    )
