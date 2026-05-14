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
is ordered by descending importance and is split by `DEFAULT_METRICS_PER_COMPONENT[name]`
into two zones:

- Indices `[0, DEFAULT_METRICS_PER_COMPONENT[name])` — the historic default schema.
  Inserting or reordering here changes the default CSV columns and breaks the
  byte-for-byte default-output guarantee. Do this only when you are intentionally
  changing the default schema, and bump `DEFAULT_METRICS_PER_COMPONENT[name]` in the
  same change if you are adding (not replacing) an entry.
- Indices `[DEFAULT_METRICS_PER_COMPONENT[name], MAX_METRICS_PER_COMPONENT)` — the
  supplemental zone surfaced only via `--metrics-per-component` (half-open: the last
  valid index is `MAX_METRICS_PER_COMPONENT - 1`, so each component holds at most
  `MAX_METRICS_PER_COMPONENT` entries). New metrics should be appended here by default
  so existing default output stays byte-identical; they are only emitted when callers
  pass `--metrics-per-component` high enough to reach them.

Up to `MAX_METRICS_PER_COMPONENT` (10) entries are allowed per component, and every
catalog in `COMPONENTS` is already at that cap. Adding a new metric therefore
requires one of:

- Replace or remove an existing supplemental metric (zone 2) — preserves the
  default schema and stays within the cap.
- Intentionally raise `MAX_METRICS_PER_COMPONENT` — must be matched by an update
  in `tests/conftest.py` (`COMPONENT_FIELDS` per-component total) and re-run the
  test suite; the import-time validator rejects any list longer than the cap.

Once the slot exists, the column flows through `_natural_column()` and
`generate_component()` automatically.

### Adding new components

A new component needs six lockstep entries — four in `anomaly-metric-creator.py`
and two in `tests/conftest.py`:

In `anomaly-metric-creator.py`:

1. `COMPONENTS[name]` — ordered `MetricSpec` list (up to `MAX_METRICS_PER_COMPONENT`).
2. `DEFAULT_METRICS_PER_COMPONENT[name]` — how many metrics the new component
   emits by default.
3. `anoms_<short>` — a module-level list of primary anomaly spec dicts (may be
   empty if the component has only cascade-driven anomalies).
4. `COMPONENT_PRIMARY_ANOMALIES[name]` — pair the new `anoms_*` list with the
   component name so the runner picks it up.

In `tests/conftest.py`:

5. `COMPONENT_FIELDS[name]` — `(anom_attr, total_metric_count)`. Drives
   `tests/test_registry.py` (component coverage, anomaly attribute presence,
   metric count) and several `tests/test_correctness.py` checks.
6. `DEFAULT_METRIC_COUNT[name]` — historic per-component default count. Drives
   `tests/test_cli.py::test_metrics_per_component_default_matches_legacy_columns`
   and the default-emitted-subset checks in `tests/test_correctness.py`.

Optional: register cascades inside `register_default_cascades()` (or the
high-pressure variant) with the new component name.

Validation is split across import time and the test suite, and the difference
matters:

- **Import time** rejects key drift between `COMPONENTS` and each of
  `DEFAULT_METRICS_PER_COMPONENT` and `COMPONENT_PRIMARY_ANOMALIES`, any catalog
  longer than `MAX_METRICS_PER_COMPONENT`, and any default count outside
  `[1, len(catalog)]`. These raise a clear `ValueError` before `main()` runs.
- **Test suite only.** Import-time validation does *not* check that each
  `COMPONENT_PRIMARY_ANOMALIES[name]` is the right list object — a paste-error
  swap like `"authservice": anoms_cache` has matching keys and imports clean.
  `tests/test_registry.py::test_component_primary_anomalies_keys_match_components`
  is what catches that, using `is` identity against the expected `anoms_*`
  attribute. Drift between `COMPONENTS` and `COMPONENT_FIELDS` /
  `DEFAULT_METRIC_COUNT` is also test-only. Run the test suite after adding
  or modifying a component — don't rely on import-time validation alone.

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
