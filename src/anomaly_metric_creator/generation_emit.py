"""CSV row formatting and emission helpers for generation.

The helpers here do not consume RNG; ``generation.py`` passes in the live
formatter callable so legacy and direct generation monkeypatches preserve their
expected visibility.
"""

from __future__ import annotations

import datetime
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

from .artifacts import _atomic_artifact_open
from .csv_layout import _INSTANCE_DIMENSION_COLUMNS
from .models_impl import Instance

START = datetime.datetime(2026, 3, 10, 0, 0, 0)


def _emit_component_metrics(
    *,
    file_path,
    fieldnames: list[str],
    values: np.ndarray,
    per_instance_values: dict[int, np.ndarray],
    drop_mask: np.ndarray,
    ts_strings: np.ndarray,
    instances: list[Instance],
    is_anonymous: bool,
    dst_inject_day: int,
    start_time: datetime.datetime,
    format_fixed3: Callable[[np.ndarray], np.ndarray],
) -> None:
    # Rounding and fixed-3 string formatting exist only to produce
    # the CSV bytes, so they run inside the emit guard: a run whose
    # ``--emit`` omits ``metrics`` previously paid the
    # full formatting cost (historically ~80% of generation
    # runtime, per the comment below) and threw the result away.
    # Safe to skip when not emitting: the ``topology_capture``
    # snapshots above were taken pre-round by design, and nothing
    # after this block reads ``values`` / the per-instance buffers.
    # No RNG is consumed here, so draw order — and therefore every
    # locked hash — is unchanged for runs that do emit metrics.
    np.round(values, 3, out=values)
    for buf in per_instance_values.values():
        np.round(buf, 3, out=buf)

    keep_mask = ~drop_mask
    kept_ts = ts_strings[keep_mask]
    kept_vals = values[keep_mask]

    # Format values to fixed 3 decimals. ``np.char.mod("%.3f", ...)`` is correct
    # but spends ~80% of the run inside ``_vec_string``. Scaling to int + numpy
    # string ops produces the same output ~2x faster.
    str_vals = format_fixed3(kept_vals)
    # Phase 4: per-instance string buffers for instances that diverged from
    # the shared baseline via a partial ``instance_filter``. Other instances
    # reuse ``str_vals`` directly.
    per_instance_str_vals: dict[int, np.ndarray] = {
        inst_idx: format_fixed3(buf[keep_mask])
        for inst_idx, buf in per_instance_values.items()
    }

    with _atomic_artifact_open(file_path) as f:
        # Precompute the shared metric suffix once per component. Every
        # instance not in ``per_instance_str_vals`` reuses this array,
        # preserving Phase 2's "precompute once, reuse per instance"
        # optimization. The anonymous branch is a single-instance
        # degenerate case so reuse is a no-op there.
        shared_metric_suffix = _format_metric_suffix(str_vals)
        if is_anonymous:
            # Dimensionless default — byte-identical to pre-Phase-2 output.
            f.write("timestamp," + ",".join(fieldnames) + "\n")
            rows = _format_csv_row_block(
                kept_ts, shared_metric_suffix,
                dim_prefix="", dst_inject_day=dst_inject_day,
                start_time=start_time,
            )
            f.write("\n".join(rows.tolist()))
            f.write("\n")
        else:
            # Long form: timestamp,id,host,pod,az,region,tenant,<metrics>
            # ``_INSTANCE_DIMENSION_COLUMNS`` is the single source of
            # truth for the column order; ``_iter_component_rows`` lifts
            # the same prefix back into the per-row dimensions dict
            # consumed by the OTEL gauge attributes path (Phase 6).
            dim_header = "timestamp," + ",".join(_INSTANCE_DIMENSION_COLUMNS)
            f.write(dim_header + "," + ",".join(fieldnames) + "\n")
            # Materialize per-instance suffixes for forked buffers only;
            # other instances reuse ``shared_metric_suffix`` so the
            # all-instances-unfiltered case stays byte-identical to
            # Phase 2.
            per_instance_metric_suffixes: dict[int, np.ndarray] = {
                inst_idx: _format_metric_suffix(buf)
                for inst_idx, buf in per_instance_str_vals.items()
            }
            for inst_idx, inst in enumerate(instances):
                # Build the dimension prefix string once per instance.
                # Reads fields off ``Instance`` in canonical column order
                # so adding/removing a field touches only
                # ``_INSTANCE_DIMENSION_COLUMNS``. The leading comma
                # lets ``_format_csv_row_block`` concatenate as
                # ``ts + dim_prefix + "," + metric_suffix`` regardless
                # of branch.
                dim_vals = ",".join(
                    getattr(inst, field) if getattr(inst, field) is not None else ""
                    for field in _INSTANCE_DIMENSION_COLUMNS
                )
                inst_suffix = per_instance_metric_suffixes.get(
                    inst_idx, shared_metric_suffix
                )
                inst_rows = _format_csv_row_block(
                    kept_ts, inst_suffix,
                    dim_prefix=f",{dim_vals}",
                    dst_inject_day=dst_inject_day,
                    start_time=start_time,
                )
                f.write("\n".join(inst_rows.tolist()))
                f.write("\n")

def _format_metric_suffix(str_vals: np.ndarray) -> np.ndarray:
    """Return ``,``-joined metric values as a 1-D string array.

    ``str_vals`` is the post-format ``(n_rows, n_cols)`` array produced by
    ``_format_fixed3``; the returned array carries one ``v0,v1,...,vk``
    string per row, with no leading comma. Callers prepend the
    ``timestamp,<optional_dims>,`` head via ``_format_csv_row_block``.

    Aliasing safety: when ``n_cols >= 2`` the first ``np.char.add`` call
    returns a fresh array, so we can start from a view of column 0 and
    rely on the loop to allocate. When ``n_cols == 1`` the loop never
    runs, so we must explicitly copy column 0 to avoid the caller's
    downstream ``np.char.add`` mutating the source array.
    """
    n_cols = str_vals.shape[1]
    if n_cols == 1:
        return str_vals[:, 0].copy()
    suffix = str_vals[:, 0]
    for col in range(1, n_cols):
        suffix = np.char.add(suffix, ",")
        suffix = np.char.add(suffix, str_vals[:, col])
    return suffix


def _format_csv_row_block(kept_ts: np.ndarray, metric_suffix: np.ndarray,
                          *, dim_prefix: str, dst_inject_day: int,
                          start_time: datetime.datetime = START) -> np.ndarray:
    """Concatenate ``timestamp + dim_prefix + ',' + metric_suffix`` per row.

    ``dim_prefix`` is the empty string for the dimensionless / single-anonymous-
    instance CSV layout, or one instance's comma-prefixed dimension
    *values* (e.g. ``",i0,,pod-0,,,"`` — leading comma, then the six
    ``_INSTANCE_DIMENSION_COLUMNS`` values with empty cells for unset
    fields) for one long-form instance block. The shared shape lets
    the same DST splice apply regardless of which branch produced the
    block — fixing a prior failure of the long-form writer to call
    ``_splice_dst_artifact`` after rebuilding rows from raw timestamps. The helper itself does not gate which combinations
    are reachable: ``_splice_dst_artifact`` runs unconditionally when
    ``dst_inject_day > 0`` regardless of ``dim_prefix``. ``parse_args``
    and the matching ``generate_component`` defense-in-depth check
    still reject ``--inject-dst-artifact-day > 0`` paired with a
    multi-instance run by design (per-instance non-monotonic timestamps
    break downstream ``heapq.merge`` in ``gauges.csv`` /
    ``combined_metrics_unified.csv``), so today every production caller
    that reaches the long-form branch has ``dst_inject_day == 0``; the
    helper would handle the long-form splice correctly under any
    future caller that relaxes the guard.
    """
    rows = np.char.add(kept_ts, f"{dim_prefix},")
    rows = np.char.add(rows, metric_suffix)
    if dst_inject_day > 0:
        rows = _splice_dst_artifact(rows, kept_ts, dst_inject_day, start_time)
    return rows


def _splice_dst_artifact(rows: np.ndarray, kept_ts: np.ndarray,
                         dst_day: int,
                         start_time: datetime.datetime = START) -> np.ndarray:
    """Duplicate the 02:00–02:59 hour on ``dst_day`` (1-based) inside ``rows``.

    ``rows`` is the formatted ``ts,v0,...,vk`` string array; ``kept_ts`` is the
    matching ``YYYY-MM-DD HH:MM:SS`` timestamps used to locate the window. The
    returned array has 3,600 / interval extra rows for the targeted day. The
    duplicate hour reuses the same timestamp prefix, so the resulting CSV has
    non-monotonic timestamps — the realistic fall-DST quirk.
    """
    day_date = (start_time + datetime.timedelta(days=dst_day - 1)).strftime("%Y-%m-%d")
    dst_start = f"{day_date} 02:00:00"
    dst_end = f"{day_date} 03:00:00"
    mask = (kept_ts >= dst_start) & (kept_ts < dst_end)
    indices = np.flatnonzero(mask)
    if indices.size == 0:
        return rows
    first = int(indices[0])
    last = int(indices[-1])
    return np.concatenate([rows[:last + 1], rows[first:last + 1], rows[last + 1:]])




def _format_fixed3(arr: np.ndarray) -> np.ndarray:
    """Format a float array as ``%.3f``-equivalent strings ~2x faster than
    ``np.char.mod``. Scales to int64, then assembles ``sign + int + '.' + frac``
    via vectorized numpy string ops.
    """
    scaled = np.round(arr * 1000).astype(np.int64)
    sign = np.where(scaled < 0, "-", "")
    absolute = np.abs(scaled)
    int_part = (absolute // 1000).astype("<U16")
    frac_part = np.char.zfill((absolute % 1000).astype("<U3"), 3)
    out = np.char.add(sign, int_part)
    out = np.char.add(out, ".")
    return np.char.add(out, frac_part)


def _build_timestamp_arrays(
    total_seconds: int,
    interval: float = 1.0,
    *,
    start_time: datetime.datetime = START,
):
    """Pre-compute the shared per-run timestamp arrays (numpy + formatted str).

    Built once per run and reused across every component — they're identical
    by construction, so re-computing them per component is pure waste.
    Row ``i`` is at ``start_time + i * interval`` seconds; row count is
    ``floor(total_seconds / interval)``. Strings are rendered at second
    precision when ``interval >= 1.0`` and at millisecond precision otherwise
    so adjacent sub-second rows never share a timestamp string.
    """
    n_rows = int(total_seconds // interval)
    step_us = int(round(interval * 1_000_000))
    ts_array = (
        np.datetime64(start_time) + np.arange(n_rows) * np.timedelta64(step_us, "us")
    )
    if interval < 1.0:
        ts_strings = np.char.replace(
            np.datetime_as_string(ts_array, unit="ms"), "T", " "
        )
    else:
        ts_strings = np.char.replace(
            np.datetime_as_string(ts_array, unit="s"), "T", " "
        )
    return ts_array, ts_strings
