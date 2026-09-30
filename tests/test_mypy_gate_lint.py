"""Acceptance tests for the canonical clean-module mypy gate."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "tools" / "check_mypy_gate.py"
GATE_SCRIPT = REPO_ROOT / "scripts" / "check.sh"
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
    assert len(modules) == 39
    assert modules[0] == "src/anomaly_metric_creator/__init__.py"
    assert modules[-1] == "src/anomaly_metric_creator/timeutil.py"
    assert {
        "src/anomaly_metric_creator/scenario_builders.py",
        "src/anomaly_metric_creator/scenario_catalog.py",
        "src/anomaly_metric_creator/scenario_validation.py",
        "src/anomaly_metric_creator/scenarios_impl.py",
        "src/anomaly_metric_creator/server_k8s_api.py",
        "src/anomaly_metric_creator/server_k8s_api_trace.py",
        "src/anomaly_metric_creator/server_ops_explain.py",
        "src/anomaly_metric_creator/server_ops_payloads.py",
    } <= set(modules)
    assert len(modules) == len(set(modules))
    assert all((REPO_ROOT / module).is_file() for module in modules)


def test_gate_and_local_preflight_invoke_checker_without_inline_module_lists() -> None:
    list_result = _run("--list")
    assert list_result.returncode == 0, list_result.stderr
    modules = list_result.stdout.splitlines()
    gate = GATE_SCRIPT.read_text(encoding="utf-8")
    preflight = LOCAL_PREFLIGHT.read_text(encoding="utf-8")

    gate_step_marker = 'step "Type-check gate (mypy, clean modules)"'
    assert gate.count(gate_step_marker) == 1
    gate_step_start = gate.index(gate_step_marker)
    gate_step_end = gate.find(
        "\nstep ", gate_step_start + len(gate_step_marker)
    )
    assert gate_step_end != -1
    gate_step = gate[gate_step_start:gate_step_end]

    preflight_marker = 'run("Clean-module mypy gate"'
    preflight_calls = [
        line for line in preflight.splitlines() if preflight_marker in line
    ]
    assert len(preflight_calls) == 1

    assert "python tools/check_mypy_gate.py" in gate_step
    assert '["tools/check_mypy_gate.py"]' in preflight_calls[0]
    for owner in (gate_step, preflight_calls[0]):
        assert not [module for module in modules if module in owner]


def test_unknown_argument_exits_two() -> None:
    result = _run("--unknown")

    assert result.returncode == 2
    assert "usage" in result.stderr
