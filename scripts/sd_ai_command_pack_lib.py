#!/usr/bin/env python3
"""Shared stdlib helpers for shipped sd-ai-command-pack Python scripts."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Iterable

DEFAULT_COMMAND_TIMEOUT = 60
DEFAULT_GIT_TIMEOUT = 60
DEFAULT_GH_TIMEOUT = 120
DEFAULT_TRELLIS_TIMEOUT = 120


class CommandError(RuntimeError):
    """Raised when a required external command cannot complete cleanly."""


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
) -> subprocess.CompletedProcess[str]:
    """Run a command with a timeout and convert expected failures to messages."""

    if not command:
        raise CommandError("cannot run an empty command")
    if allowed_returncodes is None:
        allowed_returncodes = {0}
    if capture_output:
        stdout = subprocess.PIPE
        stderr = subprocess.PIPE
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
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
