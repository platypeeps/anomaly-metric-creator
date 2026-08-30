# Copilot instructions

This repository's detailed Copilot review rules live in
`.github/instructions/anomaly-metric-creator.instructions.md`. Read that file
first, then load the relevant Trellis spec from
`docs/spec/amc/backend/index.md` for the diff shape under review.

Use the local-first review cycle:

- Prefer findings grounded in the current diff, tests, docs, and Trellis specs.
- Do not spend review comments on generic Python style that ruff or existing
  hooks already enforce.
- Check whether the PR ran the quick local gate
  (`SD_AI_COMMAND_PACK_FULL_CHECK_PRISM=0 SD_AI_COMMAND_PACK_FULL_CHECK_GITO=0 bash ~/.agents/bin/sd-ai-command-pack-full-check.sh`)
  or the full gate (`bash ~/.agents/bin/sd-ai-command-pack-full-check.sh`) before recommending
  remote CI or another Copilot pass.
- For CI/review-tooling changes, check `scripts/classify-ci-changes.sh`,
  `.github/workflows/ci.yml`, `docs/DEVELOPMENT_CYCLE.md`, and
  `docs/spec/amc/backend/testing-quality.md` together so the cadence does
  not drift. `tools/check_ci_review_contract.py` should cover the named
  anchors for those changes.
- For behavior-changing diffs, use `~/.agents/bin/sd-ai-command-pack-pr-body-scope.py`
  and `.sd-ai-command-pack/pr-body-scope.json` as the local contract for
  whether the PR body needs `Automation scope:`, `CI/review scope:`,
  `Tooling/generated scope:`, `Docs/user-facing scope:`, or
  `Runtime/server scope:`. Ask once for missing scope sections instead of
  repeating inline scope comments.
- Use `docs/REVIEW_PATTERNS.md` for recurring AMC review issues before adding
  a new comment that may already be covered by a local guard.
