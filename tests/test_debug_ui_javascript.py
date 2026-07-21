"""Syntax-check the JavaScript embedded in the server debug UI."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from anomaly_metric_creator.server_debug_ui import DEBUG_HTML


def test_debug_ui_embedded_javascript_parses_with_node(tmp_path: Path) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is unavailable; JavaScript syntax check cannot run")

    scripts = re.findall(r"<script(?:\s[^>]*)?>(.*?)</script>", DEBUG_HTML, re.DOTALL)
    assert scripts, "DEBUG_HTML must contain an embedded script to validate"
    source = tmp_path / "debug-ui.js"
    source.write_text("\n".join(scripts), encoding="utf-8")

    result = subprocess.run([node, "--check", str(source)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
