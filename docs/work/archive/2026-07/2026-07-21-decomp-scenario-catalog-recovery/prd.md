---
title: Extract scenario registry and resolution modules
status: done
created: 2026-07-21
---
# Extract scenario registry and resolution modules

## Goal

Recover the unimplemented PR B from archived decomposition step 9: move scenario builders and resolution helpers to scenarios_impl.py, move SCENARIOS and its validator to scenario_catalog.py, preserve legacy monkeypatch visibility and import-time ordering, and re-point the scenarios facade with byte-identical output.

## Requirements

- Treat this as the missing PR B from archived task
  `07-02-decomp-catalog-data`; do not reopen or rewrite its completed models +
  component-catalog PR A.
- Move the scenario-builder helpers, `Scenario`, and `register_cascade` out of
  `legacy.py` as whole functions/classes without changing callable bodies,
  spec declaration order, generator binding, or RNG draw order.
- Move the `SCENARIOS` declaration into one canonical data-registry module.
  Preserve dict insertion order and every nested primary/cascade tuple order;
  collision last-writer behavior depends on these orders.
- Move scenario validation and runtime resolution/filtering into focused
  behavior modules. Keep every behavior module below 800 lines. A single
  larger declarative `scenario_catalog.py` is allowed because splitting one
  ordered registry across category modules would make ordering and navigation
  less auditable; record this data-only exception in CLAUDE.md.
- Preserve the historic `legacy.<name>` surface and direct identity through
  `scenarios.py`. Tests that monkeypatch `legacy.SCENARIOS` must remain visible
  to `_validate_scenarios_registry`, `_resolve_scenarios`, and
  `_apply_scenarios` through explicit runtime callbacks or wrapper arguments;
  new modules must not import `legacy`.
- Preserve the single historical import-time validation call and its ordering
  after component catalogs exist. Do not validate once in the catalog and a
  second time through `legacy.py`.
- Relabel the stale `# Combine step` heading around `_EMIT_ARTIFACT_FILES` while
  this region is already being reorganized; do not change the registry.
- Update the Trellis backend architecture/testing specs, CLAUDE.md module map,
  `scenarios.py` facade, and generated repository map in the same stream.

## Acceptance Criteria

- [ ] All locked SHA-256 goldens and the full pytest suite pass unchanged.
- [ ] `tests/test_registry.py`, `tests/test_scenarios.py`, and
      `tests/test_package_facades.py` pass, including patched
      `legacy.SCENARIOS` validation and facade identity assertions.
- [ ] The import-time scenario validator executes exactly once at the same
      conceptual point, and malformed registry/spec fixtures retain their
      existing exception types and diagnostic text.
- [ ] Every new behavior module is under 800 lines; the declarative catalog
      exception is explicit and contains no runtime orchestration.
- [ ] `legacy.py` no longer owns scenario registry data or scenario behavior;
      it retains only compatibility re-exports/wrappers at those locations.
- [ ] CLI help output and default/N=3 locked outputs remain byte-identical.
- [ ] CLAUDE.md, Trellis specs, `scenarios.py`, and `docs/repomix-map.md` match
      the final module ownership.

## Notes

- Keep `prd.md` focused on requirements, constraints, and acceptance criteria.
- Lightweight tasks can remain PRD-only.
- For complex tasks, add `design.md` for technical design and `implement.md` for execution planning before `task.py start`.
- Recovery source: the archived predecessor's PRD and implementation plan say
  PR B remained after PR A, but the task was archived completed on 2026-07-18.
  Current `legacy.py` is 4,829 lines and still contains that PR B surface.
