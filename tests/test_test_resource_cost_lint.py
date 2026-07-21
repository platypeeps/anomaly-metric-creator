"""Acceptance tests for the AST-backed test resource-cost guard."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "tools" / "check_test_resource_cost.py"


def _run(*paths: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *map(str, paths)],
        capture_output=True,
        text=True,
    )


def _source(tmp_path: Path, text: str, name: str = "test_sample.py") -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("source", "diagnostic"),
    [
        ("data = path.read_bytes()\n", "read_bytes()"),
        ("rows = handle.readlines()\n", "readlines()"),
        ("rows = path.read_text().splitlines()\n", "read_text().splitlines()"),
    ],
)
def test_forbidden_eager_reads_exit_one(
    tmp_path: Path, source: str, diagnostic: str
) -> None:
    result = _run(_source(tmp_path, source))
    assert result.returncode == 1
    assert diagnostic in result.stderr


def test_multiline_call_is_detected(tmp_path: Path) -> None:
    path = _source(tmp_path, "rows = (\n    output.read_text()\n).splitlines()\n")
    result = _run(path)
    assert result.returncode == 1
    assert "read_text().splitlines()" in result.stderr


def test_comments_and_strings_do_not_match(tmp_path: Path) -> None:
    path = _source(
        tmp_path,
        '# output.read_bytes()\nmessage = "path.read_text().splitlines()"\n',
    )
    result = _run(path)
    assert result.returncode == 0, result.stderr


def test_trailing_marker_in_multiline_span_exempts_call(tmp_path: Path) -> None:
    path = _source(
        tmp_path,
        "rows = (\n    small_log.read_text()  # resource-lint: allow\n).splitlines()\n",
    )
    result = _run(path)
    assert result.returncode == 0, result.stderr


def test_midline_marker_does_not_exempt(tmp_path: Path) -> None:
    path = _source(
        tmp_path,
        "data = path.read_bytes()  # resource-lint: allow because tiny\n",
    )
    result = _run(path)
    assert result.returncode == 1


def test_directory_inputs_recurse_and_aggregate(tmp_path: Path) -> None:
    nested = tmp_path / "nested"
    nested.mkdir()
    _source(tmp_path, "data = one.read_bytes()\n", "test_one.py")
    _source(nested, "rows = two.readlines()\n", "test_two.py")
    result = _run(tmp_path)
    assert result.returncode == 1
    assert "test_one.py" in result.stderr
    assert "test_two.py" in result.stderr


def test_streaming_iteration_and_chunked_hash_are_clean(tmp_path: Path) -> None:
    path = _source(
        tmp_path,
        "for row in handle:\n    consume(row)\n"
        "with path.open('rb') as handle:\n"
        "    while chunk := handle.read(1024):\n        digest.update(chunk)\n",
    )
    result = _run(path)
    assert result.returncode == 0, result.stderr


def test_no_args_and_missing_path_exit_two(tmp_path: Path) -> None:
    assert _run().returncode == 2
    result = _run(tmp_path / "missing.py")
    assert result.returncode == 2


def test_existing_non_python_file_exits_two(tmp_path: Path) -> None:
    path = tmp_path / "test_sample.pyy"
    path.write_text("data = output.read_bytes()\n", encoding="utf-8")
    result = _run(path)
    assert result.returncode == 2
    assert "expected a Python file or directory" in result.stderr


def test_syntax_error_exits_two(tmp_path: Path) -> None:
    result = _run(_source(tmp_path, "if True print('broken')\n"))
    assert result.returncode == 2
    assert "cannot parse Python" in result.stderr


def test_non_utf8_input_exits_two(tmp_path: Path) -> None:
    path = tmp_path / "test_binary.py"
    path.write_bytes(b"\xff")
    result = _run(path)
    assert result.returncode == 2
