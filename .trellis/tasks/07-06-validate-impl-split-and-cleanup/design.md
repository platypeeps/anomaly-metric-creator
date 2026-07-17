# validate_impl split + validator cleanups — Design (SD Work Designs, 2026-07-17)

## Overview

`validate_impl.py` is 1,684 lines (2.1× the epic's own cap — the
recorded deviation this task retires), plus four review cleanups and two
folded audit items (A-011 structured violations, A-068 unknown-file
tolerance). The oracle throughout: `tests/test_validate_output.py`
behavior-identical (with one sanctioned mechanical edit for A-011's
return type).

## Proposal

**The cohesive cut (PRD asks for it here):** three modules, all <800:

- `validate_impl.py` (keeps the name = keeps the facade/legacy surface
  and the `_configure_validate_runtime` seam): document
  loading/version gate, orchestrator `validate_output`, file-set checks
  (`required_files`/`no_unknown_files`), anomalies-sorted, row-count +
  timestamp-coverage checks. ~600 lines.
- `validate_cells.py`: header/cell checks, derivation recomputers +
  `_RECOMPUTERS`, long-form dimension checks. ~450.
- `validate_topology.py`: coupling checks (aggregate + per-instance),
  anomaly-exclusion windows, `_read_component_metric_column`. ~550.

One-way imports: the two leaves never import `validate_impl`; anything
they need from the configured runtime arrives **as function arguments**
from the orchestrator (the checks are already pure-ish functions over
schema/rows/paths — parameterizing the few accessor reads is a small,
test-visible edit, not a verbatim constraint: this is a within-package
refactor gated by the validator suite, and validate writes no hashed
artifacts).

**Cleanups in the same task:**

- Hoist the four bare `100` literals to `_TOPOLOGY_MIN_ALIGNED_ROWS`
  beside the two existing threshold constants.
- Delete dead `_apply_anomaly_exclusion` + its legacy re-import stub.
- **A-068 decision — exempt dotfiles:** `_validate_no_unknown_files`
  skips `.`-prefixed names (`.DS_Store` class), keeping the hard-fail
  for non-dot unknown files (those are real mistakes worth failing on).
  Matches generation's pre-clean tolerance; documented beside the
  pre-clean note in CLAUDE.md. (Warning-downgrade rejected: it would
  soften the whole check to close a dotfile-sized hole.)
- **A-011 — frozen `Violation(component, metric, kind, message)`** with
  `__str__` reproducing today's prose byte-for-byte;
  `validate_output` returns `list[Violation]`; the CLI join prints
  `str(v)` (output bytes unchanged). The 38 substring assertions get a
  mechanical `in str(v)`-shaped wrap in the same PR; field-based
  assertions migrate opportunistically later (recorded, not required).
- Perf items stay **measure-first optional**: extend the aggregate-path
  column cache to match the per-instance path only if a quick timing
  shows it matters; the single-pass cell walk is deferred (bigger
  reshape than this task warrants).

**Ordering rationale:** cleanups + A-068 + A-011 land *before* the
split so the moved code is already Violation-shaped and the split diff
is purely structural.

## Boundaries And Non-Goals

- No new checks, no severity/exit-code changes, no `schema.json` writer
  changes; `schema.py` facade and `legacy.validate_output` surface
  unchanged.
- Not the epic's generation-side work — this closes the epic's recorded
  deviation only.

## Affected Files

`validate_impl.py` (+ two new leaf modules), `legacy.py` (dead stub
removal only), `tests/test_validate_output.py` (mechanical wraps),
CLAUDE.md (validator section + pre-clean tolerance note + module map),
`.trellis/audit/ledger.md` (flip A-011, A-068).

## Risks And Edge Cases

- `__str__` byte-fidelity: build the Violation messages from the exact
  current format strings (move the strings, don't retype them); the
  suite's 38 assertions are the net.
- The callback seam must remain configured exactly once by legacy —
  the split must not add a second configure path (leaves take
  arguments, never read the seam).
- Dotfile exemption must not exempt `.tmp` atomic-writer debris in a
  way that masks a crashed run — `*.tmp` siblings are not dot-prefixed,
  so the existing stale-tmp sweep/report behavior is unaffected; state
  it in the PR.

## Validation

- `pytest tests/test_validate_output.py -n 0` green with only the
  sanctioned mechanical edits; `pytest tests/test_package_facades.py`.
- Full suite (hashes untouched by construction); `wc -l` cap table in
  the PR; epic design.md deviation note updated to "resolved".
