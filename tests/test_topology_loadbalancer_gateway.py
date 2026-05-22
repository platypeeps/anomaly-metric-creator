"""VER-152 phase 2 / VER-156 phase 6 flag day: --topology-mode coupling.

These tests cover:

* The ``--topology-mode`` CLI flag (default ``realistic`` after the
  VER-156 phase 6 flag day; ``independent`` retained as a deprecation
  alias that emits a stderr ``DeprecationWarning``).
* Byte-identical default output: explicit ``--topology-mode realistic``
  must produce the same per-component CSVs as the no-flag default.
* Legacy regression: ``--topology-mode independent`` must reproduce the
  pre-flag-day baseline byte-for-byte so the deprecated alias keeps
  working until its post-phase-9 removal.
* Realistic coupling: makes ``apigateway.requests_per_sec`` track
  ``loadbalancer.requests_per_sec`` with Pearson correlation >= 0.95
  (edge weight = 1.0 + small noise).
* Anomaly preservation: scenario primaries that target
  ``apigateway.requests_per_sec`` still land in the coupled column and
  in ``anomalies.csv``.
* DST guard + gauges-file mutual exclusion: the existing guardrails
  still fire in realistic mode.
"""
from __future__ import annotations

import hashlib

import numpy as np
import pytest

from conftest import read_component_rows, read_manifest, run_capture
from test_scenarios import LEGACY_INDEPENDENT_ONE_DAY_HASHES


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
    return np.array([float(rows[ts][idx]) for ts in ts_sorted], dtype=np.float64), ts_sorted


# ------------------------------------------------------------------
# CLI parsing
# ------------------------------------------------------------------
def test_topology_mode_default_is_realistic(amc):
    args = amc.parse_args(["--output-dir", "test_out"])
    assert args.topology_mode == "realistic"


def test_topology_mode_accepts_realistic(amc):
    args = amc.parse_args([
        "--topology-mode", "realistic",
        "--output-dir", "test_out",
    ])
    assert args.topology_mode == "realistic"


def test_topology_mode_accepts_explicit_independent(amc):
    args = amc.parse_args([
        "--topology-mode", "independent",
        "--output-dir", "test_out",
    ])
    assert args.topology_mode == "independent"


@pytest.mark.parametrize("bad_value", ["", "REALISTIC", "realisitc", "auto", "none"])
def test_topology_mode_rejects_invalid_value(amc, bad_value):
    with pytest.raises(SystemExit):
        amc.parse_args([
            "--topology-mode", bad_value,
            "--output-dir", "test_out",
        ])


def test_topology_mode_independent_emits_deprecation_warning(amc, capsys):
    """``--topology-mode independent`` must print a stderr DeprecationWarning
    so callers see they are on a deprecated path scheduled for removal
    after VER-141 phase 9. The warning fires inside ``parse_args`` so the
    capsys hook below catches it without a full generation run."""
    amc.parse_args([
        "--topology-mode", "independent",
        "--output-dir", "test_out",
    ])
    cap = capsys.readouterr()
    assert "DeprecationWarning" in cap.err
    assert "--topology-mode independent" in cap.err


def test_topology_mode_realistic_no_deprecation_warning(amc, capsys):
    """The default realistic mode must not emit any deprecation warning."""
    amc.parse_args([
        "--topology-mode", "realistic",
        "--output-dir", "test_out",
    ])
    cap = capsys.readouterr()
    assert "DeprecationWarning" not in cap.err


def test_topology_mode_default_no_deprecation_warning(amc, capsys):
    """Default invocation (no flag) must not emit any deprecation warning —
    the user is on the new default, not the deprecated alias."""
    amc.parse_args(["--output-dir", "test_out"])
    cap = capsys.readouterr()
    assert "DeprecationWarning" not in cap.err


# ------------------------------------------------------------------
# Default-equivalence: explicit --topology-mode realistic matches the
# no-flag default byte-for-byte for apigateway, loadbalancer, and
# the anomalies manifest.
# ------------------------------------------------------------------
def test_topology_mode_realistic_matches_default_byte_for_byte(
    amc, one_day_run_a, tmp_path
):
    explicit = run_capture(
        amc, tmp_path / "explicit_realistic", days=1,
        interval_seconds=None,  # match one_day_run_a's 1s default for byte identity
        extra_args=["--topology-mode", "realistic"],
    )
    for filename in ("apigateway.csv", "loadbalancer.csv", "anomalies.csv"):
        default_hash = _sha256_path(one_day_run_a.out_dir / filename)
        explicit_hash = _sha256_path(explicit.out_dir / filename)
        assert default_hash == explicit_hash, (
            f"{filename} drifted between default run and explicit "
            f"--topology-mode realistic run"
        )


# ------------------------------------------------------------------
# Legacy regression: ``--topology-mode independent`` must reproduce the
# pre-flag-day baseline byte-for-byte. This guards the deprecated alias
# from silent drift before it is removed after VER-141 phase 9.
# ------------------------------------------------------------------
def test_topology_mode_independent_matches_legacy_baseline_byte_for_byte(
    amc, tmp_path
):
    explicit = run_capture(
        amc, tmp_path / "explicit_independent", days=1,
        interval_seconds=None,  # locked hashes pinned at 1s resolution
        extra_args=["--topology-mode", "independent"],
    )
    for filename, expected_hash in sorted(LEGACY_INDEPENDENT_ONE_DAY_HASHES.items()):
        actual = _sha256_path(explicit.out_dir / filename)
        assert actual == expected_hash, (
            f"{filename} drifted from the pre-flag-day independent baseline "
            f"under --topology-mode independent. expected={expected_hash} "
            f"actual={actual}. The deprecated alias must remain byte-for-byte "
            f"identical to its pre-VER-156 output until VER-141 phase 9 "
            f"removes it."
        )


# ------------------------------------------------------------------
# Realistic mode: apigateway.requests_per_sec tracks loadbalancer.requests_per_sec
# ------------------------------------------------------------------
def test_topology_mode_realistic_apigateway_tracks_loadbalancer(amc, tmp_path):
    # 1-day run with no anomalies so the correlation reflects only the
    # baseline coupling. ``--scenarios`` requires a real slug — exclude
    # everything by picking a deep-future scenario via --exclude-scenarios is
    # awkward, so we keep the default scenario set and slice the column to a
    # quiet window. Anomalies on requests_per_sec are infrequent enough that
    # default 1-day correlation stays above 0.95, but to be robust we exclude
    # the api_cpu_saturation retry-storm window (19:00-19:08) below.
    result = run_capture(
        amc, tmp_path / "realistic", days=1,
        interval_seconds=None,  # Pearson >= 0.95 requires 86 400 rows at 1s
        extra_args=["--topology-mode", "realistic"],
    )
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

    # Exclude the api_cpu_saturation retry-storm window (19:00:00..19:08:00)
    # so the anomaly span doesn't dominate the correlation. START is
    # ``2026-03-10 00:00:00`` (see amc.START).
    storm_start = "2026-03-10 19:00:00"
    storm_end = "2026-03-10 19:08:00"
    keep = [i for i, t in enumerate(common) if not (storm_start <= t <= storm_end)]
    lb_arr = lb_arr[keep]
    api_arr = api_arr[keep]
    corr = float(np.corrcoef(lb_arr, api_arr)[0, 1])
    assert corr >= 0.95, (
        f"realistic mode Pearson correlation {corr:.4f} below 0.95 threshold; "
        f"apigateway.requests_per_sec should track loadbalancer.requests_per_sec"
    )


def test_topology_mode_realistic_independent_correlation_is_low(amc, tmp_path):
    """Sanity check: in ``independent`` mode the correlation is well below 0.95.

    Pins the contrast so a regression that silently keeps coupling on under
    ``--topology-mode independent`` would be caught here (and not silently
    rescued by an already-high baseline correlation).
    """
    result = run_capture(
        amc, tmp_path / "independent", days=1,
        extra_args=["--topology-mode", "independent"],
    )
    lb_vals, lb_ts = _column_values(result.out_dir, "loadbalancer", "requests_per_sec")
    api_vals, api_ts = _column_values(result.out_dir, "apigateway", "requests_per_sec")
    common = sorted(set(lb_ts) & set(api_ts))
    lb_lookup = dict(zip(lb_ts, lb_vals))
    api_lookup = dict(zip(api_ts, api_vals))
    lb_arr = np.array([lb_lookup[t] for t in common], dtype=np.float64)
    api_arr = np.array([api_lookup[t] for t in common], dtype=np.float64)
    # Exclude the api_cpu_saturation retry-storm window so the anomaly
    # doesn't artificially inflate the independent-mode correlation.
    storm_start = "2026-03-10 19:00:00"
    storm_end = "2026-03-10 19:08:00"
    keep = [i for i, t in enumerate(common) if not (storm_start <= t <= storm_end)]
    lb_arr = lb_arr[keep]
    api_arr = api_arr[keep]
    corr = float(np.corrcoef(lb_arr, api_arr)[0, 1])
    assert corr < 0.5, (
        f"independent mode Pearson correlation {corr:.4f} is unexpectedly high; "
        f"the two columns should be independent Gaussians around different means"
    )


# ------------------------------------------------------------------
# Anomaly preservation in realistic mode
# ------------------------------------------------------------------
def test_topology_mode_realistic_preserves_apigateway_anomalies(amc, tmp_path):
    """``api_cpu_saturation`` injects a sustained 2x retry storm on
    apigateway.requests_per_sec at 19:00 for 8 minutes. With realistic
    coupling enabled, the anomaly override must still apply on top of the
    coupled baseline — i.e. the manifest still records the spec and the
    CSV cells inside the span are at or near the anomaly value (1600).
    """
    result = run_capture(
        amc, tmp_path / "realistic_anoms", days=1,
        extra_args=[
            "--topology-mode", "realistic",
            "--scenarios", "api_cpu_saturation",
        ],
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
        "under --topology-mode realistic"
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
def test_topology_mode_realistic_with_dst_artifact_day_runs(amc, tmp_path):
    """DST splice path is inside ``generate_component`` and operates on the
    formatted output rows after natural + anomaly + derivation. Realistic
    coupling only changes the natural baseline, so the DST splice should
    still produce the duplicate 02:00-02:59 hour without errors.
    """
    result = run_capture(
        amc, tmp_path / "realistic_dst", days=1,
        extra_args=[
            "--topology-mode", "realistic",
            "--inject-dst-artifact-day", "1",
        ],
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


def test_topology_mode_realistic_rejects_dst_with_gauges_emit(amc, tmp_path, monkeypatch):
    """``--inject-dst-artifact-day`` is mutually exclusive with the gauges
    file emission. The check is in parse_args and independent of
    --topology-mode, but exercise the combination explicitly so the guard
    doesn't silently regress under realistic coupling.
    """
    with pytest.raises(SystemExit):
        amc.parse_args([
            "--topology-mode", "realistic",
            "--inject-dst-artifact-day", "1",
            "--emit-selection", "metrics,gauges",
            "--output-dir", str(tmp_path / "rejected"),
        ])


def test_topology_mode_realistic_loadbalancer_root_uncoupled(amc, tmp_path):
    """``loadbalancer`` has no incoming TOPOLOGY edges, so even in
    ``--topology-mode realistic`` its baseline must come from its own
    natural-Gaussian spec (base ~900, std ~60), not from any other
    component. Realistic mode reorders the global generation pass — which
    shifts every component's shared-RNG draw sequence — so we can't pin
    the loadbalancer CSV bytes; pin the column's central tendency
    instead. Anything materially off ~900 would indicate accidental
    upstream coupling on a root.
    """
    realistic = run_capture(
        amc, tmp_path / "realistic_lb_root", days=1,
        extra_args=["--topology-mode", "realistic"],
    )
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
