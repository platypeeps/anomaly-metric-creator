#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
# errexit does not help here: bash's `cd ""` is a silent success, so an
# empty root (failed resolution above) must be rejected explicitly.
if [ -z "$REPO_ROOT" ] || ! cd "$REPO_ROOT"; then
  printf 'sd-ai-command-pack-full-check: cannot resolve repository root\n' >&2
  exit 1
fi

# sd-ai-command-pack review-scan-excludes start
REVIEW_SCAN_EXCLUDE_DIRS=(
  ".github"
  ".claude"
  ".codex"
  ".gemini"
  ".opencode"
  ".agents"
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

positive_int_or_default() {
  local value="$1"
  local fallback="$2"
  case "$value" in
    ''|*[!0-9]*|0) printf '%s' "$fallback" ;;
    *) printf '%s' "$value" ;;
  esac
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

gito_output_indicates_rate_limit() {
  local output_file="$1"
  local recent_output
  recent_output="$(tail -n 200 "$output_file")"
  local status_lines
  status_lines="$(
    printf '%s\n' "$recent_output" \
      | grep -Ei '(^|[^[:alnum:]])(clienterror|apierror|httperror|http status|status code|status|error|exception):?[[:space:]]*[0-9]{3}([^0-9]|$)|(^|[^[:alnum:]])[0-9]{3}[[:space:]]+(too many requests|resource exhausted|rate[ -]?limit(ed)?|slow down)([^[:alnum:]]|$)' \
      || true
  )"
  if [ -z "$status_lines" ]; then
    return 1
  fi
  printf '%s\n' "$status_lines" \
    | tail -n 1 \
    | grep -Eiq '(^|[^[:alnum:]])(clienterror|apierror|httperror|http status|status code|status|error|exception):?[[:space:]]*429([^0-9]|$)|(^|[^[:alnum:]])429[[:space:]]+(too many requests|resource exhausted|rate[ -]?limit(ed)?|slow down)([^[:alnum:]]|$)'
}

gito_max_attempts() {
  positive_int_or_default "${SD_AI_COMMAND_PACK_FULL_CHECK_GITO_MAX_ATTEMPTS:-${SD_AI_COMMAND_PACK_REVIEW_LOCAL_GITO_MAX_ATTEMPTS:-2}}" 2
}

gito_initial_retry_delay() {
  positive_int_or_default "${SD_AI_COMMAND_PACK_FULL_CHECK_GITO_RETRY_DELAY_SECONDS:-${SD_AI_COMMAND_PACK_REVIEW_LOCAL_GITO_RETRY_DELAY_SECONDS:-30}}" 30
}

gito_max_retry_delay() {
  positive_int_or_default "${SD_AI_COMMAND_PACK_FULL_CHECK_GITO_RETRY_MAX_DELAY_SECONDS:-${SD_AI_COMMAND_PACK_REVIEW_LOCAL_GITO_RETRY_MAX_DELAY_SECONDS:-120}}" 120
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
    set +e
    "$@" >"$output_file" 2>&1
    local status=$?
    set -e
    cat "$output_file"

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

package_has_script() {
  local script_name="$1"
  have node || return 1
  SCRIPT_NAME="$script_name" node -e '
const fs = require("fs");
const scriptName = process.env.SCRIPT_NAME;
const pkg = JSON.parse(fs.readFileSync("package.json", "utf8"));
process.exit(pkg.scripts && Object.prototype.hasOwnProperty.call(pkg.scripts, scriptName) ? 0 : 1);
' >/dev/null 2>&1
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

full_check_base_ref() {
  if configured_review_base_ref SD_AI_COMMAND_PACK_FULL_CHECK_BASE_REF; then
    return
  fi
  default_review_base_ref
}

full_check_gito_base_ref() {
  if configured_review_base_ref SD_AI_COMMAND_PACK_FULL_CHECK_GITO_BASE_REF; then
    return
  fi
  full_check_base_ref
}

detect_merge_base() {
  local base_ref
  base_ref="$(full_check_base_ref)"
  git merge-base "$base_ref" HEAD 2>/dev/null || true
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

collect_reviewable_changed_paths() {
  local base_ref="$1"
  local paths_file
  paths_file="$(mktemp "${TMPDIR:-/tmp}/sd-ai-command-pack-review-paths.XXXXXX")"
  : >"$paths_file"

  if git rev-parse --verify --quiet "$base_ref^{commit}" >/dev/null; then
    git diff --name-only "$base_ref"...HEAD >>"$paths_file"
  else
    warn "Could not resolve $base_ref; Gito review filter will use local changes only."
  fi

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

review_filter_pattern_for_path() {
  local path="$1"
  printf '%s\n' "$path"
}

review_filter_csv_from_paths() {
  local patterns_file
  patterns_file="$(mktemp "${TMPDIR:-/tmp}/sd-ai-command-pack-review-filters.XXXXXX")"
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
  join_by_comma "${patterns[@]}"
}

reviewable_changed_filter_csv() {
  local base_ref="$1"
  collect_reviewable_changed_paths "$base_ref" | review_filter_csv_from_paths
}

build_prism_args() {
  PRISM_ARGS=()

  local fail_on="${SD_AI_COMMAND_PACK_FULL_CHECK_PRISM_FAIL_ON:-high}"
  if [ -n "$fail_on" ]; then
    PRISM_ARGS+=(--fail-on "$fail_on")
  fi

  local max_findings="${SD_AI_COMMAND_PACK_FULL_CHECK_PRISM_MAX_FINDINGS:-}"
  if [ -n "$max_findings" ]; then
    PRISM_ARGS+=(--max-findings "$max_findings")
  fi

  local rules="${SD_AI_COMMAND_PACK_FULL_CHECK_PRISM_RULES:-}"
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
  local configured_excludes="${SD_AI_COMMAND_PACK_FULL_CHECK_PRISM_EXCLUDE:-}"
  if [ -n "$configured_excludes" ]; then
    excludes="$excludes,$configured_excludes"
  fi
  PRISM_ARGS+=(--exclude "$excludes")
}

run_prism_command() {
  local label="$1"
  shift
  local mode="${SD_AI_COMMAND_PACK_FULL_CHECK_PRISM:-auto}"
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
    3|4)
      local reason="provider authentication/configuration"
      if [ "$status" = "4" ]; then
        reason="provider/model configuration"
      fi
      if [ "$mode" = "required" ]; then
        printf 'Prism is required but %s failed with exit code %s.\n' "$reason" "$status" >&2
        exit "$status"
      fi
      warn "Prism $reason failed with exit code $status; continuing because Prism is optional by default."
      return 0
      ;;
    *)
      printf 'Prism failed with exit code %s.\n' "$status" >&2
      exit "$status"
      ;;
  esac
}

run_prism_reviews() {
  local mode="${SD_AI_COMMAND_PACK_FULL_CHECK_PRISM:-auto}"
  if is_disabled "$mode"; then
    warn "Skipping Prism review because SD_AI_COMMAND_PACK_FULL_CHECK_PRISM=$mode."
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
    warn "Could not resolve merge base for $(full_check_base_ref); skipping committed branch review."
    return 0
  fi

  if git diff --quiet "$merge_base"..HEAD --; then
    warn "No committed branch diff since $merge_base; skipping Prism range review."
    return 0
  fi

  run_prism_command "Prism review: committed branch diff" review range "$merge_base..HEAD"
}

run_gito_review() {
  local mode="${SD_AI_COMMAND_PACK_FULL_CHECK_GITO:-0}"
  if ! is_enabled "$mode"; then
    warn "Skipping Gito review. Set SD_AI_COMMAND_PACK_FULL_CHECK_GITO=1 to enable it."
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

  load_gito_pack_env

  local base_ref
  base_ref="$(full_check_gito_base_ref)"
  local out_dir="${SD_AI_COMMAND_PACK_FULL_CHECK_GITO_OUT_DIR:-.build/review/gito}"
  local filters
  filters="$(reviewable_changed_filter_csv "$base_ref")"
  if [ -z "$filters" ]; then
    warn "No changed files remain after standard review-scan exclusions; skipping Gito review."
    return 0
  fi
  mkdir -p "$out_dir"

  run_gito_command "Gito review" gito review --vs "$base_ref" --filter "$filters" --out "$out_dir"
}

run_sd_ai_command_pack_scope_check() {
  local script="scripts/sd-ai-command-pack-review-scope.sh"
  if [ ! -f "$script" ]; then
    warn "$script not found; skipping tooling/generated review-scope check."
    return 0
  fi

  run "SD AI command pack tooling/generated scope check" bash "$script"
}

run_sd_ai_command_pack_install_audit() {
  local mode="${SD_AI_COMMAND_PACK_INSTALL_AUDIT:-1}"
  local script="scripts/sd-ai-command-pack-install-audit.py"

  if is_disabled "$mode"; then
    warn "Skipping install audit because SD_AI_COMMAND_PACK_INSTALL_AUDIT=$mode."
    return 0
  fi

  if [ ! -f "$script" ]; then
    if [ "$mode" = "required" ]; then
      printf 'Install audit is required but %s is missing.\n' "$script" >&2
      exit 127
    fi
    warn "$script not found; skipping install audit."
    return 0
  fi

  if ! have python3; then
    if [ "$mode" = "required" ]; then
      printf 'Install audit is required but python3 is not found on PATH.\n' >&2
      exit 127
    fi
    warn "python3 not found on PATH; skipping install audit."
    return 0
  fi

  run "SD AI command pack install audit" python3 "$script"
}

run_pack_source_drift_gates() {
  # Deterministic pre-PR gates that only apply inside the sd-ai-command-pack
  # source repository itself: every tracked manifest target must match its
  # templates/ twin, and every pack env var read by shipped scripts must be
  # documented in the installed usage guide.
  local mode="${SD_AI_COMMAND_PACK_FULL_CHECK_PACK_DRIFT:-auto}"

  if is_disabled "$mode"; then
    warn "Skipping pack source drift gates because SD_AI_COMMAND_PACK_FULL_CHECK_PACK_DRIFT=$mode."
    return 0
  fi
  if [ ! -f "install.py" ] || [ ! -f "manifest.json" ] || [ ! -d "templates" ]; then
    return 0
  fi
  if ! have python3; then
    warn "python3 not found on PATH; skipping pack source drift gates."
    return 0
  fi

  section "Pack source drift gates: template twins and env-var docs"
  python3 - <<'PACK_SOURCE_DRIFT_GATES'
import json
import re
import sys
from pathlib import Path

errors = []

manifest = json.loads(Path("manifest.json").read_text(encoding="utf-8"))
compared = 0
for item in manifest.get("files", []):
    if item.get("kind") == "managed-block":
        continue
    source = Path(str(item["source"]))
    target = Path(str(item["target"]))
    if not target.exists():
        continue
    compared += 1
    if source.read_bytes() != target.read_bytes():
        errors.append(f"template drift: {target} differs from {source}")
print(f"template twin pairs compared: {compared}")

var_re = re.compile(r"SD_AI_COMMAND_PACK_[A-Z0-9_]+")
exempt = {
    # Internal test hook, intentionally undocumented.
    "SD_AI_COMMAND_PACK_FULL_CHECK_TEST_SOURCE",
    # Legacy rename hint prefixes emitted by the install audit, not env vars.
    "SD_AI_COMMAND_PACK_FULL_CHECK",
    "SD_AI_COMMAND_PACK_HOUSEKEEPING",
}
script_vars = set()
for path in Path("scripts").iterdir():
    if path.suffix in {".sh", ".py", ".mjs"}:
        script_vars |= set(var_re.findall(path.read_text(encoding="utf-8")))
skill_vars = set()
for path in Path("templates/.agents/skills").glob("*/SKILL.md"):
    skill_vars |= set(var_re.findall(path.read_text(encoding="utf-8")))
documented = set(
    var_re.findall(Path("docs/SD_AI_COMMAND_PACK.md").read_text(encoding="utf-8"))
)
for name in sorted(script_vars - documented - exempt):
    errors.append(
        f"undocumented env var: {name} is read by scripts/ but missing "
        "from docs/SD_AI_COMMAND_PACK.md"
    )
for name in sorted(documented - script_vars - skill_vars):
    errors.append(
        f"stale documented env var: {name} is documented but no shipped "
        "script or skill consumes it"
    )
print(f"env vars checked: {len(script_vars)} in scripts, {len(documented)} documented")

if errors:
    for error in errors:
        print(f"error: {error}", file=sys.stderr)
    sys.exit(1)
PACK_SOURCE_DRIFT_GATES
}

run_sd_ai_command_pack_pr_body_scope_check() {
  local mode="${SD_AI_COMMAND_PACK_PR_BODY_SCOPE_CHECK:-auto}"
  local script="scripts/sd-ai-command-pack-pr-body-scope.py"

  if is_disabled "$mode"; then
    warn "Skipping PR-body scope check because SD_AI_COMMAND_PACK_PR_BODY_SCOPE_CHECK=$mode."
    return 0
  fi

  if [ ! -f "$script" ]; then
    if [ "$mode" = "required" ]; then
      printf 'PR-body scope check is required but %s is missing.\n' "$script" >&2
      exit 127
    fi
    warn "$script not found; skipping PR-body scope check."
    return 0
  fi

  if ! have python3; then
    if [ "$mode" = "required" ]; then
      printf 'PR-body scope check is required but python3 is not found on PATH.\n' >&2
      exit 127
    fi
    warn "python3 not found on PATH; skipping PR-body scope check."
    return 0
  fi

  run "SD AI command pack PR-body scope check" python3 "$script"
}

collect_current_changed_paths() {
  local output_file="$1"
  : >"$output_file"

  local base_ref
  base_ref="$(full_check_base_ref)"
  if git rev-parse --verify --quiet "$base_ref^{commit}" >/dev/null; then
    git diff --name-only "$base_ref"...HEAD >>"$output_file"
  else
    warn "Could not resolve $base_ref; CI classification report will use local changes only."
  fi

  git diff --cached --name-only >>"$output_file"
  git diff --name-only >>"$output_file"
  git ls-files --others --exclude-standard >>"$output_file"
  sort -u "$output_file" -o "$output_file"
}

resolve_ci_classifier_script() {
  if [ -f "scripts/classify-ci-changes.sh" ]; then
    printf '%s\n' "scripts/classify-ci-changes.sh"
    return 0
  fi
  if [ -f "scripts/classify_ci_changes.sh" ]; then
    warn "Using legacy scripts/classify_ci_changes.sh; prefer scripts/classify-ci-changes.sh with '-- path...' support."
    printf '%s\n' "scripts/classify_ci_changes.sh"
    return 0
  fi
  return 1
}

run_ci_classification_report() {
  local script
  if ! script="$(resolve_ci_classifier_script)"; then
    warn "No scripts/classify-ci-changes.sh or scripts/classify_ci_changes.sh found; skipping current-diff CI classification report."
    return 0
  fi

  local paths_file
  paths_file="$(mktemp "${TMPDIR:-/tmp}/sd-ai-command-pack-ci-paths.XXXXXX")"
  collect_current_changed_paths "$paths_file"

  local -a changed_paths=()
  local path
  while IFS= read -r path; do
    changed_paths+=("$path")
  done <"$paths_file"

  if [ "${#changed_paths[@]}" -eq 0 ]; then
    rm -f "$paths_file"
    warn "No current changed paths; skipping current-diff CI classification report."
    return 0
  fi

  section "CI change classification: current diff"
  printf 'changed_paths=%s\n' "${#changed_paths[@]}"
  local status=0

  if [ "$script" = "scripts/classify_ci_changes.sh" ]; then
    warn "Running legacy $script with a changed-files list. Update to scripts/classify-ci-changes.sh with '-- path...' support before the next pack refresh."
    bash "$script" "$paths_file" || status=$?
  else
    bash "$script" -- "${changed_paths[@]}" || status=$?
  fi

  rm -f "$paths_file"
  return "$status"
}

run_review_preflight() {
  local mode="${SD_AI_COMMAND_PACK_FULL_CHECK_REVIEW_PREFLIGHT:-1}"
  local command="${SD_AI_COMMAND_PACK_FULL_CHECK_REVIEW_PREFLIGHT_COMMAND:-}"
  local script="${SD_AI_COMMAND_PACK_FULL_CHECK_REVIEW_PREFLIGHT_SCRIPT:-scripts/sd-ai-command-pack-review-preflight.mjs}"
  local legacy_script="scripts/check-review-preflight.mjs"

  if is_disabled "$mode"; then
    warn "Skipping review preflight because SD_AI_COMMAND_PACK_FULL_CHECK_REVIEW_PREFLIGHT=$mode."
    return 0
  fi

  if [ -n "$command" ]; then
    run "Review preflight" bash -lc "$command"
    return 0
  fi

  if ! have node; then
    if [ "$mode" = "required" ]; then
      printf 'Review preflight is required but Node.js is not found on PATH.\n' >&2
      exit 127
    fi
    warn "Node.js not found on PATH; JavaScript review preflight is unavailable; skipping review preflight."
    return 0
  fi

  local ran=0
  if [ -f "$script" ]; then
    run "SD AI command pack review preflight" node "$script"
    ran=1
  fi

  if [ -f "$legacy_script" ] && [ "$legacy_script" != "$script" ]; then
    run "Repo-local review preflight" node "$legacy_script"
    ran=1
  fi

  if [ "$ran" -eq 1 ]; then
    return 0
  fi

  if [ "$mode" = "required" ]; then
    printf 'Review preflight is required but no command is configured and neither %s nor %s exists.\n' "$script" "$legacy_script" >&2
    exit 127
  fi

  warn "$script and $legacy_script not found; skipping review preflight."
}

main() {
  section "SD AI command pack full check"
  git status -sb

  run "Whitespace check: unstaged diff" git diff --check
  run "Whitespace check: staged diff" git diff --cached --check
  run_review_preflight
  run_sd_ai_command_pack_install_audit
  run_pack_source_drift_gates
  run_sd_ai_command_pack_scope_check
  run_sd_ai_command_pack_pr_body_scope_check
  run_ci_classification_report

  local skip_package_scripts="${SD_AI_COMMAND_PACK_FULL_CHECK_SKIP_PACKAGE_SCRIPTS:-0}"
  if [ -f "package.json" ] && ! is_enabled "$skip_package_scripts"; then
    local runner="${SD_AI_COMMAND_PACK_FULL_CHECK_PACKAGE_RUNNER:-npm}"
    local scripts="${SD_AI_COMMAND_PACK_FULL_CHECK_PACKAGE_SCRIPTS:-typecheck lint test:unit test:integration build test:e2e}"

    if ! have "$runner"; then
      warn "Package runner $runner not found on PATH; skipping package-script checks."
    elif ! have node; then
      warn "Node.js not found on PATH; cannot inspect package.json scripts; skipping package-script checks."
    else
      local script_name
      # Disable globbing so a script name in the space-separated list cannot be
      # expanded as a filesystem glob; keep ordinary IFS word-splitting.
      set -f
      for script_name in $scripts; do
        if package_has_script "$script_name"; then
          run "Package script: $script_name" "$runner" run "$script_name"
        else
          warn "package.json has no script named $script_name; skipping."
        fi
      done
      set +f
    fi
  else
    warn "No package.json found, or package-script checks disabled; skipping package-script checks."
  fi

  run_prism_reviews
  run_gito_review

  section "Full check complete"
}

if [ "${SD_AI_COMMAND_PACK_FULL_CHECK_TEST_SOURCE:-0}" != "1" ]; then
  main "$@"
fi
