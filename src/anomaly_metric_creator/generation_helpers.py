"""Leaf helpers for vectorized metric generation.

Extracted from ``legacy.py`` via ``generation.py`` as part of the
generation/topology decomposition. ``generation.py`` re-exports these names so
the historic import surface and direct generation-module callers stay stable.
"""

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

from .models_impl import Instance, MetricSpec

def _natural_column(spec: MetricSpec, ts_array: np.ndarray, elapsed: np.ndarray,
                    rng: "np.random.RandomState",
                    *,
                    noise: np.ndarray | None = None,
                    latency_factor: np.ndarray | None = None,
                    error_offset: np.ndarray | None = None,
                    baseline_override: np.ndarray | None = None) -> np.ndarray:
    """Vectorized natural-value column. Multiplier/additive must accept arrays.

    The optional kwargs decouple two pieces of state that were previously
    baked into ``MetricSpec.multiplier`` / ``MetricSpec.additive`` lambdas
    by ``_compose_topology_*_specs``. Called with ``latency_factor`` and
    ``error_offset`` equal to what the lambdas would have computed, the
    result matches the lambda-baked path byte-for-byte on the locked
    baselines (pinned by the N=3 golden hashes; IEEE-754 multiplication
    and addition are not associative, so the equality is an empirical
    property of the shipped seeds holding through the 3-decimal CSV
    rounding, not a mathematical guarantee), and they unlock the
    per-instance saturation path where each instance's curve depends on
    its own upstream view:

    * ``noise`` — pre-drawn ``rng.normal(0, spec.std, n_rows)`` array.
      When provided, the function uses it instead of drawing fresh
      noise so multiple call sites (e.g. one per instance) can share
      the same noise floor without advancing the RNG more than once.
      Pass ``None`` to keep the historic single-call draw.
    * ``latency_factor`` — per-row multiplicative array applied
      *between* the natural multiplier and the natural additive,
      matching where ``_compose_topology_saturation_specs`` baked the
      saturation latency multiplier into ``MetricSpec.multiplier``.
    * ``error_offset`` — per-row additive array applied *after* the
      natural additive and *before* ``clip_min``, matching where the
      saturation error offset was baked into ``MetricSpec.additive``.
    * ``baseline_override`` — per-row array that REPLACES the natural
      baseline (used by per-instance coupling where the downstream
      load metric is fully baked from upstream views). Composes with
      ``latency_factor`` / ``error_offset`` after the replacement.
      Mirrors what ``_compose_topology_coupled_specs`` produces by
      replacing ``base=0, std=0, multiplier=None,
      additive=lambda: coupled`` on the spec — the override is the
      ``coupled`` array exactly.
    """
    if baseline_override is not None:
        col = np.array(baseline_override, dtype=np.float64, copy=True)
    else:
        col = np.full(elapsed.shape, spec.base, dtype=np.float64)
        if spec.std > 0:
            if noise is None:
                noise = rng.normal(0.0, spec.std, elapsed.shape[0])
            col += noise
        if spec.multiplier is not None:
            col *= spec.multiplier(ts_array, elapsed)
    if latency_factor is not None:
        col *= latency_factor
    if baseline_override is None and spec.additive is not None:
        col += spec.additive(ts_array, elapsed)
    if error_offset is not None:
        col += error_offset
    if spec.clip_min is not None:
        np.maximum(col, spec.clip_min, out=col)
    return col


# Sentinel returned by ``_resolve_instance_filter`` when an ``instance_filter``
# matches zero active instances. Distinct from ``None`` (which means "no
# filter / matches every instance"); the caller emits a single WARNING per
# skipped spec and drops it from the override pipeline.
_INSTANCE_FILTER_NO_MATCH = object()


def _resolve_instance_filter(spec_filter, instances: list["Instance"]):
    """Resolve a spec's ``instance_filter`` against the active instance list.

    Returns ``None`` when every active instance matches (no filter declared
    or filter matches everyone) — the caller takes the shared-values fast
    path and preserves the single-shared-buffer behavior.

    Returns ``_INSTANCE_FILTER_NO_MATCH`` when the filter matches zero
    active instances — the caller emits one WARNING per spec and drops it.

    Returns a ``bool`` ``np.ndarray`` of length ``len(instances)`` for
    partial matches — the caller applies overrides only to selected
    per-instance buffers.

    ``spec_filter`` must already have passed the structural validation in
    ``_validate_scenario_spec`` (``None``, iterable of ``str``, or
    callable). Membership against ``INSTANCES`` is not checked at import
    time because ``--instance-config`` (a later phase) will register
    runtime ids; this function compares against the per-run ``instances``
    list and warns on no-match instead.
    """
    if spec_filter is None:
        return None
    if callable(spec_filter):
        mask = np.array(
            [bool(spec_filter(inst)) for inst in instances], dtype=bool
        )
    else:
        id_set = frozenset(spec_filter)
        mask = np.array(
            [inst.id is not None and inst.id in id_set for inst in instances],
            dtype=bool,
        )
    if not mask.any():
        return _INSTANCE_FILTER_NO_MATCH
    if mask.all():
        return None
    return mask
