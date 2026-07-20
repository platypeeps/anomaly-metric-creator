"""Acceptance tests for the canonical clean-module mypy gate."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "tools" / "check_mypy_gate.py"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
LOCAL_PREFLIGHT = REPO_ROOT / "scripts" / "check-review-preflight.mjs"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )


def test_list_mode_owns_the_expected_clean_module_set() -> None:
    result = _run("--list")

    assert result.returncode == 0, result.stderr
    modules = result.stdout.splitlines()
    assert len(modules) == 19
    assert modules[0] == "src/anomaly_metric_creator/__init__.py"
    assert modules[-1] == "src/anomaly_metric_creator/timeutil.py"
    assert len(modules) == len(set(modules))
    assert all((REPO_ROOT / module).is_file() for module in modules)


def test_ci_and_local_preflight_invoke_checker_without_inline_module_lists() -> None:
    modules = _run("--list").stdout.splitlines()
    workflow = WORKFLOW.read_text(encoding="utf-8")
    preflight = LOCAL_PREFLIGHT.read_text(encoding="utf-8")

    assert "python tools/check_mypy_gate.py" in workflow
    assert '["tools/check_mypy_gate.py"]' in preflight
    for owner in (workflow, preflight):
        assert not [module for module in modules if module in owner]


def test_unknown_argument_exits_two() -> None:
    result = _run("--unknown")

    assert result.returncode == 2
    assert "usage" in result.stderr
