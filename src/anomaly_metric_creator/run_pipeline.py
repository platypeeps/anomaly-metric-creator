"""Run-level generation orchestration and artifact lifecycle helpers."""

from __future__ import annotations

import contextlib
import csv
import json
import sys
import weakref
from pathlib import Path
from typing import Callable

_DEFAULT_RUNTIME_KEY = "__default__"
_run_runtimes: dict[str, weakref.ReferenceType] = {}


def _configure_run_runtime(
    *,
    get_namespace: Callable[[], dict],
    runtime_key: str = _DEFAULT_RUNTIME_KEY,
) -> None:
    """Wire a live legacy namespace without importing the compatibility facade."""
    def discard_runtime(_ref, key=runtime_key):
        _run_runtimes.pop(key, None)

    try:
        _run_runtimes[runtime_key] = weakref.ref(get_namespace, discard_runtime)
    except TypeError as exc:
        raise TypeError("run pipeline namespace getter must be weak-referenceable") from exc


def _runtime_namespace(runtime_key: str) -> dict:
    getter_ref = _run_runtimes.get(runtime_key)
    if getter_ref is None:
        raise RuntimeError("run pipeline runtime is not configured")
    getter = getter_ref()
    if getter is None:
        _run_runtimes.pop(runtime_key, None)
        raise RuntimeError("run pipeline runtime is no longer available")
    return getter()


_EMIT_ARTIFACT_FILES = {
    "metrics": ("anomalies.csv",),
    "logs": ("metric_report.log",),
    "traces": ("metric_traces.jsonl",),
    "gauges": ("gauges.csv",),
    "schema": ("schema.json",),
}


def write_reporting_artifacts(
    output_dir: Path,
    anomaly_rows: list[dict],
    *,
    emit_logs: bool = True,
    emit_traces: bool = True,
    runtime_key: str = _DEFAULT_RUNTIME_KEY,
) -> None:
    """Emit correlated log and trace artifacts aligned to anomaly metric records.

    ``emit_logs`` / ``emit_traces`` gate which file is written; both default to
    True to preserve the historic two-file behavior for direct callers.
    """
    namespace = _runtime_namespace(runtime_key)
    _atomic_artifact_open = namespace["_atomic_artifact_open"]
    _anomaly_event_id = namespace["_anomaly_event_id"]
    _EMIT_ARTIFACT_FILES = namespace["_EMIT_ARTIFACT_FILES"]
    output_dir = Path(output_dir)
    log_path = output_dir / _EMIT_ARTIFACT_FILES["logs"][0]
    trace_path = output_dir / _EMIT_ARTIFACT_FILES["traces"][0]

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


def _collect_emitted_filenames(
    *, emit_selection, components, combine, runtime_key: str = _DEFAULT_RUNTIME_KEY
):
    """Return the sorted list of filenames a run with the given options writes.

    Same single source of truth ``_pre_clean_output_dir`` and the end-of-run
    summary already consume: ``_EMIT_ARTIFACT_FILES`` for emit-typed artifacts,
    ``_COMBINE_OUTPUT_FILENAME`` for the combine output, and one
    ``{component}.csv`` per allowlisted component when ``metrics`` is selected.

    Used by ``write_schema_json`` and the ``validate`` subcommand to keep the
    expected-file-set check anchored to one definition.
    """
    namespace = _runtime_namespace(runtime_key)
    _EMIT_ARTIFACT_FILES = namespace["_EMIT_ARTIFACT_FILES"]
    _COMBINE_OUTPUT_FILENAME = namespace["_COMBINE_OUTPUT_FILENAME"]
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


def _known_artifact_filenames(*, runtime_key: str = _DEFAULT_RUNTIME_KEY):
    """Every artifact filename this script can write into --output-dir.

    Derived from the same registries the pre-clean and end-of-run summary
    consume (`COMPONENTS`, `_EMIT_ARTIFACT_FILES`, `_COMBINE_OUTPUT_FILENAME`)
    so the temp-sibling sweep cannot drift from the real write slots.
    """
    namespace = _runtime_namespace(runtime_key)
    COMPONENTS = namespace["COMPONENTS"]
    _EMIT_ARTIFACT_FILES = namespace["_EMIT_ARTIFACT_FILES"]
    _COMBINE_OUTPUT_FILENAME = namespace["_COMBINE_OUTPUT_FILENAME"]
    filenames = [f"{component}.csv" for component in COMPONENTS]
    for files in _EMIT_ARTIFACT_FILES.values():
        filenames.extend(files)
    filenames.append(_COMBINE_OUTPUT_FILENAME)
    return filenames


def _pre_clean_output_dir(
    output_dir,
    emit_selection,
    selected_components,
    combine,
    *,
    runtime_key: str = _DEFAULT_RUNTIME_KEY,
):
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
    namespace = _runtime_namespace(runtime_key)
    COMPONENTS = namespace["COMPONENTS"]
    _EMIT_ARTIFACT_FILES = namespace["_EMIT_ARTIFACT_FILES"]
    _COMBINE_OUTPUT_FILENAME = namespace["_COMBINE_OUTPUT_FILENAME"]
    _ATOMIC_TMP_SUFFIX = namespace["_ATOMIC_TMP_SUFFIX"]
    known_filenames = namespace["_known_artifact_filenames"]()
    for filename in known_filenames:
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


def main(argv=None, *, runtime_key: str = _DEFAULT_RUNTIME_KEY):
    namespace = _runtime_namespace(runtime_key)
    COMPONENTS = namespace["COMPONENTS"]
    INSTANCES = namespace["INSTANCES"]
    Instance = namespace["Instance"]
    RunContext = namespace["RunContext"]
    SECONDS_PER_DAY = namespace["SECONDS_PER_DAY"]
    _COMBINE_OUTPUT_FILENAME = namespace["_COMBINE_OUTPUT_FILENAME"]
    _EMIT_ARTIFACT_FILES = namespace["_EMIT_ARTIFACT_FILES"]
    _SUBCOMMANDS = namespace["_SUBCOMMANDS"]
    _anomaly_event_id = namespace["_anomaly_event_id"]
    _apply_scenarios = namespace["_apply_scenarios"]
    _apply_signal_level_and_count = namespace["_apply_signal_level_and_count"]
    _atomic_artifact_open = namespace["_atomic_artifact_open"]
    _build_timestamp_arrays = namespace["_build_timestamp_arrays"]
    _collect_emitted_filenames = namespace["_collect_emitted_filenames"]
    _compose_topology_coupled_specs = namespace["_compose_topology_coupled_specs"]
    _compose_topology_saturation_specs = namespace["_compose_topology_saturation_specs"]
    _compute_topology_arrays_per_instance = namespace["_compute_topology_arrays_per_instance"]
    _filter_anomalies_for_emitted_metrics = namespace["_filter_anomalies_for_emitted_metrics"]
    _is_anonymous_instance_list = namespace["_is_anonymous_instance_list"]
    _load_instance_config = namespace["_load_instance_config"]
    _main_combine_subcommand = namespace["_main_combine_subcommand"]
    _main_serve_subcommand = namespace["_main_serve_subcommand"]
    _main_trace_bundle_subcommand = namespace["_main_trace_bundle_subcommand"]
    _main_validate_subcommand = namespace["_main_validate_subcommand"]
    _pre_clean_output_dir = namespace["_pre_clean_output_dir"]
    _resolve_effective_specs = namespace["_resolve_effective_specs"]
    _resolve_scenarios = namespace["_resolve_scenarios"]
    _topology_generation_order = namespace["_topology_generation_order"]
    combine_logs = namespace["combine_logs"]
    generate_component = namespace["generate_component"]
    np = namespace["np"]
    parse_args = namespace["parse_args"]
    stream_otel_gauges = namespace["stream_otel_gauges"]
    stream_otel_signals = namespace["stream_otel_signals"]
    write_gauges_csv = namespace["write_gauges_csv"]
    write_reporting_artifacts = namespace["write_reporting_artifacts"]
    write_schema_json = namespace["write_schema_json"]

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
