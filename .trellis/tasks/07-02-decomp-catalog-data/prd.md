# Extract catalog data modules from legacy.py (decomposition step 9)

## Goal

Move the SCENARIOS registry (~3k lines of data) to scenario_catalog.py, COMPONENTS/DEFAULT_METRICS_PER_COMPONENT to catalog.py, and MetricSpec/Instance dataclasses to models_impl.py; re-point the models.py/scenarios.py facades. Data-only moves; import-time validators move with their registries in the same PR, preserving execution order. Monkeypatch hazard: COMPONENTS/SCENARIOS/INSTANCES are patched by tests — follow design.md's move-with-callers rule.

## Requirements

- TBD

## Acceptance Criteria

- [ ] TBD

## Notes

- Keep `prd.md` focused on requirements, constraints, and acceptance criteria.
- Lightweight tasks can remain PRD-only.
- For complex tasks, add `design.md` for technical design and `implement.md` for execution planning before `task.py start`.
