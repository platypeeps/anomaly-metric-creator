"""Composition coverage for the production ``serve_main`` path."""

import threading
from types import SimpleNamespace

import pytest

from anomaly_metric_creator import server


class _StopWiring(Exception):
    """Stop ``serve_main`` immediately after the state-construction seam."""


@pytest.mark.parametrize(
    ("extra_argv", "expected_eval_mode"),
    [([], False), (["--mcp-eval-mode"], True)],
)
def test_serve_main_threads_eval_mode_to_build_state(
    amc, monkeypatch, tmp_path, extra_argv, expected_eval_mode
):
    captured = {}

    def capture_build_state(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        raise _StopWiring

    monkeypatch.setattr(server, "build_state", capture_build_state)

    with pytest.raises(_StopWiring):
        server.serve_main(
            [
                "--no-generate",
                "--port",
                "0",
                "--output-dir",
                str(tmp_path),
                *extra_argv,
            ],
            legacy_module=amc,
        )

    assert captured["args"][0] is amc
    assert captured["kwargs"]["eval_mode"] is expected_eval_mode


def test_serve_main_maps_every_security_flag(monkeypatch, amc, tmp_path):
    state = SimpleNamespace(shutdown_event=threading.Event())
    captured = {}

    monkeypatch.setattr(server, "build_state", lambda *args, **kwargs: state)
    monkeypatch.setattr(
        server, "_start_continuous_generation", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(server, "_start_otel_background", lambda *args: None)
    monkeypatch.setattr(server, "_stop_continuous_generation", lambda *args: None)

    handler = object()

    def capture_handler(received_state, *, security, request_logger):
        captured["handler_state"] = received_state
        captured["security"] = security
        captured["request_logger"] = request_logger
        return handler

    class StubServer:
        def __init__(
            self, address, received_handler, *, max_workers, max_sse
        ):
            captured["address"] = address
            captured["handler"] = received_handler
            captured["max_workers"] = max_workers
            captured["max_sse"] = max_sse
            self.server_address = ("127.0.0.1", 43210)

        def serve_forever(self):
            captured["served"] = True

        def server_close(self):
            captured["closed"] = True

    monkeypatch.setattr(server, "make_handler", capture_handler)
    monkeypatch.setattr(server, "_BoundedThreadingHTTPServer", StubServer)

    server.serve_main(
        [
            "--no-generate",
            "--port",
            "0",
            "--output-dir",
            str(tmp_path),
            "--auth-token",
            "test-token",
            "--max-request-body-bytes",
            "2048",
            "--allow-remote-without-auth",
            "--cors-allow-origin",
            "https://example.test",
            "--rate-limit-per-minute",
            "17",
            "--max-concurrent-requests",
            "7",
            "--max-sse-connections",
            "3",
            "--socket-timeout-seconds",
            "1.5",
        ],
        legacy_module=amc,
    )

    security = captured["security"]
    expected = {
        "auth_token": "test-token",
        "max_body_bytes": 2048,
        "allow_remote_without_auth": True,
        "cors_allow_origin": "https://example.test",
        "rate_limit_per_minute": 17,
        "max_concurrent_requests": 7,
        "max_sse_connections": 3,
        "socket_timeout_seconds": 1.5,
    }
    for field, value in expected.items():
        assert getattr(security, field) == value

    assert captured["handler_state"] is state
    assert captured["request_logger"] is None
    assert captured["address"] == ("127.0.0.1", 0)
    assert captured["handler"] is handler
    assert captured["max_workers"] == security.max_concurrent_requests
    assert captured["max_sse"] == security.max_sse_connections
    assert captured["served"] is True
    assert captured["closed"] is True
    assert state.shutdown_event.is_set()


def _run_serve_with_stub(
    monkeypatch,
    amc,
    tmp_path,
    argv,
    *,
    server_address,
    active_scenarios=(),
):
    """Drive ``serve_main`` to completion behind a stub HTTP server.

    Returns nothing; callers read stdout via ``capsys``. ``server_address``
    is what ``httpd.server_address`` reports, so it controls the host/port
    the inspection banner renders (and thus the loopback token rule).
    """
    state = SimpleNamespace(
        shutdown_event=threading.Event(),
        active_scenarios=tuple(active_scenarios),
    )
    monkeypatch.setattr(server, "build_state", lambda *a, **k: state)
    monkeypatch.setattr(
        server, "_start_continuous_generation", lambda *a, **k: None
    )
    monkeypatch.setattr(server, "_start_otel_background", lambda *a: None)
    monkeypatch.setattr(server, "_stop_continuous_generation", lambda *a: None)
    monkeypatch.setattr(server, "make_handler", lambda *a, **k: object())

    class StubServer:
        def __init__(self, address, handler, *, max_workers, max_sse):
            self.server_address = server_address

        def serve_forever(self):
            pass

        def server_close(self):
            pass

    monkeypatch.setattr(server, "_BoundedThreadingHTTPServer", StubServer)
    server.serve_main(
        ["--no-generate", "--output-dir", str(tmp_path), *argv],
        legacy_module=amc,
    )


def test_inspection_banner_prints_copyable_commands(
    monkeypatch, amc, tmp_path, capsys
):
    _run_serve_with_stub(
        monkeypatch,
        amc,
        tmp_path,
        ["--port", "0"],
        server_address=("127.0.0.1", 43210),
        active_scenarios=("db_stall", "cache_collapse"),
    )
    out = capsys.readouterr().out
    base = "http://127.0.0.1:43210"
    assert f"curl -fsS {base}/v1/kubeconfig -o amc-kubeconfig" in out
    assert "export KUBECONFIG=$PWD/amc-kubeconfig" in out
    assert "kubectl get pods -n saas-prod" in out
    assert "kubectl get events -n saas-prod" in out
    assert "helm list -n saas-prod" in out
    assert f"curl -X POST {base}/v1/mutations/reset" in out
    # No auth token configured -> no bearer header on any curl line.
    assert "Authorization: Bearer" not in out
    assert "Active scenarios: db_stall, cache_collapse" in out


def test_inspection_banner_loopback_embeds_real_token(
    monkeypatch, amc, tmp_path, capsys
):
    _run_serve_with_stub(
        monkeypatch,
        amc,
        tmp_path,
        ["--port", "0", "--auth-token", "secret-tok"],
        server_address=("127.0.0.1", 43210),
    )
    out = capsys.readouterr().out
    # Loopback bind: the real token is safe to echo into copyable curl lines.
    assert '-H "Authorization: Bearer secret-tok"' in out
    assert "$AMC_TOKEN" not in out


def test_inspection_banner_remote_bind_uses_token_placeholder(
    monkeypatch, amc, tmp_path, capsys
):
    _run_serve_with_stub(
        monkeypatch,
        amc,
        tmp_path,
        ["--port", "8080", "--host", "10.0.0.5", "--auth-token", "secret-tok"],
        server_address=("10.0.0.5", 8080),
    )
    out = capsys.readouterr().out
    # Non-loopback bind: the token must NOT reach stdout; use a placeholder.
    assert "secret-tok" not in out
    assert '-H "Authorization: Bearer $AMC_TOKEN"' in out


def test_inspection_banner_suppresses_scenarios_under_eval_mode(
    monkeypatch, amc, tmp_path, capsys
):
    _run_serve_with_stub(
        monkeypatch,
        amc,
        tmp_path,
        ["--port", "0", "--mcp-eval-mode"],
        server_address=("127.0.0.1", 43210),
        active_scenarios=("db_stall", "cache_collapse"),
    )
    out = capsys.readouterr().out
    # Ground-truth wall: no scenario slug or scenario line under eval mode.
    assert "Active scenarios:" not in out
    assert "db_stall" not in out
    assert "cache_collapse" not in out
    # Positive control: the rest of the banner still prints.
    assert "kubectl get pods -n saas-prod" in out


# Assert against the module's own warning constant so the test tracks the
# single source of truth rather than a hand-copied substring.
_EVAL_PERSIST_WARNING = server._EVAL_NO_PERSIST_WARNING


@pytest.mark.parametrize(
    ("extra_argv", "expect_warning"),
    [
        # Eval mode without any persistence flag: the only combination that warns.
        (["--mcp-eval-mode"], True),
        # Eval mode with each persistence flag: no warning.
        (["--mcp-eval-mode", "--persist-command-db", "traces.sqlite"], False),
        (["--mcp-eval-mode", "--persist-command-log", "traces.jsonl"], False),
        # Eval mode with both persistence flags at once: still no warning.
        (
            [
                "--mcp-eval-mode",
                "--persist-command-db",
                "traces.sqlite",
                "--persist-command-log",
                "traces.jsonl",
            ],
            False,
        ),
        # Non-eval mode without persistence: no warning (retrieval is open).
        ([], False),
    ],
)
def test_serve_main_warns_on_eval_mode_without_persistence(
    monkeypatch, amc, tmp_path, capsys, extra_argv, expect_warning
):
    _run_serve_with_stub(
        monkeypatch,
        amc,
        tmp_path,
        ["--port", "0", *extra_argv],
        server_address=("127.0.0.1", 43210),
    )
    err = capsys.readouterr().err
    if expect_warning:
        assert _EVAL_PERSIST_WARNING in err
        # The warning must name the remedy flag, not just the problem.
        assert "--persist-command-db PATH" in err
    else:
        assert _EVAL_PERSIST_WARNING not in err


def test_inspection_banner_brackets_ipv6_host(capsys):
    # An IPv6 literal must be bracketed or the URL is invalid
    # (http://::1:8088 does not parse; http://[::1]:8088 does).
    security = server.ServerSecurityConfig()
    server._print_inspection_banner(
        "::1",
        8088,
        "saas-prod",
        security,
        eval_mode=False,
        active_scenarios=(),
    )
    out = capsys.readouterr().out
    assert "http://[::1]:8088/v1/kubeconfig" in out
    assert "http://::1:8088" not in out


def test_serve_unknown_scenario_slug_exits_naming_catalog(amc, tmp_path, capsys):
    with pytest.raises(SystemExit) as excinfo:
        server.serve_main(
            [
                "--no-generate",
                "--port",
                "0",
                "--output-dir",
                str(tmp_path),
                "--scenarios",
                "not_a_real_scenario",
            ],
            legacy_module=amc,
        )
    assert excinfo.value.code != 0
    err = capsys.readouterr().err
    assert "not_a_real_scenario" in err
