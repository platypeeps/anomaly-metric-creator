"""Acceptance tests for ``tools/check_ci_review_contract.py``."""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "tools" / "check_ci_review_contract.py"


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
    )


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text).lstrip(), encoding="utf-8")


def _write_minimal_contract(root: Path, *, ci_extra: str = "") -> None:
    _write(
        root / ".github/workflows/ci.yml",
        f"""
        jobs:
          changes:
            outputs:
              full_ci_requested: ${{{{ steps['full-ci'].outputs.full_ci_requested }}}}
            steps:
              - run: bash scripts/classify_ci_changes.sh --github-output changed-files.txt
                id: full-ci
          lightweight_readiness:
            name: lightweight readiness
          quick_check:
            name: quick test
            steps:
              - run: pytest tests/test_ci_review_contract.py
          test_matrix:
            name: test (py3.12)
          test:
            name: test
            needs: [changes, lightweight_readiness, quick_check, test_matrix]
            steps:
              - run: |
                  echo full_ci_requested
                  echo full-ci
                  echo "selected lane: lightweight readiness"
                  echo "selected lane: quick test"
                  echo "selected lane: full matrix"
        {ci_extra}
        """,
    )
    _write(
        root / ".github/workflows/codeql.yml",
        """
        on:
          pull_request:
            types: [opened, synchronize, reopened, ready_for_review, labeled]
        concurrency:
          group: codeql-${{ github.ref }}
        jobs:
          analyze:
            if: github.event.action == 'synchronize' || github.event.label.name == 'full-ci'
        """,
    )
    _write(
        root / ".github/workflows/socket.yml",
        """
        on:
          pull_request:
            types: [opened, synchronize, reopened, ready_for_review, labeled]
        jobs:
          socket:
            steps:
              - run: bash scripts/classify_ci_changes.sh --github-output changed-files.txt
              - run: |
                  echo "No dependency/security-relevant changes"
                  if [ "$PR_LABEL" = "full-ci" ]; then true; fi
                  echo "$SOCKET_SECURITY_API_KEY"
        """,
    )
    _write(
        root / ".github/workflows/dependabot-auto-merge.yml",
        """
        on:
          pull_request_target:
        jobs:
          auto-merge:
            if: github.event.pull_request.user.login == 'dependabot[bot]'
            steps:
              - run: gh pr review --approve "$PR_URL"
              - run: gh pr merge --auto --squash "$PR_URL"
        """,
    )
    _write(
        root / "scripts/classify_ci_changes.sh",
        """
        emit_output "lightweight_only" "$lightweight_only"
        emit_output "app_required" "$app_required"
        emit_output "dependency_changed" "$dependency_changed"
        emit_output "workflow_changed" "$workflow_changed"
        emit_output "python_changed" "$python_changed"
        emit_output "review_tooling_changed" "$review_tooling_changed"
        git ls-files --others --exclude-standard
        scripts/trellis-full-check.sh
        """,
    )
    _write(
        root / "scripts/trellis-full-check.sh",
        """
        TRELLIS_FULL_CHECK_LEVEL=full
        tools/check_ci_review_contract.py
        run_classifier_smoke
        pytest tests/test_ci_review_contract.py
        pytest tests/test_server.py -k "apply or rollout"
        """,
    )
    for path in [
        root / "docs/DEVELOPMENT_CYCLE.md",
        root / "docs/TRELLIS_REVIEW_PR_PACK.md",
        root / ".trellis/spec/amc/backend/testing-quality.md",
    ]:
        _write(
            path,
            """
            check_ci_review_contract.py
            stable aggregate
            lightweight readiness
            quick test
            full-ci
            """,
        )


def test_real_repo_contract_is_clean() -> None:
    result = _run(str(REPO_ROOT))

    assert result.returncode == 0, result.stderr


def test_minimal_contract_fixture_passes(tmp_path: Path) -> None:
    _write_minimal_contract(tmp_path)

    result = _run(str(tmp_path))

    assert result.returncode == 0, result.stderr


def test_missing_ci_lane_fails(tmp_path: Path) -> None:
    _write_minimal_contract(tmp_path)
    ci = tmp_path / ".github/workflows/ci.yml"
    ci.write_text(
        ci.read_text(encoding="utf-8").replace("quick_check:", "quick_removed:"),
        encoding="utf-8",
    )

    result = _run(str(tmp_path))

    assert result.returncode == 1
    assert "quick lane" in result.stderr


def test_missing_codeql_synchronize_trigger_fails(tmp_path: Path) -> None:
    _write_minimal_contract(tmp_path)
    codeql = tmp_path / ".github/workflows/codeql.yml"
    codeql.write_text(
        codeql.read_text(encoding="utf-8").replace(
            "types: [opened, synchronize, reopened, ready_for_review, labeled]",
            "types: [opened, reopened, ready_for_review, labeled]",
        ).replace("github.event.action == 'synchronize' || ", ""),
        encoding="utf-8",
    )

    result = _run(str(tmp_path))

    assert result.returncode == 1
    assert "synchronize trigger" in result.stderr


def test_ci_full_ci_output_requires_bracket_expression(tmp_path: Path) -> None:
    _write_minimal_contract(tmp_path)
    ci = tmp_path / ".github/workflows/ci.yml"
    ci.write_text(
        ci.read_text(encoding="utf-8").replace(
            "steps['full-ci'].outputs.full_ci_requested",
            "steps.full-ci.outputs.full_ci_requested",
        ),
        encoding="utf-8",
    )

    result = _run(str(tmp_path))

    assert result.returncode == 1
    assert "full-ci output bracket expression" in result.stderr
    assert "full-ci output dot expression" in result.stderr


def test_missing_repo_file_exits_two(tmp_path: Path) -> None:
    _write_minimal_contract(tmp_path)
    (tmp_path / "scripts/classify_ci_changes.sh").unlink()

    result = _run(str(tmp_path))

    assert result.returncode == 2
    assert "cannot read" in result.stderr
