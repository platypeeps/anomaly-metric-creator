"""End-to-end and unit tests for the OTLP gauge stream.

Each test that exercises the streaming pipeline starts an ephemeral mock OTLP
endpoint on 127.0.0.1, points the CLI at it, and asserts on what the mock
collector observed. Builder shape tests run in-process and require no server.
"""

import csv as _csv
import filecmp
import json
import subprocess
import sys
import threading
import time
from collections import defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


from conftest import SCRIPT_PATH


def _invoke(*args, cwd=None, env=None):
    return subprocess.run(
        [sys.executable, str(SCRIPT_PATH), *args],
        capture_output=True,
        text=True,
        cwd=cwd,
        env=env,
    )


class _MockCollector(BaseHTTPRequestHandler):
    """Capture every POST to ``self.server.received`` as
    ``(path, content_type, raw_body)``. Always 200."""

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        ctype = self.headers.get("Content-Type", "")
        self.server.received.append((self.path, ctype, body))
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args, **kwargs):  # noqa: D401, ANN002, ANN003
        return


def _start_mock():
    """Return ``(server, thread, base_url)`` for a started mock collector."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _MockCollector)
    server.received = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, f"http://127.0.0.1:{server.server_port}"


def _stop_mock(server, thread):
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


# ------------------------------------------------------------------
# Builder shape tests (in-process)
# ------------------------------------------------------------------
def _entry(amc, ts, comp, metric, value):
    """Shape-correct gauge batch entry with the per-row nanos precomputed.

    Mirrors what ``stream_otel_gauges`` constructs: ``time_unix_nano`` is
    populated once per CSV row so the builders never re-parse the timestamp
    string per data point.
    """
    return {
        "timestamp": ts,
        "time_unix_nano": amc._to_unix_nanos(ts),
        "component": comp,
        "metric": metric,
        "value": value,
    }


def test_build_otlp_gauge_payload_groups_by_component_and_metric(amc):
    batch = [
        _entry(amc, "2026-03-10 00:00:00", "authservice", "cpu_util_pct", 12.5),
        _entry(amc, "2026-03-10 00:00:00", "authservice", "error_rate", 0.1),
        _entry(amc, "2026-03-10 00:00:01", "authservice", "cpu_util_pct", 13.0),
        _entry(amc, "2026-03-10 00:00:00", "cacheservice", "cpu_util_pct", 5.0),
    ]
    payload = amc._build_otlp_gauge_payload(batch)

    rms = {rm["resource"]["attributes"][0]["value"]["stringValue"]: rm
           for rm in payload["resourceMetrics"]}
    assert set(rms) == {"authservice", "cacheservice"}

    auth_metrics = {m["name"]: m for m in rms["authservice"]["scopeMetrics"][0]["metrics"]}
    assert set(auth_metrics) == {"cpu_util_pct", "error_rate"}
    assert len(auth_metrics["cpu_util_pct"]["gauge"]["dataPoints"]) == 2
    assert len(auth_metrics["error_rate"]["gauge"]["dataPoints"]) == 1

    cache_metrics = {m["name"]: m for m in rms["cacheservice"]["scopeMetrics"][0]["metrics"]}
    assert set(cache_metrics) == {"cpu_util_pct"}
    dp = cache_metrics["cpu_util_pct"]["gauge"]["dataPoints"][0]
    assert dp["asDouble"] == 5.0
    attrs = {a["key"]: a["value"]["stringValue"] for a in dp["attributes"]}
    assert attrs == {
        "metric.name": "cpu_util_pct",
        "component": "cacheservice",
        "signal.type": "metric_value",
    }


def test_build_otlp_gauge_payload_applies_metric_prefix(amc):
    batch = [_entry(amc, "2026-03-10 00:00:00", "authservice", "cpu_util_pct", 12.5)]
    payload = amc._build_otlp_gauge_payload(batch, metric_prefix="amc.")
    metrics = payload["resourceMetrics"][0]["scopeMetrics"][0]["metrics"]
    assert metrics[0]["name"] == "amc.cpu_util_pct"


def test_build_otlp_gauge_protobuf_round_trips(amc):
    from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import (
        ExportMetricsServiceRequest,
    )

    batch = [
        _entry(amc, "2026-03-10 00:00:00", "authservice", "cpu_util_pct", 12.5),
        _entry(amc, "2026-03-10 00:00:01", "authservice", "cpu_util_pct", 13.0),
        _entry(amc, "2026-03-10 00:00:00", "cacheservice", "hit_ratio", 87.5),
    ]
    raw = amc._build_otlp_gauge_protobuf(batch)
    req = ExportMetricsServiceRequest.FromString(raw)

    by_component = {}
    for rm in req.resource_metrics:
        comp = [a.value.string_value for a in rm.resource.attributes if a.key == "service.name"][0]
        metrics = {m.name: list(m.gauge.data_points) for sm in rm.scope_metrics for m in sm.metrics}
        by_component[comp] = metrics

    assert set(by_component) == {"authservice", "cacheservice"}
    auth_dps = by_component["authservice"]["cpu_util_pct"]
    assert [dp.as_double for dp in auth_dps] == [12.5, 13.0]
    cache_dps = by_component["cacheservice"]["hit_ratio"]
    assert len(cache_dps) == 1 and cache_dps[0].as_double == 87.5

    sample = auth_dps[0]
    attrs = {a.key: a.value.string_value for a in sample.attributes}
    assert attrs == {
        "metric.name": "cpu_util_pct",
        "component": "authservice",
        "signal.type": "metric_value",
    }


def test_build_otlp_gauge_json_and_protobuf_parity(amc):
    """The JSON and protobuf builders must agree on every data point in the batch
    (per-metric ordering may differ across builders, so compare as multisets)."""
    from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import (
        ExportMetricsServiceRequest,
    )
    batch = [
        _entry(amc, "2026-03-10 00:00:00", "authservice", "cpu_util_pct", 12.5),
        _entry(amc, "2026-03-10 00:00:01", "authservice", "error_rate", 0.1),
        _entry(amc, "2026-03-10 00:00:00", "cacheservice", "hit_ratio", 87.5),
    ]

    json_payload = amc._build_otlp_gauge_payload(batch)
    json_points = []
    for rm in json_payload["resourceMetrics"]:
        comp = rm["resource"]["attributes"][0]["value"]["stringValue"]
        for m in rm["scopeMetrics"][0]["metrics"]:
            for dp in m["gauge"]["dataPoints"]:
                json_points.append((comp, m["name"], dp["timeUnixNano"], dp["asDouble"]))

    proto_payload = ExportMetricsServiceRequest.FromString(
        amc._build_otlp_gauge_protobuf(batch)
    )
    proto_points = []
    for rm in proto_payload.resource_metrics:
        comp = [a.value.string_value for a in rm.resource.attributes
                if a.key == "service.name"][0]
        for sm in rm.scope_metrics:
            for m in sm.metrics:
                for dp in m.gauge.data_points:
                    proto_points.append((comp, m.name, str(dp.time_unix_nano), dp.as_double))

    assert sorted(json_points) == sorted(proto_points)


def test_build_otlp_gauge_payload_empty_batch(amc):
    """Empty batch produces an empty resourceMetrics list — no crash."""
    payload = amc._build_otlp_gauge_payload([])
    assert payload == {"resourceMetrics": []}


def test_build_otlp_gauge_builders_use_precomputed_nanos(amc):
    """Builders must read ``time_unix_nano`` from the entry directly
    and NOT re-parse ``timestamp``. Feed deliberately mismatched values and
    confirm the precomputed field wins for both JSON and protobuf paths.
    """
    from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import (
        ExportMetricsServiceRequest,
    )

    sentinel_nanos = 1_700_000_000_123_000_000
    batch = [{
        "timestamp": "2026-03-10 00:00:00",  # would parse to a different value
        "time_unix_nano": sentinel_nanos,
        "component": "authservice",
        "metric": "cpu_util_pct",
        "value": 12.5,
    }]

    json_payload = amc._build_otlp_gauge_payload(batch)
    json_dp = json_payload["resourceMetrics"][0]["scopeMetrics"][0]["metrics"][0]["gauge"]["dataPoints"][0]
    assert json_dp["timeUnixNano"] == str(sentinel_nanos)

    proto_req = ExportMetricsServiceRequest.FromString(amc._build_otlp_gauge_protobuf(batch))
    proto_dp = proto_req.resource_metrics[0].scope_metrics[0].metrics[0].gauge.data_points[0]
    assert proto_dp.time_unix_nano == sentinel_nanos


# ------------------------------------------------------------------
# Streaming end-to-end via subprocess
# ------------------------------------------------------------------
def _decode_gauge_requests(received, protocol="json"):
    """Filter ``received`` down to gauge requests (those whose body contains gauge
    data points, not Sum counters) and parse each into a dict shape:
        {component: {metric: [(time_unix_nano, value), ...]}}
    Returns a list of such dicts in the order requests were received."""
    out = []
    if protocol == "json":
        for path, ctype, body in received:
            try:
                payload = json.loads(body)
            except Exception:
                continue
            rm_list = payload.get("resourceMetrics") or []
            # Detect counters vs gauges: counters use "sum", gauges use "gauge".
            if not rm_list:
                continue
            has_gauge = any(
                "gauge" in m
                for rm in rm_list for sm in rm.get("scopeMetrics", [])
                for m in sm.get("metrics", [])
            )
            if not has_gauge:
                continue
            shaped = defaultdict(lambda: defaultdict(list))
            for rm in rm_list:
                comp = next(
                    (a["value"]["stringValue"] for a in rm["resource"]["attributes"]
                     if a["key"] == "service.name"),
                    None,
                )
                for sm in rm["scopeMetrics"]:
                    for m in sm["metrics"]:
                        if "gauge" not in m:
                            continue
                        for dp in m["gauge"]["dataPoints"]:
                            shaped[comp][m["name"]].append(
                                (int(dp["timeUnixNano"]), dp["asDouble"])
                            )
            out.append(shaped)
        return out
    # protobuf
    from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import (
        ExportMetricsServiceRequest,
    )
    for path, ctype, body in received:
        try:
            req = ExportMetricsServiceRequest.FromString(body)
        except Exception:
            continue
        has_gauge = any(
            m.HasField("gauge")
            for rm in req.resource_metrics for sm in rm.scope_metrics for m in sm.metrics
        )
        if not has_gauge:
            continue
        shaped = defaultdict(lambda: defaultdict(list))
        for rm in req.resource_metrics:
            comp = next((a.value.string_value for a in rm.resource.attributes
                         if a.key == "service.name"), None)
            for sm in rm.scope_metrics:
                for m in sm.metrics:
                    if not m.HasField("gauge"):
                        continue
                    for dp in m.gauge.data_points:
                        shaped[comp][m.name].append((dp.time_unix_nano, dp.as_double))
        out.append(shaped)
    return out


def test_stream_otel_gauges_off_by_default_no_gauge_requests(tmp_path):
    """Without --otel-emit-gauges the metrics endpoint must only receive the
    existing anomaly-counter stream — never a gauge data point."""
    server, thread, base = _start_mock()
    try:
        result = _invoke(
            "--duration-days", "1",
            "--interval-seconds", "600",
            "--components", "authservice,cacheservice",
            "--otel-enabled",
            "--otel-metrics-endpoint", f"{base}/v1/metrics",
            "--otel-stream-protocol", "json",
            "--otel-stream-speedup", "1000000",
            "--otel-stream-max-events", "5",
            "--output-dir", str(tmp_path / "no_gauges"),
        )
        assert result.returncode == 0, result.stderr
    finally:
        _stop_mock(server, thread)

    assert _decode_gauge_requests(server.received) == [], \
        "expected zero gauge requests when --otel-emit-gauges is off"
    # The counter stream did fire (each anomaly emits one /v1/metrics POST).
    assert any(r[0] == "/v1/metrics" for r in server.received)


def test_stream_otel_gauges_only_skips_anomaly_counter_requests(tmp_path):
    """--otel-gauges-only sends gauge batches without the anomaly counter stream."""
    server, thread, base = _start_mock()
    try:
        result = _invoke(
            "--duration-days", "1",
            "--interval-seconds", "3600",
            "--drop-rate", "0",
            "--components", "authservice",
            "--otel-enabled",
            "--otel-gauges-only",
            "--otel-metrics-endpoint", f"{base}/v1/metrics",
            "--otel-stream-protocol", "json",
            "--otel-stream-speedup", "1000000",
            "--otel-stream-max-events", "2",
            "--otel-gauge-batch-seconds", "43200",
            "--output-dir", str(tmp_path / "gauges_only"),
        )
        assert result.returncode == 0, result.stderr
    finally:
        _stop_mock(server, thread)

    gauge_requests = _decode_gauge_requests(server.received)
    assert gauge_requests, "expected gauge requests in --otel-gauges-only mode"
    assert len(gauge_requests) == len(server.received), (
        "expected every OTEL metrics POST to be a gauge payload; "
        f"saw {len(gauge_requests)} gauge request(s) out of "
        f"{len(server.received)} total request(s)"
    )
    assert "OTEL signal stream skipped (--otel-gauges-only)" in result.stdout


def test_stream_otel_gauges_batches_by_seconds(tmp_path):
    """At --interval-seconds=600 the run produces 144 rows per CSV; with
    --otel-gauge-batch-seconds=21600 (6h) each batch covers 36 rows and we
    expect exactly 4 batches (0..6h, 6..12h, 12..18h, 18..24h)."""
    server, thread, base = _start_mock()
    try:
        result = _invoke(
            "--duration-days", "1",
            "--interval-seconds", "600",
            "--drop-rate", "0",
            "--components", "authservice,cacheservice",
            "--otel-enabled",
            "--otel-emit-gauges",
            "--otel-metrics-endpoint", f"{base}/v1/metrics",
            "--otel-stream-protocol", "json",
            "--otel-stream-speedup", "1000000",
            "--otel-gauge-batch-seconds", "21600",
            "--output-dir", str(tmp_path / "batched"),
        )
        assert result.returncode == 0, result.stderr
    finally:
        _stop_mock(server, thread)

    gauge_reqs = _decode_gauge_requests(server.received)
    assert len(gauge_reqs) == 4, f"expected 4 gauge batches, got {len(gauge_reqs)}"
    # Each batch carries 36 rows × 2 components × DEFAULT_METRIC_COUNT data points.
    # Verify the per-component data-point totals are non-trivial and consistent.
    for batch in gauge_reqs:
        for comp in ("authservice", "cacheservice"):
            assert comp in batch, f"batch missing component {comp}"
            total_dps = sum(len(v) for v in batch[comp].values())
            assert total_dps > 0


def test_stream_otel_gauges_skips_dropped_rows(tmp_path):
    """Streamed gauge data-point count must equal kept_rows * metric_count, not
    total_rows * metric_count. We pre-compute the CSV's actual non-blank row
    count and assert the stream matches."""
    server, thread, base = _start_mock()
    out = tmp_path / "drops"
    try:
        result = _invoke(
            "--duration-days", "1",
            "--interval-seconds", "60",
            "--drop-rate", "0.5",
            "--seed", "123",
            "--components", "authservice",
            "--otel-enabled",
            "--otel-emit-gauges",
            "--otel-metrics-endpoint", f"{base}/v1/metrics",
            "--otel-stream-protocol", "json",
            "--otel-stream-speedup", "1000000",
            "--otel-gauge-batch-seconds", "86400",
            "--output-dir", str(out),
        )
        assert result.returncode == 0, result.stderr
    finally:
        _stop_mock(server, thread)

    # Count non-blank, non-header rows in the CSV.
    csv_path = out / "authservice.csv"
    with open(csv_path) as f:
        reader = _csv.reader(f)
        header = next(reader)
        kept_rows = sum(1 for row in reader if row)
    metric_count = len(header) - 1
    expected_dps = kept_rows * metric_count

    gauge_reqs = _decode_gauge_requests(server.received)
    total_dps = sum(len(v) for batch in gauge_reqs
                    for comp in batch.values() for v in comp.values())
    assert total_dps == expected_dps, (
        f"gauge data-point count {total_dps} did not equal kept_rows*metric_count "
        f"{expected_dps} (kept_rows={kept_rows}, metric_count={metric_count})"
    )


def test_stream_otel_gauges_json_and_protobuf_parity_e2e(tmp_path):
    """Two runs with identical inputs and only the protocol flag differing
    must produce semantically identical gauge data points."""
    def _run(protocol, out_subdir):
        server, thread, base = _start_mock()
        try:
            result = _invoke(
                "--duration-days", "1",
                "--interval-seconds", "1800",
                "--drop-rate", "0",
                "--seed", "7",
                "--components", "authservice",
                "--otel-enabled",
                "--otel-emit-gauges",
                "--otel-metrics-endpoint", f"{base}/v1/metrics",
                "--otel-stream-protocol", protocol,
                "--otel-stream-speedup", "1000000",
                "--otel-gauge-batch-seconds", "86400",
                "--output-dir", str(tmp_path / out_subdir),
            )
            assert result.returncode == 0, result.stderr
        finally:
            _stop_mock(server, thread)
        return _decode_gauge_requests(server.received, protocol=protocol)

    json_reqs = _run("json", "parity_json")
    proto_reqs = _run("protobuf", "parity_protobuf")

    def _flatten(reqs):
        out = []
        for batch in reqs:
            for comp, metrics in batch.items():
                for metric, dps in metrics.items():
                    for ts, val in dps:
                        out.append((comp, metric, ts, val))
        return sorted(out)

    assert _flatten(json_reqs) == _flatten(proto_reqs)


def test_stream_otel_gauges_respects_max_events_cap(tmp_path):
    """--otel-stream-max-events caps the gauge stream's request count
    (mirroring the counter stream's behavior)."""
    server, thread, base = _start_mock()
    try:
        result = _invoke(
            "--duration-days", "1",
            "--interval-seconds", "600",
            "--drop-rate", "0",
            "--components", "authservice,cacheservice",
            "--otel-enabled",
            "--otel-emit-gauges",
            "--otel-metrics-endpoint", f"{base}/v1/metrics",
            "--otel-stream-protocol", "json",
            "--otel-stream-speedup", "1000000",
            "--otel-stream-max-events", "2",
            "--otel-gauge-batch-seconds", "3600",
            "--output-dir", str(tmp_path / "capped"),
        )
        assert result.returncode == 0, result.stderr
    finally:
        _stop_mock(server, thread)

    gauge_reqs = _decode_gauge_requests(server.received)
    # max_events=2 caps gauges to 2 requests. The counter stream is also capped
    # to 2 events by the same flag.
    assert len(gauge_reqs) <= 2, f"expected gauge request count <= 2, got {len(gauge_reqs)}"


def test_stream_otel_gauges_activity_log_records_batches(tmp_path):
    """Activity log must record START/SEND/OK/END lines tagged signal=metrics_gauge."""
    server, thread, base = _start_mock()
    activity_log = tmp_path / "amc-activity.log"
    try:
        result = _invoke(
            "--duration-days", "1",
            "--interval-seconds", "600",
            "--drop-rate", "0",
            "--components", "authservice",
            "--otel-enabled",
            "--otel-emit-gauges",
            "--otel-metrics-endpoint", f"{base}/v1/metrics",
            "--otel-stream-protocol", "json",
            "--otel-stream-speedup", "1000000",
            "--otel-gauge-batch-seconds", "21600",
            "--otel-activity-log", str(activity_log),
            "--output-dir", str(tmp_path / "activity_log"),
        )
        assert result.returncode == 0, result.stderr
    finally:
        _stop_mock(server, thread)

    text = activity_log.read_text()
    # The streamer writes to the activity log in append mode after the counter
    # stream finishes; both signal=metrics_gauge START/SEND/OK/END records must
    # be present alongside the counter records.
    assert "signal=metrics_gauge" in text
    assert " START signal=metrics_gauge" in text
    assert " SEND signal=metrics_gauge" in text
    assert " OK signal=metrics_gauge" in text
    assert " END signal=metrics_gauge" in text


def test_stream_otel_gauges_metric_prefix_applied(tmp_path):
    """--otel-gauge-metric-prefix prepends the prefix to every metric name."""
    server, thread, base = _start_mock()
    try:
        result = _invoke(
            "--duration-days", "1",
            "--interval-seconds", "1800",
            "--drop-rate", "0",
            "--components", "authservice",
            "--otel-enabled",
            "--otel-emit-gauges",
            "--otel-gauge-metric-prefix", "amc.",
            "--otel-metrics-endpoint", f"{base}/v1/metrics",
            "--otel-stream-protocol", "json",
            "--otel-stream-speedup", "1000000",
            "--otel-gauge-batch-seconds", "86400",
            "--output-dir", str(tmp_path / "prefixed"),
        )
        assert result.returncode == 0, result.stderr
    finally:
        _stop_mock(server, thread)

    gauge_reqs = _decode_gauge_requests(server.received)
    assert gauge_reqs, "expected at least one gauge request"
    metric_names = {name for batch in gauge_reqs for comp in batch.values() for name in comp}
    assert metric_names, "expected at least one metric in the gauge stream"
    assert all(n.startswith("amc.") for n in metric_names), \
        f"metric names without prefix: {sorted(n for n in metric_names if not n.startswith('amc.'))}"


def test_stream_otel_gauges_does_not_change_csv_output(tmp_path):
    """With the gauge flag toggled on vs off, the per-component CSV bytes must
    be byte-identical for the same --seed (the gauge stream reads CSVs after
    they're written and must not perturb generation)."""
    without_dir = tmp_path / "without_gauges"
    with_dir = tmp_path / "with_gauges"

    # Run #1: flag off, no streaming at all (no need for a mock endpoint).
    result = _invoke(
        "--duration-days", "1",
        "--interval-seconds", "600",
        "--seed", "42",
        "--output-dir", str(without_dir),
    )
    assert result.returncode == 0, result.stderr

    # Run #2: flag on, against a live mock collector.
    server, thread, base = _start_mock()
    try:
        result = _invoke(
            "--duration-days", "1",
            "--interval-seconds", "600",
            "--seed", "42",
            "--otel-enabled",
            "--otel-emit-gauges",
            "--otel-metrics-endpoint", f"{base}/v1/metrics",
            "--otel-stream-protocol", "json",
            "--otel-stream-speedup", "1000000",
            "--otel-gauge-batch-seconds", "21600",
            "--output-dir", str(with_dir),
        )
        assert result.returncode == 0, result.stderr
    finally:
        _stop_mock(server, thread)

    for component_csv in sorted(without_dir.glob("*.csv")):
        rel = component_csv.name
        assert filecmp.cmp(component_csv, with_dir / rel, shallow=False), (
            f"{rel} bytes differ with --otel-emit-gauges on vs off"
        )


def test_stream_otel_gauges_with_protobuf_default(tmp_path):
    """When --otel-stream-protocol is omitted (default protobuf), gauge bodies
    are protobuf-encoded and decodable via ExportMetricsServiceRequest."""
    server, thread, base = _start_mock()
    try:
        result = _invoke(
            "--duration-days", "1",
            "--interval-seconds", "1800",
            "--drop-rate", "0",
            "--components", "authservice",
            "--otel-enabled",
            "--otel-emit-gauges",
            "--otel-metrics-endpoint", f"{base}/v1/metrics",
            "--otel-stream-speedup", "1000000",
            "--otel-gauge-batch-seconds", "86400",
            "--output-dir", str(tmp_path / "default_proto"),
        )
        assert result.returncode == 0, result.stderr
    finally:
        _stop_mock(server, thread)

    proto_reqs = _decode_gauge_requests(server.received, protocol="protobuf")
    assert proto_reqs, "expected at least one protobuf gauge request"
    # Each protobuf request advertised content-type=application/x-protobuf.
    proto_bodies = [r for r in server.received
                    if r[1] == "application/x-protobuf"]
    assert proto_bodies, "expected at least one application/x-protobuf body"


def test_stream_otel_gauges_with_auth_header(tmp_path, monkeypatch):
    """Auth token flows through to the gauge request's Authorization header."""
    captured_headers = []

    class _AuthHandler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            captured_headers.append({k: v for k, v in self.headers.items()})
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args, **kwargs):  # noqa: D401, ANN002, ANN003
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), _AuthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        result = _invoke(
            "--duration-days", "1",
            "--interval-seconds", "1800",
            "--drop-rate", "0",
            "--components", "authservice",
            "--otel-enabled",
            "--otel-emit-gauges",
            "--otel-metrics-endpoint", f"{base}/v1/metrics",
            "--otel-metrics-auth-token", "secret-gauge-token",
            "--otel-stream-protocol", "json",
            "--otel-stream-speedup", "1000000",
            "--otel-gauge-batch-seconds", "86400",
            "--output-dir", str(tmp_path / "auth"),
        )
        assert result.returncode == 0, result.stderr
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    # At least one request should carry the Authorization header.
    auth_headers = [h.get("Authorization") for h in captured_headers
                    if h.get("Authorization")]
    assert any("secret-gauge-token" in v for v in auth_headers), \
        f"no Authorization header carried the configured token; saw: {auth_headers}"


def test_stream_otel_gauges_max_events_caps_attempts_not_successes(amc, tmp_path):
    """``--otel-stream-max-events`` must cap attempted flushes, not
    successful ones — matching the counter stream, which pre-truncates its
    event list up-front so the same flag means the same thing in both
    streams. With a broken endpoint (every POST returns 500), the gauge
    cap of N must still trip at exactly N attempts even though zero
    succeed.
    """
    # Pre-generate CSVs (no streaming).
    out = tmp_path / "cap"
    out.mkdir()
    amc.main([
        "--duration-days", "1",
        "--interval-seconds", "600",
        "--drop-rate", "0",
        "--components", "authservice",
        "--output-dir", str(out),
    ])

    attempts = []

    class _BrokenCollector(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            attempts.append(self.path)
            self.send_response(500)
            self.end_headers()

        def log_message(self, *args, **kwargs):  # noqa: D401, ANN002, ANN003
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), _BrokenCollector)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        # max_retries=0 keeps the test fast: one HTTP attempt per flush.
        # max_events=3 caps attempted flushes at 3 regardless of success.
        requests_sent = amc.stream_otel_gauges(
            {"authservice": out / "authservice.csv"},
            endpoint=f"http://127.0.0.1:{server.server_port}/v1/metrics",
            batch_seconds=3600,
            metric_prefix="",
            speedup=1000000.0,
            timeout_seconds=2.0,
            max_events=3,
            max_retries=0,
            auth_headers=None,
            protocol="json",
            activity_log_path=None,
            verbose=False,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    # Exactly 3 attempts hit the mock; none succeeded so the return value is 0.
    assert len(attempts) == 3, (
        f"expected exactly 3 attempted flushes capped by max_events=3, "
        f"got {len(attempts)} (regression: cap was gating on successes "
        f"instead of attempts)"
    )
    assert requests_sent == 0, (
        f"expected zero successful sends against a 500 endpoint, "
        f"got {requests_sent}"
    )


def test_stream_otel_gauges_http_error_activity_log_includes_response_headers(
    amc, tmp_path, capsys
):
    """Gauge HTTP failures log response headers and payloads, including CF-Ray."""
    csv_path = tmp_path / "database.csv"
    log_target = tmp_path / "gauge_http_error.log"
    csv_path.write_text(
        "timestamp,write_latency_ms\n"
        "2026-03-10 00:00:00,12.3\n",
        encoding="utf-8",
    )

    class _BrokenCollector(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            self.send_response(403)
            self.send_header("CF-Ray", "gauge-ray-456")
            self.send_header("X-Debug-Header", "gauge-visible")
            self.end_headers()

        def log_message(self, *args, **kwargs):  # noqa: D401, ANN002, ANN003
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), _BrokenCollector)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        requests_sent = amc.stream_otel_gauges(
            {"database": csv_path},
            endpoint=f"http://127.0.0.1:{server.server_port}/v1/metrics",
            batch_seconds=60,
            metric_prefix="",
            speedup=1000000.0,
            timeout_seconds=2.0,
            max_events=1,
            max_retries=0,
            auth_headers=None,
            protocol="json",
            activity_log_path=log_target,
            verbose=False,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    captured = capsys.readouterr()
    assert requests_sent == 0
    assert "CF-Ray: gauge-ray-456" not in captured.err
    assert "X-Debug-Header: gauge-visible" not in captured.err
    assert '"resourceMetrics"' not in captured.err

    import shlex
    fail_lines = [
        shlex.split(line) for line in log_target.read_text().splitlines()
        if " FAIL " in line
    ]
    assert fail_lines, "expected FAIL activity record"
    kv = {
        token.split("=", 1)[0]: token.split("=", 1)[1]
        for token in fail_lines[0][2:]
        if "=" in token
    }
    assert kv["cf_ray"] == "gauge-ray-456"
    assert ["CF-Ray", "gauge-ray-456"] in json.loads(kv["response_headers"])
    assert ["X-Debug-Header", "gauge-visible"] in json.loads(kv["response_headers"])
    assert '"resourceMetrics"' in kv["request_body"]
    assert '"write_latency_ms"' in kv["request_body"]
    assert '"gauge"' in kv["request_body"]


def test_stream_otel_gauges_wall_clock_pacing_matches_batch_seconds(amc, tmp_path):
    """Regression for the pacing bug: between consecutive batches the
    streamer must sleep ``batch_seconds / speedup`` of wall-clock — not
    ``interval_seconds / speedup``. We seed CSVs covering N*batch_seconds of
    timeline, call ``stream_otel_gauges`` directly in-process against a mock
    collector, and assert the elapsed wall-clock is within tolerance of the
    expected ``(N-1) * batch_seconds / speedup``.
    """
    # Generate CSVs first (no streaming) so the streamer call below is the
    # only thing being timed.
    out = tmp_path / "pacing"
    out.mkdir()
    amc.main([
        "--duration-days", "1",
        "--interval-seconds", "60",
        "--drop-rate", "0",
        "--components", "authservice",
        "--output-dir", str(out),
    ])

    server, thread, base = _start_mock()
    try:
        batch_seconds = 3600   # 1 hour of timeline per batch
        speedup = 360000.0     # 1h / 360000 = 10ms wall-clock per batch boundary
        expected_n_batches = 86400 // batch_seconds  # 24

        start = time.perf_counter()
        requests_sent = amc.stream_otel_gauges(
            {"authservice": out / "authservice.csv"},
            endpoint=f"{base}/v1/metrics",
            batch_seconds=batch_seconds,
            metric_prefix="",
            speedup=speedup,
            timeout_seconds=5.0,
            max_events=None,
            max_retries=2,
            auth_headers=None,
            protocol="json",
            activity_log_path=None,
            verbose=False,
        )
        elapsed = time.perf_counter() - start
    finally:
        _stop_mock(server, thread)

    assert requests_sent == expected_n_batches
    # The streamer sleeps before each batch except the first one, so the
    # total pacing sleep budget is (N-1) * batch_seconds / speedup. With the
    # buggy pre-fix code this would collapse to (N-1) * interval_seconds /
    # speedup, ~60× shorter. Tolerate +200% / -50% for HTTP + scheduler jitter
    # — the assertion is about the *order of magnitude*, not exact timing.
    expected_sleep = (expected_n_batches - 1) * batch_seconds / speedup
    lower = expected_sleep * 0.5
    # No upper bound stricter than 4x — CI runners can be slow, but the buggy
    # path would be ~60x faster so even a 4x ceiling distinguishes the two.
    upper = expected_sleep * 4.0 + 1.0
    assert lower <= elapsed <= upper, (
        f"wall-clock {elapsed:.3f}s outside expected pacing window "
        f"[{lower:.3f}, {upper:.3f}] (expected ~{expected_sleep:.3f}s for "
        f"{expected_n_batches} batches at batch_seconds={batch_seconds}, "
        f"speedup={speedup}). The pre-fix bug would produce "
        f"~{(expected_n_batches - 1) * 60 / speedup:.5f}s — far below the lower bound."
    )


# ------------------------------------------------------------------
# Phase 6: dimension attributes from Phase 2 instance columns
# ------------------------------------------------------------------
def _entry_with_dims(amc, ts, comp, metric, value, dimensions):
    return {
        "timestamp": ts,
        "time_unix_nano": amc._to_unix_nanos(ts),
        "component": comp,
        "metric": metric,
        "value": value,
        "dimensions": dimensions,
    }


def test_build_otlp_gauge_payload_emits_dimension_attributes(amc):
    """When the batch entry carries a ``dimensions`` dict (Phase 2 instance
    columns lifted by ``_iter_component_rows``), each non-empty dimension
    surfaces as an additional string attribute on every data point alongside
    ``metric.name``, ``component``, and ``signal.type``."""
    batch = [
        _entry_with_dims(
            amc, "2026-03-10 00:00:00", "authservice", "cpu_util_pct", 12.5,
            {"id": "i0", "pod": "pod-0"},
        ),
        _entry_with_dims(
            amc, "2026-03-10 00:00:00", "authservice", "cpu_util_pct", 13.5,
            {"id": "i1", "pod": "pod-1"},
        ),
    ]
    payload = amc._build_otlp_gauge_payload(batch)
    dps = payload["resourceMetrics"][0]["scopeMetrics"][0]["metrics"][0]["gauge"]["dataPoints"]
    assert len(dps) == 2
    pods = {
        next(a["value"]["stringValue"] for a in dp["attributes"] if a["key"] == "pod")
        for dp in dps
    }
    assert pods == {"pod-0", "pod-1"}
    # The original three attributes are preserved.
    attrs_by_key = {a["key"] for a in dps[0]["attributes"]}
    assert {"metric.name", "component", "signal.type", "id", "pod"} <= attrs_by_key


def test_build_otlp_gauge_payload_omits_empty_dimension_cells(amc):
    """Empty-string or ``None`` dimension cells (e.g. ``host`` left blank in
    the CSV) must not be emitted as empty-string attributes."""
    batch = [
        _entry_with_dims(
            amc, "2026-03-10 00:00:00", "authservice", "cpu_util_pct", 12.5,
            {"id": "i0", "pod": "pod-0", "host": "", "az": None},
        ),
    ]
    payload = amc._build_otlp_gauge_payload(batch)
    dp = payload["resourceMetrics"][0]["scopeMetrics"][0]["metrics"][0]["gauge"]["dataPoints"][0]
    attr_keys = {a["key"] for a in dp["attributes"]}
    assert "host" not in attr_keys, "empty-string dimension must be omitted"
    assert "az" not in attr_keys, "None dimension must be omitted"
    assert "id" in attr_keys
    assert "pod" in attr_keys


def test_build_otlp_gauge_payload_without_dimensions_field_is_unchanged(amc):
    """Entries without a ``dimensions`` field (dimensionless CSVs, the default)
    produce the same attribute set as before Phase 6."""
    batch = [
        _entry(amc, "2026-03-10 00:00:00", "authservice", "cpu_util_pct", 12.5),
    ]
    payload = amc._build_otlp_gauge_payload(batch)
    dp = payload["resourceMetrics"][0]["scopeMetrics"][0]["metrics"][0]["gauge"]["dataPoints"][0]
    attrs = {a["key"]: a["value"]["stringValue"] for a in dp["attributes"]}
    assert attrs == {
        "metric.name": "cpu_util_pct",
        "component": "authservice",
        "signal.type": "metric_value",
    }


def test_build_otlp_gauge_protobuf_emits_dimension_attributes(amc):
    """Protobuf builder mirrors the JSON builder on dimension emission."""
    from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import (
        ExportMetricsServiceRequest,
    )
    batch = [
        _entry_with_dims(
            amc, "2026-03-10 00:00:00", "authservice", "cpu_util_pct", 12.5,
            {"id": "i0", "pod": "pod-0", "tenant": "acme"},
        ),
    ]
    raw = amc._build_otlp_gauge_protobuf(batch)
    req = ExportMetricsServiceRequest.FromString(raw)
    dp = req.resource_metrics[0].scope_metrics[0].metrics[0].gauge.data_points[0]
    attrs = {a.key: a.value.string_value for a in dp.attributes}
    assert attrs["pod"] == "pod-0"
    assert attrs["id"] == "i0"
    assert attrs["tenant"] == "acme"
    # The base three attributes are preserved.
    assert attrs["metric.name"] == "cpu_util_pct"
    assert attrs["component"] == "authservice"
    assert attrs["signal.type"] == "metric_value"


def test_build_otlp_gauge_protobuf_omits_empty_dimensions(amc):
    """Protobuf path also drops empty-string and None dimension cells."""
    from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import (
        ExportMetricsServiceRequest,
    )
    batch = [
        _entry_with_dims(
            amc, "2026-03-10 00:00:00", "authservice", "cpu_util_pct", 12.5,
            {"id": "i0", "host": "", "region": None},
        ),
    ]
    raw = amc._build_otlp_gauge_protobuf(batch)
    req = ExportMetricsServiceRequest.FromString(raw)
    dp = req.resource_metrics[0].scope_metrics[0].metrics[0].gauge.data_points[0]
    attr_keys = {a.key for a in dp.attributes}
    assert "host" not in attr_keys
    assert "region" not in attr_keys
    assert "id" in attr_keys


def test_iter_component_rows_lifts_dimension_columns(amc, tmp_path):
    """The CSV reader exposes Phase 2 dimension columns alongside metric
    values. Non-empty cells appear in the per-row dimensions dict; empty
    cells are omitted so downstream attribute emission can skip them
    cleanly."""
    csv_path = tmp_path / "authservice.csv"
    csv_path.write_text(
        "timestamp,id,host,pod,az,region,tenant,cpu_util_pct,error_rate\n"
        "2026-03-10 00:00:00,i0,,pod-0,,,,12.5,0.01\n"
        "2026-03-10 00:00:01,i1,,pod-1,,,,13.0,0.02\n",
        encoding="utf-8",
    )
    rows = list(amc._iter_component_rows("authservice", csv_path))
    assert len(rows) == 2
    # Every yielded row carries (ts, component, values, dimensions).
    for row in rows:
        assert len(row) == 4, (
            "Phase 6: _iter_component_rows must yield a 4-tuple "
            "(ts, component, values, dimensions)"
        )
    ts0, comp0, values0, dims0 = rows[0]
    assert ts0 == "2026-03-10 00:00:00"
    assert comp0 == "authservice"
    assert dict(values0) == {"cpu_util_pct": 12.5, "error_rate": 0.01}
    assert dims0 == {"id": "i0", "pod": "pod-0"}, (
        f"empty cells (host/az/region/tenant) must be omitted, got {dims0!r}"
    )
    _, _, _, dims1 = rows[1]
    assert dims1 == {"id": "i1", "pod": "pod-1"}


def test_iter_component_rows_dimensionless_csv_returns_empty_dims(amc, tmp_path):
    """Today's dimensionless CSVs (no Phase 2 columns) yield an empty
    dimensions dict so the builder treats them like pre-Phase-6 entries."""
    csv_path = tmp_path / "authservice.csv"
    csv_path.write_text(
        "timestamp,cpu_util_pct,error_rate\n"
        "2026-03-10 00:00:00,12.5,0.01\n",
        encoding="utf-8",
    )
    rows = list(amc._iter_component_rows("authservice", csv_path))
    assert len(rows) == 1
    ts, comp, values, dims = rows[0]
    assert ts == "2026-03-10 00:00:00"
    assert comp == "authservice"
    assert dict(values) == {"cpu_util_pct": 12.5, "error_rate": 0.01}
    assert dims == {}


def test_stream_otel_gauges_emits_pod_attribute_with_instances(tmp_path):
    """End-to-end: with ``--instances-per-component 3`` every gauge data
    point carries a ``pod=pod-N`` attribute matching the CSV row's
    dimension column. ``id`` is also present; the other dimension
    fields stay empty in v1 and must be omitted."""
    server, thread, base = _start_mock()
    try:
        result = _invoke(
            "--duration-days", "1",
            "--interval-seconds", "3600",
            "--drop-rate", "0",
            "--components", "authservice",
            "--instances-per-component", "3",
            "--otel-enabled",
            "--otel-emit-gauges",
            "--otel-metrics-endpoint", f"{base}/v1/metrics",
            "--otel-stream-protocol", "json",
            "--otel-stream-speedup", "1000000",
            "--otel-gauge-batch-seconds", "86400",
            "--output-dir", str(tmp_path / "dims"),
        )
        assert result.returncode == 0, result.stderr
    finally:
        _stop_mock(server, thread)

    # Every gauge data point has a pod attribute. Collect the set.
    pods_seen = set()
    other_dim_keys = set()
    for path, ctype, body in server.received:
        try:
            payload = json.loads(body)
        except Exception:
            continue
        for rm in payload.get("resourceMetrics", []):
            for sm in rm.get("scopeMetrics", []):
                for m in sm.get("metrics", []):
                    if "gauge" not in m:
                        continue
                    for dp in m["gauge"]["dataPoints"]:
                        attrs = {a["key"]: a["value"]["stringValue"]
                                 for a in dp["attributes"]}
                        assert "pod" in attrs, (
                            f"gauge data point missing pod attribute; "
                            f"attrs={attrs!r}"
                        )
                        pods_seen.add(attrs["pod"])
                        # id is also a Phase 2 dimension, must be present.
                        assert "id" in attrs
                        # Track any unexpected dimension keys.
                        for k in ("host", "az", "region", "tenant"):
                            if k in attrs:
                                other_dim_keys.add(k)
    assert pods_seen == {"pod-0", "pod-1", "pod-2"}, (
        f"expected pod-0..pod-2 as attribute values, got {pods_seen!r}"
    )
    assert not other_dim_keys, (
        f"v1 only populates id/pod; got unexpected dimension keys "
        f"emitted as empty strings: {other_dim_keys!r}"
    )


def test_stream_otel_gauges_dimensionless_csv_byte_identical_to_pre_phase6(tmp_path):
    """With ``--instances-per-component 1`` (the default) and no dimension
    columns on disk, gauge payloads carry exactly the pre-Phase-6 attribute
    set: ``metric.name``, ``component``, ``signal.type``. No empty-string
    dimension attributes leak in."""
    server, thread, base = _start_mock()
    try:
        result = _invoke(
            "--duration-days", "1",
            "--interval-seconds", "3600",
            "--drop-rate", "0",
            "--components", "authservice",
            "--otel-enabled",
            "--otel-emit-gauges",
            "--otel-metrics-endpoint", f"{base}/v1/metrics",
            "--otel-stream-protocol", "json",
            "--otel-stream-speedup", "1000000",
            "--otel-gauge-batch-seconds", "86400",
            "--output-dir", str(tmp_path / "no_dims"),
        )
        assert result.returncode == 0, result.stderr
    finally:
        _stop_mock(server, thread)

    saw_data_point = False
    for path, ctype, body in server.received:
        try:
            payload = json.loads(body)
        except Exception:
            continue
        for rm in payload.get("resourceMetrics", []):
            for sm in rm.get("scopeMetrics", []):
                for m in sm.get("metrics", []):
                    if "gauge" not in m:
                        continue
                    for dp in m["gauge"]["dataPoints"]:
                        saw_data_point = True
                        attrs = {a["key"] for a in dp["attributes"]}
                        assert attrs == {"metric.name", "component", "signal.type"}, (
                            f"dimensionless gauge data point had unexpected "
                            f"attributes: {attrs!r}"
                        )
    assert saw_data_point, "expected at least one gauge data point"


def test_build_otlp_metric_payload_emits_dimension_attributes(amc):
    """Phase 6 also extends the per-signal anomaly metric path. When an
    anomaly row carries a ``dimensions`` dict (Phase 4 territory), each
    non-empty entry surfaces as a string attribute on the anomaly.count
    data point. Anomaly rows without dimensions emit today's exact
    attribute set (v1 anomalies.csv has no instance columns)."""
    entry_with = {
        "timestamp": "2026-03-10 00:00:00",
        "component": "authservice",
        "metric": "cpu_util_pct",
        "description": "test anomaly",
        "value": 99.0,
        "dimensions": {"pod": "pod-1"},
    }
    payload = amc._build_otlp_metric_payload(entry_with)
    dp = payload["resourceMetrics"][0]["scopeMetrics"][0]["metrics"][0]["sum"]["dataPoints"][0]
    attrs = {a["key"]: a["value"]["stringValue"] for a in dp["attributes"]}
    assert attrs.get("pod") == "pod-1"
    # Original four attributes preserved.
    assert "event.id" in attrs
    assert attrs["signal.type"] == "metric_anomaly"
    assert attrs["metric.name"] == "cpu_util_pct"
    assert attrs["component"] == "authservice"


def test_build_otlp_metric_payload_without_dimensions_is_unchanged(amc):
    """An anomaly entry without a ``dimensions`` field (today's v1 anomalies)
    emits exactly the pre-Phase-6 attribute set."""
    entry = {
        "timestamp": "2026-03-10 00:00:00",
        "component": "authservice",
        "metric": "cpu_util_pct",
        "description": "test anomaly",
        "value": 99.0,
    }
    payload = amc._build_otlp_metric_payload(entry)
    dp = payload["resourceMetrics"][0]["scopeMetrics"][0]["metrics"][0]["sum"]["dataPoints"][0]
    attr_keys = {a["key"] for a in dp["attributes"]}
    assert attr_keys == {"event.id", "signal.type", "metric.name", "component"}


def test_build_otlp_metric_protobuf_emits_dimension_attributes(amc):
    """Protobuf metric builder mirrors the JSON metric builder on dimensions."""
    from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import (
        ExportMetricsServiceRequest,
    )
    entry = {
        "timestamp": "2026-03-10 00:00:00",
        "component": "authservice",
        "metric": "cpu_util_pct",
        "description": "test anomaly",
        "value": 99.0,
        "dimensions": {"pod": "pod-2", "id": "i2", "host": ""},
    }
    raw = amc._build_otlp_metric_protobuf(entry)
    req = ExportMetricsServiceRequest.FromString(raw)
    dp = req.resource_metrics[0].scope_metrics[0].metrics[0].sum.data_points[0]
    attrs = {a.key: a.value.string_value for a in dp.attributes}
    assert attrs.get("pod") == "pod-2"
    assert attrs.get("id") == "i2"
    assert "host" not in attrs, "empty dimension must be omitted"
