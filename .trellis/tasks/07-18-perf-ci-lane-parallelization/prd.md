# Run heavy and light test partitions as parallel CI jobs

## Goal

The two pytest partitions run as sequential steps inside one job, so the
CI test step costs `heavy + light` (723s + 364s = 1089s). Running them as
separate jobs makes it `max(heavy, light)`. On a public repo the extra
runner is free, so this is pure wall-clock recovery with no change to what
the tests do.

## Measurement context

- Baseline run `29631419630`: `Run test suite` = 1089s of an 1110s job.
- Both invocations live in one step: `.github/workflows/ci.yml:393-398`.
- Expected saving at current worker counts: **~366s** (1089s -> 723s).
  Compounds with `07-18-perf-ci-worker-counts`: with that child's settings
  the split lands near `max(499, 233) = 499s`.

## Requirements

- Split `test_matrix`'s `Run test suite` step into two jobs that run
  concurrently. Both keep `needs: changes` and the same
  `full_ci_requested` gating, so lane selection is unchanged.
- Preserve the `heavy` / `not heavy` partition exactly. This task changes
  *where* the partitions run, not *what* they select.
- Coverage currently aggregates via `--cov-append` across two steps in one
  working directory. Split jobs cannot share that file, so:
  - each job runs coverage with `--cov` and `--cov-report=`, renames the
    hidden `.coverage` file to a visible lane-specific filename, then uploads
    that raw data file as an artifact;
  - a combine job downloads both, runs `coverage combine`, then
    `coverage xml` before `coverage report --fail-under=85`, so the XML exists
    even when the threshold gate fails;
  - the combine job publishes `coverage.xml` (what `ci.yml:403-409` does
    today) and keeps `if: ${{ !cancelled() }}` so the report survives a
    tripped gate.
- The aggregate `test` job (`ci.yml:493`) must require both new jobs. Keep
  its `if: ${{ !cancelled() }}` guard — `tools/check_ci_review_contract.py`
  pins that form, and `always()` reintroduces the auto-merge red flash
  documented at `ci.yml:496-506`.
- The required branch-protection context stays the aggregate `CI Result`;
  do not rename it or introduce a new required context.
- Both pytest jobs and the combine job need checkout, uv, and a locked
  development sync. Duplicating ~20s of setup is the accepted cost of the
  split. The existing console-script, ruff, and mypy gates run once in the
  light lane rather than being duplicated.

## Acceptance criteria

- [ ] On a full-matrix PR run, the heavy and light jobs start within one
      scheduling window of each other rather than sequentially.
- [ ] Application-lane wall clock drops by >= 300s versus the
      `29631419630` baseline on a comparable run.
- [ ] Combined coverage equals the pre-split figure within normal jitter,
      and `--cov-fail-under=85` still gates the merge.
- [ ] `coverage.xml` is still published as a workflow artifact, including
      on a run where the coverage gate fails.
- [x] `tools/check_ci_review_contract.py` passes. If it asserts the old
      single-job shape, it is updated in the same PR to pin the new shape,
      including the `!cancelled()` guard.
- [ ] Cancelling a run (e.g. arming auto-merge mid-run) still yields a
      `cancelled` aggregate, not `failure`.
- [x] `CLAUDE.md`'s continuous-integration section describes the new job
      layout, including the coverage-combine step.

## Non-goals

- Changing worker counts — owned by `07-18-perf-ci-worker-counts`.
- Sharding either partition further. That becomes worthwhile only after
  the fixture-trim children land, since session fixtures rebuild per job
  and today's fixture fan-in would make sharding a net loss.
