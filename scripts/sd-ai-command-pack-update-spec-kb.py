#!/usr/bin/env python3
"""Forward to the machine-installed sd-ai-command-pack Obsidian KB refresh helper.

Why this file exists: the pack's own ``sd-check`` resolves its shipped helpers
only at ``<repo>/scripts/sd-ai-command-pack-<name>`` and requires a regular file
there (a symlink is rejected). Since the repo moved to a thin pack install the
installer no longer places those files, so every builtin row reported
``unavailable`` and the aggregate never reached ``passed`` -- which fails
``sd-review scope=pr`` closed for every pull request. See
``docs/DEVELOPMENT_CYCLE.md`` for why the two simpler fixes do not work.

Resolution is by name on PATH rather than by absolute path so a pack version
bump does not strand this file on a stale install directory. This script's own
directory is removed from the search path first: it shares its basename with
its target, so a checkout that puts ``scripts/`` on PATH would otherwise
resolve this forwarder to itself and exec-loop forever. ``execvp`` then
replaces this process, so the helper's exit code, stdout, and stderr reach the
caller byte-for-byte with no wrapper in the middle.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

TARGET = "sd-ai-command-pack-update-spec-kb.py"


def _search_path() -> str:
    """PATH with this script's own directory removed."""
    own = Path(__file__).resolve().parent
    entries = os.environ.get("PATH", os.defpath).split(os.pathsep)
    kept = []
    for entry in entries:
        try:
            if entry and Path(entry).resolve() == own:
                continue
        except OSError:
            pass
        kept.append(entry)
    return os.pathsep.join(kept)


def main(argv: list[str]) -> int:
    resolved = shutil.which(TARGET, path=_search_path())
    if resolved is None:
        print(
            f"{TARGET} is not resolvable on PATH; "
            "install or refresh sd-ai-command-pack, then rerun.",
            file=sys.stderr,
        )
        return 2
    if Path(resolved).resolve() == Path(__file__).resolve():
        print(f"{TARGET} resolved to this forwarder; refusing to recurse.", file=sys.stderr)
        return 2
    os.execv(resolved, [resolved, *argv[1:]])


if __name__ == "__main__":
    sys.exit(main(sys.argv))
