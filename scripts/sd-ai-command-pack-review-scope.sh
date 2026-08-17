#!/usr/bin/env bash
# Forward to the machine-installed sd-ai-command-pack review-scope helper.
#
# Why this file exists: the pack's own `sd-check` resolves its shipped helpers
# only at `<repo>/scripts/sd-ai-command-pack-<name>` and requires a regular file
# there (a symlink is rejected). Since the repo moved to a thin pack install the
# installer no longer places those files, so every builtin row reported
# `unavailable` and the aggregate never reached `passed` -- which fails
# `sd-review scope=pr` closed for every pull request. Registering an equivalent
# command in `.sd-ai-command-pack/check.json` does not help: the builtin rows
# are emitted regardless and `unavailable` outranks `passed` in the aggregate.
#
# Resolution is by name on PATH rather than by absolute path so a pack version
# bump does not strand this file on a stale install directory. `exec` replaces
# this shell, so the helper's exit code and streams reach the caller unchanged.
set -euo pipefail

TARGET="sd-ai-command-pack-review-scope.sh"

if ! command -v "$TARGET" >/dev/null 2>&1; then
  printf '%s is not resolvable on PATH; install or refresh sd-ai-command-pack, then rerun.\n' \
    "$TARGET" >&2
  exit 2
fi

exec "$TARGET" "$@"
