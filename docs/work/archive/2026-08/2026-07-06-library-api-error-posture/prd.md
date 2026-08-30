---
title: Decide the library-API error and output posture for facade exports
status: done
created: 2026-07-06
branch: docs/library-api-error-posture
---
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

## Problem (verified 2026-07-06; line refs re-anchored 2026-08-26)

Three behaviors are process-oriented but live in functions exported
through the package facades (`combine.py`, `otel.py`, `schema.py`):

- **`SystemExit` raised deep in importable modules:**
  [otlp.py:83](src/anomaly_metric_creator/otlp.py:83)/:193/:323/:410
  (protobuf ImportError),
  [csv_layout.py:195](src/anomaly_metric_creator/csv_layout.py:195)/:205
  (FD-limit preflight),
  [combine_impl.py:130](src/anomaly_metric_creator/combine_impl.py:130)/:180/:185/:400/:451/:457
  (missing inputs). A host application gets process-exit semantics instead
  of a catchable domain error. *(Re-anchored 2026-08-26, after this task's
  own docstring additions. `combine_impl.py` has six such sites, not the
  four originally listed -- `:180`/`:185` were never enumerated.)*
- **Unconditional stdout chatter in the combine path:**
  `combine_logs_unified` prints "Creating UNIFIED format…",
  per-component "Loading X.csv…", etc.
  ([combine_impl.py:134](src/anomaly_metric_creator/combine_impl.py:134)-286)
  with no quiet/verbose gate, inherited by every embedding caller.
- **Silent skip of missing component CSVs in the gauge streamer:**
  `stream_otel_gauges` filters `if p.exists()` with no warning
  ([otel_stream.py:401](src/anomaly_metric_creator/otel_stream.py:401);
  `write_gauges_csv` mirrors it at
  [gauges_impl.py:98](src/anomaly_metric_creator/gauges_impl.py:98), which
  filters on the `exists` flag `_scan_component_csv_headers` records from
  `Path.exists()` at
  [csv_layout.py:255](src/anomaly_metric_creator/csv_layout.py:255)) —
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
- Whichever is chosen, record it in the focused CLI-error spec
  `.trellis/spec/amc/backend/api-cli-server.md` so the next surface follows
  the same rule. *(Corrected 2026-08-26, per design.md step 2:
  `error-handling.md` is a compatibility pointer that forbids new
  conventions -- "Do not add new conventions here" -- so it stays
  untouched. The original "pointer stub" phrasing predates that
  conversion.)*
- If library-grade is chosen, keep stderr/stdout text byte-stable for the
  CLI paths that tests assert on.

## Acceptance Criteria

- [x] The posture decision is recorded (spec + CLAUDE.md), with rationale.
      (`api-cli-server.md` § Library-API Error Posture; CLAUDE.md module
      ownership section.)
- [x] The obligation the chosen posture carries is met. CLI-internal was
      chosen, so its obligation applies: docstrings on the exported functions
      state the process-oriented semantics explicitly. All eight modules carry
      the note — `grep -rl "CLI-internal surface" src/anomaly_metric_creator/`
      returns 8 files.

      The two postures were mutually exclusive, so this is one criterion, not
      two. Library-grade would have obliged the opposite work — no
      facade-exported function raising `SystemExit`, plus tests for a new
      domain error and the unchanged CLI exit paths — and none of it was done,
      because it was the rejected option. It is recorded as rejected in the
      Decision section below rather than carried here as a criterion no
      outcome of this task could ever satisfy.

## Notes

- Interacts with `07-02-structured-logging-in-generator` (both touch the
  "library code talking to the terminal" theme) — sequence them together
  or one after the other.

## Decision (2026-07-17, sdelmas)

**Chosen posture: CLI-internal.** The facade-exported functions are a
CLI-internal surface, not a supported programmatic API. `SystemExit` deep
in importable modules, unconditional combine-path stdout, and silent
missing-CSV skips are all acceptable and will be *documented as such*
rather than reworked.

Rationale: this is a CLI-first tool (`Private :: Do Not Upload`, git-only
install, `main()` is the only real entry point); there is no known
programmatic embedder, so library-grade error handling is YAGNI at LOW
severity. If a supported `import`-and-call API becomes a real requirement,
revisit as library-grade — and do it inside the typed-boundaries audit
work (`07-17-audit-typed-boundaries`, A-008/A-009/A-010), which already
reshapes these exact signatures, to avoid double-churn.

Execution now reduces to the CLI-internal arm of the requirements:
- Document the process-oriented semantics in CLAUDE.md and in the
  docstrings of the affected facade exports (`combine_logs_unified`,
  `stream_otel_gauges`/`write_gauges_csv`, the `otlp`/`csv_layout`/
  `combine_impl` SystemExit sites).
- Record the posture + rationale in
  `.trellis/spec/amc/backend/api-cli-server.md` so the next surface follows
  the same rule. *(Corrected 2026-08-26, same correction as the
  Requirements bullet above: this said `error-handling.md`, which is a
  compatibility pointer forbidding new conventions and stays untouched.)*
- No code change; no golden-hash impact. The library-grade acceptance
  bullet is now N/A.
