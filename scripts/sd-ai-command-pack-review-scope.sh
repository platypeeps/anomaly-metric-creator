#!/usr/bin/env bash
# shellcheck disable=SC1090
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
TARGETS_FILE="${SD_AI_COMMAND_PACK_TARGETS_FILE:-$REPO_ROOT/.sd-ai-command-pack/installed-targets.txt}"
MODE="${SD_AI_COMMAND_PACK_SCOPE_CHECK:-auto}"
GH_MODE="${SD_AI_COMMAND_PACK_SCOPE_CHECK_GH:-auto}"
scope_categories=()

warn() {
  printf 'warning: %s\n' "$*" >&2
}

fail() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

source_sd_ai_command_pack_shell_lib() {
  local lib="$SCRIPT_DIR/sd-ai-command-pack-shell-lib.sh"
  if [ ! -r "$lib" ]; then
    printf 'sd-ai-command-pack-review-scope: missing shared helper library: %s\n' "$lib" >&2
    exit 1
  fi
  . "$lib"
}

source_sd_ai_command_pack_shell_lib

is_disabled() {
  case "${1:-}" in
    0|false|FALSE|no|NO|skip|none) return 0 ;;
    *) return 1 ;;
  esac
}

is_required() {
  case "${1:-}" in
    required|1|true|TRUE|yes|YES) return 0 ;;
    *) return 1 ;;
  esac
}

have() {
  command -v "$1" >/dev/null 2>&1
}

scope_base_ref() {
  if configured_review_base_ref SD_AI_COMMAND_PACK_SCOPE_BASE_REF; then
    return
  fi
  if configured_review_base_ref SD_AI_COMMAND_PACK_FULL_CHECK_BASE_REF; then
    return
  fi
  default_review_base_ref
}

normalize_repo_path() {
  local path="${1#./}"
  printf '%s' "${path//\\//}"
}

collect_changed_files() {
  local base_ref
  base_ref="$(scope_base_ref)"

  if has_ref "$base_ref"; then
    git diff --name-only "$base_ref"...HEAD
  else
    warn "Could not resolve $base_ref; copied-file scope check will use local changes only."
  fi

  git diff --cached --name-only
  git diff --name-only
  git ls-files --others --exclude-standard
}

is_trellis_runtime_path() {
  local path
  path="$(normalize_repo_path "$1")"

  case "$path" in
    .trellis/scripts/*|.trellis/agents/*|.cursor/hooks.json|.cursor/hooks/*|.github/hooks/trellis.json|.github/copilot/hooks.json|.github/copilot/hooks/*|.opencode/lib/trellis-context.js|\
    .agent/skills/trellis-*/*|.agents/skills/trellis-*/*|.claude/skills/trellis-*/*|.codebuddy/skills/trellis-*/*|.cursor/skills/trellis-*/*|.devin/skills/trellis-*/*|\
    .factory/skills/trellis-*/*|.gemini/skills/trellis-*/*|.github/skills/trellis-*/*|.kilocode/skills/trellis-*/*|.kiro/skills/trellis-*/*|.opencode/skills/trellis-*/*|\
    .pi/skills/trellis-*/*|.qoder/skills/trellis-*/*|.reasonix/skills/trellis-*/*|.trae/skills/trellis-*/*|.zcode/skills/trellis-*/*|\
    .cursor/commands/trellis-*.md|.qoder/commands/trellis-*.md|.trae/commands/trellis-*.md|\
    .claude/commands/trellis/*|.codebuddy/commands/trellis/*|.factory/commands/trellis/*|.gemini/commands/trellis/*|.opencode/commands/trellis/*|.zcode/commands/trellis/*|\
    .agent/workflows/start.md|.agent/workflows/continue.md|.agent/workflows/finish-work.md|\
    .kilocode/workflows/start.md|.kilocode/workflows/continue.md|.kilocode/workflows/finish-work.md|\
    .devin/workflows/trellis-*.md|.pi/prompts/trellis-*.md|.pi/extensions/trellis/*|\
    .claude/hooks/*|.codebuddy/hooks/*|.factory/hooks/*|.gemini/hooks/*|.kiro/hooks/*|.qoder/hooks/*|.trae/hooks/*|.trae/hooks.json|\
    .claude/agents/trellis-*.md|.codebuddy/agents/trellis-*.md|.cursor/agents/trellis-*.md|.factory/droids/trellis-*.md|.gemini/agents/trellis-*.md|\
    .kiro/agents/trellis*.json|.opencode/agents/trellis-*.md|.pi/agents/trellis-*.md|.qoder/agents/trellis-*.md|.trae/agents/trellis-*.md|\
    .zcode/agents/trellis-*.md|.zcode/cli/agents/trellis-*.md|.codex/agents/trellis-*.toml|.codex/config.toml|.codex/hooks.json|.codex/hooks/*|\
    .codebuddy/settings.json|.factory/settings.json|.pi/settings.json|.qoder/settings.json|\
    .github/agents/trellis-*.agent.md)
      return 0
      ;;
    .github/prompts/continue.prompt.md|.github/prompts/finish-work.prompt.md)
      return 0
      ;;
  esac

  return 1
}

is_pack_target_path() {
  local path
  path="$(normalize_repo_path "$1")"

  [[ -f "$TARGETS_FILE" ]] || return 1
  grep -Fxq -- "$path" "$TARGETS_FILE"
}

is_copied_review_scope_path() {
  local path
  path="$(normalize_repo_path "$1")"

  if is_pack_target_path "$path" || is_trellis_runtime_path "$path"; then
    return 0
  fi

  return 1
}

is_repository_map_scope_path() {
  local path
  path="$(normalize_repo_path "$1")"

  case "$path" in
    docs/repomix-map.md|scripts/update_repomix)
      return 0
      ;;
  esac

  return 1
}

is_trellis_journal_scope_path() {
  local path
  path="$(normalize_repo_path "$1")"

  case "$path" in
    .trellis/workspace/*/journal-*.md|.trellis/workspace/*/index.md)
      return 0
      ;;
  esac

  return 1
}

github_pr_body_mentions_scope() {
  local body="$1"

  grep -Eiq '^[[:space:]>#*\-]*(Tooling/generated scope|Generated/tooling scope|Copied/generated scope)(:.*|[[:space:]]*)$' <<<"$body"
}

check_pr_body_scope() {
  local scoped_count="$1"

  if [[ "$scoped_count" -eq 0 ]]; then
    return 0
  fi

  if is_disabled "$GH_MODE"; then
    return 0
  fi

  if [ "${SD_AI_COMMAND_PACK_SCOPE_PR_BODY+x}" ]; then
    if ! github_pr_body_mentions_scope "$SD_AI_COMMAND_PACK_SCOPE_PR_BODY"; then
      fail "tooling/generated files changed, but the provided PR body does not include a recognized tooling/generated scope section"
    fi
    return 0
  fi

  if [ "${REVIEW_PREFLIGHT_PR_BODY+x}" ]; then
    warn "REVIEW_PREFLIGHT_PR_BODY is deprecated; prefer SD_AI_COMMAND_PACK_SCOPE_PR_BODY."
    if ! github_pr_body_mentions_scope "$REVIEW_PREFLIGHT_PR_BODY"; then
      fail "tooling/generated files changed, but the provided PR body does not include a recognized tooling/generated scope section"
    fi
    return 0
  fi

  if ! have gh; then
    if is_required "$GH_MODE"; then
      fail "gh is required for tooling/generated PR scope checks but is not on PATH"
    fi
    warn "gh not found; skipping tooling/generated PR scope body check."
    return 0
  fi

  local pr_json
  if ! pr_json="$(gh pr view --json body,title,url 2>/dev/null)"; then
    if is_required "$GH_MODE"; then
      fail "gh could not resolve the current PR for tooling/generated scope checks"
    fi
    warn "No current PR found; skipping tooling/generated PR scope body check."
    return 0
  fi

  # Isolate the PR body so the scope heading is matched against the body only,
  # never the title or url. Without a JSON parser, skip rather than grep the raw
  # JSON blob, which would risk a false pass on a heading-like title or url.
  local pr_body
  if have python3; then
    pr_body="$(python3 -c 'import json,sys; data=json.load(sys.stdin); print(data.get("body") or "")' <<<"$pr_json")"
  elif have jq; then
    pr_body="$(jq -r '.body // ""' <<<"$pr_json")"
  else
    if is_required "$GH_MODE"; then
      fail "tooling/generated scope check requires python3 or jq to parse the PR body, but neither is on PATH"
    fi
    warn "Neither python3 nor jq found; cannot parse the PR body, skipping tooling/generated PR scope body check."
    return 0
  fi

  if ! github_pr_body_mentions_scope "$pr_body"; then
    fail "tooling/generated files changed, but the PR body does not include a recognized tooling/generated scope section"
  fi
}

add_category() {
  local category="$1"
  local existing

  if [[ "${#scope_categories[@]}" -gt 0 ]]; then
    for existing in "${scope_categories[@]}"; do
      if [[ "$existing" == "$category" ]]; then
        return 0
      fi
    done
  fi

  scope_categories+=("$category")
}

main() {
  if is_disabled "$MODE"; then
    warn "Skipping tooling/generated review-scope check because SD_AI_COMMAND_PACK_SCOPE_CHECK=$MODE."
    return 0
  fi

  # bash's `cd ""` is a silent success, so an empty root (failed
  # resolution) must be rejected explicitly.
  if [ -z "$REPO_ROOT" ] || ! cd -- "$REPO_ROOT"; then
    fail "cannot resolve repository root"
  fi

  local changed_file scoped_file
  local scoped_changes=()
  scope_categories=()
  while IFS= read -r changed_file; do
    [[ -n "$changed_file" ]] || continue
    local category=""
    if is_copied_review_scope_path "$changed_file"; then
      category="copied/generated Trellis or sd-ai-command-pack files"
    elif is_repository_map_scope_path "$changed_file"; then
      category="known repository-map files"
    elif is_trellis_journal_scope_path "$changed_file"; then
      category="Trellis workspace journal/index files"
    fi
    if [[ -n "$category" ]]; then
      scoped_changes+=("$changed_file")
      add_category "$category"
    fi
  done < <(collect_changed_files | sed '/^$/d' | sort -u)

  if [[ "${#scoped_changes[@]}" -eq 0 ]]; then
    return 0
  fi

  printf 'info: Tooling/generated review-scope files changed; review local integration, wiring, provenance, and secrets only.\n'
  printf 'info: Scope categories:\n'
  local category
  for category in "${scope_categories[@]}"; do
    printf '  - %s\n' "$category"
  done
  printf 'info: Changed scope files:\n'
  for scoped_file in "${scoped_changes[@]}"; do
    printf '  - %s\n' "$scoped_file"
  done

  check_pr_body_scope "${#scoped_changes[@]}"
}

main "$@"
