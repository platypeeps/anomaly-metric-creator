---
title: Replace generator stderr prints with structured logging
status: planning
created: 2026-07-02
---
# Replace generator stderr prints with structured logging

## Audit context

- **Source:** first-time staff-engineer audit, 2026-07-02.
- **Confidence:** CONFIRMED (pattern observed across the generator).
- **Severity:** LOW — observability hygiene; the user's Python rules also flag
  `print()` in library code.
- **Category:** low-hanging fruit / observability.

## Goal

Route generator diagnostics through the `logging` module so `amc serve` can
capture/route them (into the structured request logger or a file) while CLI
mode keeps its current human-readable stderr UX.

## Problem

`generate_component` and the scenario-resolution pipeline emit warnings via
`print(..., file=sys.stderr)` — e.g. the out-of-range anomaly-spec warning at
[legacy.py:1337](src/anomaly_metric_creator/legacy.py:1337), the zero-match
instance-filter warning at
[legacy.py:1292](src/anomaly_metric_creator/legacy.py:1292), and the
severity/duration drop warnings in `_resolve_scenarios`
([legacy.py:7451](src/anomaly_metric_creator/legacy.py:7451) /
[legacy.py:7458](src/anomaly_metric_creator/legacy.py:7458)). Because
`server.py` imports and calls this same module in
a background continuous-generation thread
([server.py:1607](src/anomaly_metric_creator/server.py:1607)), these prints
escape as unstructured text onto the server process's stderr, bypassing the
`StructuredRequestLogger` ([server.py:119](src/anomaly_metric_creator/server.py:119))
and any log routing the operator configured.

*Scope note (2026-07-06 re-verification):* src/ has **13** `file=sys.stderr`
print sites and **zero** `logging` usage anywhere in the package. Generator
side: 7 in `legacy.py` + 4 in `otel_stream.py` (the OTEL retry/FAIL
notices at otel_stream.py:250/268/446/464 — decide whether they convert in
this task or stay CLI-facing); the 2 in `server.py` (:1552/:1556) are
serve-startup messages and out of scope here.

## Requirements

- Introduce a module logger (`logger = logging.getLogger("anomaly_metric_creator")`)
  and convert the generator/resolution `print(..., file=sys.stderr)` warnings to
  `logger.warning(...)`.
- Preserve CLI UX: attach a default `StreamHandler` to stderr with a plain
  formatter **only** when running as the CLI entrypoint (in `main()` / the
  console-script path), so existing stderr-scraping tests and user expectations
  hold. Do not configure logging at import time (library code must not hijack
  the root logger).
- Give server mode a hook to capture these records (e.g. the continuous-gen
  thread can attach a handler that forwards to the structured logger / a file),
  so regeneration warnings become visible in serve mode.
- Keep the message text stable where tests assert on it — grep the suite for the
  asserted `WARNING:` strings and preserve them (or update the assertions in the
  same change). Note the deterministic sorted-order warning contract in
  `_resolve_scenarios` (tests assert order).

## Acceptance criteria

- [ ] Generator/resolution warnings go through `logging`, not bare `print` to
      stderr.
- [ ] CLI runs still emit the same human-readable warnings to stderr (existing
      `tests/` that assert on warning text pass, updated if needed).
- [ ] Serve mode can capture regeneration warnings (a test asserts a warning
      raised during a continuous-generation cycle is observable via the
      configured handler, not just leaked to process stderr).
- [ ] No logging configuration happens at import time; only at the CLI
      entrypoint.
- [ ] The deterministic warning-ordering contract in `_resolve_scenarios` is
      preserved.

## Notes

- Small, self-contained; good "first contribution" style task.
- Sequence **after** `07-02-atomic-artifact-writes` if both touch the generation
  path, to avoid overlapping edits around the same writers.
