"""Combine per-component CSVs into the unified ``combined_metrics_unified.csv``.

Extracted verbatim from ``legacy.py`` (decomposition step 5; see
``.trellis/tasks/07-02-legacy-monolith-decomposition/design.md``). Holds
the wide (streaming + materialized) and long-form combine writers, the
autodiscovery helper, and the defensive monotonic pre-scan. Reads the
shared CSV primitives from ``csv_layout`` and the atomic writer from
``artifacts``; ``legacy.py`` re-imports every name so the historic
``legacy.<name>`` surface is unchanged.

Monkeypatch note: ``_wide_component_rows_are_monotonic`` is called only by
``combine_logs_unified`` in this module, so a test that stubs the pre-scan
must patch it *here* (``anomaly_metric_creator.combine_impl``), not on the
``legacy`` re-import — the intra-module call resolves in this namespace.
See ``tests/test_combine.py``.
"""

from __future__ import annotations

import csv
import heapq
import os
from pathlib import Path

from .artifacts import _atomic_artifact_open
from .csv_layout import (
    _scan_component_csv_headers,
    write_long_form_merge,
)

_NON_COMPONENT_FILES = {"anomalies.csv", "gauges.csv"}

# Written only when the 'combined' artifact is selected (which itself
# requires "metrics" in --emit). Tracked separately so the pre-clean and summary can
# treat it as its own slot.
_COMBINE_OUTPUT_FILENAME = "combined_metrics_unified.csv"


def discover_components(input_dir):
    """Return the sorted list of component names found in ``input_dir``.

    A component is any ``*.csv`` in ``input_dir`` that isn't the anomalies
    manifest or one of this script's own combine outputs.
    """
    components = []
    for path in sorted(Path(input_dir).glob("*.csv")):
        name = path.name
        if name in _NON_COMPONENT_FILES:
            continue
        if name.startswith("combined_metrics_"):
            continue
        components.append(path.stem)
    return components


def combine_logs_unified(
    components,
    input_dir,
    output_file=None,
    *,
    assume_monotonic_wide_components=None,
):
    """Join the per-component CSVs in ``input_dir`` into a single unified CSV.

    ``output_file`` defaults to ``input_dir/combined_metrics_unified.csv``.
    Returns ``(total_rows, size_mb)``. ``assume_monotonic_wide_components``
    is an optional allowlist for trusted, freshly-generated wide CSVs whose
    rows are known to be timestamp-monotonic, letting the normal non-DST
    generation path avoid a second full-file scan. Components not in that set
    still take the conservative scan so hand-staged/autodiscovered CSVs keep
    the same safety behavior as direct ``combine`` calls.

    Layout is chosen by header inspection of the per-component CSVs:

    - If every per-component CSV is dimensionless (the first column is
      ``timestamp`` followed directly by the metric columns — the
      default ``N=1`` anonymous-instance shape), the writer emits the
      wide ``timestamp,component_a_m0,component_a_m1,...`` layout
      byte-identically to the pre-existing output (so existing
      ``test_combine.py`` row/column-shape assertions continue to hold).
    - If **any** per-component CSV carries the full ``id, host, pod, az,
      region, tenant`` dimension prefix after ``timestamp``, the writer
      switches to a long layout: ``timestamp,component,id,host,pod,az,
      region,tenant,metric,value``. Rows are emitted in
      ``(timestamp, component, instance_id, metric)`` tie-break order
      via ``heapq.merge`` across per-(component, instance) iterators,
      matching the long-form ``gauges.csv`` ordering contract. Empty /
      dropped cells are skipped — long form encodes "this measurement
      was emitted" explicitly via row presence.

    The dispatch is purely header-based, so any path that produces
    dimensioned per-component CSVs routes here — ``--instances-per-
    component N > 1`` (the Phase-2 fan-out) is the canonical path, but
    ``--instance-config`` can also produce a dimensioned single-instance
    CSV and lands the same long-form output.

    Missing per-component CSVs raise ``SystemExit`` in both branches —
    the wide path checks first via ``_scan_component_csv_headers``'s
    ``layout[c]["exists"]`` flags so direct callers get a consistent
    user-facing error instead of an unhandled ``FileNotFoundError``
    later in the loop.
    """
    input_dir = Path(input_dir)
    if output_file is None:
        output_file = input_dir / _COMBINE_OUTPUT_FILENAME
    output_file = Path(output_file)

    component_csv_paths = {c: input_dir / f"{c}.csv" for c in components}
    any_dimensioned, layout = _scan_component_csv_headers(component_csv_paths)

    # Mirror ``_write_combined_long_form``'s missing-file guard for the
    # wide-form path so direct callers of ``combine_logs_unified`` see a
    # consistent user-facing error regardless of which branch they hit.
    # ``combine_logs`` already raises on missing files when invoked with
    # an explicit ``components`` allowlist, so this check is dead on the
    # main pipeline; it covers a direct caller that bypasses
    # ``combine_logs`` and lands a missing file in the layout.
    missing = [
        f"{name}.csv" for name in components
        if not layout[name]["exists"]
    ]
    if missing:
        raise SystemExit(
            f"missing component CSVs for combine: {', '.join(missing)}"
        )

    print("\nCreating UNIFIED format combined file...")
    print(f"Components discovered: {', '.join(components)}")

    if any_dimensioned:
        total_rows = _write_combined_long_form(
            components, layout, output_file,
        )
        size_mb = os.path.getsize(output_file) / (1024 * 1024)
        print(f"\nUnified format file created: {output_file}")
        print(f"Total rows: {total_rows:,}")
        print(f"File size: {size_mb:.2f} MB")
        return total_rows, size_mb

    component_metrics = {}
    row_streams = []
    nonmonotonic_components = []
    trusted_monotonic_components = (
        set(assume_monotonic_wide_components)
        if assume_monotonic_wide_components is not None
        else None
    )

    for component in components:
        input_path = input_dir / f"{component}.csv"
        print(f"Loading {component}.csv...")

        with open(input_path, "r", encoding="utf-8") as infile:
            reader = csv.DictReader(infile)
            # The any-dimensioned dispatch above already routed the long-
            # form CSVs into ``_write_combined_long_form``, so by the time
            # control reaches this DictReader path every per-component CSV
            # is the classic dimensionless ``timestamp, m0, m1, ...`` shape
            # and ``fieldnames[1:]`` is the metric list verbatim.
            #
            # ``csv.DictReader.fieldnames`` is ``None`` for a fully empty
            # input, ``[]`` for a file whose first line is blank, and may
            # legitimately omit ``timestamp`` if the user staged a CSV
            # with a different schema. ``combine_logs`` rejects a missing
            # file before we get here, but a present-but-malformed header
            # would otherwise crash either the list comprehension below
            # (``None``) or the ``row["timestamp"]`` lookup in the loop
            # (missing key). Validate all three shapes up-front and raise
            # the same flavor of ``SystemExit`` ``combine_logs`` uses for
            # missing files so the operator gets a clean diagnosis
            # instead of a stack trace.
            if not reader.fieldnames:
                raise SystemExit(
                    f"{input_path.name} is empty / has no header row; "
                    f"combine_logs cannot derive its metric columns"
                )
            if "timestamp" not in reader.fieldnames:
                raise SystemExit(
                    f"{input_path.name} header {list(reader.fieldnames)!r} "
                    f"is missing the 'timestamp' column; combine_logs "
                    f"cannot key per-component rows without it"
                )
            metric_names = [f for f in reader.fieldnames if f != "timestamp"]
            component_metrics[component] = metric_names

        def _iter_component_rows(
            component_name: str = component,
            path: Path = input_path,
            metrics: list[str] = metric_names,
        ):
            previous_timestamp = None
            occurrence = 0
            with open(path, "r", encoding="utf-8", newline="") as infile:
                reader = csv.DictReader(infile)
                for row in reader:
                    timestamp = row["timestamp"]
                    # This generator only feeds the monotonic ``heapq.merge``
                    # path, so any duplicate timestamps (the DST fall-back
                    # 02:00-02:59 wall-clock hour) arrive consecutively. Track
                    # only the previous timestamp + a running occurrence index
                    # (non-DST rows always 0) instead of a full per-timestamp
                    # dict, keeping the stream O(1) in memory.
                    if timestamp == previous_timestamp:
                        occurrence += 1
                    else:
                        occurrence = 0
                        previous_timestamp = timestamp
                    yield (
                        timestamp,
                        occurrence,
                        component_name,
                        {metric: row[metric] for metric in metrics},
                    )

        row_streams.append(_iter_component_rows())
        if (
            trusted_monotonic_components is None
            or component not in trusted_monotonic_components
        ) and not _wide_component_rows_are_monotonic(input_path):
            nonmonotonic_components.append(component)

    fieldnames = ["timestamp"]
    for component in components:
        for metric in component_metrics[component]:
            fieldnames.append(f"{component}_{metric}")

    print(f"Total columns: {len(fieldnames)} (1 timestamp + {len(fieldnames) - 1} metrics)")

    if nonmonotonic_components:
        print(
            "Non-monotonic timestamp stream detected for "
            f"{', '.join(nonmonotonic_components)}; using sorted wide merge."
        )
        total_rows = _write_combined_wide_materialized(
            components, input_dir, output_file, component_metrics, fieldnames,
        )
        size_mb = os.path.getsize(output_file) / (1024 * 1024)
        print(f"\nUnified format file created: {output_file}")
        print(f"Total rows: {total_rows:,}")
        print(f"File size: {size_mb:.2f} MB")
        return total_rows, size_mb

    with _atomic_artifact_open(output_file) as outfile:
        writer = csv.DictWriter(outfile, fieldnames=fieldnames)
        writer.writeheader()

        total_rows = 0
        current_key = None
        current_components = {}

        def _flush_current_bucket() -> None:
            nonlocal total_rows
            if current_key is None:
                return
            timestamp, _occurrence = current_key
            row = {"timestamp": timestamp}
            for component in components:
                component_row = current_components.get(component, {})
                for metric in component_metrics[component]:
                    row[f"{component}_{metric}"] = component_row.get(metric, "")
            writer.writerow(row)
            total_rows += 1

        for timestamp, occurrence, component, row_values in heapq.merge(*row_streams):
            bucket_key = (timestamp, occurrence)
            if current_key is None:
                current_key = bucket_key
            elif bucket_key != current_key:
                _flush_current_bucket()
                current_key = bucket_key
                current_components = {}
            current_components[component] = row_values

        _flush_current_bucket()

    size_mb = os.path.getsize(output_file) / (1024 * 1024)
    print(f"\nUnified format file created: {output_file}")
    print(f"Total rows: {total_rows:,}")
    print(f"File size: {size_mb:.2f} MB")
    return total_rows, size_mb


def _wide_component_rows_are_monotonic(input_path: Path) -> bool:
    """Return false when a wide component CSV's timestamp keys move backward.

    Duplicate timestamps (the DST fall-back wall-clock hour) are only valid
    when consecutive, so the occurrence index is tracked with a running
    counter against the previous timestamp rather than a full per-timestamp
    dict — keeping the scan O(1) in memory. A non-consecutive duplicate
    necessarily implies an earlier strictly-backward timestamp step, which
    this loop already reports as non-monotonic before the occurrence value
    could diverge from the dict-based count.
    """
    previous_key = None
    previous_timestamp = None
    occurrence = 0
    with open(input_path, "r", encoding="utf-8", newline="") as infile:
        reader = csv.DictReader(infile)
        for row in reader:
            timestamp = row["timestamp"]
            if timestamp == previous_timestamp:
                occurrence += 1
            else:
                occurrence = 0
                previous_timestamp = timestamp
            key = (timestamp, occurrence)
            if previous_key is not None and key < previous_key:
                return False
            previous_key = key
    return True


def _write_combined_wide_materialized(
    components: list[str],
    input_dir: Path,
    output_file: Path,
    component_metrics: dict[str, list[str]],
    fieldnames: list[str],
) -> int:
    """Write the wide combined CSV when inputs require an explicit sort.

    Normal dimensionless runs stream through ``heapq.merge`` above. DST
    artifact injection intentionally moves timestamps backward within each
    component file, so those rare runs need the historical materialized sort to
    preserve duplicate wall-clock hours exactly.
    """
    data_by_timestamp = {}
    for component in components:
        input_path = input_dir / f"{component}.csv"
        seen_in_component = {}
        with open(input_path, "r", encoding="utf-8", newline="") as infile:
            reader = csv.DictReader(infile)
            metric_names = component_metrics[component]
            for row in reader:
                timestamp = row["timestamp"]
                occurrence = seen_in_component.get(timestamp, 0)
                seen_in_component[timestamp] = occurrence + 1
                bucket = data_by_timestamp.setdefault((timestamp, occurrence), {})
                bucket[component] = {
                    metric: row[metric] for metric in metric_names
                }

    with _atomic_artifact_open(output_file) as outfile:
        writer = csv.DictWriter(outfile, fieldnames=fieldnames)
        writer.writeheader()
        for bucket_key in sorted(data_by_timestamp.keys()):
            timestamp, _occurrence = bucket_key
            row = {"timestamp": timestamp}
            for component in components:
                component_row = data_by_timestamp[bucket_key].get(component, {})
                for metric in component_metrics[component]:
                    row[f"{component}_{metric}"] = component_row.get(metric, "")
            writer.writerow(row)
    return len(data_by_timestamp)


def _write_combined_long_form(
    components: list[str], layout: dict[str, dict], output_file: Path,
) -> int:
    """Write the long-form unified CSV when any per-component CSV carries
    the multi-instance dimension prefix.

    Layout mirrors the long-form ``gauges.csv``: ``timestamp, component,
    id, host, pod, az, region, tenant, metric, value``. Per-(component,
    instance) iterators feed ``heapq.merge`` on parsed timestamps;
    sources are pre-sorted by ``(component, instance_dims)`` (alphabetical
    by component, then by id-leading dim tuple) so equal-timestamp
    groups emit component-then-instance-then-metric order — the
    documented Phase 5 ``(timestamp, component, instance_id, metric)``
    tie-break. The caller-supplied ``components`` order is **not**
    preserved (the function sorts components alphabetically internally)
    so the on-disk component order is deterministic regardless of how
    the caller built the list; this matches the long-form
    ``gauges.csv`` writer's tie-break contract.

    Missing per-component CSVs raise ``SystemExit`` rather than being
    silently skipped — the wide-form path's ``combine_logs`` guard
    raises on the same input, and the long form mirrors that contract
    so the two paths agree on what "missing input" means even when the
    caller bypasses ``combine_logs`` to call this writer directly.
    """
    # Mirror the wide-form path's contract: every requested component
    # must have a per-component CSV on disk. Defends against a direct
    # caller that bypasses ``combine_logs``'s missing-file check; the
    # autodiscovery path through ``combine_logs(...)`` already filters
    # to existing files via ``discover_components``, so this loop is a
    # no-op there.
    missing = [
        f"{name}.csv" for name in components
        if not layout[name]["exists"]
    ]
    if missing:
        raise SystemExit(
            f"missing component CSVs for long-form combine: "
            f"{', '.join(missing)}"
        )
    # Source-building, FD preflight, (component, instance_dims) sort/tie-break,
    # header, and emission are shared with the gauges long-form writer in
    # csv_layout.write_long_form_merge (07-06-long-form-merge-writer-dedupe).
    # ``components`` order is intentionally not preserved — sort alphabetically
    # so the on-disk component order is deterministic regardless of how the
    # caller built the list (matches the long-form gauges tie-break contract).
    sorted_components = sorted(components)
    return write_long_form_merge(sorted_components, layout, output_file)


def combine_logs(input_dir, components=None, *, assume_monotonic_wide_components=None):
    """Write the unified combined CSV from per-component CSVs in ``input_dir``.

    When ``components`` is ``None``, the combine step autodiscovers every
    ``*.csv`` in ``input_dir`` (excluding the anomalies manifest and prior
    combine outputs). When ``components`` is provided, it is used as the
    allowlist for which CSVs to combine. Any named component whose
    ``{name}.csv`` is missing from ``input_dir`` raises ``SystemExit``.

    Output ordering depends on which layout the underlying
    ``combine_logs_unified`` dispatches to:

    - **Wide layout** (default, dimensionless inputs) — the caller-
      supplied ``components`` order is preserved verbatim in the
      ``component_a_m0, component_a_m1, component_b_m0, …`` column
      sequence.
    - **Long layout** (any dimensioned input, phase 5) — the
      caller-supplied ``components`` order is **not** preserved. Rows
      are merged chronologically and tie-break on
      ``(component, instance_id, metric)`` with components sorted
      alphabetically; the caller-supplied order only filters which
      components participate. The on-disk column shape is fixed
      (``timestamp, component, id, host, pod, az, region, tenant,
      metric, value``), so the order argument has no column-layout
      effect in the long form.
    """
    input_dir = Path(input_dir)
    if components is None:
        components = discover_components(input_dir)
        if not components:
            raise SystemExit(f"No component CSVs found in {input_dir}/")
    else:
        components = list(components)
        missing = [f"{name}.csv" for name in components
                   if not (input_dir / f"{name}.csv").exists()]
        if missing:
            raise SystemExit(
                f"missing component CSVs in {input_dir}: "
                f"{', '.join(missing)}"
            )
    return combine_logs_unified(
        components,
        input_dir,
        assume_monotonic_wide_components=assume_monotonic_wide_components,
    )

