"""Per-instance topology composition helpers."""

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
from .topology_impl import (
    _DEFAULT_RUNTIME_KEY,
    _TOPOLOGY_COUPLE_NOISE_STD,
    Edge,
    _component_metric_base,
    _runtime_topology,
    _runtime_topology_load_metrics,
    _runtime_topology_saturation_targets,
)
from .topology_support import _apply_saturation

# Per-instance topology (phase 8).
# ------------------------------------------------------------------
# When ``--instances-per-component N > 1`` (or any non-default
# ``--instance-config``), the topology two-pass generation runs against
# each downstream instance's *matching* upstream view rather than the
# shared aggregate. The matching rule depends on the per-edge upstream
# vs. downstream cardinality:
#
# * **1:1 routing (matched cardinalities, ``len(upstream_instances) ==
#   len(downstream_instances)``).** Downstream instance ``K`` sees
#   upstream instance ``K`` exclusively for that edge. This is the
#   "matching instance set" branch from the issue scope; it
#   delivers the per-pod isolation the test verifies (a slow upstream
#   pod only saturates the corresponding downstream pod).
# * **Uniform fan-out (mismatched cardinalities).** Downstream instance
#   ``K`` sees the mean of all upstream instances' load — equivalent to
#   the issue's "edge weight divided by downstream cardinality" formula
#   averaged over ``N_up`` upstream pods. This is the fallback when the
#   1:1 mapping is undefined and matches the existing N=1-vs-N=1
#   aggregate behavior at the limit.
#
# Under symmetric upstream (no ``instance_filter`` on an upstream load
# metric, the default for every shipped scenario), every per-instance
# upstream view equals the shared aggregate view, so the per-instance
# saturation / coupling arrays collapse to the shared arrays and the
# CSV bytes are byte-identical to the pre-existing default-N=3 run. The
# locked ``N3_ONE_DAY_HASHES`` and ``N3_SEVEN_DAY_HASHES`` in
# ``tests/test_instances_per_component.py`` continue to hold without
# re-baselining.

def _matched_cardinality(upstream_inst_count: int, downstream_inst_count: int) -> bool:
    """Return True when 1:1 routing applies between source and target.

    Routes via ``upstream_instances[K] -> downstream_instances[K]``
    when both sides have the same number of instances. Otherwise the
    composer falls back to uniform fan-out (averaging across upstream
    pods) — see the module-level comment above.

    Only ``N == N`` matched lengths are 1:1; the helper treats any
    other shape (including ``N_up == 1`` against ``N_down > 1`` or vice
    versa) as the uniform-fan-out fallback so per-instance views are
    well-defined for any combination.
    """
    return (
        upstream_inst_count == downstream_inst_count
        and upstream_inst_count > 0
    )


def _per_instance_upstream_view(
    upstream_name: str,
    upstream_arrays_by_instance: list[dict[str, np.ndarray]] | None,
    upstream_arrays_shared: dict[str, np.ndarray] | None,
    downstream_inst_count: int,
    downstream_inst_idx: int,
    *,
    uniform_fanout_cache: dict[str, dict[str, np.ndarray]] | None = None,
) -> dict[str, np.ndarray] | None:
    """Return the captured-column dict that downstream instance K should
    consume from ``upstream_name``.

    Dispatches between the matched-cardinality 1:1 branch and the
    uniform fan-out branch, both producing a ``dict[metric_name,
    np.ndarray]`` shaped identically to ``upstream_arrays_shared`` so
    the existing ``_compose_topology_coupled_specs`` /
    ``_compose_topology_saturation_specs`` math can be re-used per
    instance.

    Returns ``None`` when no upstream capture is available — the
    composer skips this edge so a ``--components`` subset that drops
    the upstream degrades gracefully (identical to the N=1 path's
    ``upstream not in upstream_arrays`` guard).

    ``uniform_fanout_cache`` (optional) memoizes the averaged upstream
    dict by ``upstream_name``. Under mismatched cardinality the
    averaged view is identical for every downstream instance, so
    callers that loop across downstream instances pass a shared dict
    to avoid repeating the incremental sum-then-divide averaging work
    (O(N_down * N_up * n_rows) → O(N_up * n_rows)). Pass ``None`` for
    one-shot callers.
    """
    if upstream_arrays_by_instance is None:
        # No per-instance capture available for the upstream — fall
        # back to the shared aggregate view, equivalent to today's
        # N=1 path. This branch fires for N=1 upstream components in
        # mixed-N scenarios (the only mixed-N entry path is
        # ``--instance-config`` with a partial ``components`` map).
        return upstream_arrays_shared
    if not upstream_arrays_by_instance:
        return None
    n_up = len(upstream_arrays_by_instance)
    if _matched_cardinality(n_up, downstream_inst_count):
        return upstream_arrays_by_instance[downstream_inst_idx]
    if uniform_fanout_cache is not None:
        cached = uniform_fanout_cache.get(upstream_name)
        if cached is not None:
            return cached
    # Uniform fan-out: average across upstream pods. Each downstream
    # pod sees the same averaged view, so per-pod variation under this
    # branch only emerges from local saturation noise / coupling math
    # rather than from upstream asymmetry. The upstream instances
    # share the same metric-key set because they came from the same
    # MetricSpec list in ``generate_component``.
    averaged: dict[str, np.ndarray] = {}
    metric_keys = set()
    for entry in upstream_arrays_by_instance:
        metric_keys.update(entry.keys())
    for metric in metric_keys:
        arrays = [
            entry[metric] for entry in upstream_arrays_by_instance
            if metric in entry
        ]
        if not arrays:
            continue
        # Incremental sum-then-divide. Equal-weight mean as ``np.mean``
        # over the stacked array, but at O(n_rows) extra memory instead
        # of O(N_up × n_rows) — the ``np.stack`` allocation can become
        # multi-MB per metric for large ``N_up`` and 7-day runs.
        acc = arrays[0].astype(np.float64, copy=True)
        for arr in arrays[1:]:
            acc += arr
        acc /= len(arrays)
        averaged[metric] = acc
    if uniform_fanout_cache is not None:
        uniform_fanout_cache[upstream_name] = averaged
    return averaged


def _compute_topology_arrays_per_instance(
    component_name: str,
    specs: list[MetricSpec],
    upstream_arrays_shared: dict[str, dict[str, np.ndarray]],
    upstream_arrays_by_instance: dict[str, list[dict[str, np.ndarray]]],
    instances: list["Instance"],
    rng: "np.random.RandomState",
    n_rows: int,
    *,
    runtime_key: str | None = _DEFAULT_RUNTIME_KEY,
) -> tuple[
    list[dict[str, np.ndarray]],
    list[dict[str, tuple[np.ndarray | None, np.ndarray | None]]],
]:
    """Compute per-instance coupling and saturation arrays for ``component_name``.

    Returns ``(coupling_by_instance, saturation_by_instance)``:

    * ``coupling_by_instance[K][metric_name]`` is the per-row coupled
      baseline array for downstream instance ``K``'s coupled load
      metrics (replaces the natural baseline in ``_natural_column``
      via ``baseline_override``). Absent metrics fall back to the
      natural draw.
    * ``saturation_by_instance[K][metric_name]`` is the
      ``(latency_factor, error_offset)`` tuple applied to instance
      ``K``'s saturation-target metrics (composes with
      ``MetricSpec.multiplier`` / ``MetricSpec.additive`` via the
      ``_natural_column`` kwargs).

    Divergence detection (which instances diverge from instance 0)
    is intentionally not returned. ``generate_component`` re-derives
    it directly from the passed arrays via ``_arrays_equal_dict`` /
    ``_sat_tuples_equal_dict`` so correctness does not depend on a
    caller-supplied hint that could drift from the actual array
    contents.

    Shared ``rng.normal`` noise for callable+constant coupling is
    drawn once and reused across all instances so the
    ``_TOPOLOGY_COUPLE_NOISE_STD`` floor sits at the same magnitude
    today's shared draw produces under symmetric upstream — that
    keeps per-instance arrays under symmetric upstream byte-identical
    to the shared array a single ``_compose_topology_coupled_specs``
    call would produce.
    """
    n_inst = len(instances)
    coupling_by_instance: list[dict[str, np.ndarray]] = [{} for _ in range(n_inst)]
    saturation_by_instance: list[
        dict[str, tuple[np.ndarray | None, np.ndarray | None]]
    ] = [{} for _ in range(n_inst)]

    topology = _runtime_topology(runtime_key)
    topology_load_metrics = _runtime_topology_load_metrics(runtime_key)
    topology_saturation_targets = _runtime_topology_saturation_targets(runtime_key)
    coupled_entry = topology_load_metrics.get(component_name)
    sat_targets = topology_saturation_targets.get(component_name)

    # Determine which downstream metrics need either coupling or
    # saturation arrays.
    coupled_metric_names: tuple[str, ...] = ()
    canonical_down: str | None = None
    if coupled_entry is not None:
        canonical_down = coupled_entry[0]
        coupled_metric_names = (canonical_down, *coupled_entry[1])
    latency_metrics: tuple[str, ...] = ()
    error_metrics: tuple[str, ...] = ()
    if sat_targets is not None:
        latency_metrics, error_metrics = sat_targets

    name_to_idx = {s.name: i for i, s in enumerate(specs)}

    # Collect incoming edges once. Each entry is (upstream_name, Edge).
    # Filter to upstreams that actually have captured load arrays —
    # mirrors ``_compose_topology_coupled_specs``'s
    # ``if upstream not in upstream_arrays: continue`` guard so a
    # ``--components`` subset that drops an upstream (or a
    # ``--metrics-per-component`` trim that removes the canonical load
    # column) degrades gracefully *and* keeps the RNG draw schedule
    # aligned with the legacy path: ``shared_coupling_noise`` below
    # advances ``rng`` only when at least one upstream is actually
    # contributing, exactly as the lambda-baked composer does.
    incoming: list[tuple[str, Edge]] = []
    for upstream, edges in topology.items():
        if (
            upstream not in upstream_arrays_shared
            and upstream not in upstream_arrays_by_instance
        ):
            continue
        for edge in edges:
            if edge.target == component_name:
                incoming.append((upstream, edge))
    if not incoming:
        return coupling_by_instance, saturation_by_instance

    # Shared callable+constant noise per coupled metric — drawn lazily
    # the *first* time a metric produces an active contribution, then
    # cached across instances so symmetric upstream stays byte-identical
    # to today's shared draw. Lazy initialization (instead of an upfront
    # pre-draw over ``coupled_metric_names``) matches
    # ``_compose_topology_coupled_specs``'s RNG schedule: that legacy
    # path draws noise inside the active branch only, so a coupled
    # metric whose contributions all get skipped (e.g.
    # ``--metrics-per-component`` trimmed the canonical upstream
    # column, or every callable ``signal`` returned ``None``) consumes
    # zero RNG draws there. Pre-drawing here would have advanced
    # ``rng`` for those skipped metrics, shifting every subsequent
    # downstream's draws.
    shared_coupling_noise: dict[str, np.ndarray] = {}

    # Compute per-instance arrays.
    # Cache shared across downstream instances: under mismatched
    # cardinality, ``_per_instance_upstream_view`` averages every
    # upstream pod into a single dict that is identical for every
    # downstream pod. Without the cache the same incremental
    # sum-then-divide averaging runs N_down times per upstream
    # (O(N_down * N_up * n_rows)); with the cache it runs once
    # (O(N_up * n_rows)).
    uniform_fanout_cache: dict[str, dict[str, np.ndarray]] = {}
    for inst_idx in range(n_inst):
        # Build the per-instance upstream view dict keyed by upstream name.
        per_instance_upstream_cols: dict[str, dict[str, np.ndarray]] = {}
        for upstream, _edge in incoming:
            per_instance_upstream_cols[upstream] = (
                _per_instance_upstream_view(
                    upstream,
                    upstream_arrays_by_instance.get(upstream),
                    upstream_arrays_shared.get(upstream),
                    n_inst,
                    inst_idx,
                    uniform_fanout_cache=uniform_fanout_cache,
                )
                or {}
            )

        # ------------------------------------------------------------
        # Coupling arrays (one per coupled metric on this component).
        #
        # Callable-weight contributions are computed once per instance
        # (``edge.signal`` / ``edge.weight`` are metric-invariant) and
        # applied only to the canonical load metric — the weight
        # callable returns canonical-metric units, so a supplementary
        # metric with a different base must not receive it. Mirrors the
        # shared-path rule in ``_compose_topology_coupled_specs``.
        # ------------------------------------------------------------
        callable_active = False
        callable_contrib = np.zeros(n_rows, dtype=np.float64)
        for upstream, edge in incoming:
            if not callable(edge.weight):
                continue
            if edge.signal is None:
                continue
            ups_cols = per_instance_upstream_cols.get(upstream, {})
            signal = edge.signal(ups_cols)
            if signal is None:
                continue
            callable_contrib = callable_contrib + np.asarray(
                edge.weight(signal), dtype=np.float64
            )
            callable_active = True

        for metric_name in coupled_metric_names:
            if metric_name not in name_to_idx:
                continue
            original = specs[name_to_idx[metric_name]]
            downstream_base = float(original.base)
            if downstream_base <= 0:
                continue
            metric_callable_active = (
                callable_active and metric_name == canonical_down
            )

            # First: active constant-weight edges for normalization.
            active_constant: list[tuple[np.ndarray, float, float]] = []
            for upstream, edge in incoming:
                if callable(edge.weight):
                    continue
                if isinstance(edge.weight, bool) or not isinstance(
                    edge.weight, (int, float)
                ):
                    continue
                w = float(edge.weight)
                if w == 0.0:
                    continue
                ups_cols = per_instance_upstream_cols.get(upstream, {})
                ups_entry = topology_load_metrics.get(upstream)
                if ups_entry is None:
                    continue
                ups_canonical, _ = ups_entry
                if ups_canonical and ups_canonical in ups_cols:
                    ups_base = _component_metric_base(upstream, ups_canonical, runtime_key=runtime_key)
                    if ups_base > 0:
                        active_constant.append(
                            (ups_cols[ups_canonical], ups_base, w)
                        )

            if not active_constant and not metric_callable_active:
                continue

            constant_contrib = np.zeros(n_rows, dtype=np.float64)
            if active_constant:
                sum_w = sum(w for _, _, w in active_constant)
                # Guard sum_w == 0 as in the aggregate path above
                # (07-02-verify-topology-divzero): zero total constant weight
                # means no coupling contribution, not a divide-by-zero.
                if sum_w > 0:
                    for ups_arr, ups_base, w in active_constant:
                        w_norm = w / sum_w
                        constant_contrib = constant_contrib + (
                            ups_arr / ups_base * downstream_base * w_norm
                        )

            # Lazy noise draw: only after we know this metric has an
            # active contribution. ``setdefault`` keeps the noise
            # shared across instances — instance 0 (first iteration)
            # draws, later instances reuse the cached array — so
            # symmetric upstream still produces byte-identical
            # coupling arrays across pods.
            noise = shared_coupling_noise.get(metric_name)
            if noise is None:
                noise = rng.normal(
                    0.0, _TOPOLOGY_COUPLE_NOISE_STD, n_rows
                )
                shared_coupling_noise[metric_name] = noise
            coupling_by_instance[inst_idx][metric_name] = (
                constant_contrib
                + (callable_contrib if metric_callable_active else 0.0)
                + noise
            )

        # ------------------------------------------------------------
        # Saturation arrays.
        # ------------------------------------------------------------
        if sat_targets is None:
            continue
        latency_factor = np.ones(n_rows, dtype=np.float64)
        error_offset = np.zeros(n_rows, dtype=np.float64)
        any_active = False
        for upstream, edge in incoming:
            if edge.saturation is None:
                continue
            sat = edge.saturation
            if sat.latency_gain == 0.0 and sat.error_gain == 0.0:
                continue
            ups_cols = per_instance_upstream_cols.get(upstream, {})
            ups_entry = topology_load_metrics.get(upstream)
            if ups_entry is None:
                continue
            ups_canonical, _ups_supplementary = ups_entry
            # Canonical-only driver: ``sat.midpoint`` is tuned in the
            # upstream's canonical load-metric units, so a supplementary
            # column (different units — e.g. cacheservice's
            # ``cache_misses``) must never drive the logistic. When the
            # canonical column is absent (``--metrics-per-component``
            # trim) the edge is skipped, matching the constant-weight
            # coupling path's posture.
            driver = ups_cols.get(ups_canonical)
            if driver is None or driver.shape[0] != n_rows:
                continue
            lat_mult, err_off = _apply_saturation(driver, sat)
            latency_factor *= lat_mult
            error_offset += err_off
            any_active = True
        if not any_active:
            continue
        # Latency targets receive ONLY the multiplicative
        # ``latency_factor`` (mirrors today's
        # ``_compose_topology_saturation_specs`` wrapping
        # ``MetricSpec.multiplier``); error targets receive ONLY the
        # additive ``error_offset`` (mirrors wrapping
        # ``MetricSpec.additive``). A metric appearing in both lists
        # — rare; only triggered by future overlapping targets — gets
        # both effects applied.
        for metric_name in latency_metrics:
            saturation_by_instance[inst_idx][metric_name] = (
                latency_factor, None
            )
        for metric_name in error_metrics:
            existing = saturation_by_instance[inst_idx].get(metric_name)
            if existing is not None:
                lf_old, _ = existing
                saturation_by_instance[inst_idx][metric_name] = (
                    lf_old, error_offset
                )
            else:
                saturation_by_instance[inst_idx][metric_name] = (
                    None, error_offset
                )

    return coupling_by_instance, saturation_by_instance
