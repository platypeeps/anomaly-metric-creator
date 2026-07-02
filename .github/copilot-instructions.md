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
  (`SD_AI_COMMAND_PACK_FULL_CHECK_PRISM=0 SD_AI_COMMAND_PACK_FULL_CHECK_GITO=0 bash scripts/sd-ai-command-pack-full-check.sh`)
  or the full gate (`bash scripts/sd-ai-command-pack-full-check.sh`) before recommending
  remote CI or another Copilot pass.
- For CI/review-tooling changes, check `scripts/classify-ci-changes.sh`,
  `.github/workflows/ci.yml`, `docs/DEVELOPMENT_CYCLE.md`, and
  `.trellis/spec/amc/backend/testing-quality.md` together so the cadence does
  not drift. `tools/check_ci_review_contract.py` should cover the named
  anchors for those changes.
- For behavior-changing diffs, use `scripts/sd-ai-command-pack-pr-body-scope.py`
  and `.sd-ai-command-pack/pr-body-scope.json` as the local contract for
  whether the PR body needs `Automation scope:`, `CI/review scope:`,
  `Tooling/generated scope:`, `Docs/user-facing scope:`, or
  `Runtime/server scope:`. Ask once for missing scope sections instead of
  repeating inline scope comments.
- Use `docs/REVIEW_PATTERNS.md` for recurring AMC review issues before adding
  a new comment that may already be covered by a local guard.

<!-- SD-AI-COMMAND-PACK:COPILOT-GUIDANCE:START -->
## Trellis And SD AI Command Pack Review Guidance

- Trellis is the repository workflow foundation; the SD AI Command Pack adds
  Software Delivery command wrappers, local review tooling, post-merge
  housekeeping, and update-spec knowledge refreshes on top of it. Repo-local
  entry points: `.trellis/workflow.md`, `.agents/skills/sd-*/SKILL.md`, and
  `docs/SD_AI_COMMAND_PACK.md`.
- Treat copied-in Trellis and SD AI command pack payloads as vendored files:
  do not comment on their wording, style, examples, or implementation details
  unless the PR explicitly changes that integration, the copied file is the
  primary subject, it leaks a secret, breaks obvious syntax or repository
  wiring, or directly contradicts the PR's stated tooling goal. Copied
  payloads match these families:
  - `.trellis/scripts/**` and `.trellis/agents/**`
  - `**/skills/trellis-*/**` and `**/skills/sd-*/**` under `.agents/`,
    `.claude/`, `.codex/`, `.cursor/`, `.gemini/`, `.github/`, `.opencode/`
  - Trellis and `sd` command or prompt files under `.claude/commands/`,
    `.cursor/commands/`, `.gemini/commands/`, `.opencode/commands/`, and
    `.github/prompts/` (including `continue.prompt.md` and
    `finish-work.prompt.md`)
  - `.github/copilot/**`, `.github/hooks/trellis.json`, and
    `.github/agents/trellis-*`
  - `scripts/sd-ai-command-pack-*`, legacy `scripts/trellis-*.sh`, and
    `scripts/update_repomix*`
  - `.gito/**`, `.prism/**`, `.sd-ai-command-pack/**`,
    `docs/SD_AI_COMMAND_PACK.md`, and legacy `docs/TRELLIS_REVIEW_PR_PACK.md`
- Spend review budget on app behavior, data contracts,
  data/access/security boundaries, migrations and rollback behavior, token or
  invitation fail-closed behavior, tests, operator-facing documentation, and
  repo-owned scripts.
- Before reviewing generated, copied, Trellis workspace, repository-map, or
  pack files, look for a `Tooling/generated scope:` section in the PR body.
  Broad automation or CI diffs use `Automation scope:` or `CI/review scope:`;
  repos add categories via `.sd-ai-command-pack/pr-body-scope.json`. If the
  matching section is missing, request it once instead of scattering scope
  comments across files.
- Group duplicate root causes into one comment. When deterministic local checks
  already cover a repeated issue class, point at the failing check once instead
  of repeating inline findings; if the check is missing or fragile, ask for one
  focused fixture in the local guard suite.
- Separate current, non-outdated unresolved findings from
  stale or outdated review threads. Treat copied or generated payloads as
  source and sync-contract review surfaces, not style-review surfaces.
<!-- SD-AI-COMMAND-PACK:COPILOT-GUIDANCE:END -->
