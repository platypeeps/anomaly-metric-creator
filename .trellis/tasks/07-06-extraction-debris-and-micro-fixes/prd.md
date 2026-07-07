# Clear extraction debris and apply micro-fixes sweep

## Review context

- **Source:** deep-dive review, 2026-07-06 (architecture + generator +
  testing + tooling agents).
- **Confidence:** CONFIRMED (each item verified individually).
- **Severity:** LOW each; bundled so they stop misleading readers.
- **Category:** cleanup sweep — one small PR.

## Goal

One PR clearing the mechanical debris the review found — items too small
for their own tasks but wrong enough to mislead.

## Scope (each verified 2026-07-06)

- [combine_impl.py:500](src/anomaly_metric_creator/combine_impl.py:500)-519:
  delete the dead `from .otlp import ...` re-export block (swept up by the
  step-5 splice; its "historic `legacy.<name>` surface" comment is false
  in that file — [legacy.py:8485](src/anomaly_metric_creator/legacy.py:8485)
  holds the real re-import; no code or test imports these names via
  `combine_impl`).
- [gauges_impl.py:193](src/anomaly_metric_creator/gauges_impl.py:193)-201:
  delete the orphaned trailing comment block describing
  `SCHEMA_DOCUMENT_VERSION` (lives at
  [schema_impl.py:37](src/anomaly_metric_creator/schema_impl.py:37)).
- [legacy.py:2315](src/anomaly_metric_creator/legacy.py:2315):
  `_build_timestamp_arrays` docstring says "all six components" —
  `COMPONENTS` has 14 entries.
- [legacy.py:9124](src/anomaly_metric_creator/legacy.py:9124):
  `if otel_active else None` inside an `if otel_active` branch — dead
  condition; simplify.
- [tests/test_ci_review_contract.py:128](tests/test_ci_review_contract.py:128):
  raw-string the fixture regex to clear the
  `SyntaxWarning: invalid escape sequence '\.'` — the suite's only
  collection warning.
- [tests/test_server_hardening.py:119](tests/test_server_hardening.py:119)
  and :140: replace the bare `time.sleep(0.5)` synchronization with the
  deadline-bounded poll pattern already used at
  [tests/test_server.py:79](tests/test_server.py:79) (flake risk on
  loaded runners).
- `.gitignore`: add `.ruff_cache/` (currently protected only by ruff's
  self-written nested .gitignore; the full-check script already excludes
  it).
- [tools/check_amc_module_load.py](tools/check_amc_module_load.py): add
  the missing exit-code-2 arm + a docstring "Exit codes" section so it
  honors the documented 0/1/2 contract all 11 sibling lints follow
  (wrap file reads so an I/O error exits 2, not a traceback).
- Decide-and-do: `.understand-anything/` (tracked generated analysis
  cache, stale since 2026-05-25) — refresh it or gitignore it.
- `.trellis/spec/amc/backend/testing-quality.md`: one line noting
  `tools/benchmark_combine.py` is intentionally exempt from the
  every-tool-has-tests convention (measurement harness), so the exemption
  is recorded rather than rediscovered.

## Acceptance Criteria

- [ ] All listed items fixed in one PR; full suite green.
- [ ] No golden-hash changes (generator-side edits are comment/docstring/
      dead-condition only).
- [ ] `pytest --collect-only` emits zero warnings.
- [ ] `check_amc_module_load.py` exits 2 on an unreadable path (unit
      test added).

## Notes

- Duplicate-fixture-name trap (`one_day_schema_run` defined in both
  test_schema_file.py and test_validate_output.py at ~60x different
  cadences) is intentionally NOT renamed here — it collides with
  `07-06-heavy-marker-module-fixture-coverage`'s fixture rework; take it
  there.
