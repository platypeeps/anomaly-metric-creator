#!/usr/bin/env python3
"""Forward to the machine-installed sd-ai-command-pack pull-request body scope helper.

Why this file exists: the pack's own ``sd-check`` resolves its shipped helpers
only at ``<repo>/scripts/sd-ai-command-pack-<name>`` and requires a regular file
there (a symlink is rejected). Since the repo moved to a thin pack install the
installer no longer places those files, so every builtin row reported
``unavailable`` and the aggregate never reached ``passed`` -- which fails
``sd-review scope=pr`` closed for every pull request. See
``docs/DEVELOPMENT_CYCLE.md`` for why the two simpler fixes do not work.

Resolution lives in ``_sd_pack_forward``, shared with the other Python
forwarders; read its docstring for the PATH rules and why each one is there.
"""

from __future__ import annotations

import sys
from pathlib import Path

# ``sys.path[0]`` is normally this directory, but not under ``python -P`` or
# ``PYTHONSAFEPATH``, and the sibling import must not depend on how the
# interpreter was invoked.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _sd_pack_forward import forward  # noqa: E402  (path setup must precede)

TARGET = "sd-ai-command-pack-pr-body-scope.py"


if __name__ == "__main__":
    sys.exit(forward(TARGET, __file__, sys.argv))
