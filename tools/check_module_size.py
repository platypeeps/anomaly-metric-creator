#!/usr/bin/env python3
"""Ratchet the 800-line behavior-module limit for the runtime package.

`CLAUDE.md` and the backend specs state that behavior modules stay under 800
lines. That was a prose rule with no enforcement, and prose drifted from the
tree: the rule named `scenario_catalog.py` as "the one deliberate exception"
while six other modules were also over the limit, all of them decomposition
debt from the `legacy.py` and `server_ops.py` epics. Nothing failed, so nobody
noticed, and a reader had no way to tell a sanctioned exception from a module
that had quietly grown past the cap.

This lint makes the exception list an enumerated, checked artifact instead.

The rule
--------
Every `*.py` under `src/anomaly_metric_creator/` must be at or under
`LINE_CAP`, unless it appears in `RATCHET` -- in which case it must be at or
under the ceiling recorded there, which is its size on the day it was
enrolled.

Three ways to fail, and each has one honest remedy:

*Over the cap, not enrolled* -- a new module crossed 800 lines. Extract a leaf.
Enrolling it is the wrong move for anything but pre-existing debt.

*Over its ratchet ceiling* -- an enrolled module grew. This is the case worth
being strict about: a module already over the cap should be trending down, not
up, whether it sits just past 800 or several times it. Extract the code you
were about to add, or, if growth is genuinely the right call, raise that
module's ceiling in the same diff so the increase is a reviewed line in the
changeset rather than an invisible drift.

Both remedies are sanctioned; which one is honest depends on whether the
addition is *separable*. Extract when it is -- a new handler, a helper cluster,
anything that reads as a unit somewhere else. Raise the ceiling when it is not:
a `typing` import, a widened annotation, one branch inside an existing
function. Demanding a 1,000-line decomposition as the price of an import line
would make the lint something to route around, and a lint people route around
enforces nothing. The queued typing work (`server-traces-mypy-gate`,
`audit-typed-boundaries`) is exactly this shape and should expect to bump
ceilings rather than extract.

What the ratchet actually forbids is the *unreviewed* case: growth that nobody
had to look at. A bump is one line in the diff and someone sees it.

*Under the cap while still enrolled* -- an extraction finished the job. Delete
the entry. Left in place it would silently re-authorize 800+ lines later, which
is the exact failure this lint exists to prevent, so a stale entry is a
violation rather than a warning.

Ceilings are exact sizes, not padded budgets. Headroom is how the prose rule
drifted in the first place: any slack is spent, and spending it leaves no
trace. An exact ceiling costs a deliberate one-line edit and, in exchange,
every byte of growth in an over-cap module appears in a diff someone reviews.

Scope
-----
`src/anomaly_metric_creator/` only, and recursively: a subpackage is still a
behavior module and must not be able to escape the cap by being nested. Modules
are keyed by their package-relative path, so `server/state.py` and a top-level
`state.py` are distinct entries rather than one silently shadowing the other.
The package is flat today, which makes both properties invisible now and
load-bearing the moment it stops being flat.

`tools/` and `scripts/` are out of scope -- the cap is a behavior-module rule,
those are single-purpose lints, vendored pack helpers, and harnesses -- and
`tests/` is governed by its own conventions.

Counting
--------
Physical lines, not statements or logical lines: every newline-terminated line,
plus a trailing line that has no final newline. That agrees with `wc -l` for
any file ending in a newline -- which every module here does, and which the
`end-of-file-fixer` hook keeps true -- and it deliberately differs from it for
one that does not, where `wc -l` reports one line fewer than an editor does.
The counts quoted throughout the specs came from editors, and a module should
not slip a line under its ceiling by dropping its final newline.

Reasons in `RATCHET` are required and are printed by `--list`. Keep them
pointing at the owning epic, so an enrolled module is traceable to the work
that will remove it.

Invocation
----------
    python tools/check_module_size.py            # check the repository
    python tools/check_module_size.py --list     # enrolled table + nearest the cap
    python tools/check_module_size.py --repo DIR # check another checkout

Exit codes: 0 clean, 1 violations, 2 structural error.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PACKAGE = Path("src/anomaly_metric_creator")

LINE_CAP = 800

# package-relative path -> (ceiling, reason). Every entry is top-level today,
# so every key is a bare filename; a nested module would be `server/state.py`.
# The ceiling is the module's line count on enrollment; see the docstring for
# why it carries no headroom.
RATCHET: dict[str, tuple[int, str]] = {
    "scenario_catalog.py": (
        2030,
        "permanent: one ordered data-only registry, no validation or runtime "
        "orchestration (see architecture.md)",
    ),
    "server_ops.py": (
        4414,
        "debt: 07-06-server-ops-decomposition, extracting leaves until under cap",
    ),
    "server.py": (
        2078,
        "debt: HTTP serve facade, not yet decomposed; -130 from "
        "08-15-server-alias-getattr-delegation, which replaced the 227-line "
        "server_ops alias block with a module __getattr__ plus 40 explicit "
        "imports",
    ),
    "server_mcp.py": (
        1453,
        "debt: MCP surface, not yet decomposed",
    ),
    "server_debug_ui.py": (
        1194,
        "debt: debug UI, not yet decomposed; +5 for the csvCell CSV-formula "
        "guard (08-15-debug-ui-csv-formula-neutralization), a non-separable "
        "addition inside the embedded UI template",
    ),
    "server_traces.py": (
        1086,
        "debt: trace/overlay state, not yet decomposed; +73 for the payload "
        "TypedDicts (08-06-server-traces-mypy-gate), a non-separable addition",
    ),
    "cli_args.py": (
        960,
        "debt: CLI parser, not yet decomposed",
    ),
}


class StructuralError(Exception):
    """The repository is not shaped the way this lint can read."""


def _count_lines(path: Path) -> int:
    """Physical line count; see the docstring's Counting section.

    Read as bytes and split on newlines: the count must not depend on the
    file decoding, and a module with a stray non-UTF-8 byte should be
    reported as oversized rather than crash the lint.
    """
    try:
        data = path.read_bytes()
    except OSError as exc:  # pragma: no cover -- tracked file went missing
        raise StructuralError(f"cannot read {path}: {exc}") from None
    if not data:
        return 0
    lines = data.count(b"\n")
    # A final line with no trailing newline still counts; `wc -l` would not
    # count it. See the docstring's Counting section for why.
    return lines if data.endswith(b"\n") else lines + 1


def collect(root: Path) -> dict[str, int]:
    """Map every package module to its line count, keyed by package-relative path.

    Recursive, and keyed by path rather than basename, so that a subpackage
    can neither escape the cap nor collide with a top-level module of the same
    name. The package is flat today, which makes both properties invisible now
    and load-bearing the moment it stops being flat.
    """
    package = root / PACKAGE
    if not package.is_dir():
        raise StructuralError(f"{PACKAGE}: not a directory under {root}")
    modules = sorted(package.rglob("*.py"))
    if not modules:
        raise StructuralError(f"{PACKAGE}: contains no Python modules")
    return {
        path.relative_to(package).as_posix(): _count_lines(path) for path in modules
    }


def analyse(counts: dict[str, int]) -> list[str]:
    """Return one violation message per rule break, in reporting order."""
    violations: list[str] = []

    for name in sorted(counts):
        lines = counts[name]
        entry = RATCHET.get(name)
        if entry is None:
            if lines > LINE_CAP:
                violations.append(
                    f"{PACKAGE / name}: {lines} lines exceeds the {LINE_CAP}-line "
                    "behavior-module cap. Extract a leaf module. Only "
                    "pre-existing decomposition debt belongs in RATCHET in "
                    f"{Path(__file__).name}."
                )
            continue
        ceiling, _ = entry
        if lines > ceiling:
            violations.append(
                f"{PACKAGE / name}: {lines} lines exceeds its ratchet ceiling of "
                f"{ceiling}. This module is already over the {LINE_CAP}-line cap. "
                "Extract the addition if it is separable; otherwise -- an import, "
                "an annotation, one branch -- raise this module's ceiling in "
                f"{Path(__file__).name} in the same diff, so the growth is "
                "reviewed rather than forbidden."
            )
        elif lines <= LINE_CAP:
            violations.append(
                f"{PACKAGE / name}: {lines} lines is at or under the "
                f"{LINE_CAP}-line cap, so its RATCHET entry in "
                f"{Path(__file__).name} is stale and must be deleted. Left in "
                f"place it silently re-authorizes {ceiling} lines."
            )

    for name in sorted(RATCHET):
        if name not in counts:
            violations.append(
                f"{PACKAGE / name}: listed in RATCHET in {Path(__file__).name} "
                "but no such module exists. Delete the entry."
            )

    return violations


def _report(counts: dict[str, int]) -> None:
    print(f"behavior-module cap: {LINE_CAP} lines ({PACKAGE})\n")
    print("enrolled (over the cap, ceiling enforced):")
    enrolled = [name for name in sorted(RATCHET) if name in counts]
    for name in sorted(enrolled, key=lambda n: -counts[n]):
        ceiling, reason = RATCHET[name]
        print(f"  {name:32s} {counts[name]:5d} / {ceiling:5d}  {reason}")
    if not enrolled:
        print("  none")
    nearest = 5
    print(f"\nthe {nearest} unenrolled modules nearest the cap:")
    plain = sorted(
        (n for n in counts if n not in RATCHET), key=lambda n: -counts[n]
    )[:nearest]
    for name in plain:
        print(f"  {name:32s} {counts[name]:5d}  ({LINE_CAP - counts[name]} to spare)")
    if not plain:
        print("  none")
    print(f"\n{len(counts)} module(s), {len(enrolled)} enrolled")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", default=".", help="checkout to inspect")
    parser.add_argument(
        "--list",
        action="store_true",
        help="print the enrolled table and the modules nearest the cap, then exit 0",
    )
    # The rule is whole-package, so the hook sets `pass_filenames: false`. A
    # caller that passes filenames anyway -- a hand-run `pre-commit` variant, a
    # shell loop -- gets them accepted and ignored rather than rejected.
    parser.add_argument("files", nargs="*", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    try:
        counts = collect(Path(args.repo).resolve())
    except StructuralError as exc:
        print(f"check_module_size: {exc}", file=sys.stderr)
        return 2

    if args.list:
        _report(counts)
        return 0

    violations = analyse(counts)
    for violation in violations:
        print(violation, file=sys.stderr)
    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
