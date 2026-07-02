#!/usr/bin/env bash
set -euo pipefail

GITHUB_OUTPUT_MODE=0
FORCE_APP=0
CHANGED_FILE_LIST=""
CHANGED_PATH_ARGS=()

usage() {
  cat <<'EOF'
Usage: bash scripts/classify-ci-changes.sh [options] [changed-files.txt]
       bash scripts/classify-ci-changes.sh [options] -- changed-file [...]

Classify a diff so GitHub Actions can choose a lightweight, quick, or full gate.

Options:
  --github-output   Write outputs to $GITHUB_OUTPUT instead of stdout.
  --force-app       Treat the diff as app-required regardless of paths.
  -h, --help        Show this help.

If changed-files.txt and explicit paths are omitted, the script compares HEAD to
the merge base of TRELLIS_CI_BASE_REF (default: origin/main) and includes
unstaged/staged paths.
EOF
}

normalize_path() {
  local path="$1"
  path="${path#./}"
  printf '%s\n' "$path"
}

is_workflow_path() {
  case "$1" in
    .github/workflows/*) return 0 ;;
    *) return 1 ;;
  esac
}

is_dependency_path() {
  case "$1" in
    pyproject.toml|uv.lock|requirements*.txt|.pre-commit-config.yaml|.github/dependabot.yml)
      return 0
      ;;
    .github/workflows/socket.yml|.github/workflows/dependabot-auto-merge.yml)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

is_python_path() {
  case "$1" in
    *.py) return 0 ;;
    *) return 1 ;;
  esac
}

is_review_tooling_path() {
  case "$1" in
    scripts/classify-ci-changes.sh|scripts/classify_ci_changes.sh|scripts/check-review-preflight.mjs|scripts/sd-ai-command-pack-install-audit.py|scripts/sd-ai-command-pack-pr-body-scope.py|scripts/sd-ai-command-pack-review-scope.sh|scripts/sd-ai-command-pack-review-preflight.mjs|scripts/sd-ai-command-pack-review-local.sh|scripts/sd-ai-command-pack-full-check.sh|scripts/sd-ai-command-pack-housekeeping.sh)
      return 0
      ;;
    .sd-ai-command-pack/pr-body-scope.json|tests/test_pr_body_scope_lint.py)
      return 0
      ;;
    .agents/*|.codex/*|.claude/*|.gemini/*|.opencode/*|.prism/*)
      return 0
      ;;
    .github/agents/*|.github/hooks/*|.github/instructions/*|.github/prompts/*|.github/skills/*)
      return 0
      ;;
    .github/copilot-instructions.md|.github/copilot/hooks.json|.github/copilot/hooks/*)
      return 0
      ;;
    docs/DEVELOPMENT_CYCLE.md|docs/REVIEW_PATTERNS.md|docs/SD_AI_COMMAND_PACK.md)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

is_lightweight_path() {
  local path="$1"

  case "$path" in
    AGENTS.md|CLAUDE.md|README.md|LICENSE|LICENSE.*|.github/PULL_REQUEST_TEMPLATE.md)
      return 0
      ;;
    docs/*.md|docs/**/*.md)
      return 0
      ;;
    .trellis/spec/*|.trellis/tasks/*|.trellis/workspace/*)
      return 0
      ;;
  esac

  if is_review_tooling_path "$path"; then
    return 0
  fi

  return 1
}

collect_changed_files() {
  local base_ref="${TRELLIS_CI_BASE_REF:-origin/main}"
  local merge_base=""

  if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    merge_base="$(git merge-base "$base_ref" HEAD 2>/dev/null || true)"
    if [ -n "$merge_base" ]; then
      git diff --name-only "$merge_base"..HEAD
    fi
    git diff --name-only
    git diff --cached --name-only
    git ls-files --others --exclude-standard
  fi
}

classify_path() {
  local path="$1"

  changed_count=$((changed_count + 1))

  if is_dependency_path "$path"; then
    dependency_changed="true"
  fi
  if is_workflow_path "$path"; then
    workflow_changed="true"
  fi
  if is_python_path "$path"; then
    python_changed="true"
  fi
  if is_review_tooling_path "$path"; then
    review_tooling_changed="true"
  fi

  if ! is_lightweight_path "$path"; then
    lightweight_only="false"
    app_required="true"
  fi
}

read_changed_paths() {
  local path=""

  while IFS= read -r path || [ -n "$path" ]; do
    [ -n "$path" ] || continue
    path="$(normalize_path "$path")"
    [ -n "$path" ] || continue
    classify_path "$path"
  done
}

emit_output() {
  local key="$1"
  local value="$2"

  if [ "$GITHUB_OUTPUT_MODE" -eq 1 ]; then
    if [ -z "${GITHUB_OUTPUT:-}" ]; then
      printf 'error: --github-output requires GITHUB_OUTPUT\n' >&2
      exit 2
    fi
    printf '%s=%s\n' "$key" "$value" >> "$GITHUB_OUTPUT"
  else
    printf '%s=%s\n' "$key" "$value"
  fi
}

parse_args() {
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --github-output)
        GITHUB_OUTPUT_MODE=1
        ;;
      --force-app)
        FORCE_APP=1
        ;;
      -h|--help)
        usage
        exit 0
        ;;
      --)
        shift
        CHANGED_PATH_ARGS=("$@")
        break
        ;;
      -*)
        printf 'error: unknown option: %s\n' "$1" >&2
        usage >&2
        exit 2
        ;;
      *)
        if [ -n "$CHANGED_FILE_LIST" ]; then
          printf 'error: only one changed-files list may be provided\n' >&2
          exit 2
        fi
        CHANGED_FILE_LIST="$1"
        ;;
    esac
    shift
  done

  if [ -n "$CHANGED_FILE_LIST" ] && [ "${#CHANGED_PATH_ARGS[@]}" -gt 0 ]; then
    printf 'error: provide a changed-files list or explicit paths, not both\n' >&2
    exit 2
  fi
}

main() {
  parse_args "$@"

  if [ -n "$CHANGED_FILE_LIST" ] && [ ! -f "$CHANGED_FILE_LIST" ]; then
    printf 'error: changed-files list not found: %s\n' "$CHANGED_FILE_LIST" >&2
    exit 2
  fi

  local lightweight_only="true"
  local app_required="false"
  local dependency_changed="false"
  local workflow_changed="false"
  local python_changed="false"
  local review_tooling_changed="false"
  local changed_count=0

  if [ "${#CHANGED_PATH_ARGS[@]}" -gt 0 ]; then
    read_changed_paths < <(printf '%s\n' "${CHANGED_PATH_ARGS[@]}")
  elif [ -n "$CHANGED_FILE_LIST" ]; then
    read_changed_paths <"$CHANGED_FILE_LIST"
  else
    read_changed_paths < <(collect_changed_files)
  fi

  if [ "$changed_count" -eq 0 ]; then
    lightweight_only="false"
    app_required="true"
  fi

  if [ "$dependency_changed" = "true" ] || [ "$workflow_changed" = "true" ]; then
    lightweight_only="false"
    app_required="true"
  fi

  if [ "$FORCE_APP" -eq 1 ]; then
    lightweight_only="false"
    app_required="true"
  fi

  emit_output "changed_count" "$changed_count"
  emit_output "lightweight_only" "$lightweight_only"
  emit_output "app_required" "$app_required"
  emit_output "dependency_changed" "$dependency_changed"
  emit_output "workflow_changed" "$workflow_changed"
  emit_output "python_changed" "$python_changed"
  emit_output "review_tooling_changed" "$review_tooling_changed"
}

main "$@"
