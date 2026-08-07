"""Acceptance tests for `tools/check_module_size.py`.

The live-tree test at the bottom is the one that enforces the rule on this
repository; the synthetic ones cover the three failure modes and the exit-code
contract against a miniature package the fixture builds from scratch.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "tools" / "check_module_size.py"
PACKAGE = Path("src") / "anomaly_metric_creator"


def _run(repo: Path | None = None, *args: str) -> subprocess.CompletedProcess[str]:
    command = [sys.executable, str(SCRIPT)]
    if repo is not None:
        command += ["--repo", str(repo)]
    command += list(args)
    return subprocess.run(command, capture_output=True, text=True, check=False)


def _module(repo: Path, name: str, lines: int) -> None:
    package = repo / PACKAGE
    package.mkdir(parents=True, exist_ok=True)
    (package / name).write_text("x = 1\n" * lines, encoding="utf-8")


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """A miniature checkout with one small, unremarkable module in it."""
    _module(tmp_path, "small.py", 10)
    return tmp_path


def _patched(repo: Path, ratchet: str) -> subprocess.CompletedProcess[str]:
    """Run the lint with `RATCHET` replaced by a literal for this fixture.

    The real `RATCHET` names this repository's modules, so a miniature package
    would report every entry as missing. Rewriting the constant in a copy of
    the script is the smallest way to exercise the enrollment rules without
    adding a production-only injection seam.
    """
    source = SCRIPT.read_text(encoding="utf-8")
    start = source.index("RATCHET: dict[str, tuple[int, str]] = {")
    end = source.index("\n}\n", start) + len("\n}\n")
    copy = repo / "check_module_size_copy.py"
    copy.write_text(
        source[:start] + f"RATCHET: dict[str, tuple[int, str]] = {ratchet}\n" + source[end:],
        encoding="utf-8",
    )
    return subprocess.run(
        [sys.executable, str(copy), "--repo", str(repo)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_module_under_the_cap_exits_zero(repo: Path) -> None:
    result = _patched(repo, "{}")
    assert result.returncode == 0, result.stderr


def test_unenrolled_module_over_the_cap_exits_one(repo: Path) -> None:
    _module(repo, "big.py", 801)
    result = _patched(repo, "{}")
    assert result.returncode == 1
    assert "801 lines exceeds the 800-line behavior-module cap" in result.stderr


def test_cap_is_inclusive(repo: Path) -> None:
    _module(repo, "exact.py", 800)
    result = _patched(repo, "{}")
    assert result.returncode == 0, result.stderr


def test_enrolled_module_within_its_ceiling_exits_zero(repo: Path) -> None:
    _module(repo, "big.py", 1200)
    result = _patched(repo, "{'big.py': (1200, 'debt: test')}")
    assert result.returncode == 0, result.stderr


def test_enrolled_module_that_grew_exits_one(repo: Path) -> None:
    _module(repo, "big.py", 1201)
    result = _patched(repo, "{'big.py': (1200, 'debt: test')}")
    assert result.returncode == 1
    assert "1201 lines exceeds its ratchet ceiling of 1200" in result.stderr


def test_enrolled_module_that_shrank_below_the_cap_exits_one(repo: Path) -> None:
    """A finished extraction must delete its entry, not leave it re-authorizing."""
    _module(repo, "big.py", 400)
    result = _patched(repo, "{'big.py': (1200, 'debt: test')}")
    assert result.returncode == 1
    assert "is stale and must be deleted" in result.stderr


def test_enrolled_module_that_vanished_exits_one(repo: Path) -> None:
    result = _patched(repo, "{'gone.py': (1200, 'debt: test')}")
    assert result.returncode == 1
    assert "but no such module exists" in result.stderr


def test_final_line_without_newline_still_counts(repo: Path) -> None:
    (repo / PACKAGE / "big.py").write_text("x = 1\n" * 800 + "y = 2", encoding="utf-8")
    result = _patched(repo, "{}")
    assert result.returncode == 1
    assert "801 lines" in result.stderr


def test_missing_package_exits_two(tmp_path: Path) -> None:
    result = _run(tmp_path)
    assert result.returncode == 2
    assert "not a directory" in result.stderr


def test_empty_package_exits_two(tmp_path: Path) -> None:
    (tmp_path / PACKAGE).mkdir(parents=True)
    result = _run(tmp_path)
    assert result.returncode == 2
    assert "contains no Python modules" in result.stderr


def test_matched_filenames_are_accepted_and_ignored(repo: Path) -> None:
    """pre-commit passes filenames; the rule is whole-package, so they no-op."""
    _module(repo, "big.py", 801)
    source = SCRIPT.read_text(encoding="utf-8")
    start = source.index("RATCHET: dict[str, tuple[int, str]] = {")
    end = source.index("\n}\n", start) + len("\n}\n")
    copy = repo / "check_module_size_copy2.py"
    copy.write_text(
        source[:start] + "RATCHET: dict[str, tuple[int, str]] = {}\n" + source[end:],
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            sys.executable,
            str(copy),
            "--repo",
            str(repo),
            str(repo / PACKAGE / "small.py"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert "big.py" in result.stderr


def test_live_package_respects_the_ratchet() -> None:
    """The rule, applied to this repository."""
    result = _run(REPO_ROOT)
    assert result.returncode == 0, result.stderr


def test_live_listing_accounts_for_every_enrolled_module() -> None:
    """`--list` must print every RATCHET entry, so the table is the inventory."""
    result = _run(REPO_ROOT, "--list")
    assert result.returncode == 0, result.stderr
    listed = {
        line.split()[0]
        for line in result.stdout.splitlines()
        if line.startswith("  ") and line.strip() and line.strip() != "none"
    }
    sys.path.insert(0, str(REPO_ROOT / "tools"))
    try:
        import check_module_size
    finally:
        sys.path.pop(0)
    assert set(check_module_size.RATCHET) <= listed
