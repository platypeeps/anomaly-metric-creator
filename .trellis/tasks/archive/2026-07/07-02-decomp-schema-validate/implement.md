# Implementation Plan

## Preparation

- Read `.trellis/spec/amc/backend/index.md`, then the focused specs for
  architecture, API/CLI/schema behavior, scenarios/data, and testing quality.
- Re-check `src/anomaly_metric_creator/legacy.py` function boundaries before
  editing; line numbers may drift as earlier changes land.
- Keep the change structural. Do not relock schema hashes or change validation
  semantics.

## Ordered Steps

1. Create `src/anomaly_metric_creator/schema_impl.py`.
   - Move `SCHEMA_DOCUMENT_VERSION`, writer serializers, topology schema
     serialization, dimension schema serialization, and `write_schema_json`.
   - Import only the dependencies the moved code needs.
   - Do not import `legacy.py`.

2. Re-export writer names from `legacy.py`.
   - Delete the moved writer block.
   - Add a re-import at the former conceptual location.
   - Preserve all historic `legacy.<name>` bindings.

3. Re-point `src/anomaly_metric_creator/schema.py` for writer symbols.
   - Keep the public `__all__` unchanged.
   - Preserve facade identity expectations with `legacy.py`.

4. Run the writer-focused checks.
   - `PYTHONPYCACHEPREFIX=/private/tmp/amc-pycache python3 -m py_compile src/anomaly_metric_creator/legacy.py src/anomaly_metric_creator/schema.py src/anomaly_metric_creator/schema_impl.py`
   - `.venv/bin/pytest tests/test_package_facades.py tests/test_schema_file.py -n 0`

5. Create `src/anomaly_metric_creator/validate_impl.py`.
   - Move schema shape/load helpers, artifact validators, topology coupling
     validators, long-form dimension validators, and `validate_output`.
   - Keep validation order and messages unchanged.
   - Do not import `legacy.py`.

6. Re-export validator names from `legacy.py` and update `schema.py`.
   - Delete the moved validator block from `legacy.py`.
   - Re-import moved names at the former conceptual location.
   - Keep `validate_output` identity stable through the schema facade.

7. Run validator-focused checks.
   - `PYTHONPYCACHEPREFIX=/private/tmp/amc-pycache python3 -m py_compile src/anomaly_metric_creator/legacy.py src/anomaly_metric_creator/schema.py src/anomaly_metric_creator/schema_impl.py src/anomaly_metric_creator/validate_impl.py`
   - `.venv/bin/pytest tests/test_package_facades.py tests/test_validate_output.py tests/test_cli_surface.py -n 0`

8. Update documentation/source maps if needed.
   - Update `CLAUDE.md` module map if it still names `legacy.py` as the schema
     writer or validator owner.
   - Refresh `docs/repomix-map.md` with `scripts/update_repomix` if source tree
     changes make the map stale.

9. Final verification.
   - `.venv/bin/pytest tests/test_package_facades.py tests/test_schema_file.py tests/test_validate_output.py tests/test_cli_surface.py -n 0`
   - Run the repo's selected review gate before PR readiness if time allows:
     `scripts/sd-ai-command-pack-full-check.sh`

## Risk Points

- Circular imports between `legacy.py`, `schema_impl.py`, and `validate_impl.py`.
- Monkeypatch-sensitive helpers losing the historic `legacy.<name>` binding.
- Schema golden-hash drift from changed key ordering, list ordering, or
  serializer placement.
- Validator message/order drift from moving helper call paths.
- Import-time registry validators running twice or in a different order.

## PR Notes

- The PR should explicitly say this is one structural extraction PR with two
  internal phases: writer first, validator second.
- The PR should include the schema hash evidence and validator-focused pytest
  evidence in its verification section.
