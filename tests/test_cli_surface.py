"""Canonical CLI surface (the post-flag-day consolidated flag-set).

Covers the consolidated surface introduced by the CLI streamline and
finalized at the post-phase-9 flag day:

- ``--emit`` (with the ``combined`` token) — the only artifact selector
  (the ``--emit-selection`` / ``--combine`` aliases were removed);
- the ``combine`` / ``validate`` subcommands — the only combine/validate
  entry points (``--combine-only`` / ``--validate-output`` removed);
- ``--otel-send`` + ``--otel-endpoint`` / ``--otel-auth-token`` — the
  only OTEL surface (the five toggles and the per-signal sextet
  removed);
- removed alias spellings fail argparse as unrecognized arguments (the
  per-flag matrix lives in ``tests/test_args.py``); the canonical
  surface never emits ``DEPRECATION:`` noise (the notice mechanism is
  gone from the script entirely).

Byte-equivalence pins run at the cheap 600s interval — the compared
entry points (inline ``--emit ...,combined`` emission vs. the post-hoc
``combine`` subcommand) drive the same combine writer over the same
generated inputs, so full-resolution runs would prove nothing extra.
"""

import contextlib
import io
import subprocess
import sys

import pytest

from conftest import SCRIPT_PATH, run_capture, sha256_path


def _invoke(*args):
    return subprocess.run(
        [sys.executable, str(SCRIPT_PATH), *args],
        capture_output=True,
        text=True,
    )


def _parse(amc, argv):
    """parse_args with captured stderr; returns (namespace, stderr)."""
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        args = amc.parse_args(argv)
    return args, buf.getvalue()


def _parse_error(amc, argv):
    """Run parse_args expecting SystemExit; returns captured stderr."""
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        with pytest.raises(SystemExit):
            amc.parse_args(argv)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# --emit
# ---------------------------------------------------------------------------


def test_emit_canonical_parse(amc):
    canonical, w = _parse(amc, ["--emit", "metrics,logs", "--output-dir", "x"])
    assert canonical.emit_selection == {"metrics", "logs"}
    assert "DEPRECATION" not in w, "canonical flag must not warn"


def test_emit_combined_token_sets_combine(amc):
    args, _ = _parse(amc, ["--emit", "metrics,combined", "--output-dir", "x"])
    assert args.combine is True
    assert args.emit_selection == {"metrics"}


def test_emit_combined_byte_identical_to_combine_subcommand(amc, tmp_path):
    """--emit metrics,combined produces a combined_metrics_unified.csv
    byte-identical to generating with --emit metrics and running the
    ``combine`` subcommand over the output directory afterwards."""
    out_inline = tmp_path / "inline"
    out_post = tmp_path / "post"
    run_capture(amc, out_inline, days=1,
                extra_args=["--emit", "metrics,combined"])
    run_capture(amc, out_post, days=1, extra_args=["--emit", "metrics"])
    amc.main(["combine", str(out_post)])
    for name in ("combined_metrics_unified.csv", "apigateway.csv",
                 "anomalies.csv"):
        assert sha256_path(out_inline / name) == sha256_path(out_post / name), name


@pytest.mark.parametrize("bad", [
    ["--emit", "metrics", "--combine"],
    ["--emit", "metrics", "--emit-selection", "logs"],
    ["--emit", "metrics", "--emit-selection=logs"],
])
def test_emit_mixed_with_removed_aliases_is_unrecognized(amc, bad):
    """The flag day removed the aliases outright, so the historic
    canonical/alias mixing gate is gone too — mixing now fails argparse
    as a plain unrecognized argument."""
    err = _parse_error(amc, bad + ["--output-dir", "x"])
    assert "unrecognized arguments" in err


def test_emit_combined_alone_rejected(amc):
    err = _parse_error(amc, ["--emit", "combined", "--output-dir", "x"])
    assert "combined" in err


def test_emit_invalid_token_rejected(amc):
    err = _parse_error(amc, ["--emit", "metrics,bogus", "--output-dir", "x"])
    assert "bogus" in err


# ---------------------------------------------------------------------------
# combine / validate subcommands
# ---------------------------------------------------------------------------


def test_combine_subcommand_rejects_missing_directory(amc):
    with pytest.raises(SystemExit):
        with contextlib.redirect_stderr(io.StringIO()):
            amc.main(["combine", "/nonexistent/run/dir"])


def test_validate_subcommand_clean_run(amc, tmp_path, capsys):
    out = tmp_path / "run"
    run_capture(amc, out, days=1, extra_args=["--emit", "metrics,schema"])
    amc.main(["validate", str(out)])
    assert "OK (no violations)" in capsys.readouterr().out


def test_validate_subcommand_exit_one_on_violation(amc, tmp_path):
    out = tmp_path / "run"
    run_capture(amc, out, days=1, extra_args=["--emit", "metrics,schema"])
    (out / "stray_file.txt").write_text("not declared\n")
    with pytest.raises(SystemExit) as exc_info:
        with contextlib.redirect_stderr(io.StringIO()):
            amc.main(["validate", str(out)])
    assert exc_info.value.code == 1


def test_validate_subcommand_warn_exits_zero(amc, tmp_path):
    out = tmp_path / "run"
    run_capture(amc, out, days=1, extra_args=["--emit", "metrics,schema"])
    (out / "stray_file.txt").write_text("not declared\n")
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        amc.main(["validate", str(out), "--warn"])  # returns, no SystemExit
    assert "violation(s)" in buf.getvalue()


def test_generate_token_equivalent_to_bare_invocation(amc, tmp_path):
    bare = tmp_path / "bare"
    token = tmp_path / "token"
    run_capture(amc, bare, days=1, extra_args=["--emit", "metrics"])
    # ``generate`` is stripped by the dispatcher; drive main() directly so
    # the dispatch path itself is exercised.
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        amc.main(["generate", "--seed", "42", "--duration-days", "1",
                  "--interval-seconds", "60.0", "--emit", "metrics",
                  "--output-dir", str(token)])
    assert sha256_path(bare / "apigateway.csv") == \
        sha256_path(token / "apigateway.csv")


# ---------------------------------------------------------------------------
# --otel-send / --otel-endpoint / --otel-auth-token
# ---------------------------------------------------------------------------


def test_otel_send_gauges_only_mapping(amc):
    args, w = _parse(amc, ["--otel-send", "gauges",
                           "--otel-endpoint", "http://h:4318",
                           "--output-dir", "x"])
    assert args.otel_enabled and args.otel_emit_gauges and args.otel_gauges_only
    assert args.otel_metrics_endpoint == "http://h:4318/v1/metrics"
    # Unselected signals must not stream — even their env-var defaults
    # are overridden by the authoritative --otel-send selection.
    assert args.otel_logs_endpoint is None
    assert args.otel_traces_endpoint is None
    assert "DEPRECATION" not in w


def test_otel_send_signal_subset_derives_only_selected_endpoints(amc):
    args, _ = _parse(amc, ["--otel-send", "logs,traces",
                           "--otel-endpoint", "http://h:4318/",
                           "--otel-auth-token", "tok",
                           "--output-dir", "x"])
    assert args.otel_enabled
    assert not args.otel_emit_gauges and not args.otel_gauges_only
    assert args.otel_logs_endpoint == "http://h:4318/v1/logs"
    assert args.otel_traces_endpoint == "http://h:4318/v1/traces"
    assert args.otel_metrics_endpoint is None
    assert args.otel_logs_auth_token == "tok"
    assert args.otel_traces_auth_token == "tok"


def test_otel_send_all_expands_to_every_signal(amc):
    args, _ = _parse(amc, ["--otel-send", "all",
                           "--otel-endpoint", "http://h:1",
                           "--output-dir", "x"])
    assert args.otel_enabled and args.otel_emit_gauges
    assert not args.otel_gauges_only
    assert args.otel_logs_endpoint and args.otel_metrics_endpoint \
        and args.otel_traces_endpoint


def test_otel_send_none_is_explicit_off(amc, monkeypatch):
    """'none' overrides even an env-var endpoint default — the canonical
    replacement for the deprecated --otel-disabled escape hatch."""
    monkeypatch.setenv("MEZMO_OTEL_LOGS_ENDPOINT", "http://env:1/v1/logs")
    args, _ = _parse(amc, ["--otel-send", "none", "--output-dir", "x"])
    assert not args.otel_enabled


@pytest.mark.parametrize("bad,needle", [
    # Removed alias toggles mixed into a canonical invocation fail as
    # plain unrecognized arguments (the mixing gate died with the
    # aliases at the flag day).
    (["--otel-send", "logs", "--otel-enabled"], "unrecognized arguments"),
    (["--otel-send", "none", "--otel-gauges-only"], "unrecognized arguments"),
    (["--otel-send", "logs"], "--otel-endpoint"),
    (["--otel-endpoint", "http://h:1"], "--otel-send"),
    (["--otel-auth-token", "t"], "--otel-send"),
    (["--otel-send", "none,logs"], "none"),
    (["--otel-send", "bogus", "--otel-endpoint", "http://h:1"], "bogus"),
    (["--otel-send", "logs", "--otel-endpoint", "ftp://h:1"], "http"),
])
def test_otel_canonical_gates(amc, bad, needle):
    err = _parse_error(amc, bad + ["--output-dir", "x"])
    assert needle in err, err


# ---------------------------------------------------------------------------
# Deprecation machinery removal
# ---------------------------------------------------------------------------


def test_canonical_surface_never_warns(amc, tmp_path):
    _, w = _parse(amc, [
        "--emit", "metrics,schema,combined",
        "--otel-send", "gauges", "--otel-endpoint", "http://h:1",
        "--otel-auth-token", "t",
        "--output-dir", str(tmp_path),
    ])
    assert "DEPRECATION" not in w


def test_deprecation_notice_mechanism_fully_removed(amc):
    """The flag day removed the DEPRECATION stderr-notice machinery
    wholesale: the literal never appears in the script source, the
    alias->replacement map is gone, and _reconcile_cli_surface no
    longer takes the raw-argv scan parameter."""
    import inspect

    assert "DEPRECATION" not in SCRIPT_PATH.read_text(encoding="utf-8")
    assert not hasattr(amc, "_DEPRECATED_FLAGS")
    params = inspect.signature(amc._reconcile_cli_surface).parameters
    assert list(params) == ["p", "args"]


def test_subcommands_do_not_warn(amc, tmp_path, capsys):
    """Canonical subcommand invocations are structurally free of
    deprecation noise — they carry dedicated parsers and never route
    through parse_args."""
    out = tmp_path / "run"
    run_capture(amc, out, days=1, extra_args=["--emit", "metrics,schema"])
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        amc.main(["validate", str(out)])
        amc.main(["combine", str(out)])
    assert "DEPRECATION" not in buf.getvalue()


# ---------------------------------------------------------------------------
# Subprocess-level smoke for the new flags (end-to-end argv handling)
# ---------------------------------------------------------------------------


def test_subprocess_emit_and_subcommands_roundtrip(tmp_path):
    out = tmp_path / "run"
    gen = _invoke("--seed", "42", "--duration-days", "1",
                  "--interval-seconds", "600",
                  "--emit", "metrics,schema,combined",
                  "--output-dir", str(out))
    assert gen.returncode == 0, gen.stderr
    assert "DEPRECATION" not in gen.stderr
    assert (out / "combined_metrics_unified.csv").exists()
    val = _invoke("validate", str(out))
    assert val.returncode == 0, val.stderr
    assert "OK (no violations)" in val.stdout


def test_subcommand_directory_errors_distinguish_missing_from_file(amc, tmp_path):
    """A path that exists but is a file gets a 'not a directory' error,
    not a misleading 'does not exist' (Copilot review on PR #101)."""
    not_a_dir = tmp_path / "file.txt"
    not_a_dir.write_text("x\n")
    for sub in ("combine", "validate"):
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            with pytest.raises(SystemExit):
                amc.main([sub, str(not_a_dir)])
        assert "exists but is not one" in buf.getvalue(), (sub, buf.getvalue())
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            with pytest.raises(SystemExit):
                amc.main([sub, str(tmp_path / "missing")])
        assert "does not exist" in buf.getvalue(), (sub, buf.getvalue())


def test_otel_send_clears_env_endpoint_and_token_for_unselected_signal(
        amc, monkeypatch):
    """--otel-send is authoritative: env-var endpoint AND auth-token
    defaults for unselected signals are cleared, so a
    configured-but-unselected signal cannot leak into the stream and a
    dangling credential is not carried in the namespace (matching the
    stricter clearing of the 'none' branch)."""
    monkeypatch.setenv("MEZMO_OTEL_LOGS_ENDPOINT", "http://env:1/v1/logs")
    monkeypatch.setenv("MEZMO_OTEL_LOGS_AUTH_TOKEN", "logs-token")
    monkeypatch.setenv("MEZMO_OTEL_METRICS_ENDPOINT", "http://env:1/v1/metrics")
    monkeypatch.setenv("MEZMO_OTEL_METRICS_AUTH_TOKEN", "metrics-token")
    args, _ = _parse(amc, ["--otel-send", "logs", "--output-dir", "x"])
    assert args.otel_logs_endpoint == "http://env:1/v1/logs"
    assert args.otel_logs_auth_token == "logs-token"
    assert args.otel_metrics_endpoint is None
    assert args.otel_metrics_auth_token is None
    assert args.otel_traces_endpoint is None
    assert args.otel_traces_auth_token is None


def test_abbreviated_flags_rejected(amc):
    """allow_abbrev is off: prefix-abbreviated spellings (--emit-sel,
    --otel-en) must fail rather than silently match a canonical flag
    (--otel-en would otherwise abbreviate --otel-endpoint)."""
    for argv in (["--emit-sel", "metrics"],
                 ["--otel-en", "http://h:1"]):
        _parse_error(amc, argv + ["--output-dir", "x"])


def test_gauge_gate_message_uses_canonical_wording_for_otel_send(amc):
    """A canonical user who selects gauges without the 'metrics' artifact
    must see the gate message in canonical terms, not the deprecated
    toggle's name."""
    err = _parse_error(amc, [
        "--otel-send", "gauges",
        "--otel-endpoint", "http://h:1",
        "--emit", "logs",
        "--output-dir", "x",
    ])
    assert "--otel-send gauges" in err
    assert "--otel-gauges-only" not in err


def test_otel_send_none_with_endpoint_stays_off_without_derivation(amc):
    """'none' + --otel-endpoint parses (off wins; the endpoint is inert)
    and does not derive per-signal endpoints — there is nothing to
    derive for when streaming is off."""
    args, _ = _parse(amc, ["--otel-send", "none",
                           "--otel-endpoint", "http://h:1",
                           "--output-dir", "x"])
    assert not args.otel_enabled
    assert args.otel_logs_endpoint is None
    assert args.otel_metrics_endpoint is None
    assert args.otel_traces_endpoint is None


def test_otel_endpoint_precedence_ladder(amc, monkeypatch):
    """Per-signal precedence after the flag day (the per-signal flags
    are gone, so the historic 3-rung ladder is now 2 rungs):
    --otel-endpoint derivation > MEZMO_OTEL_* env var. An explicitly
    typed base must never be silently hijacked by a stale shell export,
    and the env var supplies the default when no base is given."""
    monkeypatch.setenv("MEZMO_OTEL_LOGS_ENDPOINT", "http://envhost:9/custom/logs")
    monkeypatch.setenv("MEZMO_OTEL_LOGS_AUTH_TOKEN", "env-token")

    # Rung 2: env var supplies the default when no base is given.
    args, _ = _parse(amc, ["--otel-send", "logs", "--output-dir", "x"])
    assert args.otel_logs_endpoint == "http://envhost:9/custom/logs"
    assert args.otel_logs_auth_token == "env-token"

    # Rung 1: an explicitly typed base (and token) beats the env var.
    args, _ = _parse(amc, ["--otel-send", "logs",
                           "--otel-endpoint", "http://cli:4318",
                           "--otel-auth-token", "cli-token",
                           "--output-dir", "x"])
    assert args.otel_logs_endpoint == "http://cli:4318/v1/logs"
    assert args.otel_logs_auth_token == "cli-token"


def test_emit_gauges_without_metrics_uses_canonical_wording(amc):
    """--emit gauges without 'metrics' errors in canonical terms instead
    of falling through to the legacy gate that names --emit-selection."""
    err = _parse_error(amc, ["--emit", "gauges,logs", "--output-dir", "x"])
    assert "--emit 'gauges' requires 'metrics'" in err
    assert "--emit-selection" not in err


def test_dst_gauges_gate_uses_canonical_wording_for_emit(amc):
    """The DST x gauges incompatibility gate names the spelling the user
    typed: --emit users see --emit 'gauges', not the deprecated alias."""
    err = _parse_error(amc, [
        "--emit", "metrics,gauges",
        "--inject-dst-artifact-day", "1",
        "--duration-days", "1",
        "--output-dir", "x",
    ])
    assert "--emit 'gauges'" in err
    assert "--emit-selection" not in err


def test_otel_send_none_clears_env_endpoints_before_validation(amc, monkeypatch):
    """'none' is truly off: env-provided per-signal values are cleared
    before the endpoint-shape validation, so a malformed shell export
    cannot fail a run the user explicitly disabled (Copilot round 4)."""
    monkeypatch.setenv("MEZMO_OTEL_LOGS_ENDPOINT", "not-a-url")
    monkeypatch.setenv("MEZMO_OTEL_LOGS_AUTH_TOKEN", "   ")
    args, _ = _parse(amc, ["--otel-send", "none", "--output-dir", "x"])
    assert not args.otel_enabled
    assert args.otel_logs_endpoint is None
    assert args.otel_logs_auth_token is None


def test_otel_send_logs_gauges_does_not_leak_metrics_signal(tmp_path):
    """--otel-send logs,gauges derives the metrics ENDPOINT (the gauge
    stream posts there) but must not leak the anomaly-count metrics
    SIGNAL through it (Copilot round 5). End-to-end against a mock
    collector: /v1/metrics receives only Gauge payloads, never Sum
    anomaly counters; /v1/logs receives the log signal."""
    import json as _json
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    received = []

    class _Collector(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            received.append((self.path, body))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args, **kwargs):  # noqa: D401, ANN002, ANN003
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Collector)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = _invoke(
            "--seed", "42", "--duration-days", "1",
            "--interval-seconds", "600",
            "--emit", "metrics",
            "--otel-send", "logs,gauges",
            "--otel-endpoint", f"http://127.0.0.1:{server.server_port}",
            "--otel-stream-speedup", "1000000",
            "--otel-stream-protocol", "json",
            "--otel-gauge-batch-seconds", "86400",
            "--otel-activity-log", str(tmp_path / "otel-activity.log"),
            "--output-dir", str(tmp_path / "run"),
        )
        assert result.returncode == 0, result.stderr
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    paths = {path for path, _ in received}
    assert "/v1/logs" in paths, "selected logs signal must stream"
    metrics_bodies = [body for path, body in received if path == "/v1/metrics"]
    assert metrics_bodies, "gauge stream must post to the metrics endpoint"
    for body in metrics_bodies:
        payload = _json.loads(body)
        names = {
            m["name"]
            for rm in payload["resourceMetrics"]
            for sm in rm["scopeMetrics"]
            for m in sm["metrics"]
        }
        assert "anomaly.count" not in names, (
            "unselected metrics signal leaked through the gauge endpoint"
        )
    assert "/v1/traces" not in paths, "unselected traces signal must not stream"
