# Heavy-lane two-worker trial — Design

## Decision boundary

The local result (`-n 0` 377.47s versus `-n 2 --dist loadfile` 259.79s,
11.25 GB peak RSS) justifies a runner trial but not adoption. The public runner
has 16 GB RAM and 14 GB SSD; the developer host has 48 GB RAM and much more
free disk. Adopt only when the runner trial observes peak system memory at or
below 12 GB and at least 2 GB of root-filesystem space after pytest.

## Trial diagnostics

Keep diagnostics in the heavy job so they observe the same runner and fixture
lifecycle as pytest:

1. Before pytest, print `nproc`, `free -m`, and `df -h /`.
2. Sample `/proc/meminfo` once per second while the heavy pytest process and
   xdist workers run. Track `MemTotal - MemAvailable` and write the maximum to
   a diagnostic file. System used memory is the relevant ceiling; a single
   process's `time -v` RSS does not sum concurrently resident xdist workers.
3. In an `if: always()` step, print the sampled peak and `df -h /` so evidence
   survives pytest failure.
4. Run the existing coverage preparation/upload only when pytest succeeds.

The diagnostic implementation may be inline shell for the one-run experiment.
Remove it from the final workflow after recording the result; do not add a
permanent tool unless repeated runner experiments justify one.

## Preserved contracts

- `--dist loadfile` stays with two workers so file-owned session fixtures do
  not fan out across workers.
- `-m heavy` still selects the auto-marked GB-scale closure and exits 5 if the
  partition becomes empty.
- The heavy coverage artifact name and downstream combine/aggregate jobs do
  not change.
- The light lane remains `-n 2` after its four-worker trial missed the
  pre-committed adoption threshold; it is not part of this experiment.

## Failure handling

- Peak memory above 12 GB: restore `-n 0`; record the peak and treat fixture
  memory as the blocker.
- Post-run free disk below 2 GB or ENOSPC: restore `-n 0`; record disk evidence
  and prioritize `07-18-perf-heavy-fixture-trim` before a re-trial.
- Test or coverage failure unrelated to capacity: fix the regression and rerun
  the same pre-committed trial; do not fit a new threshold to the outcome.
