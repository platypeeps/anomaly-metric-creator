---
name: sd-create-pr
description: Use when the user wants to update specs through the SD wrapper, commit and push the current branch, create or reuse a GitHub pull request, then enter the SD PR review loop.
---

# SD Create Pull Request

Use this project-local Software Delivery skill for `sd-create-pr` and
`/sd:create-pr` style work. It is an orchestration wrapper: it runs the
installed `sd-update-spec` workflow, commits and pushes the intended branch
changes, creates or reuses the branch pull request, then hands off to
`sd-review-pr` for deterministic checks, configured remote reviewer requests,
Copilot-style polling when configured, fixes, CI handling, and the bounded
review loop.

## Safety Rules

- Require `gh` and an authenticated GitHub session before creating or resolving
  a pull request: `gh --version` and `gh auth status`.
- Run the pack toolchain doctor once before dependency-sensitive work. Keep its
  selected Python and project-check report for this command run; do not retry
  raw interpreters in sequence after an authoritative candidate fails.
- Resolve both `sd-update-spec` and `sd-review-pr` by name using the agent's
  trusted installed-skill resolver before starting. Stop if either skill is
  missing, unreadable, empty, resolves to more than one candidate, fails
  validation, defines contradictory steps that violate this command's safety
  rules, or requires unavailable tools.
- Do not duplicate the detailed update-spec or review-pr workflows. Use
  `sd-update-spec` for repository knowledge refreshes and `sd-review-pr` for
  local full-check, configured remote reviewer requests, review polling, fix
  loops, CI handling, and final finish-work behavior.
- Do not run Prism, Gito, or other local review providers directly from this
  command. Those tools run only when the user explicitly invokes
  `sd-full-check` or `sd-review-local` (optionally with `all`); `sd-review-pr`
  disables Prism and Gito for its command-owned local gate.
- Do not create a PR from the repository default branch. If the current branch
  is the default branch, create a feature branch before continuing. Prefer
  `SD_AI_COMMAND_PACK_CREATE_PR_BRANCH` when set; otherwise derive a concise
  `codex/<slug>` name from the requested work or commit message and fall back
  to a timestamped `codex/prepare-pr-<timestamp>` name when needed.
- Do not stage unrelated or ambiguous work. Capture the dirty state before and
  after `sd-update-spec`, classify all changed and untracked paths, and stage
  only files that clearly belong to the PR. Ask before touching ambiguous
  files; in non-interactive sessions, stop by default.
- Do not create a duplicate PR. If the current branch already has an open PR,
  reuse it and continue into `sd-review-pr`.
- Do not assume the base branch is `main`. Detect the repository default branch
  with GitHub metadata when available, and let
  `SD_AI_COMMAND_PACK_CREATE_PR_BASE` override it when the target repo needs a
  different base.
- If a command, provider call, push, PR creation, or delegated skill step fails,
  stop and report the command, exit status, and complete stdout/stderr output.

## Step 1: Resolve Prerequisites And Branch State

```bash
bash scripts/sd-ai-command-pack-toolchain.sh doctor
gh --version
gh auth status
git status -sb
CURRENT_BRANCH=$(git branch --show-current)
```

If the toolchain helper is missing, stop and report that the pack should be
reinstalled. When an ad hoc Python validation needs project modules, invoke it
through `bash scripts/sd-ai-command-pack-toolchain.sh run-python
--require-module <name> -- <arguments>` instead of trying multiple Python
executables.

Resolve the base branch without hardcoding `origin/main`:

```bash
BASE_BRANCH="${SD_AI_COMMAND_PACK_CREATE_PR_BASE:-}"
if [ -z "$BASE_BRANCH" ]; then
  BASE_BRANCH=$(gh repo view --json defaultBranchRef --jq .defaultBranchRef.name)
fi
```

If GitHub metadata is unavailable, use the local remote HEAD as a fallback:

```bash
BASE_BRANCH="${BASE_BRANCH:-$(git symbolic-ref --quiet --short refs/remotes/origin/HEAD | sed 's#^origin/##')}"
```

Stop if `CURRENT_BRANCH` is empty, if no base branch can be resolved, or if the
current branch cannot be moved off the base branch.

If the current branch equals the base branch, create a feature branch instead of
stopping. Prefer an explicit branch name, then a user-provided slug, then a slug
derived from the commit message. Use a timestamped fallback when the derived
name is empty or already exists:

```bash
if [ "$CURRENT_BRANCH" = "$BASE_BRANCH" ]; then
  TARGET_BRANCH="${SD_AI_COMMAND_PACK_CREATE_PR_BRANCH:-}"
  if [ -z "$TARGET_BRANCH" ]; then
    BRANCH_SOURCE="${SD_AI_COMMAND_PACK_CREATE_PR_BRANCH_SLUG:-${SD_AI_COMMAND_PACK_CREATE_PR_COMMIT_MESSAGE:-prepare-pr}}"
    BRANCH_SLUG=$(printf '%s' "$BRANCH_SOURCE" \
      | tr '[:upper:]' '[:lower:]' \
      | sed -E 's/[^a-z0-9]+/-/g; s/^-+//; s/-+$//; s/-+/-/g' \
      | cut -c1-48)
    TARGET_BRANCH="codex/${BRANCH_SLUG:-prepare-pr}"
  fi
  if git show-ref --verify --quiet "refs/heads/$TARGET_BRANCH" \
    || git ls-remote --exit-code --heads origin "$TARGET_BRANCH" >/dev/null 2>&1; then
    TARGET_BRANCH="${TARGET_BRANCH}-$(date -u +%Y%m%d%H%M%S)"
  fi
  git switch -c "$TARGET_BRANCH"
  CURRENT_BRANCH=$(git branch --show-current)
fi
```

Capture the initial dirty state before refreshing specs:

```bash
git status --short --untracked-files=all
```

## Step 2: Run SD Update Spec

Resolve the `sd-update-spec` skill by name and follow it as the source of truth
for the spec refresh. Let that skill delegate to Trellis update-spec and run the
pack-owned repository knowledge extensions. Do not replace the delegated skill
with manual update-spec or `.obsidian-kb` steps from this command.

After it completes, capture the dirty state again:

```bash
git status --short --untracked-files=all
```

## Step 3: Decide What To Commit

Fetch the base branch so the branch-diff check is current:

```bash
git fetch origin "$BASE_BRANCH"
BASE_REF="origin/$BASE_BRANCH"
```

If the working tree is clean, check whether the branch already contains commits
not on the base branch:

```bash
git rev-list --count "$BASE_REF"..HEAD
```

If there are no local changes and no commits ahead of the base branch, stop and
report that there is nothing to publish.

When local files changed, classify every changed and untracked path. It is safe
to include:

- user-requested implementation, docs, tests, and configuration changes
- spec, task, journal, or `.obsidian-kb` updates created by `sd-update-spec`
- pack-owned files that belong to the current work stream

Ask before staging unrelated, generated, local-only, ignored, secret-like, or
ambiguous files. In non-interactive sessions, stop instead of guessing.

Before committing, run whitespace validation on the intended diff:

```bash
git diff --check
git add <intended paths>
git diff --cached --check
```

Commit only when there is a staged diff. Use the user-provided commit message
when available; otherwise prefer the `SD_AI_COMMAND_PACK_CREATE_PR_COMMIT_MESSAGE`
environment variable, then a concise message derived from the work:

```bash
git commit -m "${SD_AI_COMMAND_PACK_CREATE_PR_COMMIT_MESSAGE:-chore: prepare pull request}"
```

If the branch already had all intended commits and no new local files changed,
skip the commit and continue to push/PR resolution.

## Step 4: Push The Branch

Push the current branch, setting upstream when needed:

```bash
git push -u origin HEAD
```

If push fails because the remote branch moved, fetch and inspect the divergence.
Do not force-push unless the user explicitly approves it for this branch.

## Step 5: Create Or Reuse The PR

First try to resolve an existing PR for the current branch:

```bash
gh pr view --json number,url,headRefName,baseRefName,state
```

If an open PR exists, reuse it. If no PR exists, create one against the detected
base branch. Prefer a user-provided title/body when supplied; otherwise let
GitHub CLI fill from the branch commits and then review the generated PR text
for placeholders or misleading scope before submitting.

```bash
gh pr create --base "$BASE_BRANCH" --fill
```

If `SD_AI_COMMAND_PACK_CREATE_PR_DRAFT=1`, create the PR as draft unless the
user explicitly asked for a ready PR.

After creation or reuse, capture:

- PR number and URL
- head branch and head SHA
- base branch

## Step 6: Enter The SD Review PR Loop

Set the PR selector for the handoff, then resolve and follow the `sd-review-pr`
skill as the source of truth:

```bash
export SD_AI_COMMAND_PACK_REVIEW_PR_SELECTOR="<pr-number-or-url>"
```

Let `sd-review-pr` run its deterministic local full-check gate with Prism and
Gito disabled, request the configured remote reviewer when appropriate, wait for
review completion, address actionable comments or CI failures, push review-fix
commits, re-request review after pushed fixes, observe its configured round
limit, run finish-work after a clean loop, and run housekeeping if it observes
the PR merged.

## Final Report

Report:

- Update-spec skill path and summary of spec or repository knowledge updates.
- Staged/committed paths and commit SHA, or why no commit was needed.
- Push target and result.
- PR number, URL, base branch, and whether the PR was created or reused.
- Confirmation that the workflow handed off to `sd-review-pr`.
- Project checks: configured command or reported candidates, and which project
  checks actually ran.
- Pack full-check: deterministic gate result with Prism/Gito disabled.
- Optional AI review: configured remote-review rounds and outcome.
- Comments fixed or rebutted, CI status, finish-work actions, and final
  working-tree state.
