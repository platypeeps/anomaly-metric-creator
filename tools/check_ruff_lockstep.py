#!/usr/bin/env python3
"""Assert the ruff version is in lockstep across its two pins.

ruff is pinned in two places that must agree. The contract is stated in
the inline comments on the pins themselves — ``pyproject.toml`` ("Pinned
exactly: must match ``rev: vX.Y.Z`` … Bump both lines together") and
``.pre-commit-config.yaml`` ("``rev`` must match the ``ruff==X.Y.Z``
pin") — and summarized in ``.trellis/spec/amc/backend/testing-quality.md``.
The two pin sites:

* ``pyproject.toml`` — ``ruff==X.Y.Z`` in the ``dev`` optional-dependency
  group. Drives the local ``.venv`` ruff and any ``ruff check``.
* ``.pre-commit-config.yaml`` — ``rev: vX.Y.Z`` on the
  ``astral-sh/ruff-pre-commit`` hook. Drives the pre-commit ruff.

Historically the two stayed in step because Dependabot's ``increase``
strategy bumped the ``ruff==`` pin in the same window the ``pre-commit``
ecosystem bumped the ``rev``. Since the repo moved to
``versioning-strategy: lockfile-only`` (PR #115), Dependabot no longer
touches the exact ``ruff==`` pin — bumping an ``==`` constraint requires a
manifest change, which ``lockfile-only`` skips — while the ``pre-commit``
ecosystem keeps advancing the ``rev``. The two can therefore drift, and
with Dependabot auto-merge enabled a lone ``rev`` bump could merge while
``pyproject.toml`` stays stale. This check runs in CI as part of the
required ``test`` gate so any such drift fails the build and blocks the
merge until both pins are bumped together.

Usage::

    check_ruff_lockstep.py [PYPROJECT] [PRECOMMIT]

Both paths default to the repository-root files; they are overridable so
the test suite can point the check at fixtures. A single leading ``v`` on
the pre-commit ``rev`` is normalized away before comparison, so
``ruff==0.15.17`` matches ``rev: v0.15.17`` and ``rev: 0.15.17`` alike.

Exit codes:

* ``0`` — both pins resolve to the same version (the in-step message is
  printed to stdout).
* ``1`` — the two pins disagree (drift); one diagnostic naming both
  versions and both files is written to stderr.
* ``2`` — a pin could not be located or a file could not be read: the
  ``ruff==`` dev entry is missing, the ``ruff-pre-commit`` block or its
  ``rev`` is missing, or a file is unreadable/unparseable.
"""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_PYPROJECT = _REPO_ROOT / "pyproject.toml"
_PRECOMMIT = _REPO_ROOT / ".pre-commit-config.yaml"

# The hook repo whose ``rev`` mirrors the ``ruff==`` pin.
_RUFF_PRECOMMIT_REPO = "astral-sh/ruff-pre-commit"

# Matches an exact ``ruff==X.Y.Z`` requirement string, anchored on
# ``ruff`` so ``ruff-pre-commit`` / ``ruff[extra]`` do not match. Only
# the exact ``==`` pin is in lockstep with the pre-commit rev; a range
# spec is treated as a missing exact pin (exit 2) so the contract cannot
# be silently loosened.
_RUFF_PIN_RE = re.compile(r"^\s*ruff\s*==\s*(?P<version>[A-Za-z0-9._!+-]+)")


class _LockstepError(Exception):
    """A pin could not be located or a file could not be read (exit 2)."""


def _normalize(version: str) -> str:
    """Strip a single leading ``v`` so ``v0.15.17`` and ``0.15.17``
    compare equal. Single source of truth for normalization across both
    sides of the comparison."""
    return version[1:] if version.startswith("v") else version


def _ruff_pin_from_pyproject(path: Path) -> str:
    """Return the version from the exact ``ruff==X`` dev-extra pin.

    Raises ``_LockstepError`` if the file is unreadable, unparseable, or
    carries no exact ``ruff==`` entry under
    ``[project.optional-dependencies].dev``."""
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise _LockstepError(f"cannot read {path}: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise _LockstepError(f"cannot parse {path}: {exc}") from exc
    dev = (
        data.get("project", {})
        .get("optional-dependencies", {})
        .get("dev", [])
    )
    for entry in dev:
        if not isinstance(entry, str):
            continue
        match = _RUFF_PIN_RE.match(entry)
        if match:
            return match.group("version")
    raise _LockstepError(
        "no exact 'ruff==X.Y.Z' pin found in "
        f"[project.optional-dependencies].dev of {path}"
    )


def _ruff_rev_from_precommit(path: Path) -> str:
    """Return the ``rev`` of the ``astral-sh/ruff-pre-commit`` hook.

    Parsed with a targeted line scan rather than PyYAML so the check has
    no third-party dependency and runs anywhere (CI step, pre-commit,
    standalone): find the ``- repo: …ruff-pre-commit`` list item, then
    the first ``rev:`` line before the next ``- repo:`` boundary. Inline
    comments and surrounding quotes on the ``rev`` value are stripped.
    Raises ``_LockstepError`` if the file is unreadable, the
    ruff-pre-commit block is absent, or it carries no ``rev:``."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise _LockstepError(f"cannot read {path}: {exc}") from exc
    in_block = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("- repo:"):
            # A new list item resets the block; we are inside the ruff
            # block only while this repo line names the ruff hook repo.
            in_block = _RUFF_PRECOMMIT_REPO in stripped
            continue
        if in_block and stripped.startswith("rev:"):
            rev = stripped[len("rev:"):]
            rev = rev.split("#", 1)[0].strip().strip("'\"")
            if not rev:
                raise _LockstepError(
                    f"empty 'rev:' on the {_RUFF_PRECOMMIT_REPO} hook in {path}"
                )
            return rev
    raise _LockstepError(
        f"no 'rev:' found for the {_RUFF_PRECOMMIT_REPO} hook in {path}"
    )


def check(pyproject: Path, precommit: Path) -> tuple[bool, str]:
    """Return ``(ok, message)`` for the two pins. ``ok`` is True when
    they agree after normalization. Raises ``_LockstepError`` (exit 2)
    for structural problems. Single source of truth for the comparison
    and both message shapes."""
    pin = _ruff_pin_from_pyproject(pyproject)
    rev = _ruff_rev_from_precommit(precommit)
    if _normalize(pin) == _normalize(rev):
        return True, (
            f"ruff pins in lockstep at {pin} "
            f"({pyproject.name} 'ruff=={pin}' == {precommit.name} 'rev: {rev}')"
        )
    return False, (
        f"ruff version drift: {pyproject.name} pins 'ruff=={pin}' but "
        f"{precommit.name} pins the {_RUFF_PRECOMMIT_REPO} hook at "
        f"'rev: {rev}'. Bump both pins to the same version: 'ruff==' in "
        f"{pyproject.name} and the ruff-pre-commit 'rev' in {precommit.name}."
    )


def main(argv: list[str]) -> int:
    args = argv[1:]
    if any(a in ("-h", "--help") for a in args):
        print(__doc__)
        return 0
    if len(args) > 2:
        print(
            "usage: check_ruff_lockstep.py [PYPROJECT] [PRECOMMIT]",
            file=sys.stderr,
        )
        return 2
    pyproject = Path(args[0]) if len(args) >= 1 else _PYPROJECT
    precommit = Path(args[1]) if len(args) >= 2 else _PRECOMMIT
    try:
        ok, message = check(pyproject, precommit)
    except _LockstepError as exc:
        print(f"check_ruff_lockstep: {exc}", file=sys.stderr)
        return 2
    if ok:
        print(message)
        return 0
    print(message, file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
