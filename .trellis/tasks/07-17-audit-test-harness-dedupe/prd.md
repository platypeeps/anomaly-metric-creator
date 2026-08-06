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

- A-031 — extract _insert_trace_row(conn, trace, payload, *, delete_fts_first) shared by _insert_sqlite and _replace_sqlite_traces. (Signature corrected 2026-08-06: `payload` is an explicit parameter, not computed inside the helper — see design.md.)
- A-032 — conftest capture_otlp_server fixture modeled on test_otel_gauges._MockCollector; collapse the 22 inline classes; keep genuinely divergent handlers as variants.
- A-033 — move _column_values/_aligned_columns/_exclude_anomaly_rows to conftest; replace both hard-coded _EXCLUSION_WINDOWS lists with test_topology_llm's SCENARIOS-derived computation.
- A-037 — conftest run_tool() helper for the lint tests; decide shared-lib vs documented-standalone for the two contract checkers.

## Task map (added 2026-08-06)

This parent is an epic; each child ships its own PR. `design.md` grouped the
work as two PRs (prod, then all three test items). The test-side PR was split
once more at the sequence points `implement.md` already defined, because a
single PR touching `test_cli.py`'s 22 capture servers, four topology files,
and 15+ lint test files is not a reviewable diff.

Ordering: child 1 is fully independent (production only). Children 2 and 3
both add to `tests/conftest.py`, so they are **not** disjoint and should not
run concurrently — take them in listed order and rebase the second onto the
first to avoid a conftest conflict.

1. `08-06-trace-row-insert-dedupe` — A-031 (production, behavior-identical).
   **Merged** 2026-08-06 as PR #345; archived under
   `.trellis/tasks/archive/2026-08/`. A-031 reads `status: fixed`.
2. `08-06-conftest-helper-consolidation` — A-033 + A-037 (conftest helpers).
   Next actionable child.
3. `08-06-otlp-capture-fixture` — A-032 (OTLP capture fixture, largest diff).
   Carries `blocked: true` / `blockedOn: 08-06-conftest-helper-consolidation`
   in its `task.json` so the ordering above is machine-readable rather than
   implied by position; child 2's acceptance criteria clear those markers when
   its PR merges.

Both remaining children were converted from stub PRDs to concrete
requirements and acceptance criteria on 2026-08-06, against measured evidence
at main `29ee1bf`.

Each child flips only its own ledger items. This parent closes when all three
are merged and A-031/A-032/A-033/A-037 all read `status: fixed`.

## Acceptance criteria

- [ ] All suites green; no golden-hash changes.
- [ ] One OTLP capture harness and one exclusion-window computation remain.
      Both commands must be runnable as written and are stated with their
      expected output:
      - `grep -rn 'def capture_otlp_server' tests/` matches only
        `tests/conftest.py`.
      - `grep -rn '_EXCLUSION_WINDOWS = \[' tests/` returns no hits — every
        remaining window set is catalog-derived.
      (`-rn`, not `-c` over a glob: a multi-file `grep -c` prints a per-file
      count line for every file, so it can never "return 0". The children's
      PRDs carry the same corrected forms.)
- [ ] Closing PR flips each covered ledger item to `status: fixed` in
      `.trellis/audit/ledger.md` (in the same PR as the fix — this epic's
      convention; the ledger itself states no such rule).
