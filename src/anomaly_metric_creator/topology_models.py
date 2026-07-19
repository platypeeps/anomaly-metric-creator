"""Topology graph dataclasses."""

from __future__ import annotations

from dataclasses import dataclass
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

# ------------------------------------------------------------------
# Topology graph dataclasses (phase 1 — structural-only).
# ------------------------------------------------------------------
# The ``TOPOLOGY`` constant below declares directed service-to-service edges
# alongside ``COMPONENTS``. The dataclasses landed first (phase 1)
# so the structural shape stays stable across the two-pass coupling
# generator (phase 2, phase 3) and the saturation
# feedback layer (phase 4, phase 5).
@dataclass(frozen=True)
class SaturationParams:
    """Sigmoid-style saturation parameters attached to a topology edge.

    Read by ``_apply_saturation`` as the parameters of a logistic
    response curve on the source's load metric: latency and error gains
    are added to the target's natural latency / error rate columns once
    load crosses ``midpoint`` at ``steepness``. Zero-gain (the default)
    means the edge declares the saturation point structurally but does
    not contribute to the target's metrics — handy for placeholder
    edges declared at phase 1 that have not been wired up to gains yet.
    """
    midpoint: float
    steepness: float
    latency_gain: float = 0.0
    error_gain: float = 0.0


@dataclass(frozen=True)
class Edge:
    """A directed edge in the service-call ``TOPOLOGY`` graph.

    ``weight`` is either a constant fan-out share (``float`` in ``[0, 1]``
    for routing fractions, or any non-negative scalar for amplification
    edges) or a callable ``(np.ndarray) -> np.ndarray`` that computes the
    per-row weight from a numpy column (e.g. cache-miss rate driving the
    cache→database fan-out). The import-time ``_validate_topology``
    validator enforces both branches: constant weights must be a finite
    non-negative ``int``/``float`` (``bool`` is rejected); callable
    weights must accept a numpy array and return a numpy array.

    ``signal`` is the per-edge derivation that feeds a callable ``weight``.
    It receives a ``dict[str, np.ndarray]`` of the upstream component's
    captured load columns (the canonical metric plus any supplementary
    metrics declared in ``_TOPOLOGY_LOAD_METRICS``) and returns either an
    ``np.ndarray`` of per-row signal values (passed verbatim into
    ``weight(signal)``) or ``None`` to skip the edge entirely (e.g. when
    ``--metrics-per-component`` has trimmed a required input column).
    Required iff ``weight`` is callable; must be ``None`` for constant
    ``weight``. The validator probes the callable with a tiny captured-
    column dict so a mis-shaped signal fails at import time.

    ``saturation`` is optional; when set, the phase-4 saturation feedback
    layer adds a sigmoid-shaped latency/error contribution to the target
    component once the source's load metric crosses the configured
    midpoint.

    ``correlation_threshold`` is the minimum Pearson correlation the phase-7 ``_validate_topology_coupling`` check requires between this
    edge's source canonical load metric and its target canonical load
    metric under realistic topology coupling. ``None`` (the default)
    means "use the registry-level default
    ``_TOPOLOGY_DEFAULT_CORRELATION_THRESHOLD``". The field is read by the
    validator only and does not affect generation. Callable-weight edges
    skip the check regardless (the correlation is dominated by the per-row
    weight signal rather than the upstream load), so the field is ignored
    for them.
    """
    target: str
    weight: float | Callable[[np.ndarray], np.ndarray] = 1.0
    saturation: SaturationParams | None = None
    signal: Callable[[dict[str, np.ndarray]], "np.ndarray | None"] | None = None
    correlation_threshold: float | None = None
