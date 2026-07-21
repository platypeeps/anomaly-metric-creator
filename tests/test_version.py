"""Package-version ownership and fallback contracts."""

from __future__ import annotations

from importlib import metadata

import anomaly_metric_creator
from anomaly_metric_creator import server_mcp
from anomaly_metric_creator import version as version_module


def test_package_version_matches_installed_distribution():
    assert anomaly_metric_creator.__version__ == metadata.version(
        "anomaly-metric-creator"
    )


def test_package_version_uses_caller_owned_fallback(monkeypatch):
    def missing_distribution(_name: str) -> str:
        raise metadata.PackageNotFoundError

    monkeypatch.setattr(version_module.metadata, "version", missing_distribution)
    assert version_module.package_version() == "0+unknown"
    assert version_module.package_version(fallback="source") == "source"
    assert server_mcp._server_version() == "unknown"
