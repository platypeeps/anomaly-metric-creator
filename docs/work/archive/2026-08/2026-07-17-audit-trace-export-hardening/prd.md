---
title: "Harden trace exports: CSV formulas, CORS star, bundle compat"
status: done
created: 2026-07-17
branch: fix/trace-export-hardening
---
# Harden trace exports: CSV formulas, CORS star, bundle compat

## Audit context

- **Source:** 2026-07-17 full repo audit @ b0df00b — report:
  `.trellis/audit/report-2026-07-17.md`; per-item evidence + fix sketches:
  `.trellis/audit/ledger.md`.
- **Ledger items:** A-018 (P2·S), A-019 (P3·S), A-070 (P3·S)

## Goal

Attacker-recorded command traces can carry spreadsheet formulas into the operator's
trace-bundle CSV export; the CORS wildcard exposes an unauthenticated bind to
cross-origin reads; archived trace bundles have no version-compat story.

## Scope (ledger items)

- A-018 — neutralize leading spreadsheet trigger characters (`=`, `+`, `-`, `@`, `TAB`, or `CR`) in every user-influenced cell write_trace_bundle_csv emits (raw_input, argv/parsed_flags JSON, resource identifiers, fingerprint, matched_rule_id, stdout/stderr previews, guessed_intent — not just the obvious free-text ones); universal writer-boundary neutralization is enumeration-proof.
- A-019 — refuse or warn on --cors-allow-origin '*' without --auth-token (or never emit `*` for rubric-bearing endpoints and the `/v1/debug/*` surfaces).
- A-070 — decide + document trace-bundle version policy: N-1 compat reader or matching-tool-version archival guidance.

## Acceptance criteria

- [x] A trace containing `=cmd|...` exports with an apostrophe prefix; neutralization covers every user-influenced CSV column (not just the obvious free-text ones), with tests across them.
- [x] CORS star + no-auth combination is refused or loudly warned.
- [x] Bundle version policy recorded in README + code.
- [x] Closing PR flips each covered ledger item to `status: fixed` in
      `.trellis/audit/ledger.md` (same-PR, per ledger rules).
