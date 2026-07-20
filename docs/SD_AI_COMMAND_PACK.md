# SD AI command pack

This repo has the reusable SD AI command setup installed from
`platypeeps/sd-ai-command-pack`.

This pack assumes the repo is already initialized with Trellis. If another repo
is missing `trellis` or `.trellis/config.yaml`, follow the official
[Trellis install and first-task instructions](https://docs.trytrellis.app/start/install-and-first-task)
first; they cover `npm install -g @mindfoldhq/trellis@latest` and
`trellis init`.

Quick links:

- [What is installed](#what-is-installed)
- [Recommended review loop](#recommended-review-loop)
- [Commands](#commands)
- [Configuration](#configuration)
- [Updating the pack](#updating-the-pack)
- [Troubleshooting](#troubleshooting)

## What is installed

- `.agents/skills/sd-help/SKILL.md`: read-only SD command discovery,
  comparison, explanation, and recommendation workflow.
- `.agents/skills/sd-help/references/command-catalog.md`: generated command
  families, descriptions, release version, and bundled availability policy.
- `.agents/skills/sd-help/references/examples.md`: authored examples for common
  delivery goals and command overlaps.
- `.agents/skills/sd-status/SKILL.md`: read-only local repository and
  configured fleet status reporting.
- `.agents/skills/sd-start/SKILL.md`: Codex-visible Trellis start wrapper.
- `.agents/skills/sd-continue/SKILL.md`: Codex-visible Trellis continue wrapper.
- `.agents/skills/sd-finish-work/SKILL.md`: Codex-visible Trellis finish-work wrapper.
- `.agents/skills/sd-create-pr/SKILL.md`: spec-refresh, commit, push, PR
  creation/reuse, and PR-review orchestration workflow; custom Markdown bodies
  are materialized literally and passed to GitHub CLI with `--body-file`.
- `.agents/skills/sd-work-backlog/SKILL.md`: sequential Trellis backlog work
  loop and canonical resumable controller for planning through clean merge.
- `.agents/skills/sd-work-backlog/references/autonomous-loop.md`: shared
  planning-quality and artifact-exit contract used by both work selectors.
- `.agents/skills/sd-work-designs/SKILL.md`: thin `needs-design` selector for
  the canonical full-cycle controller, with `until=design` planning-only mode.
- `.agents/skills/sd-review-pr/SKILL.md`: deterministic local gate plus remote
  PR review workflow.
- `.agents/skills/sd-review-local/SKILL.md`: local review provider fix loop.
- `.agents/skills/sd-review-learnings/SKILL.md`: review feedback learning
  capture workflow.
- `.agents/skills/sd-audit-repo/SKILL.md`: formal multi-dimension repository
  audit orchestration workflow.
- `.agents/skills/sd-audit-repo/charters/`: fifteen per-dimension reviewer
  charters the audit dispatches; a single shared copy used by every platform
  copy of the skill.
- `.agents/skills/sd-watch-pr/SKILL.md`: PR settle watcher with gated
  housekeeping handoff.
- `.agents/skills/sd-fix-ci/SKILL.md`: red-CI triage and fix loop.
- `.agents/skills/sd-update-deps/SKILL.md`: dependency PR batch triage
  workflow.
- `.agents/skills/sd-test-gaps/SKILL.md`: coverage-driven test authoring
  loop.
- `.agents/skills/sd-retro/SKILL.md`: debug retrospective capture workflow.
- `.agents/skills/sd-ship/SKILL.md`: composite publish-to-merge orchestrator
  chaining create-pr, review-pr, watch-pr, and housekeeping.
- `.agents/skills/sd-full-check/SKILL.md`: full local verification workflow.
- `.agents/skills/sd-housekeeping/SKILL.md`: post-merge cleanup workflow.
- `.agents/skills/sd-update-spec/SKILL.md`: Trellis update-spec workflow plus
  pack-managed repository knowledge refresh.
- `scripts/sd-ai-command-pack-full-check.sh`: canonical full-check script.
- `scripts/sd-ai-command-pack-shell-lib.sh`: shared Bash helpers sourced by
  the full-check, review-local, and review-scope scripts.
- `scripts/sd-ai-command-pack-toolchain.sh`: non-mutating toolchain doctor and
  deterministic Python resolver used by SD workflows before dependency-sensitive
  checks.
- `scripts/sd-ai-command-pack-housekeeping.sh`: canonical post-merge housekeeping script.
- `scripts/sd-ai-command-pack-status.py`: read-only local/fleet status collector
  and schema-versioned JSON reporter used by housekeeping final verification.
- `scripts/sd-ai-command-pack-work-loop.py`: standard-library user-local loop
  ledger, lock, focus ranking, transition, reconciliation, and resume helper.
- `scripts/sd_ai_command_pack_fleet_lib.py`: shared fleet-manifest validation,
  machine-profile resolution, checkout override, and release-ledger contracts.
- `scripts/sd-ai-command-pack-record-session.py`: one-shot session journal
  recorder — wraps Trellis' `add_session.py`, resolving commit subjects
  from git (failing fast on unknown hashes), filling the Main Changes and
  Testing sections from `--change`/`--test` flags, and refusing to commit
  an entry that still contains template placeholders. If a previous run
  appended the entry but failed while staging or committing, a retry reuses
  the modified latest session instead of appending a duplicate.
- `scripts/sd-ai-command-pack-review-scope.sh`: copied/generated file scope
  preflight for mixed PRs.
- `scripts/sd-ai-command-pack-review-preflight.mjs`: generic dependency-free
  review preflight for copied/generated disclosure, documentation path hygiene,
  Trellis journal consistency, npm override drift, and large diff warnings.
- `scripts/sd-ai-command-pack-review-local.sh`: local Prism/Gito and configured
  review-tool runner for the review-local loop, including its `all`
  full-codebase mode.
- `scripts/sd-ai-command-pack-review-learnings.py`: local review feedback
  pattern scanner and managed learning-block updater.
- `scripts/sd-ai-command-pack-install-audit.py`: structural post-install audit
  for missing installed targets and unlisted pack-like files.
- `scripts/sd-ai-command-pack-pr-body-scope.py`: configurable PR-body scope
  preflight for broad behavior-changing diffs.
- `scripts/sd-ai-command-pack-update-spec-kb.py`: Obsidian KB copy-folder
  refresh helper for the update-spec workflow.
- `.sd-ai-command-pack/installed-targets.txt`: generated list of pack targets
  installed in this repo, used by the review-scope preflight. Normal shared
  installs should commit this file with the other pack-owned files; `--local-only`
  installs keep it in the clone-local exclude list instead.
- `.prism/rules.json`: default Prism review rules for repo-specific checks.
- `.prism/rules.schema.json`: JSON Schema for the Prism rules file, for editor
  validation and tooling.
- `.gito/config.toml`: default Gito project configuration for direct or
  pack-run local reviews. Provider credentials and model selection stay in
  `~/.gito/.env` or process environment variables.
- `.gito/sd-ai-command-pack.env`: pack-owned Gito environment defaults consumed
  by the local review runners. It sets `MAX_CONCURRENT_TASKS=4` unless the
  caller already provided a value.
- Platform adapters are installed only for detected active Trellis platforms:
  the corresponding platform folder must contain Trellis command, hook, skill,
  agent, or platform-library markers. A plain `.github` directory for Actions
  is not enough. Use `--platform <name>` or `--all` to force a platform adapter
  even when no active marker is present.
  ZCode Trellis agents are detected at `.zcode/agents/`; the legacy
  `.zcode/cli/agents/` path is still treated as copied Trellis surface during
  the transition for review scope and local-only excludes.

The command and prompt files are entry points only. The workflow behavior lives
in the shared skills and scripts. The update-spec workflow runs the
Trellis-provided `trellis-update-spec` skill as-is, refreshes repo-owned
repospec artifacts through existing maintenance infrastructure when available,
and then performs the architecture-overview check.
Codex exposes the pack entry points as skills named `sd-help`, `sd-status`,
`sd-start`, `sd-continue`,
`sd-finish-work`, `sd-create-pr`, `sd-work-backlog`, `sd-work-designs`,
`sd-full-check`, `sd-housekeeping`, `sd-review-pr`, `sd-review-local`,
`sd-review-learnings`, `sd-audit-repo`, `sd-ship`,
`sd-watch-pr`, `sd-fix-ci`, `sd-update-deps`,
`sd-test-gaps`, `sd-retro`, and `sd-update-spec`; type
`/sd` in Codex command completion or invoke them with
`$sd-review-pr`-style skill mentions.
The start, continue, and finish-work wrappers run Trellis' existing
`trellis-start`, `trellis-continue`, and `trellis-finish-work` skills as-is.
On Claude Code — where Trellis ships a SessionStart hook instead of a
`trellis-start` skill — the start wrapper derives the same session context
from `.trellis/scripts/get_context.py` directly, and the continue and
finish-work wrappers accept the installed `trellis:continue` and
`trellis:finish-work` command names as valid resolutions.
The slash command namespace is `sd`, not `trellis`, so these pack-owned wrappers
do not collide with generated Trellis commands during future `trellis update`
runs. Command-capable adapters expose either namespaced `sd/<command>` files or
flat `sd-<command>` files, matching the platform convention Trellis uses for
that tool. Skill-only adapters install the same `sd-*` skills into the
platform's native skill root.
For Gemini CLI, the project command files intentionally live under
`.gemini/commands/sd/`; Gemini maps a file such as
`.gemini/commands/sd/review-pr.toml` to `/sd:review-pr` and shows the TOML
`description` in `/help`. If the commands were installed while Gemini CLI was
already running, use `/commands reload`, then `/commands list` to confirm the
loaded project command files.

## Recommended review loop

When you do not know which workflow owns the next step, start with `sd-help`.
It inspects the bundled catalog and current skill inventory, recommends the
smallest fitting command, and returns a copy-ready invocation without running
it. Use a separate request to execute the recommendation.

1. Iterate with the narrowest deterministic checks for the files you touched.
2. Use the continue command when resuming an in-progress Trellis task.
3. Run the full-check command or `bash scripts/sd-ai-command-pack-full-check.sh`
   before PR readiness, before asking for remote review, and after substantial
   review fixes.
4. Fix deterministic failures first, then verify findings from any available
   local review provider against the actual code before changing behavior.
5. Use the review-local command when you want a current-diff local Prism/Gito
   or configured review-tool loop before involving a remote reviewer. It asks
   which findings to fix and repeats until no items are selected.
6. Use the review-local command with the `all` argument when you want the
   same local fix loop run
   against the entire checked-out repository rather than just recent diffs.
7. Use the create-pr command when you want the publishing wrapper: it runs
   `sd-update-spec`, stages only intended files, commits and pushes the feature
   branch when needed, creates or reuses the branch PR, and then enters the
   review-pr loop.
8. Use the work-backlog command when you want to work through existing Trellis
   tasks sequentially. It selects one implementation-ready task, completes it
   through create-pr, review-pr, housekeeping, and an extra housekeeping
   verification, then addresses or records follow-ups and learnings before
   selecting the next task.
9. Use the work-designs command when existing Trellis tasks have real PRDs but
   still need `design.md` or `implement.md`. It adds implementation proposals
   and execution guidance to those task artifacts, parks tasks that need user
   input, and reports links to every planning document it created or updated.
10. Use the review-pr command for an existing PR loop. It should run the deterministic
   local full-check path with Prism/Gito disabled before requesting remote
   review, then disposition any first-review boundary-risk, authored-source
   size, or multi-task scope advisory before round one. Run `sd-full-check` or
   `sd-review-local` (optionally with `all`) explicitly when you want
   Prism/Gito.
11. Request the configured remote reviewer, defaulting to GitHub Copilot, after
   a clean local pass and again after every pushed review-fix commit made
   during the loop, unless the user explicitly asked for local-only review.
12. Let the review-pr command reply to and resolve review threads as part of the
   normal loop once findings are fixed, rebutted with evidence, or confirmed
   already addressed.
13. Use the review-learnings command when review comments repeat across PRs and
   you want to capture repo-specific preventive guidance. The review-pr loop
   automatically attempts one read-only, PR-scoped learning pass after the
   overall cycle is clean; it never runs the learning pass after each round.
14. Run the update-spec command when the work taught you a durable
   implementation contract or convention. It runs the existing update-spec skill
   and also checks whether an existing architectural overview needs to be
   updated.
15. Run the finish-work command when the coding session is complete and you need
   the Trellis finish-work skill's quality gate, archive, journal, and commit
   reminder behavior.
16. After the PR merges, run the housekeeping command to get back to the default
   branch, prune/delete the merged development stream, and see the condensed
   clean-state/anomaly report.
17. If the review-pr command sees the PR is already merged or becomes merged
   while the command is running, it stops the review loop and runs post-merge
   housekeeping before the final report. This does not wake inactive sessions;
   it only runs when the active agent observes the merge.

The default remote review request uses GitHub Copilot's documented `@copilot`
CLI alias and matches resulting activity from
`copilot-pull-request-reviewer[bot]`. A successful request is only an attempt;
the loop waits for author-matched activity on the requested head before it
counts the review as materialized. Target repos can override it with
`SD_AI_COMMAND_PACK_REVIEW_PR_REMOTE_REVIEWER`,
`SD_AI_COMMAND_PACK_REVIEW_PR_REMOTE_REVIEWER_LABEL`,
`SD_AI_COMMAND_PACK_REVIEW_PR_REMOTE_AUTHOR_MATCH`,
`SD_AI_COMMAND_PACK_REVIEW_PR_REMOTE_REQUEST_COMMAND`, and
`SD_AI_COMMAND_PACK_REVIEW_PR_REMOTE_ROUND_LIMIT`. The bounded materialization
wait uses `SD_AI_COMMAND_PACK_REVIEW_PR_REMOTE_SETTLE_POLLS`. The round limit
defaults to five configured remote-review requests before the command asks
whether to keep going. Once the overall loop meets its stop conditions,
review-pr runs
`sd-ai-command-pack-review-learnings.py --github-pr <number> --dry-run`
exactly once and reports any preventive follow-up without reopening the clean
review cycle.

The create-pr wrapper honors `SD_AI_COMMAND_PACK_CREATE_PR_BASE` for a base
branch override, `SD_AI_COMMAND_PACK_CREATE_PR_COMMIT_MESSAGE` when it creates
a commit without a user-provided message, and
`SD_AI_COMMAND_PACK_CREATE_PR_DRAFT=1` when the PR should start as a draft.
It still delegates the actual review loop to review-pr after PR creation or
reuse.

The work-backlog command is the canonical resumable autonomous controller. It
inventories live Trellis state, optionally applies ordered `focus=` preference
bands or strict `focus-only=` filtering, completes missing design artifacts,
implements and validates exactly one task, then delegates the entire
publish/review/watch/finish/merge/cleanup lifecycle to `sd-ship until=merge`.
The nested ship result returns to the controller, which processes follow-ups,
verifies a clean default branch, records compact counters, and re-inventories.
Bare text is one preferred focus expression, so `sd-work-backlog CI pipeline`
is equivalent to `focus="CI pipeline"`. Structured selectors include
`priority:`, `package:`, `task:`, `status:`, and `scope:`.

The loop persists only coordination metadata in an atomic user-local ledger;
it stores no tokens, raw logs, PR bodies, or review payloads. It uses one lock
per repository, reconciles every phase with Trellis/Git/GitHub evidence, and
classifies context health as green, amber, or red. Near a clean boundary around
ten iterations it offers a non-blocking stop, but continues unless the user
asks to stop. Task-local pre-mutation blockers can be parked; contradictory or
dirty repository-wide state stops safely. Unavoidable user input gets one
recommended question and a wait of up to 15 minutes when supported.

Lifecycle phases and mutable evidence are separate contracts. `transition`
advances a phase, while the helper's `evidence` subcommand records verified
same-phase commit, PR, review-fix, finish-work, and merge facts atomically.
Task and base branch are stable iteration identity; commit ancestry, PR
identity, and the final feature-to-base branch switch are validated locally.
Verified same-phase reconciliation uses the same rules and clears obsolete
recovery checkpoints instead of routing through a synthetic checkpoint phase.

The work-designs command is a thin `needs-design` selector for that same
controller. Its default now carries selected tasks from planning through a
green merge. `sd-work-designs until=design` preserves planning-only behavior
and ends with numbered links to every planning artifact it created or updated.
Focus composes with the selector rather than replacing it.

The help command is a read-only orientation surface. Use bare help for a
compact lifecycle tour, `all` for the complete catalog, an exact command for
an explanation, two or more commands for a comparison, or an ordinary-language
goal for a recommendation. It distinguishes commands available in the current
session from bundled-but-undiscoverable, source-checkout-only, and external
skills. It reports observed version information honestly and never executes,
delegates to, or mutates state on behalf of the selected command.

## Commands

Use the platform-native command when available.

Claude Code and Gemini CLI:

```bash
/sd:help
/sd:status
/sd:start
/sd:continue
/sd:finish-work
/sd:create-pr
/sd:work-backlog
/sd:work-designs
/sd:full-check
/sd:housekeeping
/sd:review-pr
/sd:review-local
/sd:ship
/sd:review-learnings
/sd:audit-repo
/sd:watch-pr
/sd:fix-ci
/sd:update-deps
/sd:test-gaps
/sd:retro
/sd:update-spec
```

Cursor command files, GitHub Copilot prompt files, OpenCode command files,
Qoder commands, Trae commands, Pi prompts, workflow adapters, and Codex skills:

```bash
/sd-help
/sd-status
/sd-start
/sd-continue
/sd-finish-work
/sd-create-pr
/sd-work-backlog
/sd-work-designs
/sd-full-check
/sd-housekeeping
/sd-review-pr
/sd-review-local
/sd-ship
/sd-review-learnings
/sd-audit-repo
/sd-watch-pr
/sd-fix-ci
/sd-update-deps
/sd-test-gaps
/sd-retro
/sd-update-spec
```

In Codex, you can also invoke the enabled skills explicitly with
`$sd-review-pr`-style skill mentions.

Common help requests:

```text
/sd:help
/sd:help review-pr
/sd:help "compare sd-create-pr and sd-ship"
/sd:help "I need to fix failing CI"
/sd:help all
```

Use the equivalent native adapter form on other platforms. A help response may
recommend one command or a bounded workflow, but execution always requires a
separate explicit request.

CodeBuddy, Factory Droid, and ZCode use namespaced `sd/<command>` command
folders. Kiro and Reasonix expose the same entries as native `sd-*` skills.

For GitHub installs, the pack also seeds `.github/PULL_REQUEST_TEMPLATE.md`
with Summary, Test plan, and Pre-PR checklist sections that prompt for the
explicit scope sections the PR-body scope checks look for. A repo's existing
customized template is always preserved, never overwritten.

For GitHub Copilot, the installer also creates or updates a managed
`sd-ai-command-pack` block in `.github/copilot-instructions.md`. Existing
repo-specific Copilot instructions are preserved; only the marked pack block is
replaced on future installs. The block tells Copilot to ignore copied-in
Trellis runtime files and copied-in `sd-ai-command-pack` files unless a PR is
explicitly about those integrations. For mixed PRs, it tells Copilot to spend
review budget on app behavior, data contracts, specs, tests, operator docs, and
repo-owned scripts, and to comment on copied Trellis/SD-pack files only for
obvious syntax breakage, secret leakage, or a direct mismatch with the PR's
stated tooling goal. It explicitly tells Copilot not to leave line comments on
wording, spelling, links, formatting, examples, or implementation details inside
copied Trellis skills/agents/commands or copied SD command-pack
skills/prompts/scripts/docs/rules. Original Trellis-owned runtime/template
copies are also out of scope for local edits and line-by-line review; if a
<!-- narrow-globs: skip - optional Trellis-owned payload locations may not exist in every repo. -->
change appears needed in `.trellis/scripts/**`, `.trellis/agents/**`, or
platform `trellis-*` payloads, Copilot should leave one handoff comment that
sends the finding back to the sd-ai-command-pack source session instead of
reviewing the copied file. It also asks Copilot to group duplicate root causes
and point to deterministic local checks when they already cover a repeated
issue class.

Pasteable handoff for those findings:

```text
Handoff for sd-ai-command-pack source session:
A change appears needed in original Trellis-owned runtime/template files,
which should not be edited in the consumer repo copy.
Affected file(s): <paths>
Desired behavior: <short behavior>
Evidence/repro: <commands, review finding, or failure>
Please decide whether this belongs in an sd-ai-command-pack wrapper/template,
a pack-owned guard, or an upstream Trellis change, then implement the durable
source-owned fix.
```

Use the script directly from any shell:

```bash
bash scripts/sd-ai-command-pack-full-check.sh
bash scripts/sd-ai-command-pack-review-local.sh
bash scripts/sd-ai-command-pack-review-local.sh --full-codebase
bash scripts/sd-ai-command-pack-housekeeping.sh
bash scripts/sd-ai-command-pack-toolchain.sh run-python -- scripts/sd-ai-command-pack-status.py
bash scripts/sd-ai-command-pack-toolchain.sh run-python -- scripts/sd-ai-command-pack-status.py fleet --json
bash scripts/sd-ai-command-pack-toolchain.sh run-python -- \
  scripts/sd-ai-command-pack-review-learnings.py --include-working-tree
```

`sd-status` is the read-only delivery snapshot for a repository. It reports the
branch, staged/unstaged/untracked counts, Git stash count, upstream ahead/behind state, default
and local/remote branches, installed SD pack and Trellis versions, relevant PR,
open PRs/issues, current/in-progress/planned Trellis work, completed tasks
stranded outside the Trellis archive, user-local autonomous loop state,
anomalies, and numbered next steps. Loop state includes run ID,
mode/selector/focus, iteration, phase, task/PR, counters, heartbeat, context
health, checkpoint, lock, and stop reason. Reading it never refreshes the
ledger or lock. The status adapter accepts terminal `none`, `invalid`, and
`unavailable` snapshots plus complete `active`, `paused`, `stopped`, and
`completed` run snapshots. Missing, unsupported, or incomplete helper results
become bounded `invalid` anomalies without echoing helper-controlled values. A
positional path selects another checkout, so
`sd-status /path/to/repo` is equivalent to
`sd-status --repo /path/to/repo`.
`--no-network` suppresses GitHub calls and `--json` emits schema version 1. Ordinary runs do
not fetch and label ref-derived values `cached`. Relevant-PR review totals use
GitHub's GraphQL `reviews.totalCount`, so repositories with more than one REST
page of review events are reported accurately without fetching every review.

The optional positional `fleet` mode works from any installed checkout. It
resolves the canonical fleet manifest from `--fleet-manifest`,
`SD_AI_COMMAND_PACK_FLEET_MANIFEST`, the machine-local fleet profile, or the
canonical source checkout, in that order. It preserves rollout priority,
reports missing checkouts, compares installed versions to the source manifest
version, and returns one bounded row per fleet member. A dirty, stale, missing,
behind, or diverged repository is advisory in ordinary status; the command
remains read-only and exits zero after producing the report. Invalid
repositories and malformed, missing, or stale fleet configuration exit
nonzero. This repository-status command is separate from `install.py --status`,
which compares one target's installed payload to the current pack.

The full-check script runs `git diff --check`, `git diff --cached --check`,
review preflight through `scripts/sd-ai-command-pack-review-preflight.mjs`, any
configured `SD_AI_COMMAND_PACK_FULL_CHECK_REVIEW_PREFLIGHT_COMMAND`, and the
legacy repo-local `scripts/check-review-preflight.mjs` when present. It then
runs the post-install audit, the tooling/generated file scope preflight, the
PR-body scope preflight, current-diff CI classification when
`scripts/classify-ci-changes.sh` exists, optional package-script checks when a
`package.json`, Node.js, and the selected package runner are available, and
local Prism review when `prism` is available and configured. For target repos
that provide a CI classifier, prefer `scripts/classify-ci-changes.sh` with
support for `-- changed-file ...`; the full-check script also tolerates legacy
`scripts/classify_ci_changes.sh` by passing a temp changed-files list directly.
The install audit checks
`.sd-ai-command-pack/installed-targets.txt` for missing targets, reports
pack-like files that are not listed in the installed-targets snapshot, and warns
when legacy pack names such as `trellis-full-check`, `trellis-housekeeping`,
`trellis-review-pr`, or `sd-refresh-specs` still appear in target files.
Generated `docs/repomix-map.md` aggregates are excluded from that reference
scan because their source documents are scanned directly.
The audit also ignores stale provenance claims for shared or generated targets
that current installers never vouch, including the managed `.gitignore`.
Current installs also write `.sd-ai-command-pack/manifest.json`; the audit uses
that manifest snapshot to derive the expected installed target set for shared
files and detected platforms. Fleet or scripted refreshes should pass explicit
platforms, for example `--expected-platform claude --expected-platform gemini`,
so a selected-platform file cannot disappear from disk, receipts, and
provenance without the audit failing.
Missing targets that are gitignored in the current checkout downgrade to
warnings with a reinstall hint, and the installer keeps receipt entries
(reported as `kept-in-receipt`) for platforms skipped only because their
markers or anchors are gitignored here; remove a platform intentionally by
deleting its files and its receipt lines.
Two receipt policies for gitignored local-only adapters are supported and
both pass the audit: record-and-warn (the installer default — entries stay
in the receipt and absent files warn) and exclude-and-warn (repo guards
strip the entries — present-but-unlisted gitignored files warn instead of
failing). Hand-edited receipt entries with Windows-style separators are
normalized before checking. The installer also writes
`.sd-ai-command-pack/provenance.json` with the installed payload version and
`sha256` hashes of installed pack files (user-tunable files are never
vouched); the audit fails when a vouched file's content drifts from the
recorded pack content, when a vouched file is missing while not gitignored,
or when a vouched path (or the provenance file itself) is a symlink or other
non-regular node, so the "reviewed upstream" exemption for vendored pack
files is a checkable claim. The source checkout's current manifest version
can intentionally be newer than the provenance version in a target repo when
the newer release did not change installed payload bytes; a passing audit
reports the installed payload provenance version and confirms the vouched
hashes still match.
The copied/generated scope preflight reads
`.sd-ai-command-pack/installed-targets.txt`, reports changed pack/Trellis
runtime files, known repository-map files when present, and Trellis workspace
journal/index files as integration-only review surface. When the GitHub CLI can
resolve a current PR, it checks that the PR body includes a
`Tooling/generated scope:` section before review cycles spend attention on
copied or generated surfaces. Markdown headings without the colon, such as
`## Tooling/generated scope`, are accepted too. In CI or local preflights where
`gh pr view` should not run, pass the PR body through
`SD_AI_COMMAND_PACK_SCOPE_PR_BODY`.

The review preflight is intentionally generic and safe to run without project
dependencies. It checks for duplicate npm override sources of truth, changed
copied Trellis or SD command-pack surfaces without companion repo-owned
integration context, personal absolute paths in docs/prompts/specs, missing
repo path references in docs/prompts/specs, completed Trellis journal
placeholder or journal/index commit drift, generated `_example` seed rows in
changed task context after a task enters implementation, completes, or is
archived, edits to historical
journal sessions relative to the review base, and large diffs that are likely
to skip remote AI review. It also emits soft first-review warnings when changed
code adds parser/structured-input, subprocess, filesystem/path, environment or
global-state, or digest/integrity behavior. The warning names a conservative
boundary-test matrix for author disposition before remote review. Diff sizing
uses the complete review-base-to-working-tree state plus untracked files; large
untracked files are counted as large without reading the entire file. The same
byte limit bounds the first-review boundary-risk content scan; skipped
oversized untracked code files are named in an explicit warning. Its
authored-source threshold excludes installed pack/Trellis mirrors, Trellis task
and workspace records, and known generated reports. A separate warning calls
out changes spanning more than one Trellis task directory. The task-context
check inspects `implement.jsonl` and
`check.jsonl`; a changed qualifying `task.json` also checks both sibling files.
Planning scaffolds, untouched legacy archives, and
symlinked context files are skipped. Journal history is append-only: newly
added/current sessions remain editable, but an older session must be restored
and the intended current session edited by its explicit `## Session <n>:`
heading. A separate repository-wide task-location check fails when a direct
child of `.trellis/tasks/` has completed status, names the offending record,
and provides the Trellis archive command; archived, non-completed, and
symlinked task entries are ignored. Target repos can tune roots,
path-reference prefixes, integration paths, optional paths, copied-template
paths, and the `diffSizeWarningLines`, `largeFileWarningLines`,
`sourceReviewWarningLines`, and `untrackedFileReadLimitBytes` warning thresholds
with `.sd-ai-command-pack/review-preflight.json`. Repos that intentionally
document service-user paths under `/home/<user>/` can add those service users to
`allowedLinuxHomeUsers` in that config. The script requires Node 16.9 or newer
and scans regular documentation files only; symlinked docs are skipped
intentionally so local/generated links do not expand outside the repository.
Generated GitHub paths and path-like comment snippets inside the complete
`sd-review-learnings` managed block are remote provenance, so the local path
validator masks only that block while preserving line numbers. Human-authored
content around the block and incomplete marker pairs remain checked normally.

The review-local script is intentionally tool-stack aware. In this pack version
its runner-owned default toolset is Prism and Gito. Its default scope is
local-files-first: it reviews
unstaged, staged, and untracked local files when present; if there are no local
changed files, it reviews the current branch diff from the configured base. Pass
tool names as arguments, set
`SD_AI_COMMAND_PACK_REVIEW_LOCAL_TOOLS`, or configure a third-party tool with
`SD_AI_COMMAND_PACK_REVIEW_LOCAL_<TOOL>_COMMAND`. The review-local command uses
that script output to ask which findings to fix, applies only selected fixes,
and repeats the same tool stack until the user selects no more items.

Use `bash scripts/sd-ai-command-pack-review-local.sh --full-codebase` or the
review-local command with the `all` argument when you want a full
checked-out repository review.
The older `--all` flag remains a supported scope alias.
In that mode, Prism runs `prism review codebase`; Gito normally runs
`gito review --all --path <absolute-repo-root>` and writes to
`.build/review/gito-all` by default with an include filter built from existing
tracked files, so branch-diff deletions are not reviewed as deleted diff paths.
Prism and Gito scans use the pack's managed standard exclusions for top-level
AI/tooling/cache directories:

```text
.agent/
.agents/
.claude/
.codex/
.codebuddy/
.cursor/
.devin/
.factory/
.gemini/
.github/
.kiro/
.kilocode/
.opencode/
.pi/
.qoder/
.reasonix/
.trae/
.zcode/
.build/
.git/
.pytest_cache/
.obsidian-kb/
.trellis/
.ruff_cache/
.venv/
.sd-ai-command-pack/
node_modules/
```

For `uvx`-based Gito wrappers, the full-check and review-local runners set
`UV_CACHE_DIR` and `UV_TOOL_DIR` to writable temp directories when they are
unset. When Gito reports provider rate limiting through an explicit
HTTP 429 status such as `ClientError: 429` or a 429 slow-down response, the
runner retries with bounded exponential backoff. Tune attempts and delays with
`SD_AI_COMMAND_PACK_REVIEW_LOCAL_GITO_MAX_ATTEMPTS`,
`SD_AI_COMMAND_PACK_REVIEW_LOCAL_GITO_RETRY_DELAY_SECONDS`, and
`SD_AI_COMMAND_PACK_REVIEW_LOCAL_GITO_RETRY_MAX_DELAY_SECONDS`. If Prism
full-codebase review returns an empty chunk response, the runner retries in
tracked-file batches and splits a failed batch into individual paths when
needed. Configure third-party full-codebase scans with
`SD_AI_COMMAND_PACK_REVIEW_LOCAL_ALL_<TOOL>_COMMAND`; if that is not set, the
runner falls back to `SD_AI_COMMAND_PACK_REVIEW_LOCAL_<TOOL>_COMMAND`.

The PR-body scope preflight is generic and config-driven. By default it checks
pack/Trellis generated files, housekeeping automation files, and CI/review
tooling files for matching `Tooling/generated scope:`, `Automation scope:`, or
`CI/review scope:` sections when a PR body is provided. Target repos can add
runtime, docs, or other categories by committing
`.sd-ai-command-pack/pr-body-scope.json`:
Each rule accepts `label`, `headings`, `patterns`, and optional
`include_installed_targets`. Set `include_installed_targets` to `true` when the
generated `.sd-ai-command-pack/installed-targets.txt` paths should be
classified under that rule.

For mixed command-pack or generated-map updates that also touch CI/review
automation, include both sections:

```markdown
Tooling/generated scope:
- Copied SD command-pack files or generated repository maps were refreshed.
- Review focus should be integration wiring, provenance, secrets, and docs
  accuracy.

CI/review scope:
- CI, review preflight, or command-pack adapter changes were made intentionally.
- Review focus should be command invocation, env propagation, and whether local
  checks still exercise the expected paths.
```

```json
{
  "rules": [
    {
      "label": "Runtime/server scope",
      "headings": ["Runtime/server scope:", "Runtime scope:"],
      "patterns": ["src/**", "apps/**"],
      "include_installed_targets": false
    }
  ]
}
```

The start, continue, and finish-work wrappers each invoke the matching
Trellis-provided skill — `.agents/skills/trellis-start/`,
`.agents/skills/trellis-continue/`, or `.agents/skills/trellis-finish-work/`
respectively — and use it without changing its behavior. The Claude Code
adapters are the exception: start derives the session context from
`.trellis/scripts/get_context.py` (Claude's Trellis layout ships a
SessionStart hook, not a `trellis-start` skill), and continue/finish-work
accept the `trellis:continue`/`trellis:finish-work` command form.

The update-spec command does more than update `.trellis/spec/`: it is the
pack's repository-knowledge refresh path for existing repospec/Repomix outputs,
architecture overview updates, and Obsidian KB integration.

The update-spec command invokes the existing Trellis `trellis-update-spec` skill
from the target repo, uses it as-is to update `.trellis/spec/`, and then checks
whether the repo has checked-in infrastructure for maintaining a repospec
artifact. It looks for exact Makefile targets or package scripts named
`repospec`, `update-repospec`, `refresh-repospec`, `repomix`,
`update-repomix`, or `refresh-repomix`; executable `scripts/` entries with
those names or `repo-map`, `update-repo-map`, or `refresh-repo-map` and an
optional `.sh`, `.py`, `.js`, `.mjs`, or `.ts` extension; then a documented
command under a `Repospec`, `Repomix`, or `Repository map` heading in
`AGENTS.md` or `README.md`. It does not infer commands from incidental prose.
When that infrastructure exists, the command uses it to refresh the repospec
artifact instead of hand-editing generated output. If that refresh uses Repomix
or another repository-map tool, follow the target repo's documented output path;
if no path is documented, prefer `docs/repomix-map.md` and report the chosen
path. The `update-spec` command then checks for an
existing architectural overview. Candidate overview paths include
`ARCHITECTURE.md`, `ARCHITECTURE_OVERVIEW.md`, `docs/ARCHITECTURE.md`,
`docs/ARCHITECTURE_OVERVIEW.md`, and `.trellis/spec/**/architecture*.md`. If an
overview exists and the work changes high-level architecture such as packages,
command surfaces, data flow, persistence, external integrations, config/env, or
runtime/deployment topology, the wrapper updates it. Otherwise it leaves the
overview untouched and reports `not present` or `not warranted`.

The update-spec command also runs
`scripts/sd-ai-command-pack-update-spec-kb.py` to maintain `.obsidian-kb/` in the
repo root and ensure that folder is listed in `.gitignore` inside a managed
`sd-ai-command-pack obsidian-kb` marker block. For local-only installs, the same
managed block is written to `.git/info/exclude` instead. The folder contains
copies of repository-knowledge files such as README files, agent instructions,
architecture and decision docs, `.trellis/spec/**/*.md`, `.trellis/workflow.md`,
`.trellis/config.yaml`, `.trellis/tasks/**/*.md`, repo-owned repospec or
Repomix outputs such as
`docs/repomix-map.md`, and project manifests that explain the repository shape
when present. The helper writes those copies into visible semantic category
folders rather than mirroring hidden source paths, so generated KB file and
folder names do not start with `.` or use Trellis-specific naming. It should
avoid secrets, caches, build output, dependency/vendor directories, `.git/`,
`.trellis/workspace/`, and broad source trees unless a specific source
entrypoint is intentionally maintained as repo documentation. If an existing
`.obsidian-kb` folder was created by an older symlink-based helper, the refresh
replaces pack-owned relative symlinks with real copies in the category layout
and prunes the old mirrored generated paths.
The helper also creates and refreshes `.obsidian-kb/Dashboard - <repo>.md`,
a generated Markdown landing page that groups and links to the current KB
copies, adds a brief one-line description for each linked document, points to
`.obsidian-kb/LLM-KB - <repo>.md`, and includes a GitHub repository link when
`origin` is a GitHub remote. Dashboard and overview links are grouped by
semantic categories such as repository overview, agent guidance, specs, repo
maps, and project manifests rather than by source folder name.
`LLM-KB - <repo>.md` is a generated, self-contained overview for LLM and
Obsidian indexing. If a
user-owned file already exists at either generated path, the helper leaves it
untouched and reports a conflict. Exit codes: `0` clean, `1` for `--check`
staleness, `2` for hard errors, and `3` when a refresh completes but reports
conflicts it could not bring current — automation should treat `3` as
"KB partially stale", not success. Run
`python3 scripts/sd-ai-command-pack-update-spec-kb.py --dry-run` to preview the
refresh without writes, `--check` to verify the generated folder and ignore
entry are current, or `--help` for the safe CLI summary. Add `--if-present` to
any mode when automation should refresh only repositories that already have
`.obsidian-kb`; an absent folder returns success with a visible skip reason and
causes no writes, while an occupied or invalid path retains the normal failure.

To use the generated knowledge folder inside an Obsidian vault, copy the repo's
`.obsidian-kb` folder into the vault. Recopy it after future `sd-update-spec`
runs when the repository knowledge changes.

macOS/Linux:

```bash
cp -R "$(pwd)/.obsidian-kb/." "/path/to/your/vault/Repo-KB"
```

Windows PowerShell:

```powershell
New-Item -ItemType Directory -Force -Path "C:\path\to\vault\Repo-KB" | Out-Null
Copy-Item -Recurse -Force -Path "C:\path\to\repo\.obsidian-kb\*" -Destination "C:\path\to\vault\Repo-KB"
```

The housekeeping command ends a single active development stream. On an open
PR, it runs the SD finish-work flow before actual cleanup and pushes any
archive or journal commits that finish-work creates. It then runs the
housekeeping script, which checks a strict auto-merge gate:

- the working tree is clean
- the local branch head, remote branch head, and PR head all match
- the PR is open and not draft
- the base is the default branch
- merge state is clean
- at least one executed check succeeded and none are blocking: pending, or any
  conclusion other than success, skipped, or neutral (for example failed,
  cancelled, or timed out). Classifier-skipped checks do not block.
- there are no unresolved review threads

When that is true, it merges the PR and then performs normal cleanup. If that gate is
not satisfied, it behaves as a post-merge cleanup command: fetch/prune
`origin`, confirm the current feature branch's PR is merged and the local branch
head matches that PR before deleting it, switch to the default branch,
fast-forward from `origin`, and delete the merged local and remote branch. The
script then invokes the installed `sd-status` collector in strict mode, passing
the default/source branches, remote-branch policy, cleanup anomalies, and a
`refreshed` label after a successful fetch/prune. That shared collector owns the
final Git verification, pack/Trellis versions, relevant PR/review count,
repo-wide open PRs/issues, Trellis inventory, anomaly list, and numbered next
steps. Repo-wide inventory remains context rather than a cleanup blocker.

The installed script also supports
`bash scripts/sd-ai-command-pack-housekeeping.sh --self-test`, which verifies
the vendored copy's merge-gate contract against stubbed scenarios and exits.
It is hermetic (no git, gh, or network access), so repos can run it from CI or
a test suite instead of maintaining bespoke contract tests over the vendored
script; it fails non-zero if any gate scenario misbehaves.

A clean current-stream housekeeping run should end with the shared status
shape:

```text
SD status: healthy
Ref freshness: refreshed

==> Expected clean state
- branch: <default>
- working tree: clean
- upstream: origin/<default>; synchronized
- local branches (1): <default>
- git stashes: 0
- remote source branch absent: origin/<feature>

==> Delivery
- SD pack: <installed version>
- Trellis: <installed version>
- relevant PR: #<number> MERGED

==> Inventory
- open PRs (<count>): <summary>
- open issues (<count>): <summary>
- current Trellis task: <summary>

==> Anomalies
none

==> Next Steps
1. <highest-value evidence-backed next action>
```

The agent-facing final response should summarize that script output in a short
housekeeping report rather than pasting every line. A clean report should use
this shape:

```text
Housekeeping completed cleanly.
PR #<number> was <merged by housekeeping|already merged by the time the script ran>; housekeeping confirmed the merge, switched to <default>, fast-forwarded to origin/<default>, deleted the local and remote <feature> branch, and pruned refs.

Final state:
Branch: <default>
Working tree: clean
<default> matches origin/<default>
Local branches: only <default>
Remote branches: origin/HEAD, origin/<default>
PR #<number>: merged at <timestamp>
Open PRs: <none|summary>
Open issues: <none|summary>
Current Trellis task: <none active|task id + status>
PR review rounds: <n submitted reviewer review(s)|n/a — no PR in this run>
Anomalies: none

Insight:
<One short evidence-backed observation about what housekeeping proved or surfaced; omit this section when there is nothing useful beyond the final state.>

Next Steps:
<Always present, even on a verification-only clean run: the current Trellis task and the next high-value work. A short numbered list covering open follow-up items from the session, any in-progress Trellis task to resume, then high-value Trellis task candidates / roadmap items to start next. If the backlog is empty, write "No open or planned Trellis work — backlog is clear.">
```

Include `Insight:` only when the script output or session context supports a
useful observation, such as the PR lifecycle being healthy, cleanup being
verification-only because the PR was already merged, stale refs being pruned,
the repo being ready for the next work stream, or a process improvement being
worth tracking. Do not add filler insights that merely restate `clean`.
Always end with a numbered `Next Steps` section, even on a verification-only
clean run: the report still names the current Trellis task and the next
high-value work. It covers open follow-up items from the session, any
in-progress Trellis task to resume, then high-value Trellis task candidates
or roadmap items to start next. It also states the current task in the
final-state rows. If a category has no evidence, the report says that plainly
instead of inventing work, and if the whole backlog is empty it says the
backlog is clear rather than omitting the section.

The `sd-audit-repo` command runs the formal multi-dimension repository audit.
It is charter-driven: one read-only reviewer per dimension, with the charters
installed at `.agents/skills/sd-audit-repo/charters/` (12 always-on
dimensions plus consumer-impact, observability, and accessibility-i18n when
the fingerprint stage selects them). The pipeline is fixed and ordered:
fingerprint → dimension reviews → adversarial verification → synthesis → Trellis reconciliation → report + ledger.

Arguments: bare exact charter names such as `security testing`, or the explicit
`dimensions=<a,b,c>` form, restrict the run to the named charters; unknown names are an error, not a silent skip. `depth=quick|standard|deep`
controls verification (quick skips it, standard refutes P0/P1 findings, deep
refutes P0–P2 with 2-of-3 votes on P0); `follow-up` re-verifies open ledger
items against the current tree instead of sweeping the whole repository.

Every audit report contains six mandatory sections — Verdict, Findings,
Trellis reconciliation, Prioritized actions, Ledger delta, and
Coverage & limits — and empty sections state their emptiness explicitly
instead of disappearing. Findings carry fixed scores: severity P0–P3, effort
S/M/L, confidence Verified or Plausible.

Audit findings persist in the committed ledger at `.trellis/audit/ledger.md`.
The orchestrator assigns monotonic `A-NNN` finding IDs that are never reused,
keeps `fixed` entries as history, marks a reappearing fixed finding
`regressed` under the same ID, and preserves human-edited `notes:` lines.
The audit never creates Trellis tasks on its own: untracked P0–P2 findings
become prd-ready task proposals that wait for explicit user consent.

`sd-audit-repo` complements `sd-review-local` (provider loop),
`sd-review-pr` (PR loop), and `sd-full-check` (gate); it is the periodic
formal audit, not a per-change review loop.

The `sd-watch-pr` command watches the current branch's open pull request
until it settles — no pending checks, the requested reviewer has reviewed
(or a short grace period passes), and review threads are counted — inside a
bounded polling loop (default 30 minutes; `timeout-minutes=N` overrides).
On a settled, green, comment-clean PR it hands off to the `sd-housekeeping`
flow, whose gate remains the only merge authority; with `no-merge` it stops
after reporting readiness. On blockers it reports failing checks by name
and unresolved threads by path, pointing at `sd-fix-ci` or `sd-review-pr`
as follow-ups. It never merges directly.

The `sd-fix-ci` command triages a red CI run back toward green. It targets
the current branch's PR checks by default, or the default branch's latest
failing run with `main`. Each failing job is classified as real-code,
flake, infra, or stale-baseline: real-code failures on a PR branch are
reproduced locally, fixed, gated, and pushed; real-code failures on the
default branch always go through a fix branch and pull request; flakes are
re-run boundedly (`max-reruns=N`, default 1) with the evidence reported;
infra failures are reported only. It never force-pushes, never bypasses
guards, and never deletes, skips, or weakens tests to get green.

The `sd-update-deps` command batch-triages open dependency-bot pull
requests. Each PR is classified by ecosystem, semver delta, and security
linkage. The auto-merge class — patch/minor dev-dependency updates, GitHub
Actions pin bumps, and security patches (runtime minors only with
`include-runtime-minor`) — merges strictly sequentially under the
housekeeping gate criteria, re-verifying heads after every prior merge and
confirming the default branch stays green between merges. Majors are
always manual. Everything else is parked with a one-line recommendation,
and `dry-run` reports classifications without merging.

The `sd-fleet-refresh` command is an operator workflow available only in the
`sd-ai-command-pack` source checkout; it is not installed into consumer
repositories because it depends on source-only release and fleet metadata.
It rolls the current pack release across consumer repositories, following the
pack source repository's
[fleet rollout procedure](https://github.com/platypeeps/sd-ai-command-pack/blob/main/docs/FLEET_ROLLOUT.md)
with the
[fleet preflight helper](https://github.com/platypeeps/sd-ai-command-pack/blob/main/scripts/sd-ai-command-pack-fleet-preflight.py)
deciding which consumers are stale. It processes one consumer at a time: verify a clean tree (dirty
trees are skipped and reported, never touched), branch, install the
release, run the consumer's full-check, open the consumer PR, watch it to
settled, and merge through the consumer's housekeeping gate. Bare consumer
names such as `loadsmith rwbp-website`, or `consumer=a,b`, filter the run;
`no-merge` stops before merging and `dry-run` reports preflight only. Unknown
consumer names fail before mutation rather than broadening to the whole fleet.
The report is a per-consumer status table plus a fleet version summary.

The `sd-test-gaps` command closes the worst coverage gaps with targeted
tests. It runs the repository's coverage flow as a baseline (aborting if
the baseline itself fails), ranks shipped files by per-file coverage ascending
(a bare path such as `scripts/example.py`, or `file=<path>`, targets one file),
and for the top `max-gaps=N`
files (default 3) authors focused tests through the normal implement/check
flow, then re-runs coverage and reports per-file before/after numbers. It
writes test files and fixtures only — never product code — and never
lowers configured coverage floors.

The `sd-ship` command takes the current branch from committed work to a
merged pull request by sequencing the standard SD stages: the sd-create-pr
flow, the sd-review-pr loop, the sd-watch-pr settle watcher, and the
sd-housekeeping gate, which remains the only merge authority. `until=pr`,
`until=review`, or the default `until=merge` choose the stop-point, and
stage arguments such as `timeout-minutes=` pass through. It adds no new
gate logic; every stage's own gates remain authoritative, and a failed or
blocked stage stops the chain with that stage's report.

Stage 1 delegates `sd-create-pr` with an internal composite-only orchestration
context that returns after PR publication. It is not a public argument or
environment variable. This keeps standalone `sd-create-pr` behavior unchanged
while making Stage 2 the only review owner in `sd-ship`: no review for
`until=pr`, one normal review for `until=review`, and one deferred-finish-work
review for `until=merge`.

Stage 2 also owns the one post-cycle review-learning pass performed by
`sd-review-pr`. No later ship, watch, finish-work, or housekeeping stage repeats
it.

Lifecycle side effects have one owner. `until=review` keeps finish-work in
`sd-review-pr`. The default merge-through chain defers finish-work to Stage 4,
watches with `no-merge` in Stage 3, and invokes housekeeping exactly once in
Stage 4. A blocked or timed-out watch therefore leaves the active Trellis task
available for a later resume instead of archiving it before the PR settles.
After finish-work, housekeeping owns one `--if-present` KB refresh before its
merge gate so archived task documentation is current. `sd-ship` does not repeat
that refresh.

When the canonical backlog controller invokes the merge-through chain, it adds
a trusted internal `caller: sd-work-backlog` context bound to the active run,
iteration, selected task, branch, and lock. After Stage 4, `sd-ship` returns a
bounded `SD_SHIP_MERGE_RESULT` with PR/merge, finish-work, housekeeping, review
rounds, final branch/HEAD, and anomalies. This changes only report ownership:
all four stages and safety gates still run exactly as in standalone shipping,
and the parent loop remains the only owner of the overall final response.
After it creates or updates post-ship follow-up tasks, the parent controller
owns one final `--if-present` KB refresh before recording the iteration result.
That later refresh covers only mutations made after nested housekeeping
returned and is not a duplicate unconditional archive refresh.

The `sd-retro` command captures a structured retrospective after a
debugging stream or incident: what broke, the root cause, why existing
gates and tests missed it, and what limited the blast radius. It records
the retrospective as a journal entry through the session recorder
(`Retro: <topic>`). Bare text such as `deployment timeout`, or the explicit
`topic="deployment timeout"` form, supplies that topic. It then derives
prevention candidates and presents them as Trellis task proposals that wait
for explicit user consent — it never auto-creates tasks and makes no code changes.

## Configuration

Common environment variables:

### Fleet Status

Create or refresh the machine-local fleet profile explicitly from the
canonical pack checkout:

```bash
python3 /path/to/sd-ai-command-pack/install.py /path/to/target/repo \
  --configure-fleet
```

The default profile is
`$XDG_CONFIG_HOME/sd-ai-command-pack/config.json` when `XDG_CONFIG_HOME` is
set, otherwise `~/.config/sd-ai-command-pack/config.json`. It has schema
version 1 and stores `packSource`, optional `fleetManifest`, and optional
`pathOverrides` keyed by fleet consumer name. Only machine-specific locations
belong there: the checked-in `docs/fleet/consumers.json` remains authoritative
for fleet membership, rollout priority, platforms, and candidate commands.
Existing path overrides are preserved when the installer refreshes the
profile. `sd-status` reads but never writes it.

```json
{
  "schemaVersion": 1,
  "packSource": "/path/to/sd-ai-command-pack",
  "fleetManifest": "/path/to/sd-ai-command-pack/docs/fleet/consumers.json",
  "pathOverrides": {
    "rwbp-coordinator": "/path/to/rwbp-coordinator"
  }
}
```

- `SD_AI_COMMAND_PACK_FLEET_CONFIG`: advanced override for the machine profile
  path, useful when several independent pack sources are present.
- `SD_AI_COMMAND_PACK_FLEET_MANIFEST`: override the canonical fleet manifest
  for one environment. The public `--fleet-manifest` option has higher
  precedence; both take precedence over the machine profile.

### Autonomous Work-Loop State

`sd-work-backlog` and `sd-work-designs` store resumable coordination state
outside the repository. Resolution order is:

1. absolute `SD_AI_COMMAND_PACK_STATE_HOME`;
2. absolute `$XDG_STATE_HOME/sd-ai-command-pack`;
3. `%LOCALAPPDATA%/sd-ai-command-pack/state` on Windows; then
4. `~/.local/state/sd-ai-command-pack`.

Each repository gets a digest derived from its normalized root and canonical
Git remote. The schema-versioned JSON ledger and lock use atomic replacement,
user-only permissions where supported, bounded history, and no credentials or
raw command/review output. A relative explicit state path is rejected. Use
`sd-status --json` for read-only loop visibility; use the work-loop command to
resume, record evidence, reconcile, checkpoint, or stop it. For example:

`sd-status` treats the dynamically loaded work-loop helper as an input boundary:
it normalizes active and terminal snapshots to pack-owned fields, validates
nested run metadata, and sanitizes and bounds every retained string before JSON
or terminal rendering.

```bash
bash scripts/sd-ai-command-pack-toolchain.sh run-python -- \
  scripts/sd-ai-command-pack-work-loop.py evidence --repo . \
  --run-id <run-id> --head <sha> --pr-number <n> --pr-url <url>
```

### Full Check And Preflight

Run the non-mutating toolchain doctor before dependency-sensitive SD workflows:

```bash
bash scripts/sd-ai-command-pack-toolchain.sh doctor
bash scripts/sd-ai-command-pack-toolchain.sh doctor --json
bash scripts/sd-ai-command-pack-toolchain.sh run-python \
  --require-module coverage -- -m coverage --version
```

The helper resolves Python once in this order: `SD_AI_COMMAND_PACK_PYTHON`,
the repo `.venv` (POSIX or Windows layout), active `VIRTUAL_ENV`, Apple Silicon
then Intel Homebrew Python 3.13 on macOS, and finally a supported `python3` on
`PATH`. An explicit override or existing repo `.venv` is authoritative: if it
is invalid or lacks a required module, the helper stops with one `make setup`
remedy rather than silently falling through. `doctor` reports project-check
candidates but never executes them. Only
`SD_AI_COMMAND_PACK_PROJECT_CHECK_COMMAND` selects a project check.

On macOS, prefer a Homebrew Python-backed virtualenv for repo-local Python
checks, especially coverage runs. Apple/Xcode Python often lacks project dev
dependencies and can try to write bytecode caches under protected
`~/Library/Caches` paths. A portable setup is:

```bash
BREW_PYTHON="${BREW_PYTHON:-/opt/homebrew/bin/python3.13}"
test -x "$BREW_PYTHON" || BREW_PYTHON=/usr/local/bin/python3.13
"$BREW_PYTHON" -m venv .venv
. .venv/bin/activate
```

In sandboxed agent sessions, some otherwise-correct local checks fail because
their default caches or temporary files land outside the writable sandbox, or
inside repo cache directories the agent cannot write. Before running `uv run`,
`uvx`, Ruff, Python compile/coverage, `scripts/preflight-pr.sh`, or
`sd-ai-command-pack-full-check.sh`, prefer sandbox-local cache directories:

```bash
SANDBOX_TMP="${SANDBOX_TMP:-${TMPDIR:-/tmp}}"
export PYTHONPYCACHEPREFIX="${PYTHONPYCACHEPREFIX:-$SANDBOX_TMP/sd-ai-command-pack-pycache}"
export UV_CACHE_DIR="${UV_CACHE_DIR:-$SANDBOX_TMP/sd-ai-command-pack-uv-cache}"
export UV_TOOL_DIR="${UV_TOOL_DIR:-$SANDBOX_TMP/sd-ai-command-pack-uv-tools}"
export RUFF_CACHE_DIR="${RUFF_CACHE_DIR:-$SANDBOX_TMP/sd-ai-command-pack-ruff-cache}"
```

These variables are safe for normal developer shells too: they only redirect
ephemeral tool state and do not change what the checks validate.

- `SD_AI_COMMAND_PACK_PYTHON`: authoritative Python executable for the
  toolchain helper. It must be Python 3.10 or newer and include every module
  requested with `--require-module`.
- `SD_AI_COMMAND_PACK_PROJECT_CHECK_COMMAND`: explicit trusted project-check
  command selected by the repo/operator. Toolchain discovery only reports
  candidates when this is unset.
- `SD_AI_COMMAND_PACK_TOOLCHAIN_PLATFORM`: advanced/test override for platform
  detection; normal shells should leave it unset.
- `SD_AI_COMMAND_PACK_TOOLCHAIN_HOMEBREW_PREFIXES`: advanced/test override for
  the colon-separated Homebrew prefix search order; defaults to
  `/opt/homebrew:/usr/local` on macOS.
- `SD_AI_COMMAND_PACK_REPO_ROOT`: advanced/test override for the repository
  root inspected by the toolchain helper; normal runs discover the Git
  top-level directory and should leave it unset.
- `SD_AI_COMMAND_PACK_FULL_CHECK_BASE_REF`: explicit base ref for branch review.
  When unset, branch-diff helpers use the discovered remote default ref, then
  the current branch upstream, then the first available remote ref.
- `SD_AI_COMMAND_PACK_REVIEW_PREFLIGHT_BASE_REF`: explicit base ref for the
  JavaScript review-preflight branch-diff probes. Defaults to
  `SD_AI_COMMAND_PACK_FULL_CHECK_BASE_REF`, then the discovered branch-diff
  sequence above. Review size and added-code risk probes compare the working
  tree with the merge base of this ref and `HEAD`, so upstream-only changes do
  not inflate the advisory. If no merge base exists, the preflight warns and
  conservatively falls back to the configured or discovered base ref.
- `SD_AI_COMMAND_PACK_FULL_CHECK_REVIEW_PREFLIGHT=0`: skip
  repo-local review preflight.
- `SD_AI_COMMAND_PACK_FULL_CHECK_REVIEW_PREFLIGHT=required`: fail if no configured
  review preflight command can run and the shared or legacy review preflight is
  unavailable.
- `SD_AI_COMMAND_PACK_FULL_CHECK_REVIEW_PREFLIGHT_COMMAND`: repo-specific review
  preflight command to run with `bash -c`.
- `SD_AI_COMMAND_PACK_FULL_CHECK_REVIEW_PREFLIGHT_SCRIPT`: custom JavaScript
  review preflight script to run before the legacy repo-local
  `scripts/check-review-preflight.mjs` fallback.
- `SD_AI_COMMAND_PACK_INSTALL_AUDIT=0`: skip the structural post-install audit.
- `SD_AI_COMMAND_PACK_FULL_CHECK_KB`: Obsidian KB freshness check mode.
  Default `auto` runs `scripts/sd-ai-command-pack-update-spec-kb.py --check`
  only when a generated `.obsidian-kb/` folder exists and skips with a warning
  otherwise; `0` skips entirely; `required` fails when the helper, `python3`,
  or a passing check is unavailable. A stale KB fails the full check with a
  refresh hint.
- `SD_AI_COMMAND_PACK_FULL_CHECK_PACK_DRIFT=0`: skip the pack source drift
  gates (template twin parity, release-version coverage for shipped payload
  changes, and env-var documentation coverage). In `auto` mode, generic source
  markers (`install.py`, `manifest.json`, and `templates/`) only make a repo a
  candidate: the gates run only when the parsed root manifest has
  `name: sd-ai-command-pack` plus a non-empty `version` and a `files` list.
  Other installer repos, including `se-ai-command-pack`, skip the SD-specific
  gates. A malformed manifest that asserts the SD identity fails conservatively
  instead of silently bypassing source checks.
- `SD_AI_COMMAND_PACK_FULL_CHECK_RELEASE_BASE_REF`: explicit base ref for the
  pack-source release-version gate. Defaults to
  `SD_AI_COMMAND_PACK_FULL_CHECK_BASE_REF`, then the discovered branch-diff
  sequence above.
- `SD_AI_COMMAND_PACK_INSTALL_AUDIT=required`: fail if the full-check cannot run
  the audit script.
- `SD_AI_COMMAND_PACK_FULL_CHECK_PACKAGE_SCRIPTS`: space-separated package scripts
  to run when `package.json` and the selected package runner are available.
- `SD_AI_COMMAND_PACK_FULL_CHECK_PACKAGE_RUNNER`: package runner. Defaults to
  `npm` when package-script checks apply.
- `SD_AI_COMMAND_PACK_FULL_CHECK_SKIP_PACKAGE_SCRIPTS=1`: skip package-script
  checks.
- `SD_AI_COMMAND_PACK_FULL_CHECK_PRISM=0`: skip Prism review.
- `SD_AI_COMMAND_PACK_FULL_CHECK_PRISM=required`: fail if Prism is missing,
  unauthenticated, or has provider/model configuration failures.
- `SD_AI_COMMAND_PACK_FULL_CHECK_PRISM_RULES`: explicit Prism rules file. Defaults to
  `.prism/rules.json` when present.
- `SD_AI_COMMAND_PACK_FULL_CHECK_PRISM_FAIL_ON`: severity that fails the Prism
  review (passed to `prism --fail-on`). Defaults to `high`.
- `SD_AI_COMMAND_PACK_FULL_CHECK_PRISM_MAX_FINDINGS`: cap on reported Prism
  findings (passed to `prism --max-findings`). Unset by default (no cap).
- `SD_AI_COMMAND_PACK_FULL_CHECK_PRISM_EXCLUDE`: comma-separated extra Prism
  `--exclude` globs appended to the pack's built-in review-scan exclusions.
- `SD_AI_COMMAND_PACK_FULL_CHECK_GITO=1`: opt into Gito review.
- `SD_AI_COMMAND_PACK_FULL_CHECK_GITO_BASE_REF`: base ref for Gito review. Defaults to
  `SD_AI_COMMAND_PACK_FULL_CHECK_BASE_REF`, then the discovered branch-diff
  sequence above.
- `SD_AI_COMMAND_PACK_FULL_CHECK_GITO_OUT_DIR`: output folder for Gito reports. Defaults
  to `.build/review/gito`.
- `SD_AI_COMMAND_PACK_FULL_CHECK_GITO_MAX_ATTEMPTS`: max Gito attempts when the
  provider reports HTTP 429 or slow-down rate limiting. Defaults to the
  `SD_AI_COMMAND_PACK_REVIEW_LOCAL_GITO_MAX_ATTEMPTS` value, then `2`.
- `SD_AI_COMMAND_PACK_FULL_CHECK_GITO_RETRY_DELAY_SECONDS`: initial Gito retry
  delay for rate limits. Defaults to the
  `SD_AI_COMMAND_PACK_REVIEW_LOCAL_GITO_RETRY_DELAY_SECONDS` value, then `30`.
- `SD_AI_COMMAND_PACK_FULL_CHECK_GITO_RETRY_MAX_DELAY_SECONDS`: maximum Gito
  retry delay after exponential backoff. Defaults to the
  `SD_AI_COMMAND_PACK_REVIEW_LOCAL_GITO_RETRY_MAX_DELAY_SECONDS` value, then
  `120`.
- `SD_AI_COMMAND_PACK_FULL_CHECK_GITO_TIMEOUT_SECONDS`: maximum runtime for one
  full-check Gito attempt. Defaults to
  `SD_AI_COMMAND_PACK_REVIEW_LOCAL_GITO_TIMEOUT_SECONDS`, then `600`; set `0`
  to disable the timeout.

### Local Review

- `SD_AI_COMMAND_PACK_REVIEW_LOCAL_TOOLS`: local review tool list for
  `sd-review-local`. Defaults to `prism gito`; accepts spaces or commas.
- `SD_AI_COMMAND_PACK_REVIEW_LOCAL_SCOPE=all`: run the local review runner
  against the full checked-out repository. Defaults to current-diff scope. The
  `sd-review-local` command in `all` mode passes this by invoking the
  runner with
  `--full-codebase`.
- `SD_AI_COMMAND_PACK_REVIEW_LOCAL_BASE_REF`: base ref for the current-diff
  local review scope. Defaults to `SD_AI_COMMAND_PACK_FULL_CHECK_BASE_REF`,
  then the discovered branch-diff sequence above.
- `SD_AI_COMMAND_PACK_REVIEW_LOCAL_PRISM_MODE=0`: disable Prism in the local
  review runner. By default, if Prism is selected as an active local review
  tool, it must run successfully.
- `SD_AI_COMMAND_PACK_REVIEW_LOCAL_PRISM_CODEBASE_FALLBACK=0`: disable the
  tracked-file batch fallback used when Prism full-codebase review reports an
  empty chunk response.
- `SD_AI_COMMAND_PACK_REVIEW_LOCAL_PRISM_CODEBASE_BATCH_SIZE`: tracked file
  batch size for that fallback before adaptive splitting. Defaults to `25`.
- `SD_AI_COMMAND_PACK_REVIEW_LOCAL_PRISM_CODEBASE_MAX_EMPTY_CHUNK_FAILURES`:
  maximum failed single-path requests during full-codebase fallback before the
  runner stops issuing more Prism requests. Defaults to `3`; set `0` to allow
  all fallback paths.
- `SD_AI_COMMAND_PACK_REVIEW_LOCAL_PRISM_TIMEOUT_SECONDS`: maximum runtime for
  one Prism command. Defaults to `300`; set `0` to disable the timeout.
- `SD_AI_COMMAND_PACK_REVIEW_LOCAL_PRISM_FAIL_ON`: severity that fails the
  local Prism review. Defaults to
  `SD_AI_COMMAND_PACK_FULL_CHECK_PRISM_FAIL_ON`, then `high`.
- `SD_AI_COMMAND_PACK_REVIEW_LOCAL_PRISM_MAX_FINDINGS`: cap on reported local
  Prism findings. Defaults to
  `SD_AI_COMMAND_PACK_FULL_CHECK_PRISM_MAX_FINDINGS`, then unset (no cap).
- `SD_AI_COMMAND_PACK_REVIEW_LOCAL_PRISM_RULES`: explicit Prism rules file for
  the local review runner. Defaults to
  `SD_AI_COMMAND_PACK_FULL_CHECK_PRISM_RULES`, then `.prism/rules.json` when
  present.
- `SD_AI_COMMAND_PACK_REVIEW_LOCAL_PRISM_EXCLUDE`: comma-separated extra Prism
  `--exclude` globs for the local review runner. Defaults to
  `SD_AI_COMMAND_PACK_FULL_CHECK_PRISM_EXCLUDE`.
- `SD_AI_COMMAND_PACK_REVIEW_LOCAL_GITO_MODE=0`: disable Gito in the local
  review runner. By default, if Gito is selected as an active local review tool,
  it must run successfully.
- `SD_AI_COMMAND_PACK_REVIEW_LOCAL_GITO_MAX_ATTEMPTS`: max Gito attempts when
  the provider reports HTTP 429 or slow-down rate limiting. Defaults to `2`.
- `SD_AI_COMMAND_PACK_REVIEW_LOCAL_GITO_RETRY_DELAY_SECONDS`: initial Gito retry
  delay for rate limits. Defaults to `30`.
- `SD_AI_COMMAND_PACK_REVIEW_LOCAL_GITO_RETRY_MAX_DELAY_SECONDS`: maximum Gito
  retry delay after exponential backoff. Defaults to `120`.
- `SD_AI_COMMAND_PACK_REVIEW_LOCAL_GITO_TIMEOUT_SECONDS`: maximum runtime for
  one Gito attempt. Defaults to `600`; set `0` to disable the timeout.
- `MAX_CONCURRENT_TASKS`: Gito LLM concurrency cap. The pack runners load the
  installed `.gito/sd-ai-command-pack.env` default of `4` when this variable is
  unset.
- `SD_AI_COMMAND_PACK_REVIEW_LOCAL_UV_CACHE_DIR`: fallback `UV_CACHE_DIR` for
  full-check and review-local Gito when `UV_CACHE_DIR` is unset. Defaults to a temp
  `sd-ai-command-pack-uv-cache` directory.
- `SD_AI_COMMAND_PACK_REVIEW_LOCAL_UV_TOOL_DIR`: fallback `UV_TOOL_DIR` for
  full-check and review-local Gito when `UV_TOOL_DIR` is unset. Defaults to a temp
  `sd-ai-command-pack-uv-tools` directory.
- `SD_AI_COMMAND_PACK_REVIEW_LOCAL_<TOOL>_COMMAND`: command for a repo-specific
  or third-party local review tool, run with `bash -c`.
- `SD_AI_COMMAND_PACK_REVIEW_LOCAL_ALL_<TOOL>_COMMAND`: full-codebase command
  for a repo-specific or third-party local review tool. Takes precedence over
  `SD_AI_COMMAND_PACK_REVIEW_LOCAL_<TOOL>_COMMAND` when scope is `all`.
- `SD_AI_COMMAND_PACK_REVIEW_LOCAL_SEMGREP_COMMAND`: example Semgrep custom
  provider command for `sd-review-local`; follows the generic `<TOOL>` command
  naming pattern.
- `SD_AI_COMMAND_PACK_REVIEW_LOCAL_GITO_BASE_REF`: base ref for review-local Gito
  review. Defaults to `SD_AI_COMMAND_PACK_FULL_CHECK_GITO_BASE_REF`, then
  `SD_AI_COMMAND_PACK_FULL_CHECK_BASE_REF`, then the discovered branch-diff
  sequence above.
- `SD_AI_COMMAND_PACK_REVIEW_LOCAL_GITO_OUT_DIR`: output folder for review-local
  Gito reports. Defaults to `SD_AI_COMMAND_PACK_FULL_CHECK_GITO_OUT_DIR`, then
  `.build/review/gito`.
- `SD_AI_COMMAND_PACK_REVIEW_LOCAL_ALL_GITO_OUT_DIR`: output folder for
  full-codebase (`all` mode) Gito reports. Defaults to
  `SD_AI_COMMAND_PACK_REVIEW_LOCAL_GITO_OUT_DIR`, then
  `SD_AI_COMMAND_PACK_FULL_CHECK_GITO_OUT_DIR`, then `.build/review/gito-all`.

### Scope And PR Body Checks

- `SD_AI_COMMAND_PACK_SCOPE_CHECK=0`: skip tooling/generated file scope checks
  (`off`/`disabled` also work, and disable the early advisory below too).
- `SD_AI_COMMAND_PACK_SCOPE_CHECK=advisory`: classify the working/branch diff
  and, when a tooling/generated file is present, warn naming the required PR
  scope section without contacting `gh` or a PR. The shared review preflight
  (`sd-ai-command-pack-review-preflight.mjs`, which the local pre-PR gate runs)
  invokes this automatically, so the reminder to add a
  `Tooling/generated scope:` section arrives before the PR exists — while the
  full-check hard-fail with a PR present is unchanged.
- `SD_AI_COMMAND_PACK_TARGETS_FILE`: explicit installed-targets file for the
  review-scope check. Defaults to `.sd-ai-command-pack/installed-targets.txt`.
- `SD_AI_COMMAND_PACK_SCOPE_CHECK_GH=required`: fail when `gh` cannot resolve the
  current PR for the tooling/generated scope body check. Defaults to optional.
- `SD_AI_COMMAND_PACK_SCOPE_BASE_REF`: base ref for tooling/generated scope checks.
  Defaults to `SD_AI_COMMAND_PACK_FULL_CHECK_BASE_REF`, then the discovered
  branch-diff sequence above.
- `SD_AI_COMMAND_PACK_SCOPE_PR_BODY`: explicit PR body text for tooling/generated
  scope checks when `gh pr view` should not be used.
- `SD_AI_COMMAND_PACK_REVIEW_PR_SELECTOR`: PR number or URL for `sd-review-pr`
  when the command cannot resolve the pull request from the current branch.
- `SD_AI_COMMAND_PACK_REVIEW_PR_REMOTE_REVIEWER`: remote reviewer request
  identity for `sd-review-pr`. Defaults to `@copilot`.
- `SD_AI_COMMAND_PACK_REVIEW_PR_REMOTE_REVIEWER_LABEL`: human-readable remote
  reviewer name used in `sd-review-pr` status output and reports. Defaults to
  `GitHub Copilot`.
- `SD_AI_COMMAND_PACK_REVIEW_PR_REMOTE_AUTHOR_MATCH`: review/comment author
  matched after a remote review request. Defaults to
  `copilot-pull-request-reviewer[bot]` when the configured reviewer is
  `@copilot`, and to the configured reviewer otherwise.
- `SD_AI_COMMAND_PACK_REVIEW_PR_REMOTE_REQUEST_COMMAND`: custom command for
  requesting a remote review when the provider is not triggered by a standard
  GitHub reviewer request. Unset by default.
- `SD_AI_COMMAND_PACK_REVIEW_PR_REMOTE_ROUND_LIMIT`: maximum remote review
  request/fix rounds before `sd-review-pr` asks whether to continue. Defaults
  to `5`.
- `SD_AI_COMMAND_PACK_REVIEW_PR_REMOTE_SETTLE_POLLS`: maximum 30-second polls
  before an accepted remote request without author-matched activity stops as
  ambiguous. Defaults to `40`.
- `SD_AI_COMMAND_PACK_CREATE_PR_BRANCH`: explicit feature branch name for
  `sd-create-pr` when it starts on the repository default branch. When unset,
  `sd-create-pr` derives a `codex/<slug>` branch from
  `SD_AI_COMMAND_PACK_CREATE_PR_BRANCH_SLUG`,
  `SD_AI_COMMAND_PACK_CREATE_PR_COMMIT_MESSAGE`, or a timestamped fallback.
- `SD_AI_COMMAND_PACK_CREATE_PR_BRANCH_SLUG`: slug source used to derive the
  default `codex/<slug>` feature branch when an explicit branch is not set.
- `SD_AI_COMMAND_PACK_PR_BODY_SCOPE_CHECK=0`: skip configurable PR-body scope
  checks.
- `SD_AI_COMMAND_PACK_PR_BODY_SCOPE_CHECK=required`: fail if the pack-provided
  PR-body scope checker cannot run, including when `python3` is missing.
- `SD_AI_COMMAND_PACK_PR_BODY_SCOPE_CONFIG`: explicit JSON config path for
  additional PR-body scope rules. Defaults to
  `.sd-ai-command-pack/pr-body-scope.json` when present.
- `SD_AI_COMMAND_PACK_PR_BODY_SCOPE_PR_BODY`: explicit PR body text for
  configurable PR-body scope checks. Falls back to
  `SD_AI_COMMAND_PACK_SCOPE_PR_BODY`.
- `SD_AI_COMMAND_PACK_PR_BODY_SCOPE_CHANGED_FILES`: explicit newline- or
  NUL-delimited changed path list for configurable PR-body scope checks.
- `SD_AI_COMMAND_PACK_CHANGED_FILES`: fallback changed-path list for the
  PR-body scope check when the `PR_BODY_SCOPE` variant above is unset.
- `SD_AI_COMMAND_PACK_PR_BODY_SCOPE_ACTOR`: PR author login (or pass
  `--actor`). A bot login ending in `[bot]` (`dependabot[bot]`,
  `github-actions[bot]`, `renovate[bot]`, …) is exempt from strict PR-body
  scope validation and exits `0`, so wiring the check into CI does not fail
  automated PRs (whose bodies never carry the human scope headings) and
  block their auto-merge.
- `SD_AI_COMMAND_PACK_HOUSEKEEPING_GITHUB_REPO`: explicit `owner/repo` slug when the
  selected remote URL cannot be parsed as a GitHub repository.
- `SD_AI_COMMAND_PACK_HOUSEKEEPING_MERGE_STRATEGY`: auto-merge strategy: `merge`,
  `squash`, or `rebase`. Defaults to `merge`.

Prism is enabled by default when the full-check command is invoked explicitly
and the executable is present. The `sd-review-pr` cycle disables Prism for its
command-owned full-check gate. If Prism is missing or credentials/config are
unavailable, the full-check script reports the skip and continues unless
`SD_AI_COMMAND_PACK_FULL_CHECK_PRISM=required` is set.

Gito is opt-in because it can require `uvx`, cache access outside the repo,
network access, and configured LLM credentials. The `sd-review-pr` cycle
disables Gito for its command-owned full-check gate. When enabled explicitly,
Gito writes reports to `.build/review/gito` by default so generated review
artifacts do not land at the repository root. The pack installs
`.gito/config.toml` for repo-local Gito defaults and
`.gito/sd-ai-command-pack.env` with `MAX_CONCURRENT_TASKS=4`; the full-check
and review-local runners parse that env file before invoking Gito, without
sourcing arbitrary shell. If Gito reports provider rate limiting through an
explicit HTTP 429 status such as `ClientError: 429`, full-check retries with
the same bounded backoff behavior as review-local.

## CI cadence

Run the full-check locally before deliberately triggering expensive remote CI
or remote AI review. Repos can still use labels such as `full-ci`, manual
workflow dispatch, or ready-for-review transitions for provider-side expensive
checks.

## Housekeeping cadence

Run housekeeping at the end of a development stream. From an open PR branch it
owns finish-work before applying the merge gate; after an already-merged PR it
performs the remaining cleanup and verification. If the command reports
anomalies, treat them as the next manual action: dirty files, an unmerged PR,
extra branches, open PRs/issues, or remaining Trellis tasks mean the repo is
not yet in the expected clean state.

## Updating the pack

To refresh installed assets from the pack checkout:

```bash
python3 /path/to/sd-ai-command-pack/install.py /path/to/target/repo --force
```

Add `--configure-fleet` when this machine should also use that pack checkout as
the source for `sd-status fleet`. Ordinary installs do not create or modify
user-global configuration. The option honors `--dry-run`, preserves existing
checkout path overrides, and is incompatible with inspection or removal modes.

Inspect before refreshing without modifying the target:

```bash
python3 /path/to/sd-ai-command-pack/install.py /path/to/target/repo --status
python3 /path/to/sd-ai-command-pack/install.py /path/to/target/repo --status --audit
python3 /path/to/sd-ai-command-pack/install.py /path/to/target/repo --check
python3 /path/to/sd-ai-command-pack/install.py /path/to/target/repo --check --json
```

`--status` reports `current`, `refresh-required`, `not-installed`, or `invalid`
and exits `0` for every non-invalid informational result. Add `--audit` to run
the shipped structural audit. `--check` always runs the audit and exits `0`
only for a current, audit-clean install; it exits `3` for a valid missing or
stale install and `1` for invalid receipts, vouched-file drift, audit failures,
or operational errors. Argument-usage errors remain exit `2`.

`--json` emits schema version `1` with the pack and target, source and installed
versions, version relation, state, installed and active platforms, result
counts, change count, reasons, and captured audit status/output. JSON output
does not change exit semantics. Inspection modes are read-only and reject
install, removal, platform-selection, force, backup, local-only, dry-run, and
diff-check options.

| Exit | Inspection meaning |
| --- | --- |
| `0` | Status completed; for `--check`, the install is current and audit-clean. |
| `1` | Installed state is invalid, audit failed, or inspection could not run. |
| `2` | Command-line usage is invalid. |
| `3` | `--check` found a valid missing or stale installation that needs action. |

Use `python3 /path/to/sd-ai-command-pack/install.py --help` for the safe CLI
summary, or `--version` to print the pack name and version without touching a
target repo.

To remove the pack from a target checkout:

```bash
python3 /path/to/sd-ai-command-pack/install.py /path/to/target/repo --remove
```

Remove mode treats receipts and provenance as candidate discovery only. It
deletes only manifest-recognized pack artifacts and generated pack state;
tampered entries under `.git/` or arbitrary repo files are reported as
`ignored`, even with `--force`.

Normal shared installs maintain a managed `sd-ai-command-pack
trellis-gitignore` block in the repo root `.gitignore`. The block ignores
Trellis local/runtime files such as `.trellis/.developer`,
`.trellis/.runtime/`, `.trellis/.cache/`, Trellis backup directories,
`.trellis/worktrees/`, and `.trellis/.template-hashes.json` without
blanket-ignoring shareable `.trellis` workflow, spec, task, and script files.
It also keeps shared Claude SD commands trackable while ignoring the rest of
`.claude/` as local Claude Code state. Other AI-tool local state such as tool
caches, logs, sessions, tmp folders, Gito report/temp artifacts,
tool-specific local state, `.opencode/node_modules/`, and root
`node_modules/` are ignored without blanket-ignoring shareable non-Claude
platform adapter directories.
The installer replaces exact unmarked `.trellis/` ignore entries with that
specific-pattern block.

Managed blocks are intentionally replaceable on future pack updates. They look
like this:

```gitignore
# sd-ai-command-pack trellis-gitignore start
# Generated by `python3 install.py`. DO NOT EDIT MANUALLY.
# Ignore local/runtime files without hiding shared Trellis or AI-tool adapters.
# Common local secrets and environment files.
.env
.env.*
!.env.example
!.env.ci
!.env.test

# Trellis local/runtime state.
.trellis/.developer
.trellis/.backup-*
.trellis/worktrees/
.trellis/.template-hashes.json
.trellis/.runtime/
.trellis/.cache/

# Review/build artifacts.
.build/
code-review-report.json
code-review-report.md
sd-ai-command-pack-gito.*
sd-ai-command-pack-review-paths.*
sd-ai-command-pack-review-filters.*
sd-ai-command-pack-prism-codebase.*
sd-ai-command-pack-ci-paths.*
sd-ai-command-pack-uv-cache/
sd-ai-command-pack-uv-tools/

# AI-tool local state; keep shared platform adapters tracked.
.agent/**/*.local.*
.agent/**/.cache/
.agent/**/cache/
.agent/**/logs/
.agent/**/tmp/
.agent/**/*.log
# The same six local-state patterns (*.local.*, .cache/, cache/, logs/, tmp/,
# *.log) repeat for every other active platform dir (.codebuddy/, .codex/,
# .cursor/, .devin/, .factory/, .gemini/, .gito/, .kiro/, .kilocode/,
# .opencode/, .pi/, .qoder/, .reasonix/, .trae/, .zcode/), with a few extras
# (.codex/ + .opencode/ sessions/, .opencode/ state/ + node_modules/, .gemini/
# + .claude/ settings.local.json). .claude/ is handled differently: it ignores
# .claude/** while negating tracked .claude/commands/sd/*.md. A normal install
# regenerates this managed block; --local-only writes the equivalent patterns
# to .git/info/exclude instead.
node_modules/

# Project-local personal ignores can be added below this managed block.
# sd-ai-command-pack trellis-gitignore end
```

```markdown
<!-- SD-AI-COMMAND-PACK:COPILOT-GUIDANCE:START -->
Pack-owned review guidance lives here.
<!-- SD-AI-COMMAND-PACK:COPILOT-GUIDANCE:END -->
```

For a personal setup that should not add generated framework files to the
shared GitHub repository, install with:

```bash
python3 /path/to/sd-ai-command-pack/install.py /path/to/target/repo --local-only
```

Local-only mode runs `trellis init --yes --skip-existing --codex` when Trellis
is not initialized yet, passes through requested installer platforms such as
`--platform cursor`, and writes Trellis plus sd-ai-command-pack generated paths
to `.git/info/exclude`. It also creates `.sd-ai-command-pack/local-only.txt` so
pack helpers keep generated local state, including `.obsidian-kb/`, out of
tracked `.gitignore`. It also keeps `.sd-ai-command-pack/installed-targets.txt`
clone-local in this mode. If a generated framework file is already tracked by
Git, the installer stops because clone-local excludes cannot hide tracked files.

Use `--dry-run` first when you want to inspect which files would change.
Use `--backup` with `--force` if the target repo may have local edits that need
to be preserved next to the overwritten files. Existing `.prism/rules.json` and
`.gito/config.toml` files, plus `.github/PULL_REQUEST_TEMPLATE.md`, that differ
from the pack templates are reported as `preserved` and are never overwritten
or reported as conflicts, so repo-specific review policy is not replaced during
a pack refresh. The pack-owned
`.gito/sd-ai-command-pack.env` file is updateable like scripts and docs so the
standard Gito concurrency cap can be refreshed.

Normal tracked installs use plan-before-apply conflict handling: without
`--force`, the installer checks every selected pack target before its first
write and exits `2` without applying a partial refresh when any target
conflicts. Local-only Trellis bootstrap is outside this boundary because it
invokes Trellis itself before the pack is installed.

Concurrent installs are not serialized. If two completed installer runs target
the same checkout, the last writer wins, but atomic file replacement ensures the
final receipt and provenance remain parseable and internally consistent. Prefer
one refresh at a time so operator output and backup ownership stay clear.

Run refreshes on a branch and merge them through a PR. Before merge, discard or
reset a failed refresh branch to roll back. After merge, revert the refresh PR
or its merge commit, then rerun the install audit. `--backup` only preserves
files overwritten by `--force` or removed with `--remove`; it is not a
transaction journal.

To compare a consumer's installed version with a local pack checkout without
changing the normal audit exit code, run:

```bash
python3 scripts/sd-ai-command-pack-install-audit.py \
  --upstream-manifest /path/to/sd-ai-command-pack
```

The advisory reports behind, current, or ahead for stable versions. Missing,
offline, malformed, or prerelease references produce a clear "could not
determine/compare" note and do not fail the audit.

Use `--remove` to uninstall pack-owned assets. Removal deletes pack-vouched
files, files that still match the bundled template, generated pack state under
`.sd-ai-command-pack/`, and the pack-managed blocks in `.gitignore`,
`.git/info/exclude`, and `.github/copilot-instructions.md`. Drifted files,
symlinks, directories, and user-owned policy files are preserved by default;
add `--force` to delete drifted regular pack files too, and add `--backup` to
keep `.bak` copies of deleted files.
Receipt and provenance entries do not by themselves authorize deletion:
remove mode ignores paths under `.git/` and non-manifest paths instead of deleting them,
even when their recorded hashes match and `--force` is set.

After installing or refreshing a target repo, a quick smoke test is:

```bash
cd /path/to/repo
SANDBOX_TMP="${SANDBOX_TMP:-${TMPDIR:-/tmp}}"
export PYTHONPYCACHEPREFIX="${PYTHONPYCACHEPREFIX:-$SANDBOX_TMP/sd-ai-command-pack-pycache}"
export UV_CACHE_DIR="${UV_CACHE_DIR:-$SANDBOX_TMP/sd-ai-command-pack-uv-cache}"
export UV_TOOL_DIR="${UV_TOOL_DIR:-$SANDBOX_TMP/sd-ai-command-pack-uv-tools}"
export RUFF_CACHE_DIR="${RUFF_CACHE_DIR:-$SANDBOX_TMP/sd-ai-command-pack-ruff-cache}"
python3 scripts/sd-ai-command-pack-install-audit.py
bash -n scripts/sd-ai-command-pack-full-check.sh
bash -n scripts/sd-ai-command-pack-shell-lib.sh
bash -n scripts/sd-ai-command-pack-toolchain.sh
bash -n scripts/sd-ai-command-pack-review-local.sh
bash -n scripts/sd-ai-command-pack-review-scope.sh
python3 scripts/sd-ai-command-pack-update-spec-kb.py --dry-run
```

## Troubleshooting

- Missing an `sd-*` command: reinstall the pack and include the platform
  adapter for the tool you are using. Claude and Gemini expose these as
  `/sd:<command>`; GitHub Copilot, OpenCode, and Codex expose flat
  `/sd-<command>` entries.
- In Gemini CLI, after reinstalling run `/commands reload` and then
  `/commands list`; the loaded project files should include
  `.gemini/commands/sd/<command>.toml`.
- The update-spec command reports a missing `trellis-update-spec` skill: run
  `trellis update` in the target repo so the Trellis-provided skill files are
  present, then retry the wrapper command.
- `scripts/sd-ai-command-pack-update-spec-kb.py` is missing: reinstall the pack;
  update-spec uses it to rebuild `.obsidian-kb/`.
- Install audit warns about legacy `trellis-*` or `sd-refresh-specs` names:
  migrate those references to the current `sd-*` command names and
  `sd-ai-command-pack-*` scripts, then rerun the audit.
- `scripts/sd-ai-command-pack-full-check.sh` is missing: reinstall the pack; every target
  repo should receive the shared script.
- `scripts/sd-ai-command-pack-housekeeping.sh` is missing: reinstall the pack; every
  target repo should receive the shared script.
- Prism authentication/config failure: configure Prism locally, set
  `SD_AI_COMMAND_PACK_FULL_CHECK_PRISM=0` to skip it, or set
  `SD_AI_COMMAND_PACK_FULL_CHECK_PRISM=required` when review must be mandatory.
- Gito fails due to cache, network sandboxing, or provider rate limiting:
  `sd-full-check` when Gito is explicitly enabled, `sd-review-local`, and
  `sd-review-local` in `all` mode set writable `UV_CACHE_DIR` and
  `UV_TOOL_DIR` defaults
  and retry HTTP 429 / slow-down responses with bounded backoff. If the failure
  is network or credential related, run from an environment with the needed
  access. Leave `SD_AI_COMMAND_PACK_FULL_CHECK_GITO` unset unless Gito is
  configured locally.
- `uvx`, Ruff, Python compile/coverage, preflight, or full-check fail with
  `Operation not permitted` while creating cache or temporary files: export the
  sandbox-local `PYTHONPYCACHEPREFIX`, `UV_CACHE_DIR`, `UV_TOOL_DIR`, and
  `RUFF_CACHE_DIR` block from Configuration, then rerun the same command.
- Root-level `code-review-report.*` files appear after manual Gito runs: the
  managed gitignore block ignores them, but prefer running through
  `sd-review-local` (any scope) or
  `SD_AI_COMMAND_PACK_FULL_CHECK_GITO=1 bash
  scripts/sd-ai-command-pack-full-check.sh` so reports go under the
  pack-managed `.build/review/gito` and `.build/review/gito-all` directories.
- Stale generated cache causes type or build failures: clear the repo-specific
  generated cache and rerun the deterministic check that failed.
