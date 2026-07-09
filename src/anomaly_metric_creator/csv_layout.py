"""Shared per-component CSV structural + reading primitives.

Extracted verbatim from ``legacy.py`` (decomposition step 3; see
``.trellis/tasks/07-02-legacy-monolith-decomposition/design.md``).
These header-scan and row-iteration helpers are shared by the gauge
writer (``gauges_impl.py``), the combine long-form writer, and the OTEL
gauge streamer (both still in ``legacy.py`` today), plus ``server_mcp``
via ``state.legacy``. They live in this leaf so every consumer imports
one copy; ``legacy.py`` re-imports each name so the historic
``legacy.<name>`` surface is unchanged.
"""

from __future__ import annotations

import csv
import heapq
from pathlib import Path

from .artifacts import _atomic_artifact_open
from .timeutil import _parse_csv_timestamp


# Canonical column order for the multi-instance long-form dimension
# prefix. ``generate_component()``'s long-form branch writes these
# columns in this order; ``combine_logs`` matches the same shape when
# detecting a multi-instance CSV; ``_is_anonymous_instance_list`` keys
# its predicate off the same field list so all three views stay in
# lockstep with the ``Instance`` dataclass above. The Phase-5 long-form
# writers (``write_gauges_csv`` / ``combine_logs_unified``) read the same
# constant to detect dimensioned per-component CSVs by header inspection
# and to project dimension values into the long-form output rows.
_INSTANCE_DIMENSION_COLUMNS = ("id", "host", "pod", "az", "region", "tenant")

# Derived from _INSTANCE_DIMENSION_COLUMNS so the two cannot drift:
# _INSTANCE_DIMENSION_COLUMNS leads with "id" (validated separately as a
# string/None/CSV-safe value); the remaining fields are the dimension
# attributes that _validate_instance_list iterates over and schema.json
# advertises as axes.
_INSTANCE_DIMENSION_FIELDS: tuple[str, ...] = _INSTANCE_DIMENSION_COLUMNS[1:]


def _is_anonymous_instance_list(instances) -> bool:
    """True iff ``instances`` is the single-anonymous-Instance() default.

    Single source of truth for the "emit today's dimensionless format"
    branch in ``generate_component()``, schema emission, and the DST-guard
    helper. Keying off ``_INSTANCE_DIMENSION_FIELDS`` means adding or
    removing an ``Instance`` dimension field touches one derived constant
    instead of multiple predicates.
    """
    if len(instances) != 1:
        return False
    only = instances[0]
    return (
        getattr(only, "id") is None
        and all(getattr(only, field) is None for field in _INSTANCE_DIMENSION_FIELDS)
    )


def _iter_component_rows(component: str, csv_path: Path):
    """Yield ``(timestamp_str, component, [(metric_name, value)...], dimensions)``
    for each data row in ``csv_path``.

    ``dimensions`` is a ``dict[str, str]`` carrying any
    ``_INSTANCE_DIMENSION_COLUMNS`` cells lifted off the row when the CSV
    was emitted with Phase 2's multi-instance fan-out (header begins
    ``timestamp,id,host,pod,az,region,tenant,...``). Dimensionless CSVs
    (today's default, ``--instances-per-component 1``) yield an empty dict,
    so downstream consumers can treat the field as always present.
    Empty-string dimension cells are omitted from the dict so the OTEL
    attribute path never emits empty-string attributes (Phase 6).

    ``generate_component`` omits dropped rows from the CSV entirely (via the
    ``keep_mask``) rather than writing them as blank lines, so under normal
    operation the file has only the header plus one row per surviving
    timestamp — dropped timestamps are naturally absent from the gauge
    stream. The streamer still defensively skips any zero-column row to
    tolerate hand-edited inputs.
    """
    with open(csv_path, "r", encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
        # Detect the Phase 2 dimension column block. The block is the full
        # ``_INSTANCE_DIMENSION_COLUMNS`` tuple in canonical order starting at
        # column 1, written verbatim by ``generate_component`` — a partial /
        # reordered header is treated as "no dimensions" and those columns
        # flow into the metric path (where ``float(raw)`` will naturally skip
        # them).
        dim_col_count = len(_INSTANCE_DIMENSION_COLUMNS)
        if (header[0:1] == ["timestamp"]
                and len(header) >= 1 + dim_col_count
                and tuple(header[1:1 + dim_col_count])
                == _INSTANCE_DIMENSION_COLUMNS):
            dim_cols = _INSTANCE_DIMENSION_COLUMNS
            metric_start = 1 + dim_col_count
        else:
            dim_cols = ()
            metric_start = 1
        metric_cols = header[metric_start:]
        for row in reader:
            if not row:
                continue
            ts = row[0]
            dimensions: dict[str, str] = {}
            for name, raw in zip(dim_cols, row[1:metric_start]):
                if raw == "":
                    continue
                dimensions[name] = raw
            values = []
            for name, raw in zip(metric_cols, row[metric_start:]):
                if raw == "":
                    continue
                try:
                    values.append((name, float(raw)))
                except ValueError:
                    continue
            yield ts, component, values, dimensions


# Margin reserved for stdin/stdout/stderr + the output file + room
# for the OS's accounting; the long-form merge needs at least
# ``len(sources) + _LONG_FORM_FD_MARGIN`` file descriptors available.
_LONG_FORM_FD_MARGIN = 16


def _ensure_long_form_fd_capacity(n_sources: int) -> None:
    """Raise the soft FD limit to fit ``n_sources`` concurrent file
    handles, or ``SystemExit`` with an actionable message if the OS
    won't let us.

    The long-form ``heapq.merge`` over per-(component, instance)
    iterators primes every source, so all of them hold an open
    ``csv.reader`` handle for the lifetime of the merge. At max
    fan-out (``len(COMPONENTS) * MAX_INSTANCES_PER_COMPONENT`` =
    14 × 20 = 280) we can exceed the default macOS soft limit
    (256), causing ``EMFILE`` deep inside the writer.

    Fix on POSIX (Linux, macOS): try to bump the soft limit (up to
    the hard cap) using ``resource.setrlimit``; if the hard limit is
    still too low or ``setrlimit`` rejects the raise, exit early with
    a clear error naming the needed headroom and the user-facing
    levers (``--instances-per-component``, ``--components``,
    ``ulimit -n``).

    Windows is a no-op: ``resource`` is POSIX-only, and there's no
    portable equivalent of ``RLIMIT_NOFILE`` we can pre-flight. The
    helper returns silently and lets ``open()`` surface the real
    error inside ``heapq.merge`` if the OS-level FD cap is reached.
    In practice the Windows default open-file table is plenty large
    for ``MAX_INSTANCES_PER_COMPONENT * len(COMPONENTS) = 280`` so
    this is unlikely to bite; tests
    (``test_ensure_long_form_fd_capacity_raises_systemexit_when_hard_limit_too_low``)
    skip on Windows via ``pytest.importorskip("resource")``.

    ``n_sources`` is only the file-handle count from this merge; the
    ``_LONG_FORM_FD_MARGIN`` reserves space for stdio + the output
    stream + a bit of OS overhead.
    """
    needed = n_sources + _LONG_FORM_FD_MARGIN
    try:
        import resource  # POSIX-only; absent on Windows.
    except ImportError:
        # No portable rlimit on Windows. If we end up needing more
        # FDs than the platform allows, ``open()`` will surface the
        # real error; we can't pre-flight it from here, so trust the
        # OS to enforce the bound at write time.
        return
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    if soft >= needed:
        return
    target = needed if hard == resource.RLIM_INFINITY else min(needed, hard)
    if target < needed:
        raise SystemExit(
            f"long-form output needs {needed} concurrent file handles "
            f"({n_sources} per-instance sources + {_LONG_FORM_FD_MARGIN} "
            f"reserve) but the process FD hard limit is {hard}. Lower "
            f"--instances-per-component, narrow --components, or raise "
            f"the system FD limit (ulimit -n) before re-running."
        )
    try:
        resource.setrlimit(resource.RLIMIT_NOFILE, (target, hard))
    except (ValueError, OSError) as exc:
        raise SystemExit(
            f"long-form output needs {needed} concurrent file handles "
            f"({n_sources} per-instance sources + {_LONG_FORM_FD_MARGIN} "
            f"reserve) but raising the soft FD limit from {soft} to "
            f"{target} failed: {exc}. Lower --instances-per-component, "
            f"narrow --components, or raise the system FD limit "
            f"(ulimit -n) before re-running."
        ) from exc


def _classify_component_csv_header(
    header: list[str],
) -> tuple[tuple[str, ...], list[str]]:
    """Split a per-component CSV header into ``(dim_cols, metric_cols)``.

    A header is classified as dimensioned only when **all three** hold:
    column 0 is exactly ``"timestamp"``, the header is long enough to
    fit the full dimension prefix (``len(header) >= 1 +
    len(_INSTANCE_DIMENSION_COLUMNS)``), and columns 1..6 after the
    timestamp are exactly ``_INSTANCE_DIMENSION_COLUMNS`` in registry
    order — i.e. the six fields ``id, host, pod, az, region, tenant``.
    Headers that fail any of those checks fall through to the no-dim
    branch, where every post-``timestamp`` column is treated as a
    metric. The explicit ``header[0]`` and length checks defend against
    a malformed / user-staged CSV whose first column happens to be
    ``id`` (or another dim name) being mis-routed into the dimensioned
    branch. Empty / missing headers go the same no-dim route.
    """
    if not header:
        return (), []
    dim_count = len(_INSTANCE_DIMENSION_COLUMNS)
    rest = header[1:]
    if (
        header[0] == "timestamp"
        and len(header) >= 1 + dim_count
        and tuple(rest[:dim_count]) == _INSTANCE_DIMENSION_COLUMNS
    ):
        return _INSTANCE_DIMENSION_COLUMNS, rest[dim_count:]
    return (), rest


def _scan_component_csv_headers(
    component_csv_paths: dict[str, Path],
) -> tuple[bool, dict[str, dict]]:
    """Inspect every per-component CSV header and return a layout summary.

    Returns ``(any_dimensioned, info)`` where ``info[component]`` is
    ``{"path": path, "exists": bool, "dim_cols": tuple, "metric_cols":
    list[str]}``. ``any_dimensioned`` is True when at least one existing
    CSV has the dimension prefix; this drives the long-form-with-dims vs.
    classic 4-column branch in ``write_gauges_csv``.
    """
    any_dimensioned = False
    info: dict[str, dict] = {}
    for component, path in component_csv_paths.items():
        entry: dict = {
            "path": path,
            "exists": path.exists(),
            "dim_cols": (),
            "metric_cols": [],
        }
        if entry["exists"]:
            with open(path, "r", encoding="utf-8", newline="") as fh:
                reader = csv.reader(fh)
                header = next(reader, None)
            if header:
                dim_cols, metric_cols = _classify_component_csv_header(header)
                entry["dim_cols"] = dim_cols
                entry["metric_cols"] = metric_cols
                if dim_cols:
                    any_dimensioned = True
        info[component] = entry
    return any_dimensioned, info


def _scan_instance_block_layout(
    csv_path: Path, *, has_dims: bool,
) -> list[tuple[tuple[str, ...], int]]:
    """Return the ordered list of ``(dim_tuple, start_offset)`` pairs for
    every instance block in ``csv_path``, in the order they first appear.

    ``start_offset`` is the opaque seek cookie returned by
    ``fh.tell()`` BEFORE the block's first row is read — *not* a raw
    byte position. Python's text-mode files return a cookie that
    encodes both the byte position and the decoder state, and it is
    only meaningful when handed back to ``seek()`` on a file opened
    with **matching** ``encoding`` and ``newline`` parameters.
    ``_iter_component_instance_rows`` reopens ``csv_path`` with the
    same ``encoding="utf-8"`` / ``newline=""`` settings as the scan
    here, so the cookie round-trips cleanly. ``seek()``ing straight to
    the block's start cookie gives O(rows_total) total work per
    per-component CSV — each row is read at most twice (once by the
    scan, once by exactly one iterator) — instead of the
    O(rows_total × instances) the previous "scan from top each time"
    iterator did.

    ``has_dims`` short-circuits the scan: dimensionless CSVs always
    have a single conceptual block represented by a tuple of empty
    strings (one per ``_INSTANCE_DIMENSION_COLUMNS`` entry); the
    ``start_offset`` is the cookie right after the header line. For
    dimensioned CSVs the scan reads one line at a time, recording
    ``tell()`` before each line, and detects block boundaries where
    the dim tuple changes (``generate_component`` writes per-instance
    blocks sequentially, so each unique dim tuple appears in exactly
    one contiguous block). The lightweight scan uses ``readline()`` +
    ``line.split(',')`` so the recorded ``tell()`` cookie isn't
    corrupted by ``csv.reader``'s internal buffer — the writer side
    emits unquoted comma-separated values (see the ``np.char.add``
    path in ``generate_component``), so the simple split is
    equivalent to a full CSV parse for the dim columns.
    """
    dim_count = len(_INSTANCE_DIMENSION_COLUMNS)
    if not has_dims:
        empty_dims = tuple("" for _ in _INSTANCE_DIMENSION_COLUMNS)
        with open(csv_path, "r", encoding="utf-8", newline="") as fh:
            fh.readline()  # header
            start_offset = fh.tell()
        return [(empty_dims, start_offset)]
    blocks: list[tuple[tuple[str, ...], int]] = []
    last: tuple[str, ...] | None = None
    with open(csv_path, "r", encoding="utf-8", newline="") as fh:
        fh.readline()  # header
        while True:
            pos = fh.tell()
            line = fh.readline()
            if not line:
                break  # EOF
            if line in ("\n", "\r\n"):
                # Skip blank lines (tolerate hand-edited inputs).
                # ``generate_component`` omits dropped rows from the
                # CSV entirely rather than writing them as blanks
                # (see ``_iter_component_rows``), so blank lines do
                # not occur on a freshly generated file — this guard
                # only matters for staged / hand-edited inputs.
                continue
            # generate_component writes plain comma-separated values
            # without quoting (see the np.char.add path), so a simple
            # split is safe and exactly what csv.reader would parse.
            fields = line.rstrip("\r\n").split(",")
            if len(fields) < 1 + dim_count:
                continue
            dims = tuple(fields[1:1 + dim_count])
            if dims != last:
                blocks.append((dims, pos))
                last = dims
    return blocks


def _iter_component_instance_rows(
    csv_path: Path, start_offset: int, *,
    has_dims: bool, n_metrics: int,
):
    """Yield ``(ts_dt, ts_raw, metric_values)`` for the rows belonging
    to one instance block in ``csv_path``, starting at the seek cookie
    ``start_offset``.

    Opens a fresh file handle so the caller can hold multiple
    per-instance iterators on the same CSV open simultaneously (e.g.
    for ``heapq.merge``). The handle ``seek()``s straight to the
    block's start cookie produced by ``_scan_instance_block_layout`` —
    no re-scanning from the top — and a ``csv.reader`` parses from
    there. The cookie is the opaque value returned by Python's
    text-mode ``tell()``, valid only against a handle opened with the
    matching ``encoding="utf-8"`` / ``newline=""`` settings (both the
    scan and this iterator open the file that way, so the round-trip
    is well-defined). On dimensioned CSVs the iterator records the
    first row's dim tuple as the block's identity and exits as soon as
    a later row's dim tuple differs (``generate_component`` writes
    blocks contiguously, so the dim transition is the end-of-block
    marker). On dimensionless CSVs the iterator yields every data row
    to EOF; ``start_offset`` then points to the first data row.
    """
    dim_count = len(_INSTANCE_DIMENSION_COLUMNS)
    with open(csv_path, "r", encoding="utf-8", newline="") as fh:
        fh.seek(start_offset)
        reader = csv.reader(fh)
        if has_dims:
            min_cols = 1 + dim_count
            block_dims: tuple[str, ...] | None = None
            for row in reader:
                # Skip blank lines AND short/malformed rows so a row
                # with fewer than ``min_cols`` columns cannot set
                # ``block_dims`` to a truncated tuple and prematurely
                # terminate the block on the next well-formed row.
                # ``_scan_instance_block_layout`` applies the same guard
                # so the two helpers cannot disagree on what counts as
                # an in-block row.
                if len(row) < min_cols:
                    continue
                dims = tuple(row[1:1 + dim_count])
                if block_dims is None:
                    block_dims = dims
                elif dims != block_dims:
                    return  # EOF for this block
                ts = row[0]
                ts_dt = _parse_csv_timestamp(ts)
                metric_values = row[
                    1 + dim_count: 1 + dim_count + n_metrics
                ]
                yield (ts_dt, ts, metric_values)
        else:
            for row in reader:
                if not row:
                    continue
                ts = row[0]
                ts_dt = _parse_csv_timestamp(ts)
                metric_values = row[1: 1 + n_metrics]
                yield (ts_dt, ts, metric_values)


def write_long_form_merge(
    sorted_components: list[str], layout: dict[str, dict], output_path: Path,
) -> int:
    """Chronologically merge the per-(component, instance) rows of a
    dimensioned run into the 10-column long-form CSV
    ``timestamp, component, id, host, pod, az, region, tenant, metric, value``
    and atomically publish it at ``output_path``. Returns the count of
    ``(timestamp, component, instance, metric)`` rows written.

    Shared by the two long-form *file* writers — ``write_gauges_csv`` (the
    ``gauges.csv`` peer of ``stream_otel_gauges``) and
    ``_write_combined_long_form`` (the dimensioned
    ``combined_metrics_unified.csv`` path). Callers own their component
    ordering (pass a pre-sorted ``sorted_components``) and their own
    missing-input policy before calling; everything downstream — the
    ``(component, instance_dims)`` sort key, the tie-break, the header, and
    the empty-cell skip — is identical, so it lives here rather than
    duplicated at each site (07-06-long-form-merge-writer-dedupe). Raw cell
    strings pass through verbatim so the byte hash never depends on
    ``str(float)`` repr; the OTEL streamer's ``float()`` coercion is a
    deliberate, separate asymmetry that this dedupe does not touch.
    """
    sources = []
    for component in sorted_components:
        entry = layout[component]
        metric_cols = entry["metric_cols"]
        has_dims = bool(entry["dim_cols"])
        instance_blocks = _scan_instance_block_layout(
            entry["path"], has_dims=has_dims,
        )
        for instance_dims, start_offset in instance_blocks:
            row_iter = _iter_component_instance_rows(
                entry["path"], start_offset,
                has_dims=has_dims, n_metrics=len(metric_cols),
            )

            def _tagged(_iter=row_iter, _comp=component,
                        _dims=instance_dims, _cols=metric_cols):
                for ts_dt, ts_raw, values in _iter:
                    yield (ts_dt, ts_raw, _comp, _dims, _cols, values)
            # Sort key carries the full ``instance_dims`` tuple, not just the
            # leading ``id`` field, so a hypothetical future registry where
            # two instances share an ``id`` but differ in another dim still
            # gets a total order. In v1 the ``id`` is unique per component, so
            # the trailing fields are inert.
            sources.append(((component, instance_dims), _tagged()))

    # Each source holds an open file handle for the lifetime of the merge, so
    # preflight the FD soft limit (14 components x 20 instances = 280 handles
    # at max fan-out) before ``heapq.merge`` primes the heap.
    _ensure_long_form_fd_capacity(len(sources))

    sources.sort(key=lambda item: item[0])
    iters = [src for _key, src in sources]

    rows_written = 0
    with _atomic_artifact_open(output_path) as out_f:
        writer = csv.writer(out_f, lineterminator="\n")
        writer.writerow(
            ("timestamp", "component", *_INSTANCE_DIMENSION_COLUMNS, "metric", "value")
        )
        for _dt, ts, comp, dims, metric_cols, values in heapq.merge(
            *iters, key=lambda item: item[0]
        ):
            for name, raw in zip(metric_cols, values):
                if raw == "":
                    continue
                writer.writerow((ts, comp, *dims, name, raw))
                rows_written += 1
    return rows_written
