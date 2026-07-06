---
name: sd-full-check
description: Use when the user asks to run the expensive local verification gate, prepare a PR for readiness, or perform a local-review-first check before requesting remote review.
---

# SD Full Check

Run this project-local skill for `sd-full-check` and `/sd:full-check` style
work. It is an optional but strongly recommended PR-readiness gate, not an
every-edit requirement.

The canonical implementation is:

```bash
bash scripts/sd-ai-command-pack-full-check.sh
```

## What It Does

The script runs:

1. `git diff --check` for unstaged changes.
2. `git diff --cached --check` for staged changes.
3. Repo-local review preflight through
   `SD_AI_COMMAND_PACK_FULL_CHECK_REVIEW_PREFLIGHT_COMMAND` when configured, or
   through `scripts/check-review-preflight.mjs` when that Node.js script exists.
4. Copied/generated tooling scope checks for Trellis/SD AI command pack, known
   repository-map files when present, and Trellis workspace journal/index
   changes.
5. Structural post-install audit through
   `scripts/sd-ai-command-pack-install-audit.py`, verifying that copied pack
   targets match `.sd-ai-command-pack/installed-targets.txt`.
6. Configurable PR-body scope checks for generated/tooling, automation, and
   CI/review diffs, plus any repo-added categories in
   `.sd-ai-command-pack/pr-body-scope.json`.
7. Current-diff CI classification reporting through
   `scripts/classify-ci-changes.sh` when that script exists.
8. Optional package-script checks when `package.json`, Node.js, and the
   selected package runner are available. The default script-name probe looks
   for common entries: `typecheck`, `lint`, `test:unit`, `test:integration`,
   `build`, and `test:e2e`.
9. Prism local review when `prism` is on `PATH` and Prism is not disabled.
10. Gito review only when explicitly enabled.

## Safety Rules

- Do not stage, commit, push, or edit files as part of this skill unless the
  user separately asks for fixes.
- Treat failures as evidence to report and fix in the normal Trellis
  implementation flow.
- Prism is optional by default because some environments lack provider
  credentials or valid provider/model configuration. Set
  `SD_AI_COMMAND_PACK_FULL_CHECK_PRISM=required` when missing Prism, authentication
  failure, or provider/model configuration failure should fail the command.
- Gito is opt-in because it may invoke `uvx`, use a cache outside the repo, and
  require LLM credentials or network access. Set `SD_AI_COMMAND_PACK_FULL_CHECK_GITO=1` to
  run it. Reports are written to `.build/review/gito` by default. When Gito
  reports a provider rate limit through an explicit HTTP 429 status such as
  `ClientError: 429`, the script retries with bounded exponential backoff.
- Prism and Gito review scans use the pack-managed standard exclusions for
  top-level AI/tooling/cache directories such as `.github/`, `.claude/`,
  `.codex/`, `.gemini/`, `.opencode/`, `.agents/`, `.build/`, `.git/`,
  `.pytest_cache/`, `.obsidian-kb/`, `.trellis/`, `.ruff_cache/`, `.venv/`,
  `.sd-ai-command-pack/`, and `node_modules/`.
- If the script reports skipped checks, include those skips in the final report.

## Useful Environment Variables

Common toggles are below. See `docs/SD_AI_COMMAND_PACK.md` → Configuration for
the full set: base-ref discovery, package runner and script list, Prism rules
and threshold, Gito output directory and rate-limit retry tuning, PR-body config
paths, and deprecated fallbacks.

- On macOS, prefer a Homebrew Python-backed virtualenv for repo-local Python
  checks, especially coverage runs. Apple/Xcode Python often lacks project dev
  dependencies and can try to write bytecode caches under protected
  `~/Library/Caches` paths.
- In sandboxed agent sessions, set writable temp-backed caches before rerunning
  checks that fail while creating tool state: `PYTHONPYCACHEPREFIX`,
  `UV_CACHE_DIR`, `UV_TOOL_DIR`, and `RUFF_CACHE_DIR`. The canonical
  copy/paste block lives in `docs/SD_AI_COMMAND_PACK.md` → Configuration.
- `SD_AI_COMMAND_PACK_FULL_CHECK_BASE_REF`: explicit base ref for branch review.
  When unset, the script discovers the remote default ref, then the branch
  upstream, then the first available remote ref.
- `SD_AI_COMMAND_PACK_FULL_CHECK_REVIEW_PREFLIGHT=0` / `=required`: skip the
  repo-local review preflight, or fail when no preflight command can run.
- `SD_AI_COMMAND_PACK_INSTALL_AUDIT=0`: skip the structural post-install audit.
- `SD_AI_COMMAND_PACK_INSTALL_AUDIT=required`: fail if the audit helper or
  `python3` is unavailable. By default those availability problems warn and
  continue.
- `SD_AI_COMMAND_PACK_FULL_CHECK_SKIP_PACKAGE_SCRIPTS=1`: skip all package-script
  checks.
- `SD_AI_COMMAND_PACK_FULL_CHECK_PRISM=0` / `=required`: skip Prism, or fail when
  Prism is missing, unauthenticated, or misconfigured.
- `SD_AI_COMMAND_PACK_FULL_CHECK_GITO=1`: run Gito review after Prism.
- `SD_AI_COMMAND_PACK_SCOPE_PR_BODY`: explicit PR body text for tooling/generated
  and PR-body scope checks in local or CI contexts where `gh pr view` should not
  be used.

## Expected Report

Report:

- Whether deterministic checks passed.
- Whether repo-local review preflight ran, skipped, or failed.
- Whether the post-install audit ran, skipped, or failed.
- Which package-script checks ran or were skipped.
- Whether Prism ran, skipped, found findings, lacked credentials, or had
  provider/model configuration failures.
- Whether Gito ran or was intentionally skipped, and where reports were written.
- Any command that failed and the smallest next fix.
