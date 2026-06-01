#!/usr/bin/env python3
"""Forbid internal role-name leaks in text destined for GitHub.

PR #86 shipped two approval comments that leaked internal-role
vocabulary that should never reach an external GitHub thread. VER-701
edited the historical leaks and adds this literal-match lint so
future commits and ad-hoc comment bodies are checked structurally.

The canonical list of forbidden labels lives in the
``_FORBIDDEN_LABELS`` tuple below. Whole-word, case-sensitive matching
keeps incidental substrings such as ``boardroom`` or ``CEO123`` from
tripping the check — only the labels themselves count.

Two invocation modes:

* ``python tools/check_role_name_leaks.py <path>...`` — scan each
  file, skipping anything under ``.git/``, ``.venv/``,
  ``node_modules/``, anything matching ``*.lock``, and any path the OS
  reports as binary (decode failure under UTF-8). Used by the
  ``.pre-commit-config.yaml`` ``role-name-leaks`` hook against the
  set of staged files.
* ``python tools/check_role_name_leaks.py -`` — read a single text
  buffer from stdin and check it. Use this before piping a comment
  body through ``gh pr comment --body-file …`` or ``gh issue comment
  --body-file …``::

      python tools/check_role_name_leaks.py - < /tmp/body.md \
          && gh pr comment 86 --body-file /tmp/body.md

  Exits 0 on a clean body, 1 if a label appears, naming the offending
  label and the line it appeared on. The non-zero exit code chains with
  ``&&`` so the ``gh`` call only runs when the body is clean.

Exit codes:
* ``0`` — every scanned input is clean.
* ``1`` — at least one label appeared; one line per match is written to
  stderr.
* ``2`` — argument or I/O error (e.g. unreadable path that is not a
  binary skip).

The lint reports every match it finds in a single run (it does not stop
at the first violation) so reviewers see the full list rather than
playing whack-a-mole one fix at a time.

Escape hatch: a line whose right-stripped content ends with the
literal marker ``# role-name-lint: allow`` is skipped wholesale. The
trailing-only rule is deliberate — a mid-line occurrence (whether
accidental or intentional) must not silence the lint on the rest of
the line. Use sparingly: the two known legitimate use cases are the
``_FORBIDDEN_LABELS`` tuple below (which must list the labels
verbatim) and any docs that genuinely need to discuss the forbidden
vocabulary. The marker is matched against the line's trailing
content rather than a Python ``tokenize`` comment so it works
across Markdown, YAML, and any other text format pre-commit hands to
the script.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

_FORBIDDEN_LABELS: tuple[str, ...] = (  # role-name-lint: allow
    "Lead Engineer",  # role-name-lint: allow
    "Code Reviewer",  # role-name-lint: allow
    "Release Engineer",  # role-name-lint: allow
    "CEO",  # role-name-lint: allow
    "Board",  # role-name-lint: allow
)

# Word-boundary, case-sensitive. ``\b`` keeps ``boardroom`` and
# ``CEO123`` from matching, while the case-sensitive pattern keeps the
# lower-case noun "board" (used freely in agent prose to mean "the
# governance board") from masking the upper-case label when it appears
# in the wrong place.  # role-name-lint: allow
_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(label) for label in _FORBIDDEN_LABELS) + r")\b"
)

# Paths the lint silently skips. ``.git`` and ``.venv`` are the obvious
# tooling caches; ``node_modules`` covers any future JS lint config;
# ``*.lock`` skips poetry / npm / cargo lock files that may contain
# substrings outside our control. The lint operates on text that a
# human or agent authored — generated lock-file content is not in
# scope.
_SKIP_DIR_PARTS: frozenset[str] = frozenset({".git", ".venv", "node_modules"})
_SKIP_SUFFIXES: frozenset[str] = frozenset({".lock"})

# Lines carrying this literal substring are skipped by ``_scan_text``.
# See the module docstring's "Escape hatch" paragraph for the policy.
_NOQA_MARKER = "# role-name-lint: allow"


def _should_skip(path: Path) -> bool:
    if path.suffix in _SKIP_SUFFIXES:
        return True
    return any(part in _SKIP_DIR_PARTS for part in path.parts)


def _line_is_exempted(line: str) -> bool:
    """Return True iff ``line`` carries the *trailing* allow marker.

    The marker only exempts a line when it appears at the right edge
    (after any trailing whitespace). A mid-line occurrence — e.g. the
    marker inside a string literal followed by more code — does not
    silence the lint, since otherwise an accidental occurrence could
    disable the check on real leaks further along the same line.
    """
    return line.rstrip().endswith(_NOQA_MARKER)


def _scan_text(label: str, text: str) -> list[str]:
    """Return one violation line per match of a forbidden label.

    ``label`` is the human-facing path or stream name printed in the
    diagnostic; the scanner itself does not open files. A line whose
    right-stripped content ends with ``_NOQA_MARKER`` is skipped
    wholesale (see the module-level "Escape hatch" note).
    """
    violations: list[str] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if _line_is_exempted(line):
            continue
        for match in _PATTERN.finditer(line):
            violations.append(
                f"{label}:{lineno}:{match.start() + 1}: forbidden internal "
                f"role name '{match.group(1)}' — replace with a neutral "
                "reference (e.g. 'reviewer', 'maintainer', 'Sven Delmas') "
                "before posting externally."
            )
    return violations


def _scan_path(path: Path) -> tuple[list[str], list[str]]:
    """Scan a single file path.

    Returns ``(violations, unreadable)`` where ``violations`` is the
    list of label-leak diagnostic lines (drives exit ``1``) and
    ``unreadable`` is the list of I/O-error diagnostic lines (drives
    exit ``2``). Binary content (UTF-8 decode failure) is a silent
    skip and contributes to neither list.
    """
    if _should_skip(path):
        return [], []
    if not path.is_file():
        return [], []
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        # Binary file — not in scope for a text-leak lint. Pre-commit
        # passes file paths verbatim and may include images / fonts on
        # repos that grow them; skip silently rather than emit a noisy
        # diagnostic that would force every binary-add commit to add
        # an exemption.
        return [], []
    except OSError as exc:
        print(f"{path}: read failed: {exc}", file=sys.stderr)
        return [], [f"{path}:0:0: unreadable"]
    return _scan_text(str(path), text), []


def _scan_stdin() -> tuple[list[str], list[str]]:
    """Scan stdin as a single buffer.

    Used for the ``gh pr comment --body-file …`` pre-flight check.
    Same ``(violations, unreadable)`` return shape as ``_scan_path``.
    """
    try:
        text = sys.stdin.read()
    except OSError as exc:
        print(f"<stdin>: read failed: {exc}", file=sys.stderr)
        return [], ["<stdin>:0:0: unreadable"]
    return _scan_text("<stdin>", text), []


def main(argv: list[str]) -> int:
    args = argv[1:]
    if not args:
        print(
            "usage: check_role_name_leaks.py [-] <path>...\n"
            "  -        read stdin and check it (use for `gh ... --body-file`)\n"
            "  <path>   files to scan (directories are not recursed)",
            file=sys.stderr,
        )
        return 2
    violations: list[str] = []
    unreadable: list[str] = []
    for raw in args:
        v, u = _scan_stdin() if raw == "-" else _scan_path(Path(raw))
        violations.extend(v)
        unreadable.extend(u)
    # Print every diagnostic the run produced. The footer below is
    # specific to label leaks; it only fires when ``violations`` is
    # non-empty so an "unreadable"-only run does not falsely advertise
    # a role-name match.
    if violations or unreadable:
        print("\n".join(violations + unreadable), file=sys.stderr)
    if violations:
        print(
            "\nInternal role names must not appear in external-facing text "
            "(PR titles/bodies, issue comments, docs). Use a neutral "
            "reference (e.g. 'reviewer', 'Sven Delmas') instead.",
            file=sys.stderr,
        )
    # Exit-code priority: I/O errors (exit 2) take precedence over
    # label leaks (exit 1) so the caller surfaces the structural
    # failure first; a clean run exits 0.
    if unreadable:
        return 2
    if violations:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
