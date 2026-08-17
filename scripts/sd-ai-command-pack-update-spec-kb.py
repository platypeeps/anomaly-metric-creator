#!/usr/bin/env python3
"""Forward to the machine-installed sd-ai-command-pack Obsidian KB refresh helper.

Why this file exists: the pack's own ``sd-check`` resolves its shipped helpers
only at ``<repo>/scripts/sd-ai-command-pack-<name>`` and requires a regular file
there (a symlink is rejected). Since the repo moved to a thin pack install the
installer no longer places those files, so every builtin row reported
``unavailable`` and the aggregate never reached ``passed`` -- which fails
``sd-review scope=pr`` closed for every pull request. Registering an equivalent
command in ``.sd-ai-command-pack/check.json`` does not help: the builtin rows
are emitted regardless and ``unavailable`` outranks ``passed`` in the
aggregate.

Resolution is by name on PATH rather than by absolute path so a pack version
bump does not strand this file on a stale install directory. ``execvp`` replaces
this process, so the helper's exit code, stdout, and stderr reach the caller
byte-for-byte with no wrapper in the middle.
"""

from __future__ import annotations

import os
import shutil
import sys

TARGET = "sd-ai-command-pack-update-spec-kb.py"


def main(argv: list[str]) -> int:
    resolved = shutil.which(TARGET)
    if resolved is None:
        print(
            f"{TARGET} is not resolvable on PATH; "
            "install or refresh sd-ai-command-pack, then rerun.",
            file=sys.stderr,
        )
        return 2
    os.execvp(resolved, [resolved, *argv[1:]])


if __name__ == "__main__":
    sys.exit(main(sys.argv))
