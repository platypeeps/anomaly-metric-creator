"""Syntax-check the JavaScript embedded in the server debug UI."""

from __future__ import annotations

import json
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest

from anomaly_metric_creator.server_debug_ui import DEBUG_HTML


class _ScriptExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._current: list[str] | None = None
        self.scripts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag == "script":
            self._current = []

    def handle_data(self, data: str) -> None:
        if self._current is not None:
            self._current.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._current is not None:
            self.scripts.append("".join(self._current))
            self._current = None


def test_debug_ui_embedded_javascript_parses_with_node(tmp_path: Path) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is unavailable; JavaScript syntax check cannot run")

    extractor = _ScriptExtractor()
    extractor.feed(DEBUG_HTML)
    assert extractor.scripts, "DEBUG_HTML must contain an embedded script to validate"
    source = tmp_path / "debug-ui.js"
    source.write_text("\n".join(extractor.scripts), encoding="utf-8")

    result = subprocess.run([node, "--check", str(source)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def _embedded_script() -> str:
    extractor = _ScriptExtractor()
    extractor.feed(DEBUG_HTML)
    assert extractor.scripts, "DEBUG_HTML must contain an embedded script to validate"
    return "\n".join(extractor.scripts)


def _csv_cell_source() -> str:
    """Slice the `csvCell` function out of the embedded script.

    The script cannot be imported as a module -- it is an inline `<script>`
    body that immediately queries a DOM -- so the driver evaluates this one
    pure function instead of executing the page. Slicing keeps the assertion
    pointed at the *served* source rather than at a copy maintained here.
    """
    script = _embedded_script()
    start = script.index("function csvCell(value) {")
    end = script.index("\n    }", start) + len("\n    }")
    return script[start:end]


def _run_csv_cell(node: str, tmp_path: Path, values: list[str]) -> list[str]:
    """Evaluate the served `csvCell` over `values` and return its outputs."""
    driver = tmp_path / "csv-cell-driver.js"
    driver.write_text(
        f"{_csv_cell_source()}\n"
        f"const values = {json.dumps(values)};\n"
        "console.log(JSON.stringify(values.map(csvCell)));\n",
        encoding="utf-8",
    )
    result = subprocess.run([node, str(driver)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_debug_ui_csv_cell_neutralizes_formula_triggers(tmp_path: Path) -> None:
    """Every OWASP trigger the Python writer guards is inert from the UI too.

    Lockstep with `trace_bundle._CSV_FORMULA_TRIGGERS`, pinned mechanically by
    `tools/check_csv_formula_trigger_lockstep.py`; this test pins the behavior
    that trigger set is supposed to produce.
    """
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is unavailable; JavaScript evaluation cannot run")

    payloads = [f"{trigger}cmd|' /C calc'!A0" for trigger in ("=", "+", "-", "@", "\t", "\r")]
    for payload, rendered in zip(payloads, _run_csv_cell(node, tmp_path, payloads)):
        # These payloads carry no `"`, `,` or newline, so none are quoted
        # today. Strip quoting anyway before checking which character a
        # spreadsheet sees first, so the assertion still holds if the quoting
        # trigger set widens.
        unquoted = rendered[1:-1] if rendered.startswith('"') else rendered
        assert unquoted.startswith("'"), f"{payload!r} rendered as {rendered!r}"
        assert unquoted[1:] == payload.replace('"', '""')


def test_debug_ui_csv_cell_neutralizes_before_quoting(tmp_path: Path) -> None:
    """The apostrophe must land *inside* the quotes.

    Quoting first would emit `'"=a,b"`, where the field's first character is a
    quote and the spreadsheet still evaluates the formula -- the guard would be
    present in the diff and absent in the file.
    """
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is unavailable; JavaScript evaluation cannot run")

    assert _run_csv_cell(node, tmp_path, ["=a,b"]) == ["\"'=a,b\""]


def test_debug_ui_csv_cell_is_idempotent_and_spares_benign_cells(tmp_path: Path) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is unavailable; JavaScript evaluation cannot run")

    once, twice, benign, quoted = _run_csv_cell(
        node, tmp_path, ["=cmd", "'=cmd", "kubectl get pods", 'say "hi"']
    )
    assert once == "'=cmd"
    assert twice == "'=cmd", "a neutralized cell must not collect a second apostrophe"
    assert benign == "kubectl get pods"
    assert quoted == '"say ""hi"""'


def test_debug_ui_csv_cell_guard_is_present_without_node() -> None:
    """Node-independent floor: on a runner with no node the tests above skip,
    and this is the only thing standing between a dropped guard and a green
    build. Weak on its own -- the lockstep lint is what prevents divergence.
    """
    source = _csv_cell_source()
    assert "csv-formula-triggers:" in _embedded_script()
    assert "/^[=+\\-@\\t\\r]/" in source
    assert "`'${text}`" in source
