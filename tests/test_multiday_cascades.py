"""End-to-end coverage for the three multi-day cascading scenarios.

Each scenario activates at its own ``days_required`` — see
``amc.SCENARIOS[<slug>].days_required`` for the current values. The full
primary + cascade sequences span multiple days, so the asserts run against the
shared 7-day fixture. A parallel 1-day run confirms every spec is out of range
under the default duration. A pair of seeded runs locks in deterministic
``anomalies.csv`` output.
"""

from __future__ import annotations

import csv
import datetime
import statistics

from conftest import read_manifest, run_capture

# ------------------------------------------------------------------
# Scenario primaries — (component, metric, description, expected day band)
# Day numbering matches the issue: Day 1 = run start, Day N = N-1 days later.
# ------------------------------------------------------------------
SCENARIO_A_PRIMARIES = [
    ("cacheservice", "memory_util_pct",
     "Cache memory leak — slow growth 50%→95% over 51h", 2),
    ("cacheservice", "cache_misses",
     "Cache eviction cascade — misses ramp 682→3,333 (hit ratio 88%→60%) over 12h", 3),
    ("cacheservice", "memory_util_pct",
     "Cache forced restart — memory reset to 55%", 4),
    ("cacheservice", "cache_misses",
     "Cache cold start after restart — misses ~95,000 (hit ratio ~5%)", 4),
    ("cacheservice", "error_rate",
     "Cache warm-up errors during restart", 4),
]

SCENARIO_B_PRIMARIES = [
    ("loadbalancer", "tls_handshake_errors",
     "TLS cert validation flapping at POPs — errors ramp 2→25/s", 3),
    ("identityprovider", "jwks_fetch_latency_ms",
     "JWKS fetch latency sustained at 800 ms — pre-rotation slowdown", 4),
    ("authservice", "login_success_rate",
     "Login success rate decline 98%→85% as cert chain degrades", 4),
    ("loadbalancer", "tls_handshake_errors",
     "Hard cert expiration — TLS errors spike to 200/s", 5),
    ("identityprovider", "failed_oidc_flows",
     "Cert expiry — OIDC flow failures spike to 800", 5),
    ("identityprovider", "key_rotation_events",
     "Emergency key rotation — 50 events during expiry window", 5),
]

SCENARIO_C_PRIMARIES = [
    ("database", "disk_used_pct",
     "Database disk slow exhaustion 65%→92% over 96h", 2),
    ("database", "write_latency_ms",
     "Database write latency drift 12→90 ms as I/O saturates", 5),
    ("database", "error_rate",
     "Emergency log truncation — write errors spike to 12%", 6),
    ("database", "disk_used_pct",
     "Database log truncation — disk drops to 78%", 6),
    ("database", "write_latency_ms",
     "Database write latency partial relief — 30 ms post-truncation", 6),
]

ALL_PRIMARIES = SCENARIO_A_PRIMARIES + SCENARIO_B_PRIMARIES + SCENARIO_C_PRIMARIES

# ------------------------------------------------------------------
# Scenario cascade descriptions. The cold-start stampede is the only
# spec that fell back to a step (register_cascade lacks shape support);
# every other cascade matches the plan verbatim.
# ------------------------------------------------------------------
SCENARIO_A_CASCADES = [
    "Cascading: Rising cache miss volume — DB queries climb to ~32k",
    "Cascading: Cache hit-ratio decline — DB queries climb to ~42k",
    "Cascading: Cache hit-ratio decline pushes DB read latency to ~55 ms",
    "Cascading: Cache cold-start stampede — DB queries ~60k",
    "Cascading: Cache restart causes brief gateway errors (~8%)",
    "Cascading: Cache restart backs up MQ — ~180,000 pending",
]

SCENARIO_B_CASCADES = [
    "Cascading: Sporadic TLS failures propagate to gateway (~5%)",
    "Cascading: Slow JWKS fetch raises auth latency to ~350 ms",
    "Cascading: Broken auth chain — payment 5xx ~8%",
    "Cascading: Mass TLS failure floods gateway (~28%)",
    "Cascading: Unverifiable tokens drive declines to ~45%",
    "Cascading: Mass session re-auth — cache misses ~3,500",
]

SCENARIO_C_CASCADES = [
    "Cascading: Slow disk fails background-job writes (~8/min)",
    "Cascading: DB write latency drift lags observability ingest to ~180s",
    "Cascading: Consumers blocked on DB writes — ~320k pending",
    "Cascading: DB truncation event raises backend latency to ~720 ms",
    "Cascading: DB error spike propagates to gateway (~15%)",
]

ALL_CASCADES = SCENARIO_A_CASCADES + SCENARIO_B_CASCADES + SCENARIO_C_CASCADES


def _parse_ts(value: str) -> datetime.datetime:
    """Parse the manifest timestamp; supports trailing ``Z`` and microseconds."""
    return datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))


def _day_index(amc, ts_str: str) -> int:
    """Return the 1-indexed day-of-run for an ``anomalies.csv`` timestamp."""
    ts = _parse_ts(ts_str)
    start = amc.START
    if start.tzinfo is None and ts.tzinfo is not None:
        ts = ts.replace(tzinfo=None)
    elif start.tzinfo is not None and ts.tzinfo is None:
        ts = ts.replace(tzinfo=start.tzinfo)
    delta = ts - start
    return delta.days + 1


def _component_rows(out_dir, component):
    with open(out_dir / f"{component}.csv", newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _window_values(rows, metric, *, offset_seconds, duration_seconds):
    start = int(offset_seconds // 60)
    stop = start + int(duration_seconds // 60)
    return [float(row[metric]) for row in rows[start:stop]]


def _pearson(xs, ys):
    x_mean = statistics.mean(xs)
    y_mean = statistics.mean(ys)
    x_delta = [x - x_mean for x in xs]
    y_delta = [y - y_mean for y in ys]
    numerator = sum(x * y for x, y in zip(x_delta, y_delta))
    denominator = (
        sum(x * x for x in x_delta) * sum(y * y for y in y_delta)
    ) ** 0.5
    return numerator / denominator


# ------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------
def test_scenario_primaries_present_in_seven_day(amc, seven_day_run):
    """Each scenario primary spec shows up in ``anomalies.csv`` at the right
    component/metric, with a timestamp in the expected day band."""
    manifest = read_manifest(seven_day_run.out_dir)
    by_key: dict[tuple[str, str, str], list[str]] = {}
    for row in manifest:
        by_key.setdefault(
            (row["component"], row["metric"], row["description"]), []
        ).append(row["timestamp"])

    missing = []
    wrong_day = []
    for component, metric, description, expected_day in ALL_PRIMARIES:
        key = (component, metric, description)
        timestamps = by_key.get(key, [])
        if not timestamps:
            missing.append(key)
            continue
        days_seen = {_day_index(amc, t) for t in timestamps}
        if expected_day not in days_seen:
            wrong_day.append((key, expected_day, sorted(days_seen)))

    assert not missing, f"Scenario primaries missing from 7-day manifest: {missing}"
    assert not wrong_day, (
        "Scenario primaries fired on the wrong day band: "
        f"{wrong_day}"
    )


def test_scenario_cascades_present_in_seven_day(seven_day_run):
    """Each scenario cascade description appears at least once in the 7-day
    manifest."""
    descriptions = {row["description"] for row in read_manifest(seven_day_run.out_dir)}
    missing = [d for d in ALL_CASCADES if d not in descriptions]
    assert not missing, f"Scenario cascades missing from 7-day manifest: {missing}"


def test_scenarios_absent_in_one_day(amc, one_day_run_a):
    """None of the scenario specs are reachable inside the default 1-day run
    — they all live at ``time_offset >= SECONDS_PER_DAY`` and must be filtered
    out by the existing stderr WARNING path.
    """
    descriptions = {row["description"] for row in read_manifest(one_day_run_a.out_dir)}
    leaked_primaries = [
        d for (_c, _m, d, _day) in ALL_PRIMARIES if d in descriptions
    ]
    leaked_cascades = [d for d in ALL_CASCADES if d in descriptions]
    assert not leaked_primaries, (
        f"Scenario primaries leaked into 1-day manifest: {leaked_primaries}"
    )
    assert not leaked_cascades, (
        f"Scenario cascades leaked into 1-day manifest: {leaked_cascades}"
    )


def test_seven_day_run_is_deterministic(amc, tmp_path_factory):
    """Two seven-day runs at the same seed produce byte-identical
    ``anomalies.csv``."""
    out_a = tmp_path_factory.mktemp("seven_day_det_a")
    out_b = tmp_path_factory.mktemp("seven_day_det_b")
    run_capture(amc, out_a, days=7, seed=42)
    run_capture(amc, out_b, days=7, seed=42)
    bytes_a = (out_a / "anomalies.csv").read_bytes()  # resource-lint: allow
    bytes_b = (out_b / "anomalies.csv").read_bytes()  # resource-lint: allow
    assert bytes_a == bytes_b, "Seven-day anomalies.csv differs across seeded runs"


def test_gradual_scenarios_have_correlated_span_signal(amc, tmp_path):
    """Gradual scenarios should expose multivariate signal over their span."""
    result = run_capture(
        amc,
        tmp_path,
        days=7,
        drop_rate=0,
        extra_args=[
            "--scenarios",
            ",".join([
                "cache_leak_restart",
                "db_disk_exhaustion",
                "llm_enterprise_onboarding",
                "llm_rate_limit_fallout",
                "llm_weekend_batch",
            ]),
        ],
    )

    cache = _component_rows(result.out_dir, "cacheservice")
    database = _component_rows(result.out_dir, "database")
    llm = _component_rows(result.out_dir, "llm_analytics")
    gateway = _component_rows(result.out_dir, "apigateway")
    objectstore = _component_rows(result.out_dir, "objectstore")

    cache_eviction_start = 2*amc.SECONDS_PER_DAY + 12*3600 + 60
    assert _pearson(
        _window_values(
            cache, "cache_misses", offset_seconds=cache_eviction_start,
            duration_seconds=12*3600 - 60,
        ),
        _window_values(
            database, "read_latency_ms", offset_seconds=cache_eviction_start,
            duration_seconds=12*3600 - 60,
        ),
    ) > 0.55

    disk_pressure_start = 4*amc.SECONDS_PER_DAY + 6*3600 + 120
    write_latency = _window_values(
        database, "write_latency_ms", offset_seconds=disk_pressure_start,
        duration_seconds=12*3600 - 120,
    )
    assert _pearson(
        write_latency,
        _window_values(
            database, "connections", offset_seconds=disk_pressure_start,
            duration_seconds=12*3600 - 120,
        ),
    ) > 0.55
    assert _pearson(
        write_latency,
        _window_values(
            database, "cpu_util_pct", offset_seconds=disk_pressure_start,
            duration_seconds=12*3600 - 120,
        ),
    ) > 0.55

    enterprise_start = 2*amc.SECONDS_PER_DAY + 14*3600
    assert _pearson(
        _window_values(
            llm, "avg_context_window_size", offset_seconds=enterprise_start,
            duration_seconds=6*3600,
        ),
        _window_values(
            llm, "avg_llm_latency_ms", offset_seconds=enterprise_start,
            duration_seconds=6*3600,
        ),
    ) > 0.55

    rate_limit_start = 4*amc.SECONDS_PER_DAY + 9*3600 + 31*60
    assert _pearson(
        _window_values(
            llm, "llm_api_error_rate", offset_seconds=rate_limit_start,
            duration_seconds=89*60,
        ),
        _window_values(
            gateway, "error_rate", offset_seconds=rate_limit_start,
            duration_seconds=89*60,
        ),
    ) > 0.55

    weekend_start = 5*amc.SECONDS_PER_DAY + 2*3600
    assert _pearson(
        _window_values(
            llm, "input_tokens_per_sec", offset_seconds=weekend_start,
            duration_seconds=4*3600,
        ),
        _window_values(
            objectstore, "bandwidth_mbps", offset_seconds=weekend_start,
            duration_seconds=4*3600,
        ),
    ) > 0.55
