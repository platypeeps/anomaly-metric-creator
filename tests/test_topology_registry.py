"""Phase 1 (VER-143) topology scaffolding tests.

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
    assert fields == {"target", "weight", "saturation"}
    rebuilt = amc.Edge(
        target=edge.target, weight=edge.weight, saturation=edge.saturation
    )
    assert rebuilt == edge
    # default values
    bare = amc.Edge(target="database")
    assert bare.weight == 1.0
    assert bare.saturation is None


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
    """A well-behaved callable weight (ndarray -> ndarray) must pass validation."""
    src, tgt = list(amc.COMPONENTS.keys())[:2]
    patched = {src: [amc.Edge(target=tgt, weight=lambda x: np.asarray(x) * 2.0)]}
    monkeypatch.setattr(amc, "TOPOLOGY", patched)
    amc._validate_topology()  # must not raise


def test_validate_topology_rejects_non_edge_entry(amc, monkeypatch):
    """Each edge must be an ``Edge`` instance; raw tuples / dicts are rejected."""
    src, tgt = list(amc.COMPONENTS.keys())[:2]
    patched = {src: [(tgt, 1.0)]}  # tuple instead of Edge
    monkeypatch.setattr(amc, "TOPOLOGY", patched)
    with pytest.raises(ValueError, match=r"Edge"):
        amc._validate_topology()
