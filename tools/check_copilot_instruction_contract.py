#!/usr/bin/env python3
"""Guard the mechanical Copilot review-instruction contract.

Copilot instructions intentionally contain reviewer judgment that cannot be
fully tested. This checker locks the mechanical pieces that should not drift:
the canonical Trellis routing, checklist heading lockstep, review-cycle
reduction anchors, generated/copied adapter policy, copied path existence, and
local preflight wiring.

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
    "copilot": Path(".github/instructions/anomaly-metric-creator.instructions.md"),
    "pr_template": Path(".github/PULL_REQUEST_TEMPLATE.md"),
    "testing_spec": Path("docs/spec/amc/backend/testing-quality.md"),
    "documentation_spec": Path("docs/spec/amc/backend/documentation-review.md"),
    "review_preflight": Path("scripts/check-review-preflight.mjs"),
}

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

TESTING_SPEC_HEADING_FRAGMENTS = [
    "scope and description",
    "validators/schema",
    "docs/docstrings",
    "single source of truth",
    "completeness",
    "mode/flag combinations",
    "deterministic test paths",
    "hot-path performance",
    "user-facing output order",
    "test hygiene",
    "test resource cost",
    "cross-platform guards",
    "default-behavior changes",
    "ci/workflow/dependency hygiene",
    "changelog/version impact",
]

COPILOT_REQUIRED_NEEDLES = [
    ("testing spec", "docs/spec/amc/backend/testing-quality.md"),
    ("documentation spec", "docs/spec/amc/backend/documentation-review.md"),
    ("local-first cadence heading", "## Local-first review cadence"),
    ("review-cycle reduction heading", "## Review-cycle reduction"),
    ("newer commit/test de-duplication", "newer commits or tests"),
    ("grouped sibling comments", "one grouped comment per"),
    ("top-level scope comment", "one top-level scope comment"),
    ("CI review contract guard", "tools/check_ci_review_contract.py"),
    ("Copilot instruction contract guard", "tools/check_copilot_instruction_contract.py"),
    ("automation scope section", "Automation scope:"),
    ("CI review scope section", "CI/review scope:"),
    ("tooling generated scope section", "Tooling/generated scope:"),
    ("docs user-facing scope section", "Docs/user-facing scope:"),
    ("runtime server scope section", "Runtime/server scope:"),
    ("canonical CLI surface anchor", "canonical CLI surface"),
    ("required branch-protection context name", "CI Result"),
]

# Removed CLI flags that must never reappear in the Copilot instructions as
# current surface. Each was deleted at a CLI flag day; the instructions file
# now names the canonical subcommand/flag surface instead. Re-introducing any
# of these tokens (the drift this anchor guards against) fails the contract.
COPILOT_FORBIDDEN_NEEDLES = [
    ("removed --topology-mode flag", "--topology-mode"),
    ("removed --validate-output flag", "--validate-output"),
    ("removed --validate-warn flag", "--validate-warn"),
    ("removed --combine-only flag", "--combine-only"),
    ("removed --emit-selection flag", "--emit-selection"),
]

EDGE_CASE_NEEDLES = [
    "duplicate entries",
    "missing counterpart entries",
    "index-only/file-only rows",
    "invalid encoding",
    "empty values",
    "flag-looking values",
    "wildcard namespaces",
    "invalid owner/repo slugs",
    "missing paths",
    "unintended whole-repo scans",
]


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


def _require_absent(
    text: str,
    needle: str,
    *,
    path: Path,
    label: str,
    violations: list[str],
) -> None:
    if needle in text:
        violations.append(f"{path}: {label} must not appear: {needle!r}")


def _section_text(text: str, heading: str) -> str:
    lines = text.splitlines()
    collected: list[str] = []
    in_section = False
    for line in lines:
        if line.startswith("## "):
            if in_section:
                break
            in_section = line.strip() == heading
            continue
        if in_section:
            collected.append(line)
    return "\n".join(collected)


def _extract_template_headings(text: str) -> list[str]:
    section = _section_text(text, "## Pre-PR checklist")
    headings: list[str] = []
    for line in section.splitlines():
        match = re.match(r"- \[ \] (.+)$", line.strip())
        if match:
            headings.append(match.group(1).strip())
    return headings


def _extract_copilot_headings(text: str) -> list[str]:
    section = _section_text(text, "## Pre-PR checklist headings (canonical in Trellis)")
    headings: list[str] = []
    for line in section.splitlines():
        match = re.match(r"\d+\.\s+\*\*(.+?)\*\*", line.strip())
        if match:
            headings.append(match.group(1).strip())
    return headings


def _check_checklist_headings(
    root: Path,
    texts: dict[str, str],
    violations: list[str],
) -> None:
    template_headings = _extract_template_headings(texts["pr_template"])
    if template_headings != PR_CHECKLIST_HEADINGS:
        violations.append(
            f"{root / REQUIRED_FILES['pr_template']}: Pre-PR checklist headings "
            f"do not match canonical headings: {template_headings!r}"
        )

    copilot_headings = _extract_copilot_headings(texts["copilot"])
    if copilot_headings != PR_CHECKLIST_HEADINGS:
        violations.append(
            f"{root / REQUIRED_FILES['copilot']}: Copilot checklist headings "
            f"do not match canonical headings: {copilot_headings!r}"
        )

    testing_spec = texts["testing_spec"].lower()
    for fragment in TESTING_SPEC_HEADING_FRAGMENTS:
        _require_contains(
            testing_spec,
            fragment,
            path=root / REQUIRED_FILES["testing_spec"],
            label="review checklist heading",
            violations=violations,
        )


def _check_copilot_text(root: Path, text: str, violations: list[str]) -> None:
    path = root / REQUIRED_FILES["copilot"]
    for label, needle in COPILOT_REQUIRED_NEEDLES:
        _require_contains(text, needle, path=path, label=label, violations=violations)
    for label, needle in COPILOT_FORBIDDEN_NEEDLES:
        _require_absent(text, needle, path=path, label=label, violations=violations)
    for needle in EDGE_CASE_NEEDLES:
        _require_contains(
            text,
            needle,
            path=path,
            label="review-cycle edge-case matrix item",
            violations=violations,
        )


def _check_review_preflight_wiring(root: Path, text: str, violations: list[str]) -> None:
    path = root / REQUIRED_FILES["review_preflight"]
    # The full-check wiring assertions that used to live beside these were
    # dropped with the thin conversion: they read the pack's own full-check
    # script, which is no longer part of this repository.
    for label, needle in [
        ("CI review contract guard", "tools/check_ci_review_contract.py"),
        ("Copilot instruction contract guard", "tools/check_copilot_instruction_contract.py"),
        ("Copilot instruction contract tests", "tests/test_copilot_instruction_contract.py"),
    ]:
        _require_contains(text, needle, path=path, label=label, violations=violations)


def _check_documentation_spec(root: Path, text: str, violations: list[str]) -> None:
    path = root / REQUIRED_FILES["documentation_spec"]
    for label, needle in [
        ("Copilot instructions source", ".github/instructions/anomaly-metric-creator.instructions.md"),
        ("Copilot contract guard source", "tools/check_copilot_instruction_contract.py"),
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
    _check_copilot_text(root, texts["copilot"], violations)
    _check_checklist_headings(root, texts, violations)
    _check_review_preflight_wiring(root, texts["review_preflight"], violations)
    _check_documentation_spec(root, texts["documentation_spec"], violations)

    if violations:
        return 1, violations
    return 0, []


def main(argv: list[str]) -> int:
    if len(argv) > 2:
        print("usage: check_copilot_instruction_contract.py [repo-root]", file=sys.stderr)
        return 2

    root = Path(argv[1]) if len(argv) == 2 else Path.cwd()
    root = root.resolve()
    status, messages = check(root)
    if messages:
        print("\n".join(messages), file=sys.stderr)
    return status


if __name__ == "__main__":
    sys.exit(main(sys.argv))
