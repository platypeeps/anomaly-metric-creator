"""Phase 1 topology scaffolding tests.

These tests cover the structural-only ``TOPOLOGY`` constant, ``Edge`` /
``SaturationParams`` dataclasses, and ``_validate_topology`` import-time
validator. No behavior change should be observable from the rest of the
script — every other test in the suite must still pass byte-identically.
"""
from __future__ import annotations

import dataclasses

import numpy as np
import pytest


def test_topology_is_dict_keyed_by_components(amc):
    """``TOPOLOGY`` is a dict whose keys are a subset of ``COMPONENTS`` keys."""
    assert isinstance(amc.TOPOLOGY, dict)
    known = set(amc.COMPONENTS.keys())
    unknown_sources = set(amc.TOPOLOGY.keys()) - known
    assert not unknown_sources, (
        f"TOPOLOGY source nodes must exist in COMPONENTS; "
        f"unknown: {sorted(unknown_sources)}"
    )


def test_topology_edge_targets_in_components(amc):
    """Every ``Edge.target`` referenced anywhere in ``TOPOLOGY`` is a component."""
    known = set(amc.COMPONENTS.keys())
    for source, edges in amc.TOPOLOGY.items():
        assert isinstance(edges, list), (
            f"TOPOLOGY[{source!r}] must be a list of Edge, got {type(edges).__name__}"
        )
        for edge in edges:
            assert isinstance(edge, amc.Edge), (
                f"TOPOLOGY[{source!r}] contains a non-Edge entry: {edge!r}"
            )
            assert edge.target in known, (
                f"TOPOLOGY[{source!r}] -> {edge.target!r} is not in COMPONENTS"
            )


def test_topology_v1_graph_present(amc):
    """v1 graph from the design must be declared, even though it's unused at phase 1."""
    # loadbalancer -> apigateway
    sources = amc.TOPOLOGY
    assert "loadbalancer" in sources, "loadbalancer must be a topology source"
    lb_targets = {e.target for e in sources["loadbalancer"]}
    assert "apigateway" in lb_targets, "loadbalancer -> apigateway edge required"

    # apigateway -> authservice / cacheservice / database (and optional others)
    assert "apigateway" in sources, "apigateway must be a topology source"
    api_targets = {e.target for e in sources["apigateway"]}
    assert {"authservice", "cacheservice", "database"} <= api_targets, (
        "apigateway must fan out to authservice, cacheservice, and database"
    )

    # cacheservice -> database with a callable weight (cache-miss derived)
    assert "cacheservice" in sources, "cacheservice must be a topology source"
    cs_to_db = next(
        (e for e in sources["cacheservice"] if e.target == "database"), None
    )
    assert cs_to_db is not None, "cacheservice -> database edge required"
    assert callable(cs_to_db.weight), (
        "cacheservice -> database weight must be a callable (cache-miss rate)"
    )


def test_topology_llm_saturation_edge_declared(amc):
    """A saturation-bearing edge targeting llm_analytics must exist (placeholder for phase 5)."""
    saturation_edges = [
        (source, edge)
        for source, edges in amc.TOPOLOGY.items()
        for edge in edges
        if edge.saturation is not None and edge.target == "llm_analytics"
    ]
    assert saturation_edges, (
        "Expected at least one edge targeting llm_analytics with SaturationParams "
        "(token-throttle placeholder for phase 5)."
    )
    for _source, edge in saturation_edges:
        assert isinstance(edge.saturation, amc.SaturationParams), (
            f"Edge.saturation must be a SaturationParams instance, "
            f"got {type(edge.saturation).__name__}"
        )


def test_edge_dataclass_round_trip(amc):
    """``Edge`` is a frozen dataclass; constructing from its repr fields round-trips."""
    sat = amc.SaturationParams(midpoint=0.5, steepness=4.0)
    edge = amc.Edge(target="apigateway", weight=0.7, saturation=sat)
    fields = {f.name for f in dataclasses.fields(amc.Edge)}
    assert fields == {
        "target", "weight", "saturation", "signal", "correlation_threshold",
    }
    rebuilt = amc.Edge(
        target=edge.target,
        weight=edge.weight,
        saturation=edge.saturation,
        signal=edge.signal,
        correlation_threshold=edge.correlation_threshold,
    )
    assert rebuilt == edge
    # default values
    bare = amc.Edge(target="database")
    assert bare.weight == 1.0
    assert bare.saturation is None
    assert bare.signal is None
    assert bare.correlation_threshold is None


def test_saturation_params_dataclass_round_trip(amc):
    """``SaturationParams`` is a frozen dataclass with default zero gains."""
    fields = {f.name for f in dataclasses.fields(amc.SaturationParams)}
    assert fields == {"midpoint", "steepness", "latency_gain", "error_gain"}
    sp = amc.SaturationParams(midpoint=0.8, steepness=6.0)
    assert sp.latency_gain == 0.0
    assert sp.error_gain == 0.0
    rebuilt = amc.SaturationParams(
        midpoint=sp.midpoint,
        steepness=sp.steepness,
        latency_gain=sp.latency_gain,
        error_gain=sp.error_gain,
    )
    assert rebuilt == sp


def test_edge_is_frozen(amc):
    """``Edge`` must be frozen so topology declarations cannot be mutated in place."""
    edge = amc.Edge(target="apigateway")
    with pytest.raises(dataclasses.FrozenInstanceError):
        edge.target = "database"  # type: ignore[misc]


def test_saturation_params_is_frozen(amc):
    """``SaturationParams`` must be frozen for the same reason."""
    sp = amc.SaturationParams(midpoint=0.5, steepness=4.0)
    with pytest.raises(dataclasses.FrozenInstanceError):
        sp.midpoint = 0.9  # type: ignore[misc]


def test_validate_topology_accepts_current_registry(amc):
    """The shipped ``TOPOLOGY`` registry must pass ``_validate_topology()``."""
    amc._validate_topology()  # must not raise


def test_validate_topology_rejects_unknown_target(amc, monkeypatch):
    """Out-of-catalog ``target`` is rejected at import-time with ``ValueError``."""
    patched = {
        "loadbalancer": [amc.Edge(target="not_a_real_component")],
    }
    monkeypatch.setattr(amc, "TOPOLOGY", patched)
    with pytest.raises(ValueError, match=r"not_a_real_component"):
        amc._validate_topology()


def test_validate_topology_rejects_unknown_source(amc, monkeypatch):
    """Out-of-catalog source key is rejected at import-time with ``ValueError``."""
    valid_target = next(iter(amc.COMPONENTS.keys()))
    patched = {
        "ghost_component": [amc.Edge(target=valid_target)],
    }
    monkeypatch.setattr(amc, "TOPOLOGY", patched)
    with pytest.raises(ValueError, match=r"ghost_component"):
        amc._validate_topology()


def test_validate_topology_rejects_callable_weight_not_accepting_ndarray(amc, monkeypatch):
    """A callable weight that does not accept a numpy array is rejected."""
    src, tgt = list(amc.COMPONENTS.keys())[:2]

    def bad_weight():  # zero positional params; cannot take an ndarray
        return 0.0

    patched = {src: [amc.Edge(target=tgt, weight=bad_weight)]}
    monkeypatch.setattr(amc, "TOPOLOGY", patched)
    with pytest.raises(ValueError, match=r"weight"):
        amc._validate_topology()


def test_validate_topology_rejects_callable_weight_that_raises_on_ndarray(amc, monkeypatch):
    """A callable weight that raises when handed a numpy array is rejected."""
    src, tgt = list(amc.COMPONENTS.keys())[:2]

    def raising_weight(arr):
        raise RuntimeError("synthetic failure")

    patched = {src: [amc.Edge(target=tgt, weight=raising_weight)]}
    monkeypatch.setattr(amc, "TOPOLOGY", patched)
    with pytest.raises(ValueError, match=r"weight"):
        amc._validate_topology()


def test_validate_topology_accepts_callable_weight_taking_ndarray(amc, monkeypatch):
    """A well-behaved callable weight (ndarray -> ndarray) with a matching ``signal`` passes validation."""
    src, tgt = list(amc.COMPONENTS.keys())[:2]
    patched = {
        src: [
            amc.Edge(
                target=tgt,
                weight=lambda x: np.asarray(x) * 2.0,
                signal=lambda cols: np.zeros(3, dtype=np.float64),
            )
        ]
    }
    monkeypatch.setattr(amc, "TOPOLOGY", patched)
    amc._validate_topology()  # must not raise


def test_validate_topology_rejects_non_edge_entry(amc, monkeypatch):
    """Each edge must be an ``Edge`` instance; raw tuples / dicts are rejected."""
    src, tgt = list(amc.COMPONENTS.keys())[:2]
    patched = {src: [(tgt, 1.0)]}  # tuple instead of Edge
    monkeypatch.setattr(amc, "TOPOLOGY", patched)
    with pytest.raises(ValueError, match=r"Edge"):
        amc._validate_topology()


def test_validate_topology_rejects_negative_constant_weight(amc, monkeypatch):
    """Constant ``Edge.weight`` < 0 is rejected at import-time."""
    src, tgt = list(amc.COMPONENTS.keys())[:2]
    patched = {src: [amc.Edge(target=tgt, weight=-0.5)]}
    monkeypatch.setattr(amc, "TOPOLOGY", patched)
    with pytest.raises(ValueError, match=r"non-negative"):
        amc._validate_topology()


def test_validate_topology_rejects_nan_constant_weight(amc, monkeypatch):
    """NaN constant ``Edge.weight`` is rejected at import-time."""
    src, tgt = list(amc.COMPONENTS.keys())[:2]
    patched = {src: [amc.Edge(target=tgt, weight=float("nan"))]}
    monkeypatch.setattr(amc, "TOPOLOGY", patched)
    with pytest.raises(ValueError, match=r"finite"):
        amc._validate_topology()


def test_validate_topology_rejects_inf_constant_weight(amc, monkeypatch):
    """Infinite constant ``Edge.weight`` is rejected at import-time."""
    src, tgt = list(amc.COMPONENTS.keys())[:2]
    patched = {src: [amc.Edge(target=tgt, weight=float("inf"))]}
    monkeypatch.setattr(amc, "TOPOLOGY", patched)
    with pytest.raises(ValueError, match=r"finite"):
        amc._validate_topology()


def test_validate_topology_rejects_bool_constant_weight(amc, monkeypatch):
    """``bool`` constant ``Edge.weight`` is rejected even though it subclasses ``int``."""
    src, tgt = list(amc.COMPONENTS.keys())[:2]
    patched = {src: [amc.Edge(target=tgt, weight=True)]}
    monkeypatch.setattr(amc, "TOPOLOGY", patched)
    with pytest.raises(ValueError, match=r"finite non-negative"):
        amc._validate_topology()


def test_validate_topology_rejects_non_numeric_constant_weight(amc, monkeypatch):
    """Non-numeric, non-callable ``Edge.weight`` (e.g. a string) is rejected."""
    src, tgt = list(amc.COMPONENTS.keys())[:2]
    patched = {src: [amc.Edge(target=tgt, weight="0.5")]}  # type: ignore[arg-type]
    monkeypatch.setattr(amc, "TOPOLOGY", patched)
    with pytest.raises(ValueError, match=r"finite non-negative"):
        amc._validate_topology()


def test_validate_topology_accepts_zero_constant_weight(amc, monkeypatch):
    """A ``0`` constant ``Edge.weight`` (e.g. saturation-only placeholder) is accepted."""
    src, tgt = list(amc.COMPONENTS.keys())[:2]
    patched = {src: [amc.Edge(target=tgt, weight=0)]}
    monkeypatch.setattr(amc, "TOPOLOGY", patched)
    amc._validate_topology()  # must not raise


def test_validate_topology_accepts_int_constant_weight(amc, monkeypatch):
    """An ``int`` constant ``Edge.weight`` is accepted (auto-coerces in numpy math)."""
    src, tgt = list(amc.COMPONENTS.keys())[:2]
    patched = {src: [amc.Edge(target=tgt, weight=2)]}
    monkeypatch.setattr(amc, "TOPOLOGY", patched)
    amc._validate_topology()  # must not raise


# ----------------------------------------------------------------------
# phase 7: ``Edge.correlation_threshold`` field invariants
# enforced at import-time by ``_validate_topology`` so the validator-
# side override cannot smuggle in NaN/inf/bool/out-of-range thresholds.
# ----------------------------------------------------------------------
@pytest.mark.parametrize("bad_value", [
    True,
    float("nan"),
    float("inf"),
    float("-inf"),
    1.5,    # above the (-1, 1] interval
    -1.0,   # on the open boundary (-1, ...
    -2.0,
    "0.9",  # not numeric
])
def test_validate_topology_rejects_bad_correlation_threshold(
    amc, monkeypatch, bad_value,
):
    src, tgt = list(amc.COMPONENTS.keys())[:2]
    patched = {src: [amc.Edge(target=tgt, correlation_threshold=bad_value)]}
    monkeypatch.setattr(amc, "TOPOLOGY", patched)
    with pytest.raises(ValueError, match="correlation_threshold"):
        amc._validate_topology()


@pytest.mark.parametrize("good_value", [None, 0.0, 0.5, 0.85, 1.0])
def test_validate_topology_accepts_good_correlation_threshold(
    amc, monkeypatch, good_value,
):
    src, tgt = list(amc.COMPONENTS.keys())[:2]
    patched = {src: [amc.Edge(target=tgt, correlation_threshold=good_value)]}
    monkeypatch.setattr(amc, "TOPOLOGY", patched)
    amc._validate_topology()  # must not raise


def test_topology_default_correlation_threshold_constant(amc):
    """The default threshold sits at the issue acceptance bound (0.85)."""
    assert amc._TOPOLOGY_DEFAULT_CORRELATION_THRESHOLD == 0.85


def test_resolve_edge_correlation_threshold_falls_back_to_default(amc):
    """An edge declared without ``correlation_threshold`` resolves to the
    module-level default."""
    # apigateway -> authservice in the live TOPOLOGY has no override.
    assert amc._resolve_edge_correlation_threshold(
        "apigateway", "authservice"
    ) == amc._TOPOLOGY_DEFAULT_CORRELATION_THRESHOLD


def test_resolve_edge_correlation_threshold_falls_back_for_unknown_edge(amc):
    """A schema-declared edge that no longer exists in the live TOPOLOGY
    falls back to the default (graceful degradation when the build's
    edge set drifts from the schema's snapshot)."""
    assert amc._resolve_edge_correlation_threshold(
        "loadbalancer", "database"
    ) == amc._TOPOLOGY_DEFAULT_CORRELATION_THRESHOLD


# ----------------------------------------------------------------------
# phase 4: ``Edge.saturation`` field invariants enforced at
# import-time by ``_validate_topology`` (mirroring the constant-weight
# checks above so ``_apply_saturation`` cannot silently consume bad
# values).
# ----------------------------------------------------------------------
def _edge_with_saturation(amc, **overrides):
    """Construct an Edge carrying SaturationParams patched with overrides.

    Defaults to params that ``_validate_saturation_params`` accepts, so
    the test only exercises the field under test.
    """
    src, tgt = list(amc.COMPONENTS.keys())[:2]
    defaults = dict(midpoint=100.0, steepness=6.0,
                    latency_gain=0.4, error_gain=0.01)
    defaults.update(overrides)
    sat = amc.SaturationParams(**defaults)
    return src, tgt, amc.Edge(target=tgt, weight=1.0, saturation=sat)


@pytest.mark.parametrize("field", ["midpoint", "steepness"])
def test_validate_topology_rejects_zero_saturation_positive_field(
    amc, monkeypatch, field
):
    """``midpoint`` and ``steepness`` must be > 0 (zero would divide /
    collapse the logistic to a constant)."""
    src, _tgt, edge = _edge_with_saturation(amc, **{field: 0.0})
    monkeypatch.setattr(amc, "TOPOLOGY", {src: [edge]})
    with pytest.raises(ValueError, match=field):
        amc._validate_topology()


@pytest.mark.parametrize(
    "field", ["midpoint", "steepness", "latency_gain", "error_gain"],
)
def test_validate_topology_rejects_negative_saturation_field(
    amc, monkeypatch, field
):
    """No saturation field accepts a negative value (negative midpoint /
    steepness inverts the curve; negative gains flip the sign of the
    contribution)."""
    src, _tgt, edge = _edge_with_saturation(amc, **{field: -1.0})
    monkeypatch.setattr(amc, "TOPOLOGY", {src: [edge]})
    with pytest.raises(ValueError, match=field):
        amc._validate_topology()


@pytest.mark.parametrize(
    "field", ["midpoint", "steepness", "latency_gain", "error_gain"],
)
def test_validate_topology_rejects_nan_saturation_field(
    amc, monkeypatch, field
):
    """``NaN`` in any saturation field is rejected at import-time."""
    src, _tgt, edge = _edge_with_saturation(amc, **{field: float("nan")})
    monkeypatch.setattr(amc, "TOPOLOGY", {src: [edge]})
    with pytest.raises(ValueError, match=r"finite"):
        amc._validate_topology()


@pytest.mark.parametrize(
    "field", ["midpoint", "steepness", "latency_gain", "error_gain"],
)
def test_validate_topology_rejects_inf_saturation_field(
    amc, monkeypatch, field
):
    """``inf`` in any saturation field is rejected at import-time."""
    src, _tgt, edge = _edge_with_saturation(amc, **{field: float("inf")})
    monkeypatch.setattr(amc, "TOPOLOGY", {src: [edge]})
    with pytest.raises(ValueError, match=r"finite"):
        amc._validate_topology()


@pytest.mark.parametrize(
    "field", ["midpoint", "steepness", "latency_gain", "error_gain"],
)
def test_validate_topology_rejects_bool_saturation_field(
    amc, monkeypatch, field
):
    """``bool`` is an ``int`` subtype, so ``True`` would otherwise slip
    through; reject it explicitly to mirror the constant-weight rule."""
    src, _tgt, edge = _edge_with_saturation(amc, **{field: True})
    monkeypatch.setattr(amc, "TOPOLOGY", {src: [edge]})
    with pytest.raises(ValueError, match=field):
        amc._validate_topology()


def test_validate_topology_rejects_non_saturationparams_saturation(
    amc, monkeypatch
):
    """``Edge.saturation`` must be ``None`` or a ``SaturationParams``."""
    src, tgt = list(amc.COMPONENTS.keys())[:2]
    bogus = amc.Edge(target=tgt, weight=1.0, saturation="not-a-saturation")  # type: ignore[arg-type]
    monkeypatch.setattr(amc, "TOPOLOGY", {src: [bogus]})
    with pytest.raises(ValueError, match=r"SaturationParams"):
        amc._validate_topology()


def test_validate_topology_accepts_zero_gain_saturation(amc, monkeypatch):
    """Zero ``latency_gain`` and ``error_gain`` are valid — the LLM
    phase-5 placeholder edge relies on this."""
    src, _tgt, edge = _edge_with_saturation(
        amc, latency_gain=0.0, error_gain=0.0,
    )
    monkeypatch.setattr(amc, "TOPOLOGY", {src: [edge]})
    amc._validate_topology()  # must not raise


def test_validate_topology_rejects_callable_weight_without_signal(amc, monkeypatch):
    """A callable ``Edge.weight`` paired with ``signal=None`` is rejected.

    The composer feeds ``edge.signal(upstream_cols)``'s return value
    straight into ``edge.weight(signal)``; without a signal the edge
    would silently never fire.
    """
    src, tgt = list(amc.COMPONENTS.keys())[:2]
    patched = {
        src: [
            amc.Edge(
                target=tgt,
                weight=lambda x: np.asarray(x) * 2.0,
                signal=None,
            )
        ]
    }
    monkeypatch.setattr(amc, "TOPOLOGY", patched)
    with pytest.raises(ValueError, match=r"signal"):
        amc._validate_topology()


def test_validate_topology_rejects_constant_weight_with_signal(amc, monkeypatch):
    """A constant ``Edge.weight`` paired with a non-None ``signal`` is rejected.

    Signal is meaningless for constant-weight edges because the composer
    never reads it; rejecting the combination up front prevents a stale
    signal from being silently ignored.
    """
    src, tgt = list(amc.COMPONENTS.keys())[:2]
    patched = {
        src: [
            amc.Edge(
                target=tgt,
                weight=0.5,
                signal=lambda cols: np.zeros(3, dtype=np.float64),
            )
        ]
    }
    monkeypatch.setattr(amc, "TOPOLOGY", patched)
    with pytest.raises(ValueError, match=r"signal"):
        amc._validate_topology()


def test_validate_topology_rejects_signal_that_raises(amc, monkeypatch):
    """A ``signal`` callable that raises on the captured-column probe is rejected."""
    src, tgt = list(amc.COMPONENTS.keys())[:2]

    def raising_signal(cols):
        raise RuntimeError("synthetic signal failure")

    patched = {
        src: [
            amc.Edge(
                target=tgt,
                weight=lambda x: np.asarray(x) * 2.0,
                signal=raising_signal,
            )
        ]
    }
    monkeypatch.setattr(amc, "TOPOLOGY", patched)
    with pytest.raises(ValueError, match=r"signal"):
        amc._validate_topology()


def test_validate_topology_rejects_signal_returning_non_ndarray(amc, monkeypatch):
    """A ``signal`` callable that returns a scalar (not ``ndarray`` / ``None``) is rejected."""
    src, tgt = list(amc.COMPONENTS.keys())[:2]

    def scalar_signal(cols):
        return 0.5  # plain float, not an ndarray and not None

    patched = {
        src: [
            amc.Edge(
                target=tgt,
                weight=lambda x: np.asarray(x) * 2.0,
                signal=scalar_signal,
            )
        ]
    }
    monkeypatch.setattr(amc, "TOPOLOGY", patched)
    with pytest.raises(ValueError, match=r"signal"):
        amc._validate_topology()


# ----------------------------------------------------------------------
# the ``cacheservice -> database`` callable weight must read
# the database ``queries_per_sec`` baseline live from ``COMPONENTS`` on
# every invocation. PR #47 originally baked the value into a module-load
# constant (``_DATABASE_QPS_BASE``), which silently produced stale
# weights under any monkeypatch / plugin override / future spec
# rescaling. Pin the live-lookup invariant so a future refactor cannot
# silently regress back to import-time capture.
# ----------------------------------------------------------------------
def test_cacheservice_to_database_weight_reads_live_components(amc, monkeypatch):
    """Doubling ``COMPONENTS['database'].queries_per_sec.base`` must double
    the callable weight's output for the same miss-ratio input."""
    cs_to_db = next(
        e for e in amc.TOPOLOGY["cacheservice"] if e.target == "database"
    )
    miss_ratio = np.array([0.0, 0.04, 0.5, 1.0], dtype=np.float64)
    baseline_qps = amc._component_metric_base("database", "queries_per_sec")
    assert baseline_qps > 0.0, (
        "baseline guard: COMPONENTS['database'].queries_per_sec.base must "
        "be positive for the doubling check to be meaningful"
    )
    expected_before = miss_ratio * baseline_qps
    got_before = np.asarray(cs_to_db.weight(miss_ratio), dtype=np.float64)
    np.testing.assert_allclose(got_before, expected_before, rtol=0, atol=0)

    # Rebuild the database catalog with queries_per_sec.base doubled,
    # leaving every other MetricSpec untouched. A module-load capture
    # would ignore this monkeypatch and keep returning the original
    # baseline_qps; the live-lookup implementation must pick it up.
    new_base = baseline_qps * 2.0
    patched_specs = [
        dataclasses.replace(spec, base=new_base)
        if spec.name == "queries_per_sec"
        else spec
        for spec in amc.COMPONENTS["database"]
    ]
    monkeypatch.setitem(amc.COMPONENTS, "database", patched_specs)

    expected_after = miss_ratio * new_base
    got_after = np.asarray(cs_to_db.weight(miss_ratio), dtype=np.float64)
    # Strict regression assertion: a stale module-load capture would
    # return ``expected_before`` here regardless of the monkeypatch.
    # Assert this first so a regression yields the actionable message.
    assert not np.allclose(got_after, expected_before), (
        "cacheservice -> database callable weight ignored the monkeypatched "
        "COMPONENTS['database'].queries_per_sec.base; the lambda must read "
        "the baseline live from COMPONENTS on every call, not capture it "
        "at module load (regression guard)."
    )
    np.testing.assert_allclose(got_after, expected_after, rtol=0, atol=0)


def test_component_metric_base_reads_live_components(amc, monkeypatch):
    """``_component_metric_base`` is the live-lookup helper the callable
    weight relies on; pin the helper itself so the per-edge invariant
    above cannot be silently broken by a refactor that swaps the helper
    body for a cached value."""
    baseline = amc._component_metric_base("database", "queries_per_sec")
    new_base = baseline + 12345.0
    patched_specs = [
        dataclasses.replace(spec, base=new_base)
        if spec.name == "queries_per_sec"
        else spec
        for spec in amc.COMPONENTS["database"]
    ]
    monkeypatch.setitem(amc.COMPONENTS, "database", patched_specs)
    assert amc._component_metric_base("database", "queries_per_sec") == new_base
