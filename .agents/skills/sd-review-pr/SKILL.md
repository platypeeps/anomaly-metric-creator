---
name: sd-review-pr
description: Use when the user asks to ready a pull request, run the deterministic local PR gate, optionally request the configured remote reviewer when available, address review comments or CI failures, and repeat until no actionable comments remain.
---

# SD PR Review Loop

Use this project-local Software Delivery skill for `sd-review-pr` and
`/sd:review-pr` style work. It turns a draft or in-progress PR into a reviewed
PR by running the deterministic local full-check gate first, inspecting
existing comments and CI, and requesting the configured remote reviewer after a
clean local pass and after every pushed review-fix commit made during the loop.
GitHub Copilot is the default remote reviewer unless a repo overrides it.

## Safety Rules

- Require `gh` and an authenticated GitHub session before starting:
  `gh --version` and `gh auth status`.
- Run the pack toolchain doctor once before dependency-sensitive work. Reuse
  that selected Python for the command run and do not probe raw interpreters in
  sequence after an authoritative candidate fails.
- Resolve the pull request from the current branch unless the user supplies a
  PR number or URL. Stop if no PR is found.
- Do not stage unrelated work. If the working tree is dirty before the first
  review loop, classify the paths. Commit/push only changes that clearly belong
  to this PR; ask before touching anything ambiguous.
- Run the deterministic local full-check before requesting a paid/remote
  review. Disable Prism and Gito for this command-owned gate; those local
  review tools run only when the user explicitly invokes `sd-full-check`,
  or `sd-review-local` (optionally with `all`).
- Treat `sd-review-pr` as a bounded remote-review convergence loop by default.
  Unless the user explicitly asks for local-only review, request the configured
  remote reviewer after the local gate passes and again after each pushed
  review-fix commit made during this command run. Do not ask for permission
  before each configured remote-review request; stop only when the loop is clean
  or the configured round limit is reached.
- Count one remote loop as: trigger the configured remote reviewer, wait for
  completion, inspect review/CI state, address findings, and push any resulting
  commit. After the configured remote round limit, default five, stop before
  starting another loop and ask the user for permission to continue.
- Treat the configured remote reviewer and other bot comments as actionable by
  default, but verify against the current diff, project specs, and tests before
  changing code.
- The user grants standing permission to reply to review comments and resolve
  review threads during this loop. Do not ask for separate permission before
  replying or resolving once a thread is fixed, rebutted with evidence, or
  confirmed already addressed.
- If a comment is wrong or would regress behavior, reply with a concise
  rebuttal explaining the evidence, then resolve the review thread when the
  platform permits it.
- Do not resolve valid unaddressed or ambiguous threads. Ask the user when a
  comment requires product judgment, scope expansion, or a tradeoff decision.
- If the PR state is `MERGED` at startup, during polling, or before the final
  report, stop the review loop and run post-merge housekeeping automatically
  with `bash scripts/sd-ai-command-pack-housekeeping.sh`.
- End by recommending documentation, spec, prompt, or pre-commit improvements
  that would prevent avoidable review feedback next time. Do not apply those
  recommendations unless the user asks.

## Step 1: Resolve PR And Local State

```bash
bash scripts/sd-ai-command-pack-toolchain.sh doctor
gh --version
gh auth status
git status -sb
REPO_FULL=$(gh repo view --json nameWithOwner --jq .nameWithOwner)
OWNER=${REPO_FULL%/*}
REPO=${REPO_FULL#*/}
PR_SELECTOR="${SD_AI_COMMAND_PACK_REVIEW_PR_SELECTOR:-}"
PR_VIEW_ARGS=()
if [ -n "$PR_SELECTOR" ]; then
  PR_VIEW_ARGS=("$PR_SELECTOR")
fi
gh_pr_view_checked() {
  gh pr view "${PR_VIEW_ARGS[@]}" "$@" || {
    printf '%s\n' "Unable to resolve the pull request. Check out a PR branch or set SD_AI_COMMAND_PACK_REVIEW_PR_SELECTOR to a PR number or URL." >&2
    exit 1
  }
}
PR_NUMBER=$(gh_pr_view_checked --json number --jq .number)
PR_URL=$(gh_pr_view_checked --json url --jq .url)
HEAD_BRANCH=$(gh_pr_view_checked --json headRefName --jq .headRefName)
HEAD_SHA=$(gh_pr_view_checked --json headRefOid --jq .headRefOid)
BASE_BRANCH=$(gh_pr_view_checked --json baseRefName --jq .baseRefName)
LOCAL_HEAD=$(git rev-parse HEAD)
test -n "$PR_NUMBER" && test "$PR_NUMBER" != "null"
gh_pr_view_checked --json number,url,isDraft,headRefName,headRefOid,baseRefName,state,reviewRequests,latestReviews,statusCheckRollup
```

If the toolchain helper is missing, stop and report that the pack should be
reinstalled. Route ad hoc Python validations through
`bash scripts/sd-ai-command-pack-toolchain.sh run-python --require-module
<name> -- <arguments>` so dependency failures are reported once.

Capture:

- `PR_NUMBER`
- `PR_URL`
- `HEAD_BRANCH`
- `HEAD_SHA`
- `LOCAL_HEAD`
- `BASE_BRANCH`
- `REPO_FULL`, `OWNER`, and `REPO`

Before marking the PR ready or requesting review, confirm local `HEAD` matches
the PR head SHA:

```bash
test "$LOCAL_HEAD" = "$HEAD_SHA"
```

If the SHAs differ, stop and report both values. Usually this means local
commits have not been pushed, the wrong branch is checked out, or the remote PR
branch changed. Push the intended local commits or check out the PR head before
continuing; do not request remote review on stale remote code.

If the PR is draft, do not mark it ready until the deterministic local
full-check has passed unless the user explicitly asked to mark it ready. This
keeps `ready_for_review` workflows from starting before local review is clean.

## Step 1.5: Post-Merge Handoff

If the PR is already merged, do not continue the review loop. Run housekeeping
inside the current command run:

```bash
PR_STATE=$(gh pr view "$PR_NUMBER" --json state --jq .state)
if [ "$PR_STATE" = "MERGED" ]; then
  bash scripts/sd-ai-command-pack-housekeeping.sh
  exit 0
fi
```

If `scripts/sd-ai-command-pack-housekeeping.sh` is missing, read
`.agents/skills/sd-housekeeping/SKILL.md` if present and report that the
pack should be reinstalled because the canonical script is absent.

This handoff is command-local. It works when the running agent observes the
merge through `gh`, including a user merging the PR while the command is still
running. It is not a background GitHub webhook and cannot wake a closed or idle
tool session by itself.

## Step 2: Run Local Full Check

Run the deterministic local full-check gate before requesting a remote review.
This PR-review cycle must not run Prism or Gito implicitly, even if the
environment would normally enable them for `sd-full-check`:

```bash
SD_AI_COMMAND_PACK_FULL_CHECK_PRISM=0 \
SD_AI_COMMAND_PACK_FULL_CHECK_GITO=0 \
bash scripts/sd-ai-command-pack-full-check.sh
```

If `scripts/sd-ai-command-pack-full-check.sh` is missing, stop and report that the review
cycle pack is not fully installed. Do not fall back to remote-review-first
review.

If full-check fails, fix the smallest correct set of issues, run the relevant
checks again, commit and push the fixes, then return to Step 1 to refresh PR
state. Do not request remote review on code that has not passed the local gate
unless the user explicitly asks for a remote diagnostic despite local failures.

## Step 3: Decide Whether To Trigger Remote Review

Configure the remote reviewer before making any remote-review decision:

```bash
REMOTE_REVIEWER="${SD_AI_COMMAND_PACK_REVIEW_PR_REMOTE_REVIEWER:-@copilot}"
REMOTE_REVIEWER_LABEL="${SD_AI_COMMAND_PACK_REVIEW_PR_REMOTE_REVIEWER_LABEL:-GitHub Copilot}"
REMOTE_REVIEW_AUTHOR_MATCH="${SD_AI_COMMAND_PACK_REVIEW_PR_REMOTE_AUTHOR_MATCH:-}"
REMOTE_REVIEW_REQUEST_COMMAND="${SD_AI_COMMAND_PACK_REVIEW_PR_REMOTE_REQUEST_COMMAND:-}"
REMOTE_REVIEW_ROUND_LIMIT="${SD_AI_COMMAND_PACK_REVIEW_PR_REMOTE_ROUND_LIMIT:-5}"
REMOTE_REVIEW_SETTLE_POLLS="${SD_AI_COMMAND_PACK_REVIEW_PR_REMOTE_SETTLE_POLLS:-40}"

if [ -z "$REMOTE_REVIEW_AUTHOR_MATCH" ]; then
  if [ "$REMOTE_REVIEWER" = "@copilot" ]; then
    REMOTE_REVIEW_AUTHOR_MATCH="copilot-pull-request-reviewer[bot]"
  else
    REMOTE_REVIEW_AUTHOR_MATCH="$REMOTE_REVIEWER"
  fi
fi
```

Use `REMOTE_REVIEWER` as the GitHub request identity and
`REMOTE_REVIEWER_LABEL` in user-facing status and reports. Copilot deliberately
uses different identities: `@copilot` for the documented CLI request and
`copilot-pull-request-reviewer[bot]` for review/comment author matching. For
other GitHub reviewers, the configured reviewer is also the default author
matcher. If the provider is not triggered by a standard GitHub reviewer
request, set `REMOTE_REVIEW_REQUEST_COMMAND` in the target repo's documented
environment and run that command instead.

If both `REMOTE_REVIEWER` and `REMOTE_REVIEW_REQUEST_COMMAND` are empty, skip
remote-review requests, report that no remote reviewer is configured, and
continue to Step 5 to inspect existing comments and CI.

After deterministic local full-check passes:

- If the PR is draft and the user asked to ready it, mark it ready:

  ```bash
  gh pr ready "$PR_NUMBER"
  ```

- If the user asked for local review only, skip remote review and continue to
  Step 5 to inspect existing comments and CI.
- Otherwise, trigger the configured remote reviewer when no remote review has
  been requested for the current PR head during this command run.
- Also trigger the configured remote reviewer after every pushed commit created
  by Step 6 to address review comments, CI failures, or deterministic local
  gate findings.
  This includes fixes for review comments that existed before this command was
  invoked; after those fixes are pushed and the deterministic local full-check
  passes, request a fresh remote review.
- If the current PR head already has a completed remote review and this command
  has not pushed any fixes, inspect existing comments and CI before deciding
  whether another remote request is needed.

Record a remote review round only when the configured request command succeeds.
That records a **request attempted** state, not a completed remote review.

Record a UTC trigger timestamp before requesting a review. Use the `HEAD_SHA`
captured in Step 1 for the review round; if that value may be stale, return to
Step 1 before requesting review.

```bash
date -u +"%Y-%m-%dT%H:%M:%SZ"
```

Primary request path:

```bash
if [ -n "$REMOTE_REVIEW_REQUEST_COMMAND" ]; then
  bash -c "$REMOTE_REVIEW_REQUEST_COMMAND"
elif [ "$REMOTE_REVIEWER" = "@copilot" ]; then
  gh pr edit "$PR_NUMBER" --add-reviewer @copilot
elif [ -n "$REMOTE_REVIEWER" ]; then
  gh api -X POST \
    "repos/$OWNER/$REPO/pulls/$PR_NUMBER/requested_reviewers" \
    -f reviewers[]="$REMOTE_REVIEWER"
else
  printf '%s\n' "No remote reviewer configured; skipping remote review request."
fi
```

For a non-Copilot GitHub reviewer, if the REST request fails because the
reviewer is unavailable or the slug changed, try the equivalent high-level
command:

```bash
gh pr edit "$PR_NUMBER" --add-reviewer "$REMOTE_REVIEWER"
```

If the documented Copilot CLI request fails, stop and report whether the error
indicates repository policy, reviewer availability, or another GitHub failure;
do not retry it through the generic collaborator REST path. If both generic
GitHub reviewer request paths fail, stop and report the exact GitHub error. If a
custom `REMOTE_REVIEW_REQUEST_COMMAND` fails, stop and report that exact error.
Do not assume a magic trigger comment such as `@copilot review` works unless the
repository or organization docs say that trigger is enabled.

## Step 4: Wait For Review Completion

Skip this step if no remote review was requested. Otherwise, poll every 30
seconds with lightweight PR metadata only, for at most
`REMOTE_REVIEW_SETTLE_POLLS` polls. Keep updates short.

```bash
gh pr view "$PR_NUMBER" --json reviewRequests,latestReviews,headRefOid,updatedAt
```

Do not paginate full review/comment bodies during every polling interval. Once
the configured reviewer request clears, or `latestReviews` / `updatedAt`
indicates new remote-review activity after the trigger timestamp, fetch the full
review and comment bodies once before moving to Step 5:

```bash
gh api "repos/$OWNER/$REPO/pulls/$PR_NUMBER/reviews" --paginate
gh api "repos/$OWNER/$REPO/pulls/$PR_NUMBER/comments" --paginate
gh api "repos/$OWNER/$REPO/issues/$PR_NUMBER/comments" --paginate
```

Track these states separately:

- **Request attempted:** the request command returned success for the recorded
  trigger timestamp and head SHA.
- **Request pending:** the reviewer is still requested, or the bounded settle
  window has not expired and no author-matched activity is visible yet.
- **Review materialized:** the one-time full fetch confirms a review, review
  comment, or top-level PR comment from `REMOTE_REVIEW_AUTHOR_MATCH` after the
  trigger timestamp for the recorded head SHA. Re-read the PR head before
  accepting the evidence; use review/comment commit ids where available, and
  accept a top-level comment only when the head remained unchanged.
- **Ambiguous/no materialization:** the bounded polls and one-time full fetch
  found no qualifying author-matched activity, even if the reviewer request
  disappeared.

Only **review materialized** completes the remote-review wait. A successful
request command, `updatedAt` change, or cleared reviewer request is a polling
hint, never completion evidence by itself.

For custom remote-review triggers that do not expose a GitHub reviewer request,
use the same author-matched activity requirement after the trigger timestamp on
the unchanged recorded head.

The completion signal can fire BEFORE the reviewer's inline review-thread
comments are queryable: some reviewers (GitHub Copilot in particular) post the
review event first and attach the per-line threads a few seconds later. So a
thread read taken the instant completion is detected can report zero unresolved
threads prematurely — a false "clean". Guard against it two ways:

- After detecting completion, wait a short settle interval (about 30–45s) before
  the one-time full fetch and the Step 5 thread read, so late-attached inline
  comments are present.
- Treat the completion signal as necessary but NOT sufficient. The authoritative
  clean check is the thread state re-read immediately before merge (the same
  unresolved-thread guard the housekeeping merge step enforces), not a single
  post-completion read. Never declare the loop clean or attempt a merge off one
  early read; if the merge guard reports unresolved threads after you saw zero,
  that is this race — fetch the now-present threads and address them.

If the bounded polls expire without materialization, perform the one-time full
fetch, then stop with a diagnostic rather than counting a round as completed.
Report the request path, reviewer, author matcher, trigger timestamp, head SHA,
poll count/duration, and the
`SD_AI_COMMAND_PACK_REVIEW_PR_REMOTE_REQUEST_COMMAND` override. Distinguish an
accepted request with no observable activity from a policy/availability
rejection.

## Step 5: Inspect Comments, Prior Threads, And CI

Fetch thread-aware review data. Prefer platform/GitHub tools that expose review
thread ids and resolution state. If using `gh`, use GraphQL:

```bash
gh api graphql --paginate \
  -F owner="$OWNER" \
  -F repo="$REPO" \
  -F number="$PR_NUMBER" \
  -f query='
query($owner:String!, $repo:String!, $number:Int!, $endCursor:String) {
  repository(owner:$owner, name:$repo) {
    pullRequest(number:$number) {
      reviewThreads(first:100, after:$endCursor) {
        pageInfo {
          hasNextPage
          endCursor
        }
        nodes {
          id
          isResolved
          isOutdated
          path
          line
          comments(first:100) {
            pageInfo {
              hasNextPage
              endCursor
            }
            nodes {
              id
              databaseId
              author { login }
              body
              createdAt
              url
            }
          }
        }
      }
    }
  }
}'
```

`--paginate` keeps requesting review thread pages by passing `pageInfo.endCursor`
as `$endCursor` while `pageInfo.hasNextPage` is true. If not using
`--paginate`, repeat the query with `-F endCursor="<endCursor>"` until
`hasNextPage` is false before deciding the PR is clean.

Nested review-thread comments also paginate. If any thread's
`comments.pageInfo.hasNextPage` is true, fetch the remaining comments for that
thread with a follow-up GraphQL query using the thread id and comment
`endCursor` before deciding the thread has no additional actionable feedback.

Fetch top-level PR conversation comments through the issues comments API because
pull requests are issues for regular conversation comments:

```bash
gh api "repos/$OWNER/$REPO/issues/$PR_NUMBER/comments" --paginate
```

Also check CI:

```bash
gh pr checks "$PR_NUMBER" --json name,workflow,state,bucket,link,completedAt
```

If checks fail, inspect failed logs with `gh run view --log-failed` or the
workflow link, identify the root cause, fix it if appropriate, and include it
in the same review-response commit.

Classify all unresolved, non-outdated threads and any top-level bot comments:

- **Valid actionable issue**: implement the smallest correct fix and add/update
  tests when behavior changes.
- **Valid but not worth changing now**: ask the user if it changes scope or
  carries product/architecture tradeoffs.
- **Invalid / already addressed / conflicts with project rules**: reply with a
  brief rebuttal citing the code, test, or spec evidence, then resolve the
  thread when possible.
- **Ambiguous**: ask one concise question or draft a proposed reply.

Do not ignore prior unresolved comments from earlier review rounds; the loop is
done only when prior unresolved threads, new remote-review feedback, and CI
failures are all handled.

## Step 6: Reply, Resolve, Fix, Commit, And Push

For a rebuttal or clarification on a review comment, reply to the review
comment:

```bash
COMMENT_DATABASE_ID="<review comment database id>"

gh api -X POST \
  "repos/$OWNER/$REPO/pulls/$PR_NUMBER/comments/$COMMENT_DATABASE_ID/replies" \
  -f body="..."
```

Resolve a review thread after either fixing it or posting a rebuttal:

```bash
THREAD_NODE_ID="<review thread node id>"

gh api graphql \
  -F threadId="$THREAD_NODE_ID" \
  -f query='
mutation($threadId:ID!) {
  resolveReviewThread(input:{threadId:$threadId}) {
    thread { id isResolved }
  }
}'
```

For top-level PR comments that do not belong to a review thread, reply in the
conversation if needed; there is no thread to resolve.

When files change:

1. Run the narrowest relevant tests first, then the broader checks warranted by
   the touched scope.
2. Stage only intended files.
3. Commit with a review-round message, for example:
   `address local review feedback`,
   `address remote review feedback`, or
   `fix: address review feedback round N`.
4. Push the current branch.
5. Return to Step 1 before making another review, CI, or remote-trigger
   decision so `HEAD_SHA`, review requests, latest reviews, and PR state are
   fresh.
6. Treat the pushed commit as requiring another configured remote-review
   request after Step 2 passes, unless the user asked for local-only review or
   no remote reviewer is configured.

```bash
git status -sb
git add <intended paths>
git commit -m "fix: address review feedback round N"
git push
```

If no files changed because every finding was rebutted, resolved, or confirmed
already addressed, skip the commit and push. Refresh PR state if remote review
or CI status could have changed; otherwise stop when the Step 7 conditions are
already satisfied.

## Step 7: Repeat Or Stop

After every round that produced a code/docs change, return to Step 1 to refresh
all PR state, then run deterministic local full-check again. If deterministic
local full-check passes and remote review is configured, trigger another remote
review before considering the loop clean. Compare the new thread ids and
timestamps with the refreshed snapshot, and do not request duplicate remote
reviews for the same PR head when no review-fix commit has been pushed since
the latest requested remote review.

Stop when:

- deterministic local full-check passes;
- the latest requested remote review produces no new actionable
  comments;
- no review-fix commit has been pushed since the latest requested remote review;
- all prior review threads are resolved or intentionally left with a documented
  user decision;
- CI is passing or any non-passing check is documented as unrelated/flaky with
  user-visible evidence; and
- the working tree is clean and the branch is pushed.

If the remote loop would exceed `REMOTE_REVIEW_ROUND_LIMIT`, ask:

> Remote review has already run the configured round limit. Continue another
> round?

Do not continue until the user approves.

## Step 8: Finish Work And Post-Merge Housekeeping Automatically

After the loop stops because deterministic local full-check passes and no
requested remote review produced new actionable comments, run the Trellis
finish-work flow automatically before the final report. Do not ask whether to
run it; a clean review loop is the trigger.

1. Read `.agents/skills/trellis-finish-work/SKILL.md`.
2. Use that skill as the primary instructions to archive completed task state
   and record the session journal.
3. If finish-work creates archive or journal commits, push the current branch
   after those commits are created:

```bash
git status -sb
git push
```

If finish-work detects uncommitted PR work or ambiguous unrelated files, follow
the routing in `trellis-finish-work` rather than forcing a commit. The PR review
loop is complete only after finish-work has either completed successfully or
reported a concrete blocker.

Before the final report, refresh PR state:

```bash
PR_STATE=$(gh pr view "$PR_NUMBER" --json state --jq .state)
```

If `PR_STATE` is `MERGED`, immediately run:

```bash
bash scripts/sd-ai-command-pack-housekeeping.sh
```

Then include the housekeeping clean-state/anomaly report in the final response.
If housekeeping reports anomalies, do not mask them; they are the post-merge
handoff.

## Final Report

Report:

- PR number and URL.
- Project checks: configured command or reported candidates, and which project
  checks actually ran.
- Pack full-check: whether the deterministic local gate passed with Prism/Gito
  disabled for the `sd-review-pr` cycle.
- Remote review rounds used: <n> of <limit> — mandatory in every report;
  write `0 of <limit>` with the skip reason when no remote review ran.
- Optional AI review: reviewer label/slug used, or why remote review was
  skipped.
- Comments fixed, rebutted, or left for user decision.
- Commits pushed during the loop.
- Finish-work actions and any archive/journal commits pushed.
- Housekeeping actions if the PR was already merged or became merged while the
  command was running.
- CI status.
- Final working-tree state.
- Recommended internal documentation, spec, prompt, or pre-commit changes that
  would have caught avoidable mistakes before invoking remote review.
