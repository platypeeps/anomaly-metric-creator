"""Validation for the ordered anomaly scenario catalog."""

from __future__ import annotations

import math

def _validate_scenario_spec(slug: str, component: str, spec: dict,
                            *, is_cascade: bool, components: dict,
                            valid_anomaly_shapes: set[str] | frozenset[str],
                            generator_meta) -> None:
    """Schema-check one primary or cascade spec dict at import time.

    Raises ``ValueError`` naming the scenario slug, component, and offending
    field on any drift. Cascade specs reject ``shape`` / ``duration_seconds``
    / ``shape_params`` because the cascade injection path is single-row step
    writes only (see CLAUDE.md § Anomaly injection schema).

    One deliberate write side effect: an iterable ``instance_filter`` is
    normalized in place to a ``frozenset`` (``spec["instance_filter"] =
    frozenset(items)``). This is load-bearing, not incidental — element
    validation must iterate the filter, which would *exhaust* a one-shot
    iterable (a generator, ``iter(...)``) before the runtime ever saw it,
    so the materialized form has to be stored back. Callers passing a
    spec dict should expect the field to be rewritten; everything else in
    the dict is read-only to this function.
    """
    kind = "cascade_specs" if is_cascade else "primary_specs"
    location = f"SCENARIOS[{slug!r}].{kind} entry for component {component!r}"

    if not isinstance(spec, dict):
        raise ValueError(
            f"{location} is not a dict (got {type(spec).__name__}); every "
            f"spec must be a dict with keys time_offset/metric/description/generator."
        )

    required = ("time_offset", "metric", "description", "generator")
    missing = [k for k in required if k not in spec]
    if missing:
        raise ValueError(
            f"{location} is missing required key(s) {missing}; every spec "
            f"must define {list(required)}."
        )

    metric = spec["metric"]
    if not isinstance(metric, str):
        raise ValueError(
            f"{location} has non-string metric {metric!r}; expected a "
            f"metric name from COMPONENTS[{component!r}]."
        )
    catalog = components.get(component, ())
    catalog_names = {s.name for s in catalog}
    if metric not in catalog_names:
        raise ValueError(
            f"{location} references metric {metric!r} not present in "
            f"COMPONENTS[{component!r}] (full catalog). Catalog: "
            f"{sorted(catalog_names)}."
        )

    if not callable(spec["generator"]):
        raise ValueError(
            f"{location} metric={metric!r} has non-callable generator "
            f"{spec['generator']!r}; expected a callable."
        )

    time_offset = spec["time_offset"]
    # ``bool`` is a subclass of ``int`` so ``isinstance(True, (int, float))``
    # is True; reject it explicitly so a stray boolean doesn't silently
    # round to row 1.
    if isinstance(time_offset, bool) or not isinstance(time_offset, (int, float)):
        raise ValueError(
            f"{location} metric={metric!r} has time_offset {time_offset!r}; "
            f"expected int or float seconds from START."
        )
    # math.isfinite() converts non-floats to a C double first; an
    # arbitrarily large Python int can raise OverflowError before the
    # finiteness check completes. Integers are finite by definition.
    if isinstance(time_offset, float) and not math.isfinite(time_offset):
        raise ValueError(
            f"{location} metric={metric!r} has non-finite time_offset "
            f"{time_offset!r}; offsets must be finite seconds from START."
        )
    # generate_component() does ``time_offset / interval`` (float divide)
    # at runtime; a Python int that can't be represented as a float would
    # raise OverflowError there. Reject at import time so the failure
    # surfaces with the validator's clear ValueError instead of a deep
    # runtime crash.
    if isinstance(time_offset, int) and not isinstance(time_offset, bool):
        try:
            float(time_offset)
        except OverflowError:
            raise ValueError(
                f"{location} metric={metric!r} has time_offset "
                f"{time_offset!r} that overflows float representation; "
                f"offsets are converted to float at runtime."
            ) from None
    if time_offset < 0:
        raise ValueError(
            f"{location} metric={metric!r} has negative time_offset "
            f"{time_offset!r}; offsets are seconds from START and must be >= 0."
        )

    description = spec["description"]
    if not isinstance(description, str) or not description.strip():
        raise ValueError(
            f"{location} metric={metric!r} has empty or non-string "
            f"description {description!r}; manifest rows require a label."
        )

    if is_cascade:
        forbidden = [k for k in ("shape", "duration_seconds", "shape_params")
                     if k in spec]
        if forbidden:
            raise ValueError(
                f"{location} metric={metric!r} declares {forbidden}; cascade "
                f"specs are single-row step writes and must not carry "
                f"shape/duration fields. Move shaped behavior into "
                f"primary_specs."
            )
    if not is_cascade and "shape" in spec:
        shape = spec["shape"]
        if not isinstance(shape, str):
            raise ValueError(
                f"{location} metric={metric!r} has non-string shape "
                f"{shape!r}; expected one of {sorted(valid_anomaly_shapes)}."
            )
        if shape not in valid_anomaly_shapes:
            raise ValueError(
                f"{location} metric={metric!r} has unsupported shape "
                f"{shape!r}; expected one of {sorted(valid_anomaly_shapes)}."
            )

    if not is_cascade and "duration_seconds" in spec:
        duration = spec["duration_seconds"]
        if isinstance(duration, bool) or not isinstance(duration, (int, float)):
            raise ValueError(
                f"{location} metric={metric!r} has duration_seconds "
                f"{duration!r}; expected int or float."
            )
        # Integers are finite by definition; avoid the C-double conversion
        # that math.isfinite() does for non-floats (can raise OverflowError
        # on arbitrarily large Python ints).
        if isinstance(duration, float) and not math.isfinite(duration):
            raise ValueError(
                f"{location} metric={metric!r} has non-finite duration_seconds "
                f"{duration!r}; expected a finite value."
            )
        # Same float-representability check as time_offset: generate_component
        # / _resolve_anomaly_value cast duration_seconds to float at runtime.
        if isinstance(duration, int) and not isinstance(duration, bool):
            try:
                float(duration)
            except OverflowError:
                raise ValueError(
                    f"{location} metric={metric!r} has duration_seconds "
                    f"{duration!r} that overflows float representation; "
                    f"durations are converted to float at runtime."
                ) from None
        if duration < 0:
            raise ValueError(
                f"{location} metric={metric!r} has negative duration_seconds "
                f"{duration!r}; duration must be >= 0 (0 means single-row step)."
            )

    if not is_cascade and "shape_params" in spec:
        params = spec["shape_params"]
        if not isinstance(params, dict):
            raise ValueError(
                f"{location} metric={metric!r} has shape_params "
                f"{params!r}; expected a dict."
            )

    # ``instance_filter`` (Phase 4) — optional on both primary and
    # cascade specs. Accepted forms:
    #
    #   * omitted / ``None``         -> apply to every active instance
    #                                   (today's Phase 2 behavior; preserves
    #                                   the locked Phase 2 hashes).
    #   * iterable of ``str`` ids    -> apply only to instances whose
    #                                   ``Instance.id`` is in the set.
    #   * callable ``(Instance) -> bool`` -> per-instance predicate.
    #
    # Import-time validation only checks structural shape. ``INSTANCES`` is
    # static but ``--instance-config`` (a later phase) will register runtime
    # ids, so membership against the registry can't be validated at import
    # time. The runtime path (``_resolve_instance_filter`` +
    # ``generate_component``) emits a ``WARNING`` and skips the spec when
    # the filter resolves to zero active instances.
    #
    # ``bool`` is rejected before the iterable check because ``bool`` is a
    # subclass of ``int`` but a scalar — the error message should name it
    # as a scalar, not "iterable of non-string". A bare ``str`` is also
    # rejected: it's iterable in Python but almost always a bug (would
    # iterate characters, producing a per-character filter); callers must
    # pass ``["i0"]`` instead of ``"i0"``.
    if "instance_filter" in spec:
        inst_filter = spec["instance_filter"]
        if inst_filter is None:
            pass
        elif callable(inst_filter):
            pass
        elif isinstance(inst_filter, (bool, int, float)):
            raise ValueError(
                f"{location} metric={metric!r} has instance_filter "
                f"{inst_filter!r}; expected None, an iterable of instance "
                f"ids (str), or a callable (Instance) -> bool."
            )
        elif isinstance(inst_filter, str):
            raise ValueError(
                f"{location} metric={metric!r} has instance_filter "
                f"{inst_filter!r} (a bare string); expected an iterable of "
                f"instance ids like [\"i0\"], not a single string (which "
                f"would iterate characters)."
            )
        elif isinstance(inst_filter, dict):
            raise ValueError(
                f"{location} metric={metric!r} has instance_filter "
                f"{inst_filter!r} (a dict); expected None, an iterable of "
                f"instance ids (str), or a callable (Instance) -> bool."
            )
        else:
            try:
                items = list(inst_filter)
            except TypeError:
                raise ValueError(
                    f"{location} metric={metric!r} has instance_filter "
                    f"{inst_filter!r}; expected None, an iterable of "
                    f"instance ids (str), or a callable (Instance) -> bool."
                ) from None
            for item in items:
                if not isinstance(item, str):
                    raise ValueError(
                        f"{location} metric={metric!r} has instance_filter "
                        f"with non-string entry {item!r} "
                        f"(type {type(item).__name__}); ids must be strings."
                    )
            # Normalize to frozenset so one-shot iterators (generators,
            # iter(...)) are materialized and ``_resolve_instance_filter``
            # can call ``frozenset(spec_filter)`` on a reiterable object.
            # Also gives O(1) membership checks at runtime.
            spec["instance_filter"] = frozenset(items)

    # Generator signature rules. The runtime always calls a generator with
    # a fixed positional shape determined by the path:
    #   - Step path (cascades + primary step specs without duration_seconds):
    #     3-arg ``(ts, col, rng)`` or 2-arg ``(ts, col)``.
    #   - Span path (primary specs with shape != "step" or
    #     duration_seconds > 0): 5-arg ``(ts, col, t_within, span_idx, rng)``
    #     or 2-arg ``(ts, col)``.
    # The validator must accept only signatures that the runtime can call
    # without silently misbinding ``t_within``/``span_idx`` to a parameter
    # the author intended for a different value (most commonly ``rng``).
    has_shape = spec.get("shape", "step") != "step"
    # Avoid the float() conversion on raw spec data — an arbitrarily large
    # int duration_seconds would overflow. Test the raw value's positivity
    # directly; type/finiteness was already validated above.
    raw_duration = spec.get("duration_seconds", 0)
    has_duration = bool(raw_duration) and raw_duration > 0
    meta = generator_meta(spec["generator"])
    # Required keyword-only params can never be supplied by the runtime
    # (the dispatch path uses positional args only); reject up front.
    if meta["has_required_kwargs"]:
        raise ValueError(
            f"{location} metric={metric!r} has a generator with required "
            f"keyword-only parameters; generators are called positionally "
            f"at runtime, so kwarg-only requirements would fail when the "
            f"spec fires. Provide defaults for keyword-only params, or "
            f"declare them as positional."
        )
    if not meta["inspectable"]:
        # Can't introspect — trust the caller; the dispatcher's try/except
        # fallback will handle it at runtime.
        return
    target = 5 if (has_shape or has_duration) else 3
    target_form = (
        "(ts, col, t_within, span_idx, rng)" if target == 5
        else "(ts, col, rng)"
    )
    path_name = "shape/duration" if (has_shape or has_duration) else "single-row step"
    required = meta["required_positional"]
    fixed = meta["fixed_positional_count"]
    has_var = meta["has_var_positional"]
    # Mirror the dispatcher logic:
    #   - if has_var or required == target: dispatcher calls target-arg
    #   - elif required <= 2: dispatcher calls 2-arg
    #   - else: no valid dispatch
    # Safety rules below ensure that whatever shape the dispatcher picks
    # binds the runtime values to author-intended positions (required
    # params or *args overflow) and never overwrites a default-having
    # fixed positional with t_within/span_idx/rng.
    reject_reason = None
    if required > target:
        reject_reason = (
            f"required_positional={required} > target {target}; no valid "
            f"dispatch can satisfy this many required params."
        )
    elif required != target and required > 2:
        # required ∈ {3, 4} on span path: dispatcher can't call 2-arg
        # (would fail required check) or target-arg (would bind t_within
        # /span_idx to required positional 3/4 — misbind).
        reject_reason = (
            f"required_positional={required} is between 2 and target "
            f"{target}; dispatcher would bind runtime internals to "
            f"required positions that the author intended for other values."
        )
    elif has_var and required <= 2 and fixed > 2:
        # has_var + default-having fixed positions BEYOND (ts, col).
        # Dispatcher picks target-arg, fills the default-having fixed
        # positions with t_within/span_idx/rng before flowing into *args
        # — overwriting the author's declared defaults.
        reject_reason = (
            f"required_positional={required} fixed_positional_count={fixed} "
            f"with *args: the dispatcher's {target}-arg call would bind "
            f"runtime internals to default-having fixed positions 3"
            f"{' through ' + str(min(fixed, target)) if min(fixed, target) > 3 else ''}, "
            f"overwriting the declared defaults. Move the default-having "
            f"positions after ``*args`` (kwarg-only with default) or drop them."
        )
    # Note: ``required == target and fixed > target`` (e.g. (ts, col, rng,
    # extra=None) for step) is intentionally accepted. The dispatcher calls
    # exactly target args, all required positions are bound, and any
    # trailing optional positions keep their declared defaults — no misbind.
    elif not has_var and required <= 2 and fixed < 2:
        # (ts) or () — dispatcher 2-arg call would fail.
        reject_reason = (
            f"fixed_positional_count={fixed} < 2; the 2-arg dispatcher "
            f"call would fail because the generator can't accept 2 args."
        )
    if reject_reason is not None:
        raise ValueError(
            f"{location} metric={metric!r} has a generator with "
            f"{reject_reason} {path_name} specs must use either the 2-arg "
            f"legacy form (ts, col) or the {target}-arg form {target_form}; "
            f"see CLAUDE.md § Scenario registry for the full dispatch rule."
        )


def _validate_scenarios_registry(
    scenarios: dict,
    components: dict,
    *,
    valid_anomaly_shapes: set[str] | frozenset[str],
    generator_meta,
    seconds_per_day: int,
) -> None:
    """Import-time invariants for ``SCENARIOS``.

    Mirrors the registry tests in ``tests/test_scenarios.py`` so any drift
    while editing the registry is caught at module load. Kept as a function
    so loop locals don't leak into the module namespace.
    """
    known_components = set(components.keys())
    for slug, scenario in scenarios.items():
        if scenario.id != slug:
            raise ValueError(
                f"SCENARIOS[{slug!r}].id is {scenario.id!r}; id must equal "
                f"the registry key"
            )
        # isinstance check first so an unhashable malformed value
        # (e.g., severity=[]) raises ValueError rather than a raw TypeError
        # from the set membership lookup.
        if (not isinstance(scenario.severity, str)
                or scenario.severity not in {"low", "medium", "high"}):
            raise ValueError(
                f"SCENARIOS[{slug!r}].severity {scenario.severity!r} must be "
                "a string in low / medium / high"
            )
        if not isinstance(scenario.days_required, int) or scenario.days_required < 1:
            raise ValueError(
                f"SCENARIOS[{slug!r}].days_required {scenario.days_required!r} "
                "must be a positive int (the minimum --duration-days at which "
                "any of the scenario's specs become in range)"
            )
        unknown_touched = set(scenario.components_touched) - known_components
        if unknown_touched:
            raise ValueError(
                f"SCENARIOS[{slug!r}].components_touched contains unknown "
                f"component(s): {sorted(unknown_touched)}"
            )
        # Validate each spec first so missing/malformed keys produce a clear
        # error before we try to read time_offset for the days_required check.
        valid_severities = {"low", "medium", "high"}
        for component, spec in scenario.primary_specs:
            if component not in known_components:
                raise ValueError(
                    f"SCENARIOS[{slug!r}].primary_specs references unknown "
                    f"component {component!r}"
                )
            _validate_scenario_spec(
                slug, component, spec, is_cascade=False,
                components=components, valid_anomaly_shapes=valid_anomaly_shapes,
                generator_meta=generator_meta,
            )
            if "severity" in spec:
                sev = spec["severity"]
                if not isinstance(sev, str) or sev not in valid_severities:
                    raise ValueError(
                        f"SCENARIOS[{slug!r}].primary_specs entry for component "
                        f"{component!r} has severity {sev!r}; "
                        f"must be a string in {sorted(valid_severities)}. "
                        f"_apply_signal_level_and_count reads spec.get('severity', "
                        f"DEFAULT_SEVERITY), so an unknown value would be silently "
                        f"filtered out at every --signal-level."
                    )
        for target, cascade in scenario.cascade_specs:
            if target not in known_components:
                raise ValueError(
                    f"SCENARIOS[{slug!r}].cascade_specs targets unknown "
                    f"component {target!r}"
                )
            _validate_scenario_spec(
                slug, target, cascade, is_cascade=True,
                components=components, valid_anomaly_shapes=valid_anomaly_shapes,
                generator_meta=generator_meta,
            )
            if "severity" in cascade:
                sev = cascade["severity"]
                if not isinstance(sev, str) or sev not in valid_severities:
                    raise ValueError(
                        f"SCENARIOS[{slug!r}].cascade_specs entry targeting "
                        f"{target!r} has severity {sev!r}; "
                        f"must be a string in {sorted(valid_severities)}. "
                        f"_apply_signal_level_and_count reads spec.get('severity', "
                        f"DEFAULT_SEVERITY), so an unknown value would be silently "
                        f"filtered out at every --signal-level."
                    )
        # days_required must equal the day index (1-based) of the earliest
        # time_offset across primary and cascade specs. Setting it too high
        # silently drops in-range specs at the requested --duration-days;
        # too low activates the scenario before any spec is in range.
        # Spec validation above ensures time_offset is a valid finite numeric.
        offsets = [p["time_offset"] for _, p in scenario.primary_specs]
        offsets += [c["time_offset"] for _, c in scenario.cascade_specs]
        if offsets:
            min_day_required = min(offsets) // seconds_per_day + 1
            if scenario.days_required != min_day_required:
                raise ValueError(
                    f"SCENARIOS[{slug!r}].days_required={scenario.days_required} "
                    f"must equal the day index of its earliest spec offset "
                    f"({min_day_required}). Too high silently drops in-range "
                    f"specs at the requested --duration-days; too low activates "
                    f"the scenario before any spec is in range."
                )
        # components_touched must equal exactly the set of components
        # referenced by primary_specs + cascade_specs. Under-claiming
        # silently drops the scenario under a narrow --components allowlist;
        # over-claiming dilutes the filter so the scenario fires for
        # allowlists that contain none of its actual components.
        referenced_components = {c for c, _ in scenario.primary_specs}
        referenced_components.update(c for c, _ in scenario.cascade_specs)
        declared_components = set(scenario.components_touched)
        if referenced_components != declared_components:
            missing = sorted(referenced_components - declared_components)
            extras = sorted(declared_components - referenced_components)
            raise ValueError(
                f"SCENARIOS[{slug!r}].components_touched="
                f"{sorted(declared_components)} must equal components "
                f"referenced by specs={sorted(referenced_components)}; "
                f"missing={missing} extras={extras}. Under-claiming silently "
                f"drops the scenario under a narrow --components allowlist; "
                f"over-claiming dilutes the filter."
            )
