"""Per-instance topology-coupling validator helpers."""

from __future__ import annotations

import csv
import datetime
from pathlib import Path

import numpy as np

from .timeutil import _parse_csv_timestamp


def _read_component_metric_column_per_instance(
    csv_path: Path, metric_name: str,
) -> dict[str, tuple[list[datetime.datetime], np.ndarray]] | None:
    """Return per-instance ``(timestamps, values)`` for ``metric_name``
    from a dim-aware long-form per-component CSV.

    Returns ``None`` when the CSV does not exist, has no header, is
    dimensionless (no ``id`` column), or does not declare
    ``metric_name``. The result maps instance id → ``(timestamps,
    values)`` so the per-instance correlation check can align
    each instance's load column across components without re-walking
    the CSV per pair.

    Counterpart to ``_read_component_metric_column`` which aggregates
    via mean across instances; this helper keeps each instance's
    rows distinct so per-instance coupling can be Pearson-checked
    against its matching upstream instance.
    """
    if not csv_path.exists():
        return None
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
        try:
            id_idx = header.index("id")
        except ValueError:
            # Dimensionless layout — the aggregate reader is the right
            # one. Distinguish from "no header" so the caller can
            # branch on the absence specifically.
            return None
        per_instance: dict[
            str, tuple[list[datetime.datetime], list[float]]
        ] = {}
        for row in reader:
            if not row or col_idx >= len(row) or id_idx >= len(row):
                continue
            inst_id = row[id_idx]
            try:
                ts = _parse_csv_timestamp(row[0])
                value = float(row[col_idx])
            except ValueError:
                continue
            ts_list, val_list = per_instance.setdefault(inst_id, ([], []))
            ts_list.append(ts)
            val_list.append(value)
    # Per-instance CSV blocks are written in increasing timestamp
    # order by ``generate_component`` (one block per instance, rows
    # within each block ordered by elapsed seconds), and the DST
    # splice that produces non-monotonic timestamps is rejected up
    # front for non-anonymous instances — by ``parse_args`` on the
    # CLI path and by ``generate_component``'s own
    # ``dst_inject_day > 0`` + non-anonymous-instance guard for
    # direct programmatic callers. Reading rows in CSV order
    # therefore yields a monotonic ``ts_list`` per instance already —
    # no sort needed. Skipping the O(n log n) work noticeably speeds
    # up the validator on long runs.
    out: dict[str, tuple[list[datetime.datetime], np.ndarray]] = {}
    for inst_id, (ts_list, val_list) in per_instance.items():
        out[inst_id] = (ts_list, np.array(val_list, dtype=np.float64))
    return out


def _validate_topology_coupling_per_instance(
    output_dir: Path, schema: dict,
    source: str, target: str,
    source_canonical: str, target_canonical: str,
    threshold: float,
    *,
    pair_windows: list[tuple[datetime.datetime, datetime.datetime]],
    compute_keep_mask,
    min_aligned_rows: int,
    column_cache: dict | None = None,
) -> list[str]:
    """Per-instance edge correlation check (phase 8).

    Only fires when both ``source`` and ``target`` schemas declare a
    ``dimensions`` block with matched cardinalities. Under
    matched-cardinality 1:1 routing, downstream instance ``K``'s
    canonical load metric is driven by upstream instance ``K``'s
    canonical load metric exclusively; the Pearson correlation
    between the two should clear the same per-edge threshold the
    aggregate-mean check above uses.

    Returns one violation per failing pod pair so the report names
    the exact instance that diverged from the contract. Skips
    silently when:

    - Either side is dimensionless (no per-instance CSV layout).
    - Cardinalities mismatch (uniform fan-out fallback — per-pod
      isolation is not the contract there).
    - Either side has fewer than the configured minimum aligned rows for the
      matching instance.
    - Either column is zero-variance or non-finite (flagged by the
      aggregate check above; surfacing once is enough).

    Instance pairing matches the generator's index-based 1:1
    routing in ``_per_instance_upstream_view``: position ``K`` in
    the source CSV is paired with position ``K`` in the target CSV.
    This keeps the validator consistent with the generator across
    ``--instance-config`` runs where the two components declare
    instances in different orders, and across
    ``--instances-per-component`` runs whose ids ("i0".."i19")
    don't lexically sort in the same order as their generation
    index.
    """
    components_schema = schema.get("components")
    if not isinstance(components_schema, dict):
        return []

    def _find_dimensions(name: str) -> dict | None:
        entry = components_schema.get(name)
        if isinstance(entry, dict):
            dims = entry.get("dimensions")
            if isinstance(dims, dict):
                return dims
        return None

    source_dims = _find_dimensions(source)
    target_dims = _find_dimensions(target)
    if source_dims is None or target_dims is None:
        return []
    src_card = source_dims.get("cardinality")
    tgt_card = target_dims.get("cardinality")
    if (
        not isinstance(src_card, int)
        or not isinstance(tgt_card, int)
        or src_card != tgt_card
        or src_card <= 1
    ):
        # Mismatched cardinalities or single-instance — uniform
        # fan-out applies, per-pod isolation is not the contract.
        return []

    def _read_cached(component: str, metric: str):
        # One full long-form parse per (CSV, metric) per validation run:
        # a source with several outgoing edges (apigateway has four)
        # used to have its entire per-instance CSV re-parsed once per
        # edge. The cache is owned by ``_validate_topology_coupling``
        # and lives only for the duration of one validation pass.
        if column_cache is None:
            return _read_component_metric_column_per_instance(
                output_dir / f"{component}.csv", metric
            )
        key = (component, metric)
        if key not in column_cache:
            column_cache[key] = _read_component_metric_column_per_instance(
                output_dir / f"{component}.csv", metric
            )
        return column_cache[key]

    source_per_inst = _read_cached(source, source_canonical)
    target_per_inst = _read_cached(target, target_canonical)
    if source_per_inst is None or target_per_inst is None:
        return []

    # Pair instances by CSV-block insertion order, NOT sorted id.
    # ``generate_component`` writes one block per instance in
    # ``instances[k]`` order, and the 1:1 routing in
    # ``_per_instance_upstream_view`` maps downstream instance ``K``
    # to upstream instance ``K`` by *list index*. Sorting by id here
    # would mis-pair when ``--instance-config`` lists the two
    # components' instances in different declared orders, or when
    # ``--instances-per-component`` produces ``i0..i9, i10`` whose
    # lexical sort ("i10" < "i2") drifts from the numeric block
    # order. ``_read_component_metric_column_per_instance`` walks
    # rows top-to-bottom and uses ``setdefault``, so dict iteration
    # order matches the on-disk block order, which matches the
    # generator's instance list order.
    source_ids = list(source_per_inst.keys())
    target_ids = list(target_per_inst.keys())
    if len(source_ids) != len(target_ids):
        return []

    violations: list[str] = []
    # Loop-invariant: the caller computes the edge-level anomaly window
    # filter once and passes it to every pod pair.
    for src_id, tgt_id in zip(source_ids, target_ids):
        src_ts, src_vals = source_per_inst[src_id]
        tgt_ts, tgt_vals = target_per_inst[tgt_id]
        common_ts: list[datetime.datetime] = []
        target_aligned: list[float] = []
        source_aligned: list[float] = []

        # Linear-time two-pointer merge join (phase 8). Since
        # both CSV blocks were read into monotonic timestamp lists,
        # we can align them in O(N+M) without materialising a full
        # dict per pod pair.
        i = 0
        j = 0
        n_src = len(src_ts)
        n_tgt = len(tgt_ts)
        while i < n_src and j < n_tgt:
            s_t = src_ts[i]
            t_t = tgt_ts[j]
            if s_t == t_t:
                common_ts.append(s_t)
                source_aligned.append(src_vals[i])
                target_aligned.append(tgt_vals[j])
                i += 1
                j += 1
            elif s_t < t_t:
                i += 1
            else:
                j += 1

        if len(common_ts) < min_aligned_rows:
            continue
        source_arr = np.array(source_aligned, dtype=np.float64)
        target_arr = np.array(target_aligned, dtype=np.float64)
        if pair_windows:
            keep_mask = compute_keep_mask(common_ts, pair_windows)
            source_kept = source_arr[keep_mask]
            target_kept = target_arr[keep_mask]
        else:
            source_kept = source_arr
            target_kept = target_arr
        if len(source_kept) < min_aligned_rows:
            continue
        if not (np.isfinite(source_kept).all() and np.isfinite(target_kept).all()):
            # Aggregate check already flagged non-finite values; skip
            # silently to avoid duplicate noise.
            continue
        if np.std(source_kept) == 0.0 or np.std(target_kept) == 0.0:
            continue
        corr = float(np.corrcoef(source_kept, target_kept)[0, 1])
        if corr < threshold:
            violations.append(
                f"topology coupling {source}.{src_id}->{target}.{tgt_id}: "
                f"Pearson({source_canonical}, {target_canonical})="
                f"{corr:.4f} below threshold {threshold:.4f} "
                f"(per-instance, matched cardinality)"
            )
    return violations

