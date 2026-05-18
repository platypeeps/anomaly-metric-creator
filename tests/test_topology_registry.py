"""Phase 1 scaffolding for the VER-141 topology work.

The ``TOPOLOGY`` constant and the ``Edge`` / ``SaturationParams`` dataclasses
declared in this phase are structural only — no generator code consumes them
yet. These tests pin the structural contract so phase-2 work can build on a
fixed, validated shape.
"""

import dataclasses

import numpy as np
import pytest


# ---------------------------------------------------------------------------
# Dataclass shape
# ---------------------------------------------------------------------------

def test_saturation_params_fields_and_defaults(amc):
    """``SaturationParams`` declares the four fields required by VER-141 phase 5."""
    fields = {f.name: f for f in dataclasses.fields(amc.SaturationParams)}
    assert set(fields.keys()) == {"midpoint", "steepness", "latency_gain", "error_gain"}
    # midpoint and steepness are required; latency_gain / error_gain default to 0.0
    # (zero-gain means "no contribution from this edge").
    params = amc.SaturationParams(midpoint=0.5, steepness=4.0)
    assert params.latency_gain == 0.0
    assert params.error_gain == 0.0


def test_saturation_params_repr_round_trips(amc):
    """``repr`` is stable enough to reconstruct an equivalent dataclass."""
    params = amc.SaturationParams(midpoint=0.8, steepness=6.0,
                                  latency_gain=1.5, error_gain=0.25)
    rebuilt = eval(repr(params), {"SaturationParams": amc.SaturationParams})
    assert rebuilt == params


def test_edge_fields_and_defaults(amc):
    """``Edge`` declares target + weight + saturation with the documented defaults."""
    fields = {f.name: f for f in dataclasses.fields(amc.Edge)}
    assert set(fields.keys()) == {"target", "weight", "saturation"}
    edge = amc.Edge(target="apigateway")
    assert edge.weight == 1.0
    assert edge.saturation is None


def test_edge_is_frozen(amc):
    """Edges are immutable so the registry can't drift at runtime."""
    edge = amc.Edge(target="apigateway")
    with pytest.raises(dataclasses.FrozenInstanceError):
        edge.target = "database"  # type: ignore[misc]


def test_edge_repr_round_trips(amc):
    """``repr`` is stable enough to reconstruct an equivalent dataclass."""
    edge = amc.Edge(target="apigateway", weight=0.42)
    rebuilt = eval(repr(edge), {"Edge": amc.Edge})
    assert rebuilt == edge

    sat_edge = amc.Edge(
        target="database",
        weight=1.0,
        saturation=amc.SaturationParams(midpoint=0.5, steepness=4.0),
    )
    rebuilt_sat = eval(
        repr(sat_edge),
        {"Edge": amc.Edge, "SaturationParams": amc.SaturationParams},
    )
    assert rebuilt_sat == sat_edge


# ---------------------------------------------------------------------------
# TOPOLOGY shape
# ---------------------------------------------------------------------------

def test_topology_is_a_dict_of_edge_lists(amc):
    assert isinstance(amc.TOPOLOGY, dict)
    for source, edges in amc.TOPOLOGY.items():
        assert isinstance(source, str), f"TOPOLOGY source key {source!r} must be str"
        assert isinstance(edges, list), (
            f"TOPOLOGY[{source!r}] must be list[Edge], got {type(edges).__name__}"
        )
        for edge in edges:
            assert isinstance(edge, amc.Edge), (
                f"TOPOLOGY[{source!r}] contains {edge!r}; expected an Edge instance"
            )


def test_topology_sources_are_components(amc):
    unknown = set(amc.TOPOLOGY.keys()) - set(amc.COMPONENTS.keys())
    assert not unknown, f"TOPOLOGY has unknown source component(s): {sorted(unknown)}"


def test_topology_targets_are_components(amc):
    known = set(amc.COMPONENTS.keys())
    for source, edges in amc.TOPOLOGY.items():
        for edge in edges:
            assert edge.target in known, (
                f"TOPOLOGY[{source!r}] edge target {edge.target!r} is not a "
                f"known component"
            )


def test_topology_v1_graph_matches_design(amc):
    """The v1 graph declared by the VER-142 plan must be present verbatim."""
    # loadbalancer → apigateway (weight 1.0)
    lb_edges = amc.TOPOLOGY["loadbalancer"]
    assert len(lb_edges) == 1
    assert lb_edges[0].target == "apigateway"
    assert lb_edges[0].weight == 1.0

    # apigateway → authservice (0.3), cacheservice (0.4), database (0.3)
    gateway_edges = {e.target: e for e in amc.TOPOLOGY["apigateway"]}
    assert set(gateway_edges.keys()) == {"authservice", "cacheservice", "database"}
    assert gateway_edges["authservice"].weight == 0.3
    assert gateway_edges["cacheservice"].weight == 0.4
    assert gateway_edges["database"].weight == 0.3

    # cacheservice → database with callable weight on cache miss rate
    cache_edges = amc.TOPOLOGY["cacheservice"]
    assert len(cache_edges) == 1
    assert cache_edges[0].target == "database"
    assert callable(cache_edges[0].weight), (
        "cacheservice → database edge weight must be a callable on the cache "
        "miss-rate column"
    )

    # llm_analytics carries a saturation placeholder for phase 5.
    llm_edges = amc.TOPOLOGY["llm_analytics"]
    assert len(llm_edges) >= 1
    assert any(e.saturation is not None for e in llm_edges), (
        "llm_analytics must declare at least one edge with a SaturationParams "
        "placeholder (phase 5 token-throttle hook)"
    )


def test_topology_callable_weight_returns_array(amc):
    """Callable weights must accept and return numpy arrays (column-shaped)."""
    sample = np.array([0.0, 0.25, 0.5, 0.75, 1.0])
    for source, edges in amc.TOPOLOGY.items():
        for edge in edges:
            if callable(edge.weight):
                out = edge.weight(sample)
                assert isinstance(out, np.ndarray), (
                    f"TOPOLOGY[{source!r}] edge → {edge.target!r} weight "
                    f"callable returned {type(out).__name__}; expected ndarray"
                )
                assert out.shape == sample.shape, (
                    f"TOPOLOGY[{source!r}] edge → {edge.target!r} weight "
                    f"callable changed shape: in {sample.shape} → out {out.shape}"
                )


# ---------------------------------------------------------------------------
# Import-time validator
# ---------------------------------------------------------------------------

def test_validator_rejects_unknown_target(amc):
    """``_validate_topology`` raises when an edge target is not in COMPONENTS."""
    bad_topology = {
        "loadbalancer": [amc.Edge(target="not_a_component")],
    }
    with pytest.raises(ValueError, match="not_a_component"):
        amc._validate_topology(bad_topology)


def test_validator_rejects_unknown_source(amc):
    """``_validate_topology`` raises when a source key is not in COMPONENTS."""
    bad_topology = {
        "not_a_component": [amc.Edge(target="apigateway")],
    }
    with pytest.raises(ValueError, match="not_a_component"):
        amc._validate_topology(bad_topology)


def test_validator_rejects_non_array_callable_weight(amc):
    """Callable weights that can't accept a numpy array must be rejected."""

    def scalar_only(x: float) -> float:
        # Calling this with a numpy array raises a TypeError because float()
        # on a multi-element array fails.
        return float(x) * 2.0

    bad_topology = {
        "cacheservice": [amc.Edge(target="database", weight=scalar_only)],
    }
    with pytest.raises(ValueError, match="numpy"):
        amc._validate_topology(bad_topology)


def test_validator_accepts_valid_topology(amc):
    """The shipped TOPOLOGY itself must pass the validator (sanity)."""
    # No exception expected; reusing the live registry guards against future
    # additions accidentally tripping the validator without test feedback.
    amc._validate_topology(amc.TOPOLOGY)


def test_validator_rejects_non_edge_entry(amc):
    """List entries that are not Edge instances are rejected."""
    bad_topology = {
        "loadbalancer": [("apigateway", 1.0)],  # tuple, not Edge
    }
    with pytest.raises(ValueError, match="Edge"):
        amc._validate_topology(bad_topology)
