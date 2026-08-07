"""Tests for `tools/check_guard_ci_coverage.py`.

The fixture builds a miniature repository -- a pre-commit config, a CI
workflow, a tests directory, and a git index -- so each coverage rule can be
exercised without depending on this repository's real lint inventory. The final
test runs the guard over the live tree.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "tools" / "check_guard_ci_coverage.py"
CLASSIFIER = REPO_ROOT / "scripts" / "classify-ci-changes.sh"


def _run(repo: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--repo", str(repo)],
        capture_output=True,
        text=True,
        check=False,
    )


def _hook(hook_id: str, tool: str, pattern: str, marker: str = "") -> str:
    prefix = f"      {marker}\n" if marker else ""
    return (
        f"{prefix}      - id: {hook_id}\n"
        f"        name: {hook_id}\n"
        f"        entry: python tools/{tool}\n"
        "        language: python\n"
        f"        files: '{pattern}'\n"
        "        pass_filenames: true\n"
    )


def _workflow(*, guard_steps: dict[str, list[str]]) -> str:
    """Build a CI workflow.

    `guard_steps` maps a job name to the tools it runs. The three job names
    below mirror the real workflow's gating so the guard classifies them into
    the same lanes.
    """
    conditions = {
        "changes": None,
        "lightweight_readiness": "needs.changes.outputs.lightweight_only == 'true'",
        "test_light": "needs.changes.outputs.app_required == 'true'",
    }
    lines = ["name: CI", "on: [pull_request]", "jobs:"]
    for job, condition in conditions.items():
        lines.append(f"  {job}:")
        lines.append("    runs-on: ubuntu-latest")
        if condition:
            lines.append(f"    if: {condition}")
        lines.append("    steps:")
        tools = guard_steps.get(job, [])
        if tools:
            runs = "\n".join(f"          python tools/{tool}" for tool in tools)
            lines.append("      - run: |\n" + runs)
        else:
            lines.append("      - run: echo noop")
    return "\n".join(lines) + "\n"


def _test_file(tool: str, *, live: bool) -> str:
    """A test module owning `tool`, optionally with a live-tree test."""
    body = (
        "from pathlib import Path\n\n"
        "REPO_ROOT = Path(__file__).resolve().parents[1]\n"
        f'SCRIPT = REPO_ROOT / "tools" / "{tool}"\n'
        'TREE = REPO_ROOT / "src"\n\n\n'
        "def test_no_args_exits_two() -> None:\n"
        "    assert SCRIPT.name\n"
    )
    if live:
        body += "\n\ndef test_real_tree_is_clean() -> None:\n    assert TREE.name\n"
    return body


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A miniature repository with a real git index and the real classifier."""
    (tmp_path / "tools").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "scripts").mkdir()
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / "src" / "anomaly_metric_creator").mkdir(parents=True)
    (tmp_path / ".trellis" / "tasks" / "demo").mkdir(parents=True)

    # The guard shells out to the real classifier, so copy it in rather than
    # reimplementing its lane rules in the fixture.
    (tmp_path / "scripts" / "classify-ci-changes.sh").write_text(
        CLASSIFIER.read_text(encoding="utf-8"), encoding="utf-8"
    )
    (tmp_path / "src" / "anomaly_metric_creator" / "core.py").write_text(
        "x = 1\n", encoding="utf-8"
    )
    (tmp_path / ".trellis" / "tasks" / "demo" / "prd.md").write_text(
        "# demo\n", encoding="utf-8"
    )

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    return tmp_path


def _write(repo: Path, *, hooks: str, workflow: str, tests: dict[str, str]) -> None:
    (repo / ".pre-commit-config.yaml").write_text(
        "repos:\n  - repo: local\n    hooks:\n" + hooks, encoding="utf-8"
    )
    (repo / ".github" / "workflows" / "ci.yml").write_text(workflow, encoding="utf-8")
    for name, content in tests.items():
        (repo / "tests" / name).write_text(content, encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)


def test_lightweight_guard_without_an_unconditional_step_is_flagged(repo: Path) -> None:
    """The historical bug: a task-text lint with only a live-tree test."""
    _write(
        repo,
        hooks=_hook("task-criteria", "check_task_criteria.py", r"^\.trellis/tasks/.*\.md$"),
        workflow=_workflow(guard_steps={}),
        tests={"test_task_criteria_lint.py": _test_file("check_task_criteria.py", live=True)},
    )
    result = _run(repo)
    assert result.returncode == 1, result.stdout
    assert "select the lightweight lane" in result.stderr
    assert "runs in no CI job at all" in result.stderr


def test_lightweight_guard_in_the_unconditional_job_passes(repo: Path) -> None:
    _write(
        repo,
        hooks=_hook("task-criteria", "check_task_criteria.py", r"^\.trellis/tasks/.*\.md$"),
        workflow=_workflow(guard_steps={"changes": ["check_task_criteria.py"]}),
        tests={},
    )
    result = _run(repo)
    assert result.returncode == 0, result.stderr


def test_lightweight_guard_in_a_lightweight_gated_job_passes(repo: Path) -> None:
    """`lightweight_readiness` runs in the lane, so it covers LIGHT."""
    _write(
        repo,
        hooks=_hook("placeholders", "check_placeholders.py", r"^\.trellis/tasks/.*\.md$"),
        workflow=_workflow(guard_steps={"lightweight_readiness": ["check_placeholders.py"]}),
        tests={"test_placeholders_lint.py": _test_file("check_placeholders.py", live=True)},
    )
    result = _run(repo)
    assert result.returncode == 0, result.stderr


def test_lightweight_gated_job_alone_does_not_cover_the_app_lane(repo: Path) -> None:
    """A mixed PR skips `lightweight_readiness`; without a live test nothing runs."""
    _write(
        repo,
        hooks=_hook("placeholders", "check_placeholders.py", r"^\.trellis/tasks/.*\.md$"),
        workflow=_workflow(guard_steps={"lightweight_readiness": ["check_placeholders.py"]}),
        tests={"test_placeholders_lint.py": _test_file("check_placeholders.py", live=False)},
    )
    result = _run(repo)
    assert result.returncode == 1, result.stdout
    assert "app-required pull request" in result.stderr


def test_source_guard_is_covered_by_a_live_tree_test_alone(repo: Path) -> None:
    """Source files always force the app lane, where test jobs run."""
    _write(
        repo,
        hooks=_hook("trace-payload", "check_trace.py", r"^src/.*\.py$"),
        workflow=_workflow(guard_steps={}),
        tests={"test_trace_lint.py": _test_file("check_trace.py", live=True)},
    )
    result = _run(repo)
    assert result.returncode == 0, result.stderr


def test_source_guard_without_any_coverage_is_flagged(repo: Path) -> None:
    _write(
        repo,
        hooks=_hook("trace-payload", "check_trace.py", r"^src/.*\.py$"),
        workflow=_workflow(guard_steps={}),
        tests={"test_trace_lint.py": _test_file("check_trace.py", live=False)},
    )
    result = _run(repo)
    assert result.returncode == 1, result.stdout
    assert "app-required pull request" in result.stderr


def test_a_tmp_path_test_is_not_accepted_as_live_tree_evidence(repo: Path) -> None:
    """A synthetic-tree test proves nothing about this repository."""
    synthetic = (
        "from pathlib import Path\n\n"
        "REPO_ROOT = Path(__file__).resolve().parents[1]\n"
        'SCRIPT = REPO_ROOT / "tools" / "check_trace.py"\n'
        'TREE = REPO_ROOT / "src"\n\n\n'
        "def test_synthetic(tmp_path: Path) -> None:\n"
        "    assert TREE.name and tmp_path\n"
    )
    _write(
        repo,
        hooks=_hook("trace-payload", "check_trace.py", r"^src/.*\.py$"),
        workflow=_workflow(guard_steps={}),
        tests={"test_trace_lint.py": synthetic},
    )
    result = _run(repo)
    assert result.returncode == 1, result.stdout


def test_allow_marker_exempts_a_hook(repo: Path) -> None:
    _write(
        repo,
        hooks=_hook(
            "trace-payload",
            "check_trace.py",
            r"^src/.*\.py$",
            marker="# guard-ci-coverage: allow covered by the import-time suite",
        ),
        workflow=_workflow(guard_steps={}),
        tests={"test_trace_lint.py": _test_file("check_trace.py", live=False)},
    )
    result = _run(repo)
    assert result.returncode == 0, result.stderr

    listing = subprocess.run(
        [sys.executable, str(SCRIPT), "--repo", str(repo), "--list"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert "allowed: covered by the import-time suite" in listing.stdout


def test_unclassifiable_job_condition_is_a_structural_error(repo: Path) -> None:
    """An `if:` naming both lanes must fail loudly, not be assumed safe."""
    workflow = (
        "name: CI\non: [pull_request]\njobs:\n"
        "  changes:\n    runs-on: ubuntu-latest\n"
        "    if: needs.changes.outputs.lightweight_only == 'true' && "
        "needs.changes.outputs.app_required == 'true'\n"
        "    steps:\n      - run: python tools/check_trace.py\n"
    )
    _write(
        repo,
        hooks=_hook("trace-payload", "check_trace.py", r"^src/.*\.py$"),
        workflow=workflow,
        tests={},
    )
    result = _run(repo)
    assert result.returncode == 2, result.stdout
    assert "cannot classify job condition" in result.stderr


def test_a_hook_matching_no_tracked_file_is_skipped(repo: Path) -> None:
    _write(
        repo,
        hooks=_hook("orphan", "check_orphan.py", r"^does/not/exist/.*\.py$"),
        workflow=_workflow(guard_steps={}),
        tests={},
    )
    result = _run(repo)
    assert result.returncode == 0, result.stderr

    listing = subprocess.run(
        [sys.executable, str(SCRIPT), "--repo", str(repo), "--list"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert "matches no tracked file" in listing.stdout


def test_a_config_with_no_checkable_hook_is_a_structural_error(repo: Path) -> None:
    """Silently passing on an empty inventory would be the worst failure."""
    (repo / ".pre-commit-config.yaml").write_text(
        "repos:\n  - repo: local\n    hooks:\n"
        "      - id: ruff\n        name: ruff\n        entry: ruff\n"
        "        language: system\n",
        encoding="utf-8",
    )
    (repo / ".github" / "workflows" / "ci.yml").write_text(
        _workflow(guard_steps={}), encoding="utf-8"
    )
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    result = _run(repo)
    assert result.returncode == 2, result.stdout
    assert "silently passing" in result.stderr


def test_missing_pre_commit_config_exits_two(tmp_path: Path) -> None:
    result = _run(tmp_path)
    assert result.returncode == 2, result.stdout
    assert "not found" in result.stderr


def test_live_repository_guard_coverage_is_clean() -> None:
    """Every lint in this repository runs in the lanes its files select."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--repo", str(REPO_ROOT)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_live_listing_covers_every_checkable_hook() -> None:
    """The listing must not silently shrink to a subset of the inventory."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--repo", str(REPO_ROOT), "--list"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    rows = [line for line in result.stdout.splitlines() if line.strip()]
    assert len(rows) >= 10, result.stdout
    assert any("task-criteria-commands" in row for row in rows), result.stdout
