"""Package-version ownership and fallback contracts."""

from __future__ import annotations

from importlib import metadata

import anomaly_metric_creator
import anomaly_metric_creator.server_mcp as server_mcp
import anomaly_metric_creator.version as version_module


def test_package_version_matches_installed_distribution():
    assert anomaly_metric_creator.__version__ == metadata.version(
        "anomaly-metric-creator"
    )


def test_package_version_uses_caller_owned_fallback(monkeypatch):
    def missing_distribution(_name: str) -> str:
        raise metadata.PackageNotFoundError(_name)

    monkeypatch.setattr(version_module.metadata, "version", missing_distribution)
    assert version_module.package_version() == "0+unknown"
    assert version_module.package_version(fallback="source") == "source"
    assert server_mcp._server_version() == "unknown"
