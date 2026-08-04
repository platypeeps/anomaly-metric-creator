# Wire or retire the approval-duplicate gate

## Audit context

- **Source:** 2026-07-17 full repo audit @ b0df00b — report:
  `.trellis/audit/report-2026-07-17.md`; per-item evidence + fix sketches:
  `.trellis/audit/ledger.md`.
- **Ledger items:** A-034 (P2·S)

## Goal

tools/check_approval_duplicate.py (689 lines) plus its 1,000-line test file are
exercised only by their own tests — no hook, workflow, agent instruction, or spec
invokes the gate, so the convention it enforces has no enforcement path. Decision
task: pick one posture and implement it.

## Scope (ledger items)

- Option A — wire: invoke it from the comment-posting path (pack review skills / a gh wrapper) and record the convention in the canonical .trellis spec.
- Option B — retire: delete script + tests, record the convention's demise in CLAUDE.md/CHANGELOG.

## Decision (2026-08-04)

**Chosen: Option A-lite (wire), per design.md's recommendation.** Rationale is
repository-evidence-backed and follows established convention, so the wire/retire
posture is inferable without a maintainer prompt:

- The gate works and is fully covered (`tests/test_approval_duplicate_lint.py`),
  addresses a documented recurrence (PR #86's five duplicate approvals), and is
  stdlib-only/stable — retiring the ~690-line gate script (its test suite is
  a separate ~1,000 lines) would remove stable code and save ~zero maintenance.
- Every sibling comment/branch lint in the repo is *wired* (`role-name-leaks`
  and `role-name-commit-message` hooks, `branch-name` pre-push, `ruff-lockstep`
  in CI). Leaving only this gate unwired is the anomaly; wiring it restores the
  convention's consistency.
- Wiring (a new `tools/pr_comment.sh` wrapper + docs/spec records) is additive
  and reversible; retiring is a destructive deletion. Additive is the safer
  default under the standing authority.

Executes design.md's Option A-lite exactly: `tools/pr_comment.sh` chaining
role-name → approval-duplicate → `gh pr comment`, the two CLAUDE.md chain
snippets pointed at the wrapper, and the convention recorded in
`.trellis/spec/amc/backend/documentation-review.md`. No vendored `.agents/skills/`
edits; no pack/upstream PR.

## Acceptance criteria

- [x] Exactly one of wire/retire implemented; no orphaned references remain.
      (Wired — Option A-lite — via `tools/pr_comment.sh`; the raw `&&` chains
      remain documented as what the wrapper runs, so no orphaned references.)
- [x] Closing PR flips each covered ledger item to `status: fixed` in
      `.trellis/audit/ledger.md` (same-PR, per ledger rules). (A-034 flipped.)
