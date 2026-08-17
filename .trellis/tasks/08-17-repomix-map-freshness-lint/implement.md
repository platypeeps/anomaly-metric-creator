# Implementation plan — repomix map freshness lint

Order matters, though less than the first draft of this plan claimed.

**Correction, made after step 1 landed.** This plan originally asserted that the
map is "stale on this branch right now" because this task's four artifact files
are absent from it, and ordered the wiring last so the `always_run` hook would
not block intermediate commits. Running the finished guard against the *unmodified*
map returns **exit `0`**: absent-from-map is the deferred direction (design D1),
not the one this guard checks. Adding files alone therefore cannot fail it.

**Second correction, made after step 5 ran.** Adding files and *then*
regenerating the map does fail it, until those files are staged. Repomix
generates from the working tree, so a fresh map lists paths that are untracked
until `git add`; the guard resolves against the index (D3) and calls them stale.
Measured: nine such entries, dropping to `0` after `git add -A`.

This is correct behavior, not a defect, and it is the sharpest available
evidence for D3. At pre-commit time every file being committed is already
staged, so the hook sees the same index the commit will record. The window in
which the two disagree is exactly the window in which the author has not yet
decided what to commit.

The step order below is kept, for a reason that survives both corrections: step
5 must regenerate the map after the last step that creates tracked files, or the
map ships describing a tree that is already one commit out of date. That is
hygiene the guard cannot enforce — precisely because it is the deferred
direction — which makes ordering the only mechanism available.

## Step 1 — the guard

- [x] `tools/check_repomix_map_freshness.py` — new, per design D1-D5. Module
      docstring carries the full contract: what drifts and why, the tree-parse
      strategy (D2), the index-not-filesystem decision (D3), the `always_run`
      selection reason (D4), and the `0`/`1`/`2` exit split (D5). CLAUDE.md's
      lint table points reviewers at the script rather than a copy, so the
      docstring is the contract, not a summary of it.
- [x] Optional path argument defaults to the repo-root map, so tests can point
      the check at fixtures. Model the argument handling on
      `tools/check_csv_formula_trigger_lockstep.py`.

Validate: `.venv/bin/python tools/check_repomix_map_freshness.py` → exit `0`
against the live map.

## Step 2 — the tests

- [x] `tests/test_repomix_map_freshness_lint.py` — new. Cover, with fixtures
      under `tmp_path` passed as path arguments:
  - clean map → `0`
  - an entry naming a deleted path → `1`, diagnostic names the `file:line` and
    the path
  - **the PR #381 shape specifically**: an entry under
    `.trellis/tasks/<slug>/` whose task has moved to `archive/` → `1`
  - a stale entry **outside** `.trellis/` → `1`, the case the external check
    cannot see and the reason this guard is not redundant
  - a directory entry whose subtree is entirely gone → `1`
  - no `# Directory Structure` section → `2`
  - odd indentation and a skipped indent level → `2`
  - a `..` component in an entry → `2`, never a stat outside the repo
  - a missing/unreadable map path → `2`
  - many stale entries → the enumerated list is capped and the suppressed count
    is stated (D5)
  - the live repo map → `0` (regression guard on the real artifact, not only
    fixtures)

Validate: `.venv/bin/pytest tests/test_repomix_map_freshness_lint.py -v`

## Step 3 — docs

- [x] `CLAUDE.md` — add a row to the Repository lints table. That table is the
      reviewer-facing inventory; a lint missing from it is the exact drift the
      table exists to prevent.
- [x] `docs/DEVELOPMENT_CYCLE.md` — the archive-move interaction belongs in the
      release/ship cadence doc: finish-work archives the task *after* the map
      was last generated, so a completion-mode ship regenerates the map after
      `task.py archive`, not before. State the ordering, since it is what makes
      the failure recur by construction rather than by accident.

Validate: `git grep -n "check_repomix_map_freshness"` must name the tool, its
test, the hook, the CI job, and the CLAUDE.md row. Enumerate the hits rather
than asserting a count — the expected number shifts with how the docs phrase
things, and a count assertion invites tuning the number instead of checking the
sites.

## Step 4 — follow-up task

- [x] File the deferred repository → map direction (design D1, the PR #382
      class) as its own task. It is blocked on deciding where repomix's default
      ignore set comes from. Do **not** fold it into this PR.

Filed here, before the map regeneration, because filing a task creates tracked
files. Filing it after step 5 regenerates the map would strand its four artifacts
in exactly the way this task exists to prevent.

## Step 5 — regenerate the map

- [x] `./scripts/update_repomix` — the last step that creates tracked files is
      step 4, so the map is regenerated once, here, after all of them. This
      keeps the shipped map an accurate description of the shipped tree, which
      is the deferred direction no guard covers yet.

Regenerating leaves the guard failing until the new files are staged: repomix
reads the working tree, the guard reads the index. Measured nine stale entries
here, `0` after `git add -A`. See the second correction at the top; this is the
intended D3 behavior, not a step that went wrong.

Validate: `.venv/bin/python tools/check_repomix_map_freshness.py` → `0`.

## Step 6 — pre-commit and CI wiring

Last by convention rather than necessity. Per the corrections at the top, the
`always_run` hook does not block the intermediate commits of steps 1-4: those
add files without regenerating the map, which is the deferred direction. It
would have blocked a commit taken between step 5 and staging, but no step takes
one. Landing the wiring after the map regeneration reads more clearly in the
diff regardless.

- [x] `.pre-commit-config.yaml` — add hook `repomix-map-freshness` with
      `always_run: true` and `pass_filenames: false` per D4, modeled on the
      `branch-name` block (but at the default pre-commit stage, not pre-push —
      see D4a for why the archive-commit collision is accepted rather than
      dodged). The comment must say **why** it is not `files:`-selected: a
      `files:`-selected hook would run only on commits that cannot be stale.
      This is the single most likely thing for a later reader to "fix" into
      uselessness.
- [x] `.github/workflows/ci.yml` — invoke the guard explicitly with a `run:`
      step, the way `check_ruff_lockstep.py` is invoked. It is `always_run`, so
      it selects no files and lands in the **unlaned** group; it must be
      invoked by a job rather than relying on lane selection. Add
      `tests/test_repomix_map_freshness_lint.py` to the quick-lane test list.

Validate, in this order:

1. `.venv/bin/python tools/check_guard_ci_coverage.py --list` must show the
   guard under `unlaned` with an invoking CI job named, and the
   "lints whose own tests never run in the QUICK lane" section must still print
   `none`.
2. `.venv/bin/python tools/check_ci_review_contract.py` → exit `0`.
3. **Hook selection behaviour, not hook config** (PRD criterion): run
   `.venv/bin/pre-commit run repomix-map-freshness --files README.md` — a file
   the guard has nothing to do with — and confirm the hook still executes
   rather than reporting `(no files to check) Skipped`. Reading `always_run:
   true` in the config is not this check; a hook can be config-correct and
   still skip.

## Step 7 — full gates

- [x] `.venv/bin/pytest` — `2083 passed, 2 skipped in 323.57s`. Both skips are
      the pre-existing `AMC_RUN_REAL_CLIENT_SMOKE` opt-ins, not new.
- [x] `.venv/bin/pre-commit run --all-files` — 19 hooks, all `Passed`, including
      `guard generated repomix map freshness`.
- [x] `~/.agents/bin/sd-ai-command-pack-full-check.sh` — 12 Prism findings, all
      verify-prompts rather than defects; the three HIGH ones were checked
      individually (task dirs isolated; map diff is 14 additions and zero entry
      deletions, the two `-` lines being the fence widening from 4 to 5
      backticks; the one `.env`-named path is the pre-existing tracked
      `.gito/sd-ai-command-pack.env`, holding `MAX_CONCURRENT_TASKS=4` under an
      explicit no-secrets header).

## Review gates

- D3 (index, not filesystem) is the one decision a reviewer should re-derive
  rather than accept from the diff: a filesystem probe passes locally and fails
  in CI for exactly the case the guard exists to catch. The test covering a
  stale entry whose file is absent from the index pins it.
- D4's `always_run` is the second. Flag it in the PR body so it is not read as
  a missing `files:` filter.
- The narrowed acceptance criteria (one direction, not two) must be called out
  explicitly in the PR body rather than left for a reader to notice.

## Rollback points

Steps 1-2 stand alone: the guard plus its tests are useful even if the CI
wiring in step 6 is contentious, since `pre-commit run --all-files` still
invokes it. Step 5 must never be reverted independently of steps 1-2 — a
regenerated map with no guard is the state this task exists to leave behind.
