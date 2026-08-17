#!/usr/bin/env bash
# Forward to the machine-installed sd-ai-command-pack review-scope helper.
#
# Why this file exists: the pack's own `sd-check` resolves its shipped helpers
# only at `<repo>/scripts/sd-ai-command-pack-<name>` and requires a regular file
# there (a symlink is rejected). Since the repo moved to a thin pack install the
# installer no longer places those files, so every builtin row reported
# `unavailable` and the aggregate never reached `passed` -- which fails
# `sd-review scope=pr` closed for every pull request. See
# `docs/DEVELOPMENT_CYCLE.md` for why the two simpler fixes do not work.
#
# Resolution is by name on PATH rather than by absolute path so a pack version
# bump does not strand this file on a stale install directory. This script's own
# directory is removed from the search path first: it shares its basename with
# its target, so a checkout that puts `scripts/` on PATH would otherwise resolve
# this forwarder to itself and exec-loop forever. `exec` then replaces this
# shell, so the helper's exit code and streams reach the caller unchanged.
#
# Empty PATH entries -- leading, trailing, or doubled colons -- are dropped
# rather than read as POSIX's implicit current directory. Honouring them would
# let whatever directory the caller happens to be sitting in supply the helper
# this gate runs. The Python and Node forwarders narrow PATH the same way; see
# `_sd_pack_forward.py` for the shared rationale.
set -euo pipefail

TARGET="sd-ai-command-pack-review-scope.sh"

SELF_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
SELF_PATH="$SELF_DIR/$(basename -- "${BASH_SOURCE[0]}")"

FILTERED=""
while IFS= read -r entry; do
  [ -n "$entry" ] || continue
  resolved_entry="$(cd -- "$entry" 2>/dev/null && pwd -P || true)"
  [ "$resolved_entry" = "$SELF_DIR" ] && continue
  FILTERED="${FILTERED:+$FILTERED:}$entry"
done <<< "$(printf '%s' "$PATH" | tr ':' '\n')"

RESOLVED="$(PATH="$FILTERED" command -v "$TARGET" || true)"
if [ -z "$RESOLVED" ]; then
  printf '%s is not resolvable on PATH; install or refresh sd-ai-command-pack, then rerun.\n' \
    "$TARGET" >&2
  exit 2
fi
if [ "$(cd -- "$(dirname -- "$RESOLVED")" && pwd -P)/$(basename -- "$RESOLVED")" = "$SELF_PATH" ]; then
  printf '%s resolved to this forwarder; refusing to recurse.\n' "$TARGET" >&2
  exit 2
fi

exec "$RESOLVED" "$@"
