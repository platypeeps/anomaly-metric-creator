# Parallel CI test lanes — Design (SD Work Designs, 2026-07-18)

## Overview

`test_matrix` runs both pytest partitions as one step
(`.github/workflows/ci.yml:393-398`), so the application lane costs
`heavy + light`. Splitting them into two jobs makes it `max(heavy, light)`.
The only non-trivial part is coverage: `--cov-append` currently aggregates
the two invocations through a shared `.coverage` file in one working
directory, which two jobs cannot share.

## Proposal

**Job topology.** Replace `test_matrix` with three jobs:

- `test_heavy` — `uv run pytest -n 0 -m heavy --cov=src/anomaly_metric_creator --cov-report=`
- `test_light` — `uv run pytest -n 2 --dist loadfile -m "not heavy" --cov=src/anomaly_metric_creator --cov-report=`
- `coverage_combine` — `needs: [test_heavy, test_light]`; downloads both
  coverage artifacts, combines, reports, and gates.

`test_heavy` and `test_light` both keep `needs: changes` and the identical
`if:` expression (`app_required == 'true' && full_ci_requested == 'true'`),
so lane selection is untouched. Both keep the full setup preamble
(checkout, `astral-sh/setup-uv` with `enable-cache`, `uv sync --locked`).
The ~20s duplicated setup is the accepted cost; it is 2% of the saving.

Keep the matrix `strategy` on both so the job names stay
`test (py3.14)`-shaped, or flatten to fixed names and update the contract
guard — decide in step 1 of `implement.md` by reading what
`tools/check_ci_review_contract.py` actually asserts.

**Coverage combine.** `pyproject.toml` has no `[tool.coverage]` section
today; all coverage behavior comes from CLI flags. Add:

```toml
[tool.coverage.run]
relative_files = true
```

Without it, combine matches data files by absolute path. Both jobs happen to
check out to the same workspace path on GitHub runners, so it would work by
luck; `relative_files` makes it correct by construction and keeps a local
`coverage combine` working from a different checkout.

Each lane uploads its raw `.coverage` as a distinct artifact
(`coverage-data-heavy`, `coverage-data-light`). `coverage_combine`
downloads both, renames them to `.coverage.heavy` / `.coverage.light`
(`coverage combine` discovers `.coverage.*` in the working directory), then:

```
uv run coverage combine
uv run coverage report --fail-under=85
uv run coverage xml
```

The `--cov-fail-under=85` gate moves off the pytest invocation and onto
`coverage report` in the combine job. That is the same threshold on the
same combined data — not a weakening.

**Aggregate wiring.** The `test` job (`ci.yml:493`) currently reads a single
`MATRIX_RESULT`. It gains `coverage_combine` in `needs:` and checks that
result for the full-matrix branch. Checking `coverage_combine` alone is
sufficient and correct: it `needs:` both lanes, so it cannot succeed unless
both did. Keep `if: ${{ !cancelled() }}` verbatim — `always()` reintroduces
the auto-merge red flash documented at `ci.yml:496-506` and pinned by
`tools/check_ci_review_contract.py`.

## Boundaries And Non-Goals

- No worker-count changes (`07-18-perf-ci-worker-counts` owns those). This
  task must be reviewable as "same commands, different jobs".
- No change to the `heavy` / `not heavy` selection, the marker, or any test.
- No sharding within a lane. Session fixtures rebuild per job, and today's
  fixture fan-in (`seven_day_run` serves 24 tests across 4 files) would make
  a shard split regenerate GB fixtures. Revisit only after
  `07-18-perf-heavy-fixture-trim`.
- The required branch-protection context stays `CI Result`.

## Affected Files

`.github/workflows/ci.yml` (job split, artifacts, combine job, aggregate
`needs`), `pyproject.toml` (`[tool.coverage.run] relative_files`),
`tools/check_ci_review_contract.py` + `tests/test_ci_review_contract.py`
(if the guard pins the single-job shape), `CLAUDE.md` (CI section).

## Risks And Edge Cases

- **Contract guard drift.** `tools/check_ci_review_contract.py` asserts CI
  lane structure and is itself covered by
  `tests/test_ci_review_contract.py:232` `test_real_repo_contract_is_clean`,
  which runs the guard against the real repo. Any job rename breaks both
  together; update guard, fixtures, and workflow in one commit.
- **Empty-collection fail-fast must survive.** The `-m heavy` step runs
  first today so a broken marker collects zero tests and pytest exits 5
  rather than spilling GB fixtures into the parallel lane. In separate jobs
  the two no longer have an order, so `test_heavy` must still fail on an
  empty collection — pytest's exit 5 does this natively; confirm it is not
  masked by a `|| true` or a `continue-on-error`.
- **Coverage artifact missing on a failed lane.** If `test_light` fails, its
  `.coverage` may not upload and `coverage_combine` would combine one file
  and under-report — potentially *failing* the 85 gate for the wrong reason.
  Gate the combine job on both lanes succeeding (plain `needs:` without
  `!cancelled()`), so a lane failure fails the aggregate through the lane,
  not through a misleading coverage number.
- **`coverage combine` consumes its inputs**, so `coverage xml` must run
  after `combine` in the same job, and `coverage.xml` uploads from there.
- Two concurrent jobs both restore the uv cache; this is a read path and
  authenticates with the runtime token, unaffected by `permissions:`.

## Validation

- Branch PR with `full-ci` label (or auto-merge armed) to force the full
  matrix; confirm both lanes start concurrently and the aggregate is green.
- Compare the combined coverage percentage against the `29631419630`
  baseline; it must match within jitter, not drop.
- Force a coverage failure locally (`coverage report --fail-under=100`) to
  confirm `coverage.xml` still uploads under `if: ${{ !cancelled() }}`.
- Push to an auto-merge-armed PR to confirm a superseded run still reports
  `cancelled`, not `failure`.
