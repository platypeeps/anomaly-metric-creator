#!/usr/bin/env bash
set -euo pipefail

REMOTE="origin"
DRY_RUN=0
DELETE_REMOTE_BRANCH=1

ACTIONS=()
EXPECTED=()
ANOMALIES=()
DEFAULT_BRANCH=""
START_BRANCH=""
GITHUB_REPO_SLUG=""
GH_REPO_ARGS=()

usage() {
  cat <<'EOF'
Usage: bash scripts/trellis-housekeeping.sh [options]

Post-merge housekeeping for a single active Trellis development stream.

Options:
  --dry-run              Preview cleanup without running mutating git commands.
  --keep-remote-branch   Leave the merged remote branch on GitHub.
  --remote <name>        Remote to fetch, prune, pull, and clean. Defaults to origin.
  -h, --help             Show this help.
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

parse_args() {
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --dry-run)
        DRY_RUN=1
        ;;
      --keep-remote-branch)
        DELETE_REMOTE_BRANCH=0
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
  local remote_url

  GITHUB_REPO_SLUG=""
  GH_REPO_ARGS=()
  remote_url="$(git remote get-url "$REMOTE" 2>/dev/null || true)"
  GITHUB_REPO_SLUG="$(github_repo_from_remote_url "$remote_url" || true)"
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
  if [ -z "$slug" ] || [ "$slug" = "${slug#*/}" ]; then
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
        add_action "would run: git pull --ff-only $REMOTE $DEFAULT_BRANCH"
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
    add_anomaly "gh not found; skipped open PR check"
    return 0
  fi

  if ! open_prs="$(gh_pr_list --state open --limit 100 --json number,title,headRefName --jq '.[] | "#\(.number) \(.headRefName): \(.title)"' 2>/dev/null)"; then
    add_anomaly "failed to list open PRs"
    return 0
  fi

  if [ -z "$open_prs" ]; then
    add_expected "open PRs: none"
  else
    count="$(printf '%s\n' "$open_prs" | sed '/^$/d' | wc -l | tr -d ' ')"
    add_anomaly "open PRs remain ($count): $(printf '%s' "$open_prs" | paste -sd ';' -)"
  fi
}

check_open_issues() {
  local open_issues
  local count
  if ! have gh; then
    add_anomaly "gh not found; skipped open issue check"
    return 0
  fi

  if ! open_issues="$(gh_issue_list --state open --limit 100 --json number,title --jq '.[] | "#\(.number): \(.title)"' 2>/dev/null)"; then
    add_anomaly "failed to list open issues"
    return 0
  fi

  if [ -z "$open_issues" ]; then
    add_expected "open issues: none"
  else
    count="$(printf '%s\n' "$open_issues" | sed '/^$/d' | wc -l | tr -d ' ')"
    add_anomaly "open issues remain ($count): $(printf '%s' "$open_issues" | paste -sd ';' -)"
  fi
}

check_trellis_tasks() {
  local context
  if [ ! -f ".trellis/scripts/get_context.py" ]; then
    add_anomaly ".trellis/scripts/get_context.py not found; skipped Trellis active-task check"
    return 0
  fi
  if ! have python3; then
    add_anomaly "python3 not found; skipped Trellis active-task check"
    return 0
  fi

  if ! context="$(python3 ./.trellis/scripts/get_context.py --mode record 2>&1)"; then
    add_anomaly "Trellis record mode failed; run python3 ./.trellis/scripts/get_context.py --mode record"
    return 0
  fi

  if printf '%s\n' "$context" | grep -q "(no active tasks assigned to you)"; then
    add_expected "Trellis active tasks: none"
  else
    add_anomaly "Trellis active tasks may remain; run python3 ./.trellis/scripts/get_context.py --mode record"
  fi
}

check_final_git_state() {
  local final_branch
  local local_head
  local remote_head
  local extra_local
  local extra_remote
  local kept_remote_branch

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

  kept_remote_branch=""
  if [ "$DELETE_REMOTE_BRANCH" -eq 0 ] && [ -n "$START_BRANCH" ] && [ "$START_BRANCH" != "$DEFAULT_BRANCH" ]; then
    kept_remote_branch="$REMOTE/$START_BRANCH"
  fi

  extra_remote="$(
    git for-each-ref --format='%(refname:short)' "refs/remotes/$REMOTE" |
      grep -F -x -v "$REMOTE" |
      grep -F -x -v "$REMOTE/HEAD" |
      grep -F -x -v "$REMOTE/$DEFAULT_BRANCH" ||
      true
  )"
  if [ -n "$kept_remote_branch" ]; then
    extra_remote="$(printf '%s\n' "$extra_remote" | grep -F -x -v "$kept_remote_branch" || true)"
  fi

  if [ -z "$extra_remote" ]; then
    if [ -n "$kept_remote_branch" ] && git show-ref --verify --quiet "refs/remotes/$REMOTE/$START_BRANCH"; then
      add_expected "remote branches: only $REMOTE/HEAD, $REMOTE/$DEFAULT_BRANCH, and kept $kept_remote_branch"
    else
      add_expected "remote branches: only $REMOTE/HEAD and $REMOTE/$DEFAULT_BRANCH"
    fi
  else
    add_anomaly "extra remote-tracking branches remain: $(printf '%s' "$extra_remote" | paste -sd ',' -)"
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

  section "Anomalies"
  if [ "${#ANOMALIES[@]}" -eq 0 ]; then
    printf 'none\n'
    return 0
  fi
  print_list "${ANOMALIES[@]}"
  return 1
}

main() {
  local repo_root

  parse_args "$@"
  if ! have git; then
    printf 'error: git not found on PATH\n' >&2
    exit 2
  fi
  if ! repo_root="$(git rev-parse --show-toplevel 2>/dev/null)"; then
    printf 'error: not inside a git repository\n' >&2
    exit 2
  fi
  cd "$repo_root"

  section "Trellis housekeeping"
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
