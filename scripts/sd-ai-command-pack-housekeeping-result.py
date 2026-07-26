#!/usr/bin/env python3
"""Build a typed housekeeping result from delegated runtime evidence."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
STATUS_SCHEMA_VERSION = 2
TOOL_VERSION = "1.0.0"
MAX_INPUT_BYTES = 2 * 1024 * 1024
MAX_MESSAGE_LENGTH = 1000
CODE_RE = re.compile(r"[a-z][a-z0-9_]{0,63}")
CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
INDETERMINATE_ANOMALY_CODES = frozenset(
    {
        "default_branch_unavailable",
        "github_repository_unavailable",
        "pull_request_unavailable",
        "remote_branch_unavailable",
        "status_unavailable",
    }
)


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
        value = json.loads(path.read_text(encoding="utf-8"))
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
    return {"status": outcome, "reasonCodes": deduplicate(reasons)}


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
