# Extract catalog data modules — Design (SD Work Designs, 2026-07-17)

## Overview

Step 9 of the decomposition epic. Four landing modules with a clean
dependency chain — `models_impl.py` ← `catalog.py`;
`scenarios_impl.py` ← `scenario_catalog.py` — plus the epic Decision 2
assignments: the scenario-resolution helpers join `scenarios_impl.py`
and `_load_instance_config` joins `models_impl.py`.

## Proposal

**PRD's open decision (single-PR vs follow-on): two PRs**, split along
the registry families' monkeypatch/blast-radius seam:

- **PR A — `models_impl.py` + `catalog.py`:** `MetricSpec`, `Instance`,
  `_load_instance_config` (+ its `_valid_instance_fields` derivation);
  `COMPONENTS`, `INSTANCES`, `DEFAULT_METRICS_PER_COMPONENT`, the metric
  caps, and their import-time validators (metadata validator, instances
  validator, key-drift checks) moving in the same PR at the same
  execution position.
- **PR B — `scenarios_impl.py` + `scenario_catalog.py`:** scenario
  builders + `Scenario` + `_validate_scenario_spec` +
  `register_cascade`/seasonality helpers into `scenarios_impl.py`; the
  ~2,020-line `SCENARIOS` data into `scenario_catalog.py` (which imports
  `scenarios_impl` for the dataclass/builders);
  `_validate_scenarios_registry` moves with the registry. The
  resolution cluster (`_resolve_scenarios`, `_apply_scenarios`,
  `_apply_signal_level_and_count`) moves here too per epic Decision 2.

**Monkeypatch analysis (the load-bearing part).** Registry *data* moves
are patch-safe by themselves: import-time validators and builders run
before any patch, and runtime consumers that remain in `legacy.py`
(`main()`, generation until step 10) read legacy's re-import bindings —
patching `legacy.COMPONENTS`/`legacy.SCENARIOS` keeps working for them.
The exception is the moved **runtime readers** in the resolution
cluster: `_resolve_scenarios` et al. read `SCENARIOS` (and the severity
gates) at run time, so in their new home they must resolve through a
**callback seam configured by `legacy.py`**
(`_configure_scenario_runtime(get_scenarios=lambda: SCENARIOS, …)`) —
the same pattern as `schema_impl`/`validate_impl`/step 8's cli seam —
so tests patching `legacy.SCENARIOS` still steer resolution with zero
test edits. `_load_instance_config` reads `COMPONENTS` /
`INSTANCES` / `MAX_INSTANCES_PER_COMPONENT` at run time → same seam
treatment on the models side (or explicit parameters where `main()` is
the only caller — choose per call-site count at implementation; seam
for >1 caller, parameter for exactly-one).

**Housekeeping earmarked for this step:** relabel/move
`_EMIT_ARTIFACT_FILES` out from under the misleading `# Combine step`
header (epic task.json note).

**Size cap:** `scenario_catalog.py` (~2k lines of pure registry data) —
record the **data-registry exemption** explicitly in the PR +
CLAUDE.md rather than splitting by category (a category split adds
navigation cost with zero cohesion gain; the file has no logic to
cap). Mirrors the same exemption the server-ops epic plans for
`OPS_SCENARIO_PROFILES`. All logic-bearing modules stay <800.

## Boundaries And Non-Goals

- Zero behavior change; all locked hashes gate both PRs.
- No spec-dataclass typing (A-005 rides `07-17-audit-typed-boundaries`
  PR 3, sequenced *after* this lands — its design references this
  landing zone).
- Facade re-points (`models.py`, `scenarios.py`) happen in the PR that
  moves their names; identity tests gate.

## Affected Files

New: `models_impl.py`, `catalog.py`, `scenarios_impl.py`,
`scenario_catalog.py`. Edited: `legacy.py` (deletions + re-imports +
seam configure calls), `models.py`/`scenarios.py` facades, CLAUDE.md
module map + facade paragraph, spec index.

## Risks And Edge Cases

- Splice hazard on four large cut ranges (grep `^from \.` per cut).
- Import order: models/catalog validators must still run before
  scenario validation (current file order encodes it) — the re-import
  block positions preserve it; assert with the existing import-time
  validator tests.
- `test_scenarios.py` loads `_VALID_ANOMALY_SHAPES` at parametrize
  collection time via `conftest._load_amc()` — confirm the name's
  re-import lands before collection touches it (it will, via legacy's
  top-level re-import block; verify all module-level names tests import
  directly).

## Validation

- Full suite after each PR (hashes); `tests/test_package_facades.py`;
  `tests/test_scenarios.py`, `tests/test_registry.py`,
  `tests/test_instances_per_component.py` serially first.
- `--help` byte-diff (seam must not disturb step 8's surface).
