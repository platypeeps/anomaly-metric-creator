# Fix local pytest defaults that duplicate GB-scale fixtures

## Goal

A bare `.venv/bin/pytest` runs the whole suite at `-n 4`, so each GB-scale
session fixture is rebuilt **once per worker that touches a consuming
file** — up to 4x. The CI split (`-n 0 -m heavy`, then the light remainder)
builds each fixture exactly once. Local developers should get that same
property by default instead of the slowest available configuration.

## Measurement context

`pyproject.toml:86` sets `addopts = "-ra --strict-markers --dist loadfile -n 4"`
for all tests. The comment at `:67-81` correctly notes session fixtures are
instantiated per worker and that `--dist loadfile` bounds this to one
instantiation per file — but multiple *files* consume the same fixture, so
the real bound is `min(consuming files, workers)`:

| Fixture | Consuming files | Instantiations at `-n 4` |
|---|---|---|
| `n3_one_day_dataset_dir` | 4 | **4x** |
| `seven_day_run` | 6 | **4x** |
| `seven_day_schema_run` | 3 | **3x** |
| `n3_seven_day_dataset_dir` | 2 | **2x** |

The `~5 GB` budget at `pyproject.toml:75` is `4 x 1.3 GB` — the author
accounted for the 1-day fixture's fan-out but not the 7-day one.

Measured on 14-core / 48 GB darwin:

| Configuration | Wall time | Peak RSS |
|---|---|---|
| heavy `-n 0` | 377.47s | not captured |
| heavy `-n 2` | 259.79s | 11.25 GB |
| light `-n 2` | 195.66s | 9.70 GB |
| light `-n 4` | 125.34s | 8.29 GB |
| light `-n 10` | 121.43s | 8.75 GB |

`-n 10` buys 3% over `-n 4`: `--dist loadfile` caps parallelism at file
granularity, so wide `-n` values are wasted on this suite regardless of
core count.

## Requirements

- Give developers a one-command split run that builds each heavy fixture
  once. Options to weigh in `design.md`: a documented two-command sequence,
  a `Makefile` / `just` target, or a `tox`-style alias. Do **not** try to
  express the split inside `addopts` — pytest runs one invocation.
- Correct `docs/DEVELOPMENT_CYCLE.md:62-63`, which currently hands
  developers the CI runner's `-n 2` to run on a 14-core machine.
- Update the `pyproject.toml:67-81` comment: state that the `min(files,
  workers)` bound is the real one, that the `~5 GB` figure covers only the
  1-day fixture, and that `-n` above 4 does not help under
  `--dist loadfile`.
- Reconsider the `-n 4` default itself. It is right for the light lane and
  wrong for a whole-suite run; if the default stays, the docs must say that
  a bare `pytest` is the slow path and name the fast one.
- Keep `-n 0` documented as the required mode for `pdb` — `-n 1` still
  spawns a worker subprocess and breaks interactive debugging.

## Acceptance criteria

- [ ] A documented single command runs the full suite via the split and is
      measurably faster than a bare `.venv/bin/pytest` on the same machine;
      the PR records both timings.
- [ ] `docs/DEVELOPMENT_CYCLE.md` no longer recommends the CI runner's
      worker count for local use.
- [ ] The `pyproject.toml` comment states the correct fan-out bound and the
      `--dist loadfile` saturation point.
- [ ] Whatever the default becomes, `pytest` with no arguments still passes
      the full suite — no one is left with a broken bare invocation.
- [ ] `CLAUDE.md`'s parallel-execution section matches the new guidance.

## Non-goals

- CI worker counts — owned by `07-18-perf-ci-worker-counts`.
- Changing which tests are marked heavy.
