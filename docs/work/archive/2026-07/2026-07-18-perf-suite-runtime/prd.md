---
title: Cut CI and local test-suite runtime
status: done
created: 2026-07-18
---
# Cut CI and local test-suite runtime

## Measurement context

- **Source:** 2026-07-18 runtime review. CI baseline is run `29631419630`
  (push to `main`, head `aefd08f`).
- **Local baseline:** 14-core / 48 GB darwin, CPython 3.14.6, `.venv`.
  The local-to-CI scaling factor measured **~1.9x** on both partitions
  independently (heavy 377s local / 723s CI; light 196s local / 364s CI),
  so local numbers below project to CI by that factor.

### Where the time goes

The full-matrix `test (py3.14)` job is **1110s, of which the single
`Run test suite` step is 1089s (98%)**. Checkout, `uv sync`, the package
smoke, both ruff steps, and both mypy steps total **21s**. CI runtime on
this repo is test runtime; nothing else is worth optimizing.

That step runs two sequential pytest invocations:

| Partition | Tests | Mode | CI time |
|---|---|---|---|
| `-m heavy` | 48 | `-n 0` (serial) | **723.07s (66%)** |
| `-m "not heavy"` | 1555 | `-n 2 --dist loadfile` | 364.01s (33%) |

### The stale premise

`CLAUDE.md` sizes the whole heavy/light split against "the 7 GB standard
runner". **This repository is public**
(`gh api repos/platypeeps/anomaly-metric-creator --jq .private` returns
`false`), and GitHub's standard `ubuntu-latest` runner for public
repositories is **4 vCPU / 16 GB RAM / 14 GB SSD** — double the private
tier the 7 GB figure describes. Consequences:

- `-n 2` uses half the available cores on both lanes.
- The OOM history that motivated serial-heavy occurred under a memory
  ceiling that no longer applies.
- Standard-runner minutes are free on public repos, so **wall clock is the
  only cost** and extra parallel jobs are free.

### Measured alternatives

| Configuration | Wall time | Peak RSS |
|---|---|---|
| heavy `-n 0` (current) | 377.47s | not captured |
| heavy `-n 2 --dist loadfile` | **259.79s (-31%)** | **11.25 GB** |
| light `-n 2` (current) | 195.66s | 9.70 GB |
| light `-n 4` | **125.34s (-36%)** | **8.29 GB** |
| light `-n 10` | 121.43s | 8.75 GB |

All 48 heavy and all 1555 light tests pass in every configuration above.
Two findings carry into the children: `-n 4` on the light lane is faster
*and* peaks lower than `-n 2`, and there is almost no gain past `-n 4`
because `--dist loadfile` granularity caps the fan-out.

The hosted-runner trial did not reproduce the projected saving: full-matrix
run `29796112539` measured the four-worker light step at 352s, only 12s below
the 364s baseline. Because that missed the child's >=100s adoption threshold,
the CI light lane retains `-n 2`; the local measurements remain useful only as
workstation evidence.

The heavy trial did transfer: run `29798826800` completed all 48 tests in
500.62s at `-n 2 --dist loadfile`, 216.38s (30.2%) below the 717s baseline,
while peaking at 5.09 GiB system used memory and retaining 76.9 GiB free disk.
Both pre-committed capacity thresholds passed, so two heavy workers are
adopted.

### Final verification (2026-07-21)

Exact-head CI run `29831312539` at `45a5f8c` completed the heavy test step in
361s and the light test step in 378s. Because the lanes run concurrently, the
full-suite test critical path is 378s: 711s (65.3%) below the original 1089s
sequential step and comfortably below the 600s acceptance threshold.

The final partition contains 44 heavy and 1653 light tests, exactly matching
all 1697 collected tests. Both CI lanes retain real xdist execution with
`-n 2 --dist loadfile`; the exact-head run and the command-contract guard
passed. Combined coverage remained 87%, the same figure recorded before the
program closed, and the unchanged `--cov-fail-under=85` gate passed.

The collected suite grew from the 1603-test baseline to 1697 tests; no test was
deleted to obtain the speedup. The two approved golden-hash re-locks are each
recorded in `07-18-perf-heavy-fixture-trim` and isolated in commits `8f94f35`
and `23b7a56`. The heavy-worker trial passed its pre-committed thresholds and
was adopted before fixture trimming, so its backward-rerun condition did not
apply.

## Goal

Reduce the full-matrix CI test step from 1089s toward 350-500s, and remove
the duplicated work that makes a bare local `pytest` slower than the CI
split, without weakening determinism guarantees or coverage.

## Task map

| Child | Priority | Expected saving |
|---|---|---|
| `07-18-perf-ci-lane-parallelization` | P1 | ~366s CI wall clock |
| `07-18-perf-ci-worker-counts` | P1 | 12s observed; `-n 4` rejected by threshold |
| `07-20-perf-ci-heavy-worker-trial` | P1 | 216s heavy observed; adopted |
| `07-18-perf-longform-writer-test-dedupe` | P2 | ~74s local / ~140s CI |
| `07-18-perf-heavy-fixture-trim` | P2 | 122.68s local observed (32.5%); 361s hosted heavy step |
| `07-18-perf-local-test-split` | P2 | local only; removes 2-4x fixture rebuilds |
| `07-18-perf-local-gate-dedupe` | P2 | ~4s of a 6.3s deterministic gate, plus Prism/KB interrupts |
| `07-18-fix-heavy-marker-and-fixture-docs` | P3 | correctness, not speed |
| `07-18-fix-ci-classifier-script-paths` | P3 | avoids spurious 16.6-min full-matrix runs |

Ordering: the two P1 children are independent of each other and of
everything else; land them first for the bulk of the win. The fixture
children (`longform-writer-test-dedupe`, `heavy-fixture-trim`) touch
overlapping test files and should land sequentially, not concurrently.

## Cross-child acceptance criteria

- [x] The full-matrix CI test step drops below 600s with no test deleted
      and no locked hash weakened, except where a child PRD explicitly
      records the trade and the maintainer approved it.
- [x] The partition still covers the suite exactly: the `-m heavy` count
      plus the `-m "not heavy"` count equals the total collected count.
- [x] The parallel xdist ordering path stays exercised at the PR gate —
      the property CLAUDE.md gives as the reason the light lane runs under
      real xdist rather than serially.
- [x] Coverage remains aggregated across every partition and the
      `--cov-fail-under` ratchet is not lowered.
- [x] `CLAUDE.md` no longer states the 7 GB runner premise.

## Constraints

- Do not lower `--cov-fail-under=85`; the repo rule is to ratchet it up.
- Golden SHA-256 hashes are the determinism contract. A child may propose
  re-locking one, but only with the trade recorded in its own PRD and
  approved before implementation.
- `scripts/sd-ai-command-pack-full-check.sh`,
  `scripts/sd-ai-command-pack-review-preflight.mjs`, and
  `scripts/sd-ai-command-pack-update-spec-kb.py` are pack-managed
  (sd-ai-command-pack 0.15.6, per `.sd-ai-command-pack/provenance.json`).
  Local edits drift from provenance and are clobbered on pack upgrade.
