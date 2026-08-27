"""OTLP/HTTP payload builders (JSON + protobuf) for the OTEL streamers.

Extracted verbatim from ``legacy.py`` (decomposition step 2; see
``.trellis/tasks/07-02-legacy-monolith-decomposition/design.md``).
``legacy.py`` re-imports every name so the historic ``legacy.<name>``
surface is unchanged; new code should import from here. The protobuf
variants import ``opentelemetry.proto`` lazily inside each function so
the JSON path works without the optional protobuf dependency installed.

**CLI-internal surface.** The protobuf builders raise ``SystemExit`` --
not ``ImportError`` -- when ``opentelemetry.proto`` is absent, because
this module is a CLI-internal surface rather than a supported
programmatic API. That is documented semantics, not a defect. See
``.trellis/spec/amc/backend/api-cli-server.md`` § Library-API Error
Posture.
"""

from __future__ import annotations

from hashlib import sha1

from .timeutil import _to_unix_nanos


def _anomaly_event_id(entry: dict) -> str:
    """Deterministic event id used to correlate metrics, logs, and traces."""
    required = ("timestamp", "component", "metric", "description")
    missing = [k for k in required if not entry.get(k)]
    if missing:
        raise ValueError(f"anomaly entry missing required field(s): {', '.join(missing)}")
    payload = "|".join(str(entry[k]) for k in required)
    return "evt_" + sha1(payload.encode("utf-8")).hexdigest()[:16]


def _build_otlp_trace_payload(entry: dict) -> dict:
    """Build one OTLP/HTTP JSON ``resourceSpans`` payload from one anomaly event."""
    event_id = _anomaly_event_id(entry)
    component = entry["component"]
    metric = entry["metric"]
    timestamp = entry["timestamp"]
    attributes = [
        {"key": "event.id", "value": {"stringValue": event_id}},
        {"key": "signal.type", "value": {"stringValue": "metric_anomaly"}},
        {"key": "metric.name", "value": {"stringValue": metric}},
        {"key": "component", "value": {"stringValue": component}},
    ]

    ts_nano = _to_unix_nanos(timestamp)
    return {
        "resourceSpans": [{
            "resource": {
                "attributes": [
                    {"key": "service.name", "value": {"stringValue": component}},
                    {"key": "service.namespace", "value": {"stringValue": "anomaly-metric-creator"}},
                ]
            },
            "scopeSpans": [{
                "scope": {
                    "name": "anomaly-metric-creator",
                    "version": "1.0.0",
                },
                "spans": [{
                    "traceId": event_id[4:] * 2,
                    "spanId": event_id[4:20],
                    "name": f"anomaly:{metric}",
                    "kind": 1,  # SPAN_KIND_INTERNAL
                    "startTimeUnixNano": str(ts_nano),
                    "endTimeUnixNano": str(ts_nano + 1000000),  # 1ms duration
                    "attributes": attributes,
                    "status": {"code": 1},  # STATUS_CODE_OK
                }]
            }]
        }]
    }


def _build_otlp_trace_protobuf(entry: dict) -> bytes:
    """Build one OTLP protobuf ExportTraceServiceRequest payload."""
    try:
        from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
        from opentelemetry.proto.common.v1.common_pb2 import AnyValue, KeyValue
    except ImportError as exc:
        raise SystemExit(
            "OTLP protobuf mode requires opentelemetry-proto + protobuf. "
            "Install with: pip install opentelemetry-proto protobuf"
        ) from exc

    event_id = _anomaly_event_id(entry)
    component = entry["component"]
    metric = entry["metric"]
    timestamp = entry["timestamp"]
    attributes = [
        KeyValue(key="event.id", value=AnyValue(string_value=event_id)),
        KeyValue(key="signal.type", value=AnyValue(string_value="metric_anomaly")),
        KeyValue(key="metric.name", value=AnyValue(string_value=metric)),
        KeyValue(key="component", value=AnyValue(string_value=component)),
    ]

    req = ExportTraceServiceRequest()
    rspan = req.resource_spans.add()
    rspan.resource.attributes.extend([
        KeyValue(key="service.name", value=AnyValue(string_value=component)),
        KeyValue(key="service.namespace", value=AnyValue(string_value="anomaly-metric-creator")),
    ])
    sspan = rspan.scope_spans.add()
    sspan.scope.name = "anomaly-metric-creator"
    sspan.scope.version = "1.0.0"

    ts_nano = _to_unix_nanos(timestamp)
    span = sspan.spans.add()
    span.trace_id = bytes.fromhex(event_id[4:] * 2)
    span.span_id = bytes.fromhex(event_id[4:20])
    span.name = f"anomaly:{metric}"
    span.kind = 1
    span.start_time_unix_nano = ts_nano
    span.end_time_unix_nano = ts_nano + 1000000
    span.attributes.extend(attributes)
    span.status.code = 1
    return req.SerializeToString()


def _build_otlp_metric_payload(entry: dict) -> dict:
    """Build one OTLP/HTTP JSON ``resourceMetrics`` payload from one anomaly event.

    Phase 6: when the anomaly entry carries a ``dimensions`` dict
    (currently empty in v1; populated by Phase 4 ``instance_filter``), each
    non-empty key/value pair is emitted as a string attribute alongside the
    base four (``event.id``, ``signal.type``, ``metric.name``,
    ``component``). Empty-string and ``None`` values are skipped.
    """
    event_id = _anomaly_event_id(entry)
    component = entry["component"]
    metric = entry["metric"]
    timestamp = entry["timestamp"]
    # For metrics, we'll emit a counter increment for the anomaly
    attributes = [
        {"key": "event.id", "value": {"stringValue": event_id}},
        {"key": "signal.type", "value": {"stringValue": "metric_anomaly"}},
        {"key": "metric.name", "value": {"stringValue": metric}},
        {"key": "component", "value": {"stringValue": component}},
    ]
    for dim_key, dim_value in (entry.get("dimensions") or {}).items():
        if dim_value is None or dim_value == "":
            continue
        attributes.append({
            "key": dim_key,
            "value": {"stringValue": str(dim_value)},
        })
    ts_nano = _to_unix_nanos(timestamp)
    return {
        "resourceMetrics": [{
            "resource": {
                "attributes": [
                    {"key": "service.name", "value": {"stringValue": component}},
                    {"key": "service.namespace", "value": {"stringValue": "anomaly-metric-creator"}},
                ]
            },
            "scopeMetrics": [{
                "scope": {
                    "name": "anomaly-metric-creator",
                    "version": "1.0.0",
                },
                "metrics": [{
                    "name": "anomaly.count",
                    "description": "Counter of anomaly events",
                    "sum": {
                        "dataPoints": [{
                            "startTimeUnixNano": str(ts_nano),
                            "timeUnixNano": str(ts_nano),
                            "asInt": "1",
                            "attributes": attributes,
                        }],
                        "aggregationTemporality": 1, # DELTA
                        "isMonotonic": True,
                    }
                }]
            }]
        }]
    }


def _build_otlp_metric_protobuf(entry: dict) -> bytes:
    """Build one OTLP protobuf ExportMetricsServiceRequest payload.

    Mirrors the JSON builder's Phase 6 behavior on
    ``entry["dimensions"]``: non-empty values are emitted as string
    attributes; empty / ``None`` cells are dropped.
    """
    try:
        from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import ExportMetricsServiceRequest
        from opentelemetry.proto.common.v1.common_pb2 import AnyValue, KeyValue
    except ImportError as exc:
        raise SystemExit(
            "OTLP protobuf mode requires opentelemetry-proto + protobuf. "
            "Install with: pip install opentelemetry-proto protobuf"
        ) from exc

    event_id = _anomaly_event_id(entry)
    component = entry["component"]
    metric = entry["metric"]
    timestamp = entry["timestamp"]
    attributes = [
        KeyValue(key="event.id", value=AnyValue(string_value=event_id)),
        KeyValue(key="signal.type", value=AnyValue(string_value="metric_anomaly")),
        KeyValue(key="metric.name", value=AnyValue(string_value=metric)),
        KeyValue(key="component", value=AnyValue(string_value=component)),
    ]
    for dim_key, dim_value in (entry.get("dimensions") or {}).items():
        if dim_value is None or dim_value == "":
            continue
        attributes.append(KeyValue(
            key=dim_key,
            value=AnyValue(string_value=str(dim_value)),
        ))

    req = ExportMetricsServiceRequest()
    rmetric = req.resource_metrics.add()
    rmetric.resource.attributes.extend([
        KeyValue(key="service.name", value=AnyValue(string_value=component)),
        KeyValue(key="service.namespace", value=AnyValue(string_value="anomaly-metric-creator")),
    ])
    smetric = rmetric.scope_metrics.add()
    smetric.scope.name = "anomaly-metric-creator"
    smetric.scope.version = "1.0.0"

    ts_nano = _to_unix_nanos(timestamp)
    m = smetric.metrics.add()
    m.name = "anomaly.count"
    m.description = "Counter of anomaly events"
    m.sum.aggregation_temporality = 1
    m.sum.is_monotonic = True
    dp = m.sum.data_points.add()
    dp.start_time_unix_nano = ts_nano
    dp.time_unix_nano = ts_nano
    dp.as_int = 1
    dp.attributes.extend(attributes)
    return req.SerializeToString()


def _build_otlp_gauge_payload(batch: list[dict], *, metric_prefix: str = "") -> dict:
    """Build one OTLP/HTTP JSON ``resourceMetrics`` payload for a batch of per-row gauge values.

    Each ``batch`` entry is
    ``{"timestamp": str, "time_unix_nano": int, "component": str, "metric": str,
       "value": float, "dimensions": dict[str, str] (optional)}``.
    ``time_unix_nano`` is precomputed once per CSV row in ``stream_otel_gauges``
    so the builder does not re-parse the timestamp string per data point — the
    default config emits ~7,800 data points per batch, and per-data-point
    ``strptime`` was the dominant hotspot at high ``--otel-stream-speedup``.
    Entries are grouped first by ``component`` (one ``resourceMetrics`` entry
    per component) and then by ``metric`` (one ``metrics[]`` entry per metric
    within the component's scope), with one Gauge data point per row.
    ``dimensions`` (Phase 6) — when non-empty, each key/value pair is
    emitted as an additional string attribute alongside ``metric.name``,
    ``component``, and ``signal.type``. Empty-string and ``None`` values are
    skipped so the OTEL stream never carries empty-string attributes.
    """
    grouped: dict[str, dict[str, list[dict]]] = {}
    for entry in batch:
        comp = entry["component"]
        metric = entry["metric"]
        grouped.setdefault(comp, {}).setdefault(metric, []).append(entry)

    resource_metrics = []
    for component, metrics_map in grouped.items():
        metrics_list = []
        for metric_name, entries in metrics_map.items():
            data_points = []
            for entry in entries:
                attributes = [
                    {"key": "metric.name", "value": {"stringValue": metric_name}},
                    {"key": "component", "value": {"stringValue": component}},
                    {"key": "signal.type", "value": {"stringValue": "metric_value"}},
                ]
                for dim_key, dim_value in (entry.get("dimensions") or {}).items():
                    if dim_value is None or dim_value == "":
                        continue
                    attributes.append({
                        "key": dim_key,
                        "value": {"stringValue": str(dim_value)},
                    })
                data_points.append({
                    "timeUnixNano": str(entry["time_unix_nano"]),
                    "asDouble": float(entry["value"]),
                    "attributes": attributes,
                })
            metrics_list.append({
                "name": f"{metric_prefix}{metric_name}",
                "gauge": {"dataPoints": data_points},
            })
        resource_metrics.append({
            "resource": {
                "attributes": [
                    {"key": "service.name", "value": {"stringValue": component}},
                    {"key": "service.namespace", "value": {"stringValue": "anomaly-metric-creator"}},
                ]
            },
            "scopeMetrics": [{
                "scope": {
                    "name": "anomaly-metric-creator",
                    "version": "1.0.0",
                },
                "metrics": metrics_list,
            }],
        })
    return {"resourceMetrics": resource_metrics}


def _build_otlp_gauge_protobuf(batch: list[dict], *, metric_prefix: str = "") -> bytes:
    """Build one OTLP protobuf ExportMetricsServiceRequest carrying gauge data points.

    Same grouping as ``_build_otlp_gauge_payload``: one ``resource_metrics`` per
    component, one ``metrics`` entry per (component, metric) pair, with one Gauge
    data point per batch row for that metric. Mirrors the JSON builder's
    Phase 6 behavior: any non-empty ``dimensions`` key on a batch
    entry is emitted as a string attribute alongside ``metric.name``,
    ``component``, and ``signal.type``.
    """
    try:
        from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import ExportMetricsServiceRequest
        from opentelemetry.proto.common.v1.common_pb2 import AnyValue, KeyValue
    except ImportError as exc:
        raise SystemExit(
            "OTLP protobuf mode requires opentelemetry-proto + protobuf. "
            "Install with: pip install opentelemetry-proto protobuf"
        ) from exc

    grouped: dict[str, dict[str, list[dict]]] = {}
    for entry in batch:
        comp = entry["component"]
        metric = entry["metric"]
        grouped.setdefault(comp, {}).setdefault(metric, []).append(entry)

    req = ExportMetricsServiceRequest()
    for component, metrics_map in grouped.items():
        rmetric = req.resource_metrics.add()
        rmetric.resource.attributes.extend([
            KeyValue(key="service.name", value=AnyValue(string_value=component)),
            KeyValue(key="service.namespace", value=AnyValue(string_value="anomaly-metric-creator")),
        ])
        smetric = rmetric.scope_metrics.add()
        smetric.scope.name = "anomaly-metric-creator"
        smetric.scope.version = "1.0.0"
        for metric_name, entries in metrics_map.items():
            m = smetric.metrics.add()
            m.name = f"{metric_prefix}{metric_name}"
            for entry in entries:
                dp = m.gauge.data_points.add()
                dp.time_unix_nano = entry["time_unix_nano"]
                dp.as_double = float(entry["value"])
                dp.attributes.extend([
                    KeyValue(key="metric.name", value=AnyValue(string_value=metric_name)),
                    KeyValue(key="component", value=AnyValue(string_value=component)),
                    KeyValue(key="signal.type", value=AnyValue(string_value="metric_value")),
                ])
                for dim_key, dim_value in (entry.get("dimensions") or {}).items():
                    if dim_value is None or dim_value == "":
                        continue
                    dp.attributes.append(KeyValue(
                        key=dim_key,
                        value=AnyValue(string_value=str(dim_value)),
                    ))
    return req.SerializeToString()


def _build_otlp_log_payload(entry: dict) -> dict:
    """Build one OTLP/HTTP JSON ``resourceLogs`` payload from one anomaly event."""
    event_id = _anomaly_event_id(entry)
    component = entry["component"]
    metric = entry["metric"]
    timestamp = entry["timestamp"]
    description = entry["description"]
    return {
        "resourceLogs": [{
            "resource": {
                "attributes": [
                    {"key": "service.name", "value": {"stringValue": component}},
                    {"key": "service.namespace", "value": {"stringValue": "anomaly-metric-creator"}},
                ]
            },
            "scopeLogs": [{
                "scope": {
                    "name": "anomaly-metric-creator",
                    "version": "1.0.0",
                },
                "logRecords": [{
                    "timeUnixNano": str(_to_unix_nanos(timestamp)),
                    "severityText": "INFO",
                    "body": {"stringValue": description},
                    "attributes": [
                        {"key": "event.id", "value": {"stringValue": event_id}},
                        {"key": "signal.type", "value": {"stringValue": "metric_anomaly"}},
                        {"key": "metric.name", "value": {"stringValue": metric}},
                        {"key": "component", "value": {"stringValue": component}},
                    ],
                    "traceId": event_id[4:] * 2,
                    "spanId": event_id[4:20],
                }]
            }]
        }]
    }


def _build_otlp_log_protobuf(entry: dict) -> bytes:
    """Build one OTLP protobuf ExportLogsServiceRequest payload."""
    try:
        from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import ExportLogsServiceRequest
        from opentelemetry.proto.common.v1.common_pb2 import AnyValue, KeyValue
    except ImportError as exc:
        raise SystemExit(
            "OTLP protobuf mode requires opentelemetry-proto + protobuf. "
            "Install with: pip install opentelemetry-proto protobuf"
        ) from exc

    event_id = _anomaly_event_id(entry)
    component = entry["component"]
    metric = entry["metric"]
    timestamp = entry["timestamp"]
    description = entry["description"]

    req = ExportLogsServiceRequest()
    rlog = req.resource_logs.add()
    rlog.resource.attributes.extend([
        KeyValue(key="service.name", value=AnyValue(string_value=component)),
        KeyValue(key="service.namespace", value=AnyValue(string_value="anomaly-metric-creator")),
    ])
    slog = rlog.scope_logs.add()
    slog.scope.name = "anomaly-metric-creator"
    slog.scope.version = "1.0.0"

    record = slog.log_records.add()
    record.time_unix_nano = _to_unix_nanos(timestamp)
    record.severity_text = "INFO"
    record.body.CopyFrom(AnyValue(string_value=description))
    record.attributes.extend([
        KeyValue(key="event.id", value=AnyValue(string_value=event_id)),
        KeyValue(key="signal.type", value=AnyValue(string_value="metric_anomaly")),
        KeyValue(key="metric.name", value=AnyValue(string_value=metric)),
        KeyValue(key="component", value=AnyValue(string_value=component)),
    ])
    record.trace_id = bytes.fromhex(event_id[4:] * 2)
    record.span_id = bytes.fromhex(event_id[4:20])
    return req.SerializeToString()

