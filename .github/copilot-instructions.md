# Copilot review instructions for anomaly-metric-creator

This file tells GitHub Copilot about repo-specific conventions so it can give more accurate feedback and avoid flagging intentional design choices.

## Architecture overview

`anomaly-metric-creator.py` is a single-file synthetic metric generator. `main(argv=None)` is the only entry point. Tests drive the full generation path end-to-end via `main()` — keep generation numpy-vectorized.

## Registries and single-source-of-truth contracts

The following constants are the **single source of truth** for their domain. Any consumer that duplicates them (hard-coded maps, parallel lists) is a bug.

- `COMPONENTS` — ordered `{component_name: [MetricSpec, ...]}` dict. This controls CSV column order, `--components` filtering, and scenario validation. Insertion order matters.
- `DEFAULT_METRICS_PER_COMPONENT` — per-component default column count. Must be kept in lockstep with `COMPONENTS`.
- `SCENARIOS` — the full anomaly scenario catalog. All anomaly specs live here; there are no legacy `anoms_*` module-level lists.
- `_EMIT_ARTIFACT_FILES` — maps `emit_selection` token → artifact filename. Used by pre-clean, run summary ("Done —"), and `schema.json`. Adding an artifact without updating this registry is a bug.
- `_COMBINE_OUTPUT_FILENAME` — the single source for the unified CSV filename. The combine writer, pre-clean, and the Done summary all read from it.
- `DERIVED_METRICS` — marks derived columns (e.g. `cacheservice.hit_ratio`). The derivation recompute in `generate_component()` must reference this, not hard-code component names.
- `_RECOMPUTERS` — validator-side dispatch for `--validate-output` derivation checks. Keyed by `(component, metric)`. Must raise on unknown keys; never fall through silently.
- `TOPOLOGY` — the service-call graph. Edges with callable `weight` are coupled in generation; constant weights are validated at import time.

## RNG / determinism model

- Generation uses a single `np.random.RandomState(seed)` instance created in `main()` and threaded as `RunContext.rng`.
- Draw order is MT19937 + Box-Muller. Never add per-spec or per-component seeds.
- `generate_component()` applies overrides in stable `sorted(specs, key=(row_idx, metric_name))` order. Reordering specs can shift draw sequences when two specs collide on the same `(row_idx, metric)`.
- The `--topology-mode realistic` path runs components in topological order; generation order for downstream components is therefore different from the default `independent` mode.

## Validator coverage requirements

When adding a new import-time validator (`_validate_*`), it must handle all non-canonical inputs or explicitly document why they are accepted:

- `None`, `NaN`, `±inf`, negative, `bool` (a subtype of `int`), empty string, unhashable value, wrong container type.
- Every discriminator branch: callable **and** constant weights; cascade **and** primary specs; step **and** span paths; `*args` **and** fixed-arity callables.
- Dispatch tables must raise on unknown keys. Returning `None` or silently falling through is not acceptable.

## Anomaly generator dispatch

The runtime calls generators with one of two canonical positional shapes (step: `(ts, col)` or `(ts, col, rng)`; span: `(ts, col)` or `(ts, col, t_within, span_idx, rng)`). `_validate_scenario_spec()` enforces arity at import time. See CLAUDE.md "Generator dispatch rule" for the full rules.

## Mode / flag interactions

These flag combinations are explicitly rejected at `parse_args` time — do not flag them as missing validation:

- `--otel-emit-gauges` + `--inject-dst-artifact-day > 0` (non-monotonic timestamps break `heapq.merge`)
- `--emit-selection gauges` + `--inject-dst-artifact-day > 0` (same reason)
- `--validate-output` + `--combine` / `--combine-only` (mutually exclusive modes)

## Known intentional out-of-scope violations

`--validate-output` in `--validate-warn` mode reports (but does not fail on) these known violations in the default catalog:

- Several `dtype="int"` columns emit fractional values in practice (e.g. `active_connections`). Generator-side fixes are tracked under VER-134.
- `context_overflow_rate` can exceed its declared `max_value=1.0` under the LLM scenario. This is intentional signal behavior.

Do not flag these as bugs in code review.

## Output directory hygiene

`_pre_clean_output_dir()` runs at the start of every generation (not `--combine-only`). It deletes stale artifacts from prior runs that the current invocation will not regenerate. The cleanup and the Done summary both read from `_EMIT_ARTIFACT_FILES` — if they diverge, it is a bug.

## Test suite conventions

- Tests write only into `tmp_path`, never into `iot_logs/`.
- Locked SHA-256 golden hashes for 1d/7d runs live in `tests/test_scenarios.py`, `tests/test_gauges_file.py`, and `tests/test_schema_file.py`. Hash mismatches indicate an unintentional byte-output change.
- Parametrized scenario tests are driven from `amc.SCENARIOS` — hard-coding slug lists in tests is a bug waiting to happen.
- `conftest.py` holds `COMPONENT_FIELDS` and `DEFAULT_METRIC_COUNT`; drift from `COMPONENTS` / `DEFAULT_METRICS_PER_COMPONENT` is caught only by the test suite, not import-time validation.
