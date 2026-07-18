# Heavy fixture trim — Design (SD Work Designs, 2026-07-18)

## Overview

Four items, one of which is free and three of which trade full-resolution
byte coverage for time. The free one should land immediately; the other
three are behind the PRD's decision gate and must not be implemented before
it clears.

## Proposal

### Item 1 — `test_n3_1d_hashes_stable` (free, no re-lock)

`tests/test_instances_per_component.py:492` runs a **second** complete N=3
1-day pass at 1s resolution inside an already-heavy test (`_run(..., days=1,
...)`; `_run`'s `interval_seconds` defaults to `1.0` at `:29`). Its sibling
`test_n3_7d_hashes_stable` (`:522-545`) states the correct reasoning:
byte-stability is interval-independent — the invariant is "same args twice
-> same bytes", not any specific locked hash — so it runs both passes at the
cheap 60s default.

Apply the same treatment. Both passes move to 60s; no locked hash is
touched, because this test never asserts one. Measured cost today: 4.46s.

### Item 2 — `seven_day_schema_run` at 60s (needs re-lock)

`tests/test_schema_file.py:64-77` generates ~520 MB of per-component CSVs
(28.27s setup). Its single consumer (`:291-296`) reads **only**
`schema.json`.

`schema.json` is a pure function of (args, registry): `metadata` comes from
parsed args plus precomputed `total_seconds` / `n_rows`, and `components`
comes from the static registry. It never reads generated metric values —
`test_emit_schema_standalone_allowed` (`:97-106`) already proves `--emit
schema` alone is legal.

Re-locking `SCHEMA_SEVEN_DAY_HASH` at `interval_seconds=60` preserves what
the test checks: the duration-dependent fields (`total_seconds`,
`rows_per_component`) still differ between the 1-day and 7-day documents, so
the 1d-vs-7d distinction survives. Expected: ~520 MB / 28.27s -> ~9 MB /
~2s.

### Item 3 — split the schema consumer off `n3_seven_day_dataset_dir` (needs re-lock)

`tests/test_schema_file.py:608` pulls a **1.85 GB** fixture (31.33s setup)
to hash a **26 KB** `schema.json`. Give `seven_day_schema_run_n3` (`:535`)
its own cheap `--emit schema` run and re-lock
`SCHEMA_N3_SEVEN_DAY_HASH`.

That leaves `n3_seven_day_dataset_dir` with exactly one consumer
(`tests/test_instances_per_component.py:475`, `N3_SEVEN_DAY_HASHES`). Note
what this does *not* do: the 1.85 GB fixture still exists for that one test.
The item's value is decoupling, and it only becomes a large saving if
`N3_SEVEN_DAY_HASHES` is also re-locked coarser — which is a **separate,
larger** trade and is explicitly **out of scope here**. Raise it only if the
decision gate shows appetite.

### Item 4 — `synthetic_n3_run` narrowing (investigate, likely decline)

`tests/test_topology_multi_instance.py:51-127` generates 6 components
(~110 MB); only `authservice.csv` is read (`:259-267`). But the other five
are the topology *inputs* that produce the coupling under test — they are
needed in memory during generation even though nothing reads them on disk.

There is no `--emit` token that writes some components and not others, so
the only lever is `--components`, and narrowing that would change the
coupling chain and invalidate the test. **Expected outcome: decline**, and
record the reason so it is not re-proposed. Cost is only 2.71s, so this is
the lowest-value item regardless. Investigate briefly; do not engineer
around it.

## Boundaries And Non-Goals

- Not re-locking `N3_SEVEN_DAY_HASHES` or `DEFAULT_SEVEN_DAY_HASHES`. Those
  are the suite's primary full-resolution determinism guarantees.
- Not touching `seven_day_run` (24 consumers, 15 of them parametrized locked
  hashes — expensive but not wasteful).
- Not changing the `heavy` marker or partition membership.
- No `--emit` change to `n3_one_day_dataset_dir`; that fixture serves 19
  tests and is already narrow.

## Affected Files

`tests/test_instances_per_component.py` (item 1), `tests/test_schema_file.py`
(items 2-3, plus two hash constants), `tests/test_topology_multi_instance.py`
(item 4 if it proceeds), `tests/conftest.py` (docstrings — coordinate with
`07-18-fix-heavy-marker-and-fixture-docs`), `CLAUDE.md` schema section.

## Risks And Edge Cases

- **File overlap with `07-18-perf-longform-writer-test-dedupe`.** Both edit
  `tests/test_schema_file.py` and adjacent modules. Land the dedupe first;
  rebase this on it.
- **A re-lock is irreversible in review terms** — once the constant changes,
  the old bytes are no longer proven. Each re-lock gets its own commit
  recording old hash, new hash, and the changed parameter, so a revert is
  mechanical.
- **`schema.json`'s `files` list is emit-coupled**, which is exactly why
  `seven_day_schema_run` cannot derive from `seven_day_run`
  (`tests/conftest.py:184-186` states this correctly). Item 2 changes the
  *interval*, not the emit selection, so the `files` list is unaffected —
  do not conflate the two while editing.
- **Item 3 may shift heavy-marker membership.** If the N=3 schema test stops
  requesting `n3_seven_day_dataset_dir`, it stops being heavy and moves to
  the parallel lane. Verify the partition counts after the change and
  confirm the newly-light test is genuinely light.
- **Docstring coordination.** `07-18-fix-heavy-marker-and-fixture-docs`
  corrects the same docstrings' size figures. Whichever lands second must
  re-read rather than reapply.

## Validation

- Item 1: run `test_n3_1d_hashes_stable` before and after; it must still
  fail if `generate_component` is made non-deterministic (mutation check).
- Items 2-3: after re-locking, confirm the new hash is stable across two
  runs and that the 1d and 7d schema documents still differ in
  `total_seconds` / `rows_per_component`.
- Partition integrity and heavy-lane timing per the parent's protocol.
- Full suite plus `pre-commit run --all-files`.
