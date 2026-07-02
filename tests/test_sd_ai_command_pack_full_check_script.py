"""Focused tests for ``scripts/sd-ai-command-pack-full-check.sh`` review-tooling behavior."""

from __future__ import annotations

import os
import shutil
import subprocess
import textwrap
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "sd-ai-command-pack-full-check.sh"


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
    shutil.copy2(SCRIPT, repo / "scripts" / "sd-ai-command-pack-full-check.sh")
    _write(
        repo / "scripts" / "classify-ci-changes.sh",
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
    _write(repo / "scripts" / "check-review-preflight.mjs", "// fixture\n")
    _write(
        repo / "scripts" / "sd-ai-command-pack-review-scope.sh",
        "#!/usr/bin/env bash\nprintf scope-ran > scope-ran.txt\n",
        executable=True,
    )
    _write(repo / "marker.txt", "before\n")

    bin_dir = repo / "bin"
    _write(
        bin_dir / "node",
        """
        #!/usr/bin/env bash
        if [ "$1" = "-e" ]; then
          exit 0
        fi
        if [ "$1" = "scripts/check-review-preflight.mjs" ]; then
          printf preflight-ran > review-preflight-ran.txt
          exit 0
        fi
        exit 0
        """,
        executable=True,
    )
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
        if key.startswith("SD_AI_COMMAND_PACK_FULL_CHECK_PRISM"):
            env.pop(key)
    env.update(
        {
            "PATH": f"{repo / 'bin'}{os.pathsep}{env['PATH']}",
            "PRISM_COUNT_FILE": str(count_file),
            "PRISM_STATUSES": statuses,
            "SD_AI_COMMAND_PACK_FULL_CHECK_BASE_REF": "HEAD",
            "SD_AI_COMMAND_PACK_FULL_CHECK_SKIP_NPM": "1",
        }
    )
    if extra_env:
        env.update(extra_env)

    return subprocess.run(
        ["bash", "scripts/sd-ai-command-pack-full-check.sh"],
        cwd=repo,
        capture_output=True,
        env=env,
        text=True,
    )


def _prism_count(path: Path) -> int:
    if not path.exists():
        return 0
    return int(path.read_text(encoding="utf-8").strip())


def test_review_preflight_runs_when_present(tmp_path: Path) -> None:
    repo, count_file = _make_full_check_repo(tmp_path, prism_statuses="0")

    result = _run_full_check(
        repo,
        count_file,
        statuses="0",
        extra_env={"SD_AI_COMMAND_PACK_FULL_CHECK_PRISM": "0"},
    )

    assert result.returncode == 0, result.stderr
    assert (repo / "review-preflight-ran.txt").read_text(encoding="utf-8") == "preflight-ran"


def test_prism_provider_model_config_failure_is_optional(tmp_path: Path) -> None:
    repo, count_file = _make_full_check_repo(tmp_path, prism_statuses="4,0")

    result = _run_full_check(repo, count_file, statuses="4,0")

    assert result.returncode == 0, result.stderr
    assert _prism_count(count_file) == 1
    assert "Prism provider/model configuration failed" in result.stderr


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
    assert "Prism provider authentication/configuration failed" in result.stderr


def test_required_review_preflight_missing_exits_before_prism(tmp_path: Path) -> None:
    repo, count_file = _make_full_check_repo(tmp_path, prism_statuses="0")
    (repo / "scripts" / "check-review-preflight.mjs").unlink()

    result = _run_full_check(
        repo,
        count_file,
        statuses="0",
        extra_env={"SD_AI_COMMAND_PACK_FULL_CHECK_REVIEW_PREFLIGHT": "required"},
    )

    assert result.returncode == 127
    assert _prism_count(count_file) == 0
    assert "Review preflight is required" in result.stderr


def test_prism_fail_on_override_is_passed_to_prism(tmp_path: Path) -> None:
    repo, count_file = _make_full_check_repo(tmp_path, prism_statuses="0")
    args_file = repo / "prism-args.txt"

    result = _run_full_check(
        repo,
        count_file,
        statuses="0",
        extra_env={
            "PRISM_ARGS_FILE": str(args_file),
            "SD_AI_COMMAND_PACK_FULL_CHECK_PRISM_FAIL_ON": "medium",
        },
    )

    assert result.returncode == 0, result.stderr
    assert "--fail-on medium" in args_file.read_text(encoding="utf-8")


def test_prism_rules_file_is_passed_to_prism(tmp_path: Path) -> None:
    repo, count_file = _make_full_check_repo(tmp_path, prism_statuses="0")
    args_file = repo / "prism-args.txt"
    _write(repo / ".prism" / "rules.json", "{}\n")

    result = _run_full_check(
        repo,
        count_file,
        statuses="0",
        extra_env={
            "PRISM_ARGS_FILE": str(args_file),
        },
    )

    args = args_file.read_text(encoding="utf-8")
    assert result.returncode == 0, result.stderr
    assert "--rules .prism/rules.json" in args
