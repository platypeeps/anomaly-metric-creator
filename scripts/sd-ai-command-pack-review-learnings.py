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
import hashlib
import json
import os
import re
import stat
import sys
import tempfile
from pathlib import Path
from subprocess import CompletedProcess
from typing import Any, Callable

from sd_ai_command_pack_lib import (
    REVIEW_FAMILY_BOUNDARY_VALIDATION,
    REVIEW_FAMILY_CONTRACT_DOCUMENTATION,
    REVIEW_FAMILY_GENERATED_SURFACES,
    REVIEW_FAMILY_OTHER,
    REVIEW_FAMILY_REVIEWER_TEST_HARNESS,
    REVIEW_FAMILY_TASK_METADATA,
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

SIGNAL_TASK_METADATA = REVIEW_FAMILY_TASK_METADATA
SIGNAL_BOUNDARY_VALIDATION = REVIEW_FAMILY_BOUNDARY_VALIDATION
SIGNAL_CONTRACT_DOCUMENTATION = REVIEW_FAMILY_CONTRACT_DOCUMENTATION
SIGNAL_GENERATED_SURFACES = REVIEW_FAMILY_GENERATED_SURFACES
SIGNAL_REVIEWER_TEST_HARNESS = REVIEW_FAMILY_REVIEWER_TEST_HARNESS
SIGNAL_OTHER = REVIEW_FAMILY_OTHER

MAX_HISTORICAL_CLUSTERS = 5
MAX_CLUSTER_SIGNATURES = 4
MAX_CLUSTER_PRS = 8
MAX_CLUSTER_PATH_FAMILIES = 6
MAX_CLUSTER_EXAMPLES = 3
MAX_GITHUB_INVENTORY_PAGES = 10
MAX_GITHUB_REVIEW_COMMENTS = 500
MIN_PREVENTIVE_ACTION_COUNT = 2
REPORT_SCHEMA_VERSION = 1
PLANNING_SIGNAL_SCHEMA_VERSION = 1
PLANNING_RECEIPT_SCHEMA_VERSION = 1
MAX_PLANNING_CHANGED_PATHS = 200
MAX_PLANNING_REQUEST_BYTES = 16 * 1024
MAX_PLANNING_RECEIPT_BYTES = 512 * 1024
DEFAULT_PLANNING_CACHE_TTL_SECONDS = 15 * 60
MAX_PLANNING_CACHE_TTL_SECONDS = 24 * 60 * 60
DEFAULT_PLANNING_GITHUB_LIMIT = 50
MAX_PLANNING_GITHUB_DAYS = 90
MAX_PLANNING_GITHUB_PRS = 100
CONTAINMENT_REPOSITORY = "repository-local"
CONTAINMENT_EXTERNAL = "external"

SIGNAL_CATEGORY_LABELS = {
    SIGNAL_TASK_METADATA: "Task metadata",
    SIGNAL_BOUNDARY_VALIDATION: "Boundary validation",
    SIGNAL_CONTRACT_DOCUMENTATION: "Contract/documentation drift",
    SIGNAL_GENERATED_SURFACES: "Generated surfaces",
    SIGNAL_REVIEWER_TEST_HARNESS: "Reviewer/test harness quality",
    SIGNAL_OTHER: "Other recurring signals",
}

SIGNAL_CATEGORY_ORDER = tuple(SIGNAL_CATEGORY_LABELS)

_SAFE_ATTEMPT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SNAPSHOT_UPDATED_RE = re.compile(r"_Last updated: (\d{4}-\d{2}-\d{2})_")
_STATEFUL_PATH_MARKERS = (
    "controller",
    "state",
    "receipt",
    "workflow",
    "router",
    "dispatch",
    "replay",
    "lock",
)
_SOURCE_PATH_PREFIXES = (
    "app/",
    "apps/",
    "bin/",
    "cli/",
    "installer/",
    "lib/",
    "packages/",
    "scripts/",
    "src/",
)

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


def content_digest(content: bytes) -> str:
    return f"sha256:{hashlib.sha256(content).hexdigest()}"


def atomic_write_text(
    destination: Path,
    content: str,
    *,
    errors: str = "strict",
    revalidate: Any | None = None,
    mode: int | None = None,
) -> None:
    if destination.is_symlink():
        raise OSError("target is a symlink")
    if revalidate is not None:
        revalidate()
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
        if temporary_path.stat().st_dev != destination.parent.stat().st_dev:
            raise OSError("atomic update would cross filesystems")
        if revalidate is not None:
            revalidate()
        os.chmod(
            temporary_path,
            mode if mode is not None else default_text_file_mode(destination),
        )
        if revalidate is not None:
            revalidate()
        os.replace(temporary_path, destination)
        temporary_path = None
        try:
            directory_fd = os.open(destination.parent, os.O_RDONLY)
        except OSError:
            directory_fd = None
        if directory_fd is not None:
            try:
                os.fsync(directory_fd)
            except OSError:
                pass
            finally:
                os.close(directory_fd)
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass


@dataclasses.dataclass(frozen=True)
class TargetPlan:
    repository_root: Path
    requested: Path
    resolved: Path
    containment: str
    exists: bool
    existing_text: str
    before_digest: str | None
    identity: tuple[int, int] | None


def _path_is_within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _validate_existing_path_components(path: Path) -> None:
    parts = path.parts
    if not parts:
        raise ValueError("target path is empty")
    current = Path(parts[0])
    for index, part in enumerate(parts[1:], start=1):
        current /= part
        try:
            node = current.lstat()
        except FileNotFoundError:
            return
        is_final = index == len(parts) - 1
        if stat.S_ISLNK(node.st_mode):
            try:
                resolved = current.resolve(strict=True)
            except (FileNotFoundError, RuntimeError) as exc:
                raise ValueError(f"target path contains a broken symlink: {current}") from exc
            if is_final:
                raise ValueError(f"target must be a regular file, not a symlink: {current}")
            if not resolved.is_dir():
                raise ValueError(f"target parent is not a directory: {current}")
        elif not is_final and not stat.S_ISDIR(node.st_mode):
            raise ValueError(f"target parent is not a directory: {current}")


def _validate_owner(path: Path, *, label: str) -> None:
    get_euid = getattr(os, "geteuid", None)
    if get_euid is None:
        return
    node = path.stat()
    if node.st_uid != get_euid():
        raise ValueError(f"{label} is not owned by the current user: {path}")


def _nearest_existing_parent(path: Path) -> Path:
    parent = path.parent
    while True:
        try:
            node = parent.lstat()
        except FileNotFoundError:
            if parent == parent.parent:
                raise ValueError(
                    f"no existing parent directory for target: {path}"
                ) from None
            parent = parent.parent
            continue
        if stat.S_ISLNK(node.st_mode):
            raise ValueError(f"resolved target parent must not be a symlink: {parent}")
        if not stat.S_ISDIR(node.st_mode):
            raise ValueError(f"target parent is not a directory: {parent}")
        return parent


def resolve_target_plan(
    repository_root: Path,
    target: Path,
    *,
    mode: str,
    confirmed_external_target: str | None,
) -> TargetPlan:
    try:
        root = repository_root.resolve(strict=True)
    except (FileNotFoundError, RuntimeError) as exc:
        raise ValueError(f"repository root does not resolve: {repository_root}") from exc
    if not root.is_dir():
        raise ValueError(f"repository root is not a directory: {root}")

    requested = target if target.is_absolute() else root / target
    _validate_existing_path_components(requested)
    try:
        resolved = requested.resolve(strict=False)
    except RuntimeError as exc:
        raise ValueError(f"target path cannot be resolved: {requested}") from exc

    containment = (
        CONTAINMENT_REPOSITORY
        if _path_is_within(resolved, root)
        else CONTAINMENT_EXTERNAL
    )
    if containment == CONTAINMENT_EXTERNAL and mode != "update-external":
        raise ValueError(
            "target resolves outside the repository; use --update-external with "
            "an exact confirmed external target"
        )
    if mode == "update-external" and containment != CONTAINMENT_EXTERNAL:
        raise ValueError("--update-external requires a target outside the repository")
    if mode == "update-external":
        if confirmed_external_target is None:
            raise ValueError(
                "--update-external requires --confirmed-external-target with the "
                "exact resolved absolute path"
            )
        confirmation = Path(confirmed_external_target)
        if not confirmation.is_absolute() or str(confirmation) != str(resolved):
            raise ValueError(
                "--confirmed-external-target must exactly match the resolved "
                f"absolute target: {resolved}"
            )
    elif confirmed_external_target is not None:
        raise ValueError(
            "--confirmed-external-target is valid only with --update-external"
        )

    nearest_parent = _nearest_existing_parent(resolved)
    _validate_owner(nearest_parent, label="target parent")

    try:
        node = resolved.lstat()
    except FileNotFoundError:
        node = None
    if node is not None:
        if not stat.S_ISREG(node.st_mode):
            raise ValueError(f"target must be a regular file: {resolved}")
        _validate_owner(resolved, label="target")
        raw = resolved.read_bytes()
        try:
            existing_text = raw.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise ValueError(f"target is not valid UTF-8: {resolved}") from exc
        before_digest = content_digest(raw)
        identity = (node.st_dev, node.st_ino)
    else:
        existing_text = ""
        before_digest = None
        identity = None

    return TargetPlan(
        repository_root=root,
        requested=requested,
        resolved=resolved,
        containment=containment,
        exists=node is not None,
        existing_text=existing_text,
        before_digest=before_digest,
        identity=identity,
    )


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

    def planning_item(self) -> dict[str, Any]:
        signature_items = [
            {
                "summary": _one_line(_normalize_signal_text(text), limit=110),
                "count": count,
            }
            for text, count in self.signature_examples[:MAX_CLUSTER_SIGNATURES]
        ]
        pr_items = list(self.pr_numbers[:MAX_CLUSTER_PRS])
        path_items = list(self.path_families[:MAX_CLUSTER_PATH_FAMILIES])
        example_items = [
            {
                "prNumber": comment.pr_number,
                "url": _one_line(comment.pr_url, limit=500),
                "pathFamily": _path_family(comment.path),
            }
            for comment in self.examples[:MAX_CLUSTER_EXAMPLES]
        ]
        dimensions = []
        for kind, included, total in (
            ("signatures", len(signature_items), self.signature_count),
            ("prs", len(pr_items), len(self.pr_numbers)),
            ("pathFamilies", len(path_items), len(self.path_families)),
            ("examples", len(example_items), self.count),
        ):
            if included < total:
                dimensions.append(
                    {"kind": kind, "included": included, "total": total}
                )
        return {
            "familyId": self.category,
            "label": SIGNAL_CATEGORY_LABELS[self.category],
            "commentCount": self.count,
            "signatureCount": self.signature_count,
            "prNumbers": pr_items,
            "pathFamilies": path_items,
            "timeBounds": {
                "firstSeen": self.first_seen or None,
                "lastSeen": self.last_seen or None,
            },
            "representativeSignatures": signature_items,
            "exampleReferences": example_items,
            "truncation": {
                "occurred": bool(dimensions),
                "dimensions": dimensions,
            },
        }


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
    if lowered.startswith(("test/", "tests/")):
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
    if path.startswith(("test/", "tests/")):
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


def _normalize_planning_changed_paths(paths: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    normalized: set[str] = set()
    for raw in paths:
        if not isinstance(raw, str):
            raise ValueError("planning changed paths must be strings")
        value = raw.replace("\\", "/").strip("/")
        if (
            not value
            or value.startswith("../")
            or "/../" in f"/{value}/"
            or "\x00" in value
            or any(ord(character) < 32 for character in value)
        ):
            raise ValueError("planning changed path is unsafe")
        if len(value) > 500:
            raise ValueError("planning changed path exceeds 500 characters")
        normalized.add(value)
    if len(normalized) > MAX_PLANNING_CHANGED_PATHS:
        raise ValueError(
            f"planning changed paths exceed {MAX_PLANNING_CHANGED_PATHS} entries"
        )
    return tuple(sorted(normalized))


def planning_signal_categories(
    changed_paths: list[str] | tuple[str, ...],
) -> tuple[str, ...]:
    categories: set[str] = set()
    for path in _normalize_planning_changed_paths(changed_paths):
        lowered = path.casefold()
        name = Path(lowered).name
        if lowered.startswith(".trellis/tasks/") or name == "task.json":
            categories.add(SIGNAL_TASK_METADATA)
        if lowered.startswith(GENERATED_SIGNAL_PATH_PREFIXES) or lowered in GENERATED_SIGNAL_PATHS:
            categories.add(SIGNAL_GENERATED_SURFACES)
        if lowered.startswith(("test/", "tests/")):
            categories.add(SIGNAL_REVIEWER_TEST_HARNESS)
        if lowered.startswith("docs/") or lowered in {"readme.md", "changelog.md"}:
            categories.add(SIGNAL_CONTRACT_DOCUMENTATION)
        if lowered.startswith(_SOURCE_PATH_PREFIXES) or any(
            marker in name for marker in _STATEFUL_PATH_MARKERS
        ):
            categories.add(SIGNAL_BOUNDARY_VALIDATION)
    if changed_paths and not categories:
        categories.add(SIGNAL_OTHER)
    return tuple(category for category in SIGNAL_CATEGORY_ORDER if category in categories)


def _review_learning_watermark(
    comments: list[PullRequestComment],
) -> dict[str, Any]:
    ordered = sorted(
        comments,
        key=lambda comment: (
            comment.pr_number,
            comment.path,
            comment.created_at,
            comment.body,
            comment.is_resolved,
            comment.is_outdated,
        ),
    )
    digest = hashlib.sha256()
    for comment in ordered:
        row = json.dumps(
            {
                "pr": comment.pr_number,
                "path": comment.path,
                "createdAt": comment.created_at,
                "bodyDigest": content_digest(comment.body.encode("utf-8")),
                "resolved": comment.is_resolved,
                "outdated": comment.is_outdated,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        digest.update(row.encode("utf-8"))
        digest.update(b"\n")
    created_values = [
        comment.created_at
        for comment in ordered
        if _timestamp_value(comment.created_at)
    ]
    return {
        "commentCount": len(ordered),
        "latestCreatedAt": max(created_values, default=None),
        "maxPrNumber": max((comment.pr_number for comment in ordered), default=None),
        "digest": f"sha256:{digest.hexdigest()}",
    }


def _tracked_snapshot_status(
    *,
    existing_text: str | None,
    exists: bool | None,
    watermark: dict[str, Any],
) -> dict[str, Any]:
    latest = watermark.get("latestCreatedAt")
    base = {
        "lastUpdatedDate": None,
        "latestEvidenceAt": latest if isinstance(latest, str) else None,
        "updateRecommended": False,
    }
    if exists is False:
        return {
            **base,
            "status": "missing",
            "updateRecommended": True,
            "reason": "tracked snapshot is missing",
        }
    if existing_text is None or exists is None:
        return {
            **base,
            "status": "unknown",
            "reason": "tracked snapshot was not inspected",
        }
    start = existing_text.find(MANAGED_START)
    end = existing_text.find(MANAGED_END, start + len(MANAGED_START))
    if start < 0 or end < 0:
        return {
            **base,
            "status": "missing",
            "updateRecommended": True,
            "reason": "tracked snapshot has no complete managed learning block",
        }
    match = _SNAPSHOT_UPDATED_RE.search(existing_text, start, end)
    if match is None:
        return {
            **base,
            "status": "unknown",
            "reason": "tracked snapshot update date is unavailable",
        }
    try:
        snapshot_date = dt.date.fromisoformat(match.group(1))
    except ValueError:
        return {
            **base,
            "status": "unknown",
            "reason": "tracked snapshot update date is invalid",
        }
    base["lastUpdatedDate"] = snapshot_date.isoformat()
    if not isinstance(latest, str) or not _timestamp_value(latest):
        return {
            **base,
            "status": "unknown",
            "reason": "current GitHub evidence is unavailable",
        }
    latest_date = dt.datetime.fromtimestamp(
        _timestamp_value(latest),
        tz=dt.timezone.utc,
    ).date()
    if latest_date > snapshot_date:
        return {
            **base,
            "status": "stale",
            "updateRecommended": True,
            "reason": "newer GitHub review evidence exists",
        }
    return {
        **base,
        "status": "current",
        "reason": "tracked snapshot covers the latest observed review evidence",
    }


def build_review_learning_signal(
    comments: list[PullRequestComment],
    review_window: CopilotReviewWindow,
    *,
    changed_paths: list[str] | tuple[str, ...],
    requested: bool,
    source: str = "live",
    status_override: str | None = None,
    limitations: tuple[str, ...] = (),
    now: dt.datetime | None = None,
    snapshot_text: str | None = None,
    snapshot_exists: bool | None = None,
) -> dict[str, Any]:
    if source not in {"live", "cached", "stale", "unavailable", "not-requested"}:
        raise ValueError(f"unsupported review-learning source: {source}")
    paths = _normalize_planning_changed_paths(changed_paths)
    selected_categories = planning_signal_categories(paths)
    actionable, historical = partition_review_comments(comments)
    clusters = cluster_historical_comments(historical)
    shown_clusters = clusters[:MAX_HISTORICAL_CLUSTERS]
    applicable_clusters = [
        cluster for cluster in clusters if cluster.category in selected_categories
    ][:MAX_HISTORICAL_CLUSTERS]

    limitation_values = list(dict.fromkeys(limitations))
    if review_window.truncated:
        limitation_values.append("github-window-truncated")
    if len(clusters) > len(shown_clusters):
        limitation_values.append("historical-clusters-truncated")
    evidence_truncated = any(
        cluster.planning_item()["truncation"]["occurred"]
        for cluster in shown_clusters
    )
    if evidence_truncated:
        limitation_values.append("cluster-evidence-truncated")
    limitation_values = list(dict.fromkeys(limitation_values))

    if status_override is not None:
        status = status_override
    elif not requested:
        status = "not-requested"
    elif source in {"cached", "stale", "unavailable"}:
        status = source
    elif limitation_values:
        status = "truncated"
    else:
        status = "live"
    if status not in {"live", "cached", "stale", "truncated", "unavailable", "not-requested"}:
        raise ValueError(f"unsupported review-learning status: {status}")

    risk_questions = [
        {
            "familyId": cluster.category,
            "question": SIGNAL_PREVENTIVE_ACTIONS[cluster.category],
        }
        for cluster in applicable_clusters
        if cluster.count >= MIN_PREVENTIVE_ACTION_COUNT
        and cluster.category in SIGNAL_PREVENTIVE_ACTIONS
    ]
    observed_at = now or dt.datetime.now(dt.timezone.utc)
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=dt.timezone.utc)
    observed_at = observed_at.astimezone(dt.timezone.utc)
    watermark = _review_learning_watermark(comments)
    tracked_snapshot = _tracked_snapshot_status(
        existing_text=snapshot_text,
        exists=snapshot_exists,
        watermark=watermark,
    )
    latest_timestamp = watermark["latestCreatedAt"]
    evidence_age_seconds: int | None = None
    if isinstance(latest_timestamp, str) and _timestamp_value(latest_timestamp):
        evidence_age_seconds = max(
            0,
            int(observed_at.timestamp() - _timestamp_value(latest_timestamp)),
        )
    return {
        "schemaVersion": PLANNING_SIGNAL_SCHEMA_VERSION,
        "status": status,
        "evidence": {
            "source": source,
            "prsInspected": review_window.prs_inspected,
            "commentCount": len(comments),
            "actionableCommentCount": len(actionable),
            "cutoff": review_window.cutoff,
            "observedAt": observed_at.isoformat().replace("+00:00", "Z"),
            "ageSeconds": evidence_age_seconds,
            "watermark": watermark,
        },
        "selection": {
            "changedPaths": list(paths),
            "pathFamilies": sorted({_path_family(path) for path in paths}),
            "familyIds": list(selected_categories),
        },
        "trackedSnapshot": tracked_snapshot,
        "historicalClusters": [cluster.planning_item() for cluster in shown_clusters],
        "applicableClusters": [
            cluster.planning_item() for cluster in applicable_clusters
        ],
        "riskQuestions": risk_questions,
        "truncation": {
            "occurred": bool(limitation_values),
            "totalClusterCount": len(clusters),
            "returnedClusterCount": len(shown_clusters),
        },
        "limitations": limitation_values,
        "confidenceCredit": {
            "granted": False,
            "reason": "historical learning is advisory evidence only",
        },
    }


def unavailable_review_learning_signal(
    *,
    changed_paths: list[str] | tuple[str, ...],
    limitation: str,
    snapshot_text: str | None = None,
    snapshot_exists: bool | None = None,
) -> dict[str, Any]:
    return build_review_learning_signal(
        [],
        CopilotReviewWindow((), 0, None, False),
        changed_paths=changed_paths,
        requested=True,
        source="unavailable",
        status_override="unavailable",
        limitations=(_one_line(limitation, limit=300),),
        snapshot_text=snapshot_text,
        snapshot_exists=snapshot_exists,
    )


def _planning_request_fingerprint(
    *,
    repository_id: str,
    attempt_id: str,
    changed_paths: tuple[str, ...],
    request: dict[str, Any],
    snapshot_text: str | None,
    snapshot_exists: bool | None,
) -> str:
    payload = {
        "schemaVersion": PLANNING_RECEIPT_SCHEMA_VERSION,
        "repository": repository_id,
        "attemptId": attempt_id,
        "changedPaths": list(changed_paths),
        "request": request,
        "trackedSnapshot": {
            "exists": snapshot_exists,
            "digest": (
                content_digest(snapshot_text.encode("utf-8"))
                if snapshot_text is not None
                else None
            ),
        },
    }
    try:
        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("planning request must be bounded JSON data") from exc
    if len(encoded) > MAX_PLANNING_REQUEST_BYTES:
        raise ValueError(
            f"planning request exceeds {MAX_PLANNING_REQUEST_BYTES} bytes"
        )
    return content_digest(encoded)


def _validate_private_artifact_directory(path: Path, *, repo_root: Path) -> Path:
    if not path.is_absolute():
        raise ValueError("review artifact directory must be absolute")
    root = repo_root.resolve(strict=True)
    resolved = path.expanduser().resolve(strict=False)
    if _path_is_within(resolved, root):
        raise ValueError("review artifact directory must be outside the repository")
    try:
        node = path.lstat()
    except FileNotFoundError:
        path.mkdir(mode=0o700, parents=True, exist_ok=False)
        node = path.lstat()
    if stat.S_ISLNK(node.st_mode) or not stat.S_ISDIR(node.st_mode):
        raise ValueError("review artifact directory must be a real directory")
    _validate_owner(path, label="review artifact directory")
    if stat.S_IMODE(node.st_mode) & 0o077:
        raise ValueError("review artifact directory must use private permissions")
    return resolved


def _load_planning_receipt(path: Path) -> dict[str, Any] | None:
    try:
        node = path.lstat()
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(node.st_mode) or not stat.S_ISREG(node.st_mode):
        return None
    if node.st_size > MAX_PLANNING_RECEIPT_BYTES or stat.S_IMODE(node.st_mode) & 0o077:
        return None
    get_euid = getattr(os, "geteuid", None)
    if get_euid is not None and node.st_uid != get_euid():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8", errors="strict"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _valid_cached_planning_signal(value: Any) -> bool:
    if not isinstance(value, dict) or value.get("schemaVersion") != PLANNING_SIGNAL_SCHEMA_VERSION:
        return False
    if value.get("status") not in {
        "live",
        "cached",
        "stale",
        "truncated",
        "unavailable",
        "not-requested",
    }:
        return False
    evidence = value.get("evidence")
    confidence = value.get("confidenceCredit")
    snapshot = value.get("trackedSnapshot")
    return (
        isinstance(evidence, dict)
        and evidence.get("source")
        in {"live", "cached", "stale", "unavailable", "not-requested"}
        and isinstance(evidence.get("watermark"), dict)
        and isinstance(value.get("limitations"), list)
        and isinstance(value.get("truncation"), dict)
        and isinstance(snapshot, dict)
        and snapshot.get("status") in {"current", "stale", "missing", "unknown"}
        and isinstance(snapshot.get("updateRecommended"), bool)
        and isinstance(confidence, dict)
        and confidence.get("granted") is False
    )


def _cache_signal(
    signal: dict[str, Any],
    *,
    source: str,
    status: str,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    copied = json.loads(json.dumps(signal, sort_keys=True))
    copied["status"] = status
    copied["evidence"]["source"] = source
    if now is not None:
        latest = copied["evidence"]["watermark"].get("latestCreatedAt")
        copied["evidence"]["observedAt"] = now.isoformat().replace("+00:00", "Z")
        copied["evidence"]["ageSeconds"] = (
            max(0, int(now.timestamp() - _timestamp_value(latest)))
            if isinstance(latest, str) and _timestamp_value(latest)
            else None
        )
    copied["confidenceCredit"] = {
        "granted": False,
        "reason": "historical learning is advisory evidence only",
    }
    return copied


def collect_review_learning_signal_once(
    *,
    repo_root: Path,
    repository_id: str,
    attempt_id: str,
    changed_paths: list[str] | tuple[str, ...],
    request: dict[str, Any],
    fetch_window: Callable[[], CopilotReviewWindow],
    artifact_root: Path | None = None,
    ttl_seconds: int = DEFAULT_PLANNING_CACHE_TTL_SECONDS,
    now: dt.datetime | None = None,
    snapshot_text: str | None = None,
    snapshot_exists: bool | None = None,
) -> dict[str, Any]:
    if not repository_id or len(repository_id) > 200 or any(
        ord(character) < 32 for character in repository_id
    ):
        raise ValueError("repository identity is invalid")
    if not _SAFE_ATTEMPT_ID_RE.fullmatch(attempt_id):
        raise ValueError("review attempt ID is invalid")
    if isinstance(ttl_seconds, bool) or not 1 <= ttl_seconds <= MAX_PLANNING_CACHE_TTL_SECONDS:
        raise ValueError(
            f"planning cache TTL must be between 1 and {MAX_PLANNING_CACHE_TTL_SECONDS} seconds"
        )
    paths = _normalize_planning_changed_paths(changed_paths)
    fingerprint = _planning_request_fingerprint(
        repository_id=repository_id,
        attempt_id=attempt_id,
        changed_paths=paths,
        request=request,
        snapshot_text=snapshot_text,
        snapshot_exists=snapshot_exists,
    )
    current = now or dt.datetime.now(dt.timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=dt.timezone.utc)
    current = current.astimezone(dt.timezone.utc)

    receipt_path: Path | None = None
    cached: dict[str, Any] | None = None
    if artifact_root is not None:
        artifact_dir = _validate_private_artifact_directory(
            artifact_root,
            repo_root=repo_root,
        )
        repository_key = hashlib.sha256(repository_id.encode("utf-8")).hexdigest()[:24]
        learning_dir = _validate_private_artifact_directory(
            artifact_dir / "review-learnings",
            repo_root=repo_root,
        )
        receipt_dir = _validate_private_artifact_directory(
            learning_dir / repository_key,
            repo_root=repo_root,
        )
        receipt_path = receipt_dir / f"{attempt_id}.json"
        cached = _load_planning_receipt(receipt_path)
        if cached is not None and (
            cached.get("schemaVersion") != PLANNING_RECEIPT_SCHEMA_VERSION
            or cached.get("kind") != "review-learning-attempt"
            or cached.get("repository") != repository_id
            or cached.get("attemptId") != attempt_id
            or cached.get("requestFingerprint") != fingerprint
            or not _valid_cached_planning_signal(cached.get("signal"))
            or cached.get("githubWatermark")
            != cached["signal"]["evidence"]["watermark"]
        ):
            cached = None

    stale_signal: dict[str, Any] | None = None
    if cached is not None:
        expires_at = cached.get("expiresAt")
        try:
            expires = dt.datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
        except ValueError:
            cached = None
        else:
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=dt.timezone.utc)
            if current < expires.astimezone(dt.timezone.utc):
                return {
                    **cached,
                    "cache": {"status": "hit", "path": str(receipt_path)},
                }
            stale_signal = cached["signal"]

    try:
        window = fetch_window()
        if not isinstance(window, CopilotReviewWindow):
            raise TypeError("review-learning collector returned an invalid window")
        comments = list(window.comments)
        signal = build_review_learning_signal(
            comments,
            window,
            changed_paths=paths,
            requested=True,
            now=current,
            snapshot_text=snapshot_text,
            snapshot_exists=snapshot_exists,
        )
        cache_status = "miss" if cached is None else "refreshed"
    except (CommandError, OSError, RuntimeError, TypeError, json.JSONDecodeError) as exc:
        limitation = f"live review-learning scan unavailable: {_one_line(str(exc), limit=240)}"
        if stale_signal is not None:
            signal = _cache_signal(
                stale_signal,
                source="stale",
                status="stale",
                now=current,
            )
            signal["limitations"] = list(
                dict.fromkeys([*signal.get("limitations", []), limitation])
            )
            signal["truncation"]["occurred"] = True
            cache_status = "stale"
        else:
            signal = unavailable_review_learning_signal(
                changed_paths=paths,
                limitation=limitation,
                snapshot_text=snapshot_text,
                snapshot_exists=snapshot_exists,
            )
            cache_status = "unavailable"

    expires = current + dt.timedelta(seconds=ttl_seconds)
    receipt = {
        "schemaVersion": PLANNING_RECEIPT_SCHEMA_VERSION,
        "kind": "review-learning-attempt",
        "repository": repository_id,
        "attemptId": attempt_id,
        "requestFingerprint": fingerprint,
        "githubWatermark": signal["evidence"]["watermark"],
        "createdAt": current.isoformat().replace("+00:00", "Z"),
        "expiresAt": expires.isoformat().replace("+00:00", "Z"),
        "signal": signal,
    }
    if receipt_path is not None:
        encoded = json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n"
        if len(encoded.encode("utf-8")) > MAX_PLANNING_RECEIPT_BYTES:
            raise ValueError(
                f"review-learning receipt exceeds {MAX_PLANNING_RECEIPT_BYTES} bytes"
            )
        atomic_write_text(receipt_path, encoded, mode=0o600)
    return {
        **receipt,
        "cache": {
            "status": cache_status,
            "path": str(receipt_path) if receipt_path else None,
        },
    }


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
    page_count = 0
    while True:
        page_count += 1
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
        if page_count >= MAX_GITHUB_INVENTORY_PAGES:
            return prs, cutoff, True
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
) -> tuple[list[PullRequestComment], bool]:
    query = """
query($owner:String!, $name:String!, $number:Int!) {
  repository(owner:$owner, name:$name) {
    pullRequest(number:$number) {
      reviewThreads(first:100) {
        pageInfo { hasNextPage }
        nodes {
          isResolved
          isOutdated
          path
          comments(first:50) {
            pageInfo { hasNextPage }
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
    truncated = False
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
        thread_connection = _dig(
            payload,
            "data",
            "repository",
            "pullRequest",
            "reviewThreads",
        )
        if not isinstance(thread_connection, dict):
            continue
        page_info = thread_connection.get("pageInfo")
        if isinstance(page_info, dict) and page_info.get("hasNextPage"):
            truncated = True
        thread_nodes = thread_connection.get("nodes")
        if not isinstance(thread_nodes, list):
            continue
        for thread in thread_nodes:
            thread_obj = _as_dict(thread)
            comments_payload = thread_obj.get("comments")
            if not isinstance(comments_payload, dict):
                continue
            comment_page_info = comments_payload.get("pageInfo")
            if isinstance(comment_page_info, dict) and comment_page_info.get("hasNextPage"):
                truncated = True
            comment_nodes = comments_payload.get("nodes")
            if not isinstance(comment_nodes, list):
                continue
            for comment in comment_nodes:
                comment_obj = _as_dict(comment)
                author = comment_obj.get("author")
                if not isinstance(author, dict) or author.get("login") != COPILOT_LOGIN:
                    continue
                if len(comments) >= MAX_GITHUB_REVIEW_COMMENTS:
                    return comments, True
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
    return comments, truncated


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
    comments, comments_truncated = _copilot_comments_for_prs(
        repo_root,
        owner=owner,
        name=name,
        prs=prs,
    )
    return CopilotReviewWindow(
        tuple(comments),
        len(prs),
        cutoff,
        truncated or comments_truncated,
    )


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
    comments, comments_truncated = _copilot_comments_for_prs(
        repo_root,
        owner=owner,
        name=name,
        prs=prs,
    )
    return CopilotReviewWindow(tuple(comments), len(prs), None, comments_truncated)


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


def render_target_update(existing: str, block: str, *, target: Path) -> str:
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
    return updated


def apply_target_update(
    plan: TargetPlan,
    updated: str,
    *,
    mode: str,
    confirmed_external_target: str | None,
) -> bool:
    updated_bytes = updated.encode("utf-8", errors="strict")
    if plan.before_digest == content_digest(updated_bytes):
        return False

    def revalidate() -> None:
        current = resolve_target_plan(
            plan.repository_root,
            plan.requested,
            mode=mode,
            confirmed_external_target=confirmed_external_target,
        )
        if current.resolved != plan.resolved:
            raise OSError("target resolution changed before atomic replacement")
        if current.containment != plan.containment:
            raise OSError("target containment changed before atomic replacement")
        if current.identity != plan.identity:
            raise OSError("target identity changed before atomic replacement")
        if current.before_digest != plan.before_digest:
            raise OSError("target content changed before atomic replacement")

    revalidate()
    plan.resolved.parent.mkdir(parents=True, exist_ok=True)
    revalidate()
    atomic_write_text(
        plan.resolved,
        updated,
        errors="strict",
        revalidate=revalidate,
    )
    return True


def _report_payload(
    *,
    mode: str,
    plan: TargetPlan,
    findings: list[Finding],
    comments: list[PullRequestComment],
    review_window: CopilotReviewWindow,
    proposed_changes: int,
    applied_changes: int,
    write_status: str,
    wrote: bool,
    before_digest: str | None,
    after_digest: str | None,
    review_learning: dict[str, Any],
    reason: str | None = None,
) -> dict[str, Any]:
    return {
        "schemaVersion": REPORT_SCHEMA_VERSION,
        "mode": mode,
        "repositoryRoot": str(plan.repository_root),
        "target": {
            "requested": str(plan.requested),
            "resolved": str(plan.resolved),
            "containment": plan.containment,
            "exists": plan.exists,
        },
        "externalAuthorization": {
            "decision": (
                "review-learnings.external-target"
                if mode == "update-external"
                else None
            ),
            "confirmed": mode == "update-external",
            "resolvedTarget": (
                str(plan.resolved) if mode == "update-external" else None
            ),
        },
        "findings": {
            "count": len(findings),
            "items": [
                {
                    "category": finding.category,
                    "path": finding.path,
                    "line": finding.lineno,
                    "detail": finding.detail,
                    "recommendation": finding.recommendation,
                }
                for finding in findings
            ],
        },
        "github": {
            "comments": len(comments),
            "prsInspected": review_window.prs_inspected,
            "cutoff": review_window.cutoff,
            "truncated": review_window.truncated,
        },
        "reviewLearning": review_learning,
        "changes": {
            "proposed": proposed_changes,
            "applied": applied_changes,
        },
        "write": {
            "status": write_status,
            "occurred": wrote,
            "reason": reason,
        },
        "digests": {
            "before": before_digest,
            "after": after_digest,
        },
    }


def _print_human_report(report: dict[str, Any]) -> None:
    target = report["target"]
    changes = report["changes"]
    write = report["write"]
    print(f"[sd-review-learnings:report] mode: {report['mode']}")
    print(f"[sd-review-learnings:report] repository root: {report['repositoryRoot']}")
    print(f"[sd-review-learnings:report] resolved target: {target['resolved']}")
    print(f"[sd-review-learnings:report] containment: {target['containment']}")
    print(f"[sd-review-learnings:report] findings: {report['findings']['count']}")
    learning = report["reviewLearning"]
    age = learning["evidence"].get("ageSeconds")
    age_text = "age unavailable" if age is None else f"age {age}s"
    print(
        "[sd-review-learnings:report] planning signal: "
        f"{learning['status']} from {learning['evidence']['source']}; "
        f"{len(learning['applicableClusters'])} applicable family/families; "
        f"{age_text}"
    )
    if learning["limitations"]:
        print(
            "[sd-review-learnings:report] planning limitations: "
            + ", ".join(learning["limitations"])
        )
    snapshot = learning["trackedSnapshot"]
    print(
        "[sd-review-learnings:report] tracked snapshot: "
        f"{snapshot['status']}; {snapshot['reason']}"
    )
    print(
        "[sd-review-learnings:report] changes: "
        f"{changes['proposed']} proposed, {changes['applied']} applied"
    )
    write_line = (
        "[sd-review-learnings:report] write: "
        f"{write['status']} (occurred={'yes' if write['occurred'] else 'no'})"
    )
    if write["reason"]:
        write_line += f"; {write['reason']}"
    print(write_line)


def _print_report(report: dict[str, Any], *, json_output: bool) -> None:
    if json_output:
        print(json.dumps(report, sort_keys=True))
    else:
        _print_human_report(report)


def _print_early_failure(
    *,
    args: argparse.Namespace,
    mode: str,
    phase: str,
    reason: str,
) -> None:
    if not args.json:
        print(f"[sd-review-learnings:{phase}] {reason}", file=sys.stderr)
        return
    try:
        root = args.repo_root.resolve(strict=False)
        requested = args.target if args.target.is_absolute() else root / args.target
        resolved_path = requested.resolve(strict=False)
        resolved: str | None = str(resolved_path)
        containment = (
            CONTAINMENT_REPOSITORY
            if _path_is_within(resolved_path, root)
            else CONTAINMENT_EXTERNAL
        )
    except (OSError, RuntimeError, ValueError):
        root = args.repo_root
        requested = args.target
        resolved = None
        containment = "invalid"
    report = {
        "schemaVersion": REPORT_SCHEMA_VERSION,
        "mode": mode,
        "repositoryRoot": str(root),
        "target": {
            "requested": str(requested),
            "resolved": resolved,
            "containment": containment,
            "exists": None,
        },
        "externalAuthorization": {
            "decision": (
                "review-learnings.external-target"
                if mode == "update-external"
                else None
            ),
            "confirmed": False,
            "resolvedTarget": None,
        },
        "findings": {"count": 0, "items": []},
        "github": {
            "comments": 0,
            "prsInspected": 0,
            "cutoff": None,
            "truncated": False,
        },
        "reviewLearning": unavailable_review_learning_signal(
            changed_paths=(),
            limitation=reason,
        ),
        "changes": {"proposed": 0, "applied": 0},
        "write": {
            "status": "skipped" if mode == "scan" else "failed",
            "occurred": False,
            "reason": reason,
        },
        "digests": {"before": None, "after": None},
    }
    print(json.dumps(report, sort_keys=True))


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
    parser.add_argument("--target", type=Path, default=DEFAULT_TARGET, help="Markdown file to inspect or update.")
    update_group = parser.add_mutually_exclusive_group()
    update_group.add_argument(
        "--update",
        action="store_true",
        help="Write/update a repository-contained managed learnings block.",
    )
    update_group.add_argument(
        "--update-external",
        action="store_true",
        help="Exceptionally update an explicitly confirmed external target.",
    )
    parser.add_argument(
        "--confirmed-external-target",
        metavar="ABSOLUTE_PATH",
        help=(
            "Exact resolved external path recorded after structured confirmation; "
            "valid only with --update-external."
        ),
    )
    parser.add_argument("--dry-run", action="store_true", help="Print the updated markdown instead of writing it.")
    parser.add_argument("--json", action="store_true", help="Print one structured final report.")
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
        "--planning-attempt",
        metavar="ID",
        help=(
            "Emit one typed review-planning receipt for this attempt instead of "
            "rendering or updating the durable Markdown snapshot."
        ),
    )
    parser.add_argument(
        "--review-artifact-root",
        type=Path,
        help=(
            "Optional absolute private review-artifact directory used to reuse "
            "the exact planning receipt within --planning-attempt."
        ),
    )
    parser.add_argument(
        "--planning-cache-ttl",
        type=int,
        default=DEFAULT_PLANNING_CACHE_TTL_SECONDS,
        help=(
            "Private planning receipt lifetime in seconds; valid only with "
            "--planning-attempt."
        ),
    )
    parser.add_argument(
        "--env-prefix",
        action="append",
        help="Environment-variable prefix to require help coverage for. Repeat to override defaults.",
    )
    parser.add_argument("--allow", metavar="REASON", help="Return success even when findings are present.")
    return parser


def _planning_argument_error(args: argparse.Namespace) -> str | None:
    if args.review_artifact_root is not None and args.planning_attempt is None:
        return "--review-artifact-root requires --planning-attempt"
    if (
        args.planning_cache_ttl != DEFAULT_PLANNING_CACHE_TTL_SECONDS
        and args.planning_attempt is None
    ):
        return "--planning-cache-ttl requires --planning-attempt"
    if args.planning_attempt is None:
        return None
    if not args.json:
        return "--planning-attempt requires --json"
    if args.update or args.update_external or args.dry_run or args.allow is not None:
        return "--planning-attempt cannot be combined with update, dry-run, or allow modes"
    if not args.github_repo:
        return "--planning-attempt requires --github-repo OWNER/REPO"
    if not args.github_days and not args.github_pr:
        return "--planning-attempt requires --github-days or --github-pr"
    if args.github_days > MAX_PLANNING_GITHUB_DAYS:
        return f"--planning-attempt limits --github-days to {MAX_PLANNING_GITHUB_DAYS}"
    if len(set(args.github_pr)) > MAX_PLANNING_GITHUB_PRS:
        return f"--planning-attempt limits --github-pr to {MAX_PLANNING_GITHUB_PRS} unique values"
    if args.github_limit > MAX_PLANNING_GITHUB_PRS:
        return f"--planning-attempt limits --github-limit to {MAX_PLANNING_GITHUB_PRS}"
    return None


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    mode = "update-external" if args.update_external else "update" if args.update else "scan"
    planning_error = _planning_argument_error(args)
    if planning_error is not None:
        _print_early_failure(
            args=args,
            mode=mode,
            phase="setup",
            reason=planning_error,
        )
        return 2
    if args.dry_run and (args.update or args.update_external):
        _print_early_failure(
            args=args,
            mode=mode,
            phase="setup",
            reason="--dry-run cannot be combined with an update mode",
        )
        return 2
    if args.dry_run and args.json:
        _print_early_failure(
            args=args,
            mode=mode,
            phase="setup",
            reason="--dry-run Markdown preview cannot be combined with --json",
        )
        return 2
    if args.allow is not None and not args.allow.strip():
        _print_early_failure(
            args=args,
            mode=mode,
            phase="setup",
            reason="--allow requires a non-empty reason",
        )
        return 2
    if args.github_days < 0:
        _print_early_failure(
            args=args,
            mode=mode,
            phase="setup",
            reason="--github-days must be non-negative",
        )
        return 2
    if args.github_limit < 0:
        _print_early_failure(
            args=args,
            mode=mode,
            phase="setup",
            reason="--github-limit must be non-negative",
        )
        return 2
    if args.github_days and args.github_pr:
        _print_early_failure(
            args=args,
            mode=mode,
            phase="setup",
            reason="--github-days and --github-pr are mutually exclusive",
        )
        return 2
    if any(number < 1 for number in args.github_pr):
        _print_early_failure(
            args=args,
            mode=mode,
            phase="setup",
            reason="--github-pr must be positive",
        )
        return 2

    try:
        plan = resolve_target_plan(
            args.repo_root,
            args.target,
            mode=mode,
            confirmed_external_target=args.confirmed_external_target,
        )
    except (OSError, ValueError) as exc:
        _print_early_failure(
            args=args,
            mode=mode,
            phase="target",
            reason=str(exc),
        )
        return 2

    repo_root = plan.repository_root
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
        changed_paths = tuple(sorted(_parse_diff(diff_text)[0]))
        findings = (
            []
            if args.planning_attempt is not None
            else extract_findings(diff_text, repo_root, env_prefixes=env_prefixes)
        )
    except (
        CommandError,
        OSError,
        RuntimeError,
        json.JSONDecodeError,
    ) as exc:
        report = _report_payload(
            mode=mode,
            plan=plan,
            findings=[],
            comments=[],
            review_window=CopilotReviewWindow((), 0, None, False),
            proposed_changes=0,
            applied_changes=0,
            write_status="skipped" if mode == "scan" else "failed",
            wrote=False,
            before_digest=plan.before_digest,
            after_digest=plan.before_digest,
            review_learning=unavailable_review_learning_signal(
                changed_paths=(),
                limitation=f"findings scan failed: {exc}",
                snapshot_text=plan.existing_text,
                snapshot_exists=plan.exists,
            ),
            reason=f"findings scan failed: {exc}",
        )
        if args.json:
            _print_report(report, json_output=True)
        else:
            print(f"[sd-review-learnings:findings] {exc}", file=sys.stderr)
            _print_report(report, json_output=False)
        return 2

    if args.planning_attempt is not None:
        assert args.github_repo is not None
        effective_limit = args.github_limit or DEFAULT_PLANNING_GITHUB_LIMIT

        def fetch_planning_window() -> CopilotReviewWindow:
            if args.github_pr:
                return fetch_copilot_review_for_prs(
                    repo_root,
                    pr_numbers=args.github_pr,
                    github_repo=args.github_repo,
                )
            return fetch_recent_copilot_review_window(
                repo_root,
                days=args.github_days,
                limit=effective_limit,
                github_repo=args.github_repo,
            )

        request = {
            "githubDays": args.github_days,
            "githubLimit": effective_limit if args.github_days else 0,
            "githubPrs": list(dict.fromkeys(args.github_pr)),
        }
        try:
            receipt = collect_review_learning_signal_once(
                repo_root=repo_root,
                repository_id=args.github_repo.strip(),
                attempt_id=args.planning_attempt,
                changed_paths=changed_paths,
                request=request,
                fetch_window=fetch_planning_window,
                artifact_root=args.review_artifact_root,
                ttl_seconds=args.planning_cache_ttl,
                snapshot_text=plan.existing_text,
                snapshot_exists=plan.exists,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            _print_early_failure(
                args=args,
                mode=mode,
                phase="planning",
                reason=str(exc),
            )
            return 2
        print(json.dumps(receipt, sort_keys=True))
        return 0

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
        report = _report_payload(
            mode=mode,
            plan=plan,
            findings=findings,
            comments=[],
            review_window=CopilotReviewWindow((), 0, None, False),
            proposed_changes=0,
            applied_changes=0,
            write_status="skipped" if mode == "scan" else "failed",
            wrote=False,
            before_digest=plan.before_digest,
            after_digest=plan.before_digest,
            review_learning=unavailable_review_learning_signal(
                changed_paths=changed_paths,
                limitation=f"GitHub scan failed: {exc}",
                snapshot_text=plan.existing_text,
                snapshot_exists=plan.exists,
            ),
            reason=f"GitHub scan failed: {exc}",
        )
        if args.json:
            _print_report(report, json_output=True)
        else:
            print(f"[sd-review-learnings:github] {exc}", file=sys.stderr)
            _print_report(report, json_output=False)
        return 2

    comments = list(review_window.comments)
    review_learning = build_review_learning_signal(
        comments,
        review_window,
        changed_paths=changed_paths,
        requested=bool(args.github_days or args.github_pr),
        source="live" if (args.github_days or args.github_pr) else "not-requested",
        snapshot_text=plan.existing_text,
        snapshot_exists=plan.exists,
    )
    if not args.json:
        for finding in findings:
            print(finding.render())
    if (args.github_days or args.github_pr) and not args.json:
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
                "[sd-review-learnings:github] warning: GitHub evidence collection "
                "was truncated by configured safety bounds",
                file=sys.stderr,
            )

    block = render_managed_block(findings, comments)
    try:
        updated = render_target_update(plan.existing_text, block, target=plan.resolved)
    except ValueError as exc:
        report = _report_payload(
            mode=mode,
            plan=plan,
            findings=findings,
            comments=comments,
            review_window=review_window,
            proposed_changes=0,
            applied_changes=0,
            write_status="skipped" if mode == "scan" else "failed",
            wrote=False,
            before_digest=plan.before_digest,
            after_digest=plan.before_digest,
            review_learning=review_learning,
            reason=str(exc),
        )
        if not args.json:
            print(f"[sd-review-learnings:update] {exc}", file=sys.stderr)
        _print_report(report, json_output=args.json)
        return 2

    updated_digest = content_digest(updated.encode("utf-8", errors="strict"))
    proposed_changes = int(updated_digest != plan.before_digest)

    if args.update or args.update_external:
        try:
            wrote = apply_target_update(
                plan,
                updated,
                mode=mode,
                confirmed_external_target=args.confirmed_external_target,
            )
        except (OSError, ValueError) as exc:
            report = _report_payload(
                mode=mode,
                plan=plan,
                findings=findings,
                comments=comments,
                review_window=review_window,
                proposed_changes=proposed_changes,
                applied_changes=0,
                write_status="failed",
                wrote=False,
                before_digest=plan.before_digest,
                after_digest=plan.before_digest,
                review_learning=review_learning,
                reason=str(exc),
            )
            if not args.json:
                print(f"[sd-review-learnings:update] {exc}", file=sys.stderr)
            _print_report(report, json_output=args.json)
            return 2
        report = _report_payload(
            mode=mode,
            plan=plan,
            findings=findings,
            comments=comments,
            review_window=review_window,
            proposed_changes=proposed_changes,
            applied_changes=int(wrote),
            write_status="applied" if wrote else "unchanged",
            wrote=wrote,
            before_digest=plan.before_digest,
            after_digest=updated_digest,
            review_learning=review_learning,
        )
        if not args.json:
            shown_target: Path = plan.resolved
            if plan.containment == CONTAINMENT_REPOSITORY:
                shown_target = plan.resolved.relative_to(repo_root)
            print(
                f"[sd-review-learnings:OK] "
                f"{'updated' if wrote else 'unchanged'} {shown_target}"
            )
        _print_report(report, json_output=args.json)
        return 0

    report = _report_payload(
        mode=mode,
        plan=plan,
        findings=findings,
        comments=comments,
        review_window=review_window,
        proposed_changes=proposed_changes,
        applied_changes=0,
        write_status="preview" if args.dry_run else "skipped",
        wrote=False,
        before_digest=plan.before_digest,
        after_digest=updated_digest,
        review_learning=review_learning,
        reason="dry-run preview" if args.dry_run else "scan mode is read-only",
    )
    _print_report(report, json_output=args.json)
    if args.dry_run:
        print(updated, end="" if updated.endswith("\n") else "\n")
        return 0

    if findings:
        if args.allow is not None:
            if not args.json:
                print(f"[sd-review-learnings:OK] bypassed via --allow: {args.allow}")
            return 0
        return 1

    if not args.json:
        print("[sd-review-learnings:OK] no local review-cycle findings detected")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
