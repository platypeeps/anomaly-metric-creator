# Dedupe OTLP, topology, and SQLite test/store harnesses

## Audit context

- **Source:** 2026-07-17 full repo audit @ b0df00b — report:
  `.trellis/audit/report-2026-07-17.md`; per-item evidence + fix sketches:
  `.trellis/audit/ledger.md`.
- **Ledger items:** A-031 (P2·S), A-032 (P2·M), A-033 (P2·M), A-037 (P3·M)

## Goal

Near-verbatim duplication with live drift: 22 inline OTLP capture servers in
test_cli.py, the topology CSV harness copied four times (exclusion windows already
diverged), a ~55-line SQLite INSERT duplicated across two store methods, and lint
boilerplate re-defined in 15+ test files.

## Scope (ledger items)

- A-031 — extract _insert_trace_row(conn, trace, *, delete_fts_first) shared by _insert_sqlite and _replace_sqlite_traces.
- A-032 — conftest capture_otlp_server fixture modeled on test_otel_gauges._MockCollector; collapse the 22 inline classes; keep genuinely divergent handlers as variants.
- A-033 — move _column_values/_aligned_columns/_exclude_anomaly_rows to conftest; replace both hard-coded _EXCLUSION_WINDOWS lists with test_topology_llm's SCENARIOS-derived computation.
- A-037 — conftest run_tool() helper for the lint tests; decide shared-lib vs documented-standalone for the two contract checkers.

## Acceptance criteria

- [ ] All suites green; no golden-hash changes.
- [ ] grep shows one OTLP harness definition and one exclusion-window computation.
- [ ] Closing PR flips each covered ledger item to `status: fixed` in
      `.trellis/audit/ledger.md` (same-PR, per ledger rules).
