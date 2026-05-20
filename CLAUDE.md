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

### Output directory hygiene

`main()` calls `_pre_clean_output_dir()` immediately after `args.output_dir.mkdir(...)`
and before any generation runs. The helper consumes the `_EMIT_ARTIFACT_FILES`
registry (plus the `_COMBINE_OUTPUT_FILENAME` slot) and deletes any file from
a prior run into the same directory that this run will not regenerate:
per-component CSVs for components no longer in `--components` or when
`metrics` is dropped from `--emit-selection`, `anomalies.csv` /
`metric_report.log` / `metric_traces.jsonl` / `gauges.csv` for emit types
not selected, and `combined_metrics_unified.csv` when `--combine` is off.
Idempotent on missing files; files unknown to this script (user notes, the
synthetic-extra-component CSV used by the combine autodiscovery fixture) are
left alone.

The end-of-run `Done - …` summary line is built from the same `args.emit_selection`
+ `args.combine` inputs, so it names exactly the artifacts written this run.

Do **not** call `_pre_clean_output_dir()` from the `--combine-only` branch — that
path reads existing per-component CSVs as inputs and pre-cleaning them would
remove the combine inputs. The early `return` in the `--combine-only` branch
already keeps it out of the cleanup path. `./otel-activity.log` lives outside
`--output-dir` and is append-only by design; it must stay outside the registry.

### Combine step

`combine_logs(input_dir, components=None)` joins the per-component CSVs in
`input_dir` into `combined_metrics_unified.csv`. When `components` is provided,
the unified output is restricted to that list verbatim (caller-controlled order,
missing per-component CSVs raise `SystemExit`); when omitted, every `*.csv` in
`input_dir` is autodiscovered (excluding the anomalies manifest, the long-form
`gauges.csv`, and prior combine outputs — see `_NON_COMPONENT_FILES`). `main()`
threads `--components` into both call sites (`--combine` and `--combine-only`)
so the combine output honors the same allowlist as generation,
`anomalies.csv`, reporting artifacts, and OTEL streaming. The default
`--components all` keeps autodiscovery active, which preserves the
synthetic-extra-component path used by the existing test fixture.

### Output schema document (`schema.json`)

`write_schema_json(output_path, *, components, effective_specs, metadata,
emitted_files)` writes a declarative `schema.json` alongside the rest of
the artifacts. It is opt-in via `schema` in `--emit-selection` (parallel
to `metrics`, `logs`, `traces`, `gauges`) and is the single source of
truth `--validate-output` consumes.

The document carries five slices of information:

- `schema_version` — integer (`SCHEMA_DOCUMENT_VERSION`, currently `2`
  after the VER-157 phase 7 bump that added the `topology` section).
  `_load_schema_document` rejects unknown versions outright, so v1
  documents fail-fast under a v2 reader and vice versa.
- `metadata` — run-level parameters (`seed`, `start`, `duration_days`,
  `interval_seconds`, `total_seconds`, `rows_per_component`,
  `drop_rate`, `signal_level`, `scenarios`, `exclude_scenarios`,
  `components`, `inject_dst_artifact_day`, `metrics_per_component`,
  `anomaly_count`, `emit_selection`, `combine`, `topology_mode`).
  `topology_mode` (`realistic` | `independent`) lets the validator
  short-circuit the new coupling check under `independent`.
- `components` — per-component metric metadata in MetricSpec column
  order (each entry carries `name`, `unit`, `semantic_type`, `dtype`,
  `min_value`, `max_value`, `derivation`).
- `files` — sorted list of artifact filenames the run wrote, built via
  `_collect_emitted_filenames` (the same registry that drives
  `_pre_clean_output_dir` and the end-of-run summary, so the three views
  cannot drift).
- `topology` (VER-157 phase 7) — the directed coupling graph snapshot,
  built from the live `TOPOLOGY` constant via `_serialize_topology` and
  restricted to the active component set. Shape:
  `{source: [{target, weight, saturation, correlation_threshold}, ...]}`
  with each source's edge list sorted by `target` for byte-deterministic
  output. Constant-weight edges serialize their numeric weight verbatim;
  callable-weight edges serialize the literal string `"callable"`
  (full reproducibility is a code concern — the schema only declares
  the coupling exists). `saturation` is either `null` or a
  `{midpoint, steepness, latency_gain, error_gain}` dict.
  `correlation_threshold` is a float or `null`. Sources whose
  source or every target was filtered out of `--components` are omitted
  so the validator does not try to correlate columns the run did not
  write. The validator's `_validate_topology_coupling` reads this
  section under `topology_mode == "realistic"`.

The output is byte-deterministic (`sort_keys=True`, fixed indent, UTF-8
with trailing newline). Locked SHA-256 golden hashes at 1d and 7d live
in `tests/test_schema_file.py` and were re-locked at the VER-157
phase 7 schema-version bump. The `--combine-only` branch does not
regenerate `schema.json` (it returns before pre-clean), matching the
`gauges.csv` invariant.

### Multi-instance fan-out (`--instances-per-component`, VER-140)

`COMPONENTS` declares one MetricSpec list per logical component, and
`INSTANCES` is the parallel module-level registry of `Instance`
objects that name each emitting *replica* (id, host, pod, az, region,
tenant). Phase 1 (VER-144) landed the `Instance` dataclass, the
`INSTANCES = {name: [Instance()] for name in COMPONENTS}` default,
`_validate_instances_registry` / `_validate_instance_list` import-time
checks, and the `RunContext.instances` thread that passes the per-run
per-component list into `generate_component(..., instances=...)`.
Phase 2 (VER-145) wired the CLI:

- `--instances-per-component N` (default `1`, range `[1,
  MAX_INSTANCES_PER_COMPONENT=20]`) — when `N > 1`, `main()` replaces
  `ctx.instances` with `{name: [Instance(id=f"i{k}", pod=f"pod-{k}")
  for k in range(N)] for name in COMPONENTS}` (host / az / region /
  tenant remain `None` in v1; Phase 3 will plug them in via
  `--instance-config PATH`). `N == 1` keeps the module-level
  anonymous-`Instance()` map and emits today's byte-identical
  output.
- `PREFLIGHT_CELL_CAP` now multiplies by `args.instances_per_component`
  so the same `--allow-huge-output` gate that catches metric-cell
  blowups catches instance-cell blowups too. The error message lists
  `--instances-per-component` alongside `--interval-seconds`,
  `--duration-days`, `--components`, and `--metrics-per-component`
  as the levers that can lower the estimate.

The long-form emission path inside `generate_component()` keys off
the *content* of the per-run instance list (not the CLI flag): a
single anonymous `Instance()` keeps the historic `timestamp,m0,…`
header and body; any other shape (`len(instances) > 1`, or a single
instance with any non-`None` dimension field) switches to the
long-form `timestamp,id,host,pod,az,region,tenant,<metrics…>`
header and writes one full row block per instance (all rows for
`instances[0]`, then all rows for `instances[1]`, …) — column
order is fixed and tested in `tests/test_instances_per_component.py`.
All instances share the same RNG-drawn natural values and the same
anomaly overrides in v1; Phase 4 (`instance_filter` on anomaly
specs) will let scenarios target individual instances.

Out-of-scope until later phases: `--instance-config PATH` (Phase 3),
per-anomaly `instance_filter` (Phase 4), instance dimensions in
`gauges.csv` / `combined_metrics_unified.csv` (Phase 5 / VER-148),
schema.json topology + `--validate-output` (Phase 8 / VER-151), and
OTEL resource attributes (Phase 6 / VER-149). Because those
downstream emitters are not dimension-aware yet, `parse_args`
rejects `--instances-per-component > 1` paired with `--combine`,
`--combine-only`, `--emit-selection 'gauges'`, `--emit-selection
'schema'`, `--validate-output`, `--otel-emit-gauges`, or
`--otel-enabled` with a phase-attributed error message (so users
see a clear failure instead of e.g. `gauges.csv` rows writing the
string `i0` into the numeric `value` column, or `--validate-output`
flagging dimension columns as schema drift). `combine_logs` adds a
second layer of defense against the two-pass bypass (generate first,
combine in a separate `--combine-only` invocation that defaults
back to `instances_per_component=1`): it inspects each
per-component CSV's header and refuses to combine any file whose
header contains the dimension columns (`id` / `host` / `pod` /
`az` / `region` / `tenant`) with the same VER-148 / Phase 5
message. `generate_component()` mirrors the DST guard inside the
helper as well — passing a non-anonymous instance list together
with `dst_inject_day > 0` raises `ValueError` even when the call
bypasses `parse_args`. Each later phase replaces the corresponding
gate with the real implementation. The single-instance default
(`N == 1`) keeps every flag combination historically permitted, so
existing one-instance workflows do not need to change.

Locked SHA-256 N=3 golden hashes at 1d and 7d live in
`tests/test_instances_per_component.py` (`N3_ONE_DAY_HASHES` /
`N3_SEVEN_DAY_HASHES`); `anomalies.csv` matches the default-run hash
because v1 records one event per `(timestamp, component, metric)`
regardless of `N` — Phase 4 will reshape that contract when
`instance_filter` lands.

### MetricSpec schema metadata (VER-139)

`MetricSpec` carries six optional declarative fields that flow into
`schema.json` and `--validate-output`: `unit`, `semantic_type`,
`min_value`, `max_value`, `dtype` (default `"float"`), `derivation`.
Five of the six are metadata-only and do not affect generation —
they exist only so the validator can range-check, dtype-check, and
recompute derived columns. `dtype` is the exception: under the
default `--topology-mode realistic` (VER-156 phase 6 flag day) every
column declared `dtype="int"` is rounded via `np.rint` in
`generate_component()` before derivations run and before the
`topology_capture` snapshot, so the recorded value is whole-integer
on disk. The deprecated `--topology-mode independent` alias skips
the cast (`apply_dtype_int_cast=False` in `main()`) to preserve
byte-for-byte parity with the pre-flag-day baseline, so `dtype="int"`
columns there are still emitted as fractional floats — the rounding
is a realistic-mode behavior, not a declarative-metadata behavior.
`_validate_metric_spec_schema_metadata` enforces the vocabulary at
import time (`semantic_type ∈ {counter, gauge, ratio, rate}`,
`dtype ∈ {float, int}`, finite numeric bounds,
`min_value <= max_value`).

Within `generate_component()` the cast runs after the anomaly-override
pass and *before* the derivation pass, so derived columns
(`cacheservice.hit_ratio`) consume rounded integer source cells and
match what the CSV writes. It also runs *before* the
`topology_capture` snapshot, so downstream coupling signals see the
same integer values the CSV records (cache miss ratios derived from
`cache_hits` / `cache_misses` are therefore computed from the
int-cast values, not the pre-cast floats; the qualitative behavior
is unchanged because the ratio is bounded in [0, 1] in either case).

After phase 6 the only known validator violation on default output is
the LLM context-overflow scenario driving `context_overflow_rate`
above its declared `max_value=1` (8.5 at day 5 + 2h, exercising the
context-window saturation pattern). That overshoot is a
scenario-catalog issue tracked for VER-141 phase 9 re-tune — it is
*not* the integer-cast bundle's scope and is intentionally left in
place by VER-156. Under `--topology-mode independent` the validator
additionally surfaces every previously-flagged fractional-int
violation (the alias intentionally skips the cast to keep its
pre-flag-day byte parity).

### Output validator (`--validate-output`)

`--validate-output PATH` runs the validator in a standalone mode (peer
of `--combine-only`) that loads `PATH/schema.json` and runs every check
the validator knows about against the artifacts in `PATH`:

- `_validate_required_files_present` — every declared file is on disk.
- `_validate_no_unknown_files` — every file on disk is declared (mirrors
  `_pre_clean_output_dir`'s registry intent; `schema.json` is always
  allowed even if undeclared so the validator can bootstrap).
- `_validate_anomalies_sorted` — `anomalies.csv` rows are non-decreasing
  by `timestamp`.
- `_validate_component_row_count` — data rows ≤ `rows_per_component`
  plus the DST splice extras when applicable; under-emission is checked
  against an 8-sigma band around the expected drop count.
- `_validate_component_timestamp_coverage` — every row's timestamp is in
  `[START, START + total_seconds)`.
- `_validate_component_cells` — header column order matches the schema's
  MetricSpec list; each cell parses as float, falls in
  `[min_value, max_value]` when declared, is whole-integer (modulo
  3-decimal CSV precision) when `dtype="int"`, and is ≥ 0 when
  `semantic_type` is `counter` or `rate`. Each unique
  `(metric, kind)` violation reports once per CSV so the output stays
  bounded.
- `_validate_component_derivations` — for every metric whose schema entry
  declares a `derivation`, recompute the value from its source columns
  and assert agreement within `_VALIDATE_DERIVATION_TOLERANCE` (0.01).
  Dispatched by `(component, metric)` via the `_RECOMPUTERS` table —
  add a `DERIVATIONS` entry (generator) and a `_RECOMPUTERS` entry
  (validator) in lockstep.
- `_validate_topology_coupling` (VER-157 phase 7) — for every edge in
  the schema's `topology` section with a numeric weight, compute the
  Pearson correlation between the source's canonical load metric and
  the target's canonical load metric (from `_TOPOLOGY_LOAD_METRICS`)
  and flag the edge when it falls below the per-edge threshold
  (`Edge.correlation_threshold`, defaulting to
  `_TOPOLOGY_DEFAULT_CORRELATION_THRESHOLD = 0.85`). Skipped silently
  under `metadata.topology_mode == "independent"` (which decouples by
  construction), when the schema has no `topology` block (older v1
  docs), on callable-weight edges (the per-row weight signal is the
  dominant contributor, not the upstream load), and when the aligned
  row count falls below 100 (narrow `--components` or coarse
  `--interval-seconds`). Anomaly windows from `anomalies.csv` are
  excluded via `_read_anomaly_exclusion_windows` and
  `_filter_windows_for_pair`: each `[span_start, span_end]` is padded
  by `_TOPOLOGY_CORRELATION_EXCLUSION_PAD_SECONDS = 30` and applied
  only to windows whose `(component, metric)` matches the source's
  canonical, the target's canonical, *or* any other upstream
  contributor's captured load columns (so an anomaly on
  `cacheservice.cache_misses` excludes the corresponding rows from
  the `apigateway -> database` correlation, since the cacheservice
  contribution is composed into `database.queries_per_sec` via the
  callable edge). A zero-variance source or target column is treated
  as a coupling regression (Pearson is undefined; the validator emits
  a violation naming the side).

CLI semantics: default mode hard-fails (`exit 1` on any violation);
`--validate-warn` downgrades to a stderr report and `exit 0`. Mutually
exclusive with `--combine` and `--combine-only`.

### Gauge metric file (`gauges.csv`)

`write_gauges_csv(component_csv_paths, output_path)` is the file peer of the
OTEL gauge stream (`stream_otel_gauges`). Both walk the same per-component
CSVs and merge them chronologically with `heapq.merge` on the parsed
timestamp; the file writer emits one row per
`(timestamp, component, metric, value)` tuple into a long-form `gauges.csv`.
Equal-timestamp ties tie-break on sorted component name (the writer sorts
`component_csv_paths` internally so the tiebreaker holds regardless of how
the caller built the dict), then per-component CSV column order
(`MetricSpec` order). Dropped CSV rows are absent from the file, the same
way `stream_otel_gauges` never sees them.

Parity with `stream_otel_gauges` has one intentional asymmetry: the file
writer passes raw cell strings through verbatim (so the byte hash never
depends on Python's `str(float)` repr), whereas `stream_otel_gauges`
`float(raw)`-coerces and silently skips unparseable cells. In practice
`generate_component` only writes finite floats, so both paths emit the
same data points — the difference only matters for hand-edited CSVs.

Both gauge paths are mutually exclusive with `--inject-dst-artifact-day > 0`
(the DST splice produces non-monotonic CSV timestamps that break
`heapq.merge`); the parser rejects the combination for both
`--otel-emit-gauges` and `--emit-selection gauges` up front.

`gauges.csv` is opt-in via `gauges` in `--emit-selection` (which the
parser enforces alongside `metrics`); `--combine-only` does not
regenerate it. The end-of-run `Done -` summary additionally prints
`Gauge rows written: N to gauges.csv` so a CI run records how many
data points landed in the file. Locked SHA-256 golden hashes at 1d and
7d live in `tests/test_gauges_file.py`.

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

### Derived metrics

Some columns are physically derived from siblings and must stay consistent with
them under every anomaly override. `generate_component()` enforces this after the
natural-value pass and the anomaly override loop, before rounding/formatting:

- `cacheservice.hit_ratio = 100 * cache_hits / (cache_hits + cache_misses)`
  (zero-denominator → 0). Anomalies that want to influence the cache hit ratio
  must therefore drive `cache_hits` and/or `cache_misses`, not `hit_ratio`
  directly; otherwise the override is silently overwritten by the derivation.

When adding a new derived-metric rule, keep it inside `generate_component()` so
the recomputation runs once per column, after every override has settled.

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

Production code does not call `register_cascade()`: `_apply_scenarios()` reads
each scenario's `cascade_specs` and appends them directly into the per-run
`RunContext.cascading_anomalies` dict. The `register_cascade(target_component,
time_offset, metric, description, generator, *, cascade_registry=…)` helper
exists for tests that need to build a cascade registry without composing a full
`Scenario`; callers must pass `cascade_registry=` explicitly (the module-level
registry was removed in VER-131). Cascades simulate blast radius (auth →
gateway, cache → DB, DB → API/auth, MQ → API/DB, LLM → DB/cache/API). Cascades
are single-row step writes only — express ramps/sustained spans as primary
specs in `primary_specs`, not in `cascade_specs`.

### Topology graph

`TOPOLOGY: dict[str, list[Edge]]` declares the directed service-call graph
alongside `COMPONENTS`. Phase 1 (VER-143) landed the constant and its
import-time validator; phase 2 (VER-152) added the
`--topology-mode realistic` consumer (see "Generation order" below)
that re-shapes downstream RPS baselines from upstream RPS columns. The
consumer was opt-in through phase 5 and flipped to the default in
phase 6 (VER-156); `--topology-mode independent` survives only as a
deprecation alias for pre-flag-day byte parity.
Phase 3 (VER-153) extended coupling to every front-half fan-out edge.
Phase 4 (VER-154) reads `Edge.saturation` and adds a logistic-shaped
latency multiplier and error offset onto each downstream's
latency-family and error-family `MetricSpec` (see "Saturation
feedback" below). Phase 5 (VER-155) closes the v1 graph by promoting
the `apigateway → llm_analytics` placeholder to a real coupling +
saturation edge so the LLM token-throttle reads as load-driven
saturation; see "LLM token-throttle" below for the decision to keep
apigateway as the metering authority instead of introducing a
synthetic `token_limiter` virtual node.

Two dataclasses model the edges:

- `Edge(target, weight=1.0, saturation=None, signal=None,
  correlation_threshold=None)` — frozen.
  `target` is a `COMPONENTS` key. `weight` is either a constant
  `float` (fan-out share, where the outgoing weights of a routing
  source sum to 1, or any non-negative scalar for amplification edges)
  or a callable `(np.ndarray) -> np.ndarray` that derives the per-row
  weight from a per-row scalar signal (e.g. cache-miss ratio driving
  the cache→database fan-out). `signal` is the per-edge
  `(dict[str, np.ndarray]) -> np.ndarray | None` callable that produces
  that scalar signal from the upstream's captured load columns;
  required iff `weight` is callable, must be `None` for constant
  `weight`. Returning `None` from `signal` means "skip this edge" so a
  `--metrics-per-component` trim of a required input column degrades
  gracefully. `correlation_threshold` is a validator-only override
  (VER-157 phase 7) for the minimum Pearson correlation
  `_validate_topology_coupling` requires between the source's
  canonical load metric and the target's canonical load metric; `None`
  (the default) falls back to
  `_TOPOLOGY_DEFAULT_CORRELATION_THRESHOLD = 0.85`. The field does
  not affect generation and is ignored on callable-weight edges
  (which are skipped by the coupling check). `_validate_topology()`
  smoke-tests every callable weight with a 3-element `np.ndarray` and
  probes every `signal` with a per-key captured-column dict built
  from `_TOPOLOGY_LOAD_METRICS` so a zero-arg / scalar-only lambda or
  a mis-shaped signal fails at import time rather than corrupting
  phase 2's vectorized column writes. The same validator rejects
  `correlation_threshold` values that aren't finite, that fall
  outside the half-open interval `(-1, 1]`, or are `bool`.
- `SaturationParams(midpoint, steepness, latency_gain=0.0, error_gain=0.0)`
  — frozen. Parameters of a logistic response curve consumed by
  `_apply_saturation()`. Zero gains declare the saturation
  point structurally without contributing to the target's metrics;
  after VER-155 phase 5 the v1 graph no longer has any zero-gain
  saturating edges, so the "structurally inert" branch only triggers
  for synthetic test edges.

The v1 graph (phase 1 declarations + phase 4/5 saturation tuning):

- `loadbalancer → apigateway` (constant weight `1.0`, saturation
  `midpoint=860, steepness=6, latency_gain=0.4, error_gain=0.010`).
- `apigateway → authservice` (`0.3`, saturation `midpoint=760,
  steepness=6, latency_gain=0.5, error_gain=0.012`).
- `apigateway → cacheservice` (`0.4`, saturation `midpoint=760,
  steepness=6, latency_gain=0.3, error_gain=0.008`).
- `apigateway → database` (`0.3`, saturation `midpoint=760,
  steepness=6, latency_gain=0.6, error_gain=0.015`).
- `apigateway → llm_analytics` (constant weight `1.0`, saturation
  `midpoint=760, steepness=6, latency_gain=0.55, error_gain=0.015`).
  VER-155 phase 5: under realistic mode, couples
  `llm_analytics.input_tokens_per_sec` to apigateway RPS, and lifts
  `avg_llm_latency_ms` / `p95_llm_latency_ms` / `llm_api_error_rate`
  as apigateway saturates the token budget.
- `cacheservice → database` — callable weight (cache-miss ratio); no
  saturation in v1.

**Generation order (`--topology-mode`).** `main()` walks
`args.components` in one of two orders depending on
`--topology-mode`:

- `realistic` (default since VER-156 phase 6) — topological order via
  `_topology_generation_order(args.components)`. Kahn's algorithm
  walks reverse-adjacency of `TOPOLOGY` restricted to
  `args.components`; ties break on `COMPONENTS` insertion order so
  the result is deterministic. As each component finishes,
  `generate_component()` stashes its post-natural / post-anomaly /
  post-derivation load-metric columns (pre-round; full float
  precision for `dtype="float"` columns, post-`np.rint` whole
  integers for `dtype="int"` columns after the VER-156 phase 6
  integer-cast bundle so the captured signal matches what the CSV
  emits) into a shared `upstream_arrays: dict[str, dict[str,
  np.ndarray]]` keyed by `(component_name, metric_name)`. The set
  of captured metrics per component is declared in
  `_TOPOLOGY_LOAD_METRICS` as a `(canonical, supplementary)` tuple:
  `canonical` is the load metric a constant-weight edge from the
  component reads; `supplementary` lists additional captured columns
  the component's outgoing edges' `Edge.signal` callables consume
  (e.g. cacheservice exposes `("cache_hits", ("cache_misses",))`).
  Before generating a downstream component,
  `_compose_topology_coupled_specs` rewrites each of the downstream's
  load metrics (canonical + supplementary) via the incoming edges:
  - **Constant-weight edges** — `contribution = (upstream /
    upstream_base) * downstream_base * w_norm` where the upstream
    column is the source component's *canonical* load metric and
    `w_norm = w / Σw` normalizes so the combined constant term
    equals `downstream_base` at natural upstream load. At least one
    constant-weight edge must have a non-zero captured upstream for
    this path to fire.
  - **Callable-weight edges** — each callable-weight `Edge` carries
    its own `signal: Callable[[dict[str, np.ndarray]], np.ndarray
    | None]` that derives a per-row scalar from the upstream's
    captured columns (e.g. the `cacheservice → database` edge uses
    the module-level `_cache_miss_ratio_signal` to compute
    `cache_misses / (cache_hits + cache_misses)`). The composer
    calls `edge.signal(upstream_cols)`; a `None` return means
    "skip this edge" (e.g. `--metrics-per-component` trimmed a
    required column). The composer then calls `edge.weight(signal)`
    to produce an additive contribution in downstream-metric units.
    `_validate_topology` enforces the pairing: callable weight
    requires a `signal`, constant weight forbids one, and the
    validator probes the `signal` with a captured-column dict
    built from `_TOPOLOGY_LOAD_METRICS[source]` so a mis-shaped
    signal fails at import time.
  The final coupled column is `constant_contrib + callable_contrib +
  rng.normal(0, _TOPOLOGY_COUPLE_NOISE_STD, n_rows)`. The original
  MetricSpec's declarative metadata (unit, semantic_type, min/max,
  dtype, derivation, clip_min) survives via `dataclasses.replace`;
  only `base`, `std`, `multiplier`, and `additive` change.
- `independent` (deprecation alias since VER-156 phase 6) — iteration
  order of `effective_specs`, which is `COMPONENTS` insertion order.
  No coupling, no upstream capture. Pre-flag-day baseline path kept
  only so the pre-VER-152 byte-for-byte output can be regenerated for
  diffing; emits a stderr `DeprecationWarning` on use and is scheduled
  for removal after VER-141 phase 9.

The realistic and independent modes share the same `RunContext.rng`,
but because the generation order differs every component's RNG draws
shift. Realistic-mode CSV bytes therefore do **not** match
independent-mode CSV bytes for any component — even uncoupled roots
like `loadbalancer`. All locked SHA-256 hashes in `tests/` were
re-baselined under realistic mode in VER-156 (phase 6 flag day);
tests pinning behavior under either mode now either use locked
hashes against realistic output or statistical assertions (means,
correlations, in-window values) that hold across both modes.

Anomaly overrides apply on top of the coupled baseline: the
two-pass pipeline (natural → anomaly overrides → derivations →
capture → round → drop → format) is unchanged inside
`generate_component()`, so a scenario primary on
`apigateway.requests_per_sec` still rewrites the cell at its row
index after coupling has set the baseline.

**Cascade-vs-topology overlap.** Several `SCENARIOS` already encode
pairwise blast-radius via `cascade_specs` (auth → gateway, cache → DB,
DB → API/auth, MQ → API/DB, LLM → DB/cache/API). The topology graph is
an orthogonal structural view: it describes *normal* request flow, not
anomaly propagation, so the two are intentionally allowed to overlap.
Cascades remain the path for "metric X drops at exactly row Y"
behaviors; topology is the path for "load on source raises the
downstream baseline" (phase 2/3) and "load on source elevates
downstream latency + error rate" (phase 4 saturation). Phase 3
(VER-153) expanded coupling to all front-half fan-out edges, so
`authservice.login_attempts`, `cacheservice.cache_hits/cache_misses`,
and `database.queries_per_sec` are all coupled under realistic mode.
Phase 4 (VER-154) extends realistic mode to latency and error
columns: cascade overrides (`error_rate`, latency, `cpu_util_pct`)
now share the same column space as the saturation offset, but the
cascade override path *replaces* the cell at the targeted row (post
saturation, since the override is applied after the natural-column
build), so the cascade value still wins at exactly that row.

**Phase 9 (VER-159) catalog re-tune.** The saturation lift from phase 4/5 raised the column-wide std of `apigateway.error_rate` (from ~0.018 to ~0.040) and `authservice.error_rate` (from ~0.018 to ~0.050), pushing eleven hand-tuned cascade and primary generator values close to or below the new noise floor. Those eleven specs (8 from the initial audit + 3 surfaced by the regression test) were re-tuned in VER-159 to clear the floor by >3σ under realistic mode: `api_cpu_saturation` (primary 0.25), `db_stall` (primary 0.35, cascade 0.30), `lb_flapping` (cascade 0.30), `mq_jam` (primary 0.25), `vectorstore_pressure` (cascade 0.15), `payment_5xx` (cascade 0.28), `regional_failover_storm` (cascade 0.40), `llm_provider_outage` (cascade 0.35), `storage_layer_pressure` (cascade 0.30), and `network_partition_az_split` (cascade 0.40). `tests/test_scenario_deviation.py` is the regression guard: it walks every `SCENARIOS` entry under realistic mode, compares the active CSV against an `--exclude-scenarios <slug>` baseline run that fires zero anomalies, and asserts every recorded `anomalies.csv` row deviates by >1σ. A future saturation re-tune or new edge that quietly lifts a
column's std past a generator's headroom will fail this test on the
specific row that no-ops.

No `SCENARIOS` cascades were structurally removed in VER-159 —
they are kept in place per the VER-134 decision even where the
saturation feedback would now produce a similar downstream effect.
The cascade override is a single-row step write applied *after*
saturation, so it still pins the targeted cell to a specific value
regardless of upstream load; saturation only lifts the surrounding
band. Cascades that target `error_rate` on `apigateway` or
`authservice` are the most overlap-prone (the saturation curve also
elevates `error_rate` on those components under load) but remain
distinguishable: the cascade override produces a sharp step at the
recorded row, while saturation produces a smooth load-shaped band
underneath it.

### Saturation feedback (`--topology-mode realistic`, phase 4)

Each saturating edge (`Edge.saturation is not None` and at least one
non-zero gain) contributes a logistic-shaped response to its downstream
component, computed by `_apply_saturation(upstream_load, sat)`:

```
utilization        = max(upstream_load, 0) / sat.midpoint
                     clipped to [0, _SATURATION_MAX_UTILIZATION]
logistic           = 1 / (1 + exp(-sat.steepness * (utilization - 1)))
latency_multiplier = 1 + sat.latency_gain * logistic
error_offset       = sat.error_gain * logistic
```

`upstream_load` is the *upstream* component's primary load column
captured in `upstream_arrays` (per `_TOPOLOGY_LOAD_METRICS`); the
downstream's own load is still being assembled at saturation time, so
the curve cannot read it directly. The utilization clamp keeps
`np.exp` numerically stable for arbitrary load magnitudes (logistic
already exceeds 0.99 at utilization = 2 with steepness = 5, so a 5x
cap has no practical effect on the shape).

`_TOPOLOGY_SATURATION_TARGETS[downstream]` declares which of the
downstream's metrics receive the saturation effect:

- `apigateway` → latency `avg_response_time_ms`, `backend_latency_ms`;
  error `error_rate`.
- `authservice` → latency `avg_auth_latency_ms`; error `error_rate`.
- `cacheservice` → latency `avg_cache_latency_ms`; error `error_rate`.
- `database` → latency `read_latency_ms`, `write_latency_ms`; error
  `error_rate`.
- `llm_analytics` → latency `avg_llm_latency_ms`, `p95_llm_latency_ms`;
  error `llm_api_error_rate` (the LLM-specific error column the
  catalog exposes, not the generic `error_rate`). Phase 5 (VER-155).

`_compose_topology_saturation_specs(component, specs, upstream_arrays,
n_rows)` runs immediately after `_compose_topology_coupled_specs` in
the realistic-mode generation loop. It sums incoming saturating
contributions — multiplicatively for the latency factor (each edge
layers an additional load-dependent slowdown) and additively for the
error offset (each edge contributes its own failure surface) — then
composes the resulting per-row arrays on top of the metric's existing
`multiplier` / `additive` via lambda closures. The natural seasonal
patterns (e.g. `_daily_sine`, `_llm_business_hours`) therefore stay
visible underneath the saturation curve. Only `multiplier` and
`additive` change; `std`, `clip_min`, and the declarative schema
metadata pass through unchanged.

**Tuning rationale (per-edge).** Midpoints are set to ~80% of each
upstream's natural peak load (`base + ~3σ`):

- `loadbalancer → apigateway`: loadbalancer base = 900 rps, peak
  ≈ 1080, midpoint = 860 → utilization ~1.05 at natural load
  (logistic ~0.6 with steepness = 6).
- `apigateway → {authservice, cacheservice, database}`: apigateway
  base = 800 rps, peak ≈ 950, midpoint = 760 → same shape.

`latency_gain` scales with each downstream's sensitivity: `database`
gets the largest (`0.6`, heavy I/O), `authservice` next (`0.5`,
per-request crypto work), `apigateway` (`0.4`, request routing),
`cacheservice` smallest (`0.3`, in-memory ops). `error_gain` follows
the same ordering, kept inside `[0.005, 0.02]` so the saturation
offset alone cannot push `error_rate` above 1.0 (worst case
`base + 4σ + error_gain` stays well below the declared
`max_value=1`).

**Bounds and cap tests.** `latency_multiplier ∈ [1, 1 + latency_gain]`
(always positive given non-negative gains; latency never flips sign);
`error_offset ∈ [0, error_gain]` (bounded by the per-edge gain so the
saturation contribution alone cannot exceed the gain). End-to-end
tests in `tests/test_topology_saturation.py` assert both invariants on
the realized CSV columns.

The deprecated `--topology-mode independent` alias never invokes
`_compose_topology_saturation_specs`, so its output stays byte-for-byte
identical to the pre-VER-154 baseline (pinned alongside the broader
pre-flag-day baseline via `LEGACY_INDEPENDENT_ONE_DAY_HASHES` in
`tests/test_scenarios.py` and `tests/test_topology_loadbalancer_gateway.py`).
The no-flag default and explicit `--topology-mode realistic` now produce
identical latency CSV bytes; that invariant is pinned by
`tests/test_topology_saturation.py::test_realistic_mode_latency_csvs_byte_identical_to_default`.

### LLM token-throttle (`--topology-mode realistic`, phase 5)

Phase 5 (VER-155) closes the v1 topology graph by promoting the
phase-1 `apigateway → llm_analytics` placeholder into a real
coupling + saturation edge. The edge sits inside the same
phase-3 / phase-4 machinery as the front-half fan-out — no new
generator branch, no new validator, no new file format.

**Decision: no synthetic `token_limiter` virtual node.** The issue
left the upstream choice open (apigateway vs. a synthetic
`token_limiter` node that does not appear in `COMPONENTS`).
Apigateway is the natural metering authority for LLM-bound traffic
in the v1 graph: every LLM call enters the system through it, so its
RPS is a faithful proxy for the token budget being consumed. A
virtual node would require `_validate_topology` to accept upstream
keys outside `COMPONENTS`, would not produce any observable column
of its own, and would not improve the saturation shape (apigateway
RPS already drives every front-half edge). The synthetic-node path
is documented here only to record the decision; revisit it if a
future LLM scenario needs token-counting behavior independent of
apigateway throughput.

**Coupling.** `_TOPOLOGY_LOAD_METRICS["llm_analytics"] =
("input_tokens_per_sec", ())` makes `input_tokens_per_sec` the
canonical load metric for the LLM (no supplementary captures; the
canonical-shape `(canonical_metric, supplementary_tuple)` rule is
preserved). The
edge weight is positive (`1.0`); the per-downstream renormalization
in `_compose_topology_coupled_specs` collapses single-incoming
edges to `w_norm = 1.0`, so any positive weight is structurally
equivalent. Token throughput is the right unit here (not request
rate): the token budget governs tokens/second, not requests/second,
and the larger downstream baseline (25 000 tokens/s vs. 45
requests/s for the LLM RPS) keeps the upstream-driven signal well
above the absolute coupling noise floor
(`_TOPOLOGY_COUPLE_NOISE_STD = 5.0`) and clears the issue's
`>= 0.85 Pearson` correlation gate against
`apigateway.requests_per_sec` on the 1-day default seed.

**Saturation.** The edge's `SaturationParams` sit in the same
phase-4 issue ranges as the other front-half edges (`midpoint=760`
in apigateway RPS units, `steepness=6`, `latency_gain=0.55` between
authservice 0.5 and database 0.6, `error_gain=0.015` inside
`[0.005, 0.02]`). `_TOPOLOGY_SATURATION_TARGETS["llm_analytics"]`
covers both the default-emitted `avg_llm_latency_ms` and the
supplemental `p95_llm_latency_ms`; the additive error offset goes
onto `llm_api_error_rate` (the LLM-specific error column the
catalog exposes — not the generic `error_rate`, which
`llm_analytics` does not declare).

**Tests.** `tests/test_topology_llm.py` pins:

- structural invariants on the `apigateway → llm_analytics` edge
  (active positive weight, non-zero gains, ranges);
- registry entries in `_TOPOLOGY_LOAD_METRICS` and
  `_TOPOLOGY_SATURATION_TARGETS`;
- `>= 0.85 Pearson` correlation between
  `apigateway.requests_per_sec` and
  `llm_analytics.input_tokens_per_sec` in realistic mode;
- realistic-mode mean lifts for `avg_llm_latency_ms`,
  `p95_llm_latency_ms` (under `--metrics-per-component 10`), and
  `llm_api_error_rate` against the independent-mode baseline;
- caps (latency non-negative, error rate `<= 1.0`);
- LLM scenarios still fire under realistic mode (no anomaly cell
  overrides are masked by the coupling); and
- `llm_analytics.csv` byte-identity between the no-flag default and an
  explicit `--topology-mode realistic` run (after VER-156 phase 6 the
  default is realistic; the deprecation alias's pre-flag-day parity
  lives in `tests/test_topology_loadbalancer_gateway.py`).

`_validate_topology()` rejects, at import time: unknown source keys,
non-`list` edge containers, non-`Edge` entries, edge targets outside
`COMPONENTS`, callable weights that fail to accept an `ndarray` or
return something other than an `ndarray`, constant weights that
are not finite, non-negative `int`/`float` scalars (`bool` is
rejected explicitly because it is an `int` subclass), callable
weights paired with `signal=None` (or a non-callable `signal`),
constant weights paired with a non-`None` `signal`, `signal`
callables that raise on the captured-column probe, `signal`
callables that return something other than `np.ndarray` or `None`,
and any cycle in the directed `TOPOLOGY` graph (including
self-loops).

Each non-`None` `Edge.saturation` is also validated at import time via
the shared `_validate_saturation_params(sat, *, context=…)` helper:
`midpoint` and `steepness` must be finite positive non-`bool`
`int`/`float`; `latency_gain` and `error_gain` must be finite
non-negative non-`bool` `int`/`float`. `_apply_saturation()` re-runs
the same check at call time so direct callers (tests, future
consumers) cannot smuggle in `NaN`/`inf`/`bool`/negative values.

Mirror these invariants in `tests/test_topology_registry.py` when
adding new edges or constraints.

### Multi-instance fan-out (VER-140)

`Instance` is a frozen dataclass holding six optional dimension
fields (`id`, `host`, `pod`, `az`, `region`, `tenant`). The active
per-run map lives on `RunContext.instances: dict[str, list[Instance]]`
and is consumed by `generate_component()`: when the list is a single
anonymous `Instance()` (all fields `None`), CSV output is byte-
identical to the pre-Phase-1 baseline (no dimension columns); when
the list has named instances or `len > 1`, every per-component CSV
gains a `(id, host, pod, az, region, tenant)` prefix block and the
row count multiplies by the per-component instance count.

Three flag paths populate `ctx.instances` in `main()` (mutually
exclusive at parse time):

- `--instance-config` absent and `--instances-per-component 1`
  (default) → `{name: list(INSTANCES[name]) for name in COMPONENTS}`,
  where the module-level `INSTANCES[name]` defaults to
  `[Instance()]`. Today's byte-identical output path.
- `--instances-per-component N` (N in `[1, MAX_INSTANCES_PER_COMPONENT]`,
  `MAX_INSTANCES_PER_COMPONENT = 20`) → every component fans out to
  the same `[Instance(id=f"i{k}", pod=f"pod-{k}") for k in range(N)]`.
- `--instance-config PATH` (Phase 3, VER-146) → per-component fan-out
  is loaded from a YAML (`.yaml`/`.yml`) or JSON (`.json`) file via
  `_load_instance_config(path)`. The file's top-level `components`
  map keys components to lists of `Instance`-field dicts; components
  *not* listed fall back to `list(INSTANCES[name])` (anonymous
  default), so a partial config keeps untouched components on the
  byte-identical path.

`_load_instance_config(path)` validates loudly at parse time and
raises `ValueError` (caught in `main()` and re-raised via
`sys.exit`) for every schema violation: top-level value not a
mapping, missing `components` key, unknown component name (must be
in `COMPONENTS`), per-component value not a list, empty per-component
list, per-component count exceeding `MAX_INSTANCES_PER_COMPONENT`,
non-dict entry, unknown `Instance` field, and duplicate `Instance.id`
within a component (the last check is delegated to
`_validate_instance_list`). The `parse_args` rejects the flag pair
early — `--instance-config` and `--instances-per-component` share an
`argparse` mutually-exclusive group — and additionally rejects file
paths that do not exist or whose suffix is outside
`{.yaml, .yml, .json}`.

PyYAML is an *optional* runtime dependency: the YAML branch imports
it lazily inside `_load_instance_config` and raises a clear
"install with `pip install pyyaml`" error on `ImportError`. JSON
configs work with the stdlib. The `[yaml]` extra in `pyproject.toml`
declares the dependency for users who want YAML support; the `dev`
extra always pulls it in so the test suite can exercise both
formats.

Both multi-instance paths (`--instances-per-component > 1` and
`--instance-config`) are mutually exclusive with
`--inject-dst-artifact-day > 0`: the DST splice produces
non-monotonic timestamps that the multi-instance row builder is not
prepared for, and `parse_args` rejects the combination with a clear
message naming the active flag.

When adding fields to `Instance`, mirror them in three places: (1)
`_valid_instance_fields` in `_load_instance_config` so the config
loader accepts the new key, (2) the YAML/JSON example in the
README CLI table row, and (3) `_validate_instance_list` if the new
field needs uniqueness or shape checks.

### Scenario registry

`SCENARIOS: dict[str, Scenario]` holds every anomaly scenario in the catalog. There
are no legacy `anoms_*` module-level lists; all specs live in `Scenario` entries.
`_apply_scenarios()` in `main()` is the single point that populates
`component_anomalies` and `cascading_anomalies`. Each `Scenario` bundles:

- `id` — slug, must match the dict key.
- `name` — human-readable label.
- `severity ∈ {low, medium, high}` — controls which `--signal-level` activates it.
- `days_required` (positive int) — minimum `--duration-days` at which any of
  the scenario's specs becomes in range. Must equal the day index (1-based) of
  the earliest `time_offset` across all primary and cascade specs;
  `_validate_scenarios_registry` enforces this equality at import time
  (`test_scenarios_days_required_valid` mirrors the same invariant).
- `category` — free-form label for documentation/filtering.
- `components_touched` — must equal exactly the set of components referenced
  by `primary_specs` + `cascade_specs`; `_validate_scenarios_registry`
  enforces this at import time
  (`test_scenarios_components_touched_matches_specs` mirrors the same invariant).
- `primary_specs` — list of `(component, spec_dict)` pairs, same dict shape as the
  anomaly injection schema above.
- `cascade_specs` — list of `(target_component, cascade_dict)` pairs; each
  `cascade_dict` has `time_offset`, `metric`, `description`, and `generator`
  (no `shape`/`shape_params` — cascades are single-row steps).

Every primary and cascade spec is schema-checked at import time by
`_validate_scenario_spec()` (called from `_validate_scenarios_registry`):
required keys present, `metric` in the full `COMPONENTS[component]` catalog,
`generator` callable, `time_offset` a finite non-negative non-bool
`int`/`float`, `description` a non-empty string, `shape` a string in
`_VALID_ANOMALY_SHAPES`, `duration_seconds` a finite non-negative non-bool
numeric, `shape_params` a dict; cascade specs reject
`shape`/`duration_seconds`/`shape_params` outright.

Generator dispatch rule: the runtime calls each generator with one of
two canonical positional shapes per path, chosen by the generator's
**required** positional count (defaults extend capacity but do not change
the call shape):

- **Step path** (cascades + primary step specs without positive
  `duration_seconds`; note: a spec with `duration_seconds == 0` is still
  the step path):
  - `required_positional == 3` → call as `(ts, col, rng)`
  - `required_positional <= 2` → call as `(ts, col)`; any default
    positional params keep their declared defaults
  - `*args` with `fixed_positional_count <= 2` → call as
    `(ts, col, rng)` (`*args` absorbs position 3)
  - `*args` with `fixed_positional_count == 3` and
    `required_positional == 3` (i.e. `(ts, col, rng, *args)`) → call as
    `(ts, col, rng)` (positions 1–3 fill required, `*args` empty)
- **Span path** (primary specs with `shape != "step"` or
  positive `duration_seconds`):
  - `required_positional == 5` → call as
    `(ts, col, t_within, span_idx, rng)`
  - `required_positional <= 2` → call as `(ts, col)`; any default
    positional params keep their declared defaults
  - `*args` with `fixed_positional_count <= 2` → call as
    `(ts, col, t_within, span_idx, rng)` (`*args` absorbs positions 3–5)
  - `*args` with `fixed_positional_count == 5` and
    `required_positional == 5` → call as
    `(ts, col, t_within, span_idx, rng)`

`*args` is rejected when its fixed-positional prefix would cause a
silent misbind. Two distinct misbind cases the validator and
dispatchers both reject:

- **Default-overwrite case** — `required_positional <= 2` with
  `fixed_positional_count > 2`. Example: `(ts, col, scale=1.0, *args)`
  on either path. The target-arity call would overwrite the author's
  declared default at position 3 (step) or positions 3–min(fixed,5)
  (span) before the rest flows into `*args`.
- **Required-misbind case** (span path only) — `required_positional`
  in `{3, 4}` with `*args`. Example: `(ts, col, rng, *args)` on a span
  spec. The 5-arg call would bind `t_within` into the required `rng`
  slot. (Step path with `required_positional == 3` is the canonical
  shape, so this case only applies to span.)

Move any extra parameters after `*args` (kwarg-only with defaults)
instead.

Intermediate 3- and 4-arg span calls and 3-arg span calls for non-`*args`
generators are never attempted: those shapes were the silent-misbind
vector (a primary spec like `(ts, col, rng)` on a span path would have
had `t_within` bound to its `rng` parameter). The validator's
generator-arity rule rejects any generator whose required positional
count is incompatible with the path's two canonical shapes; see
`_validate_scenario_spec` for the full rule and the corresponding tests
in `tests/test_scenarios.py`.

`_resolve_scenarios()` applies the resolution pipeline:
allowlist (`--scenarios`) → exclusion (`--exclude-scenarios`) → severity filter
(`--signal-level`) → duration filter (`--duration-days`) → component filter
(`--components`). Scenarios dropped by severity or duration emit a stderr WARNING;
scenarios excluded silently by the component filter produce no output.

**RNG**: The RNG is an `np.random.RandomState(seed)` instance created in `main()` and
carried as `RunContext.rng`, passed explicitly through `generate_component()`,
`_natural_column()`, and the anomaly override path. Draw order is identical to the
former global `np.random.seed()` + module-level functions (MT19937 + Box-Muller), so
no locked SHA-256 hashes changed. The module-level `anomalies` list and
`cascading_anomalies` dict have been removed; all per-run state lives in `RunContext`.

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
     `time_offset` across all primary and cascade specs. `_validate_scenarios_registry`
     rejects any other value at import time (and
     `test_scenarios_days_required_valid` mirrors the invariant).

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
   `_validate_scenarios_registry` rejects any drift (missing or extra entries)
   at import time, so the tuple acts as the authoritative `--components` filter
   index (`test_scenarios_components_touched_matches_specs` mirrors the
   invariant).

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

A new component needs two lockstep entries in `anomaly-metric-creator.py` and two
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

- **Import time** rejects:
  - Key drift between `COMPONENTS` and `DEFAULT_METRICS_PER_COMPONENT`.
  - Any catalog longer than `MAX_METRICS_PER_COMPONENT`.
  - Any default count outside `[1, len(catalog)]`.
  - Any scenario referencing a non-existent component.
  - Any `days_required` that does not equal the day index (1-based) of
    the earliest spec offset.
  - Any `components_touched` tuple that does not equal the set of
    components actually referenced by the scenario's primary and
    cascade specs.
  - Any non-string severity, or severity outside `{low, medium, high}`,
    on a scenario, primary spec, or cascade spec.
  - **Per-spec schema drift** (via `_validate_scenario_spec`): non-dict
    specs; missing required keys (`time_offset`, `metric`, `description`,
    `generator`); non-string or unknown metric (rejected against the
    full `COMPONENTS[component]` catalog, not the trimmed default); non-
    callable generator; non-finite, non-numeric, negative, or boolean
    `time_offset`; non-string or empty `description`; non-string or
    unknown `shape`; non-numeric, non-finite, negative, or boolean
    `duration_seconds`; non-dict `shape_params`; cascade specs
    declaring `shape`/`duration_seconds`/`shape_params`.
  - **Generator arity drift** (also via `_validate_scenario_spec`):
    generators with required keyword-only parameters; generators whose
    `required_positional` / `max_positional` shape doesn't match the
    canonical 2-arg or path-target form (3 for step, 5 for span) per
    the dispatch rule above.

  All of these raise a clear `ValueError` naming the scenario slug and
  the offending field before `main()` runs.
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

## Pre-PR checklist (required before marking a PR ready for review)

This checklist maps to the 11 recurring patterns identified in VER-160. Work through each bold heading before marking the PR ready for review (i.e. before removing draft status). Copy those 11 bold headings into the PR description as a checklist (Markdown `- [ ]` lines, one per heading) and either confirm each one or write "N/A — _reason_". The bullets under each heading are guidance for what to verify, not additional checklist entries to copy verbatim. This file is the canonical source for the checklist; if a `.github/PULL_REQUEST_TEMPLATE.md` is added later to prefill the same items on every new PR, it should mirror the headings below rather than redefine them.

**Scope & description**
- PR description names every behavior change in the diff — RNG model, registries, module-level state, default-output bytes, public-helper signatures, CLI/env semantics, doc surface. If the diff is broader than the description, either split the PR or update the description.
- If the diff touches RNG, `RunContext`, registries, or any module-level state, the description calls it out explicitly and the test plan covers determinism.

**Validators and schema checks**
- For every field a new validator inspects, enumerate non-canonical inputs: `None`, `NaN`, `±inf`, negative, `bool` (a subtype of `int`), empty string, unhashable, wrong container type.
- Every *branch* of a discriminator is validated: callable **and** constant `Edge.weight`; cascade **and** primary specs; step **and** span paths; `*args` **and** fixed-arity callables.
- Dispatch tables (`_RECOMPUTERS`, `DERIVATIONS`, etc.) raise on unknown keys; never return `None` or fall through silently. If a caller genuinely needs to tolerate misses, the *caller* opts in via `try/except KeyError` — the table itself stays strict. Concrete antipatterns to grep for before review:
  - `table.get(key)` on a dispatch table — returns `None` on miss instead of raising. Use `table[key]` so a typo or registry drift fails loudly. The VER-179 fix replaced `_RECOMPUTERS.get(component)` with `_RECOMPUTERS[component]` for exactly this reason.
  - A dispatcher *function* (e.g. `_recompute_cacheservice`) that returns a sentinel — `None`, an empty string, or a "soft violation" message — for an unrecognized metric or component instead of raising `KeyError`. The caller cannot distinguish "metric is fine" from "I have no recomputer for this metric"; both look like success. VER-179 also fixed this shape by replacing the soft-violation return with `raise KeyError(...)`.
  - A dispatcher branch that silently falls through to a `return` at the bottom of the function when no `if`/`elif` matched. Add an explicit `raise KeyError(...)` instead.

**Doc / docstring sync**
- Every changed function with a docstring has its docstring updated in this diff.
- Grep every changed symbol name against CLAUDE.md and README.md and update prose that describes it.
- If a public helper was removed or repurposed, CLAUDE.md prose is updated in the same diff.

**Single source of truth**
- No hand-rolled emit→filename, metric→component, or component→derivation maps alongside a canonical registry. Every consumer reads from `_EMIT_ARTIFACT_FILES`, `COMPONENTS`, `DERIVATIONS`, etc.
- `_COMBINE_OUTPUT_FILENAME` is used by the actual combine writer, not only the cleanup/summary path.

**Completeness**
- PR title implies a class of fix (e.g. "add `clip_min` to non-negative metrics") → grep for all instances and confirm coverage.

**Mode / flag combinations**
- List every other CLI flag, env var, and `--emit-selection` token that interacts with the new flag. Gate invalid combinations in `parse_args` with a clear message, or add a test.
- New `parse_args` checks must not spuriously reject `--combine-only` or non-default `--emit-selection` invocations.

**Test path determinism**
- Every new code path has a test whose input deterministically exercises that path (no reliance on "the default seed happens to do X").
- Each new CLI flag is covered in isolation, not only in the most-permissive bundle.
- If `expected` is derived from a registry (e.g. `{m for m in COMPONENTS[c] if pred(m)}`, `{e.target for e in TOPOLOGY[s]}`, or a comprehension over `SCENARIOS`), assert `len(expected) > 0` (or the moral equivalent — `assert expected`, `assert expected_count > 0`) *before* the membership/equality check. An empty `expected` makes the downstream check trivially pass in several shapes:
  - `assert expected.issubset(actual)` / `assert expected <= actual` — `∅ ⊆ actual` is always true.
  - `for m in expected: assert <property>(m)` — zero iterations, asserts nothing.
  - `assert actual == expected` — passes whenever `actual` also happens to be empty; the test claims "actual matches registry" but really claims "both are empty".
  - `assert expected & actual == expected` and `assert actual.issuperset(expected)` — collapse to `∅ == ∅` / `actual ⊇ ∅`, both always true.
  - Three of four vacuous-test bugs on PR #50 had this exact shape: a registry filter (`if metric.dtype == "int"`, `if "ratio" in name`, etc.) excluded every candidate under the default catalog, so `expected` was empty, so the assertion ran on nothing. The non-empty guard catches the filter regression at test time instead of letting the test silently rot.
  - When the test legitimately needs `expected` to be empty for some inputs (rare), assert that *condition* explicitly and gate the membership check behind it, so a future registry change that makes `expected` accidentally empty under *different* inputs still trips the guard.

**Performance in hot paths**
- No per-row re-parsing of strings or re-computation of constants that could be hoisted above the loop.
- No broad `try/except` in a per-row loop where the body has side effects such as RNG draws.

**Action order in user-facing output**
- The end-of-run `Done - … written to …` summary line only names artifacts the run actually wrote, and is printed only after every writer it names has completed successfully.

**Test hygiene**
- New test files have no unused imports or unused helpers. The
  `.pre-commit-config.yaml` ruff hook enforces this on `tests/` using the rule
  selection in `pyproject.toml` (`[tool.ruff.lint] select = ["F401"]`); run
  `.venv/bin/pre-commit run --all-files` or `.venv/bin/ruff check tests/`
  locally if the commit hook is not installed.

**Default-behavior changes**
- If a default parameter value or fallback path changes (e.g. unseeded `RandomState`, required arg replacing optional), the PR description names it and tests cover both old and new caller shapes.

### Reviewer-before-ready gate

The Code Reviewer agent signs off in the worktree *before* the PR is marked ready for review on GitHub (i.e. before draft status is removed). Pushing the draft branch is fine — and required by step 1 — what this gate blocks is the draft → ready transition. The workflow is structurally enforced by Paperclip:

1. Implementing agent opens the PR as a **draft**.
2. Implementing agent marks the tracking issue `in_review`. (Paperclip automatically attaches an execution policy with a Code Reviewer stage on every issue checkout, so the implementing agent does not manually assign the reviewer).
3. Code Reviewer walks the pre-PR checklist, fixes any issues in the same worktree, then marks the PR ready (removes draft status) and submits an Approve decision to Paperclip, which automatically hands back to the implementing agent or advances the workflow.
4. PRs that go directly to `gh pr create` without the draft+reviewer step skip steps 1–3, but must pass the pre-PR checklist self-attestation before being marked ready.

This process avoids the Copilot round-trip: issues caught by the Code Reviewer in step 3 are fixed before Copilot's first review, not after. Paperclip's execution policy is the structural backstop — added after a same-day PR bypassed the documented gate and required five fix rounds — ensuring the Code Reviewer must explicitly sign off before the issue can transition to `done`.

## Tests

Tests live in `tests/` and write only into `tmp_path` (never `iot_logs/`). The suite
runs full 1-day and 7-day generations end-to-end via `main()` and exercises the
vectorized `generate_component()` path. Run with `.venv/bin/pytest` after installing
the `dev` extra (see [README.md](README.md#tests)).

The canonical scenario catalog — slugs, severities, `days_required`, and
`components_touched` — lives in the [README scenario catalog](README.md#scenario-catalog)
table. Tests should be derived from `amc.SCENARIOS` (and parametrized off it where
practical) rather than hard-coding slug lists, so new scenarios are automatically
covered without test edits.

### Scenario selector test layout

The `--scenarios` / `--exclude-scenarios` selector matrix is covered across three
test files:

- `tests/test_args.py` — `parse_args`-only coverage: defaults, case-insensitivity,
  whitespace tolerance, single-slug / multi-slug parsing, unknown-slug rejection.
- `tests/test_scenarios.py` — in-process composition matrix:
  - `test_compose_scenarios_x_signal_level_*` — severity gate drops the slug and
    emits exactly one stderr WARNING per dropped slug.
  - `test_compose_scenarios_x_duration_days_*` — duration gate drops the slug and
    emits exactly one stderr WARNING per dropped slug.
  - `test_compose_scenarios_x_components_*` — `components_touched` ∩ `--components`
    determines survival; disjoint drops are silent (no WARNING).
  - `test_compose_scenarios_x_exclude_scenarios_*` — exclusion wins over allowlist
    on overlap and is silent.
  - `test_validation_scenarios_*` / `test_validation_exclude_scenarios_*` —
    unknown slugs and `all`+explicit-slug mixes exit non-zero with a clear error
    message naming the offending slug and the catalog.
  - `test_warning_*` — exactly one WARNING line per dropped slug, matching the
    `WARNING: scenario <slug> requires …; skipped.` convention.
  - `test_resolve_scenarios_warning_order_is_deterministic` — WARNING lines
    appear in sorted-slug order across runs, regardless of dict iteration.
  - `test_anomaly_count_with_scenarios_*` — `--anomaly-count` restricts the
    sampling pool to the active scenarios and stays byte-deterministic for a
    given `--seed`.
  - `test_default_*_csvs_byte_identical` + `test_high_seven_day_capped_*` —
    locked SHA-256 hashes for default and `--signal-level high
    --anomaly-count 100` runs; protects against silent spec-order drift.
- `tests/test_cli.py` — subprocess-level smoke for `--scenarios` and
  `--exclude-scenarios`: help text presence, end-to-end run success on a
  single slug, non-zero exit for unknown slugs and `all`+explicit mixes.
- `tests/test_correctness.py` —
  `test_scenarios_all_matches_no_flag_byte_for_byte` is the default-equivalence
  regression: explicit `--scenarios all` must produce identical per-component
  CSV and `anomalies.csv` bytes as omitting the flag, at 1 and 7 days.

Selector composition order (locked by the VER-102 plan):
`--scenarios` → `--exclude-scenarios` → `--signal-level` → `--duration-days`
→ `--components`. Severity and duration drops are loud (WARNING); the
component filter drop is silent because the user already restricted the
allowlist on purpose.
