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
- [Local commands](#local-commands)
- [Configuration](#configuration)
- [Install or refresh this pack](#install-or-refresh-this-pack)
- [Troubleshooting](#troubleshooting)

## What is installed

- `.agents/skills/sd-start/SKILL.md`: Codex-visible Trellis start wrapper.
- `.agents/skills/sd-continue/SKILL.md`: Codex-visible Trellis continue wrapper.
- `.agents/skills/sd-finish-work/SKILL.md`: Codex-visible Trellis finish-work wrapper.
- `.agents/skills/sd-create-pr/SKILL.md`: spec-refresh, commit, push, PR
  creation/reuse, and PR-review orchestration workflow.
- `.agents/skills/sd-review-pr/SKILL.md`: deterministic local gate plus remote
  PR review workflow.
- `.agents/skills/sd-review-local/SKILL.md`: local review provider fix loop.
- `.agents/skills/sd-review-local-all/SKILL.md`: full-codebase local review
  provider fix loop.
- `.agents/skills/sd-review-learnings/SKILL.md`: review feedback learning
  capture workflow.
- `.agents/skills/sd-full-check/SKILL.md`: full local verification workflow.
- `.agents/skills/sd-housekeeping/SKILL.md`: post-merge cleanup workflow.
- `.agents/skills/sd-update-spec/SKILL.md`: Trellis update-spec workflow plus
  pack-managed repository knowledge refresh.
- `scripts/sd-ai-command-pack-full-check.sh`: canonical full-check script.
- `scripts/sd-ai-command-pack-housekeeping.sh`: canonical post-merge housekeeping script.
- `scripts/sd-ai-command-pack-record-session.py`: one-shot session journal
  recorder — wraps Trellis' `add_session.py`, resolving commit subjects
  from git (failing fast on unknown hashes), filling the Main Changes and
  Testing sections from `--change`/`--test` flags, and refusing to commit
  an entry that still contains template placeholders.
- `scripts/sd-ai-command-pack-review-scope.sh`: copied/generated file scope
  preflight for mixed PRs.
- `scripts/sd-ai-command-pack-review-preflight.mjs`: generic dependency-free
  review preflight for copied/generated disclosure, documentation path hygiene,
  Trellis journal consistency, npm override drift, and large diff warnings.
- `scripts/sd-ai-command-pack-review-local.sh`: local Prism/Gito and configured
  review-tool runner for the review-local and review-local-all loops.
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
Codex exposes the pack entry points as skills named `sd-start`, `sd-continue`,
`sd-finish-work`, `sd-create-pr`, `sd-full-check`, `sd-housekeeping`,
`sd-review-pr`, `sd-review-local`, `sd-review-local-all`,
`sd-review-learnings`, and `sd-update-spec`; type `/sd` in Codex command
completion or invoke them with `$sd-review-pr`-style skill mentions.
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
6. Use the review-local-all command when you want the same local fix loop run
   against the entire checked-out repository rather than just recent diffs.
7. Use the create-pr command when you want the publishing wrapper: it runs
   `sd-update-spec`, stages only intended files, commits and pushes the feature
   branch when needed, creates or reuses the branch PR, and then enters the
   review-pr loop.
8. Use the review-pr command for an existing PR loop. It should run the deterministic
   local full-check path with Prism/Gito disabled before requesting remote
   review. Run `sd-full-check`, `sd-review-local`, or `sd-review-local-all`
   explicitly when you want Prism/Gito.
9. Request the configured remote reviewer, defaulting to GitHub Copilot, after
   a clean local pass and again after every pushed review-fix commit made
   during the loop, unless the user explicitly asked for local-only review.
10. Let the review-pr command reply to and resolve review threads as part of the
   normal loop once findings are fixed, rebutted with evidence, or confirmed
   already addressed.
11. Use the review-learnings command when review comments repeat across PRs and
   you want to capture repo-specific preventive guidance.
12. Run the update-spec command when the work taught you a durable
   implementation contract or convention. It runs the existing update-spec skill
   and also checks whether an existing architectural overview needs to be
   updated.
13. Run the finish-work command when the coding session is complete and you need
   the Trellis finish-work skill's quality gate, archive, journal, and commit
   reminder behavior.
14. After the PR merges, run the housekeeping command to get back to the default
   branch, prune/delete the merged development stream, and see the condensed
   clean-state/anomaly report.
15. If the review-pr command sees the PR is already merged or becomes merged
   while the command is running, it stops the review loop and runs post-merge
   housekeeping before the final report. This does not wake inactive sessions;
   it only runs when the active agent observes the merge.

The default remote reviewer for review-pr is GitHub Copilot's
`copilot-pull-request-reviewer`. Target repos can override it with
`SD_AI_COMMAND_PACK_REVIEW_PR_REMOTE_REVIEWER`,
`SD_AI_COMMAND_PACK_REVIEW_PR_REMOTE_REVIEWER_LABEL`,
`SD_AI_COMMAND_PACK_REVIEW_PR_REMOTE_AUTHOR_MATCH`,
`SD_AI_COMMAND_PACK_REVIEW_PR_REMOTE_REQUEST_COMMAND`, and
`SD_AI_COMMAND_PACK_REVIEW_PR_REMOTE_ROUND_LIMIT`. The round limit defaults to
five configured remote-review requests before the command asks whether to keep
going.

The create-pr wrapper honors `SD_AI_COMMAND_PACK_CREATE_PR_BASE` for a base
branch override, `SD_AI_COMMAND_PACK_CREATE_PR_COMMIT_MESSAGE` when it creates
a commit without a user-provided message, and
`SD_AI_COMMAND_PACK_CREATE_PR_DRAFT=1` when the PR should start as a draft.
It still delegates the actual review loop to review-pr after PR creation or
reuse.

## Commands

Use the platform-native command when available.

Claude Code and Gemini CLI:

```bash
/sd:start
/sd:continue
/sd:finish-work
/sd:create-pr
/sd:full-check
/sd:housekeeping
/sd:review-pr
/sd:review-local
/sd:review-local-all
/sd:review-learnings
/sd:update-spec
```

Cursor command files, GitHub Copilot prompt files, OpenCode command files,
Qoder commands, Trae commands, Pi prompts, workflow adapters, and Codex skills:

```bash
/sd-start
/sd-continue
/sd-finish-work
/sd-create-pr
/sd-full-check
/sd-housekeeping
/sd-review-pr
/sd-review-local
/sd-review-local-all
/sd-review-learnings
/sd-update-spec
```

In Codex, you can also invoke the enabled skills explicitly with
`$sd-review-pr`-style skill mentions.

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
python3 scripts/sd-ai-command-pack-review-learnings.py --include-working-tree
```

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
`.sd-ai-command-pack/provenance.json` with the pack version and `sha256`
hashes of installed pack files (user-tunable files are never vouched); the
audit fails when a vouched file's content drifts from the recorded pack
content, when a vouched file is missing while not gitignored, or when a
vouched path (or the provenance file itself) is a symlink or other
non-regular node, so the "reviewed upstream" exemption for vendored pack
files is a checkable claim.
The copied/generated scope preflight reads
`.sd-ai-command-pack/installed-targets.txt`, reports changed pack/Trellis
runtime files, known repository-map files when present, and Trellis workspace
journal/index files as integration-only review surface. When the GitHub CLI can
resolve a current PR, it checks that the PR body includes a
`Tooling/generated scope:` section before review cycles spend attention on
copied or generated surfaces. In CI or local preflights where `gh pr view`
should not run, pass the PR body through `SD_AI_COMMAND_PACK_SCOPE_PR_BODY`.

The review preflight is intentionally generic and safe to run without project
dependencies. It checks for duplicate npm override sources of truth, changed
copied Trellis or SD command-pack surfaces without companion repo-owned
integration context, personal absolute paths in docs/prompts/specs, missing
repo path references in docs/prompts/specs, completed Trellis journal
placeholder or journal/index commit drift, and large diffs that are likely to
skip remote AI review. Target repos can tune roots, path-reference prefixes,
integration paths, optional paths, copied-template paths, and warning thresholds
with `.sd-ai-command-pack/review-preflight.json`. Repos that intentionally
document service-user paths under `/home/<user>/` can add those service users to
`allowedLinuxHomeUsers` in that config.

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
review-local-all command when you want a full checked-out repository review.
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

For `uvx`-based Gito wrappers, the
runner sets `UV_CACHE_DIR` and `UV_TOOL_DIR` to writable temp directories when
they are unset. When Gito reports provider rate limiting through an explicit
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
untouched and reports a conflict. Run
`python3 scripts/sd-ai-command-pack-update-spec-kb.py --dry-run` to preview the
refresh without writes, `--check` to verify the generated folder and ignore
entry are current, or `--help` for the safe CLI summary.

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
fast-forward from `origin`, delete the merged local and remote branch, and then
report the current-stream clean state plus anomalies. Repo-wide open PRs, open
issues, and active Trellis tasks are reported in a separate inventory section
rather than blockers for this cleanup.

The installed script also supports
`bash scripts/sd-ai-command-pack-housekeeping.sh --self-test`, which verifies
the vendored copy's merge-gate contract against stubbed scenarios and exits.
It is hermetic (no git, gh, or network access), so repos can run it from CI or
a test suite instead of maintaining bespoke contract tests over the vendored
script; it fails non-zero if any gate scenario misbehaves.

A clean current-stream housekeeping run should end with:

```text
==> Expected clean state
- branch: <default>
- working tree: clean
- <default> matches origin/<default>
- local branches: only <default>
- remote branches: only origin/HEAD and origin/<default>

==> Inventory
- open PRs: <summary>
- open issues: <summary>
- Trellis active tasks: <summary>

==> Anomalies
none
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
Current Trellis task: <none|summary>
Anomalies: none

Insight:
<One short evidence-backed observation about what housekeeping proved or surfaced; omit this section when there is nothing useful beyond the final state.>

No follow-up needed for this cleanup stream.
```

Include `Insight:` only when the script output or session context supports a
useful observation, such as the PR lifecycle being healthy, cleanup being
verification-only because the PR was already merged, stale refs being pruned,
the repo being ready for the next work stream, or a process improvement being
worth tracking. Do not add filler insights that merely restate `clean`.
If follow-up items exist, replace the final no-follow-up sentence with a
numbered `Next Steps` list that covers: open follow-up items from the session,
existing Trellis tasks already in progress, and high-value Trellis task
candidates to start next. If a category has no evidence, the report should say
that plainly instead of inventing work.

## Configuration

Common environment variables:

### Full Check And Preflight

On macOS, prefer a Homebrew Python-backed virtualenv for repo-local Python
checks, especially coverage runs. Apple/Xcode Python often lacks project dev
dependencies and can try to write bytecode caches under protected
`~/Library/Caches` paths. A portable setup is:

```bash
BREW_PYTHON="${BREW_PYTHON:-/opt/homebrew/bin/python3}"
test -x "$BREW_PYTHON" || BREW_PYTHON=/usr/local/bin/python3
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

- `SD_AI_COMMAND_PACK_FULL_CHECK_BASE_REF`: explicit base ref for branch review.
  When unset, branch-diff helpers use the discovered remote default ref, then
  the current branch upstream, then the first available remote ref.
- `SD_AI_COMMAND_PACK_REVIEW_PREFLIGHT_BASE_REF`: explicit base ref for the
  JavaScript review-preflight branch-diff probes. Defaults to
  `SD_AI_COMMAND_PACK_FULL_CHECK_BASE_REF`, then the discovered branch-diff
  sequence above.
- `SD_AI_COMMAND_PACK_FULL_CHECK_REVIEW_PREFLIGHT=0`: skip
  repo-local review preflight.
- `SD_AI_COMMAND_PACK_FULL_CHECK_REVIEW_PREFLIGHT=required`: fail if no configured
  review preflight command can run and the shared or legacy review preflight is
  unavailable.
- `SD_AI_COMMAND_PACK_FULL_CHECK_REVIEW_PREFLIGHT_COMMAND`: repo-specific review
  preflight command to run with `bash -lc`.
- `SD_AI_COMMAND_PACK_FULL_CHECK_REVIEW_PREFLIGHT_SCRIPT`: custom JavaScript
  review preflight script to run before the legacy repo-local
  `scripts/check-review-preflight.mjs` fallback.
- `SD_AI_COMMAND_PACK_INSTALL_AUDIT=0`: skip the structural post-install audit.
- `SD_AI_COMMAND_PACK_FULL_CHECK_PACK_DRIFT=0`: skip the pack source drift
  gates (template twin parity and env-var documentation coverage). These gates
  only run inside the sd-ai-command-pack source repository itself and are
  skipped automatically in target repos.
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

### Local Review

- `SD_AI_COMMAND_PACK_REVIEW_LOCAL_TOOLS`: local review tool list for
  `sd-review-local`. Defaults to `prism gito`; accepts spaces or commas.
- `SD_AI_COMMAND_PACK_REVIEW_LOCAL_SCOPE=all`: run the local review runner
  against the full checked-out repository. Defaults to current-diff scope. The
  `sd-review-local-all` command passes this by invoking the runner with
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
- `MAX_CONCURRENT_TASKS`: Gito LLM concurrency cap. The pack runners load the
  installed `.gito/sd-ai-command-pack.env` default of `4` when this variable is
  unset.
- `SD_AI_COMMAND_PACK_REVIEW_LOCAL_UV_CACHE_DIR`: fallback `UV_CACHE_DIR` for
  Gito when `UV_CACHE_DIR` is unset. Defaults to a temp
  `sd-ai-command-pack-uv-cache` directory.
- `SD_AI_COMMAND_PACK_REVIEW_LOCAL_UV_TOOL_DIR`: fallback `UV_TOOL_DIR` for
  Gito when `UV_TOOL_DIR` is unset. Defaults to a temp
  `sd-ai-command-pack-uv-tools` directory.
- `SD_AI_COMMAND_PACK_REVIEW_LOCAL_<TOOL>_COMMAND`: command for a repo-specific
  or third-party local review tool, run with `bash -c`.
- `SD_AI_COMMAND_PACK_REVIEW_LOCAL_ALL_<TOOL>_COMMAND`: full-codebase command
  for a repo-specific or third-party local review tool. Takes precedence over
  `SD_AI_COMMAND_PACK_REVIEW_LOCAL_<TOOL>_COMMAND` when scope is `all`.
- `SD_AI_COMMAND_PACK_REVIEW_LOCAL_GITO_BASE_REF`: base ref for review-local Gito
  review. Defaults to `SD_AI_COMMAND_PACK_FULL_CHECK_GITO_BASE_REF`, then
  `SD_AI_COMMAND_PACK_FULL_CHECK_BASE_REF`, then the discovered branch-diff
  sequence above.
- `SD_AI_COMMAND_PACK_REVIEW_LOCAL_GITO_OUT_DIR`: output folder for review-local
  Gito reports. Defaults to `SD_AI_COMMAND_PACK_FULL_CHECK_GITO_OUT_DIR`, then
  `.build/review/gito`.
- `SD_AI_COMMAND_PACK_REVIEW_LOCAL_ALL_GITO_OUT_DIR`: output folder for
  review-local-all Gito reports. Defaults to
  `SD_AI_COMMAND_PACK_REVIEW_LOCAL_GITO_OUT_DIR`, then
  `SD_AI_COMMAND_PACK_FULL_CHECK_GITO_OUT_DIR`, then `.build/review/gito-all`.

### Scope And PR Body Checks

- `SD_AI_COMMAND_PACK_SCOPE_CHECK=0`: skip tooling/generated file scope checks.
- `SD_AI_COMMAND_PACK_TARGETS_FILE`: explicit installed-targets file for the
  review-scope check. Defaults to `.sd-ai-command-pack/installed-targets.txt`.
- `SD_AI_COMMAND_PACK_SCOPE_CHECK_GH=required`: fail when `gh` cannot resolve the
  current PR for the tooling/generated scope body check. Defaults to optional.
- `SD_AI_COMMAND_PACK_SCOPE_BASE_REF`: base ref for tooling/generated scope checks.
  Defaults to `SD_AI_COMMAND_PACK_FULL_CHECK_BASE_REF`, then the discovered
  branch-diff sequence above.
- `SD_AI_COMMAND_PACK_SCOPE_PR_BODY`: explicit PR body text for tooling/generated
  scope checks when `gh pr view` should not be used. Deprecated fallback:
  `REVIEW_PREFLIGHT_PR_BODY`.
- `SD_AI_COMMAND_PACK_PR_BODY_SCOPE_CHECK=0`: skip configurable PR-body scope
  checks.
- `SD_AI_COMMAND_PACK_PR_BODY_SCOPE_CHECK=required`: fail if the pack-provided
  PR-body scope checker cannot run, including when `python3` is missing.
- `SD_AI_COMMAND_PACK_PR_BODY_SCOPE_CONFIG`: explicit JSON config path for
  additional PR-body scope rules. Defaults to
  `.sd-ai-command-pack/pr-body-scope.json` when present.
- `SD_AI_COMMAND_PACK_PR_BODY_SCOPE_PR_BODY`: explicit PR body text for
  configurable PR-body scope checks. Falls back to
  `SD_AI_COMMAND_PACK_SCOPE_PR_BODY`, then the deprecated
  `REVIEW_PREFLIGHT_PR_BODY`.
- `SD_AI_COMMAND_PACK_PR_BODY_SCOPE_CHANGED_FILES`: explicit newline- or
  NUL-delimited changed path list for configurable PR-body scope checks.
- `SD_AI_COMMAND_PACK_CHANGED_FILES`: fallback changed-path list for the
  PR-body scope check when the `PR_BODY_SCOPE` variant above is unset.
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

Run housekeeping after a PR is merged and any finish-work journal commit has
landed. If the command reports anomalies, treat them as the next manual action:
dirty files, an unmerged PR, extra branches, open PRs/issues, or remaining
Trellis tasks mean the repo is not yet in the expected clean state.

## Updating the pack

To refresh installed assets from the pack checkout:

```bash
python3 /path/to/sd-ai-command-pack/install.py /path/to/target/repo --force
```

Use `python3 /path/to/sd-ai-command-pack/install.py --help` for the safe CLI
summary, or `--version` to print the pack name and version without touching a
target repo.

To remove the pack from a target checkout:

```bash
python3 /path/to/sd-ai-command-pack/install.py /path/to/target/repo --remove
```

Normal shared installs maintain a managed `sd-ai-command-pack
trellis-gitignore` block in the repo root `.gitignore`. The block ignores
Trellis local/runtime files such as `.trellis/.developer`,
`.trellis/.runtime/`, `.trellis/.cache/`, Trellis backup directories,
`.trellis/worktrees/`, and `.trellis/.template-hashes.json` without
blanket-ignoring shareable `.trellis` workflow, spec, task, and script files.
It also ignores local AI-tool state such as `.claude/settings.local.json`,
tool caches, logs, sessions, tmp folders, Gito report/temp artifacts,
tool-specific local state, `.opencode/node_modules/`, and root
`node_modules/` without blanket-ignoring shareable platform adapter
directories.
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
.claude/settings.local.json
.claude/**/*.local.*
.claude/**/.cache/
.claude/**/cache/
.claude/**/logs/
.claude/**/*.log
.codebuddy/**/*.local.*
.codebuddy/**/.cache/
.codebuddy/**/cache/
.codebuddy/**/logs/
.codebuddy/**/tmp/
.codebuddy/**/*.log
.codex/**/*.local.*
.codex/**/.cache/
.codex/**/cache/
.codex/**/logs/
.codex/**/sessions/
.codex/**/tmp/
.codex/**/*.log
.cursor/**/*.local.*
.cursor/**/.cache/
.cursor/**/cache/
.cursor/**/logs/
.cursor/**/tmp/
.cursor/**/*.log
.devin/**/*.local.*
.devin/**/.cache/
.devin/**/cache/
.devin/**/logs/
.devin/**/tmp/
.devin/**/*.log
.factory/**/*.local.*
.factory/**/.cache/
.factory/**/cache/
.factory/**/logs/
.factory/**/tmp/
.factory/**/*.log
.gemini/settings.local.json
.gemini/**/*.local.*
.gemini/**/.cache/
.gemini/**/cache/
.gemini/**/logs/
.gemini/**/tmp/
.gemini/**/*.log
.gito/**/*.local.*
.gito/**/.cache/
.gito/**/cache/
.gito/**/logs/
.gito/**/tmp/
.gito/**/*.log
.kiro/**/*.local.*
.kiro/**/.cache/
.kiro/**/cache/
.kiro/**/logs/
.kiro/**/tmp/
.kiro/**/*.log
.kilocode/**/*.local.*
.kilocode/**/.cache/
.kilocode/**/cache/
.kilocode/**/logs/
.kilocode/**/tmp/
.kilocode/**/*.log
.opencode/**/*.local.*
.opencode/**/.cache/
.opencode/**/cache/
.opencode/**/logs/
.opencode/**/tmp/
.opencode/**/state/
.opencode/**/sessions/
.opencode/node_modules/
.opencode/**/*.log
.pi/**/*.local.*
.pi/**/.cache/
.pi/**/cache/
.pi/**/logs/
.pi/**/tmp/
.pi/**/*.log
.qoder/**/*.local.*
.qoder/**/.cache/
.qoder/**/cache/
.qoder/**/logs/
.qoder/**/tmp/
.qoder/**/*.log
.reasonix/**/*.local.*
.reasonix/**/.cache/
.reasonix/**/cache/
.reasonix/**/logs/
.reasonix/**/tmp/
.reasonix/**/*.log
.trae/**/*.local.*
.trae/**/.cache/
.trae/**/cache/
.trae/**/logs/
.trae/**/tmp/
.trae/**/*.log
.zcode/**/*.local.*
.zcode/**/.cache/
.zcode/**/cache/
.zcode/**/logs/
.zcode/**/tmp/
.zcode/**/*.log
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
`.gito/config.toml` files that differ from the pack templates are reported as
`preserved` and are never overwritten or reported as conflicts, so repo-specific
review rules are not replaced during a pack refresh. The pack-owned
`.gito/sd-ai-command-pack.env` file is updateable like scripts and docs so the
standard Gito concurrency cap can be refreshed.

Use `--remove` to uninstall pack-owned assets. Removal deletes pack-vouched
files, files that still match the bundled template, generated pack state under
`.sd-ai-command-pack/`, and the pack-managed blocks in `.gitignore`,
`.git/info/exclude`, and `.github/copilot-instructions.md`. Drifted files,
symlinks, directories, and user-owned policy files are preserved by default;
add `--force` to delete drifted regular pack files too, and add `--backup` to
keep `.bak` copies of deleted files.

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
  `sd-review-local` and `sd-review-local-all` set writable `UV_CACHE_DIR` and
  `UV_TOOL_DIR` defaults and retry HTTP 429 / slow-down responses with bounded
  backoff. If the failure is network or credential related, run from an
  environment with the needed access. For `sd-full-check`, leave
  `SD_AI_COMMAND_PACK_FULL_CHECK_GITO` unset unless Gito is configured locally.
- `uvx`, Ruff, Python compile/coverage, preflight, or full-check fail with
  `Operation not permitted` while creating cache or temporary files: export the
  sandbox-local `PYTHONPYCACHEPREFIX`, `UV_CACHE_DIR`, `UV_TOOL_DIR`, and
  `RUFF_CACHE_DIR` block from Configuration, then rerun the same command.
- Root-level `code-review-report.*` files appear after manual Gito runs: the
  managed gitignore block ignores them, but prefer running through
  `sd-review-local`, `sd-review-local-all`, or
  `SD_AI_COMMAND_PACK_FULL_CHECK_GITO=1 bash
  scripts/sd-ai-command-pack-full-check.sh` so reports go under the
  pack-managed `.build/review/gito` and `.build/review/gito-all` directories.
- Stale generated cache causes type or build failures: clear the repo-specific
  generated cache and rerun the deterministic check that failed.
