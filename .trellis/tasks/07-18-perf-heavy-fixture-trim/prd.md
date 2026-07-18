# Trim heavy fixtures that generate data no test reads

## Goal

Three heavy fixtures generate hundreds of megabytes to gigabytes of
per-component CSVs that no consuming test opens. The generation cost is
real; the coverage it buys is not. Trim each to what its consumers
actually read.

**This task has a decision gate.** Two of the three trims require
re-locking a golden SHA-256 hash, which trades away full-resolution byte
coverage at that fixture. Get maintainer approval on the trade before
implementing — see "Decision gate" below.

## Measurement context

Fixture sizes measured from a real session, not estimated. Setup times from
a local `-m heavy --durations` run; project to CI at ~1.9x.

| Fixture | Generated | Read by consumers | Setup |
|---|---|---|---|
| `seven_day_schema_run` (`tests/test_schema_file.py:64-77`) | ~520 MB of CSVs | `schema.json` only (`:291-296`) | 28.27s |
| `n3_seven_day_dataset_dir` (`tests/conftest.py:377-404`) | ~1.85 GB | 2 assertions; one of them reads only a 26 KB `schema.json` | 31.33s |
| `synthetic_n3_run` (`tests/test_topology_multi_instance.py:51-127`) | ~110 MB, 6 components | `authservice.csv` only (`:259-267`) | 2.71s |

Two conftest docstrings overstate their fixture's size by ~5x
(`conftest.py:347-348` says "~1.3 GB" for a measured 264 MB;
`conftest.py:386` says "~9 GB" for a measured ~1.85 GB). Correcting those
is owned by `07-18-fix-heavy-marker-and-fixture-docs`, but this task should
not re-derive sizes from the stale numbers.

### Why `schema.json` does not need the data

`schema.json` is a pure function of (args, registry): `metadata` is built
from parsed args plus precomputed `total_seconds` / `n_rows`, and
`components` comes from the static registry. It never reads generated
metric values. `test_emit_schema_standalone_allowed`
(`tests/test_schema_file.py:97-106`) already proves `--emit schema` alone
is legal. The CSVs exist only so the document's `files` list can name them.

## Requirements

- **`seven_day_schema_run` -> cheap interval.** Re-lock
  `SCHEMA_SEVEN_DAY_HASH` at `interval_seconds=60`. The duration-dependent
  fields (`total_seconds`, `rows_per_component`) still differ between the
  1-day and 7-day documents, so the 1d-vs-7d distinction the test exists to
  check survives. Expected: ~520 MB / 28s -> ~9 MB / ~2s.
- **`n3_seven_day_dataset_dir` -> split the schema consumer off.**
  `tests/test_schema_file.py:608` pulls 1.85 GB to hash a 26 KB document.
  Give `seven_day_schema_run_n3` (`:535`) its own cheap `--emit schema`
  run and re-lock `SCHEMA_N3_SEVEN_DAY_HASH`. That leaves
  `n3_seven_day_dataset_dir` with exactly one consumer
  (`tests/test_instances_per_component.py:475`, `N3_SEVEN_DAY_HASHES`).
- **`synthetic_n3_run` -> narrow the emit.** Only `authservice.csv` is read.
  The other five components are required as in-memory topology inputs
  during generation but not on disk. Confirm whether the generator can
  avoid writing them (e.g. via `--components` narrowing that still preserves
  the topology chain); if it cannot without changing the coupling inputs,
  record that and leave the fixture alone rather than weakening the test.
- **`test_n3_1d_hashes_stable` (`tests/test_instances_per_component.py:492`)
  runs a second full N=3 1-day pass at 1s** inside an already-heavy test.
  Its sibling `test_n3_7d_hashes_stable` (`:522-545`) documents the correct
  reasoning: byte-stability is interval-independent — the invariant is
  "same args twice -> same bytes", not any specific locked hash — so it runs
  both passes at the cheap 60s default. Apply the same treatment to the
  1-day version. **This one needs no re-lock** and should land first as the
  free win.

## Decision gate

Before implementing the two re-locks, confirm with the maintainer:

1. Is full-resolution (1s) byte coverage of `schema.json` at 7 days worth
   ~520 MB and ~28s per CI run, given `schema.json` contains no per-row data?
2. Same question for the N=3 7-day schema document at ~1.85 GB.

If the answer to either is yes, drop that trim and keep the fixture. The
`test_n3_1d_hashes_stable` fix and the `synthetic_n3_run` investigation
proceed regardless.

## Acceptance criteria

- [ ] `test_n3_1d_hashes_stable` runs both passes at the 60s default,
      matching `test_n3_7d_hashes_stable`, with no locked hash changed.
- [ ] Each approved re-lock is a separate commit whose message records the
      old hash, the new hash, and the parameter that changed.
- [ ] Every trimmed fixture's docstring states what its consumers actually
      read, so the next reader does not restore the waste.
- [ ] `pytest -m heavy -n 0` drops by >= 30s versus the 377.47s local
      baseline (more if both re-locks are approved).
- [ ] No test loses an assertion. If a fixture narrows, the PR names each
      consuming test and confirms what it reads.
- [ ] Any trim declined at the decision gate is recorded in this PRD with
      the reason, so it is not re-proposed.

## Non-goals

- The gauges/combined duplication — owned by
  `07-18-perf-longform-writer-test-dedupe`. Land that first; these two
  tasks touch overlapping test files.
- `seven_day_run` itself. Its 24 consumers include 15 parametrized locked
  hashes; it is expensive but not wasteful.
