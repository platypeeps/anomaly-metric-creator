# Approval-duplicate gate: wire or retire — Design (SD Work Designs, 2026-07-17)

## Overview

`tools/check_approval_duplicate.py` (689 lines) + ~1,000-line test file
enforce a real convention born from a real incident (PR #86's five
duplicate APPROVED comments), but nothing invokes the gate: CLAUDE.md
documents a manual `&&` chain, and no hook/workflow/skill runs it.

## Proposal

**Recommendation: wire, minimally (Option A-lite).** Rationale: the gate
works, has full test coverage, addresses a documented recurrence, and the
wiring cost is one small wrapper — deleting 1,700 working lines to save
zero maintenance (it's stdlib-only and stable) is the worse trade. The
task-start gate: confirm the maintainer still wants the convention; if
not, flip to Option B below (both paths are fully specified here so the
implementing session just executes the chosen one).

### Option A-lite (recommended)

- Add `tools/pr_comment.sh`: reads a body file + PR number, chains
  `check_role_name_leaks.py -` → `check_approval_duplicate.py --pr N` →
  `gh pr comment N --body-file …`; exit codes pass through (role-name and
  approval lints keep their 0/1/2 contract).
- Update CLAUDE.md's two manual-chain snippets to point at the wrapper as
  the canonical path (keep the raw chains documented as what the wrapper
  does).
- Record the convention in the canonical Trellis spec
  (`.trellis/spec/amc/backend/documentation-review.md` or the spec index's
  PR-workflow section) so task-loaded sessions see it — the audit's core
  finding was "convention exists only in prose nobody loads".
- Do **not** edit vendored `.agents/skills/` files (pack refresh would
  clobber; upstream pack change would need its own consented PR — if the
  maintainer wants pack-level wiring, write a paste-ready handoff note in
  this task instead).

### Option B (fallback if the maintainer declines)

- Delete `tools/check_approval_duplicate.py` +
  `tests/test_approval_duplicate_lint.py`; grep-sweep references
  (CLAUDE.md section, CHANGELOG mention stays as history); add a
  CHANGELOG line recording the retirement + rationale; CLAUDE.md section
  replaced by one sentence ("retired 2026-07-…, convention: edit prior
  approval comments in place").

## Boundaries And Non-Goals

- No new gate features (native PR-review endpoint support stays out of
  scope, as the script's own v1 note says).
- No pack-file edits, no upstream PRs.

## Affected Files

A-lite: new `tools/pr_comment.sh`, CLAUDE.md, one spec file, ledger flip.
B: two deletions, CLAUDE.md, CHANGELOG, ledger flip.

## Risks And Edge Cases

- Wrapper must preserve the stdin-pipe contract (`--pr` mode refuses TTY
  stdin — the wrapper always pipes the file).
- Keep the wrapper POSIX-sh simple; it runs on operator machines, not CI.

## Validation

- A-lite: shell test or manual transcript in the PR (clean body posts;
  duplicate-approval body blocks with exit 1; role-name-dirty body blocks).
  `tests/test_role_name_leaks_lint.py` and the approval-gate tests stay
  green.
- B: full-suite green after deletion; grep sweep for the script name
  returns only CHANGELOG history.
