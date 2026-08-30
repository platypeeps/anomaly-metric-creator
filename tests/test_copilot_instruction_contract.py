"""Acceptance tests for ``tools/check_copilot_instruction_contract.py``."""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "tools" / "check_copilot_instruction_contract.py"

PR_CHECKLIST_HEADINGS = [
    "Scope & description",
    "Validators and schema checks",
    "Doc / docstring sync",
    "Single source of truth",
    "Completeness",
    "Mode / flag combinations",
    "Test path determinism",
    "Performance in hot paths",
    "Action order in user-facing output",
    "Test hygiene",
    "Test resource cost",
    "Cross-platform test guards",
    "Default-behavior changes",
    "CI / workflow / dependency hygiene",
    "Changelog / version impact",
]



def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
    )


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text).lstrip(), encoding="utf-8")


def _write_minimal_contract(root: Path) -> None:
    copilot_checklist = "\n".join(
        f"{index}. **{heading}** - ok"
        for index, heading in enumerate(PR_CHECKLIST_HEADINGS, 1)
    )
    _write(
        root / ".github/instructions/anomaly-metric-creator.instructions.md",
        """
        # Copilot review instructions for anomaly-metric-creator

        Read `docs/spec/amc/backend/index.md`,
        `docs/spec/amc/backend/testing-quality.md`, and
        `docs/spec/amc/backend/documentation-review.md`.

        ## Local-first review cadence

        Keep `tools/check_ci_review_contract.py`,
        `tools/check_copilot_instruction_contract.py`, and
        `scripts/classify-ci-changes.sh` in lockstep. The required
        branch-protection context `CI Result` aggregates the jobs, and the
        canonical CLI surface is the subcommand set plus `--emit`.

        ## Review-cycle reduction

        Before posting repeated comments, check newer commits or tests and use
        one grouped comment per helper. Review duplicate entries, missing
        counterpart entries, index-only/file-only rows, invalid encoding, empty
        values, flag-looking values, wildcard namespaces, invalid owner/repo
        slugs, missing paths, and unintended whole-repo scans. Leave one
        top-level scope comment for PR-body omissions and ask for the matching
        Automation scope:, CI/review scope:, Tooling/generated scope:,
        Docs/user-facing scope:, or Runtime/server scope: section.

        ## Pre-PR checklist headings (canonical in Trellis)
        """,
    )
    with (root / ".github/instructions/anomaly-metric-creator.instructions.md").open(
        "a",
        encoding="utf-8",
    ) as handle:
        handle.write(f"\n{copilot_checklist}\n")
    _write(
        root / ".github/PULL_REQUEST_TEMPLATE.md",
        """
        ## Summary

        ## Test plan

        ## Pre-PR checklist
        """,
    )
    with (root / ".github/PULL_REQUEST_TEMPLATE.md").open("a", encoding="utf-8") as handle:
        for heading in PR_CHECKLIST_HEADINGS:
            handle.write(f"\n- [ ] {heading}")
        handle.write("\n")

    _write(
        root / "docs/spec/amc/backend/testing-quality.md",
        """
        scope and description, validators/schema, docs/docstrings,
        single source of truth, completeness, mode/flag combinations,
        deterministic test paths, hot-path performance, user-facing output
        order, test hygiene, test resource cost, cross-platform guards,
        default-behavior changes, CI/workflow/dependency hygiene, and
        changelog/version impact.
        """,
    )
    _write(
        root / "docs/spec/amc/backend/documentation-review.md",
        """
        `.github/instructions/anomaly-metric-creator.instructions.md`
        `tools/check_copilot_instruction_contract.py`
        """,
    )
    _write(
        root / "scripts/check-review-preflight.mjs",
        """
        python tools/check_ci_review_contract.py
        python tools/check_copilot_instruction_contract.py
        pytest tests/test_copilot_instruction_contract.py
        """,
    )


def test_real_repo_contract_is_clean() -> None:
    result = _run(str(REPO_ROOT))

    assert result.returncode == 0, result.stderr


def test_minimal_contract_fixture_passes(tmp_path: Path) -> None:
    _write_minimal_contract(tmp_path)

    result = _run(str(tmp_path))

    assert result.returncode == 0, result.stderr


def test_missing_review_cycle_anchor_fails(tmp_path: Path) -> None:
    _write_minimal_contract(tmp_path)
    instructions = tmp_path / ".github/instructions/anomaly-metric-creator.instructions.md"
    instructions.write_text(
        instructions.read_text(encoding="utf-8").replace(
            "## Review-cycle reduction",
            "## Review notes",
        ),
        encoding="utf-8",
    )

    result = _run(str(tmp_path))

    assert result.returncode == 1
    assert "review-cycle reduction heading" in result.stderr


def test_reintroduced_phantom_flag_fails(tmp_path: Path) -> None:
    _write_minimal_contract(tmp_path)
    instructions = tmp_path / ".github/instructions/anomaly-metric-creator.instructions.md"
    with instructions.open("a", encoding="utf-8") as handle:
        handle.write("\nGate against `--topology-mode` combinations.\n")

    result = _run(str(tmp_path))

    assert result.returncode == 1
    assert "removed --topology-mode flag must not appear" in result.stderr


def test_pr_template_heading_mismatch_fails(tmp_path: Path) -> None:
    _write_minimal_contract(tmp_path)
    template = tmp_path / ".github/PULL_REQUEST_TEMPLATE.md"
    template.write_text(
        template.read_text(encoding="utf-8").replace(
            "- [ ] Scope & description",
            "- [ ] Scope summary",
        ),
        encoding="utf-8",
    )

    result = _run(str(tmp_path))

    assert result.returncode == 1
    assert "Pre-PR checklist headings do not match" in result.stderr


def test_missing_review_preflight_wiring_fails(tmp_path: Path) -> None:
    _write_minimal_contract(tmp_path)
    review_preflight = tmp_path / "scripts/check-review-preflight.mjs"
    review_preflight.write_text(
        review_preflight.read_text(encoding="utf-8").replace(
            "tests/test_copilot_instruction_contract.py",
            "tests/test_ci_review_contract.py",
        ),
        encoding="utf-8",
    )

    result = _run(str(tmp_path))

    assert result.returncode == 1
    assert "Copilot instruction contract tests" in result.stderr


def test_missing_repo_file_exits_two(tmp_path: Path) -> None:
    _write_minimal_contract(tmp_path)
    (tmp_path / ".github/instructions/anomaly-metric-creator.instructions.md").unlink()

    result = _run(str(tmp_path))

    assert result.returncode == 2
    assert "cannot read" in result.stderr
