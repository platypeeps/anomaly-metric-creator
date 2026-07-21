# Trial two heavy-lane pytest workers on the public runner

## Goal

Trial -n 2 --dist loadfile for the heavy CI lane with measured memory and disk headroom; adopt only at <=12 GB peak memory and >=2 GB free disk.

## Requirements

- Change only the heavy CI lane from `-n 0` to
  `-n 2 --dist loadfile` for the trial.
- Capture the runner CPU count, total and available memory, peak system memory
  used while pytest and both workers run, and root-filesystem free space before
  and after the heavy partition.
- Pre-commit to the decision rule: adopt two workers only when peak system
  memory is at most 12 GB and post-run free disk is at least 2 GB.
- If the rule passes, remove temporary diagnostics, keep `-n 2 --dist
  loadfile`, and record the observed headroom and wall time in `CLAUDE.md`.
- If the rule fails, restore `-n 0`, record the measured failure mode, and
  leave a re-trial follow-up after `07-18-perf-heavy-fixture-trim` when disk or
  fixture pressure is the constraint.
- Preserve the existing heavy marker, coverage artifact, aggregate CI result,
  and pytest-exit-5 empty-partition contracts.

## Acceptance Criteria

- [ ] A full-matrix trial completes with 48 heavy tests and reports peak system
      memory plus before/after root-filesystem headroom.
- [ ] The final worker count follows the pre-committed 12 GB / 2 GB decision
      rule, with the exact observed values recorded in task and PR evidence.
- [ ] When adopted, heavy-lane wall clock improves against the 717-second
      post-parallelization baseline without weakening coverage or aggregates.
- [ ] The heavy and light partitions still sum to the full collected suite.
- [ ] Temporary diagnostics are removed from the final workflow unless they
      prove generally useful and receive their own documented contract.

## Measurement context

- Local heavy `-n 0`: 377.47s.
- Local heavy `-n 2 --dist loadfile`: 259.79s, 48 passed, 11.25 GB peak RSS
  on a 48 GB developer host.
- The public Linux runner provides 4 vCPU, 16 GB RAM, and 14 GB SSD. Local
  success cannot establish its memory or disk headroom.
- Source split: `07-18-perf-ci-worker-counts` owns the completed light trial,
  which retained `-n 2` after its remote result missed the adoption threshold;
  this task owns only the heavy experiment and decision.

## Trial preparation evidence (2026-07-20)

- The focused CI-contract and heavy-marker suite passed: 55 tests in 3.99s.
- The two-worker heavy rehearsal passed all 48 tests in 243.93s.
- Collection remains exact: 48 heavy + 1597 light = 1645 total.
- All five heavy-job shell blocks parse under `bash -n` after YAML decoding.
- Hosted memory and disk evidence remains the adoption gate; local success is
  not used to infer runner headroom.
