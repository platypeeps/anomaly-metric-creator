# Test serve_main composition incl. the eval-mode wire

## Audit context

- **Source:** 2026-07-17 full repo audit @ b0df00b — report:
  `.trellis/audit/report-2026-07-17.md`; per-item evidence + fix sketches:
  `.trellis/audit/ledger.md`.
- **Ledger items:** A-020 (P1·M, Verified)

## Goal

serve_main's body between argument validation and serve_forever — the only production
path that threads --mcp-eval-mode into build_state (server.py:1519) and maps serve
flags onto ServerSecurityConfig — is executed by zero tests; mis-threading the
default-False kwarg silently drops the ground-truth wall.

## Scope (ledger items)

- Wiring test: run serve_main with --mcp-eval-mode --no-generate --port 0 under a monkeypatched serve_forever (or capturing build_state wrapper); assert eval_mode=True and the security-config field mapping.
- Optional live smoke: bind port 0, GET /v1/anomalies → 404, clean shutdown.
- Refuter scope note: _generation_argv_without_otel is already covered via continuous-generation tests — do not duplicate.

## Acceptance criteria

- [x] A test fails if the eval_mode kwarg is dropped or mis-threaded.
- [x] Flag→ServerSecurityConfig mapping asserted field-by-field.
- [x] Closing PR flips each covered ledger item to `status: fixed` in
      `.trellis/audit/ledger.md` (same-PR, per ledger rules).
