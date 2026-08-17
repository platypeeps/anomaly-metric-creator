#!/usr/bin/env python3
"""Assert every path listed in ``docs/repomix-map.md`` still exists in the repository.

``docs/repomix-map.md`` is a generated structural map: a metadata-only Repomix
render of the tracked tree, refreshed by ``scripts/update_repomix``. Nothing
regenerates it automatically, so it goes stale whenever files move and the map
does not move with them. Two real occurrences, one week apart, each blocked a
merge:

* a ``task.py archive`` run moved ``.trellis/tasks/<slug>/`` into
  ``.trellis/tasks/archive/<month>/`` and left five map lines pointing at the
  old paths;
* six new ``scripts/`` files were added and never appeared in the map at all.

The first is *structural* rather than accidental: finish-work archives a task
after the map was last generated, so a completion-mode ship strands those
entries by construction every time. This check catches that class at commit
time, where the remedy is one ``scripts/update_repomix`` run, instead of at the
review gate after the archive commit already landed.

Direction and scope
-------------------

This check answers one question: **does every path the map lists still exist?**

It deliberately does *not* answer the reverse — whether a tracked file is
missing from the map. The two directions have very different costs. A path that
appears in the map is by definition not excluded from it, so verifying it needs
no exclusion set at all and cannot produce a false positive. Going the other way
requires knowing every rule that legitimately keeps a tracked file out of the
map, and in this repository those rules come from three unrelated places:

* ``docs/repomix-map.md`` itself, via the ``--ignore`` flag in
  ``scripts/update_repomix``;
* ``.trellis/.template-hashes.json``, matched by a root ``.gitignore`` entry
  even though the file is tracked (so a plain ``git check-ignore`` finds
  nothing; only ``--no-index`` does);
* ``uv.lock``, excluded by Repomix's *built-in* default patterns, which are
  named in no file in this repository.

Reproducing that third set means either depending on the ``repomix`` binary --
which ``scripts/update_repomix`` shows is not always present, since it exits
``127`` without it -- or hand-maintaining a mirror of an upstream list. A mirror
would be a second registry for the same fact, drifting on every Repomix upgrade
with no guard of its own: precisely the failure this check exists to prevent.
That direction is therefore left to a follow-up task with the decision stated,
rather than half-implemented here.

Resolution target
-----------------

An entry resolves if it is a **tracked** path: a file in ``git ls-files``, or a
directory that is the prefix of one. Not a filesystem probe.

A filesystem probe would let untracked local debris mask staleness -- a stale
entry that happens to match a leftover file in one developer's working tree
passes there and fails in CI. The index is identical in both. It is also the
more accurate question: the map describes the tracked, gitignore-respecting
tree, so if a listed path is untracked at this commit, a fresh clone of this
commit genuinely does not have it.

Parsing
-------

Repomix renders the listing as an indented tree inside a fenced block under a
``# Directory Structure`` heading. Each level is exactly two spaces and a
trailing ``/`` marks a directory, so an entry's full path is the stack of
enclosing directory names. This mirrors the parser in the command pack's
``sd-ai-command-pack-review-preflight.mjs`` on purpose: two independent parsers
disagreeing about what the map says would be worse than either alone.

Usage::

    check_repomix_map_freshness.py [MAP]

``MAP`` defaults to the repository-root map; it is overridable so the test suite
can point the check at fixtures. The hook passes no filenames.

Selection note (why the hook is ``always_run``)
-----------------------------------------------

Map staleness is not introduced by editing the map -- it is introduced by moving
or deleting files *elsewhere* while the map stays unchanged. A ``files:``
selected hook keyed on ``docs/repomix-map.md`` would therefore run on exactly
the commits that cannot be stale and skip every commit that can, looking
installed while guarding nothing. The pre-commit hook uses ``always_run: true``
with ``pass_filenames: false`` instead. Do not "fix" it into a ``files:``
selector.

Diagnostics
-----------

Every path in a diagnostic is rendered **relative to the repository root**.
These messages are read in CI logs and pasted into review threads, so an
absolute path leaks the runner's or the author's home directory for no benefit
and makes one finding read differently on every machine. That includes the
messages built from caught exceptions: ``OSError`` renders as
``[Errno 2] No such file or directory: '<absolute path>'``, so those report
``strerror`` rather than the exception whole.

Exit codes:

* ``0`` -- every listed path resolves (the count checked is printed to stdout).
* ``1`` -- one or more entries are stale; each is named on stderr with its
  ``file:line``, along with the regeneration command.
* ``2`` -- a structural problem: the map is missing or unreadable, has no
  directory-structure section, is empty, is malformed (bad indentation, a
  skipped level), names a path escaping the repository root, or sits somewhere
  ``git ls-files`` cannot run. None of these has *shown* the map to be stale, so
  none of them is a ``1``.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path, PurePosixPath

_REPO_ROOT = Path(__file__).resolve().parent.parent
_MAP = _REPO_ROOT / "docs" / "repomix-map.md"

# The heading that opens the generated tree, and the fence lines inside it.
_SECTION_HEADING = re.compile(r"^#\s+Directory Structure\s*$")
_ANY_HEADING = re.compile(r"^#\s")
_FENCE = re.compile(r"^\s*`{3,}\s*$")

# How many stale entries to enumerate before summarizing the rest. A wholesale
# regeneration can strand hundreds; printing all of them buries the diagnostic,
# and printing some of them without saying so implies the list is complete.
_STALE_REPORT_LIMIT = 20

_REGENERATE = "./scripts/update_repomix"

# Ceiling for the one subprocess call. See `_tracked_paths` for why it exists.
_GIT_TIMEOUT_SECONDS = 60


class _MapError(Exception):
    """The map could not be read or parsed (exit 2), as opposed to being stale."""


def _display(path: Path, repo_root: Path) -> str:
    """Render ``path`` relative to ``repo_root`` when it lives inside it.

    Diagnostics are read in CI logs and pasted into review threads, so an
    absolute path leaks the runner's or the author's home directory for no
    benefit — and the same finding then reads differently on every machine.
    Falls back to the path as given when it is genuinely outside the root."""
    try:
        return str(path.resolve().relative_to(repo_root.resolve()))
    except OSError, ValueError:
        return str(path)


def parse_entries(text: str) -> list[tuple[str, int, bool]]:
    """Return ``(path, line_number, is_directory)`` for each entry in the tree.

    ``line_number`` is 1-indexed and points at the line the entry was read from,
    so a diagnostic can cite ``map.md:<line>`` directly. Raises ``_MapError`` for
    a missing section or malformed indentation."""
    lines = text.split("\n")
    start = -1
    for index, line in enumerate(lines):
        if _SECTION_HEADING.match(line):
            start = index + 1
            break
    if start == -1:
        raise _MapError(
            "no '# Directory Structure' section; this does not look like a "
            "Repomix structural map"
        )

    entries: list[tuple[str, int, bool]] = []
    stack: list[str] = []
    for index in range(start, len(lines)):
        line = lines[index]
        if _ANY_HEADING.match(line):
            break
        if not line.strip() or _FENCE.match(line):
            continue

        indent = len(line) - len(line.lstrip())
        if indent % 2 != 0:
            raise _MapError(
                f"line {index + 1} is indented by {indent} space(s), not a "
                f"multiple of two; the map is malformed"
            )
        depth = indent // 2
        if depth > len(stack):
            raise _MapError(
                f"line {index + 1} skips an indentation level; the map is malformed"
            )

        name = line.strip()
        is_directory = name.endswith("/")
        bare = name[:-1] if is_directory else name
        del stack[depth:]
        path = "/".join([*stack, bare])
        entries.append((path, index + 1, is_directory))
        if is_directory:
            stack.append(bare)

    return entries


def _tracked_paths(repo_root: Path) -> tuple[frozenset[str], frozenset[str]]:
    """Return ``(tracked_files, tracked_directories)`` as POSIX-style strings.

    Directories are every prefix of a tracked file, so a directory entry in the
    map resolves when anything tracked lives under it."""
    try:
        completed = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=repo_root,
            capture_output=True,
            check=True,
            # This runs inside every `git commit`. Without a bound, a git that
            # hangs -- an unreachable network filesystem, a stuck index lock --
            # hangs the commit itself with no diagnostic. A ceiling turns that
            # into an exit 2 that says what happened. Generous by design: the
            # call takes milliseconds on this repository, so anything near the
            # ceiling is a fault rather than a slow machine.
            timeout=_GIT_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise _MapError(
            f"git ls-files did not finish within {_GIT_TIMEOUT_SECONDS}s; "
            f"the repository index may be locked or on an unresponsive mount"
        ) from exc
    except OSError as exc:
        raise _MapError(f"cannot run git ls-files: {exc.strerror}") from exc
    except subprocess.CalledProcessError as exc:
        # git writes the useful part to stderr; the command line it echoes back
        # would carry the repository path.
        detail = exc.stderr.decode("utf-8", errors="replace").strip() or "no detail"
        raise _MapError(
            f"git ls-files exited {exc.returncode}; is this a git repository? {detail}"
        ) from exc

    files = {
        entry.decode("utf-8", errors="surrogateescape")
        for entry in completed.stdout.split(b"\0")
        if entry
    }
    directories: set[str] = set()
    for path in files:
        parent = PurePosixPath(path).parent
        while str(parent) != ".":
            directories.add(str(parent))
            parent = parent.parent
    return frozenset(files), frozenset(directories)


def check(map_path: Path, repo_root: Path) -> tuple[bool, str]:
    """Return ``(ok, message)`` for the map's listed paths.

    ``ok`` is True when every entry resolves against the git index. Raises
    ``_MapError`` (exit 2) for structural problems. Single source of truth for
    the comparison and both message shapes."""
    shown = _display(map_path, repo_root)
    try:
        text = map_path.read_text(encoding="utf-8")
    except OSError as exc:
        # `exc` renders as "[Errno 2] No such file or directory: '<path>'", so
        # report the reason without it rather than reintroducing the absolute
        # path `_display` just stripped.
        raise _MapError(f"cannot read {shown}: {exc.strerror}") from exc

    entries = parse_entries(text)
    if not entries:
        raise _MapError(
            f"{shown}: the directory-structure section lists no entries; the "
            f"map is malformed or was generated empty"
        )

    files, directories = _tracked_paths(repo_root)
    known = files | directories

    stale: list[tuple[str, int]] = []
    for path, line, _is_directory in entries:
        # A `..` component would make the membership test meaningless and, in a
        # filesystem-probing variant, would reach outside the repository. A
        # generator never emits one, so treat it as malformed rather than stale.
        if ".." in PurePosixPath(path).parts:
            raise _MapError(
                f"{shown}:{line} lists {path}, which does not stay inside "
                f"the repository; the map is malformed"
            )
        if path not in known:
            stale.append((path, line))

    if not stale:
        return True, (
            f"repomix map is current: all {len(entries)} listed path(s) in "
            f"{map_path.name} resolve to tracked files or directories"
        )

    reported = stale[:_STALE_REPORT_LIMIT]
    parts = [
        f"{shown}:{line} lists {path}, which is not tracked at this commit"
        for path, line in reported
    ]
    suppressed = len(stale) - len(reported)
    if suppressed:
        parts.append(f"...and {suppressed} further stale path(s) not listed")
    return False, (
        "repomix map is stale:\n"
        + "\n".join(f"  {part}" for part in parts)
        + f"\nThe map no longer describes the tracked tree -- most often because "
        f"files moved (a `task.py archive` run) without the map moving with "
        f"them. Regenerate it with `{_REGENERATE}` and commit the result "
        f"alongside the change that moved them."
    )


def main(argv: list[str]) -> int:
    args = argv[1:]
    if any(a in ("-h", "--help") for a in args):
        print(__doc__)
        return 0
    if len(args) > 1:
        print(
            "usage: check_repomix_map_freshness.py [MAP]",
            file=sys.stderr,
        )
        return 2

    map_path = Path(args[0]) if args else _MAP
    repo_root = map_path.resolve().parent.parent if args else _REPO_ROOT
    try:
        ok, message = check(map_path, repo_root)
    except _MapError as exc:
        print(f"check_repomix_map_freshness: {exc}", file=sys.stderr)
        return 2
    if ok:
        print(message)
        return 0
    print(message, file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
