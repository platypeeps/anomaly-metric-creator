"""Command trace storage, persistence, and search for server mode."""

from __future__ import annotations

import json
import sqlite3
import threading
from collections import Counter, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_TRACE_LIMIT = 500
COMMAND_TRACE_DB_SCHEMA_VERSION = 2
COMMAND_TRACE_EXPORT_VERSION = 1


def _trace_tuple_field(payload: dict[str, Any], key: str) -> tuple[Any, ...]:
    value = payload.get(key, ())
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{key} must be a list or tuple")
    return tuple(value)


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

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "CommandTrace":
        return cls(
            id=int(payload["id"]),
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
            exit_code=int(payload["exit_code"]),
            stdout_preview=payload.get("stdout_preview", ""),
            stderr_preview=payload.get("stderr_preview", ""),
            stdout=payload.get("stdout", ""),
            stderr=payload.get("stderr", ""),
            latency_ms=float(payload.get("latency_ms", 0.0)),
            fingerprint=payload.get("fingerprint", ""),
            guessed_intent=payload.get("guessed_intent", ""),
        )

    def to_dict(self) -> dict[str, Any]:
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
        }


class CommandTraceStore:
    """Thread-safe ring buffer plus optional JSONL/SQLite persistence."""

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
        self._lock = threading.Lock()
        self._sqlite_write_lock = threading.Lock()
        if persist_path is not None:
            persist_path.parent.mkdir(parents=True, exist_ok=True)
        if sqlite_path is not None:
            sqlite_path.parent.mkdir(parents=True, exist_ok=True)
            self._init_sqlite()
            self._load_sqlite_tail()

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
            persist_path = self._persist_path
            sqlite_path = self._sqlite_path
            if persist_path is not None:
                with open(persist_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(trace.to_dict(), sort_keys=True) + "\n")
        if sqlite_path is not None:
            with self._sqlite_write_lock:
                self._insert_sqlite(trace)

    def list(self, limit: int | None = None) -> list[dict[str, Any]]:
        if self._sqlite_path is not None:
            return self._list_sqlite(limit=limit)
        with self._lock:
            items = list(self._items)
            version = self._version
        if limit is not None:
            items = items[-limit:]
        return [
            {"version": version, **trace.to_dict()}
            for trace in reversed(items)
        ]

    def get(self, trace_id: int) -> dict[str, Any] | None:
        if self._sqlite_path is not None:
            return self._get_sqlite(trace_id)
        with self._lock:
            for trace in self._items:
                if trace.id == trace_id:
                    return trace.to_dict()
        return None

    def count(self) -> int:
        if self._sqlite_path is not None:
            with self._connect() as conn:
                row = conn.execute("SELECT count(*) FROM command_traces").fetchone()
            return int(row[0])
        with self._lock:
            return len(self._items)

    def unsupported_summary(self) -> list[dict[str, Any]]:
        if self._sqlite_path is not None:
            traces = [
                CommandTrace.from_dict(item)
                for item in self._list_sqlite(status_not="supported", limit=None)
            ]
            return _unsupported_summary_from_traces(traces)
        with self._lock:
            unsupported = [
                trace for trace in self._items
                if trace.support_status != "supported"
            ]
        return _unsupported_summary_from_traces(unsupported)

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
            with self._sqlite_write_lock:
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

    def _export_memory_traces(self) -> list[dict[str, Any]]:
        with self._lock:
            return [trace.to_dict() for trace in self._items]

    def _export_sqlite_traces(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT payload_json FROM command_traces ORDER BY id ASC"
            ).fetchall()
        return [self._row_to_payload(row) for row in rows]

    def _connect(self) -> sqlite3.Connection:
        if self._sqlite_path is None:
            raise RuntimeError("sqlite persistence is not configured")
        conn = sqlite3.connect(self._sqlite_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_sqlite(self) -> None:
        with self._connect() as conn:
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
        with self._connect() as conn:
            self._enforce_sqlite_retention(conn)
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

    def _insert_sqlite(self, trace: CommandTrace) -> None:
        payload = trace.to_dict()
        with self._connect() as conn:
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
            self._enforce_sqlite_retention(conn)

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

    def _row_to_payload(self, row: sqlite3.Row) -> dict[str, Any]:
        return json.loads(row["payload_json"])

    def _list_sqlite(
        self,
        *,
        limit: int | None = None,
        status_not: str = "",
    ) -> list[dict[str, Any]]:
        params: list[Any] = []
        where = ""
        if status_not:
            where = "WHERE support_status != ?"
            params.append(status_not)
        sql = f"SELECT payload_json FROM command_traces {where} ORDER BY id DESC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        with self._lock:
            version = self._version
        return [{"version": version, **self._row_to_payload(row)} for row in rows]

    def _get_sqlite(self, trace_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
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
            with self._connect() as conn:
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
        with self._connect() as conn:
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
        with self._connect() as conn:
            conn.execute("DELETE FROM command_traces")
            if self._sqlite_fts_enabled:
                conn.execute("DELETE FROM command_traces_fts")
            for trace in traces:
                payload = trace.to_dict()
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
            self._enforce_sqlite_retention(conn)
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
