"""Acceptance tests for ``tools/check_local_gate_contract.py``."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "tools" / "check_local_gate_contract.py"
CONTRACT_FILES = ("Makefile", "scripts/check.sh", ".pre-commit-config.yaml")


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
    )


def _copy_contract(root: Path) -> None:
    for relative in CONTRACT_FILES:
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO_ROOT / relative, target)
    (root / "tools").mkdir()


def _replace(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new), encoding="utf-8")


def test_live_repository_contract_passes() -> None:
    result = _run(str(REPO_ROOT))

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""


def test_copied_contract_without_lints_passes(tmp_path: Path) -> None:
    _copy_contract(tmp_path)

    result = _run(str(tmp_path))

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    ("relative", "needle"),
    [
        ("Makefile", "bash scripts/check.sh"),
        ("scripts/check.sh", "pre-commit run --all-files"),
        ("scripts/check.sh", "node scripts/check-review-preflight.mjs"),
        ("scripts/check.sh", "tools/check_mypy_gate.py"),
        ("scripts/check.sh", "coverage report --fail-under="),
        ("scripts/check.sh", 'bash -n "$script"'),
        ("scripts/check.sh", "AMC_RUN_REAL_CLIENT_SMOKE=1"),
        ("scripts/check.sh", "socketcli --target-path"),
        (".pre-commit-config.yaml", "id: local-gate-contract"),
        (".pre-commit-config.yaml", "bash -c 'set -e;"),
    ],
)
def test_missing_anchor_is_a_violation(
    tmp_path: Path, relative: str, needle: str
) -> None:
    _copy_contract(tmp_path)
    _replace(tmp_path / relative, needle, "REMOVED_ANCHOR")

    result = _run(str(tmp_path))

    assert result.returncode == 1
    assert repr(needle) in result.stderr


def test_lint_that_runs_nowhere_is_a_violation(tmp_path: Path) -> None:
    _copy_contract(tmp_path)
    (tmp_path / "tools" / "check_orphan.py").write_text("", encoding="utf-8")

    result = _run(str(tmp_path))

    assert result.returncode == 1
    assert "tools/check_orphan.py: lint runs nowhere" in result.stderr


def test_lint_invoked_by_another_script_is_reachable(tmp_path: Path) -> None:
    _copy_contract(tmp_path)
    (tmp_path / "tools" / "check_orphan.py").write_text("", encoding="utf-8")
    (tmp_path / "tools" / "wrapper.sh").write_text(
        "python tools/check_orphan.py\n", encoding="utf-8"
    )

    result = _run(str(tmp_path))

    assert result.returncode == 0, result.stderr


def test_lint_naming_only_itself_is_not_reachable(tmp_path: Path) -> None:
    _copy_contract(tmp_path)
    (tmp_path / "tools" / "check_orphan.py").write_text(
        '"""Run as python tools/check_orphan.py."""\n', encoding="utf-8"
    )

    result = _run(str(tmp_path))

    assert result.returncode == 1
    assert "check_orphan.py: lint runs nowhere" in result.stderr


def test_missing_contract_file_exits_two(tmp_path: Path) -> None:
    _copy_contract(tmp_path)
    (tmp_path / "scripts" / "check.sh").unlink()

    result = _run(str(tmp_path))

    assert result.returncode == 2
    assert "cannot read" in result.stderr


def test_extra_arguments_exit_two() -> None:
    result = _run("a", "b")

    assert result.returncode == 2
    assert "usage" in result.stderr
