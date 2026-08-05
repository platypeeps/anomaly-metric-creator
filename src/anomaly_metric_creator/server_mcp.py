"""MCP (Model Context Protocol) facade over the simulation server.

Stateless streamable-HTTP JSON-RPC layer plus the read-only tool registry
served at ``POST /mcp`` by ``server.py``. Every POST is a self-contained
JSON-RPC 2.0 message answered with plain JSON (no server-initiated SSE
stream; ``GET /mcp`` is refused with 405 like the reference mock). The
module owns protocol dispatch and the tool registry; ``server.py`` stays
the HTTP facade and only routes the request body here.

Design contracts (binding, from the mcp-server-facade umbrella task):

- **Single source of truth** — every tool answers from artifacts or
  registries the run already produces (``SimulationClock``, per-component
  CSVs in ``state.output_dir``, ``COMPONENTS`` via
  ``_resolve_effective_specs``, ``TOPOLOGY`` via ``_serialize_topology``).
  No tool computes state a second way.
- **Ground-truth wall** — no tool reads ``anomalies.csv`` or the
  ``SCENARIOS`` registry. The MCP surface exposes observable telemetry and
  structure only; the anomaly manifest is the eval harness's scoring
  rubric and must stay invisible to an agent under test.
- **Stdlib only** — matches the rest of server mode.
"""

from __future__ import annotations

import datetime as _dt
import json
import math
import re
import time
from dataclasses import dataclass
from typing import Any, Callable

from .server_ops import (
    _SNAPSHOT_KINDS,
    ParsedCommand,
    RequestBodyTooLarge,
    _content_length,
    _format_dt,
    _normalize_kind,
    _preview,
    _record_server_error,
    _redact_parsed_flags,
    _snapshot_row_namespace,
    command_fingerprint,
    parse_command,
    render_command,
    resource_snapshot,
)
from .server_traces import CommandTrace
from .version import package_version

# Latest protocol revision this facade implements; requested versions we
# recognize are echoed back per the MCP spec, anything else falls back to
# the default and the client decides whether to disconnect.
MCP_PROTOCOL_VERSION = "2025-06-18"
SUPPORTED_PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")

SERVER_NAME = "anomaly-metric-creator"
SERVER_INSTRUCTIONS = (
    "Synthetic observability simulation server. Tools expose the generated "
    "telemetry (components, topology, metric histograms) and the simulated "
    "clock for incident investigation exercises."
)

# JSON-RPC 2.0 error codes.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

# Histogram bucket sizes the auto-granularity selector walks, smallest
# first; the first rung that keeps the window at or under
# _TARGET_MAX_BUCKETS wins, and the coarsest rung is used for windows
# larger than every rung. Caller-supplied granularities are capped at
# _MAX_BUCKETS buckets instead.
GRANULARITY_LADDER_MS = (
    30_000,      # 30s
    60_000,      # 1m
    300_000,     # 5m
    900_000,     # 15m
    1_800_000,   # 30m
    3_600_000,   # 1h
    10_800_000,  # 3h
    21_600_000,  # 6h
    43_200_000,  # 12h
    86_400_000,  # 1d
)
_TARGET_MAX_BUCKETS = 120
_MAX_BUCKETS = 2_000

_EPOCH_UTC = _dt.datetime(1970, 1, 1, tzinfo=_dt.timezone.utc)


class McpToolError(ValueError):
    """Invalid tool arguments; rendered as an isError tool result."""


def _server_version() -> str:
    return package_version(fallback="unknown")


def _as_utc(dt: _dt.datetime) -> _dt.datetime:
    """Interpret naive datetimes (the simulation's convention) as UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=_dt.timezone.utc)
    return dt.astimezone(_dt.timezone.utc)


def _epoch_ms(dt: _dt.datetime) -> int:
    delta = _as_utc(dt) - _EPOCH_UTC
    return delta.days * 86_400_000 + delta.seconds * 1000 + delta.microseconds // 1000


def _rfc3339(dt: _dt.datetime) -> str:
    return _as_utc(dt).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _window_boundary_strings(from_ms: int, to_ms: int) -> tuple[str, str]:
    """Lexicographic CSV-timestamp bounds for a ``[from_ms, to_ms)`` window.

    The per-component CSV timestamp column is fixed-width
    ``%Y-%m-%d %H:%M:%S[.%f]`` and therefore sorts lexicographically in
    chronological order, so a cheap string comparison against
    ``row[0]`` can gate a row out *before* the (comparatively expensive)
    ``strptime`` parse. Both bounds are deliberately conservative
    supersets: ``lo`` floors ``from_ms`` to the whole second and ``hi``
    ceils ``to_ms`` to the whole second, so any row inside
    ``[from_ms, to_ms)`` always survives the string gate (a fractional or
    integer-second CSV timestamp that lands on the same whole second as a
    boundary compares ``>= lo`` and ``< hi``). The caller still applies the
    exact ``from_ms <= ms < to_ms`` range check to every gated-in row, so
    the output is identical to the parse-every-row path by construction —
    the gate only avoids parsing rows that would have been discarded
    anyway.
    """
    ceil_ms = -(-to_ms // 1000) * 1000  # smallest whole second >= to_ms
    try:
        lo_dt = _EPOCH_UTC + _dt.timedelta(milliseconds=from_ms)
        hi_dt = _EPOCH_UTC + _dt.timedelta(milliseconds=ceil_ms)
    except OverflowError:
        # An extreme (but type-valid) epoch-ms window overflows datetime /
        # timedelta arithmetic. That is an invalid argument, not an internal
        # tool bug, so surface it as a validated McpToolError (INVALID_PARAMS)
        # rather than letting the OverflowError read as INTERNAL_ERROR.
        raise McpToolError(
            "'from_ms'/'to_ms' are outside the representable time range"
        ) from None
    fmt = "%Y-%m-%d %H:%M:%S"
    return lo_dt.strftime(fmt), hi_dt.strftime(fmt)


def _layout_allows_break(state: Any, dim_cols: tuple) -> bool:
    """Whether a scan may ``break`` once ``row[0] >= hi`` boundary string.

    A file's rows are globally monotonic in time only on the
    dimensionless (wide) layout with no DST splice. The dim-aware
    long-form CSV is written as contiguous per-instance blocks, so its
    timestamps reset at each block boundary and an early break would skip
    later instances' in-window rows. ``--inject-dst-artifact-day`` also
    duplicates an hour into the wide layout, breaking monotonicity. In
    both cases only the parse-gate applies (no break), which is still a
    pure speedup with identical output.
    """
    if dim_cols:
        return False
    return getattr(state.args, "inject_dst_artifact_day", 0) == 0


# ------------------------------------------------------------------
# Tool argument helpers
# ------------------------------------------------------------------

def _require_str(arguments: dict[str, Any], key: str) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value:
        raise McpToolError(f"argument '{key}' must be a non-empty string")
    return value


def _require_epoch_ms(arguments: dict[str, Any], key: str) -> int:
    value = arguments.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise McpToolError(f"argument '{key}' must be epoch milliseconds")
    if isinstance(value, float) and not math.isfinite(value):
        raise McpToolError(f"argument '{key}' must be finite")
    return int(value)


def _active_components(state: Any) -> list[str]:
    """Active components in ``COMPONENTS`` declaration order."""
    active = set(state.components)
    return [name for name in state.legacy.COMPONENTS if name in active]


# ------------------------------------------------------------------
# Tools
# ------------------------------------------------------------------

def _tool_get_current_time(state: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    now = state.clock.now()
    return {
        "now": _rfc3339(now),
        "one_hour_ago": _rfc3339(now - _dt.timedelta(hours=1)),
        "one_day_ago": _rfc3339(now - _dt.timedelta(days=1)),
        "epoch_ms": _epoch_ms(now),
    }


def _tool_list_components(state: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    legacy = state.legacy
    effective = legacy._resolve_effective_specs(
        getattr(state.args, "metrics_per_component", None)
    )
    components = []
    for name in _active_components(state):
        components.append({
            "name": name,
            "metrics": [
                {
                    "name": spec.name,
                    "unit": spec.unit,
                    "semantic_type": spec.semantic_type,
                    "dtype": spec.dtype,
                    "min_value": spec.min_value,
                    "max_value": spec.max_value,
                    "derivation": spec.derivation,
                }
                for spec in effective[name]
            ],
        })
    return {"components": components}


def _tool_get_topology(state: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    return {"topology": state.legacy._serialize_topology(_active_components(state))}


def _resolve_granularity_ms(from_ms: int, to_ms: int, requested: Any) -> int:
    span = to_ms - from_ms
    if requested is None:
        for rung in GRANULARITY_LADDER_MS:
            if math.ceil(span / rung) <= _TARGET_MAX_BUCKETS:
                return rung
        return GRANULARITY_LADDER_MS[-1]
    if isinstance(requested, bool) or not isinstance(requested, int) or requested <= 0:
        raise McpToolError("argument 'granularity_ms' must be a positive integer")
    if math.ceil(span / requested) > _MAX_BUCKETS:
        raise McpToolError(
            f"granularity_ms {requested} yields more than {_MAX_BUCKETS} "
            "buckets for this window; use a coarser granularity"
        )
    return requested


def _tool_get_metric_histogram(state: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    component = _require_str(arguments, "component")
    metric = _require_str(arguments, "metric")
    from_ms = _require_epoch_ms(arguments, "from_ms")
    to_ms = _require_epoch_ms(arguments, "to_ms")
    if from_ms >= to_ms:
        raise McpToolError("'from_ms' must be strictly before 'to_ms'")

    active = _active_components(state)
    if component not in active:
        raise McpToolError(
            f"unknown or inactive component: {component}; "
            f"active components: {', '.join(active)}"
        )
    csv_path = state.output_dir / f"{component}.csv"
    if not csv_path.exists():
        raise McpToolError(
            f"no CSV artifact for component {component}; "
            "was 'metrics' dropped from --emit?"
        )

    granularity_ms = _resolve_granularity_ms(
        from_ms, to_ms, arguments.get("granularity_ms")
    )
    n_buckets = math.ceil((to_ms - from_ms) / granularity_ms)
    counts = [0] * n_buckets
    sums = [0.0] * n_buckets
    mins: list[float | None] = [None] * n_buckets
    maxs: list[float | None] = [None] * n_buckets

    parse_ts = state.legacy._parse_csv_timestamp
    lo_str, hi_str = _window_boundary_strings(from_ms, to_ms)
    with csv_path.open(encoding="utf-8", newline="") as f:
        header = f.readline().rstrip("\n").split(",")
        dim_cols, _metric_cols = state.legacy._classify_component_csv_header(header)
        allow_break = _layout_allows_break(state, dim_cols)
        try:
            col = header.index(metric)
        except ValueError:
            metric_columns = [
                name for name in header[1:]
                if name not in state.legacy._INSTANCE_DIMENSION_COLUMNS
            ]
            raise McpToolError(
                f"unknown metric for {component}: {metric}; "
                f"emitted metrics: {', '.join(metric_columns)}"
            ) from None
        for line in f:
            row = line.rstrip("\n").split(",")
            ts = row[0]
            # Cheap lexicographic gate before strptime: skip rows outside
            # the window's whole-second envelope; the exact range check
            # below still decides inclusion so output is unchanged.
            if ts < lo_str:
                continue
            if ts >= hi_str:
                if allow_break:
                    break
                continue
            if len(row) <= col:
                continue
            ms = _epoch_ms(parse_ts(ts))
            if not from_ms <= ms < to_ms:
                continue
            cell = row[col]
            if not cell:
                continue
            value = float(cell)
            idx = (ms - from_ms) // granularity_ms
            counts[idx] += 1
            sums[idx] += value
            current_min = mins[idx]
            if current_min is None or value < current_min:
                mins[idx] = value
            current_max = maxs[idx]
            if current_max is None or value > current_max:
                maxs[idx] = value

    buckets = []
    for idx in range(n_buckets):
        count = counts[idx]
        buckets.append({
            "start_ms": from_ms + idx * granularity_ms,
            "count": count,
            "mean": (sums[idx] / count) if count else None,
            "min": mins[idx],
            "max": maxs[idx],
        })
    return {
        "component": component,
        "metric": metric,
        "granularity_ms": granularity_ms,
        "buckets": buckets,
    }


# ------------------------------------------------------------------
# Analysis tools (phase 2)
# ------------------------------------------------------------------

# Bucket/event budgets. Every cap is surfaced as a `truncated` flag in the
# response — no silent truncation.
_GROUP_BY_DEFAULT_LIMIT = 50
_GROUP_BY_MAX_LIMIT = 500
_TIMELINE_PER_COMPONENT_CAP = 50
_TIMELINE_TOTAL_CAP = 200
_LOGS_DEFAULT_LIMIT = 100
_LOGS_MAX_LIMIT = 1000

_VALUE_AGGS = ("avg", "sum", "min", "max", "p95", "p99")

_LOG_TIMESTAMP_LEN = len("2026-03-01 00:00:00")


def _scan_active_csv_headers(state: Any) -> tuple[bool, dict[str, dict]]:
    """Header-scan the active components' CSVs via the shared writer helper."""
    paths = {
        name: state.output_dir / f"{name}.csv"
        for name in _active_components(state)
    }
    return state.legacy._scan_component_csv_headers(paths)


def _tool_list_metric_fields(state: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    any_dimensioned, _info = _scan_active_csv_headers(state)
    fields = [
        {"name": "component", "description": "Service component name."},
        {"name": "metric", "description": "Metric column name."},
    ]
    if any_dimensioned:
        fields.extend(
            {"name": column, "description": f"Instance dimension '{column}'."}
            for column in state.legacy._INSTANCE_DIMENSION_COLUMNS
        )
    return {"fields": fields, "dimensioned": any_dimensioned}


def _resolve_limit(arguments: dict[str, Any], key: str, default: int, cap: int) -> int:
    value = arguments.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise McpToolError(f"argument '{key}' must be a positive integer")
    return min(value, cap)


def _nearest_rank(sorted_values: list[float], percentile: float) -> float:
    """Nearest-rank percentile: deterministic across Python versions."""
    rank = max(1, math.ceil(percentile / 100.0 * len(sorted_values)))
    return sorted_values[rank - 1]


def _tool_group_metrics_by_field(state: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    field = _require_str(arguments, "field")
    from_ms = _require_epoch_ms(arguments, "from_ms")
    to_ms = _require_epoch_ms(arguments, "to_ms")
    if from_ms >= to_ms:
        raise McpToolError("'from_ms' must be strictly before 'to_ms'")
    agg = arguments.get("agg", "count")
    if agg != "count" and agg not in _VALUE_AGGS:
        raise McpToolError(
            f"unknown agg: {agg}; supported: count, {', '.join(_VALUE_AGGS)}"
        )
    metric_filter = arguments.get("metric")
    if agg != "count" and not isinstance(metric_filter, str):
        raise McpToolError(f"agg '{agg}' requires a 'metric' argument to aggregate")
    limit = _resolve_limit(
        arguments, "limit", _GROUP_BY_DEFAULT_LIMIT, _GROUP_BY_MAX_LIMIT
    )

    any_dimensioned, info = _scan_active_csv_headers(state)
    dim_columns = tuple(state.legacy._INSTANCE_DIMENSION_COLUMNS)
    valid_fields = ("component", "metric") + (dim_columns if any_dimensioned else ())
    if field not in valid_fields:
        raise McpToolError(
            f"unknown field: {field}; available fields: {', '.join(valid_fields)}"
        )

    parse_ts = state.legacy._parse_csv_timestamp
    lo_str, hi_str = _window_boundary_strings(from_ms, to_ms)
    counts: dict[str, int] = {}
    sums: dict[str, float] = {}
    mins: dict[str, float] = {}
    maxs: dict[str, float] = {}
    values: dict[str, list[float]] = {}
    wants_values = agg in ("p95", "p99")

    for component, entry in info.items():
        if not entry["exists"]:
            continue
        dim_cols = entry["dim_cols"]
        allow_break = _layout_allows_break(state, dim_cols)
        metric_cols = entry["metric_cols"]
        if metric_filter is not None and metric_filter not in metric_cols:
            continue
        offset = 1 + len(dim_cols)
        dim_index = (
            1 + dim_cols.index(field) if field in dim_cols else None
        )
        with entry["path"].open(encoding="utf-8", newline="") as f:
            f.readline()
            for line in f:
                row = line.rstrip("\n").split(",")
                ts = row[0]
                # Lexicographic pre-filter before strptime (see
                # _window_boundary_strings); exact range check follows.
                if ts < lo_str:
                    continue
                if ts >= hi_str:
                    if allow_break:
                        break
                    continue
                ms = _epoch_ms(parse_ts(ts))
                if not from_ms <= ms < to_ms:
                    continue
                for metric_idx, metric_name in enumerate(metric_cols):
                    if metric_filter is not None and metric_name != metric_filter:
                        continue
                    cell = row[offset + metric_idx] if len(row) > offset + metric_idx else ""
                    if not cell:
                        continue
                    if field == "component":
                        key = component
                    elif field == "metric":
                        key = metric_name
                    else:
                        key = row[dim_index] if dim_index is not None and len(row) > dim_index else ""
                        if not key:
                            continue
                    counts[key] = counts.get(key, 0) + 1
                    if agg != "count":
                        value = float(cell)
                        sums[key] = sums.get(key, 0.0) + value
                        if key not in mins or value < mins[key]:
                            mins[key] = value
                        if key not in maxs or value > maxs[key]:
                            maxs[key] = value
                        if wants_values:
                            values.setdefault(key, []).append(value)

    buckets = []
    for key in sorted(counts, key=lambda k: (-counts[k], k)):
        bucket: dict[str, Any] = {"value": key, "count": counts[key]}
        if agg == "avg":
            bucket["agg_value"] = sums[key] / counts[key]
        elif agg == "sum":
            bucket["agg_value"] = sums[key]
        elif agg == "min":
            bucket["agg_value"] = mins[key]
        elif agg == "max":
            bucket["agg_value"] = maxs[key]
        elif agg in ("p95", "p99"):
            bucket["agg_value"] = _nearest_rank(
                sorted(values[key]), float(agg[1:])
            )
        buckets.append(bucket)

    truncated = len(buckets) > limit
    return {
        "field": field,
        "agg": agg,
        "metric": metric_filter,
        "buckets": buckets[:limit],
        "limit": limit,
        "truncated": truncated,
    }


def _tool_get_correlated_timeline(state: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    """Cross-component timeline of metric excursions, computed from the CSVs.

    Excursions are cells whose z-score against the window's own statistics
    meets the sensitivity threshold. This is intentionally *detection over
    the observable data* — the tool must never read ``anomalies.csv`` (the
    eval ground truth); an agent is supposed to find the excursions the way
    an operator would.
    """
    from_ms = _require_epoch_ms(arguments, "from_ms")
    to_ms = _require_epoch_ms(arguments, "to_ms")
    if from_ms >= to_ms:
        raise McpToolError("'from_ms' must be strictly before 'to_ms'")
    sensitivity = arguments.get("sensitivity", 3.0)
    if isinstance(sensitivity, bool) or not isinstance(sensitivity, (int, float)):
        raise McpToolError("argument 'sensitivity' must be a number")
    sensitivity = float(sensitivity)
    if not math.isfinite(sensitivity) or not 0.5 <= sensitivity <= 10.0:
        raise McpToolError("argument 'sensitivity' must be in [0.5, 10]")

    active = _active_components(state)
    requested = arguments.get("components")
    if requested is not None:
        if (not isinstance(requested, list)
                or not all(isinstance(c, str) for c in requested)):
            raise McpToolError("argument 'components' must be a list of names")
        unknown = [c for c in requested if c not in active]
        if unknown:
            raise McpToolError(
                f"unknown or inactive components: {', '.join(unknown)}; "
                f"active: {', '.join(active)}"
            )
        active = [c for c in active if c in set(requested)]

    parse_ts = state.legacy._parse_csv_timestamp
    lo_str, hi_str = _window_boundary_strings(from_ms, to_ms)
    per_component: dict[str, dict[str, Any]] = {}
    all_events: list[dict[str, Any]] = []
    for component in active:
        path = state.output_dir / f"{component}.csv"
        if not path.exists():
            continue
        series: dict[str, list[tuple[int, float]]] = {}
        with path.open(encoding="utf-8", newline="") as f:
            header = f.readline().rstrip("\n").split(",")
            dim_cols, metric_cols = state.legacy._classify_component_csv_header(header)
            allow_break = _layout_allows_break(state, dim_cols)
            offset = 1 + len(dim_cols)
            for line in f:
                row = line.rstrip("\n").split(",")
                ts = row[0]
                # Lexicographic pre-filter before strptime (see
                # _window_boundary_strings); exact range check follows.
                if ts < lo_str:
                    continue
                if ts >= hi_str:
                    if allow_break:
                        break
                    continue
                ms = _epoch_ms(parse_ts(ts))
                if not from_ms <= ms < to_ms:
                    continue
                for metric_idx, metric_name in enumerate(metric_cols):
                    cell = row[offset + metric_idx] if len(row) > offset + metric_idx else ""
                    if cell:
                        series.setdefault(metric_name, []).append((ms, float(cell)))
        events: list[dict[str, Any]] = []
        for metric_name, points in series.items():
            n = len(points)
            if n < 3:
                continue
            mean = sum(v for _ms, v in points) / n
            variance = sum((v - mean) ** 2 for _ms, v in points) / n
            std = math.sqrt(variance)
            if std == 0.0:
                continue
            for ms, value in points:
                z = (value - mean) / std
                if abs(z) >= sensitivity:
                    events.append({
                        "timestamp_ms": ms,
                        "component": component,
                        "metric": metric_name,
                        "value": value,
                        "z_score": z,
                    })
        events.sort(key=lambda e: (e["timestamp_ms"], e["metric"]))
        truncated = len(events) > _TIMELINE_PER_COMPONENT_CAP
        kept = events[:_TIMELINE_PER_COMPONENT_CAP]
        per_component[component] = {"events": kept, "truncated": truncated}
        all_events.extend(kept)

    all_events.sort(
        key=lambda e: (e["timestamp_ms"], e["component"], e["metric"])
    )
    timeline_truncated = len(all_events) > _TIMELINE_TOTAL_CAP
    return {
        "sensitivity": sensitivity,
        "components": per_component,
        "timeline": all_events[:_TIMELINE_TOTAL_CAP],
        "timeline_truncated": timeline_truncated,
    }


_LOG_QUERY_KEYS = ("component", "metric", "level")


def _parse_log_query(query: Any) -> tuple[dict[str, str], list[str]]:
    """Parse the small log query grammar: ``key:value`` filters + substrings."""
    if query is None:
        return {}, []
    if not isinstance(query, str):
        raise McpToolError("argument 'query' must be a string")
    filters: dict[str, str] = {}
    substrings: list[str] = []
    for token in query.split():
        key, sep, value = token.partition(":")
        if sep and key in _LOG_QUERY_KEYS and value:
            filters[key] = value
        else:
            substrings.append(token)
    return filters, substrings


def _log_line_matches(line: str, filters: dict[str, str], substrings: list[str]) -> bool:
    for key, value in filters.items():
        if key == "level":
            parts = line.split(" ", 3)
            if len(parts) < 3 or parts[2] != value:
                return False
        elif f"{key}={value}" not in line:
            return False
    return all(term in line for term in substrings)


def _iter_log_lines_in_window(state: Any, from_ms: int, to_ms: int):
    log_path = state.output_dir / "metric_report.log"
    if not log_path.exists():
        return None
    parse_ts = state.legacy._parse_csv_timestamp

    def _iterator():
        with log_path.open(encoding="utf-8") as f:
            for raw in f:
                line = raw.rstrip("\n")
                stamp = line[:_LOG_TIMESTAMP_LEN]
                try:
                    ms = _epoch_ms(parse_ts(stamp))
                except ValueError:
                    continue
                if from_ms <= ms < to_ms:
                    yield line

    return _iterator()


_ABSENT_LOG_NOTE = (
    "metric_report.log was not emitted by this run (the 'logs' artifact "
    "was not selected); no log lines are available"
)

# In eval mode the report log is rubric-bearing: AMC's metric_report.log is
# a verbatim rendering of the anomaly manifest (same descriptions and
# event ids that anomalies.csv carries), so serving it would hand an agent
# under evaluation the scoring key. The log tools refuse rather than leak.
_EVAL_MODE_LOG_NOTE = (
    "log access is disabled in eval mode: this server's report log renders "
    "the ground-truth anomaly manifest, so it is withheld from the "
    "investigation surface"
)


def _eval_mode(state: Any) -> bool:
    return bool(getattr(state, "eval_mode", False))


def _tool_get_logs(state: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    from_ms = _require_epoch_ms(arguments, "from_ms")
    to_ms = _require_epoch_ms(arguments, "to_ms")
    if from_ms >= to_ms:
        raise McpToolError("'from_ms' must be strictly before 'to_ms'")
    limit = _resolve_limit(arguments, "limit", _LOGS_DEFAULT_LIMIT, _LOGS_MAX_LIMIT)
    filters, substrings = _parse_log_query(arguments.get("query"))

    if _eval_mode(state):
        return {"lines": [], "truncated": False, "note": _EVAL_MODE_LOG_NOTE}
    lines_iter = _iter_log_lines_in_window(state, from_ms, to_ms)
    if lines_iter is None:
        return {"lines": [], "truncated": False, "note": _ABSENT_LOG_NOTE}
    lines: list[str] = []
    truncated = False
    for line in lines_iter:
        if not _log_line_matches(line, filters, substrings):
            continue
        if len(lines) >= limit:
            truncated = True
            break
        lines.append(line)
    return {"lines": lines, "truncated": truncated}


# Variable parts stripped before clustering: the leading timestamp, the
# per-event correlation ids, and digit runs (latencies, percentages).
_LOG_EVENT_ID_RE = re.compile(r"event_id=\S+")
_LOG_DIGITS_RE = re.compile(r"\d+(?:\.\d+)?")


def _normalize_log_line(line: str) -> str:
    normalized = line[_LOG_TIMESTAMP_LEN:].strip()
    normalized = _LOG_EVENT_ID_RE.sub("event_id=*", normalized)
    return _LOG_DIGITS_RE.sub("#", normalized)


def _tool_deduplicate_logs(state: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    from_ms = _require_epoch_ms(arguments, "from_ms")
    to_ms = _require_epoch_ms(arguments, "to_ms")
    if from_ms >= to_ms:
        raise McpToolError("'from_ms' must be strictly before 'to_ms'")
    filters, substrings = _parse_log_query(arguments.get("query"))

    if _eval_mode(state):
        return {"clusters": [], "total_lines": 0, "note": _EVAL_MODE_LOG_NOTE}
    lines_iter = _iter_log_lines_in_window(state, from_ms, to_ms)
    if lines_iter is None:
        return {"clusters": [], "total_lines": 0, "note": _ABSENT_LOG_NOTE}
    representatives: dict[str, str] = {}
    cluster_counts: dict[str, int] = {}
    total = 0
    for line in lines_iter:
        if not _log_line_matches(line, filters, substrings):
            continue
        total += 1
        key = _normalize_log_line(line)
        cluster_counts[key] = cluster_counts.get(key, 0) + 1
        representatives.setdefault(key, line)
    clusters = [
        {"representative": representatives[key], "count": cluster_counts[key]}
        for key in sorted(
            cluster_counts, key=lambda k: (-cluster_counts[k], representatives[k])
        )
    ]
    return {"clusters": clusters, "total_lines": total}


# ------------------------------------------------------------------
# Ops tools (phase 3) — wrappers over the existing command simulator
# ------------------------------------------------------------------
# These dispatch through parse_command()/render_command() and the
# overlay-aware resource_snapshot(), never a second resource model, so the
# SimulationMutations overlay and scenario ops-profiles apply automatically
# and MCP output can never contradict what a real kubectl client sees.
# Ground-truth wall for ops output: "no more than kubectl shows" — the
# rendered strings are the operator-visible surface, nothing appended.

def _rendered_command_result(state: Any, argv: list[str]) -> dict[str, Any]:
    """Run one simulated CLI invocation and return its observable output."""
    parsed = parse_command(argv=argv, default_namespace=state.namespace)
    result = render_command(state, parsed)
    return {
        "command": " ".join(argv),
        "exit_code": result.exit_code,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def _optional_str(arguments: dict[str, Any], key: str) -> str | None:
    value = arguments.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise McpToolError(f"argument '{key}' must be a non-empty string")
    return value


def _tool_kubectl_get(state: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    kind = _require_str(arguments, "kind")
    name = _optional_str(arguments, "name")
    namespace = _optional_str(arguments, "namespace")
    normalized = _normalize_kind(kind)
    if normalized not in _SNAPSHOT_KINDS:
        raise McpToolError(
            f"unknown resource kind: {kind}; known kinds: "
            f"{', '.join(sorted(_SNAPSHOT_KINDS))}"
        )
    rows = resource_snapshot(state).get(normalized, [])
    if namespace is not None:
        rows = [r for r in rows if _snapshot_row_namespace(r) in (namespace, "")]
    if name is not None:
        rows = [r for r in rows if r.get("name") == name]
    return {"kind": normalized, "count": len(rows), "rows": rows}


def _tool_describe_resource(state: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    kind = _require_str(arguments, "kind")
    name = _require_str(arguments, "name")
    namespace = _optional_str(arguments, "namespace")
    argv = ["kubectl", "describe", kind, name]
    if namespace:
        argv += ["-n", namespace]
    return _rendered_command_result(state, argv)


def _tool_get_pod_logs(state: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    pod = _require_str(arguments, "pod")
    namespace = _optional_str(arguments, "namespace")
    container = _optional_str(arguments, "container")
    tail_lines = arguments.get("tail_lines")
    if tail_lines is not None and (
        isinstance(tail_lines, bool) or not isinstance(tail_lines, int) or tail_lines < 1
    ):
        raise McpToolError("argument 'tail_lines' must be a positive integer")
    argv = ["kubectl", "logs", pod]
    if namespace:
        argv += ["-n", namespace]
    if container:
        argv += ["-c", container]
    if tail_lines is not None:
        argv += [f"--tail={tail_lines}"]
    return _rendered_command_result(state, argv)


def _tool_get_events(state: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    namespace = _optional_str(arguments, "namespace")
    rows = resource_snapshot(state).get("events", [])
    if namespace is not None:
        rows = [r for r in rows if _snapshot_row_namespace(r) in (namespace, "")]
    return {"count": len(rows), "rows": rows}


def _tool_helm_status(state: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    release = _require_str(arguments, "release")
    return _rendered_command_result(state, ["helm", "status", release])


def _tool_helm_history(state: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    release = _require_str(arguments, "release")
    return _rendered_command_result(state, ["helm", "history", release])


# ------------------------------------------------------------------
# Tool registry
# ------------------------------------------------------------------

@dataclass(frozen=True)
class McpTool:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[[Any, dict[str, Any]], dict[str, Any]]


_EPOCH_MS_SCHEMA = {"type": "integer", "description": "Epoch milliseconds (UTC)."}

MCP_TOOLS: tuple[McpTool, ...] = (
    McpTool(
        name="get_current_time",
        description=(
            "Get the current simulated server time in RFC 3339 and epoch "
            "milliseconds. Use this to anchor time ranges for the metric "
            "analysis tools."
        ),
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        handler=_tool_get_current_time,
    ),
    McpTool(
        name="list_components",
        description=(
            "List the active service components and, per component, the "
            "emitted metrics with their units, semantic types, dtypes, and "
            "declared value ranges."
        ),
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        handler=_tool_list_components,
    ),
    McpTool(
        name="get_topology",
        description=(
            "Get the directed service-call topology between active "
            "components: per-source edge lists with weights and saturation "
            "parameters describing normal request flow."
        ),
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        handler=_tool_get_topology,
    ),
    McpTool(
        name="get_metric_histogram",
        description=(
            "Get a time-bucketed histogram (count, mean, min, max per "
            "bucket) for one component metric over a time range. Buckets "
            "auto-size toward at most 120 per call unless granularity_ms "
            "is given. Use get_current_time and list_components to pick "
            "ranges and metric names."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "component": {"type": "string", "description": "Component name."},
                "metric": {"type": "string", "description": "Metric column name."},
                "from_ms": _EPOCH_MS_SCHEMA,
                "to_ms": _EPOCH_MS_SCHEMA,
                "granularity_ms": {
                    "type": "integer",
                    "description": "Optional bucket width in milliseconds.",
                },
            },
            "required": ["component", "metric", "from_ms", "to_ms"],
            "additionalProperties": False,
        },
        handler=_tool_get_metric_histogram,
    ),
    McpTool(
        name="list_metric_fields",
        description=(
            "List the fields usable in group_metrics_by_field: always "
            "'component' and 'metric', plus the instance dimensions (id, "
            "host, pod, az, region, tenant) when this run emits "
            "per-instance telemetry."
        ),
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        handler=_tool_list_metric_fields,
    ),
    McpTool(
        name="group_metrics_by_field",
        description=(
            "Group emitted measurements by a field over a time range and "
            "return the distribution. Default agg 'count' counts emitted "
            "measurements per field value; aggs avg, sum, min, max, p95, "
            "p99 aggregate a named metric's values (pass 'metric'). "
            "Buckets sort by count descending; responses are capped by "
            "'limit' (default 50) with a 'truncated' flag."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "field": {
                    "type": "string",
                    "description": "Field to group by (see list_metric_fields).",
                },
                "from_ms": _EPOCH_MS_SCHEMA,
                "to_ms": _EPOCH_MS_SCHEMA,
                "metric": {
                    "type": "string",
                    "description": "Metric to aggregate (required for value aggs).",
                },
                "agg": {
                    "type": "string",
                    "enum": ["count", "avg", "sum", "min", "max", "p95", "p99"],
                    "description": "Aggregation; default count.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max buckets returned (default 50, max 500).",
                },
            },
            "required": ["field", "from_ms", "to_ms"],
            "additionalProperties": False,
        },
        handler=_tool_group_metrics_by_field,
    ),
    McpTool(
        name="get_correlated_timeline",
        description=(
            "Detect metric excursions (cells beyond a z-score sensitivity "
            "against the window's own statistics) per component, and "
            "return per-component event lists plus one interleaved "
            "cross-component timeline ordered by timestamp for root-cause "
            "analysis. Event budgets are capped with 'truncated' flags."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "from_ms": _EPOCH_MS_SCHEMA,
                "to_ms": _EPOCH_MS_SCHEMA,
                "components": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional subset of components to analyze.",
                },
                "sensitivity": {
                    "type": "number",
                    "description": "Z-score threshold in [0.5, 10]; default 3.",
                },
            },
            "required": ["from_ms", "to_ms"],
            "additionalProperties": False,
        },
        handler=_tool_get_correlated_timeline,
    ),
    McpTool(
        name="get_logs",
        description=(
            "Fetch report log lines in a time range. Query grammar (all "
            "terms AND-ed): bare substrings, plus component:NAME, "
            "metric:NAME, level:LEVEL filters. Results are capped by "
            "'limit' (default 100) with a 'truncated' flag."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "from_ms": _EPOCH_MS_SCHEMA,
                "to_ms": _EPOCH_MS_SCHEMA,
                "query": {
                    "type": "string",
                    "description": "Optional filter (substrings and key:value terms).",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max lines returned (default 100, max 1000).",
                },
            },
            "required": ["from_ms", "to_ms"],
            "additionalProperties": False,
        },
        handler=_tool_get_logs,
    ),
    McpTool(
        name="deduplicate_logs",
        description=(
            "Cluster report log lines in a time range by their shape "
            "(timestamps, correlation ids, and numbers stripped) and "
            "return one representative per cluster with a count, sorted "
            "by count descending. Accepts the same query grammar as "
            "get_logs."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "from_ms": _EPOCH_MS_SCHEMA,
                "to_ms": _EPOCH_MS_SCHEMA,
                "query": {
                    "type": "string",
                    "description": "Optional filter (substrings and key:value terms).",
                },
            },
            "required": ["from_ms", "to_ms"],
            "additionalProperties": False,
        },
        handler=_tool_deduplicate_logs,
    ),
    McpTool(
        name="kubectl_get",
        description=(
            "List simulated Kubernetes resources of a kind as structured "
            "rows (the data behind `kubectl get`), optionally filtered by "
            "name and namespace. Reflects live simulator mutations."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "kind": {"type": "string", "description": "Resource kind, e.g. pods, deployments."},
                "name": {"type": "string", "description": "Optional exact resource name."},
                "namespace": {"type": "string", "description": "Optional namespace filter."},
            },
            "required": ["kind"],
            "additionalProperties": False,
        },
        handler=_tool_kubectl_get,
    ),
    McpTool(
        name="describe_resource",
        description=(
            "Describe one simulated Kubernetes resource — the same text "
            "`kubectl describe` would print, including events."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "kind": {"type": "string", "description": "Resource kind."},
                "name": {"type": "string", "description": "Resource name."},
                "namespace": {"type": "string", "description": "Optional namespace."},
            },
            "required": ["kind", "name"],
            "additionalProperties": False,
        },
        handler=_tool_describe_resource,
    ),
    McpTool(
        name="get_pod_logs",
        description=(
            "Fetch a simulated pod's logs — the same text `kubectl logs` "
            "would print. Optional container and tail_lines."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "pod": {"type": "string", "description": "Pod name."},
                "namespace": {"type": "string", "description": "Optional namespace."},
                "container": {"type": "string", "description": "Optional container name."},
                "tail_lines": {"type": "integer", "description": "Optional line cap."},
            },
            "required": ["pod"],
            "additionalProperties": False,
        },
        handler=_tool_get_pod_logs,
    ),
    McpTool(
        name="get_events",
        description=(
            "List simulated cluster events as structured rows, optionally "
            "filtered by namespace. Includes events added by simulator "
            "mutations."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "namespace": {"type": "string", "description": "Optional namespace filter."},
            },
            "additionalProperties": False,
        },
        handler=_tool_get_events,
    ),
    McpTool(
        name="helm_status",
        description="Show a simulated Helm release's status (like `helm status RELEASE`).",
        input_schema={
            "type": "object",
            "properties": {
                "release": {"type": "string", "description": "Release name."},
            },
            "required": ["release"],
            "additionalProperties": False,
        },
        handler=_tool_helm_status,
    ),
    McpTool(
        name="helm_history",
        description="Show a simulated Helm release's revision history (like `helm history RELEASE`).",
        input_schema={
            "type": "object",
            "properties": {
                "release": {"type": "string", "description": "Release name."},
            },
            "required": ["release"],
            "additionalProperties": False,
        },
        handler=_tool_helm_history,
    ),
)


def _validate_mcp_tools(tools: tuple[McpTool, ...]) -> None:
    """Import-time registry validation, in the house fail-loud style."""
    seen: set[str] = set()
    for tool in tools:
        if not isinstance(tool.name, str) or not tool.name:
            raise ValueError(f"MCP tool with invalid name: {tool!r}")
        if tool.name in seen:
            raise ValueError(f"duplicate MCP tool name: {tool.name}")
        seen.add(tool.name)
        if not isinstance(tool.description, str) or not tool.description.strip():
            raise ValueError(f"MCP tool {tool.name} has an empty description")
        if not isinstance(tool.input_schema, dict) or tool.input_schema.get("type") != "object":
            raise ValueError(f"MCP tool {tool.name} inputSchema must be an object schema")
        if not callable(tool.handler):
            raise ValueError(f"MCP tool {tool.name} handler is not callable")


_validate_mcp_tools(MCP_TOOLS)

_TOOLS_BY_NAME = {tool.name: tool for tool in MCP_TOOLS}


# ------------------------------------------------------------------
# JSON-RPC dispatch
# ------------------------------------------------------------------

def _error_response(req_id: Any, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": code, "message": message},
    }


def _initialize_result(params: dict[str, Any]) -> dict[str, Any]:
    requested = params.get("protocolVersion")
    version = requested if requested in SUPPORTED_PROTOCOL_VERSIONS else MCP_PROTOCOL_VERSION
    return {
        "protocolVersion": version,
        "capabilities": {"tools": {}},
        "serverInfo": {"name": SERVER_NAME, "version": _server_version()},
        "instructions": SERVER_INSTRUCTIONS,
    }


def _tools_list_result() -> dict[str, Any]:
    return {
        "tools": [
            {
                "name": tool.name,
                "description": tool.description,
                "inputSchema": tool.input_schema,
            }
            for tool in sorted(MCP_TOOLS, key=lambda t: t.name)
        ]
    }


def _record_mcp_trace(
    state: Any,
    *,
    tool_name: str,
    arguments: Any,
    client: str,
    support_status: str,
    matched_rule_id: str,
    exit_code: int,
    stdout: str,
    stderr: str,
    latency_ms: float,
    request_id: str = "",
) -> None:
    """Record one MCP tools/call as a CommandTrace (family ``mcp``).

    Runs for every call — supported, tool-error, unknown-tool, and
    internal-error alike — so agent misfires accumulate in the same
    ``/v1/debug/unsupported`` backlog real kubectl misfires use. Arguments
    are redacted through the same helper that redacts kubectl flags before
    anything reaches memory, JSONL, SQLite, or the debug UI.
    """
    redacted_arguments = (
        _redact_parsed_flags(dict(arguments)) if isinstance(arguments, dict) else {}
    )
    parsed = ParsedCommand(
        raw_input=f"mcp tools/call {tool_name}",
        argv=("mcp", "tools/call", tool_name),
        family="mcp",
        verb=tool_name,
        resource_kind="",
        resource_name="",
        namespace="",
        flags=redacted_arguments,
        positionals=(),
    )
    trace = CommandTrace(
        id=state.traces.next_id(),
        received_at_wall_time=_dt.datetime.now(_dt.timezone.utc).isoformat(),
        simulated_time=_format_dt(state.clock.now()),
        raw_input=parsed.raw_input,
        argv=parsed.argv,
        client=client,
        command_family="mcp",
        verb=tool_name,
        resource_kind="",
        resource_name="",
        namespace="",
        parsed_flags=redacted_arguments,
        support_status=support_status,
        matched_rule_id=matched_rule_id,
        active_scenarios=state.active_scenarios,
        exit_code=exit_code,
        stdout_preview=_preview(stdout),
        stderr_preview=_preview(stderr),
        stdout=stdout,
        stderr=stderr,
        latency_ms=round(latency_ms, 3),
        fingerprint=command_fingerprint(parsed, support_status),
        guessed_intent=f"mcp tool call: {tool_name}" if tool_name else "mcp tool call",
        request_id=request_id,
    )
    state.traces.record(trace)


def _tools_call(
    state: Any, req_id: Any, params: dict[str, Any], *, client: str, request_id: str = ""
) -> tuple[int, dict[str, Any]]:
    started = time.perf_counter()
    name = params.get("name")
    tool_label = name if isinstance(name, str) else repr(name)
    if not isinstance(name, str) or name not in _TOOLS_BY_NAME:
        message = (
            f"unknown tool: {name!r}; available: "
            f"{', '.join(sorted(_TOOLS_BY_NAME))}"
        )
        _record_mcp_trace(
            state, tool_name=tool_label, arguments=params.get("arguments"),
            client=client, support_status="unsupported",
            matched_rule_id="mcp.unknown_tool", exit_code=1,
            stdout="", stderr=message,
            latency_ms=(time.perf_counter() - started) * 1000.0,
            request_id=request_id,
        )
        return 200, _error_response(req_id, INVALID_PARAMS, message)
    arguments = params.get("arguments") or {}
    if not isinstance(arguments, dict):
        message = "'arguments' must be an object"
        _record_mcp_trace(
            state, tool_name=name, arguments=None, client=client,
            support_status="unsupported", matched_rule_id="mcp.invalid_arguments",
            exit_code=1, stdout="", stderr=message,
            latency_ms=(time.perf_counter() - started) * 1000.0,
            request_id=request_id,
        )
        return 200, _error_response(req_id, INVALID_PARAMS, message)
    try:
        payload = _TOOLS_BY_NAME[name].handler(state, arguments)
    except McpToolError as exc:
        result: dict[str, Any] = {
            "content": [{"type": "text", "text": str(exc)}],
            "isError": True,
        }
        _record_mcp_trace(
            state, tool_name=name, arguments=arguments, client=client,
            support_status="supported", matched_rule_id=f"mcp.{name}",
            exit_code=1, stdout="", stderr=str(exc),
            latency_ms=(time.perf_counter() - started) * 1000.0,
            request_id=request_id,
        )
    except Exception as exc:
        # A tool bug is a protocol-level internal error. The client-facing
        # message keeps the type and message but never a traceback; the operator
        # sink (stderr or the structured log) gets the traceback tail via
        # _record_server_error so a tool crash is debuggable in the default
        # posture. request_logger is None unless serve_main wired one, in which
        # case the helper falls back to stderr.
        message = f"{type(exc).__name__}: {exc}"
        _record_server_error(
            getattr(state, "request_logger", None),
            where=f"mcp.{name}",
            exc=exc,
        )
        _record_mcp_trace(
            state, tool_name=name, arguments=arguments, client=client,
            support_status="partial", matched_rule_id=f"mcp.{name}",
            exit_code=1, stdout="", stderr=message,
            latency_ms=(time.perf_counter() - started) * 1000.0,
            request_id=request_id,
        )
        return 200, _error_response(req_id, INTERNAL_ERROR, message)
    else:
        serialized = json.dumps(payload, sort_keys=True)
        result = {
            "content": [{"type": "text", "text": serialized}],
            "structuredContent": payload,
            "isError": False,
        }
        _record_mcp_trace(
            state, tool_name=name, arguments=arguments, client=client,
            support_status="supported", matched_rule_id=f"mcp.{name}",
            exit_code=0, stdout=serialized, stderr="",
            latency_ms=(time.perf_counter() - started) * 1000.0,
            request_id=request_id,
        )
    return 200, {"jsonrpc": "2.0", "id": req_id, "result": result}


def handle_mcp_http_post(
    state: Any, raw_body: bytes, *, client: str = "mcp", request_id: str = ""
) -> tuple[int, dict[str, Any] | None]:
    """Answer one streamable-HTTP POST: ``(http_status, json_body | None)``.

    ``None`` bodies are 202 Accepted responses to notifications, which the
    streamable HTTP transport answers without content. ``client`` is the
    caller identity recorded on each tools/call CommandTrace; ``request_id`` is
    the per-HTTP-request join key (A-077) threaded onto that trace so it can be
    joined to the structured request/error record for the same request.
    """
    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return 200, _error_response(None, PARSE_ERROR, f"parse error: {exc}")
    if not isinstance(payload, dict):
        return 200, _error_response(
            None, INVALID_REQUEST,
            "expected a single JSON-RPC request object (batching is not "
            "supported by this transport)",
        )

    req_id = payload.get("id")
    if not isinstance(req_id, (str, int, type(None))):
        return 200, _error_response(None, INVALID_REQUEST, "invalid request id")
    method = payload.get("method")
    if payload.get("jsonrpc") != "2.0" or not isinstance(method, str):
        return 200, _error_response(
            req_id, INVALID_REQUEST, "not a JSON-RPC 2.0 request"
        )
    params = payload.get("params") or {}
    if not isinstance(params, dict):
        return 200, _error_response(req_id, INVALID_REQUEST, "'params' must be an object")

    if method.startswith("notifications/"):
        return 202, None
    if method == "initialize":
        result: dict[str, Any] = _initialize_result(params)
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = _tools_list_result()
    elif method == "tools/call":
        return _tools_call(state, req_id, params, client=client, request_id=request_id)
    else:
        return 200, _error_response(
            req_id, METHOD_NOT_FOUND, f"method not found: {method}"
        )
    if "id" not in payload:
        # A request-shaped message without an id is a notification; the
        # transport acknowledges it without a body.
        return 202, None
    return 200, {"jsonrpc": "2.0", "id": req_id, "result": result}


def read_mcp_request_body(handler: Any, max_bytes: int) -> bytes:
    """Read the raw POST body under the server's body cap.

    Raises ``RequestBodyTooLarge`` (the shared server exception) so the
    HTTP layer can answer with a JSON-RPC-shaped 413 via
    ``body_too_large_response``.
    """
    length = _content_length(handler)
    if length > max_bytes:
        raise RequestBodyTooLarge(
            f"request body is {length} bytes; limit is {max_bytes} bytes"
        )
    return handler.rfile.read(length) if length else b""


def body_too_large_response(message: str) -> dict[str, Any]:
    return _error_response(None, INVALID_REQUEST, message)


def rate_limited_response(message: str) -> dict[str, Any]:
    return _error_response(None, INVALID_REQUEST, message)


def sse_not_supported_response() -> dict[str, Any]:
    return _error_response(
        None, INVALID_REQUEST,
        "this endpoint speaks streamable HTTP only: POST a JSON-RPC message "
        "to /mcp (legacy SSE GET is not supported)",
    )
