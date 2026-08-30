# Long-form writer test dedupe — Implementation Plan

## Execution Order

1. Branch from `main`. Correct the PRD's saving estimate to ~74s local /
   ~140s CI per `design.md` — do this first so the PR description and the
   task artifacts agree from the outset.
2. Co-locate `n3_one_day_combine_run` and its cheap header/hash guards with
   `n3_one_day_gauges_run` in `tests/test_gauges_file.py`; promoting a
   module-scoped fixture through `conftest.py` would still duplicate writer
   work across loadfile workers. Cache each streaming digest in its fixture
   and reuse it in both absolute-hash and equality assertions. Confirm the
   `heavy` marker still applies after the move:
   ```bash
   .venv/bin/pytest -m heavy --collect-only -q | tail -1
   ```
   The expected count is 46 after replacing three tests with one. (`tail -1`
   reads pytest's summary line; `grep -c .` would count that line too and
   report N+1.)
3. Add `test_n3_long_form_writer_outputs_match` using digests computed with
   `conftest.sha256_path` (streaming; the resource-cost rule forbids whole-file
   reads). Run it alone and confirm it passes.
4. Run mutation check C (make the outputs differ artificially) and confirm
   the new test fails. Restore.
5. Remove the three redundant tests
   (`test_n3_combined_dimension_values_match_per_component_csvs`,
   `test_n3_combined_chronological_order`,
   `test_n3_combined_no_empty_value_cells`). Keep
   `test_n3_combined_has_long_form_header` and
   `test_n3_combined_byte_identical_one_day`.
6. Run mutation checks A and B; record which test caught each.
7. Update `CLAUDE.md`'s combine section to state that the N=3 long-form
   outputs are byte-identical by construction and that the suite asserts it
   once rather than re-verifying invariants per writer.
8. Draft PR -> pre-PR checklist (test hygiene, test path determinism, and
   test resource cost are the load-bearing headings) -> ready -> merge.

## Validation Plan

```bash
# the affected modules, serial so timings are readable
.venv/bin/pytest tests/test_combine.py tests/test_gauges_file.py -n 0 -q

# heavy partition timing vs the 377.47s baseline
/usr/bin/time -l .venv/bin/pytest -n 0 -m heavy -q -p no:cacheprovider

# partition integrity
.venv/bin/pytest -m heavy --collect-only -q | tail -1
.venv/bin/pytest -m "not heavy" --collect-only -q | tail -1

.venv/bin/pytest            # full suite
.venv/bin/pre-commit run --all-files
```

Watch peak disk during the heavy run — the new test's closure holds two
~1.5 GB outputs simultaneously, which matters for the 14 GB CI runner:

```bash
du -sh "$(ls -dt "$(python3 -c 'import tempfile;print(tempfile.gettempdir())')"/pytest-of-*/pytest-[0-9]* | head -1)"
```

## Documentation And Spec Updates

- `CLAUDE.md` combine section — the byte-identity contract and where it is
  asserted.
- This task's `prd.md` — the corrected saving estimate (step 1).
- `.trellis/tasks/07-18-perf-suite-runtime/prd.md` task map — update the
  expected saving so the parent's projected total stays honest.

## Review Notes

- Lead with the two identical hash constants quoted verbatim. The whole
  justification rests on that fact and it is one line of evidence.
- Report all three mutation checks with the test that caught each. Removing
  tests is the kind of change a reviewer is right to be suspicious of;
  proving the remaining coverage still fails on real regressions is the
  only argument that should carry.
- Be explicit that the 1.5 GB write is **not** removed and why — a reviewer
  reading "dedupe" may expect the fixture to go away, and the PRD's original
  estimate implied it.
- Note the disk-footprint interaction with
  `07-18-perf-ci-worker-counts` Part B so it is not discovered as a
  surprise there.

## Follow-Ups

- If the equality assertion holds indefinitely, consider whether the combine
  writer needs its own N=3 derivation at all, or whether a much smaller
  N=3 input (fewer components) would prove the same dispatch. That is a
  larger scope question and belongs in `07-18-perf-heavy-fixture-trim`.

## Implementation Result (2026-07-21)

- Co-located the combine fixture and cheap N=3 guards in
  `tests/test_gauges_file.py`; both streaming digests are computed once and
  reused by independent absolute-hash checks plus runtime equality.
- Replaced three redundant combine-output scans with one digest comparison.
  Heavy collection moved from 48 to the expected 46 tests.
- Mutation checks passed: a combine-only component omission failed the
  combine hash, a shared-writer header mutation failed the gauges hash, and
  an appended combine byte failed runtime equality. All mutations were
  restored immediately.
- Serial heavy timing passed 46 tests in 276.10s, saving 101.37s (26.9%)
  against the 377.47s baseline.
- The normal four-worker suite passed 1,678 tests with 2 expected skips in
  231.75s; all pre-commit guards passed.
