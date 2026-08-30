#!/usr/bin/env python3
"""Reject work-item acceptance-criteria commands that cannot do what they claim.

Acceptance criteria in ``docs/work/**/*.md`` are quoted as runnable shell
commands with a stated expected output, so a command that cannot produce that
output is a criterion nothing can fail.  Two such defects are mechanical:

``grep -c`` against more than one file
    A multi-file ``grep -c`` prints one ``file:count`` line per searched file,
    so its output can never be the single ``0`` a criterion claims.  Use
    ``grep -rn`` and state a line count, or point ``-c`` at exactly one file.

GNU-only escapes in a portable-looking pattern
    ``\\s``, ``\\S``, ``\\d``, ``\\D``, ``\\w``, and ``\\W`` are GNU/PCRE
    extensions.  Stock BSD ``grep`` matches them as the literal letter, so the
    command silently reports the wrong answer rather than failing.  Use a POSIX
    class such as ``[[:space:]]``, or pass ``-P``.

The third failure mode in this family -- a criterion whose prose claim is wider
than the command behind it, such as "one derivation remains" checked by a grep
that only constrains location -- is not mechanically detectable.  It is a prose
rule; see ``docs/spec/amc/backend/documentation-review.md`` § Backlog and
Follow-Up Ownership.

Only ``grep``/``egrep``/``fgrep``/``rg``/``ugrep`` commands inside Markdown
inline-code spans and fenced code blocks are inspected; surrounding prose is
ignored, so a criterion may discuss ``\\s`` as long as it does not run it.  The
count rule fires only when the counting command ends its pipeline, because a
``grep -c`` piped onward is not itself the claimed output.

Escape hatch: put ``<!-- criteria-lint: allow -->`` on the offending line.

Exit codes: 0 clean, 1 violations, 2 input/read errors.
"""

from __future__ import annotations

import re
import shlex
import sys
from pathlib import Path

_ALLOW_MARKER = "<!-- criteria-lint: allow -->"

_GREP_COMMANDS = frozenset({"grep", "egrep", "fgrep", "rg", "ugrep"})

# Short flags that consume the rest of their cluster (or the next token) as a
# value, so the token after them is not a pattern or a path operand.
_VALUE_SHORT_FLAGS = frozenset("ABCDdefm")

# Long flags spelled without `=` that consume the following token.
_VALUE_LONG_FLAGS = frozenset(
    {"--regexp", "--file", "--max-count", "--after-context", "--before-context", "--context"}
)

_GNU_ONLY_ESCAPES = ("\\s", "\\S", "\\d", "\\D", "\\w", "\\W")

# Operators that end a statement, as opposed to `|`, which passes output on.
_STATEMENT_OPERATORS = frozenset({";", "&&", "||", "&"})

_FENCE_RE = re.compile(r"^\s*(```+|~~~+)")
_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
_GLOB_CHARS = frozenset("*?[")


class _GrepInvocation:
    """One parsed grep-family command from a pipeline segment.

    Patterns arrive in four shapes -- positional, ``-e VALUE``, ``-eVALUE``, and
    ``--regexp[=]VALUE`` -- and every one of them is collected, so the escape
    check does not depend on which spelling the criterion happened to use.
    """

    def __init__(self, tokens: list[str]) -> None:
        self.name = tokens[0]
        self.count_mode = False
        self.recursive = False
        self.perl_mode = False
        self.patterns: list[str] = []
        self.operands: list[str] = []
        self._parse(tokens[1:])

    def _parse(self, rest: list[str]) -> None:
        pattern_from_flag = False
        index = 0
        while index < len(rest):
            token = rest[index]
            index += 1
            if token == "--":
                self.operands.extend(rest[index:])
                break
            if token.startswith("--"):
                name, separator, inline_value = token.partition("=")
                takes_value = name in _VALUE_LONG_FLAGS
                value = inline_value if separator else None
                if takes_value and value is None and index < len(rest):
                    value = rest[index]
                    index += 1
                if name == "--count":
                    self.count_mode = True
                elif name in {"--recursive", "--dereference-recursive"}:
                    self.recursive = True
                elif name == "--perl-regexp":
                    self.perl_mode = True
                elif name in {"--regexp", "--file"}:
                    pattern_from_flag = True
                    if name == "--regexp" and value is not None:
                        self.patterns.append(value)
                continue
            if token.startswith("-") and token != "-":
                consumed_value = False
                for position, char in enumerate(token[1:], start=1):
                    if char == "c":
                        self.count_mode = True
                    elif char in {"r", "R"}:
                        self.recursive = True
                    elif char == "P":
                        self.perl_mode = True
                    if char in _VALUE_SHORT_FLAGS:
                        # The value is the cluster remainder, else the next token.
                        if position == len(token) - 1:
                            value = rest[index] if index < len(rest) else None
                            index += 1
                        else:
                            value = token[position + 1 :]
                        if char in {"e", "f"}:
                            pattern_from_flag = True
                            if char == "e" and value is not None:
                                self.patterns.append(value)
                        consumed_value = True
                        break
                if consumed_value:
                    continue
                continue
            if not self.patterns and not pattern_from_flag:
                self.patterns.append(token)
            else:
                self.operands.append(token)

    def path_operands(self) -> list[str]:
        """Operands that plausibly name a file or directory.

        Criteria often append pseudo-shell commentary (``grep -c pat file == 0``),
        and counting ``==`` and ``0`` as searched files would misdiagnose that as
        a multi-file search.  A path operand carries a separator, a suffix, or a
        glob character.
        """
        return [
            operand
            for operand in self.operands
            if "/" in operand or "." in operand or (_GLOB_CHARS & set(operand))
        ]

    def searches_many_files(self) -> bool:
        if self.recursive:
            return True
        operands = self.path_operands()
        if len(operands) >= 2:
            return True
        return any(operand.endswith("/") or (_GLOB_CHARS & set(operand)) for operand in operands)

    def gnu_only_escapes(self) -> list[str]:
        if self.perl_mode:
            return []
        return [
            escape
            for escape in _GNU_ONLY_ESCAPES
            if any(escape in pattern for pattern in self.patterns)
        ]


def _pipelines(command: str) -> list[list[list[str]]]:
    """Split into statements, then each statement into pipeline segments.

    Tokenizing happens **before** splitting, so a ``|`` inside a quoted regex
    alternation stays part of its pattern instead of being read as a shell
    pipe. ``;``, ``&&``, ``&``, and ``||`` end a statement, so the grep before
    one of them is still the claimed output of its own command; only ``|``
    passes output on. An unlexable command yields nothing rather than raising:
    task Markdown holds plenty of backticked prose that is not a command.
    """
    lexer = shlex.shlex(command, posix=True, punctuation_chars="|;&")
    lexer.whitespace_split = True
    try:
        tokens = list(lexer)
    except ValueError:
        return []

    pipelines: list[list[list[str]]] = []
    segments: list[list[str]] = [[]]
    for token in tokens:
        if token in _STATEMENT_OPERATORS:
            pipelines.append(segments)
            segments = [[]]
        elif token == "|":
            segments.append([])
        else:
            segments[-1].append(token)
    pipelines.append(segments)
    return pipelines


def _candidate_commands(lines: list[str]) -> list[tuple[int, str]]:
    """Return ``(line number, command text)`` for every code span and fence line."""
    found: list[tuple[int, str]] = []
    fence: str | None = None
    for number, line in enumerate(lines, start=1):
        match = _FENCE_RE.match(line)
        if match is not None:
            marker = match.group(1)[0] * 3
            if fence is None:
                fence = marker
            elif marker == fence:
                fence = None
            continue
        if fence is not None:
            found.append((number, line.strip().lstrip("$ ").strip()))
            continue
        found.extend((number, span) for span in _INLINE_CODE_RE.findall(line))
    return found


def _check_command(path: Path, number: int, command: str) -> list[str]:
    violations: list[str] = []
    for segments in _pipelines(command):
        for position, tokens in enumerate(segments):
            if not tokens or tokens[0] not in _GREP_COMMANDS:
                continue
            grep = _GrepInvocation(tokens)
            is_last = position == len(segments) - 1
            violations.extend(_grep_violations(path, number, command, grep, is_last))
    return violations


def _grep_violations(
    path: Path, number: int, command: str, grep: _GrepInvocation, is_last: bool
) -> list[str]:
    violations: list[str] = []
    if grep.count_mode and is_last and grep.searches_many_files():
        violations.append(
            f"{path}:{number}: `{command}` counts across more than one file; a "
            "multi-file 'grep -c' prints one 'file:count' line per file, so it "
            "cannot return a single count. Use 'grep -rn' and state a line "
            "count, or point '-c' at exactly one file."
        )
    for escape in grep.gnu_only_escapes():
        violations.append(
            f"{path}:{number}: `{command}` uses the GNU/PCRE escape '{escape}'; "
            "stock BSD grep matches it as a literal letter and silently reports "
            "the wrong answer. Use a POSIX class such as '[[:space:]]', or "
            "pass '-P'."
        )
    return violations


def _check(path: Path) -> list[str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    violations: list[str] = []
    for number, command in _candidate_commands(lines):
        if _ALLOW_MARKER in lines[number - 1]:
            continue
        violations.extend(_check_command(path, number, command))
    return violations


def _markdown_files(path: Path) -> list[Path]:
    if path.is_dir():
        return sorted(candidate for candidate in path.rglob("*.md") if candidate.is_file())
    return [path] if path.suffix == ".md" else []


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(
            "usage: check_task_criteria_commands.py <task.md|directory>...",
            file=sys.stderr,
        )
        return 2

    files: list[Path] = []
    for raw in argv[1:]:
        path = Path(raw)
        if not path.exists():
            print(f"check_task_criteria_commands: no such path: {path}", file=sys.stderr)
            return 2
        if not path.is_dir() and (not path.is_file() or path.suffix != ".md"):
            print(
                "check_task_criteria_commands: expected a Markdown file or "
                f"directory: {path}",
                file=sys.stderr,
            )
            return 2
        files.extend(_markdown_files(path))

    violations: list[str] = []
    for path in sorted(set(files)):
        try:
            violations.extend(_check(path))
        except (OSError, UnicodeError) as exc:
            print(f"check_task_criteria_commands: {exc}", file=sys.stderr)
            return 2

    if violations:
        print("\n".join(violations), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
