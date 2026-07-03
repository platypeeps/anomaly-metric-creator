# Add type-checking and coverage measurement to CI

## Audit context

- **Source:** first-time staff-engineer audit, 2026-07-02.
- **Confidence:** CONFIRMED absent.
- **Severity:** MEDIUM — quality tooling gap; annotations currently decorative.
- **Category:** conspicuously-absent / CI.

## Goal

Make the existing type annotations enforceable and give the 80% coverage target
from CLAUDE.md an actual measurement, so both stop being aspirational.

## Problem

- **No type-checker in CI.** The codebase is thoroughly annotated (PEP-8 +
  type hints per the repo's own Python style rules), but nothing runs `mypy` or
  `pyright`. Type regressions ship silently.
- **No code-coverage measurement.** `.github/workflows/ci.yml` mentions
  "coverage" only in the sense of the heavy/light **test partition**
  ([ci.yml:267](.github/workflows/ci.yml:267)), not `--cov`. CLAUDE.md /
  `common/testing.md` state an 80% minimum that nothing enforces or even reports.

## Requirements

- Add a type-check step to the CI `test` job (or a dedicated job):
  - Choose `mypy` or `pyright`. Given the numpy-heavy code, start **non-gating**
    (report-only) to surface the baseline, then tighten.
  - Add the tool to the `dev` extra in `pyproject.toml` and pin it (mirror the
    exact-pin + lockstep convention used for `ruff`; if a pre-commit hook is
    added, wire a lockstep check like `check_ruff_lockstep.py`).
  - Configure per-module strictness so third-party-untyped imports (numpy,
    protobuf) don't drown the signal.
- Add coverage measurement to the pytest step:
  - `pytest-cov` in the `dev` extra; emit `--cov=src --cov-report=term-missing`
    (and XML for CI).
  - Reconcile with the `-n`/xdist config and the `heavy`/`not heavy` partition
    so coverage is aggregated across **both** CI steps (coverage combine), not
    measured on only the light subset.
  - Start non-gating (report the number); decide a threshold in a follow-up once
    the real baseline is known.
- Update `required_plugins` / `addopts` in `pyproject.toml` if new plugins are
  introduced (the repo already gates on `required_plugins`).

## Acceptance criteria

- [x] CI runs a type-checker and prints results on every PR (non-gating to
      start); the tool is pinned in the `dev` extra.
- [x] CI produces an aggregated coverage report across the heavy + light test
      steps; the percentage is visible in the run.
- [x] `pyproject.toml` `dev` extra, `required_plugins`, and any lockstep pin are
      updated consistently; `tests/` still collect and pass under the existing
      xdist config.
- [x] CLAUDE.md's testing/CI section notes the new steps and their gating status.
- [x] A follow-up decision (threshold values, gating on/off) is recorded in this
      task's notes or a linked task.

## Notes

- Non-gating first is deliberate: a numpy-heavy monolith will have a messy
  initial `mypy` baseline; gating immediately would block all PRs.
- This pairs naturally with `07-02-legacy-monolith-decomposition` — smaller
  modules type-check far more cleanly.
