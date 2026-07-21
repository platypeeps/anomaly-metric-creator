# Close heavy-marker escape and correct fixture size docs

## Goal

Three correctness defects found while measuring suite runtime. None is a
speed win; each is a trap that would mislead the next person who tries one.

## Scope

### 1. A heavy fixture escapes the marker

`tests/test_validate_output.py:52-56` defines a **second** module-scoped
fixture also named `seven_day_schema_run` — a name listed in
`_HEAVY_MODULE_FIXTURES` (`tests/conftest.py:190-195`). It escapes the
marker because its two consumers reach it via
`request.getfixturevalue(fixture_name)` (`:707`, `:733`) with the name
passed as a parametrize string (`:696-697`, `:724`), so it never enters
`item.fixturenames` and `_item_is_heavy` (`conftest.py:198`) cannot see it.

Harmless *today* only because that fixture omits `interval_seconds` and so
inherits `_CHEAP_INTERVAL_SECONDS_DEFAULT = 60.0` (`conftest.py:36`) —
10,080 rows, ~8.7 MB. The trap: anyone adding `interval_seconds=1.0` there
gets a ~520 MB fixture in the parallel lane with no marker and no failure.
`tests/test_heavy_marker.py:49` asserts the *name* classifies heavy, which
passes while the actual item does not — so the existing guard does not
cover this shape.

### 2. A 1s-dependent test lacks the `full_resolution` marker

`tests/test_correctness.py:431` `test_anomalies_match_declared_value` does
exact-timestamp lookups (`rows[ts]`) against manifest timestamps derived
from `time_offset`. At a 60s interval those snap to minute boundaries and
miss, so the test genuinely requires 1s resolution — but it carries no
`@pytest.mark.full_resolution`. Its sibling `test_manifest_csv_cross_check`
(`:104`) carries the marker and documents the identical dependency at
`:113-116`. The marker exists to make these sites auditable; this one is
invisible to that audit.

### 3. Two fixture docstrings overstate size by ~5x

Measured against a real session:

| Docstring claim | Location | Measured |
|---|---|---|
| "~1.3 GB of output" | `tests/conftest.py:347-348` | **264 MiB on disk; 4.12s setup-only** |
| "multiple minutes and ~9 GB" | `tests/conftest.py:386` | **1.81 GiB on disk; 29.08s setup-only** |

The planning snapshot also cited a `pyproject.toml` `~5 GB` comment, but prior
performance work had already removed it before implementation. The live
pytest configuration now documents measured suite timings instead, so no
`pyproject.toml` change is required here.

Also worth correcting while in the file: the GB figures describe **on-disk
output**, not RSS. Measured peak RSS is 8-11 GB depending on lane and
worker count, and the disk/RAM distinction matters because the CI runner
has 16 GB RAM but only 14 GB SSD.

## Requirements

- Make the marker registry cover the `getfixturevalue` shape, or make the
  escape impossible. Options for `design.md`: detect
  `getfixturevalue`-by-parametrize at collection, forbid duplicate fixture
  names that collide with `_HEAVY_*_FIXTURES` entries, or rename the
  `test_validate_output.py` fixture so the collision cannot recur.
  Whichever is chosen, `tests/test_heavy_marker.py` must gain a case that
  fails against today's code.
- Add `@pytest.mark.full_resolution` to `test_anomalies_match_declared_value`
  with the same rationale comment its sibling carries.
- Correct both conftest docstrings to the measured sizes, and state that
  the figures are on-disk output rather than resident memory.
- Sweep for other stale size or timing claims in `tests/conftest.py`,
  `pyproject.toml`, and `CLAUDE.md` in the same pass — the doc-drift rule
  in the pre-PR checklist applies to numbers, not just prose.

## Acceptance criteria

- [x] A test fails against current `main` and passes after the marker fix,
      demonstrating the escape is really closed rather than documented.
- [x] Temporarily changing the renamed `test_validate_output.py` fixture to
      `interval_seconds=1.0` and registering that renamed fixture as heavy
      causes its consumers to be marked heavy; revert both changes.
- [x] `test_anomalies_match_declared_value` carries `full_resolution` and
      the `full_resolution` audit lists it.
- [x] Every size figure in `tests/conftest.py` matches a measurement taken
      in this task, with the measurement method recorded in the PR.
- [x] `pyproject.toml` no longer contains the planning snapshot's stale
      memory-budget derivation; prior performance work had already removed it.

## Non-goals

- Changing what any fixture generates — owned by
  `07-18-perf-heavy-fixture-trim`.
