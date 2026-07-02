"""Contract check for the vendored sd-ai-command-pack housekeeping script.

The script ships its own hermetic self-test (`--self-test`), so this repo just
invokes it: the contract test travels with the vendored bytes and updates
atomically on every pack refresh instead of pinning upstream internals here.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "sd-ai-command-pack-housekeeping.sh"


def test_vendored_housekeeping_script_passes_its_self_test() -> None:
    result = subprocess.run(
        ["bash", str(SCRIPT), "--self-test"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "self-test: all scenarios passed" in result.stdout
