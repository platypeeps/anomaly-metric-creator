"""Acceptance tests for ``scripts/classify_ci_changes.sh``.

The classifier is the local source of truth for the CI workflow's cheap versus
full gate decisions. These tests keep the path buckets explicit so workflow
review does not have to re-litigate the same globs on every PR.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "classify_ci_changes.sh"


def _changed_file(tmp_path: Path, *paths: str) -> Path:
    changed = tmp_path / "changed-files.txt"
    changed.write_text("\n".join(paths) + "\n", encoding="utf-8")
    return changed


def _run(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        capture_output=True,
        text=True,
        env=env,
    )


def _outputs(stdout: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in stdout.splitlines():
        key, _, value = line.partition("=")
        result[key] = value
    return result


def test_docs_specs_and_agent_files_are_lightweight(tmp_path: Path) -> None:
    changed = _changed_file(
        tmp_path,
        "docs/DEVELOPMENT_CYCLE.md",
        ".trellis/spec/amc/backend/testing-quality.md",
        ".github/prompts/review-pr.prompt.md",
        ".agents/skills/trellis-review-pr/SKILL.md",
        ".prism/rules.json",
    )

    result = _run(str(changed))

    assert result.returncode == 0, result.stderr
    outputs = _outputs(result.stdout)
    assert outputs["lightweight_only"] == "true"
    assert outputs["app_required"] == "false"
    assert outputs["review_tooling_changed"] == "true"


def test_runtime_python_requires_app_gate(tmp_path: Path) -> None:
    changed = _changed_file(tmp_path, "src/anomaly_metric_creator/server.py")

    result = _run(str(changed))

    assert result.returncode == 0, result.stderr
    outputs = _outputs(result.stdout)
    assert outputs["lightweight_only"] == "false"
    assert outputs["app_required"] == "true"
    assert outputs["python_changed"] == "true"


def test_dependency_and_workflow_changes_force_app_gate(tmp_path: Path) -> None:
    changed = _changed_file(
        tmp_path,
        "pyproject.toml",
        ".github/workflows/ci.yml",
    )

    result = _run(str(changed))

    assert result.returncode == 0, result.stderr
    outputs = _outputs(result.stdout)
    assert outputs["app_required"] == "true"
    assert outputs["dependency_changed"] == "true"
    assert outputs["workflow_changed"] == "true"
    assert outputs["lightweight_only"] == "false"


def test_review_tooling_scripts_stay_in_lightweight_lane(tmp_path: Path) -> None:
    changed = _changed_file(
        tmp_path,
        "scripts/classify_ci_changes.sh",
        "scripts/trellis-full-check.sh",
    )

    result = _run(str(changed))

    assert result.returncode == 0, result.stderr
    outputs = _outputs(result.stdout)
    assert outputs["lightweight_only"] == "true"
    assert outputs["app_required"] == "false"
    assert outputs["review_tooling_changed"] == "true"


def test_force_app_overrides_lightweight_paths(tmp_path: Path) -> None:
    changed = _changed_file(tmp_path, "docs/REVIEW_PATTERNS.md")

    result = _run("--force-app", str(changed))

    assert result.returncode == 0, result.stderr
    outputs = _outputs(result.stdout)
    assert outputs["lightweight_only"] == "false"
    assert outputs["app_required"] == "true"


def test_github_output_mode_writes_outputs_file(tmp_path: Path) -> None:
    changed = _changed_file(tmp_path, "README.md")
    output = tmp_path / "github-output.txt"
    env = {**os.environ, "GITHUB_OUTPUT": str(output)}

    result = _run("--github-output", str(changed), env=env)

    assert result.returncode == 0, result.stderr
    outputs = _outputs(output.read_text(encoding="utf-8"))
    assert outputs["changed_count"] == "1"
    assert outputs["lightweight_only"] == "true"


def test_missing_changed_file_list_exits_two(tmp_path: Path) -> None:
    result = _run(str(tmp_path / "missing.txt"))

    assert result.returncode == 2
    assert "changed-files list not found" in result.stderr


def test_default_collection_includes_untracked_files(tmp_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / "README.md").write_text("# tmp\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=tmp_path, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=AMC Tests",
            "-c",
            "user.email=amc-tests@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-m",
            "init",
        ],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "REVIEW_PATTERNS.md").write_text("# Review\n", encoding="utf-8")

    env = {**os.environ, "TRELLIS_CI_BASE_REF": "HEAD"}
    result = subprocess.run(
        ["bash", str(SCRIPT)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    outputs = _outputs(result.stdout)
    assert outputs["changed_count"] == "1"
    assert outputs["lightweight_only"] == "true"
