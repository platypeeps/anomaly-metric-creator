# Split legacy dispatch root below 800 lines — Design

## Overview

After the generation, topology, scenario, catalog, CLI, schema, validation,
OTEL, and artifact extractions, `legacy.py` is 1,587 lines. Its remaining
behavioral concentration is the 421-line `main()` orchestration path plus
run-level reporting and output hygiene. The parent epic proposed accepting a
waiver rather than splitting `main()`; the maintainer selected the split.

The end state is a one-way dependency graph:

```text
legacy.py (public bindings + live runtime wiring)
  -> run_pipeline.py (one-run orchestration + artifact lifecycle)
       -> existing focused implementation modules
  -> models_impl.py (RunContext + MetricSpec + Instance)
  -> run_defaults.py (generation command defaults)
```

No extracted module imports `legacy.py`.

## Module Ownership

### `run_pipeline.py`

Own the cohesive run-level cluster currently in `legacy.py`:

- `_EMIT_ARTIFACT_FILES`
- `write_reporting_artifacts()`
- `_collect_emitted_filenames()`
- `_known_artifact_filenames()`
- `_pre_clean_output_dir()`
- the implementation of `main()`

The file is expected to remain below 800 lines: the moved cluster is roughly
570 source lines before removing duplicated section prose, and the runtime
seam/imports fit inside the remaining budget.

`legacy.py` re-exports `write_reporting_artifacts` and the emit registry. It
keeps thin wrappers for patch-sensitive output helpers and `main()`, passing
`runtime_key=__name__` so isolated legacy modules get independent wiring.

### `models_impl.py`

Move `RunContext` next to `MetricSpec` and `Instance`. Add `field` to the
existing dataclasses import. `legacy.py` and `models.py` both import
`RunContext` from this canonical owner, preserving object identity and field
defaults. The move does not add a NumPy import because the RNG annotation is a
string and remains runtime-neutral.

### `run_defaults.py`

Own generation-command defaults now defined at the top of `legacy.py`,
including the anomaly-count salt and signal-level map. It may import the
scenario default row count/interval and shared `START`/`SECONDS_PER_DAY` leaf
constants. `legacy.py` re-exports these names so CLI configuration and historic
consumers retain the same values. No test currently monkeypatches these
constants; the pipeline nevertheless reads them through the live legacy
namespace to retain the old call-time lookup model.

### `legacy.py`

Remain the historic compatibility facade and the only place that wires live
registries/helpers from its namespace into extracted modules. Remove imports
that become unused after the extraction. Consolidate the repeated
"moved during decomposition" comments into concise owner notes and normalize
excess blank lines. This documentation cleanup is subordinate to the actual
behavior split and cannot remove any exported binding.

## Live Runtime Seam

`legacy.py` defines a named callback:

```python
def _run_runtime_namespace():
    return globals()
```

It configures `run_pipeline.py` with that callable and `runtime_key=__name__`.
The pipeline stores only a weak reference to the callback. When `main()` starts,
it resolves the callback and binds the legacy globals used by the existing body
to locals. The orchestration body then remains in its current order.

This design has four required properties:

1. Rebinding `legacy.COMPONENTS`, `_apply_scenarios`, writers, topology
   registries, or other collaborators before `main()` is visible to that run.
2. A package-qualified `spec_from_file_location()` module works even when it
   was not inserted into `sys.modules`, because `globals()` does not depend on
   a module lookup.
3. The weak callback does not retain isolated legacy module copies after their
   last reference is dropped.
4. Runtime keys prevent one isolated load from overwriting another load's
   collaborators.

The pipeline must raise an actionable internal `RuntimeError` if its configured
callback has expired. It must not silently fall back to canonical registries
for a `legacy.main()` call, because that would hide patch-visible drift.

## Compatibility Matrix

| Contract | Required handling |
| --- | --- |
| `legacy.main` | Thin wrapper, same signature and return behavior |
| subcommands | Resolve legacy `_main_*_subcommand` functions at call time |
| registries | Resolve current legacy bindings at call time |
| isolated imports | Namespace callback uses `globals()`, keyed by `__name__` |
| `RunContext` | Canonical class in `models_impl`; re-exported identically |
| reporting/output helpers | Historic legacy names retained |
| missing NumPy | Existing import-time message and exit code unchanged |
| import side effects | Import still does not invoke generation |
| RNG and hashes | Orchestration statement order and generator calls unchanged |
| atomic writes | Existing artifact helpers and pre-clean ordering unchanged |

## Boundaries And Non-Goals

- No generation algorithm, CLI flag, scenario, topology, schema, server, or
  artifact format change.
- No RNG cleanup, loop deduplication, signature modernization, or public API
  reduction.
- No migration of tests away from the historic `legacy` surface.
- No broad rewrite of existing focused modules.
- No waiver for `legacy.py` or a new behavior module over 800 lines.

## Affected Surfaces

- Source: `legacy.py`, new `run_pipeline.py`, new `run_defaults.py`,
  `models_impl.py`, `models.py`.
- Tests: focused pipeline/runtime-isolation and facade identity coverage, plus
  existing correctness, determinism, reporting, output hygiene, schema,
  combine, OTEL, topology, and multi-instance suites.
- Docs/specs: Trellis architecture/module-boundary convention, `CLAUDE.md`
  architecture map, parent epic decision/closeout, generated Repomix map.

## Risks And Mitigations

- **Hidden global lookup drift:** inventory `main()` globals with `symtable` and
  bind each legacy collaborator explicitly before moving the body.
- **Patch visibility loss:** add a focused test that replaces a collaborator
  on a fresh isolated legacy module and proves the pipeline observes it.
- **Isolated-module retention:** follow the existing named weak callback
  pattern used by generation/models/topology and test garbage-collection or
  runtime cleanup behavior where practical.
- **RNG/order delta:** move the orchestration body without reordering and use
  the complete locked-hash suite as the acceptance gate.
- **Circular imports:** `run_pipeline.py` never imports `legacy.py`; defaults
  remain in a leaf module and model ownership stays below the pipeline.
- **Line-count gaming:** the moved behavior must land first; comment/spacing
  consolidation is reviewed separately and retains concise ownership notes.

## Review Decision

Implementation may start only after the maintainer approves this PRD/design
boundary. Approval authorizes the structural refactor and its normal
verification/publish flow; it does not authorize behavior changes.

## Implementation Status (2026-07-21)

Implemented on `codex/decomp-legacy-dispatch-root`. Final line counts are
`legacy.py` 766, `run_pipeline.py` 673, `run_defaults.py` 33, and
`models_impl.py` 367. The full suite passed with 1,700 tests and two expected
real-client skips; pre-commit and the repository full-check also passed. PR
review, required CI, merge, and task/epic archive remain.
