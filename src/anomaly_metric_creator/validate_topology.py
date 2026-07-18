"""Topology-coupling validator helpers."""

from __future__ import annotations

import csv
import datetime
import math
from pathlib import Path

import numpy as np

from .timeutil import _parse_csv_timestamp
from .validate_topology_instances import (
    _validate_topology_coupling_per_instance as _validate_topology_coupling_per_instance,
)

# Default Pearson correlation gate for ``_validate_topology_coupling``.
# Mirrors the issue acceptance bound (0.85) and the existing LLM
# correlation test in ``tests/test_topology_llm.py``. Per-edge overrides
# live in ``Edge.correlation_threshold``.
_TOPOLOGY_DEFAULT_CORRELATION_THRESHOLD = 0.85

# Padding (seconds) applied around every ``anomalies.csv`` window when
# the validator excludes anomaly-affected rows from the topology
# correlation computation. Mirrors the ``_EXCLUSION_PAD_SECONDS``
# constant in ``tests/test_topology_llm.py`` so single-row cascades that
# round to the nearest sampled row don't leak into the correlation pool.
_TOPOLOGY_CORRELATION_EXCLUSION_PAD_SECONDS = 30

# Minimum aligned source/target rows required before computing topology
# Pearson correlations. Smaller samples are too noisy and usually indicate
# intentionally narrow component/interval selections.
_TOPOLOGY_MIN_ALIGNED_ROWS = 100


def _read_component_metric_column(
    csv_path: Path, metric_name: str,
) -> tuple[list[datetime.datetime], np.ndarray] | None:
    """Read a single metric column from a per-component CSV.

    Returns ``(timestamps, values)`` aligned row-by-row, or ``None`` if
    the CSV does not exist, has no header, or does not declare
    ``metric_name``. Used by ``_validate_topology_coupling`` to align
    source / target canonical load metrics on shared timestamps.

    Phase 8: when the CSV is the dim-aware long-form layout
    (multiple rows per timestamp, one per instance), values are
    collapsed to one ``(timestamp, mean)`` per unique timestamp. This
    keeps the timestamp axis monotonic (the existing
    ``_compute_anomaly_keep_mask`` forward-sweep relies on it) and
    matches the validator's "per-timestamp" coupling contract — the
    correlation is between the upstream's per-second load and the
    downstream's per-second load, not between per-instance copies of
    that load. Aggregation is the mean across instances at the
    timestamp; under the default fan-out (all instances share the
    same baseline) the mean equals any single instance's value, so
    the N=1 byte path is unchanged.
    """
    if not csv_path.exists():
        return None
    per_ts_sum: dict[datetime.datetime, float] = {}
    per_ts_count: dict[datetime.datetime, int] = {}
    order: list[datetime.datetime] = []
    with open(csv_path, "r", encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        try:
            header = next(reader)
        except StopIteration:
            return None
        try:
            col_idx = header.index(metric_name)
        except ValueError:
            return None
        for row in reader:
            if not row or col_idx >= len(row):
                continue
            try:
                ts = _parse_csv_timestamp(row[0])
                value = float(row[col_idx])
            except ValueError:
                continue
            if ts not in per_ts_count:
                order.append(ts)
                per_ts_sum[ts] = value
                per_ts_count[ts] = 1
            else:
                per_ts_sum[ts] += value
                per_ts_count[ts] += 1
    # Sort the deduplicated timestamp axis so any non-monotonic CSV row
    # layout normalizes before the downstream forward-sweep mask sees it.
    # Dim-aware per-component CSVs write contiguous per-instance blocks,
    # and the DST artifact path can duplicate wall-clock timestamps; the
    # aggregation above keeps the first-seen timestamp list unique while
    # this sort makes the returned axis monotonic. For a plain default
    # dimensionless CSV the input is already monotonic, so this is a no-op.
    order.sort()
    values = np.array(
        [per_ts_sum[ts] / per_ts_count[ts] for ts in order],
        dtype=np.float64,
    )
    return order, values


def _read_anomaly_exclusion_windows(
    anomalies_path: Path,
) -> list[tuple[datetime.datetime, datetime.datetime, str, str]]:
    """Return one padded ``(start, end, component, metric)`` tuple per row in
    ``anomalies.csv``.

    Each window spans ``[span_start, span_end]`` from the manifest with
    ``_TOPOLOGY_CORRELATION_EXCLUSION_PAD_SECONDS`` added on either side
    so the validator can excise the entire shaped anomaly span when the
    targeted column is one of the two columns the per-edge correlation
    reads. Cascade rows have ``span_start == span_end`` so they get a
    2*pad point exclusion. Returns an empty list when the manifest is
    missing — a run with ``metrics`` opted out of ``--emit``
    won't have one.

    The ``component`` / ``metric`` fields are carried in the tuple so
    the topology validator can filter windows down to those that actually
    touch the columns being correlated. Excluding *every*
    anomaly's time range globally would erase entire days for unrelated
    long ramps (e.g. ``db_stall``'s 24h ``disk_used_pct`` ramp) and
    leave too few rows to test coupling on the load columns themselves.

    Older anomalies.csv variants (or rows missing the columns) fall
    back to a point exclusion around the ``timestamp`` field; the
    column-fallback chain ensures the validator stays usable across
    manifest revisions even if the columns drift.
    """
    if not anomalies_path.exists():
        return []
    windows: list[tuple[datetime.datetime, datetime.datetime, str, str]] = []
    pad = datetime.timedelta(
        seconds=_TOPOLOGY_CORRELATION_EXCLUSION_PAD_SECONDS
    )
    with open(anomalies_path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            start_raw = row.get("span_start") or row.get("timestamp")
            end_raw = row.get("span_end") or start_raw
            if not start_raw:
                continue
            try:
                start_dt = _parse_csv_timestamp(start_raw)
                end_dt = _parse_csv_timestamp(end_raw)
            except ValueError:
                continue
            component = row.get("component") or ""
            metric = row.get("metric") or ""
            windows.append((start_dt - pad, end_dt + pad, component, metric))
    windows.sort()
    return windows


def _filter_windows_for_pair(
    windows: list[tuple[datetime.datetime, datetime.datetime, str, str]],
    source_component: str, source_metric: str,
    target_component: str, target_metric: str,
    *,
    topology: dict[str, list[object]],
    topology_load_metrics: dict[str, tuple[str, tuple[str, ...]]],
) -> list[tuple[datetime.datetime, datetime.datetime]]:
    """Return ``(start, end)`` ranges touching the columns that affect a pair.

    Drops every window whose ``(component, metric)`` does not match one
    of the load columns the topology pipeline composes into the target's
    canonical load metric: the source's canonical metric, the target's
    canonical metric, *or* any other incoming-edge source's captured
    load metric. The third category matters when the target is fed by
    more than one upstream (e.g. ``database`` is composed of an
    apigateway-driven constant edge plus a cacheservice-driven callable
    edge): an anomaly on the *other* upstream's load column shifts the
    target's baseline in a way that decouples it from the source under
    test, so excluding those windows keeps the Pearson check focused on
    the contract this specific edge enforces.

    Used by ``_validate_topology_coupling`` so anomalies on unrelated
    columns (e.g. ``disk_used_pct``) do not shrink the correlation pool
    while anomalies that genuinely break the load coupling do.
    """
    targets: set[tuple[str, str]] = {
        (source_component, source_metric),
        (target_component, target_metric),
    }
    # Walk reverse-adjacency of the live TOPOLOGY restricted to the
    # target: every component with an outgoing edge to the target is an
    # upstream contributor whose captured load columns can shift the
    # target's baseline.
    for upstream, edges in topology.items():
        if upstream == source_component:
            continue
        if not any(edge.target == target_component for edge in edges):
            continue
        ups_entry = topology_load_metrics.get(upstream)
        if ups_entry is None:
            continue
        canonical, supplementary = ups_entry
        if canonical:
            targets.add((upstream, canonical))
        for name in supplementary:
            if name:
                targets.add((upstream, name))
    return [
        (start, end)
        for start, end, comp, metric in windows
        if (comp, metric) in targets
    ]


def _compute_anomaly_keep_mask(
    timestamps: list[datetime.datetime],
    windows: list[tuple[datetime.datetime, datetime.datetime]],
) -> np.ndarray:
    """Build a boolean ``keep`` mask flagging rows outside every window.

    ``True`` at position ``i`` means ``timestamps[i]`` falls in *no*
    exclusion window. ``False`` means at least one window covers it.

    Runs in ``O(N + W log W)`` time: windows are sorted by start and
    overlapping windows are merged into disjoint intervals; a single
    forward sweep over ``timestamps`` (which the validator passes in
    chronological row-emission order) advances an index over the
    merged intervals so each timestamp checks at most one interval.
    This replaces the previous ``O(N * W)`` nested-loop check; on a
    7-day run (~604,800 rows) with ~30 exclusion windows per pair,
    the cost drops from ~18M comparisons to ~604,800 across all pairs.
    """
    n = len(timestamps)
    keep = np.ones(n, dtype=bool)
    if not windows or n == 0:
        return keep
    sorted_windows = sorted(windows)
    merged: list[tuple[datetime.datetime, datetime.datetime]] = []
    for w_start, w_end in sorted_windows:
        if merged and w_start <= merged[-1][1]:
            prev_start, prev_end = merged[-1]
            merged[-1] = (prev_start, max(prev_end, w_end))
        else:
            merged.append((w_start, w_end))
    w_idx = 0
    w_count = len(merged)
    for i, ts in enumerate(timestamps):
        while w_idx < w_count and merged[w_idx][1] < ts:
            w_idx += 1
        if w_idx == w_count:
            break
        if merged[w_idx][0] <= ts:
            keep[i] = False
    return keep


def _validate_topology_coupling(
    output_dir: Path, schema: dict,
    *,
    live_topology: dict[str, list[object]],
    live_topology_load_metrics: dict[str, tuple[str, tuple[str, ...]]],
) -> list[str]:
    """Verify every declared coupling edge produces a high Pearson correlation
    between the upstream's canonical load metric and the downstream's
    canonical load metric.

    Skipped silently — returning an empty list — when:

    - ``metadata.topology_mode != "realistic"`` (documents produced under
      the historic ``independent`` mode carry decoupled baselines by
      construction, so there is no coupling to check).
    - The schema document has no ``topology`` section (older schema docs
      written before phase 7; the loader rejects unknown
      ``schema_version`` values outright, but defensive code paths can
      still land here).
    - Either side's CSV is missing or fails to declare its canonical
      ``_TOPOLOGY_LOAD_METRICS`` metric (e.g. ``--metrics-per-component``
      trimmed the column away).

    Each edge whose weight is the literal string ``"callable"`` is also
    skipped — the per-row weight is the dominant signal in that case
    (e.g. cache-miss ratio driving database QPS), not the upstream load
    column the Pearson check inspects. Edges with ``weight == 0`` are
    likewise skipped because ``_validate_topology`` accepts them as a
    saturation-only placeholder that does not contribute to the
    downstream load baseline. The intent of the check is to catch
    silent coupling regressions on the constant-weight edges with
    non-zero load contribution, where upstream→downstream load
    tracking is the contract.

    Each surviving edge contributes one violation message when the
    realized Pearson correlation falls below the per-edge threshold
    (``Edge.correlation_threshold`` on the live ``TOPOLOGY``, falling
    back to ``_TOPOLOGY_DEFAULT_CORRELATION_THRESHOLD``).

    Malformed schema entries — a non-``dict`` ``topology`` block, a
    non-``list`` edge container, a non-``dict`` edge entry, a
    missing/non-string ``target``, a missing ``weight`` field, a
    ``weight`` that is neither numeric nor the literal string
    ``"callable"``, a non-finite numeric ``weight`` (Python's
    ``json`` parses ``NaN``/``Infinity``/``-Infinity`` as float by
    default), or a ``correlation_threshold`` that is non-numeric,
    ``bool``, not finite, or outside the half-open range
    ``(-1, 1]`` — surface as dedicated violation messages rather
    than crashing the validator. A hand-edited or older
    ``schema.json`` therefore degrades to a clear report instead of
    a ``KeyError``, ``AttributeError``, or ``TypeError`` traceback.
    An invalid ``correlation_threshold`` additionally falls back to
    ``_resolve_edge_correlation_threshold(source, target)`` so the
    rest of the coupling check still runs.

    Non-finite cell values (NaN, +/-inf) in either canonical load
    column are also flagged: ``np.std`` and ``np.corrcoef`` both
    return NaN on such input, and ``corr < threshold`` would
    silently evaluate False and bypass the check. Well-formed runs
    only emit finite floats, so this guard only fires on
    hand-edited or otherwise corrupted CSVs.
    """
    if schema["metadata"].get("topology_mode") != "realistic":
        return []
    topology = schema.get("topology")
    if topology is None or topology == {}:
        # Missing or empty block is the older-schema / narrow-run
        # case; degrade silently. Anything else must be a dict —
        # ``topology.keys()`` would crash on a list/string/scalar
        # before any per-edge violation could surface, so surface
        # one dedicated violation up front instead.
        return []
    if not isinstance(topology, dict):
        return [
            f"topology block malformed in schema.json (expected "
            f"dict, got {type(topology).__name__})"
        ]

    anomaly_windows = _read_anomaly_exclusion_windows(
        output_dir / "anomalies.csv"
    )

    # Per-run cache for the per-instance long-form column reads: one
    # parse per (component, metric) for the whole edge loop instead of
    # one per edge (see _validate_topology_coupling_per_instance).
    per_instance_column_cache: dict = {}

    violations: list[str] = []
    for source in sorted(topology.keys()):
        source_entry = live_topology_load_metrics.get(source)
        if source_entry is None:
            # An edge whose source isn't in the load-metrics registry
            # can't be correlation-checked because we don't know which
            # column to read. Skip silently — _validate_topology()
            # already rejects this at import time so reaching here would
            # mean a schema doc that names a source the current build
            # doesn't recognize, which is a separate concern.
            continue
        source_edges = topology.get(source)
        if not isinstance(source_edges, list):
            violations.append(
                f"topology coupling {source}: edge list malformed in "
                f"schema.json (expected list, got "
                f"{type(source_edges).__name__})"
            )
            continue
        source_canonical = source_entry[0]
        source_data = _read_component_metric_column(
            output_dir / f"{source}.csv", source_canonical
        )
        if source_data is None:
            continue
        source_ts, source_vals = source_data

        # Align on the intersection of timestamps (drop-rate noise
        # means a given second can appear in one CSV but not the
        # other). Build a dict lookup for the source side and walk
        # each target side to keep this O(N) rather than O(N^2).
        source_map = {ts: v for ts, v in zip(source_ts, source_vals)}

        for edge_entry in source_edges:
            if not isinstance(edge_entry, dict):
                violations.append(
                    f"topology coupling {source}: edge entry malformed "
                    f"in schema.json (expected dict, got "
                    f"{type(edge_entry).__name__})"
                )
                continue
            target = edge_entry.get("target")
            if not isinstance(target, str) or not target:
                violations.append(
                    f"topology coupling {source}: edge entry missing or "
                    f"invalid 'target' in schema.json (got {target!r})"
                )
                continue
            if "weight" not in edge_entry:
                violations.append(
                    f"topology coupling {source}->{target}: edge entry "
                    f"missing 'weight' in schema.json"
                )
                continue
            weight = edge_entry["weight"]
            if weight == "callable":
                continue
            if not isinstance(weight, (int, float)) or isinstance(
                weight, bool
            ):
                violations.append(
                    f"topology coupling {source}->{target}: edge weight "
                    f"in schema.json must be a number or the literal "
                    f"\"callable\" (got {weight!r})"
                )
                continue
            # Python's ``json`` loader parses ``NaN``/``Infinity``/
            # ``-Infinity`` as float by default (a CPython extension);
            # ``_validate_topology()`` rejects those values on the live
            # ``Edge`` so the validator's schema view must match. A
            # non-finite weight cannot drive a meaningful Pearson check
            # either, so flag it and skip the edge.
            if not math.isfinite(float(weight)):
                violations.append(
                    f"topology coupling {source}->{target}: edge weight "
                    f"in schema.json must be finite "
                    f"(got {weight!r})"
                )
                continue
            if weight == 0.0:
                # ``_validate_topology()`` accepts ``weight == 0`` as a
                # saturation-only placeholder: the edge declares the
                # logistic feedback shape (`Edge.saturation`) without
                # contributing to the downstream's canonical load
                # baseline. ``_compose_topology_coupled_specs`` skips
                # zero-weight constant edges for the same reason, so
                # there is no load-coupling contract to check here.
                continue
            target_entry = live_topology_load_metrics.get(target)
            if target_entry is None:
                continue
            target_canonical = target_entry[0]
            target_data = _read_component_metric_column(
                output_dir / f"{target}.csv", target_canonical
            )
            if target_data is None:
                continue
            target_ts, target_vals = target_data

            common_ts: list[datetime.datetime] = []
            target_aligned: list[float] = []
            source_aligned: list[float] = []
            for ts, v in zip(target_ts, target_vals):
                src_v = source_map.get(ts)
                if src_v is None:
                    continue
                common_ts.append(ts)
                target_aligned.append(v)
                source_aligned.append(src_v)
            if len(common_ts) < _TOPOLOGY_MIN_ALIGNED_ROWS:
                # Too few aligned rows to compute a meaningful Pearson
                # correlation — happens on intentionally narrow
                # ``--components`` or extreme ``--interval-seconds``
                # selections; not a coupling regression.
                continue

            # Resolve and validate the per-edge correlation threshold
            # exactly once per edge, before any comparison or
            # formatting. A hand-edited schema can carry any JSON value
            # here; treat the same set of invalid shapes the live
            # ``Edge.correlation_threshold`` validator rejects
            # (non-numeric, ``bool``, NaN, +/-inf, outside ``(-1, 1]``)
            # as a dedicated violation and fall back to the live
            # ``TOPOLOGY``'s value (or the module default) so the rest
            # of the check still runs cleanly.
            raw_threshold = edge_entry.get("correlation_threshold")
            if raw_threshold is None:
                threshold = _resolve_edge_correlation_threshold(
                    source, target, topology=live_topology
                )
            elif (
                isinstance(raw_threshold, bool)
                or not isinstance(raw_threshold, (int, float))
                or not math.isfinite(float(raw_threshold))
                or not (-1.0 < float(raw_threshold) <= 1.0)
            ):
                violations.append(
                    f"topology coupling {source}->{target}: "
                    f"correlation_threshold in schema.json must be a "
                    f"finite number in (-1, 1] or null "
                    f"(got {raw_threshold!r}); falling back to live "
                    f"TOPOLOGY"
                )
                threshold = _resolve_edge_correlation_threshold(
                    source, target, topology=live_topology
                )
            else:
                threshold = float(raw_threshold)

            source_arr = np.array(source_aligned, dtype=np.float64)
            target_arr = np.array(target_aligned, dtype=np.float64)
            pair_windows = _filter_windows_for_pair(
                anomaly_windows,
                source, source_canonical,
                target, target_canonical,
                topology=live_topology,
                topology_load_metrics=live_topology_load_metrics,
            )
            # Source and target share ``common_ts`` and ``pair_windows``,
            # so compute the keep mask once and apply it to both arrays.
            # This halves the exclusion cost per edge and keeps the
            # validator's hot path linear in the number of rows.
            if pair_windows:
                keep_mask = _compute_anomaly_keep_mask(
                    common_ts, pair_windows
                )
                source_kept = source_arr[keep_mask]
                target_kept = target_arr[keep_mask]
            else:
                source_kept = source_arr
                target_kept = target_arr
            if len(source_kept) < _TOPOLOGY_MIN_ALIGNED_ROWS:
                continue
            # Non-finite values (NaN/+/-inf) in either column would
            # poison ``np.std`` and ``np.corrcoef`` (both return NaN),
            # silently flipping ``corr < threshold`` to False and
            # bypassing the coupling check. Treat any non-finite cell
            # as a regression — well-formed runs only ever write
            # finite floats, so this only fires on hand-edited or
            # otherwise corrupted CSVs.
            source_finite = np.isfinite(source_kept)
            target_finite = np.isfinite(target_kept)
            if not (source_finite.all() and target_finite.all()):
                sides: list[str] = []
                if not source_finite.all():
                    sides.append(f"{source}.{source_canonical}")
                if not target_finite.all():
                    sides.append(f"{target}.{target_canonical}")
                violations.append(
                    f"topology coupling {source}->{target}: "
                    f"non-finite values in "
                    f"{' and '.join(sides)} "
                    f"(NaN/+/-inf); Pearson correlation undefined "
                    f"(expected >= {threshold:.4f})"
                )
                continue
            # Pearson is undefined when either side is constant
            # (zero-variance column). Treat that as a coupling
            # regression: a constant downstream load is exactly the
            # mutation the validator is supposed to flag.
            source_std = float(np.std(source_kept))
            target_std = float(np.std(target_kept))
            if source_std == 0.0 or target_std == 0.0:
                # Name the offending side(s) explicitly — both std
                # values are already computed, and "X or Y" forces the
                # operator to inspect two columns when the violation
                # already knows which one is constant.
                sides = []
                if source_std == 0.0:
                    sides.append(f"{source}.{source_canonical}")
                if target_std == 0.0:
                    sides.append(f"{target}.{target_canonical}")
                violations.append(
                    f"topology coupling {source}->{target}: zero-variance "
                    f"column ({' and '.join(sides)}); Pearson correlation "
                    f"undefined (expected >= {threshold:.4f})"
                )
                continue
            corr = float(np.corrcoef(source_kept, target_kept)[0, 1])
            if corr < threshold:
                violations.append(
                    f"topology coupling {source}->{target}: "
                    f"Pearson({source}.{source_canonical}, "
                    f"{target}.{target_canonical})={corr:.4f} "
                    f"below threshold {threshold:.4f}"
                )

            # phase 8: per-instance correlation. When both
            # sides' schemas declare ``dimensions`` with matched
            # cardinalities (1:1 routing applies), additionally
            # verify Pearson(source.iK, target.iK) >= threshold for
            # each matching pair. Runs unconditionally — even if the
            # aggregate-mean check above just recorded a violation,
            # the per-instance breakdown is still useful (it names
            # the exact pod pair that diverged from the 1:1 contract,
            # which the mean-aggregate Pearson alone cannot do).
            violations += _validate_topology_coupling_per_instance(
                output_dir, schema, source, target,
                source_canonical, target_canonical,
                threshold,
                pair_windows=pair_windows,
                compute_keep_mask=_compute_anomaly_keep_mask,
                min_aligned_rows=_TOPOLOGY_MIN_ALIGNED_ROWS,
                column_cache=per_instance_column_cache,
            )
    return violations


def _resolve_edge_correlation_threshold(
    source: str, target: str, *, topology: dict[str, list[object]]
) -> float:
    """Look up the per-edge ``correlation_threshold`` from live ``TOPOLOGY``.

    Falls back to ``_TOPOLOGY_DEFAULT_CORRELATION_THRESHOLD`` when the
    edge is not declared in the live module (e.g. the schema was written
    by a build that declared an edge the current build no longer ships)
    or when ``Edge.correlation_threshold`` is ``None``.
    """
    for edge in topology.get(source, ()):
        if edge.target == target:
            if edge.correlation_threshold is not None:
                return float(edge.correlation_threshold)
            break
    return _TOPOLOGY_DEFAULT_CORRELATION_THRESHOLD
