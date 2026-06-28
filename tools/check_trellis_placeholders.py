#!/usr/bin/env python3
"""Reject unfinished Trellis task and workspace placeholders.

Finish-work output is committed to the repository, so template text such as
``(Add details)`` and ``(Add test results)`` should not survive to ``main``.
This lint is scoped to Trellis workspace/task artifacts; examples in skills and
reference docs are intentionally outside the pre-commit hook's file pattern.
For workspace journals, the lint also checks completed sessions for leftover
template text and verifies that journal commit lists match the workspace index.

A line can be exempted with a trailing
``# trellis-placeholder-lint: allow`` marker when a task genuinely needs to
quote placeholder text.

Exit codes:

* ``0`` - no unfinished Trellis placeholders.
* ``1`` - at least one placeholder or journal consistency error was found.
* ``2`` - argument or I/O error.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

_ALLOW_MARKER = "# trellis-placeholder-lint: allow"
_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("template main-changes placeholder", re.compile(r"\(Add details\)")),
    ("template testing placeholder", re.compile(r"\(Add test results\)")),
    ("unfilled TODO marker", re.compile(r"\bTODO:\s*fill\b", re.IGNORECASE)),
    ("unfilled prose marker", re.compile(r"\bTo be filled\b", re.IGNORECASE)),
    ("uppercase placeholder marker", re.compile(r"\bPLACEHOLDER\b")),
)
_COMPLETED_PLACEHOLDER_LABELS = {
    "template main-changes placeholder",
    "template testing placeholder",
}
_COMPLETED_STATUS_RE = re.compile(r"^\[OK\]\s+\*\*Completed\*\*\s*$")
_COMMIT_RE = re.compile(r"`([0-9a-f]{7,40})`", re.IGNORECASE)
_INDEX_SESSION_ROW_RE = re.compile(r"^\|\s*(?P<session>\d+)\s*\|")
_JOURNAL_SESSION_RE = re.compile(r"^## Session (?P<session>\d+):\s*(?P<title>.+?)\s*$")


def _line_is_exempted(line: str) -> bool:
    return line.rstrip().endswith(_ALLOW_MARKER)


def _is_journal_file(path: Path) -> bool:
    return path.suffix == ".md" and path.name.startswith("journal-")


def _iter_journal_sessions(text: str) -> list[tuple[int, str, int, list[str]]]:
    """Return journal sessions as (number, title, start line, lines)."""

    lines = text.splitlines()
    starts: list[tuple[int, re.Match[str]]] = []
    for lineno, line in enumerate(lines, start=1):
        match = _JOURNAL_SESSION_RE.match(line)
        if match:
            starts.append((lineno, match))

    sessions: list[tuple[int, str, int, list[str]]] = []
    for index, (start_lineno, match) in enumerate(starts):
        end_lineno = starts[index + 1][0] - 1 if index + 1 < len(starts) else len(lines)
        sessions.append(
            (
                int(match.group("session")),
                match.group("title").strip(),
                start_lineno,
                lines[start_lineno - 1 : end_lineno],
            )
        )
    return sessions


def _session_is_completed(lines: list[str]) -> bool:
    return any(_COMPLETED_STATUS_RE.match(line.strip()) for line in lines)


def _completed_journal_template_placeholders(
    path: Path, text: str
) -> list[tuple[int, int, str, str, re.Match[str], str]]:
    if not _is_journal_file(path):
        return []

    placeholders: list[tuple[int, int, str, str, re.Match[str], str]] = []
    for session_number, session_title, start_lineno, lines in _iter_journal_sessions(text):
        if not _session_is_completed(lines):
            continue

        for offset, line in enumerate(lines):
            if _line_is_exempted(line):
                continue
            lineno = start_lineno + offset
            for label, pattern in _PATTERNS:
                if label not in _COMPLETED_PLACEHOLDER_LABELS:
                    continue
                match = pattern.search(line)
                if match:
                    placeholders.append(
                        (lineno, session_number, session_title, label, match, line)
                    )
    return placeholders


def _check_completed_journal_placeholders(path: Path, text: str) -> list[str]:
    violations: list[str] = []
    for (
        lineno,
        session_number,
        session_title,
        label,
        match,
        _line,
    ) in _completed_journal_template_placeholders(path, text):
        violations.append(
            f"{path}:{lineno}:{match.start() + 1}: completed journal "
            f"session {session_number} ({session_title}) contains {label}: "
            f"{match.group(0)!r}"
        )
    return violations


def _check_file(path: Path) -> list[str]:
    violations: list[str] = []
    is_journal = _is_journal_file(path)
    text = path.read_text(encoding="utf-8")
    completed_placeholder_lines = {
        lineno
        for lineno, *_rest in _completed_journal_template_placeholders(path, text)
    }
    for lineno, line in enumerate(text.splitlines(), start=1):
        if _line_is_exempted(line):
            continue
        for label, pattern in _PATTERNS:
            if (
                is_journal
                and lineno in completed_placeholder_lines
                and label in _COMPLETED_PLACEHOLDER_LABELS
            ):
                continue
            match = pattern.search(line)
            if match:
                violations.append(
                    f"{path}:{lineno}:{match.start() + 1}: {label}: "
                    f"{match.group(0)!r}"
                )
    violations.extend(_check_completed_journal_placeholders(path, text))
    return violations


def _workspace_root_for(path: Path) -> Path | None:
    parts = path.parts
    for index in range(len(parts) - 2):
        if parts[index] == ".trellis" and parts[index + 1] == "workspace":
            return Path(*parts[: index + 3])
    return None


def _workspace_roots(paths: list[Path]) -> list[Path]:
    roots: list[Path] = []
    for path in paths:
        root = _workspace_root_for(path)
        if root is not None and root not in roots:
            roots.append(root)
    return roots


def _format_commits(commits: list[str]) -> str:
    if not commits:
        return "(none)"
    return ", ".join(f"`{commit}`" for commit in commits)


def _parse_index_commits(path: Path) -> dict[int, tuple[list[str], int]]:
    sessions: dict[int, tuple[list[str], int]] = {}
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not _INDEX_SESSION_ROW_RE.match(line):
            continue

        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 4 or not cells[0].isdigit():
            continue

        sessions[int(cells[0])] = (_COMMIT_RE.findall(cells[3]), lineno)
    return sessions


def _parse_journal_commits(path: Path) -> dict[int, tuple[list[str], int, Path]]:
    sessions: dict[int, tuple[list[str], int, Path]] = {}
    for session_number, _session_title, start_lineno, lines in _iter_journal_sessions(
        path.read_text(encoding="utf-8")
    ):
        commits: list[str] = []
        in_git_commits = False
        for line in lines:
            stripped = line.strip()
            if stripped == "### Git Commits":
                in_git_commits = True
                continue
            if in_git_commits and stripped.startswith("### "):
                break
            if in_git_commits:
                commits.extend(_COMMIT_RE.findall(line))
        sessions[session_number] = (commits, start_lineno, path)
    return sessions


def _check_workspace_journal_commit_consistency(root: Path) -> list[str]:
    index_path = root / "index.md"
    journal_paths = sorted(root.glob("journal-*.md"))
    if not index_path.exists() and not journal_paths:
        return []
    if not index_path.exists():
        return [
            f"{root}: workspace index.md is missing; cannot verify journal/index "
            "commit-list consistency"
        ]
    if not journal_paths:
        return [
            f"{index_path}: no journal-*.md files found; cannot verify journal/index "
            "commit-list consistency"
        ]

    index_sessions = _parse_index_commits(index_path)
    journal_sessions: dict[int, tuple[list[str], int, Path]] = {}
    for journal_path in journal_paths:
        journal_sessions.update(_parse_journal_commits(journal_path))

    violations: list[str] = []
    for session_number in sorted(index_sessions.keys() | journal_sessions.keys()):
        index_entry = index_sessions.get(session_number)
        journal_entry = journal_sessions.get(session_number)

        if index_entry is None and journal_entry is not None:
            _journal_commits, journal_lineno, journal_path = journal_entry
            violations.append(
                f"{journal_path}:{journal_lineno}: session {session_number} is "
                f"missing from {index_path}"
            )
            continue
        if journal_entry is None and index_entry is not None:
            _index_commits, index_lineno = index_entry
            violations.append(
                f"{index_path}:{index_lineno}: session {session_number} is listed "
                "in the workspace index but no matching journal session was found"
            )
            continue

        assert index_entry is not None
        assert journal_entry is not None
        index_commits, index_lineno = index_entry
        journal_commits, journal_lineno, journal_path = journal_entry
        if index_commits != journal_commits:
            violations.append(
                f"{index_path}:{index_lineno}: journal/index commit list mismatch "
                f"for session {session_number}: index has {_format_commits(index_commits)}; "
                f"{journal_path}:{journal_lineno} has {_format_commits(journal_commits)}"
            )
    return violations


def main(argv: list[str]) -> int:
    args = argv[1:]
    if not args:
        print("usage: check_trellis_placeholders.py <trellis-artifact>...", file=sys.stderr)
        return 2

    violations: list[str] = []
    paths: list[Path] = []
    for arg in args:
        path = Path(arg)
        paths.append(path)
        if not path.exists():
            print(f"check_trellis_placeholders: no such file: {path}", file=sys.stderr)
            return 2
        if not path.is_file():
            continue
        try:
            violations.extend(_check_file(path))
        except (OSError, UnicodeError) as exc:
            print(f"check_trellis_placeholders: cannot read {path}: {exc}", file=sys.stderr)
            return 2

    for root in _workspace_roots(paths):
        try:
            violations.extend(_check_workspace_journal_commit_consistency(root))
        except (OSError, UnicodeError) as exc:
            print(
                "check_trellis_placeholders: cannot read workspace journal/index "
                f"files under {root}: {exc}",
                file=sys.stderr,
            )
            return 2

    if violations:
        print("\n".join(violations), file=sys.stderr)
        print(
            "\nTrellis task and workspace artifacts must not commit unfinished "
            "template placeholders, and workspace journal commit lists must match "
            "the index. Fill in the section, fix the commit list, or add the "
            f"trailing {_ALLOW_MARKER!r} marker for a deliberate quotation.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
