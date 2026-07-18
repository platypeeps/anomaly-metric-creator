# Heavy-marker escape and fixture docs — Implementation Plan

## Execution Order

1. Branch from `main`. Record the baseline partition counts — every later
   step is checked against these:
   ```bash
   .venv/bin/pytest -m heavy --collect-only -q | tail -1
   .venv/bin/pytest -m "not heavy" --collect-only -q | tail -1
   .venv/bin/pytest --collect-only -q | tail -1
   ```
2. **Write the failing test first.** Add a real-collection case to
   `tests/test_heavy_marker.py` that exercises the `getfixturevalue`
   escape — e.g. via `pytester`/`runpytest` collecting a scenario that
   mirrors `test_validate_output.py`'s parametrize shape, asserting the item
   is marked heavy. Run it; **it must fail on current `main`.** If it
   passes, the escape has not been reproduced and the rest of the task is
   built on a wrong premise.
3. Fix the collision: rename `seven_day_schema_run` in
   `tests/test_validate_output.py:52` to something scoped to that module
   (e.g. `validator_seven_day_schema_run`) and update both parametrize
   lists (`:696-697`, `:724`). Re-run the step-2 test; it must now pass.
4. Add the collision guard: a test asserting no fixture name in
   `_HEAVY_SESSION_FIXTURES | _HEAVY_MODULE_FIXTURES` is defined outside
   `tests/conftest.py`. Before adding, sweep for other collisions so it
   lands green:
   ```bash
   rg -n "^def (seven_day_run|n3_one_day_dataset_dir|n3_seven_day_dataset_dir|seven_day_schema_run|synthetic_n3_run)\b" tests/
   ```
5. Re-check partition counts against step 1. Any change must be explained
   and confirmed to be the *correct* classification — not merely different.
6. Add `@pytest.mark.full_resolution` to
   `tests/test_correctness.py:431` with the rationale comment its sibling
   carries at `:113-116`. Check nothing asserts a fixed count of marked
   sites:
   ```bash
   rg -n "full_resolution" tests/ tools/ docs/ CLAUDE.md
   ```
7. Correct the size figures in `tests/conftest.py:347-348` and `:386` to the
   measured 264 MB and ~1.85 GB, and state they are on-disk output rather
   than resident memory. Fix the derived `~5 GB` budget comment at
   `pyproject.toml:75`.
8. Sweep for other stale numbers rather than fixing only the two known:
   ```bash
   rg -n "1\.3 GB|9 GB|5 GB|~[0-9]+ ?GB" tests/ pyproject.toml CLAUDE.md docs/
   ```
9. Draft PR -> pre-PR checklist (test path determinism, test hygiene, doc
   sync) -> ready -> merge.

## Validation Plan

```bash
# the new marker test: fails before the fix, passes after
.venv/bin/pytest tests/test_heavy_marker.py -n 0 -q

# the renamed fixture's consumers still pass
.venv/bin/pytest tests/test_validate_output.py -n 0 -q

# partition integrity vs the step-1 baseline
.venv/bin/pytest -m heavy --collect-only -q | tail -1
.venv/bin/pytest -m "not heavy" --collect-only -q | tail -1
.venv/bin/pytest --collect-only -q | tail -1

.venv/bin/pytest            # full suite
.venv/bin/pre-commit run --all-files
```

Manual check for the escape, done once and reverted: set
`interval_seconds=1.0` on the renamed validator fixture, re-run
`pytest -m heavy --collect-only -q`, and confirm its consumers now appear.
That is the scenario the guard exists to prevent, and it is worth seeing it
behave correctly.

## Documentation And Spec Updates

- `tests/conftest.py` fixture docstrings — measured sizes, on-disk vs RSS.
- `pyproject.toml:75` — the derived memory budget.
- `CLAUDE.md` if it repeats any corrected figure (the testing and CI
  sections both cite fixture sizes).
- Coordinate with `07-18-perf-heavy-fixture-trim`, which edits the same
  docstrings; whichever lands second re-reads rather than reapplies.

## Review Notes

- Lead with the failing-then-passing marker test. The escape is subtle
  enough that a reviewer will not take it on description alone, and
  "`test_heavy_marker.py` already covers this" is the obvious objection —
  pre-empt it by showing that file tests the predicate, not collection.
- Present the three fix options from `design.md` and why rename+guard was
  chosen over collection-time detection. Choosing the narrower fix
  deliberately is worth stating.
- The size corrections are ~5x. Say how they were measured (`du -sh` on a
  real pytest session directory) so the new numbers are auditable rather
  than a second unsourced claim.
- Note that `pyproject.toml:75`'s budget derived from the stale figure —
  that is the concrete evidence that doc drift here already cost something.

## Follow-Ups

- If the collision guard proves useful, consider extending it to other
  registries where a name outside `conftest.py` would silently shadow a
  conftest fixture.
- The `full_resolution` marker has no automated audit today; if the set of
  1s-dependent tests keeps growing, a test asserting every
  `interval_seconds=1.0` call site carries the marker would close the gap
  the same way the heavy marker's auto-application does.
