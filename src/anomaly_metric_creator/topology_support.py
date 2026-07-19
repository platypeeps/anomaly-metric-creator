"""Shared topology math and comparison helpers."""

from __future__ import annotations

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

from .topology_impl import SaturationParams, _validate_saturation_params

# Phase 4: Maximum utilization clamp before the logistic. Keeps
# ``np.exp`` numerically stable for arbitrary load magnitudes; the logistic
# is already > 0.99 at utilization = 2 with the smallest planned steepness
# (5), so a cap at 5x has no practical effect on the shape.
_SATURATION_MAX_UTILIZATION = 5.0


def _apply_saturation(
    upstream_load: np.ndarray, sat: SaturationParams,
) -> tuple[np.ndarray, np.ndarray]:
    """Compute the per-row ``(latency_multiplier, error_offset)`` arrays
    for one saturating TOPOLOGY edge.

    The logistic response curve:

        utilization = upstream_load / sat.midpoint
                       (clamped to ``[0, _SATURATION_MAX_UTILIZATION]`` so
                       ``np.exp`` stays finite for any input)
        logistic    = 1 / (1 + exp(-sat.steepness * (utilization - 1)))
        latency_multiplier = 1 + sat.latency_gain * logistic
        error_offset       = sat.error_gain * logistic

    Bounds: ``latency_multiplier`` ∈ ``[1, 1 + latency_gain]`` (always
    positive given non-negative gains); ``error_offset`` ∈
    ``[0, error_gain]`` (capped by the gain itself).

    ``upstream_load`` is the captured load metric of the saturating edge's
    *source* component (e.g. ``loadbalancer.requests_per_sec`` for the
    ``loadbalancer -> apigateway`` edge). Phase 4 drives the curve from
    upstream load — which Kahn ordering guarantees is already captured
    in ``upstream_arrays`` when the downstream is composed — rather
    than the downstream's own load column, which is still being
    constructed at composition time.
    """
    _validate_saturation_params(sat, context="_apply_saturation")
    upstream_arr = np.asarray(upstream_load, dtype=np.float64)
    # Generated captures are finite by construction (Kahn ordering feeds this
    # only pre-round captured load columns), so this never fires on real output; it
    # fails loud for direct/programmatic callers rather than letting a
    # NaN/inf propagate silently through the logistic into a metric cell
    # (07-02-verify-topology-divzero). np.maximum/np.minimum do not filter
    # NaN, so the utilization clamp below cannot catch it.
    if not np.all(np.isfinite(upstream_arr)):
        raise ValueError(
            "_apply_saturation: upstream_load must be finite; "
            "got NaN/inf values"
        )
    utilization = np.maximum(upstream_arr, 0.0) / float(sat.midpoint)
    np.minimum(utilization, _SATURATION_MAX_UTILIZATION, out=utilization)
    logistic = 1.0 / (1.0 + np.exp(-sat.steepness * (utilization - 1.0)))
    latency_multiplier = 1.0 + sat.latency_gain * logistic
    error_offset = sat.error_gain * logistic
    return latency_multiplier, error_offset




def _arrays_equal_dict(
    a: dict[str, np.ndarray], b: dict[str, np.ndarray],
) -> bool:
    """Byte-comparison of two ``dict[str, np.ndarray]`` entries.

    Used by ``generate_component`` to detect whether the
    per-instance topology arrays returned by
    ``_compute_topology_arrays_per_instance`` diverge from instance
    0. Equality is element-wise via ``np.array_equal`` with its
    default ``equal_nan=False`` — two byte-identical arrays that
    contain NaN therefore compare *unequal* and force the divergent
    per-instance path. That is fail-safe (the divergent path still
    produces correct, identical output with an unchanged RNG schedule
    since coupling noise is pre-drawn and shared; only memory is
    wasted on redundant per-instance buffers), and NaN never reaches
    these arrays from the catalog generators today.
    """
    if a.keys() != b.keys():
        return False
    for key, arr in a.items():
        if not np.array_equal(arr, b[key]):
            return False
    return True


def _sat_tuples_equal_dict(
    a: dict[str, tuple["np.ndarray | None", "np.ndarray | None"]],
    b: dict[str, tuple["np.ndarray | None", "np.ndarray | None"]],
) -> bool:
    """Byte-comparison of two saturation-tuple dicts.

    Mirrors ``_arrays_equal_dict`` but unpacks the
    ``(latency_factor, error_offset)`` pair from each entry. Either
    side of the tuple may be ``None`` — saturation populates only
    one side per metric depending on whether the metric is a
    latency target or an error target.
    """
    if a.keys() != b.keys():
        return False
    for key, (lf_a, eo_a) in a.items():
        lf_b, eo_b = b[key]
        if (lf_a is None) != (lf_b is None):
            return False
        if lf_a is not None and not np.array_equal(lf_a, lf_b):
            return False
        if (eo_a is None) != (eo_b is None):
            return False
        if eo_a is not None and not np.array_equal(eo_a, eo_b):
            return False
    return True
