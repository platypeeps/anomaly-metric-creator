#!/usr/bin/env python3
"""Forbid bare or unpinned ``pip install`` in GitHub Actions workflows.

After ``actions/setup-python`` runs, a bare ``pip`` can resolve to a
different interpreter than the one just selected (PATH ordering), so the
install can land in the wrong environment. The robust form is
``python -m pip install``, which always targets the selected interpreter;
``uv pip install`` (uv-managed) is also fine. Direct third-party installs
must use exact ``==`` pins so CI tooling is reproducible. PR #118 shipped a
bare ``pip install`` in the former standalone Socket workflow; this lint
catches the pattern structurally instead of relying on Copilot to flag it on
each new workflow.

Invoked by the ``workflow-pip`` pre-commit hook with the staged
``.github/workflows/*.yml`` files as arguments (``pass_filenames: true``),
and usable standalone:

    check_workflow_pip.py .github/workflows/ci.yml [...]

A line with a trailing ``# pip-lint: allow`` is exempt (the check is
``line.rstrip().endswith(...)``, so a mid-line occurrence inside a string
literal does not exempt the line).

Detection: each shell-tokenized ``pip``/``pip3`` ``install`` invocation whose
immediately preceding token is neither ``uv`` (``uv pip``) nor a single-dash
short-flag group ending in ``m`` (``-m``, or combined forms like ``-Im``) is
bare. Long ``--flags``, ``pipx``, and other tokens do not exempt it. A line
mixing a good and a bare invocation (``python -m pip … && pip install …``) is
still flagged — each ``pip install`` occurrence is checked independently.
For non-bare installs, direct package arguments must contain an exact ``==``
pin, and ``--upgrade`` / ``-U`` is rejected because it defeats reproducibility.

Exit codes:

* ``0`` — no bare or unpinned ``pip install`` in any checked file.
* ``1`` — at least one bare or unpinned ``pip install``; one diagnostic per
  hit plus a one-line policy footer to stderr.
* ``2`` — argument or I/O error: no paths given, a path that does not exist,
  or a file that cannot be read or UTF-8-decoded.
"""

from __future__ import annotations

import re
import shlex
import sys
from pathlib import Path

_PYTHON_MODULE_FLAG = re.compile(r"^-[A-Za-z0-9]*m$")
_PIP_INSTALL_LINE = re.compile(r"\bpip3?\b\s+install\b")

_ALLOW_MARKER = "# pip-lint: allow"
_COMMAND_SEPARATORS = {"&&", "||", ";", "|"}
_UPGRADE_FLAGS = {"--upgrade", "-U"}
_OPTIONS_WITH_VALUE = {
    "-c", "--constraint",
    "-e", "--editable",
    "-f", "--find-links",
    "-i", "--index-url",
    "-r", "--requirement",
    "-t", "--target",
    "--abi",
    "--config-settings",
    "--extra-index-url",
    "--implementation",
    "--platform",
    "--prefix",
    "--python",
    "--python-version",
    "--root",
    "--src",
    "--trusted-host",
    "--upgrade-strategy",
}


def _is_non_bare_pip(tokens: list[str], pip_index: int) -> bool:
    if pip_index == 0:
        return False
    previous = tokens[pip_index - 1]
    return previous == "uv" or bool(_PYTHON_MODULE_FLAG.fullmatch(previous))


def _direct_install_args(tokens: list[str], install_index: int) -> list[str]:
    args = []
    for token in tokens[install_index + 1:]:
        if token in _COMMAND_SEPARATORS:
            break
        args.append(token)
    return args


def _package_args(install_args: list[str]) -> tuple[list[str], bool]:
    packages = []
    has_upgrade = False
    skip_next = False
    for token in install_args:
        if skip_next:
            skip_next = False
            continue
        if token in _UPGRADE_FLAGS:
            has_upgrade = True
            continue
        if token in _OPTIONS_WITH_VALUE:
            skip_next = True
            continue
        if any(token.startswith(option + "=") for option in _OPTIONS_WITH_VALUE):
            continue
        if token.startswith("-"):
            continue
        # Local paths are not third-party dependencies; their reproducibility is
        # governed by the checked-out tree rather than a package-index resolver.
        if (
            token == "."
            or token.startswith(("./", "../", "/"))
            or token.endswith(".whl")
        ):
            continue
        packages.append(token)
    return packages, has_upgrade


def _check_file(path: Path) -> list[str]:
    """Return one diagnostic line per workflow ``pip install`` hit in ``path``.
    Raises ``OSError`` or ``UnicodeError`` (both re-raised by ``main`` as
    exit 2) if the file cannot be read or UTF-8-decoded."""
    violations: list[str] = []
    text = path.read_text(encoding="utf-8")
    for lineno, line in enumerate(text.splitlines(), start=1):
        if line.rstrip().endswith(_ALLOW_MARKER):
            continue
        if not _PIP_INSTALL_LINE.search(line):
            continue
        try:
            # comments=True so a trailing shell comment (`pip install x==1  #
            # note`) or a comment-only line mentioning pip is not tokenized as
            # extra package args / a bare call (Copilot, PR #125).
            tokens = shlex.split(line, comments=True, posix=True)
        except ValueError as exc:
            violations.append(f"{path}:{lineno}: cannot parse shell line: {exc}")
            continue
        for index, token in enumerate(tokens[:-1]):
            if token not in {"pip", "pip3"} or tokens[index + 1] != "install":
                continue
            if not _is_non_bare_pip(tokens, index):
                violations.append(
                    f"{path}:{lineno}: bare 'pip install' — use "
                    "'python -m pip install' (or 'uv pip install') so the "
                    "install targets the interpreter actions/setup-python "
                    "selected."
                )
                continue
            install_args = _direct_install_args(tokens, index + 1)
            packages, has_upgrade = _package_args(install_args)
            if has_upgrade:
                violations.append(
                    f"{path}:{lineno}: pip install uses --upgrade/-U; pin "
                    "workflow dependencies exactly instead."
                )
                continue
            unpinned = [pkg for pkg in packages if "==" not in pkg]
            if unpinned:
                violations.append(
                    f"{path}:{lineno}: pip install package(s) lack an exact "
                    f"'==' pin: {', '.join(unpinned)}"
                )
                continue
            bad_pin = [pkg for pkg in packages if pkg.endswith("==")]
            if bad_pin:
                violations.append(
                    f"{path}:{lineno}: pip install package(s) have an empty "
                    f"exact pin: {', '.join(bad_pin)}"
                )
                continue
            # `"==" in pkg` accepts a wildcard like `pkg==2.*`, which is not a
            # reproducible exact version. Reject any version containing `*`
            # (Copilot, PR #125).
            wildcard = [pkg for pkg in packages if "*" in pkg.split("==", 1)[1]]
            if wildcard:
                violations.append(
                    f"{path}:{lineno}: pip install package(s) use a wildcard "
                    f"version, not a reproducible '==X.Y.Z' exact pin: "
                    f"{', '.join(wildcard)}"
                )
                continue
    return violations


def main(argv: list[str]) -> int:
    args = argv[1:]
    if not args:
        print("usage: check_workflow_pip.py <workflow.yml>...", file=sys.stderr)
        return 2
    violations: list[str] = []
    for arg in args:
        path = Path(arg)
        # exists() before the read so a bad path is a structural error
        # (exit 2), not a violation (exit 1) or a traceback.
        if not path.exists():
            print(f"check_workflow_pip: no such file: {path}", file=sys.stderr)
            return 2
        try:
            violations.extend(_check_file(path))
        except (OSError, UnicodeError) as exc:
            # UnicodeDecodeError (a UnicodeError/ValueError, not OSError) on a
            # non-UTF-8 file must honor the exit-2 contract, not traceback
            # (Copilot, PR #124).
            print(
                f"check_workflow_pip: cannot read {path}: {exc}", file=sys.stderr
            )
            return 2
    if violations:
        print("\n".join(violations), file=sys.stderr)
        print(
            "\nUse 'python -m pip install PACKAGE==VERSION' (or "
            "'uv pip install PACKAGE==VERSION') in workflows, not bare or "
            "unpinned 'pip' — see 'CI / workflow / dependency hygiene' in "
            "docs/spec/amc/backend/testing-quality.md. Exempt a line "
            "with a trailing "
            "'# pip-lint: allow'.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
