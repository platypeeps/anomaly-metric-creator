#!/usr/bin/env bash
# shellcheck disable=SC1090
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# The repository being classified, which is not necessarily the one hosting
# this script: a thin install moves this file to the machine, where
# `$SCRIPT_DIR/..` is the agents directory rather than any checkout. Same
# three rungs as `sd-ai-command-pack-full-check.sh`, whose comment records
# which two come from the shared shell library and why the third does not.
# Under a fat install invoked from inside the repository the second rung
# returns exactly what the third one used to.
REPO_ROOT="${SD_AI_COMMAND_PACK_REPO_ROOT:-}"
if [ -n "$REPO_ROOT" ]; then
  # Only this rung can hand back a relative path: `git rev-parse
  # --show-toplevel` and `cd ... && pwd` both answer absolute. Left relative it
  # would be re-resolved against the working directory this script later `cd`s
  # into, so every path built from it -- the targets receipt first -- would
  # point somewhere else the moment the root stopped being the caller's cwd.
  if ! REPO_ROOT="$(cd -- "$REPO_ROOT" 2>/dev/null && pwd -P)"; then
    REPO_ROOT=""
  else
    # The shared shell library reads the raw override rather than this
    # variable (`sd-ai-command-pack-shell-lib.sh:172`), so normalizing only the
    # local copy would leave the relative form live for the cache root and for
    # every child process. Put the absolute form back where it came from.
    export SD_AI_COMMAND_PACK_REPO_ROOT="$REPO_ROOT"
  fi
fi
if [ -z "$REPO_ROOT" ]; then
  REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
fi
if [ -z "$REPO_ROOT" ]; then
  REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
fi
TARGETS_FILE="${SD_AI_COMMAND_PACK_TARGETS_FILE:-$REPO_ROOT/.sd-ai-command-pack/installed-targets.txt}"
MODE="${SD_AI_COMMAND_PACK_SCOPE_CHECK:-auto}"
GH_MODE="${SD_AI_COMMAND_PACK_SCOPE_CHECK_GH:-auto}"
scope_categories=()
# Recognized tooling/generated PR-body scope sections, named in author-time
# advisories so the section gets written before the PR exists.
SCOPE_SECTION_HINT="a recognized tooling/generated scope section (for example 'Tooling/generated scope:')"

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
    0|false|FALSE|no|NO|skip|none|off|OFF|disabled|DISABLED) return 0 ;;
    *) return 1 ;;
  esac
}

is_required() {
  case "${1:-}" in
    required|1|true|TRUE|yes|YES) return 0 ;;
    *) return 1 ;;
  esac
}

is_advisory() {
  case "${1:-}" in
    advisory|advise|warn|soft) return 0 ;;
    *) return 1 ;;
  esac
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
    .claude/settings.json|.codebuddy/settings.json|.factory/settings.json|.gemini/settings.json|.pi/settings.json|.qoder/settings.json|\
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

  case "$path" in
    .sd-ai-command-pack/installed-targets.txt|.sd-ai-command-pack/manifest.json|.sd-ai-command-pack/provenance.json)
      return 0
      ;;
  esac

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

# Resolve what is known about the current PR body and print exactly one state
# token. This function reports nothing and decides nothing: advisory mode and
# enforcing mode want the same evidence but draw opposite conclusions from it,
# so the policy lives in the callers.
#
# It must never exit. `fail` calls `exit 1` and the script runs under `set -e`,
# so every subprocess here is guarded and neither `fail` nor `warn` is called.
# Callers reach it only through `resolve_pr_body_scope_state_or_unknown`, which
# captures with `|| true` and rewrites an empty result to `unknown:resolver_error`,
# so a future edit that breaks either rule degrades to a named state instead of an
# unexplained abort.
resolve_pr_body_scope_state() {
  # A supplied body that already satisfies the check is positive evidence, and
  # positive evidence outranks every reason to give up below — including a
  # disabled gh, which would otherwise report `unknown` while holding proof in
  # hand. Enforcing mode is unaffected: both orders return 0 for this input.
  if [ "${SD_AI_COMMAND_PACK_SCOPE_PR_BODY+x}" ] &&
    github_pr_body_mentions_scope "$SD_AI_COMMAND_PACK_SCOPE_PR_BODY"; then
    printf 'satisfied\n'
    return 0
  fi

  if is_disabled "$GH_MODE"; then
    printf 'unknown:gh_disabled\n'
    return 0
  fi

  if [ "${SD_AI_COMMAND_PACK_SCOPE_PR_BODY+x}" ]; then
    printf 'unsatisfied:provided\n'
    return 0
  fi

  if ! have gh; then
    printf 'unknown:gh_missing\n'
    return 0
  fi

  local pr_json
  if ! pr_json="$(gh pr view --json body,title,url,state 2>/dev/null)"; then
    printf 'unknown:no_pr\n'
    return 0
  fi

  # Isolate the PR body so the scope heading is matched against the body only,
  # never the title or url. Also read `state`: `gh pr view` with no argument
  # resolves the branch's PR and will return a CLOSED or MERGED one when no open
  # PR exists, so its (possibly stale) body must not bleed into the check
  # (finding #6: a closed same-branch PR failed the branch's next candidate).
  # Without a JSON parser, skip rather than grep the raw JSON blob, which would
  # risk a false pass on a heading-like title or url.
  local pr_body pr_state
  if have python3; then
    if ! pr_state="$(python3 -c 'import json,sys; data=json.load(sys.stdin); print(data.get("state") or "")' <<<"$pr_json" 2>/dev/null)" ||
      ! pr_body="$(python3 -c 'import json,sys; data=json.load(sys.stdin); print(data.get("body") or "")' <<<"$pr_json" 2>/dev/null)"; then
      printf 'unknown:parse_error\n'
      return 0
    fi
  elif have jq; then
    if ! pr_state="$(jq -r '.state // ""' <<<"$pr_json" 2>/dev/null)" ||
      ! pr_body="$(jq -r '.body // ""' <<<"$pr_json" 2>/dev/null)"; then
      printf 'unknown:parse_error\n'
      return 0
    fi
  else
    printf 'unknown:no_parser\n'
    return 0
  fi

  # Only an OPEN PR's body is authoritative. A CLOSED/MERGED same-branch PR is
  # treated as no usable PR so the caller falls through to the env-provided
  # intended body (set pre-publication) instead of a stale closed body.
  if [ "$pr_state" != "OPEN" ]; then
    printf 'unknown:pr_closed\n'
    return 0
  fi

  if github_pr_body_mentions_scope "$pr_body"; then
    printf 'satisfied\n'
  else
    printf 'unsatisfied:resolved\n'
  fi
}

# The only supported way to call the resolver. It is contracted to print a token
# and never exit, but a future edit could break either half, and an empty capture
# would then reach a caller's default arm as an unexplained state. Normalizing in
# one place keeps both callers on the named token the contract promises, and
# keeps them from drifting apart.
resolve_pr_body_scope_state_or_unknown() {
  local state
  state="$(resolve_pr_body_scope_state || true)"

  if [ -z "$state" ]; then
    state='unknown:resolver_error'
  fi

  printf '%s\n' "$state"
}

check_pr_body_scope() {
  local scoped_count="$1"

  if [[ "$scoped_count" -eq 0 ]]; then
    return 0
  fi

  local state
  state="$(resolve_pr_body_scope_state_or_unknown)"

  case "$state" in
    satisfied)
      return 0
      ;;
    unsatisfied:provided)
      fail "tooling/generated files changed, but the provided PR body does not include a recognized tooling/generated scope section"
      ;;
    unsatisfied:resolved)
      fail "tooling/generated files changed, but the PR body does not include a recognized tooling/generated scope section"
      ;;
    unknown:gh_disabled)
      return 0
      ;;
    unknown:gh_missing)
      if is_required "$GH_MODE"; then
        fail "gh is required for tooling/generated PR scope checks but is not on PATH"
      fi
      warn "gh not found; skipping tooling/generated PR scope body check."
      return 0
      ;;
    unknown:no_pr)
      if is_required "$GH_MODE"; then
        fail "gh could not resolve the current PR for tooling/generated scope checks"
      fi
      warn "No current PR found; when you open it, the PR body must include $SCOPE_SECTION_HINT."
      return 0
      ;;
    unknown:pr_closed)
      # The only same-branch PR is closed/merged; its body is not authoritative.
      # Behaves like no open PR: the intended body is env-provided pre-publication.
      if is_required "$GH_MODE"; then
        fail "gh found only a closed/merged PR for this branch; open the PR (its body must include a tooling/generated scope section) or provide SD_AI_COMMAND_PACK_SCOPE_PR_BODY"
      fi
      warn "Only a closed/merged PR was found for this branch; when you open the new PR, its body must include $SCOPE_SECTION_HINT."
      return 0
      ;;
    unknown:no_parser)
      if is_required "$GH_MODE"; then
        fail "tooling/generated scope check requires python3 or jq to parse the PR body, but neither is on PATH"
      fi
      warn "Neither python3 nor jq found; cannot parse the PR body, skipping tooling/generated PR scope body check."
      return 0
      ;;
    unknown:parse_error)
      fail "tooling/generated scope check could not parse the PR body returned by gh"
      ;;
    unknown:resolver_error)
      # Only reachable if the resolver stops printing a token. Named separately
      # from `*)` so the failure says which half of the contract broke.
      fail "tooling/generated scope check resolver returned no PR body state"
      ;;
    *)
      fail "tooling/generated scope check could not determine the PR body state"
      ;;
  esac
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

# Delegate the layout question to the one implementation of it. This binding
# exists so a caller that wants the classification *as data* stops having to
# run this script once per path and read its exit code -- which is the gap that
# produced five consumer-side reimplementations of the same matcher.
layout_json() {
  local python_bin
  python_bin="$(command -v python3 || true)"
  if [ -z "$python_bin" ]; then
    fail "python3 is required for --json layout classification"
  fi
  "$python_bin" "$SCRIPT_DIR/sd-ai-command-pack-review-layout.py" --root "$REPO_ROOT" "$@"
}

main() {
  if [ "${1:-}" = "--json" ]; then
    shift
    layout_json "$@"
    return $?
  fi

  if is_disabled "$MODE"; then
    warn "Skipping tooling/generated review-scope check because SD_AI_COMMAND_PACK_SCOPE_CHECK=$MODE."
    return 0
  fi

  # bash's `cd ""` is a silent success, so an empty root (failed
  # resolution) must be rejected explicitly.
  if [ -z "$REPO_ROOT" ] || ! cd -- "$REPO_ROOT"; then
    fail "cannot resolve repository root"
  fi
  prepare_tool_cache_env || exit 5

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

  if is_advisory "$MODE"; then
    # Advisory never fails and never consults gh-mode requiredness; that is an
    # enforcing-mode concept, and a fatal advisory would break the contract with
    # the review preflight, which treats this script as non-fatal.
    local advisory_state
    advisory_state="$(resolve_pr_body_scope_state_or_unknown)"

    # Silence is the whole point: a body that already carries the section has
    # nothing left to remind anyone about.
    if [[ "$advisory_state" == satisfied ]]; then
      return 0
    fi

    local advisory_message
    case "$advisory_state" in
      unsatisfied:*)
        advisory_message="This branch changes tooling/generated files, but the PR body does not include $SCOPE_SECTION_HINT. Add it to the PR body."
        ;;
      *)
        # Every unknown state warns. Absence of evidence is not evidence the
        # body is fine, and an author without gh is the one who can least afford
        # to lose the reminder.
        advisory_message="This branch changes tooling/generated files; the PR body must include $SCOPE_SECTION_HINT. Add it before opening the PR."
        ;;
    esac

    warn "$advisory_message"
    # Stable machine marker so callers (e.g. the review preflight) surface this
    # advisory by token rather than matching the human-readable wording above.
    printf 'sd-ai-command-pack-scope-advisory: %s\n' "$advisory_message"
    return 0
  fi

  check_pr_body_scope "${#scoped_changes[@]}"
}

main "$@"
