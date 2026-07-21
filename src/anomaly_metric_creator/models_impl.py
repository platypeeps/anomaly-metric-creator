"""Metric and instance data models for anomaly-metric-creator."""

from __future__ import annotations

import datetime
import weakref
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .csv_layout import (
    _INSTANCE_DIMENSION_COLUMNS,
    _INSTANCE_DIMENSION_FIELDS,
)

# ------------------------------------------------------------------
# Per-metric schema. One MetricSpec per CSV column per component.
# ------------------------------------------------------------------
@dataclass(frozen=True)
class MetricSpec:
    """Config for one synthetic metric column.

    Natural value is ``(base + N(0, std)) * multiplier(ts, sec) + additive(ts, sec)``,
    optionally clipped at ``clip_min``. ``std=0`` skips the RNG draw entirely so
    deterministic series do not perturb the shared numpy random stream.

    Schema fields (``unit``, ``semantic_type``, ``min_value``, ``max_value``,
    ``dtype``, ``derivation``) flow into ``schema.json`` and the
    ``validate`` subcommand's checks. ``dtype="int"`` also participates in
    generation when integer casting is enabled, causing
    ``generate_component`` to round the column values through ``np.rint``.
    Defaults preserve existing behavior for catalog entries that have not
    been backfilled yet.
    """
    name: str
    base: float
    std: float = 0.0
    multiplier: Callable[[datetime.datetime, int], float] | None = None
    additive: Callable[[datetime.datetime, int], float] | None = None
    clip_min: float | None = None
    # --- schema metadata ------------------------------------
    unit: str | None = None
    semantic_type: str | None = None
    min_value: float | None = None
    max_value: float | None = None
    dtype: str = "float"
    derivation: str | None = None


# ------------------------------------------------------------------
# Instance dimensions (Phase 1)
# ------------------------------------------------------------------
@dataclass(frozen=True)
class Instance:
    """One emitting instance of a component.

    Phase 1 introduces this dataclass as the foundational dimension model for
    multi-instance output. The CSV writer still emits one anonymous
    ``Instance()`` per component, so default byte output is unchanged; later
    phases plug ``--instances-per-component`` / ``--instance-config`` into the
    same shape and surface the dimensions as CSV columns, anomaly
    ``instance_filter`` selectors, OTEL resource attributes, and
    ``schema.json`` dimension declarations.

    All fields default to ``None`` so today's catalog can build the registry
    in lockstep with ``COMPONENTS`` without naming dimensions yet.
    """
    id: str | None = None
    host: str | None = None
    pod: str | None = None
    az: str | None = None
    region: str | None = None
    tenant: str | None = None


@dataclass
class RunContext:
    """Per-run mutable generation state."""

    rng: "np.random.RandomState"
    anomalies: list = field(default_factory=list)
    cascading_anomalies: dict = field(default_factory=dict)
    instances: dict = field(default_factory=dict)


_DEFAULT_RUNTIME_KEY = "__default__"
_models_runtimes = {}


def _weak_runtime_getter(getter: Callable, *, runtime_key: str) -> weakref.ReferenceType:
    """Keep extracted-module runtime hooks from retaining legacy module copies."""
    def discard_runtime(_ref, key=runtime_key):
        _models_runtimes.pop(key, None)

    try:
        return weakref.ref(getter, discard_runtime)
    except TypeError as exc:
        raise TypeError("models_impl runtime getters must be weak-referenceable") from exc


def _configure_models_runtime(
    *,
    get_components: Callable[[], dict[str, list[MetricSpec]]],
    get_max_instances_per_component: Callable[[], int],
    runtime_key: str = _DEFAULT_RUNTIME_KEY,
) -> None:
    """Wire live catalog access from ``legacy.py`` without importing it."""
    _models_runtimes[runtime_key] = {
        "get_components": _weak_runtime_getter(get_components, runtime_key=runtime_key),
        "get_max_instances_per_component": _weak_runtime_getter(
            get_max_instances_per_component,
            runtime_key=runtime_key,
        ),
    }


def _models_runtime_getter(runtime_key: str, key: str) -> Callable:
    runtime = _models_runtimes.get(runtime_key)
    if runtime is None:
        raise RuntimeError("models_impl runtime is not configured")
    getter = runtime[key]()
    if getter is None:
        _models_runtimes.pop(runtime_key, None)
        raise RuntimeError("models_impl runtime is no longer available")
    return getter


def _runtime_components(runtime_key: str) -> dict[str, list[MetricSpec]]:
    return _models_runtime_getter(runtime_key, "get_components")()


def _runtime_max_instances_per_component(runtime_key: str) -> int:
    return _models_runtime_getter(runtime_key, "get_max_instances_per_component")()


def _validate_instance_list(instances, *, where: str) -> None:
    """Per-entry invariants shared by ``_validate_instances_registry`` and
    ``generate_component`` (Phase 1, expanded in Phase 2).

    Rejects four classes of drift in ``instances`` (a non-empty iterable
    of ``Instance``):

    1. Non-``Instance`` entries: would raise a bare ``AttributeError`` on
       ``.id`` access at the next caller rather than a clear ``ValueError``.
       Mirrors ``_validate_scenarios_registry``'s isinstance-first pattern.
    2. Non-string (and non-``None``) ``Instance.id`` values: would raise a
       bare ``TypeError`` on set-membership lookup; Phase 4's
       ``instance_filter`` expects string ids.
    3. Duplicate non-None ``id`` values, or more than one anonymous
       (``id=None``) entry. Phase 4's ``instance_filter=["..."]`` looks up
       instances by id, so collisions would silently target multiple rows;
       multiple anonymous entries would be indistinguishable.
    4. Non-string (and non-``None``) dimension fields
       (``host``, ``pod``, ``az``, ``region``, ``tenant``): the Phase 2
       long-form CSV writer joins them with ``","`` directly. A non-string
       would raise a bare ``TypeError`` in the writer, and a value
       containing a comma or newline would silently corrupt the emitted
       CSV. Phase 3 (``--instance-config``) will surface this same
       constraint to file-loaded instance maps.

    ``where`` is the descriptor prefix used in raised error messages
    (e.g. ``"INSTANCES['authservice']"`` from the registry validator or
    ``"generate_component('authservice') instances"`` from the call site).
    Empty-list rejection lives at each call site so it can use a
    site-specific message.
    """
    seen_ids: set[str] = set()
    anon_count = 0
    for inst in instances:
        if not isinstance(inst, Instance):
            raise ValueError(
                f"{where} contains non-Instance entry {inst!r} "
                f"(type {type(inst).__name__}); every entry must be an "
                f"Instance dataclass."
            )
        if inst.id is not None:
            if not isinstance(inst.id, str):
                raise ValueError(
                    f"{where} entry has Instance.id={inst.id!r} "
                    f"(type {type(inst.id).__name__}); id must be None or a "
                    f"string (instance_filter looks up ids by string equality)."
                )
            if "," in inst.id or "\n" in inst.id or "\r" in inst.id:
                raise ValueError(
                    f"{where} entry has Instance.id={inst.id!r} containing "
                    f"a comma or newline; ids must not contain CSV-significant "
                    f"characters (the long-form writer does not quote id cells)."
                )
        for field_name in _INSTANCE_DIMENSION_FIELDS:
            value = getattr(inst, field_name)
            if value is None:
                continue
            if not isinstance(value, str):
                raise ValueError(
                    f"{where} entry has Instance.{field_name}={value!r} "
                    f"(type {type(value).__name__}); dimension fields must "
                    f"be None or a string (the long-form CSV writer joins "
                    f"them with ',' directly)."
                )
            if "," in value or "\n" in value or "\r" in value:
                raise ValueError(
                    f"{where} entry has Instance.{field_name}={value!r} "
                    f"containing a comma or newline; dimension values "
                    f"must not contain CSV-significant characters "
                    f"(the long-form writer does not quote dimension cells)."
                )
        if inst.id is None:
            anon_count += 1
            continue
        if inst.id in seen_ids:
            raise ValueError(
                f"{where} declares duplicate Instance.id={inst.id!r}; "
                f"ids must be unique per component for instance_filter "
                f"lookups (Phase 4)."
            )
        seen_ids.add(inst.id)
    if anon_count > 1:
        raise ValueError(
            f"{where} contains {anon_count} anonymous Instance(id=None) "
            f"entries; at most one anonymous instance is allowed per "
            f"component."
        )


def _load_instance_config(
    path: Path, *, runtime_key: str = _DEFAULT_RUNTIME_KEY
) -> dict[str, list[Instance]]:
    """Parse a YAML or JSON --instance-config file into a per-component Instance map.

    File schema::

        components:
          authservice:
            - {id: auth-east, region: us-east-1, pod: auth-1}
            - {id: auth-west, region: us-west-2, pod: auth-2}

    Every listed component must be a key of COMPONENTS. Each instance dict may
    only contain Instance field names (id, host, pod, az, region, tenant).
    Per-component instance counts are capped at MAX_INSTANCES_PER_COMPONENT.
    The id-uniqueness and shape rules from _validate_instance_list apply after
    construction.

    Returns a partial map: only components explicitly listed in the file appear
    as keys. ``main()`` fills the remaining components from the module-level
    ``INSTANCES`` registry (defaulting to ``[Instance()]``).

    Raises ``ValueError`` (caught in ``main()`` and re-raised via ``sys.exit``)
    for every schema violation: unknown components, unknown fields, empty
    component lists, duplicate ids, count exceeding the cap, missing or
    malformed top-level structure, IO errors on the file, and YAML/JSON parse
    errors.
    """
    suffix = path.suffix.lower()
    is_yaml = suffix in {".yaml", ".yml"}
    if is_yaml:
        try:
            import yaml  # PyYAML; optional dependency
        except ImportError:
            raise ValueError(
                f"--instance-config {path}: PyYAML is required to parse YAML files "
                "but is not installed. Install it with 'pip install pyyaml' or "
                "use a .json file instead."
            )
        # PyYAML's YAMLError is the parent of every parse / scanner /
        # composer error it raises.
        parse_exc_types: tuple[type[Exception], ...] = (
            yaml.YAMLError, UnicodeDecodeError,
        )
    else:
        import json
        parse_exc_types = (json.JSONDecodeError, UnicodeDecodeError)
    try:
        with open(path, encoding="utf-8") as f:
            raw = yaml.safe_load(f) if is_yaml else json.load(f)
    except OSError as exc:
        raise ValueError(
            f"--instance-config {path}: failed to read file: {exc}"
        ) from exc
    except parse_exc_types as exc:
        # Narrowed from ``except Exception`` so KeyboardInterrupt /
        # SystemExit (they inherit from BaseException, not Exception, but
        # being explicit avoids accidentally swallowing programming-error
        # exceptions like AttributeError if the parser were ever swapped).
        raise ValueError(
            f"--instance-config {path}: failed to parse "
            f"{'YAML' if is_yaml else 'JSON'}: {exc}"
        ) from exc

    if not isinstance(raw, dict):
        raise ValueError(
            f"--instance-config {path}: top-level value must be a mapping, "
            f"got {type(raw).__name__}"
        )
    # Distinguish "key absent" from "key present but explicitly null" so
    # ``components: null`` in YAML reports the more accurate
    # "must be a mapping" error rather than the misleading "missing key"
    # error.
    if "components" not in raw:
        raise ValueError(
            f"--instance-config {path}: missing required top-level key 'components'"
        )
    components_raw = raw["components"]
    if not isinstance(components_raw, dict):
        raise ValueError(
            f"--instance-config {path}: 'components' must be a mapping, "
            f"got {type(components_raw).__name__}"
        )

    components = _runtime_components(runtime_key)
    max_instances_per_component = _runtime_max_instances_per_component(runtime_key)

    # Derived from the canonical column list so a future Instance field
    # added to ``_INSTANCE_DIMENSION_COLUMNS`` is immediately accepted by
    # the config loader without a second edit.
    _valid_instance_fields = frozenset(_INSTANCE_DIMENSION_COLUMNS)
    result: dict[str, list[Instance]] = {}
    for component, inst_list in components_raw.items():
        if component not in components:
            raise ValueError(
                f"--instance-config {path}: unknown component {component!r}; "
                f"valid components: {sorted(components.keys())}"
            )
        if not isinstance(inst_list, list):
            raise ValueError(
                f"--instance-config {path}: {component!r} value must be a list, "
                f"got {type(inst_list).__name__}"
            )
        if not inst_list:
            raise ValueError(
                f"--instance-config {path}: {component!r} has an empty instance list; "
                "omit the key to fall back to a single anonymous Instance()"
            )
        if len(inst_list) > max_instances_per_component:
            raise ValueError(
                f"--instance-config {path}: {component!r} declares {len(inst_list)} "
                f"instances but MAX_INSTANCES_PER_COMPONENT={max_instances_per_component}"
            )
        instances = []
        for i, entry in enumerate(inst_list):
            if not isinstance(entry, dict):
                raise ValueError(
                    f"--instance-config {path}: {component!r}[{i}] must be a dict, "
                    f"got {type(entry).__name__}"
                )
            # Compare keys against the valid set after coercing to repr so a
            # YAML mapping with non-string keys (e.g. ``{1: 'x'}``) still
            # surfaces as an unknown-field ValueError rather than a TypeError
            # from sorting heterogeneous keys.
            unknown = [k for k in entry if k not in _valid_instance_fields]
            if unknown:
                raise ValueError(
                    f"--instance-config {path}: {component!r}[{i}] contains unknown "
                    f"field(s) {sorted(unknown, key=repr)}; valid fields: "
                    f"{sorted(_valid_instance_fields)}"
                )
            # Build the Instance kwargs from the same canonical tuple
            # used by the validator above, so a future field added to
            # _INSTANCE_DIMENSION_COLUMNS lands in both places at once
            # (validator accepts the key + constructor populates the
            # attribute) and can't be accepted-and-silently-dropped.
            instances.append(Instance(**{
                field: entry.get(field) for field in _INSTANCE_DIMENSION_COLUMNS
            }))
        _validate_instance_list(instances, where=f"--instance-config {path} {component!r}")
        result[component] = instances

    return result
