"""Tests for `tools/check_guard_ci_coverage.py`.

The fixture builds a miniature repository -- a pre-commit config, a CI
workflow, a tools directory, a tests directory, and a git index -- so each
coverage rule can be exercised without depending on this repository's real lint
inventory. The last two tests run the guard over the live tree.

The miniature workflow mirrors the real one's four job shapes, because the
guard's whole job is telling them apart:

    changes                 no `if:`                          -> LIGHT+QUICK+FULL
    lightweight_readiness   lightweight_only == 'true'        -> LIGHT
    quick_check             app_required && !full_ci          -> QUICK
    test_light              app_required && full_ci           -> FULL

`quick_check` runs an explicit list of test files; `test_light` runs the whole
suite by marker. That asymmetry is why a live-tree test can cover FULL without
covering QUICK.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "tools" / "check_guard_ci_coverage.py"
CLASSIFIER = REPO_ROOT / "scripts" / "classify-ci-changes.sh"


def _run(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--repo", str(repo), *args],
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


def _types_hook(hook_id: str, tool: str) -> str:
    """A hook that selects files by `types:` rather than a `files:` regex."""
    return (
        f"      - id: {hook_id}\n"
        f"        name: {hook_id}\n"
        f"        entry: python tools/{tool}\n"
        "        language: python\n"
        "        types: [text]\n"
    )


def _stage_hook(hook_id: str, tool: str, stage: str) -> str:
    """A hook with no file selector at all, e.g. `pre-push`."""
    return (
        f"      - id: {hook_id}\n"
        f"        name: {hook_id}\n"
        f"        entry: python tools/{tool}\n"
        "        language: python\n"
        f"        stages: [{stage}]\n"
        "        pass_filenames: false\n"
    )


def _workflow(
    *,
    guard_steps: dict[str, list[str]],
    quick_tests: tuple[str, ...] = (),
    collect_only_job: bool = False,
) -> str:
    """Build a CI workflow whose four jobs mirror the real lane gating."""
    conditions = {
        "changes": None,
        "lightweight_readiness": "needs.changes.outputs.lightweight_only == 'true'",
        "quick_check": (
            "needs.changes.outputs.app_required == 'true' && "
            "needs.changes.outputs.full_ci_requested != 'true'"
        ),
        "test_light": (
            "needs.changes.outputs.app_required == 'true' && "
            "needs.changes.outputs.full_ci_requested == 'true'"
        ),
    }
    lines = ["name: CI", "on: [pull_request]", "jobs:"]
    for job, condition in conditions.items():
        lines.append(f"  {job}:")
        lines.append("    runs-on: ubuntu-latest")
        if condition:
            lines.append(f"    if: {condition}")
        lines.append("    steps:")
        body = [f"          python tools/{tool}" for tool in guard_steps.get(job, [])]
        if job == "quick_check" and quick_tests:
            body.append("          pytest -q " + " ".join(f"tests/{t}" for t in quick_tests))
        if job == "test_light":
            body.append('          pytest -m "not heavy"')
        if not body:
            body = ["          echo noop"]
        lines.append("      - run: |\n" + "\n".join(body))
    if collect_only_job:
        # An exotic `if:` the guard must never need to classify, on a job that
        # executes no test.
        lines += [
            "  windows_collection:",
            "    runs-on: windows-latest",
            "    if: github.event_name == 'pull_request'",
            "    steps:",
            "      - run: pytest --collect-only -q",
        ]
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


def _write(
    repo: Path,
    *,
    hooks: str,
    workflow: str,
    tests: dict[str, str],
    tools: dict[str, str] | None = None,
) -> None:
    (repo / ".pre-commit-config.yaml").write_text(
        "repos:\n  - repo: local\n    hooks:\n" + hooks, encoding="utf-8"
    )
    (repo / ".github" / "workflows" / "ci.yml").write_text(workflow, encoding="utf-8")
    for name, content in tests.items():
        (repo / "tests" / name).write_text(content, encoding="utf-8")
    for name, content in (tools or {}).items():
        (repo / "tools" / name).write_text(content, encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)


TASKS = r"^\.trellis/tasks/.*\.md$"
SOURCE = r"^src/.*\.py$"


# --------------------------------------------------------------------------
# Lane coverage for file-selecting hooks
# --------------------------------------------------------------------------


def test_lightweight_guard_without_an_unconditional_step_is_flagged(repo: Path) -> None:
    """The historical bug: a task-text lint with only a full-lane test."""
    _write(
        repo,
        hooks=_hook("task-criteria", "check_task_criteria.py", TASKS),
        workflow=_workflow(guard_steps={}),
        tests={"test_task_criteria_lint.py": _test_file("check_task_criteria.py", live=True)},
    )
    result = _run(repo)
    assert result.returncode == 1, result.stdout
    assert "not exercised in the LIGHT lane" in result.stderr
    assert "runs in no CI job at all" in result.stderr


def test_lightweight_guard_in_the_unconditional_job_passes(repo: Path) -> None:
    _write(
        repo,
        hooks=_hook("task-criteria", "check_task_criteria.py", TASKS),
        workflow=_workflow(guard_steps={"changes": ["check_task_criteria.py"]}),
        tests={},
    )
    result = _run(repo)
    assert result.returncode == 0, result.stderr


def test_lightweight_gated_job_plus_a_quick_listed_test_passes(repo: Path) -> None:
    """The real `python-syntax` shape: LIGHT from a job, QUICK+FULL from tests."""
    _write(
        repo,
        hooks=_hook("placeholders", "check_placeholders.py", TASKS),
        workflow=_workflow(
            guard_steps={"lightweight_readiness": ["check_placeholders.py"]},
            quick_tests=("test_placeholders_lint.py",),
        ),
        tests={"test_placeholders_lint.py": _test_file("check_placeholders.py", live=True)},
    )
    result = _run(repo)
    assert result.returncode == 0, result.stderr


def test_a_live_test_outside_the_quick_list_does_not_cover_the_quick_lane(
    repo: Path,
) -> None:
    """`quick_check` runs only the files it names, so FULL coverage is not QUICK.

    Collapsing QUICK and FULL into one "app" lane hides this: the lint has a
    live-tree test and a lightweight-gated job, which looks like full coverage
    until you notice an ordinary `synchronize` push runs neither.
    """
    _write(
        repo,
        hooks=_hook("placeholders", "check_placeholders.py", TASKS),
        workflow=_workflow(
            guard_steps={"lightweight_readiness": ["check_placeholders.py"]},
            quick_tests=("test_unrelated.py",),
        ),
        tests={"test_placeholders_lint.py": _test_file("check_placeholders.py", live=True)},
    )
    result = _run(repo)
    assert result.returncode == 1, result.stdout
    assert "not exercised in the QUICK lane" in result.stderr
    assert "not exercised in the LIGHT lane" not in result.stderr


def test_lightweight_gated_job_alone_does_not_cover_the_app_lanes(repo: Path) -> None:
    """A mixed PR skips `lightweight_readiness`; without a test nothing runs."""
    _write(
        repo,
        hooks=_hook("placeholders", "check_placeholders.py", TASKS),
        workflow=_workflow(guard_steps={"lightweight_readiness": ["check_placeholders.py"]}),
        tests={"test_placeholders_lint.py": _test_file("check_placeholders.py", live=False)},
    )
    result = _run(repo)
    assert result.returncode == 1, result.stdout
    assert "not exercised in the QUICK lane" in result.stderr
    assert "not exercised in the FULL lane" in result.stderr


def test_a_mixed_pattern_reaches_light_through_its_lightweight_subset(
    repo: Path,
) -> None:
    """One app-required path must not mask the lightweight paths beside it.

    The pattern watches both `src/**.py` (app-required) and
    `.trellis/tasks/**.md` (lightweight). Classifying the matched set as one
    unit yields `app_required`, hiding the gap; a PR touching only the task
    file really does select the lightweight lane, where nothing runs this
    guard.
    """
    _write(
        repo,
        hooks=_hook("mixed", "check_mixed.py", r"^(src/.*\.py|\.trellis/tasks/.*\.md)$"),
        workflow=_workflow(
            guard_steps={}, quick_tests=("test_mixed_lint.py",)
        ),
        tests={"test_mixed_lint.py": _test_file("check_mixed.py", live=True)},
    )
    result = _run(repo)
    assert result.returncode == 1, result.stdout
    assert "not exercised in the LIGHT lane" in result.stderr


def test_source_guard_is_covered_by_a_quick_listed_live_test(repo: Path) -> None:
    """Source files never reach LIGHT, so tests in both app lanes suffice."""
    _write(
        repo,
        hooks=_hook("resource-cost", "check_resource_cost.py", SOURCE),
        workflow=_workflow(guard_steps={}, quick_tests=("test_resource_cost_lint.py",)),
        tests={"test_resource_cost_lint.py": _test_file("check_resource_cost.py", live=True)},
    )
    result = _run(repo)
    assert result.returncode == 0, result.stderr


def test_source_guard_without_any_coverage_is_flagged(repo: Path) -> None:
    _write(
        repo,
        hooks=_hook("resource-cost", "check_resource_cost.py", SOURCE),
        workflow=_workflow(guard_steps={}),
        tests={"test_resource_cost_lint.py": _test_file("check_resource_cost.py", live=False)},
    )
    result = _run(repo)
    assert result.returncode == 1, result.stdout
    assert "it has no live-tree test" in result.stderr


def test_a_tmp_path_test_is_not_accepted_as_live_tree_evidence(repo: Path) -> None:
    """A test taking a fixture builds a synthetic tree and proves nothing."""
    synthetic = (
        "from pathlib import Path\n\n"
        "REPO_ROOT = Path(__file__).resolve().parents[1]\n"
        'SCRIPT = REPO_ROOT / "tools" / "check_resource_cost.py"\n'
        'TREE = REPO_ROOT / "src"\n\n\n'
        "def test_synthetic(tmp_path: Path) -> None:\n"
        "    assert TREE.name and tmp_path\n"
    )
    _write(
        repo,
        hooks=_hook("resource-cost", "check_resource_cost.py", SOURCE),
        workflow=_workflow(guard_steps={}, quick_tests=("test_resource_cost_lint.py",)),
        tests={"test_resource_cost_lint.py": synthetic},
    )
    result = _run(repo)
    assert result.returncode == 1, result.stdout
    assert "it has no live-tree test" in result.stderr


def test_a_collect_only_job_is_neither_a_test_lane_nor_a_classification_error(
    repo: Path,
) -> None:
    """`--collect-only` executes nothing, and its exotic `if:` must not raise."""
    _write(
        repo,
        hooks=_hook("resource-cost", "check_resource_cost.py", SOURCE),
        workflow=_workflow(
            guard_steps={},
            quick_tests=("test_resource_cost_lint.py",),
            collect_only_job=True,
        ),
        tests={"test_resource_cost_lint.py": _test_file("check_resource_cost.py", live=True)},
    )
    result = _run(repo)
    assert result.returncode == 0, result.stderr


# --------------------------------------------------------------------------
# Hooks that select files by `types:`
# --------------------------------------------------------------------------


def test_a_types_selected_hook_is_lane_checked(repo: Path) -> None:
    """`types:` hooks were invisible to this guard, so they were never checked."""
    _write(
        repo,
        hooks=_types_hook("role-name-leaks", "check_role_names.py"),
        workflow=_workflow(guard_steps={}),
        tests={"test_role_names_lint.py": _test_file("check_role_names.py", live=True)},
    )
    result = _run(repo)
    assert result.returncode == 1, result.stdout
    assert "role-name-leaks" in result.stderr
    assert "not exercised in the LIGHT lane" in result.stderr


def test_a_types_selected_hook_in_the_unconditional_job_passes(repo: Path) -> None:
    _write(
        repo,
        hooks=_types_hook("role-name-leaks", "check_role_names.py"),
        workflow=_workflow(guard_steps={"changes": ["check_role_names.py"]}),
        tests={},
    )
    result = _run(repo)
    assert result.returncode == 0, result.stderr


# --------------------------------------------------------------------------
# Lints with no file-selecting hook
# --------------------------------------------------------------------------


def test_a_lint_that_runs_nowhere_is_flagged(repo: Path) -> None:
    """No hook, no CI job, no caller -- the lint is dead weight."""
    _write(
        repo,
        hooks=_hook("task-criteria", "check_task_criteria.py", TASKS),
        workflow=_workflow(guard_steps={"changes": ["check_task_criteria.py"]}),
        tests={},
        tools={"check_orphan.py": "# nothing runs me\n"},
    )
    result = _run(repo)
    assert result.returncode == 1, result.stdout
    assert "tools/check_orphan.py: this lint runs nowhere" in result.stderr


def test_a_stage_only_hook_carries_no_lane_duty_but_must_run_somewhere(
    repo: Path,
) -> None:
    """A `pre-push` hook selects no files, so it has no lane -- but CI names it."""
    _write(
        repo,
        hooks=(
            _hook("task-criteria", "check_task_criteria.py", TASKS)
            + _stage_hook("branch-name", "check_branch_name.py", "pre-push")
        ),
        workflow=_workflow(
            guard_steps={"changes": ["check_task_criteria.py", "check_branch_name.py"]}
        ),
        tests={},
        tools={"check_branch_name.py": "# pre-push guard\n"},
    )
    result = _run(repo)
    assert result.returncode == 0, result.stderr


def test_an_unlaned_lint_is_reachable_through_a_calling_script(repo: Path) -> None:
    """`check_approval_duplicate.py` runs only from `tools/pr_comment.sh`."""
    _write(
        repo,
        hooks=_hook("task-criteria", "check_task_criteria.py", TASKS),
        workflow=_workflow(guard_steps={"changes": ["check_task_criteria.py"]}),
        tests={},
        tools={
            "check_approval_duplicate.py": "# comment gate\n",
            "pr_comment.sh": "python tools/check_approval_duplicate.py \"$@\"\n",
        },
    )
    result = _run(repo)
    assert result.returncode == 0, result.stderr


def test_an_unlaned_lint_can_be_exempted_by_a_marker_in_its_own_source(
    repo: Path,
) -> None:
    _write(
        repo,
        hooks=_hook("task-criteria", "check_task_criteria.py", TASKS),
        workflow=_workflow(guard_steps={"changes": ["check_task_criteria.py"]}),
        tests={},
        tools={
            "check_orphan.py": "# guard-ci-coverage: allow run by hand during release\n"
        },
    )
    result = _run(repo)
    assert result.returncode == 0, result.stderr


def test_a_test_file_does_not_make_an_unlaned_lint_reachable(repo: Path) -> None:
    """A test proves the lint works, not that anything runs it."""
    _write(
        repo,
        hooks=_hook("task-criteria", "check_task_criteria.py", TASKS),
        workflow=_workflow(guard_steps={"changes": ["check_task_criteria.py"]}),
        tests={"test_orphan_lint.py": _test_file("check_orphan.py", live=True)},
        tools={"check_orphan.py": "# nothing runs me\n"},
    )
    result = _run(repo)
    assert result.returncode == 1, result.stdout
    assert "tools/check_orphan.py: this lint runs nowhere" in result.stderr


# --------------------------------------------------------------------------
# A lint's own tests must run on the pull request that edits it
# --------------------------------------------------------------------------


def test_a_lints_own_tests_must_run_in_the_quick_lane(repo: Path) -> None:
    """Full lane coverage does not imply the lint's tests ever run.

    The lint has an unconditional CI step, so every lane executes it and the
    lane rules are satisfied. But editing the lint is an app-required change,
    and the quick lane runs only the files `quick_check` names -- so a logic
    regression in the lint itself would merge green.
    """
    _write(
        repo,
        hooks=_hook("task-criteria", "check_task_criteria.py", TASKS),
        workflow=_workflow(guard_steps={"changes": ["check_task_criteria.py"]}),
        tests={"test_task_criteria_lint.py": _test_file("check_task_criteria.py", live=True)},
    )
    result = _run(repo)
    assert result.returncode == 1, result.stdout
    assert "do not run in the QUICK lane" in result.stderr
    assert "not exercised in the" not in result.stderr


def test_a_lints_own_tests_in_the_quick_list_satisfy_the_rule(repo: Path) -> None:
    _write(
        repo,
        hooks=_hook("task-criteria", "check_task_criteria.py", TASKS),
        workflow=_workflow(
            guard_steps={"changes": ["check_task_criteria.py"]},
            quick_tests=("test_task_criteria_lint.py",),
        ),
        tests={"test_task_criteria_lint.py": _test_file("check_task_criteria.py", live=True)},
    )
    result = _run(repo)
    assert result.returncode == 0, result.stderr


def test_a_synthetic_only_test_file_still_counts_as_owning_its_lint(repo: Path) -> None:
    """Owning a lint is about the SCRIPT constant, not about live-tree tests.

    A file that only tests the lint against `tmp_path` trees proves nothing
    about this repository -- so it is not lane coverage -- but it still has to
    run when the lint changes.
    """
    _write(
        repo,
        hooks=_hook("task-criteria", "check_task_criteria.py", TASKS),
        workflow=_workflow(guard_steps={"changes": ["check_task_criteria.py"]}),
        tests={"test_task_criteria_lint.py": _test_file("check_task_criteria.py", live=False)},
    )
    result = _run(repo)
    assert result.returncode == 1, result.stdout
    assert "do not run in the QUICK lane" in result.stderr


def test_a_lint_with_no_test_file_at_all_is_not_flagged_by_this_rule(repo: Path) -> None:
    """The rule is about stale wiring, not about mandating test coverage."""
    _write(
        repo,
        hooks=_hook("task-criteria", "check_task_criteria.py", TASKS),
        workflow=_workflow(guard_steps={"changes": ["check_task_criteria.py"]}),
        tests={},
    )
    result = _run(repo)
    assert result.returncode == 0, result.stderr


# --------------------------------------------------------------------------
# Escape hatch, skips, and structural errors
# --------------------------------------------------------------------------


def test_allow_marker_exempts_a_hook(repo: Path) -> None:
    _write(
        repo,
        hooks=_hook(
            "placeholders",
            "check_placeholders.py",
            TASKS,
            marker="# guard-ci-coverage: allow covered by the release job",
        ),
        workflow=_workflow(guard_steps={}),
        tests={},
    )
    result = _run(repo)
    assert result.returncode == 0, result.stderr
    listing = _run(repo, "--list")
    assert "covered by the release job" in listing.stdout


def test_unclassifiable_job_condition_is_a_structural_error(repo: Path) -> None:
    """A lane the guard cannot name is failed loudly, never assumed safe."""
    workflow = (
        "name: CI\non: [pull_request]\njobs:\n"
        "  mystery:\n"
        "    runs-on: ubuntu-latest\n"
        "    if: needs.changes.outputs.something_else == 'true'\n"
        "    steps:\n"
        "      - run: python tools/check_placeholders.py\n"
    )
    _write(
        repo,
        hooks=_hook("placeholders", "check_placeholders.py", TASKS),
        workflow=workflow,
        tests={},
    )
    result = _run(repo)
    assert result.returncode == 2, result.stdout
    assert "cannot classify job condition into CI lanes" in result.stderr


def test_a_hook_matching_no_tracked_file_is_skipped(repo: Path) -> None:
    _write(
        repo,
        hooks=_hook("nothing", "check_nothing.py", r"^does/not/exist/.*\.md$"),
        workflow=_workflow(guard_steps={}),
        tests={},
    )
    result = _run(repo)
    assert result.returncode == 0, result.stderr
    listing = _run(repo, "--list")
    assert "selects no tracked file" in listing.stdout


def test_a_config_with_no_checkable_hook_is_a_structural_error(repo: Path) -> None:
    """An empty inventory means the guard is passing without checking anything."""
    _write(
        repo,
        hooks=(
            "      - id: unrelated\n"
            "        name: unrelated\n"
            "        entry: echo hello\n"
            "        language: system\n"
        ),
        workflow=_workflow(guard_steps={}),
        tests={},
    )
    result = _run(repo)
    assert result.returncode == 2, result.stdout
    assert "nothing to lane-check" in result.stderr


def test_missing_pre_commit_config_exits_two(tmp_path: Path) -> None:
    result = _run(tmp_path)
    assert result.returncode == 2, result.stdout
    assert "not found" in result.stderr


# --------------------------------------------------------------------------
# The live repository
# --------------------------------------------------------------------------


def test_live_repository_guard_coverage_is_clean() -> None:
    """Every lint in this repository runs in every lane its files can select."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def _listed_lints(stdout: str) -> set[str]:
    """The first token of every row in `--list`'s two inventory sections.

    Exact tokens, not substring containment: with containment a lint whose
    name is a prefix of another (`check_trace.py` beside
    `check_trace_payload_antipatterns.py`) reads as present while absent, and
    this is the one test asserting the inventory is complete.

    The trailing quick-lane section is deliberately excluded. It also prints
    lint basenames, so counting it would let a lint that appears *only* there
    satisfy the assertion -- while being missing from the inventory is exactly
    the condition under test.
    """
    inventory = {"laned", "unlaned"}
    section: str | None = None
    names: set[str] = set()
    for line in stdout.splitlines():
        if not line.strip():
            continue
        if not line.startswith("  "):
            section = line.split()[0].rstrip(":")
            continue
        if section in inventory:
            names.add(line.split()[0])
    return names


def test_listed_lints_matches_whole_tokens_not_substrings() -> None:
    """The completeness check must not be satisfied by a prefix collision."""
    stdout = (
        "laned (file-selecting hook -- full per-lane obligation):\n"
        "  check_trace_payload_antipatterns.py  hook=trace by=files needs=QUICK\n"
        "\nunlaned (no file-selecting hook -- must merely run somewhere):\n"
        "  check_mypy_gate.py  no pre-commit hook; ci jobs: test_light\n"
        "\nlints whose own tests never run in the QUICK lane:\n"
        "  check_orphan.py  test_orphan_lint.py\n"
    )
    listed = _listed_lints(stdout)
    assert listed == {"check_trace_payload_antipatterns.py", "check_mypy_gate.py"}
    # The prefix would pass a containment test; it must not pass this one.
    assert "check_trace.py" not in listed
    # Present only in the quick-lane section, so absent from the inventory.
    assert "check_orphan.py" not in listed


def test_live_listing_accounts_for_every_lint_on_disk() -> None:
    """The inventory is enumerated from disk, so nothing can be silently skipped."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--list"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    on_disk = {path.name for path in (REPO_ROOT / "tools").glob("check_*.py")}
    listed = _listed_lints(result.stdout)
    assert not on_disk - listed, f"lints absent from --list: {sorted(on_disk - listed)}"
    assert not listed - on_disk, f"--list names absent from disk: {sorted(listed - on_disk)}"
