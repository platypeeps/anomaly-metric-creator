# Worker counts for the 4-vCPU runner — Implementation Plan

## Execution Order

1. Branch from `main`. Land **Part A alone first** — change `ci.yml:398`
   from `-n 2` to `-n 4` and correct the `CLAUDE.md` runner premise (Part
   C). This is the evidenced, low-risk half and should not wait on the
   trial.
2. Open that PR, label `full-ci`, confirm the light lane drops >= 100s and
   all 1555 tests pass. Merge.
3. **Then** open a second, separate PR for the Part B trial. Add a
   temporary diagnostic step before the heavy invocation:
   ```yaml
   - name: Runner capacity (trial diagnostics)
     run: |
       nproc
       free -m
       df -h /
   ```
   and repeat `df -h /` immediately after the heavy step.
4. Set `-n 2 --dist loadfile` on the heavy invocation. Push, label
   `full-ci`, and read the diagnostics against the pre-committed decision
   rule: **adopt only if peak memory <= 12 GB and post-run free disk
   >= 2 GB.**
5. If the rule passes: remove the diagnostic steps, update `CLAUDE.md`'s
   description of the split, merge. If it fails: close the PR, and record
   the measured numbers plus the failure mode in this task's PRD so the
   next attempt starts from data rather than repeating the experiment.
6. Either way, record the outcome in `07-18-perf-suite-runtime`'s task map
   so the parent's projected total reflects reality.

## Validation Plan

```bash
# both lanes at the proposed settings, before pushing
.venv/bin/pytest -n 4 --dist loadfile -m "not heavy" -q
.venv/bin/pytest -n 2 --dist loadfile -m heavy -q

# partition still covers the suite exactly
.venv/bin/pytest -m heavy --collect-only -q | tail -1
.venv/bin/pytest -m "not heavy" --collect-only -q | tail -1
.venv/bin/pytest --collect-only -q | tail -1   # must equal the sum

.venv/bin/pre-commit run --all-files
```

CI validation is the point of Part B and cannot be done locally. Capture
the run URL and the diagnostic output in the PR description.

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

- Split into two PRs deliberately. Reviewers should not have to weigh an
  evidenced one-character change against an unproven one in the same diff.
- State the decision rule for Part B **before** showing the trial result,
  so the adopt/reject call reads as pre-committed rather than fitted to
  whatever the run produced.
- The counter-intuitive measurement (`-n 4` peaks *lower* than `-n 2`) will
  draw a question; pre-empt it with the fixture-lifetime explanation from
  `design.md`.
- Do not describe Part B as "safe because it passed locally" — the local
  box has 48 GB and 237 GB free disk. That framing is exactly the error
  this task exists to correct.

## Follow-Ups

- If the Part B trial fails on disk rather than memory, that is a direct
  input to `07-18-perf-heavy-fixture-trim` — the 2.8 GB `gauges.csv` is the
  largest single artifact and the obvious first target.
- Re-run the Part B trial after any fixture-trim child lands.
