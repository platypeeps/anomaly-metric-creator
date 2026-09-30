"""Runtime simulation state for the serve-mode simulator.

This leaf owns the state every serve surface reads: ``SimulationState`` and
its ``build_state()`` constructor, the ``SimulationClock``, the
``ContinuousGenerationStatus`` and ``RefusalCounters`` records,
``load_anomaly_rows()``, and the operator error sink
(``_record_server_error`` and its traceback helpers) including the
continuous-generation failure recorder. ``server_ops`` re-imports every name
at its original position, so the historic ``server_ops.<name>`` and
``server.<name>`` surfaces stay stable, and the leaves that name
``SimulationState`` only in annotations keep their type-only import from
``server_ops``. This module never imports ``server_ops``.
"""

from __future__ import annotations

import csv
import datetime as _dt
import sys
import threading
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from .server_mutations import DEFAULT_NAMESPACE, SimulationMutations, _format_dt, load_persisted_mutations
from .server_ops_profiles import OPS_SCENARIO_PROFILES, validate_ops_profiles
from .server_ops_support import _parse_optional_timestamp, _parse_user_timestamp
from .server_traces import DEFAULT_TRACE_LIMIT, CommandTraceStore

if TYPE_CHECKING:
    from .server_ops_profiles import OpsScenarioProfile


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
    persist_mutations: Path | None = None,
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
        mutations=(
            load_persisted_mutations(
                persist_mutations,
                known_components=frozenset(components),
                extra_event_limit=trace_limit,
            )
            if persist_mutations is not None
            else SimulationMutations(extra_event_limit=trace_limit)
        ),
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
