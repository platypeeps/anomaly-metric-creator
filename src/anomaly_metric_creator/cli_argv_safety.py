"""Argparse error paths that never echo a command-line value.

argparse composes ``unrecognized arguments: <tokens>`` from raw argv, so a
mistyped flag prints whatever followed it. ``amc serve --auth-tokn s3cret``
wrote the token it was meant to guard to stderr, in the separated form and the
``--auth-tokn=s3cret`` form alike. This module is the one place that turns
leftover argv into something safe to print: flag names only, each cut at its
first ``=``.

It imports nothing from the package, so the generate parser (``cli_args``) and
the serve config layer (``server_config``) can both use it without either one
depending on the other.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Iterable, Sequence
from typing import Any

# argparse's own test for "this dash-led token is a number, not an option"
# (`ArgumentParser._negative_number_matcher`, applied with `.match`). A
# negative number after a mistyped flag is that flag's value, and a value is
# never printed.
_NEGATIVE_NUMBER = re.compile(r"-\.?\d")


def flag_names(tokens: Iterable[str]) -> list[str]:
    """The distinct flag names in ``tokens``, sorted, with every value removed.

    A flag is a dash-led token that is not a negative number, cut at its first
    ``=`` so ``--flag=value`` yields ``--flag``; a name that is nothing but
    dashes names no flag. Everything else -- the separate token after a flag,
    a stray positional -- is dropped rather than masked. Which token holds a
    secret is not knowable here: a mistyped key is on no allowlist by
    definition, and masking by pattern kept missing forms argparse echoes. Not
    keeping any non-flag token closes the class by construction.

    Two dash-led shapes are values too, so they are dropped as well. A token
    that follows a flag written without ``=`` may be that flag's value --
    ``--auth-tokn -s3cret`` -- since an unknown flag's arity is unknowable;
    this can cost the name of a second typo, never a value. And everything
    after a bare ``--`` is positional, which argparse leaves in the extras.
    """
    names: set[str] = set()
    may_take_value = False
    for token in tokens:
        if token == "--":
            break
        is_flag = token.startswith("-") and not _NEGATIVE_NUMBER.match(token)
        if is_flag and not may_take_value:
            name = token.split("=", 1)[0]
            if name.strip("-"):
                names.add(name)
        may_take_value = is_flag and "=" not in token
    return sorted(names)


def describe_unrecognized(tokens: Sequence[str]) -> str:
    """The ``unrecognized arguments`` message, naming flags and nothing else."""
    names = flag_names(tokens)
    if names:
        return f"unrecognized arguments: {', '.join(names)} (values are not shown)"
    return "unrecognized arguments (not shown: none of them is a flag)"


class UnrecognizedArguments(SystemExit):
    """Leftover argv that no action consumed, carrying its flag names only.

    A ``SystemExit`` with argparse's usage-error code, so every caller that
    already catches ``SystemExit`` or asserts ``code == 2`` is unaffected. The
    subclass exists so a caller that wants to report the refusal in its own
    words -- ``amc serve``, which owns the command line the operator typed --
    can recognize this failure without parsing stderr.
    """

    def __init__(self, flags: Sequence[str]) -> None:
        super().__init__(2)
        self.flags: tuple[str, ...] = tuple(flags)


class ValueSafeArgumentParser(argparse.ArgumentParser):
    """An ``ArgumentParser`` whose unrecognized-arguments error omits values.

    Only that one message changes. Every other error -- a bad value for a
    recognized flag, a missing required argument -- still goes through
    argparse's own ``error()``, unchanged: those name a flag the parser owns
    and are out of this class's scope.
    """

    def parse_args(  # type: ignore[override]
        self,
        args: Sequence[str] | None = None,
        namespace: Any = None,
    ) -> argparse.Namespace:
        parsed, extras = self.parse_known_args(args, namespace)
        if not extras:
            return parsed
        message = describe_unrecognized(extras)
        if not self.exit_on_error:
            raise argparse.ArgumentError(None, message)
        self.print_usage(sys.stderr)
        sys.stderr.write(f"{self.prog}: error: {message}\n")
        raise UnrecognizedArguments(flag_names(extras))
