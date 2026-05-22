#!/usr/bin/env python3
"""Forbid duplicate amc module loads in tests/.

`tests/conftest.py:_load_amc()` is the single canonical entry point for
loading `anomaly-metric-creator.py` as an importable module. Other test
files MUST consume the session-scoped `amc` fixture rather than re-issue
`importlib.util.spec_from_file_location(...).exec_module(...)`. The
duplicate exec_module pays the full registry-validation cost again.

This lint is the closing deliverable of **VER-197**, which is itself a
low-priority follow-up split out of the **VER-190** engineering
efficiency review (the review that originally flagged the duplication
on PR #63 and PR #64). Follow VER-197 for the tracking thread; VER-190
holds the historical context.

The check walks each file's AST and flags any *call* whose target is
the identifier `spec_from_file_location`. Patterns caught:

- ``importlib.util.spec_from_file_location(...)`` — attribute call.
- ``import importlib.util as _u; _u.spec_from_file_location(...)`` —
  aliased-module attribute call.
- ``from importlib.util import spec_from_file_location;
  spec_from_file_location(...)`` — bare-name call.
- ``from importlib.util import spec_from_file_location as sfl;
  sfl(...)`` — bare-name call to the import alias. The walker first
  collects every ``ImportFrom`` node that aliases
  ``spec_from_file_location`` and treats each alias as equivalent to
  the canonical name for the rest of the file.
- ``sfl = importlib.util.spec_from_file_location; sfl(...)`` —
  bare-name call to an assignment alias. The walker also walks every
  ``Assign`` / ``AnnAssign`` node whose value is either an
  ``Attribute`` whose ``.attr`` is ``spec_from_file_location`` or a
  ``Name`` whose ``.id`` is an already-collected local alias, and
  iterates to a fixpoint so chained assignments
  (``sfl = importlib.util.spec_from_file_location; sfl2 = sfl;
  sfl2(...)``) cannot evade the lint.

String literals, docstrings, and comments are not flagged because they
are not call nodes.

Exemptions:

- Files named `conftest.py` are skipped wholesale.
- A trailing ``# noqa: amc-load`` on the call's start line opts that
  specific line out of the check. Use sparingly for cases that
  legitimately need a fresh module copy:
  * tests that monkeypatch module-level callables and must isolate state
    (see `tests/test_correctness.py`);
  * collection-time parametrize loaders that fire before pytest
    fixtures resolve (see `tests/test_scenarios.py`).
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

_FN = "spec_from_file_location"
_NOQA_MARKER = "# noqa: amc-load"


def _collect_local_aliases(tree: ast.AST) -> set[str]:
    """Return the set of local names bound to
    ``importlib.util.spec_from_file_location``.

    Always includes ``spec_from_file_location`` itself so a direct
    ``from importlib.util import spec_from_file_location`` import is
    covered. Two additional binding shapes are tracked:

    - ``from X import spec_from_file_location [as Y]`` — the import
      alias ``Y`` (or the canonical name) is added. Module is not
      constrained to ``importlib.util``: any ``from`` import of the
      canonical name is treated as equivalent, since the public stdlib
      API only exposes that symbol via ``importlib.util`` and there is
      no legitimate reason a test file would rebind it.
    - ``Y = importlib.util.spec_from_file_location`` /
      ``Y = <existing_alias>`` — assignment aliases (including
      annotated assignments). Collection iterates to a fixpoint so
      chains like ``a = ...spec_from_file_location; b = a; b(...)``
      cannot evade the lint by hopping through intermediate locals.
      Tuple / list unpacking targets are also handled so
      ``sfl, _ = importlib.util.spec_from_file_location, None`` does
      not slip past — only the simple ``Name`` targets within the
      unpacking are eligible (an ``Attribute`` or ``Subscript`` target
      is ignored because the lint tracks local-name calls).
    """
    aliases: set[str] = {_FN}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == _FN:
                    aliases.add(alias.asname or alias.name)
    while True:
        before = len(aliases)
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                value: ast.expr | None = node.value
                targets: list[ast.expr] = list(node.targets)
            elif isinstance(node, ast.AnnAssign):
                if node.value is None:
                    continue
                value = node.value
                targets = [node.target]
            else:
                continue
            if not _value_resolves_to_fn(value, aliases):
                continue
            for target in targets:
                _collect_target_names(target, aliases)
        if len(aliases) == before:
            break
    return aliases


def _value_resolves_to_fn(value: ast.AST, aliases: set[str]) -> bool:
    if isinstance(value, ast.Attribute) and value.attr == _FN:
        return True
    if isinstance(value, ast.Name) and value.id in aliases:
        return True
    return False


def _collect_target_names(target: ast.AST, aliases: set[str]) -> None:
    if isinstance(target, ast.Name):
        aliases.add(target.id)
    elif isinstance(target, (ast.Tuple, ast.List)):
        for elt in target.elts:
            _collect_target_names(elt, aliases)


def _check_file(path: Path) -> list[str]:
    src = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(src, filename=str(path))
    except SyntaxError as exc:
        return [f"{path}:{exc.lineno}: syntax error: {exc.msg}"]
    lines = src.splitlines()
    local_names = _collect_local_aliases(tree)
    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name: str | None = None
        if isinstance(func, ast.Attribute):
            # Match any ``.spec_from_file_location(...)`` attribute
            # access. Practically this is `importlib.util.<canonical>`
            # (with or without an `import ... as` alias on the module
            # part), since `spec_from_file_location` is only exposed
            # by `importlib.util` and a `something_else.spec_from_file_location(...)`
            # call would have no meaning at runtime — we still flag it
            # because (a) it's still the same name that names the
            # banned function in import code, and (b) widening the
            # match to any attribute call costs nothing and removes a
            # cheap evasion vector. The alias-tracking set is
            # consulted only on the `ast.Name` branch below: attribute
            # calls read the attribute name verbatim off the receiver,
            # so an importer-bound or assignment-bound alias never
            # appears as an `.attr`.
            if func.attr == _FN:
                name = _FN
        elif isinstance(func, ast.Name):
            if func.id in local_names:
                name = _FN
        if name != _FN:
            continue
        lineno = node.lineno
        idx = lineno - 1
        line = lines[idx] if 0 <= idx < len(lines) else ""
        if _NOQA_MARKER in line:
            continue
        violations.append(
            f"{path}:{lineno}: `spec_from_file_location(...)` call outside "
            "tests/conftest.py — use the session-scoped `amc` fixture "
            f"(or annotate the call line with `{_NOQA_MARKER}` if a "
            "fresh module instance is genuinely required)."
        )
    return violations


def main(argv: list[str]) -> int:
    violations: list[str] = []
    for raw in argv[1:]:
        path = Path(raw)
        # conftest.py is the canonical loader; never lint it.
        if path.name == "conftest.py":
            continue
        if not path.is_file():
            continue
        violations.extend(_check_file(path))
    if violations:
        print("\n".join(violations), file=sys.stderr)
        print(
            "\nThe canonical AMC module load lives in `tests/conftest.py` "
            "(`_load_amc` / `amc` fixture). Re-importing via "
            "`spec_from_file_location` duplicates the registry-build cost "
            "and was the DRY violation tracked in VER-197.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
