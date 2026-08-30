# Copilot instructions

This repository's detailed Copilot review rules live in
`.github/instructions/anomaly-metric-creator.instructions.md`. Read that file
first, then load the relevant spec from `docs/spec/amc/backend/index.md` for
the diff shape under review.

Use the local-first review cycle:

- Prefer findings grounded in the current diff, tests, docs, and the specs
  under `docs/spec/`.
- Do not spend review comments on generic Python style that ruff or existing
  hooks already enforce.
- Check whether the PR ran the local deterministic gate
  (`.venv/bin/pre-commit run --all-files` and
  `node scripts/check-review-preflight.mjs`) before recommending remote CI or
  another Copilot pass.
- For CI/review-tooling changes, check `scripts/classify-ci-changes.sh`,
  `.github/workflows/ci.yml`, `docs/DEVELOPMENT_CYCLE.md`, and
  `docs/spec/amc/backend/testing-quality.md` together so the cadence does
  not drift. `tools/check_ci_review_contract.py` should cover the named
  anchors for those changes.
- For behavior-changing diffs, `.github/PULL_REQUEST_TEMPLATE.md` and
  `docs/DEVELOPMENT_CYCLE.md` are the contract for whether the PR body needs
  `Automation scope:`, `CI/review scope:`, `Tooling/generated scope:`,
  `Docs/user-facing scope:`, or `Runtime/server scope:`. No tool checks the
  body for them, so this is a review judgement -- ask once for a missing scope
  section instead of repeating inline scope comments.
- Use `docs/REVIEW_PATTERNS.md` for recurring AMC review issues before adding
  a new comment that may already be covered by a local guard.
