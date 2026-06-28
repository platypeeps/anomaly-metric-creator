#!/usr/bin/env bash
set -euo pipefail

PYTHON_BIN=""
PYTEST_BIN=""
RUFF_BIN=""

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

resolve_python_tools() {
  if [ -n "${TRELLIS_FULL_CHECK_PYTHON:-}" ]; then
    PYTHON_BIN="$TRELLIS_FULL_CHECK_PYTHON"
  elif [ -x ".venv/bin/python" ]; then
    PYTHON_BIN=".venv/bin/python"
  else
    PYTHON_BIN="python3"
  fi

  if ! have "$PYTHON_BIN"; then
    printf 'Python executable not found: %s\n' "$PYTHON_BIN" >&2
    exit 127
  fi

  if [ -n "${TRELLIS_FULL_CHECK_PYTEST:-}" ]; then
    PYTEST_BIN="$TRELLIS_FULL_CHECK_PYTEST"
  elif [ -x ".venv/bin/pytest" ]; then
    PYTEST_BIN=".venv/bin/pytest"
  else
    PYTEST_BIN="$PYTHON_BIN -m pytest"
  fi

  if [ -n "${TRELLIS_FULL_CHECK_RUFF:-}" ]; then
    RUFF_BIN="$TRELLIS_FULL_CHECK_RUFF"
  elif [ -x ".venv/bin/ruff" ]; then
    RUFF_BIN=".venv/bin/ruff"
  else
    RUFF_BIN="$PYTHON_BIN -m ruff"
  fi
}

run_pytest() {
  # shellcheck disable=SC2086
  $PYTEST_BIN "$@"
}

run_ruff() {
  # shellcheck disable=SC2086
  $RUFF_BIN "$@"
}

run_module_cli_help() {
  PYTHONPATH=src "$PYTHON_BIN" -m anomaly_metric_creator.cli --help >/dev/null
}

run_amc_console_help() {
  .venv/bin/amc --help >/dev/null
}

run_compat_console_help() {
  .venv/bin/anomaly-metric-creator --help >/dev/null
}

run_console_script_smoke() {
  if [ -x ".venv/bin/amc" ] && [ -x ".venv/bin/anomaly-metric-creator" ]; then
    run "Console script smoke: amc" run_amc_console_help
    run "Console script smoke: anomaly-metric-creator" run_compat_console_help
  else
    warn "Installed console scripts not found in .venv; using module CLI smoke."
    run "Module CLI smoke" run_module_cli_help
  fi
}

run_python_syntax_guard() {
  local files=()
  local path=""

  while IFS= read -r path; do
    files+=("$path")
  done < <(git ls-files src tests tools .codex/hooks .github/copilot/hooks .gemini/hooks | grep -E '\.py$' || true)

  if [ "${#files[@]}" -gt 0 ]; then
    run "Python syntax guard" "$PYTHON_BIN" tools/check_python_syntax.py "${files[@]}"
  else
    warn "No Python files found for syntax guard."
  fi
}

run_workflow_pip_guard() {
  local files=()
  local path=""

  while IFS= read -r path; do
    files+=("$path")
  done < <(git ls-files .github/workflows | grep -E '\.ya?ml$' || true)

  if [ "${#files[@]}" -gt 0 ]; then
    run "Workflow pip guard" "$PYTHON_BIN" tools/check_workflow_pip.py "${files[@]}"
  else
    warn "No GitHub workflow files found for workflow pip guard."
  fi
}

run_trellis_placeholder_guard() {
  local files=()
  local path=""

  while IFS= read -r path; do
    files+=("$path")
  done < <(git ls-files .trellis/tasks .trellis/workspace | grep -E '\.(md|json|jsonl|ya?ml|toml)$' || true)

  if [ "${#files[@]}" -gt 0 ]; then
    run "Trellis artifact guard" "$PYTHON_BIN" tools/check_trellis_placeholders.py "${files[@]}"
  else
    warn "No Trellis task/workspace files found for artifact guard."
  fi
}

run_trace_payload_guard() {
  local files=(
    "src/anomaly_metric_creator/server_traces.py"
    "src/anomaly_metric_creator/trace_bundle.py"
  )
  run "Trace payload anti-pattern guard" "$PYTHON_BIN" tools/check_trace_payload_antipatterns.py "${files[@]}"
}

run_ci_review_contract_guard() {
  run "CI/review cadence contract guard" "$PYTHON_BIN" tools/check_ci_review_contract.py
}

run_classifier_smoke() {
  local tmp_file
  tmp_file="$(mktemp "${TMPDIR:-/tmp}/amc-ci-classifier.XXXXXX")"
  printf '%s\n' \
    'docs/REVIEW_PATTERNS.md' \
    '.github/prompts/review-pr.prompt.md' \
    'scripts/trellis-full-check.sh' > "$tmp_file"
  run "CI change classifier smoke" bash scripts/classify_ci_changes.sh "$tmp_file"
  rm -f "$tmp_file"
}

run_review_churn_tests() {
  run "Review-churn lint tests" run_pytest -q \
    tests/test_ci_change_classifier.py \
    tests/test_python_syntax_lint.py \
    tests/test_workflow_pip_lint.py \
    tests/test_ci_review_contract.py \
    tests/test_ruff_lockstep_lint.py \
    tests/test_trellis_placeholder_lint.py \
    tests/test_trace_payload_antipatterns_lint.py
}

run_focused_server_tests() {
  run "Focused server compatibility tests" run_pytest -q tests/test_server.py -k "apply or rollout"
}

run_full_pytest_suite() {
  run "Pytest heavy suite" run_pytest -n 0 -m heavy
  run "Pytest non-heavy suite" run_pytest -n 2 --dist loadfile -m "not heavy"
}

detect_merge_base() {
  local base_ref="${TRELLIS_FULL_CHECK_BASE_REF:-origin/main}"
  git merge-base "$base_ref" HEAD 2>/dev/null || true
}

build_prism_args() {
  PRISM_ARGS=()

  local compare="${TRELLIS_FULL_CHECK_PRISM_COMPARE:-}"
  if [ -n "$compare" ]; then
    PRISM_ARGS+=(--compare "$compare")
  fi

  local provider="${TRELLIS_FULL_CHECK_PRISM_PROVIDER:-}"
  if [ -n "$provider" ]; then
    PRISM_ARGS+=(--provider "$provider")
  fi

  local model="${TRELLIS_FULL_CHECK_PRISM_MODEL:-}"
  if [ -n "$model" ]; then
    PRISM_ARGS+=(--model "$model")
  fi

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
  local retries="${TRELLIS_FULL_CHECK_PRISM_RETRIES:-1}"
  local attempt=1
  local max_attempts=0
  local status=0
  PRISM_ARGS=()
  build_prism_args

  if [[ ! "$retries" =~ ^[0-9]+$ ]]; then
    printf 'error: TRELLIS_FULL_CHECK_PRISM_RETRIES must be a non-negative integer, got: %s\n' "$retries" >&2
    exit 2
  fi
  max_attempts=$((retries + 1))

  section "$label"
  while [ "$attempt" -le "$max_attempts" ]; do
    set +e
    prism "$@" "${PRISM_ARGS[@]}"
    status=$?
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
    esac

    if [ "$attempt" -lt "$max_attempts" ]; then
      warn "Prism failed with exit code $status; retrying because non-finding, non-authentication failures can be transient (attempt $((attempt + 1)) of $max_attempts)."
    fi
    attempt=$((attempt + 1))
  done

  printf 'Prism failed with exit code %s after %s attempt(s).\n' "$status" "$max_attempts" >&2
  exit "$status"
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
  local level="${TRELLIS_FULL_CHECK_LEVEL:-full}"

  section "Trellis full check"
  git status -sb
  resolve_python_tools

  case "$level" in
    quick|full)
      ;;
    *)
      printf 'error: TRELLIS_FULL_CHECK_LEVEL must be quick or full, got: %s\n' "$level" >&2
      exit 2
      ;;
  esac

  run "Whitespace check: unstaged diff" git diff --check
  run "Whitespace check: staged diff" git diff --cached --check
  run "Review tooling shell syntax" bash -n scripts/classify_ci_changes.sh scripts/trellis-full-check.sh scripts/trellis-housekeeping.sh
  run_classifier_smoke
  run_python_syntax_guard
  run_workflow_pip_guard
  run_trellis_placeholder_guard
  run_trace_payload_guard
  run_ci_review_contract_guard
  run "Ruff version lockstep" "$PYTHON_BIN" tools/check_ruff_lockstep.py
  run "Ruff F401 in tests" run_ruff check tests/
  run_console_script_smoke
  run_review_churn_tests
  run_focused_server_tests

  if [ "$level" = "full" ]; then
    run_full_pytest_suite
  else
    warn "Skipping full pytest split because TRELLIS_FULL_CHECK_LEVEL=quick."
  fi

  run_prism_reviews
  run_gito_review

  section "Full check complete"
}

main "$@"
