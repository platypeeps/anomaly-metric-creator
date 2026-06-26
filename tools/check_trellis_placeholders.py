#!/usr/bin/env python3
"""Reject unfinished Trellis task and workspace placeholders.

Finish-work output is committed to the repository, so template text such as
``(Add details)`` and ``(Add test results)`` should not survive to ``main``.
This lint is scoped to Trellis workspace/task artifacts; examples in skills and
reference docs are intentionally outside the pre-commit hook's file pattern.

A line can be exempted with a trailing
``# trellis-placeholder-lint: allow`` marker when a task genuinely needs to
quote placeholder text.

Exit codes:

* ``0`` - no unfinished Trellis placeholders.
* ``1`` - at least one placeholder was found.
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


def _line_is_exempted(line: str) -> bool:
    return line.rstrip().endswith(_ALLOW_MARKER)


def _check_file(path: Path) -> list[str]:
    violations: list[str] = []
    text = path.read_text(encoding="utf-8")
    for lineno, line in enumerate(text.splitlines(), start=1):
        if _line_is_exempted(line):
            continue
        for label, pattern in _PATTERNS:
            match = pattern.search(line)
            if match:
                violations.append(
                    f"{path}:{lineno}:{match.start() + 1}: {label}: "
                    f"{match.group(0)!r}"
                )
    return violations


def main(argv: list[str]) -> int:
    args = argv[1:]
    if not args:
        print("usage: check_trellis_placeholders.py <trellis-artifact>...", file=sys.stderr)
        return 2

    violations: list[str] = []
    for arg in args:
        path = Path(arg)
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

    if violations:
        print("\n".join(violations), file=sys.stderr)
        print(
            "\nTrellis task and workspace artifacts must not commit unfinished "
            "template placeholders. Fill in the section or add the trailing "
            f"{_ALLOW_MARKER!r} marker for a deliberate quotation.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
