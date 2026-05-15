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

### Combine step

`combine_logs(input_dir, components=None)` joins the per-component CSVs in
`input_dir` into `combined_metrics_unified.csv`. When `components` is provided,
the unified output is restricted to that list verbatim (caller-controlled order,
missing per-component CSVs raise `SystemExit`); when omitted, every `*.csv` in
`input_dir` is autodiscovered (excluding the anomalies manifest and prior
combine outputs). `main()` threads `--components` into both call sites
(`--combine` and `--combine-only`) so the combine output honors the same
allowlist as generation, `anomalies.csv`, reporting artifacts, and OTEL
streaming. The default `--components all` keeps autodiscovery active, which
preserves the synthetic-extra-component path used by the existing
test fixture.

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

Cascade specs use `register_cascade(target_component, time_offset, metric, description,
generator)` internally and simulate blast radius (auth → gateway, cache → DB,
DB → API/auth, MQ → API/DB, LLM → DB/cache/API). Cascades are single-row step writes
only — express ramps/sustained spans as primary specs in `primary_specs`, not in
`cascade_specs`.

### Scenario registry

`SCENARIOS: dict[str, Scenario]` holds every anomaly scenario in the catalog. There
are no legacy `anoms_*` module-level lists; all specs live in `Scenario` entries.
`_apply_scenarios()` in `main()` is the single point that populates
`component_anomalies` and `cascading_anomalies`. Each `Scenario` bundles:

- `id` — slug, must match the dict key.
- `name` — human-readable label.
- `severity ∈ {low, medium, high}` — controls which `--signal-level` activates it.
- `days_required ∈ {1, 7}` — minimum `--duration-days` for specs to be in range.
- `category` — free-form label for documentation/filtering.
- `components_touched` — subset of `COMPONENTS` keys; validated at import time.
- `primary_specs` — list of `(component, spec_dict)` pairs, same dict shape as the
  anomaly injection schema above.
- `cascade_specs` — list of `(target_component, cascade_dict)` pairs; each
  `cascade_dict` has `time_offset`, `metric`, `description`, and `generator`
  (no `shape`/`shape_params` — cascades are single-row steps).

`_resolve_scenarios()` applies the resolution pipeline:
allowlist (`--scenarios`) → exclusion (`--exclude-scenarios`) → severity filter
(`--signal-level`) → duration filter (`--duration-days`) → component filter
(`--components`). Scenarios dropped by severity or duration emit a stderr WARNING;
scenarios excluded silently by the component filter produce no output.

**RNG ordering invariant (with tiebreaker caveat)**: `generate_component()` calls
Python's stable `sorted()` on override specs with key `(row_idx, metric_name)`. For
specs that round to **distinct** `(row_idx, metric)` pairs, the declaration order
of `primary_specs` / `cascade_specs` does not affect the RNG draw sequence or CSV
content. However, when two specs collide on the same `(row_idx, metric)` — e.g.
two cascades that round to the same row at a coarse `--interval-seconds`, or a
cascade landing inside a shaped primary span — the stable sort preserves their
input order and the **last** writer wins for that cell. Reordering colliding
specs can therefore change RNG draws and CSV content; preserve declaration order
within a scenario unless you have verified no collisions exist.

**`--anomaly-count` ordering**: `_apply_signal_level_and_count()` flattens the
per-component dict in `COMPONENTS` order, walks each component's spec list in the
order produced by `_apply_scenarios()`, then appends cascades in their target
component's registry order. Two ordering axes therefore matter for stable
`--anomaly-count` sampling: (1) the order of `COMPONENTS` (the dict iteration
order at the top of the file), and (2) the order in which scenarios append into
each component's list — which is the SCENARIOS dict insertion order. Preserve
both unless you intentionally want to shift the cap selection for the same seed.

## Modifying the script

### Adding a new scenario

1. Choose a unique slug (lowercase, underscores). Pick `severity` and `days_required`
   to match when the scenario should fire:
   - `severity="medium"` (default) → fires under `--signal-level medium` and `high`
   - `severity="high"` → fires only under `--signal-level high`
   - `days_required=N` → minimum `--duration-days` at which any of this scenario's
     specs becomes in range. Set this to the day index (1-based) of the earliest
     `time_offset` across all primary and cascade specs. The
     `test_scenarios_days_required_valid` test will catch values set too high
     (which would silently drop in-range specs the legacy path emitted).

2. Add a `Scenario(...)` entry to `SCENARIOS` at the appropriate position (grouped by
   severity/category; new entries go after existing ones in the same group to avoid
   shifting the `--anomaly-count` sampling pool).

3. Populate `primary_specs` and `cascade_specs`:
   - Each primary spec is `(component, {time_offset, metric, description, generator,
     optionally duration_seconds/shape/shape_params})`.
   - Each cascade spec is `(target_component, {time_offset, metric, description,
     generator})` — no shape fields.
   - All referenced components must be keys of `COMPONENTS`; import-time validation
     (`_validate_scenarios_registry`) enforces this.

4. Set `components_touched` to the tuple of `COMPONENTS` keys (component names, not
   the scenario slug) referenced by any primary or cascade spec in this scenario.
   `test_scenarios_components_touched_matches_specs` asserts this set is exactly
   equal to the components actually referenced, so it acts as the
   `--components` filter index.

5. Run the test suite. The parametrized tests in `test_scenarios.py` and the
   coverage checks in `test_correctness.py` will catch missing/wrong specs
   automatically. No conftest changes are needed for a new scenario.

6. Update `README.md`'s scenario catalog table with the new slug, severity,
   `days_required`, and a one-line description.

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

A new component needs four lockstep entries in `anomaly-metric-creator.py` and two
in `tests/conftest.py`:

In `anomaly-metric-creator.py`:

1. `COMPONENTS[name]` — ordered `MetricSpec` list (up to `MAX_METRICS_PER_COMPONENT`).
2. `DEFAULT_METRICS_PER_COMPONENT[name]` — how many metrics the new component
   emits by default.

In `tests/conftest.py`:

3. `COMPONENT_FIELDS[name]` — total metric count (int). Drives
   `tests/test_registry.py` (component coverage, metric count) and several
   `tests/test_correctness.py` checks.
4. `DEFAULT_METRIC_COUNT[name]` — historic per-component default count. Drives
   `tests/test_cli.py::test_metrics_per_component_default_matches_legacy_columns`
   and the default-emitted-subset checks in `tests/test_correctness.py`.

To add anomalies for the new component, add `Scenario` entries to `SCENARIOS` that
reference it in `primary_specs` or `cascade_specs`, and list it in
`components_touched`. No imperative registration functions need to be touched.

Validation is split across import time and the test suite:

- **Import time** rejects key drift between `COMPONENTS` and
  `DEFAULT_METRICS_PER_COMPONENT`, any catalog longer than `MAX_METRICS_PER_COMPONENT`,
  any default count outside `[1, len(catalog)]`, and any scenario referencing a
  non-existent component. These raise a clear `ValueError` before `main()` runs.
- **Test suite only.** Drift between `COMPONENTS` and `COMPONENT_FIELDS` /
  `DEFAULT_METRIC_COUNT` is caught only by the test suite. Run it after adding or
  modifying a component — don't rely on import-time validation alone.

### Anomaly metric validation

`_filter_anomalies_for_emitted_metrics()` runs before generation and treats two
cases differently:

- Metric is in the full `COMPONENTS[component]` catalog but trimmed by
  `--metrics-per-component` → silently dropped (intended behavior of the cap).
- Metric (or component) is not in the full catalog → `ValueError`. This catches
  typos in scenario specs that would otherwise silently disappear from all outputs.

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
