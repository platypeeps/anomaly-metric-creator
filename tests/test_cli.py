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
                 "--interval-seconds", "--emit-selection", "--otel-stream-endpoint",
                 "--otel-stream-auth-token", "--otel-stream-auth-token-env",
                 "--otel-stream-auth-scheme"):
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


def test_combine_requires_metrics_selection(tmp_path):
    out = tmp_path / "combine_no_metrics"
    result = _invoke(
        "--combine",
        "--emit-selection", "logs,traces",
        "--output-dir", str(out),
    )
    assert result.returncode != 0, "expected non-zero exit when --combine excludes metrics"
    assert "combine" in (result.stderr + result.stdout)


def test_invalid_otel_stream_endpoint_scheme_fails(tmp_path):
    result = _invoke(
        "--otel-stream-endpoint", "localhost:4318/v1/logs",
        "--output-dir", str(tmp_path),
    )
    assert result.returncode != 0, "expected non-zero exit for invalid otel endpoint scheme"
    assert "otel-stream-endpoint" in (result.stderr + result.stdout)


def test_invalid_otel_stream_speedup_fails(tmp_path):
    result = _invoke(
        "--otel-stream-endpoint", "http://localhost:4318/v1/logs",
        "--otel-stream-speedup", "0",
        "--output-dir", str(tmp_path),
    )
    assert result.returncode != 0, "expected non-zero exit for --otel-stream-speedup 0"
    assert "otel-stream-speedup" in (result.stderr + result.stdout)


def test_invalid_otel_stream_protocol_fails(tmp_path):
    result = _invoke(
        "--otel-stream-endpoint", "http://localhost:4318/v1/logs",
        "--otel-stream-protocol", "xml",
        "--output-dir", str(tmp_path),
    )
    assert result.returncode != 0, "expected non-zero exit for invalid --otel-stream-protocol"
    assert "otel-stream-protocol" in (result.stderr + result.stdout)


def test_otel_stream_posts_events_to_endpoint(tmp_path):
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
        endpoint = f"http://127.0.0.1:{server.server_port}/v1/logs"
        result = _invoke(
            "--duration-days", "1",
            "--interval-seconds", "60",
            "--otel-stream-endpoint", endpoint,
            "--otel-stream-speedup", "1000000",
            "--otel-stream-max-events", "3",
            "--output-dir", str(tmp_path / "stream_run"),
        )
        assert result.returncode == 0, result.stderr
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert len(received) == 3, f"expected 3 streamed events, got {len(received)}"
    for path, payload in received:
        assert path == "/v1/logs"
        assert "resourceLogs" in payload


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
            "--otel-stream-endpoint", endpoint,
            "--otel-stream-speedup", "1000000",
            "--otel-stream-max-events", "1",
            "--output-dir", str(tmp_path / "stream_fail_run"),
        )
        assert result.returncode == 0, result.stderr
        assert "WARNING: OTEL stream" in result.stderr
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert len(attempts) == 4, f"expected 4 total attempts (1 initial + 3 retries), got {len(attempts)}"


def test_otel_stream_sends_auth_header_from_env(tmp_path, monkeypatch):
    auth_headers = []
    monkeypatch.setenv("OTEL_TEST_TOKEN", "secret-token")

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
            "--otel-stream-endpoint", endpoint,
            "--otel-stream-auth-token-env", "OTEL_TEST_TOKEN",
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
            "--otel-stream-endpoint", endpoint,
            "--otel-stream-auth-token", "direct-token",
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
        monkeypatch.setenv("OTEL_STREAM_ENDPOINT", endpoint)
        monkeypatch.setenv("OTEL_STREAM_AUTH_TOKEN", "env-token")
        monkeypatch.setenv("OTEL_STREAM_AUTH_SCHEME", "Token")
        result = _invoke(
            "--duration-days", "1",
            "--interval-seconds", "60",
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
            "--otel-stream-endpoint", endpoint,
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
            "--otel-stream-endpoint", endpoint,
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
