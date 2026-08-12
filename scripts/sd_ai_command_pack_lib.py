#!/usr/bin/env python3
"""Shared stdlib helpers for shipped sd-ai-command-pack Python scripts."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from os import PathLike
from pathlib import Path, PureWindowsPath
from typing import Any, Iterable, Literal, Mapping, Sequence, overload

DEFAULT_COMMAND_TIMEOUT = 60
DEFAULT_GIT_TIMEOUT = 60
DEFAULT_GH_TIMEOUT = 120
DEFAULT_TRELLIS_TIMEOUT = 120
REVIEW_FAMILY_TASK_METADATA = "task-metadata"
REVIEW_FAMILY_BOUNDARY_VALIDATION = "boundary-validation"
REVIEW_FAMILY_CONTRACT_DOCUMENTATION = "contract-documentation-drift"
REVIEW_FAMILY_GENERATED_SURFACES = "generated-surfaces"
REVIEW_FAMILY_REVIEWER_TEST_HARNESS = "reviewer-test-harness-quality"
REVIEW_FAMILY_OTHER = "other"
REVIEW_FINDING_FAMILY_IDS = (
    REVIEW_FAMILY_TASK_METADATA,
    REVIEW_FAMILY_BOUNDARY_VALIDATION,
    REVIEW_FAMILY_CONTRACT_DOCUMENTATION,
    REVIEW_FAMILY_GENERATED_SURFACES,
    REVIEW_FAMILY_REVIEWER_TEST_HARNESS,
    REVIEW_FAMILY_OTHER,
)

# ---------------------------------------------------------------------------
# Shared verdict vocabulary (A-077)
# ---------------------------------------------------------------------------
# One naming rule across emitted payload envelopes: the top-level ``outcome``
# key holds a verdict; the top-level ``status`` key is reserved for an embedded
# sd-status document. ``VERDICT_CORE`` is the set of verdict values that mean
# the same thing in every domain that emits them (``failed`` appears in more
# than two domains; ``clean``/``blocked``/``skipped`` each appear in two with a
# compatible meaning). Per-domain verdict sets are derived from the core through
# ``declare_verdict_domain``: every value a domain emits that is absent from the
# core must be listed in that call's explicit ``opt_out`` (``findings``,
# ``at-target`` and friends), so a shared value cannot silently diverge while a
# legitimate domain-specific value is still permitted. Declaring a non-core
# verdict without opting it out raises at import time, and
# ``tests/test_verdict_vocabulary.py`` re-asserts the guarantee.
VERDICT_CORE = frozenset({"clean", "blocked", "skipped", "failed"})

# Populated at import time by ``declare_verdict_domain`` calls in each producer.
VERDICT_DOMAINS: dict[str, frozenset[str]] = {}


class VerdictVocabularyError(ValueError):
    """Raised when a domain declares a verdict outside the shared core."""


def declare_verdict_domain(
    name: str, members: Iterable[str], *, opt_out: Iterable[str] = ()
) -> frozenset[str]:
    """Register a per-domain verdict set derived from ``VERDICT_CORE``.

    Every member absent from ``VERDICT_CORE`` must appear in ``opt_out``;
    otherwise the declaration raises ``VerdictVocabularyError`` so a drifted
    vocabulary fails loudly at the producer rather than silently diverging
    across payloads. ``opt_out`` may not name a core verdict (that would hide a
    core member behind a redundant opt-out). Returns the frozen member set and
    records it under ``name`` in ``VERDICT_DOMAINS``.
    """

    member_set = frozenset(members)
    opt_out_set = frozenset(opt_out)
    redundant = opt_out_set & VERDICT_CORE
    if redundant:
        raise VerdictVocabularyError(
            f"verdict domain {name!r} opts out core verdicts: "
            + ", ".join(sorted(redundant))
        )
    undeclared = member_set - VERDICT_CORE - opt_out_set
    if undeclared:
        raise VerdictVocabularyError(
            f"verdict domain {name!r} declares non-core verdicts without opt-out: "
            + ", ".join(sorted(undeclared))
        )
    VERDICT_DOMAINS[name] = member_set
    return member_set


# Payload envelope keys renamed under A-077 and kept alive additively for one
# dual-emit window (R5). Each entry names the producer, the deprecated key's
# path inside its document, the canonical replacement, and the release that may
# drop it. The AC1 shape walker excludes these deprecated paths when checking
# that no two ``status`` keys in one document carry different value types, and
# the compat fixtures assert every alias is still emitted for the whole window.
DEPRECATED_PAYLOAD_KEYS: tuple[dict[str, Any], ...] = (
    {
        "producer": "housekeeping-result",
        "path": ("outcome", "status"),
        "replacement": ("outcome", "verdict"),
        "removed_version": "0.66.0",
    },
    {
        "producer": "review-local-stage",
        "path": ("status",),
        "replacement": ("outcome",),
        "removed_version": "0.66.0",
    },
)

CACHE_ROOT_ENV = "SD_AI_COMMAND_PACK_CACHE_ROOT"
STATE_HOME_ENV = "SD_AI_COMMAND_PACK_STATE_HOME"
CACHE_ENV_KEYS = (
    "XDG_CACHE_HOME",
    "PYTHONPYCACHEPREFIX",
    "UV_CACHE_DIR",
    "UV_TOOL_DIR",
    "PIP_CACHE_DIR",
    "RUFF_CACHE_DIR",
    "NPM_CONFIG_CACHE",
)
CACHE_DIRECTORY_NAMES = {
    "XDG_CACHE_HOME": "xdg",
    "PYTHONPYCACHEPREFIX": "python",
    "UV_CACHE_DIR": "uv",
    "UV_TOOL_DIR": "uv-tools",
    "PIP_CACHE_DIR": "pip",
    "RUFF_CACHE_DIR": "ruff",
    "NPM_CONFIG_CACHE": "npm",
}


class CommandError(RuntimeError):
    """Raised when a required external command cannot complete cleanly."""


class CacheSetupError(CommandError):
    """Raised when a private external tool cache cannot be prepared safely."""


@dataclass(frozen=True)
class ToolExecutionPlan:
    """An argv-safe command and the validated environment used to run it."""

    command: tuple[str, ...]
    environment: dict[str, str]
    cache_paths: dict[str, Path]
    cache_namespace: Path


def command_display(command: Iterable[str]) -> str:
    parts = list(command)
    return parts[0] if parts else "command"


def command_detail(
    result: subprocess.CompletedProcess[str],
    *,
    fallback: str,
) -> str:
    detail = ""
    stdout = result.stdout if isinstance(result.stdout, str) else ""
    stderr = result.stderr if isinstance(result.stderr, str) else ""
    for stream in (stderr, stdout):
        if stream.strip():
            detail = stream.strip()
            break
    return detail or fallback


def _path_from_environment(value: str, *, variable: str) -> Path:
    if not value or any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise CacheSetupError(f"{variable} must be a non-empty path without control characters")
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise CacheSetupError(f"{variable} must be an absolute path: {value}")
    return path


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _repository_boundary(repository: Path) -> Path:
    """Return the nearest conservative worktree boundary for a repository path."""

    for candidate in (repository, *repository.parents):
        marker = candidate / ".git"
        try:
            if marker.exists() or marker.is_symlink():
                return candidate
        except OSError as error:
            raise CacheSetupError(
                f"cannot inspect repository boundary marker {marker}: {error}"
            ) from error
    return repository


def _validate_external_path(path: Path, *, repo: Path, label: str) -> Path:
    try:
        resolved = path.resolve(strict=False)
    except OSError as error:
        raise CacheSetupError(f"cannot resolve {label} {path}: {error}") from error
    if resolved == Path(resolved.anchor):
        raise CacheSetupError(f"{label} must not be a filesystem root: {path}")
    if _is_within(resolved, repo):
        raise CacheSetupError(f"{label} must be outside the repository: {path}")
    try:
        if path.exists() or path.is_symlink():
            metadata = path.lstat()
            if stat.S_ISLNK(metadata.st_mode):
                raise CacheSetupError(f"{label} must not be a symlink: {path}")
            if not stat.S_ISDIR(metadata.st_mode):
                raise CacheSetupError(f"{label} must be a directory: {path}")
    except OSError as error:
        raise CacheSetupError(f"cannot inspect {label} {path}: {error}") from error
    return resolved


def _ensure_private_directory(path: Path, *, label: str) -> Path:
    try:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        metadata = path.lstat()
    except OSError as error:
        raise CacheSetupError(f"cannot create {label} {path}: {error}") from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise CacheSetupError(f"{label} must be a real directory: {path}")
    if os.name != "nt":
        if hasattr(os, "getuid") and metadata.st_uid != os.getuid():
            raise CacheSetupError(f"{label} is not owned by the current user: {path}")
        if stat.S_IMODE(metadata.st_mode) & 0o077:
            raise CacheSetupError(
                f"{label} permissions must not allow group or other access: {path}"
            )
    if not os.access(path, os.W_OK | os.X_OK):
        raise CacheSetupError(f"{label} is not writable: {path}")
    return path


def resolve_state_root(
    *,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
    os_name: str | None = None,
    state_home: Path | None = None,
) -> Path:
    """Return the user-local private state root shared by every shipped script.

    The ladder is: explicit ``state_home``, ``SD_AI_COMMAND_PACK_STATE_HOME``,
    ``XDG_STATE_HOME``, the Windows local-app-data location, then the home
    fallback. Callers wrap :class:`CommandError` in their own error type.
    """

    if state_home is not None:
        candidate = state_home.expanduser()
        if not candidate.is_absolute():
            raise CommandError("state home must be an absolute path")
        return candidate
    env = os.environ if environ is None else environ
    override = env.get(STATE_HOME_ENV, "").strip()
    if override:
        path = Path(override).expanduser()
        if not path.is_absolute():
            raise CommandError(f"{STATE_HOME_ENV} must be an absolute path")
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
        raise CommandError("home directory must resolve to an absolute path")
    return resolved_home / ".local" / "state" / "sd-ai-command-pack"


def ensure_private_directory(path: Path, *, label: str, reference: str | None = None) -> Path:
    """Create ``path`` as a private 0700 directory, refusing symlinks.

    Distinct from :func:`_ensure_private_directory`, which additionally enforces
    uid ownership and a strict permission mask appropriate to cache namespaces.
    A failing ``mkdir`` is always re-raised as :class:`CommandError` chaining the
    originating ``OSError`` as ``__cause__``, so callers can rebuild their own
    structured evidence without losing it.

    ``reference`` is the caller-chosen path rendering appended to the symlink and
    unusable diagnostics; callers that redact host paths pass ``path.name``, and
    callers that name no path at all omit it. The library never picks a
    rendering of its own, so no consumer's redaction posture changes here.
    """

    suffix = f": {reference}" if reference else ""
    if path.is_symlink():
        raise CommandError(f"{label} must not be a symlink{suffix}")
    try:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    except OSError as error:
        # ``str(error)`` embeds the absolute target (``[Errno 13] Permission
        # denied: '/…'``), which would defeat a caller's redaction posture. Only
        # ``strerror`` reaches the message; the full ``OSError`` stays on
        # ``__cause__`` for callers that build structured evidence from it.
        detail = error.strerror or type(error).__name__
        raise CommandError(f"cannot create {label}: {detail}") from error
    if path.is_symlink() or not path.is_dir():
        raise CommandError(f"{label} is unusable{suffix}")
    try:
        path.chmod(0o700)
    except OSError:
        # Permission tightening is best-effort on filesystems without chmod support.
        pass
    return path


def _cache_namespace_name(repo: Path) -> str:
    uid = str(os.getuid()) if hasattr(os, "getuid") else "user"
    digest = hashlib.sha256(os.fsencode(str(repo))).hexdigest()[:16]
    return f"sd-ai-command-pack-{uid}-{digest}"


def _prepare_namespace(base: Path, *, repo: Path, source: str) -> Path:
    validated_base = _validate_external_path(base, repo=repo, label=source)
    try:
        validated_base.mkdir(mode=0o700, parents=True, exist_ok=True)
    except OSError as error:
        raise CacheSetupError(f"cannot create {source} {validated_base}: {error}") from error
    namespace = validated_base / _cache_namespace_name(repo)
    namespace = _validate_external_path(namespace, repo=repo, label="pack cache namespace")
    return _ensure_private_directory(namespace, label="pack cache namespace")


def _cache_root_candidates(
    environment: Mapping[str, str],
    *,
    repo: Path,
) -> tuple[tuple[Path, str, bool], ...]:
    explicit_root = environment.get(CACHE_ROOT_ENV, "")
    if explicit_root:
        return ((
            _path_from_environment(explicit_root, variable=CACHE_ROOT_ENV),
            CACHE_ROOT_ENV,
            True,
        ),)

    candidates: list[tuple[Path, str, bool]] = []
    inherited_xdg = environment.get("XDG_CACHE_HOME", "")
    if inherited_xdg:
        try:
            xdg_path = _path_from_environment(inherited_xdg, variable="XDG_CACHE_HOME")
        except CacheSetupError:
            pass
        else:
            namespace_name = _cache_namespace_name(repo)
            if xdg_path.name == CACHE_DIRECTORY_NAMES["XDG_CACHE_HOME"]:
                inherited_namespace = xdg_path.parent
                if inherited_namespace.name == namespace_name:
                    xdg_path = inherited_namespace.parent
            candidates.append((xdg_path, "inherited XDG cache root", False))

    temp_values = [
        environment.get("TMPDIR", ""),
        environment.get("TEMP", ""),
        environment.get("TMP", ""),
        tempfile.gettempdir(),
    ]
    if os.name != "nt":
        # This is only a validated parent; _prepare_namespace creates mode 0700.
        temp_values.append(str(Path("/tmp").resolve()))  # nosec B108
    seen: set[str] = set()
    for value in temp_values:
        if not value:
            continue
        try:
            path = _path_from_environment(value, variable="temporary cache root")
        except CacheSetupError:
            continue
        key = os.path.normcase(str(path))
        if key in seen:
            continue
        seen.add(key)
        candidates.append((path, "temporary cache root", False))
    return tuple(candidates)


def build_tool_environment(
    *,
    repo: PathLike[str] | str | None = None,
    environ: Mapping[str, str] | None = None,
) -> tuple[dict[str, str], dict[str, Path], Path]:
    """Return inherited environment with safe external tool caches configured."""

    environment = dict(os.environ if environ is None else environ)
    repository = Path.cwd() if repo is None else Path(repo)
    try:
        repository = repository.resolve(strict=True)
    except OSError as error:
        raise CacheSetupError(f"cannot resolve repository for cache setup: {error}") from error
    repository = _repository_boundary(repository)

    namespace: Path | None = None
    failures: list[str] = []
    candidates = _cache_root_candidates(environment, repo=repository)
    for base, source, required in candidates:
        try:
            namespace = _prepare_namespace(base, repo=repository, source=source)
            break
        except CacheSetupError as error:
            if required:
                raise
            failures.append(str(error))
    if namespace is None:
        detail = failures[-1] if failures else "no absolute writable cache root is available"
        raise CacheSetupError(
            f"cache setup failed for external tools: {detail}; set {CACHE_ROOT_ENV} "
            "to a private writable directory outside the repository"
        )

    cache_paths: dict[str, Path] = {}
    for variable in CACHE_ENV_KEYS:
        inherited_override = environment.get(variable, "") if variable != "XDG_CACHE_HOME" else ""
        if inherited_override:
            override = _path_from_environment(inherited_override, variable=variable)
            cache_path = _validate_external_path(
                override,
                repo=repository,
                label=f"{variable} cache override",
            )
            cache_path = _ensure_private_directory(
                cache_path,
                label=f"{variable} cache override",
            )
        else:
            cache_path = _ensure_private_directory(
                namespace / CACHE_DIRECTORY_NAMES[variable],
                label=f"{variable} cache directory",
            )
        environment[variable] = str(cache_path)
        cache_paths[variable] = cache_path
    environment.setdefault("GIT_TERMINAL_PROMPT", "0")
    return environment, cache_paths, namespace


def build_tool_execution_plan(
    command: Sequence[str],
    *,
    cwd: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> ToolExecutionPlan:
    """Build an argv-safe command plan with the shared cache environment."""

    if not command:
        raise CommandError("cannot run an empty command")
    environment, cache_paths, namespace = build_tool_environment(
        repo=cwd,
        environ=environ,
    )
    return ToolExecutionPlan(tuple(command), environment, cache_paths, namespace)


def run_command(
    command: list[str],
    *,
    cwd: Path | None = None,
    timeout: int = DEFAULT_COMMAND_TIMEOUT,
    check: bool = False,
    allowed_returncodes: set[int] | None = None,
    capture_output: bool = True,
    stdout: int | None = subprocess.PIPE,
    stderr: int | None = subprocess.PIPE,
    text: bool = True,
    encoding: str = "utf-8",
    errors: str = "replace",
    context: str = "run command",
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a command with a timeout and convert expected failures to messages."""

    plan = build_tool_execution_plan(command, cwd=cwd, environ=env)
    if allowed_returncodes is None:
        allowed_returncodes = {0}
    if capture_output:
        stdout = subprocess.PIPE
        stderr = subprocess.PIPE
    try:
        result = subprocess.run(
            list(plan.command),
            cwd=cwd,
            env=plan.environment,
            check=False,
            capture_output=False,
            stdout=stdout,
            stderr=stderr,
            text=text,
            encoding=encoding if text else None,
            errors=errors if text else None,
            timeout=timeout,
        )
    except FileNotFoundError:
        raise CommandError(f"{command_display(command)} not found while trying to {context}") from None
    except subprocess.TimeoutExpired:
        raise CommandError(
            f"{command_display(command)} timed out after {timeout}s while trying to {context}"
        ) from None
    if check and result.returncode not in allowed_returncodes:
        detail = command_detail(
            result,
            fallback=(
                f"{command_display(command)} exited with status "
                f"{result.returncode}"
            ),
        )
        raise CommandError(f"failed to {context}: {detail}")
    return result


def run_git(
    args: list[str],
    *,
    cwd: Path | None = None,
    timeout: int = DEFAULT_GIT_TIMEOUT,
    check: bool = False,
    allowed_returncodes: set[int] | None = None,
    errors: str = "replace",
    context: str = "run git",
) -> subprocess.CompletedProcess[str]:
    return run_command(
        ["git", *args],
        cwd=cwd,
        timeout=timeout,
        check=check,
        allowed_returncodes=allowed_returncodes,
        errors=errors,
        context=context,
    )


def _run_git_process(
    args: Sequence[str],
    *,
    environment: Mapping[str, str],
    cwd: Path | None,
    timeout: int | None,
    binary: bool,
    input: bytes | None,
    stderr: int | None,
    encoding: str | None,
    errors: str | None,
) -> subprocess.CompletedProcess:
    """Run git with a caller-supplied environment, converting nothing.

    Centralizes the git subprocess invocation (argv, environment, stream and
    decoding options) while letting ``OSError``/``TimeoutExpired`` propagate so
    each caller keeps its own error policy. Never raises on a non-zero return
    code. In binary mode ``encoding``/``errors`` are forced to ``None``.
    """

    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        env=dict(environment),
        check=False,
        stdout=subprocess.PIPE,
        stderr=stderr,
        input=input,
        text=not binary,
        encoding=None if binary else encoding,
        errors=None if binary else errors,
        timeout=timeout,
    )


@overload
def run_git_minimal(
    args: Sequence[str],
    *,
    cwd: Path | None = ...,
    timeout: int | None = ...,
    binary: Literal[False] = ...,
    input: bytes | None = ...,
    stderr: int | None = ...,
    encoding: str | None = ...,
    errors: str | None = ...,
) -> subprocess.CompletedProcess[str]: ...


@overload
def run_git_minimal(
    args: Sequence[str],
    *,
    cwd: Path | None = ...,
    timeout: int | None = ...,
    binary: Literal[True],
    input: bytes | None = ...,
    stderr: int | None = ...,
    encoding: str | None = ...,
    errors: str | None = ...,
) -> subprocess.CompletedProcess[bytes]: ...


@overload
def run_git_minimal(
    args: Sequence[str],
    *,
    cwd: Path | None = ...,
    timeout: int | None = ...,
    binary: bool,
    input: bytes | None = ...,
    stderr: int | None = ...,
    encoding: str | None = ...,
    errors: str | None = ...,
) -> subprocess.CompletedProcess[Any]: ...


def run_git_minimal(
    args: Sequence[str],
    *,
    cwd: Path | None = None,
    timeout: int | None = None,
    binary: bool = False,
    input: bytes | None = None,
    stderr: int | None = subprocess.PIPE,
    encoding: str | None = None,
    errors: str | None = None,
) -> subprocess.CompletedProcess:
    """Run git with a minimal, prompt-disabled, cache-free environment.

    Unlike :func:`run_git`/:func:`run_command`, this does not build the external
    tool cache, so it never raises :class:`CacheSetupError`. It only sets
    ``GIT_TERMINAL_PROMPT=0`` on top of the inherited environment. The process
    always captures stdout (``PIPE``) and, by default, stderr (``PIPE``); text
    mode uses platform-locale decoding with strict errors and no timeout. These
    are capture defaults, not the inherit-streams behavior of a bare
    ``subprocess.run`` call. Propagates ``OSError``/``TimeoutExpired``; never
    raises on non-zero exit.
    """

    environment = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    return _run_git_process(
        args,
        environment=environment,
        cwd=cwd,
        timeout=timeout,
        binary=binary,
        input=input,
        stderr=stderr,
        encoding=encoding,
        errors=errors,
    )


@overload
def run_git_cached(
    args: Sequence[str],
    *,
    repo: Path | None,
    cwd: Path | None = ...,
    timeout: int | None = ...,
    binary: Literal[False] = ...,
    input: bytes | None = ...,
    stderr: int | None = ...,
    encoding: str | None = ...,
    errors: str | None = ...,
) -> subprocess.CompletedProcess[str]: ...


@overload
def run_git_cached(
    args: Sequence[str],
    *,
    repo: Path | None,
    cwd: Path | None = ...,
    timeout: int | None = ...,
    binary: Literal[True],
    input: bytes | None = ...,
    stderr: int | None = ...,
    encoding: str | None = ...,
    errors: str | None = ...,
) -> subprocess.CompletedProcess[bytes]: ...


def run_git_cached(
    args: Sequence[str],
    *,
    repo: Path | None,
    cwd: Path | None = None,
    timeout: int | None = None,
    binary: bool = False,
    input: bytes | None = None,
    stderr: int | None = subprocess.PIPE,
    encoding: str | None = None,
    errors: str | None = None,
) -> subprocess.CompletedProcess:
    """Run git with the shared cache-backed environment.

    ``repo`` selects the repository whose cache namespace
    :func:`build_tool_environment` prepares (and may raise
    :class:`CacheSetupError`); ``cwd`` is the child process working directory
    and is independent — callers using ``git -C`` pass ``cwd=None``. Defaults
    match :func:`run_git_minimal`. Propagates ``CacheSetupError``/``OSError``/
    ``TimeoutExpired``; never raises on non-zero exit.
    """

    environment, _, _ = build_tool_environment(repo=repo)
    environment.setdefault("GIT_TERMINAL_PROMPT", "0")
    return _run_git_process(
        args,
        environment=environment,
        cwd=cwd,
        timeout=timeout,
        binary=binary,
        input=input,
        stderr=stderr,
        encoding=encoding,
        errors=errors,
    )


def run_gh(
    args: list[str],
    *,
    cwd: Path | None = None,
    timeout: int = DEFAULT_GH_TIMEOUT,
    check: bool = False,
    allowed_returncodes: set[int] | None = None,
    errors: str = "replace",
    context: str = "run gh",
) -> subprocess.CompletedProcess[str]:
    return run_command(
        ["gh", *args],
        cwd=cwd,
        timeout=timeout,
        check=check,
        allowed_returncodes=allowed_returncodes,
        errors=errors,
        context=context,
    )


def git_stdout(
    args: list[str],
    *,
    cwd: Path | None = None,
    timeout: int = DEFAULT_GIT_TIMEOUT,
    errors: str = "replace",
    context: str = "run git",
    required: bool = False,
) -> str | None:
    result = run_git(args, cwd=cwd, timeout=timeout, errors=errors, context=context)
    if result.returncode != 0:
        if required:
            detail = command_detail(
                result,
                fallback=f"git exited with status {result.returncode}",
            )
            raise CommandError(f"failed to {context}: {detail}")
        return None
    stripped = result.stdout.strip()
    return stripped or None


def repo_root(*, fallback_to_cwd: bool = False) -> Path:
    toplevel = git_stdout(
        ["rev-parse", "--show-toplevel"],
        context="resolve repository root",
        required=not fallback_to_cwd,
    )
    if toplevel is not None:
        return Path(toplevel).resolve()
    return Path.cwd().resolve()


# ---------------------------------------------------------------------------
# Hardened atomic text write
#
# One owner for "replace a text file atomically" across the session recorder,
# knowledge-base refresh, and review-learnings receipt writers. The temporary
# file is created in the destination's own directory, fsynced, chmod'd to match
# the destination's effective mode, then renamed into place; the parent
# directory is fsynced so the rename survives a crash. Every added guard fails
# by raising rather than by silently writing to the wrong place:
#   * refuse a symlink destination
#   * refuse a cross-filesystem replace (os.replace is only atomic within one)
#   * optional `revalidate` callback re-checked at each TOCTOU-sensitive step
# ---------------------------------------------------------------------------


def default_text_file_mode(destination: Path) -> int:
    if destination.exists():
        return destination.stat().st_mode & 0o777
    current_umask = os.umask(0)
    try:
        return 0o666 & ~current_umask
    finally:
        os.umask(current_umask)


def atomic_write_text(
    destination: Path,
    content: str,
    *,
    errors: str = "strict",
    revalidate: Any | None = None,
    mode: int | None = None,
) -> None:
    if destination.is_symlink():
        raise OSError("target is a symlink")
    if revalidate is not None:
        revalidate()
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(content.encode("utf-8", errors=errors))
            temporary.flush()
            os.fsync(temporary.fileno())
        if temporary_path.stat().st_dev != destination.parent.stat().st_dev:
            raise OSError("atomic update would cross filesystems")
        if revalidate is not None:
            revalidate()
        os.chmod(
            temporary_path,
            mode if mode is not None else default_text_file_mode(destination),
        )
        if revalidate is not None:
            revalidate()
        os.replace(temporary_path, destination)
        temporary_path = None
        try:
            directory_fd = os.open(destination.parent, os.O_RDONLY)
        except OSError:
            directory_fd = None
        if directory_fd is not None:
            try:
                os.fsync(directory_fd)
            except OSError:
                pass
            finally:
                os.close(directory_fd)
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass


# ---------------------------------------------------------------------------
# Environment-blocked recovery evidence
#
# One additive, self-versioned structured fragment shared by lifecycle mutation
# owners (session recorder, finish-work, housekeeping, work-loop persistence,
# knowledge-base refresh, toolchain cache setup). An owner constructs it from
# its own control flow when it hits a filesystem or authority *boundary* — never
# by parsing stderr and never for a repository defect — to name the boundary,
# the last verified checkpoint, whether a narrow retry is safe, and a bounded,
# secret-safe recovery action. It carries no executable authority: `recoveryAction`
# is argv-shaped data or a skill-owned instruction, never an interpolated shell
# string. Consumers validate the fragment and, if they cannot, fall back to the
# host command's own bounded diagnostic rather than acting on partial evidence.
#
# Fragment schema (schemaVersion 1):
#   schemaVersion : int, fixed 1 (the fragment's own version, independent of any
#                   host result object; hosts attach it additively and keep their
#                   own schemaVersion unchanged)
#   reasonCode    : str, fixed "environment_blocked"
#   boundary      : one of ENVIRONMENT_BOUNDARIES
#   operation     : bounded, command-owned operation identifier
#   retryable     : bool, owner-derived (never inferred by presentation code);
#                   True is rejected when mutationState is "unknown"
#   checkpoint    : bounded name of the last lifecycle checkpoint the owner verified
#   mutationState : one of ENVIRONMENT_MUTATION_STATES
#   recoveryAction: None, {"kind": "argv", "argv": [token, ...]}, or
#                   {"kind": "skill", "instruction": text}; all bounded and redacted
#   diagnostic    : bounded (ENVIRONMENT_DIAGNOSTIC_LIMIT), control-stripped, with
#                   URL credentials and obvious tokens redacted
# ---------------------------------------------------------------------------

ENVIRONMENT_BLOCKED_REASON = "environment_blocked"
ENVIRONMENT_BLOCKED_SCHEMA_VERSION = 1
ENVIRONMENT_BOUNDARIES = (
    "git-metadata",
    "user-state",
    "tool-cache",
    "kb-target",
    "managed-payload",
)
ENVIRONMENT_MUTATION_STATES = ("none", "partial-recoverable", "unknown")
ENVIRONMENT_DIAGNOSTIC_LIMIT = 500
ENVIRONMENT_RECOVERY_KINDS = ("argv", "skill")
_ENVIRONMENT_FIELD_LIMIT = 120
_ENVIRONMENT_RECOVERY_TOKEN_LIMIT = 32
_ENVIRONMENT_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]+")
_ENVIRONMENT_URL_CREDENTIAL_RE = re.compile(r"([A-Za-z][A-Za-z0-9+.\-]*://)[^/@\s]+@")
# Controlled path rendering: an absolute POSIX filesystem path token — a "/" that
# begins a path segment and is not part of a scheme://host/path URL, whose
# internal slashes are always preceded by an alphanumeric, ":" or "/". Plain
# remote URLs are permitted diagnostic context (design permits "remote URLs with
# credentials" removal only), so the negative lookbehind deliberately spares them.
_ENVIRONMENT_FS_PATH_RE = re.compile(r"(?<![A-Za-z0-9:/])/[^\s'\"]+")
# --- Shared secret shapes ---------------------------------------------------
# One definition of "what a secret looks like", consumed by two policies that
# must NOT be collapsed into one another (see design.md, R2):
#   * `_redact_environment_text` below SUBSTITUTES  -- fail-open: it must never
#     drop the diagnostic that recovery depends on, so it returns a bounded,
#     redacted string and never raises.
#   * `sd-ai-command-pack-fleet-timing.py` REJECTS   -- fail-closed: it raises
#     rather than accept secret-shaped operator input into a timing record.
#
# Each shape therefore carries two forms:
#   * a DETECTOR form, which may stay maximally loose -- a bare prefix is
#     sufficient evidence to refuse input; a false positive only costs an
#     operator a rejected timing record.
#   * a SUBSTITUTER form, which must be conservative in extent but complete in
#     body coverage. A prefix-only substituter is the core hazard: it would
#     redact only the bare token prefix and leave the token body in the text --
#     worse than the old redactor. Every substituter row therefore anchors a
#     body charset and a minimum length (or, for PEM, a bounded multi-line
#     span). See tests for the concrete token-prefix leak cases.
_SECRET_TOKEN_BODY = r"[A-Za-z0-9._-]{8,}"
# (name, detector, substituter). A name prefixed "kv-" keeps its capturing
# group 1 (the "key:" / "key=" lead-in) and redacts only the value; all other
# rows redact the whole match. Order is significant for the substituter pass:
# the PEM span MUST run before the key-value rule, or a PRIVATE KEY header
# sitting after a "key:" on the same line is partly eaten by the shorter rule.
_SECRET_SHAPES: tuple[tuple[str, str, str], ...] = (
    (
        "pem-private-key",
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
        # Prefer the terminated span to the END footer (minimal, so it stops at
        # the first footer). When no footer sits within the bound -- a truncated
        # or unterminated key -- fall back to a bounded span from the header so
        # the body is still redacted instead of leaking. The {0,4096} bound caps
        # how much trailing diagnostic an unterminated header can consume.
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----"
        r"(?:[\s\S]{0,4096}?-----END [A-Z ]*PRIVATE KEY-----|[\s\S]{0,4096})",
    ),
    (
        "github-classic",
        r"gh[pousr]_",
        r"gh[pousr]_" + _SECRET_TOKEN_BODY,
    ),
    (
        "github-fine-grained",
        # gh[pousr]_ excludes the "i", so it cannot match github_pat_ at all.
        r"github_pat_",
        r"github_pat_" + _SECRET_TOKEN_BODY,
    ),
    (
        "slack",
        r"xox[baprs]-",
        r"xox[baprs]-" + _SECRET_TOKEN_BODY,
    ),
    (
        "openai",
        # The leading (?<![A-Za-z0-9]) keeps "sk-" from matching mid-word, so an
        # ordinary hyphenated word ("task-management" -> "sk-management") is not
        # redacted by the lib nor spuriously rejected by fleet-timing.
        r"(?<![A-Za-z0-9])sk-[A-Za-z0-9]",
        r"(?<![A-Za-z0-9])sk-" + _SECRET_TOKEN_BODY,
    ),
    (
        "bearer",
        r"bearer\s+[A-Za-z0-9._-]",
        r"bearer\s+" + _SECRET_TOKEN_BODY,
    ),
    (
        # Bounded key-value form. The detector's trailing \S+ is greedy across
        # punctuation; the substituter's value charset stops at whitespace,
        # comma, and semicolon so surrounding diagnostic context survives (R3).
        "kv-secret",
        r"(?:token|password|secret|api[_-]?key)\s*[:=]\s*\S+",
        r"((?:token|password|secret|api[_-]?key)\s*[:=]\s*)[^\s,;]+",
    ),
)
# Loose detector alternation for the fail-closed reject side.
_ENVIRONMENT_SECRET_DETECTOR_RE = re.compile(
    "(?i)(?:" + "|".join(detector for _, detector, _ in _SECRET_SHAPES) + ")"
)
# Ordered (pattern, replacement) pairs for the fail-open substitute side.
_ENVIRONMENT_SECRET_SUBSTITUTIONS: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (
        re.compile("(?i)" + substituter),
        r"\1[redacted]" if name.startswith("kv-") else "[redacted]",
    )
    for name, _, substituter in _SECRET_SHAPES
)


def compiled_secret_detector() -> re.Pattern[str]:
    """Loose secret DETECTOR alternation for the fail-closed reject policy.

    Seeing a covered prefix is sufficient evidence to refuse input, so this
    column may over-match; a false positive only costs a rejected record.
    Callers that must not drop text (diagnostic redaction) use the substituter
    set instead -- a detector reused under ``.sub()`` would leave secret bodies
    behind. See ``_redact_environment_text``.
    """
    return _ENVIRONMENT_SECRET_DETECTOR_RE


class EnvironmentEvidenceError(CommandError):
    """Raised when environment-blocked recovery evidence is malformed.

    Construction errors are owner-side programming bugs (an unknown boundary or
    an incoherent retry claim) and must fail closed rather than emit partial
    evidence; validation errors tell a consumer to fall back to the host
    command's own bounded diagnostic.
    """


def _redact_environment_text(value: object, *, limit: int) -> str:
    """Bound and redact free text for the fragment: strip control bytes, remove
    URL credentials, obvious tokens, and arbitrary absolute filesystem paths
    (rendered as ``[path]``), collapse whitespace, and truncate. Plain remote
    URLs without credentials are preserved as diagnostic context."""
    text = "" if value is None else str(value)
    text = _ENVIRONMENT_URL_CREDENTIAL_RE.sub(r"\1[redacted]@", text)
    for pattern, replacement in _ENVIRONMENT_SECRET_SUBSTITUTIONS:
        text = pattern.sub(replacement, text)
    text = _ENVIRONMENT_FS_PATH_RE.sub("[path]", text)
    text = _ENVIRONMENT_CONTROL_RE.sub(" ", text)
    text = " ".join(text.split())
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


def _normalize_recovery_action(action: object) -> dict[str, object] | None:
    """Validate and bound a recovery action into argv-shaped or skill-owned data."""
    if action is None:
        return None
    if not isinstance(action, Mapping):
        raise EnvironmentEvidenceError("recovery action must be a mapping or None")
    kind = action.get("kind")
    if kind not in ENVIRONMENT_RECOVERY_KINDS:
        raise EnvironmentEvidenceError(f"unknown recovery action kind: {kind!r}")
    if kind == "argv":
        raw = action.get("argv")
        if not isinstance(raw, (list, tuple)) or not raw:
            raise EnvironmentEvidenceError(
                "argv recovery action requires a non-empty argv list"
            )
        tokens = [
            _redact_environment_text(token, limit=_ENVIRONMENT_FIELD_LIMIT)
            for token in list(raw)[:_ENVIRONMENT_RECOVERY_TOKEN_LIMIT]
        ]
        if any(not token for token in tokens):
            raise EnvironmentEvidenceError(
                "argv recovery action tokens must be non-empty after redaction"
            )
        return {"kind": "argv", "argv": tokens}
    instruction = _redact_environment_text(
        action.get("instruction", ""), limit=ENVIRONMENT_DIAGNOSTIC_LIMIT
    )
    if not instruction:
        raise EnvironmentEvidenceError(
            "skill recovery action requires a non-empty instruction"
        )
    return {"kind": "skill", "instruction": instruction}


def build_environment_blocked_evidence(
    *,
    boundary: str,
    operation: str,
    checkpoint: str,
    mutation_state: str,
    retryable: bool,
    recovery_action: object = None,
    diagnostic: str = "",
) -> dict[str, object]:
    """Construct the additive environment-blocked fragment from owner control flow.

    Raises EnvironmentEvidenceError on an unknown boundary or mutation state, a
    non-boolean retry flag, a retry advertised over an unknown mutation state, or
    a malformed recovery action. Owners call this only for a genuine environment
    or authority boundary; unknown failures keep their existing failure result.
    """
    if boundary not in ENVIRONMENT_BOUNDARIES:
        raise EnvironmentEvidenceError(f"unknown environment boundary: {boundary!r}")
    if mutation_state not in ENVIRONMENT_MUTATION_STATES:
        raise EnvironmentEvidenceError(f"unknown mutation state: {mutation_state!r}")
    if not isinstance(retryable, bool):
        raise EnvironmentEvidenceError("retryable must be a boolean")
    if retryable and mutation_state == "unknown":
        raise EnvironmentEvidenceError(
            "a retryable block cannot advertise an unknown mutation state"
        )
    return {
        "schemaVersion": ENVIRONMENT_BLOCKED_SCHEMA_VERSION,
        "reasonCode": ENVIRONMENT_BLOCKED_REASON,
        "boundary": boundary,
        "operation": _redact_environment_text(operation, limit=_ENVIRONMENT_FIELD_LIMIT),
        "retryable": retryable,
        "checkpoint": _redact_environment_text(
            checkpoint, limit=_ENVIRONMENT_FIELD_LIMIT
        ),
        "mutationState": mutation_state,
        "recoveryAction": _normalize_recovery_action(recovery_action),
        "diagnostic": _redact_environment_text(
            diagnostic, limit=ENVIRONMENT_DIAGNOSTIC_LIMIT
        ),
    }


def validate_environment_blocked_evidence(fragment: object) -> dict[str, object]:
    """Validate a fragment as a consumer would and return its normalized form.

    Enforces reasonCode, the supported schemaVersion, bounded enums, and the
    retry/mutation coherence rule, then re-bounds and re-redacts every field by
    rebuilding through the composer so unknown extra fields are dropped. Raises
    EnvironmentEvidenceError when the fragment is unusable; a consumer that
    catches it must fall back to the host command's own bounded diagnostic
    rather than act on partial evidence.
    """
    if not isinstance(fragment, Mapping):
        raise EnvironmentEvidenceError("environment-blocked evidence must be a mapping")
    if fragment.get("reasonCode") != ENVIRONMENT_BLOCKED_REASON:
        raise EnvironmentEvidenceError(
            f"reasonCode must be {ENVIRONMENT_BLOCKED_REASON}"
        )
    if fragment.get("schemaVersion") != ENVIRONMENT_BLOCKED_SCHEMA_VERSION:
        raise EnvironmentEvidenceError(
            "unsupported environment-blocked schemaVersion: "
            f"{fragment.get('schemaVersion')!r}"
        )
    retryable = fragment.get("retryable")
    if not isinstance(retryable, bool):
        raise EnvironmentEvidenceError("retryable must be a boolean")
    return build_environment_blocked_evidence(
        boundary=str(fragment.get("boundary")),
        operation=str(fragment.get("operation", "")),
        checkpoint=str(fragment.get("checkpoint", "")),
        mutation_state=str(fragment.get("mutationState")),
        retryable=retryable,
        recovery_action=fragment.get("recoveryAction"),
        diagnostic=str(fragment.get("diagnostic", "")),
    )


def cache_setup_blocked_evidence(
    error: CacheSetupError,
    *,
    operation: str,
    checkpoint: str = "cache-setup",
) -> dict[str, object]:
    """Classify a cache-setup failure as a tool-cache environment block.

    Cache preparation runs before any lifecycle mutation and is idempotent:
    `build_tool_environment` reuses the same private per-repository namespace on
    a repeat run (see the namespace-reuse tests), so a failure never leaves a
    partial mutation and is always safe to retry once the environment is fixed.
    The recovery action is operator-side configuration expressed as a bounded
    skill instruction, never a command to auto-run.
    """
    return build_environment_blocked_evidence(
        boundary="tool-cache",
        operation=operation,
        checkpoint=checkpoint,
        mutation_state="none",
        retryable=True,
        recovery_action={
            "kind": "skill",
            "instruction": (
                f"Set {CACHE_ROOT_ENV} to a private writable directory outside "
                f"the repository, then retry {operation}."
            ),
        },
        diagnostic=str(error),
    )


def _cache_env_main(argv: Sequence[str]) -> int:
    args = list(argv)
    as_json = "--json" in args
    if as_json:
        args = [item for item in args if item != "--json"]
    if len(args) != 3 or args[0] != "cache-env" or args[1] != "--repo":
        print(
            "usage: sd_ai_command_pack_lib.py cache-env --repo PATH [--json]",
            file=sys.stderr,
        )
        return 2
    try:
        environment, _, _ = build_tool_environment(repo=args[2])
    except CacheSetupError as error:
        if as_json:
            # validate_environment_blocked_evidence gets its first non-test
            # caller here: a malformed fragment fails at the producer rather
            # than reaching toolchain.sh (and an agent) as a plausible blocker.
            evidence = validate_environment_blocked_evidence(
                cache_setup_blocked_evidence(error, operation="toolchain cache setup")
            )
            print(json.dumps({"outcome": "blocked", "environmentBlocked": evidence}))
        else:
            print(f"error: {error}", file=sys.stderr)
        return 2
    if as_json:
        cache_env = {variable: environment[variable] for variable in CACHE_ENV_KEYS}
        print(json.dumps({"outcome": "ok", "cacheEnv": cache_env}))
    else:
        for variable in CACHE_ENV_KEYS:
            print(f"{variable}={environment[variable]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cache_env_main(sys.argv[1:]))
