"""Pin the `scripts/sd-ai-command-pack-*` review-gate forwarders.

These five files exist because the command pack contradicts itself: `sd-check`
resolves its shipped helpers only at `<repo>/scripts/sd-ai-command-pack-<name>`
and requires a regular file there, `install-audit` fails on any
`sd-ai-command-pack-*` file not listed in
`.sd-ai-command-pack/installed-targets.txt`, and the thin installer places
none of them. The result was an `unavailable` aggregate, which fails
`sd-review scope=pr` closed for every pull request. See
`docs/DEVELOPMENT_CYCLE.md` § Local review-gate helper forwarders.

The failure mode this pins is silent: a renamed, symlinked, or unlisted
forwarder does not break any test that exists otherwise — it breaks the review
gate, which is only noticed at publish time by whoever ships next. Each
assertion below maps to one thing the pack checks.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
INSTALLED_TARGETS = REPO_ROOT / ".sd-ai-command-pack" / "installed-targets.txt"

# The exact basenames `sd-check` resolves. Renaming one silently disables that
# builtin row, so the list is spelled out rather than globbed.
FORWARDERS = (
    "sd-ai-command-pack-install-audit.py",
    "sd-ai-command-pack-pr-body-scope.py",
    "sd-ai-command-pack-review-preflight.mjs",
    "sd-ai-command-pack-review-scope.sh",
    "sd-ai-command-pack-update-spec-kb.py",
)

# The Python forwarders differ only in their target basename; the PATH rules
# live in one shared module. Its name must not look like a pack payload.
SHARED_MODULE = SCRIPTS_DIR / "_sd_pack_forward.py"
PYTHON_FORWARDERS = tuple(name for name in FORWARDERS if name.endswith(".py"))


@pytest.mark.parametrize("name", FORWARDERS)
def test_forwarder_is_a_regular_file(name: str) -> None:
    """`shipped_helper_row` requires `is_file() and not is_symlink()`."""
    path = SCRIPTS_DIR / name
    assert path.exists(), f"{name} is missing; sd-check reports its row unavailable"
    assert not path.is_symlink(), f"{name} is a symlink; sd-check rejects symlinked helpers"
    assert path.is_file()


@pytest.mark.parametrize("name", FORWARDERS)
def test_forwarder_targets_its_own_name(name: str) -> None:
    """A forwarder must delegate to the pack helper of the *same* name.

    A copy-paste rename that leaves the old target behind would forward to the
    wrong helper while every other assertion here still passed.
    """
    source = (SCRIPTS_DIR / name).read_text(encoding="utf-8")
    assert f'"{name}"' in source, f"{name} does not name itself as its forward target"


@pytest.mark.parametrize("name", FORWARDERS)
def test_forwarder_is_listed_in_installed_targets(name: str) -> None:
    """`install-audit` fails on an unlisted pack-like file."""
    listed = INSTALLED_TARGETS.read_text(encoding="utf-8").splitlines()  # resource-lint: allow
    assert f"scripts/{name}" in listed, (
        f"scripts/{name} is not in installed-targets.txt; install-audit fails with "
        "'pack-like file is not listed in installed targets'"
    )


@pytest.mark.parametrize("name", FORWARDERS)
def test_forwarder_does_not_recurse_when_scripts_is_on_path(name: str) -> None:
    """A forwarder shares its basename with its target, so `scripts/` on PATH
    would resolve it to itself and loop forever. Each one strips its own
    directory from the search path before resolving.

    Behavioral rather than a source-text assertion: the failure this guards is
    an unbounded exec loop, and only running it proves the loop is closed. The
    timeout is the assertion -- a recursing forwarder never returns.
    """
    runner = {
        ".py": [sys.executable],
        ".sh": ["bash"],
        ".mjs": [shutil.which("node") or ""],
    }[Path(name).suffix]
    if not runner[0]:
        pytest.skip("Node.js is unavailable; the .mjs forwarder cannot be executed")

    environment = dict(os.environ)
    environment["PATH"] = os.pathsep.join(
        [str(SCRIPTS_DIR), environment.get("PATH", os.defpath)]
    )
    try:
        result = subprocess.run(
            [*runner, str(SCRIPTS_DIR / name), "--help"],
            capture_output=True,
            text=True,
            timeout=60,
            env=environment,
            cwd=REPO_ROOT,
        )
    except subprocess.TimeoutExpired:  # pragma: no cover - the failure being pinned
        pytest.fail(f"{name} did not terminate; the self-resolution guard is missing")

    # Terminating via the guard is not success: it means the forwarder never
    # reached the real helper, so the sd-check row it exists to feed is dead.
    assert "refusing to recurse" not in result.stderr, (
        f"{name} resolved to itself instead of the machine-installed helper"
    )


@pytest.mark.parametrize("name", PYTHON_FORWARDERS)
def test_python_forwarder_uses_the_shared_resolution(name: str) -> None:
    """The PATH rules are security-adjacent; three copies would drift apart."""
    source = (SCRIPTS_DIR / name).read_text(encoding="utf-8")
    assert "from _sd_pack_forward import forward" in source, (
        f"{name} re-implements forwarder resolution instead of sharing it"
    )


def test_shared_module_is_not_pack_like() -> None:
    """`install-audit` fails on an unlisted file matching its pack patterns.

    The shared module is repo-owned glue rather than a pack payload, so it must
    not carry a pack-shaped name -- listing it in the receipt would claim the
    installer placed it.
    """
    assert SHARED_MODULE.is_file()
    stem = SHARED_MODULE.name
    assert not stem.startswith("sd-ai-command-pack"), stem
    assert not stem.startswith("sd_ai_command_pack"), stem


def test_shared_search_path_drops_own_directory_and_empty_entries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both narrowings are deliberate; see `_sd_pack_forward`'s docstring.

    An empty entry is POSIX's implicit current directory. Keeping it would let
    the caller's working directory supply the helper this gate runs, so leading,
    trailing, and doubled separators are all dropped rather than honoured.
    """
    sys.path.insert(0, str(SCRIPTS_DIR))
    try:
        import _sd_pack_forward
    finally:
        sys.path.remove(str(SCRIPTS_DIR))

    monkeypatch.setenv(
        "PATH", os.pathsep.join(["", "/usr/bin", str(SCRIPTS_DIR), "", "/bin", ""])
    )
    forwarder = SCRIPTS_DIR / PYTHON_FORWARDERS[0]
    kept = _sd_pack_forward.search_path(forwarder).split(os.pathsep)
    assert kept == ["/usr/bin", "/bin"], kept


def test_shared_forward_reports_an_unexecutable_target(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """An `execv` that fails must read like every other failure here.

    `which` checks the executable bit, so this is the narrow case where the
    file changed underneath the check or the kernel refused it. Uncaught, it
    would surface as a traceback where the caller expects a one-line
    diagnostic and exit 2.
    """
    sys.path.insert(0, str(SCRIPTS_DIR))
    try:
        import _sd_pack_forward
    finally:
        sys.path.remove(str(SCRIPTS_DIR))

    target = tmp_path / "sd-ai-command-pack-stand-in.py"
    target.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    target.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr(
        _sd_pack_forward.os, "execv", lambda *_: (_ for _ in ()).throw(OSError("Exec format error"))
    )

    status = _sd_pack_forward.forward(target.name, str(SCRIPTS_DIR / "caller.py"), ["caller.py"])

    assert status == 2
    assert "could not be executed: Exec format error" in capsys.readouterr().err


def test_no_unlisted_pack_like_scripts() -> None:
    """The reverse direction: a *new* forwarder must reach the receipt too.

    Enumerated from the filesystem rather than from FORWARDERS, so adding a
    sixth file without updating the receipt fails here instead of at publish
    time.
    """
    listed = set(
        INSTALLED_TARGETS.read_text(encoding="utf-8").splitlines()  # resource-lint: allow
    )
    on_disk = sorted(p.name for p in SCRIPTS_DIR.glob("sd-ai-command-pack-*"))
    unlisted = [name for name in on_disk if f"scripts/{name}" not in listed]
    assert not unlisted, f"pack-like scripts missing from installed-targets.txt: {unlisted}"
