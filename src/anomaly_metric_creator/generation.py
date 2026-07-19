"""Vectorized metric generation hot path."""

from __future__ import annotations

import datetime
import heapq
import math
import sys
import weakref
from typing import Callable

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

from .anomaly_dispatch import _resolve_anomaly_value
from .generation_emit import (
    _build_timestamp_arrays as _build_timestamp_arrays,
    _emit_component_metrics,
    _format_fixed3 as _format_fixed3,
)
from .generation_helpers import (
    _INSTANCE_FILTER_NO_MATCH as _INSTANCE_FILTER_NO_MATCH,
    _natural_column as _natural_column,
    _resolve_instance_filter as _resolve_instance_filter,
)
from .generation_derivations import (
    DERIVATIONS,
)
from .csv_layout import _is_anonymous_instance_list
from .models_impl import Instance, MetricSpec, _validate_instance_list
from .runtime_defaults import SECONDS_PER_DAY, START
from .topology_compose import _arrays_equal_dict, _sat_tuples_equal_dict
from . import topology_impl as _topology_impl

_DEFAULT_RUNTIME_KEY = "__default__"
_generation_runtimes = {}


def _weak_runtime_getter(getter: Callable, *, runtime_key: str) -> weakref.ReferenceType:
    """Keep extracted-module runtime hooks from retaining legacy module copies."""
    def discard_runtime(_ref, key=runtime_key):
        _generation_runtimes.pop(key, None)

    try:
        return weakref.ref(getter, discard_runtime)
    except TypeError as exc:
        raise TypeError("generation runtime getters must be weak-referenceable") from exc


def _configure_generation_runtime(
    *,
    get_derivations: Callable[[], dict],
    get_topology_load_metrics: Callable[[], dict[str, tuple[str, tuple[str, ...]]]],
    get_format_fixed3: Callable[[], Callable[[np.ndarray], np.ndarray]],
    runtime_key: str = _DEFAULT_RUNTIME_KEY,
) -> None:
    """Wire live registry/helper access from ``legacy.py`` without importing it."""
    _generation_runtimes[runtime_key] = {
        "get_derivations": _weak_runtime_getter(get_derivations, runtime_key=runtime_key),
        "get_topology_load_metrics": _weak_runtime_getter(
            get_topology_load_metrics,
            runtime_key=runtime_key,
        ),
        "get_format_fixed3": _weak_runtime_getter(
            get_format_fixed3,
            runtime_key=runtime_key,
        ),
    }


def _generation_runtime_getter(runtime_key: str, key: str) -> Callable | None:
    runtime = _generation_runtimes.get(runtime_key)
    if runtime is None:
        return None
    getter = runtime[key]()
    if getter is None:
        _generation_runtimes.pop(runtime_key, None)
        raise RuntimeError("generation runtime is no longer available")
    return getter


def _runtime_derivations(runtime_key: str) -> dict:
    getter = _generation_runtime_getter(runtime_key, "get_derivations")
    if getter is None:
        return DERIVATIONS
    return getter()


def _runtime_topology_load_metrics(
    runtime_key: str,
) -> dict[str, tuple[str, tuple[str, ...]]]:
    getter = _generation_runtime_getter(runtime_key, "get_topology_load_metrics")
    if getter is None:
        return _topology_impl._TOPOLOGY_LOAD_METRICS
    return getter()


def _runtime_format_fixed3(runtime_key: str) -> Callable[[np.ndarray], np.ndarray]:
    getter = _generation_runtime_getter(runtime_key, "get_format_fixed3")
    if getter is None:
        return _format_fixed3
    return getter()


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
                       apply_dtype_int_cast: bool = True,
                       runtime_key: str = _DEFAULT_RUNTIME_KEY):
    """
    specs: list of MetricSpec (one per CSV column, in column order)
    anomaly_specs: list of {'time_offset': int, 'metric': str, 'description': str, 'generator': fn}
    instances: optional list of ``Instance`` carrying the per-component
        dimension topology (Phase 1). ``None`` resolves to a single
        anonymous ``Instance()`` so today's output stays byte-identical;
        dimension columns are emitted when ``len > 1`` or any instance
        has non-None dimension fields (the Phase 2 long-form CSV layout).
    apply_dtype_int_cast: if True (default), round columns with ``dtype="int"``
        to whole numbers via ``np.rint`` before derivations. ``main()``
        always passes True; programmatic callers may pass False to keep
        the pre-cast fractional contrast.

    Vectorized: natural-value math is one numpy op per metric; anomaly overrides
    are masked writes on the column arrays; packet loss is a single boolean mask
    decided up front so a dropped row emits neither a CSV row nor a manifest
    entry. A shaped span whose *leading* row(s) are dropped still records its
    manifest entry, anchored at the span's first kept row (a span dropped in
    its entirety records none). ``ts_array``/``ts_strings`` are optional so
    callers can share them across components (main() does this). The drop mask
    is drawn per call so each component keeps its independent drop pattern.

    ``interval`` controls sampling density (seconds between rows). Timeline
    coverage stays ``total_seconds`` seconds; row count is
    ``floor(total_seconds / interval)``. Each anomaly's ``time_offset`` is
    mapped to the nearest row via ``round(time_offset / interval)``; specs
    that fall outside ``[0, n_rows)`` are skipped with the existing
    stderr warning.
    """
    file_path = base_dir / f"{component_name}.csv"
    fieldnames = [s.name for s in specs]
    n_rows = int(total_seconds // interval)

    # ctx is the sole entry point for per-run state. It carries the RNG
    # (ctx.rng), the anomaly manifest accumulator (ctx.anomalies), and the
    # cascade registry (ctx.cascading_anomalies). Callers that need to read
    # the manifest after generation must own the RunContext; constructing
    # one inside this function would discard the appended entries.
    if ctx is None:
        raise TypeError(
            "generate_component() requires an explicit ctx= argument; "
            "pass RunContext(rng=np.random.RandomState(seed))."
        )
    rng = ctx.rng

    # Resolve the active per-component instance topology. Phase 1 only
    # validates the shape and falls back to a single anonymous ``Instance()``
    # so today's dimensionless CSV output is preserved. Phases 2–8 wire the
    # dimension columns, anomaly filtering, and OTEL attributes.
    if instances is None:
        instances = [Instance()]
    if not instances:
        raise ValueError(
            f"generate_component({component_name!r}) requires at least one "
            f"Instance; got an empty list."
        )
    # Per-entry shape checks mirror _validate_instances_registry so a caller
    # bypassing the registry (test fixtures, ad-hoc reuse) gets a clear
    # ValueError naming the call site, not a downstream AttributeError /
    # TypeError once Phases 2–4 start consuming Instance metadata.
    _validate_instance_list(
        instances, where=f"generate_component({component_name!r}) instances"
    )

    # Defense-in-depth: ``parse_args`` rejects ``--inject-dst-artifact-day``
    # paired with multi-instance at the CLI; this guard mirrors the
    # rejection for direct callers (tests, future consumers) that bypass
    # the CLI. The original rationale was correctness — the long-form
    # writer rebuilt rows from pre-splice timestamps and silently dropped
    # the duplicated hour. The long-form path now routes through
    # ``_format_csv_row_block``, which applies the splice per-instance,
    # so the guard now stands on design grounds: the multi-instance
    # long-form CSV emits per-instance row blocks, and per-block splicing
    # surfaces non-monotonic timestamps inside each block that
    # ``heapq.merge`` (``gauges.csv`` / ``combined_metrics_unified.csv``)
    # cannot resolve.
    _is_anonymous = _is_anonymous_instance_list(instances)
    if not _is_anonymous and dst_inject_day > 0:
        raise ValueError(
            f"generate_component({component_name!r}): dst_inject_day > 0 "
            f"is incompatible with a non-anonymous instance list by design — "
            f"per-instance DST splicing would surface non-monotonic "
            f"timestamps inside each row block that downstream long-form "
            f"merges (gauges.csv / combined_metrics_unified.csv) cannot "
            f"resolve. Pass instances=[Instance()] or dst_inject_day=0."
        )

    # Merge primary anomalies with cascading anomalies
    all_anomalies = list(anomaly_specs)
    if component_name in ctx.cascading_anomalies:
        all_anomalies.extend(ctx.cascading_anomalies[component_name])

    # Phase 4: resolve each spec's ``instance_filter`` against the
    # active ``instances`` list before expansion. Specs whose filter matches
    # zero instances are dropped here (one WARNING per skipped spec) so they
    # never produce manifest entries or value writes. Specs with no filter
    # or whose filter matches every instance are mapped to ``None`` so the
    # shared-values fast path stays byte-identical to Phase 2 (locked
    # built-in hashes do not move). ``resolved_filters`` is keyed by
    # ``id(spec_dict)`` so the override loop below can look up the per-spec
    # mask in O(1).
    resolved_filters: dict[int, "np.ndarray | None"] = {}
    filter_skips: list[tuple[str, str]] = []
    kept_anomalies: list[dict] = []
    for s in all_anomalies:
        resolved = _resolve_instance_filter(s.get("instance_filter"), instances)
        if resolved is _INSTANCE_FILTER_NO_MATCH:
            filter_skips.append((s["metric"], s["description"]))
            continue
        resolved_filters[id(s)] = resolved
        kept_anomalies.append(s)
    all_anomalies = kept_anomalies
    if filter_skips:
        # Sorted by (metric, description) so WARNING order is deterministic
        # regardless of dict iteration order; mirrors the convention in
        # ``_resolve_scenarios``.
        for metric, desc in sorted(filter_skips):
            print(
                f"WARNING: {component_name}: skipping anomaly spec "
                f"metric={metric!r} description={desc!r} — instance_filter "
                f"matched zero active instances.",
                file=sys.stderr,
            )

    # Keep in-range anomaly spans compact here. The override loop below merges
    # these ranges lazily so long sustained scenarios do not materialize one
    # Python tuple per affected row before vectorized generation starts.
    override_spans: list[tuple[int, int, dict, int]] = []
    out_of_range: list[dict] = []
    for spec_order, s in enumerate(all_anomalies):
        if s["time_offset"] < 0:
            out_of_range.append(s)
            continue
        start_idx = int(round(s["time_offset"] / interval))
        duration_seconds = float(s.get("duration_seconds", 0) or 0)
        duration_rows = max(1, int(np.ceil(duration_seconds / interval)))
        end_idx_exclusive = min(n_rows, start_idx + duration_rows)
        if start_idx >= n_rows or end_idx_exclusive <= start_idx:
            out_of_range.append(s)
            continue
        override_spans.append((start_idx, end_idx_exclusive, s, spec_order))
    if out_of_range:
        non_negative_offsets = [
            s["time_offset"] for s in out_of_range
            if s["time_offset"] >= 0
        ]
        if non_negative_offsets:
            max_start_idx = max(
                int(round(offset / interval))
                for offset in non_negative_offsets
            )
            needed_seconds = (max_start_idx + 1) * interval
            needed_days = max(
                1.0,
                math.nextafter(needed_seconds / SECONDS_PER_DAY, math.inf),
            )
            include_hint = (
                f"Run with --duration-days {needed_days!r} to include them."
            )
        else:
            include_hint = "Check anomaly specs for negative time_offset values."
        print(
            f"WARNING: {component_name}: skipping {len(out_of_range)} anomaly spec(s) "
            f"with time_offset outside [0, {total_seconds}). "
            f"{include_hint}",
            file=sys.stderr,
        )

    # Fail loudly on identical (metric, time_offset) specs — the previous
    # ``metric_overrides = {spec["metric"]: spec["generator"] for spec in specs}``
    # silently kept only the last one.
    seen_specs: dict[tuple[str, int], dict] = {}
    duplicates: list[tuple[str, str, int]] = []
    for s in all_anomalies:
        key = (s["metric"], s["time_offset"])
        if key in seen_specs:
            duplicates.append((component_name, s["metric"], s["time_offset"]))
        else:
            seen_specs[key] = s
    if duplicates:
        raise ValueError(
            f"Overlapping anomaly specs (component, metric, time_offset): {duplicates}"
        )

    def iter_sorted_overrides():
        """Yield concrete overrides in row/metric/spec order without a row list."""
        heap = [
            (start_idx, aspec["metric"], spec_order, start_idx, end_idx, aspec)
            for start_idx, end_idx, aspec, spec_order in override_spans
        ]
        heapq.heapify(heap)
        while heap:
            row_idx, metric, spec_order, start_idx, end_idx, aspec = heapq.heappop(heap)
            span_idx = row_idx - start_idx
            yield row_idx, aspec, span_idx * interval, span_idx
            next_row_idx = row_idx + 1
            if next_row_idx < end_idx:
                heapq.heappush(
                    heap,
                    (next_row_idx, metric, spec_order, start_idx, end_idx, aspec),
                )

    if ts_array is None or ts_strings is None:
        ts_array, ts_strings = _build_timestamp_arrays(
            total_seconds, interval, start_time=start_time
        )
    drop_mask = rng.random(n_rows) < drop_rate

    # Elapsed seconds (not row index) so daily/hourly seasonality generators
    # produce the same wall-clock shape at any sampling interval.
    elapsed = np.arange(n_rows, dtype=np.float64) * interval

    # Natural values: one column array per metric, computed in a single numpy op.
    n_cols = len(specs)
    values = np.empty((n_rows, n_cols), dtype=np.float64)

    # phase 8: per-instance topology dispatch. When the caller
    # passes per-instance coupling / saturation arrays (under
    # realistic topology coupling with N>1 or a non-default
    # instance config), each instance K consumes its own arrays via
    # ``_natural_column``'s ``baseline_override`` / ``latency_factor``
    # / ``error_offset`` kwargs. Under symmetric upstream (no
    # ``instance_filter`` on an upstream load metric) the arrays are
    # byte-identical across instances → fast-path single draw shared
    # across all instances preserves today's locked N=3 hashes.
    # Under asymmetric upstream the arrays diverge → per-instance
    # natural-column draws (with shared noise via the ``noise=``
    # kwarg so the noise floor matches the symmetric case).
    use_per_instance_topology = (
        coupling_arrays_per_instance is not None
        and saturation_arrays_per_instance is not None
    )
    # Reject the half-passed shape up front so programmatic callers
    # see a clear error rather than a silent fall-back to the legacy
    # shared-arrays path that would emit wrong per-instance values.
    if (
        (coupling_arrays_per_instance is None)
        != (saturation_arrays_per_instance is None)
    ):
        raise ValueError(
            f"generate_component({component_name!r}) requires both "
            "coupling_arrays_per_instance and saturation_arrays_per_instance "
            "or neither; got "
            f"coupling={'present' if coupling_arrays_per_instance is not None else 'None'} "
            f"saturation={'present' if saturation_arrays_per_instance is not None else 'None'}."
        )
    pre_populated_per_instance_eager: dict[int, np.ndarray] = {}
    if use_per_instance_topology:
        n_inst_local = len(instances)
        # Defensive shape check: the per-instance arrays are indexed by
        # instance position below ([0] for the fast path, range() for
        # the divergent loop). A mismatched length would surface as a
        # confusing IndexError mid-loop; raise a clear ValueError up
        # front so programmatic callers see exactly which list is the
        # wrong shape. ``main()`` always passes lists built from
        # ``_compute_topology_arrays_per_instance`` with this length,
        # so this branch only catches third-party or test misuse.
        if (
            len(coupling_arrays_per_instance) != n_inst_local
            or len(saturation_arrays_per_instance) != n_inst_local
        ):
            raise ValueError(
                f"generate_component({component_name!r}) per-instance "
                f"topology arrays must match len(instances)={n_inst_local}; "
                f"got coupling_arrays_per_instance="
                f"{len(coupling_arrays_per_instance)} and "
                f"saturation_arrays_per_instance="
                f"{len(saturation_arrays_per_instance)}."
            )
        # Identify which specific instances diverge from instance 0 so
        # the divergent path only allocates per-instance buffers for
        # the instances that actually need them. At N=20 with a single
        # asymmetric upstream pod, this saves 18 full (n_rows × n_cols)
        # buffers compared to the previous "allocate-N-up-front" shape
        # (~9.7 GB at 7d / 1s / N=20 / 10 metrics).
        #
        # Divergence is always re-derived from the passed arrays so
        # correctness does not depend on any caller-supplied hint.
        divergent_instances: set[int] = set()
        if n_inst_local > 1:
            ref_coupling = coupling_arrays_per_instance[0]
            ref_saturation = saturation_arrays_per_instance[0]
            for inst_idx_k in range(1, n_inst_local):
                if not _arrays_equal_dict(
                    coupling_arrays_per_instance[inst_idx_k], ref_coupling
                ) or not _sat_tuples_equal_dict(
                    saturation_arrays_per_instance[inst_idx_k], ref_saturation
                ):
                    divergent_instances.add(inst_idx_k)

        if not divergent_instances:
            # Shared fast path — one draw per metric, reusable across instances.
            coupling = coupling_arrays_per_instance[0]
            saturation = saturation_arrays_per_instance[0]
            for col, spec in enumerate(specs):
                baseline_override = coupling.get(spec.name)
                lf, eo = saturation.get(spec.name, (None, None))
                values[:, col] = _natural_column(
                    spec, ts_array, elapsed, rng,
                    latency_factor=lf, error_offset=eo,
                    baseline_override=baseline_override,
                )
        else:
            # Divergent — write instance 0 into ``values`` (already
            # allocated) and allocate per-instance buffers only for
            # the instances that diverge. Noise per metric is drawn
            # once and shared so the only divergence flows through
            # topology arrays. Non-divergent instances stay on
            # ``values`` via the missing-key lookup in
            # ``per_instance_values`` below.
            divergent_buffers: dict[int, np.ndarray] = {
                inst_idx_k: np.empty((n_rows, n_cols), dtype=np.float64)
                for inst_idx_k in divergent_instances
            }
            for col, spec in enumerate(specs):
                shared_noise = None
                if spec.std > 0 and (
                    coupling_arrays_per_instance[0].get(spec.name) is None
                ):
                    # Coupled metrics have a baseline_override that replaces the
                    # natural draw entirely — drawing noise would advance the RNG
                    # without producing any output difference. Probe instance 0
                    # only: the per-instance composer assigns a coupling
                    # baseline_override consistently across instances for a
                    # given metric (either all instances get an override or
                    # none do — the existence of an override per metric is
                    # gated on whether any incoming edge contributed, which
                    # is decided once per metric in
                    # ``_compute_topology_arrays_per_instance``), so instance
                    # 0's presence is a faithful proxy for whether *any*
                    # instance will use the natural baseline path.
                    shared_noise = rng.normal(0.0, spec.std, n_rows)
                # Instance 0 always writes into ``values`` (the shared
                # buffer).
                coupling0 = coupling_arrays_per_instance[0]
                saturation0 = saturation_arrays_per_instance[0]
                baseline_override0 = coupling0.get(spec.name)
                lf0, eo0 = saturation0.get(spec.name, (None, None))
                values[:, col] = _natural_column(
                    spec, ts_array, elapsed, rng,
                    noise=shared_noise,
                    latency_factor=lf0, error_offset=eo0,
                    baseline_override=baseline_override0,
                )
                for inst_idx_k in divergent_instances:
                    coupling = coupling_arrays_per_instance[inst_idx_k]
                    saturation = saturation_arrays_per_instance[inst_idx_k]
                    baseline_override = coupling.get(spec.name)
                    lf, eo = saturation.get(spec.name, (None, None))
                    divergent_buffers[inst_idx_k][:, col] = _natural_column(
                        spec, ts_array, elapsed, rng,
                        noise=shared_noise,
                        latency_factor=lf, error_offset=eo,
                        baseline_override=baseline_override,
                    )
            for inst_idx_k, buf in divergent_buffers.items():
                pre_populated_per_instance_eager[inst_idx_k] = buf
    else:
        # Today's path: shared lambda-baked specs, single natural draw per column.
        for col, spec in enumerate(specs):
            values[:, col] = _natural_column(spec, ts_array, elapsed, rng)

    # Apply anomaly overrides. Skip overrides at dropped rows so manifest and
    # CSV stay coherent: a dropped row has no CSV entry, so it must have no
    # manifest entry either. For shaped spans the manifest entry is recorded
    # at the spec's first kept row (see ``manifest_emitted`` below), so a
    # span whose leading rows are dropped still surfaces in the manifest.
    #
    # Phase 4: per-instance value buffers are materialized lazily
    # for any instance touched by a partial ``instance_filter``. An
    # unfiltered override writes to shared ``values`` AND propagates the
    # same write to every already-materialized per-instance buffer (so a
    # later unfiltered spec stays visible to instances whose buffer was
    # forked by an earlier filtered spec). Built-in scenarios omit
    # ``instance_filter``, so this dict stays empty for the default run
    # and the shared-values fast path is preserved — locked Phase 2 hashes
    # do not move. RNG draw order is identical to today's path because
    # ``_resolve_anomaly_value`` is still called exactly once per
    # ``(row_idx, span_idx, aspec)`` triple in row/metric/spec order,
    # regardless of filter resolution.
    name_to_col = {s.name: i for i, s in enumerate(specs)}
    per_instance_values: dict[int, np.ndarray] = dict(pre_populated_per_instance_eager)
    # Manifest bookkeeping: one entry per spec, recorded at the spec's first
    # *kept* row. Historically the entry was gated on ``span_idx == 0``, which
    # silently lost the manifest entry for a shaped span whose first row was
    # dropped — the CSV still carried the anomalous values for the span's
    # surviving rows, but ``anomalies.csv`` (and every consumer of it, e.g.
    # the topology-coupling validator's exclusion windows) never saw the
    # event. Keyed by ``id(aspec)``; spec dicts are alive for the whole loop
    # and the duplicate-spec guard above ensures one entry per spec.
    manifest_emitted: set[int] = set()
    for row_idx, aspec, t_within, span_idx in iter_sorted_overrides():
        if drop_mask[row_idx]:
            continue
        col = name_to_col[aspec["metric"]]
        ts_py = start_time + datetime.timedelta(seconds=float(row_idx * interval))
        override_value = _resolve_anomaly_value(
            aspec, ts_py, col, t_within, span_idx, rng
        )
        inst_mask = resolved_filters.get(id(aspec))
        if inst_mask is None:
            # No filter, or filter matches every instance — write to the
            # shared ``values`` (Phase 2 fast path) AND propagate the
            # write to every already-forked per-instance buffer. The
            # propagation is for *different* rows than the row that
            # forked the buffer: e.g. a filtered spec at t=60 forks
            # pod-0's buffer; a later unfiltered spec at t=120 must
            # apply to pod-0 too, not stay stuck on its forked baseline
            # from t=60. Same-cell collisions CAN occur here — the
            # duplicate-spec guard above only rejects *identical*
            # ``(metric, time_offset)`` pairs, while two specs with
            # different offsets can round to the same row at a coarse
            # ``--interval-seconds`` (or a cascade can land inside a
            # shaped span). Colliding specs resolve last-writer-wins per
            # buffer in ``(row_idx, metric, spec_order)`` order — the
            # documented contract in CLAUDE.md's RNG-ordering section.
            values[row_idx, col] = override_value
            for buf in per_instance_values.values():
                buf[row_idx, col] = override_value
        else:
            # Partial filter — only matched instances see the override.
            # Unmatched instances continue to read ``values`` (or their
            # own forked buffer if a prior spec already diverged them).
            for inst_idx in np.flatnonzero(inst_mask):
                inst_idx = int(inst_idx)
                buf = per_instance_values.get(inst_idx)
                if buf is None:
                    # Snapshot shared values (including any unfiltered
                    # writes applied so far this loop) before diverging.
                    buf = values.copy()
                    per_instance_values[inst_idx] = buf
                buf[row_idx, col] = override_value
        if id(aspec) not in manifest_emitted:
            manifest_emitted.add(id(aspec))
            # timestamp / span_start equal the spec's first *kept* row —
            # historically always the span's first row, but under
            # ``--drop-rate`` the leading row(s) of a shaped span can be
            # dropped and the entry must anchor at the first row that
            # actually appears in the component CSV. span_end equals
            # timestamp for single-row specs and the formatted
            # end-of-span timestamp for shaped specs with
            # ``duration_seconds``. The end row is the last row index
            # covered by the span — computed from the spec's *nominal*
            # start row ``row_idx - span_idx``, clipped to ``n_rows - 1``
            # so specs whose tail spills past the run window still produce
            # a valid in-range end timestamp — then walked back to the last
            # non-dropped row in the span so span_end always names a
            # timestamp that actually appears in the component CSV.
            # ``row_idx`` itself is non-dropped (checked above), so the
            # slice is guaranteed to contain at least one kept row.
            duration_seconds = float(aspec.get("duration_seconds", 0) or 0)
            duration_rows = max(1, int(np.ceil(duration_seconds / interval)))
            start_idx_nominal = row_idx - span_idx
            end_idx_nominal = min(start_idx_nominal + duration_rows - 1, n_rows - 1)
            span_kept = ~drop_mask[row_idx:end_idx_nominal + 1]
            end_idx = row_idx + int(np.flatnonzero(span_kept)[-1])
            ts_str = str(ts_strings[row_idx])
            ctx.anomalies.append({
                "timestamp": ts_str,
                "component": component_name,
                "metric": aspec["metric"],
                "description": aspec["description"],
                "scenario_id": aspec.get("_scenario_id", ""),
                "severity": aspec.get("_severity", ""),
                "is_cascade": "true" if aspec.get("_is_cascade") else "false",
                "span_start": ts_str,
                "span_end": str(ts_strings[end_idx]),
                "shape": aspec.get("shape", "step"),
            })

    # Phase 6 integer-cast bundle. Every MetricSpec declared with
    # ``dtype="int"`` must render as a whole-integer CSV cell so the
    # validator's ``_validate_component_cells`` ``dtype="int"``
    # check passes. The cast runs *before* derivations so derived columns
    # (e.g. ``cacheservice.hit_ratio``) are recomputed from the rounded
    # integer source cells and stay self-consistent with what the CSV
    # actually writes — otherwise the validator's
    # ``_validate_component_derivations`` recompute step would flag the
    # derived cell as drifting from the recomputed value. ``np.rint``
    # rounds half-to-even into floats, which is consistent with
    # ``_format_fixed3`` printing "1235.000" for an underlying float of
    # ``1235.0``. ``apply_dtype_int_cast=False`` skips the cast for
    # programmatic callers that need the pre-cast fractional contrast;
    # main() always passes ``True`` (the phase-9 flag day removed the
    # ``--topology-mode independent`` alias that used to skip it).
    if apply_dtype_int_cast:
        for col_idx, spec in enumerate(specs):
            if spec.dtype == "int":
                np.rint(values[:, col_idx], out=values[:, col_idx])
                for buf in per_instance_values.values():
                    np.rint(buf[:, col_idx], out=buf[:, col_idx])

    # Derived metrics: rebuild self-consistent relationships after natural and
    # anomaly values have settled (and after the integer-cast bundle above
    # so derivations consume the same values the CSV emits). The registered
    # function recomputes the derived column(s) from their sibling columns;
    # without this pass, anomalies that drove only a source column (or that
    # overrode a derived column in isolation) would leave the columns
    # internally inconsistent — exactly the consistency anomaly real
    # telemetry would flag.
    derivation = _runtime_derivations(runtime_key).get(component_name)
    if derivation is not None:
        derive_fn, _ = derivation
        derive_fn(values, name_to_col)
        # Phase 4: per-instance buffers diverged in source columns, so
        # rebuild their derived columns independently from the shared run.
        for buf in per_instance_values.values():
            derive_fn(buf, name_to_col)

    # Topology phase 2/3: expose post-natural /
    # post-anomaly / post-derivation load-metric columns to downstream
    # components via the ``topology_capture`` dict. Phase 3 extends the
    # capture from a single ``requests_per_sec`` column to all metrics
    # listed in ``_TOPOLOGY_LOAD_METRICS[component_name]`` (the canonical
    # load metric plus any supplementary columns) so per-edge ``signal``
    # callables (e.g. the cacheservice -> database miss-ratio derivation)
    # can read the full upstream state. Capturing pre-round (before the
    # ``np.round(values, 3, ...)`` below) keeps the signal at full
    # 3+-decimal float precision *for ``dtype="float"`` columns*. After
    # the phase 6 integer-cast bundle, ``dtype="int"`` upstream
    # load metrics (notably ``cache_hits`` / ``cache_misses`` driving the
    # cacheservice -> database miss-ratio signal) are captured at their
    # post-cast whole-integer values, which matches what the CSV emits
    # and what the validator's derivation recompute reads — the
    # downstream coupling signal therefore stays self-consistent with
    # the on-disk row. ``None`` short-circuits so direct callers that
    # skip topology capture (e.g. the natural-baseline test fixtures)
    # see zero topology work.
    if topology_capture is not None:
        entry = _runtime_topology_load_metrics(runtime_key).get(component_name)
        if entry is not None:
            canonical_up, supplementary_up = entry
            load_metrics = (canonical_up, *supplementary_up)
            captured: dict[str, np.ndarray] = {}
            # When per-instance buffers diverged from the shared
            # ``values`` (an ``instance_filter`` partial override on a
            # load metric, or per-instance topology produced divergent
            # baselines), an instance-0-only capture biases every
            # downstream consumer of the aggregate ``topology_capture``
            # view. Mirror the documented "uniform fan-out — mean
            # across upstream pods" contract by averaging across all
            # instance buffers (instance 0 is ``values``; the rest
            # live in ``per_instance_values``). When no buffer
            # diverged, the average equals ``values`` exactly and
            # the captured bytes are unchanged.
            inst_count = len(instances)
            may_diverge = bool(per_instance_values) and inst_count > 1
            for lm in load_metrics:
                if lm and lm in name_to_col:
                    col_idx = name_to_col[lm]
                    shared_col = values[:, col_idx]
                    captured_col: np.ndarray | None = None
                    if may_diverge:
                        # ``per_instance_values`` may be populated by a
                        # filter on a *non-load* metric, in which case
                        # the load column is byte-identical across all
                        # instances and the historic ``shared_col.copy()``
                        # capture stays byte-exact. Only switch to the
                        # equal-weight mean when at least one
                        # per-instance buffer actually diverged on
                        # this load column.
                        any_diverged = False
                        for buf in per_instance_values.values():
                            if not np.array_equal(
                                buf[:, col_idx], shared_col
                            ):
                                any_diverged = True
                                break
                        if any_diverged:
                            # Incremental sum-then-divide avoids the
                            # ``(N_instances × n_rows)`` temporary
                            # ``np.stack`` would allocate. Same
                            # equal-weight mean as ``np.mean`` over the
                            # stacked array but at O(n_rows) extra
                            # memory instead of O(N_instances × n_rows).
                            #
                            # Use ``per_instance_values.get(k, values)``
                            # for *every* k including 0 so an
                            # ``instance_filter`` that targets pod 0
                            # (forking only ``per_instance_values[0]``
                            # while other pods stay on the shared
                            # ``values`` baseline) still contributes
                            # pod-0's forked buffer to the aggregate.
                            # ``shared_col`` is ``values[:, col_idx]``
                            # and would silently skip pod 0's diverged
                            # buffer if used as the initial accumulator.
                            buf0 = per_instance_values.get(0, values)
                            captured_col = buf0[:, col_idx].astype(
                                np.float64, copy=True
                            )
                            for inst_idx_k in range(1, inst_count):
                                buf = per_instance_values.get(
                                    inst_idx_k, values
                                )
                                captured_col += buf[:, col_idx]
                            captured_col /= inst_count
                    if captured_col is None:
                        captured_col = shared_col.copy()
                    captured[lm] = captured_col
            if captured:
                topology_capture[component_name] = captured

    # phase 8: per-instance load-metric capture for downstream
    # consumers. Mirrors ``topology_capture`` above but produces one
    # entry per instance. Under symmetric upstream (no
    # ``instance_filter`` on load metrics) every entry references a
    # copy of the same underlying column, so downstream composers
    # collapse back to the shared-arrays fast path and N=3 byte
    # parity holds.
    if topology_capture_by_instance is not None:
        entry = _runtime_topology_load_metrics(runtime_key).get(component_name)
        if entry is not None:
            canonical_up, supplementary_up = entry
            load_metrics = (canonical_up, *supplementary_up)
            per_inst_caps: list[dict[str, np.ndarray]] = []
            for inst_idx_k in range(len(instances)):
                buf = per_instance_values.get(inst_idx_k, values)
                inst_captured: dict[str, np.ndarray] = {}
                for lm in load_metrics:
                    if lm and lm in name_to_col:
                        inst_captured[lm] = buf[:, name_to_col[lm]].copy()
                per_inst_caps.append(inst_captured)
            # Only record when at least one entry has captures; aligns
            # with the shared ``topology_capture`` guard above.
            if any(per_inst_caps):
                topology_capture_by_instance[component_name] = per_inst_caps

    # Multi-instance fan-out (Phase 2/4). When the active instance list
    # is a single anonymous Instance() (all fields None), emit today's
    # byte-identical format: ``timestamp,m0,m1,...``. When the list carries
    # named instances (len > 1, or any non-None dimension field), prepend
    # ``id,host,pod,az,region,tenant`` columns and repeat the row block for
    # each instance sequentially (all rows for instance 0, then instance 1,
    # …). ``_is_anonymous`` was already computed above for the DST
    # defense-in-depth guard via the shared ``_is_anonymous_instance_list``
    # helper.
    #
    # Phase 4 (instance_filter): instances touched by a partial filter use
    # their own ``per_instance_str_vals`` buffer (post-override,
    # post-derive); other instances reuse the shared ``str_vals`` so the
    # all-instances-unfiltered case stays byte-identical to Phase 2.
    #
    # both branches now flow through the shared ``_format_metric_suffix``
    # / ``_format_csv_row_block`` helpers. The dimensionless branch is the
    # degenerate ``dim_prefix=""`` case of the long-form path, so the DST
    # splice — applied inside ``_format_csv_row_block`` — fires regardless
    # of the writer branch. Before the refactor the long-form path
    # rebuilt rows from pre-splice timestamps and silently dropped the
    # duplicate hour (the PR #63 long-form DST drop).

    if emit_metrics:
        _emit_component_metrics(
            file_path=file_path,
            fieldnames=fieldnames,
            values=values,
            per_instance_values=per_instance_values,
            drop_mask=drop_mask,
            ts_strings=ts_strings,
            instances=instances,
            is_anonymous=_is_anonymous,
            dst_inject_day=dst_inject_day,
            start_time=start_time,
            format_fixed3=_runtime_format_fixed3(runtime_key),
        )
