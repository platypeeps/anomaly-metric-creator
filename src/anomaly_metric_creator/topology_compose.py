"""Topology coupling and saturation composition helpers.

Extracted from ``legacy.py`` as part of the generation/topology decomposition.
All registry reads go through ``topology_impl`` runtime callbacks so the legacy
compatibility surface remains monkeypatch-visible.
"""

from __future__ import annotations

import dataclasses

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

from .models_impl import MetricSpec
from .topology_impl import (
    _DEFAULT_RUNTIME_KEY,
    _TOPOLOGY_COUPLE_NOISE_STD,
    Edge,
    _component_metric_base,
    _runtime_topology,
    _runtime_topology_load_metrics,
    _runtime_topology_saturation_targets,
)
from .topology_instances import (
    _compute_topology_arrays_per_instance as _compute_topology_arrays_per_instance,
    _matched_cardinality as _matched_cardinality,
    _per_instance_upstream_view as _per_instance_upstream_view,
)
from .topology_support import (
    _apply_saturation,
    _arrays_equal_dict as _arrays_equal_dict,
    _sat_tuples_equal_dict as _sat_tuples_equal_dict,
)

def _compose_topology_coupled_specs(
    component_name: str,
    specs: list[MetricSpec],
    upstream_arrays: dict[str, dict[str, np.ndarray]],
    rng: "np.random.RandomState",
    n_rows: int,
    *,
    runtime_key: str | None = _DEFAULT_RUNTIME_KEY,
) -> list[MetricSpec]:
    """Return a possibly-modified spec list with the downstream's load
    metric(s) coupled to upstream component(s) via the TOPOLOGY graph.

    Phase 3 extends the coupling to every constant-weight
    edge in the v1 graph plus the ``cacheservice -> database`` callable
    edge:

    * Constant-weight edges scale the upstream's captured load column to
      the downstream metric's natural baseline:
      ``contribution = (upstream / upstream_base) * downstream_base *
      w_norm`` where ``w_norm = w / Σw`` across all active constant edges
      to this downstream. The normalization makes the combined constant
      term equal ``downstream_base`` at natural upstream load *regardless*
      of the raw weights' sum — relative weights set the fan-out shares,
      but the absolute values do not leave any "uncoupled" residue at
      the natural baseline. (Today the v1 graph's three apigateway fan-
      out weights already sum to 1.0; the renormalization keeps the
      formula well-defined if that invariant is ever relaxed.)

      Side-effect under ``--components`` subsetting: the normalization
      is computed over the *active* edges only, not the full declared
      fan-out. If a run drops one of apigateway's three fan-out targets
      (say ``--components apigateway,authservice,database``), the
      surviving fan-out edges renormalize so each carries its full
      ``downstream_base`` at natural upstream load — not the routing-
      fraction-weighted share the raw weights imply. This is intentional
      (subsetting should not leave the surviving downstreams running at
      a fraction of their natural baseline), but it does mean the
      effective per-edge contribution depends on which components are
      active; pin a full ``--components all`` baseline when comparing
      coupling magnitudes across runs.
    * Callable-weight edges call ``edge.weight(signal)`` with a per-row
      scalar signal derived from the upstream's captured columns by
      ``edge.signal(upstream_cols)``. The signal callable is paired
      with the callable weight on the same ``Edge`` (the import-time
      validator enforces the pairing); ``signal`` returning ``None``
      means "skip this edge" (e.g. a ``--metrics-per-component``
      selection trimmed a required input column). The weight's return
      value is added to the downstream baseline directly (in
      downstream-metric units) — e.g. the ``cacheservice -> database``
      callable returns the per-row cache-miss QPS contribution.

    When neither path delivers any signal (no upstream captured, all
    constant weights are zero, callable signal absent) the spec list is
    returned unchanged so the downstream falls back to its natural
    Gaussian baseline.

    The natural per-metric ``MetricSpec`` (multiplier, additive,
    clip_min, declarative schema metadata) is preserved via
    ``dataclasses.replace``; only ``base``, ``std``, ``multiplier``,
    and ``additive`` change so the coupled column writes the baked
    coupled column verbatim.
    """
    topology = _runtime_topology(runtime_key)
    topology_load_metrics = _runtime_topology_load_metrics(runtime_key)
    coupled_entry = topology_load_metrics.get(component_name)
    if coupled_entry is None:
        return specs
    canonical_down, supplementary_down = coupled_entry
    coupled_metric_names = (canonical_down, *supplementary_down)
    name_to_idx = {s.name: i for i, s in enumerate(specs)}
    if not any(m in name_to_idx for m in coupled_metric_names):
        return specs
    incoming: list[tuple[str, Edge]] = []
    for upstream, edges in topology.items():
        if upstream not in upstream_arrays:
            continue
        for edge in edges:
            if edge.target == component_name:
                incoming.append((upstream, edge))
    if not incoming:
        return specs

    # Callable-weight contributions are computed once per component —
    # ``edge.signal`` / ``edge.weight`` are metric-invariant, so
    # re-evaluating them per coupled metric was redundant — and applied
    # only to the *canonical* load metric below: the weight callable
    # returns values in the downstream's canonical-metric units (e.g.
    # ``_cache_miss_ratio_signal``'s weight scales to
    # ``database.queries_per_sec``'s natural base), so adding the same
    # array to a supplementary metric with a different base would inject
    # a wrong-unit contribution. Inert today — no callable-edge target
    # declares supplementary captures — but the first one added would
    # have silently mixed units. Track whether any callable signal was
    # successfully evaluated separately from the numeric contribution —
    # a callable that happens to be exactly zero everywhere (e.g. a
    # cache with a 0% miss rate for the whole run) is still a valid
    # coupling signal, not an absent one, and must not silently fall
    # back to the natural Gaussian baseline.
    callable_active = False
    callable_contrib = np.zeros(n_rows, dtype=np.float64)
    for upstream, edge in incoming:
        if not callable(edge.weight):
            continue
        if edge.signal is None:
            # Defence-in-depth: the validator rejects callable-weight
            # edges without ``signal`` at import-time. A missing
            # ``signal`` here means a future contributor bypassed the
            # validator (e.g. via a monkeypatched TOPOLOGY in a test);
            # skip the edge rather than crashing the generator.
            continue
        ups_cols = upstream_arrays.get(upstream, {})
        signal = edge.signal(ups_cols)
        if signal is None:
            continue
        callable_contrib = callable_contrib + np.asarray(
            edge.weight(signal), dtype=np.float64
        )
        callable_active = True

    new_specs = list(specs)
    for metric_name in coupled_metric_names:
        if metric_name not in name_to_idx:
            continue
        original = specs[name_to_idx[metric_name]]
        downstream_base = float(original.base)
        if downstream_base <= 0:
            continue
        # Canonical-only: see the callable-contribution comment above.
        metric_callable_active = (
            callable_active and metric_name == canonical_down
        )

        # First pass: collect all active constant-weight edges to compute
        # the normalization factor that maps ``sum(weight)`` to 1.0 so the
        # combined contribution equals ``downstream_base`` at natural
        # upstream load.
        active_constant: list[tuple[np.ndarray, float, float]] = []  # (arr, base, w)
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
            ups_cols = upstream_arrays.get(upstream, {})
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

        # Constant contributions: normalize by sum(w) so the constant term
        # equals ``downstream_base`` at natural upstream load regardless of
        # how many contributing edges exist. Each upstream's array is scaled
        # by ``(upstream / upstream_base) * downstream_base * w_normalized``
        # so variation in the upstream flows through at a proportional scale
        # to the downstream metric's natural magnitude.
        constant_contrib = np.zeros(n_rows, dtype=np.float64)
        if active_constant:
            sum_w = sum(w for _, _, w in active_constant)
            # sum_w > 0 in the shipped graph (constant weights are positive),
            # but a monkeypatched/programmatic TOPOLOGY whose active constant
            # weights sum to 0 would divide by zero here. Zero total weight
            # means no constant coupling, so leave constant_contrib at zeros
            # (07-02-verify-topology-divzero).
            if sum_w > 0:
                for ups_arr, ups_base, w in active_constant:
                    w_norm = w / sum_w  # normalise so contributions sum to 1.0
                    constant_contrib = constant_contrib + (
                        ups_arr / ups_base * downstream_base * w_norm
                    )

        coupled = (
            constant_contrib
            + (callable_contrib if metric_callable_active else 0.0)
            + rng.normal(0.0, _TOPOLOGY_COUPLE_NOISE_STD, n_rows)
        )
        new_specs[name_to_idx[metric_name]] = dataclasses.replace(
            original,
            base=0.0,
            std=0.0,
            multiplier=None,
            additive=lambda ts, elapsed, baked=coupled: baked,
        )
    return new_specs


def _compose_topology_saturation_specs(
    component_name: str,
    specs: list[MetricSpec],
    upstream_arrays: dict[str, dict[str, np.ndarray]],
    n_rows: int,
    *,
    runtime_key: str | None = _DEFAULT_RUNTIME_KEY,
) -> list[MetricSpec]:
    """Apply saturation feedback from every incoming TOPOLOGY edge with
    non-None ``SaturationParams`` to the downstream's latency-family and
    error-family ``MetricSpec`` entries (as declared in
    ``_TOPOLOGY_SATURATION_TARGETS``).

    For each saturating incoming edge the upstream's primary captured
    load metric drives ``_apply_saturation`` once. Multiple incoming
    saturating edges to the same downstream compose multiplicatively for
    the latency factor (each edge layers an additional load-dependent
    slowdown) and additively for the error offset (each edge contributes
    its own failure surface).

    The natural ``MetricSpec.multiplier`` / ``MetricSpec.additive`` (e.g.
    a ``_daily_sine`` envelope) is preserved by closing over the
    saturation array and composing on top of the existing callable — so
    seasonal patterns remain visible underneath the saturation curve.
    Only ``multiplier`` and ``additive`` change; ``std``, ``clip_min``,
    and the declarative schema metadata pass through unchanged.

    Returns ``specs`` unchanged when:

    * the component is not in ``_TOPOLOGY_SATURATION_TARGETS``;
    * no incoming saturating edge has its upstream captured (e.g. a
      ``--components`` subset that removes the upstream);
    * every incoming saturating edge declares zero ``latency_gain`` and
      zero ``error_gain`` (no v1 edges sit in this state after
      phase 5 promoted the LLM placeholder).
    """
    topology = _runtime_topology(runtime_key)
    topology_load_metrics = _runtime_topology_load_metrics(runtime_key)
    topology_saturation_targets = _runtime_topology_saturation_targets(runtime_key)
    targets = topology_saturation_targets.get(component_name)
    if targets is None:
        return specs
    latency_metrics, error_metrics = targets
    if not latency_metrics and not error_metrics:
        return specs

    name_to_idx = {s.name: i for i, s in enumerate(specs)}
    latency_factor = np.ones(n_rows, dtype=np.float64)
    error_offset = np.zeros(n_rows, dtype=np.float64)
    any_active = False
    for upstream, edges in topology.items():
        ups_cols = upstream_arrays.get(upstream)
        if not ups_cols:
            continue
        for edge in edges:
            if edge.target != component_name or edge.saturation is None:
                continue
            sat = edge.saturation
            if sat.latency_gain == 0.0 and sat.error_gain == 0.0:
                continue  # structurally-declared but inert edge.
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
        return specs

    # Both loops read (and replace into) ``new_specs`` rather than the
    # pristine ``specs`` so a metric that appears in BOTH the latency and
    # error tuples composes both effects. Reading ``specs[idx]`` in the
    # second loop would rebuild the spec from the original and silently
    # discard the multiplier wrap the first loop installed — diverging
    # from the per-instance path, which applies both sides of the
    # ``(latency_factor, error_offset)`` tuple to an overlap target. No
    # v1 registry entry overlaps today; this keeps the two paths aligned
    # for the first one that does.
    new_specs = list(specs)
    for metric_name in latency_metrics:
        idx = name_to_idx.get(metric_name)
        if idx is None:
            continue
        original = new_specs[idx]
        old_mult = original.multiplier
        if old_mult is None:
            new_mult = lambda ts, elapsed, baked=latency_factor: baked
        else:
            new_mult = (
                lambda ts, elapsed, baked=latency_factor, base=old_mult:
                base(ts, elapsed) * baked
            )
        new_specs[idx] = dataclasses.replace(original, multiplier=new_mult)
    for metric_name in error_metrics:
        idx = name_to_idx.get(metric_name)
        if idx is None:
            continue
        original = new_specs[idx]
        old_add = original.additive
        if old_add is None:
            new_add = lambda ts, elapsed, baked=error_offset: baked
        else:
            new_add = (
                lambda ts, elapsed, baked=error_offset, base=old_add:
                base(ts, elapsed) + baked
            )
        new_specs[idx] = dataclasses.replace(original, additive=new_add)

    return new_specs
