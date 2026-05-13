"""CLI surface tests via subprocess.

Each test invokes ``anomaly-metric-creator.py`` as an external process so we
also exercise the ``if __name__ == "__main__"`` entry and prove no in-process
state is leaking determinism.
"""

import filecmp
import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from conftest import COMPONENTS, SCRIPT_PATH


def _invoke(*args, cwd=None):
    return subprocess.run(
        [sys.executable, str(SCRIPT_PATH), *args],
        capture_output=True,
        text=True,
        cwd=cwd,
    )


def test_help_lists_every_flag():
    result = _invoke("--help")
    assert result.returncode == 0, result.stderr
    out = result.stdout
    for flag in ("--duration-days", "--seed", "--output-dir", "--drop-rate",
                 "--interval-seconds", "--emit-selection", "--components",
                 "--signal-level", "--anomaly-count",
                 "--otel-enabled", "--otel-disabled",
                 "--otel-logs-endpoint", "--otel-logs-auth-token",
                 "--otel-metrics-endpoint", "--otel-metrics-auth-token",
                 "--otel-traces-endpoint", "--otel-traces-auth-token",
                 "--otel-stream-auth-scheme",
                 "--otel-verbose", "--no-otel-verbose"):
        assert flag in out, f"--help missing flag {flag}"
        # Argparse renders the help text on the line following the flag; require
        # something non-trivial follows so the flag isn't just a bare token.
        after = out.split(flag, 1)[1]
        assert any(c.isalpha() for c in after[:200]), f"{flag} has empty help text"


def test_invalid_duration_days_fails(tmp_path):
    result = _invoke("--duration-days", "0", "--output-dir", str(tmp_path))
    assert result.returncode != 0, "expected non-zero exit for --duration-days 0"
    assert "duration-days" in (result.stderr + result.stdout)


def test_invalid_drop_rate_low_fails(tmp_path):
    result = _invoke("--drop-rate", "-0.1", "--output-dir", str(tmp_path))
    assert result.returncode != 0, "expected non-zero exit for --drop-rate -0.1"
    assert "drop-rate" in (result.stderr + result.stdout)


def test_invalid_drop_rate_high_fails(tmp_path):
    result = _invoke("--drop-rate", "1.5", "--output-dir", str(tmp_path))
    assert result.returncode != 0, "expected non-zero exit for --drop-rate 1.5"
    assert "drop-rate" in (result.stderr + result.stdout)


def test_invalid_interval_seconds_zero_fails(tmp_path):
    result = _invoke("--interval-seconds", "0", "--output-dir", str(tmp_path))
    assert result.returncode != 0, "expected non-zero exit for --interval-seconds 0"
    assert "interval-seconds" in (result.stderr + result.stdout)


def test_invalid_interval_seconds_negative_fails(tmp_path):
    result = _invoke("--interval-seconds", "-1.5", "--output-dir", str(tmp_path))
    assert result.returncode != 0, "expected non-zero exit for --interval-seconds -1.5"
    assert "interval-seconds" in (result.stderr + result.stdout)


def test_output_dir_is_created(tmp_path):
    target = tmp_path / "deep" / "nested" / "iot"
    assert not target.exists()
    result = _invoke("--duration-days", "1", "--seed", "42", "--output-dir", str(target))
    assert result.returncode == 0, result.stderr
    assert target.is_dir()
    for component in COMPONENTS:
        assert (target / f"{component}.csv").exists(), f"{component}.csv not written"
    assert (target / "anomalies.csv").exists()
    assert (target / "metric_report.log").exists()
    assert (target / "metric_traces.jsonl").exists()


def test_cross_process_determinism(tmp_path):
    """Two subprocesses with the same seed produce byte-identical CSVs. Proves no
    hidden in-process state (module-level cache, lazy import side effects, etc.)
    is leaking determinism — a regression class the in-process determinism test
    can't catch.
    """
    a = tmp_path / "run_a"
    b = tmp_path / "run_b"
    for out in (a, b):
        result = _invoke("--seed", "7", "--duration-days", "1", "--output-dir", str(out))
        assert result.returncode == 0, result.stderr

    files = [f"{c}.csv" for c in COMPONENTS] + [
        "anomalies.csv",
        "metric_report.log",
        "metric_traces.jsonl",
    ]
    differ = [name for name in files if not filecmp.cmp(a / name, b / name, shallow=False)]
    assert not differ, f"cross-process determinism broken for: {differ}"


def test_invalid_emit_selection_fails(tmp_path):
    result = _invoke("--emit-selection", "metrics,invalid", "--output-dir", str(tmp_path))
    assert result.returncode != 0, "expected non-zero exit for invalid --emit-selection"
    assert "emit-selection" in (result.stderr + result.stdout)


def test_emit_selection_logs_and_traces_only(tmp_path):
    out = tmp_path / "emit_logs_traces"
    result = _invoke(
        "--duration-days", "1",
        "--interval-seconds", "60",
        "--emit-selection", "logs,traces",
        "--output-dir", str(out),
    )
    assert result.returncode == 0, result.stderr
    for component in COMPONENTS:
        assert not (out / f"{component}.csv").exists(), f"{component}.csv should not be emitted"
    assert not (out / "anomalies.csv").exists()
    assert (out / "metric_report.log").exists()
    assert (out / "metric_traces.jsonl").exists()


def test_emit_selection_metrics_only(tmp_path):
    out = tmp_path / "emit_metrics_only"
    result = _invoke(
        "--duration-days", "1",
        "--interval-seconds", "60",
        "--emit-selection", "metrics",
        "--output-dir", str(out),
    )
    assert result.returncode == 0, result.stderr
    for component in COMPONENTS:
        assert (out / f"{component}.csv").exists(), f"{component}.csv should be emitted"
    assert (out / "anomalies.csv").exists()
    assert not (out / "metric_report.log").exists()
    assert not (out / "metric_traces.jsonl").exists()


def test_components_filter_limits_csv_emission(tmp_path):
    """--components only emits CSVs for the named components."""
    out = tmp_path / "components_subset"
    result = _invoke(
        "--duration-days", "1",
        "--interval-seconds", "60",
        "--components", "authservice,database",
        "--output-dir", str(out),
    )
    assert result.returncode == 0, result.stderr
    selected = {"authservice", "database"}
    for component in COMPONENTS:
        path = out / f"{component}.csv"
        if component in selected:
            assert path.exists(), f"{component}.csv should be emitted"
        else:
            assert not path.exists(), f"{component}.csv should NOT be emitted"


def test_components_filter_filters_anomalies_csv(tmp_path):
    """Anomalies CSV only contains rows for the selected components."""
    import csv as _csv
    out = tmp_path / "components_anomalies"
    result = _invoke(
        "--duration-days", "1",
        "--interval-seconds", "60",
        "--components", "authservice",
        "--output-dir", str(out),
    )
    assert result.returncode == 0, result.stderr
    with open(out / "anomalies.csv") as f:
        rows = list(_csv.DictReader(f))
    assert rows, "expected at least one anomaly row for authservice"
    components_in_csv = {r["component"] for r in rows}
    assert components_in_csv == {"authservice"}, \
        f"anomalies.csv should only contain authservice, got {components_in_csv}"


def test_components_filter_invalid_name_fails(tmp_path):
    result = _invoke(
        "--components", "authservice,bogus_component",
        "--output-dir", str(tmp_path),
    )
    assert result.returncode != 0, "expected non-zero exit for invalid --components value"
    assert "components" in (result.stderr + result.stdout)


def test_components_filter_default_emits_all(tmp_path):
    """Default (no --components) emits every component CSV — regression guard."""
    out = tmp_path / "components_default"
    result = _invoke(
        "--duration-days", "1",
        "--interval-seconds", "60",
        "--output-dir", str(out),
    )
    assert result.returncode == 0, result.stderr
    for component in COMPONENTS:
        assert (out / f"{component}.csv").exists(), \
            f"{component}.csv missing without --components filter"


def test_components_filter_limits_otel_stream(tmp_path):
    """--components limits which component's anomalies are streamed via OTel."""
    received = []

    class _Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            received.append(json.loads(body.decode("utf-8")))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args, **kwargs):  # noqa: D401, ANN002, ANN003
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base_url = f"http://127.0.0.1:{server.server_port}"
        result = _invoke(
            "--duration-days", "1",
            "--interval-seconds", "60",
            "--components", "authservice",
            "--otel-enabled",
            "--otel-logs-endpoint", f"{base_url}/v1/logs",
            "--otel-stream-protocol", "json",
            "--otel-stream-speedup", "1000000",
            "--output-dir", str(tmp_path / "otel_components"),
        )
        assert result.returncode == 0, result.stderr
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert received, "expected at least one OTel event for authservice"
    # Every payload should reference only authservice via its log body attributes.
    for payload in received:
        body_text = json.dumps(payload)
        # The component name surfaces as an attribute value in OTLP log bodies.
        # Sanity-check: no other component's name appears in any streamed body.
        for other in COMPONENTS:
            if other == "authservice":
                continue
            assert other not in body_text, \
                f"OTel stream included {other} despite --components=authservice"


def _count_manifest_rows(out_dir):
    import csv as _csv
    with open(out_dir / "anomalies.csv") as f:
        return sum(1 for _ in _csv.DictReader(f))


def test_signal_level_default_medium_matches_no_flag(tmp_path):
    """--signal-level medium (default) is identical to omitting the flag."""
    out_default = tmp_path / "level_default"
    out_medium = tmp_path / "level_medium"
    r1 = _invoke(
        "--duration-days", "1",
        "--interval-seconds", "60",
        "--output-dir", str(out_default),
    )
    r2 = _invoke(
        "--duration-days", "1",
        "--interval-seconds", "60",
        "--signal-level", "medium",
        "--output-dir", str(out_medium),
    )
    assert r1.returncode == 0, r1.stderr
    assert r2.returncode == 0, r2.stderr
    assert _count_manifest_rows(out_default) == _count_manifest_rows(out_medium)


def test_signal_level_high_emits_more_than_medium(tmp_path):
    """--signal-level high includes high-pressure scenarios; manifest is strictly larger."""
    out_med = tmp_path / "level_medium"
    out_high = tmp_path / "level_high"
    r1 = _invoke(
        "--duration-days", "1",
        "--interval-seconds", "60",
        "--signal-level", "medium",
        "--output-dir", str(out_med),
    )
    r2 = _invoke(
        "--duration-days", "1",
        "--interval-seconds", "60",
        "--signal-level", "high",
        "--output-dir", str(out_high),
    )
    assert r1.returncode == 0, r1.stderr
    assert r2.returncode == 0, r2.stderr
    assert _count_manifest_rows(out_high) > _count_manifest_rows(out_med)


def test_signal_level_low_emits_fewer_than_medium(tmp_path):
    """--signal-level low only keeps explicitly-low specs; manifest is strictly smaller."""
    out_med = tmp_path / "level_medium"
    out_low = tmp_path / "level_low"
    r1 = _invoke(
        "--duration-days", "1",
        "--interval-seconds", "60",
        "--signal-level", "medium",
        "--output-dir", str(out_med),
    )
    r2 = _invoke(
        "--duration-days", "1",
        "--interval-seconds", "60",
        "--signal-level", "low",
        "--output-dir", str(out_low),
    )
    assert r1.returncode == 0, r1.stderr
    assert r2.returncode == 0, r2.stderr
    assert _count_manifest_rows(out_low) < _count_manifest_rows(out_med)


def test_signal_level_invalid_value_fails(tmp_path):
    result = _invoke(
        "--signal-level", "extreme",
        "--output-dir", str(tmp_path),
    )
    assert result.returncode != 0
    assert "signal-level" in (result.stderr + result.stdout)


def test_anomaly_count_caps_manifest(tmp_path):
    """--anomaly-count caps the total anomalies in the manifest."""
    out = tmp_path / "count_3"
    result = _invoke(
        "--duration-days", "1",
        "--interval-seconds", "60",
        "--drop-rate", "0",
        "--anomaly-count", "3",
        "--output-dir", str(out),
    )
    assert result.returncode == 0, result.stderr
    assert _count_manifest_rows(out) == 3


def test_anomaly_count_larger_than_pool_keeps_all(tmp_path):
    """--anomaly-count larger than the eligible pool keeps every spec — no error."""
    out_unlimited = tmp_path / "count_unlimited"
    out_huge = tmp_path / "count_huge"
    r1 = _invoke(
        "--duration-days", "1",
        "--interval-seconds", "60",
        "--output-dir", str(out_unlimited),
    )
    r2 = _invoke(
        "--duration-days", "1",
        "--interval-seconds", "60",
        "--anomaly-count", "100000",
        "--output-dir", str(out_huge),
    )
    assert r1.returncode == 0, r1.stderr
    assert r2.returncode == 0, r2.stderr
    assert _count_manifest_rows(out_huge) == _count_manifest_rows(out_unlimited)


def test_anomaly_count_deterministic_for_same_seed(tmp_path):
    """Repeated runs with the same seed + count produce the same manifest."""
    import csv as _csv

    def _manifest_keys(out_dir):
        with open(out_dir / "anomalies.csv") as f:
            return sorted(
                (r["component"], r["metric"], r["description"])
                for r in _csv.DictReader(f)
            )

    out_a = tmp_path / "count_seed_a"
    out_b = tmp_path / "count_seed_b"
    args = [
        "--duration-days", "1",
        "--interval-seconds", "60",
        "--anomaly-count", "5",
        "--seed", "42",
    ]
    r1 = _invoke(*args, "--output-dir", str(out_a))
    r2 = _invoke(*args, "--output-dir", str(out_b))
    assert r1.returncode == 0, r1.stderr
    assert r2.returncode == 0, r2.stderr
    assert _manifest_keys(out_a) == _manifest_keys(out_b)


def test_anomaly_count_invalid_value_fails(tmp_path):
    result = _invoke(
        "--anomaly-count", "0",
        "--output-dir", str(tmp_path),
    )
    assert result.returncode != 0
    assert "anomaly-count" in (result.stderr + result.stdout)


def test_combine_requires_metrics_selection(tmp_path):
    out = tmp_path / "combine_no_metrics"
    result = _invoke(
        "--combine",
        "--emit-selection", "logs,traces",
        "--output-dir", str(out),
    )
    assert result.returncode != 0, "expected non-zero exit when --combine excludes metrics"
    assert "combine" in (result.stderr + result.stdout)


def test_invalid_otel_logs_endpoint_scheme_fails(tmp_path):
    result = _invoke(
        "--otel-logs-endpoint", "localhost:4318/v1/logs",
        "--output-dir", str(tmp_path),
    )
    assert result.returncode != 0, "expected non-zero exit for invalid otel endpoint scheme"
    assert "otel-logs-endpoint" in (result.stderr + result.stdout)


def test_invalid_otel_stream_speedup_fails(tmp_path):
    result = _invoke(
        "--otel-logs-endpoint", "http://localhost:4318/v1/logs",
        "--otel-stream-speedup", "0",
        "--output-dir", str(tmp_path),
    )
    assert result.returncode != 0, "expected non-zero exit for --otel-stream-speedup 0"
    assert "otel-stream-speedup" in (result.stderr + result.stdout)


def test_invalid_otel_stream_protocol_fails(tmp_path):
    result = _invoke(
        "--otel-logs-endpoint", "http://localhost:4318/v1/logs",
        "--otel-stream-protocol", "xml",
        "--output-dir", str(tmp_path),
    )
    assert result.returncode != 0, "expected non-zero exit for invalid --otel-stream-protocol"
    assert "otel-stream-protocol" in (result.stderr + result.stdout)


def test_otel_stream_posts_events_to_endpoints(tmp_path):
    received = []

    class _Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            received.append((self.path, json.loads(body.decode("utf-8"))))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args, **kwargs):  # noqa: D401, ANN002, ANN003
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base_url = f"http://127.0.0.1:{server.server_port}"
        result = _invoke(
            "--duration-days", "1",
            "--interval-seconds", "60",
            "--otel-enabled",
            "--otel-logs-endpoint", f"{base_url}/v1/logs",
            "--otel-metrics-endpoint", f"{base_url}/v1/metrics",
            "--otel-traces-endpoint", f"{base_url}/v1/traces",
            "--otel-stream-protocol", "json",
            "--otel-stream-speedup", "1000000",
            "--otel-stream-max-events", "1",
            "--output-dir", str(tmp_path / "stream_run"),
        )
        assert result.returncode == 0, result.stderr
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    # 1 event * 3 signals = 3 POSTs
    assert len(received) == 3, f"expected 3 streamed events, got {len(received)}"
    paths = {item[0] for item in received}
    assert paths == {"/v1/logs", "/v1/metrics", "/v1/traces"}


def test_otel_stream_disabled_by_default_makes_no_requests(tmp_path):
    """Endpoint configured but --otel-enabled omitted: no POSTs should occur."""
    received = []

    class _Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            received.append(self.path)
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args, **kwargs):  # noqa: D401, ANN002, ANN003
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base_url = f"http://127.0.0.1:{server.server_port}"
        result = _invoke(
            "--duration-days", "1",
            "--interval-seconds", "60",
            "--otel-logs-endpoint", f"{base_url}/v1/logs",
            "--otel-stream-max-events", "1",
            "--output-dir", str(tmp_path / "stream_disabled_default"),
        )
        assert result.returncode == 0, result.stderr
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert received == [], f"expected no streamed events when disabled, got {received}"


def test_otel_stream_explicit_disabled_makes_no_requests(tmp_path):
    """--otel-disabled with endpoint configured: no POSTs should occur."""
    received = []

    class _Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            received.append(self.path)
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args, **kwargs):  # noqa: D401, ANN002, ANN003
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base_url = f"http://127.0.0.1:{server.server_port}"
        result = _invoke(
            "--duration-days", "1",
            "--interval-seconds", "60",
            "--otel-disabled",
            "--otel-logs-endpoint", f"{base_url}/v1/logs",
            "--otel-stream-max-events", "1",
            "--output-dir", str(tmp_path / "stream_disabled_explicit"),
        )
        assert result.returncode == 0, result.stderr
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert received == [], f"expected no streamed events when disabled, got {received}"


def test_otel_enabled_without_endpoints_fails(tmp_path):
    """--otel-enabled with no configured endpoint should be a usage error."""
    result = _invoke(
        "--otel-enabled",
        "--output-dir", str(tmp_path / "stream_enabled_no_endpoints"),
    )
    assert result.returncode != 0, "expected non-zero exit for --otel-enabled with no endpoints"
    assert "otel-enabled" in (result.stderr + result.stdout)


def test_otel_stream_warns_and_continues_on_receiver_failure(tmp_path):
    attempts = []

    class _Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            attempts.append(self.path)
            self.send_response(500)
            self.end_headers()

        def log_message(self, *args, **kwargs):  # noqa: D401, ANN002, ANN003
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        endpoint = f"http://127.0.0.1:{server.server_port}/v1/logs"
        result = _invoke(
            "--duration-days", "1",
            "--interval-seconds", "60",
            "--otel-enabled",
            "--otel-logs-endpoint", endpoint,
            "--otel-stream-speedup", "1000000",
            "--otel-stream-max-events", "1",
            "--output-dir", str(tmp_path / "stream_fail_run"),
        )
        assert result.returncode == 0, result.stderr
        assert "WARNING: OTEL logs stream" in result.stderr
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert len(attempts) == 4, f"expected 4 total attempts (1 initial + 3 retries), got {len(attempts)}"


def test_otel_stream_uses_env_based_auth_token(tmp_path, monkeypatch):
    auth_headers = []
    monkeypatch.setenv("MEZMO_OTEL_LOGS_AUTH_TOKEN", "secret-token")

    class _Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            auth_headers.append(self.headers.get("Authorization"))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args, **kwargs):  # noqa: D401, ANN002, ANN003
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        endpoint = f"http://127.0.0.1:{server.server_port}/v1/logs"
        result = _invoke(
            "--duration-days", "1",
            "--interval-seconds", "60",
            "--otel-enabled",
            "--otel-logs-endpoint", endpoint,
            "--otel-stream-max-events", "1",
            "--output-dir", str(tmp_path / "stream_auth_run"),
        )
        assert result.returncode == 0, result.stderr
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert auth_headers == ["Bearer secret-token"]


def test_otel_stream_uses_explicit_auth_token_and_scheme(tmp_path):
    auth_headers = []

    class _Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            auth_headers.append(self.headers.get("Authorization"))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args, **kwargs):  # noqa: D401, ANN002, ANN003
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        endpoint = f"http://127.0.0.1:{server.server_port}/v1/logs"
        result = _invoke(
            "--duration-days", "1",
            "--interval-seconds", "60",
            "--otel-enabled",
            "--otel-logs-endpoint", endpoint,
            "--otel-logs-auth-token", "direct-token",
            "--otel-stream-auth-scheme", "ApiKey",
            "--otel-stream-max-events", "1",
            "--output-dir", str(tmp_path / "stream_direct_auth_run"),
        )
        assert result.returncode == 0, result.stderr
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert auth_headers == ["ApiKey direct-token"]


def test_otel_stream_uses_env_controls_for_endpoint_and_auth(tmp_path, monkeypatch):
    auth_headers = []

    class _Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            auth_headers.append(self.headers.get("Authorization"))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args, **kwargs):  # noqa: D401, ANN002, ANN003
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        endpoint = f"http://127.0.0.1:{server.server_port}/v1/logs"
        monkeypatch.setenv("MEZMO_OTEL_LOGS_ENDPOINT", endpoint)
        monkeypatch.setenv("MEZMO_OTEL_LOGS_AUTH_TOKEN", "env-token")
        monkeypatch.setenv("MEZMO_OTEL_STREAM_AUTH_SCHEME", "Token")
        result = _invoke(
            "--duration-days", "1",
            "--interval-seconds", "60",
            "--otel-enabled",
            "--otel-stream-max-events", "1",
            "--output-dir", str(tmp_path / "stream_env_auth_run"),
        )
        assert result.returncode == 0, result.stderr
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert auth_headers == ["Token env-token"]


def test_otel_stream_protobuf_sets_content_type(tmp_path):
    content_types = []

    class _Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            content_types.append(self.headers.get("Content-Type"))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args, **kwargs):  # noqa: D401, ANN002, ANN003
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        endpoint = f"http://127.0.0.1:{server.server_port}/v1/logs"
        result = _invoke(
            "--duration-days", "1",
            "--interval-seconds", "60",
            "--otel-enabled",
            "--otel-logs-endpoint", endpoint,
            "--otel-stream-protocol", "protobuf",
            "--otel-stream-max-events", "1",
            "--output-dir", str(tmp_path / "stream_proto_run"),
        )
        if result.returncode != 0 and "requires opentelemetry-proto + protobuf" in result.stderr:
            return
        assert result.returncode == 0, result.stderr
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert content_types == ["application/x-protobuf"]


def test_otel_activity_log_default_path_in_cwd(tmp_path):
    """Default activity log is written to ./otel-activity.log in the CWD when streaming runs."""
    received = []

    class _Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            received.append(self.path)
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args, **kwargs):  # noqa: D401, ANN002, ANN003
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    try:
        endpoint = f"http://127.0.0.1:{server.server_port}/v1/logs"
        result = _invoke(
            "--duration-days", "1",
            "--interval-seconds", "60",
            "--otel-enabled",
            "--otel-logs-endpoint", endpoint,
            "--otel-stream-protocol", "json",
            "--otel-stream-speedup", "1000000",
            "--otel-stream-max-events", "1",
            "--output-dir", str(tmp_path / "activity_default_run"),
            cwd=str(cwd),
        )
        assert result.returncode == 0, result.stderr
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    log_path = cwd / "otel-activity.log"
    assert log_path.exists(), "default activity log not created in CWD"
    contents = log_path.read_text()
    assert "START" in contents
    assert "SEND" in contents
    assert "END" in contents


def test_otel_activity_log_custom_path(tmp_path):
    """Activity log honors --otel-activity-log path."""
    class _Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args, **kwargs):  # noqa: D401, ANN002, ANN003
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    log_target = tmp_path / "nested" / "custom.log"
    try:
        endpoint = f"http://127.0.0.1:{server.server_port}/v1/logs"
        result = _invoke(
            "--duration-days", "1",
            "--interval-seconds", "60",
            "--otel-enabled",
            "--otel-logs-endpoint", endpoint,
            "--otel-stream-protocol", "json",
            "--otel-stream-speedup", "1000000",
            "--otel-stream-max-events", "1",
            "--otel-activity-log", str(log_target),
            "--output-dir", str(tmp_path / "activity_custom_run"),
        )
        assert result.returncode == 0, result.stderr
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert log_target.exists(), "custom activity log not created at requested path"
    contents = log_target.read_text()
    assert "START" in contents
    assert "SEND" in contents


def test_otel_activity_log_records_failure(tmp_path):
    """Activity log records FAIL entries when the receiver returns errors."""
    class _Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            self.send_response(500)
            self.end_headers()

        def log_message(self, *args, **kwargs):  # noqa: D401, ANN002, ANN003
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    log_target = tmp_path / "fail.log"
    try:
        endpoint = f"http://127.0.0.1:{server.server_port}/v1/logs"
        result = _invoke(
            "--duration-days", "1",
            "--interval-seconds", "60",
            "--otel-enabled",
            "--otel-logs-endpoint", endpoint,
            "--otel-stream-protocol", "json",
            "--otel-stream-speedup", "1000000",
            "--otel-stream-max-events", "1",
            "--otel-activity-log", str(log_target),
            "--output-dir", str(tmp_path / "activity_fail_run"),
        )
        assert result.returncode == 0, result.stderr
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert log_target.exists(), "activity log not written on failure path"
    contents = log_target.read_text()
    assert "RETRY" in contents
    assert "FAIL" in contents


def test_otel_activity_log_not_created_when_streaming_disabled(tmp_path):
    """No activity log when --otel-enabled is not passed (no streaming runs)."""
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    result = _invoke(
        "--duration-days", "1",
        "--interval-seconds", "60",
        "--output-dir", str(tmp_path / "no_stream_run"),
        cwd=str(cwd),
    )
    assert result.returncode == 0, result.stderr
    assert not (cwd / "otel-activity.log").exists(), \
        "activity log should not be created when streaming is disabled"


def test_otel_activity_log_records_send_per_attempt(tmp_path):
    """Each POST attempt (including retries) records its own SEND line."""
    class _Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            self.send_response(500)
            self.end_headers()

        def log_message(self, *args, **kwargs):  # noqa: D401, ANN002, ANN003
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    log_target = tmp_path / "attempts.log"
    try:
        endpoint = f"http://127.0.0.1:{server.server_port}/v1/logs"
        result = _invoke(
            "--duration-days", "1",
            "--interval-seconds", "60",
            "--otel-enabled",
            "--otel-logs-endpoint", endpoint,
            "--otel-stream-protocol", "json",
            "--otel-stream-speedup", "1000000",
            "--otel-stream-max-events", "1",
            "--otel-activity-log", str(log_target),
            "--output-dir", str(tmp_path / "activity_attempts_run"),
        )
        assert result.returncode == 0, result.stderr
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    contents = log_target.read_text()
    send_lines = [ln for ln in contents.splitlines() if " SEND " in ln]
    retry_lines = [ln for ln in contents.splitlines() if " RETRY " in ln]
    fail_lines = [ln for ln in contents.splitlines() if " FAIL " in ln]
    # max_retries default is 3 → 1 initial attempt + 3 retries = 4 sends, 3 retries, 1 fail
    assert len(send_lines) == len(retry_lines) + len(fail_lines), (
        f"expected one SEND per attempt; got {len(send_lines)} SEND, "
        f"{len(retry_lines)} RETRY, {len(fail_lines)} FAIL"
    )
    assert len(send_lines) >= 2, f"expected SEND on retries, got only {len(send_lines)}"


def test_otel_activity_log_values_with_spaces_are_quoted(tmp_path):
    """Field values containing spaces are escaped so each k=v token is parseable."""
    import shlex

    class _Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args, **kwargs):  # noqa: D401, ANN002, ANN003
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    log_target = tmp_path / "quoted.log"
    try:
        endpoint = f"http://127.0.0.1:{server.server_port}/v1/logs"
        result = _invoke(
            "--duration-days", "1",
            "--interval-seconds", "60",
            "--otel-enabled",
            "--otel-logs-endpoint", endpoint,
            "--otel-stream-protocol", "json",
            "--otel-stream-speedup", "1000000",
            "--otel-stream-max-events", "1",
            "--otel-activity-log", str(log_target),
            "--output-dir", str(tmp_path / "activity_quoted_run"),
        )
        assert result.returncode == 0, result.stderr
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    # Every SEND line must be shlex-parseable into a single timestamp, event,
    # and exactly the documented k=v tokens. The event_ts value contains a
    # space (YYYY-MM-DD HH:MM:SS), so unescaped writes would split it across
    # two tokens.
    for line in log_target.read_text().splitlines():
        if " SEND " not in line:
            continue
        tokens = shlex.split(line)
        keys = []
        for token in tokens[2:]:  # skip ISO timestamp + event name
            assert "=" in token, f"unparseable token {token!r} in line {line!r}"
            keys.append(token.split("=", 1)[0])
        assert "event_ts" in keys
        event_ts_value = next(t for t in tokens[2:] if t.startswith("event_ts="))
        # YYYY-MM-DD HH:MM:SS is 19 chars including the embedded space
        assert len(event_ts_value.split("=", 1)[1]) == 19, (
            f"event_ts not fully captured: {event_ts_value!r} in line {line!r}"
        )


def test_otel_stream_default_protocol_is_protobuf(tmp_path):
    content_types = []

    class _Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            content_types.append(self.headers.get("Content-Type"))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args, **kwargs):  # noqa: D401, ANN002, ANN003
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        endpoint = f"http://127.0.0.1:{server.server_port}/v1/logs"
        result = _invoke(
            "--duration-days", "1",
            "--interval-seconds", "60",
            "--otel-enabled",
            "--otel-logs-endpoint", endpoint,
            "--otel-stream-max-events", "1",
            "--output-dir", str(tmp_path / "stream_default_proto_run"),
        )
        if result.returncode != 0 and "requires opentelemetry-proto + protobuf" in result.stderr:
            return
        assert result.returncode == 0, result.stderr
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert content_types == ["application/x-protobuf"]


def _activity_log_send_lines(log_path):
    import shlex
    send_lines = []
    for line in log_path.read_text().splitlines():
        if " SEND " not in line:
            continue
        send_lines.append(shlex.split(line))
    return send_lines


def test_otel_verbose_off_by_default_omits_body_and_headers(tmp_path):
    """Without --otel-verbose, SEND records do not include raw body or headers."""
    class _Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args, **kwargs):  # noqa: D401, ANN002, ANN003
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    log_target = tmp_path / "non_verbose.log"
    try:
        endpoint = f"http://127.0.0.1:{server.server_port}/v1/logs"
        result = _invoke(
            "--duration-days", "1",
            "--interval-seconds", "60",
            "--otel-enabled",
            "--otel-logs-endpoint", endpoint,
            "--otel-stream-protocol", "json",
            "--otel-stream-speedup", "1000000",
            "--otel-stream-max-events", "1",
            "--otel-activity-log", str(log_target),
            "--output-dir", str(tmp_path / "verbose_off_run"),
        )
        assert result.returncode == 0, result.stderr
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    contents = log_target.read_text()
    assert " body=" not in contents, "SEND body should be omitted when --otel-verbose is off"
    assert " content_type=" not in contents, \
        "Content-Type header should be omitted when --otel-verbose is off"


def test_otel_verbose_includes_raw_body_in_send(tmp_path):
    """--otel-verbose adds the raw OTLP payload to SEND records."""
    class _Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args, **kwargs):  # noqa: D401, ANN002, ANN003
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    log_target = tmp_path / "verbose.log"
    try:
        endpoint = f"http://127.0.0.1:{server.server_port}/v1/logs"
        result = _invoke(
            "--duration-days", "1",
            "--interval-seconds", "60",
            "--otel-enabled",
            "--otel-verbose",
            "--otel-logs-endpoint", endpoint,
            "--otel-stream-protocol", "json",
            "--otel-stream-speedup", "1000000",
            "--otel-stream-max-events", "1",
            "--otel-activity-log", str(log_target),
            "--output-dir", str(tmp_path / "verbose_on_run"),
        )
        assert result.returncode == 0, result.stderr
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    send_lines = _activity_log_send_lines(log_target)
    assert send_lines, "expected at least one SEND record"
    for tokens in send_lines:
        kv = {t.split("=", 1)[0]: t.split("=", 1)[1] for t in tokens[2:] if "=" in t}
        assert "body" in kv, f"verbose SEND missing body field: {tokens}"
        assert "content_type" in kv, f"verbose SEND missing content_type field: {tokens}"
        parsed = json.loads(kv["body"])
        assert "resourceLogs" in parsed, f"verbose body not OTLP JSON: {kv['body']!r}"
        assert kv["content_type"] == "application/json"


def test_otel_verbose_includes_response_status_on_ok(tmp_path):
    """--otel-verbose records the HTTP response status on OK records."""
    class _Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            self.send_response(202)
            self.end_headers()

        def log_message(self, *args, **kwargs):  # noqa: D401, ANN002, ANN003
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    log_target = tmp_path / "ok_status.log"
    try:
        endpoint = f"http://127.0.0.1:{server.server_port}/v1/logs"
        result = _invoke(
            "--duration-days", "1",
            "--interval-seconds", "60",
            "--otel-enabled",
            "--otel-verbose",
            "--otel-logs-endpoint", endpoint,
            "--otel-stream-protocol", "json",
            "--otel-stream-speedup", "1000000",
            "--otel-stream-max-events", "1",
            "--otel-activity-log", str(log_target),
            "--output-dir", str(tmp_path / "verbose_ok_run"),
        )
        assert result.returncode == 0, result.stderr
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    import shlex
    ok_lines = [
        shlex.split(line)
        for line in log_target.read_text().splitlines()
        if " OK " in line
    ]
    assert ok_lines, "expected at least one OK record"
    for tokens in ok_lines:
        kv = {t.split("=", 1)[0]: t.split("=", 1)[1] for t in tokens[2:] if "=" in t}
        assert kv.get("status") == "202", f"verbose OK missing status=202: {tokens}"


def test_otel_verbose_includes_error_type_on_fail(tmp_path):
    """--otel-verbose adds the exception type to RETRY and FAIL records."""
    class _Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            self.send_response(500)
            self.end_headers()

        def log_message(self, *args, **kwargs):  # noqa: D401, ANN002, ANN003
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    log_target = tmp_path / "fail_verbose.log"
    try:
        endpoint = f"http://127.0.0.1:{server.server_port}/v1/logs"
        result = _invoke(
            "--duration-days", "1",
            "--interval-seconds", "60",
            "--otel-enabled",
            "--otel-verbose",
            "--otel-logs-endpoint", endpoint,
            "--otel-stream-protocol", "json",
            "--otel-stream-speedup", "1000000",
            "--otel-stream-max-events", "1",
            "--otel-activity-log", str(log_target),
            "--output-dir", str(tmp_path / "verbose_fail_run"),
        )
        assert result.returncode == 0, result.stderr
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    import shlex
    contents = log_target.read_text()
    fail_lines = [
        shlex.split(line) for line in contents.splitlines() if " FAIL " in line
    ]
    retry_lines = [
        shlex.split(line) for line in contents.splitlines() if " RETRY " in line
    ]
    assert fail_lines, "expected at least one FAIL record"
    assert retry_lines, "expected at least one RETRY record"
    for tokens in fail_lines + retry_lines:
        kv = {t.split("=", 1)[0]: t.split("=", 1)[1] for t in tokens[2:] if "=" in t}
        assert "error_type" in kv, f"verbose record missing error_type: {tokens}"
        assert "HTTPError" in kv["error_type"], (
            f"unexpected error_type: {kv['error_type']!r}"
        )


def test_otel_verbose_masks_auth_token(tmp_path):
    """Auth tokens are masked in verbose header logging to avoid leaking secrets."""
    class _Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args, **kwargs):  # noqa: D401, ANN002, ANN003
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    log_target = tmp_path / "auth_verbose.log"
    secret_token = "supersecrettoken123"
    try:
        endpoint = f"http://127.0.0.1:{server.server_port}/v1/logs"
        result = _invoke(
            "--duration-days", "1",
            "--interval-seconds", "60",
            "--otel-enabled",
            "--otel-verbose",
            "--otel-logs-endpoint", endpoint,
            "--otel-logs-auth-token", secret_token,
            "--otel-stream-protocol", "json",
            "--otel-stream-speedup", "1000000",
            "--otel-stream-max-events", "1",
            "--otel-activity-log", str(log_target),
            "--output-dir", str(tmp_path / "verbose_auth_run"),
        )
        assert result.returncode == 0, result.stderr
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    contents = log_target.read_text()
    assert secret_token not in contents, \
        "auth token must never appear verbatim in the activity log"
    assert "authorization=" in contents.lower(), \
        "verbose mode should still record that an authorization header was sent"
