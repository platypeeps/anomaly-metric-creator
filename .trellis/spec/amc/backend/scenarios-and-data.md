# Scenarios and Data

## Scenario Registry

`SCENARIOS: dict[str, Scenario]` is the only anomaly scenario catalog; legacy
module-level anomaly lists must not return. `_apply_scenarios()` is the single
composition point that populates per-run primary and cascade anomaly state.
Sources: `CLAUDE.md`; `README.md`; `src/anomaly_metric_creator/legacy.py`;
`src/anomaly_metric_creator/scenarios.py`; `tests/test_scenarios.py`;
`tests/test_registry.py`.

Each `Scenario` entry owns its slug, label, severity, `days_required`,
category, `components_touched`, `primary_specs`, and `cascade_specs`.
`Scenario.id` must match its `SCENARIOS` key. Sources: `CLAUDE.md`;
`README.md`; `src/anomaly_metric_creator/legacy.py`;
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
shape params, and `instance_filter` forms before runtime. Sources: `CLAUDE.md`;
`src/anomaly_metric_creator/legacy.py`; `tests/test_scenarios.py`;
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
`src/anomaly_metric_creator/legacy.py`; `tests/test_args.py`;
`tests/test_scenarios.py`; `tests/test_correctness.py`.

## Adding Scenarios, Metrics, and Components

New scenarios must update `SCENARIOS`, `components_touched`, README scenario
catalog rows, ops profiles, and focused tests in the same change. Sources:
`CLAUDE.md`; `README.md`; `src/anomaly_metric_creator/legacy.py`;
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
