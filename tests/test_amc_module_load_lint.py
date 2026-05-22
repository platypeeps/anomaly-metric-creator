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


def test_flags_from_import_alias(tmp_path: Path):
    """`from importlib.util import spec_from_file_location as sfl`
    binds the function to a local name `sfl`. The lint must follow the
    rename and flag calls to `sfl(...)`. Copilot PR #74 round-1."""
    bad = tmp_path / "test_alias.py"
    bad.write_text(
        "from importlib.util import spec_from_file_location as sfl\n"
        "spec = sfl('amc', '/dev/null')\n"
    )
    result = _run(bad)
    assert result.returncode == 1, result.stderr
    assert str(bad) in result.stderr


def test_flags_assignment_alias_from_attribute(tmp_path: Path):
    """``sfl = importlib.util.spec_from_file_location; sfl(...)`` is the
    "assignment-alias bypass" Copilot flagged on PR #74 round-3. The
    lint must extend its alias-tracking past ``ImportFrom`` to also
    cover ``Assign`` targets whose value is an attribute access ending
    in ``.spec_from_file_location``."""
    bad = tmp_path / "test_assign_alias.py"
    bad.write_text(
        "import importlib.util\n"
        "sfl = importlib.util.spec_from_file_location\n"
        "spec = sfl('amc', '/dev/null')\n"
    )
    result = _run(bad)
    assert result.returncode == 1, result.stderr
    assert str(bad) in result.stderr


def test_flags_ann_assignment_alias_from_attribute(tmp_path: Path):
    """Annotated assignment variant of the same bypass:
    ``sfl: Callable = importlib.util.spec_from_file_location``."""
    bad = tmp_path / "test_ann_assign_alias.py"
    bad.write_text(
        "import importlib.util\n"
        "from typing import Callable\n"
        "sfl: Callable = importlib.util.spec_from_file_location\n"
        "spec = sfl('amc', '/dev/null')\n"
    )
    result = _run(bad)
    assert result.returncode == 1, result.stderr
    assert str(bad) in result.stderr


def test_flags_chained_assignment_alias(tmp_path: Path):
    """Hopping through a second local name must not evade the lint.
    The collector iterates to a fixpoint so chains like
    ``a = importlib.util.spec_from_file_location; b = a; b(...)`` are
    caught — same evasion family as the direct-assignment bypass."""
    bad = tmp_path / "test_chained_alias.py"
    bad.write_text(
        "import importlib.util\n"
        "a = importlib.util.spec_from_file_location\n"
        "b = a\n"
        "spec = b('amc', '/dev/null')\n"
    )
    result = _run(bad)
    assert result.returncode == 1, result.stderr
    assert str(bad) in result.stderr


def test_flags_from_import_direct(tmp_path: Path):
    """`from importlib.util import spec_from_file_location` followed by
    a bare-name call. Already caught by the ast.Name branch, but
    pinned as a regression to ensure the alias-tracking patch does not
    regress the simple-name case."""
    bad = tmp_path / "test_direct.py"
    bad.write_text(
        "from importlib.util import spec_from_file_location\n"
        "spec = spec_from_file_location('amc', '/dev/null')\n"
    )
    result = _run(bad)
    assert result.returncode == 1, result.stderr


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


def test_noqa_marker_on_closing_line_exempts_multiline_call(tmp_path: Path):
    """For a multi-line call the trailing `# noqa: amc-load` comment
    is conventionally written on the closing line — that is
    ``node.end_lineno`` in `ast`, not ``node.lineno`` (which is the
    opening-paren line). The exemption must accept the marker on
    either line so common multi-line formatting works. Copilot PR #74
    round-5."""
    ok = tmp_path / "test_multiline_noqa.py"
    ok.write_text(
        "import importlib.util\n"
        "spec = importlib.util.spec_from_file_location(\n"
        "    'amc',\n"
        "    '/dev/null',\n"
        ")  # noqa: amc-load\n"
    )
    result = _run(ok)
    assert result.returncode == 0, result.stderr


def test_noqa_marker_on_opening_line_exempts_multiline_call(tmp_path: Path):
    """Multi-line call with the marker on the opening line (less
    conventional but still legal Python). The exemption must accept
    this shape too — it's the original single-line behavior extended
    to start-line on a multi-line call. Copilot PR #74 round-5."""
    ok = tmp_path / "test_multiline_noqa_open.py"
    ok.write_text(
        "import importlib.util\n"
        "spec = importlib.util.spec_from_file_location(  # noqa: amc-load\n"
        "    'amc',\n"
        "    '/dev/null',\n"
        ")\n"
    )
    result = _run(ok)
    assert result.returncode == 0, result.stderr


def test_noqa_marker_in_string_literal_does_not_exempt(tmp_path: Path):
    """The exemption must be a real comment token, not a substring match.
    A file where the marker text appears only inside a string literal on
    the same line as the call must still be flagged. Copilot PR #74
    round-4 — the prior raw-substring check let any `# noqa: amc-load`
    text anywhere on the physical line silence the lint."""
    bad = tmp_path / "test_string_noqa.py"
    bad.write_text(
        "import importlib.util\n"
        "spec = importlib.util.spec_from_file_location('amc', '# noqa: amc-load')\n"
    )
    result = _run(bad)
    assert result.returncode == 1, result.stderr
    assert "spec_from_file_location" in result.stderr


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
    noqa marker.

    Globs every `*.py` under `tests/` (matching the pre-commit hook's
    `^tests/.*\\.py$` pattern) so the suite also covers `conftest.py`
    and any future non-`test_*.py` helpers (the script's
    `conftest.py` exemption handles conftest correctly). Copilot
    PR #74 round-1."""
    tests_dir = REPO_ROOT / "tests"
    files = sorted(tests_dir.rglob("*.py"))
    assert files, "no .py files found under tests/"
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
