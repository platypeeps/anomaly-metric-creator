# Worker counts for the 4-vCPU runner — Design (SD Work Designs, 2026-07-18)

## Overview

The light lane is under-parallelized against a runner twice the size the
config assumes, and the change is safe on measured evidence. The original
design also covered an independent heavy-lane experiment; that evidence-gated
work now lives in `07-20-perf-ci-heavy-worker-trial` so each required PR has
its own Trellis lifecycle.

## Proposal

### Part A — light lane `-n 2` -> `-n 4` (land immediately)

Measured on 14-core / 48 GB darwin, 1555 tests:

| `-n` | Wall | Peak RSS |
|---|---|---|
| 2 (current) | 195.66s | 9.70 GB |
| **4** | **125.34s** | **8.29 GB** |
| 10 | 121.43s | 8.75 GB |

`-n 4` is faster *and* peaks lower than `-n 2`. The lower peak is not noise:
finishing sooner means fewer session fixtures are simultaneously alive, and
`--dist loadfile` caps how many distinct files (hence fixtures) are in
flight. Projected CI: 364s -> **~233s**.

`-n 10` buys 3% over `-n 4` — `--dist loadfile` bounds parallelism by file
count, so wide values are wasted regardless of core count. `-n 4` is
therefore both the CI-correct and the locally-optimal setting; there is no
tension to resolve.

### Follow-up — heavy lane `-n 0` -> `-n 2`

Measured: 377.47s -> **259.79s (-31%)**, 48 passed, **peak RSS 11.25 GB**.

The follow-up trial is required because two ceilings exist and the local box clears
both by a wide margin:

- **RAM**: 11.25 GB measured against a 16 GB runner is ~70% utilization.
  The local box has 48 GB, so it cannot demonstrate headroom.
- **Disk**: the runner has **14 GB SSD total**, and the heavy partition's
  derived artifacts are large — a 2.8 GB `gauges.csv` from
  `seven_day_gauges_run`, plus ~1.5 GB each for the N=3 gauges and combined
  outputs. Disk is the more likely cause of the historical failure that
  `CLAUDE.md` records as an OOM, and it is the ceiling nobody has measured.

`07-20-perf-ci-heavy-worker-trial` must therefore capture **both** `df -h` and peak memory, not just
pass/fail. A green run that finished with 200 MB of disk left is not
evidence of a safe setting.

### Part C — correct the premise

`CLAUDE.md` states a 7 GB standard runner. The repo is public
(`gh api repos/platypeeps/anomaly-metric-creator --jq .private` -> `false`)
and GitHub's standard `ubuntu-latest` for public repositories is
4 vCPU / 16 GB / 14 GB SSD.
Correct the figure, and add the consequence that makes it actionable:
standard-runner minutes are free on public repos, so wall clock — not
billed minutes — is the optimization target.

## Boundaries And Non-Goals

- No job-topology change (`07-18-perf-ci-lane-parallelization` owns that).
  These two tasks are independent and may land in either order.
- No local `addopts` change (`07-18-perf-local-test-split` owns that).
- `--dist loadfile` stays on both lanes. `--dist load` would scatter a
  file's tests across workers and re-instantiate its session fixtures per
  worker — precisely the cost `pyproject.toml:67-81` exists to prevent.

## Affected Files

`.github/workflows/ci.yml:397-398` (the two `-n` values), `CLAUDE.md`
(runner specification and the free-minutes consequence).

## Risks And Edge Cases

- **The heavy trial is the risk; this task is not.** Ship this task before the
  follow-up experiment; the light-lane evidence is unambiguous.
- **Peak RSS is measured on darwin.** Linux allocator behavior and numpy's
  `<U` string-array intermediates may differ. The trial is the only way to
  know; treat 11.25 GB as an indication, not a Linux prediction.
- **`-m heavy` must still run first if the lanes remain in one job**, so a
  broken marker collects zero tests and pytest exits 5. If
  `07-18-perf-ci-lane-parallelization` has already landed, the lanes are
  separate jobs and this ordering property lives in the heavy job alone.
- **A green trial is not proof of a stable setting** if headroom is thin.
  Pre-commit to a decision rule before running: adopt only if peak memory
  is <= 12 GB and free disk stays >= 2 GB.
- Landing `07-18-perf-heavy-fixture-trim` first would free both RAM and
  disk and make a failed trial worth repeating.

## Validation

- Part A: a full-matrix run showing the light lane >= 100s below its 364s
  baseline, all 1555 tests passing.
- Follow-up task: a trial run with an added diagnostic step capturing `nproc`,
  `free -m`, and `df -h` before and after the heavy invocation. Record the
  numbers in the PR whether or not the change is adopted — a failed trial
  with data is a durable result; a failed trial without data gets repeated.
- Full suite locally at both settings before pushing.
