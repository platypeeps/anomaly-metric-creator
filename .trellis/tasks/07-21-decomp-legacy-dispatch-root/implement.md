# Split legacy dispatch root below 800 lines — Implementation Plan

## Execution Order

1. Branch from current `main`; capture baseline `wc -l`, import smoke, focused
   tests, and the authoritative `main()` global-read inventory.
2. Add `run_defaults.py`; move generation-command constants and re-export them
   from `legacy.py`. Run CLI argument/help and import tests.
3. Move `RunContext` to `models_impl.py`; re-export it from `legacy.py` and
   `models.py`. Run facade, determinism import, and direct construction tests.
4. Add `run_pipeline.py` with the named weak runtime seam. Move reporting,
   emitted-file collection, output hygiene, and `main()` in that order. Keep
   the orchestration statements and helper call order unchanged.
5. Replace the moved `legacy.py` implementations with re-exports/thin wrappers
   using `runtime_key=__name__`. Add focused coverage for live patching and a
   fresh package-qualified isolated legacy copy.
6. Run the focused regression set. If any hash/output delta appears, stop and
   fix the seam or move before documentation cleanup.
7. Consolidate redundant relocation-history comments and excess blank spacing
   in `legacy.py`; confirm every historic binding still exists and measure all
   touched module line counts. Do not use dynamic export tricks to meet the
   cap.
8. Update the Trellis architecture spec, `CLAUDE.md`, parent epic decision and
   checklist, then refresh `docs/repomix-map.md` with `scripts/update_repomix`.
9. Run Ruff, pre-commit, the full pytest suite, and the repo full-check gate.
10. Use the SD create/review/watch/ship flow: draft PR, required local review,
    CI/review remediation, ready/merge, then child/parent finish-work and
    housekeeping.

## Focused Validation

```bash
.venv/bin/pytest tests/test_package_facades.py tests/test_determinism.py -n 0
.venv/bin/pytest tests/test_correctness.py -n 0
.venv/bin/pytest tests/test_reporting_artifacts.py tests/test_atomic_writes.py -n 0
.venv/bin/pytest tests/test_emit_selection_hygiene.py tests/test_schema_file.py -n 0
.venv/bin/pytest tests/test_combine.py tests/test_gauges_file.py -n 0
.venv/bin/pytest tests/test_topology_registry.py tests/test_topology_multi_instance.py -n 0
python anomaly-metric-creator.py --help
.venv/bin/python -c "import anomaly_metric_creator.cli"
```

## Full Validation

```bash
.venv/bin/ruff check src/ tests/
.venv/bin/pytest
.venv/bin/pre-commit run --all-files
scripts/sd-ai-command-pack-full-check.sh
wc -l src/anomaly_metric_creator/legacy.py \
  src/anomaly_metric_creator/run_pipeline.py \
  src/anomaly_metric_creator/run_defaults.py \
  src/anomaly_metric_creator/models_impl.py
```

The PR must record the before/after line-count table and state explicitly that
all locked hashes remained unchanged.

## Documentation And Spec Updates

- `.trellis/spec/amc/backend/architecture.md`: `legacy.py` is compatibility
  wiring, `run_pipeline.py` owns run orchestration, `RunContext` belongs to
  `models_impl.py`, and the weak namespace seam is canonical.
- `CLAUDE.md`: final module map and decomposition-complete wording.
- Parent epic PRD/design/implement/task metadata: record the maintainer's split
  decision, completion evidence, and removal of the proposed waiver.
- `docs/repomix-map.md`: regenerate after all source/test/docs edits settle.

## Review Notes

- Review the initial behavior move separately from line-count cleanup.
- Treat any output-byte, RNG-order, CLI-text, import-time, or monkeypatch
  difference as a blocker.
- Verify no new module imports `legacy.py` and no anonymous lambda is stored by
  the pipeline runtime seam.
- Verify the fresh isolated module path does not rely on
  `sys.modules[__name__]`.
