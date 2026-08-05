# Make serve error plane observable by default

## Audit context

- **Source:** 2026-07-17 full repo audit @ b0df00b — report:
  `.trellis/audit/report-2026-07-17.md`; per-item evidence + fix sketches:
  `.trellis/audit/ledger.md`.
- **Ledger items:** A-071 (P2·S, Verified), A-072 (P2·S), A-073 (P2·S), A-074 (P2·S), A-075 (P2·M), A-076 (P2·S), A-077 (P3·M)

## Goal

In the default posture, unhandled-500 detail is irrecoverable (no sink at all),
background-thread failures are visible only on the eval-hidden /v1/state, mutating
requests can drop connections with no record, /readyz always says ready, DoS-bound
refusals are uncounted, and no sink ever gets a traceback.

## Scope (ledger items)

- A-071 — stderr fallback for the error-record arm when request_logger is None; consider nudging --structured-log alongside the hardening flags.
- A-072 — WARNING + traceback tail to stderr/structured log from the continuous-generation and OTEL failure arms.
- A-073 — except-Exception → 500 boundary on _handle_mutating_method (Status-shaped for API paths).
- A-074 — /readyz reflects artifact presence + generation-thread health; 503 names the failing dimension.
- A-075 — refusal counters (worker-cap 503 / SSE 503 / 429) in state.summary() + first-trip log line.
- A-076 — traceback.format_exc() into structured error records and MCP trace stderr; client bodies unchanged.
- A-077 — per-request id in structured records, threaded into trace recording as a join key.

## Acceptance criteria

- [ ] A forced 500 with default flags leaves its detail in at least one operator-visible sink.
- [ ] kubectl PATCH against a raising handler gets a 500, not a connection reset.
- [ ] readyz returns 503 under --no-generate with an empty dir.
- [ ] Closing PR flips each covered ledger item to `status: fixed` in
      `.trellis/audit/ledger.md` (same-PR, per ledger rules).
- [ ] A-075 — refusal counters (worker-cap 503 / SSE 503 / 429) surface in
      `state.summary()` with a first-trip log line. _(PR B — follow-up.)_
- [ ] A-077 — per-request id lands in structured records and is threaded into
      trace recording as a join key. _(PR B — follow-up.)_
