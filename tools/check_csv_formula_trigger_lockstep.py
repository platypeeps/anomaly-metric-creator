#!/usr/bin/env python3
"""Assert the CSV formula-injection trigger set is in lockstep across its two sites.

Recorded command traces are attacker-influenced text: whoever can reach the
simulator chooses what gets recorded. Two independent code paths export that
text as CSV, and a spreadsheet executes a cell that opens with a formula
trigger, so both paths apostrophe-prefix such cells. The two sites:

* ``src/anomaly_metric_creator/trace_bundle.py`` -- ``_CSV_FORMULA_TRIGGERS``,
  a tuple of single-character strings applied by ``_neutralize_csv_cell`` at
  the ``write_trace_bundle_csv`` boundary. This is the server-side writer, the
  path ``amc trace-bundle export-csv`` drives.
* ``src/anomaly_metric_creator/server_debug_ui.py`` -- the ``csvCell`` guard
  inside the embedded debug-UI JavaScript, which builds its CSV in the
  operator's browser and never reaches the Python writer at all.

Nothing in the code makes one follow the other: the Python set is a tuple
literal and the JavaScript set is a regular-expression character class in a
string template. Adding a trigger to one and not the other reopens the hole on
the surface that was not touched, silently, which is precisely the drift this
check exists to fail on.

Extraction is deliberately asymmetric, each side parsed the way that side is
actually authored:

* Python: the module is parsed with ``ast`` and the ``_CSV_FORMULA_TRIGGERS``
  assignment is read with ``ast.literal_eval``. A regex over source text would
  be spoofed by a reformat, a line wrap, or an escape rewritten from ``"\\t"``
  to ``"\\x09"``; the AST sees the values.
* JavaScript: the guard line carries the marker ``csv-formula-triggers:`` in a
  comment immediately above it, and the class body of the following
  ``/^[...]/`` literal is unescaped into a character set. The marker is what
  anchors the scan -- not the shape of ``csvCell``'s body -- so the guard can
  be rewritten freely as long as the marker travels with it. A refactor that
  drops the marker exits 2 (structural) rather than 0, so the check fails loud
  instead of passing vacuously over a file it can no longer read.

Usage::

    check_csv_formula_trigger_lockstep.py [TRACE_BUNDLE] [DEBUG_UI]

Both paths default to the repository-root modules; they are overridable so the
test suite can point the check at fixtures. The hook passes no filenames
(``pass_filenames: false``) because the check reads the two files as a pair --
a one-file subset cannot be compared against anything.

Exit codes:

* ``0`` -- both sites declare the same trigger set (the in-step message, naming
  the set, is printed to stdout).
* ``1`` -- the sets differ; one diagnostic naming the per-side difference and
  both files is written to stderr.
* ``2`` -- a structural problem: the Python assignment is missing or is not a
  tuple/list of single-character strings, the JavaScript marker or its regex
  literal is missing, or a file is unreadable or unparseable.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TRACE_BUNDLE = _REPO_ROOT / "src" / "anomaly_metric_creator" / "trace_bundle.py"
_DEBUG_UI = _REPO_ROOT / "src" / "anomaly_metric_creator" / "server_debug_ui.py"

# The module-level name holding the Python-side trigger tuple.
_PYTHON_TRIGGER_NAME = "_CSV_FORMULA_TRIGGERS"

# The comment marker that anchors the JavaScript scan, and the character-class
# regex literal expected on one of the lines that follow it.
_JS_MARKER = "csv-formula-triggers:"
_JS_CLASS_RE = re.compile(r"/\^\[(?P<body>[^\]]+)\]/")

# How many lines after the marker to search for the regex literal. Wide enough
# for a wrapped comment between the marker and the guard, narrow enough that an
# unrelated character class further down the file cannot be picked up.
_JS_MARKER_WINDOW = 8

# Escape sequences the JavaScript class body may use for the non-printing
# triggers, plus the regex escapes that are literal characters inside a class.
_JS_ESCAPES = {
    "t": "\t",
    "r": "\r",
    "n": "\n",
    "f": "\f",
    "v": "\v",
}


class _LockstepError(Exception):
    """A trigger set could not be located or a file could not be read (exit 2)."""


def _python_triggers(path: Path) -> frozenset[str]:
    """Return the trigger characters declared by ``_CSV_FORMULA_TRIGGERS``.

    Raises ``_LockstepError`` if the file is unreadable or unparseable, the
    assignment is absent, its value is not a literal sequence, or any element
    is not a single character."""
    try:
        source = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise _LockstepError(f"cannot read {path}: {exc}") from exc
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        raise _LockstepError(f"cannot parse {path}: {exc}") from exc
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        names = [t.id for t in node.targets if isinstance(t, ast.Name)]
        if _PYTHON_TRIGGER_NAME not in names:
            continue
        try:
            value = ast.literal_eval(node.value)
        except ValueError as exc:
            raise _LockstepError(
                f"{path}: {_PYTHON_TRIGGER_NAME} is not a literal sequence: {exc}"
            ) from exc
        if not isinstance(value, (tuple, list)) or not value:
            raise _LockstepError(
                f"{path}: {_PYTHON_TRIGGER_NAME} must be a non-empty tuple or "
                f"list; got {value!r}"
            )
        for element in value:
            if not isinstance(element, str) or len(element) != 1:
                raise _LockstepError(
                    f"{path}: every {_PYTHON_TRIGGER_NAME} element must be a "
                    f"single-character string; got {element!r}"
                )
        return frozenset(value)
    raise _LockstepError(f"{path}: no module-level {_PYTHON_TRIGGER_NAME} assignment")


def _unescape_js_class(body: str, path: Path) -> frozenset[str]:
    """Expand a JavaScript character-class body into its character set.

    Ranges (``a-z``) are rejected rather than expanded: the trigger set is a
    handful of unrelated punctuation characters, so a range here means the
    guard was rewritten into a shape this check cannot compare honestly, and
    silently expanding one would let a set far wider than the Python tuple pass
    as "in step"."""
    # Tokenize first: each token is one class member, and a token records
    # whether it was written escaped. Range detection then reads tokens rather
    # than raw text, so `\-` (a literal hyphen, what the guard writes) is never
    # mistaken for a range operator.
    tokens: list[tuple[str, bool]] = []
    index = 0
    while index < len(body):
        char = body[index]
        if char == "\\":
            if index + 1 >= len(body):
                raise _LockstepError(
                    f"{path}: trailing backslash in the trigger character class"
                )
            nxt = body[index + 1]
            tokens.append((_JS_ESCAPES.get(nxt, nxt), True))
            index += 2
            continue
        tokens.append((char, False))
        index += 1
    for position, (char, was_escaped) in enumerate(tokens):
        is_range_operator = (
            char == "-"
            and not was_escaped
            and 0 < position < len(tokens) - 1
        )
        if is_range_operator:
            span = "".join(t[0] for t in tokens[position - 1 : position + 2])
            raise _LockstepError(
                f"{path}: the trigger character class contains a range "
                f"({span!r}); this check compares explicit characters only"
            )
    chars = {char for char, _ in tokens}
    if not chars:
        raise _LockstepError(f"{path}: the trigger character class is empty")
    return frozenset(chars)


def _javascript_triggers(path: Path) -> frozenset[str]:
    """Return the trigger characters declared by the marked ``csvCell`` guard.

    Raises ``_LockstepError`` if the file is unreadable, the marker is absent,
    or no character-class regex literal follows it within the search window."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise _LockstepError(f"cannot read {path}: {exc}") from exc
    for offset, line in enumerate(lines):
        if _JS_MARKER not in line:
            continue
        window = lines[offset : offset + _JS_MARKER_WINDOW]
        for candidate in window:
            match = _JS_CLASS_RE.search(candidate)
            if match:
                return _unescape_js_class(match.group("body"), path)
        raise _LockstepError(
            f"{path}: found the {_JS_MARKER!r} marker at line {offset + 1} but "
            f"no /^[...]/ character class within {_JS_MARKER_WINDOW} lines"
        )
    raise _LockstepError(
        f"{path}: no {_JS_MARKER!r} marker; the debug UI's csvCell guard must "
        f"carry it so this check can find the trigger set"
    )


def _render(chars: frozenset[str]) -> str:
    """Render a trigger set as a stable, readable list of Python reprs."""
    return ", ".join(repr(char) for char in sorted(chars))


def check(trace_bundle: Path, debug_ui: Path) -> tuple[bool, str]:
    """Return ``(ok, message)`` for the two trigger sets.

    ``ok`` is True when they are equal. Raises ``_LockstepError`` (exit 2) for
    structural problems. Single source of truth for the comparison and both
    message shapes."""
    python_set = _python_triggers(trace_bundle)
    js_set = _javascript_triggers(debug_ui)
    if python_set == js_set:
        return True, (
            f"CSV formula triggers in lockstep: {_render(python_set)} "
            f"({trace_bundle.name} {_PYTHON_TRIGGER_NAME} == {debug_ui.name} "
            f"csvCell guard)"
        )
    only_python = python_set - js_set
    only_js = js_set - python_set
    parts = []
    if only_python:
        parts.append(
            f"{trace_bundle.name} {_PYTHON_TRIGGER_NAME} declares "
            f"{_render(only_python)} which {debug_ui.name} csvCell does not"
        )
    if only_js:
        parts.append(
            f"{debug_ui.name} csvCell declares {_render(only_js)} which "
            f"{trace_bundle.name} {_PYTHON_TRIGGER_NAME} does not"
        )
    return False, (
        "CSV formula trigger drift: "
        + "; ".join(parts)
        + ". Both CSV export paths must neutralize the same set -- a trigger "
        "guarded on only one path leaves the other exporting a live formula. "
        "Update both sites together."
    )


def main(argv: list[str]) -> int:
    args = argv[1:]
    if any(a in ("-h", "--help") for a in args):
        print(__doc__)
        return 0
    if len(args) > 2:
        print(
            "usage: check_csv_formula_trigger_lockstep.py [TRACE_BUNDLE] [DEBUG_UI]",
            file=sys.stderr,
        )
        return 2
    trace_bundle = Path(args[0]) if len(args) >= 1 else _TRACE_BUNDLE
    debug_ui = Path(args[1]) if len(args) >= 2 else _DEBUG_UI
    try:
        ok, message = check(trace_bundle, debug_ui)
    except _LockstepError as exc:
        print(f"check_csv_formula_trigger_lockstep: {exc}", file=sys.stderr)
        return 2
    if ok:
        print(message)
        return 0
    print(message, file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
