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
from pathlib import Path

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
# Helper to create a blank line (to simulate a missing sample)
# ------------------------------------------------------------------
def blank_line(file):
    file.write("\n")

# ------------------------------------------------------------------
# Core generator
# ------------------------------------------------------------------
def generate_component(component_name, fieldnames, value_generators, anomaly_specs,
                       *, base_dir, total_seconds, drop_rate):
    """
    value_generators: list of functions((ts, idx)) -> float
    anomaly_specs: list of {'time_offset': int, 'metric': str, 'description': str, 'generator': fn}
    """
    file_path = base_dir / f"{component_name}.csv"

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

    with open(file_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp"] + fieldnames)

        # Pre-compute anomaly times for quick lookup
        # Handle multiple anomalies at same time by grouping them
        anomaly_map = {}
        for spec in all_anomalies:
            anomaly_map.setdefault(spec["time_offset"], []).append(spec)

        for sec in range(total_seconds):
            ts = START + datetime.timedelta(seconds=sec)
            row = [ts.strftime("%Y-%m-%d %H:%M:%S")]

            # Normal or anomaly?
            if sec in anomaly_map:
                specs = anomaly_map[sec]
                # Add all anomalies to registry
                for spec in specs:
                    anomalies.append({
                        "timestamp": ts.strftime("%Y-%m-%d %H:%M:%S"),
                        "component": component_name,
                        "metric": spec["metric"],
                        "description": spec["description"]
                    })

                # Build metric override map for this timestamp
                metric_overrides = {spec["metric"]: spec["generator"] for spec in specs}

                # Inject anomaly values for affected metrics, normal for others
                for idx, fn in enumerate(value_generators):
                    field = fieldnames[idx]
                    if field in metric_overrides:
                        val = metric_overrides[field](ts, idx)
                    else:
                        val = fn(ts, idx)
                    row.append(round(val, 3))
            else:
                # Normal row
                row += [round(fn(ts, idx), 3) for idx, fn in enumerate(value_generators)]

            # Randomly drop a row to simulate packet loss
            if random.random() < drop_rate:
                blank_line(f)
                continue

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
# Value generators for each component
# ------------------------------------------------------------------
def va_auth(ts, idx):
    # Baseline rates
    if idx == 0:                 # active_sessions
        return 200 + np.sin(idx + ts.second / 60) * 20
    if idx == 1:                 # login_attempts
        return 250 + np.random.normal(0, 15)
    if idx == 2:                 # login_success_rate
        return 97.0 + np.random.normal(0, 0.5)
    if idx == 3:                 # avg_auth_latency_ms
        return 110 + np.random.normal(0, 5)
    if idx == 4:                 # cpu_util_pct
        return 20 + np.random.normal(0, 3)
    if idx == 5:                 # error_rate
        return 0.2 + np.random.normal(0, 0.05)

def va_cache(ts, idx):
    if idx == 0: return 5000 + np.random.normal(0, 200)
    if idx == 1: return 200 + np.random.normal(0, 20)
    if idx == 2: return 95.0 + np.random.normal(0, 0.3)
    if idx == 3: return 15 + np.random.normal(0, 1)
    if idx == 4: return 70 + np.random.normal(0, 5)
    if idx == 5: return 0.05 + np.random.normal(0, 0.02)

def va_api(ts, idx):
    if idx == 0: return 800 + np.random.normal(0, 50)
    if idx == 1: return 180 + np.random.normal(0, 10)
    if idx == 2: return 90 + np.random.normal(0, 8)
    if idx == 3: return 1200 + np.random.normal(0, 60)
    if idx == 4: return 22 + np.random.normal(0, 4)
    if idx == 5: return 0.15 + np.random.normal(0, 0.04)

def va_db(ts, idx):
    if idx == 0: return 3000 + np.random.normal(0, 400)
    if idx == 1: return 10 + np.random.normal(0, 2)
    if idx == 2: return 12 + np.random.normal(0, 3)
    if idx == 3: return 25000 + np.random.normal(0, 2000)
    if idx == 4: return 18 + np.random.normal(0, 3)
    if idx == 5: return 0.1 + np.random.normal(0, 0.05)

def va_mq(ts, idx):
    if idx == 0: return 45000 + np.random.normal(0, 3000)
    if idx == 1: return 43000 + np.random.normal(0, 2500)
    if idx == 2: return 70 + np.random.normal(0, 5)
    if idx == 3: return 5 + np.random.normal(0, 1)
    if idx == 4: return 55 + np.random.normal(0, 4)
    if idx == 5: return 0.08 + np.random.normal(0, 0.02)

def va_llm(ts, idx):
    """
    LLM Analytics Service with a daily business-hours pattern.
    """
    hour_of_day = ts.hour
    if 8 <= hour_of_day < 18:        # Business hours
        daily_multiplier = 1.4
    elif 18 <= hour_of_day < 22:     # Evening
        daily_multiplier = 1.1
    else:                            # Night/early morning
        daily_multiplier = 0.6

    if idx == 0:  # input_tokens_per_sec
        return 25000 * daily_multiplier + np.random.normal(0, 2000)
    if idx == 1:  # output_tokens_per_sec
        return 8000 * daily_multiplier + np.random.normal(0, 800)
    if idx == 2:  # avg_context_window_size (tokens)
        return 4500 + np.random.normal(0, 500)
    if idx == 3:  # llm_requests_per_sec
        return 45 * daily_multiplier + np.random.normal(0, 5)
    if idx == 4:  # avg_llm_latency_ms
        return 850 + np.random.normal(0, 80)
    if idx == 5:  # token_limit_hits_per_min
        return max(0, 2 * daily_multiplier + np.random.normal(0, 0.5))
    if idx == 6:  # context_overflow_rate (percentage)
        return max(0, 0.3 + np.random.normal(0, 0.1))
    if idx == 7:  # llm_api_error_rate
        return max(0, 0.05 + np.random.normal(0, 0.02))

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

    components = [
        ("authservice",
         ["active_sessions", "login_attempts", "login_success_rate",
          "avg_auth_latency_ms", "cpu_util_pct", "error_rate"],
         [va_auth] * 6, anoms_auth),
        ("cacheservice",
         ["cache_hits", "cache_misses", "hit_ratio",
          "avg_cache_latency_ms", "memory_util_pct", "error_rate"],
         [va_cache] * 6, anoms_cache),
        ("apigateway",
         ["requests_per_sec", "avg_response_time_ms", "backend_latency_ms",
          "active_connections", "cpu_util_pct", "error_rate"],
         [va_api] * 6, anoms_api),
        ("database",
         ["connections", "read_latency_ms", "write_latency_ms",
          "queries_per_sec", "cpu_util_pct", "error_rate"],
         [va_db] * 6, anoms_db),
        ("mqservice",
         ["pending_messages", "processed_messages", "avg_latency_ms",
          "dead_letter_queue", "mem_util_pct", "error_rate"],
         [va_mq] * 6, anoms_mq),
        ("llm_analytics",
         ["input_tokens_per_sec", "output_tokens_per_sec", "avg_context_window_size",
          "llm_requests_per_sec", "avg_llm_latency_ms", "token_limit_hits_per_min",
          "context_overflow_rate", "llm_api_error_rate"],
         [va_llm] * 8, anoms_llm),
    ]

    for name, fields, gens, specs in components:
        generate_component(name, fields, gens, specs,
                           base_dir=args.output_dir,
                           total_seconds=total_seconds,
                           drop_rate=args.drop_rate)

    with open(args.output_dir / "anomalies.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["timestamp", "component", "metric", "description"])
        writer.writeheader()
        for a in anomalies:
            writer.writerow(a)

    print(f"Done - {len(components)} log files + anomalies.csv written to {args.output_dir}")
    print(f"   Duration: {args.duration_days} day(s) ({total_seconds:,} seconds)")
    print(f"   Anomalies recorded: {len(anomalies)}")


if __name__ == "__main__":
    main()
