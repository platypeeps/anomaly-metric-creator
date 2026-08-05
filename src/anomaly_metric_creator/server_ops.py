"""Ops simulation surfaces shared by the serve-mode HTTP facade.

This module owns scenario profiles, simulator state, command rendering,
resource snapshots, Kubernetes-compatible API objects, and Helm release Secret
encoding. Client-command parsing lives in ``server_ops_parse.py`` and is
re-imported below. ``server.py`` imports and re-exports these names for
compatibility.
"""

from __future__ import annotations

import contextlib
import csv
import datetime as _dt
import json
import sys
import threading
import time
import traceback
import urllib.parse
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any, Protocol

from .server_mutations import (
    DEFAULT_NAMESPACE,
    HelmReleaseMutation as HelmReleaseMutation,
    SimulationMutations,
    WorkloadMutation as WorkloadMutation,
    _mutation_resource_key,
    _resource_prefix,
)
from .server_traces import (
    DEFAULT_TRACE_LIMIT,
    CommandTrace,
    CommandTraceStore,
)

from .server_ops_support import (
    DEFAULT_RELEASE as DEFAULT_RELEASE,
    DEFAULT_CHART as DEFAULT_CHART,
    _snapshot_row_namespace as _snapshot_row_namespace,
    _snapshot_row_labels as _snapshot_row_labels,
    _parse_user_timestamp as _parse_user_timestamp,
    _parse_optional_timestamp as _parse_optional_timestamp,
    _string_dict as _string_dict,
    _k8s_list_resource_version as _k8s_list_resource_version,
)
DEFAULT_MAX_BODY_BYTES = 1024 * 1024
_K8S_ADVERTISED_VERSION = "1.36.2"
_K8S_ADVERTISED_TAG = f"v{_K8S_ADVERTISED_VERSION}"
_K8S_ADVERTISED_GIT_VERSION = f"{_K8S_ADVERTISED_TAG}-amc"


def _query_int(query: dict[str, list[str]], name: str, default: int) -> int:
    try:
        return int(query.get(name, [str(default)])[0])
    except ValueError:
        return default


def _query_str(query: dict[str, list[str]], name: str, default: str) -> str:
    return query.get(name, [default])[0].strip()


from .server_ops_profiles import (
    OPS_SCENARIO_PROFILES as OPS_SCENARIO_PROFILES,
    OpsComponentImpact as OpsComponentImpact,
    OpsScenarioProfile as OpsScenarioProfile,
    _impact as _impact,
    _profile as _profile,
    validate_ops_profiles as validate_ops_profiles,
)


@dataclass
class SimulationClock:
    """Wall-clock to synthetic-time mapping used by server mode."""

    start_time: _dt.datetime
    speedup: float
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _base_wall: float = field(default_factory=time.time)
    _base_sim: _dt.datetime = field(init=False)
    _paused: bool = False

    def __post_init__(self) -> None:
        self._base_sim = self.start_time

    def now(self) -> _dt.datetime:
        with self._lock:
            if self._paused:
                return self._base_sim
            elapsed = max(0.0, time.time() - self._base_wall) * self.speedup
            return self._base_sim + _dt.timedelta(seconds=elapsed)

    def pause(self) -> _dt.datetime:
        with self._lock:
            if not self._paused:
                elapsed = max(0.0, time.time() - self._base_wall) * self.speedup
                self._base_sim = self._base_sim + _dt.timedelta(seconds=elapsed)
                self._paused = True
            return self._base_sim

    def resume(self) -> _dt.datetime:
        with self._lock:
            if not self._paused:
                # Already running — resume is a no-op. Resetting _base_wall here
                # would discard the elapsed time accrued since the last base and
                # rewind simulated time (audit A-012); return the live sim time.
                elapsed = max(0.0, time.time() - self._base_wall) * self.speedup
                return self._base_sim + _dt.timedelta(seconds=elapsed)
            self._base_wall = time.time()
            self._paused = False
            return self._base_sim

    def seek(self, timestamp: str) -> _dt.datetime:
        parsed = _parse_user_timestamp(timestamp)
        with self._lock:
            self._base_sim = parsed
            self._base_wall = time.time()
            return self._base_sim

    def to_dict(self) -> dict[str, Any]:
        return {
            "simulated_time": _format_dt(self.now()),
            "speedup": self.speedup,
            "paused": self._paused,
        }


# Client-command parsing lives in server_ops_parse.py (one-way import; the leaf
# never imports server_ops). Re-imported here at ParsedCommand's original
# position so the historic server_ops.<name> surface and __all__ stay stable.
# Only names the staying renderers use or that __all__ re-exports are re-imported;
# leaf-internal parse helpers (e.g. _split_flags's _store_flag_value, the explain
# token splitters, the raw flag tables) stay solely in the leaf.
from .server_ops_parse import (
    ParsedCommand as ParsedCommand,
    _SENSITIVE_FLAG_TOKENS as _SENSITIVE_FLAG_TOKENS,
    _MODELED_FLAGS as _MODELED_FLAGS,
    _KIND_ALIASES as _KIND_ALIASES,
    _EXPLAIN_RESOURCE_TARGETS as _EXPLAIN_RESOURCE_TARGETS,
    parse_command as parse_command,
    _split_flags as _split_flags,
    _flag_values as _flag_values,
    _first_flag_value as _first_flag_value,
    _parse_kubectl as _parse_kubectl,
    _parse_helm as _parse_helm,
    _split_resource_token as _split_resource_token,
    _normalize_kind as _normalize_kind,
    command_fingerprint as command_fingerprint,
    guess_intent as guess_intent,
    _redact_command_for_trace as _redact_command_for_trace,
    _redact_argv as _redact_argv,
    _redact_parsed_flags as _redact_parsed_flags,
    _is_sensitive_flag_name as _is_sensitive_flag_name,
)


from .server_command_render import (
    CommandResult as CommandResult,
    _exposed_active_scenarios as _exposed_active_scenarios,
    _is_dry_run as _is_dry_run,
    _table as _table,
    _unsupported as _unsupported,
)
from .server_mutations import _format_dt as _format_dt


@dataclass(frozen=True)
class KubernetesApiResponse:
    status: int
    body: Any
    content_type: str
    support_status: str
    matched_rule_id: str


@dataclass
class ContinuousGenerationStatus:
    enabled: bool = False
    interval_seconds: float = 0.0
    thread: str = "disabled"
    generation_count: int = 0
    last_started_at: str = ""
    last_completed_at: str = ""
    last_error: str = ""
    last_anomaly_count: int = 0
    last_seed: int | None = None
    lock: threading.RLock = field(default_factory=threading.RLock)

    def to_dict(self) -> dict[str, Any]:
        with self.lock:
            return {
                "enabled": self.enabled,
                "interval_seconds": self.interval_seconds,
                "thread": self.thread,
                "generation_count": self.generation_count,
                "last_started_at": self.last_started_at,
                "last_completed_at": self.last_completed_at,
                "last_error": self.last_error,
                "last_anomaly_count": self.last_anomaly_count,
                "last_seed": self.last_seed,
            }


# Cap the traceback tail that reaches an operator sink. A capped tail keeps the
# failing frame(s) without letting a deep recursion flood stderr or the JSONL
# error log on every retry.
class _ErrorSink(Protocol):
    """Structural interface for the operator error/request sink.

    The only concrete implementation is ``server.StructuredRequestLogger``, which
    lives in ``server.py`` and cannot be imported here (the module DAG is one-way:
    ``server`` imports ``server_ops``, never the reverse). Declaring the interface
    structurally lets ``SimulationState.request_logger`` and the sink helpers carry
    a precise type instead of ``Any`` while preserving the one-way import.
    """

    def log_request(self, record: dict[str, Any]) -> None:
        pass

    def log_error(self, record: dict[str, Any]) -> None:
        pass


_ERROR_TRACEBACK_MAX_LINES = 30


def _capture_traceback_tail(*, max_lines: int = _ERROR_TRACEBACK_MAX_LINES) -> str:
    """Return the current exception's formatted traceback, capped to the tail.

    Reads ``traceback.format_exc()`` so it must be called while an exception is
    being handled (inside the ``except`` block or a helper it calls). Returns an
    empty string when no exception is active.
    """
    text = traceback.format_exc()
    if not text or text.strip() == "NoneType: None":
        return ""
    lines = text.rstrip("\n").split("\n")
    if len(lines) > max_lines:
        # Strict cap: the truncation marker counts against ``max_lines`` so the
        # returned block is never longer than the configured flood guard. Reserve
        # one slot for the marker and keep the last ``max_lines - 1`` tail lines
        # (marker-only when ``max_lines <= 1``, so a tiny cap can't reintroduce a
        # ``lines[-0:]`` whole-list slice).
        marker = "...(traceback truncated)..."
        keep = max_lines - 1
        lines = [marker, *lines[-keep:]] if keep > 0 else [marker]
    return "\n".join(lines)


def _emit_error_record(request_logger: _ErrorSink | None, record: dict[str, Any]) -> None:
    """Write one error record to the structured logger, or a stderr block when
    no logger is configured.

    The either/or is deliberate: with ``--structured-log`` the record lands as a
    JSONL ``error`` event; without it (the default posture) the same detail —
    including the traceback tail — still reaches stderr, so a default-flags 500
    is never silent. Detail is operator-side only and must never reach a client
    response body (SECURITY.md contract). The traceback text may embed the
    exception message; stderr and the structured log are operator surfaces, so
    this stays outside the eval-mode ground-truth wall (it never reads or writes
    the anomaly manifest or scenario catalog).
    """
    if request_logger is not None:
        request_logger.log_error(record)
        return
    where = record.get("where") or "request"
    header = (
        f"[serve-error] {where}: "
        f"{record.get('error_type', 'Error')}: {record.get('message', '')}"
    )
    lines = [header]
    path = record.get("path")
    if path:
        lines.append(f"  path: {path}")
    tail = record.get("traceback")
    if tail:
        lines.append(tail)
    sys.stderr.write("\n".join(lines) + "\n")
    sys.stderr.flush()


def _record_server_error(
    request_logger: _ErrorSink | None,
    *,
    where: str,
    exc: BaseException,
    path: str | None = None,
) -> None:
    """Capture ``exc`` (type, message, traceback tail) to the operator error
    sink via :func:`_emit_error_record`.

    Call from inside the active ``except`` block so the traceback is available.
    Used by the HTTP 500 boundaries, the mutating-method boundary, the
    background continuous-generation / OTEL arms, and the MCP internal-error
    path so every error plane has one operator-visible sink by default.
    """
    record: dict[str, Any] = {
        "where": where,
        "error_type": type(exc).__name__,
        "message": str(exc),
        "traceback": _capture_traceback_tail(),
    }
    if path is not None:
        record["path"] = path
    _emit_error_record(request_logger, record)


# Kinds of DoS-bound request refusal counted by ``RefusalCounters``. Fixed
# vocabulary: worker-thread cap (raw 503 before a worker spawns), SSE-slot
# ceiling (JSON 503), and per-client rate limit (429). No scenario content —
# eval-wall-safe.
_REFUSAL_KINDS = ("worker_cap", "sse", "rate_limit")


class RefusalCounters:
    """Thread-safe tally of DoS-bound request refusals (A-075).

    The bounded server, SSE ceiling, and rate limiter each shed load to keep a
    reachable instance from exhausting threads/streams/CPU, but those refusals
    were previously invisible in the default posture: nothing counted them and
    nothing logged them. This counts each kind and exposes the totals on
    ``SimulationState.summary()`` (``/v1/state.refusals``) so an operator can see
    the instance is shedding load. Increments are the only mutation and take the
    lock per refusal (never per request), so the lock stays off the hot path.

    The first trip of each kind emits one stderr line so saturation is visible
    even when structured request logging is off. It is capped at one line per
    kind per process on purpose: per-window re-logging under a sustained attack
    would turn the refusal path into its own stderr-amplification vector, so the
    first-trip line announces the condition and ``/v1/state.refusals`` carries
    the live count thereafter.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counts = dict.fromkeys(_REFUSAL_KINDS, 0)
        self._logged: set[str] = set()

    def _increment(self, kind: str) -> bool:
        """Count one refusal under the lock; return whether it is the first
        of its kind (so the caller can emit the one-shot stderr line off the
        lock)."""
        with self._lock:
            self._counts[kind] += 1
            if kind in self._logged:
                return False
            self._logged.add(kind)
            return True

    def record(self, kind: str) -> None:
        if kind not in self._counts:
            raise KeyError(f"unknown refusal kind: {kind!r}")
        if self._increment(kind):
            sys.stderr.write(
                f"[serve-refusal] first {kind} refusal — instance shedding "
                "load; see /v1/state.refusals for the running count\n"
            )
            sys.stderr.flush()

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return dict(self._counts)


@dataclass
class SimulationState:
    legacy: Any
    args: Any
    output_dir: Path
    namespace: str
    active_scenarios: tuple[str, ...]
    components: tuple[str, ...]
    anomaly_rows: list[dict[str, str]]
    clock: SimulationClock
    traces: CommandTraceStore
    mutations: SimulationMutations = field(default_factory=SimulationMutations)
    generation: ContinuousGenerationStatus = field(default_factory=ContinuousGenerationStatus)
    otel_status: dict[str, Any] = field(default_factory=dict)
    # Guards otel_status against a torn read from /v1/state (audit A-014): the
    # background OTEL / continuous-generation threads mutate this dict while
    # ``summary()`` runs on an HTTP handler thread. Thread-safety rests on the
    # lock alone, not on the key set being fixed: every writer
    # (``update_otel_status`` / ``bump_otel_status``) mutates under this lock
    # and ``otel_status_snapshot`` copies the dict under it, so the reader never
    # iterates the live dict — a writer adding a new key can never race the
    # snapshot into a "dictionary changed size during iteration". Call sites
    # today only ever write a known, stable key set, so no resize happens in
    # practice; the lock is what makes that safe regardless.
    otel_status_lock: threading.Lock = field(default_factory=threading.Lock)
    shutdown_event: threading.Event = field(default_factory=threading.Event)
    # Optional structured-log sink, wired by ``serve_main`` after the state is
    # built. Background arms (continuous generation, OTEL streaming) read it to
    # route a failure through ``_record_server_error``; ``None`` means the
    # helper falls back to a stderr block, so background failures are visible
    # even without ``--structured-log``.
    request_logger: _ErrorSink | None = None
    # DoS-bound refusal tally (A-075), shared with the bounded server so
    # worker-cap / SSE-ceiling / rate-limit refusals surface on
    # ``summary()``. Default-factory keeps direct SimulationState() constructions
    # (tests) working; ``serve_main`` / ``start_test_server`` pass the same
    # instance into ``_BoundedThreadingHTTPServer`` so both sides increment one
    # counter.
    refusals: RefusalCounters = field(default_factory=RefusalCounters)
    # Eval mode hides every ground-truth-bearing surface (the anomaly
    # manifest, the scenario catalog, the report-log rendering of the
    # manifest, and the debug console) so an agent under evaluation cannot
    # read the scoring rubric. Single source of truth: both the HTTP route
    # dispatch and the MCP log tools read this one flag.
    eval_mode: bool = False

    def profiles(self) -> list[OpsScenarioProfile]:
        profiles: list[OpsScenarioProfile] = []
        for scenario_id in self.active_scenarios:
            profile = OPS_SCENARIO_PROFILES.get(scenario_id)
            if profile is not None:
                profiles.append(profile)
        return profiles

    def summary(self) -> dict[str, Any]:
        return {
            "namespace": self.namespace,
            "output_dir": str(self.output_dir),
            "clock": self.clock.to_dict(),
            "active_scenarios": list(self.active_scenarios),
            "components": list(self.components),
            "anomaly_count": self.generated_row_count(),
            "command_trace_count": self.traces.count(),
            "unsupported_group_count": self.traces.unsupported_fingerprint_count(),
            "otel": self.otel_status_snapshot(),
            "generation": self.generation.to_dict(),
            "refusals": self.refusals.snapshot(),
            "mutations": self.mutations.summary(),
            "profiles": [
                {
                    "scenario_id": profile.scenario_id,
                    "summary": profile.summary,
                    "affected_components": list(profile.affected_components),
                }
                for profile in self.profiles()
            ],
            "active_anomalies": self.active_anomalies(limit=20),
        }

    def otel_status_snapshot(self) -> dict[str, Any]:
        """Return a consistent copy of otel_status under the lock (A-014)."""
        with self.otel_status_lock:
            return dict(self.otel_status)

    def update_otel_status(self, **changes: Any) -> None:
        """Apply one or more otel_status field updates atomically (A-014)."""
        with self.otel_status_lock:
            self.otel_status.update(changes)

    def bump_otel_status(self, key: str, amount: int = 1) -> int:
        """Increment an integer otel_status counter under the lock (A-014)."""
        with self.otel_status_lock:
            new_value = int(self.otel_status.get(key, 0)) + amount
            self.otel_status[key] = new_value
            return new_value

    def active_anomalies(self, limit: int = 50) -> list[dict[str, str]]:
        now = self.clock.now()
        matches: list[dict[str, str]] = []
        for row in self._generated_rows_reference():
            start = _parse_optional_timestamp(row.get("span_start") or row.get("timestamp"))
            end = _parse_optional_timestamp(row.get("span_end") or row.get("timestamp"))
            if start is None or end is None:
                continue
            if start <= now <= end:
                matches.append(row)
                if len(matches) >= limit:
                    break
        return matches

    def generated_rows(self) -> list[dict[str, str]]:
        with self.generation.lock:
            return list(self.anomaly_rows)

    def generated_rows_slice(self, limit: int) -> list[dict[str, str]]:
        with self.generation.lock:
            return list(self.anomaly_rows[:max(limit, 0)])

    def _generated_rows_reference(self) -> list[dict[str, str]]:
        with self.generation.lock:
            return self.anomaly_rows

    def generated_row_count(self) -> int:
        with self.generation.lock:
            return len(self.anomaly_rows)

    def replace_generated_rows(self, rows: list[dict[str, str]]) -> None:
        with self.generation.lock:
            self.anomaly_rows = rows


def build_state(
    legacy_module: Any,
    args: Any,
    *,
    namespace: str = DEFAULT_NAMESPACE,
    trace_limit: int = DEFAULT_TRACE_LIMIT,
    persist_command_log: Path | None = None,
    persist_command_db: Path | None = None,
    persist_command_retention: int | None = None,
    eval_mode: bool = False,
) -> SimulationState:
    validate_ops_profiles(legacy_module)
    active_scenarios = tuple(sorted(legacy_module._resolve_scenarios(args)))
    components = tuple(name for name in legacy_module.COMPONENTS if name in args.components)
    anomaly_rows = load_anomaly_rows(args.output_dir / "anomalies.csv")
    clock = SimulationClock(
        start_time=getattr(args, "start_time", legacy_module.START),
        speedup=float(getattr(args, "otel_stream_speedup", 3600.0)),
    )
    return SimulationState(
        legacy=legacy_module,
        args=args,
        output_dir=args.output_dir,
        namespace=namespace,
        active_scenarios=active_scenarios,
        components=components,
        anomaly_rows=anomaly_rows,
        clock=clock,
        traces=CommandTraceStore(
            limit=trace_limit,
            persist_path=persist_command_log,
            sqlite_path=persist_command_db,
            sqlite_retention=persist_command_retention,
        ),
        mutations=SimulationMutations(extra_event_limit=trace_limit),
        # Convention (not a safety mechanism): every key the background OTEL /
        # continuous-generation writers ever set is pre-seeded here so the
        # schema is stable and /v1/state always reports the full field set.
        # Thread-safety is provided by otel_status_lock, not by this seeding —
        # every read (otel_status_snapshot) and write (update/bump) holds the
        # lock, so even a writer adding an unforeseen key cannot race the
        # snapshot copy (audit A-014).
        otel_status={
            "enabled": bool(getattr(args, "otel_enabled", False)),
            "signals": sorted(getattr(args, "otel_signal_selection", None) or []),
            "gauges": bool(getattr(args, "otel_emit_gauges", False)),
            "thread": "not_started",
            "continuous": False,
            "last_started_at": None,
            "last_completed_at": None,
            "error": "",
            "stream_batches": 0,
            "signal_events_sent": 0,
            "gauge_requests_sent": 0,
        },
        eval_mode=eval_mode,
    )


def load_anomaly_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


_SENSITIVE_QUERY_KEYS = {
    "access_token",
    "api_key",
    "apikey",
    "authorization",
    "auth_token",
    "bearer_token",
    "client_key",
    "client_secret",
    "id_token",
    "password",
    "refresh_token",
    "secret",
    "token",
}
_SNAPSHOT_KINDS = {
    "namespaces",
    "pods",
    "configmaps",
    "secrets",
    "replicationcontrollers",
    "deployments",
    "replicasets",
    "daemonsets",
    "services",
    "endpoints",
    "endpointslices",
    "events",
    "hpa",
    "jobs",
    "cronjobs",
    "serviceaccounts",
    "nodes",
    "pvc",
    "statefulsets",
    "ingress",
}
_MUTATION_SNAPSHOT_KINDS = {
    "configmaps",
    "secrets",
    "deployments",
    "daemonsets",
    "services",
    "hpa",
    "jobs",
    "cronjobs",
    "serviceaccounts",
    "pvc",
    "statefulsets",
    "ingress",
}
_CLUSTER_SCOPED_SNAPSHOT_KINDS = {"namespaces", "nodes"}
_NAMESPACED_SNAPSHOT_KINDS = _SNAPSHOT_KINDS - _CLUSTER_SCOPED_SNAPSHOT_KINDS


_EXPLAIN_RESOURCE_DESCRIPTIONS = {
    "namespaces": "Namespace is a cluster-scoped boundary for AMC simulator resources.",
    "nodes": "Node is a simulated Kubernetes worker node that hosts AMC pods.",
    "pods": "Pod is a simulator-backed workload instance derived from resource_snapshot().",
    "configmaps": "ConfigMap exposes non-sensitive AMC simulator configuration data.",
    "secrets": "Secret exposes simulator Secret metadata and redacted data payload shape.",
    "replicationcontrollers": "ReplicationController is advertised for compatibility; AMC does not create baseline objects.",
    "services": "Service exposes the stable virtual endpoint for a simulated component.",
    "endpoints": "Endpoints exposes pod IPs selected by a simulated Service.",
    "events": "Event records scenario and mutation activity in Kubernetes-compatible form.",
    "pvc": "PersistentVolumeClaim exposes simulated storage pressure for stateful components.",
    "serviceaccounts": "ServiceAccount exposes identities used by simulator workloads.",
    "deployments": "Deployment describes desired and observed state for a simulated component workload.",
    "replicasets": "ReplicaSet is projected from simulated Deployment ownership.",
    "daemonsets": "DaemonSet describes node-level simulator agents.",
    "statefulsets": "StatefulSet describes stateful simulator workloads such as the database.",
    "hpa": "HorizontalPodAutoscaler exposes simulated scaling targets and current metrics.",
    "jobs": "Job describes one-shot simulator maintenance work.",
    "cronjobs": "CronJob describes recurring simulator maintenance work.",
    "endpointslices": "EndpointSlice exposes Service endpoint subsets for real kubectl clients.",
    "ingress": "Ingress exposes the simulator edge route for the API gateway.",
}


def _snapshot_row_key(row: dict[str, Any], default_namespace: str = DEFAULT_NAMESPACE) -> str:
    return _mutation_resource_key(_snapshot_row_namespace(row, default_namespace), str(row.get("name", "")))


def _snapshot_kind_namespaced(kind: str) -> bool:
    return kind in _NAMESPACED_SNAPSHOT_KINDS or kind in {"hpa", "pvc", "ingress"}


def run_command(
    state: SimulationState,
    *,
    command: str | None = None,
    argv: list[str] | tuple[str, ...] | None = None,
    client: str = "api",
    request_id: str = "",
) -> dict[str, Any]:
    started = time.perf_counter()
    received = _dt.datetime.now(_dt.timezone.utc).isoformat()
    parsed = parse_command(command=command, argv=argv, default_namespace=state.namespace)
    simulated_time = _format_dt(state.clock.now())
    result = render_command(state, parsed)
    latency_ms = (time.perf_counter() - started) * 1000.0
    fingerprint = command_fingerprint(parsed, result.support_status)
    redacted_raw_input = _redact_command_for_trace(parsed)
    trace = CommandTrace(
        id=state.traces.next_id(),
        received_at_wall_time=received,
        simulated_time=simulated_time,
        raw_input=redacted_raw_input,
        argv=_redact_argv(parsed.argv),
        client=client,
        command_family=parsed.family,
        verb=parsed.verb,
        resource_kind=parsed.resource_kind,
        resource_name=parsed.resource_name,
        namespace=parsed.namespace,
        parsed_flags=_redact_parsed_flags(parsed.flags),
        support_status=result.support_status,
        matched_rule_id=result.matched_rule_id,
        active_scenarios=state.active_scenarios,
        exit_code=result.exit_code,
        stdout_preview=_preview(result.stdout),
        stderr_preview=_preview(result.stderr),
        stdout=result.stdout,
        stderr=result.stderr,
        latency_ms=round(latency_ms, 3),
        fingerprint=fingerprint,
        guessed_intent=guess_intent(parsed),
        request_id=request_id,
    )
    state.traces.record(trace)
    # The stored trace keeps the real active_scenarios: the walled
    # /v1/debug/* and /v1/debug/commands/export surfaces are the eval
    # harness's scoring data. But /v1/commands is investigation-open, so the
    # echoed trace must be scrubbed in eval mode — otherwise every command
    # response carries the full active-scenario list regardless of the
    # command run. stdout/stderr are already render-redacted upstream.
    trace_dict = trace.to_dict()
    trace_dict["active_scenarios"] = list(_exposed_active_scenarios(state))
    return {
        "trace": trace_dict,
        "result": {
            "exit_code": result.exit_code,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "support_status": result.support_status,
            "matched_rule_id": result.matched_rule_id,
        },
    }


def render_command(state: SimulationState, parsed: ParsedCommand) -> CommandResult:
    if parsed.parse_error:
        return CommandResult(2, "", parsed.parse_error + "\n", "unsupported", "parse.error")
    if parsed.family == "kubectl":
        return _with_flag_support(parsed, _render_kubectl(state, parsed))
    if parsed.family == "helm":
        return _with_flag_support(parsed, _render_helm(state, parsed))
    return CommandResult(
        127,
        "",
        f"{parsed.family or 'command'}: command not supported by simulator\n",
        "unsupported",
        "family.unsupported",
    )


def _with_flag_support(parsed: ParsedCommand, result: CommandResult) -> CommandResult:
    if result.support_status != "supported":
        return result
    unmodeled = sorted(flag for flag in parsed.flags if flag not in _MODELED_FLAGS)
    if not unmodeled:
        return result
    warning = "warning: flag(s) parsed but not modeled: " + ", ".join(unmodeled) + "\n"
    return CommandResult(
        result.exit_code,
        result.stdout,
        result.stderr + warning,
        "partial",
        result.matched_rule_id + ".partial-flags",
    )


def _render_kubectl(state: SimulationState, parsed: ParsedCommand) -> CommandResult:
    kind = parsed.resource_kind
    if parsed.verb == "version":
        return CommandResult(0, _render_kubectl_version(), "", "supported", "kubectl.version")
    if parsed.verb == "api-versions":
        return CommandResult(0, _render_kubectl_api_versions(), "", "supported", "kubectl.api-versions")
    if parsed.verb == "api-resources":
        return CommandResult(0, _render_kubectl_api_resources(), "", "supported", "kubectl.api-resources")
    if parsed.verb == "cluster-info":
        return CommandResult(0, _render_kubectl_cluster_info(), "", "supported", "kubectl.cluster-info")
    if parsed.verb == "explain":
        return _render_explain(state, parsed)
    if parsed.verb == "config current-context":
        return CommandResult(0, "amc-simulator\n", "", "supported", "kubectl.config.current-context")
    if parsed.verb == "config view":
        return CommandResult(
            0,
            render_kubeconfig("http://127.0.0.1:8088", state.namespace),
            "",
            "supported",
            "kubectl.config.view",
        )
    if parsed.verb == "auth can-i":
        return CommandResult(0, "yes\n", "", "supported", "kubectl.auth.can-i")
    if parsed.verb == "get":
        if parsed.flags.get("--watch") or parsed.flags.get("-w"):
            return _render_get_watch(state, kind, parsed)
        if kind in _SNAPSHOT_KINDS or kind == "all":
            return CommandResult(
                0, _render_get(state, kind, parsed), "", "supported", f"kubectl.get.{kind}"
            )
        return _unsupported(parsed, f"kubectl get {kind or '<missing-kind>'}")
    if parsed.verb == "describe":
        if kind in _SNAPSHOT_KINDS:
            return _render_describe(state, kind, parsed)
        return _unsupported(parsed, f"kubectl describe {kind or '<missing-kind>'}")
    if parsed.verb == "logs":
        if parsed.resource_name or _logs_uses_selector(parsed):
            return _render_logs_command(state, parsed)
        return CommandResult(1, "", "error: expected pod name for logs\n", "partial", "kubectl.logs.missing-pod")
    if parsed.verb == "top":
        if kind in {"pods", "nodes"}:
            return CommandResult(0, _render_top(state, kind), "", "supported", f"kubectl.top.{kind}")
        return _unsupported(parsed, f"kubectl top {kind or '<missing-kind>'}")
    if parsed.verb == "rollout status":
        if _is_deployment_rollout_target(parsed):
            return CommandResult(
                0, _render_rollout_status(state, parsed), "", "supported", "kubectl.rollout.status"
            )
        return _unsupported(parsed, "kubectl rollout status")
    if parsed.verb == "rollout history":
        if _is_deployment_rollout_target(parsed):
            return CommandResult(
                0, _render_rollout_history(state, parsed), "", "supported", "kubectl.rollout.history"
            )
        return _unsupported(parsed, "kubectl rollout history")
    if parsed.verb == "rollout restart":
        if _is_deployment_rollout_target(parsed):
            return CommandResult(
                0, _render_rollout_restart(state, parsed), "", "supported", "kubectl.rollout.restart"
            )
        return _unsupported(parsed, "kubectl rollout restart")
    if parsed.verb == "rollout pause":
        if _is_deployment_rollout_target(parsed):
            return CommandResult(
                0, _render_rollout_pause(state, parsed), "", "supported", "kubectl.rollout.pause"
            )
        return _unsupported(parsed, "kubectl rollout pause")
    if parsed.verb == "rollout resume":
        if _is_deployment_rollout_target(parsed):
            return CommandResult(
                0, _render_rollout_resume(state, parsed), "", "supported", "kubectl.rollout.resume"
            )
        return _unsupported(parsed, "kubectl rollout resume")
    if parsed.verb == "rollout undo":
        if _is_deployment_rollout_target(parsed):
            return CommandResult(
                0, _render_rollout_undo(state, parsed), "", "supported", "kubectl.rollout.undo"
            )
        return _unsupported(parsed, "kubectl rollout undo")
    if parsed.verb == "scale":
        scale_result = _render_scale(state, parsed)
        if isinstance(scale_result, CommandResult):
            return scale_result
        return CommandResult(0, scale_result, "", "supported", "kubectl.scale")
    if parsed.verb == "delete":
        delete_result = _render_delete(state, parsed)
        if isinstance(delete_result, CommandResult):
            return delete_result
        return CommandResult(0, delete_result, "", "supported", "kubectl.delete")
    if parsed.verb == "patch":
        return _render_patch(state, parsed)
    if parsed.verb == "diff":
        return _render_diff(state, parsed)
    if parsed.verb in {"apply", "create"}:
        return _render_apply(state, parsed)
    if parsed.verb == "wait":
        return CommandResult(0, _render_wait(state, parsed), "", "supported", "kubectl.wait")
    if parsed.verb == "exec":
        return CommandResult(0, _render_exec(state, parsed), "", "supported", "kubectl.exec")
    if parsed.verb == "port-forward":
        return CommandResult(
            0, _render_port_forward(parsed), "", "supported", "kubectl.port-forward"
        )
    return _unsupported(parsed, f"kubectl {parsed.verb or '<missing-verb>'}")


from .server_helm_impl import _render_helm  # noqa: F401  (re-import at original position)


def resource_snapshot(state: SimulationState) -> dict[str, list[dict[str, Any]]]:
    pods: list[dict[str, Any]] = []
    deployments: list[dict[str, Any]] = []
    replicasets: list[dict[str, Any]] = []
    services: list[dict[str, Any]] = []
    endpoints: list[dict[str, Any]] = []
    endpointslices: list[dict[str, Any]] = []
    hpas: list[dict[str, Any]] = []
    pvcs: list[dict[str, Any]] = []
    statefulsets: list[dict[str, Any]] = []
    daemonsets: list[dict[str, Any]] = []
    jobs: list[dict[str, Any]] = []
    cronjobs: list[dict[str, Any]] = []
    configmaps: list[dict[str, Any]] = []
    serviceaccounts: list[dict[str, Any]] = []
    ingress: list[dict[str, Any]] = []
    nodes = _node_rows(state)

    serviceaccounts.extend([
        {"name": "default", "secrets": 0, "age": "30d"},
        {"name": DEFAULT_RELEASE, "secrets": 0, "age": "7d"},
    ])
    configmaps.extend([
        {
            "name": "simulated-saas-config",
            "data": 4,
            "age": "7d",
            "keys": {
                "LOG_LEVEL": "info",
                "FEATURE_FLAGS": "checkout_v2,adaptive_cache",
                "OTEL_EXPORTER": "enabled",
                "SCENARIOS": ",".join(_exposed_active_scenarios(state)),
            },
        },
        {
            "name": "simulated-saas-runbook",
            "data": 2,
            "age": "7d",
            "keys": {
                "summary": "Synthetic incident runbook for AMC server mode",
                "first_steps": "kubectl get pods; kubectl get events; helm status simulated-saas",
            },
        },
    ])

    with state.mutations.lock:
        deleted_components = {
            name for name, mutation in state.mutations.workloads.items()
            if mutation.deleted
        }
        deleted_pods = set(state.mutations.deleted_pods)
        workload_metadata = {
            name: {
                "generation": mutation.generation,
                "observed_generation": mutation.observed_generation,
                "resource_version": str(mutation.resource_version),
                "deletion_timestamp": mutation.deletion_timestamp,
            }
            for name, mutation in state.mutations.workloads.items()
        }

    for component in state.components:
        if component in deleted_components:
            continue
        health = _component_health(state, component)
        replicas = _replica_count(state, component)
        ready_replicas = min(replicas, health["ready_replicas"])
        metadata = workload_metadata.get(component, {})
        generation = int(metadata.get("generation", 1) or 1)
        observed_generation = int(metadata.get("observed_generation", generation) or generation)
        resource_version = str(metadata.get("resource_version", "1") or "1")
        deployments.append({
            "name": component,
            "ready": f"{ready_replicas}/{replicas}",
            "up_to_date": ready_replicas,
            "available": ready_replicas,
            "age": "7d",
            "status": health["deployment_status"],
            "generation": generation,
            "observed_generation": observed_generation,
            "resource_version": resource_version,
        })
        replicasets.append({
            "name": f"{component}-6d9f7c8b9d",
            "desired": replicas,
            "current": replicas,
            "ready": ready_replicas,
            "age": "7d",
            "owner": component,
            "resource_version": resource_version,
        })
        services.append({
            "name": component,
            "type": "ClusterIP",
            "cluster_ip": _stable_cluster_ip(component),
            "external_ip": "<none>",
            "ports": "8080/TCP",
            "age": "7d",
        })
        endpoints.append({
            "name": component,
            "endpoints": "",
            "ports": "8080",
            "age": "7d",
        })
        endpointslices.append({
            "name": f"{component}-slice",
            "address_type": "IPv4",
            "ports": "8080",
            "endpoints": 0,
            "age": "7d",
            "service": component,
        })
        hpas.append({
            "name": component,
            "reference": f"Deployment/{component}",
            "targets": f"{health['cpu_pct']}%/80%",
            "minpods": 1,
            "maxpods": 8,
            "replicas": replicas,
            "age": "7d",
        })
        endpoint_ips: list[str] = []
        deleted_for_component: list[str] = []
        # Loop-invariant per component: hoist above the replica loop so a
        # high --instances-per-component run does not recompute the profile
        # scan / mutations-lock walk once per pod. The returned lists are
        # read-only downstream, so sharing one object across pods is
        # output-identical.
        component_scenario_ids = _exposed_component_scenarios(state, component)
        component_events = _component_events(state, component)
        for index in range(replicas):
            pod_name = _pod_name(component, index)
            if pod_name in deleted_pods:
                deleted_for_component.append(pod_name)
                continue
            pod_ip = _stable_pod_ip(pod_name)
            endpoint_ips.append(pod_ip)
            pods.append({
                "name": pod_name,
                "component": component,
                "ready": health["ready"],
                "status": health["pod_status"],
                "restarts": health["restarts"] + (1 if index == 0 and health["pod_status"] != "Running" else 0),
                "age": "7d",
                "node": nodes[index % len(nodes)]["name"],
                "pod_ip": pod_ip,
                "cpu_m": health["cpu_m"],
                "memory_mi": health["memory_mi"],
                "scenario_ids": component_scenario_ids,
                "events": component_events,
                "resource_version": resource_version,
            })
        for replacement_index, deleted_pod_name in enumerate(deleted_for_component):
            replacement_name = f"{component}-recreated-{replacement_index}"
            pod_ip = _stable_pod_ip(replacement_name)
            endpoint_ips.append(pod_ip)
            pods.append({
                "name": replacement_name,
                "component": component,
                "ready": health["ready"],
                "status": health["pod_status"],
                "restarts": health["restarts"],
                "age": "0s",
                "node": nodes[(replicas + replacement_index) % len(nodes)]["name"],
                "pod_ip": pod_ip,
                "cpu_m": health["cpu_m"],
                "memory_mi": health["memory_mi"],
                "scenario_ids": component_scenario_ids,
                "events": component_events,
                "recreated_from": deleted_pod_name,
                "resource_version": resource_version,
            })
        endpoints[-1]["endpoints"] = ",".join(f"{ip}:8080" for ip in endpoint_ips[:3])
        endpointslices[-1]["endpoints"] = len(endpoint_ips)
        if component == "database":
            statefulsets.append({
                "name": "database",
                "ready": f"{ready_replicas}/{replicas}",
                "age": "7d",
            })
            pvcs.append({
                "name": "database-data-database-0",
                "status": "Bound",
                "volume": "pvc-database-0",
                "capacity": "200Gi",
                "access_modes": "RWO",
                "storageclass": "gp3",
                "age": "7d",
                "used_pct": health["pvc_used_pct"],
            })
    if "observabilitypipeline" in state.components:
        daemonsets.append({
            "name": "observability-agent",
            "desired": len(nodes),
            "current": len(nodes),
            "ready": len(nodes),
            "up_to_date": len(nodes),
            "available": len(nodes),
            "node_selector": "kubernetes.io/os=linux",
            "age": "7d",
        })
    else:
        daemonsets.append({
            "name": "node-observer",
            "desired": len(nodes),
            "current": len(nodes),
            "ready": len(nodes),
            "up_to_date": len(nodes),
            "available": len(nodes),
            "node_selector": "kubernetes.io/os=linux",
            "age": "7d",
        })
    jobs.append({
        "name": "scheduler-backfill",
        "completions": "1/1",
        "duration": "2m14s",
        "age": "6d",
    })
    cronjobs.append({
        "name": "scheduler-nightly",
        "schedule": "15 2 * * *",
        "suspend": "False",
        "active": 0,
        "last_schedule": "18h",
        "age": "7d",
    })
    if "apigateway" in state.components:
        ingress.append({
            "name": "apigateway",
            "class": "nginx",
            "hosts": "api.simulated-saas.local",
            "address": "10.0.0.20",
            "ports": "80,443",
            "age": "7d",
        })
    snapshot = {
        "namespaces": [{"name": state.namespace, "status": "Active", "age": "30d"}],
        "pods": pods,
        "configmaps": configmaps,
        "secrets": [
            {
                "name": f"sh.helm.release.v1.{DEFAULT_RELEASE}.v{revision['version']}",
                "type": "helm.sh/release.v1",
                "data": 1,
                "age": "7d",
            }
            for revision in _helm_release_revisions(state)
        ],
        "replicationcontrollers": [],
        "deployments": deployments,
        "replicasets": replicasets,
        "daemonsets": daemonsets,
        "services": services,
        "endpoints": endpoints,
        "endpointslices": endpointslices,
        "hpa": hpas,
        "nodes": nodes,
        "pvc": pvcs,
        "statefulsets": statefulsets,
        "jobs": jobs,
        "cronjobs": cronjobs,
        "serviceaccounts": serviceaccounts,
        "ingress": ingress,
        "events": _event_rows(state),
        "helm_releases": [_helm_release(state)],
    }
    _apply_default_namespaces(state, snapshot)
    _apply_mutation_rows(state, snapshot)
    return snapshot


def _apply_default_namespaces(state: SimulationState, snapshot: dict[str, list[dict[str, Any]]]) -> None:
    for kind, rows in snapshot.items():
        if not _snapshot_kind_namespaced(kind):
            continue
        for row in rows:
            row.setdefault("namespace", state.namespace)


def _apply_mutation_rows(state: SimulationState, snapshot: dict[str, list[dict[str, Any]]]) -> None:
    with state.mutations.lock:
        deleted = {
            kind: set(names)
            for kind, names in state.mutations.deleted_resources.items()
        }
        created = {
            kind: {name: dict(row) for name, row in rows.items()}
            for kind, rows in state.mutations.created_resources.items()
        }
    for kind, rows in snapshot.items():
        if kind in {"events", "helm_releases"}:
            continue
        deleted_names = deleted.get(kind, set())
        if deleted_names:
            snapshot[kind] = [
                row for row in rows
                if _snapshot_row_key(row, state.namespace) not in deleted_names
            ]
        if kind in created:
            existing = {
                _snapshot_row_key(row, state.namespace): index
                for index, row in enumerate(snapshot[kind])
            }
            for key, row in created[kind].items():
                if key in existing:
                    snapshot[kind][existing[key]] = row
                else:
                    snapshot[kind].append(row)


_WATCH_COMMAND_NOTE = (
    "watch: live streaming is not available over the one-shot command API; "
    "fetch /v1/kubeconfig and use real kubectl for --watch\n"
)


def _render_get_watch(
    state: SimulationState, kind: str, parsed: ParsedCommand
) -> CommandResult:
    """Render `kubectl get --watch` as a one-shot table plus a partial note.

    `POST /v1/commands` cannot hold a stream open, so a `--watch` request
    renders the initial table exactly as the plain `get` would, appends one
    stderr note pointing at real kubectl, exits 0, and classifies the trace
    `partial` (rule `kubectl.get.<kind>.watch`) so the ignored flag surfaces
    in the debug backlog instead of being silently swallowed.
    """
    if kind not in _SNAPSHOT_KINDS and kind != "all":
        return _unsupported(parsed, f"kubectl get {kind or '<missing-kind>'} --watch")
    return CommandResult(
        0,
        _render_get(state, kind, parsed),
        _WATCH_COMMAND_NOTE,
        "partial",
        f"kubectl.get.{kind}.watch",
    )


def _render_get(state: SimulationState, kind: str, parsed: ParsedCommand) -> str:
    resources = resource_snapshot(state)
    if kind == "all":
        return _render_get_all(state, parsed)
    rows = _filter_snapshot_rows(kind, resources.get(kind, []), parsed)
    if "-o" in parsed.flags or "--output" in parsed.flags:
        output = parsed.flags.get("-o", parsed.flags.get("--output"))
        if output == "json":
            return json.dumps({"items": rows}, indent=2) + "\n"
        if output == "name":
            return "".join(f"{_resource_prefix(kind)}/{row['name']}\n" for row in rows)
    if kind == "pods":
        if parsed.flags.get("-o") == "wide" or parsed.flags.get("--output") == "wide":
            return _table(["NAME", "READY", "STATUS", "RESTARTS", "AGE", "IP", "NODE"], [
                [r["name"], r["ready"], r["status"], str(r["restarts"]), r["age"], r["pod_ip"], r["node"]]
                for r in rows
            ])
        return _table(["NAME", "READY", "STATUS", "RESTARTS", "AGE"], [
            [r["name"], r["ready"], r["status"], str(r["restarts"]), r["age"]]
            for r in rows
        ])
    if kind == "namespaces":
        return _table(["NAME", "STATUS", "AGE"], [
            [r["name"], r["status"], r["age"]]
            for r in rows
        ])
    if kind == "configmaps":
        return _table(["NAME", "DATA", "AGE"], [
            [r["name"], str(r["data"]), r["age"]]
            for r in rows
        ])
    if kind == "secrets":
        return _table(["NAME", "TYPE", "DATA", "AGE"], [
            [r["name"], r["type"], str(r["data"]), r["age"]]
            for r in rows
        ])
    if kind == "replicationcontrollers":
        return _table(["NAME", "DESIRED", "CURRENT", "READY", "AGE"], [])
    if kind == "deployments":
        return _table(["NAME", "READY", "UP-TO-DATE", "AVAILABLE", "AGE"], [
            [r["name"], r["ready"], str(r["up_to_date"]), str(r["available"]), r["age"]]
            for r in rows
        ])
    if kind == "replicasets":
        return _table(["NAME", "DESIRED", "CURRENT", "READY", "AGE"], [
            [r["name"], str(r["desired"]), str(r["current"]), str(r["ready"]), r["age"]]
            for r in rows
        ])
    if kind == "daemonsets":
        return _table(["NAME", "DESIRED", "CURRENT", "READY", "UP-TO-DATE", "AVAILABLE", "NODE SELECTOR", "AGE"], [
            [
                r["name"], str(r["desired"]), str(r["current"]), str(r["ready"]),
                str(r["up_to_date"]), str(r["available"]), r["node_selector"], r["age"],
            ]
            for r in rows
        ])
    if kind == "services":
        return _table(["NAME", "TYPE", "CLUSTER-IP", "EXTERNAL-IP", "PORT(S)", "AGE"], [
            [r["name"], r["type"], r["cluster_ip"], r["external_ip"], r["ports"], r["age"]]
            for r in rows
        ])
    if kind == "endpoints":
        return _table(["NAME", "ENDPOINTS", "AGE"], [
            [r["name"], r["endpoints"] or "<none>", r["age"]]
            for r in rows
        ])
    if kind == "endpointslices":
        return _table(["NAME", "ADDRESSTYPE", "PORTS", "ENDPOINTS", "AGE"], [
            [r["name"], r["address_type"], r["ports"], str(r["endpoints"]), r["age"]]
            for r in rows
        ])
    if kind == "events":
        return _table(["LAST SEEN", "TYPE", "REASON", "OBJECT", "MESSAGE"], [
            [r["last_seen"], r["type"], r["reason"], r["object"], r["message"]]
            for r in rows
        ])
    if kind == "hpa":
        return _table(["NAME", "REFERENCE", "TARGETS", "MINPODS", "MAXPODS", "REPLICAS", "AGE"], [
            [r["name"], r["reference"], r["targets"], str(r["minpods"]), str(r["maxpods"]), str(r["replicas"]), r["age"]]
            for r in rows
        ])
    if kind == "jobs":
        return _table(["NAME", "COMPLETIONS", "DURATION", "AGE"], [
            [r["name"], r["completions"], r["duration"], r["age"]]
            for r in rows
        ])
    if kind == "cronjobs":
        return _table(["NAME", "SCHEDULE", "SUSPEND", "ACTIVE", "LAST SCHEDULE", "AGE"], [
            [r["name"], r["schedule"], r["suspend"], str(r["active"]), r["last_schedule"], r["age"]]
            for r in rows
        ])
    if kind == "serviceaccounts":
        return _table(["NAME", "SECRETS", "AGE"], [
            [r["name"], str(r["secrets"]), r["age"]]
            for r in rows
        ])
    if kind == "nodes":
        return _table(["NAME", "STATUS", "ROLES", "AGE", "VERSION"], [
            [r["name"], r["status"], r["roles"], r["age"], r["version"]]
            for r in rows
        ])
    if kind == "pvc":
        return _table(["NAME", "STATUS", "VOLUME", "CAPACITY", "ACCESS MODES", "STORAGECLASS", "AGE"], [
            [r["name"], r["status"], r["volume"], r["capacity"], r["access_modes"], r["storageclass"], r["age"]]
            for r in rows
        ])
    if kind == "statefulsets":
        return _table(["NAME", "READY", "AGE"], [
            [r["name"], r["ready"], r["age"]]
            for r in rows
        ])
    if kind == "ingress":
        return _table(["NAME", "CLASS", "HOSTS", "ADDRESS", "PORTS", "AGE"], [
            [r["name"], r["class"], r["hosts"], r["address"], r["ports"], r["age"]]
            for r in rows
        ])
    return ""


def _render_get_all(state: SimulationState, parsed: ParsedCommand) -> str:
    resources = resource_snapshot(state)
    rows = []
    for kind in ("pods", "services", "deployments", "replicasets", "statefulsets", "hpa", "jobs", "cronjobs"):
        for row in _filter_snapshot_rows(kind, resources.get(kind, []), parsed):
            status = (
                row.get("status")
                or row.get("ready")
                or row.get("targets")
                or row.get("completions")
                or row.get("schedule")
                or "Active"
            )
            rows.append([_resource_prefix(kind), row["name"], str(status), row.get("age", "7d")])
    if parsed.flags.get("-o") == "name" or parsed.flags.get("--output") == "name":
        return "".join(f"{kind}/{name}\n" for kind, name, _, _ in rows)
    return _table(["KIND", "NAME", "STATUS", "AGE"], rows)


def _filter_snapshot_rows(
    kind: str,
    rows: list[dict[str, Any]],
    parsed: ParsedCommand,
) -> list[dict[str, Any]]:
    label_selector = str(parsed.flags.get("-l") or parsed.flags.get("--selector") or "")
    field_selector = str(parsed.flags.get("--field-selector") or "")
    return [
        row for row in rows
        if _snapshot_row_matches_namespace(kind, row, parsed.namespace)
        and _matches_label_selector(_snapshot_row_labels(kind, row), label_selector)
        and _snapshot_row_matches_field_selector(kind, row, field_selector)
    ]


def _snapshot_row_matches_namespace(kind: str, row: dict[str, Any], namespace: str) -> bool:
    if namespace == "*" or not _snapshot_kind_namespaced(kind):
        return True
    return _snapshot_row_namespace(row) == namespace


def _snapshot_row_matches_field_selector(kind: str, row: dict[str, Any], selector: str) -> bool:
    if not selector:
        return True
    fields = {
        "metadata.name": row.get("name", ""),
        "status.phase": "Running" if row.get("status") == "Running" else row.get("status", ""),
        "involvedObject.name": str(row.get("object", "")).split("/", 1)[-1],
        "kind": kind,
    }
    for item in _split_selector(selector):
        if "!=" in item:
            key, value = item.split("!=", 1)
            if str(fields.get(key.strip(), "")) == value.strip():
                return False
        elif "==" in item or "=" in item:
            separator = "==" if "==" in item else "="
            key, value = item.split(separator, 1)
            if str(fields.get(key.strip(), "")) != value.strip():
                return False
    return True


def _normalized_resource_prefix(kind: str) -> str:
    normalized = _mutation_snapshot_kind(kind) or _normalize_kind(kind)
    return _resource_prefix(normalized or kind or "resource")


def _render_describe(state: SimulationState, kind: str, parsed: ParsedCommand) -> CommandResult:
    name = parsed.resource_name
    resources = resource_snapshot(state)
    if kind == "pods":
        pod = _find_named(resources["pods"], name)
        if pod is None:
            return _not_found("pods", name)
        lines = [
            f"Name:           {pod['name']}",
            f"Namespace:      {state.namespace}",
            f"Node:           {pod['node']}",
            f"Status:         {pod['status']}",
            f"Controlled By:  ReplicaSet/{pod['component']}",
            "Containers:",
            f"  {pod['component']}:",
            f"    Ready:      {pod['ready'].split('/')[0] == pod['ready'].split('/')[1]}",
            f"    Restarts:   {pod['restarts']}",
            "Events:",
        ]
        lines.extend("  " + event for event in pod["events"])
        return CommandResult(0, "\n".join(lines) + "\n", "", "supported", "kubectl.describe.pods")
    if kind == "deployments":
        deployment = _find_named(resources["deployments"], name)
        if deployment is None:
            return _not_found("deployments", name)
        component = deployment["name"]
        events = _component_events(state, component)
        lines = [
            f"Name:                   {component}",
            f"Namespace:              {state.namespace}",
            f"Replicas:               {deployment['ready']} available",
            f"DeploymentStatus:       {deployment['status']}",
            "Conditions:",
            "  Type           Status  Reason",
            f"  Available      {'True' if deployment['available'] else 'False'}   MinimumReplicasAvailable",
            "Events:",
        ]
        lines.extend("  " + event for event in events)
        return CommandResult(0, "\n".join(lines) + "\n", "", "supported", "kubectl.describe.deployments")
    if kind == "replicasets":
        replicaset = _find_named(resources["replicasets"], name)
        if replicaset is None:
            return _not_found("replicasets", name)
        return CommandResult(
            0,
            (
                f"Name:           {replicaset['name']}\n"
                f"Namespace:      {state.namespace}\n"
                f"Controlled By:  Deployment/{replicaset['owner']}\n"
                f"Replicas:       {replicaset['ready']} ready / {replicaset['desired']} desired\n"
            ),
            "",
            "supported",
            "kubectl.describe.replicasets",
        )
    if kind == "daemonsets":
        daemonset = _find_named(resources["daemonsets"], name)
        if daemonset is None:
            return _not_found("daemonsets", name)
        return CommandResult(
            0,
            (
                f"Name:           {daemonset['name']}\n"
                f"Namespace:      {state.namespace}\n"
                f"Node Selector:  {daemonset['node_selector']}\n"
                f"Desired:        {daemonset['desired']}\n"
                f"Ready:          {daemonset['ready']}\n"
            ),
            "",
            "supported",
            "kubectl.describe.daemonsets",
        )
    if kind == "services":
        service = _find_named(resources["services"], name)
        if service is None:
            return _not_found("services", name)
        endpoint = _find_named(resources["endpoints"], name)
        lines = [
            f"Name:              {service['name']}",
            f"Namespace:         {state.namespace}",
            f"Type:              {service['type']}",
            f"IP:                {service['cluster_ip']}",
            f"Port:              {service['ports']}",
            f"Endpoints:         {endpoint['endpoints'] if endpoint else '<none>'}",
        ]
        return CommandResult(0, "\n".join(lines) + "\n", "", "supported", "kubectl.describe.services")
    if kind == "endpoints":
        endpoint = _find_named(resources["endpoints"], name)
        if endpoint is None:
            return _not_found("endpoints", name)
        return CommandResult(
            0,
            (
                f"Name:       {endpoint['name']}\n"
                f"Namespace:  {state.namespace}\n"
                f"Endpoints:  {endpoint['endpoints'] or '<none>'}\n"
            ),
            "",
            "supported",
            "kubectl.describe.endpoints",
        )
    if kind == "endpointslices":
        endpointslice = _find_named(resources["endpointslices"], name)
        if endpointslice is None:
            return _not_found("endpointslices", name)
        return CommandResult(
            0,
            (
                f"Name:          {endpointslice['name']}\n"
                f"Namespace:     {state.namespace}\n"
                f"Service:       {endpointslice['service']}\n"
                f"Address Type:  {endpointslice['address_type']}\n"
                f"Endpoints:     {endpointslice['endpoints']}\n"
            ),
            "",
            "supported",
            "kubectl.describe.endpointslices",
        )
    if kind == "hpa":
        hpa = _find_named(resources["hpa"], name)
        if hpa is None:
            return _not_found("horizontalpodautoscalers", name)
        return CommandResult(
            0,
            (
                f"Name:         {hpa['name']}\n"
                f"Namespace:    {state.namespace}\n"
                f"Reference:    {hpa['reference']}\n"
                f"Targets:      {hpa['targets']}\n"
                f"Replicas:     {hpa['replicas']}\n"
            ),
            "",
            "supported",
            "kubectl.describe.hpa",
        )
    if kind == "nodes":
        node = _find_named(resources["nodes"], name)
        if node is None:
            return _not_found("nodes", name)
        lines = [
            f"Name:               {node['name']}",
            f"Roles:              {node['roles']}",
            f"Status:             {node['status']}",
            "Conditions:",
            f"  Ready             {node['status'] == 'Ready'}",
            "Allocated resources:",
            f"  cpu               {node['cpu_pct']}%",
            f"  memory            {node['memory_pct']}%",
        ]
        return CommandResult(0, "\n".join(lines) + "\n", "", "supported", "kubectl.describe.nodes")
    if kind == "pvc":
        pvc = _find_named(resources["pvc"], name)
        if pvc is None:
            return _not_found("persistentvolumeclaims", name)
        lines = [
            f"Name:          {pvc['name']}",
            f"Namespace:     {state.namespace}",
            f"Status:        {pvc['status']}",
            f"Capacity:      {pvc['capacity']}",
            f"Used:          {pvc['used_pct']}%",
            "Events:",
            "  Warning VolumePressure database write volume approaching capacity"
            if pvc["used_pct"] >= 90 else "  Normal Bound volume attached",
        ]
        return CommandResult(0, "\n".join(lines) + "\n", "", "supported", "kubectl.describe.pvc")
    if kind == "statefulsets":
        statefulset = _find_named(resources["statefulsets"], name)
        if statefulset is None:
            return _not_found("statefulsets", name)
        return CommandResult(
            0,
            f"Name:       {statefulset['name']}\nNamespace:  {state.namespace}\nPods Status: {statefulset['ready']}\n",
            "",
            "supported",
            "kubectl.describe.statefulsets",
        )
    if kind == "configmaps":
        configmap = _find_named(resources["configmaps"], name)
        if configmap is None:
            return _not_found("configmaps", name)
        lines = [
            f"Name:      {configmap['name']}",
            f"Namespace: {state.namespace}",
            "Data",
        ]
        lines.extend(f"  {key}: {value}" for key, value in configmap["keys"].items())
        return CommandResult(0, "\n".join(lines) + "\n", "", "supported", "kubectl.describe.configmaps")
    if kind == "secrets":
        secret = _find_named(resources["secrets"], name)
        if secret is None:
            return _not_found("secrets", name)
        return CommandResult(
            0,
            (
                f"Name:      {secret['name']}\n"
                f"Namespace: {state.namespace}\n"
                f"Type:      {secret['type']}\n"
                f"Data:      {secret['data']}\n"
            ),
            "",
            "supported",
            "kubectl.describe.secrets",
        )
    if kind == "jobs":
        job = _find_named(resources["jobs"], name)
        if job is None:
            return _not_found("jobs", name)
        return CommandResult(
            0,
            (
                f"Name:        {job['name']}\n"
                f"Namespace:   {state.namespace}\n"
                f"Completions: {job['completions']}\n"
                f"Duration:    {job['duration']}\n"
            ),
            "",
            "supported",
            "kubectl.describe.jobs",
        )
    if kind == "cronjobs":
        cronjob = _find_named(resources["cronjobs"], name)
        if cronjob is None:
            return _not_found("cronjobs", name)
        return CommandResult(
            0,
            (
                f"Name:           {cronjob['name']}\n"
                f"Namespace:      {state.namespace}\n"
                f"Schedule:       {cronjob['schedule']}\n"
                f"Suspend:        {cronjob['suspend']}\n"
                f"Active Jobs:    {cronjob['active']}\n"
                f"Last Schedule:  {cronjob['last_schedule']}\n"
            ),
            "",
            "supported",
            "kubectl.describe.cronjobs",
        )
    if kind == "serviceaccounts":
        serviceaccount = _find_named(resources["serviceaccounts"], name)
        if serviceaccount is None:
            return _not_found("serviceaccounts", name)
        return CommandResult(
            0,
            (
                f"Name:      {serviceaccount['name']}\n"
                f"Namespace: {state.namespace}\n"
                f"Secrets:   {serviceaccount['secrets']}\n"
            ),
            "",
            "supported",
            "kubectl.describe.serviceaccounts",
        )
    if kind == "ingress":
        ingress = _find_named(resources["ingress"], name)
        if ingress is None:
            return _not_found("ingress", name)
        return CommandResult(
            0,
            (
                f"Name:      {ingress['name']}\n"
                f"Namespace: {state.namespace}\n"
                f"Class:     {ingress['class']}\n"
                f"Hosts:     {ingress['hosts']}\n"
                f"Address:   {ingress['address']}\n"
            ),
            "",
            "supported",
            "kubectl.describe.ingress",
        )
    if kind == "namespaces":
        namespace = _find_named(resources["namespaces"], name)
        if namespace is None:
            return _not_found("namespaces", name)
        return CommandResult(
            0,
            f"Name:   {namespace['name']}\nStatus: {namespace['status']}\n",
            "",
            "supported",
            "kubectl.describe.namespaces",
        )
    return _unsupported(parsed, f"kubectl describe {kind}")


def _logs_uses_selector(parsed: ParsedCommand) -> bool:
    return bool(parsed.flags.get("-l") or parsed.flags.get("--selector"))


def _render_logs_command(state: SimulationState, parsed: ParsedCommand) -> CommandResult:
    container = _logs_container_name(parsed)
    if _logs_has_container_flag(parsed) and not container:
        return CommandResult(
            1,
            "",
            "error: -c/--container requires a container name\n",
            "partial",
            "kubectl.logs.container",
        )
    pods = _logs_target_pods(state, parsed)
    if container:
        for pod in pods:
            if container != pod["component"]:
                return CommandResult(
                    1,
                    "",
                    f'error: container "{container}" is not valid for pod "{pod["name"]}"\n',
                    "partial",
                    "kubectl.logs.container",
                )
    since_time = _logs_since_time(parsed)
    if isinstance(since_time, str):
        return CommandResult(
            1,
            "",
            f'error: invalid --since-time value "{since_time}"\n',
            "partial",
            "kubectl.logs.since-time",
        )
    tail_limit = _logs_tail_limit(parsed)
    if isinstance(tail_limit, str):
        return CommandResult(
            1,
            "",
            f'error: invalid --tail value "{tail_limit}"\n',
            "partial",
            "kubectl.logs.tail",
        )
    rule_id = (
        "kubectl.logs.selector"
        if _logs_uses_selector(parsed) and not parsed.resource_name
        else "kubectl.logs.pod"
    )
    return CommandResult(
        0,
        _render_logs(state, parsed, pods=pods, since_time=since_time, tail_limit=tail_limit),
        "",
        "supported",
        rule_id,
    )


def _logs_target_pods(state: SimulationState, parsed: ParsedCommand) -> list[dict[str, Any]]:
    if parsed.resource_name:
        component = _component_from_name(parsed.resource_name, state.components) or parsed.resource_name
        return [{"name": parsed.resource_name, "component": component}]
    resources = resource_snapshot(state)
    return _filter_snapshot_rows("pods", resources["pods"], parsed)


def _logs_container_name(parsed: ParsedCommand) -> str:
    return str(parsed.flags.get("-c") or parsed.flags.get("--container") or "")


def _logs_has_container_flag(parsed: ParsedCommand) -> bool:
    return "-c" in parsed.flags or "--container" in parsed.flags


def _logs_since_time(parsed: ParsedCommand) -> _dt.datetime | str | None:
    raw = parsed.flags.get("--since-time")
    if raw is None:
        return None
    with contextlib.suppress(ValueError):
        return _parse_user_timestamp(str(raw))
    return str(raw)


def _logs_tail_limit(parsed: ParsedCommand) -> int | None | str:
    raw = parsed.flags.get("--tail")
    if raw is None:
        return 20
    with contextlib.suppress(ValueError):
        value = int(str(raw))
        return None if value < 0 else value
    return str(raw)


def _render_logs(
    state: SimulationState,
    parsed: ParsedCommand,
    *,
    pods: list[dict[str, Any]] | None = None,
    since_time: _dt.datetime | None = None,
    tail_limit: int | None = 20,
) -> str:
    target_pods = pods if pods is not None else _logs_target_pods(state, parsed)
    log_time = state.clock.now()
    if since_time is not None and since_time > log_time:
        return ""
    now = _format_dt(log_time)
    rendered: list[str] = []
    for pod in target_pods:
        rendered.extend(_render_pod_logs(state, parsed, pod, timestamp=now, tail_limit=tail_limit))
    return "".join(rendered)


def _render_pod_logs(
    state: SimulationState,
    parsed: ParsedCommand,
    pod: dict[str, Any],
    *,
    timestamp: str,
    tail_limit: int | None,
) -> list[str]:
    component = pod["component"]
    lines: list[str] = []
    for profile in state.profiles():
        if component in profile.affected_components:
            lines.extend(profile.logs)
    if not lines:
        lines = [
            f"{component} health probe ok",
            f"{component} processed request batch without anomaly",
        ]
    prefix = ""
    if parsed.flags.get("--prefix"):
        container = _logs_container_name(parsed) or component
        prefix = f"{pod['name']}/{container} "
    if parsed.flags.get("--previous") or parsed.flags.get("-p"):
        prefix += "previous "
    if tail_limit is not None:
        lines = lines[-tail_limit:] if tail_limit else []
    return [f"{timestamp} {prefix}{line}\n" for line in lines]


def _render_top(state: SimulationState, kind: str) -> str:
    resources = resource_snapshot(state)
    if kind == "pods":
        return _table(["NAME", "CPU(cores)", "MEMORY(bytes)"], [
            [pod["name"], f"{pod['cpu_m']}m", f"{pod['memory_mi']}Mi"]
            for pod in resources["pods"]
        ])
    return _table(["NAME", "CPU(cores)", "CPU%", "MEMORY(bytes)", "MEMORY%"], [
        [node["name"], f"{node['cpu_m']}m", f"{node['cpu_pct']}%", f"{node['memory_mi']}Mi", f"{node['memory_pct']}%"]
        for node in resources["nodes"]
    ])


def _render_kubectl_version() -> str:
    return (
        f"Client Version: {_K8S_ADVERTISED_TAG}\n"
        "Kustomize Version: v5.0.4\n"
        f"Server Version: {_K8S_ADVERTISED_GIT_VERSION}\n"
    )


def _render_kubectl_api_versions() -> str:
    versions = [
        "v1",
        "apps/v1",
        "autoscaling/v2",
        "batch/v1",
        "discovery.k8s.io/v1",
        "networking.k8s.io/v1",
        "metrics.k8s.io/v1beta1",
        "authorization.k8s.io/v1",
    ]
    return "\n".join(versions) + "\n"


def _render_kubectl_api_resources() -> str:
    rows = [
        ["pods", "po", "true", "Pod"],
        ["services", "svc", "true", "Service"],
        ["configmaps", "cm", "true", "ConfigMap"],
        ["secrets", "", "true", "Secret"],
        ["endpoints", "ep", "true", "Endpoints"],
        ["serviceaccounts", "sa", "true", "ServiceAccount"],
        ["nodes", "no", "false", "Node"],
        ["deployments", "deploy", "true", "Deployment"],
        ["replicasets", "rs", "true", "ReplicaSet"],
        ["daemonsets", "ds", "true", "DaemonSet"],
        ["statefulsets", "sts", "true", "StatefulSet"],
        ["horizontalpodautoscalers", "hpa", "true", "HorizontalPodAutoscaler"],
        ["jobs", "", "true", "Job"],
        ["cronjobs", "cj", "true", "CronJob"],
        ["ingresses", "ing", "true", "Ingress"],
        ["endpointslices", "", "true", "EndpointSlice"],
    ]
    return _table(["NAME", "SHORTNAMES", "NAMESPACED", "KIND"], rows)


def _render_kubectl_cluster_info() -> str:
    return (
        "Kubernetes control plane is running at http://127.0.0.1:8088\n"
        "AMC simulator debug console is running at http://127.0.0.1:8088/debug\n"
    )


def _render_explain(state: SimulationState, parsed: ParsedCommand) -> CommandResult:
    target = parsed.positionals[1] if len(parsed.positionals) > 1 else ""
    if not target:
        return CommandResult(
            1,
            "",
            "error: resource required for kubectl explain\n",
            "partial",
            "kubectl.explain.missing-resource",
        )
    schema_info = _explain_schema_for_kind(state, parsed.resource_kind)
    if schema_info is None:
        return CommandResult(
            1,
            "",
            f"error: resource {target!r} is not exposed by the simulator OpenAPI schema\n",
            "unsupported",
            "kubectl.explain.unsupported",
        )
    requested_api_version = str(parsed.flags.get("--api-version") or "")
    if "--api-version" in parsed.flags and (
        not requested_api_version or requested_api_version.startswith("-")
    ):
        return CommandResult(
            1,
            "",
            "error: --api-version requires a non-empty value\n",
            "partial",
            "kubectl.explain.api-version.invalid",
        )
    if requested_api_version and requested_api_version != schema_info["api_version"]:
        return CommandResult(
            1,
            "",
            (
                f"error: resource {target!r} is available as "
                f"{schema_info['api_version']}, not {requested_api_version}\n"
            ),
            "partial",
            "kubectl.explain.api-version",
        )
    field_schema = _explain_schema_at_path(schema_info["schema"], parsed.resource_name)
    if field_schema is None:
        return CommandResult(
            1,
            "",
            (
                f"error: field {parsed.resource_name!r} is not exposed for "
                f"{schema_info['kind']}\n"
            ),
            "partial",
            "kubectl.explain.unknown-field",
        )
    return CommandResult(
        0,
        _format_explain(schema_info, parsed.resource_name, field_schema, bool(parsed.flags.get("--recursive"))),
        "",
        "supported",
        f"kubectl.explain.{parsed.resource_kind}",
    )


def _explain_schema_for_kind(
    state: SimulationState,
    kind: str,
    snapshot: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any] | None:
    target = _EXPLAIN_RESOURCE_TARGETS.get(kind)
    if target is None:
        return None
    group, version, resource = target
    meta = _k8s_resource_meta(group, version, resource)
    objects = _k8s_objects_for_resource(state, group, resource, snapshot=snapshot) or []
    sample = objects[0] if objects else _minimal_k8s_object(state, meta["api_version"], meta["kind"])
    schema = _openapi_schema_from_value(
        sample,
        root_kind=meta["kind"],
        path=(),
        description=_EXPLAIN_RESOURCE_DESCRIPTIONS.get(kind, f"{meta['kind']} is projected by the AMC simulator."),
    )
    schema["x-kubernetes-group-version-kind"] = [{
        "group": group,
        "version": version,
        "kind": meta["kind"],
    }]
    return {
        "api_version": meta["api_version"],
        "kind": meta["kind"],
        "resource": resource,
        "schema": schema,
    }


def _minimal_k8s_object(state: SimulationState, api_version: str, kind: str) -> dict[str, Any]:
    return {
        "apiVersion": api_version,
        "kind": kind,
        "metadata": _k8s_metadata(state, f"simulated-{kind.lower()}"),
    }


def _openapi_schema_from_value(
    value: Any,
    *,
    root_kind: str,
    path: tuple[str, ...],
    description: str = "",
) -> dict[str, Any]:
    field_description = description or _explain_field_description(root_kind, path)
    if isinstance(value, bool):
        return {"type": "boolean", "description": field_description}
    if isinstance(value, int):
        return {"type": "integer", "format": "int32", "description": field_description}
    if isinstance(value, float):
        return {"type": "number", "format": "double", "description": field_description}
    if isinstance(value, dict):
        return {
            "type": "object",
            "title": _explain_title(root_kind, path),
            "description": field_description,
            "properties": {
                str(key): _openapi_schema_from_value(item, root_kind=root_kind, path=(*path, str(key)))
                for key, item in value.items()
            },
        }
    if isinstance(value, list):
        item_value = value[0] if value else {}
        return {
            "type": "array",
            "description": field_description,
            "items": _openapi_schema_from_value(item_value, root_kind=root_kind, path=(*path, "items")),
        }
    return {"type": "string", "description": field_description}


def _explain_field_description(root_kind: str, path: tuple[str, ...]) -> str:
    if not path:
        return f"{root_kind} schema projected from the AMC simulator Kubernetes facade."
    dotted = ".".join(part for part in path if part != "items")
    return f"{dotted} field projected from AMC's simulator-backed {root_kind} object."


def _explain_title(root_kind: str, path: tuple[str, ...]) -> str:
    if not path:
        return root_kind
    if path[-1] == "metadata":
        return "ObjectMeta"
    words = [root_kind, *(part for part in path if part != "items")]
    return "".join(word[:1].upper() + word[1:] for word in words if word)


def _explain_schema_at_path(schema: dict[str, Any], field_path: str) -> dict[str, Any] | None:
    node = schema
    for part in [item for item in field_path.split(".") if item]:
        node = _explain_display_schema(node)
        properties = node.get("properties")
        if not isinstance(properties, dict) or part not in properties:
            return None
        child = properties[part]
        if not isinstance(child, dict):
            return None
        node = child
    return node


def _format_explain(
    schema_info: dict[str, Any],
    field_path: str,
    field_schema: dict[str, Any],
    recursive: bool,
) -> str:
    lines = [
        f"KIND:       {schema_info['kind']}",
        f"VERSION:    {schema_info['api_version']}",
        "",
    ]
    if field_path:
        lines.extend([
            f"FIELD:      {field_path} {_explain_type_label(field_schema)}",
            "",
        ])
    lines.extend([
        "DESCRIPTION:",
        "    " + str(field_schema.get("description", "")).strip(),
        "",
    ])
    properties = _explain_properties(field_schema)
    if properties:
        lines.append("FIELDS:")
        if recursive:
            lines.extend(_format_recursive_explain_fields(properties, depth=1, max_depth=5))
        else:
            for name, child in properties.items():
                lines.append(f"  {name:<20} {_explain_type_label(child)}")
        lines.append("")
    return "\n".join(lines)


def _format_recursive_explain_fields(
    properties: dict[str, Any],
    *,
    depth: int,
    max_depth: int,
) -> list[str]:
    lines: list[str] = []
    indent = "  " * depth
    for name, child in properties.items():
        if not isinstance(child, dict):
            continue
        lines.append(f"{indent}{name:<20} {_explain_type_label(child)}")
        if depth >= max_depth:
            continue
        child_properties = _explain_properties(child)
        if child_properties:
            lines.extend(
                _format_recursive_explain_fields(
                    child_properties,
                    depth=depth + 1,
                    max_depth=max_depth,
                )
            )
    return lines


def _explain_properties(schema: dict[str, Any]) -> dict[str, Any]:
    display_schema = _explain_display_schema(schema)
    properties = display_schema.get("properties")
    return properties if isinstance(properties, dict) else {}


def _explain_display_schema(schema: dict[str, Any]) -> dict[str, Any]:
    if schema.get("type") == "array":
        items = schema.get("items")
        if isinstance(items, dict):
            return items
    return schema


def _explain_type_label(schema: dict[str, Any]) -> str:
    schema_type = schema.get("type")
    if schema_type == "array":
        items = schema.get("items")
        item_label = _explain_type_name(items) if isinstance(items, dict) else "Object"
        return f"<[]{item_label}>"
    return f"<{_explain_type_name(schema)}>"


def _explain_type_name(schema: dict[str, Any]) -> str:
    schema_type = schema.get("type")
    if schema_type == "object":
        title = schema.get("title")
        return str(title) if title else "Object"
    if schema_type == "integer":
        return "integer"
    if schema_type == "number":
        return "number"
    if schema_type == "boolean":
        return "boolean"
    return "string"


def _render_rollout_status(state: SimulationState, parsed: ParsedCommand) -> str:
    component = _rollout_component(parsed)
    if "deploy_bad_canary_rollback" in state.active_scenarios and component == "apigateway":
        return (
            "deployment \"apigateway\" successfully rolled out\n"
            "note: release was rolled back from failed canary revision\n"
        )
    health = _component_health(state, component)
    rollout_notes = _component_rollout_notes(state, component)
    if health["deployment_status"] == "RolledBack":
        output = f"deployment \"{component}\" successfully rolled out\n"
        output += "note: deployment was rolled back by simulator command\n"
        if rollout_notes:
            output += "\n".join(f"note: {note}" for note in rollout_notes) + "\n"
        return output
    if health["deployment_status"] != "Healthy":
        output = f"waiting for deployment \"{component}\" rollout to finish: {health['deployment_status']}\n"
        if rollout_notes:
            output += "\n".join(f"note: {note}" for note in rollout_notes) + "\n"
        return output
    output = f"deployment \"{component}\" successfully rolled out\n"
    if rollout_notes:
        output += "\n".join(f"note: {note}" for note in rollout_notes) + "\n"
    return output


def _render_rollout_history(state: SimulationState, parsed: ParsedCommand) -> str:
    component = _rollout_component(parsed)
    rows = [["1", "simulated-saas-0.2.0", "baseline deployment"]]
    if "deploy_bad_canary_rollback" in state.active_scenarios and component == "apigateway":
        rows.extend([
            ["2", "simulated-saas-0.3.0-canary", "canary readiness failed"],
            ["3", "simulated-saas-0.3.0", "rollback to stable revision"],
        ])
    else:
        description = "current deployment"
        rollout_notes = _component_rollout_notes(state, component)
        if rollout_notes:
            description = "; ".join(rollout_notes)
        rows.append(["2", "simulated-saas-0.3.0", description])
    return f"deployment.apps/{component}\n" + _table(["REVISION", "CHANGE-CAUSE", "DESCRIPTION"], rows)


def _render_rollout_restart(state: SimulationState, parsed: ParsedCommand) -> str:
    component = _rollout_component(parsed)
    now = state.clock.now()
    state.mutations.set_workload(
        component,
        now=now,
        deployment_status="Restarting",
        pod_status="Running",
        restarts_delta=1,
    )
    state.mutations.record_event(
        "Normal",
        "RolloutRestart",
        f"deployment/{component}",
        f"deployment {component} restarted by simulator command",
        now,
    )
    return f"deployment.apps/{component} restarted\n"


def _render_rollout_pause(state: SimulationState, parsed: ParsedCommand) -> str:
    component = _rollout_component(parsed)
    now = state.clock.now()
    replicas = _replica_count(state, component)
    state.mutations.set_workload(
        component,
        now=now,
        ready_replicas=replicas,
        deployment_status="Paused",
        pod_status="Running",
    )
    state.mutations.record_event(
        "Normal",
        "RolloutPaused",
        f"deployment/{component}",
        f"deployment {component} rollout paused by simulator command",
        now,
    )
    return f"deployment.apps/{component} paused\n"


def _render_rollout_resume(state: SimulationState, parsed: ParsedCommand) -> str:
    component = _rollout_component(parsed)
    now = state.clock.now()
    replicas = _replica_count(state, component)
    state.mutations.set_workload(
        component,
        now=now,
        ready_replicas=replicas,
        deployment_status="Healthy",
        pod_status="Running",
    )
    state.mutations.record_event(
        "Normal",
        "RolloutResumed",
        f"deployment/{component}",
        f"deployment {component} rollout resumed by simulator command",
        now,
    )
    return f"deployment.apps/{component} resumed\n"


def _render_rollout_undo(state: SimulationState, parsed: ParsedCommand) -> str:
    component = _rollout_component(parsed)
    now = state.clock.now()
    replicas = _replica_count(state, component)
    revision = _rollout_undo_revision(parsed)
    state.mutations.set_workload(
        component,
        now=now,
        ready_replicas=replicas,
        deployment_status="RolledBack",
        pod_status="Running",
    )
    state.mutations.record_event(
        "Normal",
        "RolloutUndo",
        f"deployment/{component}",
        f"deployment {component} rolled back to {_rollout_revision_label(revision)} by simulator command",
        now,
    )
    suffix = f" to revision {revision}" if revision != "previous" else ""
    return f"deployment.apps/{component} rolled back{suffix}\n"


def _rollout_undo_revision(parsed: ParsedCommand) -> str:
    revision = _first_flag_value(parsed.flags, "--to-revision", default="previous").strip()
    if not revision or revision.startswith("-"):
        return "previous"
    return revision


def _rollout_revision_label(revision: str) -> str:
    if revision == "previous":
        return "previous revision"
    return f"revision {revision}"


def _rollout_component(parsed: ParsedCommand) -> str:
    component = parsed.resource_name or "apigateway"
    if component.startswith("deployment/"):
        component = component.split("/", 1)[1]
    return component


def _is_deployment_rollout_target(parsed: ParsedCommand) -> bool:
    return parsed.resource_kind == "deployments" and bool(parsed.resource_name)


def _render_scale(state: SimulationState, parsed: ParsedCommand) -> str | CommandResult:
    if parsed.resource_kind not in {"deployments", "deployment", "deploy", ""}:
        return f"{_normalized_resource_prefix(parsed.resource_kind)}/{parsed.resource_name} scaled\n"
    name = parsed.resource_name
    if not name:
        # A nameless scale used to default to apigateway and mutate its
        # workload — real kubectl rejects an empty resource name, and silently
        # scaling an arbitrary default pollutes the overlay (audit A-013).
        return CommandResult(
            1,
            "",
            "error: resource(s) were provided, but no name was specified\n",
            "supported",
            "kubectl.scale.usage",
        )
    # Resolve the target against the overlay-aware snapshot before any write —
    # the same order the API deployment-scale path enforces (audit A-013).
    if _find_named(resource_snapshot(state)["deployments"], name) is None:
        return _not_found("deployments", name)
    component = name
    replicas = _parsed_replicas(parsed)
    now = state.clock.now()
    state.mutations.set_workload(
        component,
        now=now,
        replicas=replicas,
        ready_replicas=replicas,
        deployment_status="Healthy" if replicas else "ScaledToZero",
        pod_status="Running",
    )
    state.mutations.record_event(
        "Normal",
        "ScalingReplicaSet",
        f"deployment/{component}",
        f"scaled deployment {component} to {replicas} replicas",
        now,
    )
    return f"deployment.apps/{component} scaled\n"


def _render_delete(state: SimulationState, parsed: ParsedCommand) -> str | CommandResult:
    kind = parsed.resource_kind
    name = parsed.resource_name
    now = state.clock.now()
    if kind in {"pods", "pod"} and name:
        # Resolve against the overlay-aware snapshot before deleting — a ghost
        # name used to record a phantom deletion in the overlay while the API
        # path 404'd (audit A-013). Same order the API pods-delete branch uses.
        if _find_named(resource_snapshot(state)["pods"], name) is None:
            return _not_found("pods", name)
        state.mutations.delete_pod(name, now=now)
        return f"pod \"{name}\" deleted\n"
    if kind in {"deployments", "deployment", "deploy"} and name:
        if _find_named(resource_snapshot(state)["deployments"], name) is None:
            return _not_found("deployments", name)
        state.mutations.set_workload(
            name,
            now=now,
            replicas=0,
            ready_replicas=0,
            deployment_status="Deleted",
            pod_status="Terminating",
            deleted=True,
        )
        state.mutations.record_event(
            "Normal",
            "Deleted",
            f"deployment/{name}",
            f"deployment {name} deleted from simulator state",
            now,
        )
        return f"deployment.apps \"{name}\" deleted\n"
    snapshot_kind = _mutation_snapshot_kind(kind)
    if snapshot_kind and name:
        # Generic modeled kind: mirror the API generic-delete existence guard
        # (``resource_snapshot(...).get(kind, [])``) so a ghost generic resource
        # 404s on both entry paths instead of recording a phantom delete.
        if _find_named(resource_snapshot(state).get(snapshot_kind, []), name) is None:
            return _not_found(snapshot_kind, name)
        state.mutations.delete_resource(snapshot_kind, name, now=now, namespace=parsed.namespace)
    prefix = _resource_prefix(snapshot_kind or _KIND_ALIASES.get(kind, kind) or "resource")
    return f"{prefix} \"{name}\" deleted\n"


def _render_patch(state: SimulationState, parsed: ParsedCommand) -> CommandResult:
    kind = parsed.resource_kind
    name = parsed.resource_name
    snapshot_kind = _mutation_snapshot_kind(kind)
    if not snapshot_kind or not name:
        return CommandResult(
            1,
            "",
            f"error: unsupported patch target {kind or '<missing-kind>'}/{name or '<missing-name>'}\n",
            "unsupported",
            "kubectl.patch.unsupported",
        )
    parsed_payload = _patch_payload(state, parsed)
    if isinstance(parsed_payload, CommandResult):
        return parsed_payload
    payload = parsed_payload
    now = state.clock.now()
    replicas = _payload_replicas(payload)
    if snapshot_kind == "deployments" and name:
        # Patching a deployment that is not in the overlay-aware snapshot must
        # 404 before any write, matching the API deployment-patch path — the
        # command path used to set_workload on a ghost name (audit A-013). The
        # generic (non-deployment) branch below keeps its upsert semantics,
        # which the API generic PATCH/PUT path also uses, so the two stay in
        # parity.
        if _find_named(resource_snapshot(state)["deployments"], name) is None:
            return _not_found("deployments", name)
    if snapshot_kind == "deployments" and replicas is not None:
        state.mutations.set_workload(
            name,
            now=now,
            replicas=replicas,
            ready_replicas=replicas,
            deployment_status="Healthy" if replicas else "ScaledToZero",
            pod_status="Running",
        )
        state.mutations.record_event(
            "Normal",
            "Patched",
            f"deployment/{name}",
            f"patched deployment {name} replicas to {replicas}",
            now,
        )
    else:
        state.mutations.put_resource(
            snapshot_kind,
            name,
            _generic_resource_row(state, snapshot_kind, name, payload=payload, parsed=parsed),
            now=now,
            namespace=parsed.namespace,
        )
    return CommandResult(
        0,
        f"{_resource_prefix(snapshot_kind)}/{name} patched\n",
        "",
        "supported",
        f"kubectl.patch.{snapshot_kind}",
    )


def _patch_payload(state: SimulationState, parsed: ParsedCommand) -> dict[str, Any] | CommandResult:
    payload_text = _patch_payload_text(parsed)
    if not payload_text:
        return CommandResult(
            1,
            "",
            "error: kubectl patch requires --patch/-p JSON payload\n",
            "partial",
            "kubectl.patch.payload",
        )
    try:
        raw_payload = json.loads(payload_text)
    except json.JSONDecodeError as exc:
        return CommandResult(
            1,
            "",
            f"error: invalid patch JSON: {exc.msg}\n",
            "partial",
            "kubectl.patch.payload.invalid",
        )
    patch_type = str(parsed.flags.get("--type") or "strategic").strip().lower()
    if patch_type in {"merge", "strategic", "strategic-merge"}:
        if not isinstance(raw_payload, dict):
            return CommandResult(
                1,
                "",
                "error: merge and strategic patches must be JSON objects\n",
                "partial",
                "kubectl.patch.payload.shape",
            )
        base = _patch_base_payload(state, parsed)
        return _deep_merge_patch(base, raw_payload)
    if patch_type == "json":
        if not isinstance(raw_payload, list):
            return CommandResult(
                1,
                "",
                "error: JSON patch payload must be a list of operations\n",
                "partial",
                "kubectl.patch.payload.shape",
            )
        base = _patch_base_payload(state, parsed)
        error = _apply_json_patch(base, raw_payload)
        if error:
            return CommandResult(1, "", f"error: {error}\n", "partial", "kubectl.patch.json")
        return base
    return CommandResult(
        1,
        "",
        f"error: patch type {patch_type!r} is not modeled; use merge, strategic, or json\n",
        "partial",
        "kubectl.patch.type",
    )


def _patch_payload_text(parsed: ParsedCommand) -> str:
    payload = _first_flag_value(parsed.flags, "--patch")
    if payload:
        return payload
    p_value = parsed.flags.get("-p")
    if isinstance(p_value, str) and p_value:
        return p_value
    for token in reversed(parsed.positionals[2:]):
        stripped = token.strip()
        if stripped.startswith(("{", "[")):
            return stripped
    return ""


def _patch_base_payload(state: SimulationState, parsed: ParsedCommand) -> dict[str, Any]:
    snapshot_kind = _mutation_snapshot_kind(parsed.resource_kind)
    row = None
    if snapshot_kind:
        rows = _filter_snapshot_rows(snapshot_kind, resource_snapshot(state).get(snapshot_kind, []), parsed)
        row = _find_named(rows, parsed.resource_name)
    payload: dict[str, Any] = {
        "metadata": {
            "name": parsed.resource_name,
            "namespace": parsed.namespace,
        },
    }
    if row is None:
        return payload
    labels = _string_dict(row.get("labels"))
    annotations = _string_dict(row.get("annotations"))
    if labels:
        payload["metadata"]["labels"] = labels
    if annotations:
        payload["metadata"]["annotations"] = annotations
    if snapshot_kind == "configmaps":
        payload["data"] = {str(key): str(value) for key, value in row.get("keys", {}).items()}
    elif snapshot_kind == "services":
        payload["spec"] = {
            "type": row.get("type", "ClusterIP"),
            "clusterIP": row.get("cluster_ip"),
            "selector": row.get("selector") if isinstance(row.get("selector"), dict) else {},
            "ports": [{"port": row.get("port", 8080)}],
        }
    elif snapshot_kind in {"deployments", "statefulsets"}:
        ready = str(row.get("ready", "1/1"))
        _, _, desired = ready.partition("/")
        payload["spec"] = {"replicas": desired or "1"}
    return payload


def _deep_merge_patch(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    for key, value in patch.items():
        if value is None:
            base.pop(str(key), None)
        elif isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge_patch(base[key], value)
        else:
            base[str(key)] = value
    return base


def _apply_json_patch(target: dict[str, Any], operations: list[Any]) -> str:
    for operation in operations:
        if not isinstance(operation, dict):
            return "JSON patch operations must be objects"
        op = str(operation.get("op", ""))
        path = str(operation.get("path", ""))
        if op not in {"add", "replace", "remove"}:
            return f"JSON patch operation {op!r} is not modeled"
        if not path.startswith("/"):
            return f"JSON patch path {path!r} must start with /"
        if op == "remove":
            error = _remove_json_pointer(target, path)
        else:
            error = _set_json_pointer(target, path, operation.get("value"))
        if error:
            return error
    return ""


def _json_pointer_parts(path: str) -> list[str]:
    return [
        part.replace("~1", "/").replace("~0", "~")
        for part in path.lstrip("/").split("/")
        if part
    ]


def _set_json_pointer(target: dict[str, Any], path: str, value: Any) -> str:
    parts = _json_pointer_parts(path)
    if not parts:
        return "JSON patch root replacement is not modeled"
    cursor: dict[str, Any] = target
    for part in parts[:-1]:
        child = cursor.setdefault(part, {})
        if not isinstance(child, dict):
            return f"JSON patch path {path!r} crosses a non-object value"
        cursor = child
    cursor[parts[-1]] = value
    return ""


def _remove_json_pointer(target: dict[str, Any], path: str) -> str:
    parts = _json_pointer_parts(path)
    if not parts:
        return "JSON patch root removal is not modeled"
    cursor: dict[str, Any] = target
    for part in parts[:-1]:
        child = cursor.get(part)
        if not isinstance(child, dict):
            return f"JSON patch path {path!r} does not exist"
        cursor = child
    if parts[-1] not in cursor:
        return f"JSON patch path {path!r} does not exist"
    del cursor[parts[-1]]
    return ""


def _render_diff(state: SimulationState, parsed: ParsedCommand) -> CommandResult:
    filename = _first_flag_value(parsed.flags, "-f", "--filename")
    if not filename:
        return CommandResult(
            1,
            "",
            "error: kubectl diff requires -f/--filename for simulator-backed manifests\n",
            "partial",
            "kubectl.diff.filename",
        )
    kind, name = _resource_from_manifest_name(filename)
    snapshot_kind = _mutation_snapshot_kind(kind)
    prefix = _resource_prefix(snapshot_kind or kind)
    existing = _find_named(resource_snapshot(state).get(snapshot_kind, []), name) if snapshot_kind else None
    status = "existing" if existing else "new"
    stdout = (
        f"diff -u -N current/{prefix}/{name} desired/{prefix}/{name}\n"
        f"--- current/{prefix}/{name}\n"
        f"+++ desired/{prefix}/{name}\n"
        "@@\n"
        f"- simulator-state: {status}\n"
        f"+ simulator-manifest: {filename}\n"
    )
    return CommandResult(1, stdout, "", "supported", "kubectl.diff")


def _render_apply(state: SimulationState, parsed: ParsedCommand) -> CommandResult:
    if parsed.verb == "create":
        return _render_create(state, parsed)
    filenames = _flag_values(parsed.flags, "-f", "--filename") or ["manifest"]
    now = state.clock.now()
    dry_run = _is_dry_run(parsed)
    action = "configured"
    targets: list[tuple[str, str, str, dict[str, Any], str]] = []
    for filename in filenames:
        manifest_targets = _manifest_apply_targets(state, parsed, str(filename))
        if isinstance(manifest_targets, CommandResult):
            return manifest_targets
        targets.extend(manifest_targets)
    if not targets:
        return CommandResult(
            1,
            "",
            "error: kubectl apply did not find supported simulator resources\n",
            "partial",
            "kubectl.apply.manifest.empty",
        )
    if not dry_run:
        for snapshot_kind, name, namespace, payload, _source in targets:
            state.mutations.put_resource(
                snapshot_kind,
                name,
                _generic_resource_row(state, snapshot_kind, name, payload=payload, parsed=parsed),
                now=now,
                namespace=namespace,
            )
    suffix = " (dry run)" if dry_run else ""
    stdout = "".join(
        f"{_resource_prefix(snapshot_kind)}/{name} {action}{suffix}\n"
        for snapshot_kind, name, _namespace, _payload, _source in targets
    )
    return CommandResult(0, stdout, "", "supported", "kubectl.apply.manifest")


def _render_create(state: SimulationState, parsed: ParsedCommand) -> CommandResult:
    now = state.clock.now()
    kind = parsed.resource_kind
    name = parsed.resource_name
    snapshot_kind = _mutation_snapshot_kind(kind)
    dry_run = _is_dry_run(parsed)
    if snapshot_kind and name and not dry_run:
        state.mutations.put_resource(
            snapshot_kind,
            name,
            _generic_resource_row(state, snapshot_kind, name, payload={}, parsed=parsed),
            now=now,
            namespace=parsed.namespace,
        )
    elif not dry_run:
        state.mutations.record_event(
            "Normal",
            "Applied",
            "manifest/simulated",
            f"{parsed.verb} accepted manifest; simulator state reconciled",
            now,
        )
    target = f"{_resource_prefix(snapshot_kind)}/{name}" if snapshot_kind and name else "manifest"
    suffix = " (dry run)" if dry_run else ""
    return CommandResult(0, f"{target} created{suffix}\n", "", "supported", "kubectl.create")


def _manifest_apply_targets(
    state: SimulationState,
    parsed: ParsedCommand,
    filename: str,
) -> list[tuple[str, str, str, dict[str, Any], str]] | CommandResult:
    path = Path(filename)
    if filename == "-":
        return CommandResult(
            1,
            "",
            "error: kubectl apply from stdin is not modeled; use -f PATH\n",
            "partial",
            "kubectl.apply.manifest.stdin",
        )
    if not path.exists():
        kind, name = _resource_from_manifest_name(filename)
        snapshot_kind = _mutation_snapshot_kind(kind)
        if snapshot_kind and name:
            namespace = state.namespace if parsed.namespace == "*" else parsed.namespace
            return [(snapshot_kind, name, namespace, {}, filename)]
        return []
    if not path.is_file():
        return CommandResult(
            1,
            "",
            f"error: kubectl apply -f {filename}: path is not a regular file\n",
            "partial",
            "kubectl.apply.manifest.read",
        )
    documents = _load_manifest_documents(path)
    if isinstance(documents, CommandResult):
        return documents
    targets: list[tuple[str, str, str, dict[str, Any], str]] = []
    for index, payload in enumerate(documents, start=1):
        target = _manifest_apply_target(state, parsed, payload, filename, index)
        if isinstance(target, CommandResult):
            return target
        targets.append(target)
    return targets


def _load_manifest_documents(path: Path) -> list[dict[str, Any]] | CommandResult:
    suffix = path.suffix.lower()
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return CommandResult(
            1,
            "",
            f"error: unable to read manifest {path}: {exc}\n",
            "partial",
            "kubectl.apply.manifest.read",
        )
    try:
        if suffix == ".json":
            raw = json.loads(text)
            documents = raw if isinstance(raw, list) else [raw]
        elif suffix in {".yaml", ".yml"}:
            try:
                import yaml  # type: ignore[import-not-found]
            except ModuleNotFoundError:
                return CommandResult(
                    1,
                    "",
                    f"error: PyYAML is required to parse manifest {path}\n",
                    "partial",
                    "kubectl.apply.manifest.yaml",
                )
            try:
                documents = list(yaml.safe_load_all(text))
            except yaml.YAMLError as exc:
                return CommandResult(
                    1,
                    "",
                    f"error: invalid manifest {path}: {exc}\n",
                    "partial",
                    "kubectl.apply.manifest.parse",
                )
        else:
            return CommandResult(
                1,
                "",
                f"error: unsupported manifest extension for {path}; use .json, .yaml, or .yml\n",
                "partial",
                "kubectl.apply.manifest.extension",
            )
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        return CommandResult(
            1,
            "",
            f"error: invalid manifest {path}: {exc}\n",
            "partial",
            "kubectl.apply.manifest.parse",
        )
    return _normalize_manifest_documents(documents, str(path))


def _normalize_manifest_documents(documents: list[Any], source: str) -> list[dict[str, Any]] | CommandResult:
    normalized: list[dict[str, Any]] = []
    for index, document in enumerate(documents, start=1):
        if document is None:
            continue
        if not isinstance(document, dict):
            return CommandResult(
                1,
                "",
                f"error: manifest {source} document {index} must be a Kubernetes object\n",
                "partial",
                "kubectl.apply.manifest.shape",
            )
        if str(document.get("kind", "")).lower() == "list":
            items = document.get("items")
            if not isinstance(items, list):
                return CommandResult(
                    1,
                    "",
                    f"error: manifest {source} document {index} List.items must be a list\n",
                    "partial",
                    "kubectl.apply.manifest.shape",
                )
            for item_index, item in enumerate(items, start=1):
                if not isinstance(item, dict):
                    return CommandResult(
                        1,
                        "",
                        f"error: manifest {source} document {index} item {item_index} must be a Kubernetes object\n",
                        "partial",
                        "kubectl.apply.manifest.shape",
                    )
                normalized.append(item)
            continue
        normalized.append(document)
    return normalized


def _manifest_apply_target(
    state: SimulationState,
    parsed: ParsedCommand,
    payload: dict[str, Any],
    source: str,
    index: int,
) -> tuple[str, str, str, dict[str, Any], str] | CommandResult:
    raw_kind = str(payload.get("kind") or "").strip()
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    name = str(metadata.get("name") or "").strip()
    if not raw_kind or not name:
        return CommandResult(
            1,
            "",
            f"error: manifest {source} document {index} requires kind and metadata.name\n",
            "partial",
            "kubectl.apply.manifest.identity",
        )
    snapshot_kind = _mutation_snapshot_kind(raw_kind)
    if not snapshot_kind:
        return CommandResult(
            1,
            "",
            f"error: manifest {source} document {index} kind {raw_kind!r} is not modeled by the simulator\n",
            "partial",
            "kubectl.apply.manifest.unsupported",
        )
    namespace = str(metadata.get("namespace") or parsed.namespace or state.namespace)
    if namespace == "*":
        namespace = state.namespace
    return snapshot_kind, name, namespace, payload, source


def _resource_from_manifest_name(filename: str) -> tuple[str, str]:
    stem = Path(filename).name
    for suffix in (".yaml", ".yml", ".json"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
    tokens = [token for token in stem.replace("_", "-").split("-") if token]
    aliases = {
        "configmap": "configmaps",
        "cm": "configmaps",
        "secret": "secrets",
        "service": "services",
        "svc": "services",
        "deployment": "deployments",
        "deploy": "deployments",
        "job": "jobs",
        "cronjob": "cronjobs",
        "ingress": "ingress",
        "hpa": "hpa",
        "serviceaccount": "serviceaccounts",
    }
    if tokens and tokens[0] in aliases and len(tokens) > 1:
        return aliases[tokens[0]], "-".join(tokens[1:])
    if tokens and tokens[-1] in aliases and len(tokens) > 1:
        return aliases[tokens[-1]], "-".join(tokens[:-1])
    return "configmaps", stem or "simulated-manifest"


def _mutation_snapshot_kind(kind: str) -> str:
    normalized = _normalize_kind(kind)
    aliases = {
        "horizontalpodautoscalers": "hpa",
        "persistentvolumeclaims": "pvc",
        "ingresses": "ingress",
        "manifest": "configmaps",
    }
    normalized = aliases.get(normalized, normalized)
    return normalized if normalized in _MUTATION_SNAPSHOT_KINDS else ""


def _record_continuous_generation_failure(
    state: SimulationState,
    exc: BaseException,
) -> None:
    # A raising background regen used to leave only ``str(exc)`` on the
    # eval-hidden /v1/state (a bare "2" for SystemExit). Summarize the exit code
    # explicitly and route type/message/traceback to the operator error sink so
    # the failure is visible in the default posture too. Called from inside the
    # regen ``except`` block, so ``_capture_traceback_tail`` sees the traceback.
    if isinstance(exc, SystemExit):
        detail = f"SystemExit(code={exc.code!r})"
    else:
        detail = str(exc) or exc.__class__.__name__
    with state.generation.lock:
        state.generation.last_error = detail
        state.generation.thread = "failed"
    _record_server_error(
        getattr(state, "request_logger", None),
        where="continuous-generate",
        exc=exc,
    )
    # Split-brain guard (audit A-015): a failed pass may have already
    # atomically published a new anomalies.csv before failing on a later
    # artifact. Disk is truth for published artifacts (every writer uses an
    # atomic replace, so the file is never partial), so reload it into state —
    # otherwise /v1/anomalies and the MCP tools keep serving a stale in-memory
    # copy that disagrees with what is on disk. File-must-exist + best-effort:
    # a pre-write failure that left no file keeps the prior rows rather than
    # wiping state to empty. Reuses the generation.lock swap via
    # replace_generated_rows.
    anomalies_path = state.output_dir / "anomalies.csv"
    if anomalies_path.exists():
        try:
            state.replace_generated_rows(load_anomaly_rows(anomalies_path))
        except Exception:  # pragma: no cover - defensive disk-read boundary
            pass


def _generic_resource_row(
    state: SimulationState,
    kind: str,
    name: str,
    *,
    payload: dict[str, Any],
    parsed: ParsedCommand | None = None,
) -> dict[str, Any]:
    spec = payload.get("spec") if isinstance(payload.get("spec"), dict) else {}
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    string_data = payload.get("stringData") if isinstance(payload.get("stringData"), dict) else {}
    base = _generic_resource_metadata(state, kind, name, payload=payload, parsed=parsed)

    def row(values: dict[str, Any]) -> dict[str, Any]:
        return {**base, **values}

    if kind == "configmaps":
        keys = {str(key): str(value) for key, value in data.items()} or _configmap_keys_from_flags(parsed)
        if not keys:
            keys = {"simulated": "true"}
        return row({"name": name, "data": len(keys), "age": "0s", "keys": keys})
    if kind == "secrets":
        secret_data = {str(key): str(value) for key, value in {**data, **string_data}.items()}
        return row({"name": name, "type": payload.get("type", "Opaque"), "data": len(secret_data) or 1, "age": "0s"})
    if kind == "services":
        service_type = str(spec.get("type") or "ClusterIP")
        ports = spec.get("ports") if isinstance(spec.get("ports"), list) else []
        port = ports[0].get("port", 8080) if ports and isinstance(ports[0], dict) else 8080
        selector = spec.get("selector") if isinstance(spec.get("selector"), dict) else {}
        return row({
            "name": name,
            "type": service_type,
            "cluster_ip": str(spec.get("clusterIP") or _stable_cluster_ip(name)),
            "external_ip": "<none>",
            "ports": f"{port}/TCP",
            "port": port,
            "selector": {str(key): str(value) for key, value in selector.items()} or {"app.kubernetes.io/name": name},
            "age": "0s",
        })
    if kind == "deployments":
        replicas = _payload_replicas(payload)
        if replicas is None:
            replicas = 1
        return row({
            "name": name,
            "ready": f"{replicas}/{replicas}",
            "up_to_date": replicas,
            "available": replicas,
            "age": "0s",
            "status": "Healthy" if replicas else "ScaledToZero",
            "generation": int(base.get("generation", 1) or 1),
            "observed_generation": int(base.get("generation", 1) or 1),
        })
    if kind == "serviceaccounts":
        return row({"name": name, "secrets": len(payload.get("secrets", [])), "age": "0s"})
    if kind == "hpa":
        min_replicas = int(spec.get("minReplicas", 1) or 1)
        max_replicas = int(spec.get("maxReplicas", 8) or 8)
        target = spec.get("scaleTargetRef") if isinstance(spec.get("scaleTargetRef"), dict) else {}
        target_name = str(target.get("name") or name)
        return row({
            "name": name,
            "reference": f"{target.get('kind', 'Deployment')}/{target_name}",
            "targets": "0%/80%",
            "minpods": min_replicas,
            "maxpods": max_replicas,
            "replicas": min_replicas,
            "age": "0s",
        })
    if kind == "jobs":
        completions = int(spec.get("completions", 1) or 1)
        return row({"name": name, "completions": f"0/{completions}", "duration": "0s", "age": "0s"})
    if kind == "cronjobs":
        schedule = str(spec.get("schedule") or (parsed.flags.get("--schedule") if parsed else "") or "* * * * *")
        return row({"name": name, "schedule": schedule, "suspend": "False", "active": 0, "last_schedule": "<none>", "age": "0s"})
    if kind == "pvc":
        requests = spec.get("resources", {}).get("requests", {}) if isinstance(spec.get("resources"), dict) else {}
        access_modes = spec.get("accessModes", ["RWO"])
        if not isinstance(access_modes, list):
            access_modes = ["RWO"]
        return row({
            "name": name,
            "status": "Bound",
            "volume": f"pvc-{name}",
            "capacity": str(requests.get("storage", "1Gi")),
            "access_modes": ",".join(str(mode) for mode in access_modes),
            "storageclass": str(spec.get("storageClassName", "gp3")),
            "age": "0s",
            "used_pct": 1,
        })
    if kind == "statefulsets":
        replicas = _payload_replicas(payload)
        if replicas is None:
            replicas = 1
        return row({"name": name, "ready": f"{replicas}/{replicas}", "age": "0s"})
    if kind == "daemonsets":
        nodes = _node_rows(state)
        return row({
            "name": name,
            "desired": len(nodes),
            "current": len(nodes),
            "ready": len(nodes),
            "up_to_date": len(nodes),
            "available": len(nodes),
            "node_selector": "kubernetes.io/os=linux",
            "age": "0s",
        })
    if kind == "ingress":
        rules = spec.get("rules") if isinstance(spec.get("rules"), list) else []
        host = rules[0].get("host") if rules and isinstance(rules[0], dict) else f"{name}.simulated-saas.local"
        return row({"name": name, "class": spec.get("ingressClassName", "nginx"), "hosts": host, "address": "10.0.0.20", "ports": "80,443", "age": "0s"})
    return row({"name": name, "age": "0s"})


def _generic_resource_metadata(
    state: SimulationState,
    kind: str,
    name: str,
    *,
    payload: dict[str, Any],
    parsed: ParsedCommand | None = None,
) -> dict[str, Any]:
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    spec = payload.get("spec") if isinstance(payload.get("spec"), dict) else {}
    namespace = str(metadata.get("namespace") or (parsed.namespace if parsed else "") or state.namespace)
    if namespace == "*":
        namespace = state.namespace
    labels = _string_dict(metadata.get("labels"))
    annotations = _string_dict(metadata.get("annotations"))
    selector = spec.get("selector") if isinstance(spec.get("selector"), dict) else {}
    match_labels = selector.get("matchLabels") if isinstance(selector.get("matchLabels"), dict) else {}
    template = spec.get("template") if isinstance(spec.get("template"), dict) else {}
    template_metadata = template.get("metadata") if isinstance(template.get("metadata"), dict) else {}
    template_labels = _string_dict(template_metadata.get("labels"))
    if kind in {"deployments", "statefulsets", "daemonsets"}:
        labels = {**_k8s_workload_labels(name), **labels}
        if not match_labels:
            match_labels = {"app.kubernetes.io/name": name}
        if not template_labels:
            template_labels = {**labels, **{str(key): str(value) for key, value in match_labels.items()}}
    generation = metadata.get("generation", 1)
    try:
        generation = max(1, int(str(generation)))
    except (TypeError, ValueError):
        generation = 1
    result: dict[str, Any] = {
        "namespace": namespace,
        "labels": labels,
        "annotations": annotations,
        "generation": generation,
        "observed_generation": generation,
        "resource_version": "1",
    }
    if match_labels:
        result["selector"] = {str(key): str(value) for key, value in match_labels.items()}
    if template_labels:
        result["template_labels"] = template_labels
    owner_references = metadata.get("ownerReferences")
    if isinstance(owner_references, list):
        result["owner_references"] = [
            dict(item) for item in owner_references
            if isinstance(item, dict)
        ]
    deletion_timestamp = metadata.get("deletionTimestamp")
    if deletion_timestamp:
        result["deletion_timestamp"] = str(deletion_timestamp)
    return result


def _configmap_keys_from_flags(parsed: ParsedCommand | None) -> dict[str, str]:
    if parsed is None:
        return {}
    keys: dict[str, str] = {}
    for literal in _flag_values(parsed.flags, "--from-literal"):
        key, _, value = literal.partition("=")
        keys[key or "literal"] = value or "true"
    for from_file in _flag_values(parsed.flags, "--from-file"):
        key, separator, path = from_file.partition("=")
        if not separator:
            path = key
            key = Path(path).name or "file"
        keys[key or "file"] = f"file:{path or 'true'}"
    return keys


def _parsed_replicas(parsed: ParsedCommand) -> int:
    value = parsed.flags.get("--replicas")
    if value is None:
        for token in parsed.positionals:
            if token.startswith("--replicas="):
                value = token.split("=", 1)[1]
                break
    try:
        return max(0, int(str(value)))
    except (TypeError, ValueError):
        return 1


def _render_wait(state: SimulationState, parsed: ParsedCommand) -> str:
    component = parsed.resource_name or "apigateway"
    health = _component_health(state, component)
    condition = str(parsed.flags.get("--for") or "condition=available")
    prefix = _normalized_resource_prefix(parsed.resource_kind)
    if health["deployment_status"] in {"Healthy", "RolledBack"}:
        return f"{prefix}/{component} condition met: {condition}\n"
    return f"{prefix}/{component} condition pending: {health['deployment_status']}\n"


def _render_exec(state: SimulationState, parsed: ParsedCommand) -> str:
    pod_name = parsed.resource_name
    component = _component_from_name(pod_name, state.components)
    if len(parsed.positionals) > 2 and "--" in parsed.positionals:
        command = " ".join(parsed.positionals[parsed.positionals.index("--") + 1:])
    else:
        command = " ".join(parsed.positionals[2:]) or "healthcheck"
    if any(token in command for token in {"env", "printenv"}):
        return (
            f"SERVICE_NAME={component}\n"
            f"NAMESPACE={state.namespace}\n"
            f"SCENARIOS={','.join(_exposed_active_scenarios(state))}\n"
        )
    if "curl" in command:
        return f"HTTP/1.1 200 OK\nx-amc-component: {component}\n\nok\n"
    return f"{pod_name}: simulated exec completed for `{command}`\n"


def _render_port_forward(parsed: ParsedCommand) -> str:
    port = parsed.positionals[2] if len(parsed.positionals) > 2 else "8080:8080"
    return (
        f"Forwarding from 127.0.0.1:{port.split(':', 1)[0]} -> {port.split(':')[-1]}\n"
        "Forwarding from [::1]:"
        f"{port.split(':', 1)[0]} -> {port.split(':')[-1]}\n"
        "simulator note: stream held open only in real kubectl; command API returns immediately\n"
    )

from .server_helm_impl import (  # noqa: F401  (re-import at original position)
    _render_helm_list,
    _render_helm_status,
    _render_helm_history,
    _render_helm_env,
    _render_helm_get,
    _render_helm_test,
    _render_helm_install,
    _render_helm_upgrade,
    _helm_value_overrides,
    _render_helm_rollback,
)


def _not_found(kind: str, name: str) -> CommandResult:
    return CommandResult(
        1,
        "",
        f"Error from server (NotFound): {kind} \"{name}\" not found\n",
        "supported",
        "kubectl.not_found",
    )


_DEPLOYMENT_STATUS_PRIORITY = {
    "Healthy": 0,
    "TrafficBurst": 1,
    "ScenarioInfluenced": 1,
    "RecoveredAfterRollback": 1,
    "RetryPressure": 2,
    "CacheMissPressure": 2,
    "DatabaseBackpressure": 2,
    "AuthDependencyDegraded": 2,
    "DNSDependencyFailure": 2,
    "NetworkDegraded": 2,
    "FallbackServing": 2,
    "InferenceFallback": 2,
    "EndpointChurn": 2,
    "Backpressure": 2,
    "TelemetryBacklog": 2,
    "TenantImport": 2,
    "ContextCachePressure": 2,
    "HotKeyChurn": 2,
    "JWKSCacheChurn": 2,
    "DependencyDegraded": 3,
    "RateLimited": 3,
    "CPUSaturated": 3,
    "CacheDegraded": 3,
    "ReadPressure": 3,
    "DatabaseStall": 3,
    "QueueBacklog": 3,
    "HealthCheckFlap": 3,
    "ObjectStore5xx": 3,
    "Upstream5xx": 3,
    "IndexRebuild": 3,
    "RetrievalDegraded": 3,
    "QueueOverflow": 3,
    "Provider5xx": 3,
    "CheckoutDegraded": 3,
    "JWKSCacheMiss": 3,
    "TokenValidationSlow": 3,
    "IngestLag": 3,
    "LLMSurge": 3,
    "LargeContext": 3,
    "LookupPressure": 3,
    "ProviderRateLimited": 3,
    "BatchPressure": 3,
    "BandwidthPressure": 3,
    "BatchWritePressure": 3,
    "BatchEvictions": 3,
    "ViralTraffic": 3,
    "GatewayPressure": 3,
    "MetadataWritePressure": 3,
    "GPUFragmented": 3,
    "RegionalFailover": 3,
    "FailoverSaturated": 3,
    "ReplicationLag": 3,
    "FailoverPressure": 3,
    "ReplayBacklog": 3,
    "ProviderUnavailable": 3,
    "ProviderOutage": 3,
    "FallbackPressure": 3,
    "StoragePressure": 3,
    "StorageWait": 3,
    "UploadDegraded": 3,
    "RolledBack": 3,
    "NetworkPartition": 3,
    "CertRotation": 3,
    "JWKSRotation": 3,
    "TokenValidationFailing": 3,
    "WriteBacklog": 3,
    "PartialOutage": 4,
    "AZIsolated": 4,
    "Degraded": 4,
}
_POD_STATUS_PRIORITY = {
    "Running": 0,
    "Pending": 1,
    "CrashLoopBackOff": 3,
    "Error": 4,
}


def _component_health(state: SimulationState, component: str) -> dict[str, Any]:
    replicas = _replica_count(state, component)
    health = {
        "pod_status": "Running",
        "deployment_status": "Healthy",
        "ready": "1/1",
        "ready_replicas": replicas,
        "restarts": 0,
        "cpu_pct": 36,
        "cpu_m": 180,
        "memory_mi": 384,
        "memory_pct": 42,
        "pvc_used_pct": 61,
    }
    impacts = _component_impacts(state, component)
    for impact in impacts:
        _apply_component_impact(health, impact, replicas)
    scenarios = _component_scenarios(state, component)
    if scenarios and not impacts:
        health.update({"deployment_status": "ScenarioInfluenced", "cpu_pct": 55, "cpu_m": 550})
    with state.mutations.lock:
        mutation = state.mutations.workloads.get(component)
        if mutation is not None:
            if mutation.deployment_status:
                health["deployment_status"] = mutation.deployment_status
            if mutation.pod_status:
                health["pod_status"] = mutation.pod_status
            if mutation.ready_replicas is not None:
                health["ready_replicas"] = mutation.ready_replicas
            if mutation.restarts_delta:
                health["restarts"] += mutation.restarts_delta
            if mutation.deleted:
                health.update({
                    "deployment_status": "Deleted",
                    "pod_status": "Terminating",
                    "ready_replicas": 0,
                    "ready": "0/1",
                })
    health["ready_replicas"] = max(0, min(replicas, health["ready_replicas"]))
    return health


def _component_impacts(state: SimulationState, component: str) -> list[OpsComponentImpact]:
    return [
        impact
        for profile in state.profiles()
        for impact in profile.impacts
        if impact.component == component
    ]


def _apply_component_impact(
    health: dict[str, Any],
    impact: OpsComponentImpact,
    replicas: int,
) -> None:
    if _status_priority(
        impact.deployment_status,
        _DEPLOYMENT_STATUS_PRIORITY,
    ) >= _status_priority(
        health["deployment_status"],
        _DEPLOYMENT_STATUS_PRIORITY,
    ):
        health["deployment_status"] = impact.deployment_status
    if _status_priority(impact.pod_status, _POD_STATUS_PRIORITY) >= _status_priority(
        health["pod_status"],
        _POD_STATUS_PRIORITY,
    ):
        health["pod_status"] = impact.pod_status
    if impact.ready:
        health["ready"] = impact.ready
    elif impact.pod_status != "Running":
        health["ready"] = "0/1"
    if impact.ready_replicas is not None:
        health["ready_replicas"] = impact.ready_replicas
    elif impact.ready_replicas_delta:
        health["ready_replicas"] += impact.ready_replicas_delta
    health["ready_replicas"] = max(0, min(replicas, health["ready_replicas"]))
    health["restarts"] += impact.restarts
    if impact.cpu_pct is not None:
        health["cpu_pct"] = max(health["cpu_pct"], impact.cpu_pct)
        health["cpu_m"] = max(health["cpu_m"], impact.cpu_m or impact.cpu_pct * 10)
    elif impact.cpu_m is not None:
        health["cpu_m"] = max(health["cpu_m"], impact.cpu_m)
    if impact.memory_mi is not None:
        health["memory_mi"] = max(health["memory_mi"], impact.memory_mi)
    if impact.memory_pct is not None:
        health["memory_pct"] = max(health["memory_pct"], impact.memory_pct)
    if impact.pvc_used_pct is not None:
        health["pvc_used_pct"] = max(health["pvc_used_pct"], impact.pvc_used_pct)


def _status_priority(status: str, priority: dict[str, int]) -> int:
    return priority.get(status, 2)


def _component_scenarios(state: SimulationState, component: str) -> list[str]:
    matches = []
    for scenario_id in state.active_scenarios:
        profile = OPS_SCENARIO_PROFILES.get(scenario_id)
        if profile is not None:
            affected = set(profile.affected_components)
        else:
            affected = set(state.legacy.SCENARIOS[scenario_id].components_touched)
        if component in affected:
            matches.append(scenario_id)
    return matches


def _exposed_component_scenarios(state: SimulationState, component: str) -> list[str]:
    """Per-component scenario slugs for pod snapshot rows; empty in eval
    mode. See :func:`_exposed_active_scenarios` for the rationale. The
    behavioral :func:`_component_scenarios` (which drives the
    ``ScenarioInfluenced`` health signal) is intentionally *not* gated, so
    symptoms stay visible while the labels do not.
    """
    return [] if state.eval_mode else _component_scenarios(state, component)


def _component_events(state: SimulationState, component: str) -> list[str]:
    events: list[str] = []
    for profile in state.profiles():
        if component in profile.affected_components:
            events.extend(profile.events)
    if not events:
        events.append(f"Normal Healthy {component} probes passing")
    with state.mutations.lock:
        for event in state.mutations.extra_events:
            obj = event.get("object", "")
            if obj.endswith(f"/{component}") or obj.startswith(f"pod/{component}-"):
                events.append(
                    f"{event.get('type', 'Normal')} {event.get('reason', 'Mutation')} "
                    f"{event.get('message', '')}".strip()
                )
    return events


def _component_rollout_notes(state: SimulationState, component: str) -> list[str]:
    notes = []
    for profile in state.profiles():
        if component in profile.affected_components:
            notes.append(profile.rollout_note or profile.summary)
    return notes


def _event_rows(state: SimulationState) -> list[dict[str, str]]:
    rows = []
    now = _format_dt(state.clock.now())
    for profile in state.profiles():
        target = profile.affected_components[0] if profile.affected_components else "cluster"
        for event in profile.events:
            parts = event.split(" ", 2)
            event_type = parts[0] if parts else "Normal"
            reason = parts[1] if len(parts) > 1 else "Scenario"
            message = parts[2] if len(parts) > 2 else event
            rows.append({
                "last_seen": now,
                "type": event_type,
                "reason": reason,
                "object": f"pod/{_pod_name(target, 0)}",
                "message": message,
            })
    if not rows:
        rows.append({
            "last_seen": now,
            "type": "Normal",
            "reason": "Healthy",
            "object": "deployment/simulated-saas",
            "message": "all simulated workloads are healthy",
        })
    with state.mutations.lock:
        rows.extend(dict(event) for event in state.mutations.extra_events)
    return rows


def _node_rows(state: SimulationState) -> list[dict[str, Any]]:
    partition = "network_partition_az_split" in state.active_scenarios
    return [
        {
            "name": "ip-10-0-1-21",
            "status": "Ready",
            "roles": "worker",
            "age": "30d",
            "version": _K8S_ADVERTISED_TAG,
            "cpu_m": 2100,
            "cpu_pct": 52,
            "memory_mi": 9240,
            "memory_pct": 58,
        },
        {
            "name": "ip-10-0-2-17",
            "status": "Ready",
            "roles": "worker",
            "age": "30d",
            "version": _K8S_ADVERTISED_TAG,
            "cpu_m": 1840,
            "cpu_pct": 46,
            "memory_mi": 8120,
            "memory_pct": 51,
        },
        {
            "name": "ip-10-0-3-42",
            "status": "NotReady" if partition else "Ready",
            "roles": "worker",
            "age": "30d",
            "version": _K8S_ADVERTISED_TAG,
            "cpu_m": 2600 if partition else 1760,
            "cpu_pct": 78 if partition else 44,
            "memory_mi": 10400 if partition else 7900,
            "memory_pct": 73 if partition else 49,
        },
    ]

from .server_helm_impl import (  # noqa: F401  (re-import at original position)
    _helm_release,
    _helm_notes,
    _helm_current_description,
)


def _replica_count(state: SimulationState, component: str) -> int:
    with state.mutations.lock:
        mutation = state.mutations.workloads.get(component)
        if mutation is not None and mutation.replicas is not None:
            return mutation.replicas
    if getattr(state.args, "instances_per_component", 1) > 1:
        return int(state.args.instances_per_component)
    if component in {"apigateway", "authservice", "cacheservice"}:
        return 3
    return 1


def _pod_name(component: str, index: int) -> str:
    if component == "database":
        return f"database-{index}"
    return f"{component}-{index}"


def _component_from_name(name: str, components: tuple[str, ...]) -> str:
    for component in components:
        if name == component or name.startswith(component + "-"):
            return component
    return name.split("-", 1)[0] if name else ""


def _stable_cluster_ip(component: str) -> str:
    value = sum(ord(ch) for ch in component)
    return f"10.96.{value % 200}.{(value // 3) % 240 + 10}"


def _find_named(rows: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    for row in rows:
        if row.get("name") == name:
            return row
    return None


def _preview(value: str, limit: int = 240) -> str:
    value = value.strip()
    if len(value) <= limit:
        return value
    return value[: limit - 3] + "..."


class RequestBodyTooLarge(ValueError):
    """Raised when an HTTP request declares a body larger than server policy."""


def _read_json_body(
    handler: BaseHTTPRequestHandler,
    max_bytes: int = DEFAULT_MAX_BODY_BYTES,
) -> dict[str, Any]:
    length = _content_length(handler)
    if length > max_bytes:
        raise RequestBodyTooLarge(
            f"request body is {length} bytes; limit is {max_bytes} bytes"
        )
    raw = handler.rfile.read(length) if length else b"{}"
    try:
        payload = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON body: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("JSON body must be an object")
    return payload


def _read_optional_json_body(
    handler: BaseHTTPRequestHandler,
    max_bytes: int = DEFAULT_MAX_BODY_BYTES,
) -> dict[str, Any]:
    length = _content_length(handler)
    if length > max_bytes:
        raise RequestBodyTooLarge(
            f"request body is {length} bytes; limit is {max_bytes} bytes"
        )
    raw = handler.rfile.read(length) if length else b"{}"
    with contextlib.suppress(UnicodeDecodeError, json.JSONDecodeError):
        payload = json.loads(raw.decode("utf-8"))
        if isinstance(payload, dict):
            return payload
    return {}


def _content_length(handler: BaseHTTPRequestHandler) -> int:
    value = handler.headers.get("content-length")
    if not value:
        return 0
    try:
        length = int(value)
    except ValueError as exc:
        raise ValueError("invalid content-length header") from exc
    if length < 0:
        raise ValueError("invalid negative content-length header")
    return length


def kubernetes_api_response(
    state: SimulationState,
    method: str,
    path: str,
    query: dict[str, list[str]],
    accept_header: str = "",
) -> KubernetesApiResponse | None:
    if path != "/version" and not path.startswith(("/api", "/apis", "/openapi")):
        return None
    if method != "GET":
        return _k8s_read_only_response(method, path)
    if path.startswith("/openapi"):
        return _k8s_openapi_response(state, path)
    if path == "/version":
        major, minor, _ = _K8S_ADVERTISED_VERSION.split(".")
        return _k8s_json_response({
            "major": major,
            "minor": minor,
            "gitVersion": _K8S_ADVERTISED_GIT_VERSION,
            "gitCommit": "simulated",
            "gitTreeState": "clean",
            "buildDate": _k8s_timestamp(state.clock.now()),
            "goVersion": "go1.22.0",
            "compiler": "gc",
            "platform": "linux/amd64",
        }, "k8s.version")
    if path == "/api":
        return _k8s_json_response({
            "kind": "APIVersions",
            "apiVersion": "v1",
            "versions": ["v1"],
            "serverAddressByClientCIDRs": [],
        }, "k8s.discovery.core")
    if path == "/apis":
        return _k8s_json_response(_k8s_api_group_list(), "k8s.discovery.groups")

    parts = [part for part in path.strip("/").split("/") if part]
    if len(parts) == 2 and parts == ["api", "v1"]:
        return _k8s_json_response(_k8s_api_resource_list("", "v1"), "k8s.discovery.v1")
    if parts[:2] == ["api", "v1"]:
        return _k8s_core_resource_response(state, parts, query, _accepts_table(accept_header))
    if parts and parts[0] == "apis":
        return _k8s_group_resource_response(state, parts, query, _accepts_table(accept_header))
    return _k8s_status_response(
        404,
        f"{path} is not implemented by the simulator Kubernetes API",
        "NotFound",
        "unsupported",
        "k8s.path.unsupported",
    )


def _k8s_openapi_response(state: SimulationState, path: str) -> KubernetesApiResponse:
    normalized = path.rstrip("/") or "/"
    if normalized == "/openapi/v2":
        return _k8s_json_response(_k8s_openapi_v2_document(state), "k8s.openapi.v2")
    if normalized == "/openapi/v3":
        return _k8s_json_response(_k8s_openapi_v3_discovery(), "k8s.openapi.v3.discovery")
    prefix = "/openapi/v3/"
    if normalized.startswith(prefix):
        group_version = normalized[len(prefix):]
        group, version = _openapi_group_version_from_path(group_version)
        if (group, version) in _openapi_group_versions():
            return _k8s_json_response(
                _k8s_openapi_v3_document(state, group, version),
                f"k8s.openapi.v3.{group or 'core'}.{version}",
            )
    return _k8s_status_response(
        404,
        f"{path} is not implemented by the simulator OpenAPI facade",
        "NotFound",
        "unsupported",
        "k8s.openapi.unsupported",
    )


def _k8s_openapi_v2_document(state: SimulationState) -> dict[str, Any]:
    return {
        "swagger": "2.0",
        "info": {
            "title": "AMC simulator Kubernetes schema",
            "version": _K8S_ADVERTISED_GIT_VERSION,
        },
        "paths": _openapi_paths(openapi_version="2"),
        "definitions": _openapi_schema_definitions(state, ref_prefix="#/definitions/"),
    }


def _k8s_openapi_v3_discovery() -> dict[str, Any]:
    paths = {}
    for group, version in _openapi_group_versions():
        api_path = f"api/{version}" if not group else f"apis/{group}/{version}"
        hash_token = f"amc-{(group or 'core').replace('.', '-')}-{version}"
        paths[api_path] = {
            "serverRelativeURL": f"/openapi/v3/{api_path}?hash={hash_token}",
        }
    return {"paths": paths}


def _k8s_openapi_v3_document(
    state: SimulationState,
    group: str,
    version: str,
) -> dict[str, Any]:
    return {
        "openapi": "3.0.0",
        "info": {
            "title": f"AMC simulator Kubernetes schema {group or 'core'}/{version}",
            "version": _K8S_ADVERTISED_GIT_VERSION,
        },
        "paths": _openapi_paths(group=group, version=version, openapi_version="3"),
        "components": {
            "schemas": _openapi_schema_definitions(
                state,
                group=group,
                version=version,
                ref_prefix="#/components/schemas/",
            ),
        },
    }


def _openapi_schema_definitions(
    state: SimulationState,
    *,
    group: str | None = None,
    version: str | None = None,
    ref_prefix: str,
) -> dict[str, Any]:
    snapshot = resource_snapshot(state)
    definitions: dict[str, Any] = {}
    for kind, target in _EXPLAIN_RESOURCE_TARGETS.items():
        target_group, target_version, _resource = target
        if group is not None and (target_group != group or target_version != version):
            continue
        schema_info = _explain_schema_for_kind(state, kind, snapshot=snapshot)
        if schema_info is None:
            continue
        schema_name = _openapi_schema_name(schema_info["api_version"], schema_info["kind"])
        definitions[schema_name] = schema_info["schema"]
        definitions[_openapi_list_schema_name(schema_info["api_version"], schema_info["kind"])] = (
            _openapi_list_schema(schema_info, schema_name, ref_prefix)
        )
    return definitions


def _openapi_paths(
    *,
    group: str | None = None,
    version: str | None = None,
    openapi_version: str,
) -> dict[str, Any]:
    paths: dict[str, Any] = {}
    ref_prefix = "#/definitions/" if openapi_version == "2" else "#/components/schemas/"
    for kind, target in _EXPLAIN_RESOURCE_TARGETS.items():
        target_group, target_version, resource = target
        if group is not None and (target_group != group or target_version != version):
            continue
        api_version = target_version if not target_group else f"{target_group}/{target_version}"
        meta_kind = _k8s_resource_meta(target_group, target_version, resource)["kind"]
        schema_name = _openapi_schema_name(api_version, meta_kind)
        list_schema_name = _openapi_list_schema_name(api_version, meta_kind)
        base_path = f"/api/{target_version}" if not target_group else f"/apis/{target_group}/{target_version}"
        if _snapshot_kind_namespaced(kind):
            all_namespaces_path = f"{base_path}/{resource}"
            namespaced_path = f"{base_path}/namespaces/{{namespace}}/{resource}"
            paths[all_namespaces_path] = {
                "get": _openapi_operation(
                    "list",
                    target_group,
                    target_version,
                    meta_kind,
                    list_schema_name,
                    ref_prefix,
                    openapi_version,
                ),
            }
            paths[namespaced_path] = paths[all_namespaces_path]
            paths[f"{namespaced_path}/{{name}}"] = {
                "get": _openapi_operation(
                    "get",
                    target_group,
                    target_version,
                    meta_kind,
                    schema_name,
                    ref_prefix,
                    openapi_version,
                ),
            }
        else:
            resource_path = f"{base_path}/{resource}"
            paths[resource_path] = {
                "get": _openapi_operation(
                    "list",
                    target_group,
                    target_version,
                    meta_kind,
                    list_schema_name,
                    ref_prefix,
                    openapi_version,
                ),
            }
            paths[f"{resource_path}/{{name}}"] = {
                "get": _openapi_operation(
                    "get",
                    target_group,
                    target_version,
                    meta_kind,
                    schema_name,
                    ref_prefix,
                    openapi_version,
                ),
            }
    return paths


def _openapi_operation(
    action: str,
    group: str,
    version: str,
    kind: str,
    schema_name: str,
    ref_prefix: str,
    openapi_version: str,
) -> dict[str, Any]:
    response: dict[str, Any] = {"description": "OK"}
    schema_ref = {"$ref": ref_prefix + schema_name}
    if openapi_version == "2":
        response["schema"] = schema_ref
    else:
        response["content"] = {"application/json": {"schema": schema_ref}}
    return {
        "description": f"{action.title()} simulated {kind} resources.",
        "operationId": f"{action}{(group or 'core').replace('.', '_')}{version}{kind}",
        "responses": {"200": response},
        "x-kubernetes-action": action,
        "x-kubernetes-group-version-kind": {
            "group": group,
            "version": version,
            "kind": kind,
        },
    }


def _openapi_list_schema(
    schema_info: dict[str, Any],
    item_schema_name: str,
    ref_prefix: str,
) -> dict[str, Any]:
    return {
        "type": "object",
        "title": f"{schema_info['kind']}List",
        "description": f"List of simulator-backed {schema_info['kind']} resources.",
        "properties": {
            "apiVersion": {"type": "string", "description": "API version of this list."},
            "kind": {"type": "string", "description": "Kind of this list."},
            "metadata": {
                "type": "object",
                "title": "ListMeta",
                "description": "List metadata projected by the simulator.",
                "properties": {
                    "resourceVersion": {
                        "type": "string",
                        "description": "Synthetic list resource version.",
                    },
                },
            },
            "items": {
                "type": "array",
                "description": f"{schema_info['kind']} items.",
                "items": {"$ref": ref_prefix + item_schema_name},
            },
        },
    }


def _openapi_schema_name(api_version: str, kind: str) -> str:
    if "/" in api_version:
        group, version = api_version.split("/", 1)
        return f"io.k8s.api.{group}.{version}.{kind}"
    return f"io.k8s.api.core.{api_version}.{kind}"


def _openapi_list_schema_name(api_version: str, kind: str) -> str:
    return _openapi_schema_name(api_version, f"{kind}List")


def _openapi_group_versions() -> tuple[tuple[str, str], ...]:
    return tuple(
        sorted(
            {
                (group, version)
                for group, version, _resource in _EXPLAIN_RESOURCE_TARGETS.values()
            }
        )
    )


def _openapi_group_version_from_path(path: str) -> tuple[str, str]:
    parts = [part for part in path.strip("/").split("/") if part]
    if len(parts) == 2 and parts[0] == "api":
        return "", parts[1]
    if len(parts) == 3 and parts[0] == "apis":
        return parts[1], parts[2]
    return "", ""


def kubernetes_api_post_response(
    state: SimulationState,
    path: str,
    payload: dict[str, Any],
) -> KubernetesApiResponse | None:
    if path.startswith("/openapi"):
        return _k8s_read_only_response("POST", path)
    if path == "/version":
        return _k8s_read_only_response("POST", path)
    if not path.startswith(("/api", "/apis")):
        return None
    if path == "/apis/authorization.k8s.io/v1/selfsubjectaccessreviews":
        return KubernetesApiResponse(
            201,
            {
                "kind": "SelfSubjectAccessReview",
                "apiVersion": "authorization.k8s.io/v1",
                "metadata": payload.get("metadata", {}),
                "spec": payload.get("spec", {}),
                "status": {
                    "allowed": True,
                    "reason": "AMC simulator permits read-only diagnostic commands.",
                },
            },
            "application/json; charset=utf-8",
            "supported",
            "k8s.authorization.selfsubjectaccessreviews.create",
        )
    return kubernetes_api_mutating_response(state, "POST", path, payload)


def kubernetes_api_mutating_response(
    state: SimulationState,
    method: str,
    path: str,
    payload: dict[str, Any],
) -> KubernetesApiResponse:
    target = _k8s_mutation_target(path)
    if target is None:
        return _k8s_status_response(
            *_k8s_read_only_status_args(method, path),
        )
    resource = target["resource"]
    name = target["name"]
    subresource = target["subresource"]
    if target.get("extra") or not _k8s_subresource_mutation_allowed(method, resource, subresource):
        return _k8s_status_response(
            *_k8s_read_only_status_args(method, path),
        )
    now = state.clock.now()
    if method in {"PATCH", "PUT"} and resource == "deployments" and name:
        # Existence check BEFORE any overlay write: a refused mutation must
        # not leave partial state behind (the 404 used to be checked only
        # after set_workload/record_event had already mutated the overlay).
        if _find_named(resource_snapshot(state)["deployments"], name) is None:
            return _k8s_status_response(
                404,
                f"deployments {name!r} not found",
                "NotFound",
                "supported",
                "k8s.apps.deployments.mutate.not_found",
            )
        replicas = _payload_replicas(payload)
        if replicas is not None:
            state.mutations.set_workload(
                name,
                now=now,
                replicas=replicas,
                ready_replicas=replicas,
                deployment_status="Healthy" if replicas else "ScaledToZero",
                pod_status="Running",
            )
            reason = "ScalingReplicaSet" if subresource == "scale" else "Patched"
            state.mutations.record_event(
                "Normal",
                reason,
                f"deployment/{name}",
                f"{method.lower()} set deployment {name} replicas to {replicas}",
                now,
            )
        # Re-read after the overlay write so the response body reflects the
        # mutation, like a real API server's returned object would.
        deployment = _find_named(resource_snapshot(state)["deployments"], name)
        if deployment is None:  # pragma: no cover - defensive; checked above
            return _k8s_status_response(
                404,
                f"deployments {name!r} not found",
                "NotFound",
                "supported",
                "k8s.apps.deployments.mutate.not_found",
            )
        body = _k8s_scale(state, deployment) if subresource == "scale" else _k8s_deployment(state, deployment)
        return _k8s_json_response(body, f"k8s.apps.deployments.{method.lower()}")
    snapshot_kind = _mutation_snapshot_kind(resource)
    if method in {"PATCH", "PUT"} and snapshot_kind and name:
        state.mutations.put_resource(
            snapshot_kind,
            name,
            _generic_resource_row(state, snapshot_kind, name, payload=payload),
            now=now,
            namespace=target["namespace"],
        )
        body = _k8s_mutated_object(state, target, snapshot_kind, name)
        if body is not None:
            return _k8s_json_response(body, f"k8s.{resource}.{method.lower()}")
        return _k8s_status_response(
            200,
            f"{resource} {name!r} configured by simulator",
            "Configured",
            "supported",
            f"k8s.{resource}.{method.lower()}",
        )
    if method == "DELETE" and resource == "pods" and name:
        # Deleting a pod that is not in the (overlay-aware) snapshot must
        # 404 without touching the overlay — the unconditional delete used
        # to record phantom deletions for names that never existed.
        if _find_named(resource_snapshot(state)["pods"], name) is None:
            return _k8s_status_response(
                404,
                f"pods {name!r} not found",
                "NotFound",
                "supported",
                "k8s.core.pods.delete.not_found",
            )
        state.mutations.delete_pod(name, now=now)
        return _k8s_status_response(
            200,
            f"pods {name!r} deleted",
            "Deleted",
            "supported",
            "k8s.core.pods.delete",
        )
    if method == "DELETE" and resource == "deployments" and name:
        if _find_named(resource_snapshot(state)["deployments"], name) is None:
            return _k8s_status_response(
                404,
                f"deployments {name!r} not found",
                "NotFound",
                "supported",
                "k8s.apps.deployments.delete.not_found",
            )
        state.mutations.set_workload(
            name,
            now=now,
            replicas=0,
            ready_replicas=0,
            deployment_status="Deleted",
            pod_status="Terminating",
            deleted=True,
        )
        state.mutations.record_event(
            "Normal",
            "Deleted",
            f"deployment/{name}",
            f"deployment {name} deleted from simulator state",
            now,
        )
        return _k8s_status_response(
            200,
            f"deployments {name!r} deleted",
            "Deleted",
            "supported",
            "k8s.apps.deployments.delete",
        )
    if method == "DELETE" and snapshot_kind and name:
        rows = resource_snapshot(state).get(snapshot_kind, [])
        if _find_named(rows, name) is None:
            return _k8s_status_response(
                404,
                f"{resource} {name!r} not found",
                "NotFound",
                "supported",
                f"k8s.{resource}.delete.not_found",
            )
        state.mutations.delete_resource(snapshot_kind, name, now=now, namespace=target["namespace"])
        return _k8s_status_response(
            200,
            f"{resource} {name!r} deleted",
            "Deleted",
            "supported",
            f"k8s.{resource}.delete",
        )
    if method == "POST":
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        name = (
            name
            or str(metadata.get("name", ""))
            or f"simulated-{_normalized_resource_prefix(resource)}"
        )
        if snapshot_kind:
            state.mutations.put_resource(
                snapshot_kind,
                name,
                _generic_resource_row(state, snapshot_kind, name, payload=payload),
                now=now,
                namespace=target["namespace"],
            )
            body = _k8s_mutated_object(state, target, snapshot_kind, name)
            if body is not None:
                return KubernetesApiResponse(
                    201,
                    body,
                    "application/json; charset=utf-8",
                    "supported",
                    f"k8s.{resource}.create",
                )
        state.mutations.record_event(
            "Normal",
            "Created",
            f"{resource}/{name}",
            f"accepted create request for {resource}",
            now,
        )
        return _k8s_status_response(
            201,
            f"{resource} create accepted by simulator",
            "Created",
            "partial",
            f"k8s.{resource}.create.partial",
        )
    return _k8s_status_response(
        *_k8s_read_only_status_args(method, path),
    )


def _k8s_mutation_target(path: str) -> dict[str, str] | None:
    parts = [part for part in path.strip("/").split("/") if part]
    if parts[:3] == ["api", "v1", "namespaces"] and len(parts) >= 5:
        return {
            "group": "",
            "version": "v1",
            "namespace": parts[3],
            "resource": parts[4],
            "name": parts[5] if len(parts) >= 6 else "",
            "subresource": parts[6] if len(parts) >= 7 else "",
            "extra": "/".join(parts[7:]) if len(parts) >= 8 else "",
        }
    if parts and parts[0] == "apis" and len(parts) >= 6 and parts[3] == "namespaces":
        return {
            "group": parts[1],
            "version": parts[2],
            "namespace": parts[4],
            "resource": parts[5],
            "name": parts[6] if len(parts) >= 7 else "",
            "subresource": parts[7] if len(parts) >= 8 else "",
            "extra": "/".join(parts[8:]) if len(parts) >= 9 else "",
        }
    return None


def _k8s_subresource_mutation_allowed(method: str, resource: str, subresource: str) -> bool:
    if not subresource:
        return True
    return method in {"PATCH", "PUT"} and resource == "deployments" and subresource == "scale"


def _k8s_mutated_object(
    state: SimulationState,
    target: dict[str, str],
    snapshot_kind: str,
    name: str,
) -> dict[str, Any] | None:
    resource = target["resource"]
    group = target["group"]
    objects = _k8s_objects_for_resource(state, group, resource)
    if objects is None and snapshot_kind == "hpa":
        objects = _k8s_objects_for_resource(state, "autoscaling", "horizontalpodautoscalers")
    if objects is None and snapshot_kind == "ingress":
        objects = _k8s_objects_for_resource(state, "networking.k8s.io", "ingresses")
    if objects is None and snapshot_kind == "pvc":
        objects = _k8s_objects_for_resource(state, "", "persistentvolumeclaims")
    if objects is None:
        return None
    for obj in objects:
        metadata = obj.get("metadata", {})
        if (
            metadata.get("name") == name
            and metadata.get("namespace", target.get("namespace")) == target.get("namespace")
        ):
            return obj
    return None


def _payload_replicas(payload: dict[str, Any]) -> int | None:
    spec = payload.get("spec")
    if isinstance(spec, dict) and "replicas" in spec:
        if isinstance(spec["replicas"], bool):
            return None
        try:
            return max(0, int(spec["replicas"]))
        except (TypeError, ValueError):
            return None
    return None


def _k8s_scale(state: SimulationState, deployment: dict[str, Any]) -> dict[str, Any]:
    replicas = int(str(deployment["ready"]).split("/", 1)[1])
    ready = int(str(deployment["ready"]).split("/", 1)[0])
    return {
        "apiVersion": "autoscaling/v1",
        "kind": "Scale",
        "metadata": _k8s_metadata_for_row(
            state,
            deployment,
            labels=_snapshot_row_labels("deployments", deployment),
            include_generation=True,
        ),
        "spec": {"replicas": replicas},
        "status": {
            "replicas": replicas,
            "selector": _selector_string(_row_selector(deployment, deployment["name"])),
            "readyReplicas": ready,
        },
    }


def render_kubeconfig(
    server_url: str,
    namespace: str = DEFAULT_NAMESPACE,
    token: str = "",
) -> str:
    user_block = "  user: {}\n"
    if token:
        user_block = f"  user:\n    token: {json.dumps(token)}\n"
    return (
        "apiVersion: v1\n"
        "kind: Config\n"
        "clusters:\n"
        "- name: amc-simulator\n"
        "  cluster:\n"
        f"    server: {server_url}\n"
        "    insecure-skip-tls-verify: true\n"
        "contexts:\n"
        "- name: amc-simulator\n"
        "  context:\n"
        "    cluster: amc-simulator\n"
        "    user: amc-simulator\n"
        f"    namespace: {namespace}\n"
        "current-context: amc-simulator\n"
        "users:\n"
        "- name: amc-simulator\n"
        f"{user_block}"
    )


def record_kubernetes_api_call(
    state: SimulationState,
    *,
    method: str,
    path: str,
    query: dict[str, list[str]],
    response: KubernetesApiResponse,
    client: str,
    user_agent: str,
    latency_ms: float,
    request_id: str = "",
) -> None:
    trace_query = _redact_query(query)
    raw_input = method + " " + path
    if trace_query:
        raw_input += "?" + urllib.parse.urlencode(trace_query, doseq=True)
    stdout = _api_trace_body(response)
    trace = CommandTrace(
        id=state.traces.next_id(),
        received_at_wall_time=_dt.datetime.now(_dt.timezone.utc).isoformat(),
        simulated_time=_format_dt(state.clock.now()),
        raw_input=raw_input,
        argv=(method, path),
        client=client,
        command_family="kubernetes-api",
        verb=method,
        resource_kind=_api_resource_kind(path),
        resource_name=_api_resource_name(path),
        namespace=_api_namespace(path) or state.namespace,
        parsed_flags={
            "query": trace_query,
            "user_agent": user_agent,
        },
        support_status=response.support_status,
        matched_rule_id=response.matched_rule_id,
        active_scenarios=state.active_scenarios,
        exit_code=0 if response.status < 400 else 1,
        stdout_preview=_preview(stdout),
        stderr_preview="",
        stdout=stdout,
        stderr="",
        latency_ms=round(latency_ms, 3),
        fingerprint=_api_fingerprint(method, path),
        guessed_intent=_api_guess_intent(path, response),
        request_id=request_id,
    )
    state.traces.record(trace)


def _redact_query(query: dict[str, list[str]]) -> dict[str, list[str]]:
    return {
        key: ["***"] if _is_sensitive_query_key(key) else list(values)
        for key, values in query.items()
    }


def _is_sensitive_query_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    if normalized in _SENSITIVE_QUERY_KEYS:
        return True
    if normalized.endswith("_token"):
        return True
    return any(token.replace("-", "_") in normalized for token in _SENSITIVE_FLAG_TOKENS)


def _k8s_json_response(body: dict[str, Any], matched_rule_id: str) -> KubernetesApiResponse:
    return KubernetesApiResponse(
        200,
        body,
        "application/json; charset=utf-8",
        "supported",
        matched_rule_id,
    )


def _k8s_text_response(text: str, matched_rule_id: str) -> KubernetesApiResponse:
    return KubernetesApiResponse(
        200,
        text,
        "text/plain; charset=utf-8",
        "supported",
        matched_rule_id,
    )


def _k8s_status_response(
    status: int,
    message: str,
    reason: str,
    support_status: str,
    matched_rule_id: str,
) -> KubernetesApiResponse:
    return KubernetesApiResponse(
        status,
        {
            "kind": "Status",
            "apiVersion": "v1",
            "metadata": {},
            "status": "Success" if status < 400 else "Failure",
            "message": message,
            "reason": reason,
            "code": status,
        },
        "application/json; charset=utf-8",
        support_status,
        matched_rule_id,
    )


def _k8s_read_only_response(method: str, path: str) -> KubernetesApiResponse:
    return _k8s_status_response(*_k8s_read_only_status_args(method, path))


def _k8s_read_only_status_args(method: str, path: str) -> tuple[int, str, str, str, str]:
    return (
        405,
        f"{method} {path} is not supported by the simulator Kubernetes mutation facade",
        "MethodNotAllowed",
        "unsupported",
        "k8s.method.unsupported",
    )


def _k8s_api_group_list() -> dict[str, Any]:
    groups = [
        _k8s_api_group("apps", "v1"),
        _k8s_api_group("autoscaling", "v2"),
        _k8s_api_group("authorization.k8s.io", "v1"),
        _k8s_api_group("batch", "v1"),
        _k8s_api_group("discovery.k8s.io", "v1"),
        _k8s_api_group("networking.k8s.io", "v1"),
        _k8s_api_group("metrics.k8s.io", "v1beta1"),
    ]
    return {"kind": "APIGroupList", "apiVersion": "v1", "groups": groups}


def _k8s_api_group(name: str, version: str) -> dict[str, Any]:
    return {
        "name": name,
        "versions": [{"groupVersion": f"{name}/{version}", "version": version}],
        "preferredVersion": {"groupVersion": f"{name}/{version}", "version": version},
    }


def _k8s_group_resource_response(
    state: SimulationState,
    parts: list[str],
    query: dict[str, list[str]],
    as_table: bool,
) -> KubernetesApiResponse:
    if len(parts) < 2:
        return _k8s_status_response(
            404, "/apis requires an API group", "NotFound", "unsupported", "k8s.apis.malformed"
        )
    group = parts[1]
    versions = {
        "apps": "v1",
        "autoscaling": "v2",
        "authorization.k8s.io": "v1",
        "batch": "v1",
        "discovery.k8s.io": "v1",
        "networking.k8s.io": "v1",
        "metrics.k8s.io": "v1beta1",
    }
    if group not in versions:
        return _k8s_status_response(
            404,
            f"API group {group!r} is not implemented by the simulator",
            "NotFound",
            "unsupported",
            "k8s.group.unsupported",
        )
    if len(parts) == 2:
        return _k8s_json_response(_k8s_api_group(group, versions[group]), f"k8s.discovery.{group}")
    version = parts[2]
    if version != versions[group]:
        return _k8s_status_response(
            404,
            f"API version {group}/{version} is not implemented by the simulator",
            "NotFound",
            "unsupported",
            "k8s.version.unsupported",
        )
    if len(parts) == 3:
        return _k8s_json_response(
            _k8s_api_resource_list(group, version),
            f"k8s.discovery.{group}.{version}",
        )
    if len(parts) >= 6 and parts[3] == "namespaces":
        namespace = parts[4]
        resource = parts[5]
        name = parts[6] if len(parts) >= 7 else ""
        subresource = parts[7] if len(parts) >= 8 else ""
        if group == "apps" and resource == "deployments" and name and subresource == "scale":
            deployment = _find_named(resource_snapshot(state)["deployments"], name)
            if deployment is None:
                return _k8s_status_response(
                    404,
                    f"{resource} {name!r} not found",
                    "NotFound",
                    "supported",
                    "k8s.apps.get.scale.not_found",
                )
            return _k8s_json_response(_k8s_scale(state, deployment), "k8s.apps.get.scale")
        return _k8s_resource_response(
            state, group, version, namespace, resource, name, query, as_table
        )
    if group == "metrics.k8s.io" and len(parts) >= 4 and parts[3] == "nodes":
        name = parts[4] if len(parts) >= 5 else ""
        return _k8s_resource_response(
            state, group, version, "", "nodes", name, query, as_table
        )
    return _k8s_status_response(
        404,
        f"/{'/'.join(parts)} is not implemented by the simulator Kubernetes API",
        "NotFound",
        "unsupported",
        "k8s.group.path.unsupported",
    )


def _k8s_core_resource_response(
    state: SimulationState,
    parts: list[str],
    query: dict[str, list[str]],
    as_table: bool,
) -> KubernetesApiResponse:
    if len(parts) == 3:
        return _k8s_resource_response(state, "", "v1", "", parts[2], "", query, as_table)
    if len(parts) == 4 and parts[2] in {"nodes", "namespaces"}:
        return _k8s_resource_response(
            state, "", "v1", "", parts[2], parts[3], query, as_table
        )
    if len(parts) >= 5 and parts[2] == "namespaces":
        namespace = parts[3]
        if len(parts) == 4:
            return _k8s_resource_response(
                state, "", "v1", "", "namespaces", namespace, query, as_table
            )
        resource = parts[4]
        name = parts[5] if len(parts) >= 6 else ""
        if resource == "pods" and len(parts) >= 7 and parts[6] == "log":
            pod_name = name
            parsed = ParsedCommand(
                raw_input=f"kubectl logs {pod_name} -n {namespace}",
                argv=("kubectl", "logs", pod_name, "-n", namespace),
                family="kubectl",
                verb="logs",
                resource_kind="pods",
                resource_name=pod_name,
                namespace=namespace,
                flags={"namespace": namespace},
                positionals=("logs", pod_name),
            )
            return _k8s_text_response(_render_logs(state, parsed), "k8s.core.pods.log")
        return _k8s_resource_response(
            state, "", "v1", namespace, resource, name, query, as_table
        )
    return _k8s_status_response(
        404,
        f"/{'/'.join(parts)} is not implemented by the simulator Kubernetes API",
        "NotFound",
        "unsupported",
        "k8s.core.path.unsupported",
    )


def _k8s_api_resource_list(group: str, version: str) -> dict[str, Any]:
    read_verbs = ["get", "list"]
    mutate_verbs = ["create", "delete", "get", "list", "patch", "update"]
    resources_by_group = {
        "": [
            ("namespaces", "Namespace", False, read_verbs),
            ("nodes", "Node", False, read_verbs),
            ("pods", "Pod", True, ["get", "list", "delete"]),
            ("pods/log", "Pod", True, ["get"]),
            ("configmaps", "ConfigMap", True, mutate_verbs),
            ("secrets", "Secret", True, mutate_verbs),
            ("replicationcontrollers", "ReplicationController", True, read_verbs),
            ("services", "Service", True, mutate_verbs),
            ("endpoints", "Endpoints", True, read_verbs),
            ("events", "Event", True, read_verbs),
            ("persistentvolumeclaims", "PersistentVolumeClaim", True, mutate_verbs),
            ("serviceaccounts", "ServiceAccount", True, mutate_verbs),
        ],
        "apps": [
            ("deployments", "Deployment", True, mutate_verbs),
            ("deployments/scale", "Scale", True, ["get", "patch", "update"]),
            ("replicasets", "ReplicaSet", True, read_verbs),
            ("daemonsets", "DaemonSet", True, mutate_verbs),
            ("statefulsets", "StatefulSet", True, mutate_verbs),
        ],
        "autoscaling": [
            ("horizontalpodautoscalers", "HorizontalPodAutoscaler", True, mutate_verbs),
        ],
        "authorization.k8s.io": [
            ("selfsubjectaccessreviews", "SelfSubjectAccessReview", False, ["create"]),
        ],
        "batch": [
            ("jobs", "Job", True, mutate_verbs),
            ("cronjobs", "CronJob", True, mutate_verbs),
        ],
        "discovery.k8s.io": [
            ("endpointslices", "EndpointSlice", True, read_verbs),
        ],
        "networking.k8s.io": [
            ("ingresses", "Ingress", True, mutate_verbs),
        ],
        "metrics.k8s.io": [
            ("nodes", "NodeMetrics", False, read_verbs),
            ("pods", "PodMetrics", True, read_verbs),
        ],
    }
    group_version = version if not group else f"{group}/{version}"
    resources = []
    for name, kind, namespaced, verbs in resources_by_group.get(group, []):
        entry = {
            "name": name,
            "singularName": "",
            "namespaced": namespaced,
            "kind": kind,
            "verbs": verbs,
        }
        if name == "pods":
            entry["shortNames"] = ["po"]
        elif name == "configmaps":
            entry["shortNames"] = ["cm"]
        elif name == "services":
            entry["shortNames"] = ["svc"]
        elif name == "endpoints":
            entry["shortNames"] = ["ep"]
        elif name == "serviceaccounts":
            entry["shortNames"] = ["sa"]
        elif name == "replicationcontrollers":
            entry["shortNames"] = ["rc"]
        elif name == "persistentvolumeclaims":
            entry["shortNames"] = ["pvc"]
        elif name == "deployments":
            entry["shortNames"] = ["deploy"]
        elif name == "replicasets":
            entry["shortNames"] = ["rs"]
        elif name == "daemonsets":
            entry["shortNames"] = ["ds"]
        elif name == "statefulsets":
            entry["shortNames"] = ["sts"]
        elif name == "horizontalpodautoscalers":
            entry["shortNames"] = ["hpa"]
        elif name == "cronjobs":
            entry["shortNames"] = ["cj"]
        elif name == "ingresses":
            entry["shortNames"] = ["ing"]
        if name in {
            "pods",
            "services",
            "deployments",
            "replicasets",
            "daemonsets",
            "statefulsets",
            "horizontalpodautoscalers",
            "jobs",
            "cronjobs",
        }:
            entry["categories"] = ["all"]
        resources.append(entry)
    return {
        "kind": "APIResourceList",
        "apiVersion": "v1",
        "groupVersion": group_version,
        "resources": resources,
    }


def _k8s_resource_response(
    state: SimulationState,
    group: str,
    version: str,
    namespace: str,
    resource: str,
    name: str,
    query: dict[str, list[str]],
    as_table: bool,
) -> KubernetesApiResponse:
    objects = _k8s_objects_for_resource(state, group, resource)
    if objects is None:
        return _k8s_status_response(
            404,
            f"resource {resource!r} is not implemented by the simulator Kubernetes API",
            "NotFound",
            "unsupported",
            "k8s.resource.unsupported",
        )
    objects = _filter_k8s_objects_by_namespace(resource, objects, namespace)
    objects = _filter_k8s_objects(objects, query)
    meta = _k8s_resource_meta(group, version, resource)
    if name:
        for obj in objects:
            if obj.get("metadata", {}).get("name") == name:
                if as_table:
                    return _k8s_json_response(
                        _k8s_table(state, resource, [obj]),
                        f"k8s.{group or 'core'}.get.{resource}.table",
                    )
                return _k8s_json_response(obj, f"k8s.{group or 'core'}.get.{resource}")
        return _k8s_status_response(
            404,
            f"{resource} {name!r} not found",
            "NotFound",
            "supported",
            f"k8s.{group or 'core'}.get.not_found",
        )
    if as_table:
        return _k8s_json_response(
            _k8s_table(state, resource, objects),
            f"k8s.{group or 'core'}.list.{resource}.table",
        )
    return _k8s_json_response({
        "kind": meta["list_kind"],
        "apiVersion": meta["api_version"],
        "metadata": {"resourceVersion": _k8s_list_resource_version(state)},
        "items": objects,
    }, f"k8s.{group or 'core'}.list.{resource}")


def _filter_k8s_objects_by_namespace(
    resource: str,
    objects: list[dict[str, Any]],
    namespace: str,
) -> list[dict[str, Any]]:
    if not namespace or resource in {"namespaces", "nodes"}:
        return objects
    return [
        obj for obj in objects
        if obj.get("metadata", {}).get("namespace") == namespace
    ]


# Resource families a real-client `?watch=true` request streams as a bounded
# simulated watch. Keyed `(group, version, resource)`; the core group is "".
# v1 asserts only the two families `kubectl get --watch` most plausibly hits;
# the stream loop itself is generic over `_k8s_objects_for_resource`, so
# opting another modeled list path in is a one-line addition here.
_WATCHABLE_LIST_RESOURCES = {
    ("", "v1", "pods"),
    ("apps", "v1", "deployments"),
}


def _watch_requested(query: dict[str, list[str]]) -> bool:
    """True when the query asks for a watch (`watch=true` or `watch=1`)."""
    return any(value in ("true", "1") for value in query.get("watch", []))


def k8s_watch_plan(
    state: SimulationState,
    path: str,
    query: dict[str, list[str]],
) -> dict[str, str] | None:
    """Return a watch plan for a modeled list path, else ``None``.

    Fires only when the query requests a watch on a modeled *list* path (no
    object name) for a watchable resource family. Single-object paths,
    unmodeled resources, and non-watch requests return ``None`` so the caller
    falls through to the existing one-shot list / unsupported handling. The
    ``state`` argument is unused today but keeps the signature parallel with
    the other snapshot-backed helpers.
    """
    if not _watch_requested(query):
        return None
    parts = [segment for segment in path.split("/") if segment]
    group = version = namespace = resource = name = ""
    if parts[:2] == ["api", "v1"]:
        group, version = "", "v1"
        rest = parts[2:]
    elif parts[:1] == ["apis"] and len(parts) >= 4:
        group, version = parts[1], parts[2]
        rest = parts[3:]
    else:
        return None
    if len(rest) == 1:
        resource = rest[0]
    elif len(rest) >= 3 and rest[0] == "namespaces":
        namespace, resource = rest[1], rest[2]
        name = rest[3] if len(rest) >= 4 else ""
    else:
        return None
    if name:
        # Watching a single named object is out of scope for v1.
        return None
    if (group, version, resource) not in _WATCHABLE_LIST_RESOURCES:
        return None
    return {
        "group": group,
        "version": version,
        "namespace": namespace,
        "resource": resource,
        "matched_rule_id": f"k8s.{group or 'core'}.watch.{resource}",
    }


def k8s_watch_objects(
    state: SimulationState,
    plan: dict[str, str],
    query: dict[str, list[str]],
) -> list[dict[str, Any]]:
    """Overlay-aware object set for a watch, mirroring the list path.

    Runs the exact ``_k8s_objects_for_resource`` -> namespace filter ->
    selector filter chain ``_k8s_resource_response`` uses, so a watch always
    observes the same objects the equivalent list would return.
    """
    objects = _k8s_objects_for_resource(state, plan["group"], plan["resource"]) or []
    objects = _filter_k8s_objects_by_namespace(plan["resource"], objects, plan["namespace"])
    return _filter_k8s_objects(objects, query)


def k8s_watch_object_key(obj: dict[str, Any]) -> str:
    """Stable identity for watch diffing: ``uid`` when present, else ns/name."""
    meta = obj.get("metadata", {})
    uid = meta.get("uid")
    if uid:
        return f"uid:{uid}"
    return f"nn:{meta.get('namespace', '')}/{meta.get('name', '')}"


def k8s_watch_trace_response(
    plan: dict[str, str],
    *,
    event_count: int,
    refused: bool = False,
) -> KubernetesApiResponse:
    """Synthetic Status recorded as the watch's ``kubernetes-api`` trace.

    A refused watch (over the SSE ceiling) records a partial Status 503; a
    normal close records a supported Status naming the emitted event count.
    The refusal Status is *also* returned to the client as the HTTP response
    (the stream never started, so this Status body is what the client sees).
    The normal-close Status is trace-only: the watch body was already streamed
    to the client as newline-delimited watch events, so this Status just
    carries the event count into ``record_kubernetes_api_call``.
    """
    if refused:
        return _k8s_status_response(
            503,
            "watch stream refused: concurrent SSE connection limit reached",
            "ServiceUnavailable",
            "partial",
            plan["matched_rule_id"],
        )
    return KubernetesApiResponse(
        200,
        {
            "kind": "Status",
            "apiVersion": "v1",
            "metadata": {},
            "status": "Success",
            "message": f"watch closed after {event_count} event(s)",
            "reason": "WatchClosed",
            "code": 200,
        },
        "application/json; charset=utf-8",
        "supported",
        plan["matched_rule_id"],
    )


def _k8s_resource_meta(group: str, version: str, resource: str) -> dict[str, str]:
    api_version = version if not group else f"{group}/{version}"
    kinds = {
        "namespaces": "Namespace",
        "nodes": "Node" if group != "metrics.k8s.io" else "NodeMetrics",
        "pods": "Pod" if group != "metrics.k8s.io" else "PodMetrics",
        "configmaps": "ConfigMap",
        "secrets": "Secret",
        "replicationcontrollers": "ReplicationController",
        "services": "Service",
        "endpoints": "Endpoints",
        "endpointslices": "EndpointSlice",
        "events": "Event",
        "persistentvolumeclaims": "PersistentVolumeClaim",
        "serviceaccounts": "ServiceAccount",
        "deployments": "Deployment",
        "replicasets": "ReplicaSet",
        "daemonsets": "DaemonSet",
        "statefulsets": "StatefulSet",
        "horizontalpodautoscalers": "HorizontalPodAutoscaler",
        "jobs": "Job",
        "cronjobs": "CronJob",
        "ingresses": "Ingress",
    }
    kind = kinds.get(resource, resource.rstrip("s").title())
    return {"api_version": api_version, "kind": kind, "list_kind": f"{kind}List"}


from .server_k8s_tables import (
    _accepts_table as _accepts_table,
    _k8s_table as _k8s_table,
    _k8s_column as _k8s_column,
    _k8s_table_schema as _k8s_table_schema,
    _k8s_pod_cells as _k8s_pod_cells,
    _k8s_pod_display_status as _k8s_pod_display_status,
    _k8s_deployment_cells as _k8s_deployment_cells,
    _k8s_service_cells as _k8s_service_cells,
    _k8s_endpoints_cells as _k8s_endpoints_cells,
    _k8s_endpointslice_cells as _k8s_endpointslice_cells,
    _k8s_event_cells as _k8s_event_cells,
    _k8s_hpa_cells as _k8s_hpa_cells,
    _k8s_node_cells as _k8s_node_cells,
    _k8s_replicaset_cells as _k8s_replicaset_cells,
    _k8s_daemonset_cells as _k8s_daemonset_cells,
    _k8s_pvc_cells as _k8s_pvc_cells,
    _k8s_statefulset_cells as _k8s_statefulset_cells,
    _k8s_ingress_cells as _k8s_ingress_cells,
    _k8s_secret_cells as _k8s_secret_cells,
    _k8s_configmap_cells as _k8s_configmap_cells,
    _k8s_serviceaccount_cells as _k8s_serviceaccount_cells,
    _k8s_job_cells as _k8s_job_cells,
    _k8s_cronjob_cells as _k8s_cronjob_cells,
    _k8s_namespace_cells as _k8s_namespace_cells,
    _k8s_default_cells as _k8s_default_cells,
)


def _k8s_objects_for_resource(
    state: SimulationState,
    group: str,
    resource: str,
    snapshot: dict[str, list[dict[str, Any]]] | None = None,
) -> list[dict[str, Any]] | None:
    snapshot = snapshot if snapshot is not None else resource_snapshot(state)
    if group == "metrics.k8s.io":
        if resource == "pods":
            return [_k8s_pod_metrics(state, pod) for pod in snapshot["pods"]]
        if resource == "nodes":
            return [_k8s_node_metrics(state, node) for node in snapshot["nodes"]]
        return None
    if resource == "namespaces":
        return [_k8s_namespace(state)]
    if resource == "nodes":
        return [_k8s_node(state, node) for node in snapshot["nodes"]]
    if resource == "pods":
        return [_k8s_pod(state, pod) for pod in snapshot["pods"]]
    if resource == "configmaps":
        return [_k8s_configmap(state, configmap) for configmap in snapshot["configmaps"]]
    if resource == "serviceaccounts":
        return [_k8s_serviceaccount(state, serviceaccount) for serviceaccount in snapshot["serviceaccounts"]]
    if resource == "replicationcontrollers":
        return []
    if resource == "services":
        return [_k8s_service(state, service) for service in snapshot["services"]]
    if resource == "endpoints":
        return [_k8s_endpoints(state, endpoint) for endpoint in snapshot["endpoints"]]
    if resource == "events":
        return [_k8s_event(state, event, index) for index, event in enumerate(snapshot["events"], start=1)]
    if resource == "persistentvolumeclaims":
        return [_k8s_pvc(state, pvc) for pvc in snapshot["pvc"]]
    if resource == "secrets":
        generic_secrets = [
            _k8s_secret(state, secret)
            for secret in snapshot["secrets"]
            if secret.get("type") != "helm.sh/release.v1"
        ]
        return [*_helm_secret_objects(state), *generic_secrets]
    if resource == "deployments" and group == "apps":
        return [_k8s_deployment(state, deployment) for deployment in snapshot["deployments"]]
    if resource == "replicasets" and group == "apps":
        return [_k8s_replicaset(state, replicaset) for replicaset in snapshot["replicasets"]]
    if resource == "daemonsets" and group == "apps":
        return [_k8s_daemonset(state, daemonset) for daemonset in snapshot["daemonsets"]]
    if resource == "statefulsets" and group == "apps":
        return [_k8s_statefulset(state, sts) for sts in snapshot["statefulsets"]]
    if resource == "horizontalpodautoscalers" and group == "autoscaling":
        return [_k8s_hpa(state, hpa) for hpa in snapshot["hpa"]]
    if resource == "jobs" and group == "batch":
        return [_k8s_job(state, job) for job in snapshot["jobs"]]
    if resource == "cronjobs" and group == "batch":
        return [_k8s_cronjob(state, cronjob) for cronjob in snapshot["cronjobs"]]
    if resource == "endpointslices" and group == "discovery.k8s.io":
        return [
            _k8s_endpointslice(state, endpointslice, snapshot=snapshot)
            for endpointslice in snapshot["endpointslices"]
        ]
    if resource == "ingresses" and group == "networking.k8s.io":
        return [_k8s_ingress(state, ingress) for ingress in snapshot["ingress"]]
    return None


from .server_k8s_objects import (
    _k8s_namespace as _k8s_namespace,
    _k8s_pod as _k8s_pod,
    _k8s_configmap as _k8s_configmap,
    _k8s_secret as _k8s_secret,
    _k8s_serviceaccount as _k8s_serviceaccount,
    _k8s_deployment as _k8s_deployment,
    _k8s_replicaset as _k8s_replicaset,
    _k8s_daemonset as _k8s_daemonset,
    _k8s_statefulset as _k8s_statefulset,
    _k8s_service as _k8s_service,
    _k8s_endpoints as _k8s_endpoints,
    _k8s_event as _k8s_event,
    _k8s_hpa as _k8s_hpa,
    _k8s_job as _k8s_job,
    _k8s_cronjob as _k8s_cronjob,
    _k8s_pvc as _k8s_pvc,
    _k8s_ingress as _k8s_ingress,
    _k8s_node as _k8s_node,
    _k8s_pod_metrics as _k8s_pod_metrics,
    _k8s_node_metrics as _k8s_node_metrics,
    _k8s_metadata as _k8s_metadata,
    _k8s_metadata_for_row as _k8s_metadata_for_row,
    _row_selector as _row_selector,
    _row_template_labels as _row_template_labels,
    _selector_string as _selector_string,
    _k8s_owner_reference as _k8s_owner_reference,
    _k8s_workload_labels as _k8s_workload_labels,
    _k8s_container_state as _k8s_container_state,
    _k8s_timestamp as _k8s_timestamp,
    _stable_pod_ip as _stable_pod_ip,
)


def _k8s_endpointslice(
    state: SimulationState,
    endpointslice: dict[str, Any],
    snapshot: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    namespace = _snapshot_row_namespace(endpointslice, state.namespace)
    snapshot = snapshot if snapshot is not None else resource_snapshot(state)
    pods = [
        pod for pod in snapshot["pods"]
        if pod["component"] == endpointslice["service"]
        and _snapshot_row_namespace(pod, state.namespace) == namespace
    ]
    return {
        "apiVersion": "discovery.k8s.io/v1",
        "kind": "EndpointSlice",
        "metadata": _k8s_metadata(
            state,
            endpointslice["name"],
            namespace=namespace,
            labels={"kubernetes.io/service-name": endpointslice["service"]},
            resource_version=endpointslice.get("resource_version"),
        ),
        "addressType": endpointslice["address_type"],
        "ports": [{"name": "http", "protocol": "TCP", "port": 8080}],
        "endpoints": [
            {
                "addresses": [pod["pod_ip"]],
                "conditions": {"ready": pod["status"] == "Running"},
                "targetRef": {"kind": "Pod", "namespace": state.namespace, "name": pod["name"]},
            }
            for pod in pods
        ],
    }

from .server_helm_impl import (  # noqa: F401  (re-import at original position)
    _helm_secret_objects,
    _helm_release_revisions,
    _helm_secret_object,
    _helm_encoded_release_data,
    _helm_release_payload,
)


def _filter_k8s_objects(
    objects: list[dict[str, Any]],
    query: dict[str, list[str]],
) -> list[dict[str, Any]]:
    label_selector = _query_str(query, "labelSelector", "")
    field_selector = _query_str(query, "fieldSelector", "")
    return [
        obj for obj in objects
        if _matches_label_selector(obj.get("metadata", {}).get("labels", {}), label_selector)
        and _matches_field_selector(obj, field_selector)
    ]


def _matches_label_selector(labels: dict[str, str], selector: str) -> bool:
    if not selector:
        return True
    for item in _split_selector(selector):
        if " notin " in item or " notin(" in item:
            key, values = _selector_set_requirement(item, "notin")
            if labels.get(key) in values:
                return False
        elif " in " in item or " in(" in item:
            key, values = _selector_set_requirement(item, "in")
            if labels.get(key) not in values:
                return False
        elif "!=" in item:
            key, value = item.split("!=", 1)
            if labels.get(key.strip()) == value.strip():
                return False
        elif "==" in item or "=" in item:
            separator = "==" if "==" in item else "="
            key, value = item.split(separator, 1)
            if labels.get(key.strip()) != value.strip():
                return False
        elif item.startswith("!"):
            if item[1:].strip() in labels:
                return False
        elif item.strip() not in labels:
            return False
    return True


def _matches_field_selector(obj: dict[str, Any], selector: str) -> bool:
    if not selector:
        return True
    for item in _split_selector(selector):
        if "!=" in item:
            key, value = item.split("!=", 1)
            if str(_nested_field(obj, key.strip())) == value.strip():
                return False
        elif "==" in item or "=" in item:
            separator = "==" if "==" in item else "="
            key, value = item.split(separator, 1)
            if str(_nested_field(obj, key.strip())) != value.strip():
                return False
    return True


def _selector_set_requirement(item: str, operator: str) -> tuple[str, set[str]]:
    key, _, rest = item.partition(operator)
    values = rest.strip()
    if values.startswith("(") and values.endswith(")"):
        values = values[1:-1]
    return key.strip(), {value.strip() for value in values.split(",") if value.strip()}


def _split_selector(selector: str) -> list[str]:
    items = []
    start = 0
    depth = 0
    for index, char in enumerate(selector):
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        elif char == "," and depth == 0:
            items.append(selector[start:index].strip())
            start = index + 1
    items.append(selector[start:].strip())
    return [item for item in items if item]


def _nested_field(obj: dict[str, Any], path: str) -> Any:
    value: Any = obj
    for part in path.split("."):
        if isinstance(value, dict):
            value = value.get(part)
        else:
            return None
    return value


def _api_trace_body(response: KubernetesApiResponse) -> str:
    if isinstance(response.body, str):
        return _preview(response.body, 2000)
    safe_body = _redact_large_secret_data(response.body)
    return _preview(json.dumps(safe_body, sort_keys=True), 2000)


def _redact_large_secret_data(value: Any) -> Any:
    if isinstance(value, list):
        return [_redact_large_secret_data(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {}
    for key, item in value.items():
        if key == "data" and isinstance(item, dict) and "release" in item:
            result[key] = {**item, "release": "<helm release payload>"}
        else:
            result[key] = _redact_large_secret_data(item)
    return result


def _api_namespace(path: str) -> str:
    parts = [part for part in path.strip("/").split("/") if part]
    for index, part in enumerate(parts):
        if part == "namespaces" and index + 1 < len(parts):
            return parts[index + 1]
    return ""


def _api_resource_kind(path: str) -> str:
    parts = [part for part in path.strip("/").split("/") if part]
    for index, part in enumerate(parts):
        if part == "namespaces" and index + 2 < len(parts):
            return parts[index + 2]
    if len(parts) >= 3 and parts[:2] == ["api", "v1"]:
        return parts[2]
    if len(parts) >= 4 and parts[0] == "apis":
        return parts[3]
    return parts[-1] if parts else ""


def _api_resource_name(path: str) -> str:
    parts = [part for part in path.strip("/").split("/") if part]
    for index, part in enumerate(parts):
        if part == "namespaces" and index + 3 < len(parts):
            return parts[index + 3]
    if len(parts) >= 4 and parts[:2] == ["api", "v1"]:
        return parts[3]
    if len(parts) >= 5 and parts[0] == "apis":
        return parts[4]
    return ""


def _api_fingerprint(method: str, path: str) -> str:
    parts = [part for part in path.strip("/").split("/") if part]
    normalized = []
    index = 0
    while index < len(parts):
        part = parts[index]
        normalized.append(part)
        if part == "namespaces" and index + 1 < len(parts):
            normalized.append("{namespace}")
            index += 2
            continue
        if normalized[-1] in {
            "pods",
            "configmaps",
            "secrets",
            "replicationcontrollers",
            "services",
            "endpoints",
            "endpointslices",
            "events",
            "persistentvolumeclaims",
            "serviceaccounts",
            "deployments",
            "replicasets",
            "daemonsets",
            "statefulsets",
            "horizontalpodautoscalers",
            "ingresses",
            "nodes",
            "jobs",
            "cronjobs",
        } and index + 1 < len(parts):
            normalized.append("{name}")
            index += 2
            continue
        index += 1
    return f"kubernetes-api {method} /{'/'.join(normalized)}"


def _api_guess_intent(path: str, response: KubernetesApiResponse) -> str:
    if response.support_status == "supported":
        return "Real kubectl/helm-compatible API call handled by simulator."
    return f"Add Kubernetes API compatibility for {path}."


def _is_kubernetes_api_path(path: str) -> bool:
    return path == "/version" or path.startswith(("/api", "/apis", "/openapi"))


def _rate_limit_bucket(path: str) -> str:
    if path == "/v1/commands":
        return "commands"
    if path == "/mcp":
        # MCP tools/call is command-like: cap it per client like /v1/commands.
        return "mcp"
    if _is_kubernetes_api_path(path):
        return "kubernetes-api"
    return ""


__all__ = [
    'DEFAULT_RELEASE',
    'DEFAULT_CHART',
    'DEFAULT_NAMESPACE',
    'OpsComponentImpact',
    'OpsScenarioProfile',
    '_impact',
    '_profile',
    'OPS_SCENARIO_PROFILES',
    'validate_ops_profiles',
    'SimulationClock',
    'ParsedCommand',
    'CommandResult',
    'KubernetesApiResponse',
    'ContinuousGenerationStatus',
    'SimulationState',
    'build_state',
    'load_anomaly_rows',
    '_snapshot_row_namespace',
    '_snapshot_row_key',
    '_snapshot_kind_namespaced',
    'run_command',
    'parse_command',
    '_split_flags',
    '_parse_kubectl',
    '_parse_helm',
    '_split_resource_token',
    '_normalize_kind',
    'render_command',
    '_with_flag_support',
    '_render_kubectl',
    '_render_helm',
    '_unsupported',
    'resource_snapshot',
    '_apply_default_namespaces',
    '_apply_mutation_rows',
    '_render_get',
    '_render_get_all',
    '_filter_snapshot_rows',
    '_snapshot_row_matches_namespace',
    '_snapshot_row_labels',
    '_snapshot_row_matches_field_selector',
    '_normalized_resource_prefix',
    '_render_describe',
    '_logs_uses_selector',
    '_render_logs_command',
    '_logs_target_pods',
    '_logs_container_name',
    '_logs_has_container_flag',
    '_logs_since_time',
    '_logs_tail_limit',
    '_render_logs',
    '_render_pod_logs',
    '_render_top',
    '_render_kubectl_version',
    '_render_kubectl_api_versions',
    '_render_kubectl_api_resources',
    '_render_kubectl_cluster_info',
    '_render_rollout_status',
    '_render_rollout_history',
    '_render_rollout_restart',
    '_render_rollout_pause',
    '_render_rollout_resume',
    '_render_rollout_undo',
    '_rollout_component',
    '_is_deployment_rollout_target',
    '_render_scale',
    '_render_delete',
    '_render_apply',
    '_resource_from_manifest_name',
    '_mutation_snapshot_kind',
    '_record_continuous_generation_failure',
    '_generic_resource_row',
    '_generic_resource_metadata',
    '_string_dict',
    '_configmap_keys_from_flags',
    '_parsed_replicas',
    '_render_wait',
    '_render_exec',
    '_render_port_forward',
    '_render_helm_list',
    '_render_helm_status',
    '_render_helm_history',
    '_render_helm_env',
    '_render_helm_get',
    '_render_helm_test',
    '_render_helm_install',
    '_render_helm_upgrade',
    '_helm_value_overrides',
    '_render_helm_rollback',
    '_not_found',
    '_component_health',
    '_component_impacts',
    '_apply_component_impact',
    '_status_priority',
    '_component_scenarios',
    '_exposed_active_scenarios',
    '_exposed_component_scenarios',
    '_component_events',
    '_component_rollout_notes',
    '_event_rows',
    '_node_rows',
    '_helm_release',
    '_helm_notes',
    '_helm_current_description',
    '_replica_count',
    '_pod_name',
    '_component_from_name',
    '_stable_cluster_ip',
    '_find_named',
    '_table',
    'command_fingerprint',
    'guess_intent',
    '_preview',
    '_redact_command_for_trace',
    '_redact_argv',
    '_redact_parsed_flags',
    '_is_sensitive_flag_name',
    '_format_dt',
    '_parse_user_timestamp',
    '_parse_optional_timestamp',
    'RequestBodyTooLarge',
    '_read_json_body',
    '_read_optional_json_body',
    '_content_length',
    'kubernetes_api_response',
    'kubernetes_api_post_response',
    'kubernetes_api_mutating_response',
    '_k8s_mutation_target',
    '_k8s_subresource_mutation_allowed',
    '_k8s_mutated_object',
    '_payload_replicas',
    '_k8s_scale',
    'render_kubeconfig',
    'record_kubernetes_api_call',
    '_redact_query',
    '_is_sensitive_query_key',
    '_k8s_json_response',
    '_k8s_text_response',
    '_k8s_status_response',
    '_k8s_read_only_response',
    '_k8s_read_only_status_args',
    '_k8s_api_group_list',
    '_k8s_api_group',
    '_k8s_group_resource_response',
    '_k8s_core_resource_response',
    '_k8s_api_resource_list',
    '_k8s_resource_response',
    '_filter_k8s_objects_by_namespace',
    '_k8s_list_resource_version',
    '_k8s_resource_meta',
    '_accepts_table',
    '_k8s_table',
    '_k8s_column',
    '_k8s_table_schema',
    '_k8s_pod_cells',
    '_k8s_pod_display_status',
    '_k8s_deployment_cells',
    '_k8s_service_cells',
    '_k8s_endpoints_cells',
    '_k8s_endpointslice_cells',
    '_k8s_event_cells',
    '_k8s_hpa_cells',
    '_k8s_node_cells',
    '_k8s_replicaset_cells',
    '_k8s_daemonset_cells',
    '_k8s_pvc_cells',
    '_k8s_statefulset_cells',
    '_k8s_ingress_cells',
    '_k8s_secret_cells',
    '_k8s_configmap_cells',
    '_k8s_serviceaccount_cells',
    '_k8s_job_cells',
    '_k8s_cronjob_cells',
    '_k8s_namespace_cells',
    '_k8s_default_cells',
    '_k8s_objects_for_resource',
    '_k8s_namespace',
    '_k8s_pod',
    '_k8s_configmap',
    '_k8s_secret',
    '_k8s_serviceaccount',
    '_k8s_deployment',
    '_k8s_replicaset',
    '_k8s_daemonset',
    '_k8s_statefulset',
    '_k8s_service',
    '_k8s_endpoints',
    '_k8s_event',
    '_k8s_hpa',
    '_k8s_job',
    '_k8s_cronjob',
    '_k8s_pvc',
    '_k8s_ingress',
    '_k8s_endpointslice',
    '_k8s_node',
    '_k8s_pod_metrics',
    '_k8s_node_metrics',
    '_helm_secret_objects',
    '_helm_release_revisions',
    '_helm_secret_object',
    '_helm_encoded_release_data',
    '_helm_release_payload',
    '_k8s_metadata',
    '_k8s_metadata_for_row',
    '_row_selector',
    '_row_template_labels',
    '_selector_string',
    '_k8s_owner_reference',
    '_k8s_workload_labels',
    '_k8s_container_state',
    '_filter_k8s_objects',
    '_matches_label_selector',
    '_matches_field_selector',
    '_selector_set_requirement',
    '_split_selector',
    '_nested_field',
    '_k8s_timestamp',
    '_stable_pod_ip',
    '_api_trace_body',
    '_redact_large_secret_data',
    '_api_namespace',
    '_api_resource_kind',
    '_api_resource_name',
    '_api_fingerprint',
    '_api_guess_intent',
    '_is_kubernetes_api_path',
    '_rate_limit_bucket',
]
