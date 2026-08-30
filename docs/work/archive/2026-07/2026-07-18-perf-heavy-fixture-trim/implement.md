# Heavy fixture trim — Implementation Plan

## Execution Order

**Prerequisite:** `07-18-perf-longform-writer-test-dedupe` must be merged
first — both tasks edit `tests/test_schema_file.py` and neighbouring
modules. Rebase on it before starting.

1. Branch from `main`. **Item 1 first, alone, as its own commit.** Change
   `test_n3_1d_hashes_stable`
   (`tests/test_instances_per_component.py:492`) so both passes use the 60s
   default, matching `test_n3_7d_hashes_stable` (`:522-545`). No locked hash
   changes. Mutation-check it: make `generate_component` non-deterministic
   locally, confirm the test fails, restore.
2. **Item 4 investigation, timeboxed.** Confirm whether `synthetic_n3_run`
   can avoid writing the five unread component CSVs without changing the
   topology chain. Expected answer is no. Record the finding in this task's
   `prd.md` either way — a documented decline stops it being re-proposed.
   Do not engineer around it; it is 2.71s.
3. **Stop.** Take items 2 and 3 to the maintainer with the two questions in
   the PRD's decision gate. Do not implement a re-lock before it clears.
4. If item 2 is approved: change `seven_day_schema_run`
   (`tests/test_schema_file.py:64-77`) to `interval_seconds=60`, re-lock
   `SCHEMA_SEVEN_DAY_HASH` (`:44`). Own commit; message records old hash,
   new hash, and the changed parameter.
5. If item 3 is approved: give `seven_day_schema_run_n3` (`:535`) its own
   cheap `--emit schema` run, re-lock `SCHEMA_N3_SEVEN_DAY_HASH` (`:514`).
   Own commit, same message discipline. Then verify the partition:
   ```bash
   .venv/bin/pytest -m heavy --collect-only -q | tail -1
   ```
   If the N=3 schema test dropped out of `heavy`, confirm it is genuinely
   light (no GB fixture in its closure) rather than silently escaping.
6. Update every trimmed fixture's docstring to state what its consumers
   actually read. Coordinate with
   `07-18-fix-heavy-marker-and-fixture-docs` — if that task merged first,
   re-read its corrected size figures instead of reapplying stale ones.
7. Draft PR -> pre-PR checklist (test resource cost, test path determinism,
   doc sync) -> ready -> merge.

## Validation Plan

```bash
# the touched modules, serial
.venv/bin/pytest tests/test_schema_file.py tests/test_instances_per_component.py -n 0 -q

# re-lock stability: same args twice must produce the same hash
.venv/bin/pytest tests/test_schema_file.py -n 0 -q
.venv/bin/pytest tests/test_schema_file.py -n 0 -q

# heavy timing vs the 377.47s baseline
/usr/bin/time -l .venv/bin/pytest -n 0 -m heavy -q -p no:cacheprovider

# partition integrity
.venv/bin/pytest --collect-only -q | tail -1

.venv/bin/pytest            # full suite
.venv/bin/pre-commit run --all-files
```

After any approved re-lock, confirm by inspection that the 1-day and 7-day
schema documents still differ in `total_seconds` and `rows_per_component` —
that difference is what the 1d-vs-7d test pair exists to check, and it is
the property most at risk from an interval change.

## Documentation And Spec Updates

- Fixture docstrings in `tests/test_schema_file.py` and
  `tests/conftest.py` — what consumers read, and the corrected sizes.
- `CLAUDE.md` schema-document section, if the re-locks change what the
  locked hashes cover.
- This task's `prd.md`: record each decision-gate outcome, including any
  declined item and why.
- `.trellis/tasks/07-18-perf-suite-runtime/prd.md` task map — actual saving.

## Review Notes

- **Every re-lock is its own commit** with old hash, new hash, and the
  parameter that changed. A reviewer must be able to see exactly what
  coverage was traded without reconstructing it from a diff.
- Lead the PR description with the decision-gate outcome and who approved
  it. A golden-hash change without that context reads as a regression being
  papered over — which is precisely what the repo's determinism rules exist
  to prevent.
- Item 1 needs no approval and should be visibly separated from items 2-3 in
  the description, so the free win is not held up by the gated ones.
- State plainly that item 3 does **not** delete the 1.85 GB fixture — it
  decouples one consumer. Overstating it invites a reviewer to expect a
  saving that is not there.

## Follow-Ups

- Re-run `07-18-perf-ci-worker-counts` Part B (heavy `-n 2`) after this
  merges — the freed RAM and disk may turn a previously-failed trial green.
  The parent's `implement.md` carries this as a step; make sure it happens.
- If the decision gate shows appetite for coarser determinism, the larger
  question is whether `N3_SEVEN_DAY_HASHES` needs 1s resolution at all —
  that would retire the 1.85 GB fixture entirely. Deliberately out of scope
  here; open as its own task with its own gate.
