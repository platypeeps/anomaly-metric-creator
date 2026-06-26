#!/usr/bin/env python3
"""Validate Python syntax without writing bytecode.

This is a pre-commit friendly alternative to ``python -m py_compile`` for
source trees that include generated hook directories. ``py_compile`` writes
``__pycache__`` entries next to the checked files; this script only reads each
file and runs ``ast.parse`` so it can catch malformed Python before review
without filesystem side effects.

Exit codes:

* ``0`` - every checked Python file parses.
* ``1`` - at least one Python syntax error was found.
* ``2`` - argument or I/O error.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path


def _check_file(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    try:
        ast.parse(text, filename=str(path))
    except SyntaxError as exc:
        line = exc.lineno or 0
        offset = exc.offset or 0
        return [
            f"{path}:{line}:{offset}: invalid Python syntax: {exc.msg}"
        ]
    return []


def main(argv: list[str]) -> int:
    args = argv[1:]
    if not args:
        print("usage: check_python_syntax.py <python-file>...", file=sys.stderr)
        return 2

    violations: list[str] = []
    for arg in args:
        path = Path(arg)
        if not path.exists():
            print(f"check_python_syntax: no such file: {path}", file=sys.stderr)
            return 2
        if not path.is_file() or path.suffix != ".py":
            continue
        try:
            violations.extend(_check_file(path))
        except (OSError, UnicodeError) as exc:
            print(f"check_python_syntax: cannot read {path}: {exc}", file=sys.stderr)
            return 2

    if violations:
        print("\n".join(violations), file=sys.stderr)
        print(
            "\nPython files must parse before commit. This check uses ast.parse "
            "only, so it does not create __pycache__ entries.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
