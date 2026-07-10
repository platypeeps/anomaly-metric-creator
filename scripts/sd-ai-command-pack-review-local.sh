#!/usr/bin/env bash
# shellcheck disable=SC1090,SC2129,SC2329
set -uo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
# The script runs without -e, and bash's `cd ""` is a silent success, so
# guard both an empty root and a failed cd explicitly.
if [ -z "$REPO_ROOT" ] || ! cd -- "$REPO_ROOT"; then
  printf 'sd-ai-command-pack-review-local: cannot resolve repository root\n' >&2
  exit 1
fi

OVERALL_STATUS=0
REVIEW_LOCAL_SCOPE="${SD_AI_COMMAND_PACK_REVIEW_LOCAL_SCOPE:-diff}"
REVIEW_LOCAL_TEMP_FILES=()

cleanup_review_local_temp_files() {
  set +u
  local file
  for file in "${REVIEW_LOCAL_TEMP_FILES[@]}"; do
    [ -n "$file" ] || continue
    rm -f "$file"
  done
  set -u
}

trap cleanup_review_local_temp_files EXIT
trap 'cleanup_review_local_temp_files; exit 129' HUP
trap 'cleanup_review_local_temp_files; exit 130' INT
trap 'cleanup_review_local_temp_files; exit 143' TERM

section() {
  printf '\n==> %s\n' "$*"
}

warn() {
  printf 'warning: %s\n' "$*" >&2
}

source_sd_ai_command_pack_shell_lib() {
  local lib="$SCRIPT_DIR/sd-ai-command-pack-shell-lib.sh"
  if [ ! -r "$lib" ]; then
    printf 'sd-ai-command-pack-review-local: missing shared helper library: %s\n' "$lib" >&2
    exit 1
  fi
  . "$lib"
}

source_sd_ai_command_pack_shell_lib

have() {
  command -v "$1" >/dev/null 2>&1
}

is_disabled() {
  case "${1:-}" in
    0|false|FALSE|no|NO|skip|none) return 0 ;;
    *) return 1 ;;
  esac
}

collect_reviewable_tracked_paths() {
  local path
  while IFS= read -r path; do
    [ -n "$path" ] || continue
    [ -f "$path" ] || continue
    if ! path_is_standard_review_scan_excluded "$path"; then
      printf '%s\n' "$path"
    fi
  done < <(git ls-files)
}

collect_reviewable_local_paths() {
  local paths_file
  paths_file="$(mktemp "${TMPDIR:-/tmp}/sd-ai-command-pack-review-paths.XXXXXX")"
  REVIEW_LOCAL_TEMP_FILES+=("$paths_file")
  : >"$paths_file"

  git diff --cached --name-only >>"$paths_file"
  git diff --name-only >>"$paths_file"
  git ls-files --others --exclude-standard >>"$paths_file"

  sort -u "$paths_file" | while IFS= read -r path; do
    [ -n "$path" ] || continue
    if ! path_is_standard_review_scan_excluded "$path"; then
      printf '%s\n' "$path"
    fi
  done

  rm -f "$paths_file"
}

reviewable_local_paths_present() {
  local path
  while IFS= read -r path; do
    [ -n "$path" ] || continue
    return 0
  done < <(collect_reviewable_local_paths)
  return 1
}

collect_reviewable_branch_paths() {
  local base_ref="$1"
  if git rev-parse --verify --quiet "$base_ref^{commit}" >/dev/null; then
    git diff --name-only "$base_ref"...HEAD | while IFS= read -r path; do
      [ -n "$path" ] || continue
      if ! path_is_standard_review_scan_excluded "$path"; then
        printf '%s\n' "$path"
      fi
    done
  else
    warn "Could not resolve $base_ref; branch review filter is unavailable."
  fi
}

collect_reviewable_changed_paths() {
  local base_ref="$1"
  if reviewable_local_paths_present; then
    collect_reviewable_local_paths
  else
    collect_reviewable_branch_paths "$base_ref"
  fi
}

review_filter_pattern_for_path() {
  local path="$1"
  printf '%s\n' "$path"
}

review_filter_csv_from_paths() {
  local patterns_file
  patterns_file="$(mktemp "${TMPDIR:-/tmp}/sd-ai-command-pack-review-filters.XXXXXX")"
  REVIEW_LOCAL_TEMP_FILES+=("$patterns_file")
  : >"$patterns_file"

  local path
  while IFS= read -r path; do
    [ -n "$path" ] || continue
    review_filter_pattern_for_path "$path" >>"$patterns_file"
  done

  local patterns=()
  local pattern
  while IFS= read -r pattern; do
    [ -n "$pattern" ] || continue
    patterns+=("$pattern")
  done < <(sort -u "$patterns_file")

  rm -f "$patterns_file"
  # ${arr[@]+...} guards the empty-array case: bash < 4.4 (macOS ships 3.2)
  # treats "${arr[@]}" of an empty array as unbound under set -u.
  join_by_comma ${patterns[@]+"${patterns[@]}"}
}

reviewable_tracked_filter_csv() {
  collect_reviewable_tracked_paths | review_filter_csv_from_paths
}

reviewable_changed_filter_csv() {
  local base_ref="$1"
  collect_reviewable_changed_paths "$base_ref" | review_filter_csv_from_paths
}

normalize_review_scope() {
  case "${1:-diff}" in
    diff|changed|current) printf 'diff' ;;
    all|codebase|full) printf 'all' ;;
    *) return 1 ;;
  esac
}

review_command_name() {
  if [ "${REVIEW_LOCAL_SCOPE:-diff}" = "all" ]; then
    printf 'sd-review-local-all'
  else
    printf 'sd-review-local'
  fi
}

review_scope_label() {
  if [ "${REVIEW_LOCAL_SCOPE:-diff}" = "all" ]; then
    printf 'full codebase'
  else
    printf 'current diff'
  fi
}

usage() {
  cat <<'EOF'
Usage: bash scripts/sd-ai-command-pack-review-local.sh [--diff|--changed|--full-codebase|--all|--scope <diff|all>] [--list-tools] [tool ...]

Runs local review providers for the current diff or the full checked-out codebase.

Tools:
  prism      Built-in Prism review provider.
  gito       Built-in Gito review provider.
  all        Alias for: prism gito.
  default    Alias for: prism gito.

Custom tools are supported by setting:
  SD_AI_COMMAND_PACK_REVIEW_LOCAL_<TOOL>_COMMAND
  SD_AI_COMMAND_PACK_REVIEW_LOCAL_ALL_<TOOL>_COMMAND

Tool names must use only letters, numbers, dots, underscores, or hyphens.
Use --help to print this message.
EOF
}

list_tools() {
  printf 'prism\n'
  printf 'gito\n'
  printf 'all\n'
  printf 'default\n'
}

valid_tool_name() {
  case "$1" in
    ""|*/*|*\\*|*[!\._[:alnum:]-]*)
      return 1
      ;;
    *)
      return 0
      ;;
  esac
}

record_status() {
  local label="$1"
  local status="$2"
  if [ "$status" -ne 0 ]; then
    warn "$label exited with status $status."
    mark_overall_failure
  fi
}

mark_overall_failure() {
  if [ "$OVERALL_STATUS" -ne 2 ]; then
    OVERALL_STATUS=1
  fi
}

run_command() {
  local label="$1"
  shift
  section "$label"
  "$@"
  local status=$?
  record_status "$label" "$status"
}

handle_prism_status() {
  local label="$1"
  local status="$2"

  case "$status" in
    0)
      return 0
      ;;
    1)
      warn "$label reported findings at or above the configured threshold."
      mark_overall_failure
      ;;
    3|4)
      warn "$label could not complete because Prism provider authentication or configuration failed with exit code $status."
      mark_overall_failure
      ;;
    *)
      record_status "$label" "$status"
      ;;
  esac
}

tool_env_key() {
  printf '%s' "$1" \
    | tr '[:lower:]' '[:upper:]' \
    | sed 's/[^A-Z0-9_]/_/g'
}

configured_command_for_tool() {
  local tool="$1"
  local key
  key="$(tool_env_key "$tool")"

  if [ "${REVIEW_LOCAL_SCOPE:-diff}" = "all" ]; then
    local scoped_var_name="SD_AI_COMMAND_PACK_REVIEW_LOCAL_ALL_${key}_COMMAND"
    if [ -n "${!scoped_var_name:-}" ]; then
      printf '%s' "${!scoped_var_name}"
      return
    fi
  fi

  local var_name="SD_AI_COMMAND_PACK_REVIEW_LOCAL_${key}_COMMAND"
  printf '%s' "${!var_name:-}"
}

review_local_base_ref() {
  if configured_review_base_ref SD_AI_COMMAND_PACK_REVIEW_LOCAL_BASE_REF; then
    return
  fi
  if configured_review_base_ref SD_AI_COMMAND_PACK_FULL_CHECK_BASE_REF; then
    return
  fi
  default_review_base_ref
}

review_local_gito_base_ref() {
  if configured_review_base_ref SD_AI_COMMAND_PACK_REVIEW_LOCAL_GITO_BASE_REF; then
    return
  fi
  if configured_review_base_ref SD_AI_COMMAND_PACK_FULL_CHECK_GITO_BASE_REF; then
    return
  fi
  if configured_review_base_ref SD_AI_COMMAND_PACK_FULL_CHECK_BASE_REF; then
    return
  fi
  default_review_base_ref
}

detect_merge_base() {
  local base_ref
  base_ref="$(review_local_base_ref)"
  git merge-base "$base_ref" HEAD 2>/dev/null || true
}

build_prism_args() {
  PRISM_ARGS=()

  local fail_on="${SD_AI_COMMAND_PACK_REVIEW_LOCAL_PRISM_FAIL_ON:-${SD_AI_COMMAND_PACK_FULL_CHECK_PRISM_FAIL_ON:-high}}"
  if [ -n "$fail_on" ]; then
    PRISM_ARGS+=(--fail-on "$fail_on")
  fi

  local max_findings="${SD_AI_COMMAND_PACK_REVIEW_LOCAL_PRISM_MAX_FINDINGS:-${SD_AI_COMMAND_PACK_FULL_CHECK_PRISM_MAX_FINDINGS:-}}"
  if [ -n "$max_findings" ]; then
    PRISM_ARGS+=(--max-findings "$max_findings")
  fi

  local rules="${SD_AI_COMMAND_PACK_REVIEW_LOCAL_PRISM_RULES:-${SD_AI_COMMAND_PACK_FULL_CHECK_PRISM_RULES:-}}"
  if [ -z "$rules" ]; then
    if [ -f ".prism/rules.json" ]; then
      rules=".prism/rules.json"
    elif [ -f "prism-rules.json" ]; then
      rules="prism-rules.json"
    fi
  fi
  if [ -n "$rules" ] && [ -f "$rules" ]; then
    PRISM_ARGS+=(--rules "$rules")
  fi

  local excludes
  excludes="$(review_scan_exclude_globs_csv)"
  local configured_excludes="${SD_AI_COMMAND_PACK_REVIEW_LOCAL_PRISM_EXCLUDE:-${SD_AI_COMMAND_PACK_FULL_CHECK_PRISM_EXCLUDE:-}}"
  if [ -n "$configured_excludes" ]; then
    excludes="$excludes,$configured_excludes"
  fi
  PRISM_ARGS+=(--exclude "$excludes")
}

run_prism_command() {
  local label="$1"
  shift
  section "$label"
  prism "$@" "${PRISM_ARGS[@]}"
  local status=$?
  handle_prism_status "$label" "$status"
}

prism_output_indicates_empty_chunk() {
  local output_file="$1"
  grep -Eiq 'chunked review|no content in response' "$output_file"
}

gito_max_attempts() {
  positive_int_or_default "${SD_AI_COMMAND_PACK_REVIEW_LOCAL_GITO_MAX_ATTEMPTS:-2}" 2
}

gito_initial_retry_delay() {
  nonnegative_int_or_default "${SD_AI_COMMAND_PACK_REVIEW_LOCAL_GITO_RETRY_DELAY_SECONDS:-30}" 30
}

gito_max_retry_delay() {
  nonnegative_int_or_default "${SD_AI_COMMAND_PACK_REVIEW_LOCAL_GITO_RETRY_MAX_DELAY_SECONDS:-120}" 120
}

record_prism_empty_chunk_failure() {
  local label="$1"
  warn "$label returned an empty chunk response after fallback splitting."
  mark_overall_failure
}

run_prism_codebase_paths() {
  local label="$1"
  shift
  local paths=("$@")
  local paths_csv
  paths_csv="$(join_by_comma "${paths[@]}")"
  local output_file
  output_file="$(mktemp "${TMPDIR:-/tmp}/sd-ai-command-pack-prism-codebase.XXXXXX")"
  REVIEW_LOCAL_TEMP_FILES+=("$output_file")

  section "$label"
  prism review codebase --paths "$paths_csv" "${PRISM_ARGS[@]}" >"$output_file" 2>&1
  local status=$?
  cat "$output_file"

  if [ "$status" -eq 4 ] && [ "${#paths[@]}" -gt 1 ] && prism_output_indicates_empty_chunk "$output_file"; then
    rm -f "$output_file"
    warn "$label returned an empty chunk response; retrying each path individually."
    local path
    local path_index=1
    for path in "${paths[@]}"; do
      run_prism_codebase_paths "$label path $path_index" "$path"
      path_index=$((path_index + 1))
    done
    return
  fi

  if [ "$status" -eq 4 ] && prism_output_indicates_empty_chunk "$output_file"; then
    rm -f "$output_file"
    record_prism_empty_chunk_failure "$label"
    return
  fi
  rm -f "$output_file"
  handle_prism_status "$label" "$status"
}

run_prism_codebase_batches() {
  local batch_size
  batch_size="$(positive_int_or_default "${SD_AI_COMMAND_PACK_REVIEW_LOCAL_PRISM_CODEBASE_BATCH_SIZE:-25}" 25)"
  local batch_index=1
  local path_count=0
  local batch=()
  local path

  flush_prism_batch() {
    if [ "${#batch[@]}" -eq 0 ]; then
      return
    fi
    run_prism_codebase_paths "Prism review: full codebase batch $batch_index" "${batch[@]}"
    batch=()
    batch_index=$((batch_index + 1))
  }

  while IFS= read -r path; do
    [ -n "$path" ] || continue
    batch+=("$path")
    path_count=$((path_count + 1))
    if [ "${#batch[@]}" -ge "$batch_size" ]; then
      flush_prism_batch
    fi
  done < <(collect_reviewable_tracked_paths)

  flush_prism_batch

  if [ "$path_count" -eq 0 ]; then
    warn "No tracked files found for Prism full-codebase batch fallback."
  fi
}

run_prism_codebase_review() {
  local label="Prism review: full codebase"
  local output_file
  output_file="$(mktemp "${TMPDIR:-/tmp}/sd-ai-command-pack-prism-codebase.XXXXXX")"
  REVIEW_LOCAL_TEMP_FILES+=("$output_file")

  section "$label"
  prism review codebase "${PRISM_ARGS[@]}" >"$output_file" 2>&1
  local status=$?
  cat "$output_file"

  if [ "$status" -eq 4 ] && prism_output_indicates_empty_chunk "$output_file"; then
    rm -f "$output_file"
    if is_disabled "${SD_AI_COMMAND_PACK_REVIEW_LOCAL_PRISM_CODEBASE_FALLBACK:-1}"; then
      warn "Prism full-codebase batch fallback is disabled because SD_AI_COMMAND_PACK_REVIEW_LOCAL_PRISM_CODEBASE_FALLBACK=${SD_AI_COMMAND_PACK_REVIEW_LOCAL_PRISM_CODEBASE_FALLBACK:-}."
      warn "$label returned an empty chunk response."
      mark_overall_failure
      return
    fi
    warn "Prism full-codebase review returned an empty chunk response; retrying in tracked-file batches."
    run_prism_codebase_batches
    return
  fi

  rm -f "$output_file"
  handle_prism_status "$label" "$status"
}

run_prism_reviews() {
  local mode="${SD_AI_COMMAND_PACK_REVIEW_LOCAL_PRISM_MODE:-required}"
  if is_disabled "$mode"; then
    warn "Skipping Prism review because SD_AI_COMMAND_PACK_REVIEW_LOCAL_PRISM_MODE=$mode."
    return
  fi
  if ! have prism; then
    warn "Prism is required for $(review_command_name) but was not found on PATH."
    mark_overall_failure
    return
  fi

  build_prism_args

  if [ "$REVIEW_LOCAL_SCOPE" = "all" ]; then
    run_prism_codebase_review
    return
  fi

  local ran=0
  if reviewable_local_paths_present; then
    if ! git diff --quiet --; then
      ran=1
      run_prism_command "Prism review: unstaged changes" review unstaged
    fi

    if ! git diff --cached --quiet --; then
      ran=1
      run_prism_command "Prism review: staged changes" review staged
    fi

    if [ "$ran" -eq 0 ]; then
      local local_paths=()
      local path
      while IFS= read -r path; do
        [ -n "$path" ] || continue
        local_paths+=("$path")
      done < <(collect_reviewable_local_paths)
      ran=1
      if [ "${#local_paths[@]}" -eq 0 ]; then
        warn "No reviewable local changed files remain after exclusions; skipping Prism local review."
      else
        run_prism_codebase_paths "Prism review: local changed files" "${local_paths[@]}"
      fi
    fi
  else
    local merge_base
    merge_base="$(detect_merge_base)"
    if [ -n "$merge_base" ] && ! git diff --quiet "$merge_base"..HEAD --; then
      ran=1
      run_prism_command "Prism review: current branch diff" review range "$merge_base..HEAD"
    elif [ -z "$merge_base" ]; then
      warn "Could not resolve merge base for $(review_local_base_ref); skipping Prism current branch review."
    fi
  fi

  if [ "$ran" -eq 0 ]; then
    warn "No local changed files or current branch diff found for Prism review."
  fi
}

run_gito_review() {
  local mode="${SD_AI_COMMAND_PACK_REVIEW_LOCAL_GITO_MODE:-required}"
  if is_disabled "$mode"; then
    warn "Skipping Gito review because SD_AI_COMMAND_PACK_REVIEW_LOCAL_GITO_MODE=$mode."
    return
  fi
  if ! have gito; then
    warn "Gito is required for $(review_command_name) but was not found on PATH."
    mark_overall_failure
    return
  fi

  load_gito_pack_env
  prepare_gito_uv_env

  if [ "$REVIEW_LOCAL_SCOPE" = "all" ]; then
    local out_dir="${SD_AI_COMMAND_PACK_REVIEW_LOCAL_ALL_GITO_OUT_DIR:-${SD_AI_COMMAND_PACK_REVIEW_LOCAL_GITO_OUT_DIR:-${SD_AI_COMMAND_PACK_FULL_CHECK_GITO_OUT_DIR:-.build/review/gito-all}}}"
    local filters
    filters="$(reviewable_tracked_filter_csv)"
    if [ -z "$filters" ]; then
      warn "No tracked files remain after standard review-scan exclusions; skipping Gito full-codebase review."
      return
    fi
    mkdir -p "$out_dir"
    run_gito_command "Gito review: full codebase" gito review --all --path "$REPO_ROOT" --filter "$filters" --out "$out_dir"
    record_status "Gito review: full codebase" "$?"
    return
  fi

  local base_ref
  base_ref="$(review_local_gito_base_ref)"
  local out_dir="${SD_AI_COMMAND_PACK_REVIEW_LOCAL_GITO_OUT_DIR:-${SD_AI_COMMAND_PACK_FULL_CHECK_GITO_OUT_DIR:-.build/review/gito}}"
  local label
  if reviewable_local_paths_present; then
    label="Gito review: local changed files"
  else
    label="Gito review: current branch diff"
  fi
  local filters
  filters="$(reviewable_changed_filter_csv "$base_ref")"
  if [ -z "$filters" ]; then
    warn "No changed files remain after standard review-scan exclusions; skipping Gito review."
    return
  fi
  mkdir -p "$out_dir"
  run_gito_command "$label" gito review --vs "$base_ref" --filter "$filters" --out "$out_dir"
  record_status "$label" "$?"
}

run_custom_tool() {
  local tool="$1"
  local command="$2"
  run_command "Local review: $tool" bash -c "$command"
}

raw_tools="${SD_AI_COMMAND_PACK_REVIEW_LOCAL_TOOLS:-prism gito}"
while [ "$#" -gt 0 ]; do
  case "$1" in
    --all|--codebase|--full|--full-codebase)
      REVIEW_LOCAL_SCOPE="all"
      shift
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    --list-tools)
      list_tools
      exit 0
      ;;
    --diff|--changed)
      REVIEW_LOCAL_SCOPE="diff"
      shift
      ;;
    --scope=*)
      REVIEW_LOCAL_SCOPE="${1#--scope=}"
      shift
      ;;
    --scope)
      if [ "$#" -lt 2 ]; then
        warn "--scope requires a value: diff or all."
        exit 2
      fi
      REVIEW_LOCAL_SCOPE="$2"
      shift 2
      ;;
    --)
      shift
      if [ "$#" -gt 0 ]; then
        raw_tools="$*"
      fi
      break
      ;;
    *)
      raw_tools="$*"
      break
      ;;
  esac
done

requested_review_scope="$REVIEW_LOCAL_SCOPE"
if ! REVIEW_LOCAL_SCOPE="$(normalize_review_scope "$requested_review_scope")"; then
  warn "Unsupported local review scope '$requested_review_scope'. Use 'diff' or 'all'."
  exit 2
fi

raw_tools="${raw_tools//,/ }"
read -r -a REQUESTED_TOOLS <<< "$raw_tools"

if [ "${#REQUESTED_TOOLS[@]}" -eq 0 ]; then
  warn "No local review tools requested."
  exit 2
fi

NEED_PRISM=0
NEED_GITO=0
CUSTOM_TOOL_NAMES=()
CUSTOM_TOOL_COMMANDS=()

for raw_tool in "${REQUESTED_TOOLS[@]}"; do
  [ -n "$raw_tool" ] || continue
  if ! valid_tool_name "$raw_tool"; then
    warn "Unsupported local review tool name '$raw_tool'. Tool names may only contain letters, numbers, dots, underscores, or hyphens."
    OVERALL_STATUS=2
    continue
  fi
  tool="$(printf '%s' "$raw_tool" | tr '[:upper:]' '[:lower:]')"
  case "$tool" in
    all|default)
      NEED_PRISM=1
      NEED_GITO=1
      continue
      ;;
  esac

  configured_command="$(configured_command_for_tool "$tool")"
  if [ -n "$configured_command" ]; then
    CUSTOM_TOOL_NAMES+=("$tool")
    CUSTOM_TOOL_COMMANDS+=("$configured_command")
    continue
  fi

  case "$tool" in
    prism)
      NEED_PRISM=1
      ;;
    gito)
      NEED_GITO=1
      ;;
    *)
      key="$(tool_env_key "$tool")"
      if [ "$REVIEW_LOCAL_SCOPE" = "all" ]; then
        warn "No command configured for local review tool '$tool'. Set SD_AI_COMMAND_PACK_REVIEW_LOCAL_ALL_${key}_COMMAND or SD_AI_COMMAND_PACK_REVIEW_LOCAL_${key}_COMMAND."
      else
        warn "No command configured for local review tool '$tool'. Set SD_AI_COMMAND_PACK_REVIEW_LOCAL_${key}_COMMAND."
      fi
      OVERALL_STATUS=2
      ;;
  esac
done

section "Local review scope: $(review_scope_label)"

if [ "$NEED_PRISM" -eq 1 ]; then
  run_prism_reviews
fi

if [ "$NEED_GITO" -eq 1 ]; then
  run_gito_review
fi

for index in "${!CUSTOM_TOOL_NAMES[@]}"; do
  run_custom_tool "${CUSTOM_TOOL_NAMES[$index]}" "${CUSTOM_TOOL_COMMANDS[$index]}"
done

if [ "$OVERALL_STATUS" -eq 0 ]; then
  printf '\nLocal review providers completed without reported findings (%s scope).\n' "$(review_scope_label)"
else
  printf '\nLocal review providers reported findings, failures, or missing configuration (%s scope).\n' "$(review_scope_label)" >&2
fi

exit "$OVERALL_STATUS"
