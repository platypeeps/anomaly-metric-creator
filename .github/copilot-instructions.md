# Copilot instructions

This repository's detailed Copilot review rules live in
`.github/instructions/anomaly-metric-creator.instructions.md`. Read that file
first, then load the relevant Trellis spec from
`.trellis/spec/amc/backend/index.md` for the diff shape under review.

Use the local-first review cycle:

- Prefer findings grounded in the current diff, tests, docs, and Trellis specs.
- Do not spend review comments on generic Python style that ruff or existing
  hooks already enforce.
- Check whether the PR ran the quick local gate
  (`TRELLIS_FULL_CHECK_LEVEL=quick bash scripts/trellis-full-check.sh`) or the
  full gate (`bash scripts/trellis-full-check.sh`) before recommending remote
  CI or another Copilot pass.
- For CI/review-tooling changes, check `scripts/classify_ci_changes.sh`,
  `.github/workflows/ci.yml`, `docs/DEVELOPMENT_CYCLE.md`, and
  `.trellis/spec/amc/backend/testing-quality.md` together so the cadence does
  not drift. `tools/check_ci_review_contract.py` should cover the named
  anchors for those changes.
- Use `docs/REVIEW_PATTERNS.md` for recurring AMC review issues before adding
  a new comment that may already be covered by a local guard.
