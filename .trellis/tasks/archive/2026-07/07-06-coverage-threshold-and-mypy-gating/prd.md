# Introduce coverage threshold and per-module mypy gating

## Review context

- **Source:** deep-dive testing/tooling review, 2026-07-06.
- **Confidence:** CONFIRMED (orphaned follow-up; no open tracker existed).
- **Severity:** MEDIUM — quality gates measured but never enforced; the
  repo's own standard is an 80% coverage minimum.
- **Category:** CI governance / quality gates.

## Goal

Execute the follow-up recorded in completed task
`07-02-ci-typecheck-and-coverage`: pick a coverage threshold and a
per-module mypy gating strategy now that the legacy decomposition is 7/10
landed, and fix the stale CI comment pointing at the closed task.

## Problem

The completed task's notes say: "Revisit gating + a coverage threshold
after 07-02-legacy-monolith-decomposition lands; tighten mypy per-module
(strict on new modules, permissive on legacy.py) rather than one global
flip." Until this task, no open tracker carried that work. Current state:

- Coverage runs report-only — no `--cov-fail-under` — and the produced
  `coverage.xml` is discarded (no artifact upload step).
- mypy runs with `continue-on-error: true`. Baseline history: ~119 errors
  in 5 files at introduction (py3.12); **137 errors in 9 files** measured
  2026-07-06 under the new `python_version = "3.14"` config (typeshed
  changes + more extracted modules) — use this as the fresh baseline.
- `.github/workflows/ci.yml` (the comment above the mypy step) still says
  "tracked in Trellis task 07-02-ci-typecheck-and-coverage" — a closed
  task; CLAUDE.md's CI section repeats the stale pointer.

## Requirements

- Measure the real current coverage number on a full-suite run and record
  it in this task.
- Coverage: introduce `--cov-fail-under` at a ratchet value at or below
  the measured number (never above), with a written plan to raise it
  toward the 80% standard as decomposition steps 8-10 land; upload
  coverage.xml as a workflow artifact.
- mypy: implement the per-module strategy — strict (gating) on the ten
  extracted modules and any clean server modules; permissive
  (report-only) on `legacy.py` until steps 8-10 land; flip
  `continue-on-error` off for the gated set.
- Update the ci.yml comment (and the CLAUDE.md CI paragraph) to point at
  this task / the ratchet plan instead of the closed task.

## Measurement (recorded 2026-07-08)

- **Coverage:** full-suite TOTAL = **88%** (9104 statements, 1134 missed),
  from the last two full-matrix runs on `main` (run 28898380172 / sha
  `9ac0349`, stable across runs). Ratchet floor set to **85**
  (`--cov-fail-under=85`) — ~3 points below measured, headroom for
  xdist/partition jitter.
- **mypy:** 137 errors in 9 files under `python_version = 3.14`
  (legacy.py 43, server_ops 41, server 24, server_traces 11, schema_impl 8,
  validate_impl 6, combine_impl 2, csv_layout 1, trace_bundle 1). **19 of 28
  modules are 0-error** and form the gated set: the extracted leaf modules
  (redaction, timeutil, otlp, artifacts, gauges_impl, otel_stream), the five
  facades (combine, models, otel, scenarios, schema), cli, `__init__`, and
  the clean `server_*` modules (server_commands, server_debug_ui,
  server_helm, server_kubernetes, server_mcp, server_mutations).

## Acceptance Criteria

- [x] CI fails on coverage regression below the ratchet value
      (`--cov-fail-under=85` on the aggregating pytest run).
- [x] mypy gates on the agreed module set (19 clean modules via
      `mypy --follow-imports=silent`, `continue-on-error` off); the
      report-only whole-package baseline stays for legacy.py + the dirty
      server layer.
- [x] coverage.xml uploaded as an artifact on full-lane runs
      (`actions/upload-artifact`, `if: ${{ !cancelled() }}`).
- [x] No stale task pointers remain in ci.yml comments or CLAUDE.md (the
      `07-02-ci-typecheck-and-coverage` pointer now points at this task).

## Notes

- Sequencing: can start now; finishes naturally alongside decomposition
  steps 8-10 (`07-02-decomp-cli-args`, `07-02-decomp-catalog-data`,
  `07-02-decomp-generation-topology`).
