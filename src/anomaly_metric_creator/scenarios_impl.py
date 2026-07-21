"""Scenario selection and runtime composition helpers."""

from __future__ import annotations

import sys
import weakref
from typing import Callable

import numpy as np

from .scenario_catalog import SCENARIOS

_DEFAULT_RUNTIME_KEY = "__default__"
_scenario_runtimes: dict[str, weakref.ReferenceType] = {}


def _configure_scenarios_runtime(
    *, get_scenarios: Callable[[], dict], runtime_key: str = _DEFAULT_RUNTIME_KEY
) -> None:
    """Wire a live scenario registry without importing ``legacy``."""
    def discard_runtime(_ref, key=runtime_key):
        _scenario_runtimes.pop(key, None)

    try:
        _scenario_runtimes[runtime_key] = weakref.ref(get_scenarios, discard_runtime)
    except TypeError as exc:
        raise TypeError("scenario runtime getters must be weak-referenceable") from exc


def _runtime_scenarios(runtime_key: str) -> dict:
    getter_ref = _scenario_runtimes.get(runtime_key)
    if getter_ref is None:
        return SCENARIOS
    getter = getter_ref()
    if getter is None:
        _scenario_runtimes.pop(runtime_key, None)
        raise RuntimeError("scenario runtime is no longer available")
    return getter()


def _apply_signal_level_and_count(component_anomalies: dict, cascade_registry: dict,
                                  *, signal_level: str, selected_components: set,
                                  anomaly_count: int | None, seed: int,
                                  total_seconds: int, interval_seconds: float,
                                  signal_levels: dict[str, set[str]],
                                  default_severity: str,
                                  anomaly_count_cap_salt: int) -> None:
    """In-place filter the primary and cascade anomaly registries.

    Order is: severity (per ``signal_level``) → component allowlist
    (``selected_components``) → optional global cap (``anomaly_count``). Specs
    that fail any gate are dropped from the underlying lists/dicts so the
    generator never sees them.

    Out-of-range specs (``time_offset`` rounding to a row outside
    ``[0, n_rows)``) are excluded from the ``--anomaly-count`` sampling pool
    but always remain in the registries, so the generator's existing stderr
    soft-skip warning fires for them in every configuration. The cap still
    matches manifest output exactly because out-of-range specs cannot
    produce manifest rows.

    Sampling for ``anomaly_count`` uses a dedicated
    ``np.random.SeedSequence`` derived from ``seed`` with a fixed
    ``spawn_key`` tag, so it doesn't perturb the column-noise RNG stream that
    ``generate_component`` shares via ``np.random``. The sampled positions are
    iterated in sorted order so the manifest row order is fully deterministic
    for a given ``(seed, anomaly_count, eligible-pool)`` triple — independent
    of CPython's set iteration order.
    """
    allowed_severities = signal_levels[signal_level]
    n_rows = int(total_seconds // interval_seconds)

    def _keep(spec: dict, component: str) -> bool:
        if component not in selected_components:
            return False
        return spec.get("severity", default_severity) in allowed_severities

    def _in_range(spec: dict) -> bool:
        offset = spec.get("time_offset", 0)
        if offset < 0:
            return False
        return int(round(offset / interval_seconds)) < n_rows

    for component, specs in component_anomalies.items():
        component_anomalies[component] = [s for s in specs if _keep(s, component)]
    for component in list(cascade_registry.keys()):
        cascade_registry[component] = [s for s in cascade_registry[component]
                                       if _keep(s, component)]

    if anomaly_count is None:
        return

    # Build a positional view of the in-range pool only; out-of-range specs
    # cannot produce manifest rows but stay in the registries so the
    # generator's existing soft-skip warning still fires for them.
    positional: list[tuple[str, str, dict]] = []
    for component, specs in component_anomalies.items():
        for spec in specs:
            if _in_range(spec):
                positional.append((component, "primary", spec))
    for component, specs in cascade_registry.items():
        for spec in specs:
            if _in_range(spec):
                positional.append((component, "cascade", spec))

    if anomaly_count >= len(positional):
        return

    seq = np.random.SeedSequence(seed, spawn_key=(anomaly_count_cap_salt,))
    rng = np.random.default_rng(seq)
    # Iterate positions in sorted order so manifest row sequence is
    # independent of Python set hash-iteration order.
    keep_positions = sorted(
        rng.choice(len(positional), size=anomaly_count, replace=False).tolist()
    )

    kept_primary: dict[str, list[dict]] = {}
    kept_cascade: dict[str, list[dict]] = {}
    for pos in keep_positions:
        component, source, spec = positional[pos]
        target = kept_primary if source == "primary" else kept_cascade
        target.setdefault(component, []).append(spec)

    # Re-append out-of-range specs so the generator continues to surface its
    # stderr soft-skip warning for them; they cannot contribute manifest
    # rows, so the cap still matches output exactly.
    for component in list(component_anomalies.keys()):
        out_of_range = [s for s in component_anomalies[component] if not _in_range(s)]
        component_anomalies[component] = kept_primary.get(component, []) + out_of_range
    for component in list(cascade_registry.keys()):
        out_of_range = [s for s in cascade_registry[component] if not _in_range(s)]
        cascade_registry[component] = kept_cascade.get(component, []) + out_of_range


def _resolve_scenarios(
    args, *, signal_levels: dict[str, set[str]], runtime_key: str = _DEFAULT_RUNTIME_KEY
) -> set[str]:
    """Return the effective set of scenario slugs for this run.

    Resolution order: allowlist (``args.scenarios``) → exclusion
    (``args.exclude_scenarios``) → severity filter (``args.signal_level``) →
    duration filter (``args.duration_days``) → component filter
    (``args.components``).

    Scenarios dropped by the severity or duration filter emit a stderr
    ``WARNING: scenario <slug> requires …`` so a misconfigured run is
    diagnosable without re-running with -v. Scenarios with no
    ``components_touched`` intersection with ``args.components`` are dropped
    silently — every spec they would have produced is already filtered out
    downstream, and the warning would be noise for users who restricted
    components on purpose.

    parse_args has already validated that every slug in ``args.scenarios``
    and ``args.exclude_scenarios`` exists in ``SCENARIOS``, so this function
    never raises for unknown-slug.

    Iterates the candidate slugs in sorted order so the stderr ``WARNING``
    lines emitted on severity/duration drops appear in a deterministic
    order across runs — set iteration order would otherwise vary and make
    diagnostics harder to diff.
    """
    scenarios = _runtime_scenarios(runtime_key)
    allowed_severities = signal_levels[args.signal_level]
    resolved = set(args.scenarios) - set(args.exclude_scenarios)

    survivors: set[str] = set()
    for slug in sorted(resolved):
        scenario = scenarios[slug]
        if scenario.severity not in allowed_severities:
            print(
                f"WARNING: scenario {slug} requires --signal-level "
                f"{scenario.severity} (current: {args.signal_level}); skipped.",
                file=sys.stderr,
            )
            continue
        if scenario.days_required > args.duration_days:
            print(
                f"WARNING: scenario {slug} requires --duration-days "
                f">= {scenario.days_required} (current: {args.duration_days}); "
                "skipped.",
                file=sys.stderr,
            )
            continue
        touched = set(scenario.components_touched)
        if not (touched & args.components):
            # Silent drop — no scenario output is possible under the
            # active --components allowlist anyway.
            continue
        survivors.add(slug)
    return survivors


def _apply_scenarios(
    component_anomalies: dict,
    cascade_registry: dict,
    active_scenarios: set[str],
    *,
    runtime_key: str = _DEFAULT_RUNTIME_KEY,
) -> None:
    """Append the active scenarios' primaries and cascades onto the runtime
    registries.

    Walks ``SCENARIOS`` in declaration order. Byte-for-byte output is
    preserved when each ``(row_idx, metric)`` is unique, because
    ``generate_component`` applies Python's stable ``sorted()`` with key
    ``(row_idx, metric)`` to its expanded overrides — under that condition
    the global RNG draw sequence is anchored to ``(time_offset, metric_name)``
    and the spec list order here does not matter. When two specs collide on
    the same ``(row_idx, metric)`` (e.g. a cascade landing inside a shaped
    primary span, or coarse ``--interval-seconds`` rounding two offsets to
    the same row), the stable sort preserves declaration order and the last
    writer wins for that cell — so the per-scenario spec list order is part
    of the contract for collisions.
    """
    for slug, scenario in _runtime_scenarios(runtime_key).items():
        if slug not in active_scenarios:
            continue
        # Shallow-copy each spec dict and stamp scenario provenance with
        # ``_``-prefixed keys. ``generate_component`` carries the dict reference
        # forward into the manifest entry, and the manifest writer uses
        # ``csv.DictWriter(..., extrasaction="ignore")`` so the private keys
        # never leak into the CSV. Shallow-copying keeps the frozen SCENARIOS
        # registry pristine across test runs (the registry is reused process-wide).
        for component, spec in scenario.primary_specs:
            tagged = dict(spec)
            tagged["_scenario_id"] = slug
            tagged["_severity"] = scenario.severity
            tagged["_is_cascade"] = False
            component_anomalies.setdefault(component, []).append(tagged)
        for target, cascade in scenario.cascade_specs:
            tagged = dict(cascade)
            tagged["_scenario_id"] = slug
            tagged["_severity"] = scenario.severity
            tagged["_is_cascade"] = True
            cascade_registry.setdefault(target, []).append(tagged)
