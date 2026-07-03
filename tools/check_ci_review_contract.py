#!/usr/bin/env python3
"""Guard the local review and CI cadence contract.

The workflow cadence is intentionally spread across a few files:

* ``scripts/classify-ci-changes.sh`` owns path classification.
* ``.github/workflows/ci.yml`` chooses the lightweight, quick, or full lane.
* CodeQL, Socket, and Dependabot workflows follow the same review-economy
  policy.
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

import sys
from pathlib import Path


REQUIRED_FILES = {
    "ci": Path(".github/workflows/ci.yml"),
    "codeql": Path(".github/workflows/codeql.yml"),
    "socket": Path(".github/workflows/socket.yml"),
    "dependabot": Path(".github/workflows/dependabot-auto-merge.yml"),
    "precommit": Path(".pre-commit-config.yaml"),
    "classifier": Path("scripts/classify-ci-changes.sh"),
    "full_check": Path("scripts/sd-ai-command-pack-full-check.sh"),
    "development_cycle": Path("docs/DEVELOPMENT_CYCLE.md"),
    "review_pack": Path("docs/SD_AI_COMMAND_PACK.md"),
    "testing_spec": Path(".trellis/spec/amc/backend/testing-quality.md"),
}


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


def _check_ci(path: Path, text: str, violations: list[str]) -> None:
    for label, needle in [
        ("change classifier job", "changes:"),
        ("lightweight lane", "lightweight_readiness:"),
        ("quick lane", "quick_check:"),
        ("full matrix lane", "test_matrix:"),
        ("stable aggregate", "  test:"),
        (
            "aggregate lane dependencies",
            "needs: [changes, lightweight_readiness, quick_check, test_matrix]",
        ),
        (
            "classifier invocation",
            "bash scripts/classify-ci-changes.sh --github-output changed-files.txt",
        ),
        ("full-ci trigger", "full-ci"),
        ("full-ci output", "full_ci_requested"),
        (
            "full-ci output bracket expression",
            "steps['full-ci'].outputs.full_ci_requested",
        ),
        ("lightweight result text", "selected lane: lightweight readiness"),
        ("quick result text", "selected lane: quick test"),
        ("full result text", "selected lane: full matrix"),
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
        (
            "Copilot instruction contract test coverage",
            "tests/test_copilot_instruction_contract.py",
        ),
        (
            "PR body scope test coverage",
            "tests/test_pr_body_scope_lint.py",
        ),
        (
            "auto-merge enabled PR event",
            "types: [opened, synchronize, reopened, ready_for_review, labeled, auto_merge_enabled]",
        ),
        (
            "auto-merge synchronize gate",
            "github.event.pull_request.auto_merge != null",
        ),
        (
            "auto-merge enabled full-ci trigger",
            "auto_merge_enabled)",
        ),
        (
            "per-commit push concurrency",
            "group: ci-${{ github.event_name == 'push' && github.sha || github.ref }}",
        ),
    ]:
        _require_contains(text, needle, path=path, label=label, violations=violations)
    _require_not_contains(
        text,
        "steps.full-ci.outputs.full_ci_requested",
        path=path,
        label="full-ci output dot expression",
        violations=violations,
    )


def _check_codeql(path: Path, text: str, violations: list[str]) -> None:
    for label, needle in [
        (
            "required-context pull request events",
            "types: [opened, synchronize, reopened, ready_for_review, labeled]",
        ),
        ("concurrency", "concurrency:"),
        ("synchronize trigger", "github.event.action == 'synchronize'"),
        ("full-ci label trigger", "github.event.label.name == 'full-ci'"),
    ]:
        _require_contains(text, needle, path=path, label=label, violations=violations)


def _check_socket(path: Path, text: str, violations: list[str]) -> None:
    for label, needle in [
        (
            "visible PR check events",
            "types: [opened, synchronize, reopened, ready_for_review, labeled]",
        ),
        (
            "classifier invocation",
            "bash scripts/classify-ci-changes.sh --github-output changed-files.txt",
        ),
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
        ("SD AI command-pack review-local script", "scripts/sd-ai-command-pack-review-local.sh"),
        ("SD AI command-pack install audit", "scripts/sd-ai-command-pack-install-audit.py"),
        ("PR body scope guard", "scripts/sd-ai-command-pack-pr-body-scope.py"),
        ("PR body scope config", ".sd-ai-command-pack/pr-body-scope.json"),
        ("PR body scope tests", "tests/test_pr_body_scope_lint.py"),
    ]:
        _require_contains(text, needle, path=path, label=label, violations=violations)


def _check_precommit(path: Path, text: str, violations: list[str]) -> None:
    for label, needle in [
        ("CI review contract hook", "id: ci-review-contract"),
        ("Copilot instruction contract hook", "id: copilot-instruction-contract"),
        ("Copilot instruction contract entry", "tools/check_copilot_instruction_contract.py"),
        ("PR body scope guard trigger", "sd-ai-command-pack-pr-body-scope"),
        ("PR body scope config trigger", ".sd-ai-command-pack/pr-body-scope"),
        ("PR body scope tests trigger", "tests/test_pr_body_scope_lint"),
        ("Copilot hook pass_filenames", "pass_filenames: false"),
    ]:
        _require_contains(text, needle, path=path, label=label, violations=violations)


def _check_full_check(path: Path, text: str, violations: list[str]) -> None:
    for label, needle in [
        ("review preflight runner", "run_review_preflight"),
        ("shared review preflight script", "scripts/sd-ai-command-pack-review-preflight.mjs"),
        ("repo-local review preflight script", "scripts/check-review-preflight.mjs"),
        ("SD AI command-pack install audit runner", "run_sd_ai_command_pack_install_audit"),
        ("SD AI command-pack install audit script", "scripts/sd-ai-command-pack-install-audit.py"),
        ("SD AI command-pack scope runner", "run_sd_ai_command_pack_scope_check"),
        ("SD AI command-pack scope script", "scripts/sd-ai-command-pack-review-scope.sh"),
        ("CI classification report", "run_ci_classification_report"),
        ("package script runner", "SD_AI_COMMAND_PACK_FULL_CHECK_PACKAGE_SCRIPTS"),
        ("Prism fail threshold", "SD_AI_COMMAND_PACK_FULL_CHECK_PRISM_FAIL_ON"),
        ("Prism max findings", "SD_AI_COMMAND_PACK_FULL_CHECK_PRISM_MAX_FINDINGS"),
        ("Prism rules override", "SD_AI_COMMAND_PACK_FULL_CHECK_PRISM_RULES"),
        ("Gito opt-in", "SD_AI_COMMAND_PACK_FULL_CHECK_GITO"),
        ("PR body scope runner", "run_sd_ai_command_pack_pr_body_scope_check"),
        ("PR body scope script", "scripts/sd-ai-command-pack-pr-body-scope.py"),
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
    _check_ci(root / REQUIRED_FILES["ci"], texts["ci"], violations)
    _check_codeql(root / REQUIRED_FILES["codeql"], texts["codeql"], violations)
    _check_socket(root / REQUIRED_FILES["socket"], texts["socket"], violations)
    _check_dependabot(root / REQUIRED_FILES["dependabot"], texts["dependabot"], violations)
    _check_precommit(root / REQUIRED_FILES["precommit"], texts["precommit"], violations)
    _check_classifier(root / REQUIRED_FILES["classifier"], texts["classifier"], violations)
    _check_full_check(root / REQUIRED_FILES["full_check"], texts["full_check"], violations)
    _check_docs(root / REQUIRED_FILES["development_cycle"], texts["development_cycle"], violations)
    _check_review_pack_docs(root / REQUIRED_FILES["review_pack"], texts["review_pack"], violations)
    _check_docs(root / REQUIRED_FILES["testing_spec"], texts["testing_spec"], violations)

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
