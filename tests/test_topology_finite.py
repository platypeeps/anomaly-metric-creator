"""Regression: realistic-topology coupling/saturation math never emits
NaN/inf into a metric column (task 07-02-verify-topology-divzero).

The audit left this as a suspicion. The two-pass topology pipeline divides by
several magnitudes — the constant-edge weight sum (`w / sum_w`), each upstream
base (`ups_arr / ups_base`), and the saturation midpoint (`upstream_load /
sat.midpoint`). The shipped graph keeps every denominator positive and every
captured load finite, but a single non-finite cell would silently defeat
downstream validation (`np.std` -> NaN, every comparison False) and break the
byte-identical determinism contract. This locks the guarantee: the guards hold
on the reachable paths (unit tests below) and no generated cell is ever
non-finite under default, `--metrics-per-component`-trimmed, and narrowed
`--components` runs.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from conftest import read_component_rows, run_capture


# ------------------------------------------------------------------
# _apply_saturation: reject non-finite upstream load (residual hole 2).
# Generated captures are finite by construction, so this only fires for
# direct/programmatic callers — but a silent NaN/inf there would propagate
# through the logistic into a metric cell. Mirrors the existing
# reject-zero-midpoint / reject-negative-midpoint tests.
# ------------------------------------------------------------------

def _sat(amc):
    return amc.SaturationParams(
        midpoint=760.0, steepness=6.0, latency_gain=0.5, error_gain=0.01
    )


def test_apply_saturation_rejects_nan_upstream(amc):
    with pytest.raises(ValueError, match=r"finite"):
        amc._apply_saturation(np.array([1.0, np.nan, 3.0]), _sat(amc))


def test_apply_saturation_rejects_inf_upstream(amc):
    with pytest.raises(ValueError, match=r"finite"):
        amc._apply_saturation(np.array([1.0, np.inf]), _sat(amc))


def test_apply_saturation_finite_upstream_ok(amc):
    lat, err = amc._apply_saturation(
        np.array([0.0, 760.0, 5000.0]), _sat(amc)
    )
    assert np.all(np.isfinite(lat))
    assert np.all(np.isfinite(err))


# ------------------------------------------------------------------
# sum_w == 0 guard (residual hole 1): a constant-edge set whose weights sum
# to 0 must yield no coupling contribution, not a ZeroDivisionError. The
# shipped graph never does this, so drive it through a monkeypatched TOPOLOGY
# edge with weight 0.0 and confirm generation stays finite and does not raise.
# ------------------------------------------------------------------

def test_zero_weight_constant_edge_does_not_divide_by_zero(amc, tmp_path, monkeypatch):
    Edge = amc.Edge
    # cacheservice's only inbound constant edge is apigateway -> cacheservice
    # (weight 0.4). Replace it with a weight-0 edge: active_constant is
    # non-empty (apigateway load is captured) but sum_w == 0.
    patched = {
        src: [
            Edge(target=e.target, weight=0.0, saturation=e.saturation,
                 signal=e.signal, correlation_threshold=e.correlation_threshold)
            if (src == "apigateway" and e.target == "cacheservice")
            else e
            for e in edges
        ]
        for src, edges in amc.TOPOLOGY.items()
    }
    monkeypatch.setattr(amc, "TOPOLOGY", patched)

    out = tmp_path / "zerow"
    out.mkdir()
    # Must not raise ZeroDivisionError; cacheservice must stay finite.
    run_capture(amc, out, days=1, extra_args=["--components", "apigateway,cacheservice"])
    checked = _assert_component_csvs_finite(out, ["cacheservice"])
    assert checked > 0


# ------------------------------------------------------------------
# End-to-end finiteness under denominator-stress configs.
# ------------------------------------------------------------------

def _assert_component_csvs_finite(out_dir, components) -> int:
    """Assert every numeric metric cell in each component CSV is finite.
    Returns the number of cells checked so callers can guard against a
    vacuous pass."""
    checked = 0
    for component in components:
        rows, header = read_component_rows(out_dir, component)
        assert rows, f"{component}.csv has no data rows"
        # header[0] is 'timestamp'; the dimension-prefix columns (id/host/...)
        # are non-numeric, so skip anything that does not parse as a float.
        for row in rows.values():
            for cell in row[1:]:
                if cell == "":
                    continue
                try:
                    value = float(cell)
                except ValueError:
                    continue  # dimension string (id/host/pod/...)
                assert math.isfinite(value), (
                    f"{component}.csv non-finite cell: {cell!r}"
                )
                checked += 1
    return checked


def _all_components(amc):
    return list(amc.COMPONENTS)


def test_default_run_has_no_nonfinite_cells(amc, tmp_path):
    out = tmp_path / "default"
    out.mkdir()
    run_capture(amc, out, days=1)
    checked = _assert_component_csvs_finite(out, _all_components(amc))
    assert checked > 0


def test_metrics_per_component_trim_has_no_nonfinite_cells(amc, tmp_path):
    # Trimming to 3 metrics drops coupled upstream columns, exercising the
    # signal-returns-None / edge-skipped graceful-degradation branch.
    out = tmp_path / "trim"
    out.mkdir()
    run_capture(amc, out, days=1, extra_args=["--metrics-per-component", "3"])
    checked = _assert_component_csvs_finite(out, _all_components(amc))
    assert checked > 0


def test_narrowed_components_has_no_nonfinite_cells(amc, tmp_path):
    # A narrow --components subset leaves several downstreams with no captured
    # upstream (active_constant empty), the other degenerate-denominator path.
    out = tmp_path / "narrow"
    out.mkdir()
    run_capture(amc, out, days=1, extra_args=["--components", "database,apigateway"])
    checked = _assert_component_csvs_finite(out, ["database", "apigateway"])
    assert checked > 0
