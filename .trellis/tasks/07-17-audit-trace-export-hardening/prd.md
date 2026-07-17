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

- A-018 — neutralize leading = + - @ tab CR in free-text cells (raw_input, stdout/stderr previews, guessed_intent) in write_trace_bundle_csv.
- A-019 — refuse or warn on --cors-allow-origin '*' without --auth-token (or never emit * for rubric//v1/debug surfaces).
- A-070 — decide + document trace-bundle version policy: N-1 compat reader or matching-tool-version archival guidance.

## Acceptance criteria

- [ ] A trace containing `=cmd|...` exports with an apostrophe prefix; test covers all four free-text columns.
- [ ] CORS star + no-auth combination is refused or loudly warned.
- [ ] Bundle version policy recorded in README + code.
- [ ] Closing PR flips each covered ledger item to `status: fixed` in
      `.trellis/audit/ledger.md` (same-PR, per ledger rules).
