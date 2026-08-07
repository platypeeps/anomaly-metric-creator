"""Command trace storage, persistence, and search for server mode."""

from __future__ import annotations

import contextlib
import json
import sqlite3
import threading
from collections import Counter, deque
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NotRequired, TypedDict, cast


DEFAULT_TRACE_LIMIT = 500
COMMAND_TRACE_DB_SCHEMA_VERSION = 2
COMMAND_TRACE_EXPORT_VERSION = 1


class TracePayload(TypedDict):
    """One serialized :class:`CommandTrace`, as ``to_dict`` emits it.

    Member types are the JSON shapes, not the dataclass field types: ``argv``
    and ``active_scenarios`` are tuples on the dataclass and lists here,
    because ``to_dict`` calls ``list()`` on them and a persisted row round-trips
    through JSON.

    The required / ``NotRequired`` split is not a style choice — it mirrors how
    :meth:`CommandTrace.from_dict` reads each key. A key it subscripts is
    required, because a row missing it already raises ``KeyError`` today. A key
    it defaults is ``NotRequired``, because the store persists whole payloads
    and a row written by an older build legitimately omits it.
    """

    # Required (13): ``from_dict`` reaches these through ``payload[key]``,
    # directly or via ``_trace_int_field``.
    id: int
    received_at_wall_time: str
    simulated_time: str
    raw_input: str
    client: str
    command_family: str
    verb: str
    resource_kind: str
    resource_name: str
    namespace: str
    support_status: str
    matched_rule_id: str
    exit_code: int
    # NotRequired (11): ``from_dict`` defaults these, via ``payload.get`` or
    # ``_trace_tuple_field``.
    argv: NotRequired[list[str]]
    active_scenarios: NotRequired[list[str]]
    parsed_flags: NotRequired[dict[str, Any]]
    stdout_preview: NotRequired[str]
    stderr_preview: NotRequired[str]
    stdout: NotRequired[str]
    stderr: NotRequired[str]
    latency_ms: NotRequired[float]
    fingerprint: NotRequired[str]
    guessed_intent: NotRequired[str]
    request_id: NotRequired[str]


class TraceListItem(TracePayload):
    """A listing row: a payload plus the store version it was read at.

    Inheriting means ``{"version": v, **payload}`` checks as a
    ``TraceListItem`` by structure, with no cast at the construction sites.
    """

    version: int


def _trace_tuple_field(payload: dict[str, Any], key: str) -> tuple[Any, ...]:
    value = payload.get(key, ())
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{key} must be a list or tuple")
    return tuple(value)


def _trace_int_field(payload: dict[str, Any], key: str) -> int:
    value = payload[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{key} must be an integer")
    return int(value)


@dataclass(frozen=True)
class CommandTrace:
    id: int
    received_at_wall_time: str
    simulated_time: str
    raw_input: str
    argv: tuple[str, ...]
    client: str
    command_family: str
    verb: str
    resource_kind: str
    resource_name: str
    namespace: str
    parsed_flags: dict[str, Any]
    support_status: str
    matched_rule_id: str
    active_scenarios: tuple[str, ...]
    exit_code: int
    stdout_preview: str
    stderr_preview: str
    stdout: str
    stderr: str
    latency_ms: float
    fingerprint: str
    guessed_intent: str
    # Per-request join key (A-077): the ``uuid4().hex[:12]`` the HTTP handler
    # mints once per request, so a trace can be joined to the structured
    # request/error record for the same request. Payload-only — carried in
    # ``to_dict`` / ``from_dict`` (JSONL, export, live API), with no dedicated
    # SQLite column, so there is no schema migration. It still survives a SQLite
    # restart because the store persists the whole ``to_dict`` blob in
    # ``payload_json`` and reloads via ``from_dict``. Defaulted so non-HTTP
    # callers (tests, MCP) and any older payload without the key construct a
    # trace unchanged.
    request_id: str = ""

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "CommandTrace":
        return cls(
            id=_trace_int_field(payload, "id"),
            received_at_wall_time=payload["received_at_wall_time"],
            simulated_time=payload["simulated_time"],
            raw_input=payload["raw_input"],
            argv=_trace_tuple_field(payload, "argv"),
            client=payload["client"],
            command_family=payload["command_family"],
            verb=payload["verb"],
            resource_kind=payload["resource_kind"],
            resource_name=payload["resource_name"],
            namespace=payload["namespace"],
            parsed_flags=dict(payload.get("parsed_flags", {})),
            support_status=payload["support_status"],
            matched_rule_id=payload["matched_rule_id"],
            active_scenarios=_trace_tuple_field(payload, "active_scenarios"),
            exit_code=_trace_int_field(payload, "exit_code"),
            stdout_preview=payload.get("stdout_preview", ""),
            stderr_preview=payload.get("stderr_preview", ""),
            stdout=payload.get("stdout", ""),
            stderr=payload.get("stderr", ""),
            latency_ms=float(payload.get("latency_ms", 0.0)),
            fingerprint=payload.get("fingerprint", ""),
            guessed_intent=payload.get("guessed_intent", ""),
            request_id=payload.get("request_id", ""),
        )

    def to_dict(self) -> TracePayload:
        return {
            "id": self.id,
            "received_at_wall_time": self.received_at_wall_time,
            "simulated_time": self.simulated_time,
            "raw_input": self.raw_input,
            "argv": list(self.argv),
            "client": self.client,
            "command_family": self.command_family,
            "verb": self.verb,
            "resource_kind": self.resource_kind,
            "resource_name": self.resource_name,
            "namespace": self.namespace,
            "parsed_flags": self.parsed_flags,
            "support_status": self.support_status,
            "matched_rule_id": self.matched_rule_id,
            "active_scenarios": list(self.active_scenarios),
            "exit_code": self.exit_code,
            "stdout_preview": self.stdout_preview,
            "stderr_preview": self.stderr_preview,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "latency_ms": self.latency_ms,
            "fingerprint": self.fingerprint,
            "guessed_intent": self.guessed_intent,
            "request_id": self.request_id,
        }


class CommandTraceStore:
    """Thread-safe ring buffer plus optional JSONL/SQLite persistence.

    Concurrency / resource discipline (server is a ThreadingHTTPServer):

    - ``_lock`` guards the in-memory ring (``_items`` / ``_version`` /
      ``_next_id`` / ``_summary_cache``).
    - ``_sqlite_lock`` guards the single long-lived sqlite connection
      (``_conn``, opened once with ``check_same_thread=False``). The
      connection is **never** touched outside :meth:`_locked_conn`; a bare
      sqlite3 connection is not safe for concurrent use, so every read and
      write serializes through that one lock. Opening one connection for
      the store's lifetime (instead of ``sqlite3.connect`` per operation)
      is the A-041 hot-path fix.
    - ``_jsonl_lock`` guards the long-lived JSONL append handle
      (``_jsonl_handle``, opened once and flushed per write) so JSONL
      persistence stays off the ring lock. External rotation/deletion of
      the JSONL file requires a server restart to re-open the handle.
    """

    def __init__(
        self,
        limit: int = DEFAULT_TRACE_LIMIT,
        persist_path: Path | None = None,
        sqlite_path: Path | None = None,
        sqlite_retention: int | None = None,
    ):
        self._limit = limit
        self._persist_path = persist_path
        self._sqlite_path = sqlite_path
        self._sqlite_retention = max(0, sqlite_retention or 0)
        self._sqlite_fts_enabled = False
        self._items: deque[CommandTrace] = deque(maxlen=limit)
        self._next_id = 1
        self._version = 0
        # Memoized unsupported-summary keyed on ``_version`` so repeated
        # debug-UI polls at an unchanged trace head are O(1) instead of
        # re-deserializing the whole non-supported history (A-040).
        self._summary_cache: tuple[int, list[dict[str, Any]]] | None = None
        # Monotonic sqlite mutation generation; bumped under ``_sqlite_lock``
        # whenever persisted rows change, so it keys ``_summary_cache`` for
        # the sqlite path without the pre-commit ``_version`` skew.
        self._sqlite_gen = 0
        self._lock = threading.Lock()
        self._sqlite_lock = threading.Lock()
        self._jsonl_lock = threading.Lock()
        self._jsonl_handle = None
        self._conn: sqlite3.Connection | None = None
        if persist_path is not None:
            persist_path.parent.mkdir(parents=True, exist_ok=True)
            self._jsonl_handle = open(persist_path, "a", encoding="utf-8")
        if sqlite_path is not None:
            sqlite_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(
                sqlite_path, timeout=5.0, check_same_thread=False
            )
            self._conn.row_factory = sqlite3.Row
            self._init_sqlite()
            self._load_sqlite_tail()

    def close(self) -> None:
        """Release the long-lived JSONL handle and sqlite connection.

        Best-effort and idempotent; the store is normally process-lived so
        the OS reclaims these at exit, but tests and explicit teardown can
        call this to avoid dangling handles.
        """
        with self._jsonl_lock:
            if self._jsonl_handle is not None:
                self._jsonl_handle.close()
                self._jsonl_handle = None
        with self._sqlite_lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    @property
    def version(self) -> int:
        with self._lock:
            return self._version

    def next_id(self) -> int:
        with self._lock:
            value = self._next_id
            self._next_id += 1
            return value

    def record(self, trace: CommandTrace) -> None:
        with self._lock:
            self._items.append(trace)
            self._version += 1
        # JSONL and sqlite persistence run outside the ring lock; each has
        # its own lock, so a slow disk cannot stall in-memory readers.
        if self._jsonl_handle is not None:
            self._append_jsonl(trace)
        if self._sqlite_path is not None:
            self._insert_sqlite(trace)

    def _append_jsonl(self, trace: CommandTrace) -> None:
        line = json.dumps(trace.to_dict(), sort_keys=True) + "\n"
        with self._jsonl_lock:
            handle = self._jsonl_handle
            if handle is None:
                return
            handle.write(line)
            handle.flush()

    @contextlib.contextmanager
    def _locked_conn(self) -> Iterator[sqlite3.Connection]:
        """Yield the long-lived sqlite connection under ``_sqlite_lock``.

        The connection is opened once (``check_same_thread=False``) and is
        never used outside this guard, since a single sqlite3 connection is
        not safe for concurrent use across the server's worker threads.
        Commits on clean exit; rolls back and re-raises on error so a
        failed statement never strands a half-open transaction on the
        shared connection.
        """
        with self._sqlite_lock:
            # Check and bind under the lock: a concurrent close() also holds
            # _sqlite_lock while nulling _conn, so reading it outside the lock
            # could yield None (or a just-closed connection) if close() lands
            # in the gap. Bind a local so commit/rollback cannot hit None.
            conn = self._conn
            if conn is None:
                raise RuntimeError("sqlite persistence is not configured")
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def list_traces(self, limit: int | None = None) -> list[TraceListItem]:
        # Clamp a negative limit to 0 so both backends agree (audit A-017):
        # the memory path's ``items[-limit:]`` would otherwise slice off the
        # front for a negative value, and SQLite's ``LIMIT -1`` means "no
        # limit" — opposite behaviors from the same caller-supplied value.
        # ``limit == 0`` returns an empty list on both paths.
        if limit is not None:
            limit = max(0, limit)
        if self._sqlite_path is not None:
            return self._list_sqlite(limit=limit)
        with self._lock:
            items = list(self._items)
            version = self._version
        if limit is not None:
            items = items[-limit:] if limit else []
        return [
            {"version": version, **trace.to_dict()}
            for trace in reversed(items)
        ]

    def get(self, trace_id: int) -> TracePayload | None:
        if self._sqlite_path is not None:
            return self._get_sqlite(trace_id)
        with self._lock:
            for trace in self._items:
                if trace.id == trace_id:
                    return trace.to_dict()
        return None

    def count(self) -> int:
        if self._sqlite_path is not None:
            with self._locked_conn() as conn:
                row = conn.execute("SELECT count(*) FROM command_traces").fetchone()
            return int(row[0])
        with self._lock:
            return len(self._items)

    def unsupported_fingerprint_count(self) -> int:
        """Distinct-fingerprint count of non-supported traces.

        The ``/v1/state`` poll only needs this scalar. Computing it with a
        SQL ``COUNT(DISTINCT fingerprint)`` (or a memory-side set) keeps the
        cost flat as the trace history grows, instead of deserializing the
        whole non-supported history via :meth:`unsupported_summary` just to
        take ``len(...)``. Equal by construction to
        ``len(self.unsupported_summary())`` (the summary groups by
        fingerprint over exactly the non-supported traces).
        """
        if self._sqlite_path is not None:
            with self._locked_conn() as conn:
                row = conn.execute(
                    "SELECT COUNT(DISTINCT fingerprint) FROM command_traces "
                    "WHERE support_status != 'supported'"
                ).fetchone()
            return int(row[0])
        with self._lock:
            return len({
                trace.fingerprint
                for trace in self._items
                if trace.support_status != "supported"
            })

    def unsupported_summary(self) -> list[dict[str, Any]]:
        """Fingerprint-grouped summary of non-supported traces.

        Byte-identical to recomputing from scratch, but memoized so the
        debug UI's repeated ``/v1/debug/unsupported`` polls at an unchanged
        trace head are O(1) instead of re-deserializing the whole
        non-supported history each tick (A-040).

        Cache correctness under the ThreadingHTTPServer: the sqlite path
        keys on ``_sqlite_gen`` (bumped under ``_sqlite_lock`` on every
        mutation) and reads the generation and the rows in the *same*
        locked section, so the cached ``(gen, summary)`` pair is always
        internally consistent — the in-memory ``_version`` bumps *before*
        the sqlite commit and would allow a one-tick-stale cache, so it is
        deliberately not used as the sqlite key. The memory path keys on
        ``_version``, read atomically with the ring snapshot under
        ``_lock``.
        """
        if self._sqlite_path is not None:
            with self._locked_conn() as conn:
                gen = self._sqlite_gen
                cached = self._summary_cache
                if cached is not None and cached[0] == gen:
                    return cached[1]
                rows = conn.execute(
                    "SELECT payload_json FROM command_traces "
                    "WHERE support_status != 'supported' ORDER BY id DESC"
                ).fetchall()
            # Deserialize + group outside the sqlite lock: rows already
            # correspond to ``gen`` (no mutation can interleave the two
            # reads above), so writers are not blocked by the CPU work.
            traces = [
                CommandTrace.from_dict(json.loads(row["payload_json"]))
                for row in rows
            ]
            summary = _unsupported_summary_from_traces(traces)
            with self._sqlite_lock:
                if self._summary_cache is None or gen >= self._summary_cache[0]:
                    self._summary_cache = (gen, summary)
            return summary
        with self._lock:
            version = self._version
            cached = self._summary_cache
            if cached is not None and cached[0] == version:
                return cached[1]
            unsupported = [
                trace for trace in self._items
                if trace.support_status != "supported"
            ]
        summary = _unsupported_summary_from_traces(unsupported)
        with self._lock:
            if self._summary_cache is None or version >= self._summary_cache[0]:
                self._summary_cache = (version, summary)
        return summary

    def search(
        self,
        *,
        query: str = "",
        support_status: str = "",
        command_family: str = "",
        scenario_id: str = "",
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        limit = max(1, min(limit, 500))
        offset = max(0, offset)
        if self._sqlite_path is not None:
            return self._search_sqlite(
                query=query,
                support_status=support_status,
                command_family=command_family,
                scenario_id=scenario_id,
                limit=limit,
                offset=offset,
            )
        with self._lock:
            traces = list(self._items)
            version = self._version
        filtered = [
            trace for trace in traces
            if _trace_matches_search(
                trace,
                query=query,
                support_status=support_status,
                command_family=command_family,
                scenario_id=scenario_id,
            )
        ]
        total = len(filtered)
        page = list(reversed(filtered))[offset: offset + limit]
        return {
            "items": [{"version": version, **trace.to_dict()} for trace in page],
            "total": total,
            "limit": limit,
            "offset": offset,
            "query": query,
            "support_status": support_status,
            "command_family": command_family,
            "scenario_id": scenario_id,
            "search_backend": "memory",
        }

    def export_payload(self) -> dict[str, Any]:
        traces = self._export_sqlite_traces() if self._sqlite_path is not None else self._export_memory_traces()
        return {
            "kind": "CommandTraceExport",
            "apiVersion": f"amc.simulator/v{COMMAND_TRACE_EXPORT_VERSION}",
            "schema_version": COMMAND_TRACE_EXPORT_VERSION,
            "trace_count": len(traces),
            "traces": traces,
        }

    def import_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        traces_payload = payload.get("traces")
        if not isinstance(traces_payload, list):
            raise ValueError("trace import payload must include a traces list")
        traces = []
        for index, item in enumerate(traces_payload):
            if not isinstance(item, dict):
                raise ValueError(f"trace import entry {index} must be an object")
            try:
                traces.append(CommandTrace.from_dict(item))
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"trace import entry {index} is invalid: {exc}") from exc
        if self._sqlite_path is not None:
            previous_version = self.version
            self._replace_sqlite_traces(traces)
            self._ensure_import_version_change(previous_version)
        else:
            with self._lock:
                previous_version = self._version
                self._items.clear()
                self._items.extend(traces[-self._limit:])
                self._next_id = max((trace.id for trace in traces), default=0) + 1
                self._version = max(previous_version + 1, len(traces))
        return {
            "imported": len(traces),
            "trace_count": self.count(),
            "next_id": self._next_id_snapshot(),
        }

    def _ensure_import_version_change(self, previous_version: int) -> None:
        with self._lock:
            if self._version <= previous_version:
                self._version = previous_version + 1

    def _next_id_snapshot(self) -> int:
        with self._lock:
            return self._next_id

    def _export_memory_traces(self) -> list[TracePayload]:
        with self._lock:
            return [trace.to_dict() for trace in self._items]

    def _export_sqlite_traces(self) -> list[TracePayload]:
        with self._locked_conn() as conn:
            rows = conn.execute(
                "SELECT payload_json FROM command_traces ORDER BY id ASC"
            ).fetchall()
        return [self._row_to_payload(row) for row in rows]

    def _init_sqlite(self) -> None:
        with self._locked_conn() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS command_trace_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS command_traces (
                    id INTEGER PRIMARY KEY,
                    received_at_wall_time TEXT NOT NULL,
                    simulated_time TEXT NOT NULL,
                    raw_input TEXT NOT NULL,
                    command_family TEXT NOT NULL,
                    verb TEXT NOT NULL,
                    resource_kind TEXT NOT NULL,
                    resource_name TEXT NOT NULL,
                    namespace TEXT NOT NULL,
                    support_status TEXT NOT NULL,
                    matched_rule_id TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    guessed_intent TEXT NOT NULL,
                    active_scenarios_json TEXT NOT NULL,
                    exit_code INTEGER NOT NULL,
                    stdout_preview TEXT NOT NULL,
                    stderr_preview TEXT NOT NULL,
                    stdout TEXT NOT NULL,
                    stderr TEXT NOT NULL,
                    latency_ms REAL NOT NULL,
                    payload_json TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_command_traces_status "
                "ON command_traces(support_status, id DESC)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_command_traces_family "
                "ON command_traces(command_family, id DESC)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_command_traces_fingerprint "
                "ON command_traces(fingerprint, id DESC)"
            )
            self._sqlite_fts_enabled = self._ensure_sqlite_fts(conn)
            self._set_sqlite_meta(conn, "schema_version", str(COMMAND_TRACE_DB_SCHEMA_VERSION))
            self._set_sqlite_meta(conn, "fts5_enabled", "1" if self._sqlite_fts_enabled else "0")

    def _set_sqlite_meta(self, conn: sqlite3.Connection, key: str, value: str) -> None:
        conn.execute(
            "INSERT OR REPLACE INTO command_trace_meta(key, value) VALUES (?, ?)",
            (key, value),
        )

    def _ensure_sqlite_fts(self, conn: sqlite3.Connection) -> bool:
        try:
            conn.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS command_traces_fts USING fts5(
                    trace_id UNINDEXED,
                    raw_input,
                    stdout,
                    stderr,
                    fingerprint,
                    guessed_intent,
                    matched_rule_id
                )
                """
            )
        except sqlite3.OperationalError:
            return False
        count_row = conn.execute("SELECT count(*) AS total FROM command_traces_fts").fetchone()
        trace_count = conn.execute("SELECT count(*) AS total FROM command_traces").fetchone()
        if int(count_row["total"]) != int(trace_count["total"]):
            conn.execute("DELETE FROM command_traces_fts")
            conn.execute(
                """
                INSERT INTO command_traces_fts(
                    trace_id, raw_input, stdout, stderr, fingerprint,
                    guessed_intent, matched_rule_id
                )
                SELECT id, raw_input, stdout, stderr, fingerprint,
                    guessed_intent, matched_rule_id
                FROM command_traces
                ORDER BY id ASC
                """
            )
        return True

    def _load_sqlite_tail(self) -> None:
        with self._locked_conn() as conn:
            self._enforce_sqlite_retention(conn)
            self._sqlite_gen += 1
            rows = conn.execute(
                "SELECT id, payload_json FROM command_traces ORDER BY id DESC LIMIT ?",
                (self._limit,),
            ).fetchall()
            max_row = conn.execute("SELECT max(id) AS max_id FROM command_traces").fetchone()
            count_row = conn.execute("SELECT count(*) AS total FROM command_traces").fetchone()
        traces = [
            CommandTrace.from_dict(json.loads(row["payload_json"]))
            for row in reversed(rows)
        ]
        with self._lock:
            self._items.clear()
            self._items.extend(traces)
            self._version = int(count_row["total"])
            max_id = int(max_row["max_id"] or 0)
            self._next_id = max_id + 1

    def _insert_trace_row(
        self,
        conn: sqlite3.Connection,
        trace: CommandTrace,
        payload: TracePayload,
        *,
        delete_fts_first: bool,
    ) -> None:
        """Write one trace to ``command_traces`` and its FTS mirror.

        ``payload`` is a parameter, not a ``trace.to_dict()`` call here, so
        ``_insert_sqlite`` keeps serializing outside its ``_locked_conn()``.
        ``delete_fts_first`` is unnecessary for ``_replace_sqlite_traces``
        only because that path bulk-clears the FTS table before its loop.
        Both contracts are pinned by tests; see the trace-persistence section
        of ``.trellis/spec/amc/backend/operations-security-logging.md``.

        """
        conn.execute(
            """
            INSERT OR REPLACE INTO command_traces (
                id, received_at_wall_time, simulated_time, raw_input,
                command_family, verb, resource_kind, resource_name,
                namespace, support_status, matched_rule_id, fingerprint,
                guessed_intent, active_scenarios_json, exit_code,
                stdout_preview, stderr_preview, stdout, stderr,
                latency_ms, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trace.id,
                trace.received_at_wall_time,
                trace.simulated_time,
                trace.raw_input,
                trace.command_family,
                trace.verb,
                trace.resource_kind,
                trace.resource_name,
                trace.namespace,
                trace.support_status,
                trace.matched_rule_id,
                trace.fingerprint,
                trace.guessed_intent,
                # ``list(...)`` normalizes the tuple/list split at the CSV and
                # bundle-import boundaries so the stored JSON is the same array
                # either way. Declaration order is preserved, not sorted --
                # ``sort_keys`` only affects objects.
                json.dumps(list(trace.active_scenarios), sort_keys=True),
                trace.exit_code,
                trace.stdout_preview,
                trace.stderr_preview,
                trace.stdout,
                trace.stderr,
                trace.latency_ms,
                json.dumps(payload, sort_keys=True),
            ),
        )
        if self._sqlite_fts_enabled:
            if delete_fts_first:
                conn.execute(
                    "DELETE FROM command_traces_fts WHERE trace_id = ?",
                    (trace.id,),
                )
            conn.execute(
                """
                INSERT INTO command_traces_fts(
                    trace_id, raw_input, stdout, stderr, fingerprint,
                    guessed_intent, matched_rule_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trace.id,
                    trace.raw_input,
                    trace.stdout,
                    trace.stderr,
                    trace.fingerprint,
                    trace.guessed_intent,
                    trace.matched_rule_id,
                ),
            )

    def _insert_sqlite(self, trace: CommandTrace) -> None:
        payload = trace.to_dict()
        with self._locked_conn() as conn:
            self._insert_trace_row(conn, trace, payload, delete_fts_first=True)
            self._enforce_sqlite_retention(conn)
            self._sqlite_gen += 1

    def _enforce_sqlite_retention(self, conn: sqlite3.Connection) -> None:
        if not self._sqlite_retention:
            return
        cutoff_row = conn.execute(
            """
            SELECT min(id) AS cutoff
            FROM (
                SELECT id FROM command_traces ORDER BY id DESC LIMIT ?
            )
            """,
            (self._sqlite_retention,),
        ).fetchone()
        cutoff = cutoff_row["cutoff"]
        if cutoff is None:
            return
        conn.execute("DELETE FROM command_traces WHERE id < ?", (cutoff,))
        if self._sqlite_fts_enabled:
            conn.execute("DELETE FROM command_traces_fts WHERE trace_id < ?", (cutoff,))

    def _row_to_payload(self, row: sqlite3.Row) -> TracePayload:
        """Decode one ``payload_json`` cell into the payload shape.

        This guard covers the read paths that route through here — listing,
        ``get``, export, and search. ``unsupported_summary`` and
        ``_load_sqlite_tail`` decode ``payload_json`` themselves and hand the
        result straight to :meth:`CommandTrace.from_dict`, which subscripts it
        and so rejects a non-object row on its own terms. Widening one guard
        over all of them belongs to trace-export hardening, not here.
        """
        payload = json.loads(row["payload_json"])
        if not isinstance(payload, dict):
            # The error names no row id on purpose: every query feeding this
            # helper selects ``payload_json`` alone, so ``row["id"]`` would
            # itself raise here.
            raise TypeError(
                f"command_traces.payload_json decoded to "
                f"{type(payload).__name__}, expected a JSON object"
            )
        # Cast rather than validate per field: unlike ``--instance-config``
        # and ``schema.json``, this row is machine-written by this same store
        # and only ever *older* than the current shape. The ``NotRequired``
        # keys on ``TracePayload`` are exactly the ones an older row may omit,
        # and ``from_dict`` already defaults them.
        return cast(TracePayload, payload)

    def _list_sqlite(
        self,
        *,
        limit: int | None = None,
        status_not: str = "",
    ) -> list[TraceListItem]:
        params: list[Any] = []
        where = ""
        if status_not:
            where = "WHERE support_status != ?"
            params.append(status_not)
        sql = f"SELECT payload_json FROM command_traces {where} ORDER BY id DESC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        with self._locked_conn() as conn:
            rows = conn.execute(sql, params).fetchall()
        with self._lock:
            version = self._version
        return [{"version": version, **self._row_to_payload(row)} for row in rows]

    def _get_sqlite(self, trace_id: int) -> TracePayload | None:
        with self._locked_conn() as conn:
            row = conn.execute(
                "SELECT payload_json FROM command_traces WHERE id = ?",
                (trace_id,),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_payload(row)

    def _search_sqlite(
        self,
        *,
        query: str,
        support_status: str,
        command_family: str,
        scenario_id: str,
        limit: int,
        offset: int,
    ) -> dict[str, Any]:
        where: list[str] = []
        params: list[Any] = []
        search_backend = "like"
        if query:
            if self._sqlite_fts_enabled:
                search_backend = "fts5"
                where.append(
                    "id IN ("
                    "SELECT trace_id FROM command_traces_fts "
                    "WHERE command_traces_fts MATCH ?"
                    ")"
                )
                params.append(_sqlite_fts_query(query))
            else:
                self._append_sqlite_like_search(where, params, query)
        if support_status:
            where.append("support_status = ?")
            params.append(support_status)
        if command_family:
            where.append("command_family = ?")
            params.append(command_family)
        if scenario_id:
            where.append("active_scenarios_json LIKE ? ESCAPE '\\'")
            params.append(_sqlite_json_string_like_pattern(scenario_id))
        where_sql = "WHERE " + " AND ".join(where) if where else ""
        try:
            with self._locked_conn() as conn:
                total_row = conn.execute(
                    f"SELECT count(*) AS total FROM command_traces {where_sql}",
                    params,
                ).fetchone()
                rows = conn.execute(
                    f"SELECT payload_json FROM command_traces {where_sql} "
                    "ORDER BY id DESC LIMIT ? OFFSET ?",
                    [*params, limit, offset],
                ).fetchall()
        except sqlite3.OperationalError:
            if not query or search_backend != "fts5":
                raise
            return self._search_sqlite_like_fallback(
                query=query,
                support_status=support_status,
                command_family=command_family,
                scenario_id=scenario_id,
                limit=limit,
                offset=offset,
            )
        with self._lock:
            version = self._version
        return {
            "items": [{"version": version, **self._row_to_payload(row)} for row in rows],
            "total": int(total_row["total"]),
            "limit": limit,
            "offset": offset,
            "query": query,
            "support_status": support_status,
            "command_family": command_family,
            "scenario_id": scenario_id,
            "search_backend": search_backend if query else "sqlite",
        }

    def _search_sqlite_like_fallback(
        self,
        *,
        query: str,
        support_status: str,
        command_family: str,
        scenario_id: str,
        limit: int,
        offset: int,
    ) -> dict[str, Any]:
        where: list[str] = []
        params: list[Any] = []
        self._append_sqlite_like_search(where, params, query)
        if support_status:
            where.append("support_status = ?")
            params.append(support_status)
        if command_family:
            where.append("command_family = ?")
            params.append(command_family)
        if scenario_id:
            where.append("active_scenarios_json LIKE ? ESCAPE '\\'")
            params.append(_sqlite_json_string_like_pattern(scenario_id))
        where_sql = "WHERE " + " AND ".join(where)
        with self._locked_conn() as conn:
            total_row = conn.execute(
                f"SELECT count(*) AS total FROM command_traces {where_sql}",
                params,
            ).fetchone()
            rows = conn.execute(
                f"SELECT payload_json FROM command_traces {where_sql} "
                "ORDER BY id DESC LIMIT ? OFFSET ?",
                [*params, limit, offset],
            ).fetchall()
        with self._lock:
            version = self._version
        return {
            "items": [{"version": version, **self._row_to_payload(row)} for row in rows],
            "total": int(total_row["total"]),
            "limit": limit,
            "offset": offset,
            "query": query,
            "support_status": support_status,
            "command_family": command_family,
            "scenario_id": scenario_id,
            "search_backend": "like",
        }

    def _append_sqlite_like_search(
        self,
        where: list[str],
        params: list[Any],
        query: str,
    ) -> None:
        like = f"%{query.lower()}%"
        where.append(
            "("
            "lower(raw_input) LIKE ? OR lower(stdout) LIKE ? OR "
            "lower(stderr) LIKE ? OR lower(fingerprint) LIKE ? OR "
            "lower(guessed_intent) LIKE ? OR lower(matched_rule_id) LIKE ?"
            ")"
        )
        params.extend([like] * 6)

    def _replace_sqlite_traces(self, traces: list[CommandTrace]) -> None:
        with self._locked_conn() as conn:
            conn.execute("DELETE FROM command_traces")
            # Derived from whether the bulk clear ran, not hard-coded False,
            # so the flag cannot go stale on its own. That is defense in
            # depth only: the clear is independently required, because it
            # drops FTS rows for traces *absent* from ``traces``, which no
            # per-row delete can reach -- pinned by
            # ``test_command_trace_sqlite_per_row_fts_delete_cannot_reach_absent_traces``.
            fts_bulk_cleared = False
            if self._sqlite_fts_enabled:
                conn.execute("DELETE FROM command_traces_fts")
                fts_bulk_cleared = True
            for trace in traces:
                payload = trace.to_dict()
                self._insert_trace_row(
                    conn, trace, payload, delete_fts_first=not fts_bulk_cleared
                )
            self._enforce_sqlite_retention(conn)
            self._sqlite_gen += 1
        self._load_sqlite_tail()


def _sqlite_fts_query(query: str) -> str:
    value = " ".join(term.strip() for term in query.split() if term.strip())
    if not value:
        return '""'
    escaped = value.replace('"', '""')
    return f'"{escaped}"'


def _sqlite_json_string_like_pattern(value: str) -> str:
    quoted = json.dumps(str(value))
    escaped = (
        quoted
        .replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )
    return f"%{escaped}%"


def _unsupported_summary_from_traces(traces: list[CommandTrace]) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    for trace in traces:
        group = groups.setdefault(
            trace.fingerprint,
            {
                "fingerprint": trace.fingerprint,
                "count": 0,
                "first_seen": trace.received_at_wall_time,
                "last_seen": trace.received_at_wall_time,
                "examples": [],
                "guessed_intent": trace.guessed_intent,
                "support_statuses": Counter(),
            },
        )
        group["count"] += 1
        group["first_seen"] = min(group["first_seen"], trace.received_at_wall_time)
        group["last_seen"] = max(group["last_seen"], trace.received_at_wall_time)
        group["support_statuses"][trace.support_status] += 1
        if len(group["examples"]) < 5:
            group["examples"].append({
                "id": trace.id,
                "raw_input": trace.raw_input,
                "argv": list(trace.argv),
                "parsed_flags": trace.parsed_flags,
                "scenario_ids": list(trace.active_scenarios),
            })
    result = []
    for group in groups.values():
        group = dict(group)
        group["support_statuses"] = dict(group["support_statuses"])
        result.append(group)
    result.sort(key=lambda item: (-item["count"], item["fingerprint"]))
    return result


def unsupported_summary_from_traces(traces: list[CommandTrace]) -> list[dict[str, Any]]:
    """Group unsupported and partial traces by normalized fingerprint."""
    unsupported = [
        trace for trace in traces
        if trace.support_status != "supported"
    ]
    return _unsupported_summary_from_traces(unsupported)


def _trace_matches_search(
    trace: CommandTrace,
    *,
    query: str,
    support_status: str,
    command_family: str,
    scenario_id: str,
) -> bool:
    if support_status and trace.support_status != support_status:
        return False
    if command_family and trace.command_family != command_family:
        return False
    if scenario_id and scenario_id not in trace.active_scenarios:
        return False
    if not query:
        return True
    haystack = "\n".join([
        trace.raw_input,
        trace.stdout,
        trace.stderr,
        trace.fingerprint,
        trace.guessed_intent,
        trace.matched_rule_id,
    ]).lower()
    return query.lower() in haystack


def trace_matches_search(
    trace: CommandTrace,
    *,
    query: str = "",
    support_status: str = "",
    command_family: str = "",
    scenario_id: str = "",
) -> bool:
    """Return whether a trace matches the server/debug search filters."""
    return _trace_matches_search(
        trace,
        query=query,
        support_status=support_status,
        command_family=command_family,
        scenario_id=scenario_id,
    )
