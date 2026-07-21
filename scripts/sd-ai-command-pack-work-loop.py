#!/usr/bin/env python3
"""Manage resumable, user-local state for autonomous SD work loops."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import urlsplit, urlunsplit

SCHEMA_VERSION = 1
MAX_LEDGER_BYTES = 64 * 1024
MAX_HISTORY = 20
MAX_NOTES = 50
DEFAULT_STALE_LOCK_SECONDS = 15 * 60
TERMINAL_LOCK_NAME = "terminal-reconcile.lock.json"
STATE_HOME_ENV = "SD_AI_COMMAND_PACK_STATE_HOME"
FOCUS_FIELDS = frozenset({"priority", "package", "task", "status", "scope"})
FOCUS_PREFIX_RE = re.compile(r"^([A-Za-z][A-Za-z0-9_-]*):(.*)$", re.DOTALL)
WORD_RE = re.compile(r"[A-Za-z0-9_.-]+")
COMMIT_RE = re.compile(r"[0-9a-f]{40,64}")
SECRET_KEY_RE = re.compile(
    r"(?:token|secret|password|credential|api[_-]?key)", re.IGNORECASE
)
STATUSES = frozenset({"active", "paused", "stopped", "completed"})
MODES = frozenset({"backlog", "designs"})
SELECTORS = frozenset({"all", "needs-design"})
UNTIL_VALUES = frozenset({"design", "merge"})
CURRENT_FIELD_ORDER = (
    "task",
    "branch",
    "head",
    "baseBranch",
    "prNumber",
    "prUrl",
    "lastShippedSha",
)
CURRENT_FIELDS = frozenset(CURRENT_FIELD_ORDER)
STABLE_CURRENT_FIELDS = ("task", "baseBranch")
TRANSITION_CURRENT_FIELDS = frozenset(STABLE_CURRENT_FIELDS)
ACTIVE_EVIDENCE_PHASES = frozenset(
    {"selected", "planning", "implementing", "validating", "shipping", "followups"}
)
MERGE_EVIDENCE_PHASES = frozenset({"shipping", "followups"})
COUNTER_FIELDS = frozenset(
    {
        "completed",
        "parked",
        "blocked",
        "skipped",
        "failures",
        "mergedPrs",
        "reviewRounds",
        "ciRetries",
    }
)
LIFECYCLE_PHASES = (
    "inventory",
    "selected",
    "planning",
    "implementing",
    "validating",
    "shipping",
    "followups",
    "complete",
)
PHASES = (
    *LIFECYCLE_PHASES,
    "checkpoint",
    "stopped",
)
PHASE_ORDER = {phase: index for index, phase in enumerate(PHASES)}
LIFECYCLE_PHASE_ORDER = {
    phase: index for index, phase in enumerate(LIFECYCLE_PHASES)
}
RECOVERY_CHECKPOINT_STATES = frozenset({"ready", "paused", "blocked"})
LEGAL_TRANSITIONS: dict[str, frozenset[str]] = {
    "inventory": frozenset({"selected", "checkpoint", "stopped"}),
    "selected": frozenset(
        {"planning", "implementing", "checkpoint", "stopped"}
    ),
    "planning": frozenset({"implementing", "checkpoint", "stopped"}),
    "implementing": frozenset({"validating", "checkpoint", "stopped"}),
    "validating": frozenset(
        {"implementing", "shipping", "checkpoint", "stopped"}
    ),
    "shipping": frozenset({"validating", "followups", "checkpoint", "stopped"}),
    "followups": frozenset({"complete", "checkpoint", "stopped"}),
    "complete": frozenset({"inventory", "checkpoint", "stopped"}),
    "checkpoint": frozenset(set(PHASES) - {"selected", "complete", "checkpoint"}),
    "stopped": frozenset(),
}
CONTEXT_SIGNALS = frozenset(
    {"compaction", "continuation-summary", "truncated-output", "contradiction", "duplicate-side-effect"}
)


class WorkLoopError(ValueError):
    """Raised when loop state is invalid or unsafe to mutate."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def parse_utc(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def compact_text(value: object, *, limit: int = 300) -> str:
    text = " ".join(str(value).split())
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."


def run_git(repo: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *args],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="strict",
            timeout=20,
        )
    except (OSError, UnicodeError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def resolve_repository(path: Path) -> Path:
    candidate = path.expanduser()
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    probe = candidate if candidate.is_dir() else candidate.parent
    resolved = run_git(probe, "rev-parse", "--show-toplevel")
    if not resolved:
        raise WorkLoopError(f"not a Git repository: {path}")
    try:
        return Path(resolved).resolve(strict=True)
    except OSError as error:
        raise WorkLoopError(f"cannot resolve repository root: {error}") from error


def canonical_remote(value: str | None) -> str:
    remote = (value or "").strip()
    if not remote:
        return ""
    windows_drive_path = re.match(r"^[A-Za-z]:[\\/]", remote)
    if windows_drive_path:
        normalized_windows = PureWindowsPath(remote).as_posix().casefold()
        return normalized_windows.removesuffix(".git").rstrip("/")
    scp_match = re.fullmatch(r"([^@\s]+@)?([^:/\s]+):(.+)", remote)
    if scp_match and "://" not in remote:
        user = scp_match.group(1) or ""
        remote = f"ssh://{user}{scp_match.group(2)}/{scp_match.group(3)}"
    split = urlsplit(remote)
    if split.scheme and split.netloc:
        url_path = split.path.rstrip("/")
        if url_path.endswith(".git"):
            url_path = url_path[:-4]
        credential_free_netloc = split.netloc.rsplit("@", 1)[-1]
        return urlunsplit(
            (split.scheme.lower(), credential_free_netloc.lower(), url_path, "", "")
        )
    local_path = Path(remote).expanduser()
    try:
        normalized = local_path.resolve(strict=False)
    except OSError:
        normalized = local_path.absolute()
    return normalized.as_posix().removesuffix(".git").rstrip("/")


def repository_identity(repo: Path, *, remote: str | None = None) -> dict[str, str]:
    root = resolve_repository(repo)
    canonical = canonical_remote(
        remote if remote is not None else run_git(root, "remote", "get-url", "origin")
    )
    normalized_root = os.path.normcase(str(root))
    digest = hashlib.sha256(
        f"{normalized_root}\n{canonical}".encode("utf-8")
    ).hexdigest()
    label = root.name
    if canonical:
        label = canonical.rstrip("/").rsplit("/", 1)[-1] or label
    return {
        "root": str(root),
        "remote": canonical,
        "digest": digest,
        "label": compact_text(label, limit=120),
    }


def resolve_state_root(
    *,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
    os_name: str | None = None,
) -> Path:
    env = os.environ if environ is None else environ
    override = env.get(STATE_HOME_ENV, "").strip()
    if override:
        path = Path(override).expanduser()
        if not path.is_absolute():
            raise WorkLoopError(f"{STATE_HOME_ENV} must be an absolute path")
        return path
    xdg = env.get("XDG_STATE_HOME", "").strip()
    if xdg:
        path = Path(xdg).expanduser()
        if path.is_absolute():
            return path / "sd-ai-command-pack"
    platform_name = os.name if os_name is None else os_name
    if platform_name == "nt":
        local_app_data = env.get("LOCALAPPDATA", "").strip()
        if local_app_data:
            windows_path = PureWindowsPath(local_app_data)
            if windows_path.is_absolute():
                # Path uses Windows semantics on Windows. Normalizing separators
                # also keeps os_name-injected portability tests deterministic.
                path = Path(str(windows_path).replace("\\", "/"))
                return path / "sd-ai-command-pack" / "state"
    resolved_home = (home or Path.home()).expanduser()
    if not resolved_home.is_absolute():
        raise WorkLoopError("home directory must resolve to an absolute path")
    return resolved_home / ".local" / "state" / "sd-ai-command-pack"


def state_paths(identity: Mapping[str, str], state_root: Path) -> tuple[Path, Path]:
    directory = state_root / "work-loops" / identity["digest"]
    return directory / "state.json", directory / "lock.json"


def ensure_private_directory(path: Path) -> None:
    if path.is_symlink():
        raise WorkLoopError(f"state directory must not be a symlink: {path}")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.is_symlink() or not path.is_dir():
        raise WorkLoopError(f"state directory is unusable: {path}")
    try:
        path.chmod(0o700)
    except OSError:
        # Permission tightening is best-effort on filesystems without chmod support.
        pass


def _reject_secret_keys(value: object, *, path: str = "state") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if SECRET_KEY_RE.search(str(key)):
                raise WorkLoopError(f"secret-like key is not allowed in loop state: {path}.{key}")
            _reject_secret_keys(item, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_secret_keys(item, path=f"{path}[{index}]")


def _json_payload(value: Mapping[str, Any]) -> str:
    try:
        return json.dumps(value, indent=2, sort_keys=True) + "\n"
    except (TypeError, ValueError) as error:
        raise WorkLoopError(f"work-loop state is not JSON serializable: {error}") from error


def _normalized_pr_url(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip() or len(value) > 500:
        return None
    try:
        split = urlsplit(value.strip())
        hostname = split.hostname
        port_number = split.port
        username = split.username
        password = split.password
    except ValueError:
        return None
    if (
        split.scheme not in {"http", "https"}
        or not hostname
        or username is not None
        or password is not None
        or split.query
        or split.fragment
    ):
        return None
    port = f":{port_number}" if port_number is not None else ""
    netloc = f"{hostname.casefold()}{port}"
    path = split.path.rstrip("/")
    if not path:
        return None
    return urlunsplit((split.scheme.casefold(), netloc, path, "", ""))


def _valid_pr_record(value: object) -> bool:
    if not isinstance(value, dict) or set(value) != {
        "prNumber",
        "prUrl",
        "head",
        "mergeCommit",
    }:
        return False
    number = value.get("prNumber")
    url = value.get("prUrl")
    head = value.get("head")
    merge_commit = value.get("mergeCommit")
    if (
        not isinstance(number, int)
        or isinstance(number, bool)
        or number < 1
        or not isinstance(url, str)
        or _normalized_pr_url(url) != url
        or not isinstance(head, str)
        or COMMIT_RE.fullmatch(head) is None
        or not isinstance(merge_commit, str)
        or COMMIT_RE.fullmatch(merge_commit) is None
    ):
        return False
    final_component = urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1]
    return final_component.isdigit() and int(final_component) == number


def normalized_pr_record(
    *, pr_number: object, pr_url: object, head: object, merge_commit: object
) -> dict[str, Any]:
    normalized_url = _normalized_pr_url(pr_url)
    record = {
        "prNumber": pr_number,
        "prUrl": normalized_url,
        "head": head,
        "mergeCommit": merge_commit,
    }
    if not _valid_pr_record(record):
        raise WorkLoopError(
            "PR evidence must contain a positive number, matching canonical URL, "
            "and full lowercase head and merge commit IDs"
        )
    return record


def validate_terminal_reconciliation(value: object) -> None:
    if not isinstance(value, dict) or set(value) != {
        "status",
        "reconciledAt",
        "archivedTask",
        "taskId",
        "delivery",
        "bookkeeping",
        "observed",
    }:
        raise WorkLoopError("work-loop terminal reconciliation is malformed")
    if value.get("status") != "verified" or parse_utc(value.get("reconciledAt")) is None:
        raise WorkLoopError("work-loop terminal reconciliation is malformed")
    for field, limit in (("archivedTask", 300), ("taskId", 200)):
        item = value.get(field)
        if not isinstance(item, str) or not item.strip() or len(item) > limit:
            raise WorkLoopError("work-loop terminal reconciliation is malformed")
    if not _valid_pr_record(value.get("delivery")):
        raise WorkLoopError("work-loop terminal reconciliation is malformed")
    bookkeeping = value.get("bookkeeping")
    if bookkeeping is not None and not _valid_pr_record(bookkeeping):
        raise WorkLoopError("work-loop terminal reconciliation is malformed")
    observed = value.get("observed")
    if (
        not isinstance(observed, dict)
        or set(observed) != {"branch", "head"}
        or not isinstance(observed.get("branch"), str)
        or not observed["branch"].strip()
        or len(observed["branch"]) > 200
        or not isinstance(observed.get("head"), str)
        or COMMIT_RE.fullmatch(observed["head"]) is None
    ):
        raise WorkLoopError("work-loop terminal reconciliation is malformed")


def validate_state(state: Mapping[str, Any]) -> None:
    if state.get("schemaVersion") != SCHEMA_VERSION:
        raise WorkLoopError(
            f"unsupported work-loop schema: {state.get('schemaVersion')!r}"
        )
    if not isinstance(state.get("runId"), str) or not state["runId"]:
        raise WorkLoopError("work-loop state has no runId")
    repository = state.get("repository")
    if not isinstance(repository, dict) or not all(
        isinstance(repository.get(key), str) and repository[key]
        for key in ("root", "digest", "label")
    ):
        raise WorkLoopError("work-loop repository identity is malformed")
    if not isinstance(state.get("status"), str) or state["status"] not in STATUSES:
        raise WorkLoopError(f"invalid work-loop status: {state.get('status')!r}")
    if not isinstance(state.get("mode"), str) or state["mode"] not in MODES:
        raise WorkLoopError(f"invalid work-loop mode: {state.get('mode')!r}")
    if (
        not isinstance(state.get("selector"), str)
        or state["selector"] not in SELECTORS
    ):
        raise WorkLoopError(f"invalid work-loop selector: {state.get('selector')!r}")
    if not isinstance(state.get("until"), str) or state["until"] not in UNTIL_VALUES:
        raise WorkLoopError(f"invalid work-loop until value: {state.get('until')!r}")
    if not isinstance(state.get("phase"), str) or state["phase"] not in PHASE_ORDER:
        raise WorkLoopError(f"invalid work-loop phase: {state.get('phase')!r}")
    if not isinstance(state.get("iteration"), int) or state["iteration"] < 1:
        raise WorkLoopError("work-loop iteration must be a positive integer")
    focus = state.get("focus")
    if (
        not isinstance(focus, dict)
        or not isinstance(focus.get("mode"), str)
        or focus["mode"] not in {"none", "prefer", "only"}
    ):
        raise WorkLoopError("work-loop focus is malformed")
    originals = focus.get("original")
    selectors = focus.get("selectors")
    if (
        not isinstance(originals, list)
        or any(not isinstance(item, str) or not item for item in originals)
        or not isinstance(selectors, list)
        or len(originals) != len(selectors)
    ):
        raise WorkLoopError("work-loop focus is malformed")
    if (focus["mode"] == "none") != (not originals):
        raise WorkLoopError("work-loop focus mode does not match its selectors")
    for selector in selectors:
        if not isinstance(selector, dict):
            raise WorkLoopError("work-loop focus selector is malformed")
        kind = selector.get("kind")
        field = selector.get("field")
        value = selector.get("value")
        if (
            not isinstance(kind, str)
            or kind not in {"natural", "structured"}
            or not isinstance(field, str)
            or not isinstance(value, str)
            or not value
            or (kind == "natural" and field != "text")
            or (kind == "structured" and field not in FOCUS_FIELDS)
        ):
            raise WorkLoopError("work-loop focus selector is malformed")
    current = state.get("current")
    if not isinstance(current, dict) or not CURRENT_FIELDS.issubset(current):
        raise WorkLoopError("work-loop current state is malformed")
    if any(
        value is not None
        and (not isinstance(value, str) or not value.strip())
        for key, value in current.items()
        if key != "prNumber"
    ) or (
        current.get("prNumber") is not None
        and (
            not isinstance(current["prNumber"], int)
            or isinstance(current["prNumber"], bool)
            or current["prNumber"] < 1
        )
    ):
        raise WorkLoopError("work-loop current state is malformed")
    counters = state.get("counters")
    if (
        not isinstance(counters, dict)
        or not COUNTER_FIELDS.issubset(counters)
        or any(
            not isinstance(value, int) or isinstance(value, bool) or value < 0
            for value in counters.values()
        )
    ):
        raise WorkLoopError("work-loop counters are malformed")
    context_health = state.get("contextHealth")
    if (
        not isinstance(context_health, dict)
        or context_health.get("level") not in {"green", "amber", "red"}
        or not isinstance(context_health.get("epoch"), int)
        or isinstance(context_health.get("epoch"), bool)
        or context_health["epoch"] < 0
        or not isinstance(context_health.get("reasons"), list)
        or any(not isinstance(item, str) for item in context_health["reasons"])
    ):
        raise WorkLoopError("work-loop context health is malformed")
    checkpoint = state.get("checkpoint")
    resume_phase = checkpoint.get("resumePhase") if isinstance(checkpoint, dict) else None
    if (
        not isinstance(checkpoint, dict)
        or not {"state", "target", "reason"}.issubset(checkpoint)
        or not isinstance(checkpoint.get("state"), str)
        or checkpoint["state"]
        not in {"none", "ready", "paused", "blocked", "stopped", "completed"}
        or checkpoint.get("target") is not None
        and not isinstance(checkpoint["target"], str)
        or checkpoint.get("reason") is not None
        and not isinstance(checkpoint["reason"], str)
        or resume_phase is not None
        and (
            not isinstance(resume_phase, str)
            or resume_phase not in LIFECYCLE_PHASE_ORDER
        )
    ):
        raise WorkLoopError("work-loop checkpoint is malformed")
    for timestamp_key in ("createdAt", "updatedAt", "heartbeatAt"):
        if parse_utc(state.get(timestamp_key)) is None:
            raise WorkLoopError(f"work-loop {timestamp_key} timestamp is malformed")
    for list_key in ("iterations", "decisions", "followups"):
        if not isinstance(state.get(list_key), list):
            raise WorkLoopError(f"work-loop {list_key} history is malformed")
    if state.get("stopReason") is not None and not isinstance(
        state["stopReason"], str
    ):
        raise WorkLoopError("work-loop stop reason is malformed")
    if state.get("terminalReconciliation") is not None:
        validate_terminal_reconciliation(state["terminalReconciliation"])
        if state["status"] not in {"stopped", "completed"} or state["phase"] != "stopped":
            raise WorkLoopError(
                "terminal reconciliation requires terminal work-loop state"
            )
    _reject_secret_keys(state)
    encoded = _json_payload(state).encode("utf-8")
    if len(encoded) > MAX_LEDGER_BYTES:
        raise WorkLoopError(
            f"work-loop state exceeds {MAX_LEDGER_BYTES} bytes; compact it before writing"
        )


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8", errors="strict"))
    except FileNotFoundError:
        raise WorkLoopError(f"work-loop state does not exist: {path}") from None
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise WorkLoopError(f"cannot read work-loop state {path}: {error}") from error
    if not isinstance(value, dict):
        raise WorkLoopError(f"work-loop state must be a JSON object: {path}")
    return value


def atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    ensure_private_directory(path.parent)
    payload = _json_payload(value)
    if len(payload.encode("utf-8")) > MAX_LEDGER_BYTES:
        raise WorkLoopError(f"refusing to write oversized work-loop state: {path}")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", errors="strict") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        try:
            path.chmod(0o600)
        except OSError:
            # The atomic write succeeded; unsupported chmod must not discard it.
            pass
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            # Cleanup failures must not hide the original write failure.
            pass
        try:
            temporary.unlink()
        except OSError:
            # Cleanup failures must not hide the original write failure.
            pass
        raise


def normalize_focus(
    *,
    bare: str | None = None,
    preferred: Sequence[str] = (),
    only: Sequence[str] = (),
) -> dict[str, Any]:
    bare_value = (bare or "").strip()
    preferred_values = [value.strip() for value in preferred]
    only_values = [value.strip() for value in only]
    if any(not value for value in (*preferred_values, *only_values)):
        raise WorkLoopError("focus expressions must not be empty")
    if bare_value and (preferred_values or only_values):
        raise WorkLoopError("bare focus text cannot be mixed with focus= or focus-only=")
    if preferred_values and only_values:
        raise WorkLoopError("focus and focus-only are mutually exclusive")
    originals = [bare_value] if bare_value else preferred_values or only_values
    mode = "only" if only_values else "prefer" if originals else "none"
    normalized: list[dict[str, str]] = []
    for original in originals:
        match = FOCUS_PREFIX_RE.fullmatch(original)
        if match:
            field = match.group(1).casefold()
            value = match.group(2).strip()
            if field not in FOCUS_FIELDS:
                raise WorkLoopError(f"unknown structured focus selector: {field}")
            if not value:
                raise WorkLoopError(f"structured focus selector {field}: needs a value")
            normalized.append(
                {
                    "original": original,
                    "kind": "structured",
                    "field": field,
                    "value": value.casefold(),
                }
            )
        else:
            normalized.append(
                {
                    "original": original,
                    "kind": "natural",
                    "field": "text",
                    "value": " ".join(original.casefold().split()),
                }
            )
    return {"mode": mode, "original": originals, "selectors": normalized}


def _candidate_values(candidate: Mapping[str, Any], field: str) -> list[str]:
    aliases = {
        "task": ("id", "task", "slug"),
        "priority": ("priority",),
        "package": ("package",),
        "status": ("status",),
        "scope": ("scope",),
    }
    values: list[str] = []
    for key in aliases.get(field, (field,)):
        value = candidate.get(key)
        if isinstance(value, str) and value.strip():
            values.append(value.strip())
    return values


def focus_match(
    candidate: Mapping[str, Any], selector: Mapping[str, Any]
) -> tuple[bool, list[str]]:
    if selector.get("kind") == "structured":
        field = selector["field"]
        wanted = selector["value"]
        for value in _candidate_values(candidate, field):
            if value.casefold() == wanted:
                return True, [f"{field} equals {compact_text(value, limit=100)}"]
        return False, []

    phrase = selector["value"]
    fields = (
        "title",
        "description",
        "prd",
        "package",
        "scope",
        "relatedFiles",
        "metadata",
    )
    searchable: list[tuple[str, str]] = []
    for field in fields:
        candidate_value = candidate.get(field)
        if isinstance(candidate_value, str):
            searchable.append((field, candidate_value))
        elif isinstance(candidate_value, list):
            searchable.extend((field, str(item)) for item in candidate_value)
        elif isinstance(candidate_value, dict):
            searchable.extend(
                (field, f"{key} {item}") for key, item in candidate_value.items()
            )
    for field, value in searchable:
        if phrase and phrase in value.casefold():
            return True, [f"{field} contains {compact_text(phrase, limit=100)}"]
    terms = [term.casefold() for term in WORD_RE.findall(phrase)]
    if terms:
        combined = " ".join(value.casefold() for _field, value in searchable)
        if all(term in combined for term in terms):
            evidence = [
                f"{field} matches {compact_text(term, limit=60)}"
                for term in terms
                for field, value in searchable
                if term in value.casefold()
            ]
            return True, evidence[:5]
    return False, []


def base_candidate_key(candidate: Mapping[str, Any]) -> tuple[Any, ...]:
    status_order = {"in_progress": 0, "planning": 1}
    priority_order = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
    artifacts_complete = bool(candidate.get("artifactsComplete"))
    return (
        status_order.get(str(candidate.get("status")), 9),
        priority_order.get(str(candidate.get("priority")), 9),
        0 if artifacts_complete else 1,
        str(candidate.get("createdAt") or "9999-99-99"),
        str(candidate.get("id") or candidate.get("task") or "").casefold(),
    )


def rank_candidates(
    candidates: Sequence[Mapping[str, Any]], focus: Mapping[str, Any]
) -> list[dict[str, Any]]:
    ranked: list[dict[str, Any]] = []
    selectors = focus.get("selectors")
    if not isinstance(selectors, list):
        raise WorkLoopError("focus selectors must be a list")
    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            raise WorkLoopError("every task candidate must be an object")
        band: int | None = None
        evidence: list[str] = []
        match_kind = "none"
        for index, selector in enumerate(selectors):
            if not isinstance(selector, Mapping):
                raise WorkLoopError("focus selector is malformed")
            matched, selector_evidence = focus_match(candidate, selector)
            if matched:
                band = index
                evidence = selector_evidence
                match_kind = str(selector.get("kind"))
                break
        if focus.get("mode") == "only" and band is None:
            continue
        item = dict(candidate)
        item["focusMatch"] = band is not None
        item["focusBand"] = band
        item["focusEvidence"] = evidence
        item["focusMatchKind"] = match_kind
        ranked.append(item)
    ranked.sort(
        key=lambda item: (
            item["focusBand"] if item["focusBand"] is not None else len(selectors) + 1,
            0 if item["focusMatchKind"] == "structured" else 1,
            base_candidate_key(item),
        )
    )
    return ranked


def new_state(
    identity: Mapping[str, str],
    *,
    mode: str,
    selector: str,
    focus: Mapping[str, Any],
    until: str,
    run_id: str | None = None,
) -> dict[str, Any]:
    now = utc_now()
    state: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "runId": run_id or uuid.uuid4().hex,
        "repository": dict(identity),
        "mode": mode,
        "selector": selector,
        "focus": dict(focus),
        "until": until,
        "status": "active",
        "phase": "inventory",
        "iteration": 1,
        "createdAt": now,
        "updatedAt": now,
        "heartbeatAt": now,
        "contextHealth": {"level": "green", "epoch": 0, "reasons": []},
        "current": {
            "task": None,
            "branch": None,
            "head": None,
            "baseBranch": None,
            "prNumber": None,
            "prUrl": None,
            "lastShippedSha": None,
        },
        "counters": {
            "completed": 0,
            "parked": 0,
            "blocked": 0,
            "skipped": 0,
            "failures": 0,
            "mergedPrs": 0,
            "reviewRounds": 0,
            "ciRetries": 0,
        },
        "iterations": [],
        "decisions": [],
        "followups": [],
        "checkpoint": {
            "state": "none",
            "target": None,
            "reason": None,
            "resumePhase": None,
        },
        "stopReason": None,
    }
    validate_state(state)
    return state


def lock_payload(state: Mapping[str, Any]) -> dict[str, Any]:
    now = utc_now()
    return {
        "schemaVersion": SCHEMA_VERSION,
        "runId": state["runId"],
        "repositoryDigest": state["repository"]["digest"],
        "pid": os.getpid(),
        "hostname": socket.gethostname(),
        "acquiredAt": now,
        "heartbeatAt": now,
    }


def validate_lock(lock: Mapping[str, Any]) -> None:
    if lock.get("schemaVersion") != SCHEMA_VERSION:
        raise WorkLoopError("work-loop lock schema is malformed")
    if not isinstance(lock.get("runId"), str) or not lock["runId"]:
        raise WorkLoopError("work-loop lock runId is malformed")
    if (
        not isinstance(lock.get("repositoryDigest"), str)
        or not lock["repositoryDigest"]
    ):
        raise WorkLoopError("work-loop lock repository digest is malformed")
    if (
        not isinstance(lock.get("pid"), int)
        or isinstance(lock.get("pid"), bool)
        or lock["pid"] <= 0
    ):
        raise WorkLoopError("work-loop lock pid is malformed")
    if not isinstance(lock.get("hostname"), str) or not lock["hostname"]:
        raise WorkLoopError("work-loop lock hostname is malformed")
    for timestamp_key in ("acquiredAt", "heartbeatAt"):
        if parse_utc(lock.get(timestamp_key)) is None:
            raise WorkLoopError(f"work-loop lock {timestamp_key} is malformed")


def process_alive(pid: object) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OSError):
        return True
    return True


def lock_is_stale(
    lock: Mapping[str, Any], *, stale_after: int = DEFAULT_STALE_LOCK_SECONDS
) -> bool:
    validate_lock(lock)
    heartbeat = parse_utc(lock.get("heartbeatAt"))
    if heartbeat is None:
        raise WorkLoopError("work-loop lock heartbeatAt is malformed")
    age = (datetime.now(timezone.utc) - heartbeat).total_seconds()
    if age <= stale_after:
        return False
    if lock.get("hostname") == socket.gethostname() and process_alive(lock.get("pid")):
        return False
    return True


def acquire_lock(
    lock_path: Path,
    state: Mapping[str, Any],
    *,
    recover_stale: bool = False,
    stale_after: int = DEFAULT_STALE_LOCK_SECONDS,
) -> None:
    ensure_private_directory(lock_path.parent)
    terminal_lock_path = lock_path.parent / TERMINAL_LOCK_NAME
    if terminal_lock_path.exists():
        try:
            terminal_lock = read_json(terminal_lock_path)
            validate_lock(terminal_lock)
            state_label = (
                "stale"
                if lock_is_stale(terminal_lock, stale_after=stale_after)
                else "active"
            )
        except WorkLoopError as error:
            raise WorkLoopError(
                "terminal reconciliation lock is unreadable or malformed; "
                "do not start a work loop until it is inspected"
            ) from error
        if state_label == "stale":
            raise WorkLoopError(
                "repository has a stale terminal reconciliation lock; retry "
                "reconcile-terminal with --recover-stale-lock after verifying "
                "the prior reconciliation process is gone"
            )
        raise WorkLoopError(
            "repository has an active terminal reconciliation lock; "
            "retry after reconciliation finishes"
        )
    payload = lock_payload(state)
    while True:
        try:
            descriptor = os.open(lock_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            try:
                current = read_json(lock_path)
                validate_lock(current)
            except WorkLoopError as error:
                if not recover_stale:
                    raise WorkLoopError(
                        "work-loop lock is unreadable or malformed; inspect it or retry with "
                        "--recover-stale-lock"
                    ) from error
                try:
                    lock_path.unlink()
                except OSError as unlink_error:
                    raise WorkLoopError(
                        f"cannot recover unreadable work-loop lock: {unlink_error}"
                    ) from unlink_error
                continue
            if current.get("runId") == state["runId"]:
                current["heartbeatAt"] = utc_now()
                atomic_write_json(lock_path, current)
                return
            stale = lock_is_stale(current, stale_after=stale_after)
            if not stale or not recover_stale:
                state_label = "stale" if stale else "active"
                article = "an" if state_label == "active" else "a"
                raise WorkLoopError(
                    f"repository has {article} {state_label} work-loop lock owned by "
                    f"run {current.get('runId')}; reconcile before recovery"
                ) from None
            try:
                lock_path.unlink()
            except OSError as error:
                raise WorkLoopError(f"cannot recover stale work-loop lock: {error}") from error
            continue
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(_json_payload(payload))
            stream.flush()
            os.fsync(stream.fileno())
        return


def require_lock(lock_path: Path, run_id: str) -> dict[str, Any]:
    lock = read_json(lock_path)
    validate_lock(lock)
    if lock.get("runId") != run_id:
        raise WorkLoopError(
            f"work-loop lock belongs to run {lock.get('runId')}, not {run_id}"
        )
    lock["heartbeatAt"] = utc_now()
    atomic_write_json(lock_path, lock)
    return lock


def release_lock(lock_path: Path, run_id: str) -> None:
    if not lock_path.exists():
        return
    lock = read_json(lock_path)
    validate_lock(lock)
    if lock.get("runId") != run_id:
        raise WorkLoopError("refusing to release another work-loop run's lock")
    try:
        lock_path.unlink()
    except OSError as error:
        raise WorkLoopError(f"cannot release work-loop lock: {error}") from error


def acquire_terminal_lock(
    lock_path: Path,
    state: Mapping[str, Any],
    *,
    recover_stale: bool = False,
    stale_after: int = DEFAULT_STALE_LOCK_SECONDS,
) -> str:
    """Acquire a short-lived operation lock without reviving the run."""
    ensure_private_directory(lock_path.parent)
    operation_id = f"terminal-{state['runId']}-{uuid.uuid4().hex}"
    payload = lock_payload(state)
    payload["runId"] = operation_id
    while True:
        try:
            descriptor = os.open(
                lock_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
            )
        except FileExistsError:
            try:
                current = read_json(lock_path)
                validate_lock(current)
                stale = lock_is_stale(current, stale_after=stale_after)
            except WorkLoopError as error:
                raise WorkLoopError(
                    "terminal reconciliation lock is unreadable or malformed; "
                    "inspect it before retrying"
                ) from error
            if not stale:
                raise WorkLoopError(
                    "repository has an active terminal reconciliation lock; "
                    "retry after reconciliation finishes"
                ) from None
            if not recover_stale:
                raise WorkLoopError(
                    "repository has a stale terminal reconciliation lock; "
                    "retry with --recover-stale-lock"
                ) from None
            try:
                lock_path.unlink()
            except OSError as error:
                raise WorkLoopError(
                    f"cannot recover stale terminal reconciliation lock: {error}"
                ) from error
            continue
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(_json_payload(payload))
            stream.flush()
            os.fsync(stream.fileno())
        return operation_id


def load_state_for_repo(
    repo: Path,
    *,
    state_root: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> tuple[dict[str, Any], Path, Path, dict[str, str]]:
    identity = repository_identity(repo)
    root = state_root or resolve_state_root(environ=environ)
    state_path, lock_path = state_paths(identity, root)
    state = read_json(state_path)
    validate_state(state)
    if state["repository"]["digest"] != identity["digest"]:
        raise WorkLoopError("work-loop state belongs to a different repository identity")
    return state, state_path, lock_path, identity


def _terminal_state_guard(state: Mapping[str, Any], run_id: str) -> None:
    if state.get("runId") != run_id:
        raise WorkLoopError(
            f"state belongs to run {state.get('runId')}, not {run_id}"
        )
    if state.get("status") not in {"stopped", "completed"}:
        raise WorkLoopError(
            "terminal reconciliation requires a stopped or completed work loop"
        )
    if state.get("phase") != "stopped":
        raise WorkLoopError(
            "terminal reconciliation requires the historical phase to be stopped"
        )


def _task_identity_token(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    token = PurePosixPath(value.replace("\\", "/").rstrip("/")).name
    token = re.sub(r"^\d{2}-\d{2}-", "", token)
    return token.casefold() or None


def validated_archived_task(
    repo: Path, archived_task: str, recorded_task: object
) -> tuple[str, str]:
    if (
        not isinstance(archived_task, str)
        or not archived_task.strip()
        or len(archived_task) > 300
        or "\\" in archived_task
        or any(ord(character) < 32 for character in archived_task)
    ):
        raise WorkLoopError("archived task path is malformed")
    normalized = archived_task.strip().rstrip("/")
    pure = PurePosixPath(normalized)
    if (
        pure.is_absolute()
        or normalized != pure.as_posix()
        or pure.parts[:3] != (".trellis", "tasks", "archive")
        or len(pure.parts) < 5
        or any(part in {"", ".", ".."} for part in pure.parts)
    ):
        raise WorkLoopError(
            "archived task must be a normalized path below .trellis/tasks/archive/"
        )
    archive_root = repo / ".trellis" / "tasks" / "archive"
    try:
        resolved_archive_root = archive_root.resolve(strict=True)
    except OSError as error:
        raise WorkLoopError(f"cannot resolve Trellis archive root: {error}") from error
    candidate = repo
    for part in pure.parts:
        candidate = candidate / part
        if candidate.is_symlink():
            raise WorkLoopError("archived task path must not contain symlinks")
    try:
        resolved_candidate = candidate.resolve(strict=True)
        resolved_candidate.relative_to(resolved_archive_root)
    except (OSError, ValueError) as error:
        raise WorkLoopError(
            "archived task must resolve below .trellis/tasks/archive/"
        ) from error
    if not resolved_candidate.is_dir():
        raise WorkLoopError("archived task path is not a directory")
    task_path = resolved_candidate / "task.json"
    if task_path.is_symlink() or not task_path.is_file():
        raise WorkLoopError("archived task must contain a regular task.json")
    task = read_json(task_path)
    if task.get("status") != "completed":
        raise WorkLoopError("archived task is not completed")
    task_id = task.get("id")
    if not isinstance(task_id, str) or not task_id.strip() or len(task_id) > 200:
        raise WorkLoopError("archived task has no valid identity")
    recorded_identity = _task_identity_token(recorded_task)
    archived_identities = {
        identity
        for identity in (
            _task_identity_token(task_id),
            _task_identity_token(task.get("name")),
            _task_identity_token(pure.name),
        )
        if identity is not None
    }
    if recorded_identity is None or recorded_identity not in archived_identities:
        raise WorkLoopError(
            "archived task identity does not match the ledger's recorded task"
        )
    return pure.as_posix(), task_id.strip()


def _default_branch(repo: Path) -> str:
    symbolic = run_git(repo, "symbolic-ref", "--quiet", "refs/remotes/origin/HEAD")
    if symbolic and symbolic.startswith("refs/remotes/origin/"):
        return symbolic.removeprefix("refs/remotes/origin/")
    candidates = [
        branch
        for branch in ("main", "master")
        if run_git(repo, "rev-parse", "--verify", f"refs/remotes/origin/{branch}")
        is not None
    ]
    if len(candidates) == 1:
        return candidates[0]
    raise WorkLoopError("cannot determine the origin default branch")


def validated_default_branch(repo: Path, branch: str, head: str) -> str:
    if not isinstance(branch, str) or not branch.strip() or len(branch) > 200:
        raise WorkLoopError("observed branch is malformed")
    if not isinstance(head, str) or COMMIT_RE.fullmatch(head) is None:
        raise WorkLoopError("observed head must be a full lowercase commit ID")
    worktree = run_git(repo, "status", "--porcelain")
    if worktree is None:
        raise WorkLoopError("cannot inspect repository cleanliness")
    if worktree:
        raise WorkLoopError("terminal reconciliation requires a clean worktree")
    current_branch = run_git(repo, "branch", "--show-current")
    default_branch = _default_branch(repo)
    if current_branch != branch or branch != default_branch:
        raise WorkLoopError(
            f"terminal reconciliation requires checked-out default branch {default_branch}"
        )
    resolved_head = _resolved_commit(repo, head)
    local_head = _resolved_commit(repo, "HEAD")
    if resolved_head is None or local_head != resolved_head:
        raise WorkLoopError("observed head does not match the checked-out branch tip")
    upstream = run_git(
        repo, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"
    )
    if upstream != f"origin/{branch}":
        raise WorkLoopError(
            f"default branch must track origin/{branch} before reconciliation"
        )
    remote_head = _resolved_commit(repo, upstream)
    if remote_head != resolved_head:
        raise WorkLoopError(
            "default branch and its remote-tracking branch must match observed head"
        )
    return resolved_head


def validated_terminal_evidence(
    repo: Path,
    state: Mapping[str, Any],
    *,
    archived_task: str,
    delivery: Mapping[str, Any],
    bookkeeping: Mapping[str, Any] | None,
    branch: str,
    head: str,
) -> dict[str, Any]:
    archived_path, task_id = validated_archived_task(
        repo, archived_task, state["current"].get("task")
    )
    observed_head = validated_default_branch(repo, branch, head)

    def resolve_pr(record: Mapping[str, Any], label: str) -> dict[str, Any]:
        normalized = normalized_pr_record(
            pr_number=record.get("prNumber"),
            pr_url=record.get("prUrl"),
            head=record.get("head"),
            merge_commit=record.get("mergeCommit"),
        )
        for field in ("head", "mergeCommit"):
            resolved = _resolved_commit(repo, normalized[field])
            if resolved is None:
                raise WorkLoopError(
                    f"{label} {field} is not a local Git commit: {normalized[field]}"
                )
            normalized[field] = resolved
        return normalized

    normalized_delivery = resolve_pr(delivery, "delivery PR")
    normalized_bookkeeping = (
        resolve_pr(bookkeeping, "bookkeeping PR")
        if bookkeeping is not None
        else None
    )

    def require_merge_head_when_provable(
        record: Mapping[str, Any], label: str
    ) -> None:
        parents = run_git(repo, "rev-list", "--parents", "-n", "1", record["mergeCommit"])
        if parents is None:
            raise WorkLoopError(f"cannot inspect {label} merge strategy")
        if len(parents.split()) > 2 and not _is_ancestor(
            repo, record["head"], record["mergeCommit"]
        ):
            raise WorkLoopError(
                f"{label} merge commit does not contain the submitted PR head"
            )

    require_merge_head_when_provable(normalized_delivery, "delivery PR")
    if normalized_bookkeeping is not None:
        require_merge_head_when_provable(normalized_bookkeeping, "bookkeeping PR")
    recorded_shipped = state["current"].get("lastShippedSha") or state[
        "current"
    ].get("head")
    resolved_recorded = _resolved_commit(repo, recorded_shipped)
    if resolved_recorded is None:
        raise WorkLoopError(
            "ledger has no locally verifiable shipped head for terminal reconciliation"
        )
    if not _is_ancestor(repo, resolved_recorded, normalized_delivery["head"]):
        raise WorkLoopError(
            "delivery head must match or descend from recorded shipped evidence"
        )
    if not _is_ancestor(repo, normalized_delivery["mergeCommit"], observed_head):
        raise WorkLoopError(
            "delivery merge commit must belong to the observed default-branch history"
        )
    if normalized_bookkeeping is not None:
        if not _is_ancestor(
            repo, normalized_bookkeeping["mergeCommit"], observed_head
        ):
            raise WorkLoopError(
                "bookkeeping merge commit must belong to the observed default-branch history"
            )
        if not (
            _is_ancestor(
                repo,
                normalized_delivery["mergeCommit"],
                normalized_bookkeeping["head"],
            )
            or _is_ancestor(
                repo,
                normalized_delivery["mergeCommit"],
                normalized_bookkeeping["mergeCommit"],
            )
        ):
            raise WorkLoopError(
                "bookkeeping evidence is unrelated to the delivery merge"
            )
    return {
        "status": "verified",
        "archivedTask": archived_path,
        "taskId": task_id,
        "delivery": normalized_delivery,
        "bookkeeping": normalized_bookkeeping,
        "observed": {"branch": branch, "head": observed_head},
    }


def _inspect_terminal_run_lock(
    lock_path: Path,
    state: Mapping[str, Any],
    *,
    recover_stale: bool,
) -> bool:
    if not lock_path.exists():
        return False
    try:
        lock = read_json(lock_path)
        validate_lock(lock)
    except WorkLoopError as error:
        raise WorkLoopError(
            "work-loop run lock is unreadable or malformed; inspect it before reconciliation"
        ) from error
    if lock.get("repositoryDigest") != state["repository"]["digest"]:
        raise WorkLoopError("work-loop run lock repository identity is ambiguous")
    stale = lock_is_stale(lock)
    if not stale:
        raise WorkLoopError("terminal reconciliation rejects a live work-loop run lock")
    if not recover_stale:
        raise WorkLoopError(
            "stale work-loop run lock requires --recover-stale-lock"
        )
    return True


def reconcile_terminal_state(
    repo: Path,
    run_id: str,
    *,
    archived_task: str,
    delivery: Mapping[str, Any],
    bookkeeping: Mapping[str, Any] | None,
    branch: str,
    head: str,
    recover_stale: bool = False,
    state_root: Path | None = None,
) -> dict[str, Any]:
    state, state_path, run_lock_path, identity = load_state_for_repo(
        repo, state_root=state_root
    )
    resolved_repo = Path(identity["root"])
    _terminal_state_guard(state, run_id)
    _inspect_terminal_run_lock(
        run_lock_path, state, recover_stale=recover_stale
    )
    validated_terminal_evidence(
        resolved_repo,
        state,
        archived_task=archived_task,
        delivery=delivery,
        bookkeeping=bookkeeping,
        branch=branch,
        head=head,
    )
    operation_lock_path = state_path.parent / TERMINAL_LOCK_NAME
    operation_id = acquire_terminal_lock(
        operation_lock_path, state, recover_stale=recover_stale
    )
    try:
        state = read_json(state_path)
        validate_state(state)
        if state["repository"]["digest"] != identity["digest"]:
            raise WorkLoopError(
                "work-loop state changed repository identity during reconciliation"
            )
        _terminal_state_guard(state, run_id)
        recover_run_lock = _inspect_terminal_run_lock(
            run_lock_path, state, recover_stale=recover_stale
        )
        requested = validated_terminal_evidence(
            resolved_repo,
            state,
            archived_task=archived_task,
            delivery=delivery,
            bookkeeping=bookkeeping,
            branch=branch,
            head=head,
        )
        existing = state.get("terminalReconciliation")
        if existing is not None:
            comparable = dict(existing)
            comparable.pop("reconciledAt", None)
            if comparable != requested:
                raise WorkLoopError(
                    "terminal reconciliation conflicts with the existing verified record"
                )
            if recover_run_lock:
                run_lock_path.unlink()
            return state

        candidate = copy.deepcopy(state)
        now = utc_now()
        candidate["terminalReconciliation"] = {
            **requested,
            "reconciledAt": now,
        }
        candidate["contextHealth"] = {
            "level": "green",
            "epoch": state["contextHealth"]["epoch"],
            "reasons": [],
        }
        candidate["checkpoint"] = {
            "state": "completed",
            "target": "terminal-reconciliation",
            "reason": "verified external completion",
        }
        candidate["updatedAt"] = now
        validate_state(candidate)
        if recover_run_lock:
            run_lock_path.unlink()
        atomic_write_json(state_path, candidate)
        return candidate
    finally:
        release_lock(operation_lock_path, operation_id)


def mutate_state(
    repo: Path,
    run_id: str,
    callback: Callable[[dict[str, Any]], None],
    *,
    state_root: Path | None = None,
) -> dict[str, Any]:
    state, state_path, lock_path, _identity = load_state_for_repo(
        repo, state_root=state_root
    )
    if state["runId"] != run_id:
        raise WorkLoopError(f"state belongs to run {state['runId']}, not {run_id}")
    require_lock(lock_path, run_id)
    callback(state)
    now = utc_now()
    state["updatedAt"] = now
    state["heartbeatAt"] = now
    validate_state(state)
    atomic_write_json(state_path, state)
    return state


def transition_state(
    state: dict[str, Any],
    phase: str,
    *,
    updates: Mapping[str, Any] | None = None,
) -> None:
    current_phase = state["phase"]
    if phase not in LEGAL_TRANSITIONS[current_phase]:
        raise WorkLoopError(f"illegal work-loop transition: {current_phase} -> {phase}")
    normalized_updates: dict[str, Any] = {}
    if updates:
        for key, value in updates.items():
            if key not in state["current"]:
                raise WorkLoopError(f"unknown current-state field: {key}")
            if key not in TRANSITION_CURRENT_FIELDS:
                raise WorkLoopError(
                    f"{key} must be recorded with the evidence command, not transition"
                )
            if value is not None and not isinstance(value, str):
                raise WorkLoopError(f"{key} must be a non-empty string or null")
            normalized = compact_text(value) if isinstance(value, str) else value
            if isinstance(value, str) and not normalized:
                raise WorkLoopError(f"{key} must be a non-empty string or null")
            remembered = state["current"].get(key)
            if (
                key in STABLE_CURRENT_FIELDS
                and remembered is not None
                and remembered != normalized
            ):
                raise WorkLoopError(f"cannot replace stable current-state field: {key}")
            normalized_updates[key] = normalized
    state["phase"] = phase
    state["current"].update(normalized_updates)
    if phase == "inventory" and current_phase == "complete":
        state["iteration"] += 1
        state["current"] = {key: None for key in state["current"]}
    if phase == "stopped":
        state["status"] = "stopped"


def _resolved_commit(repo: Path, value: str) -> str | None:
    return run_git(
        repo, "rev-parse", "--verify", "--end-of-options", f"{value}^{{commit}}"
    )


def _branch_commit(repo: Path, branch: str) -> str | None:
    return _resolved_commit(repo, f"refs/heads/{branch}")


def _is_ancestor(repo: Path, ancestor: str, descendant: str) -> bool:
    return run_git(repo, "merge-base", "--is-ancestor", ancestor, descendant) is not None


def _clear_recovery_checkpoint(state: dict[str, Any]) -> None:
    if state["checkpoint"].get("state") in RECOVERY_CHECKPOINT_STATES:
        state["checkpoint"] = {
            "state": "none",
            "target": None,
            "reason": None,
            "resumePhase": None,
        }


def _known_checkpoint_resume_phase(state: Mapping[str, Any]) -> str | None:
    checkpoint = state["checkpoint"]
    resume_phase = checkpoint.get("resumePhase")
    if resume_phase is not None:
        return resume_phase
    target = checkpoint.get("target")
    if target in LIFECYCLE_PHASE_ORDER:
        return target
    return None


def checkpoint_resume_phase(
    state: Mapping[str, Any], *, explicit_resume_phase: str | None = None
) -> str:
    """Resolve the lifecycle phase owned by a checkpoint overlay."""
    resume_phase = _known_checkpoint_resume_phase(state)
    if resume_phase is not None:
        return resume_phase
    if explicit_resume_phase is not None:
        if explicit_resume_phase not in LIFECYCLE_PHASE_ORDER:
            raise WorkLoopError(
                f"invalid checkpoint resume phase: {explicit_resume_phase}"
            )
        return explicit_resume_phase
    raise WorkLoopError(
        "checkpoint recovery requires --resume-phase because its target is not "
        "a lifecycle phase"
    )


def checkpoint_owner_phase(
    state: Mapping[str, Any], *, explicit_resume_phase: str | None = None
) -> str:
    """Return the lifecycle owner without treating checkpoint as progress."""
    phase = state["phase"]
    if phase == "checkpoint":
        return checkpoint_resume_phase(
            state, explicit_resume_phase=explicit_resume_phase
        )
    if phase not in LIFECYCLE_PHASE_ORDER:
        raise WorkLoopError(f"phase {phase} cannot own a recovery checkpoint")
    resume_phase = state["checkpoint"].get("resumePhase")
    if resume_phase is not None and resume_phase != phase:
        raise WorkLoopError(
            f"checkpoint resume phase {resume_phase} differs from ledger {phase}"
        )
    return phase


def _has_complete_recovery_evidence(
    current: Mapping[str, Any], observations: Mapping[str, Any]
) -> bool:
    recorded_fields = {
        key for key in CURRENT_FIELD_ORDER if current.get(key) is not None
    }
    return recorded_fields.issubset(observations)


def validated_evidence(
    state: Mapping[str, Any],
    updates: Mapping[str, Any],
    *,
    repo: Path | None = None,
    phase: str | None = None,
) -> dict[str, Any]:
    if not updates:
        raise WorkLoopError("evidence update requires at least one current-state field")
    unknown = sorted(set(updates) - CURRENT_FIELDS)
    if unknown:
        raise WorkLoopError(f"unknown current-state field: {unknown[0]}")
    evidence_phase = phase or state["phase"]
    if evidence_phase not in ACTIVE_EVIDENCE_PHASES:
        raise WorkLoopError(f"cannot update evidence during {evidence_phase} phase")

    current = state["current"]
    candidate = dict(current)
    for key, value in updates.items():
        if key == "prNumber":
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise WorkLoopError("prNumber evidence must be a positive integer")
            candidate[key] = value
            continue
        if not isinstance(value, str) or not value.strip():
            raise WorkLoopError(f"{key} evidence must be a non-empty string")
        candidate[key] = compact_text(value)

    for key in STABLE_CURRENT_FIELDS:
        remembered = current.get(key)
        observed = candidate.get(key)
        if remembered is not None and observed != remembered:
            raise WorkLoopError(f"cannot replace stable current-state field: {key}")

    remembered_pr = current.get("prNumber")
    if remembered_pr is not None and candidate.get("prNumber") != remembered_pr:
        raise WorkLoopError("cannot replace recorded pull request number")
    remembered_url = current.get("prUrl")
    if remembered_url is not None and candidate.get("prUrl") != remembered_url:
        raise WorkLoopError("cannot replace recorded pull request URL")
    if candidate.get("prUrl") is not None:
        if candidate.get("prNumber") is None:
            raise WorkLoopError("pull request URL evidence requires a pull request number")
        final_component = urlsplit(candidate["prUrl"]).path.rstrip("/").rsplit("/", 1)[-1]
        if not final_component.isdigit() or int(final_component) != candidate["prNumber"]:
            raise WorkLoopError("pull request URL does not match the recorded number")

    evidence_repo = repo or Path(state["repository"]["root"])
    remembered_branch = current.get("branch")
    candidate_branch = candidate.get("branch")
    branch_changed = (
        remembered_branch is not None and candidate_branch != remembered_branch
    )
    if branch_changed:
        if (
            evidence_phase not in MERGE_EVIDENCE_PHASES
            or candidate_branch != candidate.get("baseBranch")
            or candidate.get("prNumber") is None
            or candidate.get("lastShippedSha") is None
        ):
            raise WorkLoopError(
                "branch evidence may change only to the base branch at a verified merge boundary"
            )

    branch_head: str | None = None
    compare_branch_head = candidate_branch is not None and bool(
        {"branch", "head"} & set(updates)
    )
    if compare_branch_head:
        if not isinstance(candidate_branch, str) or not candidate_branch.strip():
            raise WorkLoopError("branch evidence must be a non-empty string")
        branch_head = _branch_commit(evidence_repo, candidate_branch)
        if branch_head is None and "branch" in updates:
            raise WorkLoopError(
                f"branch evidence is not a local Git branch: {candidate_branch}"
            )

    remembered_head = current.get("head")
    candidate_head = candidate.get("head")
    if candidate_head is not None:
        resolved_head = _resolved_commit(evidence_repo, candidate_head)
        if resolved_head is None:
            raise WorkLoopError(f"head evidence is not a local Git commit: {candidate_head}")
        candidate["head"] = resolved_head
        candidate_head = resolved_head
        if (
            compare_branch_head
            and branch_head is not None
            and branch_head != resolved_head
        ):
            raise WorkLoopError("HEAD evidence does not match the recorded branch")
        if not branch_changed and remembered_head is not None and candidate_head != remembered_head:
            resolved_remembered = _resolved_commit(evidence_repo, remembered_head)
            if resolved_remembered is None or not _is_ancestor(
                evidence_repo, resolved_remembered, resolved_head
            ):
                raise WorkLoopError("head evidence must advance to a descendant commit")

    remembered_shipped = current.get("lastShippedSha")
    candidate_shipped = candidate.get("lastShippedSha")
    if candidate_shipped is not None:
        resolved_shipped = _resolved_commit(evidence_repo, candidate_shipped)
        if resolved_shipped is None:
            raise WorkLoopError(
                f"lastShippedSha evidence is not a local Git commit: {candidate_shipped}"
            )
        candidate["lastShippedSha"] = resolved_shipped
        candidate_shipped = resolved_shipped
        resolved_remembered_shipped = (
            _resolved_commit(evidence_repo, remembered_shipped)
            if remembered_shipped is not None
            else None
        )
        if (
            remembered_shipped is not None
            and candidate_shipped != resolved_remembered_shipped
        ):
            if resolved_remembered_shipped is None or not _is_ancestor(
                evidence_repo, resolved_remembered_shipped, resolved_shipped
            ):
                raise WorkLoopError(
                    "lastShippedSha evidence must advance to a descendant commit"
                )
        unchanged_historical_shipped_evidence = (
            resolved_remembered_shipped is not None
            and candidate_shipped == resolved_remembered_shipped
            and not branch_changed
            and isinstance(remembered_branch, str)
            and bool(remembered_branch.strip())
            and isinstance(candidate_branch, str)
            and bool(candidate_branch.strip())
            and candidate_branch == remembered_branch
            and candidate_branch == candidate.get("baseBranch")
        )
        if not unchanged_historical_shipped_evidence:
            evidence_tip = candidate_head
            if branch_changed:
                evidence_tip = (
                    _branch_commit(evidence_repo, remembered_branch)
                    if isinstance(remembered_branch, str)
                    else None
                ) or remembered_head or candidate_head
            resolved_tip = (
                _resolved_commit(evidence_repo, evidence_tip)
                if evidence_tip is not None
                else None
            )
            if resolved_tip is None:
                tip_branch = remembered_branch if branch_changed else candidate_branch
                if isinstance(tip_branch, str):
                    resolved_tip = _branch_commit(evidence_repo, tip_branch)
            if resolved_tip is None:
                raise WorkLoopError(
                    "lastShippedSha evidence requires a verifiable recorded head or branch"
                )
            if not _is_ancestor(
                evidence_repo, resolved_shipped, resolved_tip
            ):
                raise WorkLoopError(
                    "lastShippedSha evidence must belong to the shipped branch"
                )

    return candidate


def update_evidence(
    state: dict[str, Any],
    updates: Mapping[str, Any],
    *,
    repo: Path | None = None,
) -> None:
    candidate = validated_evidence(state, updates, repo=repo)
    recovery_checkpoint_active = (
        state["checkpoint"].get("state") in RECOVERY_CHECKPOINT_STATES
    )
    state["current"] = candidate
    if recovery_checkpoint_active and not _has_complete_recovery_evidence(
        candidate, updates
    ):
        return
    state["contextHealth"] = {
        "level": "green",
        "epoch": state["contextHealth"]["epoch"],
        "reasons": [],
    }
    _clear_recovery_checkpoint(state)


def apply_context_signal(state: dict[str, Any], signal: str, reason: str) -> None:
    if signal not in CONTEXT_SIGNALS:
        raise WorkLoopError(f"unknown context signal: {signal}")
    red = signal in {"contradiction", "duplicate-side-effect"}
    health = state["contextHealth"]
    health["level"] = "red" if red else "amber"
    health["epoch"] += 1
    health["reasons"] = (health.get("reasons", []) + [compact_text(reason)])[-10:]


def reconcile_state(
    state: dict[str, Any],
    observations: Mapping[str, Any],
    *,
    signal: str | None = None,
    verified_live_advance: bool = False,
    explicit_resume_phase: str | None = None,
    repo: Path | None = None,
) -> None:
    contradictions: list[str] = []
    context_signal_applied = False
    current = state["current"]
    observed_phase = observations.get("phase")
    advanced_phase: str | None = None
    recovery_checkpoint_active = (
        state["checkpoint"].get("state") in RECOVERY_CHECKPOINT_STATES
    )
    ledger_phase = state["phase"]
    try:
        owner_phase = (
            checkpoint_owner_phase(
                state, explicit_resume_phase=explicit_resume_phase
            )
            if recovery_checkpoint_active
            else ledger_phase
        )
    except WorkLoopError as error:
        contradictions.append(str(error))
        owner_phase = ledger_phase
    if observed_phase is not None:
        if observed_phase not in PHASE_ORDER:
            raise WorkLoopError(f"invalid observed phase: {observed_phase}")
        observed_is_lifecycle = observed_phase in LIFECYCLE_PHASE_ORDER
        owner_is_lifecycle = owner_phase in LIFECYCLE_PHASE_ORDER
        observed_is_forward = (
            observed_is_lifecycle
            and owner_is_lifecycle
            and LIFECYCLE_PHASE_ORDER[observed_phase]
            > LIFECYCLE_PHASE_ORDER[owner_phase]
        )
        recovering_legacy_owner = (
            recovery_checkpoint_active
            and ledger_phase == "checkpoint"
            and observed_phase == owner_phase
        )
        if observed_is_forward and verified_live_advance:
            advanced_phase = observed_phase
        elif recovering_legacy_owner:
            advanced_phase = observed_phase
        elif observed_phase != ledger_phase:
            contradictions.append(
                f"observed phase {observed_phase} differs from ledger owner {owner_phase}"
            )
    evidence_observations: dict[str, Any] = {}
    for key in CURRENT_FIELD_ORDER:
        value = observations.get(key)
        if value is None:
            continue
        evidence_observations[key] = (
            compact_text(value) if isinstance(value, str) else value
        )
    mismatches = {
        key: value
        for key, value in evidence_observations.items()
        if current.get(key) != value
    }
    candidate_current: dict[str, Any] | None = None
    has_complete_recovery_evidence = _has_complete_recovery_evidence(
        current, evidence_observations
    )
    recovery_attempted = observed_phase is not None or bool(evidence_observations)
    if (
        recovery_checkpoint_active
        and recovery_attempted
        and not has_complete_recovery_evidence
    ):
        contradictions.append(
            "checkpoint recovery requires every recorded current-state field"
        )
    should_validate_recovery = (
        recovery_checkpoint_active
        and recovery_attempted
        and has_complete_recovery_evidence
    )
    may_validate_recovery = should_validate_recovery and bool(
        evidence_observations
    ) and (
        not mismatches or verified_live_advance
    )
    if may_validate_recovery or (mismatches and verified_live_advance):
        try:
            candidate_current = validated_evidence(
                state,
                evidence_observations if should_validate_recovery else mismatches,
                repo=repo,
                phase=advanced_phase or owner_phase,
            )
        except WorkLoopError as error:
            contradictions.append(str(error))
    elif mismatches:
        contradictions.extend(
            f"observed {key} {value!r} differs from ledger {current.get(key)!r}"
            for key, value in mismatches.items()
        )
    if contradictions:
        apply_context_signal(state, "contradiction", "; ".join(contradictions))
        checkpoint_target = state["checkpoint"].get("target")
        if checkpoint_target is None:
            checkpoint_target = owner_phase
        resume_phase = owner_phase if owner_phase in LIFECYCLE_PHASE_ORDER else None
        state["checkpoint"] = {
            "state": "blocked",
            "target": checkpoint_target,
            "reason": compact_text("; ".join(contradictions)),
            "resumePhase": resume_phase,
        }
        return
    if candidate_current is not None:
        state["current"] = candidate_current
    if advanced_phase is not None:
        state["phase"] = advanced_phase
        if advanced_phase != owner_phase:
            apply_context_signal(
                state,
                "continuation-summary",
                f"verified live state advanced to {advanced_phase}",
            )
            context_signal_applied = True
        _clear_recovery_checkpoint(state)
    if signal:
        apply_context_signal(state, signal, f"runtime signal: {signal}")
    elif not context_signal_applied:
        has_observations = bool(evidence_observations) or observed_phase is not None
        recovery_current = candidate_current or current
        complete_recovery_evidence = _has_complete_recovery_evidence(
            recovery_current, evidence_observations
        )
        may_restore_health = (
            not recovery_checkpoint_active or complete_recovery_evidence
        )
        if (
            has_observations and may_restore_health
        ) or state["contextHealth"]["level"] != "red":
            state["contextHealth"] = {
                "level": "green",
                "epoch": state["contextHealth"]["epoch"],
                "reasons": [],
            }
        if recovery_attempted and complete_recovery_evidence:
            _clear_recovery_checkpoint(state)


def record_result(
    state: dict[str, Any],
    *,
    task: str,
    outcome: str,
    pr_number: int | None,
    pr_url: str | None,
    review_rounds: int,
    ci_retries: int,
    decisions: Sequence[str],
    followups: Sequence[str],
) -> None:
    counter_key = {
        "completed": "completed",
        "parked": "parked",
        "blocked": "blocked",
        "skipped": "skipped",
        "failed": "failures",
    }.get(outcome)
    if counter_key is None:
        raise WorkLoopError(f"unknown iteration outcome: {outcome}")
    if review_rounds < 0 or ci_retries < 0:
        raise WorkLoopError("review rounds and CI retries must be non-negative")
    state["counters"][counter_key] += 1
    state["counters"]["reviewRounds"] += review_rounds
    state["counters"]["ciRetries"] += ci_retries
    if outcome == "completed" and pr_number is not None:
        state["counters"]["mergedPrs"] += 1
    result = {
        "iteration": state["iteration"],
        "task": compact_text(task, limit=160),
        "outcome": outcome,
        "prNumber": pr_number,
        "prUrl": compact_text(pr_url, limit=240) if pr_url else None,
        "reviewRounds": review_rounds,
        "ciRetries": ci_retries,
        "completedAt": utc_now(),
    }
    state["iterations"] = (state["iterations"] + [result])[-MAX_HISTORY:]
    state["decisions"] = (
        state["decisions"] + [compact_text(item) for item in decisions]
    )[-MAX_NOTES:]
    state["followups"] = (
        state["followups"] + [compact_text(item) for item in followups]
    )[-MAX_NOTES:]
    state["phase"] = "complete"
    state["current"]["task"] = compact_text(task, limit=160)


def status_snapshot(
    repo: Path,
    *,
    state_root: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    try:
        identity = repository_identity(repo)
        root = state_root or resolve_state_root(environ=environ)
        state_path, lock_path = state_paths(identity, root)
        if not state_path.is_file():
            return {"status": "none"}
        state = read_json(state_path)
        validate_state(state)
        if state["repository"]["digest"] != identity["digest"]:
            raise WorkLoopError("repository identity does not match loop state")
        lock: dict[str, Any] | None = None
        if lock_path.is_file():
            lock = read_json(lock_path)
        focus = state["focus"]
        return {
            "status": state["status"],
            "runId": state["runId"],
            "mode": state["mode"],
            "selector": state["selector"],
            "until": state["until"],
            "iteration": state["iteration"],
            "phase": state["phase"],
            "task": state["current"].get("task"),
            "branch": state["current"].get("branch"),
            "head": state["current"].get("head"),
            "baseBranch": state["current"].get("baseBranch"),
            "prNumber": state["current"].get("prNumber"),
            "prUrl": state["current"].get("prUrl"),
            "lastShippedSha": state["current"].get("lastShippedSha"),
            "focusMode": focus["mode"],
            "focus": list(focus["original"]),
            "counters": dict(state["counters"]),
            "heartbeatAt": state["heartbeatAt"],
            "contextHealth": dict(state["contextHealth"]),
            "checkpoint": dict(state["checkpoint"]),
            "stopReason": state["stopReason"],
            "terminalReconciliation": copy.deepcopy(
                state.get("terminalReconciliation")
            ),
            "lock": {
                "present": lock is not None,
                "stale": lock_is_stale(lock) if lock else False,
                "runId": lock.get("runId") if lock else None,
            },
        }
    except (KeyError, TypeError, WorkLoopError) as error:
        return {"status": "invalid", "error": compact_text(error, limit=500)}


def _state_root_arg(value: str | None) -> Path | None:
    if value is None:
        return None
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise WorkLoopError("--state-home must be an absolute path")
    return path


def _print(value: Mapping[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(value, indent=2, sort_keys=True))
        return
    candidates = value.get("candidates")
    if isinstance(candidates, list):
        print(f"count: {len(candidates)}")
        for candidate in candidates:
            if not isinstance(candidate, Mapping):
                continue
            label = candidate.get("id") or candidate.get("task") or candidate.get("title")
            evidence = candidate.get("focusEvidence")
            detail = ""
            if isinstance(evidence, list) and evidence:
                detail = f" ({'; '.join(str(item) for item in evidence)})"
            print(f"- {compact_text(label, limit=160)}{detail}")
        return
    for key in (
        "status",
        "runId",
        "mode",
        "selector",
        "iteration",
        "phase",
        "task",
        "branch",
        "head",
        "baseBranch",
        "prNumber",
        "prUrl",
        "lastShippedSha",
        "focusMode",
        "heartbeatAt",
        "stopReason",
    ):
        if key in value and value[key] is not None:
            print(f"{key}: {value[key]}")


def _add_current_arguments(command: argparse.ArgumentParser) -> None:
    command.add_argument("--task")
    command.add_argument("--branch")
    command.add_argument("--head")
    command.add_argument("--base-branch")
    command.add_argument("--pr-number", type=int)
    command.add_argument("--pr-url")
    command.add_argument("--last-shipped-sha")


def _add_transition_arguments(command: argparse.ArgumentParser) -> None:
    command.add_argument("--task")
    command.add_argument("--base-branch")


def _current_updates_from_args(args: argparse.Namespace) -> dict[str, Any]:
    field_map = {
        "task": args.task,
        "branch": args.branch,
        "head": args.head,
        "baseBranch": args.base_branch,
        "prNumber": args.pr_number,
        "prUrl": args.pr_url,
        "lastShippedSha": args.last_shipped_sha,
    }
    return {key: value for key, value in field_map.items() if value is not None}


def _transition_updates_from_args(args: argparse.Namespace) -> dict[str, Any]:
    field_map = {
        "task": args.task,
        "baseBranch": args.base_branch,
    }
    return {key: value for key, value in field_map.items() if value is not None}


def _terminal_pr_from_args(
    args: argparse.Namespace, prefix: str, *, required: bool
) -> dict[str, Any] | None:
    values = {
        "prNumber": getattr(args, f"{prefix}_pr_number"),
        "prUrl": getattr(args, f"{prefix}_pr_url"),
        "head": getattr(args, f"{prefix}_head"),
        "mergeCommit": getattr(args, f"{prefix}_merge_commit"),
    }
    present = [value is not None for value in values.values()]
    if required and not all(present):
        raise WorkLoopError(f"all {prefix} PR evidence fields are required")
    if not required and any(present) and not all(present):
        raise WorkLoopError(
            f"{prefix} PR evidence must be provided as a complete group"
        )
    return values if all(present) else None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-home", help="absolute user-local state directory")
    subparsers = parser.add_subparsers(dest="command", required=True)

    start = subparsers.add_parser("start", help="start or safely resume a loop")
    start.add_argument("--repo", type=Path, default=Path.cwd())
    start.add_argument("--mode", choices=("backlog", "designs"))
    start.add_argument("--selector", choices=("all", "needs-design"))
    start.add_argument("--until", choices=("design", "merge"))
    start.add_argument("--focus", action="append", default=[])
    start.add_argument("--focus-only", action="append", default=[])
    start.add_argument("--bare-focus")
    start.add_argument("--run-id")
    start.add_argument("--recover-stale-lock", action="store_true")
    start.add_argument("--json", action="store_true")

    status = subparsers.add_parser("status", help="inspect loop state without mutation")
    status.add_argument("--repo", type=Path, default=Path.cwd())
    status.add_argument("--json", action="store_true")

    rank = subparsers.add_parser("rank", help="rank candidate tasks using stored focus")
    rank.add_argument("--repo", type=Path, default=Path.cwd())
    rank.add_argument("--candidates-file", type=Path, required=True)
    rank.add_argument("--json", action="store_true")

    transition = subparsers.add_parser("transition", help="advance a legal phase")
    transition.add_argument("--repo", type=Path, default=Path.cwd())
    transition.add_argument("--run-id", required=True)
    transition.add_argument("--phase", choices=PHASES, required=True)
    _add_transition_arguments(transition)
    transition.add_argument("--json", action="store_true")

    evidence = subparsers.add_parser(
        "evidence", help="record verified evidence without changing phase"
    )
    evidence.add_argument("--repo", type=Path, default=Path.cwd())
    evidence.add_argument("--run-id", required=True)
    _add_current_arguments(evidence)
    evidence.add_argument("--json", action="store_true")

    reconcile = subparsers.add_parser("reconcile", help="compare ledger with live evidence")
    reconcile.add_argument("--repo", type=Path, default=Path.cwd())
    reconcile.add_argument("--run-id", required=True)
    reconcile.add_argument("--observed-phase", choices=PHASES)
    _add_current_arguments(reconcile)
    reconcile.add_argument("--signal", choices=sorted(CONTEXT_SIGNALS))
    reconcile.add_argument("--verified-live-advance", action="store_true")
    reconcile.add_argument("--resume-phase", choices=LIFECYCLE_PHASES)
    reconcile.add_argument("--json", action="store_true")

    terminal = subparsers.add_parser(
        "reconcile-terminal",
        help="record verified external completion for a terminal loop",
    )
    terminal.add_argument("--repo", type=Path, default=Path.cwd())
    terminal.add_argument("--run-id", required=True)
    terminal.add_argument("--archived-task", required=True)
    terminal.add_argument("--delivery-pr-number", type=int, required=True)
    terminal.add_argument("--delivery-pr-url", required=True)
    terminal.add_argument("--delivery-head", required=True)
    terminal.add_argument("--delivery-merge-commit", required=True)
    terminal.add_argument("--bookkeeping-pr-number", type=int)
    terminal.add_argument("--bookkeeping-pr-url")
    terminal.add_argument("--bookkeeping-head")
    terminal.add_argument("--bookkeeping-merge-commit")
    terminal.add_argument("--branch", required=True)
    terminal.add_argument("--head", required=True)
    terminal.add_argument("--recover-stale-lock", action="store_true")
    terminal.add_argument("--json", action="store_true")

    result = subparsers.add_parser("result", help="record a compact iteration result")
    result.add_argument("--repo", type=Path, default=Path.cwd())
    result.add_argument("--run-id", required=True)
    result.add_argument("--task", required=True)
    result.add_argument(
        "--outcome",
        choices=("completed", "parked", "blocked", "skipped", "failed"),
        required=True,
    )
    result.add_argument("--pr-number", type=int)
    result.add_argument("--pr-url")
    result.add_argument("--review-rounds", type=int, default=0)
    result.add_argument("--ci-retries", type=int, default=0)
    result.add_argument("--decision", action="append", default=[])
    result.add_argument("--follow-up", action="append", default=[])
    result.add_argument("--json", action="store_true")

    focus = subparsers.add_parser("focus", help="replace or clear focus at a boundary")
    focus.add_argument("--repo", type=Path, default=Path.cwd())
    focus.add_argument("--run-id", required=True)
    focus.add_argument("--prefer", action="append", default=[])
    focus.add_argument("--only", action="append", default=[])
    focus.add_argument("--clear", action="store_true")
    focus.add_argument("--json", action="store_true")

    checkpoint = subparsers.add_parser("checkpoint", help="persist a resumable checkpoint")
    checkpoint.add_argument("--repo", type=Path, default=Path.cwd())
    checkpoint.add_argument("--run-id", required=True)
    checkpoint.add_argument("--target", required=True)
    checkpoint.add_argument("--reason", required=True)
    checkpoint.add_argument("--pause", action="store_true")
    checkpoint.add_argument("--json", action="store_true")

    stop = subparsers.add_parser("stop", help="stop, pause, or complete a loop")
    stop.add_argument("--repo", type=Path, default=Path.cwd())
    stop.add_argument("--run-id", required=True)
    stop.add_argument("--status", choices=("paused", "stopped", "completed"), required=True)
    stop.add_argument("--reason", required=True)
    stop.add_argument("--json", action="store_true")

    heartbeat = subparsers.add_parser("heartbeat", help="refresh the run heartbeat")
    heartbeat.add_argument("--repo", type=Path, default=Path.cwd())
    heartbeat.add_argument("--run-id", required=True)
    heartbeat.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        state_root = _state_root_arg(args.state_home)
        if args.command == "start":
            identity = repository_identity(args.repo)
            root = state_root or resolve_state_root()
            state_path, lock_path = state_paths(identity, root)
            focus = normalize_focus(
                bare=args.bare_focus,
                preferred=args.focus,
                only=args.focus_only,
            )
            focus_requested = bool(
                args.bare_focus is not None or args.focus or args.focus_only
            )
            if state_path.is_file():
                state = read_json(state_path)
                validate_state(state)
                if state["repository"]["digest"] != identity["digest"]:
                    raise WorkLoopError(
                        "existing loop state belongs to a different repository identity"
                    )
                if state["status"] in {"active", "paused"}:
                    if args.run_id and args.run_id != state["runId"]:
                        raise WorkLoopError(
                            f"resumable loop already exists as run {state['runId']}"
                        )
                    conflicting_options = [
                        name
                        for name, requested, persisted in (
                            ("--mode", args.mode, state["mode"]),
                            ("--selector", args.selector, state["selector"]),
                            ("--until", args.until, state["until"]),
                        )
                        if requested is not None and requested != persisted
                    ]
                    if conflicting_options:
                        raise WorkLoopError(
                            "resume arguments conflict with the persisted loop: "
                            + ", ".join(conflicting_options)
                        )
                    if focus_requested and focus != state["focus"]:
                        raise WorkLoopError(
                            "resume focus conflicts with the persisted loop; use the "
                            "focus subcommand at a task boundary"
                        )
                    acquire_lock(
                        lock_path,
                        state,
                        recover_stale=args.recover_stale_lock,
                    )
                    state["status"] = "active"
                    state["updatedAt"] = utc_now()
                    state["heartbeatAt"] = state["updatedAt"]
                    atomic_write_json(state_path, state)
                    _print(status_snapshot(args.repo, state_root=root), as_json=args.json)
                    return 0
            state = new_state(
                identity,
                mode=args.mode or "backlog",
                selector=args.selector or "all",
                focus=focus,
                until=args.until or "merge",
                run_id=args.run_id,
            )
            acquire_lock(
                lock_path,
                state,
                recover_stale=args.recover_stale_lock,
            )
            atomic_write_json(state_path, state)
            _print(status_snapshot(args.repo, state_root=root), as_json=args.json)
            return 0

        if args.command == "status":
            _print(status_snapshot(args.repo, state_root=state_root), as_json=args.json)
            return 0

        if args.command == "rank":
            state, _state_path, _lock_path, _identity = load_state_for_repo(
                args.repo, state_root=state_root
            )
            payload = json.loads(
                args.candidates_file.read_text(encoding="utf-8", errors="strict")
            )
            if not isinstance(payload, list):
                raise WorkLoopError("candidate file must contain a JSON array")
            ranked = rank_candidates(payload, state["focus"])
            output = {"count": len(ranked), "candidates": ranked}
            _print(output, as_json=args.json)
            return 0

        if args.command == "transition":
            updates = _transition_updates_from_args(args)
            state = mutate_state(
                args.repo,
                args.run_id,
                lambda item: transition_state(item, args.phase, updates=updates),
                state_root=state_root,
            )
        elif args.command == "evidence":
            updates = _current_updates_from_args(args)
            state = mutate_state(
                args.repo,
                args.run_id,
                lambda item: update_evidence(item, updates, repo=args.repo),
                state_root=state_root,
            )
        elif args.command == "reconcile":
            observations = _current_updates_from_args(args)
            observations["phase"] = args.observed_phase
            observations = {key: value for key, value in observations.items() if value is not None}
            state = mutate_state(
                args.repo,
                args.run_id,
                lambda item: reconcile_state(
                    item,
                    observations,
                    signal=args.signal,
                    verified_live_advance=args.verified_live_advance,
                    explicit_resume_phase=args.resume_phase,
                    repo=args.repo,
                ),
                state_root=state_root,
            )
        elif args.command == "reconcile-terminal":
            delivery = _terminal_pr_from_args(args, "delivery", required=True)
            bookkeeping = _terminal_pr_from_args(
                args, "bookkeeping", required=False
            )
            if delivery is None:
                raise WorkLoopError("delivery PR evidence is required")
            state = reconcile_terminal_state(
                args.repo,
                args.run_id,
                archived_task=args.archived_task,
                delivery=delivery,
                bookkeeping=bookkeeping,
                branch=args.branch,
                head=args.head,
                recover_stale=args.recover_stale_lock,
                state_root=state_root,
            )
        elif args.command == "result":
            state = mutate_state(
                args.repo,
                args.run_id,
                lambda item: record_result(
                    item,
                    task=args.task,
                    outcome=args.outcome,
                    pr_number=args.pr_number,
                    pr_url=args.pr_url,
                    review_rounds=args.review_rounds,
                    ci_retries=args.ci_retries,
                    decisions=args.decision,
                    followups=args.follow_up,
                ),
                state_root=state_root,
            )
        elif args.command == "focus":
            if args.clear and (args.prefer or args.only):
                raise WorkLoopError("--clear cannot be combined with focus expressions")
            if not args.clear and not (args.prefer or args.only):
                raise WorkLoopError(
                    "focus requires --clear, --prefer, or --only"
                )
            replacement = (
                normalize_focus()
                if args.clear
                else normalize_focus(preferred=args.prefer, only=args.only)
            )

            def replace_focus(item: dict[str, Any]) -> None:
                if item["current"]["task"] is not None:
                    raise WorkLoopError(
                        "focus can only change at a task boundary"
                    )
                if item["phase"] == "complete":
                    transition_state(item, "inventory")
                elif item["phase"] not in {"inventory", "checkpoint"}:
                    raise WorkLoopError(
                        "focus can only change from inventory, complete, or an idle checkpoint"
                    )
                else:
                    item["phase"] = "inventory"
                item["focus"] = replacement

            state = mutate_state(
                args.repo,
                args.run_id,
                replace_focus,
                state_root=state_root,
            )
        elif args.command == "checkpoint":
            def checkpoint(item: dict[str, Any]) -> None:
                prior_phase = item["phase"]
                resume_phase = (
                    _known_checkpoint_resume_phase(item)
                    if prior_phase == "checkpoint"
                    else prior_phase
                )
                if (
                    resume_phase is not None
                    and resume_phase not in LIFECYCLE_PHASE_ORDER
                ):
                    raise WorkLoopError(
                        f"phase {resume_phase} cannot own a recovery checkpoint"
                    )
                item["phase"] = resume_phase or "checkpoint"
                item["checkpoint"] = {
                    "state": "paused" if args.pause else "ready",
                    "target": compact_text(args.target, limit=120),
                    "reason": compact_text(args.reason),
                    "resumePhase": resume_phase,
                }
                if args.pause:
                    item["status"] = "paused"

            state = mutate_state(
                args.repo, args.run_id, checkpoint, state_root=state_root
            )
            if args.pause:
                _loaded, _path, lock_path, _identity = load_state_for_repo(
                    args.repo, state_root=state_root
                )
                release_lock(lock_path, args.run_id)
        elif args.command == "stop":
            def stop(item: dict[str, Any]) -> None:
                item["status"] = args.status
                item["stopReason"] = compact_text(args.reason)
                if args.status == "paused":
                    prior_phase = item["phase"]
                    resume_phase = (
                        _known_checkpoint_resume_phase(item)
                        if prior_phase == "checkpoint"
                        else prior_phase
                    )
                    if (
                        resume_phase is not None
                        and resume_phase not in LIFECYCLE_PHASE_ORDER
                    ):
                        raise WorkLoopError(
                            f"phase {resume_phase} cannot own a recovery checkpoint"
                        )
                    checkpoint_target = (
                        item["checkpoint"].get("target")
                        if prior_phase == "checkpoint"
                        else prior_phase
                    )
                    if checkpoint_target is None:
                        checkpoint_target = resume_phase or "checkpoint"
                    item["phase"] = resume_phase or "checkpoint"
                    item["checkpoint"] = {
                        "state": "paused",
                        "target": checkpoint_target,
                        "reason": compact_text(args.reason),
                        "resumePhase": resume_phase,
                    }
                    return
                item["phase"] = "stopped"
                item["checkpoint"] = {
                    "state": "completed" if args.status == "completed" else args.status,
                    "target": None,
                    "reason": compact_text(args.reason),
                    "resumePhase": None,
                }

            state = mutate_state(args.repo, args.run_id, stop, state_root=state_root)
            _loaded, _path, lock_path, _identity = load_state_for_repo(
                args.repo, state_root=state_root
            )
            release_lock(lock_path, args.run_id)
        elif args.command == "heartbeat":
            state = mutate_state(
                args.repo, args.run_id, lambda _item: None, state_root=state_root
            )
        else:
            parser.error(f"unsupported command: {args.command}")
            return 2
        _print(state, as_json=args.json)
        return 0
    except (WorkLoopError, OSError, UnicodeError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
