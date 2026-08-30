"""Timestamp parsing and unix-nano conversion shared across writers.

Extracted verbatim from ``legacy.py`` (decomposition step 2; see
``docs/work/archive/2026-07/2026-07-02-legacy-monolith-decomposition/design.md``).
Shared by the combine/gauge merge writers, the OTLP payload builders,
and ``server_mcp`` (via ``state.legacy._parse_csv_timestamp``), so it
lives in its own leaf module rather than inside any one consumer.
``legacy.py`` re-imports every name; new code should import from here.
"""

from __future__ import annotations

import datetime
import functools


@functools.lru_cache(maxsize=4096)
def _parse_csv_timestamp(timestamp: str) -> datetime.datetime:
    """Parse a ``YYYY-MM-DD HH:MM:SS[.SSS]`` CSV timestamp into a naive datetime.

    The integer-second and millisecond-precision forms emitted by
    ``_build_timestamp_arrays`` are both accepted. Centralizing the format
    dispatch here keeps every consumer (OTLP payload conversion, OTEL stream
    pacing, future readers) in lockstep on the supported formats.

    Cached: every per-(component, instance) source in the ``heapq.merge``
    writers re-parses the same shared timestamp grid (~42 parses per
    unique string on an N=3 14-component run). Merge access is
    window-local — the same timestamp recurs across sources within a
    merge window, then never again — so a small LRU absorbs the repeats
    without holding a 7-day grid (~600k datetimes) in memory. The
    returned ``datetime`` is immutable, so sharing one instance across
    callers is safe.
    """
    fmt = "%Y-%m-%d %H:%M:%S.%f" if "." in timestamp else "%Y-%m-%d %H:%M:%S"
    return datetime.datetime.strptime(timestamp, fmt)


_UNIX_EPOCH_UTC = datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc)


def _dt_to_unix_nanos(dt: datetime.datetime) -> int:
    """Convert a ``datetime`` (naive UTC or tz-aware) to unix-nanoseconds.

    Uses integer arithmetic on ``timedelta`` fields rather than
    ``datetime.timestamp() * 1e9`` so millisecond-precision inputs do not
    accrue floating-point rounding error on the way to a nanosecond integer.
    Naive inputs are interpreted as UTC, matching the convention used by
    ``_parse_csv_timestamp`` consumers.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    delta = dt - _UNIX_EPOCH_UTC
    return (delta.days * 86_400 + delta.seconds) * 1_000_000_000 + delta.microseconds * 1_000


def _to_unix_nanos(timestamp: str) -> int:
    """Convert ``YYYY-MM-DD HH:MM:SS[.SSS]`` timestamp strings to unix-nanoseconds."""
    return _dt_to_unix_nanos(_parse_csv_timestamp(timestamp))

