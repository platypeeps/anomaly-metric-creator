"""Long-form / 4-column ``gauges.csv`` writer.

Extracted verbatim from ``legacy.py`` (decomposition step 3; see
``.trellis/tasks/07-02-legacy-monolith-decomposition/design.md``).
Reads the shared CSV primitives from ``csv_layout`` and the timestamp
parser from ``timeutil``; ``legacy.py`` re-imports ``write_gauges_csv``
so the historic surface is unchanged.
"""

from __future__ import annotations

import csv
import heapq
from pathlib import Path

from .artifacts import _atomic_artifact_open
from .csv_layout import (
    _scan_component_csv_headers,
    write_long_form_merge,
)
from .timeutil import _parse_csv_timestamp


def write_gauges_csv(
    component_csv_paths: dict[str, Path],
    output_path: Path,
) -> int:
    """Write a long-form ``gauges.csv`` with one row per
    ``(timestamp, component, metric, value)`` tuple (4-column shape) or
    ``(timestamp, component, id, host, pod, az, region, tenant, metric,
    value)`` tuple (10-column shape, phase 5) from the given
    per-component CSVs.

    Layout is decided purely by header inspection: if **any**
    per-component CSV carries the full ``id, host, pod, az, region,
    tenant`` dimension prefix after ``timestamp``, the writer emits the
    10-column long form. If every CSV is the classic dimensionless
    shape (first column ``timestamp`` followed directly by the metric
    columns), the writer emits today's 4-column form byte-identically,
    so the existing locked golden hashes are preserved.

    The dispatch is header-based, not flag-based: the Phase-2
    ``--instances-per-component N > 1`` fan-out is the canonical path
    that lands a dimensioned CSV, but ``--instance-config`` can also
    produce a dimensioned single-instance CSV and routes to the same
    long-form output.

    Rows are emitted in a chronologically merged timeline via
    ``heapq.merge`` keyed on the parsed timestamp — the same ordering
    ``stream_otel_gauges`` produces over its OTLP data points, so the file
    artifact can be cross-checked against an OTLP collector recording.
    Equal-timestamp ties tie-break on sorted component name, then on
    instance id (sorted), then on the per-component CSV's column order
    (``MetricSpec`` order). The function sorts ``component_csv_paths.keys()``
    internally so the component tiebreaker holds regardless of how the
    caller built the mapping; the instance tiebreaker sorts ids
    *lexicographically*, which matches the generated per-instance block
    order for single-digit fan-outs but diverges from numeric order at
    ``--instances-per-component`` >= 11 (``i0, i1, i10, i11, …, i19,
    i2, …``). ``_write_combined_long_form`` sorts the same way, so the
    two long-form artifacts always agree on the tie-break.

    Values are written through verbatim from the per-component CSV's raw
    cell string — no ``float(raw)`` coercion is attempted, so the on-disk
    bytes never depend on Python's ``str(float)`` repr and any malformed
    cell would propagate as-is (this is intentionally narrower than
    ``stream_otel_gauges``, which ``float(raw)``-coerces and silently skips
    unparseable cells; ``generate_component`` only ever writes finite
    floats, so in practice the two paths emit the same data points).
    Empty / dropped cells are skipped (mirroring the OTEL gauge stream's
    behavior on dropped rows).

    Returns the number of data rows written (header excluded).

    **CLI-internal surface.** This function is part of a CLI-internal
    surface, not a supported programmatic API: a per-component CSV that
    does not exist on disk is skipped silently rather than raising --
    ``_scan_component_csv_headers`` records a per-component ``exists``
    flag from ``Path.exists()`` and the component list is filtered on it
    before any row is read. That is documented semantics, not a defect. See
    ``.trellis/spec/amc/backend/api-cli-server.md`` § Library-API Error
    Posture.
    """
    any_dimensioned, layout = _scan_component_csv_headers(component_csv_paths)

    if not component_csv_paths:
        with _atomic_artifact_open(output_path) as f:
            f.write("timestamp,component,metric,value\n")
        return 0

    # Sort the component iterators by component name so equal-timestamp
    # ties tie-break on sorted-component order regardless of how the caller
    # built ``component_csv_paths``. This is what the locked golden hashes
    # encode (callers in this module already pass ``sorted(args.components)``,
    # so the sort is idempotent in the happy path).
    sorted_components = [
        c for c in sorted(component_csv_paths)
        if layout[c]["exists"]
    ]

    if not any_dimensioned:
        # Classic 4-column path. Preserved byte-identically by keeping the
        # row iterator and writer shape unchanged from pre-existing code so
        # the locked SHA-256 hashes in ``tests/test_gauges_file.py`` still
        # apply to N=1 / dimensionless runs.
        def _row_iter_4col(component: str):
            entry = layout[component]
            metric_cols = entry["metric_cols"]
            n_metrics = len(metric_cols)
            with open(entry["path"], "r", encoding="utf-8", newline="") as fh:
                reader = csv.reader(fh)
                next(reader, None)  # header (already inspected)
                for row in reader:
                    if not row:
                        continue
                    ts = row[0]
                    ts_dt = _parse_csv_timestamp(ts)
                    values = row[1: 1 + n_metrics]
                    yield (ts_dt, ts, component,
                           list(zip(metric_cols, values)))

        iters = [_row_iter_4col(c) for c in sorted_components]
        rows_written = 0
        with _atomic_artifact_open(output_path) as out_f:
            writer = csv.writer(out_f, lineterminator="\n")
            writer.writerow(("timestamp", "component", "metric", "value"))
            for _dt, ts, comp, name_value_pairs in heapq.merge(
                *iters, key=lambda item: item[0]
            ):
                for name, raw in name_value_pairs:
                    if raw == "":
                        continue
                    writer.writerow((ts, comp, name, raw))
                    rows_written += 1
        return rows_written

    # Long form with dimensions (phase 5): merge the per-(component, instance)
    # blocks chronologically into the 10-column CSV. The source-building,
    # FD preflight, (component, instance_dims) sort/tie-break, header, and
    # empty-cell skip are shared with the combined long-form writer in
    # csv_layout.write_long_form_merge (07-06-long-form-merge-writer-dedupe).
    # This writer's caller already ensures every component CSV exists, so
    # unlike the combine path there is no missing-file guard here.
    return write_long_form_merge(sorted_components, layout, output_path)
