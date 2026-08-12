#!/usr/bin/env bash

# The single machine update action: refresh the Claude Code plugin, then
# install the machine-scope surfaces for the non-Claude platforms from the
# plugin root the update just produced. Bash 3.2 compatibility is intentional.
#
# Both halves are idempotent and the machine receipt only advances on a
# successful install, so an update interrupted between the halves shows up as
# version skew (here and in sd-status) and a rerun converges.
#
# Exit status:
#   0   plugin and machine install agree on the pack version
#   2   usage error, raised before either half runs. The machine installer
#       also exits 2 when it refuses a conflict, and step 3 propagates that,
#       so 2 alone does not identify the failure; the output does, since a
#       usage error prints the usage text and never invokes `claude`.
#   5   this script's own directory could not be resolved
#   10  `claude plugin list --json` output was not a usable plugin array
#   11  the plugin is not installed
#   12  the plugin is listed more than once
#   13  the listed plugin entry has no usable install path
#   14  both halves ran but the machine install does not match the plugin
#   15  the plugin version or the machine install state could not be read
#   127 a required program is missing (the Claude CLI, a sibling helper, or
#       the machine installer inside the resolved plugin root)
#   *   the exit status of the `claude` or machine-install step that failed

set -euo pipefail

PLUGIN_NAME="sd"
MARKETPLACE_NAME="sd-ai-command-pack"
INSTALL_ARGS=()
STATUS_ARGS=()

usage() {
  cat >&2 <<'EOF'
Usage:
  sd-ai-command-pack-pack-update.sh [--plugin NAME] [--marketplace NAME]
                                    [--home DIR] [--state-home DIR] [--force]

  --plugin/--marketplace  override the plugin identity to update
  --home/--state-home     machine-install destinations (scratch-prefix runs)
  --force                 let the machine install overwrite unowned files,
                          backing each one up first
EOF
  exit 2
}

fail() {
  printf 'sd-ai-command-pack pack-update: %s\n' "$1" >&2
  exit "${2:-1}"
}

# Helpers are siblings of this script, not repository-root paths, so this works
# in a vendored scripts/ directory, in a plugin bin/, and in ~/.agents/bin.
case "${BASH_SOURCE[0]}" in
  */*) SCRIPT_DIR="${BASH_SOURCE[0]%/*}" ;;
  *) SCRIPT_DIR="." ;;
esac
if ! SCRIPT_DIR="$(cd -- "$SCRIPT_DIR" 2>/dev/null && pwd -P)"; then
  fail "cannot resolve the directory of $0" 5
fi

TOOLCHAIN_SCRIPT="$SCRIPT_DIR/sd-ai-command-pack-toolchain.sh"

while [ "$#" -gt 0 ]; do
  case "$1" in
    --plugin)
      [ "$#" -ge 2 ] || usage
      PLUGIN_NAME="$2"
      shift 2
      ;;
    --marketplace)
      [ "$#" -ge 2 ] || usage
      MARKETPLACE_NAME="$2"
      shift 2
      ;;
    --home)
      [ "$#" -ge 2 ] || usage
      INSTALL_ARGS+=(--home "$2")
      STATUS_ARGS+=(--home "$2")
      shift 2
      ;;
    --state-home)
      [ "$#" -ge 2 ] || usage
      INSTALL_ARGS+=(--state-home "$2")
      STATUS_ARGS+=(--state-home "$2")
      shift 2
      ;;
    --force)
      INSTALL_ARGS+=(--force)
      shift
      ;;
    -h | --help) usage ;;
    *) usage ;;
  esac
done

if [ -z "$PLUGIN_NAME" ] || [ -z "$MARKETPLACE_NAME" ]; then
  usage
fi
PLUGIN_ID="$PLUGIN_NAME@$MARKETPLACE_NAME"

[ -f "$TOOLCHAIN_SCRIPT" ] ||
  fail "$TOOLCHAIN_SCRIPT is missing; reinstall the command pack." 127
command -v claude >/dev/null 2>&1 ||
  fail "the Claude Code CLI (claude) is not on PATH; it owns the plugin half of the update." 127

# Step 1: update the plugin. A failure here stops the run: installing the
# machine surfaces from the old root would claim an update that did not happen.
printf 'updating plugin %s\n' "$PLUGIN_ID"
update_status=0
claude plugin update "$PLUGIN_ID" || update_status=$?
[ "$update_status" -eq 0 ] ||
  fail "claude plugin update $PLUGIN_ID failed (exit $update_status); the machine install did not run." "$update_status"

# Step 2: resolve the NEW plugin root. Never this script's own location: the
# running copy lives in the root the update just replaced.
list_status=0
list_json="$(claude plugin list --json)" || list_status=$?
[ "$list_status" -eq 0 ] ||
  fail "claude plugin list --json failed (exit $list_status); the machine install did not run." "$list_status"

resolve_status=0
PLUGIN_ROOT="$(
  bash "$TOOLCHAIN_SCRIPT" run-python -- - "$PLUGIN_ID" "$list_json" <<'PY'
import json
import sys

wanted = sys.argv[1]
try:
    entries = json.loads(sys.argv[2])
except ValueError:
    raise SystemExit(10)
if not isinstance(entries, list):
    raise SystemExit(10)

matches = [
    entry
    for entry in entries
    if isinstance(entry, dict) and entry.get("id") == wanted
]
if not matches:
    raise SystemExit(11)
if len(matches) > 1:
    raise SystemExit(12)
root = matches[0].get("installPath")
if not isinstance(root, str) or not root.strip():
    raise SystemExit(13)
print(root.strip(), end="")
PY
)" || resolve_status=$?

case "$resolve_status" in
  0) ;;
  10) fail "claude plugin list --json did not return a JSON array of installed plugins; the machine install did not run." 10 ;;
  11) fail "plugin $PLUGIN_ID is not installed, so there is no root to install from; add it with: claude plugin install $PLUGIN_ID" 11 ;;
  12) fail "claude plugin list --json reports $PLUGIN_ID more than once; resolve the duplicate install before updating." 12 ;;
  13) fail "the claude plugin list entry for $PLUGIN_ID carries no installPath, so the updated plugin root is unknown." 13 ;;
  *) fail "could not resolve the updated plugin root for $PLUGIN_ID (exit $resolve_status)." "$resolve_status" ;;
esac

[ -d "$PLUGIN_ROOT" ] ||
  fail "the resolved plugin root does not exist: $PLUGIN_ROOT" 13
MACHINE_INSTALL="$PLUGIN_ROOT/bin/sd-machine-install"
[ -x "$MACHINE_INSTALL" ] ||
  fail "the updated plugin root has no executable machine installer: $MACHINE_INSTALL" 127

# Step 3: install the machine surfaces from that root. The installer finds its
# own bundled payload, so the root is the only thing this script has to get
# right. A failure is reported after the version summary below, which is what
# makes the half-applied update legible as skew.
printf 'installing machine surfaces from %s\n' "$PLUGIN_ROOT"
install_status=0
"$MACHINE_INSTALL" install ${INSTALL_ARGS[@]+"${INSTALL_ARGS[@]}"} || install_status=$?

# Step 4: report both versions. The plugin version comes from the new root's
# own manifest and the machine version from the install receipt, so the two
# halves are read from where each one actually landed.
read_plugin_version() {
  bash "$TOOLCHAIN_SCRIPT" run-python -- - "$PLUGIN_ROOT/.claude-plugin/plugin.json" <<'PY'
import json
import sys

try:
    with open(sys.argv[1], encoding="utf-8") as stream:
        payload = json.load(stream)
except (OSError, UnicodeError, ValueError):
    raise SystemExit(20)
if not isinstance(payload, dict):
    raise SystemExit(20)
version = payload.get("version")
if not isinstance(version, str) or not version.strip():
    raise SystemExit(20)
print(version.strip(), end="")
PY
}

# The status JSON travels as an argument, not on stdin: the toolchain's
# run-python reads the program itself from stdin.
read_machine_state() {
  local report
  report="$("$MACHINE_INSTALL" status --json ${STATUS_ARGS[@]+"${STATUS_ARGS[@]}"})" ||
    return 21
  bash "$TOOLCHAIN_SCRIPT" run-python -- - "$report" <<'PY'
import json
import sys

try:
    report = json.loads(sys.argv[1])
except ValueError:
    raise SystemExit(21)
if not isinstance(report, dict):
    raise SystemExit(21)
state = report.get("state")
if not isinstance(state, str) or not state:
    raise SystemExit(21)
version = report.get("packVersion")
print(f"{state} {version if isinstance(version, str) and version else '-'}", end="")
PY
}

plugin_version=""
plugin_version_status=0
plugin_version="$(read_plugin_version)" || plugin_version_status=$?
[ "$plugin_version_status" -eq 0 ] || plugin_version=""

machine_report=""
machine_status=0
machine_report="$(read_machine_state)" || machine_status=$?
machine_state=""
machine_version=""
if [ "$machine_status" -eq 0 ]; then
  machine_state="${machine_report%% *}"
  machine_version="${machine_report#* }"
  if [ "$machine_version" = "-" ]; then
    machine_version=""
  fi
fi

verdict="unknown"
detail=""
if [ -z "$plugin_version" ]; then
  detail="the plugin root does not report a version"
elif [ -z "$machine_state" ]; then
  detail="the machine install state could not be read"
elif [ "$machine_state" = "installed" ] && [ "$machine_version" = "$plugin_version" ]; then
  verdict="current"
elif [ "$machine_state" = "installed" ]; then
  verdict="skew"
  detail="plugin $plugin_version, machine ${machine_version:-unrecorded}"
elif [ "$machine_state" = "none" ]; then
  verdict="skew"
  detail="no machine install recorded for plugin $plugin_version"
else
  verdict="skew"
  detail="machine receipt is $machine_state"
fi

printf 'plugin:  %s %s\n' "$PLUGIN_ID" "${plugin_version:-unknown}"
printf 'root:    %s\n' "$PLUGIN_ROOT"
printf 'machine: %s %s\n' "${machine_state:-unreadable}" "${machine_version:-unknown}"
if [ -n "$detail" ]; then
  printf 'status:  %s (%s)\n' "$verdict" "$detail"
else
  printf 'status:  %s\n' "$verdict"
fi

if [ "$install_status" -ne 0 ]; then
  fail "the machine install failed (exit $install_status); the plugin is updated and the machine surfaces are not. Rerun this script to converge." "$install_status"
fi
case "$verdict" in
  current) exit 0 ;;
  skew) fail "the plugin and machine halves do not agree. Rerun this script to converge." 14 ;;
  *) fail "could not compare the plugin and machine versions." 15 ;;
esac
