#!/usr/bin/env python3
"""
Generate IoT-style metric logs for a SaaS stack with built-in anomalies.

Defaults to 50,000 rows at 1-minute resolution, matching the reference
observability telemetry CSV shape. Use ``--duration-days N`` to span more days;
multi-day scenarios activate based on their own ``days_required`` (see the
README scenario catalog for current values). ``--duration-days 7`` currently
unlocks the original week-long catalog; the default 50,000-row window also
captures the longer GPU inference serving pattern. Anomaly specs whose
``time_offset`` falls outside the configured window are skipped with a warning
on stderr.
"""

import contextlib
import csv
import datetime
import hashlib
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .runtime_defaults import SECONDS_PER_DAY, START
from .scenario_builders import (
    DEFAULT_INTERVAL_SECONDS as DEFAULT_INTERVAL_SECONDS,
    DEFAULT_ROW_COUNT as DEFAULT_ROW_COUNT,
    DEFAULT_SEVERITY as DEFAULT_SEVERITY,
)

try:
    import numpy as np
except ModuleNotFoundError as exc:
    if exc.name not in {None, "numpy"}:
        raise
    print(
        "Missing required dependency: numpy\n"
        "Install this project into the Python you are using, for example:\n"
        "  python3 -m pip install -e .\n"
        "or create the documented dev environment:\n"
        "  python3 -m venv .venv\n"
        "  .venv/bin/pip install -e '.[dev]'\n",
        file=sys.stderr,
    )
    raise SystemExit(1) from None

# ------------------------------------------------------------------
# Configuration
# ------------------------------------------------------------------
DEFAULT_SEED = 42
DEFAULT_OUTPUT_DIR = Path("iot_logs")
DEFAULT_DROP_RATE = 0.0
DEFAULT_DURATION_DAYS = (
    DEFAULT_ROW_COUNT * DEFAULT_INTERVAL_SECONDS / SECONDS_PER_DAY
)
DEFAULT_OTEL_STREAM_AUTH_SCHEME = "Bearer"
DEFAULT_SIGNAL_LEVEL = "medium"


# Preflight ceiling on total emitted cells per run, where one "cell" is one
# metric value at one timestamp summed across selected components. Trips on
# the metric × row × component product to catch the common foot-gun of
# combining a very small --interval-seconds with the default --duration-days
# and component allowlist, which silently blows up to billions of cells (and
# tens of GB of CSV) before the user notices. Override with
# --allow-huge-output when the size is intentional. 200M cells corresponds
# to roughly 5-15 GB of output and runs in tens of seconds; well above any
# default workload but well under "I rebooted my laptop by accident".
PREFLIGHT_CELL_CAP = 200_000_000

# Inclusion hierarchy for --signal-level: each level keeps its own severity tier
# plus everything weaker. A spec with no explicit ``severity`` defaults to
# ``medium`` so today's catalog continues to fire under the default level.
SIGNAL_LEVELS: dict[str, set[str]] = {
    "low": {"low"},
    "medium": {"low", "medium"},
    "high": {"low", "medium", "high"},
}

# Anomaly shape vocabulary and generator-call dispatch moved to
# anomaly_dispatch.py (decomposition final step). Re-imported here so
# scenario validation, tests, and the historic ``legacy.<name>`` surface stay
# unchanged.
from .anomaly_dispatch import (
    _VALID_ANOMALY_SHAPES as _VALID_ANOMALY_SHAPES,
    _cached_generator_meta as _cached_generator_meta,
    _call_generator_within_span as _call_generator_within_span,
    _generator_meta as _generator_meta,
    _resolve_anomaly_value as _resolve_anomaly_value,
    _span_fraction as _span_fraction,
)

# Stable named sub-seed for the --anomaly-count sampling RNG. Derived from
# sha256(b"anomaly_count_cap") and fixed at import time so the cap RNG stream
# is decoupled from any other np.random use that shares the same seed.
_ANOMALY_COUNT_CAP_SALT = int.from_bytes(
    hashlib.sha256(b"anomaly_count_cap").digest()[:4], "big"
)

# ------------------------------------------------------------------
# Per-run state container
# ------------------------------------------------------------------
@dataclass
class RunContext:
    """Per-run mutable state.

    Fields:
    - ``rng``: ``np.random.RandomState`` instance seeded from ``--seed``.
      Authoritative RNG for the run; threaded explicitly through
      ``generate_component()``, ``_natural_column()``, and the anomaly
      override path.
    - ``anomalies``: list accumulator for manifest rows. Each call to
      ``generate_component()`` appends one entry per anomaly span that
      survives drop-mask filtering.
    - ``cascading_anomalies``: dict keyed by target component name, value
      is the list of cascade spec dicts that fire on that component.
      Populated by ``_apply_scenarios()`` and consumed by
      ``generate_component()`` when it merges primary + cascade overrides.
    - ``instances``: optional per-run override of the module-level
      ``INSTANCES`` registry, keyed by component name. ``main()``
      populates it from ``INSTANCES`` by default (preserving today's
      single-anonymous-instance contract) and Phase 2+ CLI flags will
      replace the per-component list when the user asks for fan-out.
    """
    rng: "np.random.RandomState"
    anomalies: list = field(default_factory=list)
    cascading_anomalies: dict = field(default_factory=dict)
    instances: dict = field(default_factory=dict)

# Derived-metric recomputation moved to generation_derivations.py and generation.py
# (decomposition final step). Re-imported here so tests and validators keep the
# historic ``legacy.<name>`` surface.
from . import generation as _generation_module
from .generation_derivations import (
    DERIVED_METRICS as DERIVED_METRICS,
    DERIVATIONS as DERIVATIONS,
    _derive_cacheservice as _derive_cacheservice,
)

_GENERATION_DERIVATIONS_BINDING = DERIVATIONS

# ------------------------------------------------------------------
# Per-metric and instance models.
# ------------------------------------------------------------------
# MetricSpec / Instance moved to models_impl.py (decomposition step 9A).
# Re-imported here so tests, package facades, and the historic
# ``legacy.<name>`` surface stay unchanged.
from .models_impl import (
    Instance as Instance,
    MetricSpec as MetricSpec,
    _configure_models_runtime as _configure_models_runtime,
    _load_instance_config as _models_load_instance_config,
    _validate_instance_list as _validate_instance_list,
)



# _INSTANCE_DIMENSION_COLUMNS (the canonical long-form dimension column
# order) moved to csv_layout.py (decomposition step 3); re-imported here
# so the Instance model, generation, schema, combine, gauges, OTEL, and
# server_mcp (via state.legacy) consumers keep the historic binding.
from .csv_layout import (
    _INSTANCE_DIMENSION_COLUMNS as _INSTANCE_DIMENSION_COLUMNS,
    _INSTANCE_DIMENSION_FIELDS as _INSTANCE_DIMENSION_FIELDS,
    _is_anonymous_instance_list as _is_anonymous_instance_list,
)


# Topology dataclasses moved to topology_impl.py (decomposition final step).
# Re-imported here so tests, package facades, and the historic
# ``legacy.<name>`` surface stay unchanged.
from .topology_impl import (
    Edge as Edge,
    SaturationParams as SaturationParams,
)
# Scenario models/builders moved to scenario_builders.py (decomposition step 9B).
from .scenario_builders import (
    Scenario as Scenario,
    _GPU_INFERENCE_FRAGMENTATION_CASCADE_SPECS as _GPU_INFERENCE_FRAGMENTATION_CASCADE_SPECS,
    _GPU_INFERENCE_FRAGMENTATION_PRIMARY_SPECS as _GPU_INFERENCE_FRAGMENTATION_PRIMARY_SPECS,
    _clamp as _clamp,
    _const_generator as _const_generator,
    _correlated_span_generator as _correlated_span_generator,
    _gpu_feature_minutes as _gpu_feature_minutes,
    _gpu_inference_failure_minutes as _gpu_inference_failure_minutes,
    _gpu_inference_fragmentation_specs as _gpu_inference_fragmentation_specs,
    _gpu_inference_incident_intensity as _gpu_inference_incident_intensity,
    _gpu_inference_incident_windows as _gpu_inference_incident_windows,
    _gpu_inference_reference_schedule as _gpu_inference_reference_schedule,
    _gpu_inference_stress_score as _gpu_inference_stress_score,
    _ranked_choice_set as _ranked_choice_set,
    _scenario_stress_fraction as _scenario_stress_fraction,
    _weighted_choice as _weighted_choice,
    register_cascade as register_cascade,
)

# ------------------------------------------------------------------
# Atomic artifact publication
# ------------------------------------------------------------------
# Atomic artifact publication moved verbatim to artifacts.py
# (decomposition step 4, landed with step 3 because gauges_impl.py
# depends on it). Re-imported here so every writer below plus tests keep
# the historic ``legacy.<name>`` surface; new code should import from
# anomaly_metric_creator.artifacts directly.
from .artifacts import (
    _ATOMIC_TMP_SUFFIX as _ATOMIC_TMP_SUFFIX,
    _atomic_artifact_open as _atomic_artifact_open,
    _atomic_write_text as _atomic_write_text,
)


# ------------------------------------------------------------------
# Core generator
# ------------------------------------------------------------------
# The vectorized generation path moved to generation.py. Wrappers below keep
# legacy-level monkeypatches visible for tests and state.legacy consumers.
from .generation import (
    _INSTANCE_FILTER_NO_MATCH as _INSTANCE_FILTER_NO_MATCH,
    _build_timestamp_arrays as _generation_build_timestamp_arrays,
    _configure_generation_runtime as _configure_generation_runtime,
    _natural_column as _generation_natural_column,
    _resolve_instance_filter as _generation_resolve_instance_filter,
    generate_component as _generation_generate_component,
)
from .generation_emit import (
    _format_csv_row_block as _generation_format_csv_row_block,
    _format_metric_suffix as _generation_format_metric_suffix,
    _splice_dst_artifact as _generation_splice_dst_artifact,
)


def _generation_runtime_derivations():
    if DERIVATIONS is _GENERATION_DERIVATIONS_BINDING:
        return _generation_module.DERIVATIONS
    return DERIVATIONS


def _generation_runtime_topology_load_metrics():
    return _TOPOLOGY_LOAD_METRICS


def _generation_runtime_format_fixed3():
    return _format_fixed3


def _natural_column(spec: MetricSpec, ts_array: np.ndarray, elapsed: np.ndarray,
                    rng: "np.random.RandomState",
                    *,
                    noise: np.ndarray | None = None,
                    latency_factor: np.ndarray | None = None,
                    error_offset: np.ndarray | None = None,
                    baseline_override: np.ndarray | None = None) -> np.ndarray:
    return _generation_natural_column(
        spec, ts_array, elapsed, rng,
        noise=noise,
        latency_factor=latency_factor,
        error_offset=error_offset,
        baseline_override=baseline_override,
    )


def _resolve_instance_filter(spec_filter, instances: list["Instance"]):
    return _generation_resolve_instance_filter(spec_filter, instances)


def generate_component(component_name, specs: list[MetricSpec], anomaly_specs,
                       *, base_dir, total_seconds, drop_rate,
                       ctx: "RunContext",
                       interval=1.0,
                       ts_array=None, ts_strings=None, emit_metrics=True,
                       dst_inject_day=0, start_time: datetime.datetime = START,
                       instances: list["Instance"] | None = None,
                       topology_capture: dict[str, dict[str, np.ndarray]] | None = None,
                       topology_capture_by_instance: dict[str, list[dict[str, np.ndarray]]] | None = None,
                       coupling_arrays_per_instance: list[dict[str, np.ndarray]] | None = None,
                       saturation_arrays_per_instance: list[dict[str, tuple[np.ndarray | None, np.ndarray | None]]] | None = None,
                       apply_dtype_int_cast: bool = True):
    return _generation_generate_component(
        component_name, specs, anomaly_specs,
        base_dir=base_dir,
        total_seconds=total_seconds,
        drop_rate=drop_rate,
        ctx=ctx,
        interval=interval,
        ts_array=ts_array,
        ts_strings=ts_strings,
        emit_metrics=emit_metrics,
        dst_inject_day=dst_inject_day,
        start_time=start_time,
        instances=instances,
        topology_capture=topology_capture,
        topology_capture_by_instance=topology_capture_by_instance,
        coupling_arrays_per_instance=coupling_arrays_per_instance,
        saturation_arrays_per_instance=saturation_arrays_per_instance,
        apply_dtype_int_cast=apply_dtype_int_cast,
        runtime_key=__name__,
    )


def _format_metric_suffix(str_vals: np.ndarray) -> np.ndarray:
    return _generation_format_metric_suffix(str_vals)


def _format_csv_row_block(kept_ts: np.ndarray, metric_suffix: np.ndarray,
                          *, dim_prefix: str, dst_inject_day: int,
                          start_time: datetime.datetime = START) -> np.ndarray:
    return _generation_format_csv_row_block(
        kept_ts, metric_suffix,
        dim_prefix=dim_prefix,
        dst_inject_day=dst_inject_day,
        start_time=start_time,
    )


def _splice_dst_artifact(rows: np.ndarray, kept_ts: np.ndarray,
                         dst_day: int,
                         start_time: datetime.datetime = START) -> np.ndarray:
    return _generation_splice_dst_artifact(
        rows, kept_ts, dst_day, start_time=start_time
    )


def _format_fixed3(arr: np.ndarray) -> np.ndarray:
    return _generation_module._format_fixed3(arr)


def _build_timestamp_arrays(
    total_seconds: int,
    interval: float = 1.0,
    *,
    start_time: datetime.datetime = START,
):
    return _generation_build_timestamp_arrays(
        total_seconds, interval, start_time=start_time
    )

# register_cascade moved to scenario_builders.py with the other scenario helpers.

# ------------------------------------------------------------------
# ------------------------------------------------------------------
# Component and instance catalogs.
# ------------------------------------------------------------------
# COMPONENTS / INSTANCES and their catalog metadata validators moved to
# catalog.py (decomposition step 9A). legacy.py keeps the public binding and
# configures live callbacks so monkeypatches against legacy.COMPONENTS or
# legacy.INSTANCES remain visible to moved validation/config-reader helpers.
from .catalog import (
    COMPONENTS as COMPONENTS,
    DEFAULT_METRICS_PER_COMPONENT as DEFAULT_METRICS_PER_COMPONENT,
    INSTANCES as INSTANCES,
    MAX_INSTANCES_PER_COMPONENT as MAX_INSTANCES_PER_COMPONENT,
    MAX_METRICS_PER_COMPONENT as MAX_METRICS_PER_COMPONENT,
    _configure_catalog_runtime as _configure_catalog_runtime,
    _validate_instances_registry as _catalog_validate_instances_registry,
    _validate_metric_spec_schema_metadata as _catalog_validate_metric_spec_schema_metadata,
)


def _catalog_runtime_components():
    return COMPONENTS


def _catalog_runtime_instances():
    return INSTANCES


def _catalog_runtime_default_metrics_per_component():
    return DEFAULT_METRICS_PER_COMPONENT


def _catalog_runtime_max_instances_per_component():
    return MAX_INSTANCES_PER_COMPONENT


_configure_models_runtime(
    get_components=_catalog_runtime_components,
    get_max_instances_per_component=_catalog_runtime_max_instances_per_component,
    runtime_key=__name__,
)
_configure_catalog_runtime(
    get_components=_catalog_runtime_components,
    get_instances=_catalog_runtime_instances,
    get_default_metrics_per_component=_catalog_runtime_default_metrics_per_component,
    runtime_key=__name__,
)


def _validate_metric_spec_schema_metadata() -> None:
    return _catalog_validate_metric_spec_schema_metadata(runtime_key=__name__)


_validate_metric_spec_schema_metadata()




# ------------------------------------------------------------------
# Topology graph and composition helpers
# ------------------------------------------------------------------
# Topology models/registries/validators moved to topology_impl.py and the
# coupling/saturation math moved to topology_compose.py. The wrappers preserve
# legacy's patch-visible runtime view and keep import-time validation at this
# historical call site.
from .topology_impl import (
    TOPOLOGY as TOPOLOGY,
    _TOPOLOGY_COUPLE_NOISE_STD as _TOPOLOGY_COUPLE_NOISE_STD,
    _TOPOLOGY_LOAD_METRICS as _TOPOLOGY_LOAD_METRICS,
    _TOPOLOGY_SATURATION_TARGETS as _TOPOLOGY_SATURATION_TARGETS,
    _cache_miss_ratio_signal as _cache_miss_ratio_signal,
    _component_metric_base as _topology_component_metric_base,
    _configure_topology_runtime as _configure_topology_runtime,
    _topology_generation_order as _topology_generation_order_impl,
    _validate_saturation_params as _topology_validate_saturation_params,
    _validate_topology as _topology_validate_topology,
    _validate_topology_metric_registries as _topology_validate_topology_metric_registries,
)
from .topology_compose import (
    _apply_saturation as _topology_apply_saturation,
    _arrays_equal_dict as _arrays_equal_dict,
    _compose_topology_coupled_specs as _topology_compose_topology_coupled_specs,
    _compose_topology_saturation_specs as _topology_compose_topology_saturation_specs,
    _compute_topology_arrays_per_instance as _topology_compute_topology_arrays_per_instance,
    _matched_cardinality as _matched_cardinality,
    _per_instance_upstream_view as _per_instance_upstream_view,
    _sat_tuples_equal_dict as _sat_tuples_equal_dict,
)


def _topology_runtime_components():
    return COMPONENTS


def _topology_runtime_topology():
    return TOPOLOGY


def _topology_runtime_load_metrics():
    return _TOPOLOGY_LOAD_METRICS


def _topology_runtime_saturation_targets():
    return _TOPOLOGY_SATURATION_TARGETS


_configure_topology_runtime(
    get_components=_topology_runtime_components,
    get_topology=_topology_runtime_topology,
    get_topology_load_metrics=_topology_runtime_load_metrics,
    get_topology_saturation_targets=_topology_runtime_saturation_targets,
    runtime_key=__name__,
    activate=True,
)
_configure_generation_runtime(
    get_derivations=_generation_runtime_derivations,
    get_topology_load_metrics=_generation_runtime_topology_load_metrics,
    get_format_fixed3=_generation_runtime_format_fixed3,
    runtime_key=__name__,
)


def _component_metric_base(component: str, metric: str) -> float:
    return _topology_component_metric_base(component, metric, runtime_key=__name__)


def _validate_saturation_params(sat: SaturationParams, *, context: str) -> None:
    return _topology_validate_saturation_params(sat, context=context)


def _validate_topology() -> None:
    return _topology_validate_topology(runtime_key=__name__)


_validate_topology()


def _topology_generation_order(active_components: set[str]) -> list[str]:
    return _topology_generation_order_impl(active_components, runtime_key=__name__)


def _compose_topology_coupled_specs(
    component_name: str,
    specs: list[MetricSpec],
    upstream_arrays: dict[str, dict[str, np.ndarray]],
    rng: "np.random.RandomState",
    n_rows: int,
) -> list[MetricSpec]:
    return _topology_compose_topology_coupled_specs(
        component_name, specs, upstream_arrays, rng, n_rows,
        runtime_key=__name__,
    )


def _apply_saturation(
    upstream_load: np.ndarray, sat: SaturationParams,
) -> tuple[np.ndarray, np.ndarray]:
    return _topology_apply_saturation(upstream_load, sat)


def _validate_topology_metric_registries() -> None:
    return _topology_validate_topology_metric_registries(runtime_key=__name__)


_validate_topology_metric_registries()


def _compose_topology_saturation_specs(
    component_name: str,
    specs: list[MetricSpec],
    upstream_arrays: dict[str, dict[str, np.ndarray]],
    n_rows: int,
) -> list[MetricSpec]:
    return _topology_compose_topology_saturation_specs(
        component_name, specs, upstream_arrays, n_rows,
        runtime_key=__name__,
    )


def _compute_topology_arrays_per_instance(
    component_name: str,
    specs: list[MetricSpec],
    upstream_arrays_shared: dict[str, dict[str, np.ndarray]],
    upstream_arrays_by_instance: dict[str, list[dict[str, np.ndarray]]],
    instances: list["Instance"],
    rng: "np.random.RandomState",
    n_rows: int,
) -> tuple[
    list[dict[str, np.ndarray]],
    list[dict[str, tuple[np.ndarray | None, np.ndarray | None]]],
]:
    return _topology_compute_topology_arrays_per_instance(
        component_name, specs, upstream_arrays_shared,
        upstream_arrays_by_instance, instances, rng, n_rows,
        runtime_key=__name__,
    )
# Ordered scenario data moved to scenario_catalog.py (decomposition step 9B).
from .scenario_catalog import SCENARIOS as SCENARIOS


# Scenario validation moved to scenario_validation.py. Compatibility wrappers
# keep patched ``legacy.SCENARIOS`` and ``legacy.COMPONENTS`` visible and retain
# the sole historical import-time validation call below.
from .scenario_validation import (
    _validate_scenario_spec as _scenario_validate_spec,
    _validate_scenarios_registry as _scenario_validate_registry,
)


def _validate_scenario_spec(slug: str, component: str, spec: dict,
                            *, is_cascade: bool) -> None:
    return _scenario_validate_spec(
        slug,
        component,
        spec,
        is_cascade=is_cascade,
        components=COMPONENTS,
        valid_anomaly_shapes=_VALID_ANOMALY_SHAPES,
        generator_meta=_generator_meta,
    )


def _validate_scenarios_registry() -> None:
    return _scenario_validate_registry(
        SCENARIOS,
        COMPONENTS,
        valid_anomaly_shapes=_VALID_ANOMALY_SHAPES,
        generator_meta=_generator_meta,
        seconds_per_day=SECONDS_PER_DAY,
    )


_validate_scenarios_registry()


def _validate_derivations_registry() -> None:
    """Import-time invariants for ``DERIVATIONS``.

    Catches drift between the derivation registry and ``COMPONENTS``: a
    misnamed component or column would silently no-op (the dict lookup
    misses) or silently mis-target (the name lookup in the derivation
    misses), and the test-side ``DERIVED_METRICS`` exemption would skip a
    column that no longer exists. Failing fast at import time forces
    these to stay in lockstep.
    """
    known_components = set(COMPONENTS.keys())
    derivations = _generation_runtime_derivations()
    for component, (_, metrics) in derivations.items():
        if component not in known_components:
            raise ValueError(
                f"DERIVATIONS references unknown component {component!r}; "
                f"expected one of {sorted(known_components)}"
            )
        known_metrics = {spec.name for spec in COMPONENTS[component]}
        unknown_metrics = sorted(set(metrics) - known_metrics)
        if unknown_metrics:
            raise ValueError(
                f"DERIVATIONS[{component!r}] declares derived metrics "
                f"{unknown_metrics} that are not in COMPONENTS[{component!r}]; "
                f"register the MetricSpec first or correct the name."
            )
    # Reverse direction: the consistency must hold both ways. A
    # MetricSpec that declares a ``derivation`` string without a
    # matching DERIVATIONS entry would emit a schema.json claiming a
    # derivation the generator never recomputes, and the failure would
    # surface only at ``validate``-subcommand time as a runtime KeyError
    # from the strict ``_RECOMPUTERS[...]`` lookup instead of a clear
    # import-time error here. A DERIVATIONS metric whose MetricSpec
    # does NOT declare a ``derivation`` string is the mirror drift: the
    # generator recomputes the column but the schema never tells the
    # validator to check it.
    for component, specs in COMPONENTS.items():
        declared = {s.name for s in specs if s.derivation is not None}
        registered = set(derivations.get(component, (None, ()))[1])
        unregistered = sorted(declared - registered)
        if unregistered:
            raise ValueError(
                f"COMPONENTS[{component!r}] metrics {unregistered} declare "
                "a `derivation` string but have no DERIVATIONS entry; the "
                "generator would never recompute them and the validate subcommand "
                "would fail with a KeyError. Add the DERIVATIONS (and "
                "_RECOMPUTERS) entries in lockstep."
            )
        undeclared = sorted(registered - declared)
        if undeclared:
            raise ValueError(
                f"DERIVATIONS[{component!r}] recomputes metrics "
                f"{undeclared} whose MetricSpec declares no `derivation` "
                "string; schema.json would omit the derivation and "
                "the validate subcommand would silently skip the check. Declare "
                "`derivation=` on the MetricSpec."
            )


_validate_derivations_registry()


# Instance registry validation moved to catalog.py; _validate_instance_list and
# _load_instance_config live in models_impl.py. The wrappers keep legacy's
# patch-visible runtime view and preserve the old import-time call position.
def _validate_instances_registry() -> None:
    return _catalog_validate_instances_registry(runtime_key=__name__)


_validate_instances_registry()


def _load_instance_config(path: "Path") -> dict[str, list["Instance"]]:
    return _models_load_instance_config(path, runtime_key=__name__)




def _resolve_effective_specs(metrics_per_component: int | None) -> dict[str, list[MetricSpec]]:
    """Return ``{component: specs[:limit]}`` for the active --metrics-per-component.

    When ``metrics_per_component`` is None, each component is trimmed to its
    historic ``DEFAULT_METRICS_PER_COMPONENT`` count so default CSV output
    stays byte-for-byte identical. When provided, every component is trimmed
    to the same N (capped to its catalog size).
    """
    resolved: dict[str, list[MetricSpec]] = {}
    for name, specs in COMPONENTS.items():
        if metrics_per_component is None:
            limit = DEFAULT_METRICS_PER_COMPONENT[name]
        else:
            limit = min(metrics_per_component, len(specs))
        resolved[name] = specs[:limit]
    return resolved


def _filter_anomalies_for_emitted_metrics(component_anomalies: dict,
                                           cascade_registry: dict,
                                           effective_specs: dict) -> None:
    """Drop anomaly specs whose metric was trimmed by ``--metrics-per-component``.

    Two distinct cases are handled differently:

    - Metric is in the full ``COMPONENTS[component]`` catalog but not in the
      trimmed ``effective_specs[component]`` prefix → silently dropped. This
      is the intended behavior of the cap.
    - Metric (or component) is not in the full catalog at all → raise
      ``ValueError``. This is a typo in an ``anoms_*`` list or a
      ``register_cascade`` call and would otherwise be silently swallowed.

    Filtering happens in-place before the severity / count gates so the
    anomaly-count cap pool reflects what can actually emit.
    """
    full_catalog = {name: {s.name for s in specs}
                    for name, specs in COMPONENTS.items()}
    emitted = {name: {s.name for s in specs}
               for name, specs in effective_specs.items()}

    def _validate_and_filter(specs: list[dict], component: str) -> list[dict]:
        unknown: list[tuple[str, str, str]] = []
        catalog = full_catalog.get(component, set())
        emitted_for_component = emitted.get(component, set())
        kept: list[dict] = []
        for spec in specs:
            metric = spec["metric"]
            if metric not in catalog:
                unknown.append((component, metric, spec.get("description", "")))
                continue
            if metric in emitted_for_component:
                kept.append(spec)
            # else: known metric trimmed by the cap — silent drop is intentional
        if unknown:
            raise ValueError(
                "Anomaly spec(s) reference metrics or components missing "
                f"from COMPONENTS (component, metric, description): {unknown}"
            )
        return kept

    for name in list(component_anomalies.keys()):
        component_anomalies[name] = _validate_and_filter(
            component_anomalies[name], name
        )
    for name in list(cascade_registry.keys()):
        cascade_registry[name] = _validate_and_filter(
            cascade_registry[name], name
        )


# Scenario runtime composition moved to scenarios_impl.py. A named live getter
# preserves monkeypatch visibility without importing ``legacy`` from the leaf.
from .scenarios_impl import (
    _apply_scenarios as _scenarios_apply,
    _apply_signal_level_and_count as _scenarios_apply_signal_level_and_count,
    _configure_scenarios_runtime as _configure_scenarios_runtime,
    _resolve_scenarios as _scenarios_resolve,
)


def _scenario_runtime_scenarios():
    return SCENARIOS


_configure_scenarios_runtime(
    get_scenarios=_scenario_runtime_scenarios,
    runtime_key=__name__,
)


def _apply_signal_level_and_count(component_anomalies: dict, cascade_registry: dict,
                                  *, signal_level: str, selected_components: set,
                                  anomaly_count: int | None, seed: int,
                                  total_seconds: int, interval_seconds: float) -> None:
    return _scenarios_apply_signal_level_and_count(
        component_anomalies,
        cascade_registry,
        signal_level=signal_level,
        selected_components=selected_components,
        anomaly_count=anomaly_count,
        seed=seed,
        total_seconds=total_seconds,
        interval_seconds=interval_seconds,
        signal_levels=SIGNAL_LEVELS,
        default_severity=DEFAULT_SEVERITY,
        anomaly_count_cap_salt=_ANOMALY_COUNT_CAP_SALT,
    )


def _resolve_scenarios(args) -> set[str]:
    return _scenarios_resolve(
        args,
        signal_levels=SIGNAL_LEVELS,
        runtime_key=__name__,
    )


def _apply_scenarios(component_anomalies: dict, cascade_registry: dict,
                     active_scenarios: set[str]) -> None:
    return _scenarios_apply(
        component_anomalies,
        cascade_registry,
        active_scenarios,
        runtime_key=__name__,
    )


# ------------------------------------------------------------------
# CLI + entry point
# ------------------------------------------------------------------


# CLI parsing and subcommand helpers moved to cli_args.py (decomposition
# step 8). Re-imported here so tests, server state, and the historic
# ``legacy.<name>`` surface stay unchanged; new code should import from
# anomaly_metric_creator.cli_args directly.
from .cli_args import (
    _ADVANCED_DESTS as _ADVANCED_DESTS,
    _SUBCOMMANDS as _SUBCOMMANDS,
    _configure_cli_runtime as _configure_cli_runtime,
    _flag_in_argv as _flag_in_argv,
    _main_combine_subcommand as _cli_main_combine_subcommand,
    _main_serve_subcommand as _cli_main_serve_subcommand,
    _main_trace_bundle_subcommand as _cli_main_trace_bundle_subcommand,
    _main_validate_subcommand as _cli_main_validate_subcommand,
    _parse_components_value as _cli_parse_components_value,
    _parse_start_time_arg as _parse_start_time_arg,
    _reconcile_cli_surface as _reconcile_cli_surface,
    parse_args as _cli_parse_args,
)
_CLI_RUNTIME_KEY = __name__
_configure_cli_runtime(
    get_components=lambda: COMPONENTS,
    get_scenarios=lambda: SCENARIOS,
    get_default_metrics_per_component=lambda: DEFAULT_METRICS_PER_COMPONENT,
    get_legacy_module=lambda: sys.modules[__name__],
    constants={
        "DEFAULT_DROP_RATE": DEFAULT_DROP_RATE,
        "DEFAULT_DURATION_DAYS": DEFAULT_DURATION_DAYS,
        "DEFAULT_INTERVAL_SECONDS": DEFAULT_INTERVAL_SECONDS,
        "DEFAULT_OUTPUT_DIR": DEFAULT_OUTPUT_DIR,
        "DEFAULT_OTEL_STREAM_AUTH_SCHEME": DEFAULT_OTEL_STREAM_AUTH_SCHEME,
        "DEFAULT_ROW_COUNT": DEFAULT_ROW_COUNT,
        "DEFAULT_SEED": DEFAULT_SEED,
        "DEFAULT_SIGNAL_LEVEL": DEFAULT_SIGNAL_LEVEL,
        "MAX_INSTANCES_PER_COMPONENT": MAX_INSTANCES_PER_COMPONENT,
        "MAX_METRICS_PER_COMPONENT": MAX_METRICS_PER_COMPONENT,
        "PREFLIGHT_CELL_CAP": PREFLIGHT_CELL_CAP,
        "SECONDS_PER_DAY": SECONDS_PER_DAY,
        "SIGNAL_LEVELS": SIGNAL_LEVELS,
        "START": START,
    },
    runtime_key=_CLI_RUNTIME_KEY,
)


def _parse_components_value(error, raw: str) -> set[str]:
    return _cli_parse_components_value(error, raw, runtime_key=_CLI_RUNTIME_KEY)


def parse_args(argv=None):
    return _cli_parse_args(argv, runtime_key=_CLI_RUNTIME_KEY)


def _main_combine_subcommand(argv):
    return _cli_main_combine_subcommand(argv, runtime_key=_CLI_RUNTIME_KEY)


def _main_validate_subcommand(argv):
    return _cli_main_validate_subcommand(argv, runtime_key=_CLI_RUNTIME_KEY)


def _main_serve_subcommand(argv):
    return _cli_main_serve_subcommand(argv, runtime_key=_CLI_RUNTIME_KEY)


def _main_trace_bundle_subcommand(argv):
    return _cli_main_trace_bundle_subcommand(argv, runtime_key=_CLI_RUNTIME_KEY)


# ------------------------------------------------------------------
# Emit artifact registry: map selectable outputs to their canonical filenames.
# One row per timestamp; columns prefixed with the component name.
# ------------------------------------------------------------------
# Filenames written into --output-dir for each --emit item.
# Per-component CSVs are derived from args.components, not listed here.
# Consumed by _pre_clean_output_dir() and by the end-of-run summary line.
_EMIT_ARTIFACT_FILES = {
    "metrics": ("anomalies.csv",),
    "logs": ("metric_report.log",),
    "traces": ("metric_traces.jsonl",),
    "gauges": ("gauges.csv",),
    "schema": ("schema.json",),
}


# The combine writers, autodiscovery, monotonic pre-scan, and the
# _NON_COMPONENT_FILES / _COMBINE_OUTPUT_FILENAME constants moved verbatim
# to combine_impl.py (decomposition step 5). Re-imported here so the
# combine subcommand, main()'s combined-artifact pass, the pre-clean /
# summary uses of _COMBINE_OUTPUT_FILENAME, and tests keep the historic
# ``legacy.<name>`` surface. (_EMIT_ARTIFACT_FILES stays — it is a core
# emit registry, not combine-specific.) New code should import from
# anomaly_metric_creator.combine_impl directly.
from .combine_impl import (
    _COMBINE_OUTPUT_FILENAME as _COMBINE_OUTPUT_FILENAME,
    _NON_COMPONENT_FILES as _NON_COMPONENT_FILES,
    _wide_component_rows_are_monotonic as _wide_component_rows_are_monotonic,  # noqa: F401
    _write_combined_long_form as _write_combined_long_form,  # noqa: F401
    _write_combined_wide_materialized as _write_combined_wide_materialized,  # noqa: F401
    combine_logs as combine_logs,
    combine_logs_unified as combine_logs_unified,
    discover_components as discover_components,
)


def write_reporting_artifacts(
    output_dir: Path,
    anomaly_rows: list[dict],
    *,
    emit_logs: bool = True,
    emit_traces: bool = True,
) -> None:
    """Emit correlated log and trace artifacts aligned to anomaly metric records.

    ``emit_logs`` / ``emit_traces`` gate which file is written; both default to
    True to preserve the historic two-file behavior for direct callers.
    """
    output_dir = Path(output_dir)
    log_path = output_dir / "metric_report.log"
    trace_path = output_dir / "metric_traces.jsonl"

    with contextlib.ExitStack() as stack:
        log_f = (
            stack.enter_context(_atomic_artifact_open(log_path))
            if emit_logs
            else None
        )
        trace_f = (
            stack.enter_context(_atomic_artifact_open(trace_path))
            if emit_traces
            else None
        )
        for entry in anomaly_rows:
            event_id = _anomaly_event_id(entry)
            component = entry["component"]
            metric = entry["metric"]
            timestamp = entry["timestamp"]
            description = entry["description"]

            if log_f is not None:
                # Escape embedded double quotes so the key=value line
                # stays parseable if a future catalog description carries
                # one (today's descriptions are quote-free, so emitted
                # bytes are unchanged). Mirrors the shlex.quote posture
                # of _write_activity.
                safe_description = description.replace('"', '\\"')
                log_f.write(
                    f"{timestamp} INFO metric_report event_id={event_id} "
                    f"component={component} metric={metric} msg=\"{safe_description}\"\n"
                )

            if trace_f is not None:
                trace_f.write(json.dumps({
                    "timestamp": timestamp,
                    "trace_id": f"trace_{event_id[4:]}",
                    "span_id": f"span_{event_id[4:12]}",
                    "event_id": event_id,
                    "signal_type": "metric_anomaly",
                    "component": component,
                    "metric": metric,
                    "description": description,
                }) + "\n")


# Timestamp parsing / unix-nano helpers moved verbatim to timeutil.py
# (decomposition step 2). Re-imported here so the historic
# ``legacy.<name>`` surface (tests, state.legacy lookups, the merge
# writers below) is unchanged; new code should import from
# anomaly_metric_creator.timeutil directly.
from .timeutil import (
    _UNIX_EPOCH_UTC as _UNIX_EPOCH_UTC,  # noqa: F401
    _dt_to_unix_nanos as _dt_to_unix_nanos,
    _parse_csv_timestamp as _parse_csv_timestamp,
    _to_unix_nanos as _to_unix_nanos,
)

# _anomaly_event_id and the eight _build_otlp_* payload builders moved
# verbatim to otlp.py (decomposition step 2). Re-imported here so
# write_reporting_artifacts, the OTEL streamers, and tests keep the historic
# ``legacy.<name>`` binding; new code should import from
# anomaly_metric_creator.otlp directly.
from .otlp import (
    _anomaly_event_id as _anomaly_event_id,
    _build_otlp_gauge_payload as _build_otlp_gauge_payload,
    _build_otlp_gauge_protobuf as _build_otlp_gauge_protobuf,
    _build_otlp_log_payload as _build_otlp_log_payload,
    _build_otlp_log_protobuf as _build_otlp_log_protobuf,
    _build_otlp_metric_payload as _build_otlp_metric_payload,
    _build_otlp_metric_protobuf as _build_otlp_metric_protobuf,
    _build_otlp_trace_payload as _build_otlp_trace_payload,
    _build_otlp_trace_protobuf as _build_otlp_trace_protobuf,
)

# Header redaction moved verbatim to redaction.py (decomposition step 1),
# including the canonical lowercased allowlist prose. Re-imported here
# so the historic ``legacy.<name>`` surface — the shim, facades, tests, and
# ``state.legacy`` attribute lookups — is unchanged. New code should import
# from ``anomaly_metric_creator.redaction`` directly.
from .redaction import (
    _SAFE_RESPONSE_HEADER_NAMES as _SAFE_RESPONSE_HEADER_NAMES,  # noqa: F401
    _SCHEMED_SENSITIVE_HEADERS as _SCHEMED_SENSITIVE_HEADERS,  # noqa: F401
    _SENSITIVE_HEADER_NAMES as _SENSITIVE_HEADER_NAMES,  # noqa: F401
    _mask_sensitive_value as _mask_sensitive_value,  # noqa: F401
    _masked_headers as _masked_headers,
    _redact_sensitive_headers as _redact_sensitive_headers,
)

# OTEL transport streamers and activity-log helpers moved verbatim to
# otel_stream.py (decomposition step 7). Re-imported here so main(), tests,
# state.legacy lookups, and the historic ``legacy.<name>`` surface are
# unchanged; new code should import from anomaly_metric_creator.otel_stream
# directly.
from .otel_stream import (
    _http_error_activity_fields as _http_error_activity_fields,
    _verbose_body_repr as _verbose_body_repr,
    _write_activity as _write_activity,
    stream_otel_gauges as stream_otel_gauges,
    stream_otel_signals as stream_otel_signals,
)

# The shared per-component CSV primitives moved verbatim to csv_layout.py
# (decomposition step 3). Re-imported here so the combine long-form writer,
# the OTEL gauge streamer, tests, and state.legacy lookups keep the
# historic ``legacy.<name>`` surface; new code should import from
# anomaly_metric_creator.csv_layout directly.
from .csv_layout import (
    _LONG_FORM_FD_MARGIN as _LONG_FORM_FD_MARGIN,  # noqa: F401
    _classify_component_csv_header as _classify_component_csv_header,
    _ensure_long_form_fd_capacity as _ensure_long_form_fd_capacity,
    _iter_component_instance_rows as _iter_component_instance_rows,
    _iter_component_rows as _iter_component_rows,
    _scan_component_csv_headers as _scan_component_csv_headers,
    _scan_instance_block_layout as _scan_instance_block_layout,
)
# write_gauges_csv moved verbatim to gauges_impl.py (decomposition step 3);
# re-imported here so main()'s gauge pass and tests keep the historic
# ``legacy.write_gauges_csv`` binding.
from .gauges_impl import write_gauges_csv as write_gauges_csv
# Schema writer and output validator helpers moved to schema_impl.py and
# validate_impl.py (decomposition step 6). Re-imported here so the historic
# ``legacy.<name>`` surface, package facades, tests, and state.legacy lookups
# keep working unchanged.
from .schema_impl import (
    SCHEMA_DOCUMENT_VERSION as SCHEMA_DOCUMENT_VERSION,
    _configure_schema_runtime as _configure_schema_runtime,
    _metric_spec_to_schema_entry as _metric_spec_to_schema_entry,
    _saturation_params_to_schema_entry as _saturation_params_to_schema_entry,
    _edge_to_schema_entry as _edge_to_schema_entry,
    _component_dimensions_schema_entry as _component_dimensions_schema_entry,
    _serialize_topology as _serialize_topology,
    write_schema_json as write_schema_json,
)
_configure_schema_runtime(get_topology=lambda: TOPOLOGY)

from .validate_cells import (
    _VALIDATE_DERIVATION_TOLERANCE as _VALIDATE_DERIVATION_TOLERANCE,
    _VALIDATE_INT_TOLERANCE as _VALIDATE_INT_TOLERANCE,
    _recompute_cacheservice as _recompute_cacheservice,
    _RECOMPUTERS as _RECOMPUTERS,
    _schema_has_any_dimensions as _schema_has_any_dimensions,
)
from .validate_impl import (
    _configure_validate_runtime as _configure_validate_runtime,
    Violation as Violation,
    _json_path as _json_path,
    _schema_shape_error as _schema_shape_error,
    _require_schema_mapping as _require_schema_mapping,
    _require_schema_list as _require_schema_list,
    _require_schema_string as _require_schema_string,
    _require_schema_number as _require_schema_number,
    _validate_string_list_schema_shape as _validate_string_list_schema_shape,
    _validate_schema_document_shape as _validate_schema_document_shape,
    _load_schema_document as _load_schema_document,
    _validate_required_files_present as _validate_required_files_present,
    _validate_no_unknown_files as _validate_no_unknown_files,
    _validate_anomalies_sorted as _validate_anomalies_sorted,
    _validate_component_row_count as _validate_component_row_count,
    _validate_component_timestamp_coverage as _validate_component_timestamp_coverage,
    _validate_component_cells as _validate_component_cells,
    _validate_component_derivations as _validate_component_derivations,
    _filter_windows_for_pair as _filter_windows_for_pair,
    _validate_topology_coupling as _validate_topology_coupling,
    _validate_topology_coupling_per_instance as _validate_topology_coupling_per_instance,
    _resolve_edge_correlation_threshold as _resolve_edge_correlation_threshold,
    _validate_long_form_dimensions as _validate_long_form_dimensions,
    validate_output as validate_output,
)
from .validate_topology import (
    _TOPOLOGY_DEFAULT_CORRELATION_THRESHOLD as _TOPOLOGY_DEFAULT_CORRELATION_THRESHOLD,
    _TOPOLOGY_CORRELATION_EXCLUSION_PAD_SECONDS as _TOPOLOGY_CORRELATION_EXCLUSION_PAD_SECONDS,
    _TOPOLOGY_MIN_ALIGNED_ROWS as _TOPOLOGY_MIN_ALIGNED_ROWS,
    _read_component_metric_column as _read_component_metric_column,
    _read_anomaly_exclusion_windows as _read_anomaly_exclusion_windows,
    _compute_anomaly_keep_mask as _compute_anomaly_keep_mask,
)
from .validate_topology_instances import (
    _read_component_metric_column_per_instance as _read_component_metric_column_per_instance,
)
_configure_validate_runtime(
    get_topology=lambda: TOPOLOGY,
    get_topology_load_metrics=lambda: _TOPOLOGY_LOAD_METRICS,
)

def _collect_emitted_filenames(*, emit_selection, components, combine):
    """Return the sorted list of filenames a run with the given options writes.

    Same single source of truth ``_pre_clean_output_dir`` and the end-of-run
    summary already consume: ``_EMIT_ARTIFACT_FILES`` for emit-typed artifacts,
    ``_COMBINE_OUTPUT_FILENAME`` for the combine output, and one
    ``{component}.csv`` per allowlisted component when ``metrics`` is selected.

    Used by ``write_schema_json`` and the ``validate`` subcommand to keep the
    expected-file-set check anchored to one definition.
    """
    files: set[str] = set()
    if "metrics" in emit_selection:
        for component in components:
            files.add(f"{component}.csv")
    for emit_type, artifact_files in _EMIT_ARTIFACT_FILES.items():
        if emit_type in emit_selection:
            files.update(artifact_files)
    if combine:
        files.add(_COMBINE_OUTPUT_FILENAME)
    return sorted(files)


def _known_artifact_filenames():
    """Every artifact filename this script can write into --output-dir.

    Derived from the same registries the pre-clean and end-of-run summary
    consume (`COMPONENTS`, `_EMIT_ARTIFACT_FILES`, `_COMBINE_OUTPUT_FILENAME`)
    so the temp-sibling sweep cannot drift from the real write slots.
    """
    filenames = [f"{component}.csv" for component in COMPONENTS]
    for files in _EMIT_ARTIFACT_FILES.values():
        filenames.extend(files)
    filenames.append(_COMBINE_OUTPUT_FILENAME)
    return filenames


def _pre_clean_output_dir(output_dir, emit_selection, selected_components, combine):
    """Remove stale artifacts from a prior run that this run will not regenerate.

    Called right after --output-dir is created. Idempotent on missing files.
    Files unknown to this script (e.g. user notes, the synthetic-extra-component
    CSV the test fixture relies on for combine autodiscovery) are left alone.
    Not called by the ``combine`` subcommand; that path reads existing
    per-component CSVs as inputs.

    Files this run *will* regenerate are intentionally left in place: every
    generated-artifact writer publishes through ``_atomic_artifact_open``
    (temp sibling + ``os.replace``), so the previous run's content stays
    fully readable until the instant the new content replaces it. Deleting
    here would reopen the mid-delete visibility gap the atomic writers close.
    Stale ``*.tmp`` siblings from a crashed prior run are swept for every
    registry-known artifact slot regardless of the emit selection — a temp
    is never a valid artifact.
    """
    for filename in _known_artifact_filenames():
        (output_dir / (filename + _ATOMIC_TMP_SUFFIX)).unlink(missing_ok=True)
    metrics_on = "metrics" in emit_selection
    # Per-component CSVs: drop any that this run will not (re)write — either
    # because metrics was dropped from --emit or because the
    # component is no longer in --components.
    for component in COMPONENTS:
        if metrics_on and component in selected_components:
            continue
        (output_dir / f"{component}.csv").unlink(missing_ok=True)
    # Emit-typed artifacts: drop files for any emit type not selected.
    for emit_type, files in _EMIT_ARTIFACT_FILES.items():
        if emit_type in emit_selection:
            continue
        for filename in files:
            (output_dir / filename).unlink(missing_ok=True)
    # combined_metrics_unified.csv: only the 'combined' artifact writes it. Drop stale
    # output otherwise so it can't masquerade as this run's result.
    if not combine:
        (output_dir / _COMBINE_OUTPUT_FILENAME).unlink(missing_ok=True)


def main(argv=None):
    # Subcommand dispatch: 'generate' (the default when the first token is
    # not a subcommand, preserving every historic invocation), 'combine',
    # 'validate', 'serve', and 'trace-bundle'. Handled before argparse so
    # the flat generate parser never sees the subcommand token.
    argv = list(sys.argv[1:]) if argv is None else list(argv)
    if argv and argv[0] in _SUBCOMMANDS:
        sub, rest = argv[0], argv[1:]
        if sub == "combine":
            return _main_combine_subcommand(rest)
        if sub == "validate":
            return _main_validate_subcommand(rest)
        if sub == "serve":
            return _main_serve_subcommand(rest)
        if sub == "trace-bundle":
            return _main_trace_bundle_subcommand(rest)
        argv = rest  # generate: strip the token, fall through.

    args = parse_args(argv)

    # Generation knows exactly which component CSVs it just wrote. Always pass
    # that explicit allowlist to the combined writer so stale/foreign CSVs left
    # in --output-dir cannot be folded into this run's artifacts. The standalone
    # ``combine DIR`` subcommand keeps autodiscovery when its --components value
    # is the default "all". For generation's own default "all", keep the
    # discover_components-compatible sorted order for byte-parity with a later
    # ``combine DIR`` over a clean generated directory.
    if args.components == set(COMPONENTS.keys()):
        combine_components = sorted(COMPONENTS)
    else:
        combine_components = [name for name in COMPONENTS if name in args.components]

    total_seconds = SECONDS_PER_DAY * args.duration_days
    args.output_dir.mkdir(exist_ok=True, parents=True)
    _pre_clean_output_dir(
        args.output_dir,
        args.emit_selection,
        args.components,
        args.combine,
    )
    ctx = RunContext(rng=np.random.RandomState(args.seed))
    # Seed the per-run instance map. Phase 1 default: one anonymous Instance()
    # per component → byte-identical output. Phase 2: --instances-per-component
    # N > 1 fans every component out to N named instances (id=i0..iN-1,
    # pod=pod-0..pod-N-1); all other dimension fields remain None in v1.
    if args.instance_config is not None:
        # Phase 3: --instance-config populates the per-component map from file;
        # missing components fall back to the module-level INSTANCES registry
        # (default [Instance()] per component).
        try:
            config_map = _load_instance_config(args.instance_config)
        except ValueError as exc:
            sys.exit(str(exc))
        ctx.instances = {
            name: (
                config_map[name]
                if name in config_map
                else list(INSTANCES[name])
            )
            for name in COMPONENTS
        }
    elif args.instances_per_component == 1:
        ctx.instances = {name: list(INSTANCES[name]) for name in COMPONENTS}
    else:
        n = args.instances_per_component
        fan_out = [Instance(id=f"i{k}", pod=f"pod-{k}") for k in range(n)]
        ctx.instances = {name: list(fan_out) for name in COMPONENTS}

    # Build component_anomalies and cascading_anomalies entirely from the
    # SCENARIOS registry. _resolve_scenarios() applies the --scenarios /
    # --exclude-scenarios / --signal-level / --duration-days / --components
    # gates; _apply_scenarios() walks the resolved set in declaration order
    # and tail-appends each scenario's primaries and cascades.
    component_anomalies = {name: [] for name in COMPONENTS}
    active_scenarios = _resolve_scenarios(args)
    _apply_scenarios(component_anomalies, ctx.cascading_anomalies, active_scenarios)

    effective_specs = _resolve_effective_specs(args.metrics_per_component)
    _filter_anomalies_for_emitted_metrics(
        component_anomalies, ctx.cascading_anomalies, effective_specs
    )

    _apply_signal_level_and_count(
        component_anomalies,
        ctx.cascading_anomalies,
        signal_level=args.signal_level,
        selected_components=args.components,
        anomaly_count=args.anomaly_count,
        seed=args.seed,
        total_seconds=total_seconds,
        interval_seconds=args.interval_seconds,
    )

    ts_array, ts_strings = _build_timestamp_arrays(
        total_seconds,
        args.interval_seconds,
        start_time=args.start_time,
    )
    n_rows = int(total_seconds // args.interval_seconds)

    # Topology phase 2 / phase 6 flag day: we walk
    # ``args.components`` in topological order (roots first) and stash
    # each generated component's load-metric columns so downstream
    # components can reshape their baseline via
    # ``_compose_topology_coupled_specs`` and layer saturation feedback
    # via ``_compose_topology_saturation_specs``. (The deprecated
    # ``--topology-mode independent`` no-topology contrast alias was
    # removed at the phase-9 flag day; realistic is the only mode.)
    active = set(args.components)
    generation_order = [
        name for name in _topology_generation_order(active)
        if name in effective_specs
    ]
    upstream_arrays: dict[str, dict[str, np.ndarray]] = {}
    # phase 8: parallel per-instance capture. Populated by
    # ``generate_component`` whenever ``--instances-per-component
    # N>1`` (or a non-default ``--instance-config``) makes the
    # component dim-aware. Consumed by
    # ``_compute_topology_arrays_per_instance`` so each downstream
    # instance gets a "matching instance set" view of its upstream
    # (see CLAUDE.md § Per-instance topology).
    upstream_arrays_by_instance: dict[str, list[dict[str, np.ndarray]]] = {}

    for name in generation_order:
        specs = effective_specs[name]
        coupling_per_instance = None
        saturation_per_instance = None
        instances_for_component = ctx.instances[name]
        n_inst_local = len(instances_for_component)
        is_anonymous_local = _is_anonymous_instance_list(instances_for_component)

        # Realistic is the only topology mode (phase-9 flag day
        # removed the independent contrast alias).
        if n_inst_local > 1 or not is_anonymous_local:
            # phase 8 — per-instance dispatch. Skip the
            # spec-modifying composers; compute per-instance
            # arrays directly. ``_compute_topology_arrays_per_instance``
            # shares the ``_TOPOLOGY_COUPLE_NOISE_STD`` draw across
            # instances so symmetric upstream produces byte-identical
            # output to the shared lambda-baked path used by the
            # N=1 anonymous branch below. ``generate_component``
            # re-derives divergence from the returned arrays directly
            # so the helper does not need to return a hint.
            (
                coupling_per_instance,
                saturation_per_instance,
            ) = _compute_topology_arrays_per_instance(
                name, specs, upstream_arrays,
                upstream_arrays_by_instance,
                instances_for_component, ctx.rng, n_rows,
            )
        else:
            # N=1 anonymous — today's shared lambda-baked path.
            # Byte-parity contract: the default
            # ``--instances-per-component 1`` keeps this branch.
            specs = _compose_topology_coupled_specs(
                name, specs, upstream_arrays, ctx.rng, n_rows
            )
            # Phase 4: saturation feedback. Layers logistic-shaped
            # latency multipliers and error offsets on top of the coupled
            # baseline so downstream latency/error metrics respond to
            # upstream load. Composes on top of any existing multiplier /
            # additive (e.g. ``_daily_sine``) so seasonal patterns survive.
            specs = _compose_topology_saturation_specs(
                name, specs, upstream_arrays, n_rows
            )
        generate_component(name, specs, component_anomalies[name],
                           base_dir=args.output_dir,
                           total_seconds=total_seconds,
                           drop_rate=args.drop_rate,
                           interval=args.interval_seconds,
                           ts_array=ts_array,
                           ts_strings=ts_strings,
                           emit_metrics="metrics" in args.emit_selection,
                           dst_inject_day=args.inject_dst_artifact_day,
                           start_time=args.start_time,
                           ctx=ctx,
                           instances=ctx.instances[name],
                           topology_capture=upstream_arrays,
                           topology_capture_by_instance=(
                               upstream_arrays_by_instance if (
                                   n_inst_local > 1 or not is_anonymous_local
                               ) else None
                           ),
                           coupling_arrays_per_instance=coupling_per_instance,
                           saturation_arrays_per_instance=saturation_per_instance,
                           apply_dtype_int_cast=True)

    filtered_anomalies = [a for a in ctx.anomalies if a["component"] in args.components]

    # Enrich each manifest entry with ``event_id`` and ``parent_event_id`` before
    # sorting. ``event_id`` is a pure function of the four required fields
    # (timestamp, component, metric, description) — sort order does not affect
    # it. ``parent_event_id`` is computed in original (insertion) order so that
    # for each scenario the first non-cascade entry observed (which reflects
    # the COMPONENTS iteration order × per-component row_idx ordering) becomes
    # the canonical parent for every cascade row of the same scenario.
    scenario_first_primary_event_id: dict[str, str] = {}
    for entry in filtered_anomalies:
        entry["event_id"] = _anomaly_event_id(entry)
        scenario_id = entry.get("scenario_id", "")
        is_cascade = entry.get("is_cascade") == "true"
        if scenario_id and not is_cascade:
            scenario_first_primary_event_id.setdefault(scenario_id, entry["event_id"])
    for entry in filtered_anomalies:
        is_cascade = entry.get("is_cascade") == "true"
        scenario_id = entry.get("scenario_id", "")
        if is_cascade and scenario_id:
            # Orphan cascades (no surviving primary for the scenario, e.g. all
            # primaries dropped by --drop-rate) leave parent_event_id empty.
            entry["parent_event_id"] = scenario_first_primary_event_id.get(scenario_id, "")
        else:
            entry["parent_event_id"] = ""

    # Sort chronologically by ``(span_start, component, metric)`` so the manifest
    # is incident-friendly and the correlated reporting artifacts emit in the
    # same order (test_reporting_artifacts_align_with_manifest pins the index
    # alignment between anomalies.csv, metric_report.log, and metric_traces.jsonl).
    filtered_anomalies.sort(key=lambda a: (a["span_start"], a["component"], a["metric"]))

    manifest_fieldnames = [
        "timestamp", "component", "metric", "description",
        "scenario_id", "severity", "is_cascade",
        "event_id", "parent_event_id",
        "span_start", "span_end", "shape",
    ]

    if "metrics" in args.emit_selection:
        with _atomic_artifact_open(args.output_dir / "anomalies.csv") as f:
            # ``extrasaction="ignore"`` is a defensive guard so any future
            # ``_``-prefixed private keys on entry dicts cannot leak into the CSV.
            writer = csv.DictWriter(
                f, fieldnames=manifest_fieldnames, extrasaction="ignore",
            )
            writer.writeheader()
            for a in filtered_anomalies:
                writer.writerow(a)

    if {"logs", "traces"} & args.emit_selection:
        write_reporting_artifacts(
            args.output_dir,
            filtered_anomalies,
            emit_logs="logs" in args.emit_selection,
            emit_traces="traces" in args.emit_selection,
        )

    gauge_rows_written = 0
    if "gauges" in args.emit_selection:
        # Long-form file peer of the OTEL gauge stream. Derived from the
        # per-component CSVs just written above (guaranteed present because
        # the parse_args gate requires "metrics" alongside "gauges"). The
        # sorted-components iterator order makes equal-timestamp ties
        # deterministic regardless of dict iteration order.
        gauge_csv_paths = {
            c: args.output_dir / f"{c}.csv" for c in sorted(args.components)
        }
        gauge_rows_written = write_gauges_csv(
            gauge_csv_paths, args.output_dir / "gauges.csv"
        )

    if "schema" in args.emit_selection:
        # Schema doc reflects exactly what this run wrote so the validator can
        # cross-check the directory after the fact. Built from the same emit
        # selection + components + combine flag the pre-clean step and the
        # end-of-run summary already consume, so the three views stay in sync.
        schema_components_in_order = [
            c for c in COMPONENTS if c in args.components
        ]
        emitted_files = _collect_emitted_filenames(
            emit_selection=args.emit_selection,
            components=schema_components_in_order,
            combine=args.combine,
        )
        schema_metadata = {
            "seed": args.seed,
            "start": args.start_time.isoformat(),
            "duration_days": args.duration_days,
            "interval_seconds": args.interval_seconds,
            "total_seconds": total_seconds,
            "rows_per_component": n_rows,
            "drop_rate": args.drop_rate,
            "signal_level": args.signal_level,
            "metrics_per_component": args.metrics_per_component,
            "anomaly_count": args.anomaly_count,
            "scenarios": sorted(active_scenarios),
            "exclude_scenarios": sorted(args.exclude_scenarios),
            "components": schema_components_in_order,
            "inject_dst_artifact_day": args.inject_dst_artifact_day,
            "emit_selection": sorted(args.emit_selection),
            "combine": args.combine,
            # phase 7 (constant since the phase-9 flag day removed the
            # independent alias): the field is retained so the validator
            # can keep honoring documents produced under either historic
            # mode; this writer only ever emits "realistic" now. The
            # validator's Pearson coupling check only runs under
            # ``realistic`` because the historic ``independent`` mode
            # produced decoupled baselines by construction.
            "topology_mode": "realistic",
        }
        write_schema_json(
            args.output_dir / "schema.json",
            components=schema_components_in_order,
            effective_specs=effective_specs,
            metadata=schema_metadata,
            emitted_files=emitted_files,
            # phase 8: per-component ``dimensions`` block (axes +
            # cardinality) when the run is dim-aware (``--instances-per-
            # component N>1`` or a non-default ``--instance-config``).
            # Filtered to the active component set so a ``--components``
            # subset doesn't leak instance topology for components the
            # run didn't write.
            instances_by_component={
                c: ctx.instances[c] for c in schema_components_in_order
            },
        )

    streamed_events = 0
    endpoints = {
        "logs": args.otel_logs_endpoint,
        "metrics": args.otel_metrics_endpoint,
        "traces": args.otel_traces_endpoint,
    }
    # --otel-send is authoritative for the anomaly-signal stream too:
    # the gauge stream needs the metrics endpoint to exist, but a
    # selection like 'logs,gauges' must not leak the anomaly-count
    # metrics signal through it. None = legacy toggles, no filtering.
    signal_selection = getattr(args, "otel_signal_selection", None)
    if signal_selection is not None:
        signal_endpoints = {
            sig: (url if sig in signal_selection else None)
            for sig, url in endpoints.items()
        }
    else:
        signal_endpoints = endpoints
    otel_active = args.otel_enabled and any(endpoints.values())
    auth_headers = {}
    if otel_active:
        for signal in ["logs", "metrics", "traces"]:
            token = getattr(args, f"otel_{signal}_auth_token")
            if token:
                auth_headers[signal] = {"Authorization": f"{args.otel_stream_auth_scheme} {token}"}

    if otel_active and not args.otel_gauges_only and any(signal_endpoints.values()):
        streamed_events = stream_otel_signals(
            signal_endpoints,
            filtered_anomalies,
            speedup=args.otel_stream_speedup,
            timeout_seconds=args.otel_stream_timeout_seconds,
            max_events=args.otel_stream_max_events,
            auth_headers=auth_headers,
            protocol=args.otel_stream_protocol,
            activity_log_path=args.otel_activity_log,
            verbose=args.otel_verbose,
        )

    gauge_requests_sent = 0
    if otel_active and args.otel_emit_gauges:
        # Gauge stream normally appends after the anomaly-counter stream so both
        # passes share one log. In gauges-only mode (--otel-send gauges) there is no prior
        # signal pass, so the gauge stream starts a fresh log instead.
        gauge_auth = auth_headers.get("metrics")
        component_csv_paths = {
            c: args.output_dir / f"{c}.csv" for c in sorted(args.components)
        }
        gauge_requests_sent = stream_otel_gauges(
            component_csv_paths,
            endpoint=args.otel_metrics_endpoint,
            batch_seconds=args.otel_gauge_batch_seconds,
            metric_prefix=args.otel_gauge_metric_prefix,
            speedup=args.otel_stream_speedup,
            timeout_seconds=args.otel_stream_timeout_seconds,
            max_events=args.otel_stream_max_events,
            # max_retries uses the shared _OTEL_DEFAULT_MAX_RETRIES default.
            auth_headers=gauge_auth,
            protocol=args.otel_stream_protocol,
            activity_log_path=args.otel_activity_log,
            verbose=args.otel_verbose,
            append_activity_log=not args.otel_gauges_only,
        )

    if args.combine:
        # Freshly-generated, non-DST component CSVs are emitted in chronological
        # order, so the wide combine writer can skip its defensive monotonic
        # pre-scan for exactly the generated component allowlist. External
        # ``combine DIR`` invocations still take the conservative scan.
        assume_monotonic_wide_components = (
            set(combine_components)
            if args.inject_dst_artifact_day == 0
            else None
        )
        combine_logs(
            args.output_dir,
            components=combine_components,
            assume_monotonic_wide_components=assume_monotonic_wide_components,
        )

    written = []
    if "metrics" in args.emit_selection:
        written.append(f"{len(args.components)} component CSV(s)")
    for emit_type, files in _EMIT_ARTIFACT_FILES.items():
        if emit_type in args.emit_selection:
            written.extend(files)
    if args.combine:
        written.append(_COMBINE_OUTPUT_FILENAME)
    print(f"Done - {', '.join(written)} written to {args.output_dir}")
    print(f"   Duration: {args.duration_days} day(s) ({total_seconds:,} seconds)")
    print(f"   Interval: {args.interval_seconds}s ({n_rows:,} rows per component)")
    print(f"   Anomalies recorded: {len(filtered_anomalies)}")
    if "gauges" in args.emit_selection:
        print(f"   Gauge rows written: {gauge_rows_written:,} to gauges.csv")
    if otel_active:
        active = [f"{s} -> {u}" for s, u in endpoints.items() if u]
        if args.otel_gauges_only:
            print("   OTEL signal stream skipped (--otel-send gauges)")
        else:
            print(f"   OTEL signals streamed: {streamed_events} to {', '.join(active)}")
        if args.otel_emit_gauges:
            print(f"   OTEL gauge requests streamed: {gauge_requests_sent} to "
                  f"metrics -> {args.otel_metrics_endpoint}")
    elif any(endpoints.values()):
        print("   OTEL streaming disabled (pass --otel-send to stream to configured endpoints)")


if __name__ == "__main__":
    main()
