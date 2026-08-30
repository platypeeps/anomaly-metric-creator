---
title: Extract schema_impl.py + validate_impl.py from legacy.py (decomposition step 6)
status: done
created: 2026-07-02
branch: codex/decomp-schema-validate
---
# Extract schema_impl.py + validate_impl.py from legacy.py (decomposition step 6)

## Goal

Move write_schema_json + serializers to schema_impl.py and the validate-subcommand checks to validate_impl.py (writer first, then reader); re-point the schema.py facade. Schema golden hashes must be unchanged.

The user decision is to land step 6 as one PR. The implementation should still
be ordered internally: extract the writer first, verify schema hashes, then
extract the validator and validate-subcommand path.

## Confirmed Facts

- This is decomposition step 6 under `07-02-legacy-monolith-decomposition`; the parent design calls for extracting the schema writer first, then the validator, while preserving byte-identical output and the historic public import surface.
- The current package facade `src/anomaly_metric_creator/schema.py` re-exports `SCHEMA_DOCUMENT_VERSION`, `write_schema_json`, and `validate_output` directly from `legacy.py`.
- `tests/test_package_facades.py` pins that the schema facade points at the same objects exposed from `legacy.py`; after extraction, `legacy.py` must continue to re-export the moved symbols so this contract remains true.
- The schema writer block starts at `SCHEMA_DOCUMENT_VERSION` in `src/anomaly_metric_creator/legacy.py:8796`; writer helpers and `write_schema_json` span through `write_schema_json` at `src/anomaly_metric_creator/legacy.py:8947`.
- The validator block starts at `_json_path` in `src/anomaly_metric_creator/legacy.py:9054`; `validate_output` is at `src/anomaly_metric_creator/legacy.py:10634`; the validate subcommand entrypoint is `_main_validate_subcommand` at `src/anomaly_metric_creator/legacy.py:11030`.
- `tests/test_schema_file.py` locks schema bytes for one-day and seven-day runs, and `tests/test_validate_output.py` exercises malformed schema input, file checks, row/timestamp/cell validation, derivations, topology coupling, and dimension-aware validation.
- Backend specs require `schema.json` to stay deterministic, to remain an untrusted read-back boundary, and to keep `validate DIR` on the dedicated validate parser path.
- Scope decision: this is one PR, not two child tasks/PRs.

## Requirements

- Extract schema writer responsibilities into `src/anomaly_metric_creator/schema_impl.py` without changing generated `schema.json` bytes, key ordering, topology serialization, dimension declarations, emitted-file metadata, or atomic write behavior.
- Extract validate-subcommand implementation helpers into `src/anomaly_metric_creator/validate_impl.py` without changing violation messages, validation order, `--warn` behavior, malformed-schema exceptions, topology-coupling checks, or dimension-aware long-form checks.
- Keep `legacy.py` as the compatibility surface by re-importing moved names at the historic conceptual location. Existing imports and tests that access `legacy.<name>` must keep working.
- Re-point `src/anomaly_metric_creator/schema.py` to the extracted implementation module while preserving its exported names.
- Avoid new behavior, schema-version changes, golden-hash relocks, or CLI surface changes. This task is structural only.
- Keep import-time behavior stable: registry validators still run once in the same order, and the extracted modules must not import `legacy.py` in a way that introduces circular imports or duplicate validation.
- Keep dependencies explicit and local to the extracted modules. Shared constants or helpers should be imported from existing focused modules or re-exported from `legacy.py` only where needed to preserve compatibility.
- Update documentation/source maps only where the extraction changes module ownership or public module guidance.

## Acceptance Criteria

- [ ] `src/anomaly_metric_creator/schema_impl.py` owns `SCHEMA_DOCUMENT_VERSION`, schema serialization helpers, topology serialization for schema documents, and `write_schema_json`.
- [ ] `src/anomaly_metric_creator/validate_impl.py` owns schema-document loading/shape validation, artifact validation helpers, topology-coupling validation, long-form dimension validation, and `validate_output`.
- [ ] `src/anomaly_metric_creator/legacy.py` re-exports the moved names so `legacy.SCHEMA_DOCUMENT_VERSION`, `legacy.write_schema_json`, and `legacy.validate_output` remain stable.
- [ ] `src/anomaly_metric_creator/schema.py` exports from the new implementation modules or their stable facade rather than importing behavior directly from `legacy.py`.
- [ ] Existing schema golden hashes in `tests/test_schema_file.py` remain unchanged.
- [ ] `tests/test_package_facades.py`, `tests/test_schema_file.py`, `tests/test_validate_output.py`, and `tests/test_cli_surface.py` pass.
- [ ] A focused smoke generation with `--emit metrics,schema` produces no schema diff other than filesystem location of the temporary output directory.
- [ ] No direct import cycle is introduced between `legacy.py`, `schema_impl.py`, and `validate_impl.py`.
- [ ] Documentation or module maps that mention schema/validator ownership are updated if they would otherwise point future contributors back to `legacy.py` as the owner.

## Out of Scope

- Changing the schema document shape, schema version, validation semantics, topology thresholds, or dimension model.
- Relocking golden hashes.
- Refactoring unrelated generation, combine, OTEL, scenario, or server behavior.
- Splitting registry data structures or moving import-time registry validators unless required to avoid an import cycle.
