#!/usr/bin/env python3
"""Catch trace import/export payload validation anti-patterns.

PR #140 surfaced several subtle bugs caused by direct casts or silent filtering
on untrusted command trace JSON. The trace boundary modules now use focused
helpers such as ``_trace_int_field``, ``_trace_tuple_field``, and
``_bundle_int_field``. This narrow lint prevents future edits from bypassing
those helpers in ``server_traces.py`` and ``trace_bundle.py``.

Flagged shapes:

* ``int(payload[...])`` / ``int(payload.get(...))`` and ``int(raw_...)``;
* ``tuple(payload[...])`` / ``tuple(payload.get(...))``;
* comprehensions that silently keep only ``isinstance(item, dict)`` entries.

A line can be exempted with a trailing ``# trace-payload-lint: allow`` marker
if a reviewed future case truly needs a local cast.

Exit codes:

* ``0`` - no trace payload anti-patterns.
* ``1`` - at least one anti-pattern was found.
* ``2`` - argument, I/O, or syntax error.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

_ALLOW_MARKER = "# trace-payload-lint: allow"


def _line_is_exempted(lines: list[str], lineno: int) -> bool:
    if lineno <= 0 or lineno > len(lines):
        return False
    return lines[lineno - 1].rstrip().endswith(_ALLOW_MARKER)


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    return ""


def _is_payload_name(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Name)
        and (node.id == "payload" or node.id.endswith("_payload"))
    )


def _is_payload_access(node: ast.AST | None) -> bool:
    if isinstance(node, ast.Subscript):
        return _is_payload_name(node.value)
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get"
    ):
        return _is_payload_name(node.func.value)
    return False


def _is_raw_payload_name(node: ast.AST | None) -> bool:
    return isinstance(node, ast.Name) and node.id.startswith("raw_")


def _is_isinstance_dict(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call) or _call_name(node.func) != "isinstance":
        return False
    if len(node.args) < 2:
        return False
    target = node.args[1]
    return isinstance(target, ast.Name) and target.id == "dict"


class _Visitor(ast.NodeVisitor):
    def __init__(self, path: Path, source: str, lines: list[str]) -> None:
        self.path = path
        self.source = source
        self.lines = lines
        self.violations: list[str] = []

    def visit_Call(self, node: ast.Call) -> None:
        if _line_is_exempted(self.lines, node.lineno):
            return

        name = _call_name(node.func)
        first_arg = node.args[0] if node.args else None

        if name == "int" and (
            _is_payload_access(first_arg) or _is_raw_payload_name(first_arg)
        ):
            self.violations.append(
                f"{self.path}:{node.lineno}: direct int() on trace payload data "
                "bypasses strict bool/type validation; use the trace/bundle "
                "integer helper instead."
            )
        if name == "tuple" and _is_payload_access(first_arg):
            self.violations.append(
                f"{self.path}:{node.lineno}: direct tuple() on trace payload "
                "data can split strings into characters; use the trace tuple "
                "helper instead."
            )

        self.generic_visit(node)

    def _visit_comprehension(self, node: ast.AST, generators: list[ast.comprehension]) -> None:
        lineno = getattr(node, "lineno", 0)
        if _line_is_exempted(self.lines, lineno):
            return
        for generator in generators:
            for condition in generator.ifs:
                if _is_isinstance_dict(condition):
                    self.violations.append(
                        f"{self.path}:{lineno}: comprehension silently filters "
                        "non-object trace entries with isinstance(..., dict); "
                        "raise a validation error with the entry index instead."
                    )
                    return
        self.generic_visit(node)

    def visit_ListComp(self, node: ast.ListComp) -> None:
        self._visit_comprehension(node, node.generators)

    def visit_SetComp(self, node: ast.SetComp) -> None:
        self._visit_comprehension(node, node.generators)

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        self._visit_comprehension(node, node.generators)


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
        print("usage: check_trace_payload_antipatterns.py <trace-module.py>...", file=sys.stderr)
        return 2

    violations: list[str] = []
    for arg in args:
        path = Path(arg)
        if not path.exists():
            print(f"check_trace_payload_antipatterns: no such file: {path}", file=sys.stderr)
            return 2
        if not path.is_file() or path.suffix != ".py":
            continue
        try:
            violations.extend(_check_file(path))
        except (OSError, UnicodeError) as exc:
            print(
                f"check_trace_payload_antipatterns: cannot read {path}: {exc}",
                file=sys.stderr,
            )
            return 2
        except ValueError as exc:
            print(f"check_trace_payload_antipatterns: {exc}", file=sys.stderr)
            return 2

    if violations:
        print("\n".join(violations), file=sys.stderr)
        print(
            "\nTrace import/export payloads are untrusted. Validate container "
            "shape and scalar types before casting or iterating.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
