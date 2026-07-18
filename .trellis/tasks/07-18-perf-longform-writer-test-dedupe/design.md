# Long-form writer test dedupe — Design (SD Work Designs, 2026-07-18)

## Overview

At N=3 the combine writer and the gauge writer emit byte-identical output —
`tests/test_combine.py:489` and `tests/test_gauges_file.py:486` hold the
same 64 hex characters. The suite proves that, then re-verifies the same
structural invariants by scanning the second 1.5 GB copy three more times.

## Correction to the PRD estimate

The PRD claims ~105s local / ~200s CI. **That over-counts.** It includes the
30.84s `n3_one_day_combine_run` setup, which is the `combine_logs` call that
produces the file — and that call is irreducible if the suite is to keep
asserting the combine writer's own output bytes, which it should (combine is
a separate entry point with its own dispatch and autodiscovery).

The recoverable cost is the three redundant full-file scans:

| Test | Local |
|---|---|
| `test_n3_combined_dimension_values_match_per_component_csvs` (`:556`) | 28.80s |
| `test_n3_combined_chronological_order` (`:592`) | 22.99s |
| `test_n3_combined_no_empty_value_cells` (`:609`) | 22.00s |
| **Recoverable** | **~74s local / ~140s CI** |

Update the PRD's figure when this design is accepted.

## Proposal

Each of the three tests opens `combined_metrics_unified.csv`, iterates every
row with `csv.DictReader`, and asserts one property. Their docstrings each
name `_write_combined_long_form` as the code under guard — but that function
now delegates to `csv_layout.write_long_form_merge`
(07-06-long-form-merge-writer-dedupe), which is the *same* function the
gauge writer calls and which the gauges-side twins already exercise over the
same bytes.

Replace all three with a single equality assertion:

```python
def test_n3_combined_matches_gauges_bytes(n3_one_day_combine_run, n3_one_day_gauges_run):
    """The N=3 long-form combine and gauge writers share
    csv_layout.write_long_form_merge, so their outputs are byte-identical by
    construction. Asserting that directly transfers every structural
    property verified on the gauges side (header shape, dimension-tuple
    parity, chronological order, no empty value cells) without a second
    full scan of a ~1.5 GB file. A divergence fails here loudly rather than
    requiring someone to notice two locked constants drifted apart."""
```

The two fixtures live in different modules, so the test belongs in whichever
module can request both — most likely `tests/test_combine.py` importing the
gauges fixture, or a shared conftest-level fixture. Resolve during
implementation; do **not** duplicate a third derivation to make the import
tidy.

**Keep both absolute locked hashes.** `N3_GAUGES_ONE_DAY_HASH` and
`N3_COMBINED_ONE_DAY_HASH` are the same value today, but they guard
different entry points. Collapsing to one constant plus an equality
assertion is tempting and *slightly* better for maintenance, but it means a
change that alters both writers identically would pass — an absolute hash
on each side catches that. Prefer: keep both constants, add the equality
assertion, and note in a comment that the two values are expected to match.

**Keep `test_n3_combined_has_long_form_header` (`:522`).** It reads one row
(the header) and costs nothing measurable. It is the cheap smoke that tells
a reader what layout to expect.

## Boundaries And Non-Goals

- No change to the wide-form (N=1) combine tests.
- No change to fixture resolution or `--emit` selections — that is
  `07-18-perf-heavy-fixture-trim`, which must land *after* this task since
  both edit the same files.
- No removal of combine-specific coverage: the `SystemExit` on missing
  inputs, wide/long dispatch, and autodiscovery are untouched.

## Affected Files

`tests/test_combine.py` (remove three tests, add one), possibly
`tests/test_gauges_file.py` or `tests/conftest.py` (fixture visibility),
`CLAUDE.md` combine section (record the byte-identity contract).

## Risks And Edge Cases

- **Cross-module fixture access.** `n3_one_day_gauges_run` is module-scoped
  in `tests/test_gauges_file.py`. Requesting it from `tests/test_combine.py`
  requires promoting it to `conftest.py`. Promoting a module fixture changes
  its scope semantics and can change which worker instantiates it under
  `--dist loadfile` — verify the `heavy` marker still applies afterwards
  (it should, transitively via `n3_one_day_dataset_dir`).
- **Both derivations now live in one test's closure**, so that test holds
  two ~1.5 GB files at once on disk. Against the runner's 14 GB SSD this is
  the change most likely to interact with
  `07-18-perf-ci-worker-counts` Part B. Measure disk during validation.
- **Equality passing vacuously** if a bug makes both outputs empty. The
  absolute locked hashes on each side prevent this — another reason to keep
  them rather than collapse to equality alone.

## Validation

- Mutation check A: perturb `csv_layout.write_long_form_merge` (e.g. change
  the tie-break order); the suite must fail. Record which test caught it.
- Mutation check B: perturb only the combine writer's dispatch in
  `combine_impl.py`; the suite must still fail, proving combine retains
  independent cover.
- Mutation check C: make the two outputs differ artificially; the new
  equality test must fail.
- `pytest -m heavy -n 0` drops by >= 60s from the 377.47s baseline.
