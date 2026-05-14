# CLAUDE.md

Agent guide for `anomaly-metric-creator.py`. User-facing usage, install, CLI reference,
output files, and the anomaly catalog live in [README.md](README.md). Read it first
if you need to run the script or understand the failure modes it injects.

## Architecture

### Core generation pattern

The script uses a single generator function `generate_component()` that:

1. Takes a component name, a list of `MetricSpec` rows, anomaly specs, and per-run
   config (`base_dir`, `total_seconds`, `drop_rate`, `interval`, pre-built timestamp
   arrays).
2. Builds `floor(total_seconds / interval)` rows. At `interval=1.0` this is one row
   per second.
3. Injects anomalies at their nearest row (`round(time_offset / interval)`). Specs
   whose row index falls outside `[0, n_rows)` are warned on stderr and skipped.
4. Randomly emits blank lines at `drop_rate` to simulate packet loss.
5. Writes timestamp + metric columns to `{component}.csv`.

`generate_component()` is fully vectorized: one numpy op per metric column, anomalies
applied as masked writes, CSV assembled via `np.char.add`. The test suite drives full
1-day and 7-day runs end-to-end through `main()` — keep that path numpy-vectorized when
making changes.

### Entry point

`main(argv=None)` is the entry point and is only invoked under
`if __name__ == "__main__"`. Importing the module does not trigger generation, which
keeps tests and ad-hoc reuse of `generate_component()` cheap.

### Metric specs (value generation)

Each component's columns are declared in `COMPONENTS` as a list of `MetricSpec(name,
base, jitter, multiplier=…, additive=…, clip_min=…)`. The baseline column is built by
`_natural_column()`:

```
value = (base + jitter * randn(n)) * multiplier(ts, elapsed) + additive(ts, elapsed)
```

`multiplier` and `additive` must accept numpy arrays so the whole column generates in
one pass. Use `_daily_sine(amplitude)` for natural 24h variation and
`_llm_business_hours` for the LLM business-hours envelope.

### Anomaly injection schema

Anomaly specs are dicts with:

- `time_offset` — seconds from `START` (e.g., `2*3600 + 15*60` = 02:15:00, or
  `N*SECONDS_PER_DAY + …` for multi-day).
- `metric` — name of the metric field to overwrite at the matched row/span.
- `description` — human-readable description; flows into `anomalies.csv`.
- `generator` — `lambda ts, idx: value` returning the anomalous value.
- `duration_seconds` (optional) — span length; omitted/0 keeps single-row behavior.
- `shape` (optional) — one of `step` (default), `ramp_linear`, `ramp_exp`,
  `sustained`, `sawtooth`, `sine`.
- `shape_params` (optional) — shape-specific params (`start/end`, `period_s`,
  `amplitude`, `midline`, etc.).

Multiple anomalies can fire at the same timestamp across different metrics. The
anomaly registry is collected into the manifest file.

Cascades use `register_cascade(target_component, time_offset, metric, description,
generator)` and are appended after the originating anomaly's time offset to simulate
blast radius (auth → gateway, cache → DB, DB → API/auth, MQ → API/DB, LLM → DB/cache/API).

## Modifying the script

### Adding new metrics

Append a `MetricSpec` to the relevant list in `COMPONENTS`. Each component's list
is ordered by descending importance, so put the new metric where it ranks. Up to
`MAX_METRICS_PER_COMPONENT` (10) entries are allowed per component. No other
changes needed — the column flows through `_natural_column()` and
`generate_component()` automatically. The historic default-emitted count per
component is fixed at the existing `DEFAULT_METRICS_PER_COMPONENT[name]` value;
new metrics appended past that point are only emitted when callers pass
`--metrics-per-component` high enough to reach them.

### Adding new components

Add a `COMPONENTS[name]` entry with the metric list AND a matching
`DEFAULT_METRICS_PER_COMPONENT[name]` entry naming how many metrics the new
component should emit by default. Then add anomaly specs to the matching
`anoms_*` list (or register cascades in `register_default_cascades()`). The
runner picks up new components without further wiring.

Drift between `COMPONENTS` and `DEFAULT_METRICS_PER_COMPONENT` is rejected at
module import time with a `ValueError` so missing/extra keys can't sneak in.

### Anomaly metric validation

`_filter_anomalies_for_emitted_metrics()` runs before generation and treats two
cases differently:

- Metric is in the full `COMPONENTS[component]` catalog but trimmed by
  `--metrics-per-component` → silently dropped (intended behavior of the cap).
- Metric (or component) is not in the full catalog → `ValueError`. This catches
  typos in `anoms_*` lists and `register_cascade()` calls that would otherwise
  silently disappear from all outputs.

### Changing time range

Modify `START` (datetime) to shift when the synthetic day begins. To generate more
than one day, pass `--duration-days N` rather than editing the `SECONDS_PER_DAY`
constant — it is fixed at 86,400 by design.

### Adjusting anomaly timing

Time offsets are in seconds from `START`. Use expressions like `2*3600 + 15*60` for
readability (2 hours 15 minutes). For multi-day specs use `N*SECONDS_PER_DAY + …`. Any
spec whose `time_offset` is `>= SECONDS_PER_DAY * duration_days` is skipped at run time
with a stderr warning naming the duration required to include it — keep the spec,
increase `--duration-days`, rather than silently truncating.

## Tests

Tests live in `tests/` and write only into `tmp_path` (never `iot_logs/`). The suite
runs full 1-day and 7-day generations end-to-end via `main()` and exercises the
vectorized `generate_component()` path. Run with `.venv/bin/pytest` after installing
the `dev` extra (see [README.md](README.md#tests)).
