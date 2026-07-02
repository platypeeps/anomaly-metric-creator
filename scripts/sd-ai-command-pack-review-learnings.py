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
import subprocess
import sys
from pathlib import Path
from typing import Any


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


@dataclasses.dataclass(frozen=True)
class AddedLine:
    path: str
    lineno: int
    content: str


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
        location = f"{self.path}:{self.lineno}" if self.lineno else self.path
        return (
            f"- **{self.category}** `{location}`: {self.detail} "
            f"Recommendation: {self.recommendation}"
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

    def markdown_item(self) -> str:
        state = "current" if not self.is_resolved and not self.is_outdated else "historical"
        body = _one_line(self.body, limit=220)
        return f"- **{state}** PR #{self.pr_number} `{self.path}`: {body} ({self.pr_url})"


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
    first_line = text.splitlines()[0] if text.splitlines() else ""
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
        rf"\$(?:\{{((?:{prefix_pattern})_[A-Z0-9_]+)\}}|((?:{prefix_pattern})_[A-Z0-9_]+)(?![A-Za-z0-9_]))"
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

    for line in added_lines:
        shell_like = _is_shell_like(line.path, repo_root)
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


def _run_git(args: list[str], repo_root: Path, *, accept_one: bool = False) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    allowed = {0, 1} if accept_one else {0}
    if result.returncode not in allowed:
        raise RuntimeError(result.stderr.strip() or "git command failed")
    return result.stdout


def _git_diff(base_ref: str, repo_root: Path) -> str:
    return _run_git(["diff", "--no-ext-diff", f"{base_ref}...HEAD"], repo_root)


def _run_git_optional(args: list[str], repo_root: Path) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def _git_ref_exists(ref: str, repo_root: Path) -> bool:
    result = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
        cwd=repo_root,
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=60,
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
    result = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard", "-z"],
        cwd=repo_root,
        check=False,
        capture_output=True,
        timeout=60,
    )
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace")
        raise RuntimeError(stderr.strip() or "git ls-files failed")
    decoded = result.stdout.decode("utf-8", errors="replace")
    return [path for path in decoded.split("\0") if path]


def _git_working_tree_diff(repo_root: Path) -> str:
    chunks = [
        _run_git(["diff", "--no-ext-diff", "--cached"], repo_root),
        _run_git(["diff", "--no-ext-diff"], repo_root),
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
            )
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


def _one_line(text: str, *, limit: int = 220) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 1] + "..."


def _run_gh_stdout(args: list[str], repo_root: Path) -> str:
    result = subprocess.run(
        ["gh", *args],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
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


def fetch_recent_copilot_comments(
    repo_root: Path,
    *,
    days: int,
    limit: int,
    github_repo: str | None = None,
) -> list[PullRequestComment]:
    since = (
        dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)
    ).date().isoformat()
    owner, name = github_repo_slug(repo_root, github_repo)
    prs = _run_gh_json(
        [
            "pr",
            "list",
            "--state",
            "all",
            "--limit",
            str(limit),
            "--search",
            f"updated:>={since}",
            "--json",
            "number,title,url",
        ],
        repo_root,
    )
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
            }
          }
        }
      }
    }
  }
}
""".strip()
    comments: list[PullRequestComment] = []
    for pr in _as_list(prs):
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
        data = payload.get("data")
        if not isinstance(data, dict):
            continue
        repository = data.get("repository")
        if not isinstance(repository, dict):
            continue
        pull_request = repository.get("pullRequest")
        if not isinstance(pull_request, dict):
            continue
        threads = pull_request.get("reviewThreads")
        if not isinstance(threads, dict):
            continue
        thread_nodes = threads.get("nodes")
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
                    )
                )
    return comments


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

    lines.extend(["", "### Recent Copilot Review Signals"])
    if comments:
        lines.extend(comment.markdown_item() for comment in comments)
    else:
        lines.append("- No recent Copilot review comments were included in this update.")

    lines.extend(
        [
            "",
            "### Suggested Preventive Actions",
            "- Move repeated mechanical findings into local checks where possible.",
            "- Keep Copilot instructions focused on current, non-outdated unresolved findings.",
            "- Treat generated or copied payloads as source/sync-contract review surfaces, not style-review surfaces.",
            MANAGED_END,
            "",
        ]
    )
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
    target.write_text(updated, encoding="utf-8", errors="strict")
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
        default=20,
        help="Maximum PRs to inspect when --github-days is set.",
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
    if args.github_limit < 1:
        print("[sd-review-learnings:setup] --github-limit must be positive", file=sys.stderr)
        return 2

    repo_root = args.repo_root.resolve()
    env_prefixes = tuple(args.env_prefix) if args.env_prefix else DEFAULT_ENV_PREFIXES
    try:
        if args.diff_from is not None:
            diff_text = args.diff_from.read_text(encoding="utf-8", errors="replace")
        else:
            diff_text = build_local_diff(
                repo_root,
                base=args.base,
                include_working_tree=args.include_working_tree,
            )
        findings = extract_findings(diff_text, repo_root, env_prefixes=env_prefixes)
    except (
        OSError,
        RuntimeError,
        subprocess.TimeoutExpired,
        json.JSONDecodeError,
    ) as exc:
        print(f"[sd-review-learnings:findings] {exc}", file=sys.stderr)
        return 2

    try:
        comments = (
            fetch_recent_copilot_comments(
                repo_root,
                days=args.github_days,
                limit=args.github_limit,
                github_repo=args.github_repo,
            )
            if args.github_days
            else []
        )
    except (
        OSError,
        RuntimeError,
        TypeError,
        subprocess.TimeoutExpired,
        json.JSONDecodeError,
    ) as exc:
        print(f"[sd-review-learnings:github] {exc}", file=sys.stderr)
        return 2

    for finding in findings:
        print(finding.render())
    if comments:
        print(f"[sd-review-learnings:github] captured {len(comments)} Copilot review comment(s)")

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
