# Trellis review PR pack

This repo has the reusable Trellis review-cycle setup installed from
`platypeeps/trellis-review-pr-pack`.

## What is installed

- `.agents/skills/trellis-review-pr/SKILL.md`: local-review-first PR workflow.
- `.agents/skills/trellis-full-check/SKILL.md`: full local verification workflow.
- `.agents/skills/trellis-housekeeping/SKILL.md`: post-merge cleanup workflow.
- `scripts/trellis-full-check.sh`: canonical full-check script.
- `scripts/trellis-housekeeping.sh`: canonical post-merge housekeeping script.
- `.prism/rules.json`: default Prism review rules for repo-specific checks.
- Platform adapters under `.claude/`, `.gemini/`, `.github/prompts/`, and
  `.opencode/` when those platform folders exist.

The command and prompt files are entry points only. The workflow behavior lives
in the shared skills and scripts.

## Recommended review loop

1. Iterate with the narrowest deterministic checks for the files you touched.
2. Run `/trellis:full-check` or `bash scripts/trellis-full-check.sh` before PR
   readiness, before asking for remote review, and after substantial review
   fixes.
3. Fix deterministic failures first, then verify any Prism findings against the
   actual code before changing behavior.
4. Use `/trellis:review-pr` for the PR loop. It should run the local
   full-check/Prism path before requesting GitHub Copilot review.
5. Request Copilot only when explicitly wanted or as a final remote pass.
6. Let `/trellis:review-pr` reply to and resolve review threads as part of the
   normal loop once findings are fixed, rebutted with evidence, or confirmed
   already addressed.
7. After the PR merges, run `/trellis:housekeeping` to get back to the default
   branch, prune/delete the merged development stream, and see the condensed
   clean-state/anomaly report.
8. If `/trellis:review-pr` sees the PR is already merged or becomes merged
   during the active session, it auto-dispatches housekeeping before the final
   report. This does not wake inactive sessions; it only runs when the active
   agent observes the merge.

## Commands

Use the platform-native command when available:

```bash
/trellis:full-check
/trellis:housekeeping
/trellis:review-pr
```

Use the script directly from any shell:

```bash
bash scripts/trellis-full-check.sh
bash scripts/trellis-housekeeping.sh
```

The full-check script runs `git diff --check`, `git diff --cached --check`,
review-tooling shell syntax, Python/review guard scripts, `ruff check tests/`,
console-script smoke coverage, focused review-churn/server pytest coverage, and
local Prism review when Prism is available and configured. With the default
`TRELLIS_FULL_CHECK_LEVEL=full`, it also runs the same heavy/non-heavy pytest
split used by full CI. Use `TRELLIS_FULL_CHECK_LEVEL=quick` for a cheaper local
pass while iterating.

The housekeeping script performs the usual post-merge cleanup for a single
active development stream: fetch/prune `origin`, confirm the current feature
branch's PR is merged and the local branch head matches that PR before deleting
it, switch to the default branch, fast-forward from `origin`, delete the merged
local and remote branch, and then report the expected clean state plus
anomalies.

A clean housekeeping run should end with:

```text
==> Expected clean state
- branch: main
- working tree: clean
- main matches origin/main
- local branches: only main
- remote branches: only origin/HEAD and origin/main
- open PRs: none
- open issues: none
- Trellis active tasks: none

==> Anomalies
none
```

## Configuration

Common environment variables:

- `TRELLIS_FULL_CHECK_BASE_REF`: base ref for branch review. Defaults to
  `origin/main`.
- `TRELLIS_FULL_CHECK_LEVEL=quick`: skip only the full heavy/non-heavy pytest
  split while keeping deterministic lints, focused tests, Prism, and optional
  Gito.
- `TRELLIS_FULL_CHECK_LEVEL=full`: run the quick gate plus the full pytest
  split. This is the default.
- `TRELLIS_FULL_CHECK_PYTHON`: Python executable override.
- `TRELLIS_FULL_CHECK_PYTEST`: pytest command override.
- `TRELLIS_FULL_CHECK_RUFF`: ruff command override.
- `TRELLIS_FULL_CHECK_PRISM=0`: skip Prism review.
- `TRELLIS_FULL_CHECK_PRISM=required`: fail if Prism is missing or cannot run.
- `TRELLIS_FULL_CHECK_PRISM_RULES`: explicit Prism rules file. Defaults to
  `.prism/rules.json` when present.
- `TRELLIS_FULL_CHECK_GITO=1`: opt into Gito review.
- `TRELLIS_FULL_CHECK_GITO_BASE_REF`: base ref for Gito review. Defaults to
  `TRELLIS_FULL_CHECK_BASE_REF`, then `origin/main`.
- `TRELLIS_FULL_CHECK_GITO_OUT_DIR`: output folder for Gito reports. Defaults
  to `.build/review/gito`.

Prism is enabled by default when the executable is present. If Prism is missing
or credentials/config are unavailable, the script reports the skip and continues
unless `TRELLIS_FULL_CHECK_PRISM=required` is set.

Gito is opt-in because it can require `uvx`, cache access outside the repo,
network access, and configured LLM credentials. When enabled, Gito writes
reports to `.build/review/gito` by default so generated review artifacts do not
land at the repository root.

## CI cadence

Run the full-check locally before deliberately triggering expensive remote CI
or remote AI review. This repo's GitHub CI uses `scripts/classify_ci_changes.sh`
to choose between:

- `lightweight readiness` for docs/spec/agent/review-tooling-only changes.
- `quick test` for ordinary PR update churn that still touches app paths.
- the full Python 3.11/3.12 matrix for app-required diffs when a PR is opened
  or marked ready, the `full-ci` label is applied, workflow/dependency files
  change, workflow dispatch runs, or code lands on `main`.

Branch protection should continue to require the stable aggregate `test`
context rather than a lane-specific job name.

`tools/check_ci_review_contract.py` guards the named anchors in this cadence:
classifier outputs, selected CI lanes, CodeQL/Socket triggers, Dependabot
auto-merge safety, full-check integration, and the documentation/spec mentions
that keep future review sessions aligned.

## Housekeeping cadence

Run housekeeping after a PR is merged and any finish-work journal commit has
landed. If the command reports anomalies, treat them as the next manual action:
dirty files, an unmerged PR, extra branches, open PRs/issues, or remaining
Trellis tasks mean the repo is not yet in the expected clean state.

## Updating the pack

To refresh installed assets from the pack checkout:

```bash
python3 /path/to/trellis-review-pr-pack/install.py /path/to/target/repo --force
```

Use `--dry-run` first when you want to inspect which files would change.
Use `--backup` with `--force` if the target repo may have local edits that need
to be preserved next to the overwritten files.

## Troubleshooting

- Missing `/trellis:full-check` command: reinstall the pack and include the
  platform adapter for the tool you are using.
- Missing `/trellis:housekeeping` command: reinstall the pack and include the
  platform adapter for the tool you are using.
- `scripts/trellis-full-check.sh` is missing: reinstall the pack; every target
  repo should receive the shared script.
- `scripts/trellis-housekeeping.sh` is missing: reinstall the pack; every
  target repo should receive the shared script.
- Prism authentication/config failure: configure Prism locally, set
  `TRELLIS_FULL_CHECK_PRISM=0` to skip it, or set
  `TRELLIS_FULL_CHECK_PRISM=required` when review must be mandatory.
- Gito fails due to cache or network sandboxing: run from an environment with
  the needed access, or leave `TRELLIS_FULL_CHECK_GITO` unset.
- Root-level `code-review-report.*` files appear after manual Gito runs: move
  or delete them, then rerun through `TRELLIS_FULL_CHECK_GITO=1
  bash scripts/trellis-full-check.sh` so reports go under `.build/review/gito`.
- Stale generated cache causes type or build failures: clear the repo-specific
  generated cache and rerun the deterministic check that failed.
