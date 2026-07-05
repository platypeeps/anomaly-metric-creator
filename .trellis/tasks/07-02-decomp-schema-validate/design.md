# Design: schema_impl.py + validate_impl.py extraction

## Scope

This task is a structural extraction from `src/anomaly_metric_creator/legacy.py`.
It must land as one PR, with implementation ordered in two internal phases:

1. Extract schema writing into `schema_impl.py`.
2. Extract schema read-back and output validation into `validate_impl.py`.

No schema behavior, schema version, CLI behavior, violation messages, golden
hashes, or public import names should change.

## Module Boundaries

`schema_impl.py` owns writer-side schema construction:

- `SCHEMA_DOCUMENT_VERSION`
- `_metric_spec_to_schema_entry`
- `_saturation_params_to_schema_entry`
- `_edge_to_schema_entry`
- `_component_dimensions_schema_entry`
- `_serialize_topology`
- `write_schema_json`

`validate_impl.py` owns reader-side and artifact validation:

- schema shape helpers and `_load_schema_document`
- required/unknown file checks
- anomalies ordering, component row count, timestamp, cell, and derivation checks
- topology coupling and per-instance topology coupling checks
- long-form dimension checks
- `validate_output`

`legacy.py` remains the compatibility surface. It should delete the moved
blocks and re-import every historic name at the same conceptual location, using
`as` aliases where needed so `legacy.<name>` remains stable for tests,
facades, and downstream callers.

`schema.py` should stop importing behavior directly from `legacy.py`. It should
export the stable schema facade from the extracted implementation modules while
keeping `__all__ = ["SCHEMA_DOCUMENT_VERSION", "validate_output", "write_schema_json"]`.

## Dependency Direction

New modules must not import `legacy.py`.

The extracted modules may import focused modules that already own shared
primitives, such as:

- `artifacts.py` for `_atomic_write_text`
- `csv_layout.py` for component CSV layout readers/helpers
- `timeutil.py` for timestamp parsing

When a moved helper depends on constants or registry data still owned by
`legacy.py`, prefer importing the true owner only if one already exists. If no
focused owner exists yet, keep the compatibility import in `legacy.py` and use
plain module-level imports from the new module only when they do not introduce a
cycle. If a dependency would force a cycle, move the smallest coherent helper
with its call path or pass the dependency in at the call boundary.

## Data Flow

Generation continues to call `write_schema_json(...)` through the `legacy.py`
binding from `main()`. The function body lives in `schema_impl.py` and writes
the same deterministic JSON payload through `_atomic_write_text`.

The validate subcommand continues to parse arguments through
`_main_validate_subcommand` in `legacy.py` unless implementation discovers that
moving the subcommand wrapper is cycle-free and still preserves parser routing.
Either way, it must call the same `validate_output(output_dir)` compatibility
binding and preserve `--warn` semantics.

`validate_output()` loads `DIR/schema.json`, validates the schema shape before
trusting fields, and then runs validation checks in the current order.

## Compatibility

The following must remain true after extraction:

- `anomaly_metric_creator.schema.SCHEMA_DOCUMENT_VERSION == legacy.SCHEMA_DOCUMENT_VERSION`
- `anomaly_metric_creator.schema.write_schema_json is legacy.write_schema_json`
- `anomaly_metric_creator.schema.validate_output is legacy.validate_output`
- existing tests may continue to access private validator helpers on the `amc`
  / `legacy` module when they do so today
- import-time registry validators still run once and in the same relative order

## Trade-Offs

One PR keeps the facade contract coherent: the writer and validator are the two
halves of the `schema.py` surface, and the main safety gate is unchanged schema
bytes plus unchanged validator behavior. The trade-off is review size, so the PR
body should call out the two internal phases and the focused validation evidence.

Splitting into two PRs would reduce review size, but it would create an interim
shape where the schema facade is only partially extracted and would require a
second compatibility pass. The user chose one PR.

## Rollback

Rollback is mechanical: restore the moved blocks in `legacy.py`, remove
`schema_impl.py` / `validate_impl.py`, and point `schema.py` back to `legacy.py`.
No persisted data migration is involved because the schema document contract
does not change.
