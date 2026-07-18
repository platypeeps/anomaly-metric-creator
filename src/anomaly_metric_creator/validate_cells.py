"""Cell, derivation, and long-form artifact validator helpers."""

from __future__ import annotations

import csv
import math
from collections.abc import Callable
from pathlib import Path

from .combine_impl import _COMBINE_OUTPUT_FILENAME
from .csv_layout import _INSTANCE_DIMENSION_COLUMNS

# Floating-point tolerance for derived-column checks. CSV cells are written
# at 3-decimal precision (see ``np.round(values, 3)`` in
# ``generate_component``), so a derivation recomputed from rounded source
# cells can drift from the stored derived cell by at most a few units of
# the last digit. 0.01 is conservative enough to absorb that drift while
# still catching real bugs (e.g. a 5% miscompute of ``hit_ratio``).
_VALIDATE_DERIVATION_TOLERANCE = 0.01

# Integer-cell tolerance for ``dtype="int"`` checks. CSV cells are 3-decimal
# floats so a value the generator wrote as ``5.000`` round-trips exactly,
# while a fractional source value like ``4.567`` lands well outside this
# band. Using 0.0005 means we reject anything ≥ 0.001 (the smallest
# representable fractional at 3-decimal precision).
_VALIDATE_INT_TOLERANCE = 5e-4


def _validate_component_cells(
    output_dir: Path, schema: dict, component: str
) -> list[str]:
    """Every cell must respect its MetricSpec's declared schema metadata:

    - column order matches the schema's MetricSpec list,
    - parseable as a float,
    - within ``[min_value, max_value]`` if either is declared,
    - integer-valued (modulo 3-decimal CSV precision) when ``dtype="int"``,
    - non-negative when ``semantic_type`` is ``counter`` or ``rate`` (even
      if ``min_value`` was not declared — these semantic kinds are always
      ≥ 0 by definition).

    Each unique violation is reported once with a line-number example so the
    output stays bounded even when a whole column is wrong.

    Phase 8: when the per-component schema declares
    ``dimensions``, the expected header is
    ``("timestamp", *_INSTANCE_DIMENSION_COLUMNS, *metric_names)`` to
    match the Phase 2 long-form per-component CSV. The dim cells (id,
    host, pod, az, region, tenant) are strings, not metric values, so
    they are skipped by the cell-range checks below; the metric cells
    start at index ``1 + len(_INSTANCE_DIMENSION_COLUMNS)`` in that
    branch.
    """
    csv_filename = schema["components"][component]["csv_filename"]
    csv_path = output_dir / csv_filename
    metrics = schema["components"][component]["metrics"]
    dimensions = schema["components"][component].get("dimensions")
    if not csv_path.exists():
        return []

    violations = []
    with open(csv_path, "r", encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        try:
            header = next(reader)
        except StopIteration:
            return [f"{csv_filename}: file has no header row"]

        if dimensions is not None:
            expected_columns = (
                ["timestamp"]
                + list(_INSTANCE_DIMENSION_COLUMNS)
                + [m["name"] for m in metrics]
            )
            metric_col_start = 1 + len(_INSTANCE_DIMENSION_COLUMNS)
        else:
            expected_columns = ["timestamp"] + [m["name"] for m in metrics]
            metric_col_start = 1
        if header != expected_columns:
            violations.append(
                f"{csv_filename}: header {header} does not match schema "
                f"column order {expected_columns}"
            )
            return violations  # cell checks are meaningless once columns drift

        # Track which violations have already fired per (metric, kind) so we
        # don't flood the output for an entire-column violation.
        seen: set[tuple[str, str]] = set()

        def _record(metric_name: str, kind: str, msg: str) -> None:
            key = (metric_name, kind)
            if key in seen:
                return
            seen.add(key)
            violations.append(msg)

        # Hoist the per-column constants out of the row loop: the dict
        # lookups (name, dtype, bounds, semantic_type) are invariant per
        # column, and re-fetching them per cell costs ~4 lookups x rows x
        # metrics (~85M on a default 7-day validation) — the exact
        # "per-row re-computation of constants" pattern the pre-PR
        # checklist forbids in hot paths.
        column_checks = [
            (
                col_idx,
                m["name"],
                m.get("dtype") == "int",
                m.get("min_value"),
                m.get("max_value"),
                m.get("semantic_type"),
            )
            for col_idx, m in enumerate(metrics, start=metric_col_start)
        ]
        for i, row in enumerate(reader, start=2):
            if not row:
                continue
            for col_idx, name, is_int, lo, hi, semantic in column_checks:
                if col_idx >= len(row):
                    _record(name, "missing_col",
                            f"{csv_filename} line {i}: missing column for "
                            f"metric {name!r}")
                    continue
                raw = row[col_idx]
                try:
                    value = float(raw)
                except ValueError:
                    _record(name, "non_numeric",
                            f"{csv_filename} line {i}: {name}={raw!r} not "
                            "parseable as float")
                    continue
                # ``float()`` happily parses ``"nan"`` / ``"inf"`` /
                # ``"-inf"``. Without this guard a NaN cell silently
                # passes every range check below (every comparison
                # against NaN is False) and a NaN/inf cell in a
                # ``dtype="int"`` column crashes ``round()`` with an
                # uncaught ValueError/OverflowError instead of producing
                # a violation report. Mirrors the non-finite posture of
                # ``_validate_topology_coupling``.
                if not math.isfinite(value):
                    _record(name, "non_finite",
                            f"{csv_filename} line {i}: {name}={raw!r} is "
                            "not finite")
                    continue
                if is_int:
                    if abs(value - round(value)) > _VALIDATE_INT_TOLERANCE:
                        _record(name, "fractional",
                                f"{csv_filename} line {i}: {name}={value} "
                                "is fractional but dtype='int'")
                if lo is not None and value < lo:
                    _record(name, "below_min",
                            f"{csv_filename} line {i}: {name}={value} "
                            f"below min_value={lo}")
                if hi is not None and value > hi:
                    _record(name, "above_max",
                            f"{csv_filename} line {i}: {name}={value} "
                            f"above max_value={hi}")
                if semantic in ("counter", "rate") and value < 0:
                    _record(name, "negative_kind",
                            f"{csv_filename} line {i}: {name}={value} "
                            f"is negative but semantic_type={semantic!r}")
    return violations


def _validate_component_derivations(
    output_dir: Path, schema: dict, component: str
) -> list[str]:
    """For every metric with a ``derivation`` string, recompute the value
    from its source columns and assert agreement within
    ``_VALIDATE_DERIVATION_TOLERANCE``.

    Only the derivations the generator implements are checked (the
    string carries the formula for documentation; the validator dispatches
    by ``(component, metric)``). New derived columns must be added to
    ``DERIVATIONS`` in the generator and to ``_RECOMPUTERS`` here in
    lockstep — drift is caught by the test suite (``DERIVATIONS`` and
    ``_RECOMPUTERS`` keysets must match).
    """
    csv_filename = schema["components"][component]["csv_filename"]
    csv_path = output_dir / csv_filename
    metrics = schema["components"][component]["metrics"]
    if not csv_path.exists():
        return []
    derived_entries = [m for m in metrics if m.get("derivation")]
    if not derived_entries:
        return []

    # dispatch tables raise on unknown keys. ``DERIVATIONS`` and
    # ``_RECOMPUTERS`` are paired single-source registries whose keyset
    # equality is enforced by the test suite; a missing recomputer for a
    # component whose schema declares a derivation is programmer drift,
    # not a runtime data issue, and must surface loudly instead of being
    # downgraded to a violation entry.
    recompute = _RECOMPUTERS[component]

    violations = []
    # phase 8: dimensioned per-component CSVs prepend the six
    # dim columns between ``timestamp`` and the metric block, so the
    # ``name_to_col`` index must offset by that prefix to stay aligned
    # with the recomputer's ``row[col]`` reads.
    dimensions = schema["components"][component].get("dimensions")
    metric_col_start = (
        1 + len(_INSTANCE_DIMENSION_COLUMNS) if dimensions is not None else 1
    )
    name_to_col = {
        m["name"]: i + metric_col_start for i, m in enumerate(metrics)
    }
    with open(csv_path, "r", encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        try:
            next(reader)
        except StopIteration:
            return violations
        seen: set[str] = set()
        for i, row in enumerate(reader, start=2):
            if len(seen) == len(derived_entries):
                # Every derived metric has already recorded its
                # one-per-file violation; the remaining rows cannot add
                # anything (each metric reports at most once per CSV).
                break
            if not row:
                continue
            for entry in derived_entries:
                name = entry["name"]
                if name in seen:
                    continue
                col = name_to_col.get(name)
                if col is None or col >= len(row):
                    continue
                # the per-row except is narrowed to the data-only
                # exceptions (malformed cells, accidental zero-division
                # inside a recomputer). ``KeyError`` is intentionally
                # excluded so a dispatch raise from an unknown metric
                # (drift between ``DERIVATIONS`` and the recomputer body)
                # propagates instead of being silently dropped row by row.
                try:
                    actual = float(row[col])
                    expected = recompute(name, row, name_to_col)
                except (ValueError, ZeroDivisionError):
                    continue
                if expected is None:
                    continue
                # NaN poisons the tolerance gate below: ``abs(nan - x) >
                # tol`` is False, so a corrupted derived column (or a
                # NaN source cell flowing through the recomputer) would
                # validate clean. Treat any non-finite side as a
                # violation instead.
                if not (math.isfinite(actual) and math.isfinite(expected)):
                    seen.add(name)
                    violations.append(
                        f"{csv_filename} line {i}: derived {name}={actual} "
                        f"or recomputed value {expected} is not finite "
                        f"(formula: {entry['derivation']})"
                    )
                    continue
                if abs(actual - expected) > _VALIDATE_DERIVATION_TOLERANCE:
                    seen.add(name)
                    violations.append(
                        f"{csv_filename} line {i}: derived {name}={actual} "
                        f"differs from recomputed {expected} by more than "
                        f"{_VALIDATE_DERIVATION_TOLERANCE} (formula: "
                        f"{entry['derivation']})"
                    )
    return violations


def _recompute_cacheservice(metric: str, row: list[str],
                             name_to_col: dict[str, int]) -> float | None:
    """Recompute ``cacheservice.hit_ratio`` from ``cache_hits`` / ``cache_misses``.

    Returns ``None`` when one of the source columns is absent from the
    schema header (a ``--metrics-per-component`` trim drops the column).
    Unparseable source cells raise ``ValueError`` from ``float()``; the
    caller (``_validate_component_derivations``) catches ``ValueError`` /
    ``ZeroDivisionError`` per row so the cell validator can report those
    separately.

    per-metric dispatch within the recomputer raises ``KeyError``
    on any unknown metric. ``DERIVATIONS['cacheservice']`` declares only
    ``hit_ratio``; a call with any other metric is programmer drift
    between the generator-side ``DERIVATIONS`` table and the validator-
    side recomputer body and must not be silently swallowed.
    """
    if metric != "hit_ratio":
        raise KeyError(
            f"_recompute_cacheservice: unknown metric {metric!r}; "
            f"cacheservice declares only 'hit_ratio' as derived"
        )
    hits_col = name_to_col.get("cache_hits")
    misses_col = name_to_col.get("cache_misses")
    if hits_col is None or misses_col is None:
        return None
    if hits_col >= len(row) or misses_col >= len(row):
        return None
    hits = float(row[hits_col])
    misses = float(row[misses_col])
    denom = hits + misses
    if denom <= 0:
        return 0.0
    return 100.0 * hits / denom


# Per-component derivation dispatch table. Keys must mirror
# ``DERIVATIONS`` in the generator; the validator's
# ``_validate_component_derivations`` looks up by component name and
# delegates by metric name. Adding a new derived column requires
# adding both a ``DERIVATIONS`` entry (for the generator) and a
# ``_RECOMPUTERS`` entry (for the validator).
_RECOMPUTERS: dict[str, Callable[[str, list[str], dict[str, int]],
                                  float | None]] = {
    "cacheservice": _recompute_cacheservice,
}


def _schema_has_any_dimensions(schema: dict) -> bool:
    """True iff any component in the schema declares a ``dimensions`` block.

    The long-form file writers (``gauges.csv`` and
    ``combined_metrics_unified.csv``) dispatch to the dim-aware 10-column
    layout when *any* per-component CSV in the run is dimensioned; this
    helper mirrors that any-of predicate so the validator's long-form
    header check stays in lockstep with the writer.
    """
    return any(
        payload.get("dimensions") is not None
        for payload in schema.get("components", {}).values()
    )


def _validate_long_form_dimensions(
    output_dir: Path, schema: dict,
) -> list[str]:
    """Verify ``gauges.csv`` and ``combined_metrics_unified.csv`` headers
    match the layout implied by the schema's per-component ``dimensions``
    blocks.

    Phase 5 made both long-form writers dim-aware: a run with any
    dimensioned per-component CSV dispatches to the 10-column
    ``timestamp, component, id, host, pod, az, region, tenant, metric,
    value`` shape; otherwise the historic 4-column
    ``timestamp, component, metric, value`` (gauges) and wide
    ``timestamp, component_metric, ...`` (combined) layouts stay
    byte-identical. This validator only enforces the long-form 10-column
    header when at least one component declares ``dimensions``. The
    classic 4-column / wide header is *not* checked here (status quo —
    no validator inspects those headers in v1; the layout is pinned only
    by the writers' locked SHA-256 golden hashes in
    ``tests/test_gauges_file.py`` / ``tests/test_combine.py``). The
    existing required-files / unknown-files checks only verify file
    presence, not column layout, so they don't fill that gap.

    Only files actually declared in ``schema.files`` are inspected —
    missing-file flagging is already covered by
    ``_validate_required_files_present``, and an undeclared on-disk file
    is caught by ``_validate_no_unknown_files``.
    """
    if not _schema_has_any_dimensions(schema):
        return []
    declared_files = set(schema.get("files", []))
    violations: list[str] = []
    expected = (
        "timestamp",
        "component",
        *_INSTANCE_DIMENSION_COLUMNS,
        "metric",
        "value",
    )
    for filename in ("gauges.csv", _COMBINE_OUTPUT_FILENAME):
        if filename not in declared_files:
            continue
        path = output_dir / filename
        if not path.exists():
            continue  # covered by _validate_required_files_present
        with open(path, "r", encoding="utf-8", newline="") as f:
            reader = csv.reader(f)
            try:
                header = next(reader)
            except StopIteration:
                violations.append(
                    f"{filename}: file has no header row "
                    "(dim-aware long-form layout expected)"
                )
                continue
        if tuple(header) != expected:
            violations.append(
                f"{filename}: header {header} does not match dim-aware "
                f"long-form layout {list(expected)}"
            )
    return violations

