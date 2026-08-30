# Extract scenario registry and resolution modules — Design

## Overview

Recover the unimplemented scenarios half of decomposition step 9 as one
verbatim-move refactor. The current block is too large for one behavior module,
so separate declarative data from builders, validation, and runtime resolution
while preserving `legacy` as the patch-visible compatibility root.

## Proposal

Create four one-way modules:

- `scenario_builders.py`: `Scenario`, scenario generator/schedule builders,
  and `register_cascade`.
- `scenario_catalog.py`: the ordered `SCENARIOS` declaration only. It imports
  builders but performs no validation or runtime resolution.
- `scenario_validation.py`: `_validate_scenario_spec` and
  `_validate_scenarios_registry`, expressed against explicit registry,
  component-catalog, and constant inputs.
- `scenarios_impl.py`: `_apply_signal_level_and_count`,
  `_resolve_scenarios`, and `_apply_scenarios`, using a configured
  `get_scenarios` callback so tests that patch `legacy.SCENARIOS` remain live.

`legacy.py` imports the canonical objects at their historical conceptual
locations. Its `_validate_scenarios_registry()` compatibility wrapper delegates
with the current `legacy.SCENARIOS` and `legacy.COMPONENTS`, then the existing
historical import-time call remains the only validation invocation. Runtime
resolution wrappers delegate through callbacks configured with lambdas that
resolve the legacy binding at call time. New modules never import `legacy`.

`scenarios.py` re-exports canonical objects from the new modules. Its exported
objects must be identical to `legacy.Scenario`, `legacy.SCENARIOS`, and
`legacy.register_cascade`.

## Boundaries And Non-Goals

- No spec values, scenario IDs, ordering, descriptions, generator bodies,
  severity behavior, CLI defaults, or output bytes change.
- No cleanup or deduplication inside the moved registry/builders.
- No decomposition of `main()` or unrelated legacy helpers.
- `scenario_catalog.py` may exceed 800 lines as a single declarative ordered
  registry. All executable behavior modules remain below 800 lines.

## Affected Files

New runtime modules: `scenario_builders.py`, `scenario_catalog.py`,
`scenario_validation.py`, `scenarios_impl.py`.

Edited integration/docs: `legacy.py`, `scenarios.py`, CLAUDE.md,
`.trellis/spec/amc/backend/architecture.md`,
`.trellis/spec/amc/backend/testing-quality.md`, focused tests only when needed
to pin the import/callback contract, and `docs/repomix-map.md`.

## Data And Command Contracts

- `SCENARIOS` dict order and each primary/cascade tuple order are immutable
  compatibility contracts.
- `legacy.SCENARIOS` remains independently monkeypatchable at runtime.
- Validation mutates only iterable `instance_filter` values into frozensets,
  exactly as today.
- `python anomaly-metric-creator.py --help` and the console entry point retain
  byte-identical help.

## Risks And Edge Cases

- Import cycles: catalog may import builders; validation/runtime may import
  leaf types/constants, but no module imports `legacy`.
- Duplicate validation: only the historical legacy call executes at import.
- Stale bindings: every runtime read of a monkeypatched registry goes through
  a callback or explicit wrapper argument.
- Splice damage: after each deletion, verify every earlier re-import stub still
  resolves and the scenario declaration order is unchanged.
- Catalog size: the data-only exception is documented; executable code must
  not accumulate in the catalog.

## Validation

Run focused registry/scenario/facade tests first, compare help output, run the
full suite and all locked hashes, then pre-commit and the repository full-check.
The PR must request the full CI matrix because the move is determinism- and
import-order-sensitive.
