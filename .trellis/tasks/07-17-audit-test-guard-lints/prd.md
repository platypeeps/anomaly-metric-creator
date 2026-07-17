# Build the missing test-guard lints and sync checks

## Audit context

- **Source:** 2026-07-17 full repo audit @ b0df00b — report:
  `.trellis/audit/report-2026-07-17.md`; per-item evidence + fix sketches:
  `.trellis/audit/ledger.md`.
- **Ledger items:** A-058 (P2·M), A-059 (P2·S), A-023 (P3·S), A-024 (P3·M), A-025 (P3·S)

## Goal

The repo's own policy says mechanical review patterns become lints; four documented
recurrence classes still rely on reviewer attention: GB-scale test reads, the
hand-maintained scenario catalog, the heavy-marker registries, and untested debug-UI JS.

## Scope (ledger items)

- A-058 — tools/check_test_resource_cost.py flagging read_bytes/readlines/read_text().splitlines() in tests/ with a `# resource-lint: allow` marker; triage existing sites; wire to pre-commit + CI.
- A-059 — test parsing the README scenario-catalog table, parametrized over amc.SCENARIOS (slug/severity/days/components columns).
- A-023 — assert each _HEAVY_*_FIXTURES name resolves to a real fixture.
- A-024 — extract the debug-UI script body and node --check it (skip without node).
- A-025 — replace the gauge-stream wall-clock window with monkeypatched time.sleep duration assertions.

## Acceptance criteria

- [ ] New lints follow the 0/1/2 exit-code contract with acceptance tests.
- [ ] A scenario added without a README row fails the sync test.
- [ ] Closing PR flips each covered ledger item to `status: fixed` in
      `.trellis/audit/ledger.md` (same-PR, per ledger rules).
