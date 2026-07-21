# Parallel CI test lanes — Implementation Plan

## Execution Order

1. Branch from `main`. **First** read `tools/check_ci_review_contract.py`
   end-to-end and list every assertion that names a CI job, step, or lane
   string. That set decides whether the split can keep the
   `test (py3.14)` matrix naming or must rename — do not start editing the
   workflow before this list exists.
2. Add `[tool.coverage.run] relative_files = true` to `pyproject.toml`.
   Verify locally that a two-invocation combine works from separate data
   files:
   ```bash
   .venv/bin/pytest -n 0 -m heavy --cov=src/anomaly_metric_creator --cov-report=
   mv .coverage .coverage.heavy
   .venv/bin/pytest -n 2 --dist loadfile -m "not heavy" --cov=src/anomaly_metric_creator --cov-report=
   mv .coverage .coverage.light
   .venv/bin/coverage combine && .venv/bin/coverage report --fail-under=85
   ```
   This reproduces the CI topology locally and is the cheapest way to catch
   a combine misconfiguration before burning CI runs.
3. Split `test_matrix` into `test_heavy` + `test_light`. Identical setup
   preamble, identical `needs`/`if`, one pytest invocation each, each
   renaming `.coverage` to a visible lane-specific filename and uploading it
   via `actions/upload-artifact` with a distinct name. Keep the existing
   console-script smoke, ruff, and mypy gates in `test_light` only so they
   still gate once without delaying `test_heavy` from starting its partition.
   Pin the same upload action SHA already used by the workflow.
4. Add the `coverage_combine` job: `needs: [test_heavy, test_light]`,
   checkout, install uv, sync the locked Python 3.14 development environment,
   download both artifacts, rename to `.coverage.heavy` / `.coverage.light`,
   run `coverage combine`, generate `coverage xml`, then run the gating
   `coverage report --fail-under=85`. Upload `coverage.xml` with
   `if: ${{ !cancelled() }}` so it survives a failed threshold step.
5. Update the `test` aggregate: add `coverage_combine` to `needs`, replace
   `MATRIX_RESULT` with the combine job's result in the full-matrix branch.
   **Do not touch its `if: ${{ !cancelled() }}` guard.**
6. Update `tools/check_ci_review_contract.py` and
   `tests/test_ci_review_contract.py` fixtures for the new shape, from the
   list built in step 1.
7. Update `CLAUDE.md`'s continuous-integration section: the job layout, the
   coverage-combine step, and the fact that the `--cov-fail-under` gate now
   lives on `coverage report`.
8. Draft PR -> pre-PR checklist (CI/workflow hygiene and doc-sync headings
   are the load-bearing ones here) -> ready -> merge.

## Validation Plan

```bash
# local combine rehearsal (step 2)
.venv/bin/coverage combine && .venv/bin/coverage report --fail-under=85

# contract guard + its real-repo test
.venv/bin/python tools/check_ci_review_contract.py
.venv/bin/pytest tests/test_ci_review_contract.py -n 0

.venv/bin/pre-commit run --all-files
```

CI validation needs a real run — label the PR `full-ci` to force the full
matrix, then confirm on the run page:

- `test_heavy` and `test_light` start within one scheduling window;
- `coverage_combine` reports a percentage matching the pre-split figure and
  its XML artifact remains present if a deliberately higher threshold fails;
- application-lane wall clock is >= 300s below the 1089s baseline;
- `coverage.xml` is attached to the run.

## Documentation And Spec Updates

- `CLAUDE.md` CI section — job layout and coverage flow.
- `docs/DEVELOPMENT_CYCLE.md` if it describes the CI lane shape.
- No Trellis spec change expected; if `.trellis/spec/amc/backend/` documents
  the CI contract, update it in the same diff.

## Review Notes

- The PR description must state plainly that this changes **where** the
  tests run and nothing about **what** runs — reviewers will look for a
  hidden scope change in a workflow diff this size.
- Name the measured before/after wall clock with run IDs. The whole task is
  a performance claim; it should be evidenced, not asserted.
- Call out that `--cov-fail-under` moved from pytest to `coverage report`
  and that the threshold is unchanged at 85 — that line will otherwise read
  as a weakened gate.
- Flag the deliberate choice to gate `coverage_combine` on plain `needs:`
  (not `!cancelled()`), and why: it prevents a failed lane from producing a
  misleading under-reported coverage failure.

## Follow-Ups

- Sharding within a lane, once `07-18-perf-heavy-fixture-trim` reduces
  fixture fan-in enough that per-shard regeneration is not a net loss.
- If the split proves out, consider whether the `quick_check` lane should
  also publish coverage — currently it does not, so a quick-lane-only PR
  contributes no coverage data.
