#!/usr/bin/env python3
"""Plan and execute the exact-scope local stage consumed by ``sd-review``."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Literal, Mapping, Sequence, overload
from urllib.parse import urlsplit

from sd_ai_command_pack_lib import (
    REVIEW_FINDING_FAMILY_IDS,
    CacheSetupError,
    build_tool_environment,
)

SCHEMA_VERSION = 1
CONFIG_PATH = Path(".sd-ai-command-pack/review.json")
DEFAULT_ARTIFACT_ROOT = Path(".build/sd-review")
MAX_CONFIG_BYTES = 256 * 1024
MAX_PROVIDERS = 16
MAX_PATHS = 20_000
MAX_ARGV = 64
MAX_ARG_LENGTH = 4096
MAX_EXPANDED_ARGV_BYTES = 128 * 1024
MAX_FINDINGS = 1_000
MAX_FAMILY_AUDITS = 32
MAX_FAMILY_EXTENSIONS = 32
MAX_OUTPUT_BYTES = 4 * 1024 * 1024
MAX_TIMEOUT = 3600
GIT_TIMEOUT_SECONDS = 60
ID_RE = re.compile(r"[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*\Z")
ATTEMPT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
OID_RE = re.compile(r"[0-9a-f]{40}\Z")
SCOPES = frozenset({"changes", "branch", "codebase", "pr"})
CANONICAL_SCOPES = frozenset({"worktree", "branch_delta", "codebase"})
DATA_CLASSES = ("local", "private-network", "public-network")
COST_TIERS = ("none", "low", "medium", "high")
QUALITY_TIERS = ("basic", "standard", "deep")
SHELL_EXECUTABLES = frozenset({"bash", "dash", "fish", "ksh", "sh", "zsh"})
CODE_STRING_EXECUTABLES = frozenset(
    {"node", "nodejs", "perl", "python", "python3", "ruby"}
)
OUTCOMES = frozenset(
    {"clean", "findings", "unavailable", "failed", "cancelled", "skipped"}
)
TERMINAL_FAILURES = frozenset({"unavailable", "failed", "cancelled"})
FINDING_SEVERITY_RANK = {"unspecified": 0, "low": 1, "medium": 2, "high": 3}
FINDING_FAMILY_IDS = REVIEW_FINDING_FAMILY_IDS
FINDING_DISPOSITIONS = frozenset(
    {"outstanding", "fix", "fixed", "rebutted", "resolved"}
)
FAMILY_AUDIT_DIMENSIONS = {
    "task-metadata": (
        "identity-fields",
        "lifecycle-status",
        "parent-child-links",
        "branch-base-binding",
        "archive-journal-bundle",
    ),
    "boundary-validation": (
        "strict-types",
        "normalization",
        "persistence-invariants",
        "state-transitions",
        "replay-idempotency",
        "attempts-receipts",
        "exact-identity-head",
        "subprocess-failures",
        "permissions",
        "paths-symlinks-toctou",
        "controlled-diagnostics",
    ),
    "contract-documentation-drift": (
        "typed-contract",
        "human-output",
        "json-output",
        "help-documentation",
        "generated-adapters",
    ),
    "generated-surfaces": (
        "canonical-source",
        "generated-mirrors",
        "manifest-registration",
        "install-audit",
        "release-evidence",
    ),
    "reviewer-test-harness-quality": (
        "good-fixture",
        "base-fixture",
        "failure-fixture",
        "mutation-sentinel",
        "non-tautological-assertion",
    ),
    "other": (
        "root-cause",
        "sibling-paths",
        "sibling-transitions",
        "failure-branches",
        "generated-surfaces",
    ),
}
ACTIVE_PROCESSES: set[subprocess.Popen[bytes]] = set()
ACTIVE_PROCESSES_LOCK = threading.Lock()
CANCELLATION_EVENT = threading.Event()
CONFIG_KEYS = frozenset(
    {"schemaVersion", "providers", "policy", "remoteIntegration"}
)
PROVIDER_KEYS = frozenset(
    {
        "id",
        "adapter",
        "argv",
        "scopes",
        "dataHandling",
        "costTier",
        "qualityTier",
        "timeoutSeconds",
        "version",
        "enabled",
        "outcomeByExitCode",
    }
)
POLICY_KEYS = frozenset(
    {
        "allowedDataHandling",
        "documentation",
        "metadata",
        "requiredProviders",
    }
)
REMOTE_INTEGRATION_KEYS = frozenset(
    {
        "requirement",
        "descriptorPath",
        "receiptPolls",
        "pollSeconds",
        "roundLimit",
    }
)
SUBSTANTIVE_SUFFIXES = frozenset(
    {
        ".c",
        ".cc",
        ".cpp",
        ".cs",
        ".go",
        ".java",
        ".js",
        ".jsx",
        ".kt",
        ".mjs",
        ".php",
        ".py",
        ".rb",
        ".rs",
        ".sh",
        ".sql",
        ".swift",
        ".ts",
        ".tsx",
    }
)
DOCUMENT_SUFFIXES = frozenset({".md", ".mdx", ".rst", ".txt"})
METADATA_NAMES = frozenset(
    {
        ".gitignore",
        ".gitattributes",
        "changelog.md",
        "license",
        "license.md",
        "manifest.json",
    }
)


class ReviewInputError(ValueError):
    """A controlled invalid target, policy, or receipt condition."""


@dataclass(frozen=True)
class Provider:
    identifier: str
    adapter: str
    argv: tuple[str, ...]
    scopes: tuple[str, ...]
    data_handling: str
    cost_tier: str
    quality_tier: str
    timeout_seconds: int
    version: str
    enabled: bool
    outcome_by_exit: Mapping[int, str]


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _bounded(value: str, limit: int = 1200) -> str:
    text = " ".join(value.replace("\x00", " ").split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _default_config() -> dict[str, Any]:
    shared = {
        "scopes": ["worktree", "branch_delta", "codebase"],
        "dataHandling": "private-network",
        "qualityTier": "standard",
        "enabled": True,
    }
    return {
        "schemaVersion": 1,
        "providers": [
            {
                **shared,
                "id": "prism",
                "adapter": "prism",
                "argv": [],
                "costTier": "low",
                "timeoutSeconds": 300,
                "version": "builtin-v1",
                "outcomeByExitCode": {
                    "0": "clean",
                    "1": "findings",
                    "3": "unavailable",
                    "4": "unavailable",
                },
            },
            {
                **shared,
                "id": "gito",
                "adapter": "gito",
                "argv": [],
                "costTier": "medium",
                "timeoutSeconds": 600,
                "version": "builtin-v1",
                "outcomeByExitCode": {
                    "0": "clean",
                    "1": "findings",
                    "2": "unavailable",
                    "3": "unavailable",
                },
            },
        ],
        "policy": {
            "allowedDataHandling": list(DATA_CLASSES),
            "documentation": "cheapest",
            "metadata": "cheapest",
            "requiredProviders": [],
        },
        "remoteIntegration": {
            "requirement": "optional",
            "descriptorPath": "config/routed-review-setup-v1.json",
            "receiptPolls": 6,
            "pollSeconds": 5,
            "roundLimit": 5,
        },
    }


def _bounded_integer(
    value: object,
    *,
    field: str,
    minimum: int,
    maximum: int,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ReviewInputError(f"{field} must be an integer")
    if not minimum <= value <= maximum:
        raise ReviewInputError(f"{field} must be between {minimum} and {maximum}")
    return value


def _safe_config_path(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 240:
        raise ReviewInputError(f"{field} must be a bounded relative path")
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    if (
        path.is_absolute()
        or ".." in path.parts
        or any(not part for part in path.parts)
        or re.match(r"[A-Za-z]:", normalized)
        or normalized.startswith("//")
    ):
        raise ReviewInputError(f"{field} must stay inside the repository")
    return path.as_posix()


def _parse_remote_integration(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) - REMOTE_INTEGRATION_KEYS:
        raise ReviewInputError("remoteIntegration must use only supported fields")
    requirement = value.get("requirement", "optional")
    if requirement not in {"optional", "required"}:
        raise ReviewInputError(
            "remoteIntegration requirement must be optional or required"
        )
    return {
        "requirement": requirement,
        "descriptorPath": _safe_config_path(
            value.get("descriptorPath", "config/routed-review-setup-v1.json"),
            field="remoteIntegration descriptorPath",
        ),
        "receiptPolls": _bounded_integer(
            value.get("receiptPolls", 6),
            field="remoteIntegration receiptPolls",
            minimum=1,
            maximum=30,
        ),
        "pollSeconds": _bounded_integer(
            value.get("pollSeconds", 5),
            field="remoteIntegration pollSeconds",
            minimum=0,
            maximum=60,
        ),
        "roundLimit": _bounded_integer(
            value.get("roundLimit", 5),
            field="remoteIntegration roundLimit",
            minimum=1,
            maximum=10,
        ),
    }


def _read_json(path: Path, *, limit: int, label: str) -> object:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise ReviewInputError(f"cannot inspect {label} {path}: {error}") from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ReviewInputError(f"{label} must be a regular non-symlink file: {path}")
    if metadata.st_size > limit:
        raise ReviewInputError(f"{label} exceeds {limit} bytes: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8", errors="strict"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ReviewInputError(f"cannot read {label} {path}: {error}") from error
    return value


def _string_list(
    value: object, *, field: str, allowed: set[str] | None = None
) -> tuple[str, ...]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item or len(item) > MAX_ARG_LENGTH
        for item in value
    ):
        raise ReviewInputError(f"{field} must be a bounded string array")
    items = tuple(value)
    if allowed is not None and any(item not in allowed for item in items):
        raise ReviewInputError(f"{field} contains an unsupported value")
    if len(items) > MAX_ARGV:
        raise ReviewInputError(f"{field} exceeds {MAX_ARGV} entries")
    return items


def _parse_provider(value: object) -> Provider:
    if not isinstance(value, dict) or set(value) - PROVIDER_KEYS:
        raise ReviewInputError("provider entries must use only supported fields")
    identifier = value.get("id")
    adapter = value.get("adapter")
    version = value.get("version")
    if not isinstance(identifier, str) or not ID_RE.fullmatch(identifier):
        raise ReviewInputError("provider id is invalid")
    if adapter not in {"prism", "gito", "argv"}:
        raise ReviewInputError(f"provider {identifier} has an unsupported adapter")
    if not isinstance(version, str) or not version or len(version) > 128:
        raise ReviewInputError(f"provider {identifier} version is invalid")
    argv = _string_list(value.get("argv", []), field=f"provider {identifier} argv")
    if adapter == "argv" and not argv:
        raise ReviewInputError(f"provider {identifier} argv adapter requires argv")
    if adapter != "argv" and argv:
        raise ReviewInputError(
            f"provider {identifier} builtin adapter cannot override argv"
        )
    if any("\x00" in item for item in argv):
        raise ReviewInputError(f"provider {identifier} argv contains a NUL byte")
    executable = PurePosixPath(argv[0]).name.casefold() if argv else ""
    if executable in SHELL_EXECUTABLES and any(
        item in {"-c", "-lc"} for item in argv[1:]
    ):
        raise ReviewInputError(
            f"provider {identifier} cannot use a shell command string"
        )
    if executable in CODE_STRING_EXECUTABLES and any(
        item in {"-c", "-e", "--eval"} for item in argv[1:]
    ):
        raise ReviewInputError(
            f"provider {identifier} cannot use an inline code string"
        )
    scopes = _string_list(
        value.get("scopes"),
        field=f"provider {identifier} scopes",
        allowed=set(CANONICAL_SCOPES),
    )
    if not scopes:
        raise ReviewInputError(f"provider {identifier} scopes cannot be empty")
    data_handling = value.get("dataHandling")
    cost_tier = value.get("costTier")
    quality_tier = value.get("qualityTier")
    if data_handling not in DATA_CLASSES:
        raise ReviewInputError(f"provider {identifier} dataHandling is invalid")
    if cost_tier not in COST_TIERS:
        raise ReviewInputError(f"provider {identifier} costTier is invalid")
    if quality_tier not in QUALITY_TIERS:
        raise ReviewInputError(f"provider {identifier} qualityTier is invalid")
    timeout = value.get("timeoutSeconds")
    if (
        not isinstance(timeout, int)
        or isinstance(timeout, bool)
        or not 1 <= timeout <= MAX_TIMEOUT
    ):
        raise ReviewInputError(f"provider {identifier} timeoutSeconds is invalid")
    enabled = value.get("enabled")
    if not isinstance(enabled, bool):
        raise ReviewInputError(f"provider {identifier} enabled must be boolean")
    raw_exit = value.get("outcomeByExitCode")
    if not isinstance(raw_exit, dict) or len(raw_exit) > 32:
        raise ReviewInputError(f"provider {identifier} outcomeByExitCode is invalid")
    exit_map: dict[int, str] = {}
    for key, outcome in raw_exit.items():
        try:
            code = int(key)
        except (TypeError, ValueError):
            raise ReviewInputError(
                f"provider {identifier} outcomeByExitCode key is invalid"
            ) from None
        if str(code) != str(key) or code < 0 or code > 255 or outcome not in OUTCOMES:
            raise ReviewInputError(f"provider {identifier} exit mapping is invalid")
        exit_map[code] = str(outcome)
    if 0 not in exit_map:
        raise ReviewInputError(f"provider {identifier} must map exit code 0")
    return Provider(
        identifier,
        str(adapter),
        argv,
        scopes,
        str(data_handling),
        str(cost_tier),
        str(quality_tier),
        timeout,
        version,
        enabled,
        exit_map,
    )


def load_config(
    repo: Path,
) -> tuple[dict[str, Any], tuple[Provider, ...], dict[str, Any]]:
    path = repo / CONFIG_PATH
    value = (
        _read_json(path, limit=MAX_CONFIG_BYTES, label="review configuration")
        if path.exists()
        else _default_config()
    )
    if not isinstance(value, dict) or set(value) - CONFIG_KEYS:
        raise ReviewInputError("review configuration must use only supported fields")
    if value.get("schemaVersion") != SCHEMA_VERSION:
        raise ReviewInputError("review configuration schemaVersion must be 1")
    raw_providers = value.get("providers")
    if (
        not isinstance(raw_providers, list)
        or not 1 <= len(raw_providers) <= MAX_PROVIDERS
    ):
        raise ReviewInputError(
            "review configuration providers must be a bounded non-empty array"
        )
    providers = tuple(_parse_provider(item) for item in raw_providers)
    identifiers = [item.identifier for item in providers]
    if len(set(identifiers)) != len(identifiers):
        raise ReviewInputError("review provider ids must be unique")
    policy = value.get("policy")
    if not isinstance(policy, dict) or set(policy) - POLICY_KEYS:
        raise ReviewInputError("review policy must use only supported fields")
    allowed = _string_list(
        policy.get("allowedDataHandling"),
        field="policy allowedDataHandling",
        allowed=set(DATA_CLASSES),
    )
    if not allowed:
        raise ReviewInputError("policy allowedDataHandling cannot be empty")
    for field in ("documentation", "metadata"):
        if policy.get(field) not in {"cheapest", "skip"}:
            raise ReviewInputError(f"policy {field} must be cheapest or skip")
    required = _string_list(
        policy.get("requiredProviders"), field="policy requiredProviders"
    )
    unknown = sorted(set(required) - set(identifiers))
    if unknown:
        raise ReviewInputError(
            f"policy requiredProviders contains unknown provider {unknown[0]}"
        )
    normalized_policy = {
        **policy,
        "allowedDataHandling": list(allowed),
        "requiredProviders": list(required),
    }
    normalized_remote = _parse_remote_integration(
        value.get("remoteIntegration", {})
    )
    normalized = {
        "schemaVersion": 1,
        "providers": raw_providers,
        "policy": normalized_policy,
        "remoteIntegration": normalized_remote,
    }
    return normalized, providers, normalized_policy


@overload
def _git(repo: Path, *args: str, binary: Literal[False] = False) -> str: ...


@overload
def _git(repo: Path, *args: str, binary: Literal[True]) -> bytes: ...


def _git(repo: Path, *args: str, binary: bool = False) -> str | bytes:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=repo,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=not binary,
            check=False,
            timeout=GIT_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as error:
        stderr_value = error.stderr
        stderr_text = (
            stderr_value.decode("utf-8", "replace")
            if isinstance(stderr_value, bytes)
            else stderr_value
        )
        raise ReviewInputError(
            _bounded(
                stderr_text
                or f"git {' '.join(args)} timed out after {GIT_TIMEOUT_SECONDS}s"
            )
        ) from error
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", "replace") if binary else result.stderr
        raise ReviewInputError(
            _bounded(stderr or f"git {' '.join(args)} exited {result.returncode}")
        )
    return result.stdout


def _safe_relative(value: str) -> str:
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts or "\x00" in value:
        raise ReviewInputError("Git returned an unsafe review path")
    return str(path)


def _nul_paths(payload: bytes) -> list[str]:
    values = payload.split(b"\0")
    if values and values[-1] == b"":
        values.pop()
    if len(values) > MAX_PATHS:
        raise ReviewInputError(f"review target exceeds {MAX_PATHS} paths")
    return sorted({_safe_relative(os.fsdecode(item)) for item in values})


def _path_manifest(repo: Path, paths: Sequence[str]) -> list[dict[str, Any]]:
    manifest: list[dict[str, Any]] = []
    for value in paths:
        path = repo / value
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            manifest.append({"path": value, "kind": "deleted", "digest": None})
            continue
        if stat.S_ISLNK(metadata.st_mode):
            payload = os.fsencode(os.readlink(path))
            kind = "symlink"
        elif stat.S_ISREG(metadata.st_mode):
            payload = path.read_bytes()
            kind = "file"
        else:
            raise ReviewInputError(
                f"review path is not a regular file or symlink: {value}"
            )
        manifest.append(
            {"path": value, "kind": kind, "digest": hashlib.sha256(payload).hexdigest()}
        )
    return manifest


def _repository_identity(repo: Path) -> str:
    remotes = str(_git(repo, "remote")).splitlines()
    if "origin" not in remotes:
        return f"local:{hashlib.sha256(os.fsencode(str(repo))).hexdigest()}"
    remote = str(_git(repo, "remote", "get-url", "origin")).strip()
    scp_match = re.fullmatch(r"(?:[^@/:]+@)?([^/:]+):(.+)", remote)
    if scp_match and "://" not in remote:
        host, remote_path = scp_match.groups()
    else:
        parsed = urlsplit(remote)
        if not parsed.hostname:
            return f"remote:{hashlib.sha256(remote.encode()).hexdigest()}"
        host, remote_path = parsed.hostname, parsed.path
    if (
        len(host) == 1
        or "/" in host
        or "\\" in host
        or "\\" in remote_path
        or any(character.isspace() or ord(character) < 32 for character in host)
    ):
        return f"remote:{hashlib.sha256(remote.encode()).hexdigest()}"
    normalized_path = remote_path.strip("/")
    if normalized_path.endswith(".git"):
        normalized_path = normalized_path[:-4]
    raw_parts = normalized_path.split("/")
    if not normalized_path or any(part in {"", ".", ".."} for part in raw_parts):
        return f"remote:{hashlib.sha256(remote.encode()).hexdigest()}"
    return f"{host.casefold()}/{normalized_path}"


def resolve_target(repo: Path, scope: str, base: str, head: str) -> dict[str, Any]:
    head_oid = str(
        _git(repo, "rev-parse", "--verify", "--end-of-options", f"{head}^{{commit}}")
    ).strip()
    dirty = str(_git(repo, "status", "--porcelain=v1", "--untracked-files=all"))
    if scope in {"branch", "pr"}:
        if dirty:
            raise ReviewInputError(
                f"{scope} scope requires a clean worktree bound to one head"
            )
        base_oid = str(_git(repo, "merge-base", "--", base, head_oid)).strip()
        diff = bytes(
            _git(
                repo,
                "diff",
                "--binary",
                "--full-index",
                f"{base_oid}..{head_oid}",
                "--",
                binary=True,
            )
        )
        paths = _nul_paths(
            bytes(
                _git(
                    repo,
                    "diff",
                    "--name-only",
                    "-z",
                    f"{base_oid}..{head_oid}",
                    "--",
                    binary=True,
                )
            )
        )
        canonical_scope = "branch_delta"
        manifest = _path_manifest(repo, paths)
    elif scope == "changes":
        base_oid = head_oid
        unstaged = bytes(
            _git(repo, "diff", "--binary", "--full-index", "--", binary=True)
        )
        staged = bytes(
            _git(
                repo, "diff", "--cached", "--binary", "--full-index", "--", binary=True
            )
        )
        untracked = _nul_paths(
            bytes(
                _git(
                    repo,
                    "ls-files",
                    "--others",
                    "--exclude-standard",
                    "-z",
                    binary=True,
                )
            )
        )
        tracked = _nul_paths(
            bytes(_git(repo, "diff", "--name-only", "-z", "HEAD", "--", binary=True))
        )
        paths = sorted(set(tracked + untracked))
        manifest = _path_manifest(repo, paths)
        diff = (
            unstaged
            + b"\0STAGED\0"
            + staged
            + b"\0UNTRACKED\0"
            + _canonical_json(manifest)
        )
        canonical_scope = "worktree"
    else:
        if dirty:
            raise ReviewInputError(
                "codebase scope requires a clean worktree bound to one head"
            )
        base_oid = head_oid
        paths = _nul_paths(bytes(_git(repo, "ls-files", "-z", binary=True)))
        manifest = _path_manifest(repo, paths)
        diff = _canonical_json(manifest)
        canonical_scope = "codebase"
    target = {
        "repository": _repository_identity(repo),
        "scope": canonical_scope,
        "base": base_oid,
        "head": head_oid,
        "paths": manifest,
        "contentDigest": hashlib.sha256(diff).hexdigest(),
    }
    target["identity"] = _digest(target)
    return target


def classify_paths(paths: Sequence[str]) -> tuple[str, list[str]]:
    if not paths:
        return "metadata", ["empty-delta"]
    reasons: set[str] = set()
    only_docs = True
    only_metadata = True
    for value in paths:
        path = PurePosixPath(value)
        lowered = value.casefold()
        suffix = path.suffix.casefold()
        name = path.name.casefold()
        is_doc = suffix in DOCUMENT_SUFFIXES or lowered.startswith("docs/")
        is_metadata = (
            is_doc or name in METADATA_NAMES or lowered.startswith(".trellis/")
        )
        only_docs = only_docs and is_doc
        only_metadata = only_metadata and is_metadata
        if suffix in SUBSTANTIVE_SUFFIXES:
            reasons.add("source")
        if (
            bool({"test", "tests"} & set(path.parts))
            or name.startswith("test_")
            or name.endswith("_test.py")
        ):
            reasons.add("tests")
        if lowered.startswith((".github/workflows/", "scripts/", "installer/")):
            reasons.add("executable-configuration")
        if any(
            token in lowered
            for token in ("security", "auth", "receipt", "state", "contract")
        ):
            reasons.add("state-contract")
    if only_docs:
        return "documentation", ["documentation-only"]
    if only_metadata:
        return "metadata", ["metadata-only"]
    return "substantive", sorted(reasons or {"ambiguous"})


def _provider_row(provider: Provider) -> dict[str, Any]:
    return {
        "id": provider.identifier,
        "adapter": provider.adapter,
        "version": provider.version,
        "dataHandling": provider.data_handling,
        "costTier": provider.cost_tier,
        "qualityTier": provider.quality_tier,
        "timeoutSeconds": provider.timeout_seconds,
    }


def _validate_bookkeeping_evidence(
    path: Path | None, target: Mapping[str, Any]
) -> None:
    if path is None:
        raise ReviewInputError("bookkeeping successor requires --bookkeeping-evidence")
    value = _read_json(path, limit=64 * 1024, label="bookkeeping evidence")
    if not isinstance(value, dict) or value.get("schemaVersion") != 1:
        raise ReviewInputError("bookkeeping evidence schemaVersion must be 1")
    required = {"base", "head", "contentDigest", "classification"}
    if set(value) != required | {"schemaVersion"}:
        raise ReviewInputError("bookkeeping evidence has unsupported or missing fields")
    if value.get("classification") != "bookkeeping-successor" or any(
        value.get(key) != target.get(key) for key in ("base", "head", "contentDigest")
    ):
        raise ReviewInputError("bookkeeping evidence does not match the exact target")


def _family_id(value: object, *, field: str) -> str:
    if not isinstance(value, str) or value not in FINDING_FAMILY_IDS:
        raise ReviewInputError(f"{field} must use the bounded finding-family vocabulary")
    return value


def _safe_id(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not ATTEMPT_RE.fullmatch(value):
        raise ReviewInputError(f"{field} must be a bounded identifier")
    return value


def _full_oid(value: object, *, field: str, optional: bool = False) -> str | None:
    if optional and value is None:
        return None
    if not isinstance(value, str) or not OID_RE.fullmatch(value):
        raise ReviewInputError(f"{field} must be a full lowercase Git object ID")
    return value


def _plain_int(
    value: object, *, field: str, minimum: int = 0, maximum: int = MAX_FINDINGS
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ReviewInputError(f"{field} must be an integer")
    if not minimum <= value <= maximum:
        raise ReviewInputError(f"{field} must be between {minimum} and {maximum}")
    return value


def _bounded_strings(
    value: object, *, field: str, limit: int = MAX_FINDINGS
) -> list[str]:
    if not isinstance(value, list) or len(value) > limit:
        raise ReviewInputError(f"{field} must be a bounded string array")
    result: list[str] = []
    for item in value:
        if (
            not isinstance(item, str)
            or not item
            or len(item) > 500
            or "\x00" in item
        ):
            raise ReviewInputError(f"{field} must be a bounded string array")
        result.append(item)
    return result


def _audit_complete(audit: Mapping[str, Any]) -> bool:
    expected = set(FAMILY_AUDIT_DIMENSIONS[str(audit["family"])])
    dimensions = audit["dimensions"]
    observed = {
        str(item["id"]): str(item["status"])
        for item in dimensions
        if isinstance(item, Mapping)
    }
    return (
        audit["localOutcome"] == "clean"
        and not audit["localLimitations"]
        and audit["checkStatus"] == "passed"
        and audit["head"] == audit["localHead"] == audit["checkHead"]
        and set(observed) == expected
        and set(observed.values()) <= {"covered", "not-applicable"}
        and len(audit["siblingFindingIds"]) >= 2
        and audit["batchSize"] == len(audit["siblingFindingIds"])
        and len(audit["fixCommits"]) <= 1
    )


def _parse_family_finding(value: object, *, current_round: int) -> dict[str, Any]:
    keys = {
        "id",
        "provider",
        "round",
        "head",
        "family",
        "actionable",
        "disposition",
        "fixCommit",
        "siblingAuditId",
    }
    if not isinstance(value, dict) or set(value) != keys:
        raise ReviewInputError("family finding has unsupported or missing fields")
    disposition = value["disposition"]
    if disposition not in FINDING_DISPOSITIONS:
        raise ReviewInputError("family finding disposition is unsupported")
    actionable = value["actionable"]
    if not isinstance(actionable, bool):
        raise ReviewInputError("family finding actionable must be boolean")
    if not actionable and disposition in {"outstanding", "fix"}:
        raise ReviewInputError(
            "a non-actionable family finding cannot remain outstanding or selected for fix"
        )
    fix_commit = _full_oid(
        value["fixCommit"], field="family finding fixCommit", optional=True
    )
    if disposition == "fixed" and fix_commit is None:
        raise ReviewInputError("a fixed family finding requires fixCommit")
    audit_id = value["siblingAuditId"]
    if audit_id is not None:
        audit_id = _safe_id(audit_id, field="family finding siblingAuditId")
    return {
        "id": _safe_id(value["id"], field="family finding id"),
        "provider": _safe_id(value["provider"], field="family finding provider"),
        "round": _plain_int(
            value["round"], field="family finding round", minimum=1, maximum=current_round
        ),
        "head": _full_oid(value["head"], field="family finding head"),
        "family": _family_id(value["family"], field="family finding family"),
        "actionable": actionable,
        "disposition": disposition,
        "fixCommit": fix_commit,
        "siblingAuditId": audit_id,
    }


def _parse_family_audit(value: object, *, current_round: int) -> dict[str, Any]:
    keys = {
        "id",
        "family",
        "round",
        "head",
        "localReceiptId",
        "localHead",
        "localOutcome",
        "localLimitations",
        "checkHead",
        "checkStatus",
        "batchSize",
        "fixCommits",
        "siblingFindingIds",
        "dimensions",
    }
    if not isinstance(value, dict) or set(value) != keys:
        raise ReviewInputError("family audit has unsupported or missing fields")
    family = _family_id(value["family"], field="family audit family")
    outcome = value["localOutcome"]
    if outcome not in OUTCOMES - {"skipped"}:
        raise ReviewInputError("family audit localOutcome is unsupported")
    check_status = value["checkStatus"]
    if check_status not in {"passed", "failed", "unavailable"}:
        raise ReviewInputError("family audit checkStatus is unsupported")
    receipt_id = value["localReceiptId"]
    if not isinstance(receipt_id, str) or not re.fullmatch(r"[0-9a-f]{64}", receipt_id):
        raise ReviewInputError("family audit localReceiptId must be a SHA-256 digest")
    fix_commits = value["fixCommits"]
    if not isinstance(fix_commits, list) or len(fix_commits) > 1:
        raise ReviewInputError("family audit permits at most one fix commit")
    normalized_commits = [
        _full_oid(item, field="family audit fix commit") for item in fix_commits
    ]
    sibling_ids = _bounded_strings(
        value["siblingFindingIds"], field="family audit siblingFindingIds"
    )
    if len(sibling_ids) != len(set(sibling_ids)) or any(
        not ATTEMPT_RE.fullmatch(item) for item in sibling_ids
    ):
        raise ReviewInputError("family audit siblingFindingIds must be unique identifiers")
    dimensions = value["dimensions"]
    if not isinstance(dimensions, list) or len(dimensions) > 32:
        raise ReviewInputError("family audit dimensions must be a bounded array")
    expected = set(FAMILY_AUDIT_DIMENSIONS[family])
    normalized_dimensions: list[dict[str, str]] = []
    seen_dimensions: set[str] = set()
    for item in dimensions:
        if not isinstance(item, dict) or set(item) != {"id", "status"}:
            raise ReviewInputError("family audit dimension is malformed")
        identifier = item["id"]
        status_value = item["status"]
        if identifier not in expected or identifier in seen_dimensions:
            raise ReviewInputError("family audit dimension is unknown or duplicated")
        if status_value not in {"covered", "not-applicable", "missing"}:
            raise ReviewInputError("family audit dimension status is unsupported")
        seen_dimensions.add(identifier)
        normalized_dimensions.append({"id": identifier, "status": status_value})
    normalized_dimensions.sort(key=lambda item: item["id"])
    return {
        "id": _safe_id(value["id"], field="family audit id"),
        "family": family,
        "round": _plain_int(
            value["round"], field="family audit round", minimum=1, maximum=current_round
        ),
        "head": _full_oid(value["head"], field="family audit head"),
        "localReceiptId": receipt_id,
        "localHead": _full_oid(value["localHead"], field="family audit localHead"),
        "localOutcome": outcome,
        "localLimitations": _bounded_strings(
            value["localLimitations"], field="family audit localLimitations", limit=32
        ),
        "checkHead": _full_oid(value["checkHead"], field="family audit checkHead"),
        "checkStatus": check_status,
        "batchSize": _plain_int(value["batchSize"], field="family audit batchSize"),
        "fixCommits": normalized_commits,
        "siblingFindingIds": sibling_ids,
        "dimensions": normalized_dimensions,
    }


def _parse_family_extension(value: object, *, current_round: int) -> dict[str, Any]:
    keys = {"family", "afterRound", "decisionId", "approved"}
    if not isinstance(value, dict) or set(value) != keys:
        raise ReviewInputError("family extension has unsupported or missing fields")
    if value["decisionId"] != "review.round-extension" or value["approved"] is not True:
        raise ReviewInputError("family extension requires an approved review.round-extension decision")
    return {
        "family": _family_id(value["family"], field="family extension family"),
        "afterRound": _plain_int(
            value["afterRound"],
            field="family extension afterRound",
            minimum=1,
            maximum=current_round,
        ),
        "decisionId": "review.round-extension",
        "approved": True,
    }


def _family_gate(path: Path | None, target: Mapping[str, Any]) -> dict[str, Any]:
    if path is None:
        return {
            "schemaVersion": 1,
            "state": "inactive",
            "exactHead": target["head"],
            "currentRound": 0,
            "repeatedFamilies": [],
            "families": [],
            "roundsAvoided": 0,
            "siblingFindings": 0,
            "batchSize": 0,
        }
    raw = _read_json(path, limit=512 * 1024, label="family evidence")
    keys = {
        "schemaVersion",
        "lifecycleId",
        "currentRound",
        "currentHead",
        "blockedRedispatches",
        "findings",
        "audits",
        "extensions",
    }
    if not isinstance(raw, dict) or set(raw) != keys or raw.get("schemaVersion") != 1:
        raise ReviewInputError("family evidence must use the exact schemaVersion 1 contract")
    _safe_id(raw["lifecycleId"], field="family evidence lifecycleId")
    current_round = _plain_int(
        raw["currentRound"], field="family evidence currentRound", minimum=1
    )
    current_head = _full_oid(raw["currentHead"], field="family evidence currentHead")
    if current_head != target["head"]:
        raise ReviewInputError("family evidence does not match the exact review head")
    findings_value = raw["findings"]
    audits_value = raw["audits"]
    extensions_value = raw["extensions"]
    if not isinstance(findings_value, list) or len(findings_value) > MAX_FINDINGS:
        raise ReviewInputError("family evidence findings must be a bounded array")
    if not isinstance(audits_value, list) or len(audits_value) > MAX_FAMILY_AUDITS:
        raise ReviewInputError("family evidence audits must be a bounded array")
    if not isinstance(extensions_value, list) or len(extensions_value) > MAX_FAMILY_EXTENSIONS:
        raise ReviewInputError("family evidence extensions must be a bounded array")
    findings = [
        _parse_family_finding(item, current_round=current_round)
        for item in findings_value
    ]
    audits = [
        _parse_family_audit(item, current_round=current_round) for item in audits_value
    ]
    extensions = [
        _parse_family_extension(item, current_round=current_round)
        for item in extensions_value
    ]
    if len({item["id"] for item in findings}) != len(findings):
        raise ReviewInputError("family finding ids must be unique")
    if len({item["id"] for item in audits}) != len(audits):
        raise ReviewInputError("family audit ids must be unique")
    audit_by_id = {str(item["id"]): item for item in audits}
    for finding in findings:
        audit_id = finding["siblingAuditId"]
        if audit_id is not None and (
            audit_id not in audit_by_id
            or audit_by_id[audit_id]["family"] != finding["family"]
        ):
            raise ReviewInputError(
                "family finding siblingAuditId must reference an audit for the same family"
            )
    extension_keys = {
        (str(item["family"]), int(item["afterRound"])) for item in extensions
    }
    if len(extension_keys) != len(extensions):
        raise ReviewInputError("family extensions must be unique per family and round")
    family_rows: list[dict[str, Any]] = []
    for family in FINDING_FAMILY_IDS:
        observations = [
            item for item in findings if item["family"] == family and item["actionable"]
        ]
        rounds = sorted({int(item["round"]) for item in observations})
        if not rounds:
            continue
        repeated = len(rounds) >= 2
        complete_audits = sorted(
            (
                item
                for item in audits
                if item["family"] == family
                and _audit_complete(item)
                and (not repeated or item["round"] >= rounds[1])
            ),
            key=lambda item: (item["round"], item["id"]),
        )
        audit = complete_audits[-1] if complete_audits else None
        state = "observed"
        if repeated and audit is None:
            state = "sibling-audit-required"
        elif repeated and audit is not None and rounds[-1] > int(audit["round"]):
            extended = any(
                item["family"] == family and item["afterRound"] == rounds[-1]
                for item in extensions
            )
            state = "redispatch-eligible" if extended else "round-extension-required"
        elif repeated:
            state = "redispatch-eligible"
        dimension_status = {
            str(item["id"]): str(item["status"])
            for item in (audit["dimensions"] if audit is not None else [])
        }
        family_rows.append(
            {
                "family": family,
                "state": state,
                "observationCount": len(observations),
                "rounds": rounds,
                "auditId": audit["id"] if audit is not None else None,
                "auditComplete": audit is not None,
                "siblingFindings": len(audit["siblingFindingIds"]) if audit else 0,
                "batchSize": int(audit["batchSize"]) if audit else 0,
                "checklist": [
                    {
                        "id": identifier,
                        "status": dimension_status.get(identifier, "required"),
                    }
                    for identifier in FAMILY_AUDIT_DIMENSIONS[family]
                ],
            }
        )
    states = {row["state"] for row in family_rows}
    state = (
        "round-extension-required"
        if "round-extension-required" in states
        else "sibling-audit-required"
        if "sibling-audit-required" in states
        else "redispatch-eligible"
        if "redispatch-eligible" in states
        else "observed"
    )
    repeated_families = [
        str(row["family"])
        for row in family_rows
        if len(row["rounds"]) >= 2
    ]
    return {
        "schemaVersion": 1,
        "state": state,
        "exactHead": target["head"],
        "currentRound": current_round,
        "repeatedFamilies": repeated_families,
        "families": family_rows,
        "roundsAvoided": _plain_int(
            raw["blockedRedispatches"], field="family evidence blockedRedispatches"
        ),
        "siblingFindings": sum(int(row["siblingFindings"]) for row in family_rows),
        "batchSize": sum(int(row["batchSize"]) for row in family_rows),
    }


def build_plan(
    *,
    providers: Sequence[Provider],
    policy: Mapping[str, Any],
    target: Mapping[str, Any],
    local: str,
    local_policy: str,
    fix_policy: str,
    successor: str,
    finding_families: Sequence[str],
    family_gate: Mapping[str, Any],
    bookkeeping_evidence: Path | None,
    configuration_digest: str,
) -> dict[str, Any]:
    normalized_families = sorted(set(finding_families))
    if any(item not in FINDING_FAMILY_IDS for item in normalized_families):
        raise ReviewInputError("finding family ids must use the bounded vocabulary")
    if len(normalized_families) > 32:
        raise ReviewInputError("finding family input exceeds 32 entries")
    if successor == "repeated-family" and not normalized_families:
        raise ReviewInputError("repeated-family successor requires --finding-family")
    path_values = [str(row["path"]) for row in target["paths"] if isinstance(row, dict)]
    risk_class, reasons = classify_paths(path_values)
    allowed = set(policy["allowedDataHandling"])
    eligible = [
        provider
        for provider in providers
        if provider.enabled
        and str(target["scope"]) in provider.scopes
        and provider.data_handling in allowed
    ]
    by_id = {provider.identifier: provider for provider in eligible}
    required = tuple(str(item) for item in policy["requiredProviders"])
    missing_required = [
        identifier for identifier in required if identifier not in by_id
    ]
    if missing_required:
        raise ReviewInputError(
            f"required local provider is ineligible: {missing_required[0]}"
        )
    selected: list[Provider]
    policy_id: str
    if local == "none":
        if required or local_policy == "required":
            raise ReviewInputError(
                "local=none conflicts with required local review policy"
            )
        selected, policy_id = [], "explicit-none"
    elif local == "all":
        selected, policy_id = eligible, "explicit-all"
    elif local != "auto":
        if local not in by_id:
            raise ReviewInputError(
                f"requested local provider is unavailable or ineligible: {local}"
            )
        selected, policy_id = [by_id[local]], "explicit-provider"
    elif successor == "bookkeeping":
        _validate_bookkeeping_evidence(bookkeeping_evidence, target)
        selected, policy_id = [], "bookkeeping-successor"
    elif successor == "low-risk":
        selected = sorted(
            eligible,
            key=lambda item: (COST_TIERS.index(item.cost_tier), item.identifier),
        )[:1]
        policy_id = "low-risk-successor"
    elif (
        successor in {"high-risk", "repeated-family"}
        or risk_class == "substantive"
        or "ambiguous" in reasons
    ):
        selected = [
            provider
            for provider in eligible
            if provider.identifier in {"prism", "gito"}
        ]
        policy_id = (
            "repeated-family"
            if successor == "repeated-family"
            else "substantive-ensemble"
        )
    elif policy[risk_class] == "skip":
        selected, policy_id = [], f"{risk_class}-skip"
    else:
        selected = sorted(
            eligible,
            key=lambda item: (COST_TIERS.index(item.cost_tier), item.identifier),
        )[:1]
        policy_id = f"{risk_class}-cheapest"
    selected_ids = {provider.identifier for provider in selected}
    selected.extend(by_id[item] for item in required if item not in selected_ids)
    selected = sorted(
        {item.identifier: item for item in selected}.values(),
        key=lambda item: item.identifier,
    )
    if not selected and policy_id not in {
        "explicit-none",
        "bookkeeping-successor",
        "documentation-skip",
        "metadata-skip",
    }:
        raise ReviewInputError(
            "no eligible local review provider satisfies the selected policy"
        )
    plan = {
        "schemaVersion": 1,
        "scope": target["scope"],
        "riskClass": risk_class,
        "riskReasons": reasons,
        "providers": [_provider_row(item) for item in selected],
        "execution": "parallel"
        if len(selected) > 1
        else "serial"
        if selected
        else "skipped",
        "policyId": policy_id,
        "successor": successor,
        "findingFamilies": normalized_families,
        "familyGate": dict(family_gate),
        "localPolicy": local_policy,
        "fixPolicy": fix_policy,
        "configurationDigest": configuration_digest,
    }
    plan["policyDigest"] = _digest(plan)
    return plan


def _artifact_root(repo: Path, value: str | None) -> Path:
    raw = Path(value) if value else DEFAULT_ARTIFACT_ROOT
    path = raw if raw.is_absolute() else repo / raw
    try:
        lexical = path.relative_to(repo)
        if ".." in lexical.parts:
            raise ValueError("artifact root contains parent traversal")
        resolved = path.resolve(strict=False)
        resolved.relative_to(repo)
    except (OSError, ValueError) as error:
        raise ReviewInputError(
            "review artifact root must stay inside the repository"
        ) from error
    if resolved == repo or ".git" in resolved.relative_to(repo).parts:
        raise ReviewInputError("review artifact root cannot be the repository or .git")
    current = repo
    for part in lexical.parts:
        current /= part
        if current.is_symlink():
            raise ReviewInputError(
                f"review artifact root cannot traverse a symlink: {current}"
            )
    result = subprocess.run(
        ["git", "check-ignore", "-q", "--", str(resolved.relative_to(repo))],
        cwd=repo,
        check=False,
    )
    if result.returncode != 0:
        raise ReviewInputError("review artifact root must be ignored by Git")
    resolved.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.name != "nt":
        resolved.chmod(0o700)
    return resolved


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(temporary, flags, 0o600)
    try:
        try:
            handle = os.fdopen(descriptor, "w", encoding="utf-8")
        except BaseException:
            os.close(descriptor)
            raise
        with handle:
            json.dump(value, handle, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _expand_argv(
    provider: Provider,
    target: Mapping[str, Any],
    attempt_dir: Path,
    context_path: Path,
    repo: Path,
) -> list[str]:
    paths = [str(row["path"]) for row in target["paths"] if isinstance(row, dict)]
    path_csv = ",".join(paths)
    scope = str(target["scope"])
    if provider.adapter == "prism":
        if scope == "branch_delta":
            result = [
                "prism",
                "review",
                "range",
                f"{target['base']}..{target['head']}",
                "--format",
                "json",
            ]
        elif scope == "codebase":
            result = ["prism", "review", "codebase", "--format", "json"]
        else:
            if any("," in path for path in paths):
                raise ReviewInputError(
                    "Prism worktree review cannot safely encode a path containing a comma"
                )
            result = [
                "prism",
                "review",
                "codebase",
                "--paths",
                path_csv,
                "--format",
                "json",
            ]
    elif provider.adapter == "gito":
        output = str(attempt_dir / "provider-output")
        if scope == "codebase":
            result = [
                "gito",
                "review",
                "--all",
                "--path",
                str(repo),
                "--out",
                output,
            ]
        else:
            result = [
                "gito",
                "review",
                "--vs",
                str(target["base"]),
                "--out",
                output,
            ]
    else:
        if any("," in path for path in paths) and any(
            "{paths}" in item for item in provider.argv
        ):
            raise ReviewInputError(
                f"provider {provider.identifier} cannot safely encode a path containing a comma"
            )
        substitutions = {
            "{repo}": str(repo),
            "{base}": str(target["base"]),
            "{head}": str(target["head"]),
            "{paths}": path_csv,
            "{artifact}": str(attempt_dir),
            "{context}": str(context_path),
        }
        result = []
        for item in provider.argv:
            for marker, replacement in substitutions.items():
                item = item.replace(marker, replacement)
            result.append(item)
    if (
        any(len(item) > MAX_EXPANDED_ARGV_BYTES for item in result)
        or sum(len(os.fsencode(item)) + 1 for item in result) > MAX_EXPANDED_ARGV_BYTES
    ):
        raise ReviewInputError(
            f"provider {provider.identifier} expanded argv exceeds {MAX_EXPANDED_ARGV_BYTES} bytes"
        )
    return result


def _terminate(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGTERM)
        else:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                check=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
        process.wait(timeout=5)
    except (OSError, ProcessLookupError, subprocess.TimeoutExpired):
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        except (OSError, ProcessLookupError):
            pass
        try:
            process.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            pass


def _cancel_active_processes() -> None:
    with ACTIVE_PROCESSES_LOCK:
        processes = tuple(ACTIVE_PROCESSES)
    for process in processes:
        _terminate(process)


def _handle_termination(signum: int, _frame: object) -> None:
    del signum
    CANCELLATION_EVENT.set()
    _cancel_active_processes()


def _parse_json_payload(payload: bytes) -> object | None:
    if len(payload) > MAX_OUTPUT_BYTES:
        return None
    try:
        return json.loads(payload.decode("utf-8", "strict"))
    except (UnicodeError, json.JSONDecodeError):
        return None


def _parse_argv_payload(stdout: bytes) -> dict[str, Any] | None:
    value = _parse_json_payload(stdout)
    if not isinstance(value, dict) or value.get("status") not in OUTCOMES:
        return None
    findings = value.get("findings", [])
    if not isinstance(findings, list) or len(findings) > MAX_FINDINGS:
        return None
    return value


def _prism_payload(stdout: bytes) -> dict[str, Any] | None:
    value = _parse_json_payload(stdout)
    if not isinstance(value, dict) or not isinstance(value.get("findings"), list):
        return None
    raw_findings = value["findings"]
    if len(raw_findings) > MAX_FINDINGS:
        return None
    findings: list[dict[str, Any]] = []
    for raw in raw_findings:
        if not isinstance(raw, dict):
            return None
        locations = raw.get("locations")
        location = locations[0] if isinstance(locations, list) and locations else {}
        if not isinstance(location, dict):
            location = {}
        lines = location.get("lines")
        line = lines.get("start") if isinstance(lines, dict) else None
        findings.append(
            {
                "path": location.get("path"),
                "line": line,
                "severity": raw.get("severity"),
                "summary": raw.get("title") or raw.get("message"),
                "family": raw.get("category"),
            }
        )
    return {"status": "findings" if findings else "clean", "findings": findings}


def _gito_payload(attempt_dir: Path) -> dict[str, Any] | None:
    path = attempt_dir / "provider-output" / "code-review-report.json"
    try:
        value = _read_json(path, limit=MAX_OUTPUT_BYTES, label="Gito report")
    except ReviewInputError:
        return None
    if not isinstance(value, dict) or not isinstance(value.get("issues"), dict):
        return None
    raw_total = value.get("total_issues")
    if (
        not isinstance(raw_total, int)
        or isinstance(raw_total, bool)
        or not 0 <= raw_total <= MAX_FINDINGS
    ):
        return None
    findings: list[dict[str, Any]] = []
    for group_path, raw_group in value["issues"].items():
        if not isinstance(group_path, str) or not isinstance(raw_group, list):
            return None
        for raw in raw_group:
            if not isinstance(raw, dict) or len(findings) >= MAX_FINDINGS:
                return None
            affected = raw.get("affected_lines")
            location = affected[0] if isinstance(affected, list) and affected else {}
            if not isinstance(location, dict):
                location = {}
            severity = raw.get("severity")
            if isinstance(severity, int) and not isinstance(severity, bool):
                severity_name = {1: "low", 2: "medium", 3: "high"}.get(severity)
                if severity_name is None:
                    return None
            elif severity is None:
                severity_name = "unspecified"
            elif isinstance(severity, str):
                severity_name = severity.casefold()
                if severity_name not in FINDING_SEVERITY_RANK:
                    return None
            else:
                return None
            tags = raw.get("tags")
            family = tags[0] if isinstance(tags, list) and tags else "other"
            findings.append(
                {
                    "path": raw.get("file") or group_path,
                    "line": location.get("start_line"),
                    "severity": severity_name,
                    "summary": raw.get("title") or raw.get("details"),
                    "family": family,
                }
            )
    if raw_total != len(findings):
        return None
    return {"status": "findings" if findings else "clean", "findings": findings}


def _parse_provider_payload(
    provider: Provider, stdout: bytes, attempt_dir: Path
) -> dict[str, Any] | None:
    if provider.adapter == "prism":
        return _prism_payload(stdout)
    if provider.adapter == "gito":
        return _gito_payload(attempt_dir)
    return _parse_argv_payload(stdout)


def _bounded_provider_findings(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    findings: list[dict[str, Any]] = []
    for raw in value[:MAX_FINDINGS]:
        if not isinstance(raw, dict):
            continue
        raw_path = str(raw.get("path") or "")
        try:
            path = _safe_relative(raw_path) if raw_path else ""
        except ReviewInputError:
            path = ""
        raw_line = raw.get("line")
        findings.append(
            {
                "path": _bounded(path, 500),
                "line": raw_line
                if isinstance(raw_line, int)
                and not isinstance(raw_line, bool)
                and raw_line > 0
                else None,
                "severity": _bounded(str(raw.get("severity") or "unspecified"), 40),
                "summary": _bounded(str(raw.get("summary") or "provider finding"), 500),
                "family": _bounded(str(raw.get("family") or "other"), 80),
                "disposition": "outstanding",
            }
        )
    return findings


def _run_provider(
    provider: Provider,
    *,
    argv: Sequence[str],
    repo: Path,
    run_dir: Path,
    environment: Mapping[str, str],
) -> dict[str, Any]:
    attempt_dir = run_dir / provider.identifier
    attempt_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
    started = time.time()
    base = {
        "provider": _provider_row(provider),
        "startedAt": started,
        "artifact": str(attempt_dir.relative_to(run_dir.parent.parent)),
    }
    _atomic_json(attempt_dir / "attempt.json", {**base, "status": "running"})
    if CANCELLATION_EVENT.is_set():
        result = {
            **base,
            "status": "cancelled",
            "exitCode": None,
            "durationMs": 0,
            "diagnostic": "provider cancelled before start",
            "findings": [],
        }
        _atomic_json(attempt_dir / "attempt.json", result)
        return result
    executable = shutil.which(argv[0], path=environment.get("PATH"))
    if executable is None:
        result = {
            **base,
            "status": "unavailable",
            "exitCode": None,
            "durationMs": 0,
            "diagnostic": f"{argv[0]} is not available",
            "findings": [],
        }
        _atomic_json(attempt_dir / "attempt.json", result)
        return result
    stdout = b""
    stderr = b""
    exit_code: int | None = None
    status_value = "failed"
    process: subprocess.Popen[bytes] | None = None
    try:
        with (
            tempfile.TemporaryFile(mode="w+b", dir=attempt_dir) as stdout_stream,
            tempfile.TemporaryFile(mode="w+b", dir=attempt_dir) as stderr_stream,
        ):
            try:
                process = subprocess.Popen(
                    list(argv),
                    cwd=repo,
                    env=dict(environment),
                    stdin=subprocess.DEVNULL,
                    stdout=stdout_stream,
                    stderr=stderr_stream,
                    start_new_session=os.name == "posix",
                )
                with ACTIVE_PROCESSES_LOCK:
                    ACTIVE_PROCESSES.add(process)
                if CANCELLATION_EVENT.is_set():
                    _terminate(process)
                try:
                    process.communicate(timeout=provider.timeout_seconds)
                    exit_code = process.returncode
                    status_value = provider.outcome_by_exit.get(exit_code, "failed")
                except subprocess.TimeoutExpired:
                    _terminate(process)
                    try:
                        process.communicate(timeout=5)
                    except subprocess.TimeoutExpired:
                        stderr += b"\nprovider process did not terminate after timeout"
                    exit_code = 124
                    status_value = "failed"
                    stderr += (
                        f"\nprovider timed out after {provider.timeout_seconds}s".encode()
                    )
                if CANCELLATION_EVENT.is_set():
                    status_value = "cancelled"
            except OSError as error:
                if process is not None and process.poll() is None:
                    _terminate(process)
                stderr += str(error).encode()
                status_value = (
                    "cancelled" if CANCELLATION_EVENT.is_set() else "failed"
                )
            finally:
                if process is not None:
                    with ACTIVE_PROCESSES_LOCK:
                        ACTIVE_PROCESSES.discard(process)
            stdout_stream.seek(0)
            stdout = stdout_stream.read(MAX_OUTPUT_BYTES)
            stderr_stream.seek(0)
            stderr = (stderr_stream.read(MAX_OUTPUT_BYTES) + stderr)[
                :MAX_OUTPUT_BYTES
            ]
    except OSError as error:
        stderr = (stderr + str(error).encode())[:MAX_OUTPUT_BYTES]
        status_value = (
            "cancelled" if CANCELLATION_EVENT.is_set() else "failed"
        )
    payload = _parse_provider_payload(provider, stdout, attempt_dir)
    findings = (
        _bounded_provider_findings(payload.get("findings", [])) if payload else []
    )
    if payload is not None:
        payload_status = str(payload["status"])
        if status_value not in TERMINAL_FAILURES:
            status_value = (
                "findings"
                if findings
                or status_value == "findings"
                or payload_status == "findings"
                else payload_status
            )
    elif exit_code == 0:
        status_value = "failed"
        stderr += b"\nprovider did not produce a valid structured review report"
    (attempt_dir / "stdout.txt").write_bytes(stdout)
    (attempt_dir / "stderr.txt").write_bytes(stderr[:MAX_OUTPUT_BYTES])
    result = {
        **base,
        "status": status_value,
        "exitCode": exit_code,
        "durationMs": max(0, int((time.time() - started) * 1000)),
        "diagnostic": _bounded(
            stderr.decode("utf-8", "replace")
            or stdout.decode("utf-8", "replace")
            or status_value
        ),
        "findings": findings,
    }
    _atomic_json(attempt_dir / "attempt.json", result)
    return result


def _normalize_findings(
    attempts: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    for attempt in attempts:
        provider = attempt.get("provider")
        provider_id = provider.get("id") if isinstance(provider, dict) else "unknown"
        findings = attempt.get("findings", [])
        if not isinstance(findings, list):
            continue
        for raw in findings:
            if not isinstance(raw, dict):
                continue
            summary = _bounded(str(raw.get("summary") or "provider finding"), 500)
            path = _bounded(str(raw.get("path") or ""), 500)
            line = raw.get("line") if isinstance(raw.get("line"), int) else None
            severity = _bounded(str(raw.get("severity") or "unspecified"), 40)
            source_family = _bounded(str(raw.get("family") or "other"), 80) or "other"
            family = (
                source_family if source_family in FINDING_FAMILY_IDS else "other"
            )
            key = _digest({"path": path, "line": line, "summary": summary.casefold()})
            row = groups.setdefault(
                key,
                {
                    "id": key[:16],
                    "path": path or None,
                    "line": line,
                    "severity": severity,
                    "summary": summary,
                    "family": family,
                    "families": [family],
                    "sourceFamilies": [source_family],
                    "disposition": "outstanding",
                    "providers": [],
                },
            )
            if FINDING_SEVERITY_RANK.get(severity, 0) > FINDING_SEVERITY_RANK.get(
                str(row["severity"]), 0
            ):
                row["severity"] = severity
            families = row["families"]
            if isinstance(families, list) and family not in families:
                families.append(family)
                families.sort()
                row["family"] = families[0]
            source_families = row["sourceFamilies"]
            if (
                isinstance(source_families, list)
                and source_family not in source_families
            ):
                source_families.append(source_family)
                source_families.sort()
            providers = row["providers"]
            if isinstance(providers, list) and provider_id not in providers:
                providers.append(provider_id)
                providers.sort()
    return sorted(
        groups.values(),
        key=lambda row: (str(row["path"]), int(row["line"] or 0), str(row["id"])),
    )


def _aggregate_outcome(attempts: Sequence[Mapping[str, Any]]) -> str:
    if not attempts:
        return "skipped"
    statuses = {str(item.get("status")) for item in attempts}
    for status_value in ("findings", "failed", "unavailable", "cancelled"):
        if status_value in statuses:
            return status_value
    if statuses <= {"clean", "skipped"}:
        return "clean" if "clean" in statuses else "skipped"
    return "failed"


def _remote_gate(
    outcome: str,
    outstanding: int,
    local_policy: str,
    family_gate: Mapping[str, Any],
) -> dict[str, Any]:
    if outstanding or outcome == "findings":
        return {"state": "blocked", "reason": "actionable-local-findings"}
    family_state = family_gate.get("state")
    if family_state in {"sibling-audit-required", "round-extension-required"}:
        return {"state": "blocked", "reason": family_state}
    if outcome in TERMINAL_FAILURES:
        return {
            "state": "blocked"
            if local_policy == "required"
            else "eligible-with-limitations",
            "reason": "required-local-review-failed"
            if local_policy == "required"
            else "local-review-limited",
        }
    return {"state": "eligible", "reason": "local-stage-terminal"}


def _receipt_identity(target: Mapping[str, Any], plan: Mapping[str, Any]) -> str:
    return _digest(
        {
            "schemaVersion": 1,
            "target": target,
            "policyDigest": plan["policyDigest"],
            "providers": plan["providers"],
        }
    )


def _validate_reusable(
    value: object,
    *,
    target: Mapping[str, Any],
    plan: Mapping[str, Any],
    identity: str,
) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    if value.get("schemaVersion") != 1 or value.get("receiptId") != identity:
        return None
    if value.get("target") != target or value.get("plan") != plan:
        return None
    if value.get("outcome") not in OUTCOMES:
        return None
    return value


def execute(
    *,
    repo: Path,
    artifact_root: Path,
    attempt_id: str,
    target: Mapping[str, Any],
    plan: Mapping[str, Any],
    providers: Sequence[Provider],
    local_policy: str,
    fix_policy: str,
    allow_reuse: bool,
) -> tuple[dict[str, Any], bool]:
    identity = _receipt_identity(target, plan)
    receipt_path = artifact_root / "receipts" / f"{identity}.json"
    if allow_reuse and receipt_path.exists():
        value = _read_json(
            receipt_path, limit=2 * 1024 * 1024, label="local review receipt"
        )
        reusable = _validate_reusable(
            value, target=target, plan=plan, identity=identity
        )
        if reusable is None:
            raise ReviewInputError(
                "stored local review receipt failed exact-match validation"
            )
        return reusable, True
    run_dir = artifact_root / "runs" / attempt_id
    selected_ids = [
        str(row["id"]) for row in plan["providers"] if isinstance(row, dict)
    ]
    selected = [
        provider for provider in providers if provider.identifier in selected_ids
    ]
    context_path = run_dir / "review-context.json"
    commands = {
        provider.identifier: _expand_argv(
            provider,
            target,
            run_dir / provider.identifier,
            context_path,
            repo,
        )
        for provider in selected
    }
    try:
        run_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
    except FileExistsError:
        raise ReviewInputError(
            f"attempt {attempt_id} already exists without a reusable exact receipt; reconcile it before retrying"
        ) from None
    _atomic_json(
        run_dir / "invocation.json",
        {"schemaVersion": 1, "attemptId": attempt_id, "target": target, "plan": plan},
    )
    _atomic_json(
        context_path,
        {
            "schemaVersion": 1,
            "targetIdentity": target["identity"],
            "riskClass": plan["riskClass"],
            "riskReasons": plan["riskReasons"],
            "findingFamilies": plan["findingFamilies"],
            "confidenceCredit": {"granted": False},
        },
    )
    if selected:
        try:
            environment, _, _ = build_tool_environment(repo=repo)
        except CacheSetupError as error:
            raise ReviewInputError(str(error)) from error
        with ThreadPoolExecutor(
            max_workers=len(selected), thread_name_prefix="sd-review"
        ) as pool:
            futures = [
                pool.submit(
                    _run_provider,
                    provider,
                    argv=commands[provider.identifier],
                    repo=repo,
                    run_dir=run_dir,
                    environment=environment,
                )
                for provider in selected
            ]
            attempts = [future.result() for future in futures]
    else:
        attempts = []
    attempts.sort(key=lambda item: str(item["provider"]["id"]))
    findings = _normalize_findings(attempts)
    outstanding = sum(1 for item in findings if item["disposition"] == "outstanding")
    outcome = _aggregate_outcome(attempts)
    limitations = [
        f"{item['provider']['id']}:{item['status']}"
        for item in attempts
        if item["status"] in TERMINAL_FAILURES
    ]
    receipt = {
        "schemaVersion": 1,
        "receiptId": identity,
        "attemptId": attempt_id,
        "target": target,
        "plan": plan,
        "outcome": outcome,
        "attempts": attempts,
        "findings": findings,
        "disposition": {
            "outstanding": outstanding,
            "fixPolicy": fix_policy,
            "maximumFixCommitsBeforeRemote": 1,
        },
        "remoteGate": _remote_gate(
            outcome, outstanding, local_policy, plan["familyGate"]
        ),
        "confidence": {"granted": outcome == "clean", "limitations": limitations},
        "createdAt": time.time(),
    }
    _atomic_json(receipt_path, receipt)
    return receipt, False


def _remote_summary(receipt: Mapping[str, Any]) -> dict[str, Any]:
    target = receipt["target"]
    plan = receipt["plan"]
    attempts = receipt["attempts"]
    findings = receipt["findings"]
    return {
        "schemaVersion": 1,
        "repository": target["repository"],
        "base": target["base"],
        "head": target["head"],
        "contentDigest": target["contentDigest"],
        "receiptId": receipt["receiptId"],
        "outcome": receipt["outcome"],
        "providers": [
            {
                "id": row["provider"]["id"],
                "costTier": row["provider"]["costTier"],
                "qualityTier": row["provider"]["qualityTier"],
                "status": row["status"],
                "durationMs": row["durationMs"],
            }
            for row in attempts
        ],
        "findingCounts": {
            "total": len(findings),
            "outstanding": receipt["disposition"]["outstanding"],
        },
        "policyId": plan["policyId"],
        "familyGate": plan["familyGate"],
        "providerCostTiers": sorted(
            {row["provider"]["costTier"] for row in attempts}
        ),
        "remoteGate": receipt["remoteGate"],
        "confidence": receipt["confidence"],
    }


def _report(receipt: Mapping[str, Any], *, reused: bool) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "command": "sd-review-local-stage",
        "status": receipt["outcome"],
        "run": "reused" if reused else "executed",
        "receipt": receipt,
        "remoteSummary": _remote_summary(receipt),
    }


def _invalid_report(message: str) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "command": "sd-review-local-stage",
        "status": "invalid",
        "diagnostic": _bounded(message),
    }


def _cancelled_report() -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "command": "sd-review-local-stage",
        "status": "cancelled",
        "diagnostic": "local review stage cancelled by signal",
    }


def _print_human(report: Mapping[str, Any]) -> None:
    print(f"Local review stage: {report['status']}")
    if report.get("diagnostic"):
        print(f"Diagnostic: {report['diagnostic']}")
        return
    receipt = report["receipt"]
    plan = receipt["plan"]
    print(
        f"Plan: {plan['policyId']} ({', '.join(row['id'] for row in plan['providers']) or 'none'})"
    )
    print(f"Execution: {report['run']}")
    print(f"Exact head: {receipt['target']['head']}")
    print(f"Remote gate: {receipt['remoteGate']['state']}")


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=".")
    parser.add_argument("--scope", choices=sorted(SCOPES), default="branch")
    parser.add_argument("--base", default="origin/main")
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--local", default="auto")
    parser.add_argument(
        "--successor",
        choices=("first", "low-risk", "high-risk", "repeated-family", "bookkeeping"),
        default="first",
    )
    parser.add_argument("--finding-family", action="append", default=[])
    parser.add_argument("--family-evidence")
    parser.add_argument("--bookkeeping-evidence")
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--artifact-root")
    parser.add_argument(
        "--local-policy", choices=("optional", "required"), default="optional"
    )
    parser.add_argument("--fix", choices=("auto", "ask", "none"), default="auto")
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument("--no-reuse", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    report: dict[str, Any]
    try:
        if not ATTEMPT_RE.fullmatch(args.attempt_id):
            raise ReviewInputError("attempt id must be a bounded identifier")
        repo = Path(args.repo).resolve(strict=True)
        if not (repo / ".git").exists():
            raise ReviewInputError(f"not a Git repository: {repo}")
        config, providers, policy = load_config(repo)
        target = resolve_target(repo, args.scope, args.base, args.head)
        evidence = (
            Path(args.bookkeeping_evidence).resolve(strict=True)
            if args.bookkeeping_evidence
            else None
        )
        family_evidence = (
            Path(args.family_evidence).absolute()
            if args.family_evidence
            else None
        )
        family_gate = _family_gate(family_evidence, target)
        family_values = sorted(
            set(args.finding_family) | set(family_gate["repeatedFamilies"])
        )
        successor = (
            "repeated-family"
            if family_gate["state"] == "sibling-audit-required"
            else args.successor
        )
        plan = build_plan(
            providers=providers,
            policy=policy,
            target=target,
            local=args.local,
            local_policy=args.local_policy,
            fix_policy=args.fix,
            successor=successor,
            finding_families=family_values,
            family_gate=family_gate,
            bookkeeping_evidence=evidence,
            configuration_digest=_digest(config),
        )
        if CANCELLATION_EVENT.is_set():
            report = _cancelled_report()
            code = 3
        elif family_gate["state"] == "round-extension-required":
            report = {
                "schemaVersion": 1,
                "command": "sd-review-local-stage",
                "status": "blocked",
                "diagnostic": (
                    "a repeated post-audit finding family requires an approved "
                    "review.round-extension decision before another provider request"
                ),
                "familyGate": family_gate,
            }
            code = 1
        elif args.plan_only:
            report = {
                "schemaVersion": 1,
                "command": "sd-review-local-stage",
                "status": "planned",
                "target": target,
                "plan": plan,
            }
            code = 0
        else:
            root = _artifact_root(repo, args.artifact_root)
            receipt, reused = execute(
                repo=repo,
                artifact_root=root,
                attempt_id=args.attempt_id,
                target=target,
                plan=plan,
                providers=providers,
                local_policy=args.local_policy,
                fix_policy=args.fix,
                allow_reuse=not args.no_reuse,
            )
            report = _report(receipt, reused=reused)
            code = (
                0
                if receipt["outcome"] in {"clean", "skipped"}
                else 1
                if receipt["outcome"] == "findings"
                else 3
            )
    except (OSError, ReviewInputError) as error:
        report = _invalid_report(str(error))
        code = 2
    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2))
    else:
        _print_human(report)
    return code


if __name__ == "__main__":
    signal.signal(signal.SIGINT, _handle_termination)
    signal.signal(signal.SIGTERM, _handle_termination)
    raise SystemExit(main())
