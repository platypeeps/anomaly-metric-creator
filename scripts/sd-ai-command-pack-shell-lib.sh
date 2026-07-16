#!/usr/bin/env bash

# Shared helpers for shipped sd-ai-command-pack shell entry points. Callers
# define REPO_ROOT before calling helpers that need it; helpers that emit
# warnings expect warn(), and run_gito_command expects section(), gito_max_attempts(),
# gito_initial_retry_delay(), gito_max_retry_delay(), and
# gito_command_timeout_seconds(). Callers that define the optional
# REVIEW_LOCAL_TEMP_FILES array must install an EXIT/INT/TERM cleanup trap;
# run_gito_command registers each temporary output file in that array.

# sd-ai-command-pack review-scan-excludes start
REVIEW_SCAN_EXCLUDE_DIRS=(
  ".agent"
  ".agents"
  ".claude"
  ".codex"
  ".codebuddy"
  ".cursor"
  ".devin"
  ".factory"
  ".gemini"
  ".github"
  ".kiro"
  ".kilocode"
  ".opencode"
  ".pi"
  ".qoder"
  ".reasonix"
  ".trae"
  ".zcode"
  ".build"
  ".git"
  ".pytest_cache"
  ".obsidian-kb"
  ".trellis"
  ".ruff_cache"
  ".venv"
  ".sd-ai-command-pack"
  "node_modules"
)
# sd-ai-command-pack review-scan-excludes end

positive_int_or_default() {
  local value="$1"
  local fallback="$2"
  case "$value" in
    ''|*[!0-9]*|0) printf '%s' "$fallback" ;;
    *) printf '%s' "$value" ;;
  esac
}

nonnegative_int_or_default() {
  local value="$1"
  local fallback="$2"
  case "$value" in
    ''|*[!0-9]*) printf '%s' "$fallback" ;;
    *) printf '%s' "$value" ;;
  esac
}

have() {
  command -v "$1" >/dev/null 2>&1
}

run_command_with_timeout() {
  local timeout_seconds="$1"
  shift
  if [ "$timeout_seconds" -eq 0 ]; then
    "$@"
    return
  fi
  if ! command -v python3 >/dev/null 2>&1; then
    warn "python3 not found; command timeout is disabled for $1."
    "$@"
    return
  fi

  python3 - "$timeout_seconds" "$@" <<'PY'
import os
import signal
import subprocess
import sys

timeout_seconds = int(sys.argv[1])
command = sys.argv[2:]
process = subprocess.Popen(command, start_new_session=True)


def terminate_process_group(sig: signal.Signals) -> None:
    try:
        os.killpg(process.pid, sig)
    except ProcessLookupError:
        pass


try:
    returncode = process.wait(timeout=timeout_seconds)
except subprocess.TimeoutExpired:
    print(
        f"command timed out after {timeout_seconds}s: {command[0]}",
        file=sys.stderr,
    )
    terminate_process_group(signal.SIGTERM)
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        terminate_process_group(signal.SIGKILL)
        process.wait()
    raise SystemExit(124)
except KeyboardInterrupt:
    terminate_process_group(signal.SIGINT)
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        terminate_process_group(signal.SIGKILL)
        process.wait()
    raise SystemExit(130)

if returncode < 0:
    raise SystemExit(128 - returncode)
raise SystemExit(returncode)
PY
}

load_gito_pack_env() {
  local env_file="$REPO_ROOT/.gito/sd-ai-command-pack.env"
  [ -f "$env_file" ] || return 0

  local line value
  while IFS= read -r line || [ -n "$line" ]; do
    line="${line%$'\r'}"
    case "$line" in
      ''|'#'*)
        continue
        ;;
      export\ *)
        line="${line#export }"
        ;;
    esac

    case "$line" in
      MAX_CONCURRENT_TASKS=*)
        value="${line#MAX_CONCURRENT_TASKS=}"
        case "$value" in
          ''|*[!0-9]*|0)
            warn "Ignoring invalid MAX_CONCURRENT_TASKS in $env_file."
            ;;
          *)
            if [ -z "${MAX_CONCURRENT_TASKS:-}" ]; then
              export MAX_CONCURRENT_TASKS="$value"
            fi
            ;;
        esac
        ;;
    esac
  done <"$env_file"
}

prepare_gito_uv_env() {
  local default_tmp="${TMPDIR:-/tmp}"
  local cache_root="${XDG_CACHE_HOME:-}"
  if [ -n "$cache_root" ]; then
    cache_root="${cache_root%/}/sd-ai-command-pack"
  else
    cache_root="${default_tmp%/}/sd-ai-command-pack-${UID:-unknown}"
  fi
  if [ -z "${UV_CACHE_DIR:-}" ]; then
    export UV_CACHE_DIR="${SD_AI_COMMAND_PACK_REVIEW_LOCAL_UV_CACHE_DIR:-$cache_root/uv-cache}"
  fi
  if [ -z "${UV_TOOL_DIR:-}" ]; then
    export UV_TOOL_DIR="${SD_AI_COMMAND_PACK_REVIEW_LOCAL_UV_TOOL_DIR:-$cache_root/uv-tools}"
  fi
  mkdir -p "$UV_CACHE_DIR" "$UV_TOOL_DIR"
}

join_by_comma() {
  local IFS=,
  printf '%s' "$*"
}

path_is_standard_review_scan_excluded() {
  local path="${1#./}"
  local dir
  for dir in "${REVIEW_SCAN_EXCLUDE_DIRS[@]}"; do
    if [ "$path" = "$dir" ] || [[ "$path" == "$dir/"* ]]; then
      return 0
    fi
  done
  return 1
}

review_scan_exclude_globs_csv() {
  local globs=()
  local dir
  for dir in "${REVIEW_SCAN_EXCLUDE_DIRS[@]}"; do
    globs+=("$dir" "$dir/**")
  done
  join_by_comma "${globs[@]}"
}

has_ref() {
  local ref="${1:-}"
  if [ -z "$ref" ]; then
    return 1
  fi
  case "$ref" in
    -*) return 1 ;;
  esac
  git rev-parse --verify --quiet "$ref^{commit}" >/dev/null
}

default_review_base_ref() {
  local ref

  ref="$(git symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null || true)"
  if has_ref "$ref"; then
    printf '%s' "$ref"
    return
  fi

  ref="$(git rev-parse --abbrev-ref --symbolic-full-name '@{upstream}' 2>/dev/null || true)"
  if has_ref "$ref"; then
    printf '%s' "$ref"
    return
  fi

  ref="$(
    git for-each-ref --format='%(refname:short)' refs/remotes 2>/dev/null \
      | grep -v '/HEAD$' \
      | LC_ALL=C sort \
      | while IFS= read -r candidate; do
          if has_ref "$candidate"; then
            printf '%s\n' "$candidate"
            break
          fi
        done \
      || true
  )"
  if has_ref "$ref"; then
    printf '%s' "$ref"
    return
  fi

  printf 'HEAD'
}

configured_review_base_ref() {
  local var_name="$1"
  local ref="${!var_name:-}"
  if [ -z "$ref" ]; then
    return 1
  fi
  if has_ref "$ref"; then
    printf '%s' "$ref"
    return 0
  fi
  warn "$var_name=$ref does not resolve to a commit; falling back to discovered default branch."
  return 1
}

gito_output_indicates_rate_limit() {
  local output_file="$1"
  local recent_output
  recent_output="$(tail -n 200 "$output_file")"
  local status_lines
  status_lines="$(
    printf '%s\n' "$recent_output" \
      | grep -Ei '(^|[^[:alnum:]])(clienterror|apierror|httperror|http status|status code|status|error|exception):?[^0-9]*[0-9]{3}([^0-9]|$)|(^|[^[:alnum:]])[0-9]{3}[[:space:]]+(too many requests|resource exhausted|rate[ -]?limit(ed)?|slow down)([^[:alnum:]]|$)' \
      || true
  )"
  if [ -z "$status_lines" ]; then
    return 1
  fi
  printf '%s\n' "$status_lines" \
    | tail -n 1 \
    | grep -Eiq '(^|[^[:alnum:]])(clienterror|apierror|httperror|http status|status code|status|error|exception):?[^0-9]*429([^0-9]|$)|(^|[^[:alnum:]])429[[:space:]]+(too many requests|resource exhausted|rate[ -]?limit(ed)?|slow down)([^[:alnum:]]|$)'
}

register_sd_ai_command_pack_temp_file() {
  local file="$1"
  local declaration
  declaration="$(declare -p REVIEW_LOCAL_TEMP_FILES 2>/dev/null || true)"
  case "$declaration" in
    declare\ -a*REVIEW_LOCAL_TEMP_FILES=*)
      REVIEW_LOCAL_TEMP_FILES+=("$file")
      ;;
  esac
}

run_gito_command() {
  local label="$1"
  shift
  local max_attempts
  max_attempts="$(gito_max_attempts)"
  local delay
  delay="$(gito_initial_retry_delay)"
  local max_delay
  max_delay="$(gito_max_retry_delay)"
  if [ "$max_delay" -lt "$delay" ]; then
    max_delay="$delay"
  fi

  local attempt=1
  local output_file
  while :; do
    section "$label"
    if [ "$max_attempts" -gt 1 ]; then
      printf 'Gito attempt %s/%s\n' "$attempt" "$max_attempts"
    fi

    output_file="$(mktemp "${TMPDIR:-/tmp}/sd-ai-command-pack-gito.XXXXXX")"
    register_sd_ai_command_pack_temp_file "$output_file"
    local had_errexit=0
    case "$-" in
      *e*) had_errexit=1 ;;
    esac
    set +e
    local timeout_seconds
    timeout_seconds="$(gito_command_timeout_seconds)"
    run_command_with_timeout "$timeout_seconds" "$@" >"$output_file" 2>&1
    local status=$?
    if [ "$had_errexit" -eq 1 ]; then
      set -e
    else
      set +e
    fi
    cat "$output_file"

    if [ "$status" -eq 124 ]; then
      warn "$label timed out after ${timeout_seconds}s."
      rm -f "$output_file"
      return 124
    fi

    if [ "$status" -eq 0 ]; then
      rm -f "$output_file"
      return 0
    fi

    if [ "$attempt" -ge "$max_attempts" ] || ! gito_output_indicates_rate_limit "$output_file"; then
      rm -f "$output_file"
      return "$status"
    fi

    rm -f "$output_file"
    warn "Gito appears rate-limited; retrying in ${delay}s after HTTP 429 / slow-down response."
    if [ "$delay" -gt 0 ]; then
      sleep "$delay"
    fi
    attempt=$((attempt + 1))
    delay=$((delay * 2))
    if [ "$delay" -gt "$max_delay" ]; then
      delay="$max_delay"
    fi
  done
}
