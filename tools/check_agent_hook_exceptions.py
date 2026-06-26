#!/usr/bin/env python3
"""Guard generated Python agent hooks against review-churn exception shapes.

The generated Codex, Copilot, and Gemini hook adapters are intentionally
fail-open in a few places, but PR #140 still drew review comments for two
mechanical shapes:

* catching ``BaseException`` (or a bare ``except``), which also catches
  ``KeyboardInterrupt`` and ``SystemExit``;
* empty ``except`` handlers that only ``pass`` without explaining why the
  exception is safe to suppress.

This lint keeps those rules narrow to hook adapter files instead of enabling a
repo-wide broad-exception rule. A handler can be exempted with a trailing
``# agent-hook-exception-lint: allow`` marker on the ``except`` line when a
future generated adapter has a deliberate, reviewed reason.

Exit codes:

* ``0`` - no forbidden hook exception handlers.
* ``1`` - at least one forbidden handler was found.
* ``2`` - argument, I/O, or syntax error.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

_ALLOW_MARKER = "# agent-hook-exception-lint: allow"


def _line_is_exempted(lines: list[str], lineno: int) -> bool:
    if lineno <= 0 or lineno > len(lines):
        return False
    return lines[lineno - 1].rstrip().endswith(_ALLOW_MARKER)


def _has_explanatory_comment(lines: list[str], handler: ast.ExceptHandler) -> bool:
    body = handler.body
    if len(body) != 1 or not isinstance(body[0], ast.Pass):
        return True

    pass_lineno = body[0].lineno
    pass_line = lines[pass_lineno - 1] if 0 < pass_lineno <= len(lines) else ""
    if "#" in pass_line and pass_line.split("#", 1)[1].strip():
        return True

    for index in range(handler.lineno, pass_lineno - 1):
        stripped = lines[index].strip()
        if stripped.startswith("#") and stripped[1:].strip():
            return True
    return False


def _handler_type_text(source: str, handler: ast.ExceptHandler) -> str:
    if handler.type is None:
        return ""
    return ast.get_source_segment(source, handler.type) or ""


class _Visitor(ast.NodeVisitor):
    def __init__(self, path: Path, source: str, lines: list[str]) -> None:
        self.path = path
        self.source = source
        self.lines = lines
        self.violations: list[str] = []

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if _line_is_exempted(self.lines, node.lineno):
            return

        type_text = _handler_type_text(self.source, node)
        if node.type is None:
            self.violations.append(
                f"{self.path}:{node.lineno}: bare except catches BaseException; "
                "catch Exception or a narrower expected exception instead."
            )
        elif "BaseException" in type_text:
            self.violations.append(
                f"{self.path}:{node.lineno}: except BaseException is forbidden "
                "in agent hooks; catch Exception or a narrower expected "
                "exception instead."
            )

        if not _has_explanatory_comment(self.lines, node):
            self.violations.append(
                f"{self.path}:{node.body[0].lineno}: empty except/pass handler "
                "needs a short comment explaining the intentional suppression."
            )

        self.generic_visit(node)


def _check_file(path: Path) -> list[str]:
    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        line = exc.lineno or 0
        offset = exc.offset or 0
        raise ValueError(f"{path}:{line}:{offset}: cannot parse Python: {exc.msg}") from exc

    visitor = _Visitor(path, source, source.splitlines())
    visitor.visit(tree)
    return visitor.violations


def main(argv: list[str]) -> int:
    args = argv[1:]
    if not args:
        print("usage: check_agent_hook_exceptions.py <hook.py>...", file=sys.stderr)
        return 2

    violations: list[str] = []
    for arg in args:
        path = Path(arg)
        if not path.exists():
            print(f"check_agent_hook_exceptions: no such file: {path}", file=sys.stderr)
            return 2
        if not path.is_file() or path.suffix != ".py":
            continue
        try:
            violations.extend(_check_file(path))
        except ValueError as exc:
            print(f"check_agent_hook_exceptions: {exc}", file=sys.stderr)
            return 2
        except OSError as exc:
            print(
                f"check_agent_hook_exceptions: cannot read {path}: {exc}",
                file=sys.stderr,
            )
            return 2

    if violations:
        print("\n".join(violations), file=sys.stderr)
        print(
            "\nAgent hook adapters may fail open, but they must not catch "
            "BaseException and any empty pass handler must document why "
            "suppression is intentional.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
