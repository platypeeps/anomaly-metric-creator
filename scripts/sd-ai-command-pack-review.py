#!/usr/bin/env python3
"""Run the exact-scope local and routed-remote ``sd-review`` state machine."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
import time
import uuid
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence
from urllib.parse import quote

from sd_ai_command_pack_lib import (
    CacheSetupError,
    CommandError,
    build_tool_environment,
    command_detail,
    git_stdout,
    run_command,
    run_gh,
)

SCHEMA_VERSION = 1
CONFIG_PATH = Path(".sd-ai-command-pack/review.json")
DEFAULT_DESCRIPTOR_PATH = Path("config/routed-review-setup-v1.json")
RECEIPT_MARKER = "<!-- sd-github-review-receipt:v1 -->\n"
# Stage helpers are siblings of this file, never repository-root paths, so the
# controller runs the same way from a vendored scripts/ directory, a plugin
# bin/, or a machine-wide install.
CHECK_SCRIPT = Path(__file__).resolve().with_name("sd-ai-command-pack-check.py")
LOCAL_SCRIPT = Path(__file__).resolve().with_name("sd-ai-command-pack-review-local.py")
MAX_CONFIG_BYTES = 256 * 1024
MAX_DESCRIPTOR_BYTES = 64 * 1024
MAX_STATE_BYTES = 2 * 1024 * 1024
MAX_RECEIPT_BYTES = 64 * 1024
MAX_JSON_OUTPUT_BYTES = 4 * 1024 * 1024
MAX_TEXT = 1200
MAX_POLLS = 30
MAX_POLL_SECONDS = 60
MAX_ROUNDS = 10
MAX_REMOTE_LATENCY_MS = 86_400_000
OID_RE = re.compile(r"[0-9a-f]{40}\Z")
SAFE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
REPOSITORY_RE = re.compile(r"[A-Za-z0-9_.-]{1,100}\Z")
SCOPES = frozenset({"auto", "changes", "branch", "codebase", "pr"})
LOCAL_VALUES = frozenset({"auto", "all", "none"})
REMOTE_VALUES = frozenset({"auto", "cheap", "deep", "copilot", "none"})
FIX_VALUES = frozenset({"auto", "ask", "none"})
REMOTE_DISPOSITION_VALUES = frozenset({"rebutted"})
LOCAL_DISPOSITION_VALUES = frozenset({"rebutted"})
CAPABILITY_STATES = frozenset(
    {"ready", "absent", "invalid", "incompatible", "unavailable", "skipped"}
)
# Local outcomes the coordinator must never cache in its resume state. Each one
# turns on an input the attempt key does not cover: ``invalid`` rejects the
# caller's ``--local-disposition`` list, and the three provider outcomes turn on
# whether a provider was reachable at all. ``blocked`` is absent on purpose —
# local policy is decided by the configuration digest, which the key does cover,
# so replaying it is correct.
LOCAL_NON_RESUMABLE_OUTCOMES = frozenset(
    {"invalid", "unavailable", "failed", "cancelled"}
)
RECEIPT_ROUTES = frozenset({"cheap", "deep", "copilot", "none"})
RECEIPT_CHECK_NAME = "sd-github-review/receipt"
FINDING_CHANNELS = frozenset(
    {"review", "inline-comment", "conversation-comment", "check"}
)
TOP_LEVEL_CONFIG_KEYS = frozenset(
    {"schemaVersion", "providers", "policy", "remoteIntegration"}
)
REMOTE_CONFIG_KEYS = frozenset(
    {
        "requirement",
        "descriptorPath",
        "receiptPolls",
        "pollSeconds",
        "roundLimit",
    }
)


class ReviewError(ValueError):
    """A controlled review input, evidence, or lifecycle failure."""


def _canonical_text(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_text(value).encode("utf-8")).hexdigest()


def _configuration_digest(value: object) -> str:
    """Match the local stage's canonical configuration digest exactly."""
    canonical = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _bounded(value: object, *, fallback: str = "unavailable") -> str:
    text = " ".join(str(value).replace("\x00", " ").split()) or fallback
    return text if len(text) <= MAX_TEXT else text[: MAX_TEXT - 3] + "..."


def _read_json(path: Path, *, limit: int, label: str) -> object:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise ReviewError(f"cannot inspect {label} {path}: {error}") from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ReviewError(f"{label} must be a regular non-symlink file: {path}")
    if metadata.st_size > limit:
        raise ReviewError(f"{label} exceeds {limit} bytes: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="strict"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ReviewError(f"cannot read {label} {path}: {error}") from error


def _safe_relative_path(value: object, *, field: str) -> Path:
    if not isinstance(value, str) or not value or len(value) > 240:
        raise ReviewError(f"{field} must be a bounded relative path")
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or ".." in path.parts or any(not part for part in path.parts):
        raise ReviewError(f"{field} must stay inside the repository")
    if re.match(r"[A-Za-z]:", normalized) or normalized.startswith("//"):
        raise ReviewError(f"{field} must stay inside the repository")
    return Path(*path.parts)


def _bounded_integer(
    value: object,
    *,
    field: str,
    minimum: int,
    maximum: int,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ReviewError(f"{field} must be an integer")
    if value < minimum or value > maximum:
        raise ReviewError(f"{field} must be between {minimum} and {maximum}")
    return value


def _is_exact_integer(value: object, expected: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value == expected


def _receipt_latency(receipt: Mapping[str, Any]) -> int | None:
    observations = receipt.get("observations")
    if observations is None:
        return None
    if not isinstance(observations, dict):
        raise ReviewError("durable receipt observations must be an object")
    return _bounded_integer(
        observations.get("latencyMs"),
        field="durable receipt observations latencyMs",
        minimum=0,
        maximum=MAX_REMOTE_LATENCY_MS,
    )


def _parse_local_dispositions(values: Sequence[str]) -> dict[str, str]:
    """Validate ``<stable-id>=rebutted`` pairs for the local review stage.

    Deliberately the same grammar and the same single accepted value as the
    remote channel below: a caller who has verified a finding is false should
    not have to learn two vocabularies depending on which provider raised it.
    """

    dispositions: dict[str, str] = {}
    for value in values:
        identifier, separator, disposition = value.rpartition("=")
        if (
            not separator
            or not identifier
            or len(identifier) > 240
            or any(ord(character) < 32 for character in identifier)
            or disposition not in LOCAL_DISPOSITION_VALUES
        ):
            raise ReviewError("local dispositions must use <stable-id>=rebutted")
        if identifier in dispositions:
            raise ReviewError("local disposition ids must be unique")
        dispositions[identifier] = disposition
    return dispositions


def _parse_remote_dispositions(values: Sequence[str]) -> dict[str, str]:
    dispositions: dict[str, str] = {}
    for value in values:
        identifier, separator, disposition = value.rpartition("=")
        if (
            not separator
            or not identifier
            or len(identifier) > 240
            or any(ord(character) < 32 for character in identifier)
            or disposition not in REMOTE_DISPOSITION_VALUES
        ):
            raise ReviewError(
                "remote dispositions must use <stable-id>=rebutted"
            )
        if identifier in dispositions:
            raise ReviewError("remote disposition ids must be unique")
        dispositions[identifier] = disposition
    return dispositions


def load_review_configuration(repo: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    path = repo / CONFIG_PATH
    value = (
        _read_json(path, limit=MAX_CONFIG_BYTES, label="review configuration")
        if path.exists()
        else {
            "schemaVersion": 1,
            "providers": [
                {
                    "id": "prism",
                    "adapter": "prism",
                    "argv": [],
                    "scopes": ["worktree", "branch_delta", "codebase"],
                    "dataHandling": "private-network",
                    "costTier": "low",
                    "qualityTier": "standard",
                    "timeoutSeconds": 300,
                    "version": "builtin-v1",
                    "enabled": True,
                    "outcomeByExitCode": {
                        "0": "clean",
                        "1": "findings",
                        "3": "unavailable",
                        "4": "unavailable",
                    },
                },
                {
                    "id": "gito",
                    "adapter": "gito",
                    "argv": [],
                    "scopes": ["worktree", "branch_delta", "codebase"],
                    "dataHandling": "private-network",
                    "costTier": "medium",
                    "qualityTier": "standard",
                    "timeoutSeconds": 600,
                    "version": "builtin-v1",
                    "enabled": True,
                    "outcomeByExitCode": {
                        "0": "clean",
                        "1": "findings",
                        "2": "unavailable",
                        "3": "unavailable",
                    },
                },
            ],
            "policy": {
                "allowedDataHandling": [
                    "local",
                    "private-network",
                    "public-network",
                ],
                "documentation": "cheapest",
                "metadata": "cheapest",
                "requiredProviders": [],
            },
            "remoteIntegration": {
                "requirement": "optional",
                "descriptorPath": str(DEFAULT_DESCRIPTOR_PATH),
                "receiptPolls": 6,
                "pollSeconds": 5,
                "roundLimit": 5,
            },
        }
    )
    if not isinstance(value, dict) or set(value) - TOP_LEVEL_CONFIG_KEYS:
        raise ReviewError("review configuration must use only supported fields")
    if not _is_exact_integer(value.get("schemaVersion"), 1):
        raise ReviewError("review configuration schemaVersion must be 1")
    policy = value.get("policy")
    if not isinstance(policy, dict):
        raise ReviewError("review configuration policy must be an object")
    required = policy.get("requiredProviders", [])
    if not isinstance(required, list) or any(
        not isinstance(item, str) or not item for item in required
    ):
        raise ReviewError("review policy requiredProviders must be a string array")

    raw_remote = value.get("remoteIntegration", {})
    if not isinstance(raw_remote, dict) or set(raw_remote) - REMOTE_CONFIG_KEYS:
        raise ReviewError("remoteIntegration must use only supported fields")
    requirement = raw_remote.get("requirement", "optional")
    if requirement not in {"optional", "required"}:
        raise ReviewError("remoteIntegration requirement must be optional or required")
    descriptor = _safe_relative_path(
        raw_remote.get("descriptorPath", str(DEFAULT_DESCRIPTOR_PATH)),
        field="remoteIntegration descriptorPath",
    )
    polls = _bounded_integer(
        raw_remote.get("receiptPolls", 6),
        field="remoteIntegration receiptPolls",
        minimum=1,
        maximum=MAX_POLLS,
    )
    poll_seconds = _bounded_integer(
        raw_remote.get("pollSeconds", 5),
        field="remoteIntegration pollSeconds",
        minimum=0,
        maximum=MAX_POLL_SECONDS,
    )
    round_limit = _bounded_integer(
        raw_remote.get("roundLimit", 5),
        field="remoteIntegration roundLimit",
        minimum=1,
        maximum=MAX_ROUNDS,
    )
    remote = {
        "requirement": requirement,
        "descriptorPath": descriptor.as_posix(),
        "receiptPolls": polls,
        "pollSeconds": poll_seconds,
        "roundLimit": round_limit,
    }
    normalized = {**value, "remoteIntegration": remote}
    return normalized, remote


def _git(repo: Path, *args: str, required: bool = True) -> str:
    value = git_stdout(
        list(args),
        cwd=repo,
        context=f"read Git review evidence ({' '.join(args)})",
        required=required,
    )
    return value or ""


def _is_dirty(repo: Path) -> bool:
    return bool(_git(repo, "status", "--porcelain=v1", "--untracked-files=all"))


def _json_process(
    command: list[str],
    *,
    repo: Path,
    context: str,
    timeout: int,
) -> tuple[int, dict[str, Any]]:
    result = run_command(
        command,
        cwd=repo,
        timeout=timeout,
        check=False,
        context=context,
    )
    output = result.stdout if isinstance(result.stdout, str) else ""
    if len(output.encode("utf-8")) > MAX_JSON_OUTPUT_BYTES:
        raise ReviewError(f"{context} output exceeds {MAX_JSON_OUTPUT_BYTES} bytes")
    try:
        value = json.loads(output)
    except json.JSONDecodeError as error:
        detail = command_detail(result, fallback=f"exit {result.returncode}")
        raise ReviewError(f"{context} did not return JSON: {_bounded(detail)}") from error
    if not isinstance(value, dict):
        raise ReviewError(f"{context} must return one JSON object")
    return result.returncode, value


def _gh_json(
    args: list[str],
    *,
    repo: Path,
    context: str,
    timeout: int = 120,
) -> object:
    result = run_gh(args, cwd=repo, timeout=timeout, context=context)
    if result.returncode != 0:
        raise CommandError(
            f"failed to {context}: "
            + command_detail(result, fallback=f"gh exited with status {result.returncode}")
        )
    output = result.stdout if isinstance(result.stdout, str) else ""
    if len(output.encode("utf-8")) > MAX_JSON_OUTPUT_BYTES:
        raise ReviewError(f"{context} output exceeds {MAX_JSON_OUTPUT_BYTES} bytes")
    try:
        return json.loads(output)
    except json.JSONDecodeError as error:
        raise ReviewError(f"{context} returned malformed JSON") from error


def _current_branch(repo: Path) -> str:
    branch = _git(repo, "symbolic-ref", "--quiet", "--short", "HEAD", required=False)
    if not branch:
        raise ReviewError("sd-review requires an attached Git branch")
    return branch


def _discover_branch_pr(repo: Path, branch: str) -> int | None:
    try:
        value = _gh_json(
            [
                "pr",
                "list",
                "--state",
                "open",
                "--head",
                branch,
                "--limit",
                "2",
                "--json",
                "number",
            ],
            repo=repo,
            context="discover the current branch pull request",
        )
    except (CommandError, ReviewError):
        return None
    if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], dict):
        return None
    number = value[0].get("number")
    return number if isinstance(number, int) and not isinstance(number, bool) else None


def resolve_scope(repo: Path, requested: str, pr_number: int | None) -> tuple[str, int | None]:
    if requested not in SCOPES:
        raise ReviewError(f"unsupported review scope: {requested}")
    if requested == "codebase" and pr_number is not None:
        raise ReviewError("an explicit PR number cannot be combined with codebase scope")
    if requested == "pr":
        number = pr_number or _discover_branch_pr(repo, _current_branch(repo))
        if number is None:
            raise ReviewError("PR scope requires an explicit or unambiguous open pull request")
        return "pr", number
    if requested != "auto":
        if pr_number is not None:
            raise ReviewError("an explicit PR number requires auto or pr scope")
        return requested, None
    if pr_number is not None:
        return "pr", pr_number
    if _is_dirty(repo):
        return "changes", None
    discovered = _discover_branch_pr(repo, _current_branch(repo))
    return ("pr", discovered) if discovered is not None else ("branch", None)


def _repository_identity(repo: Path) -> dict[str, str]:
    value = _gh_json(
        ["repo", "view", "--json", "nameWithOwner"],
        repo=repo,
        context="resolve GitHub repository identity",
    )
    if not isinstance(value, dict):
        raise ReviewError("GitHub repository identity must be an object")
    slug = value.get("nameWithOwner")
    if not isinstance(slug, str) or slug.count("/") != 1:
        raise ReviewError("GitHub repository identity is unavailable")
    owner, name = slug.split("/", 1)
    if not REPOSITORY_RE.fullmatch(owner) or not REPOSITORY_RE.fullmatch(name):
        raise ReviewError("GitHub repository identity is invalid")
    return {"owner": owner.lower(), "name": name.lower()}


def _pr_evidence(repo: Path, number: int) -> dict[str, Any]:
    value = _gh_json(
        [
            "pr",
            "view",
            str(number),
            "--json",
            "number,headRefOid,headRefName,baseRefName,state,isDraft,url",
        ],
        repo=repo,
        context="resolve exact pull request evidence",
    )
    if not isinstance(value, dict) or value.get("number") != number:
        raise ReviewError("pull request evidence does not match the requested PR")
    head = value.get("headRefOid")
    base = value.get("baseRefName")
    state = value.get("state")
    if not isinstance(head, str) or not OID_RE.fullmatch(head.lower()):
        raise ReviewError("pull request head is unavailable")
    if not isinstance(base, str) or not base:
        raise ReviewError("pull request base branch is unavailable")
    if state != "OPEN":
        raise ReviewError(f"pull request #{number} is not open")
    local_head = _git(repo, "rev-parse", "HEAD").lower()
    if local_head != head.lower():
        raise ReviewError("local HEAD must equal the pull request head before review")
    if _is_dirty(repo):
        raise ReviewError("PR scope requires a clean working tree")
    return {
        "number": number,
        "head": head.lower(),
        "headRefName": value.get("headRefName"),
        "baseRefName": base,
        "base": f"origin/{base}",
        "url": value.get("url"),
        "draft": bool(value.get("isDraft")),
        "repository": _repository_identity(repo),
    }


def _private_directory(path: Path, *, repo: Path, create: bool = True) -> Path:
    if not path.is_absolute():
        raise ReviewError("review artifact root must be an absolute path")
    resolved = path.resolve(strict=False)
    try:
        resolved.relative_to(repo)
    except ValueError:
        pass
    else:
        raise ReviewError("review artifact root must be outside the repository")
    try:
        if create:
            resolved.mkdir(mode=0o700, parents=True, exist_ok=True)
        metadata = resolved.lstat()
    except OSError as error:
        raise ReviewError(f"cannot prepare review artifact root: {error}") from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise ReviewError("review artifact root must be a real directory")
    if os.name != "nt":
        if hasattr(os, "getuid") and metadata.st_uid != os.getuid():
            raise ReviewError("review artifact root must be owned by the current user")
        if stat.S_IMODE(metadata.st_mode) & 0o077:
            raise ReviewError("review artifact root must use private permissions")
    return resolved


def _artifact_root(repo: Path, supplied: str | None) -> Path:
    if supplied:
        return _private_directory(Path(supplied), repo=repo)
    try:
        _, _, namespace = build_tool_environment(repo=repo)
    except CacheSetupError as error:
        raise ReviewError(str(error)) from error
    return _private_directory(namespace / "review-controller", repo=repo)


def _atomic_json(path: Path, value: object) -> None:
    data = (json.dumps(value, sort_keys=True, indent=2) + "\n").encode("utf-8")
    if len(data) > MAX_STATE_BYTES:
        raise ReviewError(f"review state exceeds {MAX_STATE_BYTES} bytes")
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except OSError as error:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise ReviewError(f"cannot write review state: {error}") from error


def _state_identity(
    *,
    repo: Path,
    scope: str,
    controls: Mapping[str, Any],
    pr: Mapping[str, Any] | None,
    base: str,
    worktree_digest: str | None,
) -> dict[str, Any]:
    head = _git(repo, "rev-parse", "HEAD").lower()
    return {
        "repository": str(repo),
        "scope": scope,
        "head": head,
        "base": base,
        "worktreeDigest": worktree_digest,
        "prNumber": pr.get("number") if pr else None,
        "controls": dict(controls),
    }


def _worktree_digest(repo: Path) -> str:
    diff = run_command(
        ["git", "diff", "--binary", "HEAD", "--"],
        cwd=repo,
        timeout=120,
        check=False,
        context="hash tracked review changes",
    )
    if diff.returncode != 0:
        raise ReviewError(
            "cannot hash tracked review changes: "
            + command_detail(diff, fallback=f"git exited {diff.returncode}")
        )
    tracked = diff.stdout if isinstance(diff.stdout, str) else ""
    tracked_digest = hashlib.sha256(tracked.encode("utf-8")).hexdigest()
    untracked_text = _git(
        repo,
        "ls-files",
        "--others",
        "--exclude-standard",
        "-z",
    )
    untracked: list[dict[str, Any]] = []
    for relative in sorted(item for item in untracked_text.split("\0") if item):
        safe = _safe_relative_path(relative, field="untracked review path")
        path = repo / safe
        try:
            metadata = path.lstat()
        except OSError as error:
            raise ReviewError(f"cannot hash untracked review path {relative}: {error}") from error
        if stat.S_ISLNK(metadata.st_mode):
            payload = os.fsencode(os.readlink(path))
            payload_digest = hashlib.sha256(payload).hexdigest()
            kind = "symlink"
        elif stat.S_ISREG(metadata.st_mode):
            digest = hashlib.sha256()
            try:
                with path.open("rb") as stream:
                    while chunk := stream.read(1024 * 1024):
                        digest.update(chunk)
            except OSError as error:
                raise ReviewError(
                    f"cannot hash untracked review path {relative}: {error}"
                ) from error
            payload_digest = digest.hexdigest()
            kind = "file"
        else:
            raise ReviewError(
                f"untracked review path must be a regular file or symlink: {relative}"
            )
        untracked.append(
            {
                "path": safe.as_posix(),
                "kind": kind,
                "digest": payload_digest,
            }
        )
    return _digest({"trackedDiffDigest": tracked_digest, "untracked": untracked})


def _attempt_id(identity: Mapping[str, Any], supplied: str | None) -> str:
    if supplied:
        if not SAFE_ID_RE.fullmatch(supplied):
            raise ReviewError("attempt id must be a bounded safe identifier")
        return supplied
    return f"review-{_digest(identity)[:24]}"


def _load_or_create_state(
    path: Path,
    *,
    attempt_id: str,
    identity: Mapping[str, Any],
) -> dict[str, Any]:
    if path.exists():
        value = _read_json(path, limit=MAX_STATE_BYTES, label="review state")
        if not isinstance(value, dict) or not _is_exact_integer(
            value.get("schemaVersion"), 1
        ):
            raise ReviewError("review state schema is invalid")
        if value.get("attemptId") != attempt_id or value.get("identity") != identity:
            raise ReviewError("review state identity conflicts with the current invocation")
        return value
    state = {
        "schemaVersion": 1,
        "command": "sd-review",
        "attemptId": attempt_id,
        "identity": dict(identity),
        "phase": "resolve",
        "check": None,
        "local": None,
        "capability": None,
        "remoteRequest": None,
        "remoteReceipt": None,
        "remoteDispositions": {},
        "observation": None,
        "updatedAt": int(time.time()),
    }
    _atomic_json(path, state)
    return state


def _advance(path: Path, state: dict[str, Any], phase: str, **updates: object) -> None:
    state.update(updates)
    state["phase"] = phase
    state["updatedAt"] = int(time.time())
    _atomic_json(path, state)


def _record_stage(
    path: Path,
    state: dict[str, Any],
    phase: str,
    *,
    resumable: bool,
    **updates: object,
) -> None:
    """Persist a stage result, or keep a non-resumable one in memory only.

    Resume caching exists so an interrupted attempt picks up after the work it
    already completed. It is keyed by `_state_identity`, which covers the
    repository, scope, base, head, worktree bytes, pull-request number and the
    typed controls — not every input a stage reads. A verdict that turns on an
    input outside that key is not completed work: the next invocation is
    entitled to recompute it, and persisting it instead pins the attempt to the
    verdict with no supported way out short of a fresh `--attempt-id`, which
    discards the local and remote evidence too.

    A non-resumable result still lands in ``state`` because `_report` reads both
    stage payloads straight out of it — the caller sees exactly what this run
    computed. What is withheld is the write to the private state file, and with
    it the phase: ``phase`` names the last stage that completed, which is where
    a resume re-enters. A verdict this run declined to store completed nothing,
    so the phase stays on the stage before it. Naming this stage there would
    assert a completion that did not happen and disagree with the state file a
    resume actually reads; the failure is already carried by the report's
    ``diagnostic`` and by the stage payload beside it. Any result an earlier
    invocation did persist survives untouched on disk.
    """

    if resumable:
        _advance(path, state, phase, **updates)
        return
    state.update(updates)


def _run_check(repo: Path) -> dict[str, Any]:
    script = CHECK_SCRIPT
    if not script.is_file() or script.is_symlink():
        raise ReviewError(f"missing regular sd-check helper: {CHECK_SCRIPT.name}")
    _, report = _json_process(
        [sys.executable, str(script), "--repo", str(repo), "--json"],
        repo=repo,
        context="run typed sd-check",
        timeout=3600,
    )
    if not _is_exact_integer(report.get("schemaVersion"), 1):
        raise ReviewError("sd-check returned an unsupported schema")
    return report


def _run_local(
    repo: Path,
    *,
    scope: str,
    base: str,
    head: str,
    attempt_id: str,
    args: argparse.Namespace,
    local_policy: str,
) -> dict[str, Any]:
    script = LOCAL_SCRIPT
    if not script.is_file() or script.is_symlink():
        raise ReviewError(f"missing regular local review helper: {LOCAL_SCRIPT.name}")
    # The local stage owns its artifact root: an in-repo, git-ignored
    # directory (default .build/sd-review). The coordinator's private root
    # must stay outside the repository, so it is never forwarded here.
    command = [
        sys.executable,
        str(script),
        "--repo",
        str(repo),
        "--scope",
        scope,
        "--base",
        base,
        "--head",
        head,
        "--local",
        args.local,
        "--successor",
        args.successor,
        "--attempt-id",
        attempt_id,
        "--local-policy",
        local_policy,
        "--fix",
        args.fix,
        "--json",
    ]
    for family in args.finding_family:
        command.extend(("--finding-family", family))
    for identifier, disposition in _parse_local_dispositions(
        args.local_disposition
    ).items():
        command.extend(("--local-disposition", f"{identifier}={disposition}"))
    if args.family_evidence:
        command.extend(("--family-evidence", args.family_evidence))
    if args.bookkeeping_evidence:
        command.extend(("--bookkeeping-evidence", args.bookkeeping_evidence))
    _, report = _json_process(
        command,
        repo=repo,
        context="run exact-scope local review",
        timeout=3600,
    )
    if not _is_exact_integer(report.get("schemaVersion"), 1):
        raise ReviewError("local review returned an unsupported schema")
    return report


def _local_outcome(local: object) -> object:
    """Read the local stage's verdict from its report.

    Prefer the canonical ``outcome`` key; fall back to the deprecated ``status``
    alias for the dual-emit window (A-077).
    """

    if not isinstance(local, Mapping):
        return None
    return local.get("outcome", local.get("status"))


def _local_outstanding(local: Mapping[str, Any]) -> int | None:
    """Count the local receipt findings the caller has left outstanding.

    Provider evidence is immutable, so a receipt whose findings are all
    rebutted keeps ``outcome == "findings"``; the caller-owned disposition
    block is the only place a rebuttal lands. An unreadable receipt returns
    ``None`` so callers gate as if findings were still outstanding.
    """

    receipt = local.get("receipt")
    if not isinstance(receipt, Mapping):
        return None
    findings = receipt.get("findings")
    # A stage that reports findings while listing none has produced evidence
    # nobody can inspect or rebut. Its own remote gate blocks that shape, so a
    # zero count over an empty list must not open routing here either.
    if not isinstance(findings, list) or not findings:
        return None
    disposition = receipt.get("disposition")
    if not isinstance(disposition, Mapping):
        return None
    outstanding = disposition.get("outstanding")
    if (
        not isinstance(outstanding, int)
        or isinstance(outstanding, bool)
        or outstanding < 0
    ):
        return None
    return outstanding


def _capability(
    repo: Path,
    *,
    remote: Mapping[str, Any],
    repository: Mapping[str, str],
    intent: str,
) -> dict[str, Any]:
    if intent == "none":
        return {"state": "skipped", "reason": "remote-none"}
    descriptor_path = repo / str(remote["descriptorPath"])
    if not descriptor_path.exists():
        return {"state": "absent", "reason": "setup-descriptor-absent"}
    try:
        descriptor = _read_json(
            descriptor_path,
            limit=MAX_DESCRIPTOR_BYTES,
            label="routed-review setup descriptor",
        )
    except ReviewError as error:
        return {"state": "invalid", "reason": _bounded(error)}
    if not isinstance(descriptor, dict):
        return {"state": "invalid", "reason": "setup-descriptor-not-object"}
    try:
        if not _is_exact_integer(
            descriptor.get("schemaVersion"), 1
        ) or not _is_exact_integer(descriptor.get("contractMajor"), 1):
            return {"state": "incompatible", "reason": "unsupported-contract-major"}
        if descriptor.get("integrationId") != "sd-github-review":
            return {"state": "invalid", "reason": "unexpected-integration-id"}
        intents = descriptor.get("supportedIntents")
        operations = descriptor.get("supportedOperations")
        if not isinstance(intents, list) or intent not in intents:
            return {"state": "incompatible", "reason": "intent-not-supported"}
        if not isinstance(operations, list) or "route" not in operations:
            return {"state": "incompatible", "reason": "route-not-supported"}
        if descriptor.get("noninteractive") is not True or descriptor.get("checkoutRequired") is not False:
            return {"state": "invalid", "reason": "unsafe-execution-contract"}
        durable = descriptor.get("durableReceipt")
        workflow = descriptor.get("workflow")
        if not isinstance(durable, dict) or durable.get("supported") is not True:
            return {"state": "incompatible", "reason": "durable-receipt-not-supported"}
        check_name = durable.get("checkName")
        if not isinstance(check_name, str) or not check_name or len(check_name) > 128:
            return {"state": "invalid", "reason": "invalid-receipt-check-name"}
        if check_name != RECEIPT_CHECK_NAME:
            return {"state": "incompatible", "reason": "unsupported-receipt-check-name"}
        if not isinstance(workflow, dict):
            return {"state": "invalid", "reason": "workflow-declaration-missing"}
        workflow_path = _safe_relative_path(workflow.get("path"), field="workflow path")
        if not workflow_path.as_posix().startswith(".github/workflows/"):
            return {"state": "invalid", "reason": "workflow-path-outside-actions"}
        workflow_name = workflow.get("name")
        if not isinstance(workflow_name, str) or not workflow_name:
            return {"state": "invalid", "reason": "workflow-name-missing"}
        action_reference = descriptor.get("actionReference")
        if not isinstance(action_reference, str) or not re.fullmatch(
            r"platypeeps/sd-github-review@[0-9a-f]{40}", action_reference
        ):
            return {"state": "invalid", "reason": "action-reference-not-immutable"}
    except ReviewError as error:
        return {"state": "invalid", "reason": _bounded(error)}

    workflow_endpoint = (
        f"repos/{repository['owner']}/{repository['name']}/actions/workflows/"
        f"{quote(workflow_path.as_posix(), safe='')}"
    )
    try:
        metadata = _gh_json(
            ["api", workflow_endpoint],
            repo=repo,
            context="read routed-review workflow metadata",
        )
    except (CommandError, ReviewError) as error:
        return {"state": "unavailable", "reason": _bounded(error)}
    if not isinstance(metadata, dict):
        return {"state": "unavailable", "reason": "workflow-metadata-not-object"}
    if metadata.get("state") != "active":
        return {"state": "invalid", "reason": "workflow-not-active"}
    if metadata.get("path") != workflow_path.as_posix():
        return {"state": "invalid", "reason": "workflow-path-mismatch"}
    if metadata.get("name") != workflow_name:
        return {"state": "invalid", "reason": "workflow-name-mismatch"}
    return {
        "state": "ready",
        "reason": "compatible-enabled-workflow",
        "workflow": {"path": workflow_path.as_posix(), "name": workflow_name},
        "checkName": check_name,
        "actionReference": action_reference,
        "descriptorDigest": _digest(descriptor),
    }


def _router_local_summary(
    report: Mapping[str, Any],
    *,
    repository: Mapping[str, str],
    pr_number: int,
    head: str,
) -> dict[str, Any] | None:
    receipt = report.get("receipt")
    if not isinstance(receipt, dict):
        return None
    outcome = receipt.get("outcome")
    if outcome not in {"clean", "unavailable", "failed", "cancelled", "skipped"}:
        return None
    target = receipt.get("target")
    plan = receipt.get("plan")
    attempts = receipt.get("attempts")
    findings = receipt.get("findings")
    if not isinstance(target, dict) or not isinstance(plan, dict):
        raise ReviewError("local receipt target or plan is invalid")
    if target.get("head") != head:
        raise ReviewError("local receipt head does not match the pull request head")
    if not isinstance(attempts, list) or not isinstance(findings, list):
        raise ReviewError("local receipt attempts or findings are invalid")
    providers: list[dict[str, Any]] = []
    durations: list[int] = []
    cost_order = {"free": 0, "low": 1, "medium": 2, "high": 3, "unknown": 4}
    cost_mapping = {"none": "free", "low": "low", "medium": "medium", "high": "high"}
    quality_mapping = {"basic": "basic", "standard": "standard", "deep": "advanced"}
    costs: list[str] = []
    for row in attempts:
        if not isinstance(row, dict) or not isinstance(row.get("provider"), dict):
            raise ReviewError("local receipt provider attempt is invalid")
        provider = row["provider"]
        identifier = provider.get("id")
        raw_cost = provider.get("costTier")
        raw_quality = provider.get("qualityTier")
        cost = cost_mapping.get(str(raw_cost), "unknown")
        quality = quality_mapping.get(str(raw_quality), "unknown")
        if not isinstance(identifier, str) or not identifier:
            raise ReviewError("local receipt provider id is invalid")
        if cost not in cost_order or quality == "unknown":
            raise ReviewError("local receipt provider tiers are invalid")
        providers.append(
            {
                "id": identifier,
                "capabilityTier": quality,
                "costTier": cost,
                "qualityTier": quality,
            }
        )
        duration = row.get("durationMs", 0)
        if isinstance(duration, int) and not isinstance(duration, bool) and duration >= 0:
            durations.append(duration)
        costs.append(cost)
    if not providers and outcome != "skipped":
        return None
    if outcome == "skipped":
        providers = [
            {
                "id": "sd-review-local-policy",
                "capabilityTier": "standard",
                "costTier": "free",
                "qualityTier": "unknown",
            }
        ]
        costs = ["free"]
    unresolved = 0
    fixed = 0
    rebutted = 0
    for item in findings:
        if not isinstance(item, dict):
            raise ReviewError("local receipt finding is invalid")
        disposition = item.get("disposition")
        if disposition in {"outstanding", "fix"}:
            unresolved += 1
        elif disposition == "fixed":
            fixed += 1
        elif disposition in {"rebutted", "resolved"}:
            # Router v1 has one terminal non-fix bucket; local `resolved`
            # carries no fix-commit evidence, so it belongs with rebuttals.
            rebutted += 1
        else:
            raise ReviewError("local receipt finding disposition is invalid")
    total = unresolved + fixed + rebutted
    mapped_outcome = outcome
    confidence = 90 if outcome == "clean" else 0
    summary = {
        "schemaVersion": 1,
        "receiptId": receipt.get("receiptId"),
        "repository": dict(repository),
        "pullRequestNumber": pr_number,
        "headSha": head,
        "scopeDigest": target.get("contentDigest"),
        "configurationDigest": plan.get("configurationDigest"),
        "providers": sorted(providers, key=lambda item: str(item["id"])),
        "outcome": mapped_outcome,
        "dispositionCounts": {
            "total": total,
            "unresolved": unresolved,
            "fixed": fixed,
            "rebutted": rebutted,
        },
        "confidence": confidence,
        "latencyMs": max(durations, default=0),
        "costTier": max(costs, key=lambda item: cost_order[item], default="unknown"),
    }
    if outcome == "skipped":
        policy_id = plan.get("policyId")
        summary["skipReason"] = (
            "bookkeeping-successor"
            if policy_id == "bookkeeping-successor"
            else "explicit-none"
            if policy_id == "explicit-none"
            else "not-requested"
        )
    if not isinstance(summary["receiptId"], str) or not summary["receiptId"]:
        raise ReviewError("local receipt id is invalid")
    for field in ("scopeDigest", "configurationDigest"):
        if not isinstance(summary[field], str) or not re.fullmatch(
            r"[0-9a-f]{64}", str(summary[field])
        ):
            raise ReviewError(f"local receipt {field} is invalid")
    return summary


def _remote_request(
    *,
    repository: Mapping[str, str],
    pr: Mapping[str, Any],
    route: str,
    attempt: int,
    local_summary: Mapping[str, Any] | None,
    policy_reference: str,
) -> dict[str, Any]:
    correlation = f"sd-review-{uuid.uuid4().hex}"
    normalized: dict[str, Any] = {
        "schemaVersion": 1,
        "correlationId": correlation,
        "correlationAliases": [],
        "attempt": attempt,
        "repository": dict(repository),
        "pullRequestNumber": pr["number"],
        "headSha": pr["head"],
        "route": route,
        "policyVersion": "sd-review-v1",
        "policyReference": policy_reference,
        "caller": {"id": "sd-review", "type": "automation"},
    }
    if local_summary is not None:
        normalized["localReview"] = dict(local_summary)
    logical = _digest(
        {
            "schemaVersion": 1,
            "repository": dict(repository),
            "pullRequestNumber": pr["number"],
            "headSha": pr["head"],
            "attempt": attempt,
        }
    )
    fingerprint_fields = {
        key: value
        for key, value in normalized.items()
        if key not in {"correlationId", "correlationAliases"}
    }
    normalized["logicalDispatchId"] = logical
    normalized["requestFingerprint"] = _digest(fingerprint_fields)
    if len(_canonical_text(normalized).encode("utf-8")) > 16 * 1024:
        raise ReviewError("routed-review request exceeds 16384 bytes")
    return normalized


def _default_branch(repo: Path) -> str:
    symbolic = _git(
        repo,
        "symbolic-ref",
        "--quiet",
        "refs/remotes/origin/HEAD",
        required=False,
    )
    if symbolic.startswith("refs/remotes/origin/"):
        return symbolic.removeprefix("refs/remotes/origin/")
    candidates = [
        branch
        for branch in ("main", "master")
        if _git(
            repo,
            "rev-parse",
            "--verify",
            f"refs/remotes/origin/{branch}",
            required=False,
        )
    ]
    if len(candidates) == 1:
        return candidates[0]
    raise ReviewError("cannot determine the origin default branch")


def _dispatch(
    repo: Path,
    *,
    workflow: str,
    request: Mapping[str, Any],
) -> None:
    default_ref = _default_branch(repo)
    result = run_gh(
        [
            "workflow",
            "run",
            workflow,
            "--ref",
            default_ref,
            "-f",
            "operation=route",
            "-f",
            f"review-request={_canonical_text(request)}",
            "-f",
            "rerequest-authorized=false",
        ],
        cwd=repo,
        context="dispatch routed review workflow",
    )
    if result.returncode != 0:
        raise CommandError(
            "routed-review dispatch outcome is uncertain: "
            + command_detail(result, fallback=f"gh exited with status {result.returncode}")
        )


def _decode_receipt_check(
    check: Mapping[str, Any],
    *,
    check_name: str,
    request: Mapping[str, Any],
) -> dict[str, Any]:
    if check.get("name") != check_name or check.get("head_sha") != request["headSha"]:
        raise ReviewError("durable receipt Check Run identity is invalid")
    output = check.get("output")
    text = output.get("text") if isinstance(output, dict) else None
    if not isinstance(text, str) or not text.startswith(RECEIPT_MARKER):
        raise ReviewError("durable receipt Check Run is missing its v1 marker")
    if len(text.encode("utf-8")) > MAX_RECEIPT_BYTES:
        raise ReviewError("durable receipt Check Run is oversized")
    try:
        receipt = json.loads(text[len(RECEIPT_MARKER) :])
    except json.JSONDecodeError as error:
        raise ReviewError("durable receipt Check Run contains malformed JSON") from error
    if not isinstance(receipt, dict):
        raise ReviewError("durable receipt must be an object")
    if text != RECEIPT_MARKER + _canonical_text(receipt):
        raise ReviewError("durable receipt JSON is not canonical")
    required_integers = {
        "schemaVersion": 1,
        "pullRequestNumber": request["pullRequestNumber"],
        "attempt": request["attempt"],
    }
    for field, expected in required_integers.items():
        if not _is_exact_integer(receipt.get(field), expected):
            raise ReviewError(f"durable receipt {field} does not match the request")
    required_equal = {
        "logicalDispatchId": request["logicalDispatchId"],
        "requestFingerprint": request["requestFingerprint"],
        "repository": request["repository"],
        "headSha": request["headSha"],
        "policyVersion": request["policyVersion"],
    }
    for field, expected in required_equal.items():
        if receipt.get(field) != expected:
            raise ReviewError(f"durable receipt {field} does not match the request")
    if check.get("external_id") != request["logicalDispatchId"]:
        raise ReviewError("durable receipt Check Run external_id is invalid")
    correlations = receipt.get("correlationIds")
    if not isinstance(correlations, list) or request["correlationId"] not in correlations:
        raise ReviewError("durable receipt does not contain the current correlation id")
    route = receipt.get("selectedRoute")
    backend = receipt.get("backend")
    dispatch = receipt.get("dispatch")
    if route not in RECEIPT_ROUTES or not isinstance(dispatch, dict):
        raise ReviewError("durable receipt route or dispatch is invalid")
    _receipt_latency(receipt)
    if dispatch.get("idempotencyKey") != request["logicalDispatchId"]:
        raise ReviewError("durable receipt idempotency key is invalid")
    if dispatch.get("status") not in {"requested", "already-present", "failed", "skipped"}:
        raise ReviewError("durable receipt dispatch status is invalid")
    if dispatch.get("phase") not in {"not-started", "started", "acknowledged", "observed"}:
        raise ReviewError("durable receipt dispatch phase is invalid")
    if route == "none":
        if backend is not None or dispatch.get("status") != "skipped":
            raise ReviewError("none receipt must contain a skipped null-backend dispatch")
    else:
        if not isinstance(backend, dict):
            raise ReviewError("routed receipt backend is missing")
        channels = backend.get("findingChannels")
        if not isinstance(channels, list) or not channels or any(
            item not in FINDING_CHANNELS for item in channels
        ):
            raise ReviewError("routed receipt finding channels are invalid")
        authors = backend.get("reviewAuthors", [])
        checks = backend.get("checkNames", [])
        if not isinstance(authors, list) or not isinstance(checks, list):
            raise ReviewError("routed receipt backend identities are invalid")
        if any(
            not isinstance(item, str) or not item or len(item) > 128
            for item in [*authors, *checks]
        ):
            raise ReviewError("routed receipt backend identities are invalid")
        if len(set(authors)) != len(authors) or len(set(checks)) != len(checks):
            raise ReviewError("routed receipt backend identities must be unique")
        if any(item != "check" for item in channels) and not authors:
            raise ReviewError("comment/review channels require declared authors")
        if "check" in channels and not checks:
            raise ReviewError("check channels require declared check names")
    return receipt


def _query_receipt(
    repo: Path,
    *,
    repository: Mapping[str, str],
    check_name: str,
    request: Mapping[str, Any],
) -> dict[str, Any] | None:
    endpoint = (
        f"repos/{repository['owner']}/{repository['name']}/commits/"
        f"{request['headSha']}/check-runs"
    )
    value = _gh_json(
        [
            "api",
            "--method",
            "GET",
            endpoint,
            "-F",
            f"check_name={check_name}",
            "-F",
            "per_page=100",
        ],
        repo=repo,
        context="query durable routed-review receipt",
    )
    if not isinstance(value, dict) or not isinstance(value.get("check_runs"), list):
        raise ReviewError("durable receipt query returned an invalid payload")
    matches: list[dict[str, Any]] = []
    for raw in value["check_runs"]:
        if not isinstance(raw, dict) or raw.get("external_id") != request["logicalDispatchId"]:
            continue
        matches.append(
            _decode_receipt_check(raw, check_name=check_name, request=request)
        )
    if len(matches) > 1:
        raise ReviewError("multiple durable receipts match one logical dispatch")
    return matches[0] if matches else None


def _paginated_rest_array(
    repo: Path,
    *,
    endpoint: str,
    context: str,
) -> list[dict[str, Any]]:
    value = _gh_json(
        ["api", "--paginate", "--slurp", endpoint],
        repo=repo,
        context=context,
    )
    pages = value if isinstance(value, list) else [value]
    rows: list[dict[str, Any]] = []
    for page in pages:
        if not isinstance(page, list):
            raise ReviewError(f"{context} page must be an array")
        for row in page:
            if not isinstance(row, dict):
                raise ReviewError(f"{context} row must be an object")
            rows.append(row)
            if len(rows) > 1_000:
                raise ReviewError(f"{context} exceeds 1000 rows")
    return rows


def _matching_author(row: Mapping[str, Any], authors: set[str]) -> str | None:
    user = row.get("user")
    login = user.get("login") if isinstance(user, dict) else None
    if isinstance(login, str) and login.lower() in authors:
        return login
    author = row.get("author")
    login = author.get("login") if isinstance(author, dict) else None
    if isinstance(login, str) and login.lower() in authors:
        return login
    return None


def _stable_remote_id(row: Mapping[str, Any]) -> str:
    value = row.get("node_id") or row.get("id")
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ReviewError("remote finding is missing a stable identity")
    normalized = str(value)
    if not normalized or len(normalized) > 240 or any(
        ord(character) < 32 for character in normalized
    ):
        raise ReviewError("remote finding identity is invalid")
    return normalized


def _nested_thread_comments(
    repo: Path,
    *,
    thread_id: str,
    authors: set[str],
) -> list[dict[str, Any]]:
    query = """query($id:ID!,$endCursor:String){node(id:$id){... on PullRequestReviewThread{comments(first:100,after:$endCursor){nodes{id url body path line author{login}}pageInfo{hasNextPage endCursor}}}}}"""
    value = _gh_json(
        [
            "api",
            "graphql",
            "--paginate",
            "--slurp",
            "-f",
            f"query={query}",
            "-F",
            f"id={thread_id}",
        ],
        repo=repo,
        context="collect paginated review-thread comments",
    )
    pages = value if isinstance(value, list) else [value]
    comments: list[dict[str, Any]] = []
    for page in pages:
        data = page.get("data") if isinstance(page, dict) else None
        node = data.get("node") if isinstance(data, dict) else None
        comment_connection = (
            node.get("comments") if isinstance(node, dict) else None
        )
        rows = (
            comment_connection.get("nodes")
            if isinstance(comment_connection, dict)
            else None
        )
        if not isinstance(rows, list):
            raise ReviewError("nested review-thread query returned an invalid payload") from None
        for comment in rows:
            if not isinstance(comment, dict):
                continue
            login = _matching_author(comment, authors)
            if login is None:
                continue
            comments.append(
                {
                    "id": comment.get("id"),
                    "url": comment.get("url"),
                    "path": comment.get("path"),
                    "line": comment.get("line"),
                    "summary": _bounded(comment.get("body", "review finding")),
                    "author": login,
                }
            )
            if len(comments) > 1_000:
                raise ReviewError("nested review-thread comments exceed 1000 rows")
    return comments


def _collect_review_threads(
    repo: Path,
    *,
    owner: str,
    name: str,
    number: int,
    authors: set[str],
) -> list[dict[str, Any]]:
    query = """query($owner:String!,$name:String!,$number:Int!,$endCursor:String){repository(owner:$owner,name:$name){pullRequest(number:$number){reviewThreads(first:100,after:$endCursor){nodes{id isResolved isOutdated comments(first:100){nodes{id url body path line author{login}} pageInfo{hasNextPage}}}pageInfo{hasNextPage endCursor}}}}}"""
    thread_value = _gh_json(
        [
            "api",
            "graphql",
            "--paginate",
            "--slurp",
            "-f",
            f"query={query}",
            "-F",
            f"owner={owner}",
            "-F",
            f"name={name}",
            "-F",
            f"number={number}",
        ],
        repo=repo,
        context="collect paginated review threads",
    )
    pages = thread_value if isinstance(thread_value, list) else [thread_value]
    threads: list[dict[str, Any]] = []
    for page in pages:
        data = page.get("data") if isinstance(page, dict) else None
        repository_value = (
            data.get("repository") if isinstance(data, dict) else None
        )
        pull_request = (
            repository_value.get("pullRequest")
            if isinstance(repository_value, dict)
            else None
        )
        thread_connection = (
            pull_request.get("reviewThreads")
            if isinstance(pull_request, dict)
            else None
        )
        rows = (
            thread_connection.get("nodes")
            if isinstance(thread_connection, dict)
            else None
        )
        if not isinstance(rows, list):
            raise ReviewError("review thread query returned an invalid payload") from None
        for row in rows:
            if not isinstance(row, dict):
                raise ReviewError("review thread row must be an object")
            comments = row.get("comments")
            comment_rows = comments.get("nodes", []) if isinstance(comments, dict) else []
            matching = []
            for comment in comment_rows if isinstance(comment_rows, list) else []:
                if not isinstance(comment, dict):
                    continue
                login = _matching_author(comment, authors)
                if login is not None:
                    matching.append(
                        {
                            "id": comment.get("id"),
                            "url": comment.get("url"),
                            "path": comment.get("path"),
                            "line": comment.get("line"),
                            "summary": _bounded(comment.get("body", "review finding")),
                            "author": login,
                        }
                    )
            page_info = comments.get("pageInfo") if isinstance(comments, dict) else None
            if isinstance(page_info, dict) and page_info.get("hasNextPage"):
                thread_id = row.get("id")
                if not isinstance(thread_id, str) or not thread_id:
                    raise ReviewError("paginated review thread is missing its identity")
                matching = _nested_thread_comments(
                    repo,
                    thread_id=thread_id,
                    authors=authors,
                )
            if matching or not authors:
                threads.append(
                    {
                        "id": row.get("id"),
                        "resolved": bool(row.get("isResolved")),
                        "outdated": bool(row.get("isOutdated")),
                        "comments": matching,
                    }
                )
                if len(threads) > 1_000:
                    raise ReviewError("review threads exceed 1000 rows")
    return threads


def _collect_observation(
    repo: Path,
    *,
    pr: Mapping[str, Any],
    receipt: Mapping[str, Any],
    receipt_check_name: str,
    dispositions: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    disposition_map = dict(dispositions or {})
    backend = receipt.get("backend")
    authors = {
        str(item).lower()
        for item in (backend.get("reviewAuthors", []) if isinstance(backend, dict) else [])
    }
    check_names = {
        str(item)
        for item in (backend.get("checkNames", []) if isinstance(backend, dict) else [])
    }
    channels = set(
        backend.get("findingChannels", []) if isinstance(backend, dict) else []
    )
    dispatch = receipt.get("dispatch")
    if not isinstance(dispatch, dict):
        raise ReviewError("remote receipt dispatch is invalid")
    remote_latency = _receipt_latency(receipt)
    repository = pr["repository"]
    owner = repository["owner"]
    name = repository["name"]

    threads = (
        _collect_review_threads(
            repo,
            owner=owner,
            name=name,
            number=pr["number"],
            authors=authors,
        )
        if "inline-comment" in channels
        else []
    )

    issue_comments = (
        _paginated_rest_array(
            repo,
            endpoint=(
                f"repos/{owner}/{name}/issues/{pr['number']}/comments?per_page=100"
            ),
            context="collect pull request conversation comments",
        )
        if "conversation-comment" in channels
        else []
    )
    reviews = (
        _paginated_rest_array(
            repo,
            endpoint=f"repos/{owner}/{name}/pulls/{pr['number']}/reviews?per_page=100",
            context="collect pull request reviews",
        )
        if "review" in channels
        else []
    )
    matching_conversation = [
        {
            "id": _stable_remote_id(row),
            "url": row.get("html_url"),
            "summary": _bounded(row.get("body", "review finding")),
            "author": _matching_author(row, authors),
        }
        for row in issue_comments
        if _matching_author(row, authors) is not None
        and (
            not isinstance(dispatch.get("startedAt"), str)
            or not isinstance(row.get("created_at"), str)
            or str(row["created_at"]) >= str(dispatch["startedAt"])
        )
    ]
    matching_reviews = [
        {
            "id": _stable_remote_id(row),
            "url": row.get("html_url"),
            "state": row.get("state"),
            "summary": _bounded(row.get("body", "review")),
            "author": _matching_author(row, authors),
        }
        for row in reviews
        if _matching_author(row, authors) is not None
        and row.get("commit_id") == pr["head"]
        and row.get("state") != "DISMISSED"
    ]

    checks_value = _gh_json(
        [
            "pr",
            "checks",
            str(pr["number"]),
            "--json",
            "name,workflow,state,bucket,link,completedAt",
        ],
        repo=repo,
        context="collect pull request checks",
    )
    if not isinstance(checks_value, list):
        raise ReviewError("pull request checks must be an array")
    checks = [item for item in checks_value if isinstance(item, dict)]
    blocking_checks = [
        item
        for item in checks
        if item.get("name") != receipt_check_name
        and str(item.get("bucket", "")).lower()
        in {"fail", "pending", "cancel"}
    ]
    matching_checks = [item for item in checks if item.get("name") in check_names]
    unresolved = (
        [
            row
            for row in threads
            if not row["resolved"] and not row["outdated"] and row["comments"]
        ]
        if "inline-comment" in channels
        else []
    )
    review_findings = [
        row
        for row in matching_reviews
        if row.get("state") == "CHANGES_REQUESTED"
        and str(row.get("id")) not in disposition_map
    ]
    conversation_findings = [
        row
        for row in matching_conversation
        if str(row.get("summary", "")).strip()
        and str(row.get("id")) not in disposition_map
    ]
    materialized = (
        receipt.get("selectedRoute") == "none"
        or ("check" in channels and bool(matching_checks))
        or (
            "inline-comment" in channels
            and any(not row["outdated"] and row["comments"] for row in threads)
        )
        or ("conversation-comment" in channels and bool(matching_conversation))
        or ("review" in channels and bool(matching_reviews))
    )
    status = (
        "findings"
        if unresolved or review_findings or conversation_findings
        else "blocked"
        if any(str(item.get("bucket", "")).lower() in {"fail", "cancel"} for item in blocking_checks)
        else "pending"
        if not materialized
        or any(str(item.get("bucket", "")).lower() == "pending" for item in blocking_checks)
        else "clean"
    )
    return {
        "status": status,
        "materialized": materialized,
        "latencyMs": remote_latency,
        "reviewThreads": {
            "total": len(threads),
            "unresolved": len(unresolved),
            "items": unresolved[:100],
        },
        "conversationComments": matching_conversation[:100],
        "reviews": matching_reviews[:100],
        "dispositions": disposition_map,
        "checks": {
            "total": len(checks),
            "blocking": blocking_checks[:100],
            "backend": matching_checks[:100],
        },
    }


def _report(
    *,
    state: Mapping[str, Any],
    status: str,
    diagnostic: str | None = None,
    limitations: Sequence[str] = (),
) -> dict[str, Any]:
    identity = state.get("identity")
    identity_value = identity if isinstance(identity, dict) else {}
    local = state.get("local")
    local_receipt = local.get("receipt") if isinstance(local, dict) else None
    target = (
        local_receipt.get("target") if isinstance(local_receipt, dict) else None
    )
    target_value = target if isinstance(target, dict) else {}
    attempt_value = (
        local_receipt.get("attempts") if isinstance(local_receipt, dict) else []
    )
    attempts = attempt_value if isinstance(attempt_value, list) else []
    local_costs = [
        {
            "provider": row.get("provider", {}).get("id"),
            "status": row.get("status"),
            "costTier": row.get("provider", {}).get("costTier"),
            "latencyMs": row.get("durationMs"),
        }
        for row in attempts
        if isinstance(row, dict) and isinstance(row.get("provider"), dict)
    ]
    remote_receipt = state.get("remoteReceipt")
    backend = (
        remote_receipt.get("backend")
        if isinstance(remote_receipt, dict)
        else None
    )
    observation = state.get("observation")
    return {
        "schemaVersion": 1,
        "command": "sd-review",
        "status": status,
        "phase": state.get("phase"),
        "attemptId": state.get("attemptId"),
        "scope": identity_value.get("scope"),
        "head": identity_value.get("head"),
        "controls": identity_value.get("controls", {}),
        "target": {
            "scope": target_value.get("scope", identity_value.get("scope")),
            "base": target_value.get("base", identity_value.get("base")),
            "head": target_value.get("head", identity_value.get("head")),
            "contentDigest": target_value.get("contentDigest"),
            "worktreeDigest": identity_value.get("worktreeDigest"),
            "pullRequestNumber": identity_value.get("prNumber"),
        },
        "economics": {
            "local": local_costs,
            "remote": {
                "route": remote_receipt.get("selectedRoute")
                if isinstance(remote_receipt, dict)
                else None,
                "backend": backend.get("id") if isinstance(backend, dict) else None,
                "costTier": backend.get("costTier")
                if isinstance(backend, dict)
                else None,
                "latencyMs": observation.get("latencyMs")
                if isinstance(observation, dict)
                else None,
            },
        },
        "check": state.get("check"),
        "local": state.get("local"),
        "routerCapability": state.get("capability"),
        "remote": {
            "request": state.get("remoteRequest"),
            "receipt": state.get("remoteReceipt"),
            "observation": state.get("observation"),
            "dispositions": state.get("remoteDispositions"),
        },
        "diagnostic": diagnostic,
        "limitations": list(limitations),
        "exactHeadReady": status == "ready" and state.get("phase") == "ready",
    }


def _print_human(report: Mapping[str, Any]) -> None:
    print(f"SD review: {report['status']}")
    print(f"Scope: {report.get('scope')}")
    print(f"Phase: {report.get('phase')}")
    capability = report.get("routerCapability")
    if isinstance(capability, dict):
        print(f"Router: {capability.get('state')} ({capability.get('reason')})")
    if report.get("diagnostic"):
        print(f"Diagnostic: {report['diagnostic']}")
    limitations = report.get("limitations")
    print("Limitations: " + (", ".join(limitations) if limitations else "none"))


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=".")
    parser.add_argument("--scope", choices=sorted(SCOPES), default="auto")
    parser.add_argument("--base")
    parser.add_argument("--pr-number", type=int)
    parser.add_argument("--local", default="auto")
    parser.add_argument("--remote", choices=sorted(REMOTE_VALUES), default="auto")
    parser.add_argument("--fix", choices=sorted(FIX_VALUES), default="auto")
    parser.add_argument(
        "--successor",
        choices=("first", "low-risk", "high-risk", "repeated-family", "bookkeeping"),
        default="first",
    )
    parser.add_argument("--finding-family", action="append", default=[])
    parser.add_argument("--family-evidence")
    parser.add_argument("--bookkeeping-evidence")
    parser.add_argument("--local-disposition", action="append", default=[])
    parser.add_argument("--remote-disposition", action="append", default=[])
    parser.add_argument("--attempt", type=int, default=1)
    parser.add_argument("--round-extension-authorized", action="store_true")
    parser.add_argument("--attempt-id")
    parser.add_argument("--artifact-root")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def run(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    repo = Path(args.repo).resolve(strict=True)
    if not (repo / ".git").exists():
        raise ReviewError(f"not a Git repository: {repo}")
    if args.pr_number is not None and args.pr_number <= 0:
        raise ReviewError("PR number must be positive")
    if args.attempt <= 0 or args.attempt > 100:
        raise ReviewError("attempt must be between 1 and 100")
    if args.local not in LOCAL_VALUES and not SAFE_ID_RE.fullmatch(args.local):
        raise ReviewError("local provider selection must be auto, all, none, or a safe provider id")
    config, remote_config = load_review_configuration(repo)
    if (
        args.attempt > int(remote_config["roundLimit"])
        and not args.round_extension_authorized
    ):
        raise ReviewError(
            "attempt exceeds remoteIntegration roundLimit; record the structured "
            "review.round-extension decision before continuing"
        )
    scope, pr_number = resolve_scope(repo, args.scope, args.pr_number)
    pr = _pr_evidence(repo, pr_number) if pr_number is not None else None
    if pr is not None:
        effective_base = pr["base"]
    elif args.base is not None:
        effective_base = args.base
    elif scope == "branch":
        effective_base = f"origin/{_default_branch(repo)}"
    else:
        effective_base = "origin/main"
    worktree_digest = _worktree_digest(repo) if scope != "pr" else None
    controls = {
        "local": args.local,
        "remote": args.remote,
        "fix": args.fix,
        "successor": args.successor,
        "configurationDigest": _configuration_digest(config),
    }
    identity = _state_identity(
        repo=repo,
        scope=scope,
        controls=controls,
        pr=pr,
        base=effective_base,
        worktree_digest=worktree_digest,
    )
    attempt_id = _attempt_id(identity, args.attempt_id)
    root = _artifact_root(repo, args.artifact_root)
    state_path = root / f"{attempt_id}.json"
    state = _load_or_create_state(
        state_path,
        attempt_id=attempt_id,
        identity=identity,
    )
    supplied_dispositions = _parse_remote_dispositions(args.remote_disposition)
    stored_dispositions = state.get("remoteDispositions", {})
    if not isinstance(stored_dispositions, dict) or any(
        not isinstance(key, str) or value not in REMOTE_DISPOSITION_VALUES
        for key, value in stored_dispositions.items()
    ):
        raise ReviewError("stored remote dispositions are invalid")
    conflicting = [
        key
        for key, value in supplied_dispositions.items()
        if key in stored_dispositions and stored_dispositions[key] != value
    ]
    if conflicting:
        raise ReviewError("remote disposition conflicts with stored evidence")
    if supplied_dispositions:
        if not isinstance(state.get("remoteReceipt"), dict):
            raise ReviewError("remote disposition requires a stored durable receipt")
        merged_dispositions = {**stored_dispositions, **supplied_dispositions}
        _advance(
            state_path,
            state,
            str(state.get("phase", "resolve")),
            remoteDispositions=merged_dispositions,
        )

    required_providers = config.get("policy", {}).get("requiredProviders", [])
    local_policy = "required" if required_providers else "optional"
    repository = pr["repository"] if pr else None
    if scope == "pr" and repository is not None and state.get("capability") is None:
        capability = _capability(
            repo,
            remote=remote_config,
            repository=repository,
            intent=args.remote,
        )
        _advance(state_path, state, "capability", capability=capability)
    elif scope != "pr" and state.get("capability") is None:
        _advance(
            state_path,
            state,
            "capability",
            capability={"state": "skipped", "reason": "non-pr-scope"},
        )

    capability = state["capability"]
    if not isinstance(capability, dict) or capability.get("state") not in CAPABILITY_STATES:
        raise ReviewError("router capability state is invalid")
    cap_state = capability["state"]
    required_remote = remote_config["requirement"] == "required"
    explicit_remote = args.remote in {"cheap", "deep", "copilot"}
    if scope == "pr" and args.remote != "none":
        if cap_state == "absent" and (required_remote or explicit_remote):
            return 1, _report(
                state=state,
                status="blocked",
                diagnostic="routed review is required or explicit but is not configured",
                limitations=("router-not-configured",),
            )
        if cap_state in {"invalid", "incompatible", "unavailable"}:
            return 3, _report(
                state=state,
                status="indeterminate",
                diagnostic=f"routed-review capability is {cap_state}: {capability.get('reason')}",
                limitations=(f"router-{cap_state}",),
            )

    # The deterministic check is recomputed on every invocation rather than
    # served from the attempt state. A registered check may read an input the
    # attempt key does not cover — `pack.review-scope` reads the pull-request
    # body — so a stored verdict of *either* sign can disagree with what a
    # direct `sd-check` run reports on the same tree at the same moment.
    # Declining to persist a failure fixed only the direction that false-blocks;
    # a stored pass false-allows, and it is the worse half: the gate reports
    # `ready` for a body whose scope heading was removed after the pass. The
    # check is one cheap idempotent subprocess, so recomputing it costs the run
    # nothing it is not already paying, and the expensive local and remote
    # stages — whose inputs the key does cover — keep replaying from state.
    stored = state.get("check") is not None
    check = _run_check(repo)
    # A recompute is not a stage completing for the first time, so it must not
    # rewind `phase`, which names where a resume re-enters; passing the current
    # phase back is the same idiom the local refresh below uses. `stored` is the
    # only thing the persisted check is still consulted for — whether this is a
    # recompute or a first computation — and never the gate. A failing recompute
    # therefore stays out of the state file exactly as before, and whatever
    # verdict is left on disk cannot decide a later run.
    _record_stage(
        state_path,
        state,
        str(state.get("phase", "resolve")) if stored else "check",
        resumable=isinstance(check, dict) and check.get("status") == "passed",
        check=check,
    )
    check = state["check"]
    if not isinstance(check, dict) or check.get("status") != "passed":
        return 1, _report(
            state=state,
            status="blocked",
            diagnostic="typed sd-check did not pass",
            limitations=("deterministic-check-not-passed",),
        )

    # A rerun that supplies dispositions must reach the local stage even when a
    # report is already cached: the stage revalidates its durable receipt,
    # applies the rebuttals and persists them without re-running any provider.
    if state.get("local") is None or args.local_disposition:
        refreshed = state.get("local") is not None
        local = _run_local(
            repo,
            scope=scope,
            base=effective_base,
            head=pr["head"] if pr else str(identity["head"]),
            attempt_id=attempt_id,
            args=args,
            local_policy=local_policy,
        )
        # Refreshing a cached report must not rewind the phase: the remote
        # channel reads it for dispatch idempotency and reconciliation. A
        # non-resumable outcome is reported but not written, so a rejected
        # disposition set neither replays on the next invocation nor overwrites
        # the durable report an earlier one already stored.
        _record_stage(
            state_path,
            state,
            str(state.get("phase", "resolve")) if refreshed else "local",
            resumable=_local_outcome(local) not in LOCAL_NON_RESUMABLE_OUTCOMES,
            local=local,
        )
    local = state["local"]
    if not isinstance(local, dict):
        raise ReviewError("local review state is invalid")
    local_status = _local_outcome(local)
    if local_status == "findings":
        if _local_outstanding(local) != 0:
            return 1, _report(
                state=state,
                status="findings",
                diagnostic="local review findings require disposition before remote routing",
            )
        # Every provider finding carries a caller disposition. The receipt keeps
        # its ``findings`` outcome because provider evidence is never rewritten,
        # so routing reads the disposition count and the stage continues exactly
        # as a clean one does.
        local_status = "clean"
    if local_status == "blocked":
        local_diagnostic = local.get("diagnostic")
        if not isinstance(local_diagnostic, str) or not local_diagnostic.strip():
            raise ReviewError("blocked local review diagnostic is invalid")
        return 1, _report(
            state=state,
            status="blocked",
            diagnostic=_bounded(local_diagnostic),
            limitations=("local-policy-blocked",),
        )
    if local_status == "invalid":
        local_diagnostic = local.get("diagnostic")
        if not isinstance(local_diagnostic, str) or not local_diagnostic.strip():
            raise ReviewError("invalid local review diagnostic is invalid")
        return 2, _report(
            state=state,
            status="invalid",
            diagnostic=_bounded(local_diagnostic),
            limitations=("local-invalid",),
        )
    if local_status not in {"clean", "skipped", "unavailable", "failed", "cancelled"}:
        return 3, _report(
            state=state,
            status="indeterminate",
            diagnostic=f"local review returned {local_status}",
            limitations=("local-review-indeterminate",),
        )
    if local_status in {"unavailable", "failed", "cancelled"}:
        return 3, _report(
            state=state,
            status="failed",
            diagnostic="local provider failure blocks remote routing",
            limitations=(f"local-{local_status}",),
        )

    if scope != "pr":
        if local_status in {"clean", "skipped"}:
            _advance(state_path, state, "ready")
            return 0, _report(state=state, status="ready")
        return 3, _report(
            state=state,
            status="failed",
            diagnostic="non-PR review has no remote stage after local provider failure",
            limitations=(f"local-{local_status}",),
        )

    assert pr is not None and repository is not None
    if args.remote == "none":
        if required_remote:
            return 1, _report(
                state=state,
                status="blocked",
                diagnostic="remote=none does not satisfy required routed-review policy",
                limitations=("required-remote-skipped",),
            )
        _advance(state_path, state, "ready")
        return 0, _report(
            state=state,
            status="ready",
            limitations=("remote-intentionally-skipped",),
        )
    if cap_state == "absent":
        if local_status != "clean":
            return 3, _report(
                state=state,
                status="indeterminate",
                diagnostic="optional router absence requires a clean local review",
                limitations=("router-not-configured", f"local-{local_status}"),
            )
        _advance(state_path, state, "ready")
        return 0, _report(
            state=state,
            status="ready",
            limitations=("router-not-configured", "zero-remote-confidence"),
        )
    if cap_state != "ready":
        raise ReviewError(f"cannot route from capability state {cap_state}")

    if state.get("remoteRequest") is None:
        local_summary = _router_local_summary(
            local,
            repository=repository,
            pr_number=pr["number"],
            head=pr["head"],
        )
        request = _remote_request(
            repository=repository,
            pr=pr,
            route=args.remote,
            attempt=args.attempt,
            local_summary=local_summary,
            policy_reference=str(capability["actionReference"]),
        )
        _advance(state_path, state, "route-intent", remoteRequest=request)
    request = state["remoteRequest"]
    if not isinstance(request, dict):
        raise ReviewError("remote request state is invalid")

    if state.get("phase") == "reconciliation-required":
        existing = _query_receipt(
            repo,
            repository=repository,
            check_name=str(capability["checkName"]),
            request=request,
        )
        if existing is None:
            return 3, _report(
                state=state,
                status="indeterminate",
                diagnostic="remote dispatch outcome still requires durable reconciliation",
                limitations=("remote-dispatch-reconciliation-required",),
            )
        _advance(state_path, state, "receipt", remoteReceipt=existing)

    if state.get("remoteReceipt") is None and state.get("phase") == "route-intent":
        existing = _query_receipt(
            repo,
            repository=repository,
            check_name=str(capability["checkName"]),
            request=request,
        )
        if existing is not None:
            _advance(state_path, state, "receipt", remoteReceipt=existing)
        else:
            try:
                _dispatch(repo, workflow=str(capability["workflow"]["path"]), request=request)
            except CommandError as error:
                _advance(state_path, state, "reconciliation-required")
                return 3, _report(
                    state=state,
                    status="indeterminate",
                    diagnostic=_bounded(error),
                    limitations=("remote-dispatch-reconciliation-required",),
                )
            _advance(state_path, state, "route-dispatched")

    if state.get("remoteReceipt") is None:
        receipt = None
        for index in range(int(remote_config["receiptPolls"])):
            receipt = _query_receipt(
                repo,
                repository=repository,
                check_name=str(capability["checkName"]),
                request=request,
            )
            if receipt is not None:
                break
            if index + 1 < int(remote_config["receiptPolls"]):
                time.sleep(int(remote_config["pollSeconds"]))
        if receipt is None:
            return 3, _report(
                state=state,
                status="pending",
                diagnostic="routed-review dispatch is recorded but its durable receipt is not visible yet",
                limitations=("receipt-pending",),
            )
        _advance(state_path, state, "receipt", remoteReceipt=receipt)

    receipt = state["remoteReceipt"]
    if not isinstance(receipt, dict):
        raise ReviewError("remote receipt state is invalid")
    dispatch = receipt.get("dispatch")
    if not isinstance(dispatch, dict):
        raise ReviewError("remote receipt dispatch is invalid")
    if dispatch.get("status") == "failed" or dispatch.get("phase") == "started":
        return 3, _report(
            state=state,
            status="indeterminate",
            diagnostic="routed-review receipt requires reconciliation",
            limitations=("remote-reconciliation-required",),
        )

    latest = _pr_evidence(repo, pr["number"])
    if latest["head"] != pr["head"]:
        raise ReviewError("pull request head changed before remote observation")
    observation = _collect_observation(
        repo,
        pr=latest,
        receipt=receipt,
        receipt_check_name=str(capability["checkName"]),
        dispositions=state.get("remoteDispositions", {}),
    )
    _advance(state_path, state, "observe", observation=observation)
    observation_status = observation["status"]
    if observation_status == "clean":
        _advance(state_path, state, "ready")
        return 0, _report(state=state, status="ready")
    if observation_status == "findings":
        return 1, _report(
            state=state,
            status="findings",
            diagnostic="remote review findings require disposition",
        )
    if observation_status == "blocked":
        return 1, _report(
            state=state,
            status="blocked",
            diagnostic="pull request checks block review readiness",
        )
    return 3, _report(
        state=state,
        status="pending",
        diagnostic="remote findings or CI have not reached a terminal exact-head state",
        limitations=("observation-pending",),
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        code, report = run(args)
    except (CacheSetupError, CommandError, OSError, ReviewError) as error:
        code = 2 if isinstance(error, (OSError, ReviewError)) else 3
        report = {
            "schemaVersion": 1,
            "command": "sd-review",
            "status": "invalid" if code == 2 else "indeterminate",
            "phase": "setup",
            "diagnostic": _bounded(error),
            "limitations": [],
        }
    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2))
    else:
        _print_human(report)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
