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

Detection: ``pip install`` / ``pip3 install`` not immediately preceded by
``-m `` (``python -m pip``) or ``uv `` (``uv pip``). ``pipx install`` is not
matched. A line that mixes a good and a bad invocation
(``python -m pip … && pip install …``) is still flagged, because the scan
finds the *bare* occurrence past the excluded one.

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

# ``pip``/``pip3`` + ``install`` not preceded by ``-m `` (python -m pip) or
# ``uv `` (uv pip). Both exclusion prefixes are 3 chars wide, so the
# fixed-width negative lookbehinds are legal. ``pipx install`` does not match
# because ``\s+`` cannot consume the ``x``.
_BARE_PIP = re.compile(r"(?<!-m )(?<!uv )\bpip3?\s+install")

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
        if _BARE_PIP.search(line):
            violations.append(
                f"{path}:{lineno}: bare 'pip install' — use "
                "'python -m pip install' (or 'uv pip install') so the install "
                "targets the interpreter actions/setup-python selected."
            )
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
