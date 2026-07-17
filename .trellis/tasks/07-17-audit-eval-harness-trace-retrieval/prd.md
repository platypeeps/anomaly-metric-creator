# Fix the eval recipe's trace-evidence loss

## Audit context

- **Source:** 2026-07-17 full repo audit @ b0df00b — report:
  `.trellis/audit/report-2026-07-17.md`; per-item evidence + fix sketches:
  `.trellis/audit/ledger.md`.
- **Ledger items:** A-066 (P2·S)

## Goal

Under the documented --mcp-eval-mode recipe, every trace-read surface (including
/v1/debug/commands/export) is rubric-404'd and the in-memory ring dies with the
process — a harness following the README verbatim loses its agent-activity scoring
evidence. Related-but-distinct from open task 07-06-eval-mode-symptom-log-artifact.

## Scope (ledger items)

- Add --persist-command-db (or -log) to the recommended eval invocation in README.
- Document that on-disk persistence is the only trace-retrieval path in eval mode; note --debug-ring-size is irrelevant to harness retrieval there.
- Consider a serve-time warning when eval mode runs with no persistence configured.

## Acceptance criteria

- [ ] README eval section carries the persist flag + rationale.
- [ ] Optional: warning emitted for eval-mode-without-persistence.
- [ ] Closing PR flips each covered ledger item to `status: fixed` in
      `.trellis/audit/ledger.md` (same-PR, per ledger rules).
