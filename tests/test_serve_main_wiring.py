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
