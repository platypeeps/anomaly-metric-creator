# Suite runtime program — Integration Plan

> This parent is **not an implementation target**. Do not `task.py start`
> it. Execution happens in the children; this file records the sequencing
> and the final verification the parent owns.

## Execution Order

1. **Land the two P1s** (`07-18-perf-ci-lane-parallelization`,
   `07-18-perf-ci-worker-counts`). Independent of each other and of every
   test change; between them they carry the bulk of the win with no test
   edits. Either order.
2. **Land `07-18-perf-longform-writer-test-dedupe`.** Must precede the
   fixture trim — both edit `tests/test_combine.py` and
   `tests/test_gauges_file.py`.
3. **Take the `07-18-perf-heavy-fixture-trim` decision gate to the
   maintainer** before implementing it. Two of its three trims re-lock a
   golden hash. The `test_n3_1d_hashes_stable` item needs no re-lock and
   can proceed regardless.
4. **Re-run the `perf-ci-worker-counts` Part B trial** if it failed on
   resource headroom. The fixture trim frees both RAM and disk, so a
   previously-failed trial may now pass. This dependency runs backwards
   through the graph and is the easiest thing in this program to lose.
5. **Land the independent children in any order**, at any point:
   `07-18-perf-local-test-split`, `07-18-perf-local-gate-dedupe`,
   `07-18-fix-heavy-marker-and-fixture-docs`,
   `07-18-fix-ci-classifier-script-paths`. None share files with the CI
   work.
6. **Final verification** (below), then archive the tree.

## Validation Plan

Per-child validation lives in each child's `implement.md`. The parent's own
checks are the aggregate ones:

```bash
# partition integrity — must hold after every child
.venv/bin/pytest -m heavy --collect-only -q | tail -1
.venv/bin/pytest -m "not heavy" --collect-only -q | tail -1
.venv/bin/pytest --collect-only -q | tail -1     # equals the sum

# the program's headline number, measured the same way every time
/usr/bin/time -l .venv/bin/pytest -n 0 -m heavy -q -p no:cacheprovider
/usr/bin/time -l .venv/bin/pytest -n 2 --dist loadfile -m "not heavy" -q -p no:cacheprovider
```

CI side: read the `Run test suite` step duration (or, post-split, the longer
of the two lane jobs) off a full-matrix run — not the run's total wall
clock, which includes queueing and the other lanes.

**Exit criteria for the program:**

- test step (or longest lane) < 600s against the 1089s baseline;
- heavy + light collected counts equal the total;
- combined coverage at or above the pre-program figure, `--cov-fail-under`
  never lowered;
- no test deleted without a recorded replacement assertion;
- every golden-hash re-lock traceable to an approved decision-gate entry.

## Documentation And Spec Updates

Four children touch `CLAUDE.md`'s CI or testing prose. Because they land in
sequence, each must re-read what the previous one wrote rather than editing
against the state it saw at planning time. Before the final child merges,
sweep for stale numbers:

```bash
rg -n "7 ?GB|2-core|1089|723|364|-n 2|~1\.3 GB|~9 GB" CLAUDE.md docs/ .trellis/spec/ pyproject.toml tests/conftest.py
```

The 7 GB figure is proof this failure mode is real here — it survived
multiple CI edits because nobody grepped the *value*.

## Review Notes

- Each child PR should link back to this parent and state which step of the
  order it is, so a reviewer can see whether a prerequisite was skipped.
- Resist bundling children to "save a review cycle". The two P1s are
  reviewable as pure config; the test changes need scrutiny of what coverage
  moved. Mixing them buries the latter.
- The program's claims are performance claims. Every PR should carry
  measured before/after numbers taken by the protocol in `design.md`, with
  run IDs for CI figures — not projections presented as results.

## Follow-Ups

- Lane sharding, once fixture fan-in is low enough that per-shard
  regeneration is not a net loss (see `perf-ci-lane-parallelization`).
- Ratchet `--cov-fail-under` up toward the measured ~88% once
  `07-02-legacy-monolith-decomposition` extracts more of `legacy.py`. That
  is a separate epic's work but the ratchet is this program's neighbour and
  should not be forgotten while CI config is being edited.
- Revisit whether `quick_check` should publish coverage; today a
  quick-lane-only PR contributes none.
