---
name: trellis-full-check
description: Use when the user asks to run the expensive local verification gate, prepare a PR for readiness, or perform a Prism-first review-cycle check before requesting remote Copilot review.
---

# Trellis Full Check

Run this project-local skill for `/trellis:full-check` style work. It is an
optional but strongly recommended PR-readiness gate, not an every-edit
requirement.

The canonical implementation is:

```bash
bash scripts/trellis-full-check.sh
```

## What It Does

The script runs:

1. `git diff --check` for unstaged changes.
2. `git diff --cached --check` for staged changes.
3. Common package scripts when `package.json` contains them:
   `typecheck`, `lint`, `test:unit`, `test:integration`, `build`, and
   `test:e2e`.
4. Prism local review when `prism` is on `PATH` and Prism is not disabled.
5. Gito review only when explicitly enabled.

## Safety Rules

- Do not stage, commit, push, or edit files as part of this skill unless the
  user separately asks for fixes.
- Treat failures as evidence to report and fix in the normal Trellis
  implementation flow.
- Prism is optional by default because some environments lack provider
  credentials. Set `TRELLIS_FULL_CHECK_PRISM=required` when a missing or
  unauthenticated Prism review should fail the command.
- Gito is opt-in because it may invoke `uvx`, use a cache outside the repo, and
  require LLM credentials or network access. Set `TRELLIS_FULL_CHECK_GITO=1` to
  run it. Reports are written to `.build/review/gito` by default.
- If the script reports skipped checks, include those skips in the final report.

## Useful Environment Variables

- `TRELLIS_FULL_CHECK_BASE_REF`: base ref for branch review. Defaults to
  `origin/main`.
- `TRELLIS_FULL_CHECK_NPM_SCRIPTS`: space-separated package scripts to run.
- `TRELLIS_FULL_CHECK_PACKAGE_RUNNER`: package runner. Defaults to `npm`.
- `TRELLIS_FULL_CHECK_SKIP_NPM=1`: skip package scripts.
- `TRELLIS_FULL_CHECK_PRISM=0`: skip Prism.
- `TRELLIS_FULL_CHECK_PRISM=required`: fail if Prism is missing or
  unauthenticated.
- `TRELLIS_FULL_CHECK_PRISM_RULES`: explicit Prism rules file.
- `TRELLIS_FULL_CHECK_PRISM_FAIL_ON`: Prism fail threshold. Defaults to `high`.
- `TRELLIS_FULL_CHECK_GITO=1`: run Gito review after Prism.
- `TRELLIS_FULL_CHECK_GITO_BASE_REF`: base ref for Gito review. Defaults to
  `TRELLIS_FULL_CHECK_BASE_REF`, then `origin/main`.
- `TRELLIS_FULL_CHECK_GITO_OUT_DIR`: Gito report output directory. Defaults to
  `.build/review/gito`.

## Expected Report

Report:

- Whether deterministic checks passed.
- Which package scripts ran or were skipped.
- Whether Prism ran, skipped, found findings, or lacked credentials.
- Whether Gito ran or was intentionally skipped, and where reports were written.
- Any command that failed and the smallest next fix.
