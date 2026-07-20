#!/usr/bin/env bash

# Select the deterministic local full-check entrypoint for sd-review-pr.
# Bash 3.2 compatibility is intentional.

set -u

fail() {
  printf 'sd-ai-command-pack review full-check: %s\n' "$1" >&2
  exit "${2:-1}"
}

toolchain_script="scripts/sd-ai-command-pack-toolchain.sh"
full_check_script="scripts/sd-ai-command-pack-full-check.sh"
configured_command=""

if [ -f "package.json" ]; then
  [ -f "$toolchain_script" ] || fail "$toolchain_script is missing; reinstall the command pack." 127

  configured_command="$(
    bash "$toolchain_script" run-python -- - "package.json" <<'PY'
import json
import sys

try:
    with open(sys.argv[1], encoding="utf-8") as stream:
        payload = json.load(stream)
except OSError as exc:
    print(f"cannot read {sys.argv[1]}: {exc}", file=sys.stderr)
    raise SystemExit(11)
except (UnicodeError, ValueError):
    raise SystemExit(10)

if not isinstance(payload, dict):
    raise SystemExit(10)
scripts = payload.get("scripts", {})
command = scripts.get("check:full") if isinstance(scripts, dict) else None
if not isinstance(command, str) or not command.strip():
    raise SystemExit(10)
print(command, end="")
PY
  )"
  inspection_status=$?

  case "$inspection_status" in
    0) ;;
    10) configured_command="" ;;
    *)
      fail "could not inspect package.json through $toolchain_script (exit $inspection_status)." "$inspection_status"
      ;;
  esac
fi

if [ -n "$configured_command" ]; then
  case "$configured_command" in
    *sd-review-pr*|*sd:review-pr*|*sd/review-pr*|*sd-ai-command-pack-review-full-check.sh*)
      fail "package.json script check:full must not invoke sd-review-pr or $0." 2
      ;;
  esac

  package_runner="${SD_AI_COMMAND_PACK_FULL_CHECK_PACKAGE_RUNNER:-npm}"
  command -v "$package_runner" >/dev/null 2>&1 || fail "configured package runner not found on PATH: $package_runner" 127

  exec env SD_AI_COMMAND_PACK_FULL_CHECK_PRISM=0 SD_AI_COMMAND_PACK_FULL_CHECK_GITO=0 "$package_runner" run check:full
fi

[ -f "$full_check_script" ] || fail "$full_check_script is missing; reinstall the command pack." 127

exec env SD_AI_COMMAND_PACK_FULL_CHECK_PRISM=0 SD_AI_COMMAND_PACK_FULL_CHECK_GITO=0 bash "$full_check_script"
