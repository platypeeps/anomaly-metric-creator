#!/bin/sh
# tools/pr_comment.sh — canonical PR-comment poster.
#
# Pre-flights a comment body through the repo's two comment-body gates, then
# posts it with `gh pr comment`. The gates are the enforcement path for two
# conventions that previously lived only in prose (CLAUDE.md documented a manual
# `&&` chain that nothing invoked):
#
#   1. role-name leaks    — tools/check_role_name_leaks.py   (stdin `-` mode)
#   2. duplicate / self-correction APPROVED comments
#                         — tools/check_approval_duplicate.py (`--pr N` mode)
#
# Each gate reads the FULL body from stdin, so the wrapper redirects the body
# file into each gate independently. This is NOT a single Unix pipe: the first
# gate consumes stdin and relays only its own diagnostics, so
# `gate1 - < body | gate2 --pr N` would feed gate2 gate1's output, not the
# comment. The approval gate also refuses a TTY stdin, so the wrapper always
# redirects the file.
#
# Usage:
#   tools/pr_comment.sh --pr <N> --body-file <path> [--dry-run] [-- <extra gh args>]
#
# Exit codes (first failing gate wins; the gate's own 0/1/2 contract passes
# through unchanged):
#   0  clean (and, unless --dry-run, the comment was posted)
#   1  a gate refused the body (role-name leak, or duplicate/self-correction
#      approval)
#   2  argument / IO error, or a gate's structural failure
#
# This is operator tooling for local comment posting, not a CI step; keep it out
# of the workflow-pip / CI-mirror lint scopes. It needs `gh` authenticated for
# the post (and for the approval gate's `--pr` head/comment lookups), exactly
# like the raw chain it replaces.

set -eu

usage() {
    echo "usage: tools/pr_comment.sh --pr <N> --body-file <path> [--dry-run] [-- <extra gh args>]" >&2
    exit 2
}

PR=""
BODY=""
DRY=0
# Parse leading flags; `--` ends option parsing and leaves any extra `gh pr
# comment` args in "$@" so they forward space-safely (a collapsed `$*` string
# would word-split an argument that contains spaces). Each case shifts its own
# tokens so the post-`--` remainder in "$@" is exact.
while [ $# -gt 0 ]; do
    case "$1" in
        --pr) shift; [ $# -gt 0 ] || usage; PR="$1"; shift ;;
        --pr=*) PR="${1#--pr=}"; shift ;;
        --body-file) shift; [ $# -gt 0 ] || usage; BODY="$1"; shift ;;
        --body-file=*) BODY="${1#--body-file=}"; shift ;;
        --dry-run) DRY=1; shift ;;
        --) shift; break ;;
        -h|--help) usage ;;
        *) echo "pr_comment: unknown argument: $1" >&2; usage ;;
    esac
done

[ -n "$PR" ] || { echo "pr_comment: --pr is required" >&2; usage; }
[ -n "$BODY" ] || { echo "pr_comment: --body-file is required" >&2; usage; }
[ -f "$BODY" ] || { echo "pr_comment: body file not found: $BODY" >&2; exit 2; }

# Resolve the repo root from this script's own location so the gates and the
# venv interpreter resolve regardless of the caller's working directory.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Prefer the project venv interpreter (matches the documented chains); fall back
# to the operator's python3.
PY="$REPO_ROOT/.venv/bin/python3"
[ -x "$PY" ] || PY=python3

# Gate 1: role-name leaks. Redirect the body file into stdin.
"$PY" "$REPO_ROOT/tools/check_role_name_leaks.py" - < "$BODY" || exit $?

# Gate 2: duplicate / self-correction approval. Redirect the body file into stdin.
"$PY" "$REPO_ROOT/tools/check_approval_duplicate.py" --pr "$PR" < "$BODY" || exit $?

if [ "$DRY" -eq 1 ]; then
    echo "pr_comment: gates clean; --dry-run set, not posting to PR #$PR"
    exit 0
fi

# Post. Any extra gh args after `--` are forwarded verbatim (space-safe via "$@").
gh pr comment "$PR" --body-file "$BODY" "$@"
