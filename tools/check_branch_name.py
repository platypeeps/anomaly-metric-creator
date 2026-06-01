#!/usr/bin/env python3
"""Reject branch names that leak the project's ticket literal.

PRs #47–#77 and #86 all shipped a head ref shaped like
``sdelmas/ver-<N>-…``, republishing the internal ticket identifier
in the public PR URL even after PR titles had been cleaned up.
VER-702 adds this lint as the structural fix: any branch name
matching ``(?i)(^|\\b)ver-\\d+`` is rejected at push time so the
leak cannot reach GitHub.

The lint is wired into ``.pre-commit-config.yaml`` as a
``pre-push`` stage hook (install via
``pre-commit install --hook-type pre-push``), invoked with
``--current`` so each push checks the branch the developer is about
to publish. The script also supports literal branch-name arguments
and the raw git pre-push stdin protocol, so it can be called
directly from a hand-written ``.git/hooks/pre-push`` or from CI.

Three invocation modes:

* ``check_branch_name.py <name>...`` — check each literal branch
  name. Used by the test suite and by ad-hoc CLI runs ("is this
  name allowed?").
* ``check_branch_name.py --current`` — read the current branch via
  ``git symbolic-ref --short HEAD`` and check it. Used by the
  pre-commit pre-push hook with ``pass_filenames: false`` so the
  hook does not depend on the diff. A detached HEAD has no
  symbolic ref and is treated as "nothing to check" (exit 0),
  since there is no branch about to be pushed.
* ``check_branch_name.py -`` — read git's pre-push protocol from
  stdin and check each pushed branch. Each line carries
  ``<local-ref> <local-sha> <remote-ref> <remote-sha>``; lines
  whose ``<local-sha>`` is all zeros are deletions (no local
  branch to lint) and are skipped. Refs that are not
  ``refs/heads/...`` (e.g. ``refs/tags/v1.0.0``) are skipped too.
  This is the mode a hand-rolled ``.git/hooks/pre-push`` would
  pipe into.

Pattern: ``(?i)(^|\\b)ver-\\d+`` — case-insensitive, anchored to
start-of-string OR a word boundary, requires at least one digit
after the dash. The digit requirement keeps generic ``ver-``
prefixes (``verify-something``, ``ver-test-branch``) legal —
the leak is specifically the ticket form ``ver-<N>``. The word
boundary keeps ``fever-pitch`` and ``discover-foo`` legal, since
neither has a boundary before ``ver``. The case-insensitive flag
covers ``ver-655`` / ``VER-655`` / ``Ver-655`` uniformly.

Exit codes:

* ``0`` — every checked branch is clean (also: detached HEAD,
  empty stdin, all-deletion stdin, tag-only push).
* ``1`` — at least one branch matches the leak pattern; one
  violation line per match is written to stderr followed by a
  one-line footer naming the policy.
* ``2`` — argument error (no args, unknown flag), I/O error
  reading git refs, or git itself not installed.

There is no per-branch escape hatch. Unlike the role-name lint —
which protects external-facing text that may legitimately discuss
internal labels in docstrings or test fixtures — a branch name has
no legitimate reason to embed a ticket literal. The structural fix
is to choose a descriptive branch name instead.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

# Case-insensitive, start-of-string OR word boundary, then ``ver-``
# followed by at least one digit. See module docstring for the full
# rationale on each anchor.
_PATTERN = re.compile(r"(?i)(?:^|\b)ver-\d+")

# All-zero sha indicates a deletion in git's pre-push protocol.
# https://git-scm.com/docs/githooks#_pre_push
_NULL_SHA_RE = re.compile(r"^0+$")

# Branch refs only — tag pushes use ``refs/tags/...`` and have
# nothing to do with feature-branch naming.
_BRANCH_REF_PREFIX = "refs/heads/"


def _check_name(name: str) -> str | None:
    """Return a diagnostic line for ``name`` if it leaks, else
    ``None``. Single source of truth for the lint rule so the three
    invocation modes cannot drift on the regex or the message
    format."""
    match = _PATTERN.search(name)
    if match is None:
        return None
    return (
        f"branch name '{name}' embeds ticket literal "
        f"'{match.group(0)}' at column {match.start() + 1} — "
        "rename the branch to a descriptive label (no ticket "
        "identifiers) before pushing."
    )


def _current_branch() -> str | None:
    """Return the current branch's short name, or ``None`` if HEAD
    is detached. Raises ``OSError`` (re-raised by callers as exit 2)
    if git itself is unavailable."""
    result = subprocess.run(
        ["git", "symbolic-ref", "--quiet", "--short", "HEAD"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        # `--quiet` makes ``symbolic-ref`` exit 1 (not print) on a
        # detached HEAD. Any other non-zero is a real git failure
        # that should surface as exit 2.
        if result.returncode == 1 and not result.stderr.strip():
            return None
        raise OSError(
            f"git symbolic-ref failed (exit {result.returncode}): "
            f"{result.stderr.strip() or '<no stderr>'}"
        )
    return result.stdout.strip() or None


def _parse_pre_push_stdin(text: str) -> list[str]:
    """Extract local branch short names from git's pre-push stdin.

    Each line: ``<local-ref> <local-sha> <remote-ref> <remote-sha>``.
    Deletions (all-zero local sha) and non-branch refs (tags etc.)
    are skipped. Malformed lines (< 4 tokens) are silently ignored:
    the protocol guarantees the 4-token shape, but trailing newlines
    and CR/LF artifacts can produce empty splits in practice."""
    branches: list[str] = []
    for raw in text.splitlines():
        tokens = raw.split()
        if len(tokens) < 4:
            continue
        local_ref, local_sha, _remote_ref, _remote_sha = tokens[:4]
        if _NULL_SHA_RE.match(local_sha):
            continue
        if not local_ref.startswith(_BRANCH_REF_PREFIX):
            continue
        branches.append(local_ref[len(_BRANCH_REF_PREFIX):])
    return branches


def _print_violations(violations: list[str]) -> None:
    print("\n".join(violations), file=sys.stderr)
    print(
        "\nBranch names must not embed ticket literals (ver-<N> / "
        "VER-<N>). Pick a descriptive branch name — the PR title and "
        "description carry the ticket reference instead. Policy lives "
        "in CLAUDE.md under 'Branch-name lint'.",
        file=sys.stderr,
    )


def _usage(stream) -> None:
    print(
        "usage: check_branch_name.py [--current | - | <branch>...]\n"
        "  --current   read the current git branch and check it\n"
        "  -           read git pre-push protocol lines from stdin\n"
        "  <branch>    one or more literal branch names to check",
        file=stream,
    )


def main(argv: list[str]) -> int:
    args = argv[1:]
    if not args:
        _usage(sys.stderr)
        return 2

    # Mode dispatch: --current and - are exclusive single-flag modes.
    # Anything else is treated as a list of literal branch names.
    if args[0] == "--current":
        if len(args) != 1:
            _usage(sys.stderr)
            return 2
        try:
            branch = _current_branch()
        except OSError as exc:
            print(f"check_branch_name: {exc}", file=sys.stderr)
            return 2
        if branch is None:
            return 0
        names = [branch]
    elif args[0] == "-":
        if len(args) != 1:
            _usage(sys.stderr)
            return 2
        try:
            text = sys.stdin.read()
        except OSError as exc:
            print(f"check_branch_name: stdin unreadable: {exc}", file=sys.stderr)
            return 2
        names = _parse_pre_push_stdin(text)
    elif any(a.startswith("-") for a in args):
        # Reject unknown flags so a typo (``--currrent``) does not
        # silently fall through to the literal-name branch.
        _usage(sys.stderr)
        return 2
    else:
        names = list(args)

    violations: list[str] = []
    for name in names:
        diag = _check_name(name)
        if diag is not None:
            violations.append(diag)

    if violations:
        _print_violations(violations)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
