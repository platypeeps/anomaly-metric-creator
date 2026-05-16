import datetime
import json
import pytest
from pathlib import Path

# Use the amc fixture from conftest.py
# The builder functions are internal (start with underscore), but we can test them via the amc module.

@pytest.fixture
def sample_entry():
    return {
        "timestamp": "2026-03-10 12:00:00",
        "component": "authservice",
        "metric": "error_rate",
        "description": "High error rate detected",
    }

def test_to_unix_nanos(amc):
    ts = "2026-03-10 12:00:00"
    expected = int(datetime.datetime(2026, 3, 10, 12, 0, 0, tzinfo=datetime.timezone.utc).timestamp() * 1_000_000_000)
    assert amc._to_unix_nanos(ts) == expected


def test_to_unix_nanos_accepts_millisecond_format(amc):
    """_to_unix_nanos must parse both ``HH:MM:SS`` and ``HH:MM:SS.SSS`` so
    the OTEL streaming path keeps working at --interval-seconds < 1.0."""
    ts_second = "2026-03-10 12:00:00"
    ts_millis = "2026-03-10 12:00:00.500"
    nanos_second = amc._to_unix_nanos(ts_second)
    nanos_millis = amc._to_unix_nanos(ts_millis)
    assert nanos_millis - nanos_second == 500_000_000


def test_parse_csv_timestamp_dispatches_both_formats(amc):
    """The shared _parse_csv_timestamp helper underpins both _to_unix_nanos
    (OTLP payload conversion) and the OTEL streaming-loop pacing parser.
    Both formats must round-trip to the expected naive datetime so the two
    consumers stay in lockstep when interval drops below 1s."""
    expected_second = datetime.datetime(2026, 3, 10, 12, 0, 0)
    expected_millis = datetime.datetime(2026, 3, 10, 12, 0, 0, 500_000)
    assert amc._parse_csv_timestamp("2026-03-10 12:00:00") == expected_second
    assert amc._parse_csv_timestamp("2026-03-10 12:00:00.500") == expected_millis


def test_parse_csv_timestamp_rejects_malformed_input(amc):
    """Garbled timestamp strings must surface a parse error rather than
    silently fall through to a wrong-format default."""
    with pytest.raises(ValueError):
        amc._parse_csv_timestamp("not a timestamp")
    with pytest.raises(ValueError):
        amc._parse_csv_timestamp("2026-03-10T12:00:00")  # ISO 'T' separator not accepted

def test_anomaly_event_id_determinism(amc, sample_entry):
    id1 = amc._anomaly_event_id(sample_entry)
    id2 = amc._anomaly_event_id(sample_entry)
    assert id1 == id2
    assert id1.startswith("evt_")
    assert len(id1) == 20  # evt_ + 16 hex chars

def test_anomaly_event_id_rejects_missing_fields(amc):
    bad_entry = {"timestamp": "2026-03-10 12:00:00"}
    with pytest.raises(ValueError, match="anomaly entry missing required field"):
        amc._anomaly_event_id(bad_entry)

# --- JSON Payloads ---

def test_build_otlp_trace_payload(amc, sample_entry):
    payload = amc._build_otlp_trace_payload(sample_entry)
    assert "resourceSpans" in payload
    rspan = payload["resourceSpans"][0]
    assert rspan["resource"]["attributes"][0]["value"]["stringValue"] == sample_entry["component"]
    
    span = rspan["scopeSpans"][0]["spans"][0]
    assert span["name"] == f"anomaly:{sample_entry['metric']}"
    assert span["startTimeUnixNano"] == str(amc._to_unix_nanos(sample_entry["timestamp"]))
    
    # Check attributes
    attrs = {attr["key"]: attr["value"]["stringValue"] for attr in span["attributes"]}
    assert attrs["component"] == sample_entry["component"]
    assert attrs["metric.name"] == sample_entry["metric"]

def test_build_otlp_metric_payload(amc, sample_entry):
    payload = amc._build_otlp_metric_payload(sample_entry)
    assert "resourceMetrics" in payload
    rmetric = payload["resourceMetrics"][0]
    
    metric = rmetric["scopeMetrics"][0]["metrics"][0]
    assert metric["name"] == "anomaly.count"
    dp = metric["sum"]["dataPoints"][0]
    assert dp["asInt"] == "1"
    
    attrs = {attr["key"]: attr["value"]["stringValue"] for attr in dp["attributes"]}
    assert attrs["component"] == sample_entry["component"]

def test_build_otlp_log_payload(amc, sample_entry):
    payload = amc._build_otlp_log_payload(sample_entry)
    assert "resourceLogs" in payload
    rlog = payload["resourceLogs"][0]
    
    record = rlog["scopeLogs"][0]["logRecords"][0]
    assert record["body"]["stringValue"] == sample_entry["description"]
    assert record["severityText"] == "INFO"
    
    attrs = {attr["key"]: attr["value"]["stringValue"] for attr in record["attributes"]}
    assert attrs["component"] == sample_entry["component"]

# --- Protobuf Payloads ---

def test_build_otlp_trace_protobuf(amc, sample_entry):
    pytest.importorskip("opentelemetry.proto")
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
    
    proto_bytes = amc._build_otlp_trace_protobuf(sample_entry)
    req = ExportTraceServiceRequest()
    req.ParseFromString(proto_bytes)
    
    assert req.resource_spans[0].resource.attributes[0].value.string_value == sample_entry["component"]
    span = req.resource_spans[0].scope_spans[0].spans[0]
    assert span.name == f"anomaly:{sample_entry['metric']}"
    assert span.start_time_unix_nano == amc._to_unix_nanos(sample_entry["timestamp"])

def test_build_otlp_metric_protobuf(amc, sample_entry):
    pytest.importorskip("opentelemetry.proto")
    from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import ExportMetricsServiceRequest
    
    proto_bytes = amc._build_otlp_metric_protobuf(sample_entry)
    req = ExportMetricsServiceRequest()
    req.ParseFromString(proto_bytes)
    
    metric = req.resource_metrics[0].scope_metrics[0].metrics[0]
    assert metric.name == "anomaly.count"
    assert metric.sum.data_points[0].as_int == 1

def test_build_otlp_log_protobuf(amc, sample_entry):
    pytest.importorskip("opentelemetry.proto")
    from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import ExportLogsServiceRequest
    
    proto_bytes = amc._build_otlp_log_protobuf(sample_entry)
    req = ExportLogsServiceRequest()
    req.ParseFromString(proto_bytes)
    
    record = req.resource_logs[0].scope_logs[0].log_records[0]
    assert record.body.string_value == sample_entry["description"]
    assert record.severity_text == "INFO"
