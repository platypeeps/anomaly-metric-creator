# Library-API error posture — Design (SD Work Designs, 2026-07-17)

## Overview

The posture decision is already recorded in the PRD (2026-07-17,
sdelmas): **CLI-internal** — the facade exports are not a supported
programmatic API; `SystemExit`, combine-path stdout, and silent
missing-CSV skips are documented semantics, not defects. Execution is
docs-only; this design just fixes the exact doc surfaces and wording so
the implementing session is mechanical.

## Proposal

1. **Docstrings** at the enumerated sites (one added paragraph each,
   same wording template: "CLI-internal surface: may raise SystemExit /
   print to stdout / skip missing inputs; not a supported programmatic
   API — see error-handling spec"):
   - `combine_impl.combine_logs_unified` (+ `combine_logs`) — SystemExit
     on missing inputs, unconditional stdout progress.
   - `otel_stream.stream_otel_gauges` and `gauges_impl.write_gauges_csv`
     — silent `p.exists()` skip semantics.
   - The `otlp.py` protobuf-ImportError SystemExit sites and
     `csv_layout` FD-preflight SystemExit — a module-docstring note in
     each (site-by-site repetition adds noise; the module docstring
     carries it).
   - The three facades (`combine.py`, `otel.py`, `schema.py`) — one
     sentence each pointing at the posture.
2. **Spec:** record the posture + rationale + the revisit trigger (a real
   embedder requirement flips this to library-grade *inside* the
   typed-boundaries work, A-008/009/010 — the PRD records why) in the
   focused CLI-error spec `.trellis/spec/amc/backend/api-cli-server.md`.
   `error-handling.md` is a compatibility **pointer** that forbids new
   conventions ("Do not add new conventions here. Update the focused
   specs above instead."), so it stays untouched — this corrects the
   PRD's "fill the error-handling stub" phrasing, which predates that
   stub's conversion to a pointer.
3. **CLAUDE.md:** one short paragraph in the facade section stating the
   posture (so the next facade export follows the rule).

## Boundaries And Non-Goals

- Zero code-behavior change; zero hash exposure; the library-grade arm
  is N/A per the recorded decision.
- Do not soften the wording into "may change later" hedges — the spec
  states the posture and its single revisit trigger.

## Affected Files

`combine_impl.py`, `otel_stream.py`, `gauges_impl.py`, `otlp.py`,
`csv_layout.py`, the three facade modules (docstrings only),
`.trellis/spec/amc/backend/api-cli-server.md` (posture; `error-handling.md`
stays a pointer, untouched), CLAUDE.md.

## Risks And Edge Cases

- Docstring edits must not disturb any doctest-like or
  docstring-asserting test (grep first; none expected).
- Keep each docstring addition short — the checklist's doc-sync heading
  cuts both ways (stale *and* bloated docs are drift).

## Validation

- `pytest -m "not heavy" -n 2` (docstring-only safety) + pre-commit.
- Grep sweep: every site listed in the PRD's problem section carries
  the posture note.
