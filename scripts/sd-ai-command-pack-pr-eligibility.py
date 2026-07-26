#!/usr/bin/env python3
"""Evaluate pull-request merge eligibility without mutating repository state."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

# This import must follow the bytecode guard for direct entrypoint invocation.
from sd_ai_command_pack_lib import CacheSetupError, build_tool_environment  # noqa: E402

SCHEMA_VERSION = 1
FINISH_WORK_RECEIPT_SCHEMA_VERSION = 1
TOOL_VERSION = "1.1.0"
COMMAND_TIMEOUT_SECONDS = 60
GH_TIMEOUT_SECONDS = 120
MAX_INPUT_BYTES = 64 * 1024
MAX_THREAD_PAGES = 100
FIELD_SEPARATOR = "\x1f"
COMMIT_RE = re.compile(r"[0-9a-f]{40}")
GITHUB_SLUG_RE = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]+")
REQUEST_FIELDS = frozenset(
    {
        "schemaVersion",
        "mode",
        "repository",
        "branch",
        "pullRequestNumber",
        "remote",
        "defaultBranch",
        "finishWorkRequired",
        "finishWorkReceipt",
        "githubRepository",
    }
)
REVIEW_THREADS_QUERY = (
    "query($owner:String!,$name:String!,$number:Int!,$cursor:String){"
    "repository(owner:$owner,name:$name){pullRequest(number:$number){"
    "reviewThreads(first:100,after:$cursor){nodes{isResolved}"
    "pageInfo{hasNextPage endCursor}}}}}"
)


class EligibilityInputError(ValueError):
    """Raised when a request cannot be evaluated safely."""


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str


Runner = Callable[[Sequence[str], Path, int], CommandResult]


def run_command(argv: Sequence[str], cwd: Path, timeout: int) -> CommandResult:
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
            timeout=timeout,
        )
        return CommandResult(result.returncode, result.stdout)
    except CacheSetupError as error:
        print(f"eligibility cache setup failed: {error}", file=sys.stderr)
        return CommandResult(127, "")
    except (OSError, UnicodeError, subprocess.TimeoutExpired):
        return CommandResult(127, "")


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def safe_text(value: object, *, limit: int = 300) -> str:
    text = CONTROL_RE.sub(" ", str(value)).strip()
    if len(text) <= limit:
        return text
    return f"{text[: max(0, limit - 3)].rstrip()}..."


def require_string(
    value: object,
    field: str,
    *,
    limit: int = 512,
    allow_option: bool = False,
) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EligibilityInputError(f"eligibility input {field} must be a non-empty string")
    normalized = value.strip()
    if CONTROL_RE.search(normalized) or len(normalized) > limit:
        raise EligibilityInputError(f"eligibility input {field} is invalid")
    if not allow_option and normalized.startswith("-"):
        raise EligibilityInputError(f"eligibility input {field} must not start with '-'")
    return normalized


def validate_request(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise EligibilityInputError("eligibility input must be a JSON object")
    unknown = sorted(set(value) - REQUEST_FIELDS)
    if unknown:
        raise EligibilityInputError(
            "eligibility input contains unknown field(s): " + ", ".join(unknown)
        )
    schema_version = value.get("schemaVersion")
    if (
        isinstance(schema_version, bool)
        or not isinstance(schema_version, int)
        or schema_version != SCHEMA_VERSION
    ):
        raise EligibilityInputError(
            f"eligibility input schemaVersion must be {SCHEMA_VERSION}"
        )
    repository = require_string(
        value.get("repository"), "repository", limit=4096, allow_option=True
    )
    mode = value.get("mode")
    if mode not in {"local-branch", "dependency-pr"}:
        raise EligibilityInputError(
            "eligibility input mode must be local-branch or dependency-pr"
        )
    raw_branch = value.get("branch")
    raw_pr_number = value.get("pullRequestNumber")
    if mode == "local-branch":
        branch = require_string(raw_branch, "branch")
        if raw_pr_number is not None:
            raise EligibilityInputError(
                "eligibility input pullRequestNumber must be null in local-branch mode"
            )
        pr_number = None
    else:
        if raw_branch is not None:
            raise EligibilityInputError(
                "eligibility input branch must be null in dependency-pr mode"
            )
        branch = None
        if (
            isinstance(raw_pr_number, bool)
            or not isinstance(raw_pr_number, int)
            or raw_pr_number <= 0
        ):
            raise EligibilityInputError(
                "eligibility input pullRequestNumber must be a positive integer in dependency-pr mode"
            )
        pr_number = raw_pr_number
    remote = require_string(value.get("remote"), "remote")
    default_branch = require_string(value.get("defaultBranch"), "defaultBranch")

    finish_work_required = value.get("finishWorkRequired")
    if not isinstance(finish_work_required, bool):
        raise EligibilityInputError(
            "eligibility input finishWorkRequired must be a boolean"
        )
    finish_work_receipt = value.get("finishWorkReceipt")
    if finish_work_receipt is not None:
        finish_work_receipt = validate_finish_work_receipt(finish_work_receipt)
    if not finish_work_required and finish_work_receipt is not None:
        raise EligibilityInputError(
            "eligibility input finishWorkReceipt must be null when finishWorkRequired is false"
        )
    if mode == "local-branch" and not finish_work_required:
        raise EligibilityInputError(
            "eligibility input finishWorkRequired must be true in local-branch mode"
        )
    if mode == "dependency-pr" and finish_work_required:
        raise EligibilityInputError(
            "eligibility input finishWorkRequired must be false in dependency-pr mode"
        )

    github_repository = value.get("githubRepository")
    if github_repository is not None:
        github_repository = require_string(
            github_repository, "githubRepository", limit=200, allow_option=True
        )
        if not GITHUB_SLUG_RE.fullmatch(github_repository):
            raise EligibilityInputError(
                "eligibility input githubRepository must be an owner/repo slug"
            )

    return {
        "schemaVersion": SCHEMA_VERSION,
        "mode": mode,
        "repository": repository,
        "branch": branch,
        "pullRequestNumber": pr_number,
        "remote": remote,
        "defaultBranch": default_branch,
        "finishWorkRequired": finish_work_required,
        "finishWorkReceipt": finish_work_receipt,
        "githubRepository": github_repository,
    }


def validate_finish_work_receipt(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise EligibilityInputError(
            "eligibility input finishWorkReceipt must be a JSON object"
        )
    if value.get("schemaVersion") != FINISH_WORK_RECEIPT_SCHEMA_VERSION:
        raise EligibilityInputError(
            "eligibility input finishWorkReceipt schemaVersion must be "
            f"{FINISH_WORK_RECEIPT_SCHEMA_VERSION}"
        )
    if value.get("kind") != "trellis-bookkeeping-validation":
        raise EligibilityInputError(
            "eligibility input finishWorkReceipt kind is invalid"
        )
    if value.get("command") != "final-bundle":
        raise EligibilityInputError(
            "eligibility input finishWorkReceipt command must be final-bundle"
        )
    mode = value.get("mode")
    if mode not in {"completion", "planning"}:
        raise EligibilityInputError(
            "eligibility input finishWorkReceipt mode must be completion or planning"
        )
    if value.get("status") != "valid":
        raise EligibilityInputError(
            "eligibility input finishWorkReceipt status must be valid"
        )
    if value.get("reasonCodes") != [f"{mode}_bundle_valid"]:
        raise EligibilityInputError(
            "eligibility input finishWorkReceipt reasonCodes are invalid"
        )
    if value.get("findings") != []:
        raise EligibilityInputError(
            "eligibility input finishWorkReceipt findings must be empty"
        )
    evidence = value.get("evidence")
    if not isinstance(evidence, Mapping):
        raise EligibilityInputError(
            "eligibility input finishWorkReceipt evidence must be an object"
        )
    completion_subtype = evidence.get("completionSubtype")
    planning_subtype = evidence.get("planningSubtype")
    if completion_subtype is not None:
        require_string(
            completion_subtype,
            "finishWorkReceipt evidence.completionSubtype",
            limit=100,
        )
    if planning_subtype is not None:
        require_string(
            planning_subtype,
            "finishWorkReceipt evidence.planningSubtype",
            limit=100,
        )
    if mode == "completion" and planning_subtype is not None:
        raise EligibilityInputError(
            "eligibility input finishWorkReceipt evidence.planningSubtype must be null in completion mode"
        )
    if mode == "planning" and completion_subtype is not None:
        raise EligibilityInputError(
            "eligibility input finishWorkReceipt evidence.completionSubtype must be null in planning mode"
        )
    for field in ("baseOid", "headOid"):
        oid = evidence.get(field)
        if not isinstance(oid, str) or not COMMIT_RE.fullmatch(oid):
            raise EligibilityInputError(
                f"eligibility input finishWorkReceipt evidence.{field} must be a full lowercase commit OID"
            )
    repository = evidence.get("repository")
    if not isinstance(repository, Mapping):
        raise EligibilityInputError(
            "eligibility input finishWorkReceipt evidence.repository must be an object"
        )
    branch = repository.get("branch")
    if branch is not None:
        require_string(branch, "finishWorkReceipt evidence.repository.branch")
    lineage_digest = repository.get("lineageDigest")
    if not isinstance(lineage_digest, str) or not re.fullmatch(
        r"sha256:[0-9a-f]{64}", lineage_digest
    ):
        raise EligibilityInputError(
            "eligibility input finishWorkReceipt evidence.repository.lineageDigest is invalid"
        )
    return dict(value)


def load_request(path: Path) -> object:
    if path.is_symlink() or not path.is_file():
        raise EligibilityInputError(f"eligibility input is not a regular file: {path}")
    try:
        if path.stat().st_size > MAX_INPUT_BYTES:
            raise EligibilityInputError(
                f"eligibility input exceeds the {MAX_INPUT_BYTES}-byte limit"
            )
        return json.loads(path.read_text(encoding="utf-8", errors="strict"))
    except EligibilityInputError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise EligibilityInputError(f"cannot read eligibility input {path}: {exc}") from exc


def load_finish_work_receipt(path: Path) -> dict[str, Any]:
    return validate_finish_work_receipt(load_request(path))


def revalidate_finish_work_receipt(
    receipt: Mapping[str, Any],
    *,
    repo: Path,
    branch: str,
    head: str,
    runner: Runner,
) -> tuple[str, dict[str, Any], str]:
    evidence = receipt["evidence"]
    receipt_head = str(evidence["headOid"])
    receipt_branch = evidence["repository"].get("branch")
    summary = {
        "required": True,
        "provided": True,
        "mode": receipt["mode"],
        "completionSubtype": evidence.get("completionSubtype"),
        "planningSubtype": evidence.get("planningSubtype"),
        "headOid": receipt_head,
        "matchesCurrentHead": receipt_head == head,
        "verified": False,
    }
    if receipt_head != head or receipt_branch != branch:
        return (
            "stale",
            summary,
            "finish-work receipt does not match the current branch and exact head",
        )
    helper = Path(__file__).resolve().with_name(
        "sd-ai-command-pack-review-preflight.mjs"
    )
    result = runner(
        [
            "node",
            str(helper),
            "final-bundle",
            "--mode",
            str(receipt["mode"]),
            "--base",
            str(evidence["baseOid"]),
            "--head",
            receipt_head,
            "--repo",
            str(repo),
            "--json",
        ],
        repo,
        COMMAND_TIMEOUT_SECONDS,
    )
    if result.returncode not in {0, 1} or not result.stdout.strip():
        return (
            "unavailable",
            summary,
            "finish-work receipt could not be independently revalidated",
        )
    try:
        observed = json.loads(result.stdout)
    except json.JSONDecodeError:
        return (
            "unavailable",
            summary,
            "finish-work validator returned malformed JSON",
        )
    if not isinstance(observed, Mapping):
        return (
            "unavailable",
            summary,
            "finish-work validator returned a non-object result",
        )
    if observed.get("status") == "indeterminate":
        reasons = observed.get("reasonCodes")
        reason_text = ", ".join(
            str(item) for item in reasons[:8]
        ) if isinstance(reasons, list) else "unavailable evidence"
        return (
            "unavailable",
            summary,
            f"finish-work receipt revalidation is indeterminate: {safe_text(reason_text)}",
        )
    if observed.get("status") != "valid":
        reasons = observed.get("reasonCodes")
        reason_text = ", ".join(
            str(item) for item in reasons[:8]
        ) if isinstance(reasons, list) else "invalid evidence"
        return (
            "invalid",
            summary,
            f"finish-work receipt no longer validates: {safe_text(reason_text)}",
        )
    if dict(observed) != dict(receipt):
        return (
            "mismatch",
            summary,
            "finish-work receipt differs from independently recomputed evidence",
        )
    summary["verified"] = True
    return "valid", summary, ""


def parse_json_object(result: CommandResult, label: str) -> dict[str, Any]:
    if result.returncode != 0 or not result.stdout.strip():
        raise EligibilityInputError(f"{label} is unavailable")
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise EligibilityInputError(f"{label} returned malformed JSON") from exc
    if not isinstance(value, dict):
        raise EligibilityInputError(f"{label} returned a non-object result")
    return value


def parse_commit_output(result: CommandResult) -> str | None:
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return value if COMMIT_RE.fullmatch(value) else None


def parse_remote_head(result: CommandResult) -> str | None:
    if result.returncode != 0:
        return None
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    if len(lines) != 1:
        return None
    value = lines[0].split("\t", 1)[0].strip()
    return value if COMMIT_RE.fullmatch(value) else None


def require_pr_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or CONTROL_RE.search(value):
        raise EligibilityInputError(f"pull request field {field} is invalid")
    return value.strip()


def parse_checks(value: object) -> tuple[list[dict[str, Any]], int, int]:
    if not isinstance(value, list):
        raise EligibilityInputError("pull request statusCheckRollup is invalid")
    observed: list[dict[str, Any]] = []
    blocking = 0
    successful = 0
    for index, raw in enumerate(value, start=1):
        if not isinstance(raw, Mapping):
            raise EligibilityInputError(f"pull request check {index} is invalid")
        check_type = raw.get("__typename")
        if check_type == "CheckRun":
            status = raw.get("status")
            conclusion = raw.get("conclusion")
            if not isinstance(status, str) or (
                conclusion is not None and not isinstance(conclusion, str)
            ):
                raise EligibilityInputError(f"pull request check {index} is invalid")
            if status == "COMPLETED" and conclusion == "SUCCESS":
                successful += 1
            elif status != "COMPLETED" or conclusion not in {
                "SKIPPED",
                "NEUTRAL",
            }:
                blocking += 1
            observed.append(
                {
                    "type": "CheckRun",
                    "name": safe_text(raw.get("name") or "unnamed", limit=160),
                    "workflow": safe_text(raw.get("workflowName") or "", limit=160),
                    "status": status,
                    "conclusion": conclusion,
                    "url": safe_text(raw.get("detailsUrl") or "", limit=500),
                }
            )
        elif check_type == "StatusContext":
            state = raw.get("state")
            if not isinstance(state, str):
                raise EligibilityInputError(f"pull request check {index} is invalid")
            if state == "SUCCESS":
                successful += 1
            else:
                blocking += 1
            observed.append(
                {
                    "type": "StatusContext",
                    "name": safe_text(raw.get("context") or "unnamed", limit=160),
                    "state": state,
                    "url": safe_text(raw.get("targetUrl") or "", limit=500),
                }
            )
        else:
            raise EligibilityInputError(f"pull request check {index} has unknown type")
    return observed, blocking, successful


def parse_pr(value: Mapping[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]], int, int]:
    number = value.get("number")
    is_draft = value.get("isDraft")
    if isinstance(number, bool) or not isinstance(number, int) or number <= 0:
        raise EligibilityInputError("pull request field number is invalid")
    if not isinstance(is_draft, bool):
        raise EligibilityInputError("pull request field isDraft is invalid")
    head_oid = require_pr_string(value.get("headRefOid"), "headRefOid")
    if not COMMIT_RE.fullmatch(head_oid):
        raise EligibilityInputError("pull request field headRefOid is invalid")
    pr = {
        "number": number,
        "state": require_pr_string(value.get("state"), "state"),
        "isDraft": is_draft,
        "url": require_pr_string(value.get("url"), "url"),
        "headRefName": require_pr_string(value.get("headRefName"), "headRefName"),
        "headOid": head_oid,
        "finalHeadOid": None,
        "baseRefName": require_pr_string(value.get("baseRefName"), "baseRefName"),
        "mergeStateStatus": require_pr_string(
            value.get("mergeStateStatus"), "mergeStateStatus"
        ),
    }
    checks, blocking, successful = parse_checks(value.get("statusCheckRollup"))
    return pr, checks, blocking, successful


def query_pr(
    repo: Path,
    slug: str,
    subject: str,
    runner: Runner,
) -> tuple[dict[str, Any], list[dict[str, Any]], int, int]:
    result = runner(
        [
            "gh",
            "pr",
            "view",
            subject,
            "--repo",
            slug,
            "--json",
            "number,state,isDraft,url,headRefName,headRefOid,baseRefName,mergeStateStatus,statusCheckRollup",
        ],
        repo,
        GH_TIMEOUT_SECONDS,
    )
    return parse_pr(parse_json_object(result, "pull request readiness"))


def query_pr_head(repo: Path, slug: str, pr_number: int, runner: Runner) -> str | None:
    result = runner(
        [
            "gh",
            "pr",
            "view",
            str(pr_number),
            "--repo",
            slug,
            "--json",
            "headRefOid",
        ],
        repo,
        GH_TIMEOUT_SECONDS,
    )
    try:
        payload = parse_json_object(result, "pull request head")
        head = payload.get("headRefOid")
    except EligibilityInputError:
        return None
    return head if isinstance(head, str) and COMMIT_RE.fullmatch(head) else None


def collect_threads(
    repo: Path,
    slug: str,
    pr_number: int,
    runner: Runner,
) -> dict[str, int]:
    owner, name = slug.split("/", 1)
    cursor: str | None = None
    page_count = 0
    total_count = 0
    unresolved_count = 0
    while True:
        args = [
            "gh",
            "api",
            "graphql",
            "-F",
            f"owner={owner}",
            "-F",
            f"name={name}",
            "-F",
            f"number={pr_number}",
        ]
        if cursor is not None:
            args.extend(["-F", f"cursor={cursor}"])
        args.extend(["-f", f"query={REVIEW_THREADS_QUERY}"])
        payload = parse_json_object(
            runner(args, repo, GH_TIMEOUT_SECONDS), "review thread query"
        )
        if payload.get("errors"):
            raise EligibilityInputError("review thread query returned errors")
        try:
            threads = payload["data"]["repository"]["pullRequest"]["reviewThreads"]
            nodes = threads["nodes"]
            page_info = threads["pageInfo"]
        except (KeyError, TypeError) as exc:
            raise EligibilityInputError("review thread query result is incomplete") from exc
        if not isinstance(nodes, list) or not isinstance(page_info, Mapping):
            raise EligibilityInputError("review thread query result is incomplete")
        for node in nodes:
            if not isinstance(node, Mapping) or not isinstance(
                node.get("isResolved"), bool
            ):
                raise EligibilityInputError("review thread query contains invalid nodes")
            total_count += 1
            if not node["isResolved"]:
                unresolved_count += 1
        page_count += 1
        has_next = page_info.get("hasNextPage")
        end_cursor = page_info.get("endCursor")
        if not isinstance(has_next, bool):
            raise EligibilityInputError("review thread pageInfo is invalid")
        if not has_next:
            break
        if (
            not isinstance(end_cursor, str)
            or not end_cursor
            or CONTROL_RE.search(end_cursor)
            or page_count >= MAX_THREAD_PAGES
        ):
            raise EligibilityInputError("review thread pagination is incomplete")
        cursor = end_cursor
    return {
        "pageCount": page_count,
        "totalCount": total_count,
        "unresolvedCount": unresolved_count,
    }


def evaluate_dependency_request(
    request: Mapping[str, Any],
    *,
    runner: Runner,
    now: Callable[[], str],
) -> dict[str, Any]:
    started_at = now()
    repo_input = Path(str(request["repository"])).expanduser()
    if not repo_input.is_absolute():
        repo_input = Path.cwd() / repo_input
    if not repo_input.is_dir():
        return invalid_result(
            request,
            "repository_unavailable",
            "repository is not a directory",
            started_at,
            now(),
        )
    root_result = runner(
        ["git", "rev-parse", "--show-toplevel"], repo_input, COMMAND_TIMEOUT_SECONDS
    )
    if root_result.returncode != 0 or not root_result.stdout.strip():
        return invalid_result(
            request,
            "repository_unavailable",
            "repository identity is unavailable",
            started_at,
            now(),
        )
    try:
        repo = Path(root_result.stdout.strip()).resolve(strict=True)
    except OSError:
        return invalid_result(
            request,
            "repository_unavailable",
            "repository identity is unavailable",
            started_at,
            now(),
        )
    slug = request.get("githubRepository")
    if not isinstance(slug, str):
        return invalid_result(
            request,
            "github_repository_unavailable",
            "GitHub repository identity is required for dependency PR evaluation",
            started_at,
            now(),
        )
    requested_number = request.get("pullRequestNumber")
    if not isinstance(requested_number, int):
        return invalid_result(
            request,
            "invalid_input",
            "dependency pull request number is unavailable",
            started_at,
            now(),
        )
    try:
        pr, checks, blocking_checks, successful_checks = query_pr(
            repo, slug, str(requested_number), runner
        )
    except EligibilityInputError:
        return invalid_result(
            request,
            "pull_request_unavailable",
            f"could not inspect dependency PR #{requested_number}",
            started_at,
            now(),
        )
    start_head = pr["headOid"]
    evidence: dict[str, Any] = {
        "repository": {"root": str(repo), "githubSlug": slug},
        "invocation": {
            "mode": "dependency-pr",
            "pullRequestNumber": requested_number,
            "remote": request["remote"],
            "defaultBranch": request["defaultBranch"],
        },
        "head": {"startOid": start_head, "endOid": None, "remoteOid": start_head},
        "finishWork": {
            "required": False,
            "provided": False,
            "mode": None,
            "completionSubtype": None,
            "planningSubtype": None,
            "headOid": None,
            "matchesCurrentHead": False,
            "verified": False,
        },
        "pullRequest": pr,
        "checks": {
            "items": checks,
            "blockingCount": blocking_checks,
            "successfulCount": successful_checks,
        },
        "reviewThreads": {"pageCount": 0, "totalCount": 0, "unresolvedCount": None},
        "routedReview": {
            "status": "deferred",
            "reason": "unified receipt schema is owned by the routed-review integration task",
        },
    }

    def finish(
        status: str,
        reason_codes: Sequence[str],
        diagnostic: str,
        *,
        retryable: bool = False,
    ) -> dict[str, Any]:
        end_head = query_pr_head(repo, slug, requested_number, runner)
        evidence["head"]["endOid"] = end_head
        pr["finalHeadOid"] = end_head
        if end_head is None:
            status = "indeterminate"
            reason_codes = ["head_unavailable"]
            diagnostic = (
                f"dependency PR #{requested_number} head became unavailable; retry evaluation"
            )
            retryable = True
        elif end_head != start_head:
            status = "indeterminate"
            reason_codes = ["head_changed"]
            diagnostic = (
                f"dependency PR #{requested_number} changed from {start_head} to {end_head} "
                "during eligibility evaluation; retry for the new head"
            )
            retryable = True
        return {
            "schemaVersion": SCHEMA_VERSION,
            "toolVersion": TOOL_VERSION,
            "status": status,
            "reasonCodes": list(reason_codes),
            "retryable": retryable,
            "diagnostic": safe_text(diagnostic),
            "startedAt": started_at,
            "completedAt": now(),
            **evidence,
        }

    if pr["number"] != requested_number:
        return finish(
            "indeterminate",
            ["pull_request_identity_mismatch"],
            f"dependency PR identity changed while inspecting #{requested_number}",
        )
    if pr["state"] != "OPEN":
        return finish(
            "blocked",
            ["pull_request_not_open"],
            f"PR #{requested_number} is {pr['state']}, not OPEN; skipped auto-merge",
        )
    if pr["isDraft"]:
        return finish(
            "blocked",
            ["pull_request_draft"],
            f"PR #{requested_number} is a draft; skipped auto-merge",
        )
    if pr["baseRefName"] != request["defaultBranch"]:
        return finish(
            "blocked",
            ["pull_request_base_mismatch"],
            f"PR #{requested_number} base is {pr['baseRefName']}, expected {request['defaultBranch']}; skipped auto-merge",
        )
    if pr["mergeStateStatus"] != "CLEAN":
        return finish(
            "blocked",
            ["merge_state_not_clean"],
            f"PR #{requested_number} merge state is {pr['mergeStateStatus']}, not CLEAN; skipped auto-merge",
        )
    if successful_checks == 0:
        return finish(
            "blocked",
            ["checks_no_success"],
            f"PR #{requested_number} has no successful executed checks; skipped auto-merge",
        )
    if blocking_checks != 0:
        return finish(
            "blocked",
            ["checks_blocking"],
            f"PR #{requested_number} has non-green checks; skipped auto-merge",
        )
    try:
        thread_evidence = collect_threads(repo, slug, requested_number, runner)
    except EligibilityInputError:
        return finish(
            "indeterminate",
            ["review_threads_unavailable"],
            f"failed to inspect review threads for PR #{requested_number}; skipped auto-merge",
            retryable=True,
        )
    evidence["reviewThreads"] = thread_evidence
    if thread_evidence["unresolvedCount"] != 0:
        return finish(
            "blocked",
            ["review_threads_unresolved"],
            f"PR #{requested_number} has {thread_evidence['unresolvedCount']} unresolved review thread(s); skipped auto-merge",
        )
    return finish(
        "eligible",
        [],
        f"dependency PR #{requested_number} is open, green, comment-clean, and exact-head current",
    )


def evaluate_request(
    raw_request: object,
    *,
    runner: Runner = run_command,
    now: Callable[[], str] = utc_timestamp,
) -> dict[str, Any]:
    request = validate_request(raw_request)
    if request["mode"] == "dependency-pr":
        return evaluate_dependency_request(request, runner=runner, now=now)
    started_at = now()
    repo_input = Path(request["repository"]).expanduser()
    if not repo_input.is_absolute():
        repo_input = Path.cwd() / repo_input
    if not repo_input.is_dir():
        return invalid_result(
            request, "repository_unavailable", "repository is not a directory", started_at, now()
        )
    root_result = runner(
        ["git", "rev-parse", "--show-toplevel"], repo_input, COMMAND_TIMEOUT_SECONDS
    )
    if root_result.returncode != 0 or not root_result.stdout.strip():
        return invalid_result(
            request, "repository_unavailable", "repository identity is unavailable", started_at, now()
        )
    try:
        repo = Path(root_result.stdout.strip()).resolve(strict=True)
    except OSError:
        return invalid_result(
            request, "repository_unavailable", "repository identity is unavailable", started_at, now()
        )

    branch = request["branch"]
    start_head = parse_commit_output(
        runner(
            ["git", "rev-parse", "--verify", f"refs/heads/{branch}^{{commit}}"],
            repo,
            COMMAND_TIMEOUT_SECONDS,
        )
    )
    evidence: dict[str, Any] = {
        "repository": {
            "root": str(repo),
            "githubSlug": request["githubRepository"],
        },
        "invocation": {
            "mode": "local-branch",
            "branch": branch,
            "remote": request["remote"],
            "defaultBranch": request["defaultBranch"],
        },
        "head": {"startOid": start_head, "endOid": None, "remoteOid": None},
        "finishWork": {
            "required": request["finishWorkRequired"],
            "provided": request["finishWorkReceipt"] is not None,
            "mode": None,
            "completionSubtype": None,
            "planningSubtype": None,
            "headOid": None,
            "matchesCurrentHead": False,
            "verified": False,
        },
        "pullRequest": None,
        "checks": {"items": [], "blockingCount": None, "successfulCount": None},
        "reviewThreads": {"pageCount": 0, "totalCount": 0, "unresolvedCount": None},
        "routedReview": {
            "status": "deferred",
            "reason": "unified receipt schema is owned by the routed-review integration task",
        },
    }
    pr_slug: str | None = None
    pr_number: int | None = None
    initial_pr_head: str | None = None

    def finish(
        status: str,
        reason_codes: Sequence[str],
        diagnostic: str,
        *,
        retryable: bool = False,
    ) -> dict[str, Any]:
        final_pr_head = None
        if pr_slug is not None and pr_number is not None:
            final_pr_head = query_pr_head(repo, pr_slug, pr_number, runner)
            pull_request = evidence["pullRequest"]
            if isinstance(pull_request, dict):
                pull_request["finalHeadOid"] = final_pr_head
        end_head = parse_commit_output(
            runner(
                ["git", "rev-parse", "--verify", f"refs/heads/{branch}^{{commit}}"],
                repo,
                COMMAND_TIMEOUT_SECONDS,
            )
        )
        evidence["head"]["endOid"] = end_head
        final_status = status
        final_reasons = list(reason_codes)
        final_diagnostic = diagnostic
        final_retryable = retryable
        if start_head is None or end_head is None:
            final_status = "indeterminate"
            final_reasons = ["head_unavailable"]
            final_diagnostic = f"local {branch} head is unavailable; skipped auto-merge"
            final_retryable = True
        elif end_head != start_head:
            final_status = "indeterminate"
            final_reasons = ["head_changed"]
            final_diagnostic = (
                f"local {branch} changed from {start_head} to {end_head} during "
                "eligibility evaluation; retry housekeeping for the new head"
            )
            final_retryable = True
        elif pr_number is not None and initial_pr_head is not None:
            if final_pr_head is None:
                final_status = "indeterminate"
                final_reasons = ["head_unavailable"]
                final_diagnostic = (
                    f"PR #{pr_number} head became unavailable; retry eligibility "
                    "evaluation"
                )
                final_retryable = True
            elif final_pr_head != initial_pr_head:
                final_status = "indeterminate"
                final_reasons = ["head_changed"]
                final_diagnostic = (
                    f"PR #{pr_number} changed from {initial_pr_head} to "
                    f"{final_pr_head} during eligibility evaluation; retry for the "
                    "new head"
                )
                final_retryable = True
        return {
            "schemaVersion": SCHEMA_VERSION,
            "toolVersion": TOOL_VERSION,
            "status": final_status,
            "reasonCodes": final_reasons,
            "retryable": final_retryable,
            "diagnostic": safe_text(final_diagnostic),
            "startedAt": started_at,
            "completedAt": now(),
            **evidence,
        }

    if start_head is None:
        return finish(
            "indeterminate",
            ["head_unavailable"],
            f"local {branch} head is unavailable; skipped auto-merge",
            retryable=True,
        )

    status_result = runner(
        ["git", "status", "--porcelain"], repo, COMMAND_TIMEOUT_SECONDS
    )
    if status_result.returncode != 0:
        return finish(
            "indeterminate",
            ["working_tree_unavailable"],
            "working tree cleanliness could not be verified; skipped auto-merge",
            retryable=True,
        )
    if status_result.stdout:
        return finish(
            "blocked",
            ["working_tree_dirty"],
            "working tree has uncommitted changes; skipped auto-merge",
        )

    if request["finishWorkRequired"]:
        receipt = request["finishWorkReceipt"]
        if receipt is None:
            return finish(
                "blocked",
                ["finish_work_missing"],
                f"SD finish-work evidence is missing for PR branch {branch}; run sd-finish-work and pass its retained JSON receipt to housekeeping; skipped auto-merge",
            )
        receipt_status, receipt_summary, receipt_diagnostic = (
            revalidate_finish_work_receipt(
                receipt,
                repo=repo,
                branch=branch,
                head=start_head,
                runner=runner,
            )
        )
        evidence["finishWork"] = receipt_summary
        if receipt_status == "stale":
            return finish(
                "blocked",
                ["finish_work_stale"],
                f"{receipt_diagnostic}; rerun sd-finish-work for the current head before housekeeping; skipped auto-merge",
            )
        if receipt_status == "invalid":
            return finish(
                "blocked",
                ["finish_work_invalid"],
                f"{receipt_diagnostic}; skipped auto-merge",
            )
        if receipt_status == "mismatch":
            return finish(
                "blocked",
                ["finish_work_receipt_mismatch"],
                f"{receipt_diagnostic}; skipped auto-merge",
            )
        if receipt_status != "valid":
            return finish(
                "indeterminate",
                ["finish_work_unavailable"],
                f"{receipt_diagnostic}; skipped auto-merge",
                retryable=True,
            )

    slug = request["githubRepository"]
    if slug is None:
        return finish(
            "indeterminate",
            ["github_repository_unavailable"],
            f"could not derive GitHub repo from {request['remote']}; skipped auto-merge",
        )
    pr_slug = slug

    try:
        pr, checks, blocking_checks, successful_checks = query_pr(
            repo, slug, branch, runner
        )
    except EligibilityInputError:
        return finish(
            "indeterminate",
            ["pull_request_unavailable"],
            f"could not inspect an open PR for {branch}; skipped auto-merge",
            retryable=True,
        )
    evidence["pullRequest"] = pr
    evidence["checks"] = {
        "items": checks,
        "blockingCount": blocking_checks,
        "successfulCount": successful_checks,
    }
    pr_number = pr["number"]
    initial_pr_head = pr["headOid"]

    if pr["state"] != "OPEN":
        return finish(
            "blocked",
            ["pull_request_not_open"],
            f"PR #{pr_number} is {pr['state']}, not OPEN; skipped auto-merge",
        )
    if pr["isDraft"]:
        return finish(
            "blocked",
            ["pull_request_draft"],
            f"PR #{pr_number} for {branch} is a draft; skipped auto-merge",
        )
    if pr["headRefName"] != branch:
        return finish(
            "blocked",
            ["pull_request_branch_mismatch"],
            f"PR #{pr_number} head is {pr['headRefName']}, not {branch}; skipped auto-merge",
        )
    if pr["baseRefName"] != request["defaultBranch"]:
        return finish(
            "blocked",
            ["pull_request_base_mismatch"],
            f"PR #{pr_number} base is {pr['baseRefName']}, expected {request['defaultBranch']}; skipped auto-merge",
        )

    if pr["headOid"] != start_head:
        return finish(
            "blocked",
            ["pull_request_head_mismatch"],
            f"local {branch} is at {start_head}, but PR #{pr_number} is at {pr['headOid']}; skipped auto-merge",
        )

    remote_result = runner(
        [
            "git",
            "ls-remote",
            "--exit-code",
            request["remote"],
            f"refs/heads/{branch}",
        ],
        repo,
        COMMAND_TIMEOUT_SECONDS,
    )
    remote_head = parse_remote_head(remote_result)
    evidence["head"]["remoteOid"] = remote_head
    if remote_head is None:
        return finish(
            "indeterminate",
            ["remote_head_unavailable"],
            f"failed to read remote branch head for {request['remote']}/{branch}; skipped auto-merge",
            retryable=True,
        )
    if remote_head != start_head:
        return finish(
            "blocked",
            ["remote_head_mismatch"],
            f"remote branch {request['remote']}/{branch} is at {remote_head}, but local {branch} is at {start_head}; skipped auto-merge",
        )
    if pr["mergeStateStatus"] != "CLEAN":
        return finish(
            "blocked",
            ["merge_state_not_clean"],
            f"PR #{pr_number} merge state is {pr['mergeStateStatus']}, not CLEAN; skipped auto-merge",
        )
    if successful_checks == 0:
        return finish(
            "blocked",
            ["checks_no_success"],
            f"PR #{pr_number} has no successful executed checks; skipped auto-merge",
        )
    if blocking_checks != 0:
        return finish(
            "blocked",
            ["checks_blocking"],
            f"PR #{pr_number} has non-green checks; skipped auto-merge",
        )

    try:
        thread_evidence = collect_threads(repo, slug, pr_number, runner)
    except EligibilityInputError:
        return finish(
            "indeterminate",
            ["review_threads_unavailable"],
            f"failed to inspect review threads for PR #{pr_number}; skipped auto-merge",
            retryable=True,
        )
    evidence["reviewThreads"] = thread_evidence
    if thread_evidence["unresolvedCount"] != 0:
        return finish(
            "blocked",
            ["review_threads_unresolved"],
            f"PR #{pr_number} has {thread_evidence['unresolvedCount']} unresolved review thread(s); skipped auto-merge",
        )

    return finish(
        "eligible",
        [],
        f"PR #{pr_number} is open, green, comment-clean, and matches local {branch}",
    )


def invalid_result(
    request: Mapping[str, Any] | None,
    reason: str,
    diagnostic: str,
    started_at: str,
    completed_at: str,
) -> dict[str, Any]:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "toolVersion": TOOL_VERSION,
        "status": "indeterminate",
        "reasonCodes": [reason],
        "retryable": False,
        "diagnostic": safe_text(diagnostic),
        "startedAt": started_at,
        "completedAt": completed_at,
        "repository": {
            "root": None,
            "githubSlug": None if request is None else request.get("githubRepository"),
        },
        "invocation": None if request is None else {
            "mode": request.get("mode"),
            "branch": request.get("branch"),
            "pullRequestNumber": request.get("pullRequestNumber"),
            "remote": request.get("remote"),
            "defaultBranch": request.get("defaultBranch"),
        },
        "head": {"startOid": None, "endOid": None, "remoteOid": None},
        "finishWork": {
            "required": False if request is None else request.get("finishWorkRequired"),
            "provided": False
            if request is None
            else request.get("finishWorkReceipt") is not None,
            "mode": None,
            "completionSubtype": None,
            "planningSubtype": None,
            "headOid": None,
            "matchesCurrentHead": False,
            "verified": False,
        },
        "pullRequest": None,
        "checks": {"items": [], "blockingCount": None, "successfulCount": None},
        "reviewThreads": {"pageCount": 0, "totalCount": 0, "unresolvedCount": None},
        "routedReview": {
            "status": "deferred",
            "reason": "unified receipt schema is owned by the routed-review integration task",
        },
    }


def render_shell(result: Mapping[str, Any]) -> str:
    pr = result.get("pullRequest")
    pr_number = pr.get("number") if isinstance(pr, Mapping) else ""
    pr_url = pr.get("url") if isinstance(pr, Mapping) else ""
    head = result.get("head")
    start_head = head.get("startOid") if isinstance(head, Mapping) else ""
    reasons = result.get("reasonCodes")
    reason_text = ",".join(str(item) for item in reasons) if isinstance(reasons, list) else ""
    fields = (
        result.get("status", "indeterminate"),
        reason_text,
        pr_number or "",
        pr_url or "",
        start_head or "",
        result.get("diagnostic", "eligibility result is unavailable"),
    )
    return FIELD_SEPARATOR.join(safe_text(item, limit=1000) for item in fields)


def render_json_shell(result: Mapping[str, Any]) -> str:
    """Render one compact JSON line plus the bounded shell receipt."""

    return "\n".join(
        (
            json.dumps(result, sort_keys=True, separators=(",", ":")),
            render_shell(result),
        )
    )


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate exact-head pull-request eligibility without mutation."
    )
    parser.add_argument("--input", type=Path, help="schema-versioned JSON request")
    parser.add_argument("--repo")
    parser.add_argument("--branch")
    parser.add_argument("--dependency-pr-number", type=int)
    parser.add_argument("--remote")
    parser.add_argument("--default-branch")
    parser.add_argument("--finish-work-receipt", type=Path)
    parser.add_argument("--github-repository")
    parser.add_argument(
        "--format", choices=("json", "shell", "json-shell"), default="json"
    )
    return parser.parse_args(argv)


def request_from_args(args: argparse.Namespace) -> object:
    direct_values = (
        args.repo,
        args.branch,
        args.dependency_pr_number,
        args.remote,
        args.default_branch,
        args.finish_work_receipt,
        args.github_repository,
    )
    if args.input is not None:
        if any(value is not None for value in direct_values):
            raise EligibilityInputError(
                "--input cannot be combined with direct eligibility arguments"
            )
        return load_request(args.input)
    missing = [
        name
        for name, value in (
            ("--remote", args.remote),
            ("--default-branch", args.default_branch),
        )
        if value is None
    ]
    if args.branch is None and args.dependency_pr_number is None:
        missing.append("--branch or --dependency-pr-number")
    if args.branch is not None and args.dependency_pr_number is not None:
        raise EligibilityInputError(
            "--branch and --dependency-pr-number are mutually exclusive"
        )
    if args.dependency_pr_number is not None and args.finish_work_receipt is not None:
        raise EligibilityInputError(
            "--finish-work-receipt is not valid with --dependency-pr-number"
        )
    if missing:
        raise EligibilityInputError(
            "direct eligibility evaluation requires " + ", ".join(missing)
        )
    dependency_mode = args.dependency_pr_number is not None
    return {
        "schemaVersion": SCHEMA_VERSION,
        "mode": "dependency-pr" if dependency_mode else "local-branch",
        "repository": args.repo or ".",
        "branch": None if dependency_mode else args.branch,
        "pullRequestNumber": args.dependency_pr_number,
        "remote": args.remote,
        "defaultBranch": args.default_branch,
        "finishWorkRequired": not dependency_mode,
        "finishWorkReceipt": None
        if dependency_mode or args.finish_work_receipt is None
        else load_finish_work_receipt(args.finish_work_receipt),
        "githubRepository": args.github_repository,
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    started_at = utc_timestamp()
    try:
        request = request_from_args(args)
        result = evaluate_request(request)
    except EligibilityInputError as exc:
        result = invalid_result(
            None, "invalid_input", str(exc), started_at, utc_timestamp()
        )
    if args.format == "shell":
        print(render_shell(result))
    elif args.format == "json-shell":
        print(render_json_shell(result))
    else:
        print(json.dumps(result, indent=2, sort_keys=True))
    return {"eligible": 0, "blocked": 1}.get(str(result.get("status")), 2)


if __name__ == "__main__":
    raise SystemExit(main())
