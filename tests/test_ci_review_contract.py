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
        on:
          pull_request:
            types: [opened, synchronize, reopened, ready_for_review, labeled, auto_merge_enabled]
        concurrency:
          group: ci-${{{{ github.event_name == 'push' && github.sha || github.ref }}}}
          cancel-in-progress: true
        jobs:
          changes:
            outputs:
              full_ci_requested: ${{{{ steps['full-ci'].outputs.full_ci_requested }}}}
            steps:
              - id: classify
                env:
                  EVENT_NAME: ${{{{ github.event_name }}}}
                run: |
                  classifier_args=(--github-output)
                  if [ "$EVENT_NAME" = "workflow_dispatch" ]; then
                    classifier_args+=(--force-app)
                  fi
                  bash scripts/classify-ci-changes.sh "${{classifier_args[@]}}" changed-files.txt
              - id: full-ci
                env:
                  PR_AUTO_MERGE: ${{{{ github.event.pull_request.auto_merge != null }}}}
                run: |
                  case "$PR_ACTION" in
                    labeled)
                      if [ "$PR_LABEL" = "full-ci" ] || [ "$PR_AUTO_MERGE" = "true" ]; then
                        full_ci_requested=true
                      fi
                      ;;
                    synchronize)
                      if [ "$PR_AUTO_MERGE" = "true" ]; then
                        full_ci_requested=true
                      fi
                      ;;
                    auto_merge_enabled)
                      full_ci_requested=true
                      ;;
                  esac
          lightweight_readiness:
            name: lightweight readiness
            steps:
              - name: Set up uv for lightweight guards
                uses: astral-sh/setup-uv@11f9893b081a58869d3b5fccaea48c9e9e46f990
              - run: git diff --check "origin/$BASE_REF...HEAD"
              - run: bash -n scripts/classify-ci-changes.sh scripts/classify_ci_changes.sh scripts/sd-ai-command-pack-full-check.sh scripts/sd-ai-command-pack-housekeeping.sh scripts/sd-ai-command-pack-review-scope.sh scripts/sd-ai-command-pack-review-local.sh scripts/sd-ai-command-pack-shell-lib.sh scripts/sd-ai-command-pack-toolchain.sh
              - run: git ls-files 'scripts/*.py' 'tools/*.py' 'tests/*.py' '.codex/hooks/*.py' '.github/copilot/hooks/*.py' '.gemini/hooks/*.py'
              - run: uv run --python 3.14 --no-project python tools/check_python_syntax.py
              - run: uv run --python 3.14 --no-project python tools/check_workflow_pip.py
              - run: uv run --python 3.14 --no-project python tools/check_trellis_placeholders.py
              - run: uv run --python 3.14 --no-project python tools/check_ci_review_contract.py
              - run: uv run --python 3.14 --no-project python tools/check_copilot_instruction_contract.py
              - run: uv run --python 3.14 --no-project python scripts/sd-ai-command-pack-pr-body-scope.py
          quick_check:
            name: quick test
            steps:
              - run: uv sync --extra dev --locked --python 3.14
              - run: pytest tests/test_ci_review_contract.py
              - run: pytest tests/test_copilot_instruction_contract.py
              - run: pytest tests/test_pr_body_scope_lint.py
              - run: python tools/check_copilot_instruction_contract.py
              - run: python scripts/sd-ai-command-pack-pr-body-scope.py
          test_matrix:
            name: test (py3.12)
          test:
            name: test
            needs: [changes, lightweight_readiness, quick_check, test_matrix]
            if: ${{{{ !cancelled() }}}}
            steps:
              - run: |
                  echo full_ci_requested
                  echo full-ci
                  echo "selected lane: lightweight readiness"
                  echo "selected lane: quick test"
                  echo "selected lane: full matrix"
          socket:
            name: socket
            needs: changes
            if: ${{{{ !cancelled() && github.event_name == 'pull_request' }}}}
            steps:
              - run: |
                  echo "No dependency/security-relevant changes"
                  if [ "$PR_LABEL" = "full-ci" ]; then true; fi
                  echo "$SOCKET_SECURITY_API_KEY"
          ci_result:
            name: CI Result
            needs: [test, socket]
            if: ${{{{ !cancelled() }}}}
            steps:
              - run: echo "CI Result passed."
                env:
                  APP_RESULT: ${{{{ needs.test.result }}}}
                  SOCKET_RESULT: ${{{{ needs.socket.result }}}}
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
            if: (github.event.action == 'synchronize' && contains(github.event.pull_request.labels.*.name, 'full-ci')) || github.event.label.name == 'full-ci'
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
              - run: gh pr merge --auto --squash "$PR_URL"
        """,
    )
    _write(
        root / ".pre-commit-config.yaml",
        r"""
        repos:
          - repo: local
            hooks:
              - id: python-syntax
                entry: python tools/check_python_syntax.py
                files: ^(scripts|src|tests|tools|\.codex/hooks|\.github/copilot/hooks|\.gemini/hooks)/.*\.py$
              - id: review-tooling-shell-syntax
                entry: bash -n scripts/classify-ci-changes.sh scripts/classify_ci_changes.sh scripts/sd-ai-command-pack-full-check.sh scripts/sd-ai-command-pack-housekeeping.sh scripts/sd-ai-command-pack-review-scope.sh scripts/sd-ai-command-pack-review-local.sh scripts/sd-ai-command-pack-shell-lib.sh scripts/sd-ai-command-pack-toolchain.sh
              - id: ci-review-contract
                entry: python tools/check_ci_review_contract.py
                files: ^scripts/sd-ai-command-pack-pr-body-scope\.py|\.sd-ai-command-pack/pr-body-scope\.json|tests/test_pr_body_scope_lint\.py$
                pass_filenames: false
              - id: copilot-instruction-contract
                entry: python tools/check_copilot_instruction_contract.py
                pass_filenames: false
        """,
    )
    _write(
        root / "scripts/classify-ci-changes.sh",
        """
        emit_output "lightweight_only" "$lightweight_only"
        emit_output "app_required" "$app_required"
        emit_output "dependency_changed" "$dependency_changed"
        emit_output "workflow_changed" "$workflow_changed"
        emit_output "python_changed" "$python_changed"
        emit_output "review_tooling_changed" "$review_tooling_changed"
        git ls-files --others --exclude-standard
        scripts/sd-ai-command-pack-review-preflight.mjs
        scripts/check-review-preflight.mjs
        scripts/sd-ai-command-pack-pr-body-scope.py
        scripts/sd-ai-command-pack-review-scope.sh
        scripts/sd-ai-command-pack-review-local.sh
        scripts/sd-ai-command-pack-install-audit.py
        scripts/sd-ai-command-pack-full-check.sh
        scripts/sd-ai-command-pack-housekeeping.sh
        scripts/sd-ai-command-pack-shell-lib.sh
        scripts/sd-ai-command-pack-toolchain.sh
        .sd-ai-command-pack/*
        .trellis/audit/*
        .sd-ai-command-pack/pr-body-scope.json
        tests/test_pr_body_scope_lint.py
        """,
    )
    _write(
        root / "scripts/sd-ai-command-pack-full-check.sh",
        """
        run_review_preflight
        scripts/sd-ai-command-pack-review-preflight.mjs
        scripts/check-review-preflight.mjs
        run_sd_ai_command_pack_install_audit
        scripts/sd-ai-command-pack-install-audit.py
        run_sd_ai_command_pack_scope_check
        scripts/sd-ai-command-pack-review-scope.sh
        run_sd_ai_command_pack_pr_body_scope_check
        scripts/sd-ai-command-pack-pr-body-scope.py
        run_ci_classification_report
        SD_AI_COMMAND_PACK_FULL_CHECK_PACKAGE_SCRIPTS
        SD_AI_COMMAND_PACK_FULL_CHECK_PRISM_FAIL_ON
        SD_AI_COMMAND_PACK_FULL_CHECK_PRISM_MAX_FINDINGS
        SD_AI_COMMAND_PACK_FULL_CHECK_PRISM_RULES
        SD_AI_COMMAND_PACK_FULL_CHECK_GITO
        """,
    )
    _write(
        root / "docs/DEVELOPMENT_CYCLE.md",
        """
        check_ci_review_contract.py
        sd-ai-command-pack-pr-body-scope.py
        stable aggregate
        lightweight readiness
        quick test
        full-ci
        """,
    )
    _write(
        root / "docs/SD_AI_COMMAND_PACK.md",
        """
        scripts/sd-ai-command-pack-review-preflight.mjs
        scripts/check-review-preflight.mjs
        scripts/sd-ai-command-pack-review-scope.sh
        scripts/sd-ai-command-pack-install-audit.py
        .sd-ai-command-pack/installed-targets.txt
        Tooling/generated scope:
        SD_AI_COMMAND_PACK_FULL_CHECK_REVIEW_PREFLIGHT
        scripts/sd-ai-command-pack-pr-body-scope.py
        .sd-ai-command-pack/pr-body-scope.json
        """,
    )
    _write(
        root / ".trellis/spec/amc/backend/testing-quality.md",
        """
        check_ci_review_contract.py
        sd-ai-command-pack-pr-body-scope.py
        stable aggregate
        lightweight readiness
        quick test
        full-ci
        """,
    )
    _write(
        root / ".github/instructions/anomaly-metric-creator.instructions.md",
        """
        CI Result
        py3.14 test lane
        Python 3.14 is the only CI-tested version
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


def test_reverting_aggregate_guard_to_always_fails(tmp_path: Path) -> None:
    # Reverting the aggregate `test` job's guard from !cancelled() to
    # always() reintroduces the churn-task symptom-1 transient FAILURE; the
    # contract must catch that regression.
    _write_minimal_contract(tmp_path)
    ci = tmp_path / ".github/workflows/ci.yml"
    ci.write_text(
        ci.read_text(encoding="utf-8").replace(
            "if: ${{ !cancelled() }}", "if: ${{ always() }}"
        ),
        encoding="utf-8",
    )

    result = _run(str(tmp_path))

    assert result.returncode == 1
    assert "cancellation-safe" in result.stderr


def test_missing_stable_ci_result_fails(tmp_path: Path) -> None:
    _write_minimal_contract(tmp_path)
    ci = tmp_path / ".github/workflows/ci.yml"
    ci.write_text(
        ci.read_text(encoding="utf-8").replace("name: CI Result", "name: CI Summary"),
        encoding="utf-8",
    )

    result = _run(str(tmp_path))

    assert result.returncode == 1
    assert "stable aggregate name" in result.stderr


def test_ci_result_must_include_socket(tmp_path: Path) -> None:
    _write_minimal_contract(tmp_path)
    ci = tmp_path / ".github/workflows/ci.yml"
    ci.write_text(
        ci.read_text(encoding="utf-8").replace(
            "needs: [test, socket]", "needs: [test]"
        ),
        encoding="utf-8",
    )

    result = _run(str(tmp_path))

    assert result.returncode == 1
    assert "stable aggregate dependencies" in result.stderr


def test_stale_copilot_ci_context_guidance_fails(tmp_path: Path) -> None:
    _write_minimal_contract(tmp_path)
    guidance = (
        tmp_path / ".github/instructions/anomaly-metric-creator.instructions.md"
    )
    guidance.write_text(
        guidance.read_text(encoding="utf-8")
        + "`test` and `socket` are the required checks\n",
        encoding="utf-8",
    )

    result = _run(str(tmp_path))

    assert result.returncode == 1
    assert "legacy required contexts" in result.stderr


def test_removing_locked_sync_flag_fails(tmp_path: Path) -> None:
    # Dropping --locked from `uv sync` lets the runner silently re-resolve
    # when pyproject and uv.lock drift, voiding the committed-lock contract
    # (07-06-uv-locked-ci-enforcement); the anchor must catch that.
    _write_minimal_contract(tmp_path)
    ci = tmp_path / ".github/workflows/ci.yml"
    ci.write_text(
        ci.read_text(encoding="utf-8").replace(
            "uv sync --extra dev --locked", "uv sync --extra dev"
        ),
        encoding="utf-8",
    )

    result = _run(str(tmp_path))

    assert result.returncode == 1
    assert "locked dependency sync" in result.stderr


def test_missing_codeql_synchronize_trigger_fails(tmp_path: Path) -> None:
    _write_minimal_contract(tmp_path)
    codeql = tmp_path / ".github/workflows/codeql.yml"
    codeql.write_text(
        codeql.read_text(encoding="utf-8").replace(
            "types: [opened, synchronize, reopened, ready_for_review, labeled]",
            "types: [opened, reopened, ready_for_review, labeled]",
        ).replace(
            "(github.event.action == 'synchronize' && "
            "contains(github.event.pull_request.labels.*.name, 'full-ci')) || ",
            "",
        ),
        encoding="utf-8",
    )

    result = _run(str(tmp_path))

    assert result.returncode == 1
    assert "synchronize trigger" in result.stderr


def test_missing_codeql_persistent_full_ci_recheck_fails(tmp_path: Path) -> None:
    # Dropping the persistent contains(...labels...) re-check (leaving only a
    # plain synchronize) is the "unify CodeQL to one-shot" regression the
    # anchor guards against — it would silently cut security-scan coverage on
    # flagged PRs. The plain-synchronize replacement keeps the synchronize
    # trigger anchor satisfied so only the persistence anchor fires.
    _write_minimal_contract(tmp_path)
    codeql = tmp_path / ".github/workflows/codeql.yml"
    codeql.write_text(
        codeql.read_text(encoding="utf-8").replace(
            "(github.event.action == 'synchronize' && "
            "contains(github.event.pull_request.labels.*.name, 'full-ci'))",
            "github.event.action == 'synchronize'",
        ),
        encoding="utf-8",
    )

    result = _run(str(tmp_path))

    assert result.returncode == 1
    assert "persistent full-ci re-check on synchronize" in result.stderr


def test_ci_persistent_full_ci_recheck_is_forbidden(tmp_path: Path) -> None:
    # The inverse: ci.yml must NOT gain codeql's persistent label re-check.
    # Making the cost-gated full matrix persistent-on-label is a cadence change
    # that must update the contract, not slip in silently.
    _write_minimal_contract(
        tmp_path,
        ci_extra=(
            "# contains(github.event.pull_request.labels.*.name, 'full-ci')"
        ),
    )

    result = _run(str(tmp_path))

    assert result.returncode == 1
    assert "persistent full-ci re-check (belongs only in codeql.yml)" in result.stderr


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


def test_missing_auto_merge_synchronize_gate_fails(tmp_path: Path) -> None:
    _write_minimal_contract(tmp_path)
    ci = tmp_path / ".github/workflows/ci.yml"
    ci.write_text(
        ci.read_text(encoding="utf-8").replace(
            "github.event.pull_request.auto_merge != null",
            "false",
        ),
        encoding="utf-8",
    )

    result = _run(str(tmp_path))

    assert result.returncode == 1
    assert "auto-merge synchronize gate" in result.stderr


def test_missing_auto_merge_labeled_gate_fails(tmp_path: Path) -> None:
    _write_minimal_contract(tmp_path)
    ci = tmp_path / ".github/workflows/ci.yml"
    ci.write_text(
        ci.read_text(encoding="utf-8").replace(
            ' || [ "$PR_AUTO_MERGE" = "true" ]',
            "",
            1,
        ),
        encoding="utf-8",
    )

    result = _run(str(tmp_path))

    assert result.returncode == 1
    assert "auto-merge labeled full-ci request" in result.stderr


def test_manual_dispatch_must_force_classifier_app_gate(tmp_path: Path) -> None:
    _write_minimal_contract(tmp_path)
    ci = tmp_path / ".github/workflows/ci.yml"
    ci.write_text(
        ci.read_text(encoding="utf-8").replace(
            "classifier_args+=(--force-app)",
            ": # force-app removed",
        ),
        encoding="utf-8",
    )

    result = _run(str(tmp_path))

    assert result.returncode == 1
    assert "manual dispatch classifier force-app" in result.stderr


def test_lightweight_guards_require_pinned_python(tmp_path: Path) -> None:
    guards = (
        "tools/check_python_syntax.py",
        "tools/check_workflow_pip.py",
        "tools/check_trellis_placeholders.py",
        "tools/check_ci_review_contract.py",
        "tools/check_copilot_instruction_contract.py",
        "scripts/sd-ai-command-pack-pr-body-scope.py",
    )
    for index, guard in enumerate(guards):
        root = tmp_path / str(index)
        _write_minimal_contract(root)
        ci = root / ".github/workflows/ci.yml"
        ci.write_text(
            ci.read_text(encoding="utf-8").replace(
                f"uv run --python 3.14 --no-project python {guard}",
                f"python {guard}",
            ),
            encoding="utf-8",
        )

        result = _run(str(root))

        assert result.returncode == 1
        assert f"pinned Python lightweight guard ({guard})" in result.stderr


def test_ci_shell_syntax_must_cover_command_pack_entrypoints(tmp_path: Path) -> None:
    _write_minimal_contract(tmp_path)
    ci = tmp_path / ".github/workflows/ci.yml"
    ci.write_text(
        ci.read_text(encoding="utf-8").replace(
            " scripts/sd-ai-command-pack-toolchain.sh",
            "",
            1,
        ),
        encoding="utf-8",
    )

    result = _run(str(tmp_path))

    assert result.returncode == 1
    assert "CI review-tooling shell syntax coverage" in result.stderr


def test_missing_auto_merge_enabled_trigger_fails(tmp_path: Path) -> None:
    _write_minimal_contract(tmp_path)
    ci = tmp_path / ".github/workflows/ci.yml"
    ci.write_text(
        ci.read_text(encoding="utf-8")
        .replace(
            "types: [opened, synchronize, reopened, ready_for_review, labeled, auto_merge_enabled]",
            "types: [opened, synchronize, reopened, ready_for_review, labeled]",
        )
        .replace("auto_merge_enabled)", "never_enabled)"),
        encoding="utf-8",
    )

    result = _run(str(tmp_path))

    assert result.returncode == 1
    assert "auto-merge enabled PR event" in result.stderr
    assert "auto-merge enabled full-ci request" in result.stderr


def test_auto_merge_full_ci_assignment_removal_fails(tmp_path: Path) -> None:
    _write_minimal_contract(tmp_path)
    ci = tmp_path / ".github/workflows/ci.yml"
    ci.write_text(
        ci.read_text(encoding="utf-8").replace(
            "full_ci_requested=true",
            "full_ci_requested=false",
        ),
        encoding="utf-8",
    )

    result = _run(str(tmp_path))

    assert result.returncode == 1
    assert "auto-merge synchronize full-ci request" in result.stderr
    assert "auto-merge enabled full-ci request" in result.stderr


def test_per_ref_push_concurrency_fails(tmp_path: Path) -> None:
    _write_minimal_contract(tmp_path)
    ci = tmp_path / ".github/workflows/ci.yml"
    ci.write_text(
        ci.read_text(encoding="utf-8").replace(
            "group: ci-${{ github.event_name == 'push' && github.sha || github.ref }}",
            "group: ci-${{ github.ref }}",
        ),
        encoding="utf-8",
    )

    result = _run(str(tmp_path))

    assert result.returncode == 1
    assert "per-commit push concurrency" in result.stderr


def test_dependabot_auto_merge_forbids_actions_pr_approval(tmp_path: Path) -> None:
    _write_minimal_contract(tmp_path)
    workflow = tmp_path / ".github/workflows/dependabot-auto-merge.yml"
    workflow.write_text(
        workflow.read_text(encoding="utf-8").replace(
            '- run: gh pr merge --auto --squash "$PR_URL"',
            '- run: gh pr review --approve "$PR_URL"\n'
            '              - run: gh pr merge --auto --squash "$PR_URL"',
        ),
        encoding="utf-8",
    )

    result = _run(str(tmp_path))

    assert result.returncode == 1
    assert "GitHub Actions PR approval" in result.stderr


def test_lightweight_whitespace_requires_pr_diff_range(tmp_path: Path) -> None:
    _write_minimal_contract(tmp_path)
    ci = tmp_path / ".github/workflows/ci.yml"
    ci.write_text(
        ci.read_text(encoding="utf-8").replace(
            'git diff --check "origin/$BASE_REF...HEAD"',
            "git diff --check HEAD^ HEAD",
        ),
        encoding="utf-8",
    )

    result = _run(str(tmp_path))

    assert result.returncode == 1
    assert "lightweight whitespace PR diff" in result.stderr


def test_full_check_runs_review_preflight(tmp_path: Path) -> None:
    _write_minimal_contract(tmp_path)
    full_check = tmp_path / "scripts/sd-ai-command-pack-full-check.sh"
    full_check.write_text(
        full_check.read_text(encoding="utf-8").replace(
            "run_review_preflight",
            "run_review_notes",
        ),
        encoding="utf-8",
    )

    result = _run(str(tmp_path))

    assert result.returncode == 1
    assert "review preflight runner" in result.stderr


def test_missing_repo_file_exits_two(tmp_path: Path) -> None:
    _write_minimal_contract(tmp_path)
    (tmp_path / "scripts/classify-ci-changes.sh").unlink()

    result = _run(str(tmp_path))

    assert result.returncode == 2
    assert "cannot read" in result.stderr
