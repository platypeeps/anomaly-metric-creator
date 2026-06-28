"""Focused tests for ``scripts/trellis-full-check.sh`` review-tooling behavior."""

from __future__ import annotations

import os
import shutil
import subprocess
import textwrap
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "trellis-full-check.sh"


def _write(path: Path, text: str, *, executable: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text).lstrip(), encoding="utf-8")
    if executable:
        path.chmod(0o755)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def _make_full_check_repo(tmp_path: Path, *, prism_statuses: str) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    repo.mkdir()

    (repo / "scripts").mkdir()
    shutil.copy2(SCRIPT, repo / "scripts" / "trellis-full-check.sh")
    _write(
        repo / "scripts" / "classify_ci_changes.sh",
        """
        #!/usr/bin/env bash
        printf '%s\n' \
          'changed_count=1' \
          'lightweight_only=true' \
          'app_required=false' \
          'dependency_changed=false' \
          'workflow_changed=false' \
          'python_changed=false' \
          'review_tooling_changed=true'
        """,
        executable=True,
    )
    _write(repo / "scripts" / "trellis-housekeeping.sh", "#!/usr/bin/env bash\n:", executable=True)
    _write(repo / "marker.txt", "before\n")

    bin_dir = repo / "bin"
    _write(bin_dir / "python", "#!/usr/bin/env bash\nexit 0\n", executable=True)
    _write(bin_dir / "pytest", "#!/usr/bin/env bash\nexit 0\n", executable=True)
    _write(bin_dir / "ruff", "#!/usr/bin/env bash\nexit 0\n", executable=True)
    _write(
        bin_dir / "prism",
        """
        #!/usr/bin/env bash
        count_file="${PRISM_COUNT_FILE:?}"
        count=0
        if [ -f "$count_file" ]; then
          count="$(cat "$count_file")"
        fi
        count=$((count + 1))
        printf '%s\n' "$count" > "$count_file"
        if [ -n "${PRISM_ARGS_FILE:-}" ]; then
          printf '%s\n' "$*" >> "$PRISM_ARGS_FILE"
        fi

        IFS=',' read -r -a statuses <<< "${PRISM_STATUSES:-0}"
        index=$((count - 1))
        last_index=$((${#statuses[@]} - 1))
        if [ "$index" -gt "$last_index" ]; then
          index="$last_index"
        fi
        exit "${statuses[$index]}"
        """,
        executable=True,
    )

    _git(repo, "init", "-q")
    _git(repo, "add", ".")
    _git(repo, "-c", "user.name=AMC Test", "-c", "user.email=amc@example.test", "commit", "-qm", "initial")
    (repo / "marker.txt").write_text("after\n", encoding="utf-8")

    count_file = repo / "prism-count.txt"
    (repo / "prism-statuses.txt").write_text(prism_statuses, encoding="utf-8")
    return repo, count_file


def _run_full_check(
    repo: Path,
    count_file: Path,
    *,
    statuses: str,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    for key in list(env):
        if key.startswith("TRELLIS_FULL_CHECK_PRISM"):
            env.pop(key)
    env.update(
        {
            "PATH": f"{repo / 'bin'}{os.pathsep}{env['PATH']}",
            "PRISM_COUNT_FILE": str(count_file),
            "PRISM_STATUSES": statuses,
            "TRELLIS_FULL_CHECK_BASE_REF": "HEAD",
            "TRELLIS_FULL_CHECK_LEVEL": "quick",
            "TRELLIS_FULL_CHECK_PYTHON": str(repo / "bin" / "python"),
            "TRELLIS_FULL_CHECK_PYTEST": str(repo / "bin" / "pytest"),
            "TRELLIS_FULL_CHECK_RUFF": str(repo / "bin" / "ruff"),
        }
    )
    if extra_env:
        env.update(extra_env)

    return subprocess.run(
        ["bash", "scripts/trellis-full-check.sh"],
        cwd=repo,
        capture_output=True,
        env=env,
        text=True,
    )


def _prism_count(path: Path) -> int:
    if not path.exists():
        return 0
    return int(path.read_text(encoding="utf-8").strip())


def test_prism_unexpected_failure_retries_then_succeeds(tmp_path: Path) -> None:
    repo, count_file = _make_full_check_repo(tmp_path, prism_statuses="4,0")

    result = _run_full_check(repo, count_file, statuses="4,0")

    assert result.returncode == 0, result.stderr
    assert _prism_count(count_file) == 2
    assert "retrying because non-finding, non-authentication failures can be transient" in result.stderr
    assert "attempt 2 of 2" in result.stderr


def test_prism_findings_are_not_retried(tmp_path: Path) -> None:
    repo, count_file = _make_full_check_repo(tmp_path, prism_statuses="1,0")

    result = _run_full_check(repo, count_file, statuses="1,0")

    assert result.returncode == 1
    assert _prism_count(count_file) == 1
    assert "Prism found findings" in result.stderr


def test_optional_prism_auth_failure_is_not_retried(tmp_path: Path) -> None:
    repo, count_file = _make_full_check_repo(tmp_path, prism_statuses="3,0")

    result = _run_full_check(repo, count_file, statuses="3,0")

    assert result.returncode == 0, result.stderr
    assert _prism_count(count_file) == 1
    assert "Prism authentication/configuration failed" in result.stderr


def test_invalid_prism_retry_count_exits_two_before_running_prism(tmp_path: Path) -> None:
    repo, count_file = _make_full_check_repo(tmp_path, prism_statuses="0")

    result = _run_full_check(
        repo,
        count_file,
        statuses="0",
        extra_env={"TRELLIS_FULL_CHECK_PRISM_RETRIES": "sometimes"},
    )

    assert result.returncode == 2
    assert _prism_count(count_file) == 0
    assert "TRELLIS_FULL_CHECK_PRISM_RETRIES must be a non-negative integer" in result.stderr


def test_prism_compare_override_is_passed_to_prism(tmp_path: Path) -> None:
    repo, count_file = _make_full_check_repo(tmp_path, prism_statuses="0")
    args_file = repo / "prism-args.txt"

    result = _run_full_check(
        repo,
        count_file,
        statuses="0",
        extra_env={
            "PRISM_ARGS_FILE": str(args_file),
            "TRELLIS_FULL_CHECK_PRISM_COMPARE": "openai:gpt-5.2",
        },
    )

    assert result.returncode == 0, result.stderr
    assert "--compare openai:gpt-5.2" in args_file.read_text(encoding="utf-8")


def test_prism_provider_and_model_overrides_are_passed_to_prism(tmp_path: Path) -> None:
    repo, count_file = _make_full_check_repo(tmp_path, prism_statuses="0")
    args_file = repo / "prism-args.txt"

    result = _run_full_check(
        repo,
        count_file,
        statuses="0",
        extra_env={
            "PRISM_ARGS_FILE": str(args_file),
            "TRELLIS_FULL_CHECK_PRISM_PROVIDER": "openai",
            "TRELLIS_FULL_CHECK_PRISM_MODEL": "gpt-5.2",
        },
    )

    args = args_file.read_text(encoding="utf-8")
    assert result.returncode == 0, result.stderr
    assert "--provider openai" in args
    assert "--model gpt-5.2" in args
