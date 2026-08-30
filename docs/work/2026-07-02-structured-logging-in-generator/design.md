# Structured logging in the generator — Design (SD Work Designs, 2026-07-17)

## Overview

13 `print(..., file=sys.stderr)` sites in src/, zero `logging` usage.
Generator warnings (out-of-range specs, zero-match filters, scenario
drop WARNINGs) escape the serve process's stderr unstructured, invisible
to the `StructuredRequestLogger`. The PRD fixes the constraints:
byte-stable CLI stderr, no import-time logging config, deterministic
warning ordering preserved.

## Proposal

- **Logger:** `logger = logging.getLogger("anomaly_metric_creator")`,
  `propagate = False`. Messages keep their **exact current text
  including the `WARNING: ` literal prefix** — the CLI handler's
  formatter is bare `%(message)s`, so stderr bytes are unchanged and
  every stderr-scraping test stays green without edits. (Deliberate
  trade: redundant-looking `logger.warning("WARNING: …")` buys
  byte-stability; note it in code comment.)
- **Scope:** convert the 7 legacy.py generator/resolution sites AND the
  4 otel_stream.py retry/FAIL notices (decided in: serve streams OTEL
  from background threads too, so the same visibility argument applies;
  their text also stays verbatim). The 2 serve-startup prints in
  server.py stay prints (PRD scope note).
- **CLI handler attach:** an idempotent `_ensure_cli_log_handler()`
  called at the top of `legacy.main()` — attaches one
  `StreamHandler(sys.stderr)` with the bare formatter if not already
  attached (marker attribute on the handler). Rationale: tests invoke
  `main()` in-process and assert stderr; serve's one-shot generation
  also calls `main()`, which reproduces today's stderr behavior exactly.
  No import-time configuration anywhere.
- **Serve capture:** `_start_continuous_generation`'s worker attaches a
  forwarding handler (records → `StructuredRequestLogger` when
  configured, as `generation-warning` records) for the duration of each
  regen pass — additive: stderr keeps the lines (parity with today),
  the structured log gains them.
- **Ordering:** `_resolve_scenarios`' sorted-slug warning order is a
  data-ordering property (the loop emits in sorted order) — logging
  preserves call order by construction; the existing order test is the
  guard.

## Boundaries And Non-Goals

- No log levels/verbosity flags, no message rewording, no root-logger
  configuration, no server.py startup-print conversion.
- Coordination: the 7 legacy.py sites include resolution-cluster code
  that decomp step 9 moves to `scenarios_impl.py` — convert before or
  after the move indifferently (print→logger edits are line-local and
  survive a verbatim move); just avoid the same-window collision by not
  running both PRs concurrently.

## Affected Files

`src/anomaly_metric_creator/legacy.py` (logger + 7 sites +
`_ensure_cli_log_handler`), `src/anomaly_metric_creator/otel_stream.py`
(4 sites), `src/anomaly_metric_creator/server.py` (regen-worker
forwarding handler), tests (one new serve-capture test; existing stderr
assertions untouched), CLAUDE.md note.

## Risks And Edge Cases

- Handler idempotency under pytest-xdist re-imports and repeated
  in-process `main()` calls — the marker-attribute check must key on
  the logger's handler list, and the env-isolation/session-fixture
  rules mean no cross-test handler leakage (function-scoped forwarding
  handlers must detach in `finally`).
- `logging` module state is process-global: the forwarding handler must
  be removed after each regen pass or on shutdown, or repeated passes
  stack handlers (classic duplicate-record bug — test for single
  emission after two passes).
- Byte-stability proof: run the full suite — every existing WARNING
  assertion is the regression net.

## Validation

- New test: a warning raised during a continuous-generation cycle is
  observable via the structured logger AND appears once (not N times)
  after multiple cycles.
- Existing stderr-assertion tests pass unmodified (the acceptance).
- `pytest tests/test_scenarios.py tests/test_cli.py -n 0` + full suite.
