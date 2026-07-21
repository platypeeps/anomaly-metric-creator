# Suite runtime program — Integration Design (SD Work Designs, 2026-07-18)

## Overview

This is a coordination task; every deliverable lives in a child. What the
parent owns, and what no child can own alone, is three things: a **shared
measurement protocol** so the children's numbers are comparable, a
**landing order** that respects the interactions between them, and an
**aggregate verification** that the claimed savings actually compose rather
than double-count.

## Proposal

### Shared measurement protocol

Every child that claims a saving reports it the same way, or the parent's
projected total is meaningless:

- **CI numbers** come from the `Run test suite` step duration of a
  full-matrix run, read off the job's step timings — not from the run's
  total wall clock, which includes queueing and the other lanes.
- **Local numbers** come from the partition in isolation with
  `-p no:cacheprovider`:
  ```bash
  /usr/bin/time -l .venv/bin/pytest -n <N> [--dist loadfile] -m <selector> -q -p no:cacheprovider
  ```
  Report wall time *and* `maximum resident set size`. Peak RSS is
  load-bearing for the worker-count decisions and is cheap to capture.
- **The local-to-CI factor is ~1.9x**, established on both partitions
  independently (heavy 377.47s/723.07s = 1.92; light 195.66s/364.01s =
  1.86). Children may project with it but must label projections as such.
- **Baselines are the 2026-07-18 figures** in the parent PRD, tied to CI run
  `29631419630`. A child that re-baselines must say why.

### Landing order

```
    [perf-ci-lane-parallelization]   [perf-ci-worker-counts]      (independent, either order)
                     \                        /
                      \                      /
                    [perf-longform-writer-test-dedupe]           (must precede the trim)
                                   |
                       [perf-heavy-fixture-trim]                 (decision gate)
                                   |
                    (re-run the heavy -n 2 trial if it failed)

    [perf-local-test-split]  [perf-local-gate-dedupe]             (independent of all CI work)
    [fix-heavy-marker-and-fixture-docs]  [fix-ci-classifier-script-paths]
```

The constraints that produce this order:

- The two P1s touch only `ci.yml` and `pyproject.toml` and are independent
  of each other and of every test change. They are the bulk of the win and
  should land first.
- `perf-longform-writer-test-dedupe` and `perf-heavy-fixture-trim` both edit
  `tests/test_combine.py`, `tests/test_gauges_file.py`, and
  `tests/test_schema_file.py`. Sequential, not concurrent.
- `perf-heavy-fixture-trim` reduces both peak RSS and peak disk in the heavy
  lane, which is the input to `perf-ci-worker-counts` Part B. If that trial
  failed on resource headroom, it becomes worth repeating after the trim —
  that dependency runs backwards through the graph and is easy to forget.
- The local children and the two P3 fixes share no files with the CI work.

### Aggregate verification

Savings do **not** simply add, and the parent must not report them as if
they do:

- Lane parallelization converts `heavy + light` into `max(heavy, light)`.
  Once the light lane is the shorter side, any further heavy-lane saving
  shows up 1:1 — but any further *light*-lane saving shows up as zero until
  it becomes the longer side. After both P1s land, heavy (~499s) dominates
  light (~233s), so the fixture children's savings are real and the
  worker-count light saving is already absorbed.
- The final number to verify is one measurement: the `Run test suite`
  duration (or, post-split, the longer of the two lanes) on a full-matrix
  run after all children land. Target < 600s against the 1089s baseline.

## Boundaries And Non-Goals

- The parent is not an implementation target. It has no direct code change
  and should not be `task.py start`ed.
- No child may delete a test or lower `--cov-fail-under` to hit a number.
- Golden-hash re-locks are confined to `perf-heavy-fixture-trim` and gated
  on maintainer approval recorded there.

## Affected Files

None directly. The parent tracks `.trellis/tasks/07-18-*/` artifacts and
`CLAUDE.md`'s CI and testing sections as the surfaces the children must
leave consistent with each other.

## Risks And Edge Cases

- **Children re-measuring differently** and producing numbers that cannot be
  summed — the protocol above exists for this.
- **The backwards dependency** (fixture trim re-enabling a failed
  worker-count trial) getting lost once the worker-count task is closed.
  `perf-ci-worker-counts`'s implement.md carries a follow-up for it; the
  parent should check it before declaring the program done.
- **Doc drift across children.** Four children touch `CLAUDE.md`'s CI or
  testing prose. Landing them in sequence means each must re-read what the
  previous one wrote rather than reapplying a stale premise — the 7 GB
  figure is exactly this failure mode, already realized once.
- **Declaring victory on projections.** Every number above except the two
  baselines is a projection until a post-merge full-matrix run confirms it.

## Validation

- After each child merges, one full-matrix run on `main` (the push backstop
  already provides this) recorded against the parent's task map.
- Final: a full-matrix run showing the test step < 600s, the heavy/light
  collected counts still summing to the total, and combined coverage at or
  above the pre-program figure.
