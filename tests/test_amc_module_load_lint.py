"""Acceptance tests for `tools/check_amc_module_load.py`.

The lint catches the DRY violation pattern from PR #63 and PR #64 where
new test files re-imported `anomaly-metric-creator.py` via
``importlib.util.spec_from_file_location(...).exec_module(...)`` instead
of using the session-scoped `amc` fixture in `tests/conftest.py`. The
canonical loader (`_load_amc()`) is memoized; duplicate exec_module
calls pay the full registry-validation cost again.

The script flags `spec_from_file_location(...)` *function calls* (not
string literals or comments) in any file passed on the command line
unless the file is `conftest.py` or the offending line carries the
`# noqa: amc-load` marker. See VER-197.
"""

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "tools" / "check_amc_module_load.py"


def _run(*paths: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *map(str, paths)],
        capture_output=True,
        text=True,
    )


def test_script_exists():
    assert SCRIPT.is_file(), f"{SCRIPT} not found"


def test_flags_naked_call(tmp_path: Path):
    bad = tmp_path / "test_dup.py"
    bad.write_text(
        "import importlib.util\n"
        "spec = importlib.util.spec_from_file_location('amc', '/dev/null')\n"
    )
    result = _run(bad)
    assert result.returncode == 1, result.stderr
    assert "spec_from_file_location" in result.stderr
    assert str(bad) in result.stderr


def test_flags_attribute_call(tmp_path: Path):
    bad = tmp_path / "test_attr.py"
    bad.write_text(
        "import importlib.util as _u\n"
        "spec = _u.spec_from_file_location('amc', '/dev/null')\n"
    )
    result = _run(bad)
    assert result.returncode == 1, result.stderr
    assert "spec_from_file_location" in result.stderr


def test_conftest_is_ignored(tmp_path: Path):
    good = tmp_path / "conftest.py"
    good.write_text(
        "import importlib.util\n"
        "spec = importlib.util.spec_from_file_location('amc', '/dev/null')\n"
    )
    result = _run(good)
    assert result.returncode == 0, result.stderr


def test_string_literal_not_flagged(tmp_path: Path):
    """The pattern inside a string (e.g. subprocess source code) is not a
    call expression. The AST walker must not flag it. See
    `tests/test_determinism.py::test_import_does_not_run_generation`."""
    ok = tmp_path / "test_string.py"
    ok.write_text(
        '"""Top-level docstring mentions spec_from_file_location."""\n'
        "snippet = 'spec = importlib.util.spec_from_file_location(\"amc\", path)'\n"
    )
    result = _run(ok)
    assert result.returncode == 0, result.stderr


def test_noqa_marker_exempts_line(tmp_path: Path):
    """`# noqa: amc-load` on the call line opts that line out of the lint
    for cases that legitimately need a fresh module copy (e.g.
    monkeypatching `_apply_scenarios` in `test_correctness.py`, or a
    collection-time parametrize loader in `test_scenarios.py`)."""
    ok = tmp_path / "test_exempt.py"
    ok.write_text(
        "import importlib.util\n"
        "spec = importlib.util.spec_from_file_location('amc', '/dev/null')  # noqa: amc-load\n"
    )
    result = _run(ok)
    assert result.returncode == 0, result.stderr


def test_multiple_files_partial_violation(tmp_path: Path):
    clean = tmp_path / "test_clean.py"
    clean.write_text("x = 1\n")
    dirty = tmp_path / "test_dirty.py"
    dirty.write_text(
        "import importlib.util\n"
        "spec = importlib.util.spec_from_file_location('amc', '/dev/null')\n"
    )
    result = _run(clean, dirty)
    assert result.returncode == 1
    assert str(dirty) in result.stderr
    assert str(clean) not in result.stderr


def test_real_test_tree_is_clean():
    """Running the lint against the actual `tests/` tree must pass after
    the existing legitimate uses are annotated. This guards against a
    future test file re-introducing the duplicate load pattern without a
    noqa marker."""
    tests_dir = REPO_ROOT / "tests"
    files = sorted(tests_dir.glob("test_*.py"))
    assert files, "no test files found under tests/"
    result = _run(*files)
    assert result.returncode == 0, (
        f"lint failed against current tests/:\nstderr:\n{result.stderr}"
    )


@pytest.mark.parametrize("bad_source", [
    # Multiline call still flagged
    (
        "import importlib.util\n"
        "spec = importlib.util.spec_from_file_location(\n"
        "    'amc',\n"
        "    '/dev/null',\n"
        ")\n"
    ),
])
def test_multiline_call_flagged(tmp_path: Path, bad_source: str):
    bad = tmp_path / "test_multiline.py"
    bad.write_text(bad_source)
    result = _run(bad)
    assert result.returncode == 1, result.stderr
    assert "spec_from_file_location" in result.stderr
