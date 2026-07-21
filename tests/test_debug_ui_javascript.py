"""Syntax-check the JavaScript embedded in the server debug UI."""

from __future__ import annotations

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
