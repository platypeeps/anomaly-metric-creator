# Local test split — Implementation Plan

## Execution Order

1. Branch from `main`. Measure the baseline before changing anything, so
   the PR carries evidence rather than a claim:
   ```bash
   /usr/bin/time -l .venv/bin/pytest -q -p no:cacheprovider          # bare default
   /usr/bin/time -l .venv/bin/pytest -n 0 -m heavy -q -p no:cacheprovider
   /usr/bin/time -l .venv/bin/pytest -n 4 --dist loadfile -m "not heavy" -q -p no:cacheprovider
   ```
2. Decide the delivery mechanism (script vs. docs-only) per `design.md`.
   If a script: place it under `scripts/`, and **in the same commit** add it
   to `is_review_tooling_path` in `scripts/classify-ci-changes.sh` plus a
   case in `tests/test_ci_change_classifier.py`. A new unclassified script
   triggers the 16-minute full matrix on every edit.
3. Rewrite `docs/DEVELOPMENT_CYCLE.md:59-64`: the split is the normal
   full-suite path, not a "high-risk changes only" extra. Use `-n 4` for the
   light lane, `-n 0` (or `-n 2` with a memory caveat) for heavy, and phrase
   worker counts as a practical ceiling rather than a fixed number.
4. Rewrite the `pyproject.toml:67-81` comment against measured numbers: the
   `min(consuming files, workers)` bound, the fixture fan-out table from
   `design.md`, and the `--dist loadfile` saturation point. Drop the `~5 GB`
   figure or replace it with a measured one — it derives from a size that
   `07-18-fix-heavy-marker-and-fixture-docs` is correcting.
5. Update `CLAUDE.md`'s parallel-execution section to match.
6. Re-run the three measurements from step 1 and put both sets in the PR.
7. Draft PR -> pre-PR checklist (doc sync is the load-bearing heading) ->
   ready -> merge.

## Validation Plan

```bash
# the split must beat the bare default on the same machine
/usr/bin/time -l .venv/bin/pytest -q -p no:cacheprovider
# vs
/usr/bin/time -l .venv/bin/pytest -n 0 -m heavy -q -p no:cacheprovider && \
/usr/bin/time -l .venv/bin/pytest -n 4 --dist loadfile -m "not heavy" -q -p no:cacheprovider

# a bare invocation must still work
.venv/bin/pytest -q

# if a script was added
bash scripts/classify-ci-changes.sh -- scripts/<new-script>   # lightweight_only=true
.venv/bin/pytest tests/test_ci_change_classifier.py -n 0

.venv/bin/pre-commit run --all-files
```

## Documentation And Spec Updates

- `docs/DEVELOPMENT_CYCLE.md` — the split as the normal path.
- `pyproject.toml` — the `addopts` comment block.
- `CLAUDE.md` — parallel-execution section.
- Grep for stale worker-count guidance rather than editing only the known
  sites:
  ```bash
  rg -n -- "-n 2|-n 4|-n 0|dist loadfile" docs/ CLAUDE.md pyproject.toml .trellis/spec/
  ```

## Review Notes

- Say up front that the split was **already documented** at
  `docs/DEVELOPMENT_CYCLE.md:59-64` and that this task changes its framing
  and worker counts. A reviewer who knows it exists will otherwise read the
  PR as redundant.
- Justify leaving `addopts` at `-n 4`: changing the default to `-n 0` would
  pessimize the common case (a narrow selection) to fix the uncommon one (a
  full-suite run). That reasoning belongs in the description, not only here.
- Include both measured timings. This is a performance task; a docs diff
  without numbers is unreviewable.
- If a script is added, call out its classifier registration explicitly —
  it is the kind of one-line omission that silently costs 16 CI minutes per
  future edit.

## Follow-Ups

- Once `07-18-perf-heavy-fixture-trim` lands, the heavy lane's memory
  profile changes and the recommended local heavy worker count may be worth
  revisiting.
- If a `scripts/` entry proves useful, consider whether the other repeated
  local sequences (full-check, KB regen) deserve the same treatment.
