# Trace-export hardening — Design (SD Work Designs, 2026-07-17)

## Overview

Three export-surface items: CSV formula injection in `trace-bundle
export-csv` (free-text trace fields flow unneutralized into cells,
trace_bundle.py:208-234 — an attacker-shaped command like `=cmd|...`
recorded as a trace executes in the operator's spreadsheet), the CORS
wildcard reflecting to any origin on a no-auth bind (server.py:933-943 —
a drive-by web page can read debug/rubric surfaces on a localhost
workshop bind), and the trace-bundle reader's hard `!=` schema-version
rejection with no compat story (trace_bundle.py:57-67).

## Proposal

- **A-018 — formula neutralization:** in `write_trace_bundle_csv`,
  apostrophe-prefix any cell in the four free-text columns (`raw_input`,
  stdout preview, stderr preview, `guessed_intent`) whose first char is
  `=`, `+`, `-`, `@`, tab, or CR (the OWASP CSV-injection set). Helper
  `_neutralize_csv_cell(value)` with a docstring naming the threat;
  applied at the export boundary only — stored traces stay verbatim
  (the store is data; the CSV is the attack surface). Structured columns
  (timestamps, exit codes, fingerprints) are shape-constrained and stay
  raw. Note: `-` prefixed negative-number-looking strings in free-text
  columns get prefixed too — correctness over cosmetics; document it.
- **A-019 — refuse `*`-without-auth:** `serve_main` (and
  `start_test_server` for parity) hard-errors on
  `--cors-allow-origin '*'` with no `--auth-token`. Rationale for
  refuse over warn: the exposure includes loopback binds (any visited
  website can read `127.0.0.1` responses cross-origin), so there is no
  safe unauthenticated `*` posture; the escape is an explicit origin
  value or adding auth. parser.error message says exactly that.
  Breaking flag-combination change → CHANGELOG + SECURITY.md note.
- **A-070 — version policy: matching-version guidance now, no compat
  reader yet.** The schema version has never bumped, so an N-1 adapter
  is machinery without a customer (YAGNI). Decide + document: bundles
  are read by the tool version that wrote them; the rejection error
  message gains the policy sentence + the remedy ("re-export from the
  live server with the current tool"). README trace-bundle section
  records the policy. When a real bump happens, the bumping PR owns the
  adapter decision — leave that instruction in the code comment beside
  the check.

## Boundaries And Non-Goals

- No neutralization of stored traces or debug-UI JSON (JSON is not a
  spreadsheet surface; the UI's own CSV exports are client-side — check
  whether the debug UI builds CSV in JS, and if so file it as a
  follow-up rather than widening this PR).
- No CORS allowlist redesign; single-origin behavior unchanged.

## Affected Files

`src/anomaly_metric_creator/trace_bundle.py`,
`src/anomaly_metric_creator/server.py` (flag gate),
tests (`test_trace_bundle.py`, serve-flag gate tests), CHANGELOG,
SECURITY.md, README, `.trellis/audit/ledger.md` flips
(A-018/A-019/A-070).

## Risks And Edge Cases

- Neutralization must be idempotent (an already-apostrophed cell is not
  double-prefixed) and applied after any truncation/preview logic so the
  first byte written is the guarded one.
- The `*` gate must not break `start_test_server` callers in the suite
  that legitimately pass `*` with a token — grep first.
- Excel vs LibreOffice quoting: apostrophe-prefix is the portable
  neutralization; keep csv.writer quoting untouched.

## Validation

- A-018 tests: one trace per trigger char × four columns → all cells
  prefixed; benign cells untouched; round-trip re-import unaffected
  (import path reads JSON, not CSV — assert that stays true).
- A-019 tests: `*`+no-auth → parser error naming the remedy; `*`+token
  OK; explicit origin+no-auth OK.
- `pytest tests/test_trace_bundle.py tests/test_server.py -n 0` + full
  suite.
