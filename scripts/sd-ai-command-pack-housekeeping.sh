#!/usr/bin/env bash
set -euo pipefail

REMOTE="origin"
DRY_RUN=0
SELF_TEST=0
DELETE_REMOTE_BRANCH=1
AUTO_MERGE=1
MERGE_STRATEGY="${SD_AI_COMMAND_PACK_HOUSEKEEPING_MERGE_STRATEGY:-merge}"

ACTIONS=()
EXPECTED=()
INVENTORY=()
ANOMALIES=()
DEFAULT_BRANCH=""
START_BRANCH=""
GITHUB_REPO_SLUG=""
GH_REPO_ARGS=()

usage() {
  cat <<'EOF'
Usage: bash scripts/sd-ai-command-pack-housekeeping.sh [options]

End-of-stream housekeeping for a single active Trellis development stream.

Options:
  --dry-run              Preview cleanup without running mutating git commands.
  --no-auto-merge        Do not merge an already-green open PR.
  --merge-strategy <name> Merge strategy for ready open PRs: merge, squash, or rebase. Defaults to merge.
  --keep-remote-branch   Leave the merged remote branch on GitHub.
  --remote <name>        Remote to fetch, prune, pull, and clean. Defaults to origin.
  --self-test            Verify this installed script's merge-gate contract
                         against stubbed scenarios (hermetic: no git, gh, or
                         network access) and exit.
  -h, --help             Show this help.

Environment:
  SD_AI_COMMAND_PACK_HOUSEKEEPING_MERGE_STRATEGY
                          Default merge strategy when --merge-strategy is not set.
EOF
}

section() {
  printf '\n==> %s\n' "$*"
}

have() {
  command -v "$1" >/dev/null 2>&1
}

add_action() {
  ACTIONS+=("$*")
}

add_expected() {
  EXPECTED+=("$*")
}

add_inventory() {
  INVENTORY+=("$*")
}

add_anomaly() {
  ANOMALIES+=("$*")
}

print_list() {
  local item
  if [ "$#" -eq 0 ]; then
    printf 'none\n'
    return 0
  fi
  for item in "$@"; do
    printf -- '- %s\n' "$item"
  done
}

valid_merge_strategy() {
  case "$1" in
    merge|squash|rebase)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

valid_github_repo_slug() {
  local slug="$1"
  local owner
  local name

  case "$slug" in
    ""|/*|*/|*/*/*|*" "*|*$'\t'*|*$'\n'*)
      return 1
      ;;
  esac
  owner="${slug%%/*}"
  name="${slug#*/}"
  [ -n "$owner" ] && [ -n "$name" ] && [ "$owner" != "$slug" ]
}

parse_args() {
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --dry-run)
        DRY_RUN=1
        ;;
      --no-auto-merge)
        AUTO_MERGE=0
        ;;
      --merge-strategy)
        shift
        if [ "$#" -eq 0 ] || [ -z "${1:-}" ]; then
          printf 'error: --merge-strategy requires a value\n' >&2
          exit 2
        fi
        if valid_merge_strategy "$1"; then
          MERGE_STRATEGY="$1"
        else
          printf 'error: --merge-strategy must be merge, squash, or rebase\n' >&2
          exit 2
        fi
        ;;
      --keep-remote-branch)
        DELETE_REMOTE_BRANCH=0
        ;;
      --self-test)
        SELF_TEST=1
        ;;
      --remote)
        shift
        if [ "$#" -eq 0 ] || [ -z "${1:-}" ]; then
          printf 'error: --remote requires a value\n' >&2
          exit 2
        fi
        REMOTE="$1"
        ;;
      -h|--help)
        usage
        exit 0
        ;;
      *)
        printf 'error: unknown option: %s\n' "$1" >&2
        usage >&2
        exit 2
        ;;
    esac
    shift
  done
}

current_branch() {
  git symbolic-ref --quiet --short HEAD 2>/dev/null || true
}

working_tree_status() {
  git status --porcelain
}

working_tree_is_clean() {
  [ -z "$(working_tree_status)" ]
}

run_mutating_git() {
  if [ "$DRY_RUN" -eq 1 ]; then
    add_action "would run: git $*"
    return 0
  fi
  git "$@"
}

fetch_and_prune() {
  if ! git remote get-url "$REMOTE" >/dev/null 2>&1; then
    add_anomaly "remote $REMOTE is not configured; skipped fetch/prune and remote checks"
    return 0
  fi

  if run_mutating_git fetch --prune "$REMOTE"; then
    if [ "$DRY_RUN" -eq 1 ]; then
      return 0
    fi
    add_action "fetched and pruned $REMOTE"
  else
    add_anomaly "git fetch --prune $REMOTE failed"
  fi
}

configure_github_repo_scope() {
  local configured_slug
  local remote_url

  configured_slug="${SD_AI_COMMAND_PACK_HOUSEKEEPING_GITHUB_REPO:-}"
  GITHUB_REPO_SLUG=""
  GH_REPO_ARGS=()
  if [ -n "$configured_slug" ]; then
    if valid_github_repo_slug "$configured_slug"; then
      GITHUB_REPO_SLUG="$configured_slug"
    else
      add_anomaly "SD_AI_COMMAND_PACK_HOUSEKEEPING_GITHUB_REPO must be an owner/repo slug; ignored invalid override"
    fi
  fi
  if [ -z "$GITHUB_REPO_SLUG" ]; then
    remote_url="$(git remote get-url "$REMOTE" 2>/dev/null || true)"
    GITHUB_REPO_SLUG="$(github_repo_from_remote_url "$remote_url" || true)"
  fi
  if [ -n "$GITHUB_REPO_SLUG" ]; then
    GH_REPO_ARGS=(--repo "$GITHUB_REPO_SLUG")
  fi
}

gh_pr_view() {
  if [ -n "$GITHUB_REPO_SLUG" ]; then
    gh pr view "${GH_REPO_ARGS[@]}" "$@"
  else
    gh pr view "$@"
  fi
}

gh_pr_list() {
  if [ -n "$GITHUB_REPO_SLUG" ]; then
    gh pr list "${GH_REPO_ARGS[@]}" "$@"
  else
    gh pr list "$@"
  fi
}

gh_issue_list() {
  if [ -n "$GITHUB_REPO_SLUG" ]; then
    gh issue list "${GH_REPO_ARGS[@]}" "$@"
  else
    gh issue list "$@"
  fi
}

gh_pr_merge() {
  if [ -n "$GITHUB_REPO_SLUG" ]; then
    gh pr merge "${GH_REPO_ARGS[@]}" "$@"
  else
    gh pr merge "$@"
  fi
}

detect_default_branch() {
  local symbolic
  symbolic="$(git symbolic-ref --quiet --short "refs/remotes/$REMOTE/HEAD" 2>/dev/null || true)"
  if [ -n "$symbolic" ]; then
    DEFAULT_BRANCH="${symbolic#${REMOTE}/}"
    return 0
  fi

  if have gh; then
    if [ -n "$GITHUB_REPO_SLUG" ]; then
      DEFAULT_BRANCH="$(gh repo view "$GITHUB_REPO_SLUG" --json defaultBranchRef --jq '.defaultBranchRef.name' 2>/dev/null || true)"
    else
      DEFAULT_BRANCH="$(gh repo view --json defaultBranchRef --jq '.defaultBranchRef.name' 2>/dev/null || true)"
    fi
    if [ -n "$DEFAULT_BRANCH" ]; then
      return 0
    fi
  fi

  if git show-ref --verify --quiet "refs/remotes/$REMOTE/main"; then
    DEFAULT_BRANCH="main"
  elif git show-ref --verify --quiet "refs/remotes/$REMOTE/master"; then
    DEFAULT_BRANCH="master"
  else
    add_anomaly "could not detect the default branch for $REMOTE"
  fi
}

github_repo_from_remote_url() {
  local url="$1"
  local slug=""

  case "$url" in
    git@github.com:*)
      slug="${url#git@github.com:}"
      ;;
    ssh://git@github.com/*)
      slug="${url#ssh://git@github.com/}"
      ;;
    https://github.com/*)
      slug="${url#https://github.com/}"
      ;;
    http://github.com/*)
      slug="${url#http://github.com/}"
      ;;
    *)
      return 1
      ;;
  esac

  slug="${slug%.git}"
  slug="${slug%/}"
  if ! valid_github_repo_slug "$slug"; then
    return 1
  fi

  printf '%s\n' "$slug"
}

switch_to_default_branch() {
  if [ -z "$DEFAULT_BRANCH" ]; then
    return 0
  fi

  if [ "$(current_branch)" = "$DEFAULT_BRANCH" ]; then
    add_action "already on $DEFAULT_BRANCH"
    return 0
  fi

  if [ "$DRY_RUN" -eq 1 ]; then
    add_action "would switch to $DEFAULT_BRANCH"
    return 0
  fi

  if git show-ref --verify --quiet "refs/heads/$DEFAULT_BRANCH"; then
    if git switch "$DEFAULT_BRANCH"; then
      add_action "switched to $DEFAULT_BRANCH"
    else
      add_anomaly "failed to switch to $DEFAULT_BRANCH"
    fi
  elif git show-ref --verify --quiet "refs/remotes/$REMOTE/$DEFAULT_BRANCH"; then
    if git switch -c "$DEFAULT_BRANCH" "$REMOTE/$DEFAULT_BRANCH"; then
      add_action "created and switched to $DEFAULT_BRANCH from $REMOTE/$DEFAULT_BRANCH"
    else
      add_anomaly "failed to create $DEFAULT_BRANCH from $REMOTE/$DEFAULT_BRANCH"
    fi
  else
    add_anomaly "remote default ref $REMOTE/$DEFAULT_BRANCH does not exist"
  fi
}

fast_forward_default_branch() {
  if [ -z "$DEFAULT_BRANCH" ]; then
    return 0
  fi

  if [ "$(current_branch)" != "$DEFAULT_BRANCH" ]; then
    if [ "$DRY_RUN" -eq 1 ]; then
      if git show-ref --verify --quiet "refs/remotes/$REMOTE/$DEFAULT_BRANCH"; then
        add_action "would fast-forward $DEFAULT_BRANCH from $REMOTE/$DEFAULT_BRANCH"
      else
        add_anomaly "remote default ref $REMOTE/$DEFAULT_BRANCH does not exist"
      fi
    fi
    return 0
  fi

  if ! git show-ref --verify --quiet "refs/remotes/$REMOTE/$DEFAULT_BRANCH"; then
    add_anomaly "remote default ref $REMOTE/$DEFAULT_BRANCH does not exist"
    return 0
  fi

  if run_mutating_git pull --ff-only "$REMOTE" "$DEFAULT_BRANCH"; then
    if [ "$DRY_RUN" -eq 1 ]; then
      return 0
    fi
    add_action "fast-forwarded $DEFAULT_BRANCH from $REMOTE/$DEFAULT_BRANCH"
  else
    add_anomaly "git pull --ff-only $REMOTE $DEFAULT_BRANCH failed"
  fi
}

view_pr_for_branch() {
  local branch="$1"
  local pr_data
  pr_data="$(
    gh_pr_view \
      --json number,state,mergedAt,url,headRefName,headRefOid \
      --jq '[.number, .state, .mergedAt, .url, .headRefName, .headRefOid] | @tsv' \
      -- "$branch" \
      2>/dev/null ||
      true
  )"
  if [ -n "$pr_data" ]; then
    printf '%s\n' "$pr_data"
    return 0
  fi

  gh_pr_list --state merged --head="$branch" --limit 1 \
    --json number,state,mergedAt,url,headRefName,headRefOid \
    --jq '.[0] | select(. != null) | [.number, .state, .mergedAt, .url, .headRefName, .headRefOid] | @tsv' \
    2>/dev/null ||
    true
}

view_open_pr_readiness_for_branch() {
  local branch="$1"
  gh_pr_view \
    --json number,state,isDraft,url,headRefName,headRefOid,baseRefName,mergeStateStatus,statusCheckRollup \
    --jq '[.number, .state, .isDraft, .url, .headRefName, .headRefOid, .baseRefName, .mergeStateStatus, ([.statusCheckRollup[]? | select((.__typename == "CheckRun" and (.status != "COMPLETED" or (.conclusion != "SUCCESS" and .conclusion != "SKIPPED" and .conclusion != "NEUTRAL"))) or (.__typename == "StatusContext" and .state != "SUCCESS"))] | length), ([.statusCheckRollup[]? | select((.__typename == "CheckRun" and .status == "COMPLETED" and .conclusion == "SUCCESS") or (.__typename == "StatusContext" and .state == "SUCCESS"))] | length)] | @tsv' \
    -- "$branch" \
    2>/dev/null ||
    true
}

unresolved_review_thread_count() {
  local pr_number="$1"
  local owner
  local name
  local cursor=""
  local has_next_page="true"
  local unresolved_count=0
  local page_data
  local page_unresolved
  local gh_status
  local -a graphql_args
  if ! valid_github_repo_slug "$GITHUB_REPO_SLUG"; then
    return 1
  fi
  owner="${GITHUB_REPO_SLUG%%/*}"
  name="${GITHUB_REPO_SLUG#*/}"
  if [ -z "$owner" ] || [ -z "$name" ]; then
    return 1
  fi

  while [ "$has_next_page" = "true" ]; do
    graphql_args=(-F owner="$owner" -F name="$name" -F number="$pr_number")
    if [ -n "$cursor" ]; then
      graphql_args+=(-F cursor="$cursor")
    fi

    set +e
    page_data="$(
      gh api graphql \
        "${graphql_args[@]}" \
        -f query='query($owner:String!, $name:String!, $number:Int!, $cursor:String) { repository(owner:$owner, name:$name) { pullRequest(number:$number) { reviewThreads(first: 100, after: $cursor) { nodes { isResolved } pageInfo { hasNextPage endCursor } } } } }' \
        --jq '[([.data.repository.pullRequest.reviewThreads.nodes[]? | select(.isResolved == false)] | length), (.data.repository.pullRequest.reviewThreads.pageInfo.hasNextPage // false), (.data.repository.pullRequest.reviewThreads.pageInfo.endCursor // "")] | @tsv' \
        2>/dev/null
    )"
    gh_status=$?
    set -e
    if [ "$gh_status" -ne 0 ] || [ -z "$page_data" ]; then
      return 1
    fi

    IFS=$'\t' read -r page_unresolved has_next_page cursor <<<"$page_data"
    if ! [[ "$page_unresolved" =~ ^[0-9]+$ ]]; then
      return 1
    fi
    unresolved_count=$((unresolved_count + page_unresolved))
    if [ "$has_next_page" = "true" ] && [ -z "$cursor" ]; then
      return 1
    fi
  done

  printf '%s\n' "$unresolved_count"
}

remote_branch_head_oid() {
  local branch="$1"
  local output
  if ! output="$(git ls-remote --exit-code "$REMOTE" "refs/heads/$branch" 2>/dev/null)"; then
    return 1
  fi
  output="${output%%$'\n'*}"
  output="${output%%$'\t'*}"
  if [ -z "$output" ]; then
    return 1
  fi
  printf '%s\n' "$output"
}

merge_ready_open_pr() {
  local pr_number="$1"
  local merge_head
  local strategy_flag

  merge_head="$(git rev-parse --verify HEAD)"
  case "$MERGE_STRATEGY" in
    merge)
      strategy_flag="--merge"
      ;;
    squash)
      strategy_flag="--squash"
      ;;
    rebase)
      strategy_flag="--rebase"
      ;;
    *)
      add_anomaly "unknown merge strategy $MERGE_STRATEGY; skipped auto-merge"
      return 1
      ;;
  esac

  if [ "$DRY_RUN" -eq 1 ]; then
    add_action "would merge PR #$pr_number with $MERGE_STRATEGY strategy"
    return 0
  fi

  if gh_pr_merge "$pr_number" "$strategy_flag" --match-head-commit "$merge_head"; then
    add_action "merged PR #$pr_number with $MERGE_STRATEGY strategy"
  else
    add_anomaly "failed to merge PR #$pr_number; resolve branch protection or check failures, then rerun housekeeping"
    return 1
  fi
}

maybe_merge_ready_open_pr() {
  local branch="$1"
  local pr_data
  local pr_number
  local pr_state
  local pr_is_draft
  local pr_url
  local pr_head
  local pr_head_oid
  local pr_base
  local pr_merge_state
  local blocking_check_count
  local successful_check_count
  local local_head_oid
  local remote_head_oid
  local unresolved_count

  if [ "$AUTO_MERGE" -eq 0 ] || [ -z "$branch" ] || [ "$branch" = "$DEFAULT_BRANCH" ]; then
    return 0
  fi
  if ! working_tree_is_clean; then
    add_anomaly "working tree has uncommitted changes; skipped auto-merge"
    return 0
  fi
  if ! have gh; then
    add_anomaly "gh not found; skipped auto-merge"
    return 0
  fi
  if ! valid_merge_strategy "$MERGE_STRATEGY"; then
    add_anomaly "merge strategy is invalid; expected merge, squash, or rebase; skipped auto-merge"
    return 0
  fi

  pr_data="$(view_open_pr_readiness_for_branch "$branch")"
  if [ -z "$pr_data" ]; then
    return 0
  fi

  IFS=$'\t' read -r pr_number pr_state pr_is_draft pr_url pr_head pr_head_oid pr_base pr_merge_state blocking_check_count successful_check_count <<<"$pr_data"
  if [ "$pr_state" != "OPEN" ]; then
    return 0
  fi
  if [ "$pr_is_draft" = "true" ]; then
    add_anomaly "PR #$pr_number for $branch is a draft; skipped auto-merge"
    return 0
  fi
  if [ "$pr_head" != "$branch" ]; then
    add_anomaly "PR #$pr_number head is $pr_head, not $branch; skipped auto-merge"
    return 0
  fi
  if [ -n "$DEFAULT_BRANCH" ] && [ "$pr_base" != "$DEFAULT_BRANCH" ]; then
    add_anomaly "PR #$pr_number base is $pr_base, expected $DEFAULT_BRANCH; skipped auto-merge"
    return 0
  fi

  local_head_oid="$(git rev-parse --verify "refs/heads/$branch^{commit}")"
  if [ "$local_head_oid" != "$pr_head_oid" ]; then
    add_anomaly "local $branch is at $local_head_oid, but PR #$pr_number is at $pr_head_oid; skipped auto-merge"
    return 0
  fi
  if ! remote_head_oid="$(remote_branch_head_oid "$branch")"; then
    add_anomaly "failed to read remote branch head for $REMOTE/$branch; skipped auto-merge"
    return 0
  fi
  if [ "$remote_head_oid" != "$local_head_oid" ]; then
    add_anomaly "remote branch $REMOTE/$branch is at $remote_head_oid, but local $branch is at $local_head_oid; skipped auto-merge"
    return 0
  fi

  if [ "$pr_merge_state" != "CLEAN" ]; then
    add_anomaly "PR #$pr_number merge state is $pr_merge_state, not CLEAN; skipped auto-merge"
    return 0
  fi
  # SKIPPED and NEUTRAL check conclusions are intentional lane skips (change
  # classifiers), so they neither block the merge nor count as green. Blocking
  # means a run that has not completed or any conclusion other than SUCCESS,
  # SKIPPED, or NEUTRAL (for example FAILURE, CANCELLED, TIMED_OUT, or
  # ACTION_REQUIRED). Require at least one executed successful check and zero
  # blocking checks.
  if ! [[ "$successful_check_count" =~ ^[0-9]+$ ]] || ! [[ "$blocking_check_count" =~ ^[0-9]+$ ]]; then
    add_anomaly "PR #$pr_number has undeterminable check counts; skipped auto-merge"
    return 0
  fi
  if [ "$successful_check_count" -eq 0 ]; then
    add_anomaly "PR #$pr_number has no successful executed checks; skipped auto-merge"
    return 0
  fi
  if [ "$blocking_check_count" -ne 0 ]; then
    add_anomaly "PR #$pr_number has non-green checks; skipped auto-merge"
    return 0
  fi

  if [ -z "$GITHUB_REPO_SLUG" ]; then
    add_anomaly "could not derive GitHub repo from $REMOTE; skipped auto-merge"
    return 0
  fi
  if ! unresolved_count="$(unresolved_review_thread_count "$pr_number")"; then
    add_anomaly "failed to inspect review threads for PR #$pr_number; skipped auto-merge"
    return 0
  fi
  if [ "$unresolved_count" -ne 0 ]; then
    add_anomaly "PR #$pr_number has $unresolved_count unresolved review thread(s); skipped auto-merge"
    return 0
  fi

  add_action "PR #$pr_number is open, green, comment-clean, and matches local $branch ($pr_url)"
  merge_ready_open_pr "$pr_number" || return 0
}

cleanup_current_branch_if_merged() {
  local branch="$1"
  local pr_data
  local pr_number
  local pr_state
  local pr_merged_at
  local pr_url
  local pr_head
  local pr_head_oid
  local local_head_oid
  local ls_remote_output
  local remote_head_oid

  if [ -z "$branch" ]; then
    add_anomaly "detached HEAD; skipped branch cleanup"
    return 0
  fi
  if [ -z "$DEFAULT_BRANCH" ] || [ "$branch" = "$DEFAULT_BRANCH" ]; then
    return 0
  fi
  if ! working_tree_is_clean; then
    add_anomaly "working tree has uncommitted changes; skipped switching and branch deletion"
    return 0
  fi
  if ! have gh; then
    add_anomaly "gh not found; cannot confirm whether $branch has a merged PR"
    return 0
  fi

  pr_data="$(view_pr_for_branch "$branch")"
  if [ -z "$pr_data" ]; then
    add_anomaly "unable to resolve GitHub PR metadata for $branch; no PR was found or gh failed, so the branch was left untouched"
    return 0
  fi

  IFS=$'\t' read -r pr_number pr_state pr_merged_at pr_url pr_head pr_head_oid <<<"$pr_data"
  if [ "$pr_state" != "MERGED" ]; then
    add_anomaly "PR #$pr_number for $branch is $pr_state, not MERGED; left the branch untouched"
    return 0
  fi
  if [ "$pr_head" != "$branch" ]; then
    add_anomaly "PR #$pr_number head is $pr_head, not $branch; left the branch untouched"
    return 0
  fi
  local_head_oid="$(git rev-parse --verify "refs/heads/$branch^{commit}")"
  if [ -n "$pr_head_oid" ] && [ "$local_head_oid" != "$pr_head_oid" ]; then
    add_anomaly "local $branch is at $local_head_oid, but merged PR #$pr_number ended at $pr_head_oid; left the branch untouched"
    return 0
  fi

  add_action "confirmed PR #$pr_number merged at $pr_merged_at ($pr_url)"
  switch_to_default_branch
  fast_forward_default_branch

  if [ "$DRY_RUN" -eq 0 ] && [ "$(current_branch)" != "$DEFAULT_BRANCH" ]; then
    add_anomaly "still on $branch; skipped branch deletion"
    return 0
  fi

  if [ "$DRY_RUN" -eq 1 ]; then
    add_action "would delete local branch $branch"
  elif git show-ref --verify --quiet "refs/heads/$branch"; then
    if git branch -D -- "$branch"; then
      add_action "deleted local branch $branch"
    else
      add_anomaly "failed to delete local branch $branch"
    fi
  fi

  if [ "$DELETE_REMOTE_BRANCH" -eq 0 ]; then
    add_action "left remote branch $REMOTE/$branch because --keep-remote-branch was set"
    return 0
  fi

  set +e
  ls_remote_output="$(git ls-remote --exit-code "$REMOTE" "refs/heads/$branch" 2>/dev/null)"
  local ls_remote_status=$?
  set -e

  if [ "$ls_remote_status" -eq 0 ]; then
    remote_head_oid="${ls_remote_output%%$'\n'*}"
    remote_head_oid="${remote_head_oid%%$'\t'*}"
    if [ -z "$remote_head_oid" ]; then
      add_anomaly "failed to read remote branch head for $REMOTE/$branch"
      return 0
    fi
    if [ -n "$pr_head_oid" ] && [ "$remote_head_oid" != "$pr_head_oid" ]; then
      add_anomaly "remote branch $REMOTE/$branch is at $remote_head_oid, but merged PR #$pr_number ended at $pr_head_oid; left the remote branch untouched"
      return 0
    fi
    if [ "$DRY_RUN" -eq 1 ]; then
      add_action "would delete remote branch $REMOTE/$branch"
    elif git push "$REMOTE" ":refs/heads/$branch"; then
      add_action "deleted remote branch $REMOTE/$branch"
      if git fetch --prune "$REMOTE"; then
        add_action "pruned $REMOTE after remote branch deletion"
      else
        add_anomaly "deleted remote branch $REMOTE/$branch, but git fetch --prune $REMOTE failed"
      fi
    else
      add_anomaly "failed to delete remote branch $REMOTE/$branch"
    fi
  elif [ "$ls_remote_status" -eq 2 ]; then
    add_action "remote branch $REMOTE/$branch is already absent"
  else
    add_anomaly "failed to check whether remote branch $REMOTE/$branch exists"
  fi
}

check_open_prs() {
  local open_prs
  local count
  if ! have gh; then
    add_inventory "open PRs: skipped because gh was not found"
    return 0
  fi

  if ! open_prs="$(gh_pr_list --state open --limit 100 --json number,title,headRefName --jq '.[] | "#\(.number) \(.headRefName): \(.title)"' 2>/dev/null)"; then
    add_inventory "open PRs: unavailable because gh failed to list open PRs"
    return 0
  fi

  if [ -z "$open_prs" ]; then
    add_inventory "open PRs: none"
  else
    count="$(printf '%s\n' "$open_prs" | sed '/^$/d' | wc -l | tr -d ' ')"
    add_inventory "open PRs outside this cleanup scope ($count): $(printf '%s' "$open_prs" | paste -sd ';' -)"
  fi
}

check_open_issues() {
  local open_issues
  local count
  if ! have gh; then
    add_inventory "open issues: skipped because gh was not found"
    return 0
  fi

  if ! open_issues="$(gh_issue_list --state open --limit 100 --json number,title --jq '.[] | "#\(.number): \(.title)"' 2>/dev/null)"; then
    add_inventory "open issues: unavailable because gh failed to list open issues"
    return 0
  fi

  if [ -z "$open_issues" ]; then
    add_inventory "open issues: none"
  else
    count="$(printf '%s\n' "$open_issues" | sed '/^$/d' | wc -l | tr -d ' ')"
    add_inventory "open issues outside this cleanup scope ($count): $(printf '%s' "$open_issues" | paste -sd ';' -)"
  fi
}

check_trellis_tasks() {
  local context
  if [ ! -f ".trellis/scripts/get_context.py" ]; then
    add_inventory "Trellis active tasks: skipped because .trellis/scripts/get_context.py was not found"
    return 0
  fi
  if ! have python3; then
    add_inventory "Trellis active tasks: skipped because python3 was not found"
    return 0
  fi

  if ! context="$(python3 ./.trellis/scripts/get_context.py --mode record 2>&1)"; then
    add_inventory "Trellis active tasks: unavailable; run python3 ./.trellis/scripts/get_context.py --mode record"
    return 0
  fi

  if printf '%s\n' "$context" | grep -q "(no active tasks assigned to you)"; then
    add_inventory "Trellis active tasks: none assigned to current developer"
  else
    add_inventory "Trellis active tasks: active tasks may remain outside this cleanup scope"
  fi
}

check_final_git_state() {
  local final_branch
  local local_head
  local remote_head
  local extra_local

  final_branch="$(current_branch)"
  if [ -n "$DEFAULT_BRANCH" ] && [ "$final_branch" = "$DEFAULT_BRANCH" ]; then
    add_expected "branch: $DEFAULT_BRANCH"
  else
    add_anomaly "current branch is ${final_branch:-detached HEAD}, expected ${DEFAULT_BRANCH:-default branch}"
  fi

  if working_tree_is_clean; then
    add_expected "working tree: clean"
  else
    add_anomaly "working tree is dirty after housekeeping"
  fi

  if [ -z "$DEFAULT_BRANCH" ]; then
    add_anomaly "default branch is unknown; skipped branch inventory checks"
    return 0
  fi

  if git show-ref --verify --quiet "refs/heads/$DEFAULT_BRANCH" && git show-ref --verify --quiet "refs/remotes/$REMOTE/$DEFAULT_BRANCH"; then
    local_head="$(git rev-parse --verify "refs/heads/$DEFAULT_BRANCH^{commit}")"
    remote_head="$(git rev-parse --verify "refs/remotes/$REMOTE/$DEFAULT_BRANCH^{commit}")"
    if [ "$local_head" = "$remote_head" ]; then
      add_expected "$DEFAULT_BRANCH matches $REMOTE/$DEFAULT_BRANCH"
    else
      add_anomaly "$DEFAULT_BRANCH does not match $REMOTE/$DEFAULT_BRANCH"
    fi
  elif ! git show-ref --verify --quiet "refs/heads/$DEFAULT_BRANCH"; then
    add_anomaly "local default branch $DEFAULT_BRANCH does not exist"
  else
    add_anomaly "remote default branch $REMOTE/$DEFAULT_BRANCH does not exist"
  fi

  extra_local="$(git for-each-ref --format='%(refname:short)' refs/heads | grep -F -x -v "$DEFAULT_BRANCH" || true)"
  if [ -z "$extra_local" ]; then
    add_expected "local branches: only $DEFAULT_BRANCH"
  else
    add_anomaly "extra local branches remain: $(printf '%s' "$extra_local" | paste -sd ',' -)"
  fi

  if [ -n "$START_BRANCH" ] && [ "$START_BRANCH" != "$DEFAULT_BRANCH" ]; then
    if [ "$DELETE_REMOTE_BRANCH" -eq 0 ]; then
      if git show-ref --verify --quiet "refs/remotes/$REMOTE/$START_BRANCH"; then
        add_expected "remote source branch kept: $REMOTE/$START_BRANCH"
      else
        add_anomaly "remote source branch $REMOTE/$START_BRANCH is absent despite --keep-remote-branch"
      fi
    elif git show-ref --verify --quiet "refs/remotes/$REMOTE/$START_BRANCH"; then
      add_anomaly "remote source branch still tracked: $REMOTE/$START_BRANCH"
    else
      add_expected "remote source branch absent: $REMOTE/$START_BRANCH"
    fi
  fi
}

record_dry_run_final_state_note() {
  add_expected "dry-run preview: skipped final git-state verification because no fetch, pull, switch, or branch deletion was performed"
  if working_tree_is_clean; then
    add_expected "working tree: clean"
  else
    add_anomaly "working tree is dirty; dry-run did not change it"
  fi
}

print_report() {
  section "Tasks performed"
  print_list "${ACTIONS[@]}"

  section "Expected clean state"
  print_list "${EXPECTED[@]}"

  section "Inventory"
  print_list "${INVENTORY[@]}"

  section "Anomalies"
  if [ "${#ANOMALIES[@]}" -eq 0 ]; then
    printf 'none\n'
    return 0
  fi
  print_list "${ANOMALIES[@]}"
  return 1
}

# Hermetic self-test of the auto-merge gate contract. Every collaborator that
# would touch git, gh, or the network is overridden inside a subshell, so the
# scenarios exercise exactly the vendored gate logic in this file. Consumers
# run `bash scripts/sd-ai-command-pack-housekeeping.sh --self-test` from CI to
# verify their installed copy instead of maintaining bespoke contract tests.
self_test_scenario() {
  local name="$1" expectation="$2" is_draft="$3" merge_state="$4"
  local blocking="$5" successful="$6" unresolved="$7"
  local output merged=0 subshell_status=0

  # Capture the subshell status explicitly so a scenario that dies (for
  # example on an unexpected external call) reports a named failure instead
  # of relying on the caller's AND-OR context to suppress errexit.
  output="$(
    # Guarantee hermeticity rather than assume it: with an empty PATH every
    # unstubbed external command fails loudly, and gh is stubbed to fail so
    # future gate logic cannot silently reach GitHub even if PATH leaks.
    PATH=''
    DEFAULT_BRANCH=main
    AUTO_MERGE=1
    MERGE_STRATEGY=merge
    GITHUB_REPO_SLUG=owner/repo
    ANOMALIES=()
    working_tree_is_clean() { return 0; }
    have() { return 0; }
    gh() {
      printf 'self-test: unexpected gh call: %s\n' "$*" >&2
      return 1
    }
    view_open_pr_readiness_for_branch() {
      printf '153\tOPEN\t%s\thttps://example.test/pr/153\tfeature\theadoid\tmain\t%s\t%s\t%s\n' \
        "$is_draft" "$merge_state" "$blocking" "$successful"
    }
    remote_branch_head_oid() { printf 'headoid\n'; }
    unresolved_review_thread_count() { printf '%s\n' "$unresolved"; }
    git() {
      # Leading-paren case patterns keep bash 3.2 (macOS /bin/bash) from
      # misparsing the unbalanced ')' inside this command substitution.
      case "$*" in
        ("rev-parse --verify refs/heads/feature^{commit}")
          printf 'headoid\n'
          ;;
        (*)
          printf 'self-test: unexpected git call: %s\n' "$*" >&2
          return 1
          ;;
      esac
    }
    merge_ready_open_pr() {
      printf 'SELF_TEST_MERGE_EVENT\n'
      return 0
    }
    maybe_merge_ready_open_pr feature
    printf 'SELF_TEST_ANOMALIES=%s\n' "${ANOMALIES[*]-}"
  )" || subshell_status=$?

  if [ "$subshell_status" -ne 0 ]; then
    printf 'self-test: %s: FAIL (scenario subshell exited %s)\n' \
      "$name" "$subshell_status" >&2
    return 1
  fi

  case "$output" in
    *SELF_TEST_MERGE_EVENT*) merged=1 ;;
  esac
  local anomalies="${output#*SELF_TEST_ANOMALIES=}"

  if [ "$expectation" = merge ]; then
    if [ "$merged" -eq 1 ] && [ -z "$anomalies" ]; then
      printf 'self-test: %s: ok\n' "$name"
      return 0
    fi
  else
    case "$anomalies" in
      *"skipped auto-merge"*)
        if [ "$merged" -eq 0 ]; then
          printf 'self-test: %s: ok\n' "$name"
          return 0
        fi
        ;;
    esac
  fi
  printf 'self-test: %s: FAIL (expected %s; merged=%s anomalies=%s)\n' \
    "$name" "$expectation" "$merged" "$anomalies" >&2
  return 1
}

run_self_test() {
  local failures=0

  self_test_scenario "green executed checks merge" merge false CLEAN 0 2 0 || failures=$((failures + 1))
  # A single executed success is enough: SKIPPED/NEUTRAL conclusions are
  # pre-aggregated out of the counts by the readiness query, whose
  # classification is covered by the pack's upstream jq fixture tests.
  self_test_scenario "single executed success suffices" merge false CLEAN 0 1 0 || failures=$((failures + 1))
  self_test_scenario "blocking checks refuse" refuse false CLEAN 1 3 0 || failures=$((failures + 1))
  self_test_scenario "zero successful checks refuse" refuse false CLEAN 0 0 0 || failures=$((failures + 1))
  self_test_scenario "undeterminable counts refuse" refuse false CLEAN unknown unknown 0 || failures=$((failures + 1))
  self_test_scenario "non-clean merge state refuses" refuse false BLOCKED 0 2 0 || failures=$((failures + 1))
  self_test_scenario "draft PR refuses" refuse true CLEAN 0 2 0 || failures=$((failures + 1))
  self_test_scenario "unresolved review threads refuse" refuse false CLEAN 0 2 1 || failures=$((failures + 1))

  if [ "$failures" -ne 0 ]; then
    printf 'self-test: %s scenario(s) FAILED\n' "$failures" >&2
    return 1
  fi
  printf 'self-test: all scenarios passed\n'
  return 0
}

main() {
  local repo_root

  parse_args "$@"
  if [ "$SELF_TEST" -eq 1 ]; then
    run_self_test
    exit $?
  fi
  if ! have git; then
    printf 'error: git not found on PATH\n' >&2
    exit 2
  fi
  if ! repo_root="$(git rev-parse --show-toplevel 2>/dev/null)"; then
    printf 'error: not inside a git repository\n' >&2
    exit 2
  fi
  cd "$repo_root"

  section "SD AI command pack housekeeping"
  printf 'repo: %s\n' "$repo_root"
  if [ "$DRY_RUN" -eq 1 ]; then
    printf 'mode: dry-run\n'
  fi

  START_BRANCH="$(current_branch)"
  printf 'start branch: %s\n' "${START_BRANCH:-detached HEAD}"

  configure_github_repo_scope
  fetch_and_prune
  detect_default_branch
  if [ -n "$DEFAULT_BRANCH" ]; then
    printf 'default branch: %s\n' "$DEFAULT_BRANCH"
  fi

  if [ "$START_BRANCH" = "$DEFAULT_BRANCH" ]; then
    fast_forward_default_branch
  else
    maybe_merge_ready_open_pr "$START_BRANCH"
    cleanup_current_branch_if_merged "$START_BRANCH"
  fi

  if [ "$DRY_RUN" -eq 1 ]; then
    record_dry_run_final_state_note
  else
    check_final_git_state
  fi
  check_open_prs
  check_open_issues
  check_trellis_tasks
  print_report
}

main "$@"
