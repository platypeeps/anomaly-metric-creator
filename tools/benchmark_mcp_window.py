#!/usr/bin/env python3
"""Timing evidence for the MCP window-scan + trace-store hot-path fixes.

Covers two audit items:

- A-039: ``get_metric_histogram`` over a narrow window. Before the fix every
  CSV row was ``strptime``-parsed regardless of the window; after it, a
  lexicographic string gate skips out-of-window rows before parsing and the
  wide layout breaks past the window end. This script times a narrow-window
  call against a full-day call so the ratio is visible.
- A-040: ``/v1/state``'s unsupported-group count at growing trace history.
  Before the fix it deserialized the whole non-supported history via
  ``len(unsupported_summary())``; after it, ``unsupported_fingerprint_count()``
  runs a flat ``COUNT(DISTINCT fingerprint)``. This script times that count at
  0 and N synthetic traces so its flatness is visible.

Usage:
    .venv/bin/python tools/benchmark_mcp_window.py [--traces 5000] [--repeat 5]
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from anomaly_metric_creator import legacy as amc  # noqa: E402
from anomaly_metric_creator import server, server_mcp  # noqa: E402
from anomaly_metric_creator.server_traces import CommandTrace, CommandTraceStore  # noqa: E402

_COMPONENTS = "apigateway,cacheservice,database,authservice"


def _epoch_ms(moment: dt.datetime) -> int:
    return int(moment.replace(tzinfo=dt.timezone.utc).timestamp() * 1000)


def _build_state(out_dir: Path):
    argv = [
        "--duration-days", "1",
        "--seed", "42",
        "--components", _COMPONENTS,
        "--output-dir", str(out_dir),
        "--interval-seconds", "1",  # ~86_400 rows/component: a real scan
    ]
    amc.main(argv)
    return server.build_state(amc, amc.parse_args(argv))


def _time(fn, repeat: int) -> float:
    best = float("inf")
    for _ in range(repeat):
        start = time.perf_counter()
        fn()
        best = min(best, time.perf_counter() - start)
    return best


def _bench_histogram(repeat: int) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        state = _build_state(Path(tmp))
        component = "apigateway"
        metric = amc.COMPONENTS[component][0].name
        start = amc.START

        def call(from_ms, to_ms):
            return lambda: server_mcp._tool_get_metric_histogram(
                state,
                {"component": component, "metric": metric,
                 "from_ms": from_ms, "to_ms": to_ms},
            )

        full = _time(
            call(_epoch_ms(start), _epoch_ms(start + dt.timedelta(days=1))),
            repeat,
        )
        narrow = _time(
            call(_epoch_ms(start + dt.timedelta(hours=1)),
                 _epoch_ms(start + dt.timedelta(hours=1, minutes=5))),
            repeat,
        )
        print("A-039 get_metric_histogram (best of %d):" % repeat)
        print(f"  full-day window   : {full * 1000:8.2f} ms")
        print(f"  5-minute window   : {narrow * 1000:8.2f} ms")
        print(f"  speedup           : {full / narrow:8.1f}x")


def _trace(tid: int, status: str, fingerprint: str) -> CommandTrace:
    ts = "2026-06-25T12:00:00Z"
    return CommandTrace(
        id=tid, received_at_wall_time=ts, simulated_time=ts,
        raw_input=f"kubectl weird {tid}", argv=("kubectl", "weird", str(tid)),
        client="c", command_family="kubectl", verb="weird",
        resource_kind="", resource_name="", namespace="ns",
        parsed_flags={}, support_status=status, matched_rule_id="",
        active_scenarios=(), exit_code=1, stdout_preview="",
        stderr_preview="", stdout="", stderr="", latency_ms=1.0,
        fingerprint=fingerprint, guessed_intent="intent",
    )


def _bench_state_count(n_traces: int, repeat: int) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "commands.sqlite"
        store = CommandTraceStore(sqlite_path=db_path, limit=n_traces + 10)

        empty = _time(store.unsupported_fingerprint_count, repeat)
        for tid in range(1, n_traces + 1):
            store.record(_trace(tid, "unsupported", f"fp.{tid % 32}"))
        full = _time(store.unsupported_fingerprint_count, repeat)
        # First (uncached) full-summary build, then a memoized repeat: the
        # debug-UI /v1/debug/unsupported poll pays the first cost only when
        # the trace head changes.
        summary_cold = _time(lambda: len(store.unsupported_summary()), 1)
        summary_warm = _time(lambda: len(store.unsupported_summary()), repeat)
        store.close()

        print("A-040 /v1/state unsupported count (best of %d):" % repeat)
        print(f"  0 traces (COUNT DISTINCT)         : {empty * 1000:8.3f} ms")
        print(f"  {n_traces} traces (COUNT DISTINCT)    : {full * 1000:8.3f} ms")
        print(f"  {n_traces} traces full summary (cold): {summary_cold * 1000:8.3f} ms")
        print(f"  {n_traces} traces full summary (memo): {summary_warm * 1000:8.3f} ms")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--traces", type=int, default=5000)
    parser.add_argument("--repeat", type=int, default=5)
    args = parser.parse_args()
    _bench_histogram(args.repeat)
    print()
    _bench_state_count(args.traces, args.repeat)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
