#!/usr/bin/env python3
"""Report local or fleet SD repository status without mutating state."""

from __future__ import annotations

import argparse
import contextlib
import errno
import hashlib
import importlib.util
import json
import os
import re
import shutil
import stat
import subprocess
import sys
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit

sys.dont_write_bytecode = True

# This import must follow the bytecode guard for direct entrypoint invocation.
from sd_ai_command_pack_lib import CacheSetupError, build_tool_environment  # noqa: E402

SCHEMA_VERSION = 2
COMMAND_TIMEOUT_SECONDS = 20
MAX_ITEMS = 100
HUMAN_ITEM_LIMIT = 5
MAX_ROADMAP_SOURCE_FILES = 100
MAX_ROADMAP_SOURCE_BYTES = 256 * 1024
MAX_ROADMAP_LINE_CHARS = 2_000
MAX_ROADMAP_ITEMS = 500
CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]+")
COMMIT_RE = re.compile(r"[0-9a-f]{40,64}")
GITHUB_SLUG_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
ROADMAP_SOURCE_EXTENSIONS = frozenset({".md", ".mdx", ".txt"})
ROADMAP_SOURCE_STEMS = (
    "roadmap",
    "backlog",
    "todo",
    "program_design",
    "implementation_plan",
)
ROADMAP_SOURCE_DIRECTORIES = frozenset({"roadmap", "proposals", "rfcs"})
ROADMAP_EXCLUDED_DIRECTORIES = frozenset(
    {
        "git",
        "trellis",
        "venv",
        "build",
        "coverage",
        "dist",
        "node_modules",
        "target",
        "vendor",
    }
)
UNCHECKED_TASK_RE = re.compile(r"^[ \t]*[-*+][ \t]+\[[ \t]\][ \t]+(.+?)\s*$")
CHECKED_TASK_RE = re.compile(r"^[ \t]*[-*+][ \t]+\[[xX]\][ \t]+")
TOP_LEVEL_LIST_RE = re.compile(
    r"^(?:[-*+]|[0-9]{1,4}[.)])[ \t]+(?!\[[ xX]\][ \t]+)(.+?)\s*$"
)
MARKDOWN_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]*\)")
MARKDOWN_REFERENCE_LINK_RE = re.compile(r"\[([^\]]+)\]\[[^\]]*\]")
MARKDOWN_TAG_RE = re.compile(r"<[^>]+>")
MARKDOWN_OPEN_MARKER_RE = re.compile(
    r"(?<!\w)(?:\*{1,3}|_{1,3}|~{1,2}|`+)(?=\S)"
)
MARKDOWN_CLOSE_MARKER_RE = re.compile(
    r"(?<=\S)(?:\*{1,3}|_{1,3}|~{1,2}|`+)(?!\w)"
)
PARKED_PREFIX_RE = re.compile(r"^PARKED\s*:\s*", re.IGNORECASE)
PR_SEPARATOR = "\x1f"
PRIORITY_ORDER = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
TASK_STATUS_ORDER = {"in_progress": 0, "planning": 1, "completed": 2}
MACHINE_SCOPE_SCHEMA_VERSION = 1
# The plugin the machine-scope surfaces ship with; the identity
# sd-ai-command-pack-pack-update.sh updates.
MACHINE_PLUGIN_ID = "sd@sd-ai-command-pack"
MACHINE_UNAVAILABLE = "unavailable"
# States the machine-install engine reports from the receipt alone. A fourth
# value, MACHINE_UNAVAILABLE, is this collector's own: it means the receipt
# could not be read at all (no engine beside this script), which is neither a
# missing install nor a corrupt one.
MACHINE_RECEIPT_STATES = frozenset({"none", "installed", "invalid"})
WORK_LOOP_TERMINAL_STATUSES = frozenset({"none", "invalid", "unavailable"})
WORK_LOOP_RUN_STATUSES = frozenset({"active", "paused", "stopped", "completed"})
WORK_LOOP_REQUIRED_STRING_FIELDS = (
    "runId",
    "mode",
    "selector",
    "phase",
    "focusMode",
    "heartbeatAt",
)
REVIEW_TOTAL_COUNT_QUERY = (
    "query($owner:String!,$name:String!,$number:Int!){"
    "repository(owner:$owner,name:$name){"
    "pullRequest(number:$number){reviews{totalCount}}}}"
)
FLEET_READY_STEP = (
    "Fleet checkouts are locally ready; no immediate fleet action is required."
)
# Skew rows describe an installation that no longer matches what it pins, so
# they must reach the operator even when the advisory rows outnumber
# HUMAN_ITEM_LIMIT. fleet_next_steps sorts by this rank before truncating and
# derives followUps from the untruncated set.
FLEET_STEP_RANK_SKEW = 0
FLEET_STEP_RANK_ADVISORY = 1
# Mirrors DEFAULT_FLEET_PIN_PATH in sd_ai_command_pack_fleet_lib. Used only as a
# defensive fallback for a FleetConsumer that predates schema 5; a test asserts
# the two constants stay equal.
DEFAULT_CONSUMER_PIN_PATH = ".sd-ai-command-pack/provenance.json"


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str


@contextlib.contextmanager
def suppress_bytecode_writes() -> Iterator[None]:
    """Keep read-only status imports from creating repository-local caches."""
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        yield
    finally:
        sys.dont_write_bytecode = previous


def run_command(
    argv: Sequence[str],
    *,
    cwd: Path,
    timeout_seconds: int = COMMAND_TIMEOUT_SECONDS,
) -> CommandResult:
    try:
        environment, _, _ = build_tool_environment(repo=cwd)
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        result = subprocess.run(
            list(argv),
            cwd=cwd,
            env=environment,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="strict",
            timeout=timeout_seconds,
        )
        return CommandResult(result.returncode, result.stdout)
    except CacheSetupError as error:
        print(f"status cache setup failed: {error}", file=sys.stderr)
        return CommandResult(127, "")
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
    if git_path.is_file():
        git_path = git_path.parent
    elif not git_path.is_dir():
        return None
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


def parse_worktree_porcelain(text: str) -> list[dict[str, Any]]:
    """Parse `git worktree list --porcelain -z` output into raw rows.

    Values are returned unmodified: paths may exceed display bounds and
    contain newlines. Display bounding happens only when the outgoing
    JSON row is composed, because filesystem probes need the raw path.
    """
    rows: list[dict[str, Any]] = []
    entry: dict[str, Any] | None = None
    for record in text.split("\0"):
        if not record:
            if entry is not None:
                rows.append(entry)
                entry = None
            continue
        if record.startswith("worktree "):
            if entry is not None:
                rows.append(entry)
            entry = {
                "path": record.removeprefix("worktree "),
                "branch": None,
                "detached": False,
                "head": None,
                "bare": False,
                "locked": False,
                "prunable": False,
                "reason": None,
            }
            continue
        if entry is None:
            continue
        if record.startswith("HEAD "):
            entry["head"] = record.removeprefix("HEAD ")
        elif record.startswith("branch "):
            entry["branch"] = record.removeprefix("branch ").removeprefix(
                "refs/heads/"
            )
        elif record == "detached":
            entry["detached"] = True
        elif record == "bare":
            entry["bare"] = True
        elif record == "locked":
            entry["locked"] = True
        elif record.startswith("locked "):
            entry["locked"] = True
            entry["reason"] = record.removeprefix("locked ")
        elif record == "prunable":
            entry["prunable"] = True
        elif record.startswith("prunable "):
            entry["prunable"] = True
            entry["reason"] = record.removeprefix("prunable ")
    if entry is not None:
        rows.append(entry)
    return rows


def collect_worktrees(repo: Path) -> dict[str, Any]:
    listing = git_output(repo, "worktree", "list", "--porcelain", "-z")
    if listing is None:
        return {"status": "unavailable"}
    parsed = parse_worktree_porcelain(listing)
    reporting_raw = git_output(
        repo, "rev-parse", "--path-format=absolute", "--show-toplevel"
    )
    reporting: Path | None = None
    if reporting_raw:
        try:
            reporting = Path(reporting_raw).resolve()
        except OSError:
            reporting = None
    common_raw = git_output(
        repo, "rev-parse", "--path-format=absolute", "--git-common-dir"
    )
    common: Path | None = None
    if common_raw:
        try:
            common = Path(common_raw).resolve()
        except OSError:
            common = None
    rows: list[dict[str, Any]] = []
    current_marked = False
    for entry in parsed:
        raw_path = entry["path"]
        current = False
        if not current_marked:
            try:
                current = (
                    reporting is not None
                    and Path(raw_path).resolve() == reporting
                )
            except OSError:
                current = reporting_raw is not None and raw_path == reporting_raw
            current_marked = current
        clean: bool | None = None
        if not entry["bare"] and not entry["prunable"]:
            probe_root = Path(raw_path)
            try:
                probe_ok = probe_root.is_dir()
            except OSError:
                probe_ok = False
            if probe_ok and common is not None:
                probe_common = git_output(
                    probe_root,
                    "rev-parse",
                    "--path-format=absolute",
                    "--git-common-dir",
                )
                identity = False
                if probe_common:
                    try:
                        identity = Path(probe_common).resolve() == common
                    except OSError:
                        identity = False
                if identity:
                    porcelain = git_output(
                        probe_root, "--no-optional-locks", "status", "--porcelain"
                    )
                    if porcelain is not None:
                        clean = porcelain == ""
        rows.append(
            {
                "path": safe_text(raw_path, limit=300),
                "branch": safe_text(entry["branch"]) if entry["branch"] else None,
                "detached": entry["detached"],
                "head": safe_text(entry["head"][:12]) if entry["head"] else None,
                "bare": entry["bare"],
                "locked": entry["locked"],
                "prunable": entry["prunable"],
                "reason": safe_text(entry["reason"]) if entry["reason"] else None,
                "clean": clean,
                "current": current,
            }
        )
    return {"status": "ok", "rows": rows}


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
    worktrees = collect_worktrees(repo)
    state["worktrees"] = worktrees
    if worktrees["status"] == "ok":
        # A worktree HEAD may symref to a non-branch ref; the held set is
        # scoped to local branches so it stays a subset of localBranches.
        local_branch_names = set(state["localBranches"])
        state["branchesHeldElsewhere"] = sorted(
            {
                row["branch"]
                for row in worktrees["rows"]
                if row["branch"]
                and not row["current"]
                and row["branch"] in local_branch_names
            }
        )
    else:
        state["branchesHeldElsewhere"] = None
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
    status = payload.get("status")
    parent_value = payload.get("parent")
    if not isinstance(status, str):
        return None
    task_id = safe_text(payload.get("id") or path.parent.name)
    normalized_status = safe_text(status)
    if not task_id or not normalized_status:
        return None
    title = safe_text(payload.get("title") or payload.get("name") or task_id)
    if not title:
        title = task_id
    priority = safe_text(payload.get("priority") or "unprioritized")
    if not priority:
        priority = "unprioritized"
    if parent_value is None:
        parent = None
    elif not isinstance(parent_value, str) or not parent_value.strip():
        return None
    else:
        parent = safe_text(parent_value)
        if not parent:
            return None
    return {
        "id": task_id,
        "title": title,
        "status": normalized_status,
        "priority": priority,
        "path": path.parent.relative_to(path.parents[2]).as_posix(),
        "parent": parent,
    }


def task_sort_key(task: Mapping[str, Any]) -> tuple[int, str, str]:
    return (
        PRIORITY_ORDER.get(str(task.get("priority")), 9),
        str(task.get("title", "")).casefold(),
        str(task.get("id", "")).casefold(),
    )


def task_inventory_sort_key(
    task: Mapping[str, Any],
    *,
    active_identity: tuple[str, str] | None,
) -> tuple[int, int, int, str, str, str]:
    identity = (str(task.get("id", "")), str(task.get("path", "")))
    return (
        0 if identity == active_identity else 1,
        TASK_STATUS_ORDER.get(str(task.get("status")), 9),
        PRIORITY_ORDER.get(str(task.get("priority")), 9),
        str(task.get("title", "")).casefold(),
        identity[0].casefold(),
        identity[1].casefold(),
    )


def select_items(
    items: Sequence[Mapping[str, Any]],
    *,
    prefix: str,
) -> list[dict[str, Any]]:
    return [
        {**dict(item), "selectionId": f"{prefix}-{index}"}
        for index, item in enumerate(items, start=1)
    ]


def collect_trellis(repo: Path) -> dict[str, Any]:
    task_root = repo / ".trellis/tasks"
    tasks: list[dict[str, Any]] = []
    if task_root.is_dir():
        for task_json in sorted(task_root.glob("*/task.json")):
            if (
                task_json.parent.is_symlink()
                or task_json.is_symlink()
                or not task_json.is_file()
            ):
                continue
            task = task_record(task_json)
            if task is not None:
                tasks.append(task)

    active: dict[str, Any] | None = None
    task_script = repo / ".trellis/scripts/task.py"
    if task_script.is_file():
        # Trellis >=0.6.14 offers machine-readable `current --json`; older
        # vendored copies reject the flag with a nonzero argparse exit, so
        # fall back to the bare-path prose output they print instead.
        active_path_text = ""
        result = run_command(
            [sys.executable, str(task_script), "current", "--json"], cwd=repo
        )
        if result.returncode == 0:
            try:
                payload = json.loads(result.stdout)
            except (json.JSONDecodeError, ValueError):
                payload = None
            if isinstance(payload, dict):
                current_task = payload.get("current_task")
                if isinstance(current_task, dict):
                    active_path_text = str(current_task.get("dir") or "").strip()
            else:
                # A variant that ignores unknown flags prints the bare path
                # with exit 0; keep interpreting that prose output.
                active_path_text = result.stdout.strip()
        else:
            result = run_command(
                [sys.executable, str(task_script), "current"], cwd=repo
            )
            active_path_text = (
                result.stdout.strip() if result.returncode == 0 else ""
            )
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
    completed_outside_archive = sorted(
        (task for task in tasks if task["status"] == "completed"),
        key=task_sort_key,
    )
    scanned_active = None
    if isinstance(active, dict):
        active_identity = (str(active.get("id", "")), str(active.get("path", "")))
        scanned_active = next(
            (
                task
                for task in tasks
                if (str(task.get("id", "")), str(task.get("path", "")))
                == active_identity
            ),
            None,
        )
    inventory_active_identity = (
        (str(scanned_active.get("id", "")), str(scanned_active.get("path", "")))
        if isinstance(scanned_active, dict)
        else None
    )
    inventory = sorted(
        tasks,
        key=lambda task: task_inventory_sort_key(
            task,
            active_identity=inventory_active_identity,
        ),
    )
    return {
        "activeTask": active,
        "inProgress": in_progress,
        "planned": planned,
        "completedOutsideArchive": completed_outside_archive,
        "tasks": select_items(inventory, prefix="T"),
    }


def normalize_roadmap_source_component(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")


def is_roadmap_source(relative: PurePosixPath) -> bool:
    if relative.suffix.casefold() not in ROADMAP_SOURCE_EXTENSIONS:
        return False
    normalized_directories = {
        normalize_roadmap_source_component(part) for part in relative.parts[:-1]
    }
    if normalized_directories & ROADMAP_EXCLUDED_DIRECTORIES:
        return False
    normalized_stem = normalize_roadmap_source_component(relative.stem)
    compact_stem = normalized_stem.replace("_", "")
    if any(
        compact_stem.startswith(prefix.replace("_", ""))
        for prefix in ROADMAP_SOURCE_STEMS
    ):
        return True
    return bool(normalized_directories & ROADMAP_SOURCE_DIRECTORIES)


def path_has_symlink(repo: Path, relative: PurePosixPath) -> bool:
    candidate = repo
    for part in relative.parts:
        candidate /= part
        if candidate.is_symlink():
            return True
    return False


def visible_markdown_text(value: str, *, limit: int = 500) -> str:
    text = MARKDOWN_IMAGE_RE.sub(r"\1", value)
    text = MARKDOWN_LINK_RE.sub(r"\1", text)
    text = MARKDOWN_REFERENCE_LINK_RE.sub(r"\1", text)
    text = MARKDOWN_TAG_RE.sub(" ", text)
    text = re.sub(r"\\([\\`*_{}\[\]()#+.!~-])", r"\1", text)
    text = MARKDOWN_OPEN_MARKER_RE.sub("", text)
    text = MARKDOWN_CLOSE_MARKER_RE.sub("", text)
    return safe_text(" ".join(text.split()), limit=limit)


def normalize_roadmap_match_text(value: str) -> str:
    text = PARKED_PREFIX_RE.sub(
        "",
        visible_markdown_text(value, limit=MAX_ROADMAP_LINE_CHARS),
    )
    return " ".join(text.casefold().split())


def bounded_roadmap_reference(
    raw_text: str,
    reference: str,
    *,
    path: bool = False,
) -> bool:
    boundary = r"a-z0-9_./-" if path else r"a-z0-9_-"
    return bool(
        re.search(
            rf"(?<![{boundary}]){re.escape(reference)}(?![{boundary}])",
            raw_text,
        )
    )


def roadmap_task_match_records(
    repo: Path,
    tasks: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for task in tasks:
        record = dict(task)
        raw_path = task.get("path")
        if isinstance(raw_path, str):
            relative = PurePosixPath(raw_path)
            if (
                not relative.is_absolute()
                and relative.parts
                and all(part not in {"", ".", ".."} for part in relative.parts)
            ):
                task_json = repo.joinpath(".trellis", *relative.parts, "task.json")
                if not path_has_symlink(repo, PurePosixPath(".trellis") / relative):
                    payload = read_json_object(task_json)
                    if payload is not None:
                        title = payload.get("title") or payload.get("name")
                        if isinstance(title, str):
                            record["title"] = safe_text(
                                title,
                                limit=MAX_ROADMAP_LINE_CHARS,
                            )
        records.append(record)
    return records


def roadmap_task_reference(raw_text: str, tasks: Sequence[Mapping[str, Any]]) -> bool:
    raw_folded = raw_text.casefold()
    normalized = normalize_roadmap_match_text(raw_text)
    for task in tasks:
        title = normalize_roadmap_match_text(str(task.get("title", "")))
        if title and normalized == title:
            return True
        path = str(task.get("path", "")).casefold().strip()
        if path:
            for path_reference in (path, f".trellis/{path}"):
                if bounded_roadmap_reference(
                    raw_folded,
                    path_reference,
                    path=True,
                ):
                    return True
        references = {
            str(task.get("id", "")).casefold().strip(),
            PurePosixPath(path).name if path else "",
        }
        for reference in references:
            if reference and bounded_roadmap_reference(raw_folded, reference):
                return True
    return False


def roadmap_item_text(line: str) -> str | None:
    if CHECKED_TASK_RE.match(line):
        return None
    match = UNCHECKED_TASK_RE.match(line)
    if match is None:
        match = TOP_LEVEL_LIST_RE.match(line)
    if match is None:
        return None
    raw_text = match.group(1).strip()
    if not raw_text or CONTROL_RE.search(raw_text):
        return None
    return raw_text


def collect_roadmap_candidates(
    repo: Path,
    tasks: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    match_tasks = roadmap_task_match_records(repo, tasks)
    result = run_command(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=repo,
    )
    if result.returncode != 0:
        return [], ["roadmap source scan incomplete: Git file inventory unavailable"]

    sources: list[tuple[PurePosixPath, Path]] = []
    for raw_path in result.stdout.split("\0"):
        if not raw_path:
            continue
        relative = PurePosixPath(raw_path)
        if (
            relative.is_absolute()
            or not relative.parts
            or any(part in {"", ".", ".."} for part in relative.parts)
            or not is_roadmap_source(relative)
            or path_has_symlink(repo, relative)
        ):
            continue
        path = repo.joinpath(*relative.parts)
        if path.is_file():
            sources.append((relative, path))

    sources.sort(key=lambda item: (item[0].as_posix().casefold(), item[0].as_posix()))
    diagnostics: list[str] = []
    if len(sources) > MAX_ROADMAP_SOURCE_FILES:
        diagnostics.append(
            "roadmap source scan incomplete: "
            f"limited {len(sources)} matching files to {MAX_ROADMAP_SOURCE_FILES}"
        )
        sources = sources[:MAX_ROADMAP_SOURCE_FILES]

    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    limit_reached = False
    for relative, path in sources:
        try:
            size = path.stat().st_size
        except OSError:
            diagnostics.append(
                "roadmap source scan incomplete: cannot stat " + relative.as_posix()
            )
            continue
        if size > MAX_ROADMAP_SOURCE_BYTES:
            diagnostics.append(
                "roadmap source scan incomplete: skipped oversized file "
                + relative.as_posix()
            )
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="strict").splitlines()
        except (OSError, UnicodeError):
            diagnostics.append(
                "roadmap source scan incomplete: cannot read " + relative.as_posix()
            )
            continue
        overlong_line = False
        for line_number, line in enumerate(lines, start=1):
            if len(line) > MAX_ROADMAP_LINE_CHARS:
                overlong_line = True
                continue
            raw_text = roadmap_item_text(line)
            if raw_text is None or roadmap_task_reference(raw_text, match_tasks):
                continue
            summary = visible_markdown_text(raw_text)
            key = normalize_roadmap_match_text(raw_text)
            if not summary or not key or key in seen:
                continue
            seen.add(key)
            candidates.append(
                {
                    "kind": "roadmap",
                    "summary": summary,
                    "source": f"roadmap:{relative.as_posix()}:{line_number}",
                    "path": relative.as_posix(),
                    "line": line_number,
                }
            )
            if len(candidates) >= MAX_ROADMAP_ITEMS:
                diagnostics.append(
                    "roadmap source scan incomplete: "
                    f"limited emitted items to {MAX_ROADMAP_ITEMS}"
                )
                limit_reached = True
                break
        if overlong_line:
            diagnostics.append(
                "roadmap source scan incomplete: skipped overlong line(s) in "
                + relative.as_posix()
            )
        if limit_reached:
            break
    return candidates, diagnostics


class _UnsafeSiblingPath(OSError):
    """Path-policy rejection for a trusted sibling-module load: symlink, any
    non-regular node (socket / FIFO / directory), a missing path, or a platform
    without ``O_NOFOLLOW``. Distinct from an arbitrary open/read ``OSError`` so a
    caller can route path-policy failures through its own boundary while a real
    I/O fault still reaches the caller's original handler.

    ``reason`` carries the specific policy verdict so a caller can distinguish a
    genuinely absent helper (``missing``) from one that is present but refused
    (``no_o_nofollow`` / ``symlink`` / ``non_regular``). The refusal behavior is
    unchanged either way; only the surfaced diagnostic differs."""

    def __init__(self, message: str, *, reason: str = "unsafe") -> None:
        super().__init__(message)
        self.reason = reason


class _SiblingLoadError(ImportError):
    """The import spec/loader could not be constructed for an already path-safe
    sibling. Subclasses ``ImportError`` so callers whose existing handlers list
    ``ImportError`` classify it exactly as before."""


# errno values where the path itself violates policy: a missing final component,
# a symlinked final component (``ELOOP`` under ``O_NOFOLLOW``), or a non-directory
# in the parent chain. Any other open/read errno is a genuine I/O fault.
_PATH_POLICY_ERRNOS = frozenset(
    value
    for value in (getattr(errno, name, None) for name in ("ENOENT", "ELOOP", "ENOTDIR"))
    if value is not None
)


def _read_trusted_sibling_source(path: Path) -> bytes:
    """Read a sibling module's source with no TOCTOU window.

    Fails closed when ``O_NOFOLLOW`` is unavailable. An advisory ``lstat`` picks
    the caller branch for an unsafe path (missing / symlink / any non-regular
    node) but never authorizes a read; the authoritative gate is the fd-anchored
    ``O_NOFOLLOW`` open plus same-descriptor ``fstat``. ``O_NONBLOCK`` keeps a
    FIFO from blocking the open. Executes nothing. Raises ``_UnsafeSiblingPath``
    for a path-policy failure and lets any other open/read ``OSError`` propagate
    unchanged.
    """
    if not hasattr(os, "O_NOFOLLOW"):
        raise _UnsafeSiblingPath(
            "O_NOFOLLOW unavailable; refusing sibling load", reason="no_o_nofollow"
        )
    try:
        advisory = os.lstat(path)
    except OSError as error:
        if error.errno == errno.ENOENT:
            reason = "missing"
        elif error.errno == errno.ELOOP:
            reason = "symlink"
        elif error.errno == errno.ENOTDIR:
            # A non-directory parent component ⇒ no regular file is resolvable at
            # the computed path ⇒ "not found", not "present but refused".
            reason = "missing"
        else:
            reason = "unsafe"
        raise _UnsafeSiblingPath(str(error), reason=reason) from error
    if stat.S_ISLNK(advisory.st_mode):
        raise _UnsafeSiblingPath(f"{path} is a symlink", reason="symlink")
    if not stat.S_ISREG(advisory.st_mode):
        raise _UnsafeSiblingPath(
            f"{path} is not a regular file", reason="non_regular"
        )
    flags = (
        os.O_RDONLY
        | os.O_NOFOLLOW
        | getattr(os, "O_NONBLOCK", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        if error.errno in _PATH_POLICY_ERRNOS:
            if error.errno == errno.ENOENT:
                reason = "missing"
            elif error.errno == errno.ELOOP:
                reason = "symlink"
            elif error.errno == errno.ENOTDIR:
                # Parity with the advisory branch: a non-directory parent means the
                # module is not resolvable ⇒ "missing", not "non_regular".
                reason = "missing"
            else:
                # Defensive: unreachable for the current errno set, safe if it grows.
                reason = "non_regular"
            raise _UnsafeSiblingPath(str(error), reason=reason) from error
        raise
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise _UnsafeSiblingPath(
                f"{path} is not a regular file", reason="non_regular"
            )
        chunks = []
        while True:
            block = os.read(descriptor, 65536)
            if not block:
                break
            chunks.append(block)
    finally:
        os.close(descriptor)
    return b"".join(chunks)


def _exec_sibling_module(source, path, module_name, *, register):
    """Compile and exec already-read (fd-verified) source into a fresh module.

    The module object is built with the real ``spec_from_file_location`` +
    ``module_from_spec`` pair, so its metadata matches the retired loader
    exactly; neither call reads or executes the file. Execution runs on the bytes
    already read from the verified descriptor, never ``loader.exec_module``. When
    ``register`` is true the module is placed in ``sys.modules`` before
    ``compile`` so a compile-time failure leaves the entry registered, matching
    the retired pre-exec registration.
    """
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise _SiblingLoadError(f"cannot construct loader for {path}")
    module = importlib.util.module_from_spec(spec)
    if register:
        sys.modules[module_name] = module
    code = compile(source, str(path), "exec")
    # Trusted sibling; source read from an fd verified regular + non-symlink.
    exec(code, module.__dict__)  # nosec B102
    return module


def collect_work_loop(repo: Path) -> dict[str, Any]:
    """Read the shared user-local loop ledger without mutating it."""
    helper = Path(__file__).resolve().with_name("sd-ai-command-pack-work-loop.py")
    try:
        source = _read_trusted_sibling_source(helper)
        with suppress_bytecode_writes():
            module = _exec_sibling_module(
                source, helper, "sd_ai_command_pack_status_work_loop", register=False
            )
        snapshot = module.status_snapshot(repo)
    except _UnsafeSiblingPath as error:
        if error.reason == "missing":
            return {
                "status": "unavailable",
                "error": "work-loop helper is not installed",
            }
        return {
            "status": "unavailable",
            "error": (
                f"work-loop helper present but refused ({error.reason})"
            ),
        }
    except (
        AttributeError,
        ImportError,
        KeyError,
        OSError,
        RuntimeError,
        SyntaxError,
        TypeError,
        ValueError,
    ) as error:
        return {"status": "invalid", "error": safe_text(error, limit=500)}
    if not isinstance(snapshot, dict):
        return {"status": "invalid", "error": "work-loop helper returned invalid data"}
    return validate_work_loop_snapshot(snapshot)


def validate_work_loop_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Fail closed when a loaded helper does not honor the status contract."""
    status = snapshot.get("status")
    if not isinstance(status, str) or not status:
        return {
            "status": "invalid",
            "error": "work-loop helper returned snapshot without a valid status",
        }
    if status in WORK_LOOP_TERMINAL_STATUSES:
        terminal_snapshot = {"status": status}
        error = snapshot.get("error")
        if status == "none":
            return terminal_snapshot
        if status == "invalid" and error is None:
            return {
                "status": "invalid",
                "error": "work-loop helper reported invalid state without diagnostics",
            }
        if error is None:
            return terminal_snapshot
        if not isinstance(error, str):
            return {
                "status": "invalid",
                "error": "work-loop helper returned invalid terminal snapshot field: error",
            }
        normalized_error = safe_text(error, limit=500)
        if not normalized_error:
            if status == "invalid":
                return {
                    "status": "invalid",
                    "error": "work-loop helper reported invalid state without diagnostics",
                }
            return {
                "status": "invalid",
                "error": "work-loop helper returned invalid terminal snapshot field: error",
            }
        terminal_snapshot["error"] = normalized_error
        return terminal_snapshot
    if status not in WORK_LOOP_RUN_STATUSES:
        return {
            "status": "invalid",
            "error": "work-loop helper returned unsupported status",
        }

    def invalid_field(field: str) -> dict[str, Any]:
        return {
            "status": "invalid",
            "error": f"work-loop helper returned invalid run snapshot field: {field}",
        }

    normalized: dict[str, Any] = {"status": status}
    required_string_limits = {
        "runId": 120,
        "mode": 40,
        "selector": 120,
        "phase": 80,
        "focusMode": 40,
        "heartbeatAt": 80,
    }
    for field in WORK_LOOP_REQUIRED_STRING_FIELDS:
        value = snapshot.get(field)
        if not isinstance(value, str) or not value:
            return invalid_field(field)
        normalized_value = safe_text(value, limit=required_string_limits[field])
        if not normalized_value:
            return invalid_field(field)
        normalized[field] = normalized_value
    iteration = snapshot.get("iteration")
    if (
        isinstance(iteration, bool)
        or not isinstance(iteration, int)
        or iteration < 1
    ):
        return invalid_field("iteration")
    normalized["iteration"] = iteration
    focus = snapshot.get("focus")
    if not isinstance(focus, list) or not all(
        isinstance(value, str) for value in focus
    ):
        return invalid_field("focus")
    if len(focus) > MAX_ITEMS:
        return invalid_field("focus")
    normalized_focus = [safe_text(value, limit=160) for value in focus]
    if any(not value for value in normalized_focus):
        return invalid_field("focus")
    normalized["focus"] = normalized_focus

    counters = snapshot.get("counters")
    if (
        not isinstance(counters, dict)
        or len(counters) > MAX_ITEMS
        or any(
            not isinstance(key, str)
            or not key
            or isinstance(value, bool)
            or not isinstance(value, int)
            or value < 0
            for key, value in counters.items()
        )
    ):
        return invalid_field("counters")
    normalized_counters: dict[str, int] = {}
    for key, value in counters.items():
        normalized_key = safe_text(key, limit=80)
        if not normalized_key or normalized_key in normalized_counters:
            return invalid_field("counters")
        normalized_counters[normalized_key] = value
    normalized["counters"] = normalized_counters

    context_health = snapshot.get("contextHealth")
    if not isinstance(context_health, dict):
        return invalid_field("contextHealth")
    health_level = context_health.get("level")
    if not isinstance(health_level, str) or not health_level:
        return invalid_field("contextHealth.level")
    normalized_health: dict[str, Any] = {
        "level": safe_text(health_level, limit=40)
    }
    if not normalized_health["level"]:
        return invalid_field("contextHealth.level")
    if "epoch" in context_health:
        epoch = context_health["epoch"]
        if isinstance(epoch, bool) or not isinstance(epoch, int) or epoch < 0:
            return invalid_field("contextHealth.epoch")
        normalized_health["epoch"] = epoch
    if "reasons" in context_health:
        reasons = context_health["reasons"]
        if (
            not isinstance(reasons, list)
            or len(reasons) > MAX_ITEMS
            or not all(isinstance(value, str) for value in reasons)
        ):
            return invalid_field("contextHealth.reasons")
        normalized_reasons = [safe_text(value, limit=240) for value in reasons]
        if any(not value for value in normalized_reasons):
            return invalid_field("contextHealth.reasons")
        normalized_health["reasons"] = normalized_reasons
    normalized["contextHealth"] = normalized_health

    checkpoint = snapshot.get("checkpoint")
    if not isinstance(checkpoint, dict):
        return invalid_field("checkpoint")
    checkpoint_state = checkpoint.get("state")
    if not isinstance(checkpoint_state, str) or not checkpoint_state:
        return invalid_field("checkpoint.state")
    normalized_checkpoint: dict[str, Any] = {
        "state": safe_text(checkpoint_state, limit=40)
    }
    if not normalized_checkpoint["state"]:
        return invalid_field("checkpoint.state")
    for field, limit in (
        ("target", 240),
        ("reason", 500),
        ("resumePhase", 40),
    ):
        if field not in checkpoint:
            continue
        value = checkpoint[field]
        if value is not None and not isinstance(value, str):
            return invalid_field(f"checkpoint.{field}")
        if value is None:
            normalized_checkpoint[field] = None
            continue
        normalized_value = safe_text(value, limit=limit)
        if not normalized_value:
            return invalid_field(f"checkpoint.{field}")
        normalized_checkpoint[field] = normalized_value
    normalized["checkpoint"] = normalized_checkpoint

    for field, limit in (
        ("until", 40),
        ("task", 160),
        ("branch", 200),
        ("head", 120),
        ("baseBranch", 200),
        ("prUrl", 240),
        ("lastShippedSha", 80),
        ("stopReason", 500),
    ):
        if field not in snapshot:
            continue
        value = snapshot[field]
        if value is not None and not isinstance(value, str):
            return invalid_field(field)
        if value is None:
            normalized[field] = None
            continue
        normalized_value = safe_text(value, limit=limit)
        if not normalized_value:
            return invalid_field(field)
        normalized[field] = normalized_value

    if "prNumber" in snapshot:
        pr_number = snapshot["prNumber"]
        if pr_number is not None and (
            isinstance(pr_number, bool)
            or not isinstance(pr_number, int)
            or pr_number < 1
        ):
            return invalid_field("prNumber")
        normalized["prNumber"] = pr_number

    if "lock" in snapshot:
        lock = snapshot["lock"]
        if not isinstance(lock, dict):
            return invalid_field("lock")
        normalized_lock: dict[str, Any] = {}
        for field in ("present", "stale"):
            if field not in lock:
                continue
            if not isinstance(lock[field], bool):
                return invalid_field(f"lock.{field}")
            normalized_lock[field] = lock[field]
        if "runId" in lock:
            lock_run_id = lock["runId"]
            if lock_run_id is not None and not isinstance(lock_run_id, str):
                return invalid_field("lock.runId")
            if lock_run_id is None:
                normalized_lock["runId"] = None
            else:
                normalized_lock_run_id = safe_text(lock_run_id, limit=120)
                if not normalized_lock_run_id:
                    return invalid_field("lock.runId")
                normalized_lock["runId"] = normalized_lock_run_id
        normalized["lock"] = normalized_lock

    if "terminalReconciliation" in snapshot:
        terminal = snapshot["terminalReconciliation"]
        if terminal is None:
            normalized["terminalReconciliation"] = None
        else:
            if not isinstance(terminal, dict) or set(terminal) != {
                "status",
                "reconciledAt",
                "archivedTask",
                "taskId",
                "delivery",
                "bookkeeping",
                "observed",
            }:
                return invalid_field("terminalReconciliation")
            if terminal.get("status") != "verified":
                return invalid_field("terminalReconciliation.status")
            if status not in {"stopped", "completed"}:
                return invalid_field("terminalReconciliation")
            normalized_terminal: dict[str, Any] = {"status": "verified"}
            for field, limit in (
                ("reconciledAt", 80),
                ("archivedTask", 300),
                ("taskId", 200),
            ):
                value = terminal.get(field)
                if not isinstance(value, str):
                    return invalid_field(f"terminalReconciliation.{field}")
                normalized_value = safe_text(value, limit=limit)
                if not normalized_value:
                    return invalid_field(f"terminalReconciliation.{field}")
                normalized_terminal[field] = normalized_value
            try:
                datetime.fromisoformat(
                    normalized_terminal["reconciledAt"].replace("Z", "+00:00")
                )
            except ValueError:
                return invalid_field("terminalReconciliation.reconciledAt")
            archived_path = normalized_terminal["archivedTask"]
            pure_archived = PurePosixPath(archived_path)
            if (
                "\\" in archived_path
                or pure_archived.as_posix() != archived_path
                or pure_archived.parts[:3] != (".trellis", "tasks", "archive")
                or len(pure_archived.parts) < 5
                or any(part in {"", ".", ".."} for part in pure_archived.parts)
            ):
                return invalid_field("terminalReconciliation.archivedTask")

            def normalize_pr(value: object) -> dict[str, Any] | None:
                if not isinstance(value, dict) or set(value) != {
                    "prNumber",
                    "prUrl",
                    "head",
                    "mergeCommit",
                }:
                    return None
                number = value.get("prNumber")
                url = value.get("prUrl")
                head = value.get("head")
                merge_commit = value.get("mergeCommit")
                if (
                    isinstance(number, bool)
                    or not isinstance(number, int)
                    or number < 1
                    or not isinstance(url, str)
                    or not isinstance(head, str)
                    or COMMIT_RE.fullmatch(head) is None
                    or not isinstance(merge_commit, str)
                    or COMMIT_RE.fullmatch(merge_commit) is None
                ):
                    return None
                safe_url = safe_text(url, limit=500)
                try:
                    split = urlsplit(safe_url)
                    hostname = split.hostname
                    _ = split.port
                    username = split.username
                    password = split.password
                except ValueError:
                    return None
                final_component = split.path.rstrip("/").rsplit("/", 1)[-1]
                if (
                    split.scheme not in {"http", "https"}
                    or not hostname
                    or username is not None
                    or password is not None
                    or split.query
                    or split.fragment
                    or not final_component.isdigit()
                    or int(final_component) != number
                ):
                    return None
                return {
                    "prNumber": number,
                    "prUrl": safe_url,
                    "head": head,
                    "mergeCommit": merge_commit,
                }

            delivery = normalize_pr(terminal.get("delivery"))
            if delivery is None:
                return invalid_field("terminalReconciliation.delivery")
            normalized_terminal["delivery"] = delivery
            bookkeeping = terminal.get("bookkeeping")
            if bookkeeping is None:
                normalized_terminal["bookkeeping"] = None
            else:
                normalized_bookkeeping = normalize_pr(bookkeeping)
                if normalized_bookkeeping is None:
                    return invalid_field("terminalReconciliation.bookkeeping")
                normalized_terminal["bookkeeping"] = normalized_bookkeeping
            observed = terminal.get("observed")
            if (
                not isinstance(observed, dict)
                or set(observed) != {"branch", "head"}
                or not isinstance(observed.get("branch"), str)
                or not safe_text(observed["branch"], limit=200)
                or not isinstance(observed.get("head"), str)
                or COMMIT_RE.fullmatch(observed["head"]) is None
            ):
                return invalid_field("terminalReconciliation.observed")
            normalized_terminal["observed"] = {
                "branch": safe_text(observed["branch"], limit=200),
                "head": observed["head"],
            }
            normalized["terminalReconciliation"] = normalized_terminal

    return normalized


def collect_recovery(repo: Path) -> dict[str, Any]:
    """Classify pack-created recovery artifacts read-only for status.

    Delegates to the recovery-artifacts helper's read-only classifier and
    reduces the result to a bounded summary. Never creates, repairs, or deletes
    a receipt or Git artifact.
    """
    helper = Path(__file__).resolve().with_name(
        "sd-ai-command-pack-recovery-artifacts.py"
    )
    try:
        source = _read_trusted_sibling_source(helper)
        with suppress_bytecode_writes():
            module = _exec_sibling_module(
                source, helper, "sd_ai_command_pack_status_recovery", register=False
            )
        classified = module.classify_repository(repo)
    except _UnsafeSiblingPath as error:
        if error.reason == "missing":
            return {
                "status": "unavailable",
                "error": "recovery-artifacts helper is not installed",
            }
        return {
            "status": "unavailable",
            "error": (
                f"recovery-artifacts helper present but refused ({error.reason})"
            ),
        }
    except (
        AttributeError,
        ImportError,
        KeyError,
        OSError,
        RuntimeError,
        SyntaxError,
        TypeError,
        ValueError,
    ) as error:
        return {"status": "invalid", "error": safe_text(error, limit=500)}
    if not isinstance(classified, dict):
        return {
            "status": "invalid",
            "error": "recovery-artifacts helper returned invalid data",
        }
    expected_schema = getattr(module, "SCHEMA_VERSION", None)
    if expected_schema is None or classified.get("schemaVersion") != expected_schema:
        return {
            "status": "invalid",
            "error": "recovery-artifacts helper returned an unexpected schema version",
        }
    return summarize_recovery(classified)


def summarize_recovery(classified: Mapping[str, Any]) -> dict[str, Any]:
    """Reduce a recovery classification to a bounded, read-only status summary."""
    counts: dict[str, int] = {}
    counts_raw = classified.get("counts")
    if isinstance(counts_raw, Mapping):
        for key, value in counts_raw.items():
            if (
                isinstance(key, str)
                and isinstance(value, int)
                and not isinstance(value, bool)
                and value >= 0
            ):
                counts[safe_text(key, limit=40)] = value

    actionable: list[dict[str, str]] = []

    def add(kind: object, classification: object, reference: object, detail: object) -> None:
        if len(actionable) >= MAX_ITEMS:
            return
        actionable.append(
            {
                "type": safe_text(kind, limit=40),
                "classification": safe_text(classification, limit=40),
                "reference": safe_text(reference, limit=200),
                "detail": safe_text(detail, limit=200),
            }
        )

    receipts = classified.get("receipts")
    if isinstance(receipts, list):
        for item in receipts:
            if not isinstance(item, Mapping):
                continue
            classification = item.get("classification")
            if classification == "active":
                continue  # in-use artifacts are not actionable
            add(
                item.get("type"),
                classification,
                item.get("reference"),
                item.get("detail"),
            )

    unowned = classified.get("unowned")
    if isinstance(unowned, list):
        for entry in unowned:
            if isinstance(entry, Mapping):
                add(
                    entry.get("type"),
                    "unowned-artifact",
                    entry.get("reference"),
                    entry.get("detail"),
                )

    corrupt = classified.get("corrupt")
    if isinstance(corrupt, list):
        for entry in corrupt:
            if isinstance(entry, Mapping):
                add("receipt", "corrupt", entry.get("reference"), entry.get("reason"))

    return {
        "status": "ok",
        "counts": counts,
        "total": sum(counts.values()),
        "actionable": actionable,
    }


def machine_scope_api() -> Any:
    """Load the machine-scope install engine from beside this script.

    `installer/` sits next to the directory holding this script in every
    shipped arrangement: `scripts/` in a pack checkout, `bin/` under a plugin
    root. A vendored consumer repository carries the scripts without the
    package; that absence is reported, never guessed around.

    The engine resolves the shared helper library through
    ``sys.modules["sd_ai_command_pack_lib"]`` first, and this script has
    already registered that name from its own directory, so the state-root
    ladder in play is the copy beside THIS script rather than the one beside
    the package. Every shipped arrangement ships the same file in both places;
    they diverge only mid-skew (a refreshed package beside stale scripts). The
    loader's first-import-wins rule is deliberate and is not worked around
    here.
    """
    root = Path(__file__).resolve().parent.parent
    module_path = root / "installer" / "machinescope.py"
    if not module_path.is_file():
        raise RuntimeError(
            f"machine-scope engine is not installed beside this script ({module_path})"
        )
    root_path = str(root)
    inserted = root_path not in sys.path
    if inserted:
        sys.path.insert(0, root_path)
    try:
        with suppress_bytecode_writes():
            from installer import machinescope
    except ImportError as error:
        raise RuntimeError(
            f"machine-scope engine cannot be imported: {safe_text(error, limit=200)}"
        ) from error
    finally:
        if inserted:
            sys.path.remove(root_path)
    return machinescope


def collect_plugin_version(repo: Path) -> tuple[str, str | None]:
    """The installed plugin version, or ``unavailable`` and why.

    Every discovery failure -- no CLI, a nonzero exit, unparsable output, a
    missing or duplicated entry, an entry without a version -- reports
    ``unavailable``. A guess here would let a broken `claude` masquerade as an
    up-to-date machine.
    """
    if shutil.which("claude") is None:
        return MACHINE_UNAVAILABLE, "the Claude Code CLI is not on PATH"
    result = run_command(["claude", "plugin", "list", "--json"], cwd=repo)
    if result.returncode != 0:
        return (
            MACHINE_UNAVAILABLE,
            f"claude plugin list --json exited {result.returncode}",
        )
    try:
        entries = json.loads(result.stdout)
    except (json.JSONDecodeError, ValueError):
        return MACHINE_UNAVAILABLE, "claude plugin list --json output is not JSON"
    if not isinstance(entries, list):
        return (
            MACHINE_UNAVAILABLE,
            "claude plugin list --json did not return a plugin array",
        )
    matches = [
        entry
        for entry in entries
        if isinstance(entry, dict) and entry.get("id") == MACHINE_PLUGIN_ID
    ]
    if not matches:
        return MACHINE_UNAVAILABLE, f"plugin {MACHINE_PLUGIN_ID} is not installed"
    if len(matches) > 1:
        return (
            MACHINE_UNAVAILABLE,
            f"claude plugin list --json reports {MACHINE_PLUGIN_ID} more than once",
        )
    version = matches[0].get("version")
    normalized = safe_text(version, limit=80) if isinstance(version, str) else ""
    if not normalized:
        return (
            MACHINE_UNAVAILABLE,
            f"the listed {MACHINE_PLUGIN_ID} entry carries no version",
        )
    return normalized, None


def machine_receipt_state(
    *,
    home: Path | None,
    environ: Mapping[str, str] | None,
    state_home: Path | None,
) -> dict[str, Any]:
    """Receipt state from the engine, without needing a plugin to find it."""

    def unavailable(detail: str) -> dict[str, Any]:
        return {
            "state": MACHINE_UNAVAILABLE,
            "packVersion": None,
            "receiptPath": None,
            "detail": safe_text(detail, limit=300),
        }

    try:
        machinescope = machine_scope_api()
    except RuntimeError as error:
        return unavailable(str(error))
    try:
        expected_schema = machinescope.STATUS_SCHEMA_VERSION
        report = machinescope.status(
            home=home,
            environ=environ,
            state_home=state_home,
        )
    except (AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError) as error:
        # MachineInstallError (an unresolvable home or state root) subclasses
        # RuntimeError, so a machine the engine cannot reason about reports
        # unavailable instead of raising through a read-only status run.
        return unavailable(f"machine-scope engine failed: {safe_text(error, limit=200)}")
    if not isinstance(report, dict) or report.get("schemaVersion") != expected_schema:
        return unavailable("machine-scope engine returned an unexpected schema version")
    state = report.get("state")
    if state not in MACHINE_RECEIPT_STATES:
        return unavailable("machine-scope engine returned an unsupported state")
    receipt_path = report.get("receiptPath")
    pack_version = report.get("packVersion")
    detail = report.get("detail")
    return {
        "state": state,
        "packVersion": (
            safe_text(pack_version, limit=80)
            if state == "installed" and isinstance(pack_version, str) and pack_version.strip()
            else None
        ),
        "receiptPath": (
            safe_text(receipt_path, limit=500) if isinstance(receipt_path, str) else None
        ),
        "detail": safe_text(detail, limit=300) if isinstance(detail, str) and detail else None,
    }


def machine_comparison(
    state: object,
    pack_version: object,
    plugin_version: object,
) -> str:
    """Compare the two halves of an update, refusing to guess at either.

    ``unknown`` whenever a version is missing on either side: a broken `claude`
    CLI or an unreadable receipt must never present as ``current``.
    """
    if plugin_version == MACHINE_UNAVAILABLE or state == MACHINE_UNAVAILABLE:
        return "unknown"
    if state == "installed" and pack_version and pack_version == plugin_version:
        return "current"
    return "skew"


def collect_machine_scope(
    repo: Path,
    *,
    home: Path | None = None,
    environ: Mapping[str, str] | None = None,
    state_home: Path | None = None,
) -> dict[str, Any]:
    """Machine-scope install state against the installed plugin.

    Advisory: this reports on the machine, not the repository, and never
    changes the exit status.
    """
    receipt = machine_receipt_state(home=home, environ=environ, state_home=state_home)
    plugin_version, plugin_detail = collect_plugin_version(repo)
    return {
        "schemaVersion": MACHINE_SCOPE_SCHEMA_VERSION,
        "state": receipt["state"],
        "packVersion": receipt["packVersion"],
        "receiptPath": receipt["receiptPath"],
        "detail": receipt["detail"],
        "pluginId": MACHINE_PLUGIN_ID,
        "pluginVersion": plugin_version,
        "pluginDetail": plugin_detail,
        "comparison": machine_comparison(
            receipt["state"],
            receipt["packVersion"],
            plugin_version,
        ),
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


def collect_follow_ups(
    report: Mapping[str, Any],
    *,
    roadmap_candidates: Sequence[Mapping[str, Any]] = (),
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def add(
        kind: str,
        summary: str,
        source: str,
        *,
        path: str | None = None,
        line: int | None = None,
    ) -> None:
        normalized_summary = safe_text(summary, limit=500)
        key = (kind, normalized_summary)
        if key in seen:
            return
        seen.add(key)
        candidate: dict[str, Any] = {
            "kind": kind,
            "summary": normalized_summary,
            "source": source,
        }
        if path is not None:
            candidate["path"] = safe_text(path, limit=500)
        if line is not None:
            candidate["line"] = line
        candidates.append(candidate)

    anomalies = report.get("anomalies")
    if isinstance(anomalies, list):
        for anomaly in anomalies:
            add("issue", f"Resolve status anomaly: {anomaly}", "anomalies")

    git_value = report.get("git")
    git: Mapping[str, Any] = git_value if isinstance(git_value, dict) else {}
    tree_value = git.get("workingTree")
    tree: Mapping[str, Any] = tree_value if isinstance(tree_value, dict) else {}
    if tree.get("state") == "dirty":
        add(
            "action",
            "Review and commit or intentionally discard the current working-tree changes.",
            "git.workingTree",
        )
    sync = git.get("syncState")
    if sync == "behind":
        add(
            "action",
            "Fast-forward the current branch from its upstream before new work.",
            "git.syncState",
        )
    elif sync == "ahead":
        add(
            "action",
            "Push the local commits or confirm they are intentionally local-only.",
            "git.syncState",
        )
    elif sync == "diverged":
        add(
            "action",
            "Reconcile the diverged local and upstream histories before publishing.",
            "git.syncState",
        )
    elif sync == "no-upstream":
        add(
            "action",
            "Configure or verify the branch upstream before publishing new work.",
            "git.syncState",
        )

    github = report.get("github")
    if isinstance(github, dict):
        pr = github.get("currentPr")
        if isinstance(pr, dict) and pr.get("state") == "OPEN":
            add(
                "action",
                f"Continue PR #{pr.get('number')} through sd-ship or sd-housekeeping.",
                "github.currentPr",
            )

    work_loop = report.get("workLoop")
    if isinstance(work_loop, dict):
        loop_status = work_loop.get("status")
        run_id = work_loop.get("runId")
        if loop_status == "active":
            add(
                "action",
                f"Resume active SD work loop {run_id} at iteration "
                f"{work_loop.get('iteration')} phase {work_loop.get('phase')}.",
                "workLoop.status",
            )
        elif loop_status == "paused":
            add(
                "action",
                f"Resume paused SD work loop {run_id} from its recorded checkpoint.",
                "workLoop.status",
            )
        terminal_reconciliation = work_loop.get("terminalReconciliation")
        terminal_verified = (
            isinstance(terminal_reconciliation, dict)
            and terminal_reconciliation.get("status") == "verified"
        )
        health = work_loop.get("contextHealth")
        if (
            isinstance(health, dict)
            and health.get("level") == "red"
            and not terminal_verified
        ):
            add(
                "issue",
                "Reconcile the red SD work-loop checkpoint with live Trellis, Git, and PR state.",
                "workLoop.contextHealth",
            )

    trellis = report.get("trellis")
    if isinstance(trellis, dict) and trellis.get("completedOutsideArchive"):
        add(
            "action",
            "Archive completed active-root Trellis tasks with "
            "python3 ./.trellis/scripts/task.py archive <task-dir>.",
            "trellis.completedOutsideArchive",
        )

    versions = report.get("versions")
    if isinstance(versions, dict) and versions.get("packState") == "different":
        add(
            "recommendation",
            "Refresh the installed SD command pack to the source fleet version.",
            "versions.packState",
        )

    if isinstance(github, dict) and github.get("openIssuesStatus") == "available":
        issues = github.get("openIssues")
        if isinstance(issues, list):
            valid_issues = [issue for issue in issues if isinstance(issue, dict)]
            for issue in sorted(
                valid_issues,
                key=lambda item: (
                    item.get("number") if isinstance(item.get("number"), int) else 0,
                    str(item.get("title", "")).casefold(),
                ),
            ):
                add(
                    "issue",
                    f"Review GitHub issue #{issue.get('number')}: {issue.get('title')}",
                    "github.openIssues",
                )

    for candidate in roadmap_candidates:
        path = candidate.get("path")
        line = candidate.get("line")
        if (
            candidate.get("kind") == "roadmap"
            and isinstance(path, str)
            and isinstance(line, int)
            and not isinstance(line, bool)
            and line > 0
        ):
            add(
                "roadmap",
                str(candidate.get("summary", "")),
                str(candidate.get("source", "")),
                path=path,
                line=line,
            )

    return select_items(candidates, prefix="F")


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
            steps.append(f"Continue PR #{pr.get('number')} through sd-ship or sd-housekeeping.")
    work_loop = report.get("workLoop")
    if isinstance(work_loop, dict):
        loop_status = work_loop.get("status")
        run_id = work_loop.get("runId")
        if loop_status == "active":
            steps.append(
                f"Resume active SD work loop {run_id} at iteration "
                f"{work_loop.get('iteration')} phase {work_loop.get('phase')}."
            )
        elif loop_status == "paused":
            steps.append(
                f"Resume paused SD work loop {run_id} from its recorded checkpoint."
            )
        terminal_reconciliation = work_loop.get("terminalReconciliation")
        terminal_verified = (
            isinstance(terminal_reconciliation, dict)
            and terminal_reconciliation.get("status") == "verified"
        )
        if isinstance(work_loop.get("contextHealth"), dict) and work_loop[
            "contextHealth"
        ].get("level") == "red" and not terminal_verified:
            steps.append(
                "Reconcile the red SD work-loop checkpoint with live Trellis, Git, and PR state."
            )
    recovery = report.get("recoveryArtifacts")
    if isinstance(recovery, dict) and recovery.get("status") == "ok":
        counts = recovery.get("counts")
        counts = counts if isinstance(counts, dict) else {}
        cleanable = counts.get("safe-cleanable")
        if isinstance(cleanable, int) and cleanable > 0:
            steps.append(
                f"Retire {cleanable} safe-cleanable recovery artifact(s) via sd-housekeeping."
            )
        review = sum(
            value
            for name in ("needs-review", "unowned-artifact")
            if isinstance((value := counts.get(name)), int)
        )
        if review > 0:
            steps.append(
                f"Inspect {review} recovery artifact(s) flagged for review before cleanup."
            )
    trellis = report.get("trellis")
    if isinstance(trellis, dict):
        completed_outside_archive = trellis.get("completedOutsideArchive")
        if completed_outside_archive:
            steps.append(
                "Archive completed active-root Trellis tasks with "
                "python3 ./.trellis/scripts/task.py archive <task-dir>."
            )
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
    include_machine_scope: bool = True,
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
    work_loop = collect_work_loop(repo)
    recovery = collect_recovery(repo)
    trellis = collect_trellis(repo)
    roadmap_candidates, roadmap_diagnostics = collect_roadmap_candidates(
        repo,
        trellis.get("tasks", []),
    )
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
        "trellis": trellis,
        "workLoop": work_loop,
        "recoveryArtifacts": recovery,
        # Machine scope describes the machine, not this checkout: a fleet run
        # would repeat one identical answer per consumer, so it opts out.
        "machineScope": collect_machine_scope(repo) if include_machine_scope else None,
        "cleanupContext": {
            "sourceBranch": source_branch,
            "keepRemoteBranch": keep_remote_branch,
            "dryRun": dry_run,
        }
        if source_branch or dry_run
        else None,
        "anomalies": [safe_text(item, limit=500) for item in prior_anomalies]
        + anomalies
        + [safe_text(item, limit=500) for item in roadmap_diagnostics],
        "followUps": [],
        "nextSteps": [],
    }
    if work_loop.get("status") == "invalid":
        report["anomalies"].append(
            "work-loop state is invalid: "
            + safe_text(work_loop.get("error") or "unknown error", limit=400)
        )
    if recovery.get("status") == "invalid":
        report["anomalies"].append(
            "recovery-artifact state is invalid: "
            + safe_text(recovery.get("error") or "unknown error", limit=400)
        )
    machine_scope = report["machineScope"]
    if isinstance(machine_scope, dict) and machine_scope.get("state") == "invalid":
        # Same rule the two user-local ledgers above follow: a corrupt state
        # file is an anomaly, an unreadable one (`unavailable`) is not.
        report["anomalies"].append(
            "machine-scope receipt is invalid: "
            + safe_text(machine_scope.get("detail") or "unknown error", limit=400)
        )
    completed_outside_archive = trellis.get("completedOutsideArchive", [])
    if completed_outside_archive:
        shown = ", ".join(
            safe_text(task.get("path") or task.get("id"), limit=160)
            for task in completed_outside_archive[:HUMAN_ITEM_LIMIT]
        )
        suffix = (
            f"; +{len(completed_outside_archive) - HUMAN_ITEM_LIMIT} more"
            if len(completed_outside_archive) > HUMAN_ITEM_LIMIT
            else ""
        )
        report["anomalies"].append(
            f"{len(completed_outside_archive)} completed Trellis task(s) remain "
            f"outside .trellis/tasks/archive/: {shown}{suffix}"
        )
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
    report["followUps"] = collect_follow_ups(
        report,
        roadmap_candidates=roadmap_candidates,
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


def format_machine_scope(section: object) -> str:
    """One line carrying both halves of the update and their comparison.

    Both diagnostics are spelled out rather than reduced to a bare
    ``unavailable``: the reader has to be able to tell a machine with no
    install from one whose plugin version could not be read.
    """
    if not isinstance(section, dict):
        return "not collected; plugin unavailable; unknown"
    state = section.get("state")
    pack_version = section.get("packVersion")
    machine = (
        f"installed {pack_version}"
        if state == "installed" and pack_version
        else str(state)
    )
    detail = section.get("detail")
    if detail:
        machine += f" ({detail})"
    plugin = str(section.get("pluginVersion"))
    plugin_detail = section.get("pluginDetail")
    if plugin_detail:
        plugin += f" ({plugin_detail})"
    return f"{machine}; plugin {plugin}; {section.get('comparison')}"


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


def format_selectable_task(task: Mapping[str, Any]) -> str:
    parent = task.get("parent")
    parent_suffix = f"; parent {parent}" if isinstance(parent, str) else ""
    return (
        f"{task.get('selectionId')} [{task.get('status')}, {task.get('priority')}]: "
        f"{task.get('title')} ({task.get('id')}; {task.get('path')}{parent_suffix})"
    )


def render_selectable_inventory(
    heading: str,
    items: object,
    *,
    task_items: bool,
) -> None:
    print(f"\n==> {heading}")
    if not isinstance(items, list) or not items:
        print("none")
        return
    for item in items:
        if not isinstance(item, dict):
            continue
        if task_items:
            print(format_selectable_task(item))
        else:
            suffix = ""
            if (
                item.get("kind") == "roadmap"
                and isinstance(item.get("path"), str)
                and isinstance(item.get("line"), int)
            ):
                suffix = f" ({item.get('path')}:{item.get('line')})"
            print(
                f"{item.get('selectionId')} [{item.get('kind')}]: "
                f"{item.get('summary')}{suffix}"
            )


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
    held_elsewhere = set(git.get("branchesHeldElsewhere") or [])
    branch_labels = [
        f"{name} [worktree]" if name in held_elsewhere else name
        for name in local_branches
    ]
    print(f"- local branches ({len(local_branches)}): {', '.join(branch_labels) or 'none'}")
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

    print("\n==> Worktrees")
    worktrees = git.get("worktrees")
    if not isinstance(worktrees, dict) or worktrees.get("status") != "ok":
        print("- worktrees: unavailable")
    else:
        worktree_rows = worktrees.get("rows") or []
        if len(worktree_rows) <= 1:
            print("- linked worktrees: none")
        else:
            worktree_limit = HUMAN_ITEM_LIMIT * 2
            for row in worktree_rows[:worktree_limit]:
                if row.get("branch"):
                    checkout = f"branch {row['branch']}"
                elif row.get("detached"):
                    checkout = f"detached at {row.get('head') or 'unknown'}"
                elif row.get("bare"):
                    checkout = "bare"
                else:
                    checkout = "no branch"
                if row.get("prunable"):
                    state_label = "prunable"
                elif row.get("clean") is True:
                    state_label = "clean"
                elif row.get("clean") is False:
                    state_label = "dirty"
                else:
                    state_label = "unknown"
                if row.get("locked"):
                    state_label += ", locked"
                if row.get("reason"):
                    state_label += f" ({row['reason']})"
                suffix = " (reporting)" if row.get("current") else ""
                print(f"- {row.get('path')}: {checkout}, {state_label}{suffix}")
            if len(worktree_rows) > worktree_limit:
                print(f"- ; +{len(worktree_rows) - worktree_limit} more")

    versions = report["versions"]
    print("\n==> Delivery")
    pack = versions.get("sdAiCommandPack") or "not installed"
    target = versions.get("targetPack")
    target_suffix = f"; target {target}" if target else ""
    print(f"- SD pack: {pack} ({versions.get('packState')}{target_suffix})")
    print(f"- machine scope: {format_machine_scope(report.get('machineScope'))}")
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

    work_loop = report.get("workLoop")
    print("\n==> Work Loop")
    if not isinstance(work_loop, dict) or work_loop.get("status") == "none":
        print("- state: none")
    elif work_loop.get("status") in {"invalid", "unavailable"}:
        print(f"- state: {work_loop.get('status')}")
        print(f"- detail: {work_loop.get('error') or 'unavailable'}")
    else:
        print(
            f"- run: {work_loop.get('runId')} [{work_loop.get('status')}] "
            f"mode {work_loop.get('mode')}; selector {work_loop.get('selector')}"
        )
        print(
            f"- progress: iteration {work_loop.get('iteration')}; "
            f"phase {work_loop.get('phase')}; task {work_loop.get('task') or 'none'}; "
            f"PR {work_loop.get('prNumber') or 'none'}"
        )
        focus_values = work_loop.get("focus")
        focus_text = ", ".join(focus_values) if isinstance(focus_values, list) else ""
        print(
            f"- focus: {work_loop.get('focusMode') or 'none'}"
            f"{f' ({focus_text})' if focus_text else ''}"
        )
        health = work_loop.get("contextHealth")
        health_level = health.get("level") if isinstance(health, dict) else "unknown"
        checkpoint = work_loop.get("checkpoint")
        checkpoint_state = (
            checkpoint.get("state") if isinstance(checkpoint, dict) else "unknown"
        )
        print(
            f"- heartbeat: {work_loop.get('heartbeatAt') or 'unknown'}; "
            f"context health {health_level}; checkpoint {checkpoint_state}"
        )
        terminal = work_loop.get("terminalReconciliation")
        if isinstance(terminal, dict) and terminal.get("status") == "verified":
            print(
                "- terminal reconciliation: verified historical external completion; "
                f"reconciled {terminal.get('reconciledAt') or 'unknown'}"
            )
            delivery = terminal.get("delivery")
            bookkeeping = terminal.get("bookkeeping")
            external = (
                f"delivery PR #{delivery.get('prNumber')}"
                if isinstance(delivery, dict)
                else "delivery PR unknown"
            )
            if isinstance(bookkeeping, dict):
                external += f"; bookkeeping PR #{bookkeeping.get('prNumber')}"
            print(f"- external completion: {external}")
        print(f"- counters (loop-owned): {work_loop.get('counters') or {}}")
        if work_loop.get("stopReason"):
            print(f"- stop reason: {work_loop.get('stopReason')}")

    recovery = report.get("recoveryArtifacts")
    print("\n==> Recovery Artifacts")
    if not isinstance(recovery, dict) or recovery.get("status") not in {"ok", "invalid"}:
        detail = recovery.get("error") if isinstance(recovery, dict) else None
        print(f"- state: unavailable{f' ({detail})' if detail else ''}")
    elif recovery.get("status") == "invalid":
        print("- state: invalid")
        print(f"- detail: {recovery.get('error') or 'unavailable'}")
    else:
        counts_raw = recovery.get("counts")
        counts = counts_raw if isinstance(counts_raw, dict) else {}
        summary = ", ".join(
            f"{name} {count}"
            for name, count in sorted(counts.items())
            if isinstance(count, int) and count > 0
        )
        if not summary:
            print("- state: no tracked recovery artifacts")
        else:
            print(f"- tracked: {summary}")
            actionable = recovery.get("actionable")
            if isinstance(actionable, list) and actionable:
                for item in actionable[:HUMAN_ITEM_LIMIT]:
                    if not isinstance(item, dict):
                        continue
                    print(
                        f"  · {item.get('type')} {item.get('reference')} "
                        f"[{item.get('classification')}]: {item.get('detail')}"
                    )
                extra = len(actionable) - HUMAN_ITEM_LIMIT
                if extra > 0:
                    print(f"  · +{extra} more")

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
    print(f"- planned Trellis tasks: {len(planned)}")
    completed_outside_archive = trellis.get("completedOutsideArchive", [])
    print(
        "- completed Trellis tasks outside archive "
        f"({len(completed_outside_archive)}): "
        f"{format_task(completed_outside_archive[0]) if completed_outside_archive else 'none'}"
    )

    print("\n==> Anomalies")
    if anomalies:
        for anomaly in anomalies:
            print(f"- {anomaly}")
    else:
        print("none")

    render_selectable_inventory(
        "Follow-ups",
        report.get("followUps"),
        task_items=False,
    )
    render_selectable_inventory(
        "Tasks",
        trellis.get("tasks"),
        task_items=True,
    )
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
        with suppress_bytecode_writes():
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


def read_consumer_pin(root: Path, pin_path: str) -> dict[str, Any]:
    """Classify a thin consumer's pin as present, absent, or unreadable.

    ``read_json_object`` collapses a missing file, an I/O error, and invalid
    JSON into one ``None``, and ``collect_versions`` additionally falls back to
    the installed manifest, so neither can express this three-way state. Load
    time already rejects absolute and ``..``-bearing pin paths, but a purely
    relative path can still leave the checkout through a symlink, so the read
    repeats the containment pattern used by ``filesystem_payload_digest``:
    ``resolve(strict=True)`` then ``relative_to`` the consumer root. An escape
    is reported, never followed.
    """

    source = safe_text(pin_path, limit=300)

    def result(
        state: str,
        *,
        version: str | None = None,
        detail: str | None = None,
    ) -> dict[str, Any]:
        return {
            "state": state,
            "version": version,
            "source": source,
            "detail": safe_text(detail, limit=200) if detail else None,
        }

    try:
        resolved = (root / pin_path).resolve(strict=True)
        resolved.relative_to(root.resolve())
    except FileNotFoundError:
        return result("absent", detail="pin file does not exist")
    except (OSError, RuntimeError, ValueError) as error:
        return result(
            "unreadable",
            detail=f"pin path is not readable inside the checkout: {error}",
        )
    payload = read_json_object(resolved)
    if payload is None:
        return result("unreadable", detail="pin file is not a readable JSON object")
    version = payload.get("version")
    if not isinstance(version, str) or not version.strip():
        return result("unreadable", detail="pin file carries no version string")
    return result("present", version=safe_text(version, limit=80))


def machine_install_version(machine_scope: Mapping[str, Any] | None) -> str | None:
    """The machine install's pack version, or ``None`` when unavailable."""
    if not isinstance(machine_scope, Mapping):
        return None
    if machine_scope.get("state") != "installed":
        return None
    version = machine_scope.get("packVersion")
    return version if isinstance(version, str) and version else None


PROVIDER_CONFIG_HISTORY_SOURCE = Path(
    "templates/docs/sd-ai-command-pack-provider-config-history.json"
)


def provider_config_states(pack_root: Path, consumer_root: Path) -> list[dict[str, Any]]:
    """Classify a consumer's `if-not-exists` configs against shipped digests.

    Read entirely from the pack checkout: the record is the pack's, and the
    consumer files are read directly. That is what lets this answer "who is
    behind on a provider config" *before* anything is installed anywhere --
    the consumer's own audit cannot, because the record only reaches it by
    install, and by then the install has already refreshed the file.

    Read-only, and every unreadable input degrades to `unknown` rather than a
    clean row.
    """
    try:
        payload = json.loads(
            (pack_root / PROVIDER_CONFIG_HISTORY_SOURCE).read_text(encoding="utf-8")
        )
        sources = payload["sources"]
        if payload.get("schemaVersion") != 1 or not isinstance(sources, dict):
            raise ValueError("unsupported provider config history")
    except (OSError, ValueError, KeyError, TypeError):
        # The record is what enumerates the targets, so an unreadable one
        # leaves nothing to classify. Returning `[]` would render as a row
        # with no provider configs -- indistinguishable from a clean one --
        # so name the artifact that could not be read instead.
        return [
            {"target": PROVIDER_CONFIG_HISTORY_SOURCE.as_posix(), "state": "unknown"}
        ]

    states: list[dict[str, Any]] = []
    for source, entry in sources.items():
        # A malformed entry is reported, never skipped: dropping it would
        # shrink the list toward the same clean-looking row an unreadable
        # record used to produce.
        label = source if isinstance(source, str) else repr(source)
        if not isinstance(entry, Mapping):
            states.append({"target": label, "state": "unknown"})
            continue
        target = entry.get("target")
        current = entry.get("current")
        digests = entry.get("digests")
        if not isinstance(target, str) or not isinstance(current, str):
            states.append({"target": label, "state": "unknown"})
            continue
        if not isinstance(digests, list):
            digests = []
        path = consumer_root / target
        try:
            if path.is_symlink():
                # A symlink is a deliberate local choice and the installer
                # preserves it, so it belongs with the locally owned files.
                # Calling it `absent` would say the opposite of what it is.
                state = "local"
            elif not path.is_file():
                state = "absent"
            else:
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                if digest == current:
                    state = "current"
                elif digest in digests:
                    state = "superseded"
                else:
                    state = "local"
        except OSError:
            state = "unknown"
        states.append({"target": target, "state": state})
    states.sort(key=lambda item: item["target"])
    return states


def fleet_step_records(
    reports: Sequence[Mapping[str, Any]],
    target: str,
    *,
    machine_scope: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Every fleet step, untruncated, ranked so skew outranks advisory rows.

    Fleet-level machine rows are gated on the registry containing at least one
    thin consumer: nothing consumes the machine install while every consumer is
    fat, so an all-fat fleet reports exactly as it did before schema 5.
    """
    missing = [item["name"] for item in reports if item.get("status") == "missing"]
    available = [item for item in reports if item.get("status") == "available"]
    thin = [item for item in available if item.get("installMode") == "thin"]
    fat = [item for item in available if item.get("installMode") != "thin"]
    dirty = [
        item["name"]
        for item in available
        if item["report"]["git"]["workingTree"]["state"] == "dirty"
    ]
    stale = [
        item["name"]
        for item in fat
        if item["report"]["versions"]["sdAiCommandPack"] != target
    ]
    divergent = [
        item["name"]
        for item in available
        if item["report"]["git"]["syncState"] in {"behind", "diverged"}
    ]
    has_thin = any(item.get("installMode") == "thin" for item in reports)
    machine_version = machine_install_version(machine_scope)

    records: list[dict[str, Any]] = []

    def add(summary: str, rank: int) -> None:
        records.append({"summary": summary, "rank": rank})

    broken_pins = [
        item["name"]
        for item in thin
        if (item.get("pin") or {}).get("state") != "present"
    ]
    if broken_pins:
        add(
            "Repair missing or unreadable thin consumer pins: "
            + ", ".join(broken_pins)
            + ".",
            FLEET_STEP_RANK_SKEW,
        )
    if machine_version is None:
        if thin:
            add(
                "Machine SD install inventory is unavailable; thin consumer pins "
                "cannot be compared.",
                FLEET_STEP_RANK_SKEW,
            )
    else:
        skewed_pins = [
            item["name"]
            for item in thin
            if (item.get("pin") or {}).get("state") == "present"
            and (item.get("pin") or {}).get("version") != machine_version
        ]
        if skewed_pins:
            add(
                f"Reconcile thin consumer pins against the machine install "
                f"({machine_version}): " + ", ".join(skewed_pins) + ".",
                FLEET_STEP_RANK_SKEW,
            )
    if has_thin:
        if machine_version is None:
            add(
                "Install or repair the machine SD install; thin consumers depend "
                "on it.",
                FLEET_STEP_RANK_SKEW,
            )
        elif machine_version != target:
            add(
                f"Update the machine SD install ({machine_version}) to the target "
                f"pack version ({target}).",
                FLEET_STEP_RANK_SKEW,
            )
        if isinstance(machine_scope, Mapping) and machine_scope.get("comparison") == "skew":
            add(
                "Reconcile the SD plugin "
                f"({machine_scope.get('pluginVersion') or 'unavailable'}) and the "
                f"machine receipt ({machine_scope.get('packVersion') or 'unavailable'}).",
                FLEET_STEP_RANK_SKEW,
            )

    if missing:
        add(
            "Restore or correct missing fleet checkouts: " + ", ".join(missing) + ".",
            FLEET_STEP_RANK_ADVISORY,
        )
    if dirty:
        add(
            "Resolve uncommitted fleet work before rollout: " + ", ".join(dirty) + ".",
            FLEET_STEP_RANK_ADVISORY,
        )
    if divergent:
        add(
            "Reconcile behind or diverged fleet checkouts: " + ", ".join(divergent) + ".",
            FLEET_STEP_RANK_ADVISORY,
        )
    if stale:
        add(
            "Refresh stale SD pack installations: " + ", ".join(stale) + ".",
            FLEET_STEP_RANK_ADVISORY,
        )
    superseded_configs = [
        item["name"]
        for item in reports
        if any(
            state.get("state") == "superseded"
            for state in item.get("providerConfigs") or ()
        )
    ]
    if superseded_configs:
        add(
            "Update superseded provider configs by running install.py against: "
            + ", ".join(superseded_configs)
            + ".",
            FLEET_STEP_RANK_ADVISORY,
        )
    local_configs = [
        item["name"]
        for item in reports
        if any(
            state.get("state") == "local"
            for state in item.get("providerConfigs") or ()
        )
    ]
    if local_configs:
        # Not skew: a locally owned config is a decision the installer will
        # keep honoring. It is listed so a shipped correction that will never
        # reach it is visible to a human who can merge it.
        add(
            "Merge shipped provider config changes by hand where the consumer "
            "owns the file: " + ", ".join(local_configs) + ".",
            FLEET_STEP_RANK_ADVISORY,
        )
    unknown_configs = [
        item["name"]
        for item in reports
        if any(
            state.get("state") == "unknown"
            for state in item.get("providerConfigs") or ()
        )
    ]
    if unknown_configs:
        # An unreadable record or file is this report's own gap, and saying so
        # is the point: a consumer whose currency could not be determined must
        # not read as one that was checked and found clean.
        add(
            "Provider config currency could not be determined for: "
            + ", ".join(unknown_configs)
            + ".",
            FLEET_STEP_RANK_ADVISORY,
        )
    if not records:
        add(FLEET_READY_STEP, FLEET_STEP_RANK_ADVISORY)
    records.sort(key=lambda record: record["rank"])
    return records


def fleet_next_steps(records: Sequence[Mapping[str, Any]]) -> list[str]:
    return [str(record["summary"]) for record in records][:HUMAN_ITEM_LIMIT]


def fleet_follow_ups(records: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Derive ``F-*`` rows from the complete record set.

    Deriving them from the truncated human list would let a skew row vanish
    once enough advisory rows exist, which PRD requirement 3 forbids.
    """
    actionable = [
        str(record["summary"])
        for record in records
        if record["summary"] != FLEET_READY_STEP
    ]
    return select_items(
        [
            {"kind": "action", "summary": step, "source": "fleet"}
            for step in actionable
        ],
        prefix="F",
    )


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

    def collect_consumer(consumer: Any) -> dict[str, Any]:
        path = resolution.path_overrides.get(
            consumer.name.casefold(),
            Path(consumer.path_hint).expanduser(),
        )
        install_mode = getattr(consumer, "mode", "fat")
        pin_path = getattr(consumer, "pin_path", DEFAULT_CONSUMER_PIN_PATH)
        if not path.is_dir():
            return {
                "name": consumer.name,
                "github": consumer.github,
                "priority": consumer.rollout_priority,
                "path": str(path),
                "status": "missing",
                "installMode": install_mode,
                "pin": None,
                "providerConfigs": [],
                "report": None,
            }
        try:
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
                include_machine_scope=False,
            )
        except Exception:
            # One unreachable or misbehaving consumer must not abort the whole
            # fleet run. Render it as a degraded row exactly as an empty
            # collect_local result does (status "unavailable", no report) so the
            # remaining consumers still report. KeyboardInterrupt is a
            # BaseException and is deliberately left to propagate.
            report = None
        return {
            "name": consumer.name,
            "github": consumer.github,
            "priority": consumer.rollout_priority,
            "path": str(path),
            "status": "available" if report else "unavailable",
            "installMode": install_mode,
            "pin": (
                read_consumer_pin(path, pin_path)
                if report and install_mode == "thin"
                else None
            ),
            "providerConfigs": provider_config_states(pack_root, path),
            "report": report,
        }

    # Fleet status is subprocess-bound (git/gh per consumer) and consumers are
    # independent, so collect them concurrently in a bounded pool instead of
    # stacking each consumer's subprocess and network-timeout latency serially.
    # The useful worker ceiling tracks git/gh concurrency rather than CPU cores;
    # cap at 8 so a large fleet does not open one subprocess tree per consumer
    # at once. ThreadPoolExecutor.map yields in input order, so registry
    # rollout order is preserved without re-sorting.
    reports: list[dict[str, Any]]
    if consumers:
        worker_count = min(8, len(consumers))
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            reports = list(executor.map(collect_consumer, consumers))
    else:
        reports = []
    # One machine probe per fleet run, never one per consumer: each consumer
    # row keeps include_machine_scope=False, so no extra `claude plugin list`
    # subprocess is spawned per member.
    machine_scope = collect_machine_scope(
        pack_root,
        home=home,
        environ=environ,
    )
    records = fleet_step_records(reports, target, machine_scope=machine_scope)
    steps = fleet_next_steps(records)
    return {
        "schemaVersion": SCHEMA_VERSION,
        "mode": "fleet",
        "targetPackVersion": target,
        "machineScope": machine_scope,
        "refsFreshness": "refreshed" if refs_refreshed else "cached",
        "configuration": {
            "source": resolution.source,
            "manifest": str(resolution.manifest_path),
            "profile": (
                str(resolution.profile_path) if resolution.profile_path else None
            ),
        },
        "repositories": reports,
        "followUps": fleet_follow_ups(records),
        "nextSteps": steps,
    }


def render_fleet(report: Mapping[str, Any]) -> None:
    repositories = report["repositories"]
    available = sum(item.get("status") == "available" for item in repositories)
    missing = sum(item.get("status") == "missing" for item in repositories)
    unavailable = len(repositories) - available - missing
    machine_version = machine_install_version(report.get("machineScope"))
    attention = 0
    for item in repositories:
        local = item.get("report")
        if not isinstance(local, dict):
            attention += 1
            continue
        if (
            local["git"]["workingTree"]["state"] != "clean"
            or local["git"]["syncState"] in {"behind", "diverged"}
        ):
            attention += 1
            continue
        # Version attention follows the mode split, so the human counter and the
        # JSON skew rows cannot disagree: a thin consumer has no meaningful
        # installed tree to compare against the target.
        if item.get("installMode") == "thin":
            pin = item.get("pin") or {}
            if pin.get("state") != "present" or pin.get("version") != machine_version:
                attention += 1
        elif local["versions"]["sdAiCommandPack"] != report["targetPackVersion"]:
            attention += 1
    print(
        f"SD fleet status: {len(repositories)} repositories, "
        f"{available} available, {attention} need attention, {missing} missing, "
        f"{unavailable} unavailable"
    )
    print(f"Target pack: {report['targetPackVersion']}")
    configuration = report.get("configuration", {})
    print(f"Fleet config: {configuration.get('source', 'unknown')}")
    if any(item.get("installMode") == "thin" for item in repositories):
        print(f"Machine scope: {format_machine_scope(report.get('machineScope'))}")
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
        if item.get("installMode") == "thin":
            pin = item.get("pin") or {}
            pin_state = pin.get("state") or "unreadable"
            pack_label = (
                f"pin {pin.get('version')}"
                if pin_state == "present"
                else f"pin {pin_state}"
            )
        else:
            pack_label = f"pack {versions.get('sdAiCommandPack') or 'none'}"
        print(
            f"- {prefix}: {git['workingTree']['state']}; "
            f"{git.get('branch') or 'detached'}; "
            f"{report['refsFreshness']}:{git['syncState']}; "
            f"{pack_label}; "
            f"stashes {stash_label}; "
            f"PRs {pr_count}; "
            f"tasks {len(trellis.get('inProgress', []))}/{len(trellis.get('planned', []))}"
        )
    render_selectable_inventory(
        "Follow-ups",
        report.get("followUps"),
        task_items=False,
    )
    print("\n==> Next Steps")
    for index, step in enumerate(report["nextSteps"], start=1):
        print(f"{index}. {step}")


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Report read-only SD repository or fleet status."
    )
    parser.add_argument(
        "target",
        nargs="?",
        help="Reserved word 'fleet' or a local repository path.",
    )
    parser.add_argument("--repo", type=Path)
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
    args = parser.parse_args(argv)
    if args.target == "fleet":
        if args.repo is not None:
            parser.error("fleet cannot be combined with --repo")
        args.mode = "fleet"
        args.repo = Path.cwd()
    elif args.target is not None:
        if args.repo is not None:
            parser.error("a positional repository path cannot be combined with --repo")
        args.mode = None
        args.repo = Path(args.target)
    else:
        args.mode = None
        args.repo = args.repo if args.repo is not None else Path.cwd()
    return args


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
