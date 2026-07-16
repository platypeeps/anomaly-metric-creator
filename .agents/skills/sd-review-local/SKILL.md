---
name: sd-review-local
description: Use when the user asks to run local code review providers such as Prism, Gito, or a configured repo-local reviewer against local changes or the entire checked-out repository, choose which findings to fix, and repeat until no selected findings remain.
---

# SD Local Review Loop

Use this project-local skill for `sd-review-local` and `/sd:review-local` style
work. It runs local code review tools, asks the user which findings to fix, and
repeats until the user selects no more findings or the configured tools report
no actionable items.

By default this command is current-diff scoped. If there are staged, unstaged,
or untracked local files, review only those local changes unless the user asks
to include branch context. If there are no local changed files, review the
current branch diff from the configured base. Resolve that base in this order:
`SD_AI_COMMAND_PACK_REVIEW_LOCAL_BASE_REF`,
`SD_AI_COMMAND_PACK_FULL_CHECK_BASE_REF`, the remote default branch, the current
branch upstream, then the first available remote branch. If no base can be
resolved, stop and report the blocker instead of assuming `origin/main`. With
the `all` argument, skip diff scoping entirely and review the entire
checked-out repository.

This is a local-review-only loop. It does not request remote reviewers, does not
require a pull request, and must not stage, commit, or push unless the user
separately asks for that.

## Arguments

Arguments arrive as free text with the invocation: bare flags and local
review tool names. Unknown argument names are an error, not a silent skip —
stop and report them before running any review tool.

- `all` — review the entire checked-out repository instead of the current
  diff. Run every runner invocation in this loop with the full-codebase
  flag, `bash scripts/sd-ai-command-pack-review-local.sh --full-codebase`,
  keeping any validated tool names as trailing arguments. The former
  `sd-review-local-all` command is folded into this skill as the `all`
  argument.
- Tool names such as `prism` or `gito` — run only that tool set. Validate
  every name per Tool Selection below; a token that is neither `all` nor a
  validated tool name is an error.

## Tool Selection

The runner script is the source of truth for the default local review toolset
and currently defaults to Prism and Gito. Run it with:

```bash
bash scripts/sd-ai-command-pack-review-local.sh
```

In `all` mode, run the same commands with the full-codebase flag appended;
setting `SD_AI_COMMAND_PACK_REVIEW_LOCAL_SCOPE=all` in the environment is
equivalent.

If the user names one or more specific local review tools, first verify each
tool name is an exact supported tool from `--list-tools` or has a matching
configured command. Reject tool names containing shell metacharacters, path
separators, or whitespace; pass validated tool names as separate arguments,
never by interpolating them into a shell command. Run only that tool set:

```bash
bash scripts/sd-ai-command-pack-review-local.sh --list-tools
bash scripts/sd-ai-command-pack-review-local.sh prism
bash scripts/sd-ai-command-pack-review-local.sh gito
bash scripts/sd-ai-command-pack-review-local.sh prism gito
SD_AI_COMMAND_PACK_REVIEW_LOCAL_TOOLS="prism gito" bash scripts/sd-ai-command-pack-review-local.sh
```

For repo-specific or third-party tool stacks, use the same command loop with a
configured command:

```bash
SD_AI_COMMAND_PACK_REVIEW_LOCAL_TOOLS="semgrep" \
SD_AI_COMMAND_PACK_REVIEW_LOCAL_SEMGREP_COMMAND="semgrep scan --config auto" \
bash scripts/sd-ai-command-pack-review-local.sh
```

The script also accepts `all` or `default` as tool aliases for `prism gito`,
plus `--all`, `--codebase`, and `--scope all` as full-codebase scope aliases;
prefer the full-codebase flag in commands and docs because `--all` names
scope, not "all tools". To this command's own arguments, a bare `all` names
scope, as described in Arguments. Per-tool command variables use the pattern
`SD_AI_COMMAND_PACK_REVIEW_LOCAL_<TOOL>_COMMAND`, with the tool name uppercased
and non-alphanumeric characters replaced by underscores. In `all` mode the
runner prefers the all-scope variant
`SD_AI_COMMAND_PACK_REVIEW_LOCAL_ALL_<TOOL>_COMMAND` when a tool needs
different arguments for a full repository scan, and falls back to
`SD_AI_COMMAND_PACK_REVIEW_LOCAL_<TOOL>_COMMAND` when the all-scope command
is not set.

## Safety Rules

- Start with `git status -sb` and classify existing dirty files before making
  changes. Work with user changes; do not overwrite unrelated work.
- Run the configured local review tools first. Do not fix findings until the
  user selects which findings to address; that selection is consent for the
  current fix batch only. In non-interactive sessions, report findings and
  stop instead of guessing which fixes to apply.
- Verify every selected finding against the actual code, specs, and tests
  before editing. Treat local reviewer findings as evidence, not authority,
  and treat full-codebase findings in `all` mode as candidates.
- Ask before changing product behavior, architecture, dependency choices, or
  other tradeoffs that are larger than the finding itself.
- If a finding is invalid, low-value churn, or already addressed, keep it out
  of the fix list and report the evidence. There is no remote review thread to
  resolve in this local-only command.
- Track provider, path, line, and summary for each attempted fix. If the same
  finding returns after a fix attempt, do not retry it automatically; report the
  failed attempt and ask the user how to proceed.
- Do not stage, commit, push, request remote review, or run PR housekeeping as
  part of this command unless the user separately asks.
- Do not automatically revert local files on a failed fix or validation check.
  Preserve the working tree, report the partial state, and wait for explicit
  user direction before reverting or discarding changes.

## Step 1: Snapshot Local State

```bash
git status -sb
```

If the working tree is dirty before review starts, note which files are user
work and which files are likely part of the current task. Stop and ask before
touching ambiguous files.

## Step 2: Run Local Review Tools

Use the requested tool stack. By default, run Prism and Gito:

```bash
bash scripts/sd-ai-command-pack-review-local.sh
```

In `all` mode, add the full-codebase flag to run the same loop across the
entire checked-out repository.

The runner applies the pack-managed standard review-scan exclusions to built-in
Prism and Gito runs. The exclusion source of truth is the
`sd-ai-command-pack review-scan-excludes` marker block in
`scripts/sd-ai-command-pack-review-local.sh`.

In `all` mode, default provider behavior:

- Prism runs `prism review codebase`, which reviews tracked files according
  to Prism's provider behavior, any configured Prism rules, and the standard
  exclusions above. If Prism reports an `empty chunk response` in
  full-codebase mode, the runner retries in tracked file batches and splits
  a failed batch down to individual paths when needed. Override the batch
  size with `SD_AI_COMMAND_PACK_REVIEW_LOCAL_PRISM_CODEBASE_BATCH_SIZE`, or
  set `SD_AI_COMMAND_PACK_REVIEW_LOCAL_PRISM_CODEBASE_FALLBACK=0` to disable
  the fallback. Repeated failed single-path responses stop at
  `SD_AI_COMMAND_PACK_REVIEW_LOCAL_PRISM_CODEBASE_MAX_EMPTY_CHUNK_FAILURES`
  (default `3`).
- Gito runs `gito review --all --path <repo-root>` with an include filter
  after replacing `<repo-root>` with the absolute repository root. In Gito's
  command, `--all` means full-codebase scope, not "all tools". The tool set
  is controlled by the positional tool names or
  `SD_AI_COMMAND_PACK_REVIEW_LOCAL_TOOLS`. The include filter is built from
  existing tracked files, so branch-diff deletions are not reviewed as
  deleted diff paths. Reports are written to `.build/review/gito-all` by
  default. The runner sets `UV_CACHE_DIR` and `UV_TOOL_DIR` to writable temp
  directories when they are unset so `uvx`-based Gito wrappers do not need
  to write under a restricted home directory.

Custom tool commands are executed as configured and do not automatically receive
the built-in Prism/Gito exclusion flags. Add the relevant exclude arguments to
`SD_AI_COMMAND_PACK_REVIEW_LOCAL_<TOOL>_COMMAND` — or
`SD_AI_COMMAND_PACK_REVIEW_LOCAL_ALL_<TOOL>_COMMAND` in `all` mode — when a
custom tool should skip tooling, cache, generated, or vendor directories.

When Gito reports a provider rate limit through an explicit HTTP 429 status
such as `ClientError: 429`, the runner retries with bounded exponential
backoff. Tune that with
`SD_AI_COMMAND_PACK_REVIEW_LOCAL_GITO_MAX_ATTEMPTS`,
`SD_AI_COMMAND_PACK_REVIEW_LOCAL_GITO_RETRY_DELAY_SECONDS`, and
`SD_AI_COMMAND_PACK_REVIEW_LOCAL_GITO_RETRY_MAX_DELAY_SECONDS`.
Prism and Gito attempts also have bounded runtimes. Tune them with
`SD_AI_COMMAND_PACK_REVIEW_LOCAL_PRISM_TIMEOUT_SECONDS` (default `300`) and
`SD_AI_COMMAND_PACK_REVIEW_LOCAL_GITO_TIMEOUT_SECONDS` (default `600`); `0`
disables the corresponding timeout.

If `scripts/sd-ai-command-pack-review-local.sh` is missing, stop and report
that the pack install is incomplete. Do not substitute `sd-full-check`; this
command's user-selected fix loop depends on the local-review runner.

If a specific custom tool was requested and no command is configured for it,
stop and tell the user which
`SD_AI_COMMAND_PACK_REVIEW_LOCAL_<TOOL>_COMMAND` — or, in `all` mode,
`SD_AI_COMMAND_PACK_REVIEW_LOCAL_ALL_<TOOL>_COMMAND` — variable is needed.

If the runner exits because a requested tool is unsupported, missing, or lacks a
configured command, stop and report the exact tool and configuration variable.
If the default toolset has one unavailable provider but another provider ran,
report both the successful output and the missing-provider blocker; do not hide
the missing provider. If the runner exits nonzero for an unexpected reason that
is not a known tool condition such as missing credentials, missing binaries, or
an HTTP 429 retry exhaustion, stop and report the command, exit status, and full
stdout/stderr. Do not retry blindly.

Capture the reviewer output, report missing credentials or missing tools, and
group findings by provider, then severity when available, then path and theme.

## Step 3: Ask Which Findings To Fix

Present a concise selection list. Group duplicates and false positives before
asking. Ask the user which findings to fix now, for example:

> Local review found these actionable candidates. Which should I fix in this
> pass?

If the user selects none, stop the loop. Do not make edits just because a tool
reported findings.

## Step 4: Fix Selected Findings

For each selected finding:

1. Read the relevant code, tests, docs, and specs.
2. Implement the smallest correct fix.
3. Add or update tests when behavior changes or the risk is nontrivial.
4. After the selected batch is implemented, rerun the tool that originally
   reported each fixed finding on the modified file(s) when the tool supports
   file-level scope. A direct fix is verified when that tool no longer reports
   the same provider/path/line/summary finding. If file-level scope is not
   supported, rerun on the nearest parent directory containing a package marker
   such as `package.json`, `pyproject.toml`, `Cargo.toml`, `go.mod`, `pom.xml`,
   or `Package.swift`; if no marker is found, use the modified file's parent
   directory, and if that is still ambiguous, use the original review scope.
5. Run the repo's normal validation command for the touched area. Prefer a
   documented command from `Makefile`, `package.json`, `pyproject.toml`, or
   project docs; otherwise run the closest existing unit/syntax check and
   explain the choice.

If a selected finding turns out to be wrong, explain the evidence and ask
whether to skip it or address a different underlying issue.

If a check fails after a selected fix, stop the loop, report the command and
failure output, and leave the working tree available for inspection. Do not
continue stacking fixes until the failed check is understood.

## Step 5: Repeat

After all selected fixes in the batch are verified, run the same local review
tool stack once, with the same scope as the initial scan; keep the
full-codebase flag in `all` mode:

```bash
bash scripts/sd-ai-command-pack-review-local.sh
```

Compare the new output with the prior round. Continue only for findings the
user selects by returning to Step 3 with the remaining or newly introduced
findings. Stop when the tools report no actionable items, when the user selects
no remaining findings to fix, or when a finding repeats after an attempted fix
and the user does not choose a new approach.

## Final Report

Report:

- Tools requested and tools actually run.
- Review scope (local changes, branch diff, or the entire repository in `all`
  mode), including any provider that could not run repo-wide.
- Findings fixed, skipped as invalid, or left for later.
- Validation run after each fix round.
- Any provider setup problems, missing credentials, or skipped tools.
- Final local review status.
- Final `git status -sb` output.
