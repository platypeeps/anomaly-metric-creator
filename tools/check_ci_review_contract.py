#!/usr/bin/env python3
"""Guard the local review and CI cadence contract.

The workflow cadence is intentionally spread across a few files:

* ``scripts/classify-ci-changes.sh`` owns path classification.
* ``.github/workflows/ci.yml`` chooses the lightweight, quick, or full lane.
* The Socket job, CodeQL workflow, and Dependabot workflow follow the same
  review-economy policy.
* The scheduled command-pack workflow is PR-only and no-ops on an empty diff.
* ``scripts/sd-ai-command-pack-full-check.sh`` mirrors the local quick/full gate.

This checker is deliberately text-based and stdlib-only so pre-commit can run it
without installing project dependencies or parsing YAML. It catches accidental
removal of the contract's named anchors before a PR spends remote Actions time
discovering the drift.

Exit codes:

* ``0`` - contract anchors are present.
* ``1`` - at least one contract violation was found.
* ``2`` - argument or I/O error.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path


REQUIRED_FILES = {
    "ci": Path(".github/workflows/ci.yml"),
    "codeql": Path(".github/workflows/codeql.yml"),
    "dependabot": Path(".github/workflows/dependabot-auto-merge.yml"),
    "dependabot_config": Path(".github/dependabot.yml"),
    "pack_sync": Path(".github/workflows/sd-ai-command-pack-sync.yml"),
    "pyproject": Path("pyproject.toml"),
    "precommit": Path(".pre-commit-config.yaml"),
    "classifier": Path("scripts/classify-ci-changes.sh"),
    "full_check": Path("scripts/sd-ai-command-pack-full-check.sh"),
    "development_cycle": Path("docs/DEVELOPMENT_CYCLE.md"),
    "review_pack": Path("docs/SD_AI_COMMAND_PACK.md"),
    "testing_spec": Path(".trellis/spec/amc/backend/testing-quality.md"),
    "copilot_ci": Path(
        ".github/instructions/anomaly-metric-creator.instructions.md"
    ),
}

_REVIEW_TOOLING_SHELL_SYNTAX = (
    "bash -n scripts/classify-ci-changes.sh scripts/classify_ci_changes.sh "
    "scripts/sd-ai-command-pack-full-check.sh "
    "scripts/sd-ai-command-pack-housekeeping.sh "
    "scripts/sd-ai-command-pack-review-scope.sh "
    "scripts/sd-ai-command-pack-shell-lib.sh "
    "scripts/sd-ai-command-pack-toolchain.sh"
)
_CI_PYTHON_SYNTAX_GLOB = (
    "git ls-files 'scripts/*.py' 'tools/*.py' 'tests/*.py' "
    "'.codex/hooks/*.py' '.github/copilot/hooks/*.py' '.gemini/hooks/*.py'"
)
_PRECOMMIT_PYTHON_SYNTAX_FILES = (
    r"files: ^(scripts|src|tests|tools|\.codex/hooks|\.github/copilot/hooks|"
    r"\.gemini/hooks)/.*\.py$"
)
_LIGHTWEIGHT_PYTHON_PREFIX = "uv run --python 3.14 --no-project python"
_LIGHTWEIGHT_PYTHON_GUARDS = (
    "tools/check_python_syntax.py",
    "tools/check_workflow_pip.py",
    "tools/check_trellis_placeholders.py",
    "tools/check_ci_review_contract.py",
    "tools/check_copilot_instruction_contract.py",
    "scripts/sd-ai-command-pack-pr-body-scope.py",
)
_CODEQL_ACTION_PATTERN = re.compile(
    r"^\s*uses:\s*github/codeql-action/(init|analyze)@([0-9a-f]{40})(?:\s|$)",
    re.MULTILINE,
)


def _single_pinned_action_revision(
    text: str,
    action: str,
    *,
    path: Path,
    violations: list[str],
) -> str | None:
    """Return the shared full-SHA revision for every use of ``action``."""
    pattern = re.compile(
        rf"^\s*(?:-\s*)?uses:\s*{re.escape(action)}@([^\s#]+)(?:\s|$)",
        re.MULTILINE,
    )
    revisions = pattern.findall(text)
    if not revisions:
        violations.append(f"{path}: expected at least one {action} step")
        return None

    unpinned = sorted(
        revision
        for revision in set(revisions)
        if re.fullmatch(r"[0-9a-f]{40}", revision) is None
    )
    if unpinned:
        violations.append(
            f"{path}: every {action} step must use a full 40-character "
            f"commit SHA; found {', '.join(unpinned)}"
        )
        return None

    unique_revisions = sorted(set(revisions))
    if len(unique_revisions) != 1:
        violations.append(
            f"{path}: {action} revisions must match: "
            f"{', '.join(unique_revisions)}"
        )
        return None

    return unique_revisions[0]


def _shared_pinned_action_revisions(
    root: Path,
    texts: dict[str, str],
    action: str,
    keys: tuple[str, ...],
    *,
    violations: list[str],
) -> dict[str, str]:
    """Validate one pinned revision across the selected workflow files."""
    revisions: dict[str, str] = {}
    for key in keys:
        revision = _single_pinned_action_revision(
            texts[key],
            action,
            path=root / REQUIRED_FILES[key],
            violations=violations,
        )
        if revision is not None:
            revisions[key] = revision

    if len(set(revisions.values())) > 1:
        rendered = ", ".join(
            f"{REQUIRED_FILES[key]}@{revision}"
            for key, revision in revisions.items()
        )
        violations.append(
            f"{root / '.github/workflows'}: {action} revisions must match "
            f"across workflows: {rendered}"
        )
    return revisions


def _check_setup_uv_cache_pruning(
    path: Path,
    text: str,
    violations: list[str],
) -> None:
    """Require v8-equivalent pruning whenever setup-uv caching is enabled."""
    lines = text.splitlines()
    for action_index, action_line in enumerate(lines):
        if "uses: astral-sh/setup-uv@" not in action_line:
            continue

        step_start = action_index
        step_indent = None
        for candidate in range(action_index, -1, -1):
            match = re.match(r"^(?P<indent>\s*)-\s+", lines[candidate])
            if match is not None:
                step_start = candidate
                step_indent = match.group("indent")
                break

        step_end = len(lines)
        if step_indent is not None:
            next_step = re.compile(rf"^{re.escape(step_indent)}-\s+")
            for candidate in range(step_start + 1, len(lines)):
                if next_step.match(lines[candidate]):
                    step_end = candidate
                    break

        step_block = "\n".join(lines[step_start:step_end])
        cache_enabled = re.search(
            r"^\s*enable-cache:\s*true\s*(?:#.*)?$",
            step_block,
            re.MULTILINE,
        )
        pruning_enabled = re.search(
            r"^\s*prune-cache:\s*true\s*(?:#.*)?$",
            step_block,
            re.MULTILINE,
        )
        if cache_enabled is not None and pruning_enabled is None:
            violations.append(
                f"{path}:{action_index + 1}: setup-uv cache-enabled step "
                "must set prune-cache: true"
            )


def _read(path: Path) -> tuple[str | None, str | None]:
    try:
        return path.read_text(encoding="utf-8"), None
    except OSError as exc:
        return None, f"cannot read {path}: {exc}"
    except UnicodeError as exc:
        return None, f"cannot decode {path}: {exc}"


def _require_contains(
    text: str,
    needle: str,
    *,
    path: Path,
    label: str,
    violations: list[str],
) -> None:
    normalized_text = " ".join(text.split())
    normalized_needle = " ".join(needle.split())
    if needle not in text and normalized_needle not in normalized_text:
        violations.append(f"{path}: missing {label}: {needle!r}")


def _require_not_contains(
    text: str,
    needle: str,
    *,
    path: Path,
    label: str,
    violations: list[str],
) -> None:
    if needle in text:
        violations.append(f"{path}: forbidden {label}: {needle!r}")


def _yaml_mapping_block(text: str, key: str) -> str | None:
    """Return a top-level mapping entry without assuming its indentation."""
    match = re.search(
        rf"^(?P<indent>[ \t]*){re.escape(key)}:[ \t]*(?:#[^\n]*)?$",
        text,
        re.MULTILINE,
    )
    if match is None:
        return None

    indent = match.group("indent")
    next_entry = re.search(
        rf"^{re.escape(indent)}\S[^:\n]*:[ \t]*(?:#[^\n]*)?$",
        text[match.end() :],
        re.MULTILINE,
    )
    end = match.end() + next_entry.start() if next_entry is not None else len(text)
    return text[match.start() : end]


def _yaml_list_item_block(text: str, key: str, value: str) -> str | None:
    """Return a YAML list item and its nested block, selected by a scalar."""
    match = re.search(
        rf"^(?P<indent>[ \t]*)-\s+{re.escape(key)}:\s*[\"']?"
        rf"{re.escape(value)}[\"']?\s*(?:#[^\n]*)?$",
        text,
        re.MULTILINE,
    )
    if match is None:
        return None

    indent = match.group("indent")
    next_item = re.search(
        rf"^{re.escape(indent)}-\s+\S",
        text[match.end() :],
        re.MULTILINE,
    )
    end = match.end() + next_item.start() if next_item is not None else len(text)
    return text[match.start() : end]


def _yaml_string_list_contains(text: str, key: str, value: str) -> bool:
    """Return whether an inline or block-style YAML string list contains a value."""
    inline = re.search(
        rf"^\s*{re.escape(key)}:\s*\[(?P<items>[^\]]*)\]\s*(?:#[^\n]*)?$",
        text,
        re.MULTILINE,
    )
    if inline is not None:
        items = [
            item.strip().strip("\"'")
            for item in inline.group("items").split(",")
        ]
        return value in items

    block = re.search(
        rf"^(?P<indent>[ \t]*){re.escape(key)}:\s*(?:#[^\n]*)?$",
        text,
        re.MULTILINE,
    )
    if block is None:
        return False

    indent = block.group("indent")
    next_key = re.search(
        rf"^{re.escape(indent)}\S[^:\n]*:\s*(?:#[^\n]*)?$",
        text[block.end() :],
        re.MULTILINE,
    )
    end = block.end() + next_key.start() if next_key is not None else len(text)
    for line in text[block.end() : end].splitlines():
        item = re.match(r"^\s+-\s*(?P<value>[^#]+?)\s*(?:#.*)?$", line)
        if item is not None and item.group("value").strip().strip("\"'") == value:
            return True
    return False


def _check_lightweight_uv_cache_permissions(
    path: Path,
    text: str,
    violations: list[str],
) -> None:
    """Require a private setup-uv cache before pack-backed lightweight guards."""
    block = _yaml_mapping_block(text, "lightweight_readiness")
    if block is None:
        return

    setup_marker = "name: Set up uv for lightweight guards"
    permission_marker = "name: Harden uv cache permissions for pack subprocess guards"
    guard_marker = "name: Syntax and Trellis artifact guards"
    permission_command = 'install -d -m 0700 -- "$UV_CACHE_DIR"'

    _require_contains(
        block,
        setup_marker,
        path=path,
        label="lightweight uv setup step",
        violations=violations,
    )
    _require_contains(
        block,
        permission_marker,
        path=path,
        label="lightweight uv cache permission step",
        violations=violations,
    )
    _require_contains(
        block,
        permission_command,
        path=path,
        label="lightweight uv cache private-directory command",
        violations=violations,
    )
    _require_contains(
        block,
        guard_marker,
        path=path,
        label="lightweight Syntax and Trellis guard step",
        violations=violations,
    )

    positions = tuple(
        block.find(marker)
        for marker in (setup_marker, permission_marker, guard_marker)
    )
    if (
        all(position >= 0 for position in positions)
        and positions != tuple(sorted(positions))
    ):
        violations.append(
            f"{path}: lightweight uv cache permission step must run after "
            "setup-uv and before the Syntax and Trellis artifact guards"
        )


def _check_ci(
    path: Path,
    text: str,
    violations: list[str],
    *,
    checkout_revision: str | None,
) -> None:
    setup_uv_revision = _single_pinned_action_revision(
        text,
        "astral-sh/setup-uv",
        path=path,
        violations=violations,
    )
    _check_setup_uv_cache_pruning(path, text, violations)
    _check_lightweight_uv_cache_permissions(path, text, violations)

    for label, needle in [
        ("change classifier job", "changes:"),
        ("lightweight lane", "lightweight_readiness:"),
        ("quick lane", "quick_check:"),
        ("heavy full-test lane", "test_heavy:"),
        ("light full-test lane", "test_light:"),
        ("coverage combine lane", "coverage_combine:"),
        ("application aggregate", "  test:"),
        (
            "aggregate lane dependencies",
            "needs: [changes, lightweight_readiness, quick_check, test_heavy, test_light, coverage_combine]",
        ),
        ("stable aggregate", "  ci_result:"),
        ("stable aggregate name", "name: CI Result"),
        ("stable aggregate dependencies", "needs: [test, socket]"),
        ("application result input", "APP_RESULT: ${{ needs.test.result }}"),
        ("Socket result input", "SOCKET_RESULT: ${{ needs.socket.result }}"),
        (
            "classifier invocation",
            'bash scripts/classify-ci-changes.sh "${classifier_args[@]}" changed-files.txt',
        ),
        (
            "manual dispatch classifier force-app",
            'if [ "$EVENT_NAME" = "workflow_dispatch" ]; then'
            " classifier_args+=(--force-app) fi",
        ),
        ("full-ci trigger", "full-ci"),
        ("full-ci output", "full_ci_requested"),
        (
            "full-ci output bracket expression",
            "steps['full-ci'].outputs.full_ci_requested",
        ),
        ("lightweight result text", "selected lane: lightweight readiness"),
        ("quick result text", "selected lane: quick test"),
        ("full result text", "selected lane: full test lanes"),
        ("heavy result input", "HEAVY_RESULT: ${{ needs.test_heavy.result }}"),
        ("light result input", "LIGHT_RESULT: ${{ needs.test_light.result }}"),
        (
            "coverage result input",
            "COVERAGE_RESULT: ${{ needs.coverage_combine.result }}",
        ),
        ("heavy result gate", 'test "$HEAVY_RESULT" = "success"'),
        ("light result gate", 'test "$LIGHT_RESULT" = "success"'),
        ("coverage result gate", 'test "$COVERAGE_RESULT" = "success"'),
        (
            "lightweight whitespace PR diff",
            'git diff --check "origin/$BASE_REF...HEAD"',
        ),
        (
            "review-churn test coverage",
            "tests/test_ci_review_contract.py",
        ),
        (
            "Copilot instruction contract guard",
            "python tools/check_copilot_instruction_contract.py",
        ),
        (
            "PR body scope guard",
            "python scripts/sd-ai-command-pack-pr-body-scope.py",
        ),
        # `uv sync` must pass --locked so pyproject/uv.lock drift fails the
        # job instead of silently re-resolving in the runner
        # (07-06-uv-locked-ci-enforcement). Pins both the quick and full
        # lanes via the shared substring.
        (
            "locked dependency sync",
            "uv sync --extra dev --locked",
        ),
        (
            "Copilot instruction contract test coverage",
            "tests/test_copilot_instruction_contract.py",
        ),
        (
            "PR body scope test coverage",
            "tests/test_pr_body_scope_lint.py",
        ),
        (
            "pull-request head-ref branch guard",
            'HEAD_REF: ${{ github.head_ref }}',
        ),
        (
            "branch-name guard invocation",
            'python tools/check_branch_name.py "$HEAD_REF"',
        ),
        (
            "AMC module-load CI guard",
            "python tools/check_amc_module_load.py",
        ),
        (
            "test-resource-cost CI guard",
            "python tools/check_test_resource_cost.py",
        ),
        (
            "role-name CI guard",
            "python tools/check_role_name_leaks.py",
        ),
        (
            "role-name live-tree roots",
            "git ls-files src scripts .agents .trellis",
        ),
        (
            "agent-hook-exception CI guard",
            "python tools/check_agent_hook_exceptions.py",
        ),
        (
            # Task-text-only PRs skip every test job, so the criteria guard's
            # own test never runs for them; this step is its only CI lane.
            "task-criteria CI guard",
            "python tools/check_task_criteria_commands.py",
        ),
        (
            # Pinned separately from the invocation above: the guard is only
            # useful over the whole tracked task tree. A criterion goes stale
            # when the command it names changes, not when its own file is
            # edited, so narrowing this to the diff would satisfy the guard
            # needle while silently dropping the coverage it exists for.
            "task-criteria live-tree roots",
            "git ls-files '.trellis/tasks/*.md' '.trellis/tasks/**/*.md'",
        ),
        (
            # Load-bearing, not defensive: with zero path operands the guard
            # exits 2 with its usage line, so dropping this gate would turn an
            # empty task tree into a CI failure.
            "task-criteria empty-tree gate",
            'if [ "${#task_criteria_files[@]}" -gt 0 ]; then',
        ),
        (
            # The meta-guard that keeps every other entry in this list honest.
            # It must live in the unconditional `changes` job: gating it on a
            # lane would hide exactly the lane gap it exists to detect.
            "guard CI-coverage meta-guard",
            "python tools/check_guard_ci_coverage.py",
        ),
        (
            "canonical mypy gate invocation",
            "python tools/check_mypy_gate.py",
        ),
        (
            "heavy pytest partition",
            "pytest -n 2 --dist loadfile -m heavy "
            "--cov=src/anomaly_metric_creator --cov-report=",
        ),
        (
            "light pytest partition",
            'pytest -n 2 --dist loadfile -m "not heavy" '
            "--cov=src/anomaly_metric_creator --cov-report=",
        ),
        ("visible heavy coverage data", "mv .coverage coverage-heavy"),
        ("visible light coverage data", "mv .coverage coverage-light"),
        ("heavy coverage artifact", "name: coverage-data-heavy"),
        ("light coverage artifact", "name: coverage-data-light"),
        (
            "pinned coverage download action",
            "actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c",
        ),
        ("coverage XML command", "coverage xml"),
        ("coverage threshold gate", "coverage report --fail-under=85"),
        ("coverage XML artifact", "name: coverage-xml-py3.14"),
        (
            "auto-merge enabled PR event",
            "types: [opened, synchronize, reopened, ready_for_review, labeled, auto_merge_enabled]",
        ),
        (
            "auto-merge synchronize gate",
            "github.event.pull_request.auto_merge != null",
        ),
        # The two needles below are whitespace-normalized by
        # _require_contains, so they pin the case labels together with
        # their full_ci_requested assignments: keeping a label while
        # dropping its assignment (reopening the quick-lane auto-merge
        # gap) breaks the anchor.
        (
            "auto-merge synchronize full-ci request",
            'synchronize) if [ "$PR_AUTO_MERGE" = "true" ]; then'
            " full_ci_requested=true fi ;;",
        ),
        (
            "auto-merge labeled full-ci request",
            'labeled) if [ "$PR_LABEL" = "full-ci" ] ||'
            ' [ "$PR_AUTO_MERGE" = "true" ]; then'
            " full_ci_requested=true fi ;;",
        ),
        (
            "auto-merge enabled full-ci request",
            "auto_merge_enabled) full_ci_requested=true ;;",
        ),
        (
            "per-commit push concurrency",
            "group: ci-${{ github.event_name == 'push' && github.sha || github.ref }}",
        ),
        # The aggregate `test` job must guard with !cancelled(), not always():
        # always() runs the aggregate even when concurrency cancels the run,
        # evaluating `test "cancelled" = "success"` -> a transient FAILURE on
        # every auto-merge-armed PR (07-03-ci-cadence-churn-refinement). If
        # this anchor disappears the churn has been reintroduced.
        (
            "aggregate cancellation-safe guard",
            "if: ${{ !cancelled() }}",
        ),
        (
            "lightweight uv setup",
            "name: Set up uv for lightweight guards",
        ),
        (
            "CI review-tooling shell syntax coverage",
            _REVIEW_TOOLING_SHELL_SYNTAX,
        ),
        (
            "CI scripts Python syntax coverage",
            _CI_PYTHON_SYNTAX_GLOB,
        ),
        ("advisory Windows collection job", "  windows_collection:"),
        ("Windows runner", "runs-on: windows-latest"),
        ("Windows job advisory guard", "continue-on-error: true"),
        (
            "Windows locked development sync",
            "uv sync --extra dev --locked --python 3.14",
        ),
        (
            "Windows collection command",
            "uv run --no-sync pytest --collect-only -q",
        ),
    ]:
        _require_contains(text, needle, path=path, label=label, violations=violations)
    for guard in _LIGHTWEIGHT_PYTHON_GUARDS:
        _require_contains(
            text,
            f"{_LIGHTWEIGHT_PYTHON_PREFIX} {guard}",
            path=path,
            label=f"pinned Python lightweight guard ({guard})",
            violations=violations,
        )
    _require_not_contains(
        text,
        "steps.full-ci.outputs.full_ci_requested",
        path=path,
        label="full-ci output dot expression",
        violations=violations,
    )
    # ci.yml honors full-ci ONE-SHOT (only at the `labeled` event). The
    # persistent `contains(...labels...'full-ci')` re-check belongs to
    # codeql.yml alone; if it appears here, someone made the cost-gated full
    # matrix persistent-on-label — a deliberate cadence change that must
    # update this contract, not slip in silently. (Pairs with the codeql
    # positive anchor above.)
    _require_not_contains(
        text,
        "contains(github.event.pull_request.labels.*.name, 'full-ci')",
        path=path,
        label="persistent full-ci re-check (belongs only in codeql.yml)",
        violations=violations,
    )

    ci_result_block = _yaml_mapping_block(text, "ci_result")
    if ci_result_block is None:
        violations.append(f"{path}: cannot inspect stable aggregate job block")
    else:
        _require_not_contains(
            ci_result_block,
            "windows_collection",
            path=path,
            label="advisory Windows job in CI Result dependencies",
            violations=violations,
        )

    coverage_block = _yaml_mapping_block(text, "coverage_combine")
    if coverage_block is None:
        violations.append(f"{path}: cannot inspect coverage combine job block")
    else:
        coverage_header = coverage_block.split("steps:", maxsplit=1)[0]
        _require_not_contains(
            coverage_header,
            "if:",
            path=path,
            label="coverage combine job-level condition (must use default success dependency semantics)",
            violations=violations,
        )
        coverage_requirements = [
            ("coverage combine dependencies", "needs: [test_heavy, test_light]"),
            ("coverage combine locked sync", "uv sync --extra dev --locked --python 3.14"),
            ("heavy coverage input", "mv coverage-data/coverage-heavy .coverage.heavy"),
            ("light coverage input", "mv coverage-data/coverage-light .coverage.light"),
            ("coverage combine command", "coverage combine"),
            ("coverage XML generation", "coverage xml"),
            ("coverage threshold report", "coverage report --fail-under=85"),
            ("coverage report cancellation-safe upload", "if: ${{ !cancelled() }}"),
        ]
        if checkout_revision is not None:
            coverage_requirements.append(
                (
                    "coverage combine checkout",
                    f"actions/checkout@{checkout_revision}",
                )
            )
        if setup_uv_revision is not None:
            coverage_requirements.append(
                (
                    "coverage combine uv setup",
                    f"astral-sh/setup-uv@{setup_uv_revision}",
                )
            )

        for label, needle in coverage_requirements:
            _require_contains(
                coverage_block,
                needle,
                path=path,
                label=label,
                violations=violations,
            )

        ordered_needles = [
            "coverage combine",
            "coverage xml",
            "coverage report --fail-under=85",
            "name: Upload coverage report",
        ]
        positions = [coverage_block.find(needle) for needle in ordered_needles]
        if all(position >= 0 for position in positions) and positions != sorted(positions):
            violations.append(
                f"{path}: coverage combine, XML, threshold, and upload steps "
                "must remain in diagnostic-preserving order"
            )

    lane_contracts = [
        (
            "test_heavy",
            "heavy",
            "pytest -n 2 --dist loadfile -m heavy "
            "--cov=src/anomaly_metric_creator --cov-report=",
            "mv .coverage coverage-heavy",
            "name: coverage-data-heavy",
        ),
        (
            "test_light",
            "light",
            'pytest -n 2 --dist loadfile -m "not heavy" '
            "--cov=src/anomaly_metric_creator --cov-report=",
            "mv .coverage coverage-light",
            "name: coverage-data-light",
        ),
    ]
    full_lane_if = (
        "if: needs.changes.outputs.app_required == 'true' && "
        "needs.changes.outputs.full_ci_requested == 'true'"
    )
    for key, label, pytest_command, move_command, artifact_name in lane_contracts:
        block = _yaml_mapping_block(text, key)
        if block is None:
            violations.append(f"{path}: cannot inspect {label} full-test job block")
            continue
        for anchor_label, needle in [
            (f"{label} change dependency", "needs: changes"),
            (f"{label} full-CI condition", full_lane_if),
            (f"{label} locked sync", "uv sync --extra dev --locked"),
            (f"{label} pytest command", pytest_command),
            (f"{label} visible coverage data", move_command),
            (f"{label} coverage artifact", artifact_name),
        ]:
            _require_contains(
                block,
                needle,
                path=path,
                label=anchor_label,
                violations=violations,
            )

    light_block = _yaml_mapping_block(text, "test_light")
    if light_block is None:
        violations.append(f"{path}: cannot inspect real-client smoke job block")
    else:
        for label, needle in [
            ("fail-closed real-client installer", "set -euo pipefail"),
            ("pinned kubectl version", "KUBECTL_VERSION: v1.36.2"),
            (
                "pinned kubectl checksum",
                "KUBECTL_SHA256: 1e9045ec32bea85da43de85f0065358529ea7c7a152eca78154fba5b58c27d82",
            ),
            (
                "kubectl checksum wiring",
                "printf '%s  %s\\n' \"$KUBECTL_SHA256\" \"$client_bin/kubectl\"",
            ),
            (
                "official kubectl download",
                "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/"
                "linux/amd64/kubectl",
            ),
            ("pinned Helm version", "HELM_VERSION: v4.2.0"),
            (
                "pinned Helm checksum",
                "HELM_SHA256: 97dbeb971be4ac4b27e3839976d9564c0fb35c6f3b1da89dd1e292d236af4096",
            ),
            (
                "Helm checksum wiring",
                "printf '%s  %s\\n' \"$HELM_SHA256\" \"$client_root/helm.tar.gz\"",
            ),
            (
                "official Helm download",
                "https://get.helm.sh/helm-${HELM_VERSION}-linux-amd64.tar.gz",
            ),
            (
                "fail-closed real-client download",
                "curl --fail --location --show-error --silent",
            ),
            ("real-client download retry", "--retry 3 --retry-all-errors"),
            ("real-client PATH export", 'echo "$client_bin" >> "$GITHUB_PATH"'),
            ("real-client smoke opt-in", 'AMC_RUN_REAL_CLIENT_SMOKE: "1"'),
            ("serial real-client smoke", "pytest -n 0 -q"),
            (
                "Helm real-client smoke selector",
                "tests/test_server.py::test_real_helm4_binary_smoke_when_available",
            ),
            (
                "kubectl real-client smoke selector",
                "tests/test_server.py::test_real_kubectl_binary_smoke_when_available",
            ),
        ]:
            _require_contains(
                light_block,
                needle,
                path=path,
                label=label,
                violations=violations,
            )
        checksum_checks = light_block.count("sha256sum --check --strict")
        if checksum_checks != 2:
            violations.append(
                f"{path}: expected exactly two real-client checksum checks, "
                f"found {checksum_checks}"
            )
        fail_closed_downloads = light_block.count(
            "curl --fail --location --show-error --silent"
        )
        if fail_closed_downloads != 2:
            violations.append(
                f"{path}: expected exactly two fail-closed real-client "
                f"downloads, found {fail_closed_downloads}"
            )


def _check_pyproject(path: Path, text: str, violations: list[str]) -> None:
    for label, needle in [
        ("coverage run configuration", "[tool.coverage.run]"),
        ("relative coverage paths", "relative_files = true"),
    ]:
        _require_contains(text, needle, path=path, label=label, violations=violations)


def _check_codeql(path: Path, text: str, violations: list[str]) -> None:
    for label, needle in [
        (
            "required-context pull request events",
            "types: [opened, synchronize, reopened, ready_for_review, labeled]",
        ),
        ("concurrency", "concurrency:"),
        ("synchronize trigger", "github.event.action == 'synchronize'"),
        ("full-ci label trigger", "github.event.label.name == 'full-ci'"),
        # CodeQL honors full-ci PERSISTENTLY: the synchronize arm re-reads the
        # label set on every push (contains(...labels...)), unlike the Socket
        # job in ci.yml which is one-shot at the `labeled` event. This anchor
        # pins that intentional asymmetry so a "unify to one-shot" edit (which
        # would cut security coverage) breaks the contract instead.
        (
            "persistent full-ci re-check on synchronize",
            "contains(github.event.pull_request.labels.*.name, 'full-ci')",
        ),
    ]:
        _require_contains(text, needle, path=path, label=label, violations=violations)

    revisions: dict[str, list[str]] = {"init": [], "analyze": []}
    for action, revision in _CODEQL_ACTION_PATTERN.findall(text):
        revisions[action].append(revision)

    for action, action_revisions in revisions.items():
        if len(action_revisions) != 1:
            violations.append(
                f"{path}: expected exactly one pinned github/codeql-action/{action} "
                f"step, found {len(action_revisions)}"
            )

    if all(len(action_revisions) == 1 for action_revisions in revisions.values()):
        if revisions["init"][0] != revisions["analyze"][0]:
            violations.append(
                f"{path}: CodeQL init/analyze revisions must match: "
                f"init@{revisions['init'][0]} != analyze@{revisions['analyze'][0]}"
            )


def _check_socket(path: Path, text: str, violations: list[str]) -> None:
    for label, needle in [
        ("Socket job", "  socket:"),
        ("centralized classification dependency", "needs: changes"),
        ("PR-only Socket execution", "github.event_name == 'pull_request'"),
        ("fast-skip notice", "No dependency/security-relevant changes"),
        ("Socket secret gate", "SOCKET_SECURITY_API_KEY"),
        ("full-ci label trigger", 'PR_LABEL" = "full-ci"'),
    ]:
        _require_contains(text, needle, path=path, label=label, violations=violations)


def _check_dependabot(path: Path, text: str, violations: list[str]) -> None:
    for label, needle in [
        ("pull_request_target trigger", "pull_request_target:"),
        ("dependabot actor guard", "dependabot[bot]"),
        ("auto-merge step", "gh pr merge --auto --squash"),
    ]:
        _require_contains(text, needle, path=path, label=label, violations=violations)
    _require_not_contains(
        text,
        "actions/checkout",
        path=path,
        label="checkout of PR code",
        violations=violations,
    )
    _require_not_contains(
        text,
        "gh pr review --approve",
        path=path,
        label="GitHub Actions PR approval",
        violations=violations,
    )


def _check_dependabot_config(
    path: Path,
    text: str,
    violations: list[str],
) -> None:
    github_actions = _yaml_list_item_block(
        text,
        "package-ecosystem",
        "github-actions",
    )
    if github_actions is None:
        violations.append(f"{path}: cannot inspect github-actions update block")
        return

    groups = _yaml_mapping_block(github_actions, "groups")
    if groups is None:
        violations.append(f"{path}: missing GitHub Actions dependency groups")
        return

    codeql = _yaml_mapping_block(groups, "codeql")
    if codeql is None:
        violations.append(f"{path}: missing CodeQL dependency group")
        return

    if not _yaml_string_list_contains(
        codeql,
        "patterns",
        "github/codeql-action/*",
    ):
        violations.append(f"{path}: missing CodeQL action family pattern")


def _check_pack_sync(path: Path, text: str, violations: list[str]) -> None:
    for label, needle in [
        ("weekly schedule", "cron: '17 9 * * 1'"),
        ("manual dispatch", "workflow_dispatch:"),
        ("read-only default workflow token", "contents: read"),
        (
            "pinned Python setup",
            "actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97",
        ),
        ("installer Python version", 'python-version: "3.14"'),
        (
            "canonical pack source",
            "https://github.com/platypeeps/sd-ai-command-pack.git",
        ),
        ("shallow main clone", "git clone --depth 1 --branch main"),
        (
            "canonical forced refresh",
            'python "$RUNNER_TEMP/sd-ai-command-pack/install.py" '
            '"$GITHUB_WORKSPACE" --force',
        ),
        ("generated repository map refresh", "scripts/update_repomix"),
        (
            "pinned create-pull-request action",
            "peter-evans/create-pull-request@5f6978faf089d4d20b00c7766989d076bb2fc7f1",
        ),
        ("stable automation branch", "branch: automation/sd-ai-command-pack-sync"),
        ("stale branch cleanup", "delete-branch: true"),
        (
            "scoped PR token",
            "token: ${{ secrets.SD_AI_COMMAND_PACK_PR_TOKEN }}",
        ),
        (
            "fail-closed scoped token preflight",
            'if [ -z "$SCOPED_TOKEN" ]; then',
        ),
        (
            "scoped token failure diagnostic",
            "SD_AI_COMMAND_PACK_PR_TOKEN is not configured",
        ),
        (
            "scoped auto-merge token",
            "GH_TOKEN: ${{ secrets.SD_AI_COMMAND_PACK_PR_TOKEN }}",
        ),
        ("normal auto-merge gate", "gh pr merge --auto --squash"),
        (
            "PR-only auto-merge condition",
            "steps.create-pr.outputs.pull-request-number != ''",
        ),
    ]:
        _require_contains(text, needle, path=path, label=label, violations=violations)

    for label, needle in [
        ("direct default-branch push", "git push origin main"),
        ("branch-protection bypass", "gh pr merge --admin"),
        ("direct default-branch checkout", "git checkout main && git merge"),
        ("repo-wide workflow token for PR writes", "secrets.GITHUB_TOKEN"),
        ("default token contents write", "contents: write"),
        ("default token pull-request write", "pull-requests: write"),
    ]:
        _require_not_contains(
            text,
            needle,
            path=path,
            label=label,
            violations=violations,
        )


def _check_classifier(path: Path, text: str, violations: list[str]) -> None:
    for label, needle in [
        ("lightweight output", 'emit_output "lightweight_only"'),
        ("app output", 'emit_output "app_required"'),
        ("dependency output", 'emit_output "dependency_changed"'),
        ("workflow output", 'emit_output "workflow_changed"'),
        ("python output", 'emit_output "python_changed"'),
        ("review tooling output", 'emit_output "review_tooling_changed"'),
        ("untracked local files", "git ls-files --others --exclude-standard"),
        ("review tooling full-check script", "scripts/sd-ai-command-pack-full-check.sh"),
        ("review tooling housekeeping script", "scripts/sd-ai-command-pack-housekeeping.sh"),
        ("shared review preflight script", "scripts/sd-ai-command-pack-review-preflight.mjs"),
        ("repo-local review preflight script", "scripts/check-review-preflight.mjs"),
        ("SD AI command-pack scope script", "scripts/sd-ai-command-pack-review-scope.sh"),
        ("SD AI command-pack install audit", "scripts/sd-ai-command-pack-install-audit.py"),
        ("PR body scope guard", "scripts/sd-ai-command-pack-pr-body-scope.py"),
        ("PR body scope tests", "tests/test_pr_body_scope_lint.py"),
        ("command-pack payload classification", ".sd-ai-command-pack/*"),
        ("Trellis audit classification", ".trellis/audit/*"),
        ("command-pack shell library classification", "scripts/sd-ai-command-pack-shell-lib.sh"),
        ("command-pack toolchain classification", "scripts/sd-ai-command-pack-toolchain.sh"),
    ]:
        _require_contains(text, needle, path=path, label=label, violations=violations)


def _check_precommit(path: Path, text: str, violations: list[str]) -> None:
    for label, needle in [
        ("CI review contract hook", "id: ci-review-contract"),
        ("test-resource-cost hook", "id: test-resource-cost"),
        (
            "test-resource-cost hook entry",
            "python tools/check_test_resource_cost.py",
        ),
        ("Copilot instruction contract hook", "id: copilot-instruction-contract"),
        ("Copilot instruction contract entry", "tools/check_copilot_instruction_contract.py"),
        ("PR body scope guard trigger", "sd-ai-command-pack-pr-body-scope"),
        ("PR body scope config trigger", ".sd-ai-command-pack/pr-body-scope"),
        ("PR body scope tests trigger", "tests/test_pr_body_scope_lint"),
        ("Copilot hook pass_filenames", "pass_filenames: false"),
        ("role-name commit-message hook", "id: role-name-commit-message"),
        ("commit-message hook stage", "stages: [commit-msg]"),
        (
            "pre-commit review-tooling shell syntax coverage",
            _REVIEW_TOOLING_SHELL_SYNTAX,
        ),
        (
            "pre-commit scripts Python syntax coverage",
            _PRECOMMIT_PYTHON_SYNTAX_FILES,
        ),
    ]:
        _require_contains(text, needle, path=path, label=label, violations=violations)


def _check_full_check(path: Path, text: str, violations: list[str]) -> None:
    for label, needle in [
        ("review preflight runner", "run_review_preflight"),
        # Pack-script needles are bare names: the vendored full-check resolves
        # pack siblings from its own location (sd-ai-command-pack >= 0.65),
        # while older copies used scripts/-prefixed paths. A bare name matches
        # both shapes.
        ("shared review preflight script", "sd-ai-command-pack-review-preflight.mjs"),
        ("repo-local review preflight script", "scripts/check-review-preflight.mjs"),
        ("SD AI command-pack install audit runner", "run_sd_ai_command_pack_install_audit"),
        ("SD AI command-pack install audit script", "sd-ai-command-pack-install-audit.py"),
        ("SD AI command-pack scope runner", "run_sd_ai_command_pack_scope_check"),
        ("SD AI command-pack scope script", "sd-ai-command-pack-review-scope.sh"),
        ("CI classification report", "run_ci_classification_report"),
        ("package script runner", "SD_AI_COMMAND_PACK_FULL_CHECK_PACKAGE_SCRIPTS"),
        ("Prism fail threshold", "SD_AI_COMMAND_PACK_FULL_CHECK_PRISM_FAIL_ON"),
        ("Prism max findings", "SD_AI_COMMAND_PACK_FULL_CHECK_PRISM_MAX_FINDINGS"),
        ("Prism rules override", "SD_AI_COMMAND_PACK_FULL_CHECK_PRISM_RULES"),
        ("Gito opt-in", "SD_AI_COMMAND_PACK_FULL_CHECK_GITO"),
        ("PR body scope runner", "run_sd_ai_command_pack_pr_body_scope_check"),
        ("PR body scope script", "sd-ai-command-pack-pr-body-scope.py"),
    ]:
        _require_contains(text, needle, path=path, label=label, violations=violations)


def _check_docs(path: Path, text: str, violations: list[str]) -> None:
    for label, needle in [
        ("contract guard mention", "check_ci_review_contract.py"),
        ("PR body scope guard mention", "sd-ai-command-pack-pr-body-scope.py"),
        ("stable aggregate", "stable aggregate"),
        ("lightweight lane", "lightweight readiness"),
        ("quick lane", "quick test"),
        ("full-ci label", "full-ci"),
        ("scheduled command-pack sync", "sd-ai-command-pack-sync.yml"),
        ("Windows collection runner", "windows-latest"),
        ("Windows collection command", "pytest --collect-only -q"),
    ]:
        _require_contains(text, needle, path=path, label=label, violations=violations)


def _check_review_pack_docs(path: Path, text: str, violations: list[str]) -> None:
    for label, needle in [
        ("shared review preflight script", "scripts/sd-ai-command-pack-review-preflight.mjs"),
        ("repo-local review preflight script", "scripts/check-review-preflight.mjs"),
        ("SD AI command-pack scope script", "scripts/sd-ai-command-pack-review-scope.sh"),
        ("install audit script", "scripts/sd-ai-command-pack-install-audit.py"),
        ("installed targets", ".sd-ai-command-pack/installed-targets.txt"),
        ("tooling generated scope body", "Tooling/generated scope:"),
        ("review preflight env", "SD_AI_COMMAND_PACK_FULL_CHECK_REVIEW_PREFLIGHT"),
        ("PR body scope guard", "scripts/sd-ai-command-pack-pr-body-scope.py"),
        ("PR body scope config", ".sd-ai-command-pack/pr-body-scope.json"),
    ]:
        _require_contains(text, needle, path=path, label=label, violations=violations)


def _check_copilot_ci_guidance(
    path: Path, text: str, violations: list[str]
) -> None:
    for label, needle in [
        ("stable aggregate", "CI Result"),
        ("latest stable Python lane", "py3.14 test lane"),
        (
            "latest stable Python policy",
            "Python 3.14 is the only CI-tested version",
        ),
    ]:
        _require_contains(text, needle, path=path, label=label, violations=violations)
    for label, needle in [
        ("legacy required contexts", "`socket` are the required checks"),
        ("stale Python lane", "py3.12 test lane"),
    ]:
        _require_not_contains(
            text, needle, path=path, label=label, violations=violations
        )


def check(root: Path) -> tuple[int, list[str]]:
    texts: dict[str, str] = {}
    errors: list[str] = []
    for key, relative in REQUIRED_FILES.items():
        path = root / relative
        text, error = _read(path)
        if error is not None:
            errors.append(error)
        elif text is not None:
            texts[key] = text

    if errors:
        return 2, errors

    violations: list[str] = []
    checkout_revisions = _shared_pinned_action_revisions(
        root,
        texts,
        "actions/checkout",
        ("ci", "codeql", "pack_sync"),
        violations=violations,
    )
    _check_ci(
        root / REQUIRED_FILES["ci"],
        texts["ci"],
        violations,
        checkout_revision=checkout_revisions.get("ci"),
    )
    _check_codeql(root / REQUIRED_FILES["codeql"], texts["codeql"], violations)
    _check_socket(root / REQUIRED_FILES["ci"], texts["ci"], violations)
    _check_dependabot(root / REQUIRED_FILES["dependabot"], texts["dependabot"], violations)
    _check_dependabot_config(
        root / REQUIRED_FILES["dependabot_config"],
        texts["dependabot_config"],
        violations,
    )
    _check_pack_sync(root / REQUIRED_FILES["pack_sync"], texts["pack_sync"], violations)
    _check_pyproject(root / REQUIRED_FILES["pyproject"], texts["pyproject"], violations)
    _check_precommit(root / REQUIRED_FILES["precommit"], texts["precommit"], violations)
    _check_classifier(root / REQUIRED_FILES["classifier"], texts["classifier"], violations)
    _check_full_check(root / REQUIRED_FILES["full_check"], texts["full_check"], violations)
    _check_docs(root / REQUIRED_FILES["development_cycle"], texts["development_cycle"], violations)
    _check_review_pack_docs(root / REQUIRED_FILES["review_pack"], texts["review_pack"], violations)
    _check_docs(root / REQUIRED_FILES["testing_spec"], texts["testing_spec"], violations)
    _check_copilot_ci_guidance(
        root / REQUIRED_FILES["copilot_ci"], texts["copilot_ci"], violations
    )

    if violations:
        return 1, violations
    return 0, []


def main(argv: list[str]) -> int:
    if len(argv) > 2:
        print("usage: check_ci_review_contract.py [repo-root]", file=sys.stderr)
        return 2

    root = Path(argv[1]) if len(argv) == 2 else Path.cwd()
    root = root.resolve()
    status, messages = check(root)
    if messages:
        print("\n".join(messages), file=sys.stderr)
    return status


if __name__ == "__main__":
    sys.exit(main(sys.argv))
