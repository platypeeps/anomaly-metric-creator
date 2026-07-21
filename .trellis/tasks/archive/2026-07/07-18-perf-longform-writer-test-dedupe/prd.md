# Collapse duplicated long-form writer tests (gauges vs combined)

## Goal

At N=3 the combine writer and the gauge writer produce **byte-identical**
output — both route through `csv_layout.write_long_form_merge`. The suite
proves this with two identical hash constants, then spends ~105s locally
writing a second 1.5 GB file and re-checking the same structural invariants
over it. Once the two hashes are asserted equal, every structural property
of one file transfers to the other for free.

Of that ~105s, **~74s is recoverable** — the ~31s write itself must stay
(see the table below). The recoverable figure is the one to track.

## Measurement context

The two locked constants are the same 64 hex characters:

```
tests/test_combine.py:489      "511f455075c8f82ab765dea783230a5a23404607958c4b9da93bcb6005368c5c"
tests/test_gauges_file.py:486  "511f455075c8f82ab765dea783230a5a23404607958c4b9da93bcb6005368c5c"
```

`tests/test_combine.py:544-547` already concedes the mechanism: the combine
writer "consumes the same per-(component, instance) iterators and tie-break
order as `write_gauges_csv`".

Cost, from a local `-m heavy --durations` run (project to CI at ~1.9x):

| Stage | Local | Recoverable? |
|---|---|---|
| `n3_one_day_combine_run` setup (writes the 1.5 GB second copy) | 30.84s | **No** — see below |
| `test_n3_combined_dimension_values_match_per_component_csvs` | 28.80s | yes |
| `test_n3_combined_chronological_order` | 22.99s | yes |
| `test_n3_combined_no_empty_value_cells` | 22.00s | yes |
| **Recoverable total** | **~74s local / ~140s CI** | |

**Estimate corrected during design.** An earlier draft of this PRD claimed
~105s local / ~200s CI by counting the fixture setup. That 30.84s is the
`combine_logs` call that produces the file, and it is irreducible if the
suite is to keep asserting the combine writer's own output bytes — which it
should, since combine is a separate entry point with its own dispatch and
autodiscovery. The recoverable cost is the three redundant full-file scans
only.

Each module independently hardlinks all 16 files of the 264 MB N=3 dataset
into its own tmp dir (`test_gauges_file.py:514-516`,
`test_combine.py:515-517`) and then runs a full ~22M-row write.

## Requirements

- Keep both byte-identity assertions. `write_gauges_csv` and the combine
  writer are separate entry points; each needs its own locked-hash guard so
  a regression in one is caught even if the other is untouched.
- Replace the duplicated structural tests on the combine side with a single
  equality assertion against the gauges output. The four near-verbatim pairs:
  - `test_n3_combined_has_long_form_header` / `test_n3_gauges_csv_has_long_form_header`
  - `test_n3_combined_dimension_values_match_per_component_csvs` / `test_n3_gauges_csv_dimension_values_match_per_component_csvs`
  - `test_n3_combined_chronological_order` / `test_n3_gauges_csv_chronological_order`
  - `test_n3_combined_no_empty_value_cells` (the gauges absolute hash already
    locks the shared writer's dropped-cell behavior; runtime output equality
    transfers that exact-byte guarantee without another full-file scan)
- Prefer asserting `sha256(combined) == sha256(gauges)` **derived at runtime**
  over two independently maintained constants — a future divergence should
  fail loudly rather than requiring someone to notice two constants drifted
  apart. Keep at least one absolute locked hash so the pair cannot both drift
  together silently.
- Do not delete coverage of the combine writer's *own* dispatch: the wide-form
  path, the `SystemExit` on missing inputs, and autodiscovery are unrelated to
  this dedupe and stay untouched.
- Record in `CLAUDE.md`'s combine section that the N=3 long-form outputs are
  byte-identical by construction and that the test suite asserts it once.

## Acceptance criteria

- [x] The N=3 one-day dataset produces one long-form output per writer, and
      the suite no longer repeats the three full ~22M-row structural scans on
      the combine output.
- [x] A deliberate mutation to `csv_layout.write_long_form_merge` still fails
      the suite — verify by mutation-testing the change locally and recording
      the result in the PR description.
- [x] A deliberate mutation to *only* the combine writer's dispatch still
      fails, proving the combine path retains independent cover.
- [x] `pytest -m heavy -n 0` drops by >= 60s versus the 377.47s local
      baseline.
- [x] No locked hash is weakened; if a constant is removed, the PR names the
      assertion that replaced it.

## Non-goals

- Changing fixture resolution — owned by `07-18-perf-heavy-fixture-trim`.
- Touching the wide-form (N=1) combine tests.
