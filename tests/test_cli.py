"""CLI surface tests via subprocess.

Each test invokes ``anomaly-metric-creator.py`` as an external process so we
also exercise the ``if __name__ == "__main__"`` entry and prove no in-process
state is leaking determinism.
"""

import filecmp
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from conftest import COMPONENTS, SCRIPT_PATH


def _invoke(*args, cwd=None, env=None):
    return subprocess.run(
        [sys.executable, str(SCRIPT_PATH), *args],
        capture_output=True,
        text=True,
        cwd=cwd,
        env=env,
    )


def test_help_lists_every_flag():
    result = _invoke("--help")
    assert result.returncode == 0, result.stderr
    out = result.stdout
    for flag in ("--duration-days", "--seed", "--output-dir", "--drop-rate",
                 "--interval-seconds", "--emit-selection", "--components",
                 "--scenarios", "--exclude-scenarios",
                 "--signal-level", "--anomaly-count",
                 "--metrics-per-component",
                 "--otel-enabled", "--otel-disabled",
                 "--otel-logs-endpoint", "--otel-logs-auth-token",
                 "--otel-metrics-endpoint", "--otel-metrics-auth-token",
                 "--otel-traces-endpoint", "--otel-traces-auth-token",
                 "--otel-stream-auth-scheme",
                 "--otel-gauges-only",
                 "--otel-verbose", "--no-otel-verbose"):
        assert flag in out, f"--help missing flag {flag}"
        # Argparse renders the help text on the line following the flag; require
        # something non-trivial follows so the flag isn't just a bare token.
        after = out.split(flag, 1)[1]
        assert any(c.isalpha() for c in after[:200]), f"{flag} has empty help text"


def test_missing_numpy_dependency_has_actionable_error(tmp_path):
    sitecustomize = tmp_path / "sitecustomize.py"
    sitecustomize.write_text(
        "import importlib.abc\n"
        "\n"
        "class _BlockNumpy(importlib.abc.MetaPathFinder):\n"
        "    def find_spec(self, fullname, path=None, target=None):\n"
        "        if fullname == 'numpy' or fullname.startswith('numpy.'):\n"
        "            raise ModuleNotFoundError(\"No module named 'numpy'\")\n"
        "        return None\n"
        "\n"
        "import sys\n"
        "sys.meta_path.insert(0, _BlockNumpy())\n"
    )

    env = {**os.environ, "PYTHONPATH": str(tmp_path)}
    result = _invoke("--help", env=env)

    assert result.returncode != 0
    assert "Missing required dependency: numpy" in result.stderr
    assert "python3 -m pip install -e ." in result.stderr


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
            "--otel-activity-log", str(tmp_path / "otel-activity.log"),
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


def test_anomaly_count_preserves_manifest_row_order(tmp_path):
    """Manifest row order is identical (not just same set) across two
    same-seed runs with --anomaly-count. Guards against accidentally
    iterating a Python set, whose hash-iteration order is not stable
    across CPython versions / PYTHONHASHSEED variation.
    """
    out_a = tmp_path / "order_a"
    out_b = tmp_path / "order_b"
    args = [
        "--duration-days", "1",
        "--interval-seconds", "60",
        "--drop-rate", "0",
        "--anomaly-count", "10",
        "--seed", "42",
    ]
    r1 = _invoke(*args, "--output-dir", str(out_a))
    r2 = _invoke(*args, "--output-dir", str(out_b))
    assert r1.returncode == 0, r1.stderr
    assert r2.returncode == 0, r2.stderr
    with open(out_a / "anomalies.csv") as f_a, open(out_b / "anomalies.csv") as f_b:
        assert f_a.read() == f_b.read(), \
            "manifest row order must match exactly across same-seed runs"


def test_anomaly_count_still_warns_for_out_of_range_specs(tmp_path):
    """--anomaly-count must not silently suppress the generator's stderr
    soft-skip warning for out-of-range specs (e.g. multi-day specs whose
    time_offset falls past the end of the requested duration).

    Uses --duration-days 2 so partial multi-day scenarios such as
    cache_leak_restart (days_required=2, specs spanning Day 2-4) and
    db_disk_exhaustion (days_required=2, specs spanning Day 2-6) are
    active (their gate passes) but their Day 3+ specs fall out of
    range — which is exactly the generator's "time_offset outside"
    path. A bare "skipped" substring would also be satisfied by the
    scenario-gate warnings emitted by _resolve_scenarios, so the
    assertion looks for the generator-specific warning text.
    """
    out = tmp_path / "count_with_oor"
    result = _invoke(
        "--duration-days", "2",
        "--interval-seconds", "60",
        "--drop-rate", "0",
        "--anomaly-count", "100000",
        "--output-dir", str(out),
    )
    assert result.returncode == 0, result.stderr
    assert "time_offset outside" in result.stderr, (
        "expected generator-emitted soft-skip warning (`time_offset outside`) "
        f"even with --anomaly-count set; got: {result.stderr!r}"
    )


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
            "--otel-activity-log", str(tmp_path / "otel-activity.log"),
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
            "--otel-activity-log", str(tmp_path / "otel-activity.log"),
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
            "--otel-activity-log", str(tmp_path / "otel-activity.log"),
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
            "--otel-activity-log", str(tmp_path / "otel-activity.log"),
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
            "--otel-activity-log", str(tmp_path / "otel-activity.log"),
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
            "--otel-activity-log", str(tmp_path / "otel-activity.log"),
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


@pytest.mark.parametrize("verbose", [False, True])
def test_otel_http_error_activity_log_includes_response_headers(
    amc, tmp_path, capsys, verbose
):
    """HTTP receiver failures log response headers — and, only under
    ``verbose=True``, the request payload — including CF-Ray.

    The sensitive ``Set-Cookie`` and ``Authorization`` headers that
    Cloudflare-style intermediaries can echo on a 4xx response must be
    masked before they reach the on-disk activity log; the
    ``CF-Ray`` / ``X-Debug-Header`` diagnostic pair survives so the
    failure record stays useful. The raw ``request_body`` follows the
    ``--otel-verbose`` contract: present in verbose failure records,
    absent otherwise.
    """
    class _Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            self.send_response(403)
            self.send_header("CF-Ray", "test-ray-123")
            self.send_header("X-Debug-Header", "visible")
            self.send_header("Set-Cookie", "session=plaintext-cookie; Secure")
            self.send_header("Authorization", "Bearer echoed-token-abc")
            self.send_header("X-Api-Key", "sk_live_super_secret")
            self.end_headers()

        def log_message(self, *args, **kwargs):  # noqa: D401, ANN002, ANN003
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    log_target = tmp_path / "http_error.log"
    thread.start()
    try:
        sent = amc.stream_otel_signals(
            {"metrics": f"http://127.0.0.1:{server.server_port}/v1/metrics"},
            [{
                "timestamp": "2026-03-10 00:00:00",
                "component": "database",
                "metric": "write_latency_ms",
                "description": "Synthetic failure for header logging",
            }],
            speedup=1000000.0,
            timeout_seconds=2.0,
            max_events=1,
            max_retries=0,
            auth_headers=None,
            protocol="json",
            activity_log_path=log_target,
            verbose=verbose,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    captured = capsys.readouterr()
    assert sent == 0
    assert "CF-Ray: test-ray-123" not in captured.err
    assert "X-Debug-Header: visible" not in captured.err
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
    assert kv["cf_ray"] == "test-ray-123"
    log_text = log_target.read_text()
    response_headers = json.loads(kv["response_headers"])
    assert ["CF-Ray", "test-ray-123"] in response_headers
    assert ["X-Debug-Header", "visible"] in response_headers
    # Sensitive headers must reach the activity log only in their
    # redacted form. Asserting both the (name, "***") pair is present
    # AND the plaintext value is absent from the full log file
    # catches a regression where the redaction is skipped or
    # bypassed by a future logging path.
    assert ["Set-Cookie", "***"] in response_headers
    assert ["Authorization", "Bearer ***"] in response_headers
    assert ["X-Api-Key", "***"] in response_headers
    assert "plaintext-cookie" not in log_text
    assert "echoed-token-abc" not in log_text
    assert "sk_live_super_secret" not in log_text
    if verbose:
        assert '"resourceMetrics"' in kv["request_body"]
        assert '"write_latency_ms"' in kv["request_body"]
    else:
        assert "request_body" not in kv, (
            "non-verbose FAIL records must not carry the raw request "
            "payload (--otel-verbose contract)"
        )


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
            "--otel-activity-log", str(tmp_path / "otel-activity.log"),
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


def _read_csv_header(path):
    import csv as _csv
    with open(path) as f:
        reader = _csv.reader(f)
        return next(reader)


def test_metrics_per_component_default_matches_legacy_columns(tmp_path):
    """Without --metrics-per-component, each component CSV has exactly the
    historic per-component column count (timestamp + DEFAULT_METRIC_COUNT)."""
    from conftest import DEFAULT_METRIC_COUNT
    out = tmp_path / "default_cols"
    result = _invoke(
        "--duration-days", "1",
        "--interval-seconds", "60",
        "--output-dir", str(out),
    )
    assert result.returncode == 0, result.stderr
    for component, expected in DEFAULT_METRIC_COUNT.items():
        header = _read_csv_header(out / f"{component}.csv")
        # header = ["timestamp", ...metric names]
        assert len(header) - 1 == expected, (
            f"{component}: header has {len(header)-1} metric columns, "
            f"expected {expected}"
        )


def test_metrics_per_component_three_trims_every_component(tmp_path):
    """--metrics-per-component 3 emits exactly 3 metric columns per component."""
    from conftest import COMPONENTS as _COMPONENTS
    out = tmp_path / "metrics_3"
    result = _invoke(
        "--duration-days", "1",
        "--interval-seconds", "60",
        "--metrics-per-component", "3",
        "--output-dir", str(out),
    )
    assert result.returncode == 0, result.stderr
    for component in _COMPONENTS:
        header = _read_csv_header(out / f"{component}.csv")
        assert len(header) - 1 == 3, (
            f"{component}: header has {len(header)-1} metric columns, expected 3"
        )


def test_metrics_per_component_ten_emits_ten_columns(tmp_path):
    """--metrics-per-component 10 emits exactly 10 metric columns per component."""
    from conftest import COMPONENTS as _COMPONENTS
    out = tmp_path / "metrics_10"
    result = _invoke(
        "--duration-days", "1",
        "--interval-seconds", "60",
        "--metrics-per-component", "10",
        "--output-dir", str(out),
    )
    assert result.returncode == 0, result.stderr
    for component in _COMPONENTS:
        header = _read_csv_header(out / f"{component}.csv")
        assert len(header) - 1 == 10, (
            f"{component}: header has {len(header)-1} metric columns, expected 10"
        )


def test_metrics_per_component_columns_are_prefix_of_full_set(tmp_path):
    """The columns at --metrics-per-component N are the first N entries of
    each component's full 10-metric ordering — proving highest-value-first."""
    from conftest import COMPONENTS as _COMPONENTS
    out_three = tmp_path / "prefix_3"
    out_ten = tmp_path / "prefix_10"
    for cap, out in (("3", out_three), ("10", out_ten)):
        result = _invoke(
            "--duration-days", "1",
            "--interval-seconds", "60",
            "--metrics-per-component", cap,
            "--output-dir", str(out),
        )
        assert result.returncode == 0, result.stderr
    for component in _COMPONENTS:
        header_three = _read_csv_header(out_three / f"{component}.csv")
        header_ten = _read_csv_header(out_ten / f"{component}.csv")
        # First metric column should match (skip "timestamp")
        assert header_three[1:] == header_ten[1:4], (
            f"{component}: --metrics-per-component 3 columns "
            f"{header_three[1:]} are not the prefix of 10-metric columns "
            f"{header_ten[1:4]}"
        )


def test_metrics_per_component_invalid_value_fails(tmp_path):
    """--metrics-per-component 0 / 11 should be rejected with a usage error."""
    for bad in ("0", "11", "-1"):
        result = _invoke(
            "--metrics-per-component", bad,
            "--output-dir", str(tmp_path / f"bad_{bad}"),
        )
        assert result.returncode != 0, \
            f"expected non-zero exit for --metrics-per-component {bad}"
        assert "metrics-per-component" in (result.stderr + result.stdout)


def test_metrics_per_component_filters_anomalies_targeting_dropped_metrics(tmp_path):
    """Anomalies that target metrics dropped by --metrics-per-component are
    filtered out instead of crashing the generator. The remaining manifest
    rows reference only the trimmed metric set, AND at least one anomaly
    targeting a kept metric survives (guards against over-filtering: a bug
    that dropped every anomaly would otherwise pass this test silently)."""
    import csv as _csv
    from conftest import COMPONENTS as _COMPONENTS
    out = tmp_path / "trim_anomalies"
    result = _invoke(
        "--duration-days", "1",
        "--interval-seconds", "60",
        "--metrics-per-component", "2",
        "--output-dir", str(out),
    )
    assert result.returncode == 0, result.stderr
    # Per-component allowed metric sets
    allowed = {}
    for component in _COMPONENTS:
        header = _read_csv_header(out / f"{component}.csv")
        allowed[component] = set(header[1:])
    with open(out / "anomalies.csv") as f:
        rows = list(_csv.DictReader(f))
    assert rows, (
        "anomalies.csv is empty under --metrics-per-component 2; the filter "
        "is over-filtering eligible anomalies"
    )
    for row in rows:
        assert row["metric"] in allowed[row["component"]], (
            f"manifest references metric {row['metric']} not emitted by "
            f"{row['component']} (allowed: {sorted(allowed[row['component']])})"
        )
    # authservice keeps active_sessions and login_attempts at N=2; one or
    # both of those is the target of multiple in-range primary anomalies, so
    # at least one entry for those metrics must survive.
    auth_metrics = {r["metric"] for r in rows if r["component"] == "authservice"}
    assert auth_metrics & {"active_sessions", "login_attempts"}, (
        "no authservice anomaly survived for either retained metric "
        f"(active_sessions, login_attempts); got {auth_metrics}"
    )


# ==================================================================
# CLI surface coverage for --scenarios / --exclude-scenarios
# (subprocess-level — complements parse_args-only tests in test_args.py
# and the in-process composition tests in test_scenarios.py).
# ==================================================================


def test_cli_scenarios_unknown_slug_exits_nonzero(tmp_path):
    """``--scenarios <unknown>`` exits non-zero and the error names the bad
    slug along with the catalog. Subprocess-level so the
    ``if __name__ == "__main__"`` path is exercised end-to-end.
    """
    result = _invoke(
        "--scenarios", "not_a_scenario",
        "--output-dir", str(tmp_path),
    )
    assert result.returncode != 0, (
        f"expected non-zero exit; stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    err = result.stderr + result.stdout
    assert "not_a_scenario" in err, f"error must name bad slug; got: {err!r}"
    # Catalog hint is present so the user can pick a valid slug.
    assert "Allowed:" in err or "allowed" in err.lower(), (
        f"error must advertise the catalog; got: {err!r}"
    )


def test_cli_exclude_scenarios_unknown_slug_exits_nonzero(tmp_path):
    result = _invoke(
        "--exclude-scenarios", "not_a_scenario",
        "--output-dir", str(tmp_path),
    )
    assert result.returncode != 0, (
        f"expected non-zero exit; stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    err = result.stderr + result.stdout
    assert "not_a_scenario" in err, f"error must name bad slug; got: {err!r}"


def test_cli_scenarios_all_plus_explicit_slug_exits_nonzero(tmp_path):
    """``--scenarios all,<slug>`` exits non-zero with a mutual-exclusion error
    message. The 'all' sentinel cannot be combined with explicit slugs.
    """
    result = _invoke(
        "--scenarios", "all,cache_leak_restart",
        "--output-dir", str(tmp_path),
    )
    assert result.returncode != 0, (
        f"expected non-zero exit; stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    err = result.stderr + result.stdout
    assert "mutually exclusive" in err, (
        f"error must mention mutual exclusion; got: {err!r}"
    )


def test_cli_scenarios_single_slug_runs_end_to_end(tmp_path):
    """``--scenarios <slug>`` succeeds end-to-end via the ``__main__``
    entry: exit zero, ``anomalies.csv`` exists with at least one row.
    Reading the manifest catches the case where the subprocess exits 0
    but produces an empty manifest (e.g. a future regression that
    silently drops every spec for the chosen slug).
    """
    result = _invoke(
        "--scenarios", "auth_brute_force",
        "--duration-days", "1",
        "--drop-rate", "0",
        "--interval-seconds", "60",
        "--output-dir", str(tmp_path),
    )
    assert result.returncode == 0, (
        f"expected zero exit; stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    import csv as _csv
    manifest_path = tmp_path / "anomalies.csv"
    assert manifest_path.exists()
    with open(manifest_path) as f:
        rows = list(_csv.DictReader(f))
    assert rows, (
        "expected --scenarios auth_brute_force to produce at least one "
        "manifest row; got an empty anomalies.csv"
    )


def test_cli_exclude_scenarios_single_slug_runs_end_to_end(tmp_path):
    """``--exclude-scenarios <slug>`` succeeds end-to-end via the
    ``__main__`` entry. Asserts the manifest has at least one row from
    the non-excluded scenarios so the test proves output was actually
    produced, not just that the file was created.
    """
    result = _invoke(
        "--exclude-scenarios", "monday_baseline",
        "--duration-days", "1",
        "--drop-rate", "0",
        "--interval-seconds", "60",
        "--output-dir", str(tmp_path),
    )
    assert result.returncode == 0, (
        f"expected zero exit; stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    import csv as _csv
    manifest_path = tmp_path / "anomalies.csv"
    assert manifest_path.exists()
    with open(manifest_path) as f:
        rows = list(_csv.DictReader(f))
    assert rows, (
        "expected --exclude-scenarios monday_baseline to produce at least "
        "one manifest row from the remaining scenarios; got an empty "
        "anomalies.csv"
    )
