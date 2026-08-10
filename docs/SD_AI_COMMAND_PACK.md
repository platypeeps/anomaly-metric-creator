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
- `.agents/skills/sd-help/references/structured-questions.md`: generated host-neutral
  decision registry, question shape, noninteractive behavior, and authority
  boundaries shared by workflows that need user judgment.
- `.claude/rules/sd-planning-adversarial-review.md` and the lazily loaded
  `.claude/sd-ai-command-pack/planning-adversarial-review.md` contract:
  Claude-only planning-artifact adversarial review with an optional native
  Codex CLI peer lane.
- `.agents/skills/sd-status/SKILL.md`: read-only local repository and
  configured fleet status reporting.
- `.agents/skills/sd-start/SKILL.md`: Codex-visible Trellis start wrapper.
- `.agents/skills/sd-continue/SKILL.md`: Codex-visible Trellis continue wrapper.
- `.agents/skills/sd-finish-work/SKILL.md`: Codex-visible Trellis finish-work wrapper.
- `.agents/skills/sd-create-pr/SKILL.md`: spec-refresh, commit, push, PR
  creation/reuse, and PR-review orchestration workflow; custom Markdown bodies
  are materialized literally and passed to GitHub CLI with `--body-file`, while
  tooling/generated-only auto-filled bodies gain the required scope section
  before either standalone or `sd-ship` review handoff.
- `.agents/skills/sd-work-backlog/SKILL.md`: sequential Trellis backlog work
  loop and canonical resumable controller for planning through clean merge,
  including typed `all` and `needs-design` selectors.
- `.agents/skills/sd-work-backlog/references/autonomous-loop.md`: shared
  planning-quality and artifact-exit contract used by both work selectors.
- `.agents/skills/sd-work-backlog/references/*-recovery.md`: conditionally
  loaded ledger, ownership, stopped/red, and terminal recovery contracts.
- `.agents/skills/sd-review/SKILL.md`: unified exact-scope deterministic,
  local-provider, routed-review, finding-disposition, and exact-head lifecycle.
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
- `.agents/skills/sd-fix-ci/SKILL.md`: red-CI triage and fix loop.
- `.agents/skills/sd-update-deps/SKILL.md`: dependency PR batch triage
  workflow.
- `.agents/skills/sd-test-gaps/SKILL.md`: coverage-driven test authoring
  loop.
- `.agents/skills/sd-retro/SKILL.md`: debug retrospective capture workflow.
- `.agents/skills/sd-ship/SKILL.md`: composite publish-to-merge orchestrator
  chaining create-pr, the routed `sd-review scope=pr` loop, its own Stage 2b
  lifecycle step, watch-pr, and housekeeping.
- `.agents/skills/sd-check/SKILL.md`: typed deterministic read-only verification
  workflow.
- `.agents/skills/sd-full-check/SKILL.md`: full local verification workflow.
- `.agents/skills/sd-housekeeping/SKILL.md`: post-merge cleanup workflow.
- `.agents/skills/sd-update-spec/SKILL.md`: Trellis update-spec workflow plus
  pack-managed repository knowledge refresh.
- `scripts/sd-ai-command-pack-full-check.sh`: canonical full-check script.
- `scripts/sd-ai-command-pack-check.py`: schema-versioned read-only check
  coordinator.
- `scripts/sd-ai-command-pack-review.py`: schema-versioned unified review
  coordinator with private resumable state, router capability discovery,
  durable receipt reconciliation, and declared-channel observation.
- `scripts/sd-ai-command-pack-review-full-check.sh`: deterministic
  `sd-review-pr` selector for a repository-owned `check:full` wrapper or the
  canonical pack-script fallback.
- `scripts/sd-ai-command-pack-shell-lib.sh`: shared Bash helpers sourced by
  the full-check, review-local, and review-scope scripts.
- `scripts/sd-ai-command-pack-toolchain.sh`: non-mutating toolchain doctor and
  deterministic Python resolver used by SD workflows before dependency-sensitive
  checks.
- `scripts/sd-ai-command-pack-audit-route.py`: deterministic repository
  fingerprinting and charter selection for standard and exhaustive audits.
- `scripts/sd-ai-command-pack-audit-inventory.py`: read-only committed-tree
  inventory for architecture audits; ranks regular Git blobs by byte size
  without executing checkout-owned code or opening worktree paths.
- `scripts/sd-ai-command-pack-housekeeping.sh`: canonical post-merge housekeeping script.
- `scripts/sd-ai-command-pack-housekeeping-result.py`: read-only composer for
  schema-versioned housekeeping action, eligibility, and final-status evidence.
- `scripts/sd-ai-command-pack-pr-eligibility.py`: read-only exact-head
  pull-request eligibility evaluator used by the housekeeping merge decision.
  Accepts a schema-versioned JSON request via `--input` or the equivalent
  flags (`--repo`, `--branch`, `--dependency-pr-number`, `--remote`,
  `--default-branch`, `--finish-work-receipt`, `--github-repository`), emits
  the eligibility verdict as `--format json`, `shell`, or `json-shell`, and
  maps the verdict status to its exit code: `0` for `eligible`, `1` for
  `blocked`, and `2` for any other status, including invalid input and
  indeterminate collection failures; it never mutates repository or PR state.
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
  full-codebase mode. Distinct from the similarly named
  `scripts/sd-ai-command-pack-review-local.py`, the internal local review
  stage that `scripts/sd-ai-command-pack-review.py` invokes; the two share a
  base name but do not call each other, and the `.py` is an internal pipeline
  stage rather than an operator entry point.
- `scripts/sd-ai-command-pack-review-learnings.py`: local review feedback
  pattern scanner and managed learning-block updater. It preserves current,
  non-outdated unresolved comments as individual actionable rows, clusters
  historical signals deterministically with bounded evidence, and proposes
  only category-specific actions backed by recurring observations.
- `scripts/sd-ai-command-pack-install-audit.py`: structural post-install audit
  for missing installed targets and unlisted pack-like files.
- `scripts/sd-ai-command-pack-recovery-artifacts.py`: receipt-driven lifecycle
  manager for pack-created Git recovery stashes and worktrees; described in
  detail in the recovery-artifacts section below.
- `scripts/sd-ai-command-pack-surface-check.py`: schema-versioned shipped-surface
  validator the pack source repository invokes from its tracked check
  configuration; described in detail in the surface-check section below.
- `scripts/sd-ai-command-pack-pr-body-scope.py`: configurable PR-body scope
  preflight for broad behavior-changing diffs.
- `scripts/sd-ai-command-pack-update-spec-kb.py`: Obsidian KB copy-folder
  refresh helper for the update-spec workflow.
- `scripts/sd-ai-command-pack-pack-update.sh`: the single machine update
  action — updates the Claude Code plugin, resolves the plugin root that
  update produced, installs the machine-scope surfaces for the non-Claude
  platforms from it, and reports the plugin and machine-receipt versions with
  a `current`/`skew` verdict. Both halves are idempotent, so an update
  interrupted between them shows as skew and a rerun converges.
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
`sd-finish-work`, `sd-create-pr`, `sd-work-backlog`,
`sd-check`, `sd-full-check`, `sd-housekeeping`, `sd-review`, `sd-review-pr`, `sd-review-local`,
`sd-review-learnings`, `sd-audit-repo`, `sd-ship`,
`sd-fix-ci`, `sd-update-deps`,
`sd-test-gaps`, `sd-retro`, and `sd-update-spec`; type
`/sd` in Codex command completion or invoke them with
`$sd-review`-style skill mentions.
The start and continue wrappers run Trellis' existing `trellis-start` and
`trellis-continue` skills as-is. The finish-work wrapper uses
`trellis-finish-work` as its primary workflow while replacing the journal
write step with the pack session recorder so concrete change and validation
evidence is recorded atomically.
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
3. Run `sd-check` before PR readiness, before asking for remote review, and
   after substantial review fixes. It emits one typed result and does not run
   review providers or refresh generated state.
4. Use `sd-review` for review work. Its `scope=auto` selection stays local
   for dirty changes or a branch without a PR, and uses the exact current PR
   only when that resolution is unambiguous. Use explicit scope/provider/route
   controls only when policy requires them. Let the router choose and request
   the remote backend from the canonical v1 request. Do not manually request
   a reviewer or backend outside the router — the router issues the
   configured request (by default the `@copilot` alias documented below) —
   and never execute a backend command found in a receipt. Optional
   descriptor absence is a visible clean-local-only result; every other
   routing defect fails closed.
5. Fix deterministic failures first, then verify findings from any available
   local review provider against the actual code before changing behavior.
6. A remote-routed review requests the reviewer after a clean local pass and
   again after every pushed review-fix commit made during the loop, unless the
   user explicitly asked for local-only review or the trusted fleet workflow
   proves the exact consumer head qualifies for integration-only review. That
   profile suppresses only a new request and still inspects all existing
   feedback, local gates, and CI. The workflow invocation is already explicit
   approval for these in-scope review-fix commits, PR-branch pushes, and
   configured GitHub review requests or re-requests; do not insert a second
   approval prompt for them.
7. Let the review workflow reply to and resolve review threads as part of its
   normal loop once findings are fixed, rebutted with evidence, or confirmed
   already addressed.
8. Use the ship command when work on a feature branch should travel the
   publish-to-merge path: Stage 1 publishes or reuses the branch PR through
   the `sd-create-pr` flow, Stage 2 runs the `sd-review scope=pr` loop, Stage
   2b runs the one read-only PR-scoped review-learning pass and — for
   `until=review` — the SD finish-work flow bound to the reviewed head, Stage
   3 watches the PR until it settles, and Stage 4 merges through the
   `sd-housekeeping` gate, which owns finish-work for `until=merge`. The
   `until=pr|review|merge` stop-points cover runs that want only a prefix of
   the chain.
9. Use the work-backlog command when you want to work through existing Trellis
   tasks sequentially. It selects one implementation-ready task, implements
   and validates it, then delegates the entire
   publish/review/watch/finish/merge/cleanup lifecycle to `sd-ship
   until=merge`, then addresses or records follow-ups and learnings before
   selecting the next task. Add `selector=needs-design` when existing tasks
   still need `design.md` or `implement.md`, and `until=design` to stop after
   validating those planning artifacts.
10. Use the review-learnings command when review comments repeat across PRs and
   you want to capture repo-specific preventive guidance. It scans read-only by
   default. Repository-local persistence requires explicit `--update` and an
   atomically replaceable canonical target; an external target requires
   `--update-external`, exact-path structured confirmation, and the matching
   `--confirmed-external-target`. The command never stages, commits, pushes, or
   publishes the learning file. An `sd-ship` chain automatically attempts one
   read-only, PR-scoped learning pass (Stage 2b) after the overall review
   cycle is clean; it never runs the learning pass after each round.
11. Run the update-spec command when the work taught you a durable
   implementation contract or convention. It runs the existing update-spec skill
   and also checks whether an existing architectural overview needs to be
   updated.
12. Run the finish-work command when the coding session is complete and you need
   the Trellis finish-work skill's quality gate, archive, journal, and commit
   reminder behavior. An `sd-ship until=review` chain runs this flow itself in
   Stage 2b. Lifecycle commands must chain through `sd-finish-work`
   rather than invoking Trellis directly so the pack's concrete session
   recorder remains in the path. Every acceptance criterion is satisfied before
   archive; merge and cleanup are the task's `Post-archive handoff`, never
   unchecked acceptance criteria. The shared boundary and authoring examples live
   in `sd-help/references/completion-lifecycle.md`. The read-only `pre-archive`
   bookkeeping validator enforces this: a task whose canonical `Acceptance
   Criteria` section still has an unchecked required item fails closed with a
   stable `pre_archive_acceptance_incomplete` reason before Trellis mutates
   anything, and malformed, duplicated, or checkbox-shaped handoff structures
   fail with `pre_archive_acceptance_malformed`. Prose `Post-archive handoff`
   bullets and unchecked boxes outside the canonical section are never mistaken
   for incomplete criteria.
13. After the PR merges, run the housekeeping command to get back to the default
   branch, prune/delete the merged development stream, and see the condensed
   clean-state/anomaly report. An `sd-ship until=merge` chain already ran it
   as Stage 4.

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
whether to keep going.

Once an `sd-ship` chain's review loop meets its stop conditions, Stage 2b runs
`sd-ai-command-pack-review-learnings.py --github-pr <number> --dry-run`
exactly once and reports any preventive follow-up without reopening the clean
review cycle. Time-window and repeated `--github-pr` scans render actionable
comments first, then bounded historical clusters for task metadata, boundary
validation, contract/documentation drift, generated surfaces, reviewer/test
harness quality, and uncategorized evidence. Cluster summaries retain counts,
PRs, path families, observed dates, and bounded examples, and explicitly report
truncation. Preventive actions appear only for detected recurring categories.

The managed block is rendered wholesale from whatever GitHub scope the run
requested, so a narrowly scoped run renders a block holding only that scope's
clusters. Stage 2b's `--dry-run` never writes, but the same `--github-pr`
invocation combined with `--update` would replace a repository-wide snapshot
with one PR's signals. An update that would delete clusters already recorded in
the snapshot is refused and names them; `--allow-narrowing` accepts the
deletion deliberately.

The routed-review workflow may invoke the same scanner once per attempt with
`--planning-attempt ID --json`, an explicit `--github-repo`, and either a
bounded `--github-days` window or repeated `--github-pr`. The schema-version-1
receipt exposes bounded normalized historical clusters and only the categories
applicable to the changed paths. Full raw review bodies are omitted, and the
signal is advisory evidence with zero confidence credit. An optional
`--review-artifact-root` must be absolute, private, current-user-owned, and
outside the repository; exact request receipts are atomically reused until the
bounded TTL expires. Stale, truncated, corrupt, rate-limited, or unavailable
evidence is reported visibly and never authorizes tracked-file or GitHub
mutation. The signal separately reports the durable managed snapshot as
current, stale, missing, or unknown and recommends explicit curation only when
newer evidence exists or the snapshot is missing. Failed scans also produce a
bounded reusable attempt receipt, preventing repeated provider reads during a
degraded attempt.

The create-pr wrapper honors `SD_AI_COMMAND_PACK_CREATE_PR_BASE` for a base
branch override, `SD_AI_COMMAND_PACK_CREATE_PR_COMMIT_MESSAGE` when it creates
a commit without a user-provided message, and
`SD_AI_COMMAND_PACK_CREATE_PR_DRAFT=1` when the PR should start as a draft.
Inside an `sd-ship` chain it publishes and returns; only a standalone
invocation still hands off to the transitional review-pr loop after PR
creation or reuse.

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

A task blocked on an external dependency is machine-visible through one shared
convention: a `PARKED:` title prefix (the same marker the status board reads),
an explicit `blocked`/`blockedOn` field, or a park note. The `rank` helper flags
each candidate `blocked` with a reason, reports `actionableCount`, and sorts
every blocked task after every actionable one, so a blocked `P0` never outranks
an actionable `P3`; the controller selects the first non-blocked candidate and
reports the rest with the reason each was skipped. An optional integer `order`
field breaks ties within a priority band while the `prd.md` keeps the ordering
nuance. When no actionable task remains, the loop stops with
`all_remaining_tasks_blocked` instead of picking a blocked one.

Lifecycle phases and mutable evidence are separate contracts. `transition`
advances a phase, while the helper's `evidence` subcommand records verified
same-phase commit, PR, review-fix, finish-work, and merge facts atomically.
Task and base branch are stable iteration identity; commit ancestry, PR
identity, and the final feature-to-base branch switch are validated locally.
Verified same-phase reconciliation uses the same rules. A checkpoint is an
overlay that retains its owning lifecycle phase in `checkpoint.resumePhase`,
instead of becoming a synthetic later phase; its human target remains intact.
Recovery through an evidence update or reconciliation must supply every
non-null field in the recorded current-state ledger. A complete verified
forward recovery advances evidence and phase atomically, clears the checkpoint,
and remains amber until exact reconciliation turns green. Partial evidence and
identity, PR, Git, branch, or regression conflicts stay red without a partial
update. Legacy schema-v1 ledgers use a phase-valued target as the owner, or
require explicit `--resume-phase` when the target is human-only.

`sd-work-backlog selector=needs-design` carries matching tasks from planning
through a green merge. Adding `until=design` stops after planning and ends with
numbered links to every planning artifact it created or updated. Focus composes
with the selector rather than replacing it.

The help command is a read-only orientation surface. Use bare help for a
compact lifecycle tour, `all` for the complete catalog, an exact command for
an explanation, two or more commands for a comparison, or an ordinary-language
goal for a recommendation. It distinguishes commands available in the current
session from bundled-but-undiscoverable, source-checkout-only, and external
skills. It reports observed version information honestly and never executes,
delegates to, or mutates state on behalf of the selected command.

## Commands

Use the platform-native command when available.

Generated command, prompt, and workflow adapters run a capability-driven
checkout-trust preflight before resolving repository skills or executing
checkout-owned scripts, hooks, package tasks, provider adapters, or
command-bearing configuration. Same-repository PRs and unambiguous local
branches continue normally. Fork PRs stop as `untrusted`; detached, unreadable,
unavailable, or contradictory repository identity stops as `indeterminate`.
Both blocked states report a stable reason code and safe maintainer-run or
trusted-base inspection guidance; user approval cannot bypass the stop.
`sd-help` is the sole initial `trusted_static_only` exemption and remains
non-executing and read-only. Command reports include the selected
`checkout-trust` state and reason.

At declared unresolved decision boundaries, adapters also apply the generated
portable structured-question contract. Claude uses `AskUserQuestion`; other
hosts use a native structured capability only when available and otherwise ask
one concise plain question or apply the decision's stop, park, or report-only
behavior. The contract does not add confirmations for routine actions already
authorized by the invocation. For publication and review workflows, that
standing authority includes in-scope commits, pushes to the current PR branch,
and configured GitHub review requests or re-requests; do not ask for another
approval solely to send the diff/code through normal GitHub review. No answer
can override a safety gate.

Claude Code and Gemini CLI:

```bash
/sd:help
/sd:status
/sd:start
/sd:continue
/sd:finish-work
/sd:create-pr
/sd:work-backlog
/sd:check
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
/sd-check
/sd-full-check
/sd-housekeeping
/sd-review-pr
/sd-review-local
/sd-ship
/sd-review-learnings
/sd-audit-repo
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
bash scripts/sd-ai-command-pack-toolchain.sh run-python -- \
  scripts/sd-ai-command-pack-check.py --json
bash scripts/sd-ai-command-pack-full-check.sh
bash scripts/sd-ai-command-pack-review-local.sh
bash scripts/sd-ai-command-pack-review-local.sh --full-codebase
bash scripts/sd-ai-command-pack-housekeeping.sh --json # typed cleanup result
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
pack recovery-artifact classifications, anomalies, complete selectable
F-prefixed follow-ups and T-prefixed unarchived
tasks, and numbered next steps. Task-like items in bounded roadmap sources are
reported as source-backed F-prefixed follow-ups only when no unarchived Trellis
task represents them. Sources are limited to roadmap/backlog/TODO/program-design
or implementation-plan Markdown/text files and files below `roadmap/`,
`proposals/`, or `rfcs/`; unchecked boxes count at any indentation while
ordinary list items count only at the top level. Empty selectable sections remain visible as `none`. The
ordinals are deterministic for an unchanged snapshot but remain report-local;
durable Trellis task IDs are included in every task row. Loop state includes run ID,
mode/selector/focus, iteration, phase, task, branch/head/base-branch evidence,
PR identity, last shipped SHA, counters, heartbeat, context health, checkpoint,
lock, and stop reason. Reading it never refreshes the ledger or lock. The status
adapter accepts terminal `none`, `invalid`, and
`unavailable` snapshots plus complete `active`, `paused`, `stopped`, and
`completed` run snapshots. Missing, unsupported, or incomplete helper results
become bounded `invalid` anomalies without echoing helper-controlled values. A
positional path selects another checkout, so
`sd-status /path/to/repo` is equivalent to
`sd-status --repo /path/to/repo`.
`--no-network` suppresses GitHub calls and `--json` emits schema version 2. Ordinary runs do
not fetch and label ref-derived values `cached`. Relevant-PR review totals use
GitHub's GraphQL `reviews.totalCount`, so repositories with more than one REST
page of review events are reported accurately without fetching every review.

`scripts/sd-ai-command-pack-recovery-artifacts.py` owns the lifecycle of
pack-created Git recovery artifacts — the stashes and worktrees a workflow makes
to protect uncommitted work before a risky operation. Each artifact carries a
versioned, user-local, owner-only receipt keyed by repository identity and a
unique artifact ID; cleanup acts only through receipts, so an artifact with no
receipt is never touched. A creating workflow `register`s the receipt atomically
the instant after the artifact exists and, on the success path, retires its own
artifact and receipt in a `finally` through `cleanup --mode owner --artifact-id`;
an interruption preserves both for recovery. `sd-status` classifies every
artifact read-only as `active`, `safe-cleanable`, `needs-review`,
`missing-artifact`, or `unowned-artifact`, and moves nothing. `sd-housekeeping`
is the sole general cleanup owner: `cleanup --mode housekeeping` retires only a
stash proven redundant or superseded at its exact object, or a worktree clean at
its exact registered path with a matching common directory, no lock, and a
reachable or retained head, and preserves every ambiguous, `needs-review`,
missing, or foreign artifact. Housekeeping surfaces retired artifacts as actions
and refused or failed retires as anomalies, never prunes receipts, and never
forces a removal. The receipt JSON is bounded and exposes no secrets, remote
URLs, or raw filesystem errors. The shared ownership lifecycle is documented in
`.agents/skills/sd-help/references/recovery-artifacts.md`.

The optional positional `fleet` mode works from any installed checkout. It
resolves the canonical fleet manifest from `--fleet-manifest`,
`SD_AI_COMMAND_PACK_FLEET_MANIFEST`, the machine-local fleet profile, or the
canonical source checkout, in that order. It preserves rollout priority,
reports missing checkouts, compares installed versions to the source manifest
version, and returns one bounded row per fleet member plus F-prefixed fleet
follow-ups. Complete per-consumer follow-up and task records remain available
in nested JSON or through local status for that checkout. A dirty, stale, missing,
behind, or diverged repository is advisory in ordinary status; the command
remains read-only and exits zero after producing the report. Invalid
repositories and malformed, missing, or stale fleet configuration exit
nonzero. This repository-status command is separate from `install.py --status`,
which compares one target's installed payload to the current pack.

`sd-check` runs `scripts/sd-ai-command-pack-check.py` through the installed
Python toolchain. Its schema-version-1 JSON contains ordered result rows,
aggregate status/exit, exact HEAD observation, configuration presence, and a
before/after state guard. Statuses are `passed`, `failed`, `skipped`,
`unavailable`, `invalid`, and `indeterminate`; only aggregate `passed` plus a
passing state guard exits `0`.

Repository-specific deterministic commands use the tracked
`.sd-ai-command-pack/check.json` file:

```json
{
  "schemaVersion": 1,
  "prerequisites": [
    {
      "id": "tooling",
      "argv": ["python3", "scripts/check-tooling.py"],
      "cwd": ".",
      "timeoutSeconds": 120
    }
  ],
  "checks": [
    {
      "id": "unit",
      "argv": ["python3", "-m", "unittest", "discover", "-s", "tests"],
      "cwd": ".",
      "timeoutSeconds": 900
    }
  ]
}
```

The schema is closed and validated completely before execution. It rejects
shell strings, inline shell/code commands, provider and GitHub review
executables, non-read-only Git operations, duplicate IDs, path escapes,
malformed argv arrays, unknown schema versions/fields, and timeouts outside the
documented bound. Missing configuration is valid and runs only built-ins. A
failed prerequisite visibly skips later configured checks; a missing declared
tool is `unavailable`, never a pass.

Built-ins run staged/unstaged whitespace checks plus the installed review
preflight, payload audit, Obsidian KB `--check`, tooling/generated scope, and
PR-body scope helpers when applicable. The command never refreshes stale
knowledge, runs Prism/Gito/Copilot/routed review, mutates Git/GitHub, or reads
the legacy full-check environment/package-hook contract. Every subprocess uses
the shared external cache environment. If repository, index, ref, generated
knowledge, or guarded cache state changes, the state guard fails and reports
the changed class without reverting user or tool output.

In the pack source repository, the tracked check configuration also invokes
`scripts/sd-ai-command-pack-surface-check.py`. That schema-version-1 validator
uses the canonical registry and manifest to compute the affected shipped
surface across committed, staged, unstaged, and non-ignored untracked paths.
It distinguishes installable, generated, explicitly source-only,
documentation-only, check-only, retired, and release-evidence nodes. Missing
or stale relations fail with exact repository-relative paths and the owning
preparation command (`make generate`, `make sync`, a manifest entry, or an
explicit `SOURCE_ONLY_SKILL_REFERENCES` declaration); the validator never runs
those preparation actions itself. The local pre-publication gate and CI use
the same helper rather than reconstructing the policy from separate globs.

The independent `sd-full-check` surface remains available only during the
clean-interface migration. New deterministic callers do not invoke or alias it.
Its script runs `git diff --check`, `git diff --cached --check`,
review preflight through `scripts/sd-ai-command-pack-review-preflight.mjs`, any
configured `SD_AI_COMMAND_PACK_FULL_CHECK_REVIEW_PREFLIGHT_COMMAND`, and the
legacy repo-local `scripts/check-review-preflight.mjs` when present. It then
runs the post-install audit, the tooling/generated file scope preflight, the
PR-body scope preflight, current-diff CI classification when
`scripts/classify-ci-changes.sh` exists, optional package-script checks when a
`package.json`, Node.js, and the selected package runner are available, and
local Prism review when `prism` is available and configured. The Prism lane is
local-first: when tracked staged or unstaged changes exist, it reviews each
non-empty Git layer and skips the committed branch range; otherwise, it reviews
the branch range from the configured base. This avoids repeating committed
review work during iteration without dropping either local diff layer. For
target repos
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
hashes still match. The install audit and `provenance.json` vouch pack-owned
receipt targets only — the files the installer wrote and recorded in
`installed-targets.txt`. When a consumer relaxes its ignore policy so a
Trellis-owned platform adapter becomes newly tracked, that adapter is not added
to the pack manifest, `installed-targets.txt`, or `provenance.json` to widen the
vouch; it stays outside the pack-vouched set and is covered instead by the fleet
review classifier's integration-only eligibility — which forces the normal
remote-review loop for any changed path missing from the receipt — and by the
consumer's own integration and readiness checks.
The copied/generated scope preflight reads
`.sd-ai-command-pack/installed-targets.txt`, reports changed pack/Trellis
runtime files, known repository-map files when present, and Trellis workspace
journal/index files as integration-only review surface. Reporting a path here
marks it for review attention; it never extends the pack audit's vouch to a
Trellis-owned adapter. When the GitHub CLI can
resolve a current PR, it checks that the PR body includes a
`Tooling/generated scope:` section before review cycles spend attention on
copied or generated surfaces. Markdown headings without the colon, such as
`## Tooling/generated scope`, are accepted too. In CI or local preflights where
`gh pr view` should not run, pass the PR body through
`SD_AI_COMMAND_PACK_SCOPE_PR_BODY`.

The `sd-create-pr` no-custom-body path reuses the same classifier through
`sd-ai-command-pack-pr-body-scope.py --prepare-tooling-body`. It captures the
exact auto-filled GitHub body and NUL-delimited `base...HEAD` paths in secure
regular temporary files. A fully tooling/generated or Trellis-bookkeeping diff
gets the recognized section through `gh pr edit --body-file`; exit `3` means a
mixed or empty diff and leaves the body unchanged. Other failures stop before
review. User-provided bodies never enter this preparation mode and remain
byte-for-byte subject to the existing validator.

The review preflight is intentionally generic and safe to run without project
dependencies. `sd-create-pr` runs it before staging, committing, or pushing so
known task and context defects cannot be published for later review to find.
It checks for duplicate npm override sources of truth, changed
copied Trellis or SD command-pack surfaces without companion repo-owned
integration context, personal absolute paths in docs/prompts/specs, missing
repo path references in docs/prompts/specs, completed Trellis journal
placeholder or journal/index commit drift, generated `_example` seed rows and
non-spec/non-research file references in changed task context, edits to historical
journal sessions relative to the review base, and large diffs that are likely
to skip remote AI review. It also emits a soft first-review warning when changed
production code matches the stable `structured-input-types`,
`subprocess-command`, `environment-global-state`, `path-filesystem`,
`normalization-evidence`, or `diagnostic-redaction` categories. Every triggered
category includes bounded good/base/failure regression prompts for author
disposition; detection remains advisory and does not claim that focused
coverage exists or is missing. Diff sizing uses the complete review-base-to-
working-tree state plus untracked files; large untracked files are counted as
large without reading the entire file. The same byte limit bounds the first-
review boundary-risk content scan; skipped
oversized untracked code files are named in an explicit warning. Conventional
test and fixture paths, vendored/generated directories, installed payload
mirrors, and non-workflow YAML remain outside the production-risk scan, while
`.github/workflows/*.yml` and `.yaml` participate as executable configuration.
The authored-source threshold excludes installed pack/Trellis mirrors, Trellis
task and workspace records, and known generated reports. A separate warning calls
out changes spanning more than one Trellis task directory. The task-context
check inspects `implement.jsonl` and `check.jsonl`; a changed non-planning
`task.json` also checks both sibling files. A planning task's untouched
generated scaffold — a single row parsing to an object whose sole key is
`_example`, the shape `task.py create` writes — is exempt, so creating a task
never fails the gate; the scaffold must be replaced or emptied before the task
leaves planning. The bookkeeping validator's `task_context_seed` check exempts
it on identical terms, so neither lane fails a freshly created task. The match
is on that shape, not on Trellis's exact seed text, which Trellis owns and
changes across versions. A scaffold row that survives alongside authored rows,
carries extra keys, or appears in any non-planning or archived task still
fails; grounded planning manifests pass, while untouched legacy archives are
skipped. The two lanes diverge on unsafe artifacts: the diff-scoped gate skips
a symlinked context file, while the bookkeeping validator reports it as
`task_context_invalid` rather than skipping it.
Grounded rows may reference only `.trellis/spec/**` or
`.trellis/tasks/**/research/**`. Journal history is append-only: newly
added/current sessions remain editable, but an older session must be restored
and the intended current session edited by its explicit `## Session <n>:`
heading. A separate repository-wide task-location check fails when a direct
child of `.trellis/tasks/` has completed status, names the offending record,
and provides the Trellis archive command; archived, non-completed, and
symlinked task entries are ignored. Target repos can tune roots,
path-reference prefixes, integration paths, optional paths, copied-template
paths, and the `copilotReviewFileLimit`, `diffSizeWarningLines`, `largeFileWarningLines`,
`sourceReviewWarningLines`, and `untrackedFileReadLimitBytes` warning thresholds
with `.sd-ai-command-pack/review-preflight.json`. The config's additive
`reviewRiskCategorySignals` object maps a stable category ID to at most 20
literal, nonblank signals of at most 120 characters each; invalid or unknown
category configuration fails the preflight instead of silently changing the
matrix. `copilotReviewFileLimit` defaults to `300`, accepts only a positive
integer, and warns before remote review when the selected local diff exceeds
GitHub Copilot's changed-file limit. Repos that intentionally
document service-user paths under `/home/<user>/` can add those service users to
`allowedLinuxHomeUsers` in that config. The script requires Node 22 or newer
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

When `sd-review-local` runs through its Claude Code adapter, normal
current-diff review also adds the native Codex CLI as a concurrent peer lane.
Dirty working trees use `codex review --uncommitted`; clean-tree branch review
uses `codex review --base <resolved-ref>` with the same base selected by the
shared skill. The adapter checks for the `codex` executable before probing the
CLI and required flag, then collects both the Codex and runner results even
when one fails and verifies/deduplicates their findings before asking what to
fix. A missing executable, failed help probe, or incompatible Codex CLI skips
that optional lane visibly while the selected runner stack continues normally;
a runner-only result may still be clean. A Codex review that starts and then
fails makes the combined review incomplete, not clean.

This Claude lane calls the supported `codex review` CLI directly. It does not
require, inspect, install, or patch the OpenAI Codex Claude plugin; that plugin
may be installed or uninstalled independently. The Codex CLI itself must remain
installed and authenticated. Native Codex review has no repository-wide target,
so `sd-review-local all` runs only the configured full-codebase runner providers
and reports the Codex scope limitation rather than mixing scopes.

### Unified routed review

`sd-review` invokes `scripts/sd-ai-command-pack-review.py` through the shared
toolchain resolver. The schema-version-1 coordinator resolves one canonical
scope, runs the typed `sd-check` gate, consumes the exact-target local review
receipt, and uses the repository's released router descriptor for PR-only
remote work. Its normalized controls are:

```text
scope=auto|changes|branch|codebase|pr
local=auto|all|none|<configured-provider-id>
remote=auto|cheap|deep|copilot|none
fix=auto|ask|none
pr=<positive-number>
attempt=<positive-number>
```

The optional `.sd-ai-command-pack/review.json` `remoteIntegration` object
accepts only `requirement` (`optional|required`), a repository-contained
`descriptorPath`, and bounded `receiptPolls`, `pollSeconds`, and `roundLimit`
integers. Unknown keys, unsafe paths, and out-of-range values are invalid in
both the controller and the local-stage parser, so one configuration digest
binds local and remote policy.

For PR scope the setup descriptor must be regular strict JSON, contract major
1, checkout-free and noninteractive, support the requested route and `route`
operation, pin an immutable `platypeeps/sd-github-review` action commit, and
declare the `sd-github-review/receipt` Check Run. The live GitHub workflow path,
name, and active state must match. Capability is reported as `ready`, `absent`,
`invalid`, `incompatible`, or `unavailable`.

The controller stores private atomic coordination state outside the checkout,
persists the canonical request before `workflow_dispatch`, and queries the
durable exact-head receipt before and after dispatch. An uncertain mutation is
`reconciliation-required`; it is never retried through a direct provider
fallback. Receipt-declared review authors, check names, and finding channels
bound GraphQL review-thread, review, conversation, and CI observation. A
successful request alone is not review completion: the declared channel must
materialize on the exact head.

When integration is optional and the descriptor is absent, only a clean local
receipt may complete, with `router-not-configured` and
`zero-remote-confidence` limitations. Explicit or required routing, invalid or
incompatible setup, provider failure, malformed receipts, stale heads, and
ambiguous dispatch fail closed. The successor never calls `sd-review-local`,
`sd-review-pr`, or GitHub's reviewer API directly.

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

Pack-owned shell and Python entry points use one shared environment builder for
XDG/GitHub CLI, Python, uv, pip, Ruff, and npm cache state. The builder creates
a private per-user/per-repository namespace outside the repository while
leaving GitHub configuration and authentication paths unchanged. When Gito
reports provider rate limiting through an explicit
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
pack/Trellis generated and bookkeeping files, housekeeping automation files,
and CI/review tooling files for matching `Tooling/generated scope:`,
`Automation scope:`, or `CI/review scope:` sections when a PR body is provided.
Target repos can add
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

When that work creates or materially updates a workflow, architecture,
sequence, data-flow, lifecycle/state, or similar technical visual that belongs
in the repository documentation, the architecture extension prefers the
`archify` skill when it is available. Archify supplies the matching renderer
and deterministic validation/delivery workflow while the target repository
continues to own the document format, artifact location, and naming. Archify is
not a required pack dependency: if it is unavailable, update-spec continues
with documented repo-native visual tooling or the existing manual format and
reports the fallback. No visual is created merely because Archify is present.

The canonical update-spec skill keeps routine Trellis delegation, extension
ordering, the normal KB command, safety, and final reporting inline. It loads no
optional reference for a routine spec-only pass. Existing repository-map
infrastructure, applicable architecture changes, explicit preview, unusual KB
paths, or helper failures select only the matching direct reference under
`.agents/skills/sd-update-spec/references/`; independent extensions may load
more than one, but references never chain.

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
Each generated copy ends with a trailing `<!-- SD-AI-COMMAND-PACK:KB-COPY -->`
provenance marker, and the prune deletes a plain file in a category folder only
when the file ends with that marker, so files the pack never wrote — including
notes that merely quote the marker text — survive refreshes even when the KB
root symlink points into a personal vault whose folders share a category
title. Copies written by older pack versions carry no marker; a
refresh rewrites them with one while their source exists, and copies orphaned
before the upgrade are left in place for manual cleanup.
The root `.obsidian-kb` path may itself be a symlink when it resolves to an
existing directory, including a directory outside the repository. Refreshes
preserve that root symlink and write through it. A broken root symlink, a root
symlink to a non-directory, or an occupied non-directory path fails before KB
or ignore writes. The managed, root-anchored `/.obsidian-kb` ignore rule covers
both a real directory and the root symlink without hiding nested paths of the
same name.
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
any mode only when an intentional guarded caller should refresh repositories
that already have
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
housekeeping script with `--finish-work-receipt "$FINISH_WORK_RECEIPT"`. The
private JSON receipt is valid only after that lifecycle step and its required
checks finish; eligibility independently recomputes its exact mode/base/head
evidence. Without it, or if the branch advances afterward, the executable
leaves an open PR unmerged. The script then checks a strict auto-merge gate:

- the working tree is clean
- the local branch head, remote branch head, and PR head all match
- the PR is open and not draft
- the base is the default branch
- merge state is clean
- at least one executed check succeeded and none are blocking: pending, or any
  conclusion other than success, skipped, or neutral (for example failed,
  cancelled, or timed out). Classifier-skipped checks do not block.
- there are no unresolved review threads

The script resolves one bounded PR identity and lifecycle state for the current
branch before choosing work, then routes on that state so housekeeping stays the
sole owner of the merge-then-cleanup transition. An **open** PR must pass the
gate above to merge; after the attempt the script re-resolves the PR and cleans
up only if the merge actually landed, otherwise it records one anomaly and
leaves the open branch untouched. A **merged** PR skips the eligibility gate and
is cleaned up directly from the already-resolved identity: after fetch/prune of
`origin`, confirm the local branch head matches the merged PR before deleting
it, switch to the default branch, fast-forward from `origin`, and delete the
merged local and remote branch. A **closed** (unmerged) PR, or an indeterminate
lifecycle state, stops with a single bounded anomaly and no merge, switch, or
delete. The script then invokes the installed `sd-status` collector in strict mode, passing
the default/source branches, remote-branch policy, cleanup anomalies, and a
`refreshed` label after a successful fetch/prune. That shared collector owns the
final Git verification, pack/Trellis versions, relevant PR/review count,
repo-wide open PRs/issues, Trellis inventory, anomaly list, and numbered next
steps. Repo-wide inventory remains context rather than a cleanup blocker.

Pass `--json` to reserve stdout for one schema-version-1 housekeeping result;
progress and diagnostics move to stderr. The result embeds the existing PR
eligibility JSON unchanged, stable coded actions/anomalies, and the complete
delegated `sd-status --json` report. Its final `outcome.verdict` is
`clean|blocked|indeterminate|failed` (the `outcome.status` alias emits the same
value for one deprecation release and is dropped in 0.66.0). When an environment
or authority boundary
refuses a Git-metadata or KB-refresh write, the result also carries an additive
`environmentBlocks` array of `environment_blocked` fragments — each naming the
exact boundary, last verified checkpoint, mutation state, and a bounded,
non-authoritative recovery action — without changing `outcome`; consumers that
do not understand the array ignore it. The read-only
`sd-ai-command-pack-housekeeping-result.py` helper validates and composes these
documents but collects no Git/GitHub evidence and owns no mutation.

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

==> Follow-ups
none

==> Tasks
T-1 [planning, P1]: <title> (<durable-task-id>; <task-path>)

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
It is charter-driven: one read-only reviewer per selected dimension, with the
charters installed at `.agents/skills/sd-audit-repo/charters/`. The shipped
`scripts/sd-ai-command-pack-audit-route.py` helper deterministically records
repository fingerprints and every charter's applicability before dispatch.
Charter evidence collection is static-only: it must not execute repository
targets, scripts, hooks, package tasks, Make expansion, or application help
handlers. Architecture reviews use the shipped
`scripts/sd-ai-command-pack-audit-inventory.py` helper to inspect committed Git
tree metadata safely, including repositories with spaces, tabs, newlines, or
leading dashes in valid filenames.
The pipeline is fixed and ordered:
applicability preflight → dimension reviews → adversarial verification → synthesis → Trellis reconciliation → report + ledger.

Arguments: bare exact charter names such as `security testing`, or the explicit
`dimensions=<a,b,c>` form, add charters; unknown names are an error, not a silent skip.
`depth=standard` (the default) always runs correctness, security,
testing, tooling, and release-hygiene, then selects optional charters from
visible file, manifest, dependency, language, infrastructure, datastore, API,
UI, and deployment evidence. `depth=exhaustive` runs every charter and is the
reference for release, security, or policy-required assurance. Classification
failure falls back to exhaustive rather than silently shrinking coverage.
`follow-up` re-verifies open ledger items against the current tree instead of
sweeping the whole repository.

Every audit report contains six mandatory sections — Verdict, Findings,
Trellis reconciliation, Prioritized actions, Ledger delta, and
Coverage & limits — and empty sections state their emptiness explicitly
instead of disappearing. Coverage enumerates every charter as
`run|not-applicable|not-selected|failed`, with its reason and evidence, and a
standard report never claims exhaustive assurance. Findings carry fixed
scores: severity P0–P3, effort S/M/L, confidence Verified or Plausible.

Audit findings persist in the committed ledger at `.trellis/audit/ledger.md`.
The orchestrator assigns monotonic `A-NNN` finding IDs that are never reused,
keeps `fixed` entries as history, marks a reappearing fixed finding
`regressed` under the same ID, and preserves human-edited `notes:` lines.
The audit never creates Trellis tasks on its own: untracked P0–P2 findings
become prd-ready task proposals that wait for explicit user consent.

`sd-audit-repo` complements `sd-review-local` (provider loop),
`sd-review` (routed review), and `sd-full-check` (gate); it is the periodic
formal audit, not a per-change review loop.

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
deciding which consumers are stale. It runs manifest-defined canaries
sequentially, then may overlap isolated post-canary consumer lanes within the
configured bound. The source-only fleet controller validates the immutable
release, manifest and checkout identity; issues each bounded action once; and
records exact release, consumer, attempt, head, PR, result, blocker, and next
action receipts. After interruption, issued side effects enter reconciliation
instead of being replayed. Housekeeping merges remain serialized in manifest
order. Before review it
runs the source-side fleet review classifier against the exact pre-refresh base
and current head. A verified release/candidate ledger, exact installer
inspection and audit, safe base/current receipts, and an installer-only diff
select integration-only review; ambiguity or consumer-owned changes select the
normal configured remote-review loop. Bare consumer
names such as `loadsmith rwbp-website`, or `consumer=a,b`, filter the run;
`no-merge` stops before merging, `dry-run` reports preflight only, and
`remote-review` forces normal remote review, while `remote=<name>` selects the
release-authority Git remote (default `origin`).
Invoking normal merge-capable mode is standing approval for every eligible,
controller-issued consumer housekeeping merge, including after in-scope
review findings are addressed; the workflow does not ask again before those
serialized merges. This changes no gate: exact-head review, CI, thread state,
finish-work, housekeeping eligibility, and post-merge verification must all
pass. `no-merge` is the explicit opt-out when the operator wants PR-open
completion instead of an end-to-end rollout.
In `no-merge` mode the source scheduler accepts PR-open canaries as settled,
holds all merges, and emits no merge candidate; normal mode still requires
canaries to be at-target or merged.
Before it inventories consumers, preflight requires the matching local and
remote `v<version>` tag identities, tagged version and payload, ancestry, and
tagged plus current full-fleet candidate ledgers to agree. Missing, stale,
mismatched, or rewritten release identity fails before mutation. Unknown
consumer names also fail before mutation rather than broadening to the fleet.
The report is a per-consumer status table plus a fleet version summary.
Its consumer rows state `integration-only`, `remote`, or `n/a` review profile
so avoided remote-review rounds remain visible rather than implicit.

The source-only `scripts/sd-ai-command-pack-fleet-controller.py` owns campaign
planning, next-action issuance, normalized receipts, status, validation, and
resume. It composes `sd-ai-command-pack-fleet-wave-plan.py` internally, never
shares a mutable checkout between lanes, stops new starts and holds merges for
a verified pack-owned blocker, and never exposes scheduler state as a public
adapter argument. State is private and atomic outside repositories. A wrong
release/consumer, duplicate side effect, skipped transition, stale PR head,
changed manifest, or invalid concurrent start fails closed.

The fleet skill also records mandatory internal timing evidence with
`scripts/sd-ai-command-pack-fleet-timing.py`. One resumable run brackets
preflight and the fixed checkout, install, audit, local-gate, commit/push, PR,
reviewer-wait, CI-wait, housekeeping, and post-merge-audit stages. Reviewer and
CI waits start together after PR creation, so the report measures their overlap
and interval-union active time instead of double-counting concurrent waits.
The final summary includes critical path, summed stage time, slowest consumer,
slowest stage, overlap, and retries. State is private and atomic in the user's
local state directory; durable records and reports omit repository paths,
credentials, command output, and review text. Timing has no public fleet
argument and never changes an authoritative delivery-gate result. A telemetry
error remains visible and pauses new mutation until the last valid record can
resume.

Every verified rollout finding is also classified before watch, merge, or the
next consumer mutation with the source-only command:

```bash
bash scripts/sd-ai-command-pack-toolchain.sh run-python -- \
  scripts/sd-ai-command-pack-fleet-finding-classify.py \
  --input <temporary-findings.json> --json
```

Schema version 1 requires a non-empty `findings` array. Each row contains a
safe unique `id`, `contractFamily`, `summary`, `evidence`, and `reviewer`, plus
optional repository-relative `path` and positive `line`. Correctness,
security, install/audit, and compatibility block by default. Hardening, style,
test implementation, documentation, diagnostics, and consumer-unrelated work
defer by default. `impact: blocker` requires concrete `impactEvidence`; an
explicit `overrideDisposition` requires `overrideRationale`. No public adapter
argument or environment variable can downgrade a blocker.

Exit `0` means continue only after every observation receives an evidence-
backed reply, allowed thread resolution, and one follow-up per canonical owner
when work remains. Exit `1` pauses all further fleet mutation and batches
blocker owners into one corrective campaign. Exit `2`, malformed output, or an
unavailable command fails closed. Exact duplicate observations share their
first owner's timing decision and task, while each observation remains visible
for reply and resolution. The fleet report includes blocker and deferred
owners, duplicates, overrides with rationale, and follow-up task identifiers.

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
flow, the routed `sd-review scope=pr` loop, sd-ship's own Stage 2b lifecycle
step, its internal read-only watch coordinator, and sd-housekeeping,
whose gate remains the only merge authority. `until=pr`,
`until=review`, or the default `until=merge` choose the stop-point, and
stage arguments such as `timeout-minutes=` pass through. It adds no new
gate logic; every stage's own gates remain authoritative, and a failed or
blocked stage stops the chain with that stage's report.

Stage 1 invokes the public `sd-create-pr` flow, which publishes or reuses
the PR and reports the next command instead of running review; there is no
composite-only orchestration context or hidden argument. `sd-create-pr`
behaves identically everywhere, and Stage 2 is the only review owner in
`sd-ship`: no review for `until=pr`, and one identical review-only loop for
`until=review` and `until=merge`.

Stage 2b owns the one post-cycle review-learning pass, invoking
`sd-review-learnings` in its read-only PR-scoped completed-cycle form for both
`until=review` and `until=merge`. No other ship stage repeats it.

Lifecycle side effects have one owner. Stage 2b runs finish-work in both
`until=` modes, exactly once per chain, bound to the exact head Stage 2
reviewed; the flow's own typed contract selects completion or planning
finalization, and planning keeps the planned task open with only journal and
bookkeeping commits. If finalization moves the head, the chain re-enters
Stage 2 once for that head; a second finalization head stops the chain as a
defect. The default merge-through chain then
runs the internal read-only watch coordinator in Stage 3, and invokes
housekeeping exactly once in
Stage 4, with zero finish-work flow invocations of its own. On an unchanged
head, housekeeping passes Stage 2b's retained receipt through
`--finish-work-receipt` to the shell gate; on a moved head it recomputes the
receipt with a direct read-only final-bundle validator invocation —
completion mode against the current head's empty delta, planning mode
re-running the captured base under journal-only-recovery scope — and
eligibility recomputes and compares the proof before merge.
Housekeeping owns one normal KB refresh before merge so archived task
documentation is current. A missing handoff leaves the PR open.
The refresh creates an absent
KB and preserves a valid root directory symlink. `sd-ship` does not repeat that
refresh.

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

`sd-work-backlog` stores resumable coordination state outside the repository.
Resolution order is:

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

After resuming a paused checkpoint, reconcile its complete locally observed
state before selecting more work. Add `--verified-live-advance` when the live
lifecycle is ahead, then repeat the same complete reconcile without that flag
to turn the amber recovery into green exact agreement. Old ledgers with a
human-only checkpoint target also require
`--resume-phase <recorded-lifecycle-phase>`.

A stopped or completed run may be reconciled with task and merge state that
advanced after its execution lock was released. The orchestration layer must
first verify exact merged PR state through GitHub, including URL, head SHA,
merge commit SHA, and default base branch. The local helper deliberately makes
no network calls:

```bash
bash scripts/sd-ai-command-pack-toolchain.sh run-python -- \
  scripts/sd-ai-command-pack-work-loop.py reconcile-terminal --repo . \
  --run-id <run-id> \
  --archived-task .trellis/tasks/archive/<month>/<task> \
  --delivery-pr-number <n> --delivery-pr-url <url> \
  --delivery-head <sha> --delivery-merge-commit <sha> \
  --branch <default> --head <default-head> --json
```

The optional bookkeeping PR uses the parallel `--bookkeeping-pr-number`,
`--bookkeeping-pr-url`, `--bookkeeping-head`, and
`--bookkeeping-merge-commit` group; supply all four or none. The command
requires a completed, identity-matching archived task, a clean checked-out
default branch synchronized with its origin tracking ref, locally resolvable
commits, no live owner, and an explicit flag for safe stale-lock recovery. It
keeps phase/status terminal, preserves `current`, counters, focus, iteration,
history, and stop reason, and stores external completion in
`terminalReconciliation`. Identical normalized evidence does not rewrite the
ledger; conflicting evidence fails without mutation. Status and housekeeping
render a verified record as historical external completion and label the
preserved counters as loop-owned.

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
their default caches land outside the writable sandbox or inside the
repository. Pack-owned entry points prevent that by routing these cache
classes through the shared toolchain. To select a specific safe parent, set one
absolute external root before running the command:

```bash
export SD_AI_COMMAND_PACK_CACHE_ROOT="${SD_AI_COMMAND_PACK_CACHE_ROOT:-${TMPDIR:-/tmp}}"
bash scripts/sd-ai-command-pack-toolchain.sh doctor
```

The builder validates the parent, then creates a private deterministic
namespace whose directory name embeds the current user's UID and is created
mode 0700, and sets `XDG_CACHE_HOME`, `PYTHONPYCACHEPREFIX`, `UV_CACHE_DIR`,
`UV_TOOL_DIR`, `PIP_CACHE_DIR`, `RUFF_CACHE_DIR`, and `NPM_CONFIG_CACHE`.
`XDG_CACHE_HOME` always points to the private pack namespace; a valid inherited
value may supply the namespace's safe parent but is not preserved verbatim.
Existing valid overrides keep precedence for the other per-tool cache
variables. Relative, repository-contained, symlinked, non-directory,
non-private, or foreign-owned overrides and namespaces fail before the external
tool runs, so a co-tenant on a shared host cannot pre-create a cache or tool
directory and have it reused — or planted bytecode and tool binaries executed —
under another user's identity. `GH_CONFIG_DIR`,
tokens, credential helpers, and unrelated environment variables are never
rewritten. Reusable pack-created caches remain after successful commands;
ordinary housekeeping does not delete them. Shared workflows invoke non-Python
tools as separate argv through
`bash scripts/sd-ai-command-pack-toolchain.sh run -- <tool> [args...]`; use that
form for ad hoc `gh`, uv, pip, Ruff, or npm calls inside an SD workflow instead
of bypassing the cache contract.

- `SD_AI_COMMAND_PACK_PYTHON`: authoritative Python executable for the
  toolchain helper. It must be Python 3.10 or newer and include every module
  requested with `--require-module`.
- `SD_AI_COMMAND_PACK_CACHE_ROOT`: absolute writable parent for private
  per-user/per-repository tool-cache namespaces. It must resolve outside the
  repository and must not itself be a symlink. Defaults to a safe inherited
  XDG cache root, then a validated system temporary root.
- `SD_AI_COMMAND_PACK_CACHE_ENV_READY`: internal shell-library sentinel set
  after the shared cache environment is validated. Operators should not set it
  directly.
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
- `SD_AI_COMMAND_PACK_DEFAULT_BRANCH`: explicit repository default-branch NAME
  (for example `main`) for the review preflight's root-task `base_branch`
  rule. This is a statement of the default branch, not a diff base: the
  branch-diff base-ref variables above are deliberately ignored by this rule
  because their values (a stacked-PR base, in CI an exact SHA) have different
  semantics. When unset, the rule discovers the default from the
  `origin/HEAD` symbolic ref and skips itself when neither source resolves.
  CI exports it from the event payload because a pinned-SHA checkout never
  establishes `origin/HEAD`.
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
  otherwise. When the existing KB is stale and already ignored, `auto`
  refreshes it once through the canonical helper, reruns `--check`, and
  continues only after the recheck passes. Unignored state remains fail-closed
  so full-check does not change tracked ignore configuration; missing `git` or
  an ignore-verification error also fails with a targeted diagnostic. `0` skips
  entirely; `required` stays read-only and fails when the helper, `python3`,
  or a passing check is unavailable.
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
  Full-check still uses local-first scope: when tracked staged or unstaged
  changes exist, it reviews each non-empty local layer; otherwise, it reviews
  the committed branch range.
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

### Planning Artifact Review

- Claude Code installs review a materially created or updated active Trellis
  task `prd.md`, `design.md`, or `implement.md` once at the planning convergence
  boundary, before implementation approval or `task.py start`.
- The rule captures pre-edit existence and hashes, skips unchanged or
  non-semantic churn visibly, and keeps paid review to one coherent artifact
  batch rather than one call per write.
- Claude performs its own adversarial review. When `command -v codex` and
  `codex exec --help` succeed, it launches one `codex exec` peer review in a
  separate background task using `--sandbox read-only` and `--ephemeral`, then
  joins both results.
- Every material concern receives a `C-*` identifier and an `addressed`,
  `rebutted`, `parked`, or `unresolved` disposition backed by repository
  evidence. Changed remediation is reviewed again for up to two rounds; a
  substantive concern that persists stops for user judgment instead of starting
  a fourth automatic round.
- Missing, incompatible, unauthenticated, or failed Codex is reported as a
  degraded optional lane while Claude's host review continues. The integration
  neither requires the OpenAI Codex Claude plugin nor changes upstream Trellis.

### Local Review

- Claude Code normal-scope invocations add `codex review --uncommitted` or
  `codex review --base <resolved-ref>` as a concurrent peer lane when the Codex
  CLI advertises the required flag. Install the CLI with
  `npm install -g @openai/codex` and authenticate with `codex login` when this
  optional lane is desired. The OpenAI Codex Claude plugin is not required.
- Claude Code `all` mode skips the Codex peer lane visibly because native Codex
  review has no full-codebase target.

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
  and, when a tooling/generated file is present, resolve the PR body the same
  way the enforcing check does — `SD_AI_COMMAND_PACK_SCOPE_PR_BODY` first, then
  `gh pr view` — and warn only when that body does not already name the required
  scope section, or when no body can be resolved. A body that already satisfies
  the requirement emits no advisory warning and no
  `sd-ai-command-pack-scope-advisory:` marker; the classifier's own `info:` lines
  listing the scope categories and changed files are unaffected and still print.
  The advisory never fails, and it resolves nothing on a branch with no
  tooling/generated change, so `gh` is not contacted there; it is also skipped
  whenever `SD_AI_COMMAND_PACK_SCOPE_CHECK_GH` is disabled, in which case an
  unresolvable body still warns. The shared review
  preflight (`sd-ai-command-pack-review-preflight.mjs`, which the local pre-PR
  gate runs) invokes this automatically, so the reminder to add a
  `Tooling/generated scope:` section still arrives before the PR exists — while
  the full-check hard-fail with a PR present is unchanged.
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

Prism is enabled by default when the legacy full-check command is invoked
explicitly and the executable is present. The `sd-review-pr` cycle consumes
`sd-check` and never invokes full-check, Prism, or Gito as part of that
deterministic gate. If Prism is missing or credentials/config are unavailable,
an explicit full-check reports the skip and continues unless
`SD_AI_COMMAND_PACK_FULL_CHECK_PRISM=required` is set.

Gito is opt-in because it can require `uvx`, cache access outside the repo,
network access, and configured LLM credentials. The `sd-review-pr` cycle does
not invoke Gito through its `sd-check` gate. When enabled explicitly,
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
owns finish-work before applying the merge gate and passes the exact retained
JSON through `--finish-work-receipt` only after the lifecycle handoff succeeds;
after an already-merged PR it performs the remaining cleanup and verification
without the flag. If the command reports anomalies, treat them as the next
manual action: dirty files, an unmerged PR, extra branches, open PRs/issues, or
remaining Trellis tasks mean the repo is not yet in the expected clean state.

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
It keeps shareable `.claude/` adapter files — SD commands, the planning-review
rule, and Trellis runtime, agents, settings, and skills — trackable while
ignoring only local Claude Code state (`settings.local.json`, caches, logs,
tmp). Other AI-tool local state such as tool
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
# + .claude/ settings.local.json). .claude/ uses the same commit-by-default
# deny-list as the others, so Trellis runtime, agents, settings.json, and
# skills are tracked; only settings.local.json and local state are ignored.
# A normal install regenerates this managed block; --local-only writes the
# equivalent patterns to .git/info/exclude instead.
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
bash scripts/sd-ai-command-pack-toolchain.sh run-python -- \
  scripts/sd-ai-command-pack-install-audit.py
bash -n scripts/sd-ai-command-pack-full-check.sh
bash -n scripts/sd-ai-command-pack-review-full-check.sh
bash -n scripts/sd-ai-command-pack-shell-lib.sh
bash -n scripts/sd-ai-command-pack-toolchain.sh
bash -n scripts/sd-ai-command-pack-review-local.sh
bash -n scripts/sd-ai-command-pack-review-scope.sh
python3 scripts/sd-ai-command-pack-update-spec-kb.py --dry-run
```

## Claude Code plugin and private marketplace

The Claude-side surface of the pack — every `sd-*` skill, the `/sd:*` command
surface, and the shared pack toolchain as `bin/` executables — also ships as a
Claude Code plugin generated from the same templates. The plugin is installed
once per machine instead of vendored per repository; the `.claude/rules/`
files stay repository configuration and are not part of it.

Add the marketplace once, then install the plugin:

```bash
claude plugin marketplace add platypeeps/sd-ai-command-pack
claude plugin install sd@sd-ai-command-pack
```

The same commands work as `/plugin marketplace add ...` and
`/plugin install ...` inside a Claude Code session.

The plugin `version` is stamped from `manifest.json`, so an installed plugin
names the exact pack release it came from:

```bash
claude plugin list --json
```

### Private-repository access

`platypeeps/sd-ai-command-pack` is private, so plugin installs and updates use
your normal Git credentials. Configure them once:

```bash
gh auth setup-git
```

SSH remotes with a loaded `ssh-agent` key work equally well. Background
plugin auto-update runs its `git pull` without Git credential helpers, so a
private marketplace can fail to refresh in the background and, by default, the
marketplace entry is dropped. Keep it across such a failure:

```bash
export CLAUDE_CODE_PLUGIN_KEEP_MARKETPLACE_ON_FAILURE=1
```

In CI, export a token and run `gh auth setup-git` before installing, or
pre-seed a warm plugin cache and point the CLI at it:

```bash
export CLAUDE_CODE_PLUGIN_CACHE_DIR="$RUNNER_TEMP/claude-plugins"
```

### Machine-scope install for the non-Claude tools

Gemini CLI and OpenCode read user-level directories, so the surfaces they use
install once per machine as well. The plugin carries that payload and the
installer that writes it, so no pack checkout is needed:

| What | Where it lands |
| --- | --- |
| shared `sd-*` skills | `~/.agents/skills/` |
| pack scripts the skills invoke | `~/.agents/bin/` |
| this guide | `~/.agents/docs/` |
| Gemini `/sd:*` commands | `~/.gemini/commands/sd/` |
| OpenCode `sd-*` commands | `${XDG_CONFIG_HOME:-~/.config}/opencode/commands/` |

One command updates both halves — the plugin and the machine surfaces:

```bash
scripts/sd-ai-command-pack-pack-update.sh
```

It updates the plugin first, resolves the plugin root that update produced,
installs the machine payload from that root, and prints the plugin and
machine-receipt versions. Run the machine half on its own from a pack checkout
with `python3 install.py --machine`, which stages the payload from the checkout
and installs the same bytes the plugin ships.

Inspect or undo an install with the bootstrap the plugin ships in its `bin/`:

```bash
sd-machine-install status --json
sd-machine-install remove
```

The installer writes a receipt recording the pack version, the payload digest,
and every file it wrote, and it touches nothing else. A file it does not own —
one you wrote yourself, or one you edited after an install — refuses the whole
run before anything is written, naming each conflicting path. `--force`
proceeds and copies each displaced file to a `.bak` sibling recorded in the
receipt; `remove` then deletes what the receipt installed and restores those
originals, and nothing else. `sd-status` reports the machine receipt against
the installed plugin version, so an update that only half completed shows as
`skew` and rerunning the update command converges.

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
  pack-owned entry points route uv and generic cache state through the shared
  private environment and retry HTTP 429 / slow-down responses with bounded
  backoff. If the failure
  is network or credential related, run from an environment with the needed
  access. Leave `SD_AI_COMMAND_PACK_FULL_CHECK_GITO` unset unless Gito is
  configured locally.
- A pack-owned command reports `cache setup failed`: set
  `SD_AI_COMMAND_PACK_CACHE_ROOT` to an absolute private writable directory
  outside the repository, then rerun the same command. Do not redirect
  `GH_CONFIG_DIR`; cache routing intentionally preserves existing GitHub auth.
- Root-level `code-review-report.*` files appear after manual Gito runs: the
  managed gitignore block ignores them, but prefer running through
  `sd-review-local` (any scope) or
  `SD_AI_COMMAND_PACK_FULL_CHECK_GITO=1 bash
  scripts/sd-ai-command-pack-full-check.sh` so reports go under the
  pack-managed `.build/review/gito` and `.build/review/gito-all` directories.
- Stale generated cache causes type or build failures: clear the repo-specific
  generated cache and rerun the deterministic check that failed.
