"""Shared resolution for the ``scripts/sd-ai-command-pack-*.py`` forwarders.

Three of the five review-gate forwarders are Python and differ only in the
helper basename they delegate to. The resolution rules below are the part worth
having in one place: they are security-adjacent, they are subtle, and three
copies drift independently.

Deliberately **not** named ``sd-ai-command-pack-*`` or ``sd_ai_command_pack_*``:
those are the pack's own ``PACK_FILE_PATTERNS``, and a file matching them must
be listed in ``.sd-ai-command-pack/installed-targets.txt`` or ``install-audit``
fails. This module is repo-owned glue, not a pack payload, so it must not look
like one.

The rules
---------
*Resolve by name, not by absolute path.* A pack version bump moves the install
directory; a hard-coded path would strand the forwarder on a stale one.

*Strip this file's own directory from the search path.* A forwarder shares its
basename with its target, so a checkout that puts ``scripts/`` on ``PATH``
would otherwise resolve it to itself and exec-loop forever.

*Drop empty ``PATH`` entries.* POSIX reads an empty entry as the current
directory. Honouring that would let whatever directory the caller happens to be
sitting in supply the helper this gate runs, so all three forwarder languages
refuse it -- the shell loop skips empty entries and the Node forwarder filters
them out for the same reason. This is a deliberate narrowing of ``PATH``
semantics, not an oversight in the parsing.

*Replace the process rather than wrapping it.* ``execv`` hands the helper's
exit code, stdout, and stderr to the caller byte-for-byte.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

WHY = (
    "install or refresh sd-ai-command-pack, then rerun."
)


def search_path(self_file: Path) -> str:
    """``PATH`` with ``self_file``'s directory and every empty entry removed.

    Comparison is on resolved real paths so a symlinked or relative ``PATH``
    entry pointing at the same directory is still recognised as this one.
    """
    own = self_file.resolve().parent
    kept: list[str] = []
    for entry in os.environ.get("PATH", os.defpath).split(os.pathsep):
        if not entry:
            # Implicit-CWD entry; see the module docstring.
            continue
        try:
            if Path(entry).resolve() == own:
                continue
        except OSError:
            # An unreadable entry cannot be proven to be this directory, and
            # `shutil.which` will simply fail to find anything in it.
            pass
        kept.append(entry)
    return os.pathsep.join(kept)


def forward(target: str, self_file: str, argv: list[str]) -> int:
    """Exec the machine-installed ``target``; return an exit code if it cannot.

    Never returns on success -- ``execv`` replaces this process.
    """
    self_path = Path(self_file)
    resolved = shutil.which(target, path=search_path(self_path))
    if resolved is None:
        print(f"{target} is not resolvable on PATH; {WHY}", file=sys.stderr)
        return 2
    if Path(resolved).resolve() == self_path.resolve():
        # Belt-and-braces: `search_path` already removed this directory, so
        # reaching here means PATH holds another route to the same file.
        print(f"{target} resolved to this forwarder; refusing to recurse.", file=sys.stderr)
        return 2
    os.execv(resolved, [resolved, *argv[1:]])
