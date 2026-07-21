"""Acceptance tests for ``tools/check_ci_review_contract.py``."""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

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
              - env:
                  HEAD_REF: ${{{{ github.head_ref }}}}
                run: uv run --python 3.14 --no-project python tools/check_branch_name.py "$HEAD_REF"
              - run: |
                  git ls-files src scripts .agents .trellis
                  uv run --python 3.14 --no-project python tools/check_amc_module_load.py
                  uv run --python 3.14 --no-project python tools/check_role_name_leaks.py
                  uv run --python 3.14 --no-project python tools/check_agent_hook_exceptions.py
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
          test_heavy:
            name: test heavy (py3.14)
            needs: changes
            if: needs.changes.outputs.app_required == 'true' && needs.changes.outputs.full_ci_requested == 'true'
            steps:
              - run: uv sync --extra dev --locked --python 3.14
              - run: uv run --no-sync pytest -n 2 --dist loadfile -m heavy --cov=src/anomaly_metric_creator --cov-report=
              - run: mv .coverage coverage-heavy
              - uses: actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a
                with:
                  name: coverage-data-heavy
                  path: coverage-heavy
          test_light:
            name: test light (py3.14)
            needs: changes
            if: needs.changes.outputs.app_required == 'true' && needs.changes.outputs.full_ci_requested == 'true'
            steps:
              - run: uv sync --extra dev --locked --python 3.14
              - env:
                  KUBECTL_VERSION: v1.36.2
                  KUBECTL_SHA256: 1e9045ec32bea85da43de85f0065358529ea7c7a152eca78154fba5b58c27d82
                  HELM_VERSION: v4.2.0
                  HELM_SHA256: 97dbeb971be4ac4b27e3839976d9564c0fb35c6f3b1da89dd1e292d236af4096
                run: |
                  set -euo pipefail
                  curl --fail --location --show-error --silent --retry 3 --retry-all-errors https://dl.k8s.io/release/${{KUBECTL_VERSION}}/bin/linux/amd64/kubectl
                  printf '%s  %s\\n' "$KUBECTL_SHA256" "$client_bin/kubectl" | sha256sum --check --strict
                  curl --fail --location --show-error --silent --retry 3 --retry-all-errors https://get.helm.sh/helm-${{HELM_VERSION}}-linux-amd64.tar.gz
                  printf '%s  %s\\n' "$HELM_SHA256" "$client_root/helm.tar.gz" | sha256sum --check --strict
                  echo "$client_bin" >> "$GITHUB_PATH"
              - env:
                  AMC_RUN_REAL_CLIENT_SMOKE: "1"
                run: |
                  uv run --no-sync pytest -n 0 -q \
                    tests/test_server.py::test_real_helm4_binary_smoke_when_available \
                    tests/test_server.py::test_real_kubectl_binary_smoke_when_available
              - run: uv run --no-sync python tools/check_mypy_gate.py
              - run: uv run --no-sync pytest -n 2 --dist loadfile -m "not heavy" --cov=src/anomaly_metric_creator --cov-report=
              - run: mv .coverage coverage-light
              - uses: actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a
                with:
                  name: coverage-data-light
                  path: coverage-light
          coverage_combine:
            name: coverage (py3.14)
            needs: [test_heavy, test_light]
            steps:
              - uses: actions/checkout@9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0
              - uses: astral-sh/setup-uv@11f9893b081a58869d3b5fccaea48c9e9e46f990
              - run: uv sync --extra dev --locked --python 3.14
              - uses: actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c
                with:
                  name: coverage-data-heavy
                  path: coverage-data
              - uses: actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c
                with:
                  name: coverage-data-light
                  path: coverage-data
              - name: Combine coverage data
                run: |
                  mv coverage-data/coverage-heavy .coverage.heavy
                  mv coverage-data/coverage-light .coverage.light
                  uv run --no-sync coverage combine
              - name: Generate coverage XML
                run: uv run --no-sync coverage xml
              - name: Enforce coverage threshold
                run: uv run --no-sync coverage report --fail-under=85
              - name: Upload coverage report
                if: ${{{{ !cancelled() }}}}
                uses: actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a
                with:
                  name: coverage-xml-py3.14
          windows_collection:
            name: Windows collection (advisory)
            runs-on: windows-latest
            continue-on-error: true
            steps:
              - run: uv sync --extra dev --locked --python 3.14
              - run: uv run --no-sync pytest --collect-only -q
          test:
            name: test
            needs: [changes, lightweight_readiness, quick_check, test_heavy, test_light, coverage_combine]
            if: ${{{{ !cancelled() }}}}
            steps:
              - run: |
                  echo full_ci_requested
                  echo full-ci
                  echo "selected lane: lightweight readiness"
                  echo "selected lane: quick test"
                  echo "selected lane: full test lanes"
                  test "$HEAVY_RESULT" = "success"
                  test "$LIGHT_RESULT" = "success"
                  test "$COVERAGE_RESULT" = "success"
                env:
                  HEAVY_RESULT: ${{{{ needs.test_heavy.result }}}}
                  LIGHT_RESULT: ${{{{ needs.test_light.result }}}}
                  COVERAGE_RESULT: ${{{{ needs.coverage_combine.result }}}}
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
            steps:
              - name: Initialize CodeQL
                uses: github/codeql-action/init@1111111111111111111111111111111111111111
              - name: Perform CodeQL Analysis
                uses: github/codeql-action/analyze@1111111111111111111111111111111111111111
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
        root / ".github/workflows/sd-ai-command-pack-sync.yml",
        """
        on:
          schedule:
            - cron: '17 9 * * 1'
          workflow_dispatch:
        permissions:
          contents: read
        jobs:
          sync:
            steps:
              - uses: actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97
                with:
                  python-version: "3.14"
              - run: git clone --depth 1 --branch main https://github.com/platypeeps/sd-ai-command-pack.git "$RUNNER_TEMP/sd-ai-command-pack"
              - run: python "$RUNNER_TEMP/sd-ai-command-pack/install.py" "$GITHUB_WORKSPACE" --force
              - run: scripts/update_repomix
              - env:
                  SCOPED_TOKEN: ${{ secrets.SD_AI_COMMAND_PACK_PR_TOKEN }}
                run: |
                  if [ -z "$SCOPED_TOKEN" ]; then
                    echo "SD_AI_COMMAND_PACK_PR_TOKEN is not configured"
                    exit 1
                  fi
              - id: create-pr
                uses: peter-evans/create-pull-request@5f6978faf089d4d20b00c7766989d076bb2fc7f1
                with:
                  token: ${{ secrets.SD_AI_COMMAND_PACK_PR_TOKEN }}
                  branch: automation/sd-ai-command-pack-sync
                  delete-branch: true
              - if: steps.create-pr.outputs.pull-request-number != ''
                run: gh pr merge --auto --squash "$PR_URL"
                env:
                  GH_TOKEN: ${{ secrets.SD_AI_COMMAND_PACK_PR_TOKEN }}
        """,
    )
    _write(
        root / "pyproject.toml",
        """
        [tool.coverage.run]
        relative_files = true
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
              - id: role-name-commit-message
                entry: python tools/check_role_name_leaks.py
                stages: [commit-msg]
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
        sd-ai-command-pack-sync.yml
        windows-latest
        pytest --collect-only -q
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
        sd-ai-command-pack-sync.yml
        windows-latest
        pytest --collect-only -q
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


@pytest.mark.parametrize(
    ("job", "expected"),
    [
        ("test_heavy", "heavy full-test lane"),
        ("test_light", "light full-test lane"),
        ("coverage_combine", "coverage combine lane"),
    ],
)
def test_parallel_full_test_jobs_are_required(
    tmp_path: Path, job: str, expected: str
) -> None:
    _write_minimal_contract(tmp_path)
    ci = tmp_path / ".github/workflows/ci.yml"
    ci.write_text(
        ci.read_text(encoding="utf-8").replace(f"  {job}:", "  removed_job:"),
        encoding="utf-8",
    )

    result = _run(str(tmp_path))

    assert result.returncode == 1
    assert expected in result.stderr


@pytest.mark.parametrize(
    ("needle", "expected"),
    [
        ("set -euo pipefail", "fail-closed real-client installer"),
        ("KUBECTL_VERSION: v1.36.2", "pinned kubectl version"),
        (
            "KUBECTL_SHA256: 1e9045ec32bea85da43de85f0065358529ea7c7a152eca78154fba5b58c27d82",
            "pinned kubectl checksum",
        ),
        (
            "printf '%s  %s\\n' \"$KUBECTL_SHA256\" \"$client_bin/kubectl\"",
            "kubectl checksum wiring",
        ),
        (
            "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/"
            "linux/amd64/kubectl",
            "official kubectl download",
        ),
        ("HELM_VERSION: v4.2.0", "pinned Helm version"),
        (
            "HELM_SHA256: 97dbeb971be4ac4b27e3839976d9564c0fb35c6f3b1da89dd1e292d236af4096",
            "pinned Helm checksum",
        ),
        (
            "printf '%s  %s\\n' \"$HELM_SHA256\" \"$client_root/helm.tar.gz\"",
            "Helm checksum wiring",
        ),
        (
            "https://get.helm.sh/helm-${HELM_VERSION}-linux-amd64.tar.gz",
            "official Helm download",
        ),
        ("--retry 3 --retry-all-errors", "real-client download retry"),
        ('echo "$client_bin" >> "$GITHUB_PATH"', "real-client PATH export"),
        ('AMC_RUN_REAL_CLIENT_SMOKE: "1"', "real-client smoke opt-in"),
        ("pytest -n 0 -q", "serial real-client smoke"),
        (
            "tests/test_server.py::test_real_helm4_binary_smoke_when_available",
            "Helm real-client smoke selector",
        ),
        (
            "tests/test_server.py::test_real_kubectl_binary_smoke_when_available",
            "kubectl real-client smoke selector",
        ),
    ],
)
def test_real_client_smoke_contract_is_required(
    tmp_path: Path, needle: str, expected: str
) -> None:
    _write_minimal_contract(tmp_path)
    ci = tmp_path / ".github/workflows/ci.yml"
    ci.write_text(
        ci.read_text(encoding="utf-8").replace(needle, "REMOVED_REAL_CLIENT_ANCHOR"),
        encoding="utf-8",
    )

    result = _run(str(tmp_path))

    assert result.returncode == 1
    assert expected in result.stderr


def test_both_real_client_downloads_require_checksum_verification(
    tmp_path: Path,
) -> None:
    _write_minimal_contract(tmp_path)
    ci = tmp_path / ".github/workflows/ci.yml"
    text = ci.read_text(encoding="utf-8")
    ci.write_text(
        text.replace("sha256sum --check --strict", "sha256sum --check", 1),
        encoding="utf-8",
    )

    result = _run(str(tmp_path))

    assert result.returncode == 1
    assert "exactly two real-client checksum checks" in result.stderr


def test_both_real_client_downloads_fail_closed(tmp_path: Path) -> None:
    _write_minimal_contract(tmp_path)
    ci = tmp_path / ".github/workflows/ci.yml"
    text = ci.read_text(encoding="utf-8")
    ci.write_text(
        text.replace(
            "curl --fail --location --show-error --silent",
            "curl --location --show-error --silent",
            1,
        ),
        encoding="utf-8",
    )

    result = _run(str(tmp_path))

    assert result.returncode == 1
    assert "exactly two fail-closed real-client downloads" in result.stderr


def test_coverage_xml_must_precede_threshold_gate(tmp_path: Path) -> None:
    _write_minimal_contract(tmp_path)
    ci = tmp_path / ".github/workflows/ci.yml"
    text = ci.read_text(encoding="utf-8")
    xml_step = """- name: Generate coverage XML
        run: uv run --no-sync coverage xml"""
    threshold_step = """- name: Enforce coverage threshold
        run: uv run --no-sync coverage report --fail-under=85"""
    ci.write_text(
        text.replace(
            f"{xml_step}\n      {threshold_step}",
            f"{threshold_step}\n      {xml_step}",
        ),
        encoding="utf-8",
    )

    result = _run(str(tmp_path))

    assert result.returncode == 1
    assert "diagnostic-preserving order" in result.stderr


def test_coverage_data_must_use_relative_paths(tmp_path: Path) -> None:
    _write_minimal_contract(tmp_path)
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        pyproject.read_text(encoding="utf-8").replace(
            "relative_files = true", "relative_files = false"
        ),
        encoding="utf-8",
    )

    result = _run(str(tmp_path))

    assert result.returncode == 1
    assert "relative coverage paths" in result.stderr


@pytest.mark.parametrize(
    ("needle", "expected"),
    [
        (
            "HEAVY_RESULT: ${{ needs.test_heavy.result }}",
            "heavy result input",
        ),
        (
            "LIGHT_RESULT: ${{ needs.test_light.result }}",
            "light result input",
        ),
        (
            "COVERAGE_RESULT: ${{ needs.coverage_combine.result }}",
            "coverage result input",
        ),
    ],
)
def test_full_test_aggregate_requires_every_result(
    tmp_path: Path, needle: str, expected: str
) -> None:
    _write_minimal_contract(tmp_path)
    ci = tmp_path / ".github/workflows/ci.yml"
    ci.write_text(
        ci.read_text(encoding="utf-8").replace(needle, "REMOVED_RESULT: skipped"),
        encoding="utf-8",
    )

    result = _run(str(tmp_path))

    assert result.returncode == 1
    assert expected in result.stderr


def test_coverage_combine_keeps_default_dependency_semantics(tmp_path: Path) -> None:
    _write_minimal_contract(tmp_path)
    ci = tmp_path / ".github/workflows/ci.yml"
    ci.write_text(
        ci.read_text(encoding="utf-8").replace(
            "  coverage_combine:\n    name: coverage (py3.14)",
            "  coverage_combine:\n    name: coverage (py3.14)\n    if: ${{ always() }}",
        ),
        encoding="utf-8",
    )

    result = _run(str(tmp_path))

    assert result.returncode == 1
    assert "default success dependency semantics" in result.stderr


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


def test_windows_collection_must_remain_advisory(tmp_path: Path) -> None:
    _write_minimal_contract(tmp_path)
    ci = tmp_path / ".github/workflows/ci.yml"
    ci.write_text(
        ci.read_text(encoding="utf-8").replace(
            "needs: [test, socket]", "needs: [test, socket, windows_collection]"
        ),
        encoding="utf-8",
    )

    result = _run(str(tmp_path))

    assert result.returncode == 1
    assert "advisory Windows job in CI Result dependencies" in result.stderr


def test_windows_advisory_guard_does_not_assume_job_indentation(
    tmp_path: Path,
) -> None:
    _write_minimal_contract(tmp_path)
    ci = tmp_path / ".github/workflows/ci.yml"
    ci.write_text(
        ci.read_text(encoding="utf-8")
        .replace("  ci_result:", " ci_result:")
        .replace(
            "needs: [test, socket]", "needs: [test, socket, windows_collection]"
        ),
        encoding="utf-8",
    )

    result = _run(str(tmp_path))

    assert result.returncode == 1
    assert "advisory Windows job in CI Result dependencies" in result.stderr


def test_windows_advisory_guard_allows_commented_mapping_keys(
    tmp_path: Path,
) -> None:
    _write_minimal_contract(tmp_path)
    ci = tmp_path / ".github/workflows/ci.yml"
    ci.write_text(
        ci.read_text(encoding="utf-8").replace(
            "  ci_result:", "  ci_result:  # stable aggregate"
        )
        + "\n  later_job:  # unrelated sibling\n"
        + "    steps:\n"
        + "      - run: echo windows_collection\n",
        encoding="utf-8",
    )

    result = _run(str(tmp_path))

    assert result.returncode == 0, result.stderr


def test_windows_collection_requires_locked_environment(tmp_path: Path) -> None:
    _write_minimal_contract(tmp_path)
    ci = tmp_path / ".github/workflows/ci.yml"
    ci.write_text(
        ci.read_text(encoding="utf-8").replace(
            "uv sync --extra dev --locked --python 3.14",
            "uv sync --extra dev --python 3.14",
        ),
        encoding="utf-8",
    )

    result = _run(str(tmp_path))

    assert result.returncode == 1
    assert "Windows locked development sync" in result.stderr


def test_pack_sync_forbids_direct_main_push(tmp_path: Path) -> None:
    _write_minimal_contract(tmp_path)
    workflow = tmp_path / ".github/workflows/sd-ai-command-pack-sync.yml"
    workflow.write_text(
        workflow.read_text(encoding="utf-8") + "\n- run: git push origin main\n",
        encoding="utf-8",
    )

    result = _run(str(tmp_path))

    assert result.returncode == 1
    assert "direct default-branch push" in result.stderr


def test_pack_sync_requires_pr_creation_action(tmp_path: Path) -> None:
    _write_minimal_contract(tmp_path)
    workflow = tmp_path / ".github/workflows/sd-ai-command-pack-sync.yml"
    workflow.write_text(
        workflow.read_text(encoding="utf-8").replace(
            "peter-evans/create-pull-request@5f6978faf089d4d20b00c7766989d076bb2fc7f1",
            "removed/action@5f6978faf089d4d20b00c7766989d076bb2fc7f1",
        ),
        encoding="utf-8",
    )

    result = _run(str(tmp_path))

    assert result.returncode == 1
    assert "pinned create-pull-request action" in result.stderr


def test_pack_sync_forbids_repo_wide_workflow_token(tmp_path: Path) -> None:
    _write_minimal_contract(tmp_path)
    workflow = tmp_path / ".github/workflows/sd-ai-command-pack-sync.yml"
    workflow.write_text(
        workflow.read_text(encoding="utf-8").replace(
            "secrets.SD_AI_COMMAND_PACK_PR_TOKEN", "secrets.GITHUB_TOKEN"
        ),
        encoding="utf-8",
    )

    result = _run(str(tmp_path))

    assert result.returncode == 1
    assert "scoped PR token" in result.stderr
    assert "repo-wide workflow token for PR writes" in result.stderr


def test_pack_sync_requires_fail_closed_secret_preflight(tmp_path: Path) -> None:
    _write_minimal_contract(tmp_path)
    workflow = tmp_path / ".github/workflows/sd-ai-command-pack-sync.yml"
    workflow.write_text(
        workflow.read_text(encoding="utf-8").replace(
            'if [ -z "$SCOPED_TOKEN" ]; then',
            'if [ -n "$SCOPED_TOKEN" ]; then',
        ),
        encoding="utf-8",
    )

    result = _run(str(tmp_path))

    assert result.returncode == 1
    assert "fail-closed scoped token preflight" in result.stderr


def test_pack_sync_default_token_must_remain_read_only(tmp_path: Path) -> None:
    _write_minimal_contract(tmp_path)
    workflow = tmp_path / ".github/workflows/sd-ai-command-pack-sync.yml"
    workflow.write_text(
        workflow.read_text(encoding="utf-8").replace(
            "contents: read", "contents: write\n  pull-requests: write"
        ),
        encoding="utf-8",
    )

    result = _run(str(tmp_path))

    assert result.returncode == 1
    assert "read-only default workflow token" in result.stderr
    assert "default token contents write" in result.stderr


def test_ci_docs_must_cover_scheduled_sync(tmp_path: Path) -> None:
    _write_minimal_contract(tmp_path)
    docs = tmp_path / "docs/DEVELOPMENT_CYCLE.md"
    docs.write_text(
        docs.read_text(encoding="utf-8").replace(
            "sd-ai-command-pack-sync.yml", "removed-sync.yml"
        ),
        encoding="utf-8",
    )

    result = _run(str(tmp_path))

    assert result.returncode == 1
    assert "scheduled command-pack sync" in result.stderr


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


def test_codeql_init_and_analyze_revisions_must_match(tmp_path: Path) -> None:
    _write_minimal_contract(tmp_path)
    codeql = tmp_path / ".github/workflows/codeql.yml"
    codeql.write_text(
        codeql.read_text(encoding="utf-8").replace(
            "github/codeql-action/init@1111111111111111111111111111111111111111",
            "github/codeql-action/init@2222222222222222222222222222222222222222",
        ),
        encoding="utf-8",
    )

    result = _run(str(tmp_path))

    assert result.returncode == 1
    assert "CodeQL init/analyze revisions must match" in result.stderr


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


@pytest.mark.parametrize(
    ("needle", "label"),
    [
        ("python tools/check_amc_module_load.py", "AMC module-load CI guard"),
        ("python tools/check_role_name_leaks.py", "role-name CI guard"),
        (
            "python tools/check_agent_hook_exceptions.py",
            "agent-hook-exception CI guard",
        ),
    ],
)
def test_fast_ci_guards_are_contract_pinned(
    tmp_path: Path, needle: str, label: str
) -> None:
    _write_minimal_contract(tmp_path)
    ci = tmp_path / ".github/workflows/ci.yml"
    ci.write_text(
        ci.read_text(encoding="utf-8").replace(needle, "python removed.py"),
        encoding="utf-8",
    )

    result = _run(str(tmp_path))

    assert result.returncode == 1
    assert label in result.stderr


def test_branch_guard_must_use_pull_request_head_ref(tmp_path: Path) -> None:
    _write_minimal_contract(tmp_path)
    ci = tmp_path / ".github/workflows/ci.yml"
    ci.write_text(
        ci.read_text(encoding="utf-8").replace(
            "HEAD_REF: ${{ github.head_ref }}",
            "HEAD_REF: ${{ github.ref }}",
        ),
        encoding="utf-8",
    )

    result = _run(str(tmp_path))

    assert result.returncode == 1
    assert "pull-request head-ref branch guard" in result.stderr


def test_role_name_commit_message_hook_is_contract_pinned(tmp_path: Path) -> None:
    _write_minimal_contract(tmp_path)
    precommit = tmp_path / ".pre-commit-config.yaml"
    precommit.write_text(
        precommit.read_text(encoding="utf-8").replace(
            "id: role-name-commit-message",
            "id: removed-role-name-commit-message",
        ),
        encoding="utf-8",
    )

    result = _run(str(tmp_path))

    assert result.returncode == 1
    assert "role-name commit-message hook" in result.stderr


def test_missing_repo_file_exits_two(tmp_path: Path) -> None:
    _write_minimal_contract(tmp_path)
    (tmp_path / "scripts/classify-ci-changes.sh").unlink()

    result = _run(str(tmp_path))

    assert result.returncode == 2
    assert "cannot read" in result.stderr
