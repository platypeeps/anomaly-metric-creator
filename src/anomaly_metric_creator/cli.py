"""Package entrypoint for the anomaly metric creator CLI."""

from __future__ import annotations

import functools
from types import ModuleType


@functools.cache
def _load_legacy_module() -> ModuleType:
    """Load and memoize the packaged legacy implementation module.

    ``functools.cache`` is the singleton: the ~600 KB module is imported once
    and the resulting module is returned on every subsequent call.
    """
    from . import legacy

    return legacy


def main(argv: list[str] | None = None) -> None:
    """Run the anomaly metric creator CLI."""
    _load_legacy_module().main(argv)
