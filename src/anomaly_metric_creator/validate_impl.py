"""Output schema reader and artifact validator helpers.

Extracted from ``legacy.py`` (decomposition step 6). ``legacy.py`` configures
live topology access and re-imports every moved name so the historic
``legacy.<name>`` surface remains stable.
"""

from __future__ import annotations

import csv
import datetime
import json
import math
from collections.abc import Callable
from pathlib import Path

import numpy as np

from .combine_impl import _COMBINE_OUTPUT_FILENAME
from .csv_layout import _INSTANCE_DIMENSION_COLUMNS
from .schema_impl import SCHEMA_DOCUMENT_VERSION
from .timeutil import _parse_csv_timestamp

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

_get_topology: Callable[[], dict[str, list[object]]] | None = None
_get_topology_load_metrics: Callable[[], dict[str, tuple[str, tuple[str, ...]]]] | None = None


def _configure_validate_runtime(
    *,
    get_topology: Callable[[], dict[str, list[object]]],
    get_topology_load_metrics: Callable[[], dict[str, tuple[str, tuple[str, ...]]]],
) -> None:
    """Wire live registry access from ``legacy.py`` without importing it."""
    global _get_topology, _get_topology_load_metrics
    _get_topology = get_topology
    _get_topology_load_metrics = get_topology_load_metrics


def _live_topology() -> dict[str, list[object]]:
    if _get_topology is None:
        raise RuntimeError("validate_impl topology runtime is not configured")
    return _get_topology()


def _live_topology_load_metrics() -> dict[str, tuple[str, tuple[str, ...]]]:
    if _get_topology_load_metrics is None:
        raise RuntimeError("validate_impl topology-load runtime is not configured")
    return _get_topology_load_metrics()


# ------------------------------------------------------------------
# Output validator (the `validate` subcommand)
# ------------------------------------------------------------------
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


def _json_path(parent: str, child: str | int) -> str:
    """Return a compact dotted path for schema-shape diagnostics."""
    if isinstance(child, int):
        return f"{parent}[{child}]"
    if parent == "$":
        return f"$.{child}"
    return f"{parent}.{child}"


def _schema_shape_error(schema_path: Path, path: str, message: str) -> ValueError:
    return ValueError(f"{schema_path}: schema shape error at {path}: {message}")


def _require_schema_mapping(schema_path: Path, value, path: str) -> dict:
    if not isinstance(value, dict):
        raise _schema_shape_error(schema_path, path, "expected object")
    return value


def _require_schema_list(schema_path: Path, value, path: str) -> list:
    if not isinstance(value, list):
        raise _schema_shape_error(schema_path, path, "expected list")
    return value


def _require_schema_string(schema_path: Path, value, path: str) -> str:
    if not isinstance(value, str):
        raise _schema_shape_error(schema_path, path, "expected string")
    return value


def _require_schema_number(schema_path: Path, value, path: str) -> int | float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise _schema_shape_error(schema_path, path, "expected finite number")
    if not math.isfinite(value):
        raise _schema_shape_error(schema_path, path, "expected finite number")
    return value


def _validate_string_list_schema_shape(
    schema_path: Path, value, path: str
) -> list[str]:
    items = _require_schema_list(schema_path, value, path)
    for index, item in enumerate(items):
        _require_schema_string(schema_path, item, _json_path(path, index))
    return items


def _validate_schema_document_shape(schema_path: Path, document) -> dict:
    """Validate the structural contract consumed by ``validate DIR``.

    The downstream validators trust ``schema.json`` as their declarative input
    and intentionally index hot-path fields directly. Keep malformed or
    hand-edited schema documents from escaping as raw ``KeyError`` /
    ``TypeError`` tracebacks by checking the minimum shape at load time.
    """
    document = _require_schema_mapping(schema_path, document, "$")
    metadata = _require_schema_mapping(
        schema_path, document.get("metadata"), "$.metadata"
    )
    _validate_string_list_schema_shape(
        schema_path, metadata.get("components"), "$.metadata.components"
    )
    _validate_string_list_schema_shape(
        schema_path, metadata.get("emit_selection"), "$.metadata.emit_selection"
    )
    rows_per_component = _require_schema_number(
        schema_path, metadata.get("rows_per_component"),
        "$.metadata.rows_per_component",
    )
    if not isinstance(rows_per_component, int) or rows_per_component <= 0:
        raise _schema_shape_error(
            schema_path, "$.metadata.rows_per_component",
            "expected positive integer",
        )
    interval_seconds = _require_schema_number(
        schema_path, metadata.get("interval_seconds"),
        "$.metadata.interval_seconds",
    )
    if interval_seconds <= 0:
        raise _schema_shape_error(
            schema_path, "$.metadata.interval_seconds",
            "expected positive number",
        )
    total_seconds = _require_schema_number(
        schema_path, metadata.get("total_seconds"), "$.metadata.total_seconds"
    )
    if total_seconds <= 0:
        raise _schema_shape_error(
            schema_path, "$.metadata.total_seconds", "expected positive number"
        )
    _require_schema_string(schema_path, metadata.get("start"), "$.metadata.start")
    drop_rate = _require_schema_number(
        schema_path, metadata.get("drop_rate"), "$.metadata.drop_rate"
    )
    if not 0 <= drop_rate <= 1:
        raise _schema_shape_error(
            schema_path, "$.metadata.drop_rate", "expected value in [0, 1]"
        )
    inject_dst_artifact_day = _require_schema_number(
        schema_path, metadata.get("inject_dst_artifact_day"),
        "$.metadata.inject_dst_artifact_day",
    )
    if not isinstance(inject_dst_artifact_day, int) or inject_dst_artifact_day < 0:
        raise _schema_shape_error(
            schema_path, "$.metadata.inject_dst_artifact_day",
            "expected non-negative integer",
        )

    _validate_string_list_schema_shape(schema_path, document.get("files"), "$.files")
    components_payload = _require_schema_mapping(
        schema_path, document.get("components"), "$.components"
    )

    for component in metadata["components"]:
        payload_path = _json_path("$.components", component)
        if component not in components_payload:
            raise _schema_shape_error(
                schema_path, payload_path,
                "component listed in metadata.components is missing",
            )
        payload = _require_schema_mapping(
            schema_path, components_payload[component], payload_path
        )
        _require_schema_string(
            schema_path, payload.get("csv_filename"),
            _json_path(payload_path, "csv_filename"),
        )
        metrics = _require_schema_list(
            schema_path, payload.get("metrics"), _json_path(payload_path, "metrics")
        )
        for index, metric in enumerate(metrics):
            metric_path = _json_path(_json_path(payload_path, "metrics"), index)
            metric = _require_schema_mapping(schema_path, metric, metric_path)
            _require_schema_string(
                schema_path, metric.get("name"), _json_path(metric_path, "name")
            )
            dtype = _require_schema_string(
                schema_path, metric.get("dtype"), _json_path(metric_path, "dtype")
            )
            if dtype not in {"float", "int"}:
                raise _schema_shape_error(
                    schema_path, _json_path(metric_path, "dtype"),
                    "expected 'float' or 'int'",
                )
            for bound in ("min_value", "max_value"):
                raw_bound = metric.get(bound)
                if raw_bound is not None:
                    _require_schema_number(
                        schema_path, raw_bound, _json_path(metric_path, bound)
                    )
            for string_field in ("unit", "semantic_type", "derivation"):
                raw_value = metric.get(string_field)
                if raw_value is not None:
                    _require_schema_string(
                        schema_path, raw_value, _json_path(metric_path, string_field)
                    )
        dimensions = payload.get("dimensions")
        if dimensions is not None:
            dimensions_path = _json_path(payload_path, "dimensions")
            dimensions = _require_schema_mapping(schema_path, dimensions, dimensions_path)
            cardinality = _require_schema_number(
                schema_path, dimensions.get("cardinality"),
                _json_path(dimensions_path, "cardinality"),
            )
            if not isinstance(cardinality, int) or cardinality <= 0:
                raise _schema_shape_error(
                    schema_path, _json_path(dimensions_path, "cardinality"),
                    "expected positive integer",
                )
            _validate_string_list_schema_shape(
                schema_path, dimensions.get("axes"),
                _json_path(dimensions_path, "axes"),
            )
    return document


def _load_schema_document(schema_path: Path) -> dict:
    """Load and version-check a ``schema.json`` document.

    Raises ``ValueError`` if the file is missing, malformed JSON, or written
    by a schema-document version this build cannot validate, or if required
    structural fields are missing/mistyped. The validator intentionally
    rejects unknown versions outright rather than silently skipping unfamiliar
    fields — a stale schema would produce false-positive or false-negative
    results.
    """
    if not schema_path.exists():
        raise ValueError(
            f"validate requires {schema_path}; "
            "regenerate the run with 'schema' included in --emit "
            "(e.g. --emit metrics,schema)"
        )
    try:
        document = json.loads(schema_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ValueError(f"{schema_path} is not valid JSON: {e}") from e
    document = _require_schema_mapping(schema_path, document, "$")
    version = document.get("schema_version")
    if version != SCHEMA_DOCUMENT_VERSION:
        raise ValueError(
            f"{schema_path} has schema_version={version!r}; "
            f"this build only validates schema_version="
            f"{SCHEMA_DOCUMENT_VERSION}"
        )
    return _validate_schema_document_shape(schema_path, document)


def _validate_required_files_present(output_dir: Path, schema: dict) -> list[str]:
    """Return one violation per declared file missing from ``output_dir``."""
    violations = []
    for filename in schema.get("files", []):
        if not (output_dir / filename).exists():
            violations.append(
                f"missing declared file: {filename!r} (listed in schema.files)"
            )
    return violations


def _validate_no_unknown_files(output_dir: Path, schema: dict) -> list[str]:
    """Return one violation per file in ``output_dir`` not declared in the schema.

    Mirrors ``_pre_clean_output_dir``'s registry intent: an unknown file is
    either stale debris the pre-clean missed, a foreign file in the wrong
    directory, or a new artifact someone forgot to register.
    """
    declared = set(schema.get("files", []))
    # The schema document itself is always allowed even if a stale schema
    # somehow omits its own filename — without this exemption the validator
    # would be unable to bootstrap.
    declared.add("schema.json")
    violations = []
    for path in sorted(output_dir.iterdir()):
        if not path.is_file():
            continue
        if path.name not in declared:
            violations.append(f"unknown file in output dir: {path.name!r}")
    return violations


def _validate_anomalies_sorted(output_dir: Path, schema: dict) -> list[str]:
    """Return one violation if ``anomalies.csv`` is not sorted by timestamp.

    ``main()`` sorts the manifest chronologically by ``(span_start, component,
    metric)`` before writing; the validator only requires the timestamp axis
    to be non-decreasing (the secondary keys break ties within the same
    timestamp and don't matter for downstream consumers)."""
    anomalies_path = output_dir / "anomalies.csv"
    if not anomalies_path.exists():
        return []
    violations = []
    with open(anomalies_path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        prev = None
        for i, row in enumerate(reader, start=1):
            ts = row.get("timestamp")
            if ts is None:
                violations.append(
                    f"anomalies.csv row {i} missing 'timestamp' column"
                )
                continue
            if prev is not None and ts < prev:
                violations.append(
                    f"anomalies.csv row {i}: timestamp {ts!r} precedes "
                    f"previous timestamp {prev!r}"
                )
            prev = ts
    return violations


def _validate_component_row_count(
    output_dir: Path, schema: dict, component: str
) -> list[str]:
    """Each component CSV may have at most ``rows_per_component`` data rows
    (plus the DST splice extras when applicable). Dropped rows are absent
    from the CSV entirely (``generate_component`` filters via ``keep_mask``
    before serialization), so under-emission is expected and not flagged
    unless it exceeds the configured ``drop_rate``'s plausible band.

    Phase 8: when the per-component schema declares
    ``dimensions``, each per-row generation is fanned out across
    ``cardinality`` instances (Phase 2 long-form CSV writer), so both
    bands are multiplied by ``cardinality`` to keep the expected and
    actual counts in the same units.
    """
    csv_filename = schema["components"][component]["csv_filename"]
    csv_path = output_dir / csv_filename
    if not csv_path.exists():
        return []  # covered by _validate_required_files_present

    metadata = schema["metadata"]
    base_rows = metadata["rows_per_component"]
    interval = metadata["interval_seconds"]
    dst_day = metadata.get("inject_dst_artifact_day", 0)
    drop_rate = metadata.get("drop_rate", 0.0) or 0.0
    # The DST splice duplicates the 02:00–02:59 wall-clock hour on one day,
    # adding 3,600 / interval extra rows to that day. Use floor division to
    # mirror the generator's row-count derivation.
    dst_extra = int(3600 // interval) if dst_day and dst_day > 0 else 0
    dimensions = schema["components"][component].get("dimensions")
    cardinality = dimensions["cardinality"] if dimensions else 1
    expected_max = (base_rows + dst_extra) * cardinality

    with open(csv_path, "r", encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        try:
            next(reader)  # skip header
        except StopIteration:
            return [f"{csv_filename}: file has no header row"]
        data = sum(1 for row in reader if row)

    violations = []
    if data > expected_max:
        violations.append(
            f"{csv_filename}: data row count {data} exceeds expected max "
            f"{expected_max} (rows_per_component={base_rows} "
            f"+ DST splice {dst_extra}, cardinality={cardinality})"
        )
    # Under-emission lower bound: with drop_rate p and N rows the surviving
    # count per instance is N*(1-p) with std sqrt(N*p*(1-p)). ``generate_component``
    # draws a single ``drop_mask`` and reuses the same ``keep_mask`` for every
    # instance's row block, so row drops are *perfectly correlated* across
    # instances — the total surviving-row count is cardinality times the
    # per-instance count, and the std scales linearly (not by sqrt) on
    # ``cardinality``. Allow a generous 8-sigma band on top of that so a tiny
    # N doesn't trigger a false positive (e.g. a 144-row 600s smoke run).
    if drop_rate < 1.0:
        if base_rows > 0:
            per_instance_std = math.sqrt(base_rows * drop_rate * (1.0 - drop_rate))
            std = per_instance_std * cardinality
            lower = int(base_rows * cardinality * (1.0 - drop_rate) - 8.0 * std)
            if lower < 0:
                lower = 0
        else:
            lower = 0
        if data < lower:
            violations.append(
                f"{csv_filename}: data row count {data} is below the "
                f"expected lower bound {lower} for drop_rate={drop_rate}, "
                f"rows_per_component={base_rows}, cardinality={cardinality}"
            )
    return violations


def _validate_component_timestamp_coverage(
    output_dir: Path, schema: dict, component: str
) -> list[str]:
    """Every row's timestamp must fall in the expected window
    ``[START, START + total_seconds)``. DST duplicates are within range and
    intentionally not flagged here.
    """
    csv_filename = schema["components"][component]["csv_filename"]
    csv_path = output_dir / csv_filename
    if not csv_path.exists():
        return []

    metadata = schema["metadata"]
    start_dt = datetime.datetime.fromisoformat(metadata["start"])
    end_dt = start_dt + datetime.timedelta(seconds=metadata["total_seconds"])

    violations = []
    with open(csv_path, "r", encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        try:
            next(reader)
        except StopIteration:
            return violations
        for i, row in enumerate(reader, start=2):  # data rows start at line 2
            if not row:
                continue
            try:
                ts = _parse_csv_timestamp(row[0])
            except ValueError as e:
                violations.append(
                    f"{csv_filename} line {i}: bad timestamp {row[0]!r}: {e}"
                )
                continue
            if ts < start_dt:
                violations.append(
                    f"{csv_filename} line {i}: timestamp {row[0]!r} precedes "
                    f"START {metadata['start']!r}"
                )
                return violations  # one is enough; don't flood output
            if ts >= end_dt:
                violations.append(
                    f"{csv_filename} line {i}: timestamp {row[0]!r} is at or "
                    f"after end {(end_dt.isoformat())!r}"
                )
                return violations
    return violations


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
    ``_apply_anomaly_exclusion`` can filter windows down to those that
    actually touch the columns being correlated. Excluding *every*
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
    for upstream, edges in _live_topology().items():
        if upstream == source_component:
            continue
        if not any(edge.target == target_component for edge in edges):
            continue
        ups_entry = _live_topology_load_metrics().get(upstream)
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


def _apply_anomaly_exclusion(
    timestamps: list[datetime.datetime],
    values: np.ndarray,
    windows: list[tuple[datetime.datetime, datetime.datetime]],
) -> np.ndarray:
    """Drop rows whose timestamp falls in any anomaly exclusion window.

    Thin wrapper around ``_compute_anomaly_keep_mask`` retained as the
    single-array entry point; callers that need to filter two arrays
    aligned on the same timestamps (e.g. source/target in
    ``_validate_topology_coupling``) should compute the mask once via
    ``_compute_anomaly_keep_mask`` and index both arrays directly.
    """
    if not windows:
        return values
    keep = _compute_anomaly_keep_mask(timestamps, windows)
    return values[keep]


def _validate_topology_coupling(
    output_dir: Path, schema: dict,
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
        source_entry = _live_topology_load_metrics().get(source)
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
            target_entry = _live_topology_load_metrics().get(target)
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
            if len(common_ts) < 100:
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
                    source, target
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
                    source, target
                )
            else:
                threshold = float(raw_threshold)

            source_arr = np.array(source_aligned, dtype=np.float64)
            target_arr = np.array(target_aligned, dtype=np.float64)
            pair_windows = _filter_windows_for_pair(
                anomaly_windows,
                source, source_canonical,
                target, target_canonical,
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
            if len(source_kept) < 100:
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
                threshold, anomaly_windows,
                column_cache=per_instance_column_cache,
            )
    return violations


def _validate_topology_coupling_per_instance(
    output_dir: Path, schema: dict,
    source: str, target: str,
    source_canonical: str, target_canonical: str,
    threshold: float,
    anomaly_windows: list[tuple[datetime.datetime, datetime.datetime, str, str]],
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
    - Either side has fewer than 100 aligned rows for the matching
      instance.
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
    # Loop-invariant: the window filter depends only on the edge's
    # (component, metric) pairs, not on the pod pair — hoisted out of
    # the per-pod loop.
    pair_windows = _filter_windows_for_pair(
        anomaly_windows,
        source, source_canonical,
        target, target_canonical,
    )
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

        if len(common_ts) < 100:
            continue
        source_arr = np.array(source_aligned, dtype=np.float64)
        target_arr = np.array(target_aligned, dtype=np.float64)
        if pair_windows:
            keep_mask = _compute_anomaly_keep_mask(common_ts, pair_windows)
            source_kept = source_arr[keep_mask]
            target_kept = target_arr[keep_mask]
        else:
            source_kept = source_arr
            target_kept = target_arr
        if len(source_kept) < 100:
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


def _resolve_edge_correlation_threshold(source: str, target: str) -> float:
    """Look up the per-edge ``correlation_threshold`` from live ``TOPOLOGY``.

    Falls back to ``_TOPOLOGY_DEFAULT_CORRELATION_THRESHOLD`` when the
    edge is not declared in the live module (e.g. the schema was written
    by a build that declared an edge the current build no longer ships)
    or when ``Edge.correlation_threshold`` is ``None``.
    """
    for edge in _live_topology().get(source, ()):
        if edge.target == target:
            if edge.correlation_threshold is not None:
                return float(edge.correlation_threshold)
            break
    return _TOPOLOGY_DEFAULT_CORRELATION_THRESHOLD


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


def validate_output(output_dir: Path) -> list[str]:
    """Run every validation against the artifacts in ``output_dir``.

    Returns the list of violation messages (empty when the directory is
    fully consistent with its ``schema.json``). The CLI layer
    (the ``validate`` subcommand) prints the list, decides the exit code based on
    ``--warn``, and is the only caller that touches ``sys.exit``.
    """
    schema_path = output_dir / "schema.json"
    schema = _load_schema_document(schema_path)
    violations: list[str] = []
    violations += _validate_required_files_present(output_dir, schema)
    violations += _validate_no_unknown_files(output_dir, schema)
    if "metrics" in schema["metadata"].get("emit_selection", []):
        violations += _validate_anomalies_sorted(output_dir, schema)
    for component in schema["metadata"]["components"]:
        violations += _validate_component_row_count(output_dir, schema, component)
        violations += _validate_component_timestamp_coverage(
            output_dir, schema, component
        )
        violations += _validate_component_cells(output_dir, schema, component)
        violations += _validate_component_derivations(
            output_dir, schema, component
        )
    violations += _validate_long_form_dimensions(output_dir, schema)
    violations += _validate_topology_coupling(output_dir, schema)
    return violations
