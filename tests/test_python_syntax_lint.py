"""Acceptance tests for `tools/check_python_syntax.py`.

The lint parses Python files with `ast.parse` instead of `py_compile`, so it
catches syntax errors without creating `__pycache__` directories in generated
hook trees.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "tools" / "check_python_syntax.py"


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
    )


def test_valid_python_exits_zero(tmp_path: Path) -> None:
    path = tmp_path / "ok.py"
    path.write_text("def f(*, a=1, b=2):\n    return a + b\n", encoding="utf-8")
    result = _run(str(path))
    assert result.returncode == 0, result.stderr


def test_syntax_error_exits_one(tmp_path: Path) -> None:
    path = tmp_path / "bad.py"
    path.write_text("def f(:\n    return 1\n", encoding="utf-8")
    result = _run(str(path))
    assert result.returncode == 1
    assert "invalid Python syntax" in result.stderr
    assert str(path) in result.stderr


def test_no_args_exits_two() -> None:
    result = _run()
    assert result.returncode == 2
    assert "usage" in result.stderr.lower()


def test_missing_path_exits_two(tmp_path: Path) -> None:
    result = _run(str(tmp_path / "missing.py"))
    assert result.returncode == 2


def test_non_utf8_file_exits_two(tmp_path: Path) -> None:
    path = tmp_path / "bad.py"
    path.write_bytes(b"\xff\xfe")
    result = _run(str(path))
    assert result.returncode == 2


def test_live_python_files_parse() -> None:
    roots = [
        REPO_ROOT / "scripts",
        REPO_ROOT / "src",
        REPO_ROOT / "tests",
        REPO_ROOT / "tools",
        REPO_ROOT / ".codex" / "hooks",
        REPO_ROOT / ".github" / "copilot" / "hooks",
        REPO_ROOT / ".gemini" / "hooks",
    ]
    files = sorted(
        str(path)
        for root in roots
        for path in root.rglob("*.py")
    )
    assert files, "expected Python files to guard"
    result = _run(*files)
    assert result.returncode == 0, result.stderr
