"""Remote-bind DoS hardening (docs/work/archive/2026-07/2026-07-02-server-remote-bind-hardening).

Covers the four bounds added so a reachable non-loopback `amc serve` cannot
be driven into thread/memory exhaustion: bounded worker threads (fast 503
when saturated), a concurrent-SSE ceiling, a socket read timeout against
slow-loris, and eviction of idle rate-limiter buckets.

Concurrency tests use raw sockets for precise control and live in their own
file so xdist (`--dist loadfile`) keeps them on one worker.
"""

import socket
import time

import pytest

from anomaly_metric_creator import server


# ------------------------------------------------------------------
# Rate-limiter bucket eviction (unit, injected clock — deterministic)
# ------------------------------------------------------------------

def test_rate_limiter_evicts_idle_buckets():
    clock = {"t": 1000.0}
    limiter = server._RateLimiter(
        limit_per_minute=5, window_seconds=60.0, clock=lambda: clock["t"]
    )
    # 500 distinct one-shot clients within the first window.
    for i in range(500):
        limiter.check(f"10.0.0.{i}", "commands")
    assert limiter._table_size() == 500

    # Advance well past the window and touch one new client: the sweep must
    # drop every bucket whose newest hit is older than the window.
    clock["t"] += 120.0
    limiter.check("10.9.9.9", "commands")
    assert limiter._table_size() <= 2, (
        f"idle buckets not evicted: {limiter._table_size()} remain"
    )


def test_rate_limiter_still_limits_after_eviction():
    clock = {"t": 0.0}
    limiter = server._RateLimiter(
        limit_per_minute=2, window_seconds=60.0, clock=lambda: clock["t"]
    )
    assert limiter.check("c", "b").allowed
    assert limiter.check("c", "b").allowed
    assert not limiter.check("c", "b").allowed  # 3rd within window blocked
    clock["t"] += 61.0
    assert limiter.check("c", "b").allowed  # window rolled over


# ------------------------------------------------------------------
# Server hardening (live raw-socket tests)
# ------------------------------------------------------------------

def _hardened_state(amc, tmp_path):
    argv = [
        "--duration-days", "1", "--seed", "42",
        "--components", "apigateway,cacheservice",
        "--output-dir", str(tmp_path),
        "--interval-seconds", "3600",
    ]
    amc.main(argv)
    return server.build_state(amc, amc.parse_args(argv))


def _raw_get(host, port, path, *, linger=False, timeout=5.0):
    """Send a raw GET and return the first response bytes.

    With ``linger=True`` the socket is returned open (caller closes it) so a
    long-lived SSE handler keeps holding its worker/SSE slot.
    """
    sock = socket.create_connection((host, port), timeout=timeout)
    sock.settimeout(timeout)
    sock.sendall(f"GET {path} HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n".encode())
    if linger:
        return sock
    try:
        return sock.recv(256)
    finally:
        sock.close()


def _raw_get_full(host, port, path, *, timeout=5.0):
    """Send a raw GET and read the whole response until the peer closes."""
    sock = socket.create_connection((host, port), timeout=timeout)
    sock.settimeout(timeout)
    sock.sendall(f"GET {path} HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n".encode())
    chunks = []
    try:
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            chunks.append(chunk)
    except socket.timeout:
        pass
    finally:
        sock.close()
    return b"".join(chunks)


def _poll_until_503(host, port, path, *, deadline_seconds=5.0):
    """Poll ``path`` with fresh short connections until it returns a 503 or
    the deadline expires. Replaces a fixed ``sleep`` so a slow runner that
    takes longer to occupy the worker/SSE slots cannot flake the assertion:
    an over-cap request is refused before a worker thread spawns, so each
    poll is cheap and does not itself consume a slot."""
    end = time.monotonic() + deadline_seconds
    resp = b""
    while time.monotonic() < end:
        resp = _raw_get(host, port, path)
        if b"503" in resp:
            return resp
        time.sleep(0.02)
    return resp


def test_worker_cap_returns_503_when_saturated(amc, tmp_path):
    state = _hardened_state(amc, tmp_path)
    security = server.ServerSecurityConfig(
        max_concurrent_requests=2, max_sse_connections=8,
    )
    httpd, base_url = server.start_test_server(state, security=security)
    host, port = httpd.server_address
    held = []
    try:
        # Occupy both worker slots with long-lived SSE streams.
        for _ in range(2):
            held.append(_raw_get(host, port, "/v1/debug/events", linger=True))
        # A third connection cannot get a worker: fast 503, no thread growth.
        # Poll until the two handlers have acquired both slots (bounded wait).
        resp = _poll_until_503(host, port, "/healthz")
        assert b"503" in resp, resp
    finally:
        for sock in held:
            sock.close()
        httpd.shutdown()
        httpd.server_close()


def test_sse_ceiling_returns_503_over_limit(amc, tmp_path):
    state = _hardened_state(amc, tmp_path)
    security = server.ServerSecurityConfig(
        max_concurrent_requests=16, max_sse_connections=1,
    )
    httpd, base_url = server.start_test_server(state, security=security)
    host, port = httpd.server_address
    held = []
    try:
        held.append(_raw_get(host, port, "/v1/debug/events", linger=True))
        # Second SSE stream exceeds the ceiling: 503, worker not monopolized.
        # Poll until the first stream holds the only slot (bounded wait).
        resp = _poll_until_503(host, port, "/v1/logs/stream")
        assert b"503" in resp, resp
    finally:
        for sock in held:
            sock.close()
        httpd.shutdown()
        httpd.server_close()


def test_sse_streams_work_under_the_ceiling(amc, tmp_path):
    """Regression guard: with the shutdown event set, SSE still delivers the
    terminal event and releases its slot, so a second stream then succeeds."""
    state = _hardened_state(amc, tmp_path)
    state.shutdown_event.set()
    security = server.ServerSecurityConfig(max_sse_connections=1)
    httpd, base_url = server.start_test_server(state, security=security)
    host, port = httpd.server_address
    try:
        for _ in range(2):  # slot must be released between streams
            resp = _raw_get_full(host, port, "/v1/debug/events")
            assert b"event: shutdown" in resp, resp
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_socket_timeout_closes_slow_loris(amc, tmp_path):
    state = _hardened_state(amc, tmp_path)
    security = server.ServerSecurityConfig(socket_timeout_seconds=1.0)
    httpd, base_url = server.start_test_server(state, security=security)
    host, port = httpd.server_address
    try:
        sock = socket.create_connection((host, port), timeout=5.0)
        # Send a partial request and never finish the header block.
        sock.sendall(b"GET /healthz HTTP/1.1\r\nHost: x\r\n")
        sock.settimeout(5.0)
        start = time.monotonic()
        data = sock.recv(256)  # server should time out and close the conn
        elapsed = time.monotonic() - start
        sock.close()
        # An empty read (peer closed) within a few seconds proves the slow
        # connection was reaped rather than pinning a worker indefinitely.
        assert data == b"" or b"408" in data or b"400" in data, data
        assert elapsed < 4.0, f"slow-loris not reaped promptly ({elapsed:.1f}s)"
    finally:
        httpd.shutdown()
        httpd.server_close()


# ------------------------------------------------------------------
# Flag parsing / validation
# ------------------------------------------------------------------

def test_hardening_flags_default_and_parse():
    parser = server._build_serve_parser()
    serve_args, _ = server._parse_serve_args([], parser)
    assert serve_args.max_concurrent_requests == server.DEFAULT_MAX_CONCURRENT_REQUESTS
    assert serve_args.max_sse_connections == server.DEFAULT_MAX_SSE_CONNECTIONS
    assert serve_args.socket_timeout_seconds == server.DEFAULT_SOCKET_TIMEOUT_SECONDS

    serve_args, _ = server._parse_serve_args(
        ["--max-concurrent-requests", "8", "--max-sse-connections", "0",
         "--socket-timeout-seconds", "5"],
        parser,
    )
    assert serve_args.max_concurrent_requests == 8
    assert serve_args.max_sse_connections == 0  # 0 = unbounded, explicitly
    assert serve_args.socket_timeout_seconds == 5.0


@pytest.mark.parametrize("flag", [
    "--max-concurrent-requests", "--max-sse-connections", "--socket-timeout-seconds",
])
def test_hardening_flags_reject_negative(flag):
    parser = server._build_serve_parser()
    with pytest.raises(SystemExit):
        server.serve_main([flag, "-1", "--no-generate", "--port", "0"])


def test_defaults_do_not_disturb_normal_requests(amc, tmp_path):
    """Sane defaults (workers 64 / sse 16 / 30s) leave ordinary use intact."""
    state = _hardened_state(amc, tmp_path)
    httpd, base_url = server.start_test_server(state)  # default config
    host, port = httpd.server_address
    try:
        resp = _raw_get(host, port, "/healthz")
        assert b"200" in resp, resp
    finally:
        httpd.shutdown()
        httpd.server_close()
