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

That step runs two sequential pytest invocations
(`.github/workflows/ci.yml:397-398`):

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

## Goal

Reduce the full-matrix CI test step from 1089s toward 350-500s, and remove
the duplicated work that makes a bare local `pytest` slower than the CI
split, without weakening determinism guarantees or coverage.

## Task map

| Child | Priority | Expected saving |
|---|---|---|
| `07-18-perf-ci-lane-parallelization` | P1 | ~366s CI wall clock |
| `07-18-perf-ci-worker-counts` | P1 | ~131s light |
| `07-20-perf-ci-heavy-worker-trial` | P1 | ~224s heavy (pending CI trial) |
| `07-18-perf-longform-writer-test-dedupe` | P2 | ~74s local / ~140s CI |
| `07-18-perf-heavy-fixture-trim` | P2 | ~60s local / ~115s CI, gated on re-lock decision |
| `07-18-perf-local-test-split` | P2 | local only; removes 2-4x fixture rebuilds |
| `07-18-perf-local-gate-dedupe` | P2 | ~4s of a 6.3s deterministic gate, plus Prism/KB interrupts |
| `07-18-fix-heavy-marker-and-fixture-docs` | P3 | correctness, not speed |
| `07-18-fix-ci-classifier-script-paths` | P3 | avoids spurious 16.6-min full-matrix runs |

Ordering: the two P1 children are independent of each other and of
everything else; land them first for the bulk of the win. The fixture
children (`longform-writer-test-dedupe`, `heavy-fixture-trim`) touch
overlapping test files and should land sequentially, not concurrently.

## Cross-child acceptance criteria

- [ ] The full-matrix CI test step drops below 600s with no test deleted
      and no locked hash weakened, except where a child PRD explicitly
      records the trade and the maintainer approved it.
- [ ] The partition still covers the suite exactly: the `-m heavy` count
      plus the `-m "not heavy"` count equals the total collected count.
- [ ] The parallel xdist ordering path stays exercised at the PR gate —
      the property CLAUDE.md gives as the reason the light lane runs under
      real xdist rather than serially.
- [ ] Coverage remains aggregated across every partition and the
      `--cov-fail-under` ratchet is not lowered.
- [ ] `CLAUDE.md` no longer states the 7 GB runner premise.

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
