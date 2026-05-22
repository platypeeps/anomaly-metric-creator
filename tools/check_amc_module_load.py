#!/usr/bin/env python3
"""Forbid duplicate amc module loads in tests/.

`tests/conftest.py:_load_amc()` is the single canonical entry point for
loading `anomaly-metric-creator.py` as an importable module. Other test
files MUST consume the session-scoped `amc` fixture rather than re-issue
`importlib.util.spec_from_file_location(...).exec_module(...)`. The
duplicate exec_module pays the full registry-validation cost again and
was the recurring DRY violation flagged in VER-190 (PR #63, PR #64) and
tracked in VER-197.

The check walks each file's AST and flags any *call* whose target is the
identifier `spec_from_file_location` (matches both
`importlib.util.spec_from_file_location(...)` and aliased forms like
`_u.spec_from_file_location(...)`). String literals, docstrings, and
comments are not flagged because they are not call nodes.

Exemptions:

- Files named `conftest.py` are skipped wholesale.
- A trailing ``# noqa: amc-load`` on the call's start line opts that
  specific line out of the check. Use sparingly for cases that
  legitimately need a fresh module copy:
  * tests that monkeypatch module-level callables and must isolate state
    (see `tests/test_correctness.py`);
  * collection-time parametrize loaders that fire before pytest
    fixtures resolve (see `tests/test_scenarios.py`).
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

_FN = "spec_from_file_location"
_NOQA_MARKER = "# noqa: amc-load"


def _check_file(path: Path) -> list[str]:
    src = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(src, filename=str(path))
    except SyntaxError as exc:
        return [f"{path}:{exc.lineno}: syntax error: {exc.msg}"]
    lines = src.splitlines()
    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name: str | None = None
        if isinstance(func, ast.Attribute):
            name = func.attr
        elif isinstance(func, ast.Name):
            name = func.id
        if name != _FN:
            continue
        lineno = node.lineno
        idx = lineno - 1
        line = lines[idx] if 0 <= idx < len(lines) else ""
        if _NOQA_MARKER in line:
            continue
        violations.append(
            f"{path}:{lineno}: `spec_from_file_location(...)` call outside "
            "tests/conftest.py — use the session-scoped `amc` fixture "
            f"(or annotate the call line with `{_NOQA_MARKER}` if a "
            "fresh module instance is genuinely required)."
        )
    return violations


def main(argv: list[str]) -> int:
    violations: list[str] = []
    for raw in argv[1:]:
        path = Path(raw)
        # conftest.py is the canonical loader; never lint it.
        if path.name == "conftest.py":
            continue
        if not path.is_file():
            continue
        violations.extend(_check_file(path))
    if violations:
        print("\n".join(violations), file=sys.stderr)
        print(
            "\nThe canonical AMC module load lives in `tests/conftest.py` "
            "(`_load_amc` / `amc` fixture). Re-importing via "
            "`spec_from_file_location` duplicates the registry-build cost "
            "and was the DRY violation tracked in VER-197.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
