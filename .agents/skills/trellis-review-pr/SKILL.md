---
name: trellis-review-pr
description: Use when the user asks to ready a pull request, run the local Prism-first review cycle, optionally request a GitHub Copilot code review, address review comments or CI failures, and repeat until no actionable comments remain.
---

# Trellis PR Review Loop

Use this project-local skill for `/trellis:review-pr` style work. It turns a
draft or in-progress PR into a reviewed PR by running the local full-check and
Prism review first, inspecting existing comments and CI, and requesting GitHub
Copilot review only when the user explicitly asks for a remote/final pass or
when local review is clean and a remote review is intentionally warranted.

## Safety Rules

- Require `gh` and an authenticated GitHub session before starting:
  `gh --version` and `gh auth status`.
- Resolve the pull request from the current branch unless the user supplies a
  PR number or URL. Stop if no PR is found.
- Do not stage unrelated work. If the working tree is dirty before the first
  review loop, classify the paths. Commit/push only changes that clearly belong
  to this PR; ask before touching anything ambiguous.
- Run the local full-check before requesting paid/remote Copilot review. Use:
  `bash scripts/trellis-full-check.sh`.
- Count one remote loop as: trigger Copilot review, wait for completion,
  inspect review/CI state, address findings, and push any resulting commit.
  After five remote loops, stop before starting a sixth and ask the user for
  permission to continue.
- Treat Copilot and other bot comments as actionable by default, but verify
  against the current diff, project specs, and tests before changing code.
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
  with `bash scripts/trellis-housekeeping.sh`.
- End by recommending documentation, spec, prompt, or pre-commit improvements
  that would prevent avoidable Copilot feedback next time. Do not apply those
  recommendations unless the user asks.

## Step 1: Resolve PR And Local State

```bash
gh --version
gh auth status
git status -sb
REPO_FULL=$(gh repo view --json nameWithOwner --jq .nameWithOwner)
OWNER=${REPO_FULL%/*}
REPO=${REPO_FULL#*/}
PR_NUMBER=$(gh pr view --json number --jq .number)
PR_URL=$(gh pr view --json url --jq .url)
HEAD_BRANCH=$(gh pr view --json headRefName --jq .headRefName)
HEAD_SHA=$(gh pr view --json headRefOid --jq .headRefOid)
BASE_BRANCH=$(gh pr view --json baseRefName --jq .baseRefName)
LOCAL_HEAD=$(git rev-parse HEAD)
gh pr view --json number,url,isDraft,headRefName,headRefOid,baseRefName,state,reviewRequests,latestReviews,statusCheckRollup
```

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
continuing; do not request Copilot review on stale remote code.

If the PR is draft, do not mark it ready until the local full-check has passed
unless the user explicitly asked to mark it ready immediately. This keeps
`ready_for_review` workflows from starting before local review is clean.

## Step 1.5: Post-Merge Auto-Dispatch

If the PR is already merged, do not continue the review loop. Run housekeeping
inside the active session:

```bash
PR_STATE=$(gh pr view "$PR_NUMBER" --json state --jq .state)
if [ "$PR_STATE" = "MERGED" ]; then
  bash scripts/trellis-housekeeping.sh
fi
```

If `scripts/trellis-housekeeping.sh` is missing, read
`.agents/skills/trellis-housekeeping/SKILL.md` if present and report that the
pack should be reinstalled because the canonical script is absent.

This auto-dispatch is session-local. It works when the active agent observes
the merge through `gh`, including a user merging the PR while the session is
still running. It is not a background GitHub webhook and cannot wake a closed
or idle tool session by itself.

## Step 2: Run Local Full Check

Run the local full-check gate before requesting a remote review:

```bash
bash scripts/trellis-full-check.sh
```

If `scripts/trellis-full-check.sh` is missing, stop and report that the review
cycle pack is not fully installed. Do not fall back to Copilot-first review.

If full-check fails, fix the smallest correct set of issues, run the relevant
checks again, commit and push the fixes, then return to Step 1 to refresh PR
state. Do not request Copilot review on code that has not passed the local gate
unless the user explicitly asks for a remote diagnostic despite local failures.

## Step 3: Decide Whether To Trigger Copilot Review

After local full-check passes:

- If the PR is draft and the user asked to ready it, mark it ready:

  ```bash
  gh pr ready "$PR_NUMBER"
  ```

- If the user asked for local review only, skip Copilot and continue to Step 5
  to inspect existing comments and CI.
- If the user asked for a final remote review, or if project policy says to run
  one before merge, trigger Copilot review.

Record a remote review round only when Copilot is actually requested.

Record a UTC trigger timestamp and the current head SHA before requesting a
review:

```bash
date -u +"%Y-%m-%dT%H:%M:%SZ"
gh pr view "$PR_NUMBER" --json headRefOid
```

Primary request path:

```bash
gh api -X POST \
  "repos/$OWNER/$REPO/pulls/$PR_NUMBER/requested_reviewers" \
  -f reviewers[]=copilot-pull-request-reviewer
```

If GitHub rejects the request because Copilot review is unavailable or the
reviewer slug changed, try the equivalent high-level command:

```bash
gh pr edit "$PR_NUMBER" --add-reviewer copilot-pull-request-reviewer
```

If both fail, stop and report the exact GitHub error. Do not assume a magic
`@copilot review` comment works unless the repository or organization docs
explicitly say that trigger is enabled.

## Step 4: Wait For Review Completion

Skip this step if no remote Copilot review was requested. Otherwise, poll every
30 seconds with lightweight PR metadata only. Keep updates short.

```bash
gh pr view "$PR_NUMBER" --json reviewRequests,latestReviews,headRefOid,updatedAt
```

Do not paginate full review/comment bodies during every polling interval. Once
the Copilot review request clears, or `latestReviews` / `updatedAt` indicates
new Copilot activity after the trigger timestamp, fetch the full review and
comment bodies once before moving to Step 4:

```bash
gh api "repos/$OWNER/$REPO/pulls/$PR_NUMBER/reviews" --paginate
gh api "repos/$OWNER/$REPO/pulls/$PR_NUMBER/comments" --paginate
gh api "repos/$OWNER/$REPO/issues/$PR_NUMBER/comments" --paginate
```

Consider the Copilot review complete when:

- `copilot-pull-request-reviewer` is no longer present in review requests; and
- `latestReviews` or the one-time full fetch confirms a Copilot review, review
  comment, or top-level PR comment after the trigger timestamp for the head SHA;
  or
- the review request disappears and remains absent through two polling
  intervals.

If review status is ambiguous for more than 20 minutes, report the state and ask
whether to keep waiting.

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
          comments(first:50) {
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

Fetch top-level PR conversation comments through the issues comments API because
pull requests are issues for regular conversation comments:

```bash
gh api "repos/$OWNER/$REPO/issues/$PR_NUMBER/comments" --paginate
```

Also check CI:

```bash
gh pr checks "$PR_NUMBER" --watch --fail-fast
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
done only when prior unresolved threads, new Copilot feedback, and CI failures
are all handled.

## Step 6: Reply, Resolve, Fix, Commit, And Push

For a rebuttal or clarification on a review comment, reply to the review
comment:

```bash
gh api -X POST \
  "repos/$OWNER/$REPO/pulls/comments/{comment_database_id}/replies" \
  -f body="..."
```

Resolve a review thread after either fixing it or posting a rebuttal:

```bash
gh api graphql \
  -F threadId="THREAD_NODE_ID" \
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
   `address Copilot review feedback`, or
   `address review feedback round N`.
4. Push the current branch.

```bash
git status -sb
git add <intended paths>
git commit -m "address review feedback"
git push
```

If no files changed because every finding was rebutted/resolved, skip the
commit but still continue to the next review-trigger decision.

## Step 7: Repeat Or Stop

Run local full-check again after every round that produced a code/docs change.
Trigger another Copilot review only when the prior remote review produced
actionable feedback that has now been fixed or rebutted and the user still
wants a remote loop. Compare the new thread ids and timestamps with the prior
snapshot.

Stop when:

- local full-check passes;
- the latest remote Copilot review, if requested, produces no new actionable
  comments;
- all prior review threads are resolved or intentionally left with a documented
  user decision;
- CI is passing or any non-passing check is documented as unrelated/flaky with
  user-visible evidence; and
- the working tree is clean and the branch is pushed.

If the remote loop would start round 6, ask:

> Copilot review has already run five remote rounds. Continue another round?

Do not continue until the user approves.

## Step 8: Finish Work And Post-Merge Housekeeping Automatically

After the loop stops because local full-check passes and no requested remote
review produced new actionable comments, run the Trellis finish-work flow
automatically before the final report. Do not ask whether to run it; a clean
review loop is the trigger.

1. Read `.agents/skills/trellis-finish-work/SKILL.md`.
2. Follow that skill exactly to archive completed task state and record the
   session journal.
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
bash scripts/trellis-housekeeping.sh
```

Then include the housekeeping clean-state/anomaly report in the final response.
If housekeeping reports anomalies, do not mask them; they are the post-merge
handoff.

## Final Report

Report:

- PR number and URL.
- Whether local full-check passed and whether Prism/Gito ran or were skipped.
- Number of remote Copilot rounds completed.
- Comments fixed, rebutted, or left for user decision.
- Commits pushed during the loop.
- Finish-work actions and any archive/journal commits pushed.
- Housekeeping actions if the PR was already merged or became merged during the
  active session.
- CI status.
- Final working-tree state.
- Recommended internal documentation, spec, prompt, or pre-commit changes that
  would have caught avoidable mistakes before invoking remote review.
