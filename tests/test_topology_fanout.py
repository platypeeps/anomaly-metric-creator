"""Phase 3: Topology fan-out edges.

Extends the loadbalancer -> apigateway coupling with the rest of
the front-half graph:

* ``apigateway -> authservice`` (constant weight 0.3, couples authservice
  ``login_attempts``).
* ``apigateway -> cacheservice`` (constant weight 0.4, couples both
  ``cache_hits`` and ``cache_misses`` so cache ops/s tracks gateway RPS).
* ``apigateway -> database`` (constant weight 0.3, couples database
  ``queries_per_sec``).
* ``cacheservice -> database`` (callable weight: per-row miss ratio scaled
  by the database's natural ``queries_per_sec`` baseline; additive on
  top of the constant-weight contribution from apigateway).

These tests cover:

* Default (``--topology-mode realistic`` since phase 6
  flag day) byte-identical with an explicit ``--topology-mode
  realistic`` run, so the no-flag default and the explicit alias stay
  in lockstep on every coupled downstream CSV. The deprecated
  ``--topology-mode independent`` alias's current no-topology baseline
  is pinned in
  ``tests/test_topology_loadbalancer_gateway.py`` against
  ``LEGACY_INDEPENDENT_ONE_DAY_HASHES``, not here.
* Realistic-mode correlations between downstream load metrics and
  apigateway ``requests_per_sec`` (Pearson >= 0.9), and the cache
  miss-rate edge (Pearson >= 0.7 against ``miss_rate * gateway_rps``).
* Topology generation order: cacheservice always runs before database
  under ``--topology-mode realistic`` so the miss-ratio columns are
  available when the database baseline is composed.
* Synthetic cycle rejection by ``_validate_topology()`` (TOPOLOGY itself
  stays acyclic in v1).
* Anomaly preservation on downstream coupled metrics.
"""
from __future__ import annotations


import numpy as np
import pytest

from conftest import (
    read_component_rows,
    read_manifest,
    registry_overlay,
    run_capture,
    sha256_path,
)




def _column_values(out_dir, component, metric):
    rows, header = read_component_rows(out_dir, component)
    idx = header.index(metric)
    ts_sorted = sorted(rows.keys())
    return (
        np.array([float(rows[ts][idx]) for ts in ts_sorted], dtype=np.float64),
        ts_sorted,
    )


# Anomaly windows on coupled downstream metrics that must be excluded from
# the correlation slice so the override values do not dominate the Pearson
# coefficient. START is ``2026-03-10 00:00:00``.
_EXCLUSION_WINDOWS = [
    # auth_brute_force on authservice.login_attempts/error_rate
    ("2026-03-10 02:15:00", "2026-03-10 02:30:00"),
    # cache_collapse primary: cache_misses collapse for 20 minutes.
    ("2026-03-10 06:00:00", "2026-03-10 06:20:00"),
    # monday_baseline on apigateway.requests_per_sec and authservice.login_attempts
    ("2026-03-10 09:00:00", "2026-03-10 10:00:00"),
    # api_cpu_saturation retry storm on apigateway.requests_per_sec
    ("2026-03-10 19:00:00", "2026-03-10 19:08:00"),
    # db_stall nightly batch kickoff on database.queries_per_sec
    ("2026-03-10 23:00:00", "2026-03-10 23:20:00"),
]


def _exclude_anomaly_rows(ts_list, *arrays):
    keep = [
        i for i, t in enumerate(ts_list)
        if not any(start <= t <= end for start, end in _EXCLUSION_WINDOWS)
    ]
    return tuple(arr[keep] for arr in arrays)


def _aligned_columns(out_dir, *pairs):
    """Read multiple ``(component, metric)`` columns aligned on the common
    timestamp intersection. Returns ``(ts_common, [arr, ...])``."""
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


# ------------------------------------------------------------------
# Default realistic-mode byte equivalence: explicit --topology-mode
# realistic must match the no-flag default byte-for-byte after the
# phase 6 flag day. Pre-flag-day this was checked the other way
# round (independent matched default); under realistic-mode default the
# parity check moves to the realistic alias and the legacy-baseline
# check lives in ``test_topology_loadbalancer_gateway``.
# ------------------------------------------------------------------
@pytest.mark.full_resolution
def test_topology_fanout_realistic_matches_default_byte_for_byte(
    amc, one_day_run_a, tmp_path
):
    """Explicit ``--topology-mode realistic`` matches the session
    ``one_day_run_a`` byte-for-byte across every coupled downstream
    CSV. Locks phase 6 to the invariant that realistic mode
    is the default path and explicitly passing the flag is a no-op."""
    explicit = run_capture(
        amc, tmp_path / "explicit_realistic", days=1,
        interval_seconds=1.0,  # match one_day_run_a's 1s cadence for byte identity
        extra_args=["--topology-mode", "realistic"],
    )
    for filename in (
        "loadbalancer.csv", "apigateway.csv", "authservice.csv",
        "cacheservice.csv", "database.csv", "anomalies.csv",
    ):
        default_hash = sha256_path(one_day_run_a.out_dir / filename)
        explicit_hash = sha256_path(explicit.out_dir / filename)
        assert default_hash == explicit_hash, (
            f"{filename} drifted between default run and "
            f"--topology-mode realistic run"
        )


# ------------------------------------------------------------------
# Generation order: cacheservice before database in realistic mode.
# ------------------------------------------------------------------
def test_topology_generation_order_cacheservice_before_database(amc):
    """The cacheservice -> database callable edge requires cacheservice
    columns to be captured before the database baseline is composed.
    ``_topology_generation_order`` must put cacheservice ahead of
    database in any active-component subset that includes both."""
    order = amc._topology_generation_order(set(amc.COMPONENTS.keys()))
    cs_idx = order.index("cacheservice")
    db_idx = order.index("database")
    assert cs_idx < db_idx, (
        f"cacheservice must precede database in topology order; "
        f"got order={order}"
    )


def test_topology_generation_order_apigateway_before_fanout(amc):
    """All three apigateway fan-out targets (authservice, cacheservice,
    database) must come after apigateway."""
    order = amc._topology_generation_order(set(amc.COMPONENTS.keys()))
    api_idx = order.index("apigateway")
    for downstream in ("authservice", "cacheservice", "database"):
        assert order.index(downstream) > api_idx, (
            f"{downstream} must come after apigateway in topology order; "
            f"got order={order}"
        )


# ------------------------------------------------------------------
# Realistic mode: downstream load metrics track apigateway RPS.
#
# These tests consume the session-scoped ``one_day_run_a`` (default
# no-flag run): realistic mode is the argparse default since the
# phase 6 flag day, so an explicit ``--topology-mode realistic``
# module fixture would regenerate a byte-identical 86,400-row dataset
# (the PR #63 duplicate-fixture antipattern). The byte-identity of the
# explicit flag vs. the default is pinned separately by
# ``test_topology_fanout_realistic_matches_default_byte_for_byte`` above.
# ------------------------------------------------------------------


def test_realistic_authservice_login_attempts_tracks_apigateway(
    one_day_run_a, amc
):
    common, (api, auth) = _aligned_columns(
        one_day_run_a.out_dir,
        ("apigateway", "requests_per_sec"),
        ("authservice", "login_attempts"),
    )
    assert len(common) > 1000, (
        f"too few common rows for correlation: {len(common)}"
    )
    api_x, auth_x = _exclude_anomaly_rows(common, api, auth)
    corr = float(np.corrcoef(api_x, auth_x)[0, 1])
    assert corr >= 0.9, (
        f"realistic-mode Pearson(apigateway.rps, authservice.login_attempts)"
        f"={corr:.4f}; expected >= 0.9"
    )


def test_realistic_cacheservice_ops_tracks_apigateway(
    one_day_run_a, amc
):
    common, (api, hits, misses) = _aligned_columns(
        one_day_run_a.out_dir,
        ("apigateway", "requests_per_sec"),
        ("cacheservice", "cache_hits"),
        ("cacheservice", "cache_misses"),
    )
    ops = hits + misses
    api_x, ops_x = _exclude_anomaly_rows(common, api, ops)
    corr = float(np.corrcoef(api_x, ops_x)[0, 1])
    assert corr >= 0.9, (
        f"realistic-mode Pearson(apigateway.rps, cacheservice.ops_per_sec)"
        f"={corr:.4f}; expected >= 0.9 (ops/s = cache_hits + cache_misses)"
    )


def test_realistic_database_qps_tracks_apigateway(
    one_day_run_a, amc
):
    common, (api, db_qps) = _aligned_columns(
        one_day_run_a.out_dir,
        ("apigateway", "requests_per_sec"),
        ("database", "queries_per_sec"),
    )
    api_x, db_x = _exclude_anomaly_rows(common, api, db_qps)
    corr = float(np.corrcoef(api_x, db_x)[0, 1])
    assert corr >= 0.9, (
        f"realistic-mode Pearson(apigateway.rps, database.queries_per_sec)"
        f"={corr:.4f}; expected >= 0.9"
    )


def test_realistic_database_tracks_cache_miss_load(
    one_day_run_a, amc
):
    """The cacheservice -> database callable edge should make database
    queries_per_sec correlate with ``miss_ratio * gateway_rps``. This is
    a softer correlation (>= 0.7) because the constant-weight
    apigateway -> database edge also contributes, but miss-rate driven
    load must be a non-trivial signal in the column."""
    common, (api, hits, misses, db_qps) = _aligned_columns(
        one_day_run_a.out_dir,
        ("apigateway", "requests_per_sec"),
        ("cacheservice", "cache_hits"),
        ("cacheservice", "cache_misses"),
        ("database", "queries_per_sec"),
    )
    total = hits + misses
    miss_ratio = np.divide(
        misses, total,
        out=np.zeros_like(misses, dtype=np.float64),
        where=total > 0,
    )
    expected = miss_ratio * api
    api_x, exp_x, db_x = _exclude_anomaly_rows(common, api, expected, db_qps)
    corr = float(np.corrcoef(exp_x, db_x)[0, 1])
    assert corr >= 0.7, (
        f"realistic-mode Pearson(miss_ratio*gateway_rps, database.qps)"
        f"={corr:.4f}; expected >= 0.7 (cacheservice -> database callable edge)"
    )


# ------------------------------------------------------------------
# Independent-mode contrast: low correlation for non-root downstreams.
# ------------------------------------------------------------------
def test_independent_mode_authservice_correlation_is_low(amc, tmp_path):
    """Sanity check: under ``independent`` mode the authservice
    login_attempts column is a Gaussian around its own base 250 and
    should not be tightly correlated with apigateway RPS."""
    result = run_capture(
        amc, tmp_path / "indep_auth", days=1,
        extra_args=["--topology-mode", "independent"],
    )
    common, (api, auth) = _aligned_columns(
        result.out_dir,
        ("apigateway", "requests_per_sec"),
        ("authservice", "login_attempts"),
    )
    api_x, auth_x = _exclude_anomaly_rows(common, api, auth)
    corr = float(np.corrcoef(api_x, auth_x)[0, 1])
    assert corr < 0.5, (
        f"independent-mode correlation {corr:.4f} unexpectedly high; "
        f"the two columns should be independent Gaussians"
    )


# ------------------------------------------------------------------
# Anomaly preservation on coupled downstream metrics.
# ------------------------------------------------------------------
def test_realistic_db_stall_qps_override_survives_coupling(amc, tmp_path):
    """``db_stall`` injects a 55,000 QPS spike on database at 23:00:00.
    With realistic coupling the override must still rewrite the cell on
    top of the coupled baseline."""
    result = run_capture(
        amc, tmp_path / "phase3_db_anoms", days=1,
        extra_args=[
            "--topology-mode", "realistic",
            "--scenarios", "db_stall",
        ],
    )
    manifest = read_manifest(result.out_dir)
    qps_rows = [
        row for row in manifest
        if row["component"] == "database"
        and row["metric"] == "queries_per_sec"
        and "Nightly batch" in row["description"]
    ]
    assert qps_rows, (
        "db_stall nightly batch entry missing from anomalies.csv "
        "under --topology-mode realistic"
    )

    db_vals, db_ts = _column_values(result.out_dir, "database", "queries_per_sec")
    spike_ts = "2026-03-10 23:00:00"
    matches = [v for t, v in zip(db_ts, db_vals) if t == spike_ts]
    # The override value is 55000; with --interval-seconds default of 1s
    # only one row should match exactly, but allow the row to be dropped
    # by --drop-rate (default 0 → never).
    assert matches, (
        f"no row at {spike_ts} for database.queries_per_sec; row may have "
        f"been dropped"
    )
    # Tight band around the 55,000 override: the anomaly path writes the
    # generator's value verbatim *replacing* the coupled baseline, so the
    # cell must land within rounding distance of 55000. A loose lower-only
    # bound would miss a regression where coupling additively layers on top.
    assert 54000.0 <= matches[0] <= 56000.0, (
        f"db_stall override at {spike_ts} = {matches[0]}; expected ~55000 "
        f"(coupling must replace, not stack on, the override value)"
    )


# ------------------------------------------------------------------
# Synthetic cycle rejection (TOPOLOGY itself is acyclic in v1).
#
# These tests monkeypatch ``TOPOLOGY`` but leave ``COMPONENTS`` alone, so
# every node name used below (``apigateway``, ``database``, ``cacheservice``)
# must remain a real ``COMPONENTS`` key — otherwise ``_validate_topology``
# raises the "source not in COMPONENTS" error *before* the cycle check
# runs and the ``match=r"cycle"`` assertion passes for the wrong reason.
# If those component names are ever renamed/removed, update the patched
# graphs here in lockstep.
# ------------------------------------------------------------------
def test_validate_topology_rejects_two_node_cycle(amc, monkeypatch):
    """A direct A -> B, B -> A cycle must be rejected at import-time."""
    Edge = amc.Edge
    patched = {
        "apigateway": [Edge(target="database", weight=1.0)],
        "database": [Edge(target="apigateway", weight=1.0)],
    }
    monkeypatch.setattr(amc, "TOPOLOGY", patched)
    with pytest.raises(ValueError, match=r"cycle"):
        amc._validate_topology()


def test_validate_topology_rejects_self_loop(amc, monkeypatch):
    """A self-loop A -> A counts as a cycle."""
    Edge = amc.Edge
    patched = {
        "database": [Edge(target="database", weight=1.0)],
    }
    monkeypatch.setattr(amc, "TOPOLOGY", patched)
    with pytest.raises(ValueError, match=r"cycle"):
        amc._validate_topology()


def test_validate_topology_rejects_three_node_cycle(amc, monkeypatch):
    """An indirect A -> B -> C -> A cycle must be rejected."""
    Edge = amc.Edge
    patched = {
        "apigateway": [Edge(target="database", weight=1.0)],
        "database": [Edge(target="cacheservice", weight=1.0)],
        "cacheservice": [Edge(target="apigateway", weight=1.0)],
    }
    monkeypatch.setattr(amc, "TOPOLOGY", patched)
    with pytest.raises(ValueError, match=r"cycle"):
        amc._validate_topology()


def test_validate_topology_accepts_current_acyclic_graph(amc):
    """Sanity check: the shipped TOPOLOGY is acyclic and validates."""
    amc._validate_topology()  # must not raise


# ------------------------------------------------------------------
# Roots remain uncoupled under realistic mode.
# ------------------------------------------------------------------
def test_realistic_apigateway_central_tendency_preserved(
    one_day_run_a, amc
):
    """apigateway.requests_per_sec under realistic coupling should still
    average near its natural baseline (~800). The Phase-3 formula
    preserves the downstream's natural baseline; a regression that
    over-amplifies (or under-amplifies) the coupling would shift the
    mean materially."""
    api_vals, api_ts = _column_values(
        one_day_run_a.out_dir, "apigateway", "requests_per_sec"
    )
    keep = [
        i for i, t in enumerate(api_ts)
        if not any(start <= t <= end for start, end in _EXCLUSION_WINDOWS)
    ]
    api_clean = api_vals[keep]
    mean = float(np.mean(api_clean))
    # apigateway.requests_per_sec natural base = 800. Allow ±15% drift
    # for the Gaussian noise and the daily-sine envelope contribution.
    assert 680.0 <= mean <= 920.0, (
        f"apigateway.requests_per_sec mean={mean:.2f} drifted significantly "
        f"from the natural baseline (800); coupling scaling looks wrong"
    )


def test_callable_contribution_applies_to_canonical_metric_only(amc):
    """A callable-weight edge's contribution is in the downstream's
    *canonical*-metric units (the weight callable bakes that scaling),
    so only the canonical load metric may receive it; a supplementary
    coupled metric with a different base must stay on its natural
    baseline when no constant-weight edge is active.

    Regression: the composer used to add the same canonical-unit array
    to every coupled metric (inert today only because no callable-edge
    target declares supplementary captures)."""
    n_rows = 200
    rng = np.random.RandomState(7)
    signal_col = np.linspace(0.0, 1.0, n_rows)

    with registry_overlay(
        amc,
        _TOPOLOGY_LOAD_METRICS={
            "synthup": ("upload", ()),
            "synthdown": ("main_qps", ("extra_ops",)),
        },
        TOPOLOGY={
            "synthup": [
                amc.Edge(
                    target="synthdown",
                    weight=lambda s: s * 500.0,
                    signal=lambda cols: cols.get("upload"),
                )
            ]
        },
    ):
        specs = [
            amc.MetricSpec(name="main_qps", base=100.0, std=1.0),
            amc.MetricSpec(name="extra_ops", base=5.0, std=0.5),
        ]
        out = amc._compose_topology_coupled_specs(
            "synthdown", specs,
            {"synthup": {"upload": signal_col}},
            rng, n_rows=n_rows,
        )
        # Canonical metric: coupled (spec rewritten to the baked baseline).
        assert out[0] is not specs[0], (
            "canonical load metric must receive the callable contribution"
        )
        # Supplementary metric: untouched by identity — the callable
        # array is in canonical units (peaks at 500, vs extra_ops base
        # 5.0) and must not replace its natural baseline.
        assert out[1] is specs[1], (
            "supplementary metric must not receive a canonical-unit "
            "callable contribution"
        )
