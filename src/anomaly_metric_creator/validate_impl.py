"""Output schema reader and artifact validator helpers.

Extracted from ``legacy.py`` (decomposition step 6). ``legacy.py`` configures
live topology access and re-imports every moved name so the historic
``legacy.<name>`` surface remains stable.
"""

from __future__ import annotations

import datetime
import csv
import json
import math
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .schema_impl import SCHEMA_DOCUMENT_VERSION
from .timeutil import _parse_csv_timestamp


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


from .validate_cells import (
    _validate_component_cells as _validate_component_cells,
    _validate_component_derivations as _validate_component_derivations,
    _validate_long_form_dimensions as _validate_long_form_dimensions,
)
from .validate_topology import (
    _TOPOLOGY_MIN_ALIGNED_ROWS as _TOPOLOGY_MIN_ALIGNED_ROWS,
    _compute_anomaly_keep_mask as _compute_anomaly_keep_mask,
    _filter_windows_for_pair as _filter_windows_for_pair_impl,
    _resolve_edge_correlation_threshold as _resolve_edge_correlation_threshold_impl,
    _validate_topology_coupling as _validate_topology_coupling_impl,
)
from .validate_topology_instances import (
    _validate_topology_coupling_per_instance as _validate_topology_coupling_per_instance_impl,
)


def _filter_windows_for_pair(
    windows: list[tuple[datetime.datetime, datetime.datetime, str, str]],
    source_component: str, source_metric: str,
    target_component: str, target_metric: str,
) -> list[tuple[datetime.datetime, datetime.datetime]]:
    return _filter_windows_for_pair_impl(
        windows,
        source_component,
        source_metric,
        target_component,
        target_metric,
        topology=_live_topology(),
        topology_load_metrics=_live_topology_load_metrics(),
    )


def _validate_topology_coupling(output_dir: Path, schema: dict) -> list[str]:
    return _validate_topology_coupling_impl(
        output_dir,
        schema,
        live_topology=_live_topology(),
        live_topology_load_metrics=_live_topology_load_metrics(),
    )


def _validate_topology_coupling_per_instance(
    output_dir: Path, schema: dict,
    source: str, target: str,
    source_canonical: str, target_canonical: str,
    threshold: float,
    anomaly_windows: list[tuple[datetime.datetime, datetime.datetime, str, str]],
    column_cache: dict | None = None,
) -> list[str]:
    return _validate_topology_coupling_per_instance_impl(
        output_dir,
        schema,
        source,
        target,
        source_canonical,
        target_canonical,
        threshold,
        pair_windows=_filter_windows_for_pair(
            anomaly_windows,
            source, source_canonical,
            target, target_canonical,
        ),
        compute_keep_mask=_compute_anomaly_keep_mask,
        min_aligned_rows=_TOPOLOGY_MIN_ALIGNED_ROWS,
        column_cache=column_cache,
    )


def _resolve_edge_correlation_threshold(source: str, target: str) -> float:
    return _resolve_edge_correlation_threshold_impl(
        source, target, topology=_live_topology()
    )


# ------------------------------------------------------------------
# Output validator (the `validate` subcommand)
# ------------------------------------------------------------------


@dataclass(frozen=True)
class Violation:
    """Structured validator violation whose string form is CLI-compatible."""

    component: str | None
    metric: str | None
    kind: str
    message: str

    @classmethod
    def from_message(cls, message: str) -> "Violation":
        row_match = re.match(
            r"(?P<file>(?P<component>[^/\s]+)\.csv)\s+line\s+\d+:\s+"
            r"(?P<metric>\S+?)=.*?\s+(?P<verb>is fractional|is negative|"
            r"below min_value|above max_value|not finite)",
            message,
        )
        if row_match:
            kind = {
                "is fractional": "fractional",
                "is negative": "negative_kind",
                "below min_value": "below_min",
                "above max_value": "above_max",
                "not finite": "non_finite",
            }[row_match.group("verb")]
            return cls(
                component=row_match.group("component"),
                metric=row_match.group("metric"),
                kind=kind,
                message=message,
            )

        if message.startswith("missing declared file: "):
            return cls(None, None, "missing_declared_file", message)
        if message.startswith("unknown file in output dir: "):
            return cls(None, None, "unknown_file", message)
        if message.startswith("topology coupling "):
            return cls(None, None, "topology_coupling", message)
        return cls(None, None, "validation", message)

    def __str__(self) -> str:
        return self.message

    def __repr__(self) -> str:
        return repr(self.message)

    def __contains__(self, needle: str) -> bool:
        return needle in self.message


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
        # Match generation pre-clean's tolerance for user/system sidecars such
        # as .DS_Store while still failing on undeclared artifact-like files.
        if path.name.startswith("."):
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


def validate_output(output_dir: Path) -> list[Violation]:
    """Run every validation against the artifacts in ``output_dir``.

    Returns structured violations whose string form exactly matches the
    historic prose messages (empty when the directory is fully consistent with
    its ``schema.json``). The CLI layer (the ``validate`` subcommand) prints
    the list, decides the exit code based on ``--warn``, and is the only caller
    that touches ``sys.exit``.
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
    return [Violation.from_message(message) for message in violations]
