#!/usr/bin/env python3
"""Run deterministic sd-check verification without repository mutation."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import time
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping, Sequence, cast

from sd_ai_command_pack_lib import (
    CacheSetupError,
    CommandError,
    build_tool_environment,
    command_detail,
    git_stdout,
    run_command,
)

SCHEMA_VERSION = 1
CONFIG_PATH = Path(".sd-ai-command-pack/check.json")
MAX_CONFIG_BYTES = 256 * 1024
MAX_CONFIG_ENTRIES = 64
MAX_ID_LENGTH = 64
MAX_ARG_COUNT = 64
MAX_ARG_LENGTH = 4096
MAX_ARGV_BYTES = 32 * 1024
MAX_CWD_LENGTH = 240
MAX_TIMEOUT_SECONDS = 3600
MAX_DIAGNOSTIC_LENGTH = 1200
MAX_INVENTORY_PATHS = 100_000
STATUS_VALUES = (
    "passed",
    "failed",
    "skipped",
    "unavailable",
    "invalid",
    "indeterminate",
)
AGGREGATE_PRECEDENCE = (
    "invalid",
    "failed",
    "indeterminate",
    "unavailable",
    "passed",
)
EXIT_BY_STATUS = {
    "passed": 0,
    "failed": 1,
    "skipped": 0,
    "unavailable": 3,
    "invalid": 2,
    "indeterminate": 3,
}
ID_PATTERN = re.compile(r"[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*\Z")
WINDOWS_DRIVE_PATTERN = re.compile(r"[A-Za-z]:")
CONFIG_KEYS = frozenset({"schemaVersion", "prerequisites", "checks"})
ENTRY_KEYS = frozenset({"id", "argv", "cwd", "timeoutSeconds"})
FORBIDDEN_EXECUTABLES = frozenset(
    {
        "cp",
        "gh",
        "gito",
        "install",
        "mkdir",
        "mv",
        "prism",
        "rm",
        "rmdir",
        "touch",
    }
)
SHELL_EXECUTABLES = frozenset({"bash", "dash", "fish", "ksh", "sh", "zsh"})
CODE_STRING_EXECUTABLES = frozenset(
    {"node", "nodejs", "perl", "python", "python3", "ruby"}
)
READ_ONLY_GIT_SUBCOMMANDS = frozenset(
    {
        "branch",
        "check-ignore",
        "diff",
        "for-each-ref",
        "grep",
        "log",
        "ls-files",
        "merge-base",
        "name-rev",
        "rev-list",
        "rev-parse",
        "show",
        "status",
        "symbolic-ref",
    }
)
GUARDED_PATHS = (
    Path(".obsidian-kb"),
    Path(".sd-ai-command-pack"),
    Path(".build"),
    Path(".pytest_cache"),
    Path(".ruff_cache"),
    Path(".coverage"),
    Path("unittest-output.log"),
    Path("docs/repomix-map.md"),
)


class CheckInputError(ValueError):
    """A controlled invalid repository or configuration condition."""


def _has_control(value: str) -> bool:
    return any(ord(character) < 32 or ord(character) == 127 for character in value)


def _bounded_text(value: str, *, fallback: str) -> str:
    normalized = " ".join(value.replace("\x00", " ").split())
    if not normalized:
        normalized = fallback
    if len(normalized) > MAX_DIAGNOSTIC_LENGTH:
        normalized = normalized[: MAX_DIAGNOSTIC_LENGTH - 3] + "..."
    return normalized


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _hash_regular_file(path: Path, digest: "hashlib._Hash") -> None:
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)


class _WorktreeHashCache:
    """Per-run memo of regular-file content digests, keyed by a cheap signature.

    One check run snapshots the tree many times: once before the checks, once
    after every executed row, and once at the end. Re-reading and re-hashing
    every file's content on each of those snapshots is the payload-linear cost
    this removes. A file whose ``(st_mode, st_size, st_mtime_ns)`` signature is
    unchanged since it was last hashed *this run* reuses its content digest
    instead of being read again; a file whose signature moved is re-hashed for
    real. So instead of ``2 + rows`` full content passes the run performs the
    cold pass that fills the cache plus the deliberately cache-free final pass —
    exactly two, independent of the row count.

    Only regular-file content is cached, and the cache is per-run and never
    persisted. Symlinks are always read fresh (``readlink`` is cheap, and this
    keeps every retarget caught at *every* snapshot). The cheap signature
    deliberately cannot see a same-size, mtime-preserving rewrite that happens
    mid-run, so a per-row snapshot can miss it and lose per-row attribution; the
    run's final snapshot runs against a fresh cache and re-hashes from scratch,
    so it remains the authority that still fails the run. This is the run-level
    granularity trade recorded in the task design.
    """

    def __init__(self) -> None:
        self._digests: dict[str, tuple[tuple[int, int, int], bytes]] = {}

    def regular_file_digest(self, path: Path, metadata: os.stat_result) -> bytes:
        signature = (metadata.st_mode, metadata.st_size, metadata.st_mtime_ns)
        key = os.fspath(path)
        cached = self._digests.get(key)
        if cached is not None and cached[0] == signature:
            return cached[1]
        digest = hashlib.sha256()
        _hash_regular_file(path, digest)
        raw = digest.digest()
        self._digests[key] = (signature, raw)
        return raw


def _hash_path(path: Path, cache: "_WorktreeHashCache | None" = None) -> str:
    if cache is None:
        cache = _WorktreeHashCache()
    digest = hashlib.sha256()
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        digest.update(b"missing\0")
        return digest.hexdigest()
    except OSError as error:
        raise CheckInputError(f"cannot inspect guarded path {path}: {error}") from error

    if stat.S_ISLNK(metadata.st_mode):
        digest.update(b"symlink\0")
        try:
            digest.update(os.fsencode(os.readlink(path)))
        except OSError as error:
            raise CheckInputError(f"cannot read guarded symlink {path}: {error}") from error
        return digest.hexdigest()
    if stat.S_ISREG(metadata.st_mode):
        digest.update(b"file\0")
        try:
            digest.update(cache.regular_file_digest(path, metadata))
        except OSError as error:
            raise CheckInputError(f"cannot read guarded file {path}: {error}") from error
        return digest.hexdigest()
    if not stat.S_ISDIR(metadata.st_mode):
        digest.update(f"node:{stat.S_IFMT(metadata.st_mode)}".encode("ascii"))
        return digest.hexdigest()

    digest.update(b"directory\0")
    try:
        descendants = sorted(
            path.rglob("*"),
            key=lambda candidate: os.fsencode(str(candidate.relative_to(path))),
        )
    except OSError as error:
        raise CheckInputError(f"cannot enumerate guarded path {path}: {error}") from error
    if len(descendants) > MAX_INVENTORY_PATHS:
        raise CheckInputError(
            f"guarded path inventory exceeds {MAX_INVENTORY_PATHS} entries: {path}"
        )
    for descendant in descendants:
        relative = descendant.relative_to(path)
        digest.update(os.fsencode(str(relative)))
        digest.update(b"\0")
        try:
            child_metadata = descendant.lstat()
            if stat.S_ISLNK(child_metadata.st_mode):
                digest.update(b"symlink\0")
                digest.update(os.fsencode(os.readlink(descendant)))
            elif stat.S_ISREG(child_metadata.st_mode):
                digest.update(b"file\0")
                digest.update(cache.regular_file_digest(descendant, child_metadata))
            elif stat.S_ISDIR(child_metadata.st_mode):
                digest.update(b"directory\0")
            else:
                digest.update(f"node:{stat.S_IFMT(child_metadata.st_mode)}".encode("ascii"))
        except OSError as error:
            raise CheckInputError(
                f"cannot inspect guarded path {descendant}: {error}"
            ) from error
    return digest.hexdigest()


def _git_bytes(repo: Path, args: list[str], *, context: str) -> bytes:
    try:
        result = run_command(
            ["git", *args],
            cwd=repo,
            timeout=60,
            capture_output=True,
            text=False,
            context=context,
        )
    except CacheSetupError:
        raise
    except CommandError as error:
        raise CheckInputError(str(error)) from error
    if result.returncode != 0:
        stderr = result.stderr if isinstance(result.stderr, bytes) else b""
        stdout = result.stdout if isinstance(result.stdout, bytes) else b""
        detail = (stderr or stdout).decode("utf-8", "replace")
        raise CheckInputError(
            _bounded_text(detail, fallback=f"git exited with status {result.returncode}")
        )
    return result.stdout if isinstance(result.stdout, bytes) else b""


def _git_optional_bytes(repo: Path, args: list[str], *, context: str) -> bytes:
    try:
        result = run_command(
            ["git", *args],
            cwd=repo,
            timeout=60,
            capture_output=True,
            text=False,
            context=context,
        )
    except CacheSetupError:
        raise
    except CommandError as error:
        raise CheckInputError(str(error)) from error
    if result.returncode not in {0, 1}:
        stderr = result.stderr if isinstance(result.stderr, bytes) else b""
        stdout = result.stdout if isinstance(result.stdout, bytes) else b""
        detail = (stderr or stdout).decode("utf-8", "replace")
        raise CheckInputError(
            _bounded_text(detail, fallback=f"git exited with status {result.returncode}")
        )
    return result.stdout if isinstance(result.stdout, bytes) else b""


def _tracked_worktree_digest(
    repo: Path, cache: "_WorktreeHashCache | None" = None
) -> str:
    if cache is None:
        cache = _WorktreeHashCache()
    raw_paths = _git_bytes(
        repo,
        ["ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        context="collect tracked and untracked check inventory",
    )
    encoded_paths = [value for value in raw_paths.split(b"\0") if value]
    if len(encoded_paths) > MAX_INVENTORY_PATHS:
        raise CheckInputError(
            f"repository inventory exceeds {MAX_INVENTORY_PATHS} paths"
        )
    digest = hashlib.sha256()
    for encoded in sorted(encoded_paths):
        relative_text = os.fsdecode(encoded)
        relative = PurePosixPath(relative_text)
        if relative.is_absolute() or ".." in relative.parts:
            raise CheckInputError("git returned an unsafe repository path")
        path = repo.joinpath(*relative.parts)
        digest.update(encoded)
        digest.update(b"\0")
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            digest.update(b"missing\0")
            continue
        except OSError as error:
            raise CheckInputError(f"cannot inspect repository path: {error}") from error
        if stat.S_ISLNK(metadata.st_mode):
            digest.update(b"symlink\0")
            try:
                digest.update(os.fsencode(os.readlink(path)))
            except OSError as error:
                raise CheckInputError(f"cannot read repository symlink: {error}") from error
        elif stat.S_ISREG(metadata.st_mode):
            digest.update(b"file\0")
            try:
                digest.update(cache.regular_file_digest(path, metadata))
            except OSError as error:
                raise CheckInputError(f"cannot read repository file: {error}") from error
        else:
            digest.update(f"node:{stat.S_IFMT(metadata.st_mode)}".encode("ascii"))
    return digest.hexdigest()


def _index_digest(repo: Path, cache: "_WorktreeHashCache | None" = None) -> str:
    raw_path = _git_bytes(
        repo,
        ["rev-parse", "--git-path", "index"],
        context="resolve git index",
    ).strip()
    if not raw_path:
        return _sha256_bytes(b"missing")
    index_path = Path(os.fsdecode(raw_path))
    if not index_path.is_absolute():
        index_path = repo / index_path
    # Share the run cache so ``.git/index`` — a single but potentially large file
    # — is not re-read on every per-row snapshot. Its cheap signature moves the
    # moment git rewrites the index, and the cache-free final snapshot re-hashes
    # it regardless, so this keeps the snapshot cost flat in the row count.
    return _hash_path(index_path, cache)


def state_snapshot(
    repo: Path, cache: "_WorktreeHashCache | None" = None
) -> dict[str, str]:
    # A per-run cache is shared across the many pre-check and per-row snapshots so
    # unchanged files are hashed once, not once per row. The final authoritative
    # snapshot deliberately passes no cache (a fresh, empty one) so it re-hashes
    # from scratch — see _WorktreeHashCache and build_report's `final`.
    if cache is None:
        cache = _WorktreeHashCache()
    guarded = hashlib.sha256()
    for relative in GUARDED_PATHS:
        guarded.update(relative.as_posix().encode("utf-8"))
        guarded.update(b"\0")
        guarded.update(_hash_path(repo / relative, cache).encode("ascii"))
        guarded.update(b"\0")
    return {
        "head": _sha256_bytes(
            _git_bytes(repo, ["rev-parse", "--verify", "HEAD"], context="read HEAD")
        ),
        "headReference": _sha256_bytes(
            _git_optional_bytes(
                repo,
                ["symbolic-ref", "-q", "HEAD"],
                context="read symbolic HEAD",
            )
        ),
        "refs": _sha256_bytes(
            _git_bytes(
                repo,
                ["for-each-ref", "--format=%(refname)%00%(objectname)"],
                context="read git refs",
            )
        ),
        "index": _index_digest(repo, cache),
        "worktree": _tracked_worktree_digest(repo, cache),
        "guarded": guarded.hexdigest(),
    }


def _state_changes(before: Mapping[str, str], after: Mapping[str, str]) -> list[str]:
    return sorted(key for key in before if before.get(key) != after.get(key))


def _resolve_repo(value: str | None) -> Path:
    candidate = Path.cwd() if value is None else Path(value).expanduser()
    try:
        candidate = candidate.resolve(strict=True)
    except OSError as error:
        raise CheckInputError(f"cannot resolve repository: {error}") from error
    if not candidate.is_dir():
        raise CheckInputError(f"repository is not a directory: {candidate}")
    try:
        root = git_stdout(
            ["rev-parse", "--show-toplevel"],
            cwd=candidate,
            context="resolve repository root",
            required=True,
        )
    except CacheSetupError:
        raise
    except CommandError as error:
        raise CheckInputError(str(error)) from error
    if root is None:
        raise CheckInputError("cannot resolve repository root")
    try:
        resolved = Path(root).resolve(strict=True)
    except OSError as error:
        raise CheckInputError(f"cannot resolve repository root: {error}") from error
    if not resolved.is_dir():
        raise CheckInputError(f"repository root is not a directory: {resolved}")
    return resolved


def _resolve_config_path(repo: Path, value: str | None) -> Path:
    expected = repo / CONFIG_PATH
    if value is None:
        return expected
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = repo / candidate
    try:
        candidate = candidate.resolve(strict=False)
        expected_resolved = expected.resolve(strict=False)
    except OSError as error:
        raise CheckInputError(f"cannot resolve check configuration: {error}") from error
    if candidate != expected_resolved:
        raise CheckInputError(
            f"check configuration must be the repository-owned {CONFIG_PATH.as_posix()}"
        )
    return expected


def _read_config(path: Path) -> dict[str, Any] | None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return None
    except OSError as error:
        raise CheckInputError(f"cannot inspect {CONFIG_PATH.as_posix()}: {error}") from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise CheckInputError(f"{CONFIG_PATH.as_posix()} must be a regular file")
    if metadata.st_size > MAX_CONFIG_BYTES:
        raise CheckInputError(
            f"{CONFIG_PATH.as_posix()} exceeds {MAX_CONFIG_BYTES} bytes"
        )
    try:
        text = path.read_bytes().decode("utf-8")
    except UnicodeDecodeError as error:
        raise CheckInputError(f"{CONFIG_PATH.as_posix()} must be valid UTF-8") from error
    except OSError as error:
        raise CheckInputError(f"cannot read {CONFIG_PATH.as_posix()}: {error}") from error
    try:
        value = json.loads(text)
    except json.JSONDecodeError as error:
        raise CheckInputError(
            f"{CONFIG_PATH.as_posix()} is not valid JSON: line {error.lineno} column {error.colno}"
        ) from error
    if not isinstance(value, dict):
        raise CheckInputError(f"{CONFIG_PATH.as_posix()} must contain a JSON object")
    return value


def _validate_cwd(repo: Path, value: object, *, field: str) -> tuple[str, Path]:
    if not isinstance(value, str) or not value or len(value) > MAX_CWD_LENGTH:
        raise CheckInputError(f"{field} must be a bounded non-empty relative path")
    if _has_control(value) or "\\" in value or WINDOWS_DRIVE_PATTERN.match(value):
        raise CheckInputError(f"{field} contains an unsafe path")
    relative = PurePosixPath(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise CheckInputError(f"{field} must stay inside the repository")
    path = repo.joinpath(*relative.parts)
    current = repo
    try:
        for part in relative.parts:
            current = current / part
            metadata = current.lstat()
            if stat.S_ISLNK(metadata.st_mode):
                raise CheckInputError(f"{field} must not traverse a symlink")
        resolved = path.resolve(strict=True)
    except CheckInputError:
        raise
    except OSError as error:
        raise CheckInputError(f"{field} is not an accessible directory: {value}") from error
    try:
        resolved.relative_to(repo)
    except ValueError as error:
        raise CheckInputError(f"{field} escapes the repository") from error
    if not resolved.is_dir():
        raise CheckInputError(f"{field} is not a directory: {value}")
    return relative.as_posix(), resolved


def _executable_name(value: str) -> str:
    return PurePosixPath(value.replace("\\", "/")).name.casefold()


def _leading_options(argv: tuple[str, ...]) -> tuple[str, ...]:
    options: list[str] = []
    for argument in argv[1:]:
        if argument == "--" or not argument.startswith("-") or argument == "-":
            break
        options.append(argument)
    return tuple(options)


def _uses_long_option(options: tuple[str, ...], names: frozenset[str]) -> bool:
    return any(
        argument in names
        or any(argument.startswith(f"{name}=") for name in names)
        for argument in options
    )


def _validate_command_policy(argv: tuple[str, ...], *, field: str) -> None:
    executable = _executable_name(argv[0])
    options = _leading_options(argv)
    if executable in FORBIDDEN_EXECUTABLES:
        raise CheckInputError(f"{field} uses forbidden executable {executable}")
    if executable in SHELL_EXECUTABLES and (
        any(
            not argument.startswith("--") and "c" in argument[1:]
            for argument in options
        )
        or _uses_long_option(options, frozenset({"--command"}))
    ):
        raise CheckInputError(f"{field} must not execute a shell command string")
    is_code_string_executable = executable in CODE_STRING_EXECUTABLES or bool(
        re.fullmatch(r"python(?:3(?:\.\d+)*)?", executable)
    )
    if is_code_string_executable:
        short_prefixes: tuple[str, ...]
        long_options: frozenset[str]
        if re.fullmatch(r"python(?:3(?:\.\d+)*)?", executable):
            short_prefixes = ("-c",)
            long_options = frozenset()
        elif executable in {"node", "nodejs"}:
            short_prefixes = ("-e", "-p")
            long_options = frozenset({"--eval", "--print"})
        elif executable == "perl":
            short_prefixes = ("-e", "-E")
            long_options = frozenset()
        else:
            short_prefixes = ("-e",)
            long_options = frozenset()
        if any(
            argument.startswith(short_prefixes)
            for argument in options
            if not argument.startswith("--")
        ) or _uses_long_option(options, long_options):
            raise CheckInputError(f"{field} must not execute an inline code string")
    if executable == "git":
        subcommand = next(
            (argument for argument in argv[1:] if not argument.startswith("-")),
            "",
        )
        if subcommand not in READ_ONLY_GIT_SUBCOMMANDS:
            raise CheckInputError(f"{field} uses non-read-only git operation")


def _validate_entry(
    repo: Path,
    value: object,
    *,
    field: str,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CheckInputError(f"{field} must be an object")
    unknown = sorted(set(value) - ENTRY_KEYS)
    if unknown:
        raise CheckInputError(f"{field} has unknown field(s): {', '.join(unknown)}")
    missing = sorted(ENTRY_KEYS - set(value))
    if missing:
        raise CheckInputError(f"{field} is missing field(s): {', '.join(missing)}")
    identifier = value["id"]
    if (
        not isinstance(identifier, str)
        or len(identifier) > MAX_ID_LENGTH
        or not ID_PATTERN.fullmatch(identifier)
    ):
        raise CheckInputError(f"{field}.id must be a safe identifier")
    raw_argv = value["argv"]
    if not isinstance(raw_argv, list) or not raw_argv or len(raw_argv) > MAX_ARG_COUNT:
        raise CheckInputError(f"{field}.argv must be a non-empty bounded array")
    argv: list[str] = []
    total = 0
    for index, argument in enumerate(raw_argv):
        if not isinstance(argument, str):
            raise CheckInputError(f"{field}.argv[{index}] must be a string")
        if len(argument) > MAX_ARG_LENGTH or _has_control(argument):
            raise CheckInputError(f"{field}.argv[{index}] is unsafe or oversized")
        if index == 0 and not argument:
            raise CheckInputError(f"{field}.argv[0] must name an executable")
        total += len(argument.encode("utf-8"))
        argv.append(argument)
    if total > MAX_ARGV_BYTES:
        raise CheckInputError(f"{field}.argv exceeds {MAX_ARGV_BYTES} bytes")
    normalized_argv = tuple(argv)
    _validate_command_policy(normalized_argv, field=f"{field}.argv")
    cwd_text, cwd = _validate_cwd(repo, value["cwd"], field=f"{field}.cwd")
    timeout = value["timeoutSeconds"]
    if (
        not isinstance(timeout, int)
        or isinstance(timeout, bool)
        or not 1 <= timeout <= MAX_TIMEOUT_SECONDS
    ):
        raise CheckInputError(
            f"{field}.timeoutSeconds must be between 1 and {MAX_TIMEOUT_SECONDS}"
        )
    return {
        "id": identifier,
        "argv": normalized_argv,
        "cwd": cwd,
        "cwdText": cwd_text,
        "timeoutSeconds": timeout,
    }


def load_configuration(repo: Path, path: Path) -> dict[str, list[dict[str, Any]]]:
    raw = _read_config(path)
    if raw is None:
        return {"prerequisites": [], "checks": []}
    unknown = sorted(set(raw) - CONFIG_KEYS)
    if unknown:
        raise CheckInputError(
            f"{CONFIG_PATH.as_posix()} has unknown field(s): {', '.join(unknown)}"
        )
    missing = sorted(CONFIG_KEYS - set(raw))
    if missing:
        raise CheckInputError(
            f"{CONFIG_PATH.as_posix()} is missing field(s): {', '.join(missing)}"
        )
    version = raw["schemaVersion"]
    if not isinstance(version, int) or isinstance(version, bool) or version != 1:
        raise CheckInputError(f"unsupported check configuration schema: {version!r}")
    result: dict[str, list[dict[str, Any]]] = {}
    identifiers: set[str] = set()
    for group in ("prerequisites", "checks"):
        values = raw[group]
        if not isinstance(values, list) or len(values) > MAX_CONFIG_ENTRIES:
            raise CheckInputError(
                f"{CONFIG_PATH.as_posix()}.{group} must be a bounded array"
            )
        entries: list[dict[str, Any]] = []
        for index, value in enumerate(values):
            entry = _validate_entry(
                repo,
                value,
                field=f"{CONFIG_PATH.as_posix()}.{group}[{index}]",
            )
            identifier = str(entry["id"])
            if identifier in identifiers:
                raise CheckInputError(f"duplicate check id: {identifier}")
            identifiers.add(identifier)
            entries.append(entry)
        result[group] = entries
    return result


def _command_identity(argv: Sequence[str], cwd_text: str) -> dict[str, object]:
    return {
        "executable": argv[0],
        "argumentCount": max(0, len(argv) - 1),
        "cwd": cwd_text,
    }


def _result_row(
    identifier: str,
    kind: str,
    status_value: str,
    *,
    diagnostic: str,
    remediation: str | None = None,
    exit_code: int | None = None,
    command: dict[str, object] | None = None,
    duration_ms: int = 0,
) -> dict[str, object]:
    if status_value not in STATUS_VALUES:
        raise AssertionError(f"unsupported check status: {status_value}")
    return {
        "id": identifier,
        "kind": kind,
        "status": status_value,
        "exitCode": exit_code,
        "durationMs": max(0, duration_ms),
        "command": command,
        "diagnostic": _bounded_text(diagnostic, fallback=status_value),
        "remediation": (
            _bounded_text(remediation, fallback="none")
            if remediation is not None
            else None
        ),
    }


def _is_external_symlink(kb_root: Path, repo: Path) -> bool:
    """True iff ``kb_root`` is a symlink whose resolved target is outside ``repo``.

    An external-symlinked ``.obsidian-kb`` points at a live, gitignored,
    never-shipped vault whose freshness mutates independently of HEAD, so its
    ``--check`` result is non-deterministic and must be advisory rather than a
    blocking gate. An in-repo symlink (target resolves under the repo) or a real
    tracked directory stays deterministic against HEAD and keeps blocking. A
    broken link resolves (``strict=False``) to its declared target path, so an
    external broken link is treated as external and an in-repo broken link keeps
    blocking so the breakage surfaces.
    """
    if not kb_root.is_symlink():
        return False
    target = kb_root.resolve(strict=False)
    repo_root = repo.resolve()
    return repo_root != target and repo_root not in target.parents


def _executable_available(command: str, cwd: Path, environment: Mapping[str, str]) -> bool:
    if "/" in command or "\\" in command:
        candidate = Path(command)
        if not candidate.is_absolute():
            candidate = cwd / candidate
        return candidate.is_file() and os.access(candidate, os.X_OK)
    return shutil.which(command, path=environment.get("PATH")) is not None


def execute_check(
    *,
    identifier: str,
    kind: str,
    argv: Sequence[str],
    cwd: Path,
    cwd_text: str,
    timeout_seconds: int,
    environment: Mapping[str, str],
    remediation: str | None = None,
) -> dict[str, object]:
    command = _command_identity(argv, cwd_text)
    if not _executable_available(argv[0], cwd, environment):
        return _result_row(
            identifier,
            kind,
            "unavailable",
            diagnostic=f"{argv[0]} is not available",
            remediation=remediation,
            command=command,
        )
    started = time.monotonic()
    try:
        result = run_command(
            list(argv),
            cwd=cwd,
            timeout=timeout_seconds,
            check=False,
            capture_output=True,
            context=f"run sd-check row {identifier}",
            env=environment,
        )
    except CacheSetupError as error:
        status_value = "unavailable"
        detail = str(error)
        exit_code = None
    except CommandError as error:
        detail = str(error)
        status_value = "indeterminate" if "timed out after" in detail else "unavailable"
        exit_code = None
    except OSError as error:
        status_value = "unavailable"
        detail = f"cannot launch {argv[0]}: {error}"
        exit_code = None
    else:
        exit_code = result.returncode
        status_value = "passed" if result.returncode == 0 else "failed"
        detail = command_detail(
            result,
            fallback=(
                "check passed"
                if result.returncode == 0
                else f"{argv[0]} exited with status {result.returncode}"
            ),
        )
    duration_ms = round((time.monotonic() - started) * 1000)
    return _result_row(
        identifier,
        kind,
        status_value,
        diagnostic=detail,
        remediation=remediation,
        exit_code=exit_code,
        command=command,
        duration_ms=duration_ms,
    )


def _aggregate(rows: Sequence[Mapping[str, object]]) -> tuple[str, dict[str, int]]:
    counts = Counter(str(row["status"]) for row in rows)
    normalized = {status_value: counts.get(status_value, 0) for status_value in STATUS_VALUES}
    for status_value in AGGREGATE_PRECEDENCE:
        if status_value == "passed" or normalized[status_value]:
            return status_value, normalized
    return "passed", normalized


def _base_report(repo: Path, *, config_present: bool) -> dict[str, object]:
    head = None
    try:
        head = git_stdout(
            ["rev-parse", "--verify", "HEAD"],
            cwd=repo,
            context="read check HEAD",
            required=False,
        )
    except (CacheSetupError, CommandError):
        pass
    return {
        "schemaVersion": SCHEMA_VERSION,
        "command": "sd-check",
        "repository": {
            "root": str(repo),
            "headOid": head,
            "config": CONFIG_PATH.as_posix(),
            "configPresent": config_present,
        },
        "status": "invalid",
        "exitCode": 2,
        "counts": {status_value: 0 for status_value in STATUS_VALUES},
        "checks": [],
        "stateGuard": {
            "status": "not-run",
            "changed": [],
            "beforeDigest": None,
            "afterDigest": None,
        },
    }


def _snapshot_digest(snapshot: Mapping[str, str]) -> str:
    encoded = json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return _sha256_bytes(encoded)


def _append_guard_failure(
    rows: list[dict[str, object]],
    *,
    changes: Sequence[str],
) -> None:
    rows.append(
        _result_row(
            "state-guard",
            "guard",
            "failed",
            diagnostic="repository or Git state changed during sd-check: " + ", ".join(changes),
            remediation="inspect the named state and make the configured check read-only",
        )
    )


def build_report(repo: Path, config_path: Path) -> dict[str, object]:
    config_present = config_path.exists() or config_path.is_symlink()
    report = _base_report(repo, config_present=config_present)
    rows: list[dict[str, object]] = []
    try:
        configuration = load_configuration(repo, config_path)
        environment, _, _ = build_tool_environment(repo=repo)
        # One cache for the whole run: the cold `before` pass fills it and every
        # per-row snapshot reuses it, so unchanged files are hashed once rather
        # than once per row. The final snapshot below drops it deliberately.
        run_cache = _WorktreeHashCache()
        before = state_snapshot(repo, run_cache)
    except (CheckInputError, CacheSetupError) as error:
        rows.append(
            _result_row(
                "configuration",
                "configuration",
                "invalid" if isinstance(error, CheckInputError) else "unavailable",
                diagnostic=str(error),
                remediation=(
                    f"correct {CONFIG_PATH.as_posix()}"
                    if isinstance(error, CheckInputError)
                    else "configure a private external cache with SD_AI_COMMAND_PACK_CACHE_ROOT"
                ),
            )
        )
        status_value, counts = _aggregate(rows)
        report.update(
            {
                "status": status_value,
                "exitCode": EXIT_BY_STATUS[status_value],
                "counts": counts,
                "checks": rows,
            }
        )
        return report

    report["stateGuard"] = {
        "status": "running",
        "changed": [],
        "beforeDigest": _snapshot_digest(before),
        "afterDigest": None,
    }

    def run_and_guard(row: dict[str, object]) -> bool:
        rows.append(row)
        try:
            current = state_snapshot(repo, run_cache)
        except CheckInputError as error:
            rows.append(
                _result_row(
                    "state-guard",
                    "guard",
                    "indeterminate",
                    diagnostic=str(error),
                    remediation="restore readable repository and Git state, then rerun sd-check",
                )
            )
            report["stateGuard"] = {
                "status": "indeterminate",
                "changed": [],
                "beforeDigest": _snapshot_digest(before),
                "afterDigest": None,
            }
            return False
        changes = _state_changes(before, current)
        report["stateGuard"] = {
            "status": "failed" if changes else "passed",
            "changed": changes,
            "beforeDigest": _snapshot_digest(before),
            "afterDigest": _snapshot_digest(current),
        }
        if changes:
            _append_guard_failure(rows, changes=changes)
            return False
        return True

    def command_row(
        identifier: str,
        argv: tuple[str, ...],
        *,
        remediation: str,
        timeout_seconds: int = 120,
    ) -> dict[str, object]:
        return execute_check(
            identifier=identifier,
            kind="builtin",
            argv=argv,
            cwd=repo,
            cwd_text=".",
            timeout_seconds=timeout_seconds,
            environment=environment,
            remediation=remediation,
        )

    def shipped_helper_row(
        identifier: str,
        helper: Path,
        argv: tuple[str, ...],
        *,
        missing_diagnostic: str,
        remediation: str,
    ) -> dict[str, object]:
        if helper.is_file() and not helper.is_symlink():
            return command_row(identifier, argv, remediation=remediation)
        return _result_row(
            identifier,
            "builtin",
            "unavailable",
            diagnostic=missing_diagnostic,
            remediation=remediation,
        )

    def review_preflight_row() -> dict[str, object]:
        helper = repo / "scripts/sd-ai-command-pack-review-preflight.mjs"
        return shipped_helper_row(
            "pack.review-preflight",
            helper,
            ("node", str(helper)),
            missing_diagnostic="deterministic review preflight helper is not present",
            remediation="install the command pack or register the repository preflight in check.json",
        )

    def install_audit_row() -> dict[str, object]:
        audit = repo / "scripts/sd-ai-command-pack-install-audit.py"
        return shipped_helper_row(
            "pack.install-audit",
            audit,
            (sys.executable, str(audit)),
            missing_diagnostic="installed payload audit helper is not present",
            remediation="install or refresh the command pack, then rerun sd-check",
        )

    def kb_freshness_row() -> dict[str, object]:
        kb_root = repo / ".obsidian-kb"
        if not (kb_root.exists() or kb_root.is_symlink()):
            return _result_row(
                "knowledge.obsidian-kb",
                "builtin",
                "skipped",
                diagnostic="no .obsidian-kb directory is present",
                remediation="run sd-update-spec to create it when repository knowledge export is desired",
            )
        helper = repo / "scripts/sd-ai-command-pack-update-spec-kb.py"
        if not helper.is_file() or helper.is_symlink():
            return _result_row(
                "knowledge.obsidian-kb",
                "builtin",
                "unavailable",
                diagnostic="Obsidian KB exists but its read-only freshness helper is missing",
                remediation="reinstall the command pack, then run sd-update-spec",
            )
        row = command_row(
            "knowledge.obsidian-kb",
            (sys.executable, str(helper), "--check"),
            remediation=(
                "run sd-update-spec or python3 scripts/sd-ai-command-pack-update-spec-kb.py"
            ),
        )
        # Advisory downgrade: an external-symlinked .obsidian-kb points at a live
        # external vault (gitignored, never shipped) whose freshness is
        # non-deterministic, so a transient failure must not gate a merge. An
        # in-repo symlink or a tracked directory keeps blocking (see
        # _is_external_symlink). "skipped" is absent from AGGREGATE_PRECEDENCE,
        # so the downgraded row never contributes to the blocking verdict.
        if _is_external_symlink(kb_root, repo) and row.get("status") == "failed":
            return _result_row(
                "knowledge.obsidian-kb",
                "builtin",
                "skipped",
                diagnostic=(
                    "advisory: external-symlinked .obsidian-kb drift is "
                    "non-deterministic and never shipped; "
                    + str(row.get("diagnostic", ""))
                ),
                remediation=cast(str | None, row.get("remediation")),
                exit_code=cast(int | None, row.get("exitCode")),
                command=cast(dict[str, object] | None, row.get("command")),
                duration_ms=cast(int, row.get("durationMs") or 0),
            )
        return row

    def review_scope_row() -> dict[str, object]:
        helper = repo / "scripts/sd-ai-command-pack-review-scope.sh"
        return shipped_helper_row(
            "pack.review-scope",
            helper,
            ("bash", str(helper)),
            missing_diagnostic="tooling and generated review-scope helper is not present",
            remediation="install the command pack or register an equivalent argv check",
        )

    def pr_body_scope_row() -> dict[str, object]:
        helper = repo / "scripts/sd-ai-command-pack-pr-body-scope.py"
        return shipped_helper_row(
            "pack.pr-body-scope",
            helper,
            (sys.executable, str(helper)),
            missing_diagnostic="PR-body scope helper is not present",
            remediation="install the command pack to enable PR-body scope validation",
        )

    builtin_factories: tuple[Callable[[], dict[str, object]], ...] = (
        lambda: command_row(
            "git.whitespace.unstaged",
            ("git", "diff", "--check"),
            remediation="fix the reported unstaged whitespace errors",
            timeout_seconds=60,
        ),
        lambda: command_row(
            "git.whitespace.staged",
            ("git", "diff", "--cached", "--check"),
            remediation="fix the reported staged whitespace errors",
            timeout_seconds=60,
        ),
        review_preflight_row,
        install_audit_row,
        kb_freshness_row,
        review_scope_row,
        pr_body_scope_row,
    )
    builtins_preserved_state = True
    for factory in builtin_factories:
        if not run_and_guard(factory()):
            builtins_preserved_state = False
            break

    if builtins_preserved_state:
        prerequisites_passed = True
        for entry in configuration["prerequisites"]:
            row = execute_check(
                identifier=str(entry["id"]),
                kind="prerequisite",
                argv=entry["argv"],
                cwd=entry["cwd"],
                cwd_text=str(entry["cwdText"]),
                timeout_seconds=int(entry["timeoutSeconds"]),
                environment=environment,
                remediation="restore the declared prerequisite, then rerun sd-check",
            )
            if not run_and_guard(row):
                prerequisites_passed = False
                break
            if row["status"] != "passed":
                prerequisites_passed = False
                break
        if prerequisites_passed:
            for entry in configuration["checks"]:
                row = execute_check(
                    identifier=str(entry["id"]),
                    kind="check",
                    argv=entry["argv"],
                    cwd=entry["cwd"],
                    cwd_text=str(entry["cwdText"]),
                    timeout_seconds=int(entry["timeoutSeconds"]),
                    environment=environment,
                    remediation="fix the declared repository check, then rerun sd-check",
                )
                if not run_and_guard(row):
                    break
        else:
            completed_ids = {str(row["id"]) for row in rows}
            for entry in configuration["checks"]:
                if str(entry["id"]) in completed_ids:
                    continue
                rows.append(
                    _result_row(
                        str(entry["id"]),
                        "check",
                        "skipped",
                        diagnostic="blocked by a prerequisite that did not pass",
                        remediation="fix the prerequisite, then rerun sd-check",
                        command=_command_identity(entry["argv"], str(entry["cwdText"])),
                    )
                )

    guard = report["stateGuard"]
    if isinstance(guard, dict) and guard.get("status") == "running":
        # Authoritative final snapshot: no shared cache, so every file is
        # re-hashed from scratch. This is what still catches a same-size,
        # mtime-preserving mid-run rewrite that the cached per-row snapshots
        # cannot see, at the cost of per-row attribution for that one case.
        final = state_snapshot(repo)
        changes = _state_changes(before, final)
        if changes:
            _append_guard_failure(rows, changes=changes)
        report["stateGuard"] = {
            "status": "failed" if changes else "passed",
            "changed": changes,
            "beforeDigest": _snapshot_digest(before),
            "afterDigest": _snapshot_digest(final),
        }

    status_value, counts = _aggregate(rows)
    report.update(
        {
            "status": status_value,
            "exitCode": EXIT_BY_STATUS[status_value],
            "counts": counts,
            "checks": rows,
        }
    )
    return report


def render_human(report: Mapping[str, object]) -> str:
    repository = report.get("repository")
    root = repository.get("root", "unknown") if isinstance(repository, dict) else "unknown"
    lines = [
        "SD check",
        f"Repository: {root}",
        f"Status: {report.get('status', 'invalid')}",
        "",
        "Checks",
    ]
    checks = report.get("checks")
    if isinstance(checks, list) and checks:
        for row in checks:
            if not isinstance(row, dict):
                continue
            lines.append(
                f"- {row.get('id', 'unknown')}: {row.get('status', 'invalid')}"
                f" - {row.get('diagnostic', 'no diagnostic')}"
            )
            if row.get("remediation"):
                lines.append(f"  remediation: {row['remediation']}")
    else:
        lines.append("none")
    counts = report.get("counts")
    if isinstance(counts, dict):
        lines.extend(
            [
                "",
                "Counts: "
                + ", ".join(
                    f"{status_value}={counts.get(status_value, 0)}"
                    for status_value in STATUS_VALUES
                ),
            ]
        )
    state_guard = report.get("stateGuard")
    if isinstance(state_guard, dict):
        lines.append(f"State guard: {state_guard.get('status', 'not-run')}")
    return "\n".join(lines) + "\n"


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run deterministic read-only Software Delivery checks."
    )
    parser.add_argument("--repo", help="repository to inspect (defaults to current worktree)")
    parser.add_argument("--config", help=argparse.SUPPRESS)
    parser.add_argument("--json", action="store_true", help="emit schema-versioned JSON")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        repo = _resolve_repo(args.repo)
        config_path = _resolve_config_path(repo, args.config)
        report = build_report(repo, config_path)
    except (CheckInputError, CacheSetupError) as error:
        fallback_repo = Path.cwd().resolve()
        report = _base_report(fallback_repo, config_present=False)
        invalid = isinstance(error, CheckInputError)
        status_value = "invalid" if invalid else "unavailable"
        row = _result_row(
            "configuration",
            "configuration",
            status_value,
            diagnostic=str(error),
            remediation=(
                f"correct {CONFIG_PATH.as_posix()} or repository selection"
                if invalid
                else "configure a private external cache with SD_AI_COMMAND_PACK_CACHE_ROOT"
            ),
        )
        report["checks"] = [row]
        report["counts"] = {
            candidate: int(candidate == status_value) for candidate in STATUS_VALUES
        }
        report["status"] = status_value
        report["exitCode"] = EXIT_BY_STATUS[status_value]
    if args.json:
        print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    else:
        print(render_human(report), end="")
    exit_code = report.get("exitCode")
    return exit_code if isinstance(exit_code, int) and not isinstance(exit_code, bool) else 2


if __name__ == "__main__":
    raise SystemExit(main())
