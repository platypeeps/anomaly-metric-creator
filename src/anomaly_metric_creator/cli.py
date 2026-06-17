"""Package entrypoint for the legacy single-file CLI.

The repository still keeps ``anomaly-metric-creator.py`` as the canonical
implementation so existing direct invocations continue to work. This module
provides the installed ``amc`` console script and creates a narrow bridge for
incrementally moving cohesive areas into package modules.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

_LEGACY_MODULE: ModuleType | None = None


def _load_legacy_module() -> ModuleType:
    global _LEGACY_MODULE
    if _LEGACY_MODULE is not None:
        return _LEGACY_MODULE

    script_path = Path(__file__).resolve().parents[2] / "anomaly-metric-creator.py"
    spec = importlib.util.spec_from_file_location(
        "anomaly_metric_creator._legacy", script_path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load legacy CLI module from {script_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _LEGACY_MODULE = module
    return module


def main(argv: list[str] | None = None) -> None:
    """Run the anomaly metric creator CLI."""
    _load_legacy_module().main(argv)
