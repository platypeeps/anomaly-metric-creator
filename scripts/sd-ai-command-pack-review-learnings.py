#!/usr/bin/env python3
"""Detect and record repo-specific review learnings.

This pack-owned helper keeps repeated review feedback out of slow remote review
loops. It scans local diffs for common mechanical review-cycle patterns,
optionally summarizes recent Copilot review comments, and can update a
repo-local markdown file with a managed learnings block.
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import functools
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from subprocess import CompletedProcess
from typing import Any

from sd_ai_command_pack_lib import (
    CommandError,
)
from sd_ai_command_pack_lib import (
    run_gh as run_gh_command,
)
from sd_ai_command_pack_lib import (
    run_git as run_git_command,
)

DEFAULT_TARGET = Path("docs/review-learnings.md")
MANAGED_START = "<!-- sd-review-learnings:start -->"
MANAGED_END = "<!-- sd-review-learnings:end -->"
COPILOT_LOGIN = "copilot-pull-request-reviewer"
DEFAULT_ENV_PREFIXES = ("TRELLIS", "PRISM", "GITO", "SD", "GH", "AWS", "CI")

CATEGORY_PORTABILITY = "portability"
CATEGORY_INTERFACE = "interface"
CATEGORY_REVIEW_SCAFFOLDING = "review-scaffolding"
CATEGORY_PR_TEMPLATE = "pr-template"
CATEGORY_COPILOT_INSTRUCTIONS = "copilot-instructions"

SIGNAL_TASK_METADATA = "task-metadata"
SIGNAL_BOUNDARY_VALIDATION = "boundary-validation"
SIGNAL_CONTRACT_DOCUMENTATION = "contract-documentation-drift"
SIGNAL_GENERATED_SURFACES = "generated-surfaces"
SIGNAL_REVIEWER_TEST_HARNESS = "reviewer-test-harness-quality"
SIGNAL_OTHER = "other"

MAX_HISTORICAL_CLUSTERS = 5
MAX_CLUSTER_SIGNATURES = 4
MAX_CLUSTER_PRS = 8
MAX_CLUSTER_PATH_FAMILIES = 6
MAX_CLUSTER_EXAMPLES = 3
MIN_PREVENTIVE_ACTION_COUNT = 2

SIGNAL_CATEGORY_LABELS = {
    SIGNAL_TASK_METADATA: "Task metadata",
    SIGNAL_BOUNDARY_VALIDATION: "Boundary validation",
    SIGNAL_CONTRACT_DOCUMENTATION: "Contract/documentation drift",
    SIGNAL_GENERATED_SURFACES: "Generated surfaces",
    SIGNAL_REVIEWER_TEST_HARNESS: "Reviewer/test harness quality",
    SIGNAL_OTHER: "Other recurring signals",
}

GENERATED_SIGNAL_PATH_PREFIXES = (
    "templates/",
    ".agents/",
    ".commands/",
    ".claude/",
    ".cursor/",
    ".gemini/",
    ".github/agents/",
    ".github/copilot/",
    ".github/hooks/",
    ".github/prompts/",
    ".opencode/",
    ".agent/",
    ".codebuddy/",
    ".devin/",
    ".factory/",
    ".kilocode/",
    ".kiro/",
    ".pi/",
    ".qoder/",
    ".reasonix/",
    ".trae/",
    ".zcode/",
    ".sd-ai-command-pack/",
    ".prism/",
    ".gito/",
    "scripts/sd-ai-command-pack-",
    "scripts/sd_ai_command_pack_",
)
GENERATED_SIGNAL_PATHS = {
    ".github/copilot-instructions.md",
    ".github/pull_request_template.md",
    "docs/sd_ai_command_pack.md",
}

SIGNAL_CATEGORY_PATTERNS = (
    (
        SIGNAL_TASK_METADATA,
        (
            "task metadata",
            "task.json",
            "base branch",
            "task status",
            "task id",
            "task directory",
            "assignee",
        ),
    ),
    (
        SIGNAL_BOUNDARY_VALIDATION,
        (
            "boundary",
            "fail closed",
            "failure matrix",
            "path traversal",
            "untrusted",
            "validate",
            "validation",
            "malformed",
            "symlink",
            "allowlist",
        ),
    ),
    (
        SIGNAL_GENERATED_SURFACES,
        (
            "generated surface",
            "generated file",
            "generated copy",
            "copied surface",
            "template parity",
            "root/template",
            "installed mirror",
            "source of truth",
            "keep in sync",
        ),
    ),
    (
        SIGNAL_REVIEWER_TEST_HARNESS,
        (
            "test harness",
            "review harness",
            "reviewer quality",
            "false positive",
            "tautological",
            "fixture",
            "mock",
            "coverage",
            "assertion",
        ),
    ),
    (
        SIGNAL_CONTRACT_DOCUMENTATION,
        (
            "contract",
            "documentation",
            "terminology",
            "wording",
            "readme",
            "help text",
            "docs drift",
            "documented behavior",
        ),
    ),
)

SIGNAL_PREVENTIVE_ACTIONS = {
    SIGNAL_TASK_METADATA: (
        "Add a deterministic task-metadata validation gate before implementation "
        "or publication."
    ),
    SIGNAL_BOUNDARY_VALIDATION: (
        "Add boundary and failure-matrix fixtures for externally derived paths "
        "and states."
    ),
    SIGNAL_CONTRACT_DOCUMENTATION: (
        "Add contract terminology checks that keep documentation and help text "
        "aligned with shipped behavior."
    ),
    SIGNAL_GENERATED_SURFACES: (
        "Extend source-to-generated parity checks for every affected shipped surface."
    ),
    SIGNAL_REVIEWER_TEST_HARNESS: (
        "Strengthen reviewer and test-harness fixtures so the reported failure "
        "mode is exercised directly."
    ),
}

REQUIRED_PR_TEMPLATE_PHRASES = (
    "## Scope and surfaces",
    "Primary surfaces touched",
    "Generated/copied surfaces",
    "Verification before Copilot review",
)
RECOMMENDED_COPILOT_PHRASES = (
    "current, non-outdated unresolved",
    "stale or outdated review threads",
    "copied or generated",
)
TRELLIS_JOURNAL_PLACEHOLDERS = (
    "(Add details)",
    "(Add test results)",
)

_FILE_HEADER_RE = re.compile(r"^\+\+\+ b/(.+)$")
_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")
_NEGATIVE_ARRAY_OFFSET_RE = re.compile(
    r"\$\{(?:[^}\n\[]+\[[^\]\n]*\]\s*:\s*-\d+|[^}\n\[]+\[\s*-\d+\]|[@*]\s*:\s*-\d+)"
)
_GREP_EXPECTED_EMPTY_RE = re.compile(r"\bgrep\b[^#\n]*\s-[A-Za-z]*v[A-Za-z]*\b")
_CLASSIFY_WITH_FILES_RE = re.compile(r"classify-ci-changes\.sh\b.*\$\{files\[@\]\}")
_CLASSIFY_WITH_DELIMITER_RE = re.compile(
    r"classify-ci-changes\.sh\b.*\s--\s+['\"]?\$\{files\[@\]\}"
)
_ALL_ZERO_GREP_RE = re.compile(r"grep\b[^#\n]*-qv\b[^#\n]*\^0\*\$")
_LONG_OPTION_CASE_RE = re.compile(r"^\s*(--[a-z][a-z0-9-]*)\)")


def default_text_file_mode(destination: Path) -> int:
    if destination.exists():
        return destination.stat().st_mode & 0o777
    current_umask = os.umask(0)
    try:
        return 0o666 & ~current_umask
    finally:
        os.umask(current_umask)


def atomic_write_text(
    destination: Path,
    content: str,
    *,
    errors: str = "strict",
) -> None:
    if destination.is_symlink():
        raise OSError("target is a symlink")
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(content.encode("utf-8", errors=errors))
            temporary.flush()
            os.fsync(temporary.fileno())
        os.chmod(temporary_path, default_text_file_mode(destination))
        os.replace(temporary_path, destination)
        temporary_path = None
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass


@dataclasses.dataclass(frozen=True)
class AddedLine:
    path: str
    lineno: int
    content: str


def _neutralize_managed_markers(text: str) -> str:
    text = text.replace(MANAGED_START, "[managed-start marker removed]")
    return text.replace(MANAGED_END, "[managed-end marker removed]")


@dataclasses.dataclass(frozen=True)
class Finding:
    category: str
    path: str
    lineno: int
    detail: str
    recommendation: str

    def render(self) -> str:
        location = f"{self.path}:{self.lineno}" if self.lineno else self.path
        return f"[sd-review-learnings:{self.category}] {location}: {self.detail}"

    def markdown_item(self) -> str:
        # Same managed-block injection surface as PR comment rendering:
        # paths and details originate from repo-controlled diff content.
        location = f"{self.path}:{self.lineno}" if self.lineno else self.path
        location = _neutralize_managed_markers(location)
        detail = _neutralize_managed_markers(self.detail)
        recommendation = _neutralize_managed_markers(self.recommendation)
        return (
            f"- **{self.category}** `{location}`: {detail} "
            f"Recommendation: {recommendation}"
        )


@dataclasses.dataclass(frozen=True)
class PullRequestComment:
    pr_number: int
    pr_title: str
    pr_url: str
    path: str
    body: str
    is_resolved: bool
    is_outdated: bool
    created_at: str = ""

    def markdown_item(self) -> str:
        state = "current" if not self.is_resolved and not self.is_outdated else "historical"
        # Every rendered field is untrusted (bodies, file paths, URLs can
        # all carry repo-controlled text): an embedded managed marker would
        # splice the managed block on the next update.
        body = _neutralize_managed_markers(_one_line(self.body, limit=220))
        path = _neutralize_managed_markers(_one_line(self.path, limit=500))
        url = _neutralize_managed_markers(self.pr_url)
        return (
            f"- **{state}** PR #{self.pr_number} "
            f"{_markdown_code_span(path)}: {body} ({url})"
        )


@dataclasses.dataclass(frozen=True)
class HistoricalSignalCluster:
    category: str
    count: int
    signature_count: int
    pr_numbers: tuple[int, ...]
    path_families: tuple[str, ...]
    first_seen: str
    last_seen: str
    signature_examples: tuple[tuple[str, int], ...]
    examples: tuple[PullRequestComment, ...]

    def markdown_items(self) -> list[str]:
        label = SIGNAL_CATEGORY_LABELS[self.category]
        pr_values = ", ".join(f"#{number}" for number in self.pr_numbers[:MAX_CLUSTER_PRS])
        path_values = ", ".join(
            _markdown_code_span(_neutralize_managed_markers(path))
            for path in self.path_families[:MAX_CLUSTER_PATH_FAMILIES]
        )
        time_bounds = (
            f"observed {self.first_seen} to {self.last_seen}"
            if self.first_seen and self.last_seen
            else "time bounds unavailable"
        )
        lines = [
            f"- **{label}** (`{self.category}`): {self.count} historical "
            f"comment(s) across {self.signature_count} normalized signature(s); "
            f"PRs {pr_values or '(unknown)'}; path families "
            f"{path_values or '`(unknown)`'}; {time_bounds}."
        ]
        if self.signature_examples:
            rendered_signatures = "; ".join(
                f"{_markdown_code_span(_neutralize_managed_markers(_one_line(text, limit=110)))} "
                f"(x{count})"
                for text, count in self.signature_examples
            )
            lines.append(f"  - Representative signatures: {rendered_signatures}")
        for comment in self.examples:
            body = _neutralize_managed_markers(_one_line(comment.body, limit=160))
            path = _neutralize_managed_markers(_one_line(comment.path, limit=300))
            url = _neutralize_managed_markers(comment.pr_url)
            lines.append(
                f"  - Example: PR #{comment.pr_number} {_markdown_code_span(path)}: "
                f"{body} ({url})"
            )

        truncations: list[str] = []
        if self.signature_count > len(self.signature_examples):
            truncations.append(
                f"signatures {len(self.signature_examples)}/{self.signature_count}"
            )
        if len(self.pr_numbers) > MAX_CLUSTER_PRS:
            truncations.append(f"PRs {MAX_CLUSTER_PRS}/{len(self.pr_numbers)}")
        if len(self.path_families) > MAX_CLUSTER_PATH_FAMILIES:
            truncations.append(
                f"path families {MAX_CLUSTER_PATH_FAMILIES}/{len(self.path_families)}"
            )
        if self.count > len(self.examples):
            truncations.append(f"examples {len(self.examples)}/{self.count}")
        if truncations:
            lines.append(f"  - _Evidence truncated: {', '.join(truncations)}._")
        return lines


@dataclasses.dataclass(frozen=True)
class CopilotReviewWindow:
    comments: tuple[PullRequestComment, ...]
    prs_inspected: int
    cutoff: str | None
    truncated: bool


def _markdown_code_span(value: str) -> str:
    longest_run = max(
        (len(match.group(0)) for match in re.finditer(r"`+", value)),
        default=0,
    )
    if longest_run == 0:
        return f"`{value}`"
    fence = "`" * (longest_run + 1)
    return f"{fence} {value} {fence}"


def _parse_diff(diff_text: str) -> tuple[set[str], list[AddedLine]]:
    changed: set[str] = set()
    added: list[AddedLine] = []
    current: str | None = None
    next_lineno: int | None = None

    for raw in diff_text.splitlines():
        if raw.startswith("+++ "):
            match = _FILE_HEADER_RE.match(raw)
            current = match.group(1) if match else None
            if current is not None:
                changed.add(current)
            continue
        if raw.startswith("--- "):
            continue
        if raw.startswith("@@ "):
            match = _HUNK_RE.match(raw)
            next_lineno = int(match.group(1)) if match else None
            continue
        if next_lineno is None:
            continue
        if raw.startswith("+"):
            if current is not None:
                added.append(AddedLine(current, next_lineno, raw[1:]))
            next_lineno += 1
        elif raw.startswith("-"):
            continue
        else:
            next_lineno += 1

    return changed, added


def _read_text(repo_root: Path, path: str) -> str:
    target = repo_root / path
    if not target.is_file():
        return ""
    return target.read_text(encoding="utf-8", errors="replace")


def _is_comment(line: str) -> bool:
    return line.lstrip().startswith("#")


def _is_shell_like(path: str, repo_root: Path) -> bool:
    if path.endswith((".sh", ".bash", ".zsh", ".sh.tmpl")):
        return True
    if not path.startswith(("scripts/", "benchmarks/", ".github/actions/")):
        return False
    text = _read_text(repo_root, path)
    first_line = next(iter(text.splitlines()), "")
    return "bash" in first_line or " sh" in first_line or first_line.endswith("/sh")


def _is_workflow(path: str) -> bool:
    return path.startswith(".github/workflows/") and path.endswith((".yml", ".yaml"))


def _has_pipefail(text: str) -> bool:
    return "pipefail" in text


def _file_has_help(text: str) -> bool:
    return "Usage:" in text or "usage()" in text or "show_help" in text


def _mktemp_is_portable(line: str) -> bool:
    return "XXXX" in line or re.search(r"\bmktemp\b[^#\n]*\s-t\s+\S+", line) is not None


@functools.lru_cache(maxsize=4)
def _env_ref_re(env_prefixes: tuple[str, ...]) -> re.Pattern[str] | None:
    prefixes = tuple(sorted({prefix.strip() for prefix in env_prefixes if prefix.strip()}))
    if not prefixes:
        return None
    prefix_pattern = "|".join(re.escape(prefix) for prefix in prefixes)
    return re.compile(
        rf"\$(?:\{{((?:{prefix_pattern})_[A-Z0-9_]+)[^}}]*\}}|((?:{prefix_pattern})_[A-Z0-9_]+)(?![A-Za-z0-9_]))"
    )


def _extract_env_refs(line: str, env_prefixes: tuple[str, ...]) -> set[str]:
    env_re = _env_ref_re(env_prefixes)
    if env_re is None:
        return set()
    return {match.group(1) or match.group(2) for match in env_re.finditer(line)}


def _scan_shell_and_workflow_lines(
    added_lines: list[AddedLine],
    repo_root: Path,
    *,
    env_prefixes: tuple[str, ...],
) -> list[Finding]:
    findings: list[Finding] = []
    file_text_cache: dict[str, str] = {}
    shell_like_cache: dict[str, bool] = {}

    for line in added_lines:
        if line.path not in shell_like_cache:
            shell_like_cache[line.path] = _is_shell_like(line.path, repo_root)
        shell_like = shell_like_cache[line.path]
        workflow = _is_workflow(line.path)
        if not shell_like and not workflow:
            continue
        stripped = line.content.strip()
        if not stripped or _is_comment(line.content):
            continue

        text = file_text_cache.setdefault(line.path, _read_text(repo_root, line.path))

        if shell_like and "mktemp" in line.content and not _mktemp_is_portable(line.content):
            findings.append(
                Finding(
                    CATEGORY_PORTABILITY,
                    line.path,
                    line.lineno,
                    "mktemp call needs a portable template",
                    "Use a template such as '${TMPDIR:-/tmp}/tool.XXXXXX' instead of bare mktemp.",
                )
            )

        if shell_like and _NEGATIVE_ARRAY_OFFSET_RE.search(line.content):
            findings.append(
                Finding(
                    CATEGORY_PORTABILITY,
                    line.path,
                    line.lineno,
                    "Bash negative array offsets are not portable to macOS Bash 3.2",
                    "Use '${!#}' or compute an explicit positive index.",
                )
            )

        if (
            shell_like
            and _has_pipefail(text)
            and _GREP_EXPECTED_EMPTY_RE.search(line.content)
            and "|| true" not in line.content
            and "|| :" not in line.content
        ):
            findings.append(
                Finding(
                    CATEGORY_PORTABILITY,
                    line.path,
                    line.lineno,
                    "grep -v under pipefail can abort on an expected empty result",
                    "Handle grep status 1 explicitly when an empty filtered result is valid.",
                )
            )

        if _CLASSIFY_WITH_FILES_RE.search(line.content) and not _CLASSIFY_WITH_DELIMITER_RE.search(line.content):
            findings.append(
                Finding(
                    CATEGORY_PORTABILITY,
                    line.path,
                    line.lineno,
                    "changed filenames should be passed after '--'",
                    "Call classify-ci-changes.sh as `... -- \"${files[@]}\"` so paths starting with '-' are data.",
                )
            )

        if workflow and _ALL_ZERO_GREP_RE.search(line.content):
            findings.append(
                Finding(
                    CATEGORY_PORTABILITY,
                    line.path,
                    line.lineno,
                    "all-zero SHAs are hard to review when written as '! ... | grep -qv ^0*$'",
                    "Use a direct empty-or-all-zero guard so future edits preserve fallback behavior.",
                )
            )

        if shell_like and _file_has_help(text):
            findings.extend(_scan_shell_interface_line(line, text, env_prefixes=env_prefixes))

    return findings


def _scan_shell_interface_line(
    line: AddedLine,
    file_text: str,
    *,
    env_prefixes: tuple[str, ...],
) -> list[Finding]:
    findings: list[Finding] = []

    option_match = _LONG_OPTION_CASE_RE.match(line.content)
    if option_match:
        option = option_match.group(1)
        if file_text.count(option) < 2:
            findings.append(
                Finding(
                    CATEGORY_INTERFACE,
                    line.path,
                    line.lineno,
                    f"{option} is handled by the script but is not documented in help text",
                    "Add the option to the Usage/help block or remove the dead parser arm.",
                )
            )

    for env_name in sorted(_extract_env_refs(line.content, env_prefixes)):
        if file_text.count(env_name) < 2:
            findings.append(
                Finding(
                    CATEGORY_INTERFACE,
                    line.path,
                    line.lineno,
                    f"{env_name} is used by the script but is not documented in help text",
                    "Document operator-facing environment variables in script help or repo docs.",
                )
            )

    return findings


def _is_trellis_journal(path: str) -> bool:
    name = Path(path).name
    return path.startswith(".trellis/workspace/") and name.startswith("journal")


def _scan_trellis_journals(changed: set[str], repo_root: Path) -> list[Finding]:
    findings: list[Finding] = []
    for path in sorted(changed):
        if not _is_trellis_journal(path):
            continue
        text = _read_text(repo_root, path)
        for lineno, line in enumerate(text.splitlines(), start=1):
            for placeholder in TRELLIS_JOURNAL_PLACEHOLDERS:
                if placeholder in line:
                    findings.append(
                        Finding(
                            CATEGORY_REVIEW_SCAFFOLDING,
                            path,
                            lineno,
                            f"checked-in Trellis journal still contains placeholder {placeholder!r}",
                            "Replace placeholders with concrete changes/tests or remove the incomplete bullet.",
                        )
                    )
    return findings


def _scan_pr_template(changed: set[str], repo_root: Path) -> list[Finding]:
    path = ".github/pull_request_template.md"
    if path not in changed:
        return []
    text = _read_text(repo_root, path)
    missing = [phrase for phrase in REQUIRED_PR_TEMPLATE_PHRASES if phrase not in text]
    if not missing:
        return []
    return [
        Finding(
            CATEGORY_PR_TEMPLATE,
            path,
            1,
            "PR template is missing review-cycle scope disclosure phrase(s): " + ", ".join(missing),
            "Add a Scope and surfaces section so reviewers know code/docs/generated/copied/test surfaces up front.",
        )
    ]


def _scan_copilot_instructions(changed: set[str], repo_root: Path) -> list[Finding]:
    path = ".github/copilot-instructions.md"
    if path not in changed:
        return []
    text = _read_text(repo_root, path)
    missing = [phrase for phrase in RECOMMENDED_COPILOT_PHRASES if phrase not in text]
    if not missing:
        return []
    return [
        Finding(
            CATEGORY_COPILOT_INSTRUCTIONS,
            path,
            1,
            "Copilot instructions are missing review-cycle guidance phrase(s): " + ", ".join(missing),
            "Tell Copilot to separate current non-outdated unresolved findings from stale threads and ignore copied/generated payloads unless their source or sync contract changed.",
        )
    ]


def extract_findings(
    diff_text: str,
    repo_root: Path,
    *,
    env_prefixes: tuple[str, ...] = DEFAULT_ENV_PREFIXES,
) -> list[Finding]:
    changed, added_lines = _parse_diff(diff_text)
    findings: list[Finding] = []
    findings.extend(
        _scan_shell_and_workflow_lines(
            added_lines,
            repo_root,
            env_prefixes=env_prefixes,
        )
    )
    findings.extend(_scan_trellis_journals(changed, repo_root))
    findings.extend(_scan_pr_template(changed, repo_root))
    findings.extend(_scan_copilot_instructions(changed, repo_root))
    return findings


def _run_git(
    args: list[str],
    repo_root: Path,
    *,
    check: bool = True,
    accept_one: bool = False,
) -> CompletedProcess[str]:
    result = run_git_command(
        args,
        cwd=repo_root,
        context=f"run git {' '.join(args)}",
    )
    if check:
        allowed = {0, 1} if accept_one else {0}
        if result.returncode not in allowed:
            raise RuntimeError(result.stderr.strip() or "git command failed")
    return result


def _git_diff(base_ref: str, repo_root: Path) -> str:
    return _run_git(["diff", "--no-ext-diff", f"{base_ref}...HEAD"], repo_root).stdout


def _run_git_optional(args: list[str], repo_root: Path) -> str:
    result = _run_git(args, repo_root, check=False)
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def _git_ref_exists(ref: str, repo_root: Path) -> bool:
    result = _run_git(
        ["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
        repo_root,
        check=False,
    )
    return result.returncode == 0


def default_base_ref(repo_root: Path) -> str:
    if _git_ref_exists("origin/HEAD", repo_root):
        return "origin/HEAD"

    upstream = _run_git_optional(
        ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"],
        repo_root,
    )
    if upstream:
        return upstream

    remote_refs = sorted(
        ref
        for ref in _run_git_optional(
            ["for-each-ref", "--format=%(refname:short)", "refs/remotes"],
            repo_root,
        ).splitlines()
        if ref and not ref.endswith("/HEAD")
    )
    return remote_refs[0] if remote_refs else ""


def _git_untracked_paths(repo_root: Path) -> list[str]:
    result = run_git_command(
        ["ls-files", "--others", "--exclude-standard", "-z"],
        cwd=repo_root,
        context="run git ls-files for untracked paths",
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "git ls-files failed")
    return [path for path in result.stdout.split("\0") if path]


def _git_working_tree_diff(repo_root: Path) -> str:
    chunks = [
        _run_git(["diff", "--no-ext-diff", "--cached"], repo_root).stdout,
        _run_git(["diff", "--no-ext-diff"], repo_root).stdout,
    ]
    for path in _git_untracked_paths(repo_root):
        target = repo_root / path
        if not target.is_file():
            continue
        chunks.append(
            _run_git(
                ["diff", "--no-ext-diff", "--no-index", "--", os.devnull, path],
                repo_root,
                accept_one=True,
            ).stdout
        )
    return "\n".join(chunk for chunk in chunks if chunk)


def build_local_diff(repo_root: Path, *, base: str | None, include_working_tree: bool) -> str:
    base_ref = base or default_base_ref(repo_root)
    diff_text = _git_diff(base_ref, repo_root) if base_ref else ""
    if include_working_tree:
        working = _git_working_tree_diff(repo_root)
        if working:
            diff_text = "\n".join(part for part in (diff_text, working) if part)
    return diff_text


def _as_dict(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError(f"expected object in review learnings payload, got {type(value).__name__}")
    return value


def _as_list(value: Any) -> list[Any]:
    if not isinstance(value, list):
        raise TypeError(f"expected list in review learnings payload, got {type(value).__name__}")
    return value


def _dig(obj: Any, *keys: str) -> Any:
    """Walk nested dict keys, returning None on any shape mismatch.

    Skip-not-raise: an unexpected payload shape yields None so callers can
    silently skip it, matching the tolerant GraphQL-descent contract.
    """
    for key in keys:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(key)
    return obj


def _one_line(text: str, *, limit: int = 220) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    if limit <= 3:
        return "." * limit

    budget = limit - 3
    candidate = collapsed[:budget].rstrip()
    word_boundary = candidate.rfind(" ")
    if word_boundary > 0:
        candidate = candidate[:word_boundary].rstrip()
    return candidate + "..."


def _normalize_signal_text(text: str) -> str:
    normalized = _one_line(text, limit=800).lower()
    normalized = re.sub(r"https?://\S+", "<url>", normalized)
    normalized = re.sub(r"\bpr\s*#?\d+\b", "pr <n>", normalized)
    normalized = re.sub(r"\blines?\s+\d+(?:\s*[-:]\s*\d+)?\b", "line <n>", normalized)
    normalized = re.sub(r"(?<=\w):\d+\b", ":<n>", normalized)
    normalized = re.sub(r"[`*_~]+", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip(" .,:;!?'\"")
    return normalized or "(empty comment)"


def _path_family(path: str) -> str:
    normalized = path.replace("\\", "/").strip("/")
    lowered = normalized.lower()
    if lowered.startswith(".trellis/tasks/"):
        return SIGNAL_TASK_METADATA
    if lowered.startswith(".trellis/spec/"):
        return "trellis-spec"
    if lowered.startswith("templates/"):
        return "templates"
    if lowered.startswith("tests/"):
        return "tests"
    if lowered.startswith("docs/") or lowered in {"readme.md", "changelog.md"}:
        return "documentation"
    if lowered.startswith("scripts/"):
        return "scripts"
    if lowered.startswith(".github/"):
        return "github-config"
    if not normalized:
        return "(unknown)"
    parts = normalized.split("/")
    return "/".join(parts[:2]) if len(parts) > 1 else "repository-root"


def _signal_category(comment: PullRequestComment) -> str:
    normalized = _normalize_signal_text(comment.body)
    for category, patterns in SIGNAL_CATEGORY_PATTERNS:
        if any(pattern in normalized for pattern in patterns):
            return category

    path = comment.path.replace("\\", "/").lower()
    if path.startswith(".trellis/tasks/"):
        return SIGNAL_TASK_METADATA
    if path.startswith(GENERATED_SIGNAL_PATH_PREFIXES) or path in GENERATED_SIGNAL_PATHS:
        return SIGNAL_GENERATED_SURFACES
    if path.startswith("tests/"):
        return SIGNAL_REVIEWER_TEST_HARNESS
    if path.startswith("docs/") or path in {"readme.md", "changelog.md"}:
        return SIGNAL_CONTRACT_DOCUMENTATION
    return SIGNAL_OTHER


def _timestamp_value(value: str) -> float:
    if not value:
        return 0.0
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.timestamp()


def _timestamp_day(value: str) -> str:
    if not value or _timestamp_value(value) == 0.0:
        return ""
    return value[:10]


def _comment_sort_key(comment: PullRequestComment) -> tuple[float, int, str, str]:
    return (
        -_timestamp_value(comment.created_at),
        comment.pr_number,
        comment.path.casefold(),
        _normalize_signal_text(comment.body),
    )


def partition_review_comments(
    comments: list[PullRequestComment],
) -> tuple[list[PullRequestComment], list[PullRequestComment]]:
    actionable = sorted(
        (
            comment
            for comment in comments
            if not comment.is_resolved and not comment.is_outdated
        ),
        key=_comment_sort_key,
    )
    historical = [
        comment
        for comment in comments
        if comment.is_resolved or comment.is_outdated
    ]
    return actionable, historical


def cluster_historical_comments(
    comments: list[PullRequestComment],
) -> list[HistoricalSignalCluster]:
    signatures: dict[
        tuple[str, str, str], list[PullRequestComment]
    ] = {}
    for comment in comments:
        category = _signal_category(comment)
        signature = (
            category,
            _path_family(comment.path),
            _normalize_signal_text(comment.body),
        )
        signatures.setdefault(signature, []).append(comment)

    categories: dict[
        str, list[tuple[tuple[str, str, str], list[PullRequestComment]]]
    ] = {}
    for signature, grouped_comments in signatures.items():
        categories.setdefault(signature[0], []).append((signature, grouped_comments))

    clusters: list[HistoricalSignalCluster] = []
    for category, category_signatures in categories.items():
        ranked_signatures = sorted(
            category_signatures,
            key=lambda item: (
                -len(item[1]),
                -max((_timestamp_value(comment.created_at) for comment in item[1]), default=0.0),
                item[0],
            ),
        )
        all_comments = [
            comment
            for _signature, grouped_comments in ranked_signatures
            for comment in grouped_comments
        ]
        dated_comments = [comment for comment in all_comments if _timestamp_value(comment.created_at)]
        dated_comments.sort(key=lambda comment: _timestamp_value(comment.created_at))

        signature_examples: list[tuple[str, int]] = []
        examples: list[PullRequestComment] = []
        for _signature, grouped_comments in ranked_signatures[:MAX_CLUSTER_SIGNATURES]:
            representative = sorted(grouped_comments, key=_comment_sort_key)[0]
            signature_examples.append((representative.body, len(grouped_comments)))
            if len(examples) < MAX_CLUSTER_EXAMPLES:
                examples.append(representative)

        clusters.append(
            HistoricalSignalCluster(
                category=category,
                count=len(all_comments),
                signature_count=len(ranked_signatures),
                pr_numbers=tuple(sorted({comment.pr_number for comment in all_comments})),
                path_families=tuple(sorted({_path_family(comment.path) for comment in all_comments})),
                first_seen=_timestamp_day(dated_comments[0].created_at) if dated_comments else "",
                last_seen=_timestamp_day(dated_comments[-1].created_at) if dated_comments else "",
                signature_examples=tuple(signature_examples),
                examples=tuple(examples),
            )
        )

    return sorted(
        clusters,
        key=lambda cluster: (
            -cluster.count,
            -_timestamp_value(cluster.last_seen),
            cluster.category,
        ),
    )


def preventive_actions(clusters: list[HistoricalSignalCluster]) -> list[str]:
    actions: list[str] = []
    for cluster in clusters:
        action = SIGNAL_PREVENTIVE_ACTIONS.get(cluster.category)
        if action and cluster.count >= MIN_PREVENTIVE_ACTION_COUNT:
            actions.append(
                f"- **{SIGNAL_CATEGORY_LABELS[cluster.category]}** "
                f"({cluster.count} historical comments): {action}"
            )
    return actions


def _run_gh_stdout(args: list[str], repo_root: Path) -> str:
    result = run_gh_command(
        args,
        cwd=repo_root,
        context=f"run gh {' '.join(args)}",
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "gh command failed")
    return result.stdout


def _run_gh_json(args: list[str], repo_root: Path) -> Any:
    return json.loads(_run_gh_stdout(args, repo_root) or "null")


def github_repo_slug(repo_root: Path, override: str | None = None) -> tuple[str, str]:
    slug = override.strip() if override else ""
    if not slug:
        slug = _run_gh_stdout(
            ["repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"],
            repo_root,
        ).strip()
    if slug.count("/") != 1:
        raise RuntimeError("GitHub repository must resolve to OWNER/REPO")
    owner, name = slug.split("/", 1)
    if not owner or not name:
        raise RuntimeError("GitHub repository must resolve to OWNER/REPO")
    return owner, name


def _recent_pull_requests(
    repo_root: Path,
    *,
    days: int,
    limit: int,
    owner: str,
    name: str,
) -> tuple[list[dict[str, Any]], str, bool]:
    cutoff_dt = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)
    cutoff = cutoff_dt.isoformat().replace("+00:00", "Z")
    query = """
query($owner:String!, $name:String!, $endCursor:String) {
  repository(owner:$owner, name:$name) {
    pullRequests(first:100, after:$endCursor, states:[OPEN,MERGED,CLOSED], orderBy:{field:UPDATED_AT, direction:DESC}) {
      pageInfo { hasNextPage endCursor }
      nodes { number title url updatedAt }
    }
  }
}
""".strip()
    prs: list[dict[str, Any]] = []
    cursor: str | None = None
    while True:
        args = [
            "api",
            "graphql",
            "-F",
            f"owner={owner}",
            "-F",
            f"name={name}",
            "-f",
            f"query={query}",
        ]
        if cursor:
            args.extend(["-F", f"endCursor={cursor}"])
        payload = _run_gh_json(args, repo_root)
        connection = _dig(
            payload,
            "data",
            "repository",
            "pullRequests",
        )
        if not isinstance(connection, dict):
            break
        nodes = connection.get("nodes")
        if not isinstance(nodes, list):
            break

        reached_cutoff = False
        for value in nodes:
            pr = _as_dict(value)
            updated_at = pr.get("updatedAt")
            if not isinstance(updated_at, str):
                continue
            try:
                updated_dt = dt.datetime.fromisoformat(
                    updated_at.replace("Z", "+00:00")
                )
            except ValueError:
                continue
            if updated_dt < cutoff_dt:
                reached_cutoff = True
                break
            if limit and len(prs) >= limit:
                return prs, cutoff, True
            prs.append(pr)

        page_info = connection.get("pageInfo")
        if reached_cutoff or not isinstance(page_info, dict):
            break
        if not page_info.get("hasNextPage"):
            break
        next_cursor = page_info.get("endCursor")
        if not isinstance(next_cursor, str) or not next_cursor:
            break
        cursor = next_cursor
    return prs, cutoff, False


def _copilot_comments_for_prs(
    repo_root: Path,
    *,
    owner: str,
    name: str,
    prs: list[dict[str, Any]],
) -> list[PullRequestComment]:
    query = """
query($owner:String!, $name:String!, $number:Int!) {
  repository(owner:$owner, name:$name) {
    pullRequest(number:$number) {
      reviewThreads(first:100) {
        nodes {
          isResolved
          isOutdated
          path
          comments(first:50) {
            nodes {
              author { login }
              body
              createdAt
            }
          }
        }
      }
    }
  }
}
""".strip()
    comments: list[PullRequestComment] = []
    for pr in prs:
        pr_obj = _as_dict(pr)
        number = pr_obj.get("number")
        if not isinstance(number, int):
            continue
        payload = _run_gh_json(
            [
                "api",
                "graphql",
                "-F",
                f"owner={owner}",
                "-F",
                f"name={name}",
                "-F",
                f"number={number}",
                "-f",
                f"query={query}",
            ],
            repo_root,
        )
        thread_nodes = _dig(
            payload,
            "data",
            "repository",
            "pullRequest",
            "reviewThreads",
            "nodes",
        )
        if not isinstance(thread_nodes, list):
            continue
        for thread in thread_nodes:
            thread_obj = _as_dict(thread)
            comments_payload = thread_obj.get("comments")
            if not isinstance(comments_payload, dict):
                continue
            comment_nodes = comments_payload.get("nodes")
            if not isinstance(comment_nodes, list):
                continue
            for comment in comment_nodes:
                comment_obj = _as_dict(comment)
                author = comment_obj.get("author")
                if not isinstance(author, dict) or author.get("login") != COPILOT_LOGIN:
                    continue
                path = thread_obj.get("path")
                body = comment_obj.get("body")
                comments.append(
                    PullRequestComment(
                        number,
                        str(pr_obj.get("title") or ""),
                        str(pr_obj.get("url") or ""),
                        path if isinstance(path, str) else "(unknown path)",
                        body if isinstance(body, str) else "",
                        bool(thread_obj.get("isResolved")),
                        bool(thread_obj.get("isOutdated")),
                        str(comment_obj.get("createdAt") or ""),
                    )
                )
    return comments


def fetch_recent_copilot_review_window(
    repo_root: Path,
    *,
    days: int,
    limit: int = 0,
    github_repo: str | None = None,
) -> CopilotReviewWindow:
    owner, name = github_repo_slug(repo_root, github_repo)
    prs, cutoff, truncated = _recent_pull_requests(
        repo_root,
        days=days,
        limit=limit,
        owner=owner,
        name=name,
    )
    comments = _copilot_comments_for_prs(
        repo_root,
        owner=owner,
        name=name,
        prs=prs,
    )
    return CopilotReviewWindow(tuple(comments), len(prs), cutoff, truncated)


def fetch_copilot_review_for_prs(
    repo_root: Path,
    *,
    pr_numbers: list[int],
    github_repo: str | None = None,
) -> CopilotReviewWindow:
    owner, name = github_repo_slug(repo_root, github_repo)
    unique_numbers = list(dict.fromkeys(pr_numbers))
    prs = [
        {
            "number": number,
            "title": "",
            "url": f"https://github.com/{owner}/{name}/pull/{number}",
        }
        for number in unique_numbers
    ]
    comments = _copilot_comments_for_prs(
        repo_root,
        owner=owner,
        name=name,
        prs=prs,
    )
    return CopilotReviewWindow(tuple(comments), len(prs), None, False)


def fetch_recent_copilot_comments(
    repo_root: Path,
    *,
    days: int,
    limit: int,
    github_repo: str | None = None,
) -> list[PullRequestComment]:
    """Compatibility helper for callers that need only comment rows."""
    return list(
        fetch_recent_copilot_review_window(
            repo_root,
            days=days,
            limit=limit,
            github_repo=github_repo,
        ).comments
    )


def render_managed_block(findings: list[Finding], comments: list[PullRequestComment]) -> str:
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    lines = [
        MANAGED_START,
        "## SD Review Learnings",
        "",
        f"_Last updated: {today}_",
        "",
        "### Local Pattern Findings",
    ]
    if findings:
        lines.extend(finding.markdown_item() for finding in findings)
    else:
        lines.append("- No local review-cycle findings detected in the scanned diff.")

    actionable, historical = partition_review_comments(comments)
    clusters = cluster_historical_comments(historical)

    lines.extend(
        [
            "",
            "### Recent Copilot Review Signals",
            "",
            "#### Current Actionable Comments",
        ]
    )
    if actionable:
        lines.extend(comment.markdown_item() for comment in actionable)
    else:
        lines.append("- No current, non-outdated unresolved comments were included.")

    lines.extend(["", "#### Historical Signal Clusters"])
    shown_clusters = clusters[:MAX_HISTORICAL_CLUSTERS]
    if clusters:
        for cluster in shown_clusters:
            lines.extend(cluster.markdown_items())
        if len(clusters) > len(shown_clusters):
            lines.append(
                "- _Historical clusters truncated: showing "
                f"{len(shown_clusters)} of {len(clusters)} categories._"
            )
    else:
        lines.append("- No historical Copilot review comments were included.")

    lines.extend(["", "### Suggested Preventive Actions"])
    actions = preventive_actions(shown_clusters)
    if actions:
        lines.extend(actions)
    else:
        lines.append("- No recurring historical category met the preventive-action threshold.")
    lines.extend([MANAGED_END, ""])
    return "\n".join(lines)


def update_target(target: Path, block: str, *, dry_run: bool) -> str:
    existing = ""
    if target.is_file():
        existing = target.read_text(encoding="utf-8", errors="replace")
    start = existing.find(MANAGED_START)
    first_end = existing.find(MANAGED_END)
    if start >= 0 or first_end >= 0:
        end_marker = (
            existing.find(MANAGED_END, start + len(MANAGED_START))
            if start >= 0
            else -1
        )
        if start < 0 or end_marker < 0:
            raise ValueError(
                f"{target} contains managed review-learnings markers in invalid order"
            )
        end = end_marker + len(MANAGED_END)
        tail = existing[end:]
        if tail.startswith("\n"):
            tail = tail[1:]
        updated = existing[:start] + block + tail
        if not updated.endswith("\n"):
            updated += "\n"
    elif existing.strip():
        updated = existing.rstrip() + "\n\n" + block
    else:
        updated = "# Review Learnings\n\n" + block

    if dry_run:
        return updated
    target.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(target, updated, errors="strict")
    return updated


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Detect and update repo-specific review learnings.")
    parser.add_argument(
        "--base",
        help=(
            "Base ref for branch diff scans. Defaults to the discovered remote "
            "default ref, then the current upstream, then the first remote ref."
        ),
    )
    parser.add_argument("--diff-from", type=Path, help="Read unified diff from this file.")
    parser.add_argument(
        "--include-working-tree",
        action="store_true",
        help="Include staged, unstaged, and untracked changes.",
    )
    parser.add_argument("--repo-root", type=Path, default=Path("."), help="Repository root to scan.")
    parser.add_argument("--target", type=Path, default=DEFAULT_TARGET, help="Markdown file to update.")
    parser.add_argument("--update", action="store_true", help="Write/update the managed learnings block.")
    parser.add_argument("--dry-run", action="store_true", help="Print the updated markdown instead of writing it.")
    parser.add_argument(
        "--github-days",
        type=int,
        default=0,
        help="Include Copilot comments from PRs updated in the last N days.",
    )
    parser.add_argument(
        "--github-limit",
        type=int,
        default=0,
        help=(
            "Maximum PRs to inspect when --github-days is set; zero (the "
            "default) pages through the complete time window."
        ),
    )
    parser.add_argument(
        "--github-pr",
        type=int,
        action="append",
        default=[],
        help=(
            "Inspect one PR instead of a date window. Repeat for multiple PRs; "
            "intended for the single post-cycle learning pass."
        ),
    )
    parser.add_argument(
        "--github-repo",
        metavar="OWNER/REPO",
        help="GitHub repository to inspect; defaults to `gh repo view` for the current repo.",
    )
    parser.add_argument(
        "--env-prefix",
        action="append",
        help="Environment-variable prefix to require help coverage for. Repeat to override defaults.",
    )
    parser.add_argument("--allow", metavar="REASON", help="Return success even when findings are present.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.allow is not None and not args.allow.strip():
        print("[sd-review-learnings:setup] --allow requires a non-empty reason", file=sys.stderr)
        return 2
    if args.github_days < 0:
        print("[sd-review-learnings:setup] --github-days must be non-negative", file=sys.stderr)
        return 2
    if args.github_limit < 0:
        print("[sd-review-learnings:setup] --github-limit must be non-negative", file=sys.stderr)
        return 2
    if args.github_days and args.github_pr:
        print(
            "[sd-review-learnings:setup] --github-days and --github-pr are mutually exclusive",
            file=sys.stderr,
        )
        return 2
    if any(number < 1 for number in args.github_pr):
        print("[sd-review-learnings:setup] --github-pr must be positive", file=sys.stderr)
        return 2

    repo_root = args.repo_root.resolve()
    env_prefixes = tuple(args.env_prefix) if args.env_prefix else DEFAULT_ENV_PREFIXES
    try:
        if args.diff_from is not None:
            diff_text = args.diff_from.read_text(encoding="utf-8", errors="replace")
        else:
            resolved_base = args.base or default_base_ref(repo_root)
            if not resolved_base and not args.include_working_tree:
                print(
                    "[sd-review-learnings:scan] no base ref could be "
                    "resolved (no origin/HEAD, upstream, or remote refs); "
                    "nothing was scanned",
                    file=sys.stderr,
                )
            diff_text = build_local_diff(
                repo_root,
                base=resolved_base or None,
                include_working_tree=args.include_working_tree,
            )
        findings = extract_findings(diff_text, repo_root, env_prefixes=env_prefixes)
    except (
        CommandError,
        OSError,
        RuntimeError,
        json.JSONDecodeError,
    ) as exc:
        print(f"[sd-review-learnings:findings] {exc}", file=sys.stderr)
        return 2

    try:
        if args.github_pr:
            review_window = fetch_copilot_review_for_prs(
                repo_root,
                pr_numbers=args.github_pr,
                github_repo=args.github_repo,
            )
        elif args.github_days:
            review_window = fetch_recent_copilot_review_window(
                repo_root,
                days=args.github_days,
                limit=args.github_limit,
                github_repo=args.github_repo,
            )
        else:
            review_window = CopilotReviewWindow((), 0, None, False)
    except (
        CommandError,
        OSError,
        RuntimeError,
        TypeError,
        json.JSONDecodeError,
    ) as exc:
        print(f"[sd-review-learnings:github] {exc}", file=sys.stderr)
        return 2

    for finding in findings:
        print(finding.render())
    comments = list(review_window.comments)
    if args.github_days or args.github_pr:
        window_label = (
            f" updated since {review_window.cutoff}"
            if review_window.cutoff
            else " from the requested PR set"
        )
        print(
            "[sd-review-learnings:github] inspected "
            f"{review_window.prs_inspected} PR(s){window_label}; captured "
            f"{len(comments)} Copilot review comment(s)"
        )
        if review_window.truncated:
            print(
                "[sd-review-learnings:github] warning: --github-limit truncated "
                "the requested PR window",
                file=sys.stderr,
            )

    if args.update or args.dry_run:
        block = render_managed_block(findings, comments)
        target = args.target if args.target.is_absolute() else repo_root / args.target
        try:
            updated = update_target(target, block, dry_run=args.dry_run)
        except (OSError, ValueError) as exc:
            print(f"[sd-review-learnings:update] {exc}", file=sys.stderr)
            return 2
        if args.dry_run:
            print(updated, end="" if updated.endswith("\n") else "\n")
        else:
            try:
                shown_target = target.relative_to(repo_root)
            except ValueError:
                shown_target = target
            print(f"[sd-review-learnings:OK] updated {shown_target}")
        return 0

    if findings:
        if args.allow is not None:
            print(f"[sd-review-learnings:OK] bypassed via --allow: {args.allow}")
            return 0
        return 1

    print("[sd-review-learnings:OK] no local review-cycle findings detected")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
