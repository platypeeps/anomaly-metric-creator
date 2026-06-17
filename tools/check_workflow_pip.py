#!/usr/bin/env python3
"""Forbid bare ``pip install`` in GitHub Actions workflows.

After ``actions/setup-python`` runs, a bare ``pip`` can resolve to a
different interpreter than the one just selected (PATH ordering), so the
install can land in the wrong environment. The robust form is
``python -m pip install``, which always targets the selected interpreter;
``uv pip install`` (uv-managed) is also fine. PR #118 shipped a bare
``pip install`` in ``socket.yml``; this lint catches the pattern
structurally instead of relying on Copilot to flag it on each new workflow.

Invoked by the ``workflow-pip`` pre-commit hook with the staged
``.github/workflows/*.yml`` files as arguments (``pass_filenames: true``),
and usable standalone:

    check_workflow_pip.py .github/workflows/ci.yml [...]

A line with a trailing ``# pip-lint: allow`` is exempt (the check is
``line.rstrip().endswith(...)``, so a mid-line occurrence inside a string
literal does not exempt the line).

Detection: a ``pip``/``pip3`` ``install`` invocation whose immediately
preceding whitespace-delimited token is neither ``uv`` (``uv pip``) nor a
single-dash short-flag group ending in ``m`` (``-m``, or combined forms like
``-Im``, with any spacing after) is bare. Long ``--flags``, ``pipx``, and
other tokens do not exempt it. A line mixing a good and a bare invocation
(``python -m pip … && pip install …``) is still flagged — each ``pip
install`` occurrence is checked independently.

Exit codes:

* ``0`` — no bare ``pip install`` in any checked file.
* ``1`` — at least one bare ``pip install``; one diagnostic per hit plus a
  one-line policy footer to stderr.
* ``2`` — argument or I/O error: no paths given, a path that does not exist,
  or an unreadable file.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# A ``pip``/``pip3`` ``install`` invocation. The token immediately before
# ``pip`` decides whether the call is bare (see ``_NON_BARE_PREFIX``).
# ``pipx install`` does not match: ``[ \t]+`` cannot consume the ``x``.
_PIP_INSTALL = re.compile(r"\bpip3?\b[ \t]+install\b")

# The text preceding ``pip`` is non-bare when its last whitespace-delimited
# token is ``uv`` (``uv pip``) or a single-dash short-flag group ending in
# ``m`` (``python -m pip``; combined forms like ``-Im``; any spacing after).
# Long ``--flags`` do not match (the inner ``-`` is not ``\w``), so a genuine
# bare ``pip`` is still caught. Token-based rather than a fixed-width
# lookbehind so combined ``-m`` flags and extra whitespace are handled
# robustly (Copilot, PR #124).
_NON_BARE_PREFIX = re.compile(r"(?:^|\s)(?:uv|-[A-Za-z0-9]*m)[ \t]*$")

_ALLOW_MARKER = "# pip-lint: allow"


def _check_file(path: Path) -> list[str]:
    """Return one diagnostic line per bare-``pip install`` hit in ``path``.
    Raises ``OSError`` (re-raised by ``main`` as exit 2) if the file cannot
    be read."""
    violations: list[str] = []
    text = path.read_text(encoding="utf-8")
    for lineno, line in enumerate(text.splitlines(), start=1):
        if line.rstrip().endswith(_ALLOW_MARKER):
            continue
        for match in _PIP_INSTALL.finditer(line):
            if _NON_BARE_PREFIX.search(line[: match.start()]):
                continue  # python -m pip / -Im pip / uv pip → fine
            violations.append(
                f"{path}:{lineno}: bare 'pip install' — use "
                "'python -m pip install' (or 'uv pip install') so the install "
                "targets the interpreter actions/setup-python selected."
            )
            break  # one diagnostic per line is enough
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
        except OSError as exc:
            print(
                f"check_workflow_pip: cannot read {path}: {exc}", file=sys.stderr
            )
            return 2
    if violations:
        print("\n".join(violations), file=sys.stderr)
        print(
            "\nUse 'python -m pip install' (or 'uv pip install') in workflows, "
            "not bare 'pip' — see CLAUDE.md 'Pre-PR checklist > CI / workflow / "
            "dependency hygiene'. Exempt a line with a trailing "
            "'# pip-lint: allow'.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
