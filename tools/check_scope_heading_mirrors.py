#!/usr/bin/env python3
"""Guard that every prose description of the PR-body scope guard names the
same category headings the guard actually recognizes.

`scripts/sd-ai-command-pack-pr-body-scope.py` decides whether a PR body
carries the explicit scope section its diff requires. It is the only authority
on which headings exist: `DEFAULT_RULES` in that script, merged with the
repository's `.sd-ai-command-pack/pr-body-scope.json`. Nothing else in this
repository gets to hold an opinion about that list.

Five files describe the guard to a human or an agent, and every one of them
recites the category headings by hand. That hand-copying is the drift: a
category added to or renamed in the authority leaves five prose mirrors
claiming the old set, and each one reads like documentation rather than like
the stale copy it is.

The failure is not hypothetical. PR #362 corrected two mirrors that claimed the
guard matched five exact strings and nothing else, which is false -- the
matcher is case-insensitive, tolerates Markdown heading/list/blockquote
prefixes, treats the trailing colon as optional, and accepts a documented alias
per rule. The mirrors were wrong in a way that made a *working* heading look
unsafe. Correcting the prose fixed that instance; it did not stop the next one.

What this guard checks
----------------------

**Forward (every mirror names every canonical heading).** The canonical heading
of a rule is `headings[0]` -- the form the authority puts first and the form
every mirror recommends. A mirror that omits one has drifted behind a category
the guard enforces, so a contributor reading it will not know to write that
section.

**Reverse (no mirror names a heading the authority rejects).** Every
backtick-delimited token in a mirror that ends in `scope` or `scope:` is fed
through the authority's own `_body_has_heading` against the union of all
recognized headings and aliases. Anything unrecognized must be listed in
`ALLOWED_UNRECOGNIZED` below. That list is the escape hatch for a deliberate
counter-example -- prose that shows an invented heading precisely to say it
does not work -- and an entry is one reviewed line, not a silent exemption.

Deriving both directions from the authority rather than from a literal in this
file is the point. A stored list here would be a sixth mirror with the same
drift, one indirection further from the reader.

Limitations, stated rather than papered over
--------------------------------------------

The reverse check reads backtick-delimited tokens only.
`.github/PULL_REQUEST_TEMPLATE.md` states its headings inside an HTML comment
without backticks, so it contributes nothing to the reverse pass and is covered
by the forward pass alone. Widening the reverse pass to bare double-quoted
prose was tried and rejected: it flags ordinary sentences that happen to quote
a fragment, and a guard that cries wolf gets silenced.

`docs/SD_AI_COMMAND_PACK.md` is deliberately not a mirror. It documents
specific pack behaviors that involve individual headings -- the copied/generated
preflight, the `sd-create-pr` body preparation -- and never claims to enumerate
the category set. Requiring it to name all five would be requiring it to say
something it has no reason to say.

Invocation
----------

    python tools/check_scope_heading_mirrors.py            # scan every mirror
    python tools/check_scope_heading_mirrors.py PATH ...   # scan named files
    python tools/check_scope_heading_mirrors.py --list     # print the table
    python tools/check_scope_heading_mirrors.py --root DIR # scan another tree

Paths that are not mirrors are ignored, so the pre-commit hook can pass its
whole changed-file set. With no paths, every mirror is checked. `--root`
relocates both the authority and the mirrors, which is how the tests build a
synthetic tree; it defaults to this repository.

Exit codes
----------

    0   clean
    1   a violation: a mirror is missing a canonical heading, or names an
        unrecognized one
    2   a structural error: the authority script or a mirror file cannot be
        read, or the authority reports its own configuration error
"""

from __future__ import annotations

import argparse
import importlib.util
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parent.parent

AUTHORITY = Path("scripts/sd-ai-command-pack-pr-body-scope.py")

# Files that describe the scope guard's category set to a human or an agent.
# Each must name every canonical heading. Keep this list and the guard's own
# reason for including a file together: a file earns a place here by claiming
# to tell the reader which scope sections exist.
MIRRORS = (
    ".github/PULL_REQUEST_TEMPLATE.md",
    "docs/DEVELOPMENT_CYCLE.md",
    ".github/copilot-instructions.md",
    ".github/instructions/anomaly-metric-creator.instructions.md",
    ".trellis/spec/amc/backend/documentation-review.md",
)

# Backticked tokens that end in "scope"/"scope:" but are not headings the
# authority recognizes, and are present on purpose. Keyed by mirror path so an
# exemption cannot leak into a file that never justified it.
ALLOWED_UNRECOGNIZED: dict[str, tuple[str, ...]] = {
    # The counter-example: docs/DEVELOPMENT_CYCLE.md shows an invented heading
    # to make the point that inventing one matches nothing. Removing it would
    # remove the warning.
    "docs/DEVELOPMENT_CYCLE.md": ("Explicit doc scope",),
}

# A backtick span whose content ends in "scope" or "scope:", ignoring any
# trailing whitespace. Markdown heading/list/blockquote prefixes inside the
# span are kept: _body_has_heading is what decides whether they are tolerated,
# not this regex.
_BACKTICK_SCOPE = re.compile(r"`([^`\n]*?scope:?)[ \t]*`", re.IGNORECASE)


@dataclass(frozen=True)
class Authority:
    """The scope guard's own view of which headings exist."""

    canonical: tuple[str, ...]
    all_headings: tuple[str, ...]
    has_heading: object


class StructuralError(Exception):
    """The guard cannot run, as opposed to finding a violation."""


def _load_authority(root: Path) -> Authority:
    path = root / AUTHORITY
    if not path.is_file():
        raise StructuralError(f"authority script not found: {AUTHORITY}")

    # The authority lives under scripts/ and imports its sibling library
    # package, so scripts/ must be importable before it executes. The module
    # also has to be registered in sys.modules before exec_module: it defines
    # frozen dataclasses, and dataclasses resolves annotations through
    # sys.modules[cls.__module__], which raises AttributeError for a module
    # that is not registered yet.
    scripts_dir = str(root / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)

    module_name = "_amc_pr_body_scope_authority"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise StructuralError(f"cannot load authority script: {AUTHORITY}")
    module: ModuleType = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # noqa: BLE001 - any import failure is structural
        raise StructuralError(f"cannot execute {AUTHORITY}: {exc}") from exc

    for name in ("_rules_for_repo", "_body_has_heading"):
        if not hasattr(module, name):
            raise StructuralError(
                f"{AUTHORITY} has no {name}; the guard's contract with the "
                "authority changed and this lint needs updating"
            )

    rules, error = module._rules_for_repo(root, None)
    if error is not None:
        raise StructuralError(f"{AUTHORITY} reports a configuration error: {error}")
    if not rules:
        raise StructuralError(f"{AUTHORITY} resolved no scope rules")

    canonical: list[str] = []
    every: list[str] = []
    for rule in rules:
        if not rule.headings:
            raise StructuralError(f"{AUTHORITY}: rule {rule.label!r} has no headings")
        canonical.append(rule.headings[0])
        every.extend(rule.headings)

    return Authority(
        canonical=tuple(canonical),
        all_headings=tuple(every),
        has_heading=module._body_has_heading,
    )


def _read(root: Path, relative: str) -> str:
    path = root / relative
    if not path.is_file():
        raise StructuralError(f"mirror not found: {relative}")
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise StructuralError(f"cannot read {relative}: {exc}") from exc
    except UnicodeError as exc:
        raise StructuralError(f"cannot decode {relative}: {exc}") from exc


def _check_mirror(
    relative: str, text: str, authority: Authority, violations: list[str]
) -> None:
    for heading in authority.canonical:
        if heading not in text:
            violations.append(
                f"{relative}: does not name the canonical heading {heading!r}. "
                f"{AUTHORITY} recognizes it; this file tells readers it does not "
                "exist."
            )

    allowed = ALLOWED_UNRECOGNIZED.get(relative, ())
    for match in _BACKTICK_SCOPE.finditer(text):
        token = match.group(1)
        if token in allowed:
            continue
        # Feed the token through the authority's own matcher rather than
        # comparing strings: it is what decides whether a Markdown prefix,
        # a case difference, or a missing colon still counts.
        if authority.has_heading(token, authority.all_headings):
            continue
        line = text.count("\n", 0, match.start()) + 1
        violations.append(
            f"{relative}:{line}: {token!r} is written as a scope heading but "
            f"{AUTHORITY} does not recognize it. Use a heading the authority "
            "accepts, or add this exact token to ALLOWED_UNRECOGNIZED in "
            f"{Path(__file__).name} if it is a deliberate counter-example."
        )


def _print_table(authority: Authority, root: Path) -> None:
    print(f"authority: {AUTHORITY}")
    print(f"canonical headings ({len(authority.canonical)}):")
    for heading in authority.canonical:
        print(f"  {heading}")
    print(f"all recognized headings including aliases: {len(authority.all_headings)}")
    print("mirrors:")
    for relative in MIRRORS:
        text = _read(root, relative)
        missing = [h for h in authority.canonical if h not in text]
        exempt = len(ALLOWED_UNRECOGNIZED.get(relative, ()))
        state = "ok" if not missing else f"missing {len(missing)}"
        print(f"  {relative:<62} {state:<12} exemptions={exempt}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("paths", nargs="*", help="files to check; non-mirrors ignored")
    parser.add_argument(
        "--list", action="store_true", help="print the mirror coverage table"
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=REPO_ROOT,
        help="repository root holding the authority and the mirrors",
    )
    args = parser.parse_args(argv)

    root = args.root.resolve()
    try:
        authority = _load_authority(root)
        if args.list:
            _print_table(authority, root)
            return 0

        if args.paths:
            requested = {
                str(Path(path).resolve().relative_to(root))
                if Path(path).is_absolute()
                else str(Path(path)).replace("\\", "/")
                for path in args.paths
            }
            selected = [m for m in MIRRORS if m in requested]
        else:
            selected = list(MIRRORS)

        violations: list[str] = []
        for relative in selected:
            _check_mirror(relative, _read(root, relative), authority, violations)
    except StructuralError as exc:
        print(f"check_scope_heading_mirrors: {exc}", file=sys.stderr)
        return 2

    if violations:
        for violation in violations:
            print(violation, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
