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
from dataclasses import dataclass
from pathlib import Path


REQUIRED_FILES = {
    "copilot": Path(".github/instructions/anomaly-metric-creator.instructions.md"),
    "pr_template": Path(".github/PULL_REQUEST_TEMPLATE.md"),
    "testing_spec": Path(".trellis/spec/amc/backend/testing-quality.md"),
    "documentation_spec": Path(".trellis/spec/amc/backend/documentation-review.md"),
    "full_check": Path("scripts/sd-ai-command-pack-full-check.sh"),
    "shared_review_preflight": Path("scripts/sd-ai-command-pack-review-preflight.mjs"),
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
    ("Trellis spec index", ".trellis/spec/amc/backend/index.md"),
    ("testing spec", ".trellis/spec/amc/backend/testing-quality.md"),
    ("documentation spec", ".trellis/spec/amc/backend/documentation-review.md"),
    ("local-first cadence heading", "## Local-first review cadence"),
    ("review-cycle reduction heading", "## Review-cycle reduction"),
    ("newer commit/test de-duplication", "newer commits or tests"),
    ("grouped sibling comments", "one grouped comment per"),
    ("top-level scope comment", "one top-level scope comment"),
    ("copied adapter policy heading", "## Generated and copied adapter files"),
    ("SD command pack source", "platypeeps/sd-ai-command-pack"),
    ("line-level copied-file review skip", "Do not spend review comments on line-level"),
    ("canonical source review target", "Review the canonical source"),
    ("local wiring review target", "local wiring"),
    ("shell syntax review target", "shell syntax checks"),
    ("Trellis contradiction exception", "contradicts the canonical Trellis specs"),
    ("CI review contract guard", "tools/check_ci_review_contract.py"),
    ("Copilot instruction contract guard", "tools/check_copilot_instruction_contract.py"),
    ("PR body scope guard", "scripts/sd-ai-command-pack-pr-body-scope.py"),
    ("PR body scope config", ".sd-ai-command-pack/pr-body-scope.json"),
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


@dataclass(frozen=True)
class CopiedPath:
    label: str
    instruction_needle: str
    path: str
    glob: bool = False
    directory: bool = False


COPIED_PATHS = [
    CopiedPath(
        "Trellis GitHub agents",
        ".github/agents/trellis-*.agent.md",
        ".github/agents/trellis-*.agent.md",
        glob=True,
    ),
    CopiedPath(
        "Trellis GitHub skills",
        ".github/skills/trellis-*/**",
        ".github/skills/trellis-*/**",
        glob=True,
    ),
    CopiedPath(
        "Copilot hooks manifest",
        ".github/copilot/hooks.json",
        ".github/copilot/hooks.json",
    ),
    CopiedPath(
        "Copilot hook adapters",
        ".github/copilot/hooks/**",
        ".github/copilot/hooks/**",
        glob=True,
    ),
    CopiedPath(
        "Trellis GitHub hook config",
        ".github/hooks/trellis.json",
        ".github/hooks/trellis.json",
    ),
    CopiedPath(
        "GitHub prompt adapters",
        ".github/prompts/",
        ".github/prompts",
        directory=True,
    ),
    CopiedPath(
        "SD Codex skill wrappers",
        ".agents/skills/sd-*/**",
        ".agents/skills/sd-*/**",
        glob=True,
    ),
    CopiedPath(
        "SD GitHub prompt copies",
        ".github/prompts/sd-*.prompt.md",
        ".github/prompts/sd-*.prompt.md",
        glob=True,
    ),
    CopiedPath(
        "SD Gemini command copies",
        ".gemini/commands/sd/**",
        ".gemini/commands/sd/**",
        glob=True,
    ),
    CopiedPath(
        "SD OpenCode command copies",
        ".opencode/commands/sd-*.md",
        ".opencode/commands/sd-*.md",
        glob=True,
    ),
    CopiedPath(
        "SD installed target list",
        ".sd-ai-command-pack/installed-targets.txt",
        ".sd-ai-command-pack/installed-targets.txt",
    ),
    CopiedPath(
        "SD command-pack docs",
        "docs/SD_AI_COMMAND_PACK.md",
        "docs/SD_AI_COMMAND_PACK.md",
    ),
    CopiedPath(
        "SD AI command-pack scope script",
        "scripts/sd-ai-command-pack-review-scope.sh",
        "scripts/sd-ai-command-pack-review-scope.sh",
    ),
    CopiedPath(
        "SD AI command-pack install audit script",
        "scripts/sd-ai-command-pack-install-audit.py",
        "scripts/sd-ai-command-pack-install-audit.py",
    ),
    CopiedPath(
        "SD AI command-pack PR body scope script",
        "scripts/sd-ai-command-pack-pr-body-scope.py",
        "scripts/sd-ai-command-pack-pr-body-scope.py",
    ),
    CopiedPath(
        "SD AI command-pack review learnings script",
        "scripts/sd-ai-command-pack-review-learnings.py",
        "scripts/sd-ai-command-pack-review-learnings.py",
    ),
    CopiedPath(
        "SD AI command-pack full-check script",
        "scripts/sd-ai-command-pack-full-check.sh",
        "scripts/sd-ai-command-pack-full-check.sh",
    ),
    CopiedPath(
        "SD AI command-pack housekeeping script",
        "scripts/sd-ai-command-pack-housekeeping.sh",
        "scripts/sd-ai-command-pack-housekeeping.sh",
    ),
    CopiedPath(
        "SD AI command-pack update-spec KB script",
        "scripts/sd-ai-command-pack-update-spec-kb.py",
        "scripts/sd-ai-command-pack-update-spec-kb.py",
    ),
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


def _glob_has_file(root: Path, pattern: str) -> bool:
    if pattern.endswith("/**"):
        pattern = f"{pattern}/*"
    return any(path.is_file() for path in root.glob(pattern))


def _check_copied_paths(root: Path, copilot_text: str, violations: list[str]) -> None:
    copilot_path = root / REQUIRED_FILES["copilot"]
    for copied in COPIED_PATHS:
        _require_contains(
            copilot_text,
            copied.instruction_needle,
            path=copilot_path,
            label=f"{copied.label} instruction path",
            violations=violations,
        )

        target = root / copied.path
        if copied.glob:
            if not _glob_has_file(root, copied.path):
                violations.append(
                    f"{root}: missing copied path group for "
                    f"{copied.label}: {copied.path}"
                )
        elif copied.directory:
            if not target.is_dir():
                violations.append(f"{root}: missing copied directory for {copied.label}: {copied.path}")
        elif not target.is_file():
            violations.append(f"{root}: missing copied file for {copied.label}: {copied.path}")


def _check_full_check_wiring(root: Path, text: str, violations: list[str]) -> None:
    path = root / REQUIRED_FILES["full_check"]
    # Pack-script needles are bare names: newer vendored full-check copies
    # (sd-ai-command-pack >= 0.65) resolve pack siblings from their own
    # location, while the currently vendored copy still uses
    # scripts/-prefixed paths. A bare name matches both shapes.
    # scripts/check-review-preflight.mjs stays prefixed - it is genuinely
    # repo-local.
    for label, needle in [
        ("review preflight runner", "run_review_preflight"),
        ("shared review preflight script", "sd-ai-command-pack-review-preflight.mjs"),
        ("repo-local review preflight script", "scripts/check-review-preflight.mjs"),
        ("SD AI command-pack install audit runner", "run_sd_ai_command_pack_install_audit"),
        ("SD AI command-pack install audit script", "sd-ai-command-pack-install-audit.py"),
        ("SD AI command-pack scope runner", "run_sd_ai_command_pack_scope_check"),
        ("SD AI command-pack scope script", "sd-ai-command-pack-review-scope.sh"),
        ("PR body scope runner", "run_sd_ai_command_pack_pr_body_scope_check"),
        ("PR body scope script", "sd-ai-command-pack-pr-body-scope.py"),
    ]:
        _require_contains(text, needle, path=path, label=label, violations=violations)


def _check_review_preflight_wiring(root: Path, text: str, violations: list[str]) -> None:
    path = root / REQUIRED_FILES["review_preflight"]
    for label, needle in [
        ("CI review contract guard", "tools/check_ci_review_contract.py"),
        ("Copilot instruction contract guard", "tools/check_copilot_instruction_contract.py"),
        ("PR body scope guard", "scripts/sd-ai-command-pack-pr-body-scope.py"),
        ("Copilot instruction contract tests", "tests/test_copilot_instruction_contract.py"),
        ("PR body scope tests", "tests/test_pr_body_scope_lint.py"),
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
    _check_copied_paths(root, texts["copilot"], violations)
    _check_full_check_wiring(root, texts["full_check"], violations)
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
