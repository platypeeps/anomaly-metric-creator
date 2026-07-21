# Heavy-marker escape and fixture docs — Design (SD Work Designs, 2026-07-18)

## Overview

Three independent correctness defects surfaced while measuring runtime. None
is a speed win. Each is a trap for whoever next touches the fixtures.

## Proposal

### Item 1 — the marker escape

`tests/test_validate_output.py:52` defines a **second** module-scoped
fixture named `seven_day_schema_run` — a name in `_HEAVY_MODULE_FIXTURES`
(`tests/conftest.py:190-195`). Its consumers reach it via
`request.getfixturevalue(fixture_name)` (`:707`, `:733`) where
`fixture_name` arrives as a **parametrize string** (`:696-697`, `:724`), so
it never enters `item.fixturenames` and `_item_is_heavy`
(`conftest.py:198`) cannot see it.

`tests/test_heavy_marker.py` cannot catch this: every one of its cases calls
`_item_is_heavy(...)` with a hand-built tuple. It tests the *predicate*, not
*collection*. `test_item_is_heavy_detects_gb_scale_module_fixtures` asserts
the name classifies heavy — which passes, while the real item does not.

Harmless today only because that fixture omits `interval_seconds` and
inherits `_CHEAP_INTERVAL_SECONDS_DEFAULT = 60.0` (`conftest.py:36`) —
10,080 rows, ~8.7 MB. Add `interval_seconds=1.0` and a ~520 MB fixture lands
in the parallel lane silently.

Three candidate fixes were evaluated during implementation:

1. **Rename the shadowing fixture** (e.g. `validator_seven_day_schema_run`).
   Smallest diff, removes the collision entirely, and the parametrize
   strings update with it. Does **not** close the general hole: any future
   `getfixturevalue` of a heavy name escapes again.
2. **Detect the pattern at collection** — scan parametrize argvalues for
   strings matching `_HEAVY_*_FIXTURES` in `pytest_collection_modifyitems`.
   Closes the general hole but couples the marker to a naming convention in
   test parameters, which is fragile in its own way.
3. **Forbid the collision** — a test asserting no fixture name in
   `_HEAVY_*_FIXTURES` is defined outside `conftest.py`. Cheap, structural,
   and catches the *cause* rather than the symptom.

Implementation chose **1 + 2 + a scoped form of 3**. Renaming removes the
known collision. Collection-time callspec detection closes the general
`getfixturevalue` hole. The uniqueness guard rejects duplicate definitions
of names that are actually in the heavy registries; a blanket ban on heavy
fixture definitions outside `conftest.py` was rejected because
`seven_day_schema_run` and `synthetic_n3_run` are legitimate module fixtures.

Whichever is chosen, `tests/test_heavy_marker.py` must gain a case that
**fails against current `main`** — a real-collection assertion, not another
predicate call. Without that, the fix is unverified.

### Item 2 — the missing `full_resolution` marker

`tests/test_correctness.py:431` `test_anomalies_match_declared_value` does
exact-timestamp lookups (`rows[ts]`) against manifest timestamps derived
from `time_offset`. At 60s those snap to minute boundaries and miss, so 1s
is genuinely required. Its sibling `test_manifest_csv_cross_check` (`:104`)
carries `@pytest.mark.full_resolution` and documents the identical
dependency at `:113-116`.

Add the marker plus the same rationale comment. The marker's purpose is
auditability of 1s-dependent sites; an unmarked one defeats it.

### Item 3 — the stale size figures

| Claim | Location | Measured |
|---|---|---|
| "~1.3 GB of output" | `tests/conftest.py:347-348` | **264 MiB on disk; 4.12s setup-only** |
| "multiple minutes and ~9 GB" | `tests/conftest.py:386` | **1.81 GiB on disk; 29.08s setup-only** |

Both were materially high. The planning snapshot's `pyproject.toml` `~5 GB`
comment had already been removed by prior performance work before this task,
so implementation only updates the live marker description there.

Also correct the *kind*: these are **on-disk output**, not RSS. Measured
peak RSS is 8-11 GB depending on lane and worker count. The distinction
matters because the CI runner has 16 GB RAM but only 14 GB SSD — the two
ceilings are different and the docs currently conflate them.

## Boundaries And Non-Goals

- No change to what any fixture generates (`07-18-perf-heavy-fixture-trim`).
- No change to the heavy/light partition membership beyond whatever the
  marker fix legitimately corrects.
- Not rewriting `test_validate_output.py`'s parametrize style.

## Affected Files

`tests/test_validate_output.py` (rename + parametrize strings),
`tests/test_heavy_marker.py` (failing-first coverage, collision guard),
`tests/test_correctness.py:431` (marker), `tests/conftest.py` (collection hook
and docstrings), `pyproject.toml` and the testing guidance (marker contract).

## Risks And Edge Cases

- **Renaming may change partition membership.** If the validator fixture's
  consumers were accidentally light and become heavy (or vice versa), the
  counts shift. Verify before/after and confirm the new classification is
  the *correct* one, not merely different.
- **A collision guard may flag legitimate cases.** The initial blanket design
  did: the heavy registry intentionally includes module fixtures. The shipped
  guard therefore checks uniqueness, not location.
- **Item 3 coordinates with `07-18-perf-heavy-fixture-trim`**, which edits
  the same docstrings. Whichever lands second must re-read rather than
  reapply.
- **Adding `full_resolution` may trip an audit** that counts marked sites.
  Check whether anything asserts a fixed count.

## Validation

- The new heavy-marker case must fail on current `main` and pass after —
  demonstrate both, or the fix is unproven.
- Temporarily set `interval_seconds=1.0` on the validator fixture and
  confirm its consumers now classify heavy; revert.
- Partition counts before and after, with any change explained.
- Every size figure traceable to a measurement recorded in the PR.
