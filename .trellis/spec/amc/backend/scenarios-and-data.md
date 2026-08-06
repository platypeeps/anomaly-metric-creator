# Scenarios and Data

## Scenario Registry

`SCENARIOS: dict[str, Scenario]` is the only anomaly scenario catalog; legacy
module-level anomaly lists must not return. `_apply_scenarios()` is the single
composition point that populates per-run primary and cascade anomaly state.
The canonical ordered data lives in `scenario_catalog.py`; builders and the
`Scenario` model live in `scenario_builders.py`; runtime selection/composition
lives in `scenarios_impl.py`. `legacy.py` keeps patch-visible compatibility
bindings and wrappers, while `scenarios.py` re-exports the canonical objects.
Sources: `CLAUDE.md`; `README.md`;
`src/anomaly_metric_creator/scenario_builders.py`;
`src/anomaly_metric_creator/scenario_catalog.py`;
`src/anomaly_metric_creator/scenarios_impl.py`;
`src/anomaly_metric_creator/legacy.py`;
`src/anomaly_metric_creator/scenarios.py`; `tests/test_scenarios.py`;
`tests/test_registry.py`.

Each `Scenario` entry owns its slug, label, severity, `days_required`,
category, `components_touched`, `primary_specs`, and `cascade_specs`.
`Scenario.id` must match its `SCENARIOS` key. Sources: `CLAUDE.md`;
`README.md`; `src/anomaly_metric_creator/scenario_builders.py`;
`src/anomaly_metric_creator/scenario_catalog.py`;
`src/anomaly_metric_creator/legacy.py`;
`tests/test_scenarios.py`.

`days_required` must be a positive integer equal to the 1-based day index of
the earliest primary or cascade `time_offset` that can enter the output window.
The import-time registry validator and tests mirror this invariant. Sources:
`CLAUDE.md`; `README.md`; `src/anomaly_metric_creator/legacy.py`;
`tests/test_scenarios.py`; `tests/test_registry.py`;
`tests/test_multiday_cascades.py`.

`components_touched` must match the set of components referenced by primary and
cascade specs; it is the authoritative component-filter index for scenario
selection. Sources: `CLAUDE.md`; `src/anomaly_metric_creator/legacy.py`;
`tests/test_scenarios.py`; `tests/test_registry.py`.

## Spec Validation and Dispatch

Primary and cascade specs are validated at import time by
`_validate_scenarios_registry()` and `_validate_scenario_spec()`. Validate
required keys, component/metric membership, callable generators, finite
non-negative non-bool times/durations, non-empty descriptions, shape names,
shape params, and `instance_filter` forms before runtime. The implementation
takes explicit registry/catalog inputs; `legacy.py` owns the sole historical
import-time call and delegates with its current patch-visible bindings.
Sources: `CLAUDE.md`;
`src/anomaly_metric_creator/scenario_validation.py`;
`src/anomaly_metric_creator/legacy.py`; `tests/test_package_facades.py`;
`tests/test_scenarios.py`;
`tests/test_registry.py`.

Cascade specs are single-row step specs and must not declare `shape`,
`duration_seconds`, or `shape_params`. Sources: `CLAUDE.md`;
`src/anomaly_metric_creator/legacy.py`; `tests/test_scenarios.py`.

Generator calls have two canonical positional shapes: step path `(ts, col)` or
`(ts, col, rng)`, and span path `(ts, col)` or
`(ts, col, t_within, span_idx, rng)`. Reject ambiguous `*args` or required
positional counts that would silently misbind span variables or overwrite
defaults. Sources: `CLAUDE.md`; `src/anomaly_metric_creator/legacy.py`;
`tests/test_scenarios.py`.

The scenario resolution pipeline is allowlist, exclusion, severity,
duration, then component filter. Severity and duration drops warn on stderr;
component-disjoint drops are silent. Sources: `CLAUDE.md`; `README.md`;
`src/anomaly_metric_creator/scenarios_impl.py`;
`src/anomaly_metric_creator/legacy.py`; `tests/test_args.py`;
`tests/test_scenarios.py`; `tests/test_correctness.py`.

## Adding Scenarios, Metrics, and Components

New scenarios must update `scenario_catalog.SCENARIOS`, `components_touched`,
README scenario catalog rows, ops profiles, and focused tests in the same
change. Sources:
`CLAUDE.md`; `README.md`; `src/anomaly_metric_creator/scenario_catalog.py`;
`src/anomaly_metric_creator/legacy.py`;
`src/anomaly_metric_creator/server_ops.py`; `tests/test_scenarios.py`;
`tests/test_server.py`.

New metrics belong in the ordered `COMPONENTS` catalog. Preserve the historic
default metric zone unless intentionally changing default CSV bytes; append or
replace supplemental metrics by default and respect `MAX_METRICS_PER_COMPONENT`.
Sources: `CLAUDE.md`; `src/anomaly_metric_creator/catalog.py`;
`src/anomaly_metric_creator/legacy.py`; `tests/conftest.py`;
`tests/test_registry.py`; `tests/test_correctness.py`.

New components require lockstep updates to `COMPONENTS`,
`DEFAULT_METRICS_PER_COMPONENT`, `tests/conftest.py` component field fixtures,
and any scenario/ops/topology docs that reference the component set. Sources:
`CLAUDE.md`; `README.md`; `docs/topology.md`;
`src/anomaly_metric_creator/catalog.py`;
`src/anomaly_metric_creator/legacy.py`; `tests/conftest.py`;
`tests/test_registry.py`; `tests/test_correctness.py`.

Scenario docs must list slug, minimum signal level, `days_required`, duration
summary, touched components, and a human-readable description. Sources:
`README.md`; `CLAUDE.md`; `src/anomaly_metric_creator/legacy.py`;
`tests/test_scenarios.py`.

## Deterministic Data Rules

For a fixed seed and configuration, output order and byte shape are load-bearing
contracts. Preserve registry insertion order, stable sorted anomaly override
ordering, and deterministic component/metric order unless the change
explicitly intends to relock affected fixtures. Sources: `CLAUDE.md`;
`src/anomaly_metric_creator/legacy.py`; `tests/test_determinism.py`;
`tests/test_correctness.py`; `tests/test_schema_file.py`;
`tests/test_gauges_file.py`.

When registry-derived tests compute expected sets, assert the set is non-empty
before membership/equality checks unless emptiness is the behavior under test.
Sources: `CLAUDE.md`; `tests/test_scenarios.py`;
`tests/test_topology_registry.py`; `tests/test_correctness.py`.

## Topology and Schema Data

`TOPOLOGY` describes directed coupling among selected components; scenario
cascades express incident blast radius and are not implicit topology edges.
Sources: `docs/topology.md`; `README.md`; `CLAUDE.md`;
`src/anomaly_metric_creator/legacy.py`; `tests/test_topology_registry.py`;
`tests/test_topology_llm.py`.

The two mechanisms are allowed to overlap on the same column and must both be
kept. Topology is the path for "load on a source raises the downstream
baseline / elevates downstream latency and error rate"; a cascade is the path
for "metric X takes a specific value at exactly row Y". A cascade override is a
single-row step write applied *after* saturation composition, so it still pins
its targeted cell regardless of upstream load while saturation only lifts the
surrounding band. Cascades targeting `error_rate` on `apigateway` or
`authservice` are the most overlap-prone — saturation also elevates those
columns under load — but stay distinguishable: the cascade produces a sharp
step at the recorded row, saturation a smooth load-shaped band underneath it.
Do not remove a cascade on the grounds that saturation now produces a similar
downstream effect. The same ordering holds per instance: a per-instance
`instance_filter` override is applied after the natural-column draw, so a
cascade write at row `i` for instance `K` wins at exactly that cell regardless
of the saturation-driven baseline computed for that pod. Sources:
`docs/topology.md`; `src/anomaly_metric_creator/generation.py`;
`src/anomaly_metric_creator/topology_compose.py`;
`src/anomaly_metric_creator/topology_instances.py`;
`tests/test_scenario_deviation.py`; `tests/test_topology_multi_instance.py`.

A `dtype="int"` cast runs inside `generate_component()` after the anomaly
override pass and *before* both the derivation pass and the `topology_capture`
snapshot, so derived columns and downstream coupling signals consume the same
whole-integer values the CSV records. `main()` always passes
`apply_dtype_int_cast=True`; the kwarg survives for programmatic callers that
need the pre-cast fractional contrast. Sources:
`src/anomaly_metric_creator/generation.py`;
`src/anomaly_metric_creator/generation_helpers.py`;
`tests/test_correctness.py`.

One `anomalies.csv` manifest entry is recorded per
`(timestamp, component, metric)` regardless of instance count: a spec with an
`instance_filter` records exactly one entry no matter how many instances
matched, and none on a zero-match (which also warns on stderr and skips the
spec). Sources: `src/anomaly_metric_creator/generation.py`;
`tests/test_instances_per_component.py`.

OTLP dimension attributes are emitted from the non-empty
`_INSTANCE_DIMENSION_COLUMNS` cells of each row on the gauge and anomaly-counter
payload builders. In v1 no anomaly row carries dimensions — `anomalies.csv` is
single-instance — so the counter-path extension is structurally inert today but
keeps the JSON and protobuf shapes aligned with the gauge path; log and trace
builders stay on the base attribute set. Sources:
`src/anomaly_metric_creator/otlp.py`;
`src/anomaly_metric_creator/otel_stream.py`; `tests/test_otel_gauges.py`.

`schema.json` must serialize active component metrics, optional dimensions,
declared files, run metadata, and topology snapshot in deterministic JSON.
Validator behavior should be updated with schema changes, and schema version
bumps should accompany breaking shape changes. Sources: `README.md`;
`CLAUDE.md`; `src/anomaly_metric_creator/legacy.py`;
`src/anomaly_metric_creator/schema_impl.py`;
`src/anomaly_metric_creator/validate_impl.py`;
`tests/test_schema_file.py`; `tests/test_validate_output.py`.

`--instance-config` and `schema.json` are read-back/user-editable boundaries.
Validate shape and type on the reader side rather than trusting the local
writer. Sources: `README.md`; `CLAUDE.md`;
`src/anomaly_metric_creator/legacy.py`; `tests/test_instance_config.py`;
`tests/test_validate_output.py`; `tests/test_trace_bundle.py`.
