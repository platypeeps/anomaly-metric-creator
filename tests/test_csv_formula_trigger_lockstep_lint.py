"""Acceptance tests for `tools/check_csv_formula_trigger_lockstep.py`.

The lint asserts that the CSV formula-injection trigger set stays in lockstep
across the two independent export paths: ``_CSV_FORMULA_TRIGGERS`` in
``trace_bundle.py`` (the server-side ``write_trace_bundle_csv`` writer) and the
marked ``csvCell`` guard in the debug UI's embedded JavaScript, which builds a
CSV in the operator's browser and never touches the Python writer. Nothing in
the code makes one follow the other, so a trigger added to one site alone
reopens the injection hole on the other -- silently, on whichever surface the
change did not touch.

Pin the behaviors the script promises in its docstring:

- matching sets exit ``0``, including when the JavaScript writes a trigger as
  an escape (``\\t``) that the Python side writes as the same character;
- drifting sets exit ``1`` and the diagnostic names the missing character *and*
  the side missing it, in both directions;
- a missing Python assignment, a non-literal or malformed Python value, a
  missing JavaScript marker, a marker with no character class, and a class
  written as a range each exit ``2`` (structural error, distinct from drift);
- the *actual* repo modules are in lockstep right now (regression guard on the
  live files).

Structurally parallel to ``tests/test_ruff_lockstep_lint.py``, the repo's other
two-site lockstep guard.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "tools" / "check_csv_formula_trigger_lockstep.py"
# The live tree the regression guard below reads. Named constants rather than
# inline paths so the coverage guard can see this file exercises the real
# modules, not only fixtures.
TRACE_BUNDLE = REPO_ROOT / "src" / "anomaly_metric_creator" / "trace_bundle.py"
DEBUG_UI = REPO_ROOT / "src" / "anomaly_metric_creator" / "server_debug_ui.py"


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
    )


def _python_module(assignment: str = '_CSV_FORMULA_TRIGGERS = ("=", "+", "-", "@", "\\t", "\\r")') -> str:
    """Minimal stand-in for ``trace_bundle.py``. ``assignment`` is spliced in
    verbatim so a test can omit it, rename it, or make it non-literal."""
    return (
        '"""Fixture module."""\n'
        "\n"
        "_MAX_SEARCH_LIMIT = 500\n"
        "\n"
        f"{assignment}\n"
        "\n"
        "\n"
        "def _neutralize_csv_cell(value):\n"
        "    return value\n"
    )


def _js_module(
    guard: str = "      const safe = /^[=+\\-@\\t\\r]/.test(text) ? `'${text}` : text;",
    *,
    marker: bool = True,
) -> str:
    """Minimal stand-in for ``server_debug_ui.py``: a Python module holding the
    debug UI template as a string, with the marked guard line inside it."""
    marker_line = (
        "      // csv-formula-triggers: lockstep with trace_bundle.\n" if marker else ""
    )
    return (
        'DEBUG_HTML = """\n'
        "<script>\n"
        "    function csvCell(value) {\n"
        "      const text = String(value ?? '');\n"
        f"{marker_line}"
        f"{guard}\n"
        "      return safe;\n"
        "    }\n"
        "</script>\n"
        '"""\n'
    )


def _write_pair(tmp_path: Path, python_body: str, js_body: str) -> tuple[str, str]:
    python_path = tmp_path / "trace_bundle.py"
    js_path = tmp_path / "server_debug_ui.py"
    python_path.write_text(python_body, encoding="utf-8")
    js_path.write_text(js_body, encoding="utf-8")
    return str(python_path), str(js_path)


def test_matching_sets_exit_zero(tmp_path: Path) -> None:
    result = _run(*_write_pair(tmp_path, _python_module(), _js_module()))
    assert result.returncode == 0, result.stderr
    assert "in lockstep" in result.stdout


def test_reordered_javascript_class_still_matches(tmp_path: Path) -> None:
    """The sets are compared as sets: order is not part of the contract, so a
    reordered character class must not read as drift."""
    guard = "      const safe = /^[\\r\\t@\\-+=]/.test(text) ? `'${text}` : text;"
    result = _run(*_write_pair(tmp_path, _python_module(), _js_module(guard)))
    assert result.returncode == 0, result.stderr


def test_trigger_missing_from_javascript_exits_one(tmp_path: Path) -> None:
    guard = "      const safe = /^[=+\\-\\t\\r]/.test(text) ? `'${text}` : text;"
    result = _run(*_write_pair(tmp_path, _python_module(), _js_module(guard)))
    assert result.returncode == 1
    assert "'@'" in result.stderr
    assert "server_debug_ui.py csvCell does not" in result.stderr


def test_trigger_missing_from_python_exits_one(tmp_path: Path) -> None:
    python_body = _python_module('_CSV_FORMULA_TRIGGERS = ("=", "+", "-", "\\t", "\\r")')
    result = _run(*_write_pair(tmp_path, python_body, _js_module()))
    assert result.returncode == 1
    assert "'@'" in result.stderr
    assert "_CSV_FORMULA_TRIGGERS does not" in result.stderr


def test_missing_python_assignment_exits_two(tmp_path: Path) -> None:
    result = _run(*_write_pair(tmp_path, _python_module(""), _js_module()))
    assert result.returncode == 2
    assert "no module-level _CSV_FORMULA_TRIGGERS" in result.stderr


def test_non_literal_python_value_exits_two(tmp_path: Path) -> None:
    python_body = _python_module("_CSV_FORMULA_TRIGGERS = tuple(_load_triggers())")
    result = _run(*_write_pair(tmp_path, python_body, _js_module()))
    assert result.returncode == 2
    assert "not a literal sequence" in result.stderr


def test_multi_character_python_element_exits_two(tmp_path: Path) -> None:
    python_body = _python_module('_CSV_FORMULA_TRIGGERS = ("=", "+=", "@")')
    result = _run(*_write_pair(tmp_path, python_body, _js_module()))
    assert result.returncode == 2
    assert "single-character string" in result.stderr


def test_missing_javascript_marker_exits_two(tmp_path: Path) -> None:
    """A refactor that drops the marker must fail loud, not pass vacuously:
    without the anchor the check cannot read the guard at all."""
    result = _run(*_write_pair(tmp_path, _python_module(), _js_module(marker=False)))
    assert result.returncode == 2
    assert "csv-formula-triggers:" in result.stderr


def test_marker_without_character_class_exits_two(tmp_path: Path) -> None:
    guard = "      const safe = text;"
    result = _run(*_write_pair(tmp_path, _python_module(), _js_module(guard)))
    assert result.returncode == 2
    assert "no /^[...]/ character class" in result.stderr


def test_guard_pushed_out_of_the_marker_window_exits_two(tmp_path: Path) -> None:
    """The window is a bounded search, so a refactor can move the guard out of it.

    The distinction that matters is loud versus silent: separated far enough,
    the check must refuse rather than quietly find nothing and report the pair
    as in step. This is the case a comment marker cannot prevent, only report.
    """
    guard = "      const safe = /^[=+\\-@\\t\\r]/.test(text) ? `'${text}` : text;"
    padding = "\n".join(f"      // filler line {n}" for n in range(12))
    js_body = (
        'DEBUG_HTML = """\n'
        "<script>\n"
        "    function csvCell(value) {\n"
        "      // csv-formula-triggers: lockstep with trace_bundle.\n"
        f"{padding}\n"
        f"{guard}\n"
        "    }\n"
        "</script>\n"
        '"""\n'
    )
    result = _run(*_write_pair(tmp_path, _python_module(), js_body))
    assert result.returncode == 2
    assert "no /^[...]/ character class" in result.stderr


def test_range_in_javascript_class_exits_two(tmp_path: Path) -> None:
    """A range would silently cover characters the Python tuple never lists, so
    it is rejected rather than expanded."""
    guard = "      const safe = /^[a-z]/.test(text) ? `'${text}` : text;"
    result = _run(*_write_pair(tmp_path, _python_module(), _js_module(guard)))
    assert result.returncode == 2
    assert "contains a range" in result.stderr


def test_escaped_hyphen_is_not_read_as_a_range(tmp_path: Path) -> None:
    """`\\-` is how the real guard writes the literal hyphen trigger; it must
    survive the range check with the hyphen still in the set."""
    guard = "      const safe = /^[=+\\-@\\t\\r]/.test(text) ? `'${text}` : text;"
    result = _run(*_write_pair(tmp_path, _python_module(), _js_module(guard)))
    assert result.returncode == 0, result.stderr
    assert "'-'" in result.stdout


def test_unreadable_file_exits_two(tmp_path: Path) -> None:
    _, js_path = _write_pair(tmp_path, _python_module(), _js_module())
    result = _run(str(tmp_path / "absent.py"), js_path)
    assert result.returncode == 2
    assert "cannot read" in result.stderr


def test_option_like_argument_is_treated_as_a_path_and_exits_two(tmp_path: Path) -> None:
    """An unrecognized flag must not be silently accepted as a file: it lands
    in the path slot, fails to open, and exits 2 rather than reporting on
    whatever the second argument happened to be."""
    _, js_path = _write_pair(tmp_path, _python_module(), _js_module())
    result = _run("--not-a-flag", js_path)
    assert result.returncode == 2
    assert "cannot read" in result.stderr


def test_too_many_arguments_exits_two() -> None:
    result = _run("a", "b", "c")
    assert result.returncode == 2
    assert "usage:" in result.stderr


def test_help_exits_zero() -> None:
    result = _run("--help")
    assert result.returncode == 0
    assert "lockstep" in result.stdout


def test_real_repo_files_are_in_lockstep() -> None:
    """Regression guard on the live modules, not fixtures: this is the
    assertion that fails when someone adds a trigger to one site only."""
    result = _run(str(TRACE_BUNDLE), str(DEBUG_UI))
    assert result.returncode == 0, result.stderr
    for char in ("'='", "'+'", "'-'", "'@'", r"'\t'", r"'\r'"):
        assert char in result.stdout


def test_default_paths_resolve_to_the_live_modules() -> None:
    """Zero-argument invocation must reach the same two modules the explicit
    run above names, or the pre-commit hook would be guarding nothing."""
    assert TRACE_BUNDLE.is_file()
    assert DEBUG_UI.is_file()
    result = _run()
    assert result.returncode == 0, result.stderr
    assert TRACE_BUNDLE.name in result.stdout
    assert DEBUG_UI.name in result.stdout
