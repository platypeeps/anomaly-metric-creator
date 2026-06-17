#!/usr/bin/env python3
"""Forbid duplicate amc module loads in tests/.

`tests/conftest.py:_load_amc()` is the single canonical entry point for
loading the implementation module (`src/anomaly_metric_creator/legacy.py`)
as an importable module. Other test
files MUST consume the session-scoped `amc` fixture rather than re-issue
`importlib.util.spec_from_file_location(...).exec_module(...)`. The
duplicate exec_module pays the full registry-validation cost again.

This lint catches the duplication pattern an earlier engineering
review surfaced on PR #63 and PR #64, where new test files re-imported
the implementation module instead of consuming the shared fixture.

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
- A trailing ``# amc-load: allow`` comment on the call's opening line
  *or* closing line opts that specific call out of the check. The
  marker is detected via the ``tokenize`` stream — only real
  ``tokenize.COMMENT`` tokens count, so the same marker text appearing
  inside a string literal or any non-comment context does not silence
  the lint. Either ``node.lineno`` or ``node.end_lineno`` accepts the
  marker; multi-line calls can place the trailing comment on whichever
  line the caller's formatter prefers. Use sparingly for cases that
  legitimately need a fresh module copy:
  * tests that monkeypatch module-level callables and must isolate state
    (see `tests/test_correctness.py`);
  * collection-time parametrize loaders that fire before pytest
    fixtures resolve (see `tests/test_scenarios.py`).
"""

from __future__ import annotations

import ast
import io
import sys
import tokenize
from pathlib import Path

_FN = "spec_from_file_location"
_ALLOW_MARKER = "# amc-load: allow"


def _collect_allow_lines(src: str) -> set[int]:
    """Return line numbers that carry a real ``# amc-load: allow`` comment.

    The exemption must live in an actual ``tokenize.COMMENT`` token so a
    raw substring match inside a string literal (or anywhere else on
    the line) does not silently bypass the lint. Copilot PR #74 round-4
    flagged the prior raw-line ``in`` check as accidentally / trivially
    bypassable — e.g.
    ``spec_from_file_location('amc', '# amc-load: allow')`` would have
    been silenced because the marker text appeared verbatim on the
    physical line even though no real comment was present.

    Multi-line calls are accepted with the trailing comment on either
    the opening line (``node.lineno``) or the closing line
    (``node.end_lineno``); the call site picks the convention that
    fits its formatter. ``_check_file`` is the consumer that decides
    which AST node lines to compare against — this helper only
    returns the *set* of lines that carry a real comment with the
    marker.

    The ``try/except tokenize.TokenizeError`` is a defensive fallback,
    not a recovery path: ``_check_file`` parses with ``ast.parse``
    first and returns early on ``SyntaxError`` (which ``ast.parse``
    raises on any malformed input), so by the time we reach
    ``tokenize.generate_tokens`` the source is known to be a
    well-formed Python module. The handler is there only to keep the
    lint from crashing on rare tokenization edge cases that the AST
    layer does not surface — e.g. an inconsistent indentation pattern
    that ``compile()`` would still accept — by collapsing to "no
    exemptions" and letting the caller emit the original violation,
    which is the conservative behavior. Copilot PR #74 round-6/7.
    """
    exempt: set[int] = set()
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type == tokenize.COMMENT and _ALLOW_MARKER in tok.string:
                exempt.add(tok.start[0])
    except tokenize.TokenizeError:
        pass
    return exempt


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
    allow_lines = _collect_allow_lines(src)
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
        # Multi-line calls: accept the allow marker on the opening line
        # OR the closing line (`node.end_lineno`), since the trailing
        # comment convention varies — some formatters place it on the
        # closing paren's line, others on the opening line. Both are
        # legal Python and both should suppress the lint. Copilot
        # PR #74 round-5. `end_lineno` is available since Python 3.8;
        # the repo's `requires-python` is 3.11+, so the attribute is
        # always present, but fall back to `lineno` defensively in
        # case a future ast walker returns a node without it.
        end_lineno = getattr(node, "end_lineno", None) or lineno
        if lineno in allow_lines or end_lineno in allow_lines:
            continue
        violations.append(
            f"{path}:{lineno}: `spec_from_file_location(...)` call outside "
            "tests/conftest.py — use the session-scoped `amc` fixture "
            f"(or annotate the call line with `{_ALLOW_MARKER}` if a "
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
            "and is the DRY violation this lint guards against.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
