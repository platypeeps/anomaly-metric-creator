"""Acceptance tests for the work-item acceptance-criteria command guard."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "tools" / "check_task_criteria_commands.py"
TASKS = REPO_ROOT / "docs" / "work"


def _run(*paths: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *map(str, paths)],
        capture_output=True,
        text=True,
    )


def _doc(tmp_path: Path, body: str, name: str = "prd.md") -> Path:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


@pytest.mark.parametrize(
    "command",
    [
        "grep -c 'def _column_values' tests/*.py",
        "grep -c pattern tests/",
        "grep -rc pattern tests/conftest.py",
        "grep -c pattern tests/a.py tests/b.py",
        "grep --count pattern tests/",
    ],
)
def test_multi_file_count_is_rejected(tmp_path: Path, command: str) -> None:
    result = _run(_doc(tmp_path, f"- [ ] `{command}` returns 0.\n"))
    assert result.returncode == 1
    assert "counts across more than one file" in result.stderr


@pytest.mark.parametrize(
    "command",
    [
        "grep -c 'HTTPServer' tests/test_cli.py",
        "grep -cE '^[[:space:]]*class _Handler' tests/test_cli.py",
        "grep -c pattern",
        "grep -rn pattern tests/",
        "grep -rn 'def capture_otlp_server' tests/ | grep -c conftest",
        "grep -c pattern one.py; grep -c pattern two.py",
    ],
)
def test_single_stream_counts_are_accepted(tmp_path: Path, command: str) -> None:
    result = _run(_doc(tmp_path, f"- [ ] `{command}` returns 0.\n"))
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("escape", ["\\s", "\\S", "\\d", "\\w", "\\W"])
def test_gnu_only_escapes_are_rejected(tmp_path: Path, escape: str) -> None:
    result = _run(_doc(tmp_path, f"- [ ] `grep -rn 'def{escape}name' tests/` matches.\n"))
    assert result.returncode == 1
    assert f"GNU/PCRE escape '{escape}'" in result.stderr


@pytest.mark.parametrize(
    "command",
    [
        # A `|` inside a quoted pattern is regex alternation, not a shell pipe.
        'grep -nE "^\\s*from \\.mod import|^\\s*import .*mod" one.py',
        "grep -rn 'alpha|def\\sbeta' tests/",
        # Every pattern-bearing flag shape reaches the escape check.
        "grep -rn -e 'def\\sname' tests/",
        "grep -rn -e'def\\sname' tests/",
        "grep -rn --regexp 'def\\sname' tests/",
        "grep -rn --regexp='def\\sname' tests/",
    ],
)
def test_escapes_are_found_in_every_pattern_shape(tmp_path: Path, command: str) -> None:
    result = _run(_doc(tmp_path, f"- [ ] `{command}` matches.\n"))
    assert result.returncode == 1, result.stdout
    assert "GNU/PCRE escape '\\s'" in result.stderr


def test_alternation_does_not_hide_a_multi_file_count(tmp_path: Path) -> None:
    # Splitting on `|` before lexing made this command unparseable, and an
    # unparseable command is silently clean -- the false negative Copilot found.
    body = "- [ ] `grep -cE 'alpha|beta' tests/` returns 0.\n"
    result = _run(_doc(tmp_path, body))
    assert result.returncode == 1
    assert "counts across more than one file" in result.stderr


def test_perl_mode_permits_gnu_escapes(tmp_path: Path) -> None:
    result = _run(_doc(tmp_path, "- [ ] `grep -rnP 'def\\s+name' tests/` matches.\n"))
    assert result.returncode == 0, result.stderr


def test_escape_outside_a_grep_command_is_prose(tmp_path: Path) -> None:
    body = "Use a POSIX class, not `\\s` — BSD grep treats it as a literal.\n"
    assert _run(_doc(tmp_path, body)).returncode == 0


def test_pattern_operand_is_not_mistaken_for_a_path(tmp_path: Path) -> None:
    # `== 0` is pseudo-shell commentary, not two more searched files.
    body = "- [ ] `grep -c resource_snapshot server_k8s_api.py == 0`\n"
    assert _run(_doc(tmp_path, body)).returncode == 0


def test_fenced_block_commands_are_checked(tmp_path: Path) -> None:
    body = "```bash\ngrep -c pattern tests/*.py\n```\n"
    result = _run(_doc(tmp_path, body))
    assert result.returncode == 1
    assert "counts across more than one file" in result.stderr


def test_allow_marker_suppresses_a_violation(tmp_path: Path) -> None:
    body = "- [ ] `grep -c pattern tests/*.py` <!-- criteria-lint: allow -->\n"
    assert _run(_doc(tmp_path, body)).returncode == 0


def test_missing_path_is_a_structural_error(tmp_path: Path) -> None:
    result = _run(tmp_path / "absent.md")
    assert result.returncode == 2
    assert "no such path" in result.stderr


def test_non_markdown_file_is_a_structural_error(tmp_path: Path) -> None:
    path = tmp_path / "notes.txt"
    path.write_text("grep -c pattern tests/*.py\n", encoding="utf-8")
    result = _run(path)
    assert result.returncode == 2
    assert "expected a Markdown file or directory" in result.stderr


def test_no_arguments_prints_usage(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT)], capture_output=True, text=True
    )
    assert result.returncode == 2
    assert "usage:" in result.stderr


def test_live_task_tree_is_clean() -> None:
    result = _run(TASKS)
    assert result.returncode == 0, result.stderr
