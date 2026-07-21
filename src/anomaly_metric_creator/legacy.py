#!/usr/bin/env python3
"""Historic compatibility facade for anomaly-metric-creator.

Focused modules own generation, topology, scenarios, artifacts, validation,
and run orchestration. This module preserves the original public bindings and
configures their live, monkeypatch-visible runtime seams.
"""
import datetime
import sys
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
from .run_defaults import (
    DEFAULT_DROP_RATE as DEFAULT_DROP_RATE,
    DEFAULT_DURATION_DAYS as DEFAULT_DURATION_DAYS,
    DEFAULT_OTEL_STREAM_AUTH_SCHEME as DEFAULT_OTEL_STREAM_AUTH_SCHEME,
    DEFAULT_OUTPUT_DIR as DEFAULT_OUTPUT_DIR,
    DEFAULT_SEED as DEFAULT_SEED,
    DEFAULT_SIGNAL_LEVEL as DEFAULT_SIGNAL_LEVEL,
    PREFLIGHT_CELL_CAP as PREFLIGHT_CELL_CAP,
    SIGNAL_LEVELS as SIGNAL_LEVELS,
    _ANOMALY_COUNT_CAP_SALT as _ANOMALY_COUNT_CAP_SALT,
)
from .anomaly_dispatch import (
    _VALID_ANOMALY_SHAPES as _VALID_ANOMALY_SHAPES,
    _cached_generator_meta as _cached_generator_meta,
    _call_generator_within_span as _call_generator_within_span,
    _generator_meta as _generator_meta,
    _resolve_anomaly_value as _resolve_anomaly_value,
    _span_fraction as _span_fraction,
)
from . import generation as _generation_module
from .generation_derivations import (
    DERIVED_METRICS as DERIVED_METRICS,
    DERIVATIONS as DERIVATIONS,
    _derive_cacheservice as _derive_cacheservice,
)
_GENERATION_DERIVATIONS_BINDING = DERIVATIONS
from .models_impl import (
    Instance as Instance,
    MetricSpec as MetricSpec,
    RunContext as RunContext,
    _configure_models_runtime as _configure_models_runtime,
    _load_instance_config as _models_load_instance_config,
    _validate_instance_list as _validate_instance_list,
)
from .csv_layout import (
    _INSTANCE_DIMENSION_COLUMNS as _INSTANCE_DIMENSION_COLUMNS,
    _INSTANCE_DIMENSION_FIELDS as _INSTANCE_DIMENSION_FIELDS,
    _is_anonymous_instance_list as _is_anonymous_instance_list,
)
from .topology_impl import (
    Edge as Edge,
    SaturationParams as SaturationParams,
)
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
from .artifacts import (
    _ATOMIC_TMP_SUFFIX as _ATOMIC_TMP_SUFFIX,
    _atomic_artifact_open as _atomic_artifact_open,
    _atomic_write_text as _atomic_write_text,
)
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
from .scenario_catalog import SCENARIOS as SCENARIOS
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
from .run_pipeline import (
    _EMIT_ARTIFACT_FILES as _EMIT_ARTIFACT_FILES,
    _collect_emitted_filenames as _run_collect_emitted_filenames,
    _configure_run_runtime as _configure_run_runtime,
    _known_artifact_filenames as _run_known_artifact_filenames,
    _pre_clean_output_dir as _run_pre_clean_output_dir,
    main as _run_main,
    write_reporting_artifacts as _run_write_reporting_artifacts,
)

def _run_runtime_namespace():
    return globals()
_configure_run_runtime(get_namespace=_run_runtime_namespace, runtime_key=__name__)

def write_reporting_artifacts(
    output_dir: Path,
    anomaly_rows: list[dict],
    *,
    emit_logs: bool = True,
    emit_traces: bool = True,
) -> None:
    return _run_write_reporting_artifacts(
        output_dir,
        anomaly_rows,
        emit_logs=emit_logs,
        emit_traces=emit_traces,
        runtime_key=__name__,
    )
from .timeutil import (
    _UNIX_EPOCH_UTC as _UNIX_EPOCH_UTC,  # noqa: F401
    _dt_to_unix_nanos as _dt_to_unix_nanos,
    _parse_csv_timestamp as _parse_csv_timestamp,
    _to_unix_nanos as _to_unix_nanos,
)
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
from .redaction import (
    _SAFE_RESPONSE_HEADER_NAMES as _SAFE_RESPONSE_HEADER_NAMES,  # noqa: F401
    _SCHEMED_SENSITIVE_HEADERS as _SCHEMED_SENSITIVE_HEADERS,  # noqa: F401
    _SENSITIVE_HEADER_NAMES as _SENSITIVE_HEADER_NAMES,  # noqa: F401
    _mask_sensitive_value as _mask_sensitive_value,  # noqa: F401
    _masked_headers as _masked_headers,
    _redact_sensitive_headers as _redact_sensitive_headers,
)
from .otel_stream import (
    _http_error_activity_fields as _http_error_activity_fields,
    _verbose_body_repr as _verbose_body_repr,
    _write_activity as _write_activity,
    stream_otel_gauges as stream_otel_gauges,
    stream_otel_signals as stream_otel_signals,
)
from .csv_layout import (
    _LONG_FORM_FD_MARGIN as _LONG_FORM_FD_MARGIN,  # noqa: F401
    _classify_component_csv_header as _classify_component_csv_header,
    _ensure_long_form_fd_capacity as _ensure_long_form_fd_capacity,
    _iter_component_instance_rows as _iter_component_instance_rows,
    _iter_component_rows as _iter_component_rows,
    _scan_component_csv_headers as _scan_component_csv_headers,
    _scan_instance_block_layout as _scan_instance_block_layout,
)
from .gauges_impl import write_gauges_csv as write_gauges_csv
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
    return _run_collect_emitted_filenames(
        emit_selection=emit_selection,
        components=components,
        combine=combine,
        runtime_key=__name__,
    )

def _known_artifact_filenames():
    return _run_known_artifact_filenames(runtime_key=__name__)

def _pre_clean_output_dir(output_dir, emit_selection, selected_components, combine):
    return _run_pre_clean_output_dir(
        output_dir,
        emit_selection,
        selected_components,
        combine,
        runtime_key=__name__,
    )

def main(argv=None):
    return _run_main(argv, runtime_key=__name__)

if __name__ == "__main__":
    main()
