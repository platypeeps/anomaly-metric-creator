"""CLI subcommand dispatch helpers for anomaly-metric-creator.

Split from ``cli_args.py`` during decomposition step 8. ``cli_args`` configures
live registry access, and ``legacy.py`` re-imports these names to preserve the
historic ``legacy.<name>`` surface.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, Callable

from .combine_impl import combine_logs
from .validate_impl import validate_output

_DEFAULT_RUNTIME_KEY = "__default__"
_cli_subcommand_runtimes: dict[str, dict[str, Any]] = {}


def _configure_cli_subcommand_runtime(
    *,
    runtime_key: str = _DEFAULT_RUNTIME_KEY,
    get_components: Callable[[], dict[str, Any]],
    parse_components_value: Callable[..., set[str]],
    get_legacy_module: Callable[[], ModuleType],
) -> None:
    """Wire parser dependencies from ``cli_args.py`` without importing it."""
    _cli_subcommand_runtimes[runtime_key] = {
        "get_components": get_components,
        "parse_components_value": parse_components_value,
        "get_legacy_module": get_legacy_module,
    }


def _runtime(runtime_key: str) -> dict[str, Any]:
    runtime = _cli_subcommand_runtimes.get(runtime_key)
    if runtime is None:
        raise RuntimeError("cli_subcommands runtime is not configured")
    return runtime


def _components(runtime_key: str) -> dict[str, Any]:
    runtime = _runtime(runtime_key)
    if runtime["get_components"] is None:
        raise RuntimeError("cli_subcommands registry runtime is not configured")
    return runtime["get_components"]()


def _parse_components(
    error: Callable[[str], None], raw: str, *, runtime_key: str
) -> set[str]:
    runtime = _runtime(runtime_key)
    if runtime["parse_components_value"] is None:
        raise RuntimeError("cli_subcommands parser runtime is not configured")
    return runtime["parse_components_value"](error, raw, runtime_key=runtime_key)


def _legacy_module(runtime_key: str) -> ModuleType:
    runtime = _runtime(runtime_key)
    if runtime["get_legacy_module"] is None:
        raise RuntimeError("cli_subcommands legacy module runtime is not configured")
    return runtime["get_legacy_module"]()


_SUBCOMMANDS = ("generate", "combine", "validate", "serve", "trace-bundle")


def _main_combine_subcommand(argv, *, runtime_key: str = _DEFAULT_RUNTIME_KEY):
    """``combine DIR [--components ...]``: skip generation and join the
    existing per-component CSVs in DIR into combined_metrics_unified.csv.
    """
    sp = argparse.ArgumentParser(
        prog="anomaly-metric-creator.py combine",
        description="Join existing per-component CSVs in DIR into "
                    "combined_metrics_unified.csv (no generation).",
    )
    sp.add_argument("directory", type=Path,
                    help="Directory holding the per-component CSVs of a "
                         "prior run (a previous run's --output-dir).")
    sp.add_argument("--components", type=str, default="all",
                    help="Comma-separated allowlist of component CSVs to "
                         "combine; 'all' (default) autodiscovers every "
                         "*.csv in DIR.")
    a = sp.parse_args(argv)
    if not a.directory.is_dir():
        if a.directory.exists():
            sp.error(f"combine requires a directory; "
                     f"{a.directory} exists but is not one")
        sp.error(f"combine requires an existing directory; "
                 f"{a.directory} does not exist")
    selected = _parse_components(sp.error, a.components, runtime_key=runtime_key)
    components = _components(runtime_key)
    if selected == set(components.keys()):
        combine_components = None
    else:
        combine_components = [name for name in components if name in selected]
    combine_logs(a.directory, components=combine_components)


def _main_validate_subcommand(argv, *, runtime_key: str = _DEFAULT_RUNTIME_KEY):
    """``validate DIR [--warn]``: check the artifacts in DIR against
    DIR/schema.json and exit 1 on violations (0 with --warn).
    """
    sp = argparse.ArgumentParser(
        prog="anomaly-metric-creator.py validate",
        description="Validate the artifacts in DIR against DIR/schema.json.",
    )
    sp.add_argument("directory", type=Path,
                    help="Directory holding a prior run's artifacts, "
                         "including the schema.json written via "
                         "--emit ...,schema.")
    sp.add_argument("--warn", action="store_true",
                    help="Report violations on stderr but exit 0 (default: "
                         "exit 1 on any violation).")
    a = sp.parse_args(argv)
    if not a.directory.is_dir():
        if a.directory.exists():
            sp.error(f"validate requires a directory; "
                     f"{a.directory} exists but is not one")
        sp.error(f"validate requires an existing directory; "
                 f"{a.directory} does not exist")
    try:
        violations = validate_output(a.directory)
    except ValueError as exc:
        sp.error(str(exc))
    for line in violations:
        print(f"VALIDATION: {line}", file=sys.stderr)
    if not violations:
        print(f"validate: {a.directory} OK (no violations)")
        return
    if a.warn:
        print(f"validate: {len(violations)} violation(s) in "
              f"{a.directory} (--warn: returning 0)", file=sys.stderr)
        return
    raise SystemExit(1)


def _main_serve_subcommand(argv, *, runtime_key: str = _DEFAULT_RUNTIME_KEY):
    """``serve [server flags] [generate flags...]``: run the simulator as an
    HTTP server with Kubernetes/Helm command responses and debug APIs.
    """
    from .server import serve_main

    return serve_main(argv, legacy_module=_legacy_module(runtime_key))


def _main_trace_bundle_subcommand(argv, *, runtime_key: str = _DEFAULT_RUNTIME_KEY):
    """``trace-bundle ...``: inspect exported command traces offline."""
    from .trace_bundle import main as trace_bundle_main

    return trace_bundle_main(argv)
