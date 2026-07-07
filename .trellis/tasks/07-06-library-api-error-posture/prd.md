# Decide the library-API error and output posture for facade exports

## Review context

- **Source:** deep-dive generator-code review, 2026-07-06.
- **Confidence:** CONFIRMED behaviors; the *posture* is the open decision.
- **Severity:** LOW — fine for a CLI-first tool; wrong if programmatic
  embedding is a supported use.
- **Category:** conscious-decision / API design.

## Goal

Decide — and record — whether the package's facade-exported functions are
a supported programmatic API (raise catchable domain errors, no stdout
chatter) or a CLI-internal surface (current behavior, documented as such).

## Problem (verified 2026-07-06)

Three behaviors are process-oriented but live in functions exported
through the package facades (`combine.py`, `otel.py`, `schema.py`):

- **`SystemExit` raised deep in importable modules:**
  [otlp.py:76](src/anomaly_metric_creator/otlp.py:76)/:186/:316/:403
  (protobuf ImportError),
  [csv_layout.py:171](src/anomaly_metric_creator/csv_layout.py:171)/:181
  (FD-limit preflight),
  [combine_impl.py:126](src/anomaly_metric_creator/combine_impl.py:126)/:396/:483/:489
  (missing inputs). A host application gets process-exit semantics instead
  of a catchable domain error.
- **Unconditional stdout chatter in the combine path:**
  `combine_logs_unified` prints "Creating UNIFIED format…",
  per-component "Loading X.csv…", etc.
  ([combine_impl.py:130](src/anomaly_metric_creator/combine_impl.py:130)-243)
  with no quiet/verbose gate, inherited by every embedding caller.
- **Silent skip of missing component CSVs in the gauge streamer:**
  `stream_otel_gauges` filters `if p.exists()` with no warning
  ([otel_stream.py:338](src/anomaly_metric_creator/otel_stream.py:338)-342;
  `write_gauges_csv` mirrors it at
  [gauges_impl.py:90](src/anomaly_metric_creator/gauges_impl.py:90)-93) —
  a typo'd path in a programmatic call yields a plausible-looking stream
  with a component silently absent. `main()` always passes just-written
  files, so the CLI is unaffected.

## Requirements

- Make the posture decision first (maintainer call):
  - **CLI-internal (lowest effort):** document in CLAUDE.md and the
    facades' docstrings that exported functions may `SystemExit`, print
    to stdout, and skip missing inputs; no code change.
  - **Library-grade:** introduce a domain exception type raised instead
    of `SystemExit` (with `main()` catching it and exiting as today so
    CLI behavior and messages are unchanged), gate the combine chatter
    behind a verbosity flag defaulting to today's behavior for the CLI,
    and warn on skipped missing CSVs.
- Whichever is chosen, record it in
  `.trellis/spec/amc/backend/error-handling.md` (currently a pointer
  stub) so the next surface follows the same rule.
- If library-grade is chosen, keep stderr/stdout text byte-stable for the
  CLI paths that tests assert on.

## Acceptance Criteria

- [ ] The posture decision is recorded (spec + CLAUDE.md), with rationale.
- [ ] If library-grade: no facade-exported function raises `SystemExit`;
      tests cover the new domain error and the unchanged CLI exit paths.
- [ ] If CLI-internal: docstrings on the exported functions state the
      process-oriented semantics explicitly.

## Notes

- Interacts with `07-02-structured-logging-in-generator` (both touch the
  "library code talking to the terminal" theme) — sequence them together
  or one after the other.
