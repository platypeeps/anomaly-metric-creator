#!/usr/bin/env python3
"""Shared stdlib helpers for shipped sd-ai-command-pack Python scripts."""

from __future__ import annotations

import hashlib
import os
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from os import PathLike
from pathlib import Path
from typing import Iterable, Mapping, Sequence

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
CACHE_ROOT_ENV = "SD_AI_COMMAND_PACK_CACHE_ROOT"
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


def _cache_env_main(argv: Sequence[str]) -> int:
    if len(argv) != 3 or argv[0] != "cache-env" or argv[1] != "--repo":
        print("usage: sd_ai_command_pack_lib.py cache-env --repo PATH", file=sys.stderr)
        return 2
    try:
        environment, _, _ = build_tool_environment(repo=argv[2])
    except CacheSetupError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    for variable in CACHE_ENV_KEYS:
        print(f"{variable}={environment[variable]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cache_env_main(sys.argv[1:]))
