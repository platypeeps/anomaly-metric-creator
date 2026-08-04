# Fix stale security, reviewer, and reference docs

## Audit context

- **Source:** 2026-07-17 full repo audit @ b0df00b — report:
  `.trellis/audit/report-2026-07-17.md`; per-item evidence + fix sketches:
  `.trellis/audit/ledger.md`.
- **Ledger items:** A-026 (P2·S), A-027 (P2·S), A-028 (P3·S), A-029 (P3·S), A-030 (P3·S), A-046 (P3·S), A-064 (P3·S), A-069 (P3·S)

## Goal

Doc-vs-code drift (the repo's #1 review pattern) in load-bearing places: SECURITY.md
misstates the shipped redaction posture, Copilot instructions cite five removed
flags, and several reference surfaces lag the current CI/dependency reality.

## Scope (ledger items)

- A-026 — SECURITY.md: describe the shipped dual redaction posture; drop the completed-task pointer.
- A-027 — Copilot instructions: replace removed flags (--topology-mode/--validate-output/--combine-only/--emit-selection/independent) with the canonical surface; add contract anchors.
- A-028 — pyproject pin comments: state the mypy gate + 85% coverage ratchet; current task pointer.
- A-029 — CLAUDE.md aggregate naming: `test` feeds the required `CI Result` context.
- A-030 — complete the README dev-extra list.
- A-046 — raise dependency floors to the oldest py3.14-exercised combination (deliberate manifest change).
- A-064 — make `uv sync --extra dev --locked` the primary dev-setup instruction.
- A-069 — document MEZMO_OTEL_STREAM_AUTH_SCHEME in the README OTEL table.

## Acceptance criteria

- [x] Grep sweeps for each stale literal come back empty.
- [x] check_copilot_instruction_contract anchors added where noted.
- [x] Closing PR flips each covered ledger item to `status: fixed` in
      `.trellis/audit/ledger.md` (same-PR, per ledger rules).
