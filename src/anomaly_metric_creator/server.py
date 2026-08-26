"""HTTP simulator server for Kubernetes/Helm incident debugging.

The module is intentionally stdlib-only. Server mode should be useful in a
fresh editable install without adding a web framework dependency to the core
generator.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as _dt
import hmac
import io
import ipaddress
import json
import sys
import tempfile
import threading
import time
import urllib.parse
import uuid
from collections import deque
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

from . import server_mcp
from .server_debug_ui import DEBUG_HTML
from .server_mutations import (
    PERSIST_ERROR_PREFIX,
    HelmReleaseMutation as HelmReleaseMutation,  # noqa: F401
    SimulationMutations as SimulationMutations,  # noqa: F401
    WorkloadMutation as WorkloadMutation,  # noqa: F401
)
from .server_traces import (
    COMMAND_TRACE_DB_SCHEMA_VERSION as COMMAND_TRACE_DB_SCHEMA_VERSION,
    COMMAND_TRACE_EXPORT_VERSION as COMMAND_TRACE_EXPORT_VERSION,
    DEFAULT_TRACE_LIMIT,
    CommandTrace as CommandTrace,  # noqa: F401
    CommandTraceStore as CommandTraceStore,  # noqa: F401
)

# DEFAULT_RELEASE / DEFAULT_CHART are not defined here: they are owned by
# server_ops and reach this module through the compatibility block below.
# They used to be duplicated as literals at this position, which was invisible
# only because the alias block reassigned both a few hundred lines later.
DEFAULT_MAX_BODY_BYTES = 1024 * 1024
# Bounded Kubernetes watch stream tuning. Both are module globals so tests can
# monkeypatch them (`server._WATCH_POLL_SECONDS = 0.05`) for fast,
# deterministic streams. `_WATCH_MAX_SECONDS` is the hard ceiling that keeps a
# watch finite even under kubectl's long default timeout; a smaller
# `timeoutSeconds` query wins.
_WATCH_POLL_SECONDS = 2.0
_WATCH_MAX_SECONDS = 300.0
CORS_ALLOW_METHODS = "GET, POST, PUT, PATCH, DELETE, OPTIONS"
CORS_ALLOW_HEADERS = "authorization, content-type, accept"

# --- Ground-truth wall (eval mode) --------------------------------
# Endpoints that reveal the scoring rubric an agent under evaluation must
# not see: the anomaly manifest, the scenario catalog, /v1/state (names
# active scenarios + anomaly counts), the report-log stream (a verbatim
# rendering of the manifest — same descriptions and event ids), and the
# whole /v1/debug/* console surface (command traces carry active scenarios;
# search/unsupported/commands expose the manifest). The debug UI shell
# (`/`, `/debug`) reads all of the above.
#
# This is the single classification registry: `_rubric_endpoint` is the
# only place a path is judged rubric-bearing, and `test_server_eval_mode`
# asserts every dispatched route literal in this module is classified
# (rubric or investigation) so a new endpoint cannot be added unclassified.
_RUBRIC_ENDPOINT_EXACT = frozenset({
    "/",
    "/debug",
    "/v1/anomalies",
    "/v1/scenarios",
    "/v1/state",
    "/v1/logs/stream",
})
_RUBRIC_ENDPOINT_PREFIXES = ("/v1/debug",)

# Routes that stay open in eval mode: the investigation surface an agent is
# meant to use, plus liveness. Kept as an explicit set so the completeness
# test can prove the union covers every dispatched path.
_INVESTIGATION_ENDPOINT_EXACT = frozenset({
    "/healthz",
    "/readyz",
    "/mcp",
    "/v1/kubeconfig",
    "/v1/commands",
    "/v1/time/pause",
    "/v1/time/resume",
    "/v1/time/seek",
    "/v1/mutations/reset",
})


def _rubric_endpoint(path: str) -> bool:
    """Whether ``path`` reveals eval ground truth (hidden under eval mode)."""
    if path in _RUBRIC_ENDPOINT_EXACT:
        return True
    return any(
        path == prefix or path.startswith(prefix + "/")
        for prefix in _RUBRIC_ENDPOINT_PREFIXES
    )


# Remote-bind resource bounds (on by default, generous enough not to affect
# single-client workshop use). 0 disables an individual bound.
DEFAULT_MAX_CONCURRENT_REQUESTS = 64
DEFAULT_MAX_SSE_CONNECTIONS = 16
DEFAULT_SOCKET_TIMEOUT_SECONDS = 30.0


@dataclass(frozen=True)
class ServerSecurityConfig:
    """HTTP boundary controls for server mode."""

    auth_token: str = ""
    max_body_bytes: int = DEFAULT_MAX_BODY_BYTES
    allow_remote_without_auth: bool = False
    cors_allow_origin: str = ""
    rate_limit_per_minute: int = 0
    # DoS bounds for reachable (esp. non-loopback) binds.
    max_concurrent_requests: int = DEFAULT_MAX_CONCURRENT_REQUESTS
    max_sse_connections: int = DEFAULT_MAX_SSE_CONNECTIONS
    socket_timeout_seconds: float = DEFAULT_SOCKET_TIMEOUT_SECONDS


class StructuredRequestLogger:
    """Thread-safe JSONL request/error logger for serve mode."""

    def __init__(self, path: str | Path | None = None, *, stream: Any | None = None):
        self.path = Path(path) if path is not None else None
        self.stream = stream
        self._lock = threading.Lock()
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def log_request(self, record: dict[str, Any]) -> None:
        self._write({"event": "request", **record})

    def log_error(self, record: dict[str, Any]) -> None:
        self._write({"event": "error", **record})

    def _write(self, record: dict[str, Any]) -> None:
        line = json.dumps(record, sort_keys=True) + "\n"
        with self._lock:
            if self.path is not None:
                with self.path.open("a", encoding="utf-8") as f:
                    f.write(line)
                return
            target = self.stream if self.stream is not None else sys.stderr
            target.write(line)
            target.flush()


@dataclass(frozen=True)
class _RateLimitDecision:
    allowed: bool
    retry_after_seconds: int = 0


class _RateLimiter:
    """Small per-client fixed-window limiter for optional lab hardening.

    On a public bind the client key is the real peer IP, so a churning set of
    source addresses would otherwise grow ``_calls`` without bound (the
    DoS-hardening feature becoming its own unbounded allocation). Each
    ``check`` runs a bounded periodic sweep that drops buckets whose newest
    hit has fallen outside the window, so the table size tracks *recently
    active* clients rather than every client ever seen. ``clock`` is
    injectable for deterministic tests.
    """

    def __init__(
        self,
        limit_per_minute: int,
        *,
        window_seconds: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._limit = max(0, int(limit_per_minute))
        self._window_seconds = window_seconds
        self._clock = clock
        self._lock = threading.RLock()
        self._calls: dict[tuple[str, str], deque[float]] = {}
        self._last_sweep = clock()

    def _table_size(self) -> int:
        """Current number of tracked buckets (test/introspection helper)."""
        with self._lock:
            return len(self._calls)

    def _sweep_locked(self, now: float) -> None:
        """Drop buckets whose newest hit is older than the window.

        Runs at most once per window (amortized O(1) per check); the caller
        holds ``self._lock``.
        """
        cutoff = now - self._window_seconds
        stale = [
            key for key, calls in self._calls.items()
            if not calls or calls[-1] <= cutoff
        ]
        for key in stale:
            del self._calls[key]
        self._last_sweep = now

    def check(self, client: str, bucket: str) -> _RateLimitDecision:
        if self._limit <= 0:
            return _RateLimitDecision(True)
        now = self._clock()
        key = (client, bucket)
        with self._lock:
            if now - self._last_sweep >= self._window_seconds:
                self._sweep_locked(now)
            calls = self._calls.setdefault(key, deque())
            cutoff = now - self._window_seconds
            while calls and calls[0] <= cutoff:
                calls.popleft()
            if len(calls) >= self._limit:
                retry_after = self._window_seconds - (now - calls[0])
                return _RateLimitDecision(False, max(1, int(retry_after + 0.999)))
            calls.append(now)
        return _RateLimitDecision(True)


_SATURATED_503 = (
    b"HTTP/1.1 503 Service Unavailable\r\n"
    b"Content-Type: application/json; charset=utf-8\r\n"
    b"Connection: close\r\n"
    b"Content-Length: 24\r\n"
    b"\r\n"
    b'{"error":"server busy"}\n'
)


class _BoundedThreadingHTTPServer(ThreadingHTTPServer):
    """``ThreadingHTTPServer`` with a worker-thread cap and an SSE ceiling.

    A reachable instance must not spawn one unbounded worker thread per
    connection. ``max_workers`` caps concurrent request threads: an over-cap
    connection gets a fast raw 503 and is closed *before* a worker is spawned.
    ``max_sse_connections`` separately caps the long-lived SSE streams (each
    holds a worker for up to its wall-clock loop), so a handful of streams
    cannot monopolize the pool. Both bounds are opt-out (a value <= 0 leaves
    that dimension unbounded, preserving the historic behavior).
    """

    daemon_threads = True

    def __init__(
        self,
        *args: Any,
        max_workers: int,
        max_sse: int,
        refusals: Any = None,
        **kwargs: Any,
    ) -> None:
        self._worker_semaphore = (
            threading.BoundedSemaphore(max_workers) if max_workers > 0 else None
        )
        self._sse_semaphore = (
            threading.BoundedSemaphore(max_sse) if max_sse > 0 else None
        )
        # Shared RefusalCounters (from SimulationState) so a worker-cap refusal —
        # which happens in process_request before any handler exists — still
        # reaches the same tally the handler-side SSE / rate-limit refusals feed
        # (A-075). ``None`` leaves the count unwired (defensive; the serve
        # entrypoints always pass state.refusals).
        self._refusals = refusals
        super().__init__(*args, **kwargs)

    def process_request(self, request: Any, client_address: Any) -> None:
        if self._worker_semaphore is not None and not self._worker_semaphore.acquire(
            blocking=False
        ):
            self._refuse_saturated(request)
            return
        super().process_request(request, client_address)

    def process_request_thread(self, request: Any, client_address: Any) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            if self._worker_semaphore is not None:
                self._worker_semaphore.release()

    def _refuse_saturated(self, request: Any) -> None:
        if self._refusals is not None:
            self._refusals.record("worker_cap")
        with contextlib.suppress(OSError):
            request.sendall(_SATURATED_503)
        self.shutdown_request(request)

    def try_acquire_sse(self) -> bool:
        """Reserve an SSE slot; ``False`` means the ceiling is reached."""
        if self._sse_semaphore is None:
            return True
        return self._sse_semaphore.acquire(blocking=False)

    def release_sse(self) -> None:
        if self._sse_semaphore is not None:
            self._sse_semaphore.release()


from . import server_ops as _server_ops

# Compatibility facade: keep the historic anomaly_metric_creator.server import
# surface while the ops implementation lives in server_ops.py.
#
# Most of that surface is published by the __getattr__ delegation below, so an
# extraction that adds a name to server_ops needs no edit here. This block used
# to be 227 hand-written `NAME = _server_ops.NAME` lines that every extraction
# step had to append to.
#
# The names imported explicitly below are the exception, and the reason is
# PEP 562: a module __getattr__ is consulted for attribute access *on the
# module object*, never for global-name resolution inside the module itself.
# Anything this file reads as a bare global must therefore be a real global --
# a miss would surface as a NameError on a request path, not an ImportError at
# startup. The rest of the explicit set is read elsewhere in the repository as
# `server.<name>`; importing those keeps object identity and monkeypatch
# behavior identical to the assignments they replace rather than merely
# equivalent. tests/test_server_alias_surface.py derives the required set
# mechanically, so it fails if a later edit adds an internal use of a
# delegated name.
from .server_ops import (
    DEFAULT_RELEASE as DEFAULT_RELEASE,
    DEFAULT_NAMESPACE as DEFAULT_NAMESPACE,
    OpsScenarioProfile as OpsScenarioProfile,
    OPS_SCENARIO_PROFILES as OPS_SCENARIO_PROFILES,
    SimulationClock as SimulationClock,
    ParsedCommand as ParsedCommand,
    CommandResult as CommandResult,
    KubernetesApiResponse as KubernetesApiResponse,
    SimulationState as SimulationState,
    build_state as build_state,
    load_anomaly_rows as load_anomaly_rows,
    run_command as run_command,
    parse_command as parse_command,
    render_command as render_command,
    resource_snapshot as resource_snapshot,
    _record_continuous_generation_failure as _record_continuous_generation_failure,
    _record_server_error as _record_server_error,
    _capture_traceback_tail as _capture_traceback_tail,
    _emit_error_record as _emit_error_record,
    _render_helm_history as _render_helm_history,
    _format_dt as _format_dt,
    RequestBodyTooLarge as RequestBodyTooLarge,
    _read_json_body as _read_json_body,
    _read_optional_json_body as _read_optional_json_body,
    kubernetes_api_response as kubernetes_api_response,
    kubernetes_api_post_response as kubernetes_api_post_response,
    kubernetes_api_mutating_response as kubernetes_api_mutating_response,
    render_kubeconfig as render_kubeconfig,
    record_kubernetes_api_call as record_kubernetes_api_call,
    _redact_query as _redact_query,
    _k8s_status_response as _k8s_status_response,
    k8s_watch_plan as k8s_watch_plan,
    k8s_watch_objects as k8s_watch_objects,
    k8s_watch_object_key as k8s_watch_object_key,
    k8s_watch_trace_response as k8s_watch_trace_response,
    _k8s_objects_for_resource as _k8s_objects_for_resource,
    _helm_secret_objects as _helm_secret_objects,
    _helm_release_payload as _helm_release_payload,
    _is_kubernetes_api_path as _is_kubernetes_api_path,
    _rate_limit_bucket as _rate_limit_bucket,
)


def _is_delegation_excluded(name: str) -> bool:
    """Names `__getattr__` refuses to forward, so `__dir__` must not list them."""
    return name.startswith("__") and name.endswith("__")


def __getattr__(name: str) -> Any:
    """Publish the historic `server.<ops name>` surface from `server_ops`.

    Dunders are refused before the forward. `server_ops` defines `__all__` and
    this module deliberately does not; forwarding it would quietly change what
    `from anomaly_metric_creator.server import *` imports.
    """
    if _is_delegation_excluded(name):
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    try:
        return getattr(_server_ops, name)
    except AttributeError:
        raise AttributeError(
            f"module {__name__!r} has no attribute {name!r}"
        ) from None


def __dir__() -> list[str]:
    """Keep the delegated names visible to dir(), inspect, and completion.

    The delegated half is filtered through the same predicate `__getattr__`
    uses, so `dir()` never advertises a name that reading would refuse --
    `server_ops.__all__` being the one that actually occurs.
    """
    delegated = {
        name for name in dir(_server_ops) if not _is_delegation_excluded(name)
    }
    return sorted(set(globals()) | delegated)


def make_handler(
    state: SimulationState,
    security: ServerSecurityConfig | None = None,
    request_logger: StructuredRequestLogger | None = None,
):
    security = security or ServerSecurityConfig()
    rate_limiter = _RateLimiter(security.rate_limit_per_minute)

    class _Handler(BaseHTTPRequestHandler):
        server_version = "AMCServer/0.1"
        # StreamRequestHandler.setup() applies this to the connection socket,
        # so a slow-loris client cannot pin a worker thread indefinitely.
        timeout = (
            security.socket_timeout_seconds
            if security.socket_timeout_seconds > 0
            else None
        )

        def handle_one_request(self) -> None:
            self._request_started_at = time.perf_counter()
            self._response_status = 0
            self._response_bytes = 0
            self._structured_error: dict[str, str] | None = None
            # A-077: one id per request, minted at the single shared dispatch
            # entry so it covers do_GET / do_POST / the mutating methods. It is
            # the join key between the structured request/error record and every
            # CommandTrace recorded while handling this request.
            self._request_id = uuid.uuid4().hex[:12]
            try:
                super().handle_one_request()
            finally:
                self._write_structured_logs()

        def log_message(self, fmt: str, *args: Any) -> None:
            return

        def send_response(self, code: int, message: str | None = None) -> None:
            self._response_status = code
            super().send_response(code, message)

        def do_GET(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path
            query = urllib.parse.parse_qs(parsed.query)
            try:
                if path == "/healthz":
                    self._send_text(200, "ok\n")
                    return
                if path == "/readyz":
                    ready, reason = _readyz_check(state)
                    if ready:
                        self._send_json(200, {"ready": True})
                    else:
                        # 503 + failing dimension: the previous unconditional
                        # {"ready": true} masked a --no-generate empty dir and a
                        # failed regen thread. Harness scripts gating on /readyz
                        # now see the real not-ready condition.
                        self._send_json(503, {"ready": False, "reason": reason})
                    return
                if state.eval_mode and _rubric_endpoint(path):
                    # Hidden before auth and before the debug-shell branch:
                    # the console and every rubric endpoint 404 in eval mode.
                    self._send_json(404, {"error": "not found"})
                    return
                if path == "/" or path == "/debug":
                    self._send_html(200, DEBUG_HTML)
                    return
                if not self._is_authorized():
                    self._send_unauthorized(path)
                    return
                if self._send_rate_limited(path):
                    return
                if path == "/mcp":
                    # Streamable HTTP only: a GET here is the legacy SSE
                    # transport probe, refused like the reference mock.
                    self._send_json(405, server_mcp.sse_not_supported_response())
                    return
                watch_plan = k8s_watch_plan(state, path, query)
                if watch_plan is not None:
                    # Modeled list path + `?watch=true`: stream a bounded watch
                    # instead of the one-shot list. Unmodeled `?watch` paths
                    # return None here and fall through to the list/unsupported
                    # handling below.
                    self._send_k8s_watch(path, query, watch_plan)
                    return
                api_started = time.perf_counter()
                api_response = kubernetes_api_response(
                    state,
                    "GET",
                    path,
                    query,
                    self.headers.get("accept", ""),
                )
                if api_response is not None:
                    record_kubernetes_api_call(
                        state,
                        method="GET",
                        path=path,
                        query=query,
                        response=api_response,
                        client=self.client_address[0],
                        user_agent=self.headers.get("user-agent", ""),
                        latency_ms=(time.perf_counter() - api_started) * 1000.0,
                        request_id=self._request_id,
                    )
                    self._send_kubernetes_api_response(api_response)
                    return
                if path == "/v1/kubeconfig":
                    self._send_text(
                        200,
                        render_kubeconfig(
                            self._server_url(),
                            state.namespace,
                            token=security.auth_token,
                        ),
                    )
                elif path in {"/v1/state", "/v1/debug/state"}:
                    self._send_json(200, state.summary())
                elif path == "/v1/scenarios":
                    self._send_json(200, _scenario_payload(state))
                elif path == "/v1/anomalies":
                    limit = _query_int(query, "limit", 100)
                    self._send_json(200, {"items": state.generated_rows_slice(limit)})
                elif path == "/v1/debug/resources":
                    self._send_json(200, resource_snapshot(state))
                elif path == "/v1/debug/commands":
                    limit = _query_int(query, "limit", 100)
                    self._send_json(200, {"items": state.traces.list_traces(limit=limit)})
                elif path == "/v1/debug/commands/export":
                    self._send_json(200, state.traces.export_payload())
                elif path.startswith("/v1/debug/commands/"):
                    raw_trace_id = path.rsplit("/", 1)[1]
                    try:
                        trace_id = int(raw_trace_id)
                    except ValueError:
                        self._send_json(400, {"error": "trace id must be an integer"})
                        return
                    item = state.traces.get(trace_id)
                    if item is None:
                        self._send_json(404, {"error": "not found"})
                        return
                    self._send_json(200, item)
                elif path == "/v1/debug/unsupported":
                    self._send_json(200, {"items": state.traces.unsupported_summary()})
                elif path == "/v1/debug/search":
                    self._send_json(200, state.traces.search(
                        query=_query_str(query, "q", ""),
                        support_status=_query_str(query, "status", ""),
                        command_family=_query_str(query, "family", ""),
                        scenario_id=_query_str(query, "scenario", ""),
                        limit=_query_int(query, "limit", 50),
                        offset=_query_int(query, "offset", 0),
                    ))
                elif path == "/v1/debug/events":
                    self._with_sse_slot(self._send_debug_events)
                elif path == "/v1/logs/stream":
                    self._with_sse_slot(self._send_log_stream)
                else:
                    self._send_json(404, {"error": "not found"})
            except Exception as exc:  # pragma: no cover - defensive HTTP boundary
                self._remember_structured_error(exc)
                # Generic body: str(exc) can carry filesystem paths or other
                # internals. The structured error log keeps the detail for
                # operators; clients only learn that the request failed.
                self._send_json(500, {"error": "internal server error"})

        def do_POST(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            query = urllib.parse.parse_qs(parsed.query)
            path = parsed.path
            try:
                if state.eval_mode and _rubric_endpoint(path):
                    # Rubric 404 runs before auth and rate-limiting, matching
                    # do_GET: an unauthenticated POST to a hidden endpoint must
                    # 404, never 401, so eval mode cannot be fingerprinted by
                    # probing which paths challenge for a bearer token.
                    self._send_json(404, {"error": "not found"})
                    return
                if not self._is_authorized():
                    self._send_unauthorized(path)
                    return
                if self._send_rate_limited(path):
                    return
                if path == "/mcp":
                    self._send_mcp_post()
                    return
                if _is_kubernetes_api_path(path):
                    api_started = time.perf_counter()
                    try:
                        payload = _read_optional_json_body(self, security.max_body_bytes)
                    except RequestBodyTooLarge as exc:
                        api_response = _k8s_status_response(
                            413,
                            str(exc),
                            "RequestEntityTooLarge",
                            "unsupported",
                            "k8s.body.too_large",
                        )
                    else:
                        api_response = kubernetes_api_post_response(state, path, payload)
                    record_kubernetes_api_call(
                        state,
                        method="POST",
                        path=path,
                        query=query,
                        response=api_response,
                        client=self.client_address[0],
                        user_agent=self.headers.get("user-agent", ""),
                        latency_ms=(time.perf_counter() - api_started) * 1000.0,
                        request_id=self._request_id,
                    )
                    self._send_kubernetes_api_response(api_response)
                    return
                payload = _read_json_body(self, security.max_body_bytes)
                if path == "/v1/commands":
                    result = run_command(
                        state,
                        command=payload.get("command"),
                        argv=payload.get("argv"),
                        client=self.client_address[0],
                        request_id=self._request_id,
                    )
                    self._send_json(200, result)
                elif path == "/v1/debug/commands/import":
                    self._send_json(200, state.traces.import_payload(payload))
                elif path == "/v1/time/pause":
                    self._send_json(200, {"clock": {"simulated_time": _format_dt(state.clock.pause()), "paused": True}})
                elif path == "/v1/time/resume":
                    self._send_json(200, {"clock": {"simulated_time": _format_dt(state.clock.resume()), "paused": False}})
                elif path == "/v1/time/seek":
                    timestamp = str(payload.get("timestamp", ""))
                    self._send_json(200, {"clock": {"simulated_time": _format_dt(state.clock.seek(timestamp))}})
                elif path == "/v1/mutations/reset":
                    state.mutations.reset()
                    # scope is an additive, compatible field: reset clears only
                    # the mutation overlay, never generated artifacts, command
                    # traces, or the simulated clock (see the reset contract in
                    # operations-security-logging.md). Existing callers that read
                    # only the "mutations" key are unaffected.
                    self._send_json(
                        200,
                        {
                            "scope": "mutation-overlay",
                            "mutations": state.mutations.summary(),
                        },
                    )
                else:
                    self._send_json(404, {"error": "not found"})
            except RequestBodyTooLarge as exc:
                self._remember_structured_error(exc)
                self._send_json(413, {"error": str(exc)})
            except ValueError as exc:
                self._remember_structured_error(exc)
                self._send_json(400, {"error": str(exc)})
            except Exception as exc:
                self._remember_structured_error(exc)
                # Generic body; detail goes to the structured error log only
                # (see the do_GET boundary for the rationale).
                self._send_json(500, {"error": "internal server error"})

        def do_PUT(self) -> None:
            self._handle_mutating_method("PUT")

        def do_PATCH(self) -> None:
            self._handle_mutating_method("PATCH")

        def do_DELETE(self) -> None:
            self._handle_mutating_method("DELETE")

        def do_OPTIONS(self) -> None:
            self.send_response(204)
            self._send_common_headers(
                "text/plain; charset=utf-8",
                0,
                extra_headers=self._cors_preflight_headers(),
            )
            self.end_headers()

        def _handle_mutating_method(self, method: str) -> None:
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path
            query = urllib.parse.parse_qs(parsed.query)
            # Bound before the try so the catch-all can compute a latency even
            # when the exception fires before the Kubernetes API branch runs.
            api_started: float | None = None
            try:
                if state.eval_mode and _rubric_endpoint(path):
                    # Same fingerprint-resistant ordering as do_GET/do_POST: a
                    # PUT/PATCH/DELETE to a hidden endpoint 404s before auth, so
                    # a rubric path is indistinguishable from a nonexistent one.
                    self._send_json(404, {"error": "not found"})
                    return
                if not self._is_authorized():
                    self._send_unauthorized(path)
                    return
                if self._send_rate_limited(path):
                    return
                if _is_kubernetes_api_path(path):
                    api_started = time.perf_counter()
                    try:
                        payload = (
                            _read_json_body(self, security.max_body_bytes)
                            if method in {"PUT", "PATCH"} else {}
                        )
                    except RequestBodyTooLarge as exc:
                        api_response = _k8s_status_response(
                            413,
                            str(exc),
                            "RequestEntityTooLarge",
                            "unsupported",
                            "k8s.body.too_large",
                        )
                    except ValueError as exc:
                        api_response = _k8s_status_response(
                            400,
                            str(exc),
                            "BadRequest",
                            "unsupported",
                            "k8s.body.invalid",
                        )
                    else:
                        api_response = kubernetes_api_mutating_response(state, method, path, payload)
                    record_kubernetes_api_call(
                        state,
                        method=method,
                        path=path,
                        query=query,
                        response=api_response,
                        client=self.client_address[0],
                        user_agent=self.headers.get("user-agent", ""),
                        latency_ms=(time.perf_counter() - api_started) * 1000.0,
                        request_id=self._request_id,
                    )
                    self._send_kubernetes_api_response(api_response)
                    return
                self._send_json(405, {"error": f"{method} is not supported"})
            except Exception as exc:
                # PUT/PATCH/DELETE previously had no catch-all: a raising
                # handler reset the connection with no record. Mirror the
                # do_GET/do_POST boundary — Status-shaped 500 for Kubernetes API
                # paths, JSON 500 for app paths — and remember the error so the
                # request finalizer routes its detail (with traceback) to the
                # operator sink. Client bodies stay generic (detail never in the
                # body, per the SECURITY.md contract).
                self._remember_structured_error(exc)
                if _is_kubernetes_api_path(path):
                    api_response = _k8s_status_response(
                        500,
                        "internal server error",
                        "InternalError",
                        "unsupported",
                        "k8s.internal_error",
                    )
                    # A raising Kubernetes API mutation must still land in the
                    # kubernetes-api trace ring (the debug backlog), like the
                    # success path above — otherwise the failed mutation is
                    # invisible to /v1/debug/search. Suppress a re-raise from the
                    # recorder itself (e.g. the original exception came from it):
                    # the operator sink already holds the error detail.
                    with contextlib.suppress(Exception):
                        record_kubernetes_api_call(
                            state,
                            method=method,
                            path=path,
                            query=query,
                            response=api_response,
                            client=self.client_address[0],
                            user_agent=self.headers.get("user-agent", ""),
                            latency_ms=(
                                (time.perf_counter() - api_started) * 1000.0
                                if api_started is not None
                                else 0.0
                            ),
                            request_id=self._request_id,
                        )
                    self._send_kubernetes_api_response(api_response)
                else:
                    self._send_json(500, {"error": "internal server error"})

        def _is_authorized(self) -> bool:
            if not security.auth_token:
                return True
            supplied = self.headers.get("authorization", "")
            expected = f"Bearer {security.auth_token}"
            return hmac.compare_digest(supplied, expected)

        def _send_rate_limited(self, path: str) -> bool:
            bucket = _rate_limit_bucket(path)
            if not bucket:
                return False
            decision = rate_limiter.check(self.client_address[0], bucket)
            if decision.allowed:
                return False
            state.refusals.record("rate_limit")
            headers = {"retry-after": str(decision.retry_after_seconds)}
            if _is_kubernetes_api_path(path):
                self._send_kubernetes_api_response(
                    _k8s_status_response(
                        429,
                        "rate limit exceeded",
                        "TooManyRequests",
                        "unsupported",
                        "k8s.rate_limited",
                    ),
                    extra_headers=headers,
                )
            elif path == "/mcp":
                # JSON-RPC-shaped so an MCP client parses the refusal.
                self._send_json(
                    429,
                    server_mcp.rate_limited_response("rate limit exceeded"),
                    extra_headers=headers,
                )
            else:
                self._send_json(429, {"error": "rate limit exceeded"}, extra_headers=headers)
            return True

        def _send_unauthorized(self, path: str) -> None:
            if _is_kubernetes_api_path(path):
                response = _k8s_status_response(
                    401,
                    "missing or invalid bearer token",
                    "Unauthorized",
                    "unsupported",
                    "k8s.auth.unauthorized",
                )
                self._send_kubernetes_api_response(response)
                return
            self._send_json(
                401,
                {"error": "missing or invalid bearer token"},
                extra_headers={"www-authenticate": "Bearer"},
            )

        def _send_json(
            self,
            status: int,
            payload: Any,
            *,
            extra_headers: dict[str, str] | None = None,
        ) -> None:
            body = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
            self.send_response(status)
            self._send_common_headers(
                "application/json; charset=utf-8",
                len(body),
                extra_headers=extra_headers,
            )
            self.end_headers()
            self.wfile.write(body)

        def _send_text(self, status: int, text: str) -> None:
            body = text.encode("utf-8")
            self.send_response(status)
            self._send_common_headers("text/plain; charset=utf-8", len(body))
            self.end_headers()
            self.wfile.write(body)

        def _send_html(self, status: int, text: str) -> None:
            body = text.encode("utf-8")
            self.send_response(status)
            self._send_common_headers("text/html; charset=utf-8", len(body))
            self.end_headers()
            self.wfile.write(body)

        def _send_kubernetes_api_response(
            self,
            response: KubernetesApiResponse,
            *,
            extra_headers: dict[str, str] | None = None,
        ) -> None:
            if isinstance(response.body, str):
                body = response.body.encode("utf-8")
            else:
                body = json.dumps(response.body, indent=2, sort_keys=True).encode("utf-8")
            self.send_response(response.status)
            headers = dict(extra_headers or {})
            if response.status == 401:
                headers.setdefault("www-authenticate", "Bearer")
            self._send_common_headers(
                response.content_type,
                len(body),
                extra_headers=headers,
            )
            self.end_headers()
            self.wfile.write(body)

        def _send_common_headers(
            self,
            content_type: str,
            content_length: int,
            *,
            extra_headers: dict[str, str] | None = None,
        ) -> None:
            self._response_bytes = content_length
            self.send_header("content-type", content_type)
            self.send_header("content-length", str(content_length))
            self.send_header("cache-control", "no-store")
            self.send_header("x-content-type-options", "nosniff")
            self.send_header("referrer-policy", "no-referrer")
            self.send_header("x-frame-options", "DENY")
            self._send_cors_headers()
            for key, value in (extra_headers or {}).items():
                self.send_header(key, value)

        def _send_cors_headers(self) -> None:
            for key, value in self._cors_response_headers().items():
                self.send_header(key, value)

        def _cors_response_headers(self) -> dict[str, str]:
            allowed_origin = security.cors_allow_origin.strip()
            if not allowed_origin:
                return {}
            headers = {"vary": "Origin"}
            origin = self.headers.get("origin", "").strip()
            if allowed_origin == "*":
                headers["access-control-allow-origin"] = "*"
            elif origin == allowed_origin:
                headers["access-control-allow-origin"] = origin
            return headers

        def _cors_preflight_headers(self) -> dict[str, str]:
            if not security.cors_allow_origin.strip():
                return {}
            return {
                "access-control-allow-methods": CORS_ALLOW_METHODS,
                "access-control-allow-headers": CORS_ALLOW_HEADERS,
                "access-control-max-age": "600",
            }

        def _server_url(self) -> str:
            host = self.headers.get("host")
            if not host:
                bound_host, bound_port = self.server.server_address[:2]
                host = f"{bound_host}:{bound_port}"
            return f"http://{host}"

        def _with_sse_slot(self, handler) -> None:
            """Run a long-lived SSE handler under the server's SSE ceiling.

            Reserves one of the bounded SSE slots so concurrent streams cannot
            monopolize the worker pool; refuses with a JSON 503 (before any
            event-stream headers) when the ceiling is reached, and always
            releases the slot when the stream ends.
            """
            server_obj = self.server
            acquire = getattr(server_obj, "try_acquire_sse", None)
            if acquire is not None and not acquire():
                state.refusals.record("sse")
                self._send_json(503, {"error": "SSE connection limit reached"})
                return
            try:
                handler()
            finally:
                release = getattr(server_obj, "release_sse", None)
                if release is not None:
                    release()

        def _send_debug_events(self) -> None:
            self._send_event_stream_headers()
            if state.shutdown_event.is_set():
                self._send_shutdown_event()
                return
            last_version = -1
            for _ in range(300):
                if state.shutdown_event.is_set():
                    self._send_shutdown_event()
                    return
                version = state.traces.version
                if version != last_version:
                    payload = json.dumps({"version": version})
                    if not self._write_event_stream(
                        f"event: commands\ndata: {payload}\n\n".encode("utf-8")
                    ):
                        return
                    if not self._flush_event_stream():
                        return
                    last_version = version
                if state.shutdown_event.wait(1.0):
                    self._send_shutdown_event()
                    return

        def _send_event_stream_headers(self) -> None:
            self.send_response(200)
            self._response_bytes = 0
            self.send_header("content-type", "text/event-stream")
            self.send_header("cache-control", "no-cache")
            self.send_header("x-content-type-options", "nosniff")
            self.send_header("referrer-policy", "no-referrer")
            self._send_cors_headers()
            self.end_headers()

        def _send_shutdown_event(self) -> bool:
            payload = json.dumps({"reason": "server shutdown"})
            if not self._write_event_stream(
                f"event: shutdown\ndata: {payload}\n\n".encode("utf-8")
            ):
                return False
            return self._flush_event_stream()

        def _write_event_stream(self, payload: bytes) -> bool:
            try:
                self.wfile.write(payload)
            except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
                return False
            return True

        def _remember_structured_error(self, exc: BaseException) -> None:
            # Capture the traceback tail now, while the exception is still being
            # handled (called from the do_GET/do_POST except blocks). Deferring
            # to _write_structured_logs would lose it: format_exc() reads the
            # active exception, which is cleared once the except block exits.
            self._structured_error = {
                "error_type": type(exc).__name__,
                "message": str(exc),
                "traceback": _capture_traceback_tail(),
            }

        def _write_structured_logs(self) -> None:
            # Request (access) logging stays opt-in via --structured-log — an
            # access log by default is documented non-goal. But the error arm
            # must always reach a sink: when an error was remembered, emit it
            # through _emit_error_record, which writes the structured record when
            # a logger exists and a stderr block when it does not, so a
            # default-flags 500 is never silent.
            if request_logger is None and self._structured_error is None:
                return
            raw_path = getattr(self, "path", "")
            parsed = urllib.parse.urlparse(raw_path)
            query = urllib.parse.parse_qs(parsed.query)
            now = _dt.datetime.now(_dt.timezone.utc).isoformat()
            status = int(getattr(self, "_response_status", 0) or 0)
            duration_ms = round(
                (time.perf_counter() - getattr(self, "_request_started_at", time.perf_counter()))
                * 1000.0,
                3,
            )
            base_record = {
                "timestamp": now,
                "request_id": getattr(self, "_request_id", ""),
                "method": getattr(self, "command", ""),
                "path": parsed.path,
                "query": _redact_query(query),
                "status": status,
                "client": self.client_address[0] if self.client_address else "",
                "user_agent": self.headers.get("user-agent", "") if hasattr(self, "headers") else "",
                "authorization": (
                    "present"
                    if hasattr(self, "headers") and self.headers.get("authorization")
                    else "absent"
                ),
                "duration_ms": duration_ms,
                "response_bytes": int(getattr(self, "_response_bytes", 0) or 0),
            }
            if request_logger is not None:
                request_logger.log_request(base_record)
            if self._structured_error is not None:
                _emit_error_record(
                    request_logger,
                    {"where": "request", **base_record, **self._structured_error},
                )

        def _flush_event_stream(self) -> bool:
            try:
                self.wfile.flush()
            except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
                return False
            return True

        def _send_log_stream(self) -> None:
            self._send_event_stream_headers()
            if state.shutdown_event.is_set():
                self._send_shutdown_event()
                return
            last_generation = -1
            last_signature: tuple[int, int] | None = None
            for _ in range(300):
                if state.shutdown_event.is_set():
                    self._send_shutdown_event()
                    return
                with state.generation.lock:
                    generation = state.generation.generation_count
                    last_seed = state.generation.last_seed
                signature = _log_file_signature(state.output_dir / "metric_report.log")
                if generation != last_generation or signature != last_signature:
                    payload = json.dumps({
                        "generation_count": generation,
                        "last_seed": last_seed,
                    })
                    if not self._write_event_stream(
                        f"event: generation\ndata: {payload}\n\n".encode("utf-8")
                    ):
                        return
                    if not self._send_log_file():
                        return
                    if not self._flush_event_stream():
                        return
                    last_generation = generation
                    last_signature = signature
                if state.shutdown_event.wait(1.0):
                    self._send_shutdown_event()
                    return

        def _send_k8s_watch(self, path, query, plan) -> None:
            """Stream a bounded Kubernetes watch for a modeled list path.

            Consumes one SSE slot (watches are long-lived), then delegates the
            replay/poll/diff loop to ``_stream_k8s_watch`` and records exactly
            one ``kubernetes-api`` trace with the emitted event count. Over the
            SSE ceiling it refuses with a Kubernetes ``Status`` 503 (not the
            app JSON 503) before any stream headers, and always releases the
            slot in ``finally`` — the DoS-bound contract the SSE handlers use.
            """
            api_started = time.perf_counter()
            server_obj = self.server
            acquire = getattr(server_obj, "try_acquire_sse", None)
            if acquire is not None and not acquire():
                # Same SSE-ceiling refusal as _with_sse_slot, but the watch path
                # refuses with a Kubernetes Status rather than the app JSON 503;
                # count it on the same tally so both SSE-503 sites are visible.
                state.refusals.record("sse")
                refusal = k8s_watch_trace_response(plan, event_count=0, refused=True)
                record_kubernetes_api_call(
                    state,
                    method="GET",
                    path=path,
                    query=query,
                    response=refusal,
                    client=self.client_address[0],
                    user_agent=self.headers.get("user-agent", ""),
                    latency_ms=(time.perf_counter() - api_started) * 1000.0,
                    request_id=self._request_id,
                )
                self._send_kubernetes_api_response(refusal)
                return
            event_count = 0
            try:
                event_count = self._stream_k8s_watch(path, query, plan)
            finally:
                release = getattr(server_obj, "release_sse", None)
                if release is not None:
                    release()
                closed = k8s_watch_trace_response(plan, event_count=event_count)
                record_kubernetes_api_call(
                    state,
                    method="GET",
                    path=path,
                    query=query,
                    response=closed,
                    client=self.client_address[0],
                    user_agent=self.headers.get("user-agent", ""),
                    latency_ms=(time.perf_counter() - api_started) * 1000.0,
                    request_id=self._request_id,
                )

        def _stream_k8s_watch(self, path, query, plan) -> int:
            """Replay the current object set as ADDED, then poll for changes.

            Returns the number of watch events written. Emits one ``ADDED`` per
            initial object, then every ``_WATCH_POLL_SECONDS`` diffs the same
            overlay-aware object set the list path returns and emits
            ``ADDED``/``MODIFIED``/``DELETED`` for overlay changes. Closes at
            ``min(timeoutSeconds, _WATCH_MAX_SECONDS)`` or on the server
            shutdown event; a client disconnect ends the stream without a
            traceback (``_write_event_stream`` swallows BrokenPipe).
            """
            self._send_watch_stream_headers()
            timeout_seconds = _query_int(query, "timeoutSeconds", 0)
            max_seconds = _WATCH_MAX_SECONDS
            if timeout_seconds > 0:
                max_seconds = min(float(timeout_seconds), _WATCH_MAX_SECONDS)
            deadline = time.monotonic() + max_seconds
            event_count = 0
            seen: dict[str, dict] = {}
            for obj in k8s_watch_objects(state, plan, query):
                seen[k8s_watch_object_key(obj)] = obj
                if not self._write_watch_event("ADDED", obj):
                    return event_count
                event_count += 1
            if not self._flush_event_stream():
                return event_count
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return event_count
                if state.shutdown_event.wait(min(_WATCH_POLL_SECONDS, remaining)):
                    return event_count
                current = {
                    k8s_watch_object_key(obj): obj
                    for obj in k8s_watch_objects(state, plan, query)
                }
                emitted = False
                for key, obj in current.items():
                    prev = seen.get(key)
                    if prev is None:
                        if not self._write_watch_event("ADDED", obj):
                            return event_count
                        event_count += 1
                        emitted = True
                    elif prev != obj:
                        if not self._write_watch_event("MODIFIED", obj):
                            return event_count
                        event_count += 1
                        emitted = True
                for key, obj in seen.items():
                    if key not in current:
                        if not self._write_watch_event("DELETED", obj):
                            return event_count
                        event_count += 1
                        emitted = True
                seen = current
                if emitted and not self._flush_event_stream():
                    return event_count

        def _send_watch_stream_headers(self) -> None:
            self.send_response(200)
            self._response_bytes = 0
            # Kubernetes watch wire shape: newline-delimited JSON objects under
            # a plain application/json content type, streamed to EOF (no
            # content-length), mirroring the SSE handlers' header discipline.
            self.send_header("content-type", "application/json")
            self.send_header("cache-control", "no-cache")
            self.send_header("x-content-type-options", "nosniff")
            self.send_header("referrer-policy", "no-referrer")
            self._send_cors_headers()
            self.end_headers()

        def _write_watch_event(self, event_type: str, obj: dict) -> bool:
            payload = json.dumps({"type": event_type, "object": obj}, sort_keys=True)
            return self._write_event_stream((payload + "\n").encode("utf-8"))

        def _send_mcp_post(self) -> None:
            """Answer one streamable-HTTP MCP message at POST /mcp.

            The JSON-RPC layer (parse errors, dispatch, tool errors) lives
            in server_mcp; this method only moves bytes and maps the shared
            body cap onto a JSON-RPC-shaped 413 instead of the app-endpoint
            error shape.
            """
            try:
                raw = server_mcp.read_mcp_request_body(self, security.max_body_bytes)
            except RequestBodyTooLarge as exc:
                self._remember_structured_error(exc)
                self._send_json(413, server_mcp.body_too_large_response(str(exc)))
                return
            status, body = server_mcp.handle_mcp_http_post(
                state, raw, client=self.client_address[0], request_id=self._request_id
            )
            if body is None:
                # Notification: 202 Accepted with no content.
                self.send_response(status)
                self._send_common_headers("application/json; charset=utf-8", 0)
                self.end_headers()
                return
            self._send_json(status, body)

        def _send_log_file(self) -> bool:
            # Safe against concurrent regeneration by construction: the
            # generator publishes metric_report.log via legacy's
            # _atomic_artifact_open (temp sibling + os.replace), so this
            # open() only ever sees the complete previous or complete new
            # file, and a continuous-generate rerun with the same emit
            # selection never deletes it. The not-present branch below is
            # for runs that genuinely dropped `logs` from --emit.
            log_path = state.output_dir / "metric_report.log"
            if not log_path.exists():
                payload = json.dumps({"line": "metric_report.log is not present for this run"})
                return self._write_event_stream(f"data: {payload}\n\n".encode("utf-8"))
            with open(log_path, encoding="utf-8") as f:
                for line in f:
                    payload = json.dumps({"line": line.rstrip("\n")})
                    if not self._write_event_stream(f"data: {payload}\n\n".encode("utf-8")):
                        return False
            return True

    return _Handler


def _log_file_signature(path: Path) -> tuple[int, int] | None:
    with contextlib.suppress(OSError):
        stat = path.stat()
        return stat.st_mtime_ns, stat.st_size
    return None


def _query_int(query: dict[str, list[str]], name: str, default: int) -> int:
    try:
        return int(query.get(name, [str(default)])[0])
    except ValueError:
        return default


def _query_str(query: dict[str, list[str]], name: str, default: str) -> str:
    return query.get(name, [default])[0].strip()


def _scenario_payload(state: SimulationState) -> dict[str, Any]:
    return {
        "active": list(state.active_scenarios),
        "known": [
            _scenario_detail_payload(state, slug, scenario)
            for slug, scenario in state.legacy.SCENARIOS.items()
        ],
    }


def _scenario_detail_payload(state: SimulationState, slug: str, scenario: Any) -> dict[str, Any]:
    profile = OPS_SCENARIO_PROFILES.get(slug)
    return {
        "id": slug,
        "name": scenario.name,
        "severity": scenario.severity,
        "days_required": scenario.days_required,
        "category": scenario.category,
        "active": slug in state.active_scenarios,
        "components_touched": list(scenario.components_touched),
        "primary_specs": [
            _scenario_spec_payload(component, spec)
            for component, spec in scenario.primary_specs
        ],
        "cascade_specs": [
            _scenario_spec_payload(component, spec)
            for component, spec in scenario.cascade_specs
        ],
        "ops_profile": profile is not None,
        "ops_profile_detail": _ops_profile_payload(profile) if profile is not None else None,
    }


def _scenario_spec_payload(component: str, spec: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "component": component,
        "metric": str(spec.get("metric", "")),
        "description": str(spec.get("description", "")),
        "time_offset_seconds": spec.get("time_offset"),
        "duration_seconds": spec.get("duration_seconds"),
        "shape": spec.get("shape"),
        "severity": spec.get("severity", ""),
    }
    shape_params = spec.get("shape_params")
    if shape_params is not None:
        payload["shape_params"] = _json_safe_payload(shape_params)
    instance_filter = spec.get("instance_filter")
    if instance_filter is not None:
        payload["instance_filter"] = _json_safe_payload(instance_filter)
    return payload


def _ops_profile_payload(profile: OpsScenarioProfile) -> dict[str, Any]:
    return {
        "summary": profile.summary,
        "affected_components": list(profile.affected_components),
        "events": list(profile.events),
        "logs": list(profile.logs),
        "helm_notes": profile.helm_notes,
        "rollout_note": profile.rollout_note,
        "impacts": [
            {
                "component": impact.component,
                "deployment_status": impact.deployment_status,
                "pod_status": impact.pod_status,
                "ready": impact.ready,
                "ready_replicas": impact.ready_replicas,
                "ready_replicas_delta": impact.ready_replicas_delta,
                "restarts": impact.restarts,
                "cpu_pct": impact.cpu_pct,
                "cpu_m": impact.cpu_m,
                "memory_mi": impact.memory_mi,
                "memory_pct": impact.memory_pct,
                "pvc_used_pct": impact.pvc_used_pct,
            }
            for impact in profile.impacts
        ],
    }


def _json_safe_payload(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if callable(value):
        return "<callable>"
    if isinstance(value, dict):
        return {
            str(key): _json_safe_payload(value[key])
            for key in sorted(value, key=str)
        }
    if isinstance(value, (list, tuple)):
        return [_json_safe_payload(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted(
            (_json_safe_payload(item) for item in value),
            key=_json_safe_sort_key,
        )
    return str(value)


def _json_safe_sort_key(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))





_SERVE_CONFIG_SERVER_KEYS = {
    "host",
    "port",
    "namespace",
    "debug_ring_size",
    "persist_command_log",
    "persist_command_db",
    "persist_command_retention",
    "persist_mutations",
    "auth_token",
    "max_request_body_bytes",
    "allow_remote_without_auth",
    "cors_allow_origin",
    "rate_limit_per_minute",
    "structured_log",
    "structured_log_file",
    "no_generate",
    "continuous_generate",
    "continuous_generate_interval_seconds",
}


def _load_serve_config(path: Path) -> dict[str, Any]:
    suffix = path.suffix.lower()
    is_yaml = suffix in {".yaml", ".yml"}
    if suffix not in {".json", ".yaml", ".yml"}:
        raise ValueError(
            f"--config must be a .json, .yaml, or .yml file; got {path}"
        )
    if is_yaml:
        try:
            import yaml
        except ImportError as exc:
            raise ValueError(
                f"--config {path}: PyYAML is required to parse YAML files "
                "but is not installed. Install it with 'pip install pyyaml' "
                "or use a .json file instead."
            ) from exc
        parse_exc_types: tuple[type[Exception], ...] = (
            yaml.YAMLError,
            UnicodeDecodeError,
        )
    else:
        parse_exc_types = (json.JSONDecodeError, UnicodeDecodeError)
    try:
        with path.open(encoding="utf-8") as f:
            raw = yaml.safe_load(f) if is_yaml else json.load(f)
    except OSError as exc:
        raise ValueError(f"--config {path}: failed to read file: {exc}") from exc
    except parse_exc_types as exc:
        label = "YAML" if is_yaml else "JSON"
        raise ValueError(f"--config {path}: failed to parse {label}: {exc}") from exc
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError("--config must contain a JSON/YAML object")
    unknown_top = set(raw) - {"server", "generate"}
    if unknown_top:
        # str() every key before sorting: YAML admits non-string keys, and a
        # mixed set raises TypeError comparing an int to a str -- which would
        # escape the ValueError that names the file, the whole point of
        # validating here.
        raise ValueError(
            "--config only accepts top-level 'server' and 'generate' keys; "
            f"got {', '.join(sorted(str(key) for key in unknown_top))}"
        )
    server = raw.get("server", {})
    generate = raw.get("generate", {})
    if not isinstance(server, dict):
        raise ValueError("--config server must be an object")
    if not isinstance(generate, dict):
        raise ValueError("--config generate must be an object")
    # YAML admits non-string keys (`1:`, `true:`), which JSON cannot produce.
    # Left alone they reach `key.replace("_", "-")` and raise AttributeError,
    # escaping the ValueError refusal that names the file -- and they cannot be
    # sorted alongside string keys either. `--config` is an untrusted read-back
    # boundary: check the shape here, on the reader side.
    for section_name, mapping in (("server", server), ("generate", generate)):
        non_string = [key for key in mapping if not isinstance(key, str)]
        if non_string:
            raise _config_error(
                path,
                f"{section_name} keys must be strings; got "
                + ", ".join(repr(key) for key in non_string),
            )
    unknown_server = set(server) - _SERVE_CONFIG_SERVER_KEYS
    if unknown_server:
        # Routed through _config_error like the generate arm: a bad key in
        # either section names the file it came from, which is what the
        # README's `--config` row promises. Attribution is the whole point of
        # validating at load rather than letting a later parse fail bare.
        raise _config_error(
            path,
            "server contains unknown key(s): "
            + ", ".join(sorted(unknown_server)),
        )
    return {"server": dict(server), "generate": dict(generate)}


def _extract_serve_config_path(
    argv: list[str],
    parser: argparse.ArgumentParser,
) -> Path | None:
    config_parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    config_parser.add_argument("--config", type=Path)
    try:
        config_args, _ = config_parser.parse_known_args(argv)
    except SystemExit:
        parser.error("--config requires a file path")
    return config_args.config


def _strip_serve_config_arg(argv: list[str]) -> list[str]:
    result: list[str] = []
    skip_next = False
    for token in argv:
        if skip_next:
            skip_next = False
            continue
        if token == "--config":
            skip_next = True
            continue
        if token.startswith("--config="):
            continue
        result.append(token)
    return result


def _config_error(config_path: Path | None, detail: str) -> ValueError:
    """Build the shared ``--config`` diagnostic so every arm names the file."""
    prefix = f"--config {config_path}: " if config_path is not None else "--config: "
    return ValueError(prefix + detail)


def _config_mapping_to_argv(config: dict[str, Any]) -> list[str]:
    """Convert one config section to argv. Pure conversion, no validation.

    Both sections are validated elsewhere -- `server` names against
    `_SERVE_CONFIG_SERVER_KEYS`, `generate` against the real parser -- so this
    function neither needs the section it is converting nor the file it came
    from.
    """
    argv: list[str] = []
    for key, value in config.items():
        # `null` and `false` are the two shapes that emit nothing, so the argv
        # probe never sees these keys. Conversion stays a pure conversion and
        # _vouch_no_flag_generate_keys checks them separately, against the same
        # real parser -- validating here would need this function to hold the
        # parser too.
        if value is None or value is False:
            continue
        flag = "--" + key.replace("_", "-")
        if isinstance(value, bool):
            # Only `True` reaches here; `False` was skipped above.
            argv.append(flag)
            continue
        if isinstance(value, (list, tuple)):
            argv.extend([flag, ",".join(str(item) for item in value)])
            continue
        argv.extend([flag, str(value)])
    return argv


def _resolve_generate_parse_args(legacy_module: Any | None = None) -> Callable[..., Any]:
    """Return the generate parser entrypoint, importing legacy lazily."""
    if legacy_module is None:
        from . import legacy as legacy_module
    return legacy_module.parse_args


def _probe_config_generate_argv(
    generate_argv: list[str],
    config_path: Path | None,
    parse_args: Callable[..., Any],
) -> None:
    """Reject config-derived generate flags the real parser would not accept.

    The generate surface has no introspectable allowlist -- ``parse_args``
    builds its parser inline -- so rather than hand-maintaining a second list
    that would drift, the real parser *is* the allowlist: parse the
    config-derived argv on its own and convert argparse's exit into a
    ``ValueError`` naming the config file. This is exactly the parse
    ``serve_main`` runs later, moved earlier and given file attribution, so it
    rejects nothing that would have survived anyway.

    Both streams are captured: argparse writes diagnostics to stderr, and a
    ``help: true`` config would otherwise dump usage to stdout.
    """
    if not generate_argv:
        return
    stderr = io.StringIO()
    stdout = io.StringIO()
    try:
        with contextlib.redirect_stderr(stderr), contextlib.redirect_stdout(stdout):
            parse_args(list(generate_argv))
    except SystemExit as exc:
        if exc.code == 0:
            # A successful exit is not a rejection: `help: true` makes argparse
            # print usage and stop. Reporting that as "rejected by the parser"
            # names the wrong problem, and the captured stderr is empty, so the
            # generic arm below would surface a bare "exited with status 0".
            raise _config_error(
                config_path,
                "generate section made the parser print output and exit "
                "successfully instead of producing a configuration -- a key "
                "like 'help' or 'version' does this. Remove it.",
            ) from exc
        lines = [line for line in stderr.getvalue().strip().splitlines() if line]
        diagnostic = lines[-1] if lines else f"generate parser exited with status {exc.code}"
        raise _config_error(
            config_path,
            "generate section was rejected by the generate parser: " + diagnostic,
        ) from exc


def _vouch_no_flag_generate_keys(
    config: dict[str, Any],
    config_path: Path | None,
    parse_args: Callable[..., Any],
) -> None:
    """Check the generate keys whose value produces no flag at all.

    ``null`` and ``false`` emit nothing, so the argv probe never sees them and
    a typo would vanish entirely rather than becoming a bogus flag -- the PRD's
    "collides with nothing" case. Refusing both outright would be wrong in the
    other direction: ``otel_verbose: false`` is a real key whose off state is
    exactly what the operator wrote, and refusing it would regress a config
    that works today.

    So each such key is vouched for the same way every other key is -- by
    asking the real parser, never a second hand-maintained list. A key whose
    flag parses *on its own* is a real switch, and dropping it keeps its
    documented meaning of "use the default". Everything else is refused naming
    the file: a typo (``--componentss``), or a value-taking flag where these
    values are meaningless anyway (``--components`` alone is an error, and
    ``components: null`` cannot mean anything else).

    ``server`` keys never come here: they are already name-checked against
    ``_SERVE_CONFIG_SERVER_KEYS``, so neither shape can hide a typo there.
    """
    for key, value in config.items():
        if value is not None and value is not False:
            continue
        flag = "--" + key.replace("_", "-")
        try:
            with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(
                io.StringIO()
            ):
                parse_args([flag])
        except SystemExit as exc:
            shape = "null" if value is None else "false"
            if exc.code == 0:
                # `--help` and `--version` are recognized, so saying the parser
                # does not accept the flag would be false. They exit instead of
                # configuring anything, which no value can make meaningful.
                raise _config_error(
                    config_path,
                    f"generate key '{key}' has a {shape} value, and '{flag}' "
                    "makes the parser print output and exit rather than "
                    "configure a run, so no value for it is meaningful. "
                    "Remove the key.",
                ) from exc
            raise _config_error(
                config_path,
                f"generate key '{key}' has a {shape} value, so it produces no "
                f"flag for the parser to check, and '{flag}' on its own is not "
                "a switch the generate parser accepts. Remove the key to use "
                "its default, or give it a value.",
            ) from exc


def _parse_serve_args(
    argv: list[str],
    parser: argparse.ArgumentParser,
    *,
    legacy_module: Any | None = None,
) -> tuple[argparse.Namespace, list[str]]:
    raw_argv = list(argv)
    config_path = _extract_serve_config_path(raw_argv, parser)
    config_server_argv: list[str] = []
    config_generate_argv: list[str] = []
    if config_path is not None:
        try:
            config = _load_serve_config(config_path)
            config_server_argv = _config_mapping_to_argv(config["server"])
            config_generate_argv = _config_mapping_to_argv(config["generate"])
            generate_parse_args = _resolve_generate_parse_args(legacy_module)
            _probe_config_generate_argv(
                config_generate_argv, config_path, generate_parse_args
            )
            _vouch_no_flag_generate_keys(
                config["generate"], config_path, generate_parse_args
            )
        except ValueError as exc:
            parser.error(str(exc))
    user_argv = _strip_serve_config_arg(raw_argv)
    serve_args, generate_argv = parser.parse_known_args(
        [*config_server_argv, *config_generate_argv, *user_argv]
    )
    serve_args.config = config_path
    return serve_args, generate_argv


def _build_serve_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="anomaly-metric-creator.py serve",
        description="Run the anomaly simulator as an HTTP server with Kubernetes/Helm command responses.",
        epilog=(
            "Any unrecognized options are parsed as normal generate options "
            "(for example --scenarios, --components, --duration-days, "
            "--otel-send, and --otel-endpoint)."
        ),
        allow_abbrev=False,
    )
    parser.add_argument("--host", default="127.0.0.1", help="HTTP bind host (default: 127.0.0.1).")
    parser.add_argument("--port", type=int, default=8088, help="HTTP bind port (default: 8088; use 0 for ephemeral).")
    parser.add_argument("--namespace", default=DEFAULT_NAMESPACE, help=f"Simulated Kubernetes namespace (default: {DEFAULT_NAMESPACE}).")
    parser.add_argument("--debug-ring-size", type=int, default=DEFAULT_TRACE_LIMIT, help=f"Command trace ring size (default: {DEFAULT_TRACE_LIMIT}).")
    parser.add_argument("--persist-command-log", type=Path, default=None, help="Optional JSONL path for command traces.")
    parser.add_argument("--persist-command-db", type=Path, default=None, help="Optional SQLite path for durable command traces and search.")
    parser.add_argument("--persist-command-retention", type=int, default=0, help="Maximum SQLite command traces to retain (default: 0, unlimited).")
    parser.add_argument("--persist-mutations", type=Path, default=None, help="Optional JSON path giving the simulator mutation overlay restart continuity. Keep it outside --output-dir.")
    parser.add_argument("--config", type=Path, default=None, help="Optional JSON/YAML file with serve-mode server and generate defaults.")
    parser.add_argument("--auth-token", default="", help="Optional bearer token required for simulator HTTP and Kubernetes API requests.")
    parser.add_argument("--max-request-body-bytes", type=int, default=DEFAULT_MAX_BODY_BYTES, help=f"Maximum accepted HTTP request body size (default: {DEFAULT_MAX_BODY_BYTES}).")
    parser.add_argument("--allow-remote-without-auth", action="store_true", help="Allow non-loopback binds without --auth-token for isolated lab use.")
    parser.add_argument("--mcp-eval-mode", action="store_true", help="Hide every ground-truth-bearing surface (anomaly manifest, scenario catalog, /v1/state, the report-log stream, and the /v1/debug console + UI) so an agent evaluated via /mcp cannot read the scoring rubric; the MCP log tools refuse too. Keeps the kubectl/Helm/commands investigation surface open.")
    parser.add_argument("--cors-allow-origin", default="", help="Optional CORS Access-Control-Allow-Origin value for browser clients.")
    parser.add_argument("--rate-limit-per-minute", type=int, default=0, help="Optional per-client command/Kubernetes API request limit per minute (default: 0, off).")
    parser.add_argument("--max-concurrent-requests", type=int, default=DEFAULT_MAX_CONCURRENT_REQUESTS, help=f"Cap on concurrent worker threads; over-cap connections get a fast 503 (default: {DEFAULT_MAX_CONCURRENT_REQUESTS}; 0 disables the bound).")
    parser.add_argument("--max-sse-connections", type=int, default=DEFAULT_MAX_SSE_CONNECTIONS, help=f"Cap on concurrent SSE streams (/v1/debug/events, /v1/logs/stream) so long-lived streams cannot monopolize the worker pool (default: {DEFAULT_MAX_SSE_CONNECTIONS}; 0 disables the bound).")
    parser.add_argument("--socket-timeout-seconds", type=float, default=DEFAULT_SOCKET_TIMEOUT_SECONDS, help=f"Per-connection socket timeout guarding against slow-loris clients (default: {DEFAULT_SOCKET_TIMEOUT_SECONDS}; 0 disables the timeout).")
    parser.add_argument("--structured-log", action=argparse.BooleanOptionalAction, default=False, help="Emit structured JSONL request/error logs to stderr or --structured-log-file.")
    parser.add_argument("--structured-log-file", type=Path, default=None, help="Optional JSONL path for structured request/error logs.")
    parser.add_argument("--no-generate", action="store_true", help="Use existing artifacts in --output-dir instead of generating before serving.")
    parser.add_argument("--continuous-generate", action="store_true", help="Continuously regenerate artifacts while the server runs.")
    parser.add_argument("--continuous-generate-interval-seconds", type=float, default=60.0, help="Seconds between continuous generation passes (default: 60).")
    return parser


def _is_loopback_bind_host(host: str) -> bool:
    value = host.strip().strip("[]").lower()
    if value in {"localhost", "127.0.0.1", "::1"}:
        return True
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return False


def _url_authority(host: str, port: int) -> str:
    """Render ``host:port`` as a valid URL authority.

    An IPv6 literal (``::1``, ``fe80::1``) must be bracketed or the
    ``http://<host>:<port>`` form is ambiguous/invalid — ``http://::1:8088``
    is not a parseable URL, ``http://[::1]:8088`` is. Already-bracketed and
    IPv4/hostname values pass through unchanged.
    """
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"{host}:{port}"


def _print_inspection_banner(
    host: str,
    port: int,
    namespace: str,
    security: "ServerSecurityConfig",
    *,
    eval_mode: bool,
    active_scenarios: tuple[str, ...],
) -> None:
    """Print copyable kubectl/Helm/reset inspection commands after startup.

    Closes the interactive-failure-mode-launcher affordance gap: the base
    startup prints only the three URLs, so an operator has no ready recipe
    to attach a real ``kubectl``/Helm client or reset the simulator overlay.

    Security-sensitive token rendering (see design.md): a real bearer token
    is echoed into the curl examples only on a loopback bind. On a
    non-loopback bind the examples carry a ``$AMC_TOKEN`` placeholder
    instead so the *printed* commands do not carry the token into a remote
    shell history or log (the operator's own launch invocation still holds
    it; this only keeps the banner from re-emitting it).

    The ``Active scenarios`` line is suppressed entirely under
    ``--mcp-eval-mode``: operator stdout is not an agent-reachable surface,
    but suppressing it keeps every scenario-slug emission behind one uniform
    ground-truth-wall rule at no cost.
    """
    base = f"http://{_url_authority(host, port)}"
    if not security.auth_token:
        auth_header = ""
    elif _is_loopback_bind_host(host):
        auth_header = f' -H "Authorization: Bearer {security.auth_token}"'
    else:
        auth_header = ' -H "Authorization: Bearer $AMC_TOKEN"'
    print("Inspect the running environment:")
    print(f"  curl -fsS{auth_header} {base}/v1/kubeconfig -o amc-kubeconfig")
    print("  export KUBECONFIG=$PWD/amc-kubeconfig")
    print(f"  kubectl get pods -n {namespace}")
    print(f"  kubectl get events -n {namespace}")
    print(f"  helm list -n {namespace}")
    print(f"  curl -X POST{auth_header} {base}/v1/mutations/reset  # reset overlay")
    if not eval_mode:
        slugs = ", ".join(active_scenarios) if active_scenarios else "none"
        print(f"Active scenarios: {slugs}")


_EVAL_NO_PERSIST_WARNING = (
    "WARNING: eval mode has no --persist-command-db/--persist-command-log; "
    "command traces live only in the in-memory ring and are unrecoverable "
    "after shutdown (the /v1/debug export surface is rubric-hidden in eval "
    "mode). Pass --persist-command-db PATH (or --persist-command-log PATH) "
    "to retain scoring evidence."
)


def serve_main(argv: list[str] | None = None, *, legacy_module: Any | None = None) -> None:
    if legacy_module is None:
        from . import legacy as legacy_module

    parser = _build_serve_parser()
    serve_args, generate_argv = _parse_serve_args(
        list(argv or []), parser, legacy_module=legacy_module
    )
    if serve_args.debug_ring_size < 1:
        parser.error("--debug-ring-size must be >= 1")
    if serve_args.port < 0 or serve_args.port > 65535:
        parser.error("--port must be in [0, 65535]")
    if serve_args.max_request_body_bytes < 1:
        parser.error("--max-request-body-bytes must be >= 1")
    if serve_args.rate_limit_per_minute < 0:
        parser.error("--rate-limit-per-minute must be >= 0")
    if serve_args.max_concurrent_requests < 0:
        parser.error("--max-concurrent-requests must be >= 0")
    if serve_args.max_sse_connections < 0:
        parser.error("--max-sse-connections must be >= 0")
    if serve_args.socket_timeout_seconds < 0:
        parser.error("--socket-timeout-seconds must be >= 0")
    if serve_args.continuous_generate_interval_seconds <= 0:
        parser.error("--continuous-generate-interval-seconds must be > 0")
    if (
        not _is_loopback_bind_host(serve_args.host)
        and not serve_args.auth_token
        and not serve_args.allow_remote_without_auth
    ):
        parser.error(
            "--host binds outside loopback; pass --auth-token or "
            "--allow-remote-without-auth"
        )
    if serve_args.cors_allow_origin.strip() == "*" and not serve_args.auth_token:
        # There is no safe unauthenticated wildcard posture, loopback included:
        # any website the operator visits can read a 127.0.0.1 bind's debug and
        # rubric surfaces cross-origin. --allow-remote-without-auth does not
        # unlock this — it covers the bind host, not the browser origin.
        parser.error(
            "--cors-allow-origin '*' exposes every origin to an "
            "unauthenticated server; pass --auth-token or name an explicit "
            "origin instead of '*'"
        )

    args = legacy_module.parse_args(generate_argv)
    if not serve_args.no_generate:
        run_argv = _generation_argv_without_otel(generate_argv)
        legacy_module.main(run_argv)
        args = legacy_module.parse_args(generate_argv)

    try:
        state = build_state(
            legacy_module,
            args,
            namespace=serve_args.namespace,
            trace_limit=serve_args.debug_ring_size,
            persist_command_log=serve_args.persist_command_log,
            persist_command_db=serve_args.persist_command_db,
            persist_command_retention=serve_args.persist_command_retention,
            persist_mutations=serve_args.persist_mutations,
            eval_mode=serve_args.mcp_eval_mode,
        )
    except ValueError as exc:
        # An unreadable persisted overlay is an operator-facing startup
        # refusal, not a traceback. Match the loader's own marker rather than
        # assuming it is the only thing under build_state() that can raise
        # ValueError: gating on the flag alone would convert *every*
        # ValueError into a refusal whenever the flag is set, hiding an
        # unrelated regression behind an operator message. Any other
        # ValueError re-raises unchanged so a real bug stays a real bug.
        if serve_args.persist_mutations is None or not str(exc).startswith(
            PERSIST_ERROR_PREFIX
        ):
            raise
        raise SystemExit(f"amc serve: {exc}") from exc
    # Build the structured-log sink before the background threads start and
    # attach it to the state, so a continuous-generation / OTEL failure in the
    # first interval routes through _record_server_error to the configured
    # logger rather than only stderr. None is the default (stderr fallback).
    request_logger = None
    if serve_args.structured_log or serve_args.structured_log_file is not None:
        request_logger = StructuredRequestLogger(serve_args.structured_log_file)
    state.request_logger = request_logger
    generation_stop = _start_continuous_generation(
        state,
        generate_argv,
        enabled=serve_args.continuous_generate,
        interval_seconds=serve_args.continuous_generate_interval_seconds,
        stream_otel=bool(getattr(args, "otel_enabled", False)),
    )
    if not serve_args.continuous_generate:
        _start_otel_background(state)

    security = ServerSecurityConfig(
        auth_token=serve_args.auth_token,
        max_body_bytes=serve_args.max_request_body_bytes,
        allow_remote_without_auth=serve_args.allow_remote_without_auth,
        cors_allow_origin=serve_args.cors_allow_origin,
        rate_limit_per_minute=serve_args.rate_limit_per_minute,
        max_concurrent_requests=serve_args.max_concurrent_requests,
        max_sse_connections=serve_args.max_sse_connections,
        socket_timeout_seconds=serve_args.socket_timeout_seconds,
    )
    httpd = _BoundedThreadingHTTPServer(
        (serve_args.host, serve_args.port),
        make_handler(state, security=security, request_logger=request_logger),
        max_workers=security.max_concurrent_requests,
        max_sse=security.max_sse_connections,
        refusals=state.refusals,
    )
    # server_address is a 2-tuple for AF_INET and a 4-tuple for AF_INET6;
    # take the first two so an IPv6 bind cannot ValueError on unpack.
    host, port = httpd.server_address[:2]
    authority = _url_authority(host, port)
    print(f"AMC simulator server listening on http://{authority}/debug")
    print(f"Command API: POST http://{authority}/v1/commands")
    print(f"Kubeconfig: http://{authority}/v1/kubeconfig")
    if serve_args.mcp_eval_mode:
        print(
            "MCP eval mode: ground-truth surfaces hidden "
            "(manifest, scenarios, /v1/state, log stream, debug console)"
        )
        if (
            serve_args.persist_command_db is None
            and serve_args.persist_command_log is None
        ):
            # Eval mode 404s the /v1/debug command-trace export and the ring
            # dies with the process, so with no persistence a harness recovers
            # no record of the agent's activity. Operator stderr is not an
            # agent-reachable surface, so this stays wall-safe.
            print(_EVAL_NO_PERSIST_WARNING, file=sys.stderr)
    if security.auth_token:
        print("Bearer auth: enabled")
    elif not _is_loopback_bind_host(serve_args.host):
        print("WARNING: remote bind is running without bearer auth", file=sys.stderr)
    _print_inspection_banner(
        host,
        port,
        serve_args.namespace,
        security,
        eval_mode=serve_args.mcp_eval_mode,
        active_scenarios=tuple(getattr(state, "active_scenarios", ()) or ()),
    )
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nAMC simulator server stopping", file=sys.stderr)
    finally:
        state.shutdown_event.set()
        _stop_continuous_generation(generation_stop)
        httpd.server_close()


def _generation_argv_without_otel(generate_argv: list[str]) -> list[str]:
    # Server mode owns OTEL streaming separately from one-shot artifact
    # generation. The generation pass still writes metrics/logs/traces, but
    # this override prevents legacy main() from streaming and blocking server
    # startup or the continuous generation loop.
    return [*generate_argv, "--otel-send", "none"]


def _readyz_check(state: SimulationState) -> tuple[bool, str]:
    """Two-dimension readiness for /readyz.

    Returns ``(ready, reason)``. ``reason`` names the failing dimension only —
    ``"artifacts"`` or ``"generation"`` — never scenario content, so the
    endpoint stays eval-wall-safe (it is auth-exempt and eval-open by design).

    - artifacts: every filename the run *declared* it would emit (derived from
      the same ``_collect_emitted_filenames`` registry the pre-clean and schema
      views use, keyed off the run's emit selection — not a hardcoded list) is
      present on disk. A ``--no-generate`` run over an empty dir fails here.
    - generation: the continuous-generation worker's last pass did not fail. A
      ``disabled`` thread (no continuous generation) is healthy.
    """
    try:
        expected = state.legacy._collect_emitted_filenames(
            emit_selection=getattr(state.args, "emit_selection", ()),
            components=state.components,
            combine=bool(getattr(state.args, "combine", False)),
        )
    except Exception:
        # A registry lookup failure is itself an unready signal rather than a
        # 500 on the health probe.
        return False, "artifacts"
    for filename in expected:
        if not (state.output_dir / filename).exists():
            return False, "artifacts"
    if state.generation.thread == "failed":
        return False, "generation"
    return True, ""


def _start_continuous_generation(
    state: SimulationState,
    generate_argv: list[str],
    *,
    enabled: bool,
    interval_seconds: float,
    stream_otel: bool = False,
) -> threading.Event | None:
    with state.generation.lock:
        state.generation.enabled = enabled
        state.generation.interval_seconds = interval_seconds
        state.generation.thread = "not_started" if enabled else "disabled"
        state.generation.last_anomaly_count = len(state.anomaly_rows)
    if not enabled:
        return None
    if stream_otel:
        state.update_otel_status(thread="waiting", continuous=True)
    else:
        state.update_otel_status(thread="disabled", continuous=False)

    stop_event = threading.Event()

    def _run() -> None:
        with state.generation.lock:
            state.generation.thread = "running"
        if stream_otel:
            _stream_current_otel_once(state, idle_thread_state="waiting")
        while not stop_event.wait(interval_seconds):
            _run_continuous_generation_once(state, generate_argv, stream_otel=stream_otel)
        with state.generation.lock:
            state.generation.thread = "stopped"
        if stream_otel:
            state.update_otel_status(thread="stopped")

    thread = threading.Thread(target=_run, name="amc-continuous-generation", daemon=True)
    stop_event.worker_thread = thread
    thread.start()
    return stop_event


def _stop_continuous_generation(
    stop_event: threading.Event | None,
    *,
    timeout: float = 5.0,
) -> None:
    if stop_event is None:
        return
    stop_event.set()
    worker = getattr(stop_event, "worker_thread", None)
    if worker is None:
        return
    is_alive = getattr(worker, "is_alive", None)
    join = getattr(worker, "join", None)
    if not callable(join):
        return
    if callable(is_alive) and not is_alive():
        return
    join(timeout)


def _run_continuous_generation_once(
    state: SimulationState,
    generate_argv: list[str],
    *,
    stream_otel: bool = False,
) -> None:
    # Artifact visibility during the rerun: every writer inside main()
    # publishes via legacy's _atomic_artifact_open, so HTTP reader threads
    # observe each artifact switch from the previous run's complete content
    # to the new run's complete content with no truncated/missing window.
    # The combine/gauge writers only read CSVs published earlier in the same
    # single-threaded main() call, and reruns are serialized on this one
    # worker thread, so no combine pass can be mid-read on a CSV another
    # rerun is swapping.
    with state.generation.lock:
        next_count = state.generation.generation_count + 1
        seed = int(getattr(state.args, "seed", 42)) + next_count
        state.generation.thread = "running"
        state.generation.last_started_at = _dt.datetime.now(_dt.timezone.utc).isoformat()
        state.generation.last_seed = seed
        state.generation.last_error = ""
    run_argv = [*_generation_argv_without_otel(generate_argv), "--seed", str(seed)]
    try:
        state.legacy.main(run_argv)
        rows = load_anomaly_rows(state.output_dir / "anomalies.csv")
        state.replace_generated_rows(rows)
    except SystemExit as exc:  # pragma: no cover - defensive background boundary
        _record_continuous_generation_failure(state, exc)
        return
    except Exception as exc:  # pragma: no cover - defensive background boundary
        _record_continuous_generation_failure(state, exc)
        return
    with state.generation.lock:
        state.generation.generation_count = next_count
        state.generation.last_completed_at = _dt.datetime.now(_dt.timezone.utc).isoformat()
        state.generation.last_anomaly_count = len(rows)
    if stream_otel:
        _stream_current_otel_once(state, idle_thread_state="waiting")


def _start_otel_background(state: SimulationState) -> None:
    args = state.args
    if not getattr(args, "otel_enabled", False):
        state.update_otel_status(thread="disabled")
        return

    def _run() -> None:
        _stream_current_otel_once(state, idle_thread_state="completed")

    thread = threading.Thread(target=_run, name="amc-otel-stream", daemon=True)
    thread.start()


def _stream_current_otel_once(state: SimulationState, *, idle_thread_state: str) -> None:
    if not getattr(state.args, "otel_enabled", False):
        state.update_otel_status(thread="disabled")
        return
    state.update_otel_status(
        thread="running",
        last_started_at=_dt.datetime.now(_dt.timezone.utc).isoformat(),
    )
    try:
        _run_otel_streams(state)
    except Exception as exc:  # pragma: no cover - defensive thread boundary
        state.update_otel_status(thread="failed", error=str(exc))
        # Also route to the operator error sink: the /v1/state otel_status.error
        # is eval-hidden, so without this a background OTEL failure is invisible
        # in the default posture. Inside the except block, so the traceback tail
        # is captured.
        _record_server_error(
            getattr(state, "request_logger", None),
            where="otel-stream",
            exc=exc,
        )
        return
    state.update_otel_status(
        thread=idle_thread_state,
        last_completed_at=_dt.datetime.now(_dt.timezone.utc).isoformat(),
    )
    state.bump_otel_status("stream_batches")


def _run_otel_streams(state: SimulationState) -> None:
    args = state.args
    legacy = state.legacy
    endpoints = {
        "logs": args.otel_logs_endpoint,
        "metrics": args.otel_metrics_endpoint,
        "traces": args.otel_traces_endpoint,
    }
    signal_selection = getattr(args, "otel_signal_selection", None)
    if signal_selection is not None:
        signal_endpoints = {
            sig: (url if sig in signal_selection else None)
            for sig, url in endpoints.items()
        }
    else:
        signal_endpoints = endpoints
    auth_headers = {}
    for signal in ["logs", "metrics", "traces"]:
        token = getattr(args, f"otel_{signal}_auth_token")
        if token:
            auth_headers[signal] = {"Authorization": f"{args.otel_stream_auth_scheme} {token}"}

    if not args.otel_gauges_only and any(signal_endpoints.values()):
        sent = legacy.stream_otel_signals(
            signal_endpoints,
            state.generated_rows(),
            speedup=args.otel_stream_speedup,
            timeout_seconds=args.otel_stream_timeout_seconds,
            max_events=args.otel_stream_max_events,
            auth_headers=auth_headers,
            protocol=args.otel_stream_protocol,
            activity_log_path=args.otel_activity_log,
            verbose=args.otel_verbose,
        )
        state.update_otel_status(signal_events_sent=sent)
    if args.otel_emit_gauges:
        component_csv_paths = {
            c: args.output_dir / f"{c}.csv" for c in sorted(args.components)
        }
        sent = legacy.stream_otel_gauges(
            component_csv_paths,
            endpoint=args.otel_metrics_endpoint,
            batch_seconds=args.otel_gauge_batch_seconds,
            metric_prefix=args.otel_gauge_metric_prefix,
            speedup=args.otel_stream_speedup,
            timeout_seconds=args.otel_stream_timeout_seconds,
            max_events=args.otel_stream_max_events,
            max_retries=3,
            auth_headers=auth_headers.get("metrics"),
            protocol=args.otel_stream_protocol,
            activity_log_path=args.otel_activity_log,
            verbose=args.otel_verbose,
            append_activity_log=not args.otel_gauges_only,
        )
        state.update_otel_status(gauge_requests_sent=sent)


def start_test_server(
    state: SimulationState,
    security: ServerSecurityConfig | None = None,
    request_logger: StructuredRequestLogger | None = None,
) -> tuple[ThreadingHTTPServer, str]:
    """Start an ephemeral server for tests and return (server, base_url)."""

    resolved = security or ServerSecurityConfig()
    # Parity with serve_main's flag gate: the wildcard origin is refused without
    # a bearer token here too, so a test or embedding caller cannot reach a
    # posture the CLI refuses to start.
    if resolved.cors_allow_origin.strip() == "*" and not resolved.auth_token:
        raise ValueError(
            "cors_allow_origin '*' requires auth_token; name an explicit "
            "origin instead of '*'"
        )
    # Keep the state's background-arm sink in step with the handler's request
    # sink so tests that drive a failing regen/OTEL pass through this entry
    # point see the same _record_server_error routing serve_main wires up.
    state.request_logger = request_logger
    httpd = _BoundedThreadingHTTPServer(
        ("127.0.0.1", 0),
        make_handler(state, security=security, request_logger=request_logger),
        max_workers=resolved.max_concurrent_requests,
        max_sse=resolved.max_sse_connections,
        refusals=state.refusals,
    )
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    host, port = httpd.server_address
    return httpd, f"http://{host}:{port}"


def temp_output_dir() -> Path:
    return Path(tempfile.mkdtemp(prefix="amc-server-"))
