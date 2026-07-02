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
from dataclasses import dataclass
from importlib import metadata as _importlib_metadata
from typing import Any, Callable

from .server_ops import RequestBodyTooLarge, _content_length

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
    try:
        return _importlib_metadata.version("anomaly-metric-creator")
    except _importlib_metadata.PackageNotFoundError:
        return "unknown"


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
    with csv_path.open(encoding="utf-8", newline="") as f:
        header = f.readline().rstrip("\n").split(",")
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
            if len(row) <= col:
                continue
            ms = _epoch_ms(parse_ts(row[0]))
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


def _tools_call(state: Any, req_id: Any, params: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    name = params.get("name")
    if not isinstance(name, str) or name not in _TOOLS_BY_NAME:
        return 200, _error_response(
            req_id, INVALID_PARAMS,
            f"unknown tool: {name!r}; available: "
            f"{', '.join(sorted(_TOOLS_BY_NAME))}",
        )
    arguments = params.get("arguments") or {}
    if not isinstance(arguments, dict):
        return 200, _error_response(
            req_id, INVALID_PARAMS, "'arguments' must be an object"
        )
    try:
        payload = _TOOLS_BY_NAME[name].handler(state, arguments)
    except McpToolError as exc:
        result: dict[str, Any] = {
            "content": [{"type": "text", "text": str(exc)}],
            "isError": True,
        }
    except Exception as exc:
        # A tool bug is a protocol-level internal error; keep the type and
        # message but never a traceback.
        return 200, _error_response(
            req_id, INTERNAL_ERROR, f"{type(exc).__name__}: {exc}"
        )
    else:
        result = {
            "content": [{"type": "text", "text": json.dumps(payload, sort_keys=True)}],
            "structuredContent": payload,
            "isError": False,
        }
    return 200, {"jsonrpc": "2.0", "id": req_id, "result": result}


def handle_mcp_http_post(state: Any, raw_body: bytes) -> tuple[int, dict[str, Any] | None]:
    """Answer one streamable-HTTP POST: ``(http_status, json_body | None)``.

    ``None`` bodies are 202 Accepted responses to notifications, which the
    streamable HTTP transport answers without content.
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
        return _tools_call(state, req_id, params)
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


def sse_not_supported_response() -> dict[str, Any]:
    return _error_response(
        None, INVALID_REQUEST,
        "this endpoint speaks streamable HTTP only: POST a JSON-RPC message "
        "to /mcp (legacy SSE GET is not supported)",
    )
