"""Acceptance tests for ``scripts/classify-ci-changes.sh``.

The classifier is the local source of truth for the CI workflow's cheap versus
full gate decisions. These tests keep the path buckets explicit so workflow
review does not have to re-litigate the same globs on every PR.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "classify-ci-changes.sh"
# Repo-owned tooling that is cheap to change and has no app-level blast radius.
# The pack's own scripts are deliberately absent: since the thin conversion they
# live on the machine, not in this tree, so no diff here can contain one.
REPO_TOOLING_PATHS = ("scripts/update_repomix",)


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
        "docs/spec/amc/backend/testing-quality.md",
        ".github/prompts/review-pr.prompt.md",
        ".agents/skills/trellis-before-dev/SKILL.md",
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
        "scripts/classify-ci-changes.sh",
        "scripts/classify_ci_changes.sh",
        "scripts/check-review-preflight.mjs",
        ".sd-ai-command-pack/pr-body-scope.json",
    )

    result = _run(str(changed))

    assert result.returncode == 0, result.stderr
    outputs = _outputs(result.stdout)
    assert outputs["lightweight_only"] == "true"
    assert outputs["app_required"] == "false"
    assert outputs["review_tooling_changed"] == "true"


@pytest.mark.parametrize("path", REPO_TOOLING_PATHS)
def test_untested_repo_tooling_stays_in_lightweight_lane(
    tmp_path: Path,
    path: str,
) -> None:
    changed = _changed_file(tmp_path, path)

    result = _run(str(changed))

    assert result.returncode == 0, result.stderr
    outputs = _outputs(result.stdout)
    assert outputs["lightweight_only"] == "true"
    assert outputs["app_required"] == "false"
    assert outputs["review_tooling_changed"] == "false"


@pytest.mark.parametrize(
    "path",
    (
        "scripts/sync-agent-skills.py",
        "tools/check_role_name_leaks.py",
        "tools/benchmark_combine.py",
    ),
)
def test_tested_or_conservatively_retained_tooling_requires_app_gate(
    tmp_path: Path,
    path: str,
) -> None:
    changed = _changed_file(tmp_path, path)

    result = _run(str(changed))

    assert result.returncode == 0, result.stderr
    outputs = _outputs(result.stdout)
    assert outputs["lightweight_only"] == "false"
    assert outputs["app_required"] == "true"


def test_repo_tooling_mixed_with_runtime_path_requires_app_gate(tmp_path: Path) -> None:
    changed = _changed_file(
        tmp_path,
        "scripts/update_repomix",
        "src/anomaly_metric_creator/legacy.py",
    )

    result = _run(str(changed))

    assert result.returncode == 0, result.stderr
    outputs = _outputs(result.stdout)
    assert outputs["lightweight_only"] == "false"
    assert outputs["app_required"] == "true"


def test_command_pack_payload_and_audit_artifacts_are_lightweight(
    tmp_path: Path,
) -> None:
    changed = _changed_file(
        tmp_path,
        ".sd-ai-command-pack/installed-targets.txt",
        ".sd-ai-command-pack/manifest.json",
        ".sd-ai-command-pack/provenance.json",
        ".sd-ai-command-pack/review-preflight.json",
        ".trellis/audit/ledger.md",
        ".trellis/audit/report-2026-07-17.md",
    )

    result = _run(str(changed))

    assert result.returncode == 0, result.stderr
    outputs = _outputs(result.stdout)
    assert outputs["lightweight_only"] == "true"
    assert outputs["app_required"] == "false"
    assert outputs["review_tooling_changed"] == "true"


def test_opencode_package_json_forces_dependency_lane(tmp_path: Path) -> None:
    # `.opencode/package.json` is managed by the `npm` Dependabot ecosystem, so
    # a bump must run the full matrix + Socket re-scan. It also matches
    # `.opencode/*` (review tooling), so this pins that the npm-manifest
    # dependency classification wins over the lightweight review-tooling route.
    # The nested path also proves the `*/package.json` glob crosses `/`.
    changed = _changed_file(
        tmp_path,
        ".opencode/package.json",
        ".opencode/package-lock.json",
    )

    result = _run(str(changed))

    assert result.returncode == 0, result.stderr
    outputs = _outputs(result.stdout)
    assert outputs["dependency_changed"] == "true"
    assert outputs["app_required"] == "true"
    assert outputs["lightweight_only"] == "false"


def test_copied_trellis_and_sd_adapters_stay_in_lightweight_lane(tmp_path: Path) -> None:
    changed = _changed_file(
        tmp_path,
        ".github/agents/trellis-check.agent.md",
        ".github/skills/trellis-check/SKILL.md",
        ".github/copilot/hooks/session-start.py",
        ".github/prompts/sd-review-pr.prompt.md",
        ".sd-ai-command-pack/installed-targets.txt",
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


def test_explicit_path_list_after_separator_is_supported() -> None:
    result = _run(
        "--",
        "docs/REVIEW_PATTERNS.md",
        ".sd-ai-command-pack/installed-targets.txt",
    )

    assert result.returncode == 0, result.stderr
    outputs = _outputs(result.stdout)
    assert outputs["changed_count"] == "2"
    assert outputs["lightweight_only"] == "true"
    assert outputs["review_tooling_changed"] == "true"


def test_explicit_path_list_allows_flag_like_paths() -> None:
    result = _run("--", "-literal-file.py")

    assert result.returncode == 0, result.stderr
    outputs = _outputs(result.stdout)
    assert outputs["changed_count"] == "1"
    assert outputs["python_changed"] == "true"
    assert outputs["app_required"] == "true"


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
