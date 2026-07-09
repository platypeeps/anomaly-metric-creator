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

### Added 2026-07-07 (review-ledger completion — items found by the same review that initially went untracked)

- `pyproject.toml` `[tool.pytest.ini_options]`: add `--strict-markers` to
  `addopts`. Both used markers (`full_resolution`, `heavy`) are already
  registered, so today a typo'd marker name only warns; strict mode makes
  it fail. Validate with a full collection pass (`pytest --collect-only`)
  in the PR.
- Tighten the residual bare-substring flag assertions to anchored/token
  matching per the repo's own checklist rule
  ([tests/test_cli.py:79](tests/test_cli.py:79) `"--help-all" in out`;
  [tests/test_args.py:676](tests/test_args.py:676)-680 five flag names
  substring-matched against the preflight error;
  [tests/test_scenarios.py:1620](tests/test_scenarios.py:1620)/:1643/:1666
  flags in WARNING lines). Do NOT touch
  tests/test_server.py:2039/:2041/:2692 — those are exact-token-safe
  (`in` on an argv *list*).
- `.github/workflows/ci.yml` classifier job: drop the declared-but-unused
  job outputs (`changed_count`, `python_changed`,
  `review_tooling_changed` at ci.yml:26/:31-32 — only
  `dependency_changed`/`workflow_changed`/`lightweight_only`/
  `app_required`/`full_ci_requested` are consumed downstream). The
  underlying `emit_output` calls in `scripts/classify-ci-changes.sh` stay
  (the CI contract lint anchors `emit_output "python_changed"` in the
  *script*, not the workflow outputs block) — run
  `tools/check_ci_review_contract.py` in the PR to confirm.

## Acceptance Criteria

- [x] All listed items fixed in one PR; full suite green.
- [x] No golden-hash changes (generator-side edits are comment/docstring/
      dead-condition only).
- [x] `pytest --collect-only` emits zero warnings and passes under
      `--strict-markers`.
- [x] `check_amc_module_load.py` exits 2 on an unreadable path (unit
      test added).
- [x] Flag-presence assertions in the three listed test files use
      anchored/token matching; `check_ci_review_contract.py` still exits 0
      after the ci.yml outputs cleanup.

## Resolution (2026-07-07)

All items cleared in one sweep:

- Deleted the dead `from .otlp import ...` re-export block in
  `combine_impl.py` (verified: nothing imports OTLP names via
  `combine_impl`) and the orphaned `SCHEMA_DOCUMENT_VERSION` comment in
  `gauges_impl.py`.
- `legacy.py`: `_build_timestamp_arrays` docstring "all six components" →
  "every component" (count-agnostic so it can't drift again); removed the
  dead `if otel_active else None` (already inside an `if otel_active` branch).
- `test_ci_review_contract.py`: raw-stringed the `.pre-commit-config.yaml`
  fixture so the `\.` regex no longer emits a `SyntaxWarning` (suite now
  collects with zero warnings).
- `test_server_hardening.py`: replaced both `time.sleep(0.5)` sync points
  with a `_poll_until_503` deadline-bounded helper.
- Anchored the flag assertions in `test_cli.py` (`--help-all`),
  `test_args.py` (five preflight-lever flags, now a loop), and
  `test_scenarios.py` (three `--signal-level`/`--duration-days` WARNING
  checks; added `import re`). Left the argv-list `in` checks in
  `test_server.py` untouched (already token-safe).
- `pyproject.toml`: `--strict-markers` in `addopts`.
- `ci.yml`: dropped the three unused classifier job outputs
  (`changed_count`, `python_changed`, `review_tooling_changed`); the two
  read inside the job (`dependency_changed`/`workflow_changed`) and the
  three consumed via `needs.changes.outputs` stay. Contract lint exit 0.
- `check_amc_module_load.py`: added the "Exit codes" docstring section and
  an exit-2 arm wrapping the file read (I/O / non-UTF-8 → 2, not a
  traceback); new `test_non_utf8_file_exits_2_not_1`.
- `.gitignore`: added `.ruff_cache/` and `.understand-anything/`; untracked
  the stale `.understand-anything/` generated cache via `git rm --cached`
  (kept on disk).
- `testing-quality.md`: recorded `benchmark_combine.py` as the intentional
  every-tool-has-tests exemption (measurement harness).

Verified: 1588 tests collect with zero warnings under `--strict-markers`;
184 touched-suite tests + the new exit-2 test + the anchored flag tests
pass; golden hashes unchanged (1-day default / combine / gauge byte-identity,
96 passed); ruff clean; contract lint exit 0.

## Notes

- Duplicate-fixture-name trap (`one_day_schema_run` defined in both
  test_schema_file.py and test_validate_output.py at ~60x different
  cadences) is intentionally NOT renamed here — it collides with
  `07-06-heavy-marker-module-fixture-coverage`'s fixture rework; take it
  there.
