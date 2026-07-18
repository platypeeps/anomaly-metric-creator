#!/usr/bin/env python3
"""Report local or fleet SD repository status without mutating state."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

SCHEMA_VERSION = 1
COMMAND_TIMEOUT_SECONDS = 20
MAX_ITEMS = 100
HUMAN_ITEM_LIMIT = 5
CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]+")
GITHUB_SLUG_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
PR_SEPARATOR = "\x1f"
PRIORITY_ORDER = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
REVIEW_TOTAL_COUNT_QUERY = (
    "query($owner:String!,$name:String!,$number:Int!){"
    "repository(owner:$owner,name:$name){"
    "pullRequest(number:$number){reviews{totalCount}}}}"
)


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str


def run_command(
    argv: Sequence[str],
    *,
    cwd: Path,
    timeout_seconds: int = COMMAND_TIMEOUT_SECONDS,
) -> CommandResult:
    try:
        result = subprocess.run(
            list(argv),
            cwd=cwd,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="strict",
            timeout=timeout_seconds,
        )
        return CommandResult(result.returncode, result.stdout)
    except (OSError, UnicodeError, subprocess.TimeoutExpired):
        return CommandResult(127, "")


def safe_text(value: object, *, limit: int = 180) -> str:
    text = CONTROL_RE.sub(" ", str(value)).strip()
    if len(text) <= limit:
        return text
    return f"{text[: max(0, limit - 3)].rstrip()}..."


def read_json_object(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8", errors="strict"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def github_slug_from_url(url: str) -> str | None:
    value = url.strip()
    prefixes = (
        "git@github.com:",
        "ssh://git@github.com/",
        "https://github.com/",
        "http://github.com/",
    )
    for prefix in prefixes:
        if value.startswith(prefix):
            value = value[len(prefix) :]
            break
    else:
        return None
    value = value.removesuffix(".git").strip("/")
    return value if GITHUB_SLUG_RE.fullmatch(value) else None


def resolve_repo(path: Path) -> Path | None:
    git_path = path.expanduser()
    if not git_path.is_absolute():
        git_path = Path.cwd() / git_path
    if not git_path.is_dir():
        git_path = git_path.parent
    result = run_command(
        ["git", "-C", str(git_path), "rev-parse", "--show-toplevel"],
        cwd=git_path,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return None
    try:
        return Path(result.stdout.strip()).resolve(strict=True)
    except OSError:
        return None


def git_output(repo: Path, *args: str) -> str | None:
    result = run_command(["git", *args], cwd=repo)
    return result.stdout.strip() if result.returncode == 0 else None


def parse_porcelain_v2(output: str) -> dict[str, Any]:
    branch: str | None = None
    detached = False
    upstream: str | None = None
    ahead: int | None = None
    behind: int | None = None
    staged = 0
    unstaged = 0
    untracked = 0

    for line in output.splitlines():
        if line.startswith("# branch.head "):
            branch_value = line.removeprefix("# branch.head ").strip()
            detached = branch_value == "(detached)"
            branch = None if detached else branch_value
        elif line.startswith("# branch.upstream "):
            upstream = line.removeprefix("# branch.upstream ").strip() or None
        elif line.startswith("# branch.ab "):
            match = re.fullmatch(r"# branch\.ab \+(\d+) -(\d+)", line)
            if match:
                ahead = int(match.group(1))
                behind = int(match.group(2))
        elif line.startswith(("1 ", "2 ", "u ")):
            fields = line.split(" ", 2)
            xy = fields[1] if len(fields) > 1 else ".."
            if len(xy) == 2:
                if xy[0] not in {".", " "}:
                    staged += 1
                if xy[1] not in {".", " "}:
                    unstaged += 1
        elif line.startswith("? "):
            untracked += 1

    if upstream is None:
        ahead = None
        behind = None
    elif ahead is None or behind is None:
        ahead = 0
        behind = 0

    return {
        "branch": branch,
        "detached": detached,
        "upstream": upstream,
        "ahead": ahead,
        "behind": behind,
        "workingTree": {
            "state": "clean" if staged + unstaged + untracked == 0 else "dirty",
            "staged": staged,
            "unstaged": unstaged,
            "untracked": untracked,
        },
    }


def default_branch(repo: Path, remote: str, supplied: str | None) -> str | None:
    if supplied:
        return supplied
    symbolic = git_output(
        repo,
        "symbolic-ref",
        "--quiet",
        "--short",
        f"refs/remotes/{remote}/HEAD",
    )
    if symbolic and symbolic.startswith(f"{remote}/"):
        return symbolic.removeprefix(f"{remote}/")
    for candidate in ("main", "master"):
        if git_output(repo, "show-ref", "--verify", f"refs/remotes/{remote}/{candidate}"):
            return candidate
        if git_output(repo, "show-ref", "--verify", f"refs/heads/{candidate}"):
            return candidate
    return None


def sync_state(upstream: str | None, ahead: int | None, behind: int | None) -> str:
    if upstream is None or ahead is None or behind is None:
        return "no-upstream"
    if ahead and behind:
        return "diverged"
    if ahead:
        return "ahead"
    if behind:
        return "behind"
    return "synchronized"


def collect_git(
    repo: Path,
    *,
    remote: str,
    supplied_default: str | None,
    refs_refreshed: bool,
) -> tuple[dict[str, Any], list[str]]:
    anomalies: list[str] = []
    porcelain = git_output(
        repo,
        "status",
        "--porcelain=v2",
        "--branch",
        "--untracked-files=all",
    )
    if porcelain is None:
        return {}, ["git status is unavailable"]
    state = parse_porcelain_v2(porcelain)
    resolved_default = default_branch(repo, remote, supplied_default)
    state["defaultBranch"] = resolved_default
    state["remote"] = remote
    state["syncState"] = sync_state(
        state["upstream"], state["ahead"], state["behind"]
    )
    state["refsFreshness"] = "refreshed" if refs_refreshed else "cached"
    state["head"] = git_output(repo, "rev-parse", "--short=12", "HEAD")
    state["headSubject"] = safe_text(
        git_output(repo, "log", "-1", "--pretty=%s", "HEAD") or "unavailable"
    )
    local_branches = git_output(
        repo,
        "for-each-ref",
        "--format=%(refname:short)",
        "refs/heads",
    )
    remote_branches = git_output(
        repo,
        "for-each-ref",
        "--format=%(refname)",
        f"refs/remotes/{remote}",
    )
    state["localBranches"] = sorted(local_branches.splitlines()) if local_branches else []
    state["remoteBranches"] = (
        sorted(
            branch.removeprefix("refs/remotes/")
            for branch in remote_branches.splitlines()
        )
        if remote_branches
        else []
    )
    stash_list = git_output(repo, "stash", "list", "--format=%gd")
    if stash_list is None:
        state["stashCount"] = None
        anomalies.append("git stash inventory is unavailable")
    else:
        state["stashCount"] = len(stash_list.splitlines()) if stash_list else 0
    remote_url = git_output(repo, "remote", "get-url", remote)
    state["remoteConfigured"] = remote_url is not None
    state["github"] = github_slug_from_url(remote_url or "")
    if resolved_default:
        local_default = git_output(repo, "rev-parse", f"refs/heads/{resolved_default}")
        remote_default = git_output(
            repo,
            "rev-parse",
            f"refs/remotes/{remote}/{resolved_default}",
        )
        state["defaultLocalExists"] = local_default is not None
        state["defaultRemoteExists"] = remote_default is not None
        state["defaultMatchesRemote"] = (
            local_default == remote_default
            if local_default is not None and remote_default is not None
            else None
        )
    else:
        state["defaultLocalExists"] = False
        state["defaultRemoteExists"] = False
        state["defaultMatchesRemote"] = None
    if remote_url is None:
        anomalies.append(f"remote {safe_text(remote)} is not configured")
    return state, anomalies


def read_version(path: Path) -> str | None:
    try:
        value = path.read_text(encoding="utf-8", errors="strict").strip()
    except (OSError, UnicodeError):
        return None
    return safe_text(value, limit=80) if value else None


def collect_versions(repo: Path, target_pack_version: str | None) -> dict[str, Any]:
    provenance = read_json_object(repo / ".sd-ai-command-pack/provenance.json")
    installed_pack = provenance.get("version") if provenance else None
    if not isinstance(installed_pack, str) or not installed_pack.strip():
        installed_manifest = read_json_object(repo / ".sd-ai-command-pack/manifest.json")
        installed_pack = installed_manifest.get("version") if installed_manifest else None
    if not isinstance(installed_pack, str) or not installed_pack.strip():
        installed_pack = None
    else:
        installed_pack = safe_text(installed_pack, limit=80)

    source_manifest = read_json_object(repo / "manifest.json")
    source_pack = None
    if source_manifest and source_manifest.get("name") == "sd-ai-command-pack":
        candidate = source_manifest.get("version")
        if isinstance(candidate, str) and candidate.strip():
            source_pack = safe_text(candidate, limit=80)
    target = target_pack_version or source_pack
    if installed_pack is None:
        pack_state = "not-installed"
    elif target is None:
        pack_state = "installed"
    elif installed_pack == target:
        pack_state = "current"
    else:
        pack_state = "different"

    return {
        "sdAiCommandPack": installed_pack,
        "sourcePack": source_pack,
        "targetPack": target,
        "packState": pack_state,
        "trellis": read_version(repo / ".trellis/.version"),
    }


def task_record(path: Path) -> dict[str, Any] | None:
    payload = read_json_object(path)
    if payload is None:
        return None
    title = payload.get("title") or payload.get("name") or path.parent.name
    status = payload.get("status")
    priority = payload.get("priority")
    if not isinstance(status, str):
        return None
    return {
        "id": safe_text(payload.get("id") or path.parent.name),
        "title": safe_text(title),
        "status": safe_text(status),
        "priority": safe_text(priority or "unprioritized"),
        "path": path.parent.relative_to(path.parents[2]).as_posix(),
    }


def task_sort_key(task: Mapping[str, Any]) -> tuple[int, str, str]:
    return (
        PRIORITY_ORDER.get(str(task.get("priority")), 9),
        str(task.get("title", "")).casefold(),
        str(task.get("id", "")).casefold(),
    )


def collect_trellis(repo: Path) -> dict[str, Any]:
    task_root = repo / ".trellis/tasks"
    tasks: list[dict[str, Any]] = []
    if task_root.is_dir():
        for task_json in sorted(task_root.glob("*/task.json")):
            if task_json.is_symlink() or not task_json.is_file():
                continue
            task = task_record(task_json)
            if task is not None:
                tasks.append(task)

    active: dict[str, Any] | None = None
    task_script = repo / ".trellis/scripts/task.py"
    if task_script.is_file():
        result = run_command([sys.executable, str(task_script), "current"], cwd=repo)
        active_path_text = result.stdout.strip() if result.returncode == 0 else ""
        if active_path_text:
            candidate_path = Path(active_path_text)
            if not candidate_path.is_absolute():
                candidate_path = repo / candidate_path
            active_path: Path | None = candidate_path
            try:
                candidate_path.resolve().relative_to(task_root.resolve())
            except (OSError, ValueError):
                active_path = None
            if active_path is not None:
                active = task_record(active_path / "task.json")

    in_progress = sorted(
        (task for task in tasks if task["status"] == "in_progress"),
        key=task_sort_key,
    )
    planned = sorted(
        (task for task in tasks if task["status"] == "planning"),
        key=task_sort_key,
    )
    return {
        "activeTask": active,
        "inProgress": in_progress,
        "planned": planned,
    }


def parse_gh_lines(output: str, *, kind: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for line in output.splitlines()[:MAX_ITEMS]:
        fields = line.split(PR_SEPARATOR, 2)
        if len(fields) < 2 or not fields[0].isdigit():
            continue
        item = {
            "number": int(fields[0]),
            "title": safe_text(fields[1]),
        }
        if kind == "pr" and len(fields) > 2:
            item["head"] = safe_text(fields[2], limit=120)
        items.append(item)
    return items


def collect_relevant_pr(repo: Path, slug: str, branch: str | None) -> dict[str, Any] | None:
    if not branch:
        return None
    fields = "number,state,mergedAt,url,headRefName,headRefOid"
    jq = (
        "[.number,.state,.mergedAt,.url,.headRefName,.headRefOid] "
        "| map(if . == null then \"\" else tostring end) | join(\"\\u001f\")"
    )
    result = run_command(
        [
            "gh",
            "pr",
            "view",
            "--repo",
            slug,
            "--json",
            fields,
            "--jq",
            jq,
            "--",
            branch,
        ],
        cwd=repo,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return None
    values = result.stdout.strip().split(PR_SEPARATOR)
    if len(values) < 6 or not values[0].isdigit():
        return None
    pr = {
        "number": int(values[0]),
        "state": safe_text(values[1] or "unknown"),
        "mergedAt": safe_text(values[2]) or None,
        "url": safe_text(values[3], limit=240) or None,
        "head": safe_text(values[4], limit=120),
        "headOid": safe_text(values[5], limit=80),
        "checks": "unavailable",
        "reviewCount": None,
    }
    checks = run_command(
        [
            "gh",
            "pr",
            "checks",
            "--repo",
            slug,
            str(pr["number"]),
            "--json",
            "bucket",
            "--jq",
            "[group_by(.bucket)[] | {(.[0].bucket): length}] | add // {}",
        ],
        cwd=repo,
    )
    if checks.returncode == 0 and checks.stdout.strip():
        try:
            parsed_checks = json.loads(checks.stdout)
        except json.JSONDecodeError:
            parsed_checks = None
        if isinstance(parsed_checks, dict):
            pr["checks"] = {
                safe_text(key, limit=40): value
                for key, value in parsed_checks.items()
                if isinstance(value, int)
            }
    owner, separator, name = slug.partition("/")
    if owner and separator and name:
        reviews = run_command(
            [
                "gh",
                "api",
                "graphql",
                "-F",
                f"owner={owner}",
                "-F",
                f"name={name}",
                "-F",
                f"number={pr['number']}",
                "-f",
                f"query={REVIEW_TOTAL_COUNT_QUERY}",
                "--jq",
                ".data.repository.pullRequest.reviews.totalCount",
            ],
            cwd=repo,
        )
        if reviews.returncode == 0 and reviews.stdout.strip().isdigit():
            pr["reviewCount"] = int(reviews.stdout.strip())
    return pr


def collect_github(
    repo: Path,
    *,
    slug: str | None,
    branch: str | None,
    network: bool,
) -> dict[str, Any]:
    if not network:
        return {
            "status": "disabled",
            "currentPr": None,
            "openPrs": [],
            "openPrsStatus": "unavailable",
            "openIssues": [],
            "openIssuesStatus": "unavailable",
        }
    if slug is None:
        return {
            "status": "not-configured",
            "currentPr": None,
            "openPrs": [],
            "openPrsStatus": "unavailable",
            "openIssues": [],
            "openIssuesStatus": "unavailable",
        }
    if shutil.which("gh") is None:
        return {
            "status": "gh-unavailable",
            "currentPr": None,
            "openPrs": [],
            "openPrsStatus": "unavailable",
            "openIssues": [],
            "openIssuesStatus": "unavailable",
        }

    pr_result = run_command(
        [
            "gh",
            "pr",
            "list",
            "--repo",
            slug,
            "--state",
            "open",
            "--limit",
            str(MAX_ITEMS),
            "--json",
            "number,title,headRefName",
            "--jq",
            ".[] | [.number,.title,.headRefName] | join(\"\\u001f\")",
        ],
        cwd=repo,
    )
    issue_result = run_command(
        [
            "gh",
            "issue",
            "list",
            "--repo",
            slug,
            "--state",
            "open",
            "--limit",
            str(MAX_ITEMS),
            "--json",
            "number,title",
            "--jq",
            ".[] | [.number,.title] | join(\"\\u001f\")",
        ],
        cwd=repo,
    )
    status = "available"
    if pr_result.returncode != 0 or issue_result.returncode != 0:
        status = "partial"
    return {
        "status": status,
        "currentPr": collect_relevant_pr(repo, slug, branch),
        "openPrs": parse_gh_lines(pr_result.stdout, kind="pr")
        if pr_result.returncode == 0
        else [],
        "openPrsStatus": "available" if pr_result.returncode == 0 else "unavailable",
        "openIssues": parse_gh_lines(issue_result.stdout, kind="issue")
        if issue_result.returncode == 0
        else [],
        "openIssuesStatus": (
            "available" if issue_result.returncode == 0 else "unavailable"
        ),
    }


def strict_anomalies(
    git: Mapping[str, Any],
    *,
    default: str | None,
    remote: str,
    source_branch: str | None,
    keep_remote_branch: bool,
    dry_run: bool,
) -> list[str]:
    anomalies: list[str] = []
    tree = git.get("workingTree")
    if isinstance(tree, dict) and tree.get("state") != "clean":
        anomalies.append("working tree is dirty after housekeeping")
    if dry_run:
        return anomalies
    branch = git.get("branch")
    if default is None:
        anomalies.append("default branch is unknown; skipped branch inventory checks")
        return anomalies
    if branch != default:
        anomalies.append(
            f"current branch is {safe_text(branch or 'detached HEAD')}, expected {safe_text(default)}"
        )
    if not git.get("defaultLocalExists"):
        anomalies.append(f"local default branch {safe_text(default)} does not exist")
    elif not git.get("defaultRemoteExists"):
        anomalies.append(
            f"remote default branch {safe_text(remote)}/{safe_text(default)} does not exist"
        )
    elif git.get("defaultMatchesRemote") is not True:
        anomalies.append(f"{safe_text(default)} does not match {safe_text(remote)}/{safe_text(default)}")
    local_branches = git.get("localBranches")
    if isinstance(local_branches, list):
        extras = [item for item in local_branches if item != default]
        if extras:
            anomalies.append(
                "extra local branches remain: "
                + ",".join(safe_text(item, limit=80) for item in extras)
            )
    if source_branch and source_branch != default:
        remote_ref = f"{remote}/{source_branch}"
        remote_branches = git.get("remoteBranches")
        present = isinstance(remote_branches, list) and remote_ref in remote_branches
        if keep_remote_branch and not present:
            anomalies.append(
                f"remote source branch {safe_text(remote_ref)} is absent despite --keep-remote-branch"
            )
        elif not keep_remote_branch and present:
            anomalies.append(f"remote source branch still tracked: {safe_text(remote_ref)}")
    return anomalies


def next_steps(report: Mapping[str, Any]) -> list[str]:
    steps: list[str] = []
    if report.get("anomalies"):
        steps.append("Resolve the reported anomalies, then rerun sd-status.")
    git_value = report.get("git")
    git: Mapping[str, Any] = git_value if isinstance(git_value, dict) else {}
    tree_value = git.get("workingTree")
    tree: Mapping[str, Any] = tree_value if isinstance(tree_value, dict) else {}
    if tree.get("state") == "dirty":
        steps.append("Review and commit or intentionally discard the current working-tree changes.")
    sync = git.get("syncState")
    if sync == "behind":
        steps.append("Fast-forward the current branch from its upstream before new work.")
    elif sync == "ahead":
        steps.append("Push the local commits or confirm they are intentionally local-only.")
    elif sync == "diverged":
        steps.append("Reconcile the diverged local and upstream histories before publishing.")
    elif sync == "no-upstream":
        steps.append("Configure or verify the branch upstream before publishing new work.")
    versions = report.get("versions")
    if isinstance(versions, dict) and versions.get("packState") == "different":
        steps.append(
            "Refresh the installed SD command pack to the source fleet version."
        )
    github = report.get("github")
    if isinstance(github, dict) and isinstance(github.get("currentPr"), dict):
        pr = github["currentPr"]
        if pr.get("state") == "OPEN":
            steps.append(f"Continue PR #{pr.get('number')} through sd-watch-pr or sd-housekeeping.")
    trellis = report.get("trellis")
    if isinstance(trellis, dict):
        active = trellis.get("activeTask")
        if isinstance(active, dict):
            steps.append(
                f"Resume Trellis task {active.get('id')}: {active.get('title')}."
            )
        elif trellis.get("inProgress"):
            task = trellis["inProgress"][0]
            steps.append(
                f"Resume in-progress Trellis task {task.get('id')}: {task.get('title')}."
            )
        elif trellis.get("planned"):
            task = trellis["planned"][0]
            steps.append(
                f"Consider planned Trellis task {task.get('id')}: {task.get('title')}."
            )
    if not steps:
        steps.append("No immediate repository action is required.")
    return steps[:HUMAN_ITEM_LIMIT]


def collect_local(
    requested_repo: Path,
    *,
    remote: str,
    supplied_default: str | None,
    source_branch: str | None,
    github_repo: str | None,
    network: bool,
    refs_refreshed: bool,
    expect_clean: bool,
    keep_remote_branch: bool,
    dry_run: bool,
    prior_anomalies: Sequence[str],
    target_pack_version: str | None = None,
) -> dict[str, Any] | None:
    repo = resolve_repo(requested_repo)
    if repo is None:
        return None
    git, anomalies = collect_git(
        repo,
        remote=remote,
        supplied_default=supplied_default,
        refs_refreshed=refs_refreshed,
    )
    if not git:
        return None
    slug = github_repo or git.get("github")
    if not isinstance(slug, str) or not GITHUB_SLUG_RE.fullmatch(slug):
        slug = None
    default = git.get("defaultBranch")
    relevant_branch = source_branch
    if relevant_branch is None and git.get("branch") != default:
        relevant_branch = git.get("branch")
    report: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "mode": "local",
        "repository": {
            "path": str(repo),
            "name": repo.name,
            "github": slug,
        },
        "git": git,
        "versions": collect_versions(repo, target_pack_version),
        "github": collect_github(
            repo,
            slug=slug,
            branch=relevant_branch if isinstance(relevant_branch, str) else None,
            network=network,
        ),
        "trellis": collect_trellis(repo),
        "cleanupContext": {
            "sourceBranch": source_branch,
            "keepRemoteBranch": keep_remote_branch,
            "dryRun": dry_run,
        }
        if source_branch or dry_run
        else None,
        "anomalies": [safe_text(item, limit=500) for item in prior_anomalies]
        + anomalies,
        "nextSteps": [],
    }
    if expect_clean:
        report["anomalies"].extend(
            strict_anomalies(
                git,
                default=default if isinstance(default, str) else None,
                remote=remote,
                source_branch=source_branch,
                keep_remote_branch=keep_remote_branch,
                dry_run=dry_run,
            )
        )
    report["nextSteps"] = next_steps(report)
    return report


def format_working_tree(tree: Mapping[str, Any]) -> str:
    if tree.get("state") == "clean":
        return "clean"
    return (
        f"dirty (staged {tree.get('staged', 0)}, "
        f"unstaged {tree.get('unstaged', 0)}, untracked {tree.get('untracked', 0)})"
    )


def format_task(task: object) -> str:
    if not isinstance(task, dict):
        return "none active"
    return f"{task.get('id')} [{task.get('status')}, {task.get('priority')}]: {task.get('title')}"


def format_items(items: object) -> str:
    if not isinstance(items, list) or not items:
        return "none"
    shown = [f"#{item.get('number')}: {item.get('title')}" for item in items[:HUMAN_ITEM_LIMIT]]
    suffix = f"; +{len(items) - HUMAN_ITEM_LIMIT} more" if len(items) > HUMAN_ITEM_LIMIT else ""
    return "; ".join(shown) + suffix


def render_local(report: Mapping[str, Any], *, dry_run: bool) -> None:
    repository = report["repository"]
    git = report["git"]
    tree = git["workingTree"]
    anomalies = report["anomalies"]
    attention = (
        bool(anomalies)
        or tree.get("state") != "clean"
        or git.get("syncState") != "synchronized"
    )
    print(f"SD status: {'attention' if attention else 'healthy'}")
    identity = repository.get("github") or repository.get("name")
    print(f"Repository: {safe_text(identity)} ({repository.get('path')})")
    print(f"Ref freshness: {git.get('refsFreshness')}")

    print("\n==> Expected clean state")
    branch = git.get("branch") or f"detached at {git.get('head') or 'unknown'}"
    print(f"- branch: {branch}")
    print(f"- working tree: {format_working_tree(tree)}")
    default = git.get("defaultBranch") or "unknown"
    upstream = git.get("upstream") or "none"
    print(
        f"- upstream: {upstream}; {git.get('syncState')} "
        f"(ahead {git.get('ahead') if git.get('ahead') is not None else 'n/a'}, "
        f"behind {git.get('behind') if git.get('behind') is not None else 'n/a'}; "
        f"{git.get('refsFreshness')} refs)"
    )
    print(f"- default branch: {default}")
    comparison = git.get("defaultMatchesRemote")
    if comparison is True:
        print(f"- default comparison: {default} matches {git.get('remote')}/{default}")
    elif comparison is False:
        print(f"- default comparison: {default} differs from {git.get('remote')}/{default}")
    local_branches = git.get("localBranches") or []
    remote_branches = git.get("remoteBranches") or []
    print(f"- local branches ({len(local_branches)}): {', '.join(local_branches) or 'none'}")
    print(f"- remote branches ({len(remote_branches)}): {', '.join(remote_branches[:10]) or 'none'}")
    stash_count = git.get("stashCount")
    print(f"- git stashes: {stash_count if isinstance(stash_count, int) else 'unavailable'}")
    cleanup = report.get("cleanupContext")
    if dry_run:
        print(
            "- dry-run preview: skipped final git-state verification because no "
            "fetch, pull, switch, or branch deletion was performed"
        )
    elif isinstance(cleanup, dict):
        source_branch = cleanup.get("sourceBranch")
        if isinstance(source_branch, str) and source_branch and source_branch != default:
            remote_ref = f"{git.get('remote') or 'origin'}/{source_branch}"
            if remote_ref in remote_branches:
                label = "kept" if cleanup.get("keepRemoteBranch") else "still tracked"
                print(f"- remote source branch {label}: {remote_ref}")
            else:
                print(f"- remote source branch absent: {remote_ref}")

    versions = report["versions"]
    print("\n==> Delivery")
    pack = versions.get("sdAiCommandPack") or "not installed"
    target = versions.get("targetPack")
    target_suffix = f"; target {target}" if target else ""
    print(f"- SD pack: {pack} ({versions.get('packState')}{target_suffix})")
    print(f"- Trellis: {versions.get('trellis') or 'unknown'}")
    pr = report["github"].get("currentPr")
    if isinstance(pr, dict):
        merged = f"; merged {pr.get('mergedAt')}" if pr.get("mergedAt") else ""
        print(f"- relevant PR: #{pr.get('number')} {pr.get('state')}{merged}")
        print(f"- PR checks: {pr.get('checks')}")
        reviews = pr.get("reviewCount")
        print(f"- PR review rounds: {reviews if reviews is not None else 'unavailable'}")
    else:
        print("- relevant PR: none")

    github = report["github"]
    trellis = report["trellis"]
    print("\n==> Inventory")
    print(f"- GitHub: {github.get('status')}")
    if github.get("openPrsStatus") == "available":
        print(
            f"- open PRs ({len(github.get('openPrs', []))}): "
            f"{format_items(github.get('openPrs'))}"
        )
    else:
        print("- open PRs: unavailable")
    if github.get("openIssuesStatus") == "available":
        print(
            f"- open issues ({len(github.get('openIssues', []))}): "
            f"{format_items(github.get('openIssues'))}"
        )
    else:
        print("- open issues: unavailable")
    print(f"- current Trellis task: {format_task(trellis.get('activeTask'))}")
    print(f"- in-progress Trellis tasks: {len(trellis.get('inProgress', []))}")
    planned = trellis.get("planned", [])
    print(f"- planned Trellis tasks ({len(planned)}): {format_task(planned[0]) if planned else 'none'}")

    print("\n==> Anomalies")
    if anomalies:
        for anomaly in anomalies:
            print(f"- {anomaly}")
    else:
        print("none")

    print("\n==> Next Steps")
    for index, step in enumerate(report["nextSteps"], start=1):
        print(f"{index}. {step}")


def fleet_api() -> Any:
    scripts_dir = Path(__file__).resolve().parent
    scripts_path = str(scripts_dir)
    inserted = scripts_path not in sys.path
    if inserted:
        sys.path.insert(0, scripts_path)
    try:
        import sd_ai_command_pack_fleet_lib as fleet
    except ImportError as error:
        raise RuntimeError(
            "installed fleet helper is missing; refresh sd-ai-command-pack"
        ) from error
    finally:
        if inserted:
            sys.path.remove(scripts_path)
    return fleet


def load_fleet(
    pack_root: Path,
    path: Path | None,
    *,
    environ: Mapping[str, str] | None = None,
    cwd: Path | None = None,
    home: Path | None = None,
) -> tuple[list[Any], Any]:
    fleet = fleet_api()
    resolution = fleet.resolve_fleet_configuration(
        pack_root,
        fleet_manifest=path,
        environ=environ,
        cwd=cwd,
        home=home,
    )
    try:
        consumers = fleet.load_fleet_consumers(resolution.manifest_path)
    except ValueError as error:
        raise ValueError(
            f"{resolution.source} fleet configuration is unusable: {error}"
        ) from None
    consumer_names = {consumer.name.casefold() for consumer in consumers}
    unknown_overrides = sorted(set(resolution.path_overrides) - consumer_names)
    if unknown_overrides:
        raise ValueError(
            "machine profile has checkout overrides for unknown fleet members: "
            + ", ".join(unknown_overrides)
        )
    return consumers, resolution


def fleet_next_steps(reports: Sequence[Mapping[str, Any]], target: str) -> list[str]:
    missing = [item["name"] for item in reports if item.get("status") == "missing"]
    dirty = [
        item["name"]
        for item in reports
        if item.get("status") == "available"
        and item["report"]["git"]["workingTree"]["state"] == "dirty"
    ]
    stale = [
        item["name"]
        for item in reports
        if item.get("status") == "available"
        and item["report"]["versions"]["sdAiCommandPack"] != target
    ]
    divergent = [
        item["name"]
        for item in reports
        if item.get("status") == "available"
        and item["report"]["git"]["syncState"] in {"behind", "diverged"}
    ]
    steps: list[str] = []
    if missing:
        steps.append("Restore or correct missing fleet checkouts: " + ", ".join(missing) + ".")
    if dirty:
        steps.append("Resolve uncommitted fleet work before rollout: " + ", ".join(dirty) + ".")
    if divergent:
        steps.append("Reconcile behind or diverged fleet checkouts: " + ", ".join(divergent) + ".")
    if stale:
        steps.append("Refresh stale SD pack installations: " + ", ".join(stale) + ".")
    if not steps:
        steps.append("Fleet checkouts are locally ready; no immediate fleet action is required.")
    return steps[:HUMAN_ITEM_LIMIT]


def collect_fleet(
    pack_root: Path,
    *,
    fleet_path: Path | None,
    network: bool,
    refs_refreshed: bool,
    environ: Mapping[str, str] | None = None,
    cwd: Path | None = None,
    home: Path | None = None,
) -> dict[str, Any]:
    consumers, resolution = load_fleet(
        pack_root,
        fleet_path,
        environ=environ,
        cwd=cwd,
        home=home,
    )
    target = resolution.target_version
    reports: list[dict[str, Any]] = []
    for consumer in consumers:
        path = resolution.path_overrides.get(
            consumer.name.casefold(),
            Path(consumer.path_hint).expanduser(),
        )
        if not path.is_dir():
            reports.append(
                {
                    "name": consumer.name,
                    "github": consumer.github,
                    "priority": consumer.rollout_priority,
                    "path": str(path),
                    "status": "missing",
                    "report": None,
                }
            )
            continue
        report = collect_local(
            path,
            remote="origin",
            supplied_default=None,
            source_branch=None,
            github_repo=consumer.github,
            network=network,
            refs_refreshed=refs_refreshed,
            expect_clean=False,
            keep_remote_branch=False,
            dry_run=False,
            prior_anomalies=(),
            target_pack_version=target,
        )
        reports.append(
            {
                "name": consumer.name,
                "github": consumer.github,
                "priority": consumer.rollout_priority,
                "path": str(path),
                "status": "available" if report else "unavailable",
                "report": report,
            }
        )
    return {
        "schemaVersion": SCHEMA_VERSION,
        "mode": "fleet",
        "targetPackVersion": target,
        "refsFreshness": "refreshed" if refs_refreshed else "cached",
        "configuration": {
            "source": resolution.source,
            "manifest": str(resolution.manifest_path),
            "profile": (
                str(resolution.profile_path) if resolution.profile_path else None
            ),
        },
        "repositories": reports,
        "nextSteps": fleet_next_steps(reports, target),
    }


def render_fleet(report: Mapping[str, Any]) -> None:
    repositories = report["repositories"]
    available = sum(item.get("status") == "available" for item in repositories)
    missing = sum(item.get("status") == "missing" for item in repositories)
    unavailable = len(repositories) - available - missing
    attention = 0
    for item in repositories:
        local = item.get("report")
        if not isinstance(local, dict):
            attention += 1
        elif (
            local["git"]["workingTree"]["state"] != "clean"
            or local["git"]["syncState"] in {"behind", "diverged"}
            or local["versions"]["sdAiCommandPack"] != report["targetPackVersion"]
        ):
            attention += 1
    print(
        f"SD fleet status: {len(repositories)} repositories, "
        f"{available} available, {attention} need attention, {missing} missing, "
        f"{unavailable} unavailable"
    )
    print(f"Target pack: {report['targetPackVersion']}")
    configuration = report.get("configuration", {})
    print(f"Fleet config: {configuration.get('source', 'unknown')}")
    print(f"Ref freshness: {report['refsFreshness']}")
    print("\n==> Fleet")
    for item in repositories:
        prefix = f"P{item['priority']:02d} {item['name']}"
        local = item.get("report")
        if not isinstance(local, dict):
            print(f"- {prefix}: {item['status']} ({item['path']})")
            continue
        git = local["git"]
        versions = local["versions"]
        github = local["github"]
        trellis = local["trellis"]
        stash_count = git.get("stashCount")
        stash_label = stash_count if isinstance(stash_count, int) else "unavailable"
        pr_count = (
            str(len(github.get("openPrs", [])))
            if github.get("openPrsStatus") == "available"
            else "unavailable"
        )
        print(
            f"- {prefix}: {git['workingTree']['state']}; "
            f"{git.get('branch') or 'detached'}; "
            f"{report['refsFreshness']}:{git['syncState']}; "
            f"pack {versions.get('sdAiCommandPack') or 'none'}; "
            f"stashes {stash_label}; "
            f"PRs {pr_count}; "
            f"tasks {len(trellis.get('inProgress', []))}/{len(trellis.get('planned', []))}"
        )
    print("\n==> Next Steps")
    for index, step in enumerate(report["nextSteps"], start=1):
        print(f"{index}. {step}")


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Report read-only SD repository or fleet status."
    )
    parser.add_argument("mode", nargs="?", choices=("fleet",))
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument(
        "--fleet-manifest",
        type=Path,
        help=(
            "Use this canonical fleet manifest instead of environment, "
            "machine-profile, or source-checkout discovery."
        ),
    )
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--no-network", action="store_true")
    parser.add_argument("--expect-clean", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--refs-refreshed", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--remote", default="origin", help=argparse.SUPPRESS)
    parser.add_argument("--default-branch", help=argparse.SUPPRESS)
    parser.add_argument("--source-branch", help=argparse.SUPPRESS)
    parser.add_argument("--github-repo", help=argparse.SUPPRESS)
    parser.add_argument("--keep-remote-branch", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--dry-run", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument(
        "--prior-anomaly",
        action="append",
        default=[],
        help=argparse.SUPPRESS,
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.mode == "fleet":
        pack_root = Path(__file__).resolve().parents[1]
        try:
            report = collect_fleet(
                pack_root,
                fleet_path=args.fleet_manifest,
                network=not args.no_network,
                refs_refreshed=args.refs_refreshed,
            )
        except (RuntimeError, ValueError) as error:
            print(f"error: {safe_text(error, limit=500)}", file=sys.stderr)
            return 1
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=False))
        else:
            render_fleet(report)
        return 0

    local_report = collect_local(
        args.repo,
        remote=args.remote,
        supplied_default=args.default_branch,
        source_branch=args.source_branch,
        github_repo=args.github_repo,
        network=not args.no_network,
        refs_refreshed=args.refs_refreshed,
        expect_clean=args.expect_clean,
        keep_remote_branch=args.keep_remote_branch,
        dry_run=args.dry_run,
        prior_anomalies=args.prior_anomaly,
    )
    if local_report is None:
        print(f"error: unable to inspect Git repository: {args.repo}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(local_report, indent=2, sort_keys=False))
    else:
        render_local(local_report, dry_run=args.dry_run)
    return 1 if args.expect_clean and local_report["anomalies"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
