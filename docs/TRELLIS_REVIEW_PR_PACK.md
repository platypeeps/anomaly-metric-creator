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
in the shared skills and scripts. The refresh-specs wrapper runs the
Trellis-provided `trellis-update-spec` skill as-is, then performs the
architecture-overview check.
The continue and finish-work wrappers run Trellis' existing
`trellis-continue` and `trellis-finish-work` skills as-is.
The slash command namespace is `sd`, not `trellis`, so these pack-owned wrappers
do not collide with generated Trellis commands during future `trellis update`
runs. GitHub Copilot prompt files use `sd-<command>.prompt.md` for the same
reason.

## Recommended review loop

1. Iterate with the narrowest deterministic checks for the files you touched.
2. Use `/sd:continue` when resuming an in-progress Trellis task.
3. Run `/sd:full-check` or `bash scripts/trellis-full-check.sh` before PR
   readiness, before asking for remote review, and after substantial review
   fixes.
4. Fix deterministic failures first, then verify any Prism findings against the
   actual code before changing behavior.
5. Use `/sd:review-pr` for the PR loop. It should run the local
   full-check/Prism path before requesting GitHub Copilot review.
6. Request Copilot only when explicitly wanted or as a final remote pass.
7. Let `/sd:review-pr` reply to and resolve review threads as part of the
   normal loop once findings are fixed, rebutted with evidence, or confirmed
   already addressed.
8. Run `/sd:refresh-specs` when the work taught you a durable
   implementation contract or convention. It runs the existing update-spec skill
   and also checks whether an existing architectural overview needs to be
   updated.
9. Run `/sd:finish-work` when the coding session is complete and you need the
   Trellis finish-work skill's quality gate, archive, journal, and commit
   reminder behavior.
10. After the PR merges, run `/sd:housekeeping` to get back to the default
   branch, prune/delete the merged development stream, and see the condensed
   clean-state/anomaly report.
11. If `/sd:review-pr` sees the PR is already merged or becomes merged
   during the active session, it auto-dispatches housekeeping before the final
   report. This does not wake inactive sessions; it only runs when the active
   agent observes the merge.

## Commands

Use the platform-native command when available:

```bash
/sd:continue
/sd:finish-work
/sd:full-check
/sd:housekeeping
/sd:review-pr
/sd:refresh-specs
```

Use the script directly from any shell:

```bash
bash scripts/trellis-full-check.sh
bash scripts/trellis-housekeeping.sh
```

The full-check script runs `git diff --check`, `git diff --cached --check`,
detected package scripts, and local Prism review when Prism is available and
configured.

The continue and finish-work wrappers read `.agents/skills/trellis-continue/`
or `.agents/skills/trellis-finish-work/` and follow those Trellis-provided
skills without changing them.

The refresh-specs wrapper reads the existing Trellis `trellis-update-spec` skill
from the target repo, follows it as-is to update `.trellis/spec/`, and then
checks for an existing architectural overview. Candidate overview paths include
`ARCHITECTURE.md`, `ARCHITECTURE_OVERVIEW.md`, `docs/ARCHITECTURE.md`,
`docs/ARCHITECTURE_OVERVIEW.md`, and `.trellis/spec/**/architecture*.md`. If an
overview exists and the work changes high-level architecture such as packages,
command surfaces, data flow, persistence, external integrations, config/env, or
runtime/deployment topology, the wrapper updates it. Otherwise it leaves the
overview untouched and reports `not present` or `not warranted`.

The housekeeping script ends a single active development stream. On an open PR,
it first checks a strict auto-finalize gate: the working tree is clean, the
local branch head, remote branch head, and PR head all match, the PR is open and
not draft, the base is the default branch, merge state is clean, reported checks
are green, and there are no unresolved review threads. When that is true, it
runs `trellis-finalize`, pushes the journal commit back to the PR branch with a
best-effort `[skip ci]` marker, merges the PR, and then performs normal cleanup.
If the gate is not satisfied, it behaves as a post-merge cleanup command:
fetch/prune `origin`, confirm the current feature branch's PR is merged and the
local branch head matches that PR before deleting it, switch to the default
branch, fast-forward from `origin`, delete the merged local and remote branch,
and then report the expected clean state plus anomalies.

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
- `TRELLIS_FULL_CHECK_NPM_SCRIPTS`: space-separated package scripts to run.
- `TRELLIS_FULL_CHECK_SKIP_NPM=1`: skip package scripts.
- `TRELLIS_FULL_CHECK_PRISM=0`: skip Prism review.
- `TRELLIS_FULL_CHECK_PRISM=required`: fail if Prism is missing or cannot run.
- `TRELLIS_FULL_CHECK_PRISM_RULES`: explicit Prism rules file. Defaults to
  `.prism/rules.json` when present.
- `TRELLIS_FULL_CHECK_GITO=1`: opt into Gito review.
- `TRELLIS_FULL_CHECK_GITO_BASE_REF`: base ref for Gito review. Defaults to
  `TRELLIS_FULL_CHECK_BASE_REF`, then `origin/main`.
- `TRELLIS_FULL_CHECK_GITO_OUT_DIR`: output folder for Gito reports. Defaults
  to `.build/review/gito`.
- `TRELLIS_HOUSEKEEPING_FINALIZE_COMMAND`: executable name/path to run before
  auto-merging a ready PR. Arguments are not parsed; use a wrapper executable
  for custom argument lists. Defaults to `trellis-finalize`.
- `TRELLIS_HOUSEKEEPING_GITHUB_REPO`: explicit `owner/repo` slug when the
  selected remote URL cannot be parsed as a GitHub repository.
- `TRELLIS_HOUSEKEEPING_MERGE_STRATEGY`: auto-merge strategy: `merge`,
  `squash`, or `rebase`. Defaults to `merge`.

Prism is enabled by default when the executable is present. If Prism is missing
or credentials/config are unavailable, the script reports the skip and continues
unless `TRELLIS_FULL_CHECK_PRISM=required` is set.

Gito is opt-in because it can require `uvx`, cache access outside the repo,
network access, and configured LLM credentials. When enabled, Gito writes
reports to `.build/review/gito` by default so generated review artifacts do not
land at the repository root.

## CI cadence

Run the full-check locally before deliberately triggering expensive remote CI
or remote AI review. Repos can still use labels such as `full-ci`, manual
workflow dispatch, or ready-for-review transitions for GitHub-side expensive
checks.

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
to be preserved next to the overwritten files. An existing `.prism/rules.json`
that differs from the pack template is reported as `preserved` and is never
overwritten or reported as a conflict, so repo-specific Prism rules are not
replaced during a pack refresh.
Reinstalling the pack removes old pack-generated `/trellis:*` adapter files
when they still match the pack templates. Legacy adapter files with other
content are reported as conflicts unless `--force` is supplied; with `--force`,
they are removed while the `sd` replacement is installed.

## Troubleshooting

- Missing `/sd:continue` command: reinstall the pack and include the platform
  adapter for the tool you are using.
- Missing `/sd:finish-work` command: reinstall the pack and include the
  platform adapter for the tool you are using.
- Missing `/sd:full-check` command: reinstall the pack and include the
  platform adapter for the tool you are using.
- Missing `/sd:housekeeping` command: reinstall the pack and include the
  platform adapter for the tool you are using.
- Missing `/sd:refresh-specs` command: reinstall the pack and include the
  platform adapter for the tool you are using.
- `/sd:refresh-specs` reports a missing `trellis-update-spec` skill: run
  `trellis update` in the target repo so the Trellis-provided skill files are
  present, then retry the wrapper command.
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
