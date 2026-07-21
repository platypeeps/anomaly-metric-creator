# Right-size light-lane pytest workers for the public runner

## Goal

The light CI lane is sized for a runner this repo does not have. The
public-repo `ubuntu-latest` runner is **4 vCPU / 16 GB / 14 GB SSD**;
`CLAUDE.md` and the light worker count assume a 7 GB, effectively 2-core box.
Raise the light lane to `-n 4` and correct the stale premise so the next reader
does not re-derive the wrong constraint. The independent evidence-gated heavy
trial is tracked by `07-20-perf-ci-heavy-worker-trial`.

## Measurement context

Local, 14-core / 48 GB darwin, CPython 3.14.6. Local-to-CI factor ~1.9x.

| Configuration | Wall time | Peak RSS | Result |
|---|---|---|---|
| heavy `-n 0` (current) | 377.47s | not captured | 48 passed |
| heavy `-n 2 --dist loadfile` | **259.79s (-31%)** | **11.25 GB** | 48 passed |
| light `-n 2` (current) | 195.66s | 9.70 GB | 1555 passed |
| light `-n 4` | **125.34s (-36%)** | **8.29 GB** | 1555 passed |
| light `-n 10` | 121.43s | 8.75 GB | 1555 passed |

Two non-obvious results: `-n 4` on the light lane is both faster and lower-peak
than `-n 2`, and there is essentially no gain past `-n 4` because
`--dist loadfile` bounds parallelism by file count, not worker count.

Projected CI effect: light 364s -> **~233s**; heavy 723s -> **~499s**.

## Requirements

- Raise the light lane from `-n 2` to `-n 4` (`.github/workflows/ci.yml:398`).
  This is the low-risk half: peak RSS measured *lower* than the current
  setting, and the lane excludes every GB-scale fixture by construction.
- Correct `CLAUDE.md`: replace the "7 GB standard runner" premise with the
  public-repo 4 vCPU / 16 GB / 14 GB SSD figures, and note that standard-runner
  minutes are free on public repos so wall clock is the optimisation target.
- Keep `--dist loadfile` on both lanes. `--dist load` would scatter a file's
  tests across workers and re-instantiate its session fixtures per worker,
  which is the exact cost `pyproject.toml:67-81` exists to prevent.

## Acceptance criteria

- [ ] Light lane runs `-n 4`; a full-matrix run shows its wall clock drop by
      >= 100s versus the `29631419630` baseline of 364s.
- [ ] All 1645 tests still pass, and the heavy/light partition still sums to
      the full collected count.
- [ ] The `-m heavy` job still treats an empty marker partition as a hard
      failure (pytest exit 5) rather than spilling GB fixtures into the
      parallel lane.
- [ ] `CLAUDE.md` states the correct runner specification and no longer refers
      to a 7 GB runner.

## Non-goals

- Local `addopts` changes — owned by `07-18-perf-local-test-split`.
- Job-level parallelism — owned by `07-18-perf-ci-lane-parallelization`.
- Heavy-lane worker changes and runner diagnostics — owned by
  `07-20-perf-ci-heavy-worker-trial`.

## Part A evidence (2026-07-20)

- The current suite partitions exactly into 48 heavy and 1597 non-heavy tests,
  matching the 1645-test full collection.
- The four-worker non-heavy rehearsal completed with 1595 passed and 2 expected
  real-client-smoke skips in 130.98s (131.19s wall clock).
- The CI contract and heavy-marker focused suite passed: 55 tests in 2.06s.
- Remote acceptance remains open until the full-matrix PR run demonstrates at
  least 100 seconds of light-lane savings against run `29631419630`.
