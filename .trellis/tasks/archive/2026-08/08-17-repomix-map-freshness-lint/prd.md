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
- Current drift, map entries vs `git ls-files`, measured at merge commit
  `69a0752` before this task's own files existed: 1201 map files, 1204 tracked,
  0 stale, 3 tracked-but-absent — `.trellis/.template-hashes.json`,
  `docs/repomix-map.md`, and `uv.lock`. All three are legitimate exclusions,
  which is the constraint below.

  The counts move as soon as this task's own artifacts are committed (they add
  tracked files the map does not yet list), so treat the **three exclusions**
  as the durable finding and the raw totals as a snapshot pinned to that
  commit. Re-measure rather than citing the totals from here.

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

> **Narrowed during design.** The requirement above for *both* drift directions
> did not survive the investigation in `design.md`: the repository → map
> direction needs repomix's built-in default ignore set, which lives in the tool
> and in no file in this repository. Implementing it would mean either depending
> on the `repomix` binary — which this PRD rules out — or hand-mirroring an
> upstream list, a second registry that drifts exactly like the map does. The
> map → repository direction needs no exclusion set at all and is shipped here;
> the other is deferred to its own task with the open decision stated. Trading a
> known gap for a silent one would be the worse outcome.

- [x] A map listing a path that no longer exists fails the guard, in any tree —
      pinned by a fixture reproducing the #381 archive-move shape, and by a
      second fixture outside `.trellis/` (the case the external pack check
      cannot see).

      Evidence: `test_archive_move_shape_exits_one` (a synthetic task directory
      moved under `archive/`, with the map entry left at its pre-archive
      location) and `test_stale_entry_outside_trellis_exits_one` (a synthetic
      shell script under a scripts directory), both asserting exit `1`. Both
      fixture trees are built inside `tmp_path` and name no real repository
      path. `test_untracked_file_on_disk_is_still_stale` pins the
      harder half: a stale entry whose file *does* exist on disk but is not in
      the index still fails, which is what stops a local pass from becoming a CI
      failure.
- [x] The live repo passes with zero findings.

      Evidence: `test_live_repository_map_is_current` runs the guard against the
      real `docs/repomix-map.md`, and the direct run prints
      `repomix map is current: all 1497 listed path(s) in repomix-map.md resolve
      to tracked files or directories` at exit `0`.
- [x] A malformed or unparseable map exits `2`, distinctly from `1`.

      Evidence: six tests, one per structural failure — missing
      `# Directory Structure` section, indentation not a multiple of two, a
      skipped indent level, a `..` component, an empty listing, and an
      unreadable path — each asserting exit `2` and a distinguishing message.
      Two argument-shape tests cover the same exit.
- [x] The guard runs on commits that do **not** touch the map, since that is
      when staleness is introduced — verified through the hook's actual
      selection behavior, not by reading its config.

      Evidence: `pre-commit run repomix-map-freshness --files README.md` reports
      `Passed`, i.e. it executed. The contrast run
      `pre-commit run csv-formula-trigger-lockstep --files README.md` reports
      `(no files to check)Skipped`, which is what a `files:`-selected hook does
      on the same input and what this criterion exists to exclude.
- [x] The deferred repository → map direction is filed as its own task rather
      than left as an unchecked criterion here.

      Evidence: `.trellis/tasks/08-17-repomix-map-missing-entries-lint/`, whose
      PRD states the open decision (where repomix's built-in default ignore set
      is derived from) and lists three candidate resolutions, none chosen.
- [x] `tools/check_guard_ci_coverage.py --list` shows the new lint in the
      **unlaned** group with an invoking CI job named, and the "lints whose own
      tests never run in the QUICK lane" section still prints `none`.

      > **Corrected during design.** This criterion originally demanded
      > `needs=QUICK+FULL has=QUICK+FULL`, which is the *laned* classification.
      > Design D4 chose `always_run` deliberately — a `files:`-selected hook
      > would run only on the commits that cannot be stale — and an `always_run`
      > hook selects no files, so the coverage tool classifies it unlaned by
      > construction. The original wording would have been satisfiable only by
      > reversing the central design decision.

      Evidence: the `--list` output places `check_repomix_map_freshness.py` under
      `unlaned` as `hook repomix-map-freshness selects no files (stages:
      pre-commit); ci jobs: changes`, and the QUICK-lane section prints `none`.
      The tool itself exits `0`, as does `check_ci_review_contract.py`.
- [x] The CLAUDE.md Repository lints table gains a row for the guard.

      Evidence: `CLAUDE.md:252`. The row names the `always_run` selection and
      the deliberately out-of-scope reverse direction, so a reader of the
      inventory alone does not mistake either for an oversight.

## Notes

Bounded and mechanical, but the exclusion-derivation requirement is a real
design question — where the authoritative exclusion set is read from decides
whether this lint becomes the next thing that drifts. Worth a `design.md` pass
before implementation rather than PRD-only.
