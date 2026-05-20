"""Tests for ``instance_filter`` on anomaly specs (VER-140 Phase 4).

Verifies:
- Spec with ``instance_filter=["i0"]`` overrides only the ``i0`` (pod-0)
  instance under ``--instances-per-component 3``; other instances see the
  natural baseline.
- Spec with a callable ``instance_filter`` works the same way.
- Spec without ``instance_filter`` continues to override every instance
  (today's Phase 2 behavior).
- ``_validate_scenario_spec`` rejects malformed ``instance_filter`` values
  at import time (non-iterable, non-callable, non-None scalars; iterables
  containing non-string entries).
- A filter that matches zero active instances logs one runtime WARNING and
  skips the spec.
"""

import csv

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _good_primary_spec():
    """Minimal well-formed primary spec used by validator tests."""
    return {
        "time_offset": 60,
        "metric": "error_rate",
        "description": "Synthetic spec for instance_filter tests",
        "generator": lambda ts, idx: 0.99,
    }


def _good_cascade_spec():
    return {
        "time_offset": 60,
        "metric": "error_rate",
        "description": "Synthetic cascade for instance_filter tests",
        "generator": lambda ts, idx: 0.99,
    }


def _run_generate(amc, component, specs, *, anomaly_specs, instances,
                  tmp_path, ctx=None, total_seconds=120, interval=10.0,
                  drop_rate=0.0):
    """Call ``generate_component`` directly with explicit per-instance topology."""
    import numpy as np

    if ctx is None:
        ctx = amc.RunContext(rng=np.random.RandomState(0))

    ts_array, ts_strings = amc._build_timestamp_arrays(total_seconds, interval)
    amc.generate_component(
        component,
        specs,
        anomaly_specs,
        base_dir=tmp_path,
        total_seconds=total_seconds,
        drop_rate=drop_rate,
        interval=interval,
        ts_array=ts_array,
        ts_strings=ts_strings,
        emit_metrics=True,
        dst_inject_day=0,
        ctx=ctx,
        instances=instances,
    )
    return ctx


def _read_rows_for_instance(csv_path, instance_id):
    """Return list of dicts (one per row) restricted to rows for ``instance_id``."""
    with open(csv_path) as f:
        reader = csv.DictReader(f)
        return [row for row in reader if row.get("id") == instance_id]


# ---------------------------------------------------------------------------
# Validator: well-formed instance_filter values
# ---------------------------------------------------------------------------


def test_validate_scenario_spec_instance_filter_none_accepted(amc):
    spec = _good_primary_spec()
    spec["instance_filter"] = None
    assert amc._validate_scenario_spec(
        "test_slug", "apigateway", spec, is_cascade=False
    ) is None


def test_validate_scenario_spec_instance_filter_list_accepted(amc):
    spec = _good_primary_spec()
    spec["instance_filter"] = ["i0", "i1"]
    assert amc._validate_scenario_spec(
        "test_slug", "apigateway", spec, is_cascade=False
    ) is None


def test_validate_scenario_spec_instance_filter_tuple_accepted(amc):
    spec = _good_primary_spec()
    spec["instance_filter"] = ("i0",)
    assert amc._validate_scenario_spec(
        "test_slug", "apigateway", spec, is_cascade=False
    ) is None


def test_validate_scenario_spec_instance_filter_frozenset_accepted(amc):
    spec = _good_primary_spec()
    spec["instance_filter"] = frozenset(["i0", "i2"])
    assert amc._validate_scenario_spec(
        "test_slug", "apigateway", spec, is_cascade=False
    ) is None


def test_validate_scenario_spec_instance_filter_callable_accepted(amc):
    spec = _good_primary_spec()
    spec["instance_filter"] = lambda inst: inst.pod == "pod-1"
    assert amc._validate_scenario_spec(
        "test_slug", "apigateway", spec, is_cascade=False
    ) is None


def test_validate_scenario_spec_instance_filter_on_cascade_accepted(amc):
    """Cascade specs also support instance_filter (issue lists primary AND cascade)."""
    spec = _good_cascade_spec()
    spec["instance_filter"] = ["i0"]
    assert amc._validate_scenario_spec(
        "test_slug", "apigateway", spec, is_cascade=True
    ) is None


# ---------------------------------------------------------------------------
# Validator: malformed instance_filter values
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_value", [42, 3.14, True, False])
def test_validate_scenario_spec_instance_filter_scalar_rejected(amc, bad_value):
    """Scalars (int, float, bool) are neither iterables of str nor callables."""
    spec = _good_primary_spec()
    spec["instance_filter"] = bad_value
    with pytest.raises(ValueError, match="instance_filter"):
        amc._validate_scenario_spec(
            "test_slug", "apigateway", spec, is_cascade=False
        )


def test_validate_scenario_spec_instance_filter_string_rejected(amc):
    """Bare string is iterable in Python but almost always a bug (would
    iterate characters). Reject so callers pass ``["i0"]`` instead of
    ``"i0"``."""
    spec = _good_primary_spec()
    spec["instance_filter"] = "i0"
    with pytest.raises(ValueError, match="instance_filter"):
        amc._validate_scenario_spec(
            "test_slug", "apigateway", spec, is_cascade=False
        )


def test_validate_scenario_spec_instance_filter_dict_rejected(amc):
    spec = _good_primary_spec()
    spec["instance_filter"] = {"i0": True}
    with pytest.raises(ValueError, match="instance_filter"):
        amc._validate_scenario_spec(
            "test_slug", "apigateway", spec, is_cascade=False
        )


def test_validate_scenario_spec_instance_filter_iterable_non_str_rejected(amc):
    """An iterable of ints (or anything non-string) is rejected — ids are strings."""
    spec = _good_primary_spec()
    spec["instance_filter"] = [0, 1]
    with pytest.raises(ValueError, match="instance_filter"):
        amc._validate_scenario_spec(
            "test_slug", "apigateway", spec, is_cascade=False
        )


def test_validate_scenario_spec_instance_filter_mixed_iterable_rejected(amc):
    """One bad element in a list trips the validator."""
    spec = _good_primary_spec()
    spec["instance_filter"] = ["i0", 1]
    with pytest.raises(ValueError, match="instance_filter"):
        amc._validate_scenario_spec(
            "test_slug", "apigateway", spec, is_cascade=False
        )


def test_validate_scenario_spec_instance_filter_empty_iterable_accepted(amc):
    """An empty iterable is structurally well-formed (it just matches no
    instance at runtime). The runtime path emits the no-match WARNING; the
    validator should not double-reject."""
    spec = _good_primary_spec()
    spec["instance_filter"] = []
    assert amc._validate_scenario_spec(
        "test_slug", "apigateway", spec, is_cascade=False
    ) is None


def test_validate_scenario_spec_instance_filter_generator_normalized(amc):
    """A one-shot generator expression is materialized and written back as a
    frozenset so ``_resolve_instance_filter`` can iterate it a second time
    without seeing an exhausted iterator."""
    spec = _good_primary_spec()
    # A generator is one-shot: iterating it twice would give empty on the
    # second pass, producing a spurious no-match warning at runtime.
    spec["instance_filter"] = (x for x in ["i0", "i1"])
    amc._validate_scenario_spec("test_slug", "apigateway", spec, is_cascade=False)
    # Validator should have normalized to a frozenset.
    assert isinstance(spec["instance_filter"], frozenset)
    assert spec["instance_filter"] == {"i0", "i1"}


def test_validate_scenario_spec_instance_filter_list_normalized_to_frozenset(amc):
    """Even a plain list is normalized so ``_resolve_instance_filter`` always
    receives a frozenset (O(1) membership, reiterable)."""
    spec = _good_primary_spec()
    spec["instance_filter"] = ["i0"]
    amc._validate_scenario_spec("test_slug", "apigateway", spec, is_cascade=False)
    assert isinstance(spec["instance_filter"], frozenset)
    assert spec["instance_filter"] == {"i0"}


# ---------------------------------------------------------------------------
# Runtime: instance_filter list selects only matching instances
# ---------------------------------------------------------------------------


def _three_instances(amc):
    return [
        amc.Instance(id="i0", pod="pod-0"),
        amc.Instance(id="i1", pod="pod-1"),
        amc.Instance(id="i2", pod="pod-2"),
    ]


def test_instance_filter_id_list_targets_only_matched_instance(amc, tmp_path):
    """``instance_filter=["i0"]`` overrides only the i0 (pod-0) instance."""
    component = "apigateway"
    specs = amc.COMPONENTS[component][:amc.DEFAULT_METRICS_PER_COMPONENT[component]]
    metric = next(s.name for s in specs if s.name == "error_rate")
    assert metric == "error_rate", "test fixture expects error_rate column"

    anomaly_specs = [
        {
            "time_offset": 60,
            "metric": metric,
            "description": "i0-only override",
            "generator": lambda ts, idx: 0.987,
            "instance_filter": ["i0"],
        }
    ]
    _run_generate(
        amc, component, list(specs),
        anomaly_specs=anomaly_specs,
        instances=_three_instances(amc),
        tmp_path=tmp_path,
        total_seconds=120, interval=10.0,
    )

    csv_path = tmp_path / f"{component}.csv"
    # Row at time_offset=60s with interval=10s → row index 6 in each block.
    # Read every row; the i0 block must show 0.987 at that row index; the
    # i1 and i2 blocks must NOT show 0.987 (they keep the natural value).
    rows_i0 = _read_rows_for_instance(csv_path, "i0")
    rows_i1 = _read_rows_for_instance(csv_path, "i1")
    rows_i2 = _read_rows_for_instance(csv_path, "i2")
    assert rows_i0 and rows_i1 and rows_i2
    assert rows_i0[6][metric] == "0.987", (
        f"i0 row 6 expected 0.987 (override), got {rows_i0[6][metric]!r}"
    )
    assert rows_i1[6][metric] != "0.987", (
        f"i1 row 6 expected natural value (filter excludes i1), "
        f"got override 0.987"
    )
    assert rows_i2[6][metric] != "0.987", (
        f"i2 row 6 expected natural value (filter excludes i2), "
        f"got override 0.987"
    )


def test_instance_filter_callable_targets_only_matched_instance(amc, tmp_path):
    """``instance_filter=lambda inst: inst.pod == "pod-1"`` overrides only pod-1."""
    component = "apigateway"
    specs = amc.COMPONENTS[component][:amc.DEFAULT_METRICS_PER_COMPONENT[component]]
    metric = "error_rate"

    anomaly_specs = [
        {
            "time_offset": 60,
            "metric": metric,
            "description": "pod-1-only override",
            "generator": lambda ts, idx: 0.876,
            "instance_filter": lambda inst: inst.pod == "pod-1",
        }
    ]
    _run_generate(
        amc, component, list(specs),
        anomaly_specs=anomaly_specs,
        instances=_three_instances(amc),
        tmp_path=tmp_path,
        total_seconds=120, interval=10.0,
    )

    csv_path = tmp_path / f"{component}.csv"
    rows_i0 = _read_rows_for_instance(csv_path, "i0")
    rows_i1 = _read_rows_for_instance(csv_path, "i1")
    rows_i2 = _read_rows_for_instance(csv_path, "i2")
    assert rows_i1[6][metric] == "0.876", "pod-1 row 6 expected 0.876 override"
    assert rows_i0[6][metric] != "0.876", "pod-0 should not see the override"
    assert rows_i2[6][metric] != "0.876", "pod-2 should not see the override"


def test_instance_filter_none_targets_every_instance(amc, tmp_path):
    """No ``instance_filter`` (Phase 2 behavior) overrides every instance."""
    component = "apigateway"
    specs = amc.COMPONENTS[component][:amc.DEFAULT_METRICS_PER_COMPONENT[component]]
    metric = "error_rate"

    anomaly_specs = [
        {
            "time_offset": 60,
            "metric": metric,
            "description": "Unfiltered override hits every instance",
            "generator": lambda ts, idx: 0.654,
        }
    ]
    _run_generate(
        amc, component, list(specs),
        anomaly_specs=anomaly_specs,
        instances=_three_instances(amc),
        tmp_path=tmp_path,
        total_seconds=120, interval=10.0,
    )

    csv_path = tmp_path / f"{component}.csv"
    for inst_id in ("i0", "i1", "i2"):
        rows = _read_rows_for_instance(csv_path, inst_id)
        assert rows[6][metric] == "0.654", (
            f"{inst_id} row 6 expected 0.654 (unfiltered override); got "
            f"{rows[6][metric]!r}"
        )


def test_instance_filter_multi_id_targets_multiple_instances(amc, tmp_path):
    """``instance_filter=["i0", "i2"]`` overrides exactly two instances."""
    component = "apigateway"
    specs = amc.COMPONENTS[component][:amc.DEFAULT_METRICS_PER_COMPONENT[component]]
    metric = "error_rate"

    anomaly_specs = [
        {
            "time_offset": 60,
            "metric": metric,
            "description": "i0/i2 override",
            "generator": lambda ts, idx: 0.321,
            "instance_filter": ["i0", "i2"],
        }
    ]
    _run_generate(
        amc, component, list(specs),
        anomaly_specs=anomaly_specs,
        instances=_three_instances(amc),
        tmp_path=tmp_path,
        total_seconds=120, interval=10.0,
    )

    csv_path = tmp_path / f"{component}.csv"
    rows_i0 = _read_rows_for_instance(csv_path, "i0")
    rows_i1 = _read_rows_for_instance(csv_path, "i1")
    rows_i2 = _read_rows_for_instance(csv_path, "i2")
    assert rows_i0[6][metric] == "0.321"
    assert rows_i2[6][metric] == "0.321"
    assert rows_i1[6][metric] != "0.321", "i1 should not see the override"


def test_instance_filter_no_active_match_emits_warning_and_skips(amc, tmp_path,
                                                                   capsys):
    """A filter that matches zero active instances logs a WARNING and skips."""
    component = "apigateway"
    specs = amc.COMPONENTS[component][:amc.DEFAULT_METRICS_PER_COMPONENT[component]]
    metric = "error_rate"

    anomaly_specs = [
        {
            "time_offset": 60,
            "metric": metric,
            "description": "Targets nonexistent instance",
            "generator": lambda ts, idx: 0.111,
            "instance_filter": ["i_does_not_exist"],
        }
    ]
    _run_generate(
        amc, component, list(specs),
        anomaly_specs=anomaly_specs,
        instances=_three_instances(amc),
        tmp_path=tmp_path,
        total_seconds=120, interval=10.0,
    )

    captured = capsys.readouterr()
    assert "instance_filter" in captured.err.lower(), (
        f"Expected 'instance_filter' WARNING in stderr; got {captured.err!r}"
    )
    assert "warning" in captured.err.lower()

    # And no instance shows the override value
    csv_path = tmp_path / f"{component}.csv"
    for inst_id in ("i0", "i1", "i2"):
        rows = _read_rows_for_instance(csv_path, inst_id)
        assert rows[6][metric] != "0.111", (
            f"{inst_id} row 6 should NOT see the override; got {rows[6][metric]!r}"
        )


def test_instance_filter_partial_does_not_emit_manifest_for_excluded(amc,
                                                                       tmp_path):
    """A filtered anomaly still produces one manifest entry (Phase 4 keeps
    today's manifest shape — one row per (timestamp, component, metric))."""
    component = "apigateway"
    specs = amc.COMPONENTS[component][:amc.DEFAULT_METRICS_PER_COMPONENT[component]]
    metric = "error_rate"

    anomaly_specs = [
        {
            "time_offset": 60,
            "metric": metric,
            "description": "i0-only override",
            "generator": lambda ts, idx: 0.987,
            "instance_filter": ["i0"],
        }
    ]
    ctx = _run_generate(
        amc, component, list(specs),
        anomaly_specs=anomaly_specs,
        instances=_three_instances(amc),
        tmp_path=tmp_path,
        total_seconds=120, interval=10.0,
    )

    # Exactly one manifest entry per partially-filtered anomaly.
    entries = [e for e in ctx.anomalies if e["metric"] == metric]
    assert len(entries) == 1, (
        f"Expected exactly one manifest entry for partially-filtered anomaly; "
        f"got {len(entries)}: {entries}"
    )


def test_instance_filter_no_match_does_not_emit_manifest(amc, tmp_path):
    """When the filter excludes every active instance, no manifest row appears."""
    component = "apigateway"
    specs = amc.COMPONENTS[component][:amc.DEFAULT_METRICS_PER_COMPONENT[component]]
    metric = "error_rate"

    anomaly_specs = [
        {
            "time_offset": 60,
            "metric": metric,
            "description": "Targets nonexistent instance",
            "generator": lambda ts, idx: 0.111,
            "instance_filter": ["i_does_not_exist"],
        }
    ]
    ctx = _run_generate(
        amc, component, list(specs),
        anomaly_specs=anomaly_specs,
        instances=_three_instances(amc),
        tmp_path=tmp_path,
        total_seconds=120, interval=10.0,
    )

    entries = [e for e in ctx.anomalies if e["metric"] == metric]
    assert entries == [], (
        f"Filter matching zero instances should produce no manifest entry; got {entries}"
    )


def test_instance_filter_unfiltered_propagates_to_forked_buffer_other_rows(
    amc, tmp_path
):
    """Cross-row propagation: a filtered spec at t=60 forks pod-0's
    buffer. A later unfiltered spec at t=110 (different row) must apply
    to every pod — including pod-0's forked buffer — so pod-0 doesn't
    stay stuck on its forked baseline at the later row.

    Distinct ``(metric, time_offset)`` pairs are used because the
    runtime guard rejects two specs at the same key. This is the real
    reason ``generate_component()``'s unfiltered branch propagates to
    forked buffers (not "same-cell collisions", which can't happen)."""
    component = "apigateway"
    specs = amc.COMPONENTS[component][:amc.DEFAULT_METRICS_PER_COMPONENT[component]]
    metric = "error_rate"

    anomaly_specs = [
        {
            "time_offset": 60,
            "metric": metric,
            "description": "Filtered spec at t=60 forks pod-0",
            "generator": lambda ts, idx: 0.111,
            "instance_filter": ["i0"],
        },
        {
            "time_offset": 110,  # different row → no duplicate-guard trip
            "metric": metric,
            "description": "Unfiltered spec at t=110 must reach forked pod-0 too",
            "generator": lambda ts, idx: 0.999,
        },
    ]
    _run_generate(
        amc, component, list(specs),
        anomaly_specs=anomaly_specs,
        instances=_three_instances(amc),
        tmp_path=tmp_path,
        total_seconds=120, interval=10.0,
    )

    csv_path = tmp_path / f"{component}.csv"
    # Row 6 = t=60, row 11 = t=110 (interval=10s).
    rows_i0 = _read_rows_for_instance(csv_path, "i0")
    rows_i1 = _read_rows_for_instance(csv_path, "i1")
    rows_i2 = _read_rows_for_instance(csv_path, "i2")
    # At t=60 only i0 should see the filtered override.
    assert rows_i0[6][metric] == "0.111", (
        f"i0 row 6: filtered override; got {rows_i0[6][metric]!r}"
    )
    assert rows_i1[6][metric] != "0.111", "i1 should not see the filtered override"
    assert rows_i2[6][metric] != "0.111", "i2 should not see the filtered override"
    # At t=110 every instance — INCLUDING the previously forked i0 — must
    # see the unfiltered override propagated to its forked buffer.
    assert rows_i0[11][metric] == "0.999", (
        f"i0 row 11: unfiltered override must propagate to forked buffer; "
        f"got {rows_i0[11][metric]!r}"
    )
    assert rows_i1[11][metric] == "0.999"
    assert rows_i2[11][metric] == "0.999"


# ---------------------------------------------------------------------------
# Byte-identity: locked Phase-2 hashes must not move under Phase 4 (no
# built-in scenario uses instance_filter yet).
# ---------------------------------------------------------------------------


def test_default_built_in_scenarios_omit_instance_filter(amc):
    """Phase 4 ships without rewriting any built-in scenario, so the locked
    SHA-256 hashes pinned in test_scenarios / test_instances_per_component
    remain valid. Guards against accidental inclusion of instance_filter
    on a built-in spec that would shift those bytes."""
    for slug, scenario in amc.SCENARIOS.items():
        for component, spec in scenario.primary_specs:
            assert "instance_filter" not in spec, (
                f"SCENARIOS[{slug!r}].primary_specs for {component!r} "
                f"declares instance_filter; that would invalidate the "
                f"locked Phase 2 hashes. Move it out or re-lock the hashes."
            )
        for target, spec in scenario.cascade_specs:
            assert "instance_filter" not in spec, (
                f"SCENARIOS[{slug!r}].cascade_specs for {target!r} "
                f"declares instance_filter; that would invalidate the "
                f"locked Phase 2 hashes."
            )
