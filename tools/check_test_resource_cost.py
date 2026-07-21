#!/usr/bin/env python3
"""Reject test helpers that eagerly load potentially large files.

The AMC suite creates multi-day CSV artifacts, so an innocent-looking
``read_bytes()``, ``readlines()``, or ``read_text().splitlines()`` can double
the peak memory of an already expensive test.  This AST-backed guard ignores
comments and string literals while catching multiline call expressions.

Use a trailing ``# resource-lint: allow`` marker anywhere in the call's source
span only when the file is deliberately small (for example a control log or
schema sentinel).

Exit codes: 0 clean, 1 violations, 2 input/read/syntax errors.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

_ALLOW_MARKER = "# resource-lint: allow"


def _is_read_text_splitlines(node: ast.Call) -> bool:
    return (
        isinstance(node.func, ast.Attribute)
        and node.func.attr == "splitlines"
        and isinstance(node.func.value, ast.Call)
        and isinstance(node.func.value.func, ast.Attribute)
        and node.func.value.func.attr == "read_text"
    )


def _forbidden_kind(node: ast.Call) -> str | None:
    if _is_read_text_splitlines(node):
        return "read_text().splitlines()"
    if isinstance(node.func, ast.Attribute) and node.func.attr in {
        "read_bytes",
        "readlines",
    }:
        return f"{node.func.attr}()"
    return None


def _is_exempted(node: ast.Call, lines: list[str]) -> bool:
    end_lineno = node.end_lineno or node.lineno
    return any(
        lines[index - 1].rstrip().endswith(_ALLOW_MARKER)
        for index in range(node.lineno, end_lineno + 1)
    )


class _Visitor(ast.NodeVisitor):
    def __init__(self, path: Path, lines: list[str]) -> None:
        self.path = path
        self.lines = lines
        self.violations: list[str] = []

    def visit_Call(self, node: ast.Call) -> None:
        kind = _forbidden_kind(node)
        if kind is not None and not _is_exempted(node, self.lines):
            self.violations.append(
                f"{self.path}:{node.lineno}:{node.col_offset + 1}: {kind} "
                "eagerly loads a file in a test; stream it, hash it in "
                "chunks, or add a reviewed trailing "
                "'# resource-lint: allow' marker for a deliberately small file."
            )
            # A read_text().splitlines() match owns its nested read_text call.
            if kind == "read_text().splitlines()":
                return
        self.generic_visit(node)


def _python_files(path: Path) -> list[Path]:
    if path.is_dir():
        return sorted(candidate for candidate in path.rglob("*.py") if candidate.is_file())
    return [path] if path.suffix == ".py" else []


def _check(path: Path) -> list[str]:
    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        raise ValueError(
            f"{path}:{exc.lineno or 0}:{exc.offset or 0}: "
            f"cannot parse Python: {exc.msg}"
        ) from exc
    visitor = _Visitor(path, source.splitlines())
    visitor.visit(tree)
    return visitor.violations


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: check_test_resource_cost.py <test.py|directory>...", file=sys.stderr)
        return 2

    files: list[Path] = []
    for raw in argv[1:]:
        path = Path(raw)
        if not path.exists():
            print(f"check_test_resource_cost: no such path: {path}", file=sys.stderr)
            return 2
        if not path.is_dir() and (not path.is_file() or path.suffix != ".py"):
            print(
                f"check_test_resource_cost: expected a Python file or directory: {path}",
                file=sys.stderr,
            )
            return 2
        files.extend(_python_files(path))

    violations: list[str] = []
    for path in sorted(set(files)):
        try:
            violations.extend(_check(path))
        except (OSError, UnicodeError, ValueError) as exc:
            print(f"check_test_resource_cost: {exc}", file=sys.stderr)
            return 2

    if violations:
        print("\n".join(violations), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
