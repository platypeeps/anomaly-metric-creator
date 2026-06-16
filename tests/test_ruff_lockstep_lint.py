"""Acceptance tests for `tools/check_ruff_lockstep.py`.

The lint asserts that ruff's two version pins stay in lockstep:
``ruff==X.Y.Z`` in ``pyproject.toml``'s ``dev`` extra and ``rev: vX.Y.Z``
on the ``astral-sh/ruff-pre-commit`` hook in ``.pre-commit-config.yaml``.
Since the repo moved Dependabot to ``versioning-strategy: lockfile-only``
(PR #115), the ``uv`` ecosystem no longer bumps the exact ``ruff==`` pin,
so only the ``pre-commit`` ecosystem advances the ``rev`` — the two can
drift, and with auto-merge enabled a lone ``rev`` bump could merge stale.
This guard runs in the required CI ``test`` gate to turn that drift into a
red check.

Pin the behaviors the script promises in its docstring:

- in-step pins exit ``0`` (with and without the optional ``v`` prefix on
  the pre-commit ``rev``);
- drifting pins exit ``1`` and the diagnostic names both versions;
- a missing ``ruff==`` dev pin, a missing ``ruff-pre-commit`` block, or a
  missing ``rev`` each exit ``2`` (structural error, distinct from drift);
- the *actual* repo ``pyproject.toml`` / ``.pre-commit-config.yaml`` are
  in lockstep right now (regression guard on the live files).

Mirrors the layout of ``tests/test_branch_name_lint.py`` and
``tests/test_role_name_leaks_lint.py`` so the guardrail lints stay
structurally parallel.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "tools" / "check_ruff_lockstep.py"


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
    )


def _pyproject(pin_line: str) -> str:
    """Minimal pyproject with a configurable dev-extra body. ``pin_line``
    is spliced into the ``dev`` list verbatim (e.g. ``"ruff==0.15.17",``
    or empty to omit the pin)."""
    return (
        "[project]\n"
        'name = "x"\n'
        'version = "0"\n'
        "[project.optional-dependencies]\n"
        "dev = [\n"
        '  "pytest>=8.0",\n'
        f"{pin_line}"
        "]\n"
    )


def _precommit(*, rev_line: str = "    rev: v0.15.17\n", include_ruff: bool = True) -> str:
    """Minimal pre-commit config. With ``include_ruff`` the
    ruff-pre-commit block is present and carries ``rev_line`` verbatim
    (pass an empty string to omit the ``rev``); otherwise only a local
    repo is emitted."""
    ruff_block = (
        "  - repo: https://github.com/astral-sh/ruff-pre-commit\n"
        f"{rev_line}"
        "    hooks:\n"
        "      - id: ruff\n"
        if include_ruff
        else ""
    )
    return (
        "repos:\n"
        f"{ruff_block}"
        "  - repo: local\n"
        "    hooks:\n"
        "      - id: foo\n"
        "        name: foo\n"
        "        entry: foo\n"
        "        language: system\n"
    )


def _write_pair(tmp_path: Path, pyproject_body: str, precommit_body: str) -> tuple[str, str]:
    pp = tmp_path / "pyproject.toml"
    pc = tmp_path / ".pre-commit-config.yaml"
    pp.write_text(pyproject_body, encoding="utf-8")
    pc.write_text(precommit_body, encoding="utf-8")
    return str(pp), str(pc)


def test_in_sync_exits_zero(tmp_path: Path) -> None:
    pp, pc = _write_pair(
        tmp_path,
        _pyproject('  "ruff==0.15.17",\n'),
        _precommit(rev_line="    rev: v0.15.17\n"),
    )
    result = _run(pp, pc)
    assert result.returncode == 0, result.stderr
    assert "lockstep" in result.stdout


def test_in_sync_without_v_prefix_exits_zero(tmp_path: Path) -> None:
    # `rev: 0.15.17` (no leading v) must normalize equal to `ruff==0.15.17`.
    pp, pc = _write_pair(
        tmp_path,
        _pyproject('  "ruff==0.15.17",\n'),
        _precommit(rev_line="    rev: 0.15.17\n"),
    )
    result = _run(pp, pc)
    assert result.returncode == 0, result.stderr


def test_drift_exits_one_and_names_both_versions(tmp_path: Path) -> None:
    pp, pc = _write_pair(
        tmp_path,
        _pyproject('  "ruff==0.15.17",\n'),
        _precommit(rev_line="    rev: v0.16.0\n"),
    )
    result = _run(pp, pc)
    assert result.returncode == 1
    assert "0.15.17" in result.stderr
    assert "0.16.0" in result.stderr
    assert "drift" in result.stderr.lower()


def test_missing_pyproject_pin_exits_two(tmp_path: Path) -> None:
    pp, pc = _write_pair(
        tmp_path,
        _pyproject(""),  # dev extra has no ruff== entry
        _precommit(),
    )
    result = _run(pp, pc)
    assert result.returncode == 2
    assert "ruff==" in result.stderr


def test_missing_precommit_block_exits_two(tmp_path: Path) -> None:
    pp, pc = _write_pair(
        tmp_path,
        _pyproject('  "ruff==0.15.17",\n'),
        _precommit(include_ruff=False),
    )
    result = _run(pp, pc)
    assert result.returncode == 2
    assert "ruff-pre-commit" in result.stderr


def test_missing_rev_exits_two(tmp_path: Path) -> None:
    pp, pc = _write_pair(
        tmp_path,
        _pyproject('  "ruff==0.15.17",\n'),
        _precommit(rev_line=""),  # ruff block present but no rev:
    )
    result = _run(pp, pc)
    assert result.returncode == 2


def test_real_repo_files_in_lockstep() -> None:
    # Regression guard on the live files: the actual pins must agree, so
    # this check passes in CI today and only the *introduction* of drift
    # makes it fail.
    result = _run()  # no args -> default repo-root pyproject + pre-commit
    assert result.returncode == 0, result.stderr
