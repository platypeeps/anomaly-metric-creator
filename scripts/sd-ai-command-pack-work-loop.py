#!/usr/bin/env python3
"""Manage resumable, user-local state for autonomous SD work loops."""

from __future__ import annotations

import argparse
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
from pathlib import Path, PureWindowsPath
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import urlsplit, urlunsplit

SCHEMA_VERSION = 1
MAX_LEDGER_BYTES = 64 * 1024
MAX_HISTORY = 20
MAX_NOTES = 50
DEFAULT_STALE_LOCK_SECONDS = 15 * 60
STATE_HOME_ENV = "SD_AI_COMMAND_PACK_STATE_HOME"
FOCUS_FIELDS = frozenset({"priority", "package", "task", "status", "scope"})
FOCUS_PREFIX_RE = re.compile(r"^([A-Za-z][A-Za-z0-9_-]*):(.*)$", re.DOTALL)
WORD_RE = re.compile(r"[A-Za-z0-9_.-]+")
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
PHASES = (
    "inventory",
    "selected",
    "planning",
    "implementing",
    "validating",
    "shipping",
    "followups",
    "complete",
    "checkpoint",
    "stopped",
)
PHASE_ORDER = {phase: index for index, phase in enumerate(PHASES)}
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
        "checkpoint": {"state": "none", "target": None, "reason": None},
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
    if state["checkpoint"].get("state") in {"ready", "blocked"}:
        state["checkpoint"] = {"state": "none", "target": None, "reason": None}


def _has_complete_recovery_evidence(
    current: Mapping[str, Any], observations: Mapping[str, Any]
) -> bool:
    recorded_fields = {
        key for key in CURRENT_FIELD_ORDER if current.get(key) is not None
    }
    return bool(recorded_fields) and recorded_fields.issubset(observations)


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
        if remembered_shipped is not None and candidate_shipped != remembered_shipped:
            resolved_remembered = _resolved_commit(evidence_repo, remembered_shipped)
            if resolved_remembered is None or not _is_ancestor(
                evidence_repo, resolved_remembered, resolved_shipped
            ):
                raise WorkLoopError(
                    "lastShippedSha evidence must advance to a descendant commit"
                )
        evidence_tip = remembered_head if branch_changed else candidate_head
        resolved_tip = (
            _resolved_commit(evidence_repo, evidence_tip) if evidence_tip is not None else None
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
            raise WorkLoopError("lastShippedSha evidence must belong to the shipped branch")

    return candidate


def update_evidence(
    state: dict[str, Any],
    updates: Mapping[str, Any],
    *,
    repo: Path | None = None,
) -> None:
    candidate = validated_evidence(state, updates, repo=repo)
    recovery_checkpoint_active = state["checkpoint"].get("state") in {
        "ready",
        "blocked",
    }
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
    repo: Path | None = None,
) -> None:
    contradictions: list[str] = []
    context_signal_applied = False
    current = state["current"]
    observed_phase = observations.get("phase")
    advanced_phase: str | None = None
    if observed_phase is not None:
        if observed_phase not in PHASE_ORDER:
            raise WorkLoopError(f"invalid observed phase: {observed_phase}")
        ledger_order = PHASE_ORDER[state["phase"]]
        observed_order = PHASE_ORDER[observed_phase]
        if observed_order > ledger_order and verified_live_advance:
            advanced_phase = observed_phase
        elif observed_phase != state["phase"]:
            contradictions.append(
                f"observed phase {observed_phase} differs from ledger {state['phase']}"
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
    if mismatches and verified_live_advance:
        try:
            candidate_current = validated_evidence(
                state, mismatches, repo=repo, phase=advanced_phase
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
        state["checkpoint"] = {
            "state": "blocked",
            "target": state["phase"],
            "reason": compact_text("; ".join(contradictions)),
        }
        return
    if candidate_current is not None:
        state["current"] = candidate_current
    if advanced_phase is not None:
        state["phase"] = advanced_phase
        apply_context_signal(
            state,
            "continuation-summary",
            f"verified live state advanced to {advanced_phase}",
        )
        context_signal_applied = True
    if signal:
        apply_context_signal(state, signal, f"runtime signal: {signal}")
    elif not context_signal_applied:
        has_observations = bool(evidence_observations) or observed_phase is not None
        recovery_checkpoint_active = state["checkpoint"].get("state") in {
            "ready",
            "blocked",
        }
        recovery_current = candidate_current or current
        has_complete_recovery_evidence = _has_complete_recovery_evidence(
            recovery_current, evidence_observations
        )
        may_restore_health = (
            not recovery_checkpoint_active or has_complete_recovery_evidence
        )
        if (
            has_observations and may_restore_health
        ) or state["contextHealth"]["level"] != "red":
            state["contextHealth"] = {
                "level": "green",
                "epoch": state["contextHealth"]["epoch"],
                "reasons": [],
            }
        if has_complete_recovery_evidence:
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
    reconcile.add_argument("--json", action="store_true")

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
                    repo=args.repo,
                ),
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
                if prior_phase != "checkpoint":
                    transition_state(item, "checkpoint")
                item["checkpoint"] = {
                    "state": "paused" if args.pause else "ready",
                    "target": compact_text(args.target, limit=120),
                    "reason": compact_text(args.reason),
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
                    if prior_phase != "checkpoint":
                        transition_state(item, "checkpoint")
                    item["checkpoint"] = {
                        "state": "paused",
                        "target": prior_phase,
                        "reason": compact_text(args.reason),
                    }
                    return
                item["phase"] = "stopped"
                item["checkpoint"] = {
                    "state": "completed" if args.status == "completed" else args.status,
                    "target": None,
                    "reason": compact_text(args.reason),
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
