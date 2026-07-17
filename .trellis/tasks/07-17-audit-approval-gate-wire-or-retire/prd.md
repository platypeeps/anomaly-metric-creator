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

## Acceptance criteria

- [ ] Exactly one of wire/retire implemented; no orphaned references remain.
- [ ] Closing PR flips each covered ledger item to `status: fixed` in
      `.trellis/audit/ledger.md` (same-PR, per ledger rules).
