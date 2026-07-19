# Extract catalog data modules — Implementation Plan

## Execution Order

**Precondition:** decomp step 8 (`cli_args`) merged green.

**2026-07-18 status:** PR A is implemented on
`refactor/extract-catalog-data`. PR B remains after this branch lands.

**PR A (models + catalog):**

1. [x] Monkeypatch grep for `MetricSpec`/`Instance`/`COMPONENTS`/`INSTANCES`
   /`DEFAULT_METRICS_PER_COMPONENT`/`DERIVATIONS` over `tests/`; runtime-
   reader audit for `_load_instance_config` (seam vs parameter per
   design rule).
2. [x] Create `models_impl.py` then `catalog.py` (verbatim; validators move
   with registries at the same execution position); re-import blocks;
   splice-hazard grep.
3. [x] Re-point `models.py` facade; run facade identity + registry tests;
   update CLAUDE.md/spec module maps.
4. [x] Full suite and local `sd-ai-command-pack-full-check` gate.
5. [ ] Draft PR (`full-ci`) → checklist → merge.

**PR B (scenarios_impl + scenario_catalog + resolution cluster):**

6. Monkeypatch grep for `SCENARIOS` + resolution helpers; build
   `_configure_scenario_runtime` seam (legacy configures with lambdas).
7. Create `scenarios_impl.py` (builders, `Scenario`, spec validator,
   cascade/seasonality helpers, resolution cluster on the seam), then
   `scenario_catalog.py` (data; imports scenarios_impl); registry
   validator moves with the data; re-imports; splice grep.
8. `_EMIT_ARTIFACT_FILES` relabel/move housekeeping.
9. Re-point `scenarios.py` facade; record the scenario_catalog
   data-registry cap exemption in the PR + CLAUDE.md.
10. Full suite; `--help` byte-diff; draft PR (`full-ci`) → checklist →
   merge. Tick step 9 in the epic.

## Validation Plan

```bash
.venv/bin/pytest tests/test_registry.py tests/test_scenarios.py \
  tests/test_instances_per_component.py tests/test_package_facades.py -n 0
.venv/bin/pytest                        # hashes, both PRs
diff /tmp/help-before.txt <(python anomaly-metric-creator.py --help)
.venv/bin/pre-commit run --all-files
```

## Documentation And Spec Updates

- CLAUDE.md module map + facade paragraph (models/scenarios now
  re-point) + the cap-exemption sentence; spec index conventions.

## Review Notes

- Each PR description carries the monkeypatch grep results and the
  seam-vs-parameter decisions — the reviewer must see runtime readers
  are patch-visible.

## Follow-Ups

- `07-17-audit-typed-boundaries` PR 3 (AnomalySpec/CascadeSpec) starts
  only after PR B merges — its design points here.
