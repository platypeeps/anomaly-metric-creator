# Worker counts for the 4-vCPU runner — Implementation Plan

## Execution Order

1. Branch from `main`, trial the light lane at `-n 4`, and correct the
   `CLAUDE.md` runner premise.
2. Open that PR, label `full-ci`, and compare the remote light step with the
   364-second baseline. Adopt only at >=100 seconds saved; otherwise restore
   `-n 2`. Run `29796112539` measured 352 seconds, so restore `-n 2`.
3. Confirm the retained setting passes the focused and full gates, then merge.
4. Continue with `07-20-perf-ci-heavy-worker-trial`, which owns the separate
   diagnostic PR and its pre-committed adopt/reject rule.

## Validation Plan

```bash
# candidate rehearsal retained as historical trial evidence
.venv/bin/pytest -n 4 --dist loadfile -m "not heavy" -q

# final light-lane setting after the remote trial missed its threshold
.venv/bin/pytest -n 2 --dist loadfile -m "not heavy" -q

# partition still covers the suite exactly
.venv/bin/pytest -m heavy --collect-only -q | tail -1
.venv/bin/pytest -m "not heavy" --collect-only -q | tail -1
.venv/bin/pytest --collect-only -q | tail -1   # must equal the sum

.venv/bin/pre-commit run --all-files
```

CI validation measures the candidate against the recorded baseline and then
confirms the retained setting. The heavy-lane diagnostic output belongs to the
follow-up.

## Documentation And Spec Updates

- `CLAUDE.md`: replace the 7 GB / 2-core premise with 4 vCPU / 16 GB /
  14 GB SSD, note that public-repo standard minutes are free so wall clock
  is the target, and update the described worker counts.
- Grep for the old figure before declaring the doc sweep done — the
  pre-PR checklist's doc-drift rule is about the *value*, not the symbol:
  ```bash
  rg -n "7 ?GB|2-core|two-core" CLAUDE.md docs/ .trellis/spec/ pyproject.toml
  ```

## Review Notes

- Keep the heavy-lane experiment in its follow-up task. Reviewers should not
  have to weigh an evidenced change against an unproven one in the same diff.
- Keep both sides of the result: `-n 4` was materially faster locally, but the
  remote light step improved by only 12 seconds and therefore was not adopted.

## Follow-Ups

- Run `07-20-perf-ci-heavy-worker-trial` after this task lands. If it fails on
  disk, feed that evidence into `07-18-perf-heavy-fixture-trim`.
