#!/usr/bin/env python3
"""Build a typed housekeeping result from delegated runtime evidence.

Schema version 1 finish-work evidence (explicit in-major migration record):

- The sole finish-work head evidence is the independently verified
  ``identity.finishWork`` object; its ``headOid`` is the exact merge head and
  ``verified`` proves it was recomputed, not caller-asserted.
- ``invocation.finishWorkReceiptProvided`` is a boolean provenance flag only,
  never a head value.
- The previously emitted ``invocation.finishWorkHead`` attestation is retired,
  not aliased. No shipped, documented, or tested consumer read it; the
  replacement is strictly more authoritative; and the retired caller-trusted
  ``--finish-work-head`` input is not restored.

The schema major stays 1 deliberately. Because the removed field had no
consumer and its replacement is independently verified, this is an explicit
documented in-major migration, not a silent contract break or a compatibility
alias. Decision record:
``.trellis/tasks/07-28-decide-housekeeping-result-schema-compatibility``.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from sd_ai_command_pack_lib import (  # noqa: E402
    build_environment_blocked_evidence,
    declare_verdict_domain,
)

SCHEMA_VERSION = 1
STATUS_SCHEMA_VERSION = 2
TOOL_VERSION = "1.0.0"
# Housekeeping's verdict vocabulary as an explicit extension of the shared core
# (A-077). ``indeterminate`` is this domain's only non-core verdict.
HOUSEKEEPING_VERDICTS = declare_verdict_domain(
    "housekeeping", {"clean", "blocked", "indeterminate", "failed"}, opt_out={"indeterminate"}
)
MAX_INPUT_BYTES = 2 * 1024 * 1024
MAX_MESSAGE_LENGTH = 1000
CODE_RE = re.compile(r"[a-z][a-z0-9_]{0,63}")
CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
INDETERMINATE_ANOMALY_CODES = frozenset(
    {
        "default_branch_unavailable",
        "github_repository_unavailable",
        "pull_request_state_indeterminate",
        "pull_request_unavailable",
        "remote_branch_unavailable",
        "status_unavailable",
    }
)

# Fixed, generic recovery instructions for the environment boundaries below.
# They request only the narrow authority to re-run the same cleanup after the
# operator clears the environment fault; they never carry a command to auto-run.
_GIT_REMOTE_RECOVERY = (
    "Resolve the git remote condition -- network access, authentication, or a "
    "stale lock -- then re-run housekeeping; the pull request is already merged "
    "and only this remote cleanup step retries."
)
_GIT_LOCAL_RECOVERY = (
    "Resolve the local git condition -- a stale index lock or a worktree still "
    "holding the branch -- then re-run housekeeping to delete the merged branch."
)
_KB_TARGET_RECOVERY = (
    "Ensure the .obsidian-kb target is a writable directory, then re-run; the KB "
    "refresh regenerates every entry and prunes stale ones without duplicating "
    "work."
)

# Anomaly codes that are an unambiguous environment or authority boundary on an
# owner-side cleanup or refresh step, mapped to the classification used to build
# a structured environment_blocked fragment. Only genuine environment boundaries
# appear here: repository-state and policy conditions (a missing ref, a
# non-fast-forward, a protected branch, a missing tool, an unmerged pull request)
# are intentionally excluded so a repository defect is never mislabeled as a
# retryable permission issue. mutationState is derived from what the failed
# operation can leave behind (an atomic single-ref delete leaves nothing; a
# multi-ref prune or a regenerable mirror can leave a recoverable partial state);
# every entry is retryable because it is post-merge cleanup or a read that the
# operator can safely repeat. Diagnostics are fixed and generic so no remote URL
# or raw filesystem error reaches the durable result.
ENVIRONMENT_BLOCK_CLASSIFICATION: dict[str, dict[str, str]] = {
    "remote_fetch_failed": {
        "boundary": "git-metadata",
        "operation": "fetch and prune the git remote",
        "checkpoint": "remote-fetch",
        "mutationState": "partial-recoverable",
        "recovery": _GIT_REMOTE_RECOVERY,
        "diagnostic": "The git remote fetch and prune was refused by the environment.",
    },
    "remote_prune_failed": {
        "boundary": "git-metadata",
        "operation": "prune stale remote-tracking refs",
        "checkpoint": "remote-prune",
        "mutationState": "partial-recoverable",
        "recovery": _GIT_REMOTE_RECOVERY,
        "diagnostic": "Pruning stale remote-tracking refs was refused by the environment.",
    },
    "local_branch_delete_failed": {
        "boundary": "git-metadata",
        "operation": "delete the merged local branch",
        "checkpoint": "local-branch-delete",
        "mutationState": "none",
        "recovery": _GIT_LOCAL_RECOVERY,
        "diagnostic": "Deleting the merged local branch was refused by the environment.",
    },
    "remote_branch_delete_failed": {
        "boundary": "git-metadata",
        "operation": "delete the merged remote branch",
        "checkpoint": "remote-branch-delete",
        "mutationState": "none",
        "recovery": _GIT_REMOTE_RECOVERY,
        "diagnostic": "Deleting the merged remote branch was refused by the environment.",
    },
    "kb_refresh_failed": {
        "boundary": "kb-target",
        "operation": "refresh the linked Obsidian KB copies",
        "checkpoint": "kb-refresh",
        "mutationState": "partial-recoverable",
        "recovery": _KB_TARGET_RECOVERY,
        "diagnostic": "The linked knowledge-base refresh could not write to its target.",
    },
}


class ResultInputError(ValueError):
    """Raised when delegated evidence cannot be composed safely."""


def load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        if path.is_symlink() or not path.is_file():
            raise ResultInputError(f"{label} must be a regular file")
        size = path.stat().st_size
        if size <= 0 or size > MAX_INPUT_BYTES:
            raise ResultInputError(
                f"{label} must contain between 1 and {MAX_INPUT_BYTES} bytes"
            )
        value = json.loads(path.read_text(encoding="utf-8", errors="strict"))
    except ResultInputError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ResultInputError(f"{label} is not readable JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ResultInputError(f"{label} must contain a JSON object")
    return value


def require_schema(
    value: Mapping[str, Any],
    label: str,
    *,
    expected: int = SCHEMA_VERSION,
) -> None:
    schema = value.get("schemaVersion")
    if isinstance(schema, bool) or not isinstance(schema, int) or schema != expected:
        raise ResultInputError(f"{label} schemaVersion must be {expected}")


def validate_status(value: dict[str, Any]) -> dict[str, Any]:
    require_schema(value, "status input", expected=STATUS_SCHEMA_VERSION)
    if value.get("mode") != "local":
        raise ResultInputError("status input mode must be local")
    for field in ("repository", "git", "trellis"):
        if not isinstance(value.get(field), dict):
            raise ResultInputError(f"status input {field} must be an object")
    anomalies = value.get("anomalies")
    if not isinstance(anomalies, list) or not all(
        isinstance(item, str) for item in anomalies
    ):
        raise ResultInputError("status input anomalies must be an array of strings")
    return value


def validate_eligibility(value: dict[str, Any]) -> dict[str, Any]:
    require_schema(value, "eligibility input")
    if value.get("status") not in {"eligible", "blocked", "indeterminate"}:
        raise ResultInputError(
            "eligibility input status must be eligible, blocked, or indeterminate"
        )
    reasons = value.get("reasonCodes")
    if not isinstance(reasons, list) or not all(
        isinstance(item, str) and CODE_RE.fullmatch(item) for item in reasons
    ):
        raise ResultInputError(
            "eligibility input reasonCodes must contain stable lowercase codes"
        )
    return value


def validate_event(raw: Sequence[str], label: str) -> dict[str, str]:
    if len(raw) != 2:
        raise ResultInputError(f"{label} must contain a code and message")
    code, message = raw
    if not CODE_RE.fullmatch(code):
        raise ResultInputError(f"{label} code is invalid: {code}")
    if (
        not message.strip()
        or len(message) > MAX_MESSAGE_LENGTH
        or CONTROL_RE.search(message)
    ):
        raise ResultInputError(f"{label} message is invalid for {code}")
    return {"code": code, "message": message}


def deduplicate(values: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def classify_outcome(
    *,
    status: Mapping[str, Any] | None,
    status_exit: int,
    status_error: Mapping[str, str] | None,
    eligibility: Mapping[str, Any] | None,
    anomalies: Sequence[Mapping[str, str]],
) -> dict[str, Any]:
    status_anomalies = [] if status is None else status.get("anomalies", [])
    event_codes = [item["code"] for item in anomalies]
    eligibility_status = None if eligibility is None else eligibility.get("status")
    eligibility_reasons = (
        [] if eligibility is None else list(eligibility.get("reasonCodes", []))
    )

    if status_error is not None:
        outcome = "failed"
        reasons = [status_error["code"]]
    elif status_exit not in {0, 1}:
        outcome = "failed"
        reasons = ["status_collection_failed"]
    elif eligibility_status == "indeterminate" or any(
        code in INDETERMINATE_ANOMALY_CODES for code in event_codes
    ):
        outcome = "indeterminate"
        reasons = eligibility_reasons + event_codes
    elif eligibility_status == "blocked" or event_codes or status_anomalies:
        outcome = "blocked"
        reasons = eligibility_reasons + event_codes
        if status_anomalies and not event_codes:
            reasons.append("status_anomalies")
    elif status_exit == 1:
        outcome = "failed"
        reasons = ["status_collection_failed"]
    else:
        outcome = "clean"
        reasons = []
    return {
        "verdict": outcome,
        # Deprecated alias of ``verdict`` (A-077). ``result["status"]`` is the
        # embedded sd-status document; keeping an enum named ``status`` inside
        # ``outcome`` made one document carry two meanings of ``status``. Kept
        # additively for the dual-emit window and dropped in the release named
        # by ``DEPRECATED_PAYLOAD_KEYS`` (removed_version 0.66.0).
        "status": outcome,
        "reasonCodes": deduplicate(reasons),
    }


def build_environment_blocks(
    anomalies: Sequence[Mapping[str, str]],
) -> list[dict[str, Any]]:
    """Return an environment_blocked fragment for each environment-boundary anomaly.

    Classification is a deterministic lookup on the control-flow-selected anomaly
    code, never a heuristic read of the message text: only codes in
    ENVIRONMENT_BLOCK_CLASSIFICATION produce a fragment. The fragment is built
    owner-side through the shared composer, so it is validated and redacted the
    same way every other producer's is. The blocks are additive evidence and do
    not affect outcome classification.
    """

    blocks: list[dict[str, Any]] = []
    for anomaly in anomalies:
        spec = ENVIRONMENT_BLOCK_CLASSIFICATION.get(anomaly["code"])
        if spec is None:
            continue
        blocks.append(
            build_environment_blocked_evidence(
                boundary=spec["boundary"],
                operation=spec["operation"],
                checkpoint=spec["checkpoint"],
                mutation_state=spec["mutationState"],
                retryable=True,
                recovery_action={"kind": "skill", "instruction": spec["recovery"]},
                diagnostic=spec["diagnostic"],
            )
        )
    return blocks


def build_result(args: argparse.Namespace) -> dict[str, Any]:
    status_error = (
        None
        if args.status_error is None
        else validate_event(args.status_error, "status error")
    )
    if (args.status_input is None) == (status_error is None):
        raise ResultInputError(
            "exactly one of status input or status error must be provided"
        )
    status = (
        None
        if args.status_input is None
        else validate_status(load_json(args.status_input, "status input"))
    )
    eligibility = (
        None
        if args.eligibility_input is None
        else validate_eligibility(
            load_json(args.eligibility_input, "eligibility input")
        )
    )
    actions = [validate_event(item, "action") for item in args.action]
    anomalies = [validate_event(item, "anomaly") for item in args.anomaly]
    status_repo = (
        {
            "path": str(args.repository),
            "name": args.repository.name,
            "github": None,
        }
        if status is None
        else status["repository"]
    )
    status_git = {} if status is None else status["git"]
    pull_request = None if eligibility is None else eligibility.get("pullRequest")
    heads = None if eligibility is None else eligibility.get("head")
    finish_work = None if eligibility is None else eligibility.get("finishWork")
    return {
        "schemaVersion": SCHEMA_VERSION,
        "toolVersion": TOOL_VERSION,
        "kind": "housekeeping",
        "repository": status_repo,
        "invocation": {
            "remote": args.remote,
            "mergeStrategy": args.merge_strategy,
            "dryRun": args.dry_run,
            "keepRemoteBranch": args.keep_remote_branch,
            "dependencyPullRequestNumber": args.dependency_pr_number,
            "finishWorkReceiptProvided": bool(
                isinstance(finish_work, Mapping) and finish_work.get("provided")
            ),
        },
        "identity": {
            "startBranch": args.start_branch,
            "defaultBranch": args.default_branch,
            "currentBranch": status_git.get("branch"),
            "pullRequest": pull_request,
            "heads": heads,
            "finishWork": finish_work,
        },
        "eligibility": eligibility,
        "actions": actions,
        "anomalies": anomalies,
        "environmentBlocks": build_environment_blocks(anomalies),
        "statusError": status_error,
        "status": status,
        "outcome": classify_outcome(
            status=status,
            status_exit=args.status_exit,
            status_error=status_error,
            eligibility=eligibility,
            anomalies=anomalies,
        ),
    }


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compose schema-versioned housekeeping runtime evidence."
    )
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--status-input", type=Path)
    parser.add_argument("--status-error", nargs=2)
    parser.add_argument("--status-exit", type=int, required=True)
    parser.add_argument("--eligibility-input", type=Path)
    parser.add_argument("--start-branch")
    parser.add_argument("--default-branch")
    parser.add_argument("--remote", required=True)
    parser.add_argument(
        "--merge-strategy", choices=("merge", "squash", "rebase"), required=True
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--keep-remote-branch", action="store_true")
    parser.add_argument("--dependency-pr-number", type=int)
    parser.add_argument("--action", action="append", nargs=2, default=[])
    parser.add_argument("--anomaly", action="append", nargs=2, default=[])
    args = parser.parse_args(argv)
    if args.status_exit < 0 or args.status_exit > 255:
        parser.error("--status-exit must be between 0 and 255")
    if args.dependency_pr_number is not None and args.dependency_pr_number <= 0:
        parser.error("--dependency-pr-number must be positive")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        result = build_result(args)
    except ResultInputError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
