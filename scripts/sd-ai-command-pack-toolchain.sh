#!/usr/bin/env bash

# Resolve and verify the repository toolchain without installing dependencies or
# executing inferred project checks. Bash 3.2 compatibility is intentional.

set -u

usage() {
  cat >&2 <<'EOF'
Usage:
  sd-ai-command-pack-toolchain.sh doctor [--json]
  sd-ai-command-pack-toolchain.sh python [--require-module NAME]...
  sd-ai-command-pack-toolchain.sh run-python [--require-module NAME]... -- ARGS...
EOF
  exit 2
}

fail() {
  printf 'sd-ai-command-pack toolchain: %s\n' "$1" >&2
  exit "${2:-1}"
}

repo_root() {
  if [ -n "${SD_AI_COMMAND_PACK_REPO_ROOT:-}" ]; then
    printf '%s\n' "$SD_AI_COMMAND_PACK_REPO_ROOT"
    return
  fi
  git rev-parse --show-toplevel 2>/dev/null || pwd -P
}

REPO_ROOT="$(repo_root)"
PYTHON_COMMAND=""
PYTHON_SOURCE=""
PYTHON_VERSION=""
REQUIRED_MODULES=()
PROJECT_CHECK_CANDIDATES=()
RUN_PYTHON_SEPARATOR=0

PYTHON_PROBE_CODE='
import importlib.util
import sys

if sys.version_info < (3, 10):
    raise SystemExit(10)
missing = []
for name in sys.argv[1:]:
    try:
        available = importlib.util.find_spec(name) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        available = False
    if not available:
        missing.append(name)
if missing:
    print(",".join(missing))
    raise SystemExit(11)
print(".".join(str(part) for part in sys.version_info[:3]))
'

select_executable() {
  local candidate="$1"
  local source="$2"
  local resolved=""

  if [ -x "$candidate" ]; then
    resolved="$candidate"
  elif resolved="$(command -v "$candidate" 2>/dev/null)" && [ -n "$resolved" ]; then
    :
  else
    return 1
  fi

  PYTHON_COMMAND="$resolved"
  PYTHON_SOURCE="$source"
  return 0
}

select_python() {
  local platform prefixes old_ifs prefix

  if [ -n "${SD_AI_COMMAND_PACK_PYTHON:-}" ]; then
    select_executable "$SD_AI_COMMAND_PACK_PYTHON" "SD_AI_COMMAND_PACK_PYTHON" \
      || fail "SD_AI_COMMAND_PACK_PYTHON does not name an executable: $SD_AI_COMMAND_PACK_PYTHON" 4
    return
  fi

  if [ -d "$REPO_ROOT/.venv" ]; then
    if select_executable "$REPO_ROOT/.venv/bin/python" "repo .venv"; then
      return
    fi
    if select_executable "$REPO_ROOT/.venv/Scripts/python.exe" "repo .venv"; then
      return
    fi
    fail "repo .venv exists but has no executable Python; run 'make setup' to rebuild it" 4
  fi

  if [ -n "${VIRTUAL_ENV:-}" ]; then
    if select_executable "$VIRTUAL_ENV/bin/python" "active VIRTUAL_ENV"; then
      return
    fi
    if select_executable "$VIRTUAL_ENV/Scripts/python.exe" "active VIRTUAL_ENV"; then
      return
    fi
    fail "VIRTUAL_ENV is set but has no executable Python: $VIRTUAL_ENV" 4
  fi

  platform="${SD_AI_COMMAND_PACK_TOOLCHAIN_PLATFORM:-$(uname -s 2>/dev/null || printf unknown)}"
  if [ "$platform" = "Darwin" ]; then
    prefixes="${SD_AI_COMMAND_PACK_TOOLCHAIN_HOMEBREW_PREFIXES:-/opt/homebrew:/usr/local}"
    old_ifs="$IFS"
    IFS=:
    for prefix in $prefixes; do
      if [ -n "$prefix" ] && select_executable "$prefix/bin/python3.13" "Homebrew Python 3.13"; then
        IFS="$old_ifs"
        return
      fi
    done
    IFS="$old_ifs"
  fi

  if select_executable python3 "PATH python3"; then
    return
  fi

  fail "no supported Python interpreter found; install Homebrew Python 3.13 and run 'make setup'" 3
}

valid_module_name() {
  local module_name="$1"
  local old_ifs segment
  [ -n "$module_name" ] || return 1
  old_ifs="$IFS"
  IFS=.
  for segment in $module_name; do
    case "$segment" in
      ''|[0-9]*|*[!A-Za-z0-9_]*) IFS="$old_ifs"; return 1 ;;
    esac
  done
  IFS="$old_ifs"
  case "$module_name" in
    .*|*.|*..*) return 1 ;;
  esac
  return 0
}

valid_python_version() {
  local version="$1" remainder minor patch
  case "$version" in
    ''|*[!0-9.]*|.*|*.) return 1 ;;
  esac
  remainder="${version#*.}"
  [ "$remainder" != "$version" ] || return 1
  minor="${remainder%%.*}"
  [ -n "$minor" ] || return 1
  patch="${remainder#*.}"
  [ "$patch" != "$remainder" ] || return 1
  case "$patch" in
    ''|*.*) return 1 ;;
  esac
  return 0
}

verify_python() {
  local output status
  if [ "${#REQUIRED_MODULES[@]}" -eq 0 ]; then
    output="$(PYTHONDONTWRITEBYTECODE=1 \
      "$PYTHON_COMMAND" -c "$PYTHON_PROBE_CODE" 2>&1)"
    status=$?
  else
    output="$(PYTHONDONTWRITEBYTECODE=1 \
      "$PYTHON_COMMAND" -c "$PYTHON_PROBE_CODE" \
      "${REQUIRED_MODULES[@]}" 2>&1)"
    status=$?
  fi

  case "$status" in
    0)
      valid_python_version "$output" || \
        fail "selected executable did not report a valid Python version ($PYTHON_COMMAND from $PYTHON_SOURCE): $output; run 'make setup'" 4
      PYTHON_VERSION="$output"
      ;;
    10)
      fail "selected Python is older than 3.10 ($PYTHON_COMMAND from $PYTHON_SOURCE); run 'make setup' with Python 3.13" 4
      ;;
    11)
      fail "selected Python is missing required module(s): $output ($PYTHON_COMMAND from $PYTHON_SOURCE); run 'make setup'" 4
      ;;
    *)
      fail "selected Python failed validation ($PYTHON_COMMAND from $PYTHON_SOURCE): $output; run 'make setup'" 4
      ;;
  esac
}

parse_python_options() {
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --require-module)
        [ "$#" -ge 2 ] || usage
        valid_module_name "$2" || fail "invalid Python module name: $2" 2
        REQUIRED_MODULES+=("$2")
        shift 2
        ;;
      --)
        shift
        RUN_PYTHON_SEPARATOR=1
        RUN_PYTHON_ARGS=("$@")
        return
        ;;
      *) usage ;;
    esac
  done
  RUN_PYTHON_ARGS=()
}

add_project_check_candidate() {
  local candidate="$1"
  local existing
  if [ "${#PROJECT_CHECK_CANDIDATES[@]}" -gt 0 ]; then
    for existing in "${PROJECT_CHECK_CANDIDATES[@]}"; do
      [ "$existing" = "$candidate" ] && return
    done
  fi
  PROJECT_CHECK_CANDIDATES+=("$candidate")
}

discover_make_candidates() {
  local target label
  [ -f "$REPO_ROOT/Makefile" ] || return
  while IFS= read -r target; do
    case "$target" in
      test|lint|audit|check|preflight)
        label="make:$target"
        if [ "$target" = "check" ] \
          && grep -q 'sd-ai-command-pack-full-check\.sh' "$REPO_ROOT/Makefile"; then
          label="$label (recursive)"
        fi
        add_project_check_candidate "$label"
        ;;
    esac
  done <<EOF
$(awk -F: '/^[A-Za-z0-9_.-]+[[:space:]]*:/ { name=$1; sub(/[[:space:]]+$/, "", name); print name }' "$REPO_ROOT/Makefile")
EOF
}

discover_package_candidates() {
  local script_name label
  [ -f "$REPO_ROOT/package.json" ] || return
  while IFS= read -r script_name; do
    [ -n "$script_name" ] || continue
    label="package:${script_name%|recursive}"
    case "$script_name" in
      *'|recursive') label="$label (recursive)" ;;
    esac
    add_project_check_candidate "$label"
  done <<EOF
$(PYTHONDONTWRITEBYTECODE=1 "$PYTHON_COMMAND" - "$REPO_ROOT/package.json" 2>/dev/null <<'PY'
import json
import sys

try:
    payload = json.load(open(sys.argv[1], encoding="utf-8"))
except (OSError, ValueError):
    raise SystemExit(0)
scripts = payload.get("scripts", {})
if isinstance(scripts, dict):
    for name in ("test", "lint", "audit", "check", "preflight"):
        command = scripts.get(name)
        if isinstance(command, str):
            suffix = "|recursive" if "sd-ai-command-pack-full-check.sh" in command else ""
            print(name + suffix)
PY
)
EOF
}

discover_script_candidates() {
  local name label
  for name in preflight-pr.sh test.sh check.sh; do
    if [ -x "$REPO_ROOT/scripts/$name" ]; then
      label="script:$name"
      if grep -q 'sd-ai-command-pack-full-check\.sh' "$REPO_ROOT/scripts/$name"; then
        label="$label (recursive)"
      fi
      add_project_check_candidate "$label"
    fi
  done
}

tool_path_or_empty() {
  command -v "$1" 2>/dev/null || true
}

doctor_json() {
  local project_check="$1"
  shift
  PYTHONDONTWRITEBYTECODE=1 "$PYTHON_COMMAND" - \
    "$PYTHON_COMMAND" "$PYTHON_SOURCE" "$PYTHON_VERSION" "$project_check" \
    "$REPO_ROOT" "${PYTHONPYCACHEPREFIX:-}" "${UV_CACHE_DIR:-}" \
    "${UV_TOOL_DIR:-}" "${RUFF_CACHE_DIR:-}" \
    "$(tool_path_or_empty gh)" "$(tool_path_or_empty node)" \
    "$(tool_path_or_empty uv)" "$(tool_path_or_empty prism)" \
    "$(tool_path_or_empty gito)" "$(tool_path_or_empty shellcheck)" \
    "$@" <<'PY'
import json
import os
import sys

(
    python_path,
    python_source,
    python_version,
    project_check,
    repo_root,
    pycache,
    uv_cache,
    uv_tools,
    ruff_cache,
    gh,
    node,
    uv,
    prism,
    gito,
    shellcheck,
    *candidates,
) = sys.argv[1:]
print(json.dumps({
    "repo_root": repo_root,
    "python": {
        "path": python_path,
        "source": python_source,
        "version": python_version,
    },
    "project_check": project_check,
    "project_check_candidates": candidates,
    "pack_full_check": os.path.isfile(
        os.path.join(repo_root, "scripts", "sd-ai-command-pack-full-check.sh")
    ),
    "optional_tools": {
        "gh": gh,
        "node": node,
        "uv": uv,
        "prism": prism,
        "gito": gito,
        "shellcheck": shellcheck,
    },
    "cache_paths": {
        "PYTHONPYCACHEPREFIX": pycache,
        "UV_CACHE_DIR": uv_cache,
        "UV_TOOL_DIR": uv_tools,
        "RUFF_CACHE_DIR": ruff_cache,
    },
}, sort_keys=True))
PY
}

doctor_human() {
  local project_check="$1"
  local candidate tool path
  printf 'Repository: %s\n' "$REPO_ROOT"
  printf 'Python: %s (%s, %s)\n' "$PYTHON_COMMAND" "$PYTHON_VERSION" "$PYTHON_SOURCE"
  if [ -n "$project_check" ]; then
    printf 'Project check: %s (configured; not executed)\n' "$project_check"
  else
    printf 'Project check: not configured\n'
  fi
  if [ "${#PROJECT_CHECK_CANDIDATES[@]}" -eq 0 ]; then
    printf 'Project check candidates: none detected\n'
  else
    printf 'Project check candidates (not executed):\n'
    for candidate in "${PROJECT_CHECK_CANDIDATES[@]}"; do
      printf '  - %s\n' "$candidate"
    done
  fi
  if [ -f "$REPO_ROOT/scripts/sd-ai-command-pack-full-check.sh" ]; then
    printf 'Pack full-check: available\n'
  else
    printf 'Pack full-check: missing\n'
  fi
  printf 'Optional tools:\n'
  for tool in gh node uv prism gito shellcheck; do
    path="$(tool_path_or_empty "$tool")"
    printf '  - %s: %s\n' "$tool" "${path:-not found}"
  done
  printf 'Cache paths:\n'
  printf '  - PYTHONPYCACHEPREFIX=%s\n' "${PYTHONPYCACHEPREFIX:-}"
  printf '  - UV_CACHE_DIR=%s\n' "${UV_CACHE_DIR:-}"
  printf '  - UV_TOOL_DIR=%s\n' "${UV_TOOL_DIR:-}"
  printf '  - RUFF_CACHE_DIR=%s\n' "${RUFF_CACHE_DIR:-}"
}

configure_cache_defaults() {
  local temp_root="${TMPDIR:-/tmp}"
  : "${PYTHONPYCACHEPREFIX:=${temp_root%/}/sd-ai-command-pack-pycache}"
  : "${UV_CACHE_DIR:=${temp_root%/}/sd-ai-command-pack-uv-cache}"
  : "${UV_TOOL_DIR:=${temp_root%/}/sd-ai-command-pack-uv-tools}"
  : "${RUFF_CACHE_DIR:=${temp_root%/}/sd-ai-command-pack-ruff-cache}"
  export PYTHONPYCACHEPREFIX UV_CACHE_DIR UV_TOOL_DIR RUFF_CACHE_DIR
}

[ "$#" -ge 1 ] || usage
COMMAND="$1"
shift

case "$COMMAND" in
  python)
    parse_python_options "$@"
    [ "$RUN_PYTHON_SEPARATOR" -eq 0 ] || usage
    configure_cache_defaults
    select_python
    verify_python
    printf '%s\n' "$PYTHON_COMMAND"
    ;;
  run-python)
    parse_python_options "$@"
    [ "$RUN_PYTHON_SEPARATOR" -eq 1 ] || usage
    [ "${#RUN_PYTHON_ARGS[@]}" -gt 0 ] || usage
    configure_cache_defaults
    select_python
    verify_python
    exec "$PYTHON_COMMAND" "${RUN_PYTHON_ARGS[@]}"
    ;;
  doctor)
    JSON_MODE=0
    case "$#" in
      0) ;;
      1) [ "$1" = "--json" ] || usage; JSON_MODE=1 ;;
      *) usage ;;
    esac
    configure_cache_defaults
    select_python
    verify_python
    discover_make_candidates
    discover_package_candidates
    discover_script_candidates
    if [ "$JSON_MODE" -eq 1 ]; then
      if [ "${#PROJECT_CHECK_CANDIDATES[@]}" -eq 0 ]; then
        doctor_json "${SD_AI_COMMAND_PACK_PROJECT_CHECK_COMMAND:-}"
      else
        doctor_json "${SD_AI_COMMAND_PACK_PROJECT_CHECK_COMMAND:-}" \
          "${PROJECT_CHECK_CANDIDATES[@]}"
      fi
    else
      doctor_human "${SD_AI_COMMAND_PACK_PROJECT_CHECK_COMMAND:-}"
    fi
    ;;
  *) usage ;;
esac
