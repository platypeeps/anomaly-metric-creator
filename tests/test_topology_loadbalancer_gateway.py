"""Phase 2 / Phase 6 flag day: realistic topology coupling (loadbalancer -> apigateway).

Realistic topology coupling has been the default since the phase 6
flag day, and the phase-9 flag day removed the deprecated
``--topology-mode`` CLI flag entirely (the ``independent`` no-topology
alias no longer parses; ``tests/test_args.py`` pins the rejection).
The no-topology statistical contrast now comes from the
direct-natural session fixtures in ``tests/conftest.py``
(``natural_one_day_run``), which invoke ``generate_component``
directly with the raw specs.

These tests cover:

* Realistic coupling: makes ``apigateway.requests_per_sec`` track
  ``loadbalancer.requests_per_sec`` with Pearson correlation >= 0.95
  (edge weight = 1.0 + small noise).
* Direct-natural contrast: the uncoupled baseline correlation stays
  well below the realistic threshold.
* Anomaly preservation: scenario primaries that target
  ``apigateway.requests_per_sec`` still land in the coupled column and
  in ``anomalies.csv``.
* DST guard + gauges-file mutual exclusion: the existing guardrails
  still fire under the (default, and only) realistic coupling.
"""
from __future__ import annotations


import numpy as np
import pytest

from conftest import read_component_rows, read_manifest, run_capture




def _column_values(out_dir, component, metric):
    rows, header = read_component_rows(out_dir, component)
    idx = header.index(metric)
    ts_sorted = sorted(rows.keys())
    return np.array([float(rows[ts][idx]) for ts in ts_sorted], dtype=np.float64), ts_sorted


# ------------------------------------------------------------------
# Realistic mode: apigateway.requests_per_sec tracks loadbalancer.requests_per_sec
# ------------------------------------------------------------------
def test_realistic_apigateway_tracks_loadbalancer(amc, one_day_run_a):
    # Default 1-day session run (86,400 rows at 1s; Pearson >= 0.95
    # needs the full-resolution sweep). The default scenario set fires,
    # so we slice the column to quiet windows: anomalies on
    # requests_per_sec are infrequent enough that the 1-day correlation
    # stays above 0.95, but to be robust we exclude the
    # api_cpu_saturation retry-storm window (19:00-19:08) below.
    result = one_day_run_a
    lb_vals, lb_ts = _column_values(result.out_dir, "loadbalancer", "requests_per_sec")
    api_vals, api_ts = _column_values(result.out_dir, "apigateway", "requests_per_sec")
    # The two components share the same timeline (same start, same interval,
    # same drop_rate seed input). Independent drop_mask draws can leave
    # mismatched row sets; intersect on the timestamp set so we compare
    # corresponding rows.
    common = sorted(set(lb_ts) & set(api_ts))
    assert len(common) > 1000, (
        f"too few common rows to compute correlation: {len(common)}"
    )
    lb_lookup = dict(zip(lb_ts, lb_vals))
    api_lookup = dict(zip(api_ts, api_vals))
    lb_arr = np.array([lb_lookup[t] for t in common], dtype=np.float64)
    api_arr = np.array([api_lookup[t] for t in common], dtype=np.float64)

    # Exclude apigateway.requests_per_sec anomaly windows so those spans do
    # not dominate the correlation. START is
    # ``2026-03-10 00:00:00`` (see amc.START).
    anomaly_windows = [
        ("2026-03-10 09:00:00", "2026-03-10 10:00:00"),
        ("2026-03-10 19:00:00", "2026-03-10 19:08:00"),
    ]
    keep = [
        i for i, t in enumerate(common)
        if not any(start <= t <= end for start, end in anomaly_windows)
    ]
    lb_arr = lb_arr[keep]
    api_arr = api_arr[keep]
    corr = float(np.corrcoef(lb_arr, api_arr)[0, 1])
    assert corr >= 0.95, (
        f"realistic mode Pearson correlation {corr:.4f} below 0.95 threshold; "
        f"apigateway.requests_per_sec should track loadbalancer.requests_per_sec"
    )


def test_natural_baseline_lb_gateway_correlation_is_low(natural_one_day_run):
    """Sanity check: on the direct-natural baseline (no topology; the
    independent alias was removed at the phase-9 flag day) the
    correlation is well below 0.95.

    Pins the contrast so the realistic-mode >= 0.95 assertion above
    cannot be silently rescued by an already-high baseline correlation.
    The baseline fixture fires no anomalies, so no window exclusion is
    needed here."""
    result = natural_one_day_run
    lb_vals, lb_ts = _column_values(result.out_dir, "loadbalancer", "requests_per_sec")
    api_vals, api_ts = _column_values(result.out_dir, "apigateway", "requests_per_sec")
    common = sorted(set(lb_ts) & set(api_ts))
    lb_lookup = dict(zip(lb_ts, lb_vals))
    api_lookup = dict(zip(api_ts, api_vals))
    lb_arr = np.array([lb_lookup[t] for t in common], dtype=np.float64)
    api_arr = np.array([api_lookup[t] for t in common], dtype=np.float64)
    corr = float(np.corrcoef(lb_arr, api_arr)[0, 1])
    assert corr < 0.5, (
        f"direct-natural baseline Pearson correlation {corr:.4f} is unexpectedly "
        f"high; the two columns should be uncoupled Gaussians around different means"
    )


# ------------------------------------------------------------------
# Anomaly preservation in realistic mode
# ------------------------------------------------------------------
def test_realistic_preserves_apigateway_anomalies(amc, tmp_path):
    """``api_cpu_saturation`` injects a sustained 2x retry storm on
    apigateway.requests_per_sec at 19:00 for 8 minutes. With realistic
    coupling enabled, the anomaly override must still apply on top of the
    coupled baseline — i.e. the manifest still records the spec and the
    CSV cells inside the span are at or near the anomaly value (1600).
    """
    result = run_capture(
        amc, tmp_path / "realistic_anoms", days=1,
        extra_args=["--scenarios", "api_cpu_saturation"],
    )
    manifest = read_manifest(result.out_dir)
    retry_rows = [
        row for row in manifest
        if row["component"] == "apigateway"
        and row["metric"] == "requests_per_sec"
        and "Retry storm" in row["description"]
    ]
    assert retry_rows, (
        "api_cpu_saturation retry-storm entry missing from anomalies.csv "
        "under realistic topology coupling"
    )

    api_vals, api_ts = _column_values(result.out_dir, "apigateway", "requests_per_sec")
    storm_start = "2026-03-10 19:00:00"
    storm_end_excl = "2026-03-10 19:08:00"
    storm_values = [
        v for t, v in zip(api_ts, api_vals)
        if storm_start <= t < storm_end_excl
    ]
    assert storm_values, "no rows inside the retry-storm window"
    # The anomaly is shape="sustained" with generator returning 1600.
    # ``sustained`` shape jitters around the midline; allow a wide band but
    # require values to be in the elevated regime, well above the coupled
    # baseline (~loadbalancer's ~900 RPS).
    assert min(storm_values) > 1200, (
        f"retry-storm override appears clamped down by realistic coupling: "
        f"min in-window value={min(storm_values):.2f}, expected >> 1200"
    )


# ------------------------------------------------------------------
# DST guard and gauges mutual-exclusion still hold under realistic
# ------------------------------------------------------------------
def test_realistic_with_dst_artifact_day_runs(amc, tmp_path):
    """DST splice path is inside ``generate_component`` and operates on the
    formatted output rows after natural + anomaly + derivation. Realistic
    coupling only changes the natural baseline, so the DST splice should
    still produce the duplicate 02:00-02:59 hour without errors.
    """
    result = run_capture(
        amc, tmp_path / "realistic_dst", days=1,
        extra_args=["--inject-dst-artifact-day", "1"],
    )
    # The duplicated hour shows up as repeated timestamps in apigateway.csv.
    # Count occurrences of one 02:xx timestamp to confirm splicing happened.
    with open(result.out_dir / "apigateway.csv") as f:
        lines = f.read().splitlines()
    twos = [ln for ln in lines if ln.startswith("2026-03-10 02:00:00,")]
    assert len(twos) == 2, (
        f"expected DST splice to duplicate 02:00:00 row, found {len(twos)} "
        f"occurrence(s) in apigateway.csv"
    )


def test_realistic_rejects_dst_with_gauges_emit(amc, tmp_path, monkeypatch):
    """``--inject-dst-artifact-day`` is mutually exclusive with the gauges
    file emission. The check is in parse_args and orthogonal to topology
    coupling, but exercise the combination explicitly so the guard
    doesn't silently regress under realistic coupling.
    """
    with pytest.raises(SystemExit):
        amc.parse_args([
            "--inject-dst-artifact-day", "1",
            "--emit", "metrics,gauges",
            "--output-dir", str(tmp_path / "rejected"),
        ])


def test_realistic_loadbalancer_root_uncoupled(amc, tmp_path):
    """``loadbalancer`` has no incoming TOPOLOGY edges, so even under
    realistic coupling its baseline must come from its own
    natural-Gaussian spec (base ~900, std ~60), not from any other
    component. Realistic mode orders the global generation pass
    topologically — which shifts every component's shared-RNG draw
    sequence — so we can't pin the loadbalancer CSV bytes; pin the
    column's central tendency instead. Anything materially off ~900
    would indicate accidental upstream coupling on a root.
    """
    realistic = run_capture(amc, tmp_path / "realistic_lb_root", days=1)
    lb_vals, _ = _column_values(realistic.out_dir, "loadbalancer", "requests_per_sec")
    mean = float(np.mean(lb_vals))
    # Tolerate the api_cpu_saturation cascade on loadbalancer (if any) plus
    # normal noise; the natural base is 900 and std is 60, so anything in
    # ~[880, 920] is comfortably the uncoupled baseline.
    assert 880.0 <= mean <= 920.0, (
        f"loadbalancer.requests_per_sec mean={mean:.2f} drifted from the "
        f"uncoupled base (~900); roots must not be re-shaped by topology "
        f"coupling"
    )
