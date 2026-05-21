"""Tests for VER-140 Phase 7 partial-outage scenarios.

Verifies:
- ``auth_pod_failure`` is registered with the correct metadata and only overrides
  the ``i0`` instance when a synthetic spec with ``instance_filter=["i0"]`` is run
  against three named instances.
- ``cache_az_isolation`` is registered with the correct metadata and only overrides
  instances whose ``az`` matches ``"us-east-1a"`` when run with an instance-config
  that supplies that dimension.
- Both scenarios declare ``severity == "high"`` so they do not activate under the
  default medium signal level. The default-output byte-identity assertion lives
  in ``tests/test_scenarios.py::test_default_*_csvs_byte_identical`` (the locked
  SHA-256 hashes that would change if either scenario leaked into the default
  pool); this file does not duplicate those hashes.
- The callable ``instance_filter`` on ``cache_az_isolation`` returns ``False``
  for instances with ``az=None`` (the ``--instances-per-component`` path).
"""

import csv

import numpy as np
import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_generate(amc, component, specs, *, anomaly_specs, instances,
                  tmp_path, total_seconds=200, interval=10.0):
    ctx = amc.RunContext(rng=np.random.RandomState(42))
    ts_array, ts_strings = amc._build_timestamp_arrays(total_seconds, interval)
    amc.generate_component(
        component,
        specs,
        anomaly_specs,
        base_dir=tmp_path,
        total_seconds=total_seconds,
        drop_rate=0.0,
        interval=interval,
        ts_array=ts_array,
        ts_strings=ts_strings,
        emit_metrics=True,
        dst_inject_day=0,
        ctx=ctx,
        instances=instances,
    )
    return ctx


def _rows_for_instance(csv_path, instance_id):
    with open(csv_path) as f:
        return [r for r in csv.DictReader(f) if r.get("id") == instance_id]


def _three_instances(amc):
    return [amc.Instance(id=f"i{k}", pod=f"pod-{k}") for k in range(3)]


def _az_instances(amc, az_for_i0):
    """Two instances: i0 with the given az, i1 with a different az."""
    return [
        amc.Instance(id="i0", pod="pod-0", az=az_for_i0),
        amc.Instance(id="i1", pod="pod-1", az="us-west-2a"),
    ]


# ---------------------------------------------------------------------------
# Registry structural tests
# ---------------------------------------------------------------------------

def test_auth_pod_failure_registered(amc):
    assert "auth_pod_failure" in amc.SCENARIOS


def test_cache_az_isolation_registered(amc):
    assert "cache_az_isolation" in amc.SCENARIOS


def test_auth_pod_failure_metadata(amc):
    s = amc.SCENARIOS["auth_pod_failure"]
    assert s.severity == "high"
    assert s.days_required == 1
    assert set(s.components_touched) == {"authservice", "apigateway"}


def test_cache_az_isolation_metadata(amc):
    s = amc.SCENARIOS["cache_az_isolation"]
    assert s.severity == "high"
    assert s.days_required == 1
    assert set(s.components_touched) == {"cacheservice"}


def test_auth_pod_failure_primary_targets_authservice(amc):
    s = amc.SCENARIOS["auth_pod_failure"]
    components = {c for c, _ in s.primary_specs}
    assert "authservice" in components


def test_auth_pod_failure_cascade_targets_apigateway(amc):
    s = amc.SCENARIOS["auth_pod_failure"]
    assert s.cascade_specs, "auth_pod_failure must have at least one cascade spec"
    targets = {t for t, _ in s.cascade_specs}
    assert "apigateway" in targets


def test_auth_pod_failure_instance_filter_is_i0(amc):
    s = amc.SCENARIOS["auth_pod_failure"]
    for _, spec in s.primary_specs:
        assert "instance_filter" in spec
        filt = spec["instance_filter"]
        assert isinstance(filt, frozenset), (
            "instance_filter should be normalized to frozenset by validator"
        )
        assert "i0" in filt


def test_cache_az_isolation_instance_filter_is_callable(amc):
    s = amc.SCENARIOS["cache_az_isolation"]
    assert s.primary_specs, "cache_az_isolation must have primary specs"
    for _, spec in s.primary_specs:
        assert "instance_filter" in spec
        assert callable(spec["instance_filter"])


def test_cache_az_isolation_callable_matches_correct_az(amc):
    s = amc.SCENARIOS["cache_az_isolation"]
    filt = s.primary_specs[0][1]["instance_filter"]
    matching = amc.Instance(id="i0", pod="pod-0", az="us-east-1a")
    non_matching_az = amc.Instance(id="i1", pod="pod-1", az="us-west-2a")
    no_az = amc.Instance(id="i2", pod="pod-2")
    assert filt(matching) is True
    assert filt(non_matching_az) is False
    assert filt(no_az) is False


def test_auth_pod_failure_high_severity_not_in_default_pool(amc):
    """auth_pod_failure is high severity; medium signal-level must not activate it."""
    assert amc.SCENARIOS["auth_pod_failure"].severity == "high"


def test_cache_az_isolation_high_severity_not_in_default_pool(amc):
    """cache_az_isolation is high severity; medium signal-level must not activate it."""
    assert amc.SCENARIOS["cache_az_isolation"].severity == "high"


# ---------------------------------------------------------------------------
# Runtime behaviour: auth_pod_failure overrides only i0
# ---------------------------------------------------------------------------

def test_auth_pod_failure_registry_spec_out_of_range_warns(amc, tmp_path, capsys):
    """The registry ``auth_pod_failure`` primary spec has ``time_offset=12600s``
    (3h30m). Running it through ``generate_component`` with a 200s window must
    log the canonical "time_offset outside [0, N)" WARNING and write no override
    to any instance row. The actual filter behavior (only-i0) is exercised by
    ``test_auth_pod_failure_synthetic_only_i0_overridden`` below, which uses a
    synthetic spec whose offset lands inside the test window."""
    component = "authservice"
    specs = amc.COMPONENTS[component][:amc.DEFAULT_METRICS_PER_COMPONENT[component]]

    scenario = amc.SCENARIOS["auth_pod_failure"]
    anomaly_specs = [
        spec for comp, spec in scenario.primary_specs
        if comp == "authservice" and spec["metric"] == "error_rate"
    ]
    assert len(anomaly_specs) == 1, "Expected exactly one error_rate primary spec"
    assert anomaly_specs[0]["time_offset"] > 200, (
        "Test premise: registry offset must exceed the 200s window so the spec "
        "is out of range."
    )

    instances = _three_instances(amc)
    _run_generate(
        amc, component, list(specs),
        anomaly_specs=anomaly_specs,
        instances=instances,
        tmp_path=tmp_path,
        total_seconds=200,
        interval=10.0,
    )

    captured = capsys.readouterr()
    assert "time_offset outside" in captured.err, (
        f"Expected out-of-range WARNING; stderr was {captured.err!r}"
    )

    csv_path = tmp_path / "authservice.csv"
    assert csv_path.exists(), "authservice.csv must still be written"

    # No row in any instance block carries the registry override value (0.85);
    # natural error_rate baseline is ~0.20 (base=0.2, jitter=0.05), well below 0.85.
    target_value = anomaly_specs[0]["generator"](None, 0)
    for inst_id in ("i0", "i1", "i2"):
        for row in _rows_for_instance(csv_path, inst_id):
            assert float(row["error_rate"]) != pytest.approx(target_value), (
                f"{inst_id} row {row} unexpectedly carries the override value "
                f"{target_value}; spec should have been skipped as out-of-range."
            )


def test_auth_pod_failure_synthetic_only_i0_overridden(amc, tmp_path):
    """Synthetic auth spec with instance_filter=["i0"] — only i0 row
    at the target index is overridden; i1 and i2 are unchanged.

    Note: ``_validate_scenario_spec`` normalizes the list to a frozenset at
    import time, but the runtime ``instance_filter`` slot here is exercised
    directly through ``generate_component`` without that normalization, so the
    list form is the value the test actually passes."""
    component = "authservice"
    specs = amc.COMPONENTS[component][:amc.DEFAULT_METRICS_PER_COMPONENT[component]]

    anomaly_specs = [
        {
            "time_offset": 50,
            "metric": "error_rate",
            "description": "Synthetic pod-0 error spike",
            "generator": lambda ts, idx: 0.95,
            "instance_filter": ["i0"],
        }
    ]

    instances = _three_instances(amc)
    _run_generate(
        amc, component, list(specs),
        anomaly_specs=anomaly_specs,
        instances=instances,
        tmp_path=tmp_path,
        total_seconds=120,
        interval=10.0,
    )

    csv_path = tmp_path / "authservice.csv"
    rows_i0 = _rows_for_instance(csv_path, "i0")
    rows_i1 = _rows_for_instance(csv_path, "i1")
    rows_i2 = _rows_for_instance(csv_path, "i2")

    assert rows_i0 and rows_i1 and rows_i2, "All three instance blocks must be written"

    # time_offset=50s, interval=10s → row index 5 in each block
    assert float(rows_i0[5]["error_rate"]) == pytest.approx(0.95), (
        f"i0 row 5 expected override 0.95, got {rows_i0[5]['error_rate']!r}"
    )
    assert float(rows_i1[5]["error_rate"]) != pytest.approx(0.95), (
        f"i1 row 5 must NOT be overridden (filter excludes i1)"
    )
    assert float(rows_i2[5]["error_rate"]) != pytest.approx(0.95), (
        f"i2 row 5 must NOT be overridden (filter excludes i2)"
    )


# ---------------------------------------------------------------------------
# Runtime behaviour: cache_az_isolation overrides only matching-az instances
# ---------------------------------------------------------------------------

def test_cache_az_isolation_synthetic_only_matching_az_overridden(amc, tmp_path):
    """Synthetic cache spec with callable instance_filter matching az='us-east-1a' —
    only the i0 instance (az=us-east-1a) is overridden; i1 (us-west-2a) is not."""
    component = "cacheservice"
    specs = amc.COMPONENTS[component][:amc.DEFAULT_METRICS_PER_COMPONENT[component]]

    anomaly_specs = [
        {
            "time_offset": 50,
            "metric": "cache_hits",
            "description": "AZ isolation — cache_hits collapse on us-east-1a",
            "generator": lambda ts, idx: 10,
            "instance_filter": lambda inst: inst.az == "us-east-1a",
        },
    ]

    instances = _az_instances(amc, az_for_i0="us-east-1a")
    _run_generate(
        amc, component, list(specs),
        anomaly_specs=anomaly_specs,
        instances=instances,
        tmp_path=tmp_path,
        total_seconds=120,
        interval=10.0,
    )

    csv_path = tmp_path / "cacheservice.csv"
    rows_i0 = _rows_for_instance(csv_path, "i0")  # az=us-east-1a → overridden
    rows_i1 = _rows_for_instance(csv_path, "i1")  # az=us-west-2a → not overridden

    assert rows_i0 and rows_i1

    # time_offset=50s, interval=10s → row index 5
    assert float(rows_i0[5]["cache_hits"]) == pytest.approx(10), (
        f"i0 (us-east-1a) row 5 expected 10, got {rows_i0[5]['cache_hits']!r}"
    )
    assert float(rows_i1[5]["cache_hits"]) != pytest.approx(10), (
        f"i1 (us-west-2a) row 5 must NOT be overridden"
    )


def test_cache_az_isolation_no_az_instances_emits_warning(amc, tmp_path, capsys):
    """When --instances-per-component 3 (az=None for all), the callable filter
    matches zero instances → one WARNING is logged and no override fires."""
    component = "cacheservice"
    specs = amc.COMPONENTS[component][:amc.DEFAULT_METRICS_PER_COMPONENT[component]]

    anomaly_specs = [
        {
            "time_offset": 50,
            "metric": "cache_hits",
            "description": "AZ isolation — will match nothing (az=None)",
            "generator": lambda ts, idx: 10,
            "instance_filter": lambda inst: inst.az == "us-east-1a",
        },
    ]

    # Instances from --instances-per-component have az=None
    instances = _three_instances(amc)
    _run_generate(
        amc, component, list(specs),
        anomaly_specs=anomaly_specs,
        instances=instances,
        tmp_path=tmp_path,
        total_seconds=120,
        interval=10.0,
    )

    captured = capsys.readouterr()
    assert "WARNING" in captured.err or "warning" in captured.err.lower(), (
        "Expected a WARNING about zero-match instance_filter in stderr"
    )
    assert "instance_filter" in captured.err.lower(), (
        "WARNING should mention instance_filter"
    )

    # No row should carry the override value 10 (natural cache_hits >> 10).
    # CSV cells are formatted with 3 decimals (e.g. "10.000"), so compare as
    # float — the prior ``!= "10"`` always passed regardless of the override.
    csv_path = tmp_path / "cacheservice.csv"
    rows_i0 = _rows_for_instance(csv_path, "i0")
    assert rows_i0, "expected at least one i0 row in cacheservice.csv"
    assert float(rows_i0[5]["cache_hits"]) != pytest.approx(10), (
        f"i0 row 5 must keep natural value; got {rows_i0[5]['cache_hits']!r}, "
        "no override should have fired"
    )
