# Extract catalog data modules from legacy.py (decomposition step 9)

## Goal

Move the SCENARIOS registry (~3k lines of data) to scenario_catalog.py, COMPONENTS/DEFAULT_METRICS_PER_COMPONENT to catalog.py, and MetricSpec/Instance dataclasses to models_impl.py; re-point the models.py/scenarios.py facades. Data-only moves; import-time validators move with their registries in the same PR, preserving execution order. Monkeypatch hazard: COMPONENTS/SCENARIOS/INSTANCES are patched by tests — follow design.md's move-with-callers rule.

## Requirements (filled 2026-07-06 from the epic design + review)

- Move, following the epic's verbatim-move + re-import pattern (one-way
  imports, `legacy.<name>` surface unchanged):
  - `scenario_catalog.py` — the `SCENARIOS` registry data (currently
    [legacy.py:4459](src/anomaly_metric_creator/legacy.py:4459)–6477,
    ~2,020 lines).
  - `catalog.py` — `COMPONENTS`
    ([legacy.py:2417](src/anomaly_metric_creator/legacy.py:2417)),
    `INSTANCES` ([legacy.py:2896](src/anomaly_metric_creator/legacy.py:2896)),
    `DEFAULT_METRICS_PER_COMPONENT`, and the metric caps.
  - `models_impl.py` — `MetricSpec`, `Instance` dataclasses.
- **`scenarios_impl.py` (added by the 2026-07-06 design correction):** the
  scenario-builder helpers + `Scenario` dataclass
  ([legacy.py:362](src/anomaly_metric_creator/legacy.py:362)–1035),
  `register_cascade` + seasonality helpers (~2337–2407), and
  `_validate_scenario_spec` (~6478–6806) move with (or immediately after)
  the registry data — decide single-PR vs follow-on PR at task start; the
  design.md section map has always assigned them to `scenarios_impl.py`.
- Import-time validators (`_validate_scenarios_registry`,
  `_validate_instances_registry`, the MetricSpec metadata validator) move
  with their registries in the same PR, preserving the documented
  execution order.
- Monkeypatch inventory: `COMPONENTS`, `SCENARIOS`, `INSTANCES`,
  `DERIVATIONS` are patched by tests — apply design.md's
  move-with-callers rule for any intra-module caller.
- Housekeeping earmarked for this step (epic task.json notes): relabel or
  relocate `_EMIT_ARTIFACT_FILES` out from under the misleading
  `# Combine step` header in legacy.py.

## Acceptance Criteria

- [ ] All locked SHA-256 golden hashes unchanged (full suite, `full-ci`).
- [ ] `models.py` / `scenarios.py` facades re-pointed;
      `tests/test_package_facades.py` identity assertions pass.
- [ ] Import-time validation still fires exactly once, in the same order.
- [ ] CLAUDE.md module map updated in the same PR.
- [ ] Size-cap note: `scenario_catalog.py` will be ~2k lines of pure
      registry data — record explicitly (in the PR + design.md) whether the
      800-line cap treats data-only registries as exempt, or split the
      catalog by scenario category to honor it.

## Notes

- Keep `prd.md` focused on requirements, constraints, and acceptance criteria.
- For complex tasks, add `design.md` for technical design and `implement.md` for execution planning before `task.py start`.
- 2026-07-06: `base_branch` in task.json corrected from the merged/deleted
  `refactor/extract-redaction` stacking branch to `main`.
- 2026-07-18 PR A status: `models_impl.py` + `catalog.py` extraction is
  implemented on `refactor/extract-catalog-data`. It moves `MetricSpec`,
  `Instance`, `COMPONENTS`, `INSTANCES`, `DEFAULT_METRICS_PER_COMPONENT`,
  metric caps, catalog seasonality helpers, and component/instance metadata
  validators while preserving the `legacy.<name>` surface and
  monkeypatch-visible runtime callbacks. PR B still owns
  `scenarios_impl.py` + `scenario_catalog.py` and the `scenarios.py` facade.
