#!/usr/bin/env python3
"""
Generate IoT-style metric logs for a SaaS stack with built-in anomalies.

Defaults to one day at 1-second resolution. Use ``--duration-days N`` to span
more days; the multi-day LLM viral/cascade catalog only becomes reachable at
``--duration-days >= 7``. Anomaly specs whose ``time_offset`` falls outside the
configured window are skipped with a warning on stderr.
"""

import argparse
import csv
import datetime
import random
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np

# ------------------------------------------------------------------
# Configuration
# ------------------------------------------------------------------
START = datetime.datetime(2026, 3, 10, 0, 0, 0)
SECONDS_PER_DAY = 86_400
DEFAULT_DURATION_DAYS = 1
DEFAULT_SEED = 42
DEFAULT_OUTPUT_DIR = Path("iot_logs")
DEFAULT_DROP_RATE = 0.0005

# ------------------------------------------------------------------
# Anomaly registry and cascade tracking (reset on each main() call)
# ------------------------------------------------------------------
anomalies = []
cascading_anomalies = {}  # {component_name: [anomaly_specs]}

# ------------------------------------------------------------------
# Per-metric schema. One MetricSpec per CSV column per component.
# ------------------------------------------------------------------
@dataclass(frozen=True)
class MetricSpec:
    """Config for one synthetic metric column.

    Natural value is ``base * multiplier(ts, sec) + additive(ts, sec) + N(0, std)``,
    optionally clipped at ``clip_min``. ``std=0`` skips the RNG draw entirely so
    deterministic series do not perturb the shared numpy random stream.
    """
    name: str
    base: float
    std: float = 0.0
    multiplier: Callable[[datetime.datetime, int], float] | None = None
    additive: Callable[[datetime.datetime, int], float] | None = None
    clip_min: float | None = None


def _natural_value(spec: MetricSpec, ts: datetime.datetime, elapsed: int) -> float:
    val = spec.base
    if spec.multiplier is not None:
        val *= spec.multiplier(ts, elapsed)
    if spec.additive is not None:
        val += spec.additive(ts, elapsed)
    if spec.std > 0:
        val += np.random.normal(0, spec.std)
    if spec.clip_min is not None:
        val = max(spec.clip_min, val)
    return val


# ------------------------------------------------------------------
# Core generator
# ------------------------------------------------------------------
def generate_component(component_name, specs: list[MetricSpec], anomaly_specs,
                       *, base_dir, total_seconds, drop_rate):
    """
    specs: list of MetricSpec (one per CSV column, in column order)
    anomaly_specs: list of {'time_offset': int, 'metric': str, 'description': str, 'generator': fn}
    """
    file_path = base_dir / f"{component_name}.csv"
    fieldnames = [s.name for s in specs]

    # Merge primary anomalies with cascading anomalies
    all_anomalies = list(anomaly_specs)
    if component_name in cascading_anomalies:
        all_anomalies.extend(cascading_anomalies[component_name])

    # Surface specs outside the configured window so dead specs are loud, not silent.
    out_of_range = [s for s in all_anomalies
                    if s["time_offset"] >= total_seconds or s["time_offset"] < 0]
    if out_of_range:
        max_offset = max(s["time_offset"] for s in out_of_range)
        needed_days = max_offset // SECONDS_PER_DAY + 1
        print(
            f"WARNING: {component_name}: skipping {len(out_of_range)} anomaly spec(s) "
            f"with time_offset outside [0, {total_seconds}). "
            f"Run with --duration-days {needed_days} to include them.",
            file=sys.stderr,
        )
        all_anomalies = [s for s in all_anomalies if 0 <= s["time_offset"] < total_seconds]

    # Fail loudly on duplicate (metric, time_offset) tuples — the previous
    # ``metric_overrides = {spec["metric"]: spec["generator"] for spec in specs}``
    # silently kept only the last one.
    seen: dict[tuple[str, int], dict] = {}
    duplicates: list[tuple[str, str, int]] = []
    for spec in all_anomalies:
        key = (spec["metric"], spec["time_offset"])
        if key in seen:
            duplicates.append((component_name, spec["metric"], spec["time_offset"]))
        else:
            seen[key] = spec
    if duplicates:
        raise ValueError(
            f"Duplicate anomaly specs (component, metric, time_offset): {duplicates}"
        )

    with open(file_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp"] + fieldnames)

        # Pre-compute anomaly times for quick lookup
        # Handle multiple anomalies at same time by grouping them
        anomaly_map: dict[int, list[dict]] = {}
        for spec in all_anomalies:
            anomaly_map.setdefault(spec["time_offset"], []).append(spec)

        for sec in range(total_seconds):
            # Packet loss: a lost sample never arrives, so it produces no
            # CSV row AND no manifest entry. Decide this first so the two
            # outputs can never disagree on a dropped second.
            if random.random() < drop_rate:
                continue

            ts = START + datetime.timedelta(seconds=sec)
            row = [ts.strftime("%Y-%m-%d %H:%M:%S")]

            # Normal or anomaly?
            if sec in anomaly_map:
                aspecs = anomaly_map[sec]
                # Add all anomalies to registry
                for aspec in aspecs:
                    anomalies.append({
                        "timestamp": ts.strftime("%Y-%m-%d %H:%M:%S"),
                        "component": component_name,
                        "metric": aspec["metric"],
                        "description": aspec["description"]
                    })

                # Build metric override map for this timestamp
                metric_overrides = {aspec["metric"]: aspec["generator"] for aspec in aspecs}

                # Inject anomaly values for affected metrics, natural for others
                for idx, mspec in enumerate(specs):
                    if mspec.name in metric_overrides:
                        val = metric_overrides[mspec.name](ts, idx)
                    else:
                        val = _natural_value(mspec, ts, sec)
                    row.append(round(val, 3))
            else:
                # Normal row
                row += [round(_natural_value(mspec, ts, sec), 3) for mspec in specs]

            writer.writerow(row)

# ------------------------------------------------------------------
# Cascade helper function
# ------------------------------------------------------------------
def register_cascade(target_component, time_offset, metric, description, generator):
    """
    Register a cascading anomaly that will affect another component.
    """
    cascading_anomalies.setdefault(target_component, []).append({
        "time_offset": time_offset,
        "metric": metric,
        "description": description,
        "generator": generator,
    })

# ------------------------------------------------------------------
# Shared seasonality / shaping helpers used by COMPONENTS specs
# ------------------------------------------------------------------
def _llm_business_hours(ts: datetime.datetime, _elapsed: int) -> float:
    """Daily business-hours load multiplier for LLM analytics."""
    h = ts.hour
    if 8 <= h < 18:
        return 1.4
    if 18 <= h < 22:
        return 1.1
    return 0.6


def _daily_sine(amplitude: float) -> Callable[[datetime.datetime, int], float]:
    """Additive 24h sine shaped by the elapsed second so the curve has real
    daily seasonality (the legacy version reset every minute)."""
    def fn(_ts: datetime.datetime, elapsed: int) -> float:
        return amplitude * np.sin(2 * np.pi * elapsed / SECONDS_PER_DAY)
    return fn


# ------------------------------------------------------------------
# Per-component metric schemas. Add a metric by editing exactly one list.
# ------------------------------------------------------------------
COMPONENTS: dict[str, list[MetricSpec]] = {
    "authservice": [
        MetricSpec("active_sessions", 200, additive=_daily_sine(20)),
        MetricSpec("login_attempts", 250, 15),
        MetricSpec("login_success_rate", 97.0, 0.5),
        MetricSpec("avg_auth_latency_ms", 110, 5),
        MetricSpec("cpu_util_pct", 20, 3),
        MetricSpec("error_rate", 0.2, 0.05),
    ],
    "cacheservice": [
        MetricSpec("cache_hits", 5000, 200),
        MetricSpec("cache_misses", 200, 20),
        MetricSpec("hit_ratio", 95.0, 0.3),
        MetricSpec("avg_cache_latency_ms", 15, 1),
        MetricSpec("memory_util_pct", 70, 5),
        MetricSpec("error_rate", 0.05, 0.02),
    ],
    "apigateway": [
        MetricSpec("requests_per_sec", 800, 50),
        MetricSpec("avg_response_time_ms", 180, 10),
        MetricSpec("backend_latency_ms", 90, 8),
        MetricSpec("active_connections", 1200, 60),
        MetricSpec("cpu_util_pct", 22, 4),
        MetricSpec("error_rate", 0.15, 0.04),
    ],
    "database": [
        MetricSpec("connections", 3000, 400),
        MetricSpec("read_latency_ms", 10, 2),
        MetricSpec("write_latency_ms", 12, 3),
        MetricSpec("queries_per_sec", 25000, 2000),
        MetricSpec("cpu_util_pct", 18, 3),
        MetricSpec("error_rate", 0.1, 0.05),
    ],
    "mqservice": [
        MetricSpec("pending_messages", 45000, 3000),
        MetricSpec("processed_messages", 43000, 2500),
        MetricSpec("avg_latency_ms", 70, 5),
        MetricSpec("dead_letter_queue", 5, 1),
        MetricSpec("mem_util_pct", 55, 4),
        MetricSpec("error_rate", 0.08, 0.02),
    ],
    "llm_analytics": [
        MetricSpec("input_tokens_per_sec", 25000, 2000, multiplier=_llm_business_hours),
        MetricSpec("output_tokens_per_sec", 8000, 800, multiplier=_llm_business_hours),
        MetricSpec("avg_context_window_size", 4500, 500),
        MetricSpec("llm_requests_per_sec", 45, 5, multiplier=_llm_business_hours),
        MetricSpec("avg_llm_latency_ms", 850, 80),
        MetricSpec("token_limit_hits_per_min", 2, 0.5,
                   multiplier=_llm_business_hours, clip_min=0),
        MetricSpec("context_overflow_rate", 0.3, 0.1, clip_min=0),
        MetricSpec("llm_api_error_rate", 0.05, 0.02, clip_min=0),
    ],
}

# ------------------------------------------------------------------
# Anomaly specifications
# ------------------------------------------------------------------
anoms_auth = [
    {
        "time_offset": 2*3600 + 15*60,            # 02:15:00
        "metric": "error_rate",
        "description": "Spike in failed logins – possible brute force",
        "generator": lambda ts,idx: 0.42   # 42 % error
    },
    {
        "time_offset": 2*3600 + 15*60,
        "metric": "login_attempts",
        "description": "Login attempts surge 5×",
        "generator": lambda ts,idx: 1250
    }
]

anoms_cache = [
    {
        "time_offset": 6*3600,          # 04:30:00
        "metric": "hit_ratio",
        "description": "Cache hit ratio drops to 5 %",
        "generator": lambda ts,idx: 5.0
    }
]

anoms_api = [
    {
        "time_offset": 6*3600 + 30*60,  # 06:30:00
        "metric": "cpu_util_pct",
        "description": "CPU saturates at 100 %",
        "generator": lambda ts,idx: 100.0
    }
]

anoms_db = [
    {
        "time_offset": 11*3600,           # 11:00:00
        "metric": "read_latency_ms",
        "description": "Read latency skyrockets to 360 ms",
        "generator": lambda ts,idx: 360.0
    },
    {
        "time_offset": 11*3600,
        "metric": "error_rate",
        "description": "Backend errors rise 23 %",
        "generator": lambda ts,idx: 0.23
    }
]

anoms_mq = [
    {
        "time_offset": 14*3600 + 30*60,   # 14:30:00
        "metric": "pending_messages",
        "description": "Pending messages jam to 1 M",
        "generator": lambda ts,idx: 1_000_000
    },
    {
        "time_offset": 14*3600 + 30*60,
        "metric": "error_rate",
        "description": "Error rate jumps to 10 %",
        "generator": lambda ts,idx: 0.10
    }
]

# Multi-day LLM catalog. Unreachable at --duration-days 1; needs >= 7.
anoms_llm = [
    # Day 1 - Initial viral surge
    {
        "time_offset": 1*SECONDS_PER_DAY + 10*3600 + 15*60,  # Day 2, 10:15:00
        "metric": "llm_requests_per_sec",
        "description": "Viral surge: Customer demo goes viral, 8× request spike",
        "generator": lambda ts,idx: 360
    },
    {
        "time_offset": 1*SECONDS_PER_DAY + 10*3600 + 15*60,
        "metric": "input_tokens_per_sec",
        "description": "Token surge from viral traffic",
        "generator": lambda ts,idx: 185000
    },
    {
        "time_offset": 1*SECONDS_PER_DAY + 10*3600 + 15*60,
        "metric": "output_tokens_per_sec",
        "description": "Output token surge from viral traffic",
        "generator": lambda ts,idx: 62000
    },
    # Day 2 - Enterprise customer onboarding
    {
        "time_offset": 2*SECONDS_PER_DAY + 14*3600,  # Day 3, 14:00:00
        "metric": "llm_requests_per_sec",
        "description": "Enterprise onboarding: Major customer launches AI features",
        "generator": lambda ts,idx: 285
    },
    {
        "time_offset": 2*SECONDS_PER_DAY + 14*3600,
        "metric": "avg_context_window_size",
        "description": "Enterprise using large context windows for analytics",
        "generator": lambda ts,idx: 12500
    },
    {
        "time_offset": 2*SECONDS_PER_DAY + 14*3600,
        "metric": "token_limit_hits_per_min",
        "description": "Token limits hit frequently during enterprise rollout",
        "generator": lambda ts,idx: 45
    },
    # Day 4 - API rate limit issues
    {
        "time_offset": 4*SECONDS_PER_DAY + 9*3600 + 30*60,  # Day 5, 09:30:00
        "metric": "llm_api_error_rate",
        "description": "LLM provider rate limits hit, 18% error rate",
        "generator": lambda ts,idx: 0.18
    },
    {
        "time_offset": 4*SECONDS_PER_DAY + 9*3600 + 30*60,
        "metric": "avg_llm_latency_ms",
        "description": "LLM latency spikes due to rate limiting",
        "generator": lambda ts,idx: 4200
    },
    # Day 5 - Weekend batch processing surge
    {
        "time_offset": 5*SECONDS_PER_DAY + 2*3600,  # Day 6, 02:00:00 (weekend batch job)
        "metric": "input_tokens_per_sec",
        "description": "Weekend batch analytics job processing historical data",
        "generator": lambda ts,idx: 320000
    },
    {
        "time_offset": 5*SECONDS_PER_DAY + 2*3600,
        "metric": "context_overflow_rate",
        "description": "Context overflow from large batch documents",
        "generator": lambda ts,idx: 8.5
    },
    # Day 6 - Second viral event
    {
        "time_offset": 6*SECONDS_PER_DAY + 16*3600 + 45*60,  # Day 7, 16:45:00
        "metric": "llm_requests_per_sec",
        "description": "Social media mention drives 10× traffic spike",
        "generator": lambda ts,idx: 450
    },
    {
        "time_offset": 6*SECONDS_PER_DAY + 16*3600 + 45*60,
        "metric": "input_tokens_per_sec",
        "description": "Massive token usage from social traffic",
        "generator": lambda ts,idx: 420000
    },
    {
        "time_offset": 6*SECONDS_PER_DAY + 16*3600 + 45*60,
        "metric": "output_tokens_per_sec",
        "description": "Output tokens surge from viral event",
        "generator": lambda ts,idx: 135000
    }
]

# ------------------------------------------------------------------
# Cascading-failure registry. Same-day cascades fire under any duration;
# multi-day cascades (LLM-driven) only reach during runs of >= 7 days.
# ------------------------------------------------------------------
def register_default_cascades():
    # Auth service brute force → API Gateway sees more errors
    register_cascade("apigateway",
                     2*3600 + 15*60 + 15,
                     "error_rate",
                     "Cascading: Auth failures cause API gateway errors",
                     lambda ts, idx: 0.28)

    # Cache failure → Database sees increased load
    register_cascade("database",
                     6*3600 + 30,
                     "queries_per_sec",
                     "Cascading: Cache misses increase database queries",
                     lambda ts, idx: 38000 + np.random.normal(0, 3000))

    register_cascade("database",
                     6*3600 + 45,
                     "read_latency_ms",
                     "Cascading: Database read latency increases from cache misses",
                     lambda ts, idx: 45 + np.random.normal(0, 5))

    # API Gateway CPU saturation → cascades to multiple services
    register_cascade("authservice",
                     6*3600 + 30*60 + 12,
                     "error_rate",
                     "Cascading: API gateway overload causes auth errors",
                     lambda ts, idx: 0.35)

    register_cascade("cacheservice",
                     6*3600 + 30*60 + 18,
                     "error_rate",
                     "Cascading: API gateway overload causes cache errors",
                     lambda ts, idx: 0.15)

    # Database failure → cascades to API and Auth
    register_cascade("apigateway",
                     11*3600,
                     "backend_latency_ms",
                     "Cascading: Database latency affects API backend",
                     lambda ts, idx: 850 + np.random.normal(0, 50))

    register_cascade("apigateway",
                     11*3600 + 5,
                     "error_rate",
                     "Cascading: Database errors propagate to API",
                     lambda ts, idx: 0.19)

    register_cascade("authservice",
                     11*3600 + 10,
                     "avg_auth_latency_ms",
                     "Cascading: Database issues slow auth queries",
                     lambda ts, idx: 420 + np.random.normal(0, 30))

    # MQ service jam → cascades to API and Database
    register_cascade("apigateway",
                     14*3600 + 30*60 + 60,
                     "avg_response_time_ms",
                     "Cascading: MQ backlog delays API responses",
                     lambda ts, idx: 650 + np.random.normal(0, 40))

    register_cascade("database",
                     14*3600 + 30*60 + 90,
                     "connections",
                     "Cascading: MQ issues cause connection buildup",
                     lambda ts, idx: 8500 + np.random.normal(0, 500))

    register_cascade("database",
                     14*3600 + 30*60 + 95,
                     "write_latency_ms",
                     "Cascading: MQ backpressure increases write latency",
                     lambda ts, idx: 85 + np.random.normal(0, 10))

    # LLM viral surge → cascades to database and cache (multi-day)
    register_cascade("database",
                     1*SECONDS_PER_DAY + 10*3600 + 15*60 + 30,
                     "queries_per_sec",
                     "Cascading: LLM surge increases database queries for context retrieval",
                     lambda ts, idx: 48000 + np.random.normal(0, 4000))

    register_cascade("database",
                     1*SECONDS_PER_DAY + 10*3600 + 15*60 + 45,
                     "connections",
                     "Cascading: LLM service creates more database connections",
                     lambda ts, idx: 7200 + np.random.normal(0, 400))

    register_cascade("cacheservice",
                     1*SECONDS_PER_DAY + 10*3600 + 15*60 + 20,
                     "cache_misses",
                     "Cascading: LLM context cache misses spike",
                     lambda ts, idx: 1800 + np.random.normal(0, 150))

    register_cascade("apigateway",
                     1*SECONDS_PER_DAY + 10*3600 + 15*60 + 10,
                     "requests_per_sec",
                     "Cascading: LLM viral traffic increases API gateway load",
                     lambda ts, idx: 2400 + np.random.normal(0, 200))

    # Enterprise onboarding → database pressure (multi-day)
    register_cascade("database",
                     2*SECONDS_PER_DAY + 14*3600 + 60,
                     "read_latency_ms",
                     "Cascading: Large LLM context windows cause slow DB reads",
                     lambda ts, idx: 85 + np.random.normal(0, 8))

    register_cascade("cacheservice",
                     2*SECONDS_PER_DAY + 14*3600 + 35,
                     "memory_util_pct",
                     "Cascading: LLM context caching increases memory pressure",
                     lambda ts, idx: 92 + np.random.normal(0, 3))

    # LLM rate limit issues → API gateway sees errors (multi-day)
    register_cascade("apigateway",
                     4*SECONDS_PER_DAY + 9*3600 + 30*60 + 8,
                     "error_rate",
                     "Cascading: LLM API errors propagate to gateway",
                     lambda ts, idx: 0.22)

    # Weekend batch job → multiple service impact (multi-day)
    register_cascade("database",
                     5*SECONDS_PER_DAY + 2*3600 + 15,
                     "queries_per_sec",
                     "Cascading: Batch LLM processing hammers database",
                     lambda ts, idx: 65000 + np.random.normal(0, 5000))

    register_cascade("database",
                     5*SECONDS_PER_DAY + 2*3600 + 120,
                     "cpu_util_pct",
                     "Cascading: Database CPU saturates from batch analytics",
                     lambda ts, idx: 94 + np.random.normal(0, 2))

    register_cascade("cacheservice",
                     5*SECONDS_PER_DAY + 2*3600 + 45,
                     "hit_ratio",
                     "Cascading: Batch job overwhelms cache with cold data",
                     lambda ts, idx: 22.0 + np.random.normal(0, 3))

    # Second viral event → system-wide impact (multi-day)
    register_cascade("apigateway",
                     6*SECONDS_PER_DAY + 16*3600 + 45*60 + 5,
                     "active_connections",
                     "Cascading: Viral LLM traffic maxes out connections",
                     lambda ts, idx: 4800 + np.random.normal(0, 200))

    register_cascade("apigateway",
                     6*SECONDS_PER_DAY + 16*3600 + 45*60 + 15,
                     "cpu_util_pct",
                     "Cascading: API gateway CPU spikes from LLM traffic",
                     lambda ts, idx: 87 + np.random.normal(0, 4))

    register_cascade("database",
                     6*SECONDS_PER_DAY + 16*3600 + 45*60 + 25,
                     "connections",
                     "Cascading: Database connection pool exhausted by LLM load",
                     lambda ts, idx: 9800 + np.random.normal(0, 500))

    register_cascade("cacheservice",
                     6*SECONDS_PER_DAY + 16*3600 + 45*60 + 18,
                     "error_rate",
                     "Cascading: Cache service errors under LLM traffic",
                     lambda ts, idx: 0.31)

# ------------------------------------------------------------------
# CLI + entry point
# ------------------------------------------------------------------
def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="Generate synthetic IoT metric logs with anomalies.",
    )
    p.add_argument("--duration-days", type=int, default=DEFAULT_DURATION_DAYS,
                   help=f"Number of days of metrics to generate (default: {DEFAULT_DURATION_DAYS}). "
                        "The multi-day LLM/cascade catalog requires >= 7.")
    p.add_argument("--seed", type=int, default=DEFAULT_SEED,
                   help=f"RNG seed for deterministic output (default: {DEFAULT_SEED}).")
    p.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR,
                   help=f"Directory to write CSV files into (default: {DEFAULT_OUTPUT_DIR}).")
    p.add_argument("--drop-rate", type=float, default=DEFAULT_DROP_RATE,
                   help=f"Probability per row of writing a blank line to simulate packet loss "
                        f"(default: {DEFAULT_DROP_RATE}).")
    args = p.parse_args(argv)

    if args.duration_days < 1:
        p.error("--duration-days must be >= 1")
    if not 0.0 <= args.drop_rate <= 1.0:
        p.error("--drop-rate must be between 0 and 1")
    return args


def main(argv=None):
    args = parse_args(argv)

    total_seconds = SECONDS_PER_DAY * args.duration_days
    args.output_dir.mkdir(exist_ok=True, parents=True)
    np.random.seed(args.seed)
    random.seed(args.seed)

    # Reset module-level registries so repeated calls (e.g., from tests) don't accumulate.
    anomalies.clear()
    cascading_anomalies.clear()
    register_default_cascades()

    component_anomalies = {
        "authservice": anoms_auth,
        "cacheservice": anoms_cache,
        "apigateway": anoms_api,
        "database": anoms_db,
        "mqservice": anoms_mq,
        "llm_analytics": anoms_llm,
    }

    for name, specs in COMPONENTS.items():
        generate_component(name, specs, component_anomalies[name],
                           base_dir=args.output_dir,
                           total_seconds=total_seconds,
                           drop_rate=args.drop_rate)

    with open(args.output_dir / "anomalies.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["timestamp", "component", "metric", "description"])
        writer.writeheader()
        for a in anomalies:
            writer.writerow(a)

    print(f"Done - {len(COMPONENTS)} log files + anomalies.csv written to {args.output_dir}")
    print(f"   Duration: {args.duration_days} day(s) ({total_seconds:,} seconds)")
    print(f"   Anomalies recorded: {len(anomalies)}")


if __name__ == "__main__":
    main()
