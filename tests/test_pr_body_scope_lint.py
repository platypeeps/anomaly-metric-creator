"""Acceptance tests for the SD command-pack PR-body scope checker."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "sd-ai-command-pack-pr-body-scope.py"
PR_BODY_ENV_KEYS = (
    "SD_AI_COMMAND_PACK_SCOPE_PR_BODY",
    "SD_AI_COMMAND_PACK_PR_BODY_SCOPE_PR_BODY",
    "SD_AI_COMMAND_PACK_CHANGED_FILES",
    "SD_AI_COMMAND_PACK_PR_BODY_SCOPE_CHANGED_FILES",
    "SD_AI_COMMAND_PACK_PR_BODY_SCOPE_CONFIG",
)
TOOL_CACHE_ENV_KEYS = (
    "SD_AI_COMMAND_PACK_CACHE_ROOT",
    "SD_AI_COMMAND_PACK_CACHE_ENV_READY",
    "XDG_CACHE_HOME",
    "PYTHONPYCACHEPREFIX",
    "UV_CACHE_DIR",
    "UV_TOOL_DIR",
    "PIP_CACHE_DIR",
    "RUFF_CACHE_DIR",
    "NPM_CONFIG_CACHE",
)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text).lstrip(), encoding="utf-8")


def _clean_environment() -> dict[str, str]:
    env = os.environ.copy()
    for key in PR_BODY_ENV_KEYS + TOOL_CACHE_ENV_KEYS:
        env.pop(key, None)
    return env


def _run(tmp_path: Path, *, changed_files: str, body: str | None = None) -> subprocess.CompletedProcess:
    changed_file_list = tmp_path / "changed-files.txt"
    _write(changed_file_list, changed_files)

    env = _clean_environment()
    if body is not None:
        env["SD_AI_COMMAND_PACK_PR_BODY_SCOPE_PR_BODY"] = textwrap.dedent(body).lstrip()

    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            str(REPO_ROOT),
            "--changed-files",
            str(changed_file_list),
        ],
        capture_output=True,
        text=True,
        env=env,
    )


def test_real_repo_check_is_non_blocking_without_pr_body() -> None:
    env = _clean_environment()

    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(REPO_ROOT)],
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 0, result.stderr


def test_no_pr_body_warns_but_does_not_fail(tmp_path: Path) -> None:
    result = _run(
        tmp_path,
        changed_files="""
        scripts/sd-ai-command-pack-housekeeping.sh
        """,
    )

    assert result.returncode == 0, result.stderr
    assert "PR body not provided" in result.stdout
    assert "Automation scope" in result.stdout


def test_housekeeping_change_requires_automation_scope(tmp_path: Path) -> None:
    result = _run(
        tmp_path,
        changed_files="""
        scripts/sd-ai-command-pack-housekeeping.sh
        """,
        body="""
        Summary:
        Update housekeeping automation.
        """,
    )

    assert result.returncode == 1
    assert "missing Automation scope" in result.stderr


def test_housekeeping_scope_satisfies_automation_scope(tmp_path: Path) -> None:
    result = _run(
        tmp_path,
        changed_files="""
        scripts/sd-ai-command-pack-housekeeping.sh
        """,
        body="""
        Housekeeping scope:
        Update the post-merge housekeeping wrapper.

        Tooling/generated scope:
        Copied SD command-pack scripts were synced.

        CI/review scope:
        Full-check wiring stays covered by the local review preflight.
        """,
    )

    assert result.returncode == 0, result.stderr


def test_workflow_change_requires_ci_review_scope(tmp_path: Path) -> None:
    result = _run(
        tmp_path,
        changed_files="""
        .github/workflows/ci.yml
        """,
        body="""
        Summary:
        Adjust CI.
        """,
    )

    assert result.returncode == 1
    assert "missing CI/review scope" in result.stderr


def test_generated_file_change_requires_tooling_generated_scope(tmp_path: Path) -> None:
    result = _run(
        tmp_path,
        changed_files="""
        docs/repomix-map.md
        .sd-ai-command-pack/installed-targets.txt
        """,
        body="""
        Summary:
        Refresh generated files.
        """,
    )

    assert result.returncode == 1
    assert "missing Tooling/generated scope" in result.stderr


def test_runtime_change_requires_runtime_scope(tmp_path: Path) -> None:
    result = _run(
        tmp_path,
        changed_files="""
        src/anomaly_metric_creator/server_ops.py
        """,
        body="""
        Summary:
        Update server behavior.
        """,
    )

    assert result.returncode == 1
    assert "missing Runtime/server scope" in result.stderr


def test_user_facing_docs_change_requires_docs_scope(tmp_path: Path) -> None:
    result = _run(
        tmp_path,
        changed_files="""
        README.md
        docs/topology.md
        """,
        body="""
        Summary:
        Update user docs.
        """,
    )

    assert result.returncode == 1
    assert "missing Docs/user-facing scope" in result.stderr


def test_multiple_categories_report_each_missing_scope(tmp_path: Path) -> None:
    result = _run(
        tmp_path,
        changed_files="""
        .github/workflows/ci.yml
        docs/repomix-map.md
        src/anomaly_metric_creator/legacy.py
        """,
        body="""
        Summary:
        Broad maintenance.
        """,
    )

    assert result.returncode == 1
    assert "missing CI/review scope" in result.stderr
    assert "missing Tooling/generated scope" in result.stderr
    assert "missing Runtime/server scope" in result.stderr


def test_matching_scope_sections_pass(tmp_path: Path) -> None:
    result = _run(
        tmp_path,
        changed_files="""
        .github/workflows/ci.yml
        docs/repomix-map.md
        README.md
        src/anomaly_metric_creator/legacy.py
        """,
        body="""
        CI/review scope:
        Updates the local review cadence.

        Tooling/generated scope:
        Refreshes generated repository map output.

        Docs/user-facing scope:
        Updates README usage guidance.

        Runtime/server scope:
        Changes canonical runtime behavior.
        """,
    )

    assert result.returncode == 0, result.stderr
