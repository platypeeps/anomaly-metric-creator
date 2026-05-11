# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a Python script that generates synthetic IoT-style metric logs for a SaaS stack with built-in anomalies. By default it creates **one day** of second-by-second metrics for six components (authservice, cacheservice, apigateway, database, mqservice, llm_analytics), along with an anomalies manifest. Duration is configurable via `--duration-days`.

## Running the Script

```bash
# Default: 1 day (86,400 rows per component)
python3 anomaly-metric-creator.py

# Full week (604,800 rows per component); required to unlock the multi-day
# LLM/cascade anomaly catalog (~46 specs vs ~19 same-day specs).
python3 anomaly-metric-creator.py --duration-days 7
```

### CLI flags

| Flag             | Default     | Notes                                                              |
| ---------------- | ----------- | ------------------------------------------------------------------ |
| `--duration-days`| `1`         | Days to generate. Multi-day LLM/cascade specs require `>= 7`.      |
| `--seed`         | `42`        | RNG seed for deterministic output.                                 |
| `--output-dir`   | `iot_logs`  | Directory CSVs are written into (created if missing).              |
| `--drop-rate`    | `0.0005`    | Per-row probability of emitting a blank line (simulated packet loss). |

Anomaly specs whose `time_offset >= total_seconds` are skipped with a `WARNING:` line on stderr that names the duration needed to include them. Same-day specs (auth/cache/api/db/mq + their cascades) always fire; the LLM viral/onboarding/batch/second-viral catalog only fires at `--duration-days >= 7`.

This generates CSV files in the output directory (default `iot_logs/`):
- `authservice.csv`
- `cacheservice.csv`
- `apigateway.csv`
- `database.csv`
- `mqservice.csv`
- `llm_analytics.csv`
- `anomalies.csv` (manifest of all injected anomalies)

## Dependencies

The script requires:
- Python 3.x
- numpy
- csv, datetime, random, os, pathlib (standard library)

Install numpy if needed:
```bash
pip3 install numpy
```

## Testing

Dev dependencies (`pytest`, `numpy`) are declared in `pyproject.toml` under the `dev` extra. Python 3.11+ is the supported target.

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest
```

Tests live in `tests/` and write only into `tmp_path` (never `iot_logs/`). The suite runs full 1-day and 7-day generations end-to-end via `main()` and currently lands around ~28s — under the 30s budget. If that grows, vectorize the per-second loop in `generate_component()` rather than trimming coverage.

## Architecture

### Core Generation Pattern

The script uses a single generator function `generate_component()` that:
1. Takes component name, field names, value generators, anomaly specs, and per-run config (`base_dir`, `total_seconds`, `drop_rate`)
2. Generates `total_seconds` rows (default 86,400 = one day at 1-second resolution)
3. Injects anomalies at specific time offsets; specs outside `[0, total_seconds)` are warned and skipped
4. Randomly drops rows at `drop_rate` (default ~0.05%) to simulate packet loss
5. Writes to CSV with timestamp + metric columns

### Entry point

`main(argv=None)` is the entry point and is only invoked under `if __name__ == "__main__"`. Importing the module does not trigger generation — useful for tests and for ad-hoc reuse of `generate_component()`.

### Value Generators

Each component has a value generator function (e.g., `va_auth`, `va_cache`) that produces normal baseline values using numpy random distributions and sine waves for natural variation. These functions take `(timestamp, idx)` and return metric values based on the field index.

### Anomaly Injection

Anomalies are defined as dictionaries with:
- `time_offset`: Second of the day (e.g., `2*3600 + 15*60` = 02:15:00)
- `metric`: Name of the metric field to affect
- `description`: Human-readable description
- `generator`: Lambda function returning the anomalous value

Multiple anomalies can occur at the same timestamp across different metrics. The anomaly registry collects all anomalies for the manifest file.

## Modifying the Script

### Adding New Metrics

Add fields to the fieldnames list and extend the value generator function with the corresponding `idx` case.

### Adding New Components

Call `generate_component()` with:
1. Component name (becomes filename)
2. List of metric field names
3. List of value generator functions (one per field)
4. List of anomaly spec dictionaries

### Changing Time Range

Modify `START` (datetime) to shift when the synthetic day begins. To generate more than one day, pass `--duration-days N` rather than editing the `SECONDS_PER_DAY` constant — it is fixed at 86,400 by design.

### Adjusting Anomaly Timing

Time offsets are in seconds from `START`. Use expressions like `2*3600 + 15*60` for readability (2 hours 15 minutes). For multi-day specs use `N*SECONDS_PER_DAY + ...`. Any spec whose `time_offset` is `>= SECONDS_PER_DAY * duration_days` is skipped at run time with a stderr warning naming the duration required to include it — keep the spec, increase `--duration-days`, rather than silently truncating.
