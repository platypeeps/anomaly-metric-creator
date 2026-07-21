# Build the missing test-guard lints and sync checks

## Audit context

- **Source:** 2026-07-17 full repo audit @ b0df00b — report:
  `.trellis/audit/report-2026-07-17.md`; per-item evidence + fix sketches:
  `.trellis/audit/ledger.md`.
- **Ledger items:** A-058 (P2·M), A-059 (P2·S), A-023 (P3·S), A-024 (P3·M), A-025 (P3·S)

## Goal

The repo's own policy says mechanical review patterns become lints; five documented
recurrence classes still rely on reviewer attention: GB-scale test reads, the
hand-maintained scenario catalog, the heavy-marker registries, unparsed debug-UI JS,
and a wall-clock pacing assertion that is vulnerable to loaded-runner jitter.

## Scope (ledger items)

- A-058 — `tools/check_test_resource_cost.py` flagging executable
  `read_bytes()`, `readlines()`, and `read_text().splitlines()` calls in `tests/`
  with a trailing `# resource-lint: allow` marker; rewrite unsafe large-file
  reads, explicitly exempt intentional small-artifact reads, and wire the guard
  to pre-commit plus the always-run CI changes job.
- A-059 — test parsing the README scenario-catalog table and comparing it
  bidirectionally with `amc.SCENARIOS` (slug/severity/days/components columns).
- A-023 — assert each _HEAVY_*_FIXTURES name resolves to a real fixture.
- A-024 — extract the debug-UI script body and node --check it (skip without node).
- A-025 — replace the gauge-stream wall-clock window with monkeypatched time.sleep duration assertions.

## Acceptance criteria

- [x] New lints follow the 0/1/2 exit-code contract with acceptance tests.
- [x] The resource lint scans file and directory inputs, ignores strings and
      comments, aggregates violations, honors only a trailing line exemption,
      and fails closed on missing/unreadable or syntactically invalid inputs.
- [x] A scenario added without a README row fails the sync test.
- [x] README catalog severity, `days_required`, and `components_touched` values
      stay bidirectionally equal to the registry.
- [x] Every declared heavy fixture name resolves through pytest's fixture
      manager, and the debug-UI JavaScript passes `node --check` when Node is
      available.
- [x] Gauge pacing asserts requested sleep durations rather than elapsed wall
      time while retaining real mock-collector round trips.
- [x] Pre-commit and the always-run CI changes job both invoke the resource lint,
      and the CI contract guard pins that wiring.
- [x] Closing PR flips each covered ledger item to `status: fixed` in
      `.trellis/audit/ledger.md` (same-PR, per ledger rules).
