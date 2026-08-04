#!/usr/bin/env python3
"""Run mypy against the repository's canonical clean-module gate.

The module list lives here so CI and local review preflight cannot drift. Add a
module only after it reaches zero mypy errors; never remove one to hide a
regression.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
CLEAN_MODULES: tuple[str, ...] = (
    "src/anomaly_metric_creator/__init__.py",
    "src/anomaly_metric_creator/artifacts.py",
    "src/anomaly_metric_creator/cli.py",
    "src/anomaly_metric_creator/combine.py",
    "src/anomaly_metric_creator/gauges_impl.py",
    "src/anomaly_metric_creator/models.py",
    "src/anomaly_metric_creator/otel.py",
    "src/anomaly_metric_creator/otel_stream.py",
    "src/anomaly_metric_creator/otlp.py",
    "src/anomaly_metric_creator/redaction.py",
    "src/anomaly_metric_creator/scenario_builders.py",
    "src/anomaly_metric_creator/scenario_catalog.py",
    "src/anomaly_metric_creator/scenario_validation.py",
    "src/anomaly_metric_creator/scenarios.py",
    "src/anomaly_metric_creator/scenarios_impl.py",
    "src/anomaly_metric_creator/schema.py",
    "src/anomaly_metric_creator/server_commands.py",
    "src/anomaly_metric_creator/server_debug_ui.py",
    "src/anomaly_metric_creator/server_helm.py",
    "src/anomaly_metric_creator/server_k8s_objects.py",
    "src/anomaly_metric_creator/server_k8s_tables.py",
    "src/anomaly_metric_creator/server_kubernetes.py",
    "src/anomaly_metric_creator/server_mcp.py",
    "src/anomaly_metric_creator/server_mutations.py",
    "src/anomaly_metric_creator/server_ops_parse.py",
    "src/anomaly_metric_creator/server_ops_profiles.py",
    "src/anomaly_metric_creator/server_ops_support.py",
    "src/anomaly_metric_creator/timeutil.py",
)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args == ["--list"]:
        print("\n".join(CLEAN_MODULES))
        return 0
    if args:
        print("usage: check_mypy_gate.py [--list]", file=sys.stderr)
        return 2

    try:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "mypy",
                "--follow-imports=silent",
                *CLEAN_MODULES,
            ],
            cwd=REPO_ROOT,
            check=False,
        )
    except OSError as exc:
        print(f"check_mypy_gate: could not start mypy: {exc}", file=sys.stderr)
        return 2
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
