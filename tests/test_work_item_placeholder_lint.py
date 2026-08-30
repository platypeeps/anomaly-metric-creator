"""Acceptance tests for `tools/check_work_item_placeholders.py`.

Finish-work items are committed repo files. This lint keeps template
placeholders from landing in them. The workspace journal/index consistency
half of the old guard is gone with the journals it checked.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "tools" / "check_work_item_placeholders.py"
WORK = REPO_ROOT / "docs" / "work"


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
    )


def _artifact(tmp_path: Path, text: str) -> str:
    path = tmp_path / "prd.md"
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_clean_artifact_exits_zero(tmp_path: Path) -> None:
    result = _run(_artifact(tmp_path, "### Main Changes\n\n- Added checks.\n"))
    assert result.returncode == 0, result.stderr


def test_add_details_placeholder_exits_one(tmp_path: Path) -> None:
    result = _run(_artifact(tmp_path, "### Main Changes\n\n(Add details)\n"))
    assert result.returncode == 1
    assert "Add details" in result.stderr


def test_add_test_results_placeholder_exits_one(tmp_path: Path) -> None:
    result = _run(_artifact(tmp_path, "- [OK] (Add test results)\n"))
    assert result.returncode == 1
    assert "Add test results" in result.stderr


def test_fill_markers_exit_one(tmp_path: Path) -> None:
    result = _run(_artifact(tmp_path, "TODO: fill this in\nTo be filled later\n"))
    assert result.returncode == 1
    assert "TODO" in result.stderr
    assert "To be filled" in result.stderr


def test_uppercase_placeholder_exits_one(tmp_path: Path) -> None:
    result = _run(_artifact(tmp_path, "Owner: PLACEHOLDER\n"))
    assert result.returncode == 1
    assert "PLACEHOLDER" in result.stderr


def test_allow_marker_exempts_line(tmp_path: Path) -> None:
    result = _run(_artifact(tmp_path, "(Add details)  # work-item-lint: allow\n"))
    assert result.returncode == 0, result.stderr


def test_no_args_exits_two() -> None:
    assert _run().returncode == 2


def test_missing_path_exits_two(tmp_path: Path) -> None:
    assert _run(str(tmp_path / "missing.md")).returncode == 2


def test_non_utf8_file_exits_two(tmp_path: Path) -> None:
    path = tmp_path / "bad.md"
    path.write_bytes(b"\xff\xfe")
    assert _run(str(path)).returncode == 2


def test_live_work_items_clean() -> None:
    """The point of the guard: the real tree, every item, every time."""
    files = sorted(str(path) for path in WORK.rglob("*.md") if path.is_file())
    assert files, "expected work items to guard"
    result = _run(*files)
    assert result.returncode == 0, result.stderr
