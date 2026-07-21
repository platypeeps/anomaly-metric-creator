# Worker counts for the 4-vCPU runner — Implementation Plan

## Execution Order

1. Branch from `main`. Change the light lane from `-n 2` to `-n 4` and
   correct the `CLAUDE.md` runner premise. This is evidenced and low risk.
2. Open that PR, label `full-ci`, confirm the light lane drops >= 100s and
   all 1597 currently collected light tests pass or skip as expected. Merge.
3. Continue with `07-20-perf-ci-heavy-worker-trial`, which owns the separate
   diagnostic PR and its pre-committed adopt/reject rule.

## Validation Plan

```bash
# light lane at the proposed setting, before pushing
.venv/bin/pytest -n 4 --dist loadfile -m "not heavy" -q

# partition still covers the suite exactly
.venv/bin/pytest -m heavy --collect-only -q | tail -1
.venv/bin/pytest -m "not heavy" --collect-only -q | tail -1
.venv/bin/pytest --collect-only -q | tail -1   # must equal the sum

.venv/bin/pre-commit run --all-files
```

CI validation confirms the light-lane wall-clock improvement against the
recorded baseline. The heavy-lane diagnostic output belongs to the follow-up.

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
- The counter-intuitive measurement (`-n 4` peaks *lower* than `-n 2`) will
  draw a question; pre-empt it with the fixture-lifetime explanation from
  `design.md`.

## Follow-Ups

- Run `07-20-perf-ci-heavy-worker-trial` after this task lands. If it fails on
  disk, feed that evidence into `07-18-perf-heavy-fixture-trim`.
