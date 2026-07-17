# Extract generation.py + topology_impl.py — Design (SD Work Designs, 2026-07-17)

## Overview

Final epic step; starts only after steps 8–9 land with unchanged hashes.
Two clusters move: generation (~legacy.py:1036–2336, ~1,290 lines) and
topology (models :313–384 + `TOPOLOGY` :3076 + validators + composition
~2954–3996 + per-instance ~3997–4458, ~1,570 lines). Both exceed the
800-line cap as single files, and both intersect the monkeypatch
inventory — the two problems this design resolves up front.

## Proposal

### Module cuts (each <800, cohesive)

- `generation.py` — `generate_component`, `_natural_column`, row
  formatting (`_format_csv_row_block`, `_splice_dst_artifact`,
  `_format_fixed3`). The vectorized pipeline proper.
- `anomaly_dispatch.py` — generator-arity resolution, step/span dispatch,
  shape writers (`_VALID_ANOMALY_SHAPES` machinery). Only if the boundary
  proves clean at implementation (no shared mutable state, calls flow one
  way dispatch→generation buffers); otherwise a single `generation.py`
  with an explicit recorded deviation à la `validate_impl` — record,
  don't silently waive.
- `topology_impl.py` — `Edge`, `SaturationParams`, `TOPOLOGY`,
  `_TOPOLOGY_LOAD_METRICS`, `_TOPOLOGY_SATURATION_TARGETS`, all
  import-time topology validators.
- `topology_compose.py` — `_compose_topology_coupled_specs`,
  `_compose_topology_saturation_specs`, `_apply_saturation`,
  `_compute_topology_arrays_per_instance`, per-instance helpers. Imports
  `topology_impl` (one-way).

### Monkeypatch resolution (the load-bearing part)

Per-name plan for the design.md inventory names, following the
**combine_impl precedent** (patch the new canonical home; CLAUDE.md
documents the target):

| Name | New home | Patch consequence |
|---|---|---|
| `TOPOLOGY`, `_TOPOLOGY_LOAD_METRICS`, `_TOPOLOGY_SATURATION_TARGETS` | `topology_impl` | generation-path tests patch `topology_impl.<name>`; the schema/validate callback seams keep resolving through legacy's namespace (lambdas configured by `legacy.py`) and legacy re-imports preserve identity — tests that patch for *schema/validate* behavior keep patching `legacy.<name>` |
| `_format_fixed3` | `generation` | tests patch `generation._format_fixed3` |
| `DERIVATIONS` | stays in `legacy.py` **iff** `generate_component` receives it without a global read — audit first; if the body reads it as a global, it moves to `generation.py` (data + its only runtime reader together) and catalog step 9's validator coverage is unaffected |
| `COMPONENTS`, `SCENARIOS`, `INSTANCES` | already handled by step 9 | no change here |

Step 1 of implementation is the authoritative grep
(`monkeypatch.setattr` over `tests/`) producing the exact test-file list
per name; the table above fixes the *policy*, the grep fixes the *edits*.
Test edits are in scope for this step (unlike step 8) — the PRD's
move-with-callers rule anticipates patch-target migration; every migrated
patch site is enumerated in the PR description and the CLAUDE.md
monkeypatch note is extended in the same PR.

### RNG safety

Verbatim bodies, unchanged call order, no signature changes. The only
tolerated body edit is a read-site indirection if the DERIVATIONS audit
forces one — and the strong preference is moving the data with its reader
instead. The full golden-hash suite is the gate after every commit of the
extraction, not only at PR time.

## Boundaries And Non-Goals

- No dedupe of the two near-verbatim coupling loops — explicitly a
  follow-up PR after this settles (PRD acceptance bullet).
- No behavior or tuning changes anywhere; no new validators.
- `main()`, `RunContext`, constants stay in `legacy.py` (epic Decision 2).

## Affected Files

- New: `generation.py`, `topology_impl.py`, `topology_compose.py`
  (+ `anomaly_dispatch.py` if the boundary is clean); `legacy.py`
  re-imports; migrated test patch sites (enumerated at implementation);
  CLAUDE.md module map + monkeypatch note; spec index.

## Risks And Edge Cases

- The splice hazard on two large cut ranges (grep `^from \.` first).
- Import-time validator order: topology validators must still execute at
  the same relative position — `legacy.py`'s import of `topology_impl`
  sits where the deleted block was (design.md epic rule).
- `state.legacy.<name>` lookups from server modules must keep resolving —
  the re-import block guarantees it; the facade identity tests plus a
  targeted `server_mcp` smoke prove it.
- Per-instance topology helpers call back into generation buffers via
  arguments only (verify — any hidden global coupling forces a
  same-module placement).

## Validation

- Full suite after each extraction commit (hashes are the whole game);
  `tests/test_topology_*.py`, `tests/test_instances_per_component.py`,
  `tests/test_scenario_deviation.py` serially first for fast signal.
- `pytest tests/test_package_facades.py` identity assertions.
- A before/after `wc -l` table for every touched module in the PR
  description (cap evidence).
