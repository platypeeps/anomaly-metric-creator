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
_ENVIRONMENT_SECRET_RE = re.compile(
    r"(?i)(?:bearer\s+|(?:access[_-]?|api[_-]?)?token[=:]\s*|gh[pousr]_)[A-Za-z0-9._\-]{8,}"
)


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
    text = _ENVIRONMENT_SECRET_RE.sub("[redacted]", text)
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
            evidence = cache_setup_blocked_evidence(
                error, operation="toolchain cache setup"
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
