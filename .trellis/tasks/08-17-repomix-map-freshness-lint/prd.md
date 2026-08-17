# Guard docs/repomix-map.md freshness with a repo-owned lint

## Goal

`docs/repomix-map.md` goes stale silently and is caught only by the external,
machine-installed command pack — at the review gate, after the commit that
broke it. Move the catch into a repo-owned `tools/check_*.py` running in
pre-commit and CI, per CLAUDE.md's rule to prefer a mechanical lint whenever
the pattern is greppable.

## Context

The map went stale twice in two consecutive ships and blocked the merge gate
both times:

- **PR #382** — six new `scripts/` forwarders were added and never appeared in
  the map.
- **PR #381** — `task.py archive` moved
  `.trellis/tasks/08-15-debug-ui-csv-formula-neutralization/` into
  `archive/2026-08/`, leaving five map lines pointing at paths that no longer
  existed. `sd-check` failed, which fails `sd-review scope=pr` closed, which
  stops the ship chain. The fix each time was one `./scripts/update_repomix`
  run — cheap to do, expensive to discover, and discovered late.

The archive case is structural, not accidental: finish-work archives the task
*after* the map was last generated, so any completion-mode ship that
regenerated the map early is guaranteed to strand those entries.

## Measured baseline

Taken at `69a0752` (post-#381 merge).

- No repo-owned guard exists. `git grep -ln repomix -- tools/` returns only
  `check_ci_review_contract.py`, which checks CI cadence rather than map
  contents. `.pre-commit-config.yaml` matches `scripts/update_repomix` only as
  a shellcheck input.
- The sole existing guard is `checkGeneratedStructuralMapPaths()` in the
  machine-installed `sd-ai-command-pack-review-preflight.mjs`, and it covers
  exactly one of four quadrants:

  | drift direction | `.trellis/` | every other tree |
  | --- | --- | --- |
  | map lists a path that no longer exists | covered (external) | **not covered** |
  | tracked file absent from the map | **not covered** | **not covered** |

  The `.trellis/`-only narrowing is deliberate and documented in that helper:
  broader trees "can legitimately list files a clean clone does not carry."
  A repo-owned lint knows this repo and need not accept that limitation.
- Current drift, map entries vs `git ls-files`: 1201 map files, 1204 tracked,
  0 stale, 3 tracked-but-absent — `.trellis/.template-hashes.json`,
  `docs/repomix-map.md`, and `uv.lock`. All three are legitimate exclusions,
  which is the constraint below.

## Requirements

- A `tools/check_repomix_map_freshness.py` guard fails on map drift in both
  directions, across all trees, not only `.trellis/`.
- The three known-legitimate exclusions must not be reported. They are not
  arbitrary: the generator ignores its own output via `--ignore`, and the other
  two fall out of repomix's default ignore behavior. The exclusion set is
  **derived** from `scripts/update_repomix` and repomix's defaults, not
  hardcoded as a literal list beside them — a hardcoded copy is a second
  registry for the same fact and drifts exactly like the map does.
- The guard runs without the `repomix` binary. `scripts/update_repomix` exits
  `127` when repomix is absent, so a regenerate-and-diff design would either
  fail on contributor machines or be skipped into uselessness in CI. Compare
  the committed map's parsed entries against `git ls-files` instead.
- Follows the repo's lint contract: full module docstring, the
  `0` clean / `1` violation / `2` structural-error exit split, and an
  acceptance test file.
- Wired into `.pre-commit-config.yaml` and the CI quick lane so
  `tools/check_guard_ci_coverage.py --list` stays clean.

## Non-goals

- Regenerating the map automatically. A lint that rewrites a generated artifact
  hides the fact that the author's tree and the map disagree; the remedy stays
  one explicit `./scripts/update_repomix`.
- Replacing or duplicating the pack's `.trellis/` check. That guard is external
  and stays as-is; this one is the repo's own floor and will overlap it.

## Acceptance criteria

- [ ] A map listing a path that no longer exists fails the guard, in any tree —
      pinned by a fixture reproducing the #381 archive-move shape.
- [ ] A tracked file absent from the map fails the guard — pinned by a fixture
      reproducing the #382 new-`scripts/`-files shape.
- [ ] The live repo passes with zero findings, and the three legitimate
      exclusions are absent from the output rather than suppressed by a
      hardcoded literal list.
- [ ] A malformed or unparseable map exits `2`, distinctly from `1`.
- [ ] `tools/check_guard_ci_coverage.py --list` shows the new lint at
      `needs=QUICK+FULL has=QUICK+FULL`, and the "lints whose own tests never
      run in the QUICK lane" section still prints `none`.
- [ ] The CLAUDE.md Repository lints table gains a row for the guard.

## Notes

Bounded and mechanical, but the exclusion-derivation requirement is a real
design question — where the authoritative exclusion set is read from decides
whether this lint becomes the next thing that drifts. Worth a `design.md` pass
before implementation rather than PRD-only.
