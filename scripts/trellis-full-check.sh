#!/usr/bin/env bash
set -euo pipefail

section() {
  printf '\n==> %s\n' "$*"
}

warn() {
  printf 'warning: %s\n' "$*" >&2
}

is_enabled() {
  case "${1:-}" in
    1|true|TRUE|yes|YES|required) return 0 ;;
    *) return 1 ;;
  esac
}

is_disabled() {
  case "${1:-}" in
    0|false|FALSE|no|NO|skip|none) return 0 ;;
    *) return 1 ;;
  esac
}

have() {
  command -v "$1" >/dev/null 2>&1
}

run() {
  section "$1"
  shift
  "$@"
}

package_has_script() {
  local script_name="$1"
  have node || return 1
  node -e '
const fs = require("fs");
const scriptName = process.argv[1];
const pkg = JSON.parse(fs.readFileSync("package.json", "utf8"));
process.exit(pkg.scripts && Object.prototype.hasOwnProperty.call(pkg.scripts, scriptName) ? 0 : 1);
' "$script_name" >/dev/null 2>&1
}

detect_merge_base() {
  local base_ref="${TRELLIS_FULL_CHECK_BASE_REF:-origin/main}"
  git merge-base "$base_ref" HEAD 2>/dev/null || true
}

build_prism_args() {
  PRISM_ARGS=()

  local fail_on="${TRELLIS_FULL_CHECK_PRISM_FAIL_ON:-high}"
  if [ -n "$fail_on" ]; then
    PRISM_ARGS+=(--fail-on "$fail_on")
  fi

  local max_findings="${TRELLIS_FULL_CHECK_PRISM_MAX_FINDINGS:-}"
  if [ -n "$max_findings" ]; then
    PRISM_ARGS+=(--max-findings "$max_findings")
  fi

  local rules="${TRELLIS_FULL_CHECK_PRISM_RULES:-}"
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
}

run_prism_command() {
  local label="$1"
  shift
  local mode="${TRELLIS_FULL_CHECK_PRISM:-auto}"
  PRISM_ARGS=()
  build_prism_args

  section "$label"
  set +e
  prism "$@" "${PRISM_ARGS[@]}"
  local status=$?
  set -e

  case "$status" in
    0)
      return 0
      ;;
    1)
      printf 'Prism found findings at or above the configured threshold.\n' >&2
      exit 1
      ;;
    3)
      if [ "$mode" = "required" ]; then
        printf 'Prism is required but provider authentication/configuration failed.\n' >&2
        exit 3
      fi
      warn "Prism authentication/configuration failed; continuing because Prism is optional by default."
      return 0
      ;;
    *)
      printf 'Prism failed with exit code %s.\n' "$status" >&2
      exit "$status"
      ;;
  esac
}

run_prism_reviews() {
  local mode="${TRELLIS_FULL_CHECK_PRISM:-auto}"
  if is_disabled "$mode"; then
    warn "Skipping Prism review because TRELLIS_FULL_CHECK_PRISM=$mode."
    return 0
  fi
  if ! have prism; then
    if [ "$mode" = "required" ]; then
      printf 'Prism is required but not found on PATH.\n' >&2
      exit 127
    fi
    warn "Prism not found on PATH; skipping local AI review."
    return 0
  fi

  if ! git diff --quiet --; then
    run_prism_command "Prism review: unstaged changes" review unstaged
  fi

  if ! git diff --cached --quiet --; then
    run_prism_command "Prism review: staged changes" review staged
  fi

  local merge_base
  merge_base="$(detect_merge_base)"
  if [ -z "$merge_base" ]; then
    warn "Could not resolve merge base for ${TRELLIS_FULL_CHECK_BASE_REF:-origin/main}; skipping committed branch review."
    return 0
  fi

  if git diff --quiet "$merge_base"..HEAD --; then
    warn "No committed branch diff since $merge_base; skipping Prism range review."
    return 0
  fi

  run_prism_command "Prism review: committed branch diff" review range "$merge_base..HEAD"
}

run_gito_review() {
  local mode="${TRELLIS_FULL_CHECK_GITO:-0}"
  if ! is_enabled "$mode"; then
    warn "Skipping Gito review. Set TRELLIS_FULL_CHECK_GITO=1 to enable it."
    return 0
  fi
  if ! have gito; then
    if [ "$mode" = "required" ]; then
      printf 'Gito is required but not found on PATH.\n' >&2
      exit 127
    fi
    warn "Gito not found on PATH; skipping Gito review."
    return 0
  fi

  local base_ref="${TRELLIS_FULL_CHECK_GITO_BASE_REF:-${TRELLIS_FULL_CHECK_BASE_REF:-origin/main}}"
  local out_dir="${TRELLIS_FULL_CHECK_GITO_OUT_DIR:-.build/review/gito}"
  mkdir -p "$out_dir"

  run "Gito review" gito review --vs "$base_ref" --out "$out_dir"
}

main() {
  section "Trellis full check"
  git status -sb

  run "Whitespace check: unstaged diff" git diff --check
  run "Whitespace check: staged diff" git diff --cached --check

  if [ -f "package.json" ] && ! is_enabled "${TRELLIS_FULL_CHECK_SKIP_NPM:-0}"; then
    local runner="${TRELLIS_FULL_CHECK_PACKAGE_RUNNER:-npm}"
    local scripts="${TRELLIS_FULL_CHECK_NPM_SCRIPTS:-typecheck lint test:unit test:integration build test:e2e}"

    if ! have "$runner"; then
      warn "$runner not found on PATH; skipping package scripts."
    elif ! have node; then
      warn "node not found on PATH; cannot inspect package.json scripts; skipping package scripts."
    else
      local script_name
      for script_name in $scripts; do
        if package_has_script "$script_name"; then
          run "Package script: $script_name" "$runner" run "$script_name"
        else
          warn "package.json has no script named $script_name; skipping."
        fi
      done
    fi
  else
    warn "No package.json found, or npm checks disabled; skipping package scripts."
  fi

  run_prism_reviews
  run_gito_review

  section "Full check complete"
}

main "$@"
