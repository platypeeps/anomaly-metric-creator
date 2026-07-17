# OTLP/topology/SQLite harness dedupe — Design (SD Work Designs, 2026-07-17)

## Overview

Four duplication clusters with live drift. One is production code (the
~55-line SQLite INSERT duplicated across two store methods,
server_traces.py:442 vs :691); three are test harnesses (22 inline OTLP
capture servers in test_cli.py; the topology CSV helpers copied 4× with
already-diverged exclusion-window lists; ~6-line `_run` boilerplate in
15+ lint test files).

## Proposal — two PRs (prod vs tests)

### PR 1 — A-031 (production, behavior-identical)

Extract `_insert_trace_row(conn, trace, *, delete_fts_first: bool)` used
by both `_insert_sqlite` and `_replace_sqlite_traces`. Byte-identical SQL
strings move verbatim; the only parameterized difference is the
FTS-delete-first branch the import path needs. Store tests + trace-bundle
round-trip tests are the oracle.

### PR 2 — test harness dedupe (A-032, A-033, A-037)

- **A-032:** promote `test_otel_gauges._MockCollector`'s design into a
  `conftest.py` `capture_otlp_server` fixture (context-managed HTTP
  capture server: records bodies/headers, configurable response
  status/headers per test). Collapse the 22 inline `_Handler` scaffolds
  in test_cli.py:362-1627 onto it; genuinely divergent handlers (error
  injection, header echo, redaction probes) become fixture parameters or
  small local subclasses — do NOT force-fit; the acceptance is one
  *base* harness definition, not zero variants.
- **A-033:** move `_column_values` / `_aligned_columns` /
  `_exclude_anomaly_rows` to `tests/conftest.py`; replace both
  hand-coded `_EXCLUSION_WINDOWS` lists with the SCENARIOS-derived
  computation `test_topology_llm` already uses (the drift between the
  hand lists is the finding — derivation fixes the class). Verify the
  derived windows are a superset of each hand list before deleting it;
  any hand-window NOT derivable flags a real question, stop and check
  rather than silently widening.
- **A-037:** `conftest.run_tool(script, *args, stdin=None)` helper for
  the 15+ lint tests (subprocess invocation + CompletedProcess return).
  The two contract checkers' shared `_read`/`_require_contains`: keep
  standalone (documented decision — a shared lib couples two
  independently-copied tools; the repo's lint scripts are deliberately
  stdlib-only single files). Record that choice here so the item closes
  as "decided", not skipped.

## Boundaries And Non-Goals

- Zero behavior/assertion changes — pure structure. No golden-hash
  exposure (test-only + SQL-verbatim).
- No new fixture *scopes* that change instantiation cost (the OTLP
  fixture is function-scoped like the inline classes it replaces).

## Affected Files

`src/anomaly_metric_creator/server_traces.py` (PR 1);
`tests/conftest.py`, `tests/test_cli.py`, `tests/test_topology_*.py`,
the 15+ lint test files (PR 2); `.trellis/audit/ledger.md` flips.

## Risks And Edge Cases

- test_cli.py's 22 sites differ subtly (status codes, retry behavior,
  header echoes) — collapse mechanically one at a time with the suite
  green after each batch; the xdist `--dist loadfile` model keeps
  per-file fixtures worker-local, so moving helpers to conftest must not
  introduce cross-file mutable state (keep the fixture stateless
  between tests).
- A-033's window derivation must exclude exactly what the hand lists
  excluded for the *same assertions* — run the affected topology tests
  before/after and diff pass/fail sets, not just green-ness.

## Validation

- PR 1: `pytest tests/test_server.py tests/test_trace_bundle.py -n 0`
  (store + import/export round-trips).
- PR 2: full suite; `rg -c '_Handler' tests/test_cli.py` shows the
  collapse; one exclusion-window computation
  (`rg '_EXCLUSION_WINDOWS' tests/` → conftest only).
