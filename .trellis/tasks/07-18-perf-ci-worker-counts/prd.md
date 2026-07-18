# Right-size pytest worker counts for the 4-vCPU public runner

## Goal

Both CI lanes are sized for a runner this repo does not have. The public-repo
`ubuntu-latest` runner is **4 vCPU / 16 GB / 14 GB SSD**; `CLAUDE.md` and the
worker counts assume a 7 GB, effectively 2-core box. Raising the light lane to
`-n 4` and trialling `-n 2` on the heavy lane recovers measured time, and the
stale premise gets corrected so the next reader does not re-derive the wrong
constraint.

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
- Trial `-n 2 --dist loadfile` on the heavy lane (`ci.yml:397`) on a branch
  before adopting. This is the half that needs CI evidence, because:
  - measured peak RSS is 11.25 GB against a 16 GB runner (~70% utilisation),
    and the local box has 48 GB so it cannot reproduce the ceiling;
  - the runner has **14 GB of SSD total**, and the heavy partition's derived
    artifacts alone are large (2.8 GB `gauges.csv` from `seven_day_gauges_run`,
    1.5 GB each for the N=3 gauges and combined outputs). Disk — not RAM — is
    a plausible cause of the historical failure that `CLAUDE.md` records as
    an OOM.
  - Capture `df -h` and peak memory in the trial run so the decision rests on
    observed headroom rather than inference.
- If the heavy `-n 2` trial shows insufficient headroom, keep `-n 0` and record
  the observed numbers in `CLAUDE.md` so the next attempt starts from evidence.
  Landing `07-18-perf-heavy-fixture-trim` first would free both RAM and disk and
  make a re-trial worthwhile.
- Correct `CLAUDE.md`: replace the "7 GB standard runner" premise with the
  public-repo 4 vCPU / 16 GB / 14 GB SSD figures, and note that standard-runner
  minutes are free on public repos so wall clock is the optimisation target.
- Keep `--dist loadfile` on both lanes. `--dist load` would scatter a file's
  tests across workers and re-instantiate its session fixtures per worker,
  which is the exact cost `pyproject.toml:67-81` exists to prevent.

## Acceptance criteria

- [ ] Light lane runs `-n 4`; a full-matrix run shows its wall clock drop by
      >= 100s versus the `29631419630` baseline of 364s.
- [ ] Heavy lane either runs `-n 2` with a trial run demonstrating both memory
      and disk headroom, or stays `-n 0` with the measured reason recorded.
- [ ] All 1603 tests still pass, and the heavy/light partition still sums to
      the full collected count.
- [ ] The `-m heavy` step still runs first, so a broken marker collects zero
      tests and fails fast (pytest exit 5) rather than spilling GB fixtures
      into the parallel lane.
- [ ] `CLAUDE.md` states the correct runner specification and no longer refers
      to a 7 GB runner.

## Non-goals

- Local `addopts` changes — owned by `07-18-perf-local-test-split`.
- Job-level parallelism — owned by `07-18-perf-ci-lane-parallelization`.
