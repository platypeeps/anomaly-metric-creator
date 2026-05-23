"""phase 8: per-instance topology integration.

Verifies that ``--topology-mode realistic`` under
``--instances-per-component N > 1`` (or a non-default ``--instance-config``)
runs the topology two-pass generation against each downstream instance's
*matching* upstream instance set, so a slow upstream pod produces
saturation feedback only on the corresponding downstream pod's rows
under 1:1 routing (matched cardinalities).

The synthetic ``synthetic_apigateway_pod0_load_spike`` scenario in this module
elevates ``apigateway.requests_per_sec`` on instance ``i0`` via
``instance_filter`` so the failure surfaces as per-pod upstream load
asymmetry. Under 1:1 routing the per-instance saturation arrays
diverge: ``authservice.pod-0``'s latency lifts visibly, while pods 1
and 2 stay close to the natural baseline. The check is statistical
(mean latency in the failure window) so it tolerates the natural
noise band.

Tests also pin:

- ``--instances-per-component 1`` (default) preserves byte parity
  with the no-flag run, locking the issue's
  "byte-identical to phase 6 baseline" acceptance.
- ``generate_component`` collapses to the shared-arrays fast path
  under symmetric upstream (no upstream filter), so locked
  ``N3_ONE_DAY_HASHES`` continue to hold.
- ``_matched_cardinality`` returns the 1:1 vs uniform-fan-out
  dispatch the per-instance composer reads.
"""
from __future__ import annotations

import csv
import hashlib

import numpy as np
import pytest

from conftest import run_capture


def _sha256_path(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


# Time the synthetic scenario fires. 03:30 keeps it away from the
# busiest scenario timestamps in the default catalog so the
# correlation slice isn't polluted.
SPIKE_TIME_OFFSET = 3 * 3600 + 30 * 60  # 03:30:00 from START
SPIKE_DURATION_S = 30 * 60  # 30 minutes
SPIKE_VALUE = 1400.0  # ~7σ above the natural ~800 ± 50 baseline


@pytest.fixture(scope="module")
def synthetic_n3_run(amc, tmp_path_factory):
    """N=3 1-day run with a synthetic primary anomaly on
    ``apigateway.requests_per_sec`` filtered to instance ``i0`` only.

    Drives the per-instance saturation path: under 1:1 routing
    (matched apigateway/authservice cardinalities) authservice
    pod-0's saturation arrays should reflect the upstream pod-0
    load spike, while pods 1 and 2 stay on the natural baseline.

    Returns the output directory so individual tests can read
    per-component / per-instance CSVs.
    """
    # Patch SCENARIOS with one extra synthetic scenario so the
    # primary anomaly on apigateway.requests_per_sec fires only on
    # i0. Restored on fixture teardown.
    extra = amc.Scenario(
        id="synthetic_apigateway_pod0_load_spike",
        name="Synthetic apigateway pod-0 load spike",
        severity="medium",
        days_required=1,
        category="partial_outage",
        components_touched=("apigateway",),
        primary_specs=(
            ("apigateway", {
                "time_offset": SPIKE_TIME_OFFSET,
                "duration_seconds": SPIKE_DURATION_S,
                "metric": "requests_per_sec",
                "description": "Pod-0 load spike — apigateway requests/s elevated on i0",
                "generator": lambda ts, idx: SPIKE_VALUE,
                "instance_filter": ["i0"],
                "shape": "sustained",
            }),
        ),
        cascade_specs=(),
    )
    original = dict(amc.SCENARIOS)
    amc.SCENARIOS[extra.id] = extra
    try:
        out = tmp_path_factory.mktemp("ver158_n3_synthetic_pod0_spike")
        run = run_capture(
            amc, out, days=1,
            # ``interval_seconds=None`` keeps 1 row/s so the
            # positional ``vals[lo_s:hi_s]`` window slicing below
            # holds the "row index == elapsed seconds" invariant.
            # ``drop_rate=0.0`` prevents dropped rows from shifting
            # the spike/baseline window offsets.
            interval_seconds=None,
            drop_rate=0.0,
            extra_args=[
                "--instances-per-component", "3",
                # Pin only the synthetic scenario so unrelated catalog
                # entries cannot perturb
                # ``authservice.avg_auth_latency_ms`` inside the
                # baseline/spike windows. A future catalog addition
                # without this gate could silently lift the noise floor
                # and flake the assertion.
                "--scenarios", "synthetic_apigateway_pod0_load_spike",
                # Keep the full default topology graph in scope: the
                # apigateway → authservice 1:1 routing this test
                # asserts on requires apigateway's own upstream
                # (loadbalancer) and sibling downstreams (cacheservice,
                # database, llm_analytics) to maintain their normal
                # coupling shape. Listing the components explicitly
                # pins the set so a future default-component change
                # cannot accidentally drop one of them.
                "--components",
                "loadbalancer,apigateway,authservice,cacheservice,database,llm_analytics",
            ],
        )
    finally:
        amc.SCENARIOS.clear()
        amc.SCENARIOS.update(original)
    return run.out_dir


def _instance_block_rows(out_dir, component, instance_id, metric):
    """Return ``(timestamps, values)`` for ``component`` / ``instance_id`` /
    ``metric`` from the long-form per-component CSV.

    Reads the CSV in a single pass: the header line is consumed first
    to resolve column indices, then each data row is matched.
    Materialises the requested instance's rows into memory.
    """
    out: list[tuple[str, float]] = []
    with open(out_dir / f"{component}.csv", newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
        metric_idx = header.index(metric)
        id_idx = header.index("id")
        for cells in reader:
            if not cells or cells[id_idx] != instance_id:
                continue
            out.append((cells[0], float(cells[metric_idx])))
    out.sort(key=lambda kv: kv[0])
    ts = [kv[0] for kv in out]
    vals = np.array([kv[1] for kv in out], dtype=np.float64)
    return ts, vals


def test_generate_component_rejects_half_passed_per_instance_arrays(amc, tmp_path):
    """``generate_component`` must reject the half-passed shape
    (exactly one of ``coupling_arrays_per_instance`` /
    ``saturation_arrays_per_instance`` non-None) so programmatic
    callers see a clear ``ValueError`` rather than silently falling
    back to the legacy shared-arrays path.
    """
    specs = amc.COMPONENTS["loadbalancer"][: amc.DEFAULT_METRICS_PER_COMPONENT["loadbalancer"]]
    instances = [amc.Instance()]
    rng = np.random.RandomState(0)
    ctx = amc.RunContext(rng=rng)
    common = dict(
        component_name="loadbalancer",
        specs=specs,
        anomaly_specs=[],
        base_dir=tmp_path,
        total_seconds=60,
        drop_rate=0.0,
        interval=1.0,
        ts_array=np.arange(60, dtype=np.float64),
        ts_strings=np.array([f"t{i}" for i in range(60)]),
        emit_metrics=True,
        dst_inject_day=0,
        ctx=ctx,
        instances=instances,
    )
    with pytest.raises(ValueError, match="coupling=present saturation=None"):
        amc.generate_component(
            **common,
            coupling_arrays_per_instance=[{}],
            saturation_arrays_per_instance=None,
        )
    with pytest.raises(ValueError, match="coupling=None saturation=present"):
        amc.generate_component(
            **common,
            coupling_arrays_per_instance=None,
            saturation_arrays_per_instance=[{}],
        )


def test_matched_cardinality_dispatch(amc):
    """``_matched_cardinality`` returns True iff both sides have the
    same positive instance count.

    Pins the 1:1 vs uniform-fan-out dispatch the per-instance
    composer reads. The mismatched / zero-cardinality cases fall back
    to averaging across upstream pods.
    """
    fn = amc._matched_cardinality
    assert fn(3, 3) is True
    assert fn(1, 1) is True
    assert fn(20, 20) is True
    assert fn(3, 1) is False
    assert fn(1, 3) is False
    assert fn(0, 0) is False  # zero-cardinality treated as mismatched
    assert fn(0, 3) is False
    assert fn(2, 4) is False


def test_instances_per_component_1_byte_identity(
    amc, tmp_path_factory, default_1d
):
    """An explicit ``--instances-per-component 1`` run is byte-identical
    to the no-flag default. Both take the anonymous-N=1 lambda-baked
    topology branch (the pre-existing parity path).
    """
    out = tmp_path_factory.mktemp("ver158_n1_explicit_default")
    n1_run = run_capture(
        amc, out, days=1,
        extra_args=["--instances-per-component", "1"],
    )
    # Compare every emitted per-component CSV + anomalies.csv.
    n1_files = sorted(p.name for p in n1_run.out_dir.glob("*.csv"))
    default_files = sorted(p.name for p in default_1d.out_dir.glob("*.csv"))
    assert n1_files == default_files
    assert n1_files, "expected at least one CSV"
    for fname in n1_files:
        assert _sha256_path(n1_run.out_dir / fname) == _sha256_path(
            default_1d.out_dir / fname
        ), f"{fname} diverged between default and explicit --instances-per-component 1"


def test_synthetic_pod0_spike_lifts_only_pod0_saturation(
    amc, synthetic_n3_run
):
    """Synthetic upstream load spike on ``apigateway.requests_per_sec``
    (instance ``i0`` only) produces per-instance saturation lift on
    ``authservice.avg_auth_latency_ms`` for pod-0, but NOT on
    sibling pods 1 and 2.

    Matches the issue acceptance: a slow upstream pod
    produces saturation feedback only on the corresponding
    downstream pod's rows under 1:1 routing.

    Statistical check: compute the mean of the failure window
    (``[SPIKE_TIME_OFFSET, SPIKE_TIME_OFFSET + SPIKE_DURATION_S)`` —
    half-open to match the end-exclusive Python slicing below)
    per pod and compare against the natural-window baseline. Pod-0's
    in-window mean must be well above the noise floor; pods 1 / 2
    must stay close to their natural baseline.
    """
    out_dir = synthetic_n3_run

    # Pod-0 latency in the spike window: expect a clear lift driven
    # by the saturating ``apigateway -> authservice`` edge.
    pod0_ts, pod0_vals = _instance_block_rows(
        out_dir, "authservice", "i0", "avg_auth_latency_ms"
    )
    pod1_ts, pod1_vals = _instance_block_rows(
        out_dir, "authservice", "i1", "avg_auth_latency_ms"
    )
    pod2_ts, pod2_vals = _instance_block_rows(
        out_dir, "authservice", "i2", "avg_auth_latency_ms"
    )

    # Per-pod row count and timestamp alignment: every pod must
    # emit the same row count and the same timestamp vector at each
    # row index. ``generate_component`` writes per-instance blocks
    # from the same shared ``ts_array`` for the dimensioned long-
    # form CSV, so any divergence here would indicate a regression
    # in the per-instance writer rather than in saturation routing.
    assert pod0_ts == pod1_ts == pod2_ts, (
        "per-pod timestamp vectors must be identical "
        f"(lens={len(pod0_ts)}, {len(pod1_ts)}, {len(pod2_ts)})"
    )

    # Locate the spike-window indices by fixed second offsets — the
    # fixture pins ``drop_rate=0.0`` so the long-form CSV emits one row
    # per second per instance and row index equals elapsed seconds.
    # START = 2026-03-10 00:00:00; 03:30 → row 12600 at 1s interval.
    n_rows = len(pod0_ts)
    spike_start = SPIKE_TIME_OFFSET
    spike_end = SPIKE_TIME_OFFSET + SPIKE_DURATION_S
    # Pre-spike window (1h00–2h30) and post-spike window (4h00–5h30)
    # define the baseline; the in-spike window is the test slice.
    pre_window = (1 * 3600, 2 * 3600 + 30 * 60)
    post_window = (4 * 3600, 5 * 3600 + 30 * 60)

    def _window_mean(vals, lo_s, hi_s):
        return float(np.mean(vals[lo_s:hi_s]))

    pod0_in = _window_mean(pod0_vals, spike_start, spike_end)
    pod1_in = _window_mean(pod1_vals, spike_start, spike_end)
    pod2_in = _window_mean(pod2_vals, spike_start, spike_end)
    pod1_baseline = (
        _window_mean(pod1_vals, *pre_window)
        + _window_mean(pod1_vals, *post_window)
    ) / 2.0
    pod2_baseline = (
        _window_mean(pod2_vals, *pre_window)
        + _window_mean(pod2_vals, *post_window)
    ) / 2.0

    assert n_rows > spike_end, (
        f"authservice CSV has {n_rows} rows; spike window ends at {spike_end}"
    )
    assert pod0_in > pod1_in + 1.0, (
        f"pod-0 should see elevated latency vs pod-1 (saturation lift). "
        f"pod0_in={pod0_in:.3f}, pod1_in={pod1_in:.3f}"
    )
    assert pod0_in > pod2_in + 1.0, (
        f"pod-0 should see elevated latency vs pod-2 (saturation lift). "
        f"pod0_in={pod0_in:.3f}, pod2_in={pod2_in:.3f}"
    )
    # Sibling pods must stay close to their natural baseline (the
    # spike on apigateway.pod-0 should NOT propagate to authservice
    # pods 1 or 2 under 1:1 routing). Tolerance: 0.5% of the
    # baseline magnitude (~0.55 ms at base=110, ~0.40 ms at base=80).
    # The 30-min spike window contains ~1800 samples, so the
    # natural per-row jitter (std≈5) averages down to ~0.12 ms in
    # the mean — well below the 0.5% threshold, which leaves ample
    # signal headroom to catch any cross-pod leakage from the
    # apigateway.i0 spike.
    assert abs(pod1_in - pod1_baseline) < 0.005 * pod1_baseline, (
        f"pod-1 latency should remain at baseline (1:1 routing). "
        f"pod1_in={pod1_in:.3f}, baseline={pod1_baseline:.3f}"
    )
    assert abs(pod2_in - pod2_baseline) < 0.005 * pod2_baseline, (
        f"pod-2 latency should remain at baseline. "
        f"pod2_in={pod2_in:.3f}, baseline={pod2_baseline:.3f}"
    )


@pytest.fixture(scope="session")
def default_1d(amc, tmp_path_factory):
    out = tmp_path_factory.mktemp("ver158_default_1d_for_n1_check")
    return run_capture(amc, out, days=1)
