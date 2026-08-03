---
name: sd-create-pr
description: Use when the user wants to update specs through the SD wrapper, commit and push the current branch, then create or reuse a GitHub pull request. Invocation is explicit approval for those in-scope commits, PR-branch pushes, and PR creation or reuse without another prompt.
---

# SD Create Pull Request

Use this project-local Software Delivery skill for `sd-create-pr` and
`/sd:create-pr` style work. It is a publish-only wrapper: it runs the
installed `sd-update-spec` workflow, commits and pushes the intended branch
changes, and creates or reuses the branch pull request. It runs no review:
the final report names `sd-review scope=pr` (or the full `sd-ship` chain) as
the next command.

## Standing GitHub authority

Invoking this workflow is explicit approval for its ordinary in-scope GitHub
actions: intended commits, pushes to the current PR branch, and PR creation or
reuse. Do not ask again solely because the diff/code will be committed,
pushed, or published. This does not authorize unrelated or ambiguous files,
force pushes, default-branch pushes, scope or risk expansion, review
requests, destructive actions, or bypassing any gate.

## Sandbox-safe tool execution

Run every `gh`, `uv`, `pip`, `ruff`, or `npm` command shown in this workflow
through `bash scripts/sd-ai-command-pack-toolchain.sh run -- <tool> [args...]`.
The argv-safe wrapper changes only documented cache variables and preserves
auth/config state. If it is missing or reports a cache-setup failure, stop with
that diagnostic; do not retry the tool bare or redirect `GH_CONFIG_DIR`.

## Structured decisions

Read [`../sd-help/references/structured-questions.md`](../sd-help/references/structured-questions.md)
before asking. This skill owns only `create-pr.file-scope`; use it for genuinely
ambiguous file inclusion, not for the normal publish or PR-reuse path. Never
offer a question as a way to cross the force-push or destructive boundary.

## Safety Rules

- Require `gh` and an authenticated GitHub session before creating or resolving
  a pull request: `gh --version` and `gh auth status`.
- Run the pack toolchain doctor once before dependency-sensitive work. Keep its
  selected Python and project-check report for this command run; do not retry
  raw interpreters in sequence after an authoritative candidate fails.
- Resolve `sd-update-spec` by name using the agent's trusted installed-skill
  resolver before starting. This skill never resolves or invokes a review
  skill in any mode; review ownership stays with `sd-review scope=pr` and the
  `sd-ship` composite after publication. Stop if a required skill is missing,
  unreadable, empty, resolves to more than one candidate, fails validation,
  defines contradictory steps that violate this command's safety rules, or
  requires unavailable tools.
- Do not duplicate the detailed update-spec workflow, and do not fold review
  behavior into this command: typed deterministic checks, configured remote
  reviewer requests, review polling, fix loops, CI handling, finish-work, and
  merge all belong to the successor review, ship, and housekeeping surfaces.
- Do not run Prism, Gito, or other local review providers directly from this
  command. The routed `sd-review` workflow owns the typed `sd-check` gate and
  every configured review-provider stage.
- Do not create a PR from the repository default branch. If the current branch
  is the default branch, create a feature branch before continuing. Prefer
  `SD_AI_COMMAND_PACK_CREATE_PR_BRANCH` when set; otherwise derive a concise
  `codex/<slug>` name from the requested work or commit message and fall back
  to a timestamped `codex/prepare-pr-<timestamp>` name when needed.
- Do not stage unrelated or ambiguous work. Capture the dirty state before and
  after `sd-update-spec`, classify all changed and untracked paths, and stage
  only files that clearly belong to the PR. Ask before touching ambiguous
  files; in non-interactive sessions, stop by default.
- Run the pack's deterministic review preflight against the complete intended
  branch and working-tree diff before staging a new commit or pushing an
  already-committed branch. Never publish when that gate is missing or fails.
- Do not create a duplicate PR. If the current branch already has an open PR,
  reuse it and continue to the final report.
- Never pass generated or user-provided Markdown through `gh pr create --body`
  or `gh pr edit --body` in a shell command. Markdown commonly contains
  backticks, dollar signs, and command-substitution syntax. Materialize the
  exact body in a temporary regular file with a literal file-writing API, pass
  it through `--body-file`, and remove the file after the GitHub command.
- Do not assume the base branch is `main`. Detect the repository default branch
  with GitHub metadata when available, and let
  `SD_AI_COMMAND_PACK_CREATE_PR_BASE` override it when the target repo needs a
  different base.
- If a command, provider call, push, PR creation, or delegated skill step fails,
  stop and report the command, exit status, and complete stdout/stderr output.

## Invocation Modes

`sd-create-pr` has one behavior in every invocation: publish or reuse the
pull request, then report the next command in Step 6. There is no
composite-only delegation mode or internal orchestration context —
`sd-ship` Stage 1 invokes this same public flow and reads the Step 6
report. If the invocation carries `publish-only`, `caller=`, `stage=`, or
`return-after=`, reject the request before Step 1 and make no update-spec,
branch, commit, push, or PR changes.

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

Before staging a new commit or pushing an already-committed branch, run
whitespace validation and the deterministic pack review preflight on the
complete intended diff. The preflight catches invalid Trellis task metadata,
generated `_example` task-context rows, and task-context references outside
spec/research files before publication:

```bash
git diff --check "$BASE_REF"...HEAD
git diff --check
if [ ! -f scripts/sd-ai-command-pack-review-preflight.mjs ]; then
  printf '%s\n' "error: scripts/sd-ai-command-pack-review-preflight.mjs is missing; reinstall sd-ai-command-pack before publishing." >&2
  exit 1
fi
node scripts/sd-ai-command-pack-review-preflight.mjs
```

If the preflight exits nonzero, stop before staging, committing, or pushing and
report its complete output. Do not treat a later `sd-review scope=pr` run as a
substitute for this pre-publication gate.

When a new commit is needed, stage only the classified intended paths and
validate the staged diff:

```bash
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
base branch. Prefer a user-provided title/body when supplied. For a custom or
generated body, write the exact Markdown to a temporary file without shell
evaluation: do not use `eval`, command substitution, an unquoted heredoc, or an
inline `--body` argument. Then pass only the temporary path to GitHub CLI:

```bash
PR_BODY_FILE=$(mktemp "${TMPDIR:-/tmp}/sd-ai-command-pack-pr-body.XXXXXX")
cleanup_pr_body() { rm -f -- "$PR_BODY_FILE"; }
trap cleanup_pr_body EXIT HUP INT TERM

# Populate "$PR_BODY_FILE" through the agent or platform's literal file API.
gh pr create --base "$BASE_BRANCH" --title "$PR_TITLE" --body-file "$PR_BODY_FILE"
```

Use the same `--body-file` rule when editing an existing PR body. If no custom
body is needed, let GitHub CLI fill from the branch commits. Never run the
automatic preparation mode for a user-provided body: preserve that body
byte-for-byte and leave the existing strict scope validator authoritative.

For the no-custom-body path, use secure regular temporary files to capture the
exact auto-filled body and the NUL-delimited branch diff. The same Step 5 flow
applies to every invocation, including `sd-ship` Stage 1:

```bash
if ! gh pr create --base "$BASE_BRANCH" --fill; then
  printf '%s\n' "error: PR creation failed; stop before Step 6." >&2
  exit 1
fi

PR_BODY_FILE=
CHANGED_FILES_FILE=
cleanup_generated_pr_body() {
  if [ -n "$PR_BODY_FILE" ]; then
    rm -f -- "$PR_BODY_FILE"
  fi
  if [ -n "$CHANGED_FILES_FILE" ]; then
    rm -f -- "$CHANGED_FILES_FILE"
  fi
}
trap cleanup_generated_pr_body EXIT HUP INT TERM

if ! PR_BODY_FILE=$(mktemp "${TMPDIR:-/tmp}/sd-ai-command-pack-pr-body.XXXXXX"); then
  printf '%s\n' "error: cannot create secure PR-body temporary file; stop before Step 6." >&2
  exit 1
fi
if ! CHANGED_FILES_FILE=$(mktemp "${TMPDIR:-/tmp}/sd-ai-command-pack-pr-files.XXXXXX"); then
  printf '%s\n' "error: cannot create secure changed-files temporary file; stop before Step 6." >&2
  exit 1
fi

if ! git diff --name-only -z "$BASE_REF"...HEAD > "$CHANGED_FILES_FILE"; then
  printf '%s\n' "error: cannot capture NUL-delimited changed paths; stop before Step 6." >&2
  exit 1
fi
if ! gh pr view --json body --jq .body > "$PR_BODY_FILE"; then
  printf '%s\n' "error: cannot fetch GitHub's auto-filled PR body; stop before Step 6." >&2
  exit 1
fi

PREPARE_STATUS=0
bash scripts/sd-ai-command-pack-toolchain.sh run-python -- \
  scripts/sd-ai-command-pack-pr-body-scope.py \
  --prepare-tooling-body \
  --body-file "$PR_BODY_FILE" \
  --changed-files "$CHANGED_FILES_FILE" \
  || PREPARE_STATUS=$?

case "$PREPARE_STATUS" in
  0)
    if ! gh pr edit --body-file "$PR_BODY_FILE"; then
      printf '%s\n' "error: automatic PR-body update failed; stop before Step 6." >&2
      exit 1
    fi
    ;;
  3)
    : # The helper already reported the bounded non-error result on stdout.
    ;;
  *)
    printf '%s\n' "error: automatic PR-body scope preparation failed; stop before Step 6." >&2
    exit "$PREPARE_STATUS"
    ;;
esac
```

Exit `3` is the helper's non-error mixed-scope result. Any other nonzero status,
an unavailable helper/toolchain, a non-regular temporary body, or a failed body
fetch/edit blocks the handoff: stop before Step 6 so review never starts with a
known-missing tooling/generated section. The helper owns the canonical path
classification and appends the recognized section only when every changed path
is tooling/generated or repository bookkeeping; the skill must not duplicate
those patterns.

If `SD_AI_COMMAND_PACK_CREATE_PR_DRAFT=1`, create the PR as draft unless the
user explicitly asked for a ready PR.

After creation or reuse, capture:

- PR number and URL
- head branch and head SHA
- base branch

## Step 6: Report The Next Command

Publication ends this command's work in every invocation, including
`sd-ship` Stage 1. Do not resolve or invoke any review skill, finish-work,
housekeeping, or a polling loop. The final report names the next command
instead: `sd-review scope=pr` for the review loop alone, or `sd-ship` for
the remaining publish-to-merge chain. A composite caller reads the Step 5
PR number, URL, base branch, head branch, head SHA, and created/reused
result from this report; the composite owns its separate Stage 2
(`sd-review scope=pr`) and its Stage 2b lifecycle step.

## Final Report

Report:

- Update-spec skill path and summary of spec or repository knowledge updates.
- Pre-publication review preflight result.
- Staged/committed paths and commit SHA, or why no commit was needed.
- Push target and result.
- PR number, URL, base branch, and whether the PR was created or reused.
- Outcome: the recommended next command (`sd-review scope=pr`, or `sd-ship`
  for the full chain).
- Final working-tree state.
