"""Topology graph models, registries, and validation helpers.

Extracted from ``legacy.py`` as part of the generation/topology decomposition.
The shipped graph lives here; ``legacy.py`` configures live callbacks before
running the import-time validators so monkeypatched legacy registries stay
visible to the historic compatibility surface.
"""

from __future__ import annotations

import math
import weakref
from typing import Callable

try:
    import numpy as np
except ModuleNotFoundError as exc:
    if exc.name not in {None, "numpy"}:
        raise
    raise SystemExit(
        "Missing required dependency: numpy\n"
        "Install this project into the Python you are using, for example:\n"
        "  python3 -m pip install -e .\n"
        "or create the documented dev environment:\n"
        "  python3 -m venv .venv\n"
        "  .venv/bin/pip install -e '.[dev]'\n"
    ) from None

from .catalog import COMPONENTS
from .topology_models import Edge, SaturationParams
from .topology_registry import (
    _TOPOLOGY_COUPLE_NOISE_STD as _TOPOLOGY_COUPLE_NOISE_STD,
    _TOPOLOGY_LOAD_METRICS,
    _TOPOLOGY_SATURATION_TARGETS,
)

_DEFAULT_RUNTIME_KEY = "__default__"
_topology_runtimes = {}
_active_topology_runtime_key = _DEFAULT_RUNTIME_KEY


def _weak_runtime_getter(getter: Callable, *, runtime_key: str) -> weakref.ReferenceType:
    """Keep extracted-module runtime hooks from retaining legacy module copies."""
    def discard_runtime(_ref, key=runtime_key):
        _topology_runtimes.pop(key, None)

    try:
        return weakref.ref(getter, discard_runtime)
    except TypeError as exc:
        raise TypeError("topology runtime getters must be weak-referenceable") from exc


def _configure_topology_runtime(
    *,
    get_components: Callable[[], dict],
    get_topology: Callable[[], dict[str, list[object]]],
    get_topology_load_metrics: Callable[[], dict[str, tuple[str, tuple[str, ...]]]],
    get_topology_saturation_targets: Callable[[], dict[str, tuple[tuple[str, ...], tuple[str, ...]]]],
    runtime_key: str = _DEFAULT_RUNTIME_KEY,
    activate: bool = False,
) -> None:
    """Wire live registry access from ``legacy.py`` without importing it."""
    global _active_topology_runtime_key
    _topology_runtimes[runtime_key] = {
        "get_components": _weak_runtime_getter(get_components, runtime_key=runtime_key),
        "get_topology": _weak_runtime_getter(get_topology, runtime_key=runtime_key),
        "get_topology_load_metrics": _weak_runtime_getter(
            get_topology_load_metrics,
            runtime_key=runtime_key,
        ),
        "get_topology_saturation_targets": _weak_runtime_getter(
            get_topology_saturation_targets,
            runtime_key=runtime_key,
        ),
    }
    if activate:
        _active_topology_runtime_key = runtime_key


def _normalize_runtime_key(runtime_key: str | None) -> str:
    return _active_topology_runtime_key if runtime_key is None else runtime_key


def _topology_runtime_getter(runtime_key: str | None, key: str) -> Callable | None:
    runtime_key = _normalize_runtime_key(runtime_key)
    runtime = _topology_runtimes.get(runtime_key)
    if runtime is None:
        return None
    getter = runtime[key]()
    if getter is None:
        _topology_runtimes.pop(runtime_key, None)
        raise RuntimeError("topology runtime is no longer available")
    return getter


def _runtime_components(runtime_key: str | None = None) -> dict:
    getter = _topology_runtime_getter(runtime_key, "get_components")
    if getter is None:
        return COMPONENTS
    return getter()


def _runtime_topology(runtime_key: str | None = None) -> dict[str, list[object]]:
    getter = _topology_runtime_getter(runtime_key, "get_topology")
    if getter is None:
        return TOPOLOGY
    return getter()


def _runtime_topology_load_metrics(
    runtime_key: str | None = None,
) -> dict[str, tuple[str, tuple[str, ...]]]:
    getter = _topology_runtime_getter(runtime_key, "get_topology_load_metrics")
    if getter is None:
        return _TOPOLOGY_LOAD_METRICS
    return getter()


def _runtime_topology_saturation_targets(
    runtime_key: str | None = None,
) -> dict[str, tuple[tuple[str, ...], tuple[str, ...]]]:
    getter = _topology_runtime_getter(runtime_key, "get_topology_saturation_targets")
    if getter is None:
        return _TOPOLOGY_SATURATION_TARGETS
    return getter()


# ------------------------------------------------------------------
# Topology graph (phase 1 — structural-only).
# ------------------------------------------------------------------
# Directed service-call graph. ``TOPOLOGY[source]`` lists the ``Edge``
# instances downstream of ``source``; both source keys and ``Edge.target``
# values are component names from ``COMPONENTS``. Under the default
# ``--topology-mode realistic`` (phase 6 flag day) the graph
# is consumed by ``_compose_topology_coupled_specs`` (phase 2/3:
# rewrites downstream load-metric baselines from upstream RPS/token
# columns) and ``_compose_topology_saturation_specs`` (phase 4/5:
# lifts downstream latency/error specs via the logistic saturation
# curve). The graph is always read: the phase-9 flag day removed the
# ``--topology-mode independent`` no-topology contrast alias.
#
# v1 graph (per design):
#   loadbalancer -> apigateway                   (constant weight 1.0)
#   apigateway   -> authservice (0.3),           (request fan-out shares;
#                   cacheservice (0.4),           the weights here sum to 1
#                   database (0.3)                so the phase-2 two-pass
#                                                 generation can treat them
#                                                 as routing fractions)
#   cacheservice -> database                     (weight = callable on
#                                                 cache_miss / total rate)
#   apigateway   -> llm_analytics                (phase 5 token-
#                                                 throttle: positive
#                                                 weight couples
#                                                 input_tokens_per_sec to
#                                                 apigateway RPS; non-
#                                                 zero gains lift LLM
#                                                 latency / error as
#                                                 apigateway saturates)
#
# Cascade-vs-topology overlap: several SCENARIOS already encode pairwise
# blast-radius (e.g. auth -> gateway, cache -> DB) via cascade_specs. The
# topology graph is a structural orthogonal view — it describes *normal*
# request flow, not anomaly propagation — so the two are intentionally
# allowed to overlap. The realistic-mode pipeline applies topology
# coupling and saturation to the natural baseline before the per-row
# anomaly override loop runs, so a cascade write at row i still wins at
# exactly that row regardless of the topology-derived baseline.
def _component_metric_base(
    component: str, metric: str, *, runtime_key: str | None = None
) -> float:
    """Look up the natural ``MetricSpec.base`` for ``component[metric]``.

    Returns ``0.0`` when the metric is not in the component's catalog so
    callers can branch on the falsy value without raising. Coupling uses
    the natural baseline to map upstream load (in upstream units) to the
    downstream metric's scale (e.g. apigateway's ~800 rps to database's
    ~25k qps). Defined above ``TOPOLOGY`` so the cacheservice → database
    callable lambda can reference it at the import-time smoke test in
    ``_validate_topology``.
    """
    for spec in _runtime_components(runtime_key).get(component, ()):
        if spec.name == metric:
            return float(spec.base)
    return 0.0


def _cache_miss_ratio_signal(
    cols: dict[str, np.ndarray],
) -> "np.ndarray | None":
    """Per-edge ``Edge.signal`` for the ``cacheservice -> database`` edge.

    Receives ``cacheservice``'s captured load columns and returns the
    per-row cache-miss ratio ``cache_misses / (cache_hits + cache_misses)``
    (0.0 where the combined total is non-positive). Returns ``None`` when
    either required column is missing — the composer treats this as
    "skip this edge" so a ``--metrics-per-component`` selection that
    trims a required column degrades gracefully instead of raising.
    """
    hits = cols.get("cache_hits")
    misses = cols.get("cache_misses")
    if hits is None or misses is None:
        return None
    total = hits + misses
    return np.divide(
        misses, total,
        out=np.zeros_like(misses, dtype=np.float64),
        where=total > 0,
    )


TOPOLOGY: dict[str, list[Edge]] = {
    "loadbalancer": [
        # phase 4: saturation feedback. ``midpoint`` is the
        # upstream's load value at which the logistic curve sits at 0.5
        # (~80% of the natural peak of ~1080 rps for loadbalancer). The
        # gains shape latency and error responses as the gateway nears
        # capacity. See ``_apply_saturation`` for the exact formula and
        # ``_TOPOLOGY_SATURATION_TARGETS`` for the affected downstream
        # latency/error columns.
        Edge(
            target="apigateway", weight=1.0,
            saturation=SaturationParams(
                midpoint=860.0, steepness=6.0,
                latency_gain=0.4, error_gain=0.010,
            ),
        ),
    ],
    "apigateway": [
        # phase 4: saturation feedback on the three fan-out
        # downstreams. ``midpoint`` is ~80% of the apigateway natural
        # peak (~950 rps). ``latency_gain`` scales with each downstream's
        # sensitivity to upstream load: database is most sensitive
        # (heavy I/O), authservice next (per-request crypto work),
        # cacheservice least (in-memory ops). ``error_gain`` follows the
        # same ordering, kept inside the issue's [0.005, 0.02] band.
        Edge(
            target="authservice", weight=0.3,
            saturation=SaturationParams(
                midpoint=760.0, steepness=6.0,
                latency_gain=0.5, error_gain=0.012,
            ),
        ),
        Edge(
            target="cacheservice", weight=0.4,
            saturation=SaturationParams(
                midpoint=760.0, steepness=6.0,
                latency_gain=0.3, error_gain=0.008,
            ),
        ),
        Edge(
            target="database", weight=0.3,
            saturation=SaturationParams(
                midpoint=760.0, steepness=6.0,
                latency_gain=0.6, error_gain=0.015,
            ),
        ),
        # phase 5: LLM token-throttle. Apigateway serves as the
        # token-budget metering authority for LLM-bound traffic, so this
        # edge couples ``llm_analytics.input_tokens_per_sec`` to
        # ``apigateway.requests_per_sec`` (the renormalization in
        # ``_compose_topology_coupled_specs`` reproduces the natural
        # LLM baseline at natural apigateway load regardless of the
        # raw weight magnitude — any positive weight makes the edge
        # active). ``midpoint`` is expressed in apigateway RPS units
        # (same scale as the other apigateway -> * edges) so the
        # saturation curve shifts the LLM-side response in lockstep
        # with the rest of the front-half fan-out. ``latency_gain``
        # sits between authservice (0.5) and database (0.6); the LLM
        # is moderately sensitive to upstream throttle because every
        # token call queues behind the budget. ``error_gain`` follows
        # the same band as the other downstream edges.
        Edge(
            target="llm_analytics",
            weight=1.0,
            saturation=SaturationParams(
                midpoint=760.0, steepness=6.0,
                latency_gain=0.55, error_gain=0.015,
            ),
        ),
    ],
    # Cache miss rate drives extra database load on top of apigateway's
    # routing fraction. ``signal`` is the module-level
    # ``_cache_miss_ratio_signal`` which derives the per-row cache-miss
    # ratio (``cache_misses / (cache_hits + cache_misses)``) from
    # cacheservice's captured columns; the callable ``weight`` then
    # maps that ratio onto the additive QPS contribution to the
    # database baseline: ``weight(miss_ratio) = miss_ratio * base_qps``.
    # At the natural baseline (~4% miss rate, ~25k base QPS) this is
    # ~1000 QPS on top of the apigateway-driven contribution.
    # ``base_qps`` is resolved lazily via ``_component_metric_base`` so
    # the lambda always reads the live ``COMPONENTS`` catalog — matching
    # the constant-weight path's behavior under monkeypatched / test-
    # injected baselines.
    "cacheservice": [
        Edge(
            target="database",
            signal=_cache_miss_ratio_signal,
            weight=lambda miss_ratio: (
                np.asarray(miss_ratio, dtype=np.float64)
                * _component_metric_base("database", "queries_per_sec")
            ),
        ),
    ],
}


def _validate_saturation_params(sat: SaturationParams, *, context: str) -> None:
    """Field-level invariants for a ``SaturationParams`` instance.

    Used by ``_validate_topology()`` at import time on every edge that
    carries saturation, and re-checked at call time inside
    ``_apply_saturation()`` so direct callers (tests, future consumers)
    cannot smuggle in bad params. ``context`` is a short string naming
    the source of the params (an edge identifier or the function name)
    so the raised ``ValueError`` points at the offending site.

    Rejected inputs per field:

    - ``midpoint`` — must be a finite positive non-``bool``
      ``int``/``float``. Zero divides; negative or non-finite
      contaminates ``utilization`` with non-finite values; ``bool`` is
      an ``int`` subtype so ``True`` would otherwise slip through.
    - ``steepness`` — must be a finite positive non-``bool``
      ``int``/``float``. Zero collapses the logistic to a constant
      0.5; negative inverts the curve.
    - ``latency_gain`` / ``error_gain`` — must be finite non-negative
      non-``bool`` ``int``/``float``. The saturation curve models
      load-driven *degradation*: a positive gain raises latency and
      error rate as upstream load climbs. Negative gains would invert
      that physics (saturation reducing latency / pushing
      ``error_offset`` below zero) and, when multiplied across two
      saturating edges into the same downstream, could flip
      ``latency_multiplier`` past zero into negative latency.
    """
    def _check(name: str, value, *, positive: bool) -> None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(
                f"{context}: SaturationParams.{name}={value!r} must be a "
                f"finite {'positive' if positive else 'non-negative'} "
                f"int/float; got {type(value).__name__}."
            )
        if not math.isfinite(value):
            raise ValueError(
                f"{context}: SaturationParams.{name}={value!r} must be "
                f"finite."
            )
        if positive and value <= 0:
            raise ValueError(
                f"{context}: SaturationParams.{name}={value!r} must be > 0."
            )
        if not positive and value < 0:
            raise ValueError(
                f"{context}: SaturationParams.{name}={value!r} must be "
                f">= 0."
            )

    _check("midpoint", sat.midpoint, positive=True)
    _check("steepness", sat.steepness, positive=True)
    _check("latency_gain", sat.latency_gain, positive=False)
    _check("error_gain", sat.error_gain, positive=False)


def _validate_topology(*, runtime_key: str | None = None) -> None:
    """Import-time invariants for ``TOPOLOGY``.

    Catches drift between the topology graph and ``COMPONENTS`` at module
    load so phase 2's two-pass generator can rely on every source and
    target being a real component. Callable weights are smoke-tested with
    a tiny ``np.ndarray`` so a mis-shaped lambda (e.g. zero-arg or scalar-
    only) fails here instead of corrupting the generator's vectorized
    column writes downstream. Each non-``None`` ``Edge.saturation`` has
    its ``SaturationParams`` field invariants enforced via
    ``_validate_saturation_params`` so phase 4's saturation feedback
    cannot silently consume ``NaN``/``inf``/``bool``/negative values.
    """
    components = _runtime_components(runtime_key)
    topology = _runtime_topology(runtime_key)
    topology_load_metrics = _runtime_topology_load_metrics(runtime_key)
    known_components = set(components.keys())
    for source, edges in topology.items():
        if source not in known_components:
            raise ValueError(
                f"TOPOLOGY source {source!r} is not in COMPONENTS; "
                f"known components: {sorted(known_components)}"
            )
        if not isinstance(edges, list):
            raise ValueError(
                f"TOPOLOGY[{source!r}] must be a list of Edge, got "
                f"{type(edges).__name__}"
            )
        for edge in edges:
            if not isinstance(edge, Edge):
                raise ValueError(
                    f"TOPOLOGY[{source!r}] contains a non-Edge entry "
                    f"{edge!r} (type {type(edge).__name__}); every entry "
                    f"must be an Edge instance."
                )
            if edge.target not in known_components:
                raise ValueError(
                    f"TOPOLOGY[{source!r}] -> Edge.target={edge.target!r} "
                    f"is not in COMPONENTS; known components: "
                    f"{sorted(known_components)}"
                )
            if edge.saturation is not None:
                if not isinstance(edge.saturation, SaturationParams):
                    raise ValueError(
                        f"TOPOLOGY[{source!r}] -> {edge.target!r} "
                        f"Edge.saturation={edge.saturation!r} must be a "
                        f"SaturationParams instance or None; got "
                        f"{type(edge.saturation).__name__}."
                    )
                _validate_saturation_params(
                    edge.saturation,
                    context=f"TOPOLOGY[{source!r}] -> {edge.target!r}",
                )
            if edge.correlation_threshold is not None:
                # phase 7: validator-only per-edge override of the
                # default Pearson coupling threshold. ``bool`` is an ``int``
                # subtype so reject it explicitly before the numeric check.
                if (isinstance(edge.correlation_threshold, bool)
                        or not isinstance(
                            edge.correlation_threshold, (int, float)
                        )):
                    raise ValueError(
                        f"TOPOLOGY[{source!r}] -> {edge.target!r} "
                        f"correlation_threshold="
                        f"{edge.correlation_threshold!r} must be a finite "
                        f"float in (-1, 1] or None; got "
                        f"{type(edge.correlation_threshold).__name__}."
                    )
                if not math.isfinite(edge.correlation_threshold):
                    raise ValueError(
                        f"TOPOLOGY[{source!r}] -> {edge.target!r} "
                        f"correlation_threshold="
                        f"{edge.correlation_threshold!r} must be finite."
                    )
                if not -1.0 < edge.correlation_threshold <= 1.0:
                    raise ValueError(
                        f"TOPOLOGY[{source!r}] -> {edge.target!r} "
                        f"correlation_threshold="
                        f"{edge.correlation_threshold!r} must be in the "
                        f"half-open interval (-1, 1]."
                    )
            if callable(edge.weight):
                probe = np.array([0.0, 0.5, 1.0], dtype=np.float64)
                try:
                    result = edge.weight(probe)
                except Exception as exc:
                    raise ValueError(
                        f"TOPOLOGY[{source!r}] -> {edge.target!r} callable "
                        f"weight {edge.weight!r} raised "
                        f"{type(exc).__name__}({exc!r}) when called with a "
                        f"numpy array; callable weights must accept an "
                        f"ndarray and return an ndarray."
                    ) from exc
                if not isinstance(result, np.ndarray):
                    raise ValueError(
                        f"TOPOLOGY[{source!r}] -> {edge.target!r} callable "
                        f"weight {edge.weight!r} returned "
                        f"{type(result).__name__}; callable weights must "
                        f"return a numpy array."
                    )
                # Callable weights require a per-edge signal: the composer
                # feeds ``edge.signal(upstream_cols)``'s return value
                # straight into ``edge.weight(signal)``. Without a signal
                # the composer has no per-row input and would silently
                # skip the edge — exactly the soft footgun this refactor
                # is removing.
                if edge.signal is None:
                    raise ValueError(
                        f"TOPOLOGY[{source!r}] -> {edge.target!r} has "
                        f"callable weight but signal=None; callable "
                        f"weights require a per-edge signal callable."
                    )
                if not callable(edge.signal):
                    raise ValueError(
                        f"TOPOLOGY[{source!r}] -> {edge.target!r} signal="
                        f"{edge.signal!r} must be callable; got "
                        f"{type(edge.signal).__name__}."
                    )
                ups_entry = topology_load_metrics.get(source)
                if ups_entry is None:
                    probe_cols: dict[str, np.ndarray] = {}
                else:
                    canonical_src, supplementary_src = ups_entry
                    # Distinct array per key: real captured columns are
                    # always per-column buffers, and a future signal that
                    # mutates an input in-place (e.g. via ``out=``) must
                    # not silently alias other "columns" in the probe.
                    probe_template = np.array(
                        [0.0, 0.5, 1.0], dtype=np.float64
                    )
                    probe_cols = {
                        name: probe_template.copy()
                        for name in (canonical_src, *supplementary_src)
                        if name
                    }
                try:
                    sig_result = edge.signal(probe_cols)
                except Exception as exc:
                    raise ValueError(
                        f"TOPOLOGY[{source!r}] -> {edge.target!r} signal "
                        f"{edge.signal!r} raised {exc!r} when called with "
                        f"the upstream's captured-column probe; signal "
                        f"callables must accept a dict[str, np.ndarray] "
                        f"and return np.ndarray or None."
                    ) from exc
                if sig_result is not None and not isinstance(
                    sig_result, np.ndarray
                ):
                    raise ValueError(
                        f"TOPOLOGY[{source!r}] -> {edge.target!r} signal "
                        f"returned {type(sig_result).__name__}; signal "
                        f"callables must return np.ndarray or None."
                    )
            else:
                # Constant weight: must be a finite, non-negative scalar.
                # ``bool`` is a subclass of ``int`` so ``isinstance(True,
                # (int, float))`` is True; reject it explicitly before the
                # numeric check.
                if (isinstance(edge.weight, bool)
                        or not isinstance(edge.weight, (int, float))):
                    raise ValueError(
                        f"TOPOLOGY[{source!r}] -> {edge.target!r} weight="
                        f"{edge.weight!r} must be a finite non-negative "
                        f"int/float or a callable (np.ndarray) -> "
                        f"np.ndarray; got {type(edge.weight).__name__}."
                    )
                if not math.isfinite(edge.weight):
                    raise ValueError(
                        f"TOPOLOGY[{source!r}] -> {edge.target!r} weight="
                        f"{edge.weight!r} must be finite."
                    )
                if edge.weight < 0:
                    raise ValueError(
                        f"TOPOLOGY[{source!r}] -> {edge.target!r} weight="
                        f"{edge.weight!r} must be non-negative."
                    )
                # Constant weight: signal is meaningless because the
                # composer never reads it. Reject up-front so an edge
                # author cannot stash a stale signal on a constant edge
                # and assume it will fire.
                if edge.signal is not None:
                    raise ValueError(
                        f"TOPOLOGY[{source!r}] -> {edge.target!r} has "
                        f"constant weight={edge.weight!r} but signal is "
                        f"set; signal is only valid with a callable "
                        f"weight."
                    )

    # Cycle detection (phase 3): the two-pass realistic-mode
    # generator walks TOPOLOGY in Kahn order and expects a DAG. Reject
    # any cycle (including self-loops) at import time so a cyclic edit
    # fails fast instead of silently falling back to COMPONENTS order.
    incoming: dict[str, set[str]] = {}
    for source, edges in topology.items():
        incoming.setdefault(source, set())
        for edge in edges:
            incoming.setdefault(edge.target, set()).add(source)
    remaining = {node: set(deps) for node, deps in incoming.items()}
    while remaining:
        ready = [n for n, deps in remaining.items() if not deps]
        if not ready:
            cycle_nodes = sorted(remaining.keys())
            raise ValueError(
                f"TOPOLOGY must be acyclic; cycle detected among "
                f"nodes {cycle_nodes}"
            )
        for n in ready:
            del remaining[n]
            for deps in remaining.values():
                deps.discard(n)




def _topology_generation_order(
    active_components: set[str], *, runtime_key: str | None = None
) -> list[str]:
    """Return ``active_components`` in topological generation order.

    Roots (no incoming TOPOLOGY edges from any other active component) come
    first; downstream components come after their upstream(s). Only edges
    where both endpoints are in ``active_components`` are considered, so
    ``--components`` filtering naturally restricts the dependency graph.
    Cycles are not expected in TOPOLOGY (``_validate_topology`` rejects
    them at import time, so this branch is defensive dead code); if one
    ever appeared, the fallback flushes *all* remaining nodes — cycle
    members and their not-yet-ready downstreams alike — in one
    ``COMPONENTS``-insertion-order pass so the walk always makes
    forward progress.

    Ties (multiple roots / multiple ready nodes at the same Kahn step)
    break on ``COMPONENTS`` insertion order so the result is deterministic
    regardless of how the caller iterates ``args.components``.
    """
    topology = _runtime_topology(runtime_key)
    components = _runtime_components(runtime_key)
    incoming: dict[str, set[str]] = {c: set() for c in active_components}
    for source, edges in topology.items():
        if source not in active_components:
            continue
        for edge in edges:
            if edge.target in incoming and edge.target != source:
                incoming[edge.target].add(source)
    component_index = {name: i for i, name in enumerate(components.keys())}
    ordered: list[str] = []
    remaining = {c: set(deps) for c, deps in incoming.items()}
    while remaining:
        ready = sorted(
            (c for c, deps in remaining.items() if not deps),
            key=lambda c: component_index[c],
        )
        if not ready:
            ready = sorted(remaining.keys(), key=lambda c: component_index[c])
        for c in ready:
            ordered.append(c)
            del remaining[c]
            for deps in remaining.values():
                deps.discard(c)
    return ordered




def _validate_topology_metric_registries(
    *, runtime_key: str | None = None
) -> None:
    """Import-time validation of the topology *metric* registries.

    ``_validate_topology()`` exhaustively validates ``TOPOLOGY`` itself,
    but the two companion registries that name actual metric columns —
    ``_TOPOLOGY_LOAD_METRICS`` and ``_TOPOLOGY_SATURATION_TARGETS`` —
    were previously unchecked, and every runtime consumer degrades
    *silently* on a miss: a typo'd canonical load metric makes
    ``_component_metric_base`` return 0.0 so the coupling edge is
    skipped; an unregistered saturating source falls through
    ``ups_entry is None``; a typo'd saturation target falls through
    ``name_to_idx.get(...)``. Those soft fallbacks exist to tolerate
    legitimate runtime states (``--metrics-per-component`` trims,
    ``--components`` subsets) — but they also swallowed registry typos,
    so a new edge with a misspelled metric would pass import, generate
    fully decoupled output, and surface only at the opt-in
    ``validate`` subcommand's Pearson check. This validator fails the typo
    at import time instead. Checks:

    * every ``_TOPOLOGY_LOAD_METRICS`` key is a ``COMPONENTS`` key, and
      its canonical + supplementary names all exist in that component's
      *full* metric catalog (the un-trimmed list — trimming is a
      runtime state, not a registry property);
    * every ``_TOPOLOGY_SATURATION_TARGETS`` key is a ``COMPONENTS``
      key, and every latency-family / error-family name exists in that
      component's full catalog;
    * every ``TOPOLOGY`` source with at least one constant-weight or
      saturating outgoing edge has a ``_TOPOLOGY_LOAD_METRICS`` entry
      (the constant-weight composer and the saturation driver both
      read the source's canonical column);
    * every constant-weight edge's *target* has a
      ``_TOPOLOGY_LOAD_METRICS`` entry (the composer rewrites the
      target's own load metrics — a missing entry makes the edge
      silently inert);
    * every saturating edge's target has a
      ``_TOPOLOGY_SATURATION_TARGETS`` entry.

    Mirrored by ``tests/test_topology_registry.py``.
    """
    components = _runtime_components(runtime_key)
    topology = _runtime_topology(runtime_key)
    topology_load_metrics = _runtime_topology_load_metrics(runtime_key)
    topology_saturation_targets = _runtime_topology_saturation_targets(runtime_key)
    catalog_names = {
        comp: {s.name for s in specs} for comp, specs in components.items()
    }
    for comp, entry in topology_load_metrics.items():
        if comp not in components:
            raise ValueError(
                f"_TOPOLOGY_LOAD_METRICS key {comp!r} is not a COMPONENTS key"
            )
        canonical, supplementary = entry
        for metric in (canonical, *supplementary):
            if metric not in catalog_names[comp]:
                raise ValueError(
                    f"_TOPOLOGY_LOAD_METRICS[{comp!r}] names metric "
                    f"{metric!r} which is not in COMPONENTS[{comp!r}]"
                )
    for comp, (latency_metrics, error_metrics) in topology_saturation_targets.items():
        if comp not in components:
            raise ValueError(
                f"_TOPOLOGY_SATURATION_TARGETS key {comp!r} is not a "
                "COMPONENTS key"
            )
        for metric in (*latency_metrics, *error_metrics):
            if metric not in catalog_names[comp]:
                raise ValueError(
                    f"_TOPOLOGY_SATURATION_TARGETS[{comp!r}] names metric "
                    f"{metric!r} which is not in COMPONENTS[{comp!r}]"
                )
    for source, edges in topology.items():
        for edge in edges:
            saturating = edge.saturation is not None and (
                edge.saturation.latency_gain != 0.0
                or edge.saturation.error_gain != 0.0
            )
            if callable(edge.weight) and not saturating:
                # Callable-weight edges read the source's captured
                # columns through their own ``signal``, which
                # ``_validate_topology`` already probes against
                # ``_TOPOLOGY_LOAD_METRICS`` — but only a non-callable
                # weight or a saturating edge *requires* the canonical
                # column below.
                continue
            if source not in topology_load_metrics:
                raise ValueError(
                    f"TOPOLOGY source {source!r} has a constant-weight or "
                    f"saturating edge to {edge.target!r} but no "
                    "_TOPOLOGY_LOAD_METRICS entry; the coupling composer "
                    "and saturation driver would silently skip the edge"
                )
            if not callable(edge.weight) and edge.target not in topology_load_metrics:
                raise ValueError(
                    f"TOPOLOGY constant-weight edge {source!r} -> "
                    f"{edge.target!r} targets a component with no "
                    "_TOPOLOGY_LOAD_METRICS entry; the composer rewrites "
                    "the target's load metrics, so the edge would be "
                    "silently inert"
                )
            if saturating and edge.target not in topology_saturation_targets:
                raise ValueError(
                    f"TOPOLOGY saturating edge {source!r} -> {edge.target!r} "
                    "targets a component with no _TOPOLOGY_SATURATION_TARGETS "
                    "entry; the saturation contribution would be silently "
                    "dropped"
                )
