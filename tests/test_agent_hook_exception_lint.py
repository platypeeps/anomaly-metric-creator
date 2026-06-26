"""Acceptance tests for `tools/check_agent_hook_exceptions.py`.

The lint is scoped to generated Python hook adapters and pins the PR #140
review lesson: no `BaseException`/bare handlers, and no silent `except: pass`
without an explanatory comment.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "tools" / "check_agent_hook_exceptions.py"


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
    )


def _hook(tmp_path: Path, body: str) -> str:
    path = tmp_path / "hook.py"
    path.write_text(body, encoding="utf-8")
    return str(path)


def test_documented_empty_pass_exits_zero(tmp_path: Path) -> None:
    path = _hook(
        tmp_path,
        "try:\n    risky()\nexcept Exception:\n"
        "    pass  # Best-effort hook context; keep startup non-fatal.\n",
    )
    result = _run(path)
    assert result.returncode == 0, result.stderr


def test_comment_before_pass_exits_zero(tmp_path: Path) -> None:
    path = _hook(
        tmp_path,
        "try:\n    risky()\nexcept Exception:\n"
        "    # Optional hook context; continue without it.\n    pass\n",
    )
    result = _run(path)
    assert result.returncode == 0, result.stderr


def test_empty_pass_without_comment_exits_one(tmp_path: Path) -> None:
    path = _hook(tmp_path, "try:\n    risky()\nexcept Exception:\n    pass\n")
    result = _run(path)
    assert result.returncode == 1
    assert "empty except/pass" in result.stderr


def test_base_exception_exits_one(tmp_path: Path) -> None:
    path = _hook(
        tmp_path,
        "try:\n    risky()\nexcept BaseException as exc:\n    raise exc\n",
    )
    result = _run(path)
    assert result.returncode == 1
    assert "BaseException" in result.stderr


def test_bare_except_exits_one(tmp_path: Path) -> None:
    path = _hook(
        tmp_path,
        "try:\n    risky()\nexcept:\n"
        "    pass  # Deliberate fail-open adapter path.\n",
    )
    result = _run(path)
    assert result.returncode == 1
    assert "bare except" in result.stderr


def test_allow_marker_exempts_handler(tmp_path: Path) -> None:
    path = _hook(
        tmp_path,
        "try:\n    risky()\n"
        "except BaseException:  # agent-hook-exception-lint: allow\n"
        "    pass\n",
    )
    result = _run(path)
    assert result.returncode == 0, result.stderr


def test_no_args_exits_two() -> None:
    result = _run()
    assert result.returncode == 2


def test_syntax_error_exits_two(tmp_path: Path) -> None:
    path = _hook(tmp_path, "try:\n    pass\nexcept Exception\n    pass\n")
    result = _run(path)
    assert result.returncode == 2


def test_live_agent_hooks_clean() -> None:
    roots = [
        REPO_ROOT / ".codex" / "hooks",
        REPO_ROOT / ".github" / "copilot" / "hooks",
        REPO_ROOT / ".gemini" / "hooks",
    ]
    files = sorted(
        str(path)
        for root in roots
        for path in root.rglob("*.py")
    )
    assert files, "expected hook files to guard"
    result = _run(*files)
    assert result.returncode == 0, result.stderr
