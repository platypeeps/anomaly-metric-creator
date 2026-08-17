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
    listed = INSTALLED_TARGETS.read_text(encoding="utf-8").splitlines()
    assert f"scripts/{name}" in listed, (
        f"scripts/{name} is not in installed-targets.txt; install-audit fails with "
        "'pack-like file is not listed in installed targets'"
    )


def test_no_unlisted_pack_like_scripts() -> None:
    """The reverse direction: a *new* forwarder must reach the receipt too.

    Enumerated from the filesystem rather than from FORWARDERS, so adding a
    sixth file without updating the receipt fails here instead of at publish
    time.
    """
    listed = set(INSTALLED_TARGETS.read_text(encoding="utf-8").splitlines())
    on_disk = sorted(p.name for p in SCRIPTS_DIR.glob("sd-ai-command-pack-*"))
    unlisted = [name for name in on_disk if f"scripts/{name}" not in listed]
    assert not unlisted, f"pack-like scripts missing from installed-targets.txt: {unlisted}"
